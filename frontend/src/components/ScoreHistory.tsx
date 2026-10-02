import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ScoreHistoryEntry } from "../lib/types";

/** The scores this company has been given, newest first.
 *
 *  A score that moved is information: a site that went stale, an owner who
 *  finally listed an email. Each refresh is recorded on the server, so the
 *  movement is something to look at rather than something that silently
 *  replaced the previous number.
 *
 *  Renders nothing until there is something to compare. One entry is just the
 *  current score restated, and a section that exists only to say "no history
 *  yet" is furniture. The snapshot store keeps no history at all, and imported
 *  rows are not in the store, so both cases quietly produce nothing here. */
export function ScoreHistory({
  companyId,
  refreshedAt,
}: {
  companyId: string;
  /** Part of the query key, so a refresh landing refetches the history. */
  refreshedAt: string | null;
}) {
  const history = useQuery({
    queryKey: ["history", companyId, refreshedAt],
    queryFn: () => api.history(companyId),
    retry: false,
    staleTime: 60_000,
  });

  const entries = history.data?.history ?? [];
  if (entries.length < 2) return null;

  return (
    <section className="border-t border-[var(--color-rule)] px-6 py-5">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]">
        Score over time
      </h3>
      <ol className="mt-3 space-y-1.5 text-[13px]">
        {entries.map((entry, index) => (
          <HistoryRow key={entry.scored_at} entry={entry} previous={entries[index + 1]} />
        ))}
      </ol>
    </section>
  );
}

function HistoryRow({
  entry,
  previous,
}: {
  entry: ScoreHistoryEntry;
  previous: ScoreHistoryEntry | undefined;
}) {
  const delta = previous ? entry.score - previous.score : null;
  return (
    <li className="flex items-baseline justify-between gap-3">
      <time dateTime={entry.scored_at} className="text-[var(--color-ink-soft)]">
        {formatWhen(entry.scored_at)}
      </time>
      <span className="tnum flex items-baseline gap-2">
        {delta !== null && Math.abs(delta) >= 0.05 ? (
          <span className="text-[11px] text-[var(--color-ink-faint)]">
            {delta > 0 ? "+" : "−"}
            {Math.abs(delta).toFixed(1)}
          </span>
        ) : null}
        <span className="font-medium text-[var(--color-ink)]">{entry.score.toFixed(1)}</span>
      </span>
    </li>
  );
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
