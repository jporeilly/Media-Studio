import { describe, expect, it } from "vitest";
import { highlightParts, parseHeadings, resolveDocLink, slugifyHeading, stripLeadingTitle } from "./docs";

describe("slugifyHeading", () => {
  it("makes an anchor a link can be written by hand", () => {
    expect(slugifyHeading("The music rides the picture")).toBe("the-music-rides-the-picture");
    expect(slugifyHeading("Locks and channels")).toBe("locks-and-channels");
  });
  it("drops the inline markdown and the punctuation, not the words", () => {
    expect(slugifyHeading("`S`, and what a *piece* is")).toBe("s-and-what-a-piece-is");
    expect(slugifyHeading("[The library](x.md)")).toBe("the-library");
  });
  it("answers something for a heading with nothing slug-like in it", () => {
    expect(slugifyHeading("///")).toBe("");
  });
});

describe("parseHeadings", () => {
  const markdown = [
    "# The Timeline",
    "",
    "Text.",
    "## The four lanes",
    "```",
    "## not a heading, it is fenced",
    "```",
    "### The library",
    "#### too deep for the rail",
    "### The library",
  ].join("\n");

  it("takes levels 1 to 3 and skips a fenced block", () => {
    expect(parseHeadings(markdown).map((h) => [h.level, h.text])).toEqual([
      [1, "The Timeline"], [2, "The four lanes"], [3, "The library"], [3, "The library"],
    ]);
  });

  it("gives a repeated heading its own anchor", () => {
    expect(parseHeadings(markdown).map((h) => h.id)).toEqual([
      "the-timeline", "the-four-lanes", "the-library", "the-library-2",
    ]);
  });

  it("keeps the line each came from, so the renderer can match it", () => {
    expect(parseHeadings(markdown).map((h) => h.line)).toEqual([1, 4, 8, 10]);
  });

  it("is total: nothing, and no headings at all", () => {
    expect(parseHeadings("")).toEqual([]);
    expect(parseHeadings("just a paragraph\n")).toEqual([]);
  });
});

describe("stripLeadingTitle", () => {
  it("drops the H1 the page has already drawn, and the blank line under it", () => {
    expect(stripLeadingTitle("# The Timeline\n\nText.\n", "The Timeline")).toBe("Text.\n");
  });
  it("keeps a first heading that says something else", () => {
    const md = "# Something else\n\nText.\n";
    expect(stripLeadingTitle(md, "The Timeline")).toBe(md);
  });
  it("keeps a document that does not open with a heading", () => {
    expect(stripLeadingTitle("Text.\n# The Timeline\n", "The Timeline")).toBe("Text.\n# The Timeline\n");
  });
  it("drops a leading HTML h1 too — the README's own shape", () => {
    // The server takes the title from this tag, and nothing renders raw HTML
    // here, so leaving it would print the tag as text under the same words.
    expect(stripLeadingTitle('<h1 align="center">Media Studio Enterprise</h1>\n\nText.\n', "Media Studio Enterprise"))
      .toBe("Text.\n");
    expect(stripLeadingTitle("<h1>Something else</h1>\n\nText.\n", "The Timeline"))
      .toBe("<h1>Something else</h1>\n\nText.\n");
  });
});

describe("resolveDocLink", () => {
  const here = "docs/guides/timeline.md";

  it("resolves a sibling document against the one it was written in", () => {
    expect(resolveDocLink("music.md", here)).toEqual({ kind: "doc", slug: "docs/guides/music", hash: "" });
    expect(resolveDocLink("../../README.md#install", here)).toEqual({ kind: "doc", slug: "README", hash: "#install" });
  });
  it("takes an absolute .md as a slug and anything else absolute as a route", () => {
    expect(resolveDocLink("/docs/guides/timeline.md", here)).toEqual({ kind: "doc", slug: "docs/guides/timeline", hash: "" });
    expect(resolveDocLink("/projects", here)).toEqual({ kind: "route", to: "/projects" });
  });
  it("knows an anchor, an external link and a plain one", () => {
    expect(resolveDocLink("#the-keys", here)).toEqual({ kind: "anchor", hash: "#the-keys" });
    expect(resolveDocLink("https://example.com", here)).toEqual({ kind: "external" });
    expect(resolveDocLink("mailto:someone@example.com", here)).toEqual({ kind: "external" });
    expect(resolveDocLink("//example.com", here)).toEqual({ kind: "external" });
    expect(resolveDocLink("not-a-document", here)).toEqual({ kind: "plain" });
    expect(resolveDocLink("", here)).toEqual({ kind: "plain" });
  });
});

describe("highlightParts", () => {
  it("keeps the matched text so it can be marked", () => {
    expect(highlightParts("a lane and a Lane", "lane")).toEqual(["a ", "lane", " and a ", "Lane", ""].filter(Boolean));
  });
  it("takes a query full of punctuation literally rather than as a pattern", () => {
    expect(highlightParts("a (b) c", "(b)")).toEqual(["a ", "(b)", " c"]);
    expect(() => highlightParts("anything", "[")).not.toThrow();
  });
  it("returns the text whole when there is nothing to look for", () => {
    expect(highlightParts("a lane", "")).toEqual(["a lane"]);
  });
});
