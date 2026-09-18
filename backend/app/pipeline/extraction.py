"""Stage 7 — Extraction and classification.

Per kept post:
1. Regex pass for gmail addresses (plain and obfuscated forms like
   "name at gmail dot com").
2. LLM pass confirming the post is a genuine hiring post (not a repost,
   rant, or unrelated gmail mention).
3. No email found -> the caller falls back to search_people for a DM target
   (this module exposes the parsed intent data for that).
"""

import re

from ..llm import LLMProvider
from ..search.base import Post

CLASSIFY_SYSTEM_PROMPT = """\
You classify LinkedIn posts for a job seeker looking for hiring posts.

You receive one post's text (and the author's headline). Decide:

- "verdict": one of
  - "hiring" — a genuine post from someone hiring for a role (recruiter,
    founder, team lead announcing a position and how to apply)
  - "noise" — anything else: reposts without their own hiring intent,
    rants, layoff announcements, "looking for a job" posts, generic
    content that merely mentions an email address, courses/promos
- "company": the hiring company's name if stated, else ""
- "role": the role being hired for if stated, else ""
- "emails": every email address visible in the post text, including
  obfuscated forms like "name at gmail dot com" or "name [at] gmail
  [dot] com". Empty list when none.
- "location": the job's location as the country name, when the post
  states or implies one — use the country, not the city (e.g. "India",
  "United States", "Germany"). "Remote" when the post says remote/
  work-from-home without tying it to a country. "" when the post names
  no location at all.

Respond with ONLY a JSON object, no markdown, no commentary:

{"verdict": "hiring", "company": "", "role": "", "emails": [], "location": ""}
"""

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Obfuscated forms: "name at gmail dot com", "name [at] gmail [dot] com".
_OBFUSCATED_RE = re.compile(
    r"([a-zA-Z0-9._%+-]+)\s*(?:\[?at\]?|\(at\))\s*(gmail|gmail\.|googlemail)\s*"
    r"(?:\[?dot\]?|\(dot\))\s*(?:com)?",
    re.IGNORECASE,
)


def extract_emails_regex(text: str) -> list[str]:
    """Plain addresses first; obfuscated gmail forms reconstructed."""
    emails = [m.group(0).lower() for m in _EMAIL_RE.finditer(text)]
    for m in _OBFUSCATED_RE.finditer(text):
        local, domain = m.group(1).lower(), m.group(2).lower().rstrip(".")
        email = f"{local}@{domain}.com"
        if email not in emails:
            emails.append(email)
    # Dedup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for e in emails:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


async def classify_post(
    post: Post, provider: LLMProvider
) -> dict:
    """LLM verdict + company/role + emails. Raises LLMError on failure."""
    user = f"Author headline: {post.author_headline or '(none)'}\n\nPost text:\n{post.text[:4000]}"
    raw = await provider.generate_json(system=CLASSIFY_SYSTEM_PROMPT, user=user)

    verdict = str(raw.get("verdict", "noise")).strip().lower()
    if verdict not in ("hiring", "noise"):
        verdict = "noise"
    emails = [str(e).strip().lower() for e in raw.get("emails", []) if str(e).strip()]
    regex_emails = extract_emails_regex(post.text)
    # Regex catches what the LLM missed; LLM catches obfuscations regex missed.
    merged = list(dict.fromkeys(regex_emails + emails))
    return {
        "verdict": verdict,
        "company": str(raw.get("company", "")).strip(),
        "role": str(raw.get("role", "")).strip(),
        "emails": merged,
        "location": str(raw.get("location", "")).strip(),
    }
