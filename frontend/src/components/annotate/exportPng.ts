/**
 * The export: a canvas of the output size (the crop's, else the still's), drawn by the SAME renderer the
 * preview uses (`lib/annotate.ts renderExport` -> `drawScene`), read out as a PNG. The server stores this
 * picture as it is and never re-renders it. The page's fonts are awaited first, as the preview awaits them
 * before its first draw, so the two never rasterise text in different faces.
 */
import { type AnnotationDocument, type Ctx2D, type ImageLike, type Scratch, type Size, outputSize, renderExport } from "../../lib/annotate";

/** The document's fonts, once loaded (resolves at once where the page has no FontFaceSet - jsdom). */
export function fontsReady(): Promise<void> {
  const fonts = typeof document !== "undefined" ? (document as Document & { fonts?: FontFaceSet }).fonts : undefined;
  return fonts ? fonts.ready.then(() => undefined) : Promise.resolve();
}

/** A scratch canvas for the mosaic (an offscreen element; its context and itself, to draw back). */
export function makeScratch(width: number, height: number): Scratch {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, width);
  canvas.height = Math.max(1, height);
  const ctx = canvas.getContext("2d") as unknown as Ctx2D | null;
  if (!ctx) throw new Error("The browser gave no drawing context.");
  return { ctx, image: canvas };
}

export async function exportPng(doc: AnnotationDocument, original: ImageLike, still: Size): Promise<Blob> {
  await fontsReady();
  const size = outputSize(doc, still);
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  const ctx = canvas.getContext("2d") as unknown as Ctx2D | null;
  if (!ctx) throw new Error("The browser gave no drawing context.");
  renderExport(ctx, doc, original, still, makeScratch);
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The picture could not be exported as a PNG."))), "image/png");
  });
}
