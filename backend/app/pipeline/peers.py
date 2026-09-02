"""Peer density — how crowded is this company's niche, right here?

This is the one factor input that cannot come from a single record. It is a
property of the *neighbourhood*, which is exactly why a lead-gen tool that
scrapes companies one at a time cannot produce it, and why it is worth having.

Two ways to measure it, because the two discovery sources carry different
geography. Map data gives coordinates, so peers are counted inside a real
radius. Licence registers give a postal address and no coordinates, so peers are
counted inside the postcode. The postcode version is coarser and the factor is
told which one it got.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.pipeline.geo import haversine_km
from app.schemas import Company

logger = logging.getLogger(__name__)

# About a metro's worth of drive time — the distance over which two operators in
# the same trade genuinely compete for the same customer, and therefore the
# distance over which a roll-up thesis makes sense.
PEER_RADIUS_KM = 15.0


def annotate_peer_density(companies: list[Company], radius_km: float = PEER_RADIUS_KM) -> int:
    """Fill in `peer_count_in_niche`. Returns how many companies got a count.

    Companies with coordinates are counted by distance; the rest fall back to
    their postcode. A company with neither is left at `None` rather than zero —
    zero would read as "we checked and this niche is empty", which is a
    different and much stronger claim than "we could not check".
    """
    by_industry: dict[str, list[Company]] = defaultdict(list)
    for company in companies:
        if company.industry:
            by_industry[company.industry].append(company)

    annotated = 0

    for peers in by_industry.values():
        located = [c for c in peers if c.latitude is not None and c.longitude is not None]

        # Postcode buckets, used both for the fallback and to keep the radius
        # pass from having to look at companies it cannot measure.
        by_postcode: dict[str, int] = defaultdict(int)
        for company in peers:
            key = _postcode_key(company)
            if key:
                by_postcode[key] += 1

        for company in peers:
            if company.latitude is not None and company.longitude is not None:
                count = 0
                for other in located:
                    if other is company:
                        continue
                    assert other.latitude is not None and other.longitude is not None
                    if (
                        haversine_km(
                            company.latitude,
                            company.longitude,
                            other.latitude,
                            other.longitude,
                        )
                        <= radius_km
                    ):
                        count += 1
                company.peer_count_in_niche = count
                annotated += 1
                continue

            key = _postcode_key(company)
            if key and by_postcode[key] > 0:
                # Minus one for the company itself.
                company.peer_count_in_niche = by_postcode[key] - 1
                annotated += 1

    unmeasured = sum(1 for c in companies if c.peer_count_in_niche is None)
    if unmeasured:
        logger.info("%d companies have no geography to measure peers against", unmeasured)
    return annotated


def _postcode_key(company: Company) -> str | None:
    """Postcode trimmed to its 5-digit form. ZIP+4 varies within one street."""
    if not company.postcode:
        return None
    digits = "".join(ch for ch in company.postcode if ch.isdigit())
    return digits[:5] if len(digits) >= 5 else None


__all__ = ["PEER_RADIUS_KM", "annotate_peer_density"]
