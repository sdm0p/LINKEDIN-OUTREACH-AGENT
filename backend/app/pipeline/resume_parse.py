"""Stage 1 — Resume parse.

Extracts skills, roles, and the experience section from the resume PDF via
the LLM; YoE is ceil() of the LLM's total-experience estimate. Callers cache
the result keyed by file hash (see services/resume_service.py).
"""

import asyncio
import math
from collections.abc import Callable

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
    if len(text) < 80:
        raise LLMError(
            "Could not extract readable text from the PDF (scanned/image-only "
            "resumes are not supported)."
        )
    if log:
        log(f"Extracted {len(text):,} characters of text from PDF")

    raw = await provider.generate_json(
        system=PARSE_SYSTEM_PROMPT,
        user=f"Resume text:\n\n{text[:60_000]}",
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
    """YoE = ceil(total experience years), floored at 0, capped at 50."""
    return max(0, min(50, math.ceil(parsed.total_experience_years)))


def _safe_float(value: object) -> float:
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
