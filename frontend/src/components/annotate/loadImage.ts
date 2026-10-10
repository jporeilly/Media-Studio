/**
 * The editor's base picture: the ORIGINAL still, loaded as an image element the canvas can draw. An
 * `<img>` rather than a fetch-and-decode so the session cookie does the authentication and nothing is
 * held twice; the record was fetched first, so a 404 or a 403 has already been answered with its reason.
 * Mocked in the page's test (jsdom loads no pictures).
 */
export function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("The still could not be loaded."));
    img.src = url;
  });
}
