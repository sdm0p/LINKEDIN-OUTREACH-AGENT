"""Target-country location filter: the YoE filter's analogue.

Drop semantics mirror the YoE rule exactly — hard drop only on positive
knowledge (a resolvable place outside the targets); posts with no
resolvable place pass, just like an unstated YoE requirement passes.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_target_countries, set_target_countries
from app.main import app
from app.pipeline.location import outside_targets, selectable_target_countries
from app.search.base import Post
from app.services import queue_service


# ---------- drop semantics ----------


def test_drop_only_on_positive_knowledge():
    assert outside_targets(["India"], "Germany") is True  # known, outside
    assert outside_targets(["India"], "India") is False
    assert outside_targets(["India"], None) is False  # unknown passes
    assert outside_targets(["India"], "") is False
    assert outside_targets(["India"], "Remote") is False  # could be anywhere
    assert outside_targets(["India", "Germany"], "Germany") is False
    assert outside_targets([], "Germany") is False  # no targets = no filter


def test_selectable_targets_exclude_remote():
    targets = selectable_target_countries()
    assert "India" in targets
    assert "Remote" not in targets


# ---------- config persistence ----------


def test_target_countries_persist(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    assert get_target_countries() == []  # default: no filter

    set_target_countries(["India"])
    assert get_target_countries() == ["India"]

    # Corrupted file fails open (never silently start dropping posts).
    (tmp_path / "target_countries.json").write_text("{not json", encoding="utf-8")
    assert get_target_countries() == []


# ---------- settings API ----------


def test_settings_roundtrip_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as client:
        res = client.get("/api/settings")
        assert res.json()["location_targets"] == {"countries": [], "available": selectable_target_countries()}

        res = client.put("/api/settings/location-targets", json={"countries": ["India", "Germany"]})
        assert res.status_code == 200
        assert res.json()["countries"] == ["India", "Germany"]

        # Reflected in GET and deduped.
        res = client.put("/api/settings/location-targets", json={"countries": ["India", "India"]})
        assert res.json()["countries"] == ["India"]

        # 'Remote' and free text are rejected — the drop rule needs
        # well-defined targets.
        res = client.put("/api/settings/location-targets", json={"countries": ["Remote"]})
        assert res.status_code == 400
        res = client.put("/api/settings/location-targets", json={"countries": ["Narnia"]})
        assert res.status_code == 400

        # Empty list clears the filter.
        res = client.put("/api/settings/location-targets", json={"countries": []})
        assert res.json()["countries"] == []
        assert get_target_countries() == []


# ---------- ingest integration ----------


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = 0

    async def generate_json(self, *, system: str, user: str):
        self.calls += 1
        return {"verdict": "hiring", "company": "Acme", "role": "Dev", "emails": [], "location": ""}


class _Trace:
    def __init__(self):
        self.entries = []

    def log(self, stage, detail):
        self.entries.append((stage, detail))


def _post(text: str, pid: str) -> Post:
    return Post(
        post_id=pid,
        text=text,
        author_name="Recruiter Rita",
        author_headline="Talent Lead at Acme",
        author_profile_url="https://www.linkedin.com/in/rita/",
    )


@pytest.mark.asyncio
async def test_ingest_drops_outside_target_before_llm(tmp_path, monkeypatch):
    """A Berlin post with India as target is dropped deterministically,
    before any LLM call — the YoE drop's exact analogue."""
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    set_target_countries(["India"])
    provider = FakeProvider()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    async def _no_people(query, trace=None):
        return []

    monkeypatch.setattr(queue_service, "_search_people_safe", _no_people)

    trace = _Trace()
    counts = await queue_service.ingest_posts(
        [_post("WE'RE HIRING | .NET DEVELOPER\n📍 Location: Berlin, Germany\nDM me.", "loc-drop")],
        "kw", "run1", trace,
    )
    assert counts["location_dropped"] == 1
    assert counts["leads"] == 0
    assert provider.calls == 0  # dropped before the LLM, like the YoE drop
    assert {stage for stage, _ in trace.entries} == {"location-filter"}
    assert await queue_service.list_queue() == []


@pytest.mark.asyncio
async def test_ingest_keeps_target_and_unknown_posts(tmp_path, monkeypatch):
    """India posts pass; no-location posts pass (unknown ≠ outside)."""
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    set_target_countries(["India"])
    provider = FakeProvider()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    async def _fake_people(query, trace=None):
        return [{"name": "Recruiter Rita", "profile_url": "https://www.linkedin.com/in/rita/"}]

    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    trace = _Trace()
    counts = await queue_service.ingest_posts(
        [
            _post("Hiring!\n📍 Location: Bangalore, India. DM me.", "in1"),
            _post("We are hiring a Backend Engineer. DM me!", "unk1"),
        ],
        "kw", "run1", trace,
    )
    assert counts["location_dropped"] == 0
    assert counts["leads"] == 2
    countries = {r["post_id"]: r["country"] for r in await queue_service.list_queue()}
    assert countries == {"in1": "India", "unk1": None}


@pytest.mark.asyncio
async def test_post_llm_net_catches_regex_misses(tmp_path, monkeypatch):
    """The regex resolver misses 'opportunity in Stockholm'; the LLM's
    location field reads it, and the post-LLM check drops the post."""
    from app.pipeline.extraction import CLASSIFY_SYSTEM_PROMPT

    class SwedenLLM:
        name = "fake"

        def __init__(self):
            self.calls = 0

        async def generate_json(self, *, system: str, user: str):
            self.calls += 1
            assert system == CLASSIFY_SYSTEM_PROMPT
            return {"verdict": "hiring", "company": "Acme", "role": "Dev",
                    "emails": [], "location": "Sweden"}

    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    set_target_countries(["India"])
    provider = SwedenLLM()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    counts = await queue_service.ingest_posts(
        [_post("Great opportunity in Stockholm, apply soon!", "se1")],
        "kw", "run1", _Trace(),
    )
    assert counts["location_dropped"] == 1
    assert counts["leads"] == 0
    assert provider.calls == 1  # classified first, dropped after


@pytest.mark.asyncio
async def test_no_targets_means_no_location_filter(tmp_path, monkeypatch):
    """Empty targets (the default) never drops on location."""
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    provider = FakeProvider()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    async def _fake_people(query, trace=None):
        return [{"name": "Recruiter Rita", "profile_url": "https://www.linkedin.com/in/rita/"}]

    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    counts = await queue_service.ingest_posts(
        [_post("Hiring in Berlin, Germany! DM me.", "de-any")],
        "kw", "run1", _Trace(),
    )
    assert counts["location_dropped"] == 0
    assert counts["leads"] == 1


@pytest.mark.asyncio
async def test_city_only_india_post_survives_target_filter(tmp_path, monkeypatch):
    """Regression: a post naming only a city (no 'India' token anywhere)
    survives the location filter with target India — city aliases resolve
    to the country. Search-side query augmentation used to hide this bug
    by never returning such posts from LinkedIn at all."""
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    set_target_countries(["India"])
    provider = FakeProvider()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    async def _fake_people(query, trace=None):
        return [{"name": "Recruiter Rita", "profile_url": "https://www.linkedin.com/in/rita/"}]

    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    counts = await queue_service.ingest_posts(
        [
            _post("We are hiring a Fullstack Dev!\n📍 Location: Hyderabad\nDM me.", "city1"),
            _post("Hiring in Bangalore, great team. DM me!", "city2"),
        ],
        "kw", "run1", _Trace(),
    )
    assert counts["location_dropped"] == 0
    assert counts["leads"] == 2
    countries = {r["post_id"]: r["country"] for r in await queue_service.list_queue()}
    assert countries == {"city1": "India", "city2": "India"}
