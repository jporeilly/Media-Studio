// @vitest-environment jsdom
/**
 * The project page for a walkthrough project (W3), the real `ProjectDetailPage` under jsdom against a scripted
 * server: the heading says where it came from with a link to the builder, the Details count steps, the Generate
 * card (with its Preview) and the Slides card are there as for a deck, and a job the builder's Make a video
 * started - handed over in the route's state - is followed at once, its progress on the Generate card. The live
 * check in a browser is the supervisor's.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProjectDetailPage from "./ProjectDetail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({
  viewer: { id: "u1", username: "u1", display_name: "U1", role: "editor" },
}));
vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: hoisted.viewer }) }));
vi.mock("../lib/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/jobs")>()),
  JOB_POLL_MS: 20,
  PROJECT_JOB_POLL_MS: 40,
}));

const WID = "abc123abc123";
const STUDIO = {
  settings: {
    tts_provider: "edge_tts", edge_tts_voice: "en-US-AriaNeural", kokoro_voice: "", kokoro_lang: "", whisper_model: "",
    ollama_model: "", output_folder: "", transition_pause: 0, music_volume: 0.25, slide_transition: "none",
    transition_duration: 0.5, watermark_text: "", watermark_position: "bottom-right", watermark_opacity: 0.5,
  },
  options: {
    tts_provider: { options: [{ value: "edge_tts", label: "Edge TTS" }] },
    kokoro_lang: { options: [] },
    whisper_model: { options: [] },
    transition_pause: { min: 0, max: 5, step: 0.5, unit: "seconds" },
    music_volume: { min: 0, max: 1, step: 0.05 },
    slide_transition: { options: [{ value: "none", label: "None" }] },
    transition_duration: { min: 0.1, max: 2, step: 0.1, unit: "seconds" },
    watermark_position: { options: [{ value: "bottom-right", label: "Bottom Right" }] },
    watermark_opacity: { min: 0.1, max: 1, step: 0.1 },
  },
};

interface ServerJob { id: string; kind: string; status: string; progress: number; message: string; user_id: string | null }
const server = {
  project: {} as Record<string, unknown>,
  jobs: {} as Record<string, ServerJob>,
  active: null as string | null,
  calls: [] as { url: string; method: string }[],
};

function reply(status: number, body: unknown) {
  return { status, ok: status >= 200 && status < 300, statusText: "", text: async () => JSON.stringify(body), blob: async () => new Blob() };
}

beforeEach(() => {
  server.jobs = {};
  server.active = null;
  server.calls = [];
  server.project = {
    id: "p1", name: "Your First Win", kind: "walkthrough", source_filename: "Your-First-Win.walkthrough.json", size_bytes: 10, slide_count: 3,
    created_at: "2026-10-22T14:02:00+00:00", walkthrough_id: WID, walkthrough_name: "Your First Win", images_source: "walkthrough",
  };
  vi.stubGlobal("fetch", async (input: unknown, init?: { method?: string }) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    server.calls.push({ url, method });
    if (url === "/api/projects/p1" && method === "GET") return reply(200, server.project);
    if (url === "/api/projects/p1/job") {
      const job = server.active ? server.jobs[server.active] : null;
      return reply(200, { active_job: job ? { id: job.id, kind: job.kind, status: job.status, user_id: job.user_id } : null });
    }
    if (url === "/api/projects/p1/slides") return reply(200, { slides: [], slides_ready: true, images_source: "walkthrough", images_rendered_at: null, qa_review: null });
    if (url === "/api/settings/studio") return reply(200, STUDIO);
    if (url.startsWith("/api/voices")) {
      return reply(200, { provider: "edge_tts", voices: [{ voice_id: "en-US-AriaNeural", name: "Aria", locale: "en-US", gender: null }], error: null, notice: null });
    }
    if (url === "/api/output-presets") {
      return reply(200, { presets: [{ id: "youtube_1080p", label: "YouTube 1080p", resolution: [1920, 1080], video_bitrate: "8M", description: "" }] });
    }
    const poll = url.match(/^\/api\/jobs\/(\w+)$/);
    if (poll) {
      const job = server.jobs[poll[1]];
      return job ? reply(200, job) : reply(404, { detail: "Job not found." });
    }
    return reply(404, { detail: `no route ${url}` });
  });
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

function mount(state?: Record<string, unknown>) {
  container = document.createElement("div");
  document.body.appendChild(container);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15_000 } } });
  root = createRoot(container);
  act(() => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [state ? { pathname: "/projects/p1", state } : "/projects/p1"] },
          createElement(Routes, null, createElement(Route, { path: "/projects/:id", element: createElement(ProjectDetailPage) })))),
    );
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function pass(ms: number) {
  await act(async () => { await sleep(ms); });
}
async function until(check: () => boolean, what: string, ms = 3000) {
  for (let waited = 0; waited < ms; waited += 15) {
    if (check()) return;
    await pass(15);
  }
  expect(check(), what).toBe(true);
}
const text = () => container?.textContent ?? "";
function findCard(title: string): HTMLElement | null {
  const heading = [...(container?.querySelectorAll("h3.os-card-title") ?? [])].find((h) => h.textContent?.trim() === title);
  return (heading?.closest("section") as HTMLElement | null) ?? null;
}
const buttons = (within: ParentNode, label: string) =>
  [...within.querySelectorAll("button")].filter((b) => b.textContent?.trim() === label) as HTMLButtonElement[];

describe("a walkthrough project's page", () => {
  it("says it is from the walkthrough, links to the builder, counts steps, and has the Generate and Slides cards", async () => {
    mount();
    await until(() => text().includes("From the walkthrough"), "the heading");
    const from = container!.querySelector("[data-from-walkthrough]") as HTMLElement;
    expect(from.textContent).toContain("From the walkthrough Your First Win");
    expect((from.querySelector("a") as HTMLAnchorElement).getAttribute("href")).toBe(`/walkthroughs/${WID}`);
    expect(text()).not.toContain("Your-First-Win.walkthrough.json");
    expect(text()).toContain("Steps");
    expect(text()).toContain("walkthrough");
    await until(() => findCard("Generate video") !== null, "the Generate card");
    const generate = findCard("Generate video")!;
    await until(() => buttons(generate, "Generate video").length === 1, "the Generate button");
    expect(buttons(generate, "Preview 15 s")).toHaveLength(1);
    expect(buttons(generate, "More options")).toHaveLength(1);
    expect(findCard("Slides"), "the Slides card").not.toBeNull();
    expect(findCard("Transcript")).toBeNull();
    expect(findCard("Re-voice")).toBeNull();
  });

  it("follows the job the builder's Make a video started, from the route's state, at once", async () => {
    server.jobs.J1 = { id: "J1", kind: "generate", status: "running", progress: 0.3, message: "Rendering the walkthrough…", user_id: "u1" };
    // The scope route names no job, so only the route's state can lead the page to J1 (the poll of the
    // project's job would otherwise adopt it a moment later and hide a missing follow).
    server.active = null;
    mount({ followJob: { id: "J1", kind: "generate" } });
    await until(() => text().includes("Rendering the walkthrough…"), "the job's progress on the page");
    expect(server.calls.some((c) => c.url === "/api/jobs/J1")).toBe(true);
    const generate = findCard("Generate video")!;
    expect(generate.textContent).toContain("Rendering the walkthrough…");
    expect(buttons(generate, "Cancel"), "its starter may cancel").toHaveLength(1);
    expect(buttons(generate, "Generate video")).toHaveLength(0);
  });
});
