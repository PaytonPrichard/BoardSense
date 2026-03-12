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
