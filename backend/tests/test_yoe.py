from app.models import ExperienceEntry, ParsedResume
from app.pipeline.resume_parse import (
    experience_span_years,
    parse_date_str,
    reconciled_experience_years,
    yoe_from_parsed,
)


def test_yoe_ceil_fractional():
    assert yoe_from_parsed(ParsedResume(total_experience_years=1.11)) == 2


def test_yoe_ceil_exact_integer_stays():
    assert yoe_from_parsed(ParsedResume(total_experience_years=5.0)) == 5


def test_yoe_zero_when_unknown():
    assert yoe_from_parsed(ParsedResume(total_experience_years=0.0)) == 0


def test_yoe_negative_clamped_to_zero():
    assert yoe_from_parsed(ParsedResume(total_experience_years=-3.0)) == 0


def test_yoe_absurd_value_capped():
    assert yoe_from_parsed(ParsedResume(total_experience_years=999.0)) == 50


# ---------- date parsing ----------


def test_parse_date_iso():
    assert parse_date_str("2022-06") == (2022, 6)


def test_parse_date_month_word():
    assert parse_date_str("Jun 2022") == (2022, 6)
    assert parse_date_str("September 2023") == (2023, 9)
    assert parse_date_str("Dec. 2021") == (2021, 12)


def test_parse_date_bare_year_is_midyear():
    assert parse_date_str("2022") == (2022, 6)


def test_parse_date_garbage_is_none():
    assert parse_date_str("") is None
    assert parse_date_str("Present") is None
    assert parse_date_str("soon") is None
    assert parse_date_str("2022-13") is None


# ---------- deterministic span from date ranges ----------


def test_span_present_counts_up_to_today():
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="SWE", company="A", start_date="2022-06", end_date="Present")
        ]
    )
    span = experience_span_years(parsed)
    # Jun 2022 -> today; today (2026-09) gives ~4.3y. Just assert the shape:
    # strictly more than the naive "Jun 2022 -> Jun 2023" reading.
    assert span is not None
    assert span > 3.0, f"Present should count up to today, got {span}"


def test_span_ignores_entry_without_start():
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="SWE", company="A", start_date="", end_date="Present")
        ]
    )
    assert experience_span_years(parsed) is None


def test_span_none_when_no_dates():
    assert experience_span_years(ParsedResume()) is None


def test_span_merges_overlapping_roles():
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="A", company="X", start_date="2020-01", end_date="2022-01"),
            ExperienceEntry(title="B", company="Y", start_date="2021-06", end_date="2023-06"),
        ]
    )
    # Union: Jan 2020 -> Jun 2023 = 42 months = 3.5y (overlap counted once)
    span = experience_span_years(parsed)
    assert span is not None
    assert abs(span - 42 / 12) < 1e-9


def test_span_skips_nonsense_dates():
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="A", company="X", start_date="2025-01", end_date="2020-01")
        ]
    )
    assert experience_span_years(parsed) is None


# ---------- reconciliation: date math beats the LLM estimate ----------


def test_reconciled_prefers_date_math_on_present_undercount():
    """The reported bug: LLM says ~1y for a 2022->Present role because it
    cannot know what 'now' is. Date math must win."""
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="SWE", company="A", start_date="2022-06", end_date="Present")
        ],
        total_experience_years=1.0,  # typical LLM undercount
    )
    years, source = reconciled_experience_years(parsed)
    assert source == "date-math"
    assert years > 3.0


def test_reconciled_yoe_from_present_dates():
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="SWE", company="A", start_date="2022-06", end_date="Present")
        ],
        total_experience_years=1.0,
    )
    assert yoe_from_parsed(parsed) >= 4  # ceil of ~4.3y as of 2026-09


def test_reconciled_agreement_prefers_date_math():
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="A", company="X", start_date="2020-01", end_date="2022-01")
        ],
        total_experience_years=2.0,
    )
    years, source = reconciled_experience_years(parsed)
    assert source == "date-math"
    assert abs(years - 25 / 12) < 1e-9  # Jan 2020 through end of Jan 2022


def test_reconciled_llm_estimate_used_without_dates():
    parsed = ParsedResume(total_experience_years=6.5)
    years, source = reconciled_experience_years(parsed)
    assert source == "llm-estimate"
    assert years == 6.5


def test_reconciled_llm_estimate_wins_when_dates_miss_history():
    """Earlier roles the parser didn't extract dates for: the LLM's larger
    estimate should stand (dates only cover part of the history)."""
    parsed = ParsedResume(
        experience=[
            ExperienceEntry(title="SWE", company="A", start_date="2024-01", end_date="Present")
        ],
        total_experience_years=7.0,
    )
    years, source = reconciled_experience_years(parsed)
    assert source == "llm-estimate"
    assert years == 7.0
