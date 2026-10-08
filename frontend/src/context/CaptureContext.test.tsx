// @vitest-environment jsdom
/**
 * The recorder (T3) as behaviour: the real `CaptureProvider` mounted under jsdom with fakes for what the
 * page talks to - the shell (`window.__TAURI__`: monitors, windows, the overlay's region event, the bar,
 * the hotkeys), the media APIs (getDisplayMedia, getUserMedia, AudioContext, MediaRecorder) and the
 * server (`api`). Pinned:
 * - the state machine walked end to end: idle -> picking -> countdown -> recording -> paused ->
 *   recording -> saving -> done, the chunks sent as they come, the one the server never acknowledged sent
 *   again at Finish, the count passed to Finish, and the page moved to Projects;
 * - the hotkeys from the shell (`capture:hotkey`) and in the page (Shift+F9 / Shift+F10), and neither
 *   doing anything outside a recording;
 * - the unload guard: up while a recording is in flight, down before and after;
 * - a window share named after the window its handle names;
 * - the refusals that leave nothing behind: a cancelled share dialog, a refused microphone, a region
 *   drawn on one monitor and another screen shared, a region with a WINDOW shared (review M5), an overlay
 *   closed without an answer (Escape, or Alt+F4: the shell sends null), the countdown cancelled from the
 *   bar or by Shift+F10;
 * - LIVE (review M1): the recording's id is exposed while the recorder makes it, the heartbeat runs, and
 *   Finish says it comes from the recorder;
 * - the save: the shell's close guard stays up while the save job runs and comes down when it ends, and a
 *   close refused then says so (review M2);
 * - failures keep what arrived (review MINOR 2, M4): a recorder error after a chunk landed, a chunk refused
 *   for the disk (507) - the recording is let go, never deleted; one that never landed a chunk is thrown away;
 * - a snapped window crossing its monitor's edge is cut to the monitor (review MINOR 1);
 * - the leftovers of a page that went away mid-recording are released on mount (review MINOR 5);
 * - the save's close guard never outlives the backend (re-review B): it comes down after MAX_POLL_FAILURES
 *   unanswered polls, with the reason, and when the provider unmounts; only the caller's OWN running save
 *   is adopted on mount (re-review NIT 6);
 * - the heartbeat runs while PAUSED too (re-review NIT 2);
 * - the two-hour stop.
 */
import { act, createElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { CaptureProvider, useCapture, type CaptureApi } from "./CaptureContext";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({ countdown: 1, max: 7_200_000, heartbeat: 60, poll: 50 }));
vi.mock("../lib/capture", async (importOriginal) => {
  const real = await importOriginal<typeof import("../lib/capture")>();
  return {
    ...real,
    get COUNTDOWN_SECONDS() { return hoisted.countdown; },
    get MAX_RECORDING_MS() { return hoisted.max; },
    get HEARTBEAT_MS() { return hoisted.heartbeat; },
  };
});
vi.mock("../lib/jobs", async (importOriginal) => {
  const real = await importOriginal<typeof import("../lib/jobs")>();
  return { ...real, get JOB_POLL_MS() { return hoisted.poll; } };
});
vi.mock("./AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", username: "ed", role: "editor" } }) }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), putRaw: vi.fn(), delete: vi.fn() } };
});

// ---- fakes ----------------------------------------------------------------------------------------------

class FakeTrack {
  stopped = false;
  listeners: Record<string, (() => void)[]> = {};
  constructor(public kind: "audio" | "video", private settings: Record<string, unknown> = {}, private caps?: Record<string, unknown>) {}
  getSettings() { return this.settings; }
  getCapabilities() { return this.caps ?? {}; }
  stop() { this.stopped = true; }
  addEventListener(name: string, fn: () => void) { (this.listeners[name] ||= []).push(fn); }
}
class FakeStream {
  constructor(public tracks: FakeTrack[] = []) {}
  getTracks() { return this.tracks; }
  getAudioTracks() { return this.tracks.filter((t) => t.kind === "audio"); }
  getVideoTracks() { return this.tracks.filter((t) => t.kind === "video"); }
}
class FakeAudioContext {
  state = "running";
  async resume() {}
  async close() { this.state = "closed"; }
  createMediaStreamDestination() { return { stream: new FakeStream([new FakeTrack("audio")]) }; }
  createMediaStreamSource() { return { connect: () => {} }; }
  createConstantSource() { return { offset: { value: 1 }, connect: () => {}, start: () => {} }; }
}
class FakeRecorder {
  static isTypeSupported() { return true; }
  state: "inactive" | "recording" | "paused" = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  calls: string[] = [];
  /** The last one made: the provider builds it, the test drives it. */
  static last: FakeRecorder | null = null;
  constructor(public stream: FakeStream, public options: Record<string, unknown>) { FakeRecorder.last = this; }
  start(slice: number) { this.state = "recording"; this.calls.push(`start:${slice}`); }
  pause() { this.state = "paused"; this.calls.push("pause"); }
  resume() { this.state = "recording"; this.calls.push("resume"); }
  stop() { this.state = "inactive"; this.calls.push("stop"); setTimeout(() => this.onstop?.(), 0); }
  emit(bytes: number) { this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)]) }); }
}

interface Shell {
  invokes: { cmd: string; args?: Record<string, unknown> }[];
  handlers: Record<string, (e: { payload: unknown }) => void>;
  monitors: unknown[];
  windows: unknown[];
  region: unknown;
  windowCalls: string[];
}
let shell: Shell;
let display: FakeStream;
let mic: FakeStream;
let displayResult: () => Promise<FakeStream>;
let micResult: () => Promise<FakeStream>;

function installShell() {
  shell = {
    invokes: [], handlers: {}, windowCalls: [],
    monitors: [{ index: 0, name: "\\\\.\\DISPLAY1", x: 0, y: 0, width: 1920, height: 1080, scale: 1, primary: true }],
    windows: [{ hwnd: 42, title: "Notepad", x: 100, y: 100, width: 800, height: 600 }],
    region: null,
  };
  const w = window as unknown as Record<string, unknown>;
  w.isTauri = true;
  w.__TAURI__ = {
    core: {
      invoke: vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
        shell.invokes.push({ cmd, args });
        if (cmd === "capture_monitors") return shell.monitors;
        if (cmd === "capture_windows") return shell.windows;
        if (cmd === "capture_bar_open") return true;
        if (cmd === "capture_overlay_open") {
          setTimeout(() => shell.handlers["capture:region"]?.({ payload: shell.region }), 0);
          return shell.monitors;
        }
        return undefined;
      }),
    },
    event: {
      listen: vi.fn(async (name: string, handler: (e: { payload: unknown }) => void) => {
        shell.handlers[name] = handler;
        return () => { if (shell.handlers[name] === handler) delete shell.handlers[name]; };
      }),
      emitTo: vi.fn(async () => {}),
    },
    window: {
      getCurrentWindow: () => ({
        minimize: async () => { shell.windowCalls.push("minimize"); },
        unminimize: async () => { shell.windowCalls.push("unminimize"); },
        setFocus: async () => { shell.windowCalls.push("setFocus"); },
      }),
    },
  };
  display = new FakeStream([
    new FakeTrack("video", { deviceId: "screen:1:0", width: 1920, height: 1080, frameRate: 30 }),
    new FakeTrack("audio", { deviceId: "loopback" }),
  ]);
  mic = new FakeStream([new FakeTrack("audio", { deviceId: "default" })]);
  displayResult = async () => display;
  micResult = async () => mic;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getDisplayMedia: vi.fn(() => displayResult()),
      getUserMedia: vi.fn(() => micResult()),
      enumerateDevices: vi.fn(async () => []),
    },
  });
  const g = globalThis as unknown as Record<string, unknown>;
  g.MediaStream = FakeStream;
  g.AudioContext = FakeAudioContext;
  g.MediaRecorder = FakeRecorder;
}

// ---- the server --------------------------------------------------------------------------------------

const posts: { path: string; body: any }[] = [];
const puts: { path: string; ok: boolean }[] = [];
const deletes: string[] = [];
let failPut = new Set<number>();
/** A chunk refusal the server answers with a status (507, 413) instead of a dropped connection. */
let refusePut: { seq: number; error: ApiError } | null = null;
/** The save job's status as GET /api/jobs/job-1 answers it; "unreachable" throws as a dead server does. */
let jobStatus = "running";
/** What GET /api/recordings/job answers on mount: a save running, and whose. */
let activeSave: { id: string; status: string; user_id: string } | null = null;
function installServer() {
  posts.length = 0;
  puts.length = 0;
  deletes.length = 0;
  failPut = new Set();
  refusePut = null;
  jobStatus = "running";
  activeSave = null;
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === "/api/recordings/job") return { active_job: activeSave } as never;
    if (path === "/api/jobs/job-1") {
      if (jobStatus === "unreachable") throw new TypeError("Failed to fetch");
      return { id: "job-1", status: jobStatus } as never;
    }
    if (activeSave && path === `/api/jobs/${activeSave.id}`) return { id: activeSave.id, status: activeSave.status } as never;
    return undefined as never;
  });
  vi.mocked(api.post).mockImplementation(async (path: string, body?: unknown) => {
    posts.push({ path, body });
    if (path === "/api/recordings") return { id: "rec000000001", name: (body as { name: string }).name } as never;
    if (path.endsWith("/finish")) return { job_id: "job-1" } as never;
    if (path === "/api/capture/freeze") return { frozen: [0] } as never;
    return {} as never;
  });
  vi.mocked(api.putRaw).mockImplementation(async (path: string) => {
    const seq = Number(path.split("/").pop());
    if (refusePut && refusePut.seq === seq) { puts.push({ path, ok: false }); throw refusePut.error; }
    const ok = !failPut.has(seq);
    puts.push({ path, ok });
    if (!ok) { failPut.delete(seq); throw new Error("connection reset"); }
    return { received: seq + 1 } as never;
  });
  vi.mocked(api.delete).mockImplementation(async (path: string) => { deletes.push(path); return undefined as never; });
}

// ---- mounting ------------------------------------------------------------------------------------------

let root: Root | null = null;
let host: HTMLDivElement | null = null;
let cap: CaptureApi;
let states: string[] = [];
let path = "/";

function Probe() {
  const c = useCapture();
  const loc = useLocation();
  cap = c;
  path = loc.pathname;
  useEffect(() => {
    if (states[states.length - 1] !== c.state) states.push(c.state);
  }, [c.state]);
  return null;
}

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(MemoryRouter, { initialEntries: ["/settings"] },
        createElement(CaptureProvider, null,
          createElement(Routes, null, createElement(Route, { path: "*", element: createElement(Probe) })),
        ),
      ),
    );
  });
}

async function until(cond: () => boolean, ms = 4000) {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out; states ${states.join(" > ")}; message ${cap?.message}`);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }
}

// Only preventDefault() counts: Chromium prompts on it, and NOT on an empty returnValue - which jsdom would
// otherwise take as a cancel (`returnValue = ""` is falsy), so a guard that lost its preventDefault() still
// passed here (plant F9 stayed green until this event ignored returnValue).
const unloadPrevented = () => {
  const ev = new Event("beforeunload", { cancelable: true });
  Object.defineProperty(ev, "returnValue", { configurable: true, get: () => true, set: () => {} });
  window.dispatchEvent(ev);
  return ev.defaultPrevented;
};

const record = (over: Partial<Parameters<CaptureApi["startRecording"]>[0]> = {}) =>
  act(async () => { void cap.startRecording({ kind: "screen", microphone: true, microphoneId: null, systemSound: true, ...over }); });

beforeEach(async () => {
  hoisted.countdown = 1;
  hoisted.max = 7_200_000;
  hoisted.heartbeat = 60;
  hoisted.poll = 50;
  states = [];
  FakeRecorder.last = null;
  installShell();
  installServer();
  await mount();
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  const w = window as unknown as Record<string, unknown>;
  delete w.isTauri;
  delete w.__TAURI__;
});

// ---- the walk -------------------------------------------------------------------------------------------

describe("the recorder", () => {
  it("walks idle to done, sends the chunks as they come, resends the unacknowledged one and goes to Projects", async () => {
    expect(unloadPrevented()).toBe(false);
    await record();
    await until(() => cap.state === "recording");
    expect(states).toEqual(["idle", "picking", "countdown", "recording"]);
    // The bar opened, excluded from capture; the keys taken; the window out of the way.
    expect(shell.invokes.map((i) => i.cmd)).toEqual(expect.arrayContaining(["capture_monitors", "capture_bar_open", "capture_hotkeys_start"]));
    expect(shell.windowCalls).toEqual(["minimize"]);
    expect(FakeRecorder.last!.calls).toEqual(["start:5000"]);
    // The recording was announced with its name and source.
    const started = posts.find((p) => p.path === "/api/recordings")!;
    expect(started.body.name).toMatch(/^Screen recording \d{4}-\d{2}-\d{2} \d{2}-\d{2}$/);
    expect(started.body.source).toEqual({ kind: "screen", region: null, monitor: null, window_title: null });
    expect(started.body.timeslice_ms).toBe(5000);
    // The shell builds the bar's URL: the page names none.
    expect(shell.invokes.find((i) => i.cmd === "capture_bar_open")!.args).not.toHaveProperty("pageUrl");
    // LIVE: the recorder's id is out for the Projects page to leave alone, and the heartbeat runs.
    expect(cap.liveRecordingId).toBe("rec000000001");
    await until(() => posts.some((p) => p.path === "/api/recordings/rec000000001/heartbeat"));
    expect(unloadPrevented()).toBe(true);
    // The shell's close guard is up while the recording is in flight.
    const guards = () => shell.invokes.filter((i) => i.cmd === "capture_guard").map((i) => i.args!.active);
    expect(guards().at(-1)).toBe(true);
    // A close the shell refused is explained.
    await act(async () => shell.handlers["capture:close-blocked"]({ payload: null }));
    expect(cap.message).toMatch(/stop it first/);

    // Two chunks: the first lands, the second's upload fails and waits for Finish.
    failPut.add(1);
    await act(async () => { FakeRecorder.last!.emit(10); FakeRecorder.last!.emit(20); await new Promise((r) => setTimeout(r, 10)); });
    expect(puts).toEqual([{ path: "/api/recordings/rec000000001/chunks/0", ok: true }, { path: "/api/recordings/rec000000001/chunks/1", ok: false }]);

    // Pause from the shell's hotkey, resume from the page's key.
    await act(async () => shell.handlers["capture:hotkey"]({ payload: "pause" }));
    expect(cap.state).toBe("paused");
    const resumeKey = new KeyboardEvent("keydown", { key: "F9", shiftKey: true, cancelable: true });
    await act(async () => { window.dispatchEvent(resumeKey); });
    expect(cap.state).toBe("recording");
    expect(resumeKey.defaultPrevented).toBe(true);
    expect(FakeRecorder.last!.calls).toEqual(["start:5000", "pause", "resume"]);

    // Stop from the bar.
    await act(async () => shell.handlers["capture:control"]({ payload: { action: "stop" } }));
    await until(() => cap.state === "done");
    expect(states).toEqual(["idle", "picking", "countdown", "recording", "paused", "recording", "saving", "done"]);
    expect(puts.slice(2)).toEqual([{ path: "/api/recordings/rec000000001/chunks/1", ok: true }]);
    const finish = posts.find((p) => p.path === "/api/recordings/rec000000001/finish")!;
    expect(finish.body.chunks).toBe(2);
    expect(finish.body.from_recorder).toBe(true);
    expect(cap.savingJobId).toBe("job-1");
    expect(cap.liveRecordingId).toBeNull();
    expect(path).toBe("/projects");
    expect(shell.invokes.map((i) => i.cmd)).toEqual(expect.arrayContaining(["capture_bar_close", "capture_hotkeys_stop"]));
    expect(shell.windowCalls).toEqual(["minimize", "unminimize", "setFocus"]);
    expect(display.tracks.every((t) => t.stopped) && mic.tracks.every((t) => t.stopped)).toBe(true);
    expect(unloadPrevented()).toBe(false);
    expect(guards()[0]).toBe(false); // down at idle, before anything started
    // The save job still runs: closing the window would stop it, so the shell's guard stays up - and a
    // refused close says why.
    expect(cap.saveRunning).toBe(true);
    expect(guards().at(-1)).toBe(true);
    await act(async () => shell.handlers["capture:close-blocked"]({ payload: null }));
    expect(cap.message).toMatch(/being saved as a project/);
    // The job ends: the guard comes down.
    jobStatus = "done";
    await until(() => !cap.saveRunning);
    expect(guards().at(-1)).toBe(false);
    // The heartbeat stopped with the recording.
    const beats = posts.filter((p) => p.path.endsWith("/heartbeat")).length;
    await act(async () => { await new Promise((r) => setTimeout(r, 200)); });
    expect(posts.filter((p) => p.path.endsWith("/heartbeat")).length).toBe(beats);
    // Taken over by the Projects page once, then cleared.
    await act(async () => cap.clearSavingJob());
    expect(cap.savingJobId).toBeNull();
  });

  it("does nothing with the keys outside a recording", async () => {
    const stopKey = new KeyboardEvent("keydown", { key: "F10", shiftKey: true, cancelable: true });
    await act(async () => { window.dispatchEvent(stopKey); });
    expect(stopKey.defaultPrevented).toBe(false);
    expect(cap.state).toBe("idle");
    expect(posts).toEqual([]);
  });

  it("names a window share after the window its handle names", async () => {
    display = new FakeStream([new FakeTrack("video", { deviceId: "window:42:0", width: 800, height: 600, frameRate: 30 }), new FakeTrack("audio")]);
    await record({ kind: "window", microphone: false });
    await until(() => cap.state === "recording");
    const started = posts.find((p) => p.path === "/api/recordings")!;
    expect(started.body.name).toBe("Notepad");
    expect(started.body.source.window_title).toBe("Notepad");
    // No overlay for a window: the share dialog is the window picker.
    expect(shell.invokes.some((i) => i.cmd === "capture_overlay_open")).toBe(false);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("goes back to idle with one line when the share dialog is cancelled, leaving nothing behind", async () => {
    displayResult = async () => { throw new DOMException("Permission denied by user", "NotAllowedError"); };
    await record();
    await until(() => cap.state === "idle" && !!cap.message);
    // picking -> idle may land in one render; what matters is that the dialog was asked and nothing more.
    expect(navigator.mediaDevices.getDisplayMedia).toHaveBeenCalledTimes(1);
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(cap.message).toBe("Nothing was shared: the share dialog was cancelled or refused.");
    expect(posts.some((p) => p.path === "/api/recordings")).toBe(false);
    expect(unloadPrevented()).toBe(false);
  });

  it("stops the shared picture when the microphone is refused", async () => {
    micResult = async () => { throw new DOMException("denied", "NotAllowedError"); };
    await record();
    await until(() => cap.state === "idle" && !!cap.message);
    expect(cap.message).toMatch(/microphone was refused/);
    expect(display.tracks.every((t) => t.stopped)).toBe(true);
    expect(posts.some((p) => p.path === "/api/recordings")).toBe(false);
  });

  it("refuses a region drawn on one monitor when another screen is shared", async () => {
    shell.monitors = [
      { index: 0, name: "A", x: 0, y: 0, width: 1920, height: 1080, scale: 1, primary: true },
      { index: 1, name: "B", x: -1280, y: 0, width: 1280, height: 720, scale: 1, primary: false },
    ];
    shell.region = { x: -1180, y: 100, width: 400, height: 300, monitor: 1, window_title: null, hwnd: null };
    await record({ kind: "region" });
    await until(() => cap.state === "idle" && !!cap.message);
    expect(cap.message).toMatch(/not the one the region was drawn on/);
    expect(posts.map((p) => p.path)).toEqual(["/api/capture/freeze"]);
    expect(display.tracks.every((t) => t.stopped)).toBe(true);
    // The frozen pictures of the desktop are deleted once the match is made.
    expect(api.delete).toHaveBeenCalledWith("/api/capture/frozen");
  });

  it("records a region when its own screen is shared, with the crop's numbers", async () => {
    shell.monitors = [
      { index: 0, name: "A", x: 0, y: 0, width: 1920, height: 1080, scale: 1, primary: true },
      { index: 1, name: "B", x: -1280, y: 0, width: 1280, height: 720, scale: 1, primary: false },
    ];
    shell.region = { x: -1180, y: 100, width: 400, height: 300, monitor: 1, window_title: null, hwnd: null };
    // As measured: the settings first report the LARGEST screen's size; the capabilities carry the true one.
    display = new FakeStream([new FakeTrack("video", { deviceId: "screen:2:0", width: 1920, height: 1080, frameRate: 30 },
      { width: { max: 1280 }, height: { max: 720 } })]);
    await record({ kind: "region", microphone: false, systemSound: false });
    await until(() => cap.state === "recording");
    const started = posts.find((p) => p.path === "/api/recordings")!;
    expect([started.body.width, started.body.height]).toEqual([1280, 720]);
    expect(started.body.source).toEqual({
      kind: "region",
      region: { x: -1180, y: 100, width: 400, height: 300 },
      monitor: { x: -1280, y: 0, width: 1280, height: 720 },
      window_title: null,
    });
    const bar = shell.invokes.find((i) => i.cmd === "capture_bar_open")!.args!;
    expect(bar.y).toBe(100 + 300 + 12); // under the region, on its monitor
  });

  it("stops at the two-hour limit with a message", async () => {
    hoisted.max = 1500;
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await until(() => cap.state === "done", 6000);
    expect(cap.message).toBe("The recording reached the two-hour limit and was stopped.");
    expect(posts.some((p) => p.path.endsWith("/finish"))).toBe(true);
  });
});

describe("the fix round", () => {
  it("releases a bar, keys and overlays a previous page left behind, on mount", () => {
    const first = shell.invokes.slice(0, 3).map((i) => i.cmd).sort();
    expect(first).toEqual(["capture_bar_close", "capture_hotkeys_stop", "capture_overlay_close"]);
  });

  it("refuses a region recording whose share is a WINDOW on the region's monitor (review probe A)", async () => {
    shell.region = { x: 1200, y: 700, width: 400, height: 300, monitor: 0, window_title: null, hwnd: null };
    display = new FakeStream([new FakeTrack("video", { deviceId: "window:42:0", width: 800, height: 600, frameRate: 30 })]);
    await record({ kind: "region", microphone: false, systemSound: false });
    await until(() => cap.state === "idle" && !!cap.message);
    expect(cap.message).toMatch(/not the one the region was drawn on/);
    expect(posts.some((p) => p.path === "/api/recordings")).toBe(false);
    expect(display.tracks.every((t) => t.stopped)).toBe(true);
  });

  it("goes back to idle when the overlay closes without an answer (Escape, or Alt+F4: the shell sends null)", async () => {
    shell.region = null;
    await record({ kind: "region" });
    await until(() => cap.state === "idle" && shell.invokes.some((i) => i.cmd === "capture_overlay_open"));
    expect(navigator.mediaDevices.getDisplayMedia).not.toHaveBeenCalled();
    expect(posts.map((p) => p.path)).toEqual(["/api/capture/freeze"]);
    expect(api.delete).toHaveBeenCalledWith("/api/capture/frozen");
  });

  it("cuts a window snapped to across its monitor's edge to that monitor", async () => {
    shell.region = { x: 1700, y: 100, width: 440, height: 300, monitor: 0, window_title: "Notes", hwnd: 42 };
    await record({ kind: "region", microphone: false, systemSound: false });
    await until(() => cap.state === "recording");
    const started = posts.find((p) => p.path === "/api/recordings")!;
    expect(started.body.source.region).toEqual({ x: 1700, y: 100, width: 220, height: 300 });
  });

  it("keeps a recording whose recorder failed after a chunk landed: let go, never deleted (review probe B)", async () => {
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await act(async () => { FakeRecorder.last!.emit(10); await new Promise((r) => setTimeout(r, 10)); });
    await act(async () => { FakeRecorder.last!.onerror?.(); });
    await until(() => posts.some((p) => p.path === "/api/recordings/rec000000001/release"));
    expect(cap.state).toBe("failed");
    expect(deletes.filter((d) => d.startsWith("/api/recordings/"))).toEqual([]);
    expect(cap.message).toMatch(/The recorder failed; the recording was stopped\. What was recorded up to then is kept/);
    expect(cap.liveRecordingId).toBeNull();
  });

  it("throws away a recording whose recorder failed before any chunk landed", async () => {
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await act(async () => { FakeRecorder.last!.onerror?.(); });
    await until(() => deletes.includes("/api/recordings/rec000000001?from_recorder=true"));
    expect(posts.some((p) => p.path.endsWith("/release"))).toBe(false);
  });

  it("stops with the server's line when a chunk is refused for the disk, keeping what arrived (507)", async () => {
    const line = "Less than 1 GB is left on the disk Media Studio records to, so the recording was stopped; what arrived is kept.";
    refusePut = { seq: 1, error: new ApiError(507, line) };
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await act(async () => { FakeRecorder.last!.emit(10); await new Promise((r) => setTimeout(r, 10)); });
    await act(async () => { FakeRecorder.last!.emit(10); await new Promise((r) => setTimeout(r, 10)); });
    await until(() => cap.state === "failed");
    expect(cap.message).toBe(line);
    expect(FakeRecorder.last!.calls).toContain("stop");
    await until(() => posts.some((p) => p.path === "/api/recordings/rec000000001/release"));
    expect(deletes.filter((d) => d.startsWith("/api/recordings/"))).toEqual([]);
    expect(posts.some((p) => p.path.endsWith("/finish"))).toBe(false);
  });

  it("goes on after a chunk lost to the connection: it is sent again at Stop", async () => {
    refusePut = { seq: 0, error: new ApiError(502, "Bad gateway") };
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await act(async () => { FakeRecorder.last!.emit(10); await new Promise((r) => setTimeout(r, 10)); });
    expect(cap.state).toBe("recording");
  });

  it("cancels the countdown from the bar: nothing recorded, the recording thrown away, back to idle", async () => {
    hoisted.countdown = 3;
    await record({ microphone: false });
    await until(() => cap.state === "countdown");
    await act(async () => shell.handlers["capture:control"]({ payload: { action: "cancel" } }));
    expect(cap.state).toBe("idle");
    await until(() => deletes.includes("/api/recordings/rec000000001?from_recorder=true"));
    expect(cap.liveRecordingId).toBeNull();
    expect(display.tracks.every((t) => t.stopped)).toBe(true);
    expect(shell.invokes.map((i) => i.cmd)).toEqual(expect.arrayContaining(["capture_bar_close", "capture_hotkeys_stop"]));
    await act(async () => { await new Promise((r) => setTimeout(r, 1200)); });
    expect(FakeRecorder.last).toBeNull(); // the recorder was never made
  });

  it("cancels the countdown with Shift+F10 too", async () => {
    hoisted.countdown = 3;
    await record({ microphone: false });
    await until(() => cap.state === "countdown");
    await act(async () => shell.handlers["capture:hotkey"]({ payload: "stop" }));
    expect(cap.state).toBe("idle");
    await until(() => deletes.includes("/api/recordings/rec000000001?from_recorder=true"));
  });
});

describe("the re-review round", () => {
  const guards = () => shell.invokes.filter((i) => i.cmd === "capture_guard").map((i) => i.args!.active);

  async function stopAndSave() {
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await act(async () => { FakeRecorder.last!.emit(10); await new Promise((r) => setTimeout(r, 10)); });
    await act(async () => shell.handlers["capture:control"]({ payload: { action: "stop" } }));
    await until(() => cap.state === "done");
  }

  it("drops the save's close guard, saying why, once the server stops answering", async () => {
    await stopAndSave();
    expect(cap.saveRunning).toBe(true);
    expect(guards().at(-1)).toBe(true);
    jobStatus = "unreachable";  // every poll now fails as a dead backend's would
    // Three polls 50 ms apart: well inside 1.5 s, and inside the test's own time limit when it fails.
    await until(() => !cap.saveRunning, 1500);
    expect(guards().at(-1)).toBe(false);
    expect(cap.message).toMatch(/server stopped answering while the recording was being saved/);
    // Polled MAX_POLL_FAILURES times, then let go: no more asking.
    const asked = vi.mocked(api.get).mock.calls.filter(([p]) => p === "/api/jobs/job-1").length;
    await act(async () => { await new Promise((r) => setTimeout(r, 300)); });
    expect(vi.mocked(api.get).mock.calls.filter(([p]) => p === "/api/jobs/job-1").length).toBe(asked);
  });

  it("keeps the guard through a passing failure: two unanswered polls and then an answer", async () => {
    await stopAndSave();
    jobStatus = "unreachable";
    await until(() => vi.mocked(api.get).mock.calls.filter(([p]) => p === "/api/jobs/job-1").length >= 2);
    jobStatus = "running";
    await act(async () => { await new Promise((r) => setTimeout(r, 300)); });
    expect(cap.saveRunning).toBe(true);
    expect(guards().at(-1)).toBe(true);
  });

  it("lowers the guard when the provider unmounts mid-recording (sign-out, a reload)", async () => {
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    expect(guards().at(-1)).toBe(true);
    await act(async () => root?.unmount());
    root = null;
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    expect(guards().at(-1)).toBe(false);
  });

  it("keeps the heartbeat going while PAUSED, so a pause never lapses", async () => {
    await record({ microphone: false });
    await until(() => cap.state === "recording");
    await act(async () => shell.handlers["capture:hotkey"]({ payload: "pause" }));
    expect(cap.state).toBe("paused");
    const before = posts.filter((p) => p.path.endsWith("/heartbeat")).length;
    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    expect(cap.state).toBe("paused");
    expect(posts.filter((p) => p.path === "/api/recordings/rec000000001/heartbeat").length).toBeGreaterThan(before);
  });

  async function remount() {
    await act(async () => root?.unmount());
    host?.remove();
    shell.invokes.length = 0;
    await mount();
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }

  it("adopts the caller's own running save on mount, and holds the guard for it", async () => {
    activeSave = { id: "job-7", status: "running", user_id: "u1" };
    await remount();
    expect(cap.saveRunning).toBe(true);
    expect(guards().at(-1)).toBe(true);
  });

  it("does not adopt somebody else's running save (an administrator's scope route shows everyone's)", async () => {
    activeSave = { id: "job-8", status: "running", user_id: "u2" };
    await remount();
    expect(cap.saveRunning).toBe(false);
    expect(guards().every((g) => g === false)).toBe(true);
  });
});
