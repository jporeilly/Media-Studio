// @vitest-environment jsdom
/**
 * Workshop video - the one canvas (C1-C6), mounted for real under jsdom with the server faked: the five stages
 * across the top with the current one open and the others one line each; the Workshop stage lists the course's
 * workshops by section with their status lines and Open - the walkthrough a workshop has is opened, a workshop
 * without one is imported first (all chapters, the default name) - and moves to Capture; the Capture stage counts the
 * pictures and, in a browser, explains that capturing needs the desktop app and offers Next; the Edit stage renders
 * the builder inline without the stage buttons and its Next saves first when dirty; the Video stage lays the one form
 * out inline; the Publish stage asks the editor, summarises the diff, prefills the message and publishes through the
 * proxy, then says so - and says the link was not placed when the export setting is off.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { VideoToCourseDefaults } from "../lib/videoToCourse";
import type { Step, Walkthrough } from "../lib/walkthrough";
import { SAVE_BEFORE_NEXT, type WorkshopStatusPayload } from "../lib/workshopVideo";
import WorkshopVideoPage, { CAPTURE_BROWSER_LINE } from "./WorkshopVideo";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});
vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", username: "olive", display_name: "Olive", role: "editor" } }) }));
vi.mock("../lib/useCaptureAvailable", () => ({ useCaptureAvailable: () => false }));

const WID = "abc123abc123";
const COURSES = {
  configured: true, message: null,
  courses: [{
    id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.23", description: "",
    sections: [
      { title: "Part one", workshops: [
        { slug: "01-first-win", title: "Your First Win", description: "", estimatedMinutes: 20, stepCount: 10 },
        { slug: "02-build", title: "Build the Pipeline", description: "", estimatedMinutes: 30, stepCount: 23 },
      ] },
    ],
  }],
};
const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: null, kind: "tooltip", text: `Text ${id}`, instruction: "", placement: "auto", chapter: "", note: "",
  source: { course: "pdi-2hr-lab", slug: "01-first-win", image: null, alt: "", launcher: null }, click: "left", marker: "box", ...over,
});
const record = (over: Partial<Walkthrough> = {}): Walkthrough => ({
  id: WID, name: "Your First Win", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z",
  revision: 2, version: 1, steps: [step("aaaaaaaaaaaa", { chapter: "Start" }), step("bbbbbbbbbbbb", { image: null, width: 0, height: 0 })],
  source: { course: "pdi-2hr-lab", slug: "01-first-win", title: "PDI in 2 Hours", version: "0.1.23" }, ...over,
});
const DEFAULTS: VideoToCourseDefaults = {
  name: "Your First Win", privacy: "unlisted", privacies: [{ value: "unlisted", label: "Unlisted" }], token_set: true, allowed: true,
  course: { course: "pdi-2hr-lab", slug: "01-first-win", title: "PDI in 2 Hours" },
  place: { how: "placeholder", line: 23, heading: "", guide: "pdi-2hr-lab/01-first-win/guide.md", existing_caption: null, caption: "Your First Win", placed: false, error: null },
  project: null, job_kind: "video_to_course",
};

const server = {
  status: { course: "pdi-2hr-lab", workshops: { "01-first-win": { walkthrough: null, project: null }, "02-build": { walkthrough: null, project: null } } } as WorkshopStatusPayload,
  record: record(),
  defaults: DEFAULTS as VideoToCourseDefaults,
  config: { address: "http://localhost:8000", reachable: true, url: "https://github.com/jporeilly/Pentaho-Courses.git", ref: "main", message: null } as Record<string, unknown>,
  diff: { summary: "2 files change upstream: 01-first-win/guide.md, 01-first-win/manifest.json", upToDate: false, newCourse: false, added: [], modified: ["01-first-win/guide.md", "01-first-win/manifest.json"], removed: [], skippedIgnored: [], remoteCommit: "abc" } as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
};

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: ["/walkthroughs/workshop"] },
          createElement(Routes, null, createElement(Route, { path: "/walkthroughs/workshop", element: createElement(WorkshopVideoPage) })),
        ),
      ),
    );
  });
  // The courses, then (once the first is chosen) the status: two reads before the Workshop stage is whole.
  await settle();
  await settle();
}
const settle = (ms = 40) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const qa = (sel: string) => [...document.querySelectorAll(sel)] as HTMLElement[];
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
  server.status = { course: "pdi-2hr-lab", workshops: { "01-first-win": { walkthrough: null, project: null }, "02-build": { walkthrough: null, project: null } } };
  server.record = record();
  server.defaults = { ...DEFAULTS };
  server.config = { address: "http://localhost:8000", reachable: true, url: "https://github.com/jporeilly/Pentaho-Courses.git", ref: "main", message: null };
  server.posts = [];
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === "/api/courses") return COURSES as never;
    if (path === "/api/courses/pdi-2hr-lab/workshop-status") return server.status as never;
    if (path === `/api/walkthroughs/${WID}`) return server.record as never;
    if (path === `/api/walkthroughs/${WID}/video-to-course`) return server.defaults as never;
    if (path === "/api/courses/publish/config") return server.config as never;
    if (path === "/api/courses/pdi-2hr-lab/publish/diff") return server.diff as never;
    if (path.endsWith("/job")) return { active_job: null } as never;
    return {} as never;
  });
  vi.mocked(api.post).mockImplementation(async (path: string, body?: unknown) => {
    server.posts.push({ path, body });
    if (path === "/api/walkthroughs/import") {
      // The server now has the walkthrough: the status says so on the next read.
      server.status = { ...server.status, workshops: { ...server.status.workshops, "01-first-win": { walkthrough: { id: WID, name: "Your First Win", step_count: 2, pictured: 1, updated_at: null, created_at: null }, project: null } } };
      return record({ name: "Your First Win" }) as never;
    }
    if (path === "/api/courses/pdi-2hr-lab/publish") return { ok: true, upToDate: false, commit: "9f8e7d6c5b4a", summary: "committed 9f8e7d6 and pushed (2 files)" } as never;
    return {} as never;
  });
  vi.mocked(api.put).mockImplementation(async (_path: string, body: unknown) => {
    const sent = body as { name: string; revision: number; steps: Step[] };
    server.record = record({ name: sent.name.trim(), revision: sent.revision + 1, steps: sent.steps });
    return server.record as never;
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

const stageStates = () => qa("[data-stage]").map((el) => `${el.getAttribute("data-stage")}:${el.getAttribute("data-stage-state")}`);

describe("the canvas (C1, C2)", () => {
  it("shows the five stages with Workshop open, the others collapsed to a line and not yet openable", async () => {
    await mount();
    expect(qa("[data-stage]").map((el) => el.textContent?.split(/\d/)[1]?.trim().split(/[A-Z][a-z]+ /)[0])).toHaveLength(5);
    expect(stageStates()).toEqual(["workshop:current", "capture:later", "edit:later", "video:later", "publish:later"]);
    expect(q('[data-stage-line="capture"]')!.textContent).toBe("Capture the steps as you perform them (the desktop app).");
    expect(q('[data-stage-line="video"]')!.textContent).toBe("Render the video, publish it to Vimeo and place the link in the guide: one button.");
    expect((q('[data-stage-open="edit"]') as HTMLButtonElement).disabled).toBe(true);
    expect((q("[data-canvas-course]") as HTMLSelectElement).value).toBe("pdi-2hr-lab");
    const rows = qa("[data-canvas-workshop]");
    expect(rows.map((r) => r.getAttribute("data-canvas-workshop"))).toEqual(["01-first-win", "02-build"]);
    expect(rows[0].querySelector("[data-canvas-status]")!.textContent).toBe("no walkthrough yet");
    expect(rows[0].querySelector("button")!.textContent).toContain("Import and open");
  });

  it("Open imports a workshop without a walkthrough (all chapters, the default name) and moves to Capture; one with a walkthrough opens it", async () => {
    server.status.workshops["02-build"] = {
      walkthrough: { id: WID, name: "Build the Pipeline", step_count: 23, pictured: 9, updated_at: null, created_at: null },
      project: { id: "p1", name: "Build", created_at: "2026-10-31T13:00:00Z", rendered_at: "2026-10-31T14:02:00Z", has_video: true, vimeo: { id: "1", link: "https://vimeo.com/1/h", privacy: "unlisted", published_at: null, previous: [] }, placed: true, guide: "pdi-2hr-lab/02-build/guide.md" },
    };
    await mount();
    const rows = qa("[data-canvas-workshop]");
    expect(rows[1].querySelector("[data-canvas-status]")!.textContent).toMatch(/^walkthrough: 9 of 23 steps have a picture · video rendered 2026-10-31 \d\d:\d\d · on Vimeo \(Unlisted\) · placed in the guide$/);
    expect(rows[1].querySelector("button")!.textContent).toContain("Open");
    await act(async () => { (q('[data-canvas-open="01-first-win"]') as HTMLButtonElement).click(); });
    await settle();
    await settle();
    expect(server.posts).toEqual([{ path: "/api/walkthroughs/import", body: { course: "pdi-2hr-lab", slug: "01-first-win" } }]);
    expect(q("[data-canvas-notice]")!.textContent).toContain('Imported "Your First Win" from pdi-2hr-lab/01-first-win.');
    expect(stageStates()).toEqual(["workshop:reached", "capture:current", "edit:later", "video:later", "publish:later"]);
    expect(q('[data-stage-line="workshop"]')!.textContent).toBe("PDI in 2 Hours · Your First Win");
    // In a browser the stage explains and offers Next; the list says which steps have a picture.
    expect(q('[data-stage-body="capture"]')!.textContent).toContain(CAPTURE_BROWSER_LINE);
    expect(q("[data-canvas-capture-steps]")).toBeNull();
    expect(q("[data-canvas-record]")).toBeNull();
    expect(q("[data-canvas-captured]")!.textContent).toBe("1 of 2 steps has a picture");
    expect(qa("[data-canvas-step]").map((el) => el.getAttribute("data-has-picture"))).toEqual(["yes", "no"]);
    expect((q('[data-canvas-next="edit"]') as HTMLButtonElement).textContent).toContain("Next: Edit");

    // Back to Workshop, then Open on the workshop that has one: no import.
    await act(async () => { (q('[data-stage-open="workshop"]') as HTMLButtonElement).click(); });
    await settle();
    await act(async () => { (q('[data-canvas-open="02-build"]') as HTMLButtonElement).click(); });
    await settle();
    await settle();
    expect(server.posts).toHaveLength(1);
    expect(q("[data-canvas-notice]")).toBeNull();
    expect(stageStates()[1]).toBe("capture:current");
  });
});

describe("Edit, Video and Publish (C4-C6)", () => {
  async function toEdit() {
    await mount();
    await act(async () => { (q('[data-canvas-open="01-first-win"]') as HTMLButtonElement).click(); });
    await settle();
    await settle();
    await act(async () => { (q('[data-canvas-next="edit"]') as HTMLButtonElement).click(); });
    await settle();
    await settle();
  }

  it("renders the builder inline without the stage buttons, and its Next saves first when dirty (asked), then opens Video with the form inline", async () => {
    await toEdit();
    expect(stageStates()[2]).toBe("edit:current");
    expect(q("[data-builder-toolbar]")).not.toBeNull();
    expect(q("[data-make-video]")).toBeNull();
    expect(q("[data-export-course]")).toBeNull();
    expect(q("[data-video-to-course]")).toBeNull();
    expect(q("[data-capture-steps]")).toBeNull();
    expect(q("[data-record]")).toBeNull();
    expect(button("Save")).not.toBeNull();
    expect(button("Play")).not.toBeNull();
    expect(q("[data-builder-next]")!.textContent).toContain("Next: Video");
    // A change, then Next: asked, saved, moved on.
    const name = q("[data-walkthrough-name]") as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "Your First Win, edited");
      name.dispatchEvent(new Event("input", { bubbles: true }));
      name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    await act(async () => { (q("[data-builder-next]") as HTMLButtonElement).click(); });
    await settle();
    expect(document.body.textContent).toContain(SAVE_BEFORE_NEXT.title);
    await act(async () => { button("Cancel")!.click(); });
    await settle();
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
    expect(stageStates()[2]).toBe("edit:current");
    // Entering Video reads the defaults AGAIN (the Edit stage may have renamed the walkthrough): the form prefills from the read.
    const reads = () => vi.mocked(api.get).mock.calls.filter((c) => c[0] === `/api/walkthroughs/${WID}/video-to-course`).length;
    const readsBefore = reads();
    await act(async () => { (q("[data-builder-next]") as HTMLButtonElement).click(); });
    await settle();
    await act(async () => { button(SAVE_BEFORE_NEXT.confirmLabel)!.click(); });
    await settle();
    expect(vi.mocked(api.put)).toHaveBeenCalledTimes(1);
    expect((vi.mocked(api.put).mock.calls[0][1] as { name: string }).name).toBe("Your First Win, edited");
    expect(stageStates()[3]).toBe("video:current");
    expect(q("[data-vtc-form]")).not.toBeNull();
    expect(reads()).toBeGreaterThan(readsBefore);
    expect((q("[data-vtc-name]") as HTMLInputElement).value).toBe("Your First Win");
    expect(q("[data-vtc-where]")!.textContent).toContain("the Walkthrough placeholder at line 23");
    expect(q("[data-vtc-start]")!.textContent).toContain("Render, publish and place");
    expect(q('[data-canvas-next="publish"]')).not.toBeNull();
    expect(q('[data-stage-line="edit"]')!.textContent).toBe("Your First Win, edited · 2 steps · 1 chapter");
  });

  it("Publish asks the editor, summarises the diff, prefills the message, publishes through the proxy and says so", async () => {
    await toEdit();
    await act(async () => { (q("[data-builder-next]") as HTMLButtonElement).click(); });
    await settle();
    await act(async () => { (q('[data-canvas-next="publish"]') as HTMLButtonElement).click(); });
    await settle();
    expect(stageStates()[4]).toBe("publish:current");
    expect(q("[data-canvas-editor]")!.textContent).toBe("The Content Editor at http://localhost:8000 publishes to https://github.com/jporeilly/Pentaho-Courses.git (main).");
    expect(q("[data-canvas-diff]")!.textContent).toBe("2 files change upstream: 01-first-win/guide.md, 01-first-win/manifest.json");
    expect((q("[data-canvas-message]") as HTMLInputElement).value).toBe("Walkthrough video: Your First Win");
    expect(q("[data-canvas-not-placed]")).toBeNull();
    expect((q("[data-canvas-publish]") as HTMLButtonElement).disabled).toBe(false);
    await act(async () => { (q("[data-canvas-publish]") as HTMLButtonElement).click(); });
    await settle();
    expect(server.posts.at(-1)).toEqual({ path: "/api/courses/pdi-2hr-lab/publish", body: { message: "Walkthrough video: Your First Win" } });
    expect(q("[data-canvas-published]")!.textContent).toMatch(/^Published \d{4}-\d\d-\d\d \d\d:\d\d: committed 9f8e7d6 and pushed \(2 files\)$/);
    // The Workshop stage's status line and the Publish stage's own line remember it this session.
    await act(async () => { (q('[data-stage-open="workshop"]') as HTMLButtonElement).click(); });
    await settle();
    expect(q('[data-canvas-workshop="01-first-win"] [data-canvas-status]')!.textContent).toMatch(/· published \d{4}-\d\d-\d\d \d\d:\d\d$/);
    expect(q('[data-stage-line="publish"]')!.textContent).toMatch(/^Published /);
  });

  it("says when the editor is not running, with the server's one line and Publish greyed; and that the link was not placed when the setting is off", async () => {
    server.config = { address: "http://localhost:8010", reachable: false, url: null, ref: null, message: "The Content Editor is not running at http://localhost:8010: start it, then press Publish again." };
    server.defaults = { ...DEFAULTS, allowed: false };
    await toEdit();
    await act(async () => { (q("[data-builder-next]") as HTMLButtonElement).click(); });
    await settle();
    await act(async () => { (q('[data-canvas-next="publish"]') as HTMLButtonElement).click(); });
    await settle();
    expect(q("[data-canvas-editor]")!.textContent).toBe("The Content Editor is not running at http://localhost:8010: start it, then press Publish again.");
    expect((q("[data-canvas-publish]") as HTMLButtonElement).disabled).toBe(true);
    expect(q("[data-canvas-editor-again]")).not.toBeNull();
    expect(q("[data-canvas-diff]")).toBeNull();
    expect(q("[data-canvas-not-placed]")!.textContent).toContain("the Vimeo link was not placed in the guide");
    expect(vi.mocked(api.get).mock.calls.some((c) => c[0] === "/api/courses/pdi-2hr-lab/publish/diff")).toBe(false);
  });
});
