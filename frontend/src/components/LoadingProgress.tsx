import { useEffect, useState } from "react";
import { DEFAULT_TRICKLE, type TrickleOptions, tricklePercent } from "../lib/progress";

/** The first-load progress bar.
 *
 *  Shown only while the initial companies request is in flight — the one load
 *  that can hit a cold backend. It fills on a decelerating curve (see
 *  `progress.ts`) that trails the real request rather than racing a timer, and
 *  only the arriving response takes it to 100.
 *
 *  It stays out of the way of a warm load: nothing renders until the request
 *  has been outstanding for `showDelayMs`, so a fast response shows no flash of
 *  loader. And it explains itself only when it needs to — the "waking the
 *  server" line appears after `slowAfterMs`, which a warm load never reaches. */
type Phase = "idle" | "loading" | "complete" | "gone";

interface Config {
  showDelayMs: number;
  slowAfterMs: number;
  doneHoldMs: number;
}

const DEFAULT_CONFIG: Config = { showDelayMs: 200, slowAfterMs: 3500, doneHoldMs: 550 };

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof matchMedia !== "function") return false;
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

export function LoadingProgress({
  active,
  options = DEFAULT_TRICKLE,
  config = DEFAULT_CONFIG,
}: {
  active: boolean;
  options?: TrickleOptions;
  config?: Config;
}) {
  const reduced = usePrefersReducedMotion();
  const [phase, setPhase] = useState<Phase>("idle");
  const [percent, setPercent] = useState(0);
  const [slow, setSlow] = useState(false);
  const [prevActive, setPrevActive] = useState(active);

  // The response arriving (active: true -> false while the bar is up) is a prop
  // edge, and React's sanctioned way to fold a changed prop into state is here,
  // during render — not in an effect. This is the only path that reaches 100,
  // and doing it during render keeps it off the effect-setState path entirely.
  if (active !== prevActive) {
    setPrevActive(active);
    if (!active && phase === "loading") {
      setPhase("complete");
      setPercent(100);
    }
  }

  // A load starting is the one transition that needs a clock — reveal the bar
  // only after showDelayMs, so a fast response never flashes it. The effect
  // just owns the timer; every state change happens in its callback, off the
  // synchronous effect path the linter (rightly) warns about.
  useEffect(() => {
    if (!(active && (phase === "idle" || phase === "gone"))) return;
    const t = setTimeout(() => {
      setPercent(0);
      setSlow(false);
      setPhase("loading");
    }, config.showDelayMs);
    return () => clearTimeout(t);
  }, [active, phase, config.showDelayMs]);

  // Hold the finished bar on screen briefly, then remove it. Its own effect so
  // that entering "complete" cannot cancel its own exit timer.
  useEffect(() => {
    if (phase !== "complete") return;
    const t = setTimeout(() => setPhase("gone"), config.doneHoldMs);
    return () => clearTimeout(t);
  }, [phase, config.doneHoldMs]);

  // The ticker. A coarse interval, not a per-frame loop: the fill has a short
  // CSS transition that smooths the steps, so 100ms is fluid at a fraction of
  // the cost, and it is trivial to drive from a test's fake clock. Under
  // reduced motion it steps more slowly and the global transition-suppression
  // rule flattens the tween.
  useEffect(() => {
    if (phase !== "loading") return;
    const start = performance.now();
    const tick = () => {
      const elapsed = performance.now() - start;
      setPercent(tricklePercent(elapsed, options));
      setSlow(elapsed >= config.slowAfterMs);
    };
    tick();
    const id = setInterval(tick, reduced ? 400 : 100);
    return () => clearInterval(id);
  }, [phase, options, config.slowAfterMs, reduced]);

  if (phase === "idle" || phase === "gone") return null;

  const done = phase === "complete";
  return (
    <div
      // An overlay, not a layout block: it sits over the results area and owns
      // its own exit (fill to 100, fade, unmount) rather than being torn down
      // the moment the request resolves. Once fading it stops catching clicks
      // so the table beneath is live immediately.
      className={`absolute inset-0 z-10 flex flex-col items-center justify-center bg-[var(--color-paper)] px-6 transition-opacity duration-300 ${
        done ? "pointer-events-none opacity-0" : "opacity-100"
      }`}
    >
      <div className="w-full max-w-xs text-center">
        <h2 className="text-[15px] font-semibold tracking-tight text-[var(--color-ink)]">
          AcquisitionIQ
        </h2>

        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-label="Loading companies"
          className="mt-5 h-[3px] w-full overflow-hidden rounded-full bg-[var(--color-sunken)]"
        >
          <div
            className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-150 ease-linear"
            style={{
              width: `${percent}%`,
              // A soft leading glow so the bar reads as moving light rather than
              // a filling tank — the small thing that makes it feel considered.
              boxShadow: "0 0 8px 0 color-mix(in oklch, var(--color-accent) 60%, transparent)",
            }}
          />
        </div>

        <div className="mt-3 flex items-baseline justify-center gap-2">
          <span className="tnum font-mono text-[13px] font-medium text-[var(--color-ink)]">
            {percent}%
          </span>
        </div>

        <p
          aria-live="polite"
          className="mt-1 min-h-[1.25rem] text-[12px] leading-relaxed text-[var(--color-ink-faint)]"
        >
          {slow
            ? "Waking the server — it sleeps when idle, so the first load can take up to 30 seconds."
            : "Loading companies…"}
        </p>
      </div>
    </div>
  );
}
