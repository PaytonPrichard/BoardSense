"""
BoardSense - app.py
Streamlit dashboard for BoardSense chess coaching.

Run with:
    python -m streamlit run app.py
"""

import base64
import math
import os
import random
import threading
import time
import traceback
import chess
import chess.pgn
import chess.svg
import io
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

# ── API key: prefer st.secrets (Streamlit Cloud), fall back to .env ──────────
if not os.environ.get("ANTHROPIC_API_KEY"):
    try:
        _secret_key = st.secrets["ANTHROPIC_API_KEY"]
        os.environ["ANTHROPIC_API_KEY"] = _secret_key
    except (KeyError, FileNotFoundError):
        pass  # will be caught by validation below

from engine import analyze_game, analyze_game_iter, get_followup_lines
from tutor import explain_move, full_game_review, generate_concept_lesson, generate_ranked_lesson, generate_puzzle_hint, generate_puzzle_explanation, coach_chat_stream, parse_lesson_diagrams
from profile import bulk_analyze_games, build_player_profile, PIECE_TIERS, SKILL_CATEGORIES
from curriculum import CURRICULUM, get_stage_for_rating, get_recommended_modules, get_module, build_module_puzzles, validate_curriculum
import db
import chesscom
import lichess

# ── Background profile build registry ────────────────────────────────────────
_BUILD_LOCK = threading.Lock()
_BUILD_JOBS: dict[str, dict] = {}   # username -> job dict


_SKILL_CATS = ["Opening Prep", "Middlegame", "Endgame", "Tactics", "Consistency"]


def compute_skill_scores(sums: list[dict]) -> dict[str, int]:
    """Compute 0-100 skill scores from game summaries. Shared by Dashboard & Profile."""
    def _safe_avg(vals):
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else 50.0

    n = len(sums)
    if n == 0:
        return {c: 50 for c in _SKILL_CATS}

    op_acc  = _safe_avg([s.get("opening_accuracy") for s in sums])
    mid_acc = _safe_avg([s.get("middlegame_accuracy") for s in sums])
    end_acc = _safe_avg([s.get("endgame_accuracy") for s in sums])

    blunders_pg = sum(s.get("blunders", 0) for s in sums) / n
    mistakes_pg = sum(s.get("mistakes", 0) for s in sums) / n
    tactics = max(0, min(100, round(100 - blunders_pg * 8 - mistakes_pg * 4)))

    accs = [s.get("player_accuracy", 50) for s in sums if s.get("player_accuracy") is not None]
    if len(accs) >= 2:
        import statistics
        sd = statistics.stdev(accs)
        consistency = max(0, min(100, round(100 - sd * 2.5)))
    else:
        consistency = round(_safe_avg(accs))

    return {
        "Opening Prep": round(op_acc),
        "Middlegame": round(mid_acc),
        "Endgame": round(end_acc),
        "Tactics": tactics,
        "Consistency": consistency,
    }


def _run_profile_build(username: str, games: list, depth: int, platform: str, job: dict):
    """Run Stockfish bulk analysis + Claude profile synthesis in a background thread."""
    try:
        summaries: list = []
        start = time.time()
        for update in bulk_analyze_games(games, username, depth=depth):
            if update[0] == "progress":
                _, done, total, summary = update
                if summary:
                    summaries.append(summary)
                job["done"] = done
                job["total"] = total
                elapsed = time.time() - start
                if done >= 1:
                    job["eta_secs"] = (elapsed / done) * (total - done)
            else:
                summaries = update[1]

        # Merge with existing summaries for incremental updates
        if job.get("is_update") and job.get("existing_summaries"):
            summaries = job["existing_summaries"] + summaries

        if not summaries:
            job["error"] = "No games could be analysed."
            job["status"] = "error"
            return

        job["status"] = "synthesizing"
        profile = build_player_profile(summaries, username)

        db.save_profile(username, profile, summaries)
        db.save_profile_history(username, profile, len(summaries))
        db.save_active_user(username, platform)

        job["result"] = {"profile": profile, "summaries": summaries}
        job["status"] = "done"
    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BoardSense",
    page_icon="♟",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Board settings constants ─────────────────────────────────────────────────
_BOARD_THEMES = {
    "Brown":           {"light": "#f0d9b5", "dark": "#b58863"},
    "Chess.com Green": {"light": "#eeeed2", "dark": "#769656"},
    "Blue":            {"light": "#dee3e6", "dark": "#8ca2ad"},
    "Walnut":          {"light": "#e8d0a8", "dark": "#b48764"},
}
_PIECE_SETS = {
    "Cburnett":  "https://lichess1.org/assets/piece/cburnett/",
    "Staunty":   "https://lichess1.org/assets/piece/staunty/",
    "Neo":       "https://lichess1.org/assets/piece/neo/",
    "Alpha":     "https://lichess1.org/assets/piece/alpha/",
}
_BOARD_SIZES = {"Small (48px)": 48, "Standard (64px)": 64, "Large (76px)": 76}

# Defaults
st.session_state.setdefault("board_theme", "Brown")
st.session_state.setdefault("piece_set", "Cburnett")
st.session_state.setdefault("sound_enabled", True)
st.session_state.setdefault("animation_enabled", True)
st.session_state.setdefault("show_legal_moves", True)
st.session_state.setdefault("show_coordinates", True)
st.session_state.setdefault("board_square_size", "Standard (64px)")
st.session_state.setdefault("high_contrast", False)
st.session_state.setdefault("reduce_motion", False)

# ── Global CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* ── Chess board page background ─────────────────────────────────────── */
    .stApp {
        background-color: #0c1021;
        background-image: repeating-conic-gradient(
            #111a2e 0% 25%, #0c1021 0% 50%
        ) 0 0 / 52px 52px;
    }

    /* ── Main content card floating above the board ──────────────────────── */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 0.5rem;
        background: rgba(11, 14, 22, 0.96);
    }

    /* ── Section dividers: gradient fade instead of a hard rule ──────────── */
    hr {
        border: none !important;
        height: 1px !important;
        background: linear-gradient(
            to right, transparent, #1c2840 20%, #1c2840 80%, transparent
        ) !important;
        margin: 6px 0 !important;
    }

    /* ── Compact stat cards (kept from before) ───────────────────────────── */
    [data-testid="metric-container"] {
        background: #1e1e2e;
        border: 1px solid #2a2a3e;
        border-radius: 8px;
        padding: 8px 12px;
    }
    [data-testid="metric-container"] label { font-size: 0.72rem !important; }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
    }

    /* ── Navigation pills ────────────────────────────────────────────────── */
    [data-baseweb="tab-list"] {
        justify-content: center !important;
        gap: 6px !important;
        background: transparent !important;
        padding: 6px 0 10px !important;
        border-bottom: none !important;
        flex-wrap: wrap !important;
    }
    button[data-baseweb="tab"] {
        background: #111827 !important;
        border: 1px solid #253450 !important;
        border-radius: 999px !important;
        padding: 7px 18px !important;
        color: #6a8aaa !important;
        font-size: 0.82em !important;
        font-weight: 600 !important;
        letter-spacing: 0.04em !important;
        text-transform: uppercase !important;
        white-space: nowrap !important;
        transition: background 0.15s, border-color 0.15s, color 0.15s !important;
    }
    button[data-baseweb="tab"]:hover {
        background: #192236 !important;
        border-color: #4a6aaa !important;
        color: #a8c8e8 !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        background: #172040 !important;
        border-color: #5a7ac8 !important;
        color: #cce0f4 !important;
        box-shadow: 0 0 14px rgba(74, 106, 200, 0.35) !important;
    }
    /* Hide the underline — pills are self-contained */
    [data-baseweb="tab-highlight"],
    [data-baseweb="tab-border"] {
        display: none !important;
    }

    /* ── Widget labels (file uploader prompt, selectbox, etc.) ──────────── */
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] span {
        color: #a0bcd4 !important;
    }

    /* ── File uploader: all text inside (dropzone + uploaded filename) ───── */
    [data-testid="stFileUploader"] * {
        color: #8aaac8 !important;
    }
    /* Restore the Browse files button (dark text on light button) */
    [data-testid="stFileUploader"] button,
    [data-testid="stFileUploader"] button * {
        color: #111 !important;
    }
    [data-testid="stFileUploaderDropzone"] {
        border-color: #2a3a5a !important;
        background-color: #0d1422 !important;
    }

    /* ── Prevent button text from wrapping onto two lines ────────────────── */
    .stButton button, [data-testid="stBaseButton-secondary"],
    [data-testid="stBaseButton-primary"] {
        white-space: nowrap !important;
    }
    /* ── Sub-nav radio buttons: larger, pill-like labels ──────────────────── */
    [data-baseweb="radio"] label {
        white-space: nowrap !important;
        font-size: 0.88em !important;
        padding: 6px 14px !important;
        min-height: 36px !important;
        cursor: pointer !important;
        display: inline-flex !important;
        align-items: center !important;
    }
    [data-baseweb="radio"] label span {
        font-weight: 600 !important;
        letter-spacing: 0.03em !important;
    }

    /* ── st.info / st.success boxes ─────────────────────────────────────── */
    [data-testid="stAlert"] p {
        color: #c4d8ec !important;
    }

    /* ── General body text and all markdown elements ─────────────────────── */
    .stMarkdown p, .stMarkdown li,
    .stMarkdown h1, .stMarkdown h3,
    .stMarkdown h4, .stMarkdown h5, .stMarkdown h6 {
        color: #c0d0e0 !important;
    }
    .stMarkdown h1, .stMarkdown h3 { color: #cce0f4 !important; }
    .stMarkdown strong { color: #ddeeff !important; }
    .stMarkdown p  { line-height: 1.65 !important; margin-bottom: 0.55em !important; }
    .stMarkdown li { line-height: 1.55 !important; margin-bottom: 0.2em  !important; }

    /* ── Lesson section headers (Claude uses ## for the 5 fixed sections) ── */
    /* Render as compact small-caps labels instead of large bold headings     */
    .stMarkdown h2 {
        font-size: 0.68em !important;
        font-weight: 800 !important;
        letter-spacing: 0.12em !important;
        text-transform: uppercase !important;
        color: #5a8ab0 !important;
        margin-top: 1.1em !important;
        margin-bottom: 0.35em !important;
        padding-bottom: 5px !important;
        border-bottom: 1px solid #1a2e48 !important;
    }

    /* ── Secondary buttons: visible but not competing with primary ─────────── */
    button[data-testid="stBaseButton-secondary"] {
        background-color: #0d1a2e !important;
        border: 1px solid #2e4e72 !important;
        color: #88b4d4 !important;
        transition: background 0.15s, border-color 0.15s, color 0.15s !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover {
        background-color: #132240 !important;
        border-color: #4a7aaa !important;
        color: #b8d4ec !important;
    }

    /* ── Move list buttons: compact mono override (game review only) ─────── */
    /* Scoped to 6+-column layouts so it doesn't bleed into concept nav */
    [data-testid="stHorizontalBlock"]:has(> div:nth-child(6)) button[data-testid="stBaseButton-secondary"] {
        padding-top:    3px  !important;
        padding-bottom: 3px  !important;
        min-height:     28px !important;
        font-family: monospace !important;
        font-size: 0.82em !important;
        letter-spacing: 0.01em !important;
    }

    /* ── Keep puzzle iframes bright during Streamlit reruns ─────────────── */
    iframe { opacity: 1 !important; transition: none !important; }
    .stale, [data-stale="true"] { opacity: 1 !important; transition: none !important; }

    /* ── Daily goal card-buttons ─────────────────────────────────────────── */
    .stElementContainer:has(.dg-card-marker) { margin: 0 !important; height: 0; overflow: hidden; }
    .stElementContainer:has(.dg-card-marker) + .stElementContainer button {
        background: #0f1923 !important;
        border: 1px solid #1e2e3e !important;
        border-radius: 8px 8px 0 0 !important;
        padding: 10px 12px !important;
        min-height: 0 !important;
        font-size: 0.78em !important;
        font-weight: 600 !important;
        color: #a0bccc !important;
        cursor: pointer !important;
    }
    .stElementContainer:has(.dg-card-marker) + .stElementContainer button:hover {
        background: #132236 !important;
        border-color: #2a4a6a !important;
        color: #cce0f4 !important;
    }

    /* ── Reduce default Streamlit vertical gaps between elements ─────────── */
    [data-testid="stVerticalBlockBorderWrapper"] { padding: 0; margin: 0; }
    .stElementContainer { margin-bottom: 0.25rem; }

    /* ── Hide sidebar completely ──────────────────────────────────────────── */
    [data-testid="stSidebar"] { display: none !important; }
    [data-testid="collapsedControl"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ── Accessibility: High Contrast + Reduce Motion ─────────────────────────────
if st.session_state.get("high_contrast"):
    st.markdown("""<style>
    .stMarkdown p, .stMarkdown li { color: #e8ecf0 !important; }
    .stMarkdown strong { color: #ffffff !important; }
    .stMarkdown h2 { color: #8ab8e0 !important; border-bottom-color: #2a4a6a !important; }
    [data-testid="stSidebar"] { border-right: 2px solid #3a5a7a !important; }
    button[data-testid="stBaseButton-secondary"] { border-width: 2px !important; color: #b0d0f0 !important; }
    button[data-baseweb="tab"] { border-width: 2px !important; color: #8aaace !important; }
    button[data-baseweb="tab"][aria-selected="true"] { color: #e8f0ff !important; border-color: #7a9ae8 !important; }
    hr { background: linear-gradient(to right, transparent, #3a5a7a 20%, #3a5a7a 80%, transparent) !important; }
    </style>""", unsafe_allow_html=True)

if st.session_state.get("reduce_motion"):
    st.markdown("""<style>
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
    </style>""", unsafe_allow_html=True)

# ── Classification styling ───────────────────────────────────────────────────
COLORS = {
    "brilliant":  "#b39ddb",
    "best":       "#4fc3f7",
    "book":       "#78909c",
    "good":       "#81c784",
    "inaccuracy": "#fff176",
    "mistake":    "#ffb74d",
    "blunder":    "#e57373",
}
SYMBOLS = {
    "brilliant":  "!!",
    "best":       "★",
    "book":       "",
    "good":       "✓",
    "inaccuracy": "?!",
    "mistake":    "?",
    "blunder":    "??",
}


# ── Concept library ──────────────────────────────────────────────────────────
CONCEPT_LIBRARY: dict[str, list[str]] = {
    "Tactics": [
        "Fork", "Pin", "Skewer", "Discovered Attack", "Double Check",
        "Deflection", "Decoy", "Overloading", "Zwischenzug",
        "Back Rank Weakness", "Trapped Piece", "X-Ray Attack",
    ],
    "Pawn Structure": [
        "Isolated Pawn", "Doubled Pawns", "Backward Pawn", "Passed Pawn",
        "Pawn Island", "Minority Attack",
    ],
    "Piece Play": [
        "Outpost", "Bad Bishop", "Bishop Pair", "Rook On Open File",
        "Rook On Seventh Rank", "Piece Activity", "Knight Outpost",
    ],
    "Positional": [
        "Two Weaknesses", "Space Advantage", "Prophylaxis", "King Safety",
        "Centralization", "Initiative",
    ],
    "Endgame": [
        "Opposition", "Zugzwang", "Triangulation",
        "Lucena Position", "Philidor Position", "Bishop vs Knight",
    ],
}

# Concepts whose positions can't be reliably detected from FEN alone.
# These get a "Theory" badge and no interactive course puzzles.
_THEORY_ONLY_CONCEPTS: frozenset[str] = frozenset({
    # Tactics (pattern requires knowing the sequence, not just the static position)
    "Skewer", "Discovered Attack", "Double Check", "Deflection", "Decoy",
    "Overloading", "Zwischenzug", "X-Ray Attack",
    # Pawn Structure
    "Backward Pawn", "Pawn Island", "Minority Attack",
    # Positional
    "Two Weaknesses", "Space Advantage", "Prophylaxis", "King Safety",
    "Centralization", "Initiative",
    # Endgame
    "Zugzwang", "Triangulation", "Lucena Position", "Philidor Position",
})

CATEGORY_COLORS: dict[str, str] = {
    "Tactics":          "#e57373",
    "Pawn Structure":   "#81c784",
    "Piece Play":       "#4fc3f7",
    "Positional":       "#ffb74d",
    "Endgame":          "#b39ddb",
    "From Your Games":  "#90a8b8",
}

TRACKED_USER = ""

# ── Achievement definitions ─────────────────────────────────────────────────
_ACHIEVEMENTS = {
    "first_puzzle":     {"name": "First Steps",      "desc": "Solve your first puzzle",      "icon": "\U0001f3af"},
    "streak_5":         {"name": "On Fire",           "desc": "5 puzzles correct in a row",   "icon": "\U0001f525"},
    "streak_10":        {"name": "Unstoppable",       "desc": "10 puzzles correct in a row",  "icon": "\u26a1"},
    "puzzles_25":       {"name": "Puzzle Enthusiast", "desc": "Solve 25 puzzles",             "icon": "\U0001f9e9"},
    "puzzles_100":      {"name": "Puzzle Master",     "desc": "Solve 100 puzzles",            "icon": "\U0001f3c6"},
    "first_lesson":     {"name": "Student",           "desc": "Complete your first lesson",   "icon": "\U0001f4d6"},
    "all_concepts":     {"name": "Scholar",           "desc": "Study all concept lessons",    "icon": "\U0001f393"},
    "first_review":     {"name": "Analyst",           "desc": "Review your first game",       "icon": "\U0001f50d"},
    "perfect_course":   {"name": "Perfect Score",     "desc": "Get 5/5 on a course quiz",     "icon": "\U0001f4af"},
    "profile_built":    {"name": "Identity",          "desc": "Build your player profile",    "icon": "\U0001f464"},
}

# ── DB init (runs once per session) ──────────────────────────────────────────
if not st.session_state.get("_db_initialized"):
    db.init_db()
    for _concept, _content in db.get_all_lessons().items():
        _lk = f"concept_lesson_{_concept.lower()}"
        if _lk not in st.session_state:
            st.session_state[_lk] = _content
    _ps = db.get_puzzle_stats()
    st.session_state.setdefault("puzzle_streak",      _ps["streak"])
    st.session_state.setdefault("puzzle_best_streak", _ps["best_streak"])
    st.session_state.setdefault("puzzle_recent",      _ps["recent"])
    # Load persisted phase results
    _pps = db.get_puzzle_phase_stats()
    if _pps:
        _ppr_loaded: dict[str, list] = {}
        for _ph, _st in _pps.items():
            _ppr_loaded[_ph] = [True] * _st["correct"] + [False] * (_st["attempted"] - _st["correct"])
        st.session_state.setdefault("puzzle_phase_results", _ppr_loaded)
    # Auto-load last active user's profile
    _active = db.get_active_user()
    if _active and "profile_data" not in st.session_state:
        _au_user, _au_plat = _active
        _au_saved = db.load_profile(_au_user)
        if _au_saved:
            _au_prof, _au_summ, _au_built = _au_saved
            st.session_state.profile_data = _au_prof
            st.session_state.profile_summaries = _au_summ
            st.session_state.profile_username_built = _au_user
            st.session_state.profile_platform = _au_plat
            st.session_state.profile_username = _au_user
    st.session_state._db_initialized = True

# ── Daily puzzle counter ────────────────────────────────────────────────────
from datetime import date as _date_cls
_today = _date_cls.today().isoformat()
if st.session_state.get("_puzzle_day") != _today:
    st.session_state._puzzle_day = _today
    st.session_state.puzzles_solved_today = 0
    st.session_state.puzzle_correct_today = 0

# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_followup(moves: list[str], evals: list[float]) -> str:
    """Format an engine continuation as 'Move1 (+ev) → Move2 (+ev) → ...'"""
    if not moves:
        return ""
    return " → ".join(f"{san} ({ev:+.1f})" for san, ev in zip(moves, evals))


def get_opening_name(headers: dict) -> str:
    eco = headers.get("ECO", "")
    name = headers.get("Opening", "")
    variation = headers.get("Variation", "")
    if name:
        full = f"{name}: {variation}" if variation else name
        return f"{eco} · {full}".strip(" ·") if eco else full
    eco_url = headers.get("ECOUrl", "")
    if eco_url:
        slug = eco_url.rstrip("/").split("/")[-1]
        name = slug.replace("-", " ").title()
        return f"{eco} · {name}".strip(" ·") if eco else name
    return eco


_BADGE_TOOLTIPS: dict[str, str] = {
    "brilliant":  "Brilliant (!!): spectacular find, often a sacrifice that works",
    "best":       "Best move (★): engine's top choice for this position",
    "book":       "Book move: established opening theory",
    "good":       "Good move (✓): solid play with no significant error",
    "inaccuracy": "Inaccuracy (?!): small error — roughly 5–10% win-probability loss",
    "mistake":    "Mistake (?): significant error — roughly 10–20% win-probability loss",
    "blunder":    "Blunder (??): serious error — 20%+ win-probability loss",
}

def classification_badge(cls: str) -> str:
    color   = COLORS.get(cls, "#fff")
    symbol  = SYMBOLS.get(cls, "")
    tooltip = _BADGE_TOOLTIPS.get(cls, "")
    return (
        f'<span title="{tooltip}" style="background:{color};color:#111;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8em;font-weight:bold;cursor:help;">'
        f'{cls.upper()} {symbol}</span>'
    )


def render_board(fen: str, last_move_uci: str | None = None,
                 orientation: chess.Color = chess.WHITE,
                 best_move_uci: str | None = None) -> str:
    board = chess.Board(fen)
    last_move = None
    if last_move_uci:
        try:
            last_move = chess.Move.from_uci(last_move_uci)
        except Exception:
            pass
    fill = {}
    if last_move:
        fill[last_move.from_square] = "#aaa23a"
        fill[last_move.to_square]   = "#cdd16f"
    arrows = []
    if best_move_uci:
        try:
            bm = chess.Move.from_uci(best_move_uci)
            arrows.append(chess.svg.Arrow(bm.from_square, bm.to_square, color="#4fc3f7"))
        except Exception:
            pass
    return chess.svg.board(
        board, size=520, lastmove=last_move, fill=fill,
        arrows=arrows, orientation=orientation,
    )


def render_board_with_eval(
    fen: str,
    eval_val: float,
    last_move_uci: str | None = None,
    orientation: chess.Color = chess.WHITE,
    board_size: int = 520,
    best_move_uci: str | None = None,
) -> str:
    """
    Return an HTML block containing the eval bar (left) and board (right) side by side.

    The bar always has black on top and white on bottom (standard chess convention),
    regardless of board orientation.
    Proportions use a sigmoid: eval=0 → 50/50, eval=+3 → ~82% white, eval=-3 → ~18% white.
    """
    # Round to match the label's displayed precision so bar and number stay in sync
    eval_val = round(eval_val, 1)

    # Sigmoid maps eval (pawns) → white percentage [0, 100]
    white_pct = 100.0 / (1.0 + math.exp(-eval_val * 0.5))
    black_pct = 100.0 - white_pct

    # Eval label
    if abs(eval_val) >= 9.5:
        label = "M" if eval_val > 0 else "-M"
    elif eval_val >= 0:
        label = f"+{eval_val:.1f}"
    else:
        label = f"{eval_val:.1f}"

    label_color = "#e8e8e8" if eval_val >= 0 else "#bbb"

    # Eval bar always: black on top, white on bottom (standard chess convention)
    top_bg, top_flex = "#1c1c1c", black_pct
    bot_bg, bot_flex = "#f0ead6", white_pct

    # Board SVG as base64
    svg = render_board(fen, last_move_uci, orientation, best_move_uci=best_move_uci)
    b64 = base64.b64encode(svg.encode()).decode()

    return f"""
<div style="display:flex;gap:6px;align-items:flex-start;">
  <div style="display:flex;flex-direction:column;align-items:center;gap:4px;flex-shrink:0;">
    <div style="width:22px;height:{board_size}px;display:flex;flex-direction:column;
                border-radius:3px;overflow:hidden;border:1px solid #3a3a3a;">
      <div style="background:{top_bg};flex:{top_flex:.2f} 1 0%;min-height:4px;"></div>
      <div style="background:{bot_bg};flex:{bot_flex:.2f} 1 0%;min-height:4px;"></div>
    </div>
    <span style="font-size:0.72em;font-weight:bold;color:{label_color};
                 font-family:monospace;">{label}</span>
  </div>
  <img src="data:image/svg+xml;base64,{b64}" width="{board_size}" style="max-width:100%;height:auto;"/>
</div>
"""


def compute_accuracy(moves: list[dict], color: str) -> float:
    """
    Chess.com-style accuracy: simple average of every move's WPL-based
    accuracy score.  Book moves are evaluated identically to non-book moves;
    a book move that is Stockfish's top choice earns ~100% naturally through
    the formula, but a slightly inferior opening choice earns less.
    """
    side = [m for m in moves if m["color"] == color]
    if not side:
        return 100.0
    total = sum(m.get("move_accuracy", 100.0) for m in side)
    return round(total / len(side), 1)


# ── Eval graph ───────────────────────────────────────────────────────────────

def _soft_eval(x: float) -> float:
    """
    Compress raw engine eval (stored as ±10, where ±10 = forced mate) into a
    smooth display value using a tanh curve.

    Properties:
      - Nearly linear for |x| ≤ 2 pawns  → normal play looks proportional
      - Soft-clips large advantages:
          +3 pawns  → ≈ +2.7   (still clearly winning, not crammed at the top)
          +5 pawns  → ≈ +3.8
          ±10 (mate) → ≈ ±4.8  (near the top but not jammed against the edge)
    """
    return math.tanh(x / 5.0) * 5.0


def eval_graph_panel(moves: list[dict], current_idx: int) -> int | None:
    """
    Chess.com-style evaluation graph.

    - Y-axis uses tanh soft-compression so mate appears near ±5 rather than
      shooting to ±10, keeping normal position swings easy to read.
    - Y-axis range is dynamic: zooms to the game's actual content
      (e.g. a quiet game that stays within ±2 uses a ±2-ish scale).
    - White-advantage region shaded light; Black-advantage dark.
    - Blunders: large red circle.  Mistakes: medium orange circle.
    - Dashed blue line + dot marks the current position.
    - Clicking a point returns that move index.  Returns None if no click.
    """
    n = len(moves)
    x = list(range(n))

    y_raw = [m["eval_after"] for m in moves]
    y     = [_soft_eval(v) for v in y_raw]   # compressed display values

    # Dynamic Y range: pad 15% above the game's max swing, minimum ±1.5
    max_display = max((abs(v) for v in y), default=1.5)
    y_pad       = max(1.5, max_display * 1.15)

    # Hover shows the real (uncompressed) eval so numbers match the eval bar
    hover = []
    for m in moves:
        mn  = m["move_number"]
        dot = "." if m["color"] == "white" else "..."
        hover.append(
            f"<b>{mn}{dot}{m['move_san']}</b><br>"
            f"Eval: {m['eval_after']:+.2f}<br>"
            f"{m['classification'].upper()}"
        )

    # X-axis: move number every 5 full moves, White's turn only
    x_tickvals = [i for i in range(n)
                  if moves[i]["color"] == "white" and moves[i]["move_number"] % 5 == 0]
    x_ticktext = [str(moves[i]["move_number"]) for i in x_tickvals]

    # Y-axis: show only the outermost label that fits (plus zero).
    # Keeps the axis clean — just "-M / 0 / M" or "-3 / 0 / +3", etc.
    _TICK_RAWS = [1, 2, 3, 5, 10]   # 10 = mate sentinel
    extreme_rt, extreme_d = None, None
    for rt in _TICK_RAWS:
        d = _soft_eval(float(rt))
        if d > y_pad * 0.96:
            break
        extreme_rt, extreme_d = rt, d

    y_tickvals: list[float] = [0.0]
    y_ticktext: list[str]   = ["0"]
    if extreme_rt is not None:
        neg_lbl = "-M" if extreme_rt == 10 else f"-{extreme_rt}"
        pos_lbl = "M"  if extreme_rt == 10 else f"+{extreme_rt}"
        y_tickvals.extend([-extreme_d, extreme_d])
        y_ticktext.extend([neg_lbl, pos_lbl])

    fig = go.Figure()

    # ── Fill: White advantage (above 0) ──────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x, y=[max(0.0, v) for v in y],
        fill="tozeroy",
        fillcolor="rgba(210, 210, 210, 0.18)",
        line=dict(width=0),
        hoverinfo="skip",
        showlegend=False,
    ))

    # ── Fill: Black advantage (below 0) ──────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x, y=[min(0.0, v) for v in y],
        fill="tozeroy",
        fillcolor="rgba(35, 35, 50, 0.65)",
        line=dict(width=0),
        hoverinfo="skip",
        showlegend=False,
    ))

    # ── Main eval line ────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=x, y=y,
        mode="lines",
        line=dict(color="rgba(200, 200, 200, 0.85)", width=1.5),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover,
        showlegend=False,
        name="eval",
    ))

    # ── Blunder markers ───────────────────────────────────────────────────────
    blunder_x = [i for i, m in enumerate(moves) if m["classification"] == "blunder"]
    if blunder_x:
        fig.add_trace(go.Scatter(
            x=blunder_x,
            y=[y[i] for i in blunder_x],
            mode="markers",
            marker=dict(
                size=13, color="#e57373",
                symbol="circle",
                line=dict(color="#ffffff", width=1.5),
            ),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=[hover[i] for i in blunder_x],
            showlegend=False,
            name="Blunder ??",
        ))

    # ── Mistake markers ───────────────────────────────────────────────────────
    mistake_x = [i for i, m in enumerate(moves) if m["classification"] == "mistake"]
    if mistake_x:
        fig.add_trace(go.Scatter(
            x=mistake_x,
            y=[y[i] for i in mistake_x],
            mode="markers",
            marker=dict(
                size=9, color="#ffb74d",
                symbol="circle",
                line=dict(color="#ffffff", width=1.2),
            ),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=[hover[i] for i in mistake_x],
            showlegend=False,
            name="Mistake ?",
        ))

    # ── Current-move indicator ────────────────────────────────────────────────
    fig.add_vline(
        x=current_idx,
        line=dict(color="#4fc3f7", width=1.5, dash="dash"),
    )
    fig.add_trace(go.Scatter(
        x=[current_idx], y=[y[current_idx]],
        mode="markers",
        marker=dict(size=7, color="#4fc3f7", symbol="circle"),
        hoverinfo="skip",
        showlegend=False,
    ))

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#1a1a2e",
        margin=dict(l=44, r=12, t=8, b=36),
        height=170,
        xaxis=dict(
            range=[-0.5, n - 0.5],
            tickmode="array",
            tickvals=x_tickvals,
            ticktext=x_ticktext,
            tickfont=dict(color="#888", size=11),
            gridcolor="rgba(255,255,255,0.05)",
            showgrid=True,
            zeroline=False,
            title=dict(text="Move", font=dict(color="#666", size=11)),
        ),
        yaxis=dict(
            range=[-y_pad, y_pad],
            tickmode="array",
            tickvals=y_tickvals,
            ticktext=y_ticktext,
            tickfont=dict(color="#888", size=11),
            gridcolor="rgba(255,255,255,0.05)",
            showgrid=True,
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.25)",
            zerolinewidth=1,
        ),
        hovermode="closest",
        dragmode=False,
    )

    # Render the chart and capture click events (Streamlit >= 1.33)
    clicked_idx = None
    try:
        event = st.plotly_chart(
            fig, use_container_width=True,
            on_select="rerun", key="eval_graph",
        )
        pts = getattr(event, "selection", {})
        if pts and pts.get("points"):
            raw_x = pts["points"][0].get("x")
            if raw_x is not None:
                clicked_idx = int(round(raw_x))
                clicked_idx = max(0, min(n - 1, clicked_idx))
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, key="eval_graph")

    st.markdown(
        '<p style="text-align:center;font-size:0.72em;color:#3a5070;margin-top:2px;">'
        'Click any point to jump to that move</p>',
        unsafe_allow_html=True,
    )

    return clicked_idx


# ── Tab: Game Review ─────────────────────────────────────────────────────────

def game_overview_panel(moves: list[dict], headers: dict):
    white   = headers.get("White", "White")
    black   = headers.get("Black", "Black")
    result  = headers.get("Result", "*")
    opening = get_opening_name(headers)

    non_book = [m for m in moves if m["classification"] != "book"]

    def count(color, cls):
        return sum(1 for m in non_book if m["color"] == color and m["classification"] == cls)

    w_acc = compute_accuracy(moves, "white")
    b_acc = compute_accuracy(moves, "black")
    w_brl = count("white", "brilliant")
    b_brl = count("black", "brilliant")
    w_bln = count("white", "blunder")
    b_bln = count("black", "blunder")
    w_mis = count("white", "mistake")
    b_mis = count("black", "mistake")
    w_ina = count("white", "inaccuracy")
    b_ina = count("black", "inaccuracy")

    def _cells(w_val, b_val, lower_is_better=False):
        """
        Return (white_td_style, black_td_style).
        The better player's cell is bright + bold; the other is dimmed.
        """
        w_wins = (w_val < b_val) if lower_is_better else (w_val > b_val)
        b_wins = (b_val < w_val) if lower_is_better else (b_val > w_val)
        WIN  = "color:#a5d6a7;font-weight:700;"   # soft green, bold
        LOSE = "color:#6a6a7a;font-weight:400;"   # dim but readable
        TIE  = "color:#aaa;font-weight:500;"      # neutral
        if w_val == b_val:
            return TIE, TIE
        return (WIN if w_wins else LOSE), (WIN if b_wins else LOSE)

    def trunc(name, n=20):
        return name if len(name) <= n else name[:n - 1] + "…"

    acc_ws,  acc_bs  = _cells(w_acc, b_acc, lower_is_better=False)
    brl_ws,  brl_bs  = _cells(w_brl, b_brl, lower_is_better=False)  # more brilliants = better
    bln_ws,  bln_bs  = _cells(w_bln, b_bln, lower_is_better=True)
    mis_ws,  mis_bs  = _cells(w_mis, b_mis, lower_is_better=True)
    ina_ws,  ina_bs  = _cells(w_ina, b_ina, lower_is_better=True)

    rows = [
        ("Accuracy",     f"{w_acc}%", acc_ws, f"{b_acc}%", acc_bs),
        ("Brilliants !!",str(w_brl),  brl_ws, str(b_brl),  brl_bs),
        ("Blunders ??",  str(w_bln),  bln_ws, str(b_bln),  bln_bs),
        ("Mistakes ?",   str(w_mis),  mis_ws, str(b_mis),  mis_bs),
        ("Inaccuracies", str(w_ina),  ina_ws, str(b_ina),  ina_bs),
    ]

    tbody = ""
    for i, (label, wv, ws, bv, bs) in enumerate(rows):
        row_bg = "background:#111320;" if i % 2 == 0 else "background:#0e1117;"
        tbody += (
            f'<tr style="{row_bg}">'
            f'<td style="padding:9px 16px;color:#bbb;font-size:0.8em;'
            f'font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">{label}</td>'
            f'<td style="text-align:center;padding:9px 16px;font-size:0.95em;{ws}">{wv}</td>'
            f'<td style="text-align:center;padding:9px 16px;font-size:0.95em;{bs}">{bv}</td>'
            f'</tr>'
        )

    w_name = trunc(white)
    b_name = trunc(black)

    html = f"""
<div style="border-radius:10px;overflow:hidden;border:1px solid #1e1e2e;margin-bottom:4px;">
  <table style="width:100%;border-collapse:collapse;">
    <thead>
      <tr style="background:#161625;border-bottom:1px solid #1e1e2e;">
        <th style="text-align:left;padding:10px 16px;color:#888;font-size:0.75em;
                   font-weight:600;letter-spacing:0.06em;width:36%;">METRIC</th>
        <th style="text-align:center;padding:10px 16px;color:#e8e8e8;
                   font-weight:700;font-size:0.95em;width:32%;">⬜ {w_name}</th>
        <th style="text-align:center;padding:10px 16px;color:#ccc;
                   font-weight:700;font-size:0.95em;width:32%;">⬛ {b_name}</th>
      </tr>
    </thead>
    <tbody>{tbody}</tbody>
  </table>
</div>"""

    if opening:
        html += (
            f'<div style="color:#7a9ab0;font-size:0.78em;margin-bottom:2px;'
            f'padding-left:2px;">📖 {opening}</div>'
        )

    # Result badge sits to the right of the table header area — render above
    st.markdown(
        f'<div style="text-align:right;margin-bottom:4px;">'
        f'<span style="background:#1e1e2e;border:1px solid #2a2a3e;border-radius:4px;'
        f'padding:2px 10px;font-size:0.8em;color:#ccc;font-weight:600;">{result}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(html, unsafe_allow_html=True)


def move_commentary_panel(move: dict, all_moves: list[dict], idx: int):
    cls       = move["classification"]
    color_hex = COLORS.get(cls, "#fff")
    symbol    = SYMBOLS.get(cls, "")
    mn        = move["move_number"]
    prefix    = f"{mn}." if move["color"] == "white" else f"{mn}..."

    st.markdown(
        f'<div style="border-left:4px solid {color_hex};padding:6px 10px;'
        f'background:#1a1a2e;border-radius:0 6px 6px 0;margin-bottom:6px;">'
        f'<span style="font-size:1.1em;font-weight:700;">{prefix}{move["move_san"]}</span>'
        f'&nbsp;&nbsp;{classification_badge(cls)}<br>'
        f'<span style="color:#ccc;font-size:0.85em;">'
        f'Eval: {move["eval_before"]:+.2f} → {move["eval_after"]:+.2f}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if cls != "book" and move.get("best_move_san") and move["best_move_san"] != move["move_san"]:
        st.markdown(
            f'<div style="font-size:0.85em;color:#90caf9;margin-bottom:4px;">'
            f'Best move: <b>{move["best_move_san"]}</b></div>',
            unsafe_allow_html=True,
        )

    # Concepts are generated for notable classifications only
    _CONCEPT_CLS = {"blunder", "mistake", "brilliant", "best"}

    key_exp = f"explanation_{mn}_{move['color']}"
    if st.button("Ask Tutor", key=f"ask_{mn}_{move['color']}", use_container_width=True):
      if not _api_limit_reached():
        _count_api_call()
        with st.spinner("Asking Claude... (computing engine lines)"):
            followup_data = get_followup_lines(move["fen_after"], n_plies=4)
            followup_text = _format_followup(
                followup_data.get("moves", []), followup_data.get("evals", [])
            )

            best_followup_text = ""
            cls = move.get("classification", "")
            if cls in ("blunder", "mistake", "inaccuracy") and move.get("best_move_uci"):
                try:
                    best_board = chess.Board(move["fen_before"])
                    best_board.push(chess.Move.from_uci(move["best_move_uci"]))
                    best_data = get_followup_lines(best_board.fen(), n_plies=4)
                    best_followup_text = _format_followup(
                        best_data.get("moves", []), best_data.get("evals", [])
                    )
                except Exception:
                    pass

            game_phase = (
                "opening"    if mn <= 12  else
                "endgame"    if mn >= 36  else
                "middlegame"
            )
            history = [m["move_san"] for m in all_moves[:idx]]

            st.session_state[key_exp] = explain_move(
                move["fen_before"], move["move_san"],
                f"{move['eval_after']:+.2f}", history,
                classification=cls,
                best_move_san=move.get("best_move_san", ""),
                followup_text=followup_text,
                best_followup_text=best_followup_text,
                eval_before=move.get("eval_before", 0.0),
                eval_after=move.get("eval_after", 0.0),
                color=move.get("color", ""),
                game_phase=game_phase,
                generate_concepts=(cls in _CONCEPT_CLS),
                top_candidates=move.get("top_candidates"),
            )

    if key_exp in st.session_state:
        result = st.session_state[key_exp]
        if isinstance(result, dict):
            insights = result.get("insights", [])
            # Fallback: old format had "explanation" string
            if not insights and result.get("explanation"):
                insights = [{"label": "Analysis", "text": result["explanation"]}]
            concepts = result.get("concepts", [])
        else:
            insights = [{"label": "Analysis", "text": str(result)}]
            concepts = []

        # Render labeled insight cards
        _INSIGHT_ICONS = {
            "Board Effect":    "♟",
            "Immediate Threat":"⚡",
            "Engine Line":     "🔢",
            "Why It Loses":    "💀",
            "Better Path":     "↑",
            "Subtle Cost":     "〰",
            "The Brilliancy":  "✦",
            "Why It's Best":   "✓",
            "The Idea":        "→",
            "Opening Idea":    "📖",
            "Chess Principle": "◆",
            "Key Lesson":      "◆",
            "Analysis":        "◆",
        }
        cards_html = ""
        for ins in insights:
            lbl  = ins.get("label", "")
            txt  = ins.get("text", "")
            icon = _INSIGHT_ICONS.get(lbl, "•")
            cards_html += (
                f'<div style="margin-bottom:8px;padding:10px 14px;'
                f'background:#1a2535;border-left:3px solid #4a7fa5;border-radius:4px;">'
                f'<div style="font-size:0.72em;font-weight:700;letter-spacing:0.04em;'
                f'color:#7ab3d4;text-transform:uppercase;margin-bottom:4px;">'
                f'{icon} {lbl}</div>'
                f'<div style="font-size:0.88em;color:#d0e4f0;line-height:1.55;">{txt}</div>'
                f'</div>'
            )
        st.markdown(cards_html, unsafe_allow_html=True)

        # Concept chips
        if concepts:
            # Persist concepts for the coaching tab
            if "coaching_concepts" not in st.session_state:
                st.session_state.coaching_concepts = {}
            for c in concepts:
                if c not in st.session_state.coaching_concepts:
                    st.session_state.coaching_concepts[c] = []
                entry = {
                    "move_number":    mn,
                    "color":          move["color"],
                    "move_san":       move["move_san"],
                    "classification": move["classification"],
                }
                if entry not in st.session_state.coaching_concepts[c]:
                    st.session_state.coaching_concepts[c].append(entry)
                    # Persist to DB if we have a game_id
                    game_id = st.session_state.get("current_game_id")
                    if game_id is not None:
                        db.save_concept(
                            game_id, c, mn,
                            move["color"], move["move_san"], move["classification"],
                        )

            # Render chips as clickable buttons → navigate to Coaching tab
            st.markdown('<div style="margin-top:6px;"></div>', unsafe_allow_html=True)
            chip_cols = st.columns(len(concepts))
            for j, c in enumerate(concepts):
                with chip_cols[j]:
                    if st.button(
                        c,
                        key=f"concept_btn_{mn}_{move['color']}_{j}",
                        use_container_width=True,
                    ):
                        st.session_state.selected_concept = c
                        st.session_state.navigate_to_coaching = True
                        st.rerun()


def move_list_panel(moves: list[dict], current_idx: int):
    # Build white/black pairs
    pairs = []
    i = 0
    while i < len(moves):
        wm = moves[i] if moves[i]["color"] == "white" else None
        bm = moves[i + 1] if wm and i + 1 < len(moves) else None
        if wm is None:
            i += 1
            continue
        pairs.append((wm, bm, i))
        i += 2 if bm else 1

    def btn_label(m):
        sym = SYMBOLS.get(m["classification"], "")
        return f"{m['move_san']} {sym}".strip() if sym else m["move_san"]

    def _num(col, n):
        with col:
            st.markdown(
                f'<p style="color:#555;font-size:0.72em;font-family:monospace;'
                f'margin:0;padding:5px 1px 0 0;text-align:right;">{n}.</p>',
                unsafe_allow_html=True,
            )

    def _btn(col, m, midx):
        with col:
            if st.button(
                btn_label(m),
                key=f"ml_{m['color'][0]}_{m['move_number']}",
                use_container_width=True,
                type="primary" if midx == current_idx else "secondary",
            ):
                st.session_state.current_move_idx = midx
                st.rerun()

    # ── Clickable move grid: [ num | W | B | num | W | B ] (2 full moves/row) ─
    for pi in range(0, len(pairs), 2):
        p1 = pairs[pi]
        p2 = pairs[pi + 1] if pi + 1 < len(pairs) else None
        wm1, bm1, wi1 = p1

        c = st.columns([0.3, 1.55, 1.55, 0.3, 1.55, 1.55])
        _num(c[0], wm1["move_number"])
        _btn(c[1], wm1, wi1)
        if bm1:
            _btn(c[2], bm1, wi1 + 1)

        if p2:
            wm2, bm2, wi2 = p2
            _num(c[3], wm2["move_number"])
            _btn(c[4], wm2, wi2)
            if bm2:
                _btn(c[5], bm2, wi2 + 1)

    # ── Jump to notable moves (quick-scan shortcut) ───────────────────────────
    notable = [
        m for m in moves
        if m["classification"] in ("brilliant", "blunder", "mistake", "inaccuracy")
    ]
    if notable:
        cls_present = {m["classification"] for m in notable}
        legend_order = ["brilliant", "blunder", "mistake", "inaccuracy"]
        legend_parts = [
            f'<span style="color:{COLORS[c]};font-weight:600;">'
            f'{SYMBOLS.get(c, "")} {c}</span>'
            for c in legend_order if c in cls_present
        ]
        st.markdown(
            '<div style="font-size:0.75em;color:#7a9ab0;margin:8px 0 4px;">'
            'Jump to: &nbsp;' + ' &nbsp;·&nbsp; '.join(legend_parts) + '</div>',
            unsafe_allow_html=True,
        )
        n_cols = min(len(notable), 8)
        jcols = st.columns(n_cols)
        for k, nm in enumerate(notable):
            dot = "." if nm["color"] == "white" else "…"
            lbl = f"{nm['move_number']}{dot}{nm['move_san']} {SYMBOLS.get(nm['classification'], '')}"
            with jcols[k % n_cols]:
                if st.button(lbl, key=f"jump_{nm['move_number']}_{nm['color']}",
                             use_container_width=True):
                    st.session_state.current_move_idx = moves.index(nm)
                    st.rerun()


def ai_review_panel(moves: list[dict], headers: dict):
    if "game_review" not in st.session_state:
        st.button(
            "Generate Full Game Review (Claude AI)",
            type="primary",
            on_click=lambda: _run_review(moves, headers),
            use_container_width=True,
        )
        return

    review = st.session_state.game_review
    t1, t2, t3, t4, t5 = st.tabs(
        ["Summary", "Key Moments", "Missed Tactics", "Positional Themes", "Tips to Learn"]
    )
    with t1:
        st.write(review.get("summary", "—"))
    with t2:
        for item in review.get("key_moments", []):
            st.markdown(f"- {item}")
    with t3:
        for item in review.get("missed_tactics", []):
            st.markdown(f"- {item}")
    with t4:
        for item in review.get("positional_themes", []):
            st.markdown(f"- {item}")
    with t5:
        for i, tip in enumerate(review.get("tips_to_learn", []), 1):
            st.markdown(f"**{i}.** {tip}")


def _run_review(moves, headers):
    if _api_limit_reached():
        return
    _count_api_call()
    with st.spinner("Claude is reviewing the game..."):
        st.session_state.game_review = full_game_review(moves, headers)


def parse_all_games(pgn_text: str) -> list[tuple[dict, str]]:
    """
    Parse every game in a PGN string.
    Returns a list of (headers_dict, pgn_string) tuples, one per game.
    """
    games = []
    pgn_io = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        games.append((dict(game.headers), game.accept(exporter)))
    return games


def _game_label(idx: int, headers: dict) -> str:
    white  = headers.get("White", "?")
    black  = headers.get("Black", "?")
    result = headers.get("Result", "*")
    date   = headers.get("Date", "")[:10].replace(".", "-")
    label  = f"Game {idx + 1}: {white} vs {black}  ({result})"
    if date and date != "???":
        label += f"  —  {date}"
    return label


def inject_keyboard_nav():
    """
    Inject a JS listener so ← / → arrow keys click the Prev / Next buttons.
    Uses a guard flag to avoid duplicate listeners across Streamlit rerenders.
    Skips the event when focus is on an input element.
    """
    components.html("""
    <script>
    (function() {
        if (window.parent._chessNavKey) return;
        window.parent._chessNavKey = true;
        window.parent.document.addEventListener('keydown', function(e) {
            const active = window.parent.document.activeElement;
            if (active && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName)) return;
            if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
            e.preventDefault();
            const label = e.key === 'ArrowRight' ? '\u25ba' : '\u25c4';   // ▶ / ◀
            const btns = window.parent.document.querySelectorAll('button');
            for (const btn of btns) {
                if (btn.innerText.trim() === label && !btn.disabled) {
                    btn.click();
                    return;
                }
            }
        });
    })();
    </script>
    """, height=0)


def _check_achievement(key: str):
    """Check and unlock an achievement, showing a toast if newly unlocked."""
    if key not in _ACHIEVEMENTS:
        return
    if db.unlock_achievement(key):
        ach = _ACHIEVEMENTS[key]
        st.toast(f"{ach['icon']} Achievement Unlocked: {ach['name']}")


def _check_puzzle_achievements():
    """Check puzzle-related achievements after a puzzle solve."""
    ps = db.get_puzzle_stats()
    if ps["solved"] >= 1:
        _check_achievement("first_puzzle")
    if ps["solved"] >= 25:
        _check_achievement("puzzles_25")
    if ps["solved"] >= 100:
        _check_achievement("puzzles_100")
    if ps["streak"] >= 5:
        _check_achievement("streak_5")
    if ps["streak"] >= 10:
        _check_achievement("streak_10")


def _get_daily_goals() -> tuple[dict, dict]:
    """Get or create today's daily goals. Returns (targets, progress)."""
    from datetime import date as _dg_date
    today = _dg_date.today().isoformat()
    goals = db.get_daily_goals(today)
    if goals:
        return goals["targets"], goals["progress"]
    targets = {"puzzles": 5, "lessons": 1, "review": 1}
    progress = {"puzzles": 0, "lessons": 0, "review": 0}
    db.save_daily_goals(today, targets, progress)
    return targets, progress


def _increment_daily_goal(key: str, n: int = 1):
    """Increment a daily goal counter (thread-safe via DB-level locking)."""
    import json as _dg_json
    from datetime import date as _dg_date
    today = _dg_date.today().isoformat()
    # Use a single atomic DB operation to read-modify-write
    with db._connect() as conn:
        row = conn.execute(
            "SELECT targets_json, progress_json FROM daily_goals WHERE date=?",
            (today,),
        ).fetchone()
        if not row:
            targets = {"puzzles": 5, "lessons": 1, "review": 1}
            progress = {"puzzles": 0, "lessons": 0, "review": 0}
        else:
            targets = _dg_json.loads(row["targets_json"] or "{}")
            progress = _dg_json.loads(row["progress_json"] or "{}")
        progress[key] = progress.get(key, 0) + n
        conn.execute(
            "INSERT INTO daily_goals (date, targets_json, progress_json) "
            "VALUES (?, ?, ?) ON CONFLICT(date) DO UPDATE SET "
            "progress_json=excluded.progress_json",
            (today, _dg_json.dumps(targets), _dg_json.dumps(progress)),
        )


def _query_tablebase(fen: str) -> dict | None:
    """Query Lichess Syzygy tablebase for positions with ≤7 pieces. Returns dict or None."""
    import requests as _tb_requests
    # Count pieces in FEN
    board_part = fen.split()[0]
    piece_count = sum(1 for c in board_part if c.isalpha())
    if piece_count > 7:
        return None
    # Check session cache
    _tb_cache = st.session_state.setdefault("_tb_cache", {})
    if fen in _tb_cache:
        return _tb_cache[fen]
    try:
        resp = _tb_requests.get(
            f"https://tablebase.lichess.ovh/standard?fen={fen.replace(' ', '_')}",
            timeout=3,
        )
        if resp.status_code != 200:
            _tb_cache[fen] = None
            return None
        data = resp.json()
        _cat = data.get("category", "")
        if _cat in ("win", "cursed-win", "maybe-win"):
            _wdl_val = 1
        elif _cat in ("loss", "blessed-loss", "maybe-loss"):
            _wdl_val = -1
        else:
            _wdl_val = 0
        result = {
            "wdl": _wdl_val,
            "category": _cat,
            "dtm": data.get("dtm"),
            "best_move": None,
        }
        # Parse best move
        moves = data.get("moves", [])
        if moves:
            uci = moves[0].get("uci", "")
            san = moves[0].get("san", "")
            result["best_move"] = san or uci or None
        _tb_cache[fen] = result
        return result
    except Exception:
        _tb_cache[fen] = None
        return None


def inject_puzzle_keyboard():
    """Inject JS listener for puzzle keyboard shortcuts: Space, H, S."""
    components.html("""
    <script>
    (function() {
        if (window.parent._chessPuzKey) return;
        window.parent._chessPuzKey = true;
        window.parent.document.addEventListener('keydown', function(e) {
            var active = window.parent.document.activeElement;
            if (active && (['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName)
                || active.isContentEditable)) return;
            var key = e.key.toLowerCase();
            if (key !== ' ' && key !== 'h' && key !== 's') return;
            /* Only act when Puzzles tab is active */
            var tabs = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
            var puzzlesActive = false;
            for (var t = 0; t < tabs.length; t++) {
                if (tabs[t].getAttribute('aria-selected') === 'true' &&
                    tabs[t].innerText.indexOf('Puzzle') >= 0) {
                    puzzlesActive = true; break;
                }
            }
            if (!puzzlesActive) return;
            var btns = window.parent.document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var txt = btns[i].innerText.trim();
                if (key === ' ') {
                    if ((txt.indexOf('Next Puzzle') >= 0 || txt.indexOf('Check') >= 0)
                        && !btns[i].disabled) {
                        e.preventDefault(); btns[i].click(); return;
                    }
                } else if (key === 'h') {
                    if ((txt.indexOf('Hint') >= 0) && !btns[i].disabled) {
                        e.preventDefault(); btns[i].click(); return;
                    }
                } else if (key === 's') {
                    if ((txt === 'Skip' || txt.indexOf('\u23ed Skip') >= 0 || txt.indexOf('\u23ed') >= 0)
                        && !btns[i].disabled) {
                        e.preventDefault(); btns[i].click(); return;
                    }
                }
            }
        });
    })();
    </script>
    """, height=0)


def _build_annotated_pgn(headers: dict, moves: list[dict]) -> str:
    """Build a PGN string with engine classification comments after each move."""
    lines = []
    for tag in ("Event", "Site", "Date", "White", "Black", "Result"):
        val = headers.get(tag, "?").replace('"', '\\"')
        lines.append(f'[{tag} "{val}"]')
    lines.append("")
    tokens: list[str] = []
    for m in moves:
        if m["color"] == "white":
            tokens.append(f'{m["move_number"]}.')
        san = m["move_san"]
        cls = m["classification"]
        if cls not in ("good", "book", "best", "brilliant"):
            comment = f"{cls} -- eval: {m['eval_after']:+.2f}"
            tokens.append(f"{san} {{{comment}}}")
        else:
            tokens.append(san)
    result = headers.get("Result", "*")
    tokens.append(result)
    # Wrap at ~80 chars
    current = ""
    pgn_lines: list[str] = []
    for tok in tokens:
        if current and len(current) + len(tok) + 1 > 80:
            pgn_lines.append(current)
            current = tok
        else:
            current = f"{current} {tok}".strip()
    if current:
        pgn_lines.append(current)
    lines.extend(pgn_lines)
    return "\n".join(lines) + "\n"


def render_game_review_tab():
    # ── Back-to-profile banner (shown after a Deep Dive from profile) ─────────
    if st.session_state.get("from_profile_dive"):
        bb_col, info_col = st.columns([1, 7])
        with bb_col:
            if st.button("← Profile", key="back_to_profile", help="Return to Chess.com profile"):
                st.session_state.from_profile_dive = False
                st.session_state.navigate_to_profile = True
                st.rerun()
        with info_col:
            st.markdown(
                '<div style="padding-top:6px;font-size:0.82em;color:#7a9ab0;">'
                f'🔍 <b>Deep Dive</b> — full depth-{st.session_state.get("review_depth", 18)} Stockfish + per-move Claude coaching</div>',
                unsafe_allow_html=True,
            )
        st.markdown("---")

    # ── Source + depth selectors ──────────────────────────────────────────────
    src_col, depth_col = st.columns([5, 2])
    with src_col:
        source = st.radio(
            "Source", ["Upload PGN", "Chess.com", "Lichess"],
            horizontal=True, label_visibility="collapsed", key="game_source",
        )
    with depth_col:
        _depth_options = {18: "d18 · Quick (~45s)", 20: "d20 · Standard (~3m)", 22: "d22 · Deep (~8m)"}
        review_depth = st.selectbox(
            "Analysis depth",
            options=list(_depth_options.keys()),
            format_func=lambda d: _depth_options[d],
            index=0,
            key="review_depth",
        )

    selected_pgn: str | None      = None
    selected_headers: dict | None = None
    file_key: str | None          = None

    if source == "Upload PGN":
        # ── File upload ───────────────────────────────────────────────────────
        uploaded = st.file_uploader("Upload a PGN file (Chess.com or Lichess)", type=["pgn"])
        if uploaded is None:
            st.markdown(
                '<div style="background:#111827;border:1px solid #1e2e3e;border-radius:12px;'
                'padding:20px 18px;text-align:center;margin-top:24px;">'
                '<div style="font-size:2.2em;margin-bottom:12px;">♟</div>'
                '<div style="font-size:1.1em;font-weight:700;color:#cce0f4;margin-bottom:8px;">'
                'Upload a PGN to begin</div>'
                '<div style="font-size:0.88em;color:#7a9ab0;max-width:420px;margin:0 auto;">'
                'Export any game from <b style="color:#a0bcd4;">Chess.com</b> or '
                '<b style="color:#a0bcd4;">Lichess</b> as a .pgn file, then drop it above. '
                'Stockfish will analyse every move at depth 18–22 and Claude will coach '
                'you on each critical moment.</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            return

        raw_bytes = uploaded.read()
        try:
            pgn_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            pgn_text = raw_bytes.decode("latin-1")

        # ── Multi-game picker ─────────────────────────────────────────────────
        all_games = parse_all_games(pgn_text)
        if not all_games:
            st.error("No games found in this file.")
            return

        if len(all_games) > 1:
            labels = [_game_label(i, h) for i, (h, _) in enumerate(all_games)]
            selected_idx = st.selectbox(
                f"This file contains {len(all_games)} games — select one to analyze:",
                range(len(all_games)),
                format_func=lambda i: labels[i],
            )
        else:
            selected_idx = 0

        selected_headers, selected_pgn = all_games[selected_idx]
        file_key = uploaded.name + str(len(pgn_text)) + str(selected_idx) + f"_d{review_depth}"

    elif source == "Chess.com":
        # ── Chess.com fetch ───────────────────────────────────────────────────
        cc_col1, cc_col2, cc_col3 = st.columns([2, 1, 1])
        with cc_col1:
            username = st.text_input(
                "Chess.com username", value="", key="cc_username",
                placeholder="Enter username",
            )
        with cc_col2:
            n_months = st.number_input(
                "Months to fetch", min_value=1, max_value=6, value=1, key="cc_months"
            )
        with cc_col3:
            st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
            if st.button("Fetch Games", use_container_width=True, key="cc_fetch"):
                with st.spinner(f"Fetching games for {username}..."):
                    try:
                        fetched = chesscom.fetch_recent_games(username, int(n_months))
                        st.session_state.chesscom_games    = fetched
                        st.session_state.chesscom_username = username
                    except Exception as e:
                        st.error(f"Failed to fetch games from Chess.com: {e}")
                        return

        fetched_games = st.session_state.get("chesscom_games", [])
        cc_user       = st.session_state.get("chesscom_username", "")

        if not fetched_games:
            st.info(
                "Enter a username and click **Fetch Games** to load recent games."
            )
            return

        cc_labels = [_game_label(i, g["headers"]) for i, g in enumerate(fetched_games)]
        sel_idx   = st.selectbox(
            f"{len(fetched_games)} games fetched for **{cc_user}** — select one to analyze:",
            range(len(fetched_games)),
            format_func=lambda i: cc_labels[i],
        )
        selected_pgn     = fetched_games[sel_idx]["pgn"]
        selected_headers = fetched_games[sel_idx]["headers"]
        file_key         = f"chesscom_{cc_user}_{sel_idx}_{len(fetched_games)}_d{review_depth}"

    else:
        # ── Lichess fetch ────────────────────────────────────────────────────
        li_col1, li_col2, li_col3 = st.columns([2, 1, 1])
        with li_col1:
            li_username = st.text_input(
                "Lichess username", value="", key="li_username"
            )
        with li_col2:
            li_months = st.number_input(
                "Months to fetch", min_value=1, max_value=6, value=1, key="li_months"
            )
        with li_col3:
            st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
            if st.button("Fetch Games", use_container_width=True, key="li_fetch"):
                if not li_username.strip():
                    st.error("Please enter a Lichess username.")
                    return
                with st.spinner(f"Fetching games for {li_username}..."):
                    try:
                        fetched = lichess.fetch_recent_games(li_username.strip(), int(li_months))
                        st.session_state.lichess_games    = fetched
                        st.session_state.lichess_username = li_username.strip()
                    except Exception as e:
                        st.error(f"Failed to fetch games from Lichess: {e}")
                        return

        fetched_games = st.session_state.get("lichess_games", [])
        li_user       = st.session_state.get("lichess_username", "")

        if not fetched_games:
            st.info("Enter a Lichess username and click **Fetch Games** to load recent games.")
            return

        li_labels = [_game_label(i, g["headers"]) for i, g in enumerate(fetched_games)]
        sel_idx   = st.selectbox(
            f"{len(fetched_games)} games fetched for **{li_user}** — select one to analyze:",
            range(len(fetched_games)),
            format_func=lambda i: li_labels[i],
        )
        selected_pgn     = fetched_games[sel_idx]["pgn"]
        selected_headers = fetched_games[sel_idx]["headers"]
        file_key         = f"lichess_{li_user}_{sel_idx}_{len(fetched_games)}_d{review_depth}"

    if selected_pgn is None:
        return

    if st.session_state.get("loaded_file") != file_key:
        st.session_state.loaded_file       = file_key
        st.session_state.current_move_idx  = 0
        for k in ("moves", "headers", "game_review", "coaching_concepts", "current_game_id"):
            st.session_state.pop(k, None)

    if "moves" not in st.session_state:
        # ── Animated loading screen ──────────────────────────────────────────
        white  = selected_headers.get("White", "White")
        black  = selected_headers.get("Black", "Black")
        result = selected_headers.get("Result", "*")

        st.markdown(
            f'<h3 style="text-align:center;margin-bottom:2px;color:#cce0f4;">Analyzing game</h3>'
            f'<p style="text-align:center;color:#ccc;margin-top:0;">'
            f'⬜ {white} vs ⬛ {black} &nbsp;·&nbsp; {result}</p>',
            unsafe_allow_html=True,
        )

        board_slot    = st.empty()
        progress_slot = st.progress(0.0, text="Starting analysis...")

        orientation = st.session_state.get("board_orientation", chess.WHITE)
        result_moves, result_headers = None, None

        try:
            for update in analyze_game_iter(selected_pgn, depth=review_depth):
                if update[0] == "progress":
                    _, fen, last_uci, done, total, eval_val, best_uci = update
                    # UI updates are best-effort — a Streamlit API hiccup must
                    # not abort the Stockfish analysis.
                    try:
                        board_slot.markdown(
                            f'<div style="display:flex;justify-content:center;">'
                            + render_board_with_eval(
                                fen,
                                eval_val=eval_val,
                                last_move_uci=last_uci,
                                orientation=orientation,
                                board_size=380,
                                best_move_uci=best_uci,
                            )
                            + '</div>',
                            unsafe_allow_html=True,
                        )
                        progress_slot.progress(
                            min(1.0, done / total),
                            text=f"Stockfish: position {done} of {total}",
                        )
                    except Exception:
                        pass  # non-critical display update; keep analysing
                else:
                    _, result_moves, result_headers = update
        except Exception as e:
            tb = traceback.format_exc()
            st.error(f"Analysis failed: {type(e).__name__}: {e}")
            with st.expander("Full error details (for debugging)"):
                st.code(tb, language="text")
            return

        board_slot.empty()
        progress_slot.empty()

        st.session_state.moves   = result_moves
        st.session_state.headers = result_headers

        # Persist to DB (deduped — returns None if game already saved)
        if result_moves:
            w_acc = compute_accuracy(result_moves, "white")
            b_acc = compute_accuracy(result_moves, "black")
            game_id = db.save_game(
                selected_pgn, result_headers, result_moves, w_acc, b_acc,
            )
            st.session_state.current_game_id = game_id
            _check_achievement("first_review")
            _increment_daily_goal("review")

        st.rerun()

    moves: list[dict] = st.session_state.moves
    headers: dict     = st.session_state.headers
    total             = len(moves)
    if total == 0:
        st.error("No moves found in PGN.")
        return

    idx = max(0, min(st.session_state.current_move_idx, total - 1))
    st.session_state.current_move_idx = idx
    cur = moves[idx]

    # ── Game title ────────────────────────────────────────────────────────────
    _white = headers.get("White", "?")
    _black = headers.get("Black", "?")
    _event = headers.get("Event", "").replace("?", "").strip()
    _date  = headers.get("Date", "")[:4].replace("?", "").strip()
    _sub   = " · ".join(p for p in [_event, _date] if p and p != "????")
    st.markdown(
        f'<div style="text-align:center;margin:4px 0 18px;">'
        f'<div style="font-size:1.35em;font-weight:700;color:#cce0f4;letter-spacing:0.02em;">'
        f'⬜ {_white} &nbsp;vs&nbsp; ⬛ {_black}</div>'
        + (f'<div style="font-size:0.82em;color:#7a9ab0;margin-top:4px;letter-spacing:0.04em;">'
           f'{_sub}</div>' if _sub else '')
        + '</div>',
        unsafe_allow_html=True,
    )

    # ── Key Moments Summary Card ──────────────────────────────────────────
    _km_cls_colors = {"blunder": "#e53935", "mistake": "#fb8c00", "brilliant": "#b39ddb"}
    _key_moments = [
        (mi, m) for mi, m in enumerate(moves)
        if m.get("classification") in ("blunder", "mistake", "brilliant")
    ][:6]
    if _key_moments:
        _km_pills = "".join(
            f'<span style="background:{_km_cls_colors.get(m["classification"], "#aaa")}22;'
            f'border:1px solid {_km_cls_colors.get(m["classification"], "#aaa")}55;'
            f'border-radius:4px;padding:2px 9px;font-size:0.78em;'
            f'color:{_km_cls_colors.get(m["classification"], "#aaa")};margin:2px;display:inline-block;">'
            f'{m["move_number"]}{"." if m["color"]=="white" else "…"}{m["move_san"]} '
            f'({m["classification"]})</span>'
            for _, m in _key_moments
        )
        st.markdown(
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
            f'padding:14px 16px;margin-bottom:14px;">'
            f'<div style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
            f'text-transform:uppercase;margin-bottom:8px;">KEY MOMENTS IN THIS GAME</div>'
            f'<div style="line-height:2;">{_km_pills}</div></div>',
            unsafe_allow_html=True,
        )
        _km_cols = st.columns(min(len(_key_moments), 6))
        for _km_ci, (_km_mi, _km_m) in enumerate(_key_moments):
            with _km_cols[_km_ci]:
                _km_lbl = f'{_km_m["move_number"]}{"." if _km_m["color"]=="white" else "…"}{_km_m["move_san"]}'
                if st.button(_km_lbl, key=f"km_jump_{_km_ci}", use_container_width=True):
                    st.session_state.current_move_idx = _km_mi
                    st.rerun()

    # ── Challenge Mode toggle ────────────────────────────────────────────────
    _challenge_col1, _challenge_col2 = st.columns([6, 2])
    with _challenge_col2:
        _challenge_on = st.toggle("\U0001f3af Challenge Mode", key="challenge_mode")
    if _challenge_on:
        # Reset scores if reviewing a different game
        _ch_game_key = f"{headers.get('White','')}_vs_{headers.get('Black','')}_{headers.get('Date','')}"
        if st.session_state.get("_challenge_game_key") != _ch_game_key:
            st.session_state.challenge_correct = 0
            st.session_state.challenge_total = 0
            st.session_state._challenge_game_key = _ch_game_key
        st.session_state.setdefault("challenge_correct", 0)
        st.session_state.setdefault("challenge_total", 0)
        _ch_correct = st.session_state.challenge_correct
        _ch_total = st.session_state.challenge_total
        _ch_acc = round(100 * _ch_correct / _ch_total) if _ch_total > 0 else 0
        with _challenge_col1:
            st.markdown(
                f'<div style="font-size:0.78em;color:#a0bccc;padding-top:8px;">'
                f'Score: <b style="color:#81c784;">{_ch_correct}/{_ch_total}</b>'
                f' ({_ch_acc}% accuracy)</div>',
                unsafe_allow_html=True,
            )

    # ── Board + Commentary side by side ──────────────────────────────────────
    if "board_orientation" not in st.session_state:
        st.session_state.board_orientation = chess.WHITE

    board_col, info_col = st.columns([3, 2], gap="small")

    with board_col:
        # Board + eval bar
        _ch_hide = _challenge_on and not st.session_state.get(f"_challenge_revealed_{idx}")
        _board_fen = cur.get("fen_before", cur["fen_after"]) if _ch_hide else cur["fen_after"]
        _board_eval = None if _ch_hide else cur["eval_after"]
        st.markdown(
            render_board_with_eval(
                _board_fen,
                eval_val=_board_eval if _board_eval is not None else 0.0,
                last_move_uci=None if _ch_hide else cur.get("move_uci"),
                orientation=st.session_state.board_orientation,
                board_size=580,
                best_move_uci=None if _ch_hide else cur.get("best_move_uci"),
            ),
            unsafe_allow_html=True,
        )

        # ── Tablebase label (≤7 pieces) ──────────────────────────────────────
        _tb_result = _query_tablebase(cur["fen_after"])
        if _tb_result:
            _tb_wdl = _tb_result.get("wdl", 0)
            if _tb_wdl > 0:
                _tb_bg, _tb_border, _tb_color = "#0d2818", "#2e7d32", "#81c784"
                _tb_dtm = f" in {abs(_tb_result['dtm'])}" if _tb_result.get("dtm") is not None else ""
                _tb_qual = " (cursed)" if _tb_result.get("category") == "cursed-win" else ""
                _tb_label = f"TB: Win{_tb_dtm}{_tb_qual}"
            elif _tb_wdl < 0:
                _tb_bg, _tb_border, _tb_color = "#1a0a0a", "#b71c1c", "#ef9a9a"
                _tb_dtm = f" in {abs(_tb_result['dtm'])}" if _tb_result.get("dtm") is not None else ""
                _tb_qual = " (blessed)" if _tb_result.get("category") == "blessed-loss" else ""
                _tb_label = f"TB: Loss{_tb_dtm}{_tb_qual}"
            else:
                _tb_bg, _tb_border, _tb_color = "#1a1a2e", "#5a5a6a", "#aaa"
                _tb_label = "TB: Draw"
            _tb_best = f" \u00b7 Best: {_tb_result['best_move']}" if _tb_result.get("best_move") else ""
            st.markdown(
                f'<div style="display:inline-flex;gap:8px;align-items:center;'
                f'background:{_tb_bg};border:1px solid {_tb_border};border-radius:6px;'
                f'padding:4px 12px;font-size:0.78em;color:{_tb_color};font-weight:600;'
                f'margin-bottom:4px;">{_tb_label}{_tb_best}</div>',
                unsafe_allow_html=True,
            )

        # Compact single-row nav: ← | move info | ⇅ | →
        mn     = cur["move_number"]
        prefix = f"{mn}." if cur["color"] == "white" else f"{mn}..."
        nav_l, nav_c, nav_flip, nav_r = st.columns([1, 7, 1, 1])
        with nav_l:
            if st.button("◀", disabled=(idx == 0), use_container_width=True,
                         help="Previous move"):
                st.session_state.current_move_idx = idx - 1
                st.rerun()
        with nav_c:
            _ch_hide = _challenge_on and not st.session_state.get(f"_challenge_revealed_{idx}")
            st.markdown(
                f'<div style="text-align:center;padding-top:6px;font-size:0.9em;">'
                f'<span style="color:#888;">{idx+1}/{total}</span>'
                f'&nbsp;·&nbsp;<b>{prefix}{cur["move_san"]}</b>&nbsp;'
                + ("" if _ch_hide else classification_badge(cur["classification"]))
                + ("" if _ch_hide else f'&nbsp;<span style="color:#ccc;">{cur["eval_after"]:+.2f}</span>')
                + '</div>',
                unsafe_allow_html=True,
            )
        with nav_flip:
            if st.button("⇅", use_container_width=True, help="Flip board"):
                st.session_state.board_orientation = (
                    chess.BLACK if st.session_state.board_orientation == chess.WHITE
                    else chess.WHITE
                )
                st.rerun()
        with nav_r:
            if st.button("▶", disabled=(idx == total - 1), use_container_width=True,
                         help="Next move"):
                st.session_state.current_move_idx = idx + 1
                st.rerun()

        inject_keyboard_nav()
        st.markdown(
            '<div style="text-align:center;font-size:0.72em;color:#3a5070;margin-top:2px;">'
            '&#8592; &#8594; arrow keys to navigate</div>',
            unsafe_allow_html=True,
        )

        # ── Export Annotated PGN ──────────────────────────────────────────────
        pgn_data = _build_annotated_pgn(headers, moves)
        _w = headers.get("White", "White").replace(" ", "_")
        _b = headers.get("Black", "Black").replace(" ", "_")
        st.download_button(
            "\u2b07 Export PGN", pgn_data,
            file_name=f"{_w}_vs_{_b}.pgn", mime="text/plain",
            use_container_width=True,
        )

    with info_col:
      if _challenge_on and cur["classification"] != "book" and not st.session_state.get(f"_challenge_revealed_{idx}"):
        # Challenge Mode: guess before seeing analysis
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin-bottom:4px;">'
            '<div style="width:3px;height:15px;background:#e2c97e;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#e2c97e;font-weight:700;'
            'letter-spacing:0.04em;">WHAT WOULD YOU PLAY?</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        _ch_fen = cur.get("fen_before", cur.get("fen_after", ""))
        _ch_side = "White" if cur["color"] == "white" else "Black"
        st.markdown(
            f'<div style="font-size:0.88em;color:#a0bccc;margin-bottom:8px;">'
            f'Find the best move for {_ch_side}</div>',
            unsafe_allow_html=True,
        )
        _ch_guess = st.text_input("Your move (e.g., Nf3):", key=f"challenge_guess_{idx}")
        if st.button("Submit", key=f"challenge_submit_{idx}", type="primary", use_container_width=True):
            if _ch_guess.strip():
                _ch_best_uci = cur.get("best_move_uci") or ""
                if not _ch_best_uci:
                    # No engine best move available — skip scoring, just reveal
                    st.toast("No engine evaluation available for this position")
                    st.session_state[f"_challenge_revealed_{idx}"] = True
                    st.rerun()
                _ch_correct_move = False
                _ch_valid_move = False
                try:
                    _ch_board = chess.Board(_ch_fen)
                    _ch_parsed = _ch_board.parse_san(_ch_guess.strip())
                    _ch_valid_move = True
                    if _ch_parsed.uci() == _ch_best_uci:
                        _ch_correct_move = True
                except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
                    st.warning("Invalid move notation. Use standard algebraic notation (e.g., Nf3, e4, O-O).")
                except Exception:
                    st.warning("Could not parse that move. Please try again.")
                if _ch_valid_move:
                    st.session_state.challenge_total = st.session_state.get("challenge_total", 0) + 1
                    if _ch_correct_move:
                        st.session_state.challenge_correct = st.session_state.get("challenge_correct", 0) + 1
                        st.toast("\u2705 Correct! That's the engine's top choice!")
                        st.balloons()
                    else:
                        st.toast(f"The engine preferred {cur.get('best_move_san', '?')}")
                    st.session_state[f"_challenge_revealed_{idx}"] = True
                    st.rerun()
            else:
                st.warning("Please enter a move.")
      else:
        if _challenge_on and cur["classification"] == "book":
            st.markdown(
                '<div style="font-size:0.82em;color:#78909c;margin-bottom:8px;">'
                'Book move \u2014 skipped in challenge mode</div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin-bottom:4px;">'
            '<div style="width:3px;height:15px;background:#4a6aaa;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#7a9ad0;font-weight:700;'
            'letter-spacing:0.04em;">MOVE COMMENTARY</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        move_commentary_panel(cur, moves, idx)

        st.markdown("---")
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin-bottom:4px;">'
            '<div style="width:3px;height:15px;background:#4a6aaa;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#7a9ad0;font-weight:700;'
            'letter-spacing:0.04em;">MOVE LIST</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        move_list_panel(moves, idx)

    # ── Evaluation graph (full width) ────────────────────────────────────────
    st.markdown(
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:4px;">'
        '<div style="width:3px;height:15px;background:#4a6aaa;border-radius:2px;flex-shrink:0;"></div>'
        '<span style="font-size:0.9em;color:#7a9ad0;font-weight:700;'
        'letter-spacing:0.04em;">EVALUATION GRAPH</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    clicked = eval_graph_panel(moves, idx)
    if clicked is not None and clicked != idx:
        st.session_state.current_move_idx = clicked
        st.rerun()

    # ── Game overview / accuracy table (full width) ───────────────────────────
    game_overview_panel(moves, headers)

    # ── AI Review ────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:8px;">'
        '<div style="width:3px;height:18px;background:#4a6aaa;border-radius:2px;flex-shrink:0;"></div>'
        '<span style="font-size:0.85em;color:#7a9ad0;font-weight:700;'
        'letter-spacing:0.04em;">AI FULL GAME REVIEW</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    ai_review_panel(moves, headers)


# ── Tab: Coaching ────────────────────────────────────────────────────────────

def _coaching_concept_list() -> list[dict]:
    """
    Merge the pre-built library with DB-persisted concept stats and any
    concepts extracted during the current session.
    Returns a stable-sorted list of dicts: {name, category, examples}.
    """
    game_concepts: dict = st.session_state.get("coaching_concepts", {})
    db_stats = db.get_concept_stats()

    result: dict[str, dict] = {}
    for cat, names in CONCEPT_LIBRARY.items():
        for name in names:
            result[name.lower()] = {"name": name, "category": cat, "examples": []}

    # Seed from DB (cross-game history)
    for concept_name, stats in db_stats.items():
        key = concept_name.lower()
        if key in result:
            result[key]["examples"] = list(stats["examples"])
        else:
            result[key] = {
                "name":     concept_name,
                "category": "From Your Games",
                "examples": list(stats["examples"]),
            }

    # Overlay current-session concepts (may have examples not yet saved)
    for gname, examples in game_concepts.items():
        key = gname.lower()
        if key in result:
            existing = {(e["move_number"], e["color"]) for e in result[key]["examples"]}
            for e in examples:
                if (e["move_number"], e["color"]) not in existing:
                    result[key]["examples"].append(e)
        else:
            result[key] = {"name": gname, "category": "From Your Games", "examples": examples}

    cat_order = {cat: i for i, cat in enumerate(CONCEPT_LIBRARY.keys())}
    cat_order["From Your Games"] = len(cat_order)
    return sorted(result.values(), key=lambda x: (cat_order.get(x["category"], 99), x["name"]))


def _category_badge(category: str) -> str:
    color = CATEGORY_COLORS.get(category, "#90a8b8")
    return (
        f'<span style="background:{color}22;border:1px solid {color}55;'
        f'color:{color};font-size:0.68em;font-weight:700;letter-spacing:0.05em;'
        f'border-radius:4px;padding:2px 8px;">{category.upper()}</span>'
    )


def _get_concept_puzzle_counts() -> dict[str, int]:
    """
    Return {concept_name: puzzle_count} for all non-theory concepts.
    Scans profile critical_moves once and checks _position_has_concept.
    Result is cached in session state keyed by the number of summaries so it
    refreshes automatically when a new profile is built.
    """
    summaries = st.session_state.get("profile_summaries", [])
    cache_key = f"_concept_puz_counts_{len(summaries)}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    # Collect candidate positions (critical moves only — fast)
    candidates: list[dict] = []
    for s in summaries:
        for cm in s.get("critical_moves", []):
            if cm.get("fen_before") and cm.get("best_move_san"):
                candidates.append(cm)
    # Fallback: current-game moves
    if not candidates:
        for m in st.session_state.get("moves", []):
            if (m.get("fen_before") and m.get("best_move_san")
                    and m.get("classification") in ("blunder", "mistake", "inaccuracy")):
                candidates.append(m)

    counts: dict[str, int] = {}
    for cat, concept_names in CONCEPT_LIBRARY.items():
        for name in concept_names:
            if name in _THEORY_ONLY_CONCEPTS:
                counts[name] = 0
                continue
            n = 0
            for cm in candidates:
                if _position_has_concept(
                    cm["fen_before"], name,
                    cm.get("best_move_san", ""), cm.get("color", "white"),
                ):
                    n += 1
                    if n >= 9:   # cap counting at 9 for display
                        break
            counts[name] = n

    st.session_state[cache_key] = counts
    return counts


def _render_concept_card(concept: dict, puzzle_count: int = 0):
    name      = concept["name"]
    category  = concept["category"]
    examples  = concept["examples"]
    cat_color = CATEGORY_COLORS.get(category, "#78909c")
    has_lesson = f"concept_lesson_{name.lower()}" in st.session_state
    is_theory  = name in _THEORY_ONLY_CONCEPTS
    is_focus   = name in (st.session_state.get("profile_data") or {}).get("priority_focus", [])

    game_badge = ""
    if examples:
        game_badge = (
            f'&nbsp;<span style="background:#1a2e1a;border:1px solid #2a5a2a;'
            f'color:#81c784;font-size:0.65em;border-radius:4px;padding:1px 6px;">'
            f'★ {len(examples)}</span>'
        )

    focus_badge = ""
    if is_focus:
        focus_badge = (
            '&nbsp;<span style="background:#2a2510;border:1px solid #5a4a20;'
            'color:#e2c97e;font-size:0.62em;font-weight:700;border-radius:4px;'
            'padding:1px 6px;">🎯 FOCUS</span>'
        )

    # Independent status badges — lesson and puzzles shown separately
    badge_parts = []
    if is_theory:
        badge_parts.append(
            '<span style="font-size:0.67em;color:#7a9ab0;white-space:nowrap;">📖 Theory</span>'
        )
    if has_lesson:
        badge_parts.append(
            '<span style="font-size:0.67em;color:#6abf88;white-space:nowrap;">✓ Lesson</span>'
        )
    if not is_theory and puzzle_count > 0:
        puz_label = f"{puzzle_count}+" if puzzle_count >= 9 else str(puzzle_count)
        badge_parts.append(
            f'<span style="font-size:0.67em;color:#4fc3f7;white-space:nowrap;">'
            f'🧩 {puz_label} puzzle{"s" if puzzle_count != 1 else ""}</span>'
        )
    _cscore = db.get_course_score(name)
    if _cscore:
        _s, _t = _cscore["score"], _cscore["total"]
        _sc_color = "#81c784" if _s == _t else "#ffb74d" if _s / _t >= 0.6 else "#e57373"
        badge_parts.append(
            f'<span style="font-size:0.67em;color:{_sc_color};white-space:nowrap;">'
            f'Last: {_s}/{_t}</span>'
        )

    badge_row = (
        '<div style="display:flex;gap:8px;margin-top:5px;flex-wrap:wrap;">'
        + "".join(badge_parts)
        + "</div>"
    ) if badge_parts else '<div style="margin-top:5px;"></div>'

    card_border = "#3a4a2a" if is_focus else "#1e2e3e"
    st.markdown(
        f'<div class="concept-card" style="background:#111827;border:1px solid {card_border};border-radius:10px;'
        f'padding:14px 14px 8px;margin-bottom:4px;">'
        f'<div style="margin-bottom:6px;">'
        f'<span style="background:{cat_color}22;border:1px solid {cat_color}55;'
        f'color:{cat_color};font-size:0.65em;font-weight:700;border-radius:4px;'
        f'padding:1px 7px;">{category.upper()}</span>{game_badge}{focus_badge}'
        f'</div>'
        f'<div style="font-size:0.92em;font-weight:700;color:#cce0f4;margin:0;">{name}</div>'
        f'{badge_row}'
        f'</div>',
        unsafe_allow_html=True,
    )
    _study_col, _drill_col = st.columns([1, 1]) if (not is_theory and puzzle_count > 0) else (st.columns(1)[0], None)
    if _drill_col is None:
        if st.button("Study \u2192", key=f"study_{name}", use_container_width=True):
            st.session_state.selected_concept = name
            st.rerun()
    else:
        with _study_col:
            if st.button("Study \u2192", key=f"study_{name}", use_container_width=True):
                st.session_state.selected_concept = name
                st.rerun()
        with _drill_col:
            if st.button("Drill \u2192", key=f"drill_{name}", use_container_width=True):
                st.session_state.puzzle_concept_filter = name
                st.session_state.puzzle_idx = 0
                st.session_state.pop("_puzzle_concept_list", None)
                st.session_state.navigate_to_puzzles = True
                st.rerun()


from chess_utils import position_has_concept as _position_has_concept


def _concept_to_category(concept: str) -> str:
    """Reverse-lookup a concept name in CONCEPT_LIBRARY to find its category."""
    for cat, names in CONCEPT_LIBRARY.items():
        if concept in names or concept.lower() in [n.lower() for n in names]:
            return cat
    return "Tactics"


def _build_course_puzzles(concept: str, category: str, n: int = 5) -> list[dict]:
    """
    Build a list of puzzle dicts from profile summaries (or current game as fallback),
    filtered by coaching category and sorted by difficulty relative to player's skill rating.
    """
    # Map coaching categories to profile skill-rating keys
    _CAT_TO_SKILL = {
        "Tactics":       "Tactics",
        "Pawn Structure": "Middlegame",
        "Piece Play":    "Piece Activity",
        "Positional":    "Middlegame",
        "Endgame":       "Endgame",
    }
    skill_cat = _CAT_TO_SKILL.get(category, "Tactics")

    # Skill rating 1-5 (default 3)
    profile_data  = st.session_state.get("profile_data", {})
    skill_ratings = profile_data.get("skill_ratings", {})
    rating = max(1, min(5, int(skill_ratings.get(skill_cat, {}).get("rating", 3))))

    # Collect candidates from profile summaries — only positions that genuinely
    # illustrate the specific concept (not just the broad category).
    summaries  = st.session_state.get("profile_summaries", [])
    candidates: list[tuple[float, dict]] = []
    for s in summaries:
        for cm in s.get("critical_moves", []):
            if not cm.get("fen_before") or not cm.get("best_move_san"):
                continue
            if not _position_has_concept(
                cm["fen_before"], concept,
                cm["best_move_san"], cm.get("color", "white"),
            ):
                continue
            try:
                _vboard = chess.Board(cm["fen_before"])
                _vboard.parse_san(cm["best_move_san"])
            except Exception:
                continue
            swing = abs(cm.get("eval_after", 0.0) - cm.get("eval_before", 0.0))
            candidates.append((swing, cm))

    # Fallback: current-game moves
    if not candidates:
        for m in st.session_state.get("moves", []):
            if not m.get("fen_before") or not m.get("best_move_san"):
                continue
            if m.get("classification") not in ("blunder", "mistake", "inaccuracy"):
                continue
            if not _position_has_concept(
                m["fen_before"], concept,
                m["best_move_san"], m.get("color", "white"),
            ):
                continue
            try:
                _vboard = chess.Board(m["fen_before"])
                _vboard.parse_san(m["best_move_san"])
            except Exception:
                continue
            swing = abs(m.get("eval_after", 0.0) - m.get("eval_before", 0.0))
            candidates.append((swing, m))

    if not candidates:
        return []

    # Sort by difficulty based on skill rating
    if rating <= 2:
        candidates.sort(key=lambda x: x[0], reverse=True)   # biggest swings first (easiest)
    elif rating >= 4:
        candidates.sort(key=lambda x: x[0], reverse=False)  # subtlest first (hardest)
    else:
        random.shuffle(candidates)

    result = []
    for _, m in candidates[:n]:
        result.append({
            "fen":            m["fen_before"],
            "best_move_san":  m["best_move_san"],
            "player_color":   m.get("color", "white"),
            "classification": m.get("classification", ""),
            "eval_before":    m.get("eval_before", 0.0),
            "eval_after":     m.get("eval_after", 0.0),
            "hint":           None,
            "phases":         None,
        })
    return result


def _render_lesson_loading_card(concept: str, regenerating: bool = False):
    """Prominent loading indicator shown while Claude generates a lesson."""
    action = "Rewriting" if regenerating else "Building"
    st.markdown(
        f'<div style="background:#0d1525;border:1px solid #1e2e4a;border-radius:12px;'
        f'padding:48px 24px;text-align:center;margin:16px 0;min-height:120px;'
        f'display:flex;flex-direction:column;align-items:center;justify-content:center;">'
        f'<div class="coaching-pulse" style="width:48px;height:48px;border-radius:50%;'
        f'background:radial-gradient(circle, #4a8aba 0%, #1e3a5a 70%);'
        f'margin:0 auto 16px;"></div>'
        f'<div style="color:#cce0f4;font-size:1.1em;font-weight:600;margin-bottom:8px;">'
        f'{action} your lesson on {concept}</div>'
        f'<div style="color:#5a8ab0;font-size:0.82em;margin-bottom:20px;">'
        f'Claude is crafting personalized content — this takes a few seconds</div>'
        f'<div style="width:120px;height:4px;background:#1e2e3e;border-radius:2px;'
        f'overflow:hidden;position:relative;">'
        f'<div style="position:absolute;width:40%;height:100%;background:#4a8aba;'
        f'border-radius:2px;animation:shimmer 1.4s infinite ease-in-out;"></div></div>'
        f'</div>'
        f'<style>'
        f'@keyframes shimmer{{0%{{left:-40%}}100%{{left:100%}}}}'
        f'.coaching-pulse{{animation:cpulse 2s infinite ease-in-out}}'
        f'@keyframes cpulse{{0%,100%{{opacity:0.4;transform:scale(0.92)}}'
        f'50%{{opacity:1;transform:scale(1)}}}}'
        f'</style>',
        unsafe_allow_html=True,
    )


import re as _re_mod


def _render_time_management(summaries: list[dict]):
    """Render time management section: scatter chart + stat cards."""
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
        'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
        'margin-top:10px;padding-top:8px;border-top:1px solid #152030;">'
        'TIME MANAGEMENT</div>',
        unsafe_allow_html=True,
    )
    # Gather all move_times across summaries
    all_mt: list[dict] = []
    has_any_clock = False
    for s in summaries:
        if s.get("has_clock") and s.get("move_times"):
            has_any_clock = True
            all_mt.extend(s["move_times"])
    if not has_any_clock:
        st.markdown(
            '<p style="color:#5a7a8a;font-size:0.85em;">'
            'Clock data not available for your games</p>',
            unsafe_allow_html=True,
        )
        return
    # Filter to moves with time_spent
    valid_mt = [mt for mt in all_mt if mt.get("time_spent") is not None]
    if not valid_mt:
        st.info("No move time data available.")
        return

    import plotly.graph_objects as _tm_go
    _cls_colors = {"blunder": "#e57373", "mistake": "#ffb74d", "inaccuracy": "#fff176", "good": "#81c784"}
    _tm_x = [mt["move_number"] for mt in valid_mt]
    _tm_y = [mt["time_spent"] for mt in valid_mt]
    _tm_colors = [_cls_colors.get(mt.get("classification", "good"), "#81c784") for mt in valid_mt]
    _tm_fig = _tm_go.Figure(_tm_go.Scatter(
        x=_tm_x, y=_tm_y, mode="markers",
        marker=dict(color=_tm_colors, size=5, opacity=0.7),
        hovertemplate="Move %{x}: %{y:.1f}s<extra></extra>",
    ))
    _tm_fig.update_layout(
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117", height=220,
        margin=dict(l=50, r=20, t=10, b=40),
        xaxis=dict(title="Move Number", gridcolor="#1e2e3e",
                   tickfont=dict(color="#7a9ab0", size=10)),
        yaxis=dict(title="Time (s)", gridcolor="#1e2e3e",
                   tickfont=dict(color="#7a9ab0", size=10)),
    )
    st.plotly_chart(_tm_fig, use_container_width=True, config={"displayModeBar": False})

    # Stat cards
    all_times = [mt["time_spent"] for mt in valid_mt]
    avg_time = round(sum(all_times) / len(all_times), 1)
    fastest = round(min(all_times), 1)
    slowest = round(max(all_times), 1)
    tt_count = sum(1 for mt in valid_mt if mt.get("clock_seconds") is not None and mt["clock_seconds"] < 60)
    tt_pct = round(100 * tt_count / len(valid_mt)) if valid_mt else 0
    st.markdown(
        f'<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:8px;">'
        f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
        f'padding:8px 16px;text-align:center;">'
        f'<div style="font-size:0.95em;font-weight:700;color:#cce0f4;">{avg_time}s</div>'
        f'<div style="font-size:0.62em;color:#7a9ab0;">AVG MOVE TIME</div></div>'
        f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
        f'padding:8px 16px;text-align:center;">'
        f'<div style="font-size:0.95em;font-weight:700;color:#e57373;">{tt_pct}%</div>'
        f'<div style="font-size:0.62em;color:#7a9ab0;">TIME TROUBLE</div></div>'
        f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
        f'padding:8px 16px;text-align:center;">'
        f'<div style="font-size:0.95em;font-weight:700;color:#81c784;">{fastest}s</div>'
        f'<div style="font-size:0.62em;color:#7a9ab0;">FASTEST</div></div>'
        f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
        f'padding:8px 16px;text-align:center;">'
        f'<div style="font-size:0.95em;font-weight:700;color:#ffb74d;">{slowest}s</div>'
        f'<div style="font-size:0.62em;color:#7a9ab0;">SLOWEST</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_comparative_analytics(all_sums: list[dict], full_skills: dict[str, int], skill_cats: list[str]):
    """Render filters for time control/color/result with overlaid radar chart."""
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
        'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
        'margin-top:10px;padding-top:8px;border-top:1px solid #152030;">'
        'COMPARE YOUR PLAY</div>',
        unsafe_allow_html=True,
    )
    _ca_c1, _ca_c2, _ca_c3 = st.columns(3)
    with _ca_c1:
        _ca_tc = st.selectbox("Time Control", ["All", "Bullet", "Blitz", "Rapid", "Classical"],
                              key="_ca_tc_filter")
    with _ca_c2:
        _ca_color = st.selectbox("Color", ["All", "White", "Black"], key="_ca_color_filter")
    with _ca_c3:
        _ca_result = st.selectbox("Result", ["All", "Wins", "Losses", "Draws"], key="_ca_result_filter")

    # Filter summaries
    filtered = all_sums
    if _ca_tc != "All":
        filtered = [s for s in filtered if s.get("time_control", "").lower() == _ca_tc.lower()]
    if _ca_color != "All":
        filtered = [s for s in filtered if s.get("player_color", "").lower() == _ca_color.lower()]
    if _ca_result != "All":
        def _matches_result(s):
            r = s.get("result", "*")
            pc = s.get("player_color", "white")
            if _ca_result == "Wins":
                return (r == "1-0" and pc == "white") or (r == "0-1" and pc == "black")
            elif _ca_result == "Losses":
                return (r == "0-1" and pc == "white") or (r == "1-0" and pc == "black")
            else:
                return r == "1/2-1/2"
        filtered = [s for s in filtered if _matches_result(s)]

    if not filtered:
        st.info("No games match these filters")
    else:
        filt_skills = compute_skill_scores(filtered)
        import plotly.graph_objects as _ca_go
        # Full data (semi-transparent)
        full_vals = [full_skills.get(c, 50) for c in skill_cats]
        filt_vals = [filt_skills.get(c, 50) for c in skill_cats]
        _ca_fig = _ca_go.Figure()
        _ca_fig.add_trace(_ca_go.Scatterpolar(
            r=full_vals + [full_vals[0]], theta=skill_cats + [skill_cats[0]],
            fill="toself", fillcolor="rgba(74,106,170,0.08)",
            line=dict(color="rgba(74,106,170,0.3)", width=1, dash="dot"),
            name="All Games",
        ))
        _ca_fig.add_trace(_ca_go.Scatterpolar(
            r=filt_vals + [filt_vals[0]], theta=skill_cats + [skill_cats[0]],
            fill="toself", fillcolor="rgba(129,199,132,0.18)",
            line=dict(color="#81c784", width=2),
            name="Filtered",
        ))
        _ca_fig.update_layout(
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 100], tickvals=[25, 50, 75, 100],
                                gridcolor="#1e2e3e", linecolor="#1e2e3e",
                                tickfont=dict(color="#7a9ab0", size=9)),
                angularaxis=dict(gridcolor="#1e2e3e", linecolor="#1e2e3e",
                                 tickfont=dict(color="#cce0f4", size=11)),
                bgcolor="#0d1117",
            ),
            paper_bgcolor="#0d1117", height=260,
            margin=dict(l=60, r=60, t=20, b=20), showlegend=True,
            legend=dict(font=dict(color="#a0bccc", size=10)),
        )
        st.plotly_chart(_ca_fig, use_container_width=True, config={"displayModeBar": False})

        # Stat cards
        _ca_wins = sum(1 for s in filtered if (s.get("result") == "1-0" and s.get("player_color") == "white") or (s.get("result") == "0-1" and s.get("player_color") == "black"))
        _ca_losses = sum(1 for s in filtered if (s.get("result") == "0-1" and s.get("player_color") == "white") or (s.get("result") == "1-0" and s.get("player_color") == "black"))
        _ca_draws = len(filtered) - _ca_wins - _ca_losses
        _ca_acc_vals = [s.get("player_accuracy", 50) for s in filtered if s.get("player_accuracy") is not None]
        _ca_avg_acc = round(sum(_ca_acc_vals) / len(_ca_acc_vals), 1) if _ca_acc_vals else 0
        st.markdown(
            f'<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:8px;">'
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
            f'padding:8px 16px;text-align:center;">'
            f'<div style="font-size:0.95em;font-weight:700;color:#cce0f4;">{_ca_wins}W {_ca_losses}L {_ca_draws}D</div>'
            f'<div style="font-size:0.62em;color:#7a9ab0;">RECORD</div></div>'
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
            f'padding:8px 16px;text-align:center;">'
            f'<div style="font-size:0.95em;font-weight:700;color:#4fc3f7;">{_ca_avg_acc}%</div>'
            f'<div style="font-size:0.62em;color:#7a9ab0;">ACCURACY</div></div>'
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
            f'padding:8px 16px;text-align:center;">'
            f'<div style="font-size:0.95em;font-weight:700;color:#a0bccc;">{len(filtered)}</div>'
            f'<div style="font-size:0.62em;color:#7a9ab0;">GAMES</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _get_error_concept_map(summaries: list[dict]) -> dict[str, int]:
    """Map concepts to error counts from critical moves in summaries."""
    import hashlib as _ck_hash
    _ck_ids = "|".join(f"{s.get('white','')}{s.get('date','')}{s.get('player_color','')}" for s in summaries)
    cache_key = f"_error_concept_map_{_ck_hash.md5(_ck_ids.encode()).hexdigest()[:8]}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    counts: dict[str, int] = {}
    for s in summaries:
        for cm in s.get("critical_moves", []):
            if cm.get("classification") not in ("blunder", "mistake"):
                continue
            fen = cm.get("fen_before", "")
            best = cm.get("best_move_san", "")
            color = cm.get("color", "white")
            if not fen or not best:
                continue
            for cat, names in CONCEPT_LIBRARY.items():
                for name in names:
                    if name in _THEORY_ONLY_CONCEPTS:
                        continue
                    try:
                        if _position_has_concept(fen, name, best, color):
                            counts[name] = counts.get(name, 0) + 1
                    except Exception:
                        pass
    result = dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))
    st.session_state[cache_key] = result
    return result


def _render_lesson_chapter_nav(lesson_text: str) -> str:
    """
    If the lesson has 2+ ## headers, inject anchor IDs and a TOC pill bar at the top.
    Returns the modified lesson text (with HTML anchors).
    """
    import html as _html_mod
    # Strip trailing \r from headers (Windows line endings)
    headers = [h.rstrip('\r') for h in _re_mod.findall(r'^## (.+)$', lesson_text, _re_mod.MULTILINE)]
    if len(headers) < 2:
        return lesson_text
    # Build slugs and replace ## headers with anchored versions
    slugs = []
    seen_slugs: set[str] = set()
    modified = lesson_text
    for h in headers:
        slug = _re_mod.sub(r'[^a-z0-9]+', '-', h.lower()).strip('-') or "section"
        # Deduplicate slugs
        base_slug = slug
        counter = 2
        while slug in seen_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        seen_slugs.add(slug)
        safe_h = _html_mod.escape(h)
        slugs.append((h, slug, safe_h))
        # Use regex with line-start anchor for safe replacement
        modified = _re_mod.sub(
            r'^## ' + _re_mod.escape(h) + r'\r?$',
            f'<h2 id="ch-{slug}">{safe_h}</h2>',
            modified, count=1, flags=_re_mod.MULTILINE,
        )
    # Build TOC pills
    pills = " ".join(
        f'<a href="#ch-{slug}" style="display:inline-block;background:#1a2535;'
        f'border:1px solid #2e4a6a;border-radius:999px;padding:4px 14px;'
        f'font-size:0.72em;font-weight:600;color:#7ab3d4;text-decoration:none;'
        f'margin:2px;white-space:nowrap;">{safe_h}</a>'
        for _, slug, safe_h in slugs
    )
    toc = (
        f'<div style="margin-bottom:16px;padding:10px 0;border-bottom:1px solid #1a2e48;">'
        f'{pills}</div>'
    )
    return toc + modified


def _render_concept_detail(concept: str, *, show_header: bool = True):
    all_concepts = _coaching_concept_list()
    data     = next((c for c in all_concepts if c["name"].lower() == concept.lower()), None)
    category = data["category"] if data else "From Your Games"
    examples = data["examples"] if data else []

    if not show_header:
        # Header rendered externally (Learn tab row)
        pass
    else:
        if st.button("← Back to Library", key="coaching_back"):
            st.session_state.pop("selected_concept", None)
            st.rerun()
        st.markdown(
            f'<div style="margin:16px 0 6px;">{_category_badge(category)}</div>'
            f'<h3 style="color:#cce0f4;margin:4px 0 16px;font-size:1.55em;">{concept}</h3>',
            unsafe_allow_html=True,
        )

    # ── Overview card ─────────────────────────────────────────────────────
    _ov_parts: list[str] = []
    _ov_n_examples = len(examples) if examples else 0
    if _ov_n_examples > 0:
        _ov_parts.append(f"{_ov_n_examples} example{'s' if _ov_n_examples != 1 else ''} from your games")
    if concept in _THEORY_ONLY_CONCEPTS:
        _ov_parts.append("Theory concept \u2014 lesson only")
    else:
        _ov_puz = _get_concept_puzzle_counts().get(concept, 0)
        if _ov_puz > 0:
            _ov_parts.append(f"{_ov_puz} practice puzzle{'s' if _ov_puz != 1 else ''}")
    # Read time from stored lesson
    _ov_lesson_key = f"concept_lesson_{concept.lower()}"
    _ov_lesson_text = st.session_state.get(_ov_lesson_key) or db.get_lesson(concept) or ""
    if _ov_lesson_text:
        _ov_wc = len(_ov_lesson_text.split())
        _ov_mins = max(1, round(_ov_wc / 200))
        _ov_parts.append(f"~{_ov_mins} min read")
    if _ov_parts:
        st.markdown(
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
            f'padding:10px 14px;margin-bottom:14px;font-size:0.82em;color:#a0bccc;">'
            f'{" \u00b7 ".join(_ov_parts)}</div>',
            unsafe_allow_html=True,
        )

    # Game examples
    if examples:
        cls_color = {
            "brilliant": "#b39ddb", "best": "#4fc3f7", "good": "#81c784",
            "book": "#78909c", "inaccuracy": "#fff176",
            "mistake": "#ffb74d", "blunder": "#e57373",
        }
        pills = "".join(
            f'<span style="background:{cls_color.get(e["classification"],"#aaa")}22;'
            f'border:1px solid {cls_color.get(e["classification"],"#aaa")}55;'
            f'border-radius:4px;padding:2px 9px;font-size:0.78em;'
            f'color:{cls_color.get(e["classification"],"#aaa")};margin:2px;display:inline-block;">'
            f'{e["move_number"]}{"." if e["color"]=="white" else "…"}{e["move_san"]}</span>'
            for e in examples[:6]
        )
        st.markdown(
            f'<div style="margin-bottom:16px;">'
            f'<div style="font-size:0.72em;color:#a0bccc;font-weight:700;'
            f'letter-spacing:0.06em;margin-bottom:6px;">SEEN IN YOUR GAMES</div>'
            f'<div style="line-height:2;">{pills}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # Lesson content — pull from DB if not already in session, then auto-generate
    lesson_key = f"concept_lesson_{concept.lower()}"
    if lesson_key not in st.session_state:
        saved = db.get_lesson(concept)
        if saved:
            st.session_state[lesson_key] = saved

    # Generate inline with loading card if needed (no page-jumping rerun)
    lesson_area = st.empty()
    if lesson_key not in st.session_state:
        if _lesson_gen_remaining() <= 0:
            st.warning(f"Daily generation limit reached ({_DAILY_LESSON_CAP}/day). Resets tomorrow.")
        else:
            with lesson_area.container():
                _render_lesson_loading_card(concept)
            st.session_state[lesson_key] = generate_concept_lesson(concept, examples)
            _count_lesson_gen()
            db.save_lesson(concept, st.session_state[lesson_key])
            _check_achievement("first_lesson")
            _increment_daily_goal("lessons")
            # Check if all concept lessons are now generated
            _all_done = all(
                f"concept_lesson_{c['name'].lower()}" in st.session_state
                for c in _coaching_concept_list()
            )
            if _all_done:
                _check_achievement("all_concepts")

    if lesson_key in st.session_state:
        with lesson_area.container():
            _lesson_text, _lesson_diagrams, _ = parse_lesson_diagrams(st.session_state[lesson_key])
            _takeaway = _extract_takeaway(_lesson_text)
            # Chapter navigation TOC
            _lesson_text = _render_lesson_chapter_nav(_lesson_text)
            _, _lc, _ = st.columns([1, 6, 1])
            with _lc:
                if _takeaway:
                    _render_takeaway_card(_takeaway)
                st.markdown(_lesson_text, unsafe_allow_html=True)
                _render_lesson_diagrams(_lesson_diagrams, concept)

    _regen_disabled = _lesson_gen_remaining() <= 0
    if st.button("↺ Regenerate lesson", key="regen_lesson", disabled=_regen_disabled):
        with lesson_area.container():
            _render_lesson_loading_card(concept, regenerating=True)
        st.session_state[lesson_key] = generate_concept_lesson(concept, examples)
        _count_lesson_gen()
        db.save_lesson(concept, st.session_state[lesson_key])
        st.rerun()

    if concept in _THEORY_ONLY_CONCEPTS:
        st.markdown(
            '<div style="background:#0d1525;border:1px solid #1e2e4a;border-left:3px solid #5a7ac8;'
            'border-radius:8px;padding:12px 16px;font-size:0.85em;color:#a0bccc;">'
            '📖 <strong style="color:#cce0f4;">Theory concept</strong> — this pattern is best '
            'recognised through study rather than static positions. Interactive puzzles are not '
            'available for this concept, but the lesson above will build your understanding.'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        _puz_count = _get_concept_puzzle_counts().get(concept, 0)
        if _puz_count > 0:
            _puz_label = f"{_puz_count}+" if _puz_count >= 9 else str(_puz_count)
            st.markdown(
                f'<p style="font-size:0.82em;color:#4fc3f7;margin:0 0 8px;">'
                f'🧩 {_puz_label} practice puzzle{"s" if _puz_count != 1 else ""} '
                f'from your games</p>',
                unsafe_allow_html=True,
            )
            if st.button("▶ Start Course", key=f"start_course_{concept}", type="primary"):
                _cat = _concept_to_category(concept)
                _puzs = _build_course_puzzles(concept, _cat)
                st.session_state.active_course = {
                    "concept": concept, "category": _cat,
                    "step": 0, "puzzles": _puzs, "results": [],
                }
                st.rerun()
        else:
            st.markdown(
                '<p style="font-size:0.82em;color:#5a7a8a;margin:0 0 8px;">'
                'No practice puzzles found in your games yet — play more games or load a profile.</p>',
                unsafe_allow_html=True,
            )


def _find_reference_game(concept: str) -> dict | None:
    """
    Search profile summaries for any game containing a position that illustrates
    the given concept (scans every position in each game, not just critical moves).
    Returns a metadata dict {white, black, date, result, pgn} for the first match,
    or None if no game is found.
    """
    summaries = st.session_state.get("profile_summaries", [])
    for s in summaries:
        pgn_text = s.get("_pgn", "")
        if not pgn_text:
            continue
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
            if game is None:
                continue
            board = game.board()
            for node in game.mainline():
                fen_before = board.fen()
                move = node.move
                try:
                    san = board.san(move)
                except Exception:
                    board.push(move)
                    continue
                color = "white" if board.turn == chess.WHITE else "black"
                if _position_has_concept(fen_before, concept, san, color):
                    return {
                        "white":  s.get("white",  "?"),
                        "black":  s.get("black",  "?"),
                        "date":   s.get("date",   ""),
                        "result": s.get("result", "*"),
                        "pgn":    pgn_text,
                    }
                board.push(move)
        except Exception:
            continue
    return None


def render_course_view():
    """
    Render the active course flow: intro (step 0), puzzle steps (1..N), summary (N+1).
    Reads/writes st.session_state.active_course.
    """
    course   = st.session_state.active_course
    concept  = course["concept"]
    category = course["category"]
    step     = course["step"]
    puzzles  = course["puzzles"]
    results  = course["results"]
    total    = len(puzzles)

    # ── Step 0: Intro ──────────────────────────────────────────────────────────
    if step == 0:
        if st.button("← Back to Library", key="course_back_intro"):
            st.session_state.pop("active_course", None)
            st.rerun()

        st.markdown(
            f'<div style="margin:16px 0 6px;">{_category_badge(category)}</div>'
            f'<h3 style="color:#cce0f4;margin:4px 0 16px;font-size:1.55em;">'
            f'Course: {concept}</h3>',
            unsafe_allow_html=True,
        )

        # Lesson content
        lesson_key = f"concept_lesson_{concept.lower()}"
        if lesson_key not in st.session_state:
            saved = db.get_lesson(concept)
            if saved:
                st.session_state[lesson_key] = saved

        lesson_area = st.empty()
        if lesson_key not in st.session_state:
            if _lesson_gen_remaining() <= 0:
                st.warning(f"Daily generation limit reached ({_DAILY_LESSON_CAP}/day). Resets tomorrow.")
            else:
                with lesson_area.container():
                    _render_lesson_loading_card(concept)
                st.session_state[lesson_key] = generate_concept_lesson(concept, [])
                _count_lesson_gen()
                db.save_lesson(concept, st.session_state[lesson_key])

        if lesson_key in st.session_state:
            with lesson_area.container():
                _lt, _, _ = parse_lesson_diagrams(st.session_state[lesson_key])
                _tk = _extract_takeaway(_lt)
                _, _lc, _ = st.columns([1, 6, 1])
                with _lc:
                    if _tk:
                        _render_takeaway_card(_tk)
                    st.markdown(_lt)
        st.markdown("---")

        if not puzzles:
            with st.spinner(f"Searching your games for {concept} positions…"):
                ref = _find_reference_game(concept)

            if ref:
                opp = ref["black"] if ref["white"].lower() == st.session_state.get("profile_username_built", "").lower() else ref["white"]
                date_str = ref["date"][:10] if ref.get("date") and "?" not in ref["date"] else ""
                date_label = f" on {date_str}" if date_str else ""
                st.markdown(
                    f'<div style="background:#0d1f12;border:1px solid #2a5a32;border-radius:10px;'
                    f'padding:10px 14px;margin-bottom:8px;">'
                    f'<div style="font-size:0.72em;color:#81c784;font-weight:700;'
                    f'letter-spacing:0.08em;margin-bottom:6px;">FOUND IN YOUR GAMES</div>'
                    f'<p style="color:#c0d0e0;margin:0 0 8px;">'
                    f'We didn\'t find <strong style="color:#cce0f4">{concept}</strong> among your '
                    f'critical moves, but this concept <strong>appears in your game vs {opp}</strong>'
                    f'{date_label} ({ref["result"]}). '
                    f'Load that game in Game Review to study it in context.</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button(f"📖 Review game vs {opp}", key="course_load_ref_game", type="primary"):
                    st.session_state.pop("active_course", None)
                    _deep_dive_to_review(ref["pgn"], ref["white"], ref["black"])
            else:
                st.info(
                    f"No positions illustrating **{concept}** found in your game history yet. "
                    "Play more games or load a profile to unlock interactive puzzles."
                )

            if st.button("← Back to Library", key="course_back_no_puz"):
                st.session_state.pop("active_course", None)
                st.rerun()
            return

        st.markdown(
            f'<p style="color:#a0bccc;font-size:0.88em;margin-bottom:12px;">'
            f'{total} position{"s" if total != 1 else ""} ready from your games</p>',
            unsafe_allow_html=True,
        )
        if st.button("▶ Start Puzzles", type="primary", key="course_start"):
            st.session_state.active_course["step"] = 1
            st.rerun()
        return

    # ── Summary step (step > total) ───────────────────────────────────────────
    if step > total:
        n_correct = sum(1 for r in results if r)
        pct = round(100 * n_correct / total) if total else 0

        # Persist course score
        db.save_course_score(concept, n_correct, total)
        if n_correct == total and total >= 5:
            _check_achievement("perfect_course")

        st.markdown(
            '<h3 style="color:#cce0f4;text-align:center;margin:16px 0 8px;">Course Complete!</h3>',
            unsafe_allow_html=True,
        )

        if pct == 100:
            score_bg, score_border, score_color = "#0d1f12", "#2a5a32", "#81c784"
        elif pct >= 50:
            score_bg, score_border, score_color = "#0d1525", "#1e3a5a", "#4fc3f7"
        else:
            score_bg, score_border, score_color = "#1f1200", "#5a3500", "#ffb74d"

        st.markdown(
            f'<div style="background:{score_bg};border:1px solid {score_border};'
            f'border-radius:10px;padding:12px;text-align:center;margin-bottom:10px;">'
            f'<div style="font-size:2.5em;font-weight:800;color:{score_color};">'
            f'{n_correct}/{total}</div>'
            f'<div style="font-size:0.88em;color:#a0bccc;margin-top:4px;">'
            f'{pct}% correct</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for i, (puz, res) in enumerate(zip(puzzles, results)):
            cls      = puz.get("classification", "")
            icon     = "✓" if res else "✗"
            icon_col = "#81c784" if res else "#e57373"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;'
                f'padding:8px 12px;border-bottom:1px solid #1a2535;">'
                f'<span style="font-size:1.1em;font-weight:700;color:{icon_col};">{icon}</span>'
                f'<span style="color:#cce0f4;font-size:0.9em;">Puzzle {i + 1}</span>'
                + (f'&nbsp;{classification_badge(cls)}' if cls else '')
                + f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)

        retry_col, back_col = st.columns(2)
        with retry_col:
            if st.button("↺ Retry Course", key="course_retry", use_container_width=True):
                new_puzs = _build_course_puzzles(concept, category)
                st.session_state.active_course.update({
                    "step": 0, "results": [], "puzzles": new_puzs,
                })
                st.rerun()
        with back_col:
            if st.button(f"← Back to {concept}", key="course_back_summary", use_container_width=True):
                st.session_state.pop("active_course", None)
                st.session_state.selected_concept = concept
                st.rerun()
        return

    # ── Puzzle step (1..N) ────────────────────────────────────────────────────
    puz_idx = step - 1
    puzzle  = puzzles[puz_idx]
    cls     = puzzle.get("classification", "")

    color_cap = puzzle.get("player_color", "white").capitalize()
    pz_accent = "#e2c97e" if puzzle.get("player_color") == "white" else "#90aec4"
    pz_icon   = "&#9812;" if puzzle.get("player_color") == "white" else "&#9818;"

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap;">'
        f'<span style="color:#a0bccc;font-size:0.9em;font-weight:600;">'
        f'Course: <b style="color:#cce0f4;">{concept}</b></span>'
        f'&nbsp;·&nbsp;'
        f'<span style="color:#a0bccc;font-size:0.82em;">Puzzle {step} / {total}</span>'
        + (f'&nbsp;{classification_badge(cls)}' if cls else '')
        + '</div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
        f'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;">'
        f'Find the best move for&nbsp;<span style="color:{pz_accent};">{pz_icon}&nbsp;{color_cap}</span>'
        f'</div></div>'
        f'<div style="font-size:0.78em;color:#607d8b;margin-bottom:8px;">Click or drag a piece to its destination</div>',
        unsafe_allow_html=True,
    )

    # Lazy phase computation
    if puzzle.get("phases") is None:
        st.markdown(
            '<div style="display:flex;flex-direction:column;align-items:center;'
            'justify-content:center;padding:80px 0;gap:16px;">'
            '<div style="width:40px;height:40px;border:4px solid #1e2e3e;'
            'border-top:4px solid #5a9ac0;border-radius:50%;'
            'animation:spin 0.8s linear infinite;"></div>'
            '<div style="font-size:0.95em;color:#a0bccc;font-weight:600;">'
            'Building puzzle sequence\u2026</div>'
            '</div>'
            '<style>@keyframes spin{to{transform:rotate(360deg);}}</style>',
            unsafe_allow_html=True,
        )
        try:
            puzzle["phases"] = _build_puzzle_phases(puzzle)
        except Exception:
            puzzle["phases"] = None
        st.rerun()

    # Hint / Show Move state
    reveal_now = st.session_state.pop(f"_reveal_course_{puz_idx}", False)
    has_hint = bool(puzzle.get("hint"))

    # Interactive board (drilldown mode: puzzle_idx=-1 so no auto-advance)
    st.components.v1.html(
        _interactive_board_html(
            fen=puzzle["fen"],
            best_move_san=puzzle["best_move_san"],
            eval_before=puzzle["eval_before"],
            eval_after=puzzle["eval_after"],
            player_color=puzzle["player_color"],
            puzzle_idx=-1,
            phases=puzzle.get("phases"),
            reveal_solution=reveal_now,
            highlight_hint=(has_hint and not reveal_now),
        ),
        height=_board_iframe_height(),
        scrolling=False,
    )

    # Coaching hint → Show Move (immediately below board)
    if puzzle.get("hint"):
        _render_hint_card(puzzle["hint"])
        if st.button("▶ Show Move", key=f"course_showmove_{puz_idx}", use_container_width=True):
            st.session_state[f"_reveal_course_{puz_idx}"] = True
            st.rerun()
    else:
        if st.button("💡 Get Hint", key=f"course_hint_{puz_idx}", use_container_width=True):
            if not _api_limit_reached():
                _count_api_call()
                with st.spinner("Thinking…"):
                    try:
                        puzzle["hint"] = generate_puzzle_hint(
                            puzzle["fen"],
                            puzzle["best_move_san"],
                            puzzle["player_color"],
                            puzzle["classification"],
                        )
                    except Exception:
                        puzzle["hint"] = "Focus on piece coordination and look for tactical opportunities."
                st.rerun()

    # Self-report result buttons
    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
    got_col, miss_col = st.columns(2)
    with got_col:
        if st.button("✓ Got it", key=f"course_got_{puz_idx}", type="primary", use_container_width=True):
            st.session_state.active_course["results"].append(True)
            st.session_state.active_course["step"] += 1
            st.rerun()
    with miss_col:
        if st.button("✗ Missed it", key=f"course_miss_{puz_idx}", use_container_width=True):
            st.session_state.active_course["results"].append(False)
            st.session_state.active_course["step"] += 1
            st.rerun()


_DAILY_LESSON_CAP = 50   # max lesson generations per client per day
_DAILY_API_CAP    = 100  # max Claude API calls (hints, explanations, tutor, review, chat)


def _get_client_id() -> str:
    """Best-effort client identifier from request headers (IP-based)."""
    try:
        headers = st.context.headers
        # Behind a proxy (Vercel, nginx, etc.) the real IP is in forwarding headers
        for key in ("X-Forwarded-For", "X-Real-Ip"):
            val = headers.get(key)
            if val:
                return val.split(",")[0].strip()
    except Exception:
        pass
    return "local"


def _lesson_gen_remaining() -> int:
    """Return how many lesson generations this client has left today."""
    used = db.get_daily_generation_count(_get_client_id())
    return max(0, _DAILY_LESSON_CAP - used)


def _count_lesson_gen(n: int = 1):
    """Record lesson generation(s) against today's quota (lesson + API counters)."""
    db.increment_generation_count(_get_client_id(), n)
    db.increment_generation_count(_get_client_id() + ":api", n)


# ── Unified API-call rate limiting ───────────────────────────────────────────

def _api_calls_remaining() -> int:
    """Return how many Claude API calls this client has left today."""
    used = db.get_daily_generation_count(_get_client_id() + ":api")
    return max(0, _DAILY_API_CAP - used)


def _count_api_call(n: int = 1):
    """Record Claude API call(s) against today's quota."""
    db.increment_generation_count(_get_client_id() + ":api", n)


def _api_limit_reached() -> bool:
    """Check if daily API limit is reached and show warning if so."""
    if _api_calls_remaining() <= 0:
        st.warning(f"Daily AI usage limit reached ({_DAILY_API_CAP} calls/day). Resets tomorrow.")
        return True
    return False


def _bulk_generate_lessons(concepts: list[dict]):
    """Bulk-generate missing lessons with progress bar."""
    to_generate = []
    for c in concepts:
        lk = f"concept_lesson_{c['name'].lower()}"
        if lk in st.session_state:
            continue
        saved = db.get_lesson(c["name"])
        if saved:
            st.session_state[lk] = saved
            continue
        to_generate.append(c)

    if not to_generate:
        st.toast("All lessons already generated!")
        return

    remaining = _lesson_gen_remaining()
    if remaining <= 0:
        st.warning(f"Daily generation limit reached ({_DAILY_LESSON_CAP}/day). Resets tomorrow.")
        return
    if len(to_generate) > remaining:
        st.info(f"Generating {remaining} of {len(to_generate)} (daily limit: {_DAILY_LESSON_CAP}).")
        to_generate = to_generate[:remaining]

    progress = st.progress(0, text="Preparing lessons...")
    for i, c in enumerate(to_generate):
        progress.progress(
            i / len(to_generate),
            text=f"Generating lesson {i + 1} of {len(to_generate)}: {c['name']}...",
        )
        examples = c.get("examples", [])[:3]
        lesson = generate_concept_lesson(c["name"], examples if examples else None)
        _count_lesson_gen()
        db.save_lesson(c["name"], lesson)
        st.session_state[f"concept_lesson_{c['name'].lower()}"] = lesson

    progress.progress(1.0, text="All lessons ready!")
    import time; time.sleep(0.8)
    st.rerun()


def _render_concept_library():
    st.markdown("""
<style>
.concept-card {
    height: 118px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    gap: 6px;
    transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
    cursor: pointer;
}
.concept-card:hover {
    transform: scale(1.04);
    border-color: #3a6a96 !important;
    box-shadow: 0 6px 22px rgba(0,0,0,0.5);
    position: relative;
    z-index: 5;
}
</style>
""", unsafe_allow_html=True)

    st.markdown(
        '<p style="color:#7a9ab0;font-size:0.82em;margin:0 0 16px;">'
        'Deep-dive into specific chess concepts with AI-generated lessons and practice '
        'positions from your games. For a structured step-by-step path, '
        'switch to <strong style="color:#a0bccc;">Training</strong>.</p>',
        unsafe_allow_html=True,
    )

    all_concepts  = _coaching_concept_list()
    game_concepts = st.session_state.get("coaching_concepts", {})
    n_lessons     = sum(
        1 for c in all_concepts
        if f"concept_lesson_{c['name'].lower()}" in st.session_state
    )

    # Compute puzzle counts once (cached) — shows which concepts have practice positions
    puzzle_counts = _get_concept_puzzle_counts()
    n_with_puzzles = sum(1 for c in all_concepts if puzzle_counts.get(c["name"], 0) > 0)

    parts = [f"{len(all_concepts)} concepts"]
    if game_concepts:
        parts.append(f"{len(game_concepts)} from your games")
    if n_lessons:
        parts.append(f"{n_lessons} lesson{'s' if n_lessons != 1 else ''} ready")
    if n_with_puzzles:
        parts.append(f"{n_with_puzzles} with practice puzzles")
    st.markdown(
        f'<p style="text-align:center;color:#a0bccc;margin-bottom:18px;font-size:0.88em;">'
        f'{" · ".join(parts)}</p>',
        unsafe_allow_html=True,
    )

    # Bulk lesson generation buttons
    _has_profile = bool(st.session_state.get("profile_data"))
    _missing_any = n_lessons < len(all_concepts)

    if _missing_any:
        _gen_exhausted = _lesson_gen_remaining() <= 0
        _bcols = st.columns([1, 1, 1] if _has_profile else [1, 1])
        col_idx = 0
        if _has_profile:
            _focus = set((st.session_state.get("profile_data") or {}).get("priority_focus", []))
            _profile_concepts = [
                c for c in all_concepts
                if c["name"] in _focus or c.get("examples")
            ]
            _profile_missing = [
                c for c in _profile_concepts
                if f"concept_lesson_{c['name'].lower()}" not in st.session_state
            ]
            if _profile_missing:
                with _bcols[col_idx]:
                    if st.button(
                        f"Prepare My Courses ({len(_profile_missing)})",
                        key="bulk_gen_profile",
                        use_container_width=True,
                        type="primary",
                        disabled=_gen_exhausted,
                    ):
                        _bulk_generate_lessons(_profile_missing)
            col_idx += 1

        _all_missing = [
            c for c in all_concepts
            if f"concept_lesson_{c['name'].lower()}" not in st.session_state
        ]
        if _all_missing:
            with _bcols[col_idx]:
                if st.button(
                    f"Generate All Lessons ({len(_all_missing)})",
                    key="bulk_gen_all",
                    use_container_width=True,
                    disabled=_gen_exhausted,
                ):
                    _bulk_generate_lessons(_all_missing)
        if _gen_exhausted:
            st.caption(f"Daily limit reached ({_DAILY_LESSON_CAP}/day). Resets tomorrow.")

    # Category filter buttons
    cats = ["All"] + list(CONCEPT_LIBRARY.keys())
    if any(c["category"] == "From Your Games" for c in all_concepts):
        cats.append("From Your Games")
    active_cat = st.session_state.get("coaching_category", "All")
    filter_cols = st.columns(len(cats))
    for i, cat in enumerate(cats):
        with filter_cols[i]:
            if st.button(
                cat, key=f"cat_{cat}",
                use_container_width=True,
                type="primary" if cat == active_cat else "secondary",
            ):
                st.session_state.coaching_category = cat
                st.rerun()

    st.markdown('<div style="height:14px;"></div>', unsafe_allow_html=True)

    visible = all_concepts if active_cat == "All" else [
        c for c in all_concepts if c["category"] == active_cat
    ]

    # Float focus-area concepts to the top
    _focus_set = set((st.session_state.get("profile_data") or {}).get("priority_focus", []))
    if _focus_set:
        visible = sorted(visible, key=lambda c: c["name"] not in _focus_set)

    if not visible:
        st.markdown(
            '<div style="text-align:center;padding:32px 0;color:#90aec4;">'
            'No concepts in this category yet.</div>',
            unsafe_allow_html=True,
        )
        return

    cols = st.columns(3)
    for i, concept in enumerate(visible):
        with cols[i % 3]:
            _render_concept_card(concept, puzzle_count=puzzle_counts.get(concept["name"], 0))


def render_coaching_tab(*, _detail_header_shown: bool = False):
    # Active course takes priority over library/detail view
    if st.session_state.get("active_course"):
        render_course_view()
        return

    selected = st.session_state.get("selected_concept")
    if selected:
        _render_concept_detail(selected, show_header=not _detail_header_shown)
    else:
        _render_concept_library()


# ── Tab: Puzzles ──────────────────────────────────────────────────────────────

def _blindfold_piece_list(fen: str) -> str:
    """Parse FEN and return a text-based piece list grouped by color."""
    _UNICODE = {
        ("w", chess.KING): "\u2654", ("w", chess.QUEEN): "\u2655",
        ("w", chess.ROOK): "\u2656", ("w", chess.BISHOP): "\u2657",
        ("w", chess.KNIGHT): "\u2658", ("w", chess.PAWN): "\u2659",
        ("b", chess.KING): "\u265a", ("b", chess.QUEEN): "\u265b",
        ("b", chess.ROOK): "\u265c", ("b", chess.BISHOP): "\u265d",
        ("b", chess.KNIGHT): "\u265e", ("b", chess.PAWN): "\u265f",
    }
    _ORDER = [chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]
    board = chess.Board(fen)
    lines = []
    for color_name, color_val in [("White", chess.WHITE), ("Black", chess.BLACK)]:
        c_key = "w" if color_val == chess.WHITE else "b"
        pieces = []
        for pt in _ORDER:
            for sq in board.pieces(pt, color_val):
                sym = _UNICODE.get((c_key, pt), "?")
                pieces.append(f"{sym}{chess.square_name(sq)}")
        lines.append(f"{color_name}: {' '.join(pieces)}")
    return "\n".join(lines)


def _build_puzzle_queue() -> list[dict]:
    """
    Build a shuffled list of puzzle dicts from the stored profile summaries.
    Only includes positions that have both a FEN and a known best move.

    When profile data is available, puzzles from the player's weakest game
    phase are duplicated so they appear ~2× as often after shuffling.
    """
    summaries = st.session_state.get("profile_summaries", [])
    puzzles: list[dict] = []
    for s in summaries:
        opponent = s.get("black", "?") if s.get("player_color") == "white" else s.get("white", "?")
        for cm in s.get("critical_moves", []):
            if not cm.get("fen_before") or not cm.get("best_move_san"):
                continue
            try:
                _vboard = chess.Board(cm["fen_before"])
                _vboard.parse_san(cm["best_move_san"])
            except Exception:
                continue
            puzzles.append({
                "fen":            cm["fen_before"],
                "best_move_san":  cm["best_move_san"],
                "eval_before":    cm.get("eval_before", 0.0),
                "eval_after":     cm.get("eval_after", 0.0),
                "player_color":   cm.get("color", "white"),
                "classification": cm.get("classification", ""),
                "phase":          cm.get("phase", ""),
                "move_san":       cm.get("move_san", ""),
                "move_number":    cm.get("move_number", 0),
                "opponent":       opponent,
                "date":           s.get("date", "")[:7],
            })

    # Weight toward weakest phase — duplicate those puzzles so they appear ~2×
    # and tag them with a focus reason for the UI
    weakest_phase = None
    if summaries and puzzles:
        def _pavg(vals):
            clean = [v for v in vals if v is not None]
            return sum(clean) / len(clean) if clean else 50.0
        phase_accs = {
            "opening": _pavg([s.get("opening_accuracy") for s in summaries]),
            "middlegame": _pavg([s.get("middlegame_accuracy") for s in summaries]),
            "endgame": _pavg([s.get("endgame_accuracy") for s in summaries]),
        }
        weakest_phase = min(phase_accs, key=phase_accs.get)
        extras = []
        for p in puzzles:
            if p.get("phase") == weakest_phase:
                dup = dict(p)
                dup["focus_reason"] = f"Targeting: {weakest_phase} weakness"
                extras.append(dup)
        puzzles.extend(extras)

    # Tag original weak-phase puzzles too
    if weakest_phase:
        for p in puzzles:
            if p.get("phase") == weakest_phase and "focus_reason" not in p:
                p["focus_reason"] = f"Targeting: {weakest_phase} weakness"

    random.shuffle(puzzles)
    return puzzles


def _last10_html(recent: list) -> str:
    """
    Render 10 puzzle result slots: green ✓ for correct, red ✗ for wrong,
    small blue-grey dot for unfilled slots (filled left→right, oldest first).
    """
    parts = []
    for i in range(10):
        if i < len(recent):
            if recent[i]:
                parts.append(
                    '<div style="width:28px;height:28px;border-radius:50%;'
                    'background:#1a3525;border:1.5px solid #2e7d32;'
                    'display:flex;align-items:center;justify-content:center;'
                    'font-size:0.92em;font-weight:700;color:#66bb6a;">&#10003;</div>'
                )
            else:
                parts.append(
                    '<div style="width:28px;height:28px;border-radius:50%;'
                    'background:#351a1a;border:1.5px solid #b71c1c;'
                    'display:flex;align-items:center;justify-content:center;'
                    'font-size:0.92em;font-weight:700;color:#e57373;">&#10007;</div>'
                )
        else:
            parts.append(
                '<div style="width:11px;height:11px;border-radius:50%;'
                'background:#16202e;border:1.5px solid #253a55;flex-shrink:0;"></div>'
            )
    return (
        '<div style="display:flex;align-items:center;gap:5px;">'
        + ''.join(parts)
        + '</div>'
    )


# ── Puzzle phase helpers ──────────────────────────────────────────────────────

def _board_to_pos(board: chess.Board) -> dict:
    pos = {}
    for sq, piece in board.piece_map().items():
        pos[chess.square_name(sq)] = {
            "c": "w" if piece.color == chess.WHITE else "b",
            "t": piece.symbol().upper(),
        }
    return pos


def _board_to_legal(board: chess.Board) -> dict:
    legal: dict[str, list] = {}
    for move in board.legal_moves:
        f = chess.square_name(move.from_square)
        t = chess.square_name(move.to_square)
        if f not in legal:
            legal[f] = []
        if t not in legal[f]:
            legal[f].append(t)
    return legal


def _board_to_effects_san(board: chess.Board) -> tuple[dict, dict, dict, dict, dict, dict]:
    """Return (effects, san_map, move_meta, promo_effects, promo_san, promo_meta)."""
    before_map = dict(board.piece_map())
    effects: dict[str, list] = {}
    san_map: dict[str, str] = {}
    move_meta: dict[str, dict] = {}
    promo_effects: dict[str, dict] = {}
    promo_san: dict[str, dict] = {}
    promo_meta: dict[str, dict] = {}
    for move in board.legal_moves:
        key = move.uci()[:4]
        is_promo = move.promotion is not None
        promo_letter = chess.piece_symbol(move.promotion).lower() if move.promotion else None
        test = board.copy()
        test.push(move)
        after_map = dict(test.piece_map())
        changes: list[dict] = []
        for sq_int in set(before_map) | set(after_map):
            b = before_map.get(sq_int)
            a = after_map.get(sq_int)
            if b == a:
                continue
            sq_name = chess.square_name(sq_int)
            if a is None:
                changes.append({"sq": sq_name})
            else:
                changes.append({
                    "sq": sq_name,
                    "c": "w" if a.color == chess.WHITE else "b",
                    "t": a.symbol().upper(),
                })
        meta = {
            "capture": board.is_capture(move),
            "check": test.is_check(),
            "castle": board.is_castling(move),
        }
        san = board.san(move)
        if is_promo:
            if key not in promo_effects:
                promo_effects[key] = {}
                promo_san[key] = {}
                promo_meta[key] = {}
            promo_effects[key][promo_letter] = changes
            promo_san[key][promo_letter] = san
            promo_meta[key][promo_letter] = meta
            # Default (queen) goes into main effects for fallback
            if promo_letter == "q":
                if key not in effects:
                    effects[key] = changes
                    san_map[key] = san
                    move_meta[key] = meta
        else:
            if key not in effects:
                effects[key] = changes
                san_map[key] = san
                move_meta[key] = meta
    return effects, san_map, move_meta, promo_effects, promo_san, promo_meta


def _compute_move_effects(board: chess.Board, move: chess.Move) -> list[dict]:
    before_map = dict(board.piece_map())
    test = board.copy()
    test.push(move)
    after_map = dict(test.piece_map())
    changes: list[dict] = []
    for sq_int in set(before_map) | set(after_map):
        b = before_map.get(sq_int)
        a = after_map.get(sq_int)
        if b == a:
            continue
        sq_name = chess.square_name(sq_int)
        if a is None:
            changes.append({"sq": sq_name})
        else:
            changes.append({
                "sq": sq_name,
                "c": "w" if a.color == chess.WHITE else "b",
                "t": a.symbol().upper(),
            })
    return changes


def _make_phase(
    board: chess.Board,
    best_uci: str,
    best_san: str,
    ev_before: float,
    ev_after: float,
    engine_resp,
) -> dict:
    effects, san_map, move_meta, promo_effects, promo_san, promo_meta = _board_to_effects_san(board)
    return {
        "pos":           _board_to_pos(board),
        "legal":         _board_to_legal(board),
        "effects":       effects,
        "san_map":       san_map,
        "move_meta":     move_meta,
        "promo_effects": promo_effects,
        "promo_san":     promo_san,
        "promo_meta":    promo_meta,
        "best_uci":      best_uci,
        "best_san":      best_san,
        "ev_before":     ev_before,
        "ev_after":      ev_after,
        "engine":        engine_resp,
    }


def _render_hint_card(hint_text: str):
    """Render the coaching hint card (shared by Puzzles tab and Coaching course)."""
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:10px;'
        f'background:#0f1c2e;border:1px solid #1e3a50;border-radius:10px;'
        f'padding:12px 16px;margin-top:6px;">'
        f'<span style="font-size:1.2em;flex-shrink:0;margin-top:1px;">&#128161;</span>'
        f'<div>'
        f'<div style="font-size:0.65em;color:#5a8ab0;font-weight:700;'
        f'letter-spacing:0.09em;margin-bottom:4px;">COACHING HINT</div>'
        f'<div style="font-size:0.92em;color:#b0cce0;line-height:1.5;">'
        f'{hint_text}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def _build_puzzle_phases(puzzle: dict) -> list[dict] | None:
    """
    Build a list of player-turn phases for a multi-move puzzle.
    Returns a 1-element list (single move) or 2-element list (two-move sequence).
    Returns None on any error so the caller can fall back to single-phase mode.
    """
    try:
        board0 = chess.Board(puzzle["fen"])
    except Exception:
        return None
    if not puzzle.get("best_move_san"):
        return None
    try:
        mv0 = board0.parse_san(puzzle["best_move_san"])
    except Exception:
        return None
    best_uci0 = mv0.uci()[:4]

    board_after_p1 = board0.copy()
    board_after_p1.push(mv0)

    if board_after_p1.is_game_over():
        return [_make_phase(board0, best_uci0, puzzle["best_move_san"],
                            puzzle.get("eval_before", 0.0),
                            puzzle.get("eval_after", 0.0), None)]

    try:
        followup = get_followup_lines(board_after_p1.fen(), n_plies=3)
    except Exception:
        return None

    fmoves = followup.get("moves", [])
    fevs   = followup.get("evals", [])

    # Engine's response after player's first move
    engine_resp0 = None
    if fmoves:
        try:
            eng_mv0 = board_after_p1.parse_san(fmoves[0])
            eng_uci0 = eng_mv0.uci()[:4]
            engine_resp0 = {
                "uci":     eng_uci0,
                "san":     fmoves[0],
                "from_sq": eng_uci0[:2],
                "to_sq":   eng_uci0[2:4],
                "effects": _compute_move_effects(board_after_p1, eng_mv0),
            }
        except Exception:
            engine_resp0 = None

    phase0 = _make_phase(
        board0, best_uci0, puzzle["best_move_san"],
        puzzle.get("eval_before", 0.0),
        puzzle.get("eval_after", 0.0),
        engine_resp0,
    )

    if not engine_resp0 or len(fmoves) < 2:
        return [phase0]

    # Build position after engine's first response
    try:
        board_after_eng1 = board_after_p1.copy()
        board_after_eng1.push(board_after_p1.parse_san(fmoves[0]))
    except Exception:
        return [phase0]

    if board_after_eng1.is_game_over():
        return [phase0]

    # Phase 1: player's second move
    try:
        mv1 = board_after_eng1.parse_san(fmoves[1])
        best_uci1 = mv1.uci()[:4]
    except Exception:
        return [phase0]

    ev_before1 = fevs[0] if len(fevs) > 0 else 0.0
    ev_after1  = fevs[1] if len(fevs) > 1 else 0.0

    # Optional engine response after player's second move
    board_after_p2 = board_after_eng1.copy()
    board_after_p2.push(mv1)
    engine_resp1 = None
    if len(fmoves) >= 3 and not board_after_p2.is_game_over():
        try:
            eng_mv1 = board_after_p2.parse_san(fmoves[2])
            eng_uci1 = eng_mv1.uci()[:4]
            engine_resp1 = {
                "uci":     eng_uci1,
                "san":     fmoves[2],
                "from_sq": eng_uci1[:2],
                "to_sq":   eng_uci1[2:4],
                "effects": _compute_move_effects(board_after_p2, eng_mv1),
            }
        except Exception:
            engine_resp1 = None

    phase1 = _make_phase(
        board_after_eng1, best_uci1, fmoves[1],
        ev_before1, ev_after1, engine_resp1,
    )
    return [phase0, phase1]


# ── Training tab ("Through the Rankings") ─────────────────────────────────


def _ttr_get_username() -> str:
    """Return the username for curriculum progress tracking."""
    return st.session_state.get("profile_username_built", "")


def _ttr_get_rating() -> int | None:
    """Return player's Chess.com rapid rating from profile, or None."""
    profile = st.session_state.get("profile_data")
    if profile:
        r = profile.get("chess_com_rating") or profile.get("rapid_rating")
        if r:
            return int(r)
    return None


def _render_ttr_stages():
    """Render the stage selection view — 8 stage cards."""
    st.markdown(
        '<p style="text-align:center;color:#7a9ab0;font-size:0.88em;margin:12px 0 6px;">'
        'Through the Rankings — a structured curriculum from beginner to master</p>'
        '<p style="text-align:center;color:#5a7a8a;font-size:0.78em;margin:0 0 18px;">'
        'Work through curated lessons, walkthroughs, and puzzles stage by stage. '
        'To study a specific concept in depth, switch to <strong style="color:#8ab0c8;">Coaching</strong>.</p>',
        unsafe_allow_html=True,
    )

    rating = _ttr_get_rating()
    recommended = get_stage_for_rating(rating) if rating else 1
    username = _ttr_get_username()

    if not rating:
        st.info("Build your profile from the **Dashboard** to get personalised stage recommendations.")

    # ── Recommended For You panel ────────────────────────────────────────────
    profile_data = st.session_state.get("profile_data")
    profile_summaries = st.session_state.get("profile_summaries")
    rec_modules = get_recommended_modules(profile_data, profile_summaries, rating)
    if rec_modules:
        st.markdown(
            '<div style="background:#0d1525;border:1px solid #1a3a5a;border-radius:10px;'
            'padding:16px 20px;margin-bottom:20px;">'
            '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">'
            '<span style="font-size:1.05em;">🎯</span>'
            '<span style="font-size:0.88em;font-weight:700;color:#e2c97e;letter-spacing:0.04em;">'
            'RECOMMENDED FOR YOU</span>'
            '</div>'
            '<p style="font-size:0.8em;color:#7a9ab0;margin:0 0 12px;">'
            'Based on your games — these modules target your weakest areas.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
        for rec in rec_modules:
            reason_colors = {
                "priority focus area": ("#b39ddb", "#2a1a40"),
                "weak tactics": ("#ffb74d", "#3a2a10"),
                "weak endgame": ("#4fc3f7", "#0a2a40"),
                "weak middlegame": ("#4fc3f7", "#0a2a40"),
                "weak opening prep": ("#4fc3f7", "#0a2a40"),
                "weak piece activity": ("#4fc3f7", "#0a2a40"),
                "weak consistency": ("#4fc3f7", "#0a2a40"),
            }
            badge_fg, badge_bg = reason_colors.get(rec["reason"], ("#81c784", "#0a2a1a"))
            st.markdown(
                f'<div style="background:#111827;border:1px solid #1e2e3e;border-left:3px solid {badge_fg};'
                f'border-radius:8px;padding:12px 16px;margin-top:-12px;margin-bottom:8px;'
                f'display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
                f'<div style="flex:1;min-width:200px;">'
                f'<span style="font-size:0.92em;font-weight:600;color:#cce0f4;">{rec["title"]}</span>'
                f'<span style="font-size:0.78em;color:#5a8ab0;margin-left:8px;">Stage {rec["stage"]} · {rec["concept"]}</span>'
                f'</div>'
                f'<span style="font-size:0.7em;font-weight:600;color:{badge_fg};background:{badge_bg};'
                f'padding:2px 8px;border-radius:4px;text-transform:uppercase;">{rec["reason"]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.button(
                f"Start → {rec['title']}", key=f"ttr_rec_{rec['module_id']}",
                use_container_width=True,
            ):
                mod = get_module(rec["module_id"])
                if mod:
                    puzzles = build_module_puzzles(mod, profile_summaries)
                    st.session_state.active_module = {
                        "stage": rec["stage"],
                        "module_id": rec["module_id"],
                        "title": mod["title"],
                        "concept": mod["concept"],
                        "step": 0,
                        "puzzles": puzzles,
                        "results": [],
                        "walkthrough_step": 0,
                    }
                    st.rerun()

        st.markdown('<div style="margin-bottom:12px;"></div>', unsafe_allow_html=True)

    for stage_num, stage in CURRICULUM.items():
        completed, total = db.get_stage_completion(username, stage_num)
        pct = round(100 * completed / total) if total else 0
        is_rec = stage_num == recommended

        border_color = "#5a7ac8" if is_rec else "#1e2e3e"
        bg = "#111d30" if is_rec else "#111827"

        rec_badge = (
            '&nbsp;<span style="background:#e2c97e;color:#111;padding:2px 8px;'
            'border-radius:4px;font-size:0.72em;font-weight:700;">RECOMMENDED</span>'
            if is_rec else ""
        )

        progress_bar = ""
        if total:
            bar_color = "#81c784" if pct == 100 else "#4a8aba"
            progress_bar = (
                f'<div style="margin-top:8px;height:4px;background:#1e2e3e;border-radius:2px;overflow:hidden;">'
                f'<div style="width:{pct}%;height:100%;background:{bar_color};border-radius:2px;"></div></div>'
                f'<div style="font-size:0.72em;color:#5a7a8a;margin-top:3px;">'
                f'{completed}/{total} modules completed</div>'
            )

        st.markdown(
            f'<div style="background:{bg};border:1px solid {border_color};border-radius:10px;'
            f'padding:16px 20px;margin-bottom:10px;">'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
            f'<span style="font-size:1.3em;font-weight:800;color:#5a7ac8;">Stage {stage_num}</span>'
            f'<span style="font-size:1.05em;font-weight:700;color:#cce0f4;">{stage["name"]}</span>'
            f'<span style="font-size:0.78em;color:#5a8ab0;border:1px solid #2e4e72;'
            f'border-radius:4px;padding:1px 7px;">{stage["rating_band"]}</span>'
            f'{rec_badge}'
            f'</div>'
            f'<p style="color:#8aaac8;font-size:0.88em;margin:6px 0 0;">{stage["description"]}</p>'
            f'{progress_bar}'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.button(
            f"Open Stage {stage_num}", key=f"ttr_stage_{stage_num}",
            use_container_width=True,
        ):
            st.session_state.ttr_selected_stage = stage_num
            st.rerun()


def _render_ttr_modules(stage_num: int):
    """Render the module list for a given stage."""
    stage = CURRICULUM.get(stage_num)
    if not stage:
        st.session_state.pop("ttr_selected_stage", None)
        st.rerun()
        return

    if st.button("← Back to Stages", key="ttr_back_stages"):
        st.session_state.pop("ttr_selected_stage", None)
        st.rerun()

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">'
        f'<span style="font-size:1.3em;font-weight:800;color:#5a7ac8;">Stage {stage_num}</span>'
        f'<span style="font-size:1.15em;font-weight:700;color:#cce0f4;">{stage["name"]}</span>'
        f'<span style="font-size:0.78em;color:#5a8ab0;border:1px solid #2e4e72;'
        f'border-radius:4px;padding:1px 7px;">{stage["rating_band"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    username = _ttr_get_username()
    progress = db.get_curriculum_progress(username)

    # Build set of recommended module IDs for this player
    _rec_modules = get_recommended_modules(
        st.session_state.get("profile_data"),
        st.session_state.get("profile_summaries"),
        _ttr_get_rating(),
    )
    _rec_ids = {r["module_id"]: r["reason"] for r in _rec_modules}

    for mod in stage["modules"]:
        mid = mod["id"]
        prog = progress.get(mid)
        done = prog and prog["completed"]

        icon = "✓" if done else "○"
        icon_color = "#81c784" if done else "#3a5a7a"
        score_text = ""
        if prog:
            score_text = (
                f'<span style="font-size:0.78em;color:#5a8ab0;margin-left:8px;">'
                f'{prog["best_score"]}/{prog["total"]} best</span>'
            )

        rec_badge = ""
        if mid in _rec_ids:
            rec_badge = (
                f'<span style="font-size:0.68em;font-weight:600;color:#e2c97e;background:#2a2510;'
                f'padding:2px 7px;border-radius:4px;margin-left:8px;">🎯 FOR YOU</span>'
            )

        st.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;'
            f'background:#111827;border:1px solid {"#3a4a2a" if mid in _rec_ids else "#1e2e3e"};border-radius:8px;'
            f'padding:12px 16px;margin-bottom:8px;">'
            f'<span style="font-size:1.2em;font-weight:700;color:{icon_color};">{icon}</span>'
            f'<div style="flex:1;">'
            f'<div style="font-size:0.95em;font-weight:600;color:#cce0f4;">{mod["title"]}{rec_badge}</div>'
            f'<div style="font-size:0.78em;color:#5a8ab0;">{mod["concept"]}{score_text}</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        btn_label = "Retry" if done else "Start"
        if st.button(f"{btn_label} → {mod['title']}", key=f"ttr_mod_{mid}",
                     use_container_width=True):
            puzzles = build_module_puzzles(
                mod,
                st.session_state.get("profile_summaries"),
            )
            st.session_state.active_module = {
                "stage": stage_num,
                "module_id": mid,
                "title": mod["title"],
                "concept": mod["concept"],
                "step": 0,
                "puzzles": puzzles,
                "results": [],
                "walkthrough_step": 0,
            }
            st.rerun()


def _render_ttr_walkthrough(walkthrough: dict, step: int):
    """Render walkthrough step: static SVG board + annotation."""
    board = chess.Board(walkthrough["fen"])
    last_move = None
    for i, move_san in enumerate(walkthrough["moves"][:step]):
        mv = board.parse_san(move_san)
        if i == step - 1:
            last_move = mv
        board.push(mv)

    orientation = chess.WHITE if walkthrough["player_color"] == "white" else chess.BLACK
    svg = chess.svg.board(board, lastmove=last_move, orientation=orientation, size=400)
    b64 = base64.b64encode(svg.encode()).decode()

    st.markdown(
        f'<div style="text-align:center;margin-bottom:12px;">'
        f'<img src="data:image/svg+xml;base64,{b64}" style="max-width:400px;border-radius:8px;">'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Annotation for current step
    annotations = walkthrough.get("annotations", [])
    if step < len(annotations):
        st.markdown(
            f'<div style="background:#0d1525;border:1px solid #1e2e4a;border-radius:10px;'
            f'padding:14px 18px;margin-bottom:12px;">'
            f'<p style="color:#c0d0e0;font-size:0.92em;line-height:1.6;margin:0;">'
            f'{annotations[step]}</p></div>',
            unsafe_allow_html=True,
        )

    # Nav buttons
    total_steps = len(walkthrough["moves"])
    cols = st.columns([1, 1])
    with cols[0]:
        if step > 0 and st.button("← Back", key="ttr_wt_back"):
            st.session_state.active_module["walkthrough_step"] -= 1
            st.rerun()
    with cols[1]:
        if step < total_steps:
            if st.button("Next →", key="ttr_wt_next"):
                st.session_state.active_module["walkthrough_step"] += 1
                st.rerun()
        else:
            if st.button("Continue to Puzzles →", key="ttr_wt_done", type="primary"):
                st.session_state.active_module["step"] = 2
                st.rerun()


def _render_ttr_module_flow():
    """Render the active training module: lesson → walkthrough → puzzles → summary."""
    mod_state = st.session_state.active_module
    stage_num = mod_state["stage"]
    mid = mod_state["module_id"]
    title = mod_state["title"]
    concept = mod_state["concept"]
    step = mod_state["step"]
    puzzles = mod_state["puzzles"]
    results = mod_state["results"]
    total_puzzles = len(puzzles)

    stage = CURRICULUM.get(stage_num, {})
    module_data = get_module(mid)

    # Breadcrumb
    st.markdown(
        f'<div style="font-size:0.78em;color:#5a8ab0;margin-bottom:6px;">'
        f'Training &gt; Stage {stage_num} &gt; <strong style="color:#cce0f4;">{title}</strong></div>',
        unsafe_allow_html=True,
    )

    # ── Step 0: Lesson ────────────────────────────────────────────────────────
    if step == 0:
        if st.button("← Back to Stage", key="ttr_mod_back"):
            st.session_state.pop("active_module", None)
            st.rerun()

        st.markdown(
            f'<h3 style="color:#cce0f4;margin:4px 0 16px;font-size:1.4em;">{title}</h3>',
            unsafe_allow_html=True,
        )

        # Lesson content — rating-aware, cached per module
        lesson_key = f"ttr_lesson_{mid}"
        db_key = f"ttr:{mid}"

        if lesson_key not in st.session_state:
            saved = db.get_lesson(db_key)
            if saved:
                st.session_state[lesson_key] = saved

        lesson_area = st.empty()
        if lesson_key not in st.session_state:
            if _api_limit_reached():
                return
            _count_api_call()
            with lesson_area.container():
                _render_lesson_loading_card(concept)
            rating_band = stage.get("rating_band", "1000–1200")
            st.session_state[lesson_key] = generate_ranked_lesson(
                concept, rating_band,
            )
            db.save_lesson(db_key, st.session_state[lesson_key])

        with lesson_area.container():
            _lt, _, _ = parse_lesson_diagrams(st.session_state[lesson_key])
            _tk = _extract_takeaway(_lt)
            _, _lc, _ = st.columns([1, 6, 1])
            with _lc:
                if _tk:
                    _render_takeaway_card(_tk)
                st.markdown(_lt)

        st.markdown("---")
        if module_data and module_data.get("walkthrough"):
            if st.button("Continue to Key Position →", key="ttr_to_wt", type="primary"):
                st.session_state.active_module["step"] = 1
                st.rerun()
        elif total_puzzles > 0:
            if st.button("Continue to Puzzles →", key="ttr_to_puz", type="primary"):
                st.session_state.active_module["step"] = 2
                st.rerun()
        else:
            st.info("No practice positions available for this module yet.")
            if st.button("← Back to Stage", key="ttr_mod_back_nopuz"):
                st.session_state.pop("active_module", None)
                st.rerun()
        return

    # ── Step 1: Walkthrough ───────────────────────────────────────────────────
    if step == 1:
        wt = module_data.get("walkthrough") if module_data else None
        if not wt:
            st.session_state.active_module["step"] = 2
            st.rerun()
            return

        st.markdown(
            f'<h3 style="color:#cce0f4;margin:4px 0 4px;font-size:1.2em;">'
            f'Key Position: {title}</h3>'
            f'<p style="color:#5a8ab0;font-size:0.82em;margin-bottom:12px;">'
            f'Step through this guided example</p>',
            unsafe_allow_html=True,
        )

        wt_step = mod_state.get("walkthrough_step", 0)
        _render_ttr_walkthrough(wt, wt_step)
        return

    # ── Steps 2..N+1: Puzzles ─────────────────────────────────────────────────
    if total_puzzles > 0 and step >= 2 and step < 2 + total_puzzles:
        puz_idx = step - 2
        puzzle = puzzles[puz_idx]
        cls = puzzle.get("classification", "")

        color_cap = puzzle.get("player_color", "white").capitalize()
        pz_accent = "#e2c97e" if puzzle.get("player_color") == "white" else "#90aec4"
        pz_icon = "&#9812;" if puzzle.get("player_color") == "white" else "&#9818;"

        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap;">'
            f'<span style="color:#a0bccc;font-size:0.9em;font-weight:600;">'
            f'Training: <b style="color:#cce0f4;">{title}</b></span>'
            f'&nbsp;·&nbsp;'
            f'<span style="color:#a0bccc;font-size:0.82em;">Puzzle {puz_idx + 1} / {total_puzzles}</span>'
            + (f'&nbsp;{classification_badge(cls)}' if cls else '')
            + '</div>'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
            f'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;">'
            f'Find the best move for&nbsp;<span style="color:{pz_accent};">{pz_icon}&nbsp;{color_cap}</span>'
            f'</div></div>'
            f'<div style="font-size:0.78em;color:#607d8b;margin-bottom:8px;">'
            f'Click or drag a piece to its destination</div>',
            unsafe_allow_html=True,
        )

        # Lazy phase computation
        if puzzle.get("phases") is None:
            st.markdown(
                '<div style="display:flex;flex-direction:column;align-items:center;'
                'justify-content:center;padding:80px 0;gap:16px;">'
                '<div style="width:40px;height:40px;border:4px solid #1e2e3e;'
                'border-top:4px solid #5a9ac0;border-radius:50%;'
                'animation:spin 0.8s linear infinite;"></div>'
                '<div style="font-size:0.95em;color:#a0bccc;font-weight:600;">'
                'Building puzzle sequence\u2026</div>'
                '</div>'
                '<style>@keyframes spin{to{transform:rotate(360deg);}}</style>',
                unsafe_allow_html=True,
            )
            try:
                puzzle["phases"] = _build_puzzle_phases(puzzle)
            except Exception:
                puzzle["phases"] = None
            st.rerun()

        # Hint / Show Move state
        reveal_now = st.session_state.pop(f"_reveal_ttr_{puz_idx}", False)
        has_hint = bool(puzzle.get("hint"))

        # Interactive board
        st.components.v1.html(
            _interactive_board_html(
                fen=puzzle["fen"],
                best_move_san=puzzle["best_move_san"],
                eval_before=puzzle["eval_before"],
                eval_after=puzzle["eval_after"],
                player_color=puzzle["player_color"],
                puzzle_idx=-1,
                phases=puzzle.get("phases"),
                reveal_solution=reveal_now,
                highlight_hint=(has_hint and not reveal_now),
            ),
            height=_board_iframe_height(),
            scrolling=False,
        )

        # Coaching hint → Show Move
        if puzzle.get("hint"):
            _render_hint_card(puzzle["hint"])
            if st.button("▶ Show Move", key=f"ttr_showmove_{puz_idx}", use_container_width=True):
                st.session_state[f"_reveal_ttr_{puz_idx}"] = True
                st.rerun()
        else:
            if st.button("💡 Get Hint", key=f"ttr_hint_{puz_idx}", use_container_width=True):
                if not _api_limit_reached():
                    _count_api_call()
                    with st.spinner("Thinking\u2026"):
                        try:
                            puzzle["hint"] = generate_puzzle_hint(
                                puzzle["fen"],
                                puzzle["best_move_san"],
                                puzzle["player_color"],
                                puzzle.get("classification", ""),
                            )
                        except Exception:
                            puzzle["hint"] = "Focus on piece coordination and look for tactical opportunities."
                    st.rerun()

        # Self-report result buttons
        st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
        got_col, miss_col = st.columns(2)
        with got_col:
            if st.button("✓ Got it", key=f"ttr_got_{puz_idx}", type="primary", use_container_width=True):
                st.session_state.active_module["results"].append(True)
                st.session_state.active_module["step"] += 1
                st.rerun()
        with miss_col:
            if st.button("✗ Missed it", key=f"ttr_miss_{puz_idx}", use_container_width=True):
                st.session_state.active_module["results"].append(False)
                st.session_state.active_module["step"] += 1
                st.rerun()
        return

    # ── Summary step ──────────────────────────────────────────────────────────
    n_correct = sum(1 for r in results if r)
    pct = round(100 * n_correct / total_puzzles) if total_puzzles else 0

    st.markdown(
        '<h3 style="color:#cce0f4;text-align:center;margin:16px 0 8px;">Module Complete!</h3>',
        unsafe_allow_html=True,
    )

    if pct == 100:
        score_bg, score_border, score_color = "#0d1f12", "#2a5a32", "#81c784"
    elif pct >= 50:
        score_bg, score_border, score_color = "#0d1525", "#1e3a5a", "#4fc3f7"
    else:
        score_bg, score_border, score_color = "#1f1200", "#5a3500", "#ffb74d"

    st.markdown(
        f'<div style="background:{score_bg};border:1px solid {score_border};'
        f'border-radius:10px;padding:12px;text-align:center;margin-bottom:10px;">'
        f'<div style="font-size:2.5em;font-weight:800;color:{score_color};">'
        f'{n_correct}/{total_puzzles}</div>'
        f'<div style="font-size:0.88em;color:#a0bccc;margin-top:4px;">'
        f'{pct}% correct</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Per-puzzle checklist
    for i, (puz, res) in enumerate(zip(puzzles, results)):
        cls = puz.get("classification", "")
        icon = "✓" if res else "✗"
        icon_col = "#81c784" if res else "#e57373"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;'
            f'padding:8px 12px;border-bottom:1px solid #1a2535;">'
            f'<span style="font-size:1.1em;font-weight:700;color:{icon_col};">{icon}</span>'
            f'<span style="color:#cce0f4;font-size:0.9em;">Puzzle {i + 1}</span>'
            + (f'&nbsp;{classification_badge(cls)}' if cls else '')
            + f'</div>',
            unsafe_allow_html=True,
        )

    # Save progress to DB
    username = _ttr_get_username()
    if total_puzzles > 0:
        db.save_module_progress(username, mid, n_correct, total_puzzles)

    st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)

    # Action buttons
    retry_col, back_col, next_col = st.columns(3)
    with retry_col:
        if st.button("↺ Retry Module", key="ttr_retry", use_container_width=True):
            new_puzs = build_module_puzzles(
                module_data or {},
                st.session_state.get("profile_summaries"),
            )
            st.session_state.active_module.update({
                "step": 0, "results": [], "puzzles": new_puzs,
                "walkthrough_step": 0,
            })
            st.rerun()
    with back_col:
        if st.button("← Back to Stage", key="ttr_back_stage", use_container_width=True):
            st.session_state.pop("active_module", None)
            st.rerun()
    with next_col:
        # Find next module in stage
        if module_data and stage:
            mods = stage["modules"]
            cur_idx = next((i for i, m in enumerate(mods) if m["id"] == mid), -1)
            if cur_idx >= 0 and cur_idx + 1 < len(mods):
                next_mod = mods[cur_idx + 1]
                if st.button(f"Next: {next_mod['title']} →", key="ttr_next_mod",
                             use_container_width=True):
                    new_puzs = build_module_puzzles(
                        next_mod,
                        st.session_state.get("profile_summaries"),
                    )
                    st.session_state.active_module = {
                        "stage": stage_num,
                        "module_id": next_mod["id"],
                        "title": next_mod["title"],
                        "concept": next_mod["concept"],
                        "step": 0,
                        "puzzles": new_puzs,
                        "results": [],
                        "walkthrough_step": 0,
                    }
                    st.rerun()


def render_training_tab():
    """Top-level Training tab: routes between stages, modules, and active module flow."""
    if "active_module" in st.session_state:
        _render_ttr_module_flow()
    elif st.session_state.get("ttr_selected_stage"):
        _render_ttr_modules(st.session_state.ttr_selected_stage)
    else:
        _render_ttr_stages()


def render_puzzles_tab():
    # ── Hidden trigger buttons (clicked by iframe JS, hidden via JS below) ────
    _puz_ac = st.button("\u25cf\u2713", key="puz_ac")   # correct → advance
    _puz_aw = st.button("\u25cf\u2717", key="puz_aw")   # skip/wrong → advance

    # ── Require profile summaries ─────────────────────────────────────────────
    if "profile_summaries" not in st.session_state:
        username = st.session_state.get("profile_username", "")
        saved = db.load_profile(username)
        if saved:
            _, summaries, _ = saved
            st.session_state.profile_summaries = summaries

    if not st.session_state.get("profile_summaries"):
        st.info("Build your profile from the **Dashboard** to unlock puzzles from your games.")
        if st.button("Go to Dashboard", key="puz_to_dash"):
            st.session_state.navigate_to_dashboard = True
            st.rerun()
        return

    # ── Build / restore queue ─────────────────────────────────────────────────
    if "puzzle_queue" not in st.session_state:
        st.session_state.puzzle_queue = _build_puzzle_queue()
        st.session_state.puzzle_idx   = 0

    queue = st.session_state.puzzle_queue
    if not queue:
        st.markdown(
            '<div style="text-align:center;padding:32px 0;color:#90aec4;">'
            'No puzzles found. Build your profile with more games to generate puzzles.</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Concept filter dropdown ────────────────────────────────────────────
    if "_puzzle_concept_list" not in st.session_state:
        _detected: set[str] = set()
        for _pq in queue:
            _pq_fen = _pq.get("fen", "")
            _pq_best = _pq.get("best_move_san", "")
            _pq_color = _pq.get("player_color", "white")
            if _pq_fen and _pq_best:
                for _pc_cat, _pc_names in CONCEPT_LIBRARY.items():
                    for _pc_name in _pc_names:
                        if _pc_name in _THEORY_ONLY_CONCEPTS:
                            continue
                        try:
                            if _position_has_concept(_pq_fen, _pc_name, _pq_best, _pq_color):
                                _detected.add(_pc_name)
                        except Exception:
                            pass
        st.session_state._puzzle_concept_list = sorted(_detected)

    _concept_options = ["All"] + st.session_state._puzzle_concept_list
    _prev_filter = st.session_state.get("puzzle_concept_filter", "All")
    _puz_filter = st.selectbox(
        "Filter by concept", _concept_options,
        index=_concept_options.index(_prev_filter) if _prev_filter in _concept_options else 0,
        key="_puzzle_concept_filter_widget",
    )
    if _puz_filter != _prev_filter:
        st.session_state.puzzle_concept_filter = _puz_filter
        st.session_state.puzzle_idx = 0
        st.rerun()
    st.session_state.puzzle_concept_filter = _puz_filter

    # Apply filter
    if _puz_filter != "All":
        queue = [
            p for p in queue
            if p.get("fen") and p.get("best_move_san") and
            _position_has_concept(p["fen"], _puz_filter, p["best_move_san"], p.get("player_color", "white"))
        ]
        if not queue:
            st.info(f"No puzzles match **{_puz_filter}**. Try a different concept.")
            return

    idx    = min(st.session_state.get("puzzle_idx", 0), len(queue) - 1)
    puzzle = queue[idx]

    # ── Session stats bar ─────────────────────────────────────────────────────
    _pz_solved = st.session_state.get("puzzles_solved_today", 0)
    _pz_correct = st.session_state.get("puzzle_correct_today", 0)
    _pz_acc = round(100 * _pz_correct / _pz_solved) if _pz_solved > 0 else 0
    _pz_streak = st.session_state.get("puzzle_streak", 0)
    _pz_best = st.session_state.get("puzzle_best_streak", 0)
    _pz_stat_style = (
        'display:inline-flex;flex-direction:column;align-items:center;'
        'padding:6px 16px;'
    )
    st.markdown(
        f'<div style="display:flex;justify-content:center;gap:4px;'
        f'background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
        f'padding:8px 0;margin-bottom:12px;">'
        f'<div style="{_pz_stat_style}">'
        f'<span style="font-size:1.2em;font-weight:800;color:#cce0f4;">{_pz_solved}</span>'
        f'<span style="font-size:0.62em;color:#5a7a8a;font-weight:700;letter-spacing:0.06em;">SOLVED</span></div>'
        f'<div style="{_pz_stat_style}">'
        f'<span style="font-size:1.2em;font-weight:800;color:{"#81c784" if _pz_acc >= 60 else "#ffb74d" if _pz_acc >= 40 else "#e57373"};">{_pz_acc}%</span>'
        f'<span style="font-size:0.62em;color:#5a7a8a;font-weight:700;letter-spacing:0.06em;">ACCURACY</span></div>'
        f'<div style="{_pz_stat_style}">'
        f'<span style="font-size:1.2em;font-weight:800;color:#e2c97e;">{_pz_streak}</span>'
        f'<span style="font-size:0.62em;color:#5a7a8a;font-weight:700;letter-spacing:0.06em;">STREAK</span></div>'
        f'<div style="{_pz_stat_style}">'
        f'<span style="font-size:1.2em;font-weight:800;color:#5a7ac8;">{_pz_best}</span>'
        f'<span style="font-size:0.62em;color:#5a7a8a;font-weight:700;letter-spacing:0.06em;">BEST</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Build multi-phase data lazily (cached in queue dict) ──────────────────
    if "phases" not in puzzle:
        st.markdown(
            '<div style="display:flex;flex-direction:column;align-items:center;'
            'justify-content:center;padding:80px 0;gap:16px;">'
            '<div style="width:40px;height:40px;border:4px solid #1e2e3e;'
            'border-top:4px solid #5a9ac0;border-radius:50%;'
            'animation:spin 0.8s linear infinite;"></div>'
            '<div style="font-size:0.95em;color:#a0bccc;font-weight:600;">'
            'Building puzzle sequence\u2026</div>'
            '</div>'
            '<style>@keyframes spin{to{transform:rotate(360deg);}}</style>',
            unsafe_allow_html=True,
        )
        try:
            puzzle["phases"] = _build_puzzle_phases(puzzle)
        except Exception:
            puzzle["phases"] = None
        st.rerun()

    # ── Process trigger results (moved before columns — both end with rerun) ──
    if _puz_ac:
        new_streak = st.session_state.get("puzzle_streak", 0) + 1
        new_recent = (st.session_state.get("puzzle_recent", []) + [True])[-10:]
        db.update_puzzle_result(True, new_streak, new_recent)
        st.session_state.puzzle_streak      = new_streak
        st.session_state.puzzle_best_streak = max(
            st.session_state.get("puzzle_best_streak", 0), new_streak
        )
        st.session_state.puzzle_recent = new_recent
        _phase = puzzle.get("phase", "middlegame")
        _ppr = st.session_state.setdefault("puzzle_phase_results", {})
        _ppr.setdefault(_phase, []).append(True)
        db.update_puzzle_phase(_phase, True)
        st.session_state.puzzles_solved_today = st.session_state.get("puzzles_solved_today", 0) + 1
        st.session_state.puzzle_correct_today = st.session_state.get("puzzle_correct_today", 0) + 1
        st.session_state.puzzle_explanation_pending = True
        st.session_state.puzzle_explanation_correct = True
        _check_puzzle_achievements()
        _increment_daily_goal("puzzles")
        st.rerun()
    if _puz_aw:
        new_recent = (st.session_state.get("puzzle_recent", []) + [False])[-10:]
        db.update_puzzle_result(False, 0, new_recent)
        st.session_state.puzzle_streak = 0
        st.session_state.puzzle_recent = new_recent
        _phase = puzzle.get("phase", "middlegame")
        _ppr = st.session_state.setdefault("puzzle_phase_results", {})
        _ppr.setdefault(_phase, []).append(False)
        db.update_puzzle_phase(_phase, False)
        st.session_state.puzzles_solved_today = st.session_state.get("puzzles_solved_today", 0) + 1
        st.session_state.puzzle_explanation_pending = True
        st.session_state.puzzle_explanation_correct = False
        _increment_daily_goal("puzzles")
        st.rerun()

    # ── Pre-compute display variables ─────────────────────────────────────────
    cls         = puzzle["classification"]
    cls_color   = {"blunder": "#e53935", "mistake": "#fb8c00"}.get(cls, "#81c784")
    phase_str   = puzzle["phase"].capitalize() if puzzle["phase"] else ""
    color_cap   = puzzle["player_color"].capitalize()
    pz_accent   = "#e2c97e" if puzzle["player_color"] == "white" else "#90aec4"
    pz_icon     = "&#9812;" if puzzle["player_color"] == "white" else "&#9818;"
    n_phases    = len(puzzle.get("phases") or [0])
    phase_badge = (
        f'<span style="background:#1a2a4022;color:#5a8ab0;border:1px solid #253a5555;'
        f'font-size:0.72em;font-weight:700;border-radius:4px;padding:2px 8px;">'
        f'{n_phases}-MOVE</span>&nbsp;'
        if n_phases > 1 else ''
    )
    focus_reason = puzzle.get("focus_reason", "")
    reveal_now = st.session_state.pop(f"_reveal_puzzle_{idx}", False)
    has_hint = bool(puzzle.get("hint"))
    showing_explanation = st.session_state.get("puzzle_explanation_pending", False)

    # ── Blindfold toggle ────────────────────────────────────────────────────
    _bf_col1, _bf_col2 = st.columns([6, 1])
    with _bf_col2:
        _blindfold_on = st.toggle("\U0001f441 Blindfold", key="blindfold_mode")

    # ── Two-column layout: board left, panel right ────────────────────────────
    board_col, panel_col = st.columns([3, 2], gap="small")

    # ── LEFT COLUMN: board only ───────────────────────────────────────────────
    with board_col:
        if _blindfold_on and not st.session_state.get("_blindfold_reveal"):
            # Text-based piece list
            _bf_text = _blindfold_piece_list(puzzle["fen"])
            _bf_side = "White" if " w " in puzzle["fen"] else "Black"
            st.markdown(
                f'<div style="background:#111827;border:1px solid #1e2e3e;border-radius:12px;'
                f'padding:16px 14px;min-height:260px;display:flex;flex-direction:column;'
                f'justify-content:center;align-items:center;gap:16px;">'
                f'<div style="font-size:0.72em;color:#5a7a8a;font-weight:700;letter-spacing:0.1em;">'
                f'BLINDFOLD MODE</div>'
                f'<div style="font-family:monospace;font-size:0.95em;color:#cce0f4;'
                f'line-height:1.8;text-align:center;white-space:pre-wrap;">{_bf_text}</div>'
                f'<div style="font-size:0.88em;color:#a0bccc;margin-top:8px;">'
                f'Side to move: <b>{_bf_side}</b></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.button("Show Board", key="bf_reveal", use_container_width=True):
                st.session_state._blindfold_reveal = True
                st.rerun()
        else:
            if st.session_state.get("_blindfold_reveal") and not _blindfold_on:
                st.session_state.pop("_blindfold_reveal", None)
            st.components.v1.html(
                _interactive_board_html(
                    fen=puzzle["fen"],
                    best_move_san=puzzle["best_move_san"],
                    eval_before=puzzle["eval_before"],
                    eval_after=puzzle["eval_after"],
                    player_color=puzzle["player_color"],
                    puzzle_idx=idx,
                    phases=puzzle.get("phases"),
                    reveal_solution=reveal_now,
                    highlight_hint=(has_hint and not reveal_now),
                ),
                height=_board_iframe_height(),
                scrolling=False,
            )

    # ── RIGHT COLUMN: info panel ──────────────────────────────────────────────
    with panel_col:
        # Dark card wrapper open
        st.markdown(
            '<div class="puzzle-panel" style="background:#111827;border:1px solid #1e2e3e;'
            'border-radius:12px;padding:16px;">',
            unsafe_allow_html=True,
        )

        # R1. Puzzle progress + badges + opponent/date
        focus_html = (
            f'<div style="font-size:0.72em;color:#e2c97e;background:#2a2510;'
            f'border:1px solid #4a3a10;border-radius:4px;padding:2px 8px;'
            f'display:inline-block;margin-bottom:6px;">\U0001f3af {focus_reason}</div>'
        ) if focus_reason else ""
        st.markdown(
            f'{focus_html}'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap;">'
            f'<span style="color:#a0bccc;font-size:0.82em;">'
            f'Puzzle <b style="color:#cce0f4;">{idx + 1}</b> / {len(queue)}</span>'
            f'&nbsp;\u00b7&nbsp;'
            f'{phase_badge}'
            f'<span style="background:{cls_color}22;color:{cls_color};border:1px solid {cls_color}55;'
            f'font-size:0.72em;font-weight:700;border-radius:4px;padding:2px 8px;">{cls.upper()}</span>'
            f'</div>'
            f'<div style="font-size:0.78em;color:#a0bccc;margin-bottom:8px;">'
            f'{phase_str + " \u00b7 " if phase_str else ""}'
            f'vs {puzzle["opponent"]} \u00b7 {puzzle["date"]}</div>',
            unsafe_allow_html=True,
        )

        # R2. "Find the best move" prompt
        st.markdown(
            f'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;margin-bottom:10px;">'
            f'Find the best move for&nbsp;'
            f'<span style="color:{pz_accent};">{pz_icon}&nbsp;{color_cap}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # R4. Compact stats grid (same 4 stats as Dashboard)
        _prof = st.session_state.get("profile_data", {})
        _sums = st.session_state.get("profile_summaries", [])
        _rec = _prof.get("record", {})
        _rec_str = f"{_rec.get('wins', 0)}W {_rec.get('losses', 0)}L {_rec.get('draws', 0)}D"
        _mist_pg = _prof.get("mistakes_per_game", 0)
        _blun_pg = _prof.get("blunders_per_game", 0)
        # Best skill
        def _puz_skill_scores(sums):
            def _sa(vals):
                c = [v for v in vals if v is not None]
                return sum(c) / len(c) if c else 50.0
            if not sums:
                return {}
            n = len(sums)
            scores = {
                "Opening Prep": round(_sa([s.get("opening_accuracy") for s in sums])),
                "Middlegame":   round(_sa([s.get("middlegame_accuracy") for s in sums])),
                "Endgame":      round(_sa([s.get("endgame_accuracy") for s in sums])),
                "Tactics":      max(0, min(100, round(100
                                - (sum(s.get("blunders", 0) for s in sums) / n) * 8
                                - (sum(s.get("mistakes", 0) for s in sums) / n) * 4))),
            }
            accs = [s.get("player_accuracy", 50) for s in sums if s.get("player_accuracy") is not None]
            if len(accs) >= 2:
                import statistics as _st_mod
                scores["Consistency"] = max(0, min(100, round(100 - _st_mod.stdev(accs) * 2.5)))
            elif accs:
                scores["Consistency"] = round(accs[0])
            return scores
        _pskills = _puz_skill_scores(_sums)
        if _pskills:
            _bs_name = max(_pskills, key=_pskills.get)
            _bs_val = str(_pskills[_bs_name])
            _bs_label = _bs_name.upper()
        else:
            _bs_val = "\u2014"
            _bs_label = "BEST SKILL"
        def _mini_stat(label, value, color="#cce0f4"):
            return (
                f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
                f'padding:8px 6px;text-align:center;">'
                f'<div style="font-size:1.1em;font-weight:800;color:{color};">{value}</div>'
                f'<div style="font-size:0.62em;color:#7a9ab0;font-weight:600;letter-spacing:0.05em;'
                f'margin-top:2px;">{label}</div></div>'
            )
        st.markdown(
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;">'
            f'{_mini_stat("RECORD", _rec_str, "#cce0f4")}'
            f'{_mini_stat("MISTAKES / GAME", _mist_pg, "#fff176")}'
            f'{_mini_stat("BLUNDERS / GAME", _blun_pg, "#ef5350")}'
            f'{_mini_stat(_bs_label, _bs_val, "#e2c97e")}'
            f'</div>',
            unsafe_allow_html=True,
        )

        # R5. Divider
        st.markdown(
            '<div style="height:1px;background:#1e2e3e;margin:6px 0 10px;"></div>',
            unsafe_allow_html=True,
        )

        # R6. Explanation (if pending) OR hint/show-move controls
        if showing_explanation:
            was_correct = st.session_state.get("puzzle_explanation_correct", True)
            if not puzzle.get("explanation"):
                if _api_limit_reached():
                    puzzle["explanation"] = f"The best move was {puzzle['best_move_san']}."
                else:
                    _count_api_call()
                    with st.spinner("Generating explanation\u2026"):
                        try:
                            puzzle["explanation"] = generate_puzzle_explanation(
                                puzzle["fen"],
                                puzzle["best_move_san"],
                                puzzle["player_color"],
                                puzzle["classification"],
                                was_correct,
                            )
                        except Exception:
                            puzzle["explanation"] = f"The best move was {puzzle['best_move_san']}."
            border_color = "#2e7d32" if was_correct else "#b71c1c"
            bg_color = "#0d2818" if was_correct else "#1a0a0a"
            label = "CORRECT" if was_correct else "INCORRECT"
            label_color = "#81c784" if was_correct else "#ef9a9a"
            st.markdown(
                f'<div style="background:{bg_color};border:1px solid {border_color};border-radius:10px;'
                f'padding:14px 16px;margin:8px 0;">'
                f'<div style="font-size:0.72em;font-weight:700;color:{label_color};letter-spacing:0.08em;'
                f'margin-bottom:6px;">{label} \u2014 {puzzle["best_move_san"]}</div>'
                f'<div style="font-size:0.88em;color:#cce0f4;line-height:1.55;">{puzzle["explanation"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Related concept links
            if "related_concepts" not in puzzle:
                _rc_list = []
                _rc_fen = puzzle.get("fen_before") or puzzle.get("fen", "")
                _rc_best = puzzle.get("best_move_san", "")
                _rc_color = puzzle.get("player_color", "white")
                if _rc_fen and _rc_best:
                    for _rc_cat, _rc_names in CONCEPT_LIBRARY.items():
                        for _rc_name in _rc_names:
                            if _rc_name in _THEORY_ONLY_CONCEPTS:
                                continue
                            try:
                                if _position_has_concept(_rc_fen, _rc_name, _rc_best, _rc_color):
                                    _rc_list.append(_rc_name)
                                    if len(_rc_list) >= 2:
                                        break
                            except Exception:
                                pass
                        if len(_rc_list) >= 2:
                            break
                puzzle["related_concepts"] = _rc_list
            if puzzle["related_concepts"]:
                for _rc_ci, _rc_c in enumerate(puzzle["related_concepts"]):
                    if st.button(f"\U0001f4d6 {_rc_c} — Study →", key=f"puz_concept_{idx}_{_rc_ci}", use_container_width=True):
                        st.session_state.selected_concept = _rc_c
                        st.session_state.navigate_to_coaching = True
                        st.rerun()
            if st.button("Next Puzzle \u25b6", key="puz_next_expl", use_container_width=True, type="primary"):
                st.session_state.pop("puzzle_explanation_pending", None)
                st.session_state.pop("puzzle_explanation_correct", None)
                st.session_state.pop("_blindfold_reveal", None)
                st.session_state.puzzle_idx = min(idx + 1, len(queue) - 1)
                st.rerun()
        else:
            # Hint / Get Hint / Show Move
            if puzzle.get("hint"):
                _render_hint_card(puzzle["hint"])
                if st.button("\u25b6 Show Move", key=f"puz_showmove_{idx}", use_container_width=True):
                    st.session_state[f"_reveal_puzzle_{idx}"] = True
                    st.rerun()
            else:
                if st.button("\U0001f4a1 Get Hint", key=f"puz_hint_{idx}", use_container_width=True):
                    if not _api_limit_reached():
                        _count_api_call()
                        with st.spinner("Thinking\u2026"):
                            try:
                                puzzle["hint"] = generate_puzzle_hint(
                                    puzzle["fen"],
                                    puzzle["best_move_san"],
                                    puzzle["player_color"],
                                    puzzle["classification"],
                                )
                            except Exception:
                                puzzle["hint"] = "Focus on piece coordination and look for tactical opportunities."
                        st.rerun()

            # R7. Navigation (hidden during explanation)
            col_prev, col_shuf, col_skip, col_next = st.columns([1, 1.5, 1, 1])
            with col_prev:
                if st.button("\u25c0 Prev", disabled=idx == 0, key="puz_prev", use_container_width=True):
                    st.session_state.pop("puzzle_explanation_pending", None)
                    st.session_state.pop("_blindfold_reveal", None)
                    st.session_state.puzzle_idx = idx - 1
                    st.rerun()
            with col_shuf:
                if st.button("\u21c4 Shuffle", key="puz_shuffle", use_container_width=True):
                    st.session_state.pop("puzzle_explanation_pending", None)
                    st.session_state.pop("_blindfold_reveal", None)
                    random.shuffle(st.session_state.puzzle_queue)
                    st.session_state.puzzle_idx = 0
                    st.rerun()
            with col_skip:
                if st.button("\u23ed Skip", disabled=idx >= len(queue) - 1, key="puz_skip", use_container_width=True):
                    st.session_state.pop("puzzle_explanation_pending", None)
                    st.session_state.pop("puzzle_explanation_correct", None)
                    st.session_state.pop("_blindfold_reveal", None)
                    st.session_state.puzzle_idx = min(idx + 1, len(queue) - 1)
                    st.rerun()
            with col_next:
                if st.button("Next \u25b6", disabled=idx >= len(queue) - 1, key="puz_next", use_container_width=True):
                    st.session_state.pop("puzzle_explanation_pending", None)
                    st.session_state.pop("_blindfold_reveal", None)
                    st.session_state.puzzle_idx = idx + 1
                    st.rerun()

        # R8. "What was actually played?" expander
        if puzzle.get("move_san"):
            with st.expander("\U0001f4cb What was actually played?"):
                mn   = puzzle["move_number"]
                dot  = "." if puzzle["player_color"] == "white" else "\u2026"
                eb   = puzzle["eval_before"]
                ea   = puzzle["eval_after"]
                swing = ea - eb
                st.markdown(
                    f'<div style="font-family:monospace;font-size:0.9em;color:#90a4b8;padding:4px 0;">'
                    f'Move {mn}{dot} <b style="color:{cls_color};">{puzzle["move_san"]}</b> was played'
                    f' \u2014 a <b style="color:{cls_color};">{cls}</b>. '
                    f'Eval: <b style="color:#cce0f4;">{eb:+.2f}</b> \u2192 '
                    f'<b style="color:{cls_color};">{ea:+.2f}</b>'
                    f' (<b style="color:{cls_color};">{swing:+.2f}</b>)</div>',
                    unsafe_allow_html=True,
                )

        # R9. Improvement tracker (compact, in panel)
        _ppr = st.session_state.get("puzzle_phase_results", {})
        _profile = st.session_state.get("profile_data")
        _summaries = st.session_state.get("profile_summaries", [])
        if _profile or _ppr:
            _weak_phase = None
            if _summaries:
                def _pavg2(vals):
                    clean = [v for v in vals if v is not None]
                    return sum(clean) / len(clean) if clean else 50.0
                _phase_accs = {
                    "opening": _pavg2([s.get("opening_accuracy") for s in _summaries]),
                    "middlegame": _pavg2([s.get("middlegame_accuracy") for s in _summaries]),
                    "endgame": _pavg2([s.get("endgame_accuracy") for s in _summaries]),
                }
                _weak_phase = min(_phase_accs, key=_phase_accs.get)

            phase_bars_html = ""
            for phase in ("opening", "middlegame", "endgame"):
                results = _ppr.get(phase, [])
                n_total = len(results)
                n_correct = sum(results) if results else 0
                pct = round(100 * n_correct / n_total) if n_total else 0
                bar_color = "#81c784" if pct >= 70 else "#ffb74d" if pct >= 40 else "#e57373"
                is_weak = phase == _weak_phase
                weak_badge = (
                    '&ensp;<span style="font-size:0.7em;color:#e2c97e;background:#2a2510;'
                    'border-radius:3px;padding:1px 5px;">FOCUS</span>'
                ) if is_weak else ""
                label_color = "#cce0f4" if is_weak else "#8aaac8"
                count_text = f"{n_correct}/{n_total}" if n_total else "\u2014"
                phase_bars_html += (
                    f'<div style="margin-bottom:5px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">'
                    f'<span style="font-size:0.72em;font-weight:600;color:{label_color};'
                    f'text-transform:capitalize;">{phase}{weak_badge}</span>'
                    f'<span style="font-size:0.68em;color:#5a7a8a;">{count_text}</span>'
                    f'</div>'
                    f'<div style="height:5px;background:#1e2e3e;border-radius:3px;overflow:hidden;">'
                    f'<div style="width:{pct}%;height:100%;background:{bar_color};border-radius:3px;'
                    f'transition:width 0.3s;"></div>'
                    f'</div></div>'
                )

            st.markdown(
                f'<div style="margin-top:10px;">'
                f'<div style="font-size:0.68em;color:#a0bccc;font-weight:700;letter-spacing:0.06em;'
                f'margin-bottom:6px;">\U0001f4c8 IMPROVEMENT TRACKER</div>'
                f'{phase_bars_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Dark card wrapper close
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Puzzle keyboard shortcuts ─────────────────────────────────────────────
    inject_puzzle_keyboard()
    st.markdown(
        '<div style="text-align:center;font-size:0.68em;color:#3a5070;margin-top:2px;">'
        'Space: Check/Next \u00b7 H: Hint \u00b7 S: Skip</div>',
        unsafe_allow_html=True,
    )

    # ── Hide trigger buttons (injected JS runs after board render) ────────────
    st.components.v1.html(
        """<script>
(function(){
  function hide(){
    window.parent.document.querySelectorAll('button').forEach(function(b){
      var t=b.textContent.trim();
      if(t==='\u25cf\u2713'||t==='\u25cf\u2717'){
        var wrap=b.closest('[data-testid="stButton"]')||b;
        wrap.style.display='none';
      }
    });
  }
  hide(); setTimeout(hide,100); setTimeout(hide,400);
})();
</script>""",
        height=0,
    )

# ── Tab: Chess.com Profile ───────────────────────────────────────────────────

def _pawn_capture_html() -> str:
    """CSS animation: white and black kings advance toward each other, cross swords, retreat."""
    return """
<div style="text-align:center;padding:36px 0 20px;
            background:repeating-conic-gradient(#111a2e 0% 25%,#0c1021 0% 50%) 0 0/44px 44px;
            border-radius:12px;border:1px solid #1e2e4a;">
  <style>
    @keyframes wKingAdv {
      0%,100% { transform: translateX(-52px); }
      35%,65% { transform: translateX(0); }
    }
    @keyframes bKingAdv {
      0%,100% { transform: translateX(52px); }
      35%,65% { transform: translateX(0); }
    }
    @keyframes swordsGlow {
      0%,30%   { opacity:0; transform:translateY(0) scale(0.7); }
      48%,58%  { opacity:1; transform:translateY(-3px) scale(1.15); }
      72%,100% { opacity:0; transform:translateY(0) scale(0.7); }
    }
    .anim-wk { font-size:3.2em; display:inline-block; color:#f0ead6; line-height:1;
               animation:wKingAdv 3.2s ease-in-out infinite; }
    .anim-bk { font-size:3.2em; display:inline-block; color:#6a8aaa; line-height:1;
               animation:bKingAdv 3.2s ease-in-out infinite; }
    .anim-sw { font-size:1.4em; display:inline-block; color:#e2c97e; line-height:1;
               margin:0 -6px; vertical-align:middle;
               animation:swordsGlow 3.2s ease-in-out infinite; }
  </style>
  <span class="anim-wk">♔</span><span class="anim-sw">⚔</span><span class="anim-bk">♚</span>
  <div style="font-size:0.78em;color:#7a9ab0;margin-top:16px;letter-spacing:0.06em;">
    Analysing games…
  </div>
</div>"""


def _piece_rating_html(rating: int, size: str = "1.5em") -> str:
    """
    Render the 5-piece tier progression.
    Earned pieces use solid-fill symbols (♟♞♝♜♛) coloured in their tier colour.
    Unearned pieces use outline symbols (♙♘♗♖♕) in a near-invisible dark tone.
    """
    # solid-fill vs outline pairs per tier
    _SOLID   = {1: "♟", 2: "♞", 3: "♝", 4: "♜", 5: "♛"}
    _OUTLINE = {1: "♙", 2: "♘", 3: "♗", 4: "♖", 5: "♕"}
    parts = []
    tier  = PIECE_TIERS.get(rating, PIECE_TIERS[1])
    for r in range(1, 6):
        t = PIECE_TIERS[r]
        if r <= rating:
            parts.append(
                f'<span style="font-size:{size};color:{t["color"]};'
                f'text-shadow:0 0 6px {t["color"]}55;">{_SOLID[r]}</span>'
            )
        else:
            parts.append(
                f'<span style="font-size:{size};color:#253040;">{_OUTLINE[r]}</span>'
            )
    label = (
        f'<span style="font-size:0.72em;font-weight:700;color:{tier["color"]};'
        f'letter-spacing:0.04em;margin-left:8px;">'
        f'{tier["tier"].upper()}</span>'
    )
    return "".join(parts) + label


def _performance_level(blunders_pg: float, mistakes_pg: float) -> int:
    """
    Return a performance tier 1–5 (maps to PIECE_TIERS) based on error frequency.
    Uses a weighted score (blunders count double) calibrated for depth-12 analysis.
    This is more reliable than raw accuracy % which is miscalibrated at depth 12.
    """
    score = blunders_pg * 2 + mistakes_pg
    if score < 0.8:   return 5  # Expert
    if score < 2.0:   return 4  # Advanced
    if score < 4.0:   return 3  # Intermediate
    if score < 7.0:   return 2  # Developing
    return 1                    # Beginner


def _profile_overview_html(profile: dict) -> str:
    """Compact header: username, performance level, error rate, record."""
    rec  = profile.get("record", {})
    wins, losses, draws = rec.get("wins", 0), rec.get("losses", 0), rec.get("draws", 0)
    n            = profile.get("n_games", 0)
    dr           = profile.get("date_range", "")
    user         = profile.get("username", "")
    blunders_pg  = profile.get("blunders_per_game", 0.0)
    mistakes_pg  = profile.get("mistakes_per_game", 0.0)

    level       = _performance_level(blunders_pg, mistakes_pg)
    tier        = PIECE_TIERS[level]
    pieces_html = _piece_rating_html(level, "1.5em")

    err_parts = []
    if blunders_pg > 0:
        err_parts.append(f"<span style='color:#e57373;'>{blunders_pg:.1f} blunders</span>")
    if mistakes_pg > 0:
        err_parts.append(f"<span style='color:#ffb74d;'>{mistakes_pg:.1f} mistakes</span>")
    err_line = (" + ".join(err_parts) + " per game") if err_parts else "No significant errors"

    return f"""
<div style="background:#111827;border:1px solid #253450;border-radius:12px;
            padding:12px 16px 10px;margin-bottom:16px;">
  <div style="font-size:1.5em;font-weight:800;color:#cce0f4;margin-bottom:2px;">
    ♟ {user}
  </div>
  <div style="font-size:0.8em;color:#7a9ab0;margin-bottom:14px;">
    {n} games · {dr}
  </div>
  <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:10px;">
    <div>
      <div style="margin-bottom:4px;">{pieces_html}</div>
      <div style="font-size:0.75em;color:#7a9ab0;margin-top:4px;">{err_line}</div>
    </div>
    <div style="height:36px;width:1px;background:#253450;flex-shrink:0;"></div>
    <div style="display:flex;gap:10px;">
      <div style="text-align:center;">
        <div style="font-size:1.6em;font-weight:700;color:#81c784;">{wins}</div>
        <div style="font-size:0.7em;color:#7a9ab0;letter-spacing:0.05em;">WINS</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.6em;font-weight:700;color:#e57373;">{losses}</div>
        <div style="font-size:0.7em;color:#7a9ab0;letter-spacing:0.05em;">LOSSES</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:1.6em;font-weight:700;color:#aaa;">{draws}</div>
        <div style="font-size:0.7em;color:#7a9ab0;letter-spacing:0.05em;">DRAWS</div>
      </div>
    </div>
  </div>
</div>"""


_SECS_PER_GAME = {10: 0.12 * 41, 12: 0.35 * 41, 15: 1.0 * 41}  # ≈ 5 / 14 / 41 s/game
_MAX_BUILD_GAMES = 100  # cap games per profile build to limit CPU on hosted environments

_DEPTH_INFO = {
    10: "Fast scan (~5s/game) — may miss deeper tactical sequences (3+ move combos). Good for a quick overview.",
    12: "Balanced (~14s/game) — catches most tactical patterns with solid positional eval. Recommended for regular use.",
    15: "Deep (~41s/game) — catches virtually all tactics with precise eval. Best for serious analysis, significantly slower.",
}


def _game_dedup_key(headers: dict) -> tuple:
    """Dedup key from PGN headers of a fetched game."""
    return (
        headers.get("White", "?").lower(),
        headers.get("Black", "?").lower(),
        headers.get("Date", ""),
        headers.get("Result", "*"),
    )


def _summary_dedup_key(summary: dict) -> tuple:
    """Dedup key from an already-analysed game summary."""
    return (
        summary.get("white", "?").lower(),
        summary.get("black", "?").lower(),
        summary.get("date", ""),
        summary.get("result", "*"),
    )


def _estimate_analysis_time(n_months: int, depth: int) -> str:
    """
    Button label shown BEFORE games are fetched — we don't know the count yet.
    Show a per-game rate so it's always accurate regardless of game volume.
    """
    secs = _SECS_PER_GAME.get(depth, 0.35 * 41)
    return f"~{round(secs)}s/game"


def _estimate_from_game_count(n_games: int, depth: int) -> str:
    """
    Total-time estimate once we know the exact game count.
    Formats as seconds, minutes, or hours+minutes as appropriate.
    """
    total_secs = n_games * _SECS_PER_GAME.get(depth, 0.35 * 41)
    if total_secs < 90:
        return f"~{int(total_secs)}s"
    minutes = total_secs / 60.0
    if minutes < 60:
        return f"~{round(minutes)} min"
    hours   = int(minutes // 60)
    mins    = round(minutes % 60)
    if mins == 0:
        return f"~{hours}h"
    return f"~{hours}h {mins}m"


# ── Background build helpers ─────────────────────────────────────────────────

def _check_build_progress():
    """Check for an active background build job; handle completion/error.

    Returns the job dict if a build is in progress, else None.
    """
    username = st.session_state.get("_build_username")

    # Reconnect after page refresh: check _BUILD_JOBS for the active DB user
    if not username:
        try:
            active = db.get_active_user()
        except Exception:
            active = None
        if active:
            active_name = active[0] if isinstance(active, (list, tuple)) else active
            if active_name in _BUILD_JOBS:
                username = active_name
                st.session_state["_build_username"] = username

    if not username or username not in _BUILD_JOBS:
        return None

    job = _BUILD_JOBS[username]

    if job["status"] == "done":
        result = job["result"]
        st.session_state.profile_data = result["profile"]
        st.session_state.profile_summaries = result["summaries"]
        st.session_state.profile_username_built = username
        st.session_state.pop("profile_tc_filter", None)
        st.session_state.profile_build_depth = job.get("depth", 12)
        st.session_state.pop("profile_built_at", None)
        ng_key = job.get("ng_cache_key")
        if ng_key:
            st.session_state.pop(ng_key, None)
        # Clean up
        st.session_state.pop("_build_username", None)
        with _BUILD_LOCK:
            _BUILD_JOBS.pop(username, None)
        st.toast("Profile built successfully!")
        _check_achievement("profile_built")
        st.rerun()

    if job["status"] == "error":
        err = job.get("error", "Unknown error")
        st.session_state.pop("_build_username", None)
        with _BUILD_LOCK:
            _BUILD_JOBS.pop(username, None)
        st.error(f"Profile build failed: {err}")
        return None

    return job


def _render_build_banner(job: dict):
    """Render a persistent progress banner with kings animation and ETA."""
    done = job.get("done", 0)
    total = job.get("total", 1)
    status = job.get("status", "analyzing")
    eta_secs = job.get("eta_secs", 0)

    if status == "synthesizing":
        pct = 100
        status_text = "Claude is synthesizing your profile…"
        eta_text = "Almost done"
    else:
        pct = int((done / max(total, 1)) * 100)
        if eta_secs < 90:
            eta_text = f"{int(eta_secs)}s remaining" if eta_secs > 0 else "Estimating…"
        elif eta_secs < 3600:
            eta_text = f"~{math.ceil(eta_secs / 60)}m remaining"
        else:
            h = int(eta_secs // 3600)
            m = math.ceil((eta_secs % 3600) / 60)
            eta_text = f"~{h}h {m}m remaining" if m else f"~{h}h remaining"
        status_text = f"Analysing game {done} of {total}"

    label = "UPDATING YOUR PROFILE" if job.get("is_update") else "BUILDING YOUR PROFILE"

    st.markdown(
        f'<div style="background:linear-gradient(135deg,#0d253f 0%,#1a3a5c 100%);'
        f'border:1px solid #3a6ea5;border-radius:12px;padding:16px 20px;margin:8px 0 12px;">'
        # Kings animation
        f'<div style="text-align:center;padding:12px 0 8px;">'
        f'<style>'
        f'@keyframes wKBuild {{ 0%,100% {{ transform:translateX(-52px); }} 35%,65% {{ transform:translateX(0); }} }}'
        f'@keyframes bKBuild {{ 0%,100% {{ transform:translateX(52px); }} 35%,65% {{ transform:translateX(0); }} }}'
        f'@keyframes swBuild {{ 0%,30% {{ opacity:0;transform:translateY(0) scale(0.7); }}'
        f'  48%,58% {{ opacity:1;transform:translateY(-3px) scale(1.15); }}'
        f'  72%,100% {{ opacity:0;transform:translateY(0) scale(0.7); }} }}'
        f'</style>'
        f'<span style="font-size:2.6em;display:inline-block;color:#f0ead6;line-height:1;'
        f'animation:wKBuild 3.2s ease-in-out infinite;">♔</span>'
        f'<span style="font-size:1.2em;display:inline-block;color:#e2c97e;line-height:1;'
        f'margin:0 -6px;vertical-align:middle;animation:swBuild 3.2s ease-in-out infinite;">⚔</span>'
        f'<span style="font-size:2.6em;display:inline-block;color:#6a8aaa;line-height:1;'
        f'animation:bKBuild 3.2s ease-in-out infinite;">♚</span>'
        f'</div>'
        # Label
        f'<div style="text-align:center;margin-bottom:10px;">'
        f'<span style="font-size:0.85em;font-weight:700;color:#7ab4e0;letter-spacing:0.06em;">'
        f'{label}</span></div>'
        # Progress bar
        f'<div style="background:#0a1929;border-radius:6px;height:8px;overflow:hidden;margin-bottom:8px;">'
        f'<div style="background:linear-gradient(90deg,#3a6ea5,#5ba0d9);height:100%;'
        f'width:{pct}%;transition:width 0.5s ease;border-radius:6px;"></div></div>'
        # Status + ETA
        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-size:0.8em;color:#8ab4d0;">{status_text}</span>'
        f'<span style="font-size:0.8em;color:#e2c97e;font-weight:600;">{eta_text}</span>'
        f'</div>'
        # Subtitle
        f'<div style="font-size:0.75em;color:#5a7a8a;margin-top:6px;text-align:center;">'
        f'Feel free to explore other tabs — your profile will appear when ready.'
        f'</div></div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every=3)
def _build_poll():
    """Auto-polling fragment: only active while a background build is in progress."""
    username = st.session_state.get("_build_username")
    if not username or username not in _BUILD_JOBS:
        # No build in progress — do nothing (fragment stays dormant)
        return
    job = _BUILD_JOBS[username]
    if job["status"] in ("done", "error"):
        st.rerun()


def _mini_board_b64(
    fen_before: str,
    move_san: str,
    player_color: str = "white",
    size: int = 160,
    arrow_color: str = "#cc333388",
) -> str:
    """Render a chess board at fen_before with an arrow for the move (base64 SVG URI)."""
    orientation = chess.WHITE if player_color == "white" else chess.BLACK
    board = chess.Board(fen_before)
    arrows = []
    try:
        move = board.parse_san(move_san)
        arrows = [chess.svg.Arrow(move.from_square, move.to_square, color=arrow_color)]
    except Exception:
        pass
    svg = chess.svg.board(board, size=size, arrows=arrows, orientation=orientation)
    b64 = base64.b64encode(svg.encode()).decode()
    return f"data:image/svg+xml;base64,{b64}"



def _render_lesson_diagrams(diagrams: list[dict], concept: str = ""):
    """Render lesson diagrams as interactive 'Find the Move' puzzles."""
    if not diagrams:
        return
    st.markdown(
        '<div style="font-size:0.72em;color:#a0bccc;font-weight:700;'
        'letter-spacing:0.06em;margin:20px 0 10px;">FIND THE MOVE</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-size:0.85em;color:#8a9fb0;margin-bottom:12px;">'
        'Apply what you\'ve learned — find the correct move in each position.</div>',
        unsafe_allow_html=True,
    )
    concept_slug = concept.lower().replace(" ", "_")

    if len(diagrams) == 1:
        _render_single_lesson_diagram(diagrams[0], concept_slug, 0)
    else:
        tab_labels = [f"Position {i+1}" for i in range(len(diagrams))]
        tabs = st.tabs(tab_labels)
        for i, (tab, d) in enumerate(zip(tabs, diagrams)):
            with tab:
                _render_single_lesson_diagram(d, concept_slug, i)


def _render_single_lesson_diagram(d: dict, concept_slug: str, idx: int):
    """Render one interactive lesson diagram with Show Answer support."""
    board = chess.Board(d["fen"])
    player_color = "white" if board.turn == chess.WHITE else "black"

    reveal_key = f"_reveal_lesson_{concept_slug}_{idx}"
    reveal_now = st.session_state.pop(reveal_key, False)

    # Build phases so the opponent's reply animates after a correct move
    phases_key = f"_lesson_phases_{concept_slug}_{idx}"
    if phases_key not in st.session_state:
        puzzle_dict = {
            "fen": d["fen"],
            "best_move_san": d["move"],
            "eval_before": 0.0,
            "eval_after": 0.0,
        }
        try:
            st.session_state[phases_key] = _build_puzzle_phases(puzzle_dict)
        except Exception:
            st.session_state[phases_key] = None
    phases = st.session_state[phases_key]

    st.components.v1.html(
        _interactive_board_html(
            fen=d["fen"],
            best_move_san=d["move"],
            eval_before=0.0,
            eval_after=0.0,
            player_color=player_color,
            puzzle_idx=-1,
            phases=phases,
            reveal_solution=reveal_now,
        ),
        height=_board_iframe_height(),
        scrolling=False,
    )

    caption = d.get("caption", "")
    if caption:
        st.markdown(
            f'<div style="font-size:0.82em;color:#8a9fb0;margin-top:4px;text-align:center;">'
            f'{caption}</div>',
            unsafe_allow_html=True,
        )

    if st.button("Show Answer", key=f"show_ans_{concept_slug}_{idx}"):
        st.session_state[reveal_key] = True
        st.rerun()


def _extract_takeaway(lesson_text: str) -> str | None:
    """Extract the 'Key rule of thumb' content from lesson markdown."""
    import re
    m = re.search(
        r'##\s*Key rule of thumb\s*\n+(.*?)(?=\n##|\Z)',
        lesson_text, re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return None


def _render_takeaway_card(takeaway: str):
    """Render the key takeaway as a prominent styled card."""
    st.markdown(
        f'<div style="background:#1a1a10;border:1px solid #5a4a1a;border-left:4px solid #e2c97e;'
        f'border-radius:8px;padding:14px 18px;margin:8px 0 18px;">'
        f'<div style="font-size:0.7em;font-weight:700;color:#e2c97e;letter-spacing:0.08em;'
        f'margin-bottom:6px;">KEY TAKEAWAY</div>'
        f'<div style="font-size:1.0em;font-weight:600;color:#f0e6c8;line-height:1.5;">'
        f'{takeaway}</div></div>',
        unsafe_allow_html=True,
    )


def _board_iframe_height() -> int:
    """Compute dynamic iframe height based on board settings.

    Returns a *minimum* height; the iframe auto-resizes via ResizeObserver.
    """
    size_label = st.session_state.get("board_square_size", "Standard (64px)")
    sq_size = _BOARD_SIZES.get(size_label, 64)
    coord_w = 20 if st.session_state.get("show_coordinates", True) else 0
    return coord_w + sq_size * 8 + 44


def _interactive_board_html(
    fen: str,
    best_move_san: str | None,
    eval_before: float,
    eval_after: float,
    player_color: str,
    puzzle_idx: int = -1,
    phases: list | None = None,
    reveal_solution: bool = False,
    highlight_hint: bool = False,
) -> str:
    """
    Return a self-contained HTML page with a fully interactive chessboard.
    Zero external dependencies — Python pre-computes all chess logic and
    embeds it as JSON. Click-to-select, click-to-move with instant feedback.
    """
    import json as _json

    board = chess.Board(fen)
    mover_color = "w" if board.turn == chess.WHITE else "b"
    flip        = (player_color == "black")

    # ── Board settings ─────────────────────────────────────────────────────────
    theme_name = st.session_state.get("board_theme", "Brown")
    theme      = _BOARD_THEMES.get(theme_name, _BOARD_THEMES["Brown"])
    light_color = theme["light"]
    dark_color  = theme["dark"]
    piece_set_name = st.session_state.get("piece_set", "Cburnett")
    piece_base_url = _PIECE_SETS.get(piece_set_name, _PIECE_SETS["Cburnett"])
    size_label = st.session_state.get("board_square_size", "Standard (64px)")
    sq_size    = _BOARD_SIZES.get(size_label, 64)
    show_coords = st.session_state.get("show_coordinates", True)
    coord_w    = 20 if show_coords else 0
    board_px   = coord_w + sq_size * 8
    piece_img_sz = int(sq_size * 0.9375)  # 60/64 ratio
    sound_on   = "true" if st.session_state.get("sound_enabled", True) else "false"
    anim_on    = "true" if st.session_state.get("animation_enabled", True) else "false"
    show_legal = "true" if st.session_state.get("show_legal_moves", True) else "false"

    # ── Current position: {sq_name: {c, t}} ──────────────────────────────────
    pos: dict[str, dict] = {}
    for sq, piece in board.piece_map().items():
        pos[chess.square_name(sq)] = {
            "c": "w" if piece.color == chess.WHITE else "b",
            "t": piece.symbol().upper(),
        }

    # ── Legal moves: {from_sq: [to_sq, ...]} (deduplicated for promotions) ───
    legal: dict[str, list[str]] = {}
    for move in board.legal_moves:
        f = chess.square_name(move.from_square)
        t = chess.square_name(move.to_square)
        if f not in legal:
            legal[f] = []
        if t not in legal[f]:
            legal[f].append(t)

    # ── Effects, SAN, move_meta, promotion data ──────────────────────────────
    effects, san_map, move_meta, promo_effects, promo_san, promo_meta = _board_to_effects_san(board)

    # ── Best move UCI (4-char) ────────────────────────────────────────────────
    best_uci     = ""
    best_san_str = best_move_san or ""
    if best_move_san:
        try:
            mv       = board.parse_san(best_move_san)
            best_uci = mv.uci()[:4]
        except Exception:
            pass

    color_cap      = player_color.capitalize()
    best_san_js    = best_san_str.replace("\\", "\\\\").replace("'", "\\'")
    flip_js        = "true" if flip else "false"
    piece_icon     = "&#9812;" if player_color == "white" else "&#9818;"
    color_accent   = "#e2c97e" if player_color == "white" else "#90aec4"
    reveal_js      = "true" if reveal_solution else "false"
    highlight_js   = "true" if highlight_hint else "false"

    pos_json     = _json.dumps(pos)
    legal_json   = _json.dumps(legal)
    effects_json = _json.dumps(effects)
    san_json     = _json.dumps(san_map)
    meta_json    = _json.dumps(move_meta)
    promo_efx_json  = _json.dumps(promo_effects)
    promo_san_json  = _json.dumps(promo_san)
    promo_meta_json = _json.dumps(promo_meta)
    phases_json  = _json.dumps(phases) if phases is not None else "null"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:#0d1117;font-family:system-ui,sans-serif;color:#cce0f4;padding:8px 10px;}}
#wrap{{width:{board_px}px;margin:0 auto;}}
#board-grid{{
  display:grid;
  grid-template-columns:{f'{coord_w}px ' if coord_w else ''}repeat(8,{sq_size}px);
  grid-template-rows:repeat(8,{sq_size}px){f' {coord_w}px' if coord_w else ''};
  width:{board_px}px;
}}
.rl,.fl{{display:flex;align-items:center;justify-content:center;
  font-size:10px;color:#607d8b;user-select:none;}}
.sq{{display:flex;align-items:center;justify-content:center;
  width:{sq_size}px;height:{sq_size}px;
  cursor:pointer;position:relative;user-select:none;overflow:hidden;}}
.piece-img{{width:{piece_img_sz}px;height:{piece_img_sz}px;pointer-events:none;user-select:none;draggable:false;}}
.sq.light{{background:{light_color};}}
.sq.dark{{background:{dark_color};}}
.sq.selected{{background:#f6f669!important;}}
.sq.legal::after{{
  content:'';position:absolute;
  width:32%;height:32%;border-radius:50%;
  background:rgba(0,0,0,0.22);pointer-events:none;
}}
.sq.legal.has-piece::after{{
  width:85%;height:85%;border-radius:50%;background:transparent;
  border:5px solid rgba(0,0,0,0.28);
}}
.sq.hint-sq{{background:#5ba3d0!important;}}
.sq.drag-over{{background:#f6f669!important;}}
.sq.last-from,.sq.last-to{{background:rgba(155,199,0,0.41)!important;}}
#status{{
  margin:10px 0 0;padding:14px 16px;border-radius:10px;
  line-height:1.45;
  background:linear-gradient(135deg,#0d1525 0%,#111c30 100%);
  border:1px solid #253a55;min-height:58px;
  transition:border-color .25s,box-shadow .25s;
  box-shadow:0 3px 14px rgba(0,0,0,0.5);
}}
#status:empty{{display:none;}}
.act-btn{{
  flex:1;padding:12px 0;border-radius:8px;cursor:pointer;
  font-size:0.97em;font-weight:600;
  background:linear-gradient(135deg,#1a2535,#1e3045);
  color:#8ab8d8;border:1px solid #3a6a8a;text-align:center;
  transition:background .15s,border-color .15s,transform .1s,box-shadow .15s;
}}
.act-btn:hover{{
  background:linear-gradient(135deg,#22334a,#2a4060);
  border-color:#5a9ac0;color:#b0d4ee;
  transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,0.4);
}}
.act-btn:active{{transform:translateY(0);}}
#action-row{{display:flex;gap:8px;margin-top:10px;width:{board_px}px;}}
#reset-row{{margin-top:8px;width:{board_px}px;display:none;gap:8px;}}
.sq.answer-to{{background:#5dba80!important;}}
#hint{{
  margin-top:8px;font-size:0.8em;color:#7ab0cc;
  text-align:center;font-weight:500;letter-spacing:0.01em;
}}
#promo-overlay{{
  display:none;position:absolute;top:0;left:0;
  width:100%;height:100%;z-index:900;
  background:rgba(0,0,0,0.45);
}}
#promo-dialog{{
  position:absolute;z-index:910;
  background:#1a2535;border:2px solid #5a9ac0;border-radius:6px;
  display:flex;flex-direction:column;overflow:hidden;
  box-shadow:0 8px 24px rgba(0,0,0,0.6);
}}
.promo-choice{{
  display:flex;align-items:center;justify-content:center;
  width:{sq_size}px;height:{sq_size}px;cursor:pointer;
}}
.promo-choice:hover{{background:rgba(90,154,192,0.3);}}
.promo-choice img{{width:{piece_img_sz}px;height:{piece_img_sz}px;pointer-events:none;}}
@keyframes correctFlash{{
  0%{{box-shadow:0 0 0 rgba(46,125,50,0);}}
  40%{{box-shadow:0 0 28px rgba(46,125,50,0.5);}}
  100%{{box-shadow:0 0 0 rgba(46,125,50,0);}}
}}
@keyframes wrongShake{{
  0%,100%{{transform:translateX(0);}}
  15%{{transform:translateX(-6px);}}
  30%{{transform:translateX(6px);}}
  45%{{transform:translateX(-5px);}}
  60%{{transform:translateX(5px);}}
  75%{{transform:translateX(-3px);}}
  90%{{transform:translateX(3px);}}
}}
</style>
</head>
<body>
<div id="wrap">
  <div id="board-wrap" style="position:relative;width:{board_px}px;">
    <div id="board-grid"></div>
    <div id="promo-overlay" onclick="cancelPromotion()">
      <div id="promo-dialog" onclick="event.stopPropagation()"></div>
    </div>
    <svg id="arrow-svg" xmlns="http://www.w3.org/2000/svg"
         style="position:absolute;top:0;left:{coord_w}px;width:{sq_size*8}px;height:{sq_size*8}px;pointer-events:none;overflow:visible;">
      <defs>
        <marker id="arrowhead" markerWidth="5" markerHeight="4" refX="4.5" refY="2" orient="auto">
          <polygon points="0 0, 5 2, 0 4" fill="#e87720" opacity="0.9"/>
        </marker>
      </defs>
    </svg>
  </div>
  <div id="status"></div>
  <div id="history-nav-row" style="display:none;justify-content:center;gap:8px;margin:8px 0 0;">
    <button id="hist-back" class="act-btn" style="flex:0;padding:8px 20px;" onclick="historyBack()" disabled>&#9664; Back</button>
    <button id="hist-fwd"  class="act-btn" style="flex:0;padding:8px 20px;" onclick="historyForward()" disabled>Next &#9654;</button>
  </div>
  <div id="hint" style="display:none;"></div>
  <div id="action-row" style="display:none;"></div>
  <div id="reset-row">
    <button id="reset-btn" class="act-btn" onclick="resetBoard()">&#8635;&nbsp;&nbsp;Try Again</button>
    <button id="skip-btn"  class="act-btn" style="display:none;" onclick="notifyParent(false)">&#8594;&nbsp;&nbsp;Skip</button>
  </div>
</div>
<script>
// ── Config from Python settings ──────────────────────────────────────────────
var SQ_SIZE={sq_size}, COORD_W={coord_w}, SOUND_ON={sound_on}, ANIM_ON={anim_on}, SHOW_LEGAL={show_legal};
var LIGHT_COLOR='{light_color}', DARK_COLOR='{dark_color}';

var POS     = {pos_json};
var LEGAL   = {legal_json};
var EFFECTS = {effects_json};
var SAN_MAP = {san_json};
var MOVE_META = {meta_json};
var PROMO_EFFECTS = {promo_efx_json};
var PROMO_SAN     = {promo_san_json};
var PROMO_META    = {promo_meta_json};
var BEST_UCI = '{best_uci}';
var BEST_SAN = '{best_san_js}';
var MOVER    = '{mover_color}';
var FLIP     = {flip_js};
var EV_BEFORE= {eval_before:.2f};
var EV_AFTER = {eval_after:.2f};
var COLOR        = '{color_cap}';
var COLOR_ACCENT = '{color_accent}';
var PIECE_ICON   = '{piece_icon}';
var PUZZLE_IDX   = {puzzle_idx};
var notifyTimeout= null;
var PHASES       = {phases_json};
var phaseIdx     = 0;
var totalPhases  = PHASES ? PHASES.length : 1;
var PIECE_BASE   = '{piece_base_url}';
var REVEAL_SOLUTION = {reveal_js};
var HIGHLIGHT_HINT  = {highlight_js};

// ── Audio ─────────────────────────────────────────────────────────────────────
var _audioCtx=null;
function _getAudioCtx(){{if(!_audioCtx)_audioCtx=new(window.AudioContext||window.webkitAudioContext)();return _audioCtx;}}
function playCorrectSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var g=ctx.createGain();g.connect(ctx.destination);g.gain.setValueAtTime(0.15,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.6);
  var o1=ctx.createOscillator();o1.type='sine';o1.frequency.setValueAtTime(523.25,ctx.currentTime);o1.connect(g);o1.start(ctx.currentTime);o1.stop(ctx.currentTime+0.3);
  var o2=ctx.createOscillator();o2.type='sine';o2.frequency.setValueAtTime(659.25,ctx.currentTime+0.15);var g2=ctx.createGain();g2.connect(ctx.destination);g2.gain.setValueAtTime(0.15,ctx.currentTime+0.15);g2.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.6);o2.connect(g2);o2.start(ctx.currentTime+0.15);o2.stop(ctx.currentTime+0.5);
  }}catch(e){{}}
}}
function playWrongSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var g=ctx.createGain();g.connect(ctx.destination);g.gain.setValueAtTime(0.1,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.25);
  var o=ctx.createOscillator();o.type='square';o.frequency.setValueAtTime(180,ctx.currentTime);o.connect(g);o.start(ctx.currentTime);o.stop(ctx.currentTime+0.25);
  }}catch(e){{}}
}}

// ── Move sounds (move/capture/check/castle) ──────────────────────────────────
function playMoveSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var len=0.08;
  var buf=ctx.createBuffer(1,ctx.sampleRate*len,ctx.sampleRate);var d=buf.getChannelData(0);
  for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*Math.exp(-i/(d.length*0.15));
  var src=ctx.createBufferSource();src.buffer=buf;
  var bp=ctx.createBiquadFilter();bp.type='bandpass';bp.frequency.value=800;bp.Q.value=1.5;
  var g=ctx.createGain();g.gain.setValueAtTime(0.18,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+len);
  src.connect(bp);bp.connect(g);g.connect(ctx.destination);src.start();src.stop(ctx.currentTime+len);
  }}catch(e){{}}
}}
function playCaptureSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var len=0.1;
  var buf=ctx.createBuffer(1,ctx.sampleRate*len,ctx.sampleRate);var d=buf.getChannelData(0);
  for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*Math.exp(-i/(d.length*0.12));
  var src=ctx.createBufferSource();src.buffer=buf;
  var bp=ctx.createBiquadFilter();bp.type='bandpass';bp.frequency.value=1400;bp.Q.value=2;
  var g=ctx.createGain();g.gain.setValueAtTime(0.25,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+len);
  src.connect(bp);bp.connect(g);g.connect(ctx.destination);src.start();src.stop(ctx.currentTime+len);
  }}catch(e){{}}
}}
function playCheckSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();
  var o=ctx.createOscillator();o.type='sine';o.frequency.setValueAtTime(1200,ctx.currentTime);
  var g=ctx.createGain();g.gain.setValueAtTime(0.15,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.15);
  o.connect(g);g.connect(ctx.destination);o.start();o.stop(ctx.currentTime+0.15);
  }}catch(e){{}}
}}
function playCastleSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var t=ctx.currentTime;
  for(var n=0;n<2;n++){{
    var off=n*0.08;var len=0.06;
    var buf=ctx.createBuffer(1,ctx.sampleRate*len,ctx.sampleRate);var d=buf.getChannelData(0);
    for(var i=0;i<d.length;i++)d[i]=(Math.random()*2-1)*Math.exp(-i/(d.length*0.15));
    var src=ctx.createBufferSource();src.buffer=buf;
    var bp=ctx.createBiquadFilter();bp.type='bandpass';bp.frequency.value=800;bp.Q.value=1.5;
    var g=ctx.createGain();g.gain.setValueAtTime(0.18,t+off);g.gain.exponentialRampToValueAtTime(0.001,t+off+len);
    src.connect(bp);bp.connect(g);g.connect(ctx.destination);src.start(t+off);src.stop(t+off+len);
  }}
  }}catch(e){{}}
}}
function playMoveSoundForKey(key){{
  if(!SOUND_ON)return;
  var m=MOVE_META[key];
  if(!m){{playMoveSound();return;}}
  if(m.check)playCheckSound();
  if(m.castle){{playCastleSound();return;}}
  if(m.capture)playCaptureSound();
  else playMoveSound();
}}
function playSoundFromSAN(san){{
  if(!SOUND_ON)return;
  if(san.indexOf('+')!==-1||san.indexOf('#')!==-1)playCheckSound();
  if(san.indexOf('x')!==-1)playCaptureSound();
  else if(san==='O-O'||san==='O-O-O')playCastleSound();
  else playMoveSound();
}}

// ── History navigation state ──────────────────────────────────────────────────
var history = [];
var historyIdx = -1;
var viewingHistory = false;

// ── Last-move highlight tracking ──────────────────────────────────────────────
var lastFrom=null, lastTo=null;

function applyLastMoveHL(){{
  document.querySelectorAll('.sq.last-from,.sq.last-to').forEach(function(e){{e.classList.remove('last-from','last-to');}});
  if(lastFrom){{var c=getCell(lastFrom);if(c)c.classList.add('last-from');}}
  if(lastTo){{var c=getCell(lastTo);if(c)c.classList.add('last-to');}}
}}

function setLastMove(from,to){{
  lastFrom=from; lastTo=to;
  applyLastMoveHL();
}}

function notifyParent(correct){{
  if(PUZZLE_IDX<0)return;
  try{{
    var marker=correct?'\u25CF\u2713':'\u25CF\u2717';
    var btns=window.parent.document.querySelectorAll('button');
    for(var i=0;i<btns.length;i++){{
      if(btns[i].textContent.trim()===marker){{btns[i].click();return;}}
    }}
  }}catch(e){{console.warn('notifyParent failed:',e);}}
}}

function loadPhase(idx){{
  var ph=PHASES[idx];
  phaseIdx=idx;
  LEGAL   =ph.legal;
  EFFECTS =ph.effects;
  SAN_MAP =ph.san_map;
  MOVE_META    =ph.move_meta||{{}};
  PROMO_EFFECTS=ph.promo_effects||{{}};
  PROMO_SAN    =ph.promo_san||{{}};
  PROMO_META   =ph.promo_meta||{{}};
  BEST_UCI=ph.best_uci;
  BEST_SAN=ph.best_san;
  EV_BEFORE=ph.ev_before;
  EV_AFTER =ph.ev_after;
  curPos=JSON.parse(JSON.stringify(ph.pos));
  done=false; selected=null; suppressNextClick=false;
  lastFrom=null; lastTo=null;
  clearArrows();
  buildBoard();
  var el=document.getElementById('status');
  el.style.borderColor='#253a55';
  el.style.boxShadow='0 3px 14px rgba(0,0,0,0.5)';
  if(totalPhases>1){{
    el.innerHTML='<div style="font-size:0.72em;color:#5a8ab0;font-weight:700;letter-spacing:0.08em;text-align:center;">MOVE '+(idx+1)+' OF '+totalPhases+'</div>';
  }}else{{
    el.innerHTML='';
  }}
  document.getElementById('reset-row').style.display='none';
  if(idx===0&&history.length===0)pushHistory('Start','start');
}}

function playEngineMove(ph){{
  var eng=ph.engine;
  if(!eng){{
    var ni=phaseIdx+1;
    if(ni<totalPhases){{loadPhase(ni);}}
    else if(PUZZLE_IDX>=0){{notifyTimeout=setTimeout(function(){{notifyParent(true);}},200);}}
    else{{var sb=document.getElementById('skip-btn');if(sb)sb.style.display='none';document.getElementById('reset-row').style.display='flex';}}
    return;
  }}
  var el=document.getElementById('status');
  el.style.borderColor='#253a55';
  el.style.boxShadow='0 3px 14px rgba(0,0,0,0.5)';
  el.innerHTML='<div style="font-size:0.65em;color:#5a8ab0;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">ENGINE PLAYS</div>'
    +'<div style="font-size:1.05em;font-weight:700;color:#a0bccc;">Stockfish\u00A0\u2192\u00A0<b style="color:#cce0f4;">'+eng.san+'</b></div>';
  // Animate engine slide
  animateSlide(eng.from_sq,eng.to_sq,function(){{
    playSoundFromSAN(eng.san);
    (eng.effects||[]).forEach(function(ch){{
      var cell=getCell(ch.sq);
      if(ch.c===undefined){{delete curPos[ch.sq];if(cell)cell.innerHTML='';}}
      else{{curPos[ch.sq]={{c:ch.c,t:ch.t}};if(cell)cell.innerHTML=pc(ch.c,ch.t);}}
    }});
    setLastMove(eng.from_sq,eng.to_sq);
    buildBoard();
    pushHistory(eng.san,'engine');
    setTimeout(function(){{
      var ni=phaseIdx+1;
      if(ni<totalPhases){{loadPhase(ni);}}
      else if(PUZZLE_IDX>=0){{notifyTimeout=setTimeout(function(){{notifyParent(true);}},200);}}
      else{{var sb=document.getElementById('skip-btn');if(sb)sb.style.display='none';document.getElementById('reset-row').style.display='flex';}}
    }},400);
  }});
}}

function pc(c,t){{return '<img class="piece-img" src="'+PIECE_BASE+(c==='w'?'w':'b')+t+'.svg" draggable="false">';}}
var selected=null, done=false, suppressNextClick=false;
var curPos=JSON.parse(JSON.stringify(POS));

// ── Drag state ────────────────────────────────────────────────────────────────
var dragFrom=null, dragActive=false, dragStartX=0, dragStartY=0, ghost=null;

// ── Arrow / right-click state ─────────────────────────────────────────────────
var rightDragFrom=null;
var arrows=[];

// ── Phase reset tracking (wrong-answer phase) ─────────────────────────────────
var wrongOnPhase=-1;

// ── Promotion dialog state ────────────────────────────────────────────────────
var promoFrom=null, promoTo=null, promoKey=null, promoSkipAnim=false;

function cancelHeld(){{
  if(dragActive){{
    ghost.style.display='none';
    document.querySelectorAll('.drag-over').forEach(function(el){{el.classList.remove('drag-over');}});
    var oc=getCell(dragFrom); if(oc)oc.style.opacity='';
    dragFrom=null; dragActive=false;
  }}
  clearHL(); selected=null;
}}

function sqCenter(sq){{
  var fi='abcdefgh'.indexOf(sq[0]);
  var ri=parseInt(sq[1])-1;
  var col=FLIP?(7-fi):fi;
  var row=FLIP?ri:(7-ri);
  return {{x:col*SQ_SIZE+SQ_SIZE/2, y:row*SQ_SIZE+SQ_SIZE/2}};
}}

function drawArrows(){{
  var svg=document.getElementById('arrow-svg');
  Array.from(svg.children).forEach(function(ch){{
    if(ch.tagName.toLowerCase()!=='defs')svg.removeChild(ch);
  }});
  arrows.forEach(function(a){{
    var fc=sqCenter(a.from);
    if(a.to===null){{
      var circ=document.createElementNS('http://www.w3.org/2000/svg','circle');
      circ.setAttribute('cx',fc.x); circ.setAttribute('cy',fc.y); circ.setAttribute('r',SQ_SIZE*0.34);
      circ.setAttribute('stroke','#e87720'); circ.setAttribute('stroke-width','5');
      circ.setAttribute('fill','none'); circ.setAttribute('opacity','0.82');
      svg.appendChild(circ);
    }}else{{
      var tc=sqCenter(a.to);
      var dx=tc.x-fc.x, dy=tc.y-fc.y;
      var len=Math.sqrt(dx*dx+dy*dy)||1;
      var ux=dx/len, uy=dy/len;
      var line=document.createElementNS('http://www.w3.org/2000/svg','line');
      line.setAttribute('x1', fc.x+ux*16); line.setAttribute('y1', fc.y+uy*16);
      line.setAttribute('x2', tc.x-ux*14); line.setAttribute('y2', tc.y-uy*14);
      line.setAttribute('stroke','#e87720'); line.setAttribute('stroke-width','8');
      line.setAttribute('stroke-linecap','butt');
      line.setAttribute('marker-end','url(#arrowhead)');
      line.setAttribute('opacity','0.82');
      svg.appendChild(line);
    }}
  }});
}}

function addArrow(from,to){{
  var dest=(from===to)?null:to;
  for(var i=0;i<arrows.length;i++){{
    if(arrows[i].from===from&&arrows[i].to===dest){{arrows.splice(i,1);drawArrows();return;}}
  }}
  arrows.push({{from:from,to:dest}});
  drawArrows();
}}

function clearArrows(){{if(arrows.length){{arrows=[];drawArrows();}}}}

// ── Piece sliding animation ──────────────────────────────────────────────────
function animateSlide(fromSq,toSq,callback){{
  if(!ANIM_ON){{if(callback)callback();return;}}
  var grid=document.getElementById('board-grid');
  var gridRect=grid.getBoundingClientRect();
  var fromCell=getCell(fromSq);
  var toCell=getCell(toSq);
  if(!fromCell||!toCell){{if(callback)callback();return;}}
  var p=curPos[fromSq];
  if(!p){{if(callback)callback();return;}}
  var fromRect=fromCell.getBoundingClientRect();
  var toRect=toCell.getBoundingClientRect();
  // Create temp sliding image
  var img=document.createElement('img');
  img.src=PIECE_BASE+(p.c==='w'?'w':'b')+p.t+'.svg';
  img.style.cssText='position:fixed;pointer-events:none;z-index:800;width:'+SQ_SIZE*0.9375+'px;height:'+SQ_SIZE*0.9375+'px;transition:left 150ms ease-out,top 150ms ease-out;';
  var offX=(fromRect.width-SQ_SIZE*0.9375)/2;
  var offY=(fromRect.height-SQ_SIZE*0.9375)/2;
  img.style.left=(fromRect.left+offX)+'px';
  img.style.top=(fromRect.top+offY)+'px';
  document.body.appendChild(img);
  // Hide the piece on source square
  fromCell.innerHTML='';
  // Trigger slide
  requestAnimationFrame(function(){{
    var offX2=(toRect.width-SQ_SIZE*0.9375)/2;
    var offY2=(toRect.height-SQ_SIZE*0.9375)/2;
    img.style.left=(toRect.left+offX2)+'px';
    img.style.top=(toRect.top+offY2)+'px';
  }});
  setTimeout(function(){{
    if(img.parentNode)img.parentNode.removeChild(img);
    if(callback)callback();
  }},160);
}}

// ── History navigation ────────────────────────────────────────────────────────
function captureStatus(){{
  var el=document.getElementById('status');
  return {{html:el.innerHTML,border:el.style.borderColor,shadow:el.style.boxShadow}};
}}
function restoreStatus(snap){{
  var el=document.getElementById('status');
  el.innerHTML=snap.html; el.style.borderColor=snap.border; el.style.boxShadow=snap.shadow;
}}
function pushHistory(label,who){{
  var snap={{pos:JSON.parse(JSON.stringify(curPos)),label:label,who:who,status:captureStatus(),lastFrom:lastFrom,lastTo:lastTo}};
  if(historyIdx>=0&&historyIdx<history.length-1)history=history.slice(0,historyIdx+1);
  history.push(snap);
  historyIdx=history.length-1;
  viewingHistory=false;
  updateNavButtons();
}}
function historyBack(){{
  if(historyIdx<=0)return;
  historyIdx--;
  renderHistoryPosition();
}}
function historyForward(){{
  if(historyIdx>=history.length-1){{renderLivePosition();return;}}
  historyIdx++;
  renderHistoryPosition();
}}
function renderHistoryPosition(){{
  viewingHistory=true;
  var snap=history[historyIdx];
  curPos=JSON.parse(JSON.stringify(snap.pos));
  lastFrom=snap.lastFrom||null; lastTo=snap.lastTo||null;
  clearHL(); selected=null; clearArrows();
  buildBoard();
  restoreStatus(snap.status);
  document.getElementById('hint').style.display='none';
  document.getElementById('action-row').style.display='none';
  updateNavButtons();
}}
function renderLivePosition(){{
  if(history.length===0)return;
  historyIdx=history.length-1;
  viewingHistory=false;
  var snap=history[historyIdx];
  curPos=JSON.parse(JSON.stringify(snap.pos));
  lastFrom=snap.lastFrom||null; lastTo=snap.lastTo||null;
  clearHL(); selected=null; clearArrows();
  buildBoard();
  restoreStatus(snap.status);
  updateNavButtons();
}}
function updateNavButtons(){{
  var row=document.getElementById('history-nav-row');
  var bb=document.getElementById('hist-back');
  var bf=document.getElementById('hist-fwd');
  if(history.length>1){{
    row.style.display='flex';
    bb.disabled=(historyIdx<=0);
    bf.disabled=(historyIdx>=history.length-1);
  }}else{{
    row.style.display='none';
  }}
}}

function sqName(fi,ri){{return 'abcdefgh'[fi]+(ri+1);}}

function initGhost(){{
  ghost=document.createElement('img');
  ghost.style.cssText='position:fixed;pointer-events:none;z-index:9999;width:'+SQ_SIZE+'px;height:'+SQ_SIZE+'px;display:none;transform:translate(-50%,-50%)';
  document.body.appendChild(ghost);
}}

function getXY(e){{
  if(e.touches&&e.touches[0])return {{x:e.touches[0].clientX,y:e.touches[0].clientY}};
  if(e.changedTouches&&e.changedTouches[0])return {{x:e.changedTouches[0].clientX,y:e.changedTouches[0].clientY}};
  return {{x:e.clientX,y:e.clientY}};
}}

function sqAtPoint(x,y){{
  var el=document.elementFromPoint(x,y);
  while(el&&el!==document.body){{
    if(el.dataset&&el.dataset.sq)return el.dataset.sq;
    el=el.parentElement;
  }}
  return null;
}}

// ── Click-to-select (no carry mode) ──────────────────────────────────────────
function onSqClick(sq){{
  clearArrows();
  if(done||viewingHistory)return;
  if(suppressNextClick){{suppressNextClick=false;return;}}
  var p=curPos[sq];
  if(selected){{
    var dests=LEGAL[selected]||[];
    if(dests.indexOf(sq)!==-1){{
      makeMove(selected,sq,false);return;
    }}
    if(p&&p.c===MOVER&&sq!==selected&&LEGAL[sq]&&LEGAL[sq].length>0){{
      clearHL();selected=sq;highlightSel(sq);return;
    }}
    clearHL();selected=null;return;
  }}
  if(p&&p.c===MOVER&&LEGAL[sq]&&LEGAL[sq].length>0){{
    selected=sq;highlightSel(sq);
  }}else if(p&&p.c===MOVER){{
    var cell=getCell(sq);
    if(cell){{
      var origBg=cell.style.background;
      cell.style.background='#8b2020';
      setTimeout(function(){{cell.style.background=origBg;}},300);
    }}
  }}
}}

// ── Drag ──────────────────────────────────────────────────────────────────────
function startDrag(sq,e){{
  if(done||viewingHistory)return;
  var p=curPos[sq];
  if(!p||p.c!==MOVER||!LEGAL[sq]||!LEGAL[sq].length)return;
  dragFrom=sq; dragActive=false;
  var xy=getXY(e); dragStartX=xy.x; dragStartY=xy.y;
  ghost.src=PIECE_BASE+(p.c==='w'?'w':'b')+p.t+'.svg';
}}

function moveDrag(e){{
  var xy=getXY(e);
  if(!dragFrom)return;
  if(!dragActive){{
    var dx=xy.x-dragStartX,dy=xy.y-dragStartY;
    if(dx*dx+dy*dy<16)return;
    dragActive=true;
    clearHL(); selected=dragFrom; highlightSel(dragFrom);
    var oc=getCell(dragFrom); if(oc)oc.style.opacity='0.3';
    ghost.style.display='block';
  }}
  if(e.cancelable)e.preventDefault();
  ghost.style.left=xy.x+'px'; ghost.style.top=xy.y+'px';
  document.querySelectorAll('.drag-over').forEach(function(el){{el.classList.remove('drag-over');}});
  var tSq=sqAtPoint(xy.x,xy.y);
  if(tSq&&LEGAL[dragFrom]&&LEGAL[dragFrom].indexOf(tSq)!==-1){{
    var tc=getCell(tSq); if(tc)tc.classList.add('drag-over');
  }}
}}

function endDrag(e){{
  if(!dragFrom)return;
  ghost.style.display='none';
  document.querySelectorAll('.drag-over').forEach(function(el){{el.classList.remove('drag-over');}});
  var oc=getCell(dragFrom); if(oc)oc.style.opacity='';
  if(dragActive){{
    suppressNextClick=true;
    var xy=getXY(e);
    var destSq=sqAtPoint(xy.x,xy.y);
    clearHL(); selected=null;
    var from=dragFrom; dragFrom=null; dragActive=false;
    if(destSq&&LEGAL[from]&&LEGAL[from].indexOf(destSq)!==-1){{
      makeMove(from,destSq,true);
    }}
    return;
  }}
  dragFrom=null; dragActive=false;
}}

function buildBoard(){{
  var g=document.getElementById('board-grid');
  g.innerHTML='';
  for(var row=0;row<8;row++){{
    var ri=FLIP?row:(7-row);
    if(COORD_W>0){{
      var rl=document.createElement('div');
      rl.className='rl'; rl.textContent=ri+1; g.appendChild(rl);
    }}
    for(var col=0;col<8;col++){{
      var fi=FLIP?(7-col):col;
      var sq=sqName(fi,ri);
      var light=(fi+ri)%2!==0;
      var cell=document.createElement('div');
      cell.className='sq '+(light?'light':'dark');
      cell.dataset.sq=sq;
      (function(s){{
        cell.addEventListener('click',function(){{onSqClick(s);}});
        cell.addEventListener('mousedown',function(e){{
          if(e.button===2){{
            if(dragActive){{cancelHeld();e.preventDefault();}}
            else{{rightDragFrom=s;e.preventDefault();}}
          }}else{{startDrag(s,e);}}
        }});
        cell.addEventListener('mouseup',function(e){{
          if(e.button===2&&rightDragFrom){{addArrow(rightDragFrom,s);rightDragFrom=null;e.preventDefault();}}
        }});
        cell.addEventListener('contextmenu',function(e){{e.preventDefault();}});
        cell.addEventListener('touchstart',function(e){{startDrag(s,e);}},{{passive:true}});
      }})(sq);
      var p=curPos[sq];
      if(p)cell.innerHTML=pc(p.c,p.t);
      g.appendChild(cell);
    }}
  }}
  if(COORD_W>0){{
    var ec=document.createElement('div'); g.appendChild(ec);
    var files=FLIP?'hgfedcba':'abcdefgh';
    for(var i=0;i<8;i++){{
      var fl=document.createElement('div');
      fl.className='fl'; fl.textContent=files[i]; g.appendChild(fl);
    }}
  }}
  applyLastMoveHL();
}}

function getCell(sq){{return document.querySelector('[data-sq="'+sq+'"]');}}

function clearHL(){{
  document.querySelectorAll('.sq').forEach(function(e){{
    e.classList.remove('selected','legal','has-piece','hint-sq','drag-over');
    e.style.opacity='';
  }});
}}

function showHint(){{
  if(!BEST_UCI)return;
  var cell=getCell(BEST_UCI.slice(0,2));
  if(cell)cell.classList.add('hint-sq');
}}

function highlightSel(sq){{
  var c=getCell(sq); if(c)c.classList.add('selected');
  if(!SHOW_LEGAL)return;
  (LEGAL[sq]||[]).forEach(function(dst){{
    var dc=getCell(dst);
    if(dc){{dc.classList.add('legal');if(curPos[dst])dc.classList.add('has-piece');}}
  }});
}}

// ── Promotion dialog ─────────────────────────────────────────────────────────
function showPromotionDialog(from,to,key,skipAnim){{
  promoFrom=from; promoTo=to; promoKey=key; promoSkipAnim=skipAnim;
  var overlay=document.getElementById('promo-overlay');
  var dialog=document.getElementById('promo-dialog');
  var toCell=getCell(to);
  if(!toCell)return;
  var grid=document.getElementById('board-grid');
  var gr=grid.getBoundingClientRect();
  var cr=toCell.getBoundingClientRect();
  var c=MOVER;
  var pieces=['q','r','b','n'];
  var pTypes=['Q','R','B','N'];
  dialog.innerHTML='';
  for(var i=0;i<4;i++){{
    var d=document.createElement('div');
    d.className='promo-choice';
    d.innerHTML='<img src="'+PIECE_BASE+(c==='w'?'w':'b')+pTypes[i]+'.svg">';
    (function(pc){{d.addEventListener('click',function(){{choosePromotion(pc);}})}})(pieces[i]);
    dialog.appendChild(d);
  }}
  // Position: stack from promotion rank direction
  var isTop=(cr.top-gr.top)<(gr.bottom-cr.bottom);
  dialog.style.left=(cr.left-gr.left)+'px';
  if(isTop){{dialog.style.top=(cr.top-gr.top)+'px';dialog.style.bottom='auto';}}
  else{{dialog.style.bottom=(gr.bottom-cr.bottom)+'px';dialog.style.top='auto';}}
  overlay.style.display='block';
}}

function choosePromotion(piece){{
  document.getElementById('promo-overlay').style.display='none';
  var key=promoKey;
  var pe=PROMO_EFFECTS[key]; if(!pe||!pe[piece])return;
  var efx=pe[piece];
  var san=(PROMO_SAN[key]||{{}})[piece]||(promoFrom+'-'+promoTo);
  var meta=(PROMO_META[key]||{{}})[piece];
  clearHL(); selected=null;
  var applyFn=function(){{
    efx.forEach(function(ch){{
      var cell=getCell(ch.sq);
      if(ch.c===undefined){{delete curPos[ch.sq];if(cell)cell.innerHTML='';}}
      else{{curPos[ch.sq]={{c:ch.c,t:ch.t}};if(cell)cell.innerHTML=pc(ch.c,ch.t);}}
    }});
    setLastMove(promoFrom,promoTo);
    buildBoard();
    if(meta){{
      if(meta.check)playCheckSound();
      else if(meta.capture)playCaptureSound();
      else playMoveSound();
    }}else{{playMoveSound();}}
    showResult(san,key);
    promoFrom=null; promoTo=null; promoKey=null;
  }};
  if(!promoSkipAnim){{
    animateSlide(promoFrom,promoTo,applyFn);
  }}else{{
    applyFn();
  }}
}}

function cancelPromotion(){{
  document.getElementById('promo-overlay').style.display='none';
  promoFrom=null; promoTo=null; promoKey=null;
  // Restore piece if it was hidden
  if(selected){{clearHL();selected=null;}}
}}

// ── Core move logic ──────────────────────────────────────────────────────────
function makeMove(from,to,skipAnim){{
  clearArrows(); clearHL(); selected=null;
  var key=from+to;
  // Check for promotion
  if(PROMO_EFFECTS[key]){{
    showPromotionDialog(from,to,key,skipAnim);
    return;
  }}
  var efx=EFFECTS[key]||[];
  var san=SAN_MAP[key]||(from+'-'+to);
  var applyFn=function(){{
    efx.forEach(function(ch){{
      var cell=getCell(ch.sq);
      if(ch.c===undefined){{
        delete curPos[ch.sq];
        if(cell)cell.innerHTML='';
      }}else{{
        curPos[ch.sq]={{c:ch.c,t:ch.t}};
        if(cell)cell.innerHTML=pc(ch.c,ch.t);
      }}
    }});
    setLastMove(from,to);
    buildBoard();
    playMoveSoundForKey(key);
    showResult(san,key);
  }};
  if(!skipAnim){{
    animateSlide(from,to,applyFn);
  }}else{{
    applyFn();
  }}
}}

function showResult(san,uciKey){{
  done=true;
  var correct=(BEST_UCI&&uciKey===BEST_UCI)||(BEST_SAN&&san===BEST_SAN);
  var el=document.getElementById('status');
  document.getElementById('hint').style.display='none';
  document.getElementById('action-row').style.display='none';
  if(correct){{
    playCorrectSound();
    var bg=document.getElementById('board-grid');if(bg){{bg.style.animation='correctFlash 0.6s ease-out';setTimeout(function(){{bg.style.animation='';}},700);}}
    var phLabel=(PHASES&&totalPhases>1)?' \u00B7 Move '+(phaseIdx+1)+' of '+totalPhases:'';
    el.style.borderColor='#2e7d32';
    el.style.boxShadow='0 0 18px rgba(46,125,50,0.35)';
    el.innerHTML='<div style="font-size:0.65em;color:#2a7a32;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">\u2713 CORRECT'+phLabel+'</div>'
      +'<div style="font-size:1.1em;font-weight:700;color:#81c784;">'+san+'</div>'
      +'<div style="font-size:0.78em;color:#a0bccc;margin-top:4px;">Best move \u2014 Eval: '+EV_BEFORE.toFixed(2)+'</div>';
    pushHistory(san,'player');
    if(PHASES&&phaseIdx+1<totalPhases){{
      notifyTimeout=setTimeout(function(){{playEngineMove(PHASES[phaseIdx]);}},700);
    }}else if(PUZZLE_IDX>=0){{
      notifyTimeout=setTimeout(function(){{notifyParent(true);}},900);
    }}else{{
      var sb=document.getElementById('skip-btn');
      if(sb)sb.style.display='none';
      document.getElementById('reset-row').style.display='flex';
    }}
  }}else{{
    playWrongSound();
    var bg=document.getElementById('board-grid');if(bg){{bg.style.animation='wrongShake 0.5s ease-out';setTimeout(function(){{bg.style.animation='';}},500);}}
    el.style.borderColor='#b71c1c';
    el.style.boxShadow='0 0 18px rgba(183,28,28,0.35)';
    el.innerHTML='<div style="font-size:0.65em;color:#c62828;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">\u2717 NOT QUITE</div>'
      +'<div style="font-size:1.1em;font-weight:700;color:#ef9a9a;">'+san+'</div>'
      +'<div style="font-size:0.78em;color:#a0bccc;margin-top:4px;">Try again \u2014 or use &#128161; Get Hint below</div>';
    wrongOnPhase=phaseIdx;
    clearHL();
    var sb=document.getElementById('skip-btn');
    if(sb)sb.style.display=PUZZLE_IDX>=0?'flex':'none';
    document.getElementById('reset-row').style.display='flex';
  }}
}}

// ── Solution animation ────────────────────────────────────────────────────────
function playSolution(){{
  if(notifyTimeout){{clearTimeout(notifyTimeout);notifyTimeout=null;}}
  done=true; viewingHistory=false;
  clearHL(); clearArrows(); selected=null;
  lastFrom=null; lastTo=null;
  document.getElementById('hint').style.display='none';
  document.getElementById('action-row').style.display='none';
  document.getElementById('reset-row').style.display='none';
  history=[]; historyIdx=-1; updateNavButtons();
  if(PHASES){{
    var ph=PHASES[0];
    curPos=JSON.parse(JSON.stringify(ph.pos));
    buildBoard();
    pushHistory('Start','start');
    setTimeout(function(){{playSolutionPhase(0);}},300);
  }}else{{
    curPos=JSON.parse(JSON.stringify(POS));
    buildBoard();
    var el=document.getElementById('status');
    el.style.borderColor='#3a6a96';
    el.style.boxShadow='0 0 14px rgba(58,106,150,0.35)';
    el.innerHTML='<div style="font-size:0.65em;color:#3a6a96;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">&#9654; SOLUTION</div>'
      +'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;">Playing best move\u2026</div>';
    pushHistory('Start','start');
    if(BEST_UCI){{
      animateSingleMove(BEST_UCI,BEST_SAN,function(){{showSolutionComplete();}});
    }}else{{
      showSolutionComplete();
    }}
  }}
}}

function playSolutionPhase(idx){{
  var ph=PHASES[idx];
  phaseIdx=idx;
  LEGAL=ph.legal; EFFECTS=ph.effects; SAN_MAP=ph.san_map;
  MOVE_META=ph.move_meta||{{}};
  PROMO_EFFECTS=ph.promo_effects||{{}};
  PROMO_SAN=ph.promo_san||{{}};
  PROMO_META=ph.promo_meta||{{}};
  BEST_UCI=ph.best_uci; BEST_SAN=ph.best_san;
  EV_BEFORE=ph.ev_before; EV_AFTER=ph.ev_after;
  if(idx>0){{
    curPos=JSON.parse(JSON.stringify(ph.pos));
    clearHL(); clearArrows();
    buildBoard();
  }}
  var phLabel=totalPhases>1?'MOVE '+(idx+1)+' OF '+totalPhases+'\u00A0\u00B7\u00A0':'';
  var el=document.getElementById('status');
  el.style.borderColor='#3a6a96';
  el.style.boxShadow='0 0 14px rgba(58,106,150,0.35)';
  el.innerHTML='<div style="font-size:0.65em;color:#3a6a96;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">&#9654; '+phLabel+'SOLUTION</div>'
    +'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;">Playing best move\u2026</div>';
  if(idx>0)pushHistory('Phase '+(idx+1),'start');
  animateSingleMove(ph.best_uci,ph.best_san,function(){{
    if(ph.engine){{
      animateEngineResponse(ph,function(){{
        var ni=idx+1;
        if(ni<totalPhases){{setTimeout(function(){{playSolutionPhase(ni);}},400);}}
        else{{showSolutionComplete();}}
      }});
    }}else{{
      var ni=idx+1;
      if(ni<totalPhases){{setTimeout(function(){{playSolutionPhase(ni);}},400);}}
      else{{showSolutionComplete();}}
    }}
  }});
}}

function animateSingleMove(uci,san,callback){{
  if(!uci){{if(callback)callback();return;}}
  var fromSq=uci.slice(0,2), toSq=uci.slice(2,4);
  clearHL();
  var fc=getCell(fromSq); if(fc)fc.classList.add('hint-sq');
  var el=document.getElementById('status');
  el.style.borderColor='#3a6a96';
  el.style.boxShadow='0 0 14px rgba(58,106,150,0.35)';
  var phLabel=(PHASES&&totalPhases>1)?'MOVE '+(phaseIdx+1)+' OF '+totalPhases+'\u00A0\u00B7\u00A0':'';
  el.innerHTML='<div style="font-size:0.65em;color:#3a6a96;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">&#9654; '+phLabel+'SOLUTION</div>'
    +'<div style="font-size:1.1em;font-weight:700;color:#81c784;">'+san+'</div>';
  setTimeout(function(){{
    var key=uci.slice(0,4);
    animateSlide(fromSq,toSq,function(){{
      var efx=EFFECTS[key]||[];
      efx.forEach(function(ch){{
        var cell=getCell(ch.sq);
        if(ch.c===undefined){{delete curPos[ch.sq];if(cell)cell.innerHTML='';}}
        else{{curPos[ch.sq]={{c:ch.c,t:ch.t}};if(cell)cell.innerHTML=pc(ch.c,ch.t);}}
      }});
      playSoundFromSAN(san);
      setLastMove(fromSq,toSq);
      clearHL();
      buildBoard();
      var tc=getCell(toSq); if(tc)tc.classList.add('answer-to');
      pushHistory(san,'player');
      setTimeout(function(){{if(callback)callback();}},500);
    }});
  }},400);
}}

function animateEngineResponse(ph,callback){{
  var eng=ph.engine;
  if(!eng){{if(callback)callback();return;}}
  var el=document.getElementById('status');
  el.style.borderColor='#253a55';
  el.style.boxShadow='0 3px 14px rgba(0,0,0,0.5)';
  el.innerHTML='<div style="font-size:0.65em;color:#5a8ab0;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">ENGINE RESPONDS</div>'
    +'<div style="font-size:1.05em;font-weight:700;color:#a0bccc;">Stockfish\u00A0\u2192\u00A0<b style="color:#cce0f4;">'+eng.san+'</b></div>';
  clearHL();
  animateSlide(eng.from_sq,eng.to_sq,function(){{
    playSoundFromSAN(eng.san);
    (eng.effects||[]).forEach(function(ch){{
      var cell=getCell(ch.sq);
      if(ch.c===undefined){{delete curPos[ch.sq];if(cell)cell.innerHTML='';}}
      else{{curPos[ch.sq]={{c:ch.c,t:ch.t}};if(cell)cell.innerHTML=pc(ch.c,ch.t);}}
    }});
    setLastMove(eng.from_sq,eng.to_sq);
    buildBoard();
    pushHistory(eng.san,'engine');
    setTimeout(function(){{if(callback)callback();}},400);
  }});
}}

function showSolutionComplete(){{
  var el=document.getElementById('status');
  el.style.borderColor='#2e7d32';
  el.style.boxShadow='0 0 18px rgba(46,125,50,0.35)';
  el.innerHTML='<div style="font-size:0.65em;color:#2a7a32;font-weight:700;letter-spacing:0.1em;margin-bottom:5px;">&#9654; SOLUTION COMPLETE</div>'
    +'<div style="font-size:0.95em;font-weight:600;color:#81c784;">Use \u25C0 Back / Next \u25B6 to review</div>';
  if(history.length>0)history[history.length-1].status=captureStatus();
  var sb=document.getElementById('skip-btn');
  if(sb)sb.style.display=PUZZLE_IDX>=0?'flex':'none';
  document.getElementById('reset-row').style.display='flex';
  updateNavButtons();
}}

function resetBoard(){{
  if(notifyTimeout){{clearTimeout(notifyTimeout);notifyTimeout=null;}}
  history=[]; historyIdx=-1; viewingHistory=false; updateNavButtons();
  lastFrom=null; lastTo=null;
  if(PHASES){{
    var target=wrongOnPhase>=0?wrongOnPhase:0;
    wrongOnPhase=-1;
    loadPhase(target);
    return;
  }}
  clearArrows();
  curPos=JSON.parse(JSON.stringify(POS));
  done=false; selected=null; suppressNextClick=false;
  buildBoard();
  var el=document.getElementById('status');
  el.innerHTML='';
  el.style.borderColor='#253a55';
  el.style.boxShadow='0 3px 14px rgba(0,0,0,0.5)';
  document.getElementById('reset-row').style.display='none';
  pushHistory('Start','start');
}}

document.addEventListener('mousemove',moveDrag);
document.addEventListener('mouseup',function(e){{
  if(e.button===2){{rightDragFrom=null;}}else{{endDrag(e);}}
}});
document.addEventListener('touchmove',moveDrag,{{passive:false}});
document.addEventListener('touchend',endDrag);
buildBoard();
initGhost();
if(!PHASES)pushHistory('Start','start');
if(REVEAL_SOLUTION){{
  setTimeout(function(){{playSolution();}},300);
}}else if(HIGHLIGHT_HINT&&BEST_UCI){{
  showHint();
}}
// ── Auto-resize iframe to fit content ────────────────────────────────────────
(function(){{
  function fitFrame(){{
    try{{
      var h=document.body.scrollHeight;
      if(window.frameElement)window.frameElement.style.height=h+'px';
    }}catch(e){{}}
  }}
  if(typeof ResizeObserver!=='undefined'){{
    new ResizeObserver(fitFrame).observe(document.body);
  }}
  fitFrame();
}})();
</script>
</body></html>"""



def _deep_dive_to_review(pgn_text: str, white: str, black: str):
    """Pre-load a profile game into the Game Review tab and navigate there."""
    platform = st.session_state.get("profile_platform", "Chess.com")
    game_entry = {"pgn": pgn_text, "headers": {"White": white, "Black": black}}
    if platform == "Lichess":
        st.session_state.lichess_games = [game_entry]
        st.session_state.lichess_username = st.session_state.get("profile_username_built", "")
        st.session_state["game_source"] = "Lichess"
    else:
        st.session_state.chesscom_games = [game_entry]
        st.session_state.chesscom_username = st.session_state.get("profile_username_built", "")
        st.session_state["game_source"] = "Chess.com"
    st.session_state.from_profile_dive = True
    for k in ("moves", "headers", "game_review", "coaching_concepts",
              "current_game_id", "loaded_file"):
        st.session_state.pop(k, None)
    st.session_state.navigate_to_review = True
    st.rerun()



# ── Profile supplementary panels ─────────────────────────────────────────────

def _render_color_breakdown(summaries: list[dict], inline: bool = False):
    """White vs. Black performance split."""
    white_s = [s for s in summaries if s.get("player_color") == "white"]
    black_s = [s for s in summaries if s.get("player_color") == "black"]
    if not white_s and not black_s:
        return

    def _stats(games):
        if not games:
            return None
        wins   = sum(1 for g in games if
                     (g["result"] == "1-0" and g["player_color"] == "white") or
                     (g["result"] == "0-1" and g["player_color"] == "black"))
        losses = sum(1 for g in games if
                     (g["result"] == "0-1" and g["player_color"] == "white") or
                     (g["result"] == "1-0" and g["player_color"] == "black"))
        draws  = len(games) - wins - losses
        accs   = [g["player_accuracy"] for g in games]
        return {
            "n":        len(games),
            "wins":     wins, "losses": losses, "draws": draws,
            "win_pct":  round(wins / len(games) * 100, 1) if len(games) else 0,
            "avg_acc":  round(sum(accs) / len(accs), 1) if len(accs) else 0,
            "blunders": round(sum(g["blunders"] for g in games) / len(games), 1) if len(games) else 0,
            "mistakes": round(sum(g["mistakes"] for g in games) / len(games), 1) if len(games) else 0,
        }

    ws, bs = _stats(white_s), _stats(black_s)

    if not inline:
        st.markdown("---")
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px;">'
            '<div style="width:3px;height:15px;background:#90a4ae;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#90a4ae;font-weight:700;'
            'letter-spacing:0.04em;">WHITE vs BLACK</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    if inline:
        # Stacked cards for compact side-by-side layout
        for side, stats, symbol in [("White", ws, "⬜"), ("Black", bs, "⬛")]:
            if not stats:
                continue
            perf_html = _piece_rating_html(_performance_level(stats["blunders"], stats["mistakes"]), "1.2em")
            st.markdown(
                f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;padding:14px;margin-bottom:8px;">'
                f'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;margin-bottom:8px;">'
                f'{symbol} {side} <span style="font-size:0.7em;color:#7a9ab0;">({stats["n"]} games)</span></div>'
                f'<div style="margin-bottom:6px;">{perf_html}</div>'
                f'<div style="font-size:0.82em;color:#cce0f4;margin-bottom:4px;">'
                f'<span style="color:#81c784;">W {stats["wins"]}</span> / '
                f'<span style="color:#e57373;">L {stats["losses"]}</span> / '
                f'<span style="color:#aaa;">D {stats["draws"]}</span> &nbsp;'
                f'<span style="color:#7a9ab0;font-size:0.85em;">({stats["win_pct"]}% WR)</span></div>'
                f'<div style="font-size:0.78em;color:#90a4b8;">'
                f'🔴 {stats["blunders"]} blunders/game &nbsp;&nbsp;'
                f'🟠 {stats["mistakes"]} mistakes/game</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        return

    lcol, rcol = st.columns(2)
    for col, side, stats, symbol in [(lcol, "White", ws, "⬜"), (rcol, "Black", bs, "⬛")]:
        if not stats:
            with col:
                st.markdown(
                    f'<p style="color:#555;font-size:0.85em;">No games as {side}</p>',
                    unsafe_allow_html=True,
                )
            continue
        perf_html = _piece_rating_html(_performance_level(stats["blunders"], stats["mistakes"]), "1.2em")
        with col:
            st.markdown(
                f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;padding:16px;">'
                f'<div style="font-size:1.05em;font-weight:700;color:#cce0f4;margin-bottom:10px;">'
                f'{symbol} {side} <span style="font-size:0.7em;color:#7a9ab0;">({stats["n"]} games)</span></div>'
                f'<div style="margin-bottom:8px;">{perf_html}</div>'
                f'<div style="font-size:0.82em;color:#cce0f4;margin-bottom:6px;">'
                f'<span style="color:#81c784;">W {stats["wins"]}</span> / '
                f'<span style="color:#e57373;">L {stats["losses"]}</span> / '
                f'<span style="color:#aaa;">D {stats["draws"]}</span> &nbsp;'
                f'<span style="color:#7a9ab0;font-size:0.85em;">({stats["win_pct"]}% WR)</span></div>'
                f'<div style="font-size:0.78em;color:#90a4b8;">'
                f'🔴 {stats["blunders"]} blunders/game &nbsp;&nbsp;'
                f'🟠 {stats["mistakes"]} mistakes/game</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _build_opening_drills(summaries: list[dict]) -> dict[str, list[dict]]:
    """
    Extract opening drill positions from profile summaries.
    Returns {opening_name: [position_dicts]} where each position appeared in 2+ games.
    """
    import io as _io
    from collections import defaultdict

    def _get_opening(s) -> str:
        op = s.get("opening", "")
        if op:
            return op
        pgn_text = s.get("_pgn", "")
        if pgn_text:
            try:
                game = chess.pgn.read_game(_io.StringIO(pgn_text))
                if game:
                    h = dict(game.headers)
                    return (
                        h.get("Opening", "")
                        or h.get("ECOUrl", "").split("/")[-1].replace("-", " ").title()
                        or h.get("ECO", "")
                    )
            except Exception:
                pass
        return ""

    # Collect positions per opening: {opening: {fen: {move_san, player_color, move_number, count}}}
    opening_positions: dict[str, dict[str, dict]] = defaultdict(dict)

    for s in summaries:
        pgn_text = s.get("_pgn", "")
        if not pgn_text:
            continue
        op_name = (_get_opening(s) or "Unknown")[:45]
        player_color = s.get("player_color", "white")
        try:
            game = chess.pgn.read_game(_io.StringIO(pgn_text))
            if not game:
                continue
            board = game.board()
            node = game
            half_move = 0
            for node in game.mainline():
                if half_move >= 24:  # first 12 full moves
                    break
                move = node.move
                san = board.san(move)
                color = "white" if board.turn == chess.WHITE else "black"
                if color == player_color:
                    fen = board.fen()
                    move_number = (half_move // 2) + 1
                    pos_key = fen
                    if pos_key in opening_positions[op_name]:
                        existing = opening_positions[op_name][pos_key]
                        existing["count"] += 1
                        # Keep the move — consensus: only if same move played
                        if existing["best_move_san"] != san:
                            existing["consensus"] = False
                    else:
                        opening_positions[op_name][pos_key] = {
                            "fen": fen,
                            "best_move_san": san,
                            "player_color": player_color,
                            "move_number": move_number,
                            "half_move": half_move,
                            "count": 1,
                            "consensus": True,
                        }
                board.push(move)
                half_move += 1
        except Exception:
            continue

    # Filter: only openings with 2+ games, only positions with count >= 2 and consensus
    result: dict[str, list[dict]] = {}
    for op_name, positions in opening_positions.items():
        filtered = [
            p for p in positions.values()
            if p["count"] >= 2 and p.get("consensus", True)
        ]
        if len(filtered) >= 1:
            filtered.sort(key=lambda p: p["half_move"])
            result[op_name] = [
                {
                    "fen": p["fen"],
                    "best_move_san": p["best_move_san"],
                    "player_color": p["player_color"],
                    "move_number": p["move_number"],
                    "half_move": p["half_move"],
                }
                for p in filtered
            ]

    return result


def _render_opening_repertoire(summaries: list[dict], inline: bool = False):
    """Group games by opening — show win rate + accuracy per opening."""
    import io as _io

    def _get_opening(s) -> str:
        op = s.get("opening", "")
        if op:
            return op
        pgn_text = s.get("_pgn", "")
        if pgn_text:
            try:
                game = chess.pgn.read_game(_io.StringIO(pgn_text))
                if game:
                    h = dict(game.headers)
                    return (
                        h.get("Opening", "")
                        or h.get("ECOUrl", "").split("/")[-1].replace("-", " ").title()
                        or h.get("ECO", "")
                    )
            except Exception:
                pass
        return ""

    openings: dict[str, dict] = {}
    for s in summaries:
        op = (_get_opening(s) or "Unknown")[:45]
        d  = openings.setdefault(op, {"n": 0, "wins": 0, "losses": 0, "draws": 0,
                                       "blunders": 0, "mistakes": 0})
        d["n"]        += 1
        d["blunders"] += s["blunders"]
        d["mistakes"] += s["mistakes"]
        result, color = s.get("result", "*"), s["player_color"]
        if   (result == "1-0" and color == "white") or (result == "0-1" and color == "black"):
            d["wins"]   += 1
        elif (result == "0-1" and color == "white") or (result == "1-0" and color == "black"):
            d["losses"] += 1
        else:
            d["draws"]  += 1

    if not openings:
        return

    sorted_ops = sorted(openings.items(), key=lambda x: -x[1]["n"])[:8]

    if not inline:
        st.markdown("---")
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px;">'
            '<div style="width:3px;height:15px;background:#9c7c38;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#e2c97e;font-weight:700;'
            'letter-spacing:0.04em;">OPENING REPERTOIRE</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    # Check if drills are available for Practice buttons
    _drills = st.session_state.get("opening_drills", {})

    for _op_idx, (op_name, d) in enumerate(sorted_ops):
        n           = d["n"]
        win_pct     = round(d["wins"] / n * 100) if n else 0
        blunders_pg = round(d["blunders"] / n, 1) if n else 0
        mistakes_pg = round(d["mistakes"] / n, 1) if n else 0
        bar_color   = "#81c784" if win_pct >= 55 else "#ffb74d" if win_pct >= 40 else "#e57373"
        err_color   = "#81c784" if blunders_pg < 0.5 else "#ffb74d" if blunders_pg < 1.5 else "#e57373"
        st.markdown(
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:8px;'
            f'padding:10px 14px;margin-bottom:6px;">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">'
            f'<span style="font-size:0.88em;font-weight:600;color:#cce0f4;">{op_name}</span>'
            f'<span style="font-size:0.75em;color:#7a9ab0;">'
            f'{n} game{"s" if n != 1 else ""} &nbsp;'
            f'<span style="color:{err_color};">🔴 {blunders_pg} &nbsp;🟠 {mistakes_pg}/game</span></span>'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<div style="flex:1;background:#1a2535;border-radius:3px;height:6px;">'
            f'<div style="width:{win_pct}%;background:{bar_color};border-radius:3px;height:6px;min-width:2px;"></div>'
            f'</div>'
            f'<span style="font-size:0.75em;color:{bar_color};font-weight:700;min-width:60px;text-align:right;">'
            f'{d["wins"]}W {d["losses"]}L {d["draws"]}D</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if op_name in _drills:
            _safe_key = op_name.replace(" ", "_").replace(":", "").replace("/", "_")[:30]
            if st.button(f"Practice →", key=f"drill_go_{_op_idx}_{_safe_key}", help=f"Drill {op_name} positions"):
                st.session_state.drill_opening = op_name
                st.session_state.drill_idx = 0
                st.rerun()


def _render_progress_tracking(username: str, inline: bool = False):
    """Accuracy trend across profile builds — shown from the very first build."""
    import plotly.graph_objects as go

    history = db.get_profile_history(username)
    if len(history) < 1:
        return

    dates = [h["built_at"][:10] for h in history]
    accs  = [h["overall_acc"] for h in history]

    fig = go.Figure()

    # Use markers-only for a single point, lines+markers once we have a trend
    trace_mode = "markers" if len(accs) == 1 else "lines+markers"
    marker_size = 10 if len(accs) == 1 else 7

    fig.add_trace(go.Scatter(
        x=dates, y=accs, mode=trace_mode,
        name="Overall", line=dict(color="#4a6aaa", width=2.5),
        marker=dict(size=marker_size, color="#7ab3d4"),
        hovertemplate="Overall: %{y:.1f}%<extra></extra>",
    ))

    fig.update_layout(
        height=200 if inline else 240,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(color="#7a9ab0", size=11),
        xaxis=dict(gridcolor="#1a2535", color="#7a9ab0",
                   type="category"),
        yaxis=dict(gridcolor="#1a2535", color="#7a9ab0",
                   range=[max(0, min(accs) - 10), min(100, max(accs) + 10)],
                   ticksuffix="%"),
        legend=dict(bgcolor="#0d1117", bordercolor="#1e2e3e", font=dict(size=10)),
    )

    # Delta badge — starting point for 1 snapshot, trend for 2+
    delta_html = ""
    if len(accs) == 1:
        delta_html = (
            f'<span style="color:#7ab3d4;font-weight:700;margin-left:12px;">'
            f'Starting at {accs[0]:.1f}%</span>'
        )
    else:
        diff = accs[-1] - accs[0]
        if diff > 0:
            delta_html = (
                f'<span style="color:#81c784;font-weight:700;margin-left:12px;">'
                f'↑ {diff:+.1f}% since first build</span>'
            )
        elif diff < 0:
            delta_html = (
                f'<span style="color:#e57373;font-weight:700;margin-left:12px;">'
                f'↓ {diff:+.1f}% since first build</span>'
            )
        else:
            delta_html = (
                '<span style="color:#7a9ab0;margin-left:12px;">No change</span>'
            )

    if not inline:
        st.markdown("---")
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin-bottom:8px;">'
            '<div style="width:3px;height:15px;background:#7986cb;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#9fa8da;font-weight:700;'
            f'letter-spacing:0.04em;">PROGRESS OVER TIME</span>{delta_html}'
            '</div>'
            '<p style="font-size:0.78em;color:#7a9ab0;margin-bottom:8px;">'
            'Overall accuracy across profile builds. Rebuild periodically to track improvement.</p>',
            unsafe_allow_html=True,
        )
    elif delta_html:
        st.markdown(
            f'<div style="margin-bottom:4px;">{delta_html}</div>',
            unsafe_allow_html=True,
        )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Coach Chat ────────────────────────────────────────────────────────────────

def _build_coach_context() -> str:
    """Build a concise profile-context string injected into the coach's system prompt."""
    pd = st.session_state.get("profile_data")
    if not pd:
        return ""

    username    = pd.get("username", "the player")
    n_games     = pd.get("n_games", 0)
    record      = pd.get("record", {})
    wins        = record.get("wins", 0)
    losses      = record.get("losses", 0)
    draws       = record.get("draws", 0)
    blunders_pg = pd.get("blunders_per_game", 0)
    mistakes_pg = pd.get("mistakes_per_game", 0)
    priority    = pd.get("priority_focus", [])
    summary     = pd.get("summary", "")
    coach_msg   = pd.get("coach_message", "")

    skill_ratings = pd.get("skill_ratings", {})
    skill_lines = []
    for cat in SKILL_CATEGORIES:
        sr   = skill_ratings.get(cat, {})
        desc = sr.get("description", "")
        skill_lines.append(f"  {cat}: {desc}")

    lines = [
        f"Player: {username}",
        f"Games analysed: {n_games}  |  Record: {wins}W–{losses}L–{draws}D",
        f"Error rates: {blunders_pg:.1f} blunders/game, {mistakes_pg:.1f} mistakes/game",
        "Skill assessment:",
        *skill_lines,
        f"Priority concepts to study: {', '.join(priority) if priority else 'not assessed'}",
    ]
    if summary:
        lines.append(f"Coach's overall summary: {summary}")
    if coach_msg:
        lines.append(f"Coach's key message: {coach_msg}")

    # Add concrete examples from the player's actual games
    summaries = st.session_state.get("profile_summaries", [])
    if summaries:
        critical = []
        for s in summaries:
            for cm in s.get("critical_moves", []):
                if cm.get("classification") == "blunder" and cm.get("best_move_san"):
                    critical.append(cm)
        if critical:
            critical.sort(key=lambda c: abs(c.get("eval_before", 0) - c.get("eval_after", 0)), reverse=True)
            lines.append("\nRecurring mistake patterns from recent games (reference these when relevant):")
            for cm in critical[:3]:
                mv_num = cm.get("move_number", "?")
                played = cm.get("move_san", "?")
                best   = cm.get("best_move_san", "?")
                phase  = cm.get("phase", "?")
                ev_b   = cm.get("eval_before", 0)
                ev_a   = cm.get("eval_after", 0)
                lines.append(
                    f"  - Move {mv_num} ({phase}): played {played} (eval {ev_b:+.1f} → {ev_a:+.1f}), "
                    f"best was {best}"
                )

    return "\n".join(lines)


_COACH_STARTERS = [
    "What's the single most impactful area I should work on?",
    "How do I play against an isolated queen's pawn?",
    "Explain the two-weakness principle",
    "How do I convert a winning rook endgame?",
]

_SKILL_STARTER_MAP = {
    "Opening Prep": "What opening principles should I focus on to improve my first 12 moves?",
    "Middlegame": "What are the key strategic ideas I should look for in the middlegame?",
    "Endgame": "What are the most important endgame principles I should master?",
    "Tactics": "How can I reduce my blunders and spot tactics more consistently?",
    "Consistency": "How do I maintain consistent performance and avoid big swings between games?",
}


def _get_coach_starters() -> list[str]:
    """Return 4 starter prompts: 2 personalised + 2 generic if profile loaded, else 4 generic."""
    pd = st.session_state.get("profile_data")
    if not pd:
        return list(_COACH_STARTERS)
    starters: list[str] = []
    # Personalised #1: from priority_focus
    pf = pd.get("priority_focus", [])
    if pf:
        starters.append(f"How can I improve at {pf[0]} in my games?")
    # Personalised #2: weakest skill
    sums = st.session_state.get("profile_summaries", [])
    if sums:
        skills = compute_skill_scores(sums)
        if skills:
            weakest = min(skills, key=skills.get)
            q = _SKILL_STARTER_MAP.get(weakest, f"How do I improve my {weakest.lower()}?")
            if q not in starters:
                starters.append(q)
    # Fill up to 4 with generic starters (avoid duplicates)
    for s in _COACH_STARTERS:
        if len(starters) >= 4:
            break
        if s not in starters:
            starters.append(s)
    return starters[:4]


def render_coach_tab():
    """Streaming AI chess coach chat tab."""
    # ── Profile context indicator ─────────────────────────────────────────────
    pd = st.session_state.get("profile_data")
    if pd:
        username = pd.get("username", "Player")
        n_games  = pd.get("n_games", 0)
        st.markdown(
            f'<div style="display:inline-flex;align-items:center;gap:6px;'
            f'background:#0d1f2e;border:1px solid #2a4a6a;border-radius:12px;'
            f'padding:3px 12px;font-size:0.75em;color:#6aade0;margin-bottom:14px;">'
            f'<span style="color:#4a9a4a;font-size:0.9em;">●</span>'
            f' Profile loaded: <b style="color:#9ac8e8;">{username}</b>'
            f' &nbsp;·&nbsp; {n_games} games</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="display:inline-flex;align-items:center;gap:6px;'
            'background:#1a1010;border:1px solid #4a2a2a;border-radius:12px;'
            'padding:3px 12px;font-size:0.75em;color:#a07070;margin-bottom:14px;">'
            '<span style="color:#8a4a4a;font-size:0.9em;">●</span>'
            " No profile loaded — advice will be general. Load a profile in"
            " the <b>Profile</b> tab for personalised coaching.</div>",
            unsafe_allow_html=True,
        )

    # ── Initialise chat history ───────────────────────────────────────────────
    if "coach_messages" not in st.session_state:
        st.session_state.coach_messages = []

    msgs = st.session_state.coach_messages

    # ── Chat input — called early so its value is available this render cycle.
    # st.chat_input always renders sticky at the bottom regardless of code order.
    new_prompt = st.chat_input("Ask your chess coach anything…")
    if new_prompt:
        msgs.append({"role": "user", "content": new_prompt})

    # ── Starter chips (only when chat is still empty) ─────────────────────────
    if not msgs:
        st.markdown(
            '<p style="font-size:0.82em;color:#5a7a8a;margin-bottom:8px;">'
            "Try one of these to get started:</p>",
            unsafe_allow_html=True,
        )
        cols = st.columns(2)
        _starters = _get_coach_starters()
        for i, q in enumerate(_starters):
            with cols[i % 2]:
                if st.button(q, key=f"coach_starter_{i}", use_container_width=True):
                    msgs.append({"role": "user", "content": q})
                    # No explicit st.rerun() — the natural button rerun continues
                    # below and hits the auto-respond block directly.
        st.divider()

    # ── Render chat history ───────────────────────────────────────────────────
    for msg in msgs:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Auto-respond when last message is from user ───────────────────────────
    if msgs and msgs[-1]["role"] == "user":
        if _api_limit_reached():
            msgs.append({"role": "assistant", "content": "Daily AI usage limit reached. Please try again tomorrow."})
        else:
            _count_api_call()
            profile_ctx = _build_coach_context()
            with st.chat_message("assistant"):
                response = st.write_stream(coach_chat_stream(msgs, profile_ctx))
            msgs.append({"role": "assistant", "content": response})
        # Single rerun after streaming: hides the starter chips cleanly.
        st.rerun()

    # ── Clear button ─────────────────────────────────────────────────────────
    if msgs:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🗑 Clear chat", key="coach_clear"):
            st.session_state.coach_messages = []


def _notation_iframe_height() -> int:
    """Compute iframe height for the notation trainer board."""
    sq_size = _BOARD_SIZES.get(st.session_state.get("board_square_size", "Standard (64px)"), 64)
    coord_w = 20 if st.session_state.get("show_coordinates", True) else 0
    return coord_w + sq_size * 8 + 180


def _notation_trainer_html(mode: str = "practice", perspective: str = "white",
                           hide_coords: bool = False) -> str:
    """Return self-contained HTML for the board notation trainer game."""
    theme_name = st.session_state.get("board_theme", "Brown")
    theme = _BOARD_THEMES.get(theme_name, _BOARD_THEMES["Brown"])
    light_color = theme["light"]
    dark_color = theme["dark"]
    size_label = st.session_state.get("board_square_size", "Standard (64px)")
    sq_size = _BOARD_SIZES.get(size_label, 64)
    show_coords = st.session_state.get("show_coordinates", True)
    # Always allocate coord gutter when global coords on OR hide_coords mode
    # (hide_coords still shows corner "a" and "1" for orientation)
    coord_w = 20 if show_coords or hide_coords else 0
    board_px = coord_w + sq_size * 8
    sound_on = "true" if st.session_state.get("sound_enabled", True) else "false"
    flip = "true" if perspective == "black" else "false"
    is_speedrun = "true" if mode == "speedrun" else "false"
    hide_coords_js = "true" if hide_coords else "false"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:#0d1117;font-family:system-ui,sans-serif;color:#cce0f4;padding:8px 10px;}}
#wrap{{width:{board_px}px;margin:0 auto;}}
#target-display{{
  text-align:center;font-size:2.2em;font-weight:700;color:#e2c97e;
  letter-spacing:0.15em;margin-bottom:8px;min-height:1.4em;
}}
#timer-bar-wrap{{
  height:6px;background:#1a2233;border-radius:3px;margin-bottom:8px;
  display:{{"none" if mode != "speedrun" else "block"}};
}}
#timer-bar{{
  height:100%;background:#5a7ac8;border-radius:3px;width:100%;
  transition:width 0.1s linear;
}}
#board-grid{{
  display:grid;
  grid-template-columns:{f'{coord_w}px ' if coord_w else ''}repeat(8,{sq_size}px);
  grid-template-rows:repeat(8,{sq_size}px){f' {coord_w}px' if coord_w else ''};
  width:{board_px}px;
  border-radius:4px;overflow:hidden;
}}
.sq{{
  width:{sq_size}px;height:{sq_size}px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  font-size:{int(sq_size*0.35)}px;font-weight:700;color:transparent;
  transition:background 0.15s;user-select:none;position:relative;
}}
.sq.light{{background:{light_color};}}
.sq.dark{{background:{dark_color};}}
.sq:hover{{filter:brightness(1.15);}}
.rl,.fl{{display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:600;color:#8a9fb0;user-select:none;}}
@keyframes correctFlash{{0%{{background:#4caf50;}}100%{{background:inherit;}}}}
@keyframes wrongShake{{
  0%,100%{{transform:translateX(0);}}
  20%{{transform:translateX(-4px);}}
  40%{{transform:translateX(4px);}}
  60%{{transform:translateX(-3px);}}
  80%{{transform:translateX(3px);}}
}}
.sq.correct-flash{{animation:correctFlash 0.5s ease-out;}}
.sq.wrong-flash{{animation:wrongShake 0.4s ease-out;background:#c0392b !important;}}
.sq.show-correct{{background:#4caf50 !important;opacity:0.7;}}
#stats-bar{{
  display:flex;justify-content:space-around;align-items:center;
  margin-top:10px;padding:8px;background:#151c28;border-radius:6px;
  font-size:0.82em;
}}
.stat-item{{text-align:center;}}
.stat-val{{font-size:1.4em;font-weight:700;color:#e2c97e;}}
.stat-label{{font-size:0.75em;color:#6a8a9a;margin-top:2px;}}
#game-over{{
  display:none;position:absolute;top:0;left:0;width:100%;height:100%;
  background:rgba(13,17,23,0.92);border-radius:4px;
  flex-direction:column;align-items:center;justify-content:center;
  z-index:10;
}}
#game-over.active{{display:flex;}}
#game-over h2{{color:#e2c97e;margin-bottom:12px;font-size:1.3em;}}
#game-over .go-stat{{font-size:0.9em;color:#a0bccc;margin:3px 0;}}
#play-again{{
  margin-top:14px;padding:8px 28px;background:#5a7ac8;color:#fff;
  border:none;border-radius:6px;cursor:pointer;font-size:0.95em;font-weight:600;
}}
#play-again:hover{{background:#7a9ae8;}}
#board-wrap{{position:relative;}}
</style>
</head><body>
<div id="wrap">
  <div id="target-display">—</div>
  <div id="timer-bar-wrap"><div id="timer-bar"></div></div>
  <div id="board-wrap">
    <div id="board-grid"></div>
    <div id="game-over">
      <h2>Time's Up!</h2>
      <div class="go-stat" id="go-score"></div>
      <div class="go-stat" id="go-accuracy"></div>
      <div class="go-stat" id="go-streak"></div>
      <div class="go-stat" id="go-best" style="color:#e2c97e;font-weight:700;margin-top:4px;"></div>
      <button id="play-again" onclick="restartGame()">Play Again</button>
    </div>
  </div>
  <div id="stats-bar">
    <div class="stat-item"><div class="stat-val" id="s-score">0</div><div class="stat-label">Score</div></div>
    <div class="stat-item"><div class="stat-val" id="s-streak">0</div><div class="stat-label">Streak</div></div>
    <div class="stat-item"><div class="stat-val" id="s-best">0</div><div class="stat-label">Best Streak</div></div>
    <div class="stat-item"><div class="stat-val" id="s-acc">—</div><div class="stat-label">Accuracy</div></div>
  </div>
</div>
<script>
var FLIP = {flip};
var IS_SPEEDRUN = {is_speedrun};
var SOUND_ON = {sound_on};
var SQ_SIZE = {sq_size};
var COORD_W = {coord_w};
var HIDE_COORDS = {hide_coords_js};
var FILES = ['a','b','c','d','e','f','g','h'];
var RANKS = ['1','2','3','4','5','6','7','8'];
var LIGHT = '{light_color}';
var DARK  = '{dark_color}';

var score=0, streak=0, bestStreak=0, attempts=0, correct=0;
var targetSq='', timerMs=30000, timerInterval=null, gameActive=false, lastSq='';

// ── Audio ──
var _audioCtx=null;
function _getAudioCtx(){{if(!_audioCtx)_audioCtx=new(window.AudioContext||window.webkitAudioContext)();return _audioCtx;}}
function playCorrectSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var g=ctx.createGain();g.connect(ctx.destination);g.gain.setValueAtTime(0.15,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.6);
  var o1=ctx.createOscillator();o1.type='sine';o1.frequency.setValueAtTime(523.25,ctx.currentTime);o1.connect(g);o1.start(ctx.currentTime);o1.stop(ctx.currentTime+0.3);
  var o2=ctx.createOscillator();o2.type='sine';o2.frequency.setValueAtTime(659.25,ctx.currentTime+0.15);var g2=ctx.createGain();g2.connect(ctx.destination);g2.gain.setValueAtTime(0.15,ctx.currentTime+0.15);g2.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.6);o2.connect(g2);o2.start(ctx.currentTime+0.15);o2.stop(ctx.currentTime+0.5);
  }}catch(e){{}}
}}
function playWrongSound(){{
  if(!SOUND_ON)return;
  try{{var ctx=_getAudioCtx();var g=ctx.createGain();g.connect(ctx.destination);g.gain.setValueAtTime(0.1,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.25);
  var o=ctx.createOscillator();o.type='square';o.frequency.setValueAtTime(180,ctx.currentTime);o.connect(g);o.start(ctx.currentTime);o.stop(ctx.currentTime+0.25);
  }}catch(e){{}}
}}

function buildBoard(){{
  var grid=document.getElementById('board-grid');
  grid.innerHTML='';
  var ranks=FLIP?RANKS.slice():RANKS.slice().reverse();
  var files=FLIP?FILES.slice().reverse():FILES.slice();
  for(var r=0;r<8;r++){{
    if(COORD_W){{
      var rl=document.createElement('div');
      rl.className='rl';
      // HIDE_COORDS: only show rank label on bottom row (corner "1")
      if(!HIDE_COORDS||r===7)rl.textContent=ranks[r];
      grid.appendChild(rl);
    }}
    for(var f=0;f<8;f++){{
      var sq=document.createElement('div');
      var fi=FILES.indexOf(files[f]);
      var ri=RANKS.indexOf(ranks[r]);
      var isLight=(fi+ri)%2===1;
      sq.className='sq '+(isLight?'light':'dark');
      sq.dataset.sq=files[f]+ranks[r];
      sq.addEventListener('click',function(){{onSquareClick(this.dataset.sq,this);}});
      grid.appendChild(sq);
    }}
  }}
  if(COORD_W){{
    var spacer=document.createElement('div');spacer.className='fl';
    grid.appendChild(spacer);
    for(var f=0;f<8;f++){{
      var fl=document.createElement('div');
      fl.className='fl';
      // HIDE_COORDS: only show file label on first column (corner "a")
      if(!HIDE_COORDS||f===0)fl.textContent=files[f];
      grid.appendChild(fl);
    }}
  }}
}}

function pickRandomSquare(){{
  var sq;
  do{{
    sq=FILES[Math.floor(Math.random()*8)]+RANKS[Math.floor(Math.random()*8)];
  }}while(sq===lastSq);
  lastSq=sq;
  return sq;
}}

function onSquareClick(sq,el){{
  if(!gameActive)return;
  attempts++;
  if(sq===targetSq){{
    correct++;score++;streak++;
    if(streak>bestStreak)bestStreak=streak;
    el.classList.add('correct-flash');
    playCorrectSound();
    setTimeout(function(){{el.classList.remove('correct-flash');}},500);
    nextTarget();
  }}else{{
    streak=0;
    el.classList.add('wrong-flash');
    playWrongSound();
    // show correct square
    var allSq=document.querySelectorAll('.sq');
    allSq.forEach(function(s){{
      if(s.dataset.sq===targetSq)s.classList.add('show-correct');
    }});
    setTimeout(function(){{
      el.classList.remove('wrong-flash');
      allSq.forEach(function(s){{s.classList.remove('show-correct');}});
      nextTarget();
    }},800);
  }}
  updateStats();
}}

function nextTarget(){{
  targetSq=pickRandomSquare();
  document.getElementById('target-display').textContent=targetSq;
}}

function updateStats(){{
  document.getElementById('s-score').textContent=score;
  document.getElementById('s-streak').textContent=streak;
  document.getElementById('s-best').textContent=bestStreak;
  document.getElementById('s-acc').textContent=attempts?Math.round(correct/attempts*100)+'%':'—';
}}

function startGame(){{
  score=0;streak=0;attempts=0;correct=0;
  gameActive=true;
  document.getElementById('game-over').classList.remove('active');
  updateStats();
  nextTarget();
  if(IS_SPEEDRUN){{
    timerMs=30000;
    document.getElementById('timer-bar').style.width='100%';
    if(timerInterval)clearInterval(timerInterval);
    timerInterval=setInterval(timerTick,100);
  }}
}}

function timerTick(){{
  timerMs-=100;
  var pct=Math.max(0,timerMs/30000*100);
  document.getElementById('timer-bar').style.width=pct+'%';
  if(timerMs<=0){{
    clearInterval(timerInterval);
    timerInterval=null;
    showGameOver();
  }}
}}

function showGameOver(){{
  gameActive=false;
  var go=document.getElementById('game-over');
  document.getElementById('go-score').textContent='Score: '+score;
  document.getElementById('go-accuracy').textContent='Accuracy: '+(attempts?Math.round(correct/attempts*100)+'%':'—');
  document.getElementById('go-streak').textContent='Best Streak: '+bestStreak;
  // Speedrun high score via localStorage
  if(IS_SPEEDRUN){{
    var bestEl=document.getElementById('go-best');
    var prev=parseInt(localStorage.getItem('boardsense_speedrun_best'))||0;
    if(score>prev){{
      localStorage.setItem('boardsense_speedrun_best',score);
      bestEl.textContent='NEW BEST!';
      bestEl.style.color='#81c784';
    }}else if(prev>0){{
      bestEl.textContent='Personal Best: '+prev;
      bestEl.style.color='#e2c97e';
    }}
  }}
  go.classList.add('active');
}}

function restartGame(){{
  startGame();
}}

buildBoard();
// Show personal best above board on init (speedrun only)
if(IS_SPEEDRUN){{
  var _savedBest=parseInt(localStorage.getItem('boardsense_speedrun_best'))||0;
  if(_savedBest>0){{
    var _pbDiv=document.createElement('div');
    _pbDiv.style.cssText='text-align:center;font-size:0.82em;color:#e2c97e;font-weight:600;margin-bottom:6px;';
    _pbDiv.textContent='Personal Best: '+_savedBest;
    document.getElementById('wrap').insertBefore(_pbDiv,document.getElementById('board-wrap'));
  }}
}}
startGame();
</script>
</body></html>"""


def render_notation_tab():
    """Render the Board Notation Trainer section."""
    st.markdown(
        '<div style="font-size:0.72em;color:#a0bccc;font-weight:700;'
        'letter-spacing:0.06em;margin:8px 0 4px;">BOARD NOTATION TRAINER</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="font-size:0.85em;color:#6a8a9a;margin-bottom:12px;">'
        'Sharpen your board vision — click the square that matches the name shown. '
        'Practice at your own pace or race the clock in Speed Run mode.</p>',
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        mode_label = st.radio(
            "Mode", ["Practice", "Speed Run (30s)"],
            key="notation_mode", horizontal=True,
        )
    with c2:
        persp = st.radio(
            "Perspective", ["White", "Black"],
            key="notation_perspective", horizontal=True,
        )
    with c3:
        hide_coords = st.toggle("Hide Coordinates", key="notation_hide_coords")
    mode = "speedrun" if "Speed" in mode_label else "practice"
    perspective = persp.lower()
    html = _notation_trainer_html(mode=mode, perspective=perspective, hide_coords=hide_coords)
    components.html(html, height=_notation_iframe_height(), scrolling=False)


def render_dashboard_tab():
    """Landing dashboard — stats overview, quick actions, and recommendations."""
    profile = st.session_state.get("profile_data")
    summaries = st.session_state.get("profile_summaries", [])
    username = st.session_state.get("profile_username_built", "")

    if not profile:
        # No profile yet — guided onboarding
        st.markdown(
            '<div style="text-align:center;padding:28px 0 8px;">'
            '<div style="font-size:2.2em;margin-bottom:10px;">♔</div>'
            '<div style="font-size:1.3em;font-weight:700;color:#e2c97e;margin-bottom:6px;">'
            'Welcome to BoardSense</div>'
            '<div style="font-size:0.92em;color:#a0bccc;max-width:480px;margin:0 auto 4px;line-height:1.6;">'
            'Enter your username to build a personalised coaching profile. We\'ll '
            'analyse your recent games and tailor everything — lessons, puzzles, '
            'and training — to your actual play.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Inline build form
        _, fc, _ = st.columns([1, 2, 1])
        with fc:
            _ob_plat = st.radio(
                "Platform", ["Chess.com", "Lichess"],
                horizontal=True, key="onboard_platform",
            )
            _ob_label = "Chess.com username" if _ob_plat == "Chess.com" else "Lichess username"
            _ob_user = st.text_input(_ob_label, value="", key="onboard_username",
                                     placeholder="Enter username")
            _ob_est = _estimate_analysis_time(2, 12)
            st.caption(f"~2 months of games at standard depth ({_ob_est})")
            if st.button(
                "Build My Profile", type="primary",
                use_container_width=True, disabled=not _ob_user.strip(),
            ):
                # Pre-fill Profile tab inputs and navigate
                st.session_state.profile_platform = _ob_plat
                st.session_state.profile_username = _ob_user.strip().lower()
                st.session_state.profile_months = 2
                st.session_state.profile_depth = 12
                st.session_state._auto_build = True
                st.session_state.navigate_to_profile = True
                st.rerun()

        st.markdown('<div style="height:18px;"></div>', unsafe_allow_html=True)

        # What you'll unlock
        st.markdown(
            '<div style="text-align:center;font-size:0.78em;font-weight:700;color:#5a7ac8;'
            'letter-spacing:0.06em;margin-bottom:12px;">WHAT YOU\'LL UNLOCK</div>',
            unsafe_allow_html=True,
        )
        uc1, uc2, uc3, uc4 = st.columns(4)
        _card = (
            '<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
            'padding:16px 12px;text-align:center;height:100%;">'
            '<div style="font-size:1.3em;margin-bottom:6px;">{icon}</div>'
            '<div style="font-size:0.82em;font-weight:700;color:#cce0f4;margin-bottom:4px;">{title}</div>'
            '<div style="font-size:0.76em;color:#8a9eb5;line-height:1.5;">{desc}</div></div>'
        )
        with uc1:
            st.markdown(_card.format(
                icon="📊", title="Skill Analysis",
                desc="See exactly where you lose rating points",
            ), unsafe_allow_html=True)
        with uc2:
            st.markdown(_card.format(
                icon="📖", title="Custom Lessons",
                desc="AI lessons built from your own mistakes",
            ), unsafe_allow_html=True)
        with uc3:
            st.markdown(_card.format(
                icon="🧩", title="Your Puzzles",
                desc="Positions from games you actually played",
            ), unsafe_allow_html=True)
        with uc4:
            st.markdown(_card.format(
                icon="🎯", title="Training Plan",
                desc="Curriculum adapted to your weaknesses",
            ), unsafe_allow_html=True)

        # Game Review works without a profile
        st.markdown(
            '<div style="text-align:center;margin-top:20px;">'
            '<span style="font-size:0.82em;color:#7a9ab0;">Or '
            '<strong style="color:#a0bccc;">review a single game</strong> '
            'without a profile — upload any PGN in the Game Review tab.</span></div>',
            unsafe_allow_html=True,
        )
        return

    # ── Compute dashboard data ──────────────────────────────────────────────
    record = profile.get("record", {})
    _wins = record.get("wins", 0)
    _losses = record.get("losses", 0)
    _draws = record.get("draws", 0)
    _total_games = _wins + _losses + _draws

    _blunders_pg = profile.get("blunders_per_game", 0)
    _mistakes_pg = profile.get("mistakes_per_game", 0)

    _dash_skills = compute_skill_scores(summaries)

    # Win rate
    _win_rate = round(100 * _wins / _total_games, 1) if _total_games else 0

    # Best skill
    if _dash_skills:
        _best_skill_name = max(_dash_skills, key=_dash_skills.get)
        _best_skill_val = _dash_skills[_best_skill_name]
        _best_skill_label = _best_skill_name.upper()
    else:
        _best_skill_val = 0
        _best_skill_label = "BEST SKILL"

    # Previous build record for donut delta (new vs old games)
    _hist = db.get_profile_history(username)
    _prev_record = {}
    if len(_hist) >= 2:
        _prev_record = _hist[-2].get("record", {})
    elif len(_hist) == 1:
        _prev_record = _hist[0].get("record", {})
    _prev_w = _prev_record.get("wins", 0)
    _prev_l = _prev_record.get("losses", 0)
    _prev_d = _prev_record.get("draws", 0)
    # New games since last build (only if previous data exists)
    _has_delta = bool(_prev_record) and (_wins > _prev_w or _losses > _prev_l or _draws > _prev_d)
    _new_w = max(0, _wins - _prev_w) if _has_delta else 0
    _new_l = max(0, _losses - _prev_l) if _has_delta else 0
    _new_d = max(0, _draws - _prev_d) if _has_delta else 0
    _old_w = _wins - _new_w
    _old_l = _losses - _new_l
    _old_d = _draws - _new_d

    # ── Welcome header (slim, left-aligned) ───────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:baseline;justify-content:space-between;'
        f'margin:14px 0 12px;padding:0 2px;">'
        f'<span style="font-size:1.1em;font-weight:700;color:#cce0f4;">Welcome back, '
        f'<span style="color:#e2c97e;">{username}</span></span>'
        f'<span style="font-size:0.82em;color:#7a9ab0;">{_total_games} games analysed</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Daily Goals ──────────────────────────────────────────────────────────
    _dg_targets, _dg_progress = _get_daily_goals()
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;margin:10px 0 10px;">DAILY GOALS</div>',
        unsafe_allow_html=True,
    )
    _dg_items = [
        ("Puzzles", "puzzles", "\U0001f9e9", "#4fc3f7", "navigate_to_puzzles"),
        ("Lessons", "lessons", "\U0001f4d6", "#81c784", "navigate_to_coaching"),
        ("Reviews", "review", "\U0001f50d", "#ffb74d", "navigate_to_review"),
    ]
    _dg_cols = st.columns(len(_dg_items))
    for _dg_i, (_dg_label, _dg_key, _dg_icon, _dg_color, _dg_nav) in enumerate(_dg_items):
        with _dg_cols[_dg_i]:
            _dg_cur = _dg_progress.get(_dg_key, 0)
            _dg_tgt = _dg_targets.get(_dg_key, 1)
            _dg_pct = min(100, round(100 * _dg_cur / _dg_tgt)) if _dg_tgt > 0 else 0
            _dg_done_icon = "\u2705" if _dg_cur >= _dg_tgt else _dg_icon
            # Single clickable card: HTML progress bar + invisible-marker + styled button
            st.markdown('<div class="dg-card-marker"></div>', unsafe_allow_html=True)
            if st.button(
                f"{_dg_done_icon} {_dg_label}  {_dg_cur}/{_dg_tgt}",
                key=f"dg_nav_{_dg_key}", use_container_width=True,
            ):
                st.session_state[_dg_nav] = True
                st.rerun()
            # Progress bar sits underneath the button
            st.markdown(
                f'<div style="height:4px;background:#1e2e3e;border-radius:0 0 3px 3px;'
                f'margin-top:-8px;overflow:hidden;">'
                f'<div style="width:{_dg_pct}%;height:100%;background:{_dg_color};'
                f'border-radius:3px;transition:width 0.3s;"></div></div>',
                unsafe_allow_html=True,
            )

    # ── Achievements ──────────────────────────────────────────────────────────
    _unlocked = db.get_achievements()
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;margin:16px 0 10px;">ACHIEVEMENTS</div>',
        unsafe_allow_html=True,
    )
    _ach_count = len(_ACHIEVEMENTS)
    _ach_html = (
        f'<div style="display:grid;grid-template-columns:repeat({_ach_count}, 1fr);gap:8px;">'
    )
    for _ach_key, _ach_def in _ACHIEVEMENTS.items():
        if _ach_key in _unlocked:
            _ach_date = _unlocked[_ach_key][:10] if _unlocked[_ach_key] else ""
            _ach_html += (
                f'<div style="background:#111827;border:1px solid #2a4a6a;border-radius:10px;'
                f'padding:10px 8px;text-align:center;">'
                f'<div style="font-size:1.4em;">{_ach_def["icon"]}</div>'
                f'<div style="font-size:0.68em;font-weight:700;color:#cce0f4;margin-top:4px;'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'{_ach_def["name"]}</div>'
                f'<div style="font-size:0.62em;color:#5a7a8a;">{_ach_date}</div>'
                f'</div>'
            )
        else:
            _ach_html += (
                f'<div style="background:#0a0e18;border:1px solid #1a2030;border-radius:10px;'
                f'padding:10px 8px;text-align:center;opacity:0.4;">'
                f'<div style="font-size:1.4em;">\U0001f512</div>'
                f'<div style="font-size:0.68em;font-weight:700;color:#5a6a7a;margin-top:4px;">???</div>'
                f'</div>'
            )
    _ach_html += '</div>'
    st.markdown(_ach_html, unsafe_allow_html=True)

    # ── Group header: PERFORMANCE ──────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;margin:10px 0 6px;">PERFORMANCE</div>',
        unsafe_allow_html=True,
    )

    # ── Hero row — radar + donut (left) | stats (right) ──────────────────
    _hero_left, _hero_right = st.columns([3, 2], gap="small")

    with _hero_left:
        # Skill Radar header with styled info popover
        st.markdown(
            '<style>'
            '.sr-tip{position:relative;display:inline-flex;align-items:center;justify-content:center;'
            'width:18px;height:18px;border-radius:50%;border:1.5px solid #3a5a8a;color:#7ab3d4;'
            'font-size:0.7em;font-weight:700;cursor:help;transition:border-color .2s;}'
            '.sr-tip:hover{border-color:#7ab3d4;}'
            '.sr-tip .sr-pop{visibility:hidden;opacity:0;position:absolute;left:24px;top:-8px;'
            'width:280px;background:#0d1923;border:1px solid #1e3a5a;border-radius:8px;'
            'padding:12px 14px;z-index:100;transition:opacity .15s;pointer-events:none;'
            'box-shadow:0 4px 16px rgba(0,0,0,.4);}'
            '.sr-tip:hover .sr-pop{visibility:visible;opacity:1;}'
            '.sr-pop b{color:#cce0f4;}.sr-pop span{color:#a0bccc;font-size:0.82em;line-height:1.6;}'
            '</style>'
            '<div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">'
            '<span style="font-size:0.82em;color:#5a7ac8;font-weight:700;letter-spacing:0.06em;">'
            'SKILL RADAR</span>'
            '<div class="sr-tip">i<div class="sr-pop"><span>'
            '<b>Opening Prep</b> — How well you play moves 1–12. Covers theory, development, and early plans.<br>'
            '<b>Middlegame</b> — Your strength in moves 13–30, where most tactical and strategic battles happen.<br>'
            '<b>Endgame</b> — Precision in simplified positions (move 31+). Technique matters most here.<br>'
            '<b>Tactics</b> — How clean your play is. Fewer blunders and mistakes means a higher score.<br>'
            '<b>Consistency</b> — How steady you perform game to game. Less variance = more reliable play.'
            '</span></div></div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Skill Radar chart
        _score_vals = [_dash_skills.get(c, 50) for c in _SKILL_CATS]

        import plotly.graph_objects as _go_dash
        _radar = _go_dash.Figure(_go_dash.Scatterpolar(
            r=_score_vals + [_score_vals[0]],
            theta=_SKILL_CATS + [_SKILL_CATS[0]],
            fill="toself",
            fillcolor="rgba(74,106,170,0.18)",
            line=dict(color="#4a6aaa", width=2),
            marker=dict(size=6, color="#7ab3d4"),
            hovertemplate="%{theta}: %{r}<extra></extra>",
        ))
        _radar.update_layout(
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 100], tickvals=[25, 50, 75, 100],
                                gridcolor="#1e2e3e", linecolor="#1e2e3e",
                                tickfont=dict(color="#7a9ab0", size=9)),
                angularaxis=dict(gridcolor="#1e2e3e", linecolor="#1e2e3e",
                                 tickfont=dict(color="#cce0f4", size=11)),
                bgcolor="#0d1117",
            ),
            paper_bgcolor="#0d1117", height=260,
            margin=dict(l=50, r=50, t=18, b=18), showlegend=False,
        )
        st.plotly_chart(_radar, use_container_width=True, config={"displayModeBar": False})

        # Win Rate Donut chart — old vs new segments
        if _has_delta:
            _donut_labels = ["Wins", "New Wins", "Losses", "New Losses", "Draws", "New Draws"]
            _donut_values = [_old_w, _new_w, _old_l, _new_l, _old_d, _new_d]
            _donut_colors = ["#81c784", "#2e7d32", "#e57373", "#b71c1c", "#78909c", "#455a64"]
        else:
            _donut_labels = ["Wins", "Losses", "Draws"]
            _donut_values = [_wins, _losses, _draws]
            _donut_colors = ["#81c784", "#e57373", "#78909c"]
        _donut = _go_dash.Figure(_go_dash.Pie(
            labels=_donut_labels,
            values=_donut_values,
            hole=0.65,
            marker=dict(colors=_donut_colors),
            textinfo="none",
            hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
            sort=False,
        ))
        _donut.update_layout(
            paper_bgcolor="#0d1117", height=180,
            margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
            annotations=[dict(
                text=f"<b>{_total_games}</b>",
                x=0.5, y=0.5, font=dict(size=22, color="#cce0f4"),
                showarrow=False,
            )],
        )
        st.plotly_chart(_donut, use_container_width=True, config={"displayModeBar": False})
        # Inline legend
        if _has_delta:
            st.markdown(
                f'<div style="text-align:center;font-size:0.78em;color:#a0bccc;margin-top:-8px;">'
                f'<span style="color:#81c784;">●</span> {_old_w}W '
                f'<span style="color:#2e7d32;">●</span> +{_new_w} &nbsp;&nbsp;'
                f'<span style="color:#e57373;">●</span> {_old_l}L '
                f'<span style="color:#b71c1c;">●</span> +{_new_l} &nbsp;&nbsp;'
                f'<span style="color:#78909c;">●</span> {_old_d}D '
                f'<span style="color:#455a64;">●</span> +{_new_d}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="text-align:center;font-size:0.78em;color:#a0bccc;margin-top:-8px;">'
                f'<span style="color:#81c784;">●</span> {_wins}W &nbsp;&nbsp;'
                f'<span style="color:#e57373;">●</span> {_losses}L &nbsp;&nbsp;'
                f'<span style="color:#78909c;">●</span> {_draws}D</div>',
                unsafe_allow_html=True,
            )

    with _hero_right:
        # Trend badge helper — compare current vs previous profile_history entry
        _wr_trend_html = ""
        _acc_trend_html = ""
        if len(_hist) >= 2:
            # Win rate trend
            _prev_rec = _hist[-2].get("record", {})
            _prev_total = sum(_prev_rec.get(k, 0) for k in ("wins", "losses", "draws"))
            if _prev_total > 0:
                _prev_wr = round(100 * _prev_rec.get("wins", 0) / _prev_total, 1)
                _wr_delta = round(_win_rate - _prev_wr, 1)
                if _wr_delta != 0:
                    _wr_arrow = "▲" if _wr_delta > 0 else "▼"
                    _wr_tc = "#81c784" if _wr_delta > 0 else "#e57373"
                    _wr_trend_html = (
                        f'<div style="font-size:0.68em;color:{_wr_tc};font-weight:600;margin-top:4px;">'
                        f'{_wr_arrow} {abs(_wr_delta):+.1f}%</div>'
                    )
            # Accuracy trend (overall_acc from last two history entries)
            _cur_acc = _hist[-1].get("overall_acc")
            _prev_acc = _hist[-2].get("overall_acc")
            if _cur_acc is not None and _prev_acc is not None:
                _acc_delta = round(_cur_acc - _prev_acc, 1)
                if _acc_delta != 0:
                    _acc_arrow = "▲" if _acc_delta > 0 else "▼"
                    _acc_tc = "#81c784" if _acc_delta > 0 else "#e57373"
                    _acc_trend_html = (
                        f'<div style="font-size:0.68em;color:{_acc_tc};font-weight:600;margin-top:4px;">'
                        f'{_acc_arrow} {abs(_acc_delta):+.1f}% acc</div>'
                    )

        # Win Rate hero card
        _wr_color = "#81c784" if _win_rate >= 55 else "#ffb74d" if _win_rate >= 45 else "#e57373"
        st.markdown(
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
            f'padding:12px 12px;text-align:center;margin-bottom:8px;">'
            f'<div style="font-size:0.72em;color:#7a9ab0;font-weight:600;letter-spacing:0.06em;'
            f'margin-bottom:6px;">WIN RATE</div>'
            f'<div style="font-size:2.4em;font-weight:800;color:{_wr_color};">{_win_rate}%</div>'
            f'{_wr_trend_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Mistakes / Game + Blunders / Game side-by-side
        _mc1, _mc2 = st.columns(2)
        with _mc1:
            st.markdown(
                f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
                f'padding:14px 10px;text-align:center;">'
                f'<div style="font-size:1.4em;font-weight:800;color:#fff176;">{_mistakes_pg}</div>'
                f'<div style="font-size:0.68em;color:#7a9ab0;font-weight:600;letter-spacing:0.04em;'
                f'margin-top:3px;">MISTAKES / GAME</div></div>',
                unsafe_allow_html=True,
            )
        with _mc2:
            st.markdown(
                f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
                f'padding:14px 10px;text-align:center;">'
                f'<div style="font-size:1.4em;font-weight:800;color:#ef5350;">{_blunders_pg}</div>'
                f'<div style="font-size:0.68em;color:#7a9ab0;font-weight:600;letter-spacing:0.04em;'
                f'margin-top:3px;">BLUNDERS / GAME</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

        # Best Skill card
        _bs_color = "#81c784" if _best_skill_val >= 70 else "#ffb74d" if _best_skill_val >= 45 else "#e57373"
        st.markdown(
            f'<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
            f'padding:16px 14px;text-align:center;">'
            f'<div style="font-size:0.72em;color:#7a9ab0;font-weight:600;letter-spacing:0.06em;'
            f'margin-bottom:4px;">BEST SKILL · {_best_skill_label}</div>'
            f'<div style="font-size:2em;font-weight:800;color:{_bs_color};">{_best_skill_val}</div>'
            f'{_acc_trend_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)

    # ── Group header: RECOMMENDED FOR YOU ──────────────────────────────────
    st.markdown(
        '<div style="border-top:1px solid #1e2e3e;padding-top:12px;margin-top:4px;">'
        '<span style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;">RECOMMENDED FOR YOU</span></div>',
        unsafe_allow_html=True,
    )

    # ── Action Cards Row (3 equal columns) ────────────────────────────────
    _card_style = (
        'background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
        'padding:16px 14px;height:170px;overflow:hidden;'
    )
    _ac1, _ac2, _ac3 = st.columns(3)

    # Today's Recommendation
    with _ac1:
        _rating = profile.get("chess_com_rating") or profile.get("rapid_rating") or 0
        _rec_html = ""
        try:
            recs = get_recommended_modules(profile, summaries, _rating or None)
            if recs:
                top_rec = recs[0]
                mod = get_module(top_rec["module_id"])
                if mod:
                    _rec_html = (
                        f'<div style="font-size:0.95em;font-weight:700;color:#cce0f4;'
                        f'margin-bottom:4px;">{mod.get("title", top_rec["module_id"])}</div>'
                        f'<div style="font-size:0.78em;color:#a0bccc;line-height:1.5;">'
                        f'{mod.get("description", "")}</div>'
                    )
        except Exception:
            pass
        if not _rec_html:
            _rec_html = (
                '<div style="font-size:0.85em;color:#7a9ab0;padding:8px 0;">'
                'Build more games for recommendations</div>'
            )
        st.markdown(
            f'<div style="{_card_style}border-left:3px solid #5a7ac8;">'
            f'<div><div style="font-size:0.72em;color:#5a7ac8;font-weight:700;'
            f'letter-spacing:0.06em;margin-bottom:8px;">RECOMMENDED</div>'
            f'{_rec_html}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)
        if st.button("Start →", key="dash_rec_start"):
            st.session_state.navigate_to_training = True
            st.rerun()

    # Focus Areas
    with _ac2:
        _focus_areas = profile.get("priority_focus", [])
        if _focus_areas:
            _fa_items = "".join(
                f'<div style="background:#1a1520;border:1px solid #3a2a3a;border-radius:6px;'
                f'padding:6px 10px;margin-bottom:5px;font-size:0.85em;color:#cce0f4;'
                f'font-weight:600;">{c}</div>'
                for c in _focus_areas[:3]
            )
        else:
            _fa_items = (
                '<div style="font-size:0.85em;color:#7a9ab0;padding:8px 0;">'
                'No focus areas identified yet</div>'
            )
        st.markdown(
            f'<div style="{_card_style}border-left:3px solid #e57373;">'
            f'<div><div style="font-size:0.72em;color:#e57373;font-weight:700;'
            f'letter-spacing:0.06em;margin-bottom:8px;">FOCUS AREAS</div>'
            f'{_fa_items}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)
        if st.button("Study →", key="dash_focus"):
            st.session_state.navigate_to_coaching = True
            st.rerun()

    # Courses (curriculum progress)
    with _ac3:
        _cur_progress = db.get_curriculum_progress(username)
        _completed_count = sum(1 for v in _cur_progress.values() if v.get("completed"))
        _total_modules = sum(len(s["modules"]) for s in CURRICULUM.values())
        _rating = profile.get("chess_com_rating") or profile.get("rapid_rating") or 0
        _cur_stage = get_stage_for_rating(_rating) if _rating else 1
        _stage_info = CURRICULUM.get(_cur_stage, {})
        _stage_mods = _stage_info.get("modules", [])
        _stage_done = sum(1 for m in _stage_mods if _cur_progress.get(m["id"], {}).get("completed"))
        if _stage_mods:
            _pct = round(100 * _stage_done / len(_stage_mods))
            _bar_color = "#81c784" if _pct >= 70 else "#ffb74d" if _pct >= 40 else "#5a7ac8"
            _courses_html = (
                f'<div style="font-size:0.88em;font-weight:700;color:#cce0f4;margin-bottom:6px;">'
                f'Stage {_cur_stage}: {_stage_info.get("name", "")}</div>'
                f'<div style="background:#1a2535;border-radius:4px;height:8px;margin-bottom:6px;">'
                f'<div style="width:{_pct}%;background:{_bar_color};border-radius:4px;height:8px;">'
                f'</div></div>'
                f'<div style="font-size:0.78em;color:#a0bccc;">'
                f'{_stage_done}/{len(_stage_mods)} modules · {_completed_count} total completed</div>'
            )
        else:
            _courses_html = (
                '<div style="font-size:0.85em;color:#7a9ab0;padding:8px 0;">'
                'Start training to track progress</div>'
            )
        # Check for review-due items and append a small note
        _review_due = db.get_review_due_concepts(days=3, threshold=0.8)
        if _review_due:
            _courses_html += (
                f'<div style="font-size:0.72em;color:#ffb74d;margin-top:8px;font-weight:600;">'
                f'{len(_review_due)} concept{"s" if len(_review_due) != 1 else ""} due for review</div>'
            )
        st.markdown(
            f'<div style="{_card_style}border-left:3px solid #e2c97e;">'
            f'<div><div style="font-size:0.72em;color:#e2c97e;font-weight:700;'
            f'letter-spacing:0.06em;margin-bottom:8px;">COURSES</div>'
            f'{_courses_html}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)
        if _review_due:
            if st.button("Review Now →", key="dash_review_due"):
                st.session_state.selected_concept = _review_due[0]["concept"]
                st.session_state.navigate_to_coaching = True
                st.rerun()
        else:
            if st.button("Continue →", key="dash_review_due"):
                st.session_state.navigate_to_training = True
                st.rerun()

    st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)

    # ── Group header: QUICK ACTIONS ───────────────────────────────────────
    st.markdown(
        '<div style="border-top:1px solid #1e2e3e;padding-top:12px;margin-top:4px;">'
        '<span style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;">QUICK ACTIONS</span></div>',
        unsafe_allow_html=True,
    )
    _qa_card = (
        '<div style="background:#0f1923;border:1px solid #1e2e3e;border-radius:10px;'
        'padding:18px 14px;text-align:center;">'
        '<div style="font-size:1.6em;margin-bottom:6px;">{icon}</div>'
        '<div style="font-size:0.88em;font-weight:700;color:#cce0f4;margin-bottom:4px;">{title}</div>'
        '<div style="font-size:0.74em;color:#8a9eb5;line-height:1.4;">{desc}</div></div>'
    )
    qa1, qa2, qa3 = st.columns(3)
    with qa1:
        st.markdown(_qa_card.format(
            icon="♟", title="Solve Puzzles",
            desc="Practice tactics from your games",
        ), unsafe_allow_html=True)
        if st.button("Open Puzzles", key="dash_puzzles", use_container_width=True):
            st.session_state.navigate_to_puzzles = True
            st.rerun()
    with qa2:
        st.markdown(_qa_card.format(
            icon="♔", title="Training",
            desc="Continue your personalised curriculum",
        ), unsafe_allow_html=True)
        if st.button("Open Training", key="dash_training", use_container_width=True):
            st.session_state.navigate_to_training = True
            st.rerun()
    with qa3:
        st.markdown(_qa_card.format(
            icon="♛", title="Review a Game",
            desc="Analyse any PGN move by move",
        ), unsafe_allow_html=True)
        if st.button("Open Review", key="dash_review", use_container_width=True):
            st.session_state.navigate_to_review = True
            st.rerun()

    # ── Group header: RECENT GAMES ─────────────────────────────────────────
    st.markdown(
        '<div style="border-top:1px solid #1e2e3e;padding-top:12px;margin-top:16px;">'
        '<span style="font-size:0.7em;color:#4a6080;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;">RECENT GAMES</span></div>',
        unsafe_allow_html=True,
    )
    _recent_games = st.session_state.get("profile_summaries", [])[:8]
    if not _recent_games:
        st.markdown(
            '<div style="text-align:center;padding:24px 0;color:#5a7a8a;font-size:0.88em;">'
            'Play some games and build your profile to see recent games here.</div>',
            unsafe_allow_html=True,
        )
    else:
        for _rg_i, _rg in enumerate(_recent_games):
            _rg_date = _rg.get("date", "")[:10] or "—"
            # Derive opponent from white/black + player_color
            _rg_pc = _rg.get("player_color", "white")
            _rg_opp = _rg.get("black", "?") if _rg_pc == "white" else _rg.get("white", "?")
            # Derive result from PGN result + player_color
            _rg_pgn_result = _rg.get("result", "*")
            if _rg_pgn_result == "1-0":
                _rg_outcome = "win" if _rg_pc == "white" else "loss"
            elif _rg_pgn_result == "0-1":
                _rg_outcome = "win" if _rg_pc == "black" else "loss"
            elif _rg_pgn_result == "1/2-1/2":
                _rg_outcome = "draw"
            else:
                _rg_outcome = "draw"
            _rg_mistakes = _rg.get("mistakes", 0)
            _rg_blunders = _rg.get("blunders", 0)
            # Result badge
            if _rg_outcome == "win":
                _rg_badge = '<span style="background:#2e7d32;color:#c8e6c9;font-size:0.72em;font-weight:700;border-radius:4px;padding:2px 8px;">WIN</span>'
            elif _rg_outcome == "loss":
                _rg_badge = '<span style="background:#b71c1c;color:#ffcdd2;font-size:0.72em;font-weight:700;border-radius:4px;padding:2px 8px;">LOSS</span>'
            else:
                _rg_badge = '<span style="background:#455a64;color:#b0bec5;font-size:0.72em;font-weight:700;border-radius:4px;padding:2px 8px;">DRAW</span>'
            # Mistakes & blunders display
            _rg_m_color = "#fff176" if _rg_mistakes > 0 else "#5a7a8a"
            _rg_b_color = "#ef5350" if _rg_blunders > 0 else "#5a7a8a"
            _rg_errors_html = (
                f'<span style="font-size:0.78em;color:{_rg_m_color};font-weight:600;">{_rg_mistakes}m</span>'
                f'<span style="font-size:0.72em;color:#3a5a6a;"> · </span>'
                f'<span style="font-size:0.78em;color:{_rg_b_color};font-weight:600;">{_rg_blunders}b</span>'
            )

            _rg_row_cols = st.columns([1.2, 1.8, 1, 1.2, 1.2])
            with _rg_row_cols[0]:
                st.markdown(f'<div style="font-size:0.78em;color:#7a9ab0;padding-top:6px;">{_rg_date}</div>', unsafe_allow_html=True)
            with _rg_row_cols[1]:
                st.markdown(f'<div style="font-size:0.85em;color:#cce0f4;font-weight:600;padding-top:4px;">vs {_rg_opp}</div>', unsafe_allow_html=True)
            with _rg_row_cols[2]:
                st.markdown(f'<div style="padding-top:4px;">{_rg_badge}</div>', unsafe_allow_html=True)
            with _rg_row_cols[3]:
                st.markdown(f'<div style="padding-top:5px;">{_rg_errors_html}</div>', unsafe_allow_html=True)
            with _rg_row_cols[4]:
                if _rg.get("_pgn"):
                    if st.button("Review →", key=f"dash_rg_review_{_rg_i}", use_container_width=True):
                        _deep_dive_to_review(_rg["_pgn"], _rg.get("white", "?"), _rg.get("black", "?"))


def render_profile_tab():
    # ── Controls (platform + username moved above sub-nav) ───────────────────
    profile_platform = st.session_state.get("profile_platform", "Chess.com")
    username = st.session_state.get("profile_username", "").strip().lower()

    with st.expander("Advanced settings"):
        _adv1, _adv2 = st.columns(2)
        with _adv1:
            n_months = st.number_input("Months of games", min_value=1, max_value=6, value=2,
                                       key="profile_months")
        with _adv2:
            depth_choice = st.selectbox(
                "Analysis depth",
                options=[10, 12, 15],
                index=1,
                format_func=lambda d: {10: "Quick (d10)", 12: "Standard (d12)", 15: "Deep (d15)"}[d],
                key="profile_depth",
            )
            st.caption(_DEPTH_INFO[int(depth_choice)])

    _est = _estimate_analysis_time(int(n_months), int(depth_choice))
    build_btn = st.button(
        f"▶ Build Profile ({_est})",
        type="primary",
        use_container_width=True,
    )

    # Auto-trigger build when arriving from Dashboard onboarding
    if st.session_state.pop("_auto_build", False) and not build_btn:
        build_btn = True

    # ── Incremental update detection ────────────────────────────────────────
    _ng_cache_key = f"_new_games_check_{username}"
    _ng_params_key = "_new_games_params"

    # Invalidate cache if username or months changed
    prev_params = st.session_state.get(_ng_params_key)
    if prev_params != (username, int(n_months)):
        st.session_state.pop(_ng_cache_key, None)
        st.session_state[_ng_params_key] = (username, int(n_months))

    update_btn = False
    if not build_btn:
        saved_profile = db.load_profile(username)
        if saved_profile:
            _, existing_summaries, built_at_str = saved_profile
            if _ng_cache_key not in st.session_state:
                # First check this session — fetch and compare
                try:
                    if profile_platform == "Lichess":
                        fetched_games = lichess.fetch_recent_games(username, int(n_months))
                    else:
                        fetched_games = chesscom.fetch_recent_games(username, int(n_months))
                    existing_keys = {_summary_dedup_key(s) for s in existing_summaries}
                    new_games = [g for g in fetched_games
                                 if _game_dedup_key(g["headers"]) not in existing_keys]
                    st.session_state[_ng_cache_key] = new_games
                except Exception:
                    st.session_state[_ng_cache_key] = []

            cached_new = st.session_state.get(_ng_cache_key, [])
            if cached_new:
                n_new = len(cached_new)
                built_date = built_at_str[:10]
                st.markdown(
                    f'<div style="background:#1a1a2e;border:1px solid #3a6ea5;border-radius:10px;'
                    f'padding:14px 18px;margin:10px 0;">'
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
                    f'<span style="font-size:1.2em;">🆕</span>'
                    f'<span style="font-size:0.95em;font-weight:700;color:#7ab4e0;">'
                    f'{n_new} new game{"s" if n_new != 1 else ""} found since last build'
                    f'</span>'
                    f'</div>'
                    f'<div style="font-size:0.78em;color:#6a8a9a;">'
                    f'{len(existing_summaries)} games analysed on {built_date} '
                    f'&nbsp;&middot;&nbsp; {n_new} new to analyse</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                _upd_est = _estimate_from_game_count(n_new, int(depth_choice))
                update_btn = st.button(
                    f"⚡ Update Profile — analyse {n_new} new game{'s' if n_new != 1 else ''} ({_upd_est})",
                    type="primary",
                    use_container_width=True,
                )

    if update_btn:
        # Double-click guard
        if username in _BUILD_JOBS:
            st.warning("A profile build is already in progress.")
        elif _api_limit_reached():
            pass
        else:
            _count_api_call()
            new_games_to_analyse = st.session_state.get(_ng_cache_key, [])
            saved_profile = db.load_profile(username)
            if not saved_profile or not new_games_to_analyse:
                st.warning("Nothing to update.")
                return
            _, existing_summaries, _ = saved_profile

            st.session_state.pop("profile_data", None)
            st.session_state.pop("profile_summaries", None)
            st.session_state.profile_username_built = username

            job = {
                "status": "analyzing", "done": 0, "total": len(new_games_to_analyse),
                "eta_secs": 0, "started": time.time(), "result": None, "error": None,
                "platform": profile_platform, "depth": int(depth_choice),
                "is_update": True, "existing_summaries": existing_summaries,
                "ng_cache_key": _ng_cache_key,
            }
            with _BUILD_LOCK:
                _BUILD_JOBS[username] = job
            threading.Thread(
                target=_run_profile_build,
                args=(username, new_games_to_analyse, int(depth_choice), profile_platform, job),
                daemon=True,
            ).start()
            st.session_state["_build_username"] = username
            st.rerun()

    if build_btn:
        # Double-click guard
        if username in _BUILD_JOBS:
            st.warning("A profile build is already in progress.")
        elif _api_limit_reached():
            pass
        else:
            _count_api_call()
            # Clear any existing profile for this user
            st.session_state.pop("profile_data", None)
            st.session_state.pop("profile_summaries", None)
            st.session_state.profile_username_built = username

            # ── Fetch games (quick, ~2s — stays synchronous) ─────────────────
            with st.spinner(f"Fetching games for {username}..."):
                try:
                    if profile_platform == "Lichess":
                        games = lichess.fetch_recent_games(username, int(n_months), bypass_cache=True)
                    else:
                        games = chesscom.fetch_recent_games(username, int(n_months), bypass_cache=True)
                except Exception as e:
                    st.error(f"Failed to fetch from {profile_platform}: {e}")
                    return

            if not games:
                st.warning(f"No games found for {username} in the last {n_months} month(s).")
                return

            if len(games) > _MAX_BUILD_GAMES:
                st.info(f"Found {len(games)} games — analysing the {_MAX_BUILD_GAMES} most recent for performance.")
                games = games[:_MAX_BUILD_GAMES]

            job = {
                "status": "analyzing", "done": 0, "total": len(games),
                "eta_secs": 0, "started": time.time(), "result": None, "error": None,
                "platform": profile_platform, "depth": int(depth_choice),
                "is_update": False, "existing_summaries": None,
                "ng_cache_key": _ng_cache_key,
            }
            with _BUILD_LOCK:
                _BUILD_JOBS[username] = job
            threading.Thread(
                target=_run_profile_build,
                args=(username, games, int(depth_choice), profile_platform, job),
                daemon=True,
            ).start()
            st.session_state["_build_username"] = username
            st.rerun()

    # ── Guard: show info while build is running ──────────────────────────────
    if st.session_state.get("_build_username"):
        _guard_user = st.session_state["_build_username"]
        _guard_job = _BUILD_JOBS.get(_guard_user)
        if _guard_job:
            _gd = _guard_job.get("done", 0)
            _gt = max(_guard_job.get("total", 1), 1)
            _ge = _guard_job.get("eta_secs", 0)
            _ge_s = f" (~{int(_ge)}s left)" if _ge > 0 else ""
            if _guard_job.get("status") == "synthesizing":
                st.info("♔ ⚔ ♚  Claude is synthesizing your profile… almost done!")
            else:
                st.progress(_gd / _gt, text=f"♔ ⚔ ♚  Analysing game {_gd}/{_gt}{_ge_s}")
        else:
            st.info("Your profile is being built in the background. Feel free to explore other tabs!")
        return

    # ── Display profile — restore from DB if session was cleared ──────────────
    if "profile_data" not in st.session_state:
        username_now = st.session_state.get("profile_username", "")
        saved = db.load_profile(username_now)
        if saved:
            p_data, p_summaries, built_at = saved
            st.session_state.profile_data           = p_data
            st.session_state.profile_summaries      = p_summaries
            st.session_state.profile_built_at       = built_at
            st.session_state.profile_username_built = username_now
            db.save_active_user(username_now, profile_platform)
        else:
            st.info(
                "Enter your username and click "
                "**▶ Build Profile** to generate your personalised coaching profile."
            )
            return

    profile   = st.session_state.profile_data
    n_games   = profile.get("n_games", 0)
    depth_val = st.session_state.get("profile_build_depth", 12)

    if not st.session_state.get("profile_built_at"):
        # ── Freshly built this session — show Study Complete banner ───────────
        st.markdown(
            f'<div style="background:#0d1f12;border:1px solid #2a5a32;border-radius:10px;'
            f'padding:16px 20px;margin-bottom:18px;">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
            f'<span style="font-size:1.4em;">✅</span>'
            f'<span style="font-size:1.05em;font-weight:700;color:#81c784;">Study Complete</span>'
            f'</div>'
            f'<div style="font-size:0.83em;color:#a0c4a8;line-height:1.6;">'
            f'Stockfish analysed <b style="color:#cce0f4;">{n_games} games</b> at depth {depth_val}, '
            f'then Claude reviewed your patterns across openings, middlegames, and endgames.</div>'
            f'<div style="margin-top:10px;font-size:0.78em;color:#5a8a6a;">'
            f'<b style="color:#7ab07a;">What\'s below:</b> &nbsp;'
            f'Skill ratings across 6 categories (click <b>↓ Drill</b> on any to see your exact mistakes) &nbsp;·&nbsp; '
            f'3 priority concepts to study &nbsp;·&nbsp; '
            f'Your most instructive games for a full deep dive'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        # ── Restored from saved profile ───────────────────────────────────────
        built_at_str = st.session_state.profile_built_at[:10]
        st.markdown(
            f'<div style="text-align:right;font-size:0.72em;color:#4a6a7a;margin-bottom:10px;">'
            f'↺ Restored from last build &nbsp;·&nbsp; {built_at_str}'
            f'&nbsp;&nbsp;<span style="color:#2a4a5a;">— click ▶ Build Profile to refresh</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Time control filter ───────────────────────────────────────────────────
    _all_sums = st.session_state.get("profile_summaries", [])
    _tc_order  = ["Bullet", "Blitz", "Rapid", "Classical"]
    _tc_counts = {}
    for _s in _all_sums:
        _tc = _s.get("time_control", "Unknown")
        _tc_counts[_tc] = _tc_counts.get(_tc, 0) + 1
    _available = [tc for tc in _tc_order if tc in _tc_counts]
    # Only show filter when multiple time controls are present
    if len(_available) > 1:
        _active_tc = st.session_state.get("profile_tc_filter", "All")
        _tc_opts   = ["All"] + _available
        _f_cols    = st.columns(len(_tc_opts))
        for _i, _tc in enumerate(_tc_opts):
            _count = len(_all_sums) if _tc == "All" else _tc_counts.get(_tc, 0)
            with _f_cols[_i]:
                if st.button(
                    f"{_tc}  ({_count})", key=f"tc_filter_{_tc}",
                    type="primary" if _tc == _active_tc else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.profile_tc_filter = _tc
                    st.rerun()
        st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
    else:
        st.session_state.pop("profile_tc_filter", None)

    _active_tc = st.session_state.get("profile_tc_filter", "All")
    disp_sums  = (
        _all_sums if _active_tc == "All"
        else [s for s in _all_sums if s.get("time_control", "Unknown") == _active_tc]
    )

    # Header card — compute error rates from filtered summaries
    if disp_sums:
        _bpg = round(sum(s["blunders"] for s in disp_sums) / len(disp_sums), 1) if len(disp_sums) else 0
        _mpg = round(sum(s["mistakes"] for s in disp_sums) / len(disp_sums), 1) if len(disp_sums) else 0
        _rec_w = sum(1 for s in disp_sums if
                     (s["result"] == "1-0" and s["player_color"] == "white") or
                     (s["result"] == "0-1" and s["player_color"] == "black"))
        _rec_l = sum(1 for s in disp_sums if
                     (s["result"] == "0-1" and s["player_color"] == "white") or
                     (s["result"] == "1-0" and s["player_color"] == "black"))
        _rec_d = len(disp_sums) - _rec_w - _rec_l
        profile = {
            **profile,
            "blunders_per_game": _bpg,
            "mistakes_per_game": _mpg,
            "n_games": len(disp_sums),
            "record": {"wins": _rec_w, "losses": _rec_l, "draws": _rec_d},
        }
    # ── Panel 1: YOUR PROFILE ──────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
        'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
        'margin-top:4px;">'
        'YOUR PROFILE</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_profile_overview_html(profile), unsafe_allow_html=True)

    # Summary + strengths (side-by-side when both present)
    summary   = profile.get("summary", "")
    strengths = profile.get("strengths", [])
    _summary_html = (
        f'<div style="background:#0e1117;border-left:3px solid #4a6aaa;'
        f'padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:14px;">'
        f'<span style="color:#c0d0e0;">{summary}</span></div>'
    ) if summary else ""
    _strengths_html = ""
    if strengths:
        _badges = "".join(
            f'<span style="background:#1a2e1a;border:1px solid #2a5a2a;color:#81c784;'
            f'font-size:0.8em;border-radius:4px;padding:3px 10px;margin:2px;display:inline-block;">'
            f'✓ {s}</span>'
            for s in strengths
        )
        _strengths_html = (
            f'<div style="margin-bottom:16px;">'
            f'<span style="font-size:0.7em;color:#7a9ab0;font-weight:700;'
            f'letter-spacing:0.06em;">STRENGTHS&nbsp;&nbsp;</span>'
            f'{_badges}</div>'
        )
    if summary and strengths:
        _sum_l, _sum_r = st.columns([3, 2])
        with _sum_l:
            st.markdown(_summary_html, unsafe_allow_html=True)
        with _sum_r:
            st.markdown(_strengths_html, unsafe_allow_html=True)
    elif summary:
        st.markdown(_summary_html, unsafe_allow_html=True)
    elif strengths:
        st.markdown(_strengths_html, unsafe_allow_html=True)

    # ── Panel 2: YOUR SKILLS ──────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
        'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
        'margin-top:10px;padding-top:8px;border-top:1px solid #152030;">'
        'YOUR SKILLS</div>',
        unsafe_allow_html=True,
    )

    # Compute skill scores from actual game data
    _skill_scores = compute_skill_scores(disp_sums)
    skill_ratings = profile.get("skill_ratings", {})

    # Radar chart + score bars side-by-side
    import plotly.graph_objects as _go
    _score_vals = [_skill_scores.get(c, 50) for c in _SKILL_CATS]
    radar_fig = _go.Figure(_go.Scatterpolar(
        r     = _score_vals + [_score_vals[0]],
        theta = _SKILL_CATS + [_SKILL_CATS[0]],
        fill  = "toself",
        fillcolor = "rgba(74,106,170,0.18)",
        line  = dict(color="#4a6aaa", width=2),
        marker= dict(size=6, color="#7ab3d4"),
        hovertemplate="%{theta}: %{r}%<extra></extra>",
    ))
    radar_fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickvals=[25, 50, 75, 100],
                            gridcolor="#1e2e3e", linecolor="#1e2e3e",
                            tickfont=dict(color="#7a9ab0", size=9)),
            angularaxis=dict(gridcolor="#1e2e3e", linecolor="#1e2e3e",
                             tickfont=dict(color="#cce0f4", size=12)),
            bgcolor="#0d1117",
        ),
        paper_bgcolor="#0d1117", height=280,
        margin=dict(l=60, r=60, t=20, b=20), showlegend=False,
    )
    _radar_col, _bars_col = st.columns([3, 2])
    with _radar_col:
        st.plotly_chart(radar_fig, use_container_width=True, config={"displayModeBar": False})
    with _bars_col:
        _bars_html = ""
        for _cat in _SKILL_CATS:
            _sc = _skill_scores.get(_cat, 50)
            _bc = "#81c784" if _sc >= 70 else "#ffb74d" if _sc >= 45 else "#e57373"
            _bars_html += (
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                f'<span style="font-size:0.82em;color:#a0bcd4;font-weight:600;'
                f'min-width:100px;">{_cat}</span>'
                f'<div style="flex:1;height:6px;background:#1e2e3e;border-radius:3px;overflow:hidden;">'
                f'<div style="width:{_sc}%;height:100%;background:{_bc};border-radius:3px;"></div></div>'
                f'<span style="font-size:0.82em;font-weight:800;color:{_bc};'
                f'min-width:28px;text-align:right;">{_sc}</span>'
                f'</div>'
            )
        st.markdown(
            f'<div style="padding-top:20px;">{_bars_html}</div>',
            unsafe_allow_html=True,
        )

    if st.button("Start Training Curriculum", key="start_training",
                 use_container_width=True, type="primary"):
        _rating = st.session_state.get("profile_data", {}).get("chess_com_rating", 0)
        if _rating:
            st.session_state.ttr_selected_stage = get_stage_for_rating(int(_rating))
        st.session_state.navigate_to_training = True
        st.rerun()

    # Priority focus + Coach message (side-by-side when both present)
    priority  = profile.get("priority_focus", [])
    coach_msg = profile.get("coach_message", "")
    if priority and coach_msg:
        _focus_col, _coach_col = st.columns(2)
        with _focus_col:
            st.markdown(
                '<div style="font-size:0.75em;color:#7a9ad0;font-weight:700;'
                'letter-spacing:0.04em;margin:16px 0 10px;">PRIORITY FOCUS</div>',
                unsafe_allow_html=True,
            )
            for j, concept in enumerate(priority):
                st.markdown(
                    f'<div style="background:#111827;border:1px solid #1e2e3e;border-radius:10px;'
                    f'padding:14px;text-align:center;margin-bottom:6px;">'
                    f'<div style="font-size:0.92em;font-weight:700;color:#cce0f4;">{concept}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Study →",
                    key=f"profile_focus_{j}",
                    use_container_width=True,
                ):
                    st.session_state.selected_concept     = concept
                    st.session_state.navigate_to_coaching = True
                    st.rerun()
        with _coach_col:
            st.markdown(
                '<div style="font-size:0.75em;color:#e2c97e;font-weight:700;'
                'letter-spacing:0.04em;margin:16px 0 10px;">COACH MESSAGE</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="background:#111320;border:1px solid #2a3a5a;'
                f'border-left:3px solid #e2c97e;border-radius:8px;padding:18px 22px;">'
                f'<div style="font-size:1.05em;color:#cce0f4;line-height:1.65;">{coach_msg}</div>'
                f'<div style="margin-top:12px;display:flex;align-items:center;gap:7px;">'
                f'<span style="font-size:1.1em;color:#e2c97e;">♔</span>'
                f'<span style="font-size:0.78em;font-weight:700;color:#e2c97e;letter-spacing:0.07em;">'
                f'BOARDSENSE COACH</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
    elif priority:
        st.markdown(
            '<div style="font-size:0.75em;color:#7a9ad0;font-weight:700;'
            'letter-spacing:0.04em;margin:16px 0 10px;">PRIORITY FOCUS</div>',
            unsafe_allow_html=True,
        )
        pcols = st.columns(len(priority))
        for j, concept in enumerate(priority):
            with pcols[j]:
                st.markdown(
                    f'<div style="background:#111827;border:1px solid #1e2e3e;border-radius:10px;'
                    f'padding:14px;text-align:center;margin-bottom:6px;">'
                    f'<div style="font-size:0.92em;font-weight:700;color:#cce0f4;">{concept}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Study →",
                    key=f"profile_focus_{j}",
                    use_container_width=True,
                ):
                    st.session_state.selected_concept     = concept
                    st.session_state.navigate_to_coaching = True
                    st.rerun()
    elif coach_msg:
        st.markdown(
            '<div style="font-size:0.75em;color:#e2c97e;font-weight:700;'
            'letter-spacing:0.04em;margin:16px 0 10px;">COACH MESSAGE</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="background:#111320;border:1px solid #2a3a5a;'
            f'border-left:3px solid #e2c97e;border-radius:8px;padding:18px 22px;">'
            f'<div style="font-size:1.05em;color:#cce0f4;line-height:1.65;">{coach_msg}</div>'
            f'<div style="margin-top:12px;display:flex;align-items:center;gap:7px;">'
            f'<span style="font-size:1.1em;color:#e2c97e;">♔</span>'
            f'<span style="font-size:0.78em;font-weight:700;color:#e2c97e;letter-spacing:0.07em;">'
            f'BOARDSENSE COACH</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # ── Panel: ERROR PATTERNS ──────────────────────────────────────────────
    _error_map = _get_error_concept_map(disp_sums)
    if _error_map:
        st.markdown(
            '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
            'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
            'margin-top:10px;padding-top:8px;border-top:1px solid #152030;">'
            'ERROR PATTERNS</div>',
            unsafe_allow_html=True,
        )
        _ep_items = list(_error_map.items())[:8]
        _ep_concepts = [c for c, _ in _ep_items]
        _ep_counts = [n for _, n in _ep_items]
        _ep_colors = [CATEGORY_COLORS.get(_concept_to_category(c), "#78909c") for c in _ep_concepts]
        import plotly.graph_objects as _ep_go
        _ep_fig = _ep_go.Figure(_ep_go.Bar(
            x=_ep_counts, y=_ep_concepts, orientation="h",
            marker=dict(color=_ep_colors),
            hovertemplate="%{y}: %{x} error(s)<extra></extra>",
        ))
        _ep_fig.update_layout(
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117", height=max(180, len(_ep_items) * 34),
            margin=dict(l=140, r=20, t=10, b=10),
            xaxis=dict(title="Errors", gridcolor="#1e2e3e", tickfont=dict(color="#7a9ab0", size=10)),
            yaxis=dict(autorange="reversed", tickfont=dict(color="#cce0f4", size=11)),
        )
        st.plotly_chart(_ep_fig, use_container_width=True, config={"displayModeBar": False})
        # Concept navigation buttons
        _ep_btn_cols = st.columns(min(len(_ep_concepts), 4))
        for _ep_i, _ep_c in enumerate(_ep_concepts[:4]):
            with _ep_btn_cols[_ep_i]:
                if st.button(f"{_ep_c} \u2192", key=f"ep_nav_{_ep_i}", use_container_width=True):
                    st.session_state.selected_concept = _ep_c
                    st.session_state.navigate_to_coaching = True
                    st.rerun()
    else:
        st.markdown(
            '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
            'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
            'margin-top:10px;padding-top:8px;border-top:1px solid #152030;">'
            'ERROR PATTERNS</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="color:#5a7a8a;font-size:0.85em;">No error patterns detected yet</p>',
            unsafe_allow_html=True,
        )

    # ── Panel: COMPARE YOUR PLAY ─────────────────────────────────────────
    # (Feature 8: Comparative Analytics — use all summaries, not pre-filtered)
    _all_skills = compute_skill_scores(_all_sums)
    _render_comparative_analytics(_all_sums, _all_skills, _SKILL_CATS)

    # ── Panel: TIME MANAGEMENT ───────────────────────────────────────────
    # (Feature 5: Time Management Insights — inserted here)
    _render_time_management(disp_sums)

    # ── Panel 3: DEEP DIVES ──────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.7em;color:#4a6080;font-weight:700;'
        'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:14px;'
        'margin-top:10px;padding-top:8px;border-top:1px solid #152030;">'
        'DEEP DIVES</div>',
        unsafe_allow_html=True,
    )

    if _active_tc != "All":
        st.markdown(
            f'<p style="font-size:0.74em;color:#4a6a7a;text-align:right;margin-bottom:4px;">'
            f'Showing {_active_tc} games only &nbsp;·&nbsp; '
            f'Skill ratings reflect all time controls</p>',
            unsafe_allow_html=True,
        )

    # Color breakdown + Progress tracking side-by-side
    _dd_left, _dd_right = st.columns(2)
    with _dd_left:
        _render_color_breakdown(disp_sums, inline=True)
    with _dd_right:
        _render_progress_tracking(profile.get("username", ""), inline=True)

    _render_opening_repertoire(disp_sums, inline=True)

    # Most Instructive Games
    worst = sorted(
        [s for s in disp_sums if s.get("_pgn")],
        key=lambda s: s["blunders"] * 2 + s["mistakes"],
        reverse=True,
    )[:4]

    if worst:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:9px;margin:16px 0 8px;">'
            '<div style="width:3px;height:15px;background:#e53935;border-radius:2px;flex-shrink:0;"></div>'
            '<span style="font-size:0.9em;color:#e57373;font-weight:700;'
            'letter-spacing:0.04em;">MOST INSTRUCTIVE GAMES</span>'
            '</div>'
            '<p style="font-size:0.8em;color:#7a9ab0;margin-bottom:12px;">'
            'Your most error-prone games — ideal candidates for a deep dive at full depth.</p>',
            unsafe_allow_html=True,
        )

        ig_cols = st.columns(len(worst))
        for j, s in enumerate(worst):
            opponent = s["black"] if s["player_color"] == "white" else s["white"]
            my_sym   = "⬜" if s["player_color"] == "white" else "⬛"
            result   = s.get("result", "*")
            if result == "1/2-1/2":
                res_text, res_color = "Draw", "#aaa"
            elif (result == "1-0") == (s["player_color"] == "white"):
                res_text, res_color = "Win", "#81c784"
            else:
                res_text, res_color = "Loss", "#e57373"

            with ig_cols[j]:
                st.markdown(
                    f'<div style="background:#111827;border:1px solid #2a1a1a;border-radius:10px;'
                    f'padding:14px;text-align:center;margin-bottom:6px;">'
                    f'<div style="font-size:0.72em;color:#7a9ab0;margin-bottom:4px;">'
                    f'{s.get("date","")[:7]}</div>'
                    f'<div style="font-size:0.88em;font-weight:700;color:#cce0f4;margin-bottom:2px;">'
                    f'{my_sym} vs {opponent}</div>'
                    f'<div style="font-size:0.78em;color:{res_color};margin-bottom:8px;">{res_text}</div>'
                    f'<div style="font-size:0.8em;color:#aaa;margin-bottom:8px;">'
                    f'🔴 {s["blunders"]}B &nbsp; 🟠 {s["mistakes"]}M</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("Deep Dive →", key=f"deep_dive_{j}", use_container_width=True):
                    _deep_dive_to_review(s["_pgn"], s["white"], s["black"])


# ── Tab: Openings ────────────────────────────────────────────────────────────

def render_openings_tab():
    st.markdown(
        '<p style="text-align:center;color:#a0bccc;font-size:0.88em;margin:12px 0 20px;">'
        'Your opening repertoire drawn from the most recently built profile.</p>',
        unsafe_allow_html=True,
    )

    summaries = st.session_state.get("profile_summaries", [])
    if not summaries:
        st.info("Build your profile from the **Dashboard** to see your opening stats.")
        if st.button("Go to Dashboard", key="open_to_dash"):
            st.session_state.navigate_to_dashboard = True
            st.rerun()
        return

    # Reuse the existing opening repertoire renderer
    _render_opening_repertoire(summaries)

    # ── Insight callout: best and worst openings ──────────────────────────────
    import io as _io

    def _get_op(s):
        op = s.get("opening", "")
        if op:
            return op
        pgn = s.get("_pgn", "")
        if pgn:
            try:
                g = chess.pgn.read_game(_io.StringIO(pgn))
                if g:
                    h = dict(g.headers)
                    return (h.get("Opening", "")
                            or h.get("ECOUrl", "").split("/")[-1].replace("-", " ").title()
                            or h.get("ECO", ""))
            except Exception:
                pass
        return ""

    from collections import defaultdict as _defaultdict
    op_stats: dict = _defaultdict(lambda: {"wins": 0, "blunders": 0, "mistakes": 0, "n": 0})
    for s in summaries:
        op = (_get_op(s) or "Unknown")[:45]
        op_stats[op]["blunders"] += s["blunders"]
        op_stats[op]["mistakes"] += s["mistakes"]
        op_stats[op]["n"] += 1
        result, color = s.get("result", "*"), s["player_color"]
        if (result == "1-0" and color == "white") or (result == "0-1" and color == "black"):
            op_stats[op]["wins"] += 1

    qualified = {op: v for op, v in op_stats.items() if v["n"] >= 2 and op != "Unknown"}
    if qualified:
        win_rates     = {op: v["wins"] / v["n"] for op, v in qualified.items()}
        blunder_rates = {op: v["blunders"] / v["n"] for op, v in qualified.items()}
        best_op  = max(win_rates, key=win_rates.get)
        worst_op = max(blunder_rates, key=blunder_rates.get)
        best_n   = op_stats[best_op]["n"]
        worst_n  = op_stats[worst_op]["n"]
        best_wr  = round(win_rates[best_op] * 100)
        worst_bpg = round(blunder_rates[worst_op], 1)
        worst_mpg = round(op_stats[worst_op]["mistakes"] / worst_n, 1) if worst_n else 0

        insight_rows = ""
        if best_op != worst_op:
            insight_rows += (
                f'<div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:10px;">'
                f'<span style="font-size:1.1em;margin-top:1px;">✅</span>'
                f'<div><div style="color:#81c784;font-weight:700;font-size:0.9em;">{best_op}</div>'
                f'<div style="color:#a0bccc;font-size:0.82em;">'
                f'{best_wr}% win rate · {best_n} game{"s" if best_n != 1 else ""}</div></div></div>'
            )
            insight_rows += (
                f'<div style="display:flex;align-items:flex-start;gap:10px;">'
                f'<span style="font-size:1.1em;margin-top:1px;">⚠️</span>'
                f'<div><div style="color:#ffb74d;font-weight:700;font-size:0.9em;">{worst_op}</div>'
                f'<div style="color:#a0bccc;font-size:0.82em;">'
                f'{worst_n} game{"s" if worst_n != 1 else ""} · '
                f'🔴 {worst_bpg} blunders &nbsp;🟠 {worst_mpg} mistakes per game'
                f'</div></div></div>'
            )
            st.markdown(
                f'<div style="background:#111827;border:1px solid #1e2e3e;border-radius:10px;'
                f'padding:16px 18px;margin-bottom:20px;">'
                f'<div style="font-size:0.72em;color:#a0bccc;font-weight:700;letter-spacing:0.08em;'
                f'margin-bottom:12px;">OPENING INSIGHTS</div>'
                + insight_rows +
                '</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")
    st.markdown(
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px;">'
        '<div style="width:3px;height:15px;background:#9c7c38;border-radius:2px;flex-shrink:0;"></div>'
        '<span style="font-size:0.9em;color:#e2c97e;font-weight:700;letter-spacing:0.04em;">'
        'GAME LOG BY OPENING</span></div>',
        unsafe_allow_html=True,
    )

    openings: dict[str, list] = {}
    for s in summaries:
        op = (_get_op(s) or "Unknown")[:45]
        openings.setdefault(op, []).append(s)

    for op_name, games in sorted(openings.items(), key=lambda x: -len(x[1])):
        with st.expander(f"{op_name}  ({len(games)} games)", expanded=False):
            for s in sorted(games, key=lambda x: x.get("date", ""), reverse=True):
                opponent = s["black"] if s["player_color"] == "white" else s["white"]
                result   = s.get("result", "*")
                color    = s["player_color"]
                if   (result == "1-0" and color == "white") or (result == "0-1" and color == "black"):
                    res_text, res_col = "Win",  "#81c784"
                elif (result == "0-1" and color == "white") or (result == "1-0" and color == "black"):
                    res_text, res_col = "Loss", "#e57373"
                else:
                    res_text, res_col = "Draw", "#aaa"

                sym = "⬜" if color == "white" else "⬛"
                st.markdown(
                    f'<div style="display:flex;align-items:center;justify-content:space-between;'
                    f'padding:7px 10px;border-bottom:1px solid #1a2535;font-size:0.84em;">'
                    f'<span style="color:#cce0f4;">{sym} vs {opponent}</span>'
                    f'<span style="color:{res_col};font-weight:600;">{res_text}</span>'
                    f'<span style="color:#7a9ab0;">{s.get("date","")[:7]}</span>'
                    f'<span style="color:#90a4b8;">🔴{s["blunders"]}B &nbsp;🟠{s["mistakes"]}M</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Opening Drill ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px;">'
        '<div style="width:3px;height:15px;background:#5a7ac8;border-radius:2px;flex-shrink:0;"></div>'
        '<span style="font-size:0.9em;color:#7ab0e0;font-weight:700;letter-spacing:0.04em;">'
        'OPENING DRILL</span></div>'
        '<p style="color:#7a9ab0;font-size:0.82em;margin-bottom:12px;">'
        'Practice your most-played opening moves. Positions that appeared in 2+ games '
        'are drilled here.</p>',
        unsafe_allow_html=True,
    )

    # Build drills lazily
    if "opening_drills" not in st.session_state:
        with st.spinner("Building opening drills…"):
            st.session_state.opening_drills = _build_opening_drills(summaries)

    drills = st.session_state.opening_drills
    if not drills:
        st.markdown(
            '<div style="background:#111827;border:1px solid #1e2e3e;border-radius:10px;'
            'padding:18px;text-align:center;font-size:0.88em;color:#7a9ab0;">'
            'Not enough repeated openings to build drills. Play more games with '
            'the same openings and rebuild your profile.</div>',
            unsafe_allow_html=True,
        )
    else:
        drill_names = sorted(drills.keys())
        # Check if a specific opening was requested via Practice button
        requested = st.session_state.pop("drill_opening", None)
        default_idx = 0
        if requested and requested in drill_names:
            default_idx = drill_names.index(requested)

        selected_drill = st.selectbox(
            "Choose an opening to drill",
            drill_names,
            index=default_idx,
            key="drill_select",
        )

        positions = drills[selected_drill]
        drill_idx = st.session_state.get("drill_idx", 0)
        if drill_idx >= len(positions):
            drill_idx = 0
        pos = positions[drill_idx]

        st.markdown(
            f'<div style="font-size:0.85em;color:#a0bccc;margin-bottom:8px;">'
            f'Position <b style="color:#cce0f4;">{drill_idx + 1}</b> / {len(positions)}'
            f' &nbsp;·&nbsp; Move {pos["move_number"]}'
            f' &nbsp;·&nbsp; Find your usual move for '
            f'<b style="color:{"#cce0f4" if pos["player_color"] == "white" else "#90a4b8"}">'
            f'{"White" if pos["player_color"] == "white" else "Black"}</b></div>',
            unsafe_allow_html=True,
        )

        st.components.v1.html(
            _interactive_board_html(
                fen=pos["fen"],
                best_move_san=pos["best_move_san"],
                eval_before=0.0,
                eval_after=0.0,
                player_color=pos["player_color"],
                puzzle_idx=-1,  # drilldown mode — no rating tracking
            ),
            height=_board_iframe_height(),
            scrolling=False,
        )

        d_prev, d_next = st.columns(2)
        with d_prev:
            if st.button("◀ Prev Position", disabled=drill_idx == 0,
                         key="drill_prev", use_container_width=True):
                st.session_state.drill_idx = drill_idx - 1
                st.rerun()
        with d_next:
            if st.button("Next Position ▶", disabled=drill_idx >= len(positions) - 1,
                         key="drill_next", use_container_width=True):
                st.session_state.drill_idx = drill_idx + 1
                st.rerun()


# ── Main layout ──────────────────────────────────────────────────────────────

# ── Header bar: branding left, settings + user right ─────────────────────────
_hdr_left, _hdr_right = st.columns([3, 1])

with _hdr_left:
    st.markdown(
        '<div style="padding:14px 0 2px;">'
        '<span style="font-size:1.25em;font-weight:900;color:#e2c97e;letter-spacing:0.05em;'
        'text-shadow:0 0 24px rgba(226,201,126,0.2);">♔&ensp;BOARDSENSE</span>'
        '&ensp;<span style="font-size:0.78em;font-weight:600;color:#5a7a90;letter-spacing:0.08em;'
        'position:relative;top:-1px;">CHESS COACHING</span>'
        '</div>',
        unsafe_allow_html=True,
    )

with _hdr_right:
    _hdr_has_profile = bool(st.session_state.get("profile_summaries"))
    _hdr_user = st.session_state.get("profile_username_built", "")

    _hdr_settings_col, _hdr_user_col = st.columns(2)

    with _hdr_settings_col:
        with st.popover("⚙", use_container_width=True):
            # ── Board ──
            st.markdown(
                '<div style="font-size:0.68em;color:#4a6080;font-weight:700;'
                'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:4px;">BOARD</div>',
                unsafe_allow_html=True,
            )
            st.session_state.board_theme = st.selectbox(
                "Theme",
                list(_BOARD_THEMES.keys()),
                index=list(_BOARD_THEMES.keys()).index(st.session_state.board_theme),
                key="_bs_theme",
            )
            st.session_state.piece_set = st.selectbox(
                "Piece Set",
                list(_PIECE_SETS.keys()),
                index=list(_PIECE_SETS.keys()).index(st.session_state.piece_set),
                key="_bs_pieces",
            )
            st.session_state.board_square_size = st.selectbox(
                "Board Size",
                list(_BOARD_SIZES.keys()),
                index=list(_BOARD_SIZES.keys()).index(st.session_state.board_square_size),
                key="_bs_size",
            )
            st.session_state.sound_enabled = st.toggle(
                "Move Sounds", value=st.session_state.sound_enabled, key="_bs_sound",
            )
            st.session_state.animation_enabled = st.toggle(
                "Piece Animation", value=st.session_state.animation_enabled, key="_bs_anim",
            )
            st.session_state.show_legal_moves = st.toggle(
                "Show Legal Moves", value=st.session_state.show_legal_moves, key="_bs_legal",
            )
            st.session_state.show_coordinates = st.toggle(
                "Board Coordinates", value=st.session_state.show_coordinates, key="_bs_coords",
            )
            # ── Accessibility ──
            st.markdown(
                '<div style="font-size:0.68em;color:#4a6080;font-weight:700;'
                'letter-spacing:0.1em;text-transform:uppercase;margin:12px 0 4px;">ACCESSIBILITY</div>',
                unsafe_allow_html=True,
            )
            st.session_state.high_contrast = st.toggle(
                "High Contrast", value=st.session_state.high_contrast, key="_bs_hc",
                help="Boost text brightness and border visibility",
            )
            st.session_state.reduce_motion = st.toggle(
                "Reduce Motion", value=st.session_state.reduce_motion, key="_bs_rm",
                help="Disable animations and transitions across the app",
            )

    with _hdr_user_col:
        if _hdr_has_profile:
            with st.popover(f"{_hdr_user}", use_container_width=True):
                _hdr_plat = st.session_state.get("profile_platform", "Chess.com")
                _hdr_profile = st.session_state.get("profile_data", {})
                _hdr_sums = st.session_state.get("profile_summaries", [])
                _hdr_n = _hdr_profile.get("n_games", len(_hdr_sums))
                st.markdown(
                    f'<div style="font-size:0.82em;color:#a0bccc;line-height:1.8;margin-bottom:8px;">'
                    f'<strong style="color:#cce0f4;">{_hdr_user}</strong><br>'
                    f'{_hdr_plat} · {_hdr_n} games analysed</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<div style="height:1px;background:#1e2e3e;margin:4px 0 8px;"></div>',
                    unsafe_allow_html=True,
                )

                # ── External profile link ─────────────────────────────────
                if _hdr_plat == "Lichess":
                    _ext_url = f"https://lichess.org/@/{_hdr_user}"
                else:
                    _ext_url = f"https://www.chess.com/member/{_hdr_user}"
                st.markdown(
                    f'<a href="{_ext_url}" target="_blank" style="font-size:0.82em;'
                    f'color:#5a9ac8;text-decoration:none;">View {_hdr_plat} profile ↗</a>',
                    unsafe_allow_html=True,
                )

                # ── Export profile report ─────────────────────────────────
                def _build_profile_report() -> str:
                    lines = [
                        f"BOARDSENSE — Player Report",
                        f"{'=' * 40}",
                        f"Player:   {_hdr_user}",
                        f"Platform: {_hdr_plat}",
                        f"Games:    {_hdr_n}",
                        "",
                    ]
                    rec = _hdr_profile.get("record", {})
                    w, l, d = rec.get("wins", 0), rec.get("losses", 0), rec.get("draws", 0)
                    total = w + l + d
                    wr = round(100 * w / total, 1) if total else 0
                    lines += [
                        f"Record:   {w}W / {l}L / {d}D  ({wr}% win rate)",
                        "",
                    ]
                    skills = compute_skill_scores(_hdr_sums)
                    lines.append("Skill Scores (0–100)")
                    lines.append("-" * 30)
                    for cat in _SKILL_CATS:
                        val = skills.get(cat, 50)
                        bar = "#" * (val // 5) + "·" * (20 - val // 5)
                        lines.append(f"  {cat:<15} {val:>3}  [{bar}]")
                    lines.append("")
                    best = max(skills, key=skills.get) if skills else "—"
                    worst = min(skills, key=skills.get) if skills else "—"
                    lines += [
                        f"Strongest: {best} ({skills.get(best, 0)})",
                        f"Weakest:   {worst} ({skills.get(worst, 0)})",
                        "",
                    ]
                    pf = _hdr_profile.get("priority_focus", [])
                    if pf:
                        lines.append("Priority Focus Areas")
                        lines.append("-" * 30)
                        for c in pf:
                            lines.append(f"  • {c}")
                        lines.append("")
                    mpg = _hdr_profile.get("mistakes_per_game", 0)
                    bpg = _hdr_profile.get("blunders_per_game", 0)
                    lines += [
                        "Error Rates",
                        "-" * 30,
                        f"  Mistakes / game:  {mpg}",
                        f"  Blunders / game:  {bpg}",
                        "",
                        f"Generated by BoardSense Chess Coaching",
                    ]
                    return "\n".join(lines)

                st.download_button(
                    "Download Report",
                    data=_build_profile_report(),
                    file_name=f"boardsense_{_hdr_user}_report.txt",
                    mime="text/plain",
                    key="hdr_export",
                    use_container_width=True,
                )

                st.markdown(
                    '<div style="height:1px;background:#1e2e3e;margin:6px 0 8px;"></div>',
                    unsafe_allow_html=True,
                )

                # ── Reset training progress ───────────────────────────────
                if "confirm_reset" not in st.session_state:
                    st.session_state.confirm_reset = False

                if not st.session_state.confirm_reset:
                    if st.button("Reset Training Progress", key="hdr_reset", use_container_width=True):
                        st.session_state.confirm_reset = True
                        st.rerun()
                else:
                    st.markdown(
                        '<div style="font-size:0.78em;color:#e57373;margin-bottom:6px;">'
                        'This clears all lessons, puzzle stats, course scores, and curriculum progress. '
                        'Your game analysis and profile are kept.</div>',
                        unsafe_allow_html=True,
                    )
                    _rc1, _rc2 = st.columns(2)
                    with _rc1:
                        if st.button("Confirm", key="hdr_reset_yes", type="primary", use_container_width=True):
                            db.reset_training_progress()
                            # Clear session-state training data
                            for _k in ["puzzle_queue", "puzzle_idx", "puzzle_streak",
                                        "puzzle_best_streak", "puzzle_recent",
                                        "puzzles_solved_today", "puzzle_correct_today",
                                        "puzzle_phase_results", "active_course",
                                        "_puzzle_concept_list", "puzzle_concept_filter"]:
                                st.session_state.pop(_k, None)
                            # Clear cached lesson keys
                            for _k in list(st.session_state.keys()):
                                if _k.startswith("concept_lesson_"):
                                    del st.session_state[_k]
                            st.session_state.confirm_reset = False
                            st.rerun()
                    with _rc2:
                        if st.button("Cancel", key="hdr_reset_no", use_container_width=True):
                            st.session_state.confirm_reset = False
                            st.rerun()

                # ── Log out ───────────────────────────────────────────────
                if st.button("Log out", key="hdr_logout", use_container_width=True):
                    db.clear_active_user()
                    # Keys to preserve across logout (UI prefs only)
                    _keep = {
                        "board_theme", "piece_set", "board_square_size",
                        "sound_enabled", "animation_enabled", "show_legal_moves",
                        "show_coordinates", "high_contrast", "reduce_motion",
                    }
                    _preserved = {k: st.session_state[k] for k in _keep if k in st.session_state}
                    st.session_state.clear()
                    st.session_state.update(_preserved)
                    st.rerun()

# ── Background build: check progress & render banner ─────────────────────────
# Only activate the polling fragment when a build is actually in progress;
# run_every fires on the server even if the function returns early.
if st.session_state.get("_build_username"):
    _build_poll()
_active_build_job = _check_build_progress()
if _active_build_job:
    _render_build_banner(_active_build_job)
    # Native fallback in case HTML/CSS banner doesn't render on Cloud
    _abj_done = _active_build_job.get("done", 0)
    _abj_total = max(_active_build_job.get("total", 1), 1)
    _abj_pct = _abj_done / _abj_total
    _abj_eta = _active_build_job.get("eta_secs", 0)
    _abj_eta_str = f" (~{int(_abj_eta)}s left)" if _abj_eta > 0 else ""
    if _active_build_job.get("status") == "synthesizing":
        st.info("♔ ⚔ ♚  Claude is synthesizing your profile… almost done!")
    else:
        st.progress(_abj_pct, text=f"♔ ⚔ ♚  Analysing game {_abj_done}/{_abj_total}{_abj_eta_str}")

tab_dashboard, tab_profile, tab_learn, tab_puzzles, tab_review = st.tabs(
    ["Dashboard", "Profile", "Learn", "Puzzles", "Game Review"]
)

# JS tab navigation helpers
def _js_tab_click(tab_label: str) -> str:
    """Return a JS snippet that clicks the tab whose text matches tab_label."""
    return f"""
    <script>
    (function() {{
        function clickTab() {{
            var btns = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
            for (var i = 0; i < btns.length; i++) {{
                var t = (btns[i].textContent || '').trim().toLowerCase();
                if (t === '{tab_label.lower()}') {{ btns[i].click(); return true; }}
            }}
            return false;
        }}
        if (!clickTab()) {{
            setTimeout(function() {{
                if (!clickTab()) setTimeout(clickTab, 200);
            }}, 60);
        }}
    }})();
    </script>
    """

if st.session_state.pop("navigate_to_coaching", False):
    st.session_state.learn_section = "Coaching"
    components.html(_js_tab_click("learn"), height=0)

if st.session_state.pop("navigate_to_review", False):
    components.html(_js_tab_click("game review"), height=0)

if st.session_state.pop("navigate_to_puzzles", False):
    components.html(_js_tab_click("puzzles"), height=0)

if st.session_state.pop("navigate_to_profile", False):
    st.session_state.profile_section = "Player Profile"
    components.html(_js_tab_click("profile"), height=0)

if st.session_state.pop("navigate_to_training", False):
    st.session_state.learn_section = "Training"
    components.html(_js_tab_click("learn"), height=0)

if st.session_state.pop("navigate_to_coach", False):
    st.session_state.learn_section = "Ask Coach"
    components.html(_js_tab_click("learn"), height=0)

if st.session_state.pop("navigate_to_openings", False):
    st.session_state.profile_section = "Opening Explorer"
    components.html(_js_tab_click("profile"), height=0)

if st.session_state.pop("navigate_to_dashboard", False):
    components.html(_js_tab_click("dashboard"), height=0)

with tab_dashboard:
    render_dashboard_tab()

with tab_profile:
    # Platform + username controls (shared across sub-sections)
    _prof_ctrl_plat, _prof_ctrl_user = st.columns([1, 2])
    with _prof_ctrl_plat:
        _prof_platform = st.radio(
            "Platform", ["Chess.com", "Lichess"],
            horizontal=True, key="profile_platform",
        )
    with _prof_ctrl_user:
        _prof_plat_label = "Chess.com username" if _prof_platform == "Chess.com" else "Lichess username"
        _prof_username = st.text_input(_prof_plat_label, value="",
                                       key="profile_username",
                                       placeholder="Enter username")

    _profile_section = st.radio(
        "Section",
        ["Player Profile", "Opening Explorer"],
        horizontal=True,
        key="profile_section",
        label_visibility="collapsed",
    )
    if _profile_section == "Player Profile":
        render_profile_tab()
    else:
        render_openings_tab()

with tab_learn:
    _selected_concept = st.session_state.get("selected_concept")
    _in_concept_detail = (
        _selected_concept
        and st.session_state.get("learn_section", "Coaching") == "Coaching"
        and not st.session_state.get("active_course")
    )

    if _in_concept_detail:
        # ── Concept detail header: title left, nav + back right ──────────
        _hdr_left, _hdr_right = st.columns([3, 2])
        with _hdr_left:
            _cd_data = next(
                (c for c in _coaching_concept_list()
                 if c["name"].lower() == _selected_concept.lower()), None
            )
            _cd_cat = _cd_data["category"] if _cd_data else "From Your Games"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;margin:8px 0 4px;">'
                f'<span style="font-size:1.45em;font-weight:700;color:#cce0f4;">'
                f'{_selected_concept}</span>'
                f'{_category_badge(_cd_cat)}'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _hdr_right:
            _bk, _rCoach, _rTrain, _rAsk, _rNot = st.columns([1, 1, 1, 1, 1])
            with _bk:
                if st.button("← Back", key="coaching_back_hdr",
                             use_container_width=True):
                    st.session_state.pop("selected_concept", None)
                    st.rerun()
            with _rCoach:
                if st.button("Coaching", key="learn_nav_coaching",
                             type="primary", use_container_width=True):
                    pass  # already on Coaching
            with _rTrain:
                if st.button("Training", key="learn_nav_training",
                             use_container_width=True):
                    st.session_state.learn_section = "Training"
                    st.session_state.pop("selected_concept", None)
                    st.rerun()
            with _rAsk:
                if st.button("Coach", key="learn_nav_ask",
                             use_container_width=True):
                    st.session_state.learn_section = "Ask Coach"
                    st.session_state.pop("selected_concept", None)
                    st.rerun()
            with _rNot:
                if st.button("Notation", key="learn_nav_notation",
                             use_container_width=True):
                    st.session_state.learn_section = "Notation"
                    st.session_state.pop("selected_concept", None)
                    st.rerun()

        render_coaching_tab(_detail_header_shown=True)
    else:
        # ── Standard sub-nav radio ───────────────────────────────────────
        _learn_section = st.radio(
            "Section",
            ["Coaching", "Training", "Ask Coach", "Notation"],
            horizontal=True,
            key="learn_section",
            label_visibility="collapsed",
        )
        if _learn_section == "Coaching":
            render_coaching_tab()
        elif _learn_section == "Training":
            render_training_tab()
        elif _learn_section == "Notation":
            render_notation_tab()
        else:
            render_coach_tab()

with tab_puzzles:
    render_puzzles_tab()

with tab_review:
    render_game_review_tab()
