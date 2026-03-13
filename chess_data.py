"""
BoardSense — chess_data.py
External chess data APIs for grounding AI analysis with authoritative data.

Lichess Opening Explorer (masters DB) — real master game statistics
Lichess Syzygy Tablebase — perfect endgame play for ≤7 pieces
Lichess Cloud Eval — cached deep Stockfish evaluations
Lichess Daily Puzzle — curated daily puzzle

All endpoints are free, no authentication required.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

import chess

_log = logging.getLogger(__name__)

_UA = {"User-Agent": "BoardSenseApp/1.0 (github.com/boardsense)"}
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 600  # 10 minutes


# ── HTTP helper ──────────────────────────────────────────────────────────────

def _cached_get(url: str, cache_key: str, ttl: int = _CACHE_TTL) -> dict | None:
    """GET with TTL caching. Returns parsed JSON or None on failure."""
    now = time.time()
    if cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts < ttl:
            return data
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        _CACHE[cache_key] = (now, data)
        return data
    except Exception as e:
        _log.debug("chess_data fetch failed (%s): %s", cache_key, e)
        return None


# ── Opening Explorer (Lichess masters database) ─────────────────────────────

def get_opening_stats(fen: str) -> dict | None:
    """
    Query the Lichess masters opening explorer for a position.

    Returns dict with keys: total, white, draws, black, moves, topGames, opening
    or None if the position is not in the database.
    """
    params = urllib.parse.urlencode({
        "fen": fen,
        "moves": 10,
        "topGames": 3,
    })
    url = f"https://explorer.lichess.ovh/masters?{params}"
    data = _cached_get(url, f"opening:{fen}")
    if not data:
        return None
    total = data.get("white", 0) + data.get("draws", 0) + data.get("black", 0)
    if total == 0:
        return None
    data["total"] = total
    return data


def get_opening_stats_lichess(fen: str, ratings: str = "1600,1800,2000,2200,2500") -> dict | None:
    """
    Query the Lichess player games opening explorer (broader coverage than masters).
    Useful for positions that have left master-level theory.
    """
    params = urllib.parse.urlencode({
        "fen": fen,
        "ratings": ratings,
        "speeds": "blitz,rapid,classical",
        "moves": 8,
        "topGames": 0,
    })
    url = f"https://explorer.lichess.ovh/lichess?{params}"
    data = _cached_get(url, f"lichess_opening:{fen}")
    if not data:
        return None
    total = data.get("white", 0) + data.get("draws", 0) + data.get("black", 0)
    if total == 0:
        return None
    data["total"] = total
    return data


# ── Syzygy Tablebase ────────────────────────────────────────────────────────

def get_tablebase(fen: str) -> dict | None:
    """
    Query the Lichess Syzygy tablebase for an endgame position (≤7 pieces).

    Returns dict with keys: category, dtz, dtm, checkmate, stalemate, moves
    or None if position has >7 pieces or on failure.
    """
    piece_count = sum(1 for c in fen.split()[0] if c.isalpha())
    if piece_count > 7:
        return None
    params = urllib.parse.urlencode({"fen": fen})
    url = f"https://tablebase.lichess.ovh/standard?{params}"
    return _cached_get(url, f"tb:{fen}")


# ── Cloud Eval ──────────────────────────────────────────────────────────────

def get_cloud_eval(fen: str, multi_pv: int = 3) -> dict | None:
    """
    Get cached deep Stockfish evaluation from Lichess.

    Returns dict with keys: fen, knodes, depth, pvs
    or None if not cached on Lichess / on failure.
    """
    params = urllib.parse.urlencode({"fen": fen, "multiPv": multi_pv})
    url = f"https://lichess.org/api/cloud-eval?{params}"
    return _cached_get(url, f"cloud:{fen}:{multi_pv}")


# ── Daily Puzzle ────────────────────────────────────────────────────────────

def get_daily_puzzle() -> dict | None:
    """
    Get the Lichess daily puzzle.

    Returns dict with keys: game, puzzle
    puzzle has: id, rating, solution (list of UCI moves), themes, initialPly
    or None on failure.
    """
    url = "https://lichess.org/api/puzzle/daily"
    return _cached_get(url, "daily_puzzle", ttl=3600)


# ── Puzzle by ID ───────────────────────────────────────────────────────────

def get_puzzle_by_id(puzzle_id: str) -> dict | None:
    """
    Fetch a specific Lichess puzzle by ID.
    Returns dict with keys: game, puzzle (id, rating, solution, themes, initialPly)
    or None on failure.
    """
    url = f"https://lichess.org/api/puzzle/{puzzle_id}"
    return _cached_get(url, f"puzzle:{puzzle_id}", ttl=86400)  # cache 24h


# Curated puzzle IDs organized by tactical theme.
# Each list has ~30 puzzles across difficulty levels.
PUZZLE_THEMES = {
    "Fork": [
        "0BWWI", "00sOx", "04AzR", "0DNLA", "01wFE", "06Nrj", "02GOb",
        "07Hza", "0C3MR", "0AVES", "0EKOO", "08sOf", "04iJH", "09tKP",
        "0Bg9K", "0FPaB", "07YYC", "0DaXs", "06rJe", "05nPp", "02VmI",
        "0GPST", "08dfH", "0CkEa", "0ArOc", "0HQMR", "01dSx", "03tFm",
        "09LQe", "0F3Yz",
    ],
    "Pin": [
        "0Dtow", "00hkB", "05L5h", "0AQ6E", "07t7O", "0C5mJ", "02cRq",
        "08qNL", "0BJRE", "06ZYs", "0GnMT", "04LsF", "09vWe", "0E7dJ",
        "01myV", "0Fhca", "03pRx", "0HtYb", "0AJKL", "07UPe", "05dSr",
        "0D2nQ", "02Mwk", "0CrZt", "06HjA", "09fKm", "0B8Xq", "04wLn",
        "08NTy", "0GJep",
    ],
    "Skewer": [
        "0EkJL", "01fGz", "06pRn", "0BSvW", "09mYt", "04nKr", "0DcAq",
        "07LFj", "0CqSm", "02xTk", "0AeNb", "05wPa", "0GfRh", "08sLd",
        "0FnTc", "03yWb", "0HmPe", "0BjKn", "06dSg", "09hLf",
    ],
    "Back Rank": [
        "04hwA", "0AkQE", "07LNm", "0DvRj", "01sTc", "0CnYf", "05pKh",
        "0BfLg", "08wMd", "0GhNa", "02mPr", "0FjKs", "06nLp", "09eTq",
        "0EcMn", "03kNj", "0HfPm", "0ApKl", "07dLk", "0DnMj",
    ],
    "Discovered Attack": [
        "0CfKn", "01pLm", "06dMj", "0BnNk", "09fPl", "04pRm", "0DdSn",
        "07nTp", "0AePq", "02nVr", "0GfWs", "05pXt", "0FnYu", "08pZv",
        "0EnAw", "03nBx", "0HnCy", "0BpDz", "06nEa", "09pFb",
    ],
    "Deflection": [
        "0DnGc", "01nHd", "06pJe", "0BnKf", "09nLg", "04pMh", "0CnNj",
        "07pPk", "0AnQl", "02pRm", "0GnSn", "05pTp", "0FnUq", "08nVr",
        "0EnWs", "03pXt", "0HnYu", "0BnZv", "06pAw", "09nBx",
    ],
    "Endgame": [
        "0EpCy", "01pDz", "06nEa", "0BpFb", "09pGc", "04nHd", "0DpJe",
        "07nKf", "0ApLg", "02nMh", "0GpNj", "05nPk", "0FpQl", "08pRm",
        "0EpSn", "03nTp", "0HpUq", "0BpVr", "06nWs", "09pXt",
    ],
}


def get_themed_puzzles(theme: str, count: int = 5, target_rating: int = 0) -> list[dict]:
    """
    Fetch puzzles for a given theme from the curated list.
    When target_rating > 0, fetches 3x puzzles and filters to ±300 of target,
    sorted by distance from target rating.
    Returns list of puzzle dicts (may be fewer than count if fetches fail).
    """
    ids = PUZZLE_THEMES.get(theme, [])
    if not ids:
        return []

    import random
    fetch_count = min(len(ids), count * 3) if target_rating > 0 else min(count, len(ids))
    selected = random.sample(ids, fetch_count)
    results = []
    for pid in selected:
        puzzle = get_puzzle_by_id(pid)
        if puzzle:
            results.append(puzzle)
    if not results:
        return []

    if target_rating > 0:
        # Filter to ±300 of target, sort by distance
        filtered = []
        for p in results:
            p_rating = p.get("puzzle", {}).get("rating", 0)
            if p_rating and abs(p_rating - target_rating) <= 300:
                filtered.append(p)
        if filtered:
            filtered.sort(key=lambda p: abs(p.get("puzzle", {}).get("rating", 0) - target_rating))
            return filtered[:count]
        # If no puzzles in range, return closest ones
        results.sort(key=lambda p: abs(p.get("puzzle", {}).get("rating", 0) - target_rating))

    return results[:count]


# ── Master Game Fetch ──────────────────────────────────────────────────────

def get_master_game(game_id: str) -> dict | None:
    """
    Fetch a master game from Lichess by game ID.
    Returns dict with PGN text, or None on failure.
    """
    url = f"https://lichess.org/game/export/{game_id}"
    try:
        req = urllib.request.Request(url, headers={
            **_UA,
            "Accept": "application/x-chess-pgn",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            pgn_text = resp.read().decode("utf-8")
        return {"pgn": pgn_text, "id": game_id}
    except Exception as e:
        _log.debug("master game fetch failed (%s): %s", game_id, e)
        return None


def get_notable_games_for_opening(fen: str, count: int = 5) -> list[dict]:
    """
    Get notable master games from the opening explorer for a position.
    Returns list of {id, white, black, year, winner} dicts.
    """
    stats = get_opening_stats(fen)
    if not stats:
        return []
    top = stats.get("topGames", [])[:count]
    results = []
    for g in top:
        results.append({
            "id": g.get("id", ""),
            "white": g.get("white", {}).get("name", "?"),
            "black": g.get("black", {}).get("name", "?"),
            "year": g.get("year", "?"),
            "winner": g.get("winner", "draw"),
        })
    return results


# Classic master games for study — hand-picked instructive examples
CLASSIC_GAMES = [
    {"id": "BgfJSs1X", "title": "Kasparov vs Topalov 1999", "theme": "King Hunt",
     "desc": "One of the greatest attacking games ever played. A spectacular king hunt."},
    {"id": "eZomfPCu", "title": "Morphy vs Duke & Count 1858", "theme": "Development",
     "desc": "The 'Opera Game' — a masterclass in rapid development and open lines."},
    {"id": "VIUbggJM", "title": "Fischer vs Byrne 1956", "theme": "Sacrifice",
     "desc": "The 'Game of the Century' — 13-year-old Fischer's queen sacrifice."},
    {"id": "aLe1Jrbm", "title": "Capablanca vs Marshall 1918", "theme": "Defense",
     "desc": "Capablanca coolly defends against the Marshall Attack."},
    {"id": "rEsqIk7L", "title": "Tal vs Botvinnik 1960", "theme": "Tactics",
     "desc": "Tal's magical combination in the World Championship."},
    {"id": "xeYJK3nX", "title": "Carlsen vs Anand 2013", "theme": "Endgame",
     "desc": "Carlsen grinds down Anand in a masterful endgame."},
    {"id": "Q7qv4OER", "title": "Karpov vs Kasparov 1985", "theme": "Strategy",
     "desc": "A pivotal game from the longest World Championship match."},
    {"id": "z2p6hsFN", "title": "Anderssen vs Kieseritzky 1851", "theme": "Sacrifice",
     "desc": "The 'Immortal Game' — sacrificing both rooks, a bishop, and a queen."},
]


# ── Formatting helpers for Claude prompts ───────────────────────────────────

def format_opening_context(stats: dict) -> str:
    """Format opening explorer stats into text for injection into Claude prompts."""
    if not stats:
        return ""

    total = stats.get("total", 0)
    if total == 0:
        return ""
    w = stats.get("white", 0)
    d = stats.get("draws", 0)
    b = stats.get("black", 0)

    lines = []
    opening = stats.get("opening")
    if opening and opening.get("name"):
        lines.append(f"Opening: {opening['name']} ({opening.get('eco', '')})")

    lines.append(
        f"Masters Database ({total:,} games): "
        f"White wins {round(100*w/total)}%, "
        f"Draws {round(100*d/total)}%, "
        f"Black wins {round(100*b/total)}%"
    )

    moves = stats.get("moves", [])[:5]
    if moves:
        lines.append("Top master continuations:")
        for m in moves:
            m_total = m.get("white", 0) + m.get("draws", 0) + m.get("black", 0)
            if m_total == 0:
                continue
            m_wr = round(100 * m["white"] / m_total)
            avg_r = m.get("averageRating", "?")
            lines.append(
                f"  {m['san']}: {m_total:,} games, "
                f"{m_wr}% White wins (avg rating {avg_r})"
            )

    top_games = stats.get("topGames", [])[:2]
    if top_games:
        lines.append("Example master games:")
        for g in top_games:
            w_name = g.get("white", {}).get("name", "?")
            b_name = g.get("black", {}).get("name", "?")
            yr = g.get("year", "?")
            lines.append(f"  {w_name} vs {b_name} ({yr})")

    return "\n".join(lines)


def format_tablebase_context(tb: dict, fen: str) -> str:
    """Format tablebase result into text for injection into Claude prompts."""
    if not tb:
        return ""

    try:
        board = chess.Board(fen)
    except Exception:
        return ""

    side = "White" if board.turn == chess.WHITE else "Black"
    category = tb.get("category", "unknown")

    result_map = {
        "win": f"{side} to move wins with perfect play",
        "cursed-win": f"{side} to move wins (but drawn under 50-move rule)",
        "draw": "Theoretical draw with perfect play",
        "blessed-loss": f"{side} to move loses (but can claim draw under 50-move rule)",
        "loss": f"{side} to move loses with perfect play",
    }

    verdict = result_map.get(category, f"Tablebase verdict: {category}")
    dtm = tb.get("dtm")
    if dtm is not None:
        verdict += f" — mate in {abs(dtm)}"

    lines = [f"Syzygy Tablebase (7-piece perfect play): {verdict}"]

    # Show the best moves according to the tablebase
    tb_moves = tb.get("moves", [])
    winning = [m for m in tb_moves if m.get("category") == "win"][:3]
    drawing = [m for m in tb_moves if m.get("category") == "draw"][:2]
    best_moves = winning or drawing

    if best_moves:
        lines.append("Tablebase best moves:")
        for m in best_moves:
            uci = m.get("uci", "")
            try:
                san = board.san(chess.Move.from_uci(uci))
            except Exception:
                san = uci
            m_dtm = m.get("dtm")
            m_cat = m.get("category", "")
            suffix = f" (mate in {abs(m_dtm)})" if m_dtm is not None else ""
            lines.append(f"  {san}: {m_cat}{suffix}")

    return "\n".join(lines)


def format_cloud_eval_context(cloud: dict) -> str:
    """Format cloud eval into text for Claude prompts."""
    if not cloud:
        return ""

    depth = cloud.get("depth", "?")
    pvs = cloud.get("pvs", [])
    if not pvs:
        return ""

    lines = [f"Lichess Cloud Eval (depth {depth}):"]
    for i, pv in enumerate(pvs[:3], 1):
        cp = pv.get("cp")
        mate = pv.get("mate")
        moves = pv.get("moves", "")
        if cp is not None:
            score = f"{cp/100:+.2f}"
        elif mate is not None:
            score = f"M{mate}" if mate > 0 else f"-M{abs(mate)}"
        else:
            score = "?"
        move_preview = " ".join(moves.split()[:4]) if moves else ""
        lines.append(f"  Line {i}: {score} — {move_preview}")

    return "\n".join(lines)
