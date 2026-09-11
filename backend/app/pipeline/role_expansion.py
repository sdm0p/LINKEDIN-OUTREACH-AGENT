"""Stage 2 — Role expansion.

LLM widens the literal resume titles into adjacent role titles whose posts
the candidate could plausibly qualify for. Cached alongside the resume parse;
only re-runs when the resume hash changes (enforced by the caller).
"""

from ..llm import LLMError, LLMProvider
from ..models import ParsedResume

EXPAND_SYSTEM_PROMPT = """\
You are a senior technical recruiter who knows how job posts are actually
titled on LinkedIn. You receive a candidate's skills, the roles they have
held, and their years of experience.

Produce a list of role titles to search for in hiring posts. Include:
1. The candidate's literal roles.
2. Adjacent titles whose postings the candidate could plausibly qualify
   for given skill overlap — e.g. a GenAI/RAG skillset also shows up under
   "Fullstack Developer", "Backend Engineer", "SDE-2" postings, not just
   "GenAI Engineer".
3. Seniority calibrated to the years of experience (do not suggest roles
   far above or below the candidate's level).

Respond with ONLY a JSON object, no markdown, no commentary:

{"expanded_roles": ["Title One", "Title Two"]}

Between 8 and 14 titles, most relevant first, no duplicates.
"""

MAX_ROLES = 14


async def expand_roles(
    parsed: ParsedResume, my_yoe: int, provider: LLMProvider
) -> list[str]:
    """Return the expanded role list. Raises LLMError on failure."""
    if not parsed.skills and not parsed.roles:
        raise LLMError("Cannot expand roles: resume has no skills or roles parsed.")

    skills_block = "\n".join(f"- {s}" for s in parsed.skills) or "- (none listed)"
    roles_block = "\n".join(f"- {r}" for r in parsed.roles) or "- (none listed)"

    user = (
        f"Skills:\n{skills_block}\n\n"
        f"Roles held:\n{roles_block}\n\n"
        f"Years of experience: {my_yoe}"
    )

    raw = await provider.generate_json(system=EXPAND_SYSTEM_PROMPT, user=user)

    seen: set[str] = set()
    expanded: list[str] = []
    for item in raw.get("expanded_roles", []):
        title = str(item).strip()
        if title and title.lower() not in seen:
            seen.add(title.lower())
            expanded.append(title)
        if len(expanded) >= MAX_ROLES:
            break
    if not expanded:
        raise LLMError("Role expansion returned no usable titles")
    return expanded
