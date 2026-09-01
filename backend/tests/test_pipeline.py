"""Pipeline tests: URL handling, politeness, extraction, peer density.

Nothing here touches the network. The HTTP tests run against `respx`, so the
retry, robots and circuit-breaker behaviour is asserted rather than assumed —
those paths only ever fire when something is already going wrong, which is
exactly when you cannot afford them to be untested.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.cache.base import NullCache, http_cache_key
from app.pipeline.scrapers import website
from app.pipeline.scrapers.http import (
    CircuitOpenError,
    PoliteClient,
    RobotsDisallowedError,
    _Breaker,
    host_of,
    normalise_url,
    user_agent,
)
from app.pipeline.scrapers.overpass import (
    BoundingBox,
    build_query,
    element_to_company,
)
from app.schemas import Company

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from collect_seed import annotate_peer_density, haversine_km  # noqa: E402

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def client() -> PoliteClient:
    """A client that never reaches the shared (Postgres-backed) cache."""
    return PoliteClient(cache=NullCache())


# --------------------------------------------------------------------------- #
# url handling
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com/"),
        ("https://Example.COM/path#frag", "https://example.com/path"),
        ("https://example.com./x", "https://example.com/x"),
        ("http://example.com?a=1", "http://example.com/?a=1"),
    ],
)
def test_normalise_url(raw, expected):
    assert normalise_url(raw) == expected


def test_normalised_urls_share_a_cache_key():
    """Two spellings of one URL must not cost two fetches."""
    same = {
        http_cache_key("GET", normalise_url(spelling))
        for spelling in ("https://Example.com/x#top", "example.com/x", "Example.COM/x")
    }
    assert len(same) == 1

    # Method and path still separate entries — collapsing those would serve the
    # wrong body.
    assert http_cache_key("GET", "https://x.com/a") != http_cache_key("GET", "https://x.com/b")
    assert http_cache_key("get", "https://x.com/") == http_cache_key("GET", "https://x.com/")


def test_user_agent_is_identifiable_and_has_no_email():
    """overpass-api.de 406s any UA containing an email address."""
    ua = user_agent()
    assert ua.startswith("AcquisitionIQ/")
    assert "@" not in ua
    assert "+http" in ua


def test_host_of_handles_missing_scheme():
    assert host_of("example.com/path") == "example.com"


# --------------------------------------------------------------------------- #
# circuit breaker
# --------------------------------------------------------------------------- #

def test_breaker_opens_after_threshold_then_closes_after_cooldown():
    breaker = _Breaker()
    for _ in range(3):
        breaker.record_failure(threshold=3)
    assert breaker.is_open(cooldown=300)
    # A cooldown of zero means the window has already elapsed.
    assert not breaker.is_open(cooldown=0)
    # ...and closing it resets the count, so one bad patch does not doom a host.
    assert breaker.consecutive_failures == 0


def test_breaker_success_clears_partial_failures():
    breaker = _Breaker()
    breaker.record_failure(threshold=3)
    breaker.record_failure(threshold=3)
    breaker.record_success()
    breaker.record_failure(threshold=3)
    assert not breaker.is_open(cooldown=300)


# --------------------------------------------------------------------------- #
# http client
# --------------------------------------------------------------------------- #

@respx.mock
async def test_retries_then_succeeds():
    respx.get("https://retry.test/robots.txt").respond(404)
    route = respx.get("https://retry.test/")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, text="recovered"),
    ]
    async with client() as c:
        result = await c.get("https://retry.test/")
    assert result.ok
    assert result.text == "recovered"
    assert route.call_count == 2


@respx.mock
async def test_does_not_retry_a_404():
    respx.get("https://gone.test/robots.txt").respond(404)
    route = respx.get("https://gone.test/").respond(404)
    async with client() as c:
        result = await c.get("https://gone.test/")
    assert result.status_code == 404
    assert route.call_count == 1, "404 is a settled answer, not a transient failure"


@respx.mock
async def test_robots_disallow_is_obeyed():
    respx.get("https://strict.test/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private/"
    )
    respx.get("https://strict.test/private/x").respond(200, text="secret")
    async with client() as c:
        assert not await c.allowed("https://strict.test/private/x")
        with pytest.raises(RobotsDisallowedError):
            await c.get("https://strict.test/private/x")


@respx.mock
async def test_robots_allows_what_it_does_not_forbid():
    respx.get("https://strict2.test/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private/"
    )
    respx.get("https://strict2.test/public").respond(200, text="fine")
    async with client() as c:
        result = await c.get("https://strict2.test/public")
    assert result.text == "fine"


@respx.mock
async def test_missing_robots_means_no_restrictions():
    respx.get("https://norobots.test/robots.txt").respond(404)
    respx.get("https://norobots.test/").respond(200, text="ok")
    async with client() as c:
        result = await c.get("https://norobots.test/")
    assert result.ok


@respx.mock
async def test_robots_is_fetched_once_per_host():
    robots = respx.get("https://once.test/robots.txt").respond(404)
    respx.get("https://once.test/a").respond(200, text="a")
    respx.get("https://once.test/b").respond(200, text="b")
    async with client() as c:
        await c.get("https://once.test/a")
        await c.get("https://once.test/b")
    assert robots.call_count == 1


def test_allowlisted_api_endpoints_skip_robots():
    """The carve-out is per-prefix, and the crawler is not in it."""
    c = PoliteClient(cache=NullCache())
    assert c.is_robots_exempt("https://overpass-api.de/api/interpreter?data=x")
    assert not c.is_robots_exempt("https://overpass-api.de/something-else")
    assert not c.is_robots_exempt("https://some-small-business.com/about")


@respx.mock
async def test_get_or_none_swallows_failures():
    respx.get("https://dead.test/robots.txt").respond(404)
    respx.get("https://dead.test/").mock(side_effect=httpx.ConnectError("refused"))
    async with client() as c:
        assert await c.get_or_none("https://dead.test/") is None


@respx.mock
async def test_circuit_opens_after_repeated_failures():
    respx.get("https://flaky.test/robots.txt").respond(404)
    respx.get("https://flaky.test/").mock(side_effect=httpx.ConnectError("refused"))
    async with client() as c:
        # max_retries defaults to 3, so one call books 4 failures — past the
        # default threshold of 5 after the second call.
        await c.get_or_none("https://flaky.test/")
        await c.get_or_none("https://flaky.test/")
        with pytest.raises(CircuitOpenError):
            await c.get("https://flaky.test/")


@respx.mock
async def test_response_is_served_from_cache_on_second_call():
    class Memory:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.store.get(key)

        async def set(self, key: str, value: str, ttl_seconds: int) -> None:
            self.store[key] = value

        async def delete(self, key: str) -> None:
            self.store.pop(key, None)

        async def close(self) -> None:
            return None

    respx.get("https://cached.test/robots.txt").respond(404)
    route = respx.get("https://cached.test/").respond(200, text="body")
    async with PoliteClient(cache=Memory()) as c:
        first = await c.get("https://cached.test/")
        second = await c.get("https://cached.test/")
    assert route.call_count == 1
    assert not first.from_cache
    assert second.from_cache and second.text == "body"


# --------------------------------------------------------------------------- #
# overpass mapping
# --------------------------------------------------------------------------- #

def test_element_without_a_name_is_dropped():
    assert element_to_company({"type": "node", "id": 1, "tags": {"craft": "hvac"}}) is None


def test_element_maps_onto_a_company():
    company = element_to_company(
        {
            "type": "node",
            "id": 42,
            "lat": 39.96,
            "lon": -83.0,
            "tags": {
                "name": "Whitaker Heating",
                "craft": "hvac",
                "website": "whitaker.example",
                "contact:phone": "+1 614 555 0142",
                "addr:city": "Columbus",
                "start_date": "1979",
            },
        }
    )
    assert company is not None
    assert company.id == "osm:node:42"
    assert company.industry == "HVAC"
    assert company.website == "https://whitaker.example", "scheme is added when absent"
    assert company.founded_year == 1979
    assert company.source_url == "https://www.openstreetmap.org/node/42"
    assert company.contacts[0].phone == "+1 614 555 0142"


def test_way_uses_its_center_coordinate():
    company = element_to_company(
        {
            "type": "way",
            "id": 7,
            "center": {"lat": 40.0, "lon": -83.1},
            "tags": {"name": "Corner Vets", "amenity": "veterinary"},
        }
    )
    assert company is not None
    assert (company.latitude, company.longitude) == (40.0, -83.1)


@pytest.mark.parametrize("raw", ["not-a-year", "12", "3025", ""])
def test_implausible_start_dates_are_ignored(raw):
    company = element_to_company(
        {"type": "node", "id": 1, "tags": {"name": "X", "craft": "hvac", "start_date": raw}}
    )
    assert company is not None
    assert company.founded_year is None


def test_query_covers_every_tag_and_asks_for_centers():
    query = build_query(BoundingBox(39.8, -83.2, 40.15, -82.77), {"craft=hvac": "HVAC"})
    assert 'nwr[craft="hvac"](39.8,-83.2,40.15,-82.77);' in query
    assert query.rstrip().endswith("out center tags;")
    assert "[out:json]" in query


# --------------------------------------------------------------------------- #
# website signal extraction
# --------------------------------------------------------------------------- #

DATED_SITE = """
<html><head>
  <meta name="generator" content="Microsoft FrontPage 4.0">
</head><body>
  <p>Wilson Plumbing is a family-owned business serving Columbus since 1972.</p>
  <p>Call us at (614) 555-0142 or email dale@wilsonplumbing.com</p>
  <a href="/about">About us</a>
  <footer>&copy; 2014 Wilson Plumbing</footer>
  <script>var tracking = "not analytics"; var year = 2050;</script>
</body></html>
"""

MODERN_SITE = """
<html><head>
  <meta name="viewport" content="width=device-width">
  <script src="https://www.googletagmanager.com/gtag/js"></script>
</head><body>
  <div id="__next"></div>
  <p>A portfolio company of Meridian Capital Partners. Copyright 2026.</p>
  <a href="/careers">Careers</a>
</body></html>
"""


def test_scripts_do_not_poison_year_signals():
    """A script tag containing 2050 must not become 'latest content year'."""
    text = website._visible_text(website.HTMLParser(DATED_SITE))
    copyright_year, latest, founded = website._year_signals(text, DATED_SITE, NOW)
    assert copyright_year == 2014
    assert founded == 1972
    assert latest is not None and latest <= NOW.year


def test_legacy_generator_becomes_a_tech_hint():
    hints = website._detect_tech(DATED_SITE, "Microsoft FrontPage 4.0")
    assert "frontpage" in hints


def test_modern_framework_detected():
    assert "next.js" in website._detect_tech(MODERN_SITE, None)


def test_ownership_sentences_are_captured_verbatim():
    text = website._visible_text(website.HTMLParser(DATED_SITE))
    mentions = website._sentences_matching(text, website._OWNERSHIP_SENTENCE)
    assert any("family-owned" in m for m in mentions)


def test_pe_sentences_are_captured():
    text = website._visible_text(website.HTMLParser(MODERN_SITE))
    mentions = website._sentences_matching(text, website._PE_SENTENCE)
    assert any("portfolio company of" in m for m in mentions)


def test_contact_extraction_skips_asset_filenames():
    html = '<img src="logo@2x.png"> <a href="mailto:dale@wilson.com">dale@wilson.com</a>'
    contacts = website._extract_contacts("call (614) 555-0142", html)
    assert contacts and contacts[0].email == "dale@wilson.com"
    assert contacts[0].phone == "(614) 555-0142"


def test_contact_extraction_returns_nothing_when_there_is_nothing():
    assert website._extract_contacts("no contact details here", "<p>hi</p>") == []


@respx.mock
async def test_crawl_site_survives_a_dead_host():
    """A site that is down yields empty signals, never an exception."""
    respx.get("https://down.test/robots.txt").respond(404)
    respx.get("https://down.test/").mock(side_effect=httpx.ConnectError("refused"))
    async with client() as c:
        signals, contacts = await website.crawl_site(c, "https://down.test/")
    assert signals.fetched_at is None
    assert signals.raw_text_excerpt is None
    assert contacts == []


@respx.mock
async def test_crawl_site_extracts_the_full_signal_set():
    respx.get("https://dated.test/robots.txt").respond(404)
    respx.get("https://dated.test/").respond(200, text=DATED_SITE)
    respx.get("https://dated.test/about").respond(200, text="<p>Founded 1972 by Dale.</p>")
    async with client() as c:
        signals, contacts = await website.crawl_site(c, "https://dated.test/", now=NOW)

    assert signals.https is True
    assert signals.mobile_viewport is False
    assert signals.has_analytics is False
    assert signals.copyright_year == 2014
    assert signals.founded_year == 1972
    assert "frontpage" in signals.tech_hints
    assert any("family-owned" in m for m in signals.owner_mentions)
    assert contacts and contacts[0].email == "dale@wilsonplumbing.com"


# --------------------------------------------------------------------------- #
# peer density
# --------------------------------------------------------------------------- #

def test_haversine_matches_a_known_distance():
    # Columbus OH to Cleveland OH is ~station 200km.
    km = haversine_km(39.9612, -82.9988, 41.4993, -81.6944)
    assert 180 < km < 220


def make(id_: str, industry: str | None, lat: float | None, lon: float | None) -> Company:
    return Company(id=id_, name=id_, industry=industry, latitude=lat, longitude=lon)


def test_peer_density_counts_only_the_same_industry_nearby():
    companies = [
        make("a", "HVAC", 39.96, -83.00),
        make("b", "HVAC", 39.97, -83.01),          # ~1.4km away
        make("c", "HVAC", 41.50, -81.69),          # Cleveland, far outside
        make("d", "Dental", 39.96, -83.00),        # same spot, wrong niche
    ]
    annotate_peer_density(companies, radius_km=15.0)
    by_id = {c.id: c for c in companies}
    assert by_id["a"].peer_count_in_niche == 1
    assert by_id["b"].peer_count_in_niche == 1
    assert by_id["c"].peer_count_in_niche == 0
    assert by_id["d"].peer_count_in_niche == 0


def test_peer_density_left_none_without_coordinates():
    """No coordinates means no measurement — not a zero, which would read as
    'we checked and this niche is empty'."""
    companies = [make("a", "HVAC", None, None), make("b", "HVAC", 39.96, -83.0)]
    annotate_peer_density(companies)
    assert companies[0].peer_count_in_niche is None
    assert companies[1].peer_count_in_niche == 0
