"""The storage layer.

Three groups, in increasing order of what they need:

1. **Pure** — URL normalisation and the row mapping. No database, always run.
2. **Fallback** — what happens when a configured database is not there. These
   point at a closed port on purpose, so they need no database either, and they
   pin the behaviour the whole design leans on: a missing database costs
   persistence, never availability.
3. **Postgres** — the real thing, against `TEST_DATABASE_URL`. Skipped when it
   is not set so the suite still runs on a bare checkout; CI sets it against a
   Postgres service container, so they are not skipped where it counts.

The Postgres group's central claim is *parity*: for the same filters, the SQL
path must return exactly the companies the in-memory path does. The snapshot
store is the specification; Postgres has to agree with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.store as store_module
from app.config import settings
from app.db import models
from app.db.mapping import company_values, contact_values, to_company
from app.db.session import (
    DatabaseNotConfiguredError,
    connection_spec,
    get_engine,
    get_sessionmaker,
)
from app.schemas import Company
from app.scoring.engine import score_many
from app.store import (
    CompanyFilters,
    PostgresStore,
    ResilientStore,
    SnapshotStore,
    init_store,
    load_dataset,
    load_snapshot,
    reset_store,
    storage_status,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
BACKEND = Path(__file__).resolve().parents[1]

# Nothing listens here. Port 9 is "discard"; on a developer machine and in CI it
# is closed, so a connection attempt is refused immediately.
DEAD_DATABASE_URL = "postgresql://nobody:nothing@127.0.0.1:9/nowhere"


def _dump(company: Company) -> dict[str, object]:
    return company.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# 1. pure: connection normalisation
# --------------------------------------------------------------------------- #


def test_no_url_is_a_distinct_state_not_a_connection_error():
    with pytest.raises(DatabaseNotConfiguredError):
        connection_spec("")
    with pytest.raises(DatabaseNotConfiguredError):
        connection_spec("   ")


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@db.example.com:5432/app",
        "postgresql://u:p@db.example.com:5432/app",
        "postgresql+asyncpg://u:p@db.example.com:5432/app",
        "postgresql+psycopg://u:p@db.example.com:5432/app",
    ],
)
def test_any_postgres_url_form_becomes_the_async_driver(raw: str):
    """Dashboards hand out `postgres://`; the async engine needs the driver
    spelled out. Nobody should have to learn that from a stack trace."""
    spec = connection_spec(raw)
    assert spec.url.drivername == "postgresql+asyncpg"
    assert spec.url.host == "db.example.com"
    assert spec.url.database == "app"


def test_a_non_postgres_url_is_rejected_by_name():
    with pytest.raises(ValueError, match="Postgres"):
        connection_spec("mysql://u:p@host/db")


def test_libpq_sslmode_is_translated_not_passed_through():
    """asyncpg raises on an unknown `sslmode` keyword, and every hosted
    provider appends one."""
    spec = connection_spec("postgresql://u:p@db.example.com/app?sslmode=require")
    assert "sslmode" not in spec.url.query
    assert spec.connect_args["ssl"] == "require"


def test_sslmode_disable_means_no_tls_argument():
    spec = connection_spec("postgresql://u:p@localhost/app?sslmode=disable")
    assert "ssl" not in spec.connect_args


def test_supabase_gets_tls_even_when_the_url_does_not_ask():
    spec = connection_spec(
        "postgresql://postgres.abcd:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    assert spec.connect_args["ssl"] == "require"
    assert spec.uses_transaction_pooler is False
    assert "statement_cache_size" not in spec.connect_args


def test_supabase_transaction_pooler_disables_prepared_statement_caches():
    """Port 6543 is PgBouncer in transaction mode, where a prepared statement
    can be replayed on a server connection that never prepared it."""
    spec = connection_spec(
        "postgresql://postgres.abcd:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres?pgbouncer=true"
    )
    assert spec.uses_transaction_pooler is True
    assert spec.connect_args["statement_cache_size"] == 0
    assert spec.url.query["prepared_statement_cache_size"] == "0"
    assert "pgbouncer" not in spec.url.query
    # Names must be unique per statement, or two clients collide on the pooler.
    name = spec.connect_args["prepared_statement_name_func"]
    assert name() != name()


def test_connections_fail_fast_rather_than_holding_boot_for_a_minute():
    spec = connection_spec("postgresql://u:p@db.example.com/app")
    assert spec.connect_args["timeout"] <= 15


def test_the_log_description_never_contains_the_password():
    spec = connection_spec("postgresql://user:hunter2@db.example.com:5432/app")
    assert "hunter2" not in spec.safe_description
    assert "db.example.com" in spec.safe_description


# --------------------------------------------------------------------------- #
# 1. pure: row mapping
# --------------------------------------------------------------------------- #


def _as_row(company: Company) -> models.Company:
    row = models.Company(**company_values(company, market_key="glendale"))
    row.contacts = [models.Contact(**contact_values(company.id, c)) for c in company.contacts]
    return row


def test_every_snapshot_company_survives_the_round_trip_to_a_row_and_back():
    """The schema once lacked the columns for facts the collector had learned to
    gather, and nothing noticed because nothing read from it. This is the test
    that would have: a field present on only one side changes the output."""
    _, companies = load_dataset()
    assert len(companies) > 100
    for company in companies:
        assert _dump(to_company(_as_row(company))) == _dump(company), company.id


def test_the_round_trip_does_not_change_a_single_score():
    """Field equality is the mechanism; this is the consequence that matters."""
    _, companies = load_dataset()
    now = datetime(2026, 9, 1, tzinfo=UTC)
    before = {s.company.id: s.score.score for s in score_many(companies, now=now)}
    restored = [to_company(_as_row(c)) for c in companies]
    after = {s.company.id: s.score.score for s in score_many(restored, now=now)}
    assert after == before


# --------------------------------------------------------------------------- #
# snapshot store: query semantics both stores must share
# --------------------------------------------------------------------------- #


async def test_every_word_of_a_query_must_match_but_in_any_field():
    """'plumbing burbank' is a trade and a city. Matched as one substring it
    finds nothing; matched word by word it finds plumbers in Burbank."""
    store = SnapshotStore()
    hits = await store.search(CompanyFilters(q="plumbing burbank"))
    assert hits, "expected plumbers in Burbank in the seed market"
    for c in hits:
        blob = f"{c.name} {c.city} {c.industry}".lower()
        assert "plumbing" in blob and "burbank" in blob
    assert await store.search(CompanyFilters(q="plumbing zzzznotacity")) == []


async def test_the_snapshot_is_never_written_to():
    store = SnapshotStore()
    _, companies = load_dataset()
    scored = score_many(companies[:1])[0]
    assert await store.save_refresh(scored) is False
    assert await store.history(scored.company.id) == []


# --------------------------------------------------------------------------- #
# 2. fallback: a configured database that is not there
# --------------------------------------------------------------------------- #


@pytest.fixture
def dead_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configure a database nothing is listening on."""
    monkeypatch.setattr(settings, "database_url", DEAD_DATABASE_URL)
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
    reset_store()
    yield
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
    reset_store()


async def test_startup_with_an_unreachable_database_serves_the_snapshot(dead_database: None):
    """The failure this design exists to prevent: a database outage taking the
    whole product down with it."""
    store = await init_store()
    assert store.name == "snapshot"
    assert await store.count() > 0

    status = await storage_status()
    assert status.storage == "snapshot"
    assert status.database == "unreachable"
    assert status.detail  # says why, for whoever is looking at /api/health


async def test_a_read_that_fails_mid_flight_is_answered_from_the_snapshot(dead_database: None):
    """Startup succeeding is not the same as the database staying up."""
    resilient = ResilientStore(PostgresStore(get_sessionmaker()), SnapshotStore())
    expected = await SnapshotStore().search(CompanyFilters(industry="Plumbing"))

    got = await resilient.search(CompanyFilters(industry="Plumbing"))

    assert [c.id for c in got] == [c.id for c in expected]
    assert resilient.last_error is not None
    assert await resilient.ping() is False
    await get_engine().dispose()


async def test_an_outage_is_paid_for_once_not_on_every_request(dead_database: None):
    """Falling back correctly is not enough if every request first waits out a
    connection attempt. After one failure the database is left alone for the
    cooldown, and reads go straight to the snapshot."""
    calls = 0
    primary = PostgresStore(get_sessionmaker())
    real_search = primary.search

    async def counting_search(filters: CompanyFilters) -> list[Company]:
        nonlocal calls
        calls += 1
        return await real_search(filters)

    primary.search = counting_search  # type: ignore[method-assign]
    resilient = ResilientStore(primary, SnapshotStore(), cooldown_seconds=60)

    for _ in range(5):
        assert await resilient.search(CompanyFilters())

    assert calls == 1
    assert resilient.cooling_down
    await get_engine().dispose()


async def test_with_no_cooldown_every_read_retries_the_database(dead_database: None):
    """The control for the test above: the cooldown is what does the work."""
    calls = 0
    primary = PostgresStore(get_sessionmaker())
    real_count = primary.count

    async def counting_count() -> int:
        nonlocal calls
        calls += 1
        return await real_count()

    primary.count = counting_count  # type: ignore[method-assign]
    resilient = ResilientStore(primary, SnapshotStore(), cooldown_seconds=0)
    for _ in range(2):
        assert await resilient.count() > 0
    assert calls == 2
    await get_engine().dispose()


async def test_a_write_that_fails_reports_it_instead_of_pretending(dead_database: None):
    resilient = ResilientStore(PostgresStore(get_sessionmaker()), SnapshotStore())
    _, companies = load_dataset()
    assert await resilient.save_refresh(score_many(companies[:1])[0]) is False
    await get_engine().dispose()


async def test_no_database_configured_is_reported_as_such(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "database_url", "")
    reset_store()
    store = await init_store()
    assert store.name == "snapshot"
    status = await storage_status()
    assert (status.storage, status.database) == ("snapshot", "not configured")
    reset_store()


# --------------------------------------------------------------------------- #
# 3. postgres
# --------------------------------------------------------------------------- #

needs_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run the Postgres tests"
)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Bring the test database to head with the real migrations, once.

    Through Alembic rather than `create_all`, so these tests exercise the schema
    a deployment actually gets — including the extension and the RLS statements
    that `create_all` knows nothing about.
    """
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    for command in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=BACKEND,
            env=env,
            check=True,
            capture_output=True,
        )
    return TEST_DATABASE_URL


@pytest.fixture
async def sessionmaker(migrated_database: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    spec = connection_spec(migrated_database)
    # NullPool: each test runs on its own event loop, and a pooled asyncpg
    # connection cannot be carried from one loop to the next.
    engine = create_async_engine(spec.url, connect_args=spec.connect_args, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as session, session.begin():
        await session.execute(
            text("TRUNCATE markets, companies, contacts, scores, raw_payloads, http_cache CASCADE")
        )
    yield maker
    await engine.dispose()


@pytest.fixture
async def loaded(sessionmaker: async_sessionmaker[AsyncSession]) -> PostgresStore:
    payload, companies = load_dataset()
    async with sessionmaker() as session, session.begin():
        await load_snapshot(session, payload, companies)
    return PostgresStore(sessionmaker)


@needs_postgres
async def test_loading_the_snapshot_is_idempotent(
    sessionmaker: async_sessionmaker[AsyncSession],
):
    payload, companies = load_dataset()
    for _ in range(2):
        async with sessionmaker() as session, session.begin():
            assert await load_snapshot(session, payload, companies) == len(companies)

    async with sessionmaker() as session:
        company_count = await session.scalar(select(func.count()).select_from(models.Company))
        contact_count = await session.scalar(select(func.count()).select_from(models.Contact))
        market_count = await session.scalar(select(func.count()).select_from(models.Market))
    assert company_count == len(companies)
    # Re-running must replace contacts, not accumulate a second copy of each.
    assert contact_count == sum(len(c.contacts) for c in companies)
    assert market_count == 1


@needs_postgres
async def test_postgres_returns_every_company_exactly_as_the_snapshot_holds_it(
    loaded: PostgresStore,
):
    _, companies = load_dataset()
    stored = {c.id: c for c in await loaded.get_many(None)}
    assert set(stored) == {c.id for c in companies}
    for company in companies:
        assert _dump(stored[company.id]) == _dump(company), company.id


PARITY_FILTERS = [
    CompanyFilters(),
    CompanyFilters(q="plumbing"),
    CompanyFilters(q="PLUMBING"),
    CompanyFilters(q="plumbing burbank"),
    CompanyFilters(q="electric glendale"),
    CompanyFilters(q="zzzz-no-such-company"),
    CompanyFilters(q="100%"),  # LIKE metacharacters must be matched literally
    CompanyFilters(q="a_b"),
    CompanyFilters(industry="Plumbing"),
    CompanyFilters(industry="plumbing"),
    CompanyFilters(city="GLENDALE"),
    CompanyFilters(business_type="Sole Owner"),
    CompanyFilters(has_employees=True),
    CompanyFilters(has_employees=False),
    CompanyFilters(min_age=0),
    CompanyFilters(min_age=20),
    CompanyFilters(min_age=60),
    CompanyFilters(industry="HVAC", has_employees=True, min_age=10),
    CompanyFilters(q="air", city="Burbank"),
]


@needs_postgres
@pytest.mark.parametrize("filters", PARITY_FILTERS, ids=lambda f: repr(f)[14:-1] or "all")
async def test_sql_filtering_agrees_with_the_in_memory_filter(
    loaded: PostgresStore, filters: CompanyFilters
):
    """The snapshot store is the specification. Whatever it returns for a set
    of filters, the SQL path must return the same companies."""
    expected = {c.id for c in await SnapshotStore().search(filters)}
    got = {c.id for c in await loaded.search(filters)}
    assert got == expected


@needs_postgres
async def test_search_order_is_stable(loaded: PostgresStore):
    first = [c.id for c in await loaded.search(CompanyFilters())]
    second = [c.id for c in await loaded.search(CompanyFilters())]
    assert first == second == sorted(first)


@needs_postgres
async def test_dataset_metadata_agrees_with_the_snapshot(loaded: PostgresStore):
    want = await SnapshotStore().meta()
    got = await loaded.meta()
    assert got.market == want.market
    assert got.count == want.count
    assert got.sources == want.sources
    assert got.industries == want.industries
    assert got.cities == want.cities
    assert got.business_types == want.business_types
    assert got.generated_at is not None and want.generated_at is not None
    assert datetime.fromisoformat(got.generated_at) == datetime.fromisoformat(want.generated_at)


@needs_postgres
async def test_a_refresh_is_kept_with_its_evidence_and_its_score(
    loaded: PostgresStore, sessionmaker: async_sessionmaker[AsyncSession]
):
    company = (await loaded.search(CompanyFilters()))[0]
    refreshed = company.model_copy(deep=True)
    refreshed.website = "https://example.com"
    refreshed.website_source = "inferred:phone"
    refreshed.website_evidence = "phone number on the page matches the licence record"
    refreshed.last_refreshed = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    scored = score_many([refreshed])[0]

    assert await loaded.save_refresh(scored) is True

    # The company row now holds what the refresh found...
    stored = await loaded.get(company.id)
    assert stored is not None
    assert stored.website == "https://example.com"
    assert stored.website_evidence == refreshed.website_evidence
    assert stored.last_refreshed == refreshed.last_refreshed

    # ...the raw output is on record beside it...
    async with sessionmaker() as session:
        payloads = (
            await session.scalars(
                select(models.RawPayload).where(models.RawPayload.company_id == company.id)
            )
        ).all()
        market_key = await session.scalar(
            select(models.Company.market_key).where(models.Company.id == company.id)
        )
    assert len(payloads) == 1
    assert payloads[0].source == "refresh"
    assert payloads[0].payload["website_source"] == "inferred:phone"
    # ...and a refresh must not detach the company from its market.
    assert market_key == "glendale"

    # ...and the score has joined the company's history.
    history = await loaded.history(company.id)
    assert len(history) == 1
    assert history[0].score == scored.score.score
    assert history[0].engine_version == scored.score.engine_version


@needs_postgres
async def test_saving_an_identical_result_twice_records_it_once(loaded: PostgresStore):
    """The history is a record of changes. Refreshing a company whose facts did
    not move should not manufacture an entry."""
    company = (await loaded.search(CompanyFilters()))[0]
    scored = score_many([company])[0]
    await loaded.save_refresh(scored)
    await loaded.save_refresh(scored)
    assert len(await loaded.history(company.id)) == 1


@needs_postgres
async def test_an_empty_database_is_bootstrapped_at_startup(
    sessionmaker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
):
    """Provisioning should be one step: point the app at an empty, migrated
    database and it fills itself from the committed snapshot."""
    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)
    monkeypatch.setattr(store_module, "get_sessionmaker", lambda: sessionmaker)
    reset_store()

    store = await init_store()

    assert store.name == "postgres"
    _, companies = load_dataset()
    assert await store.count() == len(companies)
    status = await storage_status()
    assert (status.storage, status.database) == ("postgres", "ok")
    reset_store()


@needs_postgres
async def test_every_table_has_row_level_security_enabled(
    sessionmaker: async_sessionmaker[AsyncSession],
):
    """On Supabase the `public` schema is published through a REST API that
    anyone with the project's anon key can call. A table without RLS there is
    world-readable and world-writable. This pins that none is left open."""
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                )
            )
        ).all()
    assert rows, "no tables found"
    unprotected = sorted(name for name, enabled in rows if not enabled)
    assert unprotected == []
