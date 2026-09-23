# PlaywrightSource — design doc

Status: **proposal** (branch `playwright-search-source`, written before any code).
Evidence base: live probes from Sept 2026 (3-post ceiling, ignored `max_pages`,
session-volume conflicts — see git history and the MCP version survey).

## 1. Problem

`LinkedInMCPSource` (stickerdaniel/linkedin-mcp-server, spawned per run) has
three proven failures:

1. **~3 posts per keyword, hard-capped.** `search_posts` returns the first
   rendered batch; `max_pages` is advertised by its schema but ignored
   (live A/B: `max_pages=10` → same 3 posts, no scrolling occurred).
2. **Session conflicts.** Run, session-check, and login are three separate
   containers contending for the `linkedin-mcp-session` volume; stale
   `Singleton` locks after crashes are a documented failure mode.
3. **No location parameter** at the search seam.

## 2. Goal and non-goals

**Goal:** a `SearchSource` implementation that drives the user's own
logged-in LinkedIn in a real (Playwright-controlled) browser, scrolls past
the 3-post ceiling, and slots in behind `get_linkedin_source()` with nothing
outside `backend/app/search/` changing.

**Non-goals / hard constraints (unchanged from the README's guarantees):**
- Read-only: no messaging, no connections, no `send_message` equivalent.
- Human-in-the-loop: runs stay manual, pacing stays in `search_batch`
  (3–6 s between keyword batches — lives **above** the source, untouched).
- Local-only: browser and profile never leave the machine.
- ToS posture: identical in kind to what the app already does (browser
  automation of the user's own session). This is a *contract* risk, not a
  new one — see §7 for the account-level decision.

## 3. Architecture: Playwright-primary, MCP as capability fallback

**Direction (decided): the app moves to Playwright automation outright.**
MCP is not a co-equal alternative — it is retained only for the specific
capabilities Playwright has not implemented yet (see §8), and is fully
removable once those are native.

`get_linkedin_source()` (`search/base.py`) keeps returning a source under
`SOURCE_NAME = "linkedin"` so **every caller stays untouched** (nodes,
queue_service, settings, runs). The factory gains an env switch:

```
SEARCH_SOURCE=playwright # the destination default (after the merge gate)
SEARCH_SOURCE=mcp        # emergency rollback only
```

- Import stays lazy (importing `search.base` must not require playwright).
- `settings.search_source` in `config.py`; `routers/settings.py` already
  exposes `search_source.{name, available_sources}` — extend the list and
  surface "fallback ready" (docker + MCP session volume present).
- Until the browser runs inside the Docker image, the code default stays
  `mcp` and Playwright is enabled via env on the branch — flipping the
  default is part of the merge gate, not a separate decision.

## 4. Session: persistent context, one-time login

- Profile directory: `data_dir/linkedin-pw-profile/` (NOT the MCP volume —
  that is the MCP container's Chromium profile format; sharing is undefined).
- One-time login (mirrors today's port-6080 UX, minus a container):
  `uv run python -m app.search.pw_login` →
  `launch_persistent_context(user_data_dir=…, headless=False)` → user signs
  in → close. Cookies persist in the profile dir.
- **One browser process owns one profile.** Health checks (`check_health`)
  open a tab in the same browser — the Settings badge then tests the exact
  browser searches will use. Session conflict class is ended, not mitigated.

## 5. Selectors (provisional — validated by the prototype before merge)

All selectors live in one `SELECTORS` dict at the top of the module; the
prototype's first deliverable is confirming/replacing each entry:

| Extract → `Post` field | Candidate selector | MCP today |
|---|---|---|
| Post card | `div.feed-shared-update-v2` | text-blob split on "Feed post" |
| `text` | `.update-components-text` within card | guessed line ranges |
| `author_name` / `author_headline` | card's actor block | first lines |
| `author_profile_url` | `a[href^="/in/"]` in card | name→URL map from refs |
| `post_url` | card's timestamp/kebab-menu permalink `href` | **positional zip** (misaligns) |
| `job_id` / `job_url` | `a[href*="/jobs/view/"]` in card | positional zip |
| `posted_at` | relative-age string → existing `_parse_posted_at` | same, reused |

Per-card DOM extraction is structurally immune to the positional-zip
misalignment that drops permalinks today. `post_id` stays `sha256(body)[:24]`
(dedup semantics unchanged).

**Canary:** after `wait_for` on the first card, `cards.count()` vs parsed
posts must match; 0 cards on a keyword LinkedIn certainly has results for,
or count≠parsed, ⇒ `degraded=True` (existing `SearchResult` flag → trace).
On degraded, save the page HTML to `data_dir/debug/` (bounded: newest 10)
so selector rot is diagnosed offline.

## 6. Scroll strategy (the ceiling fix)

```
for _ in range(MAX_SCROLLS):            # MAX_SCROLLS ~ 10
    await page.mouse.wheel(0, 4000)
    await wait_for_count_increase(cards, timeout=5s, poll=300ms)
    if count == previous: break          # honest exhaustion
    if count >= MAX_POSTS: break         # default MAX_POSTS = 15–20
```

- Bounded both ways: never infinite-scrolls a keyword, never sits silent.
- Per-scroll pause randomized 0.8–1.6 s (on top of `search_batch` pacing).
- Per-keyword ceiling `MAX_POSTS` (env-tunable) keeps a 5-keyword run at
  ~75–100 posts worst case — human-scale, same order as "search all"
  keywords does today, not a harvesting loop.
- Recency maps through the existing `datePosted=%5B%22past-24h%22%5D`
  facet — the country is **never** appended (geo-scoping fix preserved;
  ingest filter remains the targeting guarantee).

## 7. Risk mitigations

- **ToS (contract, not criminal):** automation is prohibited regardless of
  account; individual-user exposure is realistically account restriction.
  Mitigations are the app's existing posture: read-only, manual runs,
  human pacing, home residential IP, headed-capable real profile with real
  history.
- **Account choice — DECIDED (this session): DUMMY ACCOUNT.** The Playwright
  profile logs in as a dedicated secondary account, keeping blast radius off
  the job-hunt identity. Consequences and prerequisites, in order:
  1. Create the dummy with a separate email + a phone number under the
     user's control (verification will be demanded — fresh accounts are
     LinkedIn's #1 detection target: 78M fake accounts blocked in one
     quarter per their 2026 transparency report).
  2. **Age it manually before any automation** (2–4 weeks minimum): photo,
     filled profile, 20–50 connections, ordinary daily use. Automating a
     newborn account is the exact anti-abuse pattern.
  3. Search-only usage makes this viable: the dummy never messages, never
     applies, never needs professional credibility — recruiters never see it.
  4. Volume discipline matters MORE on a dummy: manual runs only, default
     5-keyword rotation, existing pacing — no second run "because it's
     cheap".
  5. Fallback: if the dummy gets restricted mid-testing, the MCP source
     (real account) remains one env-var away — testing pauses, nothing
     breaks.
  Meanwhile, branch items 1–3 (env switch, sanity gate, DLQ) need no
  LinkedIn access at all and proceed in parallel with the aging window.
- **Selector rot:** LinkedIn redesigns will break extraction. Mitigations:
  single `SELECTORS` dict, canary + degraded flag, saved-HTML debugging,
  MCP source one env-var away, weekly live validate-search habit.
- **Volume discipline:** `MAX_POSTS` + bounded scrolls + existing pacing;
  nothing retries into a loop.

## 8. Fallback policy — when MCP may still be used

MCP is called only for capabilities Playwright does not implement, and
never for post search. The delegation lives *inside* PlaywrightSource
(lazily-created `LinkedInMCPSource` for delegated methods) so callers and
the trace never see two sources.

| Capability | Primary | v1 fallback (MCP) | v2 |
|---|---|---|---|
| `search_posts` | Playwright | **none** — a Playwright search failure fails loudly; never re-routed to MCP | — |
| `check_health` | Playwright (its own browser) | — | — |
| `search_people` (DM targets) | — | MCP (no ceiling problem, tiny volume) | native Playwright profile lookup |
| `get_job_details` / `search_job_ids` (enrichment) | — | MCP (explicit per-click action only) | native Playwright job page |

Rules and consequences:
- **Search never double-routes.** Failing search → failed run (existing
  semantics), because silently falling back would hide Playwright bugs —
  exactly what the merge gate exists to expose.
- **Fallback traffic runs as the REAL account** (the MCP session volume is
  the real login). Acceptable — read-only, per-explicit-action volume —
  but it means the dummy account's searches and the real account's DM/job
  lookups coexist by design until v2.
- **docker.sock stays mounted** until v2 retires MCP entirely; the "no
  Docker dependency" win lands then, not at v1.
- Once both v2 rows are native, `linkedin_mcp.py`, the login container,
  the port-6080 flow, and the docker.sock mount are deleted.

Rollback story: `SEARCH_SOURCE=mcp` in `backend/.env` restores today's
behavior wholesale. No data migration — both sources emit the same `Post`
rows into the same pipeline.

## 9. What a swap touches (and doesn't)

| Touch | Change |
|---|---|
| `search/playwright_source.py` | new (~250–350 lines incl. selectors dict) |
| `search/pw_login.py` | new, tiny (one-time headed login) |
| `search/base.py` | factory: ~5 lines (env switch, lazy import) |
| `config.py` | `search_source`, `pw_profile_dir`, `max_posts_per_keyword` |
| `routers/settings.py` | extend `available_sources`, surface profile dir |
| `.env.example`, `Dockerfile` | document flag; browser deps if in-container |
| graphs / services / pipeline / queue / frontend | **nothing** |

**Deployment decision — USER CALL:** run the browser inside the app
container (self-contained; image grows ~300 MB; profile dir becomes a
volume) or on the host (dev-mode only; simplest, but breaks the
one-container story). Prototype can run host-side; the Docker answer can
lag the merge.

**Scope call:** v1 implements `search_posts` + `check_health` natively;
`search_people` and job-detail enrichment delegate to MCP inside the
class (§8) — no ceiling problem there and it keeps v1 small. v2 replaces
both delegations with native Playwright page reads and deletes MCP.

## 11. Companion feature on this branch: run-page keyword picker

Requested alongside the account decision: let the user **select specific
keywords** for a run instead of only LRU-rotation or all-keywords.

- Run page: the current "Search all keywords" checkbox becomes a third
  mode — run **selected** keywords (multi-select list from the pool;
  pinned pre-checked), all, or default rotation.
- API: `POST /api/runs` accepts optional `keyword_ids: list[int]`;
  validated against the pool; empty/absent keeps today's behavior.
- `keyword_service.pick_for_run` honors an explicit id list (order as
  given, still marked used); run summary shows which mode ran.
- Why it matters here: the Playwright test phase burns live sessions on a
  dummy account — being able to run ONE known keyword, without consuming
  the LRU rotation, is the control lever for every validation run and the
  live merge gate itself.
- Effort: ~3–4 h (RunPage multi-select, router param, pick_for_run ids,
  tests). Independent of Playwright internals; builds before item 4.

## 10. Test plan

1. **Parse unit tests** against saved real-search HTML fixtures (no
   network, no browser) — this finally closes the "schema unvalidated" gap.
2. **Source stub** for the graph/e2e suite (mirror of today's
   `FakeMCPSource`) implementing the same protocol.
3. **Live gate (manual, one-time before merge):** one keyword via
   `/api/runs/validate-search`-style path; assert posts > 3, permalinks
   present per post, degraded=False; capture fixture while there.
4. **Container gate (before the code default flips to playwright):** the
   browser must work inside the Docker image (chromium deps baked in,
   profile dir on the app-data volume) — until then `mcp` stays the code
   default and Playwright is env-enabled on the branch.
