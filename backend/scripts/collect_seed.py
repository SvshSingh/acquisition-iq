"""Build the committed seed dataset.

Run once, commit the output. The demo then loads instantly and never depends on
a third-party API being up while someone is watching — but the same code path
runs live behind "Refresh from source", so nothing here is a mock.

    python scripts/collect_seed.py --limit 250 --out ../data/seed_leads.json

Everything it writes is either observed from OpenStreetMap and the company's own
website, or computed from those. Nothing is invented: a company whose size we
cannot observe keeps `employee_count = None` and the buy-box factor reports low
confidence rather than a fabricated number.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Allow `python scripts/collect_seed.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cache.base import NullCache
from app.pipeline.dedupe import annotate_chain_locations, deduplicate
from app.pipeline.geo import haversine_km
from app.pipeline.scrapers.http import PoliteClient
from app.pipeline.scrapers.overpass import (
    COLUMBUS_OH_BBOX,
    BoundingBox,
    OverpassClient,
)
from app.pipeline.scrapers.website import crawl_site
from app.pipeline.validate import MxResolver, validate_contacts
from app.schemas import Company
from app.scoring.engine import score_company

logger = logging.getLogger("collect_seed")

# Radius for the "is this niche fragmented here?" question. 15km is about a
# metro's worth of drive time — the distance over which two operators in the
# same trade genuinely compete for the same customer, and therefore the distance
# over which a roll-up thesis makes sense.
PEER_RADIUS_KM = 15.0


def annotate_peer_density(companies: list[Company], radius_km: float = PEER_RADIUS_KM) -> None:
    """Count same-industry operators within `radius_km` of each company.

    This is the one factor input that cannot come from a single record — it is a
    property of the *neighbourhood*, which is exactly why a lead-gen tool that
    scrapes companies one at a time cannot produce it. Measuring it per company
    rather than per vertical matters: an auto shop in a dense corridor and one on
    the edge of the metro face different consolidation maths, and collapsing them
    to a single per-vertical number would throw that away.

    O(n²) over the industry buckets, not the whole set. At a few hundred rows per
    metro that is milliseconds; the blocking key is the industry.
    """
    by_industry: dict[str, list[Company]] = {}
    for company in companies:
        if company.industry and company.latitude is not None and company.longitude is not None:
            by_industry.setdefault(company.industry, []).append(company)

    for peers in by_industry.values():
        for company in peers:
            assert company.latitude is not None and company.longitude is not None
            count = 0
            for other in peers:
                if other is company:
                    continue
                assert other.latitude is not None and other.longitude is not None
                if (
                    haversine_km(
                        company.latitude, company.longitude, other.latitude, other.longitude
                    )
                    <= radius_km
                ):
                    count += 1
            company.peer_count_in_niche = count

    # A company with no coordinates gets nothing rather than a guess — the
    # factor then reports the signal as missing, which is the truth.
    for company in companies:
        if company.peer_count_in_niche is None:
            logger.debug("no peer count for %s (missing coordinates or industry)", company.name)


async def enrich(
    client: PoliteClient, companies: list[Company], concurrency: int = 8
) -> None:
    """Crawl each company's own site and fold the signals in.

    Bounded separately from the HTTP client's own per-host limit: that one stops
    us hammering a single host, this one stops us opening 500 sites at once.
    """
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def one(company: Company) -> None:
        nonlocal done
        if not company.website:
            return
        async with semaphore:
            signals, contacts = await crawl_site(client, company.website)
        company.web = signals
        if contacts and not company.contacts:
            company.contacts = contacts
        elif contacts and company.contacts:
            # Merge rather than replace: the two sources are complementary. OSM
            # usually carries the phone number, the website usually carries the
            # mailbox, and only the website ever names the owner.
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
            logger.info("crawled %d sites", done)

    await asyncio.gather(*(one(c) for c in companies))


async def collect(limit: int, *, use_cache: bool) -> dict[str, object] | None:
    """Discover, enrich and score. Returns the payload; the caller writes it.

    Writing is left to the synchronous caller on purpose — blocking file I/O
    inside the event loop stalls every in-flight request, and this function is
    holding a few hundred of them.
    """
    # NullCache unless explicitly asked otherwise: the shared cache is backed by
    # Postgres, and this script has to run from a clean clone with no database
    # up. The live "refresh from source" path in the API is where the cache
    # earns its keep.
    async with PoliteClient(cache=None if use_cache else NullCache()) as client:
        logger.info("discovering companies from OpenStreetMap...")
        companies = await OverpassClient(client).discover(
            BoundingBox(*COLUMBUS_OH_BBOX), use_cache=use_cache
        )
        logger.info("discovered %d named businesses", len(companies))
        if not companies:
            logger.error("no companies discovered; aborting without writing")
            return None

        # Dedupe before anything expensive: crawling the same site twice is
        # wasted politeness budget as well as wasted time.
        result = deduplicate(companies)
        if result.removed:
            logger.info("merged %d duplicate records", result.removed)
            for match in result.matches[:10]:
                logger.info("  %s <- %s (%s)", match.kept_id, match.dropped_id, match.reason)
        companies = result.companies

        # Both of these read the *whole* discovered set, not the slice we keep.
        # Peer density and chain size are properties of the neighbourhood, and
        # measuring them over a truncated sample would understate both.
        annotate_peer_density(companies)
        chains = annotate_chain_locations(companies)
        logger.info("flagged %d companies as multi-location chains", chains)

        # Prefer companies with a website: they are the ones the crawler can say
        # anything about, and a seed set of blank records demos nothing. Some
        # site-less companies are kept deliberately so the UI has to handle the
        # low-confidence case on real data rather than only in tests.
        with_site = [c for c in companies if c.website]
        without_site = [c for c in companies if not c.website]
        keep_without = min(len(without_site), max(0, limit - len(with_site)) or limit // 10)
        selected = (with_site + without_site[:keep_without])[:limit]
        logger.info(
            "selected %d companies (%d with a website, %d without)",
            len(selected),
            sum(1 for c in selected if c.website),
            sum(1 for c in selected if not c.website),
        )

        await enrich(client, selected)

    # Validation last, once every source has contributed what it has. Running it
    # earlier would mean validating an OSM phone number and then discarding the
    # verdict when the website turns up a better contact.
    resolver = MxResolver()
    validated = 0
    for company in selected:
        if company.contacts:
            company.contacts = await validate_contacts(company.contacts, resolver)
            validated += 1
    logger.info("validated contacts for %d companies", validated)

    scored = [(c, score_company(c)) for c in selected]
    scored.sort(key=lambda pair: pair[1].score, reverse=True)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "OpenStreetMap via Overpass API (ODbL), plus each company's own website",
        "licence": "ODbL 1.0 — https://www.openstreetmap.org/copyright",
        "bbox": list(COLUMBUS_OH_BBOX),
        "peer_radius_km": PEER_RADIUS_KM,
        "count": len(scored),
        "companies": [c.model_dump(mode="json") for c, _ in scored],
    }
    scores = sorted(s.score for _, s in scored)
    industries = Counter(c.industry for c, _ in scored)
    logger.info(
        "score range %.1f-%.1f, median %.1f, IQR %.1f-%.1f",
        scores[0],
        scores[-1],
        scores[len(scores) // 2],
        scores[len(scores) // 4],
        scores[3 * len(scores) // 4],
    )
    logger.info("verticals: %s", dict(industries.most_common()))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=250, help="max companies to keep")
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[2] / "data" / "seed_leads.json"
    )
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

    payload = asyncio.run(collect(args.limit, use_cache=args.use_cache))
    if payload is None:
        raise SystemExit(1)

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s (%s companies)", out_path, payload["count"])


if __name__ == "__main__":
    main()
