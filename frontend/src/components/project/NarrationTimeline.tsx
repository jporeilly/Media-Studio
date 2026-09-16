import { type MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play, Square, X, ZoomIn, ZoomOut } from "lucide-react";
import { api, errorMessage, qs } from "../../api/client";
import { timecode } from "../../lib/format";
import {
  auditionLength,
  clampTime,
  clipsFrom,
  frameTimes,
  narrationPlanKey,
  needsNewFrames,
  pixelsPerSecond,
  poolPeaks,
  pxToSeconds,
  schedule,
  thumbCount,
  waveformPath,
  type NarrationPlan,
  type PlanSentence,
  type Schedule,
  type WaveformPeaks,
} from "../../lib/timeline";
import { Button, ErrorBox, Spinner } from "../ui";

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
}

/** Fetched three at a time. Sixty requests at once queue behind each other in
 *  the browser anyway and give the voice service a thundering herd. */
const CONCURRENCY = 3;
/** Zoom steps: 1 fits the whole recording into the strip, beyond that it scrolls. */
const ZOOMS = [1, 2, 4, 8];
/** How long to wait on the browser for one frame before giving up on the filmstrip. */
const FRAME_TIMEOUT_MS = 6000;
/** Captured small and scaled up by CSS — a filmstrip thumb is about 46px tall. */
const THUMB_W = 160;
const THUMB_H = 90;
/** The lead given to the first scheduled clip, so it lands in the future. */
const START_LEAD = 0.08;

const EMPTY_SCHEDULE: Schedule = { clips: [], overrunning: [], pushed: [], squeezed: [], end: 0 };

/**
 * The timeline view of the transcript: a filmstrip of the video, the waveform of
 * its original audio and one block per sentence, all on ONE horizontal time
 * scale — and a transport that plays the NEW narration against the picture
 * without rendering anything.
 *
 * This exists because the only way to hear a timing change used to be a full
 * re-voice: six of them in twelve minutes on one video, with no visual reference
 * for how far to nudge a sentence. So the point of the view is not the drawing,
 * it is that Play is honest. The clips it fetches are the very cache entries the
 * render will reuse — `preview_url` carries the effective voice and speed the
 * server computed for each sentence — and they are placed by the same rule
 * `assemble_master` uses: a pin is a floor, so a clip that runs long pushes the
 * next one late instead of overlapping it.
 *
 * **Phase 3a has no drag.** Offsets are typed in the List view; this view shows
 * what they did.
 */
export function NarrationTimeline({ projectId, provider, voiceId, speed, active, selected, onSelect }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);

  const plan = useQuery({
    queryKey: [...narrationPlanKey(projectId), provider, voiceId, speed],
    queryFn: () => api.get<NarrationPlan>(
      `/api/projects/${projectId}/narration/plan${qs({ provider, voice: voiceId, speed })}`,
    ),
    enabled: active,
    staleTime: 60_000,
  });

  // The peaks never change for a given audio.wav (the server caches them on its
  // mtime and size), so this is fetched once per project and kept.
  const peaks = useQuery({
    queryKey: ["waveform", projectId],
    queryFn: () => api.get<WaveformPeaks>(`/api/projects/${projectId}/waveform`),
    enabled: active,
    staleTime: Infinity,
    retry: false,
  });

  const sentences = useMemo(() => plan.data?.sentences ?? [], [plan.data]);
  const duration = plan.data?.duration ?? peaks.data?.duration ?? 0;
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
  const clockRef = useRef<HTMLSpanElement | null>(null);
  const sources = useRef(new Map<number, AudioBufferSourceNode>());
  const startedAt = useRef<{ ctxTime: number; position: number } | null>(null);
  const positionRef = useRef(0);
  const frame = useRef(0);
  /** Bumped by every `start` and every `halt`, so a start still inside its
   *  awaits knows it has been superseded and quietly gives up. */
  const startToken = useRef(0);
  const [playing, setPlaying] = useState(false);
  /** Play was pressed before the clips it needs existed: start as soon as they do. */
  const [waitingToPlay, setWaitingToPlay] = useState(false);

  const pps = pixelsPerSecond(total, width, zoom);
  const ppsRef = useRef(pps);
  ppsRef.current = pps;
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
    if (playheadRef.current) playheadRef.current.style.transform = `translateX(${at * ppsRef.current}px)`;
    // Written straight to the DOM, never through state: a setState per frame
    // would re-render every sentence block sixty times a second, and a two-hour
    // recording has fifteen hundred of them.
    if (clockRef.current) clockRef.current.textContent = timecode(at);
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

  const halt = useCallback((at: number) => {
    // Supersede any `start` still inside its awaits, so a press that has not
    // finished resuming the AudioContext cannot resurrect playback after this.
    startToken.current += 1;
    cancelAnimationFrame(frame.current);
    stopSources();
    startedAt.current = null;
    positionRef.current = at;
    videoRef.current?.pause();
    setPlaying(false);
    paint();
  }, [paint, stopSources]);

  const tick = useCallback(function run() {
    // Never two loops at once: `start` is async, so two presses inside its
    // awaits both used to reach here and the first loop ran on for ever,
    // fighting the second over the playhead.
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      positionRef.current = position();
      paint();
      const end = totalRef.current;
      if (positionRef.current >= end) { halt(end); return; }
      // Ran out of prepared narration with more still coming: wait for it
      // rather than running on past sentences in silence.
      if (timingRef.current.end > 0 && positionRef.current > timingRef.current.end + 0.05
          && preparingRef.current) {
        halt(timingRef.current.end);
        setWaitingToPlay(true);
        return;
      }
      run();
    });
  }, [halt, paint, position]);

  const start = useCallback(async () => {
    const token = ++startToken.current;
    const ctx = audioContext();
    // An AudioContext is created suspended until a user gesture; the Play button
    // IS that gesture, so resume before anything is scheduled against its clock.
    if (ctx.state === "suspended") await ctx.resume();
    if (startToken.current !== token) return;  // a halt, or a second press, won
    const from = positionRef.current >= total - 0.05 ? 0 : positionRef.current;
    stopSources();
    const ctxStart = ctx.currentTime + START_LEAD;
    positionRef.current = from;
    startedAt.current = { ctxTime: ctxStart, position: from };
    scheduleFrom(from, ctxStart);
    const video = videoRef.current;
    if (video) {
      try {
        video.currentTime = Math.min(from, Math.max(0, duration - 0.05));
        await video.play();
      } catch { /* the picture is a reference, not the point; the audio plays regardless */ }
    }
    if (startToken.current !== token) return;
    setPlaying(true);
    tick();
  }, [audioContext, duration, scheduleFrom, stopSources, tick, total]);

  /** Play, or pause. With nothing prepared yet this starts the fetching and
   *  plays as soon as the first sentence is decoded, rather than sitting there
   *  disabled. */
  const togglePlay = useCallback(() => {
    if (playing) { halt(position()); return; }
    // A press after the end starts again from the top — decided HERE and not
    // only inside `start`, because the "nothing to play yet" test below has to
    // be made against where playback will really begin.
    const from = positionRef.current >= total - 0.05 ? 0 : positionRef.current;
    positionRef.current = from;
    if (timing.end <= from + 0.05) {
      setWaitingToPlay(true);
      void prepare();  // self-guarding: a second press does not restart the pass
      return;
    }
    void start();
  }, [halt, playing, position, prepare, start, timing.end, total]);

  const cancelPrepare = useCallback(() => {
    // A request already in flight is left to finish and its result dropped:
    // `api.blob` has no abort. Nothing new is started.
    loadToken.current += 1;
    preparingRef.current = false;
    setPrep((c) => ({ ...c, running: false }));
    setWaitingToPlay(false);
  }, []);

  // A NEW PLAN means the decoded clips are audio the render will no longer
  // make, so they are dropped - and playback has to stop with them, or the
  // previous generation goes on playing to the picture's end against a timeline
  // that no longer describes it. Declared here rather than beside the signature
  // because it needs `halt`.
  useEffect(() => {
    halt(0);
    setWaitingToPlay(false);
    loadToken.current += 1;
    preparingRef.current = false;
    buffers.current.clear();
    setClipSeconds({});
    setFailed({});
    setPrep({ total: 0, done: 0, running: false });
    setPrepError(null);
    // `halt` is stable; listing it would re-run this on nothing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planSignature]);

  const seek = useCallback((to: number) => {
    const next = clampTime(to, total);
    const wasPlaying = playing;
    halt(next);
    if (wasPlaying) void start();
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

  // ── the strip's own width ────────────────────────────────────────────────
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, [active]);

  // ── the filmstrip ────────────────────────────────────────────────────────
  //
  // Client-side, with no server work and no ffmpeg: a hidden <video> seeked to N
  // evenly spaced moments, each drawn to a canvas and kept as a data URL.
  // Strictly one seek at a time — await the `seeked` event before drawing, or
  // the canvas gets whatever frame happened to be decoded.
  const [thumbs, setThumbs] = useState<string[]>([]);
  const [filmError, setFilmError] = useState<string | null>(null);
  const thumbsWidth = useRef<number | null>(null);
  const thumbsWanted = thumbCount(pictureWidth);

  useEffect(() => {
    if (!active || !(duration > 0) || thumbsWanted === 0) return;
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
          video.currentTime = at;
          await inTime(seeked);
          pen.drawImage(video, 0, 0, THUMB_W, THUMB_H);
          grabbed.push(canvas.toDataURL("image/jpeg", 0.6));
        }
        if (cancelled) return;
        thumbsWidth.current = pictureWidth;
        setThumbs(grabbed);
        setFilmError(null);
      } catch {
        if (cancelled) return;
        // A container this browser cannot decode (.mkv and .avi are both
        // importable) is not a failure of the timeline: the waveform and the
        // blocks are still the scale that matters.
        thumbsWidth.current = pictureWidth;
        setThumbs([]);
        setFilmError("This browser cannot show frames from this video file — the waveform below is still to scale.");
      } finally {
        video.removeAttribute("src");
        video.load();
      }
    })();

    return () => { cancelled = true; };
  }, [active, duration, pictureWidth, thumbsWanted, projectId]);

  // ── drawing ──────────────────────────────────────────────────────────────
  const pooled = useMemo(
    () => poolPeaks(peaks.data?.peaks ?? [], Math.round(pictureWidth)),
    [peaks.data, pictureWidth],
  );
  const path = useMemo(() => waveformPath(pooled, 100), [pooled]);

  const onStripClick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    seek(pxToSeconds(event.clientX - box.left, pps));
  };

  if (!active) return null;
  if (plan.isLoading) return <Spinner label="Reading the narration…" />;
  if (plan.isError) return <ErrorBox message={errorMessage(plan.error) || "The narration plan could not be read."} />;

  const chosen = selected !== null ? sentences.find((s) => s.index === selected) ?? null : null;
  const ready = timing.clips.length;
  const failures = Object.keys(failed).length;
  // Sentences with words that are not muted and STILL will not be in the
  // render: their offset pins them at or past the end of the video, where the
  // mux drops them. The one state the owner cannot see coming, so it is said
  // once at the top as well as on the block.
  const dropped = sentences.filter((s) => s.past_end);

  return (
    <div className="os-tl">
      <div className="os-muted os-small">
        Play here to hear the new narration against the picture — no render, no job. Every sentence
        is fetched exactly as the re-voice will speak it, so what you hear is what the re-voice will
        make. A block sits where its sentence is <strong>aimed</strong>; a pin is a floor, so a clip
        that runs long is first sped up a little to fit the gap after it, and only marked here when
        it really will push the next sentence late. Offsets are typed in the List view.
      </div>

      {dropped.length > 0 && (
        <div className="os-muted os-small">
          {dropped.length} sentence{dropped.length === 1 ? " is" : "s are"} pinned at or past the end
          of the video, so the re-voice will leave {dropped.length === 1 ? "it" : "them"} out
          entirely: {dropped.map((s) => `#${s.index + 1}`).join(", ")}. Pull the offset back inside
          the video in the List view.
        </div>
      )}

      <div className="os-tl-toolbar">
        <Button
          variant="primary"
          size="sm"
          icon={playing ? <Pause size={14} /> : <Play size={14} />}
          onClick={togglePlay}
        >
          {playing ? "Pause" : waitingToPlay ? "Starting…" : "Play"}
        </Button>
        <Button size="sm" icon={<Square size={13} />} onClick={() => { setWaitingToPlay(false); halt(0); }}>
          Stop
        </Button>
        <span className="os-tl-clock os-muted">
          <span ref={clockRef}>{timecode(0)}</span> / {timecode(total)}
        </span>

        {prep.running ? (
          <>
            <span className="os-muted os-small">
              Preparing {Math.min(prep.done + 1, prep.total)} of {prep.total} — the first sentence
              can take several seconds while the voice service warms up.
            </span>
            <Button size="sm" icon={<X size={13} />} onClick={cancelPrepare}>Cancel</Button>
          </>
        ) : ready + failures < speakable.length ? (
          <Button size="sm" onClick={() => void prepare()}>
            {ready === 0
              ? `Prepare all ${speakable.length} sentences`
              : `Prepare the remaining ${speakable.length - ready - failures}`}
          </Button>
        ) : (
          <span className="os-muted os-small">
            {ready} of {speakable.length} sentences ready
            {failures > 0 && ` — ${failures} could not be spoken`}.
          </span>
        )}

        <span className="os-tl-spacer" />
        <Button
          size="sm"
          icon={<ZoomOut size={14} />}
          aria-label="Zoom out"
          disabled={zoom === ZOOMS[0]}
          onClick={() => setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)])}
        />
        <span className="os-muted os-small">{zoom}×</span>
        <Button
          size="sm"
          icon={<ZoomIn size={14} />}
          aria-label="Zoom in"
          disabled={zoom === ZOOMS[ZOOMS.length - 1]}
          onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)])}
        />
      </div>

      {prepError && <ErrorBox message={prepError} />}
      {peaks.isError && (
        <div className="os-muted os-small">
          {errorMessage(peaks.error)} — the sentence blocks below are still to scale.
        </div>
      )}
      {filmError && <div className="os-muted os-small">{filmError}</div>}

      <div className="os-tl-scroll" ref={scrollRef}>
        {/* ONE time scale, `pps` pixels a second, shared by all three lanes: a
            sentence block sits over the burst it was spoken in. The body is as
            wide as the whole AUDITION; the filmstrip and the waveform are as
            wide as the PICTURE, because that is how much content they have.
            The two are the same until the new narration overruns the video, and
            stretching `duration` seconds of frames across a longer strip would
            slide every burst out from under its sentence. */}
        <div className="os-tl-body" style={{ width: contentWidth || "100%" }} onClick={onStripClick}>
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

          {/* A sibling of the three lanes so it spans all of them. */}
          <div className="os-tl-playhead" ref={playheadRef} />
        </div>
      </div>

      {/* The picture, muted, as the visual reference the transport drives. */}
      <video
        ref={videoRef}
        className="os-tl-picture"
        muted
        playsInline
        preload="metadata"
        src={`/api/projects/${projectId}/tracks/picture`}
      />

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
          Click a block to select the sentence and move the playhead to it; click the strip itself to
          seek anywhere.
        </div>
      )}
    </div>
  );
}
