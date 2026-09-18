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
import {
  AudioLines,
  Lock,
  Maximize2,
  Mic,
  Pause,
  Play,
  Redo2,
  RotateCcw,
  Scissors,
  Search,
  SkipBack,
  SkipForward,
  SquareSplitHorizontal,
  StepBack,
  StepForward,
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
  EPSILON,
  FRAME_SECONDS,
  UNDO_DEPTH,
  anchoredScrollLeft,
  clickSelectsPiece,
  describeJoin,
  dragOffsets,
  joins,
  maxZoom,
  nextEditForCut,
  nextEditForSplit,
  outputDuration,
  pieceAt,
  pieces,
  positionAfterEdit,
  projectPeaks,
  releaseSuppressesClick,
  sameEdit,
  sliderFromZoom,
  snap,
  stepFrame,
  stepZoom,
  ticks,
  toSource,
  trackBody,
  unmovedRelease,
  wholeKeep,
  zoomFromSlider,
  zoomToSelection,
  type DragKind,
  type Join,
  type Keep,
  type Piece,
  type Track,
  type TrackEdit,
  type TrackLocks,
} from "../../lib/edit";
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
import { ErrorBox, Spinner } from "../ui";

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
/** Camtasia's locks per editable track (lib/edit.ts). Audio · original has no list of its own: it follows Video. */
type Locks = TrackLocks;
/** One state of the edit: each track's kept ranges, or `null` for a track that keeps everything. */
type EditState = TrackEdit;
/**
 * One operation the client can send: the whole edit (both lists), or a set
 * of offsets by index. An undo entry holds one of each direction — what to
 * send to undo it and what to send to redo it (spec §11.5).
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
const UNLOCKED: Locks = { video: false, narration: false };
const NO_BLOCKS: ReadonlySet<number> = new Set();

const EMPTY_SCHEDULE: Schedule = { clips: [], overrunning: [], pushed: [], squeezed: [], end: 0 };

function readLocks(projectId: string): Locks {
  try {
    const held = JSON.parse(localStorage.getItem(locksKey(projectId)) ?? "null");
    if (held && typeof held === "object") return { video: held.video === true, narration: held.narration === true };
  } catch { /* no storage, or not ours: unlocked */ }
  return UNLOCKED;
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
 * lock icons say which tracks a Cut or a split applies to — clicking a
 * track's name selects that channel and locks the other — and a locked
 * track's list is left exactly as it is (trap 19). A sentence block can be
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
  // What the server holds, as far as this client knows: the plan's lists,
  // advanced by every commit that SUCCEEDS. The undo stack snapshots THIS,
  // never the plan's copy - which is still the previous edit until the
  // refetch lands - so the history is right even if the lock below were
  // ever bypassed. The same for the offsets: the page's stored copy,
  // advanced by every drag that lands.
  const committedRef = useRef<EditState>({ video: storedVideo, narration: storedNarration });
  useEffect(() => { committedRef.current = { video: storedVideo, narration: storedNarration }; }, [storedVideo, storedNarration]);
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
  // The pieces per lane - the stretches between boundaries, what a click selects.
  const videoPieces = useMemo(() => pieces(keep, sourceDuration), [keep, sourceDuration]);
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
  const toggleLock = useCallback((track: Track) => {
    changeLocks({ ...locksRef.current, [track]: !locksRef.current[track] });
  }, [changeLocks]);
  /** The channel whose name is selected: the one unlocked track while the other is locked. */
  const channel: Track | null = locks.video !== locks.narration ? (locks.video ? "narration" : "video") : null;
  const selectChannel = useCallback((track: Track) => {
    const other: Track = track === "video" ? "narration" : "video";
    const held = locksRef.current;
    changeLocks(!held[track] && held[other] ? UNLOCKED : { [track]: false, [other]: true } as Locks);
  }, [changeLocks]);

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
    if (inLabelRef.current) {
      inLabelRef.current.textContent = sel ? timecode(sel.start) : "";
      inLabelRef.current.style.transform = `translateX(${inAt}px) translateX(-100%)`;
    }
    if (outLabelRef.current) {
      outLabelRef.current.textContent = sel ? timecode(sel.end) : "";
      outLabelRef.current.style.transform = `translateX(${outAt}px)`;
    }
  }, []);

  const stopSources = useCallback(() => {
    sources.current.forEach((node) => {
      node.onended = null;
      try { node.stop(); } catch { /* already finished */ }
      node.disconnect();
    });
    sources.current.clear();
  }, []);

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
  }, [audioContext]);

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
  }, [audioContext, scheduleFrom, startFrom, stopSources, syncVideo, tick]);

  /** Play, or pause. With nothing prepared yet this starts the fetching and
   *  plays as soon as the first sentence is decoded, rather than sitting there
   *  disabled. */
  const togglePlay = useCallback(() => {
    if (playingRef.current) { halt(position()); return; }
    const from = startFrom();
    positionRef.current = from;
    if (timing.end <= from + 0.05) {
      setWaitingToPlay(true);
      void prepare();  // self-guarding: a second press does not restart the pass
      return;
    }
    void start();
  }, [halt, position, prepare, start, startFrom, timing.end]);

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
  /** Take the blocks a move painted through their transforms back to where the plan draws them. */
  const clearMoved = useCallback(() => {
    blockNodes.current.forEach((node) => { node.style.transform = ""; });
    dragGhosts.current.forEach((node) => { node.style.display = "none"; });
    if (moveLabelRef.current) moveLabelRef.current.style.display = "none";
  }, []);
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
      // Back to keep-everything on both tracks is a DELETE, so the record
      // reads as one that never had an edit, rather than a PUT of the whole
      // source. A whole-source SPLIT (two touching ranges) is still a PUT -
      // its boundary is the point.
      const video = trackBody(op.video, sourceDurationRef.current);
      const narration = trackBody(op.narration, sourceDurationRef.current);
      return video === null && narration === null
        ? api.delete<EditPayload>(`/api/projects/${projectId}/edit`)
        : api.put<EditPayload>(`/api/projects/${projectId}/edit`, { video, narration });
    },
    onMutate: () => setRefusal(null),
    onSuccess: (answer, variables) => {
      const { op } = variables;
      // What the server held until this instant is what undo goes back to.
      if (op.kind === "edit") {
        committedRef.current = { video: op.video, narration: op.narration };
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
  const editError = refusal ?? (commit.isError ? errorMessage(commit.error) : null);
  const bothLocked = locks.video && locks.narration;

  /**
   * Commit a new edit (a cut, a split) with what it replaces as its undo.
   * Compared and stored in the PUT body's own terms - a whole track is null -
   * so a split on the very start of an untouched track, which makes a list
   * that is still the whole source, commits nothing and leaves no undo entry.
   */
  const commitEdit = useCallback((next: EditState) => {
    const before = committedRef.current;
    const source = sourceDurationRef.current;
    if (sameEdit(next, before, source)) return;
    const after: EditState = { video: trackBody(next.video, source), narration: trackBody(next.narration, source) };
    commit.mutate({ kind: "do", op: { kind: "edit", ...after }, before: { kind: "edit", ...before } });
  }, [commit]);

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
    if (held.video && held.narration) return;
    const outcome = nextEditForCut(committedRef.current, held, sel.start, sel.end, sourceDurationRef.current);
    if (outcome.refused) {
      const track = outcome.refused === "video" ? "picture" : "narration";
      setRefusal(`Keep at least one range — that selection would remove the whole ${track}.`);
      return;
    }
    commitEdit(outcome.next);
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
    commitEdit(nextEditForSplit(committedRef.current, locksRef.current, positionRef.current, sourceDurationRef.current, all));
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
  const beginDrag = useCallback((
    event: ReactPointerEvent, kind: DragKind, anchor: number, grabbed?: number, move?: Pick<Drag, "index" | "members" | "snapTo">,
  ) => {
    const body = bodyRef.current;
    if (!body || event.button !== 0) return;
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
    };
    event.stopPropagation();
    event.preventDefault();
  }, [secondsAt]);

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
      if (!(event.ctrlKey || event.metaKey) && drag.snapTo) {
        const landed = snap(grabbed.pinned_start + delta, drag.snapTo, SNAP_PX / pps);
        delta = landed.t - grabbed.pinned_start;
        snapped = landed.snapped;
      }
      const pins = members.map((s) => s.pinned_start);
      delta = Math.max(delta, -Math.min(...pins));
      delta = Math.min(delta, totalRef.current - Math.max(...pins));
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
    // A handle drags its own end; Ctrl+drag grows from where it began. Painted
    // through the ref, never through state, until release.
    selectionRef.current = drag.kind === "in" ? normalize(t, drag.anchor) : normalize(drag.anchor, t);
    paint();
  }, [halt, normalize, paint, paintMarquee, position, secondsAt, syncVideo]);

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
        case "pick": pickOnLane("narration", narrationPiecesRef.current, event.clientX); return;
        case "keep-selection": commitSelection(selectionRef.current); return;
        case "click": if (moveLabelRef.current) moveLabelRef.current.style.display = "none"; return;
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
  }, [clearMoved, commitOffsets, commitSelection, pickOnLane, secondsAt, seek, sentences]);

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
      // Escape clears the block selection first, then (a second press) the range.
      if (key === "Escape") {
        if (selectedBlocksRef.current.size > 0) { setSelectedBlocks(NO_BLOCKS); return true; }
        if (!selectionRef.current) return false;
        setSelection(null);
        return true;
      }
      // Camtasia's plain Delete leaves space on the timeline; this one has no
      // gaps (the edit is ranges of one source), so it closes the gap too.
      if (key === "Backspace" || key === "Delete") { if (once) cutSelection(); return true; }
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
      if (key === "x" || key === "Delete") { if (once) cutSelection(); return true; }
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
  const cutTitle = bothLocked
    ? "Both tracks are locked — unlock one to cut"
    : "Remove the selection from the unlocked tracks and close the gap (Ctrl+Delete, Backspace, Ctrl+X)";
  const lockTitle = (track: Track) => (locks[track]
    ? `Unlock ${track}: cuts and splits apply to it again`
    : `Lock ${track}: cuts and splits leave it exactly as it is`);
  const nameTitle = (track: Track) => (channel === track
    ? "Selected channel — click again to unlock both tracks"
    : `Select the ${track} channel: the other track locks, so a cut or a split edits just this one`);

  return (
    <div className="os-tl">
      <div className="os-muted os-small">
        Play here to hear the new narration against the picture — no render, no job; every sentence
        is fetched exactly as the re-voice will speak it. A block sits where its sentence is{" "}
        <strong>aimed</strong>; a pin is a floor, so a clip that runs long is first sped up a little
        to fit, and only marked when it really will push the next sentence late. To cut: drag the
        green or red handle on the playhead, or Ctrl+drag, to select; the scissors (or Ctrl+Delete,
        Backspace) remove the selection and close the gap; Ctrl+Z undoes. The picture skips at a
        join because that is the edit. Click a track's <strong>name</strong> to edit just that
        channel (the other track locks; the lock icons toggle one at a time), and a locked track
        keeps every pin exactly where it is. <strong>S</strong> splits the unlocked tracks at the
        playhead (Ctrl+Shift+S all of them); once a lane is split, clicking a piece selects it
        (until then a click on a lane seeks, as on the ruler). <strong>Drag</strong> a
        sentence block to move it — it snaps to the playhead, the joins, the other sentences and
        its own spoken moment (hold Ctrl to drag freely) — or nudge the selected blocks with
        [ and ] (Shift for a quarter second); Reset timing puts them back where they were spoken.
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

      {editError && <ErrorBox message={editError} />}
      {prepError && <ErrorBox message={prepError} />}
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
              title="Step back one frame (,) — a frame is 1/30 s here; the source's own rate is not known without ffprobe"
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
              title="Step forward one frame (.) — a frame is 1/30 s here; the source's own rate is not known without ffprobe"
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
            disabled={!selection || editLocked || bothLocked}
            onClick={cutSelection}
          >
            <Scissors size={15} />
          </button>
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Split at the playhead"
            title={bothLocked ? "Both tracks are locked — unlock one to split (Ctrl+Shift+S splits all)" : "Split the unlocked tracks at the playhead (S; Ctrl+Shift+S splits all)"}
            disabled={editLocked || bothLocked}
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
            className="os-tl-zoom"
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
              <button type="button" className="os-tl-lock" aria-label={lockTitle("video")} aria-pressed={locks.video} title={lockTitle("video")} onClick={() => toggleLock("video")}>
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
              <button type="button" className="os-tl-lock" aria-label={lockTitle("narration")} aria-pressed={locks.narration} title={lockTitle("narration")} onClick={() => toggleLock("narration")}>
                {locks.narration ? <Lock size={12} /> : <Unlock size={12} />}
              </button>
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
                  blocks. */}
              {(["video", "audio"] as const).map((lane) => (
                <div key={lane} className={`os-tl-pieces ${lane}${locks.video ? " locked" : ""}`} onClick={onPieceClick}>
                  {videoPieces.map((piece) => (
                    <div
                      key={piece.start}
                      className="os-tl-piece"
                      style={{ left: piece.start * pps, width: Math.max(1, (piece.end - piece.start) * pps) }}
                      title={`${timecode(piece.start)} – ${timecode(piece.end)} (${timecode(piece.sourceStart)} – ${timecode(piece.sourceEnd)} of the source). Click to select.`}
                    />
                  ))}
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
                <div className={`os-tl-pieces narration${locks.narration ? " locked" : ""}`}>
                  {narrationPieces.map((piece) => (
                    <div
                      key={piece.start}
                      className="os-tl-piece"
                      style={{ left: piece.start * pps, width: Math.max(1, (piece.end - piece.start) * pps) }}
                      title={`${timecode(piece.start)} – ${timecode(piece.end)} (${timecode(piece.sourceStart)} – ${timecode(piece.sourceEnd)} of the source). Click to select.`}
                    />
                  ))}
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
                          + "\n\nDrag to move it (Ctrl: no snapping); [ and ] nudge the selection"
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

              {/* Siblings of the lanes so they span all of them: the selection
                  band (painted through its ref while a handle is dragged; it
                  skips a locked lane), a marker at every VIDEO join with what
                  was removed in its tooltip (the narration's are on its own
                  lane), and the playhead with its head and clock in the ruler. */}
              <div
                className={`os-tl-selection${locks.video ? " no-video" : ""}${locks.narration ? " no-narration" : ""}`}
                ref={bandRef}
                style={{ display: "none" }}
                aria-hidden="true"
              />
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
