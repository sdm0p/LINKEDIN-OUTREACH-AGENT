import pytest
from fastapi.testclient import TestClient

from app.db import (
    connect,
    get_db_path,
    init_db,
    insert_keyword,
    list_keywords,
    mark_keywords_used,
    replace_unpinned_keywords,
)
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as c:
        yield c


def test_keywords_empty_list(client):
    res = client.get("/api/keywords")
    assert res.status_code == 200
    assert res.json() == []


def test_add_and_list_keyword(client):
    res = client.post(
        "/api/keywords",
        json={"text": "hiring LangChain", "tier": "skill"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["text"] == "hiring LangChain"
    assert body["tier"] == "skill"
    assert body["pinned"] is False

    res = client.get("/api/keywords")
    assert len(res.json()) == 1


def test_add_duplicate_returns_409(client):
    client.post("/api/keywords", json={"text": "dup", "tier": "skill"})
    res = client.post("/api/keywords", json={"text": "dup", "tier": "title"})
    assert res.status_code == 409


def test_add_rejects_bad_tier(client):
    res = client.post("/api/keywords", json={"text": "x", "tier": "nope"})
    assert res.status_code == 400


def test_add_rejects_empty_text(client):
    res = client.post("/api/keywords", json={"text": "   ", "tier": "skill"})
    assert res.status_code == 400


def test_patch_pin_and_delete(client):
    kw = client.post("/api/keywords", json={"text": "p", "tier": "skill"}).json()
    res = client.patch(f"/api/keywords/{kw['id']}", json={"pinned": True})
    assert res.status_code == 200
    rows = client.get("/api/keywords").json()
    assert rows[0]["pinned"] is True

    res = client.delete(f"/api/keywords/{kw['id']}")
    assert res.status_code == 200
    assert client.get("/api/keywords").json() == []


def test_generate_requires_resume(client):
    res = client.post("/api/keywords/generate")
    assert res.status_code == 400
    assert "resume" in res.json()["detail"].lower()


# ---------- db-level behavior ----------


def test_replace_unpinned_keeps_pinned(tmp_path, monkeypatch):
    # Isolate: these db-level tests must never touch the real data dir.
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    db = get_db_path()
    init_db(db)
    conn = connect(db)
    try:
        insert_keyword(conn, text="keep-me", tier="skill", created_at="t")
        insert_keyword(conn, text="drop-me", tier="title", created_at="t")
        row = conn.execute("SELECT id FROM keywords WHERE text='keep-me'").fetchone()
        conn.execute("UPDATE keywords SET pinned = 1 WHERE id = ?", (row["id"],))
        conn.commit()

        replace_unpinned_keywords(
            conn,
            [{"text": "new-a", "tier": "skill"}, {"text": "new-b", "tier": "title"}],
        )
        texts = {r["text"] for r in list_keywords(conn)}
        assert texts == {"keep-me", "new-a", "new-b"}
    finally:
        conn.close()


def test_mark_keywords_used_updates_tracking(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    db = get_db_path()
    init_db(db)
    conn = connect(db)
    try:
        insert_keyword(conn, text="k", tier="skill", created_at="t")
        row = conn.execute("SELECT id FROM keywords WHERE text='k'").fetchone()
        mark_keywords_used(conn, [row["id"]])
        mark_keywords_used(conn, [row["id"]])
        kw = conn.execute("SELECT * FROM keywords WHERE id = ?", (row["id"],)).fetchone()
        assert kw["times_used"] == 2
        assert kw["last_used_at"] is not None
    finally:
        conn.close()
