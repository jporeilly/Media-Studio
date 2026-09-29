/**
 * The keyboard (R1d — the last part of the timeline component taken out of
 * `NarrationTimeline.tsx`; the design record is
 * docs/porting/edit-timeline.md §14): TechSmith's bindings, `K` held, and the
 * one listener on the document. The key map is the component's own closure,
 * moved whole into `keyAction`, which takes what that closure read and returns
 * the same `(event) => boolean`; the hook assigns it to its ref every render,
 * as the root assigned the closure, and binds the listener once per `active`.
 * The moves the keys make stay where they live - the audition's, the edit's,
 * the lanes', the markers', the root's - and are handed in; the hook returns
 * nothing.
 */
import { useEffect, useRef, type Dispatch, type RefObject, type SetStateAction } from "react";
import type { ShuttleKey } from "../../../lib/shuttle";
import { NO_BLOCKS, NUDGE_LARGE_SECONDS, NUDGE_SECONDS, type Selection } from "./types";

/** What the key map reads: the moves the keys make, the three selections and the range, and `K` held. */
export interface KeyActionDeps {
  /** The Library modal has the keyboard while it is open. */
  libraryOpenRef: RefObject<boolean>;
  /** `[` / `]`: the selected blocks' offsets (the edit's gestures). */
  nudge: (deltaSeconds: number) => void;
  /** The transport (the audition's). */
  togglePlay: () => void;
  stepBy: (direction: 1 | -1) => void;
  shuttleKey: (key: ShuttleKey) => void;
  seek: (to: number) => void;
  jumpToEnd: () => void;
  /** The three selections and the range - the root's state and the refs the map reads them through. */
  selectedMarkerRef: RefObject<string | null>;
  setSelectedMarker: Dispatch<SetStateAction<string | null>>;
  selectedClipRef: RefObject<string | null>;
  setSelectedClip: Dispatch<SetStateAction<string | null>>;
  selectedBlocksRef: RefObject<ReadonlySet<number>>;
  setSelectedBlocks: Dispatch<SetStateAction<ReadonlySet<number>>>;
  selectionRef: RefObject<Selection | null>;
  setSelection: Dispatch<SetStateAction<Selection | null>>;
  /** What Delete removes, in precedence: the marker, the clip, the range; and the split, the marker's drop and jump. */
  removeMarker: () => void;
  removeClip: () => void;
  cutSelection: () => void;
  splitAtPlayhead: (all: boolean) => void;
  dropMarker: () => void;
  jumpToMarker: (direction: 1 | -1) => void;
  /** The stack. */
  undo: () => void;
  redo: () => void;
  /** The selection's and the zoom's moves (the root's). */
  extendSelection: (direction: 1 | -1) => void;
  extendTo: (edge: "start" | "end") => void;
  zoomStep: (direction: 1 | -1) => void;
  zoomFit: () => void;
  zoomSelection: () => void;
  zoomAll: () => void;
  /** `K` is down: `J` and `L` step a frame instead of shuttling (the hook's own ref). */
  kHeldRef: RefObject<boolean>;
}

/** What the hook takes: the key map's deps without `K` held, which the hook owns, and whether the tab is open. */
export type TimelineKeysDeps = Omit<KeyActionDeps, "kHeldRef"> & { active: boolean };

/**
 * The key map: what one keydown does, and whether it was handled (the
 * listener prevents the browser's own default for every key that was). The
 * component's closure, moved whole (R1d): its body is the one the root
 * assigned to its ref every render, and what that closure read is `deps`.
 */
export function keyAction(deps: KeyActionDeps): (event: KeyboardEvent) => boolean {
  const {
    libraryOpenRef, nudge, togglePlay, stepBy, selectedMarkerRef, setSelectedMarker, selectedClipRef, setSelectedClip,
    selectedBlocksRef, setSelectedBlocks, selectionRef, setSelection, removeMarker, removeClip, cutSelection, splitAtPlayhead,
    dropMarker, kHeldRef, shuttleKey, extendSelection, jumpToMarker, undo, redo, seek, jumpToEnd, extendTo, zoomStep, zoomFit,
    zoomSelection, zoomAll,
  } = deps;
  return (event) => {
    // The Library modal has the keyboard while it is open: its own Escape
    // closes it, and Space must not play behind it.
    if (libraryOpenRef.current) return false;
    const ctrl = event.ctrlKey || event.metaKey;
    const shift = event.shiftKey;
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    const code = event.code;
    // The nudge keys, by the physical key (the two right of P) OR by what it
    // typed: on a QWERTZ layout `[` is AltGr+8 - Alt AND Ctrl held, as
    // Windows reports AltGr - so these are the one exception to the Alt
    // bail-out below, and to "plain Ctrl+[ / ] are Camtasia's marker keys,
    // untouched". `{` / `}` (Shift on a US layout, AltGr+7 / AltGr+0 on
    // QWERTZ) are the quarter-second step.
    const bracket = code === "BracketLeft" || key === "[" || key === "{" ? -1
      : code === "BracketRight" || key === "]" || key === "}" ? 1 : 0;
    if (event.altKey && bracket === 0) return false;
    const once = !event.repeat;
    if (bracket !== 0 && (!ctrl || event.altKey)) {
      const large = shift || key === "{" || key === "}";
      if (once) nudge(bracket * (large ? NUDGE_LARGE_SECONDS : NUDGE_SECONDS));
      return true;
    }
    // Space by `key` as well as by `code`: virtual keyboards and automation
    // set the one without the other.
    const space = code === "Space" || key === " ";
    // Play/pause, a cut and undo/redo fire ONCE per press: held down they
    // would toggle at the repeat rate and fire an undo per repeat (which is
    // how the commit race above was hit by accident). The repeat is still
    // swallowed, so a held Space cannot scroll the page either. Frame
    // stepping and the selection keys keep repeating - holding them is how
    // they are used.
    // The nudge keys and the split fire once per press too: a held `]`
    // would be a request per repeat.
    if (!ctrl && !shift) {
      if (space) { if (once) togglePlay(); return true; }
      if (code === "Comma") { stepBy(-1); return true; }
      if (code === "Period") { stepBy(1); return true; }
      // Escape clears the marker first, then the music clip, then the block
      // selection, then (a fourth press) the range - a selection of a thing
      // wins over the range while it exists.
      if (key === "Escape") {
        if (selectedMarkerRef.current !== null) { setSelectedMarker(null); return true; }
        if (selectedClipRef.current !== null) { setSelectedClip(null); return true; }
        if (selectedBlocksRef.current.size > 0) { setSelectedBlocks(NO_BLOCKS); return true; }
        if (!selectionRef.current) return false;
        setSelection(null);
        return true;
      }
      // Camtasia's plain Delete leaves space on the timeline; this one has no
      // gaps (the edit is ranges of one source), so it closes the gap too.
      // With a marker selected these keys remove THE MARKER; with a music
      // clip selected, THE CLIP (Camtasia's "delete selected media"): the
      // marker wins, then the clip, then the range, and Escape is how each
      // is given up.
      if (key === "Backspace" || key === "Delete") {
        if (once) {
          if (selectedMarkerRef.current !== null) removeMarker();
          else if (selectedClipRef.current !== null) removeClip();
          else cutSelection();
        }
        return true;
      }
      // TechSmith's S: split the selected / unlocked tracks at the playhead.
      if (code === "KeyS" || key === "s") { if (once) splitAtPlayhead(false); return true; }
      // Camtasia's M: a marker at the playhead, its name box open (E5b).
      if (code === "KeyM" || key === "m") { if (once) dropMarker(); return true; }
      // J / K / L (E5c): the shuttle - or, with K held, a frame step. Once
      // per press: held, L would climb 2×, 4×, 8× at the repeat rate. K's
      // repeats keep its flag up while it is down (its keyup clears it), and
      // the step under it repeats as Comma and Period do.
      if (code === "KeyK" || key === "k") { kHeldRef.current = true; if (once) shuttleKey("K"); return true; }
      if (code === "KeyJ" || key === "j") { if (kHeldRef.current) stepBy(-1); else if (once) shuttleKey("J"); return true; }
      if (code === "KeyL" || key === "l") { if (kHeldRef.current) stepBy(1); else if (once) shuttleKey("L"); return true; }
      return false;
    }
    if (shift && !ctrl) {
      if (code === "Comma") { extendSelection(-1); return true; }
      if (code === "Period") { extendSelection(1); return true; }
      return false;
    }
    if (ctrl && !shift) {
      // Camtasia's Ctrl+[ / Ctrl+]: the previous / next marker (E5b). Plain
      // [ and ] are E3's nudge, handled above; AltGr's Ctrl+Alt is the nudge
      // too, so this is reached only with Ctrl alone.
      if (bracket !== 0) { jumpToMarker(bracket); return true; }
      if (key === "z") { if (once) undo(); return true; }
      if (key === "y") { if (once) redo(); return true; }
      if (key === "x" || key === "Delete") {
        if (once) {
          if (selectedMarkerRef.current !== null) removeMarker();
          else if (selectedClipRef.current !== null) removeClip();
          else cutSelection();
        }
        return true;
      }
      if (key === "Home") { seek(0); return true; }
      if (key === "End") { jumpToEnd(); return true; }
      return false;
    }
    // Ctrl+Shift
    if (key === "z") { if (once) redo(); return true; }
    if (key === "d") { setSelection(null); return true; }
    // Ctrl+Shift+S: split every track at the playhead, locks or not.
    if (code === "KeyS" || key === "s") { if (once) splitAtPlayhead(true); return true; }
    if (key === "Home") { extendTo("start"); return true; }
    if (key === "End") { extendTo("end"); return true; }
    if (code === "Equal" || code === "NumpadAdd" || key === "+" || key === "=") { zoomStep(1); return true; }
    if (code === "Minus" || code === "NumpadSubtract" || key === "-" || key === "_") { zoomStep(-1); return true; }
    if (code === "Digit7") { zoomFit(); return true; }
    if (code === "Digit8") { zoomSelection(); return true; }
    if (code === "Digit9") { zoomAll(); return true; }
    return false;
  };
}

export function useTimelineKeys({ active, ...keys }: TimelineKeysDeps): void {
  // ── the keyboard ─────────────────────────────────────────────────────────
  //
  // TechSmith's own bindings, one listener on the document while this view is
  // open, ignored inside anything typed into (the List tab's boxes, the
  // Re-voice card's fields). Every key handled is prevented: Space must not
  // scroll the page and Backspace must not navigate. The handler lives in a
  // ref so the listener is bound once per `active` rather than on every
  // render.
  /** `K` is down: `J` and `L` step a frame instead of shuttling, until its keyup (or the window loses focus). */
  const kHeldRef = useRef(false);
  const keyHandler = useRef<(event: KeyboardEvent) => boolean>(() => false);
  keyHandler.current = keyAction({ ...keys, kHeldRef });
  useEffect(() => {
    if (!active) return;
    const typing = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      return !!target?.closest?.("input, textarea, select, [contenteditable]:not([contenteditable='false'])");
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.isComposing || event.defaultPrevented || typing(event)) return;
      if (keyHandler.current(event)) event.preventDefault();
    };
    // Space while a button has focus (Play keeps it after a mouse click): in
    // Chromium a default-prevented keydown never arms the button, so its
    // keyup dispatches no click and Space toggles exactly once. Firefox
    // dispatches the button's click on keyup regardless, which would toggle
    // twice - so the keyup is prevented there too, under the same rules.
    const onKeyUp = (event: KeyboardEvent) => {
      // K released: J and L shuttle again (E5c). Cleared wherever the keyup
      // lands, since the flag was raised outside any box.
      if (event.code === "KeyK" || event.key === "k" || event.key === "K") kHeldRef.current = false;
      if ((event.code !== "Space" && event.key !== " ") || typing(event)) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.("button, a")) event.preventDefault();
    };
    // A K held while the window loses focus never sees its keyup.
    const onBlur = () => { kHeldRef.current = false; };
    document.addEventListener("keydown", onKey);
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      kHeldRef.current = false;
    };
  }, [active]);
}
