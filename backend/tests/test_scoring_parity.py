"""The backend half of the cross-language parity contract.

`frontend/src/lib/scoring.ts` re-implements weight redistribution so a slider
re-sorts the table without a round trip. The fixture the TypeScript suite reads
is generated from this engine, which makes it authoritative — but only while it
is current. Without this test the fixture would freeze at whatever the engine
did the day it was written, the TypeScript suite would keep passing against a
stale contract, and the drift it exists to prevent would happen silently on the
Python side instead.

So: the committed file must still be exactly what the engine produces today.
"""

from __future__ import annotations

import json

import pytest

from scripts.export_scoring_parity import OUT, build


@pytest.fixture(scope="module")
def committed() -> dict:
    if not OUT.exists():  # pragma: no cover - only if the file is deleted
        pytest.fail(
            f"{OUT} is missing. Regenerate it with "
            "`python scripts/export_scoring_parity.py`."
        )
    return json.loads(OUT.read_text(encoding="utf-8"))


def test_fixture_matches_the_engine(committed: dict) -> None:
    """The whole point: regenerate-and-compare, so drift cannot be silent."""
    assert committed == build(), (
        "The scoring engine no longer produces the committed parity fixture.\n"
        "If the rule changed on purpose, regenerate it with\n"
        "    python scripts/export_scoring_parity.py\n"
        "and make the matching change in frontend/src/lib/scoring.ts — the "
        "TypeScript suite reads this file and will fail until both agree."
    )


def test_fixture_is_stamped_with_the_engine_version(committed: dict) -> None:
    from app.scoring.engine import ENGINE_VERSION

    assert committed["engineVersion"] == ENGINE_VERSION


def test_fixture_exercises_the_branches_that_matter(committed: dict) -> None:
    """A parity file that only covered the easy path would pass forever."""
    covered = [c["expected"]["coveredWeight"] for c in committed["cases"]]
    assert any(c == 1 for c in covered), "no fully-measured case"
    assert any(0 < c < 1 for c in covered), "no case where redistribution ran"

    from app.scoring.engine import MIN_COVERAGE_FOR_REDISTRIBUTION

    assert any(c < MIN_COVERAGE_FOR_REDISTRIBUTION for c in covered), (
        "no case below the coverage floor, so the branch that keeps the "
        "declared weights is untested on both sides"
    )


def test_unmeasured_factors_never_contribute(committed: dict) -> None:
    for case in committed["cases"]:
        factors = case["scoredCompany"]["score"]["factors"]
        for factor in factors:
            if factor["measured"]:
                continue
            key = factor["key"]
            if case["expected"]["coveredWeight"] >= 0.25:
                assert case["expected"]["effectiveWeights"][key] == 0, (
                    f"{case['label']}: unmeasured {key} kept weight"
                )
                assert case["expected"]["contributions"][key] == 0
