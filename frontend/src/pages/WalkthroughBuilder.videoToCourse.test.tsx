// @vitest-environment jsdom
/**
 * The builder's Video to course… (R1, R3, R4), mounted for real under jsdom with the server faked: the button beside
 * Make a video… opens the one dialog with everything prefilled from the defaults route - the name, the two ticks, the
 * privacy, the caption "Watch: <name>", the Welcome Tour off, and the three read-only lines (where the link goes,
 * the token, the setting); without a token the button is greyed out and the line says so; a walkthrough from no
 * course gets the no-course line and "Render and publish"; a walkthrough with a video on Vimeo gets the run-again line
 * and "… again" (and "Just place the existing video again" when the guide lost the link); the one button posts the
 * body and the builder then follows the job under its toolbar - the progress line with Cancel, then the Done line
 * with Open the video and Copy the link.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { VideoToCourseDefaults } from "../lib/videoToCourse";
import type { Step, Walkthrough } from "../lib/walkthrough";
import WalkthroughBuilderPage from "./WalkthroughBuilder";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});
vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", username: "olive", display_name: "Olive", role: "editor" } }) }));

const WID = "abc123abc123";
const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: { x: 1, y: 1, width: 20, height: 20 }, kind: "tooltip", text: "Open it", instruction: "",
  placement: "auto", chapter: "", note: "Under the hood.", source: null, click: "left", marker: "box", ...over,
});
const record = (over: Partial<Walkthrough> = {}): Walkthrough => ({
  id: WID, name: "Your First Win", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z",
  revision: 2, version: 1, steps: [step("aaaaaaaaaaaa")], source: { course: "pdi-2hr-lab", slug: "01-first-win", title: "PDI in 2 Hours", version: "1" }, ...over,
});
const DEFAULTS: VideoToCourseDefaults = {
  name: "Your First Win", privacy: "unlisted",
  privacies: [{ value: "unlisted", label: "Unlisted", note: "Needs Plus." }, { value: "private", label: "Private" }, { value: "anybody", label: "Anybody" }],
  token_set: true, allowed: true,
  course: { course: "pdi-2hr-lab", slug: "01-first-win", title: "PDI in 2 Hours" },
  place: { how: "placeholder", line: 23, heading: "", guide: "pdi-2hr-lab/01-first-win/guide.md", existing_caption: null, caption: "Your First Win", placed: false, error: null },
  project: null, job_kind: "video_to_course",
};
const PROJECT = {
  id: "p1", name: "Your First Win", created_at: "2026-10-31T13:00:00Z", rendered_at: "2026-10-31T14:02:00Z", has_video: true,
  vimeo: { id: "123", link: "https://vimeo.com/123/abc", privacy: "unlisted", published_at: null, previous: [] }, placed: true, guide: "pdi-2hr-lab/01-first-win/guide.md",
};

// Each start is a new job (ids are unique on the server; a page never follows an ended id again).
const server = { defaults: DEFAULTS as VideoToCourseDefaults, job: null as Record<string, unknown> | null, active: null as Record<string, unknown> | null, started: 0 };
const jobId = () => `J${server.started}`;

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
        createElement(MemoryRouter, { initialEntries: [`/walkthroughs/${WID}`] },
          createElement(Routes, null,
            createElement(Route, { path: "/walkthroughs/:id", element: createElement(WalkthroughBuilderPage) }),
            createElement(Route, { path: "/projects/:id", element: createElement("div", { "data-project-page": "" }, "project") }),
          ),
        ),
      ),
    );
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const input = (sel: string) => q(sel) as HTMLInputElement;

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
  server.defaults = { ...DEFAULTS };
  server.job = null;
  server.active = null;
  server.started = 0;
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === `/api/walkthroughs/${WID}`) return record() as never;
    if (path === `/api/walkthroughs/${WID}/video-to-course`) return server.defaults as never;
    if (path.startsWith("/api/jobs/")) return ({ ...(server.job ?? { kind: "video_to_course", status: "running", progress: 0.3, message: "Rendering the video… Narrating slide 1/1", user_id: "u1" }), id: jobId() }) as never;
    if (path.endsWith("/job")) return { active_job: server.active } as never;
    return {} as never;
  });
  vi.mocked(api.post).mockImplementation(async (path: string) => {
    if (path.endsWith("/video-to-course")) { server.started += 1; server.job = null; return { project_id: "p1", job_id: jobId(), created: true } as never; }
    if (path.endsWith("/cancel")) return { id: jobId(), kind: "video_to_course", status: "running", progress: 0.3, message: "x", cancel_requested: true, user_id: "u1" } as never;
    return {} as never;
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

async function openDialog() {
  await act(async () => { (q("[data-video-to-course]") as HTMLButtonElement).click(); });
  await settle();
}

describe("Video to course… (R1)", () => {
  it("sits beside Make a video… and opens the dialog with everything prefilled and the three lines", async () => {
    await mount();
    const toolbar = q("[data-builder-toolbar]")!;
    const labels = [...toolbar.querySelectorAll("button")].map((b) => (b.textContent || "").trim());
    expect(labels.indexOf("Video to course…")).toBe(labels.indexOf("Make a video…") + 1);
    expect((q("[data-video-to-course]") as HTMLButtonElement).disabled).toBe(false);
    await openDialog();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith(`/api/walkthroughs/${WID}/video-to-course`);
    expect(input("[data-vtc-name]").value).toBe("Your First Win");
    expect(input("[data-vtc-notes]").checked).toBe(true);
    expect(input("[data-vtc-zoom]").checked).toBe(true);
    expect((q("[data-vtc-privacy]") as HTMLSelectElement).value).toBe("unlisted");
    expect(input("[data-vtc-caption]").value).toBe("Watch: Your First Win");
    expect(input("[data-vtc-welcome]").checked).toBe(false);
    expect(q("[data-vtc-where]")!.textContent).toBe("pdi-2hr-lab / 01-first-win: the Walkthrough placeholder at line 23 becomes the video figure.");
    expect(q("[data-vtc-token]")!.textContent).toBe("A Vimeo token is set.");
    expect(q("[data-vtc-export]")!.textContent).toBe("Allow export into the Courses folder is on.");
    const go = q("[data-vtc-start]") as HTMLButtonElement;
    expect(go.textContent).toContain("Render, publish and place");
    expect(go.disabled).toBe(false);
    expect(q("[data-vtc-again]")).toBeNull();
    expect(q("[data-vtc-place-again]")).toBeNull();
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
  });

  it("greys the button out without a token and says so; says the setting is off", async () => {
    server.defaults = { ...DEFAULTS, token_set: false, allowed: false };
    await mount();
    await openDialog();
    expect(q("[data-vtc-token]")!.textContent).toBe("No Vimeo token: set one in Settings › Studio first.");
    expect((q("[data-vtc-start]") as HTMLButtonElement).disabled).toBe(true);
    expect(q("[data-vtc-export]")!.textContent).toContain("is off: the video is rendered and published, and the lines to paste are shown at the end");
  });

  it("drops the course part for a walkthrough from no course workshop", async () => {
    server.defaults = { ...DEFAULTS, course: null, place: null };
    await mount();
    await openDialog();
    expect(q("[data-vtc-no-course]")!.textContent).toBe("This walkthrough came from no course workshop: the video is rendered and published to Vimeo; copy its link where you need it.");
    expect(q("[data-vtc-caption]")).toBeNull();
    expect(q("[data-vtc-welcome]")).toBeNull();
    expect(q("[data-vtc-export]")).toBeNull();
    expect(q("[data-vtc-start]")!.textContent).toContain("Render and publish");
    expect(q("[data-vtc-start]")!.textContent).not.toContain("place");
  });

  it("R3: says what the walkthrough already has and offers the run again, and just place again when the guide lost the link", async () => {
    server.defaults = { ...DEFAULTS, project: PROJECT };
    await mount();
    await openDialog();
    expect(q("[data-vtc-again]")!.textContent).toMatch(/^Rendered 2026-10-31 \d\d:\d\d; on Vimeo as https:\/\/vimeo.com\/123\/abc; placed in pdi-2hr-lab\/01-first-win\/guide.md\.$/);
    expect(q("[data-vtc-start]")!.textContent).toContain("Render, publish and place again");
    expect(q("[data-vtc-place-again]")).toBeNull();
    await act(async () => root?.unmount());
    host?.remove();
    document.body.innerHTML = "";
    server.defaults = { ...DEFAULTS, project: { ...PROJECT, placed: false } };
    await mount();
    await openDialog();
    expect(q("[data-vtc-place-again]")!.textContent).toContain("Just place the existing video again");
    await act(async () => { (q("[data-vtc-place-again]") as HTMLButtonElement).click(); });
    await settle();
    const sent = vi.mocked(api.post).mock.calls.find((c) => (c[0] as string).endsWith("/video-to-course"));
    expect(sent?.[1]).toMatchObject({ mode: "place", name: "Your First Win" });
  });
});

describe("the one button and the builder's line (R2, R4)", () => {
  it("posts the body, closes, follows the job under the toolbar with Cancel, then shows the Done line with Open the video and Copy the link", async () => {
    await mount();
    await openDialog();
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input("[data-vtc-caption]"), "Watch: the first win");
      input("[data-vtc-caption]").dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { input("[data-vtc-welcome]").click(); });
    await act(async () => { (q("[data-vtc-start]") as HTMLButtonElement).click(); });
    await settle();
    const sent = vi.mocked(api.post).mock.calls.find((c) => (c[0] as string).endsWith("/video-to-course"))!;
    expect(sent[0]).toBe(`/api/walkthroughs/${WID}/video-to-course`);
    expect(sent[1]).toEqual({ name: "Your First Win", notes: true, zoom: true, privacy: "unlisted", caption: "Watch: the first win", welcome: true, mode: "full" });
    expect(q("[data-vtc-form]")).toBeNull();
    // The line under the toolbar: the job's message and Cancel for its starter.
    expect(q('[data-vtc-line="running"]')).not.toBeNull();
    expect(q('[data-vtc-line="running"]')!.textContent).toContain("Rendering the video… Narrating slide 1/1");
    const cancel = [...q('[data-vtc-line="running"]')!.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Cancel");
    expect(cancel).toBeDefined();
    expect((q("[data-video-to-course]") as HTMLButtonElement).disabled).toBe(true);
    // The job ends: the Done line with the two buttons; the defaults are read again.
    const calls = vi.mocked(api.get).mock.calls.length;
    server.job = {
      id: "J1", kind: "video_to_course", status: "done", progress: 1, message: "Done: https://vimeo.com/123/abc placed in pdi-2hr-lab/01-first-win/guide.md. Review and publish it in the Content Editor.",
      user_id: "u1", result: { project_id: "p1", link: "https://vimeo.com/123/abc", course: DEFAULTS.course, placed: true, guide: "pdi-2hr-lab/01-first-win/guide.md", written: ["01-first-win/guide.md"], lines: [], allowed: true },
    };
    await settle(1400);
    expect(q('[data-vtc-line="done"]')).not.toBeNull();
    expect(q("[data-vtc-done]")!.textContent).toBe("Done: https://vimeo.com/123/abc placed in pdi-2hr-lab/01-first-win/guide.md. Review and publish it in the Content Editor.");
    expect((q("[data-vtc-open]") as HTMLAnchorElement).getAttribute("href")).toBe("/projects/p1");
    expect(q("[data-vtc-copy]")!.textContent).toContain("Copy the link");
    expect(q("[data-vtc-copy-lines]")).toBeNull();
    expect(vi.mocked(api.get).mock.calls.slice(calls).some((c) => c[0] === `/api/walkthroughs/${WID}/video-to-course`)).toBe(true);
    expect((q("[data-video-to-course]") as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows the lines to copy when the setting was off, and a failed phase's line in red", async () => {
    await mount();
    await openDialog();
    await act(async () => { (q("[data-vtc-start]") as HTMLButtonElement).click(); });
    await settle();
    server.job = {
      id: "J1", kind: "video_to_course", status: "done", progress: 1, message: "Done: https://vimeo.com/123/abc is on Vimeo. Allow export into the Courses folder is off, so the link was not placed: copy the lines into the guide.",
      user_id: "u1", result: { project_id: "p1", link: "https://vimeo.com/123/abc", course: DEFAULTS.course, placed: false, guide: null, written: [], lines: ["<figure>", "", "</figure>"], allowed: false },
    };
    await settle(1400);
    expect(q("[data-vtc-copy-lines]")!.textContent).toContain("Copy the lines");
    await act(async () => { (q("[data-vtc-line] button[aria-label='Dismiss']") as HTMLButtonElement).click(); });
    expect(q("[data-vtc-line]")).toBeNull();

    await openDialog();
    await act(async () => { (q("[data-vtc-start]") as HTMLButtonElement).click(); });
    await settle();
    server.job = { id: "J1", kind: "video_to_course", status: "error", progress: 0.6, message: "Uploading to Vimeo failed: Vimeo refused the access token (401)", error: "Uploading to Vimeo failed: Vimeo refused the access token (401)", user_id: "u1" };
    await settle(1400);
    expect(q('[data-vtc-line="error"]')).not.toBeNull();
    expect(q("[data-vtc-error]")!.textContent).toBe("Uploading to Vimeo failed: Vimeo refused the access token (401)");
  });
});
