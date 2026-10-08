/**
 * Screen capture (T3): the recorder, mounted once inside the shell so a recording survives navigating
 * within the app. The rules are in `lib/capture.ts`; this is the wiring:
 *
 * - **Pick.** A region is drawn on the shell's overlay first: the screen is frozen
 *   (POST /api/capture/freeze), the overlay opens on every monitor, and the choice comes back as the
 *   `capture:region` event (null = Escape, or the overlay closed with Alt+F4), cut to the monitor it was
 *   drawn on. A window and a whole screen are picked in the share dialog itself - it already is a window
 *   and screen picker with thumbnails - so there is no overlay for them.
 * - **Streams.** `getDisplayMedia` (the share dialog: the user picks the screen or the window there, and
 *   turns system audio on in it) plus `getUserMedia` for the microphone. The audio tracks are mixed AT
 *   UNITY by Web Audio into one track beside the picture; a silent constant source keeps that track alive
 *   when neither is on (a destination with nothing connected yields a dead track and a 0-byte recording -
 *   measured).
 * - **Which surface.** A window share names its window by HWND (`window:<hwnd>:0`), looked up in the
 *   shell's window list for the recording's name. A screen share is matched to a monitor by size and,
 *   when two monitors share a size, by comparing a thumbnail of the shared picture with the frozen frames.
 *   A region needs a share of the screen it was drawn on: a window share, or another screen, is refused
 *   (the crop would be wrong).
 * - **Chunks.** MediaRecorder hands over a WebM chunk every TIMESLICE_MS; each goes straight to
 *   PUT /api/recordings/{id}/chunks/{seq} and is dropped from memory once acknowledged; at Stop the ones
 *   never acknowledged are sent again, then Finish starts the job that makes the project and the page goes
 *   to Projects, which follows the job and opens the project when it is made. A chunk refused for the disk
 *   (507) or the recording's cap (413) stops the recording with the server's line, keeping what arrived.
 * - **Live.** While it records (paused too) the recorder sends a heartbeat every HEARTBEAT_MS, so the
 *   server holds the recording LIVE and refuses the Projects page's Save and Discard; the page itself
 *   leaves out `liveRecordingId`. A failure after the first chunk keeps the recording and lets it go
 *   (`release`), so the Projects page offers it at once.
 * - **The bar.** A small always-on-top window the shell excludes from capture (`capture-bar.html`), told
 *   the state every second (`capture:state`) and answering with `capture:control`; the countdown shows
 *   there, with Cancel. The main window minimises when the recording starts and comes back at Stop.
 * - **Keys.** Shift+F9 pause/resume, Shift+F10 stop (cancel during the countdown): system-wide through the
 *   shell's hotkeys (`capture:hotkey`) while recording, and in the page when it has the focus.
 * - **Limits.** Two hours, then Stop with a message. Leaving the page while a recording is in flight asks
 *   first (beforeunload); closing the window is refused by the shell while a recording is in flight AND
 *   while its save runs (`closeGuardUp`). The guard never outlives what it protects: it comes down when the
 *   save job ends, when the job cannot be asked about MAX_POLL_FAILURES times running (the server is gone:
 *   SAVE_UNREACHED_MESSAGE), and when the provider unmounts (sign-out, a reload); the shell also ignores it
 *   once the backend has exited. Only the caller's OWN running save is adopted on mount.
 * - **Leftovers.** On mount the bar, the keys and any overlay a previous page left behind (a reload or a
 *   crashed renderer mid-recording) are released.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, errorMessage } from "../api/client";
import {
  AUDIO_BITS_PER_SECOND,
  BAR_HEIGHT,
  BAR_WIDTH,
  COUNTDOWN_SECONDS,
  HEARTBEAT_MS,
  KEPT_LINE,
  SAVE_UNREACHED_MESSAGE,
  LIMIT_MESSAGE,
  MAX_RECORDING_MS,
  TIMESLICE_MS,
  VIDEO_BITS_PER_SECOND,
  barPlacement,
  chunkRefusalStops,
  clipToMonitor,
  closeBlockedMessage,
  closeGuardUp,
  closestMonitor,
  displayConstraints,
  emptyLedger,
  expectedChunks,
  failureKeeps,
  hotkeyAction,
  hotkeyEvent,
  ledgerAck,
  ledgerAdd,
  ledgerUnsent,
  microphoneConstraints,
  monitorAt,
  monitorFor,
  monitorsOfSize,
  recorderNext,
  recordingName,
  regionShareProblem,
  shellBridge,
  shouldGuardUnload,
  stillName,
  surfaceSize,
  thumbnailDistance,
  windowHandleOf,
  type ChunkLedger,
  type MonitorInfo,
  type RecordOptions,
  type RecorderEvent,
  type RecorderState,
  type Region,
  type StillOptions,
  type WindowInfo,
} from "../lib/capture";
import { JOB_POLL_MS, MAX_POLL_FAILURES, isActiveStatus } from "../lib/jobs";
import { useAuth } from "./AuthContext";

export interface CaptureApi {
  state: RecorderState;
  /** Milliseconds recorded so far (paused time excluded). */
  elapsedMs: number;
  /** The message of the last failure, or a notice (the two-hour stop). */
  message: string | null;
  /** The finish job's id once Stop has been pressed, until the Projects page takes it over. */
  savingJobId: string | null;
  /** The Projects page has taken the finish job over (it follows it from here). */
  clearSavingJob: () => void;
  /** The recording this recorder is making (from its start until it is saved, let go or thrown away). */
  liveRecordingId: string | null;
  /** Whether this recorder's save job is still running (the shell's close guard stays up for it). */
  saveRunning: boolean;
  /** Whether the recording keys are live system-wide (false when another program holds them). */
  hotkeysLive: boolean;
  startRecording: (options: RecordOptions) => Promise<void>;
  takeStill: (options: StillOptions) => Promise<{ id: string } | null>;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  /** Called off during the countdown: nothing was recorded, nothing is kept. */
  cancelCountdown: () => void;
  dismiss: () => void;
  /** When the last still was taken, for the Captures card to refresh on. */
  lastStillAt: number;
}

const CaptureContext = createContext<CaptureApi | null>(null);

export function useCapture(): CaptureApi {
  const ctx = useContext(CaptureContext);
  if (!ctx) throw new Error("useCapture outside CaptureProvider");
  return ctx;
}

type Bridge = NonNullable<ReturnType<typeof shellBridge>>;

/** Delete the frozen pictures of every monitor once they have served; nothing waits on it. */
function forgetFrozen(): void {
  void api.delete("/api/capture/frozen").catch(() => {});
}

/**
 * Ask the overlay for a region: freeze, open, await the event. Null when the user pressed Escape or closed
 * the overlay. The shell builds the overlay's URL itself (on the backend's origin); the answer is cut to
 * the monitor it was drawn on.
 */
async function pickRegion(bridge: Bridge, monitors: MonitorInfo[]): Promise<Region | null> {
  await api.post("/api/capture/freeze", { monitors: monitors.map((m) => ({ index: m.index, x: m.x, y: m.y, width: m.width, height: m.height })) });
  const picked = await new Promise<Region | null>((resolve, reject) => {
    let unlisten: (() => void) | null = null;
    bridge.event!.listen!("capture:region", (e) => {
      unlisten?.();
      resolve((e.payload as Region | null) ?? null);
    })
      .then((off) => {
        unlisten = off;
        return bridge.core!.invoke!("capture_overlay_open");
      })
      .catch((err) => {
        unlisten?.();
        reject(err);
      });
  });
  return picked ? clipToMonitor(picked, monitors) : null;
}

const THUMB_WIDTH = 48;

/** A small luma thumbnail of a drawable, for telling monitors apart. Null when the DOM cannot draw. */
function lumaThumbnail(source: CanvasImageSource, width: number, height: number): Uint8ClampedArray | null {
  try {
    const h = Math.max(1, Math.round((THUMB_WIDTH * height) / width));
    const c = document.createElement("canvas");
    c.width = THUMB_WIDTH;
    c.height = h;
    const g = c.getContext("2d");
    if (!g) return null;
    g.drawImage(source, 0, 0, THUMB_WIDTH, h);
    const rgba = g.getImageData(0, 0, THUMB_WIDTH, h).data;
    const out = new Uint8ClampedArray(THUMB_WIDTH * h);
    for (let i = 0; i < out.length; i++) out[i] = (rgba[i * 4] * 299 + rgba[i * 4 + 1] * 587 + rgba[i * 4 + 2] * 114) / 1000;
    return out;
  } catch {
    return null;
  }
}

/** One real frame of the track, or null. ImageCapture's grabFrame is Chromium's (it is not in TypeScript's
 *  DOM library), so it is typed here. */
async function grabFrame(track: MediaStreamTrack): Promise<ImageBitmap | null> {
  const IC = (globalThis as unknown as { ImageCapture?: new (t: MediaStreamTrack) => { grabFrame: () => Promise<ImageBitmap> } }).ImageCapture;
  if (!IC) return null;
  try {
    return await new IC(track).grabFrame();
  } catch {
    return null;
  }
}

/** A frozen monitor's thumbnail (the freeze taken for the overlay). */
function frozenThumbnail(m: MonitorInfo): Promise<Uint8ClampedArray | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(lumaThumbnail(img, m.width, m.height));
    img.onerror = () => resolve(null);
    img.src = `/api/capture/frozen/${m.index}?t=${Date.now()}`;
  });
}

/**
 * What was shared: its real size (`surfaceSize`: a frame first - the settings report the largest screen's
 * size until frames flow), and for a screen, which monitor - by size, and by picture when two monitors
 * share a size and a freeze is there to compare with.
 */
async function sharedSurface(track: MediaStreamTrack, monitors: MonitorInfo[], frozen: boolean): Promise<{ size: { width: number; height: number } | null; monitor: MonitorInfo | null }> {
  const frame = await grabFrame(track);
  try {
    const caps = (track.getCapabilities?.() ?? null) as { width?: { max?: number }; height?: { max?: number } } | null;
    const size = surfaceSize(frame, caps, track.getSettings());
    if (!size) return { size: null, monitor: null };
    const bySize = monitorsOfSize(monitors, size.width, size.height);
    if (bySize.length <= 1 || !frozen || !frame) return { size, monitor: bySize[0] ?? null };
    const shot = lumaThumbnail(frame, frame.width, frame.height);
    if (!shot) return { size, monitor: null };
    const scored = await Promise.all(bySize.map(async (m) => ({ monitor: m, distance: thumbnailDistance(shot, (await frozenThumbnail(m)) ?? []) })));
    return { size, monitor: closestMonitor(scored) };
  } finally {
    frame?.close();
  }
}

export function CaptureProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const [state, setState] = useState<RecorderState>("idle");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [savingJobId, setSavingJobId] = useState<string | null>(null);
  const [liveRecordingId, setLiveRecordingId] = useState<string | null>(null);
  const [saveJob, setSaveJob] = useState<string | null>(null);
  const [hotkeysLive, setHotkeysLive] = useState(false);
  const [lastStillAt, setLastStillAt] = useState(0);

  const stateRef = useRef<RecorderState>("idle");
  const recorder = useRef<MediaRecorder | null>(null);
  const streams = useRef<MediaStream[]>([]);
  const audioCtx = useRef<AudioContext | null>(null);
  const ledger = useRef<ChunkLedger>(emptyLedger());
  const uploads = useRef<Promise<void>[]>([]);
  const recordingId = useRef<string | null>(null);
  const startedAt = useRef<number>(0);
  const pausedAt = useRef<number | null>(null);
  const pausedTotal = useRef<number>(0);
  const stopRequested = useRef(false);
  const unlisteners = useRef<(() => void)[]>([]);
  const barOpen = useRef(false);
  const minimised = useRef(false);

  const transition = useCallback((event: RecorderEvent): boolean => {
    const next = recorderNext(stateRef.current, event);
    if (!next) return false;
    stateRef.current = next;
    setState(next);
    return true;
  }, []);

  const elapsedNow = useCallback(() => {
    if (!startedAt.current) return 0;
    const pausedSoFar = pausedTotal.current + (pausedAt.current ? Date.now() - pausedAt.current : 0);
    return Math.max(0, Date.now() - startedAt.current - pausedSoFar);
  }, []);

  const bridge = shellBridge();

  const tellBar = useCallback((extra: Record<string, unknown> = {}) => {
    if (!bridge || !barOpen.current) return;
    void bridge.event!.emitTo!("capture-bar", "capture:state", { state: stateRef.current, elapsedMs: elapsedNow(), ...extra }).catch(() => {});
  }, [bridge, elapsedNow]);

  const releaseStreams = useCallback(() => {
    for (const s of streams.current) s.getTracks().forEach((t) => t.stop());
    streams.current = [];
    void audioCtx.current?.close().catch(() => {});
    audioCtx.current = null;
  }, []);

  const closeBar = useCallback(async () => {
    if (!bridge) return;
    if (barOpen.current) {
      barOpen.current = false;
      await bridge.core!.invoke!("capture_bar_close").catch(() => {});
    }
    await bridge.core!.invoke!("capture_hotkeys_stop").catch(() => {});
    setHotkeysLive(false);
    for (const off of unlisteners.current) off();
    unlisteners.current = [];
  }, [bridge]);

  const restoreWindow = useCallback(async () => {
    if (!bridge || !minimised.current) return;
    minimised.current = false;
    try {
      const w = bridge.window?.getCurrentWindow?.();
      await w?.unminimize();
      await w?.setFocus();
    } catch {
      /* the capability may refuse: the user restores the window by hand */
    }
  }, [bridge]);

  /** The recorder is done with its recording: no longer this page's live one. */
  const letGoOfId = useCallback((): string | null => {
    const rid = recordingId.current;
    recordingId.current = null;
    setLiveRecordingId(null);
    return rid;
  }, []);

  /** Give up before anything was recorded: back to idle with a line, nothing left behind. */
  const abandon = useCallback((text: string) => {
    releaseStreams();
    void closeBar();
    void restoreWindow();
    transition("cancel");
    setMessage(text);
  }, [closeBar, releaseStreams, restoreWindow, transition]);

  /**
   * The recording cannot go on (the recorder failed, the disk is nearly full, the cap): stop it, and once
   * the uploads in flight have settled keep what reached the server - let go of it, so the Projects page
   * offers it at once - or, when nothing did, throw the empty recording away.
   */
  const fail = useCallback((text: string) => {
    if (!transition("fail")) return;
    setMessage(text);
    stopRequested.current = true;
    const rec = recorder.current;
    if (rec && rec.state !== "inactive") {
      try { rec.stop(); } catch { /* already stopped */ }
    }
    recorder.current = null;
    releaseStreams();
    void closeBar();
    void restoreWindow();
    const rid = letGoOfId();
    if (!rid) return;
    void (async () => {
      await Promise.allSettled(uploads.current);
      if (failureKeeps(ledger.current)) {
        await api.post(`/api/recordings/${rid}/release`).catch(() => {});
        setMessage(text.includes("kept") ? text : `${text} ${KEPT_LINE}`);
      } else {
        await api.delete(`/api/recordings/${rid}?from_recorder=true`).catch(() => {});
      }
    })();
  }, [closeBar, letGoOfId, releaseStreams, restoreWindow, transition]);

  /** Finish: send the chunks never acknowledged again, then ask for the job and go to Projects. */
  const finish = useCallback(async () => {
    const rid = recordingId.current;
    if (!rid) return;
    try {
      await Promise.allSettled(uploads.current);
      for (const seq of ledgerUnsent(ledger.current)) {
        const blob = ledger.current.pending.get(seq);
        if (!blob) continue;
        await api.putRaw(`/api/recordings/${rid}/chunks/${seq}`, blob, "video/webm");
        ledger.current = ledgerAck(ledger.current, seq);
      }
      const r = await api.post<{ job_id: string }>(`/api/recordings/${rid}/finish`, {
        duration_ms: Math.round(elapsedNow()),
        chunks: expectedChunks(ledger.current) || null,
        from_recorder: true,
      });
      setSaveJob(r.job_id);
      setSavingJobId(r.job_id);
      letGoOfId();
      transition("saved");
      navigate("/projects");
    } catch (e) {
      setMessage(`The recording could not be saved: ${errorMessage(e)}. Its chunks are kept; the Projects page offers it again.`);
      transition("fail");
      letGoOfId();
      void api.post(`/api/recordings/${rid}/release`).catch(() => {});
    } finally {
      releaseStreams();
      await closeBar();
      await restoreWindow();
    }
  }, [closeBar, elapsedNow, letGoOfId, navigate, releaseStreams, restoreWindow, transition]);

  const stop = useCallback(() => {
    if (!transition("stop")) return;
    stopRequested.current = true;
    tellBar();
    const rec = recorder.current;
    recorder.current = null;
    if (rec && rec.state !== "inactive") {
      rec.onstop = () => void finish();
      rec.stop();
    } else {
      void finish();
    }
  }, [finish, tellBar, transition]);

  /** Called off during the countdown (the bar's Cancel, Shift+F10, the page's Cancel): nothing was recorded. */
  const cancelCountdown = useCallback(() => {
    if (stateRef.current !== "countdown" || !transition("cancel")) return;
    stopRequested.current = true;
    releaseStreams();
    void closeBar();
    void restoreWindow();
    const rid = letGoOfId();
    if (rid) void api.delete(`/api/recordings/${rid}?from_recorder=true`).catch(() => {});
  }, [closeBar, letGoOfId, releaseStreams, restoreWindow, transition]);

  const pause = useCallback(() => {
    if (!transition("pause")) return;
    pausedAt.current = Date.now();
    try { recorder.current?.pause(); } catch { /* already inactive */ }
    tellBar();
  }, [tellBar, transition]);

  const resume = useCallback(() => {
    if (!transition("resume")) return;
    if (pausedAt.current) pausedTotal.current += Date.now() - pausedAt.current;
    pausedAt.current = null;
    try { recorder.current?.resume(); } catch { /* already inactive */ }
    tellBar();
  }, [tellBar, transition]);

  const onHotkey = useCallback((action: "pause" | "stop") => {
    const ev = hotkeyEvent(stateRef.current, action);
    if (ev === "pause") pause();
    else if (ev === "resume") resume();
    else if (ev === "stop") stop();
    else if (ev === "cancel") cancelCountdown();
  }, [cancelCountdown, pause, resume, stop]);

  // Leftovers of a page that went away mid-recording (a reload, a crashed renderer): the bar stays open
  // and always on top, its buttons talking to nobody, and the keys stay taken. Released here, once.
  useEffect(() => {
    if (!bridge) return;
    void bridge.core!.invoke!("capture_bar_close").catch(() => {});
    void bridge.core!.invoke!("capture_hotkeys_stop").catch(() => {});
    void bridge.core!.invoke!("capture_overlay_close").catch(() => {});
    // Once per mount; the bridge is the page's for its life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A save of the CALLER's still running (the page was reloaded during it): the close guard follows it too.
  // Only their own: an administrator's scope route answers everyone's save, and another user's job is not
  // this window's to hold open.
  useEffect(() => {
    if (!bridge || !userId) return;
    let live = true;
    void api.get<{ active_job: { id: string; status: string; user_id?: string | null } | null }>("/api/recordings/job")
      .then((r) => {
        const job = r?.active_job;
        if (live && job && job.user_id === userId && isActiveStatus(job.status)) setSaveJob(job.id);
      })
      .catch(() => {});
    return () => { live = false; };
    // Once per signed-in user; the bridge is the page's for its life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  // Keys in the page itself, while it has the focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = hotkeyAction(e);
      if (!action) return;
      if (stateRef.current !== "recording" && stateRef.current !== "paused" && stateRef.current !== "countdown") return;
      e.preventDefault();
      onHotkey(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onHotkey]);

  // The save job, followed until it ends: the shell's close guard stays up while it runs (a closed window
  // stops the backend, and the save with it).
  useEffect(() => {
    if (!saveJob) return;
    let gone = false;
    let failures = 0;
    const poll = async () => {
      try {
        const job = await api.get<{ status: string } | null>(`/api/jobs/${saveJob}`);
        failures = 0;
        if (!gone && !isActiveStatus(job?.status)) setSaveJob(null);
      } catch (e) {
        if (gone) return;
        // 404: the server no longer holds it (a restart); 403: not ours to read. Either way it is over here.
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) { setSaveJob(null); return; }
        // Anything else - no answer at all - is tried again, MAX_POLL_FAILURES times running; then the server
        // is taken to be gone, and the guard comes down with the reason (a window that can never be closed
        // protects nothing once the backend has stopped).
        failures += 1;
        if (failures >= MAX_POLL_FAILURES) {
          setSaveJob(null);
          setMessage(SAVE_UNREACHED_MESSAGE);
        }
      }
    };
    const id = window.setInterval(() => void poll(), JOB_POLL_MS);
    return () => { gone = true; window.clearInterval(id); };
  }, [saveJob]);

  // Closing the WINDOW is the shell's to refuse (the page's beforeunload is never asked when the host
  // window closes): the guard is up while a recording is in flight and while its save runs, and a refused
  // close is explained here.
  const saveRunning = saveJob !== null;
  const guardUp = closeGuardUp(state, saveRunning);
  useEffect(() => {
    if (!bridge) return;
    void bridge.core!.invoke!("capture_guard", { active: guardUp }).catch(() => {});
  }, [bridge, guardUp]);
  // The provider going away (sign-out, a reload) takes the recorder and the save follower with it: the
  // guard it raised comes down with it, or nothing would ever lower it.
  useEffect(() => {
    if (!bridge) return;
    return () => { void bridge.core!.invoke!("capture_guard", { active: false }).catch(() => {}); };
  }, [bridge]);
  useEffect(() => {
    if (!bridge) return;
    let off: (() => void) | null = null;
    let gone = false;
    void bridge.event!.listen!("capture:close-blocked", () => setMessage(closeBlockedMessage(stateRef.current)))
      .then((unlisten) => { if (gone) unlisten(); else off = unlisten; })
      .catch(() => {});
    return () => { gone = true; off?.(); };
  }, [bridge]);

  // Leaving while a recording is in flight asks first.
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (!shouldGuardUnload(stateRef.current)) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);

  // The clock, the bar's second hand and the two-hour stop.
  useEffect(() => {
    if (state !== "recording" && state !== "paused" && state !== "countdown") return;
    const id = window.setInterval(() => {
      const ms = elapsedNow();
      setElapsedMs(ms);
      tellBar();
      if (ms >= MAX_RECORDING_MS && stateRef.current !== "saving") {
        setMessage(LIMIT_MESSAGE);
        stop();
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [elapsedNow, state, stop, tellBar]);

  // The heartbeat: the recording stays LIVE on the server while this recorder makes it, paused or not.
  useEffect(() => {
    if (!liveRecordingId || (state !== "recording" && state !== "paused" && state !== "countdown")) return;
    const id = window.setInterval(() => {
      void api.post(`/api/recordings/${liveRecordingId}/heartbeat`).catch(() => {});
    }, HEARTBEAT_MS);
    return () => window.clearInterval(id);
  }, [liveRecordingId, state]);

  const startRecording = useCallback(async (options: RecordOptions) => {
    if (!bridge) { setMessage("Screen capture needs the desktop app."); return; }
    if (!transition("pick")) return;
    setMessage(null);
    setSavingJobId(null);
    stopRequested.current = false;
    ledger.current = emptyLedger();
    uploads.current = [];
    pausedTotal.current = 0;
    pausedAt.current = null;
    startedAt.current = 0;
    setElapsedMs(0);
    try {
      const monitors = (await bridge.core!.invoke!("capture_monitors")) as MonitorInfo[];
      let region: Region | null = null;
      if (options.kind === "region") {
        region = await pickRegion(bridge, monitors);
        // The frozen frames are kept only to match the shared screen below.
        if (!region) { forgetFrozen(); transition("cancel"); return; }
      }

      // The share dialog: screen or window, and its "Share with system audio" switch.
      let display: MediaStream;
      try {
        display = await navigator.mediaDevices.getDisplayMedia(displayConstraints(options.systemSound));
      } catch (e) {
        const err = e as DOMException;
        if (region) forgetFrozen();
        abandon(err?.name === "NotAllowedError"
          ? "Nothing was shared: the share dialog was cancelled or refused."
          : `The screen could not be captured: ${errorMessage(e)}`);
        return;
      }
      streams.current.push(display);
      const videoTrack = display.getVideoTracks()[0];
      if (!videoTrack) throw new Error("The shared source has no picture.");
      videoTrack.addEventListener("ended", () => { if (stateRef.current === "recording" || stateRef.current === "paused") stop(); });
      const settings = videoTrack.getSettings();

      // Which surface was shared: the window by its handle, the screen by its size and picture.
      let monitor: MonitorInfo | null = null;
      let windowTitle: string | null = null;
      const hwnd = windowHandleOf(settings.deviceId);
      const shared = await sharedSurface(videoTrack, monitors, options.kind === "region");
      if (region) forgetFrozen();
      if (hwnd !== null) {
        const windows = (await bridge.core!.invoke!("capture_windows").catch(() => [])) as WindowInfo[];
        const w = windows.find((x) => x.hwnd === hwnd);
        windowTitle = w?.title ?? null;
        monitor = w ? monitorAt(monitors, w) : null;
      } else {
        monitor = shared.monitor;
      }
      const problem = regionShareProblem(options.kind, region, hwnd, monitor);
      if (problem) {
        abandon(problem);
        return;
      }
      monitor = monitor ?? monitorFor(monitors, null);

      let mic: MediaStream | null = null;
      if (options.microphone) {
        try {
          mic = await navigator.mediaDevices.getUserMedia(microphoneConstraints(options.microphoneId));
          streams.current.push(mic);
        } catch (e) {
          abandon((e as DOMException)?.name === "NotAllowedError"
            ? "The microphone was refused. Allow it when asked, or record without it."
            : `The microphone could not be opened: ${errorMessage(e)}`);
          return;
        }
      }

      // One audio track from every source, at unity.
      const ctx = new AudioContext({ sampleRate: 48000 });
      audioCtx.current = ctx;
      await ctx.resume().catch(() => {});
      const dest = ctx.createMediaStreamDestination();
      for (const s of [display, mic]) {
        if (s && s.getAudioTracks().length) ctx.createMediaStreamSource(new MediaStream(s.getAudioTracks())).connect(dest);
      }
      const keepAlive = ctx.createConstantSource();
      keepAlive.offset.value = 0;
      keepAlive.connect(dest);
      keepAlive.start();
      const mixed = new MediaStream([videoTrack, ...dest.stream.getAudioTracks()]);
      const mime = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m)) ?? "video/webm";

      const started = await api.post<{ id: string; name: string }>("/api/recordings", {
        name: recordingName(hwnd !== null ? "window" : options.kind, windowTitle),
        mime,
        frame_rate: settings.frameRate ?? 30,
        width: shared.size?.width ?? 0,
        height: shared.size?.height ?? 0,
        timeslice_ms: TIMESLICE_MS,
        source: {
          kind: options.kind,
          region: options.kind === "region" && region ? { x: region.x, y: region.y, width: region.width, height: region.height } : null,
          monitor: options.kind === "region" && monitor ? { x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height } : null,
          window_title: windowTitle,
        },
      });
      recordingId.current = started.id;
      setLiveRecordingId(started.id);

      // The bar, the keys, and the window out of the way.
      if (monitor) {
        const bw = Math.round(BAR_WIDTH * monitor.scale), bh = Math.round(BAR_HEIGHT * monitor.scale);
        const at = barPlacement(monitor, options.kind === "region" ? region : null, bw, bh);
        const excluded = (await bridge.core!.invoke!("capture_bar_open", { x: at.x, y: at.y, width: bw, height: bh }).catch(() => false)) as boolean;
        barOpen.current = true;
        if (!excluded) setMessage("This Windows cannot hide the recorder bar from the picture; keep it off the recorded area.");
      }
      unlisteners.current.push(await bridge.event!.listen!("capture:control", (e) => {
        const action = (e.payload as { action?: string } | null)?.action;
        if (action === "cancel" || (action === "stop" && stateRef.current === "countdown")) cancelCountdown();
        else if (action === "pause" || action === "resume") onHotkey("pause");
        else if (action === "stop") stop();
      }));
      unlisteners.current.push(await bridge.event!.listen!("capture:hotkey", (e) => {
        const action = e.payload as string;
        if (action === "pause" || action === "stop") onHotkey(action);
      }));
      const keys = await bridge.core!.invoke!("capture_hotkeys_start").then(() => true, (err: unknown) => { setMessage(String(err)); return false; });
      setHotkeysLive(keys);

      if (!transition("armed")) return;
      try {
        await bridge.window?.getCurrentWindow?.()?.minimize();
        minimised.current = true;
      } catch { /* refused: the window stays */ }
      for (let n = COUNTDOWN_SECONDS; n > 0; n--) {
        tellBar({ countdown: n });
        await new Promise((r) => setTimeout(r, 1000));
        if (stopRequested.current || stateRef.current !== "countdown") return;
      }

      const rec = new MediaRecorder(mixed, { mimeType: mime, videoBitsPerSecond: VIDEO_BITS_PER_SECOND, audioBitsPerSecond: AUDIO_BITS_PER_SECOND });
      recorder.current = rec;
      const rid = started.id;
      rec.ondataavailable = (ev) => {
        if (!ev.data || ev.data.size === 0) return;
        const added = ledgerAdd(ledger.current, ev.data);
        ledger.current = added.ledger;
        const p = api.putRaw(`/api/recordings/${rid}/chunks/${added.seq}`, ev.data, "video/webm")
          .then(() => { ledger.current = ledgerAck(ledger.current, added.seq); })
          .catch((err: unknown) => {
            // The disk or the cap: the recording cannot go on - stop with the server's own line.
            if (err instanceof ApiError && chunkRefusalStops(err.status)) fail(errorMessage(err));
            /* anything else: kept in the ledger; sent again at Finish */
          });
        uploads.current.push(p);
      };
      rec.onerror = () => fail("The recorder failed; the recording was stopped.");
      rec.start(TIMESLICE_MS);
      startedAt.current = Date.now();
      transition("start");
      tellBar();
    } catch (e) {
      fail(`Could not start the recording: ${errorMessage(e)}`);
    }
  }, [abandon, bridge, cancelCountdown, fail, onHotkey, stop, tellBar, transition]);

  const takeStill = useCallback(async (options: StillOptions): Promise<{ id: string } | null> => {
    if (!bridge) { setMessage("Screen capture needs the desktop app."); return null; }
    setMessage(null);
    try {
      const monitors = (await bridge.core!.invoke!("capture_monitors")) as MonitorInfo[];
      let rect: { x: number; y: number; width: number; height: number } | null = null;
      let title: string | null = null;
      if (options.kind === "screen") {
        const m = monitorFor(monitors, options.monitor);
        if (!m) throw new Error("No monitor was found.");
        rect = { x: m.x, y: m.y, width: m.width, height: m.height };
      } else {
        const region = await pickRegion(bridge, monitors);
        forgetFrozen();
        if (!region) return null;
        rect = { x: region.x, y: region.y, width: region.width, height: region.height };
        title = region.window_title;
      }
      if (options.delaySeconds > 0) await new Promise((r) => setTimeout(r, options.delaySeconds * 1000));
      const rec = await api.post<{ id: string }>("/api/captures", {
        ...rect, cursor: options.cursor, kind: options.kind, name: stillName(options.kind, title),
      });
      setLastStillAt(Date.now());
      return rec;
    } catch (e) {
      setMessage(`The still could not be taken: ${errorMessage(e)}`);
      return null;
    }
  }, [bridge]);

  const dismiss = useCallback(() => {
    setMessage(null);
    if (stateRef.current === "done" || stateRef.current === "failed") transition("reset");
  }, [transition]);

  const clearSavingJob = useCallback(() => setSavingJobId(null), []);

  const value = useMemo<CaptureApi>(() => ({
    state, elapsedMs, message, savingJobId, clearSavingJob, liveRecordingId, saveRunning, hotkeysLive,
    startRecording, takeStill, pause, resume, stop, cancelCountdown, dismiss, lastStillAt,
  }), [state, elapsedMs, message, savingJobId, clearSavingJob, liveRecordingId, saveRunning, hotkeysLive,
    startRecording, takeStill, pause, resume, stop, cancelCountdown, dismiss, lastStillAt]);

  return <CaptureContext.Provider value={value}>{children}</CaptureContext.Provider>;
}
