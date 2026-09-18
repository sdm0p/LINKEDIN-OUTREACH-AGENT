"""Parser regression tests: post permalinks and attached job references.

The MCP server's search_posts response carries `feed_post` (permalink) and
`job` (job card) references in feed order alongside the person references.
These tests pin the positional mapping onto parsed posts.
"""

import json

from app.search.linkedin_mcp import (
    LinkedInMCPSource,
    _job_references,
    _parse_posted_at,
    _post_permalinks,
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
            {"kind": "feed_post", "url": "/posts/aravind-kumar_gios-activity-7200000000000000000-AbCd", "context": "search_results"},
            {"kind": "feed_post", "url": "/feed/update/urn:li:activity:7100000000000000000/", "context": "search_results"},
            {"kind": "feed_post", "url": "/posts/manoj-kumar_vee-ugcPost-7300000000000000000-EfGh", "context": "search_results"},
            {"kind": "person", "url": "/in/sujal-vanpariya-6273a5321/", "text": "Sujal Vanpariya", "context": "search result"},
            {"kind": "person", "url": "/in/manoj-kumar-931b/", "text": "Manoj kumar Singaravel", "context": "search result"},
        ]
    },
}


def _parse_all():
    from app.search.linkedin_mcp import _person_references, _split_feed_posts, _parse_post_chunk

    refs = _person_references(SAMPLE)
    posts = []
    for lines in _split_feed_posts(SAMPLE["sections"]["search_results"]):
        post = _parse_post_chunk(lines, refs)
        if post:
            posts.append(post)
    permalinks = _post_permalinks(SAMPLE)
    job_refs = _job_references(SAMPLE)
    for i, post in enumerate(posts):
        if i < len(permalinks):
            post.post_url = permalinks[i]
        if i < len(job_refs):
            post.raw["job_id"] = job_refs[i]["job_id"]
            post.raw["job_url"] = job_refs[i]["job_url"]
    return posts


def test_post_permalinks_in_feed_order():
    links = _post_permalinks(SAMPLE)
    assert len(links) == 3
    assert links[0].startswith("https://www.linkedin.com/posts/")
    assert links[1].startswith("https://www.linkedin.com/feed/update/")


def test_job_references_extract_ids():
    jobs = _job_references(SAMPLE)
    assert jobs == [
        {
            "job_id": "4464542122",
            "job_url": "https://www.linkedin.com/jobs/view/4464542122/",
        }
    ]


def test_permalink_mapped_to_first_post():
    posts = _parse_all()
    assert posts[0].post_url.startswith("https://www.linkedin.com/posts/aravind")
    # The one job reference in the sample maps positionally onto post 0
    # (feed order): the job card for the Training Developer post.
    assert posts[0].raw.get("job_id") == "4464542122"


def test_job_mapped_positionally():
    posts = _parse_all()
    # The job reference precedes the first permalink in the sample, so it
    # maps onto post 0 (feed order).
    assert posts[0].raw.get("job_id") == "4464542122"
    assert posts[0].raw.get("job_url", "").endswith("/jobs/view/4464542122/")


def test_posted_at_parsed_from_relative_age():
    posts = _parse_all()
    assert posts[0].posted_at != ""  # 19h
    assert posts[2].posted_at != ""  # 2h
    # 2h post is more recent than the 19h post.
    assert posts[2].posted_at > posts[0].posted_at


def test_parse_posted_at_units():
    assert _parse_posted_at("2h •") != ""
    assert _parse_posted_at("1mo •") != ""
    assert _parse_posted_at("3d •") != ""
    assert _parse_posted_at("") == ""
    assert _parse_posted_at("Follow") == ""


def test_search_posts_end_to_end_with_permalink_mapping(monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_text(tool, arguments):
        assert tool == "search_posts"
        return json.dumps(SAMPLE)

    monkeypatch.setattr(source, "_tool_text", fake_tool_text)

    import asyncio

    result = asyncio.run(source.search_posts("hiring developer", "24h"))
    assert result.raw_hit_count == 3
    posts = result.posts
    assert posts[0].post_url.startswith("https://www.linkedin.com/posts/")
    assert posts[0].raw.get("job_id") == "4464542122"


def test_get_job_details_parses_sections(monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_data(tool, arguments):
        assert tool == "get_job_details"
        assert arguments["job_id"] == "4464542122"
        return {
            "url": "https://www.linkedin.com/jobs/view/4464542122/",
            "sections": {
                "job_posting": "Training Development Specialist\nGIOS Technology\n📍 Location: Cumbria (On-site)"
            },
        }

    monkeypatch.setattr(source, "_tool_data", fake_tool_data)

    import asyncio

    details = asyncio.run(source.get_job_details("4464542122"))
    assert "Training Development Specialist" in details["sections"]["job_posting"]


def test_search_job_ids_filters_numeric(monkeypatch):
    source = LinkedInMCPSource()

    async def fake_tool_data(tool, arguments):
        assert tool == "search_jobs"
        assert arguments["keywords"] == ".net developer"
        return {"url": "https://x", "sections": {}, "job_ids": ["4464542122", "oops"]}

    monkeypatch.setattr(source, "_tool_data", fake_tool_data)

    import asyncio

    matches = asyncio.run(source.search_job_ids(".net developer"))
    assert matches == [
        {
            "job_id": "4464542122",
            "job_url": "https://www.linkedin.com/jobs/view/4464542122/",
        }
    ]
