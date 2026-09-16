/**
 * The narration timeline's pure helpers: seconds↔pixels, the waveform path, the
 * filmstrip's sample points, and — the one piece of render arithmetic that
 * legitimately lives on the client — where each synthesised clip actually lands.
 *
 * Everything the SERVER can answer is answered by the server
 * (`GET /api/projects/{pid}/narration/plan`, `services/narration.py`): what each
 * sentence says, how fast it will be spoken and in whose voice. Re-deriving any
 * of that here would be a second copy of the re-voice's rate rules, free to
 * drift from the render the timeline claims to be auditioning.
 *
 * `schedule()` is the exception, and it is one for a concrete reason: a pin is a
 * FLOOR, not a position, so where a clip lands depends on how long the clip
 * before it turned out to be — and only the browser, holding the decoded audio,
 * knows that. Unit-tested in timeline.test.ts; the page itself is verified in
 * the browser by the owner.
 */

/** One sentence of `GET /api/projects/{pid}/narration/plan`. */
export interface PlanSentence {
  index: number;
  text: string;
  /** Where the sentence was SPOKEN — what lines up with the waveform burst under it. */
  start: number;
  end: number;
  /** Where the render will pin it: `start` + its offset, floored at zero. */
  pinned_start: number;
  muted: boolean;
  /**
   * Whether the render will synthesise this sentence at all — the SERVER's
   * answer, never re-derived here. "Has words" is `str.strip()` there and would
   * be `String.trim()` here, and those are different character sets (U+001C–1F
   * strip but do not trim; U+FEFF trims but does not strip), so a pasted control
   * character would be auditioned by the browser and refused with a 400 by the
   * preview route — a per-sentence failure with no cause anyone could see.
   */
  speakable: boolean;
  /** Not speakable because its offset pins it at or past the end of the video, where the mux drops it. */
  past_end: boolean;
  /** The room the render measured for it: the time until the next sentence is due. */
  window: number;
  /** Whether the render may tempo-squeeze it into that room after synthesis. */
  squeezable: boolean;
  /** The EFFECTIVE speed and voice — what the render will really synthesise with. */
  speed: number;
  voice: string;
  /** Carries that effective pair, so the clip fetched is the clip the render reuses. */
  preview_url: string;
}

export interface NarrationPlan {
  duration: number;
  baseline_rate: number;
  /** The render's own tempo-squeeze constants — see `schedule`. */
  squeeze_tolerance: number;
  squeeze_max_factor: number;
  sentences: PlanSentence[];
}

/**
 * The render's post-synthesis tempo squeeze, as the plan reports it. Carried
 * from the server rather than written here as literals: they are
 * `_revoice_video`'s constants, and a second copy in TypeScript would be free
 * to drift from the loop it exists to predict.
 */
export interface Squeeze {
  tolerance: number;
  maxFactor: number;
}

/** `GET /api/projects/{pid}/waveform`. */
export interface WaveformPeaks {
  buckets: number;
  bucket_seconds: number;
  duration: number;
  sample_rate: number;
  /** One magnitude per bucket, 0–255. */
  peaks: number[];
}

/** Float noise: two times within a tenth of a millisecond are the same moment. */
const EPSILON = 1e-4;

/**
 * The react-query key prefix for a project's audition plan. A prefix, so a
 * saved adjustment can invalidate every (provider, voice, speed) variant of it
 * in one call: where a sentence is pinned, how fast it is spoken and in whose
 * voice are all the plan's answers.
 */
export const narrationPlanKey = (projectId: string) => ["narration-plan", projectId];

// ── seconds ↔ pixels ────────────────────────────────────────────────────────

/**
 * How many pixels one second of the recording occupies.
 *
 * At zoom 1 the whole recording fits the strip's own width; at zoom 2 it is
 * twice as wide and scrolls. A recording with no length (an audio file that
 * could not be read) would divide by zero, so it is treated as one second —
 * the strip then draws as a single empty block rather than as `Infinity`.
 */
export function pixelsPerSecond(duration: number, width: number, zoom = 1): number {
  const seconds = duration > 0 ? duration : 1;
  return (Math.max(0, width) * Math.max(zoom, 0.01)) / seconds;
}

export function secondsToPx(seconds: number, pixelsPerSecondValue: number): number {
  return seconds * pixelsPerSecondValue;
}

export function pxToSeconds(px: number, pixelsPerSecondValue: number): number {
  return pixelsPerSecondValue > 0 ? px / pixelsPerSecondValue : 0;
}

/** A time clamped into the recording — what a click on the strip means. */
export function clampTime(seconds: number, duration: number): number {
  if (!Number.isFinite(seconds)) return 0;
  return Math.min(Math.max(0, seconds), Math.max(0, duration));
}

// ── the waveform ────────────────────────────────────────────────────────────

/**
 * Max-pool `peaks` down to `width` values.
 *
 * The server returns a fixed 8 buckets a second (one cache entry per project,
 * whatever the strip's width), so the client reduces to its own pixel width —
 * which is exact and trivial. MAX, not mean: a strip drawn from averages
 * flattens speech into a uniform smear and the bursts the user is aiming a
 * sentence at disappear.
 *
 * Fewer buckets than pixels needs no work at all: the path is drawn into a
 * viewBox with `preserveAspectRatio="none"`, so the browser stretches it.
 */
export function poolPeaks(peaks: number[], width: number): number[] {
  const target = Math.max(1, Math.floor(width));
  if (peaks.length === 0) return [];
  if (target >= peaks.length) return peaks.slice();

  const pooled: number[] = new Array(target);
  for (let i = 0; i < target; i++) {
    const lo = Math.floor((i * peaks.length) / target);
    const hi = Math.max(lo + 1, Math.floor(((i + 1) * peaks.length) / target));
    let max = 0;
    for (let j = lo; j < hi; j++) if (peaks[j] > max) max = peaks[j];
    pooled[i] = max;
  }
  return pooled;
}

/**
 * ONE SVG path for the whole strip: a filled shape mirrored about the centre
 * line, drawn into `viewBox="0 0 <length> <height>"` with
 * `preserveAspectRatio="none"`.
 *
 * One path and not one rect per bucket — 2728 rects is 2728 DOM nodes that
 * re-layout on every resize — and not a canvas, which would need a ref, a
 * device-pixel-ratio dance and a resize observer to stay sharp. The path scales
 * with CSS for free.
 */
export function waveformPath(pooled: number[], height = 100): string {
  if (pooled.length === 0) return "";
  const mid = height / 2;
  const round = (n: number) => Math.round(n * 100) / 100;
  const tops: string[] = [];
  const bottoms: string[] = [];
  for (let i = 0; i < pooled.length; i++) {
    // A floor of half a pixel, so silence is a hairline rather than nothing at
    // all: a gap in the strip should read as quiet, not as missing data.
    const half = Math.max(0.5, (Math.min(255, Math.max(0, pooled[i])) / 255) * mid);
    tops.push(`L${i} ${round(mid - half)}`);
    bottoms.push(`L${i} ${round(mid + half)}`);
  }
  // Out along the top, back along the bottom. The underside is the same list of
  // points walked in reverse — NOT bucket i's height drawn at pixel n-1-i,
  // which was the first attempt and drew the lower half back to front.
  return `M0 ${mid}${tops.join("")}${bottoms.reverse().join("")}Z`;
}

// ── the filmstrip ───────────────────────────────────────────────────────────

/**
 * How many frames to grab for a strip `width` pixels across — roughly one every
 * 100 px, which is what the design calls for, capped so a deeply zoomed strip
 * does not ask the browser for two hundred seeks.
 */
export function thumbCount(width: number, perThumb = 100): number {
  if (!(width > 0)) return 0;
  return Math.min(40, Math.max(1, Math.round(width / perThumb)));
}

/**
 * The moments to seek to, one per thumbnail: the CENTRE of each slice the thumb
 * will cover, never 0 and never the very last frame — seeking to exactly the
 * duration is the seek browsers are most likely to refuse.
 */
export function frameTimes(duration: number, count: number): number[] {
  if (!(duration > 0) || count <= 0) return [];
  return Array.from({ length: count }, (_, i) => ((i + 0.5) / count) * duration);
}

/**
 * True when the strip has changed width enough to be worth grabbing new frames.
 * Regenerating on every pixel of a drag would seek the video continuously; the
 * thumbs stretch perfectly well in the meantime.
 */
export function needsNewFrames(current: number, generatedAt: number | null, tolerance = 120): boolean {
  if (generatedAt === null) return true;
  return Math.abs(current - generatedAt) > tolerance;
}

// ── where each clip actually lands ──────────────────────────────────────────

export interface ScheduledClip {
  index: number;
  /** Where this clip really starts — its pin, or wherever the previous one ended. */
  start: number;
  end: number;
  /** Where the render aims it. `start` is later than this when it was pushed. */
  pinned: number;
  /** True when the render will tempo-squeeze this clip to fit its window. */
  squeezed: boolean;
}

export interface Schedule {
  /** In play order: the sentences that will actually be heard. */
  clips: ScheduledClip[];
  /** Indices whose clip runs past the NEXT clip's pin, i.e. pushes it late. */
  overrunning: number[];
  /** Indices that could not start where they are pinned. The mirror of the above. */
  pushed: number[];
  /** Indices the render will tempo-squeeze to fit their window. */
  squeezed: number[];
  /** When the last clip finishes. */
  end: number;
}

/**
 * How long the render's clip for this sentence will be, given the length the
 * browser actually decoded.
 *
 * **The post-synthesis tempo squeeze.** `_revoice_video` speeds a clip up with
 * ffmpeg's atempo to fit its window exactly when it runs more than
 * `tolerance` over it, unless that would take more than `maxFactor` — past
 * which the speech stops being intelligible and the overrun is let through.
 * Modelling it here is not optional: without it the audition plays the long
 * clip, cascades every sentence after it, and shows the owner an overrun the
 * render will not produce — the exact opposite of what this view is for.
 *
 * Both of the render's guards are respected, and both arrive in the plan: a
 * sentence with an explicit per-sentence speed is never squeezed (the user
 * named the rate), and neither is one with no window.
 *
 * **The one approximation.** The render trims each clip's leading silence and
 * lifts its opening ramp BEFORE measuring it, and the browser holds the
 * untrimmed mp3 — so the length compared here is longer by that trim, which is
 * tens of milliseconds for Edge. Reproducing the trim would mean a second copy
 * of the provider's onset profile in TypeScript, which is worse. The effect is
 * to predict the squeeze a hair early, never late.
 */
export function renderedLength(sentence: PlanSentence, decoded: number, squeeze: Squeeze): {
  length: number; squeezed: boolean;
} {
  // Integer milliseconds, because that is what the render compares: pydub's
  // `len(clip)` is whole milliseconds and the window is `int(window_s * 1000)`.
  const windowMs = Math.trunc(sentence.window * 1000);
  const clipMs = Math.round(decoded * 1000);
  if (!sentence.squeezable || windowMs <= 0 || !(clipMs > windowMs * squeeze.tolerance)) {
    return { length: decoded, squeezed: false };
  }
  if (clipMs / windowMs > squeeze.maxFactor) {
    return { length: decoded, squeezed: false };  // beyond 2x it is let through
  }
  return { length: windowMs / 1000, squeezed: true };
}

/**
 * Where every clip lands, given the plan and the REAL decoded length of each
 * clip.
 *
 * **A pin is a floor, not a position.** `assemble_master` inserts silence only
 * when the gap to a chunk's pin is positive, so sentence *i* starts at
 * `max(pinned_start_i, end of the previous clip)`: an offset can always push a
 * sentence later, and can pull it earlier only as far as the previous
 * sentence's synthesised audio actually ends. The UI must say that rather than
 * promise frame-accurate placement — and the audition must PLAY it, or it is
 * advertising a render that will not happen.
 *
 * A sentence the server says is not `speakable` is skipped entirely and gives
 * its room to the sentence before it, exactly as the render drops it before any
 * window maths: muted, wordless, or pinned at or past the end of the video
 * (where `-shortest` cuts it out of the render altogether). So is a sentence
 * with no clip: one whose synthesis failed, and — while the clips are still
 * arriving — one that has not been fetched yet, which is what lets playback
 * start before the whole narration is ready.
 *
 * Each clip is placed at the length the RENDER will use, not the length the
 * browser decoded: see `renderedLength` for the tempo squeeze, which is the
 * thing that stops one long clip cascading into every sentence after it.
 *
 * The blocks are NEVER re-sorted when one is offset past its neighbour. The
 * pipeline does not re-sort either; they simply play consecutively, and the
 * overrun is marked instead. Silently reordering the script would be a far
 * worse answer than showing that two sentences collide.
 *
 * `overrunning` and `pushed` are two views of one fact (clip *i* overruns
 * exactly when clip *i+1* is pushed), computed once because the timeline marks
 * both ends of it: the culprit gets the overrun marker, the victim is drawn
 * away from its pin.
 */
export function schedule(
  sentences: PlanSentence[], clipSeconds: Record<number, number>, squeeze: Squeeze,
): Schedule {
  const clips: ScheduledClip[] = [];
  const squeezed: number[] = [];
  let cursor = 0;
  for (const sentence of sentences) {
    if (!sentence.speakable) continue;
    const decoded = clipSeconds[sentence.index];
    if (!(typeof decoded === "number" && decoded > 0)) continue;
    const fitted = renderedLength(sentence, decoded, squeeze);
    const pinned = Math.max(0, sentence.pinned_start);
    const start = Math.max(pinned, cursor);
    clips.push({ index: sentence.index, start, end: start + fitted.length, pinned, squeezed: fitted.squeezed });
    if (fitted.squeezed) squeezed.push(sentence.index);
    cursor = start + fitted.length;
  }

  const overrunning: number[] = [];
  const pushed: number[] = [];
  for (let i = 0; i < clips.length; i++) {
    if (clips[i].start > clips[i].pinned + EPSILON) pushed.push(clips[i].index);
    if (i + 1 < clips.length && clips[i].end > clips[i + 1].pinned + EPSILON) {
      overrunning.push(clips[i].index);
    }
  }
  return { clips, overrunning, pushed, squeezed, end: cursor };
}

/**
 * The clips still audible from `position` onwards, each with how far into it to
 * start — which is what a seek into the middle of a sentence needs, since a Web
 * Audio source node is one-shot and has to be created afresh with an offset.
 */
export function clipsFrom(clips: ScheduledClip[], position: number): { clip: ScheduledClip; offset: number }[] {
  return clips
    .filter((clip) => clip.end > position + EPSILON)
    .map((clip) => ({ clip, offset: Math.max(0, position - clip.start) }));
}

/** How long the audition runs: the picture, or the narration if it overruns it. */
export function auditionLength(duration: number, scheduleEnd: number): number {
  return Math.max(duration, scheduleEnd);
}
