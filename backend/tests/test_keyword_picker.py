"""The Run-page keyword picker: explicit keyword_ids override the LRU
rotation entirely and never consume it (no usage stamp), while normal
rotation keeps stamping usage. Unknown/inactive ids fail loudly instead
of silently narrowing an explicitly scoped run.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import keyword_service


def _kw(i, *, pinned=False, active=True, last_used=None, times_used=0):
    return {
        "id": i,
        "text": f"kw{i}",
        "tier": "skill",
        "pinned": pinned,
        "active": active,
        "last_used_at": last_used,
        "times_used": times_used,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


# ---------- selection (pure) ----------


def test_pick_explicit_returns_exactly_requested_in_pool_order():
    rows = [_kw(i) for i in range(1, 6)]
    picked = keyword_service.pick_explicit(rows, [4, 2])
    assert [r["id"] for r in picked] == [2, 4]  # pool order, not pick order


def test_pick_explicit_pins_do_not_force_their_way_in():
    rows = [_kw(1, pinned=True), _kw(2), _kw(3)]
    picked = keyword_service.pick_explicit(rows, [3])
    assert [r["id"] for r in picked] == [3]


def test_pick_explicit_unknown_id_raises():
    rows = [_kw(1), _kw(2)]
    with pytest.raises(ValueError, match="Unknown or inactive"):
        keyword_service.pick_explicit(rows, [1, 99])


def test_pick_explicit_inactive_id_raises():
    rows = [_kw(1), _kw(2, active=False)]
    with pytest.raises(ValueError, match="Unknown or inactive"):
        keyword_service.pick_explicit(rows, [2])


# ---------- integration: no rotation burn ----------


@pytest.fixture()
def seeded_pool(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    from app.db import connect, get_db_path, insert_keyword, utcnow
    from app.services import resume_service

    resume_service._ensure_db()
    conn = connect(get_db_path())
    try:
        for t in ("alpha", "beta", "gamma"):
            insert_keyword(conn, text=t, tier="skill", created_at=utcnow())
    finally:
        conn.close()
    return tmp_path


@pytest.mark.asyncio
async def test_picker_run_does_not_stamp_usage(seeded_pool):
    """The core contract: a picked run leaves last_used_at untouched, so
    the everyday rotation is exactly as it was before the test run."""
    selected = await keyword_service.pick_for_run("runPK", keyword_ids=[1, 3])
    assert [k["text"] for k in selected] == ["alpha", "gamma"]

    from app.db import connect, get_db_path, list_keywords

    async with keyword_service._db_lock:
        conn = connect(get_db_path())
        try:
            rows = [dict(r) for r in list_keywords(conn)]
        finally:
            conn.close()
    assert all(r["last_used_at"] is None for r in rows)
    assert all(r["times_used"] == 0 for r in rows)


@pytest.mark.asyncio
async def test_normal_run_still_stamps_usage(seeded_pool):
    selected = await keyword_service.pick_for_run("runNORM")
    assert len(selected) == 3

    from app.db import connect, get_db_path, list_keywords

    async with keyword_service._db_lock:
        conn = connect(get_db_path())
        try:
            rows = [dict(r) for r in list_keywords(conn)]
        finally:
            conn.close()
    assert all(r["last_used_at"] is not None for r in rows)


# ---------- API ----------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as c:
        yield c


def test_trigger_accepts_keyword_ids(client, monkeypatch):
    captured = {}

    async def fake_start_run(recency, keyword_limit=None, keyword_ids=None):
        captured["keyword_ids"] = keyword_ids
        return "runPK"

    monkeypatch.setattr("app.routers.runs.run_service.start_run", fake_start_run)
    res = client.post("/api/runs", json={"recency": "24h", "keyword_ids": [2, 1, 2]})
    assert res.status_code == 200
    assert captured["keyword_ids"] == [2, 1]  # deduped, order kept


def test_trigger_rejects_non_integer_ids(client):
    res = client.post("/api/runs", json={"recency": "24h", "keyword_ids": ["a"]})
    assert res.status_code == 400
    res = client.post("/api/runs", json={"recency": "24h", "keyword_ids": [True]})
    assert res.status_code == 400


def test_trigger_empty_ids_falls_back_to_rotation(client, monkeypatch):
    captured = {}

    async def fake_start_run(recency, keyword_limit=None, keyword_ids=None):
        captured["keyword_ids"] = keyword_ids
        return "runE"

    monkeypatch.setattr("app.routers.runs.run_service.start_run", fake_start_run)
    res = client.post("/api/runs", json={"recency": "24h", "keyword_ids": []})
    assert res.status_code == 200
    assert captured["keyword_ids"] is None
