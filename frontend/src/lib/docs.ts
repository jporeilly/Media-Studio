/**
 * The documentation reader's pure helpers — the anchors the contents rail is
 * built from, where a markdown link should take the reader, and the leading
 * title the page renders itself.
 *
 * Ported from OpenSight's `components/insights/DocMarkdown.tsx`, which keeps
 * these inside the renderer. They live in `lib/` here because this repo tests
 * its pure helpers and leaves the components to the SSR render tests
 * (`docs.test.ts`).
 *
 * The heading list is derived TWICE, once on each side: `GET /api/docs/{slug}`
 * reports the headings so the server's answer is complete, and this parses the
 * same markdown to give each one the id its anchor uses. The two agree because
 * they follow the same rule — levels 1 to 3, fenced code skipped — and the
 * document route's own test pins it.
 */

/** One heading of a document, with the anchor id its link points at. */
export interface HeadingEntry {
  level: number;
  text: string;
  id: string;
  /** The line it came from, so the renderer can match a rendered heading to it. */
  line: number;
}

/**
 * A deterministic anchor id: lower case, inline markdown taken out, anything
 * that is not a letter, a digit or a space dropped, spaces to hyphens. The
 * same text always gives the same id, so a link written by hand into a
 * document keeps working.
 */
export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[`*_~]/g, "")
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
}

/**
 * The headings (levels 1 to 3) with unique ids, skipping fenced code — a `#`
 * inside a fence is a comment in someone's shell, not a section. A repeated
 * title gets `-2`, `-3` and so on, so two sections called "The library" still
 * have one anchor each.
 */
export function parseHeadings(markdown: string): HeadingEntry[] {
  const out: HeadingEntry[] = [];
  const used = new Map<string, number>();
  let fence = false;
  String(markdown || "").split(/\r?\n/).forEach((line, index) => {
    if (/^\s*(```|~~~)/.test(line)) {
      fence = !fence;
      return;
    }
    if (fence) return;
    const m = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) return;
    // The text as it is DISPLAYED: links, emphasis and code ticks taken out.
    const text = m[2].trim().replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[`*_~]/g, "").trim();
    let id = slugifyHeading(text) || "section";
    const seen = used.get(id) || 0;
    used.set(id, seen + 1);
    if (seen) id = `${id}-${seen + 1}`;
    out.push({ level: m[1].length, text, id, line: index + 1 });
  });
  return out;
}

/**
 * Drop a leading `# Title` when it is the title the page has already drawn:
 * the reader renders the title itself, so the markdown H1 would be the same
 * words twice. A document whose first heading says something else keeps it.
 *
 * An HTML `<h1>` counts, because the server's own title rule reads one (this
 * repo's README opens with a centred `<h1>` rather than a markdown heading,
 * and react-markdown has no raw-HTML plugin here, so it would otherwise print
 * the tags as text under a title saying the same thing).
 */
export function stripLeadingTitle(markdown: string, title: string): string {
  const m = /^\s*#\s+(.+?)\s*#*\s*(\r?\n|$)/.exec(markdown)
    ?? /^\s*<h1[^>]*>(.+?)<\/h1>\s*(\r?\n|$)/i.exec(markdown);
  if (!m || m[1].trim() !== title.trim()) return markdown;
  return markdown.slice(m[0].length);
}

export type ResolvedLink =
  | { kind: "doc"; slug: string; hash: string }
  | { kind: "anchor"; hash: string }
  | { kind: "route"; to: string }
  | { kind: "external" }
  | { kind: "plain" };

/**
 * Where a markdown link should take the reader, relative to the document it
 * was written in (`docs/guides/timeline.md`): another document, an anchor in
 * this one, a route inside the app, or somewhere outside it. A link to
 * anything that is not a `.md` file is left exactly as the author wrote it.
 */
export function resolveDocLink(href: string, currentPath: string): ResolvedLink {
  const raw = (href || "").trim();
  if (!raw) return { kind: "plain" };
  if (raw.startsWith("#")) return { kind: "anchor", hash: raw };
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith("//")) return { kind: "external" };

  const hashIndex = raw.indexOf("#");
  const hash = hashIndex >= 0 ? raw.slice(hashIndex) : "";
  const pathPart = (hashIndex >= 0 ? raw.slice(0, hashIndex) : raw).split("?")[0];

  if (pathPart.startsWith("/")) {
    if (/\.md$/i.test(pathPart)) return { kind: "doc", slug: pathPart.replace(/^\/+/, "").replace(/\.md$/i, ""), hash };
    return { kind: "route", to: pathPart + hash };
  }
  if (!/\.md$/i.test(pathPart)) return { kind: "plain" };

  const base = currentPath.split("/").filter(Boolean);
  base.pop(); // the folder the current document is in
  for (const segment of pathPart.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") base.pop();
    else base.push(segment);
  }
  return { kind: "doc", slug: base.join("/").replace(/\.md$/i, ""), hash };
}

/**
 * Scroll to an anchor by its id, or by what the id WOULD be for that text — so
 * a link someone wrote as `#The keys` lands as well as `#the-keys`.
 *
 * The one thing here that touches the DOM, and so the one thing without a unit
 * test: it lives beside `slugifyHeading` because that is the rule it applies,
 * and keeping it out of `DocMarkdown` leaves that file exporting a component
 * and nothing else (the eslint fast-refresh rule).
 */
export function scrollToAnchor(hash: string) {
  const id = decodeURIComponent(hash.replace(/^#/, ""));
  if (!id) return;
  const el = document.getElementById(id) || document.getElementById(slugifyHeading(id));
  el?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * Split `text` on `needle` keeping the matched parts, for the search results'
 * highlighting: `["a ", "lane", " and"]` for "lane". Case is ignored and the
 * needle is taken literally, so a query full of regex punctuation highlights
 * itself rather than throwing.
 */
export function highlightParts(text: string, needle: string): string[] {
  if (!needle) return [text];
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.split(new RegExp(`(${escaped})`, "ig")).filter((part) => part !== "");
}
