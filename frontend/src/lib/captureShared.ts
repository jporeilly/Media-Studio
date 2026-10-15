/**
 * What the recorder (`context/CaptureContext.tsx`) shares with the guided capture of a walkthrough's steps
 * (`components/walkthrough/useGuidedCapture.ts`): the recorder's context object and the hook that reads its state
 * where a provider may not be mounted, the overlay's region pick and the frozen pictures let go. Plain functions in
 * a file of their own, so the context module exports components only and Fast Refresh keeps working on it
 * (`react-refresh/only-export-components`); the rules themselves are `lib/capture.ts`.
 */
import { createContext, useContext } from "react";
import { api } from "../api/client";
import { type MonitorInfo, type RecordOptions, type RecorderState, type Region, type StillOptions, clipToMonitor, shellBridge } from "./capture";

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

/** The recorder's context: `CaptureProvider` fills it, `useCapture` reads it. */
export const CaptureContext = createContext<CaptureApi | null>(null);

/**
 * The recorder's state where a provider may not be mounted (the walkthrough builder's guided capture asks
 * whether a recording is in flight before it takes the bar and the keys): "idle" when there is no provider.
 */
export function useRecorderState(): RecorderState {
  return useContext(CaptureContext)?.state ?? "idle";
}

export type Bridge = NonNullable<ReturnType<typeof shellBridge>>;

/** Delete the frozen pictures of every monitor once they have served; nothing waits on it. */
export function forgetFrozen(): void {
  void api.delete("/api/capture/frozen").catch(() => {});
}

/**
 * Ask the overlay for a region: freeze, open, await the event. Null when the user pressed Escape or closed
 * the overlay. The shell builds the overlay's URL itself (on the backend's origin); the answer is cut to
 * the monitor it was drawn on. Shared by the recorder and the guided capture (W2), which chooses its region
 * once this way.
 */
export async function pickRegion(bridge: Bridge, monitors: MonitorInfo[]): Promise<Region | null> {
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
