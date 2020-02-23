import argparse
import json
import logging
import threading
import time
import traceback

from typing import Callable, List, Optional

import backoff
import chess
import chess.polyglot
import chess.variant
from requests.exceptions import ChunkedEncodingError, ConnectionError, HTTPError
from urllib3.exceptions import ProtocolError

from src import lichess, model, engine_wrapper
from src.color_logger import enable_color_logging
from src.config import load_config
from src.conversation import Conversation, ChatLine

try:
    # New in version 3.5: Previously, BadStatusLine('') was raised.
    from http.client import RemoteDisconnected
except ImportError:
    from http.client import BadStatusLine as RemoteDisconnected

__version__: str = "testing"

# terminated is a list so it can be accessed as a pseudo-pointer.
_TERMINATED: list = []

_QUEUE: List[dict] = []
_CHALLENGE_QUEUE: List[model.Challenge] = []

logger: logging.Logger = logging.getLogger(__name__)


def is_final(exception) -> bool:
    return (isinstance(exception, HTTPError) and exception.response.status_code < 500) or _TERMINATED


def upgrade_account(li: lichess.Lichess) -> bool:
    if li.upgrade_to_bot_account() is None:
        return False

    logger.info("Successfully upgraded to Bot Account!")
    return True


@backoff.on_exception(backoff.expo, BaseException, max_time=600, giveup=is_final)
def watch_control_stream(li: lichess.Lichess) -> None:
    response = li.get_event_stream()
    try:
        for line in response.iter_lines():
            if _TERMINATED:
                return

            if line:
                event = json.loads(line.decode('utf-8'))
                _QUEUE.append(event)
            else:
                _QUEUE.append({"type": "ping"})
    except (RemoteDisconnected, ChunkedEncodingError, ConnectionError, ProtocolError) as exception:
        logger.error("Terminating client due to connection error")
        traceback.print_exception(type(exception), exception, exception.__traceback__)
        _QUEUE.append({"type": "terminated"})


def start(li: lichess.Lichess, user_profile: dict, engine_factory: Callable, config: dict) -> None:
    global _QUEUE, _CHALLENGE_QUEUE

    challenge_config = config["challenge"]

    logger.info("You're now connected to {} and awaiting challenges.".format(config["url"]))
    control_stream = threading.Thread(target=lambda: watch_control_stream(li))
    control_stream.start()

    game_running = True

    while not _TERMINATED:
        while not _QUEUE: ...
        event = _QUEUE.pop(0)

        if event["type"] == "terminated":
            break

        elif event["type"] == "local_game_done":
            game_running = False

            logger.info(f"+++ Process Free. Total Queued: {len(_CHALLENGE_QUEUE)}. Total Used: 0")

        elif event["type"] == "challenge":
            challenge = model.Challenge(event["challenge"])
            if challenge.is_supported(challenge_config) and not challenge.is_ignore(challenge_config):
                _CHALLENGE_QUEUE.append(challenge)
                if challenge_config.get("sort_by", "best") == "best":
                    _CHALLENGE_QUEUE = sorted(_CHALLENGE_QUEUE, key=lambda c: -c.score())
            elif challenge.is_ignore(challenge_config):
                continue
            else:
                try:
                    li.decline_challenge(challenge.id)
                    logger.info("    Decline {}".format(challenge))
                except HTTPError as exception:
                    if exception.response.status_code != 404:  # ignore missing challenge
                        raise exception

        elif event["type"] == "gameStart":
            game_running = True

            game_id = event["game"]["id"]
            t = threading.Thread(target=lambda: play_game(li, game_id, engine_factory, user_profile, config))
            t.start()

            logger.info(f"--- Process Used. Total Queued: {len(_CHALLENGE_QUEUE)}. Total Used: 1")

        # keep processing the queue until empty or max_games is reached
        while not game_running and _CHALLENGE_QUEUE:
            challenge = _CHALLENGE_QUEUE.pop(0)
            try:
                game_running = True

                _ = li.accept_challenge(challenge.id)
                logger.info("    Accept {}".format(challenge))
                logger.info(f"--- Process Queue. Total Queued: {len(_CHALLENGE_QUEUE)}. Total Used: 1")
            except HTTPError as exception:
                if exception.response.status_code == 404:  # ignore missing challenge
                    logger.info("    Skip missing {}".format(challenge))
                else:
                    raise exception

    _TERMINATED.append(None)
    logger.info("Terminated")
    control_stream.join()


@backoff.on_exception(backoff.expo, BaseException, max_time=600, giveup=is_final)
def play_game(li: lichess.Lichess, game_id: str, engine_factory: Callable, user_profile: dict, config: dict) -> None:
    global _CHALLENGE_QUEUE

    response = li.get_game_stream(game_id)
    lines = response.iter_lines()

    # initial response of stream will be the full game info; store it.
    game = model.Game(
        json.loads(next(lines).decode('utf-8')),
        user_profile["username"],
        li.baseUrl,
        config.get("abort_time", 20)
    )

    board = setup_board(game)
    engine = engine_factory(board, game.speed)

    conversation = Conversation(
        game, engine, li, __version__, _CHALLENGE_QUEUE, config.get("chat_commands", {}),
        user_profile["username"]
    )

    logger.info("+++ {}".format(game))

    engine_cfg = config["engine"]
    polyglot_cfg = engine_cfg.get("polyglot", {})
    book_cfg = polyglot_cfg.get("book", {})

    try:
        if not polyglot_cfg.get("enabled") or not play_first_book_move(game, engine, board, li, book_cfg):
            play_first_move(game, engine, board, li)

        engine.set_time_control(game)

        for binary_chunk in lines:
            upd = json.loads(binary_chunk.decode('utf-8')) if binary_chunk else None
            u_type = upd["type"] if upd else "ping"

            if u_type == "chatLine":
                conversation.react(ChatLine(upd), game)

            elif u_type == "gameState":
                game.state = upd
                moves = upd["moves"].split()
                board = update_board(board, moves[-1])
                if not board.is_game_over() and is_engine_move(game, moves):
                    if not engine.did_first_move:
                        if not polyglot_cfg.get("enabled") or \
                                not play_first_book_move(game, engine, board, li, book_cfg):
                            play_first_move(game, engine, board, li)
                        continue

                    if config.get("fake_think_time") and len(moves) > 9:
                        delay = min(game.clock_initial, game.my_remaining_seconds()) * 0.015
                        accel = 1 - max(0, min(100, len(moves) - 20)) / 150
                        sleep = min(5, delay * accel)
                        time.sleep(sleep)

                    best_move = None
                    if polyglot_cfg.get("enabled") and len(moves) <= polyglot_cfg.get("max_depth", 8) * 2 - 1:
                        best_move = get_book_move(board, book_cfg)
                    if best_move is None:
                        def move_function():
                            return_value = engine.search(board, upd["wtime"], upd["btime"], upd["winc"],
                                                         upd["binc"])
                            if engine.is_game_over:
                                return

                            # do this after making sure game not over
                            move, draw_offer, resign = return_value
                            try:
                                if resign:
                                    li.resign(game.id)
                                else:
                                    li.make_move(game.id, move, offering_draw=draw_offer)
                            except (HTTPError, ValueError):  # ValueError if engine closed.
                                pass

                            game.abort_in(config.get("abort_time", 20))

                        move_thread = threading.Thread(target=move_function)
                        move_thread.start()
                        continue

                    li.make_move(game.id, best_move)
                    game.abort_in(config.get("abort_time", 20))

            elif u_type == "ping":
                if game.should_abort_now():
                    logger.info("    Aborting {} by lack of activity".format(game.url()))
                    li.abort(game.id)

    except HTTPError:
        ongoing_game = tuple(filter(lambda g: g["gameID"] == game.id, li.get_ongoing_games()))
        if ongoing_game != ():
            logger.warning("Abandoning game due to HTTP " + response.status_code)

    except (RemoteDisconnected, ChunkedEncodingError, ConnectionError, ProtocolError) as exception:
        logger.error("Abandoning game due to connection error")
        traceback.print_exception(type(exception), exception, exception.__traceback__)

    finally:
        logger.info("--- {} Game over".format(game.url()))
        engine.is_game_over = True
        engine.quit()

        _QUEUE.append({"type": "local_game_done"})


def play_first_move(game: model.Game, engine: engine_wrapper.EngineWrapper,
                    board: chess.Board, li: lichess.Lichess) -> bool:
    moves = game.state["moves"].split()
    if is_engine_move(game, moves):
        # need to hard code first movetime since Lichess has 30 sec limit.
        best_move = engine.first_search(board, 10000)
        li.make_move(game.id, best_move)
        return True
    return False


def play_first_book_move(game: model.Game, engine: engine_wrapper.EngineWrapper,
                         board: chess.Board, li: lichess.Lichess, config: dict) -> bool:
    moves = game.state["moves"].split()
    if is_engine_move(game, moves):
        book_move = get_book_move(board, config)
        if book_move:
            li.make_move(game.id, str(book_move))
            return True
        else:
            return play_first_move(game, engine, board, li)
    return False


def get_book_move(board: chess.Board, config: dict) -> Optional[chess.Move]:
    if board.uci_variant == "chess":
        book = config["standard"]
    else:
        if config.get("{}".format(board.uci_variant)):
            book = config["{}".format(board.uci_variant)]
        else:
            return None

    with chess.polyglot.open_reader(book) as reader:
        try:
            selection = config.get("selection", "weighted_random")
            if selection == "weighted_random":
                move = reader.weighted_choice(board).move()
            elif selection == "uniform_random":
                move = reader.choice(board, minimum_weight=config.get("min_weight", 1)).move()
            elif selection == "best_move":
                move = reader.find(board, minimum_weight=config.get("min_weight", 1)).move()
        except IndexError:
            # python-chess raises "IndexError" if no entries found
            move = None

    if move is not None:
        logger.info("Got move {} from book {}".format(move, book))

    return move


def setup_board(game: model.Game) -> chess.Board:
    if game.variant_name.lower() == "chess960":
        board = chess.Board(game.initial_fen, chess960=True)
    elif game.variant_name == "From Position":
        board = chess.Board(game.initial_fen)
    else:
        board = chess.variant.find_variant(game.variant_name)()

    moves = game.state["moves"].split()
    for move in moves:
        board = update_board(board, move)

    return board


def is_white_to_move(game: model.Game, moves: list) -> bool:
    return len(moves) % 2 == (0 if game.white_starts else 1)


def is_engine_move(game: model.Game, moves: list) -> bool:
    return game.is_white == is_white_to_move(game, moves)


def update_board(board: chess.Board, move: str) -> chess.Board:
    uci_move = chess.Move.from_uci(move)
    board.push(uci_move)
    return board


def intro() -> str:
    return fr"""
    .   _/|
    .  // o\
    .  || ._)  lichess-bot {__version__}
    .  //__\
    .  )___(   Play on Lichess with a bot
    """


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Play on Lichess with a bot')
    parser.add_argument('-u', action='store_true', help='Add this flag to upgrade your account to a bot account.')
    parser.add_argument('-v', action='store_true', help='Verbose output. Changes log level from INFO to DEBUG.')
    parser.add_argument('--config', help='Specify a configuration file (defaults to ./config.yml)')
    parser.add_argument('-l', '--logfile', help="Log file to append logs to.", default=None)
    arguments = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if arguments.v else logging.INFO,
        filename=arguments.logfile,
        format="%(asctime)-15s: %(message)s"
    )
    enable_color_logging(debug_lvl=logging.DEBUG if arguments.v else logging.INFO)
    logger.info(intro())

    CONFIG = load_config(arguments.config or "./config.yml")

    lichess_obj = lichess.Lichess(CONFIG["token"], CONFIG["url"], __version__)
    profile_dict = lichess_obj.get_profile()
    username = profile_dict["username"]
    is_bot = profile_dict.get("title") == "BOT"

    logger.info("Welcome {}!".format(username))

    if arguments.u is True and is_bot is False:
        is_bot = upgrade_account(lichess_obj)

    if is_bot:
        engine_foo = lambda *args, **kwargs: engine_wrapper.create_engine(CONFIG, *args, **kwargs)

        try:
            start(lichess_obj, profile_dict, engine_foo, CONFIG)
        except KeyboardInterrupt:
            logger.debug("Received SIGINT. Terminating client.")
            _TERMINATED.append(None)
    else:
        logger.error("{} is not a bot account. Please upgrade it to a bot account!".format(profile_dict["username"]))
