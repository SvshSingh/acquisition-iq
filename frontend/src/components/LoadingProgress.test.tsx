import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoadingProgress } from "./LoadingProgress";

/** The component's promises are about *timing*, so the clock is faked and
 *  driven by hand: it stays hidden through a fast load, appears and climbs on a
 *  slow one, explains itself only once the wait is genuinely long, and reaches
 *  100 only when the request actually ends. `performance.now` is pinned to the
 *  fake clock so the elapsed the curve reads advances exactly as the timers do. */
const CONFIG = { showDelayMs: 200, slowAfterMs: 3500, doneHoldMs: 550 };

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(performance, "now").mockImplementation(() => Date.now());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe("LoadingProgress", () => {
  it("shows nothing until the request has been outstanding a beat", () => {
    render(<LoadingProgress active config={CONFIG} />);
    // Before the show delay there is no loader to flash on a fast response.
    expect(screen.queryByRole("progressbar")).toBeNull();
    advance(CONFIG.showDelayMs - 20);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("never appears at all for a load that resolves faster than the delay", () => {
    const { rerender } = render(<LoadingProgress active config={CONFIG} />);
    advance(CONFIG.showDelayMs - 50);
    rerender(<LoadingProgress active={false} config={CONFIG} />);
    advance(1000);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("appears after the delay and climbs as the wait goes on", () => {
    render(<LoadingProgress active config={CONFIG} />);
    advance(CONFIG.showDelayMs);

    const bar = screen.getByRole("progressbar");
    const start = Number(bar.getAttribute("aria-valuenow"));
    expect(start).toBeGreaterThanOrEqual(0);
    expect(start).toBeLessThan(20);

    advance(5000);
    const later = Number(screen.getByRole("progressbar").getAttribute("aria-valuenow"));
    expect(later).toBeGreaterThan(start);
    expect(later).toBeLessThan(100); // the timer never completes the bar
  });

  it("explains the cold start only once the wait is genuinely long", () => {
    render(<LoadingProgress active config={CONFIG} />);
    advance(CONFIG.showDelayMs);
    expect(screen.getByText(/loading companies/i)).toBeTruthy();

    advance(CONFIG.slowAfterMs);
    expect(screen.getByText(/waking the server/i)).toBeTruthy();
    expect(screen.queryByText(/^loading companies/i)).toBeNull();
  });

  it("reaches 100 only when the request ends, then clears itself", () => {
    const { rerender } = render(<LoadingProgress active config={CONFIG} />);
    advance(CONFIG.showDelayMs + 4000);
    expect(Number(screen.getByRole("progressbar").getAttribute("aria-valuenow"))).toBeLessThan(100);

    // Response arrives.
    rerender(<LoadingProgress active={false} config={CONFIG} />);
    act(() => {}); // flush the phase-change effect
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("100");

    // ...and after the brief hold it removes itself entirely.
    advance(CONFIG.doneHoldMs);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("carries the progressbar ARIA contract throughout", () => {
    render(<LoadingProgress active config={CONFIG} />);
    advance(CONFIG.showDelayMs + 2000);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuemin")).toBe("0");
    expect(bar.getAttribute("aria-valuemax")).toBe("100");
    const now = Number(bar.getAttribute("aria-valuenow"));
    expect(now).toBeGreaterThanOrEqual(0);
    expect(now).toBeLessThanOrEqual(100);
  });
});
