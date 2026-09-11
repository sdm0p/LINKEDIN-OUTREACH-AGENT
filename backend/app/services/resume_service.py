"""Service glue for the resume stages: caching by file hash, the two-phase
parse/expand flow, and the reparse path (raw PDF kept on disk so a forced
re-parse never needs a re-upload)."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from ..config import settings
from ..db import connect, get_resume_cache, init_db, save_resume_cache
from ..llm import get_llm_provider
from ..models import ExperienceEntry, ParsedResume, ResumeState
from ..pipeline import resume_parse, role_expansion
from ..run_trace import RunTrace

_db_lock = asyncio.Lock()

RESUME_FILE_NAME = "resume.pdf"  # stored copy for re-parse; never the cache key


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _db_path():
    return settings.data_dir / "app.db"


def _resume_path():
    return settings.data_dir / RESUME_FILE_NAME


def _conn():
    return connect(_db_path())


def _ensure_db():
    init_db(_db_path())


def _row_to_state(row, cached: bool) -> ResumeState:
    parsed = json.loads(row["parsed_json"]) if row["parsed_json"] else None
    expanded = (
        json.loads(row["roles_expanded_json"]) if row["roles_expanded_json"] else None
    )
    state = ResumeState(
        has_resume=True,
        file_name=row["file_name"],
        file_size=row["file_size"],
        file_hash=row["file_hash"],
        parsed_at=row["parsed_at"],
        expanded_at=row["expanded_at"],
        cached=cached,
        roles_expanded=expanded,
    )
    if parsed:
        state.candidate_name = str(parsed.get("name", ""))
        state.skills = parsed.get("skills", [])
        state.roles = parsed.get("roles", [])
        state.experience = [ExperienceEntry(**e) for e in parsed.get("experience", [])]
        state.total_experience_years = parsed.get("total_experience_years", 0.0)
        state.my_yoe = row["my_yoe"] or 0
    return state


async def get_resume_state() -> ResumeState:
    """Current cached state; has_resume=False when nothing is stored yet."""
    async with _db_lock:
        return await get_resume_state_assuming_lock()


async def get_resume_state_assuming_lock() -> ResumeState:
    """For callers that already hold _db_lock (asyncio.Lock is not
    reentrant — calling get_resume_state from inside it would deadlock)."""
    _ensure_db()
    row = get_resume_cache(_conn())
    return _row_to_state(row, cached=True) if row else ResumeState(has_resume=False)


async def _parse_and_expand(
    data: bytes,
    file_name: str,
    file_hash: str,
    trace: RunTrace | None,
) -> ResumeState:
    """Parse -> expand -> persist. Caller must hold _db_lock."""

    def _log(stage: str, detail: str) -> None:
        if trace:
            trace.log(stage, detail)

    provider = get_llm_provider()

    _log("resume-parse", f"Parsing {file_name} ({len(data):,} bytes)...")
    parsed: ParsedResume = await resume_parse.parse_resume(
        data, provider, log=lambda d: _log("resume-parse", d)
    )
    my_yoe = resume_parse.yoe_from_parsed(parsed)
    _log(
        "resume-parse",
        f"Derived YoE: {my_yoe} (ceil of {parsed.total_experience_years})",
    )
    _log("resume-parse", f"{len(parsed.skills)} skills, {len(parsed.roles)} roles extracted")

    _log("role-expansion", "Expanding roles via LLM...")
    expanded = await role_expansion.expand_roles(parsed, my_yoe, provider)
    _log("role-expansion", f"{len(expanded)} roles generated")
    _log("role-expansion", "Cached parsed resume + expanded roles")

    conn = _conn()
    try:
        save_resume_cache(
            conn,
            {
                "file_hash": file_hash,
                "file_name": file_name,
                "file_size": len(data),
                "parsed_json": parsed.model_dump_json(),
                "my_yoe": my_yoe,
                "roles_expanded_json": json.dumps(expanded),
                "parsed_at": _now(),
                "expanded_at": _now(),
            },
        )
    finally:
        conn.close()

    return _row_to_state(get_resume_cache(_conn()), cached=False)


async def upload_resume(
    data: bytes, file_name: str, trace: RunTrace | None = None
) -> ResumeState:
    """Store/replace the resume and run parse+expand — from cache on a hash
    match, fresh LLM work otherwise."""
    file_hash = hashlib.sha256(data).hexdigest()

    async with _db_lock:
        _ensure_db()
        row = get_resume_cache(_conn())

        if row and row["file_hash"] == file_hash and row["parsed_json"]:
            # Cache hit: identical resume, skip LLM entirely.
            if trace:
                trace.log("resume-parse", "Resume hash matches cache — skipping parse")
                trace.log(
                    "role-expansion", "Cached alongside resume — skipping expansion"
                )
            return _row_to_state(row, cached=True)

        if trace:
            trace.log("resume-parse", "New or changed resume — full parse required")
        # Keep raw bytes so Reparse works without another upload.
        _resume_path().write_bytes(data)
        return await _parse_and_expand(data, file_name, file_hash, trace)


async def reparse_resume(trace: RunTrace | None = None) -> ResumeState:
    """Force a fresh parse+expand of the stored resume, bypassing the cache."""
    async with _db_lock:
        _ensure_db()
        row = get_resume_cache(_conn())
        path = _resume_path()
        if not row or not path.exists():
            raise ValueError("No resume on file to re-parse.")

        data = path.read_bytes()
        if trace:
            trace.log("resume-parse", "Forced re-parse requested — bypassing cache")
        return await _parse_and_expand(data, row["file_name"], row["file_hash"], trace)


def validate_pdf(data: bytes, file_name: str) -> str | None:
    """Return an error message if the file is not an acceptable PDF."""
    if not file_name.lower().endswith(".pdf"):
        return "Only PDF resumes are supported."
    if not data.startswith(b"%PDF"):
        return "That file does not look like a valid PDF."
    if len(data) > 10 * 1024 * 1024:
        return "PDF is larger than 10 MB."
    return None
