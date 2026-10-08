/**
 * Screen capture (T3): the pure half - what the recorder is allowed to do next, which key means what,
 * which chunks still have to reach the server, what a recording is called, where the bar goes. The
 * wiring (MediaRecorder, the overlay, the bar window, the uploads) is `context/CaptureContext.tsx`.
 * Unit-tested in capture.test.ts.
 *
 * THE HIDE RULE. The feature exists only inside the desktop shell: the page needs the shell's commands
 * (the region overlay, the recorder bar, the hotkeys) and the backend needs to be on the machine whose
 * screen is captured. `isDesktopShell()` reads `window.isTauri`, which Tauri's own initialisation script
 * defines on EVERY page the shell loads - the http page the backend serves included, where it was measured
 * present while `invoke` was still refused - before any script of ours runs. It was preferred to a custom
 * user agent because it needs no window configuration that a later shell could drop, cannot arrive by
 * accident from a browser (no browser defines it), and is the same flag Tauri's own API uses to decide
 * whether it is at home. `hasShellBridge()` (`captureAvailable()`) is the stronger question - is the
 * shell's bridge there - but a page the shell's capability REFUSES has the bridge too (measured: `isTauri`
 * and `__TAURI__` both present, every command refused). So THE hide rule is `useCaptureAvailable()`
 * (lib/useCaptureAvailable.ts): the bridge AND the shell's answer to one capture command (`probeCapture`,
 * asked once). The Dashboard's Capture tile, the Projects page's Capture button, its dialog, its unfinished
 * recordings and its Captures card all ask it and nothing else, so a refused page hides the feature
 * everywhere rather than offering buttons that fail. (`isDesktopShell()` alone decides only the browser's
 * one line.)
 */

export type CaptureKind = "region" | "window" | "screen";
export type CaptureMode = "record" | "still";

/** A rectangle in PHYSICAL virtual-screen pixels, as the shell's overlay reports it. */
export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
  monitor: number;
  window_title: string | null;
  hwnd: number | null;
}

export interface MonitorInfo {
  index: number;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  scale: number;
  primary: boolean;
}

/** What the dialog asks for. (No pointer switch: see RECORDING_CURSOR_NOTE.) */
export interface RecordOptions {
  kind: CaptureKind;
  microphone: boolean;
  microphoneId: string | null;
  systemSound: boolean;
}

export interface StillOptions {
  kind: CaptureKind;
  cursor: boolean;
  delaySeconds: number;
  monitor: number | null;
}

/** The recorder's limits and rhythms. */
export const MAX_RECORDING_MS = 2 * 60 * 60 * 1000;
export const TIMESLICE_MS = 5000;
/**
 * What MediaRecorder is asked for, whatever the picture's size: two hours of it is about 7.4 GB, inside the
 * server's per-recording cap (services/recordings.py MAX_RECORDING_BYTES, 16 GiB) with room for an encoder
 * that overshoots.
 */
export const VIDEO_BITS_PER_SECOND = 8_000_000;
export const AUDIO_BITS_PER_SECOND = 192_000;
/**
 * How often the recorder tells the server it is still making the recording - paused, or between chunks -
 * so the recording stays LIVE there (the server's window is 20 s) and the Projects page cannot save or
 * discard it from under the recorder.
 */
export const HEARTBEAT_MS = 5000;
export const COUNTDOWN_SECONDS = 3;
export const STILL_DELAYS = [0, 3, 5, 10] as const;
export const FRAME_RATE = 30;
/** The recorder bar's size in physical pixels at scale 1 (the page scales with devicePixelRatio). */
export const BAR_WIDTH = 300;
export const BAR_HEIGHT = 56;
export const BAR_MARGIN = 12;

export const RECORDING_JOB_KIND = "recording";
export const LIMIT_MESSAGE = "The recording reached the two-hour limit and was stopped.";
/** Said when the shell refused to close the window because a recording is in flight. */
export const CLOSE_BLOCKED_MESSAGE = "A recording is in progress: stop it first (Shift+F10, or Stop on the recorder bar), then close the window.";
/** Said when the shell refused to close the window because a recording is being saved. */
export const SAVE_CLOSE_BLOCKED_MESSAGE = "The recording is being saved as a project: wait until it opens, then close the window.";
/** Said when the save job could not be asked about MAX_POLL_FAILURES times running: the guard comes down. */
export const SAVE_UNREACHED_MESSAGE =
  "The server stopped answering while the recording was being saved, so closing the window is allowed again. If the save did not finish, the recording is offered under Recordings not saved yet when the app next starts.";
/** Added to a failure's line when what reached the server is kept. */
export const KEPT_LINE = "What was recorded up to then is kept: save it from the Projects page.";
export const SERVER_EDITION_LINE = "Screen capture is available in the desktop app only: it needs the app and the screen on one machine.";

// ---- the shell ----------------------------------------------------------------------------------------

interface TauriLike {
  core?: { invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  event?: {
    listen?: (name: string, handler: (event: { payload: unknown }) => void) => Promise<() => void>;
    emitTo?: (target: string, name: string, payload?: unknown) => Promise<void>;
  };
  window?: { getCurrentWindow?: () => { minimize: () => Promise<void>; unminimize: () => Promise<void>; setFocus: () => Promise<void> } };
}

type ShellWindow = Window & { isTauri?: unknown; __TAURI__?: TauriLike };

/** Inside the desktop shell: Tauri's injected flag (see the module note). */
export function isDesktopShell(w: unknown = typeof window === "undefined" ? undefined : window): boolean {
  return !!w && (w as ShellWindow).isTauri === true;
}

/** The shell's bridge, when the page can call it. */
export function shellBridge(w: unknown = typeof window === "undefined" ? undefined : window): TauriLike | null {
  if (!isDesktopShell(w)) return null;
  const t = (w as ShellWindow).__TAURI__;
  return t && t.core && typeof t.core.invoke === "function" && t.event ? t : null;
}

export function hasShellBridge(w?: unknown): boolean {
  return shellBridge(w) !== null;
}

/** Whether the page has the shell's bridge at all: the first half of the hide rule (see the module note). */
export function captureAvailable(w?: unknown): boolean {
  return hasShellBridge(w);
}

// The shell's answer to one capture command, per bridge: asked once, on first use.
const probes = new WeakMap<object, Promise<boolean>>();
const answers = new WeakMap<object, boolean>();

/**
 * The second half of the hide rule: does the shell actually GRANT this page the capture commands? A shell
 * whose capability refuses the page still has `isTauri` and a `__TAURI__` bridge (measured: both present on
 * a refused page), so the bridge alone would show buttons that then fail. One cheap, side-effect-free
 * command - `capture_monitors` - is asked once per bridge; a refusal (or any failure) answers false.
 */
export function probeCapture(w?: unknown): Promise<boolean> {
  const bridge = shellBridge(w);
  if (!bridge) return Promise.resolve(false);
  let probe = probes.get(bridge);
  if (!probe) {
    probe = bridge.core!.invoke!("capture_monitors").then(() => true, () => false).then((ok) => {
      answers.set(bridge, ok);
      return ok;
    });
    probes.set(bridge, probe);
  }
  return probe;
}

/** The probe's answer when it is in, else undefined (not asked yet, or not answered). */
export function knownCapture(w?: unknown): boolean | undefined {
  const bridge = shellBridge(w);
  return bridge ? answers.get(bridge) : false;
}

// ---- the recorder's state machine --------------------------------------------------------------------

export type RecorderState = "idle" | "picking" | "countdown" | "recording" | "paused" | "saving" | "done" | "failed";
export type RecorderEvent = "pick" | "cancel" | "armed" | "start" | "pause" | "resume" | "stop" | "saved" | "fail" | "reset";

const TRANSITIONS: Record<RecorderState, Partial<Record<RecorderEvent, RecorderState>>> = {
  idle: { pick: "picking", fail: "failed" },
  picking: { armed: "countdown", cancel: "idle", fail: "failed" },
  countdown: { start: "recording", cancel: "idle", fail: "failed" },
  recording: { pause: "paused", stop: "saving", fail: "failed" },
  paused: { resume: "recording", stop: "saving", fail: "failed" },
  saving: { saved: "done", fail: "failed" },
  done: { reset: "idle", pick: "picking" },
  failed: { reset: "idle", pick: "picking" },
};

/** The state after `event`, or null when the event means nothing in `state` (and is ignored). */
export function recorderNext(state: RecorderState, event: RecorderEvent): RecorderState | null {
  return TRANSITIONS[state][event] ?? null;
}

/** The states a recording is in flight: leaving the page would lose it. */
export function shouldGuardUnload(state: RecorderState): boolean {
  return state === "countdown" || state === "recording" || state === "paused" || state === "saving";
}

/**
 * Whether the shell refuses to close the window: while a recording is in flight, and while its SAVE runs
 * - closing the window stops the backend, and with it the job turning the chunks into a project (the
 * chunks survive and are offered again, but the user should not have to).
 */
export function closeGuardUp(state: RecorderState, saveRunning: boolean): boolean {
  return shouldGuardUnload(state) || saveRunning;
}

/** What a refused close is explained with. */
export function closeBlockedMessage(state: RecorderState): string {
  return state === "countdown" || state === "recording" || state === "paused" ? CLOSE_BLOCKED_MESSAGE : SAVE_CLOSE_BLOCKED_MESSAGE;
}

/** What the bar offers: Pause/Resume and Stop while recording; Cancel during the countdown. */
export function barControls(state: RecorderState): { pause: "pause" | "resume" | null; stop: boolean; cancel: boolean } {
  if (state === "recording") return { pause: "pause", stop: true, cancel: false };
  if (state === "paused") return { pause: "resume", stop: true, cancel: false };
  if (state === "countdown") return { pause: null, stop: false, cancel: true };
  return { pause: null, stop: false, cancel: false };
}

// ---- keys -------------------------------------------------------------------------------------------

export type HotkeyAction = "pause" | "stop";

/** Snagit's keys: Shift+F9 pause/resume, Shift+F10 stop - exactly those, no other modifier. */
export function hotkeyAction(e: { key: string; shiftKey: boolean; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean }): HotkeyAction | null {
  if (!e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return null;
  if (e.key === "F9") return "pause";
  if (e.key === "F10") return "stop";
  return null;
}

/** What a hotkey does in a state: pause toggles, stop stops - and cancels the countdown; nothing elsewhere. */
export function hotkeyEvent(state: RecorderState, action: HotkeyAction): RecorderEvent | null {
  if (action === "stop") return state === "recording" || state === "paused" ? "stop" : state === "countdown" ? "cancel" : null;
  if (state === "recording") return "pause";
  if (state === "paused") return "resume";
  return null;
}

// ---- chunks -----------------------------------------------------------------------------------------

/**
 * The chunks' ledger: every chunk gets the next sequence number as MediaRecorder hands it over, is kept
 * until the server acknowledges it, and is sent again at Finish if it never was - the server refuses to
 * assemble while a number is missing, so nothing is lost silently and nothing is kept in memory once it
 * is safe on disk.
 */
export interface ChunkLedger {
  next: number;
  pending: Map<number, Blob>;
  acked: Set<number>;
  bytes: number;
}

export function emptyLedger(): ChunkLedger {
  return { next: 0, pending: new Map(), acked: new Set(), bytes: 0 };
}

export function ledgerAdd(ledger: ChunkLedger, blob: Blob): { ledger: ChunkLedger; seq: number } {
  const seq = ledger.next;
  const pending = new Map(ledger.pending);
  pending.set(seq, blob);
  return { ledger: { ...ledger, next: seq + 1, pending, bytes: ledger.bytes + blob.size }, seq };
}

export function ledgerAck(ledger: ChunkLedger, seq: number): ChunkLedger {
  const pending = new Map(ledger.pending);
  pending.delete(seq);
  const acked = new Set(ledger.acked);
  acked.add(seq);
  return { ...ledger, pending, acked };
}

/** The chunks the server has not acknowledged, in order: what Finish sends again first. */
export function ledgerUnsent(ledger: ChunkLedger): number[] {
  return [...ledger.pending.keys()].sort((a, b) => a - b);
}

/** How many chunks the server must hold before it may assemble. */
export function expectedChunks(ledger: ChunkLedger): number {
  return ledger.next;
}

/** Whether a recorder that fails keeps the recording: once the server holds a chunk, it is not thrown away. */
export function failureKeeps(ledger: ChunkLedger): boolean {
  return ledger.acked.size > 0;
}

/**
 * Whether a refused chunk means the recording cannot go on: 507 (the disk is nearly full) and 413 (the
 * recording reached its cap). The recorder then stops with the server's own line and keeps what arrived.
 * Anything else - a dropped connection - is kept in the ledger and sent again at Stop.
 */
export function chunkRefusalStops(status: number | null | undefined): boolean {
  return status === 507 || status === 413;
}

// ---- names and numbers -------------------------------------------------------------------------------

const two = (n: number) => String(n).padStart(2, "0");

/** A window's title, else "Screen recording 2026-10-06 11-42" in local time. */
export function recordingName(kind: CaptureKind, windowTitle: string | null | undefined, now: Date = new Date()): string {
  const title = (windowTitle || "").trim();
  if (kind === "window" && title) return title.slice(0, 120);
  return `Screen recording ${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())} ${two(now.getHours())}-${two(now.getMinutes())}`;
}

export function stillName(kind: CaptureKind, windowTitle: string | null | undefined, now: Date = new Date()): string {
  const title = (windowTitle || "").trim();
  if (kind === "window" && title) return title.slice(0, 120);
  return `Screen capture ${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())} ${two(now.getHours())}-${two(now.getMinutes())}-${two(now.getSeconds())}`;
}

/** "0:07", "12:40", "1:02:03". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  return h > 0 ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
}

/**
 * A region cut to the monitor it was picked on: a window snapped to on the overlay may run past its
 * monitor's edge (its whole frame is reported), and a region is cut from ONE screen's picture - past the
 * edge, ffmpeg moves the crop or fails, and the server refuses it (400). Null when nothing is left on that
 * monitor, or the monitor is unknown.
 */
export function clipToMonitor(region: Region, monitors: MonitorInfo[]): Region | null {
  const m = monitors.find((x) => x.index === region.monitor);
  if (!m) return null;
  const x0 = Math.max(region.x, m.x), y0 = Math.max(region.y, m.y);
  const x1 = Math.min(region.x + region.width, m.x + m.width), y1 = Math.min(region.y + region.height, m.y + m.height);
  if (x1 - x0 < 1 || y1 - y0 < 1) return null;
  return { ...region, x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/**
 * Why a region recording cannot go ahead with what was shared, or null. A region is cut from a SCREEN's
 * picture at the monitor's offsets: a WINDOW share (it has a handle) is another picture altogether - the
 * crop would land on the wrong part, or fail - and so is another screen than the one the region was drawn
 * on. Other kinds crop nothing.
 */
export function regionShareProblem(kind: CaptureKind, region: Region | null, hwnd: number | null, monitor: MonitorInfo | null): string | null {
  if (kind !== "region") return null;
  if (!region || hwnd !== null || monitor?.index !== region.monitor) return WRONG_SCREEN_MESSAGE;
  return null;
}

/** The region in the captured monitor's own pixels (the backend crops with it), or null for the whole picture. */
export function cropForRegion(region: Region | null, monitor: MonitorInfo | null): { x: number; y: number; width: number; height: number } | null {
  if (!region || !monitor) return null;
  return { x: Math.max(0, region.x - monitor.x), y: Math.max(0, region.y - monitor.y), width: region.width, height: region.height };
}

/**
 * The getDisplayMedia constraints. System sound comes as a loopback track that DEFAULTS to mono with
 * echo cancellation, noise suppression and automatic gain - voice processing that pulled a steady tone
 * from 0.57 to 0.04 RMS in two seconds (measured) - and `applyConstraints` cannot change it on the live
 * track, so processing is switched off and two channels asked for here. The picture is asked for at
 * FRAME_RATE; Chromium emits a frame only when the screen changes, and the backend restores a constant
 * rate when it makes the MP4.
 */
export function displayConstraints(systemSound: boolean): MediaStreamConstraints {
  return {
    video: { frameRate: FRAME_RATE },
    audio: systemSound ? { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2 } : false,
  };
}

/** The microphone's constraints: voice processing ON, this is a voice. */
export function microphoneConstraints(deviceId: string | null): MediaStreamConstraints {
  return { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, ...(deviceId ? { deviceId: { exact: deviceId } } : {}) }, video: false };
}

/** What to tell the user the share dialog will ask, per kind. */
export function pickerHint(kind: CaptureKind): string {
  if (kind === "window") return "The share dialog opens: choose the Window tab, pick the window, then Share. Turn on \"Share with system audio\" there if you want the sound.";
  if (kind === "region") return "Drag the region on the frozen screen first. Then the share dialog opens: choose Entire Screen, pick the screen you drew on, then Share. Turn on \"Share with system audio\" there if you want the sound.";
  return "The share dialog opens: choose Entire Screen, pick the screen, then Share. Turn on \"Share with system audio\" there if you want the sound.";
}

/** Why a recording's pointer cannot be left out (measured: `cursor: "never"` is ignored, the track reports "always"). */
export const RECORDING_CURSOR_NOTE = "A recording always shows the pointer: the share dialog's capture draws it and cannot be told not to. The Cursor switch applies to stills.";

/**
 * Where the recorder bar goes, in physical pixels: under a region when there is room on its monitor,
 * else above it, else the monitor's bottom-right corner; for a whole screen or a window, the monitor's
 * bottom-right. Always inside the monitor. The bar is excluded from capture by the shell, so this is about
 * the user's eye, not the picture.
 */
export function barPlacement(monitor: MonitorInfo, region: Region | null, width = BAR_WIDTH, height = BAR_HEIGHT): { x: number; y: number } {
  const right = monitor.x + monitor.width, bottom = monitor.y + monitor.height;
  const clampX = (x: number) => Math.min(right - width - BAR_MARGIN, Math.max(monitor.x + BAR_MARGIN, x));
  if (region) {
    const below = region.y + region.height + BAR_MARGIN;
    if (below + height <= bottom - BAR_MARGIN) return { x: clampX(region.x), y: below };
    const above = region.y - BAR_MARGIN - height;
    if (above >= monitor.y + BAR_MARGIN) return { x: clampX(region.x), y: above };
  }
  return { x: right - width - BAR_MARGIN, y: bottom - height - BAR_MARGIN - 48 };
}

/** The monitor a region lies on (its index), else the primary, else the first. */
export function monitorFor(monitors: MonitorInfo[], index: number | null): MonitorInfo | null {
  if (monitors.length === 0) return null;
  return monitors.find((m) => m.index === index) ?? monitors.find((m) => m.primary) ?? monitors[0];
}

// ---- which surface was shared ------------------------------------------------------------------------

/**
 * A window share's track id is `window:<HWND>:0` (measured: the number was the handle the shell's own
 * window list gave the shared window), so the window the user picked in the browser's dialog can be named
 * from the shell's window list. Null for a screen share or an id of another shape.
 */
export function windowHandleOf(deviceId: string | null | undefined): number | null {
  const m = /^window:(\d+):/.exec(deviceId || "");
  return m ? Number(m[1]) : null;
}

/** A window from the shell's list (`capture_windows`). */
export interface WindowInfo {
  hwnd: number;
  title: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The monitor holding a rectangle's centre, else null. */
export function monitorAt(monitors: MonitorInfo[], rect: { x: number; y: number; width: number; height: number }): MonitorInfo | null {
  const cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2;
  return monitors.find((m) => cx >= m.x && cx < m.x + m.width && cy >= m.y && cy < m.y + m.height) ?? null;
}

/**
 * The monitors a shared screen can be: those whose physical size is the track's. The browser's dialog
 * numbers the screens itself ("Screen 1", "Screen 3") and that numbering was seen to change between two
 * calls an hour apart, so the size - and, when two monitors share a size, the picture - is what tells
 * which monitor the user shared.
 */
export function monitorsOfSize(monitors: MonitorInfo[], width: number, height: number): MonitorInfo[] {
  return monitors.filter((m) => m.width === width && m.height === height);
}

/**
 * The shared surface's real size. A screen track's `getSettings()` is NOT it at first: sharing a smaller
 * monitor reported the largest screen's size until frames flowed (measured), so a real frame comes first,
 * then the track's capabilities (`width.max`, which named the true size), and the settings only as the
 * last resort.
 */
export function surfaceSize(
  frame: { width: number; height: number } | null,
  caps: { width?: { max?: number }; height?: { max?: number } } | null,
  settings: { width?: number; height?: number },
): { width: number; height: number } | null {
  if (frame && frame.width > 0 && frame.height > 0) return { width: frame.width, height: frame.height };
  const cw = caps?.width?.max, ch = caps?.height?.max;
  if (cw && ch) return { width: cw, height: ch };
  if (settings.width && settings.height) return { width: settings.width, height: settings.height };
  return null;
}

/** Mean absolute difference of two equal-length luma thumbnails (0-255 scale), or Infinity. */
export function thumbnailDistance(a: ArrayLike<number>, b: ArrayLike<number>): number {
  if (a.length === 0 || a.length !== b.length) return Infinity;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;
}

/** The candidate whose thumbnail is closest to the shared picture's, or null when none can be compared. */
export function closestMonitor(candidates: { monitor: MonitorInfo; distance: number }[]): MonitorInfo | null {
  let best: { monitor: MonitorInfo; distance: number } | null = null;
  for (const c of candidates) if (Number.isFinite(c.distance) && (!best || c.distance < best.distance)) best = c;
  return best ? best.monitor : null;
}

export const WRONG_SCREEN_MESSAGE =
  "The screen shared is not the one the region was drawn on, so the region cannot be cut from it. Start again and pick that screen in the share dialog.";

/** A still in the Captures list, as GET /api/captures lists it. */
export interface Capture {
  id: string;
  name: string;
  kind: CaptureKind;
  created_at: string;
  width: number;
  height: number;
  cursor: boolean;
  size_bytes: number;
  owner_name?: string | null;
}

export const capturesQueryKey = ["captures"] as const;

/** The still's URL, cache-busted by the capture's own timestamp. */
export function captureImageUrl(c: Pick<Capture, "id" | "created_at">): string {
  return `/api/captures/${c.id}/image?v=${encodeURIComponent(c.created_at)}`;
}

/** A recording still on disk, as GET /api/recordings lists it. */
export interface Unfinished {
  id: string;
  name: string;
  created_at: string;
  /** "recording" (its recorder is still making it), "finishing" (being saved), "stopped" (savable). */
  status: string;
  /** The chunks present. */
  chunks: number;
  /** How many run unbroken from the first: the part that can be saved. */
  contiguous: number;
  /** The highest chunk number plus one. */
  highest: number;
  bytes: number;
  /** About how many seconds the savable part holds (an upper bound: chunks times the timeslice). */
  seconds: number;
}

/**
 * The recordings the Projects page offers to save or discard: stopped ones only, and never the one this
 * page's own recorder is making (whatever the server says of it) - a live recording saved or discarded
 * from under the recorder would lose the rest of it. A live one elsewhere and one being saved are not
 * offered either; the save shows on its own card.
 */
export function offeredRecordings(list: Unfinished[], liveId: string | null): Unfinished[] {
  return list.filter((r) => r.status === "stopped" && r.id !== liveId);
}

/** What Save does for an unfinished recording: all of it, the part before a gap (after a confirm), or nothing. */
export type SaveAction =
  | { kind: "all"; chunks: number }
  | { kind: "before-gap"; chunks: number; missing: number; givenUp: number; confirm: string }
  | { kind: "none"; reason: string };

export function unfinishedSaveAction(r: Pick<Unfinished, "chunks" | "contiguous" | "highest" | "seconds">): SaveAction {
  if (r.contiguous <= 0) return { kind: "none", reason: "Its first piece never arrived, so nothing of it can be saved." };
  if (r.contiguous >= r.highest) return { kind: "all", chunks: r.contiguous };
  const missing = r.highest - r.chunks;
  const givenUp = r.chunks - r.contiguous;
  const pieces = (n: number) => `${n} piece${n === 1 ? "" : "s"}`;
  return {
    kind: "before-gap",
    chunks: r.contiguous,
    missing,
    givenUp,
    confirm: `Piece ${r.contiguous + 1} of this recording never reached the disk (${pieces(missing)} missing in all), so it can be saved only up to there: about ${formatElapsed(r.seconds * 1000)}. The ${pieces(givenUp)} recorded after the gap ${givenUp === 1 ? "is" : "are"} given up and deleted with the rest.`,
  };
}

/** A finished recording's job carries the project it made. */
export function projectOfResult(result: unknown): string | null {
  const pid = (result as { project_id?: unknown } | null | undefined)?.project_id;
  return typeof pid === "string" && pid ? pid : null;
}
