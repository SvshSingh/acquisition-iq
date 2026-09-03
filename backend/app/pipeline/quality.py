"""Record completeness, scored separately from acquisition fit.

Two different questions get confused constantly in lead tools, and keeping them
apart is the point of this module:

* **Is this a good target?** — the acquisition-fit score.
* **How good is this record?** — this.

They must not be blended. A superb business we know almost nothing about should
not be marked down for our ignorance, and a thoroughly documented mediocre one
should not be flattered by its own completeness. The fit score already reports
its own coverage; this reports the state of the underlying data, which is what a
user needs when deciding whether the next step is outreach or more diligence.

The issues list is the useful half. "72, missing email and website" tells someone
what to go and find; a bare 72 tells them nothing actionable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import Company, VerificationStatus


@dataclass(frozen=True)
class QualityCheck:
    key: str
    label: str
    weight: float
    #: What is missing, phrased so it reads as an instruction to the user.
    gap: str


# Weighted by how much the absence actually costs a searcher, not by how hard
# the field is to obtain. A phone number you can dial matters more than a
# postcode; a website matters because two scoring factors depend on it.
CHECKS: tuple[QualityCheck, ...] = (
    QualityCheck("phone", "Reachable by phone", 0.20, "no phone number"),
    QualityCheck("phone_valid", "Phone validated", 0.10, "phone not validated"),
    QualityCheck("email", "Has an email address", 0.15, "no email address"),
    QualityCheck("email_verified", "Email domain accepts mail", 0.10, "email unverified"),
    QualityCheck("decision_maker", "Named decision maker", 0.15, "no named owner"),
    QualityCheck("website", "Website located", 0.10, "no website"),
    QualityCheck("web_signals", "Website crawled", 0.05, "website not crawled"),
    QualityCheck("ownership", "Ownership form on file", 0.05, "no ownership form"),
    QualityCheck("age", "Trading history known", 0.05, "no founding or licence date"),
    QualityCheck("location", "Locatable", 0.05, "no postcode or coordinates"),
)


def _passes(company: Company, key: str) -> bool:
    contact = company.primary_contact
    match key:
        case "phone":
            return bool(contact and contact.phone)
        case "phone_valid":
            return bool(contact and contact.phone_valid)
        case "email":
            return bool(contact and contact.email)
        case "email_verified":
            return bool(contact and contact.email_status is VerificationStatus.VERIFIED)
        case "decision_maker":
            return any(c.is_decision_maker and c.name for c in company.contacts)
        case "website":
            return bool(company.website)
        case "web_signals":
            return company.web.fetched_at is not None
        case "ownership":
            return bool(company.business_type)
        case "age":
            return bool(company.founded_year or company.licence_issued)
        case "location":
            return bool(company.postcode or company.latitude is not None)
        case _:  # pragma: no cover - guards a typo in CHECKS
            raise KeyError(f"unknown quality check {key!r}")


def assess(company: Company) -> tuple[float, list[str]]:
    """Return `(score 0-100, gaps)` for one record.

    Gaps are ordered by weight, so the first thing listed is the most valuable
    thing missing — a user skimming the list should read the highest-leverage
    action first.
    """
    earned = sum(check.weight for check in CHECKS if _passes(company, check.key))
    gaps = [check.gap for check in sorted(CHECKS, key=lambda c: -c.weight)
            if not _passes(company, check.key)]
    return round(earned * 100, 1), gaps


def annotate(companies: list[Company]) -> None:
    """Fill in `data_quality` and `quality_issues` in place."""
    for company in companies:
        company.data_quality, company.quality_issues = assess(company)


__all__ = ["CHECKS", "QualityCheck", "annotate", "assess"]
