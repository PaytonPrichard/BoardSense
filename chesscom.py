"""
BoardSense — chesscom.py
Thin client for the public Chess.com API.
No authentication required.
"""

import io
import time
import requests
import chess.pgn

BASE = "https://api.chess.com/pub"
_HEADERS = {"User-Agent": "BoardSenseApp/1.0 (github.com/boardsense)"}
_RATE_DELAY = 0.5   # seconds between archive requests
_CACHE_TTL = 600    # cache fetch results for 10 minutes
_cache: dict[tuple, tuple[float, list]] = {}  # (username, n_months) -> (timestamp, games)


def get_archives(username: str) -> list[str]:
    """
    Return monthly archive URLs for a player, most recent first.
    Each URL covers one calendar month of games.
    """
    url  = f"{BASE}/player/{username.lower()}/games/archives"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach Chess.com — check your connection and try again. ({e})") from e
    if resp.status_code == 403:
        raise RuntimeError(
            "Chess.com rate limit reached — please wait a few minutes and try again."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            f"Chess.com user '{username}' not found. Please check the spelling."
        )
    resp.raise_for_status()
    try:
        archives = resp.json().get("archives", [])
    except (ValueError, KeyError):
        raise RuntimeError("Unexpected response from Chess.com — please try again.")
    return list(reversed(archives))


def fetch_month(archive_url: str) -> list[dict]:
    """
    Fetch all games from one monthly archive URL.
    Returns a list of {pgn: str, headers: dict} dicts (skips games with no moves).
    """
    try:
        resp = requests.get(archive_url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        games_raw = resp.json().get("games", [])
    except requests.RequestException:
        return []  # skip this month gracefully
    except (ValueError, KeyError):
        return []

    result = []
    for g in games_raw:
        pgn_text = g.get("pgn", "").strip()
        if not pgn_text:
            continue
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None or game.next() is None:
                continue
            result.append({"pgn": pgn_text, "headers": dict(game.headers)})
        except Exception:
            continue
    return result


def fetch_recent_games(username: str, n_months: int = 1, *, bypass_cache: bool = False) -> list[dict]:
    """
    Fetch the most recent n_months of games for a Chess.com username.
    Returns a flat list of {pgn, headers} dicts, newest games first.
    Results are cached for 10 minutes to avoid rate limits.
    """
    key = (username.lower(), n_months)
    now = time.time()
    if not bypass_cache and key in _cache:
        cached_at, cached_games = _cache[key]
        if now - cached_at < _CACHE_TTL:
            return cached_games

    archives  = get_archives(username)
    all_games: list[dict] = []
    for i, url in enumerate(archives[:n_months]):
        try:
            if i > 0:
                time.sleep(_RATE_DELAY)
            month_games = fetch_month(url)
            all_games.extend(reversed(month_games))   # newest-first within month
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                raise RuntimeError(
                    "Chess.com rate limit reached — please wait a few minutes and try again."
                ) from e
            continue
        except Exception:
            continue

    _cache[key] = (now, all_games)
    return all_games
