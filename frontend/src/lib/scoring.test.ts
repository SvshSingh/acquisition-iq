import { describe, expect, it } from "vitest";
import fixture from "./__fixtures__/scoring-parity.json";
import { MIN_COVERAGE_FOR_REDISTRIBUTION, normaliseWeights, rescore } from "./scoring";
import type { Confidence, FactorKey, ScoredCompany, Weights } from "./types";

/** The parity suite.
 *
 *  `rescore` re-implements `_effective_weights` from the Python engine so that
 *  a slider re-sorts the table in a frame instead of a round trip. The
 *  duplication buys real interactivity, but nothing about writing the same rule
 *  twice keeps the two copies honest — and if they drift the UI shows a number
 *  the API would not reproduce, which quietly breaks the one claim the product
 *  is built on.
 *
 *  So the fixture below is not hand-written. `backend/scripts/export_scoring_parity.py`
 *  runs the real engine and records its answers; this suite asserts TypeScript
 *  reproduces them, and `backend/tests/test_scoring_parity.py` asserts the file
 *  still matches the engine. Changing the rule on one side without the other
 *  now fails a suite instead of shipping. */

interface ParityCase {
  label: string;
  scoredCompany: ScoredCompany;
  weights: Weights;
  expected: {
    score: number;
    confidence: Confidence;
    coveredWeight: number;
    effectiveWeights: Record<string, number>;
    contributions: Record<string, number>;
  };
}

const cases = fixture.cases as unknown as ParityCase[];

describe("rescore matches the Python engine", () => {
  it("exports cases covering every branch of the rule", () => {
    // A parity suite that silently lost its cases would pass forever.
    expect(cases.length).toBeGreaterThanOrEqual(6);
    const covered = cases.map((c) => c.expected.coveredWeight);
    expect(Math.max(...covered)).toBe(1); // nothing redistributed
    expect(covered.some((c) => c > 0 && c < 1)).toBe(true); // redistribution ran
    expect(covered.some((c) => c < MIN_COVERAGE_FOR_REDISTRIBUTION)).toBe(true); // floor hit
  });

  it.each(cases.map((c) => [c.label, c] as const))("%s", (_label, testCase) => {
    const actual = rescore(testCase.scoredCompany, testCase.weights);
    const { expected } = testCase;

    // The score is what a user reads and sorts on, so it must agree exactly at
    // the precision both sides round to.
    expect(actual.score).toBe(expected.score);
    expect(actual.confidence).toBe(expected.confidence);
    expect(actual.coveredWeight).toBeCloseTo(expected.coveredWeight, 9);

    for (const [key, weight] of Object.entries(expected.effectiveWeights)) {
      expect(actual.effective[key as FactorKey] ?? 0).toBeCloseTo(weight, 9);
    }
    for (const [key, points] of Object.entries(expected.contributions)) {
      expect(actual.contributions[key as FactorKey] ?? 0).toBeCloseTo(points, 9);
    }
  });
});

/** The fixture pins the numbers. These pin the *reasons* — the invariants that
 *  a future edit could break while still producing plausible output. */
describe("the redistribution rule holds its invariants", () => {
  const partial = cases.find((c) => c.label === "licence_only_default")!;
  const belowFloor = cases.find((c) => c.label === "sparse_below_coverage_floor")!;

  it("never lets an unmeasured factor contribute points", () => {
    const actual = rescore(partial.scoredCompany, partial.weights);
    const unmeasured = partial.scoredCompany.score.factors.filter((f) => !f.measured);
    expect(unmeasured.length).toBeGreaterThan(0);
    for (const factor of unmeasured) {
      expect(actual.effective[factor.key]).toBe(0);
      expect(actual.contributions[factor.key]).toBe(0);
    }
  });

  it("gives the measured factors the whole thesis when it redistributes", () => {
    const actual = rescore(partial.scoredCompany, partial.weights);
    const total = Object.values(actual.effective).reduce((a, b) => a + b, 0);
    expect(total).toBeCloseTo(1, 9);
  });

  it("keeps the declared weights when coverage is below the floor", () => {
    // Under the floor, reweighting would be extrapolation from almost nothing,
    // so the honest move is to score against what was actually asked for.
    expect(belowFloor.expected.coveredWeight).toBeLessThan(MIN_COVERAGE_FOR_REDISTRIBUTION);
    const actual = rescore(belowFloor.scoredCompany, belowFloor.weights);
    const declared = normaliseWeights(belowFloor.weights);
    for (const factor of belowFloor.scoredCompany.score.factors) {
      expect(actual.effective[factor.key]).toBeCloseTo(declared[factor.key], 9);
    }
  });

  it("does not let redistribution inflate confidence", () => {
    // The invariant that matters most: a company we know less about must never
    // read as more certain because its weight moved. Confidence is computed
    // against declared weights, so dropping a measured factor's weight to zero
    // can only lower it.
    const item = partial.scoredCompany;
    const measured = item.score.factors.filter((f) => f.measured).map((f) => f.key);
    const onlyUnmeasured = Object.fromEntries(
      item.score.factors.map((f) => [f.key, measured.includes(f.key) ? 0 : 1]),
    ) as Weights;

    const actual = rescore(item, onlyUnmeasured);
    // Every point of declared weight now sits on factors backed by priors.
    expect(actual.confidence).toBe("low");
  });

  it("clamps the score into 0..100 and never emits NaN", () => {
    for (const testCase of cases) {
      const actual = rescore(testCase.scoredCompany, testCase.weights);
      expect(Number.isFinite(actual.score)).toBe(true);
      expect(actual.score).toBeGreaterThanOrEqual(0);
      expect(actual.score).toBeLessThanOrEqual(100);
    }
  });
});

describe("normaliseWeights", () => {
  const keys: FactorKey[] = [
    "succession",
    "buy_box",
    "digital_gap",
    "fragmentation",
    "contactability",
    "health",
  ];
  const build = (value: number) =>
    Object.fromEntries(keys.map((k) => [k, value])) as Weights;

  it("scales any positive vector to sum to one", () => {
    const out = normaliseWeights(build(7));
    expect(Object.values(out).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 9);
  });

  it("falls back to equal weighting rather than dividing by zero", () => {
    // Reachable from the UI: drag every slider to zero.
    const out = normaliseWeights(build(0));
    for (const key of keys) expect(out[key]).toBeCloseTo(1 / keys.length, 9);
  });

  it("preserves the ratio between factors", () => {
    const out = normaliseWeights({ ...build(1), succession: 3 } as Weights);
    expect(out.succession / out.health).toBeCloseTo(3, 9);
  });
});
