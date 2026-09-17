/**
 * The edit on the client: the projection between SOURCE seconds (the one
 * video, the transcript on disk, the waveform) and TIMELINE seconds (the
 * output, with the removed ranges closed up), the ripple delete, and the
 * ruler and zoom arithmetic the timeline draws with. All pure; unit-tested in
 * edit.test.ts.
 *
 * The projection — `outputDuration`, `toTimeline`, `toSource`, `wholeSource` —
 * is a COPY of `services/edit.py`, name for name and rule for rule, and the
 * one duplication the porting spec accepts (docs/porting/edit-timeline.md §3):
 * the strip seeks and draws on every pointer movement, and a round trip to the
 * server for each is not an option. Both copies are pinned by ONE fixture,
 * `tests/fixtures/edit_projection.json`, read by `tests/test_edit.py` and by
 * edit.test.ts, so they cannot drift silently. Everything else the edit
 * decides — which sentences are spoken and where they land — is the plan's
 * answer (`GET /api/projects/{pid}/narration/plan`), never re-derived here.
 *
 * Nothing here moves the transcript: the client only ever sends `keep`.
 */

import { timecode } from "./format";
import { clampTime } from "./timeline";

/** The kept ranges of the source, `[start, end]` in SOURCE seconds, ascending and disjoint. */
export type Keep = [number, number][];

/** Seconds are stored to three decimals, the transcript's own precision. */
export const PRECISION = 3;
/**
 * Half a millisecond: the widest gap two 3-dp seconds can differ by and still
 * be the same instant after rounding (`services/edit.py::EPSILON`).
 */
export const EPSILON = 0.0005;
/** One second is about this many pixels at full zoom. */
export const MAX_PPS = 200;
/**
 * A "frame" for Comma / Period. The source's frame rate is not known without
 * ffprobe, which the packaged app does not have (spec trap 2), so a frame is
 * a thirtieth of a second — right for the screen recordings this is built
 * for and a fixed, honest step for everything else.
 */
export const FRAME_SECONDS = 1 / 30;
/** How many edits the client-side undo stack remembers. */
export const UNDO_DEPTH = 50;

/**
 * To the stored precision. `+ 0` turns the `-0` that a value like -0.0004
 * rounds to into a plain 0, as the server's `+ 0.0` does, so a range never
 * starts at "-0". This is `Math.round(x × 1000) / 1000`, and it differs from
 * Python's `round(x, 3)` on DECIMAL half-milliseconds (0.0055 → 0.006 here,
 * 0.005 there: Python rounds the exact decimal value half-to-even, this
 * rounds the scaled float half-up) - immaterial in practice, and proven so
 * by the Reviewer: no difference on 300,000 pointer-like and random values,
 * and the server's rounding of every client-rounded value is the identity,
 * so what is sent is what is stored.
 */
export function round3(seconds: number): number {
  return Math.round(seconds * 1000) / 1000 + 0;
}

// ── the projection: services/edit.py, copied ────────────────────────────────

/** How long the output is: the sum of the kept ranges. */
export function outputDuration(keep: Keep): number {
  let sum = 0;
  for (const [start, end] of keep) sum += end - start;
  return round3(sum);
}

/**
 * Where a source moment lands in the output, or `null` when it falls in a
 * removed range (or outside the source altogether). A moment exactly on a cut
 * belongs to the join: the end of one kept range and the start of the next
 * are the same output instant, so both map to it.
 */
export function toTimeline(tSource: number, keep: Keep): number | null {
  let at = 0;
  for (const [start, end] of keep) {
    if (start <= tSource && tSource <= end) return round3(at + (tSource - start));
    at += end - start;
  }
  return null;
}

/**
 * The inverse: which source moment is showing at an output moment — for the
 * playhead and the filmstrip, which seek the one source video.
 *
 * At a join the LATER range wins: the earlier range's end is the first frame
 * the output never shows (ffmpeg's `trim` end is exclusive), so a filmstrip or
 * a playhead seeked there would show a frame that was cut. `toTimeline` still
 * maps both sides of a cut to the join, so the round trip holds. Clamped
 * rather than refused at either end, because a playhead sits at
 * `outputDuration` (give or take a float) when playback finishes and must
 * show the last kept frame.
 */
export function toSource(tTimeline: number, keep: Keep): number {
  let found: number | null = null;
  let at = 0;
  for (const [start, end] of keep) {
    // The last match is the later range.
    if (at <= tTimeline && tTimeline <= at + (end - start)) found = round3(start + (tTimeline - at));
    at += end - start;
  }
  if (found !== null) return found;
  if (keep.length === 0) return 0;
  return tTimeline < 0 ? keep[0][0] : keep[keep.length - 1][1];
}

/**
 * Whether `keep` covers everything — an edit that removes nothing (a bare
 * split, or a list written back from a rounded display). The render then
 * skips the picture step. `keep` is assumed valid, so covering everything is
 * the same as adding up to the source's length.
 */
export function wholeSource(keep: Keep, sourceDuration: number): boolean {
  return outputDuration(keep) >= round3(sourceDuration) - EPSILON;
}

/** What "no edit" means to the drawing: one range, the whole source. */
export function wholeKeep(sourceDuration: number): Keep {
  return [[0, Math.max(0, round3(sourceDuration))]];
}

// ── the joins ───────────────────────────────────────────────────────────────

export interface Join {
  /** Where the join sits in the output, timeline seconds. */
  at: number;
  /** How much of the source was removed here; 0 for a bare split. */
  removed: number;
  /** The removed stretch of the source, from the end of one kept range to the start of the next. */
  from: number;
  to: number;
}

/**
 * Every place the output has a cut behind it: between consecutive ranges, at
 * 0 when the head of the source was removed, at the end when its tail was. A
 * touching pair — a split — is a join with nothing removed. Drawn as the thin
 * marker the owner asked for (spec §10.2): Camtasia shows nothing at a closed
 * cut, and nothing is how a mistake goes unnoticed.
 */
export function joins(keep: Keep, sourceDuration: number): Join[] {
  const out: Join[] = [];
  if (keep.length === 0) return out;
  if (keep[0][0] > EPSILON) out.push({ at: 0, removed: round3(keep[0][0]), from: 0, to: keep[0][0] });
  let at = 0;
  for (let i = 0; i < keep.length; i++) {
    const [start, end] = keep[i];
    at += end - start;
    if (i + 1 < keep.length) {
      const next = keep[i + 1][0];
      out.push({ at: round3(at), removed: round3(next - end), from: end, to: next });
    }
  }
  const last = keep[keep.length - 1][1];
  if (sourceDuration - last > EPSILON) {
    out.push({ at: round3(at), removed: round3(sourceDuration - last), from: last, to: sourceDuration });
  }
  return out;
}

/** The join marker's tooltip. */
export function describeJoin(join: Join): string {
  if (join.removed <= 0) return "Split — nothing removed.";
  const seconds = join.removed >= 1 ? join.removed.toFixed(1) : join.removed.toFixed(3);
  return `${seconds} s removed (${timecode(join.from)} – ${timecode(join.to)} of the source)`;
}

// ── the ripple delete ───────────────────────────────────────────────────────

/**
 * Remove the TIMELINE interval `[a, b]` from the output and close the gap —
 * Camtasia's ripple delete, and the only edit E2 makes. Per kept range, its
 * timeline interval is intersected with `[a, b]` and the intersection is
 * subtracted in source coordinates, so an interval spanning an existing join
 * takes from both neighbours. Pieces left empty are dropped; the rest are
 * rounded to the stored precision so the list can go straight into
 * `PUT /edit`. `null` when nothing would be left, which the UI refuses
 * ("keep at least one range") rather than sends.
 *
 * A zero-length interval strictly inside a range is a bare split — two
 * touching ranges — which is how E3's split-at-the-playhead will be stored;
 * the E2 gesture never makes one.
 */
export function removeRange(keep: Keep, a: number, b: number): Keep | null {
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const out: Keep = [];
  let at = 0;
  for (const [start, end] of keep) {
    const length = end - start;
    const cutFrom = Math.max(at, lo);
    const cutTo = Math.min(at + length, hi);
    if (cutFrom > cutTo) {
      out.push([round3(start), round3(end)]);
    } else {
      const pieces: [number, number][] = [
        [start, start + (cutFrom - at)],
        [start + (cutTo - at), end],
      ];
      for (const [s, e] of pieces) {
        // Rounded BEFORE the emptiness test: two moments a hair apart can
        // round to the same millisecond, and [x, x] is a range the server
        // refuses.
        const piece: [number, number] = [round3(s), round3(e)];
        if (piece[1] - piece[0] > EPSILON) out.push(piece);
      }
    }
    at += length;
  }
  return out.length > 0 ? out : null;
}

/**
 * Where the playhead should be after a commit: the same SOURCE moment in the
 * new edit. When that moment was just removed, the join the hole closed to —
 * the end of the last kept range before it, in timeline seconds (0 when it
 * was before the first). Halting at zero instead, as a plan change used to,
 * is what made "cut, listen, cut" into "cut, scroll back, listen".
 */
export function positionAfterEdit(t: number, oldKeep: Keep, newKeep: Keep): number {
  if (newKeep.length === 0) return 0;
  const source = toSource(t, oldKeep);
  const landed = toTimeline(source, newKeep);
  if (landed !== null) return landed;
  let at = 0;
  for (const [start, end] of newKeep) {
    if (source < start) return round3(at);
    at += end - start;
  }
  return round3(at);
}

// ── the waveform in timeline seconds ────────────────────────────────────────

/**
 * The waveform's peaks with the removed ranges cut out, so `poolPeaks` over
 * the result lines up with the projected blocks. `GET /waveform` stays in
 * source seconds (it is one WAV file, cached for ever); a bucket is
 * `bucketSeconds` of that file, so keeping ranges is an array operation.
 *
 * Sliced by CUMULATIVE OUTPUT bucket indices, not per range: range k, which
 * begins at timeline `at_k`, fills output buckets `round(at_k/b) …
 * round((at_k + len_k)/b)` (exclusive), slot j taking the source bucket
 * `round((start_k − at_k)/b) + j`. The result is then exactly
 * `round(outputDuration/b)` long and each boundary is off by at most half a
 * bucket - rounding every range's span on its own let the error accumulate
 * to several buckets after a few cuts (0.4 s at the zoom ceiling: a burst
 * visibly beside its sentence). Each slot is clamped to the source buckets
 * that overlap its own range, so a bucket from inside a hole is never shown
 * (a boundary that rounds outward repeats the range's edge bucket instead),
 * and a range running past the array's end repeats its last bucket rather
 * than reading undefined.
 */
export function projectPeaks(peaks: number[], bucketSeconds: number, keep: Keep): number[] {
  if (peaks.length === 0 || !(bucketSeconds > 0)) return [];
  const out: number[] = [];
  let at = 0;
  for (const [start, end] of keep) {
    const length = end - start;
    const first = Math.round(at / bucketSeconds);
    const last = Math.round((at + length) / bucketSeconds);
    const lo = Math.max(0, Math.floor(start / bucketSeconds));
    const hi = Math.min(peaks.length - 1, Math.ceil(end / bucketSeconds) - 1);
    const offset = Math.round((start - at) / bucketSeconds);
    for (let j = first; j < last; j++) {
      out.push(peaks[Math.min(hi, Math.max(lo, offset + j))]);
    }
    at += length;
  }
  return out;
}

// ── the ruler ───────────────────────────────────────────────────────────────

/** The label steps the ruler may use, each with the minor tick between its labels — seconds. */
const TICK_STEPS: ReadonlyArray<readonly [number, number]> = [
  [0.1, 0.02], [0.2, 0.05], [0.5, 0.1], [1, 0.2], [2, 0.5], [5, 1], [10, 2], [15, 5],
  [30, 5], [60, 10], [120, 30], [300, 60], [600, 120], [1800, 300], [3600, 600], [7200, 1800],
];

/**
 * A ruler label: whole seconds as `m:ss` when the labels are a second or more
 * apart - `1:05`, not `1:05.000` - and the full timecode below that, where
 * the milliseconds are the point.
 */
export function rulerLabel(at: number, step: number): string {
  if (step < 1) return timecode(at);
  const total = Math.max(0, Math.round(at));
  const m = Math.floor(total / 60);
  return `${m}:${String(total - m * 60).padStart(2, "0")}`;
}

function tickPair(pps: number, minLabelPx: number): readonly [number, number] {
  for (const pair of TICK_STEPS) if (pair[0] * pps >= minLabelPx) return pair;
  return TICK_STEPS[TICK_STEPS.length - 1];
}

/**
 * The seconds between two labels on the ruler: the smallest step whose labels
 * sit at least `minLabelPx` apart at this scale, so they never collide.
 */
export function tickStep(pps: number, minLabelPx = 80): number {
  return tickPair(pps, minLabelPx)[0];
}

export interface Tick {
  /** Timeline seconds. */
  at: number;
  /** The timecode, on a major tick only. */
  label: string;
  major: boolean;
}

/**
 * The ruler's ticks between `from` and `to` seconds (the whole strip by
 * default): a labelled major tick every `tickStep`, minor ticks between. The
 * window exists because a zoomed two-hour strip has tens of thousands of
 * minor ticks and the ruler draws only the ones on screen.
 */
export function ticks(total: number, pps: number, from = 0, to = total): Tick[] {
  if (!(total > 0) || !(pps > 0)) return [];
  const [step, minor] = tickPair(pps, 80);
  const first = Math.max(0, Math.floor(from / minor));
  const last = Math.floor(Math.min(total, to) / minor + 1e-6);
  const out: Tick[] = [];
  for (let k = first; k <= last; k++) {
    const at = round3(k * minor);
    const major = Math.abs(at / step - Math.round(at / step)) < 1e-6;
    out.push({ at, label: major ? rulerLabel(at, step) : "", major });
  }
  return out;
}

// ── zoom ────────────────────────────────────────────────────────────────────

/**
 * The zoom at which one second is `MAX_PPS` pixels — the slider's top. Never
 * below 1 (fit), and a function of the strip's width, so a resize re-clamps
 * whatever zoom is set.
 */
export function maxZoom(total: number, width: number): number {
  if (!(total > 0) || !(width > 0)) return 1;
  return Math.max(1, (MAX_PPS * total) / width);
}

/**
 * The slider (0 = fit, 1 = the ceiling) to a zoom factor, logarithmically —
 * `max^v` — so the low end, where most editing happens, is not wasted on a
 * few pixels of travel.
 */
export function zoomFromSlider(v: number, max: number): number {
  if (!(max > 1)) return 1;
  return Math.pow(max, Math.min(1, Math.max(0, v)));
}

/** The inverse: `ln z / ln max`, 0 when there is no room to zoom. */
export function sliderFromZoom(z: number, max: number): number {
  if (!(max > 1)) return 0;
  return Math.min(1, Math.max(0, Math.log(Math.max(1, z)) / Math.log(max)));
}

/** One notch in or out: a quarter more or less, clamped to `[1, max]`. */
export function stepZoom(z: number, max: number, direction: 1 | -1): number {
  const next = direction > 0 ? z * 1.25 : z / 1.25;
  return Math.min(Math.max(1, max), Math.max(1, next));
}

/** The zoom at which the selection fills about nine tenths of the strip, clamped. */
export function zoomToSelection(sel: { start: number; end: number }, total: number, width: number): number {
  const length = Math.abs(sel.end - sel.start);
  if (!(length > 0) || !(total > 0) || !(width > 0)) return 1;
  return Math.min(maxZoom(total, width), Math.max(1, (0.9 * total) / length));
}

/**
 * The `scrollLeft` that keeps `anchorSeconds` at the same screen x through a
 * zoom change: the playhead for the slider and the keys, the pointer for
 * Ctrl+wheel. Never negative — the browser would clamp it, but a negative
 * value here would mean an anchor that cannot be kept.
 */
export function anchoredScrollLeft(anchorSeconds: number, anchorScreenPx: number, pps: number): number {
  return Math.max(0, anchorSeconds * pps - anchorScreenPx);
}

// ── the transport ───────────────────────────────────────────────────────────

/** Comma / Period: one frame back or forward, inside the audition. See `FRAME_SECONDS`. */
export function stepFrame(t: number, direction: 1 | -1, total: number, frame = FRAME_SECONDS): number {
  return clampTime(t + direction * frame, total);
}

// ── the Render button ───────────────────────────────────────────────────────

/**
 * About how long the render will take, in seconds, rounded up to five.
 *
 * The picture step's cost scales with the DECODE REACH — from the first kept
 * start to the last kept end — and never with the output's length: `trim` is
 * a filter and runs after the decode (E1's Reviewer proved it on real
 * footage; spec §7, second note). Measured 16.1 s for 341 s of reach on the
 * bundled ffmpeg, so `5 + reach × 0.05`. A keep that removes nothing skips
 * the picture step and estimates 0. Narration synthesis is not in the
 * number: the audition caches it, and the wording says so.
 */
export function renderEstimate(keep: Keep, sourceDuration: number): number {
  if (keep.length === 0 || wholeSource(keep, sourceDuration)) return 0;
  const reach = keep[keep.length - 1][1] - keep[0][0];
  return Math.ceil((5 + reach * 0.05) / 5) * 5;
}

/** The line under the Render button, or `null` when the edit removes nothing. */
export function renderSummary(keep: Keep, sourceDuration: number): string | null {
  if (keep.length === 0 || wholeSource(keep, sourceDuration)) return null;
  const cuts = joins(keep, sourceDuration).filter((join) => join.removed > 0).length;
  const removed = round3(sourceDuration) - outputDuration(keep);
  return `Cuts ${cuts} range${cuts === 1 ? "" : "s"} (${removed.toFixed(1)} s removed) and re-voices — about `
    + `${renderEstimate(keep, sourceDuration)} s, plus any sentences the audition has not fetched yet.`;
}
