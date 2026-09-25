/**
 * `J` / `K` / `L` — a shuttle over the audition (E5c; spec §13.3, decision 4).
 *
 * The audition plays through Web Audio: the sentence buffers are scheduled on
 * the AudioContext's clock, the music beside them, and the muted `<video>` is
 * seeked along. That can only go FORWARD at 1×: faster with audio would pitch
 * the buffers (an AudioBufferSourceNode's `playbackRate` preserves nothing),
 * and reverse audio does not exist. So above 1×, and backwards at any rate,
 * the shuttle is picture and clock only — which is how a shuttle is used, to
 * find a frame rather than to listen (trap 40: no audio source is ever made
 * or scheduled there).
 *
 * Everything the component decides about the keys is here as a pure table,
 * tested in shuttle.test.ts: the transition each key makes from each state,
 * how far the playhead moves in a frame and which bound it hit, what the
 * transport's label says, and when a backwards frame seek is due. The
 * component owns the loop, the audio graph and the `<video>`; the live feel
 * is the owner's walk.
 */

import { EPSILON } from "./edit";

export type ShuttleKey = "J" | "K" | "L";
export type ShuttleRate = 1 | 2 | 4 | 8;

/**
 * The shuttle's state. `direction` 1 at `rate` 1 is a real play, with audio
 * (Space's own state); every other moving state is the silent shuttle.
 */
export interface Shuttle {
  direction: -1 | 0 | 1;
  rate: ShuttleRate;
}

/** Stopped; every stop, seek and halt at a bound returns here, which resets the rate. */
export const STOPPED: Shuttle = { direction: 0, rate: 1 };
/** Forward at 1× — the audition proper, with audio. */
export const FORWARD: Shuttle = { direction: 1, rate: 1 };
/** Where repeated presses in one direction stop climbing. */
export const MAX_RATE: ShuttleRate = 8;
/** About how many times a second the backwards shuttle seeks the `<video>`, so the element is not thrashed. */
export const SEEK_HZ = 15;

function faster(rate: ShuttleRate): ShuttleRate {
  return rate >= MAX_RATE ? MAX_RATE : ((rate * 2) as ShuttleRate);
}

/**
 * The whole transition table. `K` stops from anywhere. `L` pressed in the
 * direction already running doubles the rate up to 8× and stays there;
 * pressed against a backwards shuttle, or from a stop, it is forward at 1×
 * (a real play). `J` is the mirror: backwards at 1× from a stop or from any
 * forward state, and 2×, 4×, 8× on repeated presses.
 */
export function nextShuttle(state: Shuttle, key: ShuttleKey): Shuttle {
  if (key === "K") return STOPPED;
  const direction = key === "L" ? 1 : -1;
  return { direction, rate: state.direction === direction ? faster(state.rate) : 1 };
}

/**
 * The playhead after `dtSeconds` of real time at the state's direction and
 * rate: the bound AHEAD exactly when the move reaches it, with which one it
 * was, otherwise the moved position untouched. The bound BEHIND the move is
 * never a clamp: the component keeps only the bound ahead fresh (the
 * backwards floor while a backwards shuttle runs, the forward ceiling while
 * a play or a forward shuttle does), so a stale one on the far side of the
 * playhead must not pull it onto itself - the Reviewer's MAJOR 1, which had a
 * forward shuttle started below the last backwards shuttle's selection start
 * jump to that start on its first frame.
 *
 * The running position is NOT rounded to the stored precision: at 60 frames
 * a second a step of 16.7 ms rounds to 17 every time, and the shuttle would
 * drift two per cent ahead of the clock. Only the bound itself is exact, and
 * it is handed back as given (0, a selection's end, the output's end - all
 * stored values already). A negative or non-finite `dt` moves nothing.
 */
export function shuttleAdvance(
  position: number,
  state: Shuttle,
  dtSeconds: number,
  floor: number,
  ceiling: number,
): { position: number; hit: "floor" | "ceiling" | null } {
  const dt = Number.isFinite(dtSeconds) ? Math.max(0, dtSeconds) : 0;
  const moved = position + state.direction * state.rate * dt;
  if (state.direction < 0 && moved <= floor) return { position: floor, hit: "floor" };
  if (state.direction > 0 && moved >= ceiling) return { position: ceiling, hit: "ceiling" };
  return { position: moved, hit: null };
}

/**
 * Where a backwards shuttle stops: the selection's start when the playhead is
 * inside the selection - after its start, and at or before its end (so a
 * shuttle begun at the end a play halted on runs back to the start, and one
 * begun AT the start, with nothing inside left to run back to, runs to 0) -
 * else 0. The component computes it when a backwards shuttle begins and again
 * whenever the selection changes while one runs, as it keeps a play's bound,
 * so a cleared selection does not go on holding the floor.
 */
export function shuttleFloor(sel: { start: number; end: number } | null, at: number, epsilon = EPSILON): number {
  return sel && at > sel.start + epsilon && at <= sel.end + epsilon ? sel.start : 0;
}

/**
 * What the transport says beside the clock: nothing when stopped or playing
 * forward at 1× (the two states the Play button already shows), the direction
 * and the rate otherwise. Backwards at 1× is one arrow, since there is no
 * ordinary play to distinguish it from by silence alone.
 */
export function shuttleLabel(state: Shuttle): string {
  if (state.direction === 0) return "";
  if (state.direction > 0) return state.rate === 1 ? "" : `▶▶ ${state.rate}×`;
  return state.rate === 1 ? "◀ 1×" : `◀◀ ${state.rate}×`;
}

/** Whether a backwards frame seek is due: at most `hz` a second, measured on the caller's clock (ms). */
export function seekThrottle(lastSeekAt: number, now: number, hz = SEEK_HZ): boolean {
  return now - lastSeekAt >= 1000 / hz;
}
