/** The maths behind the first-load progress bar.
 *
 *  The bar exists for one situation: the API is a long-running container on a
 *  free tier that sleeps when idle, so the first request after a quiet spell
 *  waits 30-60s for the container to wake. Thirty seconds of an apparently
 *  inert page reads as broken, and the honest fix is to show that something is
 *  happening — without lying about how far along it is.
 *
 *  That last part is the whole design. The browser cannot know when a cold
 *  container will answer, so a bar animated to hit 100% on a fixed timer is
 *  guesswork dressed as fact: it crawls at 20% when the response already came,
 *  or sits at 100% with nothing loaded. This curve instead *approaches* a
 *  ceiling it never reaches, and the arriving response is the only thing that
 *  finishes it. The number always trails reality rather than inventing it.
 */

export interface TrickleOptions {
  /** The wait the curve is paced for — the cold start's ~30s. Only sets the
   *  pace; a faster or slower response still drives completion. */
  expectedMs: number;
  /** The ceiling the trickle asymptotes toward, as a fraction. Never reached:
   *  crossing it is the response's job, so the bar cannot claim done first. */
  cap: number;
}

export const DEFAULT_TRICKLE: TrickleOptions = { expectedMs: 30_000, cap: 0.92 };

/** Fraction complete (0..cap, exclusive of cap) after `elapsedMs`.
 *
 *  Shape: `cap · (1 − e^(−elapsed / τ))`, with `τ = expectedMs / 3`. The `/3`
 *  is what makes it feel calibrated: `1 − e^(−3) ≈ 0.95`, so at the expected
 *  wait the bar is about 95% of the way to the ceiling — visibly nearly-there
 *  right when a typical cold start returns — while still leaving honest
 *  headroom the response fills. Decelerating throughout: quick off the line,
 *  slow near the top, the way waiting actually feels. */
export function trickleProgress(
  elapsedMs: number,
  opts: TrickleOptions = DEFAULT_TRICKLE,
): number {
  const { expectedMs, cap } = opts;
  // Guards 0, negatives, and NaN in one comparison (NaN > 0 is false).
  if (!(elapsedMs > 0)) return 0;
  const tau = expectedMs / 3;
  return cap * (1 - Math.exp(-elapsedMs / tau));
}

/** The integer percent shown to the user. Clamped strictly below 100: only the
 *  arriving response is allowed to display 100, never the timer. */
export function tricklePercent(
  elapsedMs: number,
  opts: TrickleOptions = DEFAULT_TRICKLE,
): number {
  return Math.min(99, Math.max(0, Math.floor(trickleProgress(elapsedMs, opts) * 100)));
}
