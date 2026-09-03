import { describe, expect, it } from "vitest";
import { displayName } from "./format";

/** Casing is cosmetic until it destroys information. CSLB stores names in
 *  upper case, and the naive fix — title case everything — turns the trade,
 *  which is the single most useful token in the name, into "Hvac". These cases
 *  are drawn from names that actually appear in the Glendale extract. */
describe("displayName", () => {
  it("cases an ordinary shouted licence name", () => {
    expect(displayName("WHITAKER HEATING AND COOLING")).toBe("Whitaker Heating and Cooling");
  });

  it("keeps trade acronyms upper case", () => {
    // The whole reason this is not `toLowerCase().replace(...)`.
    expect(displayName("ABC HVAC INC")).toBe("ABC HVAC Inc");
    expect(displayName("PVC PIPE AND SUPPLY CO")).toBe("PVC Pipe and Supply Co");
  });

  it("cases legal suffixes the way they are written", () => {
    expect(displayName("CORNER PLUMBING LLC")).toBe("Corner Plumbing LLC");
    expect(displayName("SUMMIT BUILDERS CORP")).toBe("Summit Builders Corp");
  });

  it("does not start a name with a lowercase joining word", () => {
    // "the" is lowercase mid-name but capitalised when it leads.
    expect(displayName("THE PAINT SHOP")).toBe("The Paint Shop");
    expect(displayName("HOUSE OF TILE")).toBe("House of Tile");
  });

  it("keeps single letters as initials", () => {
    expect(displayName("A K INTERNATIONAL")).toBe("A K International");
  });

  it("capitalises after a hyphen or slash", () => {
    expect(displayName("SMITH-JONES CONSTRUCTION")).toBe("Smith-Jones Construction");
    expect(displayName("HEATING/COOLING EXPERTS")).toBe("Heating/Cooling Experts");
  });

  it("handles apostrophes without breaking possessives", () => {
    // A name prefix takes a capital; a possessive must not.
    expect(displayName("O'BRIEN ELECTRIC")).toBe("O'Brien Electric");
    expect(displayName("JOHNNY'S ROOFING")).toBe("Johnny's Roofing");
  });

  it("is stable on already-cased and irregular input", () => {
    expect(displayName("Corner Plumbing LLC")).toBe("Corner Plumbing LLC");
    expect(displayName("  DOUBLE   SPACED  NAME ")).toBe("Double Spaced Name");
    expect(displayName("")).toBe("");
  });
});
