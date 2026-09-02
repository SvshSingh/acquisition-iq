import type { FactorKey, FactorMeta, Weights } from "../lib/types";
import { FACTOR_ORDER } from "../lib/types";
import { factorTint } from "./primitives";

/** The thesis editor.
 *
 *  A searcher's buy box is personal — one fund cares about succession above all,
 *  another is running a roll-up and cares about fragmentation. A fixed rubric
 *  would be a different product's opinion imposed on theirs, so the weights are
 *  the user's to set and the table re-sorts as they move.
 *
 *  Weights are normalised at use, so these are proportions rather than
 *  percentages that must sum to a hundred. The reader is shown the normalised
 *  share so the sliders never appear to disagree with the arithmetic. */
export function WeightsPanel({
  factors,
  weights,
  onChange,
  onReset,
  isDirty,
}: {
  factors: FactorMeta[];
  weights: Weights;
  onChange: (key: FactorKey, value: number) => void;
  onReset: () => void;
  isDirty: boolean;
}) {
  const total = Object.values(weights).reduce((a, b) => a + b, 0) || 1;

  return (
    <section aria-labelledby="weights-heading" className="p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2
          id="weights-heading"
          className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]"
        >
          Your thesis
        </h2>
        {isDirty ? (
          <button
            type="button"
            onClick={onReset}
            className="text-[11px] text-[var(--color-ink-faint)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
          >
            Reset
          </button>
        ) : null}
      </div>

      <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-ink-faint)]">
        Weight the six factors to match how you buy. The table re-sorts as you
        move them.
      </p>

      <div className="mt-5 space-y-4">
        {FACTOR_ORDER.map((key) => {
          const meta = factors.find((f) => f.key === key);
          const share = weights[key] / total;
          return (
            <div key={key}>
              <div className="flex items-center justify-between gap-2">
                <label
                  htmlFor={`weight-${key}`}
                  className="flex items-center gap-2 text-[13px] font-medium text-[var(--color-ink)]"
                  title={meta?.description}
                >
                  <span
                    aria-hidden
                    className="size-2 shrink-0 rounded-[2px]"
                    style={{ background: factorTint(key) }}
                  />
                  {meta?.label ?? key}
                </label>
                <span className="tnum text-[12px] text-[var(--color-ink-faint)]">
                  {Math.round(share * 100)}%
                </span>
              </div>
              <input
                id={`weight-${key}`}
                type="range"
                min={0}
                max={100}
                step={1}
                value={Math.round(weights[key] * 100)}
                onChange={(e) => onChange(key, Number(e.target.value) / 100)}
                aria-describedby={`weight-${key}-desc`}
                className="mt-2 h-1 w-full cursor-pointer appearance-none rounded-full bg-[var(--color-sunken)] accent-[var(--color-accent)]"
              />
              <p id={`weight-${key}-desc`} className="sr-only">
                {meta?.description}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
