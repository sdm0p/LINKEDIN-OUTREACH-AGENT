from app.pipeline.extraction import extract_emails_regex


def test_plain_gmail():
    assert extract_emails_regex("send CV to john.doe@gmail.com today") == [
        "john.doe@gmail.com"
    ]


def test_obfuscated_at_dot_form():
    assert extract_emails_regex("reach me: name123 at gmail dot com") == [
        "name123@gmail.com"
    ]


def test_obfuscated_bracket_form():
    assert extract_emails_regex("me [at] gmail [dot] com") == ["me@gmail.com"]


def test_obfuscated_paren_form():
    assert extract_emails_regex("contact: first.last(at)gmail(dot)com") == [
        "first.last@gmail.com"
    ]


def test_mixed_and_dedup():
    text = "a@gmail.com or a at gmail dot com, also b@company.co"
    assert extract_emails_regex(text) == ["a@gmail.com", "b@company.co"]


def test_none_found():
    assert extract_emails_regex("no contact here") == []
