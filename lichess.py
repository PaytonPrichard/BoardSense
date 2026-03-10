"""
BoardSense — lichess.py
Thin client for the public Lichess API.
No authentication required.
"""

import io
import time
from datetime import datetime, timedelta, timezone

import chess.pgn
import requests

_HEADERS = {
    "User-Agent": "BoardSenseApp/1.0 (github.com/boardsense)",
    "Accept": "application/x-chess-pgn",
}
_CACHE_TTL = 600  # cache fetch results for 10 minutes
_cache: dict[tuple, tuple[float, list]] = {}  # (username, n_months) -> (timestamp, games)


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
    username: str, n_months: int = 1, max_games: int = 50, *, bypass_cache: bool = False,
) -> list[dict]:
    """
    Fetch the most recent games for a Lichess username.
    Returns a list of {pgn, headers} dicts, newest first.
    Results are cached for 10 minutes to avoid rate limits.
    """
    key = (username.lower(), n_months)
    now = time.time()
    if not bypass_cache and key in _cache:
        cached_at, cached_games = _cache[key]
        if now - cached_at < _CACHE_TTL:
            return cached_games

    since_dt = datetime.now(timezone.utc) - timedelta(days=n_months * 30)
    since_ms = int(since_dt.timestamp() * 1000)

    url = f"https://lichess.org/api/games/user/{username}"
    params = {
        "max": max_games,
        "since": since_ms,
        "opening": "true",
        "tags": "true",
    }

    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach Lichess — check your connection and try again. ({e})") from e
    if resp.status_code == 429:
        raise RuntimeError(
            "Lichess rate limit reached — please wait a few minutes and try again."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            f"Lichess user '{username}' not found. Please check the spelling."
        )
    resp.raise_for_status()

    try:
        games = _parse_pgn_stream(resp.text)
    except Exception:
        raise RuntimeError("Failed to parse games from Lichess. Please try again.")
    games.reverse()  # newest first
    _cache[key] = (now, games)
    return games
