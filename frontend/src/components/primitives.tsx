import type { Confidence, FactorKey } from "../lib/types";
import { FACTOR_ORDER } from "../lib/types";

/* The six factors are shaded as one hue at six lightnesses rather than six
   different colours. A rainbow would imply the factors are unrelated
   categories; they are six parts of one number, and a ramp says that. It also
   keeps the promise the palette makes elsewhere — colour means score, and
   nothing else competes for it. */
const FACTOR_TINT: Record<FactorKey, string> = {
  succession: "oklch(52% 0.14 58)",
  buy_box: "oklch(60% 0.13 64)",
  digital_gap: "oklch(68% 0.115 70)",
  fragmentation: "oklch(75% 0.095 76)",
  contactability: "oklch(82% 0.075 80)",
  health: "oklch(88% 0.055 84)",
};

export function factorTint(key: FactorKey): string {
  return FACTOR_TINT[key];
}

export function ScoreNumber({ value, size = "md" }: { value: number; size?: "sm" | "md" | "lg" }) {
  const cls =
    size === "lg" ? "text-5xl" : size === "md" ? "text-2xl" : "text-base";
  return (
    <span className={`tnum font-semibold tracking-tight ${cls}`}>
      {value.toFixed(1)}
    </span>
  );
}

/** Stacked bar showing which factors produced the score.
 *
 *  This is the whole pitch in one control: the number is not an opinion, it is
 *  a sum, and you can see the parts. The bar is drawn to a fixed 100-point
 *  scale rather than normalised to its own total, so two rows are comparable by
 *  eye — normalising would make a 30 and a 70 look identical. */
export function ContributionBar({
  contributions,
  height = 6,
  title,
}: {
  contributions: Partial<Record<FactorKey, number>>;
  height?: number;
  title?: string;
}) {
  return (
    <div
      className="flex w-full overflow-hidden rounded-full bg-[var(--color-sunken)]"
      style={{ height }}
      title={title}
      role="presentation"
    >
      {FACTOR_ORDER.map((key) => {
        const points = contributions[key] ?? 0;
        if (points <= 0.05) return null;
        return (
          <div
            key={key}
            style={{ width: `${points}%`, background: FACTOR_TINT[key] }}
            className="h-full"
          />
        );
      })}
    </div>
  );
}

const CONFIDENCE_COPY: Record<Confidence, string> = {
  high: "Most of the thesis was backed by evidence",
  medium: "Parts of the thesis had no evidence behind them",
  low: "Little of the thesis could be evidenced for this company",
};

export function ConfidenceBadge({ value }: { value: Confidence }) {
  return (
    <span
      title={CONFIDENCE_COPY[value]}
      className="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--color-ink-soft)]"
    >
      <span
        aria-hidden
        className="size-1.5 rounded-full"
        style={{ background: `var(--color-${value})` }}
      />
      {value}
    </span>
  );
}

/** How much of the user's declared thesis actually had evidence behind it.
 *
 *  Shown next to every score because a 78 backed by 40% of the thesis is a
 *  different claim from a 78 backed by 95%, and a product that hides the
 *  difference is the black box this one exists to replace. */
export function CoverageMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span
      className="inline-flex items-center gap-1.5 tnum text-[11px] text-[var(--color-ink-faint)]"
      title={`Scored on ${pct}% of your weighted thesis. The rest had no evidence and its weight was redistributed.`}
    >
      <span className="relative block h-1 w-8 overflow-hidden rounded-full bg-[var(--color-sunken)]">
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-[var(--color-rule-strong)]"
          style={{ width: `${pct}%` }}
        />
      </span>
      {pct}%
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded ${className}`} />;
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-md px-6 py-20 text-center">
      <p className="text-sm font-medium text-[var(--color-ink)]">{title}</p>
      {children ? (
        <p className="mt-2 text-sm leading-relaxed text-[var(--color-ink-soft)]">{children}</p>
      ) : null}
    </div>
  );
}
