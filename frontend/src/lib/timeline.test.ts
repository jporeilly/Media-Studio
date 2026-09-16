import { describe, it, expect } from "vitest";
import {
  auditionLength,
  clampTime,
  clipsFrom,
  frameTimes,
  needsNewFrames,
  pixelsPerSecond,
  poolPeaks,
  pxToSeconds,
  renderedLength,
  schedule,
  secondsToPx,
  thumbCount,
  waveformPath,
  type PlanSentence,
  type Squeeze,
} from "./timeline";

/** The render's own squeeze constants, as the plan reports them. */
const SQUEEZE: Squeeze = { tolerance: 1.15, maxFactor: 2 };
/** ... and a squeeze that can never fire, for the tests that are not about it. */
const NO_SQUEEZE: Squeeze = { tolerance: Infinity, maxFactor: 2 };

/** A plan sentence with only the fields the scheduler reads spelled out. */
function sentence(index: number, pinned: number, extra: Partial<PlanSentence> = {}): PlanSentence {
  return {
    index,
    text: `Sentence ${index}.`,
    start: pinned,
    end: pinned + 2,
    pinned_start: pinned,
    muted: false,
    speakable: true,
    past_end: false,
    window: 0,
    squeezable: false,
    speed: 1,
    voice: "en-US-AriaNeural",
    preview_url: `/api/projects/p/transcript/${index}/preview`,
    ...extra,
  };
}

describe("seconds ↔ pixels", () => {
  it("fits the whole recording into the strip at zoom 1, and stretches it beyond", () => {
    expect(pixelsPerSecond(100, 800)).toBe(8);
    expect(pixelsPerSecond(100, 800, 2)).toBe(16);
    expect(secondsToPx(12.5, 8)).toBe(100);
    expect(pxToSeconds(100, 8)).toBe(12.5);
  });

  it("never divides by a zero duration", () => {
    // Audio that could not be read still has to draw as something.
    expect(Number.isFinite(pixelsPerSecond(0, 800))).toBe(true);
    expect(pxToSeconds(100, 0)).toBe(0);
  });

  it("clamps a click on the strip into the recording", () => {
    expect(clampTime(-4, 60)).toBe(0);
    expect(clampTime(90, 60)).toBe(60);
    expect(clampTime(12.5, 60)).toBe(12.5);
    expect(clampTime(NaN, 60)).toBe(0);
  });
});

describe("poolPeaks", () => {
  it("takes the loudest sample in each pixel, never the average", () => {
    // A strip drawn from averages smears speech flat and the bursts a sentence
    // is being aimed at disappear.
    expect(poolPeaks([0, 200, 0, 0, 10, 0, 0, 0], 4)).toEqual([200, 0, 10, 0]);
  });

  it("covers every bucket, so a loud one cannot fall between two pixels", () => {
    const peaks = Array.from({ length: 1000 }, (_, i) => (i === 737 ? 255 : 3));
    const pooled = poolPeaks(peaks, 97);
    expect(pooled).toHaveLength(97);
    expect(Math.max(...pooled)).toBe(255);
  });

  it("leaves the peaks alone when there are fewer than pixels — the SVG stretches them", () => {
    expect(poolPeaks([1, 2, 3], 900)).toEqual([1, 2, 3]);
    expect(poolPeaks([], 900)).toEqual([]);
  });
});

describe("waveformPath", () => {
  it("is ONE path mirrored about the centre line", () => {
    // Not 2728 rects and not a canvas: one node that scales with CSS.
    const d = waveformPath([255, 0], 100);
    expect(d.startsWith("M0 50")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
    expect(d).toContain("L0 0");    // full scale, top
    expect(d).toContain("L0 100");  // ... and its mirror
    expect(d.match(/L/g)).toHaveLength(4);  // two buckets, drawn out and back
  });

  it("draws silence as a hairline rather than as nothing at all", () => {
    // A gap in the strip should read as quiet, not as missing data.
    const d = waveformPath([0], 100);
    expect(d).toContain("L0 49.5");
    expect(d).toContain("L0 50.5");
  });

  it("is empty when there is nothing to draw", () => {
    expect(waveformPath([], 100)).toBe("");
  });
});

describe("the filmstrip", () => {
  it("asks for roughly one frame per 100px, and never hundreds of seeks", () => {
    expect(thumbCount(0)).toBe(0);
    expect(thumbCount(820)).toBe(8);
    expect(thumbCount(60)).toBe(1);
    expect(thumbCount(20000)).toBe(40);
  });

  it("samples the centre of each slice, never 0 and never the very last frame", () => {
    // Seeking to exactly the duration is the seek a browser is most likely to
    // refuse, and a thumb of the first frame tells you nothing about its slice.
    expect(frameTimes(100, 4)).toEqual([12.5, 37.5, 62.5, 87.5]);
    expect(frameTimes(0, 4)).toEqual([]);
    expect(frameTimes(100, 0)).toEqual([]);
  });

  it("only regrabs frames when the strip has really changed width", () => {
    // Regenerating per pixel of a drag would seek the video continuously; the
    // thumbs stretch perfectly well in the meantime.
    expect(needsNewFrames(800, null)).toBe(true);
    expect(needsNewFrames(840, 800)).toBe(false);
    expect(needsNewFrames(1000, 800)).toBe(true);
  });
});

describe("schedule — a pin is a FLOOR, not a position", () => {
  it("starts every sentence at its pin when nothing overruns", () => {
    const plan = [sentence(0, 0), sentence(1, 5), sentence(2, 10)];
    const result = schedule(plan, { 0: 1.5, 1: 1.5, 2: 1.5 }, NO_SQUEEZE);

    expect(result.clips.map((c) => c.start)).toEqual([0, 5, 10]);
    expect(result.clips.map((c) => c.end)).toEqual([1.5, 6.5, 11.5]);
    expect(result.overrunning).toEqual([]);
    expect(result.pushed).toEqual([]);
    expect(result.end).toBe(11.5);
  });

  it("pushes a sentence late when the one before it runs past its pin", () => {
    // ... and the sentence AFTER that is still pinned, so the error is absorbed
    // at the next real pause rather than accumulating.
    const plan = [sentence(0, 0), sentence(1, 5), sentence(2, 10)];
    const result = schedule(plan, { 0: 7, 1: 1.5, 2: 1.5 }, NO_SQUEEZE);

    expect(result.clips.map((c) => c.start)).toEqual([0, 7, 10]);
    expect(result.overrunning).toEqual([0]);
    expect(result.pushed).toEqual([1]);
  });

  it("cannot pull a sentence earlier than the previous one finishes speaking", () => {
    // The honest limit of a negative offset, and the thing the UI must say
    // rather than promising frame-accurate placement.
    const plan = [sentence(0, 0), sentence(1, 5, { pinned_start: 0.5 })];
    const result = schedule(plan, { 0: 4, 1: 1 }, NO_SQUEEZE);

    expect(result.clips[1].start).toBe(4);
    expect(result.clips[1].pinned).toBe(0.5);
    expect(result.pushed).toEqual([1]);
  });

  it("skips a muted sentence and gives its room to the one before it", () => {
    const plan = [sentence(0, 0), sentence(1, 5, { muted: true, speakable: false }), sentence(2, 10)];
    const result = schedule(plan, { 0: 8, 1: 3, 2: 1.5 }, NO_SQUEEZE);

    expect(result.clips.map((c) => c.index)).toEqual([0, 2]);
    // The muted sentence's room is the first one's: 8 s of speech from 0 does
    // not push the third, which is pinned at 10.
    expect(result.clips[1].start).toBe(10);
    expect(result.overrunning).toEqual([]);
  });

  it("skips a sentence with no clip, which is how playback can start early", () => {
    // A sentence still being fetched, one whose synthesis failed, and one with
    // no words in it are all the same thing here: nothing to play.
    const plan = [sentence(0, 0), sentence(1, 5), sentence(2, 10)];
    const result = schedule(plan, { 0: 1.5, 2: 1.5 }, NO_SQUEEZE);

    expect(result.clips.map((c) => c.index)).toEqual([0, 2]);
    expect(schedule(plan, { 0: 1.5, 1: 0, 2: 1.5 }, NO_SQUEEZE).clips.map((c) => c.index)).toEqual([0, 2]);
  });

  it("never re-sorts sentences offset past their neighbour — it marks the overlap", () => {
    // The pipeline does not re-sort either; silently reordering the script
    // would be a far worse answer than showing that two sentences collide.
    const plan = [sentence(0, 0, { pinned_start: 9 }), sentence(1, 5, { pinned_start: 1 })];
    const result = schedule(plan, { 0: 2, 1: 2 }, NO_SQUEEZE);

    expect(result.clips.map((c) => c.index)).toEqual([0, 1]);  // still in script order
    expect(result.clips.map((c) => c.start)).toEqual([9, 11]);
    expect(result.overrunning).toEqual([0]);
    expect(result.pushed).toEqual([1]);
  });

  it("floors a pin at zero and starts from zero", () => {
    const result = schedule([sentence(0, 0, { pinned_start: -3 })], { 0: 1 }, NO_SQUEEZE);
    expect(result.clips[0].start).toBe(0);
  });

  it("is empty when nothing can be played", () => {
    expect(schedule([], {}, NO_SQUEEZE)).toEqual({ clips: [], overrunning: [], pushed: [], squeezed: [], end: 0 });
    expect(schedule([sentence(0, 0, { muted: true, speakable: false })], { 0: 2 }, NO_SQUEEZE).clips).toEqual([]);
  });

  it("does not call a rounding error an overrun", () => {
    const plan = [sentence(0, 0), sentence(1, 5)];
    const result = schedule(plan, { 0: 5.00001, 1: 1 }, NO_SQUEEZE);
    expect(result.overrunning).toEqual([]);
    expect(result.pushed).toEqual([]);
  });
});

describe("schedule — the render's own tempo squeeze", () => {
  // Without this the audition plays the long clip and cascades everything after
  // it, showing an overrun the render will never produce — the exact opposite
  // of what this view exists for.
  const crowded = (extra: Partial<PlanSentence> = {}) =>
    sentence(0, 0, { window: 4, squeezable: true, ...extra });

  it("leaves a clip alone until it runs more than the tolerance over its window", () => {
    // 4 s of room. 4.5 s is only 12.5% over, and the render lets that through:
    // Whisper often reports one sentence ending exactly where the next begins,
    // so a tight bound would tempo-adjust nearly every clip and the differing
    // factors would be audible as a wobble.
    expect(renderedLength(crowded(), 4.5, SQUEEZE)).toEqual({ length: 4.5, squeezed: false });
    expect(renderedLength(crowded(), 4.0, SQUEEZE)).toEqual({ length: 4, squeezed: false });
  });

  it("fits a clip that genuinely overruns into exactly its window", () => {
    expect(renderedLength(crowded(), 6, SQUEEZE)).toEqual({ length: 4, squeezed: true });
  });

  it("lets an overrun beyond the maximum factor through instead of mangling it", () => {
    // Past 2x the speech stops being intelligible, so the render logs it and
    // leaves it long — and the timeline must show that it really does run over.
    expect(renderedLength(crowded(), 9, SQUEEZE)).toEqual({ length: 9, squeezed: false });
  });

  it("never squeezes a sentence that carries its own speed", () => {
    // The user named the rate; tempo-adjusting it afterwards would undo the one
    // thing they asked for, in the render and therefore here.
    expect(renderedLength(crowded({ squeezable: false }), 9, SQUEEZE))
      .toEqual({ length: 9, squeezed: false });
  });

  it("never squeezes a sentence with no window", () => {
    expect(renderedLength(crowded({ window: 0 }), 9, SQUEEZE)).toEqual({ length: 9, squeezed: false });
  });

  it("stops a squeezed clip from cascading into every sentence after it", () => {
    // THE case the reviewer found: three crowded sentences 4 s apart, each
    // synthesising to 6 s. The render squeezes each into its 4 s window and
    // nothing moves. Unmodelled, the audition reported two overruns and drifted
    // 6 s by the third sentence.
    const plan = [
      sentence(0, 0, { window: 4, squeezable: true }),
      sentence(1, 4, { window: 4, squeezable: true }),
      sentence(2, 8, { window: 4, squeezable: true }),
    ];
    const result = schedule(plan, { 0: 6, 1: 6, 2: 6 }, SQUEEZE);

    expect(result.clips.map((c) => c.start)).toEqual([0, 4, 8]);
    expect(result.squeezed).toEqual([0, 1, 2]);
    expect(result.overrunning).toEqual([]);
    expect(result.pushed).toEqual([]);
    expect(result.end).toBe(12);
  });

  it("still reports the overrun the render really will produce", () => {
    // Beyond 2x is not squeezed, so this one genuinely pushes its neighbour.
    const plan = [
      sentence(0, 0, { window: 4, squeezable: true }),
      sentence(1, 4, { window: 4, squeezable: true }),
    ];
    const result = schedule(plan, { 0: 9, 1: 1 }, SQUEEZE);

    expect(result.squeezed).toEqual([]);
    expect(result.overrunning).toEqual([0]);
    expect(result.clips[1].start).toBe(9);
  });
});

describe("schedule — what the render will not speak", () => {
  it("skips a sentence the server says is not speakable, whatever the reason", () => {
    // Muted, wordless, or pinned at or past the end of the video — where
    // -shortest cuts it out of the render altogether. The client never
    // re-derives any of those: strip() and trim() are different character sets.
    const plan = [
      sentence(0, 0),
      sentence(1, 5, { speakable: false, past_end: true }),
      sentence(2, 10),
    ];
    const result = schedule(plan, { 0: 1, 1: 3, 2: 1 }, NO_SQUEEZE);

    expect(result.clips.map((c) => c.index)).toEqual([0, 2]);
    expect(result.end).toBe(11);
  });

  it("does not stretch the audition to cover a sentence pinned past the end", () => {
    const plan = [sentence(0, 0), sentence(1, 90, { speakable: false, past_end: true })];
    const result = schedule(plan, { 0: 2, 1: 2 }, NO_SQUEEZE);

    expect(auditionLength(60, result.end)).toBe(60);
  });
});

describe("clipsFrom — seeking", () => {
  const plan = [sentence(0, 0), sentence(1, 5), sentence(2, 10)];
  const { clips } = schedule(plan, { 0: 2, 1: 2, 2: 2 }, NO_SQUEEZE);

  it("takes everything still to be heard, from the top", () => {
    expect(clipsFrom(clips, 0).map((c) => c.clip.index)).toEqual([0, 1, 2]);
    expect(clipsFrom(clips, 0).map((c) => c.offset)).toEqual([0, 0, 0]);
  });

  it("starts part way into the sentence a seek lands inside", () => {
    // A Web Audio source node is one-shot, so a seek has to build new ones —
    // and the one straddling the seek point needs an offset into its buffer.
    const from = clipsFrom(clips, 5.75);
    expect(from.map((c) => c.clip.index)).toEqual([1, 2]);
    expect(from[0].offset).toBeCloseTo(0.75);
    expect(from[1].offset).toBe(0);
  });

  it("drops what has already finished, and everything past the end", () => {
    expect(clipsFrom(clips, 7).map((c) => c.clip.index)).toEqual([2]);
    expect(clipsFrom(clips, 99)).toEqual([]);
  });
});

describe("auditionLength", () => {
  it("runs to whichever of the picture and the narration ends later", () => {
    // The render trims the narration at the video's end (-shortest), but the
    // audition should still let you HEAR that it overran.
    expect(auditionLength(60, 45)).toBe(60);
    expect(auditionLength(60, 71.5)).toBe(71.5);
  });
});
