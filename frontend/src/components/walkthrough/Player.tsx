/**
 * The walkthrough player (W1): a PURE component. It takes the walkthrough's document and a function from a
 * step to its picture's URL, and makes NO API call of its own - so W2's "Export as HTML" can bundle it as it
 * is. The page `/walkthroughs/:id/play` (`pages/WalkthroughPlay.tsx`) loads the record and renders this full
 * window, outside the Shell.
 *
 * The picture is fitted (contain) and centred on a neutral backdrop; the hotspot's displayed rectangle is
 * `fitRect` + `displayedRect` (lib/walkthrough.ts, pure, tested); the hotspot is an outlined, pulsing
 * BUTTON labelled with the step's text (no pulse under prefers-reduced-motion); the tooltip sits beside it on
 * the placement asked, `auto` choosing the side with room (`tooltipPlacement`, pure, tested), never off
 * screen; a modal step - or a tooltip step without a hotspot - is a centred card over the dimmed picture (a
 * hotspot the step also has is drawn over the dim and still advances). The hotspot goes on at the click the step
 * names (B1, `hotspotAdvances`): a left step on a click, a right step on the context menu only (the browser's menu
 * suppressed), a double step on a double-click only, a step of no click kind (Shift+F8) on any click; a wrong click
 * pulses the hotspot once more and shows a nudge over it - "Right-click here", "Double-click here" - for 1.5 s
 * (`clickNudge`, `NUDGE_MS`). Next, ArrowRight, Space and Enter always go on - Enter and Space on the focused hotspot
 * are the player's own step, the button's click prevented, so a keyboard user never meets the nudge. Back or
 * ArrowLeft goes back; a click on the picture outside the hotspot makes it pulse once more (a hint) and does not
 * advance; Home / End go first / last. Escape closes what is open first - the transcript drawer, then the Chapters
 * menu - and leaves otherwise. The last step's Next reads Finish and shows an end card with Restart and Close.
 *
 * EMBEDDED (B2, `embedded`): framed in a content manager's lesson (the exported page tells by `bundleEmbedded`),
 * the player has nowhere to leave to: no Close button, a compact header, Escape does nothing when nothing is open,
 * the end card offers Start again alone, and the empty card has no Close. The standalone page is unchanged.
 *
 * The TRANSCRIPT (the owner's addendum): a header button, or T, opens a drawer over the stage with the
 * walkthrough as Markdown (`transcript`, from the document the player already holds - no request), with Copy
 * and Save as… (`<name>.md`). It is the narration script of the narrated video to come.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from "react";
import { ChevronLeft, ChevronRight, Copy, Download, FileText, List, RotateCcw, X } from "lucide-react";
import type { Size } from "../../lib/annotate";
import { copyText, downloadText } from "../../lib/files";
import {
  type HotspotEvent, type Step, END_TITLE, NO_PICTURE_CARD, NUDGE_MS, backIndex, chapterAt, chapters, clampIndex, clickLead, clickNudge, displayedRect, endIndex,
  fitRect, hasPicture, homeIndex, hotspotAdvances, nextIndex, shownAsModal, stepLabel, tooltipPlacement, transcript, transcriptFileName, typingIn,
} from "../../lib/walkthrough";

export interface PlayerProps {
  walkthrough: { name: string; steps: Step[] };
  /** A step's picture (the page gives the API's URL; an export gives a file's). */
  imageUrl: (step: Step) => string;
  /** The step to start at (0-based; clamped). */
  initialStep?: number;
  /** Escape (with nothing open), Close and the end card's Close. Never called when `embedded`. */
  onExit: () => void;
  /** Overrides the `prefers-reduced-motion` media query (the pulse is left out when true). */
  reducedMotion?: boolean;
  /** B2: framed in a lesson - no Close, a compact header, Escape leaves nothing, the end card's Start again. */
  embedded?: boolean;
}

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function useElementSize(ref: React.RefObject<HTMLElement | null>): Size {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const width = el.clientWidth, height = el.clientHeight;
      setSize((s) => (s.width === width && s.height === height ? s : { width, height }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

export function Player({ walkthrough, imageUrl, initialStep = 0, onExit, reducedMotion, embedded = false }: PlayerProps) {
  const steps = walkthrough.steps;
  const count = steps.length;
  const [index, setIndex] = useState(() => clampIndex(initialStep, count));
  const [ended, setEnded] = useState(false);
  const [hint, setHint] = useState(0);
  // B1: the nudge a wrong click on the hotspot gets, for NUDGE_MS; gone with the step.
  const [nudge, setNudge] = useState<string | null>(null);
  const nudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [chaptersOpen, setChaptersOpen] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const hotspotRef = useRef<HTMLButtonElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const stage = useElementSize(stageRef);
  const [tipSize, setTipSize] = useState<Size>({ width: 0, height: 0 });
  const reduced = reducedMotion ?? prefersReducedMotion();

  // The step shown is always a step the walkthrough has: the index is clamped wherever it is read, so a
  // document that shrank under the player (a refetch) cannot leave "Step 9 of 3" or a Next that never finishes.
  const current = clampIndex(index, count);
  const step = count > 0 ? steps[current] : null;
  const last = current >= count - 1;
  // A step without a picture yet (an imported one, W2) has no hotspot either: a card on the neutral backdrop, never a broken image.
  const pictured = !!step && hasPicture(step);
  const modal = !!step && (shownAsModal(step) || !pictured);
  const label = stepLabel(current, count);

  // The tooltip's own size, measured once it is drawn for this step, so it can be placed beside the hotspot.
  const text = step?.text ?? "";
  useLayoutEffect(() => {
    const el = tipRef.current;
    if (!el) return;
    const width = el.offsetWidth, height = el.offsetHeight;
    setTipSize((s) => (s.width === width && s.height === height ? s : { width, height }));
  }, [current, text, modal, ended, stage.width, stage.height]);

  const clearNudge = useCallback(() => {
    if (nudgeTimer.current) clearTimeout(nudgeTimer.current);
    nudgeTimer.current = null;
    setNudge(null);
  }, []);
  useEffect(() => () => { if (nudgeTimer.current) clearTimeout(nudgeTimer.current); }, []);
  const go = useCallback((i: number) => {
    setIndex(clampIndex(i, count));
    setEnded(false);
    setChaptersOpen(false);
    clearNudge();
  }, [count, clearNudge]);
  const next = useCallback(() => {
    if (ended || count === 0) return;
    const n = nextIndex(current, count);
    if (n === null) { setEnded(true); clearNudge(); }
    else go(n);
  }, [ended, count, current, go, clearNudge]);
  const back = useCallback(() => {
    if (ended) { setEnded(false); return; }
    go(backIndex(current));
  }, [ended, current, go]);
  const restart = useCallback(() => go(homeIndex()), [go]);
  /** B1: a wrong click on the hotspot - the hotspot pulses once more (drawn afresh, as after a click outside it) and the nudge shows for NUDGE_MS. */
  const showNudge = useCallback((text: string) => {
    setNudge(text);
    setHint((h) => h + 1);
    if (nudgeTimer.current) clearTimeout(nudgeTimer.current);
    nudgeTimer.current = setTimeout(() => { nudgeTimer.current = null; setNudge(null); }, NUDGE_MS);
  }, []);

  /** The Chapters menu's items; ArrowUp / ArrowDown move the focus among them, wrapping round. */
  const moveInMenu = useCallback((delta: 1 | -1) => {
    const items = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
    if (items.length === 0) return;
    const at = items.findIndex((item) => item === document.activeElement);
    const to = at < 0 ? (delta > 0 ? 0 : items.length - 1) : (at + delta + items.length) % items.length;
    items[to].focus();
  }, []);

  // The keys. Enter and Space on a focused button are the button's own click (Next's, a chapter's - and the hotspot's,
  // which handles them itself: `onHotspotKey`), so they are left alone here - handling them as well would go two steps
  // on. Escape closes what is open first: the transcript, then the Chapters menu; with nothing open it leaves - unless
  // the player is embedded (B2), where there is nowhere to leave to.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typingIn(e.target) || e.altKey || e.ctrlKey || e.metaKey) return;
      const onButton = (e.target as HTMLElement | null)?.tagName === "BUTTON";
      switch (e.key) {
        case "ArrowRight": e.preventDefault(); next(); break;
        case " ":
        case "Enter": if (onButton) return; e.preventDefault(); next(); break;
        case "ArrowLeft": e.preventDefault(); back(); break;
        case "ArrowDown": if (chaptersOpen) { e.preventDefault(); moveInMenu(1); } break;
        case "ArrowUp": if (chaptersOpen) { e.preventDefault(); moveInMenu(-1); } break;
        case "Home": e.preventDefault(); go(homeIndex()); break;
        case "End": e.preventDefault(); go(endIndex(count)); break;
        case "t":
        case "T": e.preventDefault(); setTranscriptOpen((o) => !o); break;
        case "Escape":
          e.preventDefault();
          if (transcriptOpen) setTranscriptOpen(false);
          else if (chaptersOpen) setChaptersOpen(false);
          else if (!embedded) onExit();
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, back, go, count, onExit, embedded, chaptersOpen, transcriptOpen, moveInMenu]);

  // The focus follows the step: the hotspot (a visible ring) on a tooltip step, the card's own button on a
  // modal step and on the end card - the card is what is modal, whatever else is drawn over the dim.
  useEffect(() => {
    const first = modal || ended ? primaryRef.current : hotspotRef.current;
    (first ?? primaryRef.current ?? hotspotRef.current)?.focus();
  }, [current, ended, modal, hint]);

  // The Chapters menu opens on the current chapter's item (or the first), so the arrow keys have somewhere to start.
  useEffect(() => {
    if (!chaptersOpen) return;
    const items = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
    (items.find((item) => item.getAttribute("aria-current") === "true") ?? items[0])?.focus();
  }, [chaptersOpen]);

  const fit = useMemo(() => (step && pictured ? fitRect({ width: step.width, height: step.height }, stage) : null), [step, pictured, stage]);
  const spot = step && step.hotspot && fit ? displayedRect(step.hotspot, fit) : null;
  const tip = step && spot && !modal && step.text ? tooltipPlacement(spot, tipSize, stage, step.placement) : null;
  const chapterList = useMemo(() => chapters(walkthrough), [walkthrough]);
  const chapter = chapterAt(walkthrough, current);
  const progress = count > 0 ? ((ended ? count : current + 1) / count) * 100 : 0;
  const markdown = useMemo(() => transcript(walkthrough), [walkthrough]);

  const onStageClick = () => {
    // Outside the hotspot: a hint, never a step on.
    if (!ended && step) setHint((h) => h + 1);
  };
  // B1: the hotspot's mouse events. The step goes on at the click its kind names; the context menu is suppressed
  // only where it is that click (a left step's right-click stays the browser's, as before); a wrong click nudges.
  const onHotspot = (event: HotspotEvent) => (e: ReactMouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (!step) return;
    if (hotspotAdvances(step.click, event)) {
      if (event === "contextmenu") e.preventDefault();
      next();
      return;
    }
    if (event === "click") {
      const text = clickNudge(step.click);
      if (text) showNudge(text);
    }
  };
  // Enter and Space on the focused hotspot are the player's own step on - the button's click is prevented, so a
  // right or double step never nudges a keyboard user; the window's handler leaves a button's keys alone.
  const onHotspotKey = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      next();
    }
  };
  // The nudge sits over the hotspot: above it when there is room, below it otherwise; it takes no pointer.
  const nudgeStyle = (r: { x: number; y: number; width: number; height: number }): CSSProperties =>
    r.y >= 40
      ? { left: r.x + r.width / 2, top: r.y - 8, transform: "translate(-50%, -100%)" }
      : { left: r.x + r.width / 2, top: r.y + r.height + 8, transform: "translate(-50%, 0)" };
  const copyTranscript = async () => {
    setCopied((await copyText(markdown)) ? "Copied to the clipboard." : "Could not copy: the page may not write to the clipboard.");
  };
  const nextLabel = last ? "Finish" : "Next";
  const rectStyle = (r: { x: number; y: number; width: number; height: number }): CSSProperties => ({ left: r.x, top: r.y, width: r.width, height: r.height });
  // F3: the real click to make, said first in the tooltip and in the hotspot's label; F4: the marker the hotspot is drawn as.
  const lead = step ? clickLead(step.click) : "";
  const marker = step?.marker === "beacon" ? "beacon" : "box";

  return (
    <div className={`ms-player${embedded ? " ms-player-embedded" : ""}`} data-walkthrough-player="" data-reduced-motion={reduced ? "true" : undefined} data-embedded={embedded ? "true" : undefined}>
      <header className="ms-player-head">
        <strong className="os-truncate" style={{ maxWidth: embedded ? 200 : 320 }} title={walkthrough.name}>{walkthrough.name}</strong>
        <span className="os-muted" data-player-progress="">{label}</span>
        <div className="os-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} aria-label="Progress">
          <div className="os-progress-fill" style={{ width: `${progress}%`, background: "var(--brand)" }} />
        </div>
        {chapterList.length > 0 && (
          <div className="ms-chapters">
            <button className="os-btn os-btn-secondary os-btn-sm" aria-haspopup="menu" aria-expanded={chaptersOpen} data-player-chapters="" onClick={() => setChaptersOpen((o) => !o)}>
              <List size={14} /> Chapters
            </button>
            {chaptersOpen && (
              <ul ref={menuRef} className="ms-chapters-menu" role="menu" aria-label="Chapters">
                {chapterList.map((c) => (
                  <li key={c.stepId} role="none">
                    <button role="menuitem" aria-current={chapter?.stepId === c.stepId ? "true" : undefined} onClick={() => go(c.index)}>
                      <span className="os-muted" style={{ fontSize: 11 }}>{c.index + 1}</span> {c.name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <button className="os-btn os-btn-secondary os-btn-sm" aria-expanded={transcriptOpen} data-player-transcript-toggle="" title="Transcript (T)" onClick={() => setTranscriptOpen((o) => !o)}>
          <FileText size={14} /> Transcript
        </button>
        <button className="os-btn os-btn-secondary os-btn-sm" onClick={back} disabled={current === 0 && !ended} data-player-back=""><ChevronLeft size={14} /> Back</button>
        <button className="os-btn os-btn-primary os-btn-sm" onClick={next} disabled={ended || count === 0} data-player-next="">{nextLabel} <ChevronRight size={14} /></button>
        {!embedded && <button className="os-icon-btn" onClick={onExit} title="Close (Esc)" aria-label="Close"><X size={18} /></button>}
      </header>
      <div ref={stageRef} className="ms-stage" onClick={onStageClick} data-player-stage="">
        {step && fit && pictured && <img src={imageUrl(step)} alt="" draggable={false} style={rectStyle(fit)} />}
        {(modal || ended) && step && <div className="ms-dim" />}
        {spot && !ended && (
          <button
            key={`${current}-${hint}`}
            ref={hotspotRef}
            type="button"
            className={`ms-hotspot${marker === "beacon" ? " ms-hotspot-beacon" : ""}${reduced ? "" : " ms-hotspot-pulse"}`}
            data-player-hotspot=""
            data-marker={marker}
            aria-label={`${lead ? `${lead}: ` : ""}${step!.text || "Next step"}`}
            style={rectStyle(spot)}
            onClick={onHotspot("click")}
            onContextMenu={onHotspot("contextmenu")}
            onDoubleClick={onHotspot("dblclick")}
            onKeyDown={onHotspotKey}
          >
            {marker === "beacon" && <span className="ms-beacon" data-player-beacon="" aria-hidden="true" />}
          </button>
        )}
        {spot && !ended && nudge && (
          <span className="ms-nudge" role="status" data-player-nudge="" style={nudgeStyle(spot)}>{nudge}</span>
        )}
        {step && !modal && !ended && tip && (
          <div ref={tipRef} className={`ms-tooltip ms-tooltip-${tip.side}`} role="dialog" aria-label={label} aria-live="polite" data-player-tooltip="" data-side={tip.side} style={{ left: tip.x, top: tip.y }} onClick={(e) => e.stopPropagation()}>
            {lead && <strong className="ms-tooltip-click" data-tooltip-click="">{lead}</strong>}
            {step.text}
          </div>
        )}
        {step && modal && !ended && (
          <div className="ms-modal" role="dialog" aria-modal="true" aria-label={label} aria-live="polite" data-player-modal="" onClick={(e) => e.stopPropagation()}>
            {step.text && <p style={{ margin: 0 }}>{step.text}</p>}
            {!pictured && <p className="os-muted os-small" style={{ margin: 0 }} data-player-no-picture="">{NO_PICTURE_CARD}</p>}
            <div className="os-row" style={{ justifyContent: "flex-end" }}>
              <button ref={primaryRef} className="os-btn os-btn-primary" onClick={next}>{nextLabel} <ChevronRight size={14} /></button>
            </div>
          </div>
        )}
        {ended && (
          <div className="ms-modal" role="dialog" aria-modal="true" aria-label={END_TITLE} aria-live="polite" data-player-end="" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: 0 }}>{END_TITLE}</h3>
            <p style={{ margin: 0 }} className="os-muted">{walkthrough.name}: {count} step{count === 1 ? "" : "s"}.</p>
            <div className="os-row" style={{ justifyContent: "flex-end" }}>
              <button ref={primaryRef} className="os-btn os-btn-primary" onClick={restart}><RotateCcw size={14} /> {embedded ? "Start again" : "Restart"}</button>
              {!embedded && <button className="os-btn os-btn-secondary" onClick={onExit}>Close</button>}
            </div>
          </div>
        )}
        {count === 0 && (
          <div className="ms-modal" role="dialog" aria-modal="true" aria-label="No steps" data-player-empty="" onClick={(e) => e.stopPropagation()}>
            <p style={{ margin: 0 }}>This walkthrough has no steps yet.</p>
            {!embedded && (
              <div className="os-row" style={{ justifyContent: "flex-end" }}>
                <button ref={primaryRef} className="os-btn os-btn-secondary" onClick={onExit}>Close</button>
              </div>
            )}
          </div>
        )}
        {transcriptOpen && (
          <aside className="ms-drawer" role="dialog" aria-label="Transcript" data-player-transcript="" onClick={(e) => e.stopPropagation()}>
            <div className="os-row" style={{ justifyContent: "space-between", gap: 8 }}>
              <strong>Transcript</strong>
              <div className="os-row" style={{ gap: 6 }}>
                <button className="os-btn os-btn-secondary os-btn-sm" onClick={() => void copyTranscript()} data-transcript-copy=""><Copy size={14} /> Copy</button>
                <button className="os-btn os-btn-secondary os-btn-sm" onClick={() => downloadText(transcriptFileName(walkthrough.name), markdown)} data-transcript-save=""><Download size={14} /> Save as…</button>
                <button className="os-icon-btn" onClick={() => setTranscriptOpen(false)} title="Close the transcript (Esc)" aria-label="Close the transcript"><X size={16} /></button>
              </div>
            </div>
            <p className="os-muted os-small" style={{ margin: 0 }}>What is said at each step, in order: the instruction, else the text. The narration script of the narrated video to come.</p>
            {copied && <span className="os-muted os-small" role="status" aria-live="polite" data-transcript-copied="">{copied}</span>}
            <pre className="ms-transcript" data-transcript-text="">{markdown}</pre>
          </aside>
        )}
      </div>
    </div>
  );
}
