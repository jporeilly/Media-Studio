/**
 * Two small browser side-effects the walkthrough's transcript needs - the Markdown onto the clipboard, and a
 * download of it as a file - kept here so the pure player stays free of any API and both the builder and the
 * player share them. Nothing here touches the server.
 */

/** The text onto the clipboard; false when the page may not write to it. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** A download of `text` as `fileName`, through a Blob URL the browser revokes after the click. */
export function downloadText(fileName: string, text: string, type = "text/markdown;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
