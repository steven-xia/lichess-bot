from typing import Dict, Optional

import backoff
import requests
from urllib.parse import urljoin

from requests.exceptions import ConnectionError, HTTPError
from urllib3.exceptions import ProtocolError
from http.client import RemoteDisconnected

_TERMINATED: list = []

_BACKOFF_EXCEPTIONS: tuple = (
    ConnectionError,
    HTTPError,
    ProtocolError,
    RemoteDisconnected,
)

_ENDPOINTS: Dict[str, str] = {
    "abort": "/api/bot/game/{}/abort",
    "accept": "/api/challenge/{}/accept",
    "chat": "/api/bot/game/{}/chat",
    "decline": "/api/challenge/{}/decline",
    "game": "/api/bot/game/{}",
    "move": "/api/bot/game/{}/move/{}",
    "playing": "/api/account/playing",
    "profile": "/api/account",
    "resign": "/api/bot/game/{}/resign",
    "stream": "/api/bot/game/stream/{}",
    "stream_event": "/api/stream/event",
    "upgrade": "/api/bot/account/upgrade",
}


def is_final(exc) -> bool:
    return (isinstance(exc, HTTPError) and exc.response.status_code < 500) or _TERMINATED


# lichess api documentation: https://lichess.org/api
class Lichess:
    def __init__(self, token: str, url: str, version: str):
        self.version: str = version
        self.header: Dict[str, str] = {
            "Authorization": "Bearer {}".format(token)
        }

        self.baseUrl: str = url
        self.session: requests.Session = requests.Session()
        self.session.headers.update(self.header)
        self.set_user_agent("?")

    @backoff.on_exception(backoff.expo, _BACKOFF_EXCEPTIONS, max_time=120, giveup=is_final)
    def _api_get(self, path: str) -> dict:
        url = urljoin(self.baseUrl, path)
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    @backoff.on_exception(backoff.expo, _BACKOFF_EXCEPTIONS, max_time=20, giveup=is_final)
    def _api_post(self, path: str, data: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = urljoin(self.baseUrl, path)
        response = self.session.post(url, data=data, params=params)
        response.raise_for_status()
        return response.json()

    def abort(self, game_id: str) -> dict:
        return self._api_post(_ENDPOINTS["abort"].format(game_id))

    def accept_challenge(self, challenge_id: str) -> dict:
        return self._api_post(_ENDPOINTS["accept"].format(challenge_id))

    def chat(self, game_id: str, room: str, text: str) -> dict:
        payload = {'room': room, 'text': text}
        return self._api_post(_ENDPOINTS["chat"].format(game_id), data=payload)

    def decline_challenge(self, challenge_id: str) -> dict:
        return self._api_post(_ENDPOINTS["decline"].format(challenge_id))

    def get_event_stream(self) -> requests.Response:
        url = urljoin(self.baseUrl, _ENDPOINTS["stream_event"])
        return requests.get(url, headers=self.header, stream=True)

    def get_game(self, game_id: str) -> dict:
        return self._api_get(_ENDPOINTS["game"].format(game_id))

    def get_game_stream(self, game_id: str) -> requests.Response:
        url = urljoin(self.baseUrl, _ENDPOINTS["stream"].format(game_id))
        return requests.get(url, headers=self.header, stream=True)

    def get_ongoing_games(self) -> dict:
        ongoing_games = self._api_get(_ENDPOINTS["playing"])["nowPlaying"]
        return ongoing_games

    def get_profile(self) -> dict:
        profile = self._api_get(_ENDPOINTS["profile"])
        self.set_user_agent(profile["username"])
        return profile

    def make_move(self, game_id: str, move: str, offering_draw: bool = False) -> dict:
        return self._api_post(
            _ENDPOINTS["move"].format(game_id, move),
            params={"offeringDraw": str(offering_draw).lower()}
        )

    def resign(self, game_id: str) -> None:
        self._api_post(_ENDPOINTS["resign"].format(game_id))

    def set_user_agent(self, username: str) -> None:
        self.header.update({"User-Agent": "lichess-bot/{} user:{}".format(self.version, username)})
        self.session.headers.update(self.header)

    def upgrade_to_bot_account(self) -> dict:
        return self._api_post(_ENDPOINTS["upgrade"])


def __init__(terminated_pointer: list):
    global _TERMINATED
    _TERMINATED = terminated_pointer
