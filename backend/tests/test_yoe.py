from app.models import ParsedResume
from app.pipeline.resume_parse import yoe_from_parsed


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
