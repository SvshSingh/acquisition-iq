"""Scoring engine tests.

The golden-file test at the bottom is the important one: it pins a fixed input
set to a fixed score vector so a refactor cannot silently move the numbers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas import (
    BuyBox,
    Company,
    Confidence,
    Contact,
    FactorKey,
    FactorWeights,
    VerificationStatus,
    WebSignals,
)
from app.scoring import factors
from app.scoring.engine import score_company, score_many

NOW = datetime(2026, 9, 1, tzinfo=UTC)
GOLDEN = Path(__file__).parent / "golden_scores.json"


def make_company(**overrides) -> Company:
    base = {
        "id": "c1",
        "name": "Test Co",
        "domain": "test.co",
        "website": "https://test.co",
        "industry": "hvac",
        "city": "Columbus",
        "state": "OH",
    }
    base.update(overrides)
    return Company(**base)


# --------------------------------------------------------------------------- #
# succession
# --------------------------------------------------------------------------- #

def test_family_owned_language_lifts_succession():
    plain = make_company(web=WebSignals(raw_text_excerpt="We fix furnaces."))
    family = make_company(
        web=WebSignals(raw_text_excerpt="A family-owned business serving Columbus since 1978.")
    )
    assert factors.score_succession(family, today=NOW).score > factors.score_succession(
        plain, today=NOW
    ).score


def test_pe_backing_is_disqualifying():
    backed = make_company(
        web=WebSignals(
            raw_text_excerpt="A family-owned portfolio company of Meridian Capital Partners."
        )
    )
    result = factors.score_succession(backed, today=NOW)
    assert result.score < 40
    assert any("PE-backed" in e.label for e in result.evidence)


def test_young_business_penalised():
    young = make_company(founded_year=2023)
    old = make_company(founded_year=1985)
    assert factors.score_succession(young, today=NOW).score < factors.score_succession(
        old, today=NOW
    ).score


def test_missing_signals_lower_confidence():
    bare = make_company()
    assert factors.score_succession(bare, today=NOW).confidence is not Confidence.HIGH


# --------------------------------------------------------------------------- #
# buy box
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("employees", "revenue", "expected"),
    [
        (40, 4_000_000, 100.0),   # dead centre
        (10, 1_000_000, 100.0),   # on the lower edge
        (100, 10_000_000, 100.0), # on the upper edge
        (2, 90_000, 0.0),         # far below
        (5_000, 900_000_000, 0.0),# far above
    ],
)
def test_buy_box_band(employees, revenue, expected):
    c = make_company(employee_count=employees, revenue_usd=revenue)
    assert factors.score_buy_box(c, BuyBox()).score == pytest.approx(expected, abs=0.05)


def test_buy_box_partial_credit_just_outside_band():
    c = make_company(employee_count=8, revenue_usd=900_000)
    score = factors.score_buy_box(c, BuyBox()).score
    assert 0 < score < 100


def test_buy_box_without_data_is_low_confidence():
    result = factors.score_buy_box(make_company(), BuyBox())
    assert result.confidence is Confidence.LOW
    assert "employee count" in result.missing_signals


# --------------------------------------------------------------------------- #
# digital gap
# --------------------------------------------------------------------------- #

def test_worse_site_means_more_upside():
    modern = make_company(
        web=WebSignals(
            https=True, mobile_viewport=True, has_analytics=True,
            tech_hints=["Next.js"], latest_content_year=2026,
        )
    )
    dated = make_company(
        web=WebSignals(
            https=False, mobile_viewport=False, has_analytics=False,
            tech_hints=["FrontPage"], latest_content_year=2014,
        )
    )
    assert factors.score_digital_gap(dated, today=NOW).score > factors.score_digital_gap(
        modern, today=NOW
    ).score


def test_no_website_does_not_crash():
    result = factors.score_digital_gap(make_company(website=None), today=NOW)
    assert 0 <= result.score <= 100
    assert result.confidence is Confidence.LOW


# --------------------------------------------------------------------------- #
# fragmentation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("peers", "lo", "hi"),
    [(0, 0, 40), (5, 45, 70), (20, 80, 100), (60, 60, 80), (250, 30, 60)],
)
def test_fragmentation_curve(peers, lo, hi):
    c = make_company(peer_count_in_niche=peers)
    assert lo <= factors.score_fragmentation(c).score <= hi


# --------------------------------------------------------------------------- #
# contactability
# --------------------------------------------------------------------------- #

def test_no_contacts_scores_zero():
    assert factors.score_contactability(make_company()).score == 0.0


def test_verified_owner_beats_unverified_role_address():
    owner = make_company(
        contacts=[
            Contact(
                name="Dale Whitaker", title="Owner", email="dale@test.co",
                email_status=VerificationStatus.VERIFIED, phone="+16145550142",
                phone_valid=True, linkedin_url="https://linkedin.com/in/x",
                is_decision_maker=True,
            )
        ]
    )
    generic = make_company(
        contacts=[Contact(email="info@test.co", email_status=VerificationStatus.UNKNOWN)]
    )
    assert factors.score_contactability(owner).score > factors.score_contactability(generic).score


def test_invalid_email_is_penalised():
    c = make_company(
        contacts=[Contact(email="nope@test.co", email_status=VerificationStatus.INVALID)]
    )
    ev = factors.score_contactability(c).evidence
    assert any(e.impact < 0 for e in ev)


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #

def test_score_is_bounded_and_weights_normalise():
    c = make_company(employee_count=40, revenue_usd=4_000_000)
    unnormalised = FactorWeights(
        succession=1.0, buy_box=1.0, digital_gap=1.0,
        fragmentation=1.0, contactability=1.0, health=1.0,
    )
    result = score_company(c, weights=unnormalised, now=NOW)
    assert 0 <= result.score <= 100
    assert sum(result.weights.as_map().values()) == pytest.approx(1.0)


def test_zero_weights_fall_back_to_equal():
    w = FactorWeights(
        succession=0, buy_box=0, digital_gap=0,
        fragmentation=0, contactability=0, health=0,
    )
    assert sum(w.as_map().values()) == pytest.approx(1.0)


def test_weights_change_ranking():
    """Two companies, opposite strengths — the weights should decide the winner."""
    reachable = make_company(
        id="reachable",
        contacts=[
            Contact(name="A B", title="Owner", email="a@b.co",
                    email_status=VerificationStatus.VERIFIED, phone="+16145550142",
                    phone_valid=True, is_decision_maker=True)
        ],
    )
    right_size = make_company(id="right_size", employee_count=40, revenue_usd=4_000_000)

    contact_heavy = FactorWeights(
        succession=0, buy_box=0, digital_gap=0, fragmentation=0,
        contactability=1.0, health=0,
    )
    size_heavy = FactorWeights(
        succession=0, buy_box=1.0, digital_gap=0, fragmentation=0,
        contactability=0, health=0,
    )
    by_contact = score_many([reachable, right_size], weights=contact_heavy, now=NOW)
    by_size = score_many([reachable, right_size], weights=size_heavy, now=NOW)
    assert by_contact[0].company.id == "reachable"
    assert by_size[0].company.id == "right_size"


def test_score_many_sorts_descending():
    companies = [make_company(id=f"c{i}", employee_count=i * 10) for i in range(1, 6)]
    scored = score_many(companies, now=NOW)
    assert [s.score.score for s in scored] == sorted(
        [s.score.score for s in scored], reverse=True
    )


def test_contributions_sum_to_total():
    c = make_company(employee_count=40, revenue_usd=4_000_000, peer_count_in_niche=20)
    result = score_company(c, now=NOW)
    total = sum(result.contribution(k) for k in FactorKey)
    assert total == pytest.approx(result.score, abs=0.1)


# --------------------------------------------------------------------------- #
# saturation
# --------------------------------------------------------------------------- #

def test_redundant_ownership_phrasings_do_not_stack():
    """One fact stated five ways is still one fact."""
    single = make_company(web=WebSignals(raw_text_excerpt="A family-owned HVAC company."))
    piled = make_company(
        web=WebSignals(
            raw_text_excerpt="A family-owned, family-run, owner-operated, independently "
            "owned third-generation HVAC company."
        )
    )
    assert factors.score_succession(piled, today=NOW).score == pytest.approx(
        factors.score_succession(single, today=NOW).score
    )


def test_stated_tenure_and_computed_age_are_not_double_counted():
    both = make_company(
        founded_year=1979,
        web=WebSignals(raw_text_excerpt="Serving central Ohio since 1979."),
    )
    age_only = make_company(founded_year=1979)
    assert factors.score_succession(both, today=NOW).score == pytest.approx(
        factors.score_succession(age_only, today=NOW).score
    )


def test_no_factor_pegs_the_ceiling_on_the_ideal_target():
    """A maximally attractive target must still have headroom.

    When several factors clamp at 100 the engine loses the ability to rank the
    top of the list, which is the one job it exists to do. `buy_box` is exempt:
    it is a band score where 100 means "dead centre of the thesis", not
    "unbeatable".
    """
    ideal = next(c for c in golden_fixtures() if c.id == "golden-ideal")
    result = score_company(ideal, now=NOW)
    pegged = [
        f.key.value
        for f in result.factors
        if f.score >= 100.0 and f.key is not FactorKey.BUY_BOX
    ]
    assert not pegged, f"factors saturated at the ceiling: {pegged}"


# --------------------------------------------------------------------------- #
# golden file
# --------------------------------------------------------------------------- #

def golden_fixtures() -> list[Company]:
    """Fixed, hand-built inputs spanning the interesting corners of the space."""
    return [
        make_company(
            id="golden-ideal",
            name="Whitaker Heating & Cooling",
            founded_year=1979,
            employee_count=38,
            revenue_usd=5_200_000,
            peer_count_in_niche=22,
            contacts=[
                Contact(name="Dale Whitaker", title="Owner", email="dale@whitakerhvac.com",
                        email_status=VerificationStatus.VERIFIED, phone="+16145550142",
                        phone_valid=True, is_decision_maker=True)
            ],
            web=WebSignals(
                fetched_at=NOW, https=False, mobile_viewport=False, has_analytics=False,
                tech_hints=["FrontPage"], copyright_year=2016, latest_content_year=2016,
                page_bytes=41_000, has_team_page=True,
                raw_text_excerpt="A family-owned business serving central Ohio since 1979.",
            ),
        ),
        make_company(
            id="golden-too-big",
            name="National Mechanical Group",
            founded_year=1998,
            employee_count=4_200,
            revenue_usd=680_000_000,
            peer_count_in_niche=3,
            contacts=[Contact(email="info@nmg.com", email_status=VerificationStatus.RISKY)],
            web=WebSignals(
                fetched_at=NOW, https=True, mobile_viewport=True, has_analytics=True,
                tech_hints=["Next.js"], latest_content_year=2026, page_bytes=180_000,
                has_careers_page=True,
                raw_text_excerpt="A portfolio company of Meridian Capital Partners.",
            ),
        ),
        make_company(
            id="golden-sparse",
            name="Unknown Services LLC",
            website=None,
            peer_count_in_niche=None,
        ),
        make_company(
            id="golden-dormant",
            name="Old Town Plumbing",
            founded_year=1972,
            employee_count=6,
            revenue_usd=480_000,
            peer_count_in_niche=140,
            web=WebSignals(
                fetched_at=NOW, https=False, mobile_viewport=False, has_analytics=False,
                copyright_year=2011, latest_content_year=2011, page_bytes=900,
                raw_text_excerpt="Owner-operated since 1972.",
            ),
        ),
    ]


def current_golden() -> dict[str, float]:
    """Flat `<company>.<factor>` -> score map.

    Flat rather than nested on purpose: `pytest.approx` rejects nested mappings
    outright, and a flat key also names the exact cell that moved when the
    assertion fails.
    """
    out: dict[str, float] = {}
    for c in golden_fixtures():
        result = score_company(c, now=NOW)
        out[f"{c.id}.total"] = result.score
        for f in result.factors:
            out[f"{c.id}.{f.key.value}"] = f.score
    return out


def test_golden_scores_unchanged():
    current = current_golden()
    if not GOLDEN.exists():  # pragma: no cover - first run writes the baseline
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        pytest.skip("golden baseline created")
    expected = json.loads(GOLDEN.read_text())
    assert current == pytest.approx(expected, abs=0.05), (
        "Scores moved. If this was intentional, delete tests/golden_scores.json "
        "and re-run to re-baseline."
    )
