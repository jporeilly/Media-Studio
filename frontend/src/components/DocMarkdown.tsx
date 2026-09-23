/**
 * The documentation reader's markdown, ported from OpenSight's
 * `components/insights/DocMarkdown.tsx`.
 *
 * The shared `Markdown` primitive in `ui.tsx` renders markdown with no
 * `components` override, which is right for a model's answer and not enough
 * for a document. This one wraps `react-markdown` (already a dependency, with
 * `remark-gfm` for the tables) to:
 *
 * - give the h1 to h3 headings stable ids, so the contents rail's links land;
 * - resolve a relative `.md` link against the document it was written in and
 *   follow it through the router rather than reloading the app;
 * - open an external link in a new tab;
 * - keep a wide table scrollable instead of stretching the page.
 *
 * The rules themselves live in `lib/docs.ts`, where they are unit-tested;
 * nothing here decides anything a test cannot ask about.
 */
import { isValidElement, useMemo, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { type HeadingEntry, resolveDocLink, scrollToAnchor, slugifyHeading } from "../lib/docs";

function flattenText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (isValidElement(node)) return flattenText((node.props as { children?: ReactNode }).children);
  return "";
}

export function DocMarkdown({ content, currentPath, headings }: {
  content: string; currentPath: string; headings: HeadingEntry[];
}) {
  const navigate = useNavigate();

  const components = useMemo<Components>(() => {
    // Matched by SOURCE LINE, so two sections with the same title keep the
    // two ids `parseHeadings` gave them rather than both answering to the first.
    const byLine = new Map<number, HeadingEntry>();
    headings.forEach((h) => byLine.set(h.line, h));
    const heading = (Tag: "h1" | "h2" | "h3") =>
      function Heading({ node, children, ...rest }: React.ComponentProps<typeof Tag> & { node?: { position?: { start?: { line?: number } } } }) {
        const line = node?.position?.start?.line;
        const id = (line !== undefined && byLine.get(line)?.id) || slugifyHeading(flattenText(children)) || undefined;
        return <Tag id={id} {...rest}>{children}</Tag>;
      };
    return {
      h1: heading("h1"),
      h2: heading("h2"),
      h3: heading("h3"),
      a: ({ node: _node, href, children, ...rest }) => {
        const target = resolveDocLink(href || "", currentPath);
        if (target.kind === "external") {
          return <a href={href} target="_blank" rel="noreferrer" {...rest}>{children}</a>;
        }
        if (target.kind === "doc" || target.kind === "route") {
          const to = target.kind === "doc" ? `/docs/${target.slug}${target.hash}` : target.to;
          return <a href={to} onClick={(e) => { e.preventDefault(); navigate(to); }} {...rest}>{children}</a>;
        }
        if (target.kind === "anchor") {
          return (
            <a
              href={href}
              onClick={(e) => { e.preventDefault(); scrollToAnchor(target.hash); navigate({ hash: target.hash }, { replace: true }); }}
              {...rest}
            >
              {children}
            </a>
          );
        }
        return <a href={href} {...rest}>{children}</a>;
      },
      table: ({ node: _node, ...rest }) => <div className="os-docs-table-wrap"><table {...rest} /></div>,
    };
  }, [headings, currentPath, navigate]);

  return (
    <div className="os-markdown os-docs-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{content || ""}</ReactMarkdown>
    </div>
  );
}
