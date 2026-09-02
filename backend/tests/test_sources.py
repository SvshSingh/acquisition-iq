"""Licence-record mapping, markets, and peer density.

The theme here is the difference between a measurement and a guess. Licence data
is valuable precisely because it is filed rather than claimed, and that value
evaporates the moment the mapping starts inferring things the filing does not
say. Most of these tests pin down what the code must *refuse* to conclude.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.pipeline.markets import COLUMBUS, GLENDALE, get_market
from app.pipeline.peers import annotate_peer_density
from app.pipeline.sources.cslb import CslbSource, row_to_company
from app.schemas import Company
from app.scoring.factors import score_buy_box, score_succession

NOW = date(2026, 9, 2)


def row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "LicenseNumber": "123456",
        "BusinessName": "WHITAKER ELECTRIC",
        "BusinessType": "Sole Owner",
        "Address": "100 MAIN ST",
        "City": "GLENDALE",
        "State": "CA",
        "ZIP Code": "91203",
        "County": "Los Angeles",
        "PhoneNumber": "(818) 555 0142",
        "IssueDate": "05/23/1987",
        "Classification(s)": "C10",
        "Status": "CLEAR",
        "WorkersCompCoverageType": "Workers' Compensation Insurance",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# markets
# --------------------------------------------------------------------------- #

def test_market_lookup_is_case_insensitive():
    assert get_market("GLENDALE") is GLENDALE


def test_unknown_market_names_the_known_ones():
    with pytest.raises(KeyError, match="columbus"):
        get_market("atlantis")


def test_core_city_filter():
    assert GLENDALE.is_core("Glendale")
    assert GLENDALE.is_core("BURBANK")
    assert not GLENDALE.is_core("SAN DIEGO")
    assert not GLENDALE.is_core(None)


def test_market_without_core_cities_accepts_everything():
    """Columbus is collected metro-wide, so an empty list must mean 'no filter',
    not 'reject everything'."""
    assert COLUMBUS.is_core("Anywhere")
    assert COLUMBUS.is_core(None)


# --------------------------------------------------------------------------- #
# licence record mapping
# --------------------------------------------------------------------------- #

def test_maps_a_licence_record():
    c = row_to_company(row(), GLENDALE)
    assert c is not None
    assert c.id == "cslb:123456"
    assert c.industry == "Electrical"
    assert c.business_type == "Sole Owner"
    assert c.has_employees is True
    assert c.licence_issued == date(1987, 5, 23)
    assert c.founded_year == 1987
    assert c.contacts[0].phone == "(818) 555 0142"


def test_licence_records_never_carry_an_email():
    """Withheld by statute (B&P Code 27). Inventing one would be worse than none."""
    c = row_to_company(row(), GLENDALE)
    assert c is not None
    assert c.contacts[0].email is None


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        ("Workers' Compensation Insurance", True),
        ("Self-Insured", True),
        ("Leasing Firm, Temp Agency, etc", True),
        ("Exempt", False),
        # Lapsed cover says nothing about whether anyone is employed. Reading it
        # as "no employees" would invent a finding.
        ("License does not have current W/C", None),
        (None, None),
        ("", None),
    ],
)
def test_employment_is_derived_only_where_the_filing_says_so(coverage, expected):
    c = row_to_company(row(WorkersCompCoverageType=coverage), GLENDALE)
    assert c is not None
    assert c.has_employees is expected


def test_rows_without_a_buy_box_trade_are_dropped():
    """A general building licence is out of scope, not merely low-scoring."""
    assert row_to_company(row(**{"Classification(s)": "B"}), GLENDALE) is None


def test_multi_classification_prefers_the_buy_box_trade():
    c = row_to_company(row(**{"Classification(s)": "B | C36"}), GLENDALE)
    assert c is not None
    assert c.industry == "Plumbing"


def test_unnamed_or_unlicensed_rows_are_dropped():
    assert row_to_company(row(BusinessName=None), GLENDALE) is None
    assert row_to_company(row(LicenseNumber=None), GLENDALE) is None


@pytest.mark.parametrize("raw", ["not-a-date", "", None, "13/45/2020"])
def test_unparseable_issue_dates_become_none(raw):
    c = row_to_company(row(IssueDate=raw), GLENDALE)
    assert c is not None
    assert c.licence_issued is None
    assert c.founded_year is None


async def test_source_returns_nothing_outside_california():
    """CSLB is a California register. Silently returning Ohio rows from it would
    be a fabrication."""
    assert await CslbSource().discover(COLUMBUS) == []


# --------------------------------------------------------------------------- #
# how the filings reach the score
# --------------------------------------------------------------------------- #

def test_sole_owner_outscores_corporation_on_succession():
    sole = row_to_company(row(BusinessType="Sole Owner"), GLENDALE)
    corp = row_to_company(row(BusinessType="Corporation"), GLENDALE)
    assert sole is not None and corp is not None
    assert score_succession(sole).score > score_succession(corp).score


def test_filed_ownership_beats_a_website_claim_rather_than_stacking():
    """Both describe one fact. Summing them would double-count it."""
    from app.schemas import WebSignals

    filed = row_to_company(row(BusinessType="Sole Owner"), GLENDALE)
    assert filed is not None
    both = filed.model_copy(
        update={"web": WebSignals(raw_text_excerpt="A family-owned business.")}
    )
    assert score_succession(both).score == pytest.approx(score_succession(filed).score)


def test_licence_age_evidence_does_not_claim_a_founding_date():
    """A licence can be reissued, so the date is a floor on age. Saying
    'Founded 1987' would overstate what the register knows."""
    c = row_to_company(row(IssueDate="05/23/1987"), GLENDALE)
    assert c is not None
    detail = " ".join(e.detail for e in score_succession(c).evidence)
    assert "Licensed since" in detail
    assert "floor on the age" in detail


def test_no_employees_scores_below_the_buy_box_and_counts_as_measured():
    c = row_to_company(row(WorkersCompCoverageType="Exempt"), GLENDALE)
    assert c is not None
    result = score_buy_box(c)
    assert result.score < 30
    assert result.measured, "an owner-only operation is a finding, not a gap"


def test_employing_staff_scores_mid_band_not_top():
    """The filing proves at least one employee, never how many. Scoring it as a
    perfect buy-box fit would claim more than was checked."""
    c = row_to_company(row(WorkersCompCoverageType="Workers' Compensation Insurance"), GLENDALE)
    assert c is not None
    result = score_buy_box(c)
    assert 40 < result.score < 75
    assert result.measured
    assert "exact headcount" in result.missing_signals


def test_unknown_employment_leaves_buy_box_unmeasured():
    c = row_to_company(row(WorkersCompCoverageType=None), GLENDALE)
    assert c is not None
    assert not score_buy_box(c).measured


# --------------------------------------------------------------------------- #
# peer density
# --------------------------------------------------------------------------- #

def make(id_: str, industry: str, *, postcode: str | None = None,
         lat: float | None = None, lon: float | None = None) -> Company:
    return Company(id=id_, name=id_, industry=industry, postcode=postcode,
                   latitude=lat, longitude=lon)


def test_postcode_fallback_when_there_are_no_coordinates():
    """Licence registers publish an address, not a coordinate."""
    companies = [make(f"c{i}", "HVAC", postcode="91203") for i in range(4)]
    companies.append(make("far", "HVAC", postcode="90210"))
    annotate_peer_density(companies)
    assert [c.peer_count_in_niche for c in companies[:4]] == [3, 3, 3, 3]
    assert companies[-1].peer_count_in_niche == 0


def test_zip_plus_four_does_not_split_a_postcode():
    companies = [make("a", "HVAC", postcode="91203-1234"), make("b", "HVAC", postcode="91203")]
    annotate_peer_density(companies)
    assert companies[0].peer_count_in_niche == 1


def test_coordinates_win_over_postcode_when_present():
    companies = [
        make("a", "HVAC", lat=39.96, lon=-83.00, postcode="91203"),
        make("b", "HVAC", lat=39.97, lon=-83.01, postcode="91203"),
        make("c", "HVAC", lat=41.50, lon=-81.69, postcode="91203"),  # 200km away
    ]
    annotate_peer_density(companies, radius_km=15.0)
    assert companies[0].peer_count_in_niche == 1, "the distant peer does not count"


def test_different_industries_do_not_count_as_peers():
    companies = [make("a", "HVAC", postcode="91203"), make("b", "Plumbing", postcode="91203")]
    annotate_peer_density(companies)
    assert companies[0].peer_count_in_niche == 0


def test_no_geography_leaves_the_count_unset():
    """None, not zero — zero reads as 'we checked and the niche is empty'."""
    companies = [make("a", "HVAC")]
    annotate_peer_density(companies)
    assert companies[0].peer_count_in_niche is None
