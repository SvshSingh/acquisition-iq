"""Domain types.

The shapes here are the contract between the scraping pipeline, the scoring
engine, the API and the frontend. Everything the UI shows about *why* a company
scored the way it did travels in `Evidence` objects, so the reasoning is
structured data rather than a rendered string.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class FactorKey(StrEnum):
    SUCCESSION = "succession"
    BUY_BOX = "buy_box"
    DIGITAL_GAP = "digital_gap"
    FRAGMENTATION = "fragmentation"
    CONTACTABILITY = "contactability"
    HEALTH = "health"


FACTOR_LABELS: dict[FactorKey, str] = {
    FactorKey.SUCCESSION: "Succession signal",
    FactorKey.BUY_BOX: "Buy-box fit",
    FactorKey.DIGITAL_GAP: "Digital maturity gap",
    FactorKey.FRAGMENTATION: "Niche fragmentation",
    FactorKey.CONTACTABILITY: "Contactability",
    FactorKey.HEALTH: "Business health",
}

FACTOR_DESCRIPTIONS: dict[FactorKey, str] = {
    FactorKey.SUCCESSION: (
        "How likely the owner is approaching an exit — founder-led, long-tenured, "
        "independently held businesses score highest."
    ),
    FactorKey.BUY_BOX: (
        "How closely headcount and estimated revenue sit inside a typical "
        "search-fund acquisition range."
    ),
    FactorKey.DIGITAL_GAP: (
        "How much headroom there is for post-acquisition value creation through "
        "basic digital modernisation."
    ),
    FactorKey.FRAGMENTATION: (
        "How fragmented the company's niche is in its geography — fragmented "
        "niches support roll-up theses."
    ),
    FactorKey.CONTACTABILITY: (
        "Whether a decision maker can actually be reached, with verified channels."
    ),
    FactorKey.HEALTH: (
        "Whether the business shows signs of being alive and trading — recent "
        "site activity, hiring, reviews."
    ),
}


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    RISKY = "risky"  # catch-all domain, role address
    INVALID = "invalid"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    """One observed fact that moved a factor's subscore.

    `detail` is quoted or paraphrased from the source so a user can audit the
    claim; `source_url` is where we saw it. Both are shown in the UI drawer.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    detail: str
    source_url: str | None = None
    impact: float = Field(
        0.0,
        ge=-100.0,
        le=100.0,
        description="Signed contribution this observation made to the factor subscore.",
    )


class FactorResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: FactorKey
    label: str
    score: float = Field(ge=0.0, le=100.0)
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)
    missing_signals: list[str] = Field(
        default_factory=list,
        description="Signals we looked for and did not find. Drives the confidence flag.",
    )
    measured: bool = Field(
        default=True,
        description=(
            "Whether this score is a measurement or a fallback prior. The "
            "distinction decides whether the factor earns its weight: an absence "
            "is sometimes the finding and sometimes just ignorance. Finding no "
            "way to contact a company is a real result — that lead is "
            "unreachable, and 0 is the right score. Finding no headcount "
            "published anywhere says nothing about the company at all, and any "
            "number we put there is invented. Only the first kind should move a "
            "ranking."
        ),
    )

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence is Confidence.LOW


class FactorWeights(BaseModel):
    """User-adjustable weights. Normalised at use time, so callers may pass any
    non-negative numbers and the engine still returns a 0-100 score."""

    model_config = ConfigDict(frozen=True)

    succession: float = Field(default=0.28, ge=0.0, le=1.0)
    buy_box: float = Field(default=0.24, ge=0.0, le=1.0)
    digital_gap: float = Field(default=0.16, ge=0.0, le=1.0)
    fragmentation: float = Field(default=0.12, ge=0.0, le=1.0)
    contactability: float = Field(default=0.12, ge=0.0, le=1.0)
    health: float = Field(default=0.08, ge=0.0, le=1.0)

    def as_map(self) -> dict[FactorKey, float]:
        raw = {
            FactorKey.SUCCESSION: self.succession,
            FactorKey.BUY_BOX: self.buy_box,
            FactorKey.DIGITAL_GAP: self.digital_gap,
            FactorKey.FRAGMENTATION: self.fragmentation,
            FactorKey.CONTACTABILITY: self.contactability,
            FactorKey.HEALTH: self.health,
        }
        total = sum(raw.values())
        if total <= 0:
            # Degenerate input — fall back to equal weighting rather than
            # dividing by zero or silently returning 0 for everything.
            equal = 1.0 / len(raw)
            return dict.fromkeys(raw, equal)
        return {k: v / total for k, v in raw.items()}


class BuyBox(BaseModel):
    """The acquisition thesis the score is measured against."""

    model_config = ConfigDict(frozen=True)

    min_employees: int = 10
    max_employees: int = 100
    min_revenue_usd: float = 1_000_000
    max_revenue_usd: float = 10_000_000
    # Businesses far outside the band are not merely worse, they are out of scope.
    tolerance: float = Field(
        default=0.5,
        ge=0.0,
        description="Fractional overshoot still worth partial credit (0.5 = 50% outside band).",
    )


class Contact(BaseModel):
    name: str | None = None
    title: str | None = None
    email: str | None = None
    email_status: VerificationStatus = VerificationStatus.UNKNOWN
    phone: str | None = None
    phone_valid: bool | None = None
    linkedin_url: str | None = None
    is_decision_maker: bool = False


class WebSignals(BaseModel):
    """What the site itself told us. Populated by the website crawler."""

    fetched_at: datetime | None = None
    https: bool | None = None
    mobile_viewport: bool | None = None
    has_analytics: bool | None = None
    generator: str | None = None  # CMS / site builder from <meta name="generator">
    copyright_year: int | None = None
    latest_content_year: int | None = None
    page_bytes: int | None = None
    tech_hints: list[str] = Field(default_factory=list)
    has_careers_page: bool = False
    has_team_page: bool = False
    founded_year: int | None = None
    owner_mentions: list[str] = Field(default_factory=list)
    pe_backed_mentions: list[str] = Field(default_factory=list)
    raw_text_excerpt: str | None = None


class Company(BaseModel):
    """A candidate acquisition target, at whatever level of completeness we have."""

    id: str
    name: str
    domain: str | None = None
    website: str | None = None
    industry: str | None = None
    naics: str | None = None
    city: str | None = None
    state: str | None = None
    country: str = "US"
    postcode: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    employee_count: int | None = None
    employee_count_is_estimate: bool = True
    revenue_usd: float | None = None
    revenue_is_estimate: bool = True
    founded_year: int | None = None

    contacts: list[Contact] = Field(default_factory=list)
    web: WebSignals = Field(default_factory=WebSignals)

    # Populated by the fragmentation factor's neighbourhood query.
    peer_count_in_niche: int | None = None

    source: str = "unknown"
    source_url: str | None = None
    first_seen: date | None = None
    last_refreshed: datetime | None = None

    data_quality: float | None = Field(default=None, ge=0.0, le=100.0)
    quality_issues: list[str] = Field(default_factory=list)

    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("domain")
    @classmethod
    def _lower_domain(cls, v: str | None) -> str | None:
        return v.lower().strip() if v else v

    @property
    def primary_contact(self) -> Contact | None:
        if not self.contacts:
            return None
        for c in self.contacts:
            if c.is_decision_maker:
                return c
        return self.contacts[0]


class ScoreResult(BaseModel):
    """The output the whole product exists to produce.

    Two sets of weights travel together, and the distinction matters:

    * `weights` is what the user asked for — their thesis, unaltered.
    * `effective_weights` is what was actually applied. A factor that found no
      evidence at all contributes nothing and its weight is redistributed across
      the factors that did, so the headline number answers "how good is this
      target, judged on what we could actually observe" rather than being
      dragged toward a constant by a signal no source publishes.

    `covered_weight` is the share of the user's declared thesis that had evidence
    behind it. It is the honesty gauge: a 78 scored on 40% of the thesis is a
    different claim from a 78 scored on 95% of it, and the UI shows both.
    Confidence is deliberately computed against the *declared* weights, so
    missing coverage still drags it down — otherwise redistributing would make a
    thinly-evidenced company look more certain, which is backwards.
    """

    company_id: str
    score: float = Field(ge=0.0, le=100.0)
    confidence: Confidence
    factors: list[FactorResult]
    weights: FactorWeights
    effective_weights: dict[FactorKey, float] = Field(default_factory=dict)
    covered_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    scored_at: datetime
    engine_version: str

    @property
    def factor_map(self) -> dict[FactorKey, FactorResult]:
        return {f.key: f for f in self.factors}

    @property
    def uncovered_factors(self) -> list[FactorKey]:
        """Factors that found no evidence and were therefore not scored on."""
        return [f.key for f in self.factors if not f.evidence]

    def contribution(self, key: FactorKey) -> float:
        """Points this factor contributed to the headline score."""
        f = self.factor_map.get(key)
        if f is None:
            return 0.0
        applied = self.effective_weights or self.weights.as_map()
        return f.score * applied.get(key, 0.0)


class ScoredCompany(BaseModel):
    company: Company
    score: ScoreResult


class SearchRequest(BaseModel):
    industry: str | None = None
    city: str | None = None
    state: str | None = None
    min_score: float = Field(default=0.0, ge=0.0, le=100.0)
    min_employees: int | None = None
    max_employees: int | None = None
    weights: FactorWeights = Field(default_factory=FactorWeights)
    buy_box: BuyBox = Field(default_factory=BuyBox)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    live: bool = Field(
        default=False,
        description="Bypass the seed snapshot and scrape the source live.",
    )


class SearchResponse(BaseModel):
    results: list[ScoredCompany]
    total: int
    took_ms: int
    from_cache: bool
    source: str


__all__ = [
    "FACTOR_DESCRIPTIONS",
    "FACTOR_LABELS",
    "BuyBox",
    "Company",
    "Confidence",
    "Contact",
    "Evidence",
    "FactorKey",
    "FactorResult",
    "FactorWeights",
    "HttpUrl",
    "ScoreResult",
    "ScoredCompany",
    "SearchRequest",
    "SearchResponse",
    "VerificationStatus",
    "WebSignals",
]
