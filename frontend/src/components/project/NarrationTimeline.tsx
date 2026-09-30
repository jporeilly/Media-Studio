import {
  memo,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AudioLines,
  ChevronRight,
  Eye,
  EyeOff,
  Lock,
  Magnet,
  Maximize2,
  Mic,
  Music,
  Pause,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Scissors,
  Search,
  SkipBack,
  SkipForward,
  SquareSplitHorizontal,
  StepBack,
  StepForward,
  Trash2,
  Undo2,
  Unlock,
  Video,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { api, errorMessage, qs } from "../../api/client";
import { timecode } from "../../lib/format";
import {
  CLIP_EDGE_PX,
  EPSILON,
  FRAME_SECONDS,
  anchoredScrollLeft,
  atFrameFloor,
  caughtAfterClamp,
  clickSelectsPiece,
  clipEnd,
  clipLength,
  canCut,
  drawnMarkers,
  LANES,
  MAX_CLIPS,
  snapClip,
  describeJoin,
  dragOffsets,
  joins,
  laneLocks,
  maxZoom,
  missingCutByPicture,
  moveClip,
  moveMarker,
  musicAfterTrim,
  nextEditForTrim,
  pictureCutRefusal,
  pieceAt,
  pieceEdgeAt,
  pieces,
  projectPeaks,
  releaseSuppressesClick,
  round3,
  sameMarkers,
  sameMusic,
  sliderFromZoom,
  snap,
  snapTargets,
  stepZoom,
  ticks,
  toSource,
  trimChange,
  trimClip,
  trimLabel,
  trimPiece,
  unmovedRelease,
  wholeKeep,
  zoomFromSlider,
  zoomToSelection,
  type DragKind,
  type Join,
  type Keep,
  type Lane,
  type LaneLocks,
  type Marker,
  type MusicClip,
  type Piece,
  type Track,
  type TrimChange,
} from "../../lib/edit";
import {
  clampTime,
  frameTimes,
  narrationPlanKey,
  needsNewFrames,
  pixelsPerSecond,
  poolPeaks,
  pxToSeconds,
  thumbCount,
  waveformPath,
  type NarrationPlan,
  type PlanSentence,
  type WaveformPeaks,
} from "../../lib/timeline";
import { Button, ErrorBox, Spinner } from "../ui";
import { MusicLibrary } from "./MusicLibrary";
import { ClipInspector } from "./timeline/ClipInspector";
import { MarkerRuler } from "./timeline/MarkerRuler";
import { MusicLane } from "./timeline/MusicLane";
import { NO_BLOCKS, type Drag, type EditState, type SavedSentence, type Selection } from "./timeline/types";
import { useAudition } from "./timeline/useAudition";
import { type AfterCommit, useEditCommits } from "./timeline/useEditCommits";
import { drawn, useEditGestures } from "./timeline/useEditGestures";
import { useMarkers } from "./timeline/useMarkers";
import { fileSecondsOf, useMusicLane } from "./timeline/useMusicLane";
import { useTimelineKeys } from "./timeline/useTimelineKeys";

interface Props {
  projectId: string;
  /** The Re-voice card's selection: the plan is "as THAT re-voice would speak it". */
  provider: string;
  voiceId: string;
  speed: number;
  /** Only the open tab fetches — a plan costs the voice service a calibration round trip. */
  active: boolean;
  /** The row the list view has selected, so the two views agree on "this sentence". */
  selected: number | null;
  onSelect: (index: number) => void;
  /** A job holds the project: every write of the edit is a 409, so Cut, Undo and Redo wait for it. */
  jobActive: boolean;
  /**
   * The STORED offsets, by index (null = none), from the page's copy of the
   * transcript: what undo goes back to after a drag. The plan's
   * `pinned_start − start` cannot serve — it is floored at 0.
   */
  offsets: Record<number, number | null>;
  /** A drag or a nudge saved: the parent folds the sentences into its copy, as the List's adjust does. */
  onOffsetsSaved: (updated: SavedSentence[]) => void;
}

/**
 * One sentence as `PATCH /narration/offsets` returns it: the stored segment
 * with its index. Declared beside the edit's own types since R1a
 * (timeline/types.ts) and re-exported here for the page; `Selection` and
 * `EditState` come from the same file.
 */
export type { SavedSentence } from "./timeline/types";
/**
 * Camtasia's locks per editable LANE (lib/edit.ts): the two tracks and, since
 * E4, the Music lane. Audio · original has no list of its own: it follows
 * Video.
 */
type Locks = LaneLocks;

/** How long to wait on the browser for one frame before giving up on the filmstrip. */
const FRAME_TIMEOUT_MS = 6000;
/** Captured small and scaled up by CSS — a filmstrip lane is 68px tall. */
const THUMB_W = 160;
const THUMB_H = 90;
/** Roughly one frame per this many pixels of strip: the lane is taller than 3a's, so wider thumbs crop less. */
const THUMB_PX = 120;
/** A pointer that has travelled less than this is a click, not a drag. */
const DRAG_SLOP_PX = 3;
/** A dragged block snaps to a candidate within this many pixels at the current zoom, as Camtasia's do. */
const SNAP_PX = 8;
/** Where a project's lock state is remembered: the client's, per visit, never on the record (decision 2 of §11.7). */
const locksKey = (projectId: string) => `ms:tl-locks:${projectId}`;
/** Where the magnet is remembered, beside the locks (E5a, spec §13.4): on by default, off only when it was switched off. */
const snapKey = (projectId: string) => `ms:tl-snap:${projectId}`;
const UNLOCKED: Locks = { video: false, narration: false, music: false };

const NO_MUSIC: MusicClip[] = [];
const NO_MARKERS: Marker[] = [];

/**
 * The stored locks. A value written before the Music lane existed has two
 * keys, and a missing key reads as UNLOCKED (`laneLocks`) — a project last
 * edited under E3 must not come back with its Music lane locked.
 */
function readLocks(projectId: string): Locks {
  try {
    return laneLocks(JSON.parse(localStorage.getItem(locksKey(projectId)) ?? "null"));
  } catch { /* no storage, or not ours: unlocked */ }
  return UNLOCKED;
}

/** A callback ref per named layer that keeps `into` current — built once, so React is not handed a new ref every render. */
function layerRefs(
  layers: readonly string[], into: { current: Map<string, HTMLDivElement> },
): Record<string, (node: HTMLDivElement | null) => void> {
  const out: Record<string, (node: HTMLDivElement | null) => void> = {};
  for (const layer of layers) {
    out[layer] = (node) => {
      if (node) into.current.set(layer, node);
      else into.current.delete(layer);
    };
  }
  return out;
}

/** The stored magnet: anything but a stored `false` reads as ON — a project never switched off comes back snapping. */
function readSnapping(projectId: string): boolean {
  try {
    return localStorage.getItem(snapKey(projectId)) !== "false";
  } catch { /* no storage: on, then */ }
  return true;
}

/**
 * The ruler's tick marks. Its own component so that scrolling re-renders
 * these hundred-odd spans and nothing else: it draws only the window on
 * screen (plus one screen each side, re-measured when the scroll leaves that
 * margin), because a zoomed two-hour strip has tens of thousands of minor
 * ticks and a node for each would be felt on every zoom.
 */
const Ruler = memo(function Ruler({ total, pps, scrollEl }: { total: number; pps: number; scrollEl: HTMLDivElement | null }) {
  const [view, setView] = useState({ from: 0, to: Infinity });
  useEffect(() => {
    if (!scrollEl) return;
    let drawn = { from: Infinity, to: -Infinity };
    const update = () => {
      const from = scrollEl.scrollLeft;
      const to = from + scrollEl.clientWidth;
      if (from >= drawn.from && to <= drawn.to) return;
      const margin = Math.max(200, scrollEl.clientWidth);
      drawn = { from: from - margin, to: to + margin };
      setView(drawn);
    };
    update();
    scrollEl.addEventListener("scroll", update, { passive: true });
    return () => scrollEl.removeEventListener("scroll", update);
    // `pps` and `total` are dependencies on purpose: a zoom moves the window in seconds.
  }, [scrollEl, pps, total]);
  const marks = useMemo(() => ticks(total, pps, view.from / pps, view.to / pps), [total, pps, view]);
  return (
    <div className="os-tl-ticks" aria-hidden="true">
      {marks.map((mark) => (
        <span key={mark.at} className={mark.major ? "os-tl-tick major" : "os-tl-tick"} style={{ left: mark.at * pps }}>
          {mark.major && <em>{mark.label}</em>}
        </span>
      ))}
    </div>
  );
});

/**
 * The timeline view of the transcript, in the shape of Camtasia's: a time
 * ruler, track headers, a filmstrip of the video, the waveform of its
 * original audio and one block per sentence on ONE horizontal time scale — a
 * transport that plays the NEW narration against the picture without
 * rendering anything, and (since E2) the one editing gesture: select a range
 * with the playhead's green and red handles, Cut, and the gap closes.
 *
 * This exists because the only way to hear a timing change used to be a full
 * re-voice: six of them in twelve minutes on one video, with no visual
 * reference for how far to nudge a sentence. So the point of the view is not
 * the drawing, it is that Play is honest. The clips it fetches are the very
 * cache entries the render will reuse — `preview_url` carries the effective
 * voice and speed the server computed for each sentence — and they are placed
 * by the same rule `assemble_master` uses: a pin is a floor, so a clip that
 * runs long pushes the next one late instead of overlapping it.
 *
 * **Everything is drawn in TIMELINE seconds** — the output's, with the removed
 * ranges closed up. The plan's sentences arrive projected; the filmstrip
 * seeks the source video to `toSource(t)`; the waveform's peaks are sliced by
 * `keep` before pooling. The edit itself is only ever the kept lists, sent
 * whole on release; the transcript on disk never moves (spec §3).
 *
 * **Two lists, one axis** (E3, spec §11). The Video lane (and the original
 * audio, which follows it) is drawn through the VIDEO list; the Narration
 * lane through the NARRATION list; both start at the same zero. Camtasia's
 * lock icons say which lanes a Cut or a split applies to — clicking a
 * lane's name selects that channel and locks the others (E4b made that two
 * others, and the Music lane rides the picture rather than cutting on its
 * own: §12.5) — and a locked lane is left exactly as it is (trap 19). A sentence block can be
 * dragged along its lane, and that commits its OFFSET and nothing else
 * (trap 20): one request for every block that moved, on release.
 */
export function NarrationTimeline({
  projectId, provider, voiceId, speed, active, selected, onSelect, jobActive, offsets, onOffsetsSaved,
}: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** The scroll container as STATE too, for the ruler to subscribe to. */
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  /** Held so the callback ref can disconnect the previous observer. */
  const observerRef = useRef<ResizeObserver | null>(null);
  const [width, setWidth] = useState(0);
  const widthRef = useRef(0);
  widthRef.current = width;
  /** Continuous, 1 = the whole edit fits the strip; clamped below to `maxZoom`. */
  const [zoom, setZoom] = useState(1);

  const plan = useQuery({
    queryKey: [...narrationPlanKey(projectId), provider, voiceId, speed],
    queryFn: () => api.get<NarrationPlan>(
      `/api/projects/${projectId}/narration/plan${qs({ provider, voice: voiceId, speed })}`,
    ),
    enabled: active,
    staleTime: 60_000,
    // The key carries the Re-voice card's provider, voice and speed, so a
    // change to any of them is a NEW query - and without this its data is
    // undefined until the answer lands. That emptiness reached everything
    // below: `sentences` became [], the plan signature became "[]", the
    // reset effect dropped every decoded clip and halted playback at zero,
    // then the new plan arrived - identical, as often as not - and the
    // audition had been destroyed for nothing. Keeping the previous plan
    // until the next one is here means the signature only moves when the
    // plan really differs, which is the only time a reset is right. It is
    // also what keeps the strip on the LAST plan while a cut is being
    // re-read: the drawing is always a plan the server made, never a keep
    // the client has not yet had confirmed.
    placeholderData: keepPreviousData,
  });

  // The peaks never change for a given audio.wav (the server caches them on its
  // mtime and size), so this is fetched once per project and kept. They are in
  // SOURCE seconds; the client projects them (`projectPeaks`).
  const peaks = useQuery({
    queryKey: ["waveform", projectId],
    queryFn: () => api.get<WaveformPeaks>(`/api/projects/${projectId}/waveform`),
    enabled: active,
    staleTime: Infinity,
    retry: false,
  });

  const sentences = useMemo(() => plan.data?.sentences ?? [], [plan.data]);
  /** Read by the drags' stable callbacks (the snap candidates are the drawn blocks' ends). */
  const sentencesRef = useRef(sentences);
  sentencesRef.current = sentences;
  // The OUTPUT's length when there is a cut - the plan already says so.
  const duration = plan.data?.duration ?? peaks.data?.duration ?? 0;
  const durationRef = useRef(duration);
  durationRef.current = duration;
  // ── the edit ─────────────────────────────────────────────────────────────
  //
  // From the plan, never from a separate GET: the sentences were projected
  // through exactly these lists, and a strip drawn from a plan of one moment
  // and an edit of another would put the blocks over the wrong frames.
  const storedVideo: Keep | null = plan.data?.edit?.video?.keep ?? null;
  const storedNarration: Keep | null = plan.data?.edit?.narration?.keep ?? null;
  // The Music lane's clips, from the same fetch: each already carries the
  // library's `file_duration` and whether the file has gone (`missing`).
  const storedMusic: MusicClip[] = plan.data?.edit?.music ?? NO_MUSIC;
  const musicSignature = JSON.stringify(storedMusic);
  const musicRef = useRef(storedMusic);
  musicRef.current = storedMusic;
  /**
   * The clips whose file the library has lost. The server keeps a clip it
   * already holds with its file gone (E4c: "you may keep what you have, you
   * may not add what is not there" - `services/edit.py::_check_music`), so
   * such a clip can still be moved, levelled, faded and deleted; what it
   * cannot be is trimmed, because its slice is of a length nobody knows, and
   * the render refuses while any is there. The banner names them, offers to
   * remove them all in one commit, and says the file can be put back instead.
   */
  const missingMusic = storedMusic.filter((clip) => clip.missing);
  // The markers (E5b), from the same fetch: each in SOURCE seconds with
  // `timeline_at`, where the picture's list lands it - `null` for one in
  // removed picture, which the ruler does not draw (trap 39).
  const storedMarkers: Marker[] = plan.data?.edit?.markers ?? NO_MARKERS;
  const markersSignature = JSON.stringify(storedMarkers);
  const markersRef = useRef(storedMarkers);
  markersRef.current = storedMarkers;
  // What the server holds, as far as this client knows: the plan's lists,
  // advanced by every commit that SUCCEEDS. The undo stack snapshots THIS,
  // never the plan's copy - which is still the previous edit until the
  // refetch lands - so the history is right even if the lock below were
  // ever bypassed. The same for the offsets: the page's stored copy,
  // advanced by every drag that lands.
  const committedRef = useRef<EditState>({ video: storedVideo, narration: storedNarration, music: storedMusic, markers: storedMarkers });
  useEffect(() => {
    // The clips and the markers come in the same fetch, so their signatures are what move here.
    committedRef.current = { video: storedVideo, narration: storedNarration, music: musicRef.current, markers: markersRef.current };
  }, [storedVideo, storedNarration, musicSignature, markersSignature]);
  const committedOffsetsRef = useRef(offsets);
  useEffect(() => { committedOffsetsRef.current = offsets; }, [offsets]);
  const sourceDuration = plan.data?.edit?.source_duration ?? duration;
  const sourceDurationRef = useRef(sourceDuration);
  sourceDurationRef.current = sourceDuration;
  /**
   * The PICTURE's axis - what the filmstrip, the waveform, the transport's
   * seeks and the playhead's remapping project through: the video list, or
   * the whole source. Named `keep` since E2, when it was the only list.
   */
  const keep = useMemo<Keep>(
    () => (storedVideo && storedVideo.length > 0 ? storedVideo : wholeKeep(sourceDuration)),
    [storedVideo, sourceDuration],
  );
  const keepSignature = JSON.stringify(keep);
  const keepRef = useRef(keep);
  keepRef.current = keep;
  /** The NARRATION lane's axis: its own list, or the whole source. Never the picture's (trap 18). */
  const narrationKeep = useMemo<Keep>(
    () => (storedNarration && storedNarration.length > 0 ? storedNarration : wholeKeep(sourceDuration)),
    [storedNarration, sourceDuration],
  );
  const narrationKeepRef = useRef(narrationKeep);
  narrationKeepRef.current = narrationKeep;
  // The joins per lane: the video's on the Video and Audio lanes (the
  // transport re-seeks the picture at these), the narration's on its own.
  const joinList = useMemo(() => joins(keep, sourceDuration), [keep, sourceDuration]);
  const joinsRef = useRef<Join[]>(joinList);
  joinsRef.current = joinList;
  const narrationJoins = useMemo(() => joins(narrationKeep, sourceDuration), [narrationKeep, sourceDuration]);
  const narrationJoinsRef = useRef<Join[]>(narrationJoins);
  narrationJoinsRef.current = narrationJoins;
  // The pieces per lane - the stretches between boundaries, what a click selects.
  const videoPieces = useMemo(() => pieces(keep, sourceDuration), [keep, sourceDuration]);
  const videoPiecesRef = useRef(videoPieces);
  videoPiecesRef.current = videoPieces;
  const narrationPieces = useMemo(() => pieces(narrationKeep, sourceDuration), [narrationKeep, sourceDuration]);
  const narrationPiecesRef = useRef(narrationPieces);
  narrationPiecesRef.current = narrationPieces;

  // ── the locks ────────────────────────────────────────────────────────────
  //
  // Camtasia's lock icons: a locked track's list is left exactly as it is by
  // a Cut or a split (trap 19). Clicking a track's NAME selects that channel
  // - the owner's phrase made literal: it locks the OTHER track - and
  // clicking the selected name again unlocks both; the icons still toggle one
  // at a time. The client's, per project and per visit: remembered in
  // localStorage so a reload keeps it, never on the record (it is a gesture
  // modifier, and the durable thing is the edit it produces).
  const [locks, setLocks] = useState<Locks>(() => readLocks(projectId));
  useEffect(() => { setLocks(readLocks(projectId)); }, [projectId]);
  const locksRef = useRef(locks);
  locksRef.current = locks;
  const changeLocks = useCallback((next: Locks) => {
    setLocks(next);
    try { localStorage.setItem(locksKey(projectId), JSON.stringify(next)); } catch { /* no storage: per visit, then */ }
  }, [projectId]);
  const toggleLock = useCallback((lane: Lane) => {
    changeLocks({ ...locksRef.current, [lane]: !locksRef.current[lane] });
  }, [changeLocks]);
  /** The channel whose name is selected: the one unlocked lane while the other two are locked. */
  const unlocked = LANES.filter((lane) => !locks[lane]);
  const channel: Lane | null = unlocked.length === 1 ? unlocked[0] : null;
  /**
   * E3's rule, generalised to three lanes: clicking a name selects that
   * channel — it locks the OTHERS — and clicking the selected name again
   * unlocks everything. The lock icons still toggle one lane at a time.
   */
  const selectChannel = useCallback((lane: Lane) => {
    const held = locksRef.current;
    const alone = !held[lane] && LANES.every((other) => other === lane || held[other]);
    changeLocks(alone ? UNLOCKED : { video: true, narration: true, music: true, [lane]: false });
  }, [changeLocks]);

  // ── the magnet (E5a, spec §13.4) ─────────────────────────────────────────
  //
  // Camtasia's toolbar toggle: it governs EVERY snapping gesture - the green
  // and red handles, a Ctrl+drag range's ends, a piece's trimmed edge, E3's
  // block drag and E4b's clip drag. On by default, remembered per project in
  // localStorage beside the locks. Holding Alt during a drag turns it off for
  // that drag alone (Ctrl cannot: Ctrl+drag IS the range gesture; the block
  // and clip drags keep Ctrl as a synonym).
  const [snapping, setSnapping] = useState<boolean>(() => readSnapping(projectId));
  useEffect(() => { setSnapping(readSnapping(projectId)); }, [projectId]);
  const snappingRef = useRef(snapping);
  snappingRef.current = snapping;
  const toggleSnapping = useCallback(() => {
    const next = !snappingRef.current;
    setSnapping(next);
    try { localStorage.setItem(snapKey(projectId), JSON.stringify(next)); } catch { /* no storage: per visit, then */ }
  }, [projectId]);

  // The render's own tempo-squeeze constants, never literals here: see
  // `renderedLength` in lib/timeline.ts.
  const squeeze = useMemo(() => ({
    tolerance: plan.data?.squeeze_tolerance ?? Infinity,
    maxFactor: plan.data?.squeeze_max_factor ?? 0,
  }), [plan.data]);
  // Which sentences the render will speak is the SERVER's answer (`speakable`),
  // never re-derived here: "has words" is str.strip() there and would be
  // String.trim() here, and those are different character sets — a pasted
  // control character would be auditioned by the browser and refused with a 400
  // by the preview route, surfacing as a failure with no visible cause. It also
  // covers a sentence whose offset pins it past the end of the video, which the
  // mux drops.
  const speakable = useMemo(() => sentences.filter((s) => s.speakable), [sentences]);

  // ── the audition is `useAudition` (R1b, timeline/useAudition.ts) ─────────
  //
  // The decoded clips, the music lane's audition, where each clip lands, the
  // transport and the shuttle were declared here; the hook is called below,
  // once the selection's refs and the drag record it paints through exist.
  // What stays here: the two refs the strip's own stable callbacks read -
  // the playhead, which the hook writes on every halt, start, frame and
  // key (and the scrub writes as before), and the audition's length, which
  // the hook alone writes. (The one ref only the keyboard reads, `K` held,
  // is `useTimelineKeys`' since R1d.)
  const positionRef = useRef(0);
  const totalRef = useRef(0);

  // ── the selection ────────────────────────────────────────────────────────
  //
  // State is the COMMITTED selection: what Cut removes, what the toolbar
  // enables on. While a handle is being dragged the band is painted through
  // `selectionRef`, exactly as the playhead is, and the state is set once on
  // release - a setState per pointer move would re-render every block.
  const [selection, setSelection] = useState<Selection | null>(null);
  const selectionRef = useRef<Selection | null>(null);
  const inHandleRef = useRef<HTMLDivElement | null>(null);
  const outHandleRef = useRef<HTMLDivElement | null>(null);
  const bandRef = useRef<HTMLDivElement | null>(null);
  const inLabelRef = useRef<HTMLSpanElement | null>(null);
  const outLabelRef = useRef<HTMLSpanElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<Drag | null>(null);
  /** Set by a pointer gesture so the click the browser fires after it does not seek a second time. */
  const suppressClick = useRef(false);
  // The selected BLOCKS - what a drag moves and the nudge keys act on -
  // beside the one "chosen" sentence the List view shares (`selected`). A
  // click chooses one and selects it alone; Ctrl+click toggles; Shift+click
  // extends from the last plain click; a marquee on empty lane space selects
  // what it crosses. Held as a Set in state, painted as a class per block.
  const [selectedBlocks, setSelectedBlocks] = useState<ReadonlySet<number>>(NO_BLOCKS);
  const selectedBlocksRef = useRef(selectedBlocks);
  selectedBlocksRef.current = selectedBlocks;
  const blockAnchorRef = useRef<number | null>(null);
  /** The block elements by index, for the move drag's per-frame transform. */
  const blockNodes = useRef(new Map<number, HTMLButtonElement>());
  const attachBlock = useCallback((index: number) => (node: HTMLButtonElement | null) => {
    if (node) blockNodes.current.set(index, node);
    else blockNodes.current.delete(index);
  }, []);
  const moveLabelRef = useRef<HTMLDivElement | null>(null);
  const marqueeRef = useRef<HTMLDivElement | null>(null);
  /** One outline per block at the position it is being dragged FROM, shown only while it moves. */
  const dragGhosts = useRef(new Map<number, HTMLDivElement>());
  const attachDragGhost = useCallback((index: number) => (node: HTMLDivElement | null) => {
    if (node) dragGhosts.current.set(index, node);
    else dragGhosts.current.delete(index);
  }, []);
  // A sentence the narration edit dropped is no longer selectable.
  useEffect(() => {
    setSelectedBlocks((held) => {
      const present = new Set([...held].filter((index) => sentences.some((s) => s.index === index)));
      return present.size === held.size ? held : present;
    });
  }, [sentences]);

  // ── the selected music clip ──────────────────────────────────────────────
  //
  // By ID, never by position: a cut re-orders the list and mints ids for the
  // halves it splits, and the inspector must stay on the clip the user
  // selected. While a clip is selected the DELETE keys act on IT rather than
  // on the range selection, and Escape clears it first (spec §12.5).
  const [selectedClip, setSelectedClip] = useState<string | null>(null);
  const selectedClipRef = useRef(selectedClip);
  selectedClipRef.current = selectedClip;
  // The library's open state, the clip nodes, the inspector's draft, the
  // waveform inside each clip and the lane's gestures are `useMusicLane`
  // (R1c), called below once the drag primitives it takes exist; the
  // dragged clip's label stays here, because the body's release hides it.
  const clipLabelRef = useRef<HTMLDivElement | null>(null);

  // ── the selected marker, and the one being named (E5b) ───────────────────
  //
  // By ID, as the clip is. Selecting a marker gives up the clip and the
  // blocks, and selecting a clip gives up the marker: while a marker is
  // selected the DELETE keys remove IT, before a selected clip and before the
  // range, and Escape gives it up first. `pending` is the marker `M` has just
  // dropped - drawn at the playhead with its name box open and NOT yet sent:
  // the one PUT goes when the box closes, with the typed name (Enter, or a
  // click elsewhere) or the default (Escape), so the marker always lands and
  // a name is never a second commit racing the first. It stays drawn until
  // the plan that has it lands. `nameBox` is the box itself: on the pending
  // marker, or on an existing one after a double-click (Enter renames,
  // Escape reverts).
  const [selectedMarker, setSelectedMarker] = useState<string | null>(null);
  const selectedMarkerRef = useRef(selectedMarker);
  selectedMarkerRef.current = selectedMarker;
  // The pending marker, the name box, the flag nodes and the reconcile
  // effect are `useMarkers` (R1d), called below once the drag primitives it
  // takes exist; the dragged flag's label stays here, because the body's
  // release hides it.
  const markerLabelRef = useRef<HTMLDivElement | null>(null);

  // ── the audition (R1b, timeline/useAudition.ts) ──────────────────────────
  //
  // Everything from the decoded clips to the shuttle's loop: the state, the
  // refs, `position`, `paint`, `halt`, `start`, `togglePlay`, `seek`, the
  // twelve effects, `stepBy`, `jumpToEnd`, `shuttleKey`. Called here, after
  // the selection's refs and the drag record it paints through and before
  // the zoom geometry, which reads its `total`. `ppsRef` is declared bare so
  // `paint` can close over it, and assigned where the zoom computes `pps`.
  const ppsRef = useRef(0);
  const {
    total, startsAt, overrunning, clipSeconds, failed, prep, prepError, prepare, cancelPrepare,
    musicFiles, musicPrep, musicError, musicMuted, setMusicMuted, playing, waitingToPlay,
    position, paint, planSignature, halt, seek, togglePlay, stepBy, jumpToEnd, shuttleKey, syncVideo,
    videoRef, playheadRef, headClockRef, clockRef, shuttleLabelRef,
  } = useAudition({
    active, sentences, speakable, squeeze, duration, durationRef, sourceDurationRef, keep, keepRef, keepSignature, joinsRef,
    storedMusic, musicRef, musicSignature, selection, selectionRef, setSelection, dragRef, ppsRef, positionRef, totalRef,
    inHandleRef, outHandleRef, bandRef, inLabelRef, outLabelRef,
  });

  const zoomMax = maxZoom(total, width);
  // A resize re-clamps whatever zoom is set: the ceiling is a function of the width.
  const zoomNow = Math.min(Math.max(1, zoom), zoomMax);
  const zoomRef = useRef(zoomNow);
  zoomRef.current = zoomNow;
  const pps = pixelsPerSecond(total, width, zoomNow);
  ppsRef.current = pps;
  /** The scrollLeft to apply once the strip has its new width (a zoom keeps its anchor still). */
  const pendingScroll = useRef<number | null>(null);
  // The whole audition, which runs to whichever of the picture and the
  // narration ends later...
  const contentWidth = Math.max(0, total * pps);
  // ... and the part of it the PICTURE covers. The two differ whenever the new
  // narration overruns the video, and the filmstrip and the waveform must be
  // drawn at the second: they are `duration` seconds of content, and stretching
  // them across the whole strip would put every burst under the wrong sentence,
  // which is the one thing this view exists to get right.
  const pictureWidth = Math.max(0, duration * pps);

  // Repainted on a zoom and on a new plan. The audition's other effects are
  // the hook's; this one reads `pps`, which the root computes from its `total`.
  useEffect(() => { paint(); }, [paint, pps, planSignature]);

  // ── zoom ─────────────────────────────────────────────────────────────────
  //
  // Continuous, and anchored: the strip's scrollLeft is recomputed so that
  // the anchor - the playhead for the slider and the keys, the pointer for
  // Ctrl+wheel - stays at the same screen x through the change. That is what
  // makes zooming in to place a cut and back out to see the whole feel like
  // one motion rather than a hunt. The scroll is applied by the layout effect
  // below, once the body has its new width; setting it here would be clamped
  // to the OLD width.
  const applyZoom = useCallback((next: number, anchor?: { seconds: number; screenPx: number }) => {
    const clamped = Math.min(Math.max(1, next), maxZoom(totalRef.current, widthRef.current));
    if (clamped === zoomRef.current) return;
    const scrollLeft = scrollRef.current?.scrollLeft ?? 0;
    const newPps = pixelsPerSecond(totalRef.current, widthRef.current, clamped);
    const at = anchor ?? { seconds: positionRef.current, screenPx: positionRef.current * ppsRef.current - scrollLeft };
    pendingScroll.current = anchoredScrollLeft(at.seconds, at.screenPx, newPps);
    setZoom(clamped);
  }, []);
  useLayoutEffect(() => {
    const strip = scrollRef.current;
    if (pendingScroll.current === null || !strip) return;
    strip.scrollLeft = pendingScroll.current;
    pendingScroll.current = null;
  }, [pps]);

  // Ctrl+wheel zooms about the pointer. A NATIVE listener, registered
  // non-passive by the callback ref below: React's onWheel is passive, and a
  // passive handler cannot preventDefault the browser's own Ctrl+wheel page
  // zoom.
  const onWheel = useCallback((event: WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const strip = scrollRef.current;
    const body = bodyRef.current;
    if (!strip || !body) return;
    const seconds = pxToSeconds(event.clientX - body.getBoundingClientRect().left, ppsRef.current);
    const screenPx = event.clientX - strip.getBoundingClientRect().left;
    // Scaled by the delta, as Camtasia does, rather than a notch per event: a
    // trackpad emits many small deltas per gesture and would race to the
    // ceiling. A mouse notch (~100 px) is one x1.25 step; Firefox's line and
    // page deltas are put in pixels first. `applyZoom` clamps.
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 100 : 1;
    applyZoom(zoomRef.current * Math.pow(1.25, (-event.deltaY * unit) / 100), { seconds, screenPx });
  }, [applyZoom]);

  // ── the strip's own width ────────────────────────────────────────────────
  //
  // A CALLBACK ref, not an effect keyed on `active`. Everything on this view is
  // positioned from `width`, and the effect that measured it could only fire
  // when `active` changed — but on the render where `active` first becomes
  // true this component returns a Spinner, because the plan is still being
  // read, and a cold plan takes about ten seconds while the speaking rate is
  // measured. So the measurement ran against a ref that was still null,
  // returned early, attached no observer, and never ran again: `width` stayed
  // 0, and with it every derived number. The whole strip then drew at zero
  // scale — all 62 sentences stacked at the left edge clamped to their 3px
  // minimum, no filmstrip (its own effect bails when the picture is 0 wide,
  // and bails silently because that is not a failure worth a message), and a
  // waveform pooled to nothing. Leaving the tab and coming back fixed it,
  // which is exactly what made it easy to miss: by then the plan was cached,
  // so the strip existed on the first active render.
  //
  // A callback ref fires when the node itself attaches, whichever of the three
  // early returns rendered before it, so there is no readiness condition to
  // keep in step with the returns above.
  const attachStrip = useCallback((element: HTMLDivElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    scrollRef.current?.removeEventListener("wheel", onWheel);
    scrollRef.current = element;
    setScrollEl(element);
    if (!element) return;
    element.addEventListener("wheel", onWheel, { passive: false });
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    observerRef.current = observer;
    setWidth(element.clientWidth);
  }, [onWheel]);

  // ── the filmstrip ────────────────────────────────────────────────────────
  //
  // Client-side, with no server work and no ffmpeg: a hidden <video> seeked to N
  // evenly spaced TIMELINE moments - each mapped to its source frame through
  // the edit - drawn to a canvas and kept as a data URL. Strictly one seek at a
  // time — await the `seeked` event before drawing, or the canvas gets whatever
  // frame happened to be decoded.
  const [thumbs, setThumbs] = useState<string[]>([]);
  const [filmError, setFilmError] = useState<string | null>(null);
  const thumbsWidth = useRef<number | null>(null);
  /** The edit the thumbs were made through: a changed one means new frames whatever the width. */
  const thumbsKeep = useRef<string | null>(null);
  const thumbsWanted = thumbCount(pictureWidth, THUMB_PX);

  useEffect(() => {
    if (!active || !(duration > 0) || thumbsWanted === 0) return;
    if (thumbsKeep.current !== keepSignature) thumbsWidth.current = null;
    if (!needsNewFrames(pictureWidth, thumbsWidth.current)) return;
    let cancelled = false;
    const video = document.createElement("video");
    // "metadata", not "auto": the frames are fetched by the seeks themselves as
    // byte ranges, and "auto" pulled the WHOLE source video down a second time
    // beside the one the transport is already streaming.
    video.preload = "metadata";
    video.muted = true;
    video.playsInline = true;

    const inTime = <T,>(promise: Promise<T>): Promise<T> => Promise.race([
      promise,
      new Promise<never>((_, reject) => setTimeout(
        () => reject(new Error("The browser did not answer in time.")), FRAME_TIMEOUT_MS)),
    ]);

    const once = (event: string) => new Promise<void>((resolve, reject) => {
      const ok = () => { video.removeEventListener("error", bad); resolve(); };
      const bad = () => { video.removeEventListener(event, ok); reject(new Error("failed")); };
      video.addEventListener(event, ok, { once: true });
      video.addEventListener("error", bad, { once: true });
    });

    void (async () => {
      try {
        video.src = `/api/projects/${projectId}/tracks/picture`;
        await inTime(once("loadedmetadata"));
        const canvas = document.createElement("canvas");
        canvas.width = THUMB_W;
        canvas.height = THUMB_H;
        const pen = canvas.getContext("2d");
        if (!pen) throw new Error("This browser cannot draw video frames.");

        const grabbed: string[] = [];
        for (const at of frameTimes(duration, thumbsWanted)) {
          if (cancelled) return;
          const seeked = once("seeked");
          video.currentTime = toSource(at, keepRef.current);
          await inTime(seeked);
          pen.drawImage(video, 0, 0, THUMB_W, THUMB_H);
          grabbed.push(canvas.toDataURL("image/jpeg", 0.6));
        }
        if (cancelled) return;
        thumbsWidth.current = pictureWidth;
        thumbsKeep.current = keepSignature;
        setThumbs(grabbed);
        setFilmError(null);
      } catch {
        if (cancelled) return;
        // A container this browser cannot decode (.mkv and .avi are both
        // importable) is not a failure of the timeline: the waveform and the
        // blocks are still the scale that matters.
        thumbsWidth.current = pictureWidth;
        thumbsKeep.current = keepSignature;
        setThumbs([]);
        setFilmError("This browser cannot show frames from this video file — the waveform below is still to scale.");
      } finally {
        video.removeAttribute("src");
        video.load();
      }
    })();

    return () => { cancelled = true; };
  }, [active, duration, pictureWidth, thumbsWanted, projectId, keepSignature]);

  // ── drawing ──────────────────────────────────────────────────────────────
  const pooled = useMemo(
    () => poolPeaks(projectPeaks(peaks.data?.peaks ?? [], peaks.data?.bucket_seconds ?? 0, keep), Math.round(pictureWidth)),
    [peaks.data, keep, pictureWidth],
  );
  const path = useMemo(() => waveformPath(pooled, 100), [pooled]);

  // ── the edit: commit, undo, redo ─────────────────────────────────────────
  //
  // The commit stack, its lock and its refusal are `useEditCommits` (R1a,
  // timeline/useEditCommits.ts). The two things it calls back into the strip
  // with - clearing the transforms a drag left once the plan lands, dropping
  // the marker `M` dropped when a commit is refused - are late-bound through
  // `afterCommitRef`, assigned below once `clearMoved` exists, because the
  // stack is declared here, before the lanes that paint. The refusal's state
  // and the lock's ref stay the strip's - its own handlers set the one and
  // read the other - and the stack is handed both.
  /** A refusal made here rather than by the server ("keep at least one range"). */
  const [refusal, setRefusal] = useState<string | null>(null);
  /** Read at a drag's release, which is a stable callback: a key committed mid-drag must not be followed by a second commit. */
  const editLockedRef = useRef(false);
  const afterCommitRef = useRef<AfterCommit>({ clearMoved: () => {}, dropPending: () => {} });
  const { commit, commitEdit, commitOffsets, undo, redo, history, editLocked, editError, applying } = useEditCommits({
    projectId, jobActive, plan, committedRef, committedOffsetsRef, sourceDurationRef, setSelection, onOffsetsSaved, afterCommitRef,
    refusal, setRefusal, editLockedRef,
  });

  // ── a trim in flight (E5a) ───────────────────────────────────────────────
  //
  // The edge follows the pointer, snapped, and the strip re-lays on RELEASE,
  // not per frame: the filmstrip and the waveform are drawn from the plan
  // (spec §13.1). What moves meanwhile is one overlay per pieces layer - the
  // stretch between the edge's old and new positions, hatched where it will
  // be removed and tinted where it comes back, with the live edge as a line
  // on the side the pointer is on - and a label. Painted through refs, never
  // through state. The picture's trim paints the Video AND Audio layers,
  // because the original audio follows the picture; the narration's paints
  // its own.
  const trimNodes = useRef(new Map<string, HTMLDivElement>());
  const trimLabels = useRef(new Map<string, HTMLDivElement>());
  // One callback ref per layer, made ONCE: a ref minted per render is called
  // with null and then the node again on every render of the strip.
  const trimRefs = useMemo(() => layerRefs(["video", "audio", "narration"], trimNodes), []);
  const trimLabelRefs = useMemo(() => layerRefs(["video", "narration"], trimLabels), []);
  const paintTrim = useCallback((
    drag: Drag, from: number, to: number, change: TrimChange | null, atFloor: boolean, snapped: number | null,
  ) => {
    const pps = ppsRef.current;
    const lo = Math.min(from, to) * pps;
    const hi = Math.max(from, to) * pps;
    for (const layer of drag.lane === "video" ? ["video", "audio"] : ["narration"]) {
      const node = trimNodes.current.get(layer);
      if (!node) continue;
      node.style.display = "";
      node.style.left = `${lo}px`;
      node.style.width = `${Math.max(0, hi - lo)}px`;
      node.className = `os-tl-trim ${change?.kind === "insert" ? "restore" : "cut"} ${to >= from ? "right" : "left"}`;
    }
    const label = trimLabels.current.get(drag.lane === "video" ? "video" : "narration");
    if (label) {
      label.style.display = "";
      label.style.transform = `translateX(${to * pps}px)`;
      const what = `${timecode(to)} · ${trimLabel(change, atFloor)}`;
      label.textContent = snapped === null ? what : `${what} ⌖`;
    }
  }, []);
  const clearTrim = useCallback(() => {
    trimNodes.current.forEach((node) => { node.style.display = "none"; });
    trimLabels.current.forEach((node) => { node.style.display = "none"; });
  }, []);

  /** Nothing to cut or split into: every lane is locked. */
  const allLocked = LANES.every((lane) => locks[lane]);

  // The cut, the split, the nudge and the reset - and the blocks they act
  // on - are `useEditGestures` (R1a, timeline/useEditGestures.ts); `drawn`
  // is imported from it for the block move's release below.
  const { cutSelection, splitAtPlayhead, actedOn, nudge, resetTiming } = useEditGestures({
    commitEdit, commitOffsets, editLocked, setRefusal, selectionRef, locksRef, committedRef, positionRef, sourceDurationRef,
    selectedBlocksRef, sentences, selected,
  });

  // ── the selection gesture ────────────────────────────────────────────────
  /** A selection lives on the PICTURE: nothing past its end can be cut. */
  const normalize = useCallback((a: number, b: number): Selection => {
    const limit = Math.max(0, durationRef.current);
    return { start: clampTime(Math.min(a, b), limit), end: clampTime(Math.max(a, b), limit) };
  }, []);
  /** Commit a selection to state; one with no length is no selection. */
  const commitSelection = useCallback((sel: Selection | null) => {
    setSelection(sel && sel.end - sel.start > EPSILON ? sel : null);
  }, []);
  const secondsAt = useCallback((clientX: number) => {
    const body = bodyRef.current;
    return body ? pxToSeconds(clientX - body.getBoundingClientRect().left, ppsRef.current) : 0;
  }, []);

  /** Every drag is handled on the BODY, whichever child began it, so one
   *  pair of move/up handlers serves the ruler, the head, both handles, the
   *  blocks and the lanes. The pointer is captured LAZILY - in the move
   *  handler, the moment a drag has really moved - never here: capture on
   *  pointer-down retargets the click (and the double-click) the browser
   *  fires after an unmoved release to the capturing element, and no block
   *  or head would ever receive its own. Until then the events bubble from
   *  the pressed child to the body anyway. `grabbed` is the timeline moment
   *  of the thing under the pointer (a handle, the head, a block), so that
   *  thing follows the pointer from where it was rather than jumping to it. */
  /**
   * ONE candidate set for every gesture that places a cut (E5a, spec §13.4):
   * 0, the picture's end, the playhead, every join on either track, every
   * sentence pin - each block's drawn start and end -, every clip's two
   * edges and (E5b) every DRAWN marker. Built once per gesture, never per
   * pointer move (trap 41). A dragged marker leaves its own moment out
   * (`exclude`), as a dragged clip leaves its own edges out.
   */
  const snapTargetsNow = useCallback((exclude?: string) => snapTargets({
    playhead: positionRef.current,
    duration: durationRef.current,
    joins: [...joinsRef.current, ...narrationJoinsRef.current].map((join) => join.at),
    pins: sentencesRef.current.flatMap((s) => [s.pinned_start, s.pinned_start + (s.end - s.start)]),
    clips: musicRef.current,
    markers: drawnMarkers(markersRef.current).filter((marker) => marker.id !== exclude).map((marker) => marker.timeline_at),
  }), []);

  const beginDrag = useCallback((
    event: ReactPointerEvent, kind: DragKind, anchor: number, grabbed?: number,
    move?: Pick<Drag, "index" | "members" | "snapTo" | "clip" | "zone" | "lane" | "pieceIndex" | "edge" | "marker">,
  ) => {
    const body = bodyRef.current;
    if (!body || event.button !== 0) return;
    // The handles, a range's ends and a trimmed edge share the full candidate
    // set; the block and clip drags bring their own subsets.
    const snapTo = move?.snapTo ?? (kind === "in" || kind === "out" || kind === "range" || kind === "piece-trim" ? snapTargetsNow() : undefined);
    dragRef.current = {
      kind,
      anchor,
      offset: grabbed === undefined ? 0 : secondsAt(event.clientX) - grabbed,
      seekOnClick: grabbed === undefined,
      startX: event.clientX,
      moved: false,
      pointerId: event.pointerId,
      delta: 0,
      ...move,
      snapTo,
    };
    event.stopPropagation();
    event.preventDefault();
  }, [secondsAt, snapTargetsNow]);

  // ── the Music lane is `useMusicLane` (R1c, timeline/useMusicLane.ts) ─────
  //
  // The library's open state, the clip nodes and the inspector's draft, the
  // waveform inside each clip, `paintClip` and `clearClipDrag`, `commitMusic`,
  // the studio's gain, `addMusic`, `removeClip`, `removeMissingClips`,
  // `changeClip` and the clip's two pointer handlers. Called here, after
  // `beginDrag` and `secondsAt`, which its pointer handlers take, and before
  // the body handlers, whose clip branches call its painters and its commit.
  // `clearMoved` follows it, because it composes the hook's `clearClipDrag`.
  const {
    libraryOpen, setLibraryOpen, libraryOpenRef, attachClip, clipDraft, setClipDraft, filePeaks, studioGainRef,
    addMusic, removeClip, removeMissingClips, changeClip, commitMusic, paintClip, clearClipDrag, onClipPointerDown, onClipClick,
  } = useMusicLane({
    active, musicRef, musicSignature, musicFiles, locksRef, editLocked, editLockedRef, commitEdit, committedRef, setRefusal,
    positionRef, durationRef, ppsRef, joinsRef, beginDrag, secondsAt, suppressClick, selectedClip, setSelectedClip, selectedClipRef,
    setSelectedMarker, setSelectedBlocks, clipLabelRef,
  });

  // ── the markers are `useMarkers` (R1d, timeline/useMarkers.ts) ───────────
  //
  // The pending marker and the name box, the flag nodes and the reconcile
  // effect, `paintMarker` and `clearMarkerDrag`, `commitMarkers`,
  // `dropMarker`, `finishNameBox`, `removeMarker`, `jumpToMarker`, the flag's
  // three handlers and the flags the ruler draws. Called here, after
  // `beginDrag`, `snapTargetsNow` and `seek`, which it takes, and before
  // `clearMoved`, which composes its `clearMarkerDrag`, and the body
  // handlers, whose marker branches call its painter and its commit.
  const {
    pending, nameBox, setNameBox, attachMarker, flags, nameBoxAt, commitMarkers, dropMarker, finishNameBox, removeMarker,
    jumpToMarker, paintMarker, clearMarkerDrag, onMarkerPointerDown, onMarkerClick, onMarkerDoubleClick, dropPending,
  } = useMarkers({
    storedMarkers, markersRef, markersSignature, editLocked, editLockedRef, commitEdit, committedRef, setRefusal, positionRef,
    keepRef, sourceDurationRef, ppsRef, seek, beginDrag, snapTargetsNow, suppressClick, setSelectedMarker, selectedMarkerRef,
    setSelectedClip, setSelectedBlocks, markerLabelRef,
  });

  /** Take the blocks a move painted through their transforms, and the clips and flags a drag painted, back to the plan's. */
  const clearMoved = useCallback(() => {
    blockNodes.current.forEach((node) => { node.style.transform = ""; });
    dragGhosts.current.forEach((node) => { node.style.display = "none"; });
    if (moveLabelRef.current) moveLabelRef.current.style.display = "none";
    clearClipDrag();
    clearTrim();
    clearMarkerDrag();
  }, [clearClipDrag, clearMarkerDrag, clearTrim]);
  afterCommitRef.current = { clearMoved, dropPending };

  /** Paint the marquee's band over the Narration lane between two moments, through its ref. */
  const paintMarquee = useCallback((a: number, b: number) => {
    const band = marqueeRef.current;
    if (!band) return;
    const lo = Math.max(0, Math.min(a, b)) * ppsRef.current;
    const hi = Math.max(a, b) * ppsRef.current;
    band.style.display = "";
    band.style.left = `${lo}px`;
    band.style.width = `${Math.max(1, hi - lo)}px`;
  }, []);

  const onBodyPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.moved) {
      // A release this handler never saw (the button let go off the strip
      // before the drag began, so nothing was captured): not a drag.
      if (event.buttons === 0) { dragRef.current = null; return; }
      if (Math.abs(event.clientX - drag.startX) < DRAG_SLOP_PX) return;
      drag.moved = true;
      // Now it is a drag: capture, so the moves and the release reach the
      // body wherever the pointer goes (allowed while the button is down).
      try { bodyRef.current?.setPointerCapture(event.pointerId); } catch { /* a synthetic pointer */ }
      // A scrub pauses: seek on every move, commit nothing.
      if (drag.kind === "scrub") halt(position());
      // A move leaves an outline where each block was, so a never-nudged
      // block has a trace to come back to while it is being dragged.
      if (drag.kind === "move") {
        for (const s of drag.members ?? []) {
          const ghost = dragGhosts.current.get(s.index);
          if (ghost) ghost.style.display = "";
        }
      }
    }
    const t = secondsAt(event.clientX) - drag.offset;
    if (drag.kind === "scrub") {
      const at = clampTime(t, totalRef.current);
      positionRef.current = at;
      syncVideo(at);
      paint();
      return;
    }
    if (drag.kind === "marquee") {
      paintMarquee(drag.anchor, secondsAt(event.clientX));
      return;
    }
    if (drag.kind === "clip" || drag.kind === "trim") {
      // The clip follows the pointer from where it was grabbed: its `at` for
      // a move, the dragged edge for a trim. The bounds are the model's and
      // live in lib/edit.ts (`moveClip`, `trimClip`), so a gesture can never
      // paint a clip the server would refuse.
      const base = drag.clip;
      if (!base) return;
      // The magnet governs this drag too (E5a); Ctrl stays its synonym here,
      // and Alt is the one modifier that turns snapping off everywhere.
      const free = !snappingRef.current || event.ctrlKey || event.metaKey || event.altKey;
      const threshold = SNAP_PX / ppsRef.current;
      let next: MusicClip;
      let snapped: number | null;
      if (drag.kind === "clip") {
        const landed = free || !drag.snapTo
          ? { at: Math.max(0, t), snapped: null }
          : snapClip(t, clipLength(base), drag.snapTo, threshold);
        // Clamped to the END of the audition as well as to 0 (`moveClip`), as
        // E3's block drag is: the pointer is captured on the body, so a drag
        // carries on past the strip, and a clip parked out there is dropped
        // by the render (`amix=…:duration=first`) while the Render line still
        // counts it.
        next = moveClip(base, landed.at, totalRef.current);
        // ⌖ only when the moved clip really sits on the candidate - by its
        // start or by its end, since `snapClip` may catch by either - after
        // `moveClip`'s clamps (0, the audition's end) have had their say.
        snapped = caughtAfterClamp(next.at, landed.snapped) ?? caughtAfterClamp(clipEnd(next), landed.snapped);
      } else {
        const landed = free || !drag.snapTo ? { t, snapped: null } : snap(t, drag.snapTo, threshold);
        next = trimClip(base, drag.zone === "in" ? "in" : "out", landed.t, fileSecondsOf(base));
        // ⌖ only when the trimmed edge IS the candidate: `trimClip`'s clamps
        // (0.1 s, the file's length, 0) can hold it short of one.
        snapped = caughtAfterClamp(drag.zone === "in" ? next.at : clipEnd(next), landed.snapped);
      }
      drag.clipNow = next;
      paintClip(drag, next, snapped);
      return;
    }
    if (drag.kind === "marker") {
      // The flag follows the pointer along the ruler, snapped to the one
      // candidate set without its own moment (E5b), on the OUTPUT axis and
      // never past the picture: the output only shows kept frames, so a
      // marker cannot land in removed picture. Its SOURCE moment is settled
      // on release (`moveMarker`).
      const base = drag.marker;
      if (!base) return;
      const free = !snappingRef.current || event.altKey;
      const landed = free || !drag.snapTo ? { t, snapped: null } : snap(t, drag.snapTo, SNAP_PX / ppsRef.current);
      const at = clampTime(landed.t, durationRef.current);
      drag.markerNow = at;
      // ⌖ only when the painted flag IS the candidate: the clamp to the
      // picture can hold it short of one.
      paintMarker(drag, at, caughtAfterClamp(at, landed.snapped));
      return;
    }
    if (drag.kind === "move") {
      // Every selected block follows the pointer by the same delta, painted
      // as a transform on the moved blocks - never state per frame. The
      // grabbed block's pin snaps (Camtasia's rule: to the playhead, the
      // joins, the other sentences' pins and landed ends, and its own spoken
      // moment) unless Ctrl is held; the delta is then clamped so no member
      // leaves the audition.
      const members = drag.members ?? [];
      const grabbed = members.find((s) => s.index === drag.index) ?? members[0];
      if (!grabbed) return;
      const pps = ppsRef.current;
      let delta = (event.clientX - drag.startX) / pps;
      let snapped: number | null = null;
      // The magnet governs this drag too (E5a); Ctrl stays E3's synonym for
      // Alt here, the one modifier that turns snapping off everywhere.
      if (snappingRef.current && !(event.ctrlKey || event.metaKey || event.altKey) && drag.snapTo) {
        const landed = snap(grabbed.pinned_start + delta, drag.snapTo, SNAP_PX / pps);
        delta = landed.t - grabbed.pinned_start;
        snapped = landed.snapped;
      }
      const pins = members.map((s) => s.pinned_start);
      delta = Math.max(delta, -Math.min(...pins));
      delta = Math.min(delta, totalRef.current - Math.max(...pins));
      // ⌖ only when the grabbed block's pin IS the candidate: the clamp above
      // can hold it short of one while another member sits at an end.
      snapped = caughtAfterClamp(grabbed.pinned_start + delta, snapped);
      drag.delta = delta;
      for (const s of members) {
        const node = blockNodes.current.get(s.index);
        if (node) node.style.transform = `translateX(${delta * pps}px)`;
      }
      const label = moveLabelRef.current;
      if (label) {
        const at = grabbed.pinned_start + delta;
        label.style.display = "";
        label.style.transform = `translateX(${at * pps}px)`;
        label.textContent = snapped === null ? timecode(at) : `${timecode(at)} ⌖`;
      }
      return;
    }
    if (drag.kind === "piece-trim") {
      // A piece's edge follows the pointer, snapped, and the bound is the
      // model's (`trimPiece`, lib/edit.ts) - clamped to the neighbouring
      // range, to 0 or the source's end, and to one frame of the piece - so
      // the paint never promises a list the server would refuse. The pointer
      // moves on the OUTPUT axis; the edge's source bound is the piece's own
      // mapping extended past its end, which is exactly the removed material
      // a restore brings back.
      const { lane, pieceIndex: index, edge } = drag;
      if (lane === undefined || index === undefined || edge === undefined) return;
      const piece = (lane === "video" ? videoPiecesRef.current : narrationPiecesRef.current)[index];
      if (!piece) return;
      const free = !snappingRef.current || event.altKey;
      const landed = free || !drag.snapTo ? { t, snapped: null } : snap(t, drag.snapTo, SNAP_PX / ppsRef.current);
      const oldBound = edge === "in" ? piece.sourceStart : piece.sourceEnd;
      const oldAt = edge === "in" ? piece.start : piece.end;
      const list = lane === "video" ? keepRef.current : narrationKeepRef.current;
      const source = sourceDurationRef.current;
      const trimmed = trimPiece(list, index, edge, oldBound + (landed.t - oldAt), source);
      const bound = trimmed[index][edge === "in" ? 0 : 1];
      const at = round3(oldAt + (bound - oldBound));
      drag.trimNow = { toSource: bound, at };
      // ⌖ only when the painted edge IS the candidate: `trimPiece`'s clamp -
      // the neighbouring range, the floor, 0, the end - routinely sits beside
      // one and holds the edge short of it (the Reviewer's MINOR 1).
      paintTrim(drag, oldAt, at, trimChange(list, index, edge, bound, source), atFrameFloor(trimmed, index), caughtAfterClamp(at, landed.snapped));
      return;
    }
    // A handle drags its own end; Ctrl+drag grows from where it began. Both
    // snap to the one candidate set (E5a) unless the magnet is off or Alt is
    // held; the label's ⌖ is `paint`'s, read off the drag. Painted through
    // the ref, never through state, until release.
    const free = !snappingRef.current || event.altKey;
    const landed = free || !drag.snapTo ? { t, snapped: null } : snap(t, drag.snapTo, SNAP_PX / ppsRef.current);
    let sel: Selection;
    if (drag.kind === "in") {
      sel = normalize(landed.t, drag.anchor);
      drag.snapEnd = landed.t <= drag.anchor ? "start" : "end";
    } else {
      sel = normalize(drag.anchor, landed.t);
      drag.snapEnd = landed.t < drag.anchor ? "start" : "end";
    }
    selectionRef.current = sel;
    // ⌖ only when the moving end IS the candidate: `normalize` clamps to the
    // picture, and the playhead can sit past it during an overrunning narration.
    drag.snapped = caughtAfterClamp(drag.snapEnd === "start" ? sel.start : sel.end, landed.snapped);
    paint();
  }, [halt, normalize, paint, paintClip, paintMarker, paintMarquee, paintTrim, position, secondsAt, syncVideo]);

  /** A piece under the pointer becomes the selection: the handles jump to its ends and the band spans it. */
  const selectPiece = useCallback((piece: Piece | null) => {
    if (!piece) return;
    setSelectedBlocks(NO_BLOCKS);
    commitSelection(normalize(piece.start, piece.end));
  }, [commitSelection, normalize]);
  /**
   * A click on a lane (`pick`): the piece under it, once the lane has more
   * than one; on a lane with a single piece, or a LOCKED lane - Camtasia
   * does not select a locked track's clips, and a selection there would arm
   * a cut of the other track - the click seeks, as a click on the strip
   * always has.
   */
  const pickOnLane = useCallback((track: Track, list: Piece[], clientX: number) => {
    if (clickSelectsPiece(list) && !locksRef.current[track]) selectPiece(pieceAt(list, secondsAt(clientX)));
    else seek(secondsAt(clientX));
  }, [secondsAt, seek, selectPiece]);

  const onBodyPointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (drag.moved) { try { bodyRef.current?.releasePointerCapture(event.pointerId); } catch { /* already released */ } }
    // The browser fires a click after this pointerup; after a real drag, or
    // a scrub or a marquee (whose click is the seek or the pick made here),
    // it must not act again. Cleared on the next tick in case no click
    // follows (a release off-screen). An unmoved release on a block or a
    // handle lets the click through: the block's own handler is where a
    // click chooses, selects and seeks (lib/edit.ts, `releaseSuppressesClick`).
    suppressClick.current = releaseSuppressesClick(drag.kind, drag.moved);
    setTimeout(() => { suppressClick.current = false; }, 0);
    const cancelled = event.type === "pointercancel";
    if (!drag.moved) {
      // What a click means, per kind (lib/edit.ts, `unmovedRelease`).
      switch (unmovedRelease(drag.kind, drag.seekOnClick, cancelled)) {
        case "seek": seek(secondsAt(event.clientX)); return;
        // A marquee picks on the Narration lane; a pressed piece edge on its own lane.
        case "pick":
          if (drag.lane === "video") pickOnLane("video", videoPiecesRef.current, event.clientX);
          else pickOnLane("narration", narrationPiecesRef.current, event.clientX);
          return;
        case "keep-selection": commitSelection(selectionRef.current); return;
        case "click":
          if (moveLabelRef.current) moveLabelRef.current.style.display = "none";
          if (clipLabelRef.current) clipLabelRef.current.style.display = "none";
          if (markerLabelRef.current) markerLabelRef.current.style.display = "none";
          return;
        default: return;
      }
    }
    if (drag.kind === "scrub") return;  // scrubbed on every move; nothing to commit
    if (drag.kind === "marquee") {
      if (marqueeRef.current) marqueeRef.current.style.display = "none";
      if (cancelled) return;
      // Camtasia's rubber band: every block the marquee crossed.
      const lo = Math.min(drag.anchor, secondsAt(event.clientX));
      const hi = Math.max(drag.anchor, secondsAt(event.clientX));
      const crossed = sentences.filter((s) => {
        const left = s.pinned_start;
        const right = left + Math.max(DRAG_SLOP_PX / ppsRef.current, s.end - s.start);
        return right >= lo && left <= hi;
      }).map((s) => s.index);
      setSelectedBlocks(new Set(crossed));
      return;
    }
    if (drag.kind === "clip" || drag.kind === "trim") {
      // ONE PUT on release, with the whole list (trap 8). The paint stays
      // until the plan that draws the clip where it landed is in - or is put
      // back at once when nothing changed, the commit was refused, the
      // pointer was cancelled, or a key committed something mid-drag.
      const base = drag.clip;
      const next = drag.clipNow;
      if (cancelled || editLockedRef.current || !base || !next || sameMusic([next], [base])) {
        clearClipDrag();
        return;
      }
      commitMusic(musicRef.current.map((clip) => (clip.id === base.id ? next : clip)));
      if (clipLabelRef.current) clipLabelRef.current.style.display = "none";
      return;
    }
    if (drag.kind === "marker") {
      // ONE PUT on release with the whole list: the flag's landed OUTPUT
      // moment becomes the marker's SOURCE moment through the picture's
      // list (`moveMarker`: `at = toSource(landed, keep)`), the list
      // re-sorted. The transform stays until the plan that draws the flag
      // where it landed is in - or goes at once when nothing changed, the
      // pointer was cancelled, or a key committed something mid-drag.
      const base = drag.marker;
      const at = drag.markerNow;
      if (cancelled || editLockedRef.current || !base || at === undefined) {
        clearMarkerDrag();
        return;
      }
      const next = moveMarker(markersRef.current, base.id, at, keepRef.current, sourceDurationRef.current);
      if (sameMarkers(next, markersRef.current)) {
        clearMarkerDrag();
        return;
      }
      commitMarkers(next, true);
      if (markerLabelRef.current) markerLabelRef.current.style.display = "none";
      return;
    }
    if (drag.kind === "piece-trim") {
      // ONE PUT on release, through the same path a Cut takes: the new lists
      // for every unlocked track from `nextEditForTrim` (a trim is a cut with
      // a name, trap 37), the clips from `musicAfterTrim` - the music rides
      // the PICTURE's change - and `commitEdit`, which skips a no-op
      // (`sameEdit`) and holds the commit lock. The overlay goes at once:
      // the strip re-lays from the plan the server answers with.
      clearTrim();
      const now = drag.trimNow;
      const { lane, pieceIndex: index, edge } = drag;
      if (cancelled || editLockedRef.current || !now || lane === undefined || index === undefined || edge === undefined) return;
      const held = locksRef.current;
      const before = committedRef.current;
      const outcome = nextEditForTrim(before, held, lane, index, edge, now.toSource, sourceDurationRef.current);
      if (outcome.refused) {
        const track = outcome.refused === "video" ? "picture" : "narration";
        setRefusal(`Keep at least one range — that trim would remove the whole ${track}.`);
        return;
      }
      if (!outcome.next || !outcome.change) return;
      // A shortening of the PICTURE across a missing clip is refused before
      // the PUT, as the cut is (E6, the owner's decision of 2026-09-30): its
      // undo could not be stored while the file is gone. Only where the
      // ripple applies at all (`missingCutByPicture`).
      const picture = outcome.picture;
      if (picture?.kind === "cut") {
        const across = missingCutByPicture(before.music, held, picture.a, picture.b);
        if (across.length > 0) {
          setRefusal(pictureCutRefusal("trim", across, picture.a, picture.b));
          return;
        }
      }
      const music = musicAfterTrim(before.music, held, outcome.picture);
      if (music.length > MAX_CLIPS) {
        setRefusal(`That trim would split the music into ${music.length} clips, past the limit of ${MAX_CLIPS} — `
          + "remove a clip first, or lock the Music lane to trim the picture alone.");
        return;
      }
      commitEdit({ ...outcome.next, music, markers: before.markers });
      return;
    }
    if (drag.kind === "move") {
      if (moveLabelRef.current) moveLabelRef.current.style.display = "none";
      // ONE request on release, never per frame (trap 8): the moved blocks'
      // offsets, from where they are DRAWN plus the delta. The transforms
      // stay until the plan that draws the blocks where they landed is in -
      // or are cleared at once when nothing changed, the commit failed, the
      // pointer was cancelled, or a key committed something mid-drag and the
      // gesture is locked (a second commit computed against a moving target
      // could land 0.05 s from where the block was painted).
      const changed = !cancelled && !editLockedRef.current && Math.abs(drag.delta) >= EPSILON
        && commitOffsets(dragOffsets(drawn(drag.members ?? []), drag.delta));
      if (!changed) clearMoved();
      return;
    }
    commitSelection(selectionRef.current);
  }, [clearClipDrag, clearMarkerDrag, clearMoved, clearTrim, commitEdit, commitMarkers, commitMusic, commitOffsets, commitSelection, pickOnLane, secondsAt, seek, sentences]);

  /** Down on the ruler: scrub, or with Ctrl a selection from this point. */
  const onRulerPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const t = secondsAt(event.clientX);
    if (event.ctrlKey || event.metaKey) beginDrag(event, "range", clampTime(t, durationRef.current));
    else beginDrag(event, "scrub", t);
  }, [beginDrag, secondsAt]);
  /** Ctrl+drag anywhere on the strip selects, as on Camtasia's timeline. */
  const onBodyPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    beginDrag(event, "range", clampTime(secondsAt(event.clientX), durationRef.current));
  }, [beginDrag, secondsAt]);
  const onHeadPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    beginDrag(event, "scrub", positionRef.current, positionRef.current);
  }, [beginDrag]);
  const onInPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const sel = selectionRef.current;
    beginDrag(event, "in", sel ? sel.end : positionRef.current, sel ? sel.start : positionRef.current);
  }, [beginDrag]);
  const onOutPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const sel = selectionRef.current;
    beginDrag(event, "out", sel ? sel.start : positionRef.current, sel ? sel.end : positionRef.current);
  }, [beginDrag]);

  const onStripClick = (event: MouseEvent<HTMLDivElement>) => {
    if (suppressClick.current || event.ctrlKey || event.metaKey) return;
    const box = event.currentTarget.getBoundingClientRect();
    seek(pxToSeconds(event.clientX - box.left, pps));
  };
  /**
   * A click on the Video or Audio lane: the piece under it, from the video
   * list - once the lane has more than one and the video is not locked;
   * otherwise the click seeks, as a click on the strip always has
   * (`pickOnLane`).
   */
  const onPieceClick = (event: MouseEvent<HTMLDivElement>) => {
    event.stopPropagation();
    if (suppressClick.current || event.ctrlKey || event.metaKey) return;
    pickOnLane("video", videoPieces, event.clientX);
  };
  /**
   * Down on a lane's pieces layer: a TRIM when the pointer has a piece's
   * edge - which edge is `pieceEdgeAt`'s answer at the current zoom, capped
   * at a third of the piece like a clip's, so the zones never eat a narrow
   * piece's body - on an unlocked lane only (a locked lane has no edges, and
   * Audio · original follows Video and has none of its own). Anything else
   * is left to bubble: a click picks or seeks as before, a press on the
   * Narration lane's empty space is its marquee, and Ctrl+drag is the range
   * everywhere. The pointer is captured lazily by the body's move handler.
   */
  const onPiecesPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>, lane: Track, list: Piece[]) => {
    if (event.ctrlKey || event.metaKey || event.button !== 0) return;
    if (editLocked || locksRef.current[lane]) return;
    const hit = pieceEdgeAt(list, secondsAt(event.clientX), CLIP_EDGE_PX / ppsRef.current);
    if (!hit) return;
    const piece = list[hit.index];
    const grabbed = hit.edge === "in" ? piece.start : piece.end;
    beginDrag(event, "piece-trim", grabbed, grabbed, { lane, pieceIndex: hit.index, edge: hit.edge });
  }, [beginDrag, editLocked, secondsAt]);
  /** Down on empty Narration-lane space: a marquee (a click selects the piece); Ctrl+drag stays the body's. */
  const onNarrationLanePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey) return;
    const at = clampTime(secondsAt(event.clientX), totalRef.current);
    beginDrag(event, "marquee", at, at);
  }, [beginDrag, secondsAt]);
  /**
   * Down on a block: a MOVE of every selected block (or this one alone when
   * it is not selected). The snap candidates are gathered here, once per
   * drag: the playhead, the video's joins, the other sentences' pins and
   * landed ends, and the block's own spoken moment - the ghost - so "back to
   * where it was said" is a snap, not a hunt. Ctrl leaves the event to the
   * body (Ctrl+drag is the range selection everywhere; Ctrl+click toggles
   * the block in the click handler).
   */
  const onBlockPointerDown = useCallback((event: ReactPointerEvent<HTMLButtonElement>, sentence: PlanSentence) => {
    if (event.ctrlKey || event.metaKey || event.button !== 0) return;
    event.stopPropagation();
    if (editLocked) return;  // the click still selects; nothing moves while a commit is in flight
    const chosen = selectedBlocksRef.current;
    const members = chosen.has(sentence.index) ? sentences.filter((s) => chosen.has(s.index)) : [sentence];
    const moving = new Set(members.map((s) => s.index));
    const snapTo = [0, durationRef.current, positionRef.current, ...joinsRef.current.map((join) => join.at), sentence.start];
    for (const s of sentences) {
      if (moving.has(s.index)) continue;
      snapTo.push(s.pinned_start);
      const landed = startsAt[s.index];
      if (landed) snapTo.push(landed.end);
    }
    beginDrag(event, "move", sentence.pinned_start, sentence.pinned_start, { index: sentence.index, members, snapTo });
  }, [beginDrag, editLocked, sentences, startsAt]);
  /** Click = this block alone (and seek, as before); Ctrl+click toggles it; Shift+click extends from the last plain click. */
  const onBlockClick = useCallback((event: MouseEvent<HTMLButtonElement>, sentence: PlanSentence, landedAt: number | null) => {
    event.stopPropagation();
    if (suppressClick.current) return;
    if (event.shiftKey) {
      const anchor = blockAnchorRef.current ?? sentence.index;
      const from = sentences.findIndex((s) => s.index === anchor);
      const to = sentences.findIndex((s) => s.index === sentence.index);
      const span = from < 0 ? [sentence] : sentences.slice(Math.min(from, to), Math.max(from, to) + 1);
      setSelectedBlocks(new Set(span.map((s) => s.index)));
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      setSelectedBlocks((held) => {
        const next = new Set(held);
        if (next.has(sentence.index)) next.delete(sentence.index);
        else next.add(sentence.index);
        return next;
      });
      return;
    }
    blockAnchorRef.current = sentence.index;
    setSelectedBlocks(new Set([sentence.index]));
    onSelect(sentence.index);
    seek(landedAt ?? sentence.pinned_start);
  }, [onSelect, seek, sentences]);

  // ── the transport's other moves ──────────────────────────────────────────
  /** Shift+Comma / Shift+Period: grow the selection a frame at its start or end (from the playhead with none). */
  const extendSelection = useCallback((direction: 1 | -1) => {
    const sel = selectionRef.current;
    const at = positionRef.current;
    const start = sel ? sel.start : at;
    const end = sel ? sel.end : at;
    commitSelection(direction < 0 ? normalize(start - FRAME_SECONDS, end) : normalize(start, end + FRAME_SECONDS));
  }, [commitSelection, normalize]);
  /** Ctrl+Shift+Home / End: the selection to the start or the end of the picture. */
  const extendTo = useCallback((edge: "start" | "end") => {
    const sel = selectionRef.current;
    const at = positionRef.current;
    commitSelection(edge === "start" ? normalize(0, sel ? sel.end : at) : normalize(sel ? sel.start : at, durationRef.current));
  }, [commitSelection, normalize]);
  const zoomStep = useCallback((direction: 1 | -1) => {
    applyZoom(stepZoom(zoomRef.current, maxZoom(totalRef.current, widthRef.current), direction));
  }, [applyZoom]);
  const zoomFit = useCallback(() => applyZoom(1), [applyZoom]);
  const zoomAll = useCallback(() => applyZoom(maxZoom(totalRef.current, widthRef.current)), [applyZoom]);
  /** Ctrl+Shift+8: the selection fills the strip, its start a little in from the left edge. */
  const zoomSelection = useCallback(() => {
    const sel = selectionRef.current;
    if (!sel) return;
    applyZoom(zoomToSelection(sel, totalRef.current, widthRef.current), { seconds: sel.start, screenPx: widthRef.current * 0.05 });
  }, [applyZoom]);

  // ── the keyboard is `useTimelineKeys` (R1d, timeline/useTimelineKeys.ts) ─
  //
  // TechSmith's bindings, one listener on the document while this view is
  // open: the key map (`keyAction`), `K` held, and the listener. Called here,
  // after every move the keys make.
  useTimelineKeys({
    active, libraryOpenRef, selectedMarkerRef, selectedClipRef, selectedBlocksRef, selectionRef, nudge, togglePlay, stepBy,
    setSelectedMarker, setSelectedClip, setSelectedBlocks, setSelection, removeMarker, removeClip, cutSelection, splitAtPlayhead,
    dropMarker, shuttleKey, extendSelection, jumpToMarker, undo, redo, seek, jumpToEnd, extendTo, zoomStep, zoomFit,
    zoomSelection, zoomAll,
  });

  if (!active) return null;
  if (plan.isLoading) return <Spinner label="Reading the narration…" />;
  if (plan.isError) return <ErrorBox message={errorMessage(plan.error) || "The narration plan could not be read."} />;

  const chosen = selected !== null ? sentences.find((s) => s.index === selected) ?? null : null;
  // DECODED clips, not the schedule's contiguous prefix: after a cut prunes a
  // clip in the middle the prefix stops there while every clip after it is
  // still held, and "Prepare the remaining N" must count what `prepare()`
  // will really fetch. The schedule keeps the prefix rule.
  const ready = speakable.filter((s) => clipSeconds[s.index] !== undefined).length;
  const failures = speakable.filter((s) => failed[s.index] !== undefined).length;
  // Sentences with words that are not muted and STILL will not be in the
  // render: pinned at or past the end of the picture, where the mux drops
  // them - by their offset, or, since E3, because the picture was cut short
  // under a locked narration and now ends before they are spoken. The one
  // state the owner cannot see coming, so it is said once at the top as well
  // as on the block, and each variant names its own remedy: an offset is
  // pulled back; a sentence the picture ran out on is cut with the
  // narration too, or dragged earlier.
  const dropped = sentences.filter((s) => s.past_end);
  const hasOffset = (s: PlanSentence) => Math.abs(s.pinned_start - s.start) >= 0.001;
  const droppedByOffset = dropped.filter(hasOffset);
  const droppedByPicture = dropped.filter((s) => !hasOffset(s));
  const names = (list: PlanSentence[]) => list.map((s) => `#${s.index + 1}`).join(", ");
  const sliderValue = Math.round(sliderFromZoom(zoomNow, zoomMax) * 1000);
  const status = commit.isPending
    ? (commit.variables?.op.kind === "offsets" ? "Saving the timing…" : "Saving the cut…")
    : applying ? "Re-reading the plan…" : null;
  const acted = actedOn();
  /**
   * The one lock combination a cut cannot do anything with: Music alone,
   * which is what clicking the Music channel's name produces. The music rides
   * the picture, so with the picture locked the ripple moves nothing — and a
   * scissors that appears to work and changes nothing is worse than one that
   * says why (the owner's ruling, 2026-09-21). `S` is unaffected: a split
   * changes no clip's `at`.
   */
  const musicOnly = !allLocked && !canCut(locks);
  const cutTitle = allLocked
    ? "Every lane is locked — unlock one to cut"
    : musicOnly
      ? "The music rides the picture, so cutting with the picture locked would leave the clips where they are. "
        + "Unlock Video to cut both, or use the clip's own gestures — drag its ends to trim it, or select it and "
        + "press Delete — to change the music alone. S still splits a clip at the playhead."
      : locks.video && !locks.music
        // The picture is not being cut, so the music does not ride anywhere: the
        // Music lane is unlocked and its clips still stay put. Saying only
        // "the unlocked lanes" here would state the owner's ruling backwards, at
        // the very control that performs it.
        ? "Remove the selection from the unlocked lanes and close the gap (Ctrl+Delete, Backspace, Ctrl+X). "
          + "The music rides the picture, so with Video locked the clips stay exactly where they are — "
          + "with a music clip selected those keys remove the clip instead"
        : "Remove the selection from the unlocked lanes and close the gap (Ctrl+Delete, Backspace, Ctrl+X) — "
          + "with a music clip selected those keys remove the clip instead";
  /**
   * The Music lane's two headers say something different from a track's,
   * because the owner's ruling makes it different: unlocking Music does NOT
   * make every cut apply to the clips — only a cut that cuts the PICTURE too
   * moves them, and with the picture locked they stay where they are (which is
   * exactly why the scissors is then disabled, `cutTitle`). The same words as
   * the scissors' own tooltip, so the app says one thing.
   */
  const lockTitle = (lane: Lane) => {
    if (lane === "music") {
      return locks.music
        ? "Unlock music: a split applies to the clips again, and so does a cut — but only one that cuts the picture too"
        : "Lock music: cuts and splits leave the clips exactly where they are";
    }
    return locks[lane]
      ? `Unlock ${lane}: cuts and splits apply to it again`
      : `Lock ${lane}: cuts and splits leave it exactly as it is`;
  };
  const nameTitle = (lane: Lane) => {
    if (channel === lane) return "Selected channel — click again to unlock every lane";
    if (lane === "music") {
      // Selecting this channel locks Video and Narration, which is the one
      // combination `canCut` refuses: promising "a cut edits just this one"
      // here would promise a cut at the very control that makes one impossible.
      return "Select the music channel: the other lanes lock. The music rides the picture, so the scissors is then "
        + "disabled — S still splits a clip at the playhead, and a clip can still be dragged, trimmed and deleted.";
    }
    return `Select the ${lane} channel: the other lanes lock, so a cut or a split edits just this one`;
  };
  // ── the Music lane's drawing ─────────────────────────────────────────────
  const inspected = selectedClip !== null ? storedMusic.find((held) => held.id === selectedClip) ?? null : null;
  const musicStatus = musicPrep.running
    ? `Decoding music ${Math.min(musicPrep.done + 1, musicPrep.total)} of ${musicPrep.total}…`
    : null;
  /** Why the Library's Add button is disabled, when it is. */
  const addTitle = locks.music
    ? "The Music lane is locked — unlock it to place a clip"
    : editLocked ? "Wait for the last edit to be saved" : "";

  return (
    <div className="os-tl">
      {/* The strip's own help is the BASICS, as a list of gestures: two lines
          of context, then the gesture on the left and what it does on the
          right. The owner, looking at the packaged app (2026-09-22): "the text
          is dense. Can some of this be available in documentation and just a
          summary of the basic actions displayed." Everything the two
          paragraphs that were here used to say — the lanes, the locks in full,
          splitting and pieces, the snapping, the Music lane's rules, the keys
          — is `docs/guides/timeline.md` now, which the link at the foot opens.

          Keep this SHORT. Every line added here pushes the ruler further down
          the page, which is the thing being fixed; a new rule belongs in the
          document. One element, not two: `.os-tl` is a grid and each child
          costs another 10 px of gap. */}
      <div className="os-tl-help os-muted os-small">
        <div>
          Play hears the new narration against the picture — no render, no job. A block sits where its
          sentence is <strong>aimed</strong>; the picture skips at a join: that is the edit.
        </div>
        {/* Ten rows since E5a: the trim's row is paid for by the lane's name
            and its lock sharing one, and the word budget by a word off six
            others (the render harness counts both). Eleven since E5b: the
            markers are a thing of their own - a key, a flag on the ruler
            and chapters in the render - and no row above could take them
            without losing what it says; the twelve words the row costs are
            paid for by a word or two off four others and the context.
            Twelve since E5c, the LAST row the harness's height cap allows:
            the shuttle is three keys with three facts (what each does, that
            a repeat climbs the rate, that K held turns J and L into the
            frame step), none of which a row above could carry, and the list
            was one word under its word budget already. A word each off two
            rows pays part; the harness's word cap moved for the rest and
            says why. The next gesture has to MERGE. */}
        <dl className="os-tl-actions">
          <dt>Drag the green or red handle, or Ctrl+drag</dt>
          <dd>Select a range; snaps to pins and joins, Alt: no snap</dd>
          <dt>Scissors, or Delete</dt>
          <dd>Cut the selection, close the gap</dd>
          <dt>Drag a piece's edge</dt>
          <dd>Trim the cut; drag it back out to restore</dd>
          <dt>S</dt>
          <dd>Split the unlocked lanes at the playhead</dd>
          <dt>M</dt>
          <dd>Drop a marker at the playhead; Ctrl+[ / ] jump between them</dd>
          <dt>J / K / L</dt>
          <dd>Shuttle back, stop, forward; again: 2×, 4×, 8×; K held + J or L steps a frame</dd>
          <dt>A lane's name, or its lock</dt>
          <dd>Edit only that channel, or lock it</dd>
          <dt>Drag a sentence block</dt>
          <dd>Re-time it; [ / ] nudge; Reset timing restores</dd>
          <dt>The + on Music</dt>
          <dd>Library: add a track at the playhead</dd>
          <dt>Drag a clip, or either end</dt>
          <dd>Move or trim it; Delete removes it</dd>
          <dt>The eye on Music</dt>
          <dd>The voice alone; the render still mixes it</dd>
          <dt>Ctrl+Z</dt>
          <dd>Undo</dd>
        </dl>
        <Link className="os-tl-help-link" to="/docs/guides/timeline">
          Full help: the Timeline <ChevronRight size={12} />
        </Link>
      </div>

      {droppedByOffset.length > 0 && (
        <div className="os-muted os-small">
          {droppedByOffset.length} sentence{droppedByOffset.length === 1 ? " is" : "s are"} pinned at or
          past the end of the picture by {droppedByOffset.length === 1 ? "its" : "their"} offset, so the
          re-voice will leave {droppedByOffset.length === 1 ? "it" : "them"} out entirely:{" "}
          {names(droppedByOffset)}. Pull the offset back inside the picture — drag the block earlier,
          nudge it with [, or type it in the List view.
        </div>
      )}
      {droppedByPicture.length > 0 && (
        <div className="os-muted os-small">
          The cut picture now ends before {droppedByPicture.length === 1 ? "this sentence is" : "these sentences are"}{" "}
          spoken, so the re-voice will leave {droppedByPicture.length === 1 ? "it" : "them"} out entirely:{" "}
          {names(droppedByPicture)}. Unlock Narration and cut it too, or drag
          {droppedByPicture.length === 1 ? " the sentence" : " them"} earlier.
        </div>
      )}

      {/* A missing file costs the render, not the lane (E4c): the server
          keeps a clip it already holds with its file gone, so the clip can
          still be moved, levelled, faded and removed - and since E6 trimmed
          shorter or split too: GROWING its slice is refused, because the
          file's length is unknown now, and so is a cut of the picture across
          it (its undo could not be stored). The banner names the files, says
          the render will refuse until the clips go or the files come back,
          and carries the button that removes them all in one PUT. */}
      {missingMusic.length > 0 && (
        <div className="os-muted os-small">
          {missingMusic.length === 1 ? "A music clip names a file" : `${missingMusic.length} music clips name files`}
          {" "}that {missingMusic.length === 1 ? "is" : "are"} no longer in the library
          ({[...new Set(missingMusic.map((held) => held.file))].join(", ")}), so the render will refuse until{" "}
          {missingMusic.length === 1 ? "it is removed or the file is" : "they are removed or the files are"} put back in
          the library under the same name. {missingMusic.length === 1 ? "It" : "They"} can still be moved, levelled
          and faded here, and trimmed shorter or split, but never lengthened past the part of the file{" "}
          {missingMusic.length === 1
            ? "it has, and a cut of the picture across it is refused until the Music lane is locked or the clip removed"
            : "each has, and a cut of the picture across one is refused until the Music lane is locked or the clips removed"}.{" "}
          <Button
            size="sm"
            variant="danger"
            icon={<Trash2 size={13} />}
            disabled={editLocked || locks.music}
            title={locks.music
              ? "The Music lane is locked — unlock it to remove the clips"
              : editLocked ? "Wait for the last edit to be saved"
                : `Remove ${missingMusic.length === 1 ? "it" : "them all"} in one save`}
            onClick={removeMissingClips}
          >
            {missingMusic.length === 1 ? "Remove the missing clip" : `Remove the ${missingMusic.length} missing clips`}
          </Button>
        </div>
      )}

      {editError && <ErrorBox message={editError} />}
      {prepError && <ErrorBox message={prepError} />}
      {musicError && <ErrorBox message={musicError} />}
      {peaks.isError && (
        <div className="os-muted os-small">
          {errorMessage(peaks.error)} — the sentence blocks below are still to scale.
        </div>
      )}
      {filmError && <div className="os-muted os-small">{filmError}</div>}

      {/* Camtasia's panel, dark whatever the app's theme: the canvas, the
          transport under it, then the timeline - its toolbar, and the track
          headers beside the scrolling strip. */}
      <div className="os-tl-panel">
        {/* The picture, muted, as the visual reference the transport drives. */}
        <div className="os-tl-canvas">
          <video
            ref={videoRef}
            className="os-tl-picture"
            muted
            playsInline
            preload="metadata"
            src={`/api/projects/${projectId}/tracks/picture`}
          />
        </div>

        <div className="os-tl-transport">
          <span />
          <div className="os-tl-transport-keys">
            <button type="button" className="os-tl-btn" aria-label="Jump to start" title="Jump to start (Ctrl+Home)" onClick={() => seek(0)}>
              <SkipBack size={15} />
            </button>
            <button
              type="button"
              className="os-tl-btn"
              aria-label="Step back one frame"
              title="Step back one frame (,) — a frame is 1/30 s here; the timeline never probes the source for its own rate"
              onClick={() => stepBy(-1)}
            >
              <StepBack size={15} />
            </button>
            <button
              type="button"
              className="os-tl-btn os-tl-play"
              aria-label={playing ? "Pause" : "Play"}
              title={playing
                ? "Pause (Space, or K) — L again shuttles forward at 2×, 4×, 8×; J shuttles back"
                : waitingToPlay
                  ? "Starting as soon as the first sentence is ready…"
                  : "Play (Space, or L) — J shuttles back, K stops; K held with J or L steps a frame"}
              onClick={togglePlay}
            >
              {playing ? <Pause size={18} /> : <Play size={18} />}
            </button>
            <button
              type="button"
              className="os-tl-btn"
              aria-label="Step forward one frame"
              title="Step forward one frame (.) — a frame is 1/30 s here; the timeline never probes the source for its own rate"
              onClick={() => stepBy(1)}
            >
              <StepForward size={15} />
            </button>
            <button type="button" className="os-tl-btn" aria-label="Jump to end" title="Jump to end (Ctrl+End)" onClick={jumpToEnd}>
              <SkipForward size={15} />
            </button>
            <span className="os-tl-clock">
              <span ref={clockRef}>{timecode(0)}</span> / {timecode(total)}
            </span>
            {/* The shuttle's direction and rate (E5c) - "◀◀ 4×", "▶▶ 2×" - written through the ref by
                `applyShuttle`, empty when stopped or playing at 1×. A live region, so a screen reader
                hears the rate change; polite, so it never interrupts. */}
            <span
              ref={shuttleLabelRef}
              className="os-tl-shuttle"
              aria-live="polite"
              title="J / K / L shuttle back, stop, forward; pressed again, 2×, 4×, 8×. Above 1× and backwards the picture runs silent."
            />
          </div>
          <div className="os-tl-prep">
            {prep.running ? (
              <>
                <span className="os-tl-status">
                  Preparing {Math.min(prep.done + 1, prep.total)} of {prep.total} — the first sentence
                  can take several seconds while the voice service warms up.
                </span>
                <button type="button" className="os-tl-btn text" onClick={cancelPrepare}><X size={13} /> Cancel</button>
              </>
            ) : ready + failures < speakable.length ? (
              <button type="button" className="os-tl-btn text" onClick={() => void prepare()}>
                {ready === 0
                  ? `Prepare all ${speakable.length} sentences`
                  : `Prepare the remaining ${speakable.length - ready - failures}`}
              </button>
            ) : (
              <span className="os-tl-status">
                {ready} of {speakable.length} sentences ready
                {failures > 0 && ` — ${failures} could not be spoken`}.
              </span>
            )}
          </div>
        </div>

        {/* The timeline toolbar, in Camtasia's order: undo, redo | cut | … zoom. */}
        <div className="os-tl-toolbar">
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Undo"
            title="Undo the last edit — a cut, split, drag, nudge or reset (Ctrl+Z)"
            disabled={history.past.length === 0 || editLocked}
            onClick={undo}
          >
            <Undo2 size={15} />
          </button>
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Redo"
            title="Redo the last undone edit — a cut, split, drag, nudge or reset (Ctrl+Y, Ctrl+Shift+Z)"
            disabled={history.future.length === 0 || editLocked}
            onClick={redo}
          >
            <Redo2 size={15} />
          </button>
          <span className="os-tl-sep" />
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Cut"
            title={cutTitle}
            disabled={!selection || editLocked || !canCut(locks)}
            onClick={cutSelection}
          >
            <Scissors size={15} />
          </button>
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Split at the playhead"
            title={allLocked ? "Every lane is locked — unlock one to split (Ctrl+Shift+S splits all)" : "Split the unlocked lanes at the playhead (S; Ctrl+Shift+S splits all)"}
            disabled={editLocked || allLocked}
            onClick={() => splitAtPlayhead(false)}
          >
            <SquareSplitHorizontal size={15} />
          </button>
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Reset timing"
            title={acted.length > 0
              ? `Reset timing: put the selected sentence${acted.length === 1 ? "" : "s"} back where ${acted.length === 1 ? "it was" : "they were"} spoken`
              : "Reset timing: select a sentence block first"}
            disabled={editLocked || !acted.some((s) => (offsets[s.index] ?? null) !== null)}
            onClick={resetTiming}
          >
            <RotateCcw size={14} />
          </button>
          {status && <span className="os-tl-status">{status}</span>}
          {!status && musicStatus && <span className="os-tl-status">{musicStatus}</span>}
          {jobActive && !status && <span className="os-tl-status">A job holds the project — cuts wait for it.</span>}
          <span className="os-tl-spacer" />
          <Search size={13} className="os-tl-zoom-icon" aria-hidden="true" />
          <button type="button" className="os-tl-btn" aria-label="Zoom out" title="Zoom out (Ctrl+Shift+−, or Ctrl+wheel)" disabled={zoomNow <= 1} onClick={() => zoomStep(-1)}>
            <ZoomOut size={14} />
          </button>
          {/* Focus is dropped on release: inputs are excluded from the key
              bindings, and Space right after a slider drag should still play. */}
          <input
            type="range"
            className="os-tl-range os-tl-zoom"
            min={0}
            max={1000}
            value={sliderValue}
            disabled={zoomMax <= 1}
            aria-label="Zoom"
            title={`${pps.toFixed(1)} px per second (${zoomNow.toFixed(2)}×)`}
            onChange={(e) => applyZoom(zoomFromSlider(Number(e.target.value) / 1000, zoomMax))}
            onPointerUp={(e) => e.currentTarget.blur()}
          />
          <button type="button" className="os-tl-btn" aria-label="Zoom in" title="Zoom in (Ctrl+Shift+=, or Ctrl+wheel)" disabled={zoomNow >= zoomMax} onClick={() => zoomStep(1)}>
            <ZoomIn size={14} />
          </button>
          <button type="button" className="os-tl-btn" aria-label="Fit" title="Fit the whole edit in the strip (Ctrl+Shift+7)" disabled={zoomNow <= 1} onClick={zoomFit}>
            <Maximize2 size={14} />
          </button>
          {/* Camtasia's magnet (E5a): every snapping gesture on the strip
              obeys it, and Alt while dragging turns it off for one drag. */}
          <button
            type="button"
            className="os-tl-btn os-tl-magnet"
            aria-label="Snapping"
            aria-pressed={snapping}
            title={snapping
              ? "Snapping is on: a dragged handle, a range's end, a piece's edge, a sentence block or a music clip "
                + "catches the playhead, the joins, the sentence pins and the clips' ends within 8 px. Hold Alt while "
                + "dragging to turn it off for that drag; click to turn it off."
              : "Snapping is off: drags land exactly where the pointer leaves them. Click to turn it on — a dragged "
                + "handle, a range's end, a piece's edge, a sentence block or a music clip then catches the playhead, "
                + "the joins, the sentence pins and the clips' ends within 8 px, and Alt while dragging turns it off "
                + "for that drag."}
            onClick={toggleSnapping}
          >
            <Magnet size={14} />
          </button>
        </div>

        <div className="os-tl-tracks">
          {/* Track headers: sticky by construction - the strip scrolls in its
              own column. Camtasia's lock on each editable track; the name
              selects the channel; the original audio follows the video. */}
          <div className="os-tl-headers">
            <div className="os-tl-header ruler" />
            <div className={`os-tl-header${locks.video ? " locked" : ""}${channel === "video" ? " selected" : ""}`}>
              <Video size={13} />
              <button type="button" className="os-tl-track-name" title={nameTitle("video")} aria-pressed={channel === "video"} onClick={() => selectChannel("video")}>
                Video
              </button>
              <button type="button" className="os-tl-tool os-tl-lock" aria-label={lockTitle("video")} aria-pressed={locks.video} title={lockTitle("video")} onClick={() => toggleLock("video")}>
                {locks.video ? <Lock size={12} /> : <Unlock size={12} />}
              </button>
            </div>
            <div className={`os-tl-header${locks.video ? " locked" : ""}`}>
              <AudioLines size={13} />
              <span>Audio · original<small>reference only · follows Video</small></span>
            </div>
            <div className={`os-tl-header${locks.narration ? " locked" : ""}${channel === "narration" ? " selected" : ""}`}>
              <Mic size={13} />
              <button type="button" className="os-tl-track-name" title={nameTitle("narration")} aria-pressed={channel === "narration"} onClick={() => selectChannel("narration")}>
                Narration
              </button>
              <button type="button" className="os-tl-tool os-tl-lock" aria-label={lockTitle("narration")} aria-pressed={locks.narration} title={lockTitle("narration")} onClick={() => toggleLock("narration")}>
                {locks.narration ? <Lock size={12} /> : <Unlock size={12} />}
              </button>
            </div>
            {/* Music: the library opens from here, the eye silences the lane
                in the AUDITION only (never in the render), and the lock is
                the other two lanes' lock. The three share `os-tl-tool`, which
                is the small header button's SHAPE; only the lock wears
                `os-tl-lock`, which is what turns amber when the lane is locked
                (A4's lesson — the library button and the eye used to wear a
                class called "lock" and a CSS rule had to undo the colour on
                the two that are not locks). */}
            <div className={`os-tl-header${locks.music ? " locked" : ""}${channel === "music" ? " selected" : ""}`}>
              <Music size={13} />
              <button type="button" className="os-tl-track-name" title={nameTitle("music")} aria-pressed={channel === "music"} onClick={() => selectChannel("music")}>
                Music
              </button>
              <span className="os-tl-header-tools">
                <button
                  type="button"
                  className="os-tl-tool"
                  aria-label="Open the music library"
                  title="Open the music library — add a track at the playhead, upload one, or delete one"
                  onClick={() => setLibraryOpen(true)}
                >
                  <Plus size={13} />
                </button>
                <button
                  type="button"
                  className="os-tl-tool"
                  aria-label={musicMuted ? "Hear the music again" : "Hear the voice alone"}
                  aria-pressed={musicMuted}
                  title={musicMuted
                    ? "The music is silenced in this audition only — click to hear it again. The render always mixes it."
                    : "Hear the voice alone: silences the music HERE only, never in the render"}
                  onClick={() => setMusicMuted((held) => !held)}
                >
                  {musicMuted ? <EyeOff size={12} /> : <Eye size={12} />}
                </button>
                <button type="button" className="os-tl-tool os-tl-lock" aria-label={lockTitle("music")} aria-pressed={locks.music} title={lockTitle("music")} onClick={() => toggleLock("music")}>
                  {locks.music ? <Lock size={12} /> : <Unlock size={12} />}
                </button>
              </span>
            </div>
          </div>

          <div className="os-tl-scroll" ref={attachStrip}>
            {/* ONE time scale, `pps` pixels a second, shared by every lane: a
                sentence block sits over the burst it was spoken in. The body is as
                wide as the whole AUDITION; the filmstrip and the waveform are as
                wide as the PICTURE, because that is how much content they have.
                The two are the same until the new narration overruns the video, and
                stretching `duration` seconds of frames across a longer strip would
                slide every burst out from under its sentence. */}
            <div
              className="os-tl-body"
              ref={bodyRef}
              style={{ width: contentWidth || "100%" }}
              onClick={onStripClick}
              onPointerDown={onBodyPointerDown}
              onPointerMove={onBodyPointerMove}
              onPointerUp={onBodyPointerUp}
              onPointerCancel={onBodyPointerUp}
            >
              {/* The ruler: click seeks, drag scrubs, Ctrl+drag selects. The
                  handles live here so they sit in the ruler at any zoom, and
                  so do the markers' flags (E5b): a sibling layer over the
                  ticks, one flag per DRAWN marker at `timeline_at`, with the
                  name beside it. The BUTTON is the 12 px glyph alone - the
                  name is a label inside it that takes no pointer
                  (`.os-tl-marker-name`, `pointer-events: none`), so a click
                  or a drag on the ruler under a name seeks or scrubs as it
                  does anywhere else (the Reviewer's MINOR 3), while the
                  drag's transform still carries the name with the glyph.
                  Click selects, double-click names, drag moves (through a
                  transform, as a clip's), and the box that names one sits
                  on its flag. */}
              <div
                className="os-tl-ruler"
                title="Click to seek, drag to scrub, Ctrl+drag to select a range; M drops a marker at the playhead"
                onPointerDown={onRulerPointerDown}
                onClick={(e) => e.stopPropagation()}
              >
                <Ruler total={total} pps={pps} scrollEl={scrollEl} />
                <MarkerRuler
                  flags={flags}
                  pps={pps}
                  selectedMarker={selectedMarker}
                  pending={pending}
                  nameBox={nameBox}
                  nameBoxAt={nameBoxAt}
                  setNameBox={setNameBox}
                  finishNameBox={finishNameBox}
                  attachMarker={attachMarker}
                  markerLabelRef={markerLabelRef}
                  onMarkerPointerDown={onMarkerPointerDown}
                  onMarkerClick={onMarkerClick}
                  onMarkerDoubleClick={onMarkerDoubleClick}
                />
                <span className="os-tl-sel-label in" ref={inLabelRef} />
                <span className="os-tl-sel-label out" ref={outLabelRef} />
                <div
                  className="os-tl-handle in"
                  ref={inHandleRef}
                  role="slider"
                  aria-label="Selection start"
                  aria-valuemin={0}
                  title="Drag to set where the selection starts"
                  onPointerDown={onInPointerDown}
                />
                <div
                  className="os-tl-handle out"
                  ref={outHandleRef}
                  role="slider"
                  aria-label="Selection end"
                  aria-valuemin={0}
                  title="Drag to set where the selection ends"
                  onPointerDown={onOutPointerDown}
                />
              </div>

              {/* The pieces of the Video and Audio lanes - the stretches of
                  the VIDEO list between its boundaries, what a click selects
                  - as layers over the two lanes (the film and the waveform
                  beneath are not positioned, so these paint above them). The
                  Narration lane's pieces sit inside its own lane, under the
                  blocks.

                  A piece's edges are the cut, and move (E5a): each piece of
                  an UNLOCKED lane carries a trim zone at either end - the
                  ew-resize cursor only; the pointer-down bubbles to the
                  layer, which asks `pieceEdgeAt` which edge it has. The
                  Video layer alone: Audio · original follows Video and has
                  no edges of its own, though a trim in flight is painted
                  over both, because the picture's audio goes with it. */}
              {(["video", "audio"] as const).map((lane) => (
                <div
                  key={lane}
                  className={`os-tl-pieces ${lane}${locks.video ? " locked" : ""}`}
                  onClick={onPieceClick}
                  onPointerDown={lane === "video" ? (event) => onPiecesPointerDown(event, "video", videoPieces) : undefined}
                >
                  {videoPieces.map((piece) => (
                    <div
                      key={piece.start}
                      className="os-tl-piece"
                      style={{ left: piece.start * pps, width: Math.max(1, (piece.end - piece.start) * pps) }}
                      title={`${timecode(piece.start)} – ${timecode(piece.end)} (${timecode(piece.sourceStart)} – ${timecode(piece.sourceEnd)} of the source). `
                        + (lane === "video" && !locks.video ? "Click to select; drag an edge to trim." : "Click to select.")}
                    >
                      {lane === "video" && !locks.video && (
                        <>
                          <span className="os-tl-piece-edge in" aria-hidden="true" />
                          <span className="os-tl-piece-edge out" aria-hidden="true" />
                        </>
                      )}
                    </div>
                  ))}
                  {/* A trim in flight, painted through its ref. */}
                  <div className="os-tl-trim" ref={trimRefs[lane]} style={{ display: "none" }} aria-hidden="true" />
                  {lane === "video" && (
                    <div className="os-tl-trim-label" ref={trimLabelRefs.video} style={{ display: "none" }} aria-hidden="true" />
                  )}
                </div>
              ))}

              <div className={`os-tl-film${locks.video ? " locked" : ""}`} style={{ width: pictureWidth || "100%" }} aria-hidden="true">
                {thumbs.length > 0
                  ? thumbs.map((src, i) => <img key={i} src={src} alt="" draggable={false} />)
                  : <div className="os-tl-film-empty" />}
              </div>

              {/* ONE path for the whole strip — never 2728 rects, never a canvas.
                  See lib/timeline.ts. */}
              <svg
                className={`os-tl-wave${locks.video ? " locked" : ""}`}
                style={{ width: pictureWidth || "100%" }}
                viewBox={`0 0 ${Math.max(1, pooled.length)} 100`}
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <path d={path} />
              </svg>

              {/* The Narration lane: its pieces (from ITS list) under the
                  blocks, a marquee on the empty space between them, and one
                  block per sentence that can be dragged along the lane. */}
              <div className={`os-tl-blocks${locks.narration ? " locked" : ""}`} onPointerDown={onNarrationLanePointerDown}>
                <div
                  className={`os-tl-pieces narration${locks.narration ? " locked" : ""}`}
                  onPointerDown={(event) => onPiecesPointerDown(event, "narration", narrationPieces)}
                >
                  {narrationPieces.map((piece) => (
                    <div
                      key={piece.start}
                      className="os-tl-piece"
                      style={{ left: piece.start * pps, width: Math.max(1, (piece.end - piece.start) * pps) }}
                      title={`${timecode(piece.start)} – ${timecode(piece.end)} (${timecode(piece.sourceStart)} – ${timecode(piece.sourceEnd)} of the source). `
                        + (locks.narration ? "Click to select." : "Click to select; drag an edge to trim.")}
                    >
                      {!locks.narration && (
                        <>
                          <span className="os-tl-piece-edge in" aria-hidden="true" />
                          <span className="os-tl-piece-edge out" aria-hidden="true" />
                        </>
                      )}
                    </div>
                  ))}
                  {/* A trim in flight, painted through its ref. */}
                  <div className="os-tl-trim" ref={trimRefs.narration} style={{ display: "none" }} aria-hidden="true" />
                  <div className="os-tl-trim-label" ref={trimLabelRefs.narration} style={{ display: "none" }} aria-hidden="true" />
                </div>
                {narrationJoins.map((join) => (
                  <div
                    key={join.at}
                    className={join.removed > 0 ? "os-tl-join lane" : "os-tl-join lane split"}
                    style={{ left: join.at * pps }}
                    title={describeJoin(join)}
                  />
                ))}
                {sentences.map((sentence) => {
                  const landed = startsAt[sentence.index];
                  const moved = Math.abs(sentence.pinned_start - sentence.start) > 0.001;
                  const classes = ["os-tl-block",
                    sentence.speakable ? "" : "muted",
                    sentence.index === selected ? "selected" : "",
                    selectedBlocks.has(sentence.index) ? "chosen" : "",
                    overrunning.has(sentence.index) ? "overrun" : "",
                    sentence.past_end || failed[sentence.index] ? "failed" : ""].filter(Boolean).join(" ");
                  // Said on the block itself, because it is the one state the owner
                  // cannot see coming: the sentence has words, is not muted, and
                  // will still be missing from the render.
                  const why = sentence.past_end
                    ? (hasOffset(sentence)
                      ? "\nDROPPED: its offset pins it at or past the end of the picture, so the re-voice leaves it out — drag it earlier or pull the offset back."
                      : "\nDROPPED: the cut picture ends before it is spoken, so the re-voice leaves it out — unlock Narration and cut it too, or drag it earlier.")
                    : sentence.muted ? "\nMuted." : "";
                  const nudged = (offsets[sentence.index] ?? null) !== null;
                  return (
                    <div key={sentence.index}>
                      {/* Where it was SPOKEN, whenever that is no longer where it is
                          aimed: "what I moved" without needing a second panel. */}
                      {moved && (
                        <div
                          className="os-tl-ghost"
                          style={{ left: sentence.start * pps, width: Math.max(2, (sentence.end - sentence.start) * pps) }}
                        />
                      )}
                      {/* Where it is being dragged FROM, shown only while it moves (through its ref). */}
                      <div
                        className="os-tl-ghost drag"
                        ref={attachDragGhost(sentence.index)}
                        style={{ display: "none", left: sentence.pinned_start * pps, width: Math.max(3, (sentence.end - sentence.start) * pps) }}
                      />
                      <button
                        type="button"
                        ref={attachBlock(sentence.index)}
                        className={classes}
                        aria-label={`Sentence ${sentence.index + 1} at ${timecode(sentence.pinned_start)}`}
                        aria-pressed={selectedBlocks.has(sentence.index)}
                        title={`${sentence.text}\n\nSpoken ${timecode(sentence.start)} – ${timecode(sentence.end)}`
                          + `\nAimed at ${timecode(sentence.pinned_start)}`
                          + (landed ? `\nLands at ${timecode(landed.start)}` : "")
                          + (landed?.squeezedHere ? "\nSped up slightly to fit the gap after it." : "")
                          + why
                          + "\n\nDrag to move it (Alt or Ctrl: no snapping); [ and ] nudge the selection"
                          + (nudged ? "; Reset timing puts it back where it was spoken." : ".")}
                        style={{
                          left: sentence.pinned_start * pps,
                          width: Math.max(3, (sentence.end - sentence.start) * pps),
                        }}
                        onPointerDown={(e) => onBlockPointerDown(e, sentence)}
                        onClick={(e) => onBlockClick(e, sentence, landed ? landed.start : null)}
                      >
                        <span className="os-tl-block-text">{sentence.text}</span>
                      </button>
                      {/* The overrun marker sits on the CULPRIT, where its clip
                          really ends — the pipeline never re-sorts, so the two play
                          consecutively and this is the moment it costs. */}
                      {landed && overrunning.has(sentence.index) && (
                        <div className="os-tl-overrun" style={{ left: landed.end * pps }} />
                      )}
                    </div>
                  );
                })}
                {/* The marquee's band and the dragged block's time, painted through their refs. */}
                <div className="os-tl-marquee" ref={marqueeRef} style={{ display: "none" }} aria-hidden="true" />
                <div className="os-tl-move-label" ref={moveLabelRef} style={{ display: "none" }} aria-hidden="true" />
              </div>

              {/* The Music lane: Camtasia's clips - a dark block per clip with
                  the file's name, its waveform inside (the cached peaks
                  sliced `in…out` and pooled as the audio lane's are), the
                  fades as triangles at its ends, and an 8 px trim zone at
                  each edge. A missing file is hatched: the audition skips it
                  and the render refuses on it. */}
              <MusicLane
                storedMusic={storedMusic}
                locks={locks}
                pps={pps}
                selectedClip={selectedClip}
                filePeaks={filePeaks}
                attachClip={attachClip}
                clipLabelRef={clipLabelRef}
                onClipPointerDown={onClipPointerDown}
                onClipClick={onClipClick}
              />

              {/* Siblings of the lanes so they span all of them: the selection
                  band (painted through its ref while a handle is dragged), a
                  marker at every VIDEO join with what was removed in its
                  tooltip (the narration's are on its own lane), and the
                  playhead with its head and clock in the ruler.

                  The band is ONE ROW PER LANE, in the lanes' order, and a
                  locked lane's row is simply not drawn — "this cut applies
                  here" must never be painted over a lane where it does not.
                  One rectangle could only skip lanes at the ends, so Narration
                  locked with Music unlocked was covered anyway; and each row
                  is its own lane's height, so a fifth lane is a fifth row
                  rather than another `calc` to get right. The container is
                  what the painter moves: one left and one width for all of
                  them. */}
              <div className="os-tl-selection" ref={bandRef} style={{ display: "none" }} aria-hidden="true">
                <span className="os-tl-selection-row" />
                <span className={`os-tl-selection-row lane${locks.video ? " locked" : ""}`} />
                <span className={`os-tl-selection-row lane${locks.video ? " locked" : ""}`} />
                <span className={`os-tl-selection-row lane${locks.narration ? " locked" : ""}`} />
                <span className={`os-tl-selection-row lane${locks.music ? " locked" : ""}`} />
              </div>
              {joinList.map((join) => (
                <div
                  key={join.at}
                  className={join.removed > 0 ? "os-tl-join picture" : "os-tl-join picture split"}
                  style={{ left: join.at * pps }}
                  title={describeJoin(join)}
                />
              ))}
              <div className="os-tl-playhead" ref={playheadRef}>
                <div
                  className="os-tl-head"
                  title="Drag to scrub; double-click to clear the selection"
                  onPointerDown={onHeadPointerDown}
                  onDoubleClick={() => setSelection(null)}
                >
                  <span className="os-tl-head-time" ref={headClockRef}>{timecode(0)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* The clip inspector: the row under the strip, with the level and the
          two fades. Each box commits on change (a slider release, a blur or
          Enter) as the List view's do - one PUT of the whole `music` list. */}
      {inspected && (
        <ClipInspector
          inspected={inspected}
          editLocked={editLocked}
          locks={locks}
          clipDraft={clipDraft}
          setClipDraft={setClipDraft}
          studioGainRef={studioGainRef}
          changeClip={changeClip}
          removeClip={removeClip}
        />
      )}

      <MusicLibrary
        open={libraryOpen}
        onClose={() => setLibraryOpen(false)}
        projectId={projectId}
        playhead={timecode(position())}
        canAdd={!editLocked && !locks.music}
        addTitle={addTitle}
        onAdd={addMusic}
      />

      {chosen ? (
        <div className="os-muted os-small">
          <strong>Sentence {chosen.index + 1}</strong> — spoken {timecode(chosen.start)}–{timecode(chosen.end)}
          {Math.abs(chosen.pinned_start - chosen.start) > 0.001 && <> · aimed at {timecode(chosen.pinned_start)}</>}
          {startsAt[chosen.index] && <> · lands at {timecode(startsAt[chosen.index].start)}</>}
          {startsAt[chosen.index]?.squeezedHere && <> · sped up to fit</>}
          {" · "}{chosen.voice} at {chosen.speed}×
          {chosen.muted && " · muted"}
          {chosen.past_end && (hasOffset(chosen) ? (
            <> · <strong>dropped</strong> — its offset pins it at or past the end of the picture, so the
              re-voice leaves it out. Drag it earlier or pull the offset back to hear it.</>
          ) : (
            <> · <strong>dropped</strong> — the cut picture ends before it is spoken, so the re-voice
              leaves it out. Unlock Narration and cut it too, or drag it earlier, to hear it.</>
          ))}
          {failed[chosen.index] && <> · {failed[chosen.index]}</>}
        </div>
      ) : (
        <div className="os-muted os-small">
          Click a block to select the sentence and move the playhead to it (Ctrl+click adds one, Shift+click
          a run, a drag on the empty lane a marquee); click the ruler — or a lane that has not been split —
          to seek anywhere. Undo is for this
          visit — the app keeps only the current cut and timing. Delete removes the selection and closes
          the gap, like Ctrl+Delete: this timeline has no empty space to leave.
        </div>
      )}
    </div>
  );
}
