"""Company search, scoring and export.

**Why the client re-weights instead of the server.** Every scored company travels
with its six factor subscores, and the headline number is a weighted sum of
those. So when a searcher drags a weight slider the browser can recompute and
re-sort the whole table in a frame, with no request at all. Round-tripping that
would put 200ms between moving a slider and seeing the consequence, which is the
difference between exploring a thesis and filling in a form.

The server still owns scoring — the factors, the evidence, and the decision about
what was measured are all computed here, and the client is given the arithmetic,
not the judgement.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response

from app.config import settings
from app.schemas import (
    FACTOR_DESCRIPTIONS,
    FACTOR_LABELS,
    BuyBox,
    Company,
    FactorKey,
    FactorWeights,
    ScoredCompany,
    SearchResponse,
)
from app.scoring.engine import ENGINE_VERSION, score_many

logger = logging.getLogger(__name__)
router = APIRouter(tags=["companies"])

# CRM column presets. Exporting a raw dump makes the user do the mapping by hand
# in a spreadsheet before anything can be imported; naming the columns the way
# the destination expects is the difference between a file and a workflow.
CRM_PRESETS: dict[str, dict[str, str]] = {
    "generic": {
        "name": "Company",
        "score": "Acquisition Fit",
        "confidence": "Confidence",
        "coverage": "Thesis Coverage",
        "industry": "Industry",
        "city": "City",
        "state": "State",
        "postcode": "Postcode",
        "phone": "Phone",
        "email": "Email",
        "contact_name": "Contact Name",
        "contact_title": "Contact Title",
        "website": "Website",
        "business_type": "Ownership Form",
        "licence_issued": "Licensed Since",
        "source_url": "Source",
    },
    "hubspot": {
        "name": "Name",
        "score": "acquisition_fit_score",
        "confidence": "acquisition_fit_confidence",
        "coverage": "acquisition_thesis_coverage",
        "industry": "Industry",
        "city": "City",
        "state": "State/Region",
        "postcode": "Postal Code",
        "phone": "Phone Number",
        "email": "Email",
        "contact_name": "Contact Name",
        "contact_title": "Job Title",
        "website": "Website URL",
        "business_type": "ownership_form",
        "licence_issued": "licensed_since",
        "source_url": "record_source",
    },
    "salesforce": {
        "name": "Account Name",
        "score": "Acquisition_Fit__c",
        "confidence": "Acquisition_Confidence__c",
        "coverage": "Thesis_Coverage__c",
        "industry": "Industry",
        "city": "BillingCity",
        "state": "BillingState",
        "postcode": "BillingPostalCode",
        "phone": "Phone",
        "email": "Email",
        "contact_name": "Contact_Full_Name__c",
        "contact_title": "Title",
        "website": "Website",
        "business_type": "Ownership_Form__c",
        "licence_issued": "Licensed_Since__c",
        "source_url": "Record_Source__c",
    },
}


def _seed_path() -> Path:
    configured = Path(settings.seed_dataset_path)
    if configured.is_absolute() and configured.exists():
        return configured
    root = Path(__file__).resolve().parents[4]
    for candidate in (root / "data" / "seed_glendale.json", root / configured.name):
        if candidate.exists():
            return candidate
    return root / "data" / "seed_glendale.json"


@lru_cache
def load_dataset() -> tuple[dict[str, Any], list[Company]]:
    """Read the committed snapshot once per process.

    Cached deliberately: the file is a few hundred kilobytes of JSON and parsing
    it per request would dominate the response time of every search.
    """
    path = _seed_path()
    if not path.exists():
        logger.error("seed dataset missing at %s", path)
        return {"count": 0, "companies": []}, []

    payload = json.loads(path.read_text(encoding="utf-8"))
    companies = [Company(**row) for row in payload.get("companies", [])]
    logger.info("loaded %d companies from %s", len(companies), path.name)
    return payload, companies


def _matches(
    company: Company,
    *,
    q: str | None,
    industry: str | None,
    city: str | None,
    business_type: str | None,
    has_employees: bool | None,
    min_age: int | None,
) -> bool:
    if q:
        needle = q.lower()
        haystack = f"{company.name} {company.city or ''} {company.industry or ''}".lower()
        if needle not in haystack:
            return False
    if industry and (company.industry or "").lower() != industry.lower():
        return False
    if city and (company.city or "").lower() != city.lower():
        return False
    if business_type and (company.business_type or "").lower() != business_type.lower():
        return False
    if has_employees is not None and company.has_employees is not has_employees:
        return False
    if min_age is not None:
        year = company.founded_year
        if year is None or (datetime.now(UTC).year - year) < min_age:
            return False
    return True


@router.get("/companies", response_model=SearchResponse)
def search_companies(
    q: str | None = Query(default=None, description="Free-text match on name, city or trade"),
    industry: str | None = None,
    city: str | None = None,
    business_type: str | None = None,
    has_employees: bool | None = None,
    min_age: int | None = Query(default=None, ge=0, le=150),
    min_score: float = Query(default=0.0, ge=0.0, le=100.0),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> SearchResponse:
    """Scored companies, filtered.

    Returned at the engine's default weights. The client re-weights locally —
    see the module docstring for why.
    """
    started = time.perf_counter()
    payload, companies = load_dataset()

    filtered = [
        c
        for c in companies
        if _matches(
            c,
            q=q,
            industry=industry,
            city=city,
            business_type=business_type,
            has_employees=has_employees,
            min_age=min_age,
        )
    ]
    scored = [s for s in score_many(filtered) if s.score.score >= min_score]

    return SearchResponse(
        results=scored[offset : offset + limit],
        total=len(scored),
        took_ms=int((time.perf_counter() - started) * 1000),
        from_cache=True,
        source=str(payload.get("market", {}).get("label", "seed snapshot")),
    )


@router.get("/companies/{company_id:path}", response_model=ScoredCompany)
def get_company(company_id: str) -> ScoredCompany:
    _, companies = load_dataset()
    for company in companies:
        if company.id == company_id:
            return score_many([company])[0]
    raise HTTPException(status_code=404, detail=f"no company with id {company_id!r}")


@router.get("/meta")
def meta() -> dict[str, Any]:
    """Everything the UI needs to render controls without hardcoding it.

    The filter options are derived from the data rather than declared here, so a
    dataset from a different market cannot leave the UI offering filters that
    match nothing.
    """
    payload, companies = load_dataset()
    weights = FactorWeights()
    return {
        "market": payload.get("market", {}),
        "generated_at": payload.get("generated_at"),
        "sources": payload.get("sources", []),
        "count": len(companies),
        "engine_version": ENGINE_VERSION,
        "factors": [
            {
                "key": key.value,
                "label": FACTOR_LABELS[key],
                "description": FACTOR_DESCRIPTIONS[key],
                "default_weight": weights.as_map()[key],
            }
            for key in FactorKey
        ],
        "buy_box": BuyBox().model_dump(),
        "filters": {
            "industry": sorted({c.industry for c in companies if c.industry}),
            "city": sorted({c.city for c in companies if c.city}),
            "business_type": sorted({c.business_type for c in companies if c.business_type}),
        },
        "crm_presets": sorted(CRM_PRESETS),
    }


@router.get("/export")
def export_csv(
    ids: str | None = Query(default=None, description="Comma-separated company ids"),
    preset: Literal["generic", "hubspot", "salesforce"] = "generic",
    weights_json: str | None = Query(default=None, alias="weights"),
) -> Response:
    """CSV for the given companies, with CRM-ready column names.

    Written with `csv.writer` and a UTF-8 BOM rather than by joining strings:
    company names contain commas and quotes, and Excel opens a BOM-less UTF-8
    file in the local codepage, which mangles every accented name. A file that
    looks broken on opening is not an export.
    """
    _, companies = load_dataset()
    wanted = {i.strip() for i in ids.split(",")} if ids else None
    selected = [c for c in companies if wanted is None or c.id in wanted]

    weights = FactorWeights()
    if weights_json:
        try:
            weights = FactorWeights(**json.loads(weights_json))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"bad weights: {exc}") from exc

    columns = CRM_PRESETS[preset]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(columns.values()))

    for item in score_many(selected, weights=weights):
        company, result = item.company, item.score
        contact = company.primary_contact
        writer.writerow(
            [
                company.name,
                f"{result.score:.1f}",
                result.confidence.value,
                f"{result.covered_weight:.2f}",
                company.industry or "",
                company.city or "",
                company.state or "",
                company.postcode or "",
                contact.phone if contact else "",
                contact.email if contact else "",
                contact.name if contact else "",
                contact.title if contact else "",
                company.website or "",
                company.business_type or "",
                company.licence_issued.isoformat() if company.licence_issued else "",
                company.source_url or "",
            ]
        )

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return Response(
        # BOM so Excel reads it as UTF-8 rather than the local codepage.
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="acquisitioniq-{preset}-{stamp}.csv"'
        },
    )


__all__ = ["CRM_PRESETS", "load_dataset", "router"]
