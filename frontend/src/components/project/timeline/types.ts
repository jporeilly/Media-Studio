/**
 * The types the edit's commit stack, its gestures and the timeline's root
 * share (R1a, docs/porting/edit-timeline.md §14). They were the root's own
 * block of declarations at the top of `NarrationTimeline.tsx`; each moved
 * here whole, with its comment, so the hooks under `timeline/` and the
 * component can name the same shapes without the root importing from a file
 * that imports from it.
 */
import type { ClipZone, DragKind, DrawnMarker, Marker, MusicClip, Track, TrackEdit, TrimEdge } from "../../../lib/edit";
import type { Segment } from "../../../lib/narration";
import type { EditPayload, PlanSentence, Schedule } from "../../../lib/timeline";

/** One sentence as `PATCH /narration/offsets` returns it: the stored segment with its index. */
export type SavedSentence = Segment & { index: number };

/** A range of the OUTPUT, timeline seconds, `start < end`. */
export interface Selection {
  start: number;
  end: number;
}
/**
 * One state of the edit: each track's kept ranges (`null` for a track that
 * keeps everything), the music clips AND the markers — neither is a track,
 * but both are part of the edit, so undo and redo carry them (spec §12.5,
 * §13.2).
 */
export type EditState = TrackEdit & { music: MusicClip[]; markers: Marker[] };
/**
 * One operation the client can send: the whole edit (both lists and the
 * clips), or a set of offsets by index. An undo entry holds one of each
 * direction — what to send to undo it and what to send to redo it (§11.5).
 */
export type Op = ({ kind: "edit" } & EditState) | { kind: "offsets"; values: Record<number, number | null> };
export interface Entry {
  undo: Op;
  redo: Op;
}
/**
 * One commit through the stack. A `do` may carry `keepSelection` (E6): the
 * gesture's own intent that the range selection outlives it - a marker
 * dropped, named or moved. Nothing else keeps it, and an undo or a redo never
 * does: the stack's entries do not remember the gesture that made them.
 */
export type Commit =
  | { kind: "do"; op: Op; before: Op; keepSelection?: true }
  | { kind: "undo" | "redo"; op: Op; entry: Entry };
/** What a commit answers with: the stored edit, or the sentences a batch of offsets updated. */
export type Answer = EditPayload | { sentences: SavedSentence[] };
/**
 * The record every gesture on the strip writes (R1b, with `useAudition`:
 * `paint` reads it for the ⌖ on a handle's label, so the type crossed here
 * with the four constants below; the root still owns the drags).
 */
export interface Drag {
  kind: DragKind;
  /** The end that is NOT being dragged (a handle), or where the drag began (Ctrl+drag, a marquee). */
  anchor: number;
  /** Seconds between the pointer and the thing it grabbed, so a handle does not jump to the pointer on the first move. */
  offset: number;
  /** A scrub that never moved is a click: on the ruler that seeks, on the head it does nothing. */
  seekOnClick: boolean;
  startX: number;
  moved: boolean;
  pointerId: number;
  /** A move: the block grabbed, every block that moves with it, and the moments its pin snaps to. */
  index?: number;
  members?: PlanSentence[];
  snapTo?: number[];
  /** A move: the delta the blocks are currently painted at, seconds. */
  delta: number;
  /** A clip drag: the clip grabbed and which end of it, and where it is painted now. */
  clip?: MusicClip;
  zone?: ClipZone;
  clipNow?: MusicClip;
  /** A piece trim (E5a): the lane, the piece and which of its edges; and where the edge is painted now, in both axes. */
  lane?: Track;
  pieceIndex?: number;
  edge?: TrimEdge;
  trimNow?: { toSource: number; at: number };
  /** A marker drag (E5b): the flag grabbed, and the OUTPUT moment it is painted at now. */
  marker?: DrawnMarker;
  markerNow?: number;
  /** A handle or a range end: the candidate it caught (null: none) and which end of the selection is moving, for the label's ⌖. */
  snapped?: number | null;
  snapEnd?: "start" | "end";
}

// The audition's constants, moved here with `useAudition` (R1b), each with the
// comment it had at the top of the component.
/** Fetched three at a time. Sixty requests at once queue behind each other in
 *  the browser anyway and give the voice service a thundering herd. */
export const CONCURRENCY = 3;
/** The lead given to the first scheduled clip, so it lands in the future. */
export const START_LEAD = 0.08;
/** The lead given to a clip whose buffer arrives mid-play, so its `start` is not already in the past. */
export const LATE_LEAD = 0.02;
export const EMPTY_SCHEDULE: Schedule = { clips: [], overrunning: [], pushed: [], squeezed: [], end: 0 };

// The block selection's empty set, moved here with `useMusicLane` (R1c): the
// clip's two pointer handlers clear the blocks with it, as the marker handlers
// and the key map do (R1d), so the one constant is shared by import and
// `setSelectedBlocks(NO_BLOCKS)` stays the same reference on every side.
export const NO_BLOCKS: ReadonlySet<number> = new Set();
/** The inspector's half-typed values (R1c, `useMusicLane` holds them; `ClipInspector` edits them). */
export type ClipDraft = { gain?: number; fadeIn?: string; fadeOut?: string };

// The nudge keys' two steps, moved here with `useTimelineKeys` (R1d), with the
// comment they had at the top of the component.
/** `[` / `]` move the selected blocks this much; with Shift, five times as much. */
export const NUDGE_SECONDS = 0.05;
export const NUDGE_LARGE_SECONDS = 0.25;
