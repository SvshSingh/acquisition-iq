"""SQLAlchemy 2.0 models.

Two shapes live side by side on purpose. Normalised columns carry everything we
filter, sort or join on; a `JSONB` column beside them carries the raw payload
exactly as the source returned it. We never discard provenance — when a score is
challenged, the answer has to be reconstructible from what we actually saw, not
from what our parser made of it at the time.

`Company.web` is stored as JSONB rather than twenty nullable columns because
`WebSignals` is a crawler output that will keep growing, and every new signal
would otherwise be a migration.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base. `type_annotation_map` is deliberately empty — every
    column states its type explicitly, so the mapping is readable without
    knowing the defaults."""


class Market(Base):
    """A collected market — one run of the collector over one geography.

    The snapshot file carried this as top-level JSON (`market`, `generated_at`,
    `sources`). It needs a home in the database for the same reason it needed
    one in the file: the UI names the market it is showing, and the attribution
    a source's licence requires travels with the data, not with the code.
    """

    __tablename__ = "markets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str | None] = mapped_column(String(64))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Source descriptors exactly as the collector emitted them: name, URL,
    # licence, attribution. Read whole, never queried into.
    sources: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    # Nullable: a company brought in by a user's own list belongs to no
    # collected market, and that is a fact about it rather than a gap.
    market_key: Mapped[str | None] = mapped_column(
        ForeignKey("markets.key", ondelete="SET NULL"), index=True
    )

    # Normalised to lowercase, no scheme, no www. The dedupe pipeline treats a
    # shared domain as strong evidence two rows are the same business.
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    # How the website was learned, and the proof. An inferred domain without its
    # evidence is a guess, so the two are stored or dropped together.
    website_source: Mapped[str | None] = mapped_column(String(64))
    website_evidence: Mapped[str | None] = mapped_column(Text)

    industry: Mapped[str | None] = mapped_column(String(128), index=True)
    naics: Mapped[str | None] = mapped_column(String(8), index=True)

    city: Mapped[str | None] = mapped_column(String(128), index=True)
    state: Mapped[str | None] = mapped_column(String(64), index=True)
    country: Mapped[str] = mapped_column(String(2), default="US", nullable=False)
    postcode: Mapped[str | None] = mapped_column(String(16))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    employee_count: Mapped[int | None] = mapped_column(Integer)
    employee_count_is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    revenue_usd: Mapped[float | None] = mapped_column(Float)
    revenue_is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, index=True)

    # Facts as filed with the licensing board. These are columns rather than
    # JSONB because the API filters on two of them and the scoring engine reads
    # all of them — they are the strongest evidence in the dataset.
    business_type: Mapped[str | None] = mapped_column(String(64), index=True)
    has_employees: Mapped[bool | None] = mapped_column(Boolean)
    licence_number: Mapped[str | None] = mapped_column(String(32), index=True)
    licence_issued: Mapped[date | None] = mapped_column(Date)
    licence_classifications: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )

    peer_count_in_niche: Mapped[int | None] = mapped_column(Integer)
    sibling_location_count: Mapped[int | None] = mapped_column(Integer)

    # WebSignals, serialised. See the module docstring for why this is not columns.
    web: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    source: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_refreshed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    data_quality: Mapped[float | None] = mapped_column(Float)
    quality_issues: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    contacts: Mapped[list[Contact]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    scores: Mapped[list[Score]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "data_quality IS NULL OR (data_quality >= 0 AND data_quality <= 100)",
            name="ck_companies_data_quality_range",
        ),
        # Trigram index on the name. The search box matches with an unanchored
        # `ILIKE '%term%'`, which a B-tree cannot serve at all and this can
        # (verified with EXPLAIN: a bitmap scan on this index). It is also what
        # `similarity()` needs if fuzzy dedupe is ever pushed into SQL.
        Index(
            "ix_companies_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_companies_industry_city", "industry", "city"),
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    email_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    phone_valid: Mapped[bool | None] = mapped_column(Boolean)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    is_decision_maker: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company] = relationship(back_populates="contacts")

    __table_args__ = (
        # One row per person per company. Re-running enrichment must update, not
        # accumulate near-duplicates.
        UniqueConstraint("company_id", "email", name="uq_contacts_company_email"),
    )


class Score(Base):
    """A scoring run's output, kept rather than recomputed.

    `cache_key` is the content hash from `scoring.engine.score_cache_key` — it
    covers the company fields, the weights, the buy box and the engine version,
    so an unchanged input never re-scores and an engine bump invalidates the
    whole table at once.
    """

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)

    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    buy_box: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    engine_version: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped[Company] = relationship(back_populates="scores")

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_scores_range"),
    )


class RawPayload(Base):
    """Exactly what a source returned, before we interpreted it.

    Cheap to store and the only way to answer "did the site really say that, or
    did our parser invent it?" once a page has changed underneath us.
    """

    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class HttpCacheEntry(Base):
    """Postgres-backed HTTP cache.

    This is the fallback half of the cache interface. Redis is faster and is what
    production would use, but the whole system has to work without it — a demo
    that dies because a free-tier Redis is cold is not a demo.
    """

    __tablename__ = "http_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


__all__ = [
    "Base",
    "Company",
    "Contact",
    "HttpCacheEntry",
    "Market",
    "RawPayload",
    "Score",
]
