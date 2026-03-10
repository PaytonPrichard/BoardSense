"""
BoardSense — db.py
SQLite persistence layer for games, concepts, and lessons.

All per-user tables are scoped by username to support multi-user deployments.
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "chesstutor.db"

_MAX_RETRIES = 3
_RETRY_DELAY = 0.5  # seconds

# Schema version — bump when tables change to trigger migration
_SCHEMA_VERSION = 2


def _connect() -> sqlite3.Connection:
    """Connect to SQLite with retry logic for locked/busy databases."""
    for attempt in range(_MAX_RETRIES):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")  # better concurrent access
            return conn
        except sqlite3.OperationalError:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                raise


def init_db():
    """Create tables if they don't exist. Resilient to corruption — recreates DB if needed."""
    try:
        _init_db_inner()
    except sqlite3.DatabaseError:
        # DB file is corrupted — rename it and start fresh
        backup = DB_PATH.with_suffix(".db.bak")
        try:
            DB_PATH.rename(backup)
        except OSError:
            DB_PATH.unlink(missing_ok=True)
        _init_db_inner()


def _init_db_inner():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                white       TEXT,
                black       TEXT,
                result      TEXT,
                date        TEXT,
                opening     TEXT,
                pgn         TEXT,
                w_accuracy  REAL,
                b_accuracy  REAL,
                n_moves     INTEGER,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS move_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id        INTEGER REFERENCES games(id) ON DELETE CASCADE,
                move_number    INTEGER,
                color          TEXT,
                move_san       TEXT,
                classification TEXT,
                eval_before    REAL,
                eval_after     REAL
            );

            CREATE TABLE IF NOT EXISTS concepts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id        INTEGER REFERENCES games(id) ON DELETE CASCADE,
                concept        TEXT,
                move_number    INTEGER,
                color          TEXT,
                move_san       TEXT,
                classification TEXT
            );

            CREATE TABLE IF NOT EXISTS profiles (
                username       TEXT PRIMARY KEY,
                profile_json   TEXT,
                summaries_json TEXT,
                n_games        INTEGER,
                built_at       TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS profile_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT,
                overall_acc REAL,
                skill_json  TEXT,
                n_games     INTEGER,
                record_json TEXT DEFAULT '{}',
                built_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS curriculum_progress (
                username     TEXT,
                module_id    TEXT,
                completed    INTEGER DEFAULT 0,
                score        INTEGER DEFAULT 0,
                total        INTEGER DEFAULT 0,
                best_score   INTEGER DEFAULT 0,
                attempts     INTEGER DEFAULT 0,
                completed_at TEXT,
                PRIMARY KEY (username, module_id)
            );

            CREATE TABLE IF NOT EXISTS generation_limits (
                client_id  TEXT,
                date       TEXT,
                count      INTEGER DEFAULT 0,
                PRIMARY KEY (client_id, date)
            );

            CREATE TABLE IF NOT EXISTS schema_version (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER DEFAULT 1
            );
        """)

        # ── Check schema version and migrate if needed ─────────────────────
        row = conn.execute(
            "SELECT version FROM schema_version WHERE id=1"
        ).fetchone()
        current_version = row["version"] if row else 0

        if current_version < _SCHEMA_VERSION:
            _migrate_to_v2(conn)
            conn.execute(
                "INSERT INTO schema_version (id, version) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET version=excluded.version",
                (_SCHEMA_VERSION,),
            )
        # Legacy migration: add record_json column to profile_history
        try:
            conn.execute(
                "ALTER TABLE profile_history ADD COLUMN record_json TEXT DEFAULT '{}'"
            )
        except Exception:
            pass


def _migrate_to_v2(conn: sqlite3.Connection):
    """Migrate from v1 (singleton tables) to v2 (username-scoped tables).

    Drops and recreates affected tables. On Streamlit Cloud the DB is
    ephemeral anyway; locally users just rebuild their profile.
    """
    # Drop old singleton / unscoped tables and recreate with username
    _old_tables = [
        "puzzle_stats", "puzzle_phase_stats", "streaks", "course_scores",
        "achievements", "daily_goals", "session_stats", "concept_mastery",
        "lessons", "active_session",
    ]
    for t in _old_tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")

    conn.executescript("""
        CREATE TABLE lessons (
            username   TEXT,
            concept    TEXT,
            content    TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (username, concept)
        );

        CREATE TABLE puzzle_stats (
            username    TEXT PRIMARY KEY,
            rating      REAL    DEFAULT 1200.0,
            solved      INTEGER DEFAULT 0,
            attempted   INTEGER DEFAULT 0,
            streak      INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0,
            recent_json TEXT    DEFAULT '[]',
            updated_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE puzzle_phase_stats (
            username   TEXT,
            phase      TEXT,
            correct    INTEGER DEFAULT 0,
            attempted  INTEGER DEFAULT 0,
            PRIMARY KEY (username, phase)
        );

        CREATE TABLE streaks (
            username  TEXT PRIMARY KEY,
            current   INTEGER DEFAULT 0,
            longest   INTEGER DEFAULT 0,
            last_date TEXT
        );

        CREATE TABLE course_scores (
            username     TEXT,
            concept      TEXT,
            score        INTEGER,
            total        INTEGER,
            completed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (username, concept)
        );

        CREATE TABLE achievements (
            username    TEXT,
            key         TEXT,
            unlocked_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (username, key)
        );

        CREATE TABLE daily_goals (
            username      TEXT,
            date          TEXT,
            targets_json  TEXT,
            progress_json TEXT,
            PRIMARY KEY (username, date)
        );

        CREATE TABLE session_stats (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT,
            session_date  TEXT,
            duration_secs INTEGER DEFAULT 0,
            puzzles       INTEGER DEFAULT 0,
            lessons       INTEGER DEFAULT 0,
            reviews       INTEGER DEFAULT 0,
            started_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE concept_mastery (
            username   TEXT,
            concept    TEXT,
            correct    INTEGER DEFAULT 0,
            attempted  INTEGER DEFAULT 0,
            last_at    TEXT,
            PRIMARY KEY (username, concept)
        );
    """)


# ── Game persistence ────────────────────────────────────────────────────────

def save_game(
    pgn_text: str,
    headers: dict,
    moves: list[dict],
    white_accuracy: float,
    black_accuracy: float,
) -> int | None:
    """
    Persist a completed game analysis.
    Returns the new row id, or None if the same game already exists
    (matched on white / black / date / result).
    """
    white   = headers.get("White", "?")
    black   = headers.get("Black", "?")
    result  = headers.get("Result", "*")
    date    = headers.get("Date", "")
    opening = headers.get("Opening", headers.get("ECOUrl", ""))

    with _connect() as conn:
        dup = conn.execute(
            "SELECT id FROM games WHERE white=? AND black=? AND date=? AND result=?",
            (white, black, date, result),
        ).fetchone()
        if dup:
            return None

        cur = conn.execute(
            "INSERT INTO games (white, black, result, date, opening, pgn, "
            "w_accuracy, b_accuracy, n_moves) VALUES (?,?,?,?,?,?,?,?,?)",
            (white, black, result, date, opening, pgn_text,
             white_accuracy, black_accuracy, len(moves)),
        )
        game_id = cur.lastrowid

        conn.executemany(
            "INSERT INTO move_events (game_id, move_number, color, move_san, "
            "classification, eval_before, eval_after) VALUES (?,?,?,?,?,?,?)",
            [
                (game_id, m["move_number"], m["color"], m["move_san"],
                 m["classification"], m["eval_before"], m["eval_after"])
                for m in moves
            ],
        )
    return game_id


def save_concept(
    game_id: int,
    concept: str,
    move_number: int,
    color: str,
    move_san: str,
    classification: str,
):
    """Record a concept appearance for a specific game move (deduped)."""
    with _connect() as conn:
        dup = conn.execute(
            "SELECT id FROM concepts "
            "WHERE game_id=? AND concept=? AND move_number=? AND color=?",
            (game_id, concept, move_number, color),
        ).fetchone()
        if not dup:
            conn.execute(
                "INSERT INTO concepts "
                "(game_id, concept, move_number, color, move_san, classification) "
                "VALUES (?,?,?,?,?,?)",
                (game_id, concept, move_number, color, move_san, classification),
            )


# ── Lessons (username-scoped) ───────────────────────────────────────────────

def save_lesson(username: str, concept: str, content: str):
    """Upsert a Claude-generated lesson."""
    key = username.strip().lower()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO lessons (username, concept, content, updated_at) "
            "VALUES (?,?,?,datetime('now')) "
            "ON CONFLICT(username, concept) DO UPDATE SET "
            "content=excluded.content, updated_at=excluded.updated_at",
            (key, concept, content),
        )


def get_lesson(username: str, concept: str) -> str | None:
    """Retrieve saved lesson text, or None if not yet generated."""
    key = username.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM lessons WHERE username=? AND concept=? COLLATE NOCASE",
            (key, concept),
        ).fetchone()
    return row["content"] if row else None


def get_all_lessons(username: str) -> dict[str, str]:
    """Return {concept: content} for every saved lesson for this user."""
    key = username.strip().lower()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT concept, content FROM lessons WHERE username=?", (key,)
        ).fetchall()
    return {r["concept"]: r["content"] for r in rows}


def get_concept_stats() -> dict[str, dict]:
    """
    Per-concept cross-game stats.

    Returns:
        {
          concept: {
            "count":      total move appearances,
            "game_count": distinct games,
            "examples":   list of {move_number, color, move_san, classification}  (up to 6)
          }
        }
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT concept, game_id, move_number, color, move_san, classification "
            "FROM concepts ORDER BY concept, rowid"
        ).fetchall()

    stats: dict[str, dict] = {}
    game_sets: dict[str, set] = {}

    for row in rows:
        c = row["concept"]
        if c not in stats:
            stats[c]     = {"count": 0, "game_count": 0, "examples": []}
            game_sets[c] = set()
        stats[c]["count"] += 1
        game_sets[c].add(row["game_id"])
        if len(stats[c]["examples"]) < 6:
            stats[c]["examples"].append({
                "move_number":    row["move_number"],
                "color":          row["color"],
                "move_san":       row["move_san"],
                "classification": row["classification"],
            })

    for c in stats:
        stats[c]["game_count"] = len(game_sets[c])

    return stats


def get_recent_games(limit: int = 10) -> list[dict]:
    """Return metadata for the most recently analysed games."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, white, black, result, date, opening, "
            "w_accuracy, b_accuracy, n_moves, created_at "
            "FROM games ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Profile persistence ─────────────────────────────────────────────────────

def save_profile(username: str, profile: dict, summaries: list) -> None:
    """
    Upsert the latest profile for a username.
    Overwrites any previous build for that username.
    """
    import json
    key = username.strip().lower()
    with _connect() as conn:
        # Remove any case-variant duplicates before upserting
        conn.execute(
            "DELETE FROM profiles WHERE username != ? AND username = ? COLLATE NOCASE",
            (key, key),
        )
        conn.execute(
            "INSERT INTO profiles (username, profile_json, summaries_json, n_games, built_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(username) DO UPDATE SET "
            "profile_json   = excluded.profile_json, "
            "summaries_json = excluded.summaries_json, "
            "n_games        = excluded.n_games, "
            "built_at       = excluded.built_at",
            (key, json.dumps(profile), json.dumps(summaries), len(summaries)),
        )


def save_profile_history(username: str, profile: dict, n_games: int) -> None:
    """Append one snapshot to the history table (never overwrites)."""
    import json
    key = username.strip().lower()
    record = profile.get("record", {})
    with _connect() as conn:
        conn.execute(
            "INSERT INTO profile_history (username, overall_acc, skill_json, n_games, record_json, built_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (key, profile.get("overall_acc", 0.0),
             json.dumps(profile.get("skill_ratings", {})), n_games,
             json.dumps(record)),
        )


def get_profile_history(username: str) -> list[dict]:
    """Return all history snapshots for a username, oldest first."""
    import json
    with _connect() as conn:
        rows = conn.execute(
            "SELECT overall_acc, skill_json, n_games, record_json, built_at "
            "FROM profile_history WHERE username=? COLLATE NOCASE ORDER BY built_at",
            (username,),
        ).fetchall()
    results = []
    for r in rows:
        try:
            skills = json.loads(r["skill_json"])
        except (json.JSONDecodeError, TypeError):
            skills = {}
        try:
            record = json.loads(r["record_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            record = {}
        results.append({
            "overall_acc":   r["overall_acc"],
            "skill_ratings": skills,
            "n_games":       r["n_games"],
            "record":        record,
            "built_at":      r["built_at"],
        })
    return results


def load_profile(username: str) -> tuple | None:
    """
    Load the saved profile for a username.
    Returns (profile_dict, summaries_list, built_at_str) or None if not found.
    """
    import json
    with _connect() as conn:
        row = conn.execute(
            "SELECT profile_json, summaries_json, built_at "
            "FROM profiles WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    if not row:
        return None
    try:
        profile = json.loads(row["profile_json"])
        summaries = json.loads(row["summaries_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return profile, summaries, row["built_at"]


# ── Puzzle stats (username-scoped) ──────────────────────────────────────────

def get_puzzle_stats(username: str) -> dict:
    """Return the player's puzzle performance counters including last-10 results."""
    import json
    key = username.strip().lower()
    _defaults = {"rating": 1200.0, "solved": 0, "attempted": 0, "streak": 0,
                 "best_streak": 0, "recent": []}
    if not key:
        return _defaults
    with _connect() as conn:
        row = conn.execute(
            "SELECT rating, solved, attempted, streak, best_streak, recent_json "
            "FROM puzzle_stats WHERE username=?", (key,)
        ).fetchone()
    if not row:
        return _defaults
    d = dict(row)
    try:
        d["recent"] = json.loads(d.pop("recent_json") or "[]")
    except Exception:
        d["recent"] = []
    d.pop("username", None)
    return d


def update_puzzle_result(username: str, correct: bool, new_streak: int, recent: list) -> None:
    """Record the result of one puzzle attempt (last 10 stored as JSON)."""
    import json
    key = username.strip().lower()
    if not key:
        return
    recent_json = json.dumps(recent[-10:])
    with _connect() as conn:
        conn.execute(
            """INSERT INTO puzzle_stats (username, rating, solved, attempted, streak, best_streak, recent_json, updated_at)
               VALUES (?, 1200.0, ?, 1, ?, ?, ?, datetime('now'))
               ON CONFLICT(username) DO UPDATE SET
                   solved      = puzzle_stats.solved + ?,
                   attempted   = puzzle_stats.attempted + 1,
                   streak      = ?,
                   best_streak = MAX(puzzle_stats.best_streak, ?),
                   recent_json = ?,
                   updated_at  = datetime('now')""",
            (key, 1 if correct else 0, new_streak, new_streak, recent_json,
             1 if correct else 0, new_streak, new_streak, recent_json),
        )


# ── Puzzle phase tracking (username-scoped) ─────────────────────────────────

def update_puzzle_phase(username: str, phase: str, correct: bool) -> None:
    """Increment phase-level puzzle stats."""
    key = username.strip().lower()
    if not key:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO puzzle_phase_stats (username, phase, correct, attempted) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(username, phase) DO UPDATE SET "
            "correct = puzzle_phase_stats.correct + excluded.correct, "
            "attempted = puzzle_phase_stats.attempted + 1",
            (key, phase, 1 if correct else 0),
        )


def get_puzzle_phase_stats(username: str) -> dict[str, dict]:
    """Return {phase: {correct, attempted}} for all phases."""
    key = username.strip().lower()
    if not key:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT phase, correct, attempted FROM puzzle_phase_stats WHERE username=?",
            (key,),
        ).fetchall()
    return {r["phase"]: {"correct": r["correct"], "attempted": r["attempted"]} for r in rows}


# ── Curriculum progress ──────────────────────────────────────────────────────

def save_module_progress(username: str, module_id: str, score: int, total: int) -> None:
    """Upsert module completion. Updates best_score if new score is higher. Increments attempts."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO curriculum_progress
                   (username, module_id, completed, score, total, best_score, attempts, completed_at)
               VALUES (?, ?, 1, ?, ?, ?, 1, datetime('now'))
               ON CONFLICT(username, module_id) DO UPDATE SET
                   completed    = 1,
                   score        = excluded.score,
                   total        = excluded.total,
                   best_score   = MAX(curriculum_progress.best_score, excluded.score),
                   attempts     = curriculum_progress.attempts + 1,
                   completed_at = excluded.completed_at""",
            (username, module_id, score, total, score),
        )


def get_curriculum_progress(username: str) -> dict[str, dict]:
    """Return {module_id: {completed, score, total, best_score, attempts}} for all modules attempted."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT module_id, completed, score, total, best_score, attempts "
            "FROM curriculum_progress WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchall()
    return {
        r["module_id"]: {
            "completed":  r["completed"],
            "score":      r["score"],
            "total":      r["total"],
            "best_score": r["best_score"],
            "attempts":   r["attempts"],
        }
        for r in rows
    }


# ── Generation rate limiting ─────────────────────────────────────────────────

def get_daily_generation_count(client_id: str) -> int:
    """Return how many lessons this client has generated today."""
    from datetime import date
    today = date.today().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT count FROM generation_limits WHERE client_id=? AND date=?",
            (client_id, today),
        ).fetchone()
    return row["count"] if row else 0


def increment_generation_count(client_id: str, n: int = 1) -> int:
    """Increment today's generation count for this client. Returns new total."""
    from datetime import date
    today = date.today().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO generation_limits (client_id, date, count) VALUES (?, ?, ?) "
            "ON CONFLICT(client_id, date) DO UPDATE SET count = generation_limits.count + ?",
            (client_id, today, n, n),
        )
        row = conn.execute(
            "SELECT count FROM generation_limits WHERE client_id=? AND date=?",
            (client_id, today),
        ).fetchone()
    return row["count"] if row else n


# ── Course score persistence (username-scoped) ──────────────────────────────

def save_course_score(username: str, concept: str, score: int, total: int) -> None:
    """Upsert the latest course score for a concept."""
    key = username.strip().lower()
    if not key:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO course_scores (username, concept, score, total, completed_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(username, concept) DO UPDATE SET "
            "score=excluded.score, total=excluded.total, completed_at=excluded.completed_at",
            (key, concept, score, total),
        )


def get_course_score(username: str, concept: str) -> dict | None:
    """Return {score, total, completed_at} for a concept, or None."""
    key = username.strip().lower()
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT score, total, completed_at FROM course_scores "
            "WHERE username=? AND concept=? COLLATE NOCASE",
            (key, concept),
        ).fetchone()
    if row:
        return {"score": row["score"], "total": row["total"], "completed_at": row["completed_at"]}
    return None


def get_review_due_concepts(username: str, days: int = 3, threshold: float = 0.8) -> list[dict]:
    """Return concepts studied 3+ days ago with score below threshold."""
    key = username.strip().lower()
    if not key:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT concept, score, total, completed_at FROM course_scores "
            "WHERE username=? AND completed_at <= datetime('now', ? || ' days') AND "
            "CAST(score AS REAL) / CAST(total AS REAL) < ?",
            (key, f"-{days}", threshold),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Reset training progress (username-scoped) ───────────────────────────────

def reset_training_progress(username: str) -> None:
    """Clear training progress for a specific user only."""
    key = username.strip().lower()
    if not key:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM lessons WHERE username=?", (key,))
        conn.execute("DELETE FROM course_scores WHERE username=?", (key,))
        conn.execute("DELETE FROM curriculum_progress WHERE username=? COLLATE NOCASE", (key,))
        conn.execute("DELETE FROM puzzle_phase_stats WHERE username=?", (key,))
        conn.execute("DELETE FROM puzzle_stats WHERE username=?", (key,))
        conn.execute("DELETE FROM concept_mastery WHERE username=?", (key,))


def get_stage_completion(username: str, stage: int) -> tuple[int, int]:
    """Return (completed_count, total_modules) for a given stage."""
    from curriculum import CURRICULUM
    stage_data = CURRICULUM.get(stage)
    if not stage_data:
        return (0, 0)
    total_modules = len(stage_data["modules"])
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM curriculum_progress "
            "WHERE username = ? COLLATE NOCASE AND module_id LIKE ? AND completed = 1",
            (username, f"{stage}.%"),
        ).fetchone()
    return (row["cnt"] if row else 0, total_modules)


# ── Achievements (username-scoped) ──────────────────────────────────────────

def unlock_achievement(username: str, key: str) -> bool:
    """Insert achievement if not exists. Return True if newly unlocked."""
    ukey = username.strip().lower()
    if not ukey:
        return False
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO achievements (username, key, unlocked_at) "
            "VALUES (?, ?, datetime('now'))",
            (ukey, key),
        )
    return cur.rowcount > 0


def get_achievements(username: str) -> dict[str, str]:
    """Return {key: unlocked_at} for all unlocked achievements."""
    ukey = username.strip().lower()
    if not ukey:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, unlocked_at FROM achievements WHERE username=?", (ukey,)
        ).fetchall()
    return {r["key"]: r["unlocked_at"] for r in rows}


# ── Daily Goals (username-scoped) ───────────────────────────────────────────

def get_daily_goals(username: str, date: str) -> dict | None:
    """Return {targets, progress} for a given date, or None."""
    import json
    key = username.strip().lower()
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT targets_json, progress_json FROM daily_goals WHERE username=? AND date=?",
            (key, date),
        ).fetchone()
    if not row:
        return None
    return {
        "targets": json.loads(row["targets_json"] or "{}"),
        "progress": json.loads(row["progress_json"] or "{}"),
    }


def save_daily_goals(username: str, date: str, targets: dict, progress: dict):
    """Upsert daily goals for a date."""
    import json
    key = username.strip().lower()
    if not key:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO daily_goals (username, date, targets_json, progress_json) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(username, date) DO UPDATE SET "
            "targets_json=excluded.targets_json, progress_json=excluded.progress_json",
            (key, date, json.dumps(targets), json.dumps(progress)),
        )


# ── Session Stats (username-scoped) ─────────────────────────────────────────

def save_session_stats(username: str, duration_secs: int, puzzles: int, lessons: int, reviews: int):
    """Record a session's activity stats."""
    from datetime import date
    key = username.strip().lower()
    if not key:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO session_stats (username, session_date, duration_secs, puzzles, lessons, reviews) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, date.today().isoformat(), duration_secs, puzzles, lessons, reviews),
        )


def get_session_stats(username: str, days: int = 30) -> list[dict]:
    """Return session stats for the last N days, newest first."""
    key = username.strip().lower()
    if not key:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT session_date, SUM(duration_secs) as total_secs, "
            "SUM(puzzles) as puzzles, SUM(lessons) as lessons, SUM(reviews) as reviews "
            "FROM session_stats "
            "WHERE username=? AND session_date >= date('now', ? || ' days') "
            "GROUP BY session_date ORDER BY session_date DESC",
            (key, f"-{days}"),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Login Streaks (username-scoped) ─────────────────────────────────────────

def update_login_streak(username: str) -> dict:
    """Update the daily login streak. Returns {current, longest, is_new_day}."""
    from datetime import date, timedelta
    key = username.strip().lower()
    _defaults = {"current": 0, "longest": 0, "is_new_day": False}
    if not key:
        return _defaults
    today = date.today().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT current, longest, last_date FROM streaks WHERE username=?", (key,)
        ).fetchone()
        current = row["current"] if row else 0
        longest = row["longest"] if row else 0
        last_date = row["last_date"] if row else None

        is_new_day = last_date != today
        if is_new_day:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            if last_date == yesterday:
                current += 1
            elif last_date != today:
                current = 1
            longest = max(longest, current)
            conn.execute(
                "INSERT INTO streaks (username, current, longest, last_date) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET "
                "current=excluded.current, longest=excluded.longest, last_date=excluded.last_date",
                (key, current, longest, today),
            )
    return {"current": current, "longest": longest, "is_new_day": is_new_day}


def get_login_streak(username: str) -> dict:
    """Return {current, longest, last_date}."""
    key = username.strip().lower()
    if not key:
        return {"current": 0, "longest": 0, "last_date": None}
    with _connect() as conn:
        row = conn.execute(
            "SELECT current, longest, last_date FROM streaks WHERE username=?", (key,)
        ).fetchone()
    if not row:
        return {"current": 0, "longest": 0, "last_date": None}
    return {"current": row["current"], "longest": row["longest"], "last_date": row["last_date"]}


# ── Concept mastery (username-scoped) ───────────────────────────────────────

def update_concept_mastery(username: str, concept: str, correct: bool) -> None:
    """Increment concept-level puzzle stats."""
    key = username.strip().lower()
    if not key:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO concept_mastery (username, concept, correct, attempted, last_at) "
            "VALUES (?, ?, ?, 1, datetime('now')) "
            "ON CONFLICT(username, concept) DO UPDATE SET "
            "correct = concept_mastery.correct + excluded.correct, "
            "attempted = concept_mastery.attempted + 1, "
            "last_at = excluded.last_at",
            (key, concept, 1 if correct else 0),
        )


def get_all_concept_mastery(username: str) -> dict[str, dict]:
    """Return {concept: {correct, attempted, pct}} for all tracked concepts."""
    key = username.strip().lower()
    if not key:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT concept, correct, attempted FROM concept_mastery WHERE username=?",
            (key,),
        ).fetchall()
    result = {}
    for r in rows:
        attempted = r["attempted"]
        result[r["concept"]] = {
            "correct": r["correct"],
            "attempted": attempted,
            "pct": round(100 * r["correct"] / attempted) if attempted else 0,
        }
    return result
