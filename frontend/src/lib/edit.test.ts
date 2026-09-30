import { describe, it, expect } from "vitest";
// The SAME cases services/edit.py is pinned by (tests/test_edit.py reads this
// file too), so the two copies of the projection cannot drift silently.
import fixture from "../../../tests/fixtures/edit_projection.json";
import {
  EPSILON,
  FRAME_SECONDS,
  MAX_MARKERS,
  MAX_MARKER_NAME,
  MAX_OFFSET,
  MAX_PPS,
  anchoredScrollLeft,
  atFrameFloor,
  caughtAfterClamp,
  clickSelectsPiece,
  describeJoin,
  dragOffsets,
  drawnMarkers,
  editBody,
  joins,
  markerName,
  markerNeighbours,
  markersAfterDelete,
  markersBody,
  maxZoom,
  mintMarkerId,
  moveMarker,
  newMarker,
  nextEditForCut,
  nextEditForSplit,
  nextEditForTrim,
  outputDuration,
  pieceAt,
  pieceEdgeAt,
  pieces,
  positionAfterEdit,
  projectPeaks,
  releaseSuppressesClick,
  removeRange,
  renameMarker,
  renderEstimate,
  renderSummary,
  restoreRange,
  round3,
  rulerLabel,
  sameEdit,
  sameMarkers,
  sanitizeMarkerName,
  sliderFromZoom,
  snap,
  snapTargets,
  sortMarkers,
  splitAt,
  stepFrame,
  stepZoom,
  tickStep,
  ticks,
  toSource,
  toTimeline,
  trackBody,
  trackList,
  trimChange,
  trimLabel,
  trimPiece,
  unmovedRelease,
  wholeKeep,
  wholeSource,
  zoomFromSlider,
  zoomToSelection,
  type DrawnMarker,
  type Keep,
  type Marker,
  type MusicClip,
  type TrackEdit,
  type TrimEdge,
} from "./edit";

interface TrackCase {
  video: Keep | null;
  narration: Keep | null;
  sentences: [number, number, number][];
  duration: number;
  narration_duration: number;
  cut: boolean;
  projected: boolean;
}
interface Fixture {
  source_duration: number;
  keep: Keep;
  output_duration: number;
  to_timeline: [number, number | null][];
  to_source: [number, number][];
  round_trip: number[];
  clamps: [Keep, number, number][];
  whole_source: [Keep, number, boolean][];
  output_durations: [Keep, number][];
  tracks: {
    segments: [number, number][];
    video: Keep;
    narration: Keep;
    cases: Record<"video_only" | "narration_only" | "both" | "together", TrackCase>;
  };
}
const cases = fixture as unknown as Fixture;
/** Five sentences over 12 s with 6.0–7.5 removed: the shared scenario. */
const KEEP: Keep = cases.keep;
const SOURCE = cases.source_duration;

describe("the projection — the server's copy, case for case", () => {
  it("is the shared fixture, not an emptied or truncated file", () => {
    expect(KEEP).toEqual([[0, 6], [7.5, 12]]);
    expect(cases.to_timeline.length).toBeGreaterThanOrEqual(10);
    expect(cases.to_source.length).toBeGreaterThanOrEqual(8);
    // The one rule E1 reversed - the LATER range wins at a join - must not be
    // the row a truncation happens to drop.
    expect(cases.to_source).toContainEqual([6, 7.5]);
    expect(cases.to_timeline).toContainEqual([7.5, 6]);
  });

  it("output is the sum of the kept ranges, rounded so float noise never leaks", () => {
    expect(outputDuration(KEEP)).toBe(cases.output_duration);
    for (const [keep, expected] of cases.output_durations) expect(outputDuration(keep)).toBe(expected);
  });

  it("toTimeline closes the holes and maps both sides of a cut to the join", () => {
    for (const [tSource, tTimeline] of cases.to_timeline) {
      expect(toTimeline(tSource, KEEP), `source ${tSource}`).toBe(tTimeline);
    }
  });

  it("toSource is the inverse, the later range wins at a join, and the ends clamp", () => {
    for (const [tTimeline, tSource] of cases.to_source) {
      expect(toSource(tTimeline, KEEP), `timeline ${tTimeline}`).toBe(tSource);
    }
    for (const t of cases.round_trip) expect(toTimeline(toSource(t, KEEP), KEEP), `round trip ${t}`).toBe(t);
    for (const [keep, tTimeline, tSource] of cases.clamps) expect(toSource(tTimeline, keep)).toBe(tSource);
    // Neither copy has a source to show for an empty list; the client answers 0 rather than throwing.
    expect(toSource(3, [])).toBe(0);
  });

  it("wholeSource is true only when nothing is removed", () => {
    for (const [keep, sourceDuration, expected] of cases.whole_source) {
      expect(wholeSource(keep, sourceDuration), JSON.stringify([keep, sourceDuration])).toBe(expected);
    }
  });

  it("rounds to the stored precision and never to a negative zero", () => {
    expect(round3(7.5 + (6.001 - 6))).toBe(7.501);
    expect(Object.is(round3(-0.0004), 0)).toBe(true);
    expect(round3(12.0004)).toBe(12);
  });

  it("wholeKeep is one range, the whole source, at the stored precision", () => {
    expect(wholeKeep(SOURCE)).toEqual([[0, 12]]);
    expect(wholeKeep(341.00825)).toEqual([[0, 341.008]]);
    expect(wholeKeep(-1)).toEqual([[0, 0]]);
  });
});

describe("joins — every place the output has a cut behind it", () => {
  it("sits at the closed cut, in timeline seconds, and says what was removed", () => {
    expect(joins(KEEP, SOURCE)).toEqual([{ at: 6, removed: 1.5, from: 6, to: 7.5 }]);
  });

  it("includes a removed head and a removed tail", () => {
    expect(joins([[2, 6], [7.5, 11]], SOURCE)).toEqual([
      { at: 0, removed: 2, from: 0, to: 2 },
      { at: 4, removed: 1.5, from: 6, to: 7.5 },
      { at: 7.5, removed: 1, from: 11, to: 12 },
    ]);
  });

  it("marks a bare split as a join with nothing removed", () => {
    expect(joins([[0, 6], [6, 12]], SOURCE)).toEqual([{ at: 6, removed: 0, from: 6, to: 6 }]);
  });

  it("has none for a whole source, and none for nothing", () => {
    expect(joins([[0, 12]], SOURCE)).toEqual([]);
    expect(joins([[0, 12]], 12.0004)).toEqual([]);  // the source's length rounds like the ranges
    expect(joins([], SOURCE)).toEqual([]);
  });

  it("describes itself for the tooltip", () => {
    expect(describeJoin({ at: 6, removed: 1.5, from: 6, to: 7.5 })).toBe("1.5 s removed (0:06.000 – 0:07.500 of the source)");
    expect(describeJoin({ at: 47.3, removed: 5, from: 47.3, to: 52.3 })).toBe("5.0 s removed (0:47.300 – 0:52.300 of the source)");
    expect(describeJoin({ at: 1, removed: 0.04, from: 1, to: 1.04 })).toBe("0.040 s removed (0:01.000 – 0:01.040 of the source)");
    expect(describeJoin({ at: 6, removed: 0, from: 6, to: 6 })).toBe("Split — nothing removed.");
  });
});

describe("removeRange — the ripple delete", () => {
  it("cuts a piece out of one range and closes the gap", () => {
    // Timeline 2–3 of the whole source is source 2–3.
    expect(removeRange([[0, 12]], 2, 3)).toEqual([[0, 2], [3, 12]]);
    // Timeline 7–8 under KEEP is source 8.5–9.5.
    expect(removeRange(KEEP, 7, 8)).toEqual([[0, 6], [7.5, 8.5], [9.5, 12]]);
  });

  it("takes from both neighbours when the interval spans an existing join", () => {
    expect(removeRange(KEEP, 5, 7)).toEqual([[0, 5], [8.5, 12]]);
  });

  it("removes a head, a tail, and whole ranges", () => {
    expect(removeRange([[0, 12]], 0, 2)).toEqual([[2, 12]]);
    expect(removeRange([[0, 12]], 10, 12)).toEqual([[0, 10]]);
    // Timeline 3.5–7.5 is the last half-second of the first range, all of the
    // second (timeline 4–7) and the first half-second of the third.
    expect(removeRange([[0, 4], [5, 8], [9, 12]], 3.5, 7.5)).toEqual([[0, 3.5], [9.5, 12]]);
  });

  it("refuses to leave nothing", () => {
    expect(removeRange(KEEP, 0, 10.5)).toBeNull();
    expect(removeRange([[0, 12]], -1, 99)).toBeNull();
  });

  it("leaves the list alone when the interval touches nothing", () => {
    expect(removeRange(KEEP, 20, 30)).toEqual(KEEP);
    // Exactly at the join: the intersection is a point at a range's edge, and no piece is empty-but-kept.
    expect(removeRange(KEEP, 6, 6)).toEqual(KEEP);
  });

  it("does not care which way round the interval is given", () => {
    expect(removeRange(KEEP, 8, 7)).toEqual(removeRange(KEEP, 7, 8));
  });

  it("splits at a zero-length interval strictly inside a range", () => {
    expect(removeRange(KEEP, 3, 3)).toEqual([[0, 3], [3, 6], [7.5, 12]]);
  });

  it("rounds to the stored precision and drops what rounds away", () => {
    expect(removeRange([[0, 12]], 1.00049, 2.00051)).toEqual([[0, 1], [2.001, 12]]);
    // A sliver thinner than a millisecond cannot survive as [x, x].
    expect(removeRange([[0, 6]], 0.0002, 6)).toBeNull();
    expect(removeRange([[0, 6]], 0.0004, 5.9996)).toBeNull();
  });
});

describe("positionAfterEdit — where the playhead goes after a commit", () => {
  const WHOLE: Keep = [[0, 12]];

  it("keeps the same source moment when it is still there", () => {
    expect(positionAfterEdit(2, WHOLE, KEEP)).toBe(2);
    expect(positionAfterEdit(8, WHOLE, KEEP)).toBe(6.5);   // source 8 moved left by the 1.5 s hole
    expect(positionAfterEdit(6.5, KEEP, WHOLE)).toBe(8);   // and back, after an undo
  });

  it("lands on the join when the moment was just removed", () => {
    expect(positionAfterEdit(7, WHOLE, KEEP)).toBe(6);
    expect(positionAfterEdit(6.001, WHOLE, KEEP)).toBe(6);
  });

  it("goes to 0 before the first kept range, and to the end after the last", () => {
    expect(positionAfterEdit(1, WHOLE, [[3, 12]])).toBe(0);
    expect(positionAfterEdit(8, WHOLE, [[0, 5]])).toBe(5);
    expect(positionAfterEdit(8, WHOLE, [])).toBe(0);
  });
});

describe("projectPeaks — the waveform in timeline seconds", () => {
  const BUCKET = 0.125;
  // Each bucket carries its own index, so a hole and an alignment are checkable.
  const indexed = (seconds: number) => Array.from({ length: Math.ceil(seconds / BUCKET) }, (_, i) => i);
  // 12 s at 8 buckets a second.
  const peaks = indexed(12);
  /** The source buckets lying ENTIRELY inside a removed range. */
  const holeBuckets = (keep: Keep, sourceDuration: number): Set<number> => {
    const holes: [number, number][] = [];
    let previous = 0;
    for (const [start, end] of keep) { holes.push([previous, start]); previous = end; }
    holes.push([previous, sourceDuration]);
    const inside = new Set<number>();
    for (const [from, to] of holes) {
      for (let i = Math.ceil(from / BUCKET); (i + 1) * BUCKET <= to + 1e-9; i++) inside.add(i);
    }
    return inside;
  };
  // Three cuts on the corpus source: the Reviewer's case, whose per-range
  // rounding came out one bucket short and drifted the bursts by 38 ms.
  const CORPUS: Keep = [[0, 47.312], [52.3, 120.062], [125.062, 200.437], [205, 341.008]];
  const MULTI: [Keep, number][] = [
    [KEEP, 12],
    [CORPUS, 341.008],
    [[[1.0625, 3.9375], [5.0625, 9]], 12],
    [[[0.3, 2.2], [2.9, 5.1], [5.4, 8.8], [9.3, 12]], 12],
    [[[0.06, 0.19], [0.31, 0.44], [0.56, 0.69], [0.81, 0.94], [1.06, 1.19]], 2],
  ];

  it("concatenates the kept ranges' buckets", () => {
    const projected = projectPeaks(peaks, BUCKET, KEEP);
    expect(projected).toHaveLength(84);  // round(10.5 / 0.125)
    expect(projected.slice(0, 48)).toEqual(peaks.slice(0, 48));
    expect(projected.slice(48)).toEqual(peaks.slice(60));
  });

  it("is exactly round(outputDuration / bucket) long, however many ranges", () => {
    for (const [keep, sourceDuration] of MULTI) {
      const projected = projectPeaks(indexed(sourceDuration), BUCKET, keep);
      expect(projected, JSON.stringify(keep)).toHaveLength(Math.round(outputDuration(keep) / BUCKET));
    }
    expect(projectPeaks(indexed(341.008), BUCKET, CORPUS)).toHaveLength(2612);
  });

  it("never shows a bucket from inside a hole", () => {
    for (const [keep, sourceDuration] of MULTI) {
      const inside = holeBuckets(keep, sourceDuration);
      for (const bucket of projectPeaks(indexed(sourceDuration), BUCKET, keep)) {
        expect(inside.has(bucket), `bucket ${bucket} of ${JSON.stringify(keep)}`).toBe(false);
      }
    }
  });

  it("keeps a moment under its sentence after several cuts — the error never accumulates", () => {
    // Source 210 s sits in the fourth range at timeline 195.449 s; its bucket
    // must be drawn within one bucket of that, not three cuts' worth away.
    const projected = projectPeaks(indexed(341.008), BUCKET, CORPUS);
    const drawnAt = projected.indexOf(Math.round(210 / BUCKET));
    expect(drawnAt).toBeGreaterThan(0);
    expect(Math.abs(drawnAt - toTimeline(210, CORPUS)! / BUCKET)).toBeLessThan(1);
  });

  it("is the whole array for a whole source, and nothing for nothing", () => {
    expect(projectPeaks(peaks, BUCKET, [[0, 12]])).toEqual(peaks);
    expect(projectPeaks([], BUCKET, KEEP)).toEqual([]);
    expect(projectPeaks(peaks, 0, KEEP)).toEqual([]);
  });

  it("repeats the last bucket for a range that runs past the array rather than reading undefined", () => {
    expect(projectPeaks(peaks, BUCKET, [[11, 13]])).toEqual([...peaks.slice(88), 95, 95, 95, 95, 95, 95, 95, 95]);
  });
});

describe("the ruler", () => {
  it("picks the smallest step whose labels are at least 80px apart", () => {
    expect(tickStep(8)).toBe(10);       // 5 s would be 40px
    expect(tickStep(200)).toBe(0.5);    // at full zoom, half a second is 100px
    expect(tickStep(1)).toBe(120);
    expect(tickStep(16, 80)).toBe(5);
    expect(tickStep(0.0222)).toBe(7200);  // a two-hour source at phone width: 3600 s would be 79.9px
    expect(tickStep(0.01)).toBe(7200);    // nothing fits: the coarsest there is
  });

  it("labels whole seconds as m:ss, and sub-second steps with the milliseconds", () => {
    expect(rulerLabel(65, 5)).toBe("1:05");
    expect(rulerLabel(0, 1)).toBe("0:00");
    expect(rulerLabel(3600, 600)).toBe("60:00");
    expect(rulerLabel(90, 0.5)).toBe("1:30.000");
    expect(rulerLabel(0.2, 0.2)).toBe("0:00.200");
    expect(rulerLabel(9.9999999, 1)).toBe("0:10");
  });

  it("draws labelled majors with minors between", () => {
    const marks = ticks(30, 8);
    expect(marks.map((m) => m.at)).toEqual([0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30]);
    expect(marks.filter((m) => m.major).map((m) => m.label)).toEqual(["0:00", "0:10", "0:20", "0:30"]);
    expect(marks.filter((m) => !m.major).every((m) => m.label === "")).toBe(true);
    // At the zoom ceiling the step is half a second, and the milliseconds are the point.
    expect(ticks(1, 200).filter((m) => m.major).map((m) => m.label)).toEqual(["0:00.000", "0:00.500", "0:01.000"]);
  });

  it("puts minors every 0.2 s under a step of 1 s, and every second under 5 s", () => {
    expect(ticks(1, 100).map((m) => m.at)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);  // step 1: 100px apart
    expect(ticks(5, 16).map((m) => m.at)).toEqual([0, 1, 2, 3, 4, 5]);          // step 5: 80px apart
  });

  it("draws only the window it is asked for", () => {
    const marks = ticks(100, 8, 40, 60);
    expect(marks[0].at).toBe(40);
    expect(marks[marks.length - 1].at).toBe(60);
    expect(marks).toHaveLength(11);
    expect(ticks(100, 8, -50, 4).map((m) => m.at)).toEqual([0, 2, 4]);
    expect(ticks(100, 8, 98, 500).map((m) => m.at)).toEqual([98, 100]);
  });

  it("is empty with nothing to draw", () => {
    expect(ticks(0, 8)).toEqual([]);
    expect(ticks(30, 0)).toEqual([]);
  });
});

describe("zoom", () => {
  it("tops out where one second is MAX_PPS pixels, and never below fit", () => {
    expect(MAX_PPS).toBe(200);
    expect(maxZoom(100, 800)).toBe(25);
    expect(maxZoom(2, 800)).toBe(1);
    expect(maxZoom(0, 800)).toBe(1);
    expect(maxZoom(100, 0)).toBe(1);
  });

  it("maps the slider logarithmically, both ways", () => {
    expect(zoomFromSlider(0, 25)).toBe(1);
    expect(zoomFromSlider(1, 25)).toBe(25);
    expect(zoomFromSlider(0.5, 25)).toBeCloseTo(5);
    expect(sliderFromZoom(5, 25)).toBeCloseTo(0.5);
    expect(sliderFromZoom(25, 25)).toBe(1);
    expect(sliderFromZoom(1, 25)).toBe(0);
    // No room to zoom: the slider sits at 0 and asks for 1.
    expect(sliderFromZoom(1, 1)).toBe(0);
    expect(zoomFromSlider(0.7, 1)).toBe(1);
    // Out-of-range input is clamped, never NaN.
    expect(zoomFromSlider(2, 25)).toBe(25);
    expect(sliderFromZoom(0.5, 25)).toBe(0);
  });

  it("steps by a quarter, clamped to [1, max]", () => {
    expect(stepZoom(1, 25, 1)).toBe(1.25);
    expect(stepZoom(1, 25, -1)).toBe(1);
    expect(stepZoom(24, 25, 1)).toBe(25);
    expect(stepZoom(1.25, 25, -1)).toBe(1);
  });

  it("zooms to a selection so it fills about nine tenths of the strip", () => {
    expect(zoomToSelection({ start: 10, end: 20 }, 100, 800)).toBeCloseTo(9);
    expect(zoomToSelection({ start: 20, end: 10 }, 100, 800)).toBeCloseTo(9);
    expect(zoomToSelection({ start: 0, end: 100 }, 100, 800)).toBe(1);        // never below fit
    expect(zoomToSelection({ start: 10, end: 10.1 }, 100, 800)).toBe(25);     // never past the ceiling
    expect(zoomToSelection({ start: 5, end: 5 }, 100, 800)).toBe(1);
  });

  it("keeps the anchor at the same screen x through a zoom change", () => {
    // The playhead at 10 s, drawn 100px in; at 40 px/s it is at 400px, so scroll 300.
    expect(anchoredScrollLeft(10, 100, 40)).toBe(300);
    expect(anchoredScrollLeft(10, 100, 8)).toBe(0);  // would be -20: nothing to scroll to
  });
});

describe("stepFrame", () => {
  it("moves a thirtieth of a second, inside the audition", () => {
    expect(FRAME_SECONDS).toBeCloseTo(1 / 30);
    expect(stepFrame(1, 1, 100)).toBeCloseTo(1 + 1 / 30);
    expect(stepFrame(1, -1, 100)).toBeCloseTo(1 - 1 / 30);
    expect(stepFrame(0, -1, 100)).toBe(0);
    expect(stepFrame(99.99, 1, 100)).toBe(100);
  });
});

describe("the Render button", () => {
  const CORPUS: Keep = [[0, 47.3], [52.3, 341.008]];

  it("estimates from the decode reach, never the output length, rounded up to five", () => {
    // 341 s of reach measured 32.1 s and 34.1 s at x264 medium on the bundled
    // ffmpeg (Q1; 16.1 s at ultrafast); 5 + 341 × 0.1 = 39.1, up to 40.
    expect(renderEstimate(CORPUS, 341.008)).toBe(40);
    // A short keep at the head decodes only that far, and one at the tail
    // only from its start (E1's input seek moves the origin)...
    expect(renderEstimate([[0, 10]], 341.008)).toBe(10);
    expect(renderEstimate([[331, 341.008]], 341.008)).toBe(10);
    // ... while a head plus a tail decodes everything between them.
    expect(renderEstimate([[0, 5], [336, 341.008]], 341.008)).toBe(40);
  });

  it("estimates 0 when nothing is cut — the picture step is skipped", () => {
    expect(renderEstimate([[0, 12]], 12)).toBe(0);
    expect(renderEstimate([[0, 6], [6, 12]], 12)).toBe(0);
    expect(renderEstimate([], 12)).toBe(0);
  });

  it("says what the render will do, per track, in one sentence", () => {
    // Cut together (the two lists equal): E2's line, word for word.
    expect(renderSummary(CORPUS, CORPUS, 341.008)).toBe(
      "Cuts 1 range (5.0 s removed) and re-voices — about 40 s, plus any sentences the audition has not fetched yet.",
    );
    expect(renderSummary([[2, 6], [7.5, 11]], [[2, 6], [7.5, 11]], 12)).toBe(
      "Cuts 3 ranges (4.5 s removed) and re-voices — about 10 s, plus any sentences the audition has not fetched yet.",
    );
    // The picture alone (the narration locked, or whole).
    expect(renderSummary(CORPUS, null, 341.008)).toBe(
      "Cuts 1 range of the picture (5.0 s removed) and re-voices — about 40 s, plus any sentences the audition has not fetched yet.",
    );
    expect(renderSummary(CORPUS, [[0, 341.008]], 341.008)).toBe(
      "Cuts 1 range of the picture (5.0 s removed) and re-voices — about 40 s, plus any sentences the audition has not fetched yet.",
    );
    // Both, differently: the narration's TIMELINE is shortened; which sentences go is the plan's to say.
    expect(renderSummary(KEEP, [[0, 6.4], [7, 12]], 12)).toBe(
      "Cuts 1 range of the picture (1.5 s removed) and shortens the narration's timeline by 0.6 s "
      + "(sentences spoken in the removed stretch are left out) and re-voices — about 10 s, "
      + "plus any sentences the audition has not fetched yet.",
    );
    // The narration alone: no picture step, so no estimate to give.
    expect(renderSummary(null, [[0, 6.4], [7, 12]], 12)).toBe(
      "Shortens the narration's timeline by 0.6 s (sentences spoken in the removed stretch are left out) and re-voices — "
      + "the picture is not cut, so it takes only as long as the sentences the audition has not fetched yet.",
    );
    expect(renderSummary([[0, 12]], [[0, 6.4], [7, 12]], 12)).toBe(
      "Shortens the narration's timeline by 0.6 s (sentences spoken in the removed stretch are left out) and re-voices — "
      + "the picture is not cut, so it takes only as long as the sentences the audition has not fetched yet.",
    );
  });

  it("is nothing when neither track removes anything", () => {
    expect(renderSummary([[0, 6], [6, 12]], null, 12)).toBeNull();
    expect(renderSummary(null, [[0, 6], [6, 12]], 12)).toBeNull();
    expect(renderSummary([[0, 12]], [[0, 12]], 12)).toBeNull();
    expect(renderSummary(null, null, 12)).toBeNull();
    expect(renderSummary([], [], 12)).toBeNull();
  });
});

describe("splitAt — a boundary at the playhead", () => {
  it("splits a range in two touching ranges at the source moment under the playhead", () => {
    expect(splitAt([[0, 12]], 3)).toEqual([[0, 3], [3, 12]]);
    // Timeline 7 under KEEP is source 8.5.
    expect(splitAt(KEEP, 7)).toEqual([[0, 6], [7.5, 8.5], [8.5, 12]]);
    expect(splitAt([[0, 12]], 1.00049)).toEqual([[0, 1], [1, 12]]);
  });

  it("is a no-op on an existing boundary and at either end", () => {
    expect(splitAt(KEEP, 6)).toEqual(KEEP);
    expect(splitAt(KEEP, 0)).toEqual(KEEP);
    expect(splitAt(KEEP, 10.5)).toEqual(KEEP);
    expect(splitAt([[0, 6], [6, 12]], 6)).toEqual([[0, 6], [6, 12]]);
    // A hair from a boundary rounds onto it, and the sliver is not kept as [x, x].
    expect(splitAt(KEEP, 6.0003)).toEqual(KEEP);
    expect(splitAt(KEEP, 99)).toEqual(KEEP);
    expect(splitAt([], 3)).toEqual([]);
  });

  it("removes nothing: the output is the same length and the list is still whole", () => {
    const split = splitAt([[0, 12]], 4.2);
    expect(outputDuration(split)).toBe(12);
    expect(wholeSource(split, 12)).toBe(true);
    expect(joins(split, 12)).toEqual([{ at: 4.2, removed: 0, from: 4.2, to: 4.2 }]);
  });
});

describe("pieces — the stretches of a lane between boundaries", () => {
  it("is one piece per kept range, laid end to end on the timeline", () => {
    expect(pieces(KEEP, SOURCE)).toEqual([
      { start: 0, end: 6, sourceStart: 0, sourceEnd: 6 },
      { start: 6, end: 10.5, sourceStart: 7.5, sourceEnd: 12 },
    ]);
    expect(pieces([[0, 6], [6, 12]], SOURCE)).toEqual([
      { start: 0, end: 6, sourceStart: 0, sourceEnd: 6 },
      { start: 6, end: 12, sourceStart: 6, sourceEnd: 12 },
    ]);
  });

  it("is the whole source when there is no list", () => {
    expect(pieces([], SOURCE)).toEqual([{ start: 0, end: 12, sourceStart: 0, sourceEnd: 12 }]);
  });

  it("finds the piece under a moment, the later one at a boundary, and none outside", () => {
    const list = pieces(KEEP, SOURCE);
    expect(pieceAt(list, 2)).toBe(list[0]);
    expect(pieceAt(list, 6)).toBe(list[1]);
    expect(pieceAt(list, 10.5)).toBe(list[1]);
    expect(pieceAt(list, 11)).toBeNull();
    expect(pieceAt(list, -1)).toBeNull();
  });
});

describe("the edit per track — a cut or a split on the unlocked tracks", () => {
  const NONE: TrackEdit = { video: null, narration: null };
  const OPEN = { video: false, narration: false };
  const VIDEO_LOCKED = { video: true, narration: false };
  const NARRATION_LOCKED = { video: false, narration: true };
  const BOTH_LOCKED = { video: true, narration: true };
  // The corpus: 341.008 s, the 5 s cut the live check makes.
  const CORPUS = 341.008;
  const AFTER_CUT: Keep = [[0, 47.3], [52.3, 341.008]];

  it("derives a track's working list the same way on every path: stored, else the whole source", () => {
    expect(trackList(null, CORPUS)).toEqual([[0, 341.008]]);
    expect(trackList([], CORPUS)).toEqual([[0, 341.008]]);
    expect(trackList(KEEP, SOURCE)).toBe(KEEP);
  });

  it("puts a whole track into the body as null, and keeps a whole-source split", () => {
    expect(trackBody(null, SOURCE)).toBeNull();
    expect(trackBody([], SOURCE)).toBeNull();
    expect(trackBody([[0, 12]], SOURCE)).toBeNull();
    expect(trackBody([[0, 12]], 12.0004)).toBeNull();
    expect(trackBody([[0, 6], [6, 12]], SOURCE)).toEqual([[0, 6], [6, 12]]);
    expect(trackBody(KEEP, SOURCE)).toBe(KEEP);
  });

  it("cuts both tracks with nothing stored and both unlocked — E2's cut, unchanged", () => {
    expect(nextEditForCut(NONE, OPEN, 47.3, 52.3, CORPUS)).toEqual({ next: { video: AFTER_CUT, narration: AFTER_CUT }, refused: null });
  });

  it("cuts only the unlocked track when a channel is selected, and leaves the locked one exactly as it is", () => {
    // The live check's case: the narration channel selected (video locked),
    // nothing stored on either track. The narration is cut from the WHOLE
    // source; the video stays whole - null, untouched, and NOT a refusal.
    expect(nextEditForCut(NONE, VIDEO_LOCKED, 47.3, 52.3, CORPUS)).toEqual({ next: { video: null, narration: AFTER_CUT }, refused: null });
    // The mirror.
    expect(nextEditForCut(NONE, NARRATION_LOCKED, 47.3, 52.3, CORPUS)).toEqual({ next: { video: AFTER_CUT, narration: null }, refused: null });
    // One track stored: the locked one is untouched whatever it holds, the
    // unlocked one is cut from its own list.
    const stored: TrackEdit = { video: KEEP, narration: null };
    expect(nextEditForCut(stored, NARRATION_LOCKED, 7, 8, SOURCE)).toEqual({
      next: { video: [[0, 6], [7.5, 8.5], [9.5, 12]], narration: null }, refused: null,
    });
    expect(nextEditForCut(stored, VIDEO_LOCKED, 7, 8, SOURCE)).toEqual({
      next: { video: KEEP, narration: [[0, 7], [8, 12]] }, refused: null,
    });
  });

  it("changes nothing with both tracks locked", () => {
    expect(nextEditForCut(NONE, BOTH_LOCKED, 47.3, 52.3, CORPUS)).toEqual({ next: NONE, refused: null });
    expect(nextEditForCut({ video: KEEP, narration: null }, BOTH_LOCKED, 1, 2, SOURCE)).toEqual({ next: { video: KEEP, narration: null }, refused: null });
  });

  it("refuses, naming the track, only when the cut would leave that track with nothing", () => {
    expect(nextEditForCut(NONE, OPEN, 0, CORPUS, CORPUS)).toEqual({ next: null, refused: "video" });
    expect(nextEditForCut(NONE, VIDEO_LOCKED, 0, CORPUS, CORPUS)).toEqual({ next: null, refused: "narration" });
    expect(nextEditForCut({ video: KEEP, narration: null }, NARRATION_LOCKED, 0, 10.5, SOURCE)).toEqual({ next: null, refused: "video" });
    // The narration outruns a cut picture: the same interval leaves it something.
    expect(nextEditForCut({ video: KEEP, narration: null }, OPEN, 0, 10.5, SOURCE)).toEqual({ next: null, refused: "video" });
  });

  it("splits the unlocked tracks at the playhead, every track with `all`, and nothing on a boundary", () => {
    const at = 100;
    const SPLIT: Keep = [[0, 100], [100, 341.008]];
    expect(nextEditForSplit(NONE, OPEN, at, CORPUS)).toEqual({ video: SPLIT, narration: SPLIT });
    expect(nextEditForSplit(NONE, VIDEO_LOCKED, at, CORPUS)).toEqual({ video: null, narration: SPLIT });
    expect(nextEditForSplit(NONE, NARRATION_LOCKED, at, CORPUS)).toEqual({ video: SPLIT, narration: null });
    expect(nextEditForSplit(NONE, BOTH_LOCKED, at, CORPUS)).toEqual(NONE);
    expect(nextEditForSplit(NONE, BOTH_LOCKED, at, CORPUS, true)).toEqual({ video: SPLIT, narration: SPLIT });
    // On the very start, or on an existing boundary, the track is what it was
    // - in the body's terms, so an untouched track stays null and there is
    // nothing to commit.
    expect(nextEditForSplit(NONE, OPEN, 0, CORPUS)).toEqual(NONE);
    expect(nextEditForSplit({ video: SPLIT, narration: null }, OPEN, at, CORPUS)).toEqual({ video: SPLIT, narration: SPLIT });
    expect(sameEdit(nextEditForSplit({ video: SPLIT, narration: SPLIT }, OPEN, at, CORPUS), { video: SPLIT, narration: SPLIT }, CORPUS)).toBe(true);
  });

  it("compares edits in the body's terms", () => {
    expect(sameEdit(NONE, { video: [[0, 12]], narration: [] }, SOURCE)).toBe(true);
    expect(sameEdit({ video: KEEP, narration: null }, { video: KEEP, narration: [[0, 12]] }, SOURCE)).toBe(true);
    expect(sameEdit({ video: KEEP, narration: null }, { video: null, narration: KEEP }, SOURCE)).toBe(false);
    expect(sameEdit(NONE, { video: [[0, 6], [6, 12]], narration: null }, SOURCE)).toBe(false);
  });

  it("lets a click select a piece only once a lane has more than one", () => {
    expect(clickSelectsPiece(pieces([[0, 341.008]], CORPUS))).toBe(false);
    expect(clickSelectsPiece(pieces([], CORPUS))).toBe(false);
    expect(clickSelectsPiece(pieces([[0, 100], [100, 341.008]], CORPUS))).toBe(true);
    expect(clickSelectsPiece(pieces(KEEP, SOURCE))).toBe(true);
  });
});

describe("snap — within the threshold, to the nearest candidate", () => {
  it("lands on the nearest candidate inside the threshold", () => {
    expect(snap(10.03, [10, 20], 0.05)).toEqual({ t: 10, snapped: 10 });
    expect(snap(19.96, [10, 20], 0.05)).toEqual({ t: 20, snapped: 20 });
    expect(snap(15.02, [15.05, 15], 0.05)).toEqual({ t: 15, snapped: 15 });
  });

  it("leaves a moment alone outside it, and with nothing to snap to", () => {
    expect(snap(10.06, [10, 20], 0.05)).toEqual({ t: 10.06, snapped: null });
    expect(snap(10.06, [], 0.05)).toEqual({ t: 10.06, snapped: null });
    expect(snap(10.05, [10], 0.05)).toEqual({ t: 10.05, snapped: null });  // the threshold is exclusive
  });

  it("gives a tie to the first candidate", () => {
    expect(snap(10, [9.98, 10.02], 0.05)).toEqual({ t: 9.98, snapped: 9.98 });
  });
});

describe("dragOffsets — a drag commits offsets, nothing else", () => {
  it("moves every block by the same delta, measured from where it is drawn", () => {
    expect(dragOffsets([
      { index: 3, start: 10, offset: null },
      { index: 4, start: 12, offset: 0.4 },
    ], 1.5)).toEqual([{ index: 3, offset: 1.5 }, { index: 4, offset: 1.9 }]);
    expect(dragOffsets([{ index: 0, start: 5, offset: -0.4 }], -0.6)).toEqual([{ index: 0, offset: -1 }]);
  });

  it("rounds to the stored precision and turns 0 into null", () => {
    expect(dragOffsets([{ index: 0, start: 5, offset: 0.4 }], -0.4)).toEqual([{ index: 0, offset: null }]);
    expect(dragOffsets([{ index: 0, start: 5, offset: 0.4 }], -0.4004)).toEqual([{ index: 0, offset: null }]);
    expect(dragOffsets([{ index: 0, start: 5, offset: null }], 0.12345)).toEqual([{ index: 0, offset: 0.123 }]);
  });

  it("clamps to the server's bound", () => {
    expect(MAX_OFFSET).toBe(300);
    expect(dragOffsets([{ index: 0, start: 5, offset: 299 }], 5)).toEqual([{ index: 0, offset: 300 }]);
    expect(dragOffsets([{ index: 0, start: 500, offset: -299 }], -5)).toEqual([{ index: 0, offset: -300 }]);
  });

  it("lands a block that was floored at zero where the pointer let go of it", () => {
    // Start 1 s, offset -5: drawn at 0. Dragged 2 s right it must sit at 2 s
    // (offset +1), not stay floored at 0 with an offset of -3.
    expect(dragOffsets([{ index: 0, start: 1, offset: -5 }], 2)).toEqual([{ index: 0, offset: 1 }]);
    // The caller passes what is DRAWN - the plan's `pinned_start − start`
    // (here 0 − 1) - never the page's stored number, and the answer is the same.
    expect(dragOffsets([{ index: 0, start: 1, offset: 0 - 1 }], 2)).toEqual([{ index: 0, offset: 1 }]);
    expect(dragOffsets([{ index: 4, start: 12, offset: 12.4 - 12 }], 1.5)).toEqual([{ index: 4, offset: 1.9 }]);
  });
});

describe("the pointer gestures — what an unmoved release means, and which clicks it swallows", () => {
  it("seeks from the ruler and does nothing from the head", () => {
    expect(unmovedRelease("scrub", true, false)).toBe("seek");
    expect(unmovedRelease("scrub", false, false)).toBe("nothing");
  });

  it("picks the piece under a marquee's start, leaves a block's click to the block, keeps the selection for the rest", () => {
    expect(unmovedRelease("marquee", false, false)).toBe("pick");
    expect(unmovedRelease("move", false, false)).toBe("click");
    for (const kind of ["in", "out", "range"] as const) expect(unmovedRelease(kind, false, false)).toBe("keep-selection");
  });

  it("picks the piece when its edge is pressed and released without moving - the edge is a zone of the piece", () => {
    // E5a: a press on a piece's trim zone that never moved is a click on the
    // piece, and it must mean what a click beside the zone means (select the
    // piece, or seek on a single-piece or locked lane) - not a seek from the
    // strip, which is where a suppressed-nothing click would have landed on
    // the Narration lane.
    expect(unmovedRelease("piece-trim", false, false)).toBe("pick");
    expect(unmovedRelease("piece-trim", true, false)).toBe("pick");
  });

  it("does nothing for a pointer the browser cancelled, whatever the kind", () => {
    for (const kind of ["scrub", "in", "out", "range", "move", "marquee", "piece-trim"] as const) {
      expect(unmovedRelease(kind, true, true)).toBe("nothing");
    }
  });

  it("swallows the follow-up click after a real drag, a scrub, a marquee or a piece edge - never after an unmoved block or handle release", () => {
    for (const kind of ["scrub", "in", "out", "range", "move", "marquee", "piece-trim"] as const) expect(releaseSuppressesClick(kind, true)).toBe(true);
    expect(releaseSuppressesClick("scrub", false)).toBe(true);
    expect(releaseSuppressesClick("marquee", false)).toBe(true);
    // The release itself picked, so the click that follows must not seek a second time.
    expect(releaseSuppressesClick("piece-trim", false)).toBe(true);
    expect(releaseSuppressesClick("move", false)).toBe(false);
    expect(releaseSuppressesClick("range", false)).toBe(false);
    expect(releaseSuppressesClick("in", false)).toBe(false);
    expect(releaseSuppressesClick("out", false)).toBe(false);
  });
});

describe("the fixture's tracks — two lists, one axis", () => {
  const tracks = cases.tracks;
  const list = (keep: Keep | null) => (keep && keep.length > 0 ? keep : wholeKeep(SOURCE));

  it("is the shared scenario, not an emptied section", () => {
    expect(tracks.segments).toHaveLength(5);
    expect(tracks.video).toEqual(KEEP);
    expect(Object.keys(tracks.cases).sort()).toEqual(["both", "narration_only", "together", "video_only"]);
  });

  it("places every listed sentence through the NARRATION list, and none of the dropped ones", () => {
    for (const [name, c] of Object.entries(tracks.cases)) {
      const through = list(c.narration);
      const listed = new Map(c.sentences.map(([index, start, end]) => [index, { start, end }]));
      tracks.segments.forEach(([start], index) => {
        const landed = toTimeline(start, through);
        const expected = listed.get(index);
        if (expected) expect(landed, `${name}: sentence ${index}`).toBe(expected.start);
        else expect(landed, `${name}: sentence ${index} was dropped`).toBeNull();
      });
      // The picture's length is the VIDEO list's.
      expect(outputDuration(list(c.video)), name).toBe(c.duration);
      expect(outputDuration(list(c.narration)), name).toBe(c.narration_duration);
      expect(!wholeSource(list(c.video), SOURCE), name).toBe(c.cut);
      expect(!wholeSource(list(c.narration), SOURCE), name).toBe(c.projected);
    }
  });

  it("would place them wrongly through the VIDEO list — the trap the cases exist to catch", () => {
    const c = tracks.cases.both;
    const wrong = c.sentences.filter(([index, start]) => toTimeline(tracks.segments[index][0], list(c.video)) !== start);
    expect(wrong.length).toBeGreaterThan(0);
    // ... while a video-only edit moves no sentence at all: nothing is projected.
    for (const [index, start] of tracks.cases.video_only.sentences) expect(start).toBe(tracks.segments[index][0]);
  });

  it("draws each lane's pieces and joins from its own list", () => {
    const c = tracks.cases.both;
    expect(pieces(list(c.video), SOURCE).map((p) => [p.start, p.end])).toEqual([[0, 6], [6, 10.5]]);
    expect(pieces(list(c.narration), SOURCE).map((p) => [p.start, p.end])).toEqual([[0, 6.4], [6.4, 11.4]]);
    expect(joins(list(c.video), SOURCE).map((j) => j.at)).toEqual([6]);
    expect(joins(list(c.narration), SOURCE).map((j) => j.at)).toEqual([6.4]);
    // A locked (absent) track is one piece and no join.
    expect(pieces(list(tracks.cases.video_only.narration), SOURCE)).toHaveLength(1);
    expect(joins(list(tracks.cases.video_only.narration), SOURCE)).toEqual([]);
  });
});

describe("EPSILON", () => {
  it("is half a millisecond, as the server's", () => {
    expect(EPSILON).toBe(0.0005);
  });
});

// ── E5a: the trim — a piece's edge is the cut, and moves ────────────────────

/** A repeatable pseudo-random source, so a failing round is the same round twice. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The list's invariants, as the server's `validate_keep` reads them and a piece's floor adds to them. */
function expectValidKeep(keep: Keep, sourceDuration: number, where: string) {
  let previous = 0;
  for (const [start, end] of keep) {
    expect(start, `${where}: start >= previous end`).toBeGreaterThanOrEqual(previous - EPSILON);
    expect(end, `${where}: start < end`).toBeGreaterThan(start);
    expect(end, `${where}: within the source`).toBeLessThanOrEqual(sourceDuration + EPSILON);
    expect(round3(start), `${where}: 3 dp`).toBe(start);
    expect(round3(end), `${where}: 3 dp`).toBe(end);
    previous = end;
  }
}

describe("trimPiece — a bound moved, clamped so the list stays what the server stores", () => {
  // KEEP is [[0, 6], [7.5, 12]] over a 12 s source: two pieces, a 1.5 s hole
  // between them, laid out on the output as 0–6 and 6–10.5.
  const FRAME = round3(FRAME_SECONDS);

  it("shortens from the start when an in-edge is dragged right, and restores when it is dragged left", () => {
    expect(trimPiece(KEEP, 1, "in", 8.2, SOURCE)).toEqual([[0, 6], [8.2, 12]]);
    expect(trimPiece(KEEP, 1, "in", 6.5, SOURCE)).toEqual([[0, 6], [6.5, 12]]);
  });

  it("restores an in-edge no further than the previous range's end, and the first no further than 0", () => {
    expect(trimPiece(KEEP, 1, "in", 2, SOURCE)).toEqual([[0, 6], [6, 12]]);
    expect(trimPiece(KEEP, 1, "in", -50, SOURCE)).toEqual([[0, 6], [6, 12]]);
    // The head trim, and the head restored.
    expect(trimPiece(KEEP, 0, "in", 1.5, SOURCE)).toEqual([[1.5, 6], [7.5, 12]]);
    expect(trimPiece([[2, 6], [7.5, 12]], 0, "in", -3, SOURCE)).toEqual([[0, 6], [7.5, 12]]);
  });

  it("shortens from the end when an out-edge is dragged left, and restores when it is dragged right", () => {
    expect(trimPiece(KEEP, 0, "out", 5, SOURCE)).toEqual([[0, 5], [7.5, 12]]);
    expect(trimPiece(KEEP, 0, "out", 7, SOURCE)).toEqual([[0, 7], [7.5, 12]]);
  });

  it("restores an out-edge no further than the next range's start, and the last no further than the source's end", () => {
    expect(trimPiece(KEEP, 0, "out", 99, SOURCE)).toEqual([[0, 7.5], [7.5, 12]]);
    // The tail trim, and the tail restored.
    expect(trimPiece(KEEP, 1, "out", 11, SOURCE)).toEqual([[0, 6], [7.5, 11]]);
    expect(trimPiece([[0, 6], [7.5, 11]], 1, "out", 99, SOURCE)).toEqual([[0, 6], [7.5, 12]]);
    expect(trimPiece([[0, 6], [7.5, 11]], 1, "out", 99, 12.0004)).toEqual([[0, 6], [7.5, 12]]);
  });

  it("stops one frame short of removing the piece, from either edge (decision 6)", () => {
    const fromStart = trimPiece(KEEP, 0, "in", 99, SOURCE);
    expect(fromStart).toEqual([[round3(6 - FRAME_SECONDS), 6], [7.5, 12]]);
    expect(round3(fromStart[0][1] - fromStart[0][0])).toBe(FRAME);
    const fromEnd = trimPiece(KEEP, 1, "out", 0, SOURCE);
    expect(fromEnd).toEqual([[0, 6], [7.5, round3(7.5 + FRAME_SECONDS)]]);
    expect(round3(fromEnd[1][1] - fromEnd[1][0])).toBe(FRAME);
    // Exactly at the floor is allowed; a hair past it is the floor.
    expect(trimPiece(KEEP, 0, "in", 6 - FRAME_SECONDS, SOURCE)).toEqual(fromStart);
    expect(trimPiece(KEEP, 0, "in", 5.99, SOURCE)).toEqual(fromStart);
    expect(atFrameFloor(fromStart, 0)).toBe(true);
    expect(atFrameFloor(fromEnd, 1)).toBe(true);
    expect(atFrameFloor(KEEP, 0)).toBe(false);
    expect(atFrameFloor(KEEP, 9)).toBe(false);
  });

  it("lets a piece already shorter than a frame only grow, never shrink (a split can leave one)", () => {
    const tiny: Keep = [[0, 4], [5, 5.01], [6, 12]];
    expect(trimPiece(tiny, 1, "in", 99, SOURCE)).toBe(tiny);
    expect(trimPiece(tiny, 1, "out", 0, SOURCE)).toBe(tiny);
    expect(trimPiece(tiny, 1, "in", 4.5, SOURCE)).toEqual([[0, 4], [4.5, 5.01], [6, 12]]);
    expect(trimPiece(tiny, 1, "out", 5.5, SOURCE)).toEqual([[0, 4], [5, 5.5], [6, 12]]);
    expect(atFrameFloor(tiny, 1)).toBe(true);
  });

  it("reads an empty or whole list as the whole source first, as pieces() does", () => {
    expect(trimPiece([], 0, "in", 2, SOURCE)).toEqual([[2, 12]]);
    expect(trimPiece([[0, 12]], 0, "out", 10, SOURCE)).toEqual([[0, 10]]);
    expect(trimPiece([], 0, "out", 99, SOURCE)).toEqual([[0, 12]]);
  });

  it("returns an equal list — the same one — when the bound does not move, or the piece is not there", () => {
    expect(trimPiece(KEEP, 1, "in", 7.5, SOURCE)).toBe(KEEP);
    expect(trimPiece(KEEP, 0, "out", 6, SOURCE)).toBe(KEEP);
    expect(trimPiece(KEEP, 0, "out", 6.0004, SOURCE)).toBe(KEEP);
    expect(trimPiece(KEEP, 5, "in", 3, SOURCE)).toBe(KEEP);
    expect(trimPiece(KEEP, -1, "in", 3, SOURCE)).toBe(KEEP);
    // A bare split has no gap: neither edge at the boundary can restore anything.
    const split: Keep = [[0, 6], [6, 12]];
    expect(trimPiece(split, 0, "out", 7, SOURCE)).toBe(split);
    expect(trimPiece(split, 1, "in", 5, SOURCE)).toBe(split);
  });

  it("rounds once, at the bound, and never touches the other ranges", () => {
    expect(trimPiece(KEEP, 1, "in", 8.12345, SOURCE)).toEqual([[0, 6], [8.123, 12]]);
    expect(trimPiece(KEEP, 1, "in", 8.12345, SOURCE)[0]).toBe(KEEP[0]);
  });

  it("never overlaps, never disorders, never empties a piece — over random lists and drags", () => {
    const random = mulberry32(20260924);
    for (let round = 0; round < 3000; round++) {
      const source = round3(5 + random() * 30);
      const bounds = new Set<number>();
      const count = 1 + Math.floor(random() * 4);
      while (bounds.size < count * 2) bounds.add(round3(random() * source));
      const sorted = [...bounds].sort((x, y) => x - y);
      const keep: Keep = [];
      for (let i = 0; i < sorted.length; i += 2) if (sorted[i + 1] - sorted[i] > EPSILON) keep.push([sorted[i], sorted[i + 1]]);
      if (keep.length === 0) continue;
      const index = Math.floor(random() * keep.length);
      const edge: TrimEdge = random() < 0.5 ? "in" : "out";
      const toSource = round3(-5 + random() * (source + 10));
      const next = trimPiece(keep, index, edge, toSource, source);
      const where = `round ${round}: ${JSON.stringify(keep)} ${edge} ${index} -> ${toSource}`;
      expect(next, where).toHaveLength(keep.length);
      expectValidKeep(next, source, where);
      // Only the dragged bound moved.
      next.forEach((range, i) => {
        if (i !== index) expect(range, where).toBe(keep[i]);
        else if (edge === "in") expect(range[1], where).toBe(keep[i][1]);
        else expect(range[0], where).toBe(keep[i][0]);
      });
      // The floor: a piece longer than a frame is never left shorter than one.
      const was = round3(keep[index][1] - keep[index][0]);
      const is = round3(next[index][1] - next[index][0]);
      if (was >= round3(FRAME_SECONDS)) expect(is, where).toBeGreaterThanOrEqual(round3(FRAME_SECONDS) - EPSILON);
      else expect(is, where).toBeGreaterThanOrEqual(was - EPSILON);
    }
  });
});

describe("restoreRange — the inverse of a cut, bounded by the gap the cut left on THAT list", () => {
  it("moves the range that starts at the boundary back into the gap before it", () => {
    expect(restoreRange(KEEP, 6, 1, "before", SOURCE)).toEqual([[0, 6], [6.5, 12]]);
    // As much as the gap holds, and no more.
    expect(restoreRange(KEEP, 6, 5, "before", SOURCE)).toEqual([[0, 6], [6, 12]]);
  });

  it("moves the range that ends at the boundary forward into the gap after it", () => {
    expect(restoreRange(KEEP, 6, 1, "after", SOURCE)).toEqual([[0, 7], [7.5, 12]]);
    expect(restoreRange(KEEP, 6, 5, "after", SOURCE)).toEqual([[0, 7.5], [7.5, 12]]);
  });

  it("restores the head at 0 with `before`, bounded by 0, and the tail at the output's end with `after`, bounded by the source", () => {
    expect(restoreRange([[2, 6], [7.5, 12]], 0, 1, "before", SOURCE)).toEqual([[1, 6], [7.5, 12]]);
    expect(restoreRange([[2, 6], [7.5, 12]], 0, 5, "before", SOURCE)).toEqual([[0, 6], [7.5, 12]]);
    // [[0, 6], [7.5, 11]] runs to 9.5 on the output.
    expect(restoreRange([[0, 6], [7.5, 11]], 9.5, 0.5, "after", SOURCE)).toEqual([[0, 6], [7.5, 11.5]]);
    expect(restoreRange([[0, 6], [7.5, 11]], 9.5, 5, "after", SOURCE)).toEqual([[0, 6], [7.5, 12]]);
  });

  it("leaves the list exactly as it is — the same object — when nothing starts or ends there", () => {
    expect(restoreRange(KEEP, 3, 1, "before", SOURCE)).toBe(KEEP);
    expect(restoreRange(KEEP, 3, 1, "after", SOURCE)).toBe(KEEP);
    // No range ENDS at 0 and none STARTS at the output's end.
    expect(restoreRange(KEEP, 0, 1, "after", SOURCE)).toBe(KEEP);
    expect(restoreRange(KEEP, 10.5, 1, "before", SOURCE)).toBe(KEEP);
    // Trap 38, the case that matters: the narration never lost anything at 6.
    const whole: Keep = [[0, 12]];
    expect(restoreRange(whole, 6, 1, "before", SOURCE)).toBe(whole);
    expect(restoreRange(whole, 6, 1, "after", SOURCE)).toBe(whole);
    const none: Keep = [];
    expect(restoreRange(none, 0, 1, "before", SOURCE)).toBe(none);
  });

  it("changes nothing at a bare split, whose gap is empty, and with nothing to restore", () => {
    const split: Keep = [[0, 6], [6, 12]];
    expect(restoreRange(split, 6, 1, "before", SOURCE)).toBe(split);
    expect(restoreRange(split, 6, 1, "after", SOURCE)).toBe(split);
    expect(restoreRange(KEEP, 6, 0, "before", SOURCE)).toBe(KEEP);
    expect(restoreRange(KEEP, 6, -1, "after", SOURCE)).toBe(KEEP);
    expect(restoreRange(KEEP, 6, Number.NaN, "after", SOURCE)).toBe(KEEP);
  });

  it("finds the boundary within half a millisecond, and rounds the bound once", () => {
    expect(restoreRange(KEEP, 6.0004, 1, "before", SOURCE)).toEqual([[0, 6], [6.5, 12]]);
    expect(restoreRange(KEEP, 5.9996, 1, "after", SOURCE)).toEqual([[0, 7], [7.5, 12]]);
    expect(restoreRange(KEEP, 6, 0.12345, "before", SOURCE)).toEqual([[0, 6], [7.377, 12]]);
  });
});

describe("trimChange — what one bound's move does to the output", () => {
  it("is a cut of the removed stretch, at the piece's edge as laid out now", () => {
    expect(trimChange(KEEP, 1, "in", 8.2, SOURCE)).toEqual({ kind: "cut", a: 6, b: 6.7 });
    expect(trimChange(KEEP, 0, "out", 5, SOURCE)).toEqual({ kind: "cut", a: 5, b: 6 });
  });

  it("is an insert of the restored length at the edge", () => {
    expect(trimChange(KEEP, 1, "in", 6.5, SOURCE)).toEqual({ kind: "insert", t: 6, d: 1 });
    expect(trimChange(KEEP, 0, "out", 7, SOURCE)).toEqual({ kind: "insert", t: 6, d: 1 });
    expect(trimChange([[2, 6], [7.5, 12]], 0, "in", 0, SOURCE)).toEqual({ kind: "insert", t: 0, d: 2 });
  });

  it("is nothing when the bound does not move, or the piece is not there", () => {
    expect(trimChange(KEEP, 1, "in", 7.5, SOURCE)).toBeNull();
    expect(trimChange(KEEP, 1, "in", 7.5004, SOURCE)).toBeNull();
    expect(trimChange(KEEP, 4, "in", 7.5, SOURCE)).toBeNull();
  });
});

describe("nextEditForTrim — one gesture, two lists (spec §13.1)", () => {
  const OPEN = { video: false, narration: false };
  const VIDEO_LOCKED = { video: true, narration: false };
  const NARRATION_LOCKED = { video: false, narration: true };

  it("shortens the picture and removes the same output interval from the narration, exactly as a cut does", () => {
    const outcome = nextEditForTrim({ video: KEEP, narration: null }, OPEN, "video", 1, "in", 8.2, SOURCE);
    expect(outcome.change).toEqual({ kind: "cut", a: 6, b: 6.7 });
    expect(outcome.next).toEqual({ video: [[0, 6], [8.2, 12]], narration: [[0, 6], [6.7, 12]] });
    expect(outcome.next?.narration).toEqual(removeRange([[0, 12]], 6, 6.7));
    expect(outcome.refused).toBeNull();
    expect(outcome.picture).toEqual({ kind: "cut", a: 6, b: 6.7 });
  });

  it("leaves a locked track exactly as it is, whatever the trimmed lane does (trap 19)", () => {
    const outcome = nextEditForTrim({ video: KEEP, narration: null }, NARRATION_LOCKED, "video", 1, "in", 8.2, SOURCE);
    expect(outcome.next).toEqual({ video: [[0, 6], [8.2, 12]], narration: null });
    expect(nextEditForTrim({ video: KEEP, narration: KEEP }, VIDEO_LOCKED, "narration", 0, "out", 5, SOURCE).next)
      .toEqual({ video: KEEP, narration: [[0, 5], [7.5, 12]] });
  });

  it("restores the picture and lets the narration's own bound back into its OWN gap at that moment", () => {
    const both: TrackEdit = { video: KEEP, narration: KEEP };
    const outcome = nextEditForTrim(both, OPEN, "video", 1, "in", 6.5, SOURCE);
    expect(outcome.change).toEqual({ kind: "insert", t: 6, d: 1 });
    expect(outcome.next).toEqual({ video: [[0, 6], [6.5, 12]], narration: [[0, 6], [6.5, 12]] });
    expect(outcome.next?.narration).toEqual(restoreRange(KEEP, 6, 1, "before", SOURCE));
    // The out-edge of the piece before the hole, the other way round.
    const back = nextEditForTrim(both, OPEN, "video", 0, "out", 7, SOURCE);
    expect(back.change).toEqual({ kind: "insert", t: 6, d: 1 });
    expect(back.next).toEqual({ video: [[0, 7], [7.5, 12]], narration: [[0, 7], [7.5, 12]] });
  });

  it("restores only as much of the other track as ITS gap holds", () => {
    // The narration's hole at output 6 is 6–7 of the source; the picture's is 6–7.5.
    const outcome = nextEditForTrim({ video: KEEP, narration: [[0, 6], [7, 12]] }, OPEN, "video", 1, "in", 6, SOURCE);
    expect(outcome.change).toEqual({ kind: "insert", t: 6, d: 1.5 });
    expect(outcome.next).toEqual({ video: [[0, 6], [6, 12]], narration: [[0, 6], [6, 12]] });
  });

  it("never invents narration the narration list never lost (trap 38): no join there, nothing restored", () => {
    // A picture cut made with Narration locked, restored later: the frames
    // come back and the sentences stay exactly where they are.
    const outcome = nextEditForTrim({ video: KEEP, narration: null }, OPEN, "video", 1, "in", 6.5, SOURCE);
    expect(outcome.next).toEqual({ video: [[0, 6], [6.5, 12]], narration: null });
    // A narration with a join elsewhere is equally untouched.
    const elsewhere = nextEditForTrim({ video: KEEP, narration: [[0, 3], [4, 12]] }, OPEN, "video", 1, "in", 6.5, SOURCE);
    expect(elsewhere.next?.narration).toEqual([[0, 3], [4, 12]]);
  });

  it("refuses, naming the track, when a shortening would empty the other track", () => {
    // The narration keeps only output 0–1 (source 6–7); trimming the head of
    // the whole picture by 2 s removes all of it.
    const outcome = nextEditForTrim({ video: null, narration: [[6, 7]] }, OPEN, "video", 0, "in", 2, SOURCE);
    expect(outcome).toEqual({ next: null, refused: "narration", change: { kind: "cut", a: 0, b: 2 }, picture: null });
    const mirror = nextEditForTrim({ video: [[6, 7]], narration: null }, OPEN, "narration", 0, "in", 2, SOURCE);
    expect(mirror.refused).toBe("video");
    // Never the trimmed lane itself: its floor is a frame, so it cannot be emptied.
    expect(nextEditForTrim({ video: null, narration: null }, OPEN, "video", 0, "in", 99, SOURCE).refused).toBeNull();
  });

  it("changes nothing when the trimmed lane is locked, and hands the same edit back", () => {
    const edit: TrackEdit = { video: KEEP, narration: null };
    const outcome = nextEditForTrim(edit, VIDEO_LOCKED, "video", 1, "in", 8.2, SOURCE);
    expect(outcome).toEqual({ next: edit, refused: null, change: null, picture: null });
    expect(outcome.next).toBe(edit);
    expect(sameEdit(outcome.next!, edit, SOURCE)).toBe(true);
  });

  it("is a no-op when the bound does not move, in the body's own terms", () => {
    const edit: TrackEdit = { video: KEEP, narration: null };
    const outcome = nextEditForTrim(edit, OPEN, "video", 1, "in", 7.5, SOURCE);
    expect(outcome.change).toBeNull();
    expect(outcome.next).toBe(edit);
    // A whole track stays null after a trim that restores everything a cut took.
    const whole = nextEditForTrim({ video: [[2, 12]], narration: null }, OPEN, "video", 0, "in", 0, SOURCE);
    expect(whole.next).toEqual({ video: null, narration: null });
  });

  it("reports the PICTURE's own change for the music — the trimmed lane's when the picture is it", () => {
    const cut = nextEditForTrim({ video: KEEP, narration: null }, OPEN, "video", 0, "out", 5, SOURCE);
    expect(cut.picture).toEqual(cut.change);
    const restore = nextEditForTrim({ video: KEEP, narration: KEEP }, OPEN, "video", 0, "out", 7, SOURCE);
    expect(restore.picture).toEqual(restore.change);
  });

  it("reports no picture change for a narration trim the picture did not follow (the music rides the picture)", () => {
    // Video locked: the picture cannot change.
    expect(nextEditForTrim({ video: KEEP, narration: KEEP }, VIDEO_LOCKED, "narration", 1, "in", 8.2, SOURCE).picture).toBeNull();
    expect(nextEditForTrim({ video: KEEP, narration: KEEP }, VIDEO_LOCKED, "narration", 1, "in", 6.5, SOURCE).picture).toBeNull();
    // Video unlocked but whole: no join at 6, so a narration restore there leaves the picture alone.
    const noJoin = nextEditForTrim({ video: null, narration: KEEP }, OPEN, "narration", 1, "in", 6.5, SOURCE);
    expect(noJoin.change).toEqual({ kind: "insert", t: 6, d: 1 });
    expect(noJoin.next?.video).toBeNull();
    expect(noJoin.picture).toBeNull();
    // A narration cut past the end of a shorter picture changes no frame.
    const beyond = nextEditForTrim({ video: [[0, 6]], narration: null }, OPEN, "narration", 0, "out", 10, SOURCE);
    expect(beyond.change).toEqual({ kind: "cut", a: 10, b: 12 });
    expect(beyond.next?.video).toEqual([[0, 6]]);
    expect(beyond.picture).toBeNull();
  });

  it("reports the picture's change as what its list really did: the gap it had, the end it has", () => {
    // The narration restores 1.5 s at 6; the picture's own hole there is only 1 s.
    const half = nextEditForTrim({ video: [[0, 6], [7, 12]], narration: KEEP }, OPEN, "narration", 1, "in", 6, SOURCE);
    expect(half.change).toEqual({ kind: "insert", t: 6, d: 1.5 });
    expect(half.next?.video).toEqual([[0, 6], [6, 12]]);
    expect(half.picture).toEqual({ kind: "insert", t: 6, d: 1 });
    // A narration cut of 9–12 on a picture that ends at 10 removes 9–10 of it.
    const clipped = nextEditForTrim({ video: [[0, 10]], narration: null }, OPEN, "narration", 0, "out", 9, SOURCE);
    expect(clipped.change).toEqual({ kind: "cut", a: 9, b: 12 });
    expect(clipped.next?.video).toEqual([[0, 9]]);
    expect(clipped.picture).toEqual({ kind: "cut", a: 9, b: 10 });
  });

  it("is a cut with a name (trap 37): the trimmed lane's own list is what removeRange / restoreRange give it", () => {
    // Over random lists and drags, the list `trimPiece` produces for the
    // trimmed lane is exactly the list the OTHER track's arithmetic would
    // produce for the same output interval — so there is one ripple, not two.
    const random = mulberry32(13);
    let cuts = 0;
    let inserts = 0;
    for (let round = 0; round < 2000; round++) {
      const source = round3(5 + random() * 30);
      const bounds = new Set<number>();
      const count = 1 + Math.floor(random() * 4);
      while (bounds.size < count * 2) bounds.add(round3(random() * source));
      const sorted = [...bounds].sort((x, y) => x - y);
      const keep: Keep = [];
      for (let i = 0; i < sorted.length; i += 2) if (sorted[i + 1] - sorted[i] > EPSILON) keep.push([sorted[i], sorted[i + 1]]);
      if (keep.length === 0) continue;
      const index = Math.floor(random() * keep.length);
      const edge: TrimEdge = random() < 0.5 ? "in" : "out";
      const toSource = round3(-5 + random() * (source + 10));
      const outcome = nextEditForTrim({ video: keep, narration: null }, OPEN, "video", index, edge, toSource, source);
      const where = `round ${round}: ${JSON.stringify(keep)} ${edge} ${index} -> ${toSource}`;
      if (outcome.change === null) { expect(outcome.next, where).toEqual({ video: keep, narration: null }); continue; }
      const own = trackList(outcome.next!.video, source);
      if (outcome.change.kind === "cut") {
        cuts += 1;
        expect(own, where).toEqual(removeRange(keep, outcome.change.a, outcome.change.b));
      } else {
        inserts += 1;
        expect(own, where).toEqual(restoreRange(keep, outcome.change.t, outcome.change.d, edge === "in" ? "before" : "after", source));
      }
      // ... and the output moved by exactly what the change says.
      const moved = round3(outputDuration(own) - outputDuration(keep));
      expect(moved, where).toBe(outcome.change.kind === "cut" ? round3(outcome.change.a - outcome.change.b) : outcome.change.d);
    }
    expect(cuts).toBeGreaterThan(100);
    expect(inserts).toBeGreaterThan(100);
  });
});

describe("pieceEdgeAt — which edge of which piece a pointer has", () => {
  // KEEP's pieces on the output: 0–6 and 6–10.5, a join at 6. 8 px at 40 px/s.
  const list = pieces(KEEP, SOURCE);
  const edge = 8 / 40;

  it("splits the join: the left third is the left piece's out-edge, the right third the right piece's in-edge", () => {
    expect(pieceEdgeAt(list, 5.9, edge)).toEqual({ index: 0, edge: "out" });
    expect(pieceEdgeAt(list, 5.8, edge)).toEqual({ index: 0, edge: "out" });
    expect(pieceEdgeAt(list, 6.1, edge)).toEqual({ index: 1, edge: "in" });
    expect(pieceEdgeAt(list, 6.2, edge)).toEqual({ index: 1, edge: "in" });
    // The join itself belongs to the later piece, as `pieceAt` and `toSource` choose.
    expect(pieceEdgeAt(list, 6, edge)).toEqual({ index: 1, edge: "in" });
    // Beyond the zone on either side is the body: no edge.
    expect(pieceEdgeAt(list, 5.79, edge)).toBeNull();
    expect(pieceEdgeAt(list, 6.21, edge)).toBeNull();
    expect(pieceEdgeAt(list, 3, edge)).toBeNull();
  });

  it("gives a single whole piece a head in-edge and a tail out-edge, and a body between", () => {
    const whole = pieces([], SOURCE);
    expect(pieceEdgeAt(whole, 0, edge)).toEqual({ index: 0, edge: "in" });
    expect(pieceEdgeAt(whole, 0.19, edge)).toEqual({ index: 0, edge: "in" });
    expect(pieceEdgeAt(whole, 0.21, edge)).toBeNull();
    expect(pieceEdgeAt(whole, 11.81, edge)).toEqual({ index: 0, edge: "out" });
    expect(pieceEdgeAt(whole, 12, edge)).toEqual({ index: 0, edge: "out" });
    // The first in-edge and the last out-edge of a cut list too: the head and tail trims.
    expect(pieceEdgeAt(list, 0.1, edge)).toEqual({ index: 0, edge: "in" });
    expect(pieceEdgeAt(list, 10.4, edge)).toEqual({ index: 1, edge: "out" });
  });

  it("leaves a narrow piece a middle third to click: a third each side, never more", () => {
    const narrow = pieces([[0, 6], [6, 6.3], [6.3, 12]], SOURCE);
    expect(pieceEdgeAt(narrow, 6.05, edge)).toEqual({ index: 1, edge: "in" });
    expect(pieceEdgeAt(narrow, 6.15, edge)).toBeNull();
    expect(pieceEdgeAt(narrow, 6.25, edge)).toEqual({ index: 1, edge: "out" });
    // However wide the zone is asked to be, the middle third survives.
    expect(pieceEdgeAt(narrow, 6.15, 5)).toBeNull();
    expect(pieceEdgeAt(narrow, 6.09, 5)).toEqual({ index: 1, edge: "in" });
  });

  it("answers nothing outside every piece, and for no pieces", () => {
    expect(pieceEdgeAt(list, -0.5, edge)).toBeNull();
    expect(pieceEdgeAt(list, 11, edge)).toBeNull();
    expect(pieceEdgeAt([], 1, edge)).toBeNull();
  });
});

describe("snapTargets — one candidate set for every gesture that places a cut", () => {
  const clip = (id: string, at: number, out: number): MusicClip => ({ id, file: "bed.mp3", at, in: 0, out, gain: 0.15, fade_in: 0, fade_out: 0 });

  it("offers 0, the output's end, the playhead, every join, every pin and every clip's two edges, deduplicated, rounded, sorted", () => {
    expect(snapTargets({
      playhead: 3.0004,
      duration: 10.5,
      joins: [6, 6, 8.9996],
      pins: [0, 2, 5.0001, 7],
      clips: [clip("a", 1, 6), clip("b", 9, 1.5)],
    })).toEqual([0, 1, 2, 3, 5, 6, 7, 9, 10.5]);
  });

  it("is the three fixed moments alone with nothing else on the strip", () => {
    expect(snapTargets({ playhead: 0, duration: 12, joins: [], pins: [], clips: [] })).toEqual([0, 12]);
    expect(snapTargets({ playhead: 4, duration: 12, joins: [], pins: [], clips: [] })).toEqual([0, 4, 12]);
  });

  it("feeds `snap` unchanged", () => {
    const targets = snapTargets({ playhead: 4, duration: 12, joins: [6], pins: [2.5], clips: [] });
    expect(snap(2.52, targets, 0.05)).toEqual({ t: 2.5, snapped: 2.5 });
    expect(snap(6.04, targets, 0.05)).toEqual({ t: 6, snapped: 6 });
    expect(snap(9, targets, 0.05)).toEqual({ t: 9, snapped: null });
  });
});

describe("caughtAfterClamp — ⌖ means caught, not asked for", () => {
  it("keeps the candidate only when the painted position is it, within half a millisecond", () => {
    expect(caughtAfterClamp(6, 6)).toBe(6);
    expect(caughtAfterClamp(6.0004, 6)).toBe(6);
    expect(caughtAfterClamp(8, 6)).toBeNull();
    expect(caughtAfterClamp(6.001, 6)).toBeNull();
    expect(caughtAfterClamp(6, null)).toBeNull();
  });

  it("drops the ⌖ when trimPiece's clamp overrides the snap — the Reviewer's walk (E5a, MINOR 1)", () => {
    // keep [[0, 10], [12, 30]] over 60 s: piece 1 is output 10–28 (source
    // 12–30) with a 2 s gap before it. The playhead parked at output 6 is a
    // candidate; the in-edge of piece 1 dragged left to it lands the POINTER
    // on 6, but the edge can restore only to the previous range's end
    // (source 10, output 8) — so the label must not say ⌖.
    const keep: Keep = [[0, 10], [12, 30]];
    const list = pieces(keep, 60);
    const targets = snapTargets({ playhead: 6, duration: 28, joins: joins(keep, 60).map((j) => j.at), pins: [], clips: [] });
    const landed = snap(6.02, targets, 8 / 40);
    expect(landed).toEqual({ t: 6, snapped: 6 });
    const piece = list[1];
    const trimmed = trimPiece(keep, 1, "in", piece.sourceStart + (landed.t - piece.start), 60);
    expect(trimmed).toEqual([[0, 10], [10, 30]]);
    const at = round3(piece.start + (trimmed[1][0] - piece.sourceStart));
    expect(at).toBe(8);
    expect(caughtAfterClamp(at, landed.snapped)).toBeNull();
    // The same walk toward a candidate the edge really reaches keeps its ⌖.
    const near = snap(9.03, [9], 8 / 40);
    const toNine = trimPiece(keep, 1, "in", piece.sourceStart + (near.t - piece.start), 60);
    expect(caughtAfterClamp(round3(piece.start + (toNine[1][0] - piece.sourceStart)), near.snapped)).toBe(9);
    // ... and the floor does the same: a candidate past the last frame while
    // shortening (0.01, with the edge held at 0.033) is not claimed.
    const floor = trimPiece(keep, 0, "out", 0.01, 60);
    expect(floor[0]).toEqual([0, round3(FRAME_SECONDS)]);
    expect(caughtAfterClamp(round3(floor[0][1]), snap(0.01, [0.01], 8 / 40).snapped)).toBeNull();
  });
});

describe("trimLabel — what the drag says", () => {
  it("says what is removed, what comes back, and why the edge will go no further", () => {
    expect(trimLabel({ kind: "cut", a: 6, b: 7.2 }, false)).toBe("−1.200 s");
    expect(trimLabel({ kind: "cut", a: 6, b: 6.033 }, false)).toBe("−0.033 s");
    expect(trimLabel({ kind: "insert", t: 6, d: 0.8 }, false)).toBe("+0.800 s restored");
    expect(trimLabel({ kind: "cut", a: 6, b: 11.967 }, true)).toBe("one frame — use Cut to remove it");
    expect(trimLabel(null, true)).toBe("one frame — use Cut to remove it");
    expect(trimLabel(null, false)).toBe("no change");
  });
});

describe("the markers — named moments of the picture's SOURCE (E5b, spec §13.2)", () => {
  // The fixture's cut: 6.0–7.5 of a 12 s source removed, so the output is
  // 10.5 s and output 8 is source 9.5; the join at output 6 is source 7.5
  // (the later range wins, as `toSource` chooses).
  const SOURCE = 12;
  const CUT: Keep = [[0, 6], [7.5, 12]];
  const marker = (over: Partial<Marker> = {}): Marker => ({ id: "k1", at: 2, name: "Intro", ...over });
  const drawnAt = (id: string, timeline_at: number): DrawnMarker => ({ id, at: timeline_at, name: id, timeline_at });

  it("mints an id under the server's rule, never one already in the list", () => {
    const id = mintMarkerId(["k1"], () => 0.5);
    expect(id).toMatch(/^[a-z0-9_-]{1,32}$/);
    expect(id.startsWith("k")).toBe(true);
    // A random that never moves: the mint still answers with a free id.
    const stuck = mintMarkerId([mintMarkerId([], () => 0.25)], () => 0.25);
    expect(stuck).not.toBe(mintMarkerId([], () => 0.25));
    expect(stuck).toMatch(/^[a-z0-9_-]{1,32}$/);
  });

  it("trims a name to the server's rule, and an empty one is nothing", () => {
    expect(markerName("  Intro  ")).toBe("Intro");
    expect(markerName("   ")).toBe("");
    expect(markerName("x".repeat(100))).toHaveLength(MAX_MARKER_NAME);
    expect(markerName(`${"x".repeat(79)} y`)).toBe(`${"x".repeat(79)}`);
  });

  it("strips the control characters a paste can carry before the server sees the name", () => {
    expect(sanitizeMarkerName("In\ttro\n")).toBe("Intro");
    expect(sanitizeMarkerName("a\r\nb\u0000c\u007fd")).toBe("abcd");
    expect(sanitizeMarkerName("  \t  ")).toBe("");
    // Then the same trim and cap as `markerName`; anything printable rides through.
    expect(sanitizeMarkerName("  Q&A: très bien — 日本 ✓  ")).toBe("Q&A: très bien — 日本 ✓");
    expect(sanitizeMarkerName(`\t${"x".repeat(100)}`)).toHaveLength(MAX_MARKER_NAME);
  });

  it("draws only the markers the picture's list projects, in order", () => {
    const list = [marker({ id: "a", timeline_at: 1 }), marker({ id: "b", timeline_at: null }), marker({ id: "c" }), marker({ id: "d", timeline_at: 7.5 })];
    expect(drawnMarkers(list).map((held) => held.id)).toEqual(["a", "d"]);
    expect(drawnMarkers([])).toEqual([]);
  });

  it("sorts by `at`, stably, into a new array", () => {
    const list = [marker({ id: "b", at: 5 }), marker({ id: "a", at: 1 }), marker({ id: "c", at: 5 })];
    const sorted = sortMarkers(list);
    expect(sorted.map((held) => held.id)).toEqual(["a", "b", "c"]);
    expect(sorted).not.toBe(list);
    expect(sorted[0]).toBe(list[1]);
  });

  it("drops a marker at the playhead's SOURCE moment, named Marker N, drawn where it was dropped", () => {
    expect(newMarker([marker()], 8, CUT, SOURCE, () => "k9")).toEqual({ id: "k9", at: 9.5, name: "Marker 2", timeline_at: 8 });
    // At a join the later range wins, so the marker sits on the first kept
    // frame after the cut, never in the removed stretch.
    expect(newMarker([], 6, CUT, SOURCE, () => "k9")).toEqual({ id: "k9", at: 7.5, name: "Marker 1", timeline_at: 6 });
    // Inside the source at either end.
    expect(newMarker([], 99, CUT, SOURCE, () => "k9")?.at).toBe(12);
    expect(newMarker([], -1, CUT, SOURCE, () => "k9")?.at).toBe(0);
    // A whole picture: the source moment is the output moment.
    expect(newMarker([], 3.2, wholeKeep(SOURCE), SOURCE, () => "k9")).toEqual({ id: "k9", at: 3.2, name: "Marker 1", timeline_at: 3.2 });
    // The mint is handed every id in the list.
    const taken: string[][] = [];
    newMarker([marker({ id: "a" }), marker({ id: "b" })], 1, CUT, SOURCE, (ids) => { taken.push(ids); return "z"; });
    expect(taken).toEqual([["a", "b"]]);
    // The cap: nothing, which the caller says rather than sending 201.
    const full = Array.from({ length: MAX_MARKERS }, (_, i) => marker({ id: `k${i}`, at: i / 100 }));
    expect(newMarker(full, 1, CUT, SOURCE)).toBeNull();
    expect(newMarker(full.slice(1), 1, CUT, SOURCE)).not.toBeNull();
  });

  it("renames through the server's rule, and hands the same list back when nothing changes", () => {
    const list = [marker(), marker({ id: "k2", at: 5, name: "Two" })];
    const next = renameMarker(list, "k2", "  Second  ");
    expect(next[1]).toEqual({ id: "k2", at: 5, name: "Second" });
    expect(next[0]).toBe(list[0]);
    expect(renameMarker(list, "k2", "   ")).toBe(list);
    expect(renameMarker(list, "k2", "Two")).toBe(list);
    expect(renameMarker(list, "zz", "x")).toBe(list);
    expect(renameMarker(list, "k1", "x".repeat(100))[0].name).toHaveLength(MAX_MARKER_NAME);
  });

  it("moves a marker by its OUTPUT moment into a SOURCE moment through the picture's list, and re-sorts", () => {
    const list = [marker({ id: "a", at: 1, timeline_at: 1 }), marker({ id: "b", at: 9, timeline_at: 7.5 })];
    // Output 8 is source 9.5: `a` moves past `b`, and the read-back's projection moves with it.
    const next = moveMarker(list, "a", 8, CUT, SOURCE);
    expect(next.map((held) => held.id)).toEqual(["b", "a"]);
    expect(next[1]).toEqual({ id: "a", at: 9.5, name: "Intro", timeline_at: 8 });
    expect(next[0]).toBe(list[1]);
    // A join: output 6 is source 7.5, never 6 — a marker cannot land in removed picture.
    expect(moveMarker(list, "a", 6, CUT, SOURCE).find((held) => held.id === "a")?.at).toBe(7.5);
    // Clamped to the source at both ends.
    expect(moveMarker(list, "a", 99, CUT, SOURCE).find((held) => held.id === "a")?.at).toBe(12);
    expect(moveMarker(list, "b", -3, CUT, SOURCE).find((held) => held.id === "b")?.at).toBe(0);
    // Nothing moved, or no such marker: the same list.
    expect(moveMarker(list, "a", 1, CUT, SOURCE)).toBe(list);
    expect(moveMarker(list, "zz", 3, CUT, SOURCE)).toBe(list);
  });

  it("deletes one by id and leaves the rest the same objects; a stale id changes nothing", () => {
    const list = [marker({ id: "a" }), marker({ id: "b", at: 3 }), marker({ id: "c", at: 4 })];
    const next = markersAfterDelete(list, "b");
    expect(next.map((held) => held.id)).toEqual(["a", "c"]);
    expect(next[0]).toBe(list[0]);
    expect(next[1]).toBe(list[2]);
    expect(markersAfterDelete(list, "zz")).toBe(list);
    expect(markersAfterDelete([], "a")).toEqual([]);
  });

  it("finds the previous and the next drawn marker strictly either side of the playhead", () => {
    const drawn = [drawnAt("c", 8), drawnAt("a", 1), drawnAt("b", 4)];
    expect(markerNeighbours(drawn, 5)).toEqual({ previous: 4, next: 8 });
    // Sitting ON a marker: strictly before and after, so Ctrl+] moves on rather than staying.
    expect(markerNeighbours(drawn, 4)).toEqual({ previous: 1, next: 8 });
    expect(markerNeighbours(drawn, 4.0004)).toEqual({ previous: 1, next: 8 });
    expect(markerNeighbours(drawn, 0)).toEqual({ previous: null, next: 1 });
    expect(markerNeighbours(drawn, 9)).toEqual({ previous: 8, next: null });
    expect(markerNeighbours([], 3)).toEqual({ previous: null, next: null });
  });

  it("sends the three stored keys and never the read-back's timeline_at, and calls two lists the same in those terms", () => {
    expect(markersBody([marker({ timeline_at: 2, at: 2.00049, name: " Intro " })])).toEqual([{ id: "k1", at: 2, name: "Intro" }]);
    expect(sameMarkers([marker({ timeline_at: 2 })], [marker({ timeline_at: null })])).toBe(true);
    expect(sameMarkers([marker()], [marker({ name: "Other" })])).toBe(false);
    expect(sameMarkers([marker()], [marker({ at: 2.001 })])).toBe(false);
    expect(sameMarkers([marker()], [marker({ id: "k2" })])).toBe(false);
    expect(sameMarkers([], [marker()])).toBe(false);
    expect(sameMarkers([], [])).toBe(true);
  });

  it("rides in the body only when the operation changes them, as the music does (editBody)", () => {
    const held = [marker({ timeline_at: 2 })];
    const base = { video: CUT, narration: null, music: [] as MusicClip[] };
    expect("markers" in editBody({ ...base, markers: held }, { music: [], markers: held }, SOURCE)).toBe(false);
    expect(editBody({ ...base, markers: [] }, { music: [], markers: held }, SOURCE).markers).toEqual([]);
    expect(editBody({ ...base, markers: [marker({ name: "Renamed", timeline_at: 2 })] }, { music: [], markers: held }, SOURCE).markers)
      .toEqual([{ id: "k1", at: 2, name: "Renamed" }]);
    // The read-back's projection is not a change: a cut that hid a marker re-sends nothing.
    expect("markers" in editBody({ ...base, markers: [marker({ timeline_at: null })] }, { music: [], markers: held }, SOURCE)).toBe(false);
    // And a marker commit re-sends no clips.
    const body = editBody({ ...base, markers: [] }, { music: [], markers: held }, SOURCE);
    expect("music" in body).toBe(false);
  });

  it("joins the one snap candidate set as the drawn moments, deduplicated and sorted", () => {
    expect(snapTargets({ playhead: 3, duration: 10.5, joins: [6], pins: [], clips: [], markers: [8, 6, 1.0004] }))
      .toEqual([0, 1, 3, 6, 8, 10.5]);
    // Without the key the set is E5a's.
    expect(snapTargets({ playhead: 3, duration: 10.5, joins: [], pins: [], clips: [] })).toEqual([0, 3, 10.5]);
  });

  it("treats a flag's unmoved release as a click that selects, letting the click through", () => {
    expect(unmovedRelease("marker", false, false)).toBe("click");
    expect(unmovedRelease("marker", true, false)).toBe("click");
    expect(unmovedRelease("marker", false, true)).toBe("nothing");
    expect(releaseSuppressesClick("marker", false)).toBe(false);
    expect(releaseSuppressesClick("marker", true)).toBe(true);
  });
});
