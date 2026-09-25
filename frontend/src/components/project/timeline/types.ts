/**
 * The types the edit's commit stack, its gestures and the timeline's root
 * share (R1a, docs/porting/edit-timeline.md §14). They were the root's own
 * block of declarations at the top of `NarrationTimeline.tsx`; each moved
 * here whole, with its comment, so the hooks under `timeline/` and the
 * component can name the same shapes without the root importing from a file
 * that imports from it.
 */
import type { Marker, MusicClip, TrackEdit } from "../../../lib/edit";
import type { Segment } from "../../../lib/narration";
import type { EditPayload } from "../../../lib/timeline";

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
export type Commit =
  | { kind: "do"; op: Op; before: Op }
  | { kind: "undo" | "redo"; op: Op; entry: Entry };
/** What a commit answers with: the stored edit, or the sentences a batch of offsets updated. */
export type Answer = EditPayload | { sentences: SavedSentence[] };
