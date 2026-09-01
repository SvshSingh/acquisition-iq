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

ENGINE_VERSION = "1.1.0"

# Confidence is derived from the weighted share of factors that had good
# coverage, not from an average of enum values.
_CONFIDENCE_VALUE = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.55, Confidence.LOW: 0.15}


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

    weight_map = w.as_map()
    total = sum(r.score * weight_map[r.key] for r in results)
    confidence = _overall_confidence(results, weight_map)

    return ScoreResult(
        company_id=company.id,
        score=round(max(0.0, min(100.0, total)), 1),
        confidence=confidence,
        factors=results,
        weights=w,
        scored_at=at,
        engine_version=ENGINE_VERSION,
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
