"""Tests for country/location resolution on hiring posts."""

from app.pipeline.location import (
    canonical_from_llm,
    extract_location_hint,
    known_countries,
    resolve_country,
)


def test_labeled_line_country_last():
    assert resolve_country("We are hiring!\n📍 Location: Bangalore, India") == "India"


def test_labeled_line_city_alias():
    assert resolve_country("Hiring now\nLocation: Bengaluru") == "India"


def test_labeled_line_remote():
    assert resolve_country("Hiring\nLocation: Remote") == "Remote"


def test_in_place_form():
    assert resolve_country("We are hiring Java developers in India. DM me.") == "India"
    assert resolve_country("Now hiring in Berlin!") == "Germany"


def test_remote_only():
    assert resolve_country("Fully remote role, apply by DM.") == "Remote"


def test_remote_with_country_parenthetical():
    # "Remote (US)" — the parenthetical narrows the country.
    assert resolve_country("Remote (US) only. DM me.") == "United States"


def test_nationality_hint():
    assert resolve_country("Looking for candidates only from USA.") == "United States"


def test_no_location_is_none():
    assert resolve_country("We are hiring a Backend Engineer. DM me!") is None
    assert resolve_country("") is None


def test_unknown_place_is_none():
    assert resolve_country("Hiring in Zyrgx Valley!") is None


def test_extract_location_hint():
    assert extract_location_hint("📍 Location: Bangalore, India") == "Bangalore, India"
    assert extract_location_hint("no location here") is None


def test_llm_location_canonicalized():
    assert canonical_from_llm("India") == "India"
    assert canonical_from_llm("india") == "India"
    assert canonical_from_llm("Remote") == "Remote"
    assert canonical_from_llm("USA") == "United States"
    assert canonical_from_llm("Bangalore") == "India"


def test_llm_unknown_location_dropped():
    assert canonical_from_llm("Atlantis") is None
    assert canonical_from_llm("") is None


def test_known_countries_includes_remote():
    countries = known_countries()
    assert "Remote" in countries
    assert "India" in countries
    assert "United States" in countries
