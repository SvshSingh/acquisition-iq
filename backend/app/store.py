"""Where companies come from.

The API used to read a JSON file directly, in five separate places. That was
honest for a demo and wrong for a product: nothing a user did could ever be
kept, the trigram index the schema declared was never queried, and "Postgres"
was a diagram rather than a dependency.

Every route now reads through a `CompanyStore`, and there are two of them:

- `PostgresStore` is the serving path whenever `DATABASE_URL` is set. Filters
  run in SQL, a refresh is written back, and each refresh leaves a score and a
  raw payload behind so a number can be traced to what was seen at the time.
- `SnapshotStore` serves the committed JSON file. It is what runs with no
  database configured, and what `ResilientStore` falls back to when a configured
  database cannot be reached.

That fallback is a choice worth defending. A lead list that is a day stale is
still useful; a 500 is not. So a read that fails against Postgres is answered
from the snapshot and logged loudly, and `/api/health` reports the database as
unreachable rather than pretending all is well. Writes are never silently
redirected — a refresh that could not be saved says so in its response.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

from sqlalchemy import delete, distinct, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import models
from app.db.mapping import company_values, contact_values, to_company
from app.db.session import connection_spec, database_configured, get_sessionmaker
from app.schemas import BuyBox, Company, ScoredCompany
from app.scoring.engine import score_cache_key

logger = logging.getLogger(__name__)

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# shared shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CompanyFilters:
    q: str | None = None
    industry: str | None = None
    city: str | None = None
    business_type: str | None = None
    has_employees: bool | None = None
    min_age: int | None = None


@dataclass(frozen=True)
class DatasetMeta:
    """What the UI needs to describe the dataset and build its filter controls."""

    market: dict[str, Any] = field(default_factory=dict)
    generated_at: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    industries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    business_types: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return str(self.market.get("label") or "seed snapshot")


@dataclass(frozen=True)
class ScoreRecord:
    """One persisted scoring run, for a company's history."""

    score: float
    confidence: str
    engine_version: str
    scored_at: datetime


class CompanyStore(Protocol):
    name: str

    async def search(self, filters: CompanyFilters) -> list[Company]: ...

    async def get(self, company_id: str) -> Company | None: ...

    async def get_many(self, ids: set[str] | None) -> list[Company]: ...

    async def meta(self) -> DatasetMeta: ...

    async def count(self) -> int: ...

    async def save_refresh(self, scored: ScoredCompany) -> bool:
        """Persist a refreshed company and its score. True if it was kept."""
        ...

    async def history(self, company_id: str) -> list[ScoreRecord]: ...


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #


def _seed_path() -> Path:
    configured = Path(settings.seed_dataset_path)
    if configured.is_absolute() and configured.exists():
        return configured
    root = Path(__file__).resolve().parents[2]
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


def _terms(q: str) -> list[str]:
    """Split a free-text query into the words that must each match.

    "plumbing burbank" should find plumbers in Burbank. Matching the whole
    string as one substring cannot, because the trade and the city are different
    fields; requiring every word to appear in *some* searchable field can. Both
    stores use this, so the snapshot and Postgres agree on what a query means.
    """
    return [t for t in q.lower().split() if t]


def _matches(company: Company, f: CompanyFilters) -> bool:
    if f.q:
        fields = [(v or "").lower() for v in (company.name, company.city, company.industry)]
        if not all(any(term in value for value in fields) for term in _terms(f.q)):
            return False
    if f.industry and (company.industry or "").lower() != f.industry.lower():
        return False
    if f.city and (company.city or "").lower() != f.city.lower():
        return False
    if f.business_type and (company.business_type or "").lower() != f.business_type.lower():
        return False
    if f.has_employees is not None and company.has_employees is not f.has_employees:
        return False
    if f.min_age is not None:
        year = company.founded_year
        if year is None or (datetime.now(UTC).year - year) < f.min_age:
            return False
    return True


class SnapshotStore:
    """The committed JSON file. Read-only by construction."""

    name = "snapshot"

    async def search(self, filters: CompanyFilters) -> list[Company]:
        _, companies = load_dataset()
        return [c for c in companies if _matches(c, filters)]

    async def get(self, company_id: str) -> Company | None:
        _, companies = load_dataset()
        return next((c for c in companies if c.id == company_id), None)

    async def get_many(self, ids: set[str] | None) -> list[Company]:
        _, companies = load_dataset()
        return [c for c in companies if ids is None or c.id in ids]

    async def meta(self) -> DatasetMeta:
        payload, companies = load_dataset()
        return DatasetMeta(
            market=dict(payload.get("market", {})),
            generated_at=payload.get("generated_at"),
            sources=list(payload.get("sources", [])),
            count=len(companies),
            industries=sorted({c.industry for c in companies if c.industry}),
            cities=sorted({c.city for c in companies if c.city}),
            business_types=sorted({c.business_type for c in companies if c.business_type}),
        )

    async def count(self) -> int:
        _, companies = load_dataset()
        return len(companies)

    async def save_refresh(self, scored: ScoredCompany) -> bool:
        # The shipped file is never written to: two people running the same
        # build would otherwise see different data with no way to tell why.
        return False

    async def history(self, company_id: str) -> list[ScoreRecord]:
        return []


# --------------------------------------------------------------------------- #
# postgres
# --------------------------------------------------------------------------- #


def _like(needle: str) -> str:
    """A LIKE pattern that matches `needle` literally, anywhere."""
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class PostgresStore:
    """Postgres as the serving path."""

    name = "postgres"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def search(self, filters: CompanyFilters) -> list[Company]:
        c = models.Company
        stmt = select(c)
        for term in _terms(filters.q or ""):
            pattern = _like(term)
            # `name` is the one that matters at scale and the one with a trigram
            # GIN index, which is what lets an unanchored ILIKE avoid a
            # sequential scan. City and trade are low-cardinality.
            stmt = stmt.where(
                or_(
                    c.name.ilike(pattern, escape="\\"),
                    c.city.ilike(pattern, escape="\\"),
                    c.industry.ilike(pattern, escape="\\"),
                )
            )
        if filters.industry:
            stmt = stmt.where(func.lower(c.industry) == filters.industry.lower())
        if filters.city:
            stmt = stmt.where(func.lower(c.city) == filters.city.lower())
        if filters.business_type:
            stmt = stmt.where(func.lower(c.business_type) == filters.business_type.lower())
        if filters.has_employees is not None:
            stmt = stmt.where(c.has_employees.is_(filters.has_employees))
        if filters.min_age is not None:
            stmt = stmt.where(c.founded_year <= datetime.now(UTC).year - filters.min_age)
        # A stable order, so equal scores do not shuffle between requests and
        # break pagination.
        stmt = stmt.order_by(c.id)

        async with self._sessionmaker() as session:
            rows = (await session.scalars(stmt)).all()
            return [to_company(r) for r in rows]

    async def get(self, company_id: str) -> Company | None:
        async with self._sessionmaker() as session:
            row = await session.get(models.Company, company_id)
            return to_company(row) if row else None

    async def get_many(self, ids: set[str] | None) -> list[Company]:
        stmt = select(models.Company).order_by(models.Company.id)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(models.Company.id.in_(ids))
        async with self._sessionmaker() as session:
            rows = (await session.scalars(stmt)).all()
            return [to_company(r) for r in rows]

    async def meta(self) -> DatasetMeta:
        c = models.Company
        async with self._sessionmaker() as session:
            market = await session.scalar(
                select(models.Market).order_by(models.Market.generated_at.desc().nulls_last())
            )
            count = await session.scalar(select(func.count()).select_from(c)) or 0

            async def options(column: Any) -> list[str]:
                values = await session.scalars(
                    select(distinct(column)).where(column.is_not(None)).order_by(column)
                )
                return [v for v in values.all() if v]

            return DatasetMeta(
                market=(
                    {"key": market.key, "label": market.label, "state": market.state}
                    if market
                    else {}
                ),
                generated_at=(
                    market.generated_at.isoformat() if market and market.generated_at else None
                ),
                sources=list(market.sources) if market else [],
                count=int(count),
                industries=await options(c.industry),
                cities=await options(c.city),
                business_types=await options(c.business_type),
            )

    async def count(self) -> int:
        async with self._sessionmaker() as session:
            return int(await session.scalar(select(func.count()).select_from(models.Company)) or 0)

    async def save_refresh(self, scored: ScoredCompany) -> bool:
        company, result = scored.company, scored.score
        async with self._sessionmaker() as session, session.begin():
            existing_market = await session.scalar(
                select(models.Company.market_key).where(models.Company.id == company.id)
            )
            await upsert_companies(session, [company], market_key=existing_market)

            # What the crawl actually returned, kept beside what we concluded
            # from it. When a score is questioned later the page may have
            # changed; this is the only record of what it said.
            session.add(
                models.RawPayload(
                    company_id=company.id,
                    source="refresh",
                    url=company.website,
                    payload={
                        "web": company.web.model_dump(mode="json"),
                        "contacts": [c.model_dump(mode="json") for c in company.contacts],
                        "website_source": company.website_source,
                        "website_evidence": company.website_evidence,
                    },
                )
            )

            await session.execute(
                pg_insert(models.Score)
                .values(
                    company_id=company.id,
                    cache_key=score_cache_key(company, result.weights, BuyBox()),
                    score=result.score,
                    confidence=result.confidence.value,
                    factors=[f.model_dump(mode="json") for f in result.factors],
                    weights=result.weights.model_dump(mode="json"),
                    buy_box=BuyBox().model_dump(mode="json"),
                    engine_version=result.engine_version,
                    scored_at=result.scored_at,
                )
                # The key is a hash of everything that can change the answer, so
                # a conflict means this exact result is already on record.
                .on_conflict_do_nothing(index_elements=[models.Score.cache_key])
            )
        return True

    async def history(self, company_id: str) -> list[ScoreRecord]:
        s = models.Score
        async with self._sessionmaker() as session:
            rows = (
                await session.scalars(
                    select(s).where(s.company_id == company_id).order_by(s.scored_at.desc())
                )
            ).all()
            return [
                ScoreRecord(
                    score=r.score,
                    confidence=r.confidence,
                    engine_version=r.engine_version,
                    scored_at=r.scored_at,
                )
                for r in rows
            ]

    async def ping(self) -> None:
        async with self._sessionmaker() as session:
            await session.execute(select(1))


# Rows per statement. Postgres caps a statement at 32,767 bind parameters and a
# company row uses a few dozen, so this stays well inside the limit.
_BATCH = 400


async def upsert_companies(
    session: AsyncSession, companies: list[Company], *, market_key: str | None
) -> None:
    """Insert or update companies and replace their contacts, in bulk.

    One statement per batch rather than per row. Against a local database the
    difference is unnoticeable; against a hosted one every statement is a
    network round trip, and loading a market row by row turns a two-second
    bootstrap into a minute of container start-up.
    """
    for start in range(0, len(companies), _BATCH):
        batch = companies[start : start + _BATCH]
        rows = [company_values(c, market_key=market_key) for c in batch]
        insert = pg_insert(models.Company).values(rows)
        update: dict[str, Any] = {
            column: insert.excluded[column] for column in rows[0] if column != "id"
        }
        update["updated_at"] = func.now()
        await session.execute(
            insert.on_conflict_do_update(index_elements=[models.Company.id], set_=update)
        )

        ids = [c.id for c in batch]
        await session.execute(delete(models.Contact).where(models.Contact.company_id.in_(ids)))
        contacts = [contact_values(c.id, contact) for c in batch for contact in c.contacts]
        if contacts:
            await session.execute(pg_insert(models.Contact).values(contacts))


async def load_snapshot(
    session: AsyncSession, payload: dict[str, Any], companies: list[Company]
) -> int:
    """Load a collected snapshot into Postgres. Idempotent: safe to re-run.

    Used to bootstrap an empty database from the committed file, and by
    `scripts/load_seed.py` to push a freshly collected market.
    """
    market = payload.get("market") or {}
    key = str(market.get("key") or "default")
    generated = payload.get("generated_at")
    values = {
        "key": key,
        "label": str(market.get("label") or key),
        "state": market.get("state"),
        "generated_at": datetime.fromisoformat(generated) if generated else None,
        "sources": list(payload.get("sources", [])),
    }
    await session.execute(
        pg_insert(models.Market)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[models.Market.key],
            set_={**{k: v for k, v in values.items() if k != "key"}, "updated_at": func.now()},
        )
    )
    await upsert_companies(session, companies, market_key=key)
    return len(companies)


# --------------------------------------------------------------------------- #
# resilience
# --------------------------------------------------------------------------- #


class ResilientStore:
    """Postgres first; the snapshot if Postgres cannot answer.

    Only reads fall back. `last_error` is what `/api/health` reports, so a
    degraded deployment is visible to whoever is watching rather than only to
    whoever is reading logs.

    After a failure the database is left alone for `cooldown_seconds`. Without
    that, an outage would make every request wait out a connection attempt
    before being answered from the snapshot — the fallback would work and the
    product would still feel down. With it, one request pays for discovering
    the outage and the rest are served immediately. A successful health ping
    ends the cooldown early, so recovery does not wait for the timer.
    """

    def __init__(
        self, primary: PostgresStore, standby: SnapshotStore, *, cooldown_seconds: float = 20.0
    ) -> None:
        self._primary = primary
        self._standby = standby
        self._cooldown = cooldown_seconds
        self._skip_until = 0.0
        self.name = primary.name
        self.last_error: str | None = None

    def _failed(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"[:300]
        self._skip_until = time.monotonic() + self._cooldown

    @property
    def cooling_down(self) -> bool:
        return time.monotonic() < self._skip_until

    async def _read(
        self,
        what: str,
        primary: Callable[[], Awaitable[T]],
        standby: Callable[[], Awaitable[T]],
    ) -> T:
        if self.cooling_down:
            return await standby()
        try:
            result = await primary()
        except Exception as exc:
            self._failed(exc)
            logger.error("postgres %s failed, serving the snapshot instead: %s", what, exc)
            return await standby()
        self.last_error = None
        return result

    async def search(self, filters: CompanyFilters) -> list[Company]:
        return await self._read(
            "search", lambda: self._primary.search(filters), lambda: self._standby.search(filters)
        )

    async def get(self, company_id: str) -> Company | None:
        return await self._read(
            "get", lambda: self._primary.get(company_id), lambda: self._standby.get(company_id)
        )

    async def get_many(self, ids: set[str] | None) -> list[Company]:
        return await self._read(
            "get_many", lambda: self._primary.get_many(ids), lambda: self._standby.get_many(ids)
        )

    async def meta(self) -> DatasetMeta:
        return await self._read("meta", self._primary.meta, self._standby.meta)

    async def count(self) -> int:
        return await self._read("count", self._primary.count, self._standby.count)

    async def history(self, company_id: str) -> list[ScoreRecord]:
        return await self._read(
            "history",
            lambda: self._primary.history(company_id),
            lambda: self._standby.history(company_id),
        )

    async def save_refresh(self, scored: ScoredCompany) -> bool:
        if self.cooling_down:
            return False
        try:
            return await self._primary.save_refresh(scored)
        except Exception as exc:
            self._failed(exc)
            logger.error("could not persist refresh of %s: %s", scored.company.id, exc)
            return False

    async def ping(self) -> bool:
        """Check the database directly, cooldown or not.

        This is the one call that always tries, which is what makes it the
        recovery path: the first healthy ping reopens the database to traffic.
        """
        try:
            await self._primary.ping()
        except Exception as exc:
            self._failed(exc)
            return False
        self.last_error = None
        self._skip_until = 0.0
        return True


# --------------------------------------------------------------------------- #
# process-wide store
# --------------------------------------------------------------------------- #


@dataclass
class StorageStatus:
    """What `/api/health` says about storage."""

    storage: str  # which store answers reads: "postgres" or "snapshot"
    database: str  # "ok" | "not configured" | "unreachable"
    detail: str | None = None


_store: CompanyStore | None = None
_startup_failure: str | None = None


def get_store() -> CompanyStore:
    """The store for this process. The snapshot until `init_store` says otherwise,
    so tests and scripts that never run the app's lifespan still work."""
    global _store
    if _store is None:
        _store = SnapshotStore()
    return _store


async def init_store() -> CompanyStore:
    """Choose the store at startup, bootstrapping an empty database.

    Never raises: a database that is configured but unusable costs the
    deployment its persistence, not its availability.
    """
    global _store, _startup_failure
    _startup_failure = None
    snapshot = SnapshotStore()

    if not database_configured():
        logger.info("no DATABASE_URL: serving the committed snapshot")
        _store = snapshot
        return _store

    try:
        where = connection_spec().safe_description
        primary = PostgresStore(get_sessionmaker())
        existing = await primary.count()
        if existing == 0:
            payload, companies = load_dataset()
            async with get_sessionmaker()() as session, session.begin():
                loaded = await load_snapshot(session, payload, companies)
            logger.info("bootstrapped empty database at %s with %d companies", where, loaded)
        else:
            logger.info("serving %d companies from postgres at %s", existing, where)
        _store = ResilientStore(primary, snapshot)
    except Exception as exc:
        _startup_failure = f"{type(exc).__name__}: {exc}"[:300]
        logger.error(
            "DATABASE_URL is set but the database is unusable (%s); serving the snapshot. "
            "If the schema is missing, run `alembic upgrade head`.",
            _startup_failure,
        )
        _store = snapshot
    return _store


async def storage_status() -> StorageStatus:
    store = get_store()
    if isinstance(store, ResilientStore):
        healthy = await store.ping()
        return StorageStatus(
            storage="postgres" if healthy else "snapshot",
            database="ok" if healthy else "unreachable",
            detail=None if healthy else store.last_error,
        )
    if database_configured():
        return StorageStatus(storage="snapshot", database="unreachable", detail=_startup_failure)
    return StorageStatus(storage="snapshot", database="not configured")


def reset_store() -> None:
    """Forget the chosen store. For tests."""
    global _store, _startup_failure
    _store = None
    _startup_failure = None


__all__ = [
    "CompanyFilters",
    "CompanyStore",
    "DatasetMeta",
    "PostgresStore",
    "ResilientStore",
    "ScoreRecord",
    "SnapshotStore",
    "StorageStatus",
    "get_store",
    "init_store",
    "load_dataset",
    "load_snapshot",
    "reset_store",
    "storage_status",
    "upsert_companies",
]
