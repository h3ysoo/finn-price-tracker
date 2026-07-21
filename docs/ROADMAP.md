# ROADMAP — From Personal Tool to Public Web Service

> **Purpose of this document.** This is a self-contained handoff plan. It gives any
> developer — or any AI assistant session with no prior context — everything needed
> to continue turning finn-price-tracker into a publicly deployable web service.
> Work through the phases in order; each phase leaves the app in a working state.

## Context: what this project is today

finn-price-tracker scrapes **Finn.no** (Norwegian second-hand marketplace),
runs statistical price analysis, tracks price history in SQLite, and evaluates
top listings with **Claude Vision** (structured output via a forced tool call).
It has a CLI (`main.py`) and a Streamlit UI (`app.py`). See `README.md` for
features and project structure. Owner: GitHub user **h3ysoo**, repo
`h3ysoo/finn-price-tracker`, branch `main`.

### Working conventions (follow these when continuing)

- Commits go **directly to `main`**, in **English**, one focused commit per change.
- Always `git fetch origin` and rebase/pull before pushing — multiple
  sessions/machines push to this repo and it has diverged before.
- Tests must pass before every push: `python -m pytest tests/` (CI runs them on
  Python 3.11 & 3.12, including a Playwright/Chromium fixture test).
- Real secrets live only in `.env` (gitignored). `.env.example` holds
  placeholders only — never put a real key in it.
- The Streamlit UI is verified by seeding a temporary `data/listings.db` with
  demo listings, viewing the app, then deleting the demo DB.

### Why it is NOT publicly deployable as-is

1. **In-process scraping.** Each search launches headless Chromium inside the
   web process (300–500 MB RAM, 30 s–4 min). A handful of concurrent users
   exhausts any small server.
2. **Single API key, no auth, no quotas.** Every visitor's Claude Vision
   analysis bills the owner's `ANTHROPIC_API_KEY`; abuse is trivial.
3. **Legal exposure.** Personal scraping ≠ operating a scraping service for
   the public. `README.md` explicitly scopes the project to personal/
   educational use. Finn.no's ToS applies.
4. **Single server IP.** All scrapes from one datacenter IP will likely get
   blocked quickly (locally, each user scrapes from their own residential IP).
5. **SQLite, one shared file.** No user separation, poor concurrent-write
   behavior, all visitors would see each other's searches.

---

## Phase 0 — Decisions before any code (BLOCKING)

- [ ] **Legal check.** Research Finn.no's current Terms of Service and their
      official API / partner program (Schibsted/FINN API). Decide: proceed with
      polite scraping + caching, switch to an official API, or restrict the
      public product to *bring-your-own-instance* (self-hosted). Document the
      decision here. **Do not skip** — everything downstream depends on it.
- [ ] **Audience model.** Recommended first step: invite-only / password-protected
      deployment for the owner and a few friends, not an open site.
- [ ] **Hosting.** Pick a Docker-capable host (e.g. Hetzner VPS, Fly.io,
      Railway). Needs ≥ 2 GB RAM for one browser worker. Streamlit Community
      Cloud is NOT suitable (RAM limits, no background workers).
- [ ] **Budget.** Set a monthly Anthropic spend cap and a per-user quota target.

## Phase 1 — Containerize (app still single-user) — ✅ DONE

Goal: the current app runs identically in Docker; no behavior change.

- [x] `Dockerfile`: `python:3.12-slim` + `playwright install --with-deps chromium`
      + `requirements.txt`. Non-root user, health check, Streamlit entrypoint.
- [x] `docker-compose.yml`: `web` service + persistent `finn-data` volume,
      `.env`-driven config. (`worker`/`db`/`redis` services added in Phase 2/3.)
- [x] Env-var config in `config.py` (`DATA_DIR`, `DB_PATH`, `CLAUDE_MODEL`,
      limits, delays) with current values as defaults.
- [x] CI builds the image (`docker` job in `.github/workflows/ci.yml`).

## Phase 2 — Job queue (kills problem #1) — ✅ DONE (code); needs a live compose run

Goal: scraping never runs inside the web process.

- [x] Extract the pipeline into `pipeline.py` — `SearchParams` / `SearchResult`
      + `run_search` (async) / `run_search_sync`, with a `progress` callback.
      Both `app.py` and `main.py` now drive it. This is the seam the worker calls.
- [x] **RQ + Redis** via `jobs.py`: `enqueue_search` / `fetch_job` /
      `run_search_job` (reports stage via `job.meta`). Gated on `REDIS_URL` —
      unset means in-process fallback, so local dev needs no Redis. Tested
      with fakeredis + a synchronous queue (`tests/test_jobs.py`).
- [x] Worker concurrency: one RQ worker = one job (one browser) at a time;
      scale with `docker compose up --scale worker=2` if RAM allows.
      (Browser reuse *across* jobs is a possible later optimization.)
- [x] Streamlit UI enqueues + polls every 2s showing the worker's stage;
      results stay in `st.session_state`.
- [ ] **Validation gap:** the full queued flow has not been exercised on a
      machine with Docker (dev machine had none — image + unit tests are CI-
      verified). First session on a Docker-capable machine: `docker compose
      up --build`, run a search, confirm the worker executes it and the page
      polls to completion.

## Phase 3 — Postgres + result caching (kills problem #5, halves scraping)

- [x] Replace SQLite with Postgres: `database/db.py` now runs on SQLAlchemy —
      SQLite at `DB_PATH` by default (zero-setup local dev), Postgres when
      `DATABASE_URL` is set (the compose stack does; `db` service +
      `pgdata` volume). Dialect-sensitive SQL is covered by
      `tests/test_postgres.py`, which CI runs against a postgres:16 service
      container (locally it skips without `TEST_DATABASE_URL`).
- [x] **Query result cache:** `_load_cached` in `pipeline.py` serves stored
      results when the query was scanned within `SEARCH_CACHE_TTL_HOURS`
      (default 6 h, 0 disables). `SearchParams.use_cache=False` / CLI
      `--fresh` / web "Force fresh scan" bypass it; results are labeled with
      the scan time. Tested in `tests/test_cache.py`.
- [ ] Add `user_id` scoping: searches belong to users; listings/history remain
      shared (they're public market data) but "saved searches" views become
      per-user.

## Phase 4 — Auth, quotas, cost control (kills problem #2)

- [ ] Authentication: `streamlit-authenticator` (quickest) or move the web
      layer to FastAPI + a real frontend later. Start invite-only.
- [ ] Per-user daily quotas: max searches/day and max AI analyses/day,
      enforced server-side in the job queue, not in the UI.
- [ ] Global monthly budget guard: stop AI analysis (scraping can continue)
      when the configured spend cap is reached. Log token usage per job
      (`response.usage`) to compute spend.
- [ ] Optional: let users paste their **own** Anthropic API key (stored
      encrypted, or session-only) to run AI analysis on their own budget.

## Phase 5 — Scraping resilience & etiquette (mitigates #3, #4)

- [ ] One **global** rate limiter across all users (the job queue makes this
      natural — a single worker already serializes). Keep the existing
      randomized delays (`REQUEST_DELAY_MIN/MAX` in `config.py`).
- [ ] Cache-first policy from Phase 3 so identical queries never re-scrape
      within the cache window.
- [ ] Block detection: watch for 429s/captcha pages in
      `scraper/finn_scraper.py`, back off exponentially, alert the owner.
      The retry helper `FinnScraper._goto_with_retry` is the hook point.
- [ ] IP strategy — decide one: (a) accept the risk on a single IP with heavy
      caching, (b) reputable rotating proxy provider, or (c) pivot: keep the
      hosted site as a **viewer** of shared data and ship scraping as a small
      local agent users run themselves. Option (c) is the most ToS-friendly.
- [ ] Revisit the README disclaimer wording once the model is chosen.

## Phase 6 — Production operations

- [ ] Reverse proxy with HTTPS (Caddy or Traefik) + domain.
- [ ] Nightly Postgres backups; test a restore once.
- [ ] Error tracking (Sentry) for web + worker; uptime monitoring.
- [ ] Deploy workflow: GitHub Actions builds the image and deploys on push to
      `main` (after tests pass).
- [ ] Load test with 5–10 simulated users before inviting anyone.

---

## Suggested order of attack for a continuing session

Phases 1→2 are pure engineering and safe to start immediately; Phase 0's legal
research can run in parallel and **must** conclude before the site goes public.
Each phase should land as a series of small commits on `main` with tests.

Key files to read first when picking this up cold:
`README.md`, `pipeline.py` (`run_search` — the shared pipeline),
`app.py` (UI driving `run_search_sync`), `scraper/finn_scraper.py`
(`FinnScraper`), `database/db.py` (`Database`), `analyzer/ai_analyzer.py`
(`analyze_top_listings`), `config.py`, `tests/`.

## Progress log

- **Phase 1 complete** (env-var config, Dockerfile + compose, CI image build).
- **Phase 2 complete in code**: `pipeline.py` (shared pipeline), `jobs.py`
  (RQ queue, `REDIS_URL`-gated with in-process fallback), Streamlit
  enqueue+poll, and compose now runs `web` + `worker` + `redis`.
  Outstanding: one live `docker compose up` validation on a Docker-capable
  machine (see the checkbox in Phase 2).
- **Phase 3 cache done**: query-result cache live in the shared pipeline with
  CLI/web overrides, verified end-to-end in the UI (instant cached results,
  labeled with scan time).
- **Phase 3 essentially complete**: result cache + SQLAlchemy data layer
  with Postgres in compose and CI. Remaining Phase 3 item (`user_id`
  scoping) is deferred into Phase 4 since it needs the user model.
- **Next: Phase 4** — auth + quotas. Suggested order: (1) invite-only login
  (streamlit-authenticator), (2) per-user daily search/AI quotas enforced in
  `jobs.py` / the worker, (3) monthly AI budget guard using
  `response.usage` token logging in `analyzer/ai_analyzer.py`.
- Reminder: the live `docker compose up` validation (Phase 2 checkbox) is
  still outstanding — now it also covers the Postgres service.
