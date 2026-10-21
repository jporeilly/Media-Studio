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
 *   its saved baseline; the session moves to the next step and the bar follows. A refusal of ONE capture - no
 *   window under the pointer, no region, a still that could not be taken - goes to the builder's line and to the
 *   bar as a transient `message`, cleared by the next payload (D1).
 * - **A click (W2b).** With "Capture on click" the shell's hook raises `capture:step-click` for every left or
 *   right press outside the app's own windows (`capture_hotkeys_start` with `clicks: true`); the hook calls the
 *   SAME `capture(pointer)` with the point - one path, one `hotspotOf`, one attach. For a Region a click outside
 *   the frame, for Full screen a click off the chosen monitor, is no step and the bar says so (`clickArea`,
 *   `clickRule`, `outsideLine`); a right click is a step like a left one. The shell tries the hook whether or not
 *   Shift+F8 went up and answers `{key, clicks}` (H1, read by `keysUpOf` for any shell); the bar and the builder
 *   then say which of the two is off (`keysLines`), and the "Click to capture" hint is shown only while the clicks
 *   are live.
 * - **Two in flight (F8).** A click (or a key press) while a still is being taken is NOT lost: the step is claimed
 *   at once (`claimStep`: the session moves on, the bar follows), its still is taken at once beside the first
 *   (`POST /api/captures` twice over; the grab is what must happen at the click, and the server grabs each into a
 *   file of its own), and the attaches land in click order through one promise chain; the bar reads "Capturing…"
 *   until the chain drains. A third while two are in flight is dropped with "Too fast: wait for the still."
 *   (`MAX_IN_FLIGHT`). A still that fails on its own puts the bar back on its step; one that fails while a later
 *   click's still is in flight leaves its step without its new picture and a STANDING line on the bar and the
 *   builder ("Step 6 has no picture: press Back to take it again.", or "Step 6 kept its old picture: …" for a step
 *   that had one, `missedLine`) until that step gets a picture or the session ends.
 * - **Done.** The bar closed, the key given back, the window restored, the audit row (done) written with the
 *   counts (the distinct steps pictured, the captures, how many by click, the skips), and the builder reloads.
 *   Done waits while a still is in flight. Leaving the page does the same without the row. Closing the window
 *   while the bar is up is allowed: the shell closes the bar, removes the click hook and gives the key back with
 *   the window (`capture::shutdown`); the pictures already attached are kept, and a still whose attach has not
 *   landed may not be attached - the page that would attach it goes with the window.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../api/client";
import { type MonitorInfo, type Region, type WindowInfo, barPlacement, monitorFor, shellBridge } from "../../lib/capture";
import { type Bridge, forgetFrozen, pickRegion, useRecorderState } from "../../lib/captureShared";
import {
  type BarPayload, type GuidedOptions, type GuidedPhase, type GuidedSession, type MissedStep, type Point, BAR_MODE, BAR_NOT_HIDDEN, BAR_NOT_OPENED,
  CONTROL_EVENT, KEYS_MODE, MAX_IN_FLIGHT, NEEDS_DESKTOP, NOTHING_TO_CAPTURE, RECORDING_IN_PROGRESS, STATE_EVENT,
  STEPS_BAR_HEIGHT, STEPS_BAR_WIDTH, STEP_CLICK_EVENT, STEP_KEY_EVENT, TOO_FAST,
  advance, atEnd, barMessage, barPayload, captureName, captureTarget, capturedSteps, claimStep, clickArea, clickOf, clickRule, frameFor, guidedHotkey,
  hotspotOf, keysLines, keysUpOf, missedLine, outsideLine, pointerOf, recordCapture, sessionOf, stepBack,
} from "../../lib/guided";
import type { Size } from "../../lib/annotate";
import { type Walkthrough, type WalkthroughDocument, hasPicture, stepIndex, stepsToCapture } from "../../lib/walkthrough";

export interface GuidedCaptureArgs {
  wid: string;
  /** The walkthrough as the builder holds it (saved before a start). */
  document: () => WalkthroughDocument | null;
  /** The record the server answered after a picture was attached: the builder's new saved baseline. */
  onAttached: (record: Walkthrough) => void;
  /** Done (or the page leaving) after a session: the builder reloads. */
  onFinished: (session: GuidedSession) => void;
}

/** Who asked for a capture: the key (or the bar's button, which carries no pointer) or the click hook. */
export type CaptureBy = "key" | "click";

export interface GuidedCapture {
  phase: GuidedPhase;
  session: GuidedSession | null;
  message: string | null;
  bar: BarPayload | null;
  start: (options: GuidedOptions) => Promise<boolean>;
  capture: (pointer: Point | null, by?: CaptureBy) => Promise<void>;
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
  /** The run's frame (Window kind, W2b): the widest width × the tallest height of its stills so far, kept from the start to Done. */
  const frame = useRef<Size | null>(null);
  /** How many stills are in flight (F8), and the chain their attaches land through, in click order. */
  const inFlight = useRef(0);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const monitors = useRef<MonitorInfo[]>([]);
  const unlisteners = useRef<(() => void)[]>([]);
  const barOpen = useRef(false);
  /** The refusal lines the session runs under (the key taken, the bar not hidden, the clicks off): in every payload to the bar. */
  const standing = useRef<string | null>(null);
  /**
   * The steps left without their new still by a still that failed beside a later one (G2): step id -> its number in
   * the walkthrough and whether it kept an old picture (H2). Their "Step N has no picture…" / "Step N kept its old
   * picture…" lines stand on the bar and the builder until the step gets a picture or the session ends.
   */
  const missed = useRef<Map<string, MissedStep>>(new Map());
  const missedText = useCallback(() => missedLine([...missed.current.values()]), []);
  /** The clicks are live: asked for AND the shell's hook up (H1). The payload's `clicks`, so the hint shows only then. */
  const clicksLive = useRef(false);
  const minimised = useRef(false);
  const bridgeRef = useRef<Bridge | null>(null);
  const callbacks = useRef({ document, onAttached, onFinished });
  callbacks.current = { document, onAttached, onFinished };

  const setPhaseBoth = useCallback((p: GuidedPhase) => { phaseRef.current = p; setPhase(p); }, []);
  const setSessionBoth = useCallback((s: GuidedSession | null) => { sessionRef.current = s; setSession(s); }, []);

  /**
   * The bar told where the session stands (and the builder's own copy of the same payload); nothing when idle. A
   * `transient` line - one capture's refusal, a click outside the frame - rides in THIS payload only: the next one
   * is sent without it, so the bar clears it (D1).
   */
  /** The red line: the standing lines (refusals, then the missed steps), then a transient one - the bar's and the builder's alike. */
  const redLine = useCallback((transient: string | null) => barMessage(barMessage(standing.current, missedText()), transient), [missedText]);

  const tellBar = useCallback((s: GuidedSession | null, p: GuidedPhase, transient: string | null = null) => {
    const doc = callbacks.current.document();
    if (!s || !doc) { setBar(null); return; }
    const payload = barPayload(doc.name, doc, s, p, redLine(transient), clicksLive.current);
    setBar(payload);
    const bridge = bridgeRef.current;
    if (bridge && barOpen.current) void bridge.event!.emitTo!("capture-bar", STATE_EVENT, payload).catch(() => {});
  }, [redLine]);

  /** A refusal the capture goes on under: with the others, on the builder's line and in the bar's payload. */
  const noteOnBar = useCallback((line: string) => {
    standing.current = standing.current && !standing.current.includes(line) ? `${standing.current} ${line}` : line;
    setMessage(standing.current);
  }, []);

  /** The bar, the key and the window given back; the listeners dropped. */
  const release = useCallback(async () => {
    const bridge = bridgeRef.current;
    for (const off of unlisteners.current) off();
    unlisteners.current = [];
    standing.current = null;
    missed.current.clear();
    clicksLive.current = false;
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

  /** One capture landed or failed: the chain drained to here; the bar back to Capture once nothing is in flight. */
  const settle = useCallback((transient: string | null) => {
    inFlight.current = Math.max(0, inFlight.current - 1);
    const s = sessionRef.current;
    if (inFlight.current > 0) { tellBar(s, "capturing", transient); return; }
    const p: GuidedPhase = s && atEnd(s) ? "finished" : "ready";
    setPhaseBoth(p);
    tellBar(s, p, transient);
  }, [setPhaseBoth, tellBar]);

  const capture = useCallback(async (pointer: Point | null, by: CaptureBy = "key") => {
    const opts = options.current;
    const bridge = bridgeRef.current;
    const doc = callbacks.current.document();
    const live = () => phaseRef.current === "ready" || phaseRef.current === "capturing";
    if (!live() || !sessionRef.current || !opts || !bridge || !doc) return;
    if (inFlight.current >= MAX_IN_FLIGHT) { tellBar(sessionRef.current, phaseRef.current, TOO_FAST); return; }
    // The target first - the window list is one quick command - so a refusal claims no step.
    const windows = opts.kind === "window" ? ((await bridge.core!.invoke!("capture_windows").catch(() => [])) as WindowInfo[]) : [];
    const s = sessionRef.current;
    if (!live() || !s) return;
    const target = captureTarget(opts.kind, { region: region.current, monitors: monitors.current, monitor: opts.monitor, windows, pointer, clicks: clicksLive.current });
    if ("error" in target) {
      // The refusal beside the standing lines, never in place of them (n-a): the builder's line is the bar's.
      setMessage(redLine(target.error));
      tellBar(s, phaseRef.current, target.error);
      return;
    }
    if (inFlight.current >= MAX_IN_FLIGHT) { tellBar(s, phaseRef.current, TOO_FAST); return; }
    // The step is claimed now and the bar moves on: the still is taken AT ONCE - a second grab may run beside the
    // first, the screen being what must be caught at the click - and the attaches land in click order (the chain).
    const claimed = claimStep(s);
    if (!claimed) return;
    const claimedAt = s.at;
    setSessionBoth(claimed.session);
    inFlight.current += 1;
    setPhaseBoth("capturing");
    tellBar(claimed.session, "capturing");
    const number = stepIndex(doc, claimed.id) + 1;
    // A step that has a picture already ("Every step") keeps it if this still fails (H2).
    const kept = number > 0 && hasPicture(doc.steps[number - 1]);
    const still = api.post<{ id: string; x: number; y: number; width: number; height: number }>("/api/captures", {
      ...target.rect, cursor: opts.cursor, kind: opts.kind, name: captureName(doc.name, number),
    });
    still.catch(() => { /* awaited in the chain, in its turn; this keeps a still that fails first from being "unhandled" */ });
    chain.current = chain.current.then(async () => {
      try {
        const taken = await still;
        const hotspot = hotspotOf(pointer, { x: taken.x, y: taken.y, width: taken.width, height: taken.height });
        // The run's frame (Window kind): max width × max height so far; a still smaller on a side is drawn on it by the server.
        const placed = frameFor(opts.kind, frame.current, { width: taken.width, height: taken.height });
        frame.current = placed.frame;
        const rec = await api.post<Walkthrough>(`/api/walkthroughs/${wid}/steps/${claimed.id}/picture`, {
          capture_id: taken.id, hotspot, ...(placed.send ? { frame: placed.send } : {}),
        });
        callbacks.current.onAttached(rec);
        if (sessionRef.current) setSessionBoth(recordCapture(sessionRef.current, claimed.id, by === "click"));
        // A step left without a picture earlier is no longer missed once it has one; the others' lines stand.
        missed.current.delete(claimed.id);
        setMessage(redLine(null));
        settle(null);
      } catch (e) {
        const line = `The step could not be captured: ${errorMessage(e)}`;
        // A lone failure - nothing claimed after it - puts the bar back on the step, as before; one with a later
        // claim in flight cannot (the later step is the one the user is on), so the step is left without its new
        // picture and a STANDING line names it on the bar and the builder until it gets one (G2; H2's wording when
        // it kept an old picture).
        const now = sessionRef.current;
        if (now && inFlight.current === 1 && now.at === claimedAt + 1) {
          setSessionBoth(stepBack(now));
        } else {
          missed.current.set(claimed.id, { number, kept });
        }
        setMessage(redLine(line));
        settle(line);
      }
    });
  }, [wid, setPhaseBoth, setSessionBoth, tellBar, settle, redLine]);

  /** The shell's click hook (W2b): the same path as the key, under the click rule. */
  const click = useCallback((payload: unknown) => {
    const c = clickOf(payload);
    const opts = options.current;
    if (!c || !opts || (phaseRef.current !== "ready" && phaseRef.current !== "capturing")) return;
    if (clickRule(opts.kind, clickArea(opts.kind, region.current, monitors.current, opts.monitor), c.point) === "outside") {
      tellBar(sessionRef.current, phaseRef.current, outsideLine(opts.kind));
      return;
    }
    void capture(c.point, "click");
  }, [capture, tellBar]);

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
    // The session ends and the builder's red line goes with it, whatever it said: a no-picture line (the builder's
    // list shows the step without one), or a key-taken / clicks-off line that speaks of a capture no longer running.
    await release();
    setMessage(null);
    void api.post(`/api/walkthroughs/${wid}/guided-capture`, {
      event: "done", captured: capturedSteps(s), skipped: s.skipped, captures: s.captured, by_click: s.byClick,
    }).catch(() => {});
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
    standing.current = null;
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
      frame.current = null;
      inFlight.current = 0;
      chain.current = Promise.resolve();
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
      if (opts.clicks) unlisteners.current.push(await bridge.event!.listen!(STEP_CLICK_EVENT, (e) => { click(e.payload); }));
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
      // The key and, when asked, the click hook - tried whether or not the key goes up (H1). The shell answers which
      // of the two is up (`keysUpOf` reads any shell's answer); the bar and the builder say which is off, and the
      // clicks are live only when asked and up.
      const answer = await bridge.core!.invoke!("capture_hotkeys_start", { keys: KEYS_MODE, clicks: opts.clicks }).then(
        (value) => ({ value, refused: false }),
        () => ({ value: undefined as unknown, refused: true }),
      );
      const keys = keysLines(keysUpOf(answer.value, answer.refused), opts.clicks);
      clicksLive.current = keys.clicks;
      for (const line of keys.lines) noteOnBar(line);
      // The audit row's ", on click" says what is live, not what was ticked: an older shell or a refused hook writes none.
      await api.post(`/api/walkthroughs/${wid}/guided-capture`, { event: "start", kind: opts.kind, cursor: opts.cursor, clicks: keys.clicks });
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
  }, [wid, recorder, back, capture, click, done, release, skip, noteOnBar, setPhaseBoth, setSessionBoth, tellBar]);

  // The page's own Shift+F8 while it has the focus (the shell's key may be taken by another program): no pointer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!guidedHotkey(e) || (phaseRef.current !== "ready" && phaseRef.current !== "capturing")) return;
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
