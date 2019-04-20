import copy
import os
import subprocess
import time

import backoff
import chess
import chess.uci
import chess.xboard


INFINITY = 100000


@backoff.on_exception(backoff.expo, BaseException, max_time=120)
def create_engine(config, board):
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
        "resignation": cfg.get("resignation", {"threshold": 10 * INFINITY, "sustain_turns": 1}),
    }

    if engine_type == "xboard":
        return XBoardEngine(board, commands, cfg.get("xboard_options", {}) or {}, game_end_conditions,
                            silence_stderr)

    return UCIEngine(board, commands, cfg.get("uci_options", {}) or {}, game_end_conditions, silence_stderr, ponder)


class EngineWrapper:

    def __init__(self, board, commands, options, game_end_conditions, silence_stderr=False, ponder_on=False):
        self.board = board
        self.commands = commands
        self.options = options
        self.draw_conditions = game_end_conditions["draw"]
        self.resignation_conditions = game_end_conditions["resignation"]
        self.silence_stderr = silence_stderr
        self.ponder_on = ponder_on

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

    @staticmethod
    def print_handler_stats(info, stats):
        for stat in filter(lambda s: s in info, stats):
            print("    {}: {}".format(stat, info[stat]))

    @staticmethod
    def get_handler_stats(info, stats):
        stats_str = []
        for stat in filter(lambda s: s in info, stats):
            stats_str.append("{}: {}".format(stat, info[stat]))
        return stats_str


class UCIEngine(EngineWrapper):

    def __init__(self, board, commands, options, game_end_conditions, silence_stderr=False, ponder_on=False):
        commands = commands[0] if len(commands) == 1 else commands
        self.go_commands = options.get("go_commands", {})

        self.engine = chess.uci.popen_engine(commands, stderr=subprocess.DEVNULL if silence_stderr else None)
        self.engine.uci()

        if options:
            self.engine.setoption(options)

        self.draw_conditions = game_end_conditions["draw"]
        self.resignation_conditions = game_end_conditions["resignation"]

        self.engine.setoption({
            "UCI_Variant": type(board).uci_variant,
            "UCI_Chess960": board.chess960
        })

        self.engine.position(board)

        info_handler = chess.uci.InfoHandler()
        self.engine.info_handlers.append(info_handler)

        self.ponder_on = ponder_on
        self.ponder_command = False
        self.ponder_board = chess.Board()

        self.past_scores = []
        self.move_number = 1

    def first_search(self, board, movetime):
        self.engine.position(board)
        best_move, _ = self.engine.go(movetime=movetime)
        return best_move

    def search(self, board, wtime, btime, winc, binc):
        self.move_number += 1

        search_start_time = time.time()
        cmds = self.go_commands

        best_move = None
        ponder_move = None

        if self.ponder_command:
            if self.ponder_board.fen() == board.fen():
                self.engine.ponderhit()
                while not self.ponder_command.done():
                    pass
                best_move, ponder_move = self.ponder_command.result()
            else:
                self.engine.stop()

            self.ponder_command = False

        if best_move is None:
            self.engine.position(board)
            best_move, ponder_move = self.engine.go(
                wtime=wtime,
                btime=btime,
                winc=winc,
                binc=binc,
                depth=cmds.get("depth"),
                nodes=cmds.get("nodes"),
                movetime=cmds.get("movetime")
            )

        try:
            score = self.engine.info_handlers[0].info["score"][1]
            score = score.cp if score.cp is not None else INFINITY * score.mate
            self.past_scores.append(score)
        except (KeyError, AttributeError):
            self.past_scores = []  # reset the past scores so nothing will screw up if engine doesn't report score
        if self.ponder_on and ponder_move is not None:
            if board.turn == chess.WHITE:
                wtime -= int(1000 * (time.time() - search_start_time))
            else:
                btime -= int(1000 * (time.time() - search_start_time))

            self.ponder_board = copy.deepcopy(board)
            self.ponder_board.push(best_move)
            self.ponder_board.push(ponder_move)
            self.ponder(self.ponder_board, wtime, btime, winc, binc)

        draw_scores = self.past_scores[-self.draw_conditions["sustain_turns"]:]
        draw = max(draw_scores, key=abs) <= self.draw_conditions["threshold"] \
            if len(self.past_scores) >= self.draw_conditions["sustain_turns"] and \
            self.move_number >= self.draw_conditions["minimum_turns"] \
            else False

        resign_scores = self.past_scores[-self.resignation_conditions["sustain_turns"]:]
        resign = max(resign_scores) <= -self.resignation_conditions["threshold"] \
            if len(resign_scores) >= self.resignation_conditions["sustain_turns"] else False

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
        self.print_handler_stats(self.engine.info_handlers[0].info, ["string", "depth", "nps", "nodes", "score"])

    def get_stats(self):
        return self.get_handler_stats(self.engine.info_handlers[0].info, ["depth", "nps", "nodes", "score"])


class XBoardEngine(EngineWrapper):

    def __init__(self, board, commands, options, game_end_conditions, silence_stderr=False, ponder_on=False):
        commands = commands[0] if len(commands) == 1 else commands
        self.engine = chess.xboard.popen_engine(commands, stderr=subprocess.DEVNULL if silence_stderr else None)
        self.engine.xboard()

        if board.chess960:
            self.engine.send_variant("fischerandom")
        elif type(board).uci_variant != "chess":
            self.engine.send_variant(type(board).uci_variant)

        if options:
            self._handle_options(options)

        self.draw_conditions = game_end_conditions["draw"]
        self.resignation_conditions = game_end_conditions["resignation"]

        self.engine.setboard(board)

        post_handler = chess.xboard.PostHandler()
        self.engine.post_handlers.append(post_handler)

        self.past_scores = []
        self.move_number = 1

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
                    except EngineStateException:
                        # If the user specifies more TBs than the engine supports, ignore the error.
                        pass
            else:
                try:
                    self.engine.features.set_option(option, value)
                except EngineStateException:
                    pass

    def set_time_control(self, game):
        minutes = game.clock_initial / 1000 / 60
        seconds = game.clock_initial / 1000 % 60
        inc = game.clock_increment / 1000
        self.engine.level(0, minutes, seconds, inc)

    def first_search(self, board, movetime):
        self.engine.setboard(board)
        self.engine.level(0, 0, movetime / 1000, 0)
        bestmove = self.engine.go()
        return bestmove

    def search(self, board, wtime, btime, winc, binc):
        self.move_number += 1

        self.engine.setboard(board)
        if board.turn == chess.WHITE:
            self.engine.time(wtime / 10)
            self.engine.otim(btime / 10)
        else:
            self.engine.time(btime / 10)
            self.engine.otim(wtime / 10)
        best_move = self.engine.go()

        try:
            score = self.engine.post_handlers[0].post["score"][1]
            score = score.cp if score.cp is not None else INFINITY * score.mate
            self.past_scores.append(score)
        except (KeyError, AttributeError):
            self.past_scores = []  # reset the past scores so nothing will screw up if engine doesn't report score

        draw_scores = self.past_scores[-self.draw_conditions["sustain_turns"]:]
        draw = max(draw_scores, key=abs) <= self.draw_conditions["threshold"] \
            if len(self.past_scores) >= self.draw_conditions["sustain_turns"] and \
            self.move_number >= self.draw_conditions["minimum_turns"] \
            else False

        resign_scores = self.past_scores[-self.resignation_conditions["sustain_turns"]:]
        resign = max(resign_scores) <= -self.resignation_conditions["threshold"] \
            if len(resign_scores) >= self.resignation_conditions["sustain_turns"] else False

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
