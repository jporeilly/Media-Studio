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
export const STEPS_BAR_HEIGHT = 96;
/** The shell's events: the key press with the pointer (`{x, y}`), and the bar's buttons (`{action}`). */
export const STEP_KEY_EVENT = "capture:step-key";
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
export const KEY_TAKEN = "Shift+F8 is taken by another program: use Capture on the bar (a capture from the bar has no hotspot).";
export const BAR_NOT_HIDDEN = "This Windows cannot hide the capture bar from the picture; keep it off the captured area.";
/** The shell could not open the bar at all: the start is given up (the reason follows the colon). */
export const BAR_NOT_OPENED = "The capture bar could not be opened";

export interface GuidedOptions {
  kind: CaptureKind;
  cursor: boolean;
  /** Every step, or only those without a picture. */
  every: boolean;
  /** The monitor for a full-screen capture (null: the primary). */
  monitor: number | null;
}

export type GuidedPhase = "idle" | "starting" | "ready" | "capturing" | "finished";

/** Where a session stands: the step ids it visits, the index of the current one, and the counts for the audit row. */
export interface GuidedSession { ids: string[]; at: number; captured: number; skipped: number }

export function sessionOf(ids: string[]): GuidedSession {
  return { ids: [...ids], at: 0, captured: 0, skipped: 0 };
}

export function currentId(s: GuidedSession): string | null {
  return s.at >= 0 && s.at < s.ids.length ? s.ids[s.at] : null;
}

export function atEnd(s: GuidedSession): boolean {
  return s.at >= s.ids.length;
}

/** The session moved on after a capture or a skip. */
export function advance(s: GuidedSession, outcome: "captured" | "skipped"): GuidedSession {
  if (atEnd(s)) return s;
  return { ...s, at: s.at + 1, captured: s.captured + (outcome === "captured" ? 1 : 0), skipped: s.skipped + (outcome === "skipped" ? 1 : 0) };
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

/** The top-most window whose frame holds the point (the shell lists windows top-most first), or null. */
export function windowUnder(windows: WindowInfo[], point: Point): WindowInfo | null {
  return windows.find((w) => point.x >= w.x && point.x < w.x + w.width && point.y >= w.y && point.y < w.y + w.height) ?? null;
}

export type CaptureTarget = { rect: Rect; title: string | null } | { error: string };

/**
 * What one capture grabs: the region chosen at the start, the window under the pointer (the front-most one when
 * the capture came from the bar's button, which carries no pointer), or the chosen monitor.
 */
export function captureTarget(
  kind: CaptureKind,
  input: { region: Region | null; monitors: MonitorInfo[]; monitor: number | null; windows: WindowInfo[]; pointer: Point | null },
): CaptureTarget {
  if (kind === "region") {
    if (!input.region) return { error: "No region was chosen." };
    const r = input.region;
    return { rect: { x: r.x, y: r.y, width: r.width, height: r.height }, title: r.window_title };
  }
  if (kind === "window") {
    const w = input.pointer ? windowUnder(input.windows, input.pointer) : input.windows[0] ?? null;
    if (!w) return { error: NO_WINDOW_UNDER_POINTER };
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
  /** A refusal the bar shows in red under the step - the key taken, the bar not hidden from the picture - or null. */
  message: string | null;
}

/**
 * The bar's state: what the bar page shows (`public/capture-bar.html`, steps mode) and the builder's own copy. The
 * hook sends it on every change and again when the bar asks (`capture:control` `hello`, sent once the bar's
 * listener is up: the first state would otherwise be missed, the page loading after the hook's first send).
 */
export function barPayload(name: string, doc: WalkthroughDocument, s: GuidedSession, phase: GuidedPhase, message: string | null = null): BarPayload {
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
    message,
  };
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
