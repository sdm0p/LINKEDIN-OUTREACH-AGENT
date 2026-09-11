"""Stage 3 — Keyword generation.

LLM builds a pool of ~15 search keyword combinations in two tiers:
- skill-anchored (primary, higher recall): "hiring LangChain"
- title-anchored (backup): "hiring fullstack GenAI"

Pinned keywords survive regeneration; rotation picks a subset per run
(see services/keyword_service.py).
"""

from ..llm import LLMError, LLMProvider

GENERATE_SYSTEM_PROMPT = """\
You build LinkedIn "Posts" search keywords for finding hiring managers'
posts. You receive a candidate's expanded role titles and their skills.

Build a pool of 15 search keyword strings in two tiers:

- "skill" tier (8 keywords, primary, higher recall): skill-anchored
  searches that catch posts by technology, e.g. "hiring LangChain",
  "looking for RAG pipeline experience", "hiring agentic AI",
  "looking for FastAPI developer". Prioritize the candidate's strongest,
  most in-demand skills.
- "title" tier (7 keywords, backup): title-anchored searches, e.g.
  "hiring fullstack GenAI", "hiring Java fullstack AI engineer",
  "we are hiring Backend Engineer".

Rules:
- Short, natural search strings (2-5 words), the way hiring posts are
  actually written. Vary phrasing: "hiring X", "looking for X",
  "we are hiring X", "need X developer".
- No generic strings that drown results ("hiring", "job opening").
- Calibrate seniority to the candidate's experience.
- Lowercase as written in posts; no hashtags.

Respond with ONLY a JSON object, no markdown, no commentary:

{"skill_keywords": ["..."], "title_keywords": ["..."]}
"""


async def generate_keywords(
    expanded_roles: list[str], skills: list[str], provider: LLMProvider
) -> dict[str, list[str]]:
    """Return {"skill": [...], "title": [...]}. Raises LLMError on failure."""
    if not expanded_roles and not skills:
        raise LLMError("Cannot generate keywords: no roles or skills available.")

    roles_block = "\n".join(f"- {r}" for r in expanded_roles) or "- (none)"
    skills_block = "\n".join(f"- {s}" for s in skills) or "- (none)"

    user = (
        f"Expanded role titles:\n{roles_block}\n\n"
        f"Skills:\n{skills_block}\n\n"
        f"Generate the keyword pool."
    )

    raw = await provider.generate_json(system=GENERATE_SYSTEM_PROMPT, user=user)

    def _clean(items: object) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items if isinstance(items, list) else []:
            text = str(item).strip().strip('"')
            if text and text.lower() not in seen and len(text) <= 80:
                seen.add(text.lower())
                out.append(text)
        return out

    skill_kws = _clean(raw.get("skill_keywords"))
    title_kws = _clean(raw.get("title_keywords"))
    if not skill_kws and not title_kws:
        raise LLMError("Keyword generation returned no usable keywords")
    return {"skill": skill_kws, "title": title_kws}
