import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type SearchParams, type UploadResult } from "./lib/api";
import { rescore } from "./lib/scoring";
import type { FactorKey, ScoredCompany, Weights } from "./lib/types";
import { FACTOR_ORDER } from "./lib/types";
import { DetailDrawer } from "./components/DetailDrawer";
import {
  ResultsTable,
  type Row,
  type SortKey,
  type SortState,
} from "./components/ResultsTable";
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
  // Below the `lg` breakpoint the filter and weights column has nowhere to sit
  // beside the table, so it becomes a sheet the user opens. Hiding it outright
  // would remove the thesis controls entirely, which are half the product.
  const [panelOpen, setPanelOpen] = useState(false);
  // Refreshed companies overlay the snapshot rather than replacing it. A
  // refresh answers "what does this look like right now"; the shipped dataset
  // stays exactly as collected so two people see the same baseline.
  const [refreshed, setRefreshed] = useState<Record<string, ScoredCompany>>({});
  // An imported list, when present, takes over the table — the same scoring,
  // table and drawer, just fed from the user's own leads instead of the seed.
  const [imported, setImported] = useState<UploadResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [sort, setSort] = useState<SortState>({ key: "score", dir: "desc" });

  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: Infinity });
  const search = useQuery({
    queryKey: ["companies", filters],
    queryFn: () => api.search(filters),
    placeholderData: (prev) => prev,
  });

  const refresh = useMutation({
    mutationFn: api.refresh,
    onSuccess: (updated) =>
      setRefreshed((prev) => ({ ...prev, [updated.company.id]: updated })),
  });

  const upload = useMutation({
    mutationFn: api.scoreUpload,
    onSuccess: (result) => {
      setImported(result);
      setOpenId(null);
      setChecked(new Set());
    },
  });

  /* Re-weighting happens here rather than on the server, so moving a slider
     re-sorts in a frame instead of a round trip. The subscores and the decision
     about what was measured are the server's; this is only the arithmetic. */
  const rows: Row[] = useMemo(() => {
    const items = imported ? imported.results : (search.data?.results ?? []);
    const scored = items.map((item) => {
      const current = refreshed[item.company.id] ?? item;
      return { item: current, rescored: rescore(current, weights) };
    });

    const direction = sort.dir === "asc" ? 1 : -1;
    return scored.sort((a, b) => {
      if (sort.key === "name") {
        // localeCompare so accented names sort where a reader expects, and
        // numeric so "Shop 2" precedes "Shop 10".
        return (
          direction *
          a.item.company.name.localeCompare(b.item.company.name, undefined, { numeric: true })
        );
      }
      const left = sort.key === "coverage" ? a.rescored.coveredWeight : a.rescored.score;
      const right = sort.key === "coverage" ? b.rescored.coveredWeight : b.rescored.score;
      // Ties fall back to score, so re-sorting by a coarse column does not
      // scramble rows that share a value.
      return direction * (left - right) || b.rescored.score - a.rescored.score;
    });
  }, [imported, search.data, weights, refreshed, sort]);

  const onSort = useCallback((key: SortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : // A new column starts in the direction that is useful first: highest
          // score, but names A–Z.
          { key, dir: key === "name" ? "asc" : "desc" },
    );
  }, []);

  function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = ""; // let the same file be re-picked after a clear
  }

  const open = rows.find((r) => r.item.company.id === openId) ?? null;

  const setWeight = useCallback((key: FactorKey, value: number) => {
    setWeights((w) => ({ ...w, [key]: value }));
  }, []);

  const isDirty = FACTOR_ORDER.some((k) => weights[k] !== DEFAULT_WEIGHTS[k]);

  // `limit` is plumbing, not a user choice, so it must not count as an active
  // filter — otherwise the panel always claims one is set.
  const activeFilterCount = (Object.keys(filters) as (keyof SearchParams)[]).filter(
    (k) => k !== "limit" && filters[k] !== undefined && filters[k] !== "",
  ).length;

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
          <button
            type="button"
            onClick={() => setPanelOpen(true)}
            aria-label="Open filters and thesis weights"
            className="-ml-1 rounded p-1.5 text-[var(--color-ink-soft)] hover:bg-[var(--color-surface)] hover:text-[var(--color-ink)] lg:hidden"
          >
            <svg viewBox="0 0 16 16" className="size-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M2 4h12M2 8h12M2 12h8" strokeLinecap="round" />
            </svg>
          </button>
          <h1 className="text-[15px] font-semibold tracking-tight">AcquisitionIQ</h1>
          <p className="hidden text-[12px] text-[var(--color-ink-faint)] sm:block">
            Acquisition-fit scoring for search funds
          </p>
        </div>
        <div className="flex items-center gap-4 text-[12px] text-[var(--color-ink-faint)]">
          {meta.data && !imported ? (
            <span className="hidden md:inline">
              {meta.data.market.label} · {meta.data.count} companies
            </span>
          ) : null}
          <input
            ref={fileInput}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={onPickFile}
          />
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={upload.isPending}
            className="rounded border border-[var(--color-rule-strong)] px-2 py-1 font-medium text-[var(--color-ink)] hover:bg-[var(--color-surface)] disabled:cursor-wait disabled:opacity-60"
            title="Score your own lead list — a CSV from SaaSquatch, a CRM, or a broker sheet"
          >
            {upload.isPending ? "Scoring…" : "Import CSV"}
          </button>
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
        {panelOpen ? (
          <button
            type="button"
            aria-label="Close filters"
            onClick={() => setPanelOpen(false)}
            className="fixed inset-0 z-40 cursor-default bg-black/40 lg:hidden"
          />
        ) : null}

        <aside
          className={`${
            panelOpen ? "fixed inset-y-0 left-0 z-50 w-72 shadow-2xl" : "hidden"
          } shrink-0 overflow-y-auto border-r border-[var(--color-rule)] bg-[var(--color-paper)] lg:static lg:z-auto lg:block lg:w-64 lg:shadow-none`}
        >
          <section className="border-b border-[var(--color-rule)] p-5">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]">
                Filter
                {activeFilterCount ? (
                  <span className="ml-1.5 font-normal text-[var(--color-ink-faint)]">
                    ({activeFilterCount})
                  </span>
                ) : null}
              </h2>
              {activeFilterCount ? (
                <button
                  type="button"
                  onClick={() => setFilters({ limit: 500 })}
                  className="text-[11px] text-[var(--color-ink-faint)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
                >
                  Clear all
                </button>
              ) : null}
            </div>
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
          {imported ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--color-rule)] bg-[var(--color-accent-soft)] px-4 py-2 text-[12px]">
              <span className="font-medium text-[var(--color-ink)]">
                Scoring {imported.total} companies from {imported.source}
              </span>
              <span className="text-[var(--color-ink-soft)]">
                mapped {imported.fields_present.length} field
                {imported.fields_present.length === 1 ? "" : "s"}
                {imported.unmapped_columns.length
                  ? ` · ignored ${imported.unmapped_columns.length} unrecognised column${imported.unmapped_columns.length === 1 ? "" : "s"}`
                  : ""}
                {imported.skipped_rows ? ` · skipped ${imported.skipped_rows} empty rows` : ""}
              </span>
              <button
                type="button"
                onClick={() => {
                  setImported(null);
                  setOpenId(null);
                  setChecked(new Set());
                }}
                className="ml-auto underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
              >
                Back to {meta.data?.market.label ?? "the seed market"}
              </button>
            </div>
          ) : null}

          {upload.isError ? (
            <div className="border-b border-[var(--color-rule)] px-4 py-2 text-[12px] text-[var(--color-ink-soft)]">
              Import failed: {(upload.error as Error).message}
            </div>
          ) : null}

          {!imported && search.isError ? (
            <EmptyState title="Could not load companies">
              {(search.error as Error).message}
            </EmptyState>
          ) : !imported && !search.isLoading && rows.length === 0 ? (
            <EmptyState title="No companies match these filters">
              Widen the trade or ownership filter, or lower the minimum trading
              years.
            </EmptyState>
          ) : (
            <ResultsTable
              rows={rows}
              loading={search.isLoading && !imported}
              selectedId={openId}
              checked={checked}
              onOpen={setOpenId}
              onToggle={toggle}
              onToggleAll={toggleAll}
              sort={sort}
              onSort={onSort}
            />
          )}
        </main>

        {/* The breakdown is the product, so it must open at every width. It sits
            beside the table from `xl` and slides over it below that — the
            previous version was `hidden xl:block`, which meant a click on a row
            in a smaller window did nothing at all and the app looked broken. */}
        {open ? (
          <>
            <button
              type="button"
              aria-label="Close breakdown"
              onClick={() => setOpenId(null)}
              className="fixed inset-0 z-40 cursor-default bg-black/40 xl:hidden"
            />
            <aside className="fixed inset-y-0 right-0 z-50 w-full max-w-[26rem] shadow-2xl xl:static xl:z-auto xl:w-[26rem] xl:max-w-none xl:shrink-0 xl:shadow-none">
              <DetailDrawer
                item={open.item}
                rescored={open.rescored}
                onClose={() => setOpenId(null)}
                onRefresh={(id) => refresh.mutate(id)}
                refreshing={refresh.isPending && refresh.variables === open.item.company.id}
                refreshError={refresh.isError ? (refresh.error as Error).message : null}
              />
            </aside>
          </>
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
