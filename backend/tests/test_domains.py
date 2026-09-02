"""Domain inference.

Nearly all of these test what must be *rejected*. A wrong website is far worse
than a missing one: it staples a stranger's business onto a licence record, and
the moment a user clicks through to check the evidence and finds someone else,
every other claim the product makes becomes suspect. A missing website costs
only a factor reporting itself unmeasured, which the engine already handles.
"""

from __future__ import annotations

import pytest

from app.pipeline.domains import candidate_domains, distinctive_tokens, verify
from app.schemas import Company, Contact


def co(name: str, *, phone: str | None = "+16267153902", city: str | None = "GLENDALE") -> Company:
    return Company(
        id=f"cslb:{name}",
        name=name,
        city=city,
        contacts=[Contact(phone=phone)] if phone else [],
    )


def page(body: str) -> str:
    return f"<html><body>{body}<p>{'padding. ' * 80}</p></body></html>"


# --------------------------------------------------------------------------- #
# candidate generation
# --------------------------------------------------------------------------- #

def test_two_word_name_produces_the_obvious_domain():
    assert "https://hickeyplumbing.com" in candidate_domains("HICKEY PLUMBING")


def test_apostrophes_are_absorbed_not_split():
    """"Johnny's" must become johnnys, never johnny-s-air-conditioning."""
    for url in candidate_domains("JOHNNY'S AIR CONDITIONING SERVICES"):
        assert "-s-" not in url
        assert "johnnys" in url


def test_long_names_are_shortened_the_way_contractors_shorten_them():
    """Nobody registers gabekaprelianelectricalcontractor.com."""
    urls = candidate_domains("GABE KAPRELIAN ELECTRICAL CONTRACTOR")
    assert all(len(u) < 45 for u in urls)
    assert any("gabekaprelianelectric" in u for u in urls)


def test_leading_initial_is_kept():
    """'A' in a trade name is an initial far more often than an article, and
    dropping it invents a company that does not exist."""
    hosts = {u.removeprefix("https://") for u in candidate_domains("A K INTERNATIONAL")}
    assert "akinternational.com" in hosts
    assert "kinternational.com" not in hosts


def test_legal_suffixes_are_dropped():
    urls = candidate_domains("ACCURATE ENERGY SERVICES INC")
    assert all("inc" not in u.split("//")[1].split(".")[0][-3:] for u in urls)


def test_candidate_count_is_bounded():
    """Every candidate is a request to a stranger's server."""
    assert len(candidate_domains("SOME REASONABLY LONG BUSINESS NAME HERE")) <= 4


@pytest.mark.parametrize("name", ["", "   ", "INC", "THE CO"])
def test_unusable_names_produce_nothing(name):
    assert candidate_domains(name) == []


def test_distinctive_tokens_exclude_trade_words():
    assert distinctive_tokens("HICKEY PLUMBING") == ["hickey"]
    assert distinctive_tokens("QUALITY PLUMBING SERVICES") == []


# --------------------------------------------------------------------------- #
# verification: what is accepted
# --------------------------------------------------------------------------- #

def test_phone_on_the_page_is_conclusive():
    html = page("<p>Call us on (626) 715-3902 for a quote.</p>")
    match = verify(co("HICKEY PLUMBING"), "https://hickeyplumbing.com", html)
    assert match is not None
    assert match.method == "phone"


@pytest.mark.parametrize(
    "rendering",
    ["(626) 715-3902", "626.715.3902", "626-715-3902", "6267153902", "+1 626 715 3902"],
)
def test_phone_matches_however_it_is_formatted(rendering):
    match = verify(
        co("HICKEY PLUMBING"), "https://hickeyplumbing.com", page(f"<p>{rendering}</p>")
    )
    assert match is not None and match.method == "phone"


def test_name_plus_city_is_accepted_as_weaker_evidence():
    html = page("<p>Hickey has served Glendale since 1984.</p>")
    match = verify(co("HICKEY PLUMBING"), "https://hickeyplumbing.com", html)
    assert match is not None
    assert match.method == "name"
    # The claim must state its own limits.
    assert "not appear" in match.detail


def test_a_changed_phone_number_does_not_block_a_real_match():
    """Businesses change numbers after filing. The name route is what catches
    them, and it correctly reports that the filed number was absent."""
    html = page("<p>Accurate Energy serves Glendale. Call (626) 358-8375.</p>")
    match = verify(co("ACCURATE ENERGY SERVICES INC"), "https://aenergy.us", html)
    assert match is not None and match.method == "name"


# --------------------------------------------------------------------------- #
# verification: what must be rejected
# --------------------------------------------------------------------------- #

def test_a_generic_token_alone_cannot_carry_a_match():
    """The failure this guard exists for: 'A K International' reduces to the
    single token 'international', which appears on international.com — a real
    site belonging to somebody else."""
    html = page("<p>International shipping and logistics worldwide.</p>")
    assert verify(co("A K INTERNATIONAL"), "https://international.com", html) is None


def test_name_without_the_city_is_rejected():
    html = page("<p>Hickey Industries, a division of Hickey Global.</p>")
    assert verify(co("HICKEY PLUMBING"), "https://hickey.com", html) is None


def test_partial_name_match_is_rejected():
    """All distinctive tokens must appear, not merely one of them."""
    html = page("<p>Gabe's Diner, Glendale's favourite since 1990.</p>")
    assert verify(co("GABE KAPRELIAN ELECTRICAL CONTRACTOR"), "https://gabe.com", html) is None


@pytest.mark.parametrize(
    "marker",
    ["This domain is for sale", "Buy this domain", "Coming soon", "Under construction"],
)
def test_parked_domains_are_rejected(marker):
    html = page(f"<h1>{marker}</h1><p>(626) 715-3902</p>")
    assert verify(co("HICKEY PLUMBING"), "https://hickeyplumbing.com", html) is None


def test_near_empty_pages_are_rejected():
    assert verify(co("HICKEY PLUMBING"), "https://x.com", "<html><body>hi</body></html>") is None


def test_unrelated_business_is_rejected():
    html = page("<p>Westside Dental Group, Santa Monica. Call (310) 555-0100.</p>")
    assert verify(co("HICKEY PLUMBING"), "https://hickeyplumbing.com", html) is None


def test_company_without_a_city_cannot_match_by_name():
    """With no city there is nothing to corroborate the name against, so the
    weaker route is unavailable rather than merely weaker."""
    html = page("<p>Hickey and sons, established 1984.</p>")
    assert verify(co("HICKEY PLUMBING", phone=None, city=None), "https://hickey.com", html) is None


def test_scripts_cannot_supply_the_proof():
    """A tracking snippet or JSON blob containing the digits is not the business
    publishing its phone number."""
    html = (
        "<html><body><script>var id='6267153902';</script>"
        f"<p>{'unrelated content. ' * 60}</p></body></html>"
    )
    assert verify(co("HICKEY PLUMBING"), "https://hickeyplumbing.com", html) is None
