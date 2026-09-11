from app.pipeline.yoe_filter import company_family, extract_required_yoe, passes_yoe_filter
from app.search.base import Post


def _post(text: str, headline: str = "") -> Post:
    return Post(post_id="x", text=text, author_headline=headline)


def test_experience_colon_plus_form():
    assert extract_required_yoe("Experience: 3+ Years") == 3


def test_range_form():
    assert extract_required_yoe("Experience:0–5 Years") == 0


def test_range_form_min_extracted():
    assert extract_required_yoe("looking for 5-7 years of experience") == 5


def test_plain_n_plus_years():
    assert extract_required_yoe("Need someone with 2+ years in .NET") == 2


def test_minimum_form():
    assert extract_required_yoe("minimum 4 years required") == 4


def test_unstated_defaults_to_zero():
    assert extract_required_yoe("We are hiring, DM me!") == 0


def test_implausible_values_ignored():
    assert extract_required_yoe("99 years of experience needed lol") == 0


def test_filter_keeps_when_requirement_le_mine():
    kept, required = passes_yoe_filter(_post("Experience: 2+ Years"), 2)
    assert kept is True
    assert required == 2


def test_filter_drops_when_requirement_gt_mine():
    kept, required = passes_yoe_filter(_post("Experience: 5+ Years"), 2)
    assert kept is False
    assert required == 5


def test_filter_keeps_unstated():
    kept, required = passes_yoe_filter(_post("hiring now"), 0)
    assert kept is True
    assert required == 0


def test_company_family_from_pipe_header():
    post = _post("Hiring | .NET DEVELOPER\n\nLocation: Bangalore")
    assert company_family(post) == ".net developer"


def test_company_family_from_headline():
    post = _post("some body", headline="Recruiter at Vee Technologies | Hiring")
    assert company_family(post) == "vee technologies"


def test_company_family_none_when_nothing_found():
    assert company_family(_post("plain post")) is None
