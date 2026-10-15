/**
 * Guided capture (W2): the WIRING of the builder's Capture steps. The rules are `lib/guided.ts`; this hook talks
 * to the shell and the server:
 *
 * - **Start.** Refused while the recorder records (its bar and keys are in use) and outside the desktop app. For
 *   a region, the overlay is opened once (`pickRegion`, the recorder's own) and the region reused for every step.
 *   The bar's events are listened to FIRST (`capture:control`: capture / skip / back / done, and `hello`; the
 *   shell's `capture:step-key`), then the capture bar is opened in its second mode (`capture_bar_open` with
 *   `mode: "steps"`, excluded from capture, placed where the recorder bar goes) - a bar that cannot be opened
 *   gives the start up with "The capture bar could not be opened: …" - the global key taken (`capture_hotkeys_start`
 *   with `keys: "steps"`: Shift+F8, raised as `capture:step-key` with the pointer's position read in the shell AT
 *   the key press), the audit row `walkthrough.guided_capture` (start) written and the main window minimised out
 *   of the picture.
 * - **The bar's state.** Sent on every change (`capture:state`), and again when the bar asks: the bar page sends
 *   `hello` once its listener is up, because the hook's first send can beat the page's load (the first state was
 *   missed without it). A refusal the capture goes on under - the key taken by another program, a Windows that
 *   cannot hide the bar from the picture - travels in the payload as `message`, shown on the bar in red.
 * - **A capture.** The rectangle per kind (`captureTarget`), the still taken exactly as the Capture dialog takes
 *   one (`POST /api/captures`, named "<walkthrough> · step N"), attached to the step with the hotspot the pointer
 *   gives (`POST /api/walkthroughs/{wid}/steps/{sid}/picture` with `hotspot`: the rectangle, or null for a pointer
 *   outside the picture or a capture from the bar's button); the server's record is handed back to the builder as
 *   its saved baseline; the session moves to the next step and the bar follows.
 * - **Done.** The bar closed, the key given back, the window restored, the audit row (done) written with the
 *   counts, and the builder reloads. Leaving the page does the same without the row. Closing the window while
 *   the bar is up is allowed: nothing is in flight between key presses, and the shell closes the bar and gives
 *   the key back with the window (`capture::shutdown`).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../api/client";
import { type MonitorInfo, type Region, type WindowInfo, barPlacement, monitorFor, shellBridge } from "../../lib/capture";
import { type Bridge, forgetFrozen, pickRegion, useRecorderState } from "../../lib/captureShared";
import {
  type BarPayload, type GuidedOptions, type GuidedPhase, type GuidedSession, type Point, BAR_MODE, BAR_NOT_HIDDEN, BAR_NOT_OPENED, CONTROL_EVENT,
  KEYS_MODE, KEY_TAKEN, NEEDS_DESKTOP, NOTHING_TO_CAPTURE, RECORDING_IN_PROGRESS, STATE_EVENT, STEPS_BAR_HEIGHT, STEPS_BAR_WIDTH, STEP_KEY_EVENT,
  advance, atEnd, barPayload, captureName, captureTarget, currentId, guidedHotkey, hotspotOf, pointerOf, sessionOf, stepBack,
} from "../../lib/guided";
import { type Walkthrough, type WalkthroughDocument, stepIndex, stepsToCapture } from "../../lib/walkthrough";

export interface GuidedCaptureArgs {
  wid: string;
  /** The walkthrough as the builder holds it (saved before a start). */
  document: () => WalkthroughDocument | null;
  /** The record the server answered after a picture was attached: the builder's new saved baseline. */
  onAttached: (record: Walkthrough) => void;
  /** Done (or the page leaving) after a session: the builder reloads. */
  onFinished: (session: GuidedSession) => void;
}

export interface GuidedCapture {
  phase: GuidedPhase;
  session: GuidedSession | null;
  message: string | null;
  bar: BarPayload | null;
  start: (options: GuidedOptions) => Promise<boolean>;
  capture: (pointer: Point | null) => Promise<void>;
  skip: () => void;
  back: () => void;
  done: () => Promise<void>;
  dismiss: () => void;
}

/** The bar page asks for the current state with this action once its listener is up. */
export const HELLO_ACTION = "hello";

export function useGuidedCapture({ wid, document, onAttached, onFinished }: GuidedCaptureArgs): GuidedCapture {
  const recorder = useRecorderState();
  const [phase, setPhase] = useState<GuidedPhase>("idle");
  const [session, setSession] = useState<GuidedSession | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [bar, setBar] = useState<BarPayload | null>(null);
  const phaseRef = useRef<GuidedPhase>("idle");
  const sessionRef = useRef<GuidedSession | null>(null);
  const options = useRef<GuidedOptions | null>(null);
  const region = useRef<Region | null>(null);
  const monitors = useRef<MonitorInfo[]>([]);
  const unlisteners = useRef<(() => void)[]>([]);
  const barOpen = useRef(false);
  /** The refusal lines the session runs under (the key taken, the bar not hidden): in every payload to the bar. */
  const barMessage = useRef<string | null>(null);
  const minimised = useRef(false);
  const bridgeRef = useRef<Bridge | null>(null);
  const callbacks = useRef({ document, onAttached, onFinished });
  callbacks.current = { document, onAttached, onFinished };

  const setPhaseBoth = useCallback((p: GuidedPhase) => { phaseRef.current = p; setPhase(p); }, []);
  const setSessionBoth = useCallback((s: GuidedSession | null) => { sessionRef.current = s; setSession(s); }, []);

  /** The bar told where the session stands (and the builder's own copy of the same payload); nothing when idle. */
  const tellBar = useCallback((s: GuidedSession | null, p: GuidedPhase) => {
    const doc = callbacks.current.document();
    if (!s || !doc) { setBar(null); return; }
    const payload = barPayload(doc.name, doc, s, p, barMessage.current);
    setBar(payload);
    const bridge = bridgeRef.current;
    if (bridge && barOpen.current) void bridge.event!.emitTo!("capture-bar", STATE_EVENT, payload).catch(() => {});
  }, []);

  /** A refusal the capture goes on under: on the builder's line and, with the others, in the bar's payload. */
  const noteOnBar = useCallback((line: string) => {
    barMessage.current = barMessage.current && !barMessage.current.includes(line) ? `${barMessage.current} ${line}` : line;
    setMessage(line);
  }, []);

  /** The bar, the key and the window given back; the listeners dropped. */
  const release = useCallback(async () => {
    const bridge = bridgeRef.current;
    for (const off of unlisteners.current) off();
    unlisteners.current = [];
    barMessage.current = null;
    if (!bridge) return;
    if (barOpen.current) {
      barOpen.current = false;
      await bridge.core!.invoke!("capture_bar_close").catch(() => {});
    }
    await bridge.core!.invoke!("capture_hotkeys_stop").catch(() => {});
    if (minimised.current) {
      minimised.current = false;
      try {
        const w = bridge.window?.getCurrentWindow?.();
        await w?.unminimize();
        await w?.setFocus();
      } catch { /* the capability may refuse: the user restores the window by hand */ }
    }
  }, []);

  const capture = useCallback(async (pointer: Point | null) => {
    const s = sessionRef.current;
    const opts = options.current;
    const bridge = bridgeRef.current;
    const doc = callbacks.current.document();
    if (phaseRef.current !== "ready" || !s || !opts || !bridge || !doc) return;
    const sid = currentId(s);
    if (!sid) return;
    setPhaseBoth("capturing");
    tellBar(s, "capturing");
    try {
      const windows = opts.kind === "window" ? ((await bridge.core!.invoke!("capture_windows").catch(() => [])) as WindowInfo[]) : [];
      const target = captureTarget(opts.kind, { region: region.current, monitors: monitors.current, monitor: opts.monitor, windows, pointer });
      if ("error" in target) {
        setMessage(target.error);
        setPhaseBoth("ready");
        tellBar(s, "ready");
        return;
      }
      const number = stepIndex(doc, sid) + 1;
      const still = await api.post<{ id: string; x: number; y: number; width: number; height: number }>("/api/captures", {
        ...target.rect, cursor: opts.cursor, kind: opts.kind, name: captureName(doc.name, number),
      });
      const hotspot = hotspotOf(pointer, { x: still.x, y: still.y, width: still.width, height: still.height });
      const rec = await api.post<Walkthrough>(`/api/walkthroughs/${wid}/steps/${sid}/picture`, { capture_id: still.id, hotspot });
      callbacks.current.onAttached(rec);
      const next = advance(s, "captured");
      setSessionBoth(next);
      const p: GuidedPhase = atEnd(next) ? "finished" : "ready";
      setPhaseBoth(p);
      setMessage(null);
      tellBar(next, p);
    } catch (e) {
      setMessage(`The step could not be captured: ${errorMessage(e)}`);
      setPhaseBoth("ready");
      tellBar(s, "ready");
    }
  }, [wid, setPhaseBoth, setSessionBoth, tellBar]);

  const skip = useCallback(() => {
    const s = sessionRef.current;
    if (!s || (phaseRef.current !== "ready" && phaseRef.current !== "finished") || atEnd(s)) return;
    const next = advance(s, "skipped");
    setSessionBoth(next);
    const p: GuidedPhase = atEnd(next) ? "finished" : "ready";
    setPhaseBoth(p);
    tellBar(next, p);
  }, [setPhaseBoth, setSessionBoth, tellBar]);

  const back = useCallback(() => {
    const s = sessionRef.current;
    if (!s || (phaseRef.current !== "ready" && phaseRef.current !== "finished") || s.at === 0) return;
    const next = stepBack(s);
    setSessionBoth(next);
    setPhaseBoth("ready");
    tellBar(next, "ready");
  }, [setPhaseBoth, setSessionBoth, tellBar]);

  const done = useCallback(async () => {
    const s = sessionRef.current;
    if (phaseRef.current === "idle" || phaseRef.current === "capturing" || !s) return;
    setPhaseBoth("idle");
    await release();
    void api.post(`/api/walkthroughs/${wid}/guided-capture`, { event: "done", captured: s.captured, skipped: s.skipped }).catch(() => {});
    setSessionBoth(null);
    setBar(null);
    callbacks.current.onFinished(s);
  }, [wid, release, setPhaseBoth, setSessionBoth]);

  const start = useCallback(async (opts: GuidedOptions): Promise<boolean> => {
    const bridge = shellBridge();
    if (!bridge) { setMessage(NEEDS_DESKTOP); return false; }
    if (recorder !== "idle" && recorder !== "done" && recorder !== "failed") { setMessage(RECORDING_IN_PROGRESS); return false; }
    if (phaseRef.current !== "idle") return false;
    const doc = callbacks.current.document();
    if (!doc) return false;
    const ids = stepsToCapture(doc, opts.every);
    if (ids.length === 0) { setMessage(NOTHING_TO_CAPTURE); return false; }
    bridgeRef.current = bridge;
    options.current = opts;
    setMessage(null);
    barMessage.current = null;
    setPhaseBoth("starting");
    const giveUp = async (line: string) => {
      setMessage(line);
      await release();
      setSessionBoth(null);
      setPhaseBoth("idle");
      return false;
    };
    try {
      monitors.current = (await bridge.core!.invoke!("capture_monitors")) as MonitorInfo[];
      region.current = null;
      if (opts.kind === "region") {
        region.current = await pickRegion(bridge, monitors.current);
        forgetFrozen();
        if (!region.current) { setPhaseBoth("idle"); return false; }
      }
      const s = sessionOf(ids);
      setSessionBoth(s);
      // The listeners first: the bar's hello (and a key press) can arrive the moment the bar is up.
      unlisteners.current.push(await bridge.event!.listen!(CONTROL_EVENT, (e) => {
        const action = (e.payload as { action?: string } | null)?.action;
        if (action === "capture") void capture(null);
        else if (action === "skip") skip();
        else if (action === "back") back();
        else if (action === "done") void done();
        else if (action === HELLO_ACTION) tellBar(sessionRef.current, phaseRef.current);
      }));
      unlisteners.current.push(await bridge.event!.listen!(STEP_KEY_EVENT, (e) => { void capture(pointerOf(e.payload)); }));
      const monitor = monitorFor(monitors.current, opts.kind === "region" && region.current ? region.current.monitor : opts.kind === "screen" ? opts.monitor : null);
      if (monitor) {
        const bw = Math.round(STEPS_BAR_WIDTH * monitor.scale), bh = Math.round(STEPS_BAR_HEIGHT * monitor.scale);
        const at = barPlacement(monitor, opts.kind === "region" ? region.current : null, bw, bh);
        // Open before the invoke answers: a hello that beats the answer is still told the state; the close is idempotent.
        barOpen.current = true;
        let excluded: boolean;
        try {
          excluded = (await bridge.core!.invoke!("capture_bar_open", { x: at.x, y: at.y, width: bw, height: bh, mode: BAR_MODE })) as boolean;
        } catch (e) {
          return await giveUp(`${BAR_NOT_OPENED}: ${errorMessage(e)}`);
        }
        if (!excluded) noteOnBar(BAR_NOT_HIDDEN);
      }
      await bridge.core!.invoke!("capture_hotkeys_start", { keys: KEYS_MODE }).catch(() => { noteOnBar(KEY_TAKEN); });
      await api.post(`/api/walkthroughs/${wid}/guided-capture`, { event: "start", kind: opts.kind, cursor: opts.cursor });
      try {
        await bridge.window?.getCurrentWindow?.()?.minimize();
        minimised.current = true;
      } catch { /* refused: the window stays */ }
      setPhaseBoth("ready");
      tellBar(s, "ready");
      return true;
    } catch (e) {
      return await giveUp(`Could not start the capture: ${errorMessage(e)}`);
    }
  }, [wid, recorder, back, capture, done, release, skip, noteOnBar, setPhaseBoth, setSessionBoth, tellBar]);

  // The page's own Shift+F8 while it has the focus (the shell's key may be taken by another program): no pointer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!guidedHotkey(e) || phaseRef.current !== "ready") return;
      e.preventDefault();
      void capture(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [capture]);

  // The page leaving mid-session: the bar, the key and the window given back; no audit row (nothing finished).
  useEffect(() => () => {
    if (phaseRef.current !== "idle") void release();
  }, [release]);

  const dismiss = useCallback(() => setMessage(null), []);

  return { phase, session, message, bar, start, capture, skip, back, done, dismiss };
}
