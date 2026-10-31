// @vitest-environment jsdom
/**
 * Export to course… (W2c, C8), mounted for real under jsdom with the server faked: the preview on opening (the
 * plan route, the player box sent), the placed / not placed / skipped / removed / new-in-guide lists, the files and
 * the diff collapsed, the setting's state deciding between Write to the course and Download zip, the write sending
 * the sha256 the preview showed and saying the server's line, the 409 shown with Preview again, the player box
 * re-previewing with the player and pointing the zip at it, and a walkthrough that was not imported offering only
 * its player. Nothing here touches the screen: the browser edition has it all.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api/client";
import type { ExportPlan, ExportResult } from "../../lib/courseExport";
import { ExportCourseDialog } from "./ExportCourseDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../api/client")>();
  return { ...real, api: { ...real.api, post: vi.fn() } };
});

const WID = "abc123abc123";
const SHA = "a".repeat(64);
const SHA2 = "c".repeat(64);
const FIRST = {
  slug: "01-first", title: "First", guide: "demo/01-first/guide.md", guide_sha256: SHA,
  placed: [
    { number: 2, step_id: "s2s2s2s2s2s2", text: "Right-click Valid rows.", image: "wt-01-first-02-s2s2s2s2s2s2.png", hotspot: { x: "45.0", y: "23.3" }, line: 9, how: "new" as const },
    { number: 4, step_id: "s4s4s4s4s4s4", text: "Select Cancel.", image: "wt-01-first-04-s4s4s4s4s4s4.png", hotspot: null, line: 30, how: "unchanged" as const },
  ],
  not_placed: [{ number: 5, step_id: "s5s5s5s5s5s5", text: "Select Close.", reason: "Its text is not in the guide any more (the item was reworded, moved or removed)." }],
  skipped: [{ number: 1, step_id: "s1s1s1s1s1s1", text: "Open it.", reason: "No picture yet." }],
  removed: [{ step_id: "s9s9s9s9s9s9", line: 40, reason: "The step is no longer in the walkthrough." }],
  new_in_guide: [{ text: "Press Cancel to leave.", line: 33 }],
  kept: [{ path: "_assets/images/wt-01-first-abc123abc123-s9s9s9s9s9s9.png", reason: "still used by 02-other" }],
  figures: 2,
  changed: true,
  diff: "--- a/01-first/guide.md\n+++ b/01-first/guide.md\n@@ -9,0 +10,3 @@\n+<!-- media-studio -->",
};
const PLAN: ExportPlan = {
  course: { id: "demo", title: "Demo", version: "1.0" },
  workshop: { slug: "01-first", title: "First" },
  guide: "demo/01-first/guide.md",
  guide_sha256: SHA,
  guides: [FIRST],
  placed: FIRST.placed,
  not_placed: FIRST.not_placed,
  skipped: FIRST.skipped,
  removed: FIRST.removed,
  new_in_guide: FIRST.new_in_guide,
  kept: FIRST.kept,
  debris: [{ path: "01-first/.guide.md.1234abcd.keep", reason: "left by an interrupted export" }],
  files: [{ path: "01-first/guide.md", action: "write" }, { path: "_assets/images/wt-01-first-02-s2s2s2s2s2s2.png", action: "write" }],
  changed: true,
  figures: 2,
  player: false,
  player_caption: "",
  player_lines: [],
  player_block: null,
  diff: FIRST.diff,
  allowed: false,
};
/** W2e: a walkthrough of two workshops - a block per guide, and a step of no guide (added from a still). */
const SECOND = {
  slug: "02-second", title: "Second", guide: "demo/02-second/guide.md", guide_sha256: SHA2,
  placed: [{ number: 6, step_id: "s6s6s6s6s6s6", text: "Drag Text file input.", image: "wt-02-second-abc123abc123-s6s6s6s6s6s6.png", hotspot: { x: "45.0", y: "23.3" }, line: 5, how: "new" as const }],
  not_placed: [], skipped: [], removed: [], new_in_guide: [], kept: [], figures: 1, changed: true,
  diff: "--- a/02-second/guide.md\n+++ b/02-second/guide.md\n@@ -5,0 +6,3 @@\n+<!-- media-studio -->",
};
const HOMELESS = { number: 9, step_id: "s9s9s9s9s9s9", text: "Added later.", reason: "Added in the walkthrough, not imported: with several workshops it names no guide to go into, and none is guessed." };
/** X5: a step of no guide WITHOUT a picture: skipped (counted in the first line), in no guide's own list. */
const HOMELESS_SKIPPED = { number: 10, step_id: "sasasasasasa", text: "Nothing yet.", reason: "No picture yet." };
const PLAN2: ExportPlan = {
  ...PLAN, guides: [FIRST, SECOND], placed: [...FIRST.placed, ...SECOND.placed], not_placed: [...FIRST.not_placed, HOMELESS],
  skipped: [...FIRST.skipped, HOMELESS_SKIPPED], figures: 3,
  files: [...PLAN.files, { path: "02-second/guide.md", action: "write" }], diff: `${FIRST.diff}\n${SECOND.diff}`,
};
/** B3: the figure the guide gets with the player, as the server's `player_lines` answers it. */
const PLAYER_LINES = (caption: string) => [
  '<figure class="pcm-walkthrough">', "",
  '<iframe src="walkthrough/index.html" title="Interactive walkthrough: Tour" loading="lazy"></iframe>', "",
  `<figcaption>${caption}. Click where the beacon pulses; right-click or double-click when the tooltip says so.</figcaption>`, "",
  "</figure>",
];
const WRITTEN: ExportResult = {
  course: "demo", slug: "01-first", guide: "demo/01-first/guide.md", figures: 2, images: 1, player: false, guide_written: true,
  guides: [{ slug: "01-first", guide: "demo/01-first/guide.md", figures: 2, guide_written: true, guide_sha256: "b".repeat(64) }],
  written: ["_assets/images/wt-01-first-02-s2s2s2s2s2s2.png", "01-first/guide.md"], removed: [], guide_sha256: "b".repeat(64),
  message: "Wrote 2 figures into demo/01-first/guide.md. Review and publish it in the Content Editor.",
};

let root: Root | null = null;
let host: HTMLDivElement | null = null;
let closed = 0;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client: qc }, createElement(ExportCourseDialog, { open: true, wid: WID, onClose: () => { closed += 1; } })));
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const text = (sel: string) => (q(sel)?.textContent || "").replace(/\s+/g, " ").trim();
const button = (label: string) => [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === label) ?? null;
const posts = () => vi.mocked(api.post).mock.calls;

beforeEach(() => {
  closed = 0;
  vi.mocked(api.post).mockReset();
  vi.mocked(api.post).mockImplementation(async (path: string, body: unknown) => {
    if (path.endsWith("/export/course/plan")) return { ...PLAN, player: !!(body as { player: boolean }).player } as never;
    throw new Error(`unexpected ${path}`);
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("the preview", () => {
  it("previews on opening and shows what goes where, what does not and why - nothing written", async () => {
    await mount();
    expect(posts()).toEqual([[`/api/walkthroughs/${WID}/export/course/plan`, { player: false }]]);
    expect(text("[data-export-line]")).toBe("2 figures into demo/01-first/guide.md; 1 step not placed; 1 skipped");
    const placed = [...document.querySelectorAll("[data-export-placed] li")].map((li) => (li.textContent || "").replace(/\s+/g, " ").trim());
    expect(placed).toEqual([
      "Step 2Right-click Valid rows.pin at 45.0 %, 23.3 % · after line 9",
      "Step 4Select Cancel.no hotspot: a plain figure · unchanged at line 30",
    ]);
    expect(text("[data-export-not-placed]")).toContain("Step 5: Select Close. - Its text is not in the guide any more");
    expect(text("[data-export-skipped]")).toContain("Step 1: Open it. - No picture yet.");
    expect(text("[data-export-removed]")).toContain("The figure at line 40 - The step is no longer in the walkthrough.");
    expect(text("[data-export-new-in-guide]")).toContain("Line 33: Press Cancel to leave.");
    expect(text("[data-export-kept]")).toContain("_assets/images/wt-01-first-abc123abc123-s9s9s9s9s9s9.png - still used by 02-other");
    expect(text("[data-export-debris]")).toContain("01-first/.guide.md.1234abcd.keep - left by an interrupted export");
    // The intro states which content manager draws the pin (recheck NIT-5; W2d fix F2: from 0.7.42).
    expect(text("[data-export-caveat]")).toBe(
      "The pins show in a Content Manager from 0.7.42 (a Content Editor from 1.30.0); an older one shows the picture, the pin's text as a numbered line and the caption. The interactive player needs a Content Manager from 0.7.44 to show the embedded player; an older one shows an empty frame and the caption. Export into a published course once its readers have that version.",
    );
    // Without the player ticked there is no caption to edit and no figure to show.
    expect(q("[data-export-player-caption]")).toBeNull();
    expect(q("[data-export-player-figure]")).toBeNull();
    // The files and the diff are there, collapsed.
    expect((q("[data-export-files]") as HTMLDetailsElement).open).toBe(false);
    expect(text("[data-export-files] summary")).toBe("2 files to write");
    expect((q("[data-export-diff]") as HTMLDetailsElement).open).toBe(false);
    expect(q("[data-export-diff] pre")!.textContent).toBe(PLAN.diff);
  });

  it("with the setting off: no Write to the course, the zip is the way, and the player beside it", async () => {
    await mount();
    expect(text("[data-export-setting]")).toContain("Allow export into the Courses folder is off");
    expect(q("[data-export-write]")).toBeNull();
    expect(q("[data-export-zip]")!.getAttribute("href")).toBe(`/api/walkthroughs/${WID}/export/course.zip`);
    expect(q("[data-export-zip]")!.className).toContain("os-btn-primary");
    expect(q("[data-export-player]")!.getAttribute("href")).toBe(`/api/walkthroughs/${WID}/export/player.zip`);
    await act(async () => { button("Close")!.click(); });
    expect(closed).toBe(1);
  });

  it("the player box previews again with the player, and the zip carries it", async () => {
    await mount();
    await act(async () => { (q("[data-export-with-player]") as HTMLInputElement).click(); });
    await settle();
    expect(posts().at(-1)).toEqual([`/api/walkthroughs/${WID}/export/course/plan`, { player: true }]);
    expect(q("[data-export-zip]")!.getAttribute("href")).toBe(`/api/walkthroughs/${WID}/export/course.zip?player=1`);
  });

  it("B3: with the player ticked the dialog shows the figure the guide gets, where it goes and its caption - edited, the caption previews again and travels with the write and the zip", async () => {
    let wrote = false;
    vi.mocked(api.post).mockImplementation(async (path: string, body: unknown) => {
      const b = body as { player: boolean; caption?: string };
      if (path.endsWith("/export/course/plan")) {
        const caption = b.caption ?? "Walkthrough: Tour";
        return {
          ...PLAN, allowed: true, changed: !wrote, player: b.player,
          player_caption: b.player ? caption : "",
          player_lines: b.player ? PLAYER_LINES(caption) : [],
          player_block: b.player ? { how: "placeholder", line: 11, heading: "" } : null,
        } as never;
      }
      if (path.endsWith("/export/course")) { wrote = true; return { ...WRITTEN, player: true } as never; }
      throw new Error(`unexpected ${path}`);
    });
    await mount();
    await act(async () => { (q("[data-export-with-player]") as HTMLInputElement).click(); });
    await settle();
    expect(posts().at(-1)).toEqual([`/api/walkthroughs/${WID}/export/course/plan`, { player: true }]);
    const caption = q("[data-export-player-caption]") as HTMLInputElement;
    expect(caption.value).toBe("Walkthrough: Tour");
    expect(caption.maxLength).toBe(200);
    expect(text("[data-export-player-where]")).toBe("The figure replaces the Interactive walkthrough placeholder at line 11.");
    expect(q("[data-export-player-figure] pre")!.textContent).toBe(PLAYER_LINES("Walkthrough: Tour").join("\n"));
    // Typing changes nothing until the field is left; then the plan is read again with the caption.
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(caption, "Walkthrough: the first win");
      caption.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(posts().filter(([path]) => String(path).endsWith("/plan")).length).toBe(2);
    await act(async () => { caption.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
    await settle();
    expect(posts().at(-1)).toEqual([`/api/walkthroughs/${WID}/export/course/plan`, { player: true, caption: "Walkthrough: the first win" }]);
    expect(q("[data-export-player-figure] pre")!.textContent).toContain("Walkthrough: the first win. Click where the beacon pulses");
    expect(q("[data-export-zip]")!.getAttribute("href")).toBe(`/api/walkthroughs/${WID}/export/course.zip?player=1&caption=Walkthrough%3A+the+first+win`);
    await act(async () => { (q("[data-export-write]") as HTMLButtonElement).click(); });
    await settle();
    expect(posts().filter(([path]) => String(path).endsWith("/export/course"))).toEqual([
      [`/api/walkthroughs/${WID}/export/course`, { guides: [{ slug: "01-first", sha256: SHA }], player: true, caption: "Walkthrough: the first win" }],
    ]);
  });

  it("W2e: a walkthrough of two workshops - the combined line, a block per guide with its own line, lists and diff folded, the step of no guide", async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith("/export/course/plan")) return { ...PLAN2, allowed: true } as never;
      if (path.endsWith("/export/course")) return { ...WRITTEN, figures: 3 } as never;
      throw new Error(`unexpected ${path}`);
    });
    await mount();
    expect(text("[data-export-line]")).toBe("3 figures into 2 guides; 2 steps not placed; 2 skipped");
    const blocks = [...document.querySelectorAll("[data-export-guide]")];
    expect(blocks.map((b) => b.getAttribute("data-export-guide"))).toEqual(["01-first", "02-second"]);
    expect(text('[data-export-guide="01-first"] [data-export-guide-line]')).toBe("2 figures into demo/01-first/guide.md; 1 step not placed; 1 skipped");
    expect(text('[data-export-guide="02-second"] [data-export-guide-line]')).toBe("1 figure into demo/02-second/guide.md");
    expect([...document.querySelectorAll('[data-export-guide="02-second"] [data-export-placed] li')].map((li) => (li.textContent || "").replace(/\s+/g, " ").trim())).toEqual([
      "Step 6Drag Text file input.pin at 45.0 %, 23.3 % · after line 5",
    ]);
    expect(text('[data-export-guide="01-first"] [data-export-not-placed]')).toContain("Step 5: Select Close.");
    expect(text('[data-export-guide="01-first"] [data-export-kept]')).toContain("still used by 02-other");
    // The step of no guide is listed once, above the guides, with its reason.
    expect(text("[data-export-homeless]")).toContain("Step 9: Added later. - Added in the walkthrough, not imported");
    expect(q('[data-export-guide="01-first"] [data-export-not-placed]')!.textContent).not.toContain("Added later");
    // X5: a step of no guide without a picture is listed there too, as skipped, with its reason - and in no guide's own list.
    expect(text("[data-export-homeless-skipped]")).toContain("Step 10: Nothing yet. - No picture yet.");
    expect(text("[data-export-homeless-skipped]")).not.toContain("Added later");
    expect([...document.querySelectorAll("[data-export-guide] [data-export-skipped]")].map((el) => el.textContent || "").join(" ")).not.toContain("Nothing yet");
    // Each guide's diff, folded.
    const diffs = [...document.querySelectorAll("[data-export-guide] [data-export-diff]")] as HTMLDetailsElement[];
    expect(diffs.map((d) => d.open)).toEqual([false, false]);
    expect(diffs.map((d) => d.querySelector("pre")!.textContent)).toEqual([FIRST.diff, SECOND.diff]);
    // The write sends every guide's sha256 from the preview.
    await act(async () => { (q("[data-export-write]") as HTMLButtonElement).click(); });
    await settle();
    expect(posts().filter(([path]) => String(path).endsWith("/export/course"))).toEqual([
      [`/api/walkthroughs/${WID}/export/course`, { guides: [{ slug: "01-first", sha256: SHA }, { slug: "02-second", sha256: SHA2 }], player: false }],
    ]);
  });

  it("a walkthrough that was not imported says so and offers only its player", async () => {
    vi.mocked(api.post).mockImplementation(async () => {
      throw new ApiError(400, "This walkthrough was not imported from a course workshop: only the player can be exported.");
    });
    await mount();
    expect(text(".os-error, [role=alert]") || document.body.textContent).toContain("only the player can be exported");
    expect(q("[data-export-zip]")).toBeNull();
    expect(q("[data-export-write]")).toBeNull();
    expect(q("[data-export-player]")).not.toBeNull();
  });
});

describe("the write", () => {
  beforeEach(() => {
    let wrote = false;
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith("/export/course/plan")) return { ...PLAN, allowed: true, changed: !wrote } as never;
      if (path.endsWith("/export/course")) { wrote = true; return WRITTEN as never; }
      throw new Error(`unexpected ${path}`);
    });
  });

  it("writes with the sha256 the preview showed, says the server's line and previews again", async () => {
    await mount();
    expect(text("[data-export-setting]")).toContain("is on");
    expect(q("[data-export-zip]")!.className).toContain("os-btn-secondary");
    const write = q("[data-export-write]") as HTMLButtonElement;
    expect(write.textContent).toContain("Write to the course");
    expect(write.disabled).toBe(false);
    await act(async () => { write.click(); });
    await settle();
    expect(posts().filter(([path]) => String(path).endsWith("/export/course"))).toEqual([[`/api/walkthroughs/${WID}/export/course`, { guides: [{ slug: "01-first", sha256: SHA }], player: false }]]);
    expect(text("[data-export-written]")).toBe(WRITTEN.message);
    expect(posts().filter(([path]) => String(path).endsWith("/plan")).length).toBe(2);
    expect((q("[data-export-write]") as HTMLButtonElement).disabled).toBe(true);
  });

  it("a guide changed since the preview: the server's line, and Preview again reads it afresh", async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path.endsWith("/export/course/plan")) return { ...PLAN, allowed: true } as never;
      throw new ApiError(409, "The guide demo/01-first/guide.md changed since the preview: preview again.");
    });
    await mount();
    await act(async () => { (q("[data-export-write]") as HTMLButtonElement).click(); });
    await settle();
    expect(document.body.textContent).toContain("The guide demo/01-first/guide.md changed since the preview: preview again.");
    await act(async () => { (q("[data-export-again]") as HTMLButtonElement).click(); });
    await settle();
    expect(document.body.textContent).not.toContain("changed since the preview");
    expect(posts().filter(([path]) => String(path).endsWith("/plan")).length).toBe(2);
  });
});
