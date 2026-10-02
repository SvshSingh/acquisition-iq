# Architecture

Every number in this document was measured on the running system, not estimated.

---

## Shape

```
  California CSLB              OpenStreetMap             Company websites
  (licence register)           (Overpass API)            (direct crawl)
  structure: ownership,        presence: coordinates,    signals: HTTPS, mobile,
  issue date, employment       websites                  analytics, staleness
        │                            │                          │
        └────────────┬───────────────┘                          │
                     ▼                                          │
              DiscoverySource ──────────────────────────────────┘
                     │
                     ▼
   dedupe → chain detection → peer density → domain inference
          → website crawl → contact validation
                     │
                     ▼
              Scoring engine  (pure, deterministic, no I/O)
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
   data/seed_glendale.json    FastAPI  ──────►  React SPA
   (committed snapshot)       (container)       (static bundle)
```

---

## Data sources

| Source | Provides | Licence | Why this one |
|---|---|---|---|
| **CSLB public data portal** | Ownership form, licence issue date, trade classification, workers' comp status, address, phone | Public domain (California Conditions of Use) | Filed facts rather than marketing copy. Ownership and issue date are on **100%** of rows against 12% and 10% when scraped from websites |
| **OpenStreetMap** (Overpass) | Coordinates, occasional website | ODbL 1.0 | The only open source linking a business to a URL |
| **Company websites** | HTTPS, mobile viewport, analytics, CMS, content freshness, emails, owner names | Public pages, `robots.txt` respected | The only source for post-acquisition digital upside |

**Deliberately not used:** Google Places and Yelp. Both forbid storing or
redistributing results, which would make the committed dataset in this repo
impossible.

### Ethical position, stated plainly

- `robots.txt` is fetched once per host, cached, and obeyed. A disallowed URL is
  not fetched.
- Two-level concurrency (16 global, **2 per host**), exponential backoff with
  full jitter, `Retry-After` honoured, per-host circuit breaker.
- The User-Agent identifies the project and links to this repository.
- **One documented carve-out.** `overpass-api.de` publishes `Disallow: /api/`.
  That rule exists to stop search engines spidering expensive API URLs;
  programmatic use is governed by the project's separate usage policy, which we
  follow. Access is via an explicit per-prefix allowlist in `config.py`, not a
  global switch  **the website crawler is exempt from nothing**.

---

## Storage — PostgreSQL 16

Postgres is the serving path whenever `DATABASE_URL` is set. Every route reads
through one `CompanyStore` interface (`app/store.py`) with two implementations:

| Store | When | What it does |
|---|---|---|
| `PostgresStore` | `DATABASE_URL` set | Filters run in SQL; a refresh is written back with its raw output and its score |
| `SnapshotStore` | no database, or the database is unreachable | Serves the committed 250-company JSON file; read-only |

Postgres was chosen for two features that are used, not listed:

- **`JSONB`** holds what a source returned beside the normalised columns. Each
  refresh writes a `raw_payloads` row, so when a score is questioned the answer
  is reconstructible from what the crawl saw rather than from what the parser
  made of it, even after the page has changed.
- **`pg_trgm`** with a GIN index on `companies.name`. The search box matches
  with an unanchored `ILIKE '%term%'`, which a B-tree cannot serve and a trigram
  index can. At 250 rows the planner rightly prefers a sequential scan; the
  index is there for the table this is meant to grow into.

Six tables: `markets`, `companies`, `contacts`, `scores`, `raw_payloads`,
`http_cache`. SQLAlchemy 2.0 with `Mapped[...]` annotations; Alembic migrations.

**Provisioning is one step.** The container runs `alembic upgrade head` on start,
and the API loads the committed snapshot into an empty database. Pointing the
service at a bare Postgres is the whole procedure; `scripts/load_seed.py` exists
for pushing a newly collected market into a database that already has data.

**The SQL path is held to the in-memory path.** The snapshot store is the
specification. `tests/test_store.py` runs nineteen filter combinations through
both stores and requires the same companies back, round-trips every company
through its row and requires it unchanged, and checks that no score moves. CI
runs these against a real Postgres service, so they are not skipped where it
counts.

**A database outage costs persistence, not availability.** A read that fails
against Postgres is answered from the snapshot, and the database is then left
alone for a short cooldown so that one request pays for discovering the outage
rather than every request waiting out a connection attempt. A stale lead list is
still useful; a 500 is not. This is never silent: `/api/health` reports
`"storage"` (which store is answering right now) and `"database"`
(`ok`, `unreachable` with the reason, or `not configured`), and a refresh that
could not be saved says so in its `X-Persisted` header. The first healthy ping
restores Postgres without a restart, and that includes a database that was down
when the container booted: the one-time preparation (schema check, loading an
empty database) is retried from the health check rather than only at startup.

**Row level security is on for every table**, including Alembic's own. This is
not optional on Supabase: the `public` schema is published through an
auto-generated REST API reachable with the project's anon key, and a table there
without RLS is readable and writable by anyone holding it. With RLS enabled and
no policies, the API roles get nothing, while the backend connects as the table
owner and is unaffected. A test fails if any table is left open.

**Connection handling** (`app/db/session.py`) normalises whatever URL a
dashboard hands out: `postgres://` becomes the async driver, libpq's `sslmode`
is translated to asyncpg's `ssl`, Supabase hosts always get TLS, and the
transaction pooler (port 6543, PgBouncer) has prepared-statement caching
disabled because a statement prepared on one server connection may be replayed
on another. Render reaches Supabase through the **session pooler**: Supabase's
direct connection is IPv6-only and Render's network is IPv4.

**Production target:** Supabase. Whether a given deployment is on it is not
something to take from this document: `GET /api/health` on the running service
says which store is answering.

---

## Caching — two layers

| Layer | Key | TTL | Purpose |
|---|---|---|---|
| HTTP response | `sha256(method + url)` | 24h | Re-running the collector costs nothing; sites change slowly |
| Score memo | content hash of company + weights + buy box + **engine version** | 7d | Unchanged input never re-scores; an engine bump invalidates everything at once |

One `Cache` protocol, four implementations: Redis, Postgres, null (for tests),
and a fallback wrapper. **Every layer is guarded, including the last one.** A
cache is an optimisation, and an optimisation that can fail a request is a
liability the first failure logs once and demotes to the next standby for the
process lifetime rather than paying a timeout on every subsequent call.

The chain is Redis → Postgres → null. That final link was added after the live
refresh endpoint raised `ConnectionRefusedError` in an environment with no
database: guarding only Redis assumed that if the app is up its own database is
up. The worst outcome of having no storage at all should be doing the work
twice, not failing the request. With no `DATABASE_URL` the Postgres link is
skipped outright rather than attempted and caught, since there is nothing to
fall back *from*.

**Production target:** Upstash Redis in front of Postgres. Redis is specified
and not provisioned; the Postgres cache table is the path that runs.

---

## Scoring engine

Pure functions of a `Company`. No I/O, no randomness, **no LLM** which is a
product decision, not a limitation. A searcher committing seven figures cannot
audit a model's opinion, and lead tools already ship opaque AI scores. The
gap this fills is explainability, so every subscore carries the evidence and the
source URL behind it, and the whole engine is pinned by a golden-file test.

Six factors, user-weighted. Two mechanisms are worth naming:

**Measured vs. prior.** A factor that observed nothing contributes nothing, and
its weight is redistributed across factors that did. `buy_box` had a standard
deviation of **0.00** across 250 companies before this existed no source
publishes headcount, so it returned the same prior every time while carrying 24%
of the weight. `covered_weight` records how much of the thesis had evidence, and
the UI shows it beside every score.

The distinction is finer than "did it produce evidence". Finding no way to
contact a business *is* a finding; finding no published headcount says nothing
about the company. Factors declare which case they are in.

**Confidence is computed against declared weights, never effective ones.**
Otherwise dropping an unevidenced factor would *raise* confidence knowing less
would read as knowing more.

### Client-side re-weighting

Weight changes re-score in the browser. Each company ships with its six factor
subscores, so moving a slider is arithmetic the client does in a frame; a round
trip would put ~200ms between a control and its consequence. The server keeps the
judgement what each factor scored, what evidence supports it, whether it was
measured and hands the client only the sum. The redistribution rule is
duplicated in `frontend/src/lib/scoring.ts`, including the coverage floor,
because a UI showing a number the API would not reproduce is worse than latency.

---

## Hosting

| | Choice | Why |
|---|---|---|
| **Frontend** | **Static** bundle on Vercel's CDN | No SSR needed. 246KB JS / 76KB gzipped, 15.6KB CSS |
| **Backend** | **Long-running container** on Render — deliberately *not* serverless | Refresh-from-source scrape jobs outlast a typical serverless timeout; the connection pool and the parsed dataset are only worth having if the process survives between requests |

Serverless was rejected on the workload rather than defaulted away from. A cold Lambda
would re-parse the dataset and rebuild the pool on every invocation, and a
90-second Overpass query does not fit the model at all.

`POST /api/companies/{id}/refresh` is the endpoint that makes that concrete. It
crawls the company's own site, re-runs domain inference if no URL is known,
re-validates contacts against DNS, and re-scores seconds of work per call.
It exists because justifying an architecture with a feature that does not exist
is worse than choosing the wrong architecture: the claim was in this document
before the endpoint was, and that was a defect.

With Postgres serving, a refresh is written back: the company row is updated,
the raw crawl output is stored beside it, and the score joins the company's
history. The committed snapshot is never written to, because silently mutating a
shipped file would mean two people running the same build saw different data
with no way to tell why. The `X-Persisted` response header says which happened.

The cost of that choice is the free tier's flip side: Render sleeps an idle
container after ~15 minutes, so the first request after a quiet spell pays a
30-60s cold start. That is the plan, not a fault the same long-running process
that justifies the architecture is the thing being suspended. `keep-warm.yml`
pings `/api/health` on a schedule to hold it awake; where a warm service has to
be guaranteed, an external uptime pinger on the same URL is more reliable than
GitHub's best-effort cron. The health check runs a query against the database,
so the same ping is also what recovers a database that was down at boot. And when a cold start does happen, the client covers it with a
first-load progress bar paced to the wait it advances on a curve tied to the
real request and only the arriving response takes it to 100, so it never claims
done before the data is there.

---

## Deployment

GitHub Actions: `ruff` → `mypy --strict` → `pytest` → `tsc -b` → `oxlint` →
`vitest` → `vite build` → `docker compose up` smoke test → deploy. (`tsc -b`,
not `tsc --noEmit`: the root tsconfig is solution-style, so `--noEmit` compiled
zero files and passed over anything — the typecheck step was green by
construction until it was switched to the build mode that actually reads the
sources.)

The schema is managed by Alembic: `alembic upgrade head` creates the tables and
the trigram index (the first migration installs the `pg_trgm` extension, or the
`gin_trgm_ops` index would fail on a fresh database) and enables row level
security. Both migrations are verified through a full downgrade-to-base and
re-upgrade round trip against a clean Postgres, and `alembic check` reports no
drift between the models and the migrated schema. The container runs the upgrade
on start when a database is configured; if that fails it starts anyway on the
snapshot and reports the database as unreachable, rather than crash-looping.

The compose smoke test asserts `"storage":"postgres"` on `/api/health`. A green
health check alone would not show the database was in use, because the snapshot
fallback is healthy too.

Gates, all currently passing: **292 backend tests** (the API routes, both
migrations, and the storage layer against a real Postgres; 28 of them skip on a
checkout with no database and run in CI) and **64 frontend tests** (the
cross-language scoring parity fixture, name casing, keyboard navigation, a
structural layout guard, the first-load progress curve and its component, and
the score history panel),
`ruff` clean, `mypy --strict` clean across 39 modules, `oxlint` clean, `tsc -b`
clean.

---

## Performance

| Operation | Measured |
|---|---|
| Filtered search over 250 scored companies | **26ms** |
| Full collection: 12,065 licences → 3,846 → 250 scored | ~4 min |
| Dedupe over 3,858 records | <1s |
| Contact validation, 250 companies | **5.1s** |
| Frontend production build | 197ms |

**Dedupe blocks rather than compares pairwise.** All-pairs is 31k comparisons at
250 rows and 12.5M at 5,000 — the feature would die exactly when it started to
matter. Records are bucketed on several cheap keys (registrable domain, email
domain, phone digits, name prefix + locality) and only same-bucket pairs are
compared.

**Contact validation is concurrent across companies** with a per-domain MX memo.
Most companies hold one contact, so validating per company serialised the stage
into consecutive DNS round trips; the memo means fan-out costs the resolvers
almost nothing, since queries collapse onto the few distinct mail domains in play.

---

## Scope, stated honestly

**Redis is specified and not provisioned.** The HTTP cache runs on its Postgres
table, behind the same interface Redis would sit in front of.

**Supabase is the production target, and the code path is complete and tested
against real Postgres**, but this document cannot know whether the service you
are looking at has been pointed at it. `/api/health` can, and does.

**Scoring is not pushed into SQL.** Filters run in the database; the score is
computed in Python per request. The engine is deterministic code under a
golden-file test, and re-expressing it as SQL to filter on `min_score` in the
database would mean two implementations of the judgement. At hundreds of
companies this is invisible. At hundreds of thousands it is the first thing to
change, most likely by serving default-weight scores from the `scores` table.

`--market columbus` is defined and runnable but not collected: Ohio has no
equivalent licence register, so the two markets would not compare like with like.

**Known ceiling.** `digital_gap` and `health` depend on a website, and no
register publishes one. Domain inference recovers some, and every remaining gap
is reported rather than hidden which is why `covered_weight` is on screen next
to every score.
