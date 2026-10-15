/**
 * Annotate a still (T3b): the pure model of lib/annotate.ts - what the tools make, hit-tests, handles and
 * resizes, the clamp that keeps every number inside the server's range (fix round R2), step numbering and
 * Restart, the crop maths and clipping, undo / redo with content-equal commits as no change (R3), serialise /
 * parse with the version refusal, the mosaic cell, the output size, the backing-store cap (R8), the zoom
 * steps and the keys.
 */
import { describe, expect, it } from "vitest";
import {
  type AnnotationDocument, type BoxShape, type CalloutShape, type MagnifyShape, type Measure, type SegmentShape, type Shape, type StepShape,
  type TextShape, DEFAULT_STYLE, DOCUMENT_VERSION, MAX_CANVAS_PIXELS, MAX_CANVAS_SIDE, MAX_SHAPES, MAX_TEXT_CHARS, MIN_CROP_SIDE, MIN_MOSAIC_CELL,
  STEP_RADIUS, TEXT_PX, TOOL_KEYS, X_FIELDS, Y_FIELDS, ZOOM_STEPS,
  addShape, backingScale, bounds, canAddShape, clampShape, clipRect, cropFromDrag, currentSequence, deleteShape,
  fitZoom, handleAt, handles, history, hitTest, hitsLens, lensRect, mosaicCell, moveCrop, moveLens, moveShape, outputSize, parseDocument,
  placeShape, resizeCrop, resizeShape, restartSequence, serialise, setCrop, shapeFromDrag, stepNumbers, textBoxHeight, textOn, toolForKey,
  updateShape, withinStill, wrapText, zoomIn, zoomOut,
} from "./annotate";
import { canRedo, canUndo, commit, redo, undo } from "./history";

const STILL = { width: 1000, height: 800 };
const measure: Measure = (text) => text.length * 8;
const step = (id: string, sequence = 0, x = 10, y = 10): StepShape => ({ id, type: "step", colour: "#e53935", stroke: "medium", x, y, sequence });
const arrow = (id = "a", x1 = 10, y1 = 10, x2 = 110, y2 = 10): SegmentShape => ({ id, type: "arrow", colour: "#e53935", stroke: "medium", x1, y1, x2, y2 });
const rect = (id = "r", x = 100, y = 100, width = 50, height = 40): BoxShape => ({ id, type: "rect", colour: "#1e88e5", stroke: "thin", x, y, width, height });
const doc = (...shapes: Shape[]): AnnotationDocument => ({ version: DOCUMENT_VERSION, shapes, crop: null });

describe("the tool keys", () => {
  it("map V A C T R E L S B H K M, upper or lower case, and nothing with a modifier", () => {
    expect(Object.keys(TOOL_KEYS).sort().join("")).toBe("abcehklmrstv");
    expect(toolForKey({ key: "v" })).toBe("select");
    expect(toolForKey({ key: "A" })).toBe("arrow");
    expect(toolForKey({ key: "k" })).toBe("crop");
    expect(toolForKey({ key: "m" })).toBe("magnify");
    expect(toolForKey({ key: "a", ctrlKey: true })).toBeNull();
    expect(toolForKey({ key: "x" })).toBeNull();
  });
});

describe("what the tools make", () => {
  it("an arrow from a drag, nothing from a drag too short to mean anything", () => {
    const a = shapeFromDrag("arrow", { x: 10, y: 20 }, { x: 110, y: 60 }, DEFAULT_STYLE, STILL, "id1") as SegmentShape;
    expect(a).toMatchObject({ type: "arrow", id: "id1", x1: 10, y1: 20, x2: 110, y2: 60, colour: DEFAULT_STYLE.colour, stroke: "medium" });
    expect(shapeFromDrag("arrow", { x: 10, y: 20 }, { x: 11, y: 20 }, DEFAULT_STYLE, STILL)).toBeNull();
    expect(shapeFromDrag("rect", { x: 10, y: 20 }, { x: 11, y: 60 }, DEFAULT_STYLE, STILL)).toBeNull();
  });

  it("a box from a drag in any direction, normalised; the highlighter takes the highlight colour", () => {
    expect(shapeFromDrag("rect", { x: 100, y: 80 }, { x: 40, y: 20 }, DEFAULT_STYLE, STILL)).toMatchObject({ type: "rect", x: 40, y: 20, width: 60, height: 60 });
    expect(shapeFromDrag("ellipse", { x: 0, y: 0 }, { x: 10, y: 10 }, DEFAULT_STYLE, STILL)).toMatchObject({ type: "ellipse" });
    expect(shapeFromDrag("blur", { x: 0, y: 0 }, { x: 10, y: 10 }, DEFAULT_STYLE, STILL)).toMatchObject({ type: "blur" });
    expect(shapeFromDrag("highlight", { x: 0, y: 0 }, { x: 10, y: 10 }, DEFAULT_STYLE, STILL)).toMatchObject({ type: "highlight", colour: DEFAULT_STYLE.highlight });
  });

  it("a lens beside its source: to the right when there is room, else to the left, else below", () => {
    const right = shapeFromDrag("magnify", { x: 100, y: 100 }, { x: 200, y: 150 }, DEFAULT_STYLE, STILL) as MagnifyShape;
    expect(right).toMatchObject({ type: "magnify", x: 100, y: 100, width: 100, height: 50, factor: 2, lens_x: 216, lens_y: 100 });
    expect(lensRect(right)).toEqual({ x: 216, y: 100, width: 200, height: 100 });
    const left = shapeFromDrag("magnify", { x: 850, y: 100 }, { x: 950, y: 150 }, DEFAULT_STYLE, STILL) as MagnifyShape;
    expect(left.lens_x).toBe(850 - 16 - 200);
    const below = shapeFromDrag("magnify", { x: 100, y: 100 }, { x: 900, y: 150 }, { ...DEFAULT_STYLE, lensFactor: 3 }, STILL) as MagnifyShape;
    expect(below).toMatchObject({ factor: 3, lens_x: 100, lens_y: 166 });
    expect(lensRect(below).width).toBe(800 * 3);
  });

  it("a click places a step with the current sequence, a text box of the default width, a callout with a tail", () => {
    expect(placeShape("step", { x: 5, y: 6 }, DEFAULT_STYLE, measure, 2, STILL)).toMatchObject({ type: "step", x: 5, y: 6, sequence: 2 });
    const t = placeShape("text", { x: 5, y: 6 }, DEFAULT_STYLE, measure, 0, STILL) as TextShape;
    expect(t).toMatchObject({ type: "text", x: 5, y: 6, width: 240, text: "", text_size: "medium" });
    expect(t.height).toBe(textBoxHeight("", "medium", 240, measure));
    const c = placeShape("callout", { x: 5, y: 6 }, { ...DEFAULT_STYLE, textSize: "large" }, measure, 0, STILL) as CalloutShape;
    expect(c).toMatchObject({ type: "callout", text_size: "large", tail_x: 5 - 24 });
    expect(c.tail_y).toBeGreaterThan(c.y + c.height);
    expect(placeShape("arrow", { x: 0, y: 0 }, DEFAULT_STYLE, measure, 0, STILL)).toBeNull();
  });
});

describe("text wrapping", () => {
  it("wraps on spaces to the width, breaks a word too long for it by characters, keeps new lines", () => {
    expect(wrapText("one two three", 8 * 7, "f", measure)).toEqual(["one two", "three"]);
    expect(wrapText("abcdefghij", 8 * 4, "f", measure)).toEqual(["abcd", "efgh", "ij"]);
    expect(wrapText("a\nb", 100, "f", measure)).toEqual(["a", "b"]);
    expect(textBoxHeight("one two three", "small", 8 * 7 + 12, measure)).toBe(Math.ceil(2 * TEXT_PX.small * 1.25 + 12));
  });
});

describe("the clamp: nothing the page makes is outside what the server accepts (R2)", () => {
  it("names the same fields as api/schemas.py, kind by kind", () => {
    expect(X_FIELDS.callout).toEqual(["x", "width", "tail_x"]);
    expect(Y_FIELDS.magnify).toEqual(["y", "height", "lens_y"]);
    expect(X_FIELDS.step).toEqual(["x"]);
    expect(Object.keys(X_FIELDS).sort()).toEqual(["arrow", "blur", "callout", "ellipse", "highlight", "line", "magnify", "rect", "step", "text"]);
  });

  it("a move past -size lands at -size", () => {
    const r = rect("r", 500, 100, 100, 40);
    const moved = moveShape(r, -1600, 0, STILL) as BoxShape;
    expect(moved.x).toBe(-1000);
    expect(withinStill(moved, STILL)).toBe(true);
    expect(withinStill({ ...r, x: -1100 }, STILL)).toBe(false);
    const lens = moveLens({ id: "m", type: "magnify", colour: "#000000", stroke: "thin", x: 10, y: 10, width: 20, height: 20, lens_x: 100, lens_y: 100, factor: 2 }, 5000, -5000, STILL);
    expect(lens).toMatchObject({ lens_x: 2000, lens_y: -800 });
  });

  it("a resize to 3 x the still's width is held at 2 x", () => {
    const r = rect("r", -1000, 100, 100, 40);
    const wide = resizeShape(r, "e", { x: 2000, y: 100 }, STILL) as BoxShape;
    expect(wide.width).toBe(2000);
    expect(withinStill(wide, STILL)).toBe(true);
    expect(withinStill({ ...r, width: 3000 }, STILL)).toBe(false);
    expect(withinStill({ ...r, width: 0.5 }, STILL)).toBe(false);
    expect(clampShape({ ...r, width: 0 }, STILL).width).toBe(1);
  });

  it("500 large characters on a 400 x 300 still cap the box at 2 x the still's height", () => {
    const small = { width: 400, height: 300 };
    const text = "x".repeat(MAX_TEXT_CHARS);
    // Uncapped, the box is taller than the still twice over (732 px with this 8-px-a-character measure;
    // about 1,450 with the real font); capped, exactly 2 x 300.
    expect(textBoxHeight(text, "large", 240, measure)).toBeGreaterThan(2 * small.height);
    expect(textBoxHeight(text, "large", 240, measure, small)).toBe(600);
    const box: TextShape = { id: "t", type: "text", colour: "#000000", stroke: "medium", x: 10, y: 10, width: 240, height: textBoxHeight(text, "large", 240, measure, small), text, text_size: "large" };
    expect(withinStill(box, small)).toBe(true);
  });
});

describe("hit-tests", () => {
  it("find a line within the tolerance, a step within its circle, a box inside it, and the topmost shape first", () => {
    const d = doc(arrow("a", 10, 10, 110, 10), rect("r", 100, 100, 50, 40), step("s", 0, 300, 300));
    expect(hitTest(d, { x: 60, y: 14 }, 3)).toBe("a");
    expect(hitTest(d, { x: 60, y: 30 }, 3)).toBeNull();
    expect(hitTest(d, { x: 120, y: 120 }, 3)).toBe("r");
    expect(hitTest(d, { x: 300 + STEP_RADIUS.medium - 1, y: 300 }, 0)).toBe("s");
    expect(hitTest(d, { x: 300 + STEP_RADIUS.medium + 5, y: 300 }, 0)).toBeNull();
    const over = addShape(d, rect("top", 110, 110, 10, 10));
    expect(hitTest(over, { x: 115, y: 115 }, 0)).toBe("top");
  });

  it("find a magnify shape by its source or its lens, and tell the lens apart", () => {
    const m: MagnifyShape = { id: "m", type: "magnify", colour: "#000000", stroke: "thin", x: 10, y: 10, width: 20, height: 20, lens_x: 100, lens_y: 100, factor: 2 };
    expect(hitTest(doc(m), { x: 15, y: 15 }, 0)).toBe("m");
    expect(hitTest(doc(m), { x: 130, y: 130 }, 0)).toBe("m");
    expect(hitsLens(m, { x: 130, y: 130 }, 0)).toBe(true);
    expect(hitsLens(m, { x: 15, y: 15 }, 0)).toBe(false);
    expect(hitTest(doc(m), { x: 60, y: 60 }, 0)).toBeNull();
    expect(bounds(m)).toEqual({ x: 10, y: 10, width: 130, height: 130 });
  });
});

describe("handles and resizes", () => {
  it("a box has eight handles and its south-east one drags the far corner; dragging past the opposite edge flips, never inverts", () => {
    const r = rect("r", 100, 100, 50, 40);
    expect(handles(r).map((h) => h.name)).toEqual(["nw", "n", "ne", "e", "se", "s", "sw", "w"]);
    expect(handleAt(handles(r), { x: 151, y: 141 }, 4)).toBe("se");
    expect(handleAt(handles(r), { x: 125, y: 120 }, 4)).toBeNull();
    expect(resizeShape(r, "se", { x: 200, y: 180 }, STILL)).toMatchObject({ x: 100, y: 100, width: 100, height: 80 });
    expect(resizeShape(r, "nw", { x: 120, y: 110 }, STILL)).toMatchObject({ x: 120, y: 110, width: 30, height: 30 });
    expect(resizeShape(r, "e", { x: 60, y: 999 }, STILL)).toMatchObject({ x: 60, y: 100, width: 40, height: 40 });
    expect(resizeShape(r, "n", { x: 999, y: 160 }, STILL)).toMatchObject({ x: 100, y: 140, width: 50, height: 20 });
  });

  it("a line has two ends, a callout a tail besides its box, a step none", () => {
    const a = arrow();
    expect(handles(a).map((h) => h.name)).toEqual(["start", "end"]);
    expect(resizeShape(a, "end", { x: 5, y: 5 }, STILL)).toMatchObject({ x1: 10, y1: 10, x2: 5, y2: 5 });
    const c: CalloutShape = { id: "c", type: "callout", colour: "#fb8c00", stroke: "medium", x: 10, y: 10, width: 100, height: 40, tail_x: 0, tail_y: 80, text: "x", text_size: "small" };
    expect(handles(c).map((h) => h.name)).toContain("tail");
    expect(resizeShape(c, "tail", { x: 300, y: 300 }, STILL)).toMatchObject({ tail_x: 300, tail_y: 300, x: 10 });
    expect(handles(step("s"))).toEqual([]);
    expect(resizeShape(step("s"), "se", { x: 1, y: 1 }, STILL)).toEqual(step("s"));
  });

  it("moving a callout takes its tail along, moving a magnify its lens; the lens moves alone too", () => {
    const c: CalloutShape = { id: "c", type: "callout", colour: "#fb8c00", stroke: "medium", x: 10, y: 10, width: 100, height: 40, tail_x: 0, tail_y: 80, text: "x", text_size: "small" };
    expect(moveShape(c, 5, 7, STILL)).toMatchObject({ x: 15, y: 17, tail_x: 5, tail_y: 87 });
    const m: MagnifyShape = { id: "m", type: "magnify", colour: "#000000", stroke: "thin", x: 10, y: 10, width: 20, height: 20, lens_x: 100, lens_y: 100, factor: 2 };
    expect(moveShape(m, 1, 2, STILL)).toMatchObject({ x: 11, y: 12, lens_x: 101, lens_y: 102 });
    expect(moveLens(m, 1, 2, STILL)).toMatchObject({ x: 10, y: 10, lens_x: 101, lens_y: 102 });
    expect(moveShape(arrow(), 1, 1, STILL)).toMatchObject({ x1: 11, y1: 11, x2: 111, y2: 11 });
  });
});

describe("the crop", () => {
  it("comes from a drag, clamped to the still and in whole pixels; too small is no crop", () => {
    expect(cropFromDrag({ x: -10.4, y: 5.6 }, { x: 200.2, y: 2000 }, STILL)).toEqual({ x: 0, y: 6, width: 200, height: 794 });
    expect(cropFromDrag({ x: 0, y: 0 }, { x: MIN_CROP_SIDE - 1, y: 100 }, STILL)).toBeNull();
    expect(cropFromDrag({ x: 0, y: 0 }, { x: MIN_CROP_SIDE, y: MIN_CROP_SIDE }, STILL)).toEqual({ x: 0, y: 0, width: 16, height: 16 });
  });

  it("is resized by its handles inside the still and refuses to shrink under the minimum; moves stay inside", () => {
    const c = { x: 100, y: 100, width: 200, height: 100 };
    expect(resizeCrop(c, "se", { x: 1500, y: 1500 }, STILL)).toEqual({ x: 100, y: 100, width: 900, height: 700 });
    expect(resizeCrop(c, "e", { x: 105, y: 0 }, STILL)).toEqual(c);
    expect(moveCrop(c, -500, 50, STILL)).toEqual({ ...c, x: 0, y: 150 });
    expect(moveCrop(c, 5000, 0, STILL)).toEqual({ ...c, x: 800 });
  });

  it("clips a shape's rectangle to it rather than deleting the shape; the output size is the crop's", () => {
    expect(clipRect({ x: 50, y: 50, width: 100, height: 100 }, { x: 100, y: 0, width: 100, height: 100 })).toEqual({ x: 100, y: 50, width: 50, height: 50 });
    expect(clipRect({ x: 0, y: 0, width: 10, height: 10 }, { x: 20, y: 20, width: 10, height: 10 })).toBeNull();
    const d = { ...doc(arrow("a", -50, -50, 2000, 2000)), crop: { x: 10, y: 20, width: 300, height: 200 } };
    expect(outputSize(d, STILL)).toEqual({ width: 300, height: 200 });
    expect(d.shapes).toHaveLength(1);
    expect(outputSize(doc(), STILL)).toEqual(STILL);
  });
});

describe("step numbering", () => {
  it("numbers the steps 1, 2, 3 in the order placed, renumbers when one goes, and restarts at 1 for a new sequence", () => {
    let d = doc(step("s1"), step("s2"), step("s3"));
    expect([...stepNumbers(d).entries()]).toEqual([["s1", 1], ["s2", 2], ["s3", 3]]);
    d = deleteShape(d, "s2");
    expect([...stepNumbers(d).entries()]).toEqual([["s1", 1], ["s3", 2]]);
    expect(currentSequence(d)).toBe(0);
    const next = restartSequence(d);
    expect(next).toBe(1);
    d = addShape(addShape(d, step("s4", next)), step("s5", next));
    expect([...stepNumbers(d).entries()]).toEqual([["s1", 1], ["s3", 2], ["s4", 1], ["s5", 2]]);
    expect(currentSequence(d)).toBe(1);
    expect(restartSequence(doc())).toBe(0);
    expect(stepNumbers(doc(arrow())).size).toBe(0);
  });
});

describe("undo and redo", () => {
  it("commit, undo, redo; a commit after an undo forgets the redo; unlimited within the session", () => {
    let h = history(doc());
    expect(canUndo(h)).toBe(false);
    h = commit(h, addShape(h.present, arrow("a")));
    h = commit(h, addShape(h.present, arrow("b")));
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a", "b"]);
    h = undo(h);
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a"]);
    expect(canRedo(h)).toBe(true);
    h = redo(h);
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a", "b"]);
    h = undo(h);
    h = commit(h, addShape(h.present, arrow("c")));
    expect(canRedo(h)).toBe(false);
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a", "c"]);
    expect(commit(h, h.present)).toBe(h);
    for (let i = 0; i < 200; i++) h = commit(h, addShape(h.present, arrow(`x${i}`)));
    for (let i = 0; i < 200; i++) h = undo(h);
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a", "c"]);
    expect(undo(history(doc())).present).toEqual(doc());
  });

  it("a commit of a content-equal document adds no step: a click that moved nothing, a drag too small to crop, a style that applied to nothing", () => {
    const r = rect("r");
    const d = { ...doc(r), crop: { x: 0, y: 0, width: 100, height: 100 } };
    const h = history(d);
    const clicked = commit(h, updateShape(d, "r", moveShape(r, 0, 0, STILL)));
    expect(clicked).toBe(h);
    expect(canUndo(clicked)).toBe(false);
    expect(commit(h, setCrop(d, d.crop))).toBe(h);
    expect(commit(h, updateShape(d, "r", (s) => ({ ...s })))).toBe(h);
    expect(canUndo(commit(h, updateShape(d, "r", moveShape(r, 1, 0, STILL))))).toBe(true);
    // A difference under a hundredth of a pixel is the same stored document, so the same.
    expect(commit(h, updateShape(d, "r", moveShape(r, 0.001, 0, STILL)))).toBe(h);
  });
});

describe("serialise and parse", () => {
  it("round-trips every kind of shape and the crop, with version 1", () => {
    const d: AnnotationDocument = {
      version: 1,
      crop: { x: 1, y: 2, width: 300, height: 200 },
      shapes: [
        arrow("a"), { ...arrow("l"), type: "line" }, rect("r"), { ...rect("e"), type: "ellipse" }, { ...rect("b"), type: "blur" },
        { ...rect("h"), type: "highlight" },
        { id: "t", type: "text", colour: "#000000", stroke: "medium", x: 1.234, y: 2, width: 100, height: 30, text: "Hi", text_size: "small" },
        { id: "c", type: "callout", colour: "#fb8c00", stroke: "thick", x: 1, y: 2, width: 100, height: 30, tail_x: 0, tail_y: 90, text: "Look", text_size: "large" },
        step("s1"), step("s2", 1),
        { id: "m", type: "magnify", colour: "#43a047", stroke: "medium", x: 1, y: 2, width: 10, height: 10, lens_x: 50, lens_y: 50, factor: 3 },
      ],
    };
    const text = serialise(d);
    const back = parseDocument(text);
    expect(back).toEqual({ ...d, shapes: d.shapes.map((s) => (s.id === "t" ? { ...s, x: 1.23 } : s)) });
    expect(JSON.parse(text).version).toBe(DOCUMENT_VERSION);
    expect(parseDocument(JSON.parse(text))).toEqual(back);
  });

  it("refuses another version first, naming both, and a document it cannot read", () => {
    expect(() => parseDocument({ version: 2, shapes: [] })).toThrow(/version 2; this app reads version 1/);
    expect(() => parseDocument({ version: 1 })).toThrow(/no list of shapes/);
    expect(() => parseDocument({ version: 1, shapes: [{ id: "x", type: "pen", colour: "#000000" }] })).toThrow(/unknown kind "pen"/);
    expect(() => parseDocument({ version: 1, shapes: [{ ...arrow("a"), x2: Number.NaN }] })).toThrow(/has no x2/);
    expect(() => parseDocument({ version: 1, shapes: [{ ...arrow("a"), colour: "red" }] })).toThrow(/no colour/);
    expect(() => parseDocument("[1]")).toThrow(/not an object/);
    expect(() => parseDocument({ version: 1, shapes: [], crop: { x: 0 } })).toThrow(/crop has no y/);
    expect(() => parseDocument({ version: 1, shapes: Array.from({ length: MAX_SHAPES + 1 }, (_, i) => step(`s${i}`)) })).toThrow(/at most 500/);
    // A stroke or a text size it does not know falls back; a lens factor it does not know becomes 2.
    const loose = parseDocument({ version: 1, shapes: [{ ...arrow("a"), stroke: "huge" }, { ...step("s"), sequence: 0 }, { id: "m", type: "magnify", colour: "#000000", x: 0, y: 0, width: 1, height: 1, lens_x: 0, lens_y: 0, factor: 9 }] });
    expect(loose.shapes[0].stroke).toBe("medium");
    expect((loose.shapes[2] as MagnifyShape).factor).toBe(2);
  });

  it("bounds a document at MAX_SHAPES", () => {
    const full = doc(...Array.from({ length: MAX_SHAPES }, (_, i) => step(`s${i}`)));
    expect(canAddShape(full)).toBe(false);
    expect(canAddShape(deleteShape(full, "s0"))).toBe(true);
  });
});

describe("the mosaic, the zoom, the backing store and the colours", () => {
  it("cells are 1 % of the shorter side, never under 8", () => {
    expect(mosaicCell(STILL)).toBe(MIN_MOSAIC_CELL);
    expect(mosaicCell({ width: 3840, height: 2160 })).toBe(22);
    expect(mosaicCell({ width: 2160, height: 3840 })).toBe(22);
    expect(mosaicCell({ width: 40, height: 30 })).toBe(8);
  });

  it("fits the still in the viewport, never above 1, and steps the zoom through the list", () => {
    expect(fitZoom({ width: 524, height: 1000 }, STILL)).toBe(0.5);
    expect(fitZoom({ width: 5000, height: 5000 }, STILL)).toBe(1);
    expect(fitZoom({ width: 0, height: 0 }, STILL)).toBe(0.05);
    expect(zoomIn(1)).toBe(2);
    expect(zoomIn(0.3)).toBe(0.5);
    expect(zoomIn(4)).toBe(4);
    expect(zoomOut(1)).toBe(0.5);
    expect(zoomOut(0.3)).toBe(0.25);
    expect(zoomOut(0.25)).toBe(0.25);
    expect(ZOOM_STEPS).toEqual([0.25, 0.5, 1, 2, 4]);
  });

  it("caps the preview's backing store at 16,384 a side and 128 million pixels, and leaves an ordinary still alone (R8)", () => {
    expect(MAX_CANVAS_SIDE).toBe(16_384);
    expect(MAX_CANVAS_PIXELS).toBe(128_000_000);
    // A 4K still at 400 % on a 1.5x display would be 23,040 x 12,960 = 298 M px: held to the area cap.
    const k4 = { width: 3840, height: 2160 };
    const s1 = backingScale(k4, 4, 1.5);
    expect(s1).toBeLessThan(6);
    expect(s1).toBeCloseTo(Math.sqrt(128_000_000 / (3840 * 2160)), 6);
    expect(k4.width * s1 * k4.height * s1).toBeLessThanOrEqual(128_000_000 + 1);
    expect(k4.width * s1).toBeLessThanOrEqual(16_384);
    // A 10,000 x 10,000 still at 200 % on a 2x display would be 400 M px: held under both caps.
    const big = { width: 10_000, height: 10_000 };
    const s2 = backingScale(big, 2, 2);
    expect(s2).toBeCloseTo(Math.sqrt(1.28), 6);
    expect(big.width * s2).toBeLessThanOrEqual(16_384);
    expect(big.width * s2 * big.height * s2).toBeLessThanOrEqual(128_000_000 + 1);
    // A 20,000-wide still: the side cap bites before the area cap at 100 %.
    expect(backingScale({ width: 20_000, height: 1_000 }, 1, 1)).toBeCloseTo(16_384 / 20_000, 6);
    // An ordinary still at an ordinary zoom: zoom x dpr, unchanged.
    expect(backingScale(STILL, 1, 1)).toBe(1);
    expect(backingScale(STILL, 2, 1.5)).toBe(3);
  });

  it("puts white text on a dark colour and black on a light one", () => {
    expect(textOn("#e53935")).toBe("#ffffff");
    expect(textOn("#fdd835")).toBe("#000000");
    expect(textOn("#ffffff")).toBe("#000000");
    expect(textOn("#000000")).toBe("#ffffff");
  });
});
