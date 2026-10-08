/**
 * Screen capture (T3): the pure rules of lib/capture.ts - the recorder's state machine, the keys, the
 * chunk ledger, the names, the clock, the unload and close guards, the crop, the region's screen, the
 * constraints, the bar's place, the unfinished recordings the Projects page offers and what Save does with
 * one, and the hide rule.
 */
import { describe, expect, it, vi } from "vitest";
import {
  AUDIO_BITS_PER_SECOND,
  BAR_HEIGHT,
  BAR_MARGIN,
  BAR_WIDTH,
  CLOSE_BLOCKED_MESSAGE,
  HEARTBEAT_MS,
  MAX_RECORDING_MS,
  SAVE_CLOSE_BLOCKED_MESSAGE,
  TIMESLICE_MS,
  VIDEO_BITS_PER_SECOND,
  WRONG_SCREEN_MESSAGE,
  barControls,
  barPlacement,
  captureAvailable,
  chunkRefusalStops,
  clipToMonitor,
  closeBlockedMessage,
  closeGuardUp,
  closestMonitor,
  cropForRegion,
  displayConstraints,
  emptyLedger,
  expectedChunks,
  failureKeeps,
  formatElapsed,
  hasShellBridge,
  hotkeyAction,
  hotkeyEvent,
  isDesktopShell,
  knownCapture,
  probeCapture,
  ledgerAck,
  ledgerAdd,
  ledgerUnsent,
  microphoneConstraints,
  monitorAt,
  monitorFor,
  monitorsOfSize,
  offeredRecordings,
  pickerHint,
  projectOfResult,
  recorderNext,
  recordingName,
  regionShareProblem,
  shouldGuardUnload,
  stillName,
  surfaceSize,
  thumbnailDistance,
  unfinishedSaveAction,
  windowHandleOf,
  type MonitorInfo,
  type RecorderEvent,
  type RecorderState,
  type Region,
  type Unfinished,
} from "./capture";

const STATES: RecorderState[] = ["idle", "picking", "countdown", "recording", "paused", "saving", "done", "failed"];
const EVENTS: RecorderEvent[] = ["pick", "cancel", "armed", "start", "pause", "resume", "stop", "saved", "fail", "reset"];

describe("the recorder's state machine", () => {
  it("walks idle -> picking -> countdown -> recording -> paused -> recording -> saving -> done", () => {
    let s: RecorderState = "idle";
    for (const [ev, next] of [["pick", "picking"], ["armed", "countdown"], ["start", "recording"], ["pause", "paused"], ["resume", "recording"], ["stop", "saving"], ["saved", "done"], ["reset", "idle"]] as const) {
      const got = recorderNext(s, ev);
      expect(got, `${s} --${ev}-->`).toBe(next);
      s = got!;
    }
  });
  it("stops from paused too, cancels only before the recording runs, and fails from anywhere but done/failed", () => {
    expect(recorderNext("paused", "stop")).toBe("saving");
    expect(recorderNext("picking", "cancel")).toBe("idle");
    expect(recorderNext("countdown", "cancel")).toBe("idle");
    expect(recorderNext("recording", "cancel")).toBeNull();
    for (const s of STATES) {
      expect(recorderNext(s, "fail"), s).toBe(s === "done" || s === "failed" ? null : "failed");
    }
  });
  it("ignores every event that means nothing in a state", () => {
    const legal: Record<RecorderState, RecorderEvent[]> = {
      idle: ["pick", "fail"], picking: ["armed", "cancel", "fail"], countdown: ["start", "cancel", "fail"],
      recording: ["pause", "stop", "fail"], paused: ["resume", "stop", "fail"], saving: ["saved", "fail"],
      done: ["reset", "pick"], failed: ["reset", "pick"],
    };
    for (const s of STATES) for (const ev of EVENTS) {
      expect(recorderNext(s, ev) !== null, `${s} --${ev}-->`).toBe(legal[s].includes(ev));
    }
  });
  it("guards the page while a recording is in flight and not otherwise", () => {
    expect(STATES.filter(shouldGuardUnload)).toEqual(["countdown", "recording", "paused", "saving"]);
  });
  it("keeps the shell's close guard up while a recording is in flight AND while its save runs", () => {
    expect(STATES.filter((s) => closeGuardUp(s, false))).toEqual(["countdown", "recording", "paused", "saving"]);
    // The recorder is done (the job was submitted) but the save still runs: closing would stop it.
    expect(STATES.filter((s) => closeGuardUp(s, true))).toEqual(STATES);
    expect(closeBlockedMessage("recording")).toBe(CLOSE_BLOCKED_MESSAGE);
    expect(closeBlockedMessage("countdown")).toBe(CLOSE_BLOCKED_MESSAGE);
    expect(closeBlockedMessage("saving")).toBe(SAVE_CLOSE_BLOCKED_MESSAGE);
    expect(closeBlockedMessage("done")).toBe(SAVE_CLOSE_BLOCKED_MESSAGE);
  });
  it("offers Pause while recording, Resume while paused, Stop in both, Cancel during the countdown, nothing elsewhere", () => {
    expect(barControls("recording")).toEqual({ pause: "pause", stop: true, cancel: false });
    expect(barControls("paused")).toEqual({ pause: "resume", stop: true, cancel: false });
    expect(barControls("countdown")).toEqual({ pause: null, stop: false, cancel: true });
    for (const s of ["idle", "picking", "saving", "done", "failed"] as RecorderState[]) {
      expect(barControls(s)).toEqual({ pause: null, stop: false, cancel: false });
    }
  });
});

describe("the keys", () => {
  it("are Shift+F9 pause and Shift+F10 stop, with no other modifier", () => {
    expect(hotkeyAction({ key: "F9", shiftKey: true })).toBe("pause");
    expect(hotkeyAction({ key: "F10", shiftKey: true })).toBe("stop");
    expect(hotkeyAction({ key: "F9", shiftKey: false })).toBeNull();
    expect(hotkeyAction({ key: "F9", shiftKey: true, ctrlKey: true })).toBeNull();
    expect(hotkeyAction({ key: "F10", shiftKey: true, altKey: true })).toBeNull();
    expect(hotkeyAction({ key: "F8", shiftKey: true })).toBeNull();
  });
  it("pause toggles, stop stops - and cancels the countdown - and both do nothing outside a recording", () => {
    expect(hotkeyEvent("recording", "pause")).toBe("pause");
    expect(hotkeyEvent("paused", "pause")).toBe("resume");
    expect(hotkeyEvent("recording", "stop")).toBe("stop");
    expect(hotkeyEvent("paused", "stop")).toBe("stop");
    expect(hotkeyEvent("countdown", "stop")).toBe("cancel");
    expect(hotkeyEvent("countdown", "pause")).toBeNull();
    for (const s of ["idle", "picking", "saving", "done", "failed"] as RecorderState[]) {
      expect(hotkeyEvent(s, "pause")).toBeNull();
      expect(hotkeyEvent(s, "stop")).toBeNull();
    }
  });
});

describe("the chunk ledger", () => {
  const blob = (n: number) => new Blob([new Uint8Array(n)]);
  it("numbers chunks in order, keeps them until acknowledged, and names the unsent ones for a resend", () => {
    let l = emptyLedger();
    const a = ledgerAdd(l, blob(5)); l = a.ledger;
    const b = ledgerAdd(l, blob(7)); l = b.ledger;
    const c = ledgerAdd(l, blob(1)); l = c.ledger;
    expect([a.seq, b.seq, c.seq]).toEqual([0, 1, 2]);
    expect(expectedChunks(l)).toBe(3);
    expect(l.bytes).toBe(13);
    l = ledgerAck(l, 1);
    expect(ledgerUnsent(l)).toEqual([0, 2]);
    expect(l.pending.has(1)).toBe(false);
    l = ledgerAck(l, 0);
    l = ledgerAck(l, 2);
    expect(ledgerUnsent(l)).toEqual([]);
    expect(expectedChunks(l)).toBe(3);
  });
  it("is immutable: an add or an ack leaves the previous ledger as it was", () => {
    const l0 = emptyLedger();
    const { ledger: l1 } = ledgerAdd(l0, blob(2));
    expect(l0.next).toBe(0);
    expect(l1.next).toBe(1);
    const l2 = ledgerAck(l1, 0);
    expect(l1.pending.size).toBe(1);
    expect(l2.pending.size).toBe(0);
  });
  it("keeps a failed recording once the server holds a chunk, and throws away one it never got", () => {
    const { ledger: sent } = ledgerAdd(emptyLedger(), blob(3));
    expect(failureKeeps(sent)).toBe(false); // sent, never acknowledged
    expect(failureKeeps(ledgerAck(sent, 0))).toBe(true);
    expect(failureKeeps(emptyLedger())).toBe(false);
  });
  it("stops the recording on a chunk refused for the disk (507) or the cap (413), and only then", () => {
    expect(chunkRefusalStops(507)).toBe(true);
    expect(chunkRefusalStops(413)).toBe(true);
    for (const s of [400, 403, 404, 409, 500, 502, 0, null, undefined]) expect(chunkRefusalStops(s), String(s)).toBe(false);
  });
  it("records five-second chunks for two hours at most, at a rate whose two hours fit the server's cap", () => {
    expect(TIMESLICE_MS).toBe(5000);
    expect(MAX_RECORDING_MS).toBe(7_200_000);
    expect(HEARTBEAT_MS).toBeLessThan(20_000 / 2); // two beats inside the server's live window
    const twoHoursBytes = ((VIDEO_BITS_PER_SECOND + AUDIO_BITS_PER_SECOND) / 8) * (MAX_RECORDING_MS / 1000);
    expect(twoHoursBytes * 1.5).toBeLessThan(16 * 1024 ** 3);
  });
});

describe("names and the clock", () => {
  const at = new Date(2026, 9, 6, 11, 42, 7);
  it("names a window recording after the window and anything else after the moment", () => {
    expect(recordingName("window", "Budget 2026 - Excel", at)).toBe("Budget 2026 - Excel");
    expect(recordingName("window", "", at)).toBe("Screen recording 2026-10-06 11-42");
    expect(recordingName("region", "Notepad", at)).toBe("Screen recording 2026-10-06 11-42");
    expect(recordingName("screen", null, at)).toBe("Screen recording 2026-10-06 11-42");
    expect(stillName("window", "Notepad", at)).toBe("Notepad");
    expect(stillName("region", null, at)).toBe("Screen capture 2026-10-06 11-42-07");
  });
  it("formats elapsed time as m:ss and h:mm:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(7_400)).toBe("0:07");
    expect(formatElapsed(760_000)).toBe("12:40");
    expect(formatElapsed(3_723_000)).toBe("1:02:03");
    expect(formatElapsed(-5)).toBe("0:00");
  });
});

// A made-up layout: a 1600x900 monitor left of and below the top of a 2560x1440 primary.
const monitor: MonitorInfo = { index: 1, name: "\\\\.\\DISPLAY2", x: -1600, y: 120, width: 1600, height: 900, scale: 1, primary: false };
const primary: MonitorInfo = { index: 0, name: "\\\\.\\DISPLAY1", x: 0, y: 0, width: 2560, height: 1440, scale: 1, primary: true };
const region: Region = { x: -1500, y: 220, width: 401, height: 301, monitor: 1, window_title: null, hwnd: null };

describe("regions, monitors and the bar", () => {
  it("crops in the captured monitor's own pixels", () => {
    expect(cropForRegion(region, monitor)).toEqual({ x: 100, y: 100, width: 401, height: 301 });
    expect(cropForRegion(null, monitor)).toBeNull();
    expect(cropForRegion(region, null)).toBeNull();
  });
  it("cuts a region - a window snapped to across the monitor's edge - to the monitor it was picked on", () => {
    const across: Region = { x: -300, y: 400, width: 700, height: 300, monitor: 1, window_title: "Notes", hwnd: 7 };
    expect(clipToMonitor(across, [primary, monitor])).toEqual({ ...across, x: -300, width: 300 });
    const overTheTop: Region = { ...region, y: 20, height: 200 };
    expect(clipToMonitor(overTheTop, [primary, monitor])).toEqual({ ...overTheTop, y: 120, height: 100 });
    expect(clipToMonitor(region, [primary, monitor])).toEqual(region); // inside: untouched
    expect(clipToMonitor({ ...region, x: 10 }, [primary, monitor])).toBeNull(); // nothing of it on monitor 1
    expect(clipToMonitor(region, [primary])).toBeNull(); // its monitor is gone
  });
  it("finds a region's monitor, else the primary, else the first", () => {
    expect(monitorFor([primary, monitor], 1)).toBe(monitor);
    expect(monitorFor([monitor, primary], null)).toBe(primary);
    expect(monitorFor([monitor], 7)).toBe(monitor);
    expect(monitorFor([], 0)).toBeNull();
  });
  it("puts the bar under a region, above it when there is no room below, and bottom-right otherwise", () => {
    expect(barPlacement(monitor, region)).toEqual({ x: -1500, y: 220 + 301 + BAR_MARGIN });
    const low: Region = { ...region, y: 120 + 900 - 320, height: 300 };
    expect(barPlacement(monitor, low)).toEqual({ x: -1500, y: low.y - BAR_MARGIN - BAR_HEIGHT });
    const corner = barPlacement(primary, null);
    expect(corner).toEqual({ x: 2560 - BAR_WIDTH - BAR_MARGIN, y: 1440 - BAR_HEIGHT - BAR_MARGIN - 48 });
    // Never past the monitor's right edge.
    const farRight: Region = { ...region, x: -1600 + 1600 - 50 };
    expect(barPlacement(monitor, farRight).x).toBe(-1600 + 1600 - BAR_WIDTH - BAR_MARGIN);
  });
});

describe("which surface was shared", () => {
  it("reads the window's handle from a window share's id, and nothing from a screen's", () => {
    expect(windowHandleOf("window:4242:0")).toBe(4242);
    expect(windowHandleOf("screen:5:0")).toBeNull();
    expect(windowHandleOf("")).toBeNull();
    expect(windowHandleOf(undefined)).toBeNull();
    expect(windowHandleOf("window:abc:0")).toBeNull();
  });
  it("finds the monitor holding a rectangle's centre", () => {
    expect(monitorAt([primary, monitor], { x: -1400, y: 300, width: 800, height: 500 })).toBe(monitor);
    expect(monitorAt([primary, monitor], { x: 100, y: 100, width: 10, height: 10 })).toBe(primary);
    expect(monitorAt([primary, monitor], { x: -5000, y: 0, width: 10, height: 10 })).toBeNull();
  });
  it("narrows a screen share to the monitors of its size", () => {
    const twin: MonitorInfo = { ...primary, index: 2, x: 2560, primary: false };
    expect(monitorsOfSize([primary, monitor, twin], 1600, 900)).toEqual([monitor]);
    expect(monitorsOfSize([primary, monitor, twin], 2560, 1440)).toEqual([primary, twin]);
    expect(monitorsOfSize([primary, monitor], 800, 600)).toEqual([]);
  });
  it("takes the surface's size from a real frame, then the capabilities, then the settings", () => {
    // Measured: a smaller monitor's track first reported the largest screen's size in its settings.
    const lying = { width: 2560, height: 1440 };
    expect(surfaceSize({ width: 1600, height: 900 }, { width: { max: 1600 }, height: { max: 900 } }, lying)).toEqual({ width: 1600, height: 900 });
    expect(surfaceSize(null, { width: { max: 1600 }, height: { max: 900 } }, lying)).toEqual({ width: 1600, height: 900 });
    expect(surfaceSize(null, null, lying)).toEqual(lying);
    expect(surfaceSize({ width: 0, height: 0 }, {}, {})).toBeNull();
  });
  it("tells two same-sized monitors apart by the closest thumbnail", () => {
    expect(thumbnailDistance([0, 10, 20], [0, 10, 20])).toBe(0);
    expect(thumbnailDistance([0, 0], [10, 30])).toBe(20);
    expect(thumbnailDistance([0], [0, 0])).toBe(Infinity);
    expect(thumbnailDistance([], [])).toBe(Infinity);
    const twin: MonitorInfo = { ...primary, index: 2, x: 2560, primary: false };
    expect(closestMonitor([{ monitor: primary, distance: 40 }, { monitor: twin, distance: 3 }])).toBe(twin);
    expect(closestMonitor([{ monitor: primary, distance: Infinity }])).toBeNull();
    expect(closestMonitor([])).toBeNull();
  });
  it("records a region only from a share of the screen it was drawn on - never a window share", () => {
    expect(regionShareProblem("region", region, null, monitor)).toBeNull();
    // The review's probe: a window share whose window sits on the region's monitor.
    expect(regionShareProblem("region", region, 42, monitor)).toBe(WRONG_SCREEN_MESSAGE);
    expect(regionShareProblem("region", region, null, primary)).toBe(WRONG_SCREEN_MESSAGE);
    expect(regionShareProblem("region", region, null, null)).toBe(WRONG_SCREEN_MESSAGE);
    expect(regionShareProblem("region", null, null, monitor)).toBe(WRONG_SCREEN_MESSAGE);
    expect(regionShareProblem("window", null, 42, monitor)).toBeNull();
    expect(regionShareProblem("screen", null, null, primary)).toBeNull();
  });
});

describe("the streams' constraints and the picker's hint", () => {
  it("asks for the loopback without voice processing and in stereo, or no system sound at all", () => {
    const on = displayConstraints(true);
    expect(on.video).toEqual({ frameRate: 30 });
    expect(on.audio).toEqual({ echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2 });
    expect(displayConstraints(false).audio).toBe(false);
  });
  it("asks for the microphone with voice processing on, by device when one is chosen", () => {
    expect(microphoneConstraints(null)).toEqual({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    expect((microphoneConstraints("abc").audio as MediaTrackConstraints).deviceId).toEqual({ exact: "abc" });
  });
  it("tells the user which tab of the share dialog to use", () => {
    expect(pickerHint("window")).toMatch(/Window tab/);
    expect(pickerHint("region")).toMatch(/frozen screen/);
    expect(pickerHint("region")).toMatch(/Entire Screen/);
    expect(pickerHint("screen")).toMatch(/Entire Screen/);
    for (const k of ["window", "region", "screen"] as const) expect(pickerHint(k)).toMatch(/system audio/);
  });
});

describe("the unfinished recordings", () => {
  const rec = (over: Partial<Unfinished>): Unfinished => ({
    id: "a", name: "A", created_at: "2026-10-06T10:00:00Z", status: "stopped", chunks: 3, contiguous: 3, highest: 3, bytes: 9, seconds: 15, ...over,
  });
  it("offers the stopped ones, never the recorder's live one, one live elsewhere, or one being saved", () => {
    const list = [rec({ id: "live1" }), rec({ id: "old1" }), rec({ id: "busy", status: "finishing" }), rec({ id: "other", status: "recording" })];
    expect(offeredRecordings(list, "live1").map((r) => r.id)).toEqual(["old1"]);
    expect(offeredRecordings(list, null).map((r) => r.id)).toEqual(["live1", "old1"]);
  });
  it("saves all of an unbroken recording, the part before a gap after a confirm that says what is lost, or nothing", () => {
    expect(unfinishedSaveAction(rec({}))).toEqual({ kind: "all", chunks: 3 });
    const gap = unfinishedSaveAction(rec({ chunks: 3, contiguous: 2, highest: 4, seconds: 10 }));
    expect(gap.kind).toBe("before-gap");
    if (gap.kind !== "before-gap") return;
    expect([gap.chunks, gap.missing, gap.givenUp]).toEqual([2, 1, 1]);
    expect(gap.confirm).toBe("Piece 3 of this recording never reached the disk (1 piece missing in all), so it can be saved only up to there: about 0:10. The 1 piece recorded after the gap is given up and deleted with the rest.");
    const two = unfinishedSaveAction(rec({ chunks: 5, contiguous: 1, highest: 8, seconds: 5 }));
    expect(two.kind === "before-gap" && [two.missing, two.givenUp]).toEqual([3, 4]);
    expect(unfinishedSaveAction(rec({ chunks: 2, contiguous: 0, highest: 3 })).kind).toBe("none");
  });
});

describe("the hide rule", () => {
  it("is Tauri's own flag, and the bridge needs invoke and the events", () => {
    expect(isDesktopShell(undefined)).toBe(false);
    expect(isDesktopShell({})).toBe(false);
    expect(isDesktopShell({ isTauri: "yes" })).toBe(false);
    expect(isDesktopShell({ isTauri: true })).toBe(true);
    expect(hasShellBridge({ isTauri: true })).toBe(false);
    expect(hasShellBridge({ isTauri: true, __TAURI__: { core: { invoke: () => Promise.resolve() } } })).toBe(false);
    expect(hasShellBridge({ isTauri: true, __TAURI__: { core: { invoke: () => Promise.resolve() }, event: {} } })).toBe(true);
    expect(hasShellBridge({ __TAURI__: { core: { invoke: () => Promise.resolve() }, event: {} } })).toBe(false);
  });
  it("offers capture exactly where the page can talk to the shell", () => {
    const bridged = { isTauri: true, __TAURI__: { core: { invoke: () => Promise.resolve() }, event: {} } };
    expect(captureAvailable(bridged)).toBe(true);
    expect(captureAvailable({ isTauri: true })).toBe(false); // a shell whose capability refuses the page
    expect(captureAvailable({})).toBe(false);
  });
  it("asks the shell once whether it grants this page the capture commands, and keeps the answer", async () => {
    const granted = { isTauri: true, __TAURI__: { core: { invoke: vi.fn(async () => []) }, event: {} } };
    expect(knownCapture(granted)).toBeUndefined();
    expect(await probeCapture(granted)).toBe(true);
    expect(await probeCapture(granted)).toBe(true);
    expect(granted.__TAURI__.core.invoke).toHaveBeenCalledTimes(1);
    expect(granted.__TAURI__.core.invoke).toHaveBeenCalledWith("capture_monitors");
    expect(knownCapture(granted)).toBe(true);
    // A refused page: the bridge is there, the command is refused.
    const refused = { isTauri: true, __TAURI__: { core: { invoke: vi.fn(async () => { throw new Error("not allowed"); }) }, event: {} } };
    expect(captureAvailable(refused)).toBe(true);
    expect(await probeCapture(refused)).toBe(false);
    expect(knownCapture(refused)).toBe(false);
    // No bridge at all: no question asked.
    expect(await probeCapture({})).toBe(false);
  });
  it("reads the project a finished save made", () => {
    expect(projectOfResult({ project_id: "abc123abc123" })).toBe("abc123abc123");
    expect(projectOfResult({ cancelled: true })).toBeNull();
    expect(projectOfResult(null)).toBeNull();
  });
});
