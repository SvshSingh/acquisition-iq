"""Translation between the API's `Company` and its database row.

Kept in one place, and symmetric on purpose. The schema the scoring engine reads
and the row Postgres stores drift apart the moment a field is added to one and
not the other — which is exactly how the first schema ended up unable to hold
the facts the collector had learned to gather. `tests/test_store.py` round-trips
every company in the snapshot through both functions and requires the result to
be identical, so a field that exists on only one side fails a test rather than
quietly vanishing on the way to disk.
"""

from __future__ import annotations

from typing import Any

from app.db import models
from app.schemas import Company, Contact, VerificationStatus, WebSignals


def company_values(company: Company, *, market_key: str | None) -> dict[str, Any]:
    """Column values for an insert or upsert of `company`.

    Contacts are not included: they are rows of their own and are replaced
    wholesale by the caller, since a re-enriched contact list supersedes the
    old one rather than merging with it.
    """
    return {
        "id": company.id,
        "name": company.name,
        "market_key": market_key,
        "domain": company.domain,
        "website": company.website,
        "website_source": company.website_source,
        "website_evidence": company.website_evidence,
        "industry": company.industry,
        "naics": company.naics,
        "city": company.city,
        "state": company.state,
        "country": company.country,
        "postcode": company.postcode,
        "latitude": company.latitude,
        "longitude": company.longitude,
        "employee_count": company.employee_count,
        "employee_count_is_estimate": company.employee_count_is_estimate,
        "revenue_usd": company.revenue_usd,
        "revenue_is_estimate": company.revenue_is_estimate,
        "founded_year": company.founded_year,
        "business_type": company.business_type,
        "has_employees": company.has_employees,
        "licence_number": company.licence_number,
        "licence_issued": company.licence_issued,
        "licence_classifications": list(company.licence_classifications),
        "peer_count_in_niche": company.peer_count_in_niche,
        "sibling_location_count": company.sibling_location_count,
        "web": company.web.model_dump(mode="json"),
        "source": company.source,
        "source_url": company.source_url,
        "first_seen": company.first_seen,
        "last_refreshed": company.last_refreshed,
        "data_quality": company.data_quality,
        "quality_issues": list(company.quality_issues),
    }


def contact_values(company_id: str, contact: Contact) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "name": contact.name,
        "title": contact.title,
        "email": contact.email,
        "email_status": contact.email_status.value,
        "phone": contact.phone,
        "phone_valid": contact.phone_valid,
        "linkedin_url": contact.linkedin_url,
        "is_decision_maker": contact.is_decision_maker,
    }


def to_contact(row: models.Contact) -> Contact:
    return Contact(
        name=row.name,
        title=row.title,
        email=row.email,
        email_status=VerificationStatus(row.email_status),
        phone=row.phone,
        phone_valid=row.phone_valid,
        linkedin_url=row.linkedin_url,
        is_decision_maker=row.is_decision_maker,
    )


def to_company(row: models.Company) -> Company:
    return Company(
        id=row.id,
        name=row.name,
        domain=row.domain,
        website=row.website,
        website_source=row.website_source,
        website_evidence=row.website_evidence,
        industry=row.industry,
        naics=row.naics,
        city=row.city,
        state=row.state,
        country=row.country,
        postcode=row.postcode,
        latitude=row.latitude,
        longitude=row.longitude,
        employee_count=row.employee_count,
        employee_count_is_estimate=row.employee_count_is_estimate,
        revenue_usd=row.revenue_usd,
        revenue_is_estimate=row.revenue_is_estimate,
        founded_year=row.founded_year,
        business_type=row.business_type,
        has_employees=row.has_employees,
        licence_number=row.licence_number,
        licence_issued=row.licence_issued,
        licence_classifications=list(row.licence_classifications or []),
        # Insertion order is the order the collector ranked them in, and the
        # API's "primary contact" is the first one, so it must survive storage.
        contacts=[to_contact(c) for c in sorted(row.contacts, key=lambda c: c.id or 0)],
        web=WebSignals(**(row.web or {})),
        peer_count_in_niche=row.peer_count_in_niche,
        sibling_location_count=row.sibling_location_count,
        source=row.source,
        source_url=row.source_url,
        first_seen=row.first_seen,
        last_refreshed=row.last_refreshed,
        data_quality=row.data_quality,
        quality_issues=list(row.quality_issues or []),
    )


__all__ = ["company_values", "contact_values", "to_company", "to_contact"]
