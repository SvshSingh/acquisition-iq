import { useEffect, useRef } from "react";
import { displayName } from "../lib/format";
import type { Rescored } from "../lib/scoring";
import type { ScoredCompany } from "../lib/types";
import {
  ConfidenceBadge,
  ContributionBar,
  CoverageMeter,
  ScoreNumber,
} from "./primitives";

export interface Row {
  item: ScoredCompany;
  rescored: Rescored;
}

export type SortKey = "score" | "name" | "coverage";
export interface SortState {
  key: SortKey;
  dir: "asc" | "desc";
}

/** The ranked list.
 *
 *  Rows are focusable and navigable with the arrow keys, because this is a list
 *  someone works down for an hour — reaching for the mouse for every row is the
 *  difference between a tool and a demo. Enter opens the drawer, space toggles
 *  selection for export. */
export function ResultsTable({
  rows,
  selectedId,
  checked,
  onOpen,
  onToggle,
  onToggleAll,
  sort,
  onSort,
}: {
  rows: Row[];
  selectedId: string | null;
  checked: Set<string>;
  onOpen: (id: string) => void;
  onToggle: (id: string) => void;
  onToggleAll: () => void;
  sort: SortState;
  onSort: (key: SortKey) => void;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);

  // Keep the focused row in view when arrowing past the fold.
  useEffect(() => {
    if (!selectedId) return;
    const el = bodyRef.current?.querySelector(`[data-row-id="${CSS.escape(selectedId)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>, index: number, id: string) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = event.key === "ArrowDown" ? index + 1 : index - 1;
      const target = rows[next];
      if (target) {
        const el = bodyRef.current?.querySelector<HTMLElement>(
          `[data-row-id="${CSS.escape(target.item.company.id)}"]`,
        );
        el?.focus();
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      onOpen(id);
    } else if (event.key === " ") {
      event.preventDefault();
      onToggle(id);
    }
  }

  const allChecked = rows.length > 0 && rows.every((r) => checked.has(r.item.company.id));

  function header(key: SortKey, label: string, align: "left" | "right" = "left") {
    const active = sort.key === key;
    return (
      <button
        type="button"
        onClick={() => onSort(key)}
        // aria-sort belongs on the header a screen reader announces, and it has
        // to say "none" rather than be omitted when another column is active.
        aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
        className={`flex items-center gap-1 uppercase tracking-[0.1em] hover:text-[var(--color-ink)] ${
          align === "right" ? "justify-end" : ""
        } ${active ? "text-[var(--color-ink)]" : ""}`}
      >
        {label}
        <span aria-hidden className={active ? "opacity-100" : "opacity-0"}>
          {sort.dir === "asc" ? "↑" : "↓"}
        </span>
      </button>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="grid grid-cols-[2rem_1fr_5.5rem_9rem_7rem] items-center gap-3 border-b border-[var(--color-rule)] px-4 py-2 text-[11px] font-semibold text-[var(--color-ink-faint)]">
        <input
          type="checkbox"
          aria-label="Select all rows"
          checked={allChecked}
          onChange={onToggleAll}
          className="size-3.5 accent-[var(--color-accent)]"
        />
        {header("name", "Company")}
        {header("score", "Fit", "right")}
        <span className="uppercase tracking-[0.1em]">Contribution</span>
        {header("coverage", "Confidence")}
      </div>

      <div ref={bodyRef} className="relative min-h-0 flex-1 overflow-y-auto">
        {rows.map((row, index) => {
          const { company } = row.item;
          const active = company.id === selectedId;
          return (
            <div
              key={company.id}
              data-row-id={company.id}
              tabIndex={0}
              role="button"
              aria-pressed={active}
              onClick={() => onOpen(company.id)}
              onKeyDown={(e) => onKeyDown(e, index, company.id)}
              className={`grid cursor-pointer grid-cols-[2rem_1fr_5.5rem_9rem_7rem] items-center gap-3 border-b border-[var(--color-rule)] px-4 py-3 text-left transition-colors ${
                active
                  ? "bg-[var(--color-accent-soft)]"
                  : "hover:bg-[var(--color-surface)]"
              }`}
            >
              <input
                type="checkbox"
                aria-label={`Select ${company.name}`}
                checked={checked.has(company.id)}
                onChange={() => onToggle(company.id)}
                onClick={(e) => e.stopPropagation()}
                className="size-3.5 accent-[var(--color-accent)]"
              />

              <div className="min-w-0">
                <div className="truncate text-[13.5px] font-medium text-[var(--color-ink)]">
                  {displayName(company.name)}
                </div>
                <div className="mt-0.5 flex items-center gap-2 truncate text-[11.5px] text-[var(--color-ink-faint)]">
                  <span>{company.industry}</span>
                  <span aria-hidden>·</span>
                  <span>{company.city}</span>
                  {company.business_type ? (
                    <>
                      <span aria-hidden>·</span>
                      <span>{company.business_type}</span>
                    </>
                  ) : null}
                </div>
              </div>

              <div className="text-right">
                <ScoreNumber value={row.rescored.score} size="sm" />
              </div>

              <ContributionBar
                contributions={row.rescored.contributions}
                title="How each factor contributed to this score"
              />

              <div className="flex flex-col items-start gap-1">
                <ConfidenceBadge value={row.rescored.confidence} />
                <CoverageMeter value={row.rescored.coveredWeight} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
