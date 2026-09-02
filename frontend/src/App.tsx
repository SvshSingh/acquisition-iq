import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type SearchParams } from "./lib/api";
import { rescore } from "./lib/scoring";
import type { FactorKey, Weights } from "./lib/types";
import { FACTOR_ORDER } from "./lib/types";
import { DetailDrawer } from "./components/DetailDrawer";
import { ResultsTable, type Row } from "./components/ResultsTable";
import { WeightsPanel } from "./components/WeightsPanel";
import { EmptyState } from "./components/primitives";

const DEFAULT_WEIGHTS: Weights = {
  succession: 0.28,
  buy_box: 0.24,
  digital_gap: 0.16,
  fragmentation: 0.12,
  contactability: 0.12,
  health: 0.08,
};

export default function App() {
  const [filters, setFilters] = useState<SearchParams>({ limit: 500 });
  const [weights, setWeights] = useState<Weights>(DEFAULT_WEIGHTS);
  const [openId, setOpenId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [preset, setPreset] = useState("generic");

  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: Infinity });
  const search = useQuery({
    queryKey: ["companies", filters],
    queryFn: () => api.search(filters),
    placeholderData: (prev) => prev,
  });

  /* Re-weighting happens here rather than on the server, so moving a slider
     re-sorts in a frame instead of a round trip. The subscores and the decision
     about what was measured are the server's; this is only the arithmetic. */
  const rows: Row[] = useMemo(() => {
    const items = search.data?.results ?? [];
    return items
      .map((item) => ({ item, rescored: rescore(item, weights) }))
      .sort((a, b) => b.rescored.score - a.rescored.score);
  }, [search.data, weights]);

  const open = rows.find((r) => r.item.company.id === openId) ?? null;

  const setWeight = useCallback((key: FactorKey, value: number) => {
    setWeights((w) => ({ ...w, [key]: value }));
  }, []);

  const isDirty = FACTOR_ORDER.some((k) => weights[k] !== DEFAULT_WEIGHTS[k]);

  const toggle = useCallback((id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setChecked((prev) =>
      prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.item.company.id)),
    );
  }, [rows]);

  const exportHref = api.exportUrl(
    checked.size ? [...checked] : rows.map((r) => r.item.company.id),
    preset,
    weights,
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between gap-6 border-b border-[var(--color-rule)] px-5 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[15px] font-semibold tracking-tight">AcquisitionIQ</h1>
          <p className="hidden text-[12px] text-[var(--color-ink-faint)] sm:block">
            Acquisition-fit scoring for search funds
          </p>
        </div>
        <div className="flex items-center gap-4 text-[12px] text-[var(--color-ink-faint)]">
          {meta.data ? (
            <span>
              {meta.data.market.label} · {meta.data.count} companies
            </span>
          ) : null}
          <a
            className="underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
            href={exportHref}
          >
            Export {checked.size ? `${checked.size} selected` : "all"}
          </a>
          <select
            aria-label="CRM column preset"
            value={preset}
            onChange={(e) => setPreset(e.target.value)}
            className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-1.5 py-1 text-[12px]"
          >
            {(meta.data?.crm_presets ?? ["generic"]).map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-64 shrink-0 overflow-y-auto border-r border-[var(--color-rule)] lg:block">
          <section className="border-b border-[var(--color-rule)] p-5">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]">
              Filter
            </h2>
            <div className="mt-3 space-y-3">
              <input
                type="search"
                placeholder="Name, city or trade"
                aria-label="Search companies"
                value={filters.q ?? ""}
                onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value || undefined }))}
                className="w-full rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-[13px] placeholder:text-[var(--color-ink-faint)]"
              />
              <Select
                label="Trade"
                value={filters.industry}
                options={meta.data?.filters.industry ?? []}
                onChange={(v) => setFilters((f) => ({ ...f, industry: v }))}
              />
              <Select
                label="Ownership"
                value={filters.business_type}
                options={meta.data?.filters.business_type ?? []}
                onChange={(v) => setFilters((f) => ({ ...f, business_type: v }))}
              />
              <label className="flex items-center gap-2 text-[12.5px] text-[var(--color-ink-soft)]">
                <input
                  type="checkbox"
                  checked={filters.has_employees ?? false}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, has_employees: e.target.checked || undefined }))
                  }
                  className="size-3.5 accent-[var(--color-accent)]"
                />
                Employs staff
              </label>
              <label className="block text-[12.5px] text-[var(--color-ink-soft)]">
                Trading at least
                <span className="tnum ml-1 font-medium text-[var(--color-ink)]">
                  {filters.min_age ?? 0}
                </span>{" "}
                years
                <input
                  type="range"
                  min={0}
                  max={50}
                  value={filters.min_age ?? 0}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, min_age: Number(e.target.value) || undefined }))
                  }
                  className="mt-1.5 h-1 w-full cursor-pointer appearance-none rounded-full bg-[var(--color-sunken)] accent-[var(--color-accent)]"
                />
              </label>
            </div>
          </section>

          <WeightsPanel
            factors={meta.data?.factors ?? []}
            weights={weights}
            onChange={setWeight}
            onReset={() => setWeights(DEFAULT_WEIGHTS)}
            isDirty={isDirty}
          />
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          {search.isError ? (
            <EmptyState title="Could not load companies">
              {(search.error as Error).message}
            </EmptyState>
          ) : !search.isLoading && rows.length === 0 ? (
            <EmptyState title="No companies match these filters">
              Widen the trade or ownership filter, or lower the minimum trading
              years.
            </EmptyState>
          ) : (
            <ResultsTable
              rows={rows}
              loading={search.isLoading}
              selectedId={openId}
              checked={checked}
              onOpen={setOpenId}
              onToggle={toggle}
              onToggleAll={toggleAll}
            />
          )}
        </main>

        {open ? (
          <aside className="hidden w-[26rem] shrink-0 xl:block">
            <DetailDrawer
              item={open.item}
              rescored={open.rescored}
              onClose={() => setOpenId(null)}
            />
          </aside>
        ) : null}
      </div>
    </div>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | undefined;
  options: string[];
  onChange: (value: string | undefined) => void;
}) {
  return (
    <label className="block text-[12.5px] text-[var(--color-ink-soft)]">
      {label}
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="mt-1 w-full rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-[13px] text-[var(--color-ink)]"
      >
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}
