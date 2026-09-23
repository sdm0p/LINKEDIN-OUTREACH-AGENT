"""Fixture-first harness (branch item 5, design §6).

Contract: the committed seed fixture parses through the SAME entry point
the live source uses (parse_search_payload), the fixture saver behaves
per its off/always/on-error contract and never breaks a search, and the
replay CLI reports every fixture it is given.
"""

import json

import pytest

from app.search import fixtures as fx
from app.search import pw_replay
from app.search.base import Post
from app.search.linkedin_mcp import LinkedInMCPSource, parse_search_payload

SEED = (
    "mcp_search_hiring_developer_24h.json"
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    return tmp_path


@pytest.fixture()
def seed_payload():
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / SEED
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- seed fixture parses through the live entry point ----------


def test_seed_fixture_parses_to_clean_posts(seed_payload):
    posts = parse_search_payload(seed_payload)
    assert len(posts) == 4
    first = posts[0]
    assert first.author_name == "Aravind Kumar"
    assert "hiring" in first.text.lower()
    assert first.post_url.startswith("https://www.linkedin.com/posts/")
    assert first.raw.get("job_id") == "4464542122"
    assert first.posted_at  # relative age parsed to a timestamp


def test_seed_fixture_posts_pass_sanity(seed_payload):
    from app.pipeline.sanity import check_post

    for post in parse_search_payload(seed_payload):
        assert check_post(post).ok, post.author_name


def test_seed_fixture_ids_are_content_derived_and_stable(seed_payload):
    posts = parse_search_payload(seed_payload)
    again = parse_search_payload(seed_payload)
    assert [p.post_id for p in posts] == [p.post_id for p in again]
    assert all(len(p.post_id) == 24 for p in posts)


# ---------- the saver ----------


def test_saver_off_by_default_writes_nothing(isolated, seed_payload, monkeypatch):
    """Off (the default) means nothing is ever written — verified through
    the same decision the live source makes before calling the writer."""
    from app.config import settings

    assert settings.effective_save_fixtures() == "off"
    assert not fx.should_save(settings.effective_save_fixtures(), degraded=True)
    assert fx.load_fixtures() == []


def test_saver_always_writes_and_caps(isolated, seed_payload, monkeypatch):
    monkeypatch.setattr(fx, "FIXTURES_KEEP", 3)
    for i in range(5):
        fx.save_fixture("mcp", f"kw{i}", "24h", seed_payload, kind="mcp-payload")
    files = fx.load_fixtures()
    assert len(files) == 3  # newest kept, oldest evicted


@pytest.mark.asyncio
async def test_saver_on_error_only_saves_degraded(isolated, seed_payload, monkeypatch):
    """on-error mode: the source saves ONLY unusable responses (the
    selector-rot alarm) — clean searches write nothing."""
    from app.config import settings

    monkeypatch.setattr(settings, "save_fixtures", "on-error")
    source = LinkedInMCPSource()

    async def garbage(tool, arguments):
        return "<html>login page</html>"  # non-JSON -> degraded

    async def clean(tool, arguments):
        return json.dumps(seed_payload)

    monkeypatch.setattr(source, "_tool_text", garbage)
    await source.search_posts("kw", "24h")
    assert len(fx.load_fixtures()) == 1

    monkeypatch.setattr(source, "_tool_text", clean)
    await source.search_posts("kw", "24h")
    assert len(fx.load_fixtures()) == 1  # clean search saved nothing


def test_saver_kind_sets_extension(isolated, seed_payload):
    p1 = fx.save_fixture("pw", "kw", "24h", "<html></html>", kind="playwright-html")
    assert p1 is not None and p1.suffix == ".html"
    p2 = fx.save_fixture("mcp", "kw", "24h", seed_payload, kind="mcp-payload")
    assert p2 is not None and p2.suffix == ".json"


def test_should_save_matrix():
    assert fx.should_save("off", degraded=True) is False
    assert fx.should_save("always", degraded=False) is True
    assert fx.should_save("on-error", degraded=True) is True
    assert fx.should_save("on-error", degraded=False) is False


def test_effective_save_fixtures_fail_safe(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "save_fixtures", "ALWAYS")
    assert settings.effective_save_fixtures() == "always"
    monkeypatch.setattr(settings, "save_fixtures", "wat")
    assert settings.effective_save_fixtures() == "off"


# ---------- live-source hook ----------


@pytest.mark.asyncio
async def test_mcp_source_saves_fixture_when_enabled(isolated, seed_payload, monkeypatch):
    from app.config import settings

    source = LinkedInMCPSource()

    async def fake_tool_text(tool, arguments):
        return json.dumps(seed_payload)

    monkeypatch.setattr(source, "_tool_text", fake_tool_text)
    monkeypatch.setattr(settings, "save_fixtures", "always")

    result = await source.search_posts("hiring developer", "24h")
    assert len(result.posts) == 4
    files = fx.load_fixtures()
    assert len(files) == 1
    # The saved fixture re-parses to the same posts (round-trip proof).
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(parse_search_payload(saved)) == 4


@pytest.mark.asyncio
async def test_mcp_source_saves_nothing_when_off(isolated, seed_payload, monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_text(tool, arguments):
        return json.dumps(seed_payload)

    monkeypatch.setattr(source, "_tool_text", fake_tool_text)
    assert await source.search_posts("kw", "24h") is not None
    assert fx.load_fixtures() == []


# ---------- the replay CLI ----------


def test_cli_reports_seed_fixture(isolated, seed_payload, capsys):
    d = isolated / "fixtures"
    d.mkdir()
    (d / SEED).write_text(json.dumps(seed_payload), encoding="utf-8")
    rc = pw_replay.main(["--dir", str(d)])
    out = capsys.readouterr().out
    assert "Aravind Kumar" in out
    assert "4 posts" in out
    assert rc == 0


def test_cli_flags_degraded_capture(isolated, capsys):
    d = isolated / "fixtures"
    d.mkdir()
    (d / "mcp_bad_24h_x.json").write_text('{"not_sections": true}', encoding="utf-8")
    rc = pw_replay.main(["--dir", str(d)])
    out = capsys.readouterr().out
    assert "no sections dict" in out
    assert rc == 1


def test_cli_diff_reports_changes(isolated, seed_payload, capsys):
    """Post ids are body hashes, so the diff mutates a post BODY — that is
    exactly what a parser/shape change does to the extracted text."""
    a = isolated / "a.json"
    b = isolated / "b.json"
    a.write_text(json.dumps(seed_payload), encoding="utf-8")
    altered = json.loads(json.dumps(seed_payload))
    altered["sections"]["search_results"] = altered["sections"][
        "search_results"
    ].replace("Remote (US)", "Remote (EU)")
    b.write_text(json.dumps(altered), encoding="utf-8")
    rc = pw_replay.main(["--diff", str(a), str(b)])
    out = capsys.readouterr().out
    assert "+ new parse:" in out  # the reworded post is a different body now
    assert "- lost post:" in out
    assert rc == 1


def test_cli_playwright_html_reported_honestly(isolated, capsys):
    d = isolated / "fixtures"
    d.mkdir()
    (d / "pw_kw_24h_x.html").write_text("<html><body>feed</body></html>", encoding="utf-8")
    rc = pw_replay.main(["--dir", str(d)])
    out = capsys.readouterr().out
    assert "branch item 6" in out
    assert rc == 1
