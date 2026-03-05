"""
BoardSense — db.py
SQLite persistence layer for games, concepts, and lessons.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "chesstutor.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
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

            CREATE TABLE IF NOT EXISTS lessons (
                concept    TEXT PRIMARY KEY,
                content    TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS puzzle_stats (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                rating      REAL    DEFAULT 1200.0,
                solved      INTEGER DEFAULT 0,
                attempted   INTEGER DEFAULT 0,
                streak      INTEGER DEFAULT 0,
                best_streak INTEGER DEFAULT 0,
                recent_json TEXT    DEFAULT '[]',
                updated_at  TEXT    DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO puzzle_stats (id) VALUES (1);

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

            CREATE TABLE IF NOT EXISTS puzzle_phase_stats (
                phase      TEXT PRIMARY KEY,
                correct    INTEGER DEFAULT 0,
                attempted  INTEGER DEFAULT 0
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

            CREATE TABLE IF NOT EXISTS active_session (
                id       INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT,
                platform TEXT DEFAULT 'Chess.com'
            );

            CREATE TABLE IF NOT EXISTS course_scores (
                concept      TEXT PRIMARY KEY,
                score        INTEGER,
                total        INTEGER,
                completed_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS achievements (
                key         TEXT PRIMARY KEY,
                unlocked_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_goals (
                date          TEXT PRIMARY KEY,
                targets_json  TEXT,
                progress_json TEXT
            );
        """)
        # Migration: add recent_json column to existing DBs (safe no-op if already present)
        try:
            conn.execute(
                "ALTER TABLE puzzle_stats ADD COLUMN recent_json TEXT DEFAULT '[]'"
            )
        except Exception:
            pass
        # Migration: add record_json column to profile_history
        try:
            conn.execute(
                "ALTER TABLE profile_history ADD COLUMN record_json TEXT DEFAULT '{}'"
            )
        except Exception:
            pass


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


def save_lesson(concept: str, content: str):
    """Upsert a Claude-generated lesson."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO lessons (concept, content, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(concept) DO UPDATE SET "
            "content=excluded.content, updated_at=excluded.updated_at",
            (concept, content),
        )


def get_lesson(concept: str) -> str | None:
    """Retrieve saved lesson text, or None if not yet generated."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM lessons WHERE concept=? COLLATE NOCASE", (concept,)
        ).fetchone()
    return row["content"] if row else None


def get_all_lessons() -> dict[str, str]:
    """Return {concept: content} for every saved lesson."""
    with _connect() as conn:
        rows = conn.execute("SELECT concept, content FROM lessons").fetchall()
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
    return [
        {
            "overall_acc":   r["overall_acc"],
            "skill_ratings": json.loads(r["skill_json"]),
            "n_games":       r["n_games"],
            "record":        json.loads(r["record_json"] or "{}"),
            "built_at":      r["built_at"],
        }
        for r in rows
    ]


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
    return json.loads(row["profile_json"]), json.loads(row["summaries_json"]), row["built_at"]


def get_puzzle_stats() -> dict:
    """Return the player's puzzle performance counters including last-10 results."""
    import json
    with _connect() as conn:
        row = conn.execute(
            "SELECT rating, solved, attempted, streak, best_streak, recent_json "
            "FROM puzzle_stats WHERE id=1"
        ).fetchone()
    if not row:
        return {"rating": 1200.0, "solved": 0, "attempted": 0, "streak": 0,
                "best_streak": 0, "recent": []}
    d = dict(row)
    try:
        d["recent"] = json.loads(d.pop("recent_json") or "[]")
    except Exception:
        d["recent"] = []
    return d


def update_puzzle_result(correct: bool, new_streak: int, recent: list) -> None:
    """Record the result of one puzzle attempt (last 10 stored as JSON)."""
    import json
    recent_json = json.dumps(recent[-10:])
    with _connect() as conn:
        conn.execute(
            """UPDATE puzzle_stats
               SET solved      = solved + ?,
                   attempted   = attempted + 1,
                   streak      = ?,
                   best_streak = MAX(best_streak, ?),
                   recent_json = ?,
                   updated_at  = datetime('now')
               WHERE id = 1""",
            (1 if correct else 0, new_streak, new_streak, recent_json),
        )


# ── Puzzle phase tracking ────────────────────────────────────────────────────

def update_puzzle_phase(phase: str, correct: bool) -> None:
    """Increment phase-level puzzle stats."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO puzzle_phase_stats (phase, correct, attempted) VALUES (?, ?, 1) "
            "ON CONFLICT(phase) DO UPDATE SET "
            "correct = puzzle_phase_stats.correct + excluded.correct, "
            "attempted = puzzle_phase_stats.attempted + 1",
            (phase, 1 if correct else 0),
        )


def get_puzzle_phase_stats() -> dict[str, dict]:
    """Return {phase: {correct, attempted}} for all phases."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT phase, correct, attempted FROM puzzle_phase_stats"
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


# ── Course score persistence ─────────────────────────────────────────────────

def save_course_score(concept: str, score: int, total: int) -> None:
    """Upsert the latest course score for a concept."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO course_scores (concept, score, total, completed_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(concept) DO UPDATE SET "
            "score=excluded.score, total=excluded.total, completed_at=excluded.completed_at",
            (concept, score, total),
        )


def get_course_score(concept: str) -> dict | None:
    """Return {score, total, completed_at} for a concept, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT score, total, completed_at FROM course_scores WHERE concept=? COLLATE NOCASE",
            (concept,),
        ).fetchone()
    if row:
        return {"score": row["score"], "total": row["total"], "completed_at": row["completed_at"]}
    return None


def get_review_due_concepts(days: int = 3, threshold: float = 0.8) -> list[dict]:
    """Return concepts studied 3+ days ago with score below threshold."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT concept, score, total, completed_at FROM course_scores "
            "WHERE completed_at <= datetime('now', ? || ' days') AND "
            "CAST(score AS REAL) / CAST(total AS REAL) < ?",
            (f"-{days}", threshold),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Active session persistence ───────────────────────────────────────────────

def save_active_user(username: str, platform: str = "Chess.com") -> None:
    """Remember the logged-in user across page refreshes."""
    key = username.strip().lower()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO active_session (id, username, platform) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, platform=excluded.platform",
            (key, platform),
        )


def get_active_user() -> tuple[str, str] | None:
    """Return (username, platform) if a user was previously logged in, else None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT username, platform FROM active_session WHERE id=1"
        ).fetchone()
    if row and row["username"]:
        return row["username"], row["platform"]
    return None


def clear_active_user() -> None:
    """Log out — remove the saved active user."""
    with _connect() as conn:
        conn.execute("DELETE FROM active_session WHERE id=1")


def reset_training_progress() -> None:
    """Clear all training progress: lessons, puzzle stats, course scores, curriculum, phase stats."""
    with _connect() as conn:
        conn.execute("DELETE FROM lessons")
        conn.execute("DELETE FROM course_scores")
        conn.execute("DELETE FROM curriculum_progress")
        conn.execute("DELETE FROM puzzle_phase_stats")
        conn.execute("UPDATE puzzle_stats SET solved=0, attempted=0, streak=0, best_streak=0, recent_json='[]' WHERE id=1")


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


# ── Achievements ─────────────────────────────────────────────────────────────

def unlock_achievement(key: str) -> bool:
    """Insert achievement if not exists. Return True if newly unlocked."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO achievements (key, unlocked_at) VALUES (?, datetime('now'))",
            (key,),
        )
    return cur.rowcount > 0


def get_achievements() -> dict[str, str]:
    """Return {key: unlocked_at} for all unlocked achievements."""
    with _connect() as conn:
        rows = conn.execute("SELECT key, unlocked_at FROM achievements").fetchall()
    return {r["key"]: r["unlocked_at"] for r in rows}


# ── Daily Goals ──────────────────────────────────────────────────────────────

def get_daily_goals(date: str) -> dict | None:
    """Return {targets, progress} for a given date, or None."""
    import json
    with _connect() as conn:
        row = conn.execute(
            "SELECT targets_json, progress_json FROM daily_goals WHERE date=?",
            (date,),
        ).fetchone()
    if not row:
        return None
    return {
        "targets": json.loads(row["targets_json"] or "{}"),
        "progress": json.loads(row["progress_json"] or "{}"),
    }


def save_daily_goals(date: str, targets: dict, progress: dict):
    """Upsert daily goals for a date."""
    import json
    with _connect() as conn:
        conn.execute(
            "INSERT INTO daily_goals (date, targets_json, progress_json) "
            "VALUES (?, ?, ?) ON CONFLICT(date) DO UPDATE SET "
            "targets_json=excluded.targets_json, progress_json=excluded.progress_json",
            (date, json.dumps(targets), json.dumps(progress)),
        )
