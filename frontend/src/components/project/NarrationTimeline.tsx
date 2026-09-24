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
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  DEFAULT_MUSIC_GAIN,
  EPSILON,
  FRAME_SECONDS,
  UNDO_DEPTH,
  anchoredScrollLeft,
  atFrameFloor,
  caughtAfterClamp,
  clickSelectsPiece,
  clipAt,
  clipEnd,
  clipGain,
  clipLength,
  clipPeaks,
  clipPlayback,
  clipSnapTargets,
  clipsAfterDelete,
  canCut,
  cutMusic,
  musicAfterCut,
  LANES,
  MAX_CLIPS,
  snapClip,
  describeJoin,
  dragOffsets,
  editBody,
  editRefusal,
  fadePoints,
  fitFades,
  joins,
  laneLocks,
  maxZoom,
  mintClipId,
  missingAcross,
  missingAcrossRefusal,
  moveClip,
  musicAfterTrim,
  newMusicClip,
  nextEditForCut,
  nextEditForSplit,
  nextEditForTrim,
  outputDuration,
  pieceAt,
  pieceEdgeAt,
  pieces,
  positionAfterEdit,
  projectPeaks,
  releaseSuppressesClick,
  round3,
  sameEdit,
  sameMusic,
  sliderFromZoom,
  snap,
  snapTargets,
  stepFrame,
  stepZoom,
  ticks,
  toSource,
  trackBody,
  trimChange,
  trimClip,
  trimLabel,
  trimPiece,
  unmovedRelease,
  wholeKeep,
  withoutMissing,
  zoomFromSlider,
  zoomToSelection,
  type ClipZone,
  type DragKind,
  type Join,
  type Keep,
  type Lane,
  type LaneLocks,
  type MusicClip,
  type Piece,
  type Track,
  type TrackEdit,
  type TrimChange,
  type TrimEdge,
} from "../../lib/edit";
import { useStudioSettings } from "../../lib/studioSettings";
import type { Segment } from "../../lib/narration";
import {
  auditionLength,
  clampTime,
  clipsFrom,
  reachedEnd,
  frameTimes,
  narrationPlanKey,
  needsNewFrames,
  pixelsPerSecond,
  poolPeaks,
  pxToSeconds,
  schedule,
  thumbCount,
  waveformPath,
  type EditPayload,
  type NarrationPlan,
  type PlanSentence,
  type Schedule,
  type WaveformPeaks,
} from "../../lib/timeline";
import { Button, ErrorBox, Input, Spinner } from "../ui";
import { MusicLibrary, type MusicFile } from "./MusicLibrary";

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

/** One sentence as `PATCH /narration/offsets` returns it: the stored segment with its index. */
export type SavedSentence = Segment & { index: number };

/** A range of the OUTPUT, timeline seconds, `start < end`. */
interface Selection {
  start: number;
  end: number;
}
/**
 * Camtasia's locks per editable LANE (lib/edit.ts): the two tracks and, since
 * E4, the Music lane. Audio · original has no list of its own: it follows
 * Video.
 */
type Locks = LaneLocks;
/**
 * One state of the edit: each track's kept ranges (`null` for a track that
 * keeps everything) AND the music clips — the lane is not a track, but it is
 * part of the edit, so undo and redo carry it (spec §12.5).
 */
type EditState = TrackEdit & { music: MusicClip[] };
/**
 * One operation the client can send: the whole edit (both lists and the
 * clips), or a set of offsets by index. An undo entry holds one of each
 * direction — what to send to undo it and what to send to redo it (§11.5).
 */
type Op = ({ kind: "edit" } & EditState) | { kind: "offsets"; values: Record<number, number | null> };
interface Entry {
  undo: Op;
  redo: Op;
}
type Commit =
  | { kind: "do"; op: Op; before: Op }
  | { kind: "undo" | "redo"; op: Op; entry: Entry };
/** What a commit answers with: the stored edit, or the sentences a batch of offsets updated. */
type Answer = EditPayload | { sentences: SavedSentence[] };
interface Drag {
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
  /** A handle or a range end: the candidate it caught (null: none) and which end of the selection is moving, for the label's ⌖. */
  snapped?: number | null;
  snapEnd?: "start" | "end";
}

/** Fetched three at a time. Sixty requests at once queue behind each other in
 *  the browser anyway and give the voice service a thundering herd. */
const CONCURRENCY = 3;
/** How long to wait on the browser for one frame before giving up on the filmstrip. */
const FRAME_TIMEOUT_MS = 6000;
/** Captured small and scaled up by CSS — a filmstrip lane is 68px tall. */
const THUMB_W = 160;
const THUMB_H = 90;
/** Roughly one frame per this many pixels of strip: the lane is taller than 3a's, so wider thumbs crop less. */
const THUMB_PX = 120;
/** The lead given to the first scheduled clip, so it lands in the future. */
const START_LEAD = 0.08;
/** A pointer that has travelled less than this is a click, not a drag. */
const DRAG_SLOP_PX = 3;
/** A dragged block snaps to a candidate within this many pixels at the current zoom, as Camtasia's do. */
const SNAP_PX = 8;
/** `[` / `]` move the selected blocks this much; with Shift, five times as much. */
const NUDGE_SECONDS = 0.05;
const NUDGE_LARGE_SECONDS = 0.25;
/** Where a project's lock state is remembered: the client's, per visit, never on the record (decision 2 of §11.7). */
const locksKey = (projectId: string) => `ms:tl-locks:${projectId}`;
/** Where the magnet is remembered, beside the locks (E5a, spec §13.4): on by default, off only when it was switched off. */
const snapKey = (projectId: string) => `ms:tl-snap:${projectId}`;
const UNLOCKED: Locks = { video: false, narration: false, music: false };
const NO_BLOCKS: ReadonlySet<number> = new Set();
/** The lead given to a clip whose buffer arrives mid-play, so its `start` is not already in the past. */
const LATE_LEAD = 0.02;

const EMPTY_SCHEDULE: Schedule = { clips: [], overrunning: [], pushed: [], squeezed: [], end: 0 };
const NO_MUSIC: MusicClip[] = [];

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
  const qc = useQueryClient();
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
  // What the server holds, as far as this client knows: the plan's lists,
  // advanced by every commit that SUCCEEDS. The undo stack snapshots THIS,
  // never the plan's copy - which is still the previous edit until the
  // refetch lands - so the history is right even if the lock below were
  // ever bypassed. The same for the offsets: the page's stored copy,
  // advanced by every drag that lands.
  const committedRef = useRef<EditState>({ video: storedVideo, narration: storedNarration, music: storedMusic });
  useEffect(() => {
    // The clips come in the same fetch, so the signature is what moves here.
    committedRef.current = { video: storedVideo, narration: storedNarration, music: musicRef.current };
  }, [storedVideo, storedNarration, musicSignature]);
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

  // ── the decoded clips ─────────────────────────────────────────────────────
  //
  // decodeAudioData IS used here, and that does NOT contradict the porting
  // spec's "no decodeAudioData in the browser": that rule is about the source
  // audio.wav, which is 115 MB for a two-hour recording and is drawn from
  // server-side peaks instead. These are per-sentence TTS clips — the whole
  // narration of a 5m41s video is a 1.3 MB mp3, about 22 kB a sentence. Do not
  // "fix" this into a pile of <audio> elements: Web Audio is the only way to
  // place a clip on a shared clock to the millisecond.
  const buffers = useRef(new Map<number, AudioBuffer>());
  const [clipSeconds, setClipSeconds] = useState<Record<number, number>>({});
  const [failed, setFailed] = useState<Record<number, string>>({});
  const [prep, setPrep] = useState({ total: 0, done: 0, running: false });
  const [prepError, setPrepError] = useState<string | null>(null);
  const loadToken = useRef(0);
  // Whether a fetch pass is running, kept as a REF and set synchronously rather
  // than read off `prep.running`: state does not update until the next render,
  // so two Play presses in the same tick both saw "not running", both entered
  // prepare(), and the second bumped the load token — throwing away every clip
  // the first had in flight. Also what the animation loop reads.
  const preparingRef = useRef(false);

  const ctxRef = useRef<AudioContext | null>(null);
  const audioContext = useCallback(() => {
    if (!ctxRef.current) ctxRef.current = new AudioContext();
    return ctxRef.current;
  }, []);
  useEffect(() => () => { void ctxRef.current?.close(); }, []);

  // ── the music lane's audition (E4, spec §12.5) ───────────────────────────
  //
  // ONE decoded buffer per FILE, keyed by name and held BESIDE the sentence
  // buffers, which are keyed by index (trap 31: decoded music is PCM - a
  // five-minute stereo track is about 100 MB in memory - so it is decoded
  // once per file and never once per clip; and the two maps are never merged,
  // since a name and an index say different things).
  //
  // Lazily: nothing is fetched on mount, and never for a file the library has
  // lost (`missing`) - the audition skips those clips and the render refuses
  // on them (trap 25).
  const musicBuffers = useRef(new Map<string, AudioBuffer>());
  /** Bumped when a buffer lands, so the effect below can schedule it into a running audition. */
  const [musicReady, setMusicReady] = useState(0);
  const [musicPrep, setMusicPrep] = useState({ running: false, done: 0, total: 0 });
  const [musicError, setMusicError] = useState<string | null>(null);
  const musicLoading = useRef(false);
  /** The clip source nodes by clip id, each through its own gain node (the fades). */
  const musicSources = useRef(new Map<string, { node: AudioBufferSourceNode; gain: GainNode }>());
  /** One bus for every clip, so the eye can mute the audition instantly and the render is untouched. */
  const musicBusRef = useRef<GainNode | null>(null);
  const [musicMuted, setMusicMuted] = useState(false);
  const musicMutedRef = useRef(musicMuted);
  musicMutedRef.current = musicMuted;
  const musicBus = useCallback(() => {
    const ctx = audioContext();
    if (!musicBusRef.current) {
      const bus = ctx.createGain();
      bus.connect(ctx.destination);
      musicBusRef.current = bus;
    }
    musicBusRef.current.gain.value = musicMutedRef.current ? 0 : 1;
    return musicBusRef.current;
  }, [audioContext]);
  // The eye takes effect at once, mid-play and all: the bus is one node
  // between every clip and the speakers.
  useEffect(() => {
    if (musicBusRef.current) musicBusRef.current.gain.value = musicMuted ? 0 : 1;
  }, [musicMuted]);

  /** The distinct files the lane needs: one entry per name, missing files left out. */
  const musicFiles = useMemo(
    () => [...new Set(storedMusic.filter((clip) => !clip.missing).map((clip) => clip.file))],
    // `storedMusic` rides with its signature.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [musicSignature],
  );

  /** Decode every file the lane needs, once each. Called by Play, never on mount. */
  const prepareMusic = useCallback(async () => {
    if (musicLoading.current) return;
    const wanted = musicFiles.filter((name) => !musicBuffers.current.has(name));
    if (wanted.length === 0) return;
    musicLoading.current = true;
    setMusicError(null);
    setMusicPrep({ running: true, done: 0, total: wanted.length });
    for (const name of wanted) {
      try {
        const file = await api.blob(`/api/music/${encodeURIComponent(name)}`);
        const decoded = await audioContext().decodeAudioData(await file.arrayBuffer());
        musicBuffers.current.set(name, decoded);
        setMusicReady((n) => n + 1);
      } catch (err) {
        // Named, and the rest of the audition still plays: one unreadable
        // track is not a reason to lose the narration.
        setMusicError(`${name}: ${errorMessage(err)}`);
      }
      setMusicPrep((c) => ({ ...c, done: c.done + 1 }));
    }
    musicLoading.current = false;
    setMusicPrep((c) => ({ ...c, running: false }));
  }, [audioContext, musicFiles]);

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

  // A new plan means new clips: different WORDS, a different voice or a
  // different rate, so what is decoded is audio the render will no longer make.
  //
  // The text is in the signature and has to be. `preview_url` carries the
  // index, the voice and the speed but never the words, and it cannot: the
  // fitting rule floors at the job's speed, so a sentence whose narrator was
  // slower than the TTS baseline sits exactly on that floor and its URL never
  // moves however it is reworded - as does every sentence carrying an explicit
  // speed. Signed on the URL alone, fixing a Whisper mis-hearing left the OLD
  // clip in the buffer, played it back, and - because its duration feeds the
  // schedule - mis-placed every sentence after it too.
  //
  // Not the fetch time, so a background refetch returning the same plan does
  // not throw the audition away: that is what makes "nudge an offset, come back
  // and listen" instant.
  //
  // Per sentence as well as whole, so that a cut - which changes the plan but
  // leaves most sentences' words and URLs exactly as they were - drops only
  // the clips it invalidated. That is the difference between "cut, listen,
  // cut" and "cut, wait twenty seconds, listen".
  const signatures = useMemo(() => {
    const map = new Map<number, string>();
    for (const s of sentences) map.set(s.index, JSON.stringify([s.text, s.preview_url]));
    return map;
  }, [sentences]);
  const planSignature = useMemo(
    () => JSON.stringify(sentences.map((s) => [s.index, s.text, s.preview_url])),
    [sentences],
  );

  const prepare = useCallback(async () => {
    // Synchronously, before the first await: see `preparingRef`.
    if (preparingRef.current) return;
    const token = ++loadToken.current;
    const wanted = speakable.filter((s) => !buffers.current.has(s.index));
    if (wanted.length === 0) return;
    preparingRef.current = true;
    setPrepError(null);
    setPrep({ total: wanted.length, done: 0, running: true });

    let cursor = 0;
    const worker = async () => {
      while (cursor < wanted.length) {
        if (loadToken.current !== token) return;
        const sentence = wanted[cursor++];
        try {
          const clip = await api.blob(sentence.preview_url);
          const decoded = await audioContext().decodeAudioData(await clip.arrayBuffer());
          if (loadToken.current !== token) return;
          buffers.current.set(sentence.index, decoded);
          setClipSeconds((current) => ({ ...current, [sentence.index]: decoded.duration }));
        } catch (err) {
          if (loadToken.current !== token) return;
          // Recorded per sentence rather than as one banner: which line could
          // not be spoken is the useful fact, and the rest of the audition is
          // still worth hearing.
          setFailed((current) => ({ ...current, [sentence.index]: errorMessage(err) }));
          setPrepError(errorMessage(err));
        }
        if (loadToken.current === token) setPrep((c) => ({ ...c, done: c.done + 1 }));
      }
    };

    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, wanted.length) }, worker));
    if (loadToken.current === token) {
      preparingRef.current = false;
      setPrep((c) => ({ ...c, running: false }));
    }
  }, [speakable, audioContext]);

  // ── where each clip lands ────────────────────────────────────────────────
  //
  // Built from the CONTIGUOUS ready prefix rather than from whatever happens to
  // have arrived: a pin is a floor, so a clip appearing in the middle would move
  // every clip after it. Stopping at the first sentence that is neither decoded
  // nor known to have failed means the schedule only ever grows at the tail, so
  // a clip already playing never has to be stopped and restarted — which is what
  // lets playback begin before the whole narration is ready.
  const readyPrefix = useMemo(() => {
    const out: PlanSentence[] = [];
    for (const sentence of sentences) {
      if (!sentence.speakable) { out.push(sentence); continue; }
      if (clipSeconds[sentence.index] === undefined && failed[sentence.index] === undefined) break;
      out.push(sentence);
    }
    return out;
  }, [sentences, clipSeconds, failed]);

  const timing = useMemo(
    () => schedule(readyPrefix, clipSeconds, squeeze),
    [readyPrefix, clipSeconds, squeeze],
  );
  const timingRef = useRef<Schedule>(EMPTY_SCHEDULE);
  useEffect(() => { timingRef.current = timing; }, [timing]);

  const startsAt = useMemo(() => {
    const map: Record<number, { start: number; end: number; squeezedHere: boolean }> = {};
    for (const clip of timing.clips) {
      map[clip.index] = { start: clip.start, end: clip.end, squeezedHere: clip.squeezed };
    }
    return map;
  }, [timing]);
  const overrunning = useMemo(() => new Set(timing.overrunning), [timing]);
  const total = auditionLength(duration, timing.end);
  // Read by the animation loop, which is a self-calling closure: a clip
  // arriving mid-play can extend the audition past the picture, and the loop
  // would otherwise go on halting at the length it was created with.
  const totalRef = useRef(total);
  totalRef.current = total;

  // ── the transport ────────────────────────────────────────────────────────
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const playheadRef = useRef<HTMLDivElement | null>(null);
  const headClockRef = useRef<HTMLSpanElement | null>(null);
  const clockRef = useRef<HTMLSpanElement | null>(null);
  const sources = useRef(new Map<number, AudioBufferSourceNode>());
  const startedAt = useRef<{ ctxTime: number; position: number } | null>(null);
  const positionRef = useRef(0);
  const frame = useRef(0);
  /** Bumped by every `start` and every `halt`, so a start still inside its
   *  awaits knows it has been superseded and quietly gives up. */
  const startToken = useRef(0);
  const [playing, setPlaying] = useState(false);
  const playingRef = useRef(false);
  playingRef.current = playing;
  /** Play was pressed before the clips it needs existed: start as soon as they do. */
  const [waitingToPlay, setWaitingToPlay] = useState(false);
  /** The selection's end while playback began inside one: the loop's second bound. */
  const playBoundRef = useRef<number | null>(null);
  /** The next join ahead of the playhead, so the picture is re-seeked as it is crossed. */
  const nextJoinRef = useRef<Join | null>(null);

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
  const [libraryOpen, setLibraryOpen] = useState(false);
  const libraryOpenRef = useRef(libraryOpen);
  libraryOpenRef.current = libraryOpen;
  /** The clip elements by id, for the drag's per-frame paint. */
  const clipNodes = useRef(new Map<string, HTMLButtonElement>());
  const attachClip = useCallback((id: string) => (node: HTMLButtonElement | null) => {
    if (node) clipNodes.current.set(id, node);
    else clipNodes.current.delete(id);
  }, []);
  const clipLabelRef = useRef<HTMLDivElement | null>(null);
  /** The inspector's half-typed values, cleared when the selection or the plan moves on. */
  const [clipDraft, setClipDraft] = useState<{ gain?: number; fadeIn?: string; fadeOut?: string }>({});
  useEffect(() => { setClipDraft({}); }, [selectedClip, musicSignature]);
  // A clip the edit removed - by a cut, an undo, or a delete - is no longer selected.
  useEffect(() => {
    // The clips ride with their signature (`musicRef` is not reactive).
    setSelectedClip((held) => (held !== null && !musicRef.current.some((clip) => clip.id === held) ? null : held));
  }, [musicSignature]);

  const zoomMax = maxZoom(total, width);
  // A resize re-clamps whatever zoom is set: the ceiling is a function of the width.
  const zoomNow = Math.min(Math.max(1, zoom), zoomMax);
  const zoomRef = useRef(zoomNow);
  zoomRef.current = zoomNow;
  const pps = pixelsPerSecond(total, width, zoomNow);
  const ppsRef = useRef(pps);
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

  const position = useCallback(() => {
    const started = startedAt.current;
    const ctx = ctxRef.current;
    if (!started || !ctx) return positionRef.current;
    // Clamped at the start, because the clock reads BEFORE it during the lead.
    return Math.max(started.position, started.position + (ctx.currentTime - started.ctxTime));
  }, []);

  const paint = useCallback(() => {
    const at = positionRef.current;
    const scale = ppsRef.current;
    // Written straight to the DOM, never through state: a setState per frame
    // would re-render every sentence block sixty times a second, and a two-hour
    // recording has fifteen hundred of them. The head and its time label are
    // children of the line, so one transform moves all three.
    if (playheadRef.current) playheadRef.current.style.transform = `translateX(${at * scale}px)`;
    const clock = timecode(at);
    if (clockRef.current) clockRef.current.textContent = clock;
    if (headClockRef.current) headClockRef.current.textContent = clock;
    // The handles sit at the selection's ends, or both at the playhead when
    // there is none; the band and its in/out times only exist with one.
    const sel = selectionRef.current;
    const inAt = (sel ? sel.start : at) * scale;
    const outAt = (sel ? sel.end : at) * scale;
    // The handles are sliders to a screen reader: their value is where they sit.
    const limit = Math.max(0, durationRef.current).toFixed(3);
    if (inHandleRef.current) {
      inHandleRef.current.style.transform = `translateX(${inAt}px)`;
      inHandleRef.current.setAttribute("aria-valuenow", (sel ? sel.start : at).toFixed(3));
      inHandleRef.current.setAttribute("aria-valuemax", limit);
    }
    if (outHandleRef.current) {
      outHandleRef.current.style.transform = `translateX(${outAt}px)`;
      outHandleRef.current.setAttribute("aria-valuenow", (sel ? sel.end : at).toFixed(3));
      outHandleRef.current.setAttribute("aria-valuemax", limit);
    }
    if (bandRef.current) {
      if (sel) {
        bandRef.current.style.display = "";
        bandRef.current.style.left = `${inAt}px`;
        bandRef.current.style.width = `${Math.max(1, outAt - inAt)}px`;
      } else {
        bandRef.current.style.display = "none";
      }
    }
    // A handle or a range end that caught a snap candidate says so with ⌖ on
    // the end that is moving (E5a) - read off the drag here rather than
    // written by the move handler, because the animation loop repaints these
    // labels every frame while the audition plays.
    const drag = dragRef.current;
    const caught = drag && drag.moved && drag.snapped !== null && drag.snapped !== undefined ? drag.snapEnd : undefined;
    if (inLabelRef.current) {
      inLabelRef.current.textContent = sel ? `${timecode(sel.start)}${caught === "start" ? " ⌖" : ""}` : "";
      inLabelRef.current.style.transform = `translateX(${inAt}px) translateX(-100%)`;
    }
    if (outLabelRef.current) {
      outLabelRef.current.textContent = sel ? `${timecode(sel.end)}${caught === "end" ? " ⌖" : ""}` : "";
      outLabelRef.current.style.transform = `translateX(${outAt}px)`;
    }
  }, []);

  /** Every music clip's node, with the gain node that carried its fades - one left connected leaks per seek. */
  const stopMusic = useCallback(() => {
    musicSources.current.forEach(({ node, gain }) => {
      node.onended = null;
      try { node.stop(); } catch { /* already finished */ }
      node.disconnect();
      gain.disconnect();
    });
    musicSources.current.clear();
  }, []);

  const stopSources = useCallback(() => {
    sources.current.forEach((node) => {
      node.onended = null;
      try { node.stop(); } catch { /* already finished */ }
      node.disconnect();
    });
    sources.current.clear();
    stopMusic();
  }, [stopMusic]);

  /** Build the one-shot source nodes for everything still to be heard from
   *  `from`, against a clock that reads `from` at `ctxStart`. Web Audio source
   *  nodes cannot be restarted, so these are made afresh after every stop and
   *  every seek. */
  const scheduleFrom = useCallback((from: number, ctxStart: number) => {
    const ctx = audioContext();
    for (const { clip, offset } of clipsFrom(timingRef.current.clips, from)) {
      if (sources.current.has(clip.index)) continue;
      const buffer = buffers.current.get(clip.index);
      if (!buffer) continue;
      const node = ctx.createBufferSource();
      node.buffer = buffer;
      node.connect(ctx.destination);
      node.onended = () => { sources.current.delete(clip.index); };
      node.start(ctxStart + Math.max(0, clip.start - from), offset);
      sources.current.set(clip.index, node);
    }
    // The music, on the SAME clock: `start(ctxStart + max(0, at − from),
    // in + max(0, from − at), the rest)` (spec §12.5), through a gain node
    // that follows the clip's level and its two LINEAR fades - the shape
    // ffmpeg's `afade` will use, so what is heard here is what is rendered
    // (decision 4). A clip whose file is missing is skipped by `clipPlayback`.
    for (const clip of musicRef.current) {
      if (musicSources.current.has(clip.id)) continue;
      const buffer = musicBuffers.current.get(clip.file);
      if (!buffer) continue;
      let placed = clipPlayback(clip, from);
      if (!placed) continue;
      let when = ctxStart + placed.delay;
      if (when < ctx.currentTime) {
        // Its buffer arrived after the moment it was due (a decode that
        // finished mid-play): re-place it against the LIVE clock rather than
        // handing `start` a time in the past, which the browser would turn
        // into "now, from the wrong offset".
        placed = clipPlayback(clip, from + (ctx.currentTime - ctxStart) + LATE_LEAD);
        if (!placed) continue;
        when = ctx.currentTime + LATE_LEAD + placed.delay;
      }
      const gain = ctx.createGain();
      gain.connect(musicBus());
      // `played` is how far into the clip this playback begins, which is what
      // decides where on the envelope it starts.
      for (const point of fadePoints(clip, clipLength(clip) - placed.length)) {
        if (point.ramp) gain.gain.linearRampToValueAtTime(point.value, when + point.at);
        else gain.gain.setValueAtTime(point.value, when + point.at);
      }
      const node = ctx.createBufferSource();
      node.buffer = buffer;
      node.connect(gain);
      node.onended = () => { musicSources.current.delete(clip.id); gain.disconnect(); };
      node.start(when, placed.offset, placed.length);
      musicSources.current.set(clip.id, { node, gain });
    }
  }, [audioContext, musicBus]);

  /** Show the SOURCE frame for a timeline moment: the picture is the one
   *  unedited video, so every seek goes through the projection. Never past the
   *  source's last frame, which is the seek browsers most often refuse. */
  const syncVideo = useCallback((at: number) => {
    const video = videoRef.current;
    if (!video) return;
    const source = toSource(at, keepRef.current);
    try {
      video.currentTime = Math.min(source, Math.max(0, sourceDurationRef.current - 0.05));
    } catch { /* no metadata yet: the seek is applied when it arrives */ }
  }, []);

  const halt = useCallback((at: number) => {
    // Supersede any `start` still inside its awaits, so a press that has not
    // finished resuming the AudioContext cannot resurrect playback after this.
    startToken.current += 1;
    cancelAnimationFrame(frame.current);
    stopSources();
    startedAt.current = null;
    positionRef.current = at;
    videoRef.current?.pause();
    syncVideo(at);
    setPlaying(false);
    paint();
  }, [paint, stopSources, syncVideo]);

  const tick = useCallback(function run() {
    // Never two loops at once: `start` is async, so two presses inside its
    // awaits both used to reach here and the first loop ran on for ever,
    // fighting the second over the playhead.
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      positionRef.current = position();
      paint();
      const end = totalRef.current;
      // Never halt on an end that is not known. `total` is derived from the
      // plan's length, else the waveform's, and either can be momentarily
      // absent mid-play (a query re-keyed, a refetch); a bare `position >=
      // end` then read `position >= 0`, true at once, and halted the audition
      // at zero a few seconds in with every clip still loaded - the "playback
      // stops after a few seconds" report. Same rule the guard below already
      // follows for the narration's own end.
      if (reachedEnd(positionRef.current, end)) { halt(end); return; }
      // Playing a selection: it ends where the selection does.
      const bound = playBoundRef.current;
      if (bound !== null && positionRef.current >= bound) { halt(bound); return; }
      // Ran out of prepared narration with more still coming: wait for it
      // rather than running on past sentences in silence.
      if (timingRef.current.end > 0 && positionRef.current > timingRef.current.end + 0.05
          && preparingRef.current) {
        halt(timingRef.current.end);
        setWaitingToPlay(true);
        return;
      }
      // The picture across a cut: the hidden video plays SOURCE time, so at
      // each join it is jumped to where the output resumes - the skip IS the
      // edit - and once the narration outruns the cut picture it is paused
      // rather than left to run on into a removed tail.
      const join = nextJoinRef.current;
      if (join && positionRef.current >= join.at) {
        syncVideo(positionRef.current);
        nextJoinRef.current = joinsRef.current.find((j) => j.at > positionRef.current + EPSILON) ?? null;
      }
      const video = videoRef.current;
      if (video && !video.paused && positionRef.current >= durationRef.current) video.pause();
      run();
    });
  }, [halt, paint, position, syncVideo]);

  /** Where a Play begins: inside the selection when there is one and the
   *  playhead is in it (so pause and resume work), else the selection's start;
   *  with none, from the playhead, or from the top after the end. Decided in
   *  ONE place because `togglePlay`'s "nothing to play yet" test has to be
   *  made against where playback will really begin. */
  const startFrom = useCallback(() => {
    const at = positionRef.current;
    const sel = selectionRef.current;
    if (sel) return at >= sel.start - EPSILON && at < sel.end - 0.05 ? at : sel.start;
    return at >= totalRef.current - 0.05 ? 0 : at;
  }, []);

  /** `at` names where to begin - a seek's target - and bypasses `startFrom`'s
   *  selection rule: a seek is the user naming a position, and Play is the
   *  key that plays the selection. */
  const start = useCallback(async (at?: number) => {
    const token = ++startToken.current;
    const ctx = audioContext();
    // The music is decoded on the first PLAY, never on mount (trap 31): one
    // fetch and one decode per file, and the clips are scheduled into the
    // running audition as each buffer lands.
    void prepareMusic();
    // An AudioContext is created suspended until a user gesture; the Play button
    // IS that gesture, so resume before anything is scheduled against its clock.
    if (ctx.state === "suspended") await ctx.resume();
    if (startToken.current !== token) return;  // a halt, or a second press, won
    const from = at ?? startFrom();
    stopSources();
    const ctxStart = ctx.currentTime + START_LEAD;
    positionRef.current = from;
    startedAt.current = { ctxTime: ctxStart, position: from };
    scheduleFrom(from, ctxStart);
    const sel = selectionRef.current;
    playBoundRef.current = sel && from >= sel.start - EPSILON && from < sel.end - EPSILON ? sel.end : null;
    nextJoinRef.current = joinsRef.current.find((join) => join.at > from + EPSILON) ?? null;
    const video = videoRef.current;
    if (video) {
      try {
        syncVideo(from);
        await video.play();
      } catch { /* the picture is a reference, not the point; the audio plays regardless */ }
    }
    if (startToken.current !== token) return;
    setPlaying(true);
    tick();
  }, [audioContext, prepareMusic, scheduleFrom, startFrom, stopSources, syncVideo, tick]);

  /** Play, or pause. With nothing prepared yet this starts the fetching and
   *  plays as soon as the first sentence is decoded, rather than sitting there
   *  disabled. */
  const togglePlay = useCallback(() => {
    if (playingRef.current) { halt(position()); return; }
    const from = startFrom();
    positionRef.current = from;
    // The music is decoded on the first Play press, whatever state the
    // narration is in (trap 31: once per file, never on mount).
    void prepareMusic();
    if (timing.end <= from + 0.05) {
      void prepare();  // self-guarding: a second press does not restart the pass
      // Nothing of the narration is ready yet. With clips on the Music lane
      // there is still something to hear, so playback starts and the
      // sentences join as they arrive; with none, it waits for the first one.
      // A clip whose FILE is missing is not something to hear - the audition
      // skips it (trap 25) - so a lane of nothing but missing clips waits too,
      // rather than running the playhead across a silent strip.
      if (!musicRef.current.some((clip) => !clip.missing)) { setWaitingToPlay(true); return; }
    }
    void start();
  }, [halt, position, prepare, prepareMusic, start, startFrom, timing.end]);

  const cancelPrepare = useCallback(() => {
    // A request already in flight is left to finish and its result dropped:
    // `api.blob` has no abort. Nothing new is started.
    loadToken.current += 1;
    preparingRef.current = false;
    setPrep((c) => ({ ...c, running: false }));
    setWaitingToPlay(false);
  }, []);

  // A CHANGED PICTURE: the same source moment is somewhere else on the
  // timeline now (or nowhere, if it was just cut), so the playhead is
  // remapped rather than sent back to zero, and the selection - which was in
  // the old coordinates - is cleared. On the VIDEO list: the picture is the
  // axis the playhead and the selection live on. A list that changed without
  // removing anything - a split - moved nothing, so playback and the
  // selection are left alone (trap 23). Declared before the plan effect
  // below, which reads the position this one sets. The previous keep is kept
  // in a ref because the plan and its edit arrive in the same fetch.
  const previousKeepRef = useRef<Keep>(keep);
  useEffect(() => {
    const before = previousKeepRef.current;
    previousKeepRef.current = keep;
    if (JSON.stringify(before) === keepSignature) return;
    if (outputDuration(before) === outputDuration(keep)) return;
    halt(positionAfterEdit(position(), before, keep));
    setSelection(null);
    // `halt` and `position` are stable; `keep` rides with its signature.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keepSignature]);

  // A NEW PLAN means SOME of the decoded clips are audio the render will no
  // longer make - only those, since E2: a cut leaves most sentences' words and
  // URLs untouched, and their clips are still exactly right. Each clip whose
  // sentence changed or vanished is dropped; a fetch pass in flight was for
  // the old plan and is cancelled as before; and playback halts - where it is
  // when anything survived, from the top when nothing did (a new voice or
  // rate changes every URL, and that path is exactly what it was).
  const signaturesRef = useRef(signatures);
  useEffect(() => {
    const previous = signaturesRef.current;
    signaturesRef.current = signatures;
    const changed = (index: number) => signatures.get(index) !== previous.get(index);
    const prune = <T,>(current: Record<number, T>): Record<number, T> => {
      let next: Record<number, T> | null = null;
      for (const key of Object.keys(current)) {
        const index = Number(key);
        if (changed(index)) { next ??= { ...current }; delete next[index]; }
      }
      return next ?? current;
    };
    let removed = 0;
    buffers.current.forEach((_buffer, index) => {
      if (changed(index)) { buffers.current.delete(index); removed += 1; }
    });
    setClipSeconds(prune);
    setFailed(prune);
    loadToken.current += 1;
    preparingRef.current = false;
    setPrep({ total: 0, done: 0, running: false });
    setPrepError(null);
    setWaitingToPlay(false);
    halt(removed > 0 && buffers.current.size === 0 ? 0 : position());
    // `halt` and `position` are stable; `signatures` rides with the plan signature.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planSignature]);

  const seek = useCallback((to: number) => {
    const next = clampTime(to, total);
    const wasPlaying = playing;
    halt(next);
    // Resumed AT the target, never at the selection's start: a seek while
    // playing is the user naming a position (a ruler click, a block, Ctrl+Home).
    if (wasPlaying) void start(next);
  }, [halt, playing, start, total]);

  // A clip that arrives while the audition is running is scheduled straight
  // away, so a long transcript plays from the top while its tail is still being
  // fetched. Safe only because the ready-prefix rule above means nothing already
  // scheduled has moved.
  useEffect(() => {
    if (!playing || !startedAt.current) return;
    scheduleFrom(startedAt.current.position, startedAt.current.ctxTime);
  }, [timing, playing, scheduleFrom]);

  // A music buffer that finishes decoding mid-play is scheduled at once, on
  // the same clock (`scheduleFrom` re-places a clip whose moment has already
  // passed against the live one).
  useEffect(() => {
    if (!playing || !startedAt.current || musicReady === 0) return;
    scheduleFrom(startedAt.current.position, startedAt.current.ctxTime);
  }, [musicReady, playing, scheduleFrom]);

  // The CLIPS changed under a running audition - an add, a move, a trim, a
  // gain or a rippled cut has landed and the plan has been re-read. Whatever
  // is scheduled is at the old places, so it is dropped and the new list is
  // placed from where the playhead is now. (A cut also halts through the
  // picture effect above; this is for the commits that do not.)
  //
  // The re-placed clips land LATE_LEAD (20 ms) behind the voice: they are
  // scheduled against `ctx.currentTime + LATE_LEAD` while `startedAt.current`
  // - the narration's clock origin - does not move, so the music is 20 ms
  // late relative to the sentences until the next seek. Deliberate: the lead
  // is what keeps `start` from being handed a time already in the past, and
  // 20 ms under a bed is inaudible where a re-scheduled sentence would not be.
  useEffect(() => {
    if (!playingRef.current || !startedAt.current || !ctxRef.current) return;
    stopMusic();
    const ctx = ctxRef.current;
    scheduleFrom(position(), ctx.currentTime + LATE_LEAD);
    // The clips ride with their signature; `position` and the two callbacks are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [musicSignature]);

  // Play pressed before there was anything to play — or playback caught up with
  // the clips and stopped to wait for more.
  useEffect(() => {
    if (!waitingToPlay || playing) return;
    if (timing.end <= positionRef.current + 0.05) {
      if (!prep.running) setWaitingToPlay(false);  // nothing more is coming
      return;
    }
    setWaitingToPlay(false);
    void start();
    // `prep.running` is read as state on purpose here: this effect has to
    // re-run when the fetch pass finishes with nothing to show for it.
  }, [waitingToPlay, playing, timing.end, prep.running, start]);

  // Leaving the tab (or the page) must not leave the narration playing over a
  // video nobody can see. Paused where it was, not rewound: the List tab is
  // where an offset is typed, and coming back to find the playhead back at zero
  // would make "change it, hear it again" worse rather than better.
  useEffect(() => {
    if (!active) { halt(position()); setWaitingToPlay(false); }
  }, [active, halt, position]);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  useEffect(() => { paint(); }, [paint, pps, planSignature]);
  // The committed selection: mirrored into the ref the painter and the
  // transport read, then painted once. Mid-play the bound follows it - gone
  // when the selection is cleared, the new end when the playhead is inside
  // the new selection, else none - so Escape or a handle drag while playing
  // does not leave playback halting at an end that no longer exists.
  useEffect(() => {
    selectionRef.current = selection;
    if (startedAt.current) {
      const at = position();
      playBoundRef.current = selection && at >= selection.start - EPSILON && at < selection.end - EPSILON
        ? selection.end : null;
    }
    paint();
  }, [selection, paint, position]);

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
  /** A refusal made here rather than by the server ("keep at least one range"). */
  const [refusal, setRefusal] = useState<string | null>(null);
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
  }, []);

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
  }, []);

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

  /** Take the blocks a move painted through their transforms, and the clips a clip drag painted, back to the plan's. */
  const clearMoved = useCallback(() => {
    blockNodes.current.forEach((node) => { node.style.transform = ""; });
    dragGhosts.current.forEach((node) => { node.style.display = "none"; });
    if (moveLabelRef.current) moveLabelRef.current.style.display = "none";
    clearClipDrag();
    clearTrim();
  }, [clearClipDrag, clearTrim]);
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
      clearMoved();
    }
  }, [awaiting, plan.dataUpdatedAt, plan.errorUpdatedAt, clearMoved]);
  /** Between a successful commit and the plan it produced: the strip is about to change. */
  const applying = awaiting !== null;
  const commit = useMutation({
    mutationFn: ({ op }: Commit): Promise<Answer> => {
      if (op.kind === "offsets") {
        const body = Object.entries(op.values).map(([index, offset]) => ({ index: Number(index), offset }));
        return api.patch<{ sentences: SavedSentence[] }>(`/api/projects/${projectId}/narration/offsets`, { offsets: body });
      }
      // ALWAYS a PUT, never a DELETE, and `music` only when this operation
      // changes it: both rules live in `editBody` (lib/edit.ts), which is
      // where they are tested. The single highest-risk decision in E4b is not
      // one to leave inside a mutation as four lines of its own.
      return api.put<EditPayload>(
        `/api/projects/${projectId}/edit`,
        editBody(op, committedRef.current, sourceDurationRef.current),
      );
    },
    onMutate: () => setRefusal(null),
    onSuccess: (answer, variables) => {
      const { op } = variables;
      // What the server held until this instant is what undo goes back to.
      if (op.kind === "edit") {
        committedRef.current = { video: op.video, narration: op.narration, music: op.music };
        setSelection(null);
      } else {
        committedOffsetsRef.current = { ...committedOffsetsRef.current, ...op.values };
        // The List view reads the new numbers from the page's copy, folded in
        // rather than refetched - a refetch would drop half-typed words.
        if ("sentences" in answer) onOffsetsSaved(answer.sentences);
      }
      setHistory((h) => {
        if (variables.kind === "do") {
          return { past: [...h.past, { undo: variables.before, redo: op }].slice(-UNDO_DEPTH), future: [] };
        }
        if (variables.kind === "undo") return { past: h.past.slice(0, -1), future: [...h.future, variables.entry] };
        return { past: [...h.past, variables.entry].slice(-UNDO_DEPTH), future: h.future.slice(0, -1) };
      });
      setAwaiting(planStampRef.current);
      void qc.invalidateQueries({ queryKey: narrationPlanKey(projectId) });
      if (op.kind === "edit") {
        void qc.invalidateQueries({ queryKey: ["project", projectId] });
        void qc.invalidateQueries({ queryKey: ["edit", projectId] });
      }
    },
    onError: clearMoved,
  });
  const editLocked = jobActive || commit.isPending || applying;
  /** Read at a drag's release, which is a stable callback: a key committed mid-drag must not be followed by a second commit. */
  const editLockedRef = useRef(editLocked);
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
  /** Nothing to cut or split into: every lane is locked. */
  const allLocked = LANES.every((lane) => locks[lane]);

  /**
   * Commit a new edit (a cut, a split) with what it replaces as its undo.
   * Compared and stored in the PUT body's own terms - a whole track is null -
   * so a split on the very start of an untouched track, which makes a list
   * that is still the whole source, commits nothing and leaves no undo entry.
   */
  const commitEdit = useCallback((next: EditState) => {
    const before = committedRef.current;
    const source = sourceDurationRef.current;
    if (sameEdit(next, before, source) && sameMusic(next.music, before.music)) return;
    const after: EditState = {
      video: trackBody(next.video, source),
      narration: trackBody(next.narration, source),
      music: next.music,
    };
    commit.mutate({ kind: "do", op: { kind: "edit", ...after }, before: { kind: "edit", ...before } });
  }, [commit]);

  /**
   * A clip gesture: the whole `music` list, with the two tracks exactly as
   * they stand, in ONE PUT on release (trap 8). The tracks ride along because
   * every edit operation carries the whole edit — that is what lets undo and
   * redo put back a state rather than a fragment (decision 10).
   */
  const commitMusic = useCallback((clips: MusicClip[]) => {
    const before = committedRef.current;
    commitEdit({ video: before.video, narration: before.narration, music: clips });
  }, [commitEdit]);

  // ── the music clips: add, remove, and the inspector's boxes ──────────────
  //
  // The studio's `music_volume` is a new clip's level (decision 2): the one
  // static bed under the voice the owner ruled on, never ducking; 0.15 when
  // the settings cannot be read.
  const studio = useStudioSettings();
  const studioGainRef = useRef<number | undefined>(undefined);
  studioGainRef.current = studio.data?.settings?.music_volume;

  /** A clip's file length as the read-back reports it; `null` for a file the library has lost. */
  const fileSecondsOf = (clip: MusicClip): number | null => clip.file_duration ?? null;

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
  }, [commitMusic]);

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
  }, [commitMusic]);

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
  }, [commitMusic]);

  /** The inspector's boxes: one field of one clip, with the fades kept legal whatever is typed. */
  const changeClip = useCallback((id: string, patch: Partial<MusicClip>) => {
    if (editLockedRef.current || locksRef.current.music) return;
    const clips = musicRef.current;
    const held = clips.find((clip) => clip.id === id);
    if (!held) return;
    const next = { ...held, ...patch };
    const [fade_in, fade_out] = fitFades(clipLength(next), next.fade_in, next.fade_out);
    commitMusic(clips.map((clip) => (clip.id === id ? { ...next, fade_in, fade_out } : clip)));
  }, [commitMusic]);

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
    commitEdit({ ...outcome.next, music });
  }, [commitEdit, editLocked]);

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
    commitEdit({ ...tracks, music });
  }, [commitEdit, editLocked]);

  /** The blocks a nudge or a Reset acts on: the selected blocks, else the chosen sentence when it is on the strip. */
  const actedOn = useCallback((): PlanSentence[] => {
    const chosen = selectedBlocksRef.current;
    if (chosen.size > 0) return sentences.filter((s) => chosen.has(s.index));
    const one = selected !== null ? sentences.find((s) => s.index === selected) : undefined;
    return one ? [one] : [];
  }, [selected, sentences]);
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
  }, [commit]);
  /**
   * The blocks as `dragOffsets` wants them: from what is DRAWN - the plan's
   * pin, never the page's stored offset, which can be a plan refetch behind
   * (a value typed in the List a moment ago) and would land the block
   * seconds from where it was dropped. The stored copy is undo's business
   * only (`commitOffsets`).
   */
  const drawn = (blocks: PlanSentence[]) => blocks.map((s) => ({ index: s.index, start: s.start, offset: s.pinned_start - s.start }));
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
   * sentence pin - each block's drawn start and end - and every clip's two
   * edges. Built once per gesture, never per pointer move (trap 41).
   */
  const snapTargetsNow = useCallback(() => snapTargets({
    playhead: positionRef.current,
    duration: durationRef.current,
    joins: [...joinsRef.current, ...narrationJoinsRef.current].map((join) => join.at),
    pins: sentencesRef.current.flatMap((s) => [s.pinned_start, s.pinned_start + (s.end - s.start)]),
    clips: musicRef.current,
  }), []);

  const beginDrag = useCallback((
    event: ReactPointerEvent, kind: DragKind, anchor: number, grabbed?: number,
    move?: Pick<Drag, "index" | "members" | "snapTo" | "clip" | "zone" | "lane" | "pieceIndex" | "edge">,
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
  }, [halt, normalize, paint, paintClip, paintMarquee, paintTrim, position, secondsAt, syncVideo]);

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
      // A shortening ACROSS a missing clip would change its slice, which the
      // server refuses (E4c) in words about a trim of the clip, not this
      // gesture: refused here first, as the cut and the split are, only
      // where the ripple applies at all.
      const picture = outcome.picture;
      if (picture?.kind === "cut" && !held.music && !held.video) {
        const across = missingAcross(before.music, picture.a, picture.b);
        if (across.length > 0) {
          setRefusal(missingAcrossRefusal("trim", across, picture.a, picture.b));
          return;
        }
      }
      const music = musicAfterTrim(before.music, held, outcome.picture);
      if (music.length > MAX_CLIPS) {
        setRefusal(`That trim would split the music into ${music.length} clips, past the limit of ${MAX_CLIPS} — `
          + "remove a clip first, or lock the Music lane to trim the picture alone.");
        return;
      }
      commitEdit({ ...outcome.next, music });
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
  }, [clearClipDrag, clearMoved, clearTrim, commitEdit, commitMusic, commitOffsets, commitSelection, pickOnLane, secondsAt, seek, sentences]);

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
  }, [beginDrag, editLocked, secondsAt]);

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
    setSelectedBlocks(NO_BLOCKS);
  }, []);

  // ── the transport's other moves ──────────────────────────────────────────
  /** Comma / Period: a frame is 1/30 s here (see FRAME_SECONDS); stepping pauses. */
  const stepBy = useCallback((direction: 1 | -1) => {
    halt(stepFrame(position(), direction, totalRef.current));
  }, [halt, position]);
  const jumpToEnd = useCallback(() => { halt(totalRef.current); }, [halt]);
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

  // ── the keyboard ─────────────────────────────────────────────────────────
  //
  // TechSmith's own bindings, one listener on the document while this view is
  // open, ignored inside anything typed into (the List tab's boxes, the
  // Re-voice card's fields). Every key handled is prevented: Space must not
  // scroll the page and Backspace must not navigate. The handler lives in a
  // ref so the listener is bound once per `active` rather than on every
  // render.
  const keyHandler = useRef<(event: KeyboardEvent) => boolean>(() => false);
  keyHandler.current = (event) => {
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
      // Escape clears the music clip first, then the block selection, then
      // (a third press) the range - the clip selection wins while it exists.
      if (key === "Escape") {
        if (selectedClipRef.current !== null) { setSelectedClip(null); return true; }
        if (selectedBlocksRef.current.size > 0) { setSelectedBlocks(NO_BLOCKS); return true; }
        if (!selectionRef.current) return false;
        setSelection(null);
        return true;
      }
      // Camtasia's plain Delete leaves space on the timeline; this one has no
      // gaps (the edit is ranges of one source), so it closes the gap too.
      // With a music clip selected these keys remove THE CLIP instead
      // (Camtasia's "delete selected media"): the clip selection wins while
      // it exists, and Escape is how it is given up.
      if (key === "Backspace" || key === "Delete") {
        if (once) { if (selectedClipRef.current !== null) removeClip(); else cutSelection(); }
        return true;
      }
      // TechSmith's S: split the selected / unlocked tracks at the playhead.
      if (code === "KeyS" || key === "s") { if (once) splitAtPlayhead(false); return true; }
      return false;
    }
    if (shift && !ctrl) {
      if (code === "Comma") { extendSelection(-1); return true; }
      if (code === "Period") { extendSelection(1); return true; }
      return false;
    }
    if (ctrl && !shift) {
      if (key === "z") { if (once) undo(); return true; }
      if (key === "y") { if (once) redo(); return true; }
      if (key === "x" || key === "Delete") {
        if (once) { if (selectedClipRef.current !== null) removeClip(); else cutSelection(); }
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
      if ((event.code !== "Space" && event.key !== " ") || typing(event)) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.("button, a")) event.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("keyup", onKeyUp);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keyup", onKeyUp);
    };
  }, [active]);

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
          Play to hear the new narration against the picture — no render, no job. A block sits where its
          sentence is <strong>aimed</strong>; the picture skips at a join because that is the edit.
        </div>
        {/* Ten rows since E5a: the trim's row is paid for by the lane's name
            and its lock sharing one, and the word budget by a word off six
            others (the render harness counts both). */}
        <dl className="os-tl-actions">
          <dt>Drag the green or red handle, or Ctrl+drag</dt>
          <dd>Select a range; snaps to pins and joins, Alt: no snap</dd>
          <dt>Scissors, or Delete</dt>
          <dd>Cut the selection, close the gap</dd>
          <dt>Drag a piece's edge</dt>
          <dd>Trim the cut; drag it back out to restore</dd>
          <dt>S</dt>
          <dd>Split the unlocked lanes at the playhead</dd>
          <dt>A lane's name, or its lock</dt>
          <dd>Edit that channel alone (the others lock), or leave that lane as it is</dd>
          <dt>Drag a sentence block</dt>
          <dd>Re-time it; [ / ] nudge, Reset timing puts it back</dd>
          <dt>The + on Music</dt>
          <dd>The library: add a track at the playhead</dd>
          <dt>Drag a clip, or either of its ends</dt>
          <dd>Move or trim it; Delete removes it</dd>
          <dt>The eye on Music</dt>
          <dd>Hear the voice alone; the render still mixes it</dd>
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
          still be moved, levelled, faded and removed - only its slice is
          fixed, because the file's length is unknown now. The banner names
          the files, says the render will refuse until the clips go or the
          files come back, and carries the button that removes them all in
          one PUT. */}
      {missingMusic.length > 0 && (
        <div className="os-muted os-small">
          {missingMusic.length === 1 ? "A music clip names a file" : `${missingMusic.length} music clips name files`}
          {" "}that {missingMusic.length === 1 ? "is" : "are"} no longer in the library
          ({[...new Set(missingMusic.map((held) => held.file))].join(", ")}), so the render will refuse until{" "}
          {missingMusic.length === 1 ? "it is removed or the file is" : "they are removed or the files are"} put back in
          the library under the same name. {missingMusic.length === 1 ? "It" : "They"} can still be moved, levelled
          and faded here, but not trimmed, and a cut or split across {missingMusic.length === 1 ? "it" : "one"} is
          refused until the lane is locked or the clip removed.{" "}
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
            {missingMusic.length === 1 ? "Remove the stuck clip" : `Remove the ${missingMusic.length} stuck clips`}
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
              title={playing ? "Pause (Space)" : waitingToPlay ? "Starting as soon as the first sentence is ready…" : "Play (Space)"}
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
                  handles live here so they sit in the ruler at any zoom. */}
              <div className="os-tl-ruler" onPointerDown={onRulerPointerDown} onClick={(e) => e.stopPropagation()}>
                <Ruler total={total} pps={pps} scrollEl={scrollEl} />
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
              <div className={`os-tl-music${locks.music ? " locked" : ""}`}>
                {storedMusic.map((held) => {
                  const length = clipLength(held);
                  const width = Math.max(3, length * pps);
                  const held_peaks = filePeaks[held.file];
                  const pooledClip = held_peaks
                    ? poolPeaks(clipPeaks(held_peaks.peaks, held_peaks.bucket_seconds, held), Math.round(width))
                    : [];
                  const classes = ["os-tl-clip",
                    held.id === selectedClip ? "selected" : "",
                    held.missing ? "missing" : ""].filter(Boolean).join(" ");
                  return (
                    <button
                      type="button"
                      key={held.id}
                      ref={attachClip(held.id)}
                      className={classes}
                      aria-pressed={held.id === selectedClip}
                      aria-label={`Music clip ${held.file} at ${timecode(held.at)}`}
                      title={`${held.file}\n${timecode(held.at)} – ${timecode(clipEnd(held))}`
                        + ` (${timecode(held.in)} – ${timecode(held.out)} of the file)`
                        + `\nLevel ${Math.round(clipGain(held.gain) * 100)}%`
                        + `, fades ${held.fade_in.toFixed(1)} s in / ${held.fade_out.toFixed(1)} s out`
                        + (held.missing
                          ? "\n\nMISSING: this file is no longer in the library, so it is silent here and the"
                            + " render will refuse. Drag to move it, set its level and fades, or Delete to remove"
                            + " it — it cannot be trimmed while the file is gone. Or upload the file again under"
                            + " the same name."
                          : "\n\nDrag to move it (Alt or Ctrl: no snapping); drag an end to trim it; Delete removes it.")}
                      style={{ left: held.at * pps, width }}
                      onPointerDown={(event) => onClipPointerDown(event, held)}
                      onClick={(event) => onClipClick(event, held)}
                    >
                      {pooledClip.length > 0 && (
                        <svg
                          className="os-tl-clip-wave"
                          viewBox={`0 0 ${Math.max(1, pooledClip.length)} 100`}
                          preserveAspectRatio="none"
                          aria-hidden="true"
                        >
                          <path d={waveformPath(pooledClip, 100)} />
                        </svg>
                      )}
                      {held.fade_in > 0 && (
                        <span className="os-tl-clip-fade in" style={{ width: Math.min(width, held.fade_in * pps) }} aria-hidden="true" />
                      )}
                      {held.fade_out > 0 && (
                        <span className="os-tl-clip-fade out" style={{ width: Math.min(width, held.fade_out * pps) }} aria-hidden="true" />
                      )}
                      <span className="os-tl-clip-name">{held.missing ? `${held.file} — missing` : held.file}</span>
                      {/* The trim zones: the cursor only - the pointer-down
                          bubbles to the clip, which asks `clipAt` which end
                          it has. A missing clip has a body and no edges
                          (E4c: its slice cannot change), so it gets no
                          zones and no ew-resize cursor - `clipAt` answers
                          "body" for it wherever it is pressed. */}
                      {!held.missing && (
                        <>
                          <span className="os-tl-clip-edge in" aria-hidden="true" />
                          <span className="os-tl-clip-edge out" aria-hidden="true" />
                        </>
                      )}
                    </button>
                  );
                })}
                {/* The dragged clip's new time, painted through its ref. */}
                <div className="os-tl-clip-label" ref={clipLabelRef} style={{ display: "none" }} aria-hidden="true" />
              </div>

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
        <div className="os-tl-inspector">
          <span className="os-tl-clip-file" title={inspected.file}>{inspected.file}</span>
          <span
            className="os-tl-status"
            title={inspected.missing
              ? "This file is no longer in the library, so the clip cannot be trimmed: its slice is of a length "
                + "nobody knows now. Move it, set its level and fades, remove it, or put the file back under the same name."
              : undefined}
          >
            at {timecode(inspected.at)} · {timecode(inspected.in)}–{timecode(inspected.out)} of the file
            {" "}· {clipLength(inspected).toFixed(2)} s
            {inspected.missing && " · the file is missing, so it cannot be trimmed"}
          </span>
          <label className="os-tl-inspector-field">
            Level
            <input
              type="range"
              className="os-tl-range os-tl-level"
              min={0}
              max={100}
              step={1}
              list="os-tl-gain-default"
              disabled={editLocked || locks.music}
              value={Math.round((clipDraft.gain ?? clipGain(inspected.gain)) * 100)}
              aria-label="Music level"
              title={`A linear level, as the render applies it. The studio's default is `
                + `${Math.round((studioGainRef.current ?? DEFAULT_MUSIC_GAIN) * 100)}%.`}
              onChange={(e) => setClipDraft((held) => ({ ...held, gain: Number(e.target.value) / 100 }))}
              onPointerUp={() => { if (clipDraft.gain !== undefined) changeClip(inspected.id, { gain: clipDraft.gain }); }}
              onKeyUp={(e) => {
                if (e.key !== "Enter" && !e.key.startsWith("Arrow") && e.key !== "Home" && e.key !== "End") return;
                if (clipDraft.gain !== undefined) changeClip(inspected.id, { gain: clipDraft.gain });
              }}
              onBlur={() => { if (clipDraft.gain !== undefined) changeClip(inspected.id, { gain: clipDraft.gain }); }}
            />
            <datalist id="os-tl-gain-default">
              <option value={Math.round((studioGainRef.current ?? DEFAULT_MUSIC_GAIN) * 100)} />
            </datalist>
            <span className="os-tl-inspector-value">{Math.round((clipDraft.gain ?? clipGain(inspected.gain)) * 100)}%</span>
          </label>
          <label className="os-tl-inspector-field">
            Fade in
            <Input
              type="number"
              min={0}
              step={0.5}
              style={{ width: 82 }}
              disabled={editLocked || locks.music}
              value={clipDraft.fadeIn ?? String(inspected.fade_in)}
              onChange={(e) => setClipDraft((held) => ({ ...held, fadeIn: e.target.value }))}
              onBlur={(e) => { const s = Number(e.target.value); if (Number.isFinite(s)) changeClip(inspected.id, { fade_in: Math.max(0, s) }); }}
              onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
            />
            s
          </label>
          <label className="os-tl-inspector-field">
            Fade out
            <Input
              type="number"
              min={0}
              step={0.5}
              style={{ width: 82 }}
              disabled={editLocked || locks.music}
              value={clipDraft.fadeOut ?? String(inspected.fade_out)}
              onChange={(e) => setClipDraft((held) => ({ ...held, fadeOut: e.target.value }))}
              onBlur={(e) => { const s = Number(e.target.value); if (Number.isFinite(s)) changeClip(inspected.id, { fade_out: Math.max(0, s) }); }}
              onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
            />
            s
          </label>
          <span className="os-tl-spacer" />
          <button
            type="button"
            className="os-tl-btn text"
            disabled={editLocked || locks.music}
            title="Remove this clip from the Music lane (Delete)"
            onClick={removeClip}
          >
            <Trash2 size={13} /> Remove clip
          </button>
        </div>
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
