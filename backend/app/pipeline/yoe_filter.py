"""Stage 5+6 — dedup keys and the YoE filter.

Dedup itself is record-keeping in the store (the orchestrator skips posts
already processed); this module derives the dedup keys. The YoE filter
extracts the required experience from post text and drops posts asking for
more than the candidate has. Posts with no stated requirement default to 0
and always pass.
"""

import re

from ..search.base import Post

# Observed post phrasings: "Experience: 3+ Years", "0–5 Years",
# "5-7 years of experience", "minimum 2 years".
_YOE_PATTERNS = [
    re.compile(
        r"(?:experience|exp\.?)\s*(?:of|:|=)?\s*(?P<min>\d{1,2})\s*(?:\+|\+ years)?\s*"
        r"(?:[-–—]|to)?\s*(?P<max>\d{1,2})?\s*(?:years|yrs|year)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<min>\d{1,2})\s*\+\s*(?:years|yrs|year)\b(?:\s*of\s+experience)?",
        re.IGNORECASE,
    ),
    re.compile(r"\bminimum(?:\s+of)?\s*(?P<min>\d{1,2})\s*(?:years|yrs)\b", re.IGNORECASE),
    re.compile(
        r"(?P<min>\d{1,2})\s*(?:-|–|—|to)\s*(?P<max>\d{1,2})\s*(?:years|yrs)\b"
        r"(?:\s*of\s+experience)?",
        re.IGNORECASE,
    ),
]

_INVALID = (99, 40)  # sanity bounds: YoE requirements outside this are noise


def extract_required_yoe(text: str) -> int:
    """Minimum required years from post text; 0 when not stated."""
    for pattern in _YOE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        min_years = int(match.group("min"))
        max_years = int(match.group("max")) if match.groupdict().get("max") else None
        if not (0 <= min_years <= _INVALID[0]):
            continue
        if max_years is not None and not (min_years <= max_years <= _INVALID[1]):
            continue
        return min_years
    return 0


def passes_yoe_filter(post: Post, my_yoe: int) -> tuple[bool, int]:
    """(kept?, required_yoe). Keep when required_min <= my_yoe; unstated -> 0."""
    required = extract_required_yoe(post.text)
    return required <= my_yoe, required



def company_family(post: Post) -> str | None:
    """Best-effort company guess for dedup ('company/role-family').

    Sources, in order: 'X | Y' in the post's first line (role | company),
    'at Company' in the author headline. Lowercased for matching; None when
    nothing plausible is found.
    """
    first_line = post.text.splitlines()[0] if post.text else ""
    if "|" in first_line:
        tail = first_line.split("|", 1)[1].strip()
        if 1 < len(tail) <= 40:
            return tail.lower()
    match = re.search(r"\bat\s+([A-Z][\w&.'\- ]{1,40})", post.author_headline or "")
    if match:
        return match.group(1).strip().lower()
    return None
