"""SQLite access. Kept deliberately thin: the plan leaves the future store
(local SQLite vs Supabase Postgres) as an open decision, so all SQL lives
here and nowhere else.

The `drafts` table doubles as the lead queue: a run inserts qualified leads
with status 'pending' and no draft text; a draft is generated later, only
when explicitly requested, flipping the row to 'new' (then reviewed/sent).
"""

import json
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
    location TEXT,
    country TEXT,
    posted_at TEXT,
    job_id TEXT,
    job_url TEXT,
    job_details_json TEXT,
    post_text TEXT NOT NULL DEFAULT '',
    draft_text TEXT,
    drafted_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'new', 'reviewed', 'sent', 'skipped')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Created after the migration below: on a pre-feature database the base
# schema runs while the table still lacks these columns, and an index on a
# missing column fails.
_DRAFT_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_drafts_country ON drafts(country);
CREATE INDEX IF NOT EXISTS idx_drafts_posted_at ON drafts(posted_at);
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
        _migrate_drafts(conn)
        conn.executescript(_DRAFT_INDEXES)
        conn.commit()
    finally:
        conn.close()


_DRAFT_COLUMNS_ADDED = {
    "location": "TEXT",
    "country": "TEXT",
    "posted_at": "TEXT",
    "job_id": "TEXT",
    "job_url": "TEXT",
    "job_details_json": "TEXT",
}


def _migrate_drafts(conn: sqlite3.Connection) -> None:
    """In-place upgrade of the drafts table.

    Pre-queue tables are rebuilt (post_text/drafted_at added, draft_text
    widened to nullable, 'pending' status allowed). Existing tables only
    get the queue-feature columns added, one ALTER TABLE per missing
    column — cheap, and SQLite needs no table rebuild for that.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)")}
    if not cols:
        return
    if "post_text" not in cols:
        _rebuild_pre_queue_drafts(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)")}
    for name, decl in _DRAFT_COLUMNS_ADDED.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE drafts ADD COLUMN {name} {decl}")


def _rebuild_pre_queue_drafts(conn: sqlite3.Connection) -> None:
    """In-place upgrade of pre-queue drafts tables: add post_text/drafted_at,
    widen draft_text to nullable, and allow the 'pending' status. Preserves
    existing rows (they already carry draft_text, so they keep their
    status)."""
    conn.executescript("""
        CREATE TABLE drafts_migrated (
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
            location TEXT,
            country TEXT,
            posted_at TEXT,
            job_id TEXT,
            job_url TEXT,
            job_details_json TEXT,
            post_text TEXT NOT NULL DEFAULT '',
            draft_text TEXT,
            drafted_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'new', 'reviewed', 'sent', 'skipped')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO drafts_migrated
            (id, post_id, run_id, keyword, company, role, author_name,
             author_headline, author_profile_url, post_url, contact_method,
             contact_value, yoe_required, classification, post_text,
             draft_text, drafted_at, status, created_at, updated_at)
        SELECT id, post_id, run_id, keyword, company, role, author_name,
               author_headline, author_profile_url, post_url, contact_method,
               contact_value, yoe_required, classification, '',
               draft_text, NULL, status, created_at, updated_at
        FROM drafts;
        DROP TABLE drafts;
        ALTER TABLE drafts_migrated RENAME TO drafts;
    """)


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
    """Insert a queue row. With no draft_text the row is a pending lead
    awaiting an on-demand draft; with draft_text it is a finished draft
    (status must say so)."""
    cur = conn.execute(
        """
        INSERT INTO drafts
            (post_id, run_id, keyword, company, role, author_name,
             author_headline, author_profile_url, post_url, contact_method,
             contact_value, yoe_required, classification, location,
             country, posted_at, job_id, job_url, job_details_json,
             post_text, draft_text, drafted_at, status, created_at, updated_at)
        VALUES (:post_id, :run_id, :keyword, :company, :role, :author_name,
                :author_headline, :author_profile_url, :post_url,
                :contact_method, :contact_value, :yoe_required,
                :classification, :location, :country, :posted_at, :job_id,
                :job_url, :job_details_json, :post_text, :draft_text,
                :drafted_at, :status, :created_at, :updated_at)
        """,
        data,
    )
    conn.commit()
    return int(cur.lastrowid)


def save_generated_draft(
    conn: sqlite3.Connection, draft_id: int, draft_text: str
) -> None:
    """Fill in a pending row's draft on explicit request -> status 'new'."""
    now = utcnow()
    conn.execute(
        """
        UPDATE drafts
        SET draft_text = ?, status = 'new', drafted_at = ?, updated_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (draft_text, now, now, draft_id),
    )
    conn.commit()


def list_pending_drafts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM drafts WHERE status = 'pending' ORDER BY id ASC"
    ).fetchall()


def update_draft_location(
    conn: sqlite3.Connection, draft_id: int, country: str | None, hint: str | None
) -> None:
    """Set country (and location hint when empty) on one lead row."""
    conn.execute(
        """
        UPDATE drafts
        SET country = ?, location = COALESCE(location, ?), updated_at = ?
        WHERE id = ?
        """,
        (country, hint, utcnow(), draft_id),
    )
    conn.commit()


def save_job_details(
    conn: sqlite3.Connection, draft_id: int, details: dict
) -> None:
    """Persist enriched job details on a lead row (explicit fetch only).
    The id/url live in their own columns; everything else is stored whole
    as JSON."""
    now = utcnow()
    payload = {k: v for k, v in details.items() if k not in ("job_id", "job_url")}
    conn.execute(
        """
        UPDATE drafts
        SET job_id = ?, job_url = ?, job_details_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            details.get("job_id"),
            details.get("job_url"),
            json.dumps(payload, ensure_ascii=False),
            now,
            draft_id,
        ),
    )
    conn.commit()


def list_drafts(
    conn: sqlite3.Connection,
    status: str | None = None,
    country: str | None = None,
    order: str = "added",
) -> list[sqlite3.Row]:
    """Queue rows. `order` picks the sort: 'posted' = by the post's own
    timestamp when known, falling back to the ingest order (id) for rows
    without one, so never-posted-time rows don't float to the top.
    'added' = newest ingest first (the historical default)."""
    where = []
    params: list[str] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if country is not None:
        if country == "":
            where.append("(country IS NULL OR country = '')")
        else:
            where.append("country = ?")
            params.append(country)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = (
        "ORDER BY (posted_at IS NULL OR posted_at = '') ASC, posted_at DESC, id DESC"
        if order == "posted"
        else "ORDER BY id DESC"
    )
    return conn.execute(
        f"SELECT * FROM drafts {where_sql} {order_sql}", params
    ).fetchall()


def list_queue_countries(conn: sqlite3.Connection) -> list[str]:
    """Distinct non-empty countries present in the queue, alphabetical."""
    rows = conn.execute(
        """
        SELECT DISTINCT country FROM drafts
        WHERE country IS NOT NULL AND country != ''
        ORDER BY country ASC
        """
    ).fetchall()
    return [r["country"] for r in rows]


def get_draft(conn: sqlite3.Connection, draft_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def update_draft_status(
    conn: sqlite3.Connection, draft_id: int, status: str
) -> None:
    if status not in ("pending", "new", "reviewed", "sent", "skipped"):
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


def count_drafts_generated_on(conn: sqlite3.Connection, day_utc: str) -> int:
    """Drafts actually generated on a day (drafted_at set), for the cap."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE substr(drafted_at, 1, 10) = ?",
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
