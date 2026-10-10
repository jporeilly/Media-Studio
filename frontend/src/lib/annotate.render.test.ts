/**
 * Annotate a still (T3b): the RENDERER of lib/annotate.ts against a recording 2D-context stub - each tool
 * draws what it should, the mosaic's cell size, the lens drawing from the ORIGINAL, the highlighter's
 * multiply blend, the step numbers, the text in the UI font, and the export: the crop's size, the scene
 * translated by the crop's origin, and none of the editor's overlay - the preview and the export draw
 * the same scene through the one `drawScene`.
 */
import { describe, expect, it } from "vitest";
import {
  type AnnotationDocument, type Ctx2D, type MakeScratch, type Scratch, type Shape, CROP_DIM_ALPHA, HIGHLIGHT_ALPHA, STEP_RADIUS, STROKE_PX,
  TEXT_PADDING, TEXT_PX, UI_FONT, arrowHeadLength, drawScene, fontFor, mosaicCell, renderExport, renderPreview, textOn,
} from "./annotate";

interface Call { op: string; args: unknown[] }
interface Stub { ctx: Ctx2D; calls: Call[]; sets: { prop: string; value: unknown }[]; props: Record<string, unknown> }

/** A context that records every method call and every property set; `measureText` is 8 px a character. */
function stub(): Stub {
  const calls: Call[] = [];
  const sets: { prop: string; value: unknown }[] = [];
  const props: Record<string, unknown> = { font: "", measureText: (text: string) => ({ width: text.length * 8 }) };
  const ctx = new Proxy(props, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      return (...args: unknown[]) => { calls.push({ op: prop, args }); };
    },
    set(target, prop: string, value) {
      target[prop] = value;
      sets.push({ prop, value });
      return true;
    },
  }) as unknown as Ctx2D;
  return { ctx, calls, sets, props };
}

const STILL = { width: 1000, height: 800 };
const original = { width: 1000, height: 800, kind: "original" };
const scratches: { width: number; height: number; stub: Stub }[] = [];
const make: MakeScratch = (width, height): Scratch => {
  const s = stub();
  scratches.push({ width, height, stub: s });
  return { ctx: s.ctx, image: { width, height, kind: "scratch" } as unknown as Scratch["image"] };
};
const doc = (shapes: Shape[], crop: AnnotationDocument["crop"] = null): AnnotationDocument => ({ version: 1, shapes, crop });
const ops = (s: Stub) => s.calls.map((c) => c.op);
const of = (s: Stub, op: string) => s.calls.filter((c) => c.op === op);
const set = (s: Stub, prop: string) => s.sets.filter((x) => x.prop === prop).map((x) => x.value);

describe("the scene", () => {
  it("draws the original first, then every shape in document order", () => {
    const s = stub();
    drawScene(s.ctx, doc([
      { id: "r", type: "rect", colour: "#1e88e5", stroke: "thin", x: 10, y: 20, width: 30, height: 40 },
      { id: "l", type: "line", colour: "#000000", stroke: "thick", x1: 0, y1: 0, x2: 5, y2: 5 },
    ]), original, STILL, make);
    expect(s.calls[0]).toEqual({ op: "drawImage", args: [original, 0, 0] });
    const strokeRect = of(s, "strokeRect")[0];
    expect(strokeRect.args).toEqual([10, 20, 30, 40]);
    expect(ops(s).indexOf("strokeRect")).toBeLessThan(ops(s).lastIndexOf("stroke"));
    expect(set(s, "lineWidth")).toEqual([STROKE_PX.thin, STROKE_PX.thick]);
    expect(set(s, "strokeStyle")).toEqual(["#1e88e5", "#000000"]);
  });

  it("an arrow is a shaft stroked short of the tip and a filled head at the tip; a line is one stroke", () => {
    const s = stub();
    drawScene(s.ctx, doc([{ id: "a", type: "arrow", colour: "#e53935", stroke: "medium", x1: 0, y1: 0, x2: 100, y2: 0 }]), original, STILL, make);
    const len = arrowHeadLength("medium");
    const shaftEnd = of(s, "lineTo")[0].args;
    expect(shaftEnd).toEqual([100 - len, 0]);
    expect(of(s, "stroke")).toHaveLength(1);
    expect(of(s, "fill")).toHaveLength(1);
    expect(of(s, "moveTo")[1].args).toEqual([100, 0]);
    const line = stub();
    drawScene(line.ctx, doc([{ id: "l", type: "line", colour: "#e53935", stroke: "medium", x1: 0, y1: 0, x2: 100, y2: 0 }]), original, STILL, make);
    expect(of(line, "stroke")).toHaveLength(1);
    expect(of(line, "fill")).toHaveLength(0);
    expect(of(line, "lineTo")[0].args).toEqual([100, 0]);
  });

  it("an ellipse is an ellipse of the box's centre and radii", () => {
    const s = stub();
    drawScene(s.ctx, doc([{ id: "e", type: "ellipse", colour: "#1e88e5", stroke: "thin", x: 10, y: 20, width: 30, height: 40 }]), original, STILL, make);
    expect(of(s, "ellipse")[0].args).toEqual([25, 40, 15, 20, 0, 0, 2 * Math.PI]);
    expect(of(s, "stroke")).toHaveLength(1);
  });

  it("the highlighter fills with the multiply blend at its alpha and ignores the stroke", () => {
    const s = stub();
    drawScene(s.ctx, doc([{ id: "h", type: "highlight", colour: "#fdd835", stroke: "thick", x: 10, y: 20, width: 30, height: 40 }]), original, STILL, make);
    expect(set(s, "globalCompositeOperation")).toEqual(["multiply"]);
    expect(set(s, "globalAlpha")).toEqual([HIGHLIGHT_ALPHA]);
    expect(of(s, "fillRect")[0].args).toEqual([10, 20, 30, 40]);
    expect(of(s, "stroke")).toHaveLength(0);
    expect(of(s, "strokeRect")).toHaveLength(0);
    expect(set(s, "fillStyle")).toContain("#fdd835");
  });

  it("the blur is a mosaic: the original under the box drawn into a scratch of one pixel a cell, drawn back without smoothing, clipped to the box", () => {
    scratches.length = 0;
    const s = stub();
    const cell = mosaicCell(STILL);
    expect(cell).toBe(8);
    drawScene(s.ctx, doc([{ id: "b", type: "blur", colour: "#000000", stroke: "medium", x: 100, y: 200, width: 40, height: 20 }]), original, STILL, make);
    expect(scratches).toHaveLength(1);
    expect(scratches[0]).toMatchObject({ width: 5, height: 3 });
    const small = scratches[0].stub;
    expect(set(small, "imageSmoothingEnabled")).toEqual([true]);
    expect(of(small, "drawImage")[0].args).toEqual([original, 100, 200, 40, 24, 0, 0, 5, 3]);
    expect(of(s, "clip")).toHaveLength(1);
    expect(of(s, "rect")[0].args).toEqual([100, 200, 40, 20]);
    expect(set(s, "imageSmoothingEnabled")).toEqual([false]);
    const back = of(s, "drawImage")[1];
    expect(back.args[0]).toMatchObject({ kind: "scratch", width: 5, height: 3 });
    expect(back.args.slice(1)).toEqual([0, 0, 5, 3, 100, 200, 40, 24]);
  });

  it("the mosaic's cell follows the still: 22 px on a 4K still", () => {
    scratches.length = 0;
    const s = stub();
    const big = { width: 3840, height: 2160 };
    drawScene(s.ctx, doc([{ id: "b", type: "blur", colour: "#000000", stroke: "medium", x: 0, y: 0, width: 220, height: 44 }]), { ...original, ...big }, big, make);
    expect(mosaicCell(big)).toBe(22);
    expect(scratches[0]).toMatchObject({ width: 10, height: 2 });
  });

  it("a step is a filled circle of the stroke's radius with its number in the UI font; two sequences restart at 1", () => {
    const s = stub();
    drawScene(s.ctx, doc([
      { id: "s1", type: "step", colour: "#e53935", stroke: "thick", x: 10, y: 10, sequence: 0 },
      { id: "s2", type: "step", colour: "#e53935", stroke: "thick", x: 50, y: 10, sequence: 0 },
      { id: "s3", type: "step", colour: "#fdd835", stroke: "thin", x: 90, y: 10, sequence: 1 },
    ]), original, STILL, make);
    expect(of(s, "arc").map((c) => c.args)).toEqual([[10, 10, STEP_RADIUS.thick, 0, 2 * Math.PI], [50, 10, STEP_RADIUS.thick, 0, 2 * Math.PI], [90, 10, STEP_RADIUS.thin, 0, 2 * Math.PI]]);
    expect(of(s, "fillText").map((c) => c.args[0])).toEqual(["1", "2", "1"]);
    expect(set(s, "fillStyle")).toContain(textOn("#fdd835"));
    expect(set(s, "font").every((f) => String(f).includes(UI_FONT))).toBe(true);
  });

  it("text is wrapped to its box and drawn in the UI font at its size, clipped to the box; a callout fills its box and tail and writes in the contrasting colour", () => {
    const s = stub();
    drawScene(s.ctx, doc([{ id: "t", type: "text", colour: "#1e88e5", stroke: "medium", x: 10, y: 20, width: 8 * 7 + 2 * TEXT_PADDING, height: 60, text: "one two three", text_size: "small" }]), original, STILL, make);
    expect(set(s, "font")).toContain(fontFor(TEXT_PX.small));
    expect(of(s, "fillText").map((c) => c.args)).toEqual([["one two", 10 + TEXT_PADDING, 20 + TEXT_PADDING], ["three", 10 + TEXT_PADDING, 20 + TEXT_PADDING + TEXT_PX.small * 1.25]]);
    expect(of(s, "clip")).toHaveLength(1);
    expect(set(s, "fillStyle")).toContain("#1e88e5");
    const c = stub();
    drawScene(c.ctx, doc([{ id: "c", type: "callout", colour: "#fb8c00", stroke: "medium", x: 100, y: 100, width: 120, height: 40, tail_x: 50, tail_y: 200, text: "Look", text_size: "large" }]), original, STILL, make);
    expect(of(c, "fill")).toHaveLength(2);
    expect(of(c, "moveTo")[0].args).toEqual([50, 200]);
    expect(of(c, "fillText")[0].args[0]).toBe("Look");
    expect(set(c, "fillStyle")).toContain(textOn("#fb8c00"));
    expect(set(c, "font")).toContain(fontFor(TEXT_PX.large));
  });

  it("the lens copies the ORIGINAL's pixels at its factor, without smoothing, with a border in the stroke and a thin connector", () => {
    for (const factor of [2, 3] as const) {
      const s = stub();
      drawScene(s.ctx, doc([
        { id: "a", type: "arrow", colour: "#e53935", stroke: "medium", x1: 0, y1: 0, x2: 50, y2: 50 },
        { id: "m", type: "magnify", colour: "#43a047", stroke: "thick", x: 10, y: 20, width: 30, height: 40, lens_x: 300, lens_y: 400, factor },
      ]), original, STILL, make);
      const lens = of(s, "drawImage")[1];
      expect(lens.args).toEqual([original, 10, 20, 30, 40, 300, 400, 30 * factor, 40 * factor]);
      expect(set(s, "imageSmoothingEnabled")).toEqual([false]);
      const borders = of(s, "strokeRect").map((c) => c.args);
      expect(borders).toEqual([[10, 20, 30, 40], [300, 400, 30 * factor, 40 * factor]]);
      expect(set(s, "lineWidth")).toEqual([STROKE_PX.medium, STROKE_PX.thick, 1, 1.5, STROKE_PX.thick]);
      expect(of(s, "lineTo").at(-1)!.args).toEqual([300 + 15 * factor, 400 + 20 * factor]);
    }
  });
});

describe("the export and the preview", () => {
  const shapes: Shape[] = [
    { id: "a", type: "arrow", colour: "#e53935", stroke: "medium", x1: 0, y1: 0, x2: 500, y2: 500 },
    { id: "r", type: "rect", colour: "#1e88e5", stroke: "thin", x: 900, y: 700, width: 50, height: 50 },
  ];
  const crop = { x: 100, y: 50, width: 300, height: 200 };

  it("the export is the crop's size, the scene translated by the crop's origin, with nothing of the editor's overlay", () => {
    const s = stub();
    const size = renderExport(s.ctx, doc(shapes, crop), original, STILL, make);
    expect(size).toEqual({ width: 300, height: 200 });
    expect(of(s, "translate")[0].args).toEqual([-100, -50]);
    expect(of(s, "drawImage")[0].args).toEqual([original, 0, 0]);
    expect(of(s, "setLineDash")).toHaveLength(0);
    expect(of(s, "fillRect")).toHaveLength(0);
    expect(set(s, "fillStyle").some((v) => String(v).includes("rgba"))).toBe(false);
    // A shape outside the crop is still drawn - the canvas's edge clips it; nothing deleted it.
    expect(of(s, "strokeRect")[0].args).toEqual([900, 700, 50, 50]);
    expect(renderExport(stub().ctx, doc(shapes), original, STILL, make)).toEqual(STILL);
    const plain = stub();
    renderExport(plain.ctx, doc(shapes), original, STILL, make);
    expect(of(plain, "translate")).toHaveLength(0);
  });

  it("the preview draws the same scene and then dims outside the crop, outlines it, and draws the selection's handles", () => {
    const exp = stub();
    renderExport(exp.ctx, doc(shapes, crop), original, STILL, make);
    const pre = stub();
    renderPreview(pre.ctx, doc(shapes, crop), original, STILL, make, { selectedId: "r", cropHandles: true, zoom: 2 });
    // The scene: the export's calls without its translate and its outer save / restore, in the same order.
    const scene = (s: Stub) => s.calls.filter((c) => c.op !== "translate" && c.op !== "save" && c.op !== "restore");
    const exportScene = scene(exp);
    expect(scene(pre).slice(0, exportScene.length)).toEqual(exportScene);
    // Then the overlay: four dimming rectangles round the crop, the crop's outline dashed, eight crop handles and the box's eight.
    const dims = of(pre, "fillRect").slice(0, 4).map((c) => c.args);
    expect(dims).toEqual([[0, 0, 1000, 50], [0, 250, 1000, 550], [0, 50, 100, 200], [400, 50, 600, 200]]);
    expect(set(pre, "fillStyle")).toContain(`rgba(0, 0, 0, ${CROP_DIM_ALPHA})`);
    expect(of(pre, "setLineDash")[0].args).toEqual([[3, 2]]);
    expect(of(pre, "strokeRect").map((c) => c.args)).toContainEqual([100, 50, 300, 200]);
    // Handles: 8 for the crop + 8 for the selected box, each a 4-pixel square at zoom 2 (8 screen pixels).
    const handleRects = of(pre, "fillRect").slice(4);
    expect(handleRects).toHaveLength(16);
    expect(handleRects.every((c) => c.args[2] === 4 && c.args[3] === 4)).toBe(true);
    // No crop, nothing selected: the preview IS the scene.
    const bare = stub();
    renderPreview(bare.ctx, doc(shapes), original, STILL, make, { selectedId: null, cropHandles: false, zoom: 1 });
    expect(of(bare, "fillRect")).toHaveLength(0);
    expect(of(bare, "setLineDash")).toHaveLength(0);
  });
});
