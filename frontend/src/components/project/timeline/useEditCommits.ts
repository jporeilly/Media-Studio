/**
 * The edit's commit stack, its lock and its refusal (R1a — the first part of
 * the timeline component taken out of `NarrationTimeline.tsx`; the design
 * record is docs/porting/edit-timeline.md §14). Every body here is the
 * component's own, moved whole: the closure variables it read are the deps
 * the hook takes, the refs it wrote are the refs it is handed, and the
 * callbacks keep their names, so the root's handlers did not change. The
 * round's report lists the two lines that did: the calls back into the strip
 * — clearing the transforms a drag left once the plan lands, dropping the
 * marker `M` dropped when a commit is refused — go through `afterCommitRef`,
 * which the root assigns once those painters exist (the component's own
 * `keyHandler.current` idiom), because the stack is declared before the
 * lanes that paint and must not know them.
 */
import { useCallback, useLayoutEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { api, errorMessage } from "../../../api/client";
import { UNDO_DEPTH, editBody, editRefusal, sameEdit, sameMarkers, sameMusic, trackBody } from "../../../lib/edit";
import { narrationPlanKey, type EditPayload, type NarrationPlan } from "../../../lib/timeline";
import type { Answer, Commit, EditState, Entry, Op, SavedSentence, Selection } from "./types";

/** The undo stack: the entries to undo, oldest first, and the ones undone, ready to redo. */
export interface History {
  past: Entry[];
  future: Entry[];
}

/**
 * What the stack calls back into the strip with. Late-bound through a ref the
 * root assigns once the strip's painters exist, because the stack is declared
 * before the lanes that paint and must not know them (§14, the one cycle).
 */
export interface AfterCommit {
  /** The plan a commit produced has landed: the transforms a drag left go. */
  clearMoved: () => void;
  /** A commit was refused: the marker `M` dropped, which the server never took, goes. */
  dropPending: () => void;
}

export interface EditCommitsDeps {
  projectId: string;
  /** A job holds the project: every write of the edit is a 409, so Cut, Undo and Redo wait for it. */
  jobActive: boolean;
  /** The plan query: its `dataUpdatedAt` / `errorUpdatedAt` stamp the lock (see `awaiting`). */
  plan: Pick<UseQueryResult<NarrationPlan, Error>, "dataUpdatedAt" | "errorUpdatedAt">;
  /** What the server holds, as far as this client knows: the root's ref, advanced here by every commit that succeeds. */
  committedRef: RefObject<EditState>;
  /** The page's stored offsets, advanced here by every drag or nudge that lands. */
  committedOffsetsRef: RefObject<Record<number, number | null>>;
  sourceDurationRef: RefObject<number>;
  setSelection: Dispatch<SetStateAction<Selection | null>>;
  /** A drag or a nudge saved: the parent folds the sentences into its copy, as the List's adjust does. */
  onOffsetsSaved: (updated: SavedSentence[]) => void;
  afterCommitRef: RefObject<AfterCommit>;
  /**
   * A refusal made on the strip rather than by the server ("keep at least one
   * range"): the root's state, because the root's own handlers set it - read
   * into `editError` here and cleared on every commit.
   */
  refusal: string | null;
  setRefusal: Dispatch<SetStateAction<string | null>>;
  /** The lock as a ref, for the root's stable callbacks (a drag's release): the root's object, written here every render. */
  editLockedRef: RefObject<boolean>;
}

/**
 * The request one commit makes — the mutation's own body, with the closure
 * variables it read (`projectId`, `committedRef.current`,
 * `sourceDurationRef.current`) as parameters, so the rule is testable without
 * a component. A set of offsets is one PATCH. Everything else is ALWAYS a
 * PUT, never a DELETE, and `music` only when this operation changes it: both
 * rules live in `editBody` (lib/edit.ts), which is where they are tested. The
 * single highest-risk decision in E4b is not one to leave inside a mutation
 * as four lines of its own.
 */
export function commitRequest(op: Op, committed: EditState, sourceDuration: number, projectId: string): Promise<Answer> {
  if (op.kind === "offsets") {
    const body = Object.entries(op.values).map(([index, offset]) => ({ index: Number(index), offset }));
    return api.patch<{ sentences: SavedSentence[] }>(`/api/projects/${projectId}/narration/offsets`, { offsets: body });
  }
  return api.put<EditPayload>(
    `/api/projects/${projectId}/edit`,
    editBody(op, committed, sourceDuration),
  );
}

/**
 * The stack after a commit SUCCEEDED — the `setHistory` updater's own body,
 * with the closure's `op` read off `variables`: a `do` pushes its entry (what
 * was sent and what it replaced) and empties the redo side; an `undo` moves
 * the last entry over; a `redo` moves it back. Capped at `UNDO_DEPTH`.
 */
export function nextHistory(h: History, variables: Commit): History {
  const { op } = variables;
  if (variables.kind === "do") {
    return { past: [...h.past, { undo: variables.before, redo: op }].slice(-UNDO_DEPTH), future: [] };
  }
  if (variables.kind === "undo") return { past: h.past.slice(0, -1), future: [...h.future, variables.entry] };
  return { past: [...h.past, variables.entry].slice(-UNDO_DEPTH), future: h.future.slice(0, -1) };
}

export function useEditCommits({
  projectId, jobActive, plan, committedRef, committedOffsetsRef, sourceDurationRef, setSelection, onOffsetsSaved, afterCommitRef,
  refusal, setRefusal, editLockedRef,
}: EditCommitsDeps) {
  const qc = useQueryClient();
  // ── the edit: commit, undo, redo ─────────────────────────────────────────
  //
  // ONE request per gesture, on release, with the whole thing: the record is
  // rewritten whole and served whole (spec trap 8), and a drag of twelve
  // blocks is one PATCH of twelve offsets, never twelve (trap 21). Nothing
  // local is drawn from the new state - the plan is invalidated and the
  // strip is redrawn from the plan the server answers with, so a refused
  // commit (a 400 naming the range, a 409 while a job holds the project)
  // leaves the drawing exactly on the server's state with the message shown.
  //
  // Undo is a client-side stack of OPERATIONS - a cut or a split stores the
  // whole edit before and after, a drag or a Reset the moved sentences'
  // offsets before and after - pushed only when a commit SUCCEEDS; undo
  // sends the "before", redo the "after". It is lost on reload: the server
  // keeps only the current state, and the view says so.
  const [history, setHistory] = useState<{ past: Entry[]; future: Entry[] }>({ past: [], future: [] });
  // THE RACE THE LOCK CLOSES. A commit succeeds and the plan is invalidated,
  // but until the refetch lands the strip is still drawn from the PREVIOUS
  // plan (`keepPreviousData`) - and the server re-measures the speaking rate
  // first, so that window is hundreds of milliseconds warm and seconds cold.
  // A second Cut inside it would be computed against the stale keep and
  // would silently overwrite the first cut on the server; a Ctrl+Z inside it
  // (one key auto-repeat away) would snapshot the wrong "before". So the
  // gesture - Cut, Undo, Redo, buttons and keys alike - stays locked until a
  // NEWER plan than the one held at the commit replaces it, or its refetch
  // fails, or the tab is left. Stamped by the plan's own `dataUpdatedAt` /
  // `errorUpdatedAt` rather than by `isFetching`, which is scheduler-timed
  // and could read false before the refetch has begun.
  const [awaiting, setAwaiting] = useState<{ data: number; error: number } | null>(null);
  const planStampRef = useRef({ data: plan.dataUpdatedAt, error: plan.errorUpdatedAt });
  planStampRef.current = { data: plan.dataUpdatedAt, error: plan.errorUpdatedAt };
  // A LAYOUT effect: the render that brings the new plan also gives the moved
  // blocks their new `left`, and the transforms a drag left on them would
  // paint one frame doubled if they were cleared only after that paint.
  useLayoutEffect(() => {
    if (!awaiting) return;
    // Leaving the tab does NOT clear it: the plan is refetched on return (the
    // commit invalidated it) and that landing is what unlocks - clearing on
    // `!active` would reopen the window for the first gesture after coming
    // back, computed against the plan the tab left with.
    if (plan.dataUpdatedAt !== awaiting.data || plan.errorUpdatedAt !== awaiting.error) {
      setAwaiting(null);
      // The plan now draws the moved blocks where they landed.
      afterCommitRef.current.clearMoved();
    }
  }, [awaiting, plan.dataUpdatedAt, plan.errorUpdatedAt, afterCommitRef]);
  /** Between a successful commit and the plan it produced: the strip is about to change. */
  const applying = awaiting !== null;
  const commit = useMutation({
    mutationFn: ({ op }: Commit): Promise<Answer> => commitRequest(op, committedRef.current, sourceDurationRef.current, projectId),
    onMutate: () => setRefusal(null),
    onSuccess: (answer, variables) => {
      const { op } = variables;
      // What the server held until this instant is what undo goes back to.
      if (op.kind === "edit") {
        committedRef.current = { video: op.video, narration: op.narration, music: op.music, markers: op.markers };
        setSelection(null);
      } else {
        committedOffsetsRef.current = { ...committedOffsetsRef.current, ...op.values };
        // The List view reads the new numbers from the page's copy, folded in
        // rather than refetched - a refetch would drop half-typed words.
        if ("sentences" in answer) onOffsetsSaved(answer.sentences);
      }
      setHistory((h) => nextHistory(h, variables));
      setAwaiting(planStampRef.current);
      void qc.invalidateQueries({ queryKey: narrationPlanKey(projectId) });
      if (op.kind === "edit") {
        void qc.invalidateQueries({ queryKey: ["project", projectId] });
        void qc.invalidateQueries({ queryKey: ["edit", projectId] });
      }
    },
    // A refused commit puts every painted thing back - and drops the marker
    // `M` dropped, which the server never took.
    onError: () => { afterCommitRef.current.clearMoved(); afterCommitRef.current.dropPending(); },
  });
  const editLocked = jobActive || commit.isPending || applying;
  editLockedRef.current = editLocked;
  /**
   * A refusal the user can act on, with the server's own sentence kept after
   * it (`editRefusal`): the server names a clip by its position and its id,
   * which is a handle nobody here chose, and the first thing to say is that
   * nothing was saved. The detail is never swallowed - a refusal this client
   * does not recognise still reaches the user whole.
   */
  const editError = refusal ?? (commit.isError
    ? editRefusal(errorMessage(commit.error), commit.variables?.op.kind === "offsets" ? "timing" : "edit")
    : null);

  /**
   * Commit a new edit (a cut, a split) with what it replaces as its undo.
   * Compared and stored in the PUT body's own terms - a whole track is null -
   * so a split on the very start of an untouched track, which makes a list
   * that is still the whole source, commits nothing and leaves no undo entry.
   */
  const commitEdit = useCallback((next: EditState) => {
    const before = committedRef.current;
    const source = sourceDurationRef.current;
    if (sameEdit(next, before, source) && sameMusic(next.music, before.music) && sameMarkers(next.markers, before.markers)) return;
    const after: EditState = {
      video: trackBody(next.video, source),
      narration: trackBody(next.narration, source),
      music: next.music,
      markers: next.markers,
    };
    commit.mutate({ kind: "do", op: { kind: "edit", ...after }, before: { kind: "edit", ...before } });
  }, [commit, committedRef, sourceDurationRef]);

  /** ONE request for however many blocks: their offsets before (from the stored copy) and after. False when nothing changed. */
  const commitOffsets = useCallback((next: { index: number; offset: number | null }[]): boolean => {
    const stored = committedOffsetsRef.current;
    const changed = next.filter(({ index, offset }) => (stored[index] ?? null) !== offset);
    if (changed.length === 0) return false;
    const before: Record<number, number | null> = {};
    const after: Record<number, number | null> = {};
    for (const { index, offset } of changed) {
      before[index] = stored[index] ?? null;
      after[index] = offset;
    }
    commit.mutate({ kind: "do", op: { kind: "offsets", values: after }, before: { kind: "offsets", values: before } });
    return true;
  }, [commit, committedOffsetsRef]);

  const undo = useCallback(() => {
    if (history.past.length === 0 || editLocked) return;
    const entry = history.past[history.past.length - 1];
    commit.mutate({ kind: "undo", op: entry.undo, entry });
  }, [commit, editLocked, history.past]);
  const redo = useCallback(() => {
    if (history.future.length === 0 || editLocked) return;
    const entry = history.future[history.future.length - 1];
    commit.mutate({ kind: "redo", op: entry.redo, entry });
  }, [commit, editLocked, history.future]);

  return { commit, commitEdit, commitOffsets, undo, redo, history, editLocked, editError, applying };
}
