"""Parse-sanity gate (playwright-source-design.md §7).

Dedup is forever: once a post_id lands in processed_posts, no run ever
looks at that post again. So a selector bug that silently empties a
post's fields would poison it permanently — never retried, and the only
visible symptom would be a queue that quietly shrinks.

The gate is the quality-check station before the stamp: a cheap, purely
local check that an extraction looks complete and believable. It runs
BEFORE dedup in ingest; failures are skipped loudly and simply retried
by the next run (they were never marked seen). No LLM, no network —
a few length checks that convert "silent permanent loss" into "loud
retryable failure".

Tuned so real hiring posts never trip it: the floor sits well below any
genuine post yet above every mangled shape (empty text, a stub, a UI
fragment). A missing author line is the other hard signal. Residual
risk — a real post whose text extracted as a short fragment — is what
the run-summary uniformity canary and the DLQ's raw HTML are for; the
gate is the cheap first line, not the whole safety net.
"""

from dataclasses import dataclass, field

from ..search.base import Post

MIN_TEXT_CHARS = 20  # catches empty/stub extractions; terse-but-real posts (>20c) pass
MIN_WORDS = 3
MAX_TEXT_CHARS = 20_000  # a real post is never this long; this is parser-runaway


@dataclass
class SanityReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_post(post: Post) -> SanityReport:
    """Return the gate verdict for one extracted post. `ok=False` means
    the extraction looks broken: do not process, do not mark seen."""
    reasons: list[str] = []

    text = (post.text or "").strip()
    if len(text) < MIN_TEXT_CHARS:
        reasons.append(f"text under {MIN_TEXT_CHARS} chars")
    elif len(text.split()) < MIN_WORDS:
        reasons.append(f"text under {MIN_WORDS} words")
    elif len(post.text) > MAX_TEXT_CHARS:
        reasons.append(f"text over {MAX_TEXT_CHARS} chars (parser runaway)")

    # An empty id is proof the parser emitted nothing; dedup would treat
    # every such post as the same row. A merely short id is NOT evidence of
    # breakage by itself — uniform-id anomalies belong to the run-summary
    # canary, not a per-post drop.
    if not (post.post_id or "").strip():
        reasons.append("post_id missing")

    if not (post.author_name or "").strip() and not (post.author_headline or "").strip():
        reasons.append("no author name or headline")

    return SanityReport(ok=not reasons, reasons=reasons)
