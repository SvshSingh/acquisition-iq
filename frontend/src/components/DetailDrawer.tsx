import { useEffect, useRef } from "react";
import { displayName } from "../lib/format";
import type { Rescored } from "../lib/scoring";
import type { FactorResult, ScoredCompany } from "../lib/types";
import {
  ConfidenceBadge,
  ContributionBar,
  CoverageMeter,
  ScoreNumber,
  factorTint,
} from "./primitives";

/** "Why did this score 72?"
 *
 *  This panel is the product. Anyone can put a number next to a company; the
 *  claim being made here is that a searcher can interrogate it — see which
 *  factors moved it, read the sentence the evidence came from, click through to
 *  the source, and see what the engine looked for and could not find.
 *
 *  The missing-signals list is not an apology. A user deciding where to spend a
 *  week of diligence is better served by "we could not establish headcount"
 *  than by a confident number quietly built on a default. */
export function DetailDrawer({
  item,
  rescored,
  onClose,
  onRefresh,
  refreshing,
  refreshError,
}: {
  item: ScoredCompany | null;
  rescored: Rescored | null;
  onClose: () => void;
  onRefresh?: (id: string) => void;
  refreshing?: boolean;
  refreshError?: string | null;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!item) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  if (!item || !rescored) return null;

  const { company, score } = item;
  const contact = company.contacts[0];
  const ordered = [...score.factors].sort(
    (a, b) => (rescored.contributions[b.key] ?? 0) - (rescored.contributions[a.key] ?? 0),
  );

  return (
    <div
      ref={panelRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="false"
      aria-label={`Score breakdown for ${displayName(company.name)}`}
      className="flex h-full min-h-0 w-full flex-col border-l border-[var(--color-rule)] bg-[var(--color-paper)]"
    >
      <header className="border-b border-[var(--color-rule)] px-6 py-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold leading-tight tracking-tight text-[var(--color-ink)]">
              {displayName(company.name)}
            </h2>
            <p className="mt-1 text-[12.5px] text-[var(--color-ink-soft)]">
              {[company.industry, company.city, company.state].filter(Boolean).join(" · ")}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close breakdown"
            className="-mr-2 -mt-1 rounded p-2 text-[var(--color-ink-faint)] hover:bg-[var(--color-surface)] hover:text-[var(--color-ink)]"
          >
            <svg viewBox="0 0 16 16" className="size-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="mt-5 flex items-end justify-between gap-4">
          <div className="flex items-baseline gap-3">
            <ScoreNumber value={rescored.score} size="lg" />
            <span className="text-[12px] text-[var(--color-ink-faint)]">/ 100</span>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <ConfidenceBadge value={rescored.confidence} />
            <CoverageMeter value={rescored.coveredWeight} />
          </div>
        </div>
        <div className="mt-3">
          <ContributionBar contributions={rescored.contributions} height={8} />
        </div>

        {onRefresh ? (
          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              onClick={() => onRefresh(company.id)}
              disabled={refreshing}
              className="rounded border border-[var(--color-rule-strong)] px-2.5 py-1 text-[12px] font-medium text-[var(--color-ink)] hover:bg-[var(--color-surface)] disabled:cursor-wait disabled:opacity-60"
            >
              {refreshing ? "Re-checking source…" : "Refresh from source"}
            </button>
            <span className="text-[11px] text-[var(--color-ink-faint)]">
              {company.data_quality !== null && company.data_quality !== undefined
                ? `Record ${Math.round(company.data_quality)}% complete`
                : null}
            </span>
          </div>
        ) : null}

        {refreshError ? (
          <p className="mt-2 text-[11.5px] text-[var(--color-ink-soft)]">
            Refresh failed: {refreshError}
          </p>
        ) : null}

        {company.quality_issues?.length ? (
          <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--color-ink-faint)]">
            <span className="font-medium">Record gaps:</span>{" "}
            {company.quality_issues.slice(0, 4).join(", ")}.
          </p>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <FiledFacts company={company} />

        <section className="px-6 py-5">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]">
            How the score was built
          </h3>
          <div className="mt-4 space-y-5">
            {ordered.map((factor) => (
              <FactorBlock
                key={factor.key}
                factor={factor}
                points={rescored.contributions[factor.key] ?? 0}
                weight={rescored.effective[factor.key] ?? 0}
              />
            ))}
          </div>
        </section>

        {contact ? (
          <section className="border-t border-[var(--color-rule)] px-6 py-5">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]">
              Reaching them
            </h3>
            <dl className="mt-3 space-y-2 text-[13px]">
              {contact.name ? (
                <Row label={contact.title ?? "Contact"}>{contact.name}</Row>
              ) : null}
              {contact.phone ? (
                <Row label="Phone">
                  <a className="underline decoration-dotted underline-offset-2" href={`tel:${contact.phone}`}>
                    {contact.phone}
                  </a>
                  {contact.phone_valid ? (
                    <span className="ml-2 text-[11px] text-[var(--color-ink-faint)]">validated</span>
                  ) : null}
                </Row>
              ) : null}
              {contact.email ? (
                <Row label="Email">
                  <a className="underline decoration-dotted underline-offset-2" href={`mailto:${contact.email}`}>
                    {contact.email}
                  </a>
                  <span className="ml-2 text-[11px] text-[var(--color-ink-faint)]">
                    {contact.email_status}
                  </span>
                </Row>
              ) : null}
            </dl>
          </section>
        ) : null}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-28 shrink-0 text-[var(--color-ink-faint)]">{label}</dt>
      <dd className="min-w-0 text-[var(--color-ink)]">{children}</dd>
    </div>
  );
}

/** The filed record, stated plainly and separately from the score.
 *
 *  These are facts from a licensing register rather than inferences, and
 *  showing them apart from the scoring narrative lets a user check the inputs
 *  before deciding whether they believe the output. */
function FiledFacts({ company }: { company: ScoredCompany["company"] }) {
  const facts: Array<[string, React.ReactNode]> = [];
  if (company.business_type) facts.push(["Ownership form", company.business_type]);
  if (company.licence_issued)
    facts.push([
      "Licensed since",
      <>
        {company.licence_issued.slice(0, 4)}
        <span className="ml-2 text-[11px] text-[var(--color-ink-faint)]">
          floor on age, not founding year
        </span>
      </>,
    ]);
  if (company.has_employees !== null)
    facts.push([
      "Employees",
      company.has_employees ? "Carries workers' comp cover" : "Exempt — owner only",
    ]);
  if (company.licence_classifications.length)
    facts.push(["Classifications", company.licence_classifications.join(", ")]);
  if (company.peer_count_in_niche !== null)
    facts.push(["Peers nearby", `${company.peer_count_in_niche} in the same trade`]);
  if (!facts.length) return null;

  return (
    <section className="border-b border-[var(--color-rule)] bg-[var(--color-surface)] px-6 py-5">
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-soft)]">
        On the public record
      </h3>
      <dl className="mt-3 space-y-2 text-[13px]">
        {facts.map(([label, value]) => (
          <Row key={label} label={label}>
            {value}
          </Row>
        ))}
      </dl>
      {company.source_url ? (
        <a
          href={company.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-3 inline-block text-[12px] text-[var(--color-accent-ink)] underline decoration-dotted underline-offset-2"
        >
          Verify on the licensing board →
        </a>
      ) : null}

      {company.website ? <WebsiteProvenance company={company} /> : null}
    </section>
  );
}

/** Where the website came from, and how sure we are.
 *
 *  No source publishes a URL for a licensed contractor, so most of these were
 *  derived from the business name and then proved against the page itself.
 *  Presenting an inferred link with the same confidence as a published one
 *  would be the quiet dishonesty this product exists to avoid, so the proof is
 *  shown rather than implied. */
function WebsiteProvenance({ company }: { company: ScoredCompany["company"] }) {
  const inferred = company.website_source?.startsWith("inferred");
  const conclusive = company.website_source === "inferred:phone";

  return (
    <div className="mt-4 border-t border-[var(--color-rule)] pt-3">
      <a
        href={company.website ?? undefined}
        target="_blank"
        rel="noreferrer noopener"
        className="block truncate text-[12.5px] text-[var(--color-accent-ink)] underline decoration-dotted underline-offset-2"
      >
        {company.website?.replace(/^https?:\/\//, "")}
      </a>
      {inferred ? (
        <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--color-ink-faint)]">
          <span className="font-medium">
            {conclusive ? "Matched by phone number" : "Matched by name and city"}
          </span>
          {company.website_evidence ? ` — ${company.website_evidence}` : null}
        </p>
      ) : null}
    </div>
  );
}

function FactorBlock({
  factor,
  points,
  weight,
}: {
  factor: FactorResult;
  points: number;
  weight: number;
}) {
  const dropped = !factor.measured;
  return (
    <article className={dropped ? "opacity-60" : undefined}>
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="flex items-center gap-2 text-[13px] font-medium text-[var(--color-ink)]">
          <span
            aria-hidden
            className="size-2 shrink-0 rounded-[2px]"
            style={{ background: factorTint(factor.key) }}
          />
          {factor.label}
        </h4>
        <div className="tnum shrink-0 text-[12px] text-[var(--color-ink-soft)]">
          {dropped ? (
            <span className="text-[var(--color-ink-faint)]">not scored</span>
          ) : (
            <>
              {factor.score.toFixed(0)}
              <span className="text-[var(--color-ink-faint)]">
                {" "}
                × {Math.round(weight * 100)}% = {points.toFixed(1)}
              </span>
            </>
          )}
        </div>
      </div>

      {dropped ? (
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--color-ink-faint)]">
          Nothing was observed for this factor, so it contributed nothing and its
          weight moved to the factors that were measured.
        </p>
      ) : null}

      {factor.evidence.length ? (
        <ul className="mt-2 space-y-2">
          {factor.evidence.map((e, i) => (
            <li key={i} className="border-l-2 border-[var(--color-rule)] pl-3">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[12.5px] font-medium text-[var(--color-ink)]">{e.label}</span>
                <span
                  className={`tnum shrink-0 text-[11px] ${
                    e.impact >= 0 ? "text-[var(--color-ink-soft)]" : "text-[var(--color-ink-faint)]"
                  }`}
                >
                  {e.impact >= 0 ? "+" : ""}
                  {e.impact.toFixed(0)}
                </span>
              </div>
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-ink-soft)]">
                {e.detail}
              </p>
              {e.source_url ? (
                <a
                  href={e.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="mt-1 inline-block text-[11.5px] text-[var(--color-accent-ink)] underline decoration-dotted underline-offset-2"
                >
                  Source →
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {factor.missing_signals.length ? (
        <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-ink-faint)]">
          <span className="font-medium">Looked for, not found:</span>{" "}
          {factor.missing_signals.join(", ")}.
        </p>
      ) : null}
    </article>
  );
}
