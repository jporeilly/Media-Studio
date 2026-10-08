// @vitest-environment jsdom
/**
 * The Projects page's half of screen capture (T3), mounted for real under jsdom with the recorder, the
 * signed-in user and the server faked:
 * - THE hide rule (`captureAvailable`, review MINOR 7 / plant P11): in a browser there is no Capture
 *   button, no Captures card and no unfinished recordings - the one line instead; in a shell whose
 *   capability refuses the page, none of it either; in the shell with its bridge, the button and the card;
 * - the recording the recorder is making is never offered (review M1): its id is left out whatever the
 *   server says of it, and so are one live elsewhere and one being saved;
 * - Save (review M3): all of an unbroken recording at once, with no duration; the part before a gap only
 *   after a confirm that says what is given up, and then with `accept_loss`; nothing when the first piece
 *   never arrived;
 * - Discard is a plain DELETE - never `from_recorder`, which would defeat the server's live refusal
 *   (re-review NIT 1);
 * - a shell whose capability REFUSES the page (the bridge is there, every command refused) offers nothing
 *   either: the shell is asked once (re-review NIT 5);
 * - the save's card offers Cancel to its starter, through the job's own cancel route (re-review B).
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SERVER_EDITION_LINE, type Unfinished } from "../lib/capture";
import ProjectsPage from "./Projects";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fake = vi.hoisted(() => ({
  capture: {
    state: "idle", elapsedMs: 0, message: null, savingJobId: null, clearSavingJob: () => {}, liveRecordingId: null as string | null,
    saveRunning: false, hotkeysLive: false, startRecording: async () => {}, takeStill: async () => null, pause: () => {},
    resume: () => {}, stop: () => {}, cancelCountdown: () => {}, dismiss: () => {}, lastStillAt: 0,
  },
  recordings: [] as unknown[],
  activeSave: null as null | Record<string, unknown>,
}));
vi.mock("../context/CaptureContext", () => ({ useCapture: () => fake.capture }));
vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", username: "ed", role: "editor" } }) }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), delete: vi.fn() } };
});

const rec = (over: Partial<Unfinished>): Unfinished => ({
  id: "old000000001", name: "Old recording", created_at: "2026-10-06T10:00:00Z", status: "stopped",
  chunks: 3, contiguous: 3, highest: 3, bytes: 3000, seconds: 15, ...over,
});

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: ["/projects"] }, createElement(ProjectsPage)),
      ),
    );
  });
  // Let the queries answer.
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}

function inShell(bridge: boolean | "refused" = true) {
  const w = window as unknown as Record<string, unknown>;
  w.isTauri = true;
  if (bridge === "refused") {
    // As measured on a refused page: the bridge is there, every command is refused.
    w.__TAURI__ = { core: { invoke: async () => { throw new Error("capture_monitors not allowed"); } }, event: { listen: async () => () => {} } };
  } else if (bridge) {
    w.__TAURI__ = { core: { invoke: async () => [] }, event: { listen: async () => () => {} } };
  }
}

async function until(cond: () => boolean, ms = 3000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error("timed out");
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }
}

const buttons = () => [...document.querySelectorAll("button")];
const button = (text: string) => buttons().find((b) => (b.textContent || "").trim() === text) ?? null;
const cardTitles = () => [...document.querySelectorAll(".os-card-title")].map((h) => h.textContent || "");
const offeredIds = () => [...document.querySelectorAll("[data-recording]")].map((e) => e.getAttribute("data-recording"));
const asked = (path: string) => vi.mocked(api.get).mock.calls.some(([p]) => p === path);

beforeEach(() => {
  fake.capture.liveRecordingId = null;
  fake.recordings = [];
  fake.activeSave = null;
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.delete).mockImplementation(async () => undefined as never);
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === "/api/projects") return { projects: [] } as never;
    if (path === "/api/recordings") return { recordings: fake.recordings } as never;
    if (path === "/api/captures") return { captures: [] } as never;
    if (path === "/api/recordings/job") return { active_job: fake.activeSave } as never;
    if (fake.activeSave && path === `/api/jobs/${fake.activeSave.id}`) return fake.activeSave as never;
    return {} as never;
  });
  vi.mocked(api.post).mockImplementation(async () => ({ job_id: "job-9" }) as never);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
  const w = window as unknown as Record<string, unknown>;
  delete w.isTauri;
  delete w.__TAURI__;
});

describe("the Projects page's capture, by THE hide rule", () => {
  it("offers nothing of it in a browser: no button, no Captures card, no recordings - the one line", async () => {
    fake.recordings = [rec({})];
    await mount();
    expect(button("Capture")).toBeNull();
    expect(cardTitles()).not.toContain("Captures");
    expect(cardTitles()).not.toContain("Recordings not saved yet");
    expect(asked("/api/recordings")).toBe(false);
    expect(asked("/api/captures")).toBe(false);
    expect(document.body.textContent).toContain(SERVER_EDITION_LINE);
  });

  it("offers nothing of it in a shell whose capability refuses the page, and says nothing either", async () => {
    inShell(false);
    fake.recordings = [rec({})];
    await mount();
    expect(button("Capture")).toBeNull();
    expect(cardTitles()).not.toContain("Captures");
    expect(asked("/api/recordings")).toBe(false);
    expect(document.body.textContent).not.toContain(SERVER_EDITION_LINE);
  });

  it("offers nothing of it in a shell whose capability refuses the page, though the bridge is there", async () => {
    inShell("refused");
    fake.recordings = [rec({})];
    await mount();
    expect(button("Capture")).toBeNull();
    expect(cardTitles()).not.toContain("Captures");
    expect(asked("/api/recordings")).toBe(false);
  });

  it("offers the button, the Captures card and the unfinished recordings in the shell", async () => {
    inShell();
    fake.recordings = [rec({})];
    await mount();
    expect(button("Capture")).not.toBeNull();
    expect(cardTitles()).toContain("Captures");
    expect(cardTitles()).toContain("Recordings not saved yet");
    expect(document.body.textContent).not.toContain(SERVER_EDITION_LINE);
  });
});

describe("the unfinished recordings", () => {
  it("never offers the recording the recorder is making, one live elsewhere, or one being saved", async () => {
    inShell();
    fake.capture.liveRecordingId = "live00000001";
    fake.recordings = [
      rec({ id: "live00000001", name: "Live" }), // what the server lists for the recorder's own (stale, or lapsed)
      rec({ id: "old000000001" }),
      rec({ id: "elsewhere001", status: "recording" }),
      rec({ id: "saving000001", status: "finishing" }),
    ];
    await mount();
    expect(offeredIds()).toEqual(["old000000001"]);
  });

  it("saves an unbroken recording at once, naming its chunks and no duration", async () => {
    inShell();
    fake.recordings = [rec({})];
    await mount();
    await act(async () => { button("Save as a project")!.click(); });
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/recordings/old000000001/finish", { chunks: 3, accept_loss: false });
  });

  it("saves the part before a gap only after a confirm that says what is given up", async () => {
    inShell();
    fake.recordings = [rec({ id: "gap000000001", chunks: 3, contiguous: 2, highest: 4, seconds: 10 })];
    await mount();
    expect(button("Save as a project")).toBeNull();
    await act(async () => { button("Save the part before the gap")!.click(); });
    expect(document.body.textContent).toContain("Piece 3 of this recording never reached the disk");
    expect(document.body.textContent).toContain("given up and deleted");
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
    // Cancel: nothing is sent.
    await act(async () => { button("Cancel")!.click(); });
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
    // Confirmed: the part before the gap, with the loss accepted.
    await act(async () => { button("Save the part before the gap")!.click(); });
    await act(async () => { button("Save that part")!.click(); });
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/recordings/gap000000001/finish", { chunks: 2, accept_loss: true });
  });

  it("offers no save when the first piece never arrived", async () => {
    inShell();
    fake.recordings = [rec({ id: "none00000001", chunks: 2, contiguous: 0, highest: 3 })];
    await mount();
    expect(button("Save as a project")!.disabled).toBe(true);
  });
});

describe("the re-review round", () => {
  it("discards with a plain DELETE, never claiming to be the recorder", async () => {
    inShell();
    fake.recordings = [rec({})];
    await mount();
    await act(async () => { button("Discard")!.click(); });
    const dialog = document.querySelector("[role=dialog]")!;
    expect(dialog.textContent).toContain("Its chunks are deleted");
    const confirmDiscard = [...dialog.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === "Discard")!;
    await act(async () => { confirmDiscard.click(); });
    expect(vi.mocked(api.delete)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.delete)).toHaveBeenCalledWith("/api/recordings/old000000001");
  });

  it("offers Cancel on the save's card, through the job's own cancel route", async () => {
    inShell();
    fake.activeSave = { id: "job-9", kind: "recording", status: "running", progress: 0.3, message: "Converting to MP4 (30%)…", user_id: "u1" };
    await mount();
    await until(() => cardTitles().includes("Saving the recording") && !!button("Cancel"));
    await act(async () => { button("Cancel")!.click(); });
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/jobs/job-9/cancel", {});
  });

  it("offers no Cancel on somebody else's save", async () => {
    inShell();
    fake.activeSave = { id: "job-9", kind: "recording", status: "running", progress: 0.3, message: "Converting", user_id: "u2" };
    await mount();
    await until(() => cardTitles().includes("Saving the recording"));
    expect(button("Cancel")).toBeNull();
  });
});
