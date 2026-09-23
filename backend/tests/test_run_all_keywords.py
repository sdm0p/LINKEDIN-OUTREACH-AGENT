"""The "search all keywords" option: a run may take the entire active pool
in one go instead of the default 5-keyword LRU rotation."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.keyword_service import KEYWORDS_PER_RUN, select_for_run


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


# ---------- selection ----------


def test_limit_none_selects_entire_pool():
    rows = [_kw(i) for i in range(15)]
    picked = select_for_run(rows, limit=None)
    assert len(picked) == 15


def test_limit_none_excludes_inactive():
    rows = [_kw(1), _kw(2, active=False), _kw(3, active=False)]
    picked = select_for_run(rows, limit=None)
    assert [r["id"] for r in picked] == [1]


def test_limit_none_keeps_lru_order():
    rows = [
        _kw(1, last_used="2026-01-02T00:00:00+00:00", times_used=2),
        _kw(2, last_used=None, times_used=0),
        _kw(3, last_used="2026-01-01T00:00:00+00:00", times_used=5),
    ]
    picked = select_for_run(rows, limit=None)
    assert [r["id"] for r in picked] == [2, 3, 1]


def test_default_limit_unchanged():
    rows = [_kw(i) for i in range(15)]
    assert len(select_for_run(rows)) == KEYWORDS_PER_RUN == 5


# ---------- API ----------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as c:
        yield c


def test_trigger_accepts_keyword_limit_all(client, monkeypatch):
    captured = {}

    async def fake_start_run(recency, keyword_limit=None, keyword_ids=None):
        captured["recency"] = recency
        captured["keyword_limit"] = keyword_limit
        return "run123"

    monkeypatch.setattr("app.routers.runs.run_service.start_run", fake_start_run)
    res = client.post("/api/runs", json={"recency": "24h", "keyword_limit": "all"})
    assert res.status_code == 200
    assert res.json() == {"run_id": "run123"}
    assert captured["keyword_limit"] is None


def test_trigger_default_keeps_rotation(client, monkeypatch):
    captured = {}

    async def fake_start_run(recency, keyword_limit=None, keyword_ids=None):
        captured["keyword_limit"] = keyword_limit

    monkeypatch.setattr("app.routers.runs.run_service.start_run", fake_start_run)
    monkeypatch.setattr(
        "app.routers.runs.keyword_service.KEYWORDS_PER_RUN", 7
    )
    res = client.post("/api/runs", json={"recency": "24h"})
    assert res.status_code == 200
    assert captured["keyword_limit"] == 7


def test_trigger_rejects_bad_keyword_limit(client, monkeypatch):
    ran = {}

    async def fake_start_run(recency, keyword_limit=None, keyword_ids=None):
        ran["called"] = True

    monkeypatch.setattr("app.routers.runs.run_service.start_run", fake_start_run)
    res = client.post("/api/runs", json={"recency": "24h", "keyword_limit": "lots"})
    assert res.status_code == 400
    res = client.post("/api/runs", json={"recency": "24h", "keyword_limit": 0})
    assert res.status_code == 400
    assert not ran.get("called")


# ---------- end-to-end through the graph node ----------


def test_pick_keywords_node_honors_limit_none(monkeypatch, tmp_path):
    from app.graphs import nodes

    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)

    seen_limits = []

    async def fake_pick(run_id, limit=KEYWORDS_PER_RUN, keyword_ids=None):
        seen_limits.append(limit)
        return [{"id": 1, "text": "kw1", "tier": "skill"}]

    monkeypatch.setattr(nodes.keyword_service, "pick_for_run", fake_pick)

    import asyncio

    from app.graphs.trace_writer import register_trace, release_trace
    from app.run_trace import RunTrace

    trace = RunTrace()
    register_trace("no-such-run", trace)
    try:
        state = {"run_id": "no-such-run", "keyword_limit": None}
        result = asyncio.run(nodes.pick_keywords(state))
    finally:
        release_trace("no-such-run")

    assert seen_limits == [None]
    assert result["keyword_index"] == 0
    assert result["totals"]["leads"] == 0
