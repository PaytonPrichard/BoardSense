"""
BoardSense — lichess.py
Thin client for the public Lichess API.
No authentication required.
"""

import io
from datetime import datetime, timedelta, timezone

import chess.pgn
import requests

_HEADERS = {
    "User-Agent": "BoardSenseApp/1.0 (github.com/boardsense)",
    "Accept": "application/x-chess-pgn",
}


def _parse_pgn_stream(text: str) -> list[dict]:
    """Split a multi-game PGN stream into individual {pgn, headers} dicts."""
    results: list[dict] = []
    pgn_io = io.StringIO(text)
    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break
        if game.next() is None:
            continue  # skip games with no moves
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        pgn_text = game.accept(exporter)
        results.append({"pgn": pgn_text, "headers": dict(game.headers)})
    return results


def fetch_recent_games(
    username: str, n_months: int = 1, max_games: int = 50
) -> list[dict]:
    """
    Fetch the most recent games for a Lichess username.
    Returns a list of {pgn, headers} dicts, newest first.
    """
    since_dt = datetime.now(timezone.utc) - timedelta(days=n_months * 30)
    since_ms = int(since_dt.timestamp() * 1000)

    url = f"https://lichess.org/api/games/user/{username}"
    params = {
        "max": max_games,
        "since": since_ms,
        "opening": "true",
        "tags": "true",
    }

    resp = requests.get(url, headers=_HEADERS, params=params, timeout=30)
    resp.raise_for_status()

    games = _parse_pgn_stream(resp.text)
    games.reverse()  # newest first
    return games
