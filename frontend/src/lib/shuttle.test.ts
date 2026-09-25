/**
 * The shuttle's tables (E5c, spec §13.3): every state × every key, the frame's
 * advance to the bound ahead, never clamped by the bound behind, the label and the seek throttle. What the
 * component does with them - the silent loop, the audio graph left alone
 * above 1× and backwards, the `<video>`'s rate - is pinned on its own source
 * in lib/music.test.ts; the live feel is the owner's.
 */
import { describe, expect, it } from "vitest";
import {
  FORWARD,
  MAX_RATE,
  SEEK_HZ,
  STOPPED,
  type Shuttle,
  type ShuttleKey,
  nextShuttle,
  seekThrottle,
  shuttleAdvance,
  shuttleFloor,
  shuttleLabel,
} from "./shuttle";

const S = (direction: -1 | 0 | 1, rate: 1 | 2 | 4 | 8): Shuttle => ({ direction, rate });
/** Every state the shuttle can be in: stopped, and each direction at each rate. */
const STATES: Shuttle[] = [
  STOPPED,
  S(1, 1), S(1, 2), S(1, 4), S(1, 8),
  S(-1, 1), S(-1, 2), S(-1, 4), S(-1, 8),
];

describe("nextShuttle - the whole transition table", () => {
  it.each<[string, Shuttle, ShuttleKey, Shuttle]>([
    // From a stop: L is a play, J a backwards shuttle at 1×, K stays stopped.
    ["stopped + L → forward 1×", STOPPED, "L", S(1, 1)],
    ["stopped + J → backward 1×", STOPPED, "J", S(-1, 1)],
    ["stopped + K → stopped", STOPPED, "K", STOPPED],
    // L in the forward direction doubles, to 8× and no further.
    ["forward 1× + L → forward 2×", S(1, 1), "L", S(1, 2)],
    ["forward 2× + L → forward 4×", S(1, 2), "L", S(1, 4)],
    ["forward 4× + L → forward 8×", S(1, 4), "L", S(1, 8)],
    ["forward 8× + L → forward 8× (stays)", S(1, 8), "L", S(1, 8)],
    // J against a forward state reverses at 1×, whatever the rate was.
    ["forward 1× + J → backward 1×", S(1, 1), "J", S(-1, 1)],
    ["forward 2× + J → backward 1×", S(1, 2), "J", S(-1, 1)],
    ["forward 4× + J → backward 1×", S(1, 4), "J", S(-1, 1)],
    ["forward 8× + J → backward 1×", S(1, 8), "J", S(-1, 1)],
    // J in the backward direction doubles, to 8× and no further.
    ["backward 1× + J → backward 2×", S(-1, 1), "J", S(-1, 2)],
    ["backward 2× + J → backward 4×", S(-1, 2), "J", S(-1, 4)],
    ["backward 4× + J → backward 8×", S(-1, 4), "J", S(-1, 8)],
    ["backward 8× + J → backward 8× (stays)", S(-1, 8), "J", S(-1, 8)],
    // L against a backward state is forward at 1× - a real play - never a faster forward.
    ["backward 1× + L → forward 1×", S(-1, 1), "L", S(1, 1)],
    ["backward 2× + L → forward 1×", S(-1, 2), "L", S(1, 1)],
    ["backward 4× + L → forward 1×", S(-1, 4), "L", S(1, 1)],
    ["backward 8× + L → forward 1×", S(-1, 8), "L", S(1, 1)],
    // K from anywhere: stopped, and the rate back to 1×.
    ["forward 1× + K → stopped", S(1, 1), "K", STOPPED],
    ["forward 2× + K → stopped", S(1, 2), "K", STOPPED],
    ["forward 4× + K → stopped", S(1, 4), "K", STOPPED],
    ["forward 8× + K → stopped", S(1, 8), "K", STOPPED],
    ["backward 1× + K → stopped", S(-1, 1), "K", STOPPED],
    ["backward 2× + K → stopped", S(-1, 2), "K", STOPPED],
    ["backward 4× + K → stopped", S(-1, 4), "K", STOPPED],
    ["backward 8× + K → stopped", S(-1, 8), "K", STOPPED],
  ])("%s", (_name, from, key, expected) => {
    expect(nextShuttle(from, key)).toEqual(expected);
  });

  it("covers every state with every key, and never leaves the eight rates or the three directions", () => {
    // The table above is the spec; this is the closure: 9 states × 3 keys,
    // every answer a legal state, and the constants what the table assumes.
    expect(STATES).toHaveLength(9);
    for (const from of STATES) {
      for (const key of ["J", "K", "L"] as const) {
        const next = nextShuttle(from, key);
        expect([1, 2, 4, 8]).toContain(next.rate);
        expect([-1, 0, 1]).toContain(next.direction);
        if (next.direction === 0) expect(next.rate).toBe(1);
        expect(next.rate).toBeLessThanOrEqual(MAX_RATE);
      }
    }
    expect(FORWARD).toEqual(S(1, 1));
    expect(STOPPED).toEqual(S(0, 1));
    expect(MAX_RATE).toBe(8);
  });

  it("never mutates the state it is given", () => {
    const from = S(1, 2);
    nextShuttle(from, "L");
    nextShuttle(from, "J");
    nextShuttle(from, "K");
    expect(from).toEqual(S(1, 2));
  });
});

describe("shuttleAdvance - a frame's move, halted on the bound ahead, never clamped by the bound behind", () => {
  it.each<[string, number, Shuttle, number, number, number, number, "floor" | "ceiling" | null]>([
    // name, position, state, dt, floor, ceiling, expected position, hit
    ["forward 1× moves dt", 10, S(1, 1), 0.5, 0, 60, 10.5, null],
    ["forward 2× moves 2·dt", 10, S(1, 2), 0.5, 0, 60, 11, null],
    ["forward 4× moves 4·dt", 10, S(1, 4), 0.5, 0, 60, 12, null],
    ["forward 8× moves 8·dt", 10, S(1, 8), 0.5, 0, 60, 14, null],
    ["backward 1× moves −dt", 10, S(-1, 1), 0.5, 0, 60, 9.5, null],
    ["backward 2× moves −2·dt", 10, S(-1, 2), 0.5, 0, 60, 9, null],
    ["backward 4× moves −4·dt", 10, S(-1, 4), 0.5, 0, 60, 8, null],
    ["backward 8× moves −8·dt", 10, S(-1, 8), 0.5, 0, 60, 6, null],
    // The bounds: landing on one is a hit, overshooting one is the bound exactly.
    ["forward lands on the ceiling", 59.5, S(1, 1), 0.5, 0, 60, 60, "ceiling"],
    ["forward overshoots the ceiling", 59.9, S(1, 8), 0.5, 0, 60, 60, "ceiling"],
    ["forward at the ceiling already", 60, S(1, 2), 0.016, 0, 60, 60, "ceiling"],
    ["forward stops at a selection's end, not the output's", 5.9, S(1, 2), 0.1, 0, 6, 6, "ceiling"],
    ["backward lands on the floor", 0.5, S(-1, 1), 0.5, 0, 60, 0, "floor"],
    ["backward overshoots the floor", 0.1, S(-1, 8), 0.5, 0, 60, 0, "floor"],
    ["backward at the floor already", 0, S(-1, 1), 0.016, 0, 60, 0, "floor"],
    ["backward stops at a selection's start, not 0", 4.05, S(-1, 4), 0.1, 4, 60, 4, "floor"],
    // dt = 0 (the first frame, whose clock has only just been read) moves nothing and hits nothing...
    ["dt = 0 forward", 10, S(1, 8), 0, 0, 60, 10, null],
    ["dt = 0 backward", 10, S(-1, 8), 0, 0, 60, 10, null],
    // ... unless the playhead is already ON the bound it is heading into.
    ["dt = 0 at the ceiling, heading in", 60, S(1, 1), 0, 0, 60, 60, "ceiling"],
    ["dt = 0 at the floor, heading in", 0, S(-1, 1), 0, 0, 60, 0, "floor"],
    // A bad clock is a frame of nothing, never a jump.
    ["a negative dt moves nothing", 10, S(1, 4), -0.5, 0, 60, 10, null],
    ["a NaN dt moves nothing", 10, S(1, 4), Number.NaN, 0, 60, 10, null],
    // Stopped moves nothing, whatever the dt.
    ["stopped moves nothing", 10, STOPPED, 1, 0, 60, 10, null],
    // An unknown end (the loop passes Infinity while `total` is momentarily absent) is no bound.
    ["no ceiling while the end is unknown", 10, S(1, 8), 100, 0, Number.POSITIVE_INFINITY, 810, null],
    // The bound BEHIND the move is never a clamp (the Reviewer's MAJOR 1): the
    // component keeps only the bound ahead fresh, so a stale floor or ceiling
    // on the far side of the playhead must not pull it onto them - a forward
    // shuttle started below the last backwards shuttle's selection start, a
    // backwards shuttle started past the last play's selection end.
    ["forward from below a stale floor moves freely", 0.5, S(1, 2), 0.016, 30, 60, 0.532, null],
    ["backward from above a stale ceiling moves freely", 60, S(-1, 1), 0.016, 0, 40, 59.984, null],
  ])("%s", (_name, position, state, dt, floor, ceiling, expected, hit) => {
    const out = shuttleAdvance(position, state, dt, floor, ceiling);
    expect(out.position).toBeCloseTo(expected, 9);
    expect(out.hit).toBe(hit);
  });

  it("hands the bound back exactly as given, and never rounds the running position", () => {
    // The bound is a stored value already (a selection's end at 3 dp); it
    // comes back byte-for-byte, not through a second rounding.
    expect(shuttleAdvance(6.1, S(1, 1), 1, 0, 6.123).position).toBe(6.123);
    expect(shuttleAdvance(4.1, S(-1, 1), 1, 4.001, 60).position).toBe(4.001);
    // Sixty frames of 16.7 ms at 1× is 1.002 s, not the 1.02 s that rounding
    // each step to a millisecond would make it (17 ms × 60).
    let at = 0;
    for (let i = 0; i < 60; i += 1) at = shuttleAdvance(at, S(1, 1), 0.0167, 0, 60).position;
    expect(at).toBeCloseTo(1.002, 9);
    expect(at).not.toBeCloseTo(1.02, 3);
  });
});

describe("shuttleFloor - where a backwards shuttle stops", () => {
  // "J stops at the selection's start when the playhead is inside one, else
  // at 0" (spec §13.3). Inside is after the start and at or before the end:
  // at the end a play halted on, J runs back to the start; AT the start,
  // with nothing inside left to run back to, J runs to 0. Half a millisecond
  // either side is the same instant (EPSILON), as everywhere on the strip.
  const SEL = { start: 30, end: 40 };
  it.each<[string, { start: number; end: number } | null, number, number]>([
    ["inside the selection: its start", SEL, 35, 30],
    ["at the selection's end, where a play halted: its start", SEL, 40, 30],
    ["within EPSILON past the end: still its start", SEL, 40.0004, 30],
    ["past the end: 0", SEL, 40.001, 0],
    ["AT the selection's start, nothing inside to run back to: 0", SEL, 30, 0],
    ["within EPSILON after the start: still 0", SEL, 30.0004, 0],
    ["a millisecond after the start: its start", SEL, 30.001, 30],
    ["before the selection: 0", SEL, 10, 0],
    ["no selection: 0", null, 35, 0],
    ["no selection, at 0: 0", null, 0, 0],
  ])("%s", (_name, sel, at, floor) => {
    expect(shuttleFloor(sel, at)).toBe(floor);
  });
});

describe("shuttleLabel - what the transport says beside the clock", () => {
  it.each<[Shuttle, string]>([
    [STOPPED, ""],
    [S(1, 1), ""],
    [S(1, 2), "▶▶ 2×"],
    [S(1, 4), "▶▶ 4×"],
    [S(1, 8), "▶▶ 8×"],
    [S(-1, 1), "◀ 1×"],
    [S(-1, 2), "◀◀ 2×"],
    [S(-1, 4), "◀◀ 4×"],
    [S(-1, 8), "◀◀ 8×"],
  ])("%j reads %j", (state, label) => {
    expect(shuttleLabel(state)).toBe(label);
  });

  it("says nothing for exactly the two states the Play button already shows", () => {
    const silent = STATES.filter((state) => shuttleLabel(state) === "");
    expect(silent).toEqual([STOPPED, S(1, 1)]);
  });
});

describe("seekThrottle - at most about fifteen seeks a second", () => {
  it.each<[string, number, number, number | undefined, boolean]>([
    ["the first seek is due at once", Number.NEGATIVE_INFINITY, 0, undefined, true],
    ["the same instant is not", 1000, 1000, undefined, false],
    ["one frame later (16.7 ms) is not", 1000, 1016.7, undefined, false],
    ["four frames later (66.7 ms) is", 1000, 1066.7, undefined, true],
    ["exactly the period is", 1000, 1000 + 1000 / 15, undefined, true],
    ["just short of the period is not", 1000, 1000 + 1000 / 15 - 0.01, undefined, false],
    ["at 30 Hz, 40 ms is", 1000, 1040, 30, true],
    ["at 30 Hz, 30 ms is not", 1000, 1030, 30, false],
    ["at 1 Hz, 999 ms is not", 1000, 1999, 1, false],
    ["at 1 Hz, a second is", 1000, 2000, 1, true],
  ])("%s", (_name, last, now, hz, due) => {
    expect(hz === undefined ? seekThrottle(last, now) : seekThrottle(last, now, hz)).toBe(due);
  });

  it("defaults to SEEK_HZ, which is 15", () => {
    expect(SEEK_HZ).toBe(15);
    expect(seekThrottle(0, 1000 / 15)).toBe(true);
    expect(seekThrottle(0, 1000 / 16)).toBe(false);
  });
});
