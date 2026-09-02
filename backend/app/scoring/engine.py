"""Weighted composition of the six factors into one explainable score."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.schemas import (
    BuyBox,
    Company,
    Confidence,
    FactorKey,
    FactorResult,
    FactorWeights,
    ScoredCompany,
    ScoreResult,
)
from app.scoring import factors

ENGINE_VERSION = "1.3.0"

# Confidence is derived from the weighted share of factors that had good
# coverage, not from an average of enum values.
_CONFIDENCE_VALUE = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.55, Confidence.LOW: 0.15}

# Below this share of the declared thesis, redistribution is extrapolation
# rather than inference: stretching one or two observations to stand in for the
# whole buy box says more about our data than about the company. Under the
# threshold we keep the declared weights, the score stays near its priors, and
# confidence — computed against those same declared weights — reports LOW.
MIN_COVERAGE_FOR_REDISTRIBUTION = 0.25


def score_company(
    company: Company,
    *,
    weights: FactorWeights | None = None,
    buy_box: BuyBox | None = None,
    now: datetime | None = None,
) -> ScoreResult:
    """Score one company. Pure, deterministic, and safe to memoise."""
    w = weights or FactorWeights()
    at = now or datetime.now(UTC)

    results: list[FactorResult] = [
        factors.score_succession(company, today=at),
        factors.score_buy_box(company, buy_box),
        factors.score_digital_gap(company, today=at),
        factors.score_fragmentation(company),
        factors.score_contactability(company),
        factors.score_health(company, today=at),
    ]

    declared = w.as_map()
    effective, covered_weight = _effective_weights(results, declared)
    total = sum(r.score * effective[r.key] for r in results)

    # Confidence is measured against the *declared* weights on purpose. If it
    # used the effective ones, dropping an unevidenced factor would raise
    # confidence, which is exactly backwards: knowing less should never look
    # like knowing more.
    confidence = _overall_confidence(results, declared)

    return ScoreResult(
        company_id=company.id,
        score=round(max(0.0, min(100.0, total)), 1),
        confidence=confidence,
        factors=results,
        weights=w,
        effective_weights=effective,
        covered_weight=round(covered_weight, 4),
        scored_at=at,
        engine_version=ENGINE_VERSION,
    )


def _effective_weights(
    results: list[FactorResult], declared: dict[FactorKey, float]
) -> tuple[dict[FactorKey, float], float]:
    """Redistribute the weight of factors that found no evidence.

    A factor with an empty evidence list did not observe anything — it returned
    its prior. Letting that prior contribute is how 250 real companies ended up
    inside a four-point band: `buy_box` had a standard deviation of 0.00 across
    the whole seed dataset because no source publishes headcount for a local
    business, yet it carried 24% of the weight and pulled every score toward the
    same number.

    So the weight moves to the factors that did observe something, and
    `covered_weight` records how much of the user's thesis that was. Returns
    `(effective, covered_weight)`.

    Degenerate inputs fall back to the declared weights rather than dividing by
    zero: a company where nothing at all was observed, or a user who zeroed every
    weight that happens to have coverage.
    """
    covered = {r.key for r in results if r.measured}
    covered_weight = sum(v for k, v in declared.items() if k in covered)

    if not covered or covered_weight < MIN_COVERAGE_FOR_REDISTRIBUTION:
        return declared, covered_weight

    return (
        {k: (v / covered_weight if k in covered else 0.0) for k, v in declared.items()},
        covered_weight,
    )


def score_many(
    companies: list[Company],
    *,
    weights: FactorWeights | None = None,
    buy_box: BuyBox | None = None,
    now: datetime | None = None,
) -> list[ScoredCompany]:
    at = now or datetime.now(UTC)
    scored = [
        ScoredCompany(
            company=c, score=score_company(c, weights=weights, buy_box=buy_box, now=at)
        )
        for c in companies
    ]
    scored.sort(key=lambda s: s.score.score, reverse=True)
    return scored


def _overall_confidence(
    results: list[FactorResult], weight_map: dict[FactorKey, float]
) -> Confidence:
    weighted = sum(_CONFIDENCE_VALUE[r.confidence] * weight_map[r.key] for r in results)
    if weighted >= 0.7:
        return Confidence.HIGH
    if weighted >= 0.4:
        return Confidence.MEDIUM
    return Confidence.LOW


def score_cache_key(company: Company, weights: FactorWeights, buy_box: BuyBox) -> str:
    """Content-addressed cache key.

    Keyed on the *inputs that can change the answer* — company fields, weights,
    thesis, engine version — so an unchanged page never gets re-scored, and a
    change to the engine invalidates every entry at once.
    """
    payload = "|".join(
        [
            ENGINE_VERSION,
            company.model_dump_json(exclude={"raw", "last_refreshed", "first_seen"}),
            weights.model_dump_json(),
            buy_box.model_dump_json(),
        ]
    )
    return "score:" + hashlib.sha256(payload.encode()).hexdigest()[:32]


__all__ = ["ENGINE_VERSION", "score_cache_key", "score_company", "score_many"]
