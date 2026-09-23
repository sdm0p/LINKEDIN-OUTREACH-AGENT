"""Tests for the SEARCH_SOURCE switch (branch item 1).

The factory must pick the implementation from settings, keep registry
state isolated between picks, and behave fail-safe on unknown values —
without importing playwright or docker at module import time.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.search import base
from app.search.base import (
    SOURCE_NAME,
    get_linkedin_source,
    reset_source_registry,
)
from app.search.linkedin_mcp import LinkedInMCPSource
from app.search.playwright_source import PlaywrightSource


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_source_registry()
    yield
    reset_source_registry()


# ---------- factory selection ----------


def test_default_is_mcp(monkeypatch):
    monkeypatch.setattr("app.config.settings.search_source", "mcp")
    source = get_linkedin_source()
    assert isinstance(source, LinkedInMCPSource)
    assert base.registry.get(SOURCE_NAME) is source


def test_playwright_setting_selects_playwright_source(monkeypatch):
    monkeypatch.setattr("app.config.settings.search_source", "playwright")
    source = get_linkedin_source()
    assert isinstance(source, PlaywrightSource)
    assert source.name == SOURCE_NAME


def test_unknown_setting_falls_back_to_mcp(monkeypatch):
    monkeypatch.setattr("app.config.settings.search_source", "nonsense")
    assert isinstance(get_linkedin_source(), LinkedInMCPSource)


def test_effective_search_source_normalizes(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "search_source", "playwright")
    assert settings.effective_search_source() == "playwright"
    monkeypatch.setattr(settings, "search_source", " MCP ")
    assert settings.effective_search_source() == "mcp"  # tolerated spelling
    monkeypatch.setattr(settings, "search_source", "")
    assert settings.effective_search_source() == "mcp"


def test_registry_caches_until_reset(monkeypatch):
    monkeypatch.setattr("app.config.settings.search_source", "mcp")
    first = get_linkedin_source()
    assert get_linkedin_source() is first
    monkeypatch.setattr("app.config.settings.search_source", "playwright")
    assert get_linkedin_source() is first  # cached until reset
    reset_source_registry()
    assert isinstance(get_linkedin_source(), PlaywrightSource)


# ---------- settings API ----------


def test_settings_endpoint_roundtrip_and_validation():
    with TestClient(app) as client:
        res = client.put("/api/settings/search-source", json={"source": "wat"})
        assert res.status_code == 400

        res = client.put("/api/settings/search-source", json={"source": "playwright"})
        assert res.status_code == 200
        assert res.json()["source"] == "playwright"

        res = client.get("/api/settings")
        assert res.json()["search_source"]["name"] == "playwright"
        assert "mcp" in res.json()["search_source"]["available_sources"]

        res = client.put("/api/settings/search-source", json={"source": "mcp"})
        assert res.status_code == 200
        assert res.json()["source"] == "mcp"


# ---------- the honesty rules (design §8) ----------


def test_playwright_search_posts_fails_loudly_not_via_mcp(monkeypatch):
    """A selected-but-unimplemented source must raise, never silently
    re-route search to MCP — that would hide Playwright bugs."""
    monkeypatch.setattr("app.config.settings.search_source", "playwright")
    source = get_linkedin_source()
    with pytest.raises(NotImplementedError):
        asyncio.run(source.search_posts("hiring developer", "24h"))


def test_playwright_dm_and_job_calls_delegate_to_mcp(monkeypatch):
    """search_people and job enrichment are the design's v1 MCP fallback,
    delegated inside the class."""
    monkeypatch.setattr("app.config.settings.search_source", "playwright")
    source = get_linkedin_source()

    class FakeDelegate:
        def __init__(self):
            self.people_calls = []
            self.job_calls = []

        async def search_people(self, query):
            self.people_calls.append(query)
            return [{"name": "Rita", "profile_url": "https://x/in/rita/"}]

        async def get_job_details(self, job_id):
            self.job_calls.append(job_id)
            return {"url": "x", "sections": {}}

        async def search_job_ids(self, keyword, location=None):
            return [{"job_id": "1", "job_url": "u"}]

        async def close(self):
            pass

    fake = FakeDelegate()
    monkeypatch.setattr(source, "_mcp_delegate", lambda: fake)

    assert asyncio.run(source.search_people("rita acme")) == [
        {"name": "Rita", "profile_url": "https://x/in/rita/"}
    ]
    assert fake.people_calls == ["rita acme"]
    assert asyncio.run(source.get_job_details("42")) == {"url": "x", "sections": {}}
    assert asyncio.run(source.search_job_ids("kw")) == [
        {"job_id": "1", "job_url": "u"}
    ]
    asyncio.run(source.close())  # delegate released, safe to call again


def test_playwright_check_health_not_configured(monkeypatch, tmp_path):
    """Honest badge: no profile dir yet -> not_configured, never 'valid'."""
    from app.config import settings

    monkeypatch.setattr("app.config.settings.search_source", "playwright")
    monkeypatch.setattr(settings, "pw_profile_dir", tmp_path / "nope")
    source = get_linkedin_source()
    health = asyncio.run(source.check_health())
    assert health["status"] == "not_configured"
