/**
 * Annotate a still (T3b): the PURE half of the editor - the document (shapes in the still's own pixel
 * coordinates, plus one optional crop), what the tools make, hit-testing, the handles and the resize
 * maths, step numbering, the crop maths, the undo stack, serialise / parse with a version field, and the
 * RENDERER as a function over a `CanvasRenderingContext2D`-like interface. The page and the surface
 * (`components/annotate/*`) are the wiring; the server (`api/schemas.py AnnotationDocument`) validates
 * exactly the document written here. Unit-tested in annotate.test.ts and annotate.render.test.ts.
 *
 * ONE RENDERER. The editor's view and the saved PNG come from the same function: `drawScene` draws the
 * still and every shape; `renderPreview` calls it and then draws what only the editor shows (the dimmed
 * outside of the crop, the selection's handles); `renderExport` calls it translated by the crop's origin
 * onto a canvas of the crop's size. What the page shows is what is saved, pixel for pixel at 100 %. A
 * canvas rather than an SVG overlay because three of the nine tools are PIXEL operations - the mosaic
 * (Blur), the lens (Magnify, which copies the original's pixels) and the multiply blend (Highlighter) -
 * that an SVG could only approximate with filters whose rasterisation nobody controls, and because the
 * export must rasterise the text with the very font string the preview used (`UI_FONT`): one `ctx.font`,
 * one `measureText`, no serialisation step between the two pictures.
 *
 * BLUR IS A MOSAIC: square cells of `mosaicCell` pixels (1 % of the still's shorter side, at least 8),
 * each one colour. A Gaussian blur can be partly undone; one colour per 8-pixel cell cannot be read back.
 */

// ---- limits and constants ------------------------------------------------------------------------------

export const DOCUMENT_VERSION = 1;
/** Shapes a document may hold (the server holds the same bound, services/captures.py MAX_SHAPES). */
export const MAX_SHAPES = 500;
/** Characters of text in one Text or Callout (the server: MAX_TEXT_CHARS). */
export const MAX_TEXT_CHARS = 500;
/** A still of more pixels than this is not opened (the server refuses a picture past it too: MAX_ANNOTATE_PIXELS). */
export const MAX_ANNOTATE_PIXELS = 100_000_000;
/** A crop's sides, at least (the server: MIN_CROP_SIDE). */
export const MIN_CROP_SIDE = 16;
/** The mosaic's cell: this share of the still's shorter side, never under MIN_MOSAIC_CELL. */
export const MOSAIC_SHARE = 0.01;
export const MIN_MOSAIC_CELL = 8;
/** The lens copies its source at one of these. */
export const LENS_FACTORS = [2, 3] as const;
/** The zoom steps + and - walk; Fit is whatever fills the viewport, 100 % is 1. */
export const ZOOM_STEPS = [0.25, 0.5, 1, 2, 4] as const;
/** The three zooms the toolbar names beside Fit. */
export const ZOOM_PRESETS = [0.5, 1, 2] as const;

/** Eight colours that read on light and dark screens, a Snagit-like red first, then black and white. */
export const PALETTE: { name: string; hex: string }[] = [
  { name: "Red", hex: "#e53935" },
  { name: "Orange", hex: "#fb8c00" },
  { name: "Yellow", hex: "#fdd835" },
  { name: "Green", hex: "#43a047" },
  { name: "Blue", hex: "#1e88e5" },
  { name: "Purple", hex: "#8e24aa" },
  { name: "Pink", hex: "#ec407a" },
  { name: "Teal", hex: "#00acc1" },
  { name: "Black", hex: "#000000" },
  { name: "White", hex: "#ffffff" },
];
export const DEFAULT_COLOUR = "#e53935";
export const DEFAULT_HIGHLIGHT = "#fdd835";
export const HIGHLIGHT_ALPHA = 0.4;
export const STROKE_PX: Record<Stroke, number> = { thin: 2, medium: 4, thick: 7 };
export const TEXT_PX: Record<TextSize, number> = { small: 14, medium: 20, large: 32 };
/** A step's circle follows the stroke width setting. */
export const STEP_RADIUS: Record<Stroke, number> = { thin: 12, medium: 16, thick: 22 };
/** The app's UI font stack (theme.css --font): the preview and the export set the same string. */
export const UI_FONT = '"IBM Plex Sans", "Inter", "Segoe UI", system-ui, -apple-system, sans-serif';
export const LINE_HEIGHT = 1.25;
export const TEXT_PADDING = 6;
export const DEFAULT_TEXT_WIDTH = 240;
/** Screen pixels: a handle's side, and how close a click must come to a line. */
export const HANDLE_PX = 8;
export const HIT_PX = 6;
export const CROP_DIM_ALPHA = 0.55;
export const CALLOUT_RADIUS = 8;
export const TOO_MANY_SHAPES_MESSAGE = `A still can carry at most ${MAX_SHAPES} shapes.`;
/** Said on the Projects page when the editor's still is gone (deleted meanwhile, or somebody else's). */
export const GONE_MESSAGE = "That still is no longer there: it was deleted, or it is not yours.";
/** What leaving with unsaved changes asks. */
export const UNSAVED_ASK = { title: "Leave without saving?", message: "The annotations you made since the last save are lost if you leave now.", confirmLabel: "Leave" };
export const TOO_BIG_MESSAGE = (width: number, height: number) =>
  `This still is ${width} × ${height} pixels, more than the ${MAX_ANNOTATE_PIXELS / 1_000_000} million the editor can hold. Add it to a deck as it is, or take a smaller still.`;

// ---- the document -------------------------------------------------------------------------------------

export type Tool = "select" | "arrow" | "callout" | "text" | "rect" | "ellipse" | "line" | "step" | "blur" | "highlight" | "crop" | "magnify";
export type Stroke = "thin" | "medium" | "thick";
export type TextSize = "small" | "medium" | "large";
export type LensFactor = (typeof LENS_FACTORS)[number];

/** The one shared style, remembered for the session. */
export interface Style {
  colour: string;
  highlight: string;
  stroke: Stroke;
  textSize: TextSize;
  lensFactor: LensFactor;
}
export const DEFAULT_STYLE: Style = { colour: DEFAULT_COLOUR, highlight: DEFAULT_HIGHLIGHT, stroke: "medium", textSize: "medium", lensFactor: 2 };

interface Base { id: string; colour: string; stroke: Stroke }
export interface SegmentShape extends Base { type: "arrow" | "line"; x1: number; y1: number; x2: number; y2: number }
export interface BoxShape extends Base { type: "rect" | "ellipse" | "blur" | "highlight"; x: number; y: number; width: number; height: number }
export interface TextShape extends Base { type: "text"; x: number; y: number; width: number; height: number; text: string; text_size: TextSize }
export interface CalloutShape extends Base {
  type: "callout"; x: number; y: number; width: number; height: number; tail_x: number; tail_y: number; text: string; text_size: TextSize;
}
export interface StepShape extends Base { type: "step"; x: number; y: number; sequence: number }
export interface MagnifyShape extends Base {
  type: "magnify"; x: number; y: number; width: number; height: number; lens_x: number; lens_y: number; factor: LensFactor;
}
export type Shape = SegmentShape | BoxShape | TextShape | CalloutShape | StepShape | MagnifyShape;
export type ShapeType = Shape["type"];

export interface Rect { x: number; y: number; width: number; height: number }
export interface Point { x: number; y: number }
export interface Size { width: number; height: number }
export type Crop = Rect;

export interface AnnotationDocument {
  version: typeof DOCUMENT_VERSION;
  shapes: Shape[];
  crop: Crop | null;
}

export function emptyDocument(): AnnotationDocument {
  return { version: DOCUMENT_VERSION, shapes: [], crop: null };
}

/** The tool keys: V select, A arrow, C callout, T text, R rectangle, E ellipse, L line, S step, B blur, H highlighter, K crop, M magnify. */
export const TOOL_KEYS: Record<string, Tool> = {
  v: "select", a: "arrow", c: "callout", t: "text", r: "rect", e: "ellipse", l: "line", s: "step", b: "blur", h: "highlight", k: "crop", m: "magnify",
};
export const TOOL_LABELS: Record<Tool, string> = {
  select: "Select", arrow: "Arrow", callout: "Callout", text: "Text", rect: "Rectangle", ellipse: "Ellipse", line: "Line",
  step: "Step", blur: "Blur", highlight: "Highlighter", crop: "Crop", magnify: "Magnify",
};
export const TOOL_ORDER: Tool[] = ["select", "arrow", "callout", "text", "rect", "ellipse", "line", "step", "blur", "highlight", "crop", "magnify"];

/** The tool a plain key press (no modifier) selects, or null. */
export function toolForKey(e: { key: string; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean }): Tool | null {
  if (e.ctrlKey || e.altKey || e.metaKey) return null;
  return TOOL_KEYS[e.key.toLowerCase()] ?? null;
}

let counter = 0;
/** A shape id: unique within a session, and in the server's shape (letters, digits, `_` and `-`). */
export function shapeId(now: number = Date.now()): string {
  counter = (counter + 1) % 1_679_616; // 36^4
  return `${now.toString(36)}-${counter.toString(36)}`;
}

export function normalise(a: Point, b: Point): Rect {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) };
}

export function mosaicCell(still: Size): number {
  return Math.max(MIN_MOSAIC_CELL, Math.round(Math.min(still.width, still.height) * MOSAIC_SHARE));
}

export function lensRect(s: MagnifyShape): Rect {
  return { x: s.lens_x, y: s.lens_y, width: s.width * s.factor, height: s.height * s.factor };
}

/** The export's size: the crop's when there is one, else the still's. */
export function outputSize(doc: AnnotationDocument, still: Size): Size {
  return doc.crop ? { width: doc.crop.width, height: doc.crop.height } : { width: still.width, height: still.height };
}

export function canAddShape(doc: AnnotationDocument): boolean {
  return doc.shapes.length < MAX_SHAPES;
}

/**
 * The fields measured along the still's width and its height, per kind: field for field the server's
 * `api/schemas.py` X_FIELDS / Y_FIELDS (`_Shape._within_the_still`). The server refuses any of them outside
 * -size .. 2 x size (a width or a height also under 1); `clampShape` keeps every one of them inside, applied by
 * everything that makes or changes a shape, so the page never hands the server a document it refuses.
 */
export const X_FIELDS: Record<ShapeType, string[]> = {
  arrow: ["x1", "x2"], line: ["x1", "x2"], rect: ["x", "width"], ellipse: ["x", "width"], blur: ["x", "width"], highlight: ["x", "width"],
  text: ["x", "width"], callout: ["x", "width", "tail_x"], step: ["x"], magnify: ["x", "width", "lens_x"],
};
export const Y_FIELDS: Record<ShapeType, string[]> = {
  arrow: ["y1", "y2"], line: ["y1", "y2"], rect: ["y", "height"], ellipse: ["y", "height"], blur: ["y", "height"], highlight: ["y", "height"],
  text: ["y", "height"], callout: ["y", "height", "tail_y"], step: ["y"], magnify: ["y", "height", "lens_y"],
};
const LENGTHS = ["width", "height"];

/** Whether every number the server checks is inside its range: what `api/schemas.py` would accept. */
export function withinStill(s: Shape, still: Size): boolean {
  const r = s as unknown as Record<string, number>;
  const ok = (names: string[], size: number) =>
    names.every((n) => Number.isFinite(r[n]) && r[n] >= (LENGTHS.includes(n) ? 1 : -size) && r[n] <= 2 * size);
  return ok(X_FIELDS[s.type], still.width) && ok(Y_FIELDS[s.type], still.height);
}

/** `s` with every field the server checks brought inside -size .. 2 x size (a length also at least 1). */
export function clampShape<T extends Shape>(s: T, still: Size): T {
  const out = { ...s } as unknown as Record<string, unknown>;
  const clampTo = (names: string[], size: number) => {
    for (const n of names) {
      const v = out[n] as number;
      out[n] = Math.max(LENGTHS.includes(n) ? 1 : -size, Math.min(2 * size, v));
    }
  };
  clampTo(X_FIELDS[s.type], still.width);
  clampTo(Y_FIELDS[s.type], still.height);
  return out as unknown as T;
}

// ---- what the tools make ------------------------------------------------------------------------------

/**
 * The shape a DRAG makes with a drawing tool, or null when the drag is too short to mean anything
 * (under 2 px). Text, Callout and Step are placed by a click (`placeShape`); Crop is `cropFromDrag`.
 */
export function shapeFromDrag(tool: Tool, start: Point, end: Point, style: Style, still: Size, id: string = shapeId()): Shape | null {
  const base = { id, colour: style.colour, stroke: style.stroke };
  if (tool === "arrow" || tool === "line") {
    if (Math.hypot(end.x - start.x, end.y - start.y) < 2) return null;
    return clampShape({ ...base, type: tool, x1: start.x, y1: start.y, x2: end.x, y2: end.y }, still);
  }
  const r = normalise(start, end);
  if (r.width < 2 || r.height < 2) return null;
  if (tool === "rect" || tool === "ellipse" || tool === "blur") return clampShape({ ...base, type: tool, ...r }, still);
  if (tool === "highlight") return clampShape({ ...base, type: "highlight", colour: style.highlight, ...r }, still);
  if (tool === "magnify") {
    const factor = style.lensFactor;
    // The lens sits to the right of its source when there is room, else to the left, else below.
    const gap = 16;
    let lens_x = r.x + r.width + gap, lens_y = r.y;
    if (lens_x + r.width * factor > still.width) lens_x = r.x - gap - r.width * factor;
    if (lens_x < 0) { lens_x = r.x; lens_y = r.y + r.height + gap; }
    return clampShape({ ...base, type: "magnify", ...r, lens_x, lens_y, factor }, still);
  }
  return null;
}

/** How wide a line of text is, in the still's pixels, in the font the renderer uses (`ctx.measureText`). */
export type Measure = (text: string, font: string) => number;

export function fontFor(px: number, weight: number | string = 500): string {
  return `${weight} ${px}px ${UI_FONT}`;
}

/** The lines `text` wraps to inside `maxWidth`: on spaces, and a word too long for the width on characters. */
export function wrapText(text: string, maxWidth: number, font: string, measure: Measure): string[] {
  const out: string[] = [];
  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(" ");
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (measure(candidate, font) <= maxWidth || !line) {
        if (measure(candidate, font) <= maxWidth) { line = candidate; continue; }
        // A single word wider than the box: break it by characters.
        let piece = "";
        for (const ch of word) {
          if (measure(piece + ch, font) > maxWidth && piece) { out.push(piece); piece = ""; }
          piece += ch;
        }
        line = piece;
      } else {
        out.push(line);
        line = word;
        if (measure(line, font) > maxWidth) {
          let piece = "";
          for (const ch of word) {
            if (measure(piece + ch, font) > maxWidth && piece) { out.push(piece); piece = ""; }
            piece += ch;
          }
          line = piece;
        }
      }
    }
    out.push(line);
  }
  return out;
}

/**
 * The height a text box needs for `text` at `size` and `width`: its wrapped lines plus the padding - capped
 * at 2 x the still's height when `still` is given (the server's bound on a height; the box clips beyond it).
 */
export function textBoxHeight(text: string, size: TextSize, width: number, measure: Measure, still?: Size): number {
  const px = TEXT_PX[size];
  const lines = wrapText(text || " ", Math.max(1, width - 2 * TEXT_PADDING), fontFor(px), measure);
  const height = Math.ceil(lines.length * px * LINE_HEIGHT + 2 * TEXT_PADDING);
  return still ? Math.min(height, 2 * still.height) : height;
}

/** A click places a Text, a Callout or a Step at `p`. */
export function placeShape(tool: Tool, p: Point, style: Style, measure: Measure, sequence: number, still: Size, id: string = shapeId()): Shape | null {
  const base = { id, colour: style.colour, stroke: style.stroke };
  if (tool === "step") return clampShape({ ...base, type: "step", x: p.x, y: p.y, sequence }, still);
  if (tool === "text" || tool === "callout") {
    const width = DEFAULT_TEXT_WIDTH;
    const height = textBoxHeight("", style.textSize, width, measure, still);
    if (tool === "text") return clampShape({ ...base, type: "text", x: p.x, y: p.y, width, height, text: "", text_size: style.textSize }, still);
    return clampShape({ ...base, type: "callout", x: p.x, y: p.y, width, height, tail_x: p.x - 24, tail_y: p.y + height + 32, text: "", text_size: style.textSize }, still);
  }
  return null;
}

/** The crop a drag makes, clamped to the still and in whole pixels; null under MIN_CROP_SIDE a side. */
export function cropFromDrag(start: Point, end: Point, still: Size): Crop | null {
  return clampCrop(normalise(start, end), still);
}

export function clampCrop(r: Rect, still: Size): Crop | null {
  const x0 = Math.max(0, Math.round(r.x)), y0 = Math.max(0, Math.round(r.y));
  const x1 = Math.min(still.width, Math.round(r.x + r.width)), y1 = Math.min(still.height, Math.round(r.y + r.height));
  if (x1 - x0 < MIN_CROP_SIDE || y1 - y0 < MIN_CROP_SIDE) return null;
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** The part of `a` inside `b`, or null when they do not meet: a shape outside the crop is clipped, never deleted. */
export function clipRect(a: Rect, b: Rect): Rect | null {
  const x0 = Math.max(a.x, b.x), y0 = Math.max(a.y, b.y);
  const x1 = Math.min(a.x + a.width, b.x + b.width), y1 = Math.min(a.y + a.height, b.y + b.height);
  if (x1 <= x0 || y1 <= y0) return null;
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

// ---- geometry: bounds, hit-tests, handles, moves and resizes ---------------------------------------------

export function bounds(s: Shape): Rect {
  switch (s.type) {
    case "arrow":
    case "line":
      return normalise({ x: s.x1, y: s.y1 }, { x: s.x2, y: s.y2 });
    case "step": {
      const r = STEP_RADIUS[s.stroke];
      return { x: s.x - r, y: s.y - r, width: 2 * r, height: 2 * r };
    }
    case "callout":
      return union({ x: s.x, y: s.y, width: s.width, height: s.height }, { x: s.tail_x, y: s.tail_y, width: 0, height: 0 });
    case "magnify":
      return union({ x: s.x, y: s.y, width: s.width, height: s.height }, lensRect(s));
    default:
      return { x: s.x, y: s.y, width: s.width, height: s.height };
  }
}

export function union(a: Rect, b: Rect): Rect {
  const x0 = Math.min(a.x, b.x), y0 = Math.min(a.y, b.y);
  const x1 = Math.max(a.x + a.width, b.x + b.width), y1 = Math.max(a.y + a.height, b.y + b.height);
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function inRect(p: Point, r: Rect, tol = 0): boolean {
  return p.x >= r.x - tol && p.x <= r.x + r.width + tol && p.y >= r.y - tol && p.y <= r.y + r.height + tol;
}

/** The distance from `p` to the segment a-b. */
export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/** Whether `p` is on `s` (within `tol` still-pixels of a line or an edge). */
export function hits(s: Shape, p: Point, tol: number): boolean {
  switch (s.type) {
    case "arrow":
    case "line":
      return distanceToSegment(p, { x: s.x1, y: s.y1 }, { x: s.x2, y: s.y2 }) <= tol + STROKE_PX[s.stroke] / 2;
    case "step":
      return Math.hypot(p.x - s.x, p.y - s.y) <= STEP_RADIUS[s.stroke] + tol;
    case "callout":
      return inRect(p, { x: s.x, y: s.y, width: s.width, height: s.height }, tol) || Math.hypot(p.x - s.tail_x, p.y - s.tail_y) <= 2 * tol;
    case "magnify":
      return inRect(p, { x: s.x, y: s.y, width: s.width, height: s.height }, tol) || inRect(p, lensRect(s), tol);
    default:
      return inRect(p, { x: s.x, y: s.y, width: s.width, height: s.height }, tol);
  }
}

/** The topmost shape under `p` (the last drawn wins), or null. */
export function hitTest(doc: AnnotationDocument, p: Point, tol: number): string | null {
  for (let i = doc.shapes.length - 1; i >= 0; i--) {
    if (hits(doc.shapes[i], p, tol)) return doc.shapes[i].id;
  }
  return null;
}

/** Whether `p` is on the LENS of a magnify shape (dragging it moves the lens alone). */
export function hitsLens(s: Shape, p: Point, tol: number): boolean {
  return s.type === "magnify" && inRect(p, lensRect(s), tol);
}

export type HandleName = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "start" | "end" | "tail";
export interface Handle { name: HandleName; x: number; y: number }

function boxHandles(r: Rect): Handle[] {
  const cx = r.x + r.width / 2, cy = r.y + r.height / 2, x1 = r.x + r.width, y1 = r.y + r.height;
  return [
    { name: "nw", x: r.x, y: r.y }, { name: "n", x: cx, y: r.y }, { name: "ne", x: x1, y: r.y }, { name: "e", x: x1, y: cy },
    { name: "se", x: x1, y: y1 }, { name: "s", x: cx, y: y1 }, { name: "sw", x: r.x, y: y1 }, { name: "w", x: r.x, y: cy },
  ];
}

/** The handles a selected shape shows: a box's eight, a line's two ends, a callout's tail besides its box; a step has none (it is moved). */
export function handles(s: Shape): Handle[] {
  switch (s.type) {
    case "arrow":
    case "line":
      return [{ name: "start", x: s.x1, y: s.y1 }, { name: "end", x: s.x2, y: s.y2 }];
    case "step":
      return [];
    case "callout":
      return [...boxHandles({ x: s.x, y: s.y, width: s.width, height: s.height }), { name: "tail", x: s.tail_x, y: s.tail_y }];
    default:
      return boxHandles({ x: s.x, y: s.y, width: s.width, height: s.height });
  }
}

export function cropHandles(crop: Crop): Handle[] {
  return boxHandles(crop);
}

/** The handle of `list` under `p`, within `tol`, or null. */
export function handleAt(list: Handle[], p: Point, tol: number): HandleName | null {
  for (const h of list) if (Math.abs(p.x - h.x) <= tol && Math.abs(p.y - h.y) <= tol) return h.name;
  return null;
}

/** `s` moved by (dx, dy), every field kept inside the still's range (`clampShape`). */
export function moveShape(s: Shape, dx: number, dy: number, still: Size): Shape {
  switch (s.type) {
    case "arrow":
    case "line":
      return clampShape({ ...s, x1: s.x1 + dx, y1: s.y1 + dy, x2: s.x2 + dx, y2: s.y2 + dy }, still);
    case "step":
      return clampShape({ ...s, x: s.x + dx, y: s.y + dy }, still);
    case "callout":
      return clampShape({ ...s, x: s.x + dx, y: s.y + dy, tail_x: s.tail_x + dx, tail_y: s.tail_y + dy }, still);
    case "magnify":
      return clampShape({ ...s, x: s.x + dx, y: s.y + dy, lens_x: s.lens_x + dx, lens_y: s.lens_y + dy }, still);
    default:
      return clampShape({ ...s, x: s.x + dx, y: s.y + dy }, still);
  }
}

export function moveLens(s: MagnifyShape, dx: number, dy: number, still: Size): MagnifyShape {
  return clampShape({ ...s, lens_x: s.lens_x + dx, lens_y: s.lens_y + dy }, still);
}

function resizeRect(r: Rect, handle: HandleName, p: Point, min = 1): Rect {
  let x0 = r.x, y0 = r.y, x1 = r.x + r.width, y1 = r.y + r.height;
  if (handle.includes("w")) x0 = p.x;
  if (handle.includes("e")) x1 = p.x;
  if (handle.includes("n") && handle !== "ne" && handle !== "nw") y0 = p.y;
  if (handle === "ne" || handle === "nw") y0 = p.y;
  if (handle.includes("s") && handle !== "se" && handle !== "sw") y1 = p.y;
  if (handle === "se" || handle === "sw") y1 = p.y;
  const out = normalise({ x: x0, y: y0 }, { x: x1, y: y1 });
  return { ...out, width: Math.max(min, out.width), height: Math.max(min, out.height) };
}

/** `s` with `handle` dragged to `p`; a box dragged past its opposite edge flips, never inverts; every field kept inside the still's range. */
export function resizeShape(s: Shape, handle: HandleName, p: Point, still: Size): Shape {
  switch (s.type) {
    case "arrow":
    case "line":
      return clampShape(handle === "start" ? { ...s, x1: p.x, y1: p.y } : handle === "end" ? { ...s, x2: p.x, y2: p.y } : s, still);
    case "step":
      return s;
    case "callout":
      if (handle === "tail") return clampShape({ ...s, tail_x: p.x, tail_y: p.y }, still);
      return clampShape({ ...s, ...resizeRect({ x: s.x, y: s.y, width: s.width, height: s.height }, handle, p, 8) }, still);
    default:
      if (handle === "start" || handle === "end" || handle === "tail") return s;
      return clampShape({ ...s, ...resizeRect({ x: s.x, y: s.y, width: s.width, height: s.height }, handle, p, s.type === "text" ? 8 : 1) }, still);
  }
}

/** The crop with `handle` dragged to `p`, kept inside the still; the old crop when the drag would make it too small. */
export function resizeCrop(crop: Crop, handle: HandleName, p: Point, still: Size): Crop {
  const r = resizeRect(crop, handle, { x: Math.max(0, Math.min(still.width, p.x)), y: Math.max(0, Math.min(still.height, p.y)) }, 1);
  return clampCrop(r, still) ?? crop;
}

export function moveCrop(crop: Crop, dx: number, dy: number, still: Size): Crop {
  const x = Math.max(0, Math.min(still.width - crop.width, Math.round(crop.x + dx)));
  const y = Math.max(0, Math.min(still.height - crop.height, Math.round(crop.y + dy)));
  return { ...crop, x, y };
}

// ---- the document's edits ---------------------------------------------------------------------------------

export function addShape(doc: AnnotationDocument, s: Shape): AnnotationDocument {
  return { ...doc, shapes: [...doc.shapes, s] };
}

export function updateShape(doc: AnnotationDocument, id: string, next: Shape | ((s: Shape) => Shape)): AnnotationDocument {
  return { ...doc, shapes: doc.shapes.map((s) => (s.id === id ? (typeof next === "function" ? next(s) : next) : s)) };
}

export function deleteShape(doc: AnnotationDocument, id: string): AnnotationDocument {
  return { ...doc, shapes: doc.shapes.filter((s) => s.id !== id) };
}

export function shapeById(doc: AnnotationDocument, id: string | null): Shape | null {
  return id ? doc.shapes.find((s) => s.id === id) ?? null : null;
}

export function setCrop(doc: AnnotationDocument, crop: Crop | null): AnnotationDocument {
  return { ...doc, crop };
}

/**
 * Step numbers are automatic: a step's number is its place among the steps of its SEQUENCE in document
 * order, so deleting one renumbers the rest, and Restart numbering (a new sequence) starts at 1 again.
 */
export function stepNumbers(doc: AnnotationDocument): Map<string, number> {
  const counts = new Map<number, number>();
  const out = new Map<string, number>();
  for (const s of doc.shapes) {
    if (s.type !== "step") continue;
    const n = (counts.get(s.sequence) ?? 0) + 1;
    counts.set(s.sequence, n);
    out.set(s.id, n);
  }
  return out;
}

/** The sequence a new step joins: the latest one in the document (0 when there is none). */
export function currentSequence(doc: AnnotationDocument): number {
  let latest = 0;
  for (const s of doc.shapes) if (s.type === "step" && s.sequence > latest) latest = s.sequence;
  return latest;
}

/** Restart numbering: the next step starts a new sequence at 1. */
export function restartSequence(doc: AnnotationDocument): number {
  return doc.shapes.some((s) => s.type === "step") ? currentSequence(doc) + 1 : 0;
}

// ---- undo / redo -----------------------------------------------------------------------------------------

export interface History<T> { past: T[]; present: T; future: T[] }

export function history<T>(present: T): History<T> {
  return { past: [], present, future: [] };
}

/**
 * A new present; the old one joins the past and the future is forgotten. Unlimited within the session. A
 * document EQUAL in content to the present (a click that moved nothing, a drag too small to crop, a style
 * pick that applied to nothing) is no change and adds no step - compared as the server would store it
 * (`serialise`), never by reference.
 */
export function commit<T>(h: History<T>, next: T): History<T> {
  if (next === h.present || sameContent(next, h.present)) return h;
  return { past: [...h.past, h.present], present: next, future: [] };
}

function sameContent(a: unknown, b: unknown): boolean {
  const doc = (v: unknown): v is AnnotationDocument => !!v && typeof v === "object" && Array.isArray((v as AnnotationDocument).shapes);
  return doc(a) && doc(b) ? serialise(a) === serialise(b) : false;
}

export function undo<T>(h: History<T>): History<T> {
  if (h.past.length === 0) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function redo<T>(h: History<T>): History<T> {
  if (h.future.length === 0) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

export const canUndo = <T,>(h: History<T>) => h.past.length > 0;
export const canRedo = <T,>(h: History<T>) => h.future.length > 0;

// ---- serialise / parse -----------------------------------------------------------------------------------

const round = (v: number) => Math.round(v * 100) / 100;

/** The document as the server stores it: version 1, the shapes with their numbers rounded to a hundredth of a pixel, the crop in whole pixels. */
export function serialise(doc: AnnotationDocument): string {
  const shapes = doc.shapes.map((s) => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(s)) out[k] = typeof v === "number" ? round(v) : v;
    return out;
  });
  const crop = doc.crop ? { x: Math.round(doc.crop.x), y: Math.round(doc.crop.y), width: Math.round(doc.crop.width), height: Math.round(doc.crop.height) } : null;
  return JSON.stringify({ version: DOCUMENT_VERSION, shapes, crop });
}

const NUMBERS: Record<ShapeType, string[]> = {
  arrow: ["x1", "y1", "x2", "y2"], line: ["x1", "y1", "x2", "y2"],
  rect: ["x", "y", "width", "height"], ellipse: ["x", "y", "width", "height"], blur: ["x", "y", "width", "height"], highlight: ["x", "y", "width", "height"],
  text: ["x", "y", "width", "height"], callout: ["x", "y", "width", "height", "tail_x", "tail_y"],
  step: ["x", "y", "sequence"], magnify: ["x", "y", "width", "height", "lens_x", "lens_y", "factor"],
};
const STROKES: Stroke[] = ["thin", "medium", "thick"];
const SIZES: TextSize[] = ["small", "medium", "large"];

/**
 * A stored document (a JSON string, or the object the record carries) as an AnnotationDocument. Throws a
 * plain Error for anything this release cannot read: another version first (so an older app says so
 * rather than drawing half a document), then a shape of an unknown type or without its numbers.
 */
export function parseDocument(value: unknown): AnnotationDocument {
  const raw = typeof value === "string" ? JSON.parse(value) : value;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("The annotation document is not an object.");
  const obj = raw as Record<string, unknown>;
  if (obj.version !== DOCUMENT_VERSION) {
    throw new Error(`This annotation document is version ${String(obj.version)}; this app reads version ${DOCUMENT_VERSION}. Update the app to open it.`);
  }
  if (!Array.isArray(obj.shapes)) throw new Error("The annotation document has no list of shapes.");
  if (obj.shapes.length > MAX_SHAPES) throw new Error(TOO_MANY_SHAPES_MESSAGE);
  const shapes: Shape[] = obj.shapes.map((item, i) => {
    if (!item || typeof item !== "object") throw new Error(`Shape ${i + 1} is not an object.`);
    const s = item as Record<string, unknown>;
    const type = s.type as ShapeType;
    if (!(type in NUMBERS)) throw new Error(`Shape ${i + 1} is of an unknown kind "${String(s.type)}".`);
    if (typeof s.id !== "string" || !s.id) throw new Error(`Shape ${i + 1} has no id.`);
    if (typeof s.colour !== "string" || !/^#[0-9a-fA-F]{6}$/.test(s.colour)) throw new Error(`Shape ${i + 1} has no colour.`);
    const stroke = STROKES.includes(s.stroke as Stroke) ? (s.stroke as Stroke) : "medium";
    for (const key of NUMBERS[type]) {
      if (typeof s[key] !== "number" || !Number.isFinite(s[key] as number)) throw new Error(`Shape ${i + 1} (${type}) has no ${key}.`);
    }
    const out: Record<string, unknown> = { id: s.id, type, colour: s.colour, stroke };
    for (const key of NUMBERS[type]) out[key] = s[key];
    if (type === "text" || type === "callout") {
      if (typeof s.text !== "string") throw new Error(`Shape ${i + 1} (${type}) has no text.`);
      out.text = s.text.slice(0, MAX_TEXT_CHARS);
      out.text_size = SIZES.includes(s.text_size as TextSize) ? s.text_size : "medium";
    }
    if (type === "magnify" && !(LENS_FACTORS as readonly number[]).includes(out.factor as number)) out.factor = 2;
    return out as unknown as Shape;
  });
  let crop: Crop | null = null;
  if (obj.crop !== null && obj.crop !== undefined) {
    const c = obj.crop as Record<string, unknown>;
    for (const key of ["x", "y", "width", "height"]) {
      if (typeof c?.[key] !== "number" || !Number.isFinite(c[key] as number)) throw new Error("The crop has no " + key + ".");
    }
    crop = { x: c.x as number, y: c.y as number, width: c.width as number, height: c.height as number };
  }
  return { version: DOCUMENT_VERSION, shapes, crop };
}

// ---- zoom ------------------------------------------------------------------------------------------------

/** The zoom that fits the whole still in the viewport, never above 1 (a small still is not blown up). */
export function fitZoom(viewport: Size, still: Size, margin = 24): number {
  const w = Math.max(1, viewport.width - margin), h = Math.max(1, viewport.height - margin);
  const z = Math.min(w / Math.max(1, still.width), h / Math.max(1, still.height), 1);
  return Math.max(0.05, Math.round(z * 1000) / 1000);
}

export function zoomIn(z: number): number {
  return ZOOM_STEPS.find((step) => step > z + 1e-9) ?? ZOOM_STEPS[ZOOM_STEPS.length - 1];
}

export function zoomOut(z: number): number {
  const below = [...ZOOM_STEPS].reverse().find((step) => step < z - 1e-9);
  return below ?? ZOOM_STEPS[0];
}

export function zoomLabel(z: number): string {
  return `${Math.round(z * 100)} %`;
}

/** A 2D canvas's backing store: at most this a side, and this many pixels (half Chromium's documented 268 M). */
export const MAX_CANVAS_SIDE = 16_384;
export const MAX_CANVAS_PIXELS = 128_000_000;

/**
 * The scale the preview's backing store is drawn at: the largest scale no more than zoom x dpr that keeps
 * each side within MAX_CANVAS_SIDE and the area within MAX_CANVAS_PIXELS. The CSS size stays still x zoom,
 * so a 4K still at 400 % on a 1.5x display is shown at the size asked for, drawn at the scale the canvas can
 * hold rather than going blank.
 */
export function backingScale(still: Size, zoom: number, dpr: number): number {
  const w = Math.max(1, still.width), h = Math.max(1, still.height);
  const scale = Math.min(zoom * dpr, MAX_CANVAS_SIDE / w, MAX_CANVAS_SIDE / h, Math.sqrt(MAX_CANVAS_PIXELS / (w * h)));
  return Math.max(0.01, scale);
}

// ---- the renderer ---------------------------------------------------------------------------------------

/** What the renderer asks of a 2D context: the subset it uses, so a recording stub can stand in. */
export interface Ctx2D {
  save(): void; restore(): void; translate(x: number, y: number): void; scale(x: number, y: number): void;
  beginPath(): void; closePath(): void; moveTo(x: number, y: number): void; lineTo(x: number, y: number): void;
  rect(x: number, y: number, w: number, h: number): void;
  ellipse(x: number, y: number, rx: number, ry: number, rotation: number, start: number, end: number): void;
  arc(x: number, y: number, r: number, start: number, end: number): void;
  fill(): void; stroke(): void; clip(): void;
  fillRect(x: number, y: number, w: number, h: number): void; strokeRect(x: number, y: number, w: number, h: number): void;
  setLineDash(segments: number[]): void;
  drawImage(image: ImageLike, ...args: number[]): void;
  fillText(text: string, x: number, y: number): void;
  measureText(text: string): { width: number };
  strokeStyle: string; fillStyle: string; lineWidth: number; lineCap: string; lineJoin: string; font: string;
  textBaseline: string; textAlign: string; globalAlpha: number; globalCompositeOperation: string; imageSmoothingEnabled: boolean;
}
/** A picture the context can draw: the original still, or a scratch canvas. */
export interface ImageLike { width: number; height: number }
/** A scratch canvas for the mosaic: its context and the picture it is, to draw back. */
export interface Scratch { ctx: Ctx2D; image: ImageLike }
export type MakeScratch = (width: number, height: number) => Scratch;

export interface Overlay {
  /** The selected shape's handles. */
  selectedId: string | null;
  /** The crop's handles too (the Crop tool is active). */
  cropHandles: boolean;
  /** Screen pixels per still pixel, so handles keep their screen size. */
  zoom: number;
}

export const measureWith = (ctx: Ctx2D): Measure => (text, font) => { ctx.font = font; return ctx.measureText(text).width; };

/** White or black text, whichever reads on `hex`. */
export function textOn(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return 0.299 * r + 0.587 * g + 0.114 * b > 160 ? "#000000" : "#ffffff";
}

export function arrowHeadLength(stroke: Stroke): number {
  return 6 + 3 * STROKE_PX[stroke];
}

function roundedRect(ctx: Ctx2D, r: Rect, radius: number): void {
  const rad = Math.min(radius, r.width / 2, r.height / 2);
  const x1 = r.x + r.width, y1 = r.y + r.height;
  ctx.beginPath();
  ctx.moveTo(r.x + rad, r.y);
  ctx.lineTo(x1 - rad, r.y);
  ctx.arc(x1 - rad, r.y + rad, rad, -Math.PI / 2, 0);
  ctx.lineTo(x1, y1 - rad);
  ctx.arc(x1 - rad, y1 - rad, rad, 0, Math.PI / 2);
  ctx.lineTo(r.x + rad, y1);
  ctx.arc(r.x + rad, y1 - rad, rad, Math.PI / 2, Math.PI);
  ctx.lineTo(r.x, r.y + rad);
  ctx.arc(r.x + rad, r.y + rad, rad, Math.PI, 1.5 * Math.PI);
  ctx.closePath();
}

function drawTextLines(ctx: Ctx2D, box: Rect, text: string, size: TextSize, colour: string): void {
  const px = TEXT_PX[size];
  ctx.font = fontFor(px);
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  ctx.fillStyle = colour;
  ctx.save();
  ctx.beginPath();
  ctx.rect(box.x, box.y, box.width, box.height);
  ctx.clip();
  const lines = wrapText(text, Math.max(1, box.width - 2 * TEXT_PADDING), ctx.font, measureWith(ctx));
  lines.forEach((line, i) => ctx.fillText(line, box.x + TEXT_PADDING, box.y + TEXT_PADDING + i * px * LINE_HEIGHT));
  ctx.restore();
}

/** Where the ray from the box's centre towards `tip` leaves the box. */
function exitPoint(box: Rect, tip: Point): Point {
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const dx = tip.x - cx, dy = tip.y - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const tx = dx === 0 ? Infinity : (box.width / 2) / Math.abs(dx);
  const ty = dy === 0 ? Infinity : (box.height / 2) / Math.abs(dy);
  const t = Math.min(tx, ty, 1);
  return { x: cx + dx * t, y: cy + dy * t };
}

function drawShape(ctx: Ctx2D, s: Shape, original: ImageLike, still: Size, numbers: Map<string, number>, make: MakeScratch): void {
  const stroke = STROKE_PX[s.stroke];
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = s.colour;
  ctx.fillStyle = s.colour;
  ctx.lineWidth = stroke;
  switch (s.type) {
    case "line": {
      ctx.beginPath();
      ctx.moveTo(s.x1, s.y1);
      ctx.lineTo(s.x2, s.y2);
      ctx.stroke();
      break;
    }
    case "arrow": {
      const len = arrowHeadLength(s.stroke);
      const angle = Math.atan2(s.y2 - s.y1, s.x2 - s.x1);
      const bx = s.x2 - len * Math.cos(angle), by = s.y2 - len * Math.sin(angle);
      ctx.beginPath();
      ctx.moveTo(s.x1, s.y1);
      ctx.lineTo(bx, by);
      ctx.stroke();
      const half = len * 0.5;
      ctx.beginPath();
      ctx.moveTo(s.x2, s.y2);
      ctx.lineTo(bx + half * Math.sin(angle), by - half * Math.cos(angle));
      ctx.lineTo(bx - half * Math.sin(angle), by + half * Math.cos(angle));
      ctx.closePath();
      ctx.fill();
      break;
    }
    case "rect":
      ctx.strokeRect(s.x, s.y, s.width, s.height);
      break;
    case "ellipse":
      ctx.beginPath();
      ctx.ellipse(s.x + s.width / 2, s.y + s.height / 2, s.width / 2, s.height / 2, 0, 0, 2 * Math.PI);
      ctx.stroke();
      break;
    case "highlight":
      // Translucent, multiply: the text under it stays legible, as a highlighter pen leaves it.
      ctx.globalCompositeOperation = "multiply";
      ctx.globalAlpha = HIGHLIGHT_ALPHA;
      ctx.fillRect(s.x, s.y, s.width, s.height);
      break;
    case "blur": {
      // The mosaic: the original's pixels under the box averaged into square cells, drawn back without
      // smoothing - one colour per cell, nothing to read back.
      const cell = mosaicCell(still);
      const cols = Math.max(1, Math.ceil(s.width / cell)), rows = Math.max(1, Math.ceil(s.height / cell));
      const scratch = make(cols, rows);
      scratch.ctx.imageSmoothingEnabled = true;
      scratch.ctx.drawImage(original, s.x, s.y, cols * cell, rows * cell, 0, 0, cols, rows);
      ctx.beginPath();
      ctx.rect(s.x, s.y, s.width, s.height);
      ctx.clip();
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(scratch.image, 0, 0, cols, rows, s.x, s.y, cols * cell, rows * cell);
      break;
    }
    case "step": {
      const r = STEP_RADIUS[s.stroke];
      ctx.beginPath();
      ctx.arc(s.x, s.y, r, 0, 2 * Math.PI);
      ctx.fill();
      ctx.fillStyle = textOn(s.colour);
      ctx.font = fontFor(Math.round(r * 1.15), 700);
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(numbers.get(s.id) ?? 1), s.x, s.y + 1);
      break;
    }
    case "text":
      drawTextLines(ctx, { x: s.x, y: s.y, width: s.width, height: s.height }, s.text, s.text_size, s.colour);
      break;
    case "callout": {
      const box = { x: s.x, y: s.y, width: s.width, height: s.height };
      const exit = exitPoint(box, { x: s.tail_x, y: s.tail_y });
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      const angle = Math.atan2(s.tail_y - cy, s.tail_x - cx);
      const half = Math.max(6, Math.min(14, Math.min(box.width, box.height) / 4));
      ctx.beginPath();
      ctx.moveTo(s.tail_x, s.tail_y);
      ctx.lineTo(exit.x + half * Math.sin(angle), exit.y - half * Math.cos(angle));
      ctx.lineTo(exit.x - half * Math.sin(angle), exit.y + half * Math.cos(angle));
      ctx.closePath();
      ctx.fill();
      roundedRect(ctx, box, CALLOUT_RADIUS);
      ctx.fill();
      drawTextLines(ctx, box, s.text, s.text_size, textOn(s.colour));
      break;
    }
    case "magnify": {
      const lens = lensRect(s);
      // The connector and the source's outline, thin; then the lens: the ORIGINAL's pixels, not the
      // annotations under them, copied without smoothing so each pixel is a crisp 2 x 2 or 3 x 3 block.
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(s.x + s.width / 2, s.y + s.height / 2);
      ctx.lineTo(lens.x + lens.width / 2, lens.y + lens.height / 2);
      ctx.stroke();
      ctx.lineWidth = 1.5;
      ctx.strokeRect(s.x, s.y, s.width, s.height);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(original, s.x, s.y, s.width, s.height, lens.x, lens.y, lens.width, lens.height);
      ctx.lineWidth = stroke;
      ctx.strokeRect(lens.x, lens.y, lens.width, lens.height);
      break;
    }
  }
  ctx.restore();
}

/** The still and every shape, in document order, at the still's origin. The ONE picture both views draw. */
export function drawScene(ctx: Ctx2D, doc: AnnotationDocument, original: ImageLike, still: Size, make: MakeScratch): void {
  ctx.drawImage(original, 0, 0);
  const numbers = stepNumbers(doc);
  for (const s of doc.shapes) drawShape(ctx, s, original, still, numbers, make);
}

/**
 * The export: the scene translated by the crop's origin onto a canvas of the crop's size (or the still's
 * when there is no crop) - so a shape outside the crop is clipped by the canvas's edge, never deleted.
 * Returns the size the canvas must have.
 */
export function renderExport(ctx: Ctx2D, doc: AnnotationDocument, original: ImageLike, still: Size, make: MakeScratch): Size {
  const size = outputSize(doc, still);
  ctx.save();
  if (doc.crop) ctx.translate(-doc.crop.x, -doc.crop.y);
  drawScene(ctx, doc, original, still, make);
  ctx.restore();
  return size;
}

function drawHandles(ctx: Ctx2D, list: Handle[], zoom: number): void {
  const side = HANDLE_PX / zoom;
  ctx.lineWidth = 1 / zoom;
  for (const h of list) {
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#1e293b";
    ctx.fillRect(h.x - side / 2, h.y - side / 2, side, side);
    ctx.strokeRect(h.x - side / 2, h.y - side / 2, side, side);
  }
}

/**
 * The editor's view: the scene, then what only the editor shows - the kept rectangle clear and the rest
 * dimmed when there is a crop, the selected shape's bounds and handles, the crop's handles while the Crop
 * tool is active. Nothing drawn here reaches the export.
 */
export function renderPreview(ctx: Ctx2D, doc: AnnotationDocument, original: ImageLike, still: Size, make: MakeScratch, overlay: Overlay): void {
  drawScene(ctx, doc, original, still, make);
  const { zoom } = overlay;
  ctx.save();
  if (doc.crop) {
    const c = doc.crop;
    ctx.fillStyle = `rgba(0, 0, 0, ${CROP_DIM_ALPHA})`;
    ctx.fillRect(0, 0, still.width, c.y);
    ctx.fillRect(0, c.y + c.height, still.width, still.height - c.y - c.height);
    ctx.fillRect(0, c.y, c.x, c.height);
    ctx.fillRect(c.x + c.width, c.y, still.width - c.x - c.width, c.height);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1 / zoom;
    ctx.setLineDash([6 / zoom, 4 / zoom]);
    ctx.strokeRect(c.x, c.y, c.width, c.height);
    ctx.setLineDash([]);
    if (overlay.cropHandles) drawHandles(ctx, cropHandles(c), zoom);
  }
  const selected = shapeById(doc, overlay.selectedId);
  if (selected) {
    const b = bounds(selected);
    ctx.strokeStyle = "#1e88e5";
    ctx.lineWidth = 1 / zoom;
    ctx.setLineDash([4 / zoom, 3 / zoom]);
    ctx.strokeRect(b.x, b.y, b.width, b.height);
    ctx.setLineDash([]);
    drawHandles(ctx, handles(selected), zoom);
  }
  ctx.restore();
}
