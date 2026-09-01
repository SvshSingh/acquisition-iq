"""Company discovery via the OpenStreetMap Overpass API.

Chosen over the obvious alternative on purpose. Google Places would return richer
records, but its terms forbid storing or redistributing the results, which makes
the committed seed dataset in this repo impossible and the whole thing
undemonstrable. OpenStreetMap is ODbL: we can scrape it, keep it, ship it in the
repo, and say exactly where every row came from. That trade — less data, but data
we are actually allowed to have — is stated openly in the README.

What OSM gives us is location, trade, and contact details. What it does not give
us is headcount or revenue. We do not invent them: a company with no size data
keeps `employee_count = None`, and the buy-box factor reports low confidence and
names the missing signal. Guessing would make the demo look better and the
product worthless.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.pipeline.scrapers.http import PoliteClient
from app.schemas import Company, Contact, VerificationStatus

logger = logging.getLogger(__name__)

# The buy box, expressed as OSM tags. Deliberately narrow: these are verticals a
# search fund actually buys — owner-operated, licence- or relationship-moated,
# recurring revenue, and a retiring principal — not every local business.
#
# The vertical set was chosen from measured OSM coverage, not from intuition. A
# survey of the Columbus bbox on 2026-09-02 found 1,005 named businesses, of
# which the home-services trades this project originally targeted accounted for
# 14 (craft=hvac: 7, craft=plumber: 2). Building a demo on a vertical the open
# data barely covers would have meant either a 14-row dataset or quietly
# switching to a source we are not licensed to redistribute. The trades stay in
# the set — they are the canonical thesis and they cost nothing — but the volume
# comes from verticals OSM actually knows about.
TRADE_TAGS: dict[str, str] = {
    # Healthcare practices: licence-moated, owner-operated, classic ETA targets.
    "amenity=veterinary": "Veterinary",
    "amenity=dentist": "Dental",
    "shop=optician": "Optometry",
    "shop=hearing_aids": "Audiology",
    # Services with recurring revenue and physical switching costs.
    "shop=car_repair": "Auto repair",
    "shop=storage_rental": "Self-storage",
    "shop=funeral_directors": "Funeral services",
    "shop=dry_cleaning": "Dry cleaning",
    "shop=laundry": "Laundry",
    "amenity=childcare": "Childcare",
    # Book-of-business firms — the value is the renewal list, not the premises.
    "office=insurance": "Insurance agency",
    "office=accountant": "Accounting",
    # The original home-services thesis. Thin in OSM, kept because it is the
    # textbook search-fund vertical and the scoring engine was tuned on it.
    "craft=hvac": "HVAC",
    "craft=plumber": "Plumbing",
    "craft=electrician": "Electrical",
    "craft=roofer": "Roofing",
    "shop=hvac": "HVAC",
    "craft=heating_engineer": "HVAC",
}

# Columbus, OH. Roughly the I-270 outerbelt plus inner suburbs.
COLUMBUS_OH_BBOX = (39.80, -83.20, 40.15, -82.77)


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def as_overpass(self) -> str:
        return f"{self.south},{self.west},{self.north},{self.east}"


def build_query(bbox: BoundingBox, tags: dict[str, str], timeout: int = 90) -> str:
    """Overpass QL for every matching business in the box.

    `out center tags` returns a single representative coordinate for ways and
    relations instead of their full geometry — we want a pin per business, not
    building outlines, and asking for geometry we discard is rude to a free
    public API.
    """
    clauses = "\n  ".join(
        f'nwr[{tag.split("=")[0]}="{tag.split("=")[1]}"]({bbox.as_overpass()});'
        for tag in tags
    )
    return f"[out:json][timeout:{timeout}];\n(\n  {clauses}\n);\nout center tags;"


def _first(tags: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = tags.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _industry_for(tags: dict[str, str]) -> str | None:
    for tag, label in TRADE_TAGS.items():
        key, value = tag.split("=")
        if tags.get(key) == value:
            return label
    return None


def _founded_year(tags: dict[str, str]) -> int | None:
    """OSM `start_date` is loosely formatted — "1979", "1979-06", "~1979"."""
    raw = _first(tags, "start_date")
    if not raw:
        return None
    digits = "".join(c for c in raw[:4] if c.isdigit())
    if len(digits) != 4:
        return None
    year = int(digits)
    return year if 1800 <= year <= date.today().year else None


def element_to_company(element: dict[str, Any]) -> Company | None:
    """Map one Overpass element onto our domain type.

    Returns None for anything unnamed — an unnamed node is a map annotation, not
    a business we can research or contact.
    """
    tags: dict[str, str] = element.get("tags") or {}
    name = _first(tags, "name", "operator")
    if not name:
        return None

    osm_type = str(element.get("type", "node"))
    osm_id = element.get("id")
    center = element.get("center") or {}
    latitude = element.get("lat", center.get("lat"))
    longitude = element.get("lon", center.get("lon"))

    website = _first(tags, "website", "contact:website", "url")
    if website and "://" not in website:
        website = f"https://{website}"

    email = _first(tags, "email", "contact:email")
    phone = _first(tags, "phone", "contact:phone", "telephone")

    contacts: list[Contact] = []
    if email or phone:
        contacts.append(
            Contact(
                email=email,
                # Straight from the map, unverified. The validation pass decides
                # whether it is deliverable; claiming otherwise here would put a
                # false "verified" badge in front of the user.
                email_status=VerificationStatus.UNKNOWN,
                phone=phone,
                phone_valid=None,
            )
        )

    return Company(
        id=f"osm:{osm_type}:{osm_id}",
        name=name,
        website=website,
        industry=_industry_for(tags),
        city=_first(tags, "addr:city"),
        state=_first(tags, "addr:state") or "OH",
        postcode=_first(tags, "addr:postcode"),
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
        founded_year=_founded_year(tags),
        contacts=contacts,
        source="openstreetmap",
        source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        first_seen=date.today(),
        raw=element,
    )


class OverpassClient:
    """Thin client over the Overpass API.

    Overpass is a free service run on donated capacity with a published usage
    policy: keep the query count low, set a timeout, and cache. All three are
    handled here plus the shared `PoliteClient` cache, so re-running the seed
    collector is one cache hit rather than another query against their servers.
    """

    def __init__(self, client: PoliteClient, endpoints: list[str] | None = None) -> None:
        from app.config import settings

        self._client = client
        self._endpoints = endpoints or settings.overpass_endpoints

    async def discover(
        self,
        bbox: BoundingBox,
        tags: dict[str, str] | None = None,
        *,
        use_cache: bool = True,
    ) -> list[Company]:
        """Query each endpoint in turn until one answers.

        Overpass instances rate-limit, go down for maintenance, and return HTML
        error pages with a 200. All three look different and all three mean the
        same thing: ask the next mirror.
        """
        query = build_query(bbox, tags or TRADE_TAGS)
        encoded = _urlencode(query)

        for endpoint in self._endpoints:
            # GET with the query in the URL, so the response lands in the same
            # response cache as everything else rather than needing its own path.
            result = await self._client.get_or_none(
                f"{endpoint}?data={encoded}", use_cache=use_cache
            )
            if result is None or not result.ok:
                status = result.status_code if result else "no response"
                logger.warning("Overpass %s returned %s; trying next", endpoint, status)
                continue

            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError:
                # An HTML error page served with a 200. Not a mirror we can use.
                logger.warning(
                    "Overpass %s returned non-JSON (%d bytes); trying next",
                    endpoint,
                    len(result.text),
                )
                continue

            elements = payload.get("elements", [])
            companies = [
                c for c in (element_to_company(e) for e in elements) if c is not None
            ]
            logger.info(
                "Overpass %s: %d elements -> %d named businesses (cache hit: %s)",
                endpoint,
                len(elements),
                len(companies),
                result.from_cache,
            )
            return companies

        logger.error("Every Overpass endpoint failed: %s", ", ".join(self._endpoints))
        return []


def _urlencode(query: str) -> str:
    from urllib.parse import quote

    return quote(query, safe="")


__all__ = [
    "COLUMBUS_OH_BBOX",
    "TRADE_TAGS",
    "BoundingBox",
    "OverpassClient",
    "build_query",
    "element_to_company",
]
