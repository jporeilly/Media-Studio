/**
 * The edit's gestures that commit through the stack (R1a, with
 * `useEditCommits`): the cut, the split, the nudge and the reset, and the
 * blocks they act on. Every body is the component's own, moved whole; the
 * closure variables each read are the deps the hook takes, and the callbacks
 * keep their names. `drawn` is exported for the block move's release, which
 * stays in the root.
 */
import { useCallback, type Dispatch, type RefObject, type SetStateAction } from "react";
import {
  LANES,
  MAX_CLIPS,
  canCut,
  cutMusic,
  dragOffsets,
  missingAcross,
  missingAcrossRefusal,
  musicAfterCut,
  nextEditForCut,
  nextEditForSplit,
  type LaneLocks,
} from "../../../lib/edit";
import type { PlanSentence } from "../../../lib/timeline";
import type { EditState, Selection } from "./types";

export interface EditGesturesDeps {
  commitEdit: (next: EditState) => void;
  commitOffsets: (next: { index: number; offset: number | null }[]) => boolean;
  editLocked: boolean;
  setRefusal: Dispatch<SetStateAction<string | null>>;
  selectionRef: RefObject<Selection | null>;
  locksRef: RefObject<LaneLocks>;
  committedRef: RefObject<EditState>;
  positionRef: RefObject<number>;
  sourceDurationRef: RefObject<number>;
  selectedBlocksRef: RefObject<ReadonlySet<number>>;
  sentences: PlanSentence[];
  /** The row the list view has selected, so the two views agree on "this sentence". */
  selected: number | null;
}

/**
 * The blocks as `dragOffsets` wants them: from what is DRAWN - the plan's
 * pin, never the page's stored offset, which can be a plan refetch behind
 * (a value typed in the List a moment ago) and would land the block
 * seconds from where it was dropped. The stored copy is undo's business
 * only (`commitOffsets`).
 */
export const drawn = (blocks: PlanSentence[]) => blocks.map((s) => ({ index: s.index, start: s.start, offset: s.pinned_start - s.start }));

export function useEditGestures({
  commitEdit, commitOffsets, editLocked, setRefusal, selectionRef, locksRef, committedRef, positionRef, sourceDurationRef,
  selectedBlocksRef, sentences, selected,
}: EditGesturesDeps) {
  /**
   * Camtasia's ripple delete on the unlocked tracks (`nextEditForCut`,
   * lib/edit.ts): the selection removed from each list that is not locked,
   * its gap closed; a locked list left exactly as it is (trap 19) - so
   * cutting the picture with Narration locked moves no pin, and every later
   * sentence lands earlier against the picture by the length removed. A
   * locked track that is whole stays `null`, which is not a refusal: the
   * helper says which track, if any, the cut would empty.
   */
  const cutSelection = useCallback(() => {
    const sel = selectionRef.current;
    if (!sel || editLocked) return;
    const held = locksRef.current;
    if (LANES.every((lane) => held[lane])) return;
    // THE MUSIC RIDES THE PICTURE (the owner's ruling, 2026-09-21). With
    // Music the only unlocked lane the ripple below cannot move anything, so
    // the gesture would appear to work and do nothing at all: the scissors is
    // disabled for it (`canCut`, and `cutTitle` says why) and the keys refuse
    // here, rather than sending a PUT that changes no clip.
    if (!canCut(held)) {
      setRefusal("The music rides the picture, so a cut with the picture locked would leave every clip exactly "
        + "where it is. Unlock Video to cut both, or change the music alone with the clip's own gestures — drag "
        + "its ends to trim it, or select it and press Delete.");
      return;
    }
    const before = committedRef.current;
    const outcome = nextEditForCut(before, held, sel.start, sel.end, sourceDurationRef.current);
    if (outcome.refused) {
      const track = outcome.refused === "video" ? "picture" : "narration";
      setRefusal(`Keep at least one range — that selection would remove the whole ${track}.`);
      return;
    }
    // A cut ACROSS a missing clip would change its slice, which the server
    // refuses (E4c) in words that describe a trim, not this gesture (the
    // Reviewer's M1): refused here first, before anything is sent, naming
    // the gesture, the file and both ways out. Only where the ripple applies
    // at all - the same two locks `musicAfterCut` reads.
    if (!held.music && !held.video) {
      const across = missingAcross(before.music, sel.start, sel.end);
      if (across.length > 0) {
        setRefusal(missingAcrossRefusal("cut", across, sel.start, sel.end));
        return;
      }
    }
    // THE MUSIC RIDES THE PICTURE (the owner's ruling, 2026-09-21): the rule
    // and its reasons are `musicAfterCut` in lib/edit.ts, where a table over
    // all eight lock combinations tests it rather than a regex over this file.
    const music = musicAfterCut(before.music, held, sel.start, sel.end);
    if (music.length > MAX_CLIPS) {
      setRefusal(`That cut would split the music into ${music.length} clips, past the limit of ${MAX_CLIPS} — `
        + "remove a clip first, or lock the Music lane to cut the picture alone.");
      return;
    }
    // The markers ride along UNCHANGED (trap 39): they are moments of the
    // source, and the cut moves or hides them by projection, never by
    // rewriting them.
    commitEdit({ ...outcome.next, music, markers: before.markers });
  }, [commitEdit, editLocked, committedRef, locksRef, selectionRef, setRefusal, sourceDurationRef]);

  /**
   * Split at the playhead (`S`): a boundary in each unlocked list - or in
   * every list, regardless of locks, for Ctrl+Shift+S - at the source moment
   * under the playhead (`nextEditForSplit`). Nothing is removed (trap 23);
   * the pieces it makes are what a click selects. A split on an existing
   * boundary changes no list and commits nothing.
   */
  const splitAtPlayhead = useCallback((all: boolean) => {
    if (editLocked) return;
    const before = committedRef.current;
    const at = positionRef.current;
    const tracks = nextEditForSplit(before, locksRef.current, at, sourceDurationRef.current, all);
    // A split of the Music lane is the same ripple with nothing removed: the
    // clips under the playhead become two, and nothing moves (`cutMusic`).
    // Unaffected by the picture rule the cut follows: a split changes no
    // clip's `at`, so it cannot put the music out of step with the frames.
    // A split THROUGH a missing clip would change its slice, which the
    // server refuses (E4c) in words that describe a trim, not this gesture
    // (the Reviewer's M1): refused here first, before anything is sent,
    // wherever the music would be split at all - the lane unlocked, or
    // Ctrl+Shift+S, which splits regardless of the locks.
    if (all || !locksRef.current.music) {
      const across = missingAcross(before.music, at, at);
      if (across.length > 0) {
        setRefusal(missingAcrossRefusal("split", across, at));
        return;
      }
    }
    const music = !all && locksRef.current.music ? before.music : cutMusic(before.music, at, at);
    if (music.length > MAX_CLIPS) {
      setRefusal(`That split would make ${music.length} music clips, past the limit of ${MAX_CLIPS} — `
        + "remove a clip first, or lock the Music lane to split the tracks alone.");
      return;
    }
    commitEdit({ ...tracks, music, markers: before.markers });
  }, [commitEdit, editLocked, committedRef, locksRef, positionRef, setRefusal, sourceDurationRef]);

  /** The blocks a nudge or a Reset acts on: the selected blocks, else the chosen sentence when it is on the strip. */
  const actedOn = useCallback((): PlanSentence[] => {
    const chosen = selectedBlocksRef.current;
    if (chosen.size > 0) return sentences.filter((s) => chosen.has(s.index));
    const one = selected !== null ? sentences.find((s) => s.index === selected) : undefined;
    return one ? [one] : [];
  }, [selected, sentences, selectedBlocksRef]);
  /** `[` / `]`: the selected blocks a little earlier or later, one request per press. */
  const nudge = useCallback((deltaSeconds: number) => {
    if (editLocked) return;
    const blocks = actedOn();
    if (blocks.length === 0) return;
    commitOffsets(dragOffsets(drawn(blocks), deltaSeconds));
  }, [actedOn, commitOffsets, editLocked]);
  /** Reset timing: back to the spoken moment for the selected sentences - `offset: null` for each. */
  const resetTiming = useCallback(() => {
    if (editLocked) return;
    commitOffsets(actedOn().map((s) => ({ index: s.index, offset: null })));
  }, [actedOn, commitOffsets, editLocked]);

  return { cutSelection, splitAtPlayhead, actedOn, nudge, resetTiming };
}
