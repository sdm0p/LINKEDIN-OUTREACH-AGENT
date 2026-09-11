# LinkedIn Outreach Agent

A local-only, human-in-the-loop job-hunting agent. It reads your resume, works
out which roles and keywords to search for, finds LinkedIn posts from people
who are actually hiring, filters them by required experience, and queues the
leads for your review. Outreach drafts are written **only when you explicitly
ask for one** — and nothing is ever sent automatically.

Built for one specific workflow: a job seeker who wants a steady stream of
fresh, relevant hiring posts (the kind recruiters and founders write by hand,
not structured job listings), screened against their real years of
experience, with a review queue they action manually in their own mail and
LinkedIn clients.

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
[4] LinkedIn search ─────── per keyword, human-paced, recency-filtered
   │                        (stickerdaniel/linkedin-mcp-server in Docker)
   ▼
[5] Dedup ────────────────── posts already processed are skipped
[6] YoE filter ───────────── "5+ years required" vs your YoE; unstated -> kept
[7] Classification ───────── LLM verdict: genuine hiring post vs noise;
   │                        email extraction (plain + obfuscated "name at gmail dot com")
[8] Contact resolution ───── email found -> email lead; none -> LinkedIn DM target
   ▼
Pending leads (review queue)          ← a run stops here. It never drafts.
   │
   │  you click "Generate draft" (per lead, or generate-all)
   ▼
[9] Draft generation ────── short, specific email or DM, daily-capped
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
| Tooling | uv (Python), npm (Node), pytest (66 tests) |

## Getting started

Prerequisites: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 18+,
Docker, and a free Gemini API key (https://aistudio.google.com/apikey).

### Backend

```bash
cd backend
cp .env.example .env        # then set GEMINI_API_KEY
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Tests: `uv run pytest`

### Frontend

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to :8000
```

### LinkedIn session (one-time)

The app never handles your LinkedIn cookie directly; it delegates to the
MCP server's own login flow:

```bash
docker run -it --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp \
  -p 127.0.0.1:6080:6080 \
  stickerdaniel/linkedin-mcp-server:latest --login --login-viewer
```

Open the printed loopback URL and sign in. The session lives in the named
Docker volume `linkedin-mcp-session` (Windows bind mounts break the server's
profile-creation ACL hardening). During runs the server is invoked per run
over stdio as
`docker run -i --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp stickerdaniel/linkedin-mcp-server:latest`.

## Usage

1. **Resume page** — upload your PDF. It's parsed once and cached by file
   hash (SHA-256); re-uploading an identical file skips the LLM entirely,
   and "Re-parse" forces a fresh pass from the stored file.
2. **Keywords & roles** — generate the keyword pool from the parsed resume.
   Edit, pin (pins always go out), or remove entries; rotation covers the
   rest over roughly a week at 5 per run.
3. **Run page** — "Run now" with a recency filter (24h / week / month) and
   watch the live per-stage trace as each keyword batch executes.
4. **Review queue** — leads land as `pending` with the source post attached.
   Generate drafts when you're ready (per lead or generate-all; daily-capped),
   review the text, send it yourself from your mail/LinkedIn client, and mark
   it sent. Statuses: `pending → new → reviewed/sent/skipped`.
5. **Settings** — LLM provider/model status, LinkedIn session prerequisites,
   draft cap, and retention info (with manual purge; nothing purges itself).

## Configuration

All config lives in `backend/.env` (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Required. Free key from Google AI Studio. |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Any Gemini-API model id. Rate limits are **per model**, so switching models is the quickest way onto a fresh free-tier quota. Gemma models work too (JSON mode is auto-disabled for them). |
| `MAX_DRAFTS_PER_DAY` | `20` | Cost cap, enforced at draft time. |

## Project structure

```
backend/
  app/
    graphs/          LangGraph: run graph, draft graph, nodes, state, trace writer
    llm/             provider-agnostic LLM interface + Gemini implementation
    pipeline/        per-stage logic: resume parse, role expansion, keywords,
                     YoE filter, extraction/classification, draft generation
    routers/         FastAPI endpoints: resume, keywords, runs, queue, settings
    search/          search-source abstraction + LinkedIn MCP implementation
    services/        orchestration glue: run_service, queue_service,
                     resume_service, keyword_service
    db.py            all SQL (SQLite); in-place migrations
  tests/             pytest suite (unit + API, fake LLM — no network)
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

## Status

Working end-to-end: resume → keywords → search → qualify → pending leads →
on-demand drafts → review queue. See `linkedin-outreach-agent-plan.md` for
the design rationale and open decisions (multi-source search, hosted
dashboard split, local-model provider).

## Ethics & terms

This tool reads only content you can already see while logged into LinkedIn,
at human-paced manual runs, and sends nothing by itself. You are responsible
for complying with LinkedIn's terms and the laws of your jurisdiction — use
it the way it's built: slowly, manually, and for your own job search.
