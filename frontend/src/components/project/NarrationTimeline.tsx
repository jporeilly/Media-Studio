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
  Maximize2,
  Mic,
  Pause,
  Play,
  Redo2,
  Scissors,
  Search,
  SkipBack,
  SkipForward,
  StepBack,
  StepForward,
  Undo2,
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
  describeJoin,
  joins,
  maxZoom,
  positionAfterEdit,
  projectPeaks,
  removeRange,
  sliderFromZoom,
  stepFrame,
  stepZoom,
  ticks,
  toSource,
  wholeKeep,
  wholeSource,
  zoomFromSlider,
  zoomToSelection,
  type Join,
  type Keep,
} from "../../lib/edit";
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
}

/** A range of the OUTPUT, timeline seconds, `start < end`. */
interface Selection {
  start: number;
  end: number;
}
/** One state of the edit: the kept ranges, or `null` for no edit at all. */
type Snapshot = Keep | null;
interface Commit {
  next: Snapshot;
  kind: "cut" | "undo" | "redo";
}
type DragKind = "scrub" | "in" | "out" | "range";
interface Drag {
  kind: DragKind;
  /** The end that is NOT being dragged (a handle), or where the drag began (Ctrl+drag). */
  anchor: number;
  /** Seconds between the pointer and the thing it grabbed, so a handle does not jump to the pointer on the first move. */
  offset: number;
  /** A scrub that never moved is a click: on the ruler that seeks, on the head it does nothing. */
  seekOnClick: boolean;
  startX: number;
  moved: boolean;
  pointerId: number;
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

const EMPTY_SCHEDULE: Schedule = { clips: [], overrunning: [], pushed: [], squeezed: [], end: 0 };

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
 * `keep` before pooling. The edit itself is only ever `keep`, sent whole on
 * release; the transcript on disk never moves (spec §3).
 *
 * Offsets are still typed in the List view; this view shows what they did.
 */
export function NarrationTimeline({ projectId, provider, voiceId, speed, active, selected, onSelect, jobActive }: Props) {
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
  // through exactly this `keep`, and a strip drawn from a plan of one moment
  // and an edit of another would put the blocks over the wrong frames.
  const storedKeep: Snapshot = plan.data?.edit?.keep ?? null;
  // What the server holds, as far as this client knows: the plan's keep,
  // advanced by every commit that SUCCEEDS. The undo stack snapshots THIS,
  // never the plan's copy - which is still the previous edit until the
  // refetch lands - so the history is right even if the lock below were
  // ever bypassed.
  const committedKeepRef = useRef<Snapshot>(storedKeep);
  useEffect(() => { committedKeepRef.current = storedKeep; }, [storedKeep]);
  const sourceDuration = plan.data?.edit?.source_duration ?? duration;
  const sourceDurationRef = useRef(sourceDuration);
  sourceDurationRef.current = sourceDuration;
  /** What the drawing projects through: the stored ranges, or the whole source. */
  const keep = useMemo<Keep>(
    () => (storedKeep && storedKeep.length > 0 ? storedKeep : wholeKeep(sourceDuration)),
    [storedKeep, sourceDuration],
  );
  const keepSignature = JSON.stringify(keep);
  const keepRef = useRef(keep);
  keepRef.current = keep;
  const joinList = useMemo(() => joins(keep, sourceDuration), [keep, sourceDuration]);
  const joinsRef = useRef<Join[]>(joinList);
  joinsRef.current = joinList;

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

  // A CHANGED EDIT: the same source moment is somewhere else on the timeline
  // now (or nowhere, if it was just cut), so the playhead is remapped rather
  // than sent back to zero, and the selection - which was in the old
  // coordinates - is cleared. Declared before the plan effect below, which
  // reads the position this one sets. The previous keep is kept in a ref
  // because the plan and its edit arrive in the same fetch.
  const previousKeepRef = useRef<Keep>(keep);
  useEffect(() => {
    const before = previousKeepRef.current;
    previousKeepRef.current = keep;
    if (JSON.stringify(before) === keepSignature) return;
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
  // ONE request per gesture, on release, with the whole list: the record is
  // rewritten whole and served whole (spec trap 8). Nothing local is drawn
  // from the new list - the plan is invalidated and the strip is redrawn from
  // the plan the server answers with, so a refused edit (a 400 naming the
  // range, a 409 while a job holds the project) leaves the drawing exactly on
  // the server's state with the message shown.
  //
  // Undo is a client-side stack of snapshots, `null` meaning no edit, pushed
  // only when a commit SUCCEEDS. It is lost on reload: the server keeps only
  // the current cut, and the view says so.
  const [history, setHistory] = useState<{ past: Snapshot[]; future: Snapshot[] }>({ past: [], future: [] });
  /** A refusal made here rather than by the server ("keep at least one range"). */
  const [refusal, setRefusal] = useState<string | null>(null);
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
  useEffect(() => {
    if (!awaiting) return;
    // Leaving the tab does NOT clear it: the plan is refetched on return (the
    // commit invalidated it) and that landing is what unlocks - clearing on
    // `!active` would reopen the window for the first gesture after coming
    // back, computed against the plan the tab left with.
    if (plan.dataUpdatedAt !== awaiting.data || plan.errorUpdatedAt !== awaiting.error) setAwaiting(null);
  }, [awaiting, plan.dataUpdatedAt, plan.errorUpdatedAt]);
  /** Between a successful commit and the plan it produced: the strip is about to change. */
  const applying = awaiting !== null;
  const commit = useMutation({
    mutationFn: ({ next }: Commit) => {
      // Back to keep-everything is a DELETE, so the record reads as one that
      // never had an edit, rather than a PUT of the whole source.
      const whole = next === null || (next.length === 1 && wholeSource(next, sourceDurationRef.current));
      return whole
        ? api.delete<EditPayload>(`/api/projects/${projectId}/edit`)
        : api.put<EditPayload>(`/api/projects/${projectId}/edit`, { keep: next });
    },
    onMutate: () => setRefusal(null),
    onSuccess: (_stored, { next, kind }) => {
      // What the server held until this instant is what undo goes back to.
      const before = committedKeepRef.current;
      committedKeepRef.current = next;
      setHistory((h) => {
        if (kind === "cut") return { past: [...h.past, before].slice(-UNDO_DEPTH), future: [] };
        if (kind === "undo") return { past: h.past.slice(0, -1), future: [...h.future, before] };
        return { past: [...h.past, before].slice(-UNDO_DEPTH), future: h.future.slice(0, -1) };
      });
      setSelection(null);
      setAwaiting(planStampRef.current);
      void qc.invalidateQueries({ queryKey: narrationPlanKey(projectId) });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
      void qc.invalidateQueries({ queryKey: ["edit", projectId] });
    },
  });
  const editLocked = jobActive || commit.isPending || applying;
  const editError = refusal ?? (commit.isError ? errorMessage(commit.error) : null);

  /** Camtasia's ripple delete: remove the selection, close the gap. */
  const cutSelection = useCallback(() => {
    const sel = selectionRef.current;
    if (!sel || editLocked) return;
    const next = removeRange(keepRef.current, sel.start, sel.end);
    if (next === null) { setRefusal("Keep at least one range — that selection would remove the whole video."); return; }
    commit.mutate({ next, kind: "cut" });
  }, [commit, editLocked]);
  const undo = useCallback(() => {
    if (history.past.length === 0 || editLocked) return;
    commit.mutate({ next: history.past[history.past.length - 1], kind: "undo" });
  }, [commit, editLocked, history.past]);
  const redo = useCallback(() => {
    if (history.future.length === 0 || editLocked) return;
    commit.mutate({ next: history.future[history.future.length - 1], kind: "redo" });
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

  /** Every drag is captured on the BODY, whichever child began it, so one
   *  pair of move/up handlers serves the ruler, the head and both handles.
   *  `grabbed` is the timeline moment of the thing under the pointer (a
   *  handle, the head), so that thing follows the pointer from where it was
   *  rather than jumping to it. */
  const beginDrag = useCallback((
    event: ReactPointerEvent, kind: DragKind, anchor: number, grabbed?: number,
  ) => {
    const body = bodyRef.current;
    if (!body || event.button !== 0) return;
    try { body.setPointerCapture(event.pointerId); } catch { /* a synthetic pointer */ }
    dragRef.current = {
      kind,
      anchor,
      offset: grabbed === undefined ? 0 : secondsAt(event.clientX) - grabbed,
      seekOnClick: grabbed === undefined,
      startX: event.clientX,
      moved: false,
      pointerId: event.pointerId,
    };
    event.stopPropagation();
    event.preventDefault();
  }, [secondsAt]);

  const onBodyPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.moved) {
      if (Math.abs(event.clientX - drag.startX) < DRAG_SLOP_PX) return;
      drag.moved = true;
      // A scrub pauses: seek on every move, commit nothing.
      if (drag.kind === "scrub") halt(position());
    }
    const t = secondsAt(event.clientX) - drag.offset;
    if (drag.kind === "scrub") {
      const at = clampTime(t, totalRef.current);
      positionRef.current = at;
      syncVideo(at);
      paint();
      return;
    }
    // A handle drags its own end; Ctrl+drag grows from where it began. Painted
    // through the ref, never through state, until release.
    selectionRef.current = drag.kind === "in" ? normalize(t, drag.anchor) : normalize(drag.anchor, t);
    paint();
  }, [halt, normalize, paint, position, secondsAt, syncVideo]);

  const onBodyPointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    try { bodyRef.current?.releasePointerCapture(event.pointerId); } catch { /* already released */ }
    // The browser fires a click after this pointerup; it must not seek again.
    // Cleared on the next tick in case no click follows (a release off-screen).
    suppressClick.current = true;
    setTimeout(() => { suppressClick.current = false; }, 0);
    if (drag.kind === "scrub") {
      // A click on the ruler seeks, and keeps playing if it was; a click on
      // the head, or a pointer the browser cancelled, does nothing.
      if (!drag.moved && drag.seekOnClick && event.type !== "pointercancel") seek(secondsAt(event.clientX));
      return;
    }
    commitSelection(selectionRef.current);
  }, [commitSelection, secondsAt, seek]);

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
    if (event.altKey) return false;
    const ctrl = event.ctrlKey || event.metaKey;
    const shift = event.shiftKey;
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    const code = event.code;
    // Space by `key` as well as by `code`: virtual keyboards and automation
    // set the one without the other.
    const space = code === "Space" || key === " ";
    // Play/pause, a cut and undo/redo fire ONCE per press: held down they
    // would toggle at the repeat rate and fire an undo per repeat (which is
    // how the commit race above was hit by accident). The repeat is still
    // swallowed, so a held Space cannot scroll the page either. Frame
    // stepping and the selection keys keep repeating - holding them is how
    // they are used.
    const once = !event.repeat;
    if (!ctrl && !shift) {
      if (space) { if (once) togglePlay(); return true; }
      if (code === "Comma") { stepBy(-1); return true; }
      if (code === "Period") { stepBy(1); return true; }
      if (key === "Escape") { if (!selectionRef.current) return false; setSelection(null); return true; }
      // Camtasia's plain Delete leaves space on the timeline; this one has no
      // gaps (the edit is ranges of one source), so it closes the gap too.
      if (key === "Backspace" || key === "Delete") { if (once) cutSelection(); return true; }
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
  // render: their offset pins them at or past the end of the video, where the
  // mux drops them. The one state the owner cannot see coming, so it is said
  // once at the top as well as on the block.
  const dropped = sentences.filter((s) => s.past_end);
  const sliderValue = Math.round(sliderFromZoom(zoomNow, zoomMax) * 1000);
  const status = commit.isPending ? "Saving the cut…" : applying ? "Re-reading the plan…" : null;

  return (
    <div className="os-tl">
      <div className="os-muted os-small">
        Play here to hear the new narration against the picture — no render, no job; every sentence
        is fetched exactly as the re-voice will speak it. A block sits where its sentence is{" "}
        <strong>aimed</strong>; a pin is a floor, so a clip that runs long is first sped up a little
        to fit, and only marked when it really will push the next sentence late. Offsets are typed
        in the List view. To cut: drag the green or red handle on the playhead, or Ctrl+drag, to
        select; the scissors (or Ctrl+Delete, Backspace) remove the selection and close the gap;
        Ctrl+Z undoes. The picture skips at a join because that is the edit.
      </div>

      {dropped.length > 0 && (
        <div className="os-muted os-small">
          {dropped.length} sentence{dropped.length === 1 ? " is" : "s are"} pinned at or past the end
          of the video, so the re-voice will leave {dropped.length === 1 ? "it" : "them"} out
          entirely: {dropped.map((s) => `#${s.index + 1}`).join(", ")}. Pull the offset back inside
          the video in the List view.
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
            title="Undo the last cut (Ctrl+Z)"
            disabled={history.past.length === 0 || editLocked}
            onClick={undo}
          >
            <Undo2 size={15} />
          </button>
          <button
            type="button"
            className="os-tl-btn"
            aria-label="Redo"
            title="Redo (Ctrl+Y, Ctrl+Shift+Z)"
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
            title="Remove the selection and close the gap (Ctrl+Delete, Backspace, Ctrl+X)"
            disabled={!selection || editLocked}
            onClick={cutSelection}
          >
            <Scissors size={15} />
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
          {/* Track headers: sticky by construction - the strip scrolls in its own column. */}
          <div className="os-tl-headers" aria-hidden="true">
            <div className="os-tl-header ruler" />
            <div className="os-tl-header"><Video size={13} /> <span>Video</span></div>
            <div className="os-tl-header"><AudioLines size={13} /> <span>Audio · original<small>reference only</small></span></div>
            <div className="os-tl-header"><Mic size={13} /> <span>Narration</span></div>
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

              <div className="os-tl-film" style={{ width: pictureWidth || "100%" }} aria-hidden="true">
                {thumbs.length > 0
                  ? thumbs.map((src, i) => <img key={i} src={src} alt="" draggable={false} />)
                  : <div className="os-tl-film-empty" />}
              </div>

              {/* ONE path for the whole strip — never 2728 rects, never a canvas.
                  See lib/timeline.ts. */}
              <svg
                className="os-tl-wave"
                style={{ width: pictureWidth || "100%" }}
                viewBox={`0 0 ${Math.max(1, pooled.length)} 100`}
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <path d={path} />
              </svg>

              <div className="os-tl-blocks">
                {sentences.map((sentence) => {
                  const landed = startsAt[sentence.index];
                  const moved = Math.abs(sentence.pinned_start - sentence.start) > 0.001;
                  const classes = ["os-tl-block",
                    sentence.speakable ? "" : "muted",
                    sentence.index === selected ? "selected" : "",
                    overrunning.has(sentence.index) ? "overrun" : "",
                    sentence.past_end || failed[sentence.index] ? "failed" : ""].filter(Boolean).join(" ");
                  // Said on the block itself, because it is the one state the owner
                  // cannot see coming: the sentence has words, is not muted, and
                  // will still be missing from the render.
                  const why = sentence.past_end
                    ? "\nDROPPED: its offset pins it at or past the end of the video, so the re-voice leaves it out."
                    : sentence.muted ? "\nMuted." : "";
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
                      <button
                        type="button"
                        className={classes}
                        aria-label={`Sentence ${sentence.index + 1} at ${timecode(sentence.pinned_start)}`}
                        title={`${sentence.text}\n\nSpoken ${timecode(sentence.start)} – ${timecode(sentence.end)}`
                          + `\nAimed at ${timecode(sentence.pinned_start)}`
                          + (landed ? `\nLands at ${timecode(landed.start)}` : "")
                          + (landed?.squeezedHere ? "\nSped up slightly to fit the gap after it." : "")
                          + why}
                        style={{
                          left: sentence.pinned_start * pps,
                          width: Math.max(3, (sentence.end - sentence.start) * pps),
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.ctrlKey || e.metaKey || suppressClick.current) return;  // a Ctrl+drag began here
                          onSelect(sentence.index);
                          seek(landed ? landed.start : sentence.pinned_start);
                        }}
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
              </div>

              {/* Siblings of the lanes so they span all of them: the selection
                  band (painted through its ref while a handle is dragged), a
                  marker at every join with what was removed in its tooltip, and
                  the playhead with its head and clock in the ruler. */}
              <div className="os-tl-selection" ref={bandRef} style={{ display: "none" }} aria-hidden="true" />
              {joinList.map((join) => (
                <div
                  key={join.at}
                  className={join.removed > 0 ? "os-tl-join" : "os-tl-join split"}
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
          {chosen.past_end && (
            <> · <strong>dropped</strong> — its offset pins it at or past the end of the video, so the
              re-voice leaves it out. Pull it back inside to hear it.</>
          )}
          {failed[chosen.index] && <> · {failed[chosen.index]}</>}
        </div>
      ) : (
        <div className="os-muted os-small">
          Click a block to select the sentence and move the playhead to it; click the ruler or the strip
          to seek anywhere. Undo is for this visit — the app keeps only the current cut. Delete removes
          the selection and closes the gap, like Ctrl+Delete: this timeline has no empty space to leave.
        </div>
      )}
    </div>
  );
}
