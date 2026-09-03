/** Client-side re-weighting.
 *
 *  This is the one piece of scoring logic that lives in the browser, and it is
 *  arithmetic rather than judgement. The server decides what each factor scored,
 *  what evidence supports it, and — critically — whether it was measured at all.
 *  All this does is re-apply weights to those fixed subscores.
 *
 *  It exists so the weight sliders feel like moving a dial rather than
 *  submitting a query. A round trip per adjustment would put a couple hundred
 *  milliseconds between the cause and the effect, and at that latency you stop
 *  exploring a thesis and start filling in a form.
 *
 *  It mirrors `_effective_weights` in `backend/app/scoring/engine.py` exactly,
 *  including the coverage floor. That duplication is deliberate — the
 *  alternative is a round trip per slider movement — but it is a real risk: if
 *  the two implementations ever drift, the UI shows a number the API would not
 *  reproduce, and the whole "you can audit this score" claim collapses.
 *
 *  The two are held together by a generated fixture rather than by care.
 *  `backend/scripts/export_scoring_parity.py` runs the real engine over cases
 *  chosen for the branches that differ — full coverage, partial coverage,
 *  coverage under the floor, all-zero weights — and records its answers.
 *  `scoring.test.ts` asserts this file reproduces them; the backend's
 *  `test_scoring_parity.py` asserts the fixture still matches the engine.
 *  Change the rule on either side alone and a suite goes red.
 */

import type { Confidence, FactorKey, ScoredCompany, Weights } from "./types";

/** Below this share of the declared thesis, redistribution is extrapolation
 *  rather than inference, and the declared weights are kept instead. */
export const MIN_COVERAGE_FOR_REDISTRIBUTION = 0.25;

const CONFIDENCE_VALUE: Record<Confidence, number> = {
  high: 1.0,
  medium: 0.55,
  low: 0.15,
};

export function normaliseWeights(weights: Weights): Weights {
  const total = Object.values(weights).reduce((a, b) => a + b, 0);
  if (total <= 0) {
    // Degenerate input — equal weighting beats dividing by zero.
    const equal = 1 / Object.keys(weights).length;
    return Object.fromEntries(
      Object.keys(weights).map((k) => [k, equal]),
    ) as Weights;
  }
  return Object.fromEntries(
    Object.entries(weights).map(([k, v]) => [k, v / total]),
  ) as Weights;
}

export interface Rescored {
  score: number;
  confidence: Confidence;
  coveredWeight: number;
  effective: Partial<Record<FactorKey, number>>;
  /** Points each factor contributed, for the segmented bar. */
  contributions: Partial<Record<FactorKey, number>>;
}

export function rescore(item: ScoredCompany, weights: Weights): Rescored {
  const declared = normaliseWeights(weights);
  const measured = new Set(
    item.score.factors.filter((f) => f.measured).map((f) => f.key),
  );

  const coveredWeight = item.score.factors
    .filter((f) => measured.has(f.key))
    .reduce((sum, f) => sum + (declared[f.key] ?? 0), 0);

  const redistribute =
    measured.size > 0 && coveredWeight >= MIN_COVERAGE_FOR_REDISTRIBUTION;

  const effective: Partial<Record<FactorKey, number>> = {};
  for (const f of item.score.factors) {
    effective[f.key] = redistribute
      ? measured.has(f.key)
        ? (declared[f.key] ?? 0) / coveredWeight
        : 0
      : (declared[f.key] ?? 0);
  }

  const contributions: Partial<Record<FactorKey, number>> = {};
  let total = 0;
  for (const f of item.score.factors) {
    const points = f.score * (effective[f.key] ?? 0);
    contributions[f.key] = points;
    total += points;
  }

  // Confidence is measured against the *declared* weights, never the effective
  // ones. Redistributing must not make a thinly-evidenced company look more
  // certain — knowing less should never read as knowing more.
  const weighted = item.score.factors.reduce(
    (sum, f) => sum + CONFIDENCE_VALUE[f.confidence] * (declared[f.key] ?? 0),
    0,
  );
  const confidence: Confidence =
    weighted >= 0.7 ? "high" : weighted >= 0.4 ? "medium" : "low";

  return {
    score: Math.round(Math.max(0, Math.min(100, total)) * 10) / 10,
    confidence,
    coveredWeight,
    effective,
    contributions,
  };
}
