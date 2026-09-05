import { describe, expect, it } from "vitest";
import { DEFAULT_TRICKLE, trickleProgress, tricklePercent } from "./progress";

/** The curve's whole reason to exist is that it never lies about being done.
 *  These pin the properties that guarantee it — monotonic, bounded strictly
 *  below the cap, paced to the expected wait — rather than one hand-picked
 *  number, so a future tweak to the shape cannot quietly break the contract. */
describe("trickleProgress", () => {
  it("is zero at and before the start, and on bad input", () => {
    expect(trickleProgress(0)).toBe(0);
    expect(trickleProgress(-1000)).toBe(0);
    expect(trickleProgress(Number.NaN)).toBe(0);
  });

  it("increases monotonically", () => {
    let prev = -1;
    for (let t = 0; t <= 120_000; t += 250) {
      const v = trickleProgress(t);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });

  it("stays below the cap for any realistic wait, and never above it", () => {
    // Strictly below across the range a cold start could plausibly span...
    for (const t of [30_000, 60_000, 120_000, 300_000]) {
      expect(trickleProgress(t)).toBeLessThan(DEFAULT_TRICKLE.cap);
    }
    // ...and, at absurd elapsed where e^(-t/τ) underflows to zero, it may equal
    // the cap in floating point but must never exceed it. The guarantee the UI
    // actually leans on — that the *shown* number never hits 100 — is enforced
    // by tricklePercent's clamp and checked in its own test below.
    expect(trickleProgress(3_600_000)).toBeLessThanOrEqual(DEFAULT_TRICKLE.cap);
  });

  it("asymptotes toward the cap for a very long wait", () => {
    const v = trickleProgress(DEFAULT_TRICKLE.expectedMs * 20);
    expect(v).toBeGreaterThan(DEFAULT_TRICKLE.cap * 0.999);
    expect(v).toBeLessThanOrEqual(DEFAULT_TRICKLE.cap);
  });

  it("is paced to the expected wait — nearly full, not full, at that mark", () => {
    // ~95% of the way to the cap at the expected wait (1 - e^-3), by design.
    const atExpected = trickleProgress(DEFAULT_TRICKLE.expectedMs);
    expect(atExpected).toBeGreaterThan(DEFAULT_TRICKLE.cap * 0.9);
    expect(atExpected).toBeLessThan(DEFAULT_TRICKLE.cap);
  });

  it("decelerates — the first half of the wait covers more ground than the second", () => {
    const half = DEFAULT_TRICKLE.expectedMs / 2;
    const firstHalf = trickleProgress(half) - trickleProgress(0);
    const secondHalf = trickleProgress(DEFAULT_TRICKLE.expectedMs) - trickleProgress(half);
    expect(firstHalf).toBeGreaterThan(secondHalf);
  });

  it("moves slower when paced for a longer wait", () => {
    const fast = trickleProgress(5_000, { expectedMs: 10_000, cap: 0.92 });
    const slow = trickleProgress(5_000, { expectedMs: 60_000, cap: 0.92 });
    expect(slow).toBeLessThan(fast);
  });
});

describe("tricklePercent", () => {
  it("is an integer the user could read off the screen", () => {
    for (const t of [0, 1_000, 5_000, 15_000, 30_000, 90_000]) {
      const p = tricklePercent(t);
      expect(Number.isInteger(p)).toBe(true);
    }
  });

  it("never displays 100 from the timer, even at absurd elapsed", () => {
    expect(tricklePercent(0)).toBe(0);
    expect(tricklePercent(60 * 60_000)).toBeLessThan(100);
    expect(tricklePercent(60 * 60_000)).toBeLessThanOrEqual(Math.floor(DEFAULT_TRICKLE.cap * 100));
  });
});
