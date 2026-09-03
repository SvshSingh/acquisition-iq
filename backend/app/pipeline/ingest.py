"""Ingest an arbitrary lead list and map it onto the scoring schema.

This is the layer that turns AcquisitionIQ from a parallel tool into something
that sits on top of a lead-gen product. A searcher exports their list — from
SaaSquatch, a CRM, a broker spreadsheet — drops it in, and gets it scored for
acquisition fit with the same explainable breakdown, without leaving whatever
workflow produced the list.

The mapping is intentionally forgiving and intentionally transparent. Every
source names its columns differently, so headers are matched against a table of
aliases; but the caller is always told exactly which column became which field,
so a wrong guess is visible rather than silent. Nothing is invented — a column
we cannot place is reported as unmapped, and a field no column supplied stays
empty, which the scoring engine already reports as missing rather than filling in.

The synergy worth noting: a SaaSquatch export carries employee-count and revenue
estimates, which are exactly the size signals the public licence and map sources
cannot provide. So an imported list makes the buy-box factor measurable where the
seed dataset can only report it as unknown — the two sources are complementary,
not redundant.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.schemas import Company, Contact, VerificationStatus

# Canonical field -> header aliases. Matched case-insensitively after stripping
# non-alphanumerics, so "Company Name", "company_name" and "COMPANYNAME" all hit.
_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("company", "companyname", "business", "businessname", "accountname", "name", "organization"),
    "website": ("website", "websiteurl", "url", "domain", "web", "site", "homepage", "companywebsite"),
    "email": ("email", "emailaddress", "contactemail", "workemail", "primaryemail"),
    "phone": ("phone", "phonenumber", "telephone", "contactphone", "tel", "mobile", "workphone"),
    "city": ("city", "town", "billingcity", "locationcity"),
    "state": ("state", "region", "province", "billingstate", "stateregion"),
    "postcode": ("zip", "zipcode", "postalcode", "postcode", "billingpostalcode", "postal"),
    "industry": ("industry", "sector", "category", "trade", "vertical", "naicsdescription"),
    "employee_count": ("employees", "employeecount", "headcount", "staff", "companysize", "numberofemployees", "size"),
    "revenue_usd": ("revenue", "annualrevenue", "sales", "estimatedrevenue", "revenueusd", "airevenue"),
    "founded_year": ("founded", "yearfounded", "established", "foundedyear", "foundingyear"),
    "business_type": ("ownership", "entitytype", "businesstype", "ownershipform", "legalform"),
    "contact_name": ("contactname", "contact", "owner", "ownername", "decisionmaker", "fullname", "name2"),
    "contact_title": ("title", "jobtitle", "role", "position", "contacttitle"),
}

MAX_ROWS = 2000
_MONEY = str.maketrans("", "", "$,€£ ")


@dataclass
class IngestResult:
    companies: list[Company]
    column_mapping: dict[str, str]  # source header -> canonical field
    unmapped_columns: list[str]
    row_count: int
    skipped_rows: int = 0
    fields_present: list[str] = field(default_factory=list)


def _canonical(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum())


def build_mapping(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map source headers to canonical fields. Returns (mapping, unmapped).

    First alias wins, and each canonical field is claimed once — so a sheet with
    both "Email" and "Work Email" does not have the second silently overwrite the
    first. The order of `_ALIASES` therefore encodes preference.
    """
    lookup: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            lookup.setdefault(alias, canonical)

    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    unmapped: list[str] = []
    for header in headers:
        field_name = lookup.get(_canonical(header))
        if field_name is not None and field_name not in claimed:
            mapping[header] = field_name
            claimed.add(field_name)
        else:
            unmapped.append(header)
    return mapping, unmapped


def _to_int(value: str) -> int | None:
    digits = value.translate(_MONEY).strip()
    # "11-50" style bands (common in CRM size fields) → take the upper bound,
    # which is the more useful end for a buy-box ceiling check.
    if "-" in digits:
        digits = digits.split("-")[-1].strip()
    try:
        return int(float(digits)) if digits else None
    except ValueError:
        return None


def _to_money(value: str) -> float | None:
    raw = value.translate(_MONEY).strip().lower()
    if not raw:
        return None
    mult = 1.0
    if raw.endswith("k"):
        mult, raw = 1_000.0, raw[:-1]
    elif raw.endswith("m"):
        mult, raw = 1_000_000.0, raw[:-1]
    elif raw.endswith("b"):
        mult, raw = 1_000_000_000.0, raw[:-1]
    try:
        return float(raw) * mult if raw else None
    except ValueError:
        return None


def _to_year(value: str) -> int | None:
    digits = "".join(ch for ch in value if ch.isdigit())[:4]
    if len(digits) == 4:
        year = int(digits)
        if 1800 <= year <= date.today().year:
            return year
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_to_company(row: dict[str, str], mapping: dict[str, str], index: int) -> Company | None:
    """Map one CSV row onto a Company using the resolved mapping."""
    vals: dict[str, str | None] = {}
    for header, canonical in mapping.items():
        vals[canonical] = _clean(row.get(header))

    name = vals.get("name")
    if not name:
        return None  # a row with no company name is not a lead

    website = vals.get("website")
    if website and "://" not in website:
        website = f"https://{website}"

    contact: Contact | None = None
    if any(vals.get(k) for k in ("email", "phone", "contact_name")):
        contact = Contact(
            name=vals.get("contact_name"),
            title=vals.get("contact_title"),
            email=vals.get("email"),
            email_status=VerificationStatus.UNKNOWN,
            phone=vals.get("phone"),
            phone_valid=None,
            is_decision_maker=bool(vals.get("contact_name")),
        )

    return Company(
        id=f"import:{index}",
        name=name,
        website=website,
        industry=vals.get("industry"),
        city=vals.get("city"),
        state=vals.get("state"),
        postcode=vals.get("postcode"),
        business_type=vals.get("business_type"),
        employee_count=_to_int(emp) if (emp := vals.get("employee_count")) else None,
        revenue_usd=_to_money(rev) if (rev := vals.get("revenue_usd")) else None,
        founded_year=_to_year(yr) if (yr := vals.get("founded_year")) else None,
        contacts=[contact] if contact else [],
        source="import",
        first_seen=date.today(),
        raw=dict(row),
    )


def ingest_csv(content: str) -> IngestResult:
    """Parse CSV text into scored-ready companies. Never raises on bad rows.

    A malformed row is skipped and counted, not fatal — a single stray line
    should not reject a thousand good leads.
    """
    reader = csv.DictReader(io.StringIO(content))
    headers = [h for h in (reader.fieldnames or []) if h and h.strip()]
    mapping, unmapped = build_mapping(headers)

    companies: list[Company] = []
    skipped = 0
    for line_no, row in enumerate(reader, start=1):
        if line_no > MAX_ROWS:
            break
        try:
            company = _row_to_company(row, mapping, line_no)
        except Exception:  # one malformed row must not fail the whole batch
            company = None
        if company is None:
            skipped += 1
        else:
            companies.append(company)

    return IngestResult(
        companies=companies,
        column_mapping=mapping,
        unmapped_columns=unmapped,
        row_count=len(companies),
        skipped_rows=skipped,
        fields_present=sorted(set(mapping.values())),
    )


__all__ = ["MAX_ROWS", "IngestResult", "build_mapping", "ingest_csv"]
