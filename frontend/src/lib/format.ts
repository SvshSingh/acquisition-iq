/** Licence registers store business names in upper case, because they are
 *  transcribed from paper forms. Rendering them that way makes a screen of
 *  results read as shouting and measurably harder to scan, so they are cased
 *  for display only — the underlying record is untouched, and the licence
 *  number remains the identity.
 *
 *  Acronyms are the reason this is not a one-liner. Naive title casing turns
 *  "ABC HVAC INC" into "Abc Hvac Inc", which is worse than leaving it alone:
 *  the trade is the most informative token in the name. So known acronyms
 *  survive, and legal suffixes are cased normally. */

const KEEP_UPPER = new Set([
  "HVAC",
  "AC",
  "USA",
  "US",
  "LA",
  "CA",
  "TV",
  "AAA",
  "ABC",
  "HD",
  "PVC",
  "LED",
  "AV",
  "IT",
  "RV",
  "CNC",
  "GC",
  "JR",
  "SR",
  "II",
  "III",
  "IV",
]);

const FORCE_CASE: Record<string, string> = {
  INC: "Inc",
  LLC: "LLC",
  LLP: "LLP",
  LTD: "Ltd",
  CO: "Co",
  CORP: "Corp",
  DBA: "dba",
  AND: "and",
  OF: "of",
  THE: "the",
};

export function displayName(raw: string): string {
  const words = raw.trim().split(/\s+/);
  return words
    .map((word, index) => {
      const bare = word.replace(/[^A-Za-z]/g, "");
      const upper = bare.toUpperCase();

      if (KEEP_UPPER.has(upper)) return word.toUpperCase();

      const forced = FORCE_CASE[upper];
      // Lowercase joining words read wrong as the first token of a name.
      if (forced) return index === 0 ? capitalise(forced) : forced;

      // A single letter is an initial — "A K INTERNATIONAL" keeps its shape.
      if (bare.length === 1) return word.toUpperCase();

      return capitalise(word.toLowerCase());
    })
    .join(" ");
}

function capitalise(word: string): string {
  let out = word.replace(/^([a-z])/, (letter) => letter.toUpperCase());

  // A hyphen or slash always starts a new word: Smith-Jones, Heating/Cooling.
  out = out.replace(
    /([\-/])([a-z])/g,
    (_, sep: string, letter: string) => sep + letter.toUpperCase(),
  );

  // An apostrophe only starts a new word in a name prefix — O'Brien, D'Angelo —
  // which is why the rule requires a single letter before it. Capitalising
  // after every apostrophe turns "Johnny's" into "Johnny'S", and possessives
  // are far more common in trade names than Irish surnames.
  out = out.replace(
    /^([A-Za-z]['’])([a-z])/,
    (_, prefix: string, letter: string) => prefix + letter.toUpperCase(),
  );

  return out;
}
