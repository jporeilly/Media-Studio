/**
 * Walkthroughs (W1): the PURE half - the document (an ordered list of steps, each a picture copied from a
 * still with a hotspot, a tooltip or modal text, a placement and a chapter), what the builder's edits make
 * of it, what the player derives from it (the chapters, where next / back / home / end / a chapter jump
 * land, how the picture fits the window, where the hotspot is drawn and where the tooltip goes), and the
 * one content-equality rule its undo history is made with. The pages (`pages/Walkthroughs.tsx`,
 * `pages/WalkthroughBuilder.tsx`, `pages/WalkthroughPlay.tsx`) and the player (`components/walkthrough/
 * Player.tsx`) are the wiring; the server (`api/schemas.py WalkthroughIn`) validates exactly the document
 * written here. Unit-tested in walkthrough.test.ts.
 *
 * The hotspot is DRAWN with the annotation editor's crop maths (`lib/annotate.ts`: cropFromDrag, clampCrop,
 * cropHandles, handleAt, resizeCrop, moveCrop) with MIN_HOTSPOT_SIDE passed in as the minimum side; nothing
 * here copies them. `clampHotspot` is the one rule of its own - the server's `clamp_hotspot`, applied when a
 * step's picture is replaced by a smaller one.
 */
import type { Rect, Size } from "./annotate";
import { parseDate } from "./format";

// ---- limits and constants ------------------------------------------------------------------------------

/** Steps in a walkthrough (the server holds the same bound, services/walkthroughs.py MAX_STEPS). */
export const MAX_STEPS = 200;
/** Characters of a step's text (the server: MAX_STEP_TEXT). */
export const MAX_STEP_TEXT = 1000;
/** Characters of a step's instruction - what the learner does, as it would be spoken (the server: MAX_INSTRUCTION_CHARS). */
export const MAX_INSTRUCTION_CHARS = 2000;
/** Characters of a chapter's name (the server: MAX_CHAPTER_CHARS). */
export const MAX_CHAPTER_CHARS = 80;
/** Characters of a step's note - the guide's "Under the hood" hint an import keeps for the narrated video to come (the server: MAX_NOTE_CHARS). */
export const MAX_NOTE_CHARS = 4000;
/** Characters of the walkthrough's name (the server: MAX_NAME_CHARS). */
export const MAX_NAME_CHARS = 120;
/** A hotspot's sides, at least (the server: MIN_HOTSPOT_SIDE). */
export const MIN_HOTSPOT_SIDE = 8;
/**
 * The hotspot a guided capture draws round the pointer, and Add hotspot puts at the picture's centre (F2), in
 * picture pixels (the server clamps it again). `lib/guided.ts` re-exports it as the capture's.
 */
export const HOTSPOT_DEFAULT: Size = { width: 160, height: 60 };
export const DOCUMENT_VERSION = 1;

/** What the server says to a save whose revision is not the record's any more (its 409). */
export const STALE_MESSAGE = "This walkthrough was changed elsewhere: reload it.";
/** Said on the list when the builder's or the player's walkthrough is gone (deleted meanwhile, or somebody else's). */
export const GONE_MESSAGE = "That walkthrough is no longer there: it was deleted, or it is not yours.";
export const UNSAVED_ASK = { title: "Leave without saving?", message: "The changes you made since the last save are lost if you leave now.", confirmLabel: "Leave" };
export const SAVE_BEFORE_PLAY = { title: "Save before playing?", message: "The player shows what is saved. Save the walkthrough now, then play it?", confirmLabel: "Save and play" };
export const RELOAD_ASK = { title: "Reload the walkthrough?", message: "Your unsaved changes are lost; the walkthrough as it was last saved is loaded.", confirmLabel: "Reload" };
export const TOO_MANY_STEPS_MESSAGE = `A walkthrough can hold at most ${MAX_STEPS} steps.`;
/** The empty list says how a walkthrough is made - and where a still comes from. */
export const EMPTY_HOW =
  "Press Record… (in the desktop app) and click through the application: every click makes a step, with a beacon where you clicked. Or take a still with Capture, annotate it if you like, and add it as a step - or import a course workshop and capture its steps. Building and playing a walkthrough work in the browser edition too; only recording and taking a still need the desktop app.";
export const END_TITLE = "That is the end";
/** What the builder's placeholder and the player's card say of a step that has no picture yet (an imported one). */
export const NO_PICTURE_TITLE = "No picture yet";
export const NO_PICTURE_HOW = "Capture it in the desktop app (Capture steps…), or Replace picture… with a still.";
export const NO_PICTURE_CARD = "This step has no picture yet.";

// ---- the document -------------------------------------------------------------------------------------

export type StepKind = "tooltip" | "modal";
export type Placement = "auto" | "top" | "bottom" | "left" | "right";
export type Side = Exclude<Placement, "auto">;
export const PLACEMENTS: Placement[] = ["auto", "top", "bottom", "left", "right"];
export const PLACEMENT_LABELS: Record<Placement, string> = { auto: "Auto", top: "Above", bottom: "Below", left: "Left", right: "Right" };
/**
 * The real click the learner makes at a step (F3): the player's tooltip starts with "Right-click" / "Double-click"
 * when so, the hotspot still advances on any click. "left" is the default - a record from before the field, an
 * imported step, a step added from a still; Record sets it by the button pressed ("none" for the key, the bar's
 * button and a typed pause). The server: `services.walkthroughs.CLICKS`.
 */
export type ClickKind = "left" | "right" | "double" | "none";
export const CLICKS: ClickKind[] = ["left", "right", "double", "none"];
export const CLICK_LABELS: Record<ClickKind, string> = { left: "Left", right: "Right", double: "Double", none: "None" };
/**
 * How the hotspot is drawn in the player (F4): the outlined, pulsing box as before (the default, so every existing
 * walkthrough looks as it did), or a beacon - a pulsing ring with a dot at the hotspot's centre; the whole rectangle
 * is the click target either way. Record and Capture steps set "beacon" on the steps they picture. The server:
 * `services.walkthroughs.MARKERS`.
 */
export type Marker = "box" | "beacon";
export const MARKERS: Marker[] = ["box", "beacon"];
export const MARKER_LABELS: Record<Marker, string> = { box: "Box", beacon: "Beacon" };

/** What the player's tooltip starts with for the step's click kind: the real click to make; nothing for a left click or none. */
export function clickLead(click: ClickKind | undefined): string {
  return click === "right" ? "Right-click" : click === "double" ? "Double-click" : "";
}

export interface Hotspot { x: number; y: number; width: number; height: number }
/** Where a step's picture came from (informational): a still, or a course workshop's guide (W2). */
export interface CaptureSource { capture_id: string; annotated: boolean }
/**
 * A step imported from a course workshop (W2). Once a capture replaced its picture (W2c) it keeps the workshop's
 * fields - `image` then null, the picture no longer the guide's - with the capture's `capture_id` and `annotated`.
 */
export interface WorkshopSource { course: string; slug: string; image: string | null; alt: string; launcher: string | null; capture_id?: string; annotated?: boolean }
export type StepSource = CaptureSource | WorkshopSource;
/** Where an imported step is in its guide (W2c): set by the import, kept by the server through every save. */
export interface StepAnchor { key: string; ordinal: number }
/**
 * The workshop a walkthrough was imported from (W2), on the record - since W2e the workshops: `workshops` lists them
 * all in the course's order (`slug` is the first's, for a reader before this; absent on a record before W2e). The
 * server's own: the builder never sends `source` (`putBody` carries the name and the steps), as a step's `anchor`.
 */
export interface WalkthroughSource { course: string; slug: string; title: string; version: string; workshops?: { slug: string; title: string }[] }

export interface Step {
  id: string;
  /** The picture's file name in the walkthrough's folder (the server names it); null for a step without a picture yet (an imported one). */
  image: string | null;
  /** The picture's own size, in pixels (0 x 0 without a picture). */
  width: number;
  height: number;
  /** In the picture's pixels, inside it, at least MIN_HOTSPOT_SIDE a side; null for a step without one. */
  hotspot: Hotspot | null;
  kind: StepKind;
  /** What the viewer READS on the picture (the tooltip or the card): plain text with line breaks, at most MAX_STEP_TEXT. */
  text: string;
  /**
   * What the learner DOES at the step, as it would be spoken - read in order, the walkthrough's transcript and,
   * later, its narration; at most MAX_INSTRUCTION_CHARS. "" when not written (a record from before the field).
   */
  instruction: string;
  placement: Placement;
  /** The name of the chapter this step starts, or "". */
  chapter: string;
  /** The guide's "Under the hood" hint an import kept (W2): for the narrated video to come; the player does not show it. */
  note: string;
  /** Where the picture came from: informational only. */
  source: StepSource | null;
  /**
   * Where an imported step is in its guide (W2c, read by Export to course). The server's alone: the builder neither
   * edits nor sends it (`normaliseStep` leaves it out), and every save keeps the record's own.
   */
  anchor?: StepAnchor | null;
  /** The real click the learner makes here (F3): "left" by default. */
  click: ClickKind;
  /** How the hotspot is drawn (F4): "box" by default, "beacon" for a pulsing ring with a dot. */
  marker: Marker;
}

/** What the builder edits and the history holds: the name and the steps; the revision is kept beside it. */
export interface WalkthroughDocument { name: string; steps: Step[] }

/** The record as GET /api/walkthroughs/{wid} answers it. */
export interface Walkthrough extends WalkthroughDocument {
  id: string;
  owner_id: string | null;
  owner_name: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
  version: number;
  /** The workshop it was imported from (W2), when it was. */
  source?: WalkthroughSource | null;
}

/** Whether the step has a picture at all (W2: an imported step may not, yet). */
export function hasPicture(step: Pick<Step, "image">): boolean {
  return !!step.image;
}

/** One row of GET /api/walkthroughs: the counts and the first step's id for a thumbnail, never the steps. */
export interface WalkthroughSummary {
  id: string;
  name: string;
  owner_id: string | null;
  owner_name: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
  step_count: number;
  chapter_count: number;
  first_step_id: string | null;
}

/** The body of a save: the document with the revision it was loaded at. */
export interface WalkthroughPut { version: typeof DOCUMENT_VERSION; name: string; revision: number; steps: Step[] }

const STEP_KEYS: (keyof Step)[] = ["id", "image", "width", "height", "hotspot", "kind", "text", "instruction", "placement", "chapter", "note", "source", "click", "marker"];
/** What a step reads as without a field (a record from before it): the server's defaults, one place. */
const STEP_DEFAULTS: Partial<Record<keyof Step, unknown>> = { hotspot: null, source: null, image: null, kind: "tooltip", placement: "auto", click: "left", marker: "box" };

/** A text as the server stores it: CRLF and a lone CR become LF. */
const plainText = (value: unknown): string => String(value ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");

/**
 * A step with exactly the fields the server writes and reads, in one order, AS THE SERVER STORES THEM - the one
 * normalisation rule: the chapter trimmed, the text, the instruction and the note with LF line breaks, the hotspot
 * in whole pixels, a missing instruction or note "", a missing picture null, a missing click kind "left" and a
 * missing marker "box" (F3, F4: a record from before the fields). `serialiseDocument` compares documents through
 * it, so a document with `" Intro "` typed is the same document as the server's stripped answer, and "Unsaved
 * changes" clears after a save.
 */
export function normaliseStep(s: Step): Step {
  const out = {} as Record<string, unknown>;
  for (const key of STEP_KEYS) out[key] = s[key] ?? (key in STEP_DEFAULTS ? STEP_DEFAULTS[key] : "");
  const h = s.hotspot;
  out.hotspot = h ? { x: Math.round(h.x), y: Math.round(h.y), width: Math.round(h.width), height: Math.round(h.height) } : null;
  out.text = plainText(s.text);
  out.instruction = plainText(s.instruction);
  out.note = plainText(s.note);
  out.chapter = String(s.chapter ?? "").trim();
  return out as unknown as Step;
}

/** The document a loaded record carries. */
export function documentOf(rec: Pick<Walkthrough, "name" | "steps">): WalkthroughDocument {
  return { name: rec.name, steps: rec.steps.map(normaliseStep) };
}

/** The whole document as the server stores it (the name trimmed, every step normalised): the one string two documents are compared by. */
export function serialiseDocument(doc: WalkthroughDocument): string {
  return JSON.stringify({ name: doc.name.trim(), steps: doc.steps.map(normaliseStep) });
}

/** The content-equality rule the builder's history is made with: equal as the server would store them. */
export function sameDocument(a: WalkthroughDocument, b: WalkthroughDocument): boolean {
  return serialiseDocument(a) === serialiseDocument(b);
}

/** The body of `PUT /api/walkthroughs/{wid}`: the pictures never travel in it. */
export function putBody(doc: WalkthroughDocument, revision: number): WalkthroughPut {
  return { version: DOCUMENT_VERSION, name: doc.name.trim(), revision, steps: doc.steps.map(normaliseStep) };
}

/** The pictures a document names: what a saved record still holds (the builder prunes its history by it). */
export function imagesOf(doc: Pick<WalkthroughDocument, "steps">): Set<string> {
  return new Set(doc.steps.flatMap((s) => (s.image ? [s.image] : [])));
}

/** Whether every step of `doc` names a picture in `held` (a step without one names nothing) - a document that does not can never be saved again. */
export function namesOnly(doc: Pick<WalkthroughDocument, "steps">, held: Set<string>): boolean {
  return doc.steps.every((s) => !s.image || held.has(s.image));
}

/** The ids of the steps a guided capture visits: every step, or those without a picture yet. */
export function stepsToCapture(doc: Pick<WalkthroughDocument, "steps">, every: boolean): string[] {
  return doc.steps.filter((s) => every || !hasPicture(s)).map((s) => s.id);
}

// ---- the narration and the transcript ---------------------------------------------------------------------

/**
 * What is said at a step: the instruction when it is not blank, else the text. The ONE rule (the server's
 * `narration_of` is the same); the transcript, and later the narrated video, read it.
 */
export function narrationOf(step: Pick<Step, "text" | "instruction">): string {
  const instruction = step.instruction ?? "";
  return instruction.trim() ? instruction : step.text ?? "";
}

/**
 * The walkthrough as a transcript, in Markdown: the name as the title; each chapter as a heading at the step that
 * starts it (the steps before any chapter under none); each step a numbered item carrying `narrationOf(step)`
 * ("(no instruction yet)" when there is none), a step's further lines indented under its number. Byte for byte
 * what the server's `transcript_markdown` writes - the two tests name each other.
 */
export function transcript(doc: Pick<WalkthroughDocument, "name" | "steps">): string {
  const name = (doc.name ?? "").trim() || "Walkthrough";
  const lines: string[] = [`# ${name}`, ""];
  if (doc.steps.length === 0) lines.push("(no steps yet)");
  doc.steps.forEach((step, i) => {
    const chapter = (step.chapter ?? "").trim();
    if (chapter) {
      if (lines[lines.length - 1] !== "") lines.push("");
      lines.push(`## ${chapter}`, "");
    }
    const narration = plainText(narrationOf(step)).trim() || "(no instruction yet)";
    const parts = narration.split("\n");
    lines.push(`${i + 1}. ${parts[0]}`);
    for (const part of parts.slice(1)) lines.push(`   ${part}`);
  });
  return lines.join("\n") + "\n";
}

/**
 * A file stem from the walkthrough's name, as the server's `safe_stem` makes one: only letters, digits, space,
 * `_`, `.` and `-` kept, spaces to dashes, trimmed, at most 60 characters, "walkthrough" when nothing is left.
 */
export function safeFileStem(name: string, fallback = "walkthrough", limit = 60): string {
  const cleaned = (name || "").replace(/[^A-Za-z0-9 _.-]+/g, "").trim().replace(/^\.+|\.+$/g, "");
  const dashed = cleaned.replace(/\s+/g, "-").replace(/^-+|-+$/g, "");
  return dashed.slice(0, limit).replace(/[-.]+$/g, "") || fallback;
}

export function transcriptFileName(name: string): string {
  return `${safeFileStem(name)}.md`;
}

// ---- the builder's edits --------------------------------------------------------------------------------

export function stepIndex(doc: WalkthroughDocument, sid: string | null): number {
  return sid ? doc.steps.findIndex((s) => s.id === sid) : -1;
}

export function stepById(doc: WalkthroughDocument, sid: string | null): Step | null {
  return sid ? doc.steps.find((s) => s.id === sid) ?? null : null;
}

export function canAddStep(doc: WalkthroughDocument): boolean {
  return doc.steps.length < MAX_STEPS;
}

export function updateStep(doc: WalkthroughDocument, sid: string, next: Partial<Step> | ((s: Step) => Step)): WalkthroughDocument {
  return { ...doc, steps: doc.steps.map((s) => (s.id === sid ? (typeof next === "function" ? next(s) : { ...s, ...next }) : s)) };
}

export function deleteStep(doc: WalkthroughDocument, sid: string): WalkthroughDocument {
  return { ...doc, steps: doc.steps.filter((s) => s.id !== sid) };
}

/** `step` inserted after the step `afterId` (appended when null or unknown); a step already there is not added twice. */
export function insertStep(doc: WalkthroughDocument, step: Step, afterId: string | null): WalkthroughDocument {
  if (doc.steps.some((s) => s.id === step.id)) return doc;
  const at = stepIndex(doc, afterId);
  const steps = [...doc.steps];
  steps.splice(at < 0 ? steps.length : at + 1, 0, normaliseStep(step));
  return { ...doc, steps };
}

/** The step moved one place up (-1) or down (+1); the document as it was at either end. */
export function moveStep(doc: WalkthroughDocument, sid: string, delta: -1 | 1): WalkthroughDocument {
  const from = stepIndex(doc, sid);
  if (from < 0) return doc;
  return reorderStep(doc, from, from + delta);
}

/** The step at `from` placed at `to` (a drag); the document as it was for a place that is not there. */
export function reorderStep(doc: WalkthroughDocument, from: number, to: number): WalkthroughDocument {
  const n = doc.steps.length;
  if (from < 0 || from >= n || to < 0 || to >= n || from === to) return doc;
  const steps = [...doc.steps];
  const [moved] = steps.splice(from, 1);
  steps.splice(to, 0, moved);
  return { ...doc, steps };
}

export function setHotspot(doc: WalkthroughDocument, sid: string, hotspot: Hotspot | null): WalkthroughDocument {
  return updateStep(doc, sid, { hotspot: hotspot ? { ...hotspot } : null });
}

/**
 * The hotspot kept inside a picture of `size`: moved in when it hangs over an edge, shrunk when it is larger
 * than the picture, dropped (null) when the picture cannot hold MIN_HOTSPOT_SIDE a side - the server's
 * `clamp_hotspot` (services/walkthroughs.py), applied here to the builder's own hotspot when Replace picture
 * brings a smaller picture, so an unsaved hotspot is kept too.
 */
export function clampHotspot(hotspot: Hotspot | null, size: Size): Hotspot | null {
  if (!hotspot) return null;
  if (size.width < MIN_HOTSPOT_SIDE || size.height < MIN_HOTSPOT_SIDE) return null;
  const width = Math.max(MIN_HOTSPOT_SIDE, Math.min(Math.round(hotspot.width), size.width));
  const height = Math.max(MIN_HOTSPOT_SIDE, Math.min(Math.round(hotspot.height), size.height));
  const x = Math.max(0, Math.min(Math.round(hotspot.x), size.width - width));
  const y = Math.max(0, Math.min(Math.round(hotspot.y), size.height - height));
  return { x, y, width, height };
}

/**
 * The hotspot Add hotspot makes (F2): `HOTSPOT_DEFAULT` centred on a picture of `size`, kept inside it by the
 * one clamp (a side larger than the picture takes the picture's); null for a picture that cannot hold one.
 */
export function centredHotspot(size: Size): Hotspot | null {
  const width = Math.min(HOTSPOT_DEFAULT.width, size.width), height = Math.min(HOTSPOT_DEFAULT.height, size.height);
  return clampHotspot({ x: Math.round((size.width - width) / 2), y: Math.round((size.height - height) / 2), width, height }, size);
}

/** A tooltip step without a hotspot has nothing to sit beside: it is shown as a modal (the guide says so). */
export function shownAsModal(step: Pick<Step, "kind" | "hotspot">): boolean {
  return step.kind === "modal" || !step.hotspot;
}

// ---- chapters and navigation ---------------------------------------------------------------------------

export interface Chapter { name: string; index: number; stepId: string }

/** The chapters, in order: every step whose chapter name is not empty starts one at its index. */
export function chapters(doc: Pick<WalkthroughDocument, "steps">): Chapter[] {
  const out: Chapter[] = [];
  doc.steps.forEach((s, index) => {
    const name = (s.chapter || "").trim();
    if (name) out.push({ name, index, stepId: s.id });
  });
  return out;
}

export function chapterCount(doc: Pick<WalkthroughDocument, "steps">): number {
  return chapters(doc).length;
}

/** The chapter the step at `index` belongs to: the latest one starting at or before it; null before the first. */
export function chapterAt(doc: Pick<WalkthroughDocument, "steps">, index: number): Chapter | null {
  let found: Chapter | null = null;
  for (const c of chapters(doc)) {
    if (c.index <= index) found = c;
    else break;
  }
  return found;
}

export function clampIndex(index: number, count: number): number {
  if (count <= 0) return 0;
  const n = Number.isFinite(index) ? Math.trunc(index) : 0;
  return Math.max(0, Math.min(count - 1, n));
}

/** The step after `index`, or null at the last one (the player then shows its end card). */
export function nextIndex(index: number, count: number): number | null {
  return index + 1 < count ? index + 1 : null;
}

export function backIndex(index: number): number {
  return Math.max(0, index - 1);
}

export function homeIndex(): number {
  return 0;
}

export function endIndex(count: number): number {
  return Math.max(0, count - 1);
}

/** Where the chapter named `name` starts, or -1. */
export function chapterStart(doc: Pick<WalkthroughDocument, "steps">, name: string): number {
  return chapters(doc).find((c) => c.name === name)?.index ?? -1;
}

export function stepLabel(index: number, count: number): string {
  return count > 0 ? `Step ${index + 1} of ${count}` : "No steps";
}

/** "12 steps · 3 chapters", with the singulars; no chapter part when there are none. */
export function countsLine(stepCount: number, chapterCount: number): string {
  const steps = `${stepCount} step${stepCount === 1 ? "" : "s"}`;
  if (chapterCount <= 0) return steps;
  return `${steps} · ${chapterCount} chapter${chapterCount === 1 ? "" : "s"}`;
}

// ---- the player's geometry ------------------------------------------------------------------------------

/** The picture's displayed rectangle: fitted (contain) and centred in the container, with the scale used. */
export interface Fit extends Rect { scale: number }

/** The picture of `natural` size fitted inside `container`: contain, centred; a zero container fits nothing. */
export function fitRect(natural: Size, container: Size): Fit {
  const nw = Math.max(1, natural.width), nh = Math.max(1, natural.height);
  const cw = Math.max(0, container.width), ch = Math.max(0, container.height);
  const scale = Math.min(cw / nw, ch / nh);
  if (!(scale > 0) || !Number.isFinite(scale)) return { x: 0, y: 0, width: 0, height: 0, scale: 0 };
  const width = nw * scale, height = nh * scale;
  return { x: (cw - width) / 2, y: (ch - height) / 2, width, height, scale };
}

/** A rectangle in the picture's pixels, where it is on the screen under `fit`. */
export function displayedRect(r: Rect, fit: Fit): Rect {
  return { x: fit.x + r.x * fit.scale, y: fit.y + r.y * fit.scale, width: r.width * fit.scale, height: r.height * fit.scale };
}

export interface TooltipPosition { x: number; y: number; side: Side }

/**
 * Where a tooltip of `tip` size goes beside the displayed hotspot `target`, inside the `container`: on the
 * side asked, or - for `auto` - the first of below, right, above, left with room for it (else the side with
 * the most room), centred on the hotspot along that side, and then kept on screen whatever was asked.
 */
export function tooltipPlacement(target: Rect, tip: Size, container: Size, placement: Placement, gap = 12, margin = 8): TooltipPosition {
  const room: Record<Side, number> = {
    top: target.y, bottom: container.height - (target.y + target.height),
    left: target.x, right: container.width - (target.x + target.width),
  };
  const needs: Record<Side, number> = {
    top: tip.height + gap + margin, bottom: tip.height + gap + margin, left: tip.width + gap + margin, right: tip.width + gap + margin,
  };
  let side: Side;
  if (placement === "auto") {
    const order: Side[] = ["bottom", "right", "top", "left"];
    side = order.find((s) => room[s] >= needs[s]) ?? order.reduce((best, s) => (room[s] - needs[s] > room[best] - needs[best] ? s : best), order[0]);
  } else {
    side = placement;
  }
  let x: number, y: number;
  if (side === "top" || side === "bottom") {
    x = target.x + target.width / 2 - tip.width / 2;
    y = side === "top" ? target.y - gap - tip.height : target.y + target.height + gap;
  } else {
    y = target.y + target.height / 2 - tip.height / 2;
    x = side === "left" ? target.x - gap - tip.width : target.x + target.width + gap;
  }
  const clamp = (v: number, size: number, extent: number) => Math.max(margin, Math.min(Math.max(margin, extent - size - margin), v));
  return { x: Math.round(clamp(x, tip.width, container.width)), y: Math.round(clamp(y, tip.height, container.height)), side };
}

// ---- make a video (W3) -----------------------------------------------------------------------------------

export const SAVE_BEFORE_VIDEO = {
  title: "Save before making the video?",
  message: "The video is made from the walkthrough as it is saved. Save the walkthrough now, then make the video?",
  confirmLabel: "Save and make",
};
export const NO_NOTES = "No step has a note.";
export const NO_HOTSPOTS = "No step has a hotspot.";

/** The body of `POST /api/walkthroughs/{wid}/video`: the project's name, the notes narrated, the zoom. */
export interface MakeVideoBody { name: string; notes: boolean; zoom: boolean }
/** Its answer: the project made and its generate job, already running. */
export interface MadeVideo { project_id: string; job_id: string }

export function makeVideoUrl(wid: string): string {
  return `/api/walkthroughs/${wid}/video`;
}

/** Whether any step carries a note (the guide's "Under the hood" hint): the dialog's notes box is for those. */
export function hasNotes(doc: Pick<WalkthroughDocument, "steps">): boolean {
  return doc.steps.some((s) => (s.note ?? "").trim() !== "");
}

/** Whether any step has a hotspot: the dialog's zoom box is for those. */
export function hasHotspots(doc: Pick<WalkthroughDocument, "steps">): boolean {
  return doc.steps.some((s) => !!s.hotspot);
}

/** "12 steps, 2 without a picture, 1 without any text" - what the video will be made of; the parts that are 0 left out. */
export function stepsLine(doc: Pick<WalkthroughDocument, "steps">): string {
  const n = doc.steps.length;
  const noPicture = doc.steps.filter((s) => !hasPicture(s)).length;
  const noText = doc.steps.filter((s) => !narrationOf(s).trim()).length;
  const parts = [`${n} step${n === 1 ? "" : "s"}`];
  if (noPicture) parts.push(`${noPicture} without a picture`);
  if (noText) parts.push(`${noText} without any text`);
  return parts.join(", ");
}

/** A row of `GET /api/projects` as the dialog reads it: a project made from a walkthrough names it. */
export interface MadeProject { id: string; name: string; kind: string; created_at: string; walkthrough_id?: string | null }

/** The projects made from the walkthrough `wid`, newest first. */
export function madeFrom(projects: MadeProject[], wid: string): MadeProject[] {
  return projects
    .filter((p) => p.kind === "walkthrough" && p.walkthrough_id === wid)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
}

/** When a project was made, as "2026-10-22 14:02" in local time; "" for no date. */
export function madeWhen(value: string | null | undefined): string {
  const d = parseDate(value);
  if (!d) return "";
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

export function projectUrl(pid: string): string {
  return `/projects/${pid}`;
}

// ---- keys and urls --------------------------------------------------------------------------------------

/** Whether a key event comes from a box being typed in (the builder's and the player's keys stay out of those). */
export function typingIn(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || !!el.isContentEditable);
}

export const walkthroughsQueryKey = ["walkthroughs"] as const;
/** One record; under the list's key, so invalidating the list refreshes it too. */
export const walkthroughQueryKey = (id: string) => ["walkthroughs", id] as const;

/** A step's picture, cache-busted by the record's revision (a replaced picture bumps it). */
export function stepImageUrl(wid: string, sid: string, revision: number): string {
  return `/api/walkthroughs/${wid}/steps/${sid}/image?v=${revision}`;
}

export function builderUrl(id: string): string {
  return `/walkthroughs/${id}`;
}

export function playUrl(id: string, step?: number): string {
  return step && step > 0 ? `/walkthroughs/${id}/play?step=${step}` : `/walkthroughs/${id}/play`;
}
