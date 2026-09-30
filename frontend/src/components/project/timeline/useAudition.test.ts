// @vitest-environment jsdom
/**
 * The audition as BEHAVIOUR (R1b): the hook mounted for real under jsdom
 * with a fake Web Audio context, a fake `<video>` and a frame clock stepped
 * by hand, so what a regex over the component's source used to pin - the
 * linear fade envelope, the ⌖ on a handle's label, the wait for the first
 * sentence, the frame step, every J/K/L transition, the silent shuttle that
 * touches no audio graph, the loop's advance and bounds and throttled seek,
 * the `<video>`'s rate written in one place and reset by every stop, the
 * selection mirror - is asserted on what the fakes were told and what the
 * refs read. What a count over the file says best (one writer, one starter,
 * no exponential ramp, the state as a ref) is pinned on the hook's own text
 * at the foot. Each test was watched failing with its defect planted; the
 * round's report lists the plants.
 */
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../../api/client";
import { fadePoints, joins, positionAfterEdit, round3, stepFrame, toSource, wholeKeep, type Keep, type MusicClip } from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import type { PlanSentence } from "../../../lib/timeline";
import { SEEK_HZ } from "../../../lib/shuttle";
import { mountHook } from "./testing/mountHook";
import { FakeAudioContext, FakeVideo, FrameClock, blobOf } from "./testing/fakes";
import { LATE_LEAD, START_LEAD, type Drag, type Selection } from "./types";
import { useAudition, type AuditionDeps } from "./useAudition";
import auditionSource from "./useAudition.ts?raw";

vi.mock("../../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../../api/client")>();
  return { ...real, api: { ...real.api, blob: vi.fn(), get: vi.fn() } };
});

const SOURCE = 12;
const ref = <T,>(current: T) => ({ current });
const sentence = (index: number, start: number, end: number, over: Partial<PlanSentence> = {}): PlanSentence => ({
  index, start, end, pinned_start: start, text: `s${index}`, voice: "en-GB", speed: 1, muted: false, past_end: false,
  speakable: true, window: 5, squeezable: false, preview_url: `/p${index}`, ...over,
});
/** Three sentences, two seconds each once decoded: they land at 0, 3 and 6, so the narration ends at 8 inside a 12 s picture. */
const SENTENCES = [sentence(0, 0, 2), sentence(1, 3, 5), sentence(2, 6, 8)];
const clip = (over: Partial<MusicClip> = {}): MusicClip => ({ id: "m1", file: "bed.mp3", at: 0, in: 0, out: 6, gain: 0.5, fade_in: 2, fade_out: 4, file_duration: 30, ...over });
const gone = clip({ id: "g", file: "gone.mp3", missing: true, file_duration: null });
/** How long each fake fetch "decodes" to, by the path the hook asks for. */
const SECONDS: Record<string, number> = { "/p0": 2, "/p1": 2, "/p2": 2, "/api/music/bed.mp3": 30 };
const el = <K extends keyof HTMLElementTagNameMap>(tag: K) => document.createElement(tag);

let clock: FrameClock;
let mounted: ReturnType<typeof mount> | null = null;

beforeEach(() => {
  FakeAudioContext.instances = [];
  clock = new FrameClock();
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("requestAnimationFrame", clock.request);
  vi.stubGlobal("cancelAnimationFrame", clock.cancel);
  vi.spyOn(performance, "now").mockImplementation(() => clock.now);
  vi.mocked(api.blob).mockReset();
  vi.mocked(api.blob).mockImplementation(async (path: string) => blobOf(SECONDS[path] ?? 1));
});
afterEach(() => {
  mounted?.unmount();
  mounted = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The hook with the root's refs, state and nodes faked; the props thunk reads the mutable `state`, as the root's render would. */
function mount(
  over: Partial<AuditionDeps> = {},
  state0: { sentences?: PlanSentence[]; keep?: Keep; duration?: number; storedMusic?: MusicClip[]; selection?: Selection | null } = {},
  /** `inRender`: attach the transport's nodes during the render, as the JSX's refs attach in the commit, BEFORE the first effects run; `position`: the root's held playhead. */
  opts: { inRender?: boolean; position?: number } = {},
) {
  const state = {
    active: true,
    sentences: state0.sentences ?? SENTENCES,
    keep: state0.keep ?? wholeKeep(SOURCE),
    duration: state0.duration ?? SOURCE,
    storedMusic: state0.storedMusic ?? [],
    selection: state0.selection ?? null,
  };
  const durationRef = ref(state.duration);
  const sourceDurationRef = ref(SOURCE);
  const keepRef = ref(state.keep);
  const joinsRef = ref(joins(state.keep, SOURCE));
  const musicRef = ref(state.storedMusic);
  const selectionRef = ref<Selection | null>(null);
  const setSelection = vi.fn((value: Selection | null | ((held: Selection | null) => Selection | null)) => {
    state.selection = typeof value === "function" ? value(state.selection) : value;
  });
  const dragRef = ref<Drag | null>(null);
  const ppsRef = ref(10);
  const positionRef = ref(opts.position ?? 0);
  const totalRef = ref(0);
  const nodes = {
    playhead: el("div"), clock: el("span"), headClock: el("span"), shuttleLabel: el("span"),
    inHandle: el("div"), outHandle: el("div"), band: el("div"), inLabel: el("span"), outLabel: el("span"),
  };
  const inHandleRef = ref<HTMLDivElement | null>(nodes.inHandle);
  const outHandleRef = ref<HTMLDivElement | null>(nodes.outHandle);
  const bandRef = ref<HTMLDivElement | null>(nodes.band);
  const inLabelRef = ref<HTMLSpanElement | null>(nodes.inLabel);
  const outLabelRef = ref<HTMLSpanElement | null>(nodes.outLabel);
  const video = new FakeVideo();
  const useAttached = (deps: AuditionDeps) => {
    const out = useAudition(deps);
    if (opts.inRender) {
      out.videoRef.current = video as unknown as HTMLVideoElement;
      out.playheadRef.current = nodes.playhead;
      out.clockRef.current = nodes.clock;
      out.headClockRef.current = nodes.headClock;
      out.shuttleLabelRef.current = nodes.shuttleLabel;
    }
    return out;
  };
  const m = mountHook(useAttached, () => {
    // The root mirrors these every render; the selection's ref is the hook's own to mirror.
    durationRef.current = state.duration;
    keepRef.current = state.keep;
    joinsRef.current = joins(state.keep, SOURCE);
    musicRef.current = state.storedMusic;
    return {
      active: state.active,
      sentences: state.sentences,
      speakable: state.sentences.filter((s) => s.speakable),
      squeeze: { tolerance: Infinity, maxFactor: 0 },
      duration: state.duration, durationRef, sourceDurationRef,
      keep: state.keep, keepRef, keepSignature: JSON.stringify(state.keep), joinsRef,
      storedMusic: state.storedMusic, musicRef, musicSignature: JSON.stringify(state.storedMusic),
      selection: state.selection, selectionRef, setSelection, dragRef, ppsRef, positionRef, totalRef,
      inHandleRef, outHandleRef, bandRef, inLabelRef, outLabelRef,
      ...over,
    };
  });
  // The transport's nodes, as the JSX would attach them.
  m.current().videoRef.current = video as unknown as HTMLVideoElement;
  m.current().playheadRef.current = nodes.playhead;
  m.current().clockRef.current = nodes.clock;
  m.current().headClockRef.current = nodes.headClock;
  m.current().shuttleLabelRef.current = nodes.shuttleLabel;
  const ctx = () => FakeAudioContext.instances[0];
  /** One animation frame `dtMs` later: the audio clock and `performance.now()` both move by it. */
  const frame = (dtMs = 16) => act(() => {
    const c = ctx();
    if (c) c.currentTime += dtMs / 1000;
    clock.step(dtMs);
  });
  /** Frames until `until` holds, or `limit` frames. */
  const frames = (limit: number, dtMs: number, until: () => boolean = () => false) => {
    let n = 0;
    while (n < limit && !until()) { frame(dtMs); n += 1; }
    return n;
  };
  return { ...m, state, video, nodes, ctx, frame, frames, durationRef, keepRef, musicRef, selectionRef, setSelection, dragRef, ppsRef, positionRef, totalRef };
}
type M = ReturnType<typeof mount>;
/** Press a transport key or button, inside act. */
const press = (fn: () => void) => act(fn);
/** Play from a stop and let `start` finish its awaits: the context resumed, the clips scheduled, the picture playing. */
async function play(m: M) {
  press(() => m.current().togglePlay());
  await m.settle();
}
/** The shuttle's state as the transport shows it: the label the ref was written, and whether `playing` reads Pause. */
const shuttle = (m: M) => ({ label: m.current().shuttleLabelRef.current?.textContent ?? "(no node)", playing: m.current().playing });
/** An `api.blob` that answers nothing until the test lands each path - a fetch pass still in flight. */
function pendingBlobs() {
  const waiting = new Map<string, (blob: Blob) => void>();
  vi.mocked(api.blob).mockImplementation((path: string) => new Promise((resolve) => { waiting.set(path, resolve); }));
  return { waiting, land: (path: string) => { waiting.get(path)!(blobOf(SECONDS[path] ?? 1)); } };
}

describe("paint: the playhead, the clocks, the handles and the band, from the refs", () => {
  it("writes the playhead's transform, both clocks, the handles' positions and slider values, and the band, without a render", () => {
    const m = (mounted = mount());
    m.positionRef.current = 2.5;
    press(() => m.current().paint());
    expect(m.nodes.playhead.style.transform).toBe("translateX(25px)");
    expect(m.nodes.clock.textContent).toBe(timecode(2.5));
    expect(m.nodes.headClock.textContent).toBe(timecode(2.5));
    // No selection: both handles sit at the playhead, the band is hidden, the labels are empty.
    expect(m.nodes.inHandle.style.transform).toBe("translateX(25px)");
    expect(m.nodes.outHandle.style.transform).toBe("translateX(25px)");
    expect(m.nodes.inHandle.getAttribute("aria-valuenow")).toBe("2.500");
    expect(m.nodes.inHandle.getAttribute("aria-valuemax")).toBe("12.000");
    expect(m.nodes.band.style.display).toBe("none");
    expect(m.nodes.inLabel.textContent).toBe("");
    expect(m.nodes.outLabel.textContent).toBe("");
    // A committed selection: the mirror effect writes the ref and paints once.
    m.state.selection = { start: 1, end: 4 };
    m.rerender();
    expect(m.selectionRef.current).toEqual({ start: 1, end: 4 });
    expect(m.nodes.inHandle.style.transform).toBe("translateX(10px)");
    expect(m.nodes.outHandle.style.transform).toBe("translateX(40px)");
    expect(m.nodes.inHandle.getAttribute("aria-valuenow")).toBe("1.000");
    expect(m.nodes.outHandle.getAttribute("aria-valuenow")).toBe("4.000");
    expect(m.nodes.band.style.display).toBe("");
    expect(m.nodes.band.style.left).toBe("10px");
    expect(m.nodes.band.style.width).toBe("30px");
    expect(m.nodes.inLabel.textContent).toBe(timecode(1));
    expect(m.nodes.outLabel.textContent).toBe(timecode(4));
    expect(m.nodes.inLabel.style.transform).toBe("translateX(10px) translateX(-100%)");
    expect(m.nodes.outLabel.style.transform).toBe("translateX(40px)");
  });

  it("claims ⌖ on the MOVING end's label only while a drag has really caught a candidate (E5a)", () => {
    const m = (mounted = mount({}, { selection: { start: 1, end: 4 } }));
    const drag = (over: Partial<Drag>): Drag => ({ kind: "in", anchor: 4, offset: 0, seekOnClick: false, startX: 0, moved: true, pointerId: 1, delta: 0, ...over });
    m.dragRef.current = drag({ snapped: 1, snapEnd: "start" });
    press(() => m.current().paint());
    expect(m.nodes.inLabel.textContent).toBe(`${timecode(1)} ⌖`);
    expect(m.nodes.outLabel.textContent).toBe(timecode(4));
    m.dragRef.current = drag({ kind: "out", snapped: 4, snapEnd: "end" });
    press(() => m.current().paint());
    expect(m.nodes.inLabel.textContent).toBe(timecode(1));
    expect(m.nodes.outLabel.textContent).toBe(`${timecode(4)} ⌖`);
    // Nothing caught: no ⌖ on either end. A drag that has not moved yet: none either.
    m.dragRef.current = drag({ snapped: null, snapEnd: "start" });
    press(() => m.current().paint());
    expect(m.nodes.inLabel.textContent).toBe(timecode(1));
    expect(m.nodes.outLabel.textContent).toBe(timecode(4));
    m.dragRef.current = drag({ moved: false, snapped: 1, snapEnd: "start" });
    press(() => m.current().paint());
    expect(m.nodes.inLabel.textContent).toBe(timecode(1));
    m.dragRef.current = null;
    press(() => m.current().paint());
    expect(m.nodes.inLabel.textContent).toBe(timecode(1));
  });
});

describe("the schedule: the sentences and the music on one clock, the fades linear", () => {
  it("shapes a clip's GainNode with fadePoints - anchors by setValueAtTime, ramps by linearRampToValueAtTime - and starts it where clipPlayback places it", async () => {
    const bed = clip({ at: 1 });
    const m = (mounted = mount({}, { storedMusic: [bed], sentences: [] }));
    await play(m);
    const ctx = m.ctx();
    expect(ctx.resumed).toBe(1);
    expect(m.current().playing).toBe(true);
    // The music was decoded on this first Play (never on mount) and scheduled into the running audition.
    expect(api.blob).toHaveBeenCalledWith("/api/music/bed.mp3");
    expect(ctx.sources).toHaveLength(1);
    const [node] = ctx.sources;
    const when = START_LEAD + 1;   // ctxStart (the context read 0) + the clip's delay
    expect(node.starts).toEqual([{ when, offset: 0, duration: 6 }]);
    // Through its own gain node, on the bus: the envelope is exactly `fadePoints`, from the clip's top.
    const gain = ctx.gains.find((g) => g === node.connectedTo);
    expect(gain).toBeDefined();
    expect(gain!.gain.calls).toEqual(fadePoints(bed, 0).map((p) => ({
      method: p.ramp ? "linearRampToValueAtTime" : "setValueAtTime", value: p.value, at: when + p.at,
    })));
    expect(gain!.gain.calls.map((c) => c.method)).toEqual(["setValueAtTime", "linearRampToValueAtTime", "setValueAtTime", "linearRampToValueAtTime"]);
    expect(gain!.gain.calls[gain!.gain.calls.length - 1].value).toBe(0);
    // The bus is the one gain between every clip and the speakers; the eye takes it to 0 at once and back.
    const bus = ctx.gains.find((g) => g.connectedTo === ctx.destination);
    expect(bus).toBeDefined();
    expect(gain!.connectedTo).toBe(bus);
    expect(bus!.gain.value).toBe(1);
    press(() => m.current().setMusicMuted(true));
    expect(bus!.gain.value).toBe(0);
    press(() => m.current().setMusicMuted(false));
    expect(bus!.gain.value).toBe(1);
  });

  it("schedules each sentence as its clip lands, at its landed start against the clock, and offsets a clip the playhead is inside", async () => {
    const m = (mounted = mount());
    m.positionRef.current = 4;
    await play(m);
    const ctx = m.ctx();
    // The narration: fetched on Play (the wait), decoded, then scheduled from 4 - clip 0 (0-2) is over, clip 1 (3-5) starts a second in, clip 2 waits its turn.
    expect(m.current().clipSeconds).toEqual({ 0: 2, 1: 2, 2: 2 });
    expect(m.current().total).toBe(SOURCE);
    expect(m.totalRef.current).toBe(SOURCE);
    const starts = ctx.sources.map((s) => s.starts[0]);
    expect(starts).toEqual([{ when: START_LEAD, offset: 1, duration: undefined }, { when: START_LEAD + 2, offset: 0, duration: undefined }]);
    expect(m.current().startsAt).toEqual({ 0: { start: 0, end: 2, squeezedHere: false }, 1: { start: 3, end: 5, squeezedHere: false }, 2: { start: 6, end: 8, squeezedHere: false } });
  });

  it("re-places a music buffer that lands after its moment against the LIVE clock, LATE_LEAD ahead, never a start in the past", async () => {
    const bed = clip();
    const m = (mounted = mount({}, { storedMusic: [bed], sentences: [] }));
    // Slow decode: the buffer lands two seconds into the play.
    const slow = pendingBlobs();
    await play(m);
    const ctx = m.ctx();
    expect(ctx.sources).toHaveLength(0);
    ctx.currentTime = 2;
    slow.land("/api/music/bed.mp3");
    await m.settle();
    expect(ctx.sources).toHaveLength(1);
    // From 0 the clip was due at START_LEAD, which is past: placed from 2 + LATE_LEAD (minus the lead the clock already had), so it starts now-ish, further into the file.
    const behind = round3(2 - START_LEAD + LATE_LEAD);
    expect(ctx.sources[0].starts).toEqual([{ when: 2 + LATE_LEAD, offset: behind, duration: round3(6 - behind) }]);
    // And the envelope begins THAT far into the clip - the level it had reached, the fades from there - not at its top.
    const gain = ctx.gains.find((g) => g === ctx.sources[0].connectedTo);
    expect(gain).toBeDefined();
    const shape = (calls: { method: string; value: number; at: number }[]) => calls.map((c) => ({ method: c.method, value: round3(c.value), at: round3(c.at) }));
    expect(shape(gain!.gain.calls)).toEqual(shape(fadePoints(bed, behind).map((p) => ({
      method: p.ramp ? "linearRampToValueAtTime" : "setValueAtTime", value: p.value, at: 2 + LATE_LEAD + p.at,
    }))));
    expect(gain!.gain.calls[0].value).not.toBe(fadePoints(bed, 0)[0].value);
  });

  it("the clips changed under a running audition: what was scheduled stops, and the new list is placed from the playhead LATE_LEAD behind the live clock", async () => {
    const m = (mounted = mount({}, { storedMusic: [clip()], sentences: [] }));
    await play(m);
    const ctx = m.ctx();
    const [old] = ctx.sources;
    expect(old.stopped).toBe(0);
    ctx.currentTime = 1.5;
    m.state.storedMusic = [clip({ at: 3 })];
    m.rerender();
    expect(old.stopped).toBe(1);
    expect(ctx.sources).toHaveLength(2);
    // The playhead reads 1.5 - START_LEAD off the clock; the moved clip is 3 - that ahead of it, on the live clock plus the lead.
    const at = 1.5 - START_LEAD;
    expect(ctx.sources[1].starts[0].when).toBeCloseTo(1.5 + LATE_LEAD + (3 - at), 6);
    expect(ctx.sources[1].starts[0].offset).toBe(0);
  });
});

describe("Play: what it waits for (trap 25 and trap 31)", () => {
  it("waits for the first sentence when nothing is decoded and every clip on the lane is MISSING, and plays at once with one live clip", async () => {
    // The sentences are being fetched (the pass is in flight); the one clip's file has gone.
    const m = (mounted = mount({}, { storedMusic: [gone] }));
    pendingBlobs();
    await play(m);
    expect(m.current().waitingToPlay).toBe(true);
    expect(m.current().prep.running).toBe(true);
    expect(m.current().playing).toBe(false);
    // Nothing to hear yet: no context made, the picture untouched, the transport stopped.
    expect(FakeAudioContext.instances).toHaveLength(0);
    expect(m.video.writes).toEqual([]);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
    // `K` gives the wait up.
    press(() => m.current().shuttleKey("K"));
    expect(m.current().waitingToPlay).toBe(false);
    // One live clip beside the missing one: something to hear, so playback starts and the sentences join as they arrive.
    m.state.storedMusic = [gone, clip()];
    m.rerender();
    await play(m);
    expect(m.current().waitingToPlay).toBe(false);
    expect(m.current().playing).toBe(true);
    expect(m.ctx().resumed).toBe(1);
    expect(m.video.writes.slice(-3)).toEqual(["currentTime=0", "play", "playbackRate=1"]);
    expect(shuttle(m)).toEqual({ label: "", playing: true });
  });

  it("with sentences and nothing decoded, Play starts the fetch and plays as soon as the FIRST sentence is ready - a later one landing first is not enough", async () => {
    const m = (mounted = mount());
    const fetching = pendingBlobs();
    press(() => m.current().togglePlay());
    expect(m.current().waitingToPlay).toBe(true);
    expect(m.current().prep.running).toBe(true);
    expect(m.current().playing).toBe(false);
    // Three workers, three fetches in flight. The third sentence lands: the ready prefix is still empty, so it keeps waiting.
    expect([...fetching.waiting.keys()]).toEqual(["/p0", "/p1", "/p2"]);
    fetching.land("/p2");
    await m.settle();
    expect(m.current().clipSeconds).toEqual({ 2: 2 });
    expect(m.current().waitingToPlay).toBe(true);
    expect(m.current().playing).toBe(false);
    fetching.land("/p0");
    await m.settle();
    expect(m.current().waitingToPlay).toBe(false);
    expect(m.current().playing).toBe(true);
    expect(m.ctx().sources.map((s) => s.starts[0].when)).toEqual([START_LEAD]);
    // Cancel is a real stop of the pass: nothing more is started, the wait is given up.
    press(() => m.current().cancelPrepare());
    expect(m.current().prep.running).toBe(false);
    fetching.land("/p1");
    await m.settle();
    expect(m.current().clipSeconds).toEqual({ 0: 2, 2: 2 });
  });
});

describe("the frame step, the jumps and a seek: every stop resets the shuttle through halt", () => {
  it("steps one frame from the playhead, paused, the picture seeked and the shuttle STOPPED", () => {
    const m = (mounted = mount());
    m.positionRef.current = 1;
    press(() => m.current().stepBy(1));
    expect(m.positionRef.current).toBe(stepFrame(1, 1, SOURCE));
    expect(m.current().playing).toBe(false);
    expect(shuttle(m).label).toBe("");
    expect(m.video.writes).toEqual(["playbackRate=1", "pause", `currentTime=${toSource(stepFrame(1, 1, SOURCE), m.state.keep)}`]);
    press(() => m.current().stepBy(-1));
    expect(m.positionRef.current).toBe(1);
    // Never past the audition's end, never before 0.
    m.positionRef.current = SOURCE;
    press(() => m.current().stepBy(1));
    expect(m.positionRef.current).toBe(SOURCE);
    m.positionRef.current = 0;
    press(() => m.current().stepBy(-1));
    expect(m.positionRef.current).toBe(0);
  });

  it("jumps to the end stopped, and seeks - clamped - resuming AT the target when it was playing", async () => {
    const m = (mounted = mount());
    press(() => m.current().jumpToEnd());
    expect(m.positionRef.current).toBe(SOURCE);
    expect(m.current().playing).toBe(false);
    press(() => m.current().seek(4));
    expect(m.positionRef.current).toBe(4);
    expect(m.current().playing).toBe(false);
    expect(m.video.seeks.at(-1)).toBe(4);
    await play(m);
    expect(m.current().playing).toBe(true);
    press(() => m.current().seek(99));
    await m.settle();
    // Clamped to the audition's length; halted there and started again there, not at any selection's start.
    expect(m.positionRef.current).toBe(SOURCE);
    expect(m.current().position()).toBe(SOURCE);
    expect(m.current().playing).toBe(true);
    expect(shuttle(m).label).toBe("");
    expect(m.video.rates.at(-1)).toBe(1);
  });

  it("pauses through halt: the sources stopped, the clock origin dropped, the picture paused at 1x, the playhead where the clock had it", async () => {
    const m = (mounted = mount());
    await play(m);
    const ctx = m.ctx();
    expect(ctx.sources.length).toBeGreaterThan(0);
    m.frame(500);
    expect(m.positionRef.current).toBeCloseTo(0.5 - START_LEAD, 6);
    press(() => m.current().togglePlay());
    expect(m.current().playing).toBe(false);
    expect(ctx.sources.every((s) => s.stopped === 1)).toBe(true);
    expect(m.video.writes.slice(-3)).toEqual(["playbackRate=1", "pause", `currentTime=${m.positionRef.current}`]);
    expect(m.video.paused).toBe(true);
    // Stopped: the clock no longer moves the playhead.
    const at = m.positionRef.current;
    m.frame(500);
    expect(m.positionRef.current).toBe(at);
    expect(m.current().position()).toBe(at);
  });
});

describe("J / K / L: the shuttle (E5c)", () => {
  it("J from a stop runs the playhead back at 1x on real time to the selection's start when inside one, seeking the picture at once and then at most SEEK_HZ a second", () => {
    const m = (mounted = mount({}, { selection: { start: 2, end: 5 } }));
    m.positionRef.current = 4;
    press(() => m.current().shuttleKey("J"));
    expect(shuttle(m)).toEqual({ label: "◀ 1×", playing: true });
    // Backwards is seeked, never played: the picture paused at 1x.
    expect(m.video.writes).toEqual(["playbackRate=1", "pause"]);
    expect(clock.armed).toBe(1);
    // The first frame seeks at once; then the throttle holds the seeks to the rate.
    m.frame(16);
    expect(m.positionRef.current).toBeCloseTo(4 - 0.016, 9);
    expect(m.video.seeks).toEqual([toSource(4 - 0.016, m.state.keep)]);
    m.frames(9, 16);
    expect(m.video.seeks.length).toBeLessThanOrEqual(1 + Math.floor(144 / (1000 / SEEK_HZ)));
    expect(m.video.seeks.length).toBeGreaterThanOrEqual(2);
    expect(m.positionRef.current).toBeCloseTo(4 - 0.16, 9);
    // On to the floor - the selection's start - and halted there, STOPPED.
    m.frames(200, 16, () => !m.current().playing);
    expect(m.positionRef.current).toBe(2);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
    expect(clock.armed).toBe(0);
    expect(m.video.seeks.at(-1)).toBe(2);
  });

  it("K stops from anywhere through halt, and gives up a Play that was waiting", () => {
    const m = (mounted = mount());
    m.positionRef.current = 6;
    press(() => m.current().shuttleKey("J"));
    m.frames(5, 16);
    const at = m.positionRef.current;
    press(() => m.current().shuttleKey("K"));
    expect(shuttle(m)).toEqual({ label: "", playing: false });
    expect(m.positionRef.current).toBe(at);
    expect(m.video.writes.slice(-3)).toEqual(["playbackRate=1", "pause", `currentTime=${at}`]);
    expect(clock.armed).toBe(0);
  });

  it("L from a stop is Space's own path; L again doubles the rate SILENTLY - no source made or started, the audio stopped, the clock origin dropped - and L out of a backwards shuttle halts THEN starts", async () => {
    const m = (mounted = mount({}, { storedMusic: [clip()] }));
    press(() => m.current().shuttleKey("L"));
    await m.settle();
    const ctx = m.ctx();
    expect(m.current().playing).toBe(true);
    expect(shuttle(m).label).toBe("");
    const made = ctx.sources.length;
    expect(made).toBe(4);   // three sentences and the bed, scheduled by the play
    expect(ctx.starts).toHaveLength(made);
    // 2x: the picture runs at the rate, the audio is gone, and nothing new is made or started - on this press or on any frame after it.
    press(() => m.current().shuttleKey("L"));
    expect(shuttle(m)).toEqual({ label: "▶▶ 2×", playing: true });
    expect(m.video.rates.at(-1)).toBe(2);
    expect(ctx.sources.every((s) => s.stopped === 1)).toBe(true);
    expect(ctx.sources).toHaveLength(made);
    const before = m.positionRef.current;
    m.frame(100);
    expect(m.positionRef.current).toBeCloseTo(before + 0.2, 9);
    m.frames(5, 100);
    expect(ctx.sources).toHaveLength(made);
    expect(ctx.starts).toHaveLength(made);
    expect(clock.armed).toBe(1);
    press(() => m.current().shuttleKey("L"));
    expect(shuttle(m).label).toBe("▶▶ 4×");
    expect(m.video.rates.at(-1)).toBe(4);
    m.frame(100);
    expect(ctx.sources).toHaveLength(made);
    // J against a forward shuttle: backwards at 1x, paused and seeked.
    press(() => m.current().shuttleKey("J"));
    expect(shuttle(m).label).toBe("◀ 1×");
    expect(m.video.writes.slice(-2)).toEqual(["playbackRate=1", "pause"]);
    m.frames(3, 16);
    expect(ctx.sources).toHaveLength(made);
    // L out of it: a REAL play - halt first (the loop cancelled, the state reset, `playing` false before `start`'s awaits), then start from here.
    const at = m.positionRef.current;
    const writes = m.video.writes.length;
    press(() => m.current().shuttleKey("L"));
    expect(m.current().playing).toBe(false);
    expect(clock.armed).toBe(0);
    await m.settle();
    expect(m.current().playing).toBe(true);
    expect(shuttle(m).label).toBe("");
    expect(m.video.writes.slice(writes)).toEqual(["playbackRate=1", "pause", `currentTime=${at}`, `currentTime=${toSource(at, m.state.keep)}`, "play", "playbackRate=1"]);
    // A play makes sources again - the only place that ever does.
    expect(ctx.sources.length).toBeGreaterThan(made);
    expect(ctx.starts).toHaveLength(ctx.sources.length);
  });

  it("the loop advances by real time at the rate, halts through halt at the bound ahead, and takes an unknown end as NO bound - one frame armed at a time", async () => {
    const m = (mounted = mount({}, { sentences: [], storedMusic: [clip()] }));
    m.positionRef.current = 11.5;
    // A forward silent shuttle is reached only through a play (L from a stop is Space's path, and it needs something to hear - the bed), then L again.
    press(() => m.current().shuttleKey("L"));
    await m.settle();
    expect(m.current().playing).toBe(true);
    press(() => m.current().shuttleKey("L"));
    expect(shuttle(m).label).toBe("▶▶ 2×");
    expect(clock.armed).toBe(1);
    m.frame(100);
    expect(m.positionRef.current).toBeCloseTo(11.7, 9);
    // The end is not known: no ceiling, the playhead runs on past the picture's length.
    m.state.duration = 0;
    m.rerender();
    expect(m.current().total).toBe(0);
    m.frame(100);
    m.frame(100);
    expect(m.positionRef.current).toBeCloseTo(12.1, 9);
    expect(m.current().playing).toBe(true);
    // The length is back: the next frame reaches the ceiling and halts there, STOPPED, the picture paused at 1x.
    m.state.duration = SOURCE;
    m.rerender();
    m.frame(100);
    expect(m.positionRef.current).toBe(SOURCE);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
    expect(m.video.rates.at(-1)).toBe(1);
    expect(m.video.paused).toBe(true);
    expect(clock.armed).toBe(0);
  });

  it("re-seeks the picture ONCE at a join and pauses it past the picture's end, forward, as tick does", async () => {
    // A cut of 3-5: a join at 3 with two seconds removed; the picture is 10 s, the narration lands at 0, 3 and 6 as before.
    const keep: Keep = [[0, 3], [5, 12]];
    const m = (mounted = mount({}, { keep, duration: 10, sentences: [sentence(0, 0, 2), sentence(1, 9, 11)] }));
    m.positionRef.current = 2.9;
    await play(m);
    const ctx = m.ctx();
    expect(ctx.currentTime).toBe(0);
    // Through `tick`, on the audio clock: at 3.1 the join is crossed, the picture jumped to the source that resumes there (5.1), once.
    const seeks = m.video.seeks.length;
    m.frame(START_LEAD * 1000 + 200);
    expect(m.positionRef.current).toBeCloseTo(3.1, 6);
    expect(m.video.seeks.slice(seeks)).toEqual([toSource(3.1, keep)]);
    m.frame(100);
    expect(m.video.seeks.slice(seeks)).toHaveLength(1);
    // Through the shuttle's loop, 2x: the same join rule, and past the picture's end (10) the video is paused while the narration (to 11) runs on.
    press(() => m.current().seek(2.9));
    await m.settle();
    press(() => m.current().shuttleKey("L"));
    expect(shuttle(m).label).toBe("▶▶ 2×");
    const seeks2 = m.video.seeks.length;
    m.frame(100);
    expect(m.positionRef.current).toBeCloseTo(3.1, 9);
    expect(m.video.seeks.slice(seeks2)).toEqual([toSource(3.1, keep)]);
    m.frame(100);
    expect(m.video.seeks.slice(seeks2)).toHaveLength(1);
    expect(m.video.paused).toBe(false);
    m.positionRef.current = 9.95;
    m.frame(100);
    expect(m.positionRef.current).toBeCloseTo(10.15, 9);
    expect(m.video.paused).toBe(true);
    expect(m.current().playing).toBe(true);
    expect(m.current().total).toBe(11);
    m.frames(20, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(11);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
  });
});

describe("the selection mirror: both of a shuttle's bounds and a play's follow the selection", () => {
  it("a play begun inside a selection halts at its end; the bound follows a new end, and goes when the selection is cleared", async () => {
    // A bed the whole picture long, so there is something to hear from anywhere.
    const m = (mounted = mount({}, { sentences: [], storedMusic: [clip({ out: 12, fade_in: 0, fade_out: 0 })], selection: { start: 1, end: 4 } }));
    m.positionRef.current = 1;
    await play(m);
    m.frames(400, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(4);
    expect(m.current().playing).toBe(false);
    // Again, and the selection grows mid-play: the bound follows it.
    await play(m);
    m.state.selection = { start: 1, end: 6 };
    m.rerender();
    m.frames(400, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(6);
    // And cleared mid-play: no bound, so playback runs to the end of the audition.
    m.positionRef.current = 1;
    m.state.selection = { start: 1, end: 4 };
    m.rerender();
    await play(m);
    m.state.selection = null;
    m.rerender();
    m.frames(400, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(SOURCE);
    expect(m.setSelection).not.toHaveBeenCalled();
  });

  it("a silent forward SHUTTLE's ceiling follows the selection too (E5c, the twin of the floor's): grown, it halts at the new end; cleared, it runs to the audition's end", async () => {
    // A play inside {1, 4}, then L: 2x, silent - the clock origin is dropped on that press, so from here only the
    // mirror's shuttle branch (`shuttleRef.current.direction === 1`) can move the bound with the selection.
    const m = (mounted = mount({}, { sentences: [], storedMusic: [clip({ out: 12, fade_in: 0, fade_out: 0 })], selection: { start: 1, end: 4 } }));
    m.positionRef.current = 1;
    await play(m);
    press(() => m.current().shuttleKey("L"));
    expect(shuttle(m)).toEqual({ label: "▶▶ 2×", playing: true });
    m.state.selection = { start: 1, end: 6 };
    m.rerender();
    m.frames(400, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(6);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
    // Again from inside a selection, cleared mid-shuttle: no bound, so the shuttle runs on to the audition's end.
    m.positionRef.current = 1;
    m.state.selection = { start: 1, end: 4 };
    m.rerender();
    await play(m);
    press(() => m.current().shuttleKey("L"));
    expect(shuttle(m).label).toBe("▶▶ 2×");
    m.state.selection = null;
    m.rerender();
    m.frames(400, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(SOURCE);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
  });

  it("a backwards shuttle's floor follows the selection too: cleared mid-shuttle, it runs to 0", () => {
    const m = (mounted = mount({}, { selection: { start: 2, end: 5 } }));
    m.positionRef.current = 4;
    press(() => m.current().shuttleKey("J"));
    m.frames(10, 100);
    expect(m.positionRef.current).toBeCloseTo(3, 9);
    m.state.selection = null;
    m.rerender();
    m.frames(100, 100, () => !m.current().playing);
    expect(m.positionRef.current).toBe(0);
    expect(shuttle(m)).toEqual({ label: "", playing: false });
  });
});

describe("what the plan and the picture do to a running audition", () => {
  it("a changed picture remaps the playhead to the same source moment and clears the selection; a split does neither", () => {
    const m = (mounted = mount({}, { selection: { start: 1, end: 2 } }));
    m.positionRef.current = 6;
    const cut: Keep = [[0, 3], [5, 12]];
    m.state.keep = cut;
    m.state.duration = 10;
    m.rerender();
    expect(m.positionRef.current).toBe(positionAfterEdit(6, wholeKeep(SOURCE), cut));
    expect(m.positionRef.current).toBe(4);
    expect(m.setSelection).toHaveBeenCalledWith(null);
    expect(m.current().playing).toBe(false);
    // A split: the same output length, so nothing moves and the selection stands.
    m.setSelection.mockClear();
    m.state.keep = [[0, 3], [5, 8], [8, 12]];
    m.rerender();
    expect(m.positionRef.current).toBe(4);
    expect(m.setSelection).not.toHaveBeenCalled();
  });

  it("a new plan drops only the clips whose sentence changed, cancels the pass, and halts where it is - or at zero when nothing survived", async () => {
    const m = (mounted = mount());
    await play(m);
    m.frame(1000);
    expect(m.current().clipSeconds).toEqual({ 0: 2, 1: 2, 2: 2 });
    const at = m.positionRef.current;
    m.state.sentences = [SENTENCES[0], sentence(1, 3, 5, { text: "reworded" }), SENTENCES[2]];
    m.rerender();
    expect(m.current().clipSeconds).toEqual({ 0: 2, 2: 2 });
    expect(m.current().playing).toBe(false);
    expect(m.positionRef.current).toBeCloseTo(at, 6);
    expect(m.current().prep).toEqual({ total: 0, done: 0, running: false });
    // Every sentence changed (a new voice): nothing survives, and playback halts at the top.
    m.state.sentences = SENTENCES.map((s) => ({ ...s, preview_url: `${s.preview_url}?voice=b` }));
    m.rerender();
    expect(m.current().clipSeconds).toEqual({});
    expect(m.positionRef.current).toBe(0);
  });

  it("leaving the tab halts where it was, not at zero, and gives up a waiting Play", async () => {
    const m = (mounted = mount());
    await play(m);
    m.frame(1000);
    const at = m.positionRef.current;
    expect(at).toBeGreaterThan(0.5);
    m.state.active = false;
    m.rerender();
    expect(m.current().playing).toBe(false);
    expect(m.positionRef.current).toBeCloseTo(at, 6);
    // Back on the tab, a new plan whose sentences are still being fetched: Play waits, and leaving again gives the wait up.
    m.state.active = true;
    m.state.storedMusic = [gone];
    m.state.sentences = SENTENCES.map((s) => ({ ...s, preview_url: `${s.preview_url}?voice=b` }));
    m.rerender();
    pendingBlobs();
    await play(m);
    expect(m.current().waitingToPlay).toBe(true);
    m.state.active = false;
    m.rerender();
    expect(m.current().waitingToPlay).toBe(false);
    expect(m.current().playing).toBe(false);
  });

  it("adds nothing on the FIRST mount with the tab active: the one seek there is the plan's own halt, as before E6 (E6)", () => {
    // The rejoin rule fires on `active` turning true AGAIN, never on the
    // mount. Its effect's work is not visible on its own there: the plan
    // effect already halts at the held position on the mount, which paints
    // and seeks the picture (unchanged by E6). So the test counts: mounted
    // with the <video> attached in the render - as the JSX attaches it in the
    // commit, BEFORE the first effects; the harness's own mount attaches it
    // after them, where any seek on mount lands on no <video> and every count
    // is zero - and a playhead the root holds at 12.5 s, the picture is sought
    // there exactly ONCE. A rejoin rule firing on the mount too seeks it twice.
    const m = (mounted = mount({ sourceDurationRef: ref(30) }, { keep: wholeKeep(30), duration: 30 }, { inRender: true, position: 12.5 }));
    expect(m.video.seeks).toEqual([12.5]);
    expect(m.nodes.clock.textContent).toBe(timecode(12.5));
    expect(m.current().playing).toBe(false);
  });

  it("rejoining the tab paints the clock and the playhead at the held position and seeks the NEW <video> there, once (E6)", () => {
    // The root returns nothing while the tab is away, so on the way back the
    // clock, the playhead and the <video> are new nodes: the clock reading
    // 0:00.000 and the picture at 0 as the JSX makes them, the position held
    // all along (R1b's walk) - until the next frame painted them.
    const m = (mounted = mount({ sourceDurationRef: ref(30) }, { keep: wholeKeep(30), duration: 30 }));
    act(() => m.current().halt(17.86));
    expect(m.nodes.clock.textContent).toBe(timecode(17.86));
    expect(m.video.currentTime).toBe(17.86);
    m.state.active = false;
    m.rerender();
    expect(m.positionRef.current).toBe(17.86);
    // What the JSX attaches when the tab comes back: fresh nodes at zero.
    const clock = el("span");
    clock.textContent = timecode(0);
    const headClock = el("span");
    headClock.textContent = timecode(0);
    const playhead = el("div");
    const video = new FakeVideo();
    m.current().clockRef.current = clock;
    m.current().headClockRef.current = headClock;
    m.current().playheadRef.current = playhead;
    m.current().videoRef.current = video as unknown as HTMLVideoElement;
    m.state.active = true;
    m.rerender();
    expect(clock.textContent).toBe(timecode(17.86));
    expect(headClock.textContent).toBe(timecode(17.86));
    expect(playhead.style.transform).toBe(`translateX(${17.86 * 10}px)`);
    expect(video.seeks).toEqual([17.86]);
    expect(video.currentTime).toBe(17.86);
    expect(m.current().playing).toBe(false);
    // Once per rejoin: a render while the tab stays open paints and seeks nothing more.
    m.rerender();
    expect(video.seeks).toEqual([17.86]);
  });
});

describe("the hook's own text: what a count over the file says best", () => {
  /** One `const name = useCallback(…)` block of the hook, the pins' own idiom. */
  const handler = (name: string): string => {
    const from = auditionSource.indexOf(`const ${name} = useCallback(`);
    expect(from, `${name} not found`).toBeGreaterThan(-1);
    return auditionSource.slice(from, auditionSource.indexOf("\n  }, [", from));
  };

  it("writes the <video>'s playbackRate in ONE place, and hands applyShuttle only STOPPED, FORWARD or the table's next", () => {
    expect(auditionSource.match(/video\.playbackRate =/g)).toHaveLength(2);
    expect(handler("applyShuttle")).toMatch(/if \(next\.direction === 1\) \{\s*video\.playbackRate = next\.rate;/);
    expect(handler("applyShuttle")).toMatch(/\} else \{\s*video\.playbackRate = 1;\s*video\.pause\(\);\s*\}/);
    const calls = [...auditionSource.matchAll(/applyShuttle\((\w+)\)/g)].map((m) => m[1]);
    expect(calls.length).toBeGreaterThanOrEqual(3);
    for (const arg of calls) expect(["STOPPED", "FORWARD", "next"]).toContain(arg);
  });

  it("never names an exponential ramp, keeps the shuttle's state as a ref and never state, writes the backwards floor in two places, and starts a source only inside scheduleFrom", () => {
    // `exponentialRampToValueAtTime` throws on a target of 0, which every fade-out ends at (and the fake above has no such method).
    expect(auditionSource).not.toMatch(/exponentialRampToValueAtTime/);
    expect(auditionSource).toMatch(/const shuttleRef = useRef<Shuttle>\(STOPPED\);/);
    expect(auditionSource).not.toMatch(/useState<Shuttle>/);
    // The floor: on entering a backwards shuttle, and from the selection mirror while one runs - nothing else writes it.
    expect(auditionSource.match(/shuttleFloorRef\.current = /g)).toHaveLength(2);
    // Trap 40: the only two `.start(` calls in the file are the narration's and the music's, both in `scheduleFrom`.
    const starters = auditionSource.match(/node\.start\(/g) ?? [];
    expect(starters).toHaveLength(2);
    for (const starter of starters) expect(handler("scheduleFrom")).toContain(starter);
    expect(handler("shuttleLoop")).not.toMatch(/\bstart\(|scheduleFrom\(|createBufferSource|\.start\(|stopSources|startedAt/);
  });
});
