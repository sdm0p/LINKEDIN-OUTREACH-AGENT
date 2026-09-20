# LinkedIn Outreach Agent

A local-only, human-in-the-loop job-hunting agent. It reads your resume, works
out which roles and keywords to search for, finds LinkedIn posts from people
who are actually hiring, filters them by location and required experience, and
queues the leads for your review. Outreach drafts are written **only when you
explicitly ask for one** — and nothing is ever sent automatically.

Built for one specific workflow: a job seeker who wants a steady stream of
fresh, relevant hiring posts (the kind recruiters and founders write by hand,
not structured job listings), screened against their target countries and real
years of experience, with a review queue they action manually in their own
mail and LinkedIn clients.

## How it works

```
Resume (PDF)
   │
   ▼
[1] Resume parse ────────── skills, roles, experience, YoE (ceil)
[2] Role expansion ──────── adjacent titles worth searching, not just the literal one
[3] Keyword generation ──── ~15 search strings in two tiers (skill / title),
   │                        rotated 5 per run, least-recently-used first
   ▼
[4] LinkedIn search ─────── per keyword, human-paced, recency-filtered,
   │                        geo-scoped when target countries are set
   │                        (stickerdaniel/linkedin-mcp-server in Docker)
   ▼
[5] Dedup ────────────────── posts already processed are skipped
[6] Location filter ──────── post provably outside target countries -> dropped
   │                        (no place stated -> kept, like unstated YoE)
[7] YoE filter ───────────── "5+ years required" vs your YoE; unstated -> kept
[8] Classification ───────── LLM verdict: genuine hiring post vs noise;
   │                        email extraction (plain + obfuscated "name at gmail dot com");
   │                        location field (second net for the country filter)
[9] Contact resolution ───── email found -> email lead; none -> LinkedIn DM target
   ▼
Pending leads (review queue)          ← a run stops here. It never drafts.
   │
   │  you click "Generate draft" (per lead, or generate-all)
   ▼
[10] Draft generation ────── short, specific email or DM, daily-capped
   ▼
Review → you send it yourself, then mark it sent
```

Two hard guarantees, enforced in code rather than convention:

- **Runs never draft.** A run only *qualifies* posts into pending leads.
  Draft generation is a separate LangGraph graph whose `interrupt()` gate
  stops execution cold; the queue endpoint's `Command(resume=True)` — issued
  because *you* clicked the button — is the only thing that carries it past
  the gate.
- **Nothing sends.** No SMTP client, no LinkedIn message-sending calls, no
  scheduler, no cron. Every run is triggered by hand; every send is done by
  you, outside the app. This is a permanent design constraint, not a TODO.

## Location targeting

Searches on LinkedIn's post feed are global text searches, so geo-scoping is
built at this app's layer, in three stages that mirror the YoE filter's
philosophy (drop only on positive knowledge, never guess):

1. **Geo-scoped queries** — with target countries set, every keyword goes out
   as `"<keyword> India"` so the results skew toward the places you want.
2. **Pre-LLM hard drop** — a deterministic resolver
   (`backend/app/pipeline/location.py`) reads each post's text: labeled lines
   (`📍 Location: Bangalore, India`), city aliases (Bangalore/Bengaluru,
   Gurgaon, Dubai, London, … → country), `"hiring in Germany"`,
   `"Remote (US)"`, nationality hints (`"US only"`). If the post provably
   sits outside your targets, it's dropped before it ever costs an LLM call,
   and the run trace shows why.
3. **Post-LLM net** — the classifier also extracts the job's country against
   a closed list; any post the regexes missed but the LLM places outside the
   targets is dropped at ingest.

Posts that name **no location at all are kept** — the same rule as an
unstated YoE requirement. They land in the queue's **"Location unknown"**
bucket rather than being guessed at. Bare "Remote" posts also pass: remote
work could sit inside any target country.

Set targets from the **Run page dropdown** (single country, next to Recency)
or the **Settings page** (multi-country). Both write the same setting, which
persists in the data directory and survives restarts. Leave it empty and the
filter is fully off.

## The review queue

Leads land as `pending` with the source post attached, then:

- **Sort** by time added, or by **time the post was posted** (newest first;
  LinkedIn only exposes a relative age — "19h", "2d" — which is converted to
  an approximate timestamp at ingest; posts with no age sort last).
- **Filter by status and by country** — the country dropdown lists the
  countries actually present in your queue, plus a "Location unknown" bucket.
- **Real post links** — the Source column opens the post itself; when only
  the author's profile was available, it's labeled "profile".
- **Job details on demand** — posts with an attached LinkedIn job card
  capture the job id; a "Fetch job details" button pulls the job's location
  line, full posting text, and link (an explicit click, since it costs a
  browser navigation; posts without a job card fall back to a LinkedIn job
  search on the lead's keyword).
- **Drafts on demand** — generate per lead or generate-all (daily-capped),
  review the text, send it yourself, mark it sent.
  Statuses: `pending → new → reviewed/sent/skipped`.

Leads ingested before the location feature are **backfilled automatically**
the next time the queue loads: their stored post text is re-run through the
deterministic resolver (cheap — regex, no LLM, no network) so the country
filter works on old rows too.

## Orchestration: LangGraph

The pipeline runs on a checkpointed LangGraph workflow (`backend/app/graphs/`):

- **Run graph** — one node execution per keyword batch
  (`search_batch → ingest_batch` loops over the rotated keywords). Each batch
  is checkpointed to SQLite (`AsyncSqliteSaver`), so a failed run leaves an
  inspectable per-batch trail, and the run trace is persisted live at every
  batch boundary — the Run page shows progress *while* the pipeline executes.
- **Draft graph** — a small checkpointed graph invoked per lead on demand,
  with the `interrupt()` approval gate described above.

Node bodies stay thin: the business logic lives in ordinary services and
pipeline modules (`app/services/`, `app/pipeline/`); the graph is
orchestration only.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Orchestration | LangGraph (StateGraph + SQLite checkpointing, interrupt/resume) |
| LLM | Gemini API via `google-genai` (JSON mode, retry/backoff, Gemma-compatible) |
| Database | SQLite (WAL) — resume cache, keywords, dedup, leads/drafts, runs, checkpoints |
| Search | `stickerdaniel/linkedin-mcp-server` in Docker (headless Chromium, your own session) |
| Resume parsing | pdfplumber |
| Frontend | React 19 + TypeScript + Vite, plain CSS |
| Tooling | uv (Python), npm (Node), pytest (146 tests) |

## Getting started

Prerequisites: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 18+,
Docker, and a free Gemini API key (https://aistudio.google.com/apikey).

### Run it as an app (Docker, one command)

The whole app — API **and** dashboard — ships as a single container built in
two stages (Node compiles the frontend, the Python image serves the built
files alongside the API). This is the easiest way to run it on any machine
(Windows/macOS/Linux with Docker Desktop), and the recommended way to share it:

```bash
# 1. One-time: add your Gemini key (Settings page, or backend/.env — see Step 1 below)
# 2. Build + run:
docker compose up -d --build
# 3. Open http://localhost:8000 — dashboard served by the backend itself
```

There is no separate frontend server or port: one uvicorn process serves the
API at `/api/*` and the SPA at `/`. The port is bound to loopback only.

**Data persists across rebuilds** in the `app-data` volume (SQLite database,
stored API key, target countries). When you pull new code, re-run
`docker compose up -d --build` — then **hard-refresh the browser**
(Ctrl+Shift+R) so it doesn't keep serving the old JS bundle.

The one-time LinkedIn login (browser flow, port 6080) is documented at the
bottom of `docker-compose.yml` — it creates the host volume
`linkedin-mcp-session` that search reuses. Everything stays on the machine:
your resume, leads, and LinkedIn session never leave it.

### Step 1 — Add your Gemini API key

Get a free key at https://aistudio.google.com/apikey (**Create API key**;
keys start with `AIza`). Then choose one of:

**Option A — paste it in the app (recommended)**

1. Open http://localhost:8000 and go to **Settings**.
2. Paste the key into the API-key field and save.
3. The backend **live-verifies** the key with one tiny LLM call before
   storing — a wrong or revoked key is rejected and never saved.

The key is stored in the data directory (the `app-data` Docker volume),
survives restarts and rebuilds, is never returned by any endpoint, and can
be removed from the same page. An existing `GEMINI_API_KEY` in the
environment still works as a fallback and is never modified by the app.

**Option B — `backend/.env`**

```bash
cd backend
cp .env.example .env    # then edit: GEMINI_API_KEY=AIza...
```

In a Docker deployment, restart the stack afterwards (`docker compose up -d`)
— or just use Option A, which needs no restart at all.

### Step 2 — Log in to LinkedIn (one-time, interactive)

The app never handles your LinkedIn cookie directly; it delegates to the
MCP server's own login flow. Run this **from the host** (not inside the app
container) — it opens a remote browser viewer on loopback port 6080:

```bash
docker run -it --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp \
  -p 127.0.0.1:6080:6080 \
  stickerdaniel/linkedin-mcp-server:latest --login --login-viewer
```

1. Open the URL the command prints — **http://127.0.0.1:6080** — in your
   browser. A browser window running inside the container appears.
2. Sign in to LinkedIn as usual (complete any 2FA challenge). Wait until
   you land on your logged-in feed.
3. The session is saved automatically into the named Docker volume
   `linkedin-mcp-session` — stop the login container with **Ctrl+C** when
   done. You will not need to do this again until the session expires
   (typically weeks); if a run ever fails at the session boundary, just
   rerun the same command.

Why a named volume: Windows bind mounts break the server's
profile-creation ACL hardening. During normal runs the server is invoked
per run over stdio as
`docker run -i --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp stickerdaniel/linkedin-mcp-server:latest`.

### Step 3 — Verify and go

- **Settings → "Check session now"** (or `POST
  /api/settings/linkedin/check`): spawns the MCP container, makes one
  read-only call, and reports the result. Slow by design (container
  cold-start, up to a minute) and only ever runs on an explicit click.
  The badge should show **valid**.
- First-run checklist:
  1. **Resume** — upload your PDF (parsed once, cached by file hash).
  2. **Keywords & roles** — generate the pool, edit/pin as you like.
  3. **Run** — pick Recency and, optionally, a Location target (e.g.
     India), then **Run now** and watch the live trace.
  4. **Queue** — review pending leads, then draft when ready.

### Install with a coding agent (one prompt)

Hand this prompt to any coding agent (Codebuff, Claude Code, Cursor,
Copilot Workspace, …) opened at the repository root — it installs,
configures, and verifies the whole stack, pausing only where a human is
actually required:

```text
Set up the LinkedIn Outreach Agent in this repository and bring it fully up.

Do the following, in order, and pause where marked:

1. Check prerequisites and stop with clear instructions if any are
   missing: Docker (daemon running), Python 3.12+, uv, Node 18+.
2. If backend/.env does not exist, create it from backend/.env.example.
   Then ask me for my Gemini API key (free at
   https://aistudio.google.com/apikey) and write it into backend/.env as
   GEMINI_API_KEY=<key>. Never print the key back to me or commit it.
3. Build and start the app: docker compose up -d --build
   Then verify http://127.0.0.1:8000/api/health returns {"ok": true}.
4. PAUSE and tell me to complete the LinkedIn login myself — this step is
   interactive and cannot be automated. Give me this command to run:
     docker run -it --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp \
       -p 127.0.0.1:6080:6080 \
       stickerdaniel/linkedin-mcp-server:latest --login --login-viewer
   I will open the printed http://127.0.0.1:6080 URL in my browser, sign
   in to LinkedIn, and stop the container once the session is saved.
5. After I confirm the login, verify the session with:
     curl -s -X POST http://127.0.0.1:8000/api/settings/linkedin/check
   Report the result. If it is not "valid", diagnose using the README's
   Troubleshooting section (a stale Chromium profile lock is the usual
   cause — the fix is documented there).
6. Finally, walk me through http://localhost:8000: upload a resume on the
   Resume page, generate keywords, optionally set a location target on
   the Run page. Do NOT start a LinkedIn search without my explicit
   go-ahead, and never draft or send anything unasked.

Constraints: keep everything local (the app is loopback-only by design),
do not push to any git remote, and do not modify anything outside this
repository.
```

### Development setup

#### Backend

```bash
cd backend
cp .env.example .env        # then set GEMINI_API_KEY
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Tests: `uv run pytest` (146 tests, fake LLM, no network — safe to run freely)

#### Frontend

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to :8000
```

(For the full LinkedIn login walkthrough, see **Step 2** under Getting
started; re-login is only needed when a session expires.)

## Usage

1. **Resume page** — upload your PDF. It's parsed once and cached by file
   hash (SHA-256); re-uploading an identical file skips the LLM entirely,
   and "Re-parse" forces a fresh pass from the stored file.
2. **Keywords & roles** — generate the keyword pool from the parsed resume.
   Edit, pin (pins always go out), or remove entries; rotation covers the
   rest over roughly a week at 5 per run.
3. **Run page** — "Run now" with a **recency filter** (24h / week / month)
   and a **location dropdown** (default "All locations" = filter off). Watch
   the live per-stage trace: geo-scoping, per-batch search, and the
   location/YoE drop stages with reasons (e.g. `Dropped — Germany outside
   target (India)`). The summary counts both drop kinds.
4. **Review queue** — leads land as `pending` with the source post attached.
   Sort by posted time, filter by status/country, open the post, fetch job
   details when a job card was attached. Generate drafts when you're ready
   (per lead or generate-all; daily-capped), review the text, send it
   yourself from your mail/LinkedIn client, and mark it sent.
5. **Settings** — LLM provider/model status (set the API key here; it's
   live-verified before storing), **target countries** multi-picker,
   LinkedIn session prerequisites + manual health check, draft cap, and
   retention info (with manual purge; nothing purges itself).

## Configuration

All config lives in `backend/.env` (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Required (or set it from the Settings page, where it's verified before storing). |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Any Gemini-API model id. Rate limits are **per model**, so switching models is the quickest way onto a fresh free-tier quota. Gemma models work too (JSON mode is auto-disabled for them). |
| `MAX_DRAFTS_PER_DAY` | `20` | Cost cap, enforced at draft time. |

Target countries are **not** env config — set them in the UI (Run page or
Settings); they persist in the data directory (`target_countries.json`).

## Project structure

```
backend/
  app/
    graphs/          LangGraph: run graph, draft graph, nodes, state, trace writer
    llm/             provider-agnostic LLM interface + Gemini implementation
    pipeline/        per-stage logic: resume parse, role expansion, keywords,
                     YoE filter, location resolution/targeting,
                     extraction/classification, draft generation
    routers/         FastAPI endpoints: resume, keywords, runs, queue, settings
    search/          search-source abstraction + LinkedIn MCP implementation
    services/        orchestration glue: run_service, queue_service,
                     resume_service, keyword_service
    db.py            all SQL (SQLite); in-place migrations
  tests/             pytest suite (146 tests: unit + API + batch e2e, fake LLM)
frontend/
  src/pages/         Resume, Keywords, Run (live trace), Queue, Settings
```

## Reliability notes

- **Free-tier 429s**: transient quota errors (429/5xx) are retried with
  backoff; a failed classification or draft leaves the post unprocessed so a
  later run retries it. Per-model quota exhaustion is best fixed by switching
  `GEMINI_MODEL`.
- **Search failures** fail the run loudly (the browser session is the shared
  bottleneck); everything downstream of search fails soft, per batch.
- **Cookie expiry**: hard failures stop the run at the session boundary;
  the Settings page surfaces session prerequisites rather than relying on
  silent log-watching.
- **Degraded responses**: search results that parse but look wrong are
  flagged in the trace for manual review.
- **Schema migrations** run in place at startup (`ALTER TABLE` for columns
  added after your database was created) — existing rows keep their data;
  new columns fill in as new runs happen.

## Troubleshooting

**Browser shows an old UI after updating** — the backend serves the frontend
bundle that was baked into the image. Rebuild (`docker compose up -d
--build`) and hard-refresh the browser (Ctrl+Shift+R).

**Search fails with "The profile appears to be in use by another Chromium
process … on another computer"** — a stale profile lock survived a container
restart. Clear it (safe while no search is running):

```bash
docker run --rm -v linkedin-mcp-session:/s alpine \
  sh -c "rm -f /s/Singleton* ; ls /s | head"
```

Then re-check the session from Settings.

**Location dropdown is empty / filter seems off** — the dropdown lists what
`GET /api/settings` reports; if it lacks `location_targets`, the running
server predates the feature (rebuild the image). Old queue rows are
backfilled with countries the next time the queue loads.

**A post stayed in the queue that's clearly not in my country** — the filter
only drops posts whose location is *provably* outside your targets. A post
that says "great opportunity, DM me" with no place at all is kept by design.
Use the country filter to group it, or skip it manually.

## Status

Working end-to-end: resume → keywords → geo-scoped search → location/YoE
qualification → pending leads → on-demand drafts → review queue with
posted-time sort, country filter, post permalinks, and job-detail fetching.
See `linkedin-outreach-agent-plan.md` for the design rationale and open
decisions (multi-source search, hosted dashboard split, local-model provider).

## Ethics & terms

This tool reads only content you can already see while logged into LinkedIn,
at human-paced manual runs, and sends nothing by itself. You are responsible
for complying with LinkedIn's terms and the laws of your jurisdiction — use
it the way it's built: slowly, manually, and for your own job search.
