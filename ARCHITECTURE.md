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

Chosen for two features actually used rather than by default:

- **`JSONB`** holds the raw source payload beside the normalised columns. Provenance
  is never discarded, so when a score is challenged the answer is reconstructible
  from what the source returned rather than from what the parser made of it.
- **`pg_trgm`** with a GIN index on `companies.name` for fuzzy dedupe. Without it,
  `similarity()` across the table is a sequential scan per candidate.

Five tables: `companies`, `contacts`, `scores`, `raw_payloads`, `http_cache`.
SQLAlchemy 2.0 with `Mapped[...]` annotations; Alembic for migrations.

**Production:** Supabase (managed Postgres, free tier).
**Shipped demo:** Postgres in Docker. The demo reads a committed 250-company
snapshot, so the database is not on the request path see *Scope* below.

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
up, which is false here, since the API serves a committed snapshot and Render
provisions no Postgres. The worst outcome of having no storage at all should be
doing the work twice, not failing the request.

**Production:** Upstash Redis in front of Postgres.
**Shipped demo:** the Postgres path, degrading to uncached, which is why the
guard exists.

---

## Scoring engine

Pure functions of a `Company`. No I/O, no randomness, **no LLM** which is a
product decision, not a limitation. A searcher committing seven figures cannot
audit a model's opinion, and SaaSquatch already ships an opaque AI score. The
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

The serverless question is the one the handbook asks by name, and the answer is
that it was rejected on the workload rather than defaulted into. A cold Lambda
would re-parse the dataset and rebuild the pool on every invocation, and a
90-second Overpass query does not fit the model at all.

`POST /api/companies/{id}/refresh` is the endpoint that makes that concrete. It
crawls the company's own site, re-runs domain inference if no URL is known,
re-validates contacts against DNS, and re-scores seconds of work per call.
It exists because justifying an architecture with a feature that does not exist
is worse than choosing the wrong architecture: the claim was in this document
before the endpoint was, and that was a defect.

It deliberately does not write back to the committed snapshot. A refresh answers
"what does this company look like right now"; silently mutating the shipped
dataset would mean two people running the demo saw different data with no way to
tell why.

The cost of that choice is the free tier's flip side: Render sleeps an idle
container after ~15 minutes, so the first request after a quiet spell pays a
30-60s cold start. That is the plan, not a fault the same long-running process
that justifies the architecture is the thing being suspended. `keep-warm.yml`
pings `/api/health` on a schedule to hold it awake; for a guaranteed-warm review
window an external uptime pinger on the same URL is more reliable than GitHub's
best-effort cron. And when a cold start does happen, the client covers it with a
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

The schema is managed by Alembic: `alembic upgrade head` creates all five tables
and the trigram index (the migration installs the `pg_trgm` extension first, or
the `gin_trgm_ops` index would fail on a fresh database). The initial migration
is autogenerated from the models and verified through a full downgrade-to-base
and re-upgrade round trip against a clean Postgres.

Gates, all currently passing: **236 backend tests** (including the API routes
and the schema migration) and **57 frontend tests** (the cross-language scoring
parity fixture, name casing, keyboard navigation, a structural layout guard,
and the first-load progress curve and its component), `ruff` clean, `mypy --strict` clean across 36 modules, `oxlint` clean,
`tsc -b` clean.

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

Two production services are specified above but **not provisioned** for the
shipped demo: Supabase and Upstash. Neither adds anything a 250-row committed
snapshot can demonstrate, and the time went to the interface instead. Both sit
behind interfaces the code already uses the cache fallback is the code path the
demo runs on, not a stub.

`--market columbus` is defined and runnable but not collected: Ohio has no
equivalent licence register, so the two markets would not compare like with like.

**Known ceiling.** `digital_gap` and `health` depend on a website, and no
register publishes one. Domain inference recovers some, and every remaining gap
is reported rather than hidden which is why `covered_weight` is on screen next
to every score.
