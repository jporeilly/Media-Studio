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
 * - a region is chosen once through the overlay and reused;
 * - (W2b) Capture on click, on by default: the key is taken with `clicks: true`, the start row says so, the bar's
 *   payload carries `clicks`; the shell's click takes the window under the point with the hotspot round it through
 *   the SAME path as the key, a right click as a left one; a click while a capture is in flight is dropped; for a
 *   region a click outside the frame is no step and the bar says "Click inside the frame…" until the next payload;
 *   the tick off is the 0.17.0 behaviour (no hook, no listener); a shell that could not install the hook puts the
 *   clicks-off line on the bar; a capture's own refusal (no window under the pointer, a still that failed) rides to
 *   the bar as a transient message (D1); "Captured N steps" counts distinct steps and the done row the captures.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { CLICKS_OFF, KEY_TAKEN, RECORDING_IN_PROGRESS } from "../lib/guided";
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
  /** `capture_hotkeys_start` throws: a shell before H1 whose key was taken (no hook either), or the other set held. */
  hotkeysRefused: boolean;
  /** `capture_bar_open` throws (no origin, the window refused). */
  barRefused: boolean;
  /** `capture_bar_open` answers false: this Windows cannot hide the bar from the picture. */
  barNotHidden: boolean;
  /** Another program holds Shift+F8: the shell answers `key: false` (H1) - and still installs the hook when asked. */
  keyTaken: boolean;
  /** The click hook could not be installed: the shell answers `clicks: false`. */
  clicksRefused: boolean;
  /** When set, `capture_hotkeys_start` answers this instead (an older shell: `undefined` from 0.17.0, a boolean from W2b before H1). */
  legacyAnswer: { value: unknown } | null;
  /** When set, `capture_windows` waits on it: presses that arrive inside one window-list round trip. */
  windowsGate: Promise<void> | null;
}
let shell: Shell;

function installShell() {
  shell = {
    invokes: [], handlers: {}, told: [], windowCalls: [], hotkeysRefused: false, barRefused: false, barNotHidden: false, keyTaken: false, clicksRefused: false,
    legacyAnswer: null, windowsGate: null,
  };
  const w = window as unknown as Record<string, unknown>;
  w.isTauri = true;
  w.__TAURI__ = {
    core: {
      invoke: vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
        shell.invokes.push({ cmd, args });
        if (cmd === "capture_monitors") return [{ index: 0, name: "A", x: 0, y: 0, width: 1920, height: 1080, scale: 1, primary: true }];
        if (cmd === "capture_windows") {
          if (shell.windowsGate) await shell.windowsGate;
          return [
            { hwnd: 7, title: "Dialog on top", x: 300, y: 300, width: 400, height: 200 },
            { hwnd: 42, title: "Spoon", x: 100, y: 100, width: 1200, height: 800 },
          ];
        }
        if (cmd === "capture_bar_open" && shell.barRefused) throw new Error("No backend origin yet.");
        if (cmd === "capture_bar_open") return !shell.barNotHidden;
        if (cmd === "capture_hotkeys_start" && shell.hotkeysRefused) throw new Error("Shift+F8 is taken by another program.");
        if (cmd === "capture_hotkeys_start" && shell.legacyAnswer) return shell.legacyAnswer.value;
        if (cmd === "capture_hotkeys_start") return { key: !shell.keyTaken, clicks: args?.clicks === true && !shell.clicksRefused };
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
/** Gates for the next `POST /api/captures` calls, taken in order: each still waits on its own (a capture held in flight). */
let stillGates: Promise<void>[] = [];
/** When set, `POST /api/captures` fails with it once. */
let stillFailure: Error | null = null;

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
function setSelect(el: HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("change", { bubbles: true }));
}
/** The dialog with "Every step" chosen (A, B and C), then Start. */
async function openEveryAndStart() {
  await act(async () => { q("[data-capture-steps]")!.click(); });
  await act(async () => { setSelect(q("[data-capture-which]") as HTMLSelectElement, "every"); });
  await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
  await settle(60);
}
const attaches = () => posts().filter(([p]) => /\/steps\/[^/]+\/picture$/.test(p)).map(([p]) => /\/steps\/([^/]+)\/picture$/.exec(p)![1]);
/** The body of the last guided-capture start row posted. */
const startRow = () => [...posts()].reverse().find(([p, b]) => p.endsWith("/guided-capture") && (b as { event?: string }).event === "start")?.[1];

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
  stillGates = [];
  stillFailure = null;
  vi.mocked(api.post).mockImplementation(async (path: string, body: unknown) => {
    if (path === "/api/captures") {
      // The id and the failure are taken in REQUEST order; the gate only decides when the answer comes.
      stillId += 1;
      const id = `cap0000000${stillId.toString().padStart(2, "0")}`;
      const fail = stillFailure;
      stillFailure = null;
      const gate = stillGates.shift();
      if (gate) await gate;
      if (fail) throw fail;
      const b = body as { x: number; y: number; width: number; height: number };
      return { id, x: b.x, y: b.y, width: b.width, height: b.height } as never;
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
    // Capture on click is on by default: the start row says so.
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "start", kind: "window", cursor: true, clicks: true }))).toBe(true);
    const bar = invoked("capture_bar_open");
    expect(bar.length).toBe(1);
    expect(bar[0].args).toMatchObject({ mode: "steps", width: 600, height: 112 });
    expect((bar[0].args!.x as number) + 600 + 12).toBe(1920);
    // The key is taken WITH the click hook (steps mode only: the shell's rule).
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps", clicks: true });
    expect(shell.windowCalls).toEqual(["minimize"]);
    expect(Object.keys(shell.handlers).sort()).toEqual(["capture:control", "capture:step-click", "capture:step-key"]);
    // The listeners are up BEFORE the bar opens: the bar's hello (and a key press, a click) can arrive the moment it is.
    const order = shell.invokes.map((i) => i.cmd);
    expect(order.indexOf("listen:capture:control")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-key")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-click")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(lastTold()).toMatchObject({ mode: "steps", phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text bbbbbbbbbbbb", instruction: "Do bbbbbbbbbbbb", remaining: 2, canBack: false, canCapture: true, message: null, clicks: true });
    expect(q("[data-capture-line]")!.textContent).toContain("Click to capture · Shift+F8 captures without a click");
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
    // The bar's Capture button: no pointer, the front-most window, no hotspot - and, another size than the first
    // still (the run's frame, R7), sent with the frame for the server to draw it on.
    await fire("capture:control", { action: "capture" });
    const second = posts().filter(([p]) => p === "/api/captures")[1];
    expect(second[1]).toMatchObject({ x: 300, y: 300, width: 400, height: 200, name: "Tour · step 3" });
    const attachC = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)!;
    expect(attachC[1]).toEqual({ capture_id: cid(2), hotspot: null, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ phase: "finished", label: "All steps visited", remaining: 0, canCapture: false });
    // Done: the bar closed, the key given back, the window restored, the done row, the builder reloaded.
    const gets = vi.mocked(api.get).mock.calls.length;
    await fire("capture:control", { action: "done" });
    await settle();
    expect(invoked("capture_bar_close").length).toBe(1);
    expect(invoked("capture_hotkeys_stop").length).toBe(1);
    expect(shell.windowCalls).toEqual(["minimize", "unminimize", "setFocus"]);
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 0 }))).toBe(true);
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
    // With the clicks on (the default), the line says a click will do too (W2b-1).
    expect(q("[data-capture-message]")!.textContent).toBe("No window is under the pointer: point at the window and click, or press Shift+F8.");
    // D1: the refusal reaches the bar too, in the one payload after it, with the capture still ready; the next payload clears it.
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: "No window is under the pointer: point at the window and click, or press Shift+F8." });
    await fire("capture:step-key", { x: 150, y: 150 });
    const attach = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)!;
    expect(attach[1]).toEqual({ capture_id: cid(1), hotspot: { x: 0, y: 20, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", message: null });
    // A still the server refuses: the same way to the bar, cleared by the next payload (a skip here).
    stillFailure = new Error("The grab timed out.");
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "F8", shiftKey: true, bubbles: true, cancelable: true })); await new Promise((r) => setTimeout(r, 30)); });
    expect(q("[data-capture-message]")!.textContent).toBe("The step could not be captured: The grab timed out.");
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", message: "The step could not be captured: The grab timed out." });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)).toBeUndefined();
    // The page's key: no pointer, so the front-most window (the dialog, another size than the first still: the frame
    // travels with it, R7) and no hotspot. (The failed grab above took the second still id.)
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "F8", shiftKey: true, bubbles: true, cancelable: true })); await new Promise((r) => setTimeout(r, 30)); });
    const attachC = posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)!;
    expect(attachC[1]).toEqual({ capture_id: cid(3), hotspot: null, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ phase: "finished", message: null });
    await act(async () => { q("[data-capture-done]")!.click(); });
    await settle();
    // B was skipped and then captured after Back: not skipped (F5) - two steps pictured, none skipped.
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 0 }))).toBe(true);
    expect(status()).toBe("Captured 2 steps.");
  });

  it("a click is a step (W2b): the window under the point, the hotspot round it, through the same path as the key; a right click as a left one; nothing after the last step", async () => {
    await mount();
    await openAndStart();
    // A left click on Spoon (100..1300 x 100..900), outside the dialog on top of it.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(invoked("capture_windows").length).toBe(1);
    expect(posts().find(([p]) => p === "/api/captures")![1]).toEqual({ x: 100, y: 100, width: 1200, height: 800, cursor: true, kind: "window", name: "Tour · step 2" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    expect(status()).toBe("Saved");
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", clicks: true, message: null });
    // A right click on the dialog on top (300..700 x 300..500): its window, the hotspot in its pixels (clamped at the
    // left edge), and the run's frame - the first still's 1200 x 800 - for the server to draw the smaller still on (R7).
    await fire("capture:step-click", { x: 350, y: 350, button: "right" });
    expect(posts().filter(([p]) => p === "/api/captures")[1][1]).toMatchObject({ x: 300, y: 300, width: 400, height: 200, name: "Tour · step 3" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)![1]).toEqual({ capture_id: cid(2), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ phase: "finished", label: "All steps visited", clicks: true });
    // Every step visited: a click takes nothing more; a payload that is no click is ignored.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await fire("capture:step-click", { x: 900, y: 700 });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    await fire("capture:control", { action: "done" });
    await settle();
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 2 }))).toBe(true);
    expect(status()).toBe("Captured 2 steps.");
  });

  it("a click while a still is being taken is not lost: its still is taken at once and the attaches land in click order; a third is too fast (F8)", async () => {
    await mount();
    await openEveryAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 1 of 3" });
    let openFirst!: () => void, openSecond!: () => void;
    stillGates = [new Promise<void>((r) => { openFirst = r; }), new Promise<void>((r) => { openSecond = r; })];
    // The first click: step A claimed, its still in flight, the bar moved on to step 2 and "Capturing…".
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    expect(lastTold()).toMatchObject({ phase: "capturing", canCapture: false, label: "Step 2 of 3" });
    // The second click while the first still is held: step B claimed, its still taken AT ONCE, beside the first.
    await fire("capture:step-click", { x: 350, y: 350, button: "right" });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    expect(posts().filter(([p]) => p === "/api/captures")[1][1]).toMatchObject({ name: "Tour · step 2", x: 300, y: 300 });
    expect(lastTold()).toMatchObject({ phase: "capturing", label: "Step 3 of 3", message: null });
    // A third while two are in flight: dropped, the bar says so; the key is held to the same rule.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    expect(lastTold()).toMatchObject({ phase: "capturing", label: "Step 3 of 3", message: "Too fast: wait for the still." });
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    expect(attaches()).toEqual([]);
    // The SECOND still lands first: nothing is attached yet - the chain waits for the first.
    await act(async () => { openSecond(); await new Promise((r) => setTimeout(r, 40)); });
    expect(attaches()).toEqual([]);
    expect(lastTold()).toMatchObject({ phase: "capturing" });
    // The first lands: A then B, in click order, each with its own still and hotspot; the bar back to Capture at step 3.
    await act(async () => { openFirst(); await new Promise((r) => setTimeout(r, 60)); });
    expect(attaches()).toEqual([A, B]);
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${A}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(2), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", canCapture: true, message: null });
    expect(status()).toBe("Saved");
    // Step C by the key: the run ends; the counts say three landed, two by click.
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(lastTold()).toMatchObject({ phase: "finished" });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 3, skipped: 0, captures: 3, by_click: 2 }))).toBe(true);
    expect(status()).toBe("Captured 3 steps.");
  });

  it("a still that fails while another is in flight leaves its step without its new picture and a STANDING line naming it, until it gets one (F8, G2, H2)", async () => {
    // "Every step": A HAS a picture, so the line says it kept it (H2).
    const missedA = "Step 1 kept its old picture: press Back to take it again.";
    await mount();
    await openEveryAndStart();
    let openFirst!: () => void;
    stillGates = [new Promise<void>((r) => { openFirst = r; })];
    stillFailure = new Error("The grab timed out.");
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    await act(async () => { openFirst(); await new Promise((r) => setTimeout(r, 60)); });
    // The first failed (step A keeps the picture it had: nothing attached), the second landed on B; the bar carried
    // the failure and is ready at C - a later claim was in flight, so the failed step is left for Back, not rewound to.
    expect(attaches()).toEqual([B]);
    expect(shell.told.some((t) => ((t as { message: string | null }).message || "").includes("The step could not be captured: The grab timed out."))).toBe(true);
    expect(server.steps.find((s) => s.id === A)!.source).toBeNull();
    expect(server.steps.find((s) => s.id === B)!.source).toEqual({ capture_id: cid(2), annotated: false });
    expect(q(`[data-step="${B}"] img`)).not.toBeNull();
    // B's landing does NOT erase it (G2): the bar and the builder's line name the step left without a picture.
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", message: missedA });
    expect(q("[data-capture-message]")!.textContent).toBe(missedA);
    // A refusal meanwhile - a click on no window - shows its own line beside the standing one, on the builder as on the
    // bar; it never replaces it (n-a). No step is claimed.
    await fire("capture:step-click", { x: 5, y: 5, button: "left" });
    expect(q("[data-capture-message]")!.textContent).toContain("No window is under the pointer");
    expect(q("[data-capture-message]")!.textContent).toContain(missedA);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", message: expect.stringContaining(missedA) });
    expect(lastTold()).toMatchObject({ message: expect.stringContaining("No window is under the pointer") });
    // It stands through Back and Back again.
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", message: missedA });
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 1 of 3", canBack: false, message: missedA });
    // A key press on A: A lands, and the line goes from the bar and the builder.
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(attaches()).toEqual([B, A]);
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", message: null });
    expect(q("[data-capture-message]")).toBeNull();
  });

  it("the standing no-picture line ends with the session (G2)", async () => {
    await mount();
    await openEveryAndStart();
    let openFirst!: () => void;
    stillGates = [new Promise<void>((r) => { openFirst = r; })];
    stillFailure = new Error("The grab timed out.");
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await act(async () => { openFirst(); await new Promise((r) => setTimeout(r, 60)); });
    expect(q("[data-capture-message]")!.textContent).toBe("Step 1 kept its old picture: press Back to take it again.");
    await fire("capture:control", { action: "done" });
    await settle();
    expect(q("[data-capture-message]")).toBeNull();
    expect(status()).toBe("Captured 1 step.");
  });

  it("in the desktop app the builder's line for a step without a picture names the click too (H4)", async () => {
    await mount();
    await act(async () => { (q(`[data-step="${B}"]`) as HTMLElement).click(); });
    expect(q("[data-hotspot-line]")!.textContent).toBe(
      "No picture yet, so no hotspot: the hotspot comes with the capture (Capture steps…: click through the step, or press Shift+F8 - it goes round the click or the pointer).",
    );
  });

  it("a step WITHOUT a picture whose still fails beside a later one is named \"has no picture\" (G2, H2)", async () => {
    await mount();
    await openEveryAndStart();
    // Skip A (it has a picture): the bar is on B, which has none.
    await fire("capture:control", { action: "skip" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3" });
    let openFirst!: () => void;
    stillGates = [new Promise<void>((r) => { openFirst = r; })];
    stillFailure = new Error("The grab timed out.");
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await act(async () => { openFirst(); await new Promise((r) => setTimeout(r, 60)); });
    expect(attaches()).toEqual([C]);
    expect(lastTold()).toMatchObject({ phase: "finished", message: "Step 2 has no picture: press Back to take it again." });
    expect(q("[data-capture-message]")!.textContent).toBe("Step 2 has no picture: press Back to take it again.");
  });

  it("three presses inside one window-list round trip: the first two are taken, the third is too fast (G3)", async () => {
    await mount();
    await openEveryAndStart();
    let openWindows!: () => void;
    shell.windowsGate = new Promise<void>((r) => { openWindows = r; });
    // All three pass the first guard (nothing in flight yet): each waits on the window list.
    await act(async () => {
      for (const at of [{ x: 900, y: 700 }, { x: 350, y: 350 }, { x: 950, y: 750 }]) shell.handlers["capture:step-click"]?.({ payload: { ...at, button: "left" } });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(invoked("capture_windows").length).toBe(3);
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    // The list answers: the first two claim steps and fire their stills; the third meets the SECOND guard.
    shell.windowsGate = null;
    await act(async () => { openWindows(); await new Promise((r) => setTimeout(r, 60)); });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    expect(shell.told.some((t) => (t as { message: string | null }).message === "Too fast: wait for the still.")).toBe(true);
    expect(attaches()).toEqual([A, B]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3" });
  });

  it("in Full screen a click off the captured monitor is no step and the bar says so; on it, a step (W2b-2, G6)", async () => {
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='screen']") as HTMLButtonElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", clicks: true, message: null });
    // Off the monitor (0..1920 x 0..1080): no still, the line on the bar, the capture still ready.
    await fire("capture:step-click", { x: 2500, y: 500, button: "left" });
    await fire("capture:step-click", { x: 500, y: 1080, button: "right" });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", canCapture: true, message: "Click on the captured screen to capture this step." });
    // On it - any application there: the monitor's still, the hotspot round the click; the line gone.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().find(([p]) => p === "/api/captures")![1]).toEqual({ x: 0, y: 0, width: 1920, height: 1080, cursor: true, kind: "screen", name: "Tour · step 2" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 820, y: 670, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", message: null });
    // The key is never "outside": Shift+F8 with the pointer off the monitor takes the monitor's still, with no hotspot.
    await fire("capture:step-key", { x: 2500, y: 500 });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)![1]).toEqual({ capture_id: cid(2), hotspot: null });
  });

  it("a run that starts on a small window converges on the larger one: the larger still keeps its size and becomes the frame (F7)", async () => {
    await mount();
    await openEveryAndStart();
    // Step A: the dialog (400 x 200) - the first still, the frame for now.
    await fire("capture:step-click", { x: 350, y: 350, button: "left" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${A}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 0, y: 20, width: 160, height: 60 } });
    // Step B: Spoon (1200 x 800), larger than the frame: attached at its own size, no frame sent, and the frame from here on.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(2), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    // Step C: the dialog again, which fits Spoon's frame: drawn on it.
    await fire("capture:step-click", { x: 350, y: 350, button: "right" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)![1]).toEqual({ capture_id: cid(3), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ phase: "finished" });
  });

  it("\"skipped\" counts the distinct steps skipped and not captured since (F5)", async () => {
    await mount();
    await openAndStart();
    // Skip B, Back, skip B again, skip C: two skipped; Back to C and capture it: one.
    await fire("capture:control", { action: "skip" });
    await fire("capture:control", { action: "back" });
    await fire("capture:control", { action: "skip" });
    await fire("capture:control", { action: "skip" });
    expect(lastTold()).toMatchObject({ phase: "finished", remaining: 0 });
    await fire("capture:control", { action: "back" });
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 1, skipped: 1, captures: 1, by_click: 1 }))).toBe(true);
    expect(status()).toBe("Captured 1 step, skipped 1.");
  });

  it("for a region a click outside the frame is no step and the bar says so until the next payload; inside it, a step (W2b)", async () => {
    hoisted.region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", clicks: true, message: null });
    // Outside the frame (x past 310, or y past 220): no capture, the line on the bar, the capture still ready.
    await fire("capture:step-click", { x: 500, y: 100, button: "left" });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", canCapture: true, message: "Click inside the frame to capture this step." });
    await fire("capture:step-click", { x: 100, y: 221, button: "right" });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    // Inside: the region's still, the hotspot round the point in the region's pixels - and the line is gone.
    await fire("capture:step-click", { x: 100, y: 100, button: "left" });
    expect(posts().find(([p]) => p === "/api/captures")![1]).toEqual({ x: 10, y: 20, width: 300, height: 200, cursor: true, kind: "region", name: "Tour · step 2" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 10, y: 50, width: 160, height: 60 } });
    expect(shell.told.every((t) => (t as { message: string | null }).message !== "Click inside the frame to capture this step." || (t as { phase: string }).phase === "ready")).toBe(true);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", message: null });
    // A key press is never "outside": the pointer outside the frame gives no hotspot, as before.
    await fire("capture:step-key", { x: 500, y: 100 });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)![1]).toEqual({ capture_id: cid(2), hotspot: null });
  });

  it("the tick off is the one key press per step: no hook asked for, no click listened to, the row and the bar say so (W2b)", async () => {
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    const tick = q("[data-capture-clicks]") as HTMLInputElement;
    expect(tick.checked).toBe(true);
    await act(async () => { tick.click(); });
    expect(tick.checked).toBe(false);
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps", clicks: false });
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "start", kind: "window", cursor: true, clicks: false }))).toBe(true);
    expect(Object.keys(shell.handlers).sort()).toEqual(["capture:control", "capture:step-key"]);
    expect(lastTold()).toMatchObject({ phase: "ready", clicks: false });
    expect(q("[data-capture-line]")!.textContent).toContain("Shift+F8 captures; the bar has Capture");
    expect(q("[data-capture-line]")!.textContent).not.toContain("Click to capture");
    // A click arriving anyway (an older page, a stray event) finds no listener: nothing happens.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    // The no-window line keeps its key-only wording when the clicks are off (W2b-1).
    await fire("capture:step-key", { x: 50, y: 50 });
    expect(q("[data-capture-message]")!.textContent).toBe("No window is under the pointer: point at the window and press Shift+F8.");
  });

  it("Shift+F8 held by another program never switches the clicks off: the bar says the key is taken and a click still captures (H1)", async () => {
    shell.keyTaken = true;
    await mount();
    await openAndStart();
    const keyTaken = "Shift+F8 is taken by another program: click to capture, or use Capture on the bar.";
    // The line names the key alone; the clicks are live, so the "Click to capture" hint stays, on the bar and the builder.
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: keyTaken, clicks: true });
    expect(q("[data-capture-message]")!.textContent).toBe(keyTaken);
    expect(q("[data-capture-line]")!.textContent).toContain("Click to capture · Shift+F8 captures without a click");
    // A click is a step, and after it lands the line still stands on the bar and the builder.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", message: keyTaken, clicks: true });
    expect(q("[data-capture-message]")!.textContent).toBe(keyTaken);
    // The start row says ", on click" from what is LIVE (n-2): here the clicks are.
    expect(startRow()).toEqual({ event: "start", kind: "window", cursor: true, clicks: true });
    // Done ends the session, and the builder's red line goes with it (S-1): it would tell the user to click.
    await fire("capture:control", { action: "done" });
    await settle();
    expect(status()).toBe("Captured 1 step.");
    expect(q("[data-capture-message]")).toBeNull();
    expect(q("[data-capture-line]")).toBeNull();
  });

  it("the key up and the click hook refused: the bar says the clicks are off and the hint goes; the key still captures (H1)", async () => {
    shell.clicksRefused = true;
    await mount();
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toBe(CLICKS_OFF);
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: CLICKS_OFF, clicks: false });
    expect(q("[data-capture-line]")!.textContent).not.toContain("Click to capture");
    expect(q("[data-capture-line]")!.textContent).toContain("Shift+F8 captures; the bar has Capture");
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", message: CLICKS_OFF, clicks: false });
    // The tick was on, but no click is a step: the start row writes no ", on click" (n-2).
    expect(startRow()).toEqual({ event: "start", kind: "window", cursor: true, clicks: false });
    // Done: the builder's line goes with the session (S-1).
    await fire("capture:control", { action: "done" });
    await settle();
    expect(q("[data-capture-message]")).toBeNull();
    expect(q("[data-capture-line]")).toBeNull();
  });

  it("both off with the tick on: the bar and the builder carry ONE line naming both, the hint goes (H1)", async () => {
    shell.keyTaken = true;
    shell.clicksRefused = true;
    await mount();
    await openAndStart();
    const both = "Shift+F8 is taken by another program and clicks could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).";
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: both, clicks: false });
    expect(q("[data-capture-message]")!.textContent).toBe(both);
    expect(q("[data-capture-message]")!.textContent).not.toContain(KEY_TAKEN);
    expect(q("[data-capture-message]")!.textContent).not.toContain(CLICKS_OFF);
    expect(q("[data-capture-line]")!.textContent).not.toContain("Click to capture");
    // The bar's own Capture still works.
    await fire("capture:control", { action: "capture" });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
  });

  it("the tick off and the key taken: the key-only line, and no clicks (H1)", async () => {
    shell.keyTaken = true;
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-clicks]") as HTMLInputElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps", clicks: false });
    expect(lastTold()).toMatchObject({ phase: "ready", message: KEY_TAKEN, clicks: false });
    expect(q("[data-capture-message]")!.textContent).toBe(KEY_TAKEN);
  });

  it("an older shell's answer is read for what it can do: 0.17.0's nothing means no clicks; W2b's true means both (H1)", async () => {
    // The 0.17.0 shell answers nothing and has no click hook (the updater refreshes the page, never the shell).
    shell.legacyAnswer = { value: undefined };
    await mount();
    await openAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", message: CLICKS_OFF, clicks: false });
    expect(startRow()).toEqual({ event: "start", kind: "window", cursor: true, clicks: false });
    await fire("capture:control", { action: "done" });
    await settle();
    // The W2b shell before H1 answered true when everything asked was up.
    shell.legacyAnswer = { value: true };
    shell.told.length = 0;
    await openAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", message: null, clicks: true });
  });

  it("\"Captured N steps\" counts the distinct steps pictured; the done row keeps the captures and those by click (W2b, R6)", async () => {
    await mount();
    await openAndStart();
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3" });
    // Back to step 2 and a retake by the key: two captures, one step pictured.
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3" });
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`).length).toBe(2);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3" });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(posts().some(([p, b]) => p === `/api/walkthroughs/${WID}/guided-capture` && JSON.stringify(b) === JSON.stringify({ event: "done", captured: 1, skipped: 0, captures: 2, by_click: 1 }))).toBe(true);
    expect(status()).toBe("Captured 1 step.");
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
