"""Regression tests for the search_posts response parser.

Shapes mirror the live response confirmed by the validation pass:
sections.search_results is one text blob with posts separated by
"Feed post" lines; references.search_results maps author names to
profile URLs. If LinkedIn changes its page structure and the live
validation pass starts looking wrong, these tests are the first place
the new reality gets encoded.
"""

import json

from app.search.linkedin_mcp import (
    LinkedInMCPSource,
    _person_references,
    _split_feed_posts,
    _parse_post_chunk,
)

SAMPLE = {
    "url": "https://www.linkedin.com/search/results/content/?keywords=hiring+developer",
    "sections": {
        "search_results": (
            "Feed post\n\nAravind Kumar\n\n \n • 3rd+\n\nTechnical Recruiter at Gios Technology\n\n19h • \n\n"
            "Follow\n\nWe are Hiring: Training Developer | Onsite\nWhat We Look For\n#Hiring\n… more\n\n"
            "Training Development Specialist (Verified job)\nTraining Development Specialist \n\nGIOS Technology\n\n"
            "Cumbria (On-site)\n\nView job\n\nActively reviewing applicants\n"
            "Feed post\n\nSujal Vanpariya\n\n \n • 3rd+\n\nBusiness Development Executive at Krox\n\n11h • \n\n"
            "Follow\n\nImmediate Hiring | Java Developer | USA\nWe are actively hiring Java Developers.\n"
            "Experience:0–5 Years\n\n6\n6\n"
            "Feed post\n\nManoj kumar Singaravel\n\n \n • 3rd+\n\nTeam Lead | Vee Technologies\n\n2h • \n\n"
            "Follow\n\nWE'RE HIRING | .NET DEVELOPER\n📍 Location: Bangalore\n💼 Experience: 3+ Years\n\n5\n"
        )
    },
    "references": {
        "search_results": [
            {"kind": "person", "url": "/in/aravind-kumar-71b418201/", "text": "Aravind Kumar", "context": "search result"},
            {"kind": "job", "url": "/jobs/view/4464542122/", "text": "job", "context": "job result"},
            {"kind": "person", "url": "/in/sujal-vanpariya-6273a5321/", "text": "Sujal Vanpariya", "context": "search result"},
            {"kind": "person", "url": "/in/manoj-kumar-931b/", "text": "Manoj kumar Singaravel", "context": "search result"},
        ]
    },
}


def _parse_all():
    data = SAMPLE
    refs = _person_references(data)
    posts = []
    for lines in _split_feed_posts(data["sections"]["search_results"]):
        post = _parse_post_chunk(lines, refs)
        if post:
            posts.append(post)
    return refs, posts


def test_person_references_extracts_profiles_and_skips_jobs():
    refs, _ = _parse_all()
    assert "aravind kumar" in refs
    assert refs["aravind kumar"] == "https://www.linkedin.com/in/aravind-kumar-71b418201/"
    assert all("jobs/view" not in u for u in refs.values())


def test_splits_into_three_posts():
    _, posts = _parse_all()
    assert len(posts) == 3


def test_authors_and_headlines():
    _, posts = _parse_all()
    assert posts[0].author_name == "Aravind Kumar"
    assert posts[0].author_headline == "Technical Recruiter at Gios Technology"
    assert posts[1].author_name == "Sujal Vanpariya"
    assert posts[2].author_name == "Manoj kumar Singaravel"


def test_profile_urls_resolved():
    _, posts = _parse_all()
    assert posts[0].author_profile_url.endswith("/in/aravind-kumar-71b418201/")
    assert posts[2].author_profile_url.endswith("/in/manoj-kumar-931b/")


def test_job_card_noise_removed_from_body():
    _, posts = _parse_all()
    assert "View job" not in posts[0].text
    assert "Actively reviewing" not in posts[0].text
    assert "(Verified job)" not in posts[0].text
    assert posts[0].text.startswith("We are Hiring")


def test_trailing_reaction_counts_dropped():
    _, posts = _parse_all()
    assert not posts[1].text.splitlines()[-1].strip().isdigit()
    assert "Experience:0–5 Years" in posts[1].text


def test_post_ids_are_stable_content_hashes():
    _, posts = _parse_all()
    again_refs, again_posts = _parse_all()
    assert [p.post_id for p in posts] == [p.post_id for p in again_posts]
    assert len({p.post_id for p in posts}) == 3


def test_search_posts_end_to_end_on_sample(monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_text(tool, arguments):
        assert tool == "search_posts"
        assert arguments["date_posted"] == "past-24h"
        return json.dumps(SAMPLE)

    monkeypatch.setattr(source, "_tool_text", fake_tool_text)

    import asyncio

    result = asyncio.run(source.search_posts("hiring developer", "24h"))
    assert result.raw_hit_count == 3
    assert len(result.posts) == 3
    assert result.degraded is False


def test_degraded_flag_on_garbage_text(monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_text(tool, arguments):
        return "Sorry, something went wrong with your request."

    monkeypatch.setattr(source, "_tool_text", fake_tool_text)

    import asyncio

    result = asyncio.run(source.search_posts("hiring developer", "24h"))
    assert result.posts == []
    assert result.degraded is True


def test_empty_results_not_degraded(monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_text(tool, arguments):
        return json.dumps({"url": "https://x", "sections": {"search_results": ""}, "references": {}})

    monkeypatch.setattr(source, "_tool_text", fake_tool_text)

    import asyncio

    result = asyncio.run(source.search_posts("hiring developer", "24h"))
    assert result.posts == []
    assert result.degraded is False
