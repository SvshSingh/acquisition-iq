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

from app.api.routes.companies import load_dataset
from app.api.routes.companies import router as companies_router
from app.cache import close_cache
from app.config import settings
from app.db.session import dispose_engine
from app.scoring.engine import ENGINE_VERSION

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
    _, companies = load_dataset()
    logger.info("%s ready: %d companies, engine %s", settings.app_name, len(companies), ENGINE_VERSION)
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


@app.get("/api/health")
def health() -> dict[str, object]:
    _, companies = load_dataset()
    return {
        "status": "ok" if companies else "degraded",
        "engine_version": ENGINE_VERSION,
        "companies": len(companies),
        "environment": settings.environment,
    }


__all__ = ["app"]
