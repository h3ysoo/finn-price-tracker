# Design Notes

## What this project is

finn-price-tracker is a **personal, single-user tool**. It runs on your own
machine via the CLI (`python main.py ...`) or the Streamlit UI
(`streamlit run app.py`), stores data in a local SQLite file, and scrapes
Finn.no from your own IP. That is the intended and supported way to use it.

## Public web service: explored, then reverted

An earlier iteration prototyped turning this into a multi-user public web
service. That work was **deliberately reverted** — for a personal tool the
extra moving parts were pure overhead, and hosting a public scraper of
Finn.no raises Terms-of-Service and cost/abuse concerns that aren't worth it
here (see the project README's Disclaimer).

**Reverted (removed from the codebase):**

- RQ + Redis job queue (`jobs.py`) and the Streamlit enqueue/poll path —
  only needed to keep a browser out of a shared web process.
- SQLAlchemy + Postgres data layer — SQLite is the right fit for one user;
  removing it also dropped the `sqlalchemy` and `psycopg` dependencies.
- Docker (`Dockerfile`, `docker-compose.yml`) and the Docker/Postgres CI jobs.

**Kept (useful for a personal tool regardless):**

- `pipeline.py` — the shared search pipeline used by both the CLI and the UI.
- Query result cache (`SEARCH_CACHE_TTL_HOURS`, `--fresh` / "Force fresh scan").
- Query normalization (case/spacing variants share one cache, history, rows).
- Retention pruning (`prune` command, `RETENTION_DAYS`).
- Env-var configuration in `config.py`.

## If you ever revisit going public

It would be a real re-architecture, not a deploy. The load-bearing problems,
in rough order: run scraping in a job queue (not the web process); add auth +
per-user quotas + an AI spend cap (one shared API key otherwise bills the
owner for every visitor); move to Postgres for concurrency; cache aggressively
and add block/backoff handling for the single-server-IP problem; and settle
the Finn.no Terms-of-Service question first. The git history around the
"ROADMAP Phase 1–3" commits shows one way each of these was built, if useful
as a reference.
