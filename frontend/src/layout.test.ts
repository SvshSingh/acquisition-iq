import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** A layout invariant, enforced against the source.
 *
 *  Tailwind's `sr-only` is `position: absolute`. An absolutely positioned
 *  element resolves its containing block to the nearest *positioned* ancestor,
 *  so inside a scroll container that is merely `overflow-y-auto` it escapes the
 *  clip entirely and contributes its full height to the document instead.
 *
 *  That is not hypothetical. The six `sr-only` descriptions on the weight
 *  sliders did exactly this: they pinned the document to 831px regardless of
 *  the window, so any viewport shorter than that grew a second full-height
 *  scrollbar down the right edge and a band of dead space below the last row.
 *  Nothing visible was out of place, which is what made it expensive to find —
 *  the page was being sized by text that only a screen reader reads.
 *
 *  A DOM test would not catch the regression, because happy-dom does not do
 *  layout. The rule is structural, so it is checked structurally: a scroll
 *  container must also establish a containing block. */

const SRC = join(__dirname);

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return tsxFiles(full);
    return entry.endsWith(".tsx") ? [full] : [];
  });
}

/** Pull out every `className="..."` / `` className={`...`} `` literal, with
 *  any `${...}` interpolation removed.
 *
 *  Dropping the interpolations is the point, not a shortcut. The sidebar's
 *  conditional branch contains `fixed` for the mobile overlay, and reading that
 *  as "this container is positioned" is exactly the false pass that let the
 *  original bug through — at desktop widths that branch is `hidden` and the
 *  element resolves to `lg:static`. Only classes that apply unconditionally
 *  count. */
function classAttributes(source: string): string[] {
  const out: string[] = [];
  const re = /className=(?:"([^"]*)"|\{`([\s\S]*?)`\})/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(source)) !== null) {
    const raw = match[1] ?? match[2] ?? "";
    out.push(raw.replace(/\$\{[\s\S]*?\}/g, " "));
  }
  return out;
}

describe("scroll containers establish a containing block", () => {
  const files = tsxFiles(SRC);

  it("finds the components it means to check", () => {
    expect(files.length).toBeGreaterThan(3);
  });

  it.each(files.map((f) => [f.slice(SRC.length + 1), f] as const))(
    "%s",
    (_name, file) => {
      const source = readFileSync(file, "utf8");
      for (const className of classAttributes(source)) {
        const scrolls = /\boverflow-(y-|x-)?(auto|scroll)\b/.test(className);
        if (!scrolls) continue;
        // `fixed`, `absolute` and `sticky` are positioned already; `relative`
        // is the usual way to say it without moving anything.
        const positioned = /\b(relative|absolute|fixed|sticky)\b/.test(className);
        expect(
          positioned,
          `A scroll container in ${file} is missing a containing block, so an ` +
            `absolutely positioned descendant (any \`sr-only\` text, a tooltip) ` +
            `would escape its clip and stretch the document:\n  ${className}`,
        ).toBe(true);
      }
    },
  );
});

describe("the app shell is pinned to the viewport", () => {
  it("keeps the document itself from scrolling", () => {
    // The counterpart to the rule above: only the inner regions scroll, so the
    // page never grows a second scrollbar of its own.
    const css = readFileSync(join(SRC, "index.css"), "utf8");
    expect(css).toMatch(/html,\s*\n?\s*body,\s*\n?\s*#root\s*\{[^}]*height:\s*100%/);
  });
});
