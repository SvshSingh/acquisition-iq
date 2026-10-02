# AcquisitionIQ

**Explainable acquisition-fit scoring for search funds.** It scores a small
business 0 to 100 as an *acquisition target*, not as a sales lead, and shows its
working for every point.

Sourav Singh · [github.com/SvshSingh](https://github.com/SvshSingh)

**Live:** [App](https://frontend-orcin-five-88.vercel.app)

[Interactive API docs](https://acquisition-iq-api.onrender.com/docs)

> The backend runs on a free tier that sleeps when idle, so the **first** load
> after a quiet spell takes about 30 seconds while the container wakes. A
> progress bar covers the wait. If the page is slow or empty on opening, give it
> a moment and reload; it is a cold start, not a fault.

---

## The problem

A searcher looking for a business to buy and a salesperson looking for a
customer want opposite things from the same company record. Lead-generation
tools are built for the salesperson: discovery, enrichment, outreach, and a
score that means "likely to buy from you". Two gaps follow.

1. **Nothing scores a company as something to acquire.** An owner near
   retirement, a business that fits a buy box, a website a decade out of date:
   these are noise to a sales score and the whole point to a buyer.
2. **Nothing explains itself.** An opaque score cannot be trusted by someone
   making a seven-figure decision on it.

AcquisitionIQ fills both. Six weighted factors, each returning a subscore, the
evidence behind it with source links, and the signals it looked for and could
not find.

---

## What it does

**Scores acquisition fit, not sales fit.** Succession pressure, buy-box size,
digital modernisation upside (inverted: a *worse* website scores higher, because
that is headroom after the purchase), niche fragmentation, contactability, and
liveness.

**Shows its work.** Click any row: every factor with its subscore, its weight,
the arithmetic between them, the evidence sentence, and a link to the source
record. Factors that observed nothing are shown greyed with their weight visibly
moved elsewhere.

**Says what it doesn't know.** Every score carries a *thesis coverage* figure:
how much of your weighted buy box actually had evidence behind it. A 78 backed by
40% of the thesis is a different claim from a 78 backed by 95%, and the tool
refuses to blur them.

**Is yours to tune.** The six weights are sliders. The table re-sorts as you move
them, in the browser, with no round trip.

**Scores the list you already have.** Import a CSV from a lead tool, a CRM or a
broker sheet and it is validated and acquisition-scored in place, with the same
explainable breakdown. The columns are mapped automatically and the response
says exactly which column became which field. An imported list usually carries
headcount and revenue estimates, which the public sources cannot provide, so the
buy-box factor becomes measurable on imported rows where the licence data can
only report it as unknown.

**Refreshes from source, and keeps what it finds.** One click re-crawls a
company's own site, re-validates its contacts against DNS and re-scores it. The
result is saved, the raw crawl output is kept beside it, and the detail panel
shows how the score has moved over time.

**Exports to your CRM.** HubSpot and Salesforce column presets, not a raw dump.

---

## Why the score is trustworthy

The engine is **deterministic and has no LLM in it**, which is deliberate.

A model's opinion cannot be audited, and closing an explainability gap with
another black box would be self-defeating. Every number here traces to a filed
record or an observed page, and a golden-file test pins the whole engine so a
refactor cannot silently move the numbers.

The same principle runs through the data layer:

- **`VERIFIED` on an email means the domain accepts mail and the address is
  well-formed. It never means the mailbox exists.** Confirming that means opening
  SMTP conversations under false pretences thousands of times, which is
  unreliable, rude, and how a sending IP gets blocklisted. The evidence string
  says exactly what was checked.
- **A DNS timeout is `UNKNOWN`, never `INVALID`.** "We could not find out" must
  not be recorded as "this will bounce."
- **An inferred website is proved before it is stored.** Candidates are derived
  from the business name, then accepted only if the licensed phone number appears
  on the page (conclusive) or every distinctive name token plus the licensed city
  does (suggestive, and labelled as such). Everything else is discarded.
- **Absence is not evidence.** A licence register has no website column, so a
  missing URL means "this source doesn't carry one", not "this business has none".
  That distinction is the difference between a factor scoring 50 and reporting
  itself unmeasured.

---

## Data, and the right to use it

| Source | Licence | Committed here? |
|---|---|---|
| California CSLB public data portal | Public domain (California Conditions of Use) | Yes, in `data/raw/` |
| OpenStreetMap via Overpass | ODbL 1.0 | Derived only |
| Company websites | Public pages, `robots.txt` obeyed | Derived signals only |

**Google Places and Yelp were deliberately not used.** Both forbid storing or
redistributing results, which would make the committed dataset in this repository
impossible. Less data, but data we are actually allowed to have.

The crawler asks each host's `robots.txt` once and obeys it, holds itself to two
concurrent requests per host, backs off exponentially with jitter, honours
`Retry-After`, and trips a circuit breaker per host. Its User-Agent names the
project and links here.

One carve-out is documented rather than hidden: `overpass-api.de` publishes
`Disallow: /api/`, a rule aimed at search engines spidering expensive API URLs.
Programmatic use is governed by that project's separate usage policy, which this
follows. The exemption is an explicit per-prefix allowlist in `config.py`, and
**the website crawler is exempt from nothing.**

---

## Run it

### Everything, with Postgres

```bash
git clone https://github.com/SvshSingh/acquisition-iq && cd acquisition-iq
```

```bash
docker compose up --build
```

Open <http://localhost:8080>. The API migrates the database, loads the seed
market into it and serves from it. `curl localhost:8000/api/health` reports
`"storage": "postgres"`.

### For development

Requires Python 3.11+ and Node 20.19+.

**Backend**

```bash
cd backend && python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

On macOS or Linux use `.venv/bin/python` instead of `./.venv/Scripts/python.exe`.

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

**Frontend**, in a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>. The API is proxied, so nothing else needs
configuring. With no `DATABASE_URL` set the API serves the committed snapshot
and needs no database, no Redis and no API keys.

To develop against Postgres, set `DATABASE_URL` to any Postgres URL and run
`alembic upgrade head` from `backend/` once. The API loads the seed market into
an empty database on start.

### Storage, and how to tell which one is live

| `DATABASE_URL` | What serves | `/api/health` |
|---|---|---|
| set and reachable | Postgres: SQL filters, refreshes saved, score history | `"storage": "postgres"`, `"database": "ok"` |
| set, unreachable | the committed snapshot, read-only, until the database returns | `"storage": "snapshot"`, `"database": "unreachable"`, with the reason |
| not set | the committed snapshot, read-only | `"storage": "snapshot"`, `"database": "not configured"` |

A database outage costs persistence, not availability, and the health endpoint
always says which of the three is happening.

### Verify the build

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy app scripts
```

The storage tests need a real Postgres and skip without one. To run them, point
`TEST_DATABASE_URL` at a scratch database (they truncate it).

```bash
cd frontend && npm ci && npm test && npx tsc -b && npm run lint
```

### Rebuild the dataset from source

```bash
cd backend && ./.venv/Scripts/python.exe scripts/collect_seed.py --market glendale --limit 250
```

---

## Layout

```
backend/app/scoring/     the six factors and weighted composition: pure, no I/O
backend/app/pipeline/    sources, dedupe, validation, domain inference, peers
backend/app/store.py     where companies come from: Postgres, or the snapshot
backend/app/db/          models, row mapping, connection handling
backend/app/api/         FastAPI routes
backend/alembic/         schema migrations
backend/tests/           292 tests (28 need Postgres: set TEST_DATABASE_URL)
frontend/src/            React 18 + TypeScript + Tailwind
frontend/src/**/*.test.* 64 tests (scoring parity, casing, keyboard nav, layout, loader, history)
data/raw/                CSLB exports, public domain
data/seed_glendale.json  the committed scored snapshot
ARCHITECTURE.md          storage, caching, hosting, deployment: the specifics
```

---

## Known limits

- **`digital_gap` and `health` need a website, and no register publishes one.**
  Domain inference recovers part of that. The rest is reported rather than
  hidden, which is what the coverage figure beside every score is for.
- **One market.** The seed dataset is 250 licensed contractors around Glendale,
  California. The collector is market-agnostic, but only states with a public
  licence register comparable to California's will produce like-for-like data.
- **Scoring runs per request, in Python.** Filters run in the database. That is
  invisible at hundreds of companies and the first thing to change at hundreds
  of thousands.
