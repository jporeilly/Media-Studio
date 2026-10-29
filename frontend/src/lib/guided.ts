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
 * The counts: `captured` is every capture that landed (a key, a click, a typed pause or the bar's button), `taken` the
 * DISTINCT steps that got a picture this run (a retake is not a second step - `capturedSteps`), `byClick` the captures
 * the click hook made, `byTyping` those a typed pause made, `skipped` the DISTINCT steps skipped and not captured since
 * (`passed` holds their ids), `landed` how many captures landed on each step this run (`stillsThisStep`).
 *
 * THE STEP IS YOURS UNTIL NEXT (W2d, R1). A step is seldom one click: "right-click Valid rows and choose Preview rows"
 * is two, and any click on the way (focusing a window, an OK) used to drift the run by a step. So by default
 * (`advance: "next"`) a capture lands on the CURRENT step and the session does not move on - `claimStep` keeps `at`,
 * the id being claimed at the moment of the capture so a still in flight lands on the step it was taken for whatever
 * Next does meanwhile - and every capture until Next re-takes the step's picture through the replace-picture path, the
 * chain keeping the order, so the last still stands (the hook removes a superseded still from Captures). Next - the
 * bar's button, Shift+F9 (`STEP_NEXT_EVENT`, `nextHotkey`), the builder's line - moves on (`advance(s, "next")`): the
 * step is passed (skipped) only when it got no picture this run, and the one button reads Next or Skip by that
 * (`nextLabel`). `advance: "capture"` is 0.18.0's one capture per step: the session moves on at the claim.
 *
 * THE STILL AFTER THE CLICK (R2): with `settleMs` > 0 a CLICK's still is taken that long after the press (the hook
 * waits `delay`; the step is claimed and the in-flight count taken at once), so the picture shows what the click did -
 * a menu, a dialog - with the hotspot where you clicked. The key, the bar's button and a typed pause take it at once.
 *
 * TYPING TAKES A STILL (R3): with `typing` the shell's keyboard hook raises `capture:step-typed` a second after the
 * last typing key, with the pointer - and nothing about any key: which keys you press is never recorded. The hook
 * captures with the step's last click point this run as the hotspot (in `next` mode; the event's pointer otherwise).
 */
import type { Size } from "./annotate";
import { type CaptureKind, type MonitorInfo, type Region, type WindowInfo, monitorFor } from "./capture";
import { type ClickKind, type Hotspot, type WalkthroughDocument, HOTSPOT_DEFAULT, clampHotspot, stepIndex, stepLabel } from "./walkthrough";

export interface Point { x: number; y: number }
export interface Rect { x: number; y: number; width: number; height: number }

/** The hotspot a guided capture draws round the pointer, in picture pixels (the server clamps it again): the walkthrough's one default (Add hotspot uses it too, F2). */
export { HOTSPOT_DEFAULT };
/** The capture bar's size in its second mode, physical pixels at scale 1 (the recorder's is 300 x 56): the thumbnail box at the left (W2d). */
export const STEPS_BAR_WIDTH = 760;
export const STEPS_BAR_HEIGHT = 112;
/** How long after a click its still is taken when "the still after the click" is ticked (R2), ms. */
export const SETTLE_MS = 500;
/**
 * The shell's events: the key press with the pointer (`{x, y}`), a click (`{x, y, button}`), Next (Shift+F9, no
 * payload), a typed pause (`{x, y, window}`: the pointer and the foreground window's frame - never a key), and the bar's buttons (`{action}`).
 */
export const STEP_KEY_EVENT = "capture:step-key";
export const STEP_CLICK_EVENT = "capture:step-click";
export const STEP_NEXT_EVENT = "capture:step-next";
export const STEP_TYPED_EVENT = "capture:step-typed";
export const CONTROL_EVENT = "capture:control";
export const STATE_EVENT = "capture:state";
/** The bar's second mode, as `capture_bar_open` and the state payload name it. */
export const BAR_MODE = "steps";
/** The key set `capture_hotkeys_start` is asked for: Shift+F8 (capture) and Shift+F9 (next), each registered on its own (F3). */
export const KEYS_MODE = "steps";
/** The shell's pause after the last typing key before its still (`TYPING_PAUSE_MS` in capture.rs), mirrored for the page's own rule (G2). */
export const TYPING_PAUSE_MS = 1000;
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
/** Shift+F8 taken by another program (the keys are registered one by one, F3: each is named on its own). */
export const KEY_TAKEN = "Shift+F8 is taken by another program: use Capture on the bar (a capture from the bar has no hotspot).";
/** The same refusal when the clicks are live (H1): a click captures whoever holds the key. */
export const KEY_TAKEN_CLICK = "Shift+F8 is taken by another program: click to capture, or use Capture on the bar.";
/** Shift+F9 taken (F3): Next is on the bar. */
export const NEXT_KEY_TAKEN = "Shift+F9 is taken by another program: use Next on the bar.";
/** Both keys taken: one line naming both, by whether the clicks are live. */
export const KEYS_TAKEN = "Shift+F8 and Shift+F9 are taken by another program: use Capture and Next on the bar (a capture from the bar has no hotspot).";
export const KEYS_TAKEN_CLICK = "Shift+F8 and Shift+F9 are taken by another program: click to capture, or use Capture and Next on the bar.";
/** Shift+F8 taken and the clicks off with the tick on (H1): ONE line naming both - the bar's Capture is the one way left. */
export const KEYS_AND_CLICKS_OFF =
  "Shift+F8 is taken by another program and clicks could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).";
/** Shift+F8 taken and both hooks off (W2d): still one line. */
export const KEYS_AND_HOOKS_OFF =
  "Shift+F8 is taken by another program and clicks and typing could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).";
/** The keyboard hook asked for and not up (R3): a typed step is captured by a click, the key or the button. */
export const TYPING_OFF = "Typing could not be captured on this computer: click, press Shift+F8 or use the bar's Capture for a typed step.";
/** The same beside a line that says Shift+F8 is taken (G5): never "press Shift+F8" there - a click, or the bar's Capture. */
export const TYPING_OFF_CLICK = "Typing could not be captured on this computer: click, or use the bar's Capture for a typed step.";
export const TYPING_OFF_BAR = "Typing could not be captured on this computer: use the bar's Capture for a typed step.";
/** Both hooks asked for and neither up: one line. */
export const HOOKS_OFF = "Clicks and typing could not be captured on this computer: capture with Shift+F8 or the bar's Capture.";
export const BAR_NOT_HIDDEN = "This Windows cannot hide the capture bar from the picture; keep it off the captured area.";
/** The shell could not open the bar at all: the start is given up (the reason follows the colon). */
export const BAR_NOT_OPENED = "The capture bar could not be opened";
/** A click outside the region's frame (W2b): no step; shown on the bar until the next payload. */
export const CLICK_OUTSIDE_FRAME = "Click inside the frame to capture this step.";
/** A click off the chosen monitor in Full screen (W2b-2): no step; shown on the bar until the next payload. */
export const CLICK_OUTSIDE_SCREEN = "Click on the captured screen to capture this step.";
/**
 * A typed pause whose window (the one typed into) is off a Region's frame, off the Full screen's monitor, or - in the
 * Window kind (G1) - off the rectangle of the step's last still this run: no still.
 */
export const TYPED_OUTSIDE_FRAME = "Typing outside the frame takes no still.";
export const TYPED_OUTSIDE_SCREEN = "Typing off the captured screen takes no still.";
export const TYPED_OUTSIDE_WINDOW = "Typing outside the step's window takes no still.";
/** The bar's line for a typed pause outside the kind's area. */
export function typedOutsideLine(kind: CaptureKind): string {
  return kind === "screen" ? TYPED_OUTSIDE_SCREEN : kind === "window" ? TYPED_OUTSIDE_WINDOW : TYPED_OUTSIDE_FRAME;
}
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

/** Whether Shift+F8, Shift+F9, the click hook and the keyboard hook went up (H1, W2d, F3): what `capture_hotkeys_start` answers. */
export interface KeysUp { key: boolean; next: boolean; clicks: boolean; typing: boolean }

/**
 * Any shell's answer to `capture_hotkeys_start`, read: `{key, next, clicks, typing}` from a shell since the W2d fix
 * round (the two keys registered one by one, F3); `{key, clicks, typing}` from the W2d shell before it, whose keys
 * were one set (`next` follows `key`); `{key, clicks}` from one since H1 (no keyboard hook: the typing off); a boolean
 * from the W2b shell before it (true: everything asked is up; false: the key up, the hook refused); nothing
 * (`undefined`) from the 0.17.0 shell, which has no hook at all - the key up, the clicks and the typing off (the
 * in-app updater refreshes the page, never the shell, so a newer page can meet that shell); a refusal (`refused`)
 * from a shell before H1 whose key was taken - it installed no hook either - or from any shell while the other key
 * set is held: all off.
 */
export function keysUpOf(answer: unknown, refused: boolean): KeysUp {
  if (refused) return { key: false, next: false, clicks: false, typing: false };
  if (answer && typeof answer === "object") {
    const a = answer as { key?: unknown; next?: unknown; clicks?: unknown; typing?: unknown };
    const key = a.key !== false;
    return { key, next: a.next === undefined ? key : a.next !== false, clicks: a.clicks === true, typing: a.typing === true };
  }
  if (typeof answer === "boolean") return { key: true, next: true, clicks: answer, typing: false };
  return { key: true, next: true, clicks: false, typing: false };
}

/**
 * What the start says about the keys, the clicks and the typing (H1, W2d, F3), and which are live: the clicks only
 * when asked AND the hook is up (so the bar's and the builder's "Click to capture" hint is shown only then), the
 * typing likewise. Each key is named on its own: `KEY_TAKEN_CLICK` / `KEY_TAKEN` for Shift+F8 (by whether the clicks
 * are live), `NEXT_KEY_TAKEN` for Shift+F9, `KEYS_TAKEN_CLICK` / `KEYS_TAKEN` for both; `CLICKS_OFF` / `TYPING_OFF`
 * when a hook was asked and is not up - both: ONE line, `HOOKS_OFF`. Shift+F8 taken AND the clicks off with the tick
 * on: ONE line, `KEYS_AND_CLICKS_OFF` (the two single lines would each point at the other's way) -
 * `KEYS_AND_HOOKS_OFF` when the typing is off as well - and Shift+F9's own line after it when that is taken too.
 */
export function keysLines(up: KeysUp, clicksAsked: boolean, typingAsked = false): { clicks: boolean; typing: boolean; lines: string[] } {
  const clicks = clicksAsked && up.clicks;
  const typing = typingAsked && up.typing;
  const clicksOff = clicksAsked && !up.clicks;
  const typingOff = typingAsked && !up.typing;
  const lines: string[] = [];
  if (!up.key && clicksOff) {
    lines.push(typingOff ? KEYS_AND_HOOKS_OFF : KEYS_AND_CLICKS_OFF);
    if (!up.next) lines.push(NEXT_KEY_TAKEN);
    return { clicks, typing, lines };
  }
  if (!up.key && !up.next) lines.push(clicks ? KEYS_TAKEN_CLICK : KEYS_TAKEN);
  else if (!up.key) lines.push(clicks ? KEY_TAKEN_CLICK : KEY_TAKEN);
  else if (!up.next) lines.push(NEXT_KEY_TAKEN);
  if (clicksOff && typingOff) lines.push(HOOKS_OFF);
  else if (clicksOff) lines.push(CLICKS_OFF);
  // The typing line never says "press Shift+F8" beside a line that says the key is taken (G5).
  else if (typingOff) lines.push(up.key ? TYPING_OFF : clicks ? TYPING_OFF_CLICK : TYPING_OFF_BAR);
  return { clicks, typing, lines };
}
/** The shell took the key but could not install the click hook (W2b): the capture goes on by the key and the button. */
export const CLICKS_OFF = "Clicks could not be captured on this computer: capture with Shift+F8 or the bar's Capture.";
/** The bar's second line in click mode (W2b). */
export const CLICK_HINT = "Click to capture · Shift+F8 captures without a click";
/** The clause the hint gains while the typing hook is up (W2d, R3). */
export const TYPING_HINT = "typing captures a second after you stop";
/** The bar's hint line by what is live: the click hint, with the typing clause when typing is; typing alone names the key; neither, nothing. */
export function hintLine(clicks: boolean, typing: boolean): string {
  if (clicks) return typing ? `${CLICK_HINT} · ${TYPING_HINT}` : CLICK_HINT;
  return typing ? `Shift+F8 captures · ${TYPING_HINT}` : "";
}

/** When the session moves on (W2d, R1): at Next (the default: every capture until then re-takes the step), or at each capture (0.18.0). */
export type GuidedAdvance = "next" | "capture";
/**
 * The guided capture's two modes (F1): "steps" pictures the walkthrough's steps in order (Capture steps…); "record"
 * makes a NEW step of every capture, appended after the last (Record…) - the same bar, keys, hooks and rules.
 */
export type GuidedMode = "steps" | "record";

export interface GuidedOptions {
  /** Capture the steps the walkthrough has, or record new ones (F1). */
  mode: GuidedMode;
  kind: CaptureKind;
  cursor: boolean;
  /** Every step, or only those without a picture. */
  every: boolean;
  /** The monitor for a full-screen capture (null: the primary). */
  monitor: number | null;
  /** Capture on click (W2b): every click in the frame takes the step's still; Shift+F8 still captures without one. */
  clicks: boolean;
  /** Move on at Next, or after each capture (R1). */
  advance: GuidedAdvance;
  /** The still after the click (R2): 0 takes it at the press, `SETTLE_MS` that long after; clicks only. */
  settleMs: number;
  /** Typing takes a still (R3): a second after you stop typing, the hotspot where you last clicked in the step. */
  typing: boolean;
}

export type GuidedPhase = "idle" | "starting" | "ready" | "capturing" | "finished";

/** Who asked for a capture: the key (or the bar's button, which carries no pointer), the click hook, or a typed pause (R3). */
export type CaptureBy = "key" | "click" | "typing";

/**
 * Where a session stands: the step ids it visits, the index of the current one, and the counts for the audit row -
 * `captured` every capture that landed (a key, a click, a typed pause, the bar's button), `taken` the distinct steps
 * that got a picture this run (a retake after Back, or a second still of the step held until Next, is not a second
 * step), `byClick` how many captures the click hook made, `byTyping` how many a typed pause made, `landed` how many
 * landed on each step this run (by id, so a still in flight lands on the step it was taken for), `passed` the
 * distinct steps skipped and `skipped` how many of those got no picture since (a step skipped, Back, skipped again
 * is one; a step skipped and then captured is not skipped).
 */
export interface GuidedSession {
  ids: string[];
  at: number;
  captured: number;
  skipped: number;
  taken: string[];
  byClick: number;
  byTyping: number;
  landed: Record<string, number>;
  passed: string[];
  /** Record mode (F1): the session visits no step - every capture makes one - and never reaches an end. */
  record: boolean;
  /** The steps made this run, in order, with who made each (record mode); Undo takes the last one back. */
  made: MadeStep[];
}

/** A step a recording made (F1): its id and who made it - the click hook, a typed pause, or the key / the bar's button. */
export interface MadeStep { id: string; by: CaptureBy }

export function sessionOf(ids: string[], record = false): GuidedSession {
  return { ids: [...ids], at: 0, captured: 0, skipped: 0, taken: [], byClick: 0, byTyping: 0, landed: {}, passed: [], record, made: [] };
}

export function currentId(s: GuidedSession): string | null {
  return s.at >= 0 && s.at < s.ids.length ? s.ids[s.at] : null;
}

/** Every step visited - never in record mode, which has no steps to visit. */
export function atEnd(s: GuidedSession): boolean {
  return !s.record && s.at >= s.ids.length;
}

/** Record mode (F1): a capture landed as a new step `id` - counted as any capture is, and kept in `made` with who made it. */
export function recordMade(s: GuidedSession, id: string, by: CaptureBy): GuidedSession {
  return { ...recordCapture(s, id, by), made: [...s.made, { id, by }] };
}

/** Undo (F1): the LAST step made this run leaves the session; the captures that landed stay counted. Unchanged with none. */
export function undoMade(s: GuidedSession): GuidedSession {
  return s.made.length ? { ...s, made: s.made.slice(0, -1) } : s;
}

/** The recorded row's counts (F1): the steps kept, and how many of them a click or a typed pause made. */
export function madeCounts(s: GuidedSession): { steps: number; byClick: number; byTyping: number } {
  return { steps: s.made.length, byClick: s.made.filter((m) => m.by === "click").length, byTyping: s.made.filter((m) => m.by === "typing").length };
}

/** The bar's label in record mode: "Recording · 3 steps". */
export function recordLabel(s: GuidedSession): string {
  const n = s.made.length;
  return `Recording · ${n} step${n === 1 ? "" : "s"}`;
}

/** What the bar's line reads in record mode, in place of a step's text. */
export const RECORD_BAR_TEXT = "Every click in the application makes a step - typing and Shift+F8 too. Undo takes the last step back; Done goes back to the builder.";

/** The click kind a recorded step gets (F1, F3): the button pressed for a click; "none" for the key, the bar's button and a typed pause. */
export function clickKindOf(by: CaptureBy, button: ClickButton | undefined): ClickKind {
  return by === "click" ? button ?? "left" : "none";
}

/** The text a recorded step starts with, by its click kind (so the player works at once; the author rewrites it after). */
export function recordedText(click: ClickKind): string {
  return click === "left" ? "Click here" : click === "right" ? "Right-click here" : click === "double" ? "Double-click here" : "";
}

/** The skipped count: the distinct steps skipped (`passed`) that got no picture since. */
function skippedOf(passed: string[], taken: string[]): number {
  return passed.filter((id) => !taken.includes(id)).length;
}

/**
 * The step a capture claims - the current one - and the session after the claim: moved on AT ONCE in `capture` mode
 * (0.18.0: the still and the attach follow, in click order when more than one is in flight), unchanged in `next` mode
 * (R1: the step is yours until Next; the id is still claimed now, so the still lands on it whatever Next does
 * meanwhile). `recordCapture` counts the one that lands. Null when every step is visited.
 */
export function claimStep(s: GuidedSession, advance: GuidedAdvance): { id: string; session: GuidedSession } | null {
  const id = currentId(s);
  if (!id) return null;
  return { id, session: advance === "capture" ? { ...s, at: s.at + 1 } : s };
}

/**
 * A capture that landed on step `id`: one more in `captured` (and in `byClick` or `byTyping` by who made it), one more
 * on the step in `landed`, the id in `taken` once however often the step is captured again, and the step no longer
 * skipped.
 */
export function recordCapture(s: GuidedSession, id: string, by: CaptureBy = "key"): GuidedSession {
  const taken = s.taken.includes(id) ? s.taken : [...s.taken, id];
  return {
    ...s, captured: s.captured + 1, byClick: s.byClick + (by === "click" ? 1 : 0), byTyping: s.byTyping + (by === "typing" ? 1 : 0),
    landed: { ...s.landed, [id]: (s.landed[id] ?? 0) + 1 }, taken, skipped: skippedOf(s.passed, taken),
  };
}

/**
 * The session moved on: after a capture (claimed and landed in one go - the `capture` mode's path when nothing else
 * is in flight), or at Next (R1: the step's id in `passed` once when it got no picture this run - a Skip - and not at
 * all when it did; `skipped` counts the distinct steps skipped and not captured since). Unchanged at the end.
 */
export function advance(s: GuidedSession, outcome: "captured" | "next", by: CaptureBy = "key"): GuidedSession {
  const claimed = claimStep(s, "capture");
  if (!claimed) return s;
  if (outcome === "captured") return recordCapture(claimed.session, claimed.id, by);
  const passed = s.taken.includes(claimed.id) || s.passed.includes(claimed.id) ? s.passed : [...s.passed, claimed.id];
  return { ...claimed.session, passed, skipped: skippedOf(passed, s.taken) };
}

/** How many DISTINCT steps got a picture this run: what "Captured N steps" counts (a retake is not a second step). */
export function capturedSteps(s: GuidedSession): number {
  return s.taken.length;
}

/** How many captures landed on the current step this run (R4: "2 stills this step" when more than one). */
export function stillsThisStep(s: GuidedSession): number {
  const id = currentId(s);
  return id ? s.landed[id] ?? 0 : 0;
}

/** The one button where Skip was (R1): "Next" when the current step got a picture this run, "Skip" otherwise; "Next" at the end. */
export function nextLabel(s: GuidedSession): "Next" | "Skip" {
  const id = currentId(s);
  return !id || s.taken.includes(id) ? "Next" : "Skip";
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
 * wording of the no-window refusal (a click will do too when the clicks are on). For a typed pause (F1) `window`
 * is given - the frame of the window typed into, which wins over the list and the pointer in the Window kind, or
 * null for no such window, which is the no-window refusal; left out for every other capture.
 */
export function captureTarget(
  kind: CaptureKind,
  input: { region: Region | null; monitors: MonitorInfo[]; monitor: number | null; windows: WindowInfo[]; pointer: Point | null; clicks?: boolean; window?: Rect | null },
): CaptureTarget {
  if (kind === "region") {
    if (!input.region) return { error: "No region was chosen." };
    const r = input.region;
    return { rect: { x: r.x, y: r.y, width: r.width, height: r.height }, title: r.window_title };
  }
  if (kind === "window") {
    if (input.window !== undefined) {
      if (!input.window) return { error: noWindowLine(!!input.clicks) };
      return { rect: { ...input.window }, title: null };
    }
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
  /** The typing is live - asked for AND the shell's keyboard hook up (R3): the hint gains " · typing captures a second after you stop". */
  typing: boolean;
  /** How many captures landed on the current step this run (R4): the bar says "2 stills this step" past one. */
  stillsThisStep: number;
  /** The LAST capture that landed in the run, whichever step: its number and a thumbnail (a data URL made by the page); null before the first, or when none could be made. */
  lastStill: LastStill | null;
  /** Record mode (F1): the label reads "Recording · N steps", the one button is Undo, Back is hidden. */
  record: boolean;
  /** Undo may be pressed: a step was made this run and nothing is in flight (record mode only). */
  canUndo: boolean;
}

/** The last still of the run, as the bar shows it (R4): the step's number and a JPEG data URL `THUMB_WIDTH` wide. */
export interface LastStill { step: number; thumb: string }

/**
 * The bar's state: what the bar page shows (`public/capture-bar.html`, steps mode) and the builder's own copy. The
 * hook sends it on every change and again when the bar asks (`capture:control` `hello`, sent once the bar's
 * listener is up: the first state would otherwise be missed, the page loading after the hook's first send).
 */
export function barPayload(
  name: string, doc: WalkthroughDocument, s: GuidedSession, phase: GuidedPhase, message: string | null = null, clicks = false, typing = false,
  lastStill: LastStill | null = null,
): BarPayload {
  if (s.record) {
    return {
      mode: BAR_MODE, phase, name, label: recordLabel(s), text: RECORD_BAR_TEXT, instruction: "",
      remaining: 0, canBack: false, canCapture: phase === "ready",
      message, clicks, typing, stillsThisStep: 0, lastStill, record: true, canUndo: phase === "ready" && s.made.length > 0,
    };
  }
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
    message, clicks, typing, stillsThisStep: stillsThisStep(s), lastStill, record: false, canUndo: false,
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

/** Shift+F9 exactly: Next (W2d; the shell's global key, and the page's own when the builder has the focus). */
export function nextHotkey(e: { key: string; shiftKey: boolean; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean }): boolean {
  return e.key === "F9" && e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey;
}

/** A wait of `ms` (R2: the still after the click); the tests fake the timers. */
export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

/** A typed pause as the shell raises it (F1): the pointer, and the frame of the window typed into - the foreground window - or null for each. */
export interface StepTyped { point: Point | null; window: Rect | null }

/** The typed pause `capture:step-typed` carries - `{x, y, window: {x, y, width, height} | null}`; an older shell's bare pointer has no window. */
export function typedOf(payload: unknown): StepTyped {
  const point = pointerOf(payload);
  const w = (payload as { window?: unknown } | null)?.window as { x?: unknown; y?: unknown; width?: unknown; height?: unknown } | null | undefined;
  const window =
    w && typeof w === "object" && typeof w.x === "number" && typeof w.y === "number" && typeof w.width === "number" && typeof w.height === "number" && w.width > 0 && w.height > 0
      ? { x: Math.round(w.x), y: Math.round(w.y), width: Math.round(w.width), height: Math.round(w.height) }
      : null;
  return { point, window };
}

/** Whether two rectangles share a pixel (half-open on both axes). */
export function rectsIntersect(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

/**
 * The area a typed pause's window must meet, per kind (F1, G1): a Region's frame or the Full screen's monitor (the
 * click area), or - for a Window - the rectangle of the step's LAST STILL this run (`lastRect`; none before the step's
 * first still, so the window typed into is taken then).
 */
export function typedArea(kind: CaptureKind, clickArea: Rect | null, lastRect: Rect | null): Rect | null {
  return kind === "window" ? lastRect : clickArea;
}

/**
 * What a typed pause does, by the window typed into (F1, G1): with an area (`typedArea`) and a window that does not
 * meet it, no still ("outside", the bar says so with `typedOutsideLine`) - so a reply typed in a chat window takes
 * nothing in any kind; no window known (an older shell, no foreground window) or no area (a Window step with no still
 * yet this run) is the still as before. In the Window kind the still is of the window typed into (`captureTarget`).
 */
export function typedRule(kind: CaptureKind, area: Rect | null, window: Rect | null): "capture" | "outside" {
  void kind;
  if (area && window && !rectsIntersect(window, area)) return "outside";
  return "capture";
}

/**
 * Whether a typed pause arriving at `now` fires within the pause of a Next pressed at `nextAt` (G2): the user typed,
 * then pressed Next within the second, so the still belongs to the step just left and is dropped. Never pressed: no.
 */
export function typedAfterNext(nextAt: number | null, now: number): boolean {
  return nextAt !== null && now - nextAt < TYPING_PAUSE_MS;
}

/**
 * Where a typed still's hotspot goes (F1): the step's last click this run when it lies in the captured rectangle, else
 * the pointer when it does, else nowhere (null: no hotspot).
 */
export function typedHotspotPoint(lastClick: Point | null, pointer: Point | null, rect: Rect): Point | null {
  if (lastClick && insideFrame(lastClick, rect)) return lastClick;
  if (pointer && insideFrame(pointer, rect)) return pointer;
  return null;
}
