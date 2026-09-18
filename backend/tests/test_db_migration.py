"""Migration tests: an existing drafts table (pre-feature schema) must be
upgraded in place without losing rows."""

import sqlite3

from app.db import connect, init_db, list_drafts, list_queue_countries, save_job_details


def _make_old_db(tmp_path):
    """Create a DB with the pre-feature drafts schema and one row."""
    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE drafts (
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
            contact_method TEXT NOT NULL,
            contact_value TEXT,
            yoe_required INTEGER,
            classification TEXT,
            post_text TEXT NOT NULL DEFAULT '',
            draft_text TEXT,
            drafted_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO drafts (post_id, run_id, keyword, contact_method, status, created_at, updated_at)
        VALUES ('p1', 'r1', 'kw', 'dm', 'pending', '2026-09-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_migration_adds_feature_columns_and_keeps_rows(tmp_path):
    db_path = _make_old_db(tmp_path)
    init_db(db_path)

    conn = connect(db_path)
    try:
        rows = list_drafts(conn)
        assert len(rows) == 1
        assert rows[0]["post_id"] == "p1"
        # New columns exist and are empty on migrated rows.
        assert rows[0]["country"] is None
        assert rows[0]["posted_at"] is None
        assert rows[0]["job_id"] is None
        # The new columns are writable.
        save_job_details(
            conn,
            rows[0]["id"],
            {"job_id": "42", "job_url": "https://www.linkedin.com/jobs/view/42/", "text": "A role"},
        )
        rows = list_drafts(conn)
        assert rows[0]["job_id"] == "42"
        assert "A role" in rows[0]["job_details_json"]
        assert list_queue_countries(conn) == []
    finally:
        conn.close()


def test_fresh_db_has_indexes(tmp_path):
    db_path = tmp_path / "app.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        names = {
            r[1] for r in conn.execute("PRAGMA index_list(drafts)").fetchall()
        }
        assert "idx_drafts_country" in names
        assert "idx_drafts_posted_at" in names
    finally:
        conn.close()
