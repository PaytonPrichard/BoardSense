"""
BoardSense - engine.py
All Stockfish logic: position analysis and full game analysis.

Accuracy model mirrors Chess.com with a depth-compensation adjustment:
  - Win probability: wp(cp) = 100 / (1 + exp(-0.00450 * cp))
    where cp is centipawns from the MOVING player's perspective.
    NOTE: _WP_K = 0.00450 (vs Chess.com's published 0.00368208).
    Chess.com analyses at depth ~22; we use depth 18.  At shallower depth
    Stockfish evaluations are compressed toward zero — the same mistake
    looks less severe.  Scaling k by (22/18) ≈ 1.22 compensates for this:
    the WP function treats the same centipawn difference as more impactful,
    approximating what a deeper engine would report.
  - Win probability loss (WPL) = wp_before - wp_after, clamped to [0, 100].
  - Move accuracy = 103.1668 * exp(-0.04354 * WPL) - 3.1668, clamped to [0, 100].
  - Game accuracy = average move accuracy.

Classification thresholds (WPL):
  blunder ≥ 20%  |  mistake ≥ 10%  |  inaccuracy ≥ 5%  |  good < 5%

Book move detection:
  Uses the Lichess masters opening explorer API. A move is "book" only if it
  appears in master-level games at that exact position. Once a position is
  absent from the database the game is considered out of theory and all
  subsequent moves receive eval-based classifications.
"""

import json
import math
import os
import platform
import shutil
import urllib.parse
import urllib.request
import chess
import chess.engine
import chess.pgn
import io
from stockfish import Stockfish


def _find_stockfish() -> str:
    """Locate the Stockfish binary across platforms.

    Search order:
      1. STOCKFISH_PATH environment variable (explicit override)
      2. System PATH (works if installed via apt, brew, choco, etc.)
      3. Common platform-specific locations
      4. Local Windows binary (dev fallback)
    """
    # 1. Explicit env var
    env_path = os.environ.get("STOCKFISH_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2. System PATH
    which = shutil.which("stockfish")
    if which:
        return which

    # 3. Platform-specific known locations
    _candidates = []
    system = platform.system()
    if system == "Linux":
        _candidates = ["/usr/games/stockfish", "/usr/bin/stockfish",
                       "/usr/local/bin/stockfish"]
    elif system == "Darwin":
        _candidates = ["/usr/local/bin/stockfish", "/opt/homebrew/bin/stockfish"]
    elif system == "Windows":
        _candidates = [
            os.path.join(os.path.dirname(__file__),
                         "stockfish-windows-x86-64-avx2", "stockfish",
                         "stockfish-windows-x86-64-avx2.exe"),
        ]

    for p in _candidates:
        if os.path.isfile(p):
            return p

    # 4. Last resort: local Windows binary (backward compat)
    fallback = os.path.join(
        os.path.dirname(__file__),
        "stockfish-windows-x86-64-avx2", "stockfish",
        "stockfish-windows-x86-64-avx2.exe",
    )
    if os.path.isfile(fallback):
        return fallback

    raise FileNotFoundError(
        "Stockfish not found. Install it (apt install stockfish / brew install "
        "stockfish) or set the STOCKFISH_PATH environment variable."
    )


STOCKFISH_PATH = _find_stockfish()

STOCKFISH_DEPTH   = 18   # Deeper than 15; single-threaded at 18 ≈ 300–800 ms per position
STOCKFISH_HASH    = 128  # MB; transposition reuse across the game's positions
STOCKFISH_THREADS = 1    # Threads=1 → fully deterministic (Lazy SMP is non-deterministic)

# Accuracy calibration constants
# _WP_K is scaled up from Chess.com's 0.00368208 by the ratio (22/18) ≈ 1.222
# to compensate for our shallower depth-18 analysis vs Chess.com's depth ~22.
# The remaining three constants are unchanged from Chess.com's published formula.
_WP_K        = 0.00450      # sigmoid steepness (depth-adjusted)
_ACC_A       = 103.1668     # accuracy curve amplitude
_ACC_B       = 0.04354      # accuracy curve decay rate
_ACC_OFFSET  = 3.1668       # accuracy curve baseline shift

# Lichess opening explorer endpoint (masters database)
_LICHESS_EXPLORER = "https://explorer.lichess.ovh/masters"



# ── Win probability helpers ──────────────────────────────────────────────────

def _win_prob(cp: float) -> float:
    """
    Win probability for the player with `cp` centipawn advantage.
    cp > 0 → player is ahead.  Returns a value in [0, 100].
    """
    return 100.0 / (1.0 + math.exp(-_WP_K * cp))


def _wp_loss(eval_before: float, eval_after: float, color: str) -> float:
    """
    Win probability lost by the moving side (percentage points, 0–100).
    eval_before / eval_after are in PAWNS from White's perspective.
    Positive return = bad move for the player who moved.
    """
    sign = 1.0 if color == "white" else -1.0
    wp_before = _win_prob(eval_before * 100.0 * sign)
    wp_after  = _win_prob(eval_after  * 100.0 * sign)
    return max(0.0, wp_before - wp_after)


def _accuracy_from_loss(loss_pct: float) -> float:
    """Chess.com accuracy formula: WP loss → move accuracy [0, 100]."""
    return max(0.0, min(100.0, _ACC_A * math.exp(-_ACC_B * loss_pct) - _ACC_OFFSET))


# ── Opening book (Lichess masters database) ──────────────────────────────────

def _lichess_book_ucis(fen: str) -> set[str]:
    """
    Query the Lichess masters opening explorer for a position.
    Returns the set of UCI moves that appear at master level, or an empty set
    if the position is unknown or the request fails.
    """
    try:
        params = urllib.parse.urlencode({
            "fen":         fen,
            "moves":       30,
            "topGames":    0,
            "recentGames": 0,
        })
        req = urllib.request.Request(
            f"{_LICHESS_EXPLORER}?{params}",
            headers={"User-Agent": "BoardSenseApp/1.0 (github.com/boardsense)"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return {m["uci"] for m in data.get("moves", [])}
    except Exception:
        return set()


def _build_book_flags(fens: list[str], move_ucis: list[str]) -> list[bool]:
    """
    For each move in the game, determine whether it is a recognized mainline
    move in the Lichess masters database.

    Stops querying as soon as a position is not found in the database — once
    a game leaves opening theory it does not return to it via the main line.
    """
    flags = []
    for fen, uci in zip(fens, move_ucis):
        book_ucis = _lichess_book_ucis(fen)
        if not book_ucis:
            # Position unknown to the database — game is out of theory
            flags.extend([False] * (len(move_ucis) - len(flags)))
            break
        flags.append(uci in book_ucis)
    return flags


# ── Brilliancy detection helper ──────────────────────────────────────────────

def _eval_for_mover(t: dict) -> float:
    """
    Convert a single get_top_moves entry to a float score from the side-to-move's
    perspective (positive = good for mover), capped at ±10.
    The stockfish package already returns Centipawn from side-to-move perspective.
    """
    cp   = t.get("Centipawn")
    mate = t.get("Mate")
    if cp is not None:
        return max(-10.0, min(10.0, cp / 100.0))
    if mate is not None:
        return 10.0 if mate > 0 else -10.0
    return 0.0


# ── Public engine helpers ────────────────────────────────────────────────────

def get_engine(depth: int = STOCKFISH_DEPTH) -> Stockfish:
    """Start Stockfish and return the engine object."""
    if not os.path.exists(STOCKFISH_PATH):
        raise FileNotFoundError(f"Stockfish not found at: {STOCKFISH_PATH}")
    return Stockfish(
        path=STOCKFISH_PATH,
        depth=depth,
        parameters={
            "Hash":    STOCKFISH_HASH,
            "Threads": STOCKFISH_THREADS,
        },
    )


def format_evaluation(evaluation: dict) -> str:
    """Turn Stockfish's raw evaluation dict into a readable string."""
    if evaluation["type"] == "cp":
        score = evaluation["value"] / 100
        return f"+{score:.1f}" if score > 0 else ("0.0" if score == 0 else f"{score:.1f}")
    elif evaluation["type"] == "mate":
        moves = evaluation["value"]
        return f"M{moves}" if moves > 0 else f"-M{abs(moves)}"
    return "?"


def analyze_position(fen: str) -> dict:
    """Given a FEN string, return Stockfish's top moves and evaluation."""
    engine = get_engine()
    engine.set_fen_position(fen)
    return {
        "fen": fen,
        "top_moves": engine.get_top_moves(3),
        "evaluation": engine.get_evaluation(),
    }


def classify_move(
    eval_before: float,
    eval_after: float,
    color: str,
) -> tuple[str, float, float]:
    """
    Classify a move using Chess.com-style win probability loss.

    Args:
        eval_before: Stockfish eval BEFORE the move (pawns, White's perspective).
        eval_after:  Stockfish eval AFTER the move  (pawns, White's perspective).
        color:       "white" or "black" (the side that moved).

    Returns:
        (classification, wp_loss_pct, move_accuracy_pct)
    """
    loss = _wp_loss(eval_before, eval_after, color)
    accuracy = _accuracy_from_loss(loss)

    # Centipawn loss from the moving player's perspective.
    # Used as a second gate alongside WPL: Stockfish depth-15 evals can
    # fluctuate 30–80 cp between adjacent positions (horizon effects), which
    # can push WPL just over the mistake/blunder thresholds for moves that
    # are not real errors.  Requiring a minimum centipawn drop prevents these
    # noise-induced false positives while leaving inaccuracy uncapped (small
    # moves still matter there and the stakes of a wrong label are lower).
    sign = 1.0 if color == "white" else -1.0
    cp_loss = max(0.0, (eval_before - eval_after) * 100.0 * sign)

    if loss >= 20.0 and cp_loss >= 100.0:   # blunder:  WPL ≥ 20% + ≥ 1 pawn
        cls = "blunder"
    elif loss >= 10.0 and cp_loss >= 50.0:  # mistake:  WPL ≥ 10% + ≥ ½ pawn
        cls = "mistake"
    elif loss >= 5.0:                        # inaccuracy: pure WPL (no floor)
        cls = "inaccuracy"
    else:
        cls = "good"

    return cls, loss, accuracy


# ── Game analysis ────────────────────────────────────────────────────────────

def analyze_game_iter(pgn_text: str, depth: int = STOCKFISH_DEPTH):
    """
    Generator that analyzes a game position by position.

    After evaluating each position it yields:
        ('progress', fen, last_move_uci_or_None, positions_done, total_positions, eval_float)

    When all positions are evaluated it yields once more:
        ('done', moves_list, headers_dict)

    Design:
    - Book moves are identified via the Lichess masters database before
      Stockfish runs. A move is "book" only if it genuinely appears in
      master-level theory; otherwise it receives eval-based classification.
    - Each FEN is evaluated EXACTLY ONCE via the stockfish package's
      get_top_moves(1). Centipawn is side-to-move perspective; we flip the
      sign for Black so the stored eval is always from White's perspective.
      STOCKFISH_THREADS=1 makes the search fully deterministic at fixed depth.
    - eval_after of move i == eval_before of move i+1 (same Stockfish result),
      so adjacent positions are always consistent.
    - Accuracy uses the Chess.com win-probability model.

    Note: chess.engine.SimpleEngine was tried but its internal asyncio event
    loop conflicts with Streamlit's Tornado runtime, causing EngineError.
    The stockfish package avoids this — it communicates with the Stockfish
    process directly via subprocess pipes with no asyncio involvement.
    """
    engine = get_engine(depth=depth)

    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("Could not parse PGN.")

    headers   = dict(game.headers)
    board     = game.board()
    main_line = list(game.mainline_moves())

    # --- Pass 1: collect FENs and move metadata ---
    fens        = [board.fen()]
    move_sans   = []
    move_ucis   = []
    move_colors = []

    for move in main_line:
        move_colors.append("white" if board.turn == chess.WHITE else "black")
        move_sans.append(board.san(move))
        move_ucis.append(move.uci())
        board.push(move)
        fens.append(board.fen())

    total = len(fens)

    # --- Pass 1b: determine book moves via Lichess masters database ---
    # fens[:-1] are the positions before each move; move_ucis are the moves played.
    book_flags = _build_book_flags(fens[:-1], move_ucis)

    # --- Pass 2: evaluate each position once, yield progress ---
    evals      = []
    best_ucis  = []
    eval_gaps  = []   # gap (pawns, mover's perspective) between 1st and 2nd best move
    second_evs = []   # 2nd best eval from mover's perspective (lower = 2nd best is bad)
    tops_list  = []   # raw get_top_moves(3) output per position, used in Pass 3

    for i, fen in enumerate(fens):
        board_tmp = chess.Board(fen)

        # Terminal positions (checkmate / stalemate) have no legal moves, so
        # get_top_moves(2) returns [] and gives no eval. Handle them directly.
        if board_tmp.is_game_over():
            if board_tmp.is_checkmate():
                # Side to move is mated. From White's perspective:
                #   Black to move + mated → White won   → +10
                #   White to move + mated → Black won   → -10
                ev_float = 10.0 if board_tmp.turn == chess.BLACK else -10.0
            else:
                ev_float = 0.0   # stalemate / draw
            best_ucis.append(None)
            tops_list.append([])
            evals.append(ev_float)
            eval_gaps.append(0.0)
            second_evs.append(0.0)
            last_uci = move_ucis[i - 1] if i > 0 else None
            yield ("progress", fen, last_uci, i + 1, total, evals[-1], best_ucis[-1])
            continue

        engine.set_fen_position(fen)

        # get_top_moves(3) returns the top 3 moves in a single MultiPV search.
        # Centipawn is from the SIDE-TO-MOVE's perspective; flip sign for Black
        # to keep a consistent White's-perspective eval stored in evals[].
        # The gap between 1st and 2nd (mover's perspective) is stored separately
        # in eval_gaps[] and used later for brilliant detection.
        top = engine.get_top_moves(3)
        best_ucis.append(top[0]["Move"] if top else None)

        if top:
            t        = top[0]
            is_black = fen.split()[1] == "b"
            cp       = t.get("Centipawn")
            mate     = t.get("Mate")

            if cp is not None:
                ev_float = max(-10.0, min(10.0, (-cp if is_black else cp) / 100.0))
            elif mate is not None:
                # Positive mate → side to move can force checkmate; flip for Black.
                ev_float = 10.0 if mate > 0 else -10.0
                if is_black:
                    ev_float = -ev_float
            else:
                ev_float = 0.0

            # Eval gap: how much better is move 1 vs move 2, from mover's view?
            if len(top) >= 2:
                gap       = _eval_for_mover(top[0]) - _eval_for_mover(top[1])
                second_ev = _eval_for_mover(top[1])
            else:
                gap       = 0.0
                second_ev = _eval_for_mover(top[0])
        else:
            ev_float  = 0.0
            gap       = 0.0
            second_ev = 0.0

        evals.append(ev_float)
        eval_gaps.append(gap)
        second_evs.append(second_ev)
        tops_list.append(top)
        last_uci = move_ucis[i - 1] if i > 0 else None
        yield ("progress", fen, last_uci, i + 1, total, evals[-1], best_ucis[-1])

    # --- Pass 3: build move records ---
    result_moves = []

    for i in range(len(main_line)):
        eval_before = evals[i]
        eval_after  = evals[i + 1]
        color       = move_colors[i]

        if book_flags[i]:
            # Compute real WPL-based accuracy for book moves just like any
            # other move — Chess.com evaluates every move with the engine.
            # The "book" label is informational only; it does not auto-grant
            # 100% the way a zero-loss move earns it through the formula.
            _, wpl, move_acc = classify_move(eval_before, eval_after, color)
            classification = "book"
        else:
            classification, wpl, move_acc = classify_move(
                eval_before, eval_after, color
            )

        best_move_uci = best_ucis[i]
        best_move_san = None
        if best_move_uci:
            try:
                pre_board     = chess.Board(fens[i])
                best_move_san = pre_board.san(chess.Move.from_uci(best_move_uci))
            except Exception:
                best_move_san = None

        # Upgrade "good" → "best" only when the player matched Stockfish's top choice
        if best_move_uci and move_ucis[i] == best_move_uci and classification == "good":
            classification = "best"

        # Upgrade "best" → "brilliant" — reserved for genuinely spectacular finds only.
        # All five conditions must hold:
        #   1. Gap ≥ 3.5 pawns between best and 2nd-best (mover's perspective) — one dominant move
        #   2. 2nd-best ≤ −1.5 for the mover — alternatives clearly lose, not just slightly worse
        #   3. The move itself achieves a real advantage (eval_after ≥ +1.0 for the mover)
        #   4. Position was not already clearly decided (≤ ±3.0) — rules out trivial "forced" continuations
        #   5. Previous move was not a blunder or mistake — the "obvious best response after a blunder"
        #      scenario (huge eval gap, bad 2nd option) must not be rewarded as brilliant
        sign = 1.0 if color == "white" else -1.0
        eval_after_for_mover = eval_after * sign
        prev_cls = result_moves[-1]["classification"] if result_moves else "good"
        if (classification == "best"
                and eval_gaps[i]         >= 3.5
                and second_evs[i]        <= -1.5
                and eval_after_for_mover >= 1.0
                and abs(eval_before)     <= 3.0
                and prev_cls not in ("blunder", "mistake")):
            classification = "brilliant"

        # Build top_candidates: top 3 engine moves at this position with SAN + mover eval.
        # Excludes the played move itself (already shown as move_san / eval_after).
        top_candidates = []
        pre_board_cands = chess.Board(fens[i])
        for t_entry in tops_list[i]:
            try:
                cand_uci = t_entry["Move"]
                cand_san = pre_board_cands.san(chess.Move.from_uci(cand_uci))
                cand_ev  = round(_eval_for_mover(t_entry), 2)
                top_candidates.append({"san": cand_san, "eval": cand_ev})
            except Exception:
                pass

        result_moves.append({
            "move_number":  (i // 2) + 1,
            "color":        color,
            "move_san":     move_sans[i],
            "move_uci":     move_ucis[i],
            "fen_before":   fens[i],
            "fen_after":    fens[i + 1],
            "eval_before":  eval_before,
            "eval_after":   eval_after,
            "eval_delta":   eval_after - eval_before,
            "classification": classification,
            "wp_loss":      wpl,
            "move_accuracy": move_acc,
            "best_move_san": best_move_san,
            "best_move_uci": best_move_uci,
            "top_candidates": top_candidates,
        })

    yield ("done", result_moves, headers)


def analyze_game(pgn_text: str) -> tuple[list[dict], dict]:
    """Non-streaming wrapper around analyze_game_iter."""
    for update in analyze_game_iter(pgn_text):
        if update[0] == "done":
            return update[1], update[2]
    raise RuntimeError("analyze_game_iter ended without 'done' update")


def get_followup_lines(fen: str, n_plies: int = 4) -> dict:
    """
    Play n_plies of engine-best moves from `fen`, returning the principal variation.

    Used by the tutor to give Claude a concrete look-ahead after any move.

    Returns:
        {"moves": [san1, san2, ...], "evals": [ev1, ev2, ...]}
    """
    engine = get_engine()
    board  = chess.Board(fen)
    moves_san: list[str]   = []
    evals:     list[float] = []

    for _ in range(n_plies):
        if board.is_game_over():
            break

        engine.set_fen_position(board.fen())
        top = engine.get_top_moves(1)
        if not top:
            break

        t        = top[0]
        uci      = t["Move"]
        move_obj = chess.Move.from_uci(uci)
        san      = board.san(move_obj)
        moves_san.append(san)

        is_black = board.turn == chess.BLACK
        cp       = t.get("Centipawn")
        mate     = t.get("Mate")

        if cp is not None:
            ev = max(-10.0, min(10.0, (-cp if is_black else cp) / 100.0))
        elif mate is not None:
            ev = 10.0 if mate > 0 else -10.0
            if is_black:
                ev = -ev
        else:
            ev = 0.0

        evals.append(ev)
        board.push(move_obj)

    return {"moves": moves_san, "evals": evals}
