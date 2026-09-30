/**
 * The audition (R1b — the second part of the timeline component taken out of
 * `NarrationTimeline.tsx`; the design record is docs/porting/edit-timeline.md
 * §14): the decoded sentence clips and the music's buffers, where each clip
 * lands, the transport — `position`, `paint`, `scheduleFrom`, `syncVideo`,
 * `applyShuttle`, `halt`, `tick`, `start`, `togglePlay`, `seek` — the shuttle
 * and its loop, and the twelve effects that keep them honest against the
 * plan, the picture, the clips and the selection. Every body here is the
 * component's own, moved whole: the closure variables each read are the deps
 * the hook takes, the refs it alone writes are created here and returned,
 * and the callbacks keep their names, so the root's handlers did not change.
 * `paint` came with it because the loops and the effects here call it four
 * times over; it is handed the selection's five nodes and the drag record,
 * which stay the root's, as `ppsRef` does (the root declares it bare and
 * assigns it where the zoom computes `pps`). The paint-on-zoom effect stays
 * in the root: it reads `pps`, which the root computes from `total`.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { api, errorMessage } from "../../../api/client";
import { timecode } from "../../../lib/format";
import {
  EPSILON,
  clipLength,
  clipPlayback,
  fadePoints,
  outputDuration,
  positionAfterEdit,
  stepFrame,
  toSource,
  type Join,
  type Keep,
  type MusicClip,
} from "../../../lib/edit";
import {
  auditionLength,
  clampTime,
  clipsFrom,
  reachedEnd,
  schedule,
  type PlanSentence,
  type Schedule,
  type Squeeze,
} from "../../../lib/timeline";
import {
  FORWARD,
  STOPPED,
  type Shuttle,
  type ShuttleKey,
  nextShuttle,
  seekThrottle,
  shuttleAdvance,
  shuttleFloor,
  shuttleLabel,
} from "../../../lib/shuttle";
import { CONCURRENCY, EMPTY_SCHEDULE, LATE_LEAD, START_LEAD, type Drag, type Selection } from "./types";

export interface AuditionDeps {
  /** Only the open tab plays: leaving it halts where it was. */
  active: boolean;
  sentences: PlanSentence[];
  /** The sentences the render will speak - the server's answer, filtered by the root. */
  speakable: PlanSentence[];
  squeeze: Squeeze;
  /** The picture's length (the output's, with the removed ranges closed up). */
  duration: number;
  durationRef: RefObject<number>;
  sourceDurationRef: RefObject<number>;
  /** The PICTURE's axis, its signature, and the ref the stable callbacks read it through. */
  keep: Keep;
  keepRef: RefObject<Keep>;
  keepSignature: string;
  /** The video's joins: the picture is re-seeked as each is crossed. */
  joinsRef: RefObject<Join[]>;
  /** The Music lane's clips, their signature, and the ref `scheduleFrom` and `togglePlay` read. */
  storedMusic: MusicClip[];
  musicRef: RefObject<MusicClip[]>;
  musicSignature: string;
  /** The committed selection - the root's state - and the ref the painter and the transport read. */
  selection: Selection | null;
  selectionRef: RefObject<Selection | null>;
  setSelection: Dispatch<SetStateAction<Selection | null>>;
  /** The drag in flight: `paint` reads it for the ⌖ on the moving end's label. */
  dragRef: RefObject<Drag | null>;
  /** Pixels per second, as the zoom computes it every render; declared bare by the root before this hook is called. */
  ppsRef: RefObject<number>;
  /**
   * The playhead, and the audition's length, as refs: the root's objects,
   * because the strip's own stable callbacks - the drags, the zoom, the
   * markers - read them (R1b's one boundary correction, R1a's pattern). The
   * playhead is written here by every halt, start, frame and key, and by
   * the root's scrub as before; the length is written here alone.
   */
  positionRef: RefObject<number>;
  totalRef: RefObject<number>;
  /** The selection's five painted nodes: the two handles, the band and its two labels. */
  inHandleRef: RefObject<HTMLDivElement | null>;
  outHandleRef: RefObject<HTMLDivElement | null>;
  bandRef: RefObject<HTMLDivElement | null>;
  inLabelRef: RefObject<HTMLSpanElement | null>;
  outLabelRef: RefObject<HTMLSpanElement | null>;
}

export function useAudition({
  active, sentences, speakable, squeeze, duration, durationRef, sourceDurationRef, keep, keepRef, keepSignature, joinsRef,
  storedMusic, musicRef, musicSignature, selection, selectionRef, setSelection, dragRef, ppsRef, positionRef, totalRef,
  inHandleRef, outHandleRef, bandRef, inLabelRef, outLabelRef,
}: AuditionDeps) {
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
  totalRef.current = total;

  // ── the transport ────────────────────────────────────────────────────────
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const playheadRef = useRef<HTMLDivElement | null>(null);
  const headClockRef = useRef<HTMLSpanElement | null>(null);
  const clockRef = useRef<HTMLSpanElement | null>(null);
  const sources = useRef(new Map<number, AudioBufferSourceNode>());
  const startedAt = useRef<{ ctxTime: number; position: number } | null>(null);
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
  // ── the shuttle (E5c) ────────────────────────────────────────────────────
  //
  // `J` / `K` / `L`. The state is a ref, never React state: the loop reads
  // it every frame, and the label beside the clock is written through its
  // ref as the clock is (`paint`'s rule). Forward at 1× is the audition
  // proper - `start`'s audio play - and every other moving state is the
  // SILENT shuttle: no audio source is made or scheduled (trap 40), the
  // playhead advances at `rate × real time` on `shuttleLoop`, and the muted
  // `<video>` runs at `playbackRate = rate` forward or is seeked along
  // backwards. `halt` resets it, so a stop, a seek, a bound and the end all
  // put the rate back to 1×.
  const shuttleRef = useRef<Shuttle>(STOPPED);
  const shuttleLabelRef = useRef<HTMLSpanElement | null>(null);
  /** `performance.now()` at the last shuttle frame, so each frame advances by real elapsed time. */
  const shuttleClockRef = useRef(0);
  /** Where a backwards shuttle stops: the selection's start when it began inside one, else 0. */
  const shuttleFloorRef = useRef(0);
  /** `performance.now()` at the last backwards seek of the `<video>`, for the throttle. */
  const lastSeekRef = useRef(Number.NEGATIVE_INFINITY);

  const position = useCallback(() => {
    const started = startedAt.current;
    const ctx = ctxRef.current;
    if (!started || !ctx) return positionRef.current;
    // Clamped at the start, because the clock reads BEFORE it during the lead.
    return Math.max(started.position, started.position + (ctx.currentTime - started.ctxTime));
  }, [positionRef]);

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
  }, [bandRef, dragRef, durationRef, inHandleRef, inLabelRef, outHandleRef, outLabelRef, positionRef, ppsRef, selectionRef]);

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
  }, [audioContext, musicBus, musicRef]);

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
  }, [keepRef, sourceDurationRef]);

  /**
   * The shuttle's state, and the picture for it: forward runs the muted
   * `<video>` at the rate (1 for the audition proper, whose `start` has
   * already played it); backwards and stopped leave it PAUSED at 1× - a
   * backwards shuttle is seeked, never played. The ONE place the element's
   * `playbackRate` is written, so it can only ever be above 1 while the
   * shuttle is forward, and is back at 1 on every halt. The label beside the
   * clock is written here too, through its ref.
   */
  const applyShuttle = useCallback((next: Shuttle) => {
    shuttleRef.current = next;
    if (shuttleLabelRef.current) shuttleLabelRef.current.textContent = shuttleLabel(next);
    const video = videoRef.current;
    if (!video) return;
    if (next.direction === 1) {
      video.playbackRate = next.rate;
      if (video.paused && positionRef.current < durationRef.current) {
        video.play().catch(() => { /* the picture is a reference, not the point */ });
      }
    } else {
      video.playbackRate = 1;
      video.pause();
    }
  }, [durationRef, positionRef]);

  const halt = useCallback((at: number) => {
    // Supersede any `start` still inside its awaits, so a press that has not
    // finished resuming the AudioContext cannot resurrect playback after this.
    startToken.current += 1;
    cancelAnimationFrame(frame.current);
    stopSources();
    startedAt.current = null;
    positionRef.current = at;
    // Stopped: the picture paused at 1×, the shuttle's rate reset (E5c).
    applyShuttle(STOPPED);
    syncVideo(at);
    setPlaying(false);
    paint();
  }, [applyShuttle, paint, stopSources, syncVideo, positionRef]);

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
  }, [halt, paint, position, syncVideo, durationRef, joinsRef, positionRef, totalRef]);

  /**
   * The silent shuttle's loop (E5c), `tick`'s twin for the states that have
   * no audio clock: each frame advances the playhead by `rate × real time`
   * (`performance.now()`'s delta), paints, and handles the picture - forward,
   * the `<video>` is already running at the rate (`applyShuttle`) and is
   * re-synced at every join and paused past the picture's end exactly as
   * `tick` does; backwards, it is seeked to the playhead at most about
   * fifteen times a second, so the element is not thrashed. A bound - 0 or
   * the selection's start behind, the selection's end or the output's end
   * ahead - halts through `halt`, which resets the state. The same `frame`
   * ref as `tick`, cancelled first, so there are never two loops at once;
   * and an end that is not known yet is no bound (`tick`'s own rule).
   */
  const shuttleLoop = useCallback(function run() {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const state = shuttleRef.current;
      if (state.direction === 0) return;
      const now = performance.now();
      const dt = (now - shuttleClockRef.current) / 1000;
      shuttleClockRef.current = now;
      const end = totalRef.current;
      const ceiling = playBoundRef.current ?? (end > 0 ? end : Number.POSITIVE_INFINITY);
      const { position: at, hit } = shuttleAdvance(positionRef.current, state, dt, shuttleFloorRef.current, ceiling);
      positionRef.current = at;
      paint();
      if (hit) { halt(at); return; }
      if (state.direction === 1) {
        const join = nextJoinRef.current;
        if (join && at >= join.at) {
          syncVideo(at);
          nextJoinRef.current = joinsRef.current.find((j) => j.at > at + EPSILON) ?? null;
        }
        const video = videoRef.current;
        if (video && !video.paused && at >= durationRef.current) video.pause();
      } else if (seekThrottle(lastSeekRef.current, now)) {
        lastSeekRef.current = now;
        syncVideo(at);
      }
      run();
    });
  }, [halt, paint, syncVideo, durationRef, joinsRef, positionRef, totalRef]);

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
  }, [positionRef, selectionRef, totalRef]);

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
    // Forward at 1×, with audio: the shuttle's state for a play (E5c).
    applyShuttle(FORWARD);
    setPlaying(true);
    tick();
  }, [applyShuttle, audioContext, prepareMusic, scheduleFrom, startFrom, stopSources, syncVideo, tick, joinsRef, positionRef, selectionRef]);

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
  }, [halt, position, prepare, prepareMusic, start, startFrom, timing.end, musicRef, positionRef]);

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
  }, [waitingToPlay, playing, timing.end, prep.running, start, positionRef]);

  // Leaving the tab (or the page) must not leave the narration playing over a
  // video nobody can see. Paused where it was, not rewound: the List tab is
  // where an offset is typed, and coming back to find the playhead back at zero
  // would make "change it, hear it again" worse rather than better.
  //
  // Coming BACK (E6): the strip returned nothing while the tab was away, so
  // the clock, the playhead and the `<video>` are new nodes, drawn at 0 until
  // something paints them - the position was held all along, and Play resumed
  // from it, but nothing showed it. So on `active` turning true again the
  // strip is painted once and the new picture seeked to the held position;
  // on the first mount, when nothing was held, neither.
  const wasActiveRef = useRef(active);
  useEffect(() => {
    const rejoined = active && !wasActiveRef.current;
    wasActiveRef.current = active;
    if (!active) { halt(position()); setWaitingToPlay(false); return; }
    if (rejoined) {
      paint();
      syncVideo(positionRef.current);
    }
  }, [active, halt, paint, position, positionRef, syncVideo]);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  // The committed selection: mirrored into the ref the painter and the
  // transport read, then painted once. Mid-play the bound follows it - gone
  // when the selection is cleared, the new end when the playhead is inside
  // the new selection, else none - so Escape or a handle drag while playing
  // does not leave playback halting at an end that no longer exists.
  useEffect(() => {
    selectionRef.current = selection;
    // Both of a shuttle's bounds follow the selection the same way (E5c): a
    // forward shuttle's ceiling by the play's rule above, a backwards
    // shuttle's floor by `shuttleFloor` - so Escape or a handle drag
    // mid-shuttle does not leave it halting at a start that no longer exists
    // either (the Reviewer's MINOR 2).
    if (startedAt.current || shuttleRef.current.direction === 1) {
      const at = position();
      playBoundRef.current = selection && at >= selection.start - EPSILON && at < selection.end - EPSILON
        ? selection.end : null;
    }
    if (shuttleRef.current.direction === -1) shuttleFloorRef.current = shuttleFloor(selection, positionRef.current);
    paint();
  }, [selection, paint, position, positionRef, selectionRef]);

  /** Comma / Period: a frame is 1/30 s here (see FRAME_SECONDS); stepping pauses. */
  const stepBy = useCallback((direction: 1 | -1) => {
    halt(stepFrame(position(), direction, totalRef.current));
  }, [halt, position, totalRef]);
  const jumpToEnd = useCallback(() => { halt(totalRef.current); }, [halt, totalRef]);
  /**
   * `J` / `K` / `L` (E5c): one transition of `nextShuttle`'s table, and what
   * it means for the audition. `K` is a stop, through `halt` as Pause is,
   * and it gives up a Play that was waiting for its first sentence. Forward
   * at 1× is a REAL play: from a stop it is Space's own path (`togglePlay` -
   * the prepare, the wait for the first sentence), and out of a backwards
   * shuttle it is `start` from where the playhead is. Everything else is
   * the silent shuttle: the audio - if any is playing - is stopped and its
   * clock origin dropped WITHOUT `halt`'s side effects (the playhead stays
   * exactly where the clock had it, the selection's bound and the next join
   * stand), a `start` still inside its awaits is superseded as `halt`
   * supersedes it, the loop runs on real time from now, and the Play button
   * reads Pause. No audio source is made or scheduled on this path (trap 40).
   */
  const shuttleKey = useCallback((key: ShuttleKey) => {
    const before = shuttleRef.current;
    const next = nextShuttle(before, key);
    if (next.direction === 0) { halt(position()); setWaitingToPlay(false); return; }
    if (next.direction === 1 && next.rate === 1) {
      if (before.direction === 0) { togglePlay(); return; }
      // Out of a backwards shuttle: `seek`'s own two lines. `halt` cancels
      // the loop, resets the state and sets `playing` false BEFORE `start`
      // enters its awaits - so nothing moves the playhead meanwhile, and a
      // second L inside them is the ordinary second press the token resolves
      // as a play, never a stop (the Reviewer's MINOR 1).
      halt(positionRef.current);
      void start(positionRef.current);
      return;
    }
    positionRef.current = position();
    startToken.current += 1;
    stopSources();
    startedAt.current = null;
    setWaitingToPlay(false);
    if (next.direction === -1 && before.direction !== -1) {
      // Backwards from here: the floor is the selection's start when the
      // playhead is inside one, else 0 (`shuttleFloor`); the selection effect
      // keeps it current while the shuttle runs, as it keeps a play's bound.
      // The first frame seeks at once.
      shuttleFloorRef.current = shuttleFloor(selectionRef.current, positionRef.current);
      lastSeekRef.current = Number.NEGATIVE_INFINITY;
    }
    shuttleClockRef.current = performance.now();
    applyShuttle(next);
    setPlaying(true);
    shuttleLoop();
  }, [applyShuttle, halt, position, shuttleLoop, start, stopSources, togglePlay, positionRef, selectionRef]);

  return {
    total, startsAt, overrunning, clipSeconds, failed, prep, prepError, prepare, cancelPrepare,
    musicFiles, musicPrep, musicError, musicMuted, setMusicMuted, playing, waitingToPlay,
    position, paint, planSignature, halt, seek, togglePlay, stepBy, jumpToEnd, shuttleKey, syncVideo,
    videoRef, playheadRef, headClockRef, clockRef, shuttleLabelRef,
  };
}
