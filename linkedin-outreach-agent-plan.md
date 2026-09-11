# LinkedIn hiring-lead pipeline — plan

## Goal

An agent that takes my resume, figures out which roles/keywords to search
for, finds LinkedIn posts from people hiring (not structured job listings),
filters them by required experience, and drafts outreach (email or LinkedIn
DM) for review. Nothing sends automatically — I review and send myself.

## Pipeline stages

1. **Resume parse (cached)**
   - Extract skills, roles, and experience section.
   - Compute YoE from experience section, round up with `ceil()`
     (e.g. 1.11 years -> 2).
   - Hash the resume file. On each run, compare hash to cache:
     - Match -> skip re-parsing, load cached values.
     - Mismatch (resume updated) -> re-parse and overwrite cache.

2. **Role expansion (cached alongside resume parse)**
   - LLM takes parsed skills/experience and generates a broader role list
     beyond the literal title -- e.g. a GenAI/RAG skillset now shows up
     under "Fullstack Developer", "Backend Engineer", "SDE-2" postings too,
     not just "GenAI Engineer".
   - Only re-runs when the resume hash changes.

3. **Keyword generation**
   - Two tiers:
     - Skill-anchored (primary, higher recall): "hiring LangChain",
       "looking for RAG pipeline experience", "hiring agentic AI"
     - Title-anchored (backup): "hiring fullstack GenAI",
       "hiring Java fullstack AI engineer"
   - Pool of ~15 combinations; rotate 4-6 per run to keep search volume low
     and human-paced.
   - "Last used" tracked per keyword so coverage over a week is visible.

4. **Search**
   - LinkedIn access via `stickerdaniel/linkedin-mcp-server` (self-hosted
     Docker, authenticated with my own `li_at` session cookie, headless
     Chromium browser automation).
   - Actual tool used: `search_posts` (global keyword post search, "Posts"
     tab) with a recency filter -- past-24h / past-week / past-month,
     exposed as a UI control, default past-24h.
   - Other available tools on this server: `search_jobs`, `get_saved_jobs`,
     `search_people`, `get_job_details`, `get_feed`, `close_session`.
     `search_people` is a planned fallback (see stage 6).
   - Pace calls with a few seconds between each keyword, even for manual
     runs, to reduce behavioral-detection signal.
   - First real run should be a validation pass: run one keyword, inspect
     the raw response, confirm actual field names before trusting
     downstream parsing.

5. **Dedup**
   - Local store (SQLite, or Supabase Postgres if the dashboard is split
     out -- see Hosting) tracks post IDs already processed, plus
     company/role-family to avoid near-duplicate outreach across multiple
     posts from the same company in a short window.
   - Retention: dashboard shows total dedup entries, oldest entry age,
     with a manual purge action -- no silent auto-purge.

6. **YoE filter**
   - Per post, extract required YoE from post text.
   - Not stated -> treat as 0 (so it always passes the check, never
     excluded for being unstated).
   - Keep the post if `required_min_yoe <= my_yoe`.

7. **Extraction / classification**
   - Regex pass for gmail addresses (plain + obfuscated forms like
     "name at gmail dot com").
   - LLM pass confirms the post is a genuine hiring post, not noise
     (a repost, a rant, an unrelated gmail mention).
   - If no email found -> fall back to `search_people` on the poster's
     name/company to resolve a profile for a DM instead of dropping the
     lead.

8. **Draft generation**
   - Email found -> draft a personalized outreach email.
   - No email -> draft a LinkedIn DM, tagged with the poster's profile URL.
   - Drafts carry a status: new / reviewed / sent / skipped, persisted so
     re-viewing the queue doesn't resurface already-handled items.

9. **Output**
   - Consolidated list: role, company, poster's LinkedIn profile, contact
     method (email/DM), draft text, source post link.
   - Never auto-sent. Reviewed and sent manually.

## Dashboard / UI

Not a generic Streamlit prototype -- a proper small app (FastAPI backend +
React frontend), restrained styling (1-2 accent colors, real type
hierarchy, no emoji-as-icons, consistent spacing), table-dense review
layout rather than a form-with-big-button look.

Pages:
- **Resume** -- upload/replace, view parsed skills/roles/YoE, last-parsed
  timestamp, re-parse button.
- **Keywords & roles** -- view generated pool, manually edit/pin/remove,
  last-used tracking per keyword.
- **Run** -- "Run now" button, recency filter control (24h/week/month),
  live chain-of-thought trace as it executes (see below), last-run
  summary.
- **Review queue** -- table: company, role, contact method, YoE match,
  draft preview, source link, mark-as-sent action.
- **Settings** -- LinkedIn cookie status (valid/expiring/expired), LLM
  provider in use, data retention controls.

### Chain-of-thought trace (shown live during a run)

Collapsible, per-stage, populated as the run progresses:

| Stage | Shown |
|---|---|
| Resume parse | Extracted skills/roles/YoE, cached-or-fresh badge |
| Role expansion | Generated role list |
| Keyword generation | Keywords used this run, tier tag |
| Search (per keyword) | Raw hit count from `search_posts` |
| YoE filter | Per post: required YoE detected, kept/dropped |
| Extraction | Per post: email found / DM fallback, hiring-post verdict |
| Draft | Final draft, linked to source post |

## Reliability

- **Cookie expiry detection**: `li_at` expires ~every 30 days.
  - Hard failure (session init throws) -> caught at the MCP session
    boundary, logged, run stops immediately.
  - Soft failure (session technically alive but LinkedIn throws a
    checkpoint/captcha) -> tool calls return empty/garbage; add a canary
    check on the first response to distinguish "genuinely no results"
    from "response didn't parse as expected."
  - Surface cookie status directly on the Settings page rather than
    relying on silent log-watching.
- **Cost cap**: max-drafts-per-day limit so a noisy keyword day doesn't
  run away on LLM calls.
- **Multi-source abstraction**: the search step sits behind a plain
  interface (`search_hiring_listings(source, keywords)`) from the start,
  so adding Naukri/Indeed later doesn't touch the rest of the pipeline.

## Cost

- Self-hosted `linkedin-mcp-server` (Docker) -- free.
- SQLite/local dedup store -- free.
- Cron/scheduler -- explicitly not used; every run is manual, triggered
  from the dashboard's "Run now" button.
- Sending -- manual, from whatever email/LinkedIn client I normally use.
  No SMTP wired into the app.
- LLM calls (role expansion, classification, drafting) -- currently the
  Anthropic API; a local model via Ollama (already have it installed) is
  the pending swap to bring this to zero cost. **Not yet decided/built.**

## Hosting

Free hosting (Render / HF Spaces) is fine for the dashboard, but not for
the scraping worker:

- Free tiers have ephemeral storage -- the dedup store and draft queue
  would reset on redeploy unless backed by external persistent storage.
- Free tier resource limits struggle to run headless Chromium reliably.
- Putting the live `li_at` session cookie on a public host is a real
  security exposure -- if that host is compromised, so is the LinkedIn
  account.

**Planned split**:
- **Dashboard/review UI** (FastAPI + React, no cookie, no browser) hosted
  free on Render -- safe to expose.
- **Scraping worker** (MCP server + cookie + Chromium) stays local-only,
  run manually from my own machine.
- Both read/write a shared **Supabase free-tier Postgres** database, so
  the hosted dashboard can show results without ever touching the
  LinkedIn session itself.

## Open decisions

- [ ] LLM provider: Anthropic API (current) vs local Ollama model (free) --
      not yet built either way.
- [ ] Exact `search_posts` response schema -- needs a live validation
      call before the extraction/YoE-parsing code can be trusted.
- [ ] Whether the local-only worker + hosted dashboard split is worth the
      Supabase dependency, vs keeping everything local with no hosting.

---

#