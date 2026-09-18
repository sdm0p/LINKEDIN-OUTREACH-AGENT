"""Location normalisation: turn the location-ish lines of a LinkedIn hiring
post into a country.

Two regex passes do the heavy lifting (labeled lines like "📍 Location:
Bangalore, India" and bare city/country mentions with country aliases), and
the LLM's own extraction is merged on top in the pipeline. The regexes exist
so the cheap, deterministic answer wins when the text states a place plainly;
anything the text only implies is left to the LLM field.

The LLM classifies against a closed list of the countries that actually show
up in hiring posts (plus "Other"), and `_canonical_country` maps anything it
says back into the same canonical set — free-text location columns are only
filterable when the values repeat.
"""

import re

# Canonical country names as they appear in the queue's filter dropdown.
_COUNTRIES = [
    "India", "United States", "United Kingdom", "Canada", "Germany", "France",
    "Netherlands", "Spain", "Italy", "Poland", "Portugal", "Ireland", "Sweden",
    "Switzerland", "Austria", "Belgium", "Denmark", "Norway", "Finland",
    "Australia", "New Zealand", "Singapore", "United Arab Emirates",
    "Saudi Arabia", "Qatar", "Japan", "South Korea", "China", "Hong Kong",
    "Brazil", "Mexico", "Argentina", "South Africa", "Nigeria", "Kenya",
    "Egypt", "Israel", "Turkey", "Indonesia", "Malaysia", "Philippines",
    "Vietnam", "Thailand", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal",
]

# Aliases seen in posts/locations that should map to a canonical country.
_ALIASES = {
    "usa": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "us": "United States",
    "america": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "uae": "United Arab Emirates",
    "dubai": "United Arab Emirates",
    "abu dhabi": "United Arab Emirates",
    "ksa": "Saudi Arabia",
    "bengaluru": "India",
    "bangalore": "India",
    "gurugram": "India",
    "gurgaon": "India",
    "noida": "India",
    "hyderabad": "India",
    "pune": "India",
    "mumbai": "India",
    "delhi": "India",
    "new delhi": "India",
    "chennai": "India",
    "kolkata": "India",
    "ahmedabad": "India",
    "jaipur": "India",
    "indore": "India",
    "kochi": "India",
    "coimbatore": "India",
    "chandigarh": "India",
    "nyc": "United States",
    "new york": "United States",
    "san francisco": "United States",
    "bay area": "United States",
    "silicon valley": "United States",
    "seattle": "United States",
    "austin": "United States",
    "boston": "United States",
    "chicago": "United States",
    "los angeles": "United States",
    "toronto": "Canada",
    "vancouver": "Canada",
    "montreal": "Canada",
    "london": "United Kingdom",
    "manchester": "United Kingdom",
    "berlin": "Germany",
    "munich": "Germany",
    "amsterdam": "Netherlands",
    "paris": "France",
    "singapore city": "Singapore",
    "sydney": "Australia",
    "melbourne": "Australia",
    "brisbane": "Australia",
    "perth": "Australia",
}

# Longest-first so "united states of america" wins over "united states".
_LOOKUP_NAMES = sorted(
    set(_COUNTRIES) | set(_ALIASES), key=len, reverse=True
)

# "📍 Location: Bangalore, India" / "Location: Remote" / "Location - Dubai"
# Capture stops at sentence end (period followed by space/capital, newline,
# emoji) so trailing prose ("Bangalore, India. DM me") isn't swallowed.
_LABELED_LOCATION_RE = re.compile(
    r"(?:📍|📌|🌐)?\s*location\s*[:\-–]\s*(?P<val>[^\n]{2,80}?)(?=[.](?:\s|$)|\n|$)",
    re.IGNORECASE,
)

# "Hiring in Bengaluru" / "role based in India" / "office in London"
_IN_PLACE_RE = re.compile(
    r"\b(?:in|at|from)\s+([A-Z][A-Za-z .,'()&-]{1,40}?)"
    r"(?=[,.!;\n]|\s+(?:are|is|apply|send|dm|to|with|and|or|$))",
)

_REMOTE_RE = re.compile(
    r"\bremote\b(?:\s*[-–(]\s*(?P<region>anywhere|worldwide|us|usa|india|uk|eu|europe)\s*\)?)?",
    re.IGNORECASE,
)

# "US only", "India-based", "EU candidates"
_NATIONALITY_RE = re.compile(
    r"\b(usa|us|uk|india|canada|australia|germany|france|netherlands|singapore|"
    r"uae|europe|eu)\b(?:\s*(?:only|candidates?|applicants?|based))?",
    re.IGNORECASE,
)


def _canonical(raw: str) -> str | None:
    """Map a free-text place onto the canonical country list; None when it
    matches nothing (unknown places are dropped rather than guessed)."""
    text = (raw or "").strip().strip(".,;:()[]").lower()
    if not text:
        return None
    # Either the whole string is a place, or a place heads a comma list
    # ("Bangalore, India" -> try the whole, then each comma part).
    parts = [text] + [p.strip() for p in text.split(",")]
    for part in parts:
        for name in _LOOKUP_NAMES:
            if part == name.lower() or part.startswith(name.lower() + " "):
                return _ALIASES.get(name, name) if name in _ALIASES else name
    return None


def extract_location_hint(text: str) -> str | None:
    """The raw location line of the post ("Bangalore, India"), if one is
    labeled — used for display and as the LLM's cross-check."""
    match = _LABELED_LOCATION_RE.search(text or "")
    if not match:
        return None
    hint = match.group("val").strip().strip(".,;:()[]")
    return hint or None


def resolve_country(text: str) -> str | None:
    """Best-effort country from post text; None when nothing canonical is
    mentioned. Resolution order: labeled location line -> "in <Place>" ->
    remote/nationality hints."""
    text = text or ""
    hint = extract_location_hint(text)
    if hint:
        # "Bangalore, India": the tail is most often the country.
        parts = [p.strip() for p in hint.split(",") if p.strip()]
        for part in reversed(parts):
            country = _canonical(part)
            if country:
                return country
    match = _IN_PLACE_RE.search(text)
    if match:
        country = _canonical(match.group(1))
        if country:
            return country
    remote = _REMOTE_RE.search(text)
    if remote:
        # "Remote (US)" narrows the country; bare "remote" is its own bucket.
        country = _canonical(remote.group("region") or "")
        if country:
            return country
        return "Remote"
    match = _NATIONALITY_RE.search(text)
    if match:
        return _canonical(match.group(1))
    return None


def canonical_from_llm(value: str) -> str | None:
    """Canonicalize the LLM's location answer. "Remote" and the closed
    country list are kept; anything else is dropped."""
    cleaned = (value or "").strip().strip(".,;:()[]")
    if not cleaned:
        return None
    if cleaned.lower() == "remote":
        return "Remote"
    return _canonical(cleaned)


def known_countries() -> list[str]:
    """The dropdown list: every canonical country, plus the Remote and
    unknown buckets the queue can actually hold."""
    return ["Remote"] + _COUNTRIES


def selectable_target_countries() -> list[str]:
    """Countries that can be chosen as run targets. Deliberately excludes
    the Remote bucket: a bare "remote" post is not provably inside or
    outside any country, so "Remote" as a target would make the drop
    logic ill-defined."""
    return list(_COUNTRIES)


def outside_targets(targets: list[str], country: str | None) -> bool:
    """The location filter's drop condition — the YoE filter's analogue.

    True only when the place is KNOWN and provably not among the targets
    ("Berlin, Germany" with targets=["India"]). Never true for a post
    that names no resolvable place (None) — unknown passes, exactly like
    an unstated YoE requirement passes — nor for bare "Remote", which
    could sit inside any target country.
    """
    if not targets or not country:
        return False
    if country == "Remote":
        return False
    return country not in targets
