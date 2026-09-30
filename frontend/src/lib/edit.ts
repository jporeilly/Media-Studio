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
 * Since E3 the edit is one list PER TRACK (spec §11): the video list is the
 * picture's axis and the narration list the sentences', both laid out from
 * the same zero, so the one-list arithmetic here is applied to each in turn
 * and never to the wrong one (trap 18). The split, the pieces, the snap and
 * the drag's arithmetic live here too.
 *
 * Nothing here moves the transcript: the client only ever sends the lists,
 * and a drag sends `offset` (trap 20).
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
 * A "frame" for Comma / Period. The timeline never probes the source for its
 * real frame rate — it answers instantly from what it already holds (spec
 * trap 2) — so a frame is a thirtieth of a second: right for the screen
 * recordings this is built for and a fixed, honest step for everything else.
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
 * Split at the playhead (Camtasia's `S`): a boundary in the list at
 * `toSource(tTimeline)` — `[s, e]` becomes `[s, x], [x, e]` — which the
 * server accepts as touching ranges and `wholeSource` reads as removing
 * nothing, so a split changes no output (trap 23); its value is the PIECES
 * it makes. The same list as `removeRange(keep, t, t)`; the name says what
 * the gesture means. A no-op on an existing boundary and at either end of a
 * range, so the caller can tell "nothing to commit" by comparing lists.
 */
export function splitAt(keep: Keep, tTimeline: number): Keep {
  return removeRange(keep, tTimeline, tTimeline) ?? keep;
}

/** A stretch of a lane between two boundaries, in both coordinate spaces. */
export interface Piece {
  /** Timeline seconds. */
  start: number;
  end: number;
  /** The same stretch of the source. */
  sourceStart: number;
  sourceEnd: number;
}

/**
 * The pieces of a lane — what a Camtasia user clicks: one per kept range,
 * laid end to end on the timeline. A split makes two where there was one; a
 * join sits between two as well. With no list at all the whole source is
 * one piece, which is what "no edit" means to the drawing.
 */
export function pieces(keep: Keep, sourceDuration: number): Piece[] {
  const source = keep.length > 0 ? keep : wholeKeep(sourceDuration);
  const out: Piece[] = [];
  let at = 0;
  for (const [start, end] of source) {
    const next = round3(at + (end - start));
    out.push({ start: round3(at), end: next, sourceStart: start, sourceEnd: end });
    at = next;
  }
  return out;
}

/** The piece under a timeline moment, the later one at a shared boundary (as `toSource` chooses); null outside. */
export function pieceAt(list: Piece[], tTimeline: number): Piece | null {
  let found: Piece | null = null;
  for (const piece of list) if (piece.start <= tTimeline && tTimeline <= piece.end) found = piece;
  return found;
}

/**
 * Snap a moment to the nearest of `candidates` within `thresholdSeconds`
 * (8 px at the current zoom, as Camtasia's), or leave it alone. `snapped` is
 * the candidate it landed on, for the label. The nearest wins; a tie goes to
 * the first given.
 */
export function snap(t: number, candidates: number[], thresholdSeconds: number): { t: number; snapped: number | null } {
  let best: number | null = null;
  let gap = thresholdSeconds;
  for (const candidate of candidates) {
    const distance = Math.abs(candidate - t);
    if (distance < gap || (distance === gap && best === null)) {
      best = candidate;
      gap = distance;
    }
  }
  return best === null ? { t, snapped: null } : { t: best, snapped: best };
}

/**
 * The candidate a label may claim AFTER a clamp: `snapped` only when the
 * painted position `at` is that candidate (within `EPSILON`), else `null`.
 * `snap` lands the POINTER on a candidate; the model's clamp — `trimPiece`'s
 * neighbouring range, floor, 0 or end; `trimClip`'s; `normalize`'s picture;
 * a block's members at the audition's ends — can then put the thing
 * somewhere else, and ⌖ means "caught", not "asked for". The Reviewer's walk
 * (E5a, MINOR 1): the playhead parked at output 6, piece 1's in-edge dragged
 * to it, the clamp holding the edge at the previous range's end — a ⌖ for a
 * moment the edge never reached.
 */
export function caughtAfterClamp(at: number, snapped: number | null): number | null {
  return snapped !== null && Math.abs(at - snapped) <= EPSILON ? snapped : null;
}

/** The bound `services/narration.py::MAX_OFFSET_SECONDS` puts on an offset. */
export const MAX_OFFSET = 300;

/**
 * The offsets a drag commits: every moved block's pin, moved by the same
 * `deltaSeconds`, as an OFFSET — nothing else (trap 20). The caller passes
 * what is DRAWN — `offset` is the plan's `pinned_start − start`, never the
 * page's stored number, which can be a plan-refetch behind — so the pin is
 * `max(0, start + offset)` = `pinned_start` and a block lands exactly where
 * the pointer let go of it; a stored offset that pinned the sentence before
 * zero is floored the same way, so that case lands where it was dropped too.
 * Rounded to the stored precision, clamped to the server's ±300 s, and 0
 * becomes `null` — the List view's own rule for its Offset box
 * (`numberChange`), so a block dragged back to where it was spoken leaves no
 * key behind.
 */
export function dragOffsets(
  blocks: { index: number; start: number; offset: number | null }[], deltaSeconds: number,
): { index: number; offset: number | null }[] {
  return blocks.map(({ index, start, offset }) => {
    const pin = Math.max(0, start + (offset ?? 0));
    const next = round3(Math.min(MAX_OFFSET, Math.max(-MAX_OFFSET, pin + deltaSeconds - start)));
    return { index, offset: next === 0 ? null : next };
  });
}

// ── the edit per track ──────────────────────────────────────────────────────

/** The two tracks an edit has (E3): each a list, or `null` for a track that keeps everything. */
export interface TrackEdit {
  video: Keep | null;
  narration: Keep | null;
}
export type Track = keyof TrackEdit;
/** Camtasia's locks: a locked track's list is left exactly as it is by a cut or a split. */
export type TrackLocks = Record<Track, boolean>;
export const TRACKS: readonly Track[] = ["video", "narration"];

/**
 * Every lane the locks and the channel selection cover (E4): the two tracks —
 * material with an edit, each a kept list — and the Music lane, which is
 * CLIPS placed on the output and has no list at all (spec §12.5). `TrackEdit`
 * stays the two: a cut or a split reads `TrackLocks`, and the music's lock is
 * read beside it.
 */
export type Lane = Track | "music";
export const LANES: readonly Lane[] = ["video", "narration", "music"];
export type LaneLocks = Record<Lane, boolean>;

/**
 * The locks as held in `localStorage` — anything else reads as unlocked.
 * **A value written before E4 has two keys**, so a missing `music` is not
 * "lock it": every absent key is unlocked, which is what a project edited
 * before the Music lane existed must come back as.
 */
export function laneLocks(held: unknown): LaneLocks {
  const stored = (held && typeof held === "object" ? held : {}) as Record<string, unknown>;
  return { video: stored.video === true, narration: stored.narration === true, music: stored.music === true };
}

/**
 * A track's WORKING list — what a gesture starts from: the stored list, else
 * the whole source. Every path derives it the same way; a track with no
 * stored ranges is whole, never empty (a cut computed from `[]` would refuse
 * itself as "nothing left").
 */
export function trackList(keep: Keep | null, sourceDuration: number): Keep {
  return keep && keep.length > 0 ? keep : wholeKeep(sourceDuration);
}

/**
 * A track's list as the PUT body wants it: `null` for a whole track — none
 * stored, or one range covering the source — so the record carries no
 * trivial list. A whole-source SPLIT (two touching ranges) is kept: its
 * boundary is the point.
 */
export function trackBody(keep: Keep | null, sourceDuration: number): Keep | null {
  if (keep === null || keep.length === 0) return null;
  return keep.length === 1 && wholeSource(keep, sourceDuration) ? null : keep;
}

/** Whether two edits are the same once each track is in the body's terms. */
export function sameEdit(a: TrackEdit, b: TrackEdit, sourceDuration: number): boolean {
  return TRACKS.every((track) => JSON.stringify(trackBody(a[track], sourceDuration)) === JSON.stringify(trackBody(b[track], sourceDuration)));
}

export type CutOutcome = { next: TrackEdit; refused: null } | { next: null; refused: Track };

/**
 * Camtasia's ripple delete on the unlocked tracks: the timeline interval
 * `[a, b]` removed from each list that is not locked; a locked list is left
 * exactly as it is (trap 19), so cutting the picture with Narration locked
 * moves no pin. The interval means the same instant on both lists — each is
 * laid out from 0 on the output axis (trap 18). Refused, naming the track,
 * when it would leave a track with nothing.
 */
export function nextEditForCut(
  edit: TrackEdit, locks: TrackLocks, a: number, b: number, sourceDuration: number,
): CutOutcome {
  const next: TrackEdit = { video: edit.video, narration: edit.narration };
  for (const track of TRACKS) {
    if (locks[track]) continue;
    const cut = removeRange(trackList(edit[track], sourceDuration), a, b);
    if (cut === null) return { next: null, refused: track };
    next[track] = trackBody(cut, sourceDuration);
  }
  return { next, refused: null };
}

/**
 * Whether a cut can change anything at all under these locks — what the
 * scissors is enabled by, and what the keys check before they send.
 *
 * Two combinations can do nothing. Every lane locked is the obvious one. The
 * other is **Music alone**, which is exactly what clicking the Music channel's
 * name produces: the clips ride the picture — `at` is in output seconds, the
 * picture's axis (spec §12.2) — so the ripple applies only when the picture's
 * own list changed, and with the picture locked a cut would leave every clip
 * where it is. A scissors that appears to work and changes nothing is worse
 * than one that says why (the owner's ruling, 2026-09-21: unlock Video to cut
 * both, or use the clip's own gestures). `S` is unaffected and still splits a
 * clip at the playhead — a split changes no clip's `at`.
 */
export function canCut(locks: LaneLocks): boolean {
  if (LANES.every((lane) => locks[lane])) return false;
  return !(locks.video && locks.narration && !locks.music);
}

/**
 * Split at the playhead (`S`) on the unlocked tracks, or on every track
 * regardless of locks (`Ctrl+Shift+S`, `all`). A boundary already there, or
 * a moment at either end, changes nothing on that track — compare with
 * `sameEdit` to know whether there is anything to commit.
 */
export function nextEditForSplit(
  edit: TrackEdit, locks: TrackLocks, tTimeline: number, sourceDuration: number, all = false,
): TrackEdit {
  const next: TrackEdit = { video: edit.video, narration: edit.narration };
  for (const track of TRACKS) {
    if (!all && locks[track]) continue;
    next[track] = trackBody(splitAt(trackList(edit[track], sourceDuration), tTimeline), sourceDuration);
  }
  return next;
}

/**
 * Whether a plain click on a lane selects the piece under it: only once the
 * lane has more than one piece — a split or a cut made them. With a single
 * piece the lane is the whole picture, and a click there seeks, as it always
 * has; selecting the entire picture with one click would only ever arm a
 * Cut that must refuse.
 */
export function clickSelectsPiece(list: Piece[]): boolean {
  return list.length > 1;
}

// ── the trim: a piece's edge is the cut, and moves (E5a, spec §13.1) ────────

/** Which edge of a piece a trim moves: its start (`in`) or its end (`out`). */
export type TrimEdge = "in" | "out";

/**
 * What a trim did to the OUTPUT, in output seconds: a `cut` of `[a, b]` — the
 * same interval a Cut removes, applied with `removeRange` — or an `insert` of
 * `d` seconds at `t`, its inverse, applied with `restoreRange`. One gesture,
 * two lists (spec §13.1): the trimmed lane's own change is the source of
 * this, and every other unlocked track gets the same interval.
 */
export type TrimChange = { kind: "cut"; a: number; b: number } | { kind: "insert"; t: number; d: number };

/**
 * A piece's bound moved to `round3(toSource)`, clamped so the list stays what
 * the server stores — ordered, disjoint, every piece at least a frame long:
 *
 * - the in-edge within `[the previous range's end (or 0), end − FRAME_SECONDS]`;
 * - the out-edge within `[start + FRAME_SECONDS, the next range's start (or the source's end)]`.
 *
 * Dragging an in-edge RIGHT removes more from the piece's start; LEFT restores
 * source as far as the previous range's end. An out-edge: left shortens, right
 * restores up to the next range's start. The output re-lays itself from zero,
 * so everything after the edge moves — the ripple is the model's (spec §2), not
 * code here.
 *
 * **A trim never removes a piece** (decision 6): the floor is one frame, the
 * label says so, and Cut is how a piece goes. A piece already shorter than a
 * frame (a split can leave one) can only grow. The floor is the gesture's,
 * not the server's — `validate_keep` asks only `start < end`.
 *
 * An empty or whole `keep` is `wholeKeep(sourceDuration)` first, as `pieces()`
 * reads it. Rounded ONCE, at the bound, like `removeRange`. A no-op — the bound
 * where it already is — returns an equal list, so the caller can compare.
 */
export function trimPiece(keep: Keep, index: number, edge: TrimEdge, toSource: number, sourceDuration: number): Keep {
  const list = keep.length > 0 ? keep : wholeKeep(sourceDuration);
  const range = list[index];
  if (!range) return list;
  const [start, end] = range;
  const wanted = round3(toSource);
  if (edge === "in") {
    const floor = index > 0 ? list[index - 1][1] : 0;
    const ceiling = Math.max(start, round3(end - FRAME_SECONDS));
    const bound = Math.min(Math.max(wanted, floor), ceiling);
    if (bound === start) return list;
    return list.map((held, i): [number, number] => (i === index ? [bound, end] : held));
  }
  const ceiling = index + 1 < list.length ? list[index + 1][0] : round3(sourceDuration);
  const floor = Math.min(end, round3(start + FRAME_SECONDS));
  const bound = Math.max(Math.min(wanted, ceiling), floor);
  if (bound === end) return list;
  return list.map((held, i): [number, number] => (i === index ? [start, bound] : held));
}

/** Whether a piece sits at the trim's floor — one frame long, or shorter — where the label says "use Cut". */
export function atFrameFloor(keep: Keep, index: number): boolean {
  const range = keep[index];
  return !!range && round3(range[1] - range[0]) <= round3(FRAME_SECONDS) + EPSILON;
}

/**
 * The inverse of a cut, on ONE list: at a boundary at output moment `tOutput`
 * (within `EPSILON`), the bound on `side` moves back into that list's OWN gap
 * by up to `seconds` — `before`: the range that STARTS there begins earlier,
 * as far as the previous range's end (or 0), which at `tOutput` 0 is the head
 * coming back; `after`: the range that ENDS there runs later, as far as the
 * next range's start (or the source's end), which at the output's end is the
 * tail coming back. No boundary there — or a boundary with no gap behind it,
 * a bare split — and the list comes back exactly as it is (the same object).
 *
 * Trap 38: a restore is bounded by the gap the cut left, per track. Restoring
 * picture that the narration never lost cannot invent narration — a picture
 * cut made with Narration locked, restored later, brings the frames back and
 * leaves the sentences where they are, which is what "locked" meant when the
 * cut was made.
 */
export function restoreRange(keep: Keep, tOutput: number, seconds: number, side: "before" | "after", sourceDuration: number): Keep {
  if (!(seconds > 0)) return keep;
  const list = keep.length > 0 ? keep : wholeKeep(sourceDuration);
  let at = 0;
  for (let i = 0; i < list.length; i++) {
    const [start, end] = list[i];
    if (side === "before" && Math.abs(at - tOutput) <= EPSILON) {
      const floor = i > 0 ? list[i - 1][1] : 0;
      const bound = round3(Math.max(floor, start - seconds));
      if (bound >= start - EPSILON) return keep;
      return list.map((held, k): [number, number] => (k === i ? [bound, end] : held));
    }
    at = round3(at + (end - start));
    if (side === "after" && Math.abs(at - tOutput) <= EPSILON) {
      const ceiling = i + 1 < list.length ? list[i + 1][0] : round3(sourceDuration);
      const bound = round3(Math.min(ceiling, end + seconds));
      if (bound <= end + EPSILON) return keep;
      return list.map((held, k): [number, number] => (k === i ? [start, bound] : held));
    }
  }
  return keep;
}

/**
 * What moving one bound of `keep`'s piece `index` to `bound` (source seconds)
 * does to the OUTPUT: a cut of the removed stretch, or an insert of the
 * restored one, at the piece's edge as it is laid out NOW. `null` when the
 * bound does not move. Rounded once, at the interval's far end: the near end
 * is the piece's own rounded boundary.
 */
export function trimChange(keep: Keep, index: number, edge: TrimEdge, bound: number, sourceDuration: number): TrimChange | null {
  const piece = pieces(keep, sourceDuration)[index];
  if (!piece) return null;
  const delta = round3(bound - (edge === "in" ? piece.sourceStart : piece.sourceEnd));
  if (delta === 0) return null;
  if (edge === "in") {
    return delta > 0
      ? { kind: "cut", a: piece.start, b: round3(piece.start + delta) }
      : { kind: "insert", t: piece.start, d: -delta };
  }
  return delta < 0
    ? { kind: "cut", a: round3(piece.end + delta), b: piece.end }
    : { kind: "insert", t: piece.end, d: delta };
}

export interface TrimOutcome {
  /** The next edit, or `null` when refused. The same edit when the lane is locked or nothing moved. */
  next: TrackEdit | null;
  /** The track a shortening would leave with nothing, as `nextEditForCut` names it. */
  refused: Track | null;
  /** The trimmed lane's own change — what the label reports. `null` for a no-op or a locked lane. */
  change: TrimChange | null;
  /**
   * The PICTURE's change — what the music rides (`musicAfterTrim`). The same
   * as `change` when the picture is the trimmed lane; when the narration is,
   * it is what the video list really did with the interval: nothing when
   * Video is locked or has no boundary at that moment, and only as much as
   * its own gap held on a restore. `null` when the picture did not change.
   */
  picture: TrimChange | null;
}

/**
 * The picture's own change, read off its two lists rather than assumed from
 * the locks: the music rides the picture (the owner's ruling, 2026-09-21),
 * so a trim that leaves the picture's length alone must move no clip, and a
 * restore the picture's gap only half held moves the clips by that half.
 */
function pictureChange(before: Keep | null, after: Keep | null, change: TrimChange, sourceDuration: number, locked: boolean): TrimChange | null {
  if (locked) return null;
  const moved = round3(outputDuration(trackList(after, sourceDuration)) - outputDuration(trackList(before, sourceDuration)));
  if (moved === 0) return null;
  return change.kind === "cut"
    ? { kind: "cut", a: change.a, b: round3(change.a - moved) }
    : { kind: "insert", t: change.t, d: moved };
}

/**
 * A trim of one piece's edge on `lane`, applied to every unlocked track the
 * way `nextEditForCut` applies a cut (trap 37: a trim is a cut with a name,
 * and a second implementation of "remove `[a, b]`" is a defect):
 *
 * - the trimmed lane must be unlocked, else nothing changes (`change: null`);
 * - its own list is `trimPiece`, and the output interval that moved is `change`;
 * - a SHORTENING is `removeRange` of that interval on each other unlocked
 *   track — refused, naming the track, if it would empty it;
 * - a RESTORE is `restoreRange` on each other unlocked track: that track's own
 *   bound at the same output moment moves back into its own gap, by the
 *   restored length or as much as the gap holds, and not at all where it has
 *   no join (trap 38).
 *
 * The interval means the same instant on every list — each is laid out from
 * 0 on the output axis (trap 18). `picture` is what the video list really did
 * with it, for the music.
 */
export function nextEditForTrim(
  edit: TrackEdit, locks: TrackLocks, lane: Track, index: number, edge: TrimEdge, toSource: number, sourceDuration: number,
): TrimOutcome {
  const unchanged: TrimOutcome = { next: edit, refused: null, change: null, picture: null };
  if (locks[lane]) return unchanged;
  const list = trackList(edit[lane], sourceDuration);
  const trimmed = trimPiece(list, index, edge, toSource, sourceDuration);
  const range = trimmed[index];
  if (!range) return unchanged;
  const change = trimChange(list, index, edge, edge === "in" ? range[0] : range[1], sourceDuration);
  if (change === null) return unchanged;
  const next: TrackEdit = { video: edit.video, narration: edit.narration };
  next[lane] = trackBody(trimmed, sourceDuration);
  for (const track of TRACKS) {
    if (track === lane || locks[track]) continue;
    const held = trackList(edit[track], sourceDuration);
    if (change.kind === "cut") {
      const cut = removeRange(held, change.a, change.b);
      if (cut === null) return { next: null, refused: track, change, picture: null };
      next[track] = trackBody(cut, sourceDuration);
    } else {
      next[track] = trackBody(restoreRange(held, change.t, change.d, edge === "in" ? "before" : "after", sourceDuration), sourceDuration);
    }
  }
  return { next, refused: null, change, picture: pictureChange(edit.video, next.video, change, sourceDuration, locks.video) };
}

/**
 * Which edge of which piece a pointer has, or `null` for a body (or nothing).
 * The zones are `edgeSeconds` wide (8 px at the current zoom) but never more
 * than a THIRD of the piece each, as `clipAt`'s are, so a narrow piece keeps a
 * third of itself to click. At a join the two edges coincide (the strip closes
 * holes): the zone to the LEFT of the join is the left piece's out-edge, to the
 * RIGHT the right piece's in-edge, and the join itself belongs to the later
 * piece, as `pieceAt` and `toSource` choose. The very first in-edge and the
 * last out-edge are the head and tail trims, and exist on a lane with a single
 * whole piece too.
 */
export function pieceEdgeAt(list: Piece[], t: number, edgeSeconds: number): { index: number; edge: TrimEdge } | null {
  let found = -1;
  for (let i = 0; i < list.length; i++) if (list[i].start <= t && t <= list[i].end) found = i;
  if (found < 0) return null;
  const piece = list[found];
  const grab = Math.min(Math.max(0, edgeSeconds), (piece.end - piece.start) / 3);
  if (t <= piece.start + grab) return { index: found, edge: "in" };
  if (t >= piece.end - grab) return { index: found, edge: "out" };
  return null;
}

/**
 * The drag's label: what the trim does to the output. At the floor it says
 * why the edge will go no further (decision 6); with nothing moved, that.
 */
export function trimLabel(change: TrimChange | null, atFloor: boolean): string {
  if (atFloor) return "one frame — use Cut to remove it";
  if (change === null) return "no change";
  return change.kind === "cut" ? `−${(change.b - change.a).toFixed(3)} s` : `+${change.d.toFixed(3)} s restored`;
}

// ── the music lane: clips on the output axis (E4, spec §12.2 and §12.5) ─────

/**
 * One music clip, exactly as the record stores it and `PUT /edit` takes it.
 * `at` is in OUTPUT seconds — the axis the ruler shows, which is what makes
 * the lane behave as Camtasia's under a cut (`cutMusic`) — while `in` / `out`
 * are the slice of the FILE in its own seconds, so the clip's length on the
 * timeline is `out − in`. `gain` is a LINEAR factor 0–1 (trap 26: never the
 * old "rough dB" formula), and the fades are linear ramps in seconds,
 * together never longer than the clip.
 *
 * `id` is client-minted and unique in the list (`mintClipId`): the selection,
 * the undo stack and the inspector need a handle that survives re-ordering,
 * and the server never renumbers. `file_duration` and `missing` are added by
 * the READ-BACK only (`services/edit.py::stored_music`) and are never sent —
 * `musicBody` strips them, because `EditIn` forbids an unknown key.
 */
export interface MusicClip {
  id: string;
  file: string;
  at: number;
  in: number;
  out: number;
  gain: number;
  fade_in: number;
  fade_out: number;
  /** Read-back only: the library's recorded length for `file`, `null` when it has gone. */
  file_duration?: number | null;
  /** Read-back only: the file is no longer in the library — the render will refuse. */
  missing?: boolean;
}

/** `services/edit.py::MIN_CLIP_SECONDS` — shorter than this is a click, not music. */
export const MIN_CLIP_SECONDS = 0.1;
/** `services/edit.py::MAX_CLIPS`. */
export const MAX_CLIPS = 200;
/** The studio's `music_volume` when it cannot be read (spec §12.8, decision 2). */
export const DEFAULT_MUSIC_GAIN = 0.15;
/** A new clip's fades (decision 2): a bed under narration nearly always wants one. */
export const DEFAULT_FADE_IN = 1;
export const DEFAULT_FADE_OUT = 2;
/** The grab zone at each end of a clip, pixels — Camtasia's trim handles. */
export const CLIP_EDGE_PX = 8;

/** How long the clip runs on the timeline: the slice of the file it plays. */
export function clipLength(clip: MusicClip): number {
  return round3(clip.out - clip.in);
}
/** Where it ends on the OUTPUT axis. */
export function clipEnd(clip: MusicClip): number {
  return round3(clip.at + clipLength(clip));
}

/** A gain the server will accept: linear, 0–1; anything unreadable falls back to the default. */
export function clipGain(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, round3(value))) : DEFAULT_MUSIC_GAIN;
}

/**
 * The two fades cut down to fit `length`: each at least 0, and together never
 * longer than the clip — the server's rule (`fade_in + fade_out <= length`),
 * applied here so a trim or a cut can never produce a list it would refuse.
 * The fade IN is kept whole first: it is the one a listener hears.
 */
export function fitFades(length: number, fadeIn: number, fadeOut: number): [number, number] {
  const span = Math.max(0, round3(length));
  const into = Math.min(Math.max(0, round3(fadeIn)), span);
  const outOf = Math.min(Math.max(0, round3(fadeOut)), Math.max(0, round3(span - into)));
  return [into, outOf];
}

/**
 * The clips as the PUT body wants them: the eight stored keys and nothing
 * else. The read-back's `file_duration` and `missing` are DERIVED, and
 * `EditIn` forbids an unknown key — sending a clip back as it arrived is a
 * 422.
 */
export function musicBody(clips: MusicClip[]): MusicClip[] {
  return clips.map((clip) => ({
    id: clip.id,
    file: clip.file,
    at: round3(clip.at),
    in: round3(clip.in),
    out: round3(clip.out),
    gain: clipGain(clip.gain),
    fade_in: round3(clip.fade_in),
    fade_out: round3(clip.fade_out),
  }));
}

/** Whether two clip lists are the same in the body's terms — so a gesture that changed nothing commits nothing. */
export function sameMusic(a: MusicClip[], b: MusicClip[]): boolean {
  return JSON.stringify(musicBody(a)) === JSON.stringify(musicBody(b));
}

/**
 * A fresh id under `prefix`: `^[a-z0-9_-]{1,32}$`, as the server's id rule
 * wants, and not one of `taken` — never a number the list is renumbered by,
 * because the id is the handle the selection and the undo stack hold.
 */
function mintId(prefix: string, taken: Iterable<string>, random: () => number): string {
  const held = new Set(taken);
  for (let attempt = 0; attempt < 64; attempt++) {
    const id = `${prefix}${Math.floor(random() * 0xffffff).toString(36)}${attempt > 8 ? attempt.toString(36) : ""}`;
    if (!held.has(id)) return id;
  }
  // Exhausted 64 draws (a fake random that never moves): fall back to a
  // counter, so a mint always answers with a free id rather than a duplicate
  // the server would refuse.
  for (let n = 0; ; n++) if (!held.has(`${prefix}${n.toString(36)}`)) return `${prefix}${n.toString(36)}`;
}

/** A fresh clip id (`m…`), as the server's `_CLIP_ID_RE` wants and not one of `taken`. */
export function mintClipId(taken: Iterable<string> = [], random: () => number = Math.random): string {
  return mintId("m", taken, random);
}

/**
 * The clip the Library's "Add at playhead" makes (spec §12.5): at the
 * playhead, from the top of the file, as long as the file or as long as the
 * output has room for — whichever is shorter — at the studio's music volume,
 * with the default fades cut down to fit. `null` when there is less than
 * `MIN_CLIP_SECONDS` of room (the playhead is at the very end, or the file is
 * a click): the caller says so rather than sending a clip the server refuses.
 */
export function newMusicClip(args: {
  id: string; file: string; fileDuration: number; at: number; outputDuration: number; gain: number;
}): MusicClip | null {
  const at = Math.max(0, round3(args.at));
  const file = Math.max(0, round3(args.fileDuration));
  const room = args.outputDuration > 0 ? Math.max(0, round3(args.outputDuration - at)) : file;
  const out = round3(Math.min(file, room));
  if (out < MIN_CLIP_SECONDS - EPSILON) return null;
  const [fade_in, fade_out] = fitFades(out, DEFAULT_FADE_IN, DEFAULT_FADE_OUT);
  return { id: args.id, file: args.file, at, in: 0, out, gain: clipGain(args.gain), fade_in, fade_out };
}

/**
 * A clip moved along the lane: only `at` changes, never the slice (the drag's
 * whole contract). Bounded at both ends — never before 0, and never past
 * `limit − its length`, so the clip cannot be parked beyond the end of the
 * audition. The server has no such rule (`at >= 0` is all it asks), and the
 * render mixes under `amix=…:duration=first`, so a clip dropped past the end
 * would be silently absent from the output while the Render line still counted
 * it — and it would be drawn past the ruler. E3's block drag clamps the same
 * way. `limit` defaults to no ceiling, for the callers that have no length to
 * offer.
 */
export function moveClip(clip: MusicClip, at: number, limit = Infinity): MusicClip {
  const ceiling = round3(limit - clipLength(clip));
  // There is NO room for it: a clip longer than what is left of the audition,
  // which a picture cut made with the Music lane locked can leave behind. A
  // drag must never move a clip somewhere the user did not drag it to, and
  // capping the ceiling at 0 did exactly that — the first drag slammed the
  // clip to 0 and committed it, and every drag after that did nothing at all.
  // So it stays exactly where it is, and the gesture commits nothing
  // (`sameMusic` sees no change).
  if (ceiling < 0) return clip;
  return { ...clip, at: Math.min(Math.max(0, round3(at)), ceiling) };
}

/** Which end of a clip a pointer has: its left edge, its right edge, or its body. */
export type ClipZone = "in" | "body" | "out";

/**
 * A clip trimmed by dragging one of its edges to `toTimeline` (output
 * seconds). The LEFT edge moves `at` and `in` together — the audio stays
 * where it is under the pointer, as Camtasia's trim does — and the right edge
 * moves `out`. Every bound the server checks is applied here: never past the
 * file's length, never `in < 0` or `at < 0`, never shorter than
 * `MIN_CLIP_SECONDS`; the fades are re-fitted to the new length.
 * `fileDuration` is the library's recorded length — `null` for a file that
 * has gone, which bounds BOTH edges by the slice the clip has: its `in` may
 * rise and its `out` may fall, never past the values it holds (E6: the server
 * keeps a missing clip's slice shrunk, never grown). The clip handed in is
 * the stored one — a drag starts from the plan's clip — so those values are
 * the stored slice.
 */
export function trimClip(clip: MusicClip, edge: "in" | "out", toTimeline: number, fileDuration: number | null): MusicClip {
  const length = clipLength(clip);
  const limit = fileDuration === null ? clip.out : round3(fileDuration);
  if (edge === "in") {
    const wanted = round3(toTimeline) - clip.at;
    // How far the left edge may move LEFT: to the top of the file (and 0 on
    // the output) for a file with a length, not at all for a missing one.
    const floor = fileDuration === null ? 0 : Math.max(-clip.in, -clip.at);
    const delta = round3(Math.min(Math.max(wanted, floor), round3(length - MIN_CLIP_SECONDS)));
    const next = { ...clip, at: Math.max(0, round3(clip.at + delta)), in: Math.max(0, round3(clip.in + delta)) };
    const [fade_in, fade_out] = fitFades(clipLength(next), next.fade_in, next.fade_out);
    return { ...next, fade_in, fade_out };
  }
  const wanted = round3(toTimeline) - clipEnd(clip);
  const delta = round3(Math.min(Math.max(wanted, round3(MIN_CLIP_SECONDS - length)), round3(limit - clip.out)));
  const next = { ...clip, out: round3(clip.out + delta) };
  const [fade_in, fade_out] = fitFades(clipLength(next), next.fade_in, next.fade_out);
  return { ...next, fade_in, fade_out };
}

/** One piece of a clip after a cut: the same file, a slice of it, at its new place on the output. */
function clipSlice(clip: MusicClip, at: number, inSeconds: number, outSeconds: number, id: string): MusicClip {
  const start = round3(inSeconds);
  const end = round3(outSeconds);
  // A fade belongs to the clip's OWN edge. An edge the cut made is new, and
  // carries no fade: a bed sliced in two must not gain two ramps nobody asked
  // for. The surviving fades are re-fitted to the shorter piece.
  const [fade_in, fade_out] = fitFades(
    round3(end - start),
    Math.abs(start - clip.in) <= EPSILON ? clip.fade_in : 0,
    Math.abs(end - clip.out) <= EPSILON ? clip.fade_out : 0,
  );
  return { ...clip, id, at: Math.max(0, round3(at)), in: start, out: end, fade_in, fade_out };
}

/**
 * Camtasia's ripple delete applied to the clips (spec §12.5, decision 1),
 * in OUTPUT seconds and only when the Music lane is UNLOCKED (trap 19 —
 * locked, the caller passes the list straight through):
 *
 * - a clip wholly after `[a, b]` moves earlier by `b − a`;
 * - one that spans the cut is SPLIT in two — the part before keeps its `at`
 *   and its id, the part after starts at `a` with `in` advanced by the
 *   overlap it lost and a freshly minted id;
 * - one wholly inside is removed;
 * - one overlapping an edge is trimmed.
 *
 * Every result still satisfies the model: ordered by `at`, no piece shorter
 * than `MIN_CLIP_SECONDS` (a shorter remnant is dropped), fades fitted to the
 * new length, ids unique, and nothing left overlapping the cut. A ZERO-LENGTH
 * interval is the split gesture (`S` on the Music lane): the clips under the
 * playhead become two, and nothing moves.
 *
 * **Every piece lies inside the slice of the clip it came from**, of the same
 * file: a head keeps `in` and ends earlier, a tail starts later in the file
 * and keeps `out`. That is exactly the server's rule for a clip whose file
 * the library has lost (E6, the owner's decision of 2026-09-29: "you may keep
 * what you have, and you may keep less of it" — `services/edit.py::
 * _check_music`), so the server stores a cut or a split ACROSS a missing
 * clip. E4c's `missingAcross`, which refused one before the PUT because the
 * server then would not, went with that rule; a property test in
 * `lib/music.test.ts` holds every piece of every random cut and split inside
 * its clip's slice. A cut of the PICTURE across a missing clip is still
 * refused on the strip, for a different reason — its undo could not be
 * stored while the file is gone (`missingCutByPicture`).
 *
 * **The interval is rounded ONCE, at its bounds**, and every number after that
 * is computed from those two and rounded only when it becomes a clip's own
 * value. That is the same rounding the PICTURE's list gets — `removeRange`
 * rounds the pieces it keeps, so the join lands at `round3(a)` and everything
 * after it moves by `round3(b) − round3(a)` — and it is what makes a rippled
 * clip land exactly ON the join rather than beside it. Rounding the
 * DIFFERENCE as well put the two a millisecond apart on bounds that are not
 * round numbers: a Ctrl+drag cut of 9.9856 – 13.3144 left the join at 9.986
 * and the clip at 9.985, and a clip at 20 s at 16.671 where the picture had
 * moved by 3.328 (the supervisor's live check, 2026-09-21).
 */
export function cutMusic(
  clips: MusicClip[], a: number, b: number, mint: (taken: Set<string>) => string = mintClipId,
): MusicClip[] {
  const lo = round3(Math.max(0, Math.min(a, b)));
  const hi = round3(Math.max(0, Math.max(a, b)));
  const cut = hi - lo;
  const taken = new Set(clips.map((clip) => clip.id));
  const out: MusicClip[] = [];
  for (const clip of clips) {
    const start = clip.at;
    const end = clipEnd(clip);
    const headEnd = Math.min(end, lo);
    const tailStart = Math.max(start, hi);
    const head = headEnd - start >= MIN_CLIP_SECONDS - EPSILON;
    const tail = end - tailStart >= MIN_CLIP_SECONDS - EPSILON;
    if (head) out.push(clipSlice(clip, start, clip.in, clip.in + (headEnd - start), clip.id));
    if (tail) {
      const id = head ? mint(taken) : clip.id;
      taken.add(id);
      out.push(clipSlice(clip, tailStart - cut, clip.in + (tailStart - start), clip.out, id));
    }
  }
  return out.sort((x, y) => x.at - y.at);
}

/**
 * The clips a CUT leaves, under these locks — **the music rides the picture**
 * (the owner's ruling, 2026-09-21), as one rule in one place rather than a
 * line inside the component that only a regex over 164 kB of source could
 * pin.
 *
 * `at` is in OUTPUT seconds — the picture's axis (spec §12.2) — so the ripple
 * applies exactly when the picture's own list really changed, and that is
 * exactly when Video is unlocked: a cut that does not shorten the picture must
 * not move a clip out of sync with the frames it was placed against. The Music
 * lane obeys the locks like a track (trap 24), so its own lock passes the list
 * straight through as well. With the picture cut and Music unlocked a clip
 * after the cut moves earlier, one across it is split and one inside it goes.
 *
 * A SPLIT is not subject to this and does not come through here: it changes no
 * clip's `at`, so it cannot put the music out of step with the frames.
 */
export function musicAfterCut(clips: MusicClip[], locks: LaneLocks, a: number, b: number): MusicClip[] {
  return locks.music || locks.video ? clips : cutMusic(clips, a, b);
}

/**
 * The clips a RESTORE leaves — the mirror of `cutMusic`'s ripple, under the
 * same rule as `musicAfterCut` (the music rides the picture): when Video AND
 * Music are unlocked, every clip at or after output moment `t` moves later by
 * `d`; a clip straddling `t` is not split and does not move (the picture
 * grew under its tail; nothing was placed against the frames that came
 * back); with either lane locked the clips are untouched — the same objects.
 * A clip that does not move is the same object too, and a list in which
 * nothing moves is the same array, so `sameMusic` and trap 37's `===` both
 * read "unchanged". The two numbers are rounded ONCE, at the top, and each
 * moved clip's `at` when it becomes the clip's own (§12.5's As-built on the
 * join).
 */
export function musicAfterInsert(clips: MusicClip[], locks: LaneLocks, t: number, d: number): MusicClip[] {
  if (locks.music || locks.video) return clips;
  const at = round3(Math.max(0, t));
  const by = round3(d);
  if (!(by > 0)) return clips;
  let moved = false;
  const out = clips.map((clip) => {
    if (clip.at < at - EPSILON) return clip;
    moved = true;
    return { ...clip, at: round3(clip.at + by) };
  });
  return moved ? out : clips;
}

/**
 * The clips a TRIM leaves: `musicAfterCut` over a shortening's interval,
 * `musicAfterInsert` for a restore, the same list for a no-op. `change` is
 * the PICTURE's change (`TrimOutcome.picture`), never merely the trimmed
 * lane's: a narration trim that leaves the picture's length alone moves no
 * clip, and a restore the picture's gap only half held moves them by that
 * half — the music rides the picture, not the gesture.
 */
export function musicAfterTrim(clips: MusicClip[], locks: LaneLocks, change: TrimChange | null): MusicClip[] {
  if (change === null) return clips;
  return change.kind === "cut"
    ? musicAfterCut(clips, locks, change.a, change.b)
    : musicAfterInsert(clips, locks, change.t, change.d);
}

/**
 * The MISSING clips a cut of the PICTURE over `[a, b]` would shorten, split or
 * remove — so the strip refuses that cut before anything is sent (E6, the
 * owner's decision of 2026-09-30: "refuse the cut first").
 *
 * Why. Since E6 the server keeps a missing clip shrunk or split, so such a cut
 * would be stored — but its UNDO would put the clip's longer slice back, and
 * the server refuses a missing clip that grows, the picture's part of that
 * step with it: an ordinary picture cut that could not be undone, with nothing
 * said before it was made. Refused first, every cut made while a file is
 * missing stays undoable (a few are refused whose undo the server would have
 * taken: another clip of the same file still holds the whole stretch). A clip
 * the cut removes whole is named too: its undo would add a file the project no
 * longer holds. The trims and splits of the missing clip
 * itself — its own edges, `S` — stay allowed; their undo is refused and taken
 * off the list with a message that says so (`useEditCommits.ts::afterRefusal`).
 *
 * Exactly when `musicAfterCut` would ripple the clips — Video and Music both
 * unlocked; with either locked the clips are untouched and nothing is named —
 * and by `cutMusic`'s own arithmetic: a missing clip is named unless the cut
 * leaves it as ONE piece with the same slice (wholly before the cut, or wholly
 * after it and only moved). A piece trim that shortens the picture is the same
 * cut, over the picture's change (`TrimOutcome.picture`). LIVE clips are never
 * named. E4c's `missingAcross` refused a changed slice for another reason (the
 * server kept none) and let a clip swallowed whole through.
 */
export function missingCutByPicture(clips: MusicClip[], locks: LaneLocks, a: number, b: number): MusicClip[] {
  if (locks.music || locks.video) return [];
  return clips.filter((clip) => {
    if (!clip.missing) return false;
    const pieces = cutMusic([clip], a, b, () => "piece");
    return !(pieces.length === 1 && pieces[0].in === clip.in && pieces[0].out === clip.out);
  });
}

/** `a.mp3`; `a.mp3 and b.mp3`; `a.mp3, b.mp3 and c.mp3`. */
function listed(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * What the strip says when it refuses a picture cut or a piece trim across a
 * missing clip (`missingCutByPicture`): the gesture and where, in the strip's
 * own timecode, the file or files, why, and the two ways out — the one that
 * keeps the clip first.
 */
export function pictureCutRefusal(gesture: "cut" | "trim", across: MusicClip[], a: number, b: number): string {
  const files = [...new Set(across.map((clip) => clip.file))];
  const oneFile = files.length === 1;
  return `That ${gesture} (${timecode(Math.min(a, b))} – ${timecode(Math.max(a, b))}) would cut across ${listed(files)},`
    + ` but ${oneFile ? "its file is" : "their files are"} no longer in the library, so the ${gesture} could not be undone`
    + ` while ${oneFile ? "it is" : "they are"} gone. Lock the Music lane to ${gesture} the picture alone, or remove the`
    + ` missing ${across.length === 1 ? "clip" : "clips"} first.`;
}

/**
 * The clips a DELETE leaves: the named clip goes, missing or not, and the
 * rest are the same objects in the same order. One rule for the inspector's
 * button and for the Delete key with a clip selected.
 *
 * Under E4b a missing clip took every other missing clip with it, because the
 * server refused to store any list still naming a file the library had lost
 * and so the only body it would take was the list minus all of them. Since
 * E4c the server keeps a clip it already holds ("you may keep what you have,
 * you may not add what is not there" — `services/edit.py::_check_music`), so
 * a missing clip is deleted like any other, and clearing the lane of every
 * missing clip is the banner's own gesture, `withoutMissing`.
 *
 * An id that is not in the list leaves the list alone: a stale selection must
 * not clear the lane.
 */
export function clipsAfterDelete(clips: MusicClip[], id: string): MusicClip[] {
  if (!clips.some((clip) => clip.id === id)) return clips;
  return clips.filter((clip) => clip.id !== id);
}

/**
 * The clips the banner's button leaves: every clip whose file the library
 * has lost goes, in one commit, and the rest are the same objects in the same
 * order. A different gesture from Delete since E4c — Delete takes one clip —
 * so it has a rule of its own. A list with nothing missing comes back as it
 * is (the caller commits nothing then: the file came back and the plan
 * refetched, trap 37).
 */
export function withoutMissing(clips: MusicClip[]): MusicClip[] {
  return clips.some((clip) => clip.missing) ? clips.filter((clip) => !clip.missing) : clips;
}

/**
 * The moments a dragged clip snaps to (E3's `snap` does the snapping): the
 * playhead, the picture's joins, 0, the picture's end, and the other clips'
 * starts and ends — never its own, which would pin it where it already is.
 */
export function clipSnapTargets(args: {
  playhead: number; duration: number; joins: number[]; clips: MusicClip[]; exclude?: string;
}): number[] {
  const out = [0, round3(args.duration), round3(args.playhead), ...args.joins.map(round3)];
  for (const clip of args.clips) {
    if (clip.id === args.exclude) continue;
    out.push(round3(clip.at), clipEnd(clip));
  }
  return out;
}

/**
 * ONE candidate set for every gesture that places a cut (E5a, spec §13.4):
 * 0, the output's end, the playhead, every join, every sentence pin — each
 * block's drawn start and end, `pins`, which the component passes —, every
 * clip's two edges and (E5b) every DRAWN marker's `timeline_at`, `markers`.
 * The green and red handles, a Ctrl+drag range's ends, a piece's trimmed
 * edge and a dragged marker all snap to this, built once at the gesture's
 * start (trap 41), through `snap` unchanged. Deduplicated at the stored
 * precision and sorted, so a hundred pins is a small array and a tie is the
 * earlier moment. The block and clip drags keep their own subsets
 * (`clipSnapTargets`, and the block's gathered in the component). A dragged
 * marker's own moment is left out by the caller, as a dragged clip's is.
 */
export function snapTargets(args: {
  playhead: number; duration: number; joins: number[]; pins: number[]; clips: MusicClip[]; markers?: number[];
}): number[] {
  const out = new Set<number>([0, round3(Math.max(0, args.duration)), round3(Math.max(0, args.playhead))]);
  for (const at of args.joins) out.add(round3(at));
  for (const pin of args.pins) out.add(round3(pin));
  for (const clip of args.clips) {
    out.add(round3(clip.at));
    out.add(clipEnd(clip));
  }
  for (const at of args.markers ?? []) out.add(round3(at));
  return [...out].sort((x, y) => x - y);
}

// ── the markers: named moments of the picture's SOURCE (E5b, spec §13.2) ────

/**
 * One marker, exactly as the record stores it and `PUT /edit` takes it: `at`
 * in SOURCE seconds of the picture — like a sentence's `start`, unlike a
 * clip's `at` — so a cut before it moves it with its frame, a cut over it
 * hides it, a restore brings it back, and nothing rewrites it (trap 39: the
 * clips are the deliberate exception, because a clip is not IN the source).
 * `timeline_at` is the READ-BACK's projection through the picture's list
 * (`services/edit.py::stored_markers`), `null` for a marker in removed
 * picture, and is never sent — `markersBody` strips it, because `EditIn`
 * forbids an unknown key.
 *
 * `id` is client-minted and unique in the list (`mintMarkerId`): the
 * selection, the undo stack and the name box need a handle that survives
 * re-sorting, and the server never renumbers.
 */
export interface Marker {
  id: string;
  at: number;
  name: string;
  /** Read-back only: where it lands in the output, `null` in removed picture. */
  timeline_at?: number | null;
}
/** A marker the ruler draws: its `timeline_at` is a number. */
export type DrawnMarker = Marker & { timeline_at: number };

/** `services/edit.py::MAX_MARKERS` and `MAX_MARKER_NAME`. */
export const MAX_MARKERS = 200;
export const MAX_MARKER_NAME = 80;

/** A fresh marker id (`k…`), under the same rule as a clip's and not one of `taken`. */
export function mintMarkerId(taken: Iterable<string> = [], random: () => number = Math.random): string {
  return mintId("k", taken, random);
}

/** A name as the server stores it: trimmed and no longer than `MAX_MARKER_NAME`; `""` when nothing is left. */
export function markerName(name: string): string {
  return name.trim().slice(0, MAX_MARKER_NAME).trim();
}

/**
 * What the name box hands the server: `markerName` of the typed text with
 * every control character (below U+0020, and U+007F) removed first — a
 * pasted name can carry a tab or a newline the box cannot show, the server
 * refuses one inside a name (a chapter title is cut at it by ffmpeg's own
 * reader), and the user should never meet that refusal for a paste.
 */
export function sanitizeMarkerName(name: string): string {
  // eslint-disable-next-line no-control-regex
  return markerName(name.replace(/[\u0000-\u001f\u007f]/g, ""));
}

/** A SOURCE moment as the server will accept it: rounded, within `[0, sourceDuration]`. */
function clampSource(at: number, sourceDuration: number): number {
  return Math.min(Math.max(0, round3(at)), Math.max(0, round3(sourceDuration)));
}

/** The markers the ruler draws, in order: those the picture's list projects. */
export function drawnMarkers(markers: Marker[]): DrawnMarker[] {
  return markers.filter((marker): marker is DrawnMarker =>
    typeof marker.timeline_at === "number" && Number.isFinite(marker.timeline_at));
}

/** The list as the server keeps it: sorted by `at`, stable, a new array. */
export function sortMarkers(markers: Marker[]): Marker[] {
  return [...markers].sort((x, y) => x.at - y.at);
}

/**
 * The marker `M` drops: at the playhead's SOURCE moment (`toSource(tOutput,
 * keep)`, inside the source), named "Marker N" with N one more than the
 * count, its `timeline_at` the output moment it was dropped at so the ruler
 * can draw it before the plan has it. `null` at the cap, which the caller
 * says rather than sending a list the server refuses.
 */
export function newMarker(
  markers: Marker[], tOutput: number, keep: Keep, sourceDuration: number, mint: (taken: string[]) => string = mintMarkerId,
): DrawnMarker | null {
  if (markers.length >= MAX_MARKERS) return null;
  const at = clampSource(toSource(tOutput, keep), sourceDuration);
  const landed = toTimeline(at, keep);
  return {
    id: mint(markers.map((marker) => marker.id)),
    at,
    name: `Marker ${markers.length + 1}`,
    timeline_at: landed === null ? round3(Math.max(0, tOutput)) : landed,
  };
}

/**
 * The list with `id` renamed to `name` as the server stores it (trimmed,
 * `MAX_MARKER_NAME`): the same list when the id is not there, the name would
 * be empty, or it is the name already, so the caller commits nothing then.
 * Every other marker is the same object.
 */
export function renameMarker(markers: Marker[], id: string, name: string): Marker[] {
  const next = markerName(name);
  const held = markers.find((marker) => marker.id === id);
  if (!held || !next || next === held.name) return markers;
  return markers.map((marker) => (marker.id === id ? { ...marker, name: next } : marker));
}

/**
 * The list with `id` moved to the OUTPUT moment `tOutput`: its `at` becomes
 * `toSource(tOutput, keep)` — a marker cannot land in removed picture, since
 * the output axis only shows kept frames —, inside the source, and the list
 * is re-sorted. The same list when the marker is not there or did not move.
 */
export function moveMarker(markers: Marker[], id: string, tOutput: number, keep: Keep, sourceDuration: number): Marker[] {
  const held = markers.find((marker) => marker.id === id);
  if (!held) return markers;
  const at = clampSource(toSource(tOutput, keep), sourceDuration);
  if (at === round3(held.at)) return markers;
  return sortMarkers(markers.map((marker) => (marker.id === id ? { ...marker, at, timeline_at: toTimeline(at, keep) } : marker)));
}

/**
 * The list a DELETE leaves: the named marker goes and the rest are the same
 * objects in the same order; an id that is not there leaves the list alone,
 * so a stale selection clears nothing.
 */
export function markersAfterDelete(markers: Marker[], id: string): Marker[] {
  if (!markers.some((marker) => marker.id === id)) return markers;
  return markers.filter((marker) => marker.id !== id);
}

/**
 * Where Ctrl+[ and Ctrl+] go from `t`: the nearest drawn marker's
 * `timeline_at` STRICTLY before it and strictly after it (beyond `EPSILON`,
 * so a playhead sitting on a marker jumps to the next one rather than
 * staying), `null` where there is none.
 */
export function markerNeighbours(drawn: DrawnMarker[], t: number): { previous: number | null; next: number | null } {
  let previous: number | null = null;
  let next: number | null = null;
  for (const marker of drawn) {
    const at = marker.timeline_at;
    if (at < t - EPSILON && (previous === null || at > previous)) previous = at;
    if (at > t + EPSILON && (next === null || at < next)) next = at;
  }
  return { previous, next };
}

/**
 * The markers as the PUT body wants them: the three stored keys and nothing
 * else — the read-back's `timeline_at` is DERIVED, and `EditIn` forbids an
 * unknown key —, `at` rounded and the name as the server stores it.
 */
export function markersBody(markers: Marker[]): { id: string; at: number; name: string }[] {
  return markers.map((marker) => ({ id: marker.id, at: round3(marker.at), name: markerName(marker.name) }));
}

/** Whether two marker lists are the same in the body's terms — so a gesture that changed nothing commits nothing. */
export function sameMarkers(a: Marker[], b: Marker[]): boolean {
  return JSON.stringify(markersBody(a)) === JSON.stringify(markersBody(b));
}

/**
 * Where a dragged clip lands: its start snapped to a candidate, or — when
 * nothing is near its start — its END snapped instead, so a bed can be pulled
 * up against a join or another clip by either edge (Camtasia snaps both).
 * `snapped` is the candidate it caught, for the label; `null` is a free drag.
 */
export function snapClip(
  at: number, length: number, candidates: number[], thresholdSeconds: number,
): { at: number; snapped: number | null } {
  const start = snap(at, candidates, thresholdSeconds);
  if (start.snapped !== null) return { at: Math.max(0, start.t), snapped: start.snapped };
  const end = snap(round3(at + length), candidates, thresholdSeconds);
  if (end.snapped !== null) return { at: Math.max(0, round3(end.t - length)), snapped: end.snapped };
  return { at: Math.max(0, round3(at)), snapped: null };
}

/**
 * The clip under a timeline moment and which part of it: the LAST one that
 * covers it, because clips may overlap and the last is the one drawn on top.
 * The edge zones are `edgeSeconds` wide (8 px at the current zoom) but never
 * more than a THIRD of the clip each, so a narrow clip keeps a third of itself
 * to grab. Half each — which is what a `/ 2` cap means — left a clip 16 px
 * wide or narrower with the two zones meeting in the middle and no body at
 * all: it could be trimmed and never moved, at any zoom that drew it that
 * small. The CSS edge overlays follow the same rule (`min(8px, 33%)`), so the
 * `ew-resize` cursor never promises a trim where this answers "body".
 *
 * **A missing clip has both edges, like any other** (E6). E4c gave it a body
 * and no edges, because the server then kept it only with the slice it had;
 * since E6 the server keeps it shrunk as well (never grown), so its edges
 * trim it — `trimClip` bounds them by the slice it holds, the file's length
 * being unknown.
 */
export function clipAt(clips: MusicClip[], t: number, edgeSeconds: number): { clip: MusicClip; zone: ClipZone } | null {
  for (let i = clips.length - 1; i >= 0; i--) {
    const clip = clips[i];
    const end = clipEnd(clip);
    if (t < clip.at || t > end) continue;
    const grab = Math.min(Math.max(0, edgeSeconds), clipLength(clip) / 3);
    const zone: ClipZone = t <= clip.at + grab ? "in" : t >= end - grab ? "out" : "body";
    return { clip, zone };
  }
  return null;
}

/**
 * The file's peaks for the stretch a clip plays. `GET /api/music/{name}/peaks`
 * answers in the FILE's seconds, and the clip shows `in…out` of it — which is
 * one kept range of that file, so the audio lane's own rule does the work
 * (`projectPeaks`) and there is no second bucket arithmetic to drift.
 */
export function clipPeaks(peaks: number[], bucketSeconds: number, clip: MusicClip): number[] {
  return projectPeaks(peaks, bucketSeconds, [[clip.in, clip.out]]);
}

/** Where a clip's one-shot source node goes on the audition's clock. */
export interface ClipPlayback {
  /** Seconds after `from` that it starts — 0 when it is already running. */
  delay: number;
  /** How far into the FILE to begin. */
  offset: number;
  /** How much of it is left to play. */
  length: number;
}

/**
 * A clip placed on the audition's clock from `from` (spec §12.5):
 * `start(ctxStart + max(0, at − from), in + max(0, from − at),
 * out − in − max(0, from − at))`. `null` when there is nothing left to hear —
 * the clip finished before `from`, or its file is missing, which the audition
 * skips (trap 25).
 */
export function clipPlayback(clip: MusicClip, from: number): ClipPlayback | null {
  if (clip.missing) return null;
  const behind = Math.max(0, from - clip.at);
  const length = round3(clipLength(clip) - behind);
  if (length <= EPSILON) return null;
  return { delay: Math.max(0, round3(clip.at - from)), offset: round3(clip.in + behind), length };
}

/**
 * The clip's own level `played` seconds in: its gain shaped by the two LINEAR
 * fades (decision 4 — ffmpeg's `afade` default curve and the browser's
 * `linearRampToValueAtTime` are the same shape, so the audition and the render
 * agree). The two are multiplied, as the render's two chained `afade` filters
 * are; validation keeps them from overlapping, so at most one is ever below 1.
 */
export function fadeLevel(clip: MusicClip, played: number): number {
  const length = clipLength(clip);
  const u = Math.min(Math.max(0, played), length);
  const rise = clip.fade_in > 0 ? Math.min(1, u / clip.fade_in) : 1;
  const fall = clip.fade_out > 0 ? Math.min(1, (length - u) / clip.fade_out) : 1;
  return Math.max(0, Math.min(1, clipGain(clip.gain) * rise * fall));
}

/** One instruction for the clip's `GainNode`, `at` seconds after playback begins. */
export interface FadePoint {
  at: number;
  value: number;
  /** A linear ramp to `value` (`linearRampToValueAtTime`), else a plain `setValueAtTime`. */
  ramp: boolean;
}

/**
 * The whole envelope a clip's `GainNode` follows, for a playback that begins
 * `played` seconds into the clip (a seek into the middle of a bed starts at
 * the level it had reached). Always begins with a `setValueAtTime`, because a
 * ramp is only ever linear FROM the previous event — the plateau before a
 * fade-out has to be anchored or the bed would slide down across its whole
 * length.
 */
export function fadePoints(clip: MusicClip, played = 0): FadePoint[] {
  const length = clipLength(clip);
  const level = clipGain(clip.gain);
  const start = Math.min(Math.max(0, played), length);
  const points: FadePoint[] = [{ at: 0, value: fadeLevel(clip, start), ramp: false }];
  const risesUntil = round3(clip.fade_in - start);
  if (clip.fade_in > 0 && risesUntil > 0) points.push({ at: risesUntil, value: level, ramp: true });
  if (clip.fade_out > 0) {
    const fallsFrom = round3(length - clip.fade_out - start);
    if (fallsFrom > 0) points.push({ at: fallsFrom, value: level, ramp: false });
    points.push({ at: round3(length - start), value: 0, ramp: true });
  }
  return points;
}

// ── the commit: what a PUT /edit carries, and what a refusal says ───────────

/**
 * One state of the edit as a client operation carries it: each track's kept
 * ranges (`null` for a track that keeps everything), the music clips and
 * (E5b) the markers. Neither is a track — they have no kept list — but both
 * are part of the edit, so undo and redo carry them (spec §12.5, decision
 * 10; §13.2).
 */
export interface EditOp extends TrackEdit {
  music: MusicClip[];
  markers: Marker[];
}

/** The body of `PUT /api/projects/{pid}/edit`: a key left out means UNCHANGED (trap 32). */
export interface EditBody {
  video: Keep | null;
  narration: Keep | null;
  music?: MusicClip[];
  markers?: { id: string; at: number; name: string }[];
}

/**
 * The body a commit sends — the single highest-risk decision in E4b, so it is
 * a function with tests rather than four lines inside a mutation.
 *
 * **Always a PUT, never a DELETE.** E3 sent `DELETE /edit` when both tracks
 * went back to whole, because that left the record with no `edit` key at all;
 * since E4a a DELETE clears the MUSIC too, so undoing the last picture cut
 * would have silently wiped the lane. `{video: null, narration: null}` says
 * exactly what is meant, and the server removes the `edit` key itself when
 * nothing but the version would remain (spec §12.6).
 *
 * **`music` is sent only when this operation CHANGES it**, because absent
 * means unchanged for every key (trap 32). A cut that leaves the clips alone
 * must not re-send them: a list the server did not need is a list it has to
 * check, and under E4b — when it still refused any list naming a file the
 * library had lost — re-sending an unchanged one made every unrelated edit
 * impossible on a project with one missing file. E4c keeps a stored missing
 * clip, but the rule stands on its own terms. Compared in the BODY's own
 * terms (`sameMusic`), so the read-back's `file_duration` and `missing` never
 * look like a change. **`markers` ride exactly as `music` does** (E5b): sent
 * only when the operation changes them, compared by `sameMarkers`, stripped
 * of the read-back's `timeline_at` — so a cut never re-sends the markers,
 * and a marker commit never re-sends the clips.
 */
export function editBody(op: EditOp, committed: { music: MusicClip[]; markers: Marker[] }, sourceDuration: number): EditBody {
  const body: EditBody = {
    video: trackBody(op.video, sourceDuration),
    narration: trackBody(op.narration, sourceDuration),
  };
  if (!sameMusic(op.music, committed.music)) body.music = musicBody(op.music);
  if (!sameMarkers(op.markers, committed.markers)) body.markers = markersBody(op.markers);
  return body;
}

/**
 * What the panel says when a commit is refused: the user's terms first, the
 * server's sentence after them.
 *
 * The server names a clip by its POSITION in the list and by its id — "music
 * clip 2 (sting): file 'musicB.mp3' is not in the library." — which is a
 * handle the user never chose and a number they cannot see. That is the
 * second half of the answer, not the first: what they need to read is that
 * nothing was saved. The detail is KEPT rather than replaced, because a
 * refusal this client does not recognise must still reach the user whole,
 * and no rule of the server's is restated here.
 *
 * Under E4b a refusal about the library gained a paragraph saying the lane
 * was frozen and pointing at the banner's button. Since E4c the lane is not
 * frozen — the server keeps a stored clip whose file has gone — and its own
 * sentences say what may not be done and what to do instead (a missing file
 * cannot be added; since E6 a missing clip's slice can shrink but not grow:
 * trim it shorter, split it, move it, level it, fade it, remove it, or put
 * the file back), so nothing is added to them.
 *
 * An undo or a redo the server refused on its body is taken off the stack
 * (E6, `useEditCommits.ts::afterRefusal`), and its lead says so: an edit
 * step's the undo's or the redo's own, a timing step's the timing lead with
 * the list named. Only when the server's sentence says a file "is not in the
 * library" — both of `_check_music`'s refusals of a missing file, "file 'x'
 * is not in the library." and "file 'x' is not in the library, so its slice
 * can shrink but not grow; …" — does the lead add the way to take the step
 * after all, putting the file back; any other refusal (a range past a source
 * re-extracted shorter, an offset for a sentence a transcript save removed)
 * names no file it has nothing to do with.
 */
export type RefusalLead = "edit" | "timing" | "undo" | "redo" | "timing-undo" | "timing-redo";

export function editRefusal(detail: string, what: RefusalLead): string {
  const fileGone = detail.includes("is not in the library");
  const lead = what === "timing" || what === "timing-undo" || what === "timing-redo"
    ? "That timing was not saved — the blocks are back where the last saved plan puts them."
      + (what === "timing" ? "" : ` It has been taken off the ${what === "timing-undo" ? "undo" : "redo"} list.`)
    : what === "undo" || what === "redo"
      ? `That ${what} was refused and has been taken off the ${what} list — the strip still shows the cut and the clips`
        + " the server holds." + (fileGone ? ` To ${what} past it, put the file back in the library first.` : "")
      : "That edit was not saved — the strip still shows the cut and the clips the server holds.";
  const said = detail.trim() ? ` The server said: ${detail.trim()}` : "";
  return `${lead}${said}`;
}

// ── the pointer gestures ────────────────────────────────────────────────────

/**
 * Every drag the strip knows: a scrub (the ruler or the head), a handle, a
 * Ctrl+drag range, a block move, a marquee, (E4) a music clip moved along
 * its lane or trimmed by one of its edges, (E5a) a PIECE trimmed by one of
 * its edges — the cut itself, moved — and (E5b) a marker's flag moved along
 * the ruler.
 */
export type DragKind = "scrub" | "in" | "out" | "range" | "move" | "marquee" | "clip" | "trim" | "piece-trim" | "marker";
/**
 * What a release that never moved — a click on the thing that was pressed —
 * means per kind: `seek` (the ruler), `pick` (the piece under a marquee's
 * start or under a pressed piece edge, or a seek where the lane has one piece
 * or is locked), `click` (a block, a music clip and its trim edges, or a
 * marker's flag: left to its own click handler, and the handlers deliberately
 * differ — a block's chooses, selects and SEEKS, a clip's and a marker's only
 * select, because seeking would jump the playhead back every time the
 * inspector was reached for while the audition plays (the owner's ruling,
 * 2026-09-21)), `keep-selection` (a handle or a Ctrl+click: the selection as
 * it stands), `nothing` (the head, or a pointer the browser cancelled).
 *
 * A piece edge pressed and released without moving is a click ON THE PIECE,
 * so it picks as a click beside the edge would: the edge is a zone of the
 * piece, not a thing of its own.
 */
export type UnmovedRelease = "seek" | "pick" | "click" | "keep-selection" | "nothing";

export function unmovedRelease(kind: DragKind, seekOnClick: boolean, cancelled: boolean): UnmovedRelease {
  if (cancelled) return "nothing";
  switch (kind) {
    case "scrub": return seekOnClick ? "seek" : "nothing";
    case "marquee": case "piece-trim": return "pick";
    case "move": case "clip": case "trim": case "marker": return "click";
    default: return "keep-selection";
  }
}

/**
 * Whether the click the browser fires after a release must be ignored: a
 * real drag happened, or the release itself was the click's meaning (a scrub
 * seeks, a marquee picks, a piece edge picks). A block's, a music clip's, a
 * clip edge's, a marker's or a handle's unmoved release lets the click
 * through — the element's own handler is where a click is a click, and those
 * handlers deliberately differ: a block's seeks, a clip's and a marker's only
 * select (the owner's ruling, 2026-09-21).
 *
 * The pointer is captured LAZILY, only once a drag has really moved: capture
 * on pointer-down would retarget that click (and a double-click) to the
 * capturing element, and no block or head would ever receive its own.
 */
export function releaseSuppressesClick(kind: DragKind, moved: boolean): boolean {
  return moved || kind === "scrub" || kind === "marquee" || kind === "piece-trim";
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
 * footage; spec §7, second note). The cut is encoded like a final render,
 * x264 `medium` (Q1): measured 32.1 s and 34.1 s for the corpus's 341 s of
 * 1080p30 reach on the bundled ffmpeg 7.1 (it was 16.1 s at `ultrafast`), so
 * `5 + reach × 0.1`. A keep that removes nothing skips the picture step and
 * estimates 0. Narration synthesis is not in the number: the audition caches
 * it, and the wording says so.
 */
export function renderEstimate(keep: Keep, sourceDuration: number): number {
  if (keep.length === 0 || wholeSource(keep, sourceDuration)) return 0;
  const reach = keep[keep.length - 1][1] - keep[0][0];
  return Math.ceil((5 + reach * 0.1) / 5) * 5;
}

/** Whether a track's list (null = whole) removes anything. */
function removes(keep: Keep | null, sourceDuration: number): keep is Keep {
  return keep !== null && keep.length > 0 && !wholeSource(keep, sourceDuration);
}

/**
 * The line under the Render button, or `null` when neither track removes
 * anything and there is no music. Cut together — the two lists equal — it is
 * E2's line word for word. Otherwise the picture part ("Cuts 2 ranges of the
 * picture (12.4 s removed)"), the narration part, which is about the
 * narration's TIMELINE (that stretch closes up and the sentences spoken in it
 * are left out — which ones is the plan's answer, so no count is claimed
 * here), and the music part, which is the second ffmpeg pass under the
 * finished narration (spec §12.4). The estimate is the picture step's alone.
 *
 * The music clause is joined with a COMMA, as §12.5 writes the sentence:
 * "Cuts 1 range of the picture (5.0 s removed), mixes 2 music clips under the
 * narration and re-voices …". Joined with "and", as it first shipped, the line
 * read "… and mixes 2 music clips under the narration and re-voices", which is
 * two joins where the sentence has room for one.
 *
 * A clip whose file has gone gets its own sentence, because the render
 * REFUSES on it (trap 25) — the job would stop before any work, and a Render
 * button that did not say so would be the one thing this line exists to
 * prevent.
 */
export function renderSummary(
  video: Keep | null, narration: Keep | null, sourceDuration: number, music: MusicClip[] = [],
): string | null {
  const cutsPicture = removes(video, sourceDuration);
  const cutsNarration = removes(narration, sourceDuration);
  const clips = music ?? [];
  if (!cutsPicture && !cutsNarration && clips.length === 0) return null;
  const together = cutsPicture && cutsNarration && JSON.stringify(video) === JSON.stringify(narration);
  const parts: string[] = [];
  if (cutsPicture) {
    const cuts = joins(video, sourceDuration).filter((join) => join.removed > 0).length;
    const removed = round3(sourceDuration) - outputDuration(video);
    parts.push(`Cuts ${cuts} range${cuts === 1 ? "" : "s"}${together ? "" : " of the picture"} (${removed.toFixed(1)} s removed)`);
  }
  if (cutsNarration && !together) {
    const removed = round3(sourceDuration) - outputDuration(narration);
    parts.push(`${cutsPicture ? "shortens" : "Shortens"} the narration's timeline by ${removed.toFixed(1)} s `
      + "(sentences spoken in the removed stretch are left out)");
  }
  const cuts = parts.join(" and ");
  const mixes = clips.length === 0 ? ""
    : `${cuts ? "mixes" : "Mixes"} ${clips.length} music clip${clips.length === 1 ? "" : "s"} under the narration`;
  const what = mixes && cuts ? `${cuts}, ${mixes}` : `${cuts}${mixes}`;
  const mix = clips.length > 0 ? "the music mix and " : "";
  const tail = cutsPicture
    ? `about ${renderEstimate(video, sourceDuration)} s, plus ${mix}any sentences the audition has not fetched yet.`
    : `the picture is not cut, so it takes only as long as ${mix}the sentences the audition has not fetched yet.`;
  const missing = clips.filter((clip) => clip.missing);
  const names = [...new Set(missing.map((clip) => clip.file))].join(", ");
  const refusal = missing.length === 0 ? "" : ` The render will refuse while ${missing.length} music clip`
    + `${missing.length === 1 ? " names" : "s name"} a file that is not in the library (${names}): remove `
    + `${missing.length === 1 ? "the clip" : "those clips"} or upload the file again.`;
  return `${what} and re-voices — ${tail}${refusal}`;
}
