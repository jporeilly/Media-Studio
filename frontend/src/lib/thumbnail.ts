/**
 * The thumbnail the capture bar shows of the last still (W2d, R4). Made by the MAIN page - it has the session and
 * the cookie - from the attached picture, and sent to the bar IN the state payload as a data URL, so the bar page
 * never fetches anything. One function over the browser's canvas, which the tests stub: a picture that cannot be
 * drawn (a blob that is no image, no canvas) rejects, and the hook then sends no thumbnail - the picture is on the
 * step either way.
 */

/** The thumbnail's width in CSS pixels; the height follows the picture. */
export const THUMB_WIDTH = 320;
/** JPEG, at this quality: a few tens of kilobytes a thumbnail, small enough to ride in every payload. */
export const THUMB_QUALITY = 0.7;
/**
 * The bar's background (`public/capture-bar.html`'s `.steps`), painted under the picture first: JPEG has no alpha, so
 * a framed still's transparent padding would come out black otherwise (F5).
 */
export const THUMB_BACKGROUND = "#0f172a";

/** A JPEG data URL of `blob`, `THUMB_WIDTH` wide, on the bar's background. Rejects when the picture cannot be decoded or drawn. */
export async function thumbnailOf(blob: Blob): Promise<string> {
  const bitmap = await createImageBitmap(blob);
  try {
    const width = Math.max(1, Math.min(THUMB_WIDTH, bitmap.width));
    const height = Math.max(1, Math.round((bitmap.height * width) / Math.max(1, bitmap.width)));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("No canvas.");
    ctx.fillStyle = THUMB_BACKGROUND;
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(bitmap, 0, 0, width, height);
    return canvas.toDataURL("image/jpeg", THUMB_QUALITY);
  } finally {
    bitmap.close();
  }
}
