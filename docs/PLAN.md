# Caprae Capital — Full Stack Developer Handbook Submission

## Context

Sourav is submitting the Caprae Capital Partners "Full Stack Developer AI Interview Handbook" pre-work. The handbook asks candidates to study Caprae's own leadgen product, **SaaSquatch Leads** (saasquatchleads.com), and build one or two impactful features that improve it. Submission goes to `recruiting@capraecapital.com` with subject `Full Stack Developer - Handbook Submission - {Name}`.

**Scored out of 40:** Business Use Case Understanding (10) · UX/UI (10) · Technicality (10) · Design (5) · Other (5).

**Timeline:** 3 days. The handbook says "dedicate no more than 5 hours" to the build. We keep the *core engineering* scoped to roughly that, and spend the remaining time on the non-code deliverables the doc also demands — README, architecture write-up, three essays, video. The submission never claims a false timeline.

**Confirmed decisions:** Python/FastAPI + React · live deploy on free tier · cached seed dataset *plus* a working live-refresh path.

---

## The insight the whole build rests on

Caprae is **not** a generic B2B SaaS company. It is an ETA / search-fund firm. From their own material:

- Their LinkedIn post "riches are in niches" says explicitly: *"Caprae Capital Partners has SaaSquatch Leads looking for the niches for us"* — they use the tool to find **acquisition targets and fragmented verticals**, not just sales contacts.
- Kevin Hong's founder story (#BleedAndBuild) is about search-fund governance, CEO terminations, and operator-first investing.
- Their model is "M&A as a Service" — value created **post**-acquisition over a seven-year horizon.

SaaSquatch today (per its own site and footer nav: Companies, Persons, AI News, AI Web Scanner, Financial Analysis, Email Generator, LinkedIn Messenger, PPT Generator, Validators, Teams) does **discovery → enrichment → outreach** for generic B2B sales. Two gaps stand out:

1. **Nothing scores a company as an acquisition target.** There's an "AI company score" for sales fit, not a search-fund buy-box fit.
2. **Nothing explains itself.** A score with no reasoning can't be trusted by an operator making a seven-figure decision.

That is the wedge. Building it demonstrates we read their business, not just their landing page — which is exactly the 10 points for Business Use Case Understanding.

---

## What we build

**Working name: `AcquisitionIQ`** — an acquisition-fit intelligence layer for SaaSquatch.

### Feature 1 (core): Explainable Acquisition-Fit Scoring Engine

Scores each company 0–100 as a **search-fund acquisition target**, and shows its work. Six weighted factors, each returning a subscore, evidence strings, and source URLs:

| Factor | Signal | Why Caprae cares |
|---|---|---|
| Succession / ownership | Founder-led, "family owned since 19xx", no PE backing, long tenure | The core ETA thesis — owner looking to exit |
| Buy-box fit | Employee count + revenue estimate inside $1–10M / 10–100 headcount | Wrong size = wasted outreach |
| Digital maturity gap | Stale site, no HTTPS, no CMS/analytics, poor mobile | Post-acquisition value creation upside — Caprae's stated model |
| Niche fragmentation | Count of similar small players in the same geo + vertical | "Riches are in niches" |
| Contactability | Verified email + phone + LinkedIn for a decision maker | A lead you can't reach scores zero |
| Health / recency | Hiring, recent news, review volume, site freshness | Filters dead companies |

Deterministic weighted rubric — auditable and defensible in the video — with an optional LLM pass only for extracting qualitative signals from page text (never for the score itself). Every result carries a **confidence flag** when the underlying data is thin. Weights are user-adjustable in the UI, so a searcher can tune to their own thesis.

### Feature 2 (companion): Data Quality Pipeline

Feeds Feature 1 and directly targets the Technicality bonus points ("deduplication, enrichment, or validation"):

- **Dedupe** — domain normalization, `pg_trgm` trigram similarity on company name, email-domain matching, blocking keys to keep it O(n) not O(n²)
- **Validation** — RFC-compliant email syntax, MX record lookup, disposable-domain blocklist, catch-all detection; phone parsing via `phonenumbers`
- **Normalization** — address, phone E.164, industry → NAICS mapping
- Each record carries a **data-quality score** shown next to the fit score

---

## Architecture

Documenting this precisely matters — the handbook asks by name for data storage, caching, hosting model, deployment, and cloud provider.

**Backend** — Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 + Alembic · `httpx` async client · `selectolax` for parsing · Playwright (Chromium) only as a fallback for JS-rendered pages

**Scraping layer** — async worker pool with a semaphore per domain; `robots.txt` respected via `urllib.robotparser`; exponential backoff with jitter; rotating UA pool; circuit breaker per host. Sources chosen to be **ethically defensible**, which also scores in the "Other" category:
- **OpenStreetMap Overpass API** for local business discovery (open data, no ToS problem — unlike scraping Google Maps)
- Direct company-website crawl for signals (tech stack, copyright year, "about/team" pages, contact details)
- MX/DNS lookups for validation
- No paywalled or ToS-violating source, and this is stated openly in the README

**Storage** — PostgreSQL on **Supabase** free tier. Normalized tables for companies/contacts/scores; `JSONB` for raw scrape payloads so we never lose provenance; `pg_trgm` extension for fuzzy dedupe.

**Caching** — **Upstash Redis** free tier. Two layers: TTL'd HTTP response cache (24h) keyed by URL hash, and a memoized scoring cache keyed by content hash so unchanged pages never re-score. Falls back gracefully to a Postgres cache table if Redis is unavailable.

**Data strategy (the "both" choice)** — A real seed dataset of ~200 scraped companies committed to the repo at `data/seed_leads.json`, so the demo is instant and never breaks live. A **"Refresh from source"** button triggers the live scrape path, writes through the cache, and updates the row in place. The video demos both.

**Frontend** — React 18 · Vite · TypeScript · TanStack Query · TanStack Table · Tailwind + shadcn/ui · Recharts for the factor breakdown

**Hosting** — frontend as a **static** build on Vercel (global CDN, no SSR needed); backend as a **containerized long-running service** on Render free tier — deliberately not serverless, because scraping jobs exceed serverless timeouts and we want a warm connection pool. This trade-off is worth saying out loud in the video; it shows the decision was made, not defaulted into.

**Deployment** — GitHub Actions: lint → typecheck → test → build image → deploy. Alembic migrations run on release.

---

## UX / UI

The 15 points across UX/UI and Design are won on one idea: **the tool explains itself.**

- **Single-screen workflow.** Search panel (industry, geo, size band, score threshold) → results table → detail drawer. No wizard, no page hops.
- **Score column reads at a glance** — numeric score plus a segmented bar showing which factors contributed.
- **Click a row → drawer** with the six-factor breakdown, the actual evidence sentence pulled from the source, and a link to that source. This is the moment that sells the product.
- **Adjustable weights** in a sidebar; the table re-sorts live.
- **Bulk select → Export** with CRM-ready column presets (HubSpot / Salesforce field names), not just a raw dump.
- Guided empty state, skeleton loaders, keyboard navigation, full light/dark support.
- Restrained palette, one accent color, generous whitespace, real typographic hierarchy. No dashboard-template look.

---

## Repository layout

```
acquisition-iq/
├── README.md                 # setup, screenshots, the 5-hour scoping note
├── ARCHITECTURE.md           # answers their exact questions, with a diagram
├── docker-compose.yml        # one-command local run
├── data/seed_leads.json      # committed real dataset
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/routes/       # search, score, enrich, export
│   │   ├── scoring/          # factors/, engine.py, weights.py
│   │   ├── pipeline/         # scrapers/, dedupe.py, validate.py, normalize.py
│   │   ├── cache/            # redis.py + postgres fallback
│   │   └── db/               # models, session, alembic/
│   └── tests/
└── frontend/
    └── src/{components,hooks,lib,pages}/
```

---

## Three-day schedule

**Day 1 — Foundation**
Repo scaffold, docker-compose, DB schema + migrations · scraping layer with rate limiting and cache · build the seed dataset (pick vertical, run the scrape, commit it) · scoring engine with all six factors + unit tests

**Day 2 — Product**
FastAPI routes · dedupe + validation pipeline · React app: search, table, detail drawer, weight controls, export · design pass · deploy all three services and verify end-to-end

**Day 3 — Submission**
README + ARCHITECTURE.md · three business essays (3–4 paragraphs each) · four short answers + employment-terms confirmation · record and edit the 2-minute video · assemble and send the email

---

## Non-code deliverables

**Three essays** (3–4 paragraphs each), drawing on the source material now gathered:
- *Caprae's mission* — transformation over financial engineering; M&A as a seven-year journey; AI as the operating lever
- *Why Caprae* — the operator-first, anti-gatekeeper thesis; "horsepower over mileage"; the specific match to Sourav's background
- *How Caprae is changing ETA and broader PE* — the governance work (CEO terminations, missing employment agreements, the investor-ratings survey), SaaS + MaaS productizing the search, founder-first capital

**Four short answers** — US working status, 40 hrs/week, why Caprae, expected salary — plus explicit confirmation of the 3-month probation, 9AM–6PM EST training hours, and off-hours availability. Sourav has confirmed the hours are acceptable.

**Video (~2 min)** — scripted to a beat sheet: the ETA insight (20s) → live demo of search → score → the "why this score" drawer (60s) → architecture and trade-offs (30s) → what I'd build next (10s).

---

## Verification

- `pytest` unit tests: scoring factors, weight math, dedupe blocking, email/phone validators
- Golden-file test on the scorer — a fixed input set must produce a fixed score vector, so refactors can't silently change results
- `ruff` + `mypy --strict` on backend, `tsc --noEmit` + ESLint on frontend
- Integration test hitting the live scrape path against a known-stable URL
- Manual end-to-end on the deployed URL: cold load, search, score, drawer, weight adjust, live refresh, CSV export opens clean in Excel
- Lighthouse pass on the frontend (accessibility and performance both matter for the UX score)
- README setup instructions followed from scratch in a clean container — if `docker compose up` doesn't work first try, the graders won't debug it

---

## Open items to confirm before Day 1

1. **Seed dataset vertical.** Recommendation: US home services (HVAC / plumbing / electrical) in one metro — the canonical search-fund buy box, well covered by OpenStreetMap, and full of owner-operated businesses. Alternative with more flair: one of the three niches from Caprae's own "riches in niches" post, which signals we read their content.
2. **Name and resume version** to use on the submission and repo.
3. **GitHub account** — `github.com/SvshSingh` per the resume, or a different one?
4. **Expected salary** figure for the short-answer section.
5. **Anthropic API key** availability, if we use the optional LLM signal-extraction pass (the build works without it — it degrades to regex/heuristic extraction).
