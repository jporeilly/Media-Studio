// @vitest-environment jsdom
/**
 * Our half of the content manager's renderer contract for the figure Export to course writes (W2c, C3): the figure is
 * the golden file the backend's own test pins byte for byte (tests/fixtures/course_export/figure.golden.md), read here
 * as it is, parsed as HTML and walked in the shape the content manager documents for `<figure class="pcm-annotated">`
 * (its AnnotatedImage: the `<img>`'s src and alt, each `<li>`'s data-x / data-y, the `<figcaption>`) - one hotspot at
 * the percent the backend computed, the picture, the text unescaped, the caption, and no blank line inside the
 * figure's HTML (it would end CommonMark's HTML block).
 *
 * The other half - what the content manager's REAL `MarkdownBody` makes of it - is `courseFigure.pcm.test.ts`.
 */
import { describe, expect, it } from "vitest";

// Node's own modules (the golden file lives with the backend's tests, outside what Vite serves): imported by name
// when the test runs, so they come in untyped - this frontend is a browser bundle and carries no Node typings.
const nodeModule = (name: string): Promise<unknown> => import(/* @vite-ignore */ `node:${name}`);
const { readFileSync } = (await nodeModule("fs")) as { readFileSync(path: string, encoding: "utf8"): string };
const { join, resolve } = (await nodeModule("path")) as { join(...parts: string[]): string; resolve(...parts: string[]): string };
const { fileURLToPath } = (await nodeModule("url")) as { fileURLToPath(url: string): string };
const REPO = resolve(fileURLToPath(import.meta.url), "..", "..", "..", "..");
const GOLDEN = readFileSync(join(REPO, "tests", "fixtures", "course_export", "figure.golden.md"), "utf8").replace(/\r\n/g, "\n");

/** The pin the backend computed for the golden step: (100 + 160 / 2) / 1600 and (50 + 60 / 2) / 900, one decimal. */
const PIN = { x: 11.3, y: 8.9 };
const TEXT = "Click \"Run\" & <wait> for O'Brien's [FIELD] rows.";

describe("the exported figure's markup", () => {
  it("is one annotated figure: the picture, one hotspot at the computed percent, the caption", () => {
    const doc = new DOMParser().parseFromString(GOLDEN, "text/html");
    const figures = doc.querySelectorAll("figure");
    expect(figures.length).toBe(1);
    const figure = figures[0];
    expect([...figure.classList]).toEqual(["pcm-annotated"]);
    // The documented shape: an img's src and alt, a figcaption's text, each li's data-x / data-y.
    let imgSrc = "", imgAlt = "", caption = "";
    const hotspots: { x: number; y: number; text: string }[] = [];
    const walk = (el: Element) => {
      const t = el.tagName.toLowerCase();
      if (t === "img") { imgSrc = el.getAttribute("src") ?? ""; imgAlt = el.getAttribute("alt") ?? ""; }
      else if (t === "figcaption") caption = el.textContent ?? "";
      else if (t === "li") hotspots.push({ x: parseFloat(el.getAttribute("data-x") ?? "") || 0, y: parseFloat(el.getAttribute("data-y") ?? "") || 0, text: el.textContent ?? "" });
      else for (const child of el.children) walk(child);
    };
    for (const child of figure.children) walk(child);
    expect(hotspots).toEqual([{ ...PIN, text: TEXT }]);
    expect(imgSrc).toBe("../_assets/images/wt-01-first-aaaaaaaaaaaa-bbbbbbbbbbbb.png");
    expect(imgAlt).toBe(TEXT);
    expect(caption).toBe("3. Run it & check");
    expect(figure.parentElement?.getAttribute("align")).toBe("center");
  });

  it("holds no blank line inside its HTML (CommonMark would end the HTML block there) and sits between its markers", () => {
    const lines = GOLDEN.split("\n");
    const open = lines.indexOf('<div align="center">'), close = lines.indexOf("</div>");
    expect(open).toBeGreaterThan(0);
    expect(lines.slice(open, close + 1).every((l) => l.trim() !== "")).toBe(true);
    expect(lines[0]).toMatch(/^<!-- media-studio: walkthrough [0-9a-f]{12} step [0-9a-f]{12} -->$/);
    expect(lines[lines.length - 2]).toBe("<!-- /media-studio -->");
  });
});
