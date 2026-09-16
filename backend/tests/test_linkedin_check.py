"""The LinkedIn session canary endpoint: POST /api/settings/linkedin/check.

The health check spawns the MCP container for one read-only tool call, so
tests fake the source — no Docker, no network."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


class _FakeSource:
    def __init__(self, result=None, error=None):
        self.result = result or {"status": "valid", "detail": "Session is live."}
        self.error = error
        self.closed = False
        self.check_calls = 0

    async def check_health(self) -> dict:
        self.check_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as c:
        yield c


def _install(monkeypatch, fake: _FakeSource) -> None:
    monkeypatch.setattr(
        "app.routers.settings.get_linkedin_source", lambda: fake
    )


def test_check_returns_valid_payload(client, monkeypatch):
    fake = _FakeSource({"status": "valid", "detail": "Session is live."})
    _install(monkeypatch, fake)
    res = client.post("/api/settings/linkedin/check")
    assert res.status_code == 200
    assert res.json() == {"status": "valid", "detail": "Session is live."}
    assert fake.check_calls == 1
    assert fake.closed  # container must be shut down after the canary


def test_check_maps_auth_failure(client, monkeypatch):
    # check_health classifies internally; expired comes through verbatim.
    fake = _FakeSource({"status": "expired", "detail": "login required"})
    _install(monkeypatch, fake)
    res = client.post("/api/settings/linkedin/check")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "expired"
    assert fake.closed


def test_check_swallows_crash_and_still_closes(client, monkeypatch):
    fake = _FakeSource(error=RuntimeError("spawn boom"))
    _install(monkeypatch, fake)
    res = client.post("/api/settings/linkedin/check")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "unavailable"
    assert "spawn boom" in body["detail"]
    assert fake.closed
