"""Generate the cross-language parity fixture for the scoring rule.

The weight-redistribution rule is implemented twice: once in
`app/scoring/engine.py`, which owns the judgement, and once in
`frontend/src/lib/scoring.ts`, so that moving a weight slider re-sorts in a
frame instead of a round trip. That duplication is deliberate and explained at
both sites, but it is also a real hazard — if the two drift, the UI shows a
number the API would not reproduce, and the product's central claim that a score
can be audited quietly stops being true.

Prose in a comment does not prevent drift. This does: the Python engine emits
the cases below with its own answers, the TypeScript test asserts its
implementation reproduces them exactly, and the Python test asserts the
committed file still matches what the engine produces today. A change to either
side without the other fails a suite.

    python scripts/export_scoring_parity.py

Cases are chosen for the branches that actually differ between a naive
implementation and this one: full coverage, partial coverage with
redistribution, coverage below the floor where redistribution is skipped,
all-zero weights, and a factor whose absence is a finding rather than a gap.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.schemas import (
    Company,
    Contact,
    FactorWeights,
    VerificationStatus,
    WebSignals,
)
from app.scoring.engine import score_company

NOW = datetime(2026, 9, 1, tzinfo=UTC)

OUT = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "lib"
    / "__fixtures__"
    / "scoring-parity.json"
)

DEFAULT = FactorWeights()
SUCCESSION_HEAVY = FactorWeights(
    succession=1.0, buy_box=0.0, digital_gap=0.0, fragmentation=0.0,
    contactability=0.0, health=0.0,
)
ALL_ZERO = FactorWeights(
    succession=0.0, buy_box=0.0, digital_gap=0.0, fragmentation=0.0,
    contactability=0.0, health=0.0,
)


def cases() -> list[tuple[str, Company, FactorWeights]]:
    rich = Company(
        id="parity-rich",
        name="Whitaker Heating & Cooling",
        website="https://whitakerhvac.example",
        industry="HVAC",
        city="Glendale",
        postcode="91203",
        business_type="Sole Owner",
        has_employees=True,
        employee_count=38,
        revenue_usd=5_200_000,
        founded_year=1979,
        peer_count_in_niche=22,
        contacts=[
            Contact(
                name="Dale Whitaker", title="Owner", email="dale@whitakerhvac.example",
                email_status=VerificationStatus.VERIFIED, phone="+16145550142",
                phone_valid=True, is_decision_maker=True,
            )
        ],
        web=WebSignals(
            fetched_at=NOW, https=False, mobile_viewport=False, has_analytics=False,
            tech_hints=["FrontPage"], copyright_year=2016, latest_content_year=2016,
            page_bytes=41_000, has_team_page=True,
            raw_text_excerpt="A family-owned business serving central Ohio since 1979.",
        ),
    )
    # Licence-only: no website at all, so the two web-dependent factors are
    # unmeasured and their weight must move. This is the shape of most real rows.
    licence_only = Company(
        id="parity-licence-only",
        name="Corner Plumbing",
        industry="Plumbing",
        city="Burbank",
        postcode="91502",
        business_type="Sole Owner",
        has_employees=True,
        founded_year=1988,
        peer_count_in_niche=14,
        contacts=[Contact(phone="+16145550143", phone_valid=True)],
    )
    # Almost nothing known: coverage falls under the floor, so redistribution is
    # skipped and the declared weights are kept.
    sparse = Company(id="parity-sparse", name="Unknown Services LLC")

    return [
        ("rich_default_weights", rich, DEFAULT),
        ("rich_succession_heavy", rich, SUCCESSION_HEAVY),
        ("licence_only_default", licence_only, DEFAULT),
        ("licence_only_succession_heavy", licence_only, SUCCESSION_HEAVY),
        ("sparse_below_coverage_floor", sparse, DEFAULT),
        ("rich_all_zero_weights", rich, ALL_ZERO),
    ]


def build() -> dict[str, object]:
    out: list[dict[str, object]] = []
    for label, company, weights in cases():
        result = score_company(company, weights=weights, now=NOW)
        out.append(
            {
                "label": label,
                # The client only ever receives a scored company, so the fixture
                # carries exactly that shape rather than the raw inputs.
                "scoredCompany": {
                    "company": json.loads(company.model_dump_json()),
                    "score": json.loads(result.model_dump_json()),
                },
                "weights": json.loads(weights.model_dump_json()),
                "expected": {
                    "score": result.score,
                    "confidence": result.confidence.value,
                    "coveredWeight": result.covered_weight,
                    "effectiveWeights": {
                        k.value: round(v, 10) for k, v in result.effective_weights.items()
                    },
                    "contributions": {
                        f.key.value: round(result.contribution(f.key), 10)
                        for f in result.factors
                    },
                },
            }
        )
    return {
        "generatedBy": "backend/scripts/export_scoring_parity.py",
        "engineVersion": out[0]["scoredCompany"]["score"]["engine_version"],  # type: ignore[index]
        "note": (
            "Emitted by the Python engine. frontend/src/lib/scoring.test.ts asserts "
            "the TypeScript implementation reproduces these exactly; "
            "backend/tests/test_scoring_parity.py asserts this file still matches "
            "the engine. Regenerate with the script above when the rule changes "
            "on purpose."
        ),
        "cases": out,
    }


def main() -> None:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(payload['cases'])} cases)")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
