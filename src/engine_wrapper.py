import copy
import os
import subprocess
import time

import backoff
import chess
import chess.uci
import chess.xboard


MATE_SCORE = 1 << 31

# time to subtract from the time given to the engine. this helps with bad
# connections in which Lichess compensates the time but you still need the
# engine to make the move faster. also useful when you have an unstable
# connection that lags every once in a while.
XBOARD_MOVE_OVERHEAD = 1000

METRIC_PREFIXES = {
    10 ** 12: "T",
    10 ** 9: "G",
    10 ** 6: "M",
    10 ** 3: "k",
}

LARGE_NUMBER_ABBREVIATIONS = {
    10 ** 12: "T",
    10 ** 9: "B",
    10 ** 6: "M",
    10 ** 3: "k",
}


GAME_SPEEDS = ("ultraBullet", "bullet", "blitz", "rapid", "classical")

PIECES = frozenset((
    chess.Piece(chess.KNIGHT, chess.WHITE),
    chess.Piece(chess.KNIGHT, chess.BLACK),
    chess.Piece(chess.BISHOP, chess.WHITE),
    chess.Piece(chess.BISHOP, chess.BLACK),
    chess.Piece(chess.ROOK, chess.WHITE),
    chess.Piece(chess.ROOK, chess.BLACK),
    chess.Piece(chess.QUEEN, chess.WHITE),
    chess.Piece(chess.QUEEN, chess.BLACK),
))


def get_config(config, speed):
    speed_index = GAME_SPEEDS.index(speed)
    for d in range(len(GAME_SPEEDS)):
        close_speeds = filter(lambda s: abs(GAME_SPEEDS.index(s) - speed_index) == d, GAME_SPEEDS)
        for close_speed in close_speeds:
            try:
                return config[close_speed]
            except KeyError:
                pass
    return None


def parse_configs(options, speed):
    for name, value in options.items():
        if name in ("go_commands", "egtpath") or type(value) == int:
            continue
        if type(value) == dict:
            new_value = get_config(value, speed)
            if new_value is None:
                del options[name]
            else:
                options[name] = new_value
    return options


@backoff.on_exception(backoff.expo, BaseException, max_time=120)
def create_engine(config, board, game_speed):
    cfg = config["engine"]
    engine_path = os.path.join(cfg["dir"], cfg["name"])
    engine_type = cfg.get("protocol")
    engine_options = cfg.get("engine_options", {})
    commands = [engine_path]

    for k, v in engine_options.items():
        commands.append("--{}={}".format(k, v))

    silence_stderr = cfg.get("silence_stderr", False)
    ponder = cfg.get("ponder", False)

    game_end_conditions = {
        "draw": cfg.get("offer_draw", {"threshold": -1, "sustain_turns": 9999, "minimum_turns": 0}),
        "resignation": cfg.get("resignation", {"threshold": 9999 * MATE_SCORE, "sustain_turns": 1}),
    }

    if engine_type == "xboard":
        options = parse_configs(cfg.get("xboard_options", {}), game_speed)
        return XBoardEngine(board, commands, options, game_end_conditions, silence_stderr)
    else:
        options = parse_configs(cfg.get("uci_options", {}), game_speed)
        return UCIEngine(board, commands, options, game_end_conditions, silence_stderr, ponder)


def is_endgame(board):
    pieces = tuple(p for p in board.piece_map().values() if p in PIECES)
    return len(pieces) <= 6


class EngineWrapper:

    def __init__(self, board, commands, options, game_end_conditions, silence_stderr=False, ponder_on=False):
        self.board = board
        self.commands = commands
        self.options = options
        self.draw_conditions = game_end_conditions["draw"]
        self.resignation_conditions = game_end_conditions["resignation"]
        self.silence_stderr = silence_stderr
        self.ponder_on = ponder_on

        self.past_scores = []
        self.is_game_over = False

        self.did_first_move = False

    def set_time_control(self, game):
        pass

    def first_search(self, board, movetime):
        pass

    def search(self, board, wtime, btime, winc, binc):
        pass

    def print_stats(self):
        pass

    def name(self):
        return self.engine.name

    def quit(self):
        self.engine.quit()

    def process_endgame_conditions(self, board):
        draw_scores = self.past_scores[-self.draw_conditions["sustain_turns"]:]
        draw = abs(max(draw_scores, key=abs)) <= self.draw_conditions["threshold"] \
            if board.fullmove_number >= self.draw_conditions["minimum_turns"] and \
            board.halfmove_clock >= 2 * self.draw_conditions["sustain_turns"] and \
            len(self.past_scores) >= self.draw_conditions["sustain_turns"] and \
            is_endgame(board) else False

        resign_scores = self.past_scores[-self.resignation_conditions["sustain_turns"]:]
        resign = max(resign_scores) <= -self.resignation_conditions["threshold"] \
            if len(resign_scores) >= self.resignation_conditions["sustain_turns"] and \
            is_endgame(board) else False

        return draw, resign

    @staticmethod
    def get_pretty_stat(stat_name, stat_value):
        if stat_name == "nps":
            for size, prefix in METRIC_PREFIXES.items():
                if stat_value >= size:
                    formatted_value = round(stat_value / size, 1)
                    if round(formatted_value) >= 10:
                        formatted_value = round(formatted_value)
                    return "{} {}nps".format(formatted_value, prefix)
            return "{} nps".format(stat_value)
        elif stat_name == "nodes":
            for size, abbreviation in LARGE_NUMBER_ABBREVIATIONS.items():
                if stat_value >= size:
                    formatted_value = round(stat_value / size, 1)
                    if round(formatted_value) >= 10:
                        formatted_value = round(formatted_value)
                    return "{}{} nodes".format(formatted_value, abbreviation)
            return "{} nodes".format(stat_value)
        elif stat_name == "score":
            try:
                score = stat_value[1]
                if score.cp is not None:
                    if score.cp > 0:
                        score = "+{}".format(score.cp / 100)
                    elif score.cp <= 0:
                        score = "{}".format(str(score.cp / 100))
                else:
                    if score.mate > 0:
                        score = "+M{}".format(score.mate)
                    elif score.mate < 0:
                        score = "-M{}".format(abs(score.mate))
                    else:
                        score = "M0"
            except (KeyError, AttributeError):
                score = stat_value[1]
            return "Score: {}".format(score)
        elif stat_name == "depth":
            return "Depth: {} ply".format(stat_value)
        elif stat_name == "tbhits":
            for size, abbreviation in LARGE_NUMBER_ABBREVIATIONS.items():
                if stat_value >= size:
                    formatted_value = round(stat_value / size, 1)
                    if round(formatted_value) >= 10:
                        formatted_value = round(formatted_value)
                    return "{}{} tb hits".format(formatted_value, abbreviation)
            return "{} tb hits".format(stat_value)
        else:
            return "{}: {}".format(stat_name, stat_value)

    @staticmethod
    def print_handler_stats(info, stats):
        for stat in filter(lambda s: s in info, stats):
            print("    {}: {}".format(stat, info[stat]))

    def get_handler_stats(self, info, stats):
        stats_str = []
        for stat in filter(lambda s: s in info, stats):
            try:
                if stat == "depth" and "seldepth" in info and info["seldepth"] > info["depth"]:
                    stats_str.append(self.get_pretty_stat(stat, "{}/{}".format(info["depth"], info["seldepth"])))
                else:
                    stats_str.append(self.get_pretty_stat(stat, info[stat]))
            except Exception as err:
                pass
        return stats_str


class UCIEngine(EngineWrapper):

    def __init__(self, board, commands, options, game_end_conditions, silence_stderr=False, ponder_on=False):
        super().__init__(board, commands, options, game_end_conditions, silence_stderr, ponder_on)
        commands = commands[0] if len(commands) == 1 else commands
        self.go_commands = options.get("go_commands", {})
        self.move_overhead = options.get("Move Overhead", XBOARD_MOVE_OVERHEAD)

        self.engine = chess.uci.popen_engine(commands, stderr=subprocess.DEVNULL if silence_stderr else None)
        self.engine.uci()

        if options:
            self.engine.setoption(options)

        self.engine.setoption({
            "UCI_Variant": type(board).uci_variant,
            "UCI_Chess960": board.chess960
        })

        self.engine.position(board)

        info_handler = chess.uci.InfoHandler()
        self.engine.info_handlers.append(info_handler)

        self.ponder_command = False
        self.ponder_board = chess.Board()

    def first_search(self, board, movetime):
        self.engine.position(board)
        best_move, _ = self.engine.go(movetime=movetime)
        self.did_first_move = True
        return best_move

    def search(self, board, wtime, btime, winc, binc):
        search_start_time = time.time()
        cmds = self.go_commands

        if board.turn == chess.WHITE:
            wtime = max(0, wtime - self.move_overhead)
        else:
            btime = max(0, btime - self.move_overhead)

        best_move = None
        ponder_move = None

        if self.ponder_command:
            if self.ponder_board.fen() == board.fen():
                self.engine.ponderhit()
                while not self.ponder_command.done():
                    if self.is_game_over:
                        return
                best_move, ponder_move = self.ponder_command.result()
            else:
                self.engine.stop()

            self.ponder_command = False

        if best_move is None:
            self.engine.position(board)
            callback = self.engine.go(
                wtime=wtime,
                btime=btime,
                winc=winc,
                binc=binc,
                depth=cmds.get("depth"),
                nodes=cmds.get("nodes"),
                movetime=cmds.get("movetime"),
                async_callback=True
            )

            while not callback.done():
                if self.is_game_over:
                    return
            best_move, ponder_move = callback.result()

        try:
            score = self.engine.info_handlers[0].info["score"][1]
            score = score.cp if score.cp is not None else MATE_SCORE * score.mate
            self.past_scores.append(score)
        except (KeyError, AttributeError):
            self.past_scores = []  # reset the past scores so nothing will screw up if engine doesn't report score

        if self.ponder_on and ponder_move is not None:

            time_to_ponder = True
            if board.turn == chess.WHITE:
                wtime -= int(1000 * (time.time() - search_start_time))
                if wtime < self.move_overhead:
                    time_to_ponder = False
            else:
                btime -= int(1000 * (time.time() - search_start_time))
                if btime < self.move_overhead:
                    time_to_ponder = False

            if time_to_ponder:
                self.ponder_board = copy.deepcopy(board)
                self.ponder_board.push(best_move)
                self.ponder_board.push(ponder_move)
                self.ponder(self.ponder_board, wtime, btime, winc, binc)

        draw, resign = self.process_endgame_conditions(board)
        return best_move, draw, resign

    def ponder(self, board, wtime, btime, winc, binc):
        cmds = self.go_commands

        self.engine.position(board)
        self.ponder_command = self.engine.go(
            wtime=wtime,
            btime=btime,
            winc=winc,
            binc=binc,
            depth=cmds.get("depth"),
            nodes=cmds.get("nodes"),
            movetime=cmds.get("movetime"),
            ponder=True,
            async_callback=True
        )

    def stop(self):
        self.engine.stop()

    def print_stats(self):
        self.print_handler_stats(self.engine.info_handlers[0].info,
                                 ["string", "depth", "nps", "nodes", "tbhits", "score"])

    def get_stats(self):
        return self.get_handler_stats(self.engine.info_handlers[0].info,
                                      ["depth", "nps", "nodes", "tbhits", "score"])


class XBoardEngine(EngineWrapper):

    def __init__(self, board, commands, options, game_end_conditions, silence_stderr=False, ponder_on=False):
        super().__init__(board, commands, options, game_end_conditions, silence_stderr, ponder_on)
        commands = commands[0] if len(commands) == 1 else commands
        self.engine = chess.xboard.popen_engine(commands, stderr=subprocess.DEVNULL if silence_stderr else None)
        self.engine.xboard()

        if board.chess960:
            self.engine.send_variant("fischerandom")
        elif type(board).uci_variant != "chess":
            self.engine.send_variant(type(board).uci_variant)

        if options:
            self._handle_options(options)

        self.engine.setboard(board)

        post_handler = chess.xboard.PostHandler()
        self.engine.post_handlers.append(post_handler)

    def _handle_options(self, options):
        for option, value in options.items():
            if option == "memory":
                self.engine.memory(value)
            elif option == "cores":
                self.engine.cores(value)
            elif option == "egtpath":
                for egttype, egtpath in value.items():
                    try:
                        self.engine.egtpath(egttype, egtpath)
                    except chess.uci.EngineStateException:
                        # If the user specifies more TBs than the engine supports, ignore the error.
                        pass
            else:
                try:
                    self.engine.features.set_option(option, value)
                except chess.uci.EngineStateException:
                    pass

    def set_time_control(self, game):
        minutes = game.clock_initial / 1000 / 60
        seconds = game.clock_initial / 1000 % 60
        inc = game.clock_increment / 1000
        self.engine.level(0, minutes, seconds, inc)

    def first_search(self, board, movetime):
        self.engine.setboard(board)
        self.engine.st(movetime / 1000)
        bestmove = self.engine.go()
        self.did_first_move = True
        return bestmove

    def search(self, board, wtime, btime, winc, binc):
        self.engine.force()
        try:
            self.engine.usermove(board.peek())
        except IndexError:
            self.engine.setboard(board)

        if board.turn == chess.WHITE:
            wtime = max(0, wtime - XBOARD_MOVE_OVERHEAD)
            self.engine.time(wtime / 10)
            self.engine.otim(btime / 10)
        else:
            btime = max(0, btime - XBOARD_MOVE_OVERHEAD)
            self.engine.time(btime / 10)
            self.engine.otim(wtime / 10)
        best_move = self.engine.go()

        try:
            score = self.engine.post_handlers[0].post["score"][1]
            score = score.cp if score.cp is not None else MATE_SCORE * score.mate
            self.past_scores.append(score)
        except (KeyError, AttributeError):
            self.past_scores = []  # reset the past scores so nothing will screw up if engine doesn't report score

        draw, resign = self.process_endgame_conditions(board)
        return best_move, draw, resign

    def print_stats(self):
        self.print_handler_stats(self.engine.post_handlers[0].post, ["depth", "nodes", "score"])

    def get_stats(self):
        return self.get_handler_stats(self.engine.post_handlers[0].post, ["depth", "nodes", "score"])

    def name(self):
        try:
            return self.engine.features.get("myname")
        except Exception:
            return None
