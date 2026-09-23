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

## 3. Architecture: one name, two implementations

`get_linkedin_source()` (`search/base.py`) keeps returning a source under
`SOURCE_NAME = "linkedin"` so **every caller stays untouched** (nodes,
queue_service, settings, runs). The factory gains an env switch:

```
SEARCH_SOURCE=mcp        # default, today's behavior
SEARCH_SOURCE=playwright # new source
```

- Import stays lazy (importing `search.base` must not require playwright).
- `settings.search_source` in `config.py`; `routers/settings.py` already
  exposes `search_source.{name, available_sources}` — extend the list.
- Docker spawning code becomes irrelevant to this source: no container, no
  docker.sock dependency for searches (the MCP source keeps it as fallback).

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
- **Account choice — USER DECISION:** real account (lowest detection
  profile; blast radius on the job-hunt identity) vs dummy account (blast
  radius off the identity; but fresh accounts match LinkedIn's anti-abuse
  pattern — 78M fake accounts blocked in one quarter per their 2026
  transparency report — and need phone verification, which re-identifies).
  Not decidable in this doc.
- **Selector rot:** LinkedIn redesigns will break extraction. Mitigations:
  single `SELECTORS` dict, canary + degraded flag, saved-HTML debugging,
  MCP source one env-var away, weekly live validate-search habit.
- **Volume discipline:** `MAX_POSTS` + bounded scrolls + existing pacing;
  nothing retries into a loop.

## 8. Fallback plan

- **MCP remains the default** until the prototype proves, live, that it
  beats 3 posts/keyword with clean extraction (that comparison is the
  merge gate).
- Switching is `SEARCH_SOURCE=` in `backend/.env` (or Settings later);
  rollback is unsetting it. No data migration — both sources emit the same
  `Post` rows into the same pipeline.
- If a run fails at the source boundary, existing semantics hold: search
  failures fail the run loudly; downstream fails soft per post.

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

**Scope call:** v1 implements `search_posts` + `check_health`;
`search_people` (DM fallback) can initially fall back to the MCP source —
it has no ceiling problem (single-profile lookups) and keeps v1 small.

## 10. Test plan

1. **Parse unit tests** against saved real-search HTML fixtures (no
   network, no browser) — this finally closes the "schema unvalidated" gap.
2. **Source stub** for the graph/e2e suite (mirror of today's
   `FakeMCPSource`) implementing the same protocol.
3. **Live gate (manual, one-time before merge):** one keyword via
   `/api/runs/validate-search`-style path; assert posts > 3, permalinks
   present per post, degraded=False; capture fixture while there.
