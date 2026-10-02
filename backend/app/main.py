"""FastAPI application.

A long-running container, deliberately not a serverless function. Two reasons,
both worth stating out loud because the trade-off was chosen rather than
defaulted into: scrape jobs behind "refresh from source" outlast a typical
serverless timeout, and the connection pool and dataset cache are only worth
having if the process survives between requests.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.routes.companies import router as companies_router
from app.cache import close_cache
from app.config import settings
from app.db.session import dispose_engine
from app.scoring.engine import ENGINE_VERSION
from app.store import get_store, init_store, load_dataset, storage_status

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Parse the dataset at startup rather than on the first request, so a cold
    # container costs the operator a slow boot instead of costing the first
    # visitor a slow page.
    # The snapshot is parsed either way: it is the serving path with no
    # database, the bootstrap source for an empty one, and the fallback if a
    # configured one stops answering.
    load_dataset()
    store = await init_store()
    logger.info(
        "%s ready: %d companies from %s, engine %s",
        settings.app_name,
        await store.count(),
        store.name,
        ENGINE_VERSION,
    )
    yield
    await close_cache()
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version=ENGINE_VERSION,
    description=(
        "Explainable acquisition-fit scoring for search-fund lead generation. "
        "Every score carries the evidence behind it and the signals it could not find."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
# The company payload is repetitive JSON — evidence strings, factor labels,
# source URLs — which compresses to a fraction of its size.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(companies_router, prefix="/api")


@app.get("/", include_in_schema=False)
async def index() -> dict[str, object]:
    """An index at the root, because people paste API URLs into browsers.

    Every route lives under `/api`, so `/` returned a bare `{"detail":"Not
    Found"}` — technically correct and useless to a human who has just been
    handed the link. This says what the service is and where to go next.
    """
    return {
        "service": settings.app_name,
        "description": "Explainable acquisition-fit scoring for search funds.",
        "engine_version": ENGINE_VERSION,
        "companies": await get_store().count(),
        "endpoints": {
            "interactive_docs": "/docs",
            "health": "/api/health",
            "dataset_and_filters": "/api/meta",
            "search": "/api/companies?limit=10&min_score=60",
            "one_company": "/api/companies/{id}",
            "csv_export": "/api/export?preset=hubspot",
        },
        "repository": settings.project_url,
    }


@app.get("/api/health")
async def health() -> dict[str, object]:
    """Liveness, and an honest account of where the data is coming from.

    Always 200 while the process can answer: with a database down the service
    is degraded, not dead (it is serving the snapshot), and a platform that
    restarts a container for reporting its own degradation only makes things
    worse. The body is where the truth is: `storage` names the store answering
    reads right now, and `database` says why if that is not Postgres.
    """
    companies = await get_store().count()
    storage = await storage_status()
    degraded = not companies or storage.database == "unreachable"
    body: dict[str, object] = {
        "status": "degraded" if degraded else "ok",
        "engine_version": ENGINE_VERSION,
        "companies": companies,
        "environment": settings.environment,
        "storage": storage.storage,
        "database": storage.database,
    }
    if storage.detail:
        body["database_detail"] = storage.detail
    return body


__all__ = ["app"]
