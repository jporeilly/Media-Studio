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
 *   the bar as a transient message (D1); "Captured N steps" counts distinct steps and the done row the captures;
 * - (W2d) the step is yours until Next, by default: two clicks on one step are two stills of the one step, the last
 *   stands and the first leaves Captures; the one button reads Next or Skip; Shift+F9 both ways; the still after the
 *   click waits the settle time with the step claimed at once; a typed pause lands on the step's last click point in
 *   next mode and on the pointer in capture mode; the thumbnail travels in the payload; the rows say what is live.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { CLICKS_OFF, HOOKS_OFF, KEY_TAKEN, RECORDING_IN_PROGRESS, SETTLE_MS, TYPING_OFF, TYPING_PAUSE_MS } from "../lib/guided";
import { thumbnailOf } from "../lib/thumbnail";
import type { Step, Walkthrough } from "../lib/walkthrough";
import WalkthroughBuilderPage from "./WalkthroughBuilder";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({ recorder: "idle" as string, region: null as unknown }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), blob: vi.fn() } };
});
vi.mock("../lib/useCaptureAvailable", () => ({ useCaptureAvailable: () => true }));
// The thumbnail (R4) is one function over the canvas, which jsdom has not: stubbed, a fixed data URL.
vi.mock("../lib/thumbnail", () => ({ THUMB_WIDTH: 320, thumbnailOf: vi.fn(async () => "data:image/jpeg;base64,thumb") }));
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
/** The two windows the faked shell lists, as frames a typed event carries (F1: the foreground window's visible frame). */
const SPOON = { x: 100, y: 100, width: 1200, height: 800 };
const DIALOG = { x: 300, y: 300, width: 400, height: 200 };
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
  /** Another program holds Shift+F9 (F3: the two keys are registered one by one): the shell answers `next: false`. */
  nextTaken: boolean;
  /** The click hook could not be installed: the shell answers `clicks: false`. */
  clicksRefused: boolean;
  /** The keyboard hook could not be installed (W2d): the shell answers `typing: false`. */
  typingRefused: boolean;
  /** When set, `capture_hotkeys_start` answers this instead (an older shell: `undefined` from 0.17.0, a boolean from W2b before H1). */
  legacyAnswer: { value: unknown } | null;
  /** When set, `capture_windows` waits on it: presses that arrive inside one window-list round trip. */
  windowsGate: Promise<void> | null;
}
let shell: Shell;

function installShell() {
  shell = {
    invokes: [], handlers: {}, told: [], windowCalls: [], hotkeysRefused: false, barRefused: false, barNotHidden: false, keyTaken: false, nextTaken: false,
    clicksRefused: false, typingRefused: false, legacyAnswer: null, windowsGate: null,
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
        if (cmd === "capture_hotkeys_start") {
          return {
            key: !shell.keyTaken, next: !shell.nextTaken, clicks: args?.clicks === true && !shell.clicksRefused, typing: args?.typing === true && !shell.typingRefused,
          };
        }
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
/**
 * When the session moves on (W2d, R1): the dialog's default is at Next; the 0.18.0 flows these tests pin - the bar moving
 * on at each capture - choose "after each capture" in the open dialog.
 */
type Advance = "next" | "capture";
async function chooseAdvance(advance: Advance) {
  if (advance === "capture") await act(async () => { (q("[data-capture-advance='capture']") as HTMLInputElement).click(); });
}
/** The dialog with "Every step" chosen (A, B and C), then Start. */
async function openEveryAndStart(advance: Advance = "capture") {
  await act(async () => { q("[data-capture-steps]")!.click(); });
  await act(async () => { setSelect(q("[data-capture-which]") as HTMLSelectElement, "every"); });
  await chooseAdvance(advance);
  await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
  await settle(60);
}
const attaches = () => posts().filter(([p]) => /\/steps\/[^/]+\/picture$/.test(p)).map(([p]) => /\/steps\/([^/]+)\/picture$/.exec(p)![1]);
/** The body of the last guided-capture start row posted. */
const startRow = () => [...posts()].reverse().find(([p, b]) => p.endsWith("/guided-capture") && (b as { event?: string }).event === "start")?.[1];
/** The body of the last guided-capture done row posted. */
const doneRow = () => [...posts()].reverse().find(([p, b]) => p.endsWith("/guided-capture") && (b as { event?: string }).event === "done")?.[1];
/** The still ids deleted from Captures (a superseded still, R1). */
const deleted = () => vi.mocked(api.delete).mock.calls.map(([p]) => p);
/** The start row as the hook writes it: what is LIVE, and the dialog's choices. */
const start = (over: Record<string, unknown> = {}) => ({ event: "start", kind: "window", cursor: true, clicks: true, typing: true, settle_ms: 0, advance: "capture", ...over });

async function openAndStart(advance: Advance = "capture") {
  await act(async () => { q("[data-capture-steps]")!.click(); });
  expect(q("[role=dialog]")!.textContent).toContain("Capture steps");
  await chooseAdvance(advance);
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
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.delete).mockResolvedValue(undefined as never);
  vi.mocked(api.blob).mockReset();
  vi.mocked(thumbnailOf).mockReset();
  vi.mocked(thumbnailOf).mockImplementation(async () => "data:image/jpeg;base64,thumb");
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
  vi.useRealTimers();
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("the guided capture", () => {
  it("Start saves first, writes the start row, opens the bar in its second mode, takes the keys, minimises the window and tells the bar the first step without a picture", async () => {
    await mount();
    expect(q("[data-capture-steps]")).not.toBeNull();
    // A dirty document: Start saves it first (as Play does).
    await act(async () => { setValue(document.querySelector("[data-step-text]") as HTMLTextAreaElement, "Edited"); });
    expect(status()).toBe("Unsaved changes");
    await openAndStart();
    expect(vi.mocked(api.put)).toHaveBeenCalledTimes(1);
    // Capture on click and typing are on by default: the start row says so, and which mode was chosen (here: after each capture).
    expect(startRow()).toEqual(start());
    const bar = invoked("capture_bar_open");
    expect(bar.length).toBe(1);
    expect(bar[0].args).toMatchObject({ mode: "steps", width: 760, height: 112 });
    expect((bar[0].args!.x as number) + 760 + 12).toBe(1920);
    // The keys are taken WITH the click hook and the keyboard hook (steps mode only: the shell's rule).
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps", clicks: true, typing: true });
    expect(shell.windowCalls).toEqual(["minimize"]);
    expect(Object.keys(shell.handlers).sort()).toEqual(["capture:control", "capture:step-click", "capture:step-key", "capture:step-next", "capture:step-typed"]);
    // The listeners are up BEFORE the bar opens: the bar's hello (and a key press, a click) can arrive the moment it is.
    const order = shell.invokes.map((i) => i.cmd);
    expect(order.indexOf("listen:capture:control")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-key")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-click")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-next")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(order.indexOf("listen:capture:step-typed")).toBeLessThan(order.indexOf("capture_bar_open"));
    expect(lastTold()).toMatchObject({
      mode: "steps", phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text bbbbbbbbbbbb", instruction: "Do bbbbbbbbbbbb", remaining: 2, canBack: false, canCapture: true,
      message: null, clicks: true, typing: true, stillsThisStep: 0, lastStill: null,
    });
    expect(q("[data-capture-line]")!.textContent).toContain("Click to capture · Shift+F8 captures without a click · typing captures a second after you stop; the bar has Capture, Next, Back and Done.");
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
    // A refusal of the whole start (a shell before H1, or the other set held): every key and hook off - the one line for
    // Shift+F8 and both hooks, then Shift+F9's own (F3), then the bar line.
    expect(q("[data-capture-message]")!.textContent).toContain("Shift+F8 is taken by another program and clicks and typing could not be captured");
    expect(q("[data-capture-message]")!.textContent).toContain("Shift+F9 is taken by another program: use Next on the bar.");
    const told = lastTold() as { message: string; canCapture: boolean };
    expect(told.canCapture).toBe(true);
    expect(told.message).toContain("Shift+F8 is taken by another program and clicks and typing could not be captured");
    expect(told.message).toContain("Shift+F9 is taken");
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
    expect(doneRow()).toEqual({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 0, by_typing: 0 });
    expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(gets);
    expect(status()).toBe("Captured 2 steps.");
    expect(q("[data-capture-line]")).toBeNull();
    expect(Object.keys(shell.handlers)).toEqual([]);
  });

  it("Skip leaves a step for later, Back returns to it, and a pointer outside the window gives no hotspot; the page's own Shift+F8 captures without a pointer", async () => {
    await mount();
    await openAndStart();
    await fire("capture:control", { action: "next" });
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
    expect(doneRow()).toEqual({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 0, by_typing: 0 });
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
    expect(doneRow()).toEqual({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 2, by_typing: 0 });
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
    expect(doneRow()).toEqual({ event: "done", captured: 3, skipped: 0, captures: 3, by_click: 2, by_typing: 0 });
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
    await fire("capture:control", { action: "next" });
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
    await chooseAdvance("capture");
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
    // Skip B, Back, skip B again, skip C: two skipped; Back to C and capture it: one. (The bar's action is "next": the
    // one button reads Skip for a step with no still and skips it, R1; an older bar's "skip" is read the same.)
    await fire("capture:control", { action: "next" });
    await fire("capture:control", { action: "back" });
    await fire("capture:control", { action: "skip" });
    await fire("capture:control", { action: "next" });
    expect(lastTold()).toMatchObject({ phase: "finished", remaining: 0 });
    await fire("capture:control", { action: "back" });
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(doneRow()).toEqual({ event: "done", captured: 1, skipped: 1, captures: 1, by_click: 1, by_typing: 0 });
    expect(status()).toBe("Captured 1 step, skipped 1.");
  });

  it("for a region a click outside the frame is no step and the bar says so until the next payload; inside it, a step (W2b)", async () => {
    hoisted.region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await chooseAdvance("capture");
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
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps", clicks: false, typing: true });
    // No clicks: no still after a click either (settle_ms 0 whatever the tick); the dialog's default mode, at Next.
    expect(startRow()).toEqual(start({ clicks: false, advance: "next" }));
    expect(Object.keys(shell.handlers).sort()).toEqual(["capture:control", "capture:step-key", "capture:step-next", "capture:step-typed"]);
    expect(lastTold()).toMatchObject({ phase: "ready", clicks: false, typing: true });
    expect(q("[data-capture-line]")!.textContent).toContain("Shift+F8 captures · typing captures a second after you stop; the bar has Capture, Next, Back and Done.");
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
    expect(startRow()).toEqual(start());
    // Done ends the session, and the builder's red line goes with it (S-1): it would tell the user to click.
    await fire("capture:control", { action: "done" });
    await settle();
    expect(status()).toBe("Captured 1 step.");
    expect(q("[data-capture-message]")).toBeNull();
    expect(q("[data-capture-line]")).toBeNull();
    // F3: the keys are registered one by one. Shift+F9 held (Snagit's pause) alone: its own line, and Shift+F8 still captures;
    // the shell's step-next event never comes, but the bar's Next does. Both held: one line naming both.
    shell.keyTaken = false;
    shell.nextTaken = true;
    shell.told.length = 0;
    await openAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", message: "Shift+F9 is taken by another program: use Next on the bar.", clicks: true });
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(lastTold()).toMatchObject({ label: "All steps visited", message: "Shift+F9 is taken by another program: use Next on the bar." });
    await fire("capture:control", { action: "done" });
    await settle();
    shell.keyTaken = true;
    shell.told.length = 0;
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { setSelect(q("[data-capture-which]") as HTMLSelectElement, "every"); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(lastTold()).toMatchObject({ phase: "ready", message: "Shift+F8 and Shift+F9 are taken by another program: click to capture, or use Capture and Next on the bar.", clicks: true });
  });

  it("the key up and the click hook refused: the bar says the clicks are off and the hint goes; the key still captures (H1)", async () => {
    shell.clicksRefused = true;
    await mount();
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toBe(CLICKS_OFF);
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: CLICKS_OFF, clicks: false, typing: true });
    expect(q("[data-capture-line]")!.textContent).not.toContain("Click to capture");
    expect(q("[data-capture-line]")!.textContent).toContain("Shift+F8 captures · typing captures a second after you stop; the bar has Capture, Next, Back and Done.");
    await fire("capture:step-key", { x: 900, y: 700 });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", message: CLICKS_OFF, clicks: false });
    // The tick was on, but no click is a step: the start row writes no ", on click" (n-2), and no still after a click.
    expect(startRow()).toEqual(start({ clicks: false }));
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
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: both, clicks: false, typing: true });
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
    expect(invoked("capture_hotkeys_start")[0].args).toEqual({ keys: "steps", clicks: false, typing: true });
    expect(lastTold()).toMatchObject({ phase: "ready", message: KEY_TAKEN, clicks: false });
    expect(q("[data-capture-message]")!.textContent).toBe(KEY_TAKEN);
  });

  it("an older shell's answer is read for what it can do: 0.17.0's nothing means no hooks; W2b's true means the clicks; H1's {key, clicks} no typing (H1, W2d)", async () => {
    // The 0.17.0 shell answers nothing and has no hook at all (the updater refreshes the page, never the shell): both hooks off, one line.
    shell.legacyAnswer = { value: undefined };
    await mount();
    await openAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", message: HOOKS_OFF, clicks: false, typing: false });
    expect(startRow()).toEqual(start({ clicks: false, typing: false }));
    await fire("capture:control", { action: "done" });
    await settle();
    // The W2b shell before H1 answered true when everything asked was up - it had no keyboard hook: the typing off.
    shell.legacyAnswer = { value: true };
    shell.told.length = 0;
    await openAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", message: TYPING_OFF, clicks: true, typing: false });
    expect(q("[data-capture-line]")!.textContent).toContain("Click to capture · Shift+F8 captures without a click; the bar has");
    await fire("capture:control", { action: "done" });
    await settle();
    // The H1 shell (0.18.0): {key, clicks} - the typing off, the clicks as it says.
    shell.legacyAnswer = { value: { key: true, clicks: true } };
    shell.told.length = 0;
    await openAndStart();
    expect(lastTold()).toMatchObject({ phase: "ready", message: TYPING_OFF, clicks: true, typing: false });
    expect(startRow()).toEqual(start({ typing: false }));
  });

  it("the keyboard hook refused: the bar says typing is off and the hint loses its clause; both hooks refused: one line (W2d, R3)", async () => {
    shell.typingRefused = true;
    await mount();
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toBe(TYPING_OFF);
    expect(lastTold()).toMatchObject({ phase: "ready", canCapture: true, message: TYPING_OFF, clicks: true, typing: false });
    expect(q("[data-capture-line]")!.textContent).toContain("Click to capture · Shift+F8 captures without a click; the bar has Capture, Next, Back and Done.");
    expect(q("[data-capture-line]")!.textContent).not.toContain("typing captures");
    expect(startRow()).toEqual(start({ typing: false }));
    await fire("capture:control", { action: "done" });
    await settle();
    shell.clicksRefused = true;
    await openAndStart();
    expect(q("[data-capture-message]")!.textContent).toBe(HOOKS_OFF);
    expect(lastTold()).toMatchObject({ message: HOOKS_OFF, clicks: false, typing: false });
    expect(startRow()).toEqual(start({ clicks: false, typing: false }));
  });

  it("the dialog's default holds the step until Next (R1): two clicks are two stills of the one step, the last stands and the first leaves Captures, the bar says \"2 stills this step\", the one button reads Next, Next skips nothing, Skip reads Skip with no still", async () => {
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    // The dialog's defaults: at Next, the still at the press, typing on.
    expect((q("[data-capture-advance='next']") as HTMLInputElement).checked).toBe(true);
    expect((q("[data-capture-advance='capture']") as HTMLInputElement).checked).toBe(false);
    expect((q("[data-capture-settle]") as HTMLInputElement).checked).toBe(false);
    expect((q("[data-capture-typing]") as HTMLInputElement).checked).toBe(true);
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(startRow()).toEqual(start({ advance: "next" }));
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", stillsThisStep: 0 });
    expect(q("[data-capture-next]")!.textContent).toBe("Skip");
    // The first click: B's still and hotspot, and the bar STAYS on step 2 - with one still landed, the button reads Next.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", canCapture: true, stillsThisStep: 1, message: null });
    expect(q("[data-capture-label]")!.textContent).toBe("Step 2 of 3");
    expect(q("[data-capture-next]")!.textContent).toBe("Next");
    expect(deleted()).toEqual([]);
    // The second click (a right click on the dialog): a second still of the SAME step through the replace-picture path - the
    // last still stands - and the first, superseded within the run, leaves Captures once the later attach landed.
    await fire("capture:step-click", { x: 350, y: 350, button: "right" });
    expect(attaches()).toEqual([B, B]);
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)[1][1]).toEqual({ capture_id: cid(2), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    expect(server.steps.find((s) => s.id === B)!.source).toEqual({ capture_id: cid(2), annotated: false });
    expect(deleted()).toEqual([`/api/captures/${cid(1)}`]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", stillsThisStep: 2 });
    // A third, the deletion of the second; the count follows.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(deleted()).toEqual([`/api/captures/${cid(1)}`, `/api/captures/${cid(2)}`]);
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 3 });
    // Next from the builder's line: moved on to step 3, nothing skipped (B has its picture), the button reads Skip there.
    await act(async () => { q("[data-capture-next]")!.click(); await new Promise((r) => setTimeout(r, 30)); });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", stillsThisStep: 0 });
    expect(q("[data-capture-next]")!.textContent).toBe("Skip");
    // Back to B: three stills there, Next again; forward to C and Done: one step captured, three captures, none skipped - the
    // bar's Next on C was never pressed, so C is not skipped.
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 3 });
    expect(q("[data-capture-next]")!.textContent).toBe("Next");
    await fire("capture:control", { action: "next" });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(doneRow()).toEqual({ event: "done", captured: 1, skipped: 0, captures: 3, by_click: 3, by_typing: 0 });
    expect(status()).toBe("Captured 1 step.");
  });

  it("Shift+F9 both ways (R1): the shell's step-next event and the page's own key move on, and a Skip of a step with no still passes it; Next while a still is in flight is allowed and the attach lands on its claimed step", async () => {
    await mount();
    await openAndStart("next");
    // The shell's Shift+F9: B skipped (no still), on to C.
    await fire("capture:step-next", null);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", canBack: true });
    // The page's own Shift+F9 while the builder has the focus: C skipped, the end.
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "F9", shiftKey: true, bubbles: true, cancelable: true })); await new Promise((r) => setTimeout(r, 30)); });
    expect(lastTold()).toMatchObject({ phase: "finished", label: "All steps visited", remaining: 0 });
    // Back twice to B; a click's still is held in flight, and Next meanwhile is allowed: the bar moves on to C while
    // "Capturing…", and the still lands on B - the step it was taken for - so B is pictured and not skipped.
    await fire("capture:control", { action: "back" });
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3" });
    let openStill!: () => void;
    stillGates = [new Promise<void>((r) => { openStill = r; })];
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ phase: "capturing", label: "Step 2 of 3", canCapture: false });
    await fire("capture:step-next", null);
    expect(lastTold()).toMatchObject({ phase: "capturing", label: "Step 3 of 3" });
    expect(attaches()).toEqual([]);
    await act(async () => { openStill(); await new Promise((r) => setTimeout(r, 60)); });
    expect(attaches()).toEqual([B]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", stillsThisStep: 0, message: null });
    expect(q("[data-capture-message]")).toBeNull();
    await fire("capture:control", { action: "done" });
    await settle();
    // B got its picture after it was passed: not skipped; C was skipped by the page's key and never pictured.
    expect(doneRow()).toEqual({ event: "done", captured: 1, skipped: 1, captures: 1, by_click: 1, by_typing: 0 });
    expect(status()).toBe("Captured 1 step, skipped 1.");
  });

  it("a still that fails on its own in next mode leaves the bar on its step with the transient line, and no \"press Back\" line (R1)", async () => {
    await mount();
    await openAndStart("next");
    stillFailure = new Error("The grab timed out.");
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", stillsThisStep: 0, message: "The step could not be captured: The grab timed out." });
    expect(q("[data-capture-message]")!.textContent).toBe("The step could not be captured: The grab timed out.");
    // The next capture lands, and the line goes.
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", stillsThisStep: 1, message: null });
    expect(q("[data-capture-message]")).toBeNull();
  });

  it("the still after the click (R2): the step is claimed and the bar told at once, the grab is issued SETTLE_MS later; Shift+F8 grabs at once", async () => {
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-settle]") as HTMLInputElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(startRow()).toEqual(start({ advance: "next", settle_ms: SETTLE_MS }));
    vi.useFakeTimers();
    const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    // The click: claimed at once (the bar reads "Capturing…"), no grab yet.
    await act(async () => { shell.handlers["capture:step-click"]?.({ payload: { x: 900, y: 700, button: "left" } }); await vi.advanceTimersByTimeAsync(0); });
    expect(lastTold()).toMatchObject({ phase: "capturing", label: "Step 2 of 3", canCapture: false });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    await tick(SETTLE_MS - 1);
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    // Half a second after the press: the grab, then the attach with the hotspot where the click was.
    await tick(1);
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    await tick(50);
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", stillsThisStep: 1 });
    // The key's whole point is "now": no wait.
    await act(async () => { shell.handlers["capture:step-key"]?.({ payload: { x: 900, y: 700 } }); await vi.advanceTimersByTimeAsync(0); });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    vi.useRealTimers();
  });

  it("a typed pause (R3, F1) takes a still of the window typed into, with the step's last click as the hotspot when it is in the picture, else the pointer; counted as typed", async () => {
    await mount();
    await openAndStart("next");
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 1 });
    // A reply typed into a chat window off the step's last still (G1: the Window kind's area is the rectangle of the step's
    // last still this run): no still, the line on the bar, the step's picture kept.
    await fire("capture:step-typed", { x: 1600, y: 200, window: { x: 1500, y: 100, width: 300, height: 300 } });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    expect(deleted()).toEqual([]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", canCapture: true, stillsThisStep: 1, message: "Typing outside the step's window takes no still." });
    // Typed into Spoon (the foreground window's frame rides in the event) with the pointer on nothing (5, 5): the still is
    // Spoon's, and the hotspot the step's last click, which lies in it.
    await fire("capture:step-typed", { x: 5, y: 5, window: SPOON });
    expect(posts().filter(([p]) => p === "/api/captures")[1][1]).toMatchObject({ x: 100, y: 100, width: 1200, height: 800, name: "Tour · step 2" });
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)[1][1]).toEqual({ capture_id: cid(2), hotspot: { x: 720, y: 570, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 2, message: null });
    // Typed into the dialog on top with the pointer on it: the still is the DIALOG's (not the window under the last click),
    // and the last click (900, 700) is not in that picture, so the hotspot is round the pointer.
    await fire("capture:step-typed", { x: 350, y: 350, window: DIALOG });
    expect(posts().filter(([p]) => p === "/api/captures")[2][1]).toMatchObject({ x: 300, y: 300, width: 400, height: 200 });
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)[2][1]).toEqual({ capture_id: cid(3), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    // Typed into the dialog with the pointer off it and the last click off it: the still, with no hotspot.
    await fire("capture:step-typed", { x: 5, y: 5, window: DIALOG });
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)[3][1]).toEqual({ capture_id: cid(4), hotspot: null, frame: { width: 1200, height: 800 } });
    // No foreground window (null): in Window kind no still, the no-window refusal on the bar and the builder's line.
    await fire("capture:step-typed", { x: 900, y: 700, window: null });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(4);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", message: "No window is under the pointer: point at the window and click, or press Shift+F8." });
    // Next to C, which was never clicked and has no still yet this run (no area, G1): a typed pause there - a second on, the
    // pause of Next being over (G2) - takes the still of the window typed into, the hotspot round the pointer.
    await fire("capture:step-next", null);
    await settle(1000);
    await fire("capture:step-typed", { x: 350, y: 350, window: DIALOG });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${C}/picture`)![1]).toEqual({ capture_id: cid(5), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", stillsThisStep: 1, message: null });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(doneRow()).toEqual({ event: "done", captured: 2, skipped: 0, captures: 5, by_click: 1, by_typing: 4 });
    expect(status()).toBe("Captured 2 steps.");
    // Capture mode: the click moved the session on, so a typed pause has no click point - the pointer when it is in the
    // picture. (Every step this time: all three have pictures after the first run, so the run starts at step 1.)
    shell.told.length = 0;
    await openAndStart("capture");
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 1 of 3" });
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3" });
    await fire("capture:step-typed", { x: 350, y: 350, window: DIALOG });
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`).pop()![1]).toEqual({ capture_id: cid(7), hotspot: { x: 0, y: 20, width: 160, height: 60 }, frame: { width: 1200, height: 800 } });
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3" });
    await fire("capture:control", { action: "done" });
    await settle();
    expect(doneRow()).toEqual({ event: "done", captured: 2, skipped: 0, captures: 2, by_click: 1, by_typing: 1 });
  });

  it("a typed pause that fires within a second of Next belongs to the step just left and is ignored (G2); a second on, it is a still", async () => {
    await mount();
    await openAndStart("next");
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 1 });
    vi.useFakeTimers();
    const fireNow = (name: string, payload: unknown) => act(async () => { shell.handlers[name]?.({ payload }); await vi.advanceTimersByTimeAsync(0); });
    // The user types into Spoon, then presses Shift+F9 within the second: the pause fires AFTER Next - on step 3 - and is dropped.
    await fireNow("capture:step-next", null);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", stillsThisStep: 0 });
    await act(async () => { await vi.advanceTimersByTimeAsync(TYPING_PAUSE_MS - 1); });
    await fireNow("capture:step-typed", { x: 900, y: 700, window: SPOON });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", stillsThisStep: 0 });
    // The bar's Next is the same: the pause of Next counts from whichever moved on last.
    await fireNow("capture:control", { action: "back" });
    await fireNow("capture:control", { action: "next" });
    await fireNow("capture:step-typed", { x: 900, y: 700, window: SPOON });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(1);
    // A second on: a typed pause is a still of step 3 (no still yet there: the window typed into).
    await act(async () => { await vi.advanceTimersByTimeAsync(TYPING_PAUSE_MS); });
    await fireNow("capture:step-typed", { x: 900, y: 700, window: SPOON });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(posts().filter(([p]) => p === "/api/captures").length).toBe(2);
    expect(attaches()).toEqual([B, C]);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", stillsThisStep: 1 });
    vi.useRealTimers();
  });

  it("a typed pause in a Region (F1): the window typed into must meet the frame - off it, no still and the bar says so; no window known, the still as before", async () => {
    hoisted.region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", typing: true });
    // Typed into a window off the frame (x past 310): no still, the transient line, the capture still ready.
    await fire("capture:step-typed", { x: 600, y: 600, window: { x: 500, y: 500, width: 300, height: 300 } });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", canCapture: true, message: "Typing outside the frame takes no still." });
    // Typed into a window that meets the frame: the region's still; the hotspot round the pointer, which is in it; the line gone.
    await fire("capture:step-typed", { x: 100, y: 100, window: { x: 200, y: 100, width: 300, height: 300 } });
    expect(posts().find(([p]) => p === "/api/captures")![1]).toEqual({ x: 10, y: 20, width: 300, height: 200, cursor: true, kind: "region", name: "Tour · step 2" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 10, y: 50, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 1, message: null });
    // No foreground window known: the still as before, by the pointer rule - the pointer off the frame gives no hotspot.
    await fire("capture:step-typed", { x: 500, y: 100, window: null });
    expect(posts().filter(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)[1][1]).toEqual({ capture_id: cid(2), hotspot: null });
    expect(lastTold()).toMatchObject({ stillsThisStep: 2, message: null });
  });

  it("a typed pause in Full screen (F1): the window typed into must meet the captured monitor - off it, no still and the bar says so", async () => {
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='screen']") as HTMLButtonElement).click(); });
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    // A window on another monitor (x past 1920): no still, the line.
    await fire("capture:step-typed", { x: 2600, y: 200, window: { x: 2500, y: 100, width: 300, height: 300 } });
    expect(posts().filter(([p]) => p === "/api/captures")).toEqual([]);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 2 of 3", canCapture: true, message: "Typing off the captured screen takes no still." });
    // A window that straddles the edge meets the monitor: the monitor's still, the hotspot round the pointer on it.
    await fire("capture:step-typed", { x: 900, y: 700, window: { x: 1800, y: 100, width: 300, height: 300 } });
    expect(posts().find(([p]) => p === "/api/captures")![1]).toEqual({ x: 0, y: 0, width: 1920, height: 1080, cursor: true, kind: "screen", name: "Tour · step 2" });
    expect(posts().find(([p]) => p === `/api/walkthroughs/${WID}/steps/${B}/picture`)![1]).toEqual({ capture_id: cid(1), hotspot: { x: 820, y: 670, width: 160, height: 60 } });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", stillsThisStep: 1, message: null });
  });

  it("the thumbnail's follow-up payload carries the last transient line (F5), and a thumbnail still in flight at Done never lands on the next session", async () => {
    hoisted.region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    await mount();
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await chooseAdvance("capture");
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    // The thumbnail is held: the still lands and the bar settles without it.
    let openThumb!: (url: string) => void;
    vi.mocked(thumbnailOf).mockImplementationOnce(() => new Promise<string>((r) => { openThumb = r; }));
    await fire("capture:step-click", { x: 100, y: 100, button: "left" });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", lastStill: null, message: null });
    // A refusal meanwhile - a click outside the frame - is a transient line; the thumbnail arriving must not wipe it (n5).
    await fire("capture:step-click", { x: 500, y: 100, button: "left" });
    expect(lastTold()).toMatchObject({ message: "Click inside the frame to capture this step.", lastStill: null });
    await act(async () => { openThumb("data:image/jpeg;base64,one"); await new Promise((r) => setTimeout(r, 30)); });
    expect(lastTold()).toMatchObject({ message: "Click inside the frame to capture this step.", lastStill: { step: 2, thumb: "data:image/jpeg;base64,one" } });
    // The next state change clears the line as ever.
    await fire("capture:control", { action: "back" });
    expect(lastTold()).toMatchObject({ label: "Step 2 of 3", message: null, lastStill: { step: 2, thumb: "data:image/jpeg;base64,one" } });
    // A thumbnail held across Done (n6): the session ends, a new one starts and lands its own still (its thumbnail held too);
    // the OLD thumbnail resolving then is dropped - the new session shows nothing until its own lands.
    let openOld!: (url: string) => void, openNew!: (url: string) => void;
    vi.mocked(thumbnailOf).mockImplementationOnce(() => new Promise<string>((r) => { openOld = r; }));
    await fire("capture:step-click", { x: 100, y: 100, button: "left" });
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", lastStill: { step: 2, thumb: "data:image/jpeg;base64,one" } });
    await fire("capture:control", { action: "done" });
    await settle();
    // The next session: only C has no picture now, so it is the one step; its still lands, its thumbnail held too.
    shell.told.length = 0;
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await chooseAdvance("capture");
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(lastTold()).toMatchObject({ phase: "ready", label: "Step 3 of 3", lastStill: null });
    vi.mocked(thumbnailOf).mockImplementationOnce(() => new Promise<string>((r) => { openNew = r; }));
    await fire("capture:step-click", { x: 100, y: 100, button: "left" });
    expect(lastTold()).toMatchObject({ phase: "finished", lastStill: null });
    await act(async () => { openOld("data:image/jpeg;base64,old"); await new Promise((r) => setTimeout(r, 30)); });
    expect(lastTold()).toMatchObject({ phase: "finished", lastStill: null });
    expect(shell.told.some((t) => ((t as { lastStill: { thumb: string } | null }).lastStill?.thumb ?? "") === "data:image/jpeg;base64,old")).toBe(false);
    await act(async () => { openNew("data:image/jpeg;base64,new"); await new Promise((r) => setTimeout(r, 30)); });
    expect(lastTold()).toMatchObject({ phase: "finished", lastStill: { step: 3, thumb: "data:image/jpeg;base64,new" } });
  });

  it("the thumbnail (R4): the last still's picture is fetched by the page and travels in the payload as a data URL with the step's number; one that cannot be made leaves nothing", async () => {
    await mount();
    await openAndStart();
    await fire("capture:step-click", { x: 900, y: 700, button: "left" });
    expect(vi.mocked(api.blob).mock.calls.map(([p]) => p)).toEqual([`/api/walkthroughs/${WID}/steps/${B}/image?v=${server.revision}`]);
    expect(vi.mocked(thumbnailOf)).toHaveBeenCalledTimes(1);
    expect(lastTold()).toMatchObject({ label: "Step 3 of 3", lastStill: { step: 2, thumb: "data:image/jpeg;base64,thumb" } });
    // The bar's hello carries it too.
    shell.told.length = 0;
    await fire("capture:control", { action: "hello" });
    expect(lastTold()).toMatchObject({ lastStill: { step: 2, thumb: "data:image/jpeg;base64,thumb" } });
    // A thumbnail that cannot be made (the canvas, the fetch): null, and nothing said; the picture is on the step either way.
    vi.mocked(thumbnailOf).mockImplementationOnce(async () => { throw new Error("no canvas"); });
    await fire("capture:step-key", { x: 350, y: 350 });
    expect(attaches()).toEqual([B, C]);
    expect(lastTold()).toMatchObject({ phase: "finished", lastStill: null, message: null });
    expect(q("[data-capture-message]")).toBeNull();
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
    // The retake superseded the first still within the run: it leaves Captures (R1), in this mode too.
    expect(deleted()).toEqual([`/api/captures/${cid(1)}`]);
    await fire("capture:control", { action: "done" });
    await settle();
    expect(doneRow()).toEqual({ event: "done", captured: 1, skipped: 0, captures: 2, by_click: 1, by_typing: 0 });
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
    await chooseAdvance("capture");
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(vi.mocked(pickRegion)).toHaveBeenCalledTimes(1);
    expect(invoked("capture_bar_open")).toEqual([]);
    expect(q("[data-capture-steps]")!.hasAttribute("disabled")).toBe(false);
    hoisted.region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    shell.hotkeysRefused = true;
    await act(async () => { q("[data-capture-steps]")!.click(); });
    await act(async () => { (q("[data-capture-kind='region']") as HTMLButtonElement).click(); });
    await chooseAdvance("capture");
    await act(async () => { (q("[data-capture-steps-start]") as HTMLButtonElement).click(); });
    await settle(60);
    expect(invoked("capture_bar_open").length).toBe(1);
    // Under the region (y = 20 + 200 + 12), its x kept inside the monitor's margin.
    expect(invoked("capture_bar_open")[0].args).toMatchObject({ x: 12, y: 232, mode: "steps" });
    expect(q("[data-capture-message]")!.textContent).toContain("Shift+F8 is taken by another program");
    expect(lastTold()).toMatchObject({ message: expect.stringContaining("Shift+F8 is taken by another program") });
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
