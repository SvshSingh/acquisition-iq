# AcquisitionIQ

**An acquisition-fit layer for SaaSquatch Leads.** It scores a company 0–100 as a
*search-fund acquisition target* — not as a sales lead — and shows its working
for every point.

Built for the Caprae Capital Full Stack Developer handbook challenge.
Sourav Singh · [github.com/SvshSingh](https://github.com/SvshSingh)

---

## The insight it's built on

Caprae is not a generic B2B SaaS company; it is an ETA / search-fund firm. Their
own LinkedIn says it outright — *"Caprae Capital Partners has SaaSquatch Leads
looking for the niches for us"* — and the founder's writing is about search-fund
governance, not sales pipeline.

SaaSquatch today does discovery → enrichment → outreach for generic B2B sales,
with an AI company score. Two gaps follow from that:

1. **Nothing scores a company as an acquisition target.** The existing score is
   sales-fit, not buy-box fit. Different question, different answer.
2. **Nothing explains itself.** An opaque score cannot be trusted by an operator
   making a seven-figure decision.

AcquisitionIQ fills both. Six weighted factors, each returning a subscore, the
evidence behind it with source links, and the signals it looked for and could
not find.

---

## What it does

**Scores acquisition fit, not sales fit.** Succession pressure, buy-box size,
digital modernisation upside (inverted — a *worse* website scores higher, because
that is post-acquisition headroom), niche fragmentation, contactability, and
liveness.

**Shows its work.** Click any row: every factor with its subscore, its weight,
the arithmetic between them, the evidence sentence, and a link to the source
record. Factors that observed nothing are shown greyed with their weight visibly
moved elsewhere.

**Says what it doesn't know.** Every score carries a *thesis coverage* figure —
how much of your weighted buy box actually had evidence behind it. A 78 backed by
40% of the thesis is a different claim from a 78 backed by 95%, and the tool
refuses to blur them.

**Is yours to tune.** The six weights are sliders; the table re-sorts as you move
them, in the browser, with no round trip.

**Scores the list you already have.** Import a CSV from SaaSquatch, a CRM or a
broker sheet and it is validated and acquisition-scored in place, with the same
explainable breakdown — no new tool to adopt, no leaving the workflow that
produced the list. The columns are auto-mapped and the response says exactly
which column became which field. This is where the reframe lands: AcquisitionIQ
is a *layer* on top of a lead source, not a replacement for one. It also closes
the one gap the public sources can't — a SaaSquatch export carries employee-count
and revenue estimates, so `buy_box` becomes high-confidence on imported rows where
the licence data can only report it as unknown.

**Exports to your CRM.** HubSpot and Salesforce column presets, not a raw dump.

---

## Why the score is trustworthy

The engine is **deterministic and has no LLM in it**, which is deliberate.

A model's opinion cannot be audited. SaaSquatch already has an opaque AI score,
and that opacity is precisely the gap this fills — so introducing another black
box to close it would be self-defeating. Every number here traces to a filed
record or an observed page, and a golden-file test pins the whole engine so a
refactor cannot silently move the numbers.

The same principle runs through the data layer:

- **`VERIFIED` on an email means the domain accepts mail and the address is
  well-formed — never that the mailbox exists.** Confirming that means opening
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
| California CSLB public data portal | Public domain (California Conditions of Use) | Yes — `data/raw/` |
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
follows. The exemption is an explicit per-prefix allowlist in `config.py` —
**the website crawler is exempt from nothing.**

---

## Run it

Requires Python 3.11+ and Node 20.19+.

```bash
git clone https://github.com/SvshSingh/acquisition-iq && cd acquisition-iq
```

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
configuring — the demo reads the committed snapshot and needs no database, no
Redis, and no API keys.

**Verify the build**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy app scripts
```

**Rebuild the dataset from source**

```bash
cd backend && ./.venv/Scripts/python.exe scripts/collect_seed.py --market glendale --limit 250
```

---

## Layout

```
backend/app/scoring/     the six factors and weighted composition — pure, no I/O
backend/app/pipeline/    sources, dedupe, validation, domain inference, peers
backend/app/api/         FastAPI routes
backend/tests/           184 tests
frontend/src/            React 18 + TypeScript + Tailwind
data/raw/                CSLB exports, public domain
data/seed_glendale.json  the committed scored snapshot
ARCHITECTURE.md          storage, caching, hosting, deployment — the specifics
```

---

## Scope, honestly

The handbook asks for no more than five hours of build. The **core engineering**
was scoped to roughly that; the remaining time went to the dataset, the
documentation and the presentation. This README will not claim otherwise.

Two production services are specified in `ARCHITECTURE.md` but not provisioned:
Supabase and Upstash. Neither adds anything a 250-row committed snapshot can
demonstrate. Both sit behind interfaces the code already uses — the Postgres
cache fallback is the path the demo actually runs on, not a stub.

The market is a parameter (`--market`), and `columbus` is defined and runnable.
It is not shipped as a second dataset, because Ohio has no equivalent licence
register and the two would not compare like with like. Saying so is better than
shipping a second dataset that looks like generalisation and proves none.

**The known ceiling:** `digital_gap` and `health` need a website, and no register
publishes one. Domain inference recovers part of that; the rest is reported
rather than hidden. That is what the coverage figure beside every score is for.
