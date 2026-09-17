"""Runtime Gemini key management: POST/DELETE /api/settings/llm/key.

The set endpoint live-verifies the key with a real one-token call before
persisting, so tests fake the provider layer."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Point data_dir (where the key mirror lives) at tmp BEFORE the app
    # starts so no test can ever touch the developer's real data dir.
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_runtime_key():
    yield
    config.settings.clear_api_key_runtime()


def _fake_verify(monkeypatch, ok: bool) -> None:
    from app.llm.base import LLMError

    class _Probe:
        def __init__(self, api_key, model):
            pass

        async def generate_json(self, *, system, user):
            # Mirror the real provider's contract: every failure is an LLMError.
            if not ok:
                raise LLMError("Gemini call failed: API key not valid")

    # The endpoint imports GeminiProvider lazily from this module, so
    # patching at the source is what the call-time import resolves to.
    monkeypatch.setattr("app.llm.gemini.GeminiProvider", _Probe)


def test_set_rejects_garbage_format(client):
    res = client.post("/api/settings/llm/key", json={"api_key": "hello-world"})
    assert res.status_code == 400
    assert "AIza" in res.json()["detail"]


def test_set_rejects_empty(client):
    res = client.post("/api/settings/llm/key", json={"api_key": "   "})
    assert res.status_code == 400


def test_set_rejects_key_that_fails_verification(client, monkeypatch):
    _fake_verify(monkeypatch, ok=False)
    res = client.post(
        "/api/settings/llm/key", json={"api_key": "AIza" + "a" * 30}
    )
    assert res.status_code == 400
    assert "rejected" in res.json()["detail"].lower()
    # Nothing persisted on failure.
    assert config.settings.effective_gemini_api_key() is None


def test_set_stores_key_and_persisted_mirror(client, monkeypatch, tmp_path):
    _fake_verify(monkeypatch, ok=True)
    key = "AIza" + "b" * 30
    res = client.post("/api/settings/llm/key", json={"api_key": key})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "verified": True}

    # Effective key now resolves (runtime wins).
    assert config.settings.effective_gemini_api_key() == key

    # Persisted mirror written into data_dir (tmp), never into backend/.env.
    key_file = tmp_path / "gemini_key"
    assert key_file.read_text(encoding="utf-8").strip() == key
    assert not (tmp_path / ".env").exists()

    # provider_info reflects the source; the key itself never leaks.
    info = client.get("/api/settings").json()["llm"]
    assert info["configured"] is True
    assert info["source"] == "runtime"
    assert key not in client.get("/api/settings").text


def test_key_survives_restart(client, monkeypatch, tmp_path):
    """The whole point of the mirror: a fresh process (no runtime key in
    memory) must still find the key via the data-dir file."""
    _fake_verify(monkeypatch, ok=True)
    client.post("/api/settings/llm/key", json={"api_key": "AIza" + "e" * 30})

    # Simulate restart: forget in-memory state only.
    monkeypatch.setattr(config, "_runtime_api_key", None)
    assert config.settings.effective_gemini_api_key() == "AIza" + "e" * 30
    assert config.settings.key_source() == "stored"


def test_clear_removes_runtime_and_mirror(client, monkeypatch, tmp_path):
    _fake_verify(monkeypatch, ok=True)
    client.post("/api/settings/llm/key", json={"api_key": "AIza" + "c" * 30})

    res = client.delete("/api/settings/llm/key")
    assert res.status_code == 200
    assert config.settings.effective_gemini_api_key() is None
    assert not (tmp_path / "gemini_key").exists()


def test_env_key_still_works_as_fallback(client, monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.gemini_api_key", "AIza" + "d" * 30
    )
    info = client.get("/api/settings").json()["llm"]
    assert info["configured"] is True
    assert info["source"] == "env"
