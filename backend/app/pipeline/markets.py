"""Markets the collector can run against.

A market is a parameter, never a constant baked into a query. The first version
of this pipeline hardcoded one bounding box, which made "does this generalise?"
unanswerable without a rewrite.

Each market carries what both discovery sources need — a bounding box for
OpenStreetMap, a state and county for the licensing boards, and the OSM tag set
appropriate to it — so adding a market is a data change, not a code change.

Only Glendale ships as a committed dataset. Columbus is defined and runnable but
deliberately not collected; see the note above it for why the two would not be
comparable even if it were.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.pipeline.scrapers.overpass import TRADE_TAGS, BoundingBox


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
    # Which OSM tags to ask for. A market with no licence register has to get
    # its whole population from the map, and the trade tags are too sparse in
    # the US to carry that alone — so such markets widen to the verticals OSM
    # actually covers rather than returning a dozen rows.
    osm_tags: dict[str, str] | None = None

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

# A second market, supported but not shipped as a committed dataset.
#
# It exists to keep the market genuinely parameterised rather than to pad the
# submission: `--market columbus` runs the same code path end to end and returns
# a real result. What it cannot do is compare like with like. Ohio licenses
# contractors through a different board with its own acquisition problem, so
# there is no licence register behind this market, and the OSM trade tags that
# would stand in for one return roughly a dozen businesses across the whole
# metro (measured, not assumed — see `sources/osm.py`).
#
# So it widens to the verticals OSM does cover well in the US: veterinary,
# dental, auto repair, self-storage and the rest. Those are legitimate ETA
# targets, but they are a different buy box from the California trades, and the
# README says so rather than implying two comparable datasets.
COLUMBUS = Market(
    key="columbus",
    label="Columbus, OH",
    bbox=BoundingBox(39.80, -83.20, 40.15, -82.77),
    state="OH",
    counties=("Franklin",),
    core_cities=(),
    osm_tags=TRADE_TAGS,
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
