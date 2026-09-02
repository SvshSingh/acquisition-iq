"""Markets the collector can run against.

A market is a parameter, never a constant baked into a query. The first version
of this pipeline hardcoded one bounding box, and the cost of that showed up
immediately: "does this generalise?" is the first question anyone asks of a
scraper, and the honest answer has to be a second market collected by the same
code path rather than an assurance.

Each market carries what both discovery sources need — a bounding box for
OpenStreetMap, and a state/county pair for the licensing boards — so adding a
market is a data change, not a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.pipeline.scrapers.overpass import BoundingBox


@dataclass(frozen=True)
class Market:
    key: str
    label: str
    bbox: BoundingBox
    state: str
    counties: tuple[str, ...] = ()
    # The core cities of the metro. Discovery runs county-wide because that is
    # the granularity licensing boards publish, then this narrows to the metro
    # a searcher would actually drive around in a day.
    core_cities: tuple[str, ...] = field(default_factory=tuple)

    def is_core(self, city: str | None) -> bool:
        if not self.core_cities:
            return True
        if not city:
            return False
        return city.strip().upper() in {c.upper() for c in self.core_cities}


# Caprae is headquartered in Glendale. Running the primary market on their own
# doorstep is deliberate: every row in the demo is a business the reader could
# drive past, which makes the scores arguable in a way that an anonymous metro
# never is. The handbook does not ask for any particular geography — this is our
# choice, and the README says so rather than implying otherwise.
GLENDALE = Market(
    key="glendale",
    label="Glendale-Pasadena-Burbank, CA",
    bbox=BoundingBox(34.05, -118.40, 34.28, -118.05),
    state="CA",
    counties=("Los Angeles",),
    core_cities=(
        "GLENDALE", "PASADENA", "BURBANK", "NORTH HOLLYWOOD", "SUN VALLEY",
        "TUJUNGA", "SUNLAND", "LA CRESCENTA", "MONTROSE", "EAGLE ROCK",
        "ALTADENA", "SOUTH PASADENA", "SIERRA MADRE", "ARCADIA", "TOLUCA LAKE",
        "VAN NUYS", "SHERMAN OAKS", "STUDIO CITY", "LOS ANGELES",
    ),
)

# The second market exists to prove the collector generalises, and to test the
# counter-thesis: search-fund returns are argued to be better in secondary
# Midwest markets, where there is less aggregator competition. If the scorer is
# working, LA targets should skew more consolidated than Columbus ones.
COLUMBUS = Market(
    key="columbus",
    label="Columbus, OH",
    bbox=BoundingBox(39.80, -83.20, 40.15, -82.77),
    state="OH",
    counties=("Franklin",),
    core_cities=(),
)

MARKETS: dict[str, Market] = {m.key: m for m in (GLENDALE, COLUMBUS)}

DEFAULT_MARKET = GLENDALE.key


def get_market(key: str) -> Market:
    try:
        return MARKETS[key.lower()]
    except KeyError:
        known = ", ".join(sorted(MARKETS))
        raise KeyError(f"unknown market {key!r}; known markets: {known}") from None


__all__ = ["COLUMBUS", "DEFAULT_MARKET", "GLENDALE", "MARKETS", "Market", "get_market"]
