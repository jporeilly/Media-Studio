// @vitest-environment jsdom
/**
 * The project page's job wiring as the user meets it (E7 fix round, the review's m2): the real
 * `ProjectDetailPage` rendered under jsdom against a scripted server (`fetch`), with the intervals shortened
 * (lib/jobs mocked: JOB_POLL_MS 20 ms, PROJECT_JOB_POLL_MS 40 ms) and the Timeline replaced by a stub that
 * counts its mounts (its own behaviour is the timeline's tests'). Pinned here, on the cards themselves: a job
 * someone else started shows on the card that runs it, read-only, while the others wait; the lost job's line
 * and the buttons freed; the cancelled re-voice's line on the Re-voice card; **Transcribe again** asking first
 * and posting only on confirm; the Transcript card starting afresh with each new transcript; and, on a deck,
 * the Generate card's Cancel (T2): for the starter and an administrator, not another editor, "Cancelling…"
 * once asked, the closing line kept with the previous render still in the players and the buttons free, and
 * a refused cancel's message under its own job. The live check in a browser is still the owner's.
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
  /** What a cancel does to the job: the re-voice's (or the render's) closing line and result. */
  onCancel: null as ((job: ServerJob) => void) | null,
  /** What the cancel route answers: 200 with the job, or the 403 of the cancel rule. */
  cancelStatus: 200,
};

const CANCEL_FORBIDDEN = "Only the user who started this job, or an admin, can cancel it.";

/** The studio settings and the lists a deck's Generate card needs before its buttons are usable. */
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

function reply(status: number, body: unknown) {
  return { status, ok: status >= 200 && status < 300, statusText: "", text: async () => JSON.stringify(body), blob: async () => new Blob() };
}

beforeEach(() => {
  server.phase = "up";
  server.jobs = {};
  server.active = null;
  server.calls = [];
  server.onCancel = null;
  server.cancelStatus = 200;
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
      if (server.cancelStatus !== 200) return reply(server.cancelStatus, { detail: CANCEL_FORBIDDEN });
      const job = server.jobs[cancel[1]];
      job.cancel_requested = true;
      server.onCancel?.(job);
      return reply(200, job);
    }
    // A deck's cards: the slides (none, so the editor stays quiet), the studio settings, the voices and the
    // presets the Generate card's buttons wait for.
    if (url === "/api/projects/p1/slides") return reply(200, { slides: [], slides_ready: false, images_source: null, images_rendered_at: null, qa_review: null });
    if (url === "/api/settings/studio") return reply(200, STUDIO);
    if (url.startsWith("/api/voices")) {
      return reply(200, { provider: "edge_tts", voices: [{ voice_id: "en-US-AriaNeural", name: "Aria", locale: "en-US", gender: null }], error: null, notice: null });
    }
    if (url === "/api/output-presets") {
      return reply(200, { presets: [{ id: "youtube_1080p", label: "YouTube 1080p", resolution: [1920, 1080], video_bitrate: "8M", description: "" }] });
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

describe("the Generate card's Cancel (a deck)", () => {
  const LINE = "Render cancelled before the video was written; the project is as it was.";

  function deck() {
    server.project = {
      id: "p1", name: "Deck", kind: "deck", source_filename: "deck.pptx", size_bytes: 10, slide_count: 3,
      created_at: "2026-09-29T10:00:00+00:00", output_video: "deck.mp4", outputs: { srt: "deck.srt" },
      rendered_at: "2026-09-29T11:00:00+00:00",
    };
  }
  const generateCard = () => findCard("Generate video");

  it("shows Cancel to the starter, reads Cancelling… once asked, and keeps the closing line with the previous render in the player and the buttons free", async () => {
    deck();
    const job = runningJob("G", "generate", "u1", "Encoding 40%");
    // The server sees the flag at its next check: the job stays "Cancelling…" for a moment, then ends cancelled.
    server.onCancel = () => {
      setTimeout(() => {
        Object.assign(job, { status: "done", progress: 1, message: LINE, result: { cancelled: true, stage: "encode" } });
        server.active = null;
      }, 120);
    };
    mount();
    await until(() => !!generateCard() && buttons(generateCard()!, "Cancel").length === 1, "the starter's Cancel on the Generate card");
    const cancel = buttons(generateCard()!, "Cancel")[0];
    expect(cancel.title).toContain("Stops the render or the preview");
    expect(cancel.title).toContain("the previous video (or preview) stays as it was");
    expect(generateCard()!.textContent).toContain("Encoding 40%");
    act(() => cancel.click());
    await until(() => buttons(generateCard()!, "Cancelling…").length === 1, "the button reads Cancelling… once asked");
    expect(buttons(generateCard()!, "Cancelling…")[0].disabled).toBe(true);
    expect(posts("/api/jobs/G/cancel")).toBe(1);
    await until(() => (generateCard()?.textContent ?? "").includes(LINE), "the closing line as the card's notice");
    const card = generateCard()!;
    expect(card.querySelector('video[src^="/api/projects/p1/video"]'), "the player still shows the previous render").toBeTruthy();
    expect(card.textContent).not.toContain("could not be rendered");
    await until(() => buttons(generateCard()!, "Regenerate").length === 1 && !buttons(generateCard()!, "Regenerate")[0].disabled, "Generate is enabled again");
    expect(buttons(card, "Preview 15 s")[0].disabled, "and so is Preview").toBe(false);
    expect(findCard("Slides")!.textContent).not.toContain(LINE);
  });

  it("offers Cancel to an administrator as well", async () => {
    deck();
    hoisted.viewer = { id: "a1", username: "a1", display_name: "A1", role: "admin" };
    runningJob("G", "generate", "u1", "Encoding 40%");
    mount();
    await until(() => (generateCard()?.textContent ?? "").includes("Encoding 40%"), "the render's progress");
    expect(buttons(generateCard()!, "Cancel")).toHaveLength(1);
  });

  it("shows another editor the progress read-only, with no Cancel", async () => {
    deck();
    hoisted.viewer = { id: "u2", username: "u2", display_name: "U2", role: "editor" };
    runningJob("G", "generate", "u1", "Encoding 40%");
    mount();
    await until(() => (generateCard()?.textContent ?? "").includes("Encoding 40%"), "the render's progress");
    expect(buttons(generateCard()!, "Cancel")).toHaveLength(0);
    expect(buttons(generateCard()!, "Regenerate"), "the buttons wait while the job runs").toHaveLength(0);
  });

  it("shows a refused cancel's message under its own job, on the Generate card alone", async () => {
    deck();
    runningJob("G", "generate", "u1", "Encoding 40%");
    server.cancelStatus = 403;
    mount();
    await until(() => !!generateCard() && buttons(generateCard()!, "Cancel").length === 1, "the Cancel");
    act(() => buttons(generateCard()!, "Cancel")[0].click());
    await until(() => (generateCard()?.textContent ?? "").includes(CANCEL_FORBIDDEN), "the refusal under the render");
    expect(findCard("Slides")!.textContent).not.toContain(CANCEL_FORBIDDEN);
    expect(buttons(generateCard()!, "Cancel"), "still a Cancel, not Cancelling…: nothing was asked of the job").toHaveLength(1);
  });
});

describe("the Slides card's Cancel for an AI job (through the page's one cancel)", () => {
  function deck() {
    server.project = {
      id: "p1", name: "Deck", kind: "deck", source_filename: "deck.pptx", size_bytes: 10, slide_count: 3,
      created_at: "2026-09-29T10:00:00+00:00",
    };
  }

  it("posts the cancel once for the starter and reads Cancelling…", async () => {
    deck();
    runningJob("A", "ai-enhance", "u1", "Enhancing 2/3");
    mount();
    await until(() => !!findCard("Slides") && buttons(findCard("Slides")!, "Cancel").length === 1, "the starter's Cancel on the Slides card");
    expect(findCard("Slides")!.textContent).toContain("Enhancing 2/3");
    expect(buttons(findCard("Generate video")!, "Cancel"), "nothing on the Generate card").toHaveLength(0);
    act(() => buttons(findCard("Slides")!, "Cancel")[0].click());
    await until(() => buttons(findCard("Slides")!, "Cancelling…").length === 1, "the button reads Cancelling… once asked");
    expect(buttons(findCard("Slides")!, "Cancelling…")[0].disabled).toBe(true);
    expect(posts("/api/jobs/A/cancel")).toBe(1);
  });

  it("shows a refused cancel's message under the Slides card and nowhere else", async () => {
    deck();
    runningJob("A", "ai-enhance", "u1", "Enhancing 2/3");
    server.cancelStatus = 403;
    mount();
    await until(() => !!findCard("Slides") && buttons(findCard("Slides")!, "Cancel").length === 1, "the Cancel");
    act(() => buttons(findCard("Slides")!, "Cancel")[0].click());
    await until(() => (findCard("Slides")?.textContent ?? "").includes(CANCEL_FORBIDDEN), "the refusal under the AI job");
    expect(findCard("Generate video")!.textContent).not.toContain(CANCEL_FORBIDDEN);
    expect(posts("/api/jobs/A/cancel")).toBe(1);
    expect(buttons(findCard("Slides")!, "Cancel"), "still a Cancel: nothing was asked of the job").toHaveLength(1);
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
