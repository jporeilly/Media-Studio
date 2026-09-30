/**
 * The markers (R1d — the fourth and last part of the timeline component taken
 * out of `NarrationTimeline.tsx`; the design record is
 * docs/porting/edit-timeline.md §14): the marker `M` has just dropped and the
 * box that names one, the flag nodes and the effect that reconciles both with
 * the plan, the painters a flag drag goes through (`paintMarker`,
 * `clearMarkerDrag`), `commitMarkers` — the one PUT every marker gesture
 * makes —, `dropMarker`, `finishNameBox`, `removeMarker`, `jumpToMarker`, the
 * flag's three handlers, and the flags the ruler draws. Every body here is the
 * component's own, moved whole: the closure variables each read are the deps
 * the hook takes, the state and the refs it alone writes are created here,
 * and the callbacks keep their names, so the root's handlers did not change.
 * The marker branches of the body's two pointer handlers stay in the root and
 * call the painters and the commit from here; the dragged flag's label stays
 * the root's (its release reads it) and is handed in, and the selected marker
 * stays the root's beside the selected clip and blocks. `dropPending` is what
 * the root puts into the commit stack's `afterCommitRef`, where it put
 * `() => setPending(null)` before. The ruler's markup is `MarkerRuler.tsx`
 * beside this file.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
  type SetStateAction,
} from "react";
import {
  MAX_MARKERS,
  drawnMarkers,
  markerNeighbours,
  markersAfterDelete,
  newMarker,
  renameMarker,
  sanitizeMarkerName,
  sortMarkers,
  type DragKind,
  type DrawnMarker,
  type Keep,
  type Marker,
} from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import { NO_BLOCKS, type Drag, type EditState } from "./types";

export interface MarkersDeps {
  /** The markers as the plan holds them, their signature, and the ref the stable callbacks read them through. */
  storedMarkers: Marker[];
  markersRef: RefObject<Marker[]>;
  markersSignature: string;
  /** The commit lock, as the value (`onMarkerPointerDown` takes it as a dependency) and as the ref every commit path reads. */
  editLocked: boolean;
  editLockedRef: RefObject<boolean>;
  /** The stack's one entry point (`keepSelection`: the range outlives the commit, E6), and what the server holds as far as the client knows. */
  commitEdit: (next: EditState, keepSelection?: boolean) => void;
  committedRef: RefObject<EditState>;
  setRefusal: Dispatch<SetStateAction<string | null>>;
  /** The playhead, the picture's list, the source's length and the pixels per second - the root's refs. */
  positionRef: RefObject<number>;
  keepRef: RefObject<Keep>;
  sourceDurationRef: RefObject<number>;
  ppsRef: RefObject<number>;
  /** The audition's seek: a jump to a marker moves the playhead as a click on the ruler does. */
  seek: (to: number) => void;
  /** The drag primitives a flag's press goes through, and the flag a release raises against the click after it. */
  beginDrag: (
    event: ReactPointerEvent, kind: DragKind, anchor: number, grabbed?: number,
    move?: Pick<Drag, "index" | "members" | "snapTo" | "clip" | "zone" | "lane" | "pieceIndex" | "edge" | "marker">,
  ) => void;
  snapTargetsNow: (exclude?: string) => number[];
  suppressClick: RefObject<boolean>;
  /** The three selections stay the root's; the markers set their own and clear the other two. */
  setSelectedMarker: Dispatch<SetStateAction<string | null>>;
  selectedMarkerRef: RefObject<string | null>;
  setSelectedClip: Dispatch<SetStateAction<string | null>>;
  setSelectedBlocks: Dispatch<SetStateAction<ReadonlySet<number>>>;
  /** The dragged flag's label, the root's ref: the painters here write it, and the body's release hides it. */
  markerLabelRef: RefObject<HTMLDivElement | null>;
}

export function useMarkers({
  storedMarkers, markersRef, markersSignature, editLocked, editLockedRef, commitEdit, committedRef, setRefusal, positionRef,
  keepRef, sourceDurationRef, ppsRef, seek, beginDrag, snapTargetsNow, suppressClick, setSelectedMarker, selectedMarkerRef,
  setSelectedClip, setSelectedBlocks, markerLabelRef,
}: MarkersDeps) {
  const [pending, setPending] = useState<DrawnMarker | null>(null);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const [nameBox, setNameBox] = useState<{ id: string; draft: string } | null>(null);
  /** Mirrors `nameBox` synchronously, so a box closes exactly once whichever event closes it first. */
  const nameBoxRef = useRef(nameBox);
  nameBoxRef.current = nameBox;
  /** The flag elements by id, for the drag's per-frame paint. */
  const markerNodes = useRef(new Map<string, HTMLButtonElement>());
  const attachMarker = useCallback((id: string) => (node: HTMLButtonElement | null) => {
    if (node) markerNodes.current.set(id, node);
    else markerNodes.current.delete(id);
  }, []);
  // A pending marker the plan now draws is pending no longer; a selected
  // marker the edit removed - a delete, an undo - is no longer selected (the
  // pending one is kept: it is not in the plan yet).
  useEffect(() => {
    setPending((held) => (held && markersRef.current.some((marker) => marker.id === held.id) ? null : held));
    setSelectedMarker((held) => (
      held !== null && !markersRef.current.some((marker) => marker.id === held) && pendingRef.current?.id !== held ? null : held
    ));
  }, [markersSignature, markersRef, setSelectedMarker]);

  // ── a marker in flight (E5b) ─────────────────────────────────────────────
  //
  // The flag follows the pointer along the ruler, snapped, as a clip does
  // along its lane: a transform on the flag and a label with the moment,
  // through refs, never state. The strip draws the flag where it landed
  // from the plan the server answers with.
  const paintMarker = useCallback((drag: Drag, at: number, snapped: number | null) => {
    const base = drag.marker;
    if (!base) return;
    const pps = ppsRef.current;
    const node = markerNodes.current.get(base.id);
    if (node) node.style.transform = `translateX(${(at - base.timeline_at) * pps}px)`;
    const label = markerLabelRef.current;
    if (label) {
      label.style.display = "";
      label.style.transform = `translateX(${at * pps}px)`;
      label.textContent = snapped === null ? timecode(at) : `${timecode(at)} ⌖`;
    }
  }, [markerLabelRef, ppsRef]);
  const clearMarkerDrag = useCallback(() => {
    markerNodes.current.forEach((node) => { node.style.transform = ""; });
    if (markerLabelRef.current) markerLabelRef.current.style.display = "none";
  }, [markerLabelRef]);

  // ── the markers: drop, name, move, remove, jump (E5b) ────────────────────
  //
  // A marker is a named moment of the picture's SOURCE (spec §13.2): every
  // change is the whole `markers` list in ONE PUT through `commitEdit`, the
  // tracks and the clips riding along unchanged - and a cut, a split or a
  // trim carries the markers along unchanged in turn, because nothing
  // rewrites a marker (trap 39). The rules live in lib/edit.ts (`newMarker`,
  // `renameMarker`, `moveMarker`, `markersAfterDelete`, `markerNeighbours`),
  // where they are tested. `keepSelection` is the gesture's own say on the
  // range selection (E6): a drop, a rename and a move leave it, a removal
  // clears it as every other edit does.
  const commitMarkers = useCallback((list: Marker[], keepSelection: boolean) => {
    if (editLockedRef.current) return;
    const before = committedRef.current;
    commitEdit({ video: before.video, narration: before.narration, music: before.music, markers: list }, keepSelection);
  }, [commitEdit, committedRef, editLockedRef]);

  /**
   * `M`: a marker at the playhead, named "Marker N", drawn at once with its
   * name box open - the PUT waits for the box (see `finishNameBox`), so the
   * keys typed into it can never reach the strip's own bindings.
   */
  const dropMarker = useCallback(() => {
    if (editLockedRef.current || pendingRef.current) return;
    const marker = newMarker(markersRef.current, positionRef.current, keepRef.current, sourceDurationRef.current);
    if (!marker) {
      setRefusal(`The markers are limited to ${MAX_MARKERS} — remove one first.`);
      return;
    }
    setPending(marker);
    setNameBox({ id: marker.id, draft: marker.name });
    setSelectedMarker(marker.id);
    setSelectedClip(null);
    setSelectedBlocks(NO_BLOCKS);
  }, [editLockedRef, keepRef, markersRef, positionRef, setRefusal, setSelectedBlocks, setSelectedClip, setSelectedMarker, sourceDurationRef]);

  /**
   * The name box closes: `keep` is Enter or a click elsewhere (the typed
   * name), else Escape. A PENDING marker lands either way - with the typed
   * name, or the default when the box was given up or emptied - in the one
   * PUT that adds it; an existing marker is renamed through `renameMarker`
   * only when the name really changed, and Escape reverts it. The typed
   * text goes through `sanitizeMarkerName` (a paste's tabs and newlines
   * dropped), and a job that took the project while the box was open is
   * said, not swallowed, on either path.
   */
  const finishNameBox = useCallback((keep: boolean) => {
    const box = nameBoxRef.current;
    if (!box) return;
    nameBoxRef.current = null;
    setNameBox(null);
    const held = pendingRef.current;
    if (held && held.id === box.id) {
      const name = keep ? sanitizeMarkerName(box.draft) || held.name : held.name;
      if (editLockedRef.current) {
        // A job took the project while the box was open: the marker was
        // never sent, so it must not stay drawn as though it had been.
        setPending(null);
        setRefusal("The marker was not added — wait for the last edit to be saved, then press M again.");
        return;
      }
      // The flag shows the name being saved while its save is in flight
      // (E6): drawn from the pending marker until the plan has it, which
      // held the default until then - "Marker N" for some 600 ms after
      // Enter (the R1d walk). Escape keeps the default, as the save does.
      if (name !== held.name) setPending({ ...held, name });
      commitMarkers(sortMarkers([...markersRef.current, { ...held, name }]), true);
      return;
    }
    if (!keep) return;
    const next = renameMarker(markersRef.current, box.id, sanitizeMarkerName(box.draft));
    if (next === markersRef.current) return;
    if (editLockedRef.current) {
      setRefusal("The name was not saved — wait for the last edit to be saved, then rename it again.");
      return;
    }
    commitMarkers(next, true);
  }, [commitMarkers, editLockedRef, markersRef, setRefusal]);

  /** Delete / Backspace with a marker selected: the marker goes, before a clip and before the range. */
  const removeMarker = useCallback(() => {
    const id = selectedMarkerRef.current;
    if (id === null || editLockedRef.current) return;
    commitMarkers(markersAfterDelete(markersRef.current, id), false);
  }, [commitMarkers, editLockedRef, markersRef, selectedMarkerRef]);

  /** Ctrl+[ / Ctrl+]: the playhead to the previous / next DRAWN marker, if there is one. */
  const jumpToMarker = useCallback((direction: 1 | -1) => {
    const { previous, next } = markerNeighbours(drawnMarkers(markersRef.current), positionRef.current);
    const to = direction < 0 ? previous : next;
    if (to !== null) seek(to);
  }, [seek, markersRef, positionRef]);

  /**
   * Down on a marker's flag (E5b): a MOVE along the ruler, snapped to the
   * one candidate set without its own moment; grabbing it selects it, and
   * gives up the clip and the blocks. The press must not reach the ruler,
   * whose own pointer-down is a scrub. Ctrl leaves the event alone (Ctrl+drag
   * is the range everywhere); the pointer is captured lazily by the body.
   */
  const onMarkerPointerDown = useCallback((event: ReactPointerEvent<HTMLButtonElement>, marker: DrawnMarker) => {
    if (event.ctrlKey || event.metaKey || event.button !== 0) return;
    event.stopPropagation();
    setSelectedMarker(marker.id);
    setSelectedClip(null);
    setSelectedBlocks(NO_BLOCKS);
    if (editLocked || pendingRef.current?.id === marker.id) return;  // the click still selects; a pending flag is not sent yet
    beginDrag(event, "marker", marker.timeline_at, marker.timeline_at, { marker, snapTo: snapTargetsNow(marker.id) });
  }, [beginDrag, editLocked, snapTargetsNow, setSelectedBlocks, setSelectedClip, setSelectedMarker]);
  /** A click on a flag SELECTS the marker and nothing else - no seek, as a clip's click seeks nothing. */
  const onMarkerClick = useCallback((event: MouseEvent<HTMLButtonElement>, marker: DrawnMarker) => {
    event.stopPropagation();
    if (suppressClick.current || event.ctrlKey || event.metaKey) return;
    setSelectedMarker(marker.id);
    setSelectedClip(null);
    setSelectedBlocks(NO_BLOCKS);
  }, [setSelectedBlocks, setSelectedClip, setSelectedMarker, suppressClick]);
  /** A double-click on a flag opens its name box: Enter renames, Escape reverts. */
  const onMarkerDoubleClick = useCallback((event: MouseEvent<HTMLButtonElement>, marker: DrawnMarker) => {
    event.stopPropagation();
    if (editLockedRef.current || pendingRef.current?.id === marker.id) return;
    setNameBox({ id: marker.id, draft: marker.name });
  }, [editLockedRef]);

  // ── the markers' drawing (E5b) ───────────────────────────────────────────
  //
  // The flags are the DRAWN markers - one in removed picture has no
  // `timeline_at` and is not on the ruler (trap 39) - plus the one `M` has
  // just dropped while the plan does not have it yet. The name box sits on
  // whichever flag is being named.
  const drawnFlags = drawnMarkers(storedMarkers);
  const flags: DrawnMarker[] = pending && !storedMarkers.some((held) => held.id === pending.id) ? [...drawnFlags, pending] : drawnFlags;
  const nameBoxAt = nameBox ? flags.find((held) => held.id === nameBox.id)?.timeline_at : undefined;
  /** A commit was refused (the stack's `afterCommitRef`, R1a): the marker `M` dropped, which the server never took, goes. */
  const dropPending = () => setPending(null);

  return {
    pending, nameBox, setNameBox, attachMarker, flags, nameBoxAt, commitMarkers, dropMarker, finishNameBox, removeMarker,
    jumpToMarker, paintMarker, clearMarkerDrag, onMarkerPointerDown, onMarkerClick, onMarkerDoubleClick, dropPending,
  };
}
