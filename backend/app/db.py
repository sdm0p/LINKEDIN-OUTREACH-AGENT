"""SQLite access. Kept deliberately thin: the plan leaves the future store
(local SQLite vs Supabase Postgres) as an open decision, so all SQL lives
here and nowhere else."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resume_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    file_hash TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    parsed_json TEXT,
    my_yoe INTEGER,
    roles_expanded_json TEXT,
    parsed_at TEXT,
    expanded_at TEXT
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL CHECK (tier IN ('skill', 'title')),
    pinned INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT,
    times_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    recency TEXT NOT NULL DEFAULT '24h',
    trace_json TEXT,
    summary_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS processed_posts (
    post_id TEXT PRIMARY KEY,
    company_family TEXT,
    post_url TEXT,
    first_seen_at TEXT NOT NULL,
    run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    company TEXT,
    role TEXT,
    author_name TEXT,
    author_headline TEXT,
    author_profile_url TEXT,
    post_url TEXT,
    contact_method TEXT NOT NULL CHECK (contact_method IN ('email', 'dm')),
    contact_value TEXT,
    yoe_required INTEGER,
    classification TEXT,
    draft_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed', 'sent', 'skipped')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_db_path() -> Path:
    from .config import settings

    return settings.data_dir / "app.db"


# ---------- resume cache ----------


def get_resume_cache(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM resume_cache WHERE id = 1").fetchone()


def save_resume_cache(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO resume_cache
            (id, file_hash, file_name, file_size, parsed_json, my_yoe,
             roles_expanded_json, parsed_at, expanded_at)
        VALUES (1, :file_hash, :file_name, :file_size, :parsed_json, :my_yoe,
                :roles_expanded_json, :parsed_at, :expanded_at)
        """,
        data,
    )
    conn.commit()


# ---------- keyword pool ----------


def list_keywords(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM keywords ORDER BY pinned DESC, tier ASC, text ASC"
    ).fetchall()


def insert_keyword(
    conn: sqlite3.Connection, *, text: str, tier: str, created_at: str
) -> bool:
    """Returns False when the keyword already exists."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO keywords (text, tier, created_at)
        VALUES (?, ?, ?)
        """,
        (text, tier, created_at),
    )
    conn.commit()
    return cur.rowcount > 0


def update_keyword(conn: sqlite3.Connection, kw_id: int, fields: dict) -> None:
    allowed = {"text", "pinned", "active", "tier"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE keywords SET {sets} WHERE id = ?", (*updates.values(), kw_id))
    conn.commit()


def delete_keyword(conn: sqlite3.Connection, kw_id: int) -> None:
    conn.execute("DELETE FROM keywords WHERE id = ?", (kw_id,))
    conn.commit()


def mark_keywords_used(conn: sqlite3.Connection, kw_ids: list[int]) -> None:
    now = utcnow()
    for kw_id in kw_ids:
        conn.execute(
            """
            UPDATE keywords
            SET last_used_at = ?, times_used = times_used + 1
            WHERE id = ?
            """,
            (now, kw_id),
        )
    conn.commit()


def replace_unpinned_keywords(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Delete unpinned keywords, then insert the new pool. Pinned keywords
    survive a regeneration (pins are the user's explicit overrides)."""
    conn.execute("DELETE FROM keywords WHERE pinned = 0")
    now = utcnow()
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO keywords (text, tier, created_at)
            VALUES (:text, :tier, :created_at)
            """,
            {**row, "created_at": now},
        )
    conn.commit()


# ---------- runs ----------


def save_run(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO runs
            (id, started_at, finished_at, status, recency, trace_json,
             summary_json, error)
        VALUES (:id, :started_at, :finished_at, :status, :recency,
                :trace_json, :summary_json, :error)
        """,
        data,
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


# ---------- dedup (processed posts) ----------


def seen_post_ids(conn: sqlite3.Connection, post_ids: list[str]) -> set[str]:
    if not post_ids:
        return set()
    placeholders = ",".join("?" for _ in post_ids)
    rows = conn.execute(
        f"SELECT post_id FROM processed_posts WHERE post_id IN ({placeholders})",
        post_ids,
    ).fetchall()
    return {r["post_id"] for r in rows}


def record_processed_posts(
    conn: sqlite3.Connection, rows: list[dict], run_id: str
) -> None:
    now = utcnow()
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO processed_posts
                (post_id, company_family, post_url, first_seen_at, run_id)
            VALUES (:post_id, :company_family, :post_url, :first_seen_at, :run_id)
            """,
            {**row, "first_seen_at": now, "run_id": run_id},
        )
    conn.commit()


def count_processed_posts(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM processed_posts").fetchone()
    return int(row["n"])


def oldest_processed_post_age_days(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MIN(first_seen_at) AS oldest FROM processed_posts"
    ).fetchone()
    if not row or not row["oldest"]:
        return 0
    oldest = datetime.fromisoformat(row["oldest"])
    return max(0, (datetime.now(UTC) - oldest).days)


def purge_processed_posts(conn: sqlite3.Connection) -> int:
    """Manual purge only — nothing in the app calls this automatically."""
    cur = conn.execute("DELETE FROM processed_posts")
    conn.commit()
    return cur.rowcount


# ---------- drafts (review queue) ----------


def insert_draft(conn: sqlite3.Connection, data: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO drafts
            (post_id, run_id, keyword, company, role, author_name,
             author_headline, author_profile_url, post_url, contact_method,
             contact_value, yoe_required, classification, draft_text,
             status, created_at, updated_at)
        VALUES (:post_id, :run_id, :keyword, :company, :role, :author_name,
                :author_headline, :author_profile_url, :post_url,
                :contact_method, :contact_value, :yoe_required,
                :classification, :draft_text, :status, :created_at, :updated_at)
        """,
        {**data, "status": "new"},
    )
    conn.commit()
    return int(cur.lastrowid)


def list_drafts(
    conn: sqlite3.Connection, status: str | None = None
) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM drafts WHERE status = ? "
            "ORDER BY id DESC",
            (status,),
        ).fetchall()
    return conn.execute("SELECT * FROM drafts ORDER BY id DESC").fetchall()


def get_draft(conn: sqlite3.Connection, draft_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def update_draft_status(
    conn: sqlite3.Connection, draft_id: int, status: str
) -> None:
    if status not in ("new", "reviewed", "sent", "skipped"):
        raise ValueError(f"Invalid status: {status}")
    conn.execute(
        "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
        (status, utcnow(), draft_id),
    )
    conn.commit()


def count_drafts_created_on(conn: sqlite3.Connection, day_utc: str) -> int:
    """day_utc is a date string like 2026-09-11 (UTC)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE substr(created_at, 1, 10) = ?",
        (day_utc,),
    ).fetchone()
    return int(row["n"])


def purge_drafts(conn: sqlite3.Connection, status: str | None = None) -> int:
    """Manual purge; optionally only one status bucket."""
    if status:
        cur = conn.execute("DELETE FROM drafts WHERE status = ?", (status,))
    else:
        cur = conn.execute("DELETE FROM drafts")
    conn.commit()
    return cur.rowcount
