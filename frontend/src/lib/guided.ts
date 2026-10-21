/**
 * Guided capture (W2): the PURE half - which steps a session visits, where it stands, the hotspot made from the
 * pointer at the key press, the window under the pointer, the rectangle a capture takes, the name of the still,
 * what the capture bar is told, which key is the capture key. The wiring - the shell's bar window and its global
 * key, the overlay for a region, the two requests a capture makes - is `components/walkthrough/useGuidedCapture.ts`.
 * Desktop app only: the bar, the key and the pointer are the shell's. Unit-tested in guided.test.ts.
 *
 * THE POINTER RULE. The pointer's position at the moment Shift+F8 is pressed comes from the shell in PHYSICAL
 * virtual-screen pixels (the space the overlay's regions, the monitors and the windows are in: the hotkey thread
 * reads `GetCursorPos` before the page is told, so it is read before the still is taken). The hotspot is a
 * `HOTSPOT_DEFAULT` rectangle centred on that point in the PICTURE's own pixels - the pointer minus the
 * capture's origin - clamped into the picture; a pointer outside the captured area gives no hotspot. The bar's
 * Capture button carries no pointer (it is on the bar), so a capture from the button gives no hotspot either.
 *
 * THE CLICK RULE (W2b). With "Capture on click" (the default) the shell's click hook raises every left or right
 * press outside the app's own windows as `capture:step-click` with the point (`clickOf`), and the page treats it
 * exactly as the key: the same capture, the same hotspot round the point. On top: for a Region, a click outside
 * the frame is no step and the bar says "Click inside the frame to capture this step.", and for Full screen a click
 * off the chosen monitor is none either - "Click on the captured screen to capture this step." (`clickArea`,
 * `clickRule`, `outsideLine`; every click ON that monitor is a step, other applications' included); a right
 * click is a step like a left one (the context menu is the result on the next still). Shift+F8 still captures
 * without a click. A click while a still is being taken is NOT lost: the step is claimed at once (`claimStep`:
 * the session moves on, the bar follows), its still is taken at once beside the first, and the attaches land in
 * click order; up to `MAX_IN_FLIGHT` stills may be in flight, a click past that is dropped with `TOO_FAST`.
 * The counts: `captured` is every capture that landed (a key, a click or the bar's button), `taken` the DISTINCT
 * steps that got a picture this run (a retake is not a second step - `capturedSteps`), `byClick` the captures the
 * hook made, `skipped` the DISTINCT steps skipped and not captured since (`passed` holds their ids).
 */
import type { Size } from "./annotate";
import { type CaptureKind, type MonitorInfo, type Region, type WindowInfo, monitorFor } from "./capture";
import { type Hotspot, type WalkthroughDocument, clampHotspot, stepIndex, stepLabel } from "./walkthrough";

export interface Point { x: number; y: number }
export interface Rect { x: number; y: number; width: number; height: number }

/** The hotspot a guided capture draws round the pointer, in picture pixels (the server clamps it again). */
export const HOTSPOT_DEFAULT: Size = { width: 160, height: 60 };
/** The capture bar's size in its second mode, physical pixels at scale 1 (the recorder's is 300 x 56). */
export const STEPS_BAR_WIDTH = 600;
export const STEPS_BAR_HEIGHT = 112;
/** The shell's events: the key press with the pointer (`{x, y}`), a click (`{x, y, button}`), and the bar's buttons (`{action}`). */
export const STEP_KEY_EVENT = "capture:step-key";
export const STEP_CLICK_EVENT = "capture:step-click";
export const CONTROL_EVENT = "capture:control";
export const STATE_EVENT = "capture:state";
/** The bar's second mode, as `capture_bar_open` and the state payload name it. */
export const BAR_MODE = "steps";
/** The key set `capture_hotkeys_start` is asked for: Shift+F8 alone. */
export const KEYS_MODE = "steps";
export const RECORDING_IN_PROGRESS = "A recording is in progress: stop it first (Shift+F10, or Stop on the recorder bar), then capture the steps.";
export const NEEDS_DESKTOP = "Capturing the steps needs the desktop app on the machine whose screen it is.";
export const NOTHING_TO_CAPTURE = "Every step has a picture already. Choose \"Every step\" to capture them again.";
export const NO_WINDOW_UNDER_POINTER = "No window is under the pointer: point at the window and press Shift+F8.";
/** The same refusal in click mode (W2b-1): a click will do too. */
export const NO_WINDOW_UNDER_POINTER_CLICK = "No window is under the pointer: point at the window and click, or press Shift+F8.";
/** The no-window line by mode: with the clicks on, a click will do; off, the key alone. */
export function noWindowLine(clicks: boolean): string {
  return clicks ? NO_WINDOW_UNDER_POINTER_CLICK : NO_WINDOW_UNDER_POINTER;
}
/** How many stills may be in flight at once (F8): a click while the first still is taken is captured beside it; one more is `TOO_FAST`. */
export const MAX_IN_FLIGHT = 2;
export const TOO_FAST = "Too fast: wait for the still.";
export const KEY_TAKEN = "Shift+F8 is taken by another program: use Capture on the bar (a capture from the bar has no hotspot).";
/** The same refusal when the clicks are live (H1): a click captures whoever holds Shift+F8. */
export const KEY_TAKEN_CLICK = "Shift+F8 is taken by another program: click to capture, or use Capture on the bar.";
/** Both off with the tick on (H1): ONE line naming both - the bar's Capture is the one way left. */
export const KEYS_AND_CLICKS_OFF =
  "Shift+F8 is taken by another program and clicks could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).";
export const BAR_NOT_HIDDEN = "This Windows cannot hide the capture bar from the picture; keep it off the captured area.";
/** The shell could not open the bar at all: the start is given up (the reason follows the colon). */
export const BAR_NOT_OPENED = "The capture bar could not be opened";
/** A click outside the region's frame (W2b): no step; shown on the bar until the next payload. */
export const CLICK_OUTSIDE_FRAME = "Click inside the frame to capture this step.";
/** A click off the chosen monitor in Full screen (W2b-2): no step; shown on the bar until the next payload. */
export const CLICK_OUTSIDE_SCREEN = "Click on the captured screen to capture this step.";
/**
 * A step whose still failed while a later click's still was in flight (G2): it is left without a new picture, and
 * the bar and the builder say so - a STANDING line, until that step gets a picture or the session ends. A step
 * that had no picture has none ("has no picture"); one that had a picture ("Every step") keeps its old one (H2).
 */
export function noPictureLine(stepNumber: number): string {
  return `Step ${stepNumber} has no picture: press Back to take it again.`;
}
export function keptPictureLine(stepNumber: number): string {
  return `Step ${stepNumber} kept its old picture: press Back to take it again.`;
}
/** A step left without its new still this run: its number in the walkthrough, and whether it kept an old picture. */
export interface MissedStep { number: number; kept: boolean }
/** The standing lines of the steps left without their new still this run, in step order, or null for none. */
export function missedLine(steps: MissedStep[]): string | null {
  const sorted = [...steps].sort((a, b) => a.number - b.number);
  return sorted.length ? sorted.map((s) => (s.kept ? keptPictureLine(s.number) : noPictureLine(s.number))).join(" ") : null;
}

/** Whether Shift+F8 and the click hook went up (H1): what `capture_hotkeys_start` answers. */
export interface KeysUp { key: boolean; clicks: boolean }

/**
 * Any shell's answer to `capture_hotkeys_start`, read: `{key, clicks}` from a shell since H1; a boolean from the W2b
 * shell before it (true: everything asked is up; false: the key up, the hook refused); nothing (`undefined`) from the
 * 0.17.0 shell, which has no click hook at all - the key up, the clicks off (the in-app updater refreshes the page,
 * never the shell, so a newer page can meet that shell); a refusal (`refused`) from a shell before H1 whose key was
 * taken - it installed no hook either - or from any shell while the other key set is held: both off.
 */
export function keysUpOf(answer: unknown, refused: boolean): KeysUp {
  if (refused) return { key: false, clicks: false };
  if (answer && typeof answer === "object") {
    const a = answer as { key?: unknown; clicks?: unknown };
    return { key: a.key !== false, clicks: a.clicks === true };
  }
  if (typeof answer === "boolean") return { key: true, clicks: answer };
  return { key: true, clicks: false };
}

/**
 * What the start says about the key and the clicks (H1), and whether the clicks are live: the clicks only when asked
 * AND the hook is up (so the bar's and the builder's "Click to capture" hint is shown only then); `KEY_TAKEN_CLICK`
 * when the key is taken but the clicks are live, `KEY_TAKEN` when it is taken and they were not asked; `CLICKS_OFF`
 * when they were asked and the hook is not up. Both off with the tick on: ONE line, `KEYS_AND_CLICKS_OFF` - the two
 * single lines would each point at the other's way.
 */
export function keysLines(up: KeysUp, clicksAsked: boolean): { clicks: boolean; lines: string[] } {
  const clicks = clicksAsked && up.clicks;
  if (!up.key && clicksAsked && !up.clicks) return { clicks, lines: [KEYS_AND_CLICKS_OFF] };
  const lines: string[] = [];
  if (!up.key) lines.push(clicks ? KEY_TAKEN_CLICK : KEY_TAKEN);
  if (clicksAsked && !up.clicks) lines.push(CLICKS_OFF);
  return { clicks, lines };
}
/** The shell took the key but could not install the click hook (W2b): the capture goes on by the key and the button. */
export const CLICKS_OFF = "Clicks could not be captured on this computer: capture with Shift+F8 or the bar's Capture.";
/** The bar's second line in click mode (W2b). */
export const CLICK_HINT = "Click to capture · Shift+F8 captures without a click";

export interface GuidedOptions {
  kind: CaptureKind;
  cursor: boolean;
  /** Every step, or only those without a picture. */
  every: boolean;
  /** The monitor for a full-screen capture (null: the primary). */
  monitor: number | null;
  /** Capture on click (W2b): every click in the frame takes the step's still; Shift+F8 still captures without one. */
  clicks: boolean;
}

export type GuidedPhase = "idle" | "starting" | "ready" | "capturing" | "finished";

/**
 * Where a session stands: the step ids it visits, the index of the current one, and the counts for the audit row -
 * `captured` every capture that landed (a key, a click, the bar's button), `taken` the distinct steps that got a
 * picture this run (a retake after Back is not a second step), `byClick` how many captures the click hook made,
 * `passed` the distinct steps skipped and `skipped` how many of those got no picture since (a step skipped, Back,
 * skipped again is one; a step skipped and then captured is not skipped).
 */
export interface GuidedSession { ids: string[]; at: number; captured: number; skipped: number; taken: string[]; byClick: number; passed: string[] }

export function sessionOf(ids: string[]): GuidedSession {
  return { ids: [...ids], at: 0, captured: 0, skipped: 0, taken: [], byClick: 0, passed: [] };
}

export function currentId(s: GuidedSession): string | null {
  return s.at >= 0 && s.at < s.ids.length ? s.ids[s.at] : null;
}

export function atEnd(s: GuidedSession): boolean {
  return s.at >= s.ids.length;
}

/** The skipped count: the distinct steps skipped (`passed`) that got no picture since. */
function skippedOf(passed: string[], taken: string[]): number {
  return passed.filter((id) => !taken.includes(id)).length;
}

/**
 * The step a capture claims - the current one - with the session moved on AT ONCE: the still and the attach follow
 * (in click order, when more than one is in flight) and `recordCapture` counts the one that lands. Null when every
 * step is visited.
 */
export function claimStep(s: GuidedSession): { id: string; session: GuidedSession } | null {
  const id = currentId(s);
  if (!id) return null;
  return { id, session: { ...s, at: s.at + 1 } };
}

/**
 * A capture that landed on step `id`: one more in `captured` (and in `byClick` when the click hook made it), the
 * id in `taken` once however often the step is captured again after Back, and the step no longer skipped.
 */
export function recordCapture(s: GuidedSession, id: string, byClick = false): GuidedSession {
  const taken = s.taken.includes(id) ? s.taken : [...s.taken, id];
  return { ...s, captured: s.captured + 1, byClick: s.byClick + (byClick ? 1 : 0), taken, skipped: skippedOf(s.passed, taken) };
}

/**
 * The session moved on after a capture (claimed and landed in one go: the key's path when nothing else is in
 * flight) or a skip (the step's id in `passed` once; `skipped` counts the distinct steps skipped and not captured).
 */
export function advance(s: GuidedSession, outcome: "captured" | "skipped", byClick = false): GuidedSession {
  const claimed = claimStep(s);
  if (!claimed) return s;
  if (outcome === "captured") return recordCapture(claimed.session, claimed.id, byClick);
  const passed = s.passed.includes(claimed.id) ? s.passed : [...s.passed, claimed.id];
  return { ...claimed.session, passed, skipped: skippedOf(passed, s.taken) };
}

/** How many DISTINCT steps got a picture this run: what "Captured N steps" counts (a retake is not a second step). */
export function capturedSteps(s: GuidedSession): number {
  return s.taken.length;
}

/** Back to the step before (a capture can be taken again); the counts are what happened and stay. */
export function stepBack(s: GuidedSession): GuidedSession {
  return s.at > 0 ? { ...s, at: s.at - 1 } : s;
}

/** How many steps are still to visit, the current one included. */
export function remaining(s: GuidedSession): number {
  return Math.max(0, s.ids.length - s.at);
}

/**
 * The hotspot round `point` (physical pixels) on a picture that starts at `origin` (physical pixels) and is
 * `picture` big: `size` centred on the pointer in the picture's own pixels, kept inside it; null when the pointer
 * is outside the captured area, or the picture cannot hold a hotspot.
 */
export function hotspotAround(point: Point, size: Size, origin: Point, picture: Size): Hotspot | null {
  const x = point.x - origin.x, y = point.y - origin.y;
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x >= picture.width || y >= picture.height) return null;
  const width = Math.min(size.width, picture.width), height = Math.min(size.height, picture.height);
  return clampHotspot({ x: Math.round(x - width / 2), y: Math.round(y - height / 2), width, height }, picture);
}

/** The hotspot of a capture: `HOTSPOT_DEFAULT` round the pointer, or none when there was no pointer (the bar's button). */
export function hotspotOf(pointer: Point | null, capture: Rect): Hotspot | null {
  if (!pointer) return null;
  return hotspotAround(pointer, HOTSPOT_DEFAULT, { x: capture.x, y: capture.y }, { width: capture.width, height: capture.height });
}

/**
 * The run's frame (W2b, R7: "a standard window size"), Window kind only: the widest width × the tallest height of
 * the stills so far (G5), so a run converges on one frame whatever order its windows come in - a wider-but-shorter
 * still and a taller-but-narrower one end on the same frame. A still is never scaled: one smaller than the frame on
 * either side is sent with `frame` and the server draws it centred on a transparent canvas of that size; one larger
 * on a side grows the frame on that side first, and is attached on the NEW frame when the other side is now smaller
 * than it, at its own size when it is the frame. Earlier stills keep the frame they were drawn on (start with the
 * main window). Region and Full screen have one size already, so no frame is kept for them.
 */
export function frameFor(kind: CaptureKind, frame: Size | null, still: Size): { frame: Size | null; send: Size | null } {
  if (kind !== "window") return { frame: null, send: null };
  const next = frame
    ? { width: Math.max(frame.width, still.width), height: Math.max(frame.height, still.height) }
    : { width: still.width, height: still.height };
  const same = next.width === still.width && next.height === still.height;
  return { frame: next, send: same ? null : next };
}

/** Whether a point (physical pixels) lies inside a rectangle (the same pixels). */
export function insideFrame(point: Point, frame: Rect): boolean {
  return point.x >= frame.x && point.x < frame.x + frame.width && point.y >= frame.y && point.y < frame.y + frame.height;
}

/**
 * Where a click must land to be a step, per kind: the region drawn at the start (Region), the CHOSEN monitor - the
 * primary when none was chosen (Full screen, W2b-2) - or anywhere (Window: the window under the point, as the key).
 */
export function clickArea(kind: CaptureKind, region: Region | null, monitors: MonitorInfo[], monitor: number | null): Rect | null {
  if (kind === "region") return region;
  if (kind === "screen") return monitorFor(monitors, monitor);
  return null;
}

/**
 * What a click does per kind (W2b): outside the kind's area (`clickArea`: a Region's frame, a Full screen's
 * monitor) it is no step ("outside", the bar says so with `outsideLine`); inside it, and anywhere for a Window, a
 * capture. No area known (no region chosen, no monitor found): a capture, whose own path then refuses with its line.
 */
export function clickRule(kind: CaptureKind, area: Rect | null, point: Point): "capture" | "outside" {
  if ((kind === "region" || kind === "screen") && area && !insideFrame(point, area)) return "outside";
  return "capture";
}

/** The bar's line for a click outside the kind's area. */
export function outsideLine(kind: CaptureKind): string {
  return kind === "screen" ? CLICK_OUTSIDE_SCREEN : CLICK_OUTSIDE_FRAME;
}

/** The top-most window whose frame holds the point (the shell lists windows top-most first), or null. */
export function windowUnder(windows: WindowInfo[], point: Point): WindowInfo | null {
  return windows.find((w) => point.x >= w.x && point.x < w.x + w.width && point.y >= w.y && point.y < w.y + w.height) ?? null;
}

export type CaptureTarget = { rect: Rect; title: string | null } | { error: string };

/**
 * What one capture grabs: the region chosen at the start, the window under the pointer (the front-most one when
 * the capture came from the bar's button, which carries no pointer), or the chosen monitor. `clicks` picks the
 * wording of the no-window refusal (a click will do too when the clicks are on).
 */
export function captureTarget(
  kind: CaptureKind,
  input: { region: Region | null; monitors: MonitorInfo[]; monitor: number | null; windows: WindowInfo[]; pointer: Point | null; clicks?: boolean },
): CaptureTarget {
  if (kind === "region") {
    if (!input.region) return { error: "No region was chosen." };
    const r = input.region;
    return { rect: { x: r.x, y: r.y, width: r.width, height: r.height }, title: r.window_title };
  }
  if (kind === "window") {
    const w = input.pointer ? windowUnder(input.windows, input.pointer) : input.windows[0] ?? null;
    if (!w) return { error: noWindowLine(!!input.clicks) };
    return { rect: { x: w.x, y: w.y, width: w.width, height: w.height }, title: w.title };
  }
  const m = monitorFor(input.monitors, input.monitor);
  if (!m) return { error: "No monitor was found." };
  return { rect: { x: m.x, y: m.y, width: m.width, height: m.height }, title: null };
}

/** "<walkthrough> · step N", within the still's 120 characters. */
export function captureName(walkthrough: string, number: number): string {
  const suffix = ` · step ${number}`;
  const base = ((walkthrough || "").trim() || "Walkthrough").slice(0, Math.max(1, 120 - suffix.length)).trim();
  return `${base}${suffix}`;
}

/** What the capture bar shows in its second mode. */
export interface BarPayload {
  mode: typeof BAR_MODE;
  phase: GuidedPhase;
  name: string;
  /** "Step 3 of 12" - the step's place in the walkthrough. */
  label: string;
  /** The step's text (the tooltip line), and its instruction under it. */
  text: string;
  instruction: string;
  remaining: number;
  canBack: boolean;
  canCapture: boolean;
  /**
   * A line the bar shows in red under the step, or null: the standing lines in every payload - the refusals the
   * capture goes on under (the key taken, the bar not hidden from the picture, the clicks off) and, for a step left
   * without its new still beside a later one, "Step N has no picture…" or "Step N kept its old picture…" (G2, H2)
   * until that step gets a picture - and a per-capture refusal (no window under the pointer, a click outside the
   * frame or off the screen, a still that could not be taken, "Too fast") in the one payload after it - the next
   * payload clears it (W2b, D1).
   */
  message: string | null;
  /**
   * The clicks are live - asked for AND the shell's hook up (H1): the bar adds "Click to capture · Shift+F8 captures
   * without a click" under the instruction only then.
   */
  clicks: boolean;
}

/**
 * The bar's state: what the bar page shows (`public/capture-bar.html`, steps mode) and the builder's own copy. The
 * hook sends it on every change and again when the bar asks (`capture:control` `hello`, sent once the bar's
 * listener is up: the first state would otherwise be missed, the page loading after the hook's first send).
 */
export function barPayload(name: string, doc: WalkthroughDocument, s: GuidedSession, phase: GuidedPhase, message: string | null = null, clicks = false): BarPayload {
  const id = currentId(s);
  const index = id ? stepIndex(doc, id) : -1;
  const step = index >= 0 ? doc.steps[index] : null;
  const done = atEnd(s) || !step;
  return {
    mode: BAR_MODE, phase, name,
    label: done ? "All steps visited" : stepLabel(index, doc.steps.length),
    text: done ? "Press Done to close the bar and go back to the builder." : step.text || "(no text yet)",
    instruction: done ? "" : step.instruction,
    remaining: remaining(s), canBack: s.at > 0, canCapture: !done && phase === "ready",
    message, clicks,
  };
}

/** The lines the bar shows in red: the session's standing refusals and a transient one, joined; null for none. */
export function barMessage(standing: string | null, transient: string | null): string | null {
  const lines = [standing, transient].filter((line): line is string => !!line);
  return lines.length ? lines.join(" ") : null;
}

/** Shift+F8 exactly: the capture key (the shell's global key, and the page's own when it has the focus). */
export function guidedHotkey(e: { key: string; shiftKey: boolean; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean }): boolean {
  return e.key === "F8" && e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey;
}

/** The pointer the shell's key event carries, or null for anything else (an older shell's bare payload). */
export function pointerOf(payload: unknown): Point | null {
  const p = payload as { x?: unknown; y?: unknown } | null | undefined;
  if (!p || typeof p !== "object" || typeof p.x !== "number" || typeof p.y !== "number") return null;
  return { x: Math.round(p.x), y: Math.round(p.y) };
}

export type ClickButton = "left" | "right";
/** A click the shell's hook raised (W2b): the point in physical pixels and the button. */
export interface StepClick { point: Point; button: ClickButton }

/** The click the shell's `capture:step-click` carries - `{x, y, button}` - or null for anything else (ignored). */
export function clickOf(payload: unknown): StepClick | null {
  const point = pointerOf(payload);
  const button = (payload as { button?: unknown } | null)?.button;
  if (!point || (button !== "left" && button !== "right")) return null;
  return { point, button };
}
