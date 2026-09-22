/**
 * The Music lane's pure helpers (E4b, spec §12.5): the ripple that a cut
 * applies to the clips, the move and the trim, the clip a Library "Add at
 * playhead" makes, the hit test and its 8 px edges, where a dragged clip
 * snaps, the waveform slice, the audition's placement and fade envelope, the
 * body a commit sends, what a refusal says, which lock combinations a cut can
 * do anything with, and the Render line's music clauses. They live in
 * lib/edit.ts beside the rest of the edit's arithmetic; they are tested here
 * so the E3 file stays the one the shared projection fixture drives.
 *
 * `cutMusic` is checked against a BRUTE-FORCE reference that works a
 * different way round - it samples each clip's span, keeps the moments the
 * cut leaves and groups the survivors into runs - so a case analysis and a
 * simulation have to agree, and then against the model's own rules over a few
 * thousand random lists.
 */
import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
// The components' own source, as text, for the handful of decisions no pure
// helper can hold - the audition's ramps, the commit lock over a drag's
// release, the two clicks that differ on purpose, the ceiling a drag passes
// to `moveClip`. Vite's `?raw` rather than node:fs, which would need
// @types/node. What a RENDER can prove instead is proved by one:
// components/project/NarrationTimeline.render.test.tsx.
import timelineSource from "../components/project/NarrationTimeline.tsx?raw";
import librarySource from "../components/project/MusicLibrary.tsx?raw";
import { libraryChangeKeys, musicLibraryKey, narrationPlanKey } from "./timeline";
import {
  CLIP_EDGE_PX,
  DEFAULT_FADE_IN,
  DEFAULT_FADE_OUT,
  DEFAULT_MUSIC_GAIN,
  EPSILON,
  LANES,
  MAX_CLIPS,
  MIN_CLIP_SECONDS,
  canCut,
  clipAt,
  clipEnd,
  clipGain,
  clipLength,
  clipPeaks,
  clipPlayback,
  clipSnapTargets,
  clipsAfterDelete,
  cutMusic,
  editBody,
  editRefusal,
  musicAfterCut,
  fadeLevel,
  fadePoints,
  fitFades,
  laneLocks,
  mintClipId,
  moveClip,
  musicBody,
  newMusicClip,
  renderSummary,
  round3,
  sameMusic,
  snapClip,
  trimClip,
  unmovedRelease,
  type Keep,
  type MusicClip,
} from "./edit";

const FILE_SECONDS = 10;

function clip(over: Partial<MusicClip> = {}): MusicClip {
  return {
    id: "m1", file: "bed.mp3", at: 5, in: 0, out: 4,
    gain: 0.15, fade_in: 1, fade_out: 2, ...over,
  };
}

/** A repeatable pseudo-random source, so a failure is the same failure twice. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Ids a test can predict, so a split's second half is identifiable. */
function counterMint(): (taken: Set<string>) => string {
  let n = 0;
  return () => `new${(n += 1)}`;
}

describe("the clip's own arithmetic", () => {
  it("is as long as the slice of the file it plays, and ends there on the output", () => {
    expect(clipLength(clip({ in: 1.5, out: 4.25 }))).toBe(2.75);
    expect(clipEnd(clip({ at: 5, in: 1.5, out: 4.25 }))).toBe(7.75);
    expect(clipEnd(clip({ at: 0.1, in: 0, out: 0.2 }))).toBe(0.3);
  });

  it("keeps the gain a linear factor in [0, 1], and falls back to the studio default", () => {
    expect(clipGain(0.15)).toBe(0.15);
    expect(clipGain(-2)).toBe(0);
    expect(clipGain(4)).toBe(1);
    expect(clipGain(0.12345)).toBe(0.123);
    expect(clipGain(Number.NaN)).toBe(DEFAULT_MUSIC_GAIN);
    expect(clipGain(Number.POSITIVE_INFINITY)).toBe(DEFAULT_MUSIC_GAIN);
  });

  it("fits the two fades inside the clip, the fade in kept whole first", () => {
    expect(fitFades(10, 1, 2)).toEqual([1, 2]);
    expect(fitFades(2.5, 1, 2)).toEqual([1, 1.5]);
    expect(fitFades(0.6, 1, 2)).toEqual([0.6, 0]);
    expect(fitFades(3, -1, -1)).toEqual([0, 0]);
    expect(fitFades(3, 0, 5)).toEqual([0, 3]);
    // Exactly the clip's length is allowed - the server's rule is `<=`.
    const [into, outOf] = fitFades(4, 1, 3);
    expect(into + outOf).toBe(4);
  });

  it("sends the eight stored keys and never the read-back's two", () => {
    const read = clip({ file_duration: 9.5, missing: false, at: 5.0004, gain: 2 });
    expect(musicBody([read])).toEqual([
      { id: "m1", file: "bed.mp3", at: 5, in: 0, out: 4, gain: 1, fade_in: 1, fade_out: 2 },
    ]);
    expect(Object.keys(musicBody([read])[0])).toHaveLength(8);
  });

  it("calls two lists the same when the body would be the same", () => {
    expect(sameMusic([clip()], [clip({ file_duration: 10, missing: false })])).toBe(true);
    expect(sameMusic([clip()], [clip({ at: 5.001 })])).toBe(false);
    expect(sameMusic([], [])).toBe(true);
    expect(sameMusic([clip()], [])).toBe(false);
  });

  it("mints an id the server accepts, never one already in the list", () => {
    const random = mulberry32(7);
    const taken = new Set<string>();
    for (let i = 0; i < 500; i++) {
      const id = mintClipId(taken, random);
      expect(id).toMatch(/^[a-z0-9_-]{1,32}$/);
      expect(taken.has(id)).toBe(false);
      taken.add(id);
    }
    // A random source that never moves still answers with a free id.
    const stuck = () => 0.5;
    expect(mintClipId(taken, stuck)).toMatch(/^[a-z0-9_-]{1,32}$/);
    expect(taken.has(mintClipId(taken, stuck))).toBe(false);
  });
});

describe("the clip a Library add makes", () => {
  const add = (over: Partial<Parameters<typeof newMusicClip>[0]> = {}) => newMusicClip({
    id: "m9", file: "bed.mp3", fileDuration: 95.25, at: 12.5, outputDuration: 341.008, gain: 0.15, ...over,
  });

  it("starts at the playhead, at the top of the file, as long as the file", () => {
    expect(add()).toEqual({
      id: "m9", file: "bed.mp3", at: 12.5, in: 0, out: 95.25,
      gain: 0.15, fade_in: DEFAULT_FADE_IN, fade_out: DEFAULT_FADE_OUT,
    });
  });

  it("stops at the end of the output when the file would run past it", () => {
    const made = add({ at: 300, fileDuration: 95.25 });
    expect(made?.out).toBe(41.008);
    expect(made ? clipEnd(made) : 0).toBe(341.008);
  });

  it("cuts the default fades down to a short clip", () => {
    expect(add({ fileDuration: 2.5 })).toMatchObject({ out: 2.5, fade_in: 1, fade_out: 1.5 });
    expect(add({ fileDuration: 0.4 })).toMatchObject({ out: 0.4, fade_in: 0.4, fade_out: 0 });
  });

  it("refuses when there is less than the minimum length of room", () => {
    expect(add({ at: 341 })).toBeNull();
    expect(add({ at: 340.95 })).toBeNull();
    expect(add({ fileDuration: 0.05 })).toBeNull();
    // Exactly the minimum is a clip, not a refusal.
    expect(add({ at: 340.908 })?.out).toBe(0.1);
  });

  it("clamps the gain and the playhead, and falls back to the file when no output length is known", () => {
    expect(add({ gain: 9 })?.gain).toBe(1);
    expect(add({ gain: Number.NaN })?.gain).toBe(DEFAULT_MUSIC_GAIN);
    expect(add({ at: -4 })?.at).toBe(0);
    expect(add({ outputDuration: 0, fileDuration: 8 })).toMatchObject({ at: 12.5, out: 8 });
  });
});

describe("moving and trimming a clip", () => {
  it("moves only `at`, never the slice, and never before zero", () => {
    const moved = moveClip(clip({ at: 5, in: 1, out: 4 }), 8.2504);
    expect(moved).toMatchObject({ at: 8.25, in: 1, out: 4 });
    expect(moveClip(clip(), -3).at).toBe(0);
  });

  it("stops the drag at the end of the audition, so a clip cannot be parked past it", () => {
    // 4 s long: the last place it fits on a 20 s audition is 16.
    const bed = clip({ at: 5, in: 0, out: 4 });
    expect(moveClip(bed, 20, 20).at).toBe(16);
    expect(moveClip(bed, 1e6, 20).at).toBe(16);
    expect(moveClip(bed, 12, 20).at).toBe(12);
    // Exactly as long as the audition: it fits in one place, at 0.
    expect(moveClip(bed, 9, 4).at).toBe(0);
    // With no limit named - the callers that have no length to offer - only the floor applies.
    expect(moveClip(bed, 1e6).at).toBe(1e6);
  });

  it("leaves a clip LONGER than the audition exactly where it is, rather than slamming it to 0", () => {
    // Reachable: `newMusicClip` bounds a new clip to the output, but a picture
    // cut made later with the Music lane locked can leave a clip longer than
    // what remains. `ceiling = max(0, limit - length)` made that ceiling 0, so
    // the FIRST drag moved the clip to 0 and committed it - something the user
    // never asked for - and every drag after that did nothing at all, because
    // it was already there. A drag that cannot land anywhere must change
    // nothing, so the commit's `sameMusic` check drops it.
    const long = clip({ at: 5, in: 0, out: 30 });
    expect(moveClip(long, 8, 20)).toEqual(long);
    expect(moveClip(long, 0, 20)).toEqual(long);
    expect(moveClip(long, -50, 20)).toEqual(long);
    expect(moveClip(long, 1e6, 20)).toEqual(long);
    // A clip already at 0 is equally untouched - and still not moved backwards.
    expect(moveClip(clip({ at: 0, in: 0, out: 30 }), 9, 20).at).toBe(0);
    // An audition with no length yet cannot move a clip either.
    expect(moveClip(long, 9, 0)).toEqual(long);
  });

  it("drags the left edge: `at` and `in` move together, so the audio stays under the pointer", () => {
    const trimmed = trimClip(clip({ at: 5, in: 1, out: 4, fade_in: 0, fade_out: 0 }), "in", 6, FILE_SECONDS);
    expect(trimmed).toMatchObject({ at: 6, in: 2, out: 4 });
    expect(clipLength(trimmed)).toBe(2);
  });

  it("stops the left edge at the top of the file, at zero, and at the minimum length", () => {
    // `in` is 1, so the edge cannot go more than 1 s earlier.
    expect(trimClip(clip({ at: 5, in: 1, out: 4 }), "in", 0, FILE_SECONDS)).toMatchObject({ at: 4, in: 0 });
    // ... and never past `out - 0.1`.
    const short = trimClip(clip({ at: 5, in: 1, out: 4 }), "in", 99, FILE_SECONDS);
    expect(clipLength(short)).toBe(MIN_CLIP_SECONDS);
    expect(short.in).toBe(round3(4 - MIN_CLIP_SECONDS));
    // A clip at the very start of the output cannot be dragged before zero.
    expect(trimClip(clip({ at: 0.5, in: 3, out: 6 }), "in", -5, FILE_SECONDS)).toMatchObject({ at: 0, in: 2.5 });
  });

  it("drags the right edge: `out` moves, never past the file's length nor below the minimum", () => {
    expect(trimClip(clip({ at: 5, in: 1, out: 4 }), "out", 9, FILE_SECONDS)).toMatchObject({ out: 5 });
    expect(trimClip(clip({ at: 5, in: 1, out: 4 }), "out", 99, FILE_SECONDS).out).toBe(FILE_SECONDS);
    expect(clipLength(trimClip(clip({ at: 5, in: 1, out: 4 }), "out", 0, FILE_SECONDS))).toBe(MIN_CLIP_SECONDS);
    // A missing file has no recorded length: the right edge is pinned where it is.
    expect(trimClip(clip({ at: 5, in: 1, out: 4, missing: true }), "out", 99, null).out).toBe(4);
  });

  it("re-fits the fades to the length a trim leaves", () => {
    const trimmed = trimClip(clip({ at: 5, in: 0, out: 8, fade_in: 1, fade_out: 2 }), "out", 6.5, FILE_SECONDS);
    expect(clipLength(trimmed)).toBe(1.5);
    expect(trimmed.fade_in + trimmed.fade_out).toBeLessThanOrEqual(clipLength(trimmed) + EPSILON);
    expect(trimmed).toMatchObject({ fade_in: 1, fade_out: 0.5 });
  });
});

describe("the hit test and its edges", () => {
  const clips = [clip({ id: "a", at: 0, in: 0, out: 4 }), clip({ id: "b", at: 6, in: 0, out: 2 })];
  // 8 px at 40 px per second is a fifth of a second.
  const edge = CLIP_EDGE_PX / 40;

  it("finds the clip under a moment, and nothing between them", () => {
    expect(clipAt(clips, 2, edge)?.clip.id).toBe("a");
    expect(clipAt(clips, 7, edge)?.clip.id).toBe("b");
    expect(clipAt(clips, 5, edge)).toBeNull();
    expect(clipAt(clips, 9, edge)).toBeNull();
    expect(clipAt([], 1, edge)).toBeNull();
  });

  it("answers the edge zones within 8 px of each end, and the body between them", () => {
    expect(clipAt(clips, 0, edge)?.zone).toBe("in");
    expect(clipAt(clips, 0.19, edge)?.zone).toBe("in");
    expect(clipAt(clips, 0.21, edge)?.zone).toBe("body");
    expect(clipAt(clips, 3.79, edge)?.zone).toBe("body");
    expect(clipAt(clips, 3.81, edge)?.zone).toBe("out");
    expect(clipAt(clips, 4, edge)?.zone).toBe("out");
  });

  it("leaves a narrow clip a body to grab: a third at each end, never a half", () => {
    // 0.2 s drawn at 80 px/s is 16 px, and 8 px at each end is half of it: the
    // two zones met in the middle and the clip could be trimmed but never
    // moved. A third each leaves the middle third for the drag.
    const tiny = [clip({ id: "t", at: 1, in: 0, out: 0.2 })];
    const eightPx = CLIP_EDGE_PX / 80;
    expect(clipAt(tiny, 1.02, eightPx)?.zone).toBe("in");
    expect(clipAt(tiny, 1.1, eightPx)?.zone).toBe("body");
    expect(clipAt(tiny, 1.18, eightPx)?.zone).toBe("out");
    // However wide the zone is asked to be, the middle third survives.
    expect(clipAt(tiny, 1.1, 5)?.zone).toBe("body");
  });

  it("gives the topmost clip where two overlap - the last one drawn", () => {
    const stacked = [clip({ id: "under", at: 0, in: 0, out: 6 }), clip({ id: "over", at: 2, in: 0, out: 2 })];
    expect(clipAt(stacked, 3, edge)?.clip.id).toBe("over");
    expect(clipAt(stacked, 1, edge)?.clip.id).toBe("under");
  });
});

describe("the snap candidates", () => {
  it("offers the playhead, the joins, zero, the picture's end and the OTHER clips' ends", () => {
    const clips = [clip({ id: "a", at: 0, in: 0, out: 4 }), clip({ id: "b", at: 6, in: 0, out: 2 })];
    expect(clipSnapTargets({ playhead: 3.5, duration: 20, joins: [6.25], clips, exclude: "b" }))
      .toEqual([0, 20, 3.5, 6.25, 0, 4]);
    // Nothing excluded: both clips' ends are candidates.
    expect(clipSnapTargets({ playhead: 0, duration: 20, joins: [], clips })).toEqual([0, 20, 0, 0, 4, 6, 8]);
  });
});

describe("the waveform inside a clip", () => {
  // One bucket a second makes the slicing easy to read.
  const peaks = [10, 20, 30, 40, 50, 60, 70, 80];

  it("is the file's peaks for `in`…`out`, through the audio lane's own rule", () => {
    expect(clipPeaks(peaks, 1, clip({ in: 2, out: 5 }))).toEqual([30, 40, 50]);
    expect(clipPeaks(peaks, 1, clip({ in: 0, out: 8 }))).toEqual(peaks);
  });

  it("is about as long as the clip, and never reads past the array", () => {
    expect(clipPeaks(peaks, 0.5, clip({ in: 1, out: 3 }))).toHaveLength(4);
    expect(clipPeaks(peaks, 1, clip({ in: 7.5, out: 9 }))).toEqual([80, 80]);
    expect(clipPeaks([], 1, clip())).toEqual([]);
    expect(clipPeaks(peaks, 0, clip())).toEqual([]);
  });
});

describe("the audition's placement and its fades", () => {
  it("places a clip from the audition's start", () => {
    expect(clipPlayback(clip({ at: 12.5, in: 0, out: 95.25 }), 0)).toEqual({ delay: 12.5, offset: 0, length: 95.25 });
  });

  it("places one the playhead is already inside, from where it is", () => {
    expect(clipPlayback(clip({ at: 10, in: 2, out: 8 }), 13)).toEqual({ delay: 0, offset: 5, length: 3 });
  });

  it("skips a clip that is over, and one whose file is missing (trap 25)", () => {
    expect(clipPlayback(clip({ at: 0, in: 0, out: 4 }), 4)).toBeNull();
    expect(clipPlayback(clip({ at: 0, in: 0, out: 4 }), 9)).toBeNull();
    expect(clipPlayback(clip({ at: 12, missing: true }), 0)).toBeNull();
  });

  it("reads the level off the linear envelope", () => {
    const bed = clip({ at: 0, in: 0, out: 10, gain: 0.5, fade_in: 2, fade_out: 4 });
    expect(fadeLevel(bed, 0)).toBe(0);
    expect(fadeLevel(bed, 1)).toBe(0.25);
    expect(fadeLevel(bed, 2)).toBe(0.5);
    expect(fadeLevel(bed, 5)).toBe(0.5);
    expect(fadeLevel(bed, 8)).toBe(0.25);
    expect(fadeLevel(bed, 10)).toBe(0);
    // Outside the clip it is clamped to its ends, never negative.
    expect(fadeLevel(bed, -5)).toBe(0);
    expect(fadeLevel(bed, 99)).toBe(0);
  });

  it("gives the GainNode a point per corner, anchored before every ramp", () => {
    const bed = clip({ at: 0, in: 0, out: 10, gain: 0.5, fade_in: 2, fade_out: 4 });
    expect(fadePoints(bed, 0)).toEqual([
      { at: 0, value: 0, ramp: false },
      { at: 2, value: 0.5, ramp: true },
      { at: 6, value: 0.5, ramp: false },
      { at: 10, value: 0, ramp: true },
    ]);
  });

  it("starts a seek into the middle of a fade at the level it had reached", () => {
    const bed = clip({ at: 0, in: 0, out: 10, gain: 0.5, fade_in: 2, fade_out: 4 });
    expect(fadePoints(bed, 1)).toEqual([
      { at: 0, value: 0.25, ramp: false },
      { at: 1, value: 0.5, ramp: true },
      { at: 5, value: 0.5, ramp: false },
      { at: 9, value: 0, ramp: true },
    ]);
    // Inside the fade OUT: no plateau left, just the ramp to silence.
    expect(fadePoints(bed, 8)).toEqual([
      { at: 0, value: 0.25, ramp: false },
      { at: 2, value: 0, ramp: true },
    ]);
  });

  it("is one flat point for a clip with no fades", () => {
    expect(fadePoints(clip({ in: 0, out: 4, gain: 0.3, fade_in: 0, fade_out: 0 }))).toEqual([
      { at: 0, value: 0.3, ramp: false },
    ]);
  });
});

describe("cutMusic - Camtasia's ripple, applied to the clips", () => {
  it("moves a clip that is wholly after the cut earlier by what was removed, id and fades kept", () => {
    const [moved] = cutMusic([clip({ id: "a", at: 10, in: 0, out: 4 })], 2, 5);
    expect(moved).toMatchObject({ id: "a", at: 7, in: 0, out: 4, fade_in: 1, fade_out: 2 });
  });

  it("leaves a clip that is wholly before the cut exactly as it was", () => {
    const before = clip({ id: "a", at: 0, in: 0, out: 4 });
    expect(cutMusic([before], 6, 9)).toEqual([before]);
  });

  it("removes a clip that lies wholly inside the cut", () => {
    expect(cutMusic([clip({ at: 3, in: 0, out: 2 })], 2, 8)).toEqual([]);
  });

  it("splits a clip that spans the cut: the part before keeps its `at`, the part after starts at `a`", () => {
    const [head, tail] = cutMusic([clip({ id: "a", at: 4, in: 1, out: 11 })], 6, 9, counterMint());
    // The clip runs 4 – 14 over the output, playing 1 – 11 of the file.
    expect(head).toMatchObject({ id: "a", at: 4, in: 1, out: 3 });
    expect(tail).toMatchObject({ id: "new1", at: 6, in: 6, out: 11 });
    // Nothing of the cut survives: the two halves touch at `a`.
    expect(clipEnd(head)).toBe(6);
    expect(clipEnd(tail)).toBe(11);
  });

  it("trims a clip that overlaps one edge of the cut", () => {
    expect(cutMusic([clip({ id: "a", at: 4, in: 0, out: 6 })], 7, 12)[0]).toMatchObject({ at: 4, in: 0, out: 3 });
    expect(cutMusic([clip({ id: "a", at: 4, in: 0, out: 6 })], 2, 7)[0]).toMatchObject({ at: 2, in: 3, out: 6 });
  });

  it("drops a remnant shorter than the minimum length rather than sending one the server refuses", () => {
    // The clip covers 4 – 10 of the output; the cut leaves 0.05 s in front of it.
    const next = cutMusic([clip({ id: "a", at: 4, in: 0, out: 6 })], 4.05, 9);
    expect(next).toHaveLength(1);
    // The surviving half keeps the id, since the head it would have shared it with is gone.
    expect(next[0]).toMatchObject({ id: "a", at: 4.05, in: 5, out: 6 });
    // ... and both remnants too short is an empty lane.
    expect(cutMusic([clip({ at: 4, in: 0, out: 0.3 })], 4.05, 4.25)).toEqual([]);
  });

  it("gives a new edge no fade and re-fits the ones it keeps", () => {
    const [head, tail] = cutMusic([clip({ id: "a", at: 0, in: 0, out: 12, fade_in: 1, fade_out: 2 })], 5, 8, counterMint());
    expect(head).toMatchObject({ fade_in: 1, fade_out: 0 });
    expect(tail).toMatchObject({ fade_in: 0, fade_out: 2 });
    // A head too short for the fade it keeps gets a shorter one.
    const [short] = cutMusic([clip({ at: 0, in: 0, out: 12, fade_in: 4, fade_out: 0 })], 0.5, 9);
    expect(short).toMatchObject({ at: 0, fade_in: 0.5 });
  });

  it("lands a rippled clip exactly ON the cut, with bounds that are not round numbers", () => {
    // A Ctrl+drag selection carries three decimals, and the PICTURE's list
    // rounds its pieces (`removeRange`), so the join lands at 9.986 and
    // everything after it moves by exactly 3.328. The clips must agree with
    // that to the millisecond: rounding the DIFFERENCE as well as the bounds
    // left a 1 ms gap between the join and the clip that should start there
    // (the supervisor's live check, 2026-09-21).
    const clips = [
      clip({ id: "a", at: 8, in: 0, out: 8, fade_in: 0, fade_out: 0 }),   // 8 – 16, across the cut
      clip({ id: "b", at: 20, in: 0, out: 4, fade_in: 0, fade_out: 0 }),  // wholly after it
    ];
    const [head, tail, later] = cutMusic(clips, 9.9856, 13.3144, counterMint());
    expect(head.at).toBe(8);
    expect(clipEnd(head)).toBe(9.986);
    expect(tail.at).toBe(9.986);
    expect(later.at).toBe(16.672);
    // The same holds the other way round: a cut whose bounds round DOWN.
    const [moved] = cutMusic([clip({ id: "c", at: 20, in: 0, out: 4 })], 9.98649, 13.31449);
    expect(moved.at).toBe(16.672);
  });

  it("can hand back one clip more than the cap, which is why the gesture checks first", () => {
    const many = Array.from({ length: MAX_CLIPS }, (_, i) => clip({ id: `c${i}`, at: i * 5, in: 0, out: 4 }));
    // A split through the first clip makes two of it: 201, which the server
    // refuses - so the cut and the split say so instead of sending it.
    expect(cutMusic(many, 1, 1)).toHaveLength(MAX_CLIPS + 1);
  });

  it("splits at a point (`S` on the lane) without moving anything", () => {
    const [head, tail] = cutMusic([clip({ id: "a", at: 2, in: 0, out: 6 })], 5, 5, counterMint());
    expect(head).toMatchObject({ id: "a", at: 2, in: 0, out: 3 });
    expect(tail).toMatchObject({ id: "new1", at: 5, in: 3, out: 6 });
    // On a boundary, at either end, or away from every clip: nothing changes.
    const one = clip({ id: "a", at: 2, in: 0, out: 6 });
    expect(cutMusic([one], 2, 2)).toEqual([one]);
    expect(cutMusic([one], 8, 8)).toEqual([one]);
    expect(cutMusic([one], 0.5, 0.5)).toEqual([one]);
  });

  it("keeps the list ordered by `at` and the ids unique across every clip", () => {
    const clips = [
      clip({ id: "a", at: 0, in: 0, out: 12 }),
      clip({ id: "b", at: 3, in: 0, out: 2 }),
      clip({ id: "c", at: 9, in: 0, out: 3 }),
    ];
    const next = cutMusic(clips, 2, 4, counterMint());
    expect(next.map((c) => c.at)).toEqual([...next.map((c) => c.at)].sort((x, y) => x - y));
    expect(new Set(next.map((c) => c.id)).size).toBe(next.length);
  });

  it("agrees with a brute-force reference that samples the survivors and groups them", () => {
    const random = mulberry32(20260921);
    const step = 0.01;
    for (let round = 0; round < 300; round++) {
      const clips: MusicClip[] = [];
      const count = 1 + Math.floor(random() * 3);
      for (let i = 0; i < count; i++) {
        const start = round3(Math.floor(random() * 12 / 0.05) * 0.05);
        const length = round3(0.1 + Math.floor(random() * 6 / 0.05) * 0.05);
        clips.push(clip({ id: `c${i}`, at: start, in: 0, out: length, fade_in: 0, fade_out: 0 }));
      }
      const lo = round3(Math.floor(random() * 14 / 0.05) * 0.05);
      const hi = round3(lo + Math.floor(random() * 5 / 0.05) * 0.05);

      // The reference: walk each clip's span in small steps, keep the moments
      // the cut leaves, group the survivors into runs, and place each run
      // where the ripple puts it. Written the other way round from the helper
      // on purpose - a sampling loop against a case analysis.
      const expected: { at: number; in: number; out: number }[] = [];
      for (const c of clips) {
        const runs: [number, number][] = [];
        let from: number | null = null;
        let to = 0;
        for (let k = 0; ; k++) {
          const u = round3(c.at + k * step);
          if (u >= clipEnd(c) - EPSILON) break;
          const removed = hi > lo && u >= lo - EPSILON && u < hi - EPSILON;
          // A zero-length cut is a BOUNDARY, not a removal: it ends the run
          // and the next one starts at the same moment.
          const boundary = hi === lo && Math.abs(u - lo) < EPSILON;
          if ((removed || boundary) && from !== null) { runs.push([from, to]); from = null; }
          if (removed) continue;
          if (from === null) from = u;
          to = round3(u + step);
        }
        if (from !== null) runs.push([from, to]);
        for (const [runFrom, runTo] of runs) {
          if (round3(runTo - runFrom) < MIN_CLIP_SECONDS - EPSILON) continue;
          const at = runFrom <= lo ? runFrom : round3(runFrom - (hi - lo));
          expected.push({
            at: round3(at),
            in: round3(runFrom - c.at + c.in),
            out: round3(runTo - c.at + c.in),
          });
        }
      }
      expected.sort((x, y) => x.at - y.at || x.in - y.in);

      const got = cutMusic(clips, lo, hi, counterMint())
        .map((c) => ({ at: c.at, in: c.in, out: c.out }))
        .sort((x, y) => x.at - y.at || x.in - y.in);
      expect(got, `round ${round}: cut ${lo}–${hi}`).toHaveLength(expected.length);
      got.forEach((c, i) => {
        expect(c.at, `round ${round} clip ${i} at`).toBeCloseTo(expected[i].at, 2);
        expect(c.in, `round ${round} clip ${i} in`).toBeCloseTo(expected[i].in, 2);
        expect(c.out, `round ${round} clip ${i} out`).toBeCloseTo(expected[i].out, 2);
      });
    }
  });

  it("leaves a list the model still accepts, over a few thousand random cuts", () => {
    const random = mulberry32(99);
    for (let round = 0; round < 4000; round++) {
      const clips: MusicClip[] = [];
      const count = 1 + Math.floor(random() * 4);
      for (let i = 0; i < count; i++) {
        const start = round3(Math.floor(random() * 30 / 0.05) * 0.05);
        const length = round3(0.1 + Math.floor(random() * 10 / 0.05) * 0.05);
        clips.push(clip({
          id: `c${i}`, at: start, in: round3(Math.floor(random() * 5 / 0.05) * 0.05),
          out: 0, fade_in: round3(random() * 2), fade_out: round3(random() * 2),
        }));
        const made = clips[clips.length - 1];
        made.out = round3(made.in + length);
        const [into, outOf] = fitFades(length, made.fade_in, made.fade_out);
        made.fade_in = into;
        made.fade_out = outOf;
      }
      const lo = round3(Math.floor(random() * 32 / 0.05) * 0.05);
      const hi = round3(lo + Math.floor(random() * 8 / 0.05) * 0.05);
      const fileSeconds = round3(Math.max(...clips.map((c) => c.out)) + 1);
      const next = cutMusic(clips, lo, hi);
      const seen = new Set<string>();
      let previous = -1;
      const before = clips.reduce((sum, c) => sum + clipLength(c), 0);
      let after = 0;
      for (const c of next) {
        expect(c.at, `round ${round}`).toBeGreaterThanOrEqual(0);
        expect(c.at, `round ${round}: ordered by at`).toBeGreaterThanOrEqual(previous);
        previous = c.at;
        expect(c.id).toMatch(/^[a-z0-9_-]{1,32}$/);
        expect(seen.has(c.id), `round ${round}: ${c.id} twice`).toBe(false);
        seen.add(c.id);
        expect(c.in).toBeGreaterThanOrEqual(0);
        expect(c.out).toBeLessThanOrEqual(fileSeconds + EPSILON);
        expect(clipLength(c), `round ${round}: length`).toBeGreaterThanOrEqual(MIN_CLIP_SECONDS - EPSILON);
        expect(c.fade_in).toBeGreaterThanOrEqual(0);
        expect(c.fade_out).toBeGreaterThanOrEqual(0);
        expect(c.fade_in + c.fade_out).toBeLessThanOrEqual(clipLength(c) + EPSILON);
        // Nothing may still lie across the cut: the hole is closed.
        expect(c.at < lo - EPSILON && clipEnd(c) > lo + EPSILON, `round ${round}: spans the cut`).toBe(false);
        after += clipLength(c);
      }
      expect(after).toBeLessThanOrEqual(before + EPSILON);
      expect(next.length).toBeLessThanOrEqual(MAX_CLIPS);
    }
  });
});

describe("the body a commit sends (spec §12.6, decision 2)", () => {
  // The highest-risk decision in E4b, tested as BEHAVIOUR rather than as
  // source text: E3 sent `DELETE /edit` when both tracks went whole, and a
  // DELETE now clears the MUSIC too, so undoing the last picture cut would
  // silently wipe the lane. `editBody` is the one place that decides, so a
  // rename or a reformat of the component cannot defeat these.
  const SOURCE = 12;
  const WHOLE: Keep = [[0, 12]];
  const CUT: Keep = [[0, 6], [7.5, 12]];
  const bed = clip({ id: "m1" });

  it("says a whole track with `null` instead of deleting the edit", () => {
    const body = editBody({ video: WHOLE, narration: null, music: [bed] }, { music: [bed] }, SOURCE);
    expect(body).toEqual({ video: null, narration: null });
    // A whole-source SPLIT is still a list: its boundary is the point.
    expect(editBody({ video: [[0, 6], [6, 12]], narration: null, music: [] }, { music: [] }, SOURCE).video)
      .toEqual([[0, 6], [6, 12]]);
  });

  it("leaves the `music` key OUT when the operation does not change the clips", () => {
    const body = editBody({ video: CUT, narration: CUT, music: [bed] }, { music: [bed] }, SOURCE);
    expect("music" in body).toBe(false);
    expect(body).toEqual({ video: CUT, narration: CUT });
    // The read-back's derived keys are not a change.
    const read = clip({ id: "m1", file_duration: 10, missing: false });
    expect("music" in editBody({ video: CUT, narration: null, music: [read] }, { music: [bed] }, SOURCE)).toBe(false);
  });

  it("sends the clips when they change, as the eight stored keys", () => {
    const moved = clip({ id: "m1", at: 9, file_duration: 10, missing: false });
    const body = editBody({ video: null, narration: null, music: [moved] }, { music: [bed] }, SOURCE);
    expect(body.music).toEqual([
      { id: "m1", file: "bed.mp3", at: 9, in: 0, out: 4, gain: 0.15, fade_in: 1, fade_out: 2 },
    ]);
    // Clearing the lane is an empty list, which the server takes as "no music".
    expect(editBody({ video: null, narration: null, music: [] }, { music: [bed] }, SOURCE).music).toEqual([]);
  });

  it("lets a cut through on a lane whose file has gone, because it sends no `music` key", () => {
    // The owner's rule: the music rides the picture, so a cut with Video
    // locked leaves the clips exactly as they are - and an unchanged list is
    // an ABSENT key, which is what keeps the picture editable while a clip
    // names a file the library has lost (the server refuses to store one).
    const lost = clip({ id: "m1", file: "gone.mp3", missing: true, file_duration: null });
    const body = editBody({ video: CUT, narration: CUT, music: [lost] }, { music: [lost] }, SOURCE);
    expect("music" in body).toBe(false);
    expect(body).toEqual({ video: CUT, narration: CUT });
  });
});

describe("the timeline's own source, where the unit suite cannot reach", () => {
  // Three effects and one gesture that no pure helper can hold, pinned in the
  // file's own `?raw` idiom. They prove the code says the right thing, not
  // that the browser does it: the live check is the supervisor's and the
  // owner's.
  const timeline = timelineSource;
  /** One `const name = useCallback(…)` block, so an assertion cannot be satisfied by another handler. */
  const handler = (name: string): string => {
    const from = timeline.indexOf(`const ${name} = useCallback(`);
    expect(from, `${name} not found`).toBeGreaterThan(-1);
    return timeline.slice(from, timeline.indexOf("\n  }, [", from));
  };

  it("commits every edit with a PUT, through `editBody`, and never deletes the edit", () => {
    expect(timeline).toMatch(/api\.put<EditPayload>\(\s*`\/api\/projects\/\$\{projectId\}\/edit`/);
    expect(timeline).toMatch(/editBody\(op, committedRef\.current, sourceDurationRef\.current\)/);
    expect(timeline).not.toMatch(/api\.delete[^\n]*\/edit/);
  });

  it("carries the music on every undo entry, so an undone cut puts the clips back", () => {
    expect(timeline).toMatch(/type EditState = TrackEdit & \{ music: MusicClip\[\] \}/);
    expect(timeline).toMatch(/committedRef\.current = \{ video: op\.video, narration: op\.narration, music: op\.music \}/);
  });

  it("ripples the clips only when the PICTURE's list changed (the owner's rule)", () => {
    // The rule itself is `musicAfterCut`, tested as a table over all eight
    // lock combinations below; this pins that the CUT is the caller of it.
    expect(timeline).toMatch(/const music = musicAfterCut\(before\.music, held, sel\.start, sel\.end\)/);
    // A split is not subject to it: it changes no clip's `at`.
    expect(timeline).toMatch(/const music = !all && locksRef\.current\.music \? before\.music : cutMusic\(before\.music, at, at\)/);
    // ... and both gestures check the 200-clip cap the ripple can cross.
    expect(timeline.match(/music\.length > MAX_CLIPS/g) ?? []).toHaveLength(2);
    // The scissors is disabled by the rule `canCut` holds, and the keys ask it too.
    expect(timeline).toMatch(/disabled=\{!selection \|\| editLocked \|\| !canCut\(locks\)\}/);
    expect(timeline).toMatch(/if \(!canCut\(held\)\) \{/);
  });

  it("fades the audition with LINEAR ramps only", () => {
    // `exponentialRampToValueAtTime` throws a RangeError on a target of 0, and
    // `fadePoints` always ends a fade-out at 0 - so that mutation does not
    // merely sound wrong, it kills the audition at the first clip with a fade
    // out. It is also decision 4: ffmpeg's `afade` is linear, so the audition
    // must be too or what is heard is not what is rendered.
    expect(timeline).toMatch(/gain\.gain\.linearRampToValueAtTime\(/);
    expect(timeline).toMatch(/gain\.gain\.setValueAtTime\(/);
    expect(timeline).not.toMatch(/exponentialRampToValueAtTime/);
  });

  it("holds the commit lock over a clip drag's release, as it does over every other commit", () => {
    // The E2 MAJOR class: a second gesture landing inside a refetch window
    // overwrites the first. `editLockedRef` is the lock, read at the release
    // because the release is a stable callback.
    expect(timeline).toMatch(/cancelled \|\| editLockedRef\.current \|\| !base \|\| !next/);
  });

  it("selects a music clip WITHOUT seeking, while a sentence block still seeks", () => {
    // The owner's ruling, 2026-09-21: `seek` halts and restarts playback, so a
    // clip click that seeked jumped the playhead back to the clip's start
    // every time the inspector was reached for. The two gestures are meant to
    // differ - a block click still seeks, deliberately (E3).
    expect(handler("onClipClick")).not.toMatch(/seek\(/);
    expect(handler("onClipClick")).toMatch(/setSelectedClip\(clip\.id\)/);
    expect(handler("onBlockClick")).toMatch(/seek\(landedAt \?\? sentence\.pinned_start\)/);
  });

  it("removes clips through the ONE rule, from both the key and the banner's button", () => {
    // What goes is `clipsAfterDelete`, tested as behaviour below. The banner's
    // button is the same gesture aimed at the first stuck clip, not a second
    // rule beside it - and its guard is what keeps it honest after the file
    // has come back and the plan has refetched.
    expect(handler("removeClip")).toMatch(/commitMusic\(clipsAfterDelete\(musicRef\.current, id\)\)/);
    expect(handler("removeMissingClips")).toMatch(/const stuck = clips\.find\(\(clip\) => clip\.missing\)/);
    expect(handler("removeMissingClips")).toMatch(/if \(!stuck\) return;/);
    expect(handler("removeMissingClips")).toMatch(/commitMusic\(clipsAfterDelete\(clips, stuck\.id\)\)/);
  });

  it("passes the audition's own length as the drag's ceiling", () => {
    // `moveClip`'s ceiling is tested directly, but that the COMPONENT hands it
    // `totalRef.current` is only ever said here: with the argument dropped the
    // helper's default is `Infinity` and a clip can be dragged past the end of
    // the strip again, where the render silently drops it
    // (`amix=...:duration=first`) while the Render line still counts it.
    expect(timeline).toMatch(/next = moveClip\(base, landed\.at, totalRef\.current\)/);
  });

  it("waits for the first sentence when every clip on the lane is MISSING", () => {
    // The audition skips a clip whose file has gone (trap 25), so a lane of
    // nothing but missing clips has nothing to hear: Play must wait for the
    // first sentence rather than running the playhead across a silent strip.
    // `musicRef.current.length === 0` - the obvious spelling - gets that wrong.
    expect(handler("togglePlay")).toMatch(/if \(!musicRef\.current\.some\(\(clip\) => !clip\.missing\)\) \{ setWaitingToPlay\(true\); return; \}/);
  });

  it("invalidates the PLAN on a library change, not just the library", () => {
    // The banner offers two ways out, and the second one - put the file back
    // under the same name - left the app lying: `missing` is the server's
    // answer and reaches the strip only through the plan, which an upload did
    // not invalidate. The banner then still claimed the lane was frozen and
    // its red button was still armed over clips whose file had come back.
    // ONE mechanism for both mutations, so a delete cannot drift from an
    // upload; the keys themselves are `libraryChangeKeys`, tested below.
    expect(librarySource.match(/onSuccess: libraryChanged,/g) ?? []).toHaveLength(2);
    expect(librarySource).toMatch(
      /for \(const queryKey of libraryChangeKeys\(projectId\)\) void qc\.invalidateQueries\(\{ queryKey \}\);/,
    );
    // ... and the timeline hands the modal the project whose plan that is.
    expect(timeline).toMatch(/<MusicLibrary\s+open=\{libraryOpen\}\s+onClose=\{\(\) => setLibraryOpen\(false\)\}\s+projectId=\{projectId\}/);
  });
});

describe("what a change to the LIBRARY makes stale", () => {
  // The behavioural half of the same rule: the keys really do reach the two
  // queries that carry the server's `missing` answer, and nothing else.
  const OTHER = "p2";

  function seeded() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // The plan's key carries the Re-voice card's provider, voice and speed, so
    // the library's key must match it as a PREFIX or the refetch never happens.
    qc.setQueryData([...narrationPlanKey("p1"), "edge", "en-GB", 1], { sentences: [] });
    qc.setQueryData([...narrationPlanKey("p1"), "elevenlabs", "rachel", 1.1], { sentences: [] });
    qc.setQueryData(["edit", "p1"], { version: 2 });
    qc.setQueryData(musicLibraryKey, { files: [] });
    qc.setQueryData([...narrationPlanKey(OTHER), "edge", "en-GB", 1], { sentences: [] });
    qc.setQueryData(["edit", OTHER], { version: 2 });
    qc.setQueryData(["waveform", "p1"], { peaks: [] });
    return qc;
  }
  const invalidate = (qc: QueryClient, projectId: string) => {
    for (const queryKey of libraryChangeKeys(projectId)) void qc.invalidateQueries({ queryKey });
  };
  const stale = (qc: QueryClient, key: unknown[]) => qc.getQueryState(key)?.isInvalidated === true;

  it("marks the library, EVERY variant of the plan, and the page's copy of the edit", () => {
    const qc = seeded();
    expect(stale(qc, [...narrationPlanKey("p1"), "edge", "en-GB", 1])).toBe(false);
    invalidate(qc, "p1");
    expect(stale(qc, musicLibraryKey)).toBe(true);
    expect(stale(qc, [...narrationPlanKey("p1"), "edge", "en-GB", 1])).toBe(true);
    expect(stale(qc, [...narrationPlanKey("p1"), "elevenlabs", "rachel", 1.1])).toBe(true);
    expect(stale(qc, ["edit", "p1"])).toBe(true);
  });

  it("leaves another project's plan and edit alone, and the waveform with them", () => {
    const qc = seeded();
    invalidate(qc, "p1");
    expect(stale(qc, [...narrationPlanKey(OTHER), "edge", "en-GB", 1])).toBe(false);
    expect(stale(qc, ["edit", OTHER])).toBe(false);
    // The peaks are cached on the audio file's own mtime and never change.
    expect(stale(qc, ["waveform", "p1"])).toBe(false);
  });

  it("is the same two keys a successful commit invalidates", () => {
    // One list, so the modal's path and the commit's cannot drift apart.
    expect(libraryChangeKeys("p1")).toEqual([musicLibraryKey, narrationPlanKey("p1"), ["edit", "p1"]]);
    expect(timelineSource).toMatch(/void qc\.invalidateQueries\(\{ queryKey: narrationPlanKey\(projectId\) \}\);/);
    expect(timelineSource).toMatch(/void qc\.invalidateQueries\(\{ queryKey: \["edit", projectId\] \}\);/);
  });
});

describe("what a Delete leaves (clipsAfterDelete)", () => {
  const live = (id: string, at: number) => clip({ id, at, file: "bed.mp3", missing: false });
  const gone = (id: string, at: number) => clip({ id, at, file: "gone.mp3", missing: true, file_duration: null });

  it("takes an ordinary clip alone", () => {
    const clips = [live("m1", 0), live("m2", 5), live("m3", 10)];
    expect(clipsAfterDelete(clips, "m2").map((held) => held.id)).toEqual(["m1", "m3"]);
  });

  it("takes EVERY missing clip with a missing one, because no other list can be stored", () => {
    // The spec's own ordinary case: the same bed laid twice (§12.2 - there is
    // no looping), its file gone. Removing one half leaves the other in the
    // body and the server refuses by the name of the clip nobody touched.
    const clips = [gone("m1", 0), live("m2", 5), gone("m3", 10)];
    expect(clipsAfterDelete(clips, "m1").map((held) => held.id)).toEqual(["m2"]);
    expect(clipsAfterDelete(clips, "m3").map((held) => held.id)).toEqual(["m2"]);
    // Every clip missing clears the lane, which is the body that unfreezes it.
    expect(clipsAfterDelete([gone("m1", 0), gone("m2", 5)], "m2")).toEqual([]);
  });

  it("leaves the missing clips exactly where they are when a LIVE clip goes", () => {
    // The lane stays frozen, and the banner still describes it - the live
    // clip's own delete is refused by the server, which is the state B1 says
    // the banner's button is the only escape from.
    const clips = [gone("m1", 0), live("m2", 5)];
    expect(clipsAfterDelete(clips, "m2")).toEqual([clips[0]]);
  });

  it("changes nothing for an id the list does not hold", () => {
    // A stale selection, or the banner's button pressed after the file came
    // back and the plan refetched: `missing` is gone, so the guard finds
    // nothing stuck and this is never reached - but if it were, the lane must
    // not be cleared.
    const clips = [live("m1", 0), gone("m2", 5)];
    expect(clipsAfterDelete(clips, "nope")).toEqual(clips);
    expect(clipsAfterDelete([], "m1")).toEqual([]);
  });

  it("never re-orders or rewrites the clips it keeps", () => {
    const clips = [live("m3", 9), live("m1", 0), live("m2", 5)];
    expect(clipsAfterDelete(clips, "m1")).toEqual([clips[0], clips[2]]);
    // The same objects, not copies: a delete is not an edit of what remains.
    expect(clipsAfterDelete(clips, "m1")[0]).toBe(clips[0]);
  });
});

describe("what a cut leaves on the lane (musicAfterCut, the owner's ruling)", () => {
  // THE MUSIC RIDES THE PICTURE: `at` is in OUTPUT seconds, so the ripple
  // applies exactly when the picture's own list changed - exactly when Video
  // is unlocked. The eight combinations as a table, rather than a regex over a
  // 164 kB component.
  const before = [clip({ id: "m1", at: 0, in: 0, out: 2 }), clip({ id: "m2", at: 8, in: 0, out: 3 })];
  const locks = (video: boolean, narration: boolean, music: boolean) => ({ video, narration, music });
  const CASES: [boolean, boolean, boolean, boolean][] = [
    // video, narration, music, does the cut ripple the clips?
    [false, false, false, true],
    [false, true, false, true],
    [false, false, true, false],
    [false, true, true, false],
    [true, false, false, false],
    [true, true, false, false],
    [true, false, true, false],
    [true, true, true, false],
  ];

  it.each(CASES)("video=%s narration=%s music=%s -> ripples=%s", (video, narration, music, ripples) => {
    const after = musicAfterCut(before, locks(video, narration, music), 3, 5);
    if (ripples) expect(after).toEqual(cutMusic(before, 3, 5));
    else expect(after).toBe(before);
  });

  it("really moves the clips when it ripples, and really does not when it does not", () => {
    // The table above would pass if `cutMusic` were a no-op, so the one case
    // that matters is spelled out: the clip after a 2 s cut moves 2 s earlier.
    expect(musicAfterCut(before, locks(false, false, false), 3, 5).map((held) => held.at)).toEqual([0, 6]);
    expect(musicAfterCut(before, locks(true, false, false), 3, 5).map((held) => held.at)).toEqual([0, 8]);
  });

  it("hands the list back untouched when the lane is locked, whatever the picture does", () => {
    // Trap 19: a locked lane is left exactly as it is, and identity is the
    // proof - not a deep-equal that a rebuilt list would also satisfy.
    for (const held of [locks(false, false, true), locks(true, false, true), locks(true, true, true)]) {
      expect(musicAfterCut(before, held, 0, 12)).toBe(before);
    }
  });

  it("splits a clip the cut runs through, when it ripples at all", () => {
    const spanning = [clip({ id: "m1", at: 0, in: 0, out: 10, fade_in: 0, fade_out: 0 })];
    const after = musicAfterCut(spanning, locks(false, false, false), 3, 5);
    expect(after).toHaveLength(2);
    expect(after.map((held) => held.at)).toEqual([0, 3]);
    expect(musicAfterCut(spanning, locks(true, false, false), 3, 5)).toBe(spanning);
  });
});

describe("the lanes and their locks", () => {
  it("has three lanes the locks cover, music under the two tracks", () => {
    expect(LANES).toEqual(["video", "narration", "music"]);
  });

  it("reads a lock value written before the Music lane existed as music UNLOCKED", () => {
    expect(laneLocks({ video: true, narration: false })).toEqual({ video: true, narration: false, music: false });
    expect(laneLocks({ video: false, narration: true, music: true })).toEqual({ video: false, narration: true, music: true });
  });

  it("reads anything else as unlocked", () => {
    for (const held of [null, undefined, 3, "locked", [], { music: "yes" }]) {
      expect(laneLocks(held)).toEqual({ video: false, narration: false, music: false });
    }
  });

  it("leaves a clip's own click to the clip, and swallows nothing extra", () => {
    expect(unmovedRelease("clip", false, false)).toBe("click");
    expect(unmovedRelease("trim", false, false)).toBe("click");
    expect(unmovedRelease("clip", false, true)).toBe("nothing");
  });

  it("disables the scissors when Music is the ONLY unlocked lane, and never otherwise", () => {
    // The music rides the picture, so with the picture locked a cut moves no
    // clip: the gesture would appear to work and do nothing (the owner's
    // ruling, 2026-09-21). Every other combination has something to cut.
    const locks = (video: boolean, narration: boolean, music: boolean) => ({ video, narration, music });
    expect(canCut(locks(false, false, false))).toBe(true);
    expect(canCut(locks(true, false, false))).toBe(true);
    expect(canCut(locks(false, true, false))).toBe(true);
    expect(canCut(locks(false, false, true))).toBe(true);
    expect(canCut(locks(true, false, true))).toBe(true);
    expect(canCut(locks(false, true, true))).toBe(true);
    // Music alone - what clicking the Music channel's name produces.
    expect(canCut(locks(true, true, false))).toBe(false);
    // Nothing unlocked at all.
    expect(canCut(locks(true, true, true))).toBe(false);
  });
});

describe("where a dragged clip lands (snapClip)", () => {
  // The playhead, the picture's end, a join, and the other clip's start and end.
  const targets = [0, 20, 3.5, 6.25, 4, 10];

  it("catches a candidate with the clip's START, and says which", () => {
    expect(snapClip(3.45, 2, targets, 0.2)).toEqual({ at: 3.5, snapped: 3.5 });
  });

  it("falls back to the clip's END when nothing is near its start", () => {
    // The end lands on 10, so the clip starts two seconds before it.
    expect(snapClip(7.95, 2, targets, 0.2)).toEqual({ at: 8, snapped: 10 });
  });

  it("leaves a drag between candidates exactly where it is", () => {
    expect(snapClip(14, 2, targets, 0.2)).toEqual({ at: 14, snapped: null });
    expect(snapClip(14.0004, 2, targets, 0.2)).toEqual({ at: 14, snapped: null });
    // No candidates at all is a free drag, not a throw.
    expect(snapClip(14, 2, [], 0.2)).toEqual({ at: 14, snapped: null });
  });

  it("prefers the start when both ends are in reach of one", () => {
    expect(snapClip(3.45, 2, [3.5, 5.5], 0.2)).toEqual({ at: 3.5, snapped: 3.5 });
  });

  it("never lands before zero, by either edge", () => {
    expect(snapClip(-3, 2, targets, 0.2).at).toBe(0);
    // The END would catch 0.05 and put the start at -1.95: clamped to 0.
    expect(snapClip(-1.9, 2, [0.05], 0.2)).toEqual({ at: 0, snapped: 0.05 });
  });
});

describe("what a refused commit says", () => {
  const said = "music clip 2 (sting): file 'musicB.mp3' is not in the library.";

  it("leads with what happened, and keeps the server's own sentence after it", () => {
    const line = editRefusal(said, "edit");
    expect(line.startsWith("That edit was not saved")).toBe(true);
    // Never swallowed: a refusal this client does not recognise must still arrive whole.
    expect(line).toContain(said);
    expect(editRefusal("Something nobody here has ever seen.", "edit"))
      .toContain("Something nobody here has ever seen.");
  });

  it("says the lane is frozen, and points at the button, only while clips are stuck", () => {
    const stuck = editRefusal(said, "edit", 2);
    expect(stuck).toContain("2 clips name files");
    expect(stuck).toContain("the button above");
    expect(editRefusal(said, "edit", 1)).toContain("one clip names a file");
    expect(editRefusal(said, "edit", 0)).not.toContain("Music lane");
  });

  it("never blames the missing clips for a refusal that is not about them", () => {
    // A clip whose file has gone is a state a project can sit in for a whole
    // session, and while it did, EVERY other 400 had the frozen-lane paragraph
    // appended to it. The count alone is not the test: the server's own
    // sentence has to say the refusal is the library's.
    for (const other of [
      "A job holds the project.",
      "Keep at least one range.",
      "music clip 1 (bed): fade_in + fade_out is longer than the clip.",
      "Something the client has never seen: constraint 7 failed.",
    ]) {
      const line = editRefusal(other, "edit", 2);
      expect(line).not.toContain("Music lane");
      expect(line).not.toContain("the button above");
      // A3's promise is untouched: the detail still arrives whole.
      expect(line).toContain(other);
    }
  });

  it("recognises the library's refusal however the clip in front of it is named", () => {
    // `services/edit.py::_check_music` names the clip by its POSITION and its
    // id, and both vary; the phrase is what is matched.
    for (const detail of [
      "music clip 2 (sting): file 'musicB.mp3' is not in the library.",
      "music clip 17 (m4f2a): file 'bed.mp3' is not in the library.",
      "File is Not In The Library.",
    ]) {
      expect(editRefusal(detail, "edit", 1)).toContain("one clip names a file");
    }
    // ... and an empty detail says nothing about the lane either: with no
    // sentence from the server there is nothing to attribute.
    expect(editRefusal("", "edit", 2)).not.toContain("Music lane");
  });

  it("names the TIMING for an offsets refusal, and never blames the music for it", () => {
    const line = editRefusal("A job holds the project.", "timing", 2);
    expect(line.startsWith("That timing was not saved")).toBe(true);
    expect(line).not.toContain("Music lane");
    expect(line).toContain("A job holds the project.");
  });

  it("is the lead alone when the server said nothing at all", () => {
    expect(editRefusal("   ", "edit")).toBe(
      "That edit was not saved — the strip still shows the cut and the clips the server holds.",
    );
  });
});

describe("the Render line, with music under it", () => {
  const SOURCE = 12;
  const WHOLE = null;

  it("says nothing when nothing is cut and there is no music", () => {
    expect(renderSummary(WHOLE, WHOLE, SOURCE, [])).toBeNull();
    expect(renderSummary(WHOLE, WHOLE, SOURCE)).toBeNull();
  });

  it("is E3's line, word for word, when there is no music", () => {
    expect(renderSummary([[0, 6], [7.5, 12]], [[0, 6], [7.5, 12]], SOURCE))
      .toBe("Cuts 1 range (1.5 s removed) and re-voices — about 10 s, plus any sentences the audition has not fetched yet.");
  });

  it("mixes the clips into the same sentence, joined with a COMMA (§12.5)", () => {
    // One "and", as the spec writes the line: "Cuts 1 range of the picture
    // (5.0 s removed), mixes 2 music clips under the narration and re-voices".
    // Joined with "and" it read "… and mixes … and re-voices", which is two
    // joins where the sentence has room for one.
    expect(renderSummary(WHOLE, WHOLE, SOURCE, [clip(), clip({ id: "m2" })]))
      .toBe("Mixes 2 music clips under the narration and re-voices — the picture is not cut, so it takes "
        + "only as long as the music mix and the sentences the audition has not fetched yet.");
    expect(renderSummary([[0, 6], [7.5, 12]], [[0, 6], [7.5, 12]], SOURCE, [clip()]))
      .toBe("Cuts 1 range (1.5 s removed), mixes 1 music clip under the narration and re-voices — "
        + "about 10 s, plus the music mix and any sentences the audition has not fetched yet.");
    // The picture cut alone, with the narration's own clause between them.
    expect(renderSummary([[0, 6], [7.5, 12]], null, SOURCE, [clip()]))
      .toBe("Cuts 1 range of the picture (1.5 s removed), mixes 1 music clip under the narration and "
        + "re-voices — about 10 s, plus the music mix and any sentences the audition has not fetched yet.");
    expect(renderSummary([[0, 6], [7.5, 12]], [[0, 6.4], [7, 12]], SOURCE, [clip()]))
      .toBe("Cuts 1 range of the picture (1.5 s removed) and shortens the narration's timeline by 0.6 s "
        + "(sentences spoken in the removed stretch are left out), mixes 1 music clip under the narration "
        + "and re-voices — about 10 s, plus the music mix and any sentences the audition has not fetched yet.");
  });

  it("says the render will REFUSE while a clip names a file that has gone", () => {
    const line = renderSummary(WHOLE, WHOLE, SOURCE, [clip({ missing: true, file: "gone.mp3" })]);
    expect(line).toContain("Mixes 1 music clip under the narration");
    expect(line).toContain("The render will refuse while 1 music clip names a file that is not in the library "
      + "(gone.mp3): remove the clip or upload the file again.");
  });

  it("names each missing file once, however many clips use it", () => {
    const line = renderSummary(WHOLE, WHOLE, SOURCE, [
      clip({ id: "a", missing: true, file: "gone.mp3" }),
      clip({ id: "b", missing: true, file: "gone.mp3" }),
      clip({ id: "c", missing: false, file: "bed.mp3" }),
    ]);
    expect(line).toContain("Mixes 3 music clips under the narration");
    expect(line).toContain("refuse while 2 music clips name a file that is not in the library (gone.mp3): "
      + "remove those clips or upload the file again.");
  });
});
