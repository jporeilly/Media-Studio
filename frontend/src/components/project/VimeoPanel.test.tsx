// @vitest-environment jsdom
/**
 * The Generate card's Vimeo panel (V2, V3), mounted for real under jsdom with the server faked: Publish to Vimeo…
 * opens a dialog with the defaults (the title, the description, the privacy) and Publish posts the body and hands the
 * job to the page; without a token the dialog says so and Publish is greyed out; a published video shows its line
 * with Copy, Open and Publish again…, and Place in the course… only for a project from a course workshop; a running
 * publish shows its progress and Cancel for whoever may; the Place dialog previews the plan, takes the caption, the
 * Welcome Tour and the insertion, writes with the shas from the preview, and offers the lines to paste when the
 * setting is off or the guide has no place for them.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { PlacePlan, VimeoDefaults, VimeoRecord } from "../../lib/vimeo";
import { VimeoPanel } from "./VimeoPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn() } };
});

const PID = "p1";
const PRIVACIES = [
  { value: "unlisted", label: "Unlisted", note: "Needs Vimeo Plus or higher." },
  { value: "private", label: "Private", note: "Needs Vimeo Plus or higher." },
  { value: "anybody", label: "Anybody", note: "Free." },
];
const VIMEO: VimeoRecord = { id: "123", link: "https://vimeo.com/123/abc", hash: "abc", player_url: null, privacy: "unlisted", published_at: "2026-10-29T10:00:00Z", title: "Tour", previous: [] };
const DEFAULTS: VimeoDefaults = {
  title: "Your First Win", description: "# Your First Win\n\n1. Open it.", privacy: "unlisted", privacies: PRIVACIES, token_set: true, name_max: 128,
  description_max: 5000, vimeo: null, course: null, allowed: true,
};
const PLAN: PlacePlan = {
  course: "demo", course_title: "Demo course", slug: "01-first", workshop: "First", guide: "demo/01-first/guide.md", how: "placeholder", line: 12, heading: "",
  caption: "Your First Win", existing_caption: null, link: "https://vimeo.com/123/abc", lines: ["<figure>", "", "![Walkthrough](https://vimeo.com/123/abc)", "", '<figcaption class="pcm-video-caption">Watch: Your First Win</figcaption>', "", "</figure>"],
  diff: "--- a\n+++ b\n+![Walkthrough](https://vimeo.com/123/abc)", welcome: false, insert: false,
  files: [{ path: "01-first/guide.md", action: "write" }, { path: "01-first/manifest.json", action: "write" }], changed: true,
  guide_sha256: "a".repeat(64), manifest_sha256: "b".repeat(64), course_sha256: null, manifest: "demo/01-first/manifest.json", allowed: true,
};

const server = { defaults: DEFAULTS as VimeoDefaults, plan: PLAN as PlacePlan, posts: [] as { url: string; body: unknown }[] };
const handlers = {
  onJobStarted: vi.fn(),
  onCancelJob: vi.fn(),
  onDismissJobNotice: vi.fn(),
};
const clipboard = { writeText: vi.fn(async () => undefined) };

let root: Root | null = null;
let host: HTMLDivElement | null = null;

interface MountOver {
  vimeo?: VimeoRecord | null;
  job?: { id: string; kind: string; status: string; progress: number; message: string; user_id?: string | null; cancel_requested?: boolean };
  jobActive?: boolean;
  activeKind?: string;
  mayCancel?: boolean;
  jobError?: string | null;
  jobNotice?: string | null;
}

async function mount(over: MountOver = {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(VimeoPanel, {
          projectId: PID,
          project: { kind: "walkthrough", vimeo: over.vimeo ?? null },
          job: over.job,
          jobActive: over.jobActive ?? false,
          activeKind: over.activeKind,
          mayCancel: over.mayCancel ?? true,
          onCancelJob: handlers.onCancelJob,
          cancelPending: false,
          cancelError: null,
          jobError: over.jobError ?? null,
          jobNotice: over.jobNotice ?? null,
          onDismissJobNotice: handlers.onDismissJobNotice,
          onJobStarted: handlers.onJobStarted,
        }),
      ),
    );
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const input = (sel: string) => q(sel) as HTMLInputElement;
const click = (sel: string) => act(async () => { (q(sel) as HTMLButtonElement).click(); });
const buttons = (label: string) => [...document.querySelectorAll("button")].filter((b) => b.textContent?.trim() === label) as HTMLButtonElement[];
async function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function choose(el: HTMLSelectElement, value: string) {
  await act(async () => {
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

beforeEach(() => {
  server.defaults = { ...DEFAULTS };
  server.plan = { ...PLAN };
  server.posts = [];
  handlers.onJobStarted.mockReset();
  handlers.onCancelJob.mockReset();
  clipboard.writeText.mockClear();
  Object.defineProperty(navigator, "clipboard", { value: clipboard, configurable: true });
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.get).mockImplementation(async () => server.defaults as never);
  vi.mocked(api.post).mockImplementation(async (url: string, body?: unknown) => {
    server.posts.push({ url, body });
    if (url.endsWith("/vimeo/publish")) return { job_id: "J1" } as never;
    if (url.endsWith("/place/plan")) return { ...server.plan, caption: (body as { caption: string }).caption || server.plan.caption, insert: (body as { insert: boolean }).insert, welcome: (body as { welcome: boolean }).welcome } as never;
    if (url.endsWith("/vimeo/place")) return { course: "demo", slug: "01-first", guide: "demo/01-first/guide.md", link: "https://vimeo.com/123/abc", written: ["01-first/guide.md", "01-first/manifest.json"], welcome: false, message: "Placed the video in demo/01-first/guide.md. Review and publish it in the Content Editor." } as never;
    throw new Error(`unexpected post ${url}`);
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("Publish to Vimeo", () => {
  it("opens with the defaults and Publish posts the body and hands the job to the page", async () => {
    await mount();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/projects/p1/vimeo");
    expect(q("[data-vimeo-line]")).toBeNull();
    expect(q("[data-vimeo-publish]")!.textContent?.trim()).toBe("Publish to Vimeo…");
    expect(q("[data-vimeo-place]")).toBeNull();
    await click("[data-vimeo-publish]");
    expect(input("[data-vimeo-title]").value).toBe("Your First Win");
    expect((q("[data-vimeo-description]") as HTMLTextAreaElement).value).toBe("# Your First Win\n\n1. Open it.");
    expect((q("[data-vimeo-privacy]") as HTMLSelectElement).value).toBe("unlisted");
    expect([...(q("[data-vimeo-privacy]") as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["Unlisted", "Private", "Anybody"]);
    expect(q("[data-vimeo-no-token]")).toBeNull();
    await type(input("[data-vimeo-title]"), " Tour of the console ");
    await type(q("[data-vimeo-description]") as HTMLTextAreaElement, "What it shows");
    await choose(q("[data-vimeo-privacy]") as HTMLSelectElement, "anybody");
    await click("[data-vimeo-publish-go]");
    await settle();
    expect(server.posts).toEqual([{ url: "/api/projects/p1/vimeo/publish", body: { title: "Tour of the console", description: "What it shows", privacy: "anybody" } }]);
    expect(handlers.onJobStarted).toHaveBeenCalledWith("J1", "vimeo_publish");
    expect(q("[data-vimeo-title]"), "the dialog closed").toBeNull();
  });

  it("says when no token is set and greys Publish out", async () => {
    server.defaults = { ...DEFAULTS, token_set: false };
    await mount();
    await click("[data-vimeo-publish]");
    expect(q("[data-vimeo-no-token]")!.textContent).toContain("No Vimeo access token is set");
    expect((q("[data-vimeo-publish-go]") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a published video's line with Copy, Open and Publish again, and Place in the course only for a course walkthrough", async () => {
    await mount({ vimeo: { ...VIMEO, previous: ["100", "99"] } });
    expect(q("[data-vimeo-line]")!.textContent).toBe("On Vimeo: https://vimeo.com/123/abc (Unlisted)");
    expect((q("[data-vimeo-open]") as HTMLAnchorElement).getAttribute("href")).toBe("https://vimeo.com/123/abc");
    expect((q("[data-vimeo-open]") as HTMLAnchorElement).getAttribute("target")).toBe("_blank");
    expect(q("[data-vimeo-previous]")!.textContent).toContain("2 earlier videos stay on Vimeo");
    await click("[data-vimeo-copy]");
    expect(clipboard.writeText).toHaveBeenCalledWith("https://vimeo.com/123/abc");
    expect(q("[data-vimeo-publish]")!.textContent?.trim()).toBe("Publish again…");
    expect(q("[data-vimeo-place]"), "a deck, or a walkthrough not from a course: no place to put it").toBeNull();
    await click("[data-vimeo-publish]");
    expect(document.body.textContent).toContain("Publish to Vimeo again");
    expect(document.body.textContent).toContain("the one published before stays on Vimeo");
  });

  it("shows the running publish with its progress and Cancel for whoever may, and the error and notice of its own kind", async () => {
    await mount({ job: { id: "J1", kind: "vimeo_publish", status: "running", progress: 0.4, message: "Uploading… 20 of 50 MB", user_id: "u1" }, jobActive: true, activeKind: "vimeo_publish" });
    expect(q("[data-vimeo-running]")!.textContent).toContain("Uploading… 20 of 50 MB");
    expect(q("[data-vimeo-publish]")).toBeNull();
    expect(buttons("Cancel")).toHaveLength(1);
    await act(async () => { buttons("Cancel")[0].click(); });
    expect(handlers.onCancelJob).toHaveBeenCalledTimes(1);
    await act(async () => root?.unmount());
    await mount({ job: { id: "J1", kind: "vimeo_publish", status: "running", progress: 0.4, message: "Uploading…", user_id: "u2" }, jobActive: true, activeKind: "vimeo_publish", mayCancel: false });
    expect(buttons("Cancel")).toHaveLength(0);
    await act(async () => root?.unmount());
    await mount({ jobError: "Vimeo refused the access token (401): …", jobNotice: "Publish cancelled; the unfinished video was removed from Vimeo." });
    expect(document.body.textContent).toContain("Vimeo refused the access token (401)");
    expect(document.body.textContent).toContain("Publish cancelled");
    // Another kind's job greys the buttons but shows no progress here.
    await act(async () => root?.unmount());
    await mount({ job: { id: "J2", kind: "generate", status: "running", progress: 0.1, message: "Rendering…", user_id: "u1" }, jobActive: true, activeKind: "generate" });
    expect(q("[data-vimeo-running]")).toBeNull();
    expect((q("[data-vimeo-publish]") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Place in the course", () => {
  const COURSE = { course: "demo", slug: "01-first", title: "Demo course" };

  it("previews the plan, takes the caption, and writes with every sha256 from the preview", async () => {
    server.defaults = { ...DEFAULTS, vimeo: VIMEO, course: COURSE };
    await mount({ vimeo: VIMEO });
    expect(q("[data-vimeo-place]")!.textContent?.trim()).toBe("Place in the course…");
    await click("[data-vimeo-place]");
    await settle();
    expect(server.posts[0]).toEqual({ url: "/api/projects/p1/vimeo/place/plan", body: { caption: "", welcome: false, insert: false } });
    expect(q("[data-place-line]")!.textContent).toBe("The Walkthrough placeholder at line 12 of demo/01-first/guide.md becomes the video figure.");
    expect(q("[data-place-setting]")!.textContent).toContain("is on");
    expect(input("[data-place-caption]").value).toBe("Your First Win");
    expect(q("[data-place-diff]")!.textContent).toContain("+![Walkthrough](https://vimeo.com/123/abc)");
    expect(q("[data-place-files]")!.textContent).toContain("2 files to write");
    expect(q("[data-place-insert]"), "the guide has a placeholder: no insertion to offer").toBeNull();
    expect(q("[data-place-lines]"), "the setting is on and there is a place: nothing to paste by hand").toBeNull();
    await type(input("[data-place-caption]"), "the console tour");
    await settle();
    expect(server.posts.at(-1)).toEqual({ url: "/api/projects/p1/vimeo/place/plan", body: { caption: "the console tour", welcome: false, insert: false } });
    await act(async () => { input("[data-place-welcome]").click(); });
    await settle();
    expect(server.posts.at(-1)!.body).toEqual({ caption: "the console tour", welcome: true, insert: false });
    expect((q("[data-place-write]") as HTMLButtonElement).disabled).toBe(false);
    await click("[data-place-write]");
    await settle();
    const write = server.posts.find((p) => p.url === "/api/projects/p1/vimeo/place");
    expect(write!.body).toEqual({ caption: "the console tour", welcome: true, insert: false, guide_sha256: "a".repeat(64), manifest_sha256: "b".repeat(64), course_sha256: null });
    expect(q("[data-place-written]")!.textContent).toBe("Placed the video in demo/01-first/guide.md. Review and publish it in the Content Editor.");
  });

  it("with no placeholder offers the insertion and the lines to paste; with the setting off, the lines and no Place button", async () => {
    server.defaults = { ...DEFAULTS, vimeo: VIMEO, course: COURSE };
    server.plan = { ...PLAN, how: "none", line: 5, heading: "## 1. Open it", changed: false, diff: "", files: [{ path: "01-first/guide.md", action: "unchanged" }] };
    await mount({ vimeo: VIMEO });
    await click("[data-vimeo-place]");
    await settle();
    expect(q("[data-place-line]")!.textContent).toBe("demo/01-first/guide.md has no Walkthrough placeholder and no video figure of ours.");
    expect(q("[data-place-lines]")!.textContent).toContain("![Walkthrough](https://vimeo.com/123/abc)");
    expect((q("[data-place-write]") as HTMLButtonElement).disabled).toBe(true);
    expect(q("[data-place-insert]")!.closest("label")!.textContent).toContain("Insert the figure after the guide's first section heading (## 1. Open it)");
    server.plan = { ...server.plan, how: "insert", changed: true, diff: "+<figure>" };
    await act(async () => { input("[data-place-insert]").click(); });
    await settle();
    expect(server.posts.at(-1)!.body).toEqual({ caption: "Your First Win", welcome: false, insert: true });
    expect(q("[data-place-line]")!.textContent).toBe('The figure goes after "## 1. Open it" (line 5) of demo/01-first/guide.md.');
    expect((q("[data-place-write]") as HTMLButtonElement).disabled).toBe(false);
    await click("[data-place-copy]");
    expect(clipboard.writeText).toHaveBeenCalledWith(PLAN.lines.join("\n"));

    await act(async () => root?.unmount());
    server.plan = { ...PLAN, allowed: false };
    await mount({ vimeo: VIMEO });
    await click("[data-vimeo-place]");
    await settle();
    expect(q("[data-place-write]")).toBeNull();
    expect(q("[data-place-setting]")!.textContent).toContain("copy the lines into the guide by hand");
    expect(q("[data-place-lines]")!.textContent).toContain('<figcaption class="pcm-video-caption">Watch: Your First Win</figcaption>');
  });
});
