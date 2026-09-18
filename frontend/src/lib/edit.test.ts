import { describe, it, expect } from "vitest";
// The SAME cases services/edit.py is pinned by (tests/test_edit.py reads this
// file too), so the two copies of the projection cannot drift silently.
import fixture from "../../../tests/fixtures/edit_projection.json";
import {
  EPSILON,
  FRAME_SECONDS,
  MAX_OFFSET,
  MAX_PPS,
  anchoredScrollLeft,
  clickSelectsPiece,
  describeJoin,
  dragOffsets,
  joins,
  maxZoom,
  nextEditForCut,
  nextEditForSplit,
  outputDuration,
  pieceAt,
  pieces,
  positionAfterEdit,
  projectPeaks,
  releaseSuppressesClick,
  removeRange,
  renderEstimate,
  renderSummary,
  round3,
  rulerLabel,
  sameEdit,
  sliderFromZoom,
  snap,
  splitAt,
  stepFrame,
  stepZoom,
  tickStep,
  ticks,
  toSource,
  toTimeline,
  trackBody,
  trackList,
  unmovedRelease,
  wholeKeep,
  wholeSource,
  zoomFromSlider,
  zoomToSelection,
  type Keep,
  type TrackEdit,
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
    // 341 s of reach measured 16.1 s on the bundled ffmpeg; 5 + 341 × 0.05 = 22.05, up to 25.
    expect(renderEstimate(CORPUS, 341.008)).toBe(25);
    // A short keep at the head decodes only that far, and one at the tail
    // only from its start (E1's input seek moves the origin)...
    expect(renderEstimate([[0, 10]], 341.008)).toBe(10);
    expect(renderEstimate([[331, 341.008]], 341.008)).toBe(10);
    // ... while a head plus a tail decodes everything between them.
    expect(renderEstimate([[0, 5], [336, 341.008]], 341.008)).toBe(25);
  });

  it("estimates 0 when nothing is cut — the picture step is skipped", () => {
    expect(renderEstimate([[0, 12]], 12)).toBe(0);
    expect(renderEstimate([[0, 6], [6, 12]], 12)).toBe(0);
    expect(renderEstimate([], 12)).toBe(0);
  });

  it("says what the render will do, per track, in one sentence", () => {
    // Cut together (the two lists equal): E2's line, word for word.
    expect(renderSummary(CORPUS, CORPUS, 341.008)).toBe(
      "Cuts 1 range (5.0 s removed) and re-voices — about 25 s, plus any sentences the audition has not fetched yet.",
    );
    expect(renderSummary([[2, 6], [7.5, 11]], [[2, 6], [7.5, 11]], 12)).toBe(
      "Cuts 3 ranges (4.5 s removed) and re-voices — about 10 s, plus any sentences the audition has not fetched yet.",
    );
    // The picture alone (the narration locked, or whole).
    expect(renderSummary(CORPUS, null, 341.008)).toBe(
      "Cuts 1 range of the picture (5.0 s removed) and re-voices — about 25 s, plus any sentences the audition has not fetched yet.",
    );
    expect(renderSummary(CORPUS, [[0, 341.008]], 341.008)).toBe(
      "Cuts 1 range of the picture (5.0 s removed) and re-voices — about 25 s, plus any sentences the audition has not fetched yet.",
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

  it("does nothing for a pointer the browser cancelled, whatever the kind", () => {
    for (const kind of ["scrub", "in", "out", "range", "move", "marquee"] as const) {
      expect(unmovedRelease(kind, true, true)).toBe("nothing");
    }
  });

  it("swallows the follow-up click after a real drag, a scrub or a marquee - never after an unmoved block or handle release", () => {
    for (const kind of ["scrub", "in", "out", "range", "move", "marquee"] as const) expect(releaseSuppressesClick(kind, true)).toBe(true);
    expect(releaseSuppressesClick("scrub", false)).toBe(true);
    expect(releaseSuppressesClick("marquee", false)).toBe(true);
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
