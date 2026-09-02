"""Build a committed seed dataset for one market.

    python scripts/collect_seed.py --market glendale --limit 250
    python scripts/collect_seed.py --market columbus --limit 250

Run once per market, commit the output. The demo then loads instantly and never
depends on a third-party API being up while someone is watching — but the same
code path runs live behind "Refresh from source", so nothing here is a mock.

The market is a parameter, not a constant, so "does this generalise beyond one
city?" is answered by running the flag rather than by rewriting the collector.
Glendale is the market that ships as a committed dataset; Columbus is defined
and runnable but not collected, because Ohio has no equivalent licence register
and the two would not be comparing like with like. `markets.py` says so at the
definition rather than leaving it to be inferred.

Two sources feed it, because they are good at opposite things. Licence registers
publish structure — ownership form, issue date, whether anyone is employed — on
every row. OpenStreetMap publishes presence, including the website that the
crawler needs and that no licence register carries. Where both know a business,
the merged record is far better than either alone.

Nothing here is invented. A company whose size we cannot observe keeps
`employee_count = None`, and the scoring engine reports the gap rather than
filling it with a plausible number.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Allow `python scripts/collect_seed.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache.base import NullCache
from app.pipeline.dedupe import annotate_chain_locations, deduplicate
from app.pipeline.markets import DEFAULT_MARKET, MARKETS, Market, get_market
from app.pipeline.normalize import normalise_name
from app.pipeline.peers import annotate_peer_density
from app.pipeline.scrapers.http import PoliteClient
from app.pipeline.scrapers.website import crawl_site
from app.pipeline.sources.cslb import CslbSource
from app.pipeline.sources.osm import HOME_SERVICES_TAGS, OverpassSource
from app.pipeline.validate import MxResolver, validate_contacts
from app.schemas import Company
from app.scoring.engine import score_company

logger = logging.getLogger("collect_seed")


def merge_sources(primary: list[Company], secondary: list[Company]) -> tuple[list[Company], int]:
    """Fold map records into licence records, keyed on name within a city.

    The licence register is authoritative for identity and for everything
    structured; the map contributes the two things it alone has — a website and
    coordinates. Matching is deliberately conservative: normalised name plus
    city, no fuzzy threshold. A wrong match here would staple one business's
    website onto another's licence, and a user clicking through to check the
    evidence would find a different company. A missed match costs only a blank
    field, which the scorer already knows how to report.

    Returns the merged list and how many enrichments landed.
    """
    index: dict[tuple[str, str], Company] = {}
    for company in primary:
        key = (normalise_name(company.name), (company.city or "").strip().upper())
        index.setdefault(key, company)

    enriched = 0
    unmatched: list[Company] = []

    for candidate in secondary:
        key = (normalise_name(candidate.name), (candidate.city or "").strip().upper())
        match = index.get(key)
        if match is None:
            unmatched.append(candidate)
            continue

        updates: dict[str, object] = {}
        if not match.website and candidate.website:
            updates["website"] = candidate.website
        if match.latitude is None and candidate.latitude is not None:
            updates["latitude"] = candidate.latitude
            updates["longitude"] = candidate.longitude
        if updates:
            for field, value in updates.items():
                setattr(match, field, value)
            enriched += 1

    # Map-only businesses are kept. They are real companies the licence register
    # did not match — often trading under a name that differs from the licensed
    # entity — and dropping them would quietly narrow the market.
    return primary + unmatched, enriched


def select_sample(companies: list[Company], limit: int) -> list[Company]:
    """Choose the seed slice: proportional across trades, richest records first.

    Taking the first N in source order produced a dataset that was 97%
    electricians, purely because the electrical export happened to be read first.
    A demo whose trade mix is an artefact of file ordering misrepresents the
    market it claims to describe, and the fragmentation factor — which compares
    a company against its own niche — would be reading a distorted one.

    So the sample is stratified by trade in proportion to the real population,
    and within each trade it is spread evenly rather than sorted by completeness.

    That second part was a correction. Sorting by completeness seemed obviously
    right — show the UI the rows it can say most about — and it quietly produced
    a dataset where 97% of companies had a named owner against 10% of the
    population it claimed to represent. The demo looked better and meant less:
    a signal every row shares cannot rank anything, and succession's spread fell
    by a third. A seed that misreports its own market is the failure this whole
    product is pitched against, so the only preference kept is for the handful of
    companies with a website, which are too rare to distort anything and are the
    only rows that exercise the crawler at all.
    """
    by_trade: dict[str, list[Company]] = {}
    for company in companies:
        by_trade.setdefault(company.industry or "Unknown", []).append(company)

    total = len(companies)
    quotas = {
        trade: max(1, round(limit * len(group) / total)) for trade, group in by_trade.items()
    }

    selected: list[Company] = []
    for trade, group in sorted(by_trade.items(), key=lambda kv: -len(kv[1])):
        quota = quotas[trade]
        with_site = [c for c in group if c.website]
        rest = [c for c in group if not c.website]

        take = with_site[:quota]
        remaining = quota - len(take)
        if remaining > 0 and rest:
            # Evenly spaced rather than the first N: the exports arrive ordered
            # by licence number, so the head of the list is the oldest licences
            # and taking it wholesale would skew the age distribution the
            # succession factor depends on.
            step = max(1, len(rest) // remaining)
            take.extend(rest[::step][:remaining])
        selected.extend(take)

    # Rounding can overshoot or undershoot; top up from the largest trade and
    # trim from the end rather than silently returning the wrong count.
    if len(selected) < limit:
        chosen = {c.id for c in selected}
        for company in companies:
            if len(selected) >= limit:
                break
            if company.id not in chosen:
                selected.append(company)
    return selected[:limit]


async def enrich_websites(
    client: PoliteClient, companies: list[Company], concurrency: int = 8
) -> int:
    """Crawl each company's own site and fold the signals in.

    Bounded separately from the HTTP client's per-host limit: that one stops us
    hammering a single host, this one stops us opening hundreds at once.
    """
    targets = [c for c in companies if c.website]
    if not targets:
        return 0

    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def one(company: Company) -> None:
        nonlocal done
        async with semaphore:
            signals, contacts = await crawl_site(client, company.website or "")
        company.web = signals
        if contacts and not company.contacts:
            company.contacts = contacts
        elif contacts and company.contacts:
            # The sources are complementary: the licence register carries the
            # phone, the website carries the mailbox, and only the website ever
            # names the owner.
            existing, found = company.contacts[0], contacts[0]
            merged = {
                field: getattr(found, field)
                for field in ("email", "name", "title")
                if not getattr(existing, field) and getattr(found, field)
            }
            if found.is_decision_maker:
                merged["is_decision_maker"] = True
            if merged:
                company.contacts[0] = existing.model_copy(update=merged)
        company.last_refreshed = datetime.now(UTC)
        done += 1
        if done % 25 == 0:
            logger.info("crawled %d/%d sites", done, len(targets))

    await asyncio.gather(*(one(c) for c in targets))
    return done


async def validate_all(companies: list[Company], concurrency: int = 24) -> int:
    """Validate every contact, concurrently across companies.

    Most companies hold a single contact, so validating per company would
    serialise the stage into one DNS round trip after another. The per-domain
    memo inside MxResolver means the fan-out costs the resolvers almost nothing:
    the queries collapse onto the handful of distinct mail domains in play.
    """
    resolver = MxResolver()
    with_contacts = [c for c in companies if c.contacts]
    semaphore = asyncio.Semaphore(concurrency)

    async def one(company: Company) -> None:
        async with semaphore:
            company.contacts = await validate_contacts(company.contacts, resolver)

    await asyncio.gather(*(one(c) for c in with_contacts))
    return len(with_contacts)


async def collect(market: Market, limit: int, *, use_cache: bool) -> dict[str, object] | None:
    """Discover, merge, enrich and score. Returns the payload; caller writes it.

    Writing is left to the synchronous caller: blocking file I/O inside the
    event loop stalls every in-flight request, and this holds hundreds.
    """
    # NullCache unless asked otherwise — the shared cache is Postgres-backed and
    # this has to run from a clean clone with no database up.
    async with PoliteClient(cache=None if use_cache else NullCache()) as client:
        licensed = await CslbSource().discover(market)
        osm_tags = market.osm_tags or HOME_SERVICES_TAGS
        mapped = await OverpassSource(
            client, osm_tags, use_cache=use_cache
        ).discover(market)

        if licensed:
            companies, enriched = merge_sources(licensed, mapped)
            logger.info(
                "merged: %d licence records + %d map records -> %d companies "
                "(%d enriched with a website or coordinates)",
                len(licensed),
                len(mapped),
                len(companies),
                enriched,
            )
        else:
            # No licence coverage for this market (anything outside California).
            companies = mapped
            logger.info("no licence data for %s; using map data alone", market.label)

        if not companies:
            logger.error("no companies discovered; aborting without writing")
            return None

        result = deduplicate(companies)
        if result.removed:
            logger.info("merged %d duplicate records", result.removed)
        companies = result.companies

        # Both read the whole discovered set, not the slice we keep — peer
        # density and chain size are properties of the neighbourhood, and
        # measuring them over a truncated sample would understate both.
        measured = annotate_peer_density(companies)
        chains = annotate_chain_locations(companies)
        logger.info("peer density on %d companies; %d flagged as chains", measured, chains)

        selected = select_sample(companies, limit)
        logger.info(
            "selected %d of %d (%d with a website) — %s",
            len(selected),
            len(companies),
            sum(1 for c in selected if c.website),
            dict(Counter(c.industry for c in selected).most_common()),
        )

        crawled = await enrich_websites(client, selected)
        logger.info("crawled %d sites", crawled)

    started = time.monotonic()
    validated = await validate_all(selected)
    logger.info("validated %d companies in %.1fs", validated, time.monotonic() - started)

    scored = sorted(
        ((c, score_company(c)) for c in selected), key=lambda pair: pair[1].score, reverse=True
    )

    scores = sorted(s.score for _, s in scored)
    n = len(scores)
    logger.info(
        "score range %.1f-%.1f | median %.1f | IQR %.1f-%.1f",
        scores[0],
        scores[-1],
        scores[n // 2],
        scores[n // 4],
        scores[3 * n // 4],
    )
    logger.info("verticals: %s", dict(Counter(c.industry for c, _ in scored).most_common()))

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "market": {"key": market.key, "label": market.label, "state": market.state},
        "sources": [
            "California Contractors State License Board public data portal "
            "(public domain, California Conditions of Use)",
            "OpenStreetMap via the Overpass API (ODbL 1.0 — "
            "https://www.openstreetmap.org/copyright)",
            "Each company's own website",
        ],
        "count": len(scored),
        "companies": [c.model_dump(mode="json") for c, _ in scored],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market", default=DEFAULT_MARKET, choices=sorted(MARKETS), help="which market to collect"
    )
    parser.add_argument("--limit", type=int, default=250, help="max companies to keep")
    parser.add_argument("--out", type=Path, default=None, help="output path")
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="route fetches through the shared cache (needs a reachable database)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    market = get_market(args.market)
    out_path: Path = args.out or (
        Path(__file__).resolve().parents[2] / "data" / f"seed_{market.key}.json"
    )

    payload = asyncio.run(collect(market, args.limit, use_cache=args.use_cache))
    if payload is None:
        raise SystemExit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s (%s companies)", out_path, payload["count"])


if __name__ == "__main__":
    main()
