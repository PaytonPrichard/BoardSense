"""
BoardSense - tutor.py
All Claude AI logic: move explanation and full game review.
"""

import json
import logging
import re
import chess
from anthropic import Anthropic, APIError, APIConnectionError, APITimeoutError
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

load_dotenv()

# Regex matching SAN-like tokens: piece moves (Nxd5+), pawn moves (e4, exd5),
# castling (O-O, O-O-O), promotions (e8=Q). Word-bounded to avoid English words.
_SAN_RE = re.compile(
    r'\b([KQRBN][a-h]?[1-8]?x?[a-h][1-8][+#]?'   # piece moves
    r'|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?'          # pawn captures (exd5, exd8=Q)
    r'|[a-h][1-8](?:=[QRBN])?[+#]?'                 # pawn pushes (e4, e8=Q)
    r'|O-O-O[+#]?|O-O[+#]?)\b'                      # castling
)


def _validate_move_refs(text: str, fen: str) -> str:
    """Strip illegal SAN tokens from text. Leaves legal ones untouched."""
    try:
        board = chess.Board(fen)
    except Exception:
        return text

    def _check(m: re.Match) -> str:
        token = m.group(1)
        try:
            board.parse_san(token)
            return token
        except Exception:
            return ""

    return _SAN_RE.sub(_check, text)


def _parse_explain_response(raw: str) -> dict:
    """
    Parse Claude's JSON response into insights and concept tags.
    Expected format: {"insights": [{"label": str, "text": str}, ...], "concepts": [...]}
    Falls back gracefully if JSON is malformed.
    Returns {"insights": list[dict], "concepts": list[str]}.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        parsed = json.loads(text)
        insights = parsed.get("insights", [])
        concepts = [c.strip().title() for c in parsed.get("concepts", []) if c.strip()][:4]
        return {"insights": insights, "concepts": concepts}
    except (json.JSONDecodeError, AttributeError):
        # Fallback: wrap the raw text as a single unlabeled insight
        return {"insights": [{"label": "Analysis", "text": text[:600]}], "concepts": []}


_LABEL_MAP = {
    "best":       "the engine's top choice — an excellent move",
    "good":       "a good move",
    "inaccuracy": "an inaccuracy — a slight error",
    "mistake":    "a mistake — a significant error",
    "blunder":    "a blunder — a serious error",
    "brilliant":  "a brilliant move — a spectacular find",
    "book":       "a known opening theory move",
}

_INSTRUCTION_MAP = {
    "blunder": (
        "**Why this loses**: Identify the concrete tactical or strategic refutation. "
        "What does the opponent now play to punish this? Walk through the forcing line."
    ),
    "mistake": (
        "**The better path**: Why was {best_move} stronger? What immediate threat or "
        "plan does it create that {move_san} misses?"
    ),
    "inaccuracy": (
        "**The subtle drift**: What long-term consequence does this create? "
        "How does it weaken structure, concede space, or allow counterplay?"
    ),
    "brilliant": (
        "**The brilliancy**: Explain the deeper calculation — what makes this sacrifice "
        "work? Walk through why the opponent cannot simply take and come out ahead."
    ),
    "best": (
        "**Why this is optimal**: What makes this the engine's top choice? "
        "Name the specific threat, plan, or positional advantage it creates over alternatives."
    ),
    "good": (
        "**What this achieves**: What concrete idea drives this move? "
        "How does it fit the demands of the position?"
    ),
    "book": (
        "**Opening idea**: What is the strategic and tactical idea behind this "
        "theoretical move? What plan does it support or prevent?"
    ),
}


def explain_move(
    fen: str,
    move_san: str,
    eval_text: str,
    move_history: list[str],
    classification: str = "",
    best_move_san: str = "",
    followup_text: str = "",
    best_followup_text: str = "",
    eval_before: float = 0.0,
    eval_after: float = 0.0,
    color: str = "",
    game_phase: str = "",
    generate_concepts: bool = False,
    top_candidates: list[dict] | None = None,
) -> dict:
    """
    Ask Claude to explain a specific move at grandmaster depth.
    Includes a 3-4 ply engine continuation so Claude can discuss the next few moves.

    Returns {"explanation": str, "concepts": list[str]}.
    concepts is populated only when generate_concepts=True (for notable moves).
    """
    client = Anthropic()

    color_cap  = color.capitalize() if color else "The player"
    label      = _LABEL_MAP.get(classification, classification)
    history_text = ", ".join(move_history) if move_history else "game just started"
    phase_text = f" ({game_phase})" if game_phase else ""

    best_section = ""
    if best_move_san and best_move_san != move_san:
        best_section = f"\nEngine's recommended move instead: **{best_move_san}**"

    candidates_section = ""
    if top_candidates:
        played_eval = None
        for c in top_candidates:
            if c["san"] == move_san:
                played_eval = c["eval"]
                break
        lines = []
        for rank, c in enumerate(top_candidates, 1):
            marker = " ← played" if c["san"] == move_san else ""
            lines.append(f"  {rank}. {c['san']} ({c['eval']:+.2f}){marker}")
        # If the played move didn't appear in top 3 at all, append it explicitly
        if played_eval is None:
            played_ev_mover = round(eval_after if color == "white" else -eval_after, 2)
            lines.append(f"  (played) {move_san} ({played_ev_mover:+.2f}) ← played (outside top 3)")
        candidates_section = "\nStockfish top candidates (mover's perspective, higher = better for mover):\n" + "\n".join(lines)

    followup_section = ""
    if followup_text:
        followup_section = f"\nEngine's best play from here: {followup_text}"
    else:
        followup_section = "\n(no continuation available)"

    best_continuation_section = ""
    if best_followup_text and classification in ("blunder", "mistake", "inaccuracy"):
        best_continuation_section = (
            f"\nIf {best_move_san} had been played instead, the likely continuation would be: "
            f"{best_followup_text}"
        )

    _INSIGHT4_LABEL = {
        "blunder":   "Why It Loses",
        "mistake":   "Better Path",
        "inaccuracy":"Subtle Cost",
        "brilliant": "The Brilliancy",
        "best":      "Why It's Best",
        "good":      "The Idea",
        "book":      "Opening Idea",
    }
    insight4_label = _INSIGHT4_LABEL.get(classification, "Key Lesson")

    instruction = _INSTRUCTION_MAP.get(classification, _INSTRUCTION_MAP["good"])
    instruction = instruction.format(best_move=best_move_san or "the best move", move_san=move_san)

    concepts_field = ', "concepts": ["Concept1", "Concept2"]' if generate_concepts else ""

    prompt = f"""You are a grandmaster-level chess coach. Analyse this move with precision and brevity.

POSITION & MOVE
FEN before move: {fen}
{color_cap} played: {move_san} — {label}
Evaluation shift: {eval_before:+.2f} → {eval_after:+.2f} (positive = White winning){phase_text}
Move history: {history_text}{best_section}{candidates_section}

ENGINE CONTINUATION after {move_san}:{followup_section}{best_continuation_section}

Return ONLY valid JSON (no markdown, no code fences):
{{
  "insights": [
    {{"label": "Board Effect",      "text": "<1–2 sentences>"}},
    {{"label": "Immediate Threat",  "text": "<1–2 sentences>"}},
    {{"label": "Engine Line",       "text": "<1–2 sentences>"}},
    {{"label": "{insight4_label}",  "text": "<1–2 sentences>"}},
    {{"label": "Chess Principle",   "text": "<1–2 sentences>"}}
  ]{concepts_field}
}}

Instructions per insight (30–50 words each, use move names throughout):
- Board Effect: what does {move_san} concretely do — captures, threats, lines opened/closed, squares controlled or weakened
- Immediate Threat: what happens in the very next 1–2 moves for both sides; name the moves
- Engine Line: reference the continuation above and explain the mechanism, not just "White is better"
- {insight4_label}: {instruction}
- Chess Principle: name the exact concept (e.g. "overloading the rook on e8", "trading the bad bishop", "the two-weakness principle")"""

    if generate_concepts:
        prompt += (
            '\n\nFor "concepts": 1–4 specific chess concept labels (2–5 words each).'
            ' Prefer "Overloaded Defender" over "Tactics", "Weak Back Rank" over "Positioning".'
            " Do not include move notation."
        )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            timeout=45.0,
            messages=[{"role": "user", "content": prompt}]
        )
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("explain_move API error: %s", e)
        return {"insights": [{"label": "Analysis", "text": "Analysis temporarily unavailable. Please try again."}], "concepts": []}

    result = _parse_explain_response(message.content[0].text)
    for ins in result.get("insights", []):
        if "text" in ins:
            ins["text"] = _validate_move_refs(ins["text"], fen)
    return result


def generate_concept_lesson(
    concept: str,
    game_examples: list[dict] | None = None,
    enriched_examples: list[dict] | None = None,
) -> str:
    """
    Generate a focused educational lesson on a chess concept.

    game_examples: list of {move_number, color, move_san, classification} dicts
                   sourced from the player's own game analysis.
    enriched_examples: list of {fen, move_san, best_move_san, eval_before, eval_after,
                       classification, phase, move_number, color} dicts with full
                       engine data — used to ground the lesson in real positions.

    Returns markdown-formatted lesson text (ready for st.markdown).
    """
    client = Anthropic()

    ex_text = ""
    if enriched_examples:
        # Use enriched data with FENs and evals for grounded teaching
        lines = []
        for e in enriched_examples[:3]:
            dot = "." if e.get("color") == "white" else "..."
            ev_b = e.get("eval_before", 0)
            ev_a = e.get("eval_after", 0)
            best = e.get("best_move_san", "")
            fen = e.get("fen", "")
            phase = e.get("phase", "")
            line = (
                f"  - Move {e.get('move_number', '?')}{dot}{e.get('move_san', '?')} "
                f"({e.get('classification', '?')}, {phase})\n"
                f"    FEN: {fen}\n"
                f"    Eval: {ev_b:+.2f} → {ev_a:+.2f}"
            )
            if best and best != e.get("move_san"):
                line += f" | Best was: {best}"
            lines.append(line)
        ex_text = (
            "\n\nThis concept appeared in the student's recent games. "
            "Use these EXACT positions to illustrate the concept — reference the FEN, "
            "the move played, why the engine's recommendation was better, and what the "
            "eval shift reveals:\n"
            + "\n".join(lines)
            + "\n\nIMPORTANT: In 'How to spot it', reference signals visible in these "
            "actual positions (piece placement, pawn structure, king safety, etc.). "
            "In 'How to use it', walk through one of the above positions step by step."
        )
    elif game_examples:
        lines = []
        for e in game_examples[:3]:
            dot = "." if e["color"] == "white" else "..."
            lines.append(
                f"  - Move {e['move_number']}{dot}{e['move_san']} ({e['classification']})"
            )
        ex_text = (
            "\n\nThis concept appeared in the student's recent game:\n"
            + "\n".join(lines)
            + "\nReference these specific positions where it adds clarity."
        )

    # If we have enriched examples, use their FENs as example positions
    # instead of asking Claude to invent positions from memory
    example_instruction = ""
    if enriched_examples:
        fen_examples = []
        for e in enriched_examples[:2]:
            fen = e.get("fen", "")
            best = e.get("best_move_san", e.get("move_san", ""))
            if fen and best:
                fen_examples.append(
                    f"FEN: {fen}\n"
                    f"MOVE: {best}\n"
                    f"CAPTION: From the student's game — {e.get('classification', 'notable')} "
                    f"at move {e.get('move_number', '?')} (eval {e.get('eval_before', 0):+.1f} → "
                    f"{e.get('eval_after', 0):+.1f})"
                )
        if fen_examples:
            example_instruction = (
                "\n\n## Example Positions\n\n"
                "Use these REAL positions from the student's games as examples. "
                "You may add 1 additional classic textbook position if needed.\n\n"
                "---EXAMPLES---\n"
                + "\n\n".join(fen_examples)
                + "\n\nYou may add ONE more example using a well-known position, format:\n"
                "FEN: <valid FEN>\nMOVE: <legal SAN>\nCAPTION: <1-sentence>"
            )
    if not example_instruction:
        example_instruction = """

## Example Positions

After the lesson text, provide 2–3 illustrative board positions that demonstrate the concept.
Use well-known textbook patterns or classic game positions.

CRITICAL: Each FEN must be a valid chess position (correct piece counts, valid castling rights,
exactly one king per side). Each MOVE must be legal in that exact FEN position.
Double-check before writing: could this move actually be played on this board?

Use this exact format after your lesson text (the delimiter line is required):

---EXAMPLES---
FEN: <valid FEN string>
MOVE: <legal SAN move in this position>
CAPTION: <1-sentence explanation of what this move demonstrates>

FEN: <valid FEN string>
MOVE: <legal SAN move in this position>
CAPTION: <1-sentence explanation>"""

    prompt = f"""You are a chess coach writing a focused lesson for a club-level player (rated 1200–1600).

Write a practical, actionable lesson on: **{concept}**{ex_text}

Use exactly these section headers (markdown ## level, no deviations):

## What is it?
## Why it matters
## How to spot it
## How to use it
## Key rule of thumb

Guidelines:
- Total length: 260–330 words
- Write directly to the student ("you", "your")
- Use move notation where it adds precision (e.g. Rxf7!, Nd5+)
- Every sentence must be actionable — no vague generalities
- "How to spot it": give 3–4 concrete signals to look for in any position
- "Key rule of thumb": one memorable, punchy sentence they can recall mid-game at the board
- When referencing the student's games, cite the eval shift to show WHY the move was wrong
  (e.g. "Your 14.Bxf7 dropped the eval from +0.5 to -2.1 because...")
- Name specific squares, pieces, and lines — never say "a piece" when you mean "the knight on d5"{example_instruction}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            timeout=60.0,
            messages=[{"role": "user", "content": prompt}]
        )
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("generate_concept_lesson API error: %s", e)
        raise RuntimeError(f"Lesson generation temporarily unavailable: {e}") from e
    return message.content[0].text.strip()


def parse_lesson_diagrams(text: str) -> tuple[str, list[dict], None]:
    """
    Split lesson text into clean markdown and validated board diagrams.

    Returns (lesson_text, diagrams, None) where:
    - diagrams is a list of {"fen": str, "move": str, "caption": str} dicts
    - Third element is always None (kept for backward compatibility).
    """
    # Strip any legacy ---QUESTION--- block from existing DB lessons
    q_delim = "---QUESTION---"
    if q_delim in text:
        text = text.split(q_delim, 1)[0]

    delimiter = "---EXAMPLES---"
    if delimiter not in text:
        return (text.strip(), [], None)

    parts = text.split(delimiter, 1)
    lesson_text = parts[0].strip()
    examples_raw = parts[1].strip()

    diagrams = []
    current: dict[str, str] = {}
    for line in examples_raw.split("\n"):
        line = line.strip()
        if line.upper().startswith("FEN:"):
            if current.get("fen"):
                diagrams.append(current)
                current = {}
            current["fen"] = line[4:].strip()
        elif line.upper().startswith("MOVE:"):
            current["move"] = line[5:].strip()
        elif line.upper().startswith("CAPTION:"):
            current["caption"] = line[8:].strip()
    if current.get("fen"):
        diagrams.append(current)

    # Validate each diagram with python-chess
    valid = []
    for d in diagrams:
        fen = d.get("fen", "")
        move_san = d.get("move", "")
        caption = d.get("caption", "")
        if not fen or not move_san:
            continue
        try:
            board = chess.Board(fen)
            board.parse_san(move_san)  # validates legality
            valid.append({"fen": fen, "move": move_san, "caption": caption})
        except Exception:
            continue

    return (lesson_text, valid, None)


def generate_ranked_lesson(
    concept: str,
    rating_band: str,
    game_examples: list[dict] | None = None,
) -> str:
    """
    Generate a focused educational lesson calibrated to the player's rating band.

    rating_band: e.g. "1000–1200"
    game_examples: list of {move_number, color, move_san, classification} dicts.

    Returns markdown-formatted lesson text (ready for st.markdown).
    """
    client = Anthropic()

    ex_text = ""
    if game_examples:
        lines = []
        for e in game_examples[:3]:
            dot = "." if e["color"] == "white" else "..."
            lines.append(
                f"  - Move {e['move_number']}{dot}{e['move_san']} ({e['classification']})"
            )
        ex_text = (
            "\n\nThis concept appeared in the student's recent game:\n"
            + "\n".join(lines)
            + "\nReference these specific positions where it adds clarity."
        )

    prompt = f"""You are a chess coach writing a focused lesson for a chess player rated {rating_band}.

Calibrate complexity to this level. For lower ratings, focus on basic recognition and simple examples. For higher ratings, discuss nuances, exceptions, and deeper strategic implications.

Write a practical, actionable lesson on: **{concept}**{ex_text}

Use exactly these section headers (markdown ## level, no deviations):

## What is it?
## Why it matters
## How to spot it
## How to use it
## Key rule of thumb

Guidelines:
- Total length: 260–330 words
- Write directly to the student ("you", "your")
- Use move notation where it adds precision (e.g. Rxf7!, Nd5+)
- Every sentence must be actionable — no vague generalities
- "How to spot it": give 3–4 concrete signals to look for in any position
- "Key rule of thumb": one memorable sentence they can recall at the board"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=700,
            timeout=45.0,
            messages=[{"role": "user", "content": prompt}]
        )
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("generate_ranked_lesson API error: %s", e)
        raise RuntimeError(f"Lesson generation temporarily unavailable: {e}") from e
    return message.content[0].text.strip()


_GENERIC_HINTS = [
    "Look for a forcing move that creates two threats at once.",
    "One of your pieces can exploit an undefended target — find the right sequence.",
    "There is a tactical pattern here involving a vulnerable piece. Look carefully at what is unprotected.",
    "Consider which of your opponent's pieces is overworked or misplaced, and how to take advantage.",
]


def _check_hint_leaks(hint_text: str, best_move_san: str, fen: str) -> bool:
    """Return True if the hint leaks the answer move, from-square, or to-square."""
    # Strip check/mate symbols for comparison
    clean_san = best_move_san.replace("+", "").replace("#", "")
    if clean_san.lower() in hint_text.lower():
        return True

    # Extract from/to squares from the move
    try:
        board = chess.Board(fen)
        move = board.parse_san(best_move_san)
    except Exception:
        return False

    from_sq = chess.square_name(move.from_square)
    to_sq = chess.square_name(move.to_square)

    if re.search(r'\b' + re.escape(from_sq) + r'\b', hint_text, re.IGNORECASE):
        return True
    if re.search(r'\b' + re.escape(to_sq) + r'\b', hint_text, re.IGNORECASE):
        return True

    # Check for castling keywords if the move is castling
    if board.is_castling(move):
        if re.search(r'\bcastl', hint_text, re.IGNORECASE):
            return True

    return False


def generate_puzzle_hint(
    fen: str,
    best_move_san: str,
    player_color: str,
    classification: str,
    eval_before: float | None = None,
    eval_after: float | None = None,
) -> str:
    """
    Generate a short coaching hint for a puzzle position without revealing the move.

    Returns a 1-2 sentence hint (plain text, no markdown).
    """
    client = Anthropic()

    eval_context = ""
    if eval_before is not None and eval_after is not None:
        swing = abs(eval_after - eval_before)
        eval_context = (
            f"\nEval context: position was {eval_before:+.2f} before, the played move "
            f"changed it to {eval_after:+.2f} (swing of {swing:.1f} pawns). "
            f"Use this to gauge the severity — guide urgency accordingly."
        )

    prompt = f"""You are a chess coach giving a hint to a student working on a puzzle.

Position (FEN): {fen}
Player: {player_color.capitalize()}
Classification: {classification} (there is a significantly better move available)
Best move: {best_move_san} — DO NOT mention this move, its starting square, or its destination square.{eval_context}

Write exactly 1-2 sentences that guide the student toward finding the best move WITHOUT revealing it.
Focus on the tactical or strategic idea: name the pattern (pin, fork, discovered attack, back rank, overloaded piece, etc.) and point toward the weakness or resource to exploit.
Reference specific pieces and squares visible in the FEN — no generic advice.

Reply with ONLY the hint text. No quotes, no labels, no markdown."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            timeout=30.0,
            messages=[{"role": "user", "content": prompt}],
        )
        hint = message.content[0].text.strip()
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("generate_puzzle_hint API error: %s", e)
        import random
        return random.choice(_GENERIC_HINTS)
    if _check_hint_leaks(hint, best_move_san, fen):
        import random
        hint = random.choice(_GENERIC_HINTS)
    return hint


def generate_puzzle_explanation(
    fen: str,
    best_move_san: str,
    player_color: str,
    classification: str,
    was_correct: bool,
    eval_before: float | None = None,
    eval_after: float | None = None,
    played_move_san: str | None = None,
) -> str:
    """
    Generate a short explanation of why the best move works in a puzzle position.

    Returns 2-3 sentences plain text.
    """
    client = Anthropic()

    outcome = (
        "The student found the correct move. Affirm briefly, then add a deeper insight about the position."
        if was_correct
        else "The student did NOT find the correct move. Explain the pattern they should recognize next time."
    )

    eval_context = ""
    if eval_before is not None and eval_after is not None:
        eval_context = (
            f"\nEval shift: {eval_before:+.2f} → {eval_after:+.2f} "
            f"(the played move cost {abs(eval_after - eval_before):.1f} pawns). "
            f"Reference this eval swing to show WHY the best move matters."
        )

    played_context = ""
    if played_move_san and played_move_san != best_move_san and not was_correct:
        played_context = (
            f"\nThe student played: {played_move_san}. Briefly explain what's wrong "
            f"with this move compared to {best_move_san}."
        )

    prompt = f"""You are a chess coach explaining a puzzle solution.

Position (FEN): {fen}
Best move: {best_move_san}
Player: {player_color.capitalize()}
Classification: {classification}
{outcome}{eval_context}{played_context}

In 2-3 sentences, explain WHY {best_move_san} is the best move. Name the tactic or strategy (pin, fork, discovered attack, etc.) and reference specific squares and pieces from the FEN. Be concrete and educational.

Reply with ONLY the explanation text. No quotes, no labels, no markdown."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=180,
            timeout=30.0,
            messages=[{"role": "user", "content": prompt}],
        )
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("generate_puzzle_explanation API error: %s", e)
        return f"The best move was {best_move_san}. Explanation temporarily unavailable."
    return _validate_move_refs(message.content[0].text.strip(), fen)


def full_game_review(game_moves: list[dict], pgn_headers: dict) -> dict:
    """
    Send a structured summary of the whole game to Claude and get a full review.

    Only sends key moments (blunders + mistakes) rather than every move, to save tokens.

    Returns:
        {
            "summary": str,
            "key_moments": list[str],
            "missed_tactics": list[str],
            "positional_themes": list[str],
            "tips_to_learn": list[str],
        }
    """
    client = Anthropic()

    white = pgn_headers.get("White", "White")
    black = pgn_headers.get("Black", "Black")
    result = pgn_headers.get("Result", "*")
    date = pgn_headers.get("Date", "")
    opening = pgn_headers.get("Opening", "")

    # Build a compact move list with classifications
    move_lines = []
    for m in game_moves:
        mn = m["move_number"]
        col = m["color"]
        san = m["move_san"]
        cls = m["classification"]
        ev_before = m["eval_before"]
        ev_after = m["eval_after"]
        best = m.get("best_move_san", "")
        prefix = f"{mn}." if col == "white" else f"{mn}..."
        line = f"{prefix}{san} [{cls}, eval {ev_before:+.1f}→{ev_after:+.1f}]"
        if cls in ("blunder", "mistake") and best and best != san:
            line += f" (best was {best})"
        move_lines.append(line)

    # Only pass blunders and mistakes in detail; include full move list briefly
    critical_moves = [m for m in game_moves if m["classification"] in ("blunder", "mistake")]
    critical_summary = []
    for m in critical_moves:
        mn = m["move_number"]
        col = m["color"]
        san = m["move_san"]
        cls = m["classification"]
        best = m.get("best_move_san", "")
        ev_before = m["eval_before"]
        ev_after = m["eval_after"]
        fen_before = m.get("fen_before", "")
        entry = (
            f"Move {mn} ({'White' if col == 'white' else 'Black'}): "
            f"{san} was a {cls} (eval changed {ev_before:+.1f} → {ev_after:+.1f})"
        )
        if best and best != san:
            entry += f". Better was {best}."
        if fen_before:
            entry += f"\n  FEN before: {fen_before}"
        critical_summary.append(entry)

    full_move_list = " ".join(move_lines)
    critical_text = "\n".join(critical_summary) if critical_summary else "No major blunders or mistakes."

    # Count stats
    white_blunders = sum(1 for m in game_moves if m["color"] == "white" and m["classification"] == "blunder")
    black_blunders = sum(1 for m in game_moves if m["color"] == "black" and m["classification"] == "blunder")
    white_mistakes = sum(1 for m in game_moves if m["color"] == "white" and m["classification"] == "mistake")
    black_mistakes = sum(1 for m in game_moves if m["color"] == "black" and m["classification"] == "mistake")

    # WP loss by phase
    _phase_wpl: dict[str, dict[str, float]] = {}
    for m in game_moves:
        _mn = m["move_number"]
        _ph = "opening" if _mn <= 12 else "endgame" if _mn >= 36 else "middlegame"
        _col = m["color"]
        _phase_wpl.setdefault((_ph, _col), 0.0)
        _phase_wpl[(_ph, _col)] += m.get("wp_loss", 0)
    _wp_lines = []
    for _ph in ["opening", "middlegame", "endgame"]:
        _w = _phase_wpl.get((_ph, "white"), 0)
        _b = _phase_wpl.get((_ph, "black"), 0)
        if _w > 0 or _b > 0:
            _wp_lines.append(f"  {_ph.capitalize()}: White lost {_w:.1f}%, Black lost {_b:.1f}%")
    _wp_phase_text = "\n".join(_wp_lines) if _wp_lines else "  (not available)"

    prompt = f"""You are a chess coach reviewing a completed game. Provide a structured analysis.

Game details:
- White: {white}
- Black: {black}
- Result: {result}
- Date: {date}
- Opening: {opening}
- White blunders/mistakes: {white_blunders}/{white_mistakes}
- Black blunders/mistakes: {black_blunders}/{black_mistakes}
- Win probability cost by phase:
{_wp_phase_text}

Critical moments:
{critical_text}

Full annotated move list:
{full_move_list}

Respond ONLY with valid JSON (no markdown, no code fences) in this exact format:
{{
  "summary": "2-3 sentence overview of how the game went and who had the advantage",
  "key_moments": ["Move X: description of what happened and why it mattered", ...],
  "missed_tactics": ["Specific tactical pattern that was missed at move X: description", ...],
  "positional_themes": ["Theme 1: explanation", ...],
  "tips_to_learn": ["Actionable tip 1", "Actionable tip 2", "Actionable tip 3"]
}}

Keep each list to 3-5 items. Be specific and educational.
When FENs are provided for critical moments, reference the actual piece placement — name specific squares, pieces, and threats visible in the position. Never say "a piece" when you can say "the knight on d5"."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            timeout=60.0,
            messages=[{"role": "user", "content": prompt}]
        )
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("full_game_review API error: %s", e)
        return {
            "summary": "Game review temporarily unavailable. Please try again.",
            "key_moments": [],
            "missed_tactics": [],
            "positional_themes": [],
            "tips_to_learn": [],
        }

    raw = message.content[0].text.strip()

    # Strip markdown code fences if Claude wrapped it anyway
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback if parsing fails
        return {
            "summary": raw[:300],
            "key_moments": [],
            "missed_tactics": [],
            "positional_themes": [],
            "tips_to_learn": [],
        }


def coach_chat_stream(messages: list[dict], profile_context: str = ""):
    """
    Streaming chess coach chat.

    messages: list of {"role": "user"/"assistant", "content": str}
    profile_context: pre-built string with the player's profile stats, or ""

    Yields text chunks (compatible with st.write_stream).
    """
    client = Anthropic()

    system = (
        "You are a friendly, knowledgeable chess coach. "
        "Give practical, actionable advice suited for club-level players (roughly 800–1800 Elo). "
        "When explaining concepts, use concrete examples with move notation where helpful (e.g. Nd5+, Rxf7!). "
        "Keep answers focused — 2–5 short paragraphs unless a longer breakdown is genuinely needed. "
        "Prioritise what the player can actually use at the board over abstract theory. "
        "When you reference a specific game position, name the piece and square (e.g. 'your knight on d5'). "
        "Never pad with empty encouragement — every sentence should teach something."
    )

    if profile_context:
        system += (
            "\n\nHere is the player's profile from their recent games "
            "(use this to personalise your advice — reference their specific weaknesses and strengths):\n"
            + profile_context
        )

    try:
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=system,
            messages=messages,
        ) as stream:
            yield from stream.text_stream
    except (APIError, APIConnectionError, APITimeoutError) as e:
        _log.warning("coach_chat_stream API error: %s", e)
        yield "I'm temporarily unable to respond. Please try again in a moment."
