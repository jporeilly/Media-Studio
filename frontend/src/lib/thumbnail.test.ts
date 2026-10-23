// @vitest-environment jsdom
/**
 * The bar's thumbnail (W2d, R4; the fix round's F5): the picture is drawn 320 wide on a canvas filled with the bar's
 * background first, so a framed still's transparent padding comes out in the bar's colour rather than JPEG's black,
 * and encoded as a JPEG data URL; a canvas that cannot be had rejects, and the bitmap is closed either way. jsdom has
 * no canvas and no `createImageBitmap`, so both are stubbed and the calls recorded in order.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { THUMB_BACKGROUND, THUMB_QUALITY, THUMB_WIDTH, thumbnailOf } from "./thumbnail";

interface Call { name: string; args: unknown[] }

function fakeCanvas(calls: Call[], withContext = true) {
  const ctx = {
    fillStyle: "",
    fillRect: (...args: unknown[]) => { calls.push({ name: "fillRect", args: [ctx.fillStyle, ...args] }); },
    drawImage: (...args: unknown[]) => { calls.push({ name: "drawImage", args }); },
  };
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => (withContext ? ctx : null),
    toDataURL: (...args: unknown[]) => { calls.push({ name: "toDataURL", args }); return "data:image/jpeg;base64,thumb"; },
  };
  return canvas;
}

const original = document.createElement.bind(document);

afterEach(() => {
  vi.restoreAllMocks();
  delete (globalThis as { createImageBitmap?: unknown }).createImageBitmap;
});

describe("thumbnailOf", () => {
  it("fills the canvas with the bar's background, then draws the picture 320 wide, then encodes a JPEG; the bitmap is closed", async () => {
    const calls: Call[] = [];
    const canvas = fakeCanvas(calls);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => (tag === "canvas" ? (canvas as unknown as HTMLElement) : original(tag)));
    const bitmap = { width: 1200, height: 800, close: vi.fn() };
    (globalThis as { createImageBitmap?: unknown }).createImageBitmap = vi.fn(async () => bitmap);
    const blob = new Blob(["png"], { type: "image/png" });
    await expect(thumbnailOf(blob)).resolves.toBe("data:image/jpeg;base64,thumb");
    expect([canvas.width, canvas.height]).toEqual([320, 213]);
    expect(calls.map((c) => c.name)).toEqual(["fillRect", "drawImage", "toDataURL"]);
    // The fill is the bar's colour, over the whole canvas, BEFORE the picture (n7: JPEG has no alpha, so the padding would be black).
    expect(calls[0].args).toEqual([THUMB_BACKGROUND, 0, 0, 320, 213]);
    expect(calls[1].args).toEqual([bitmap, 0, 0, 320, 213]);
    expect(calls[2].args).toEqual(["image/jpeg", THUMB_QUALITY]);
    expect(bitmap.close).toHaveBeenCalledTimes(1);
    expect([THUMB_WIDTH, THUMB_QUALITY, THUMB_BACKGROUND]).toEqual([320, 0.7, "#0f172a"]);
  });

  it("a picture narrower than 320 keeps its width; no canvas context rejects, and the bitmap is still closed", async () => {
    const calls: Call[] = [];
    const canvas = fakeCanvas(calls);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => (tag === "canvas" ? (canvas as unknown as HTMLElement) : original(tag)));
    const small = { width: 200, height: 100, close: vi.fn() };
    (globalThis as { createImageBitmap?: unknown }).createImageBitmap = vi.fn(async () => small);
    await thumbnailOf(new Blob(["png"]));
    expect([canvas.width, canvas.height]).toEqual([200, 100]);
    const none = fakeCanvas([], false);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => (tag === "canvas" ? (none as unknown as HTMLElement) : original(tag)));
    const bitmap = { width: 1200, height: 800, close: vi.fn() };
    (globalThis as { createImageBitmap?: unknown }).createImageBitmap = vi.fn(async () => bitmap);
    await expect(thumbnailOf(new Blob(["png"]))).rejects.toThrow("No canvas.");
    expect(bitmap.close).toHaveBeenCalledTimes(1);
  });
});
