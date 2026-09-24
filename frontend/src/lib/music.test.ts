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
  missingAcross,
  missingAcrossRefusal,
  moveClip,
  musicAfterInsert,
  musicAfterTrim,
  musicBody,
  newMusicClip,
  renderSummary,
  round3,
  sameMusic,
  snapClip,
  trimClip,
  unmovedRelease,
  withoutMissing,
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

  it("gives a MISSING clip a body and no edges: a press on either end is a move, never a trim", () => {
    // E4c: the server keeps a stored clip whose file has gone but not with
    // a changed slice, so the strip must never start a trim on one. The
    // same moments that are edges on a live clip are its body here.
    const gone = [clip({ id: "g", at: 0, in: 0, out: 4, missing: true, file_duration: null })];
    expect(clipAt(gone, 0, edge)).toEqual({ clip: gone[0], zone: "body" });
    expect(clipAt(gone, 0.19, edge)?.zone).toBe("body");
    expect(clipAt(gone, 2, edge)?.zone).toBe("body");
    expect(clipAt(gone, 3.81, edge)?.zone).toBe("body");
    expect(clipAt(gone, 4, edge)?.zone).toBe("body");
    // ... and it is still found where it is, and not beyond it.
    expect(clipAt(gone, 4.1, edge)).toBeNull();
    // A live clip beside it keeps its edges.
    const mixed = [...gone, clip({ id: "live", at: 6, in: 0, out: 2 })];
    expect(clipAt(mixed, 6, edge)?.zone).toBe("in");
    expect(clipAt(mixed, 0, edge)?.zone).toBe("body");
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
    // ... and all three gestures that ripple the clips - the cut, the split
    // and (E5a) the trim - check the 200-clip cap the ripple can cross.
    expect(timeline.match(/music\.length > MAX_CLIPS/g) ?? []).toHaveLength(3);
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

  it("removes ONE clip from the key and EVERY missing clip from the banner's button, each through its own rule", () => {
    // What goes is tested as behaviour below: `clipsAfterDelete` for the
    // key and the inspector's button, `withoutMissing` for the banner's.
    // Two gestures since E4c, so two rules - and the banner's guard is what
    // keeps it honest after the file has come back and the plan has
    // refetched: a list with nothing missing comes back as the same array,
    // and nothing is committed.
    expect(handler("removeClip")).toMatch(/commitMusic\(clipsAfterDelete\(musicRef\.current, id\)\)/);
    expect(handler("removeMissingClips")).toMatch(/const kept = withoutMissing\(clips\);/);
    expect(handler("removeMissingClips")).toMatch(/if \(kept === clips\) return;/);
    expect(handler("removeMissingClips")).toMatch(/commitMusic\(kept\)/);
    expect(handler("removeMissingClips")).not.toMatch(/clipsAfterDelete/);
  });

  it("refuses a cut or a split across a MISSING clip before any PUT, in the gesture's own words", () => {
    // The Reviewer's M1: the server refuses a changed slice of a missing
    // clip with a sentence about a trim, which is not what the user did. Both
    // gestures ask `missingAcross` first and refuse with the strip's own
    // sentence (`missingAcrossRefusal`, tested above) before `commitEdit` -
    // the cut only where the ripple applies (the two locks `musicAfterCut`
    // reads), the split wherever the music would be split at all.
    const cut = handler("cutSelection");
    expect(cut).toMatch(
      /if \(!held\.music && !held\.video\) \{\s*const across = missingAcross\(before\.music, sel\.start, sel\.end\);\s*if \(across\.length > 0\) \{\s*setRefusal\(missingAcrossRefusal\("cut", across, sel\.start, sel\.end\)\);\s*return;/,
    );
    expect(cut.indexOf("missingAcross(")).toBeLessThan(cut.indexOf("commitEdit("));
    const split = handler("splitAtPlayhead");
    expect(split).toMatch(
      /if \(all \|\| !locksRef\.current\.music\) \{\s*const across = missingAcross\(before\.music, at, at\);\s*if \(across\.length > 0\) \{\s*setRefusal\(missingAcrossRefusal\("split", across, at\)\);\s*return;/,
    );
    expect(split.indexOf("missingAcross(")).toBeLessThan(split.indexOf("cutMusic("));
    expect(split.indexOf("missingAcross(")).toBeLessThan(split.indexOf("commitEdit("));
  });

  it("draws no trim zones on a missing clip, and the refusal copy carries no frozen-lane paragraph", () => {
    // The edge overlays are what carry the ew-resize cursor; a missing clip
    // has a body and no edges (E4c), so they are not rendered for it - the
    // render harness proves the markup, this pins the condition.
    expect(timeline).toMatch(/\{!held\.missing && \(\s*<>\s*<span className="os-tl-clip-edge in"/);
    // `editRefusal` takes the detail and the kind, nothing about the lane.
    expect(timeline).toMatch(/editRefusal\(errorMessage\(commit\.error\), commit\.variables\?\.op\.kind === "offsets" \? "timing" : "edit"\)/);
    expect(timeline).not.toMatch(/frozen|unfreeze|cannot be changed until/);
  });

  it("snaps the handles, a range's ends and a trimmed edge to ONE candidate set, built once per gesture (E5a)", () => {
    // The set is `snapTargets` (tested in edit.test.ts); this pins that
    // `beginDrag` builds it once for exactly these kinds (trap 41) and that
    // the pins are the drawn blocks' starts and ends.
    expect(timeline).toMatch(
      /const snapTo = move\?\.snapTo \?\? \(kind === "in" \|\| kind === "out" \|\| kind === "range" \|\| kind === "piece-trim" \? snapTargetsNow\(\) : undefined\)/,
    );
    expect(handler("snapTargetsNow")).toMatch(/pins: sentencesRef\.current\.flatMap\(\(s\) => \[s\.pinned_start, s\.pinned_start \+ \(s\.end - s\.start\)\]\)/);
    expect(handler("snapTargetsNow")).toMatch(/joins: \[\.\.\.joinsRef\.current, \.\.\.narrationJoinsRef\.current\]/);
    // The handle / range branch and the trim branch snap through `drag.snapTo`
    // unless the magnet is off or Alt is held - the same line in both.
    const free = timeline.match(/const free = !snappingRef\.current \|\| event\.altKey;\s*const landed = free \|\| !drag\.snapTo \? \{ t, snapped: null \} : snap\(t, drag\.snapTo, SNAP_PX \/ ppsRef\.current\);/g) ?? [];
    expect(free).toHaveLength(2);
    // The handle's label gains ⌖ from `paint`, off the drag, on the moving end.
    expect(handler("paint")).toMatch(/const caught = drag && drag\.moved && drag\.snapped !== null && drag\.snapped !== undefined \? drag\.snapEnd : undefined;/);
    expect(handler("paint")).toMatch(/\$\{caught === "start" \? " ⌖" : ""\}/);
    expect(handler("paint")).toMatch(/\$\{caught === "end" \? " ⌖" : ""\}/);
  });

  it("lets the magnet govern the block drag and the clip drag too, with Ctrl kept as their synonym for Alt (E5a)", () => {
    expect(handler("onBodyPointerMove")).toMatch(/const free = !snappingRef\.current \|\| event\.ctrlKey \|\| event\.metaKey \|\| event\.altKey;/);
    expect(handler("onBodyPointerMove")).toMatch(/if \(snappingRef\.current && !\(event\.ctrlKey \|\| event\.metaKey \|\| event\.altKey\) && drag\.snapTo\) \{/);
    // Remembered per project beside the locks; anything but a stored `false` is on.
    expect(timeline).toMatch(/const snapKey = \(projectId: string\) => `ms:tl-snap:\$\{projectId\}`;/);
    expect(timeline).toMatch(/return localStorage\.getItem\(snapKey\(projectId\)\) !== "false";/);
  });

  it("commits a trim through nextEditForTrim, then musicAfterTrim on the PICTURE's change, then commitEdit (E5a)", () => {
    // A trim is a cut with a name (trap 37): one PUT on release through the
    // same `commitEdit` a cut uses, the clips following the picture's own
    // change - never merely the trimmed lane's - and nothing sent for a
    // no-op, a refusal, or a release under the commit lock.
    const up = handler("onBodyPointerUp");
    expect(up).toMatch(/if \(cancelled \|\| editLockedRef\.current \|\| !now \|\| lane === undefined \|\| index === undefined \|\| edge === undefined\) return;/);
    expect(up).toMatch(/const outcome = nextEditForTrim\(before, held, lane, index, edge, now\.toSource, sourceDurationRef\.current\);/);
    expect(up).toMatch(/const music = musicAfterTrim\(before\.music, held, outcome\.picture\);/);
    expect(up).toMatch(/commitEdit\(\{ \.\.\.outcome\.next, music \}\);/);
    expect(up.indexOf("nextEditForTrim(")).toBeLessThan(up.indexOf("musicAfterTrim("));
    expect(up.indexOf("musicAfterTrim(")).toBeLessThan(up.indexOf("commitEdit({ ...outcome.next, music })"));
    // A shortening across a MISSING clip is refused before the PUT, as the cut's is.
    expect(up).toMatch(/if \(picture\?\.kind === "cut" && !held\.music && !held\.video\) \{\s*const across = missingAcross\(before\.music, picture\.a, picture\.b\);\s*if \(across\.length > 0\) \{\s*setRefusal\(missingAcrossRefusal\("trim", across, picture\.a, picture\.b\)\);\s*return;/);
    // The gesture begins only from an edge `pieceEdgeAt` answers, on an unlocked lane, outside the commit lock.
    const down = handler("onPiecesPointerDown");
    expect(down).toMatch(/if \(editLocked \|\| locksRef\.current\[lane\]\) return;/);
    expect(down).toMatch(/const hit = pieceEdgeAt\(list, secondsAt\(event\.clientX\), CLIP_EDGE_PX \/ ppsRef\.current\);/);
    expect(down).toMatch(/beginDrag\(event, "piece-trim", grabbed, grabbed, \{ lane, pieceIndex: hit\.index, edge: hit\.edge \}\)/);
    // The live edge is the model's bound (`trimPiece`), painted, never state.
    const move = handler("onBodyPointerMove");
    expect(move).toMatch(/const trimmed = trimPiece\(list, index, edge, oldBound \+ \(landed\.t - oldAt\), source\);/);
    expect(move).toMatch(/paintTrim\(drag, oldAt, at, trimChange\(list, index, edge, bound, source\), atFrameFloor\(trimmed, index\), caughtAfterClamp\(at, landed\.snapped\)\);/);
  });

  it("claims ⌖ only for a candidate the painted thing really sits on, after every clamp (the Reviewer's MINOR 1)", () => {
    // `snap` lands the pointer; the model's clamp can then hold the thing
    // short of the candidate, and ⌖ means "caught". The rule is
    // `caughtAfterClamp` (edit.test.ts walks the trim); this pins that all
    // five clamped drags pass the PAINTED position through it - the piece
    // trim, the clip trim, the clip move (by its start OR its end, since
    // `snapClip` may catch by either), the handles and the block - never
    // the pointer's.
    const move = handler("onBodyPointerMove");
    expect(move).toMatch(/caughtAfterClamp\(at, landed\.snapped\)/);
    expect(move).toMatch(/next = moveClip\(base, landed\.at, totalRef\.current\);\s*(\/\/[^\n]*\n\s*)+snapped = caughtAfterClamp\(next\.at, landed\.snapped\) \?\? caughtAfterClamp\(clipEnd\(next\), landed\.snapped\);/);
    expect(move).toMatch(/next = trimClip\(base, drag\.zone === "in" \? "in" : "out", landed\.t, fileSecondsOf\(base\)\);\s*\/\/[^\n]*\n\s*\/\/[^\n]*\n\s*snapped = caughtAfterClamp\(drag\.zone === "in" \? next\.at : clipEnd\(next\), landed\.snapped\);/);
    expect(move).toMatch(/drag\.snapped = caughtAfterClamp\(drag\.snapEnd === "start" \? sel\.start : sel\.end, landed\.snapped\);/);
    expect(move).toMatch(/delta = Math\.min\(delta, totalRef\.current - Math\.max\(\.\.\.pins\)\);\s*\/\/[^\n]*\n\s*\/\/[^\n]*\n\s*snapped = caughtAfterClamp\(grabbed\.pinned_start \+ delta, snapped\);/);
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
    // not invalidate. The banner then still claimed the clips were missing and
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

  it("takes a MISSING clip alone too, since E4c lets the server keep the others", () => {
    // The spec's own ordinary case: the same bed laid twice (§12.2 - there is
    // no looping), its file gone. Under E4b removing one half took the other
    // with it, because the server refused any list still naming the file;
    // it now keeps a stored clip whose file has gone, so one half goes and
    // the other stays, still missing, still drawn hatched.
    const clips = [gone("m1", 0), live("m2", 5), gone("m3", 10)];
    expect(clipsAfterDelete(clips, "m1").map((held) => held.id)).toEqual(["m2", "m3"]);
    expect(clipsAfterDelete(clips, "m3").map((held) => held.id)).toEqual(["m1", "m2"]);
    expect(clipsAfterDelete([gone("m1", 0), gone("m2", 5)], "m2").map((held) => held.id)).toEqual(["m1"]);
  });

  it("leaves the missing clips exactly where they are when a LIVE clip goes", () => {
    // The server accepts that list now (the missing clip is stored, unchanged).
    const clips = [gone("m1", 0), live("m2", 5)];
    expect(clipsAfterDelete(clips, "m2")).toEqual([clips[0]]);
  });

  it("changes nothing for an id the list does not hold", () => {
    // A stale selection must not clear the lane.
    const clips = [live("m1", 0), gone("m2", 5)];
    expect(clipsAfterDelete(clips, "nope")).toBe(clips);
    expect(clipsAfterDelete([], "m1")).toEqual([]);
  });

  it("never re-orders or rewrites the clips it keeps", () => {
    const clips = [live("m3", 9), live("m1", 0), live("m2", 5)];
    expect(clipsAfterDelete(clips, "m1")).toEqual([clips[0], clips[2]]);
    // The same objects, not copies: a delete is not an edit of what remains.
    expect(clipsAfterDelete(clips, "m1")[0]).toBe(clips[0]);
  });
});

describe("what the banner's button leaves (withoutMissing)", () => {
  const live = (id: string, at: number) => clip({ id, at, file: "bed.mp3", missing: false });
  const gone = (id: string, at: number) => clip({ id, at, file: "gone.mp3", missing: true, file_duration: null });

  it("takes EVERY missing clip, in one list, and nothing else", () => {
    const clips = [gone("m1", 0), live("m2", 5), gone("m3", 10)];
    expect(withoutMissing(clips)).toEqual([clips[1]]);
    expect(withoutMissing(clips)[0]).toBe(clips[1]);
    // Every clip missing clears the lane.
    expect(withoutMissing([gone("m1", 0), gone("m2", 5)])).toEqual([]);
  });

  it("gives back the SAME array when nothing is missing, so the handler commits nothing", () => {
    // The file came back and the plan refetched (trap 37): the button must
    // do nothing rather than remove live clips, and `kept === clips` is how
    // the handler tells.
    const clips = [live("m1", 0), live("m2", 5)];
    expect(withoutMissing(clips)).toBe(clips);
    const none: MusicClip[] = [];
    expect(withoutMissing(none)).toBe(none);
  });

  it("never re-orders the clips it keeps", () => {
    const clips = [live("m3", 9), gone("g", 4), live("m1", 0), live("m2", 5)];
    expect(withoutMissing(clips)).toEqual([clips[0], clips[2], clips[3]]);
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

describe("what a restore leaves on the lane (musicAfterInsert, the mirror of the ripple - E5a)", () => {
  // The same rule as the cut's: the clips move only when the PICTURE's own
  // list changed, which is when Video is unlocked, and the lane obeys its own
  // lock like a track. Three clips: one before the moment, one straddling it,
  // one at or after it.
  const before = [
    clip({ id: "m1", at: 0, in: 0, out: 2 }),   // 0–2, before
    clip({ id: "m2", at: 7, in: 0, out: 4 }),   // 7–11, straddles 8
    clip({ id: "m3", at: 8, in: 0, out: 3 }),   // 8–11, exactly at 8
    clip({ id: "m4", at: 20, in: 0, out: 1 }),  // 20–21, after
  ];
  const locks = (video: boolean, narration: boolean, music: boolean) => ({ video, narration, music });
  const CASES: [boolean, boolean, boolean, boolean][] = [
    // video, narration, music, does the restore move the clips?
    [false, false, false, true],
    [false, true, false, true],
    [false, false, true, false],
    [false, true, true, false],
    [true, false, false, false],
    [true, true, false, false],
    [true, false, true, false],
    [true, true, true, false],
  ];

  it.each(CASES)("video=%s narration=%s music=%s -> moves=%s", (video, narration, music, moves) => {
    const after = musicAfterInsert(before, locks(video, narration, music), 8, 2);
    if (moves) expect(after.map((held) => held.at)).toEqual([0, 7, 10, 22]);
    else expect(after).toBe(before);
  });

  it("moves every clip at or after the moment later by the restored length, and nothing before it", () => {
    const after = musicAfterInsert(before, locks(false, false, false), 8, 2);
    expect(after.map((held) => held.at)).toEqual([0, 7, 10, 22]);
    // A moved clip keeps its slice, its level and its fades: only `at` changes.
    expect(after[2]).toEqual({ ...before[2], at: 10 });
    expect(after[3]).toEqual({ ...before[3], at: 22 });
  });

  it("neither splits nor moves a clip that straddles the moment", () => {
    const after = musicAfterInsert(before, locks(false, false, false), 8, 2);
    expect(after).toHaveLength(before.length);
    expect(after[1]).toBe(before[1]);
    expect(clipLength(after[1])).toBe(4);
  });

  it("hands back the same objects where nothing changed, and the same array when nothing moves", () => {
    const after = musicAfterInsert(before, locks(false, false, false), 8, 2);
    expect(after[0]).toBe(before[0]);
    expect(after[1]).toBe(before[1]);
    expect(after[2]).not.toBe(before[2]);
    // Nothing at or after 30: the same array, so trap 37's `===` reads "unchanged".
    expect(musicAfterInsert(before, locks(false, false, false), 30, 2)).toBe(before);
    expect(musicAfterInsert([], locks(false, false, false), 0, 2)).toEqual([]);
    // Nothing restored is nothing moved.
    expect(musicAfterInsert(before, locks(false, false, false), 8, 0)).toBe(before);
    expect(musicAfterInsert(before, locks(false, false, false), 8, -1)).toBe(before);
  });

  it("rounds the moment and the length once, at the top, so a clip lands where the picture's join does", () => {
    // A Ctrl+drag trim carries three decimals: the picture restores round3(d) at round3(t).
    const [, , exact, later] = musicAfterInsert(before, locks(false, false, false), 7.9996, 3.3284);
    expect(exact.at).toBe(11.328);
    expect(later.at).toBe(23.328);
    // Half a millisecond before the moment counts as at it.
    const [edge] = musicAfterInsert([clip({ id: "e", at: 7.9996, in: 0, out: 1 })], locks(false, false, false), 8, 1);
    expect(edge.at).toBe(9);
  });
});

describe("what a trim leaves on the lane (musicAfterTrim)", () => {
  const clips = [clip({ id: "m1", at: 0, in: 0, out: 2 }), clip({ id: "m2", at: 8, in: 0, out: 3 })];
  const open = { video: false, narration: false, music: false };
  const musicLocked = { video: false, narration: false, music: true };

  it("is the cut's ripple for a shortening, the restore's for a restore, and nothing for nothing", () => {
    expect(musicAfterTrim(clips, open, { kind: "cut", a: 3, b: 5 })).toEqual(musicAfterCut(clips, open, 3, 5));
    expect(musicAfterTrim(clips, open, { kind: "cut", a: 3, b: 5 }).map((held) => held.at)).toEqual([0, 6]);
    expect(musicAfterTrim(clips, open, { kind: "insert", t: 3, d: 2 })).toEqual(musicAfterInsert(clips, open, 3, 2));
    expect(musicAfterTrim(clips, open, { kind: "insert", t: 3, d: 2 }).map((held) => held.at)).toEqual([0, 10]);
    expect(musicAfterTrim(clips, open, null)).toBe(clips);
  });

  it("passes the list straight through under the locks the two rules read", () => {
    expect(musicAfterTrim(clips, musicLocked, { kind: "cut", a: 3, b: 5 })).toBe(clips);
    expect(musicAfterTrim(clips, musicLocked, { kind: "insert", t: 3, d: 2 })).toBe(clips);
    expect(musicAfterTrim(clips, { ...open, video: true }, { kind: "insert", t: 3, d: 2 })).toBe(clips);
  });
});

describe("the missing clips a cut would slice (missingAcross)", () => {
  // A missing clip on the output at [5, 9): the server keeps it only with
  // the slice it has (E4c), so a cut or a split that changes that slice is
  // refused on the strip before anything is sent (the Reviewer's M1).
  const gone = clip({ id: "g", at: 5, in: 0, out: 4, file: "gone.mp3", missing: true, file_duration: null });
  const live = clip({ id: "l", at: 5, in: 0, out: 4 });

  it("names a missing clip the cut begins inside, ends inside, or spans", () => {
    expect(missingAcross([gone], 7, 12)).toEqual([gone]);   // straddles the start of the cut
    expect(missingAcross([gone], 2, 7)).toEqual([gone]);    // straddles its end
    expect(missingAcross([gone], 6, 8)).toEqual([gone]);    // straddles both: two pieces, one of them a new id
    expect(missingAcross([gone], 8, 6)).toEqual([gone]);    // whichever way round the bounds are given
  });

  it("names nothing that only ripples, goes whole, or is merely touched", () => {
    expect(missingAcross([gone], 10, 12)).toEqual([]);      // wholly before the cut: only ripples
    expect(missingAcross([gone], 0, 3)).toEqual([]);        // wholly after it: unchanged
    expect(missingAcross([gone], 4, 10)).toEqual([]);       // wholly inside it: goes whole, which the server allows
    expect(missingAcross([gone], 9, 12)).toEqual([]);       // ends exactly where the cut starts
    expect(missingAcross([gone], 2, 5)).toEqual([]);        // starts exactly where the cut ends
    expect(missingAcross([], 6, 8)).toEqual([]);
  });

  it("never names a LIVE clip, however the cut falls on it", () => {
    expect(missingAcross([live], 6, 8)).toEqual([]);
    expect(missingAcross([live, gone], 6, 8)).toEqual([gone]);
    expect(missingAcross([live, gone], 7, 7)).toEqual([gone]);
  });

  it("treats a split (a === b) as a cut of nothing: named over the clip, not beside it", () => {
    expect(missingAcross([gone], 7, 7)).toEqual([gone]);
    expect(missingAcross([gone], 10, 10)).toEqual([]);
    expect(missingAcross([gone], 5, 5)).toEqual([]);        // exactly on its start: the whole clip is the tail, unchanged
    expect(missingAcross([gone], 9, 9)).toEqual([]);        // exactly on its end: the whole clip is the head, unchanged
  });

  it("agrees with cutMusic about a remnant too short to keep: dropped whole, so not named", () => {
    // cutMusic drops a piece shorter than MIN_CLIP_SECONDS; a cut that leaves
    // only such a remnant of the missing clip removes the clip, which the
    // server accepts, so naming it would refuse what the server would take.
    expect(missingAcross([gone], 5.05, 12)).toEqual([]);
    expect(cutMusic([gone], 5.05, 12)).toEqual([]);
    // ... but a split there keeps the tail with `in` advanced: sliced, named.
    expect(missingAcross([gone], 5.05, 5.05)).toEqual([gone]);
    expect(cutMusic([gone], 5.05, 5.05, counterMint()).map((held) => held.in)).toEqual([0.05]);
  });

  it("keeps the clips' own order and names each straddled missing clip", () => {
    const g2 = { ...gone, id: "g2", at: 20 };
    expect(missingAcross([g2, live, gone], 6, 22)).toEqual([g2, gone]);
  });

  it("is exactly `cutMusic` changing a missing clip's slice or minting it a second id, over random cuts", () => {
    // The helper is a prediction of the slicer; the slicer is the truth. For
    // each missing clip, sliced means a piece with a different `in`/`out`
    // than the clip's own, or more than one piece.
    const random = mulberry32(2026_09_24);
    for (let round = 0; round < 3000; round++) {
      const clips: MusicClip[] = [];
      const count = 1 + Math.floor(random() * 4);
      for (let i = 0; i < count; i++) {
        const length = round3(0.05 + random() * 6);
        const start = round3(random() * 3);
        clips.push(clip({
          id: `c${i}`, at: round3(random() * 12), in: start, out: round3(start + length),
          file: random() < 0.5 ? "gone.mp3" : "bed.mp3", missing: random() < 0.6, file_duration: null,
        }));
      }
      const a = round3(random() * 16);
      const b = random() < 0.25 ? a : round3(random() * 16);
      const predicted = missingAcross(clips, a, b).map((held) => held.id);
      const sliced = clips.filter((held) => {
        if (!held.missing) return false;
        const pieces = cutMusic([held], a, b, counterMint());
        return pieces.length > 1 || pieces.some((piece) => piece.in !== held.in || piece.out !== held.out);
      }).map((held) => held.id);
      expect(predicted, `clips ${JSON.stringify(clips)} cut ${a}-${b}`).toEqual(sliced);
    }
  });
});

describe("what the strip says before such a cut (missingAcrossRefusal)", () => {
  const gone = clip({ id: "g", at: 5, in: 0, out: 4, file: "sting.wav", missing: true, file_duration: null });

  it("names the split, where, the file, why, and both ways out - the one that keeps the clip first", () => {
    expect(missingAcrossRefusal("split", [gone], 2)).toBe(
      "That split at 0:02.000 would cut into sting.wav, but its file is no longer in the library, so its slice cannot"
      + " change — lock the Music lane and split the picture alone, or remove the clip first.",
    );
  });

  it("names the cut's range in order, whichever way round it was dragged", () => {
    const said = "That cut (0:02.000 – 0:03.500) would cut into sting.wav, but its file is no longer in the library,"
      + " so its slice cannot change — lock the Music lane and cut the picture alone, or remove the clip first.";
    expect(missingAcrossRefusal("cut", [gone], 2, 3.5)).toBe(said);
    expect(missingAcrossRefusal("cut", [gone], 3.5, 2)).toBe(said);
  });

  it("names a TRIM as a trim (E5a): the same slicing, the same ways out, in the gesture's own word", () => {
    expect(missingAcrossRefusal("trim", [gone], 2, 3.5)).toBe(
      "That trim (0:02.000 – 0:03.500) would cut into sting.wav, but its file is no longer in the library,"
      + " so its slice cannot change — lock the Music lane and trim the picture alone, or remove the clip first.",
    );
  });

  it("names each file once, in the plural, and counts the clips", () => {
    const bed = { ...gone, id: "b", file: "bed.mp3" };
    expect(missingAcrossRefusal("cut", [gone, bed], 2, 3)).toContain(
      "would cut into sting.wav and bed.mp3, but their files are no longer in the library, so their slices cannot change",
    );
    expect(missingAcrossRefusal("cut", [gone, bed], 2, 3)).toContain("or remove the clips first.");
    // The same bed laid twice: one file, two clips.
    const again = { ...gone, id: "g2", at: 20 };
    expect(missingAcrossRefusal("split", [gone, again], 7)).toContain("would cut into sting.wav, but its file is");
    // One file, but two slices: the count of clips drives "their slices" as it
    // drives "the clips" (the re-review's nit - one file was giving two clips one slice).
    expect(missingAcrossRefusal("split", [gone, again], 7)).toContain("so their slices cannot change");
    expect(missingAcrossRefusal("split", [gone, again], 7)).toContain("or remove the clips first.");
    // Three files, listed as a sentence would list them.
    const third = { ...gone, id: "t", file: "third.mp3" };
    expect(missingAcrossRefusal("cut", [gone, bed, third], 2, 3)).toContain("sting.wav, bed.mp3 and third.mp3");
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

  it("adds nothing of its own to the library's refusal: the lane is not frozen since E4c", () => {
    // Under E4b a refusal about the library gained a paragraph saying the
    // lane was frozen and pointing at the banner's button. The server now
    // keeps a stored clip whose file has gone, and its own sentences say
    // what may not be done and what to do - so the lead, the sentence, and
    // nothing between them, for every refusal alike.
    for (const detail of [
      said,
      "music clip 1 (a): file 'sting.wav' is not in the library, so its slice cannot change; it was 0.000–4.500"
        + " of the file. Move it, level it, fade it, remove it, or put the file back under the same name.",
      "A job holds the project.",
      "Keep at least one range.",
      "music clip 1 (bed): fade_in + fade_out is longer than the clip.",
      "Something the client has never seen: constraint 7 failed.",
    ]) {
      const line = editRefusal(detail, "edit");
      expect(line).toBe(`That edit was not saved — the strip still shows the cut and the clips the server holds. The server said: ${detail}`);
      expect(line).not.toMatch(/Music lane|the button above|frozen|cannot be changed until/);
    }
  });

  it("names the TIMING for an offsets refusal, and never blames the music for it", () => {
    const line = editRefusal("A job holds the project.", "timing");
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
