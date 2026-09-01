"""Dedupe, normalisation and chain detection.

The interesting cases here are the near-misses. Deduplication that is slightly
too eager silently deletes real businesses from a user's list, and they cannot
tell — the row simply is not there. That failure is worse than a visible
duplicate, so most of these tests are about what must *not* merge.
"""

from __future__ import annotations

import pytest

from app.pipeline.dedupe import (
    CHAIN_LOCATION_THRESHOLD,
    annotate_chain_locations,
    blocking_keys,
    deduplicate,
)
from app.pipeline.normalize import (
    email_domain,
    name_blocking_key,
    normalise_domain,
    normalise_name,
    normalise_postcode,
    registrable_domain,
)
from app.schemas import Company, Contact


def co(
    id_: str,
    name: str,
    *,
    website: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    city: str | None = "Columbus",
    postcode: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> Company:
    contacts = [Contact(email=email, phone=phone)] if (email or phone) else []
    return Company(
        id=id_,
        name=name,
        website=website,
        latitude=lat,
        longitude=lon,
        city=city,
        postcode=postcode,
        contacts=contacts,
    )


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Whitaker-HVAC.com/about", "whitaker-hvac.com"),
        ("WWW.EXAMPLE.COM", "example.com"),
        ("example.com:8080", "example.com"),
        ("http://example.com.", "example.com"),
        (None, None),
        ("", None),
    ],
)
def test_normalise_domain(raw, expected):
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("shop.whitakerhvac.com", "whitakerhvac.com"),
        ("whitakerhvac.com", "whitakerhvac.com"),
        ("book.appointments.vets.co.uk", "vets.co.uk"),
    ],
)
def test_registrable_domain(raw, expected):
    assert registrable_domain(raw) == expected


def test_ampersand_and_and_converge():
    """"Heating & Cooling" and "Heating and Cooling" are one business."""
    assert normalise_name("Whitaker Heating & Cooling, Inc.") == normalise_name(
        "WHITAKER HEATING AND COOLING LLC"
    )


def test_legal_suffixes_are_stripped():
    assert normalise_name("Acme Dental PLLC") == "acme dental"


def test_name_of_only_suffixes_does_not_become_empty():
    """An empty key would collide with every other empty key and merge them."""
    assert normalise_name("The Company Inc") != ""


def test_blocking_key_is_stable_across_spellings():
    assert name_blocking_key("Whitaker Heating & Cooling") == name_blocking_key(
        "whitaker heating and cooling llc"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("43215-1234", "43215"), ("43215", "43215"), ("abc", None), (None, None)],
)
def test_normalise_postcode(raw, expected):
    assert normalise_postcode(raw) == expected


def test_email_domain():
    assert email_domain("Dale@WWW.Acme.com") == "acme.com"
    assert email_domain("not-an-email") is None


# --------------------------------------------------------------------------- #
# blocking
# --------------------------------------------------------------------------- #

def test_a_record_gets_several_blocking_keys():
    """One key per record would make the block a single point of failure."""
    keys = blocking_keys(
        co(
            "a",
            "Acme Dental",
            website="https://acme.example",
            email="x@other.example",
            phone="+16142204000",
            postcode="43215",
        )
    )
    assert any(k.startswith("d:") for k in keys)
    assert any(k.startswith("e:") for k in keys)
    assert any(k.startswith("p:") for k in keys)
    assert any(k.startswith("n:") for k in keys)


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #

def test_same_business_two_spellings_merges():
    companies = [
        co("a", "Whitaker Heating & Cooling, Inc.", lat=39.9600, lon=-83.0000),
        co("b", "WHITAKER HEATING AND COOLING LLC", lat=39.9601, lon=-83.0001),
    ]
    result = deduplicate(companies)
    assert len(result.companies) == 1
    assert result.matches[0].reason == "near-identical name"


def test_chain_branches_do_not_merge():
    """The failure that mattered on real data.

    Three NTB tyre centres share a name and a domain. Merging them deletes two
    real businesses from the user's list, invisibly.
    """
    companies = [
        co("a", "NTB", website="https://ntb.com", lat=39.96, lon=-83.00),
        co("b", "NTB", website="https://ntb.com", lat=40.05, lon=-82.95),
        co("c", "NTB", website="https://ntb.com", lat=39.90, lon=-83.10),
    ]
    result = deduplicate(companies)
    assert len(result.companies) == 3
    assert result.matches == []


def test_similar_names_at_one_address_merge():
    companies = [
        co("a", "Columbus Dental Care", lat=39.96, lon=-83.0, postcode="43215"),
        co("b", "Columbus Dental Care LLC", lat=39.9602, lon=-83.0002, postcode="43215"),
    ]
    assert len(deduplicate(companies).companies) == 1


def test_different_practices_with_similar_names_do_not_merge():
    """Dental "Care" and dental "Group" are different businesses."""
    companies = [
        co("a", "Columbus Dental Care", lat=39.96, lon=-83.0),
        co("b", "Columbus Dental Group", lat=39.9601, lon=-83.0),
    ]
    assert len(deduplicate(companies).companies) == 2


def test_merge_keeps_the_more_complete_record_and_loses_nothing():
    rich = co(
        "rich", "Acme Dental", lat=39.96, lon=-83.0,
        email="dale@acme.example", phone="+16142204000", postcode="43215",
    )
    sparse = co("sparse", "Acme Dental LLC", lat=39.9601, lon=-83.0)
    sparse.founded_year = 1988

    result = deduplicate([sparse, rich])
    assert len(result.companies) == 1
    survivor = result.companies[0]
    assert survivor.contacts, "kept the record a user can actually act on"
    assert survivor.founded_year == 1988, "learned what the discarded copy knew"


def test_dedupe_output_order_is_stable():
    """A re-run must produce a byte-identical dataset, or the committed seed
    file churns on every collection."""
    companies = [co(f"c{i}", f"Business {i}", lat=39.9 + i / 100, lon=-83.0) for i in range(5)]
    first = [c.id for c in deduplicate(companies).companies]
    second = [c.id for c in deduplicate(companies).companies]
    assert first == second == [c.id for c in companies]


def test_records_without_coordinates_still_merge_on_name():
    companies = [
        co("a", "Shaw-Davis Funeral Home", lat=None, lon=None, postcode="43215"),
        co("b", "Shaw Davis Funeral Home", lat=None, lon=None, postcode="43215"),
    ]
    assert len(deduplicate(companies).companies) == 1


# --------------------------------------------------------------------------- #
# chain detection
# --------------------------------------------------------------------------- #

def test_three_locations_is_a_chain():
    companies = [
        co("a", "Valvoline", lat=39.96, lon=-83.00),
        co("b", "Valvoline", lat=40.05, lon=-82.95),
        co("c", "Valvoline", lat=39.90, lon=-83.10),
    ]
    assert annotate_chain_locations(companies) == 3
    assert all(c.sibling_location_count == 3 for c in companies)


def test_two_locations_is_not_a_chain():
    """A dentist with a second surgery is still a business someone can sell."""
    companies = [
        co("a", "Hutta Orthodontics", lat=39.96, lon=-83.00),
        co("b", "Hutta Orthodontics", lat=40.05, lon=-82.95),
    ]
    assert annotate_chain_locations(companies) == 0
    assert companies[0].sibling_location_count == 2 < CHAIN_LOCATION_THRESHOLD


def test_one_business_mapped_repeatedly_is_not_a_chain():
    """Counts distinct premises, not distinct records — otherwise a
    double-mapped shop gets libelled as a franchise."""
    companies = [
        co("a", "Corner Vets", lat=39.9600, lon=-83.0000),
        co("b", "Corner Vets", lat=39.96001, lon=-83.00001),
        co("c", "Corner Vets", lat=39.96002, lon=-83.00002),
    ]
    assert annotate_chain_locations(companies) == 0
