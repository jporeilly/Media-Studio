/**
 * The Music lane (R1c — the third part of the timeline component taken out of
 * `NarrationTimeline.tsx`; the design record is docs/porting/edit-timeline.md
 * §14): the library's open state, the clip nodes and the inspector's draft,
 * the waveform inside each clip, the painters a clip drag goes through
 * (`paintClip`, `clearClipDrag`), `commitMusic` — the one PUT every clip
 * gesture makes —, the studio's gain for a new clip, `addMusic`,
 * `removeClip`, `removeMissingClips`, `changeClip`, and the clip's two
 * pointer handlers. Every body here is the component's own, moved whole: the
 * closure variables each read are the deps the hook takes, the refs it alone
 * writes are created here and returned, and the callbacks keep their names,
 * so the root's handlers did not change. The clip branches of the body's two
 * pointer handlers stay in the root and call the painters and the commit
 * from here; the label's ref stays the root's (its release reads it) and is
 * handed in; `fileSecondsOf` closes over nothing and is a module export, as
 * `useEditGestures`' `drawn` is, for the root's trim branch to import. The
 * lane's and the inspector's markup are `MusicLane.tsx` and
 * `ClipInspector.tsx` beside this file.
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
import { api } from "../../../api/client";
import {
  CLIP_EDGE_PX,
  DEFAULT_MUSIC_GAIN,
  MAX_CLIPS,
  clipAt,
  clipEnd,
  clipLength,
  clipSnapTargets,
  clipsAfterDelete,
  fitFades,
  mintClipId,
  newMusicClip,
  withoutMissing,
  type ClipZone,
  type DragKind,
  type Join,
  type LaneLocks,
  type MusicClip,
} from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import { useStudioSettings } from "../../../lib/studioSettings";
import type { WaveformPeaks } from "../../../lib/timeline";
import type { MusicFile } from "../MusicLibrary";
import { NO_BLOCKS, type Drag, type EditState } from "./types";

export interface MusicLaneDeps {
  /** Only the open tab fetches the clips' waveforms. */
  active: boolean;
  /** The Music lane's clips as the plan holds them, their signature, and the distinct files (the audition's `musicFiles`). */
  musicRef: RefObject<MusicClip[]>;
  musicSignature: string;
  musicFiles: string[];
  /** The strip's locks: a locked Music lane refuses every gesture here. */
  locksRef: RefObject<LaneLocks>;
  /** The commit lock, as the value (`onClipPointerDown` takes it as a dependency) and as the ref every commit path reads. */
  editLocked: boolean;
  editLockedRef: RefObject<boolean>;
  /** The stack's one entry point, and what the server holds as far as the client knows. */
  commitEdit: (next: EditState) => void;
  committedRef: RefObject<EditState>;
  setRefusal: Dispatch<SetStateAction<string | null>>;
  /** The playhead, the picture's length, the pixels per second and the picture's joins - the root's refs. */
  positionRef: RefObject<number>;
  durationRef: RefObject<number>;
  ppsRef: RefObject<number>;
  joinsRef: RefObject<Join[]>;
  /** The drag primitives the clip's pointer-down goes through, and the flag a release raises against the click after it. */
  beginDrag: (
    event: ReactPointerEvent, kind: DragKind, anchor: number, grabbed?: number,
    move?: Pick<Drag, "index" | "members" | "snapTo" | "clip" | "zone" | "lane" | "pieceIndex" | "edge" | "marker">,
  ) => void;
  secondsAt: (clientX: number) => number;
  suppressClick: RefObject<boolean>;
  /** The three selections stay the root's; the lane sets its own and clears the other two. */
  selectedClip: string | null;
  setSelectedClip: Dispatch<SetStateAction<string | null>>;
  selectedClipRef: RefObject<string | null>;
  setSelectedMarker: Dispatch<SetStateAction<string | null>>;
  setSelectedBlocks: Dispatch<SetStateAction<ReadonlySet<number>>>;
  /** The dragged clip's label, the root's ref: the painters here write it, and the body's release hides it. */
  clipLabelRef: RefObject<HTMLDivElement | null>;
}


/** A clip's file length as the read-back reports it; `null` for a file the library has lost. */
export const fileSecondsOf = (clip: MusicClip): number | null => clip.file_duration ?? null;

export function useMusicLane({
  active, musicRef, musicSignature, musicFiles, locksRef, editLocked, editLockedRef, commitEdit, committedRef, setRefusal,
  positionRef, durationRef, ppsRef, joinsRef, beginDrag, secondsAt, suppressClick, selectedClip, setSelectedClip, selectedClipRef,
  setSelectedMarker, setSelectedBlocks, clipLabelRef,
}: MusicLaneDeps) {
  const [libraryOpen, setLibraryOpen] = useState(false);
  const libraryOpenRef = useRef(libraryOpen);
  libraryOpenRef.current = libraryOpen;
  /** The clip elements by id, for the drag's per-frame paint. */
  const clipNodes = useRef(new Map<string, HTMLButtonElement>());
  const attachClip = useCallback((id: string) => (node: HTMLButtonElement | null) => {
    if (node) clipNodes.current.set(id, node);
    else clipNodes.current.delete(id);
  }, []);
  /** The inspector's half-typed values, cleared when the selection or the plan moves on. */
  const [clipDraft, setClipDraft] = useState<{ gain?: number; fadeIn?: string; fadeOut?: string }>({});
  useEffect(() => { setClipDraft({}); }, [selectedClip, musicSignature]);
  // A clip the edit removed - by a cut, an undo, or a delete - is no longer selected.
  useEffect(() => {
    // The clips ride with their signature (`musicRef` is not reactive).
    setSelectedClip((held) => (held !== null && !musicRef.current.some((clip) => clip.id === held) ? null : held));
  }, [musicSignature, musicRef, setSelectedClip]);

  // ── the waveform inside each clip ────────────────────────────────────────
  //
  // `GET /api/music/{name}/peaks` is the cached JSON the library wrote at
  // upload - a few kilobytes, in the FILE's seconds - so it is fetched on the
  // first DRAW of a clip, once per file, and sliced per clip (`clipPeaks`).
  const [filePeaks, setFilePeaks] = useState<Record<string, WaveformPeaks>>({});
  const peaksAsked = useRef(new Set<string>());
  useEffect(() => {
    if (!active) return;
    let dropped = false;
    void (async () => {
      for (const name of musicFiles) {
        if (peaksAsked.current.has(name)) continue;
        peaksAsked.current.add(name);
        try {
          const answer = await api.get<WaveformPeaks>(`/api/music/${encodeURIComponent(name)}/peaks`);
          if (!dropped) setFilePeaks((held) => ({ ...held, [name]: answer }));
        } catch { /* no waveform for this clip: it still draws, plays and edits */ }
      }
    })();
    return () => { dropped = true; };
  }, [active, musicFiles]);

  /**
   * Paint a clip where the drag has it NOW - through the DOM, never through
   * state: a setState per pointer move would re-render every block and every
   * clip. A move is a transform; a trim rewrites the left and the width,
   * which is why `clearClipDrag` writes the plan's values back rather than
   * clearing them (React only re-writes a style it sees change).
   */
  const paintClip = useCallback((drag: Drag, next: MusicClip, snapped: number | null) => {
    const base = drag.clip;
    if (!base) return;
    const pps = ppsRef.current;
    const node = clipNodes.current.get(base.id);
    if (node) {
      if (drag.kind === "clip") node.style.transform = `translateX(${(next.at - base.at) * pps}px)`;
      else {
        node.style.left = `${next.at * pps}px`;
        node.style.width = `${Math.max(3, clipLength(next) * pps)}px`;
      }
    }
    const label = clipLabelRef.current;
    if (label) {
      label.style.display = "";
      label.style.transform = `translateX(${next.at * pps}px)`;
      const what = drag.kind === "clip" ? timecode(next.at) : `${timecode(next.at)} · ${clipLength(next).toFixed(2)} s`;
      label.textContent = snapped === null ? what : `${what} ⌖`;
    }
  }, [clipLabelRef, ppsRef]);

  /** Back to where the plan draws them: after a refusal, an unchanged release, or the plan that landed. */
  const clearClipDrag = useCallback(() => {
    const pps = ppsRef.current;
    for (const clip of musicRef.current) {
      const node = clipNodes.current.get(clip.id);
      if (!node) continue;
      node.style.transform = "";
      node.style.left = `${clip.at * pps}px`;
      node.style.width = `${Math.max(3, clipLength(clip) * pps)}px`;
    }
    if (clipLabelRef.current) clipLabelRef.current.style.display = "none";
  }, [clipLabelRef, musicRef, ppsRef]);

  /**
   * A clip gesture: the whole `music` list, with the two tracks and the
   * markers exactly as they stand, in ONE PUT on release (trap 8). The rest
   * rides along because every edit operation carries the whole edit — that
   * is what lets undo and redo put back a state rather than a fragment
   * (decision 10); `editBody` sends only the keys that changed.
   */
  const commitMusic = useCallback((clips: MusicClip[]) => {
    const before = committedRef.current;
    commitEdit({ video: before.video, narration: before.narration, music: clips, markers: before.markers });
  }, [commitEdit, committedRef]);

  // ── the music clips: add, remove, and the inspector's boxes ──────────────
  //
  // The studio's `music_volume` is a new clip's level (decision 2): the one
  // static bed under the voice the owner ruled on, never ducking; 0.15 when
  // the settings cannot be read.
  const studio = useStudioSettings();
  const studioGainRef = useRef<number | undefined>(undefined);
  studioGainRef.current = studio.data?.settings?.music_volume;

  /** The Library's "Add at playhead" (spec §12.5): the defaults of decision 2, committed at once. */
  const addMusic = useCallback((file: MusicFile) => {
    if (editLockedRef.current || locksRef.current.music) return;
    const clips = musicRef.current;
    if (clips.length >= MAX_CLIPS) {
      setRefusal(`The music is limited to ${MAX_CLIPS} clips — remove one first.`);
      return;
    }
    const clip = newMusicClip({
      id: mintClipId(clips.map((held) => held.id)),
      file: file.name,
      fileDuration: file.duration,
      at: positionRef.current,
      outputDuration: durationRef.current,
      gain: studioGainRef.current ?? DEFAULT_MUSIC_GAIN,
    });
    if (!clip) {
      setRefusal("There is no room for a clip at the playhead — move it earlier, or use a longer track.");
      return;
    }
    setLibraryOpen(false);
    setSelectedClip(clip.id);
    commitMusic([...clips, clip]);
  }, [commitMusic, durationRef, editLockedRef, locksRef, musicRef, positionRef, setRefusal, setSelectedClip]);

  /**
   * Delete / Backspace with a clip selected: the clip goes, not the range
   * selection. The rule for WHAT goes is `clipsAfterDelete` (lib/edit.ts),
   * where it is tested — the one clip, missing or not, since E4c lets the
   * server keep the other missing clips it already holds.
   */
  const removeClip = useCallback(() => {
    const id = selectedClipRef.current;
    if (id === null || editLockedRef.current || locksRef.current.music) return;
    commitMusic(clipsAfterDelete(musicRef.current, id));
  }, [commitMusic, editLockedRef, locksRef, musicRef, selectedClipRef]);

  /**
   * The banner's button: every clip whose file has gone, in one PUT. A
   * different gesture from Delete since E4c, with a rule of its own
   * (`withoutMissing`, lib/edit.ts). The guard is what keeps it honest once
   * the user has taken the banner's other way out and put the file back — the
   * plan refetches (the library's own mutations invalidate it,
   * `libraryChangeKeys`), nothing is missing any more, and this does nothing
   * rather than removing live clips (trap 37).
   */
  const removeMissingClips = useCallback(() => {
    if (editLockedRef.current || locksRef.current.music) return;
    const clips = musicRef.current;
    const kept = withoutMissing(clips);
    if (kept === clips) return;
    commitMusic(kept);
  }, [commitMusic, editLockedRef, locksRef, musicRef]);

  /** The inspector's boxes: one field of one clip, with the fades kept legal whatever is typed. */
  const changeClip = useCallback((id: string, patch: Partial<MusicClip>) => {
    if (editLockedRef.current || locksRef.current.music) return;
    const clips = musicRef.current;
    const held = clips.find((clip) => clip.id === id);
    if (!held) return;
    const next = { ...held, ...patch };
    const [fade_in, fade_out] = fitFades(clipLength(next), next.fade_in, next.fade_out);
    commitMusic(clips.map((clip) => (clip.id === id ? { ...next, fade_in, fade_out } : clip)));
  }, [commitMusic, editLockedRef, locksRef, musicRef]);

  /**
   * Down on a clip: a MOVE from its body, a TRIM from either 8 px edge —
   * which end the pointer has is `clipAt`'s answer, at the current zoom, so
   * the zones never eat a short clip's body. The snap candidates are
   * gathered once per drag (the playhead, the picture's joins, 0, the
   * picture's end and the other clips' ends); Ctrl drags freely, as
   * everywhere. The pointer is captured lazily by the body's move handler,
   * never here — an eager capture retargets the click that selects the clip.
   */
  const onClipPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>, clip: MusicClip) => {
    if (event.ctrlKey || event.metaKey || event.button !== 0) return;
    event.stopPropagation();
    if (editLocked || locksRef.current.music) return;  // the click still selects
    // Grabbing a clip selects it, as Camtasia's does: the inspector under the
    // strip is then already on the clip being dragged.
    setSelectedClip(clip.id);
    setSelectedMarker(null);
    setSelectedBlocks(NO_BLOCKS);
    const zone: ClipZone = clipAt([clip], secondsAt(event.clientX), CLIP_EDGE_PX / ppsRef.current)?.zone ?? "body";
    const snapTo = clipSnapTargets({
      playhead: positionRef.current,
      duration: durationRef.current,
      joins: joinsRef.current.map((join) => join.at),
      clips: musicRef.current,
      exclude: clip.id,
    });
    const grabbed = zone === "out" ? clipEnd(clip) : clip.at;
    beginDrag(event, zone === "body" ? "clip" : "trim", clip.at, grabbed, { clip, zone, snapTo });
  }, [beginDrag, editLocked, secondsAt, durationRef, joinsRef, locksRef, musicRef, positionRef, ppsRef, setSelectedBlocks, setSelectedClip, setSelectedMarker]);

  /**
   * A click on a clip SELECTS it, and does nothing else: the playhead, the
   * transport and any running playback are left exactly as they are.
   *
   * Deliberately unlike a sentence block, whose click also seeks (E3), and
   * the two are meant to differ (the owner's ruling, 2026-09-21). `seek`
   * halts playback and starts it again at the target, so seeking here would
   * jump the playhead back to the clip's start every time the inspector was
   * reached for — and adjusting a bed's level or its fades WHILE the audition
   * plays is the whole point of having the inspector under the strip.
   * Camtasia does not seek on a clip click either.
   */
  const onClipClick = useCallback((event: MouseEvent<HTMLButtonElement>, clip: MusicClip) => {
    event.stopPropagation();
    if (suppressClick.current || event.ctrlKey || event.metaKey) return;
    setSelectedClip(clip.id);
    setSelectedMarker(null);
    setSelectedBlocks(NO_BLOCKS);
  }, [setSelectedBlocks, setSelectedClip, setSelectedMarker, suppressClick]);

  return {
    libraryOpen, setLibraryOpen, libraryOpenRef, attachClip, clipDraft, setClipDraft, filePeaks, studioGainRef,
    addMusic, removeClip, removeMissingClips, changeClip, commitMusic, paintClip, clearClipDrag, onClipPointerDown, onClipClick,
  };
}
