"""Stage 8 — Draft generation.

Email found -> personalized outreach email. No email -> LinkedIn DM.
Drafts are only drafted: nothing here sends anything, ever. The daily cap
(max_drafts_per_day) is enforced by the caller before invoking this stage.
"""

from ..llm import LLMProvider
from ..models import ResumeState
from ..search.base import Post

DRAFT_SYSTEM_PROMPT = """\
You write short, specific outreach messages from a job candidate to
someone who posted a hiring message on LinkedIn.

You receive the candidate's profile summary and one hiring post. Write
either an EMAIL or a LINKEDIN DM as instructed.

Rules:
- Under 150 words. No filler, no "I hope this finds you well".
- Reference the specific role and one specific requirement from the post.
- Connect ONE or TWO of the candidate's actual skills to that requirement.
- Confident, plain, professional. No emoji, no exclamation marks.
- Sign off with the candidate's name when provided. When no name is
  provided, end after the final sentence — never invent a name.
- Subject line for emails, none for DMs.

Respond with ONLY a JSON object, no markdown:

Email:
{"subject": "...", "body": "..."}

DM:
{"subject": "", "body": "..."}
"""


def _candidate_summary(state: ResumeState) -> str:
    skills = ", ".join(state.skills[:12]) or "(not parsed)"
    roles = ", ".join(state.roles[:5]) or "(not parsed)"
    name = f"Candidate name: {state.candidate_name}.\n" if state.candidate_name else ""
    return (
        f"{name}"
        f"Candidate profile: {roles}. {state.my_yoe} years of experience. "
        f"Key skills: {skills}."
    )


async def generate_draft(
    post: Post,
    *,
    contact_method: str,
    company: str,
    role: str,
    state: ResumeState,
    provider: LLMProvider,
) -> dict:
    """Return {"subject": str, "body": str}. Raises LLMError on failure."""
    kind = "EMAIL" if contact_method == "email" else "LINKEDIN DM"
    user = (
        f"{_candidate_summary(state)}\n\n"
        f"Write a {kind}.\n"
        f"Hiring company: {company or '(not stated)'}\n"
        f"Role: {role or '(not stated in post)'}\n"
        f"Posted by: {post.author_name} ({post.author_headline or 'headline unavailable'})\n\n"
        f"Post text:\n{post.text[:3500]}"
    )
    raw = await provider.generate_json(system=DRAFT_SYSTEM_PROMPT, user=user)
    body = str(raw.get("body", "")).strip()
    if not body:
        raise ValueError("Draft generation returned an empty body")
    return {"subject": str(raw.get("subject", "")).strip(), "body": body}
