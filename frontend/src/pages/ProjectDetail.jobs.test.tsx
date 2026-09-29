// @vitest-environment jsdom
/**
 * The project page's job wiring as the user meets it (E7 fix round, the review's m2): the real
 * `ProjectDetailPage` rendered under jsdom against a scripted server (`fetch`), with the intervals shortened
 * (lib/jobs mocked: JOB_POLL_MS 20 ms, PROJECT_JOB_POLL_MS 40 ms) and the Timeline replaced by a stub that
 * counts its mounts (its own behaviour is the timeline's tests'). Pinned here, on the cards themselves: a job
 * someone else started shows on the card that runs it, read-only, while the others wait; the lost job's line
 * and the buttons freed; the cancelled re-voice's line on the Re-voice card; **Transcribe again** asking first
 * and posting only on confirm; and the Transcript card starting afresh with each new transcript. The live
 * check in a browser is still the owner's.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProjectDetailPage from "./ProjectDetail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({
  viewer: { id: "u1", username: "u1", display_name: "U1", role: "editor" } as { id: string; username: string; display_name: string; role: string },
  timelineMounts: { count: 0 },
}));

vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: hoisted.viewer }) }));
vi.mock("../lib/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/jobs")>()),
  JOB_POLL_MS: 20,
  PROJECT_JOB_POLL_MS: 40,
}));
vi.mock("../components/project/NarrationTimeline", async () => {
  const { createElement: h, useEffect } = await import("react");
  return {
    NarrationTimeline: () => {
      useEffect(() => {
        hoisted.timelineMounts.count += 1;
      }, []);
      return h("div", { "data-stub": "timeline" });
    },
  };
});

type Phase = "up" | "restarted";
interface ServerJob {
  id: string;
  kind: string;
  status: string;
  progress: number;
  message: string;
  user_id: string | null;
  result?: unknown;
  cancel_requested?: boolean;
}

const TRANSCRIPT = [
  { start: 0, end: 2, text: "First sentence.", offset: -0.4 },
  { start: 2, end: 4, text: "Second sentence." },
];

const server = {
  phase: "up" as Phase,
  project: {} as Record<string, unknown>,
  jobs: {} as Record<string, ServerJob>,
  active: null as string | null,
  calls: [] as { url: string; method: string }[],
  /** What a cancel does to the job: the re-voice's closing line and result. */
  onCancel: null as ((job: ServerJob) => void) | null,
};

function reply(status: number, body: unknown) {
  return { status, ok: status >= 200 && status < 300, statusText: "", text: async () => JSON.stringify(body), blob: async () => new Blob() };
}

beforeEach(() => {
  server.phase = "up";
  server.jobs = {};
  server.active = null;
  server.calls = [];
  server.onCancel = null;
  server.project = {
    id: "p1", name: "Clip", kind: "video", source_filename: "clip.mp4", size_bytes: 10, slide_count: null,
    created_at: "2026-09-29T10:00:00+00:00", transcript: TRANSCRIPT, transcribed_at: "2026-09-29T10:00:00+00:00",
  };
  hoisted.viewer = { id: "u1", username: "u1", display_name: "U1", role: "editor" };
  hoisted.timelineMounts.count = 0;
  vi.stubGlobal("fetch", async (input: unknown, init?: { method?: string }) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    server.calls.push({ url, method });
    const lost = server.phase === "restarted";
    if (url === "/api/projects/p1" && method === "GET") return reply(200, server.project);
    if (url === "/api/projects/p1/job") {
      const job = !lost && server.active ? server.jobs[server.active] : null;
      return reply(200, { active_job: job ? { id: job.id, kind: job.kind, status: job.status, user_id: job.user_id } : null });
    }
    if (url === "/api/projects/p1/transcribe" && method === "POST") {
      server.jobs.K = { id: "K", kind: "transcribe", status: "running", progress: 0.2, message: "Transcribing…", user_id: hoisted.viewer.id };
      server.active = "K";
      return reply(200, { job_id: "K" });
    }
    const cancel = url.match(/^\/api\/jobs\/(\w+)\/cancel$/);
    if (cancel && method === "POST") {
      const job = server.jobs[cancel[1]];
      job.cancel_requested = true;
      server.onCancel?.(job);
      return reply(200, job);
    }
    const poll = url.match(/^\/api\/jobs\/(\w+)$/);
    if (poll) {
      const job = lost ? undefined : server.jobs[poll[1]];
      return job ? reply(200, job) : reply(404, { detail: "Job not found." });
    }
    return reply(404, { detail: `no route ${url}` });
  });
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;
let qc: QueryClient | null = null;

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
  qc = null;
  vi.unstubAllGlobals();
});

function mount() {
  container = document.createElement("div");
  document.body.appendChild(container);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15_000 } } });
  root = createRoot(container);
  act(() => {
    root!.render(
      createElement(QueryClientProvider, { client: qc! },
        createElement(MemoryRouter, { initialEntries: ["/projects/p1"] },
          createElement(Routes, null, createElement(Route, { path: "/projects/:id", element: createElement(ProjectDetailPage) })))),
    );
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function pass(ms: number) {
  await act(async () => {
    await sleep(ms);
  });
}
async function until(check: () => boolean, what: string, ms = 3000) {
  for (let waited = 0; waited < ms; waited += 15) {
    if (check()) return;
    await pass(15);
  }
  expect(check(), what).toBe(true);
}

const text = () => container?.textContent ?? "";
/** The card whose title is `title`, or null while it is not on the page. */
function findCard(title: string): HTMLElement | null {
  const heading = [...(container?.querySelectorAll("h3.os-card-title") ?? [])].find((h) => h.textContent?.trim() === title);
  return (heading?.closest("section") as HTMLElement | null) ?? null;
}
/** The card whose title is `title`. */
function card(title: string): HTMLElement {
  const found = findCard(title);
  expect(found, `no "${title}" card`).toBeTruthy();
  return found!;
}
const buttons = (within: ParentNode, label: string) =>
  [...within.querySelectorAll("button")].filter((b) => b.textContent?.trim() === label) as HTMLButtonElement[];
const dialog = () => container!.querySelector('[role="dialog"]') as HTMLElement | null;
const posts = (url: string) => server.calls.filter((c) => c.url === url && c.method === "POST").length;

function runningJob(id: string, kind: string, user_id: string, message = "Working…"): ServerJob {
  const job = { id, kind, status: "running", progress: 0.4, message, user_id };
  server.jobs[id] = job;
  server.active = id;
  return job;
}

describe("a job someone else started (a reload, another tab, another user)", () => {
  it("shows on the card that runs it, read-only, while the other cards wait", async () => {
    runningJob("R", "revoice", "u2", "Re-voicing…");
    mount();
    await until(() => text().includes("Re-voicing…"), "the adopted re-voice's progress shows");
    const revoice = card("Re-voice");
    expect(revoice.textContent).toContain("Re-voicing…");
    expect(buttons(revoice, "Cancel"), "no Cancel for a viewer who did not start it").toHaveLength(0);
    const again = buttons(card("Transcript"), "Transcribe again")[0];
    expect(again.disabled, "the Transcript card waits").toBe(true);
    expect(again.title).toContain("A job is running for this project (revoice)");
  });

  it("offers Cancel to the user who started it", async () => {
    hoisted.viewer = { id: "u2", username: "u2", display_name: "U2", role: "editor" };
    runningJob("R", "revoice", "u2", "Re-voicing…");
    mount();
    await until(() => text().includes("Re-voicing…"), "adopted");
    expect(buttons(card("Re-voice"), "Cancel")).toHaveLength(1);
  });
});

describe("a job the server lost in a restart", () => {
  it("leaves the restart line on its card and frees the buttons", async () => {
    server.project = { ...server.project, transcript: [], transcribed_at: undefined };
    runningJob("J", "transcribe", "u2", "Transcribing…");
    mount();
    await until(() => text().includes("Transcribing…"), "adopted");
    server.phase = "restarted";
    await until(() => text().includes("The job stopped when the server restarted. Start it again."), "the restart line");
    const transcript = card("Transcript");
    expect(transcript.textContent).toContain("The job stopped when the server restarted. Start it again.");
    expect(buttons(transcript, "Transcribe audio")[0].disabled, "the button is free again").toBe(false);
  });
});

describe("a cancelled re-voice", () => {
  it("keeps its closing line on the Re-voice card", async () => {
    const line = "Re-voice cancelled before the new video was written; the project is as it was.";
    server.onCancel = (job) => Object.assign(job, { status: "done", progress: 1, message: line, result: { cancelled: true, stage: "synthesis" } });
    runningJob("R", "revoice", "u1", "Re-voicing…");
    mount();
    await until(() => !!findCard("Re-voice") && buttons(findCard("Re-voice")!, "Cancel").length === 1, "the starter's Cancel");
    act(() => buttons(card("Re-voice"), "Cancel")[0].click());
    server.active = null;
    await until(() => card("Re-voice").textContent!.includes(line), "the closing line on the Re-voice card");
    expect(card("Transcript").textContent).not.toContain(line);
    expect(posts("/api/jobs/R/cancel")).toBe(1);
  });
});

describe("Transcribe again", () => {
  it("asks first, in the app's own dialog with the count, and posts only on confirm", async () => {
    mount();
    await until(() => !!findCard("Transcript") && buttons(findCard("Transcript")!, "Transcribe again").length === 1, "the button");
    act(() => buttons(card("Transcript"), "Transcribe again")[0].click());
    await until(() => dialog() !== null, "the dialog opens");
    expect(dialog()!.textContent).toContain("1 sentence has some");
    expect(dialog()!.textContent).toContain("cuts, markers and music clips");
    act(() => buttons(dialog()!, "Cancel")[0].click());
    await pass(60);
    expect(dialog()).toBeNull();
    expect(posts("/api/projects/p1/transcribe"), "nothing is sent without a confirm").toBe(0);

    act(() => buttons(card("Transcript"), "Transcribe again")[0].click());
    await until(() => dialog() !== null, "the dialog opens again");
    act(() => buttons(dialog()!, "Transcribe again")[0].click());
    await until(() => posts("/api/projects/p1/transcribe") === 1, "confirmed: one request");
    await until(() => text().includes("Transcribing…"), "the transcription's progress on the card");
  });

  it("the card starts afresh with a new transcript, and only then", async () => {
    mount();
    await until(() => hoisted.timelineMounts.count === 1, "the Timeline is mounted once");
    await act(async () => {
      await qc!.invalidateQueries({ queryKey: ["project", "p1"] });
    });
    await pass(60);
    expect(hoisted.timelineMounts.count, "the same transcript: nothing remounts").toBe(1);
    server.project = { ...server.project, transcribed_at: "2026-09-29T11:00:00+00:00", transcript: [{ start: 0, end: 3, text: "New." }] };
    await act(async () => {
      await qc!.invalidateQueries({ queryKey: ["project", "p1"] });
    });
    await until(() => hoisted.timelineMounts.count === 2, "a new transcript: the card and its Timeline start afresh");
  });
});
