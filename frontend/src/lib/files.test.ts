// @vitest-environment jsdom
/**
 * The two browser side-effects the transcript needs (lib/files.ts): the clipboard write answers true only when the
 * browser took the text - a refusal, or no clipboard at all, answers false and never throws - and the download is
 * an anchor click with the file's name over a Blob URL that is revoked afterwards.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText, downloadText } from "./files";

afterEach(() => {
  delete (navigator as unknown as { clipboard?: unknown }).clipboard;
  vi.restoreAllMocks();
});

describe("copyText", () => {
  it("answers true when the browser takes the text", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    expect(await copyText("# Tour")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("# Tour");
  });

  it("answers false, never throws, when the page may not write to the clipboard", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async () => { throw new DOMException("Write permission denied.", "NotAllowedError"); }) } });
    expect(await copyText("# Tour")).toBe(false);
  });

  it("answers false, never throws, when there is no clipboard at all", async () => {
    delete (navigator as unknown as { clipboard?: unknown }).clipboard;
    expect(await copyText("# Tour")).toBe(false);
  });
});

describe("downloadText", () => {
  it("clicks an anchor named after the file over a Blob URL, then revokes the URL", async () => {
    const urls = globalThis.URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
    const hadCreate = urls.createObjectURL, hadRevoke = urls.revokeObjectURL;
    const create = vi.fn(() => "blob:transcript");
    const revoke = vi.fn();
    urls.createObjectURL = create;
    urls.revokeObjectURL = revoke;
    const clicked: { download: string; href: string }[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push({ download: this.download, href: this.href }); });
    try {
      downloadText("Tour.md", "# Tour\n");
      expect(clicked).toEqual([{ download: "Tour.md", href: "blob:transcript" }]);
      const blob = (create.mock.calls[0] as unknown as [Blob])[0];
      expect(blob).toBeInstanceOf(Blob);
      expect(blob.type).toBe("text/markdown;charset=utf-8");
      expect(await blob.text()).toBe("# Tour\n");
      expect(document.querySelector("a[download]")).toBeNull(); // the anchor is removed again
      await new Promise((r) => setTimeout(r, 5));
      expect(revoke).toHaveBeenCalledWith("blob:transcript");
    } finally {
      click.mockRestore();
      urls.createObjectURL = hadCreate;
      urls.revokeObjectURL = hadRevoke;
    }
  });
});
