"""
BoardSense — profile.py
Bulk game analysis + player profile generation.

Key differences from single-game analysis (engine.py):
  - Depth 12 instead of 18 (≈4× faster per position)
  - No Lichess book-detection API calls (saves ~30 HTTP requests per game)
  - No per-move Claude explanations (only one final synthesis call)
  - Returns compact per-game summaries, then one Claude profile at the end
"""

import io
import json
import math
import statistics

from anthropic import Anthropic
import chess
import chess.pgn
from stockfish import Stockfish

from engine import STOCKFISH_PATH, STOCKFISH_HASH, _wp_loss, _accuracy_from_loss

# ── Constants ─────────────────────────────────────────────────────────────────

BULK_DEPTH = 12   # Fast enough for bulk; still catches 95%+ of blunders correctly

PIECE_TIERS: dict[int, dict] = {
    1: {"piece": "♙", "name": "Pawn",   "tier": "Beginner",     "color": "#78909c"},
    2: {"piece": "♘", "name": "Knight", "tier": "Developing",   "color": "#81c784"},
    3: {"piece": "♗", "name": "Bishop", "tier": "Intermediate", "color": "#4fc3f7"},
    4: {"piece": "♖", "name": "Rook",   "tier": "Advanced",     "color": "#ffb74d"},
    5: {"piece": "♕", "name": "Queen",  "tier": "Expert",       "color": "#b39ddb"},
}

SKILL_CATEGORIES = [
    "Tactics",
    "Opening Prep",
    "Middlegame",
    "Endgame",
    "Piece Activity",
    "Consistency",
]

# Concept names Claude can choose for priority_focus — must match coaching library
_CONCEPT_HINTS = (
    "Fork, Pin, Skewer, Discovered Attack, Deflection, Overloading, Zwischenzug, "
    "Back Rank Weakness, Trapped Piece, Isolated Pawn, Passed Pawn, Minority Attack, "
    "Outpost, Bad Bishop, Rook On Open File, Rook On Seventh Rank, "
    "Two Weaknesses, Prophylaxis, King Safety, Initiative, "
    "Opposition, Zugzwang, Lucena Position, Philidor Position"
)


# ── Time control helpers ──────────────────────────────────────────────────────

def _time_control_category(tc_str: str) -> str:
    """
    Parse a PGN TimeControl field (e.g. "600", "180+2", "60+0") and return
    one of: Bullet / Blitz / Rapid / Classical / Unknown.
    """
    try:
        # Strip increment and period notation; take base seconds
        base = int(tc_str.split("+")[0].split("/")[-1])
    except (ValueError, AttributeError, IndexError):
        return "Unknown"
    if base < 180:    return "Bullet"
    if base < 600:    return "Blitz"
    if base < 1800:   return "Rapid"
    return "Classical"


# ── Phase detection (material-based) ──────────────────────────────────────────

# Piece values for material counting (pawns excluded from non-pawn material)
_PIECE_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _detect_phase(board: chess.Board, half_move: int) -> str:
    """
    Determine game phase from actual board material, not move number.

    Opening:     half_move < 20 AND both sides still have major+minor pieces
                 (i.e. no significant trades yet — total non-pawn material >= 50)
    Endgame:     no queens on the board, OR each side's non-pawn material <= 13
                 (roughly rook + minor piece or less per side)
    Middlegame:  everything else
    """
    piece_map = board.piece_map()

    w_material = 0  # non-pawn material for White
    b_material = 0
    has_w_queen = False
    has_b_queen = False

    for piece in piece_map.values():
        val = _PIECE_VALUES.get(piece.piece_type, 0)
        if piece.color == chess.WHITE:
            w_material += val
            if piece.piece_type == chess.QUEEN:
                has_w_queen = True
        else:
            b_material += val
            if piece.piece_type == chess.QUEEN:
                has_b_queen = True

    total_material = w_material + b_material

    # Endgame: no queens at all, OR both sides have very low material
    if (not has_w_queen and not has_b_queen) or (w_material <= 13 and b_material <= 13 and total_material <= 20):
        return "endgame"

    # Opening: early moves AND most material still on board (starting = 62 points)
    if half_move < 20 and total_material >= 50:
        return "opening"

    return "middlegame"


# ── Engine setup ──────────────────────────────────────────────────────────────

def _get_bulk_engine(depth: int = BULK_DEPTH) -> Stockfish:
    """Stockfish at reduced depth for bulk analysis."""
    return Stockfish(
        path=STOCKFISH_PATH,
        depth=depth,
        parameters={"Hash": STOCKFISH_HASH, "Threads": 1},
    )


def _eval_from_top(top: list[dict], is_black: bool) -> float:
    """Convert get_top_moves(1) result to White's-perspective pawn eval."""
    if not top:
        return 0.0
    t = top[0]
    cp   = t.get("Centipawn")
    mate = t.get("Mate")
    if cp is not None:
        ev = max(-10.0, min(10.0, cp / 100.0))
        return -ev if is_black else ev
    if mate is not None:
        ev = 10.0 if mate > 0 else -10.0
        return -ev if is_black else ev
    return 0.0


# ── Single-game bulk analysis ─────────────────────────────────────────────────

def _analyze_single_game(
    pgn_text: str,
    engine: Stockfish,
    username: str,
) -> dict | None:
    """
    Analyse one game at bulk depth (no book detection, no Claude).
    Returns a compact summary dict, or None if the PGN cannot be parsed.
    """
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None or game.next() is None:
            return None
        headers   = dict(game.headers)
        main_line = list(game.mainline_moves())
    except Exception:
        return None

    white_name = headers.get("White", "").lower()
    black_name = headers.get("Black", "").lower()
    user_lower = username.lower()
    player_color = "white" if user_lower == white_name else "black"

    opening_name = (
        headers.get("Opening", "")
        or headers.get("ECOUrl", "").split("/")[-1].replace("-", " ").title()
        or headers.get("ECO", "")
    )

    # ── Collect positions + clock data ──────────────────────────────────────
    import re as _re
    _CLK_RE = _re.compile(r'\[%clk\s+(\d+):(\d+):(\d+)(?:\.(\d+))?')

    board = game.board()
    fens        = [board.fen()]
    move_colors = []
    move_sans   = []
    clock_secs: list[float | None] = []  # remaining clock in seconds per move

    node = game
    for move in main_line:
        node = node.next()
        move_colors.append("white" if board.turn == chess.WHITE else "black")
        move_sans.append(board.san(move))
        # Parse clock comment
        comment = node.comment if node else ""
        clk_match = _CLK_RE.search(comment) if comment else None
        if clk_match:
            h, m, s = int(clk_match.group(1)), int(clk_match.group(2)), int(clk_match.group(3))
            frac_str = clk_match.group(4)
            frac = int(frac_str) / (10 ** len(frac_str)) if frac_str else 0.0
            clock_secs.append(h * 3600 + m * 60 + s + frac)
        else:
            clock_secs.append(None)
        board.push(move)
        fens.append(board.fen())

    # ── Evaluate every position once ──────────────────────────────────────────
    evals:     list[float]       = []
    best_ucis: list[str | None]  = []   # best move UCI per position (for critical_moves)
    for fen in fens:
        board_tmp = chess.Board(fen)
        if board_tmp.is_game_over():
            if board_tmp.is_checkmate():
                ev = 10.0 if board_tmp.turn == chess.BLACK else -10.0
            else:
                ev = 0.0
            evals.append(ev)
            best_ucis.append(None)
            continue
        engine.set_fen_position(fen)
        top = engine.get_top_moves(1)
        is_black = fen.split()[1] == "b"
        evals.append(_eval_from_top(top, is_black))
        best_ucis.append(top[0]["Move"] if top else None)

    # ── Compute per-move stats for the tracked player ─────────────────────────
    opening_accs:    list[float] = []
    middlegame_accs: list[float] = []
    endgame_accs:    list[float] = []
    blunders = mistakes = inaccuracies = 0
    critical_moves: list[dict] = []

    for i in range(len(main_line)):
        color = move_colors[i]
        loss  = _wp_loss(evals[i], evals[i + 1], color)
        acc   = _accuracy_from_loss(loss)
        sign  = 1.0 if color == "white" else -1.0
        cp_loss = max(0.0, (evals[i] - evals[i + 1]) * 100.0 * sign)

        if loss >= 20.0 and cp_loss >= 100.0:
            cls = "blunder"
        elif loss >= 10.0 and cp_loss >= 50.0:
            cls = "mistake"
        elif loss >= 5.0:
            cls = "inaccuracy"
        else:
            cls = "good"

        if color == player_color:
            move_num = (i // 2) + 1
            phase = _detect_phase(chess.Board(fens[i]), i)
            if phase == "opening":
                opening_accs.append(acc)
            elif phase == "endgame":
                endgame_accs.append(acc)
            else:
                middlegame_accs.append(acc)

            if cls == "blunder":
                blunders += 1
            elif cls == "mistake":
                mistakes += 1
            elif cls == "inaccuracy":
                inaccuracies += 1

            if cls in ("blunder", "mistake"):
                san = move_sans[i]
                best_move_san = None
                best_uci = best_ucis[i] if i < len(best_ucis) else None
                if best_uci:
                    try:
                        candidate = chess.Board(fens[i]).san(chess.Move.from_uci(best_uci))
                        if candidate != san:
                            best_move_san = candidate
                    except Exception:
                        pass
                critical_moves.append({
                    "move_number":    move_num,
                    "color":          color,
                    "move_san":       san,
                    "best_move_san":  best_move_san,
                    "fen_before":     fens[i],
                    "eval_before":    round(evals[i], 2),
                    "eval_after":     round(evals[i + 1], 2),
                    "classification": cls,
                    "phase":          phase,
                    "is_piece_move":  san[0].isupper() and san[0] != "O",
                })

    def _avg(lst):
        clean = [x for x in lst if x is not None]
        return round(sum(clean) / len(clean), 1) if clean else None

    all_player_accs = opening_accs + middlegame_accs + endgame_accs

    # ── Clock data: compute move times from remaining clock ──────────────────
    move_times: list[dict] = []
    has_clock = any(c is not None for c in clock_secs)
    # Parse increment from TimeControl header (e.g. "600+5" → 5)
    _tc_raw = headers.get("TimeControl", "")
    _tc_inc = 0.0
    try:
        if "+" in _tc_raw:
            _tc_inc = float(_tc_raw.split("+")[1])
    except (ValueError, IndexError):
        pass
    if has_clock:
        prev_clock: dict[str, float | None] = {"white": None, "black": None}
        for i in range(len(main_line)):
            color = move_colors[i]
            clk = clock_secs[i]
            time_spent = None
            if clk is not None and prev_clock[color] is not None:
                # Account for increment: time_spent = prev_clock + increment - current_clock
                time_spent = max(0.0, prev_clock[color] + _tc_inc - clk)
            prev_clock[color] = clk
            if color == player_color:
                move_times.append({
                    "move_number": (i // 2) + 1,
                    "clock_seconds": clk,
                    "time_spent": time_spent,
                    "classification": "good",  # placeholder, overridden below
                })
        # Backfill classifications for player moves
        _cls_idx = 0
        for i in range(len(main_line)):
            if move_colors[i] == player_color:
                loss  = _wp_loss(evals[i], evals[i + 1], move_colors[i])
                sign  = 1.0 if move_colors[i] == "white" else -1.0
                cp_loss = max(0.0, (evals[i] - evals[i + 1]) * 100.0 * sign)
                if loss >= 20.0 and cp_loss >= 100.0:
                    c = "blunder"
                elif loss >= 10.0 and cp_loss >= 50.0:
                    c = "mistake"
                elif loss >= 5.0:
                    c = "inaccuracy"
                else:
                    c = "good"
                if _cls_idx < len(move_times):
                    move_times[_cls_idx]["classification"] = c
                _cls_idx += 1

    _player_times = [mt["time_spent"] for mt in move_times if mt.get("time_spent") is not None]
    avg_move_time = round(sum(_player_times) / len(_player_times), 1) if _player_times else None
    # Time trouble threshold: 10% of initial time or 60s, whichever is larger
    _tc_base = 60.0
    try:
        _tc_base_raw = int(_tc_raw.split("+")[0].split("/")[-1]) if _tc_raw else 0
        _tt_threshold = max(60.0, _tc_base_raw * 0.10)
    except (ValueError, IndexError):
        _tt_threshold = 60.0
    time_trouble_moves = sum(
        1 for mt in move_times
        if mt.get("clock_seconds") is not None and mt["clock_seconds"] < _tt_threshold
        and mt.get("time_spent") is not None and mt["time_spent"] > 0
    ) if has_clock else 0

    return {
        "white":        headers.get("White", "?"),
        "black":        headers.get("Black", "?"),
        "result":       headers.get("Result", "*"),
        "date":         headers.get("Date", ""),
        "opening":      opening_name,
        "n_moves":      len(main_line),
        "player_color": player_color,
        "player_accuracy":     _avg(all_player_accs) or 50.0,
        "opening_accuracy":    _avg(opening_accs),
        "middlegame_accuracy": _avg(middlegame_accs),
        "endgame_accuracy":    _avg(endgame_accs),
        "blunders":       blunders,
        "mistakes":       mistakes,
        "inaccuracies":   inaccuracies,
        "critical_moves": critical_moves,
        "time_control":   _time_control_category(headers.get("TimeControl", "")),
        "_pgn":           pgn_text,
        "has_clock":      has_clock,
        "avg_move_time":  avg_move_time,
        "time_trouble_moves": time_trouble_moves,
        "move_times":     move_times if has_clock else [],
    }


# ── Bulk generator ────────────────────────────────────────────────────────────

def bulk_analyze_games(
    games: list[dict],
    username: str,
    depth: int = BULK_DEPTH,
):
    """
    Generator: analyse a list of {pgn, headers} dicts at bulk depth.

    Yields:
        ("progress", games_done, total_games, summary_or_None)

    Final yield:
        ("done", list_of_summaries)
    """
    engine    = _get_bulk_engine(depth)
    summaries = []

    for i, game_data in enumerate(games):
        summary = _analyze_single_game(game_data["pgn"], engine, username)
        if summary:
            summaries.append(summary)
        yield ("progress", i + 1, len(games), summary)

    yield ("done", summaries)


# ── Profile synthesis ─────────────────────────────────────────────────────────

def build_player_profile(summaries: list[dict], username: str) -> dict:
    """
    Aggregate cross-game stats and ask Claude for a personalised profile.

    Returns a dict with keys:
        username, n_games, date_range, overall_acc, record,
        summary, strengths, skill_ratings, priority_focus, coach_message
    """
    if not summaries:
        return {}

    n = len(summaries)

    def _avg(lst):
        clean = [x for x in lst if x is not None]
        return round(sum(clean) / len(clean), 1) if clean else 0.0

    overall_acc    = _avg([s["player_accuracy"]     for s in summaries])
    opening_acc    = _avg([s["opening_accuracy"]    for s in summaries])
    middlegame_acc = _avg([s["middlegame_accuracy"] for s in summaries])
    endgame_acc    = _avg([s["endgame_accuracy"]    for s in summaries])

    blunders_per_game     = _avg([s["blunders"]     for s in summaries])
    mistakes_per_game     = _avg([s["mistakes"]     for s in summaries])
    inaccuracies_per_game = _avg([s["inaccuracies"] for s in summaries])

    accs = [s["player_accuracy"] for s in summaries]
    consistency_sd = round(statistics.stdev(accs), 1) if len(accs) >= 2 else 0.0

    wins   = sum(
        1 for s in summaries
        if (s["result"] == "1-0" and s["player_color"] == "white")
        or (s["result"] == "0-1" and s["player_color"] == "black")
    )
    losses = sum(
        1 for s in summaries
        if (s["result"] == "0-1" and s["player_color"] == "white")
        or (s["result"] == "1-0" and s["player_color"] == "black")
    )
    draws = n - wins - losses

    half            = max(1, n // 2)
    first_half_acc  = _avg([s["player_accuracy"] for s in summaries[:half]])
    second_half_acc = _avg([s["player_accuracy"] for s in summaries[half:]])
    if second_half_acc > first_half_acc + 1.5:
        trend = "improving"
    elif second_half_acc < first_half_acc - 1.5:
        trend = "declining"
    else:
        trend = "stable"

    phase_map   = {"Opening": opening_acc, "Middlegame": middlegame_acc, "Endgame": endgame_acc}
    worst_phase = min(phase_map, key=phase_map.get)
    best_phase  = max(phase_map, key=phase_map.get)

    dates      = sorted(s["date"] for s in summaries if s.get("date") and "?" not in s["date"])
    date_range = f"{dates[0][:7]} – {dates[-1][:7]}" if len(dates) >= 2 else (dates[0][:7] if dates else "")

    prompt = f"""You are a chess coach reviewing {n} recent games for player "{username}".

AGGREGATE STATS ({date_range}):
- Overall accuracy: {overall_acc}%
- Opening (moves 1–12): {opening_acc}%
- Middlegame (13–30): {middlegame_acc}%
- Endgame (31+): {endgame_acc}%
- Blunders per game: {blunders_per_game:.1f}
- Mistakes per game: {mistakes_per_game:.1f}
- Inaccuracies per game: {inaccuracies_per_game:.1f}
- Accuracy std dev (consistency): {consistency_sd}%  ← lower = more consistent
- Trend: {trend}
- Weakest phase: {worst_phase} | Strongest phase: {best_phase}
- Record: {wins}W {losses}L {draws}D from {n} games

Rate the player on exactly these 6 categories using integers 1–5:
  1 = Pawn (Beginner) | 2 = Knight (Developing) | 3 = Bishop (Intermediate)
  4 = Rook (Advanced) | 5 = Queen (Expert)

For "priority_focus": choose exactly 3 concepts to study from this list (use exact names):
{_CONCEPT_HINTS}

Respond ONLY with valid JSON (no markdown, no code fences):
{{
  "summary": "2–3 sentences on this player's style and main pattern",
  "strengths": ["strength 1", "strength 2"],
  "skill_ratings": {{
    "Tactics":        {{"rating": 1, "description": "one sentence"}},
    "Opening Prep":   {{"rating": 1, "description": "one sentence"}},
    "Middlegame":     {{"rating": 1, "description": "one sentence"}},
    "Endgame":        {{"rating": 1, "description": "one sentence"}},
    "Piece Activity": {{"rating": 1, "description": "one sentence"}},
    "Consistency":    {{"rating": 1, "description": "one sentence"}}
  }},
  "priority_focus": ["Concept1", "Concept2", "Concept3"],
  "coach_message": "One encouraging, actionable sentence"
}}"""

    client  = Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw   = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "summary":      raw[:400],
            "strengths":    [],
            "skill_ratings": {},
            "priority_focus": [],
            "coach_message": "",
        }

    # Attach computed metadata
    result["username"]          = username
    result["n_games"]           = n
    result["date_range"]        = date_range
    result["overall_acc"]       = overall_acc
    result["blunders_per_game"] = round(blunders_per_game, 1)
    result["mistakes_per_game"] = round(mistakes_per_game, 1)
    result["record"]            = {"wins": wins, "losses": losses, "draws": draws}

    return result
