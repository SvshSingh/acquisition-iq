"""OpenStreetMap discovery, behind the shared source interface.

Demoted from primary to secondary on measured evidence rather than a hunch. A
survey of the Columbus bounding box returned 1,005 named businesses, of which
the home-services trades accounted for **fourteen** — `craft=hvac` 7,
`craft=plumber` 2, `craft=electrician` 0, `craft=roofer` 4. US trade contractors
have no walk-in storefront and are rarely mapped, exactly as the project brief
predicted before anyone had checked.

It stays in the pipeline for the one thing no licensing board provides: a
**website**. A licence record cannot tell you the company's site is a 2011
FrontPage page with no HTTPS, and that is half the scoring thesis. Where OSM has
a business the licence board also has, the two combine into a far better record
than either alone.
"""

from __future__ import annotations

import logging

from app.pipeline.markets import Market
from app.pipeline.scrapers.http import PoliteClient
from app.pipeline.scrapers.overpass import TRADE_TAGS, OverpassClient
from app.schemas import Company

logger = logging.getLogger(__name__)

# The trade tags, for markets run on OSM alone. Kept separate from the wider
# TRADE_TAGS set so a licensing-board market and a map-only market can ask for
# different things without one silently reshaping the other.
HOME_SERVICES_TAGS: dict[str, str] = {
    "craft=hvac": "HVAC",
    "shop=hvac": "HVAC",
    "craft=heating_engineer": "HVAC",
    "craft=plumber": "Plumbing",
    "craft=electrician": "Electrical",
    "craft=roofer": "Roofing",
}


class OverpassSource:
    """Discovery via the Overpass API."""

    name = "openstreetmap"

    def __init__(
        self,
        client: PoliteClient,
        tags: dict[str, str] | None = None,
        *,
        use_cache: bool = True,
    ) -> None:
        self._overpass = OverpassClient(client)
        self._tags = tags if tags is not None else TRADE_TAGS
        self._use_cache = use_cache

    async def discover(self, market: Market) -> list[Company]:
        companies = await self._overpass.discover(
            market.bbox, self._tags, use_cache=self._use_cache
        )
        kept = [c for c in companies if market.is_core(c.city)] if market.core_cities else companies
        logger.info(
            "OpenStreetMap: %d discovered -> %d in %s", len(companies), len(kept), market.label
        )
        return kept


__all__ = ["HOME_SERVICES_TAGS", "OverpassSource"]
