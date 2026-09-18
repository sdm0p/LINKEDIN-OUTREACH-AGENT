"""Stage 1 — Resume parse.

Extracts skills, roles, and the experience section from the resume PDF via
the LLM. YoE reconciles the LLM's total-experience estimate against the
deterministic union of the parsed date ranges ("Present" = today), because
LLMs chronically undercount ongoing roles. Callers cache the result keyed
by file hash (see services/resume_service.py).
"""

import asyncio
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime

import pdfplumber

from ..llm import LLMError, LLMProvider
from ..models import ExperienceEntry, ParsedResume

PARSE_SYSTEM_PROMPT = """\
You are a precise resume parser. You receive the raw text of a resume and
respond with ONLY a JSON object, no markdown, no commentary, matching:

{
  "name": "...",
  "skills": ["..."],
  "roles": ["..."],
  "experience": [
    {"title": "...", "company": "...", "start_date": "YYYY-MM", "end_date": "YYYY-MM | Present"}
  ],
  "total_experience_years": 0.0
}

Today's date is {TODAY}. Roles whose end date is "Present" are ongoing —
count them up to today, not up to any other reference point.

Rules:
- name: the candidate's full name as written at the top of the resume.
- skills: concrete technical and professional skills (languages, frameworks,
  tools, domains). Keep the resume's own naming. No soft skills.
- roles: the job titles the candidate has held, cleaned up (e.g. "SDE-2",
  "GenAI Engineer"). Most recent first.
- experience: one entry per role in the experience section, most recent
  first. Dates in YYYY-MM when the resume gives them, otherwise the year
  ("2023"). Use exactly "Present" for the current role. Empty strings when
  a field is genuinely absent — never invent companies or dates.
- total_experience_years: your best estimate of cumulative professional
  experience in years, as a number, possibly fractional (e.g. 1.75), based
  only on the experience section. Count overlapping roles once, not twice.
"""

# Date parsing for the deterministic cross-check below. The parser prompt
# asks for "YYYY-MM" (or a bare year); these tolerate the variants real
# resumes actually contain: "Jun 2022", "June 2022", "2022-06", "2022".
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_WORD_RE = re.compile(r"^([a-z]{3,9})\.?\s+(\d{4})$", re.IGNORECASE)
_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_YEAR_RE = re.compile(r"^(\d{4})$")


def parse_date_str(value: str) -> tuple[int, int] | None:
    """('2022-06' | 'Jun 2022' | '2022') -> (year, month); None when
    unparseable. A bare year resolves to June (mid-year, ±0.5y error)."""
    text = (value or "").strip()
    if not text:
        return None
    iso = _ISO_RE.match(text)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        return (year, month) if 1 <= month <= 12 else None
    year_only = _YEAR_RE.match(text)
    if year_only:
        return int(year_only.group(1)), 6  # mid-year: bounds error at ±0.5y
    month_word = _MONTH_WORD_RE.match(text)
    if month_word:
        month = _MONTHS.get(month_word.group(1)[:3].lower())
        if month:
            return int(month_word.group(2)), month
    return None


def experience_span_years(parsed: ParsedResume) -> float | None:
    """Deterministic cumulative experience from the parsed date ranges.

    Union of [start, end] intervals ("Present" -> now), overlapping roles
    counted once. Returns None when the dates are too incomplete to do the
    math (no entry with both dates, or a start with no usable end)."""
    now = datetime.now(UTC)
    today_ym = (now.year, now.month)

    intervals: list[tuple[float, float]] = []
    for entry in parsed.experience:
        start = parse_date_str(entry.start_date)
        if start is None:
            continue
        end_text = (entry.end_date or "").strip().lower()
        if end_text and end_text not in ("present", "current", "now"):
            end = parse_date_str(end_text)
            if end is None:
                continue  # unparseable end: skip rather than guess ongoing
        else:
            end = today_ym  # "Present" (or empty) means the role is ongoing
        start_month = start[0] * 12 + (start[1] - 1)  # months since year 0
        end_month = end[0] * 12 + (end[1] - 1) + 1  # ...through end of month
        if end_month < start_month:
            continue  # nonsense dates (parse typo): ignore the entry
        intervals.append((start_month, end_month))

    if not intervals:
        return None

    intervals.sort()
    merged: list[list[int]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:  # touching intervals count as continuous
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged) / 12.0


def reconciled_experience_years(parsed: ParsedResume) -> tuple[float, str]:
    """The number YoE should be ceil() of: the LLM's estimate cross-checked
    against the deterministic date math.

    The date-derived union of employment intervals is the source of truth
    whenever it is computable — it can't systematically undercount the way
    the LLM does on roles ending in "Present". The LLM estimate only fills
    gaps: when no usable date range exists, or when it is meaningfully
    higher (a date range the parser missed, e.g. "2019-2021" condensed to
    one entry)."""
    span = experience_span_years(parsed)
    estimate = parsed.total_experience_years
    if span is None:
        return estimate, "llm-estimate"
    if span > estimate + 0.25:  # LLM undercounted (typical with Present ends)
        return span, "date-math"
    if estimate > span + 0.25:  # dates missed part of the history
        return estimate, "llm-estimate"
    return span, "date-math"  # agreement: prefer the deterministic number


def extract_pdf_text(data: bytes) -> str:
    """Synchronous PDF text extraction (run via to_thread)."""
    import io

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            parts.append(text)
    return "\n\n".join(parts).strip()


async def parse_resume(
    data: bytes, provider: LLMProvider, *, log: Callable[[str], None] | None = None
) -> ParsedResume:
    """Parse resume bytes into structured data. Raises LLMError on failure."""

    def _extract() -> str:
        return extract_pdf_text(data)

    text = await asyncio.to_thread(_extract)
    text_for_prompt = f"Today's date: {datetime.now(UTC).strftime('%Y-%m-%d')}\n\n{text}"
    if len(text) < 80:
        raise LLMError(
            "Could not extract readable text from the PDF (scanned/image-only "
            "resumes are not supported)."
        )
    if log:
        log(f"Extracted {len(text):,} characters of text from PDF")

    raw = await provider.generate_json(
        system=PARSE_SYSTEM_PROMPT.format(TODAY=datetime.now(UTC).strftime("%Y-%m")),
        user=f"Resume text:\n\n{text_for_prompt[:60_000]}",
    )
    if log:
        log("LLM returned structured resume data")

    parsed = ParsedResume(
        name=str(raw.get("name", "")).strip(),
        skills=[str(s).strip() for s in raw.get("skills", []) if str(s).strip()],
        roles=[str(r).strip() for r in raw.get("roles", []) if str(r).strip()],
        experience=[
            ExperienceEntry(
                title=str(e.get("title", "")).strip(),
                company=str(e.get("company", "")).strip(),
                start_date=str(e.get("start_date", "")).strip(),
                end_date=str(e.get("end_date", "")).strip(),
            )
            for e in raw.get("experience", [])
            if isinstance(e, dict) and str(e.get("title", "")).strip()
        ],
        total_experience_years=_safe_float(raw.get("total_experience_years")),
    )
    return parsed


def yoe_from_parsed(parsed: ParsedResume) -> int:
    """YoE = ceil(reconciled experience years), floored at 0, capped at 50.

    The reconciliation prefers deterministic date math over the LLM's raw
    estimate whenever the dates support it (the LLM undercounts roles that
    run to 'Present' — it can't know what 'now' is unless told, and even
    then it drifts)."""
    years, _source = reconciled_experience_years(parsed)
    return max(0, min(50, math.ceil(years)))


def _safe_float(value: object) -> float:
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
