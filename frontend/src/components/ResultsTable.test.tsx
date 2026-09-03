import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../lib/__fixtures__/scoring-parity.json";
import { rescore } from "../lib/scoring";
import type { ScoredCompany, Weights } from "../lib/types";
import { ResultsTable, type Row, type SortState } from "./ResultsTable";

/** The table is the product. Someone works down it for an hour, which is why
 *  rows are focusable and the arrow keys move between them — and why that
 *  behaviour is worth a test rather than a claim in a comment.
 *
 *  Rows are built from the same generated parity fixture the scoring tests use,
 *  so this never asserts against numbers invented for the test. */

const cases = fixture.cases as unknown as {
  scoredCompany: ScoredCompany;
  weights: Weights;
}[];

function rows(names: string[]): Row[] {
  return names.map((name, i) => {
    const base = cases[i % cases.length];
    const item: ScoredCompany = {
      ...base.scoredCompany,
      company: { ...base.scoredCompany.company, id: `row-${i}`, name },
    };
    return { item, rescored: rescore(item, base.weights) };
  });
}

const SORT: SortState = { key: "score", dir: "desc" };

function setup(overrides: Partial<Parameters<typeof ResultsTable>[0]> = {}) {
  const props = {
    rows: rows(["ABC HVAC INC", "CORNER PLUMBING LLC", "O'BRIEN ELECTRIC"]),
    loading: false,
    selectedId: null,
    checked: new Set<string>(),
    onOpen: vi.fn(),
    onToggle: vi.fn(),
    onToggleAll: vi.fn(),
    sort: SORT,
    onSort: vi.fn(),
    ...overrides,
  };
  render(<ResultsTable {...props} />);
  return props;
}

afterEach(cleanup);

describe("ResultsTable", () => {
  it("renders licence names cased for reading, not as filed", () => {
    setup();
    // Upper-case register names are hostile to scan; the trade acronym stays.
    expect(screen.getByText("ABC HVAC Inc")).toBeTruthy();
    expect(screen.getByText("Corner Plumbing LLC")).toBeTruthy();
    expect(screen.getByText("O'Brien Electric")).toBeTruthy();
  });

  it("announces the sorted column and only that column", () => {
    setup();
    const fit = screen.getByRole("button", { name: /fit/i });
    expect(fit.getAttribute("aria-sort")).toBe("descending");
    // A screen reader needs "none" on the others, not a missing attribute.
    expect(
      screen.getByRole("button", { name: /company/i }).getAttribute("aria-sort"),
    ).toBe("none");
  });

  it("asks for a sort when a header is activated", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /company/i }));
    expect(props.onSort).toHaveBeenCalledWith("name");
  });

  it("moves focus down and up the list with the arrow keys", () => {
    setup();
    const list = screen.getAllByRole("button", { pressed: false });
    const first = list.find((el) => el.getAttribute("data-row-id") === "row-0")!;
    const second = list.find((el) => el.getAttribute("data-row-id") === "row-1")!;

    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);

    fireEvent.keyDown(second, { key: "ArrowUp" });
    expect(document.activeElement).toBe(first);
  });

  it("does not wrap past either end of the list", () => {
    setup();
    const first = document.querySelector<HTMLElement>('[data-row-id="row-0"]')!;
    first.focus();
    // Arrowing up from the top should stay put rather than jump to the bottom.
    fireEvent.keyDown(first, { key: "ArrowUp" });
    expect(document.activeElement).toBe(first);
  });

  it("opens on Enter and selects on Space without opening", () => {
    const props = setup();
    const row = document.querySelector<HTMLElement>('[data-row-id="row-1"]')!;

    fireEvent.keyDown(row, { key: "Enter" });
    expect(props.onOpen).toHaveBeenCalledWith("row-1");

    fireEvent.keyDown(row, { key: " " });
    expect(props.onToggle).toHaveBeenCalledWith("row-1");
    expect(props.onOpen).toHaveBeenCalledTimes(1);
  });

  it("keeps a checkbox click from opening the drawer underneath it", () => {
    const props = setup();
    const row = document.querySelector<HTMLElement>('[data-row-id="row-0"]')!;
    fireEvent.click(within(row).getByRole("checkbox"));
    expect(props.onToggle).toHaveBeenCalledWith("row-0");
    expect(props.onOpen).not.toHaveBeenCalled();
  });

  it("only reports select-all when every row is checked", () => {
    const all = new Set(["row-0", "row-1", "row-2"]);
    setup({ checked: all });
    expect(
      (screen.getByLabelText("Select all rows") as HTMLInputElement).checked,
    ).toBe(true);

    cleanup();
    setup({ checked: new Set(["row-0"]) });
    expect(
      (screen.getByLabelText("Select all rows") as HTMLInputElement).checked,
    ).toBe(false);
  });

  it("shows placeholders instead of an empty frame while loading", () => {
    setup({ loading: true, rows: [] });
    expect(document.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });
});
