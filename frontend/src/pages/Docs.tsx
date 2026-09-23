/**
 * The documentation reader, ported from OpenSight's `pages/Docs.tsx` — the
 * reference implementation (CLAUDE.md), and what the owner asked this page to
 * look like.
 *
 * The documents themselves are ordinary markdown in the checkout, served by
 * `api/routers/docs.py`: the four root documents and `docs/guides/`. Nothing
 * is declared in them — the title is the first `# ` line, the summary the
 * first line of prose, the section the folder — so writing a guide is writing
 * a file.
 *
 * The page is a sidebar of collapsible sections with a search over the whole
 * set, the document itself, a contents rail built from its headings that
 * follows the scroll, and the previous/next pair at the foot. The rules it
 * needs are pure and live in `lib/docs.ts`; the rendering is `DocMarkdown`.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, BookOpen, ChevronDown, ChevronRight, FileText, Search, X } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { Button, Card, EmptyState, ErrorBox, Input, PageHeader, Spinner } from "../components/ui";
import { DocMarkdown } from "../components/DocMarkdown";
import { highlightParts, parseHeadings, scrollToAnchor, stripLeadingTitle } from "../lib/docs";

interface DocItem { slug: string; title: string; section: string; summary: string; words: number; path: string }
interface DocSection { name: string; items: DocItem[] }
interface DocList { sections: DocSection[]; count: number }
interface DocDetail { slug: string; title: string; section: string; content: string; headings: { level: number; text: string }[]; path: string }
interface SearchHit extends DocItem { snippet: string; matches: number }

/** How far down the page a heading counts as the one being read (the topbar is sticky). */
const SPY_OFFSET_PX = 80;

function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  return (
    <>
      {highlightParts(text, q).map((part, i) => (
        part.toLowerCase() === q.toLowerCase() ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>
      ))}
    </>
  );
}

export default function DocsPage() {
  const params = useParams();
  const splat = (params["*"] || "").replace(/^\/+|\/+$/g, "");
  const location = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [activeHeading, setActiveHeading] = useState("");

  // Debounced: a search fires a quarter of a second after the typing stops,
  // not once per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const list = useQuery({ queryKey: ["docs"], queryFn: () => api.get<DocList>("/api/docs"), staleTime: 5 * 60_000 });
  const allItems = useMemo(() => (list.data?.sections || []).flatMap((s) => s.items), [list.data]);
  const readme = allItems.find((d) => d.slug.toLowerCase() === "readme");
  const defaultSlug = readme?.slug || allItems[0]?.slug || "";
  // A link in the app says /docs/guides/timeline; the document itself is
  // addressed as docs/guides/timeline. Both reach it (the route accepts the
  // short form), and this keeps the sidebar's highlight on the right entry.
  const activeSlug = useMemo(() => {
    if (!splat) return defaultSlug;
    if (allItems.some((d) => d.slug === splat)) return splat;
    const prefixed = `docs/${splat}`;
    return allItems.some((d) => d.slug === prefixed) ? prefixed : splat;
  }, [splat, defaultSlug, allItems]);
  const activeItem = allItems.find((d) => d.slug === activeSlug);

  const doc = useQuery({
    queryKey: ["doc", activeSlug],
    queryFn: () => api.get<DocDetail>(`/api/docs/${activeSlug}`),
    enabled: !!activeSlug,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const hits = useQuery({
    queryKey: ["docs-search", q],
    queryFn: () => api.get<SearchHit[]>(`/api/docs/search?q=${encodeURIComponent(q)}`),
    enabled: q.length >= 2,
    staleTime: 60_000,
  });

  const body = useMemo(() => (doc.data ? stripLeadingTitle(doc.data.content, doc.data.title) : ""), [doc.data]);
  const headings = useMemo(() => parseHeadings(body), [body]);
  const tocHeadings = useMemo(() => {
    const h1s = headings.filter((h) => h.level === 1).length;
    return headings.filter((h) => h.level > 1 || h1s > 1);
  }, [headings]);

  // Keep the section holding the document that is open expanded.
  useEffect(() => {
    if (activeItem) setCollapsed((c) => (c[activeItem.section] ? { ...c, [activeItem.section]: false } : c));
  }, [activeItem]);

  // To the anchor once the document is on screen, or to the top for a fresh one.
  useEffect(() => {
    if (!doc.data) return;
    if (location.hash) requestAnimationFrame(() => scrollToAnchor(location.hash));
    else window.scrollTo({ top: 0 });
  }, [doc.data, location.hash]);

  // Which heading the contents rail marks: the last one above the fold.
  useEffect(() => {
    if (!tocHeadings.length) {
      setActiveHeading("");
      return;
    }
    const onScroll = () => {
      let current = tocHeadings[0].id;
      for (const h of tocHeadings) {
        const el = document.getElementById(h.id);
        if (!el) continue;
        if (el.getBoundingClientRect().top <= SPY_OFFSET_PX) current = h.id;
        else break;
      }
      setActiveHeading(current);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [tocHeadings]);

  const index = allItems.findIndex((d) => d.slug === activeSlug);
  const prev = index > 0 ? allItems[index - 1] : null;
  const next = index >= 0 && index < allItems.length - 1 ? allItems[index + 1] : null;
  const words = activeItem?.words ?? (doc.data ? doc.data.content.split(/\s+/).filter(Boolean).length : 0);
  const searching = q.length >= 2;

  return (
    <>
      <PageHeader
        title="Documents"
        subtitle={list.data
          ? `${list.data.count} document${list.data.count === 1 ? "" : "s"}: the guides and the project's own notes, shipped with Media Studio.`
          : "The guides and the project's own notes, shipped with Media Studio."}
        actions={readme && activeSlug !== readme.slug
          ? <Button icon={<BookOpen size={15} />} onClick={() => navigate(`/docs/${readme.slug}`)}>Open README</Button>
          : undefined}
      />

      <div className="os-docs">
        <Card className="os-docs-nav">
          <div className="os-docs-search">
            <Search size={15} />
            <Input
              placeholder="Search the documents…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search the documents"
            />
            {search && (
              <button type="button" className="os-icon-btn os-docs-clear" onClick={() => setSearch("")} aria-label="Clear the search">
                <X size={14} />
              </button>
            )}
          </div>

          {searching ? (
            hits.isPending ? <Spinner label="Searching…" />
              : hits.isError ? <ErrorBox message={errorMessage(hits.error)} />
                : hits.data.length === 0 ? (
                  <div className="os-muted os-small" style={{ padding: 10 }}>No document mentions “{q}”.</div>
                ) : (
                  <>
                    <div className="os-docs-hits-title">{hits.data.length} result{hits.data.length === 1 ? "" : "s"}</div>
                    {hits.data.map((hit) => (
                      <Link key={hit.slug} to={`/docs/${hit.slug}`} className={`os-docs-hit ${hit.slug === activeSlug ? "active" : ""}`}>
                        <div className="os-docs-hit-title"><Highlight text={hit.title} q={q} /></div>
                        <div className="os-docs-hit-meta">{hit.section} · {hit.matches} match{hit.matches === 1 ? "" : "es"}</div>
                        {hit.snippet && <div className="os-docs-hit-snippet"><Highlight text={hit.snippet} q={q} /></div>}
                      </Link>
                    ))}
                  </>
                )
          ) : list.isPending ? <Spinner label="Reading the documents…" />
            : list.isError ? <ErrorBox message={errorMessage(list.error)} />
              : list.data.sections.length === 0 ? (
                <EmptyState icon={<BookOpen size={32} />} title="No documents" sub="Add a markdown file under docs/guides/ and it appears here." />
              ) : (
                list.data.sections.map((section) => {
                  const shut = !!collapsed[section.name];
                  return (
                    <div key={section.name}>
                      <button
                        type="button"
                        className="os-docs-section"
                        onClick={() => setCollapsed((c) => ({ ...c, [section.name]: !shut }))}
                        aria-expanded={!shut}
                      >
                        {shut ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                        {section.name}
                        <span className="os-docs-section-count">{section.items.length}</span>
                      </button>
                      {!shut && section.items.map((item) => (
                        <Link
                          key={item.slug}
                          to={`/docs/${item.slug}`}
                          className={`os-docs-item ${item.slug === activeSlug ? "active" : ""}`}
                          title={item.summary || item.title}
                        >
                          {item.title}
                          <small>{item.words.toLocaleString()} words</small>
                        </Link>
                      ))}
                    </div>
                  );
                })
              )}
        </Card>

        <Card>
          {!activeSlug ? (
            list.isPending ? <Spinner label="Reading the documents…" /> : (
              <EmptyState icon={<FileText size={32} />} title="Nothing to read yet" sub="Add a markdown file under docs/guides/ and it appears here." />
            )
          ) : doc.isPending ? <Spinner label="Reading the document…" />
            : doc.isError ? (
              <EmptyState
                icon={<FileText size={32} />}
                title="No such document"
                sub={`${errorMessage(doc.error)} (${activeSlug})`}
                action={<Button onClick={() => navigate("/docs")}>Back to the documents</Button>}
              />
            ) : (
              <>
                <div className="os-docs-crumbs">
                  <Link to="/docs">Documents</Link>
                  <ChevronRight size={13} />
                  <span>{doc.data.section}</span>
                  <ChevronRight size={13} />
                  <span style={{ color: "var(--text-2)" }}>{doc.data.title}</span>
                </div>
                <h1 className="os-docs-title">{doc.data.title}</h1>
                <div className="os-docs-meta">
                  <span>{words.toLocaleString()} words</span>
                  <span>about {Math.max(1, Math.round(words / 200))} min read</span>
                  <code>{doc.data.path}</code>
                </div>
                <div className={`os-docs-body ${tocHeadings.length >= 2 ? "" : "no-toc"}`}>
                  <DocMarkdown content={body} currentPath={doc.data.path} headings={headings} />
                  {tocHeadings.length >= 2 && (
                    <aside className="os-docs-toc">
                      <div className="os-docs-toc-title">On this page</div>
                      {tocHeadings.map((h) => (
                        <a
                          key={h.id}
                          href={`#${h.id}`}
                          className={`l${h.level} ${activeHeading === h.id ? "active" : ""}`}
                          onClick={(e) => { e.preventDefault(); scrollToAnchor(`#${h.id}`); navigate({ hash: h.id }, { replace: true }); }}
                        >
                          {h.text}
                        </a>
                      ))}
                    </aside>
                  )}
                </div>
                {(prev || next) && (
                  <div className="os-docs-pager">
                    {prev && (
                      <Link to={`/docs/${prev.slug}`} className="prev">
                        <small><ArrowLeft size={11} /> Previous</small>
                        <span className="os-docs-pager-title">{prev.title}</span>
                      </Link>
                    )}
                    {next && (
                      <Link to={`/docs/${next.slug}`} className="next">
                        <small>Next <ArrowRight size={11} /></small>
                        <span className="os-docs-pager-title">{next.title}</span>
                      </Link>
                    )}
                  </div>
                )}
              </>
            )}
        </Card>
      </div>
    </>
  );
}
