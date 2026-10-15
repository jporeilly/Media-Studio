// @vitest-environment jsdom
/**
 * The guided capture (W2) in the builder, with the shell's bridge faked (`window.__TAURI__`: the monitors, the
 * windows, the bar window, the keys, the events) and the server faked (`api`), inside the desktop shell
 * (`useCaptureAvailable` true). Pinned:
 * - Capture steps… opens the options dialog; Start saves a dirty document first, writes the start row, opens the
 *   bar in its second mode where the recorder bar goes, takes the Shift+F8 key, minimises the window and tells the
 *   bar "Step 2 of 3" with the step's text and instruction (the first step without a picture);
 * - the shell's key with the pointer: the window under the pointer is captured (`POST /api/captures` as the Capture
 *   dialog takes a still, named "<walkthrough> · step N"), attached with the hotspot round the pointer in the
 *   picture's own pixels, the server's record becomes the saved baseline (the list shows the picture, nothing
 *   unsaved), and the bar moves to the next step;
 * - the bar's Capture button carries no pointer: the front-most window, no hotspot;
 * - Skip, Back, the end, Done: the bar closed, the key given back, the window restored, the done row with the
 *   counts, the builder reloaded;
 * - the bar's listeners are up before the bar opens, and the bar's hello is answered with the state as it stands
 *   (never while idle); the refusals the capture goes on under (the key taken, the bar not hidden) ride in the
 *   payload as `message`; a bar that cannot be opened gives the start up with the line, nothing taken;
 * - refused while a recording is in flight, with the line and nothing taken;
 * - a region is chosen once through the overlay and reused.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { RECORDING_IN_PROGRESS } from "../lib/guided";
import type { Step, Walkthrough } from "../lib/walkthrough";
import WalkthroughBuilderPage from "./WalkthroughBuilder";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({ recorder: "idle" as string, region: null as unknown }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});
vi.mock("../lib/useCaptureAvailable", () => ({ useCaptureAvailable: () => true }));
vi.mock("../lib/captureShared", async (importOriginal) => {
  const real = await importOriginal<typeof import("../lib/captureShared")>();
  return {
    ...real,
    useRecorderState: () => hoisted.recorder,
    pickRegion: vi.fn(async () => hoisted.region),
    forgetFrozen: vi.fn(),
  };
});

const WID = "abc123abc123";
const A = "aaaaaaaaaaaa", B = "bbbbbbbbbbbb", C = "cccccccccccc";
const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: null, kind: "tooltip", text: `Text ${id}`, instruction: `Do ${id}`, placement: "auto", chapter: "", note: "", source: null, ...over,
});
const bare = (id: string, over: Partial<Step> = {}) => step(id, { image: null, width: 0, height: 0, ...over });
const record = (over: Partial<Walkthrough> = {}): Walkthrough => ({
  id: WID, name: "Tour", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z",
  revision: 2, version: 1, steps: [step(A), bare(B), bare(C)], ...over,
});

interface Shell {
  /** Every command invoked, in order - and, in the same list, every `listen:<event>` as it was registered. */
  invokes: { cmd: string; args?: Record<string, unknown> }[];
  handlers: Record<string, (e: { payload: unknown }) => void>;
  told: unknown[];
  windowCalls: string[];
  hotkeysRefused: boolean;
  /** `capture_bar_open` throws (no origin, the window refused). */
  barRefused: boolean;
  /** `capture_bar_open` answers false: this Windows cannot hide the bar from the picture. */
  barNotHidden: boolean;
}
let shell: Shell;

function installShell() {
  shell = { invokes: [], handlers: {}, told: [], windowCalls: [], hotkeysRefused: false, barRefused: false, barNotHidden: false };
  const w = window as unknown as Record<string, unknown>;
  w.isTauri = true;
  w.__TAURI__ = {
    core: {
      invoke: vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
        shell.invokes.push({ cmd, args });
        if (cmd === "capture_monitors") return [{ index: 0, name: "A", x: 0, y: 0, width: 1920, height: 1080, scale: 1, primary: true }];
        if (cmd === "capture_windows") return [
          { hwnd: 7, title: "Dialog on top", x: 300, y: 300, width: 400, height: 200 },
          { hwnd: 42, title: "Spoon", x: 100, y: 100, width: 1200, height: 800 },
        ];
        if (cmd === "capture_bar_open" && shell.barRefused) throw new Error("No backend origin yet.");
        if (cmd === "capture_bar_open") return !shell.barNotHidden;
        if (cmd === "capture_hotkeys_start" && shell.hotkeysRefused) throw new Error("Shift+F8 is taken by another program.");
        return undefined;
      }),
    },
    event: {
      listen: vi.fn(async (name: string, handler: (e: { payload: unknown }) => void) => {
        shell.invokes.push({ cmd: `listen:${name}` });
        shell.handlers[name] = handler;
        return () => { if (shell.handlers[name] === handler) delete shell.handlers[name]; };
      }),
      emitTo: vi.fn(async (_target: string, _name: string, payload: unknown) => { shell.told.push(payload); }),
    },
    window: {
      getCurrentWindow: () => ({
        minimize: async () => { shell.windowCalls.push("minimize"); },
        unminimize: async () => { shell.windowCalls.push("unminimize"); },
        setFocus: async () => { shell.windowCalls.push("setFocus"); },
      }),
    },
  };
}

let root: Root | null = null;
let host: HTMLDivElement | null = null;
let server: Walkthrough;
let stillId = 0;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [`/walkthroughs/${WID}`] },
          createElement(Routes, null, createElement(Route, { path: "/walkthroughs/:id", element: createElement(WalkthroughBuilderPage) })),
        ),
      ),
    );
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const status = () => (q("[data-builder-status]")?.textContent || "").trim();
const invoked = (cmd: string) => shell.invokes.filter((i) => i.cmd === cmd);
const lastTold = () => shell.told[shell.told.length - 1] as Record<string, unknown>;
const fire = (name: string, payload: unknown) => act(async () => { shell.handlers[name]?.({ payload }); await new Promise((r) => setTimeout(r, 30)); });
const posts = () => vi.mocked(api.post).mock.calls;
/** The still ids the faked server hands out, in order. */
const cid = (n: number) => `cap0000000${String(n).padStart(2, "0")}`;
function setValue(el: HTMLTextAreaElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function openAndStart() {
  await act(async () => { q("[data-capture-steps]")!.click(); });
  expect(q("[role=dialog]")!.textContent).toContain("Capture steps");
  await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
  await settle(60);
}

beforeEach(() => {
  installShell();
  hoisted.recorder = "idle";
  hoisted.region = null;
  stillId = 0;
  server = record();
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === `/api/walkthroughs/${WID}`) return server as never;
    return {} as never;
  });
  vi.mocked(api.put).mockImplementation(async (_path: string, body: unknown) => {
    const sent = body as { name: string; revision: number; steps: Step[] };
    server = { ...server, name: sent.name, steps: sent.steps, revision: sent.revision + 1 };
    return server as never;
  });
  vi.mocked(api.post).mockImplementation(async (path: string, body: unknown) => {
    if (path === "/api/captures") {
      const b = body as { x: number; y: number; width: number; height: number };
      stillId += 1;
      return { id: `cap0000000${stillId.toString().padStart(2, "0")}`, x: b.x, y: b.y, width: b.width, height: b.height } as never;
    }
    const attach = /\/api\/walkthroughs\/[^/]+\/steps\/([^/]+)\/picture$/.exec(path);
    if (attach) {
      const b = body as { capture_id: string; hotspot: Step["hotspot"] };
      server = {
        ...server, revision: server.revision + 1,
        steps: server.steps.map((s) => (s.id === attach[1] ? { ...s, image: `step-${s.id}.png`, width: 1200, height: 800, hotspot: b.hotspot, source: { capture_id: b.capture_id, annotated: false } } : s)),
      };
      return server as never;
    }
    return undefined as never;
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("the guided capture", () => {
  it("Start saves first, writes the start row, opens the bar in its second mode, takes the key, minimises the window and tells the bar the first step without a picture", async () => {
    await mount();
    expect(q("[data-capture-steps]")).not.toBeNull();
    // A dirty document: Start saves it first (as Play does).
    await act(async () => { setValue(document.querySelector("[data-step-text]") as HTMLTextAreaElement, "Edited"); });
    expect(status()).toBe("Unsaved changes");
    await openAndStart();
    expect(vi.mocked(api.put)).toHaveBeenCalledTimes(1);
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "start", kind: "window", cursor: true }))).toBe(true);
    const bar = invoked("capture_bar_open");
    expect(bar.length).toBe(1);
    expect(bar[0].args).toMatchObject({ mode: "steps", width: 600, height: 96 });
    expect((bar[0].args!.x as number) + 600 + 12).toBe(1920);
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps" });
    expect(shell.windowCalls).toEqual(["minimize"]);
    expect(Object.keys(shell.handlers).sort()).toEqual(["capture:control", "capture:step-key"]);
    // The listeners are up BEFORE the bar opens: the bar's hello (and a key press) can arrive the moment it is.
    const order = shell.invokes.map((i) => i.cmd);
    expect(order.indexOf("listen:capture:control")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-key")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(lastTold()).toMatchObject({ mode: "steps", phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text bbbbbbbbbbbb", instruction: "Do bbbbbbbbbbbb", remaining: 2, canBack: false, canCapture: true, message: null });
    expect(q("[data-capture-label]")!.textContent).toBe("Step 2 of 3");
    expect(q("[data-capture-steps]")!.hasAttribute("disabled")).toBe(true);
    // The bar's hello (its listener is up): the current state is sent again, as it stands - the first state is never missed.
    shell.told.length = 0;
    await fire("capture:control", { action: "hello" });
    expect(shell.told.length).toBe(1);
    expect(lastTold()).toMatchObject({ mode: "steps", phase: "ready", label: "Step 2 of 3", text: "Text bbbbbbbbbbbb", canCapture: true, message: null });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
  });

  it("a hello while idle is ignored, and after Done nothing listens", async () => {
    await mount();
    await openAndStart();
    await fire("capture:control", { action: "done" });
    await settle();
    expect(Object.keys(shell.handlers)).toEqual([]);
    shell.told.length = 0;
    shell.handlers["capture:control"]?.({ payload: { action: "hello" } });
    await settle();
    expect(shell.told).toEqual([]);
  });

  it("the refusals the capture goes on under ride in the bar's payload: the key taken, the bar not hidden from the picture", async () => {
    shell.hotkeysRefused = true;
    shell.barNotHidden = true;
    await mount();
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toContain("Shift+F8 is taken");
    const told = lastTold() as { message: string; canCapture: boolean };
    expect(told.canCapture).toBe(true);
    expect(told.message).toContain("Shift+F8 is taken");
    expect(told.message).toContain("cannot hide the capture bar");
    // The hello says the same; a capture goes on from the bar's button.
    shell.told.length = 0;
    await fire("capture:control", { action: "hello" });
    expect((lastTold() as { message: string }).message).toContain("Shift+F8 is taken");
    await fire("capture:control", { action: "capture" });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    expect((lastTold() as { message: string; label: string }).label).toBe("Step 3 of 3");
    expect((lastTold() as { message: string }).message).toContain("Shift+F8 is taken");
  });

  it("a bar that cannot be opened gives the start up: the line names it, no key taken, no start row, the window stays", async () => {
    shell.barRefused = true;
    await mount();
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toContain("The capture bar could not be opened");
    expect(q("[data-capture-message]")!.textContent).toContain("No backend origin yet.");
    expect(q("[data-capture-message]")!.textContent).not.toContain("cannot hide");
    expect(invoked("capture_bar_open").length).toBe(1);
    expect(invoked("capture_hotkeys_start")).toEqual([]);
    expect(posts().filter(([p]) => p.endsWith("/guided-capture"))).toEqual([]);
    expect(shell.windowCalls).toEqual([]);
    expect(Object.keys(shell.handlers)).toEqual([]);
    expect(invoked("capture_bar_close").length).toBe(1);
    expect(q("[data-capture-steps]")!.hasAttribute("disabled")).toBe(false);
    expect(q("[data-capture-label]")).toBeNull();
  });

  it("the shell's key with the pointer captures the window under it, attaches it with the hotspot round the pointer, and moves the bar on; the bar's button captures the front-most window with no hotspot", async () => {
    await mount();
    await openAndStart();
    // The pointer on Spoon (100..1300 x 100..900), outside the dialog on top of it (300..700 x 300..500).
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(invoked("capture_windows").length).toBe(1);
    const still = posts().find(([p]) => p === "/api/captures")!;
    expect(still[1]).toEqual({ x: 100, y: 100, width: 1200, height: 800, cursor: true, kind: "window", name: "Tour · step 2" });
    const attach = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)!;
    expect(attach[1]).toEqual({ capture_id: cid(1), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    // The server's record is the saved baseline: the list shows B's picture, nothing is unsaved.
    expect(q(`[data-step="${B}"] img`)).not.toBeNull();
    expect(q(`[data-step="${B}"] [data-step-no-picture]`)).toBeNull();
    expect(status()).toBe("Saved");
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", text: "Text cccccccccccc", remaining: 1, canBack: true });
    // The bar's Capture button: no pointer, the front-most window, no hotspot.
    await fire("capture:control", { action: "capture" });
    const second = posts().filter(([p]) => p === "/api/captures")[1];
    expect(second[1]).toMatchObject({ x: 300, y: 300, width: 400, height: 200, name: "Tour · step 3" });
    const attachC = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)!;
    expect(attachC[1]).toEqual({ capture_id: cid(2), hotspot: null });
    expect(lastTold()).toMatchObject({ phase: "finished", label: "All steps visited", remaining: 0, canCapture: false });
    // Done: the bar closed, the key given back, the window restored, the done row, the builder reloaded.
    const gets = vi.mocked(api.get).mock.calls.length;
    await fire("capture:control", { action: "done" });
    await settle();
    expect(invoked("capture_bar_close").length).toBe(1);
    expect(invoked("capture_hotkeys_stop").length).toBe(1);
    expect(shell.windowCalls).toEqual(["minimize", "unminimize", "setFocus"]);
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 2, skipped: 0 }))).toBe(true);
    expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(gets);
    expect(status()).toBe("Captured 2 steps.");
    expect(q("[data-capture-line]")).toBeNull();
    expect(Object.keys(shell.handlers)).toEqual([]);
  });

  it("Skip leaves a step for later, Back returns to it, and a pointer outside the window gives no hotspot; the page's own Shift+F8 captures without a pointer", async () => {
    await mount();
    await openAndStart();
    await fire("capture:control", { action: "skip" });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", canBack: true });
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", canBack: false });
    // The pointer on the window's frame but the capture is the window: a pointer outside the captured area is no hotspot.
    await fire("capture:step-key", { x: 50, y: 50 });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)).toBeUndefined();
    expect(q("[data-capture-message]")!.textContent).toContain("No window is under the pointer");
    await fire("capture:step-key", { x: 150, y: 150 });
    const attach = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)!;
    expect(attach[1]).toEqual({ capture_id: cid(1), hotspot: { x: 0, y: 20, width: 160, height: 60 } });
    // The page's key: no pointer, so the front-most window and no hotspot.
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "F8", shiftKey: true, bubbles: true, cancelable: true })); await new Promise((r) => setTimeout(r, 30)); });
    const attachC = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)!;
    expect(attachC[1]).toEqual({ capture_id: cid(2), hotspot: null });
    await act(async () => { q("[data-capture-done]")!.click(); });
    await settle();
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 2, skipped: 1 }))).toBe(true);
    expect(status()).toBe("Captured 2 steps, skipped 1.");
  });

  it("is refused while a recording is in flight, with the line and nothing taken", async () => {
    hoisted.recorder = "recording";
    await mount();
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toBe(RECORDING_IN_PROGRESS);
    expect(invoked("capture_bar_open")).toEqual([]);
    expect(invoked("capture_hotkeys_start")).toEqual([]);
    expect(posts().filter(([p]) => p.endsWith("/guided-capture"))).toEqual([]);
    expect(shell.windowCalls).toEqual([]);
  });

  it("a region is chosen once through the overlay and reused for every step; cancelling the overlay starts nothing; a refused key still leaves the bar's button", async () => {
    const { pickRegion } = await import("../lib/captureShared");
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(vi.mocked(pickRegion)).toHaveBeenCalledTimes(1);
    expect(invoked("capture_bar_open")).toEqual([]);
    expect(q("[data-capture-steps]")!.hasAttribute("disabled")).toBe(false);
    hoisted.region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    shell.hotkeysRefused = true;
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(invoked("capture_bar_open").length).toBe(1);
    // Under the region (y = 20 + 200 + 12), its x kept inside the monitor's margin.
    expect(invoked("capture_bar_open")[0].args).toMatchObject({ x: 12, y: 232, mode: "steps" });
    expect(q("[data-capture-message]")!.textContent).toContain("Shift+F8 is taken");
    expect(lastTold()).toMatchObject({ message: expect.stringContaining("Shift+F8 is taken") });
    await fire("capture:control", { action: "capture" });
    await fire("capture:step-key", { x: 100, y: 100 });
    const stills = posts().filter(([p]) => p === "/api/captures");
    expect(stills.map(([, b]) => b)).toEqual([
      { x: 10, y: 20, width: 300, height: 200, cursor: true, kind: "region", name: "Tour · step 2" },
      { x: 10, y: 20, width: 300, height: 200, cursor: true, kind: "region", name: "Tour · step 3" },
    ]);
    expect(invoked("capture_windows")).toEqual([]);
    const attachC = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)!;
    expect(attachC[1]).toEqual({ capture_id: cid(2), hotspot: { x: 10, y: 50, width: 160, height: 60 } });
  });
});
