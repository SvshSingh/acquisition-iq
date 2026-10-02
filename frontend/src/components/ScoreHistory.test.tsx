import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { ScoreHistoryEntry } from "../lib/types";
import { ScoreHistory } from "./ScoreHistory";

function entry(score: number, scoredAt: string): ScoreHistoryEntry {
  return { score, confidence: "medium", engine_version: "1.3.0", scored_at: scoredAt };
}

function renderWith(history: ScoreHistoryEntry[] | Error) {
  const spy = vi.spyOn(api, "history");
  if (history instanceof Error) spy.mockRejectedValue(history);
  else spy.mockResolvedValue({ company_id: "c1", history });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ScoreHistory companyId="c1" refreshedAt={null} />
    </QueryClientProvider>,
  );
  return spy;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ScoreHistory", () => {
  it("shows each recorded score, newest first, with how far it moved", async () => {
    renderWith([
      entry(72.7, "2026-10-02T10:00:00+00:00"),
      entry(68.2, "2026-09-20T10:00:00+00:00"),
      entry(70.0, "2026-09-10T10:00:00+00:00"),
    ]);

    await screen.findByText("Score over time");
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("72.7");
    expect(rows[0].textContent).toContain("+4.5");
    // A fall uses a real minus sign, and the oldest entry has nothing to compare to.
    expect(rows[1].textContent).toContain("−1.8");
    expect(rows[2].textContent).toContain("70.0");
    expect(rows[2].textContent).not.toMatch(/[+−]/);
  });

  it("renders nothing for a single entry, which is only the current score restated", async () => {
    const spy = renderWith([entry(72.7, "2026-10-02T10:00:00+00:00")]);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByText("Score over time")).toBeNull();
  });

  it("renders nothing when the server keeps no history", async () => {
    const spy = renderWith([]);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByText("Score over time")).toBeNull();
  });

  it("stays out of the way when the company is not in the store", async () => {
    // An imported row has no server-side record, so the request 404s. That is
    // not an error worth showing in a panel about something else.
    const spy = renderWith(new Error("no company with id 'c1'"));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByText("Score over time")).toBeNull();
    expect(screen.queryByText(/no company/)).toBeNull();
  });

  it("does not show a change too small to survive rounding", async () => {
    renderWith([
      entry(70.02, "2026-10-02T10:00:00+00:00"),
      entry(70.0, "2026-09-20T10:00:00+00:00"),
    ]);
    await screen.findByText("Score over time");
    expect(screen.getAllByRole("listitem")[0].textContent).not.toMatch(/[+−]/);
  });
});
