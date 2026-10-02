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

import asyncio
import csv
import io
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile

from app.pipeline.domains import find_website
from app.pipeline.ingest import ingest_csv
from app.pipeline.quality import assess
from app.pipeline.scrapers.http import PoliteClient
from app.pipeline.scrapers.website import crawl_site
from app.pipeline.validate import MxResolver, validate_contacts
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
from app.store import CompanyFilters, get_store

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


@router.get("/companies", response_model=SearchResponse)
async def search_companies(
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
    store = get_store()

    # Filtering happens in the store, in SQL when Postgres is serving. Scoring
    # stays here, in Python, on purpose: the engine is deterministic code under
    # a golden-file test, and re-expressing it as SQL to push `min_score` down
    # would mean two implementations of the judgement instead of one.
    filtered = await store.search(
        CompanyFilters(
            q=q,
            industry=industry,
            city=city,
            business_type=business_type,
            has_employees=has_employees,
            min_age=min_age,
        )
    )
    scored = [s for s in score_many(filtered) if s.score.score >= min_score]
    dataset = await store.meta()

    return SearchResponse(
        results=scored[offset : offset + limit],
        total=len(scored),
        took_ms=int((time.perf_counter() - started) * 1000),
        from_cache=True,
        source=dataset.label,
    )


@router.get("/companies/{company_id:path}/history")
async def company_history(company_id: str) -> dict[str, Any]:
    """Every score this company has been given, newest first.

    A score that moves is information: a site that went stale, an owner who
    finally listed an email. Each refresh records its result, so the movement
    is something a user can look at rather than something that silently
    overwrote the previous number. Empty when serving the snapshot, which keeps
    no history by construction.

    Declared before the catch-all company route below, which would otherwise
    swallow the `/history` suffix as part of the id.
    """
    store = get_store()
    if await store.get(company_id) is None:
        raise HTTPException(status_code=404, detail=f"no company with id {company_id!r}")
    records = await store.history(company_id)
    return {
        "company_id": company_id,
        "history": [
            {
                "score": r.score,
                "confidence": r.confidence,
                "engine_version": r.engine_version,
                "scored_at": r.scored_at.isoformat(),
            }
            for r in records
        ],
    }


@router.get("/companies/{company_id:path}", response_model=ScoredCompany)
async def get_company(company_id: str) -> ScoredCompany:
    company = await get_store().get(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail=f"no company with id {company_id!r}")
    return score_many([company])[0]


@router.post("/companies/{company_id:path}/refresh", response_model=ScoredCompany)
async def refresh_company(company_id: str, response: Response) -> ScoredCompany:
    """Re-fetch this company from source, re-validate, and re-score.

    This is the live path, and the reason the backend is a long-running
    container rather than a serverless function. It crawls the company's own
    site, re-runs contact validation against DNS, and re-scores — work that
    outlasts a typical serverless timeout and benefits from a warm connection
    pool. Documenting that trade-off while having no endpoint that exercises it
    would have been an architecture decision justified by a feature that did not
    exist.

    With Postgres serving, the result is written back: the company row is
    updated, the crawl's raw output is kept beside it, and the score joins the
    company's history. Work a user triggered is not thrown away when the
    response is sent.

    The committed snapshot, by contrast, is never written to. Quietly mutating
    a shipped file would mean two people running the same build saw different
    data with no way to tell why. The `X-Persisted` response header says which
    of the two happened.
    """
    store = get_store()
    company = await store.get(company_id)
    if company is None:
        raise HTTPException(status_code=404, detail=f"no company with id {company_id!r}")

    # A copy, so a failed or partial refresh cannot corrupt what is being served.
    working = company.model_copy(deep=True)

    async with PoliteClient() as client:
        if not working.website:
            match = await find_website(client, working)
            if match is not None:
                working.website = match.url
                working.website_source = f"inferred:{match.method}"
                working.website_evidence = match.detail

        if working.website:
            signals, found = await crawl_site(client, working.website)
            working.web = signals
            if found and not working.contacts:
                working.contacts = found
            elif found and working.contacts:
                existing, fresh = working.contacts[0], found[0]
                merged = {
                    field: getattr(fresh, field)
                    for field in ("email", "name", "title", "linkedin_url")
                    if not getattr(existing, field) and getattr(fresh, field)
                }
                if merged:
                    working.contacts[0] = existing.model_copy(update=merged)

    if working.contacts:
        resolver = MxResolver()
        try:
            working.contacts = await validate_contacts(working.contacts, resolver)
        finally:
            await resolver.aclose()

    working.last_refreshed = datetime.now(UTC)
    working.data_quality, working.quality_issues = assess(working)

    scored = score_many([working])[0]
    persisted = await store.save_refresh(scored)
    response.headers["X-Persisted"] = "true" if persisted else "false"
    return scored


@router.post("/score-upload")
async def score_upload(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    """Score a lead list the user brings in — the layer on top of any lead source.

    A searcher exports from SaaSquatch, a CRM or a broker sheet, drops the CSV
    here, and gets it validated and acquisition-scored with the same explainable
    breakdown as the seed data. The point is workflow fit: they do not leave
    whatever produced the list, and they do not adopt a new tool to enrich it.

    Only the columns present are used, and the response says exactly which column
    became which field. Contacts are validated (phone to E.164, email domain
    against DNS) because that is fast and is the named enrichment bonus; websites
    are deliberately not crawled synchronously here — that is per-row network work
    with an abuse surface, and the per-company refresh endpoint covers it.

    Nothing web-dependent is invented for rows without a site: the coverage meter
    already reports how much of the thesis the supplied columns could support,
    which is exactly the honest behaviour a bring-your-own-list flow needs.
    """
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(status_code=413, detail="file too large (limit 5MB)")
    try:
        text = raw.decode("utf-8-sig")  # -sig strips a BOM if the export has one
    except UnicodeDecodeError:
        text = raw.decode("latin-1")  # Excel exports are frequently not UTF-8

    result = ingest_csv(text)
    if not result.companies:
        raise HTTPException(
            status_code=422,
            detail=(
                "No scorable rows found. The file needs at least a company-name "
                f"column. Columns seen: {', '.join(result.unmapped_columns) or 'none'}."
            ),
        )

    # Concurrent across rows, not one at a time. Most rows carry a single
    # contact, so validating per row serialises the stage into consecutive DNS
    # round trips — at the 2,000-row cap that is minutes of latency and a near
    # certain gateway timeout. The per-domain memo inside MxResolver means the
    # fan-out costs the resolvers little: queries collapse onto the handful of
    # distinct mail domains a real list actually contains.
    resolver = MxResolver()
    with_contacts = [c for c in result.companies if c.contacts]
    semaphore = asyncio.Semaphore(24)

    async def validate_one(company: Company) -> None:
        async with semaphore:
            company.contacts = await validate_contacts(company.contacts, resolver)

    try:
        await asyncio.gather(*(validate_one(c) for c in with_contacts))
    finally:
        await resolver.aclose()

    for company in result.companies:
        company.data_quality, company.quality_issues = assess(company)

    scored = score_many(result.companies)
    return {
        "results": [s.model_dump(mode="json") for s in scored],
        "total": len(scored),
        "column_mapping": result.column_mapping,
        "unmapped_columns": result.unmapped_columns,
        "fields_present": result.fields_present,
        "skipped_rows": result.skipped_rows,
        "source": file.filename or "uploaded list",
    }


@router.get("/meta")
async def meta() -> dict[str, Any]:
    """Everything the UI needs to render controls without hardcoding it.

    The filter options are derived from the data rather than declared here, so a
    dataset from a different market cannot leave the UI offering filters that
    match nothing.
    """
    store = get_store()
    dataset = await store.meta()
    weights = FactorWeights()
    return {
        "market": dataset.market,
        "generated_at": dataset.generated_at,
        "sources": dataset.sources,
        "count": dataset.count,
        "storage": store.name,
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
            "industry": dataset.industries,
            "city": dataset.cities,
            "business_type": dataset.business_types,
        },
        "crm_presets": sorted(CRM_PRESETS),
    }


@router.get("/export")
async def export_csv(
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
    wanted = {i.strip() for i in ids.split(",") if i.strip()} if ids else None
    selected = await get_store().get_many(wanted)

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


__all__ = ["CRM_PRESETS", "router"]
