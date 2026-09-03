"""Record completeness, and the DNS-over-HTTPS fallback."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.pipeline.quality import CHECKS, annotate, assess
from app.pipeline.scrapers import website
from app.pipeline.validate import MxResolver, verify_email
from app.schemas import Company, Contact, VerificationStatus, WebSignals


def co(**kw: object) -> Company:
    base: dict[str, object] = {"id": "c1", "name": "Test Co"}
    base.update(kw)
    return Company(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# data quality
# --------------------------------------------------------------------------- #

def test_weights_sum_to_one():
    """Otherwise the score is not on the 0-100 scale it claims to be."""
    assert sum(c.weight for c in CHECKS) == pytest.approx(1.0)


def test_empty_record_scores_zero_and_lists_everything():
    score, gaps = assess(co())
    assert score == 0.0
    assert len(gaps) == len(CHECKS)


def test_complete_record_scores_one_hundred():
    score, gaps = assess(
        co(
            website="https://x.test",
            postcode="91203",
            business_type="Sole Owner",
            founded_year=1984,
            web=WebSignals(fetched_at="2026-09-03T00:00:00Z"),
            contacts=[
                Contact(
                    name="Dale Whitaker",
                    title="Owner",
                    email="dale@x.test",
                    email_status=VerificationStatus.VERIFIED,
                    phone="+16145550142",
                    phone_valid=True,
                    is_decision_maker=True,
                )
            ],
        )
    )
    assert score == 100.0
    assert gaps == []


def test_gaps_are_ordered_by_what_costs_most():
    """A user skimming the list should read the highest-leverage action first."""
    _, gaps = assess(co())
    assert gaps[0] == "no phone number"


def test_quality_is_independent_of_acquisition_fit():
    """A thin record on a great business must not be marked down for our
    ignorance, and vice versa. These answer different questions."""
    great_but_unknown = co(business_type="Sole Owner", founded_year=1975)
    poor_but_documented = co(
        business_type="Corporation",
        founded_year=2025,
        website="https://x.test",
        postcode="91203",
        web=WebSignals(fetched_at="2026-09-03T00:00:00Z"),
        contacts=[
            Contact(
                name="A B", title="Owner", email="a@b.test",
                email_status=VerificationStatus.VERIFIED,
                phone="+16145550142", phone_valid=True, is_decision_maker=True,
            )
        ],
    )
    assert assess(poor_but_documented)[0] > assess(great_but_unknown)[0]


def test_annotate_fills_the_fields_in_place():
    companies = [co(), co(id="c2", postcode="91203")]
    annotate(companies)
    assert all(c.data_quality is not None for c in companies)
    assert companies[1].data_quality > companies[0].data_quality


# --------------------------------------------------------------------------- #
# linkedin extraction
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<a href="https://www.linkedin.com/company/acme-plumbing">us</a>',
         "https://www.linkedin.com/company/acme-plumbing"),
        ('<a href="https://linkedin.com/in/dale-whitaker/">Dale</a>',
         "https://linkedin.com/in/dale-whitaker"),
    ],
)
def test_linkedin_profiles_are_captured(html, expected):
    contacts = website._extract_contacts("call 614 555 0142", html)
    assert contacts and contacts[0].linkedin_url == expected


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://www.linkedin.com">LinkedIn</a>',
        '<a href="https://www.linkedin.com/sharing/share-offsite/?url=x">Share</a>',
        '<a href="https://www.linkedin.com/feed/update/urn:li:activity:123">Post</a>',
    ],
)
def test_non_profile_linkedin_links_are_ignored(html):
    """A share widget or footer icon is not a profile, and putting one in front
    of a user as an outreach channel wastes their click."""
    contacts = website._extract_contacts("call 614 555 0142", html)
    assert contacts and contacts[0].linkedin_url is None


# --------------------------------------------------------------------------- #
# DNS-over-HTTPS fallback
# --------------------------------------------------------------------------- #

@respx.mock
async def test_doh_is_used_when_direct_dns_is_unavailable():
    """Networks that permit HTTPS but drop UDP/53 are common enough that
    failing every lookup on them made validation a silent no-op."""
    respx.get(url__startswith="https://cloudflare-dns.com/dns-query").mock(
        return_value=httpx.Response(
            200,
            json={"Status": 0, "Answer": [{"type": 15, "data": "10 mx.acme.test."}]},
        )
    )
    resolver = MxResolver(timeout=0.001)  # forces the direct path to fail
    hosts = await resolver.mx_hosts("acme.test")
    await resolver.aclose()
    assert hosts == ["mx.acme.test"]


@respx.mock
async def test_doh_nxdomain_is_a_real_answer_not_a_failure():
    # A registrable-looking domain on purpose. `.test` is a reserved TLD that
    # email_validator rejects on syntax alone, so using one here made this pass
    # without DNS ever being consulted.
    respx.get(url__startswith="https://cloudflare-dns.com/dns-query").mock(
        return_value=httpx.Response(200, json={"Status": 3})
    )
    resolver = MxResolver(timeout=0.001)
    verdict = await verify_email("x@nowheredomain12345.com", resolver)
    await resolver.aclose()
    assert verdict.status is VerificationStatus.INVALID
    assert any("bounce" in r for r in verdict.reasons)


@respx.mock
async def test_unreachable_resolvers_stay_unknown():
    """If nothing could answer, the verdict must not harden into INVALID."""
    respx.get(url__startswith="https://cloudflare-dns.com").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get(url__startswith="https://dns.google").mock(
        side_effect=httpx.ConnectError("down")
    )
    resolver = MxResolver(timeout=0.001)
    verdict = await verify_email("x@acmeplumbing12345.com", resolver)
    await resolver.aclose()
    assert verdict.status is VerificationStatus.UNKNOWN
