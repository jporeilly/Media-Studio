// @vitest-environment jsdom
/**
 * The Music lane as BEHAVIOUR (R1c): the hook mounted for real under jsdom
 * with the root's refs, setters and drag primitives faked and the API
 * mocked, so what a regex over the component's source used to pin - a clip
 * click selecting without a seek, ONE clip removed through `clipsAfterDelete`
 * and EVERY missing one through `withoutMissing`, `commitMusic` carrying the
 * markers unchanged - is asserted on what the fakes were told; and the rules
 * that were never pinned - the Library's add, the inspector's boxes, the
 * painters, the peaks fetch, the deselect - get their first tests. The
 * lane's and the inspector's markup are rendered for real too, so the click
 * and the boxes are driven through the DOM. What a count over the file says
 * best (one `commitEdit`, no `seek`, no request of its own) is pinned on the
 * hook's own text at the foot. Each test was watched failing with its defect
 * planted; the round's report lists the plants.
 */
import { act, createElement, type MouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../../api/client";
import {
  DEFAULT_MUSIC_GAIN,
  MAX_CLIPS,
  clipEnd,
  clipSnapTargets,
  clipsAfterDelete,
  fitFades,
  joins,
  newMusicClip,
  withoutMissing,
  type Join,
  type Keep,
  type LaneLocks,
  type Marker,
  type MusicClip,
} from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import type { WaveformPeaks } from "../../../lib/timeline";
import type { MusicFile } from "../MusicLibrary";
import { ClipInspector, type ClipInspectorProps } from "./ClipInspector";
import { MusicLane, type MusicLaneProps } from "./MusicLane";
import { mountHook } from "./testing/mountHook";
import { NO_BLOCKS, type ClipDraft, type Drag, type EditState } from "./types";
import { fileSecondsOf, useMusicLane, type MusicLaneDeps } from "./useMusicLane";
import laneSource from "./useMusicLane.ts?raw";
import laneMarkupSource from "./MusicLane.tsx?raw";
import inspectorSource from "./ClipInspector.tsx?raw";

vi.mock("../../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});

const SOURCE = 12;
const CUT: Keep = [[0, 6], [7.5, 12]];
/** A clip whose file the library still has: 1 s to 7 s of the output, 6 s of a 9 s file. */
const bed: MusicClip = { id: "m1", file: "bed.mp3", at: 1, in: 0, out: 6, gain: 0.15, fade_in: 1, fade_out: 2, file_duration: 9, missing: false };
/** One whose file has gone: the banner's subject. */
const gone: MusicClip = { id: "m2", file: "gone.mp3", at: 8, in: 0, out: 3, gain: 0.4, fade_in: 0, fade_out: 0, file_duration: null, missing: true };
const gone2: MusicClip = { ...gone, id: "m3", at: 11 };
const intro: Marker = { id: "k1", at: 1, name: "Intro" };
const PEAKS: WaveformPeaks = { buckets: 4, bucket_seconds: 2.25, duration: 9, sample_rate: 44100, peaks: [10, 120, 250, 60] };
/** What the studio answers with: `music_volume` is a new clip's level. */
const STUDIO = { settings: { music_volume: 0.3 }, options: {} };
const track = (name: string, duration: number): MusicFile => ({ name, size: 1000, duration, sample_rate: 44100, channels: 2, uploaded_at: "", uploaded_by: "" });
const ref = <T,>(current: T) => ({ current });
const el = <K extends keyof HTMLElementTagNameMap>(tag: K) => document.createElement(tag);
type Setter<T> = T | ((held: T) => T);
const apply = <T,>(held: T, value: Setter<T>): T => (typeof value === "function" ? (value as (h: T) => T)(held) : value);

let mounted: { unmount: () => void }[] = [];

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  // The studio's settings and every file's cached peaks, by path; anything else is refused.
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === "/api/settings/studio") return STUDIO;
    if (path === "/api/music/bed.mp3/peaks") return PEAKS;
    throw new Error(`no answer for ${path}`);
  });
});
afterEach(() => {
  for (const m of mounted.reverse()) m.unmount();
  mounted = [];
  vi.restoreAllMocks();
});

interface State {
  active: boolean;
  storedMusic: MusicClip[];
  locks: LaneLocks;
  editLocked: boolean;
  selectedClip: string | null;
  selectedMarker: string | null;
  selectedBlocks: ReadonlySet<number>;
  refusal: string | null;
  position: number;
  duration: number;
}

/** The hook with the root's refs, state, setters and drag primitives faked; the props thunk reads the mutable `state`, as the root's render would. */
function mount(state0: Partial<State> = {}, over: Partial<MusicLaneDeps> = {}) {
  const state: State = {
    active: true, storedMusic: [bed], locks: { video: false, narration: false, music: false }, editLocked: false,
    selectedClip: null, selectedMarker: null, selectedBlocks: NO_BLOCKS, refusal: null, position: 2, duration: SOURCE, ...state0,
  };
  const markers = [intro];
  const musicRef = ref(state.storedMusic);
  const locksRef = ref(state.locks);
  const editLockedRef = ref(state.editLocked);
  const commitEdit = vi.fn<(next: EditState) => void>();
  const committedRef = ref<EditState>({ video: CUT, narration: null, music: state.storedMusic, markers });
  const setRefusal = vi.fn((value: Setter<string | null>) => { state.refusal = apply(state.refusal, value); });
  const positionRef = ref(state.position);
  const durationRef = ref(state.duration);
  const ppsRef = ref(10);
  const joinsRef = ref<Join[]>(joins(CUT, SOURCE));
  const beginDrag = vi.fn<MusicLaneDeps["beginDrag"]>();
  /** Ten pixels a second, from the strip's left edge at 0. */
  const secondsAt = vi.fn((clientX: number) => clientX / 10);
  const suppressClick = ref(false);
  const setSelectedClip = vi.fn((value: Setter<string | null>) => { state.selectedClip = apply(state.selectedClip, value); });
  const selectedClipRef = ref(state.selectedClip);
  const setSelectedMarker = vi.fn((value: Setter<string | null>) => { state.selectedMarker = apply(state.selectedMarker, value); });
  const setSelectedBlocks = vi.fn((value: Setter<ReadonlySet<number>>) => { state.selectedBlocks = apply(state.selectedBlocks, value); });
  const label = el("div");
  const clipLabelRef = ref<HTMLDivElement | null>(label);
  // `musicFiles` is the audition's memo on the signature: one array per signature, as the root hands it.
  let files = { signature: "", list: [] as string[] };
  const m = mountHook(useMusicLane, () => {
    const musicSignature = JSON.stringify(state.storedMusic);
    if (files.signature !== musicSignature) {
      files = { signature: musicSignature, list: [...new Set(state.storedMusic.filter((clip) => !clip.missing).map((clip) => clip.file))] };
    }
    // The root mirrors these every render, and its committed effect follows the plan's clips.
    musicRef.current = state.storedMusic;
    locksRef.current = state.locks;
    editLockedRef.current = state.editLocked;
    selectedClipRef.current = state.selectedClip;
    positionRef.current = state.position;
    durationRef.current = state.duration;
    committedRef.current = { video: CUT, narration: null, music: state.storedMusic, markers };
    return {
      active: state.active, musicRef, musicSignature, musicFiles: files.list, locksRef, editLocked: state.editLocked, editLockedRef,
      commitEdit, committedRef, setRefusal, positionRef, durationRef, ppsRef, joinsRef, beginDrag, secondsAt, suppressClick,
      selectedClip: state.selectedClip, setSelectedClip, selectedClipRef, setSelectedMarker, setSelectedBlocks, clipLabelRef,
      ...over,
    };
  });
  mounted.push(m);
  /** The one `commitEdit` call, or a failure naming how many there were. */
  const sent = () => {
    expect(commitEdit).toHaveBeenCalledTimes(1);
    return commitEdit.mock.calls[0][0];
  };
  return {
    ...m, state, markers, musicRef, committedRef, commitEdit, setRefusal, positionRef, durationRef, ppsRef, joinsRef, beginDrag,
    secondsAt, suppressClick, setSelectedClip, setSelectedMarker, setSelectedBlocks, label, sent,
  };
}
type M = ReturnType<typeof mount>;
const press = (fn: () => void) => act(fn);
/** The ids a gesture selected: the deselect effect's updaters (functions) are not gestures. */
const selectedIds = (m: M) => m.setSelectedClip.mock.calls.map((c) => c[0]).filter((v) => typeof v !== "function");
/** The three selection setters, as one gesture calls them: the clip taken, the marker and the blocks given up. */
function expectSelected(m: M, id: string) {
  expect(selectedIds(m)).toEqual([id]);
  expect(m.setSelectedMarker).toHaveBeenCalledWith(null);
  expect(m.setSelectedBlocks).toHaveBeenCalledTimes(1);
  expect(m.setSelectedBlocks.mock.calls[0][0]).toBe(NO_BLOCKS);
}
/** A pointer-down as React hands it to `onClipPointerDown`, at `clientX` px from the strip's left edge. */
const pointer = (clientX: number, over: Partial<{ ctrlKey: boolean; metaKey: boolean; button: number }> = {}) => ({
  ctrlKey: false, metaKey: false, button: 0, clientX, stopPropagation: vi.fn(), ...over,
}) as unknown as ReactPointerEvent<HTMLElement> & { stopPropagation: ReturnType<typeof vi.fn> };
/** A click as React hands it to `onClipClick`. */
const click = (over: Partial<{ ctrlKey: boolean; metaKey: boolean }> = {}) => ({
  ctrlKey: false, metaKey: false, stopPropagation: vi.fn(), ...over,
}) as unknown as MouseEvent<HTMLButtonElement> & { stopPropagation: ReturnType<typeof vi.fn> };

/** The lane's markup, rendered for real under a parent that counts the clicks that reach it. */
function renderLane(m: M, over: Partial<MusicLaneProps> = {}) {
  const host = el("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const parentClicks = vi.fn();
  const render = () => act(() => {
    root.render(createElement("div", { onClick: parentClicks }, createElement(MusicLane, {
      storedMusic: m.state.storedMusic, locks: m.state.locks, pps: m.ppsRef.current, selectedClip: m.state.selectedClip,
      filePeaks: m.current().filePeaks, attachClip: m.current().attachClip, clipLabelRef: { current: m.label },
      onClipPointerDown: m.current().onClipPointerDown, onClipClick: m.current().onClipClick, ...over,
    })));
  });
  render();
  mounted.push({ unmount: () => { act(() => root.unmount()); host.remove(); } });
  const clip = (id: string) => host.querySelector<HTMLButtonElement>(`button.os-tl-clip[aria-label*="${id}"]`)!;
  return { host, render, parentClicks, clip, clips: () => [...host.querySelectorAll<HTMLButtonElement>("button.os-tl-clip")] };
}

/** The inspector's markup, rendered for real with its callbacks faked; `setClipDraft` keeps the draft as the hook's state would, and `render` shows it. */
function renderInspector(over: Partial<ClipInspectorProps> = {}) {
  const host = el("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const view = { draft: {} as ClipDraft };
  const props: ClipInspectorProps = {
    inspected: bed, editLocked: false, locks: { music: false }, clipDraft: {},
    setClipDraft: vi.fn((value: Setter<ClipDraft>) => { view.draft = apply(view.draft, value); }),
    studioGainRef: ref<number | undefined>(0.3), changeClip: vi.fn(), removeClip: vi.fn(), ...over,
  };
  view.draft = props.clipDraft;
  const render = (again: Partial<ClipInspectorProps> = {}) => act(() => { root.render(createElement(ClipInspector, { ...props, clipDraft: view.draft, ...again })); });
  render();
  mounted.push({ unmount: () => { act(() => root.unmount()); host.remove(); } });
  const setValue = (input: HTMLInputElement, value: string) => {
    // Through the prototype's setter, past React's own value tracker, so the `input` event is seen as a change.
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  };
  return {
    host, props, view, render, setValue,
    level: () => host.querySelector<HTMLInputElement>("input.os-tl-level")!,
    numbers: () => [...host.querySelectorAll<HTMLInputElement>("input[type=number]")],
    remove: () => host.querySelector<HTMLButtonElement>("button.os-tl-btn.text")!,
  };
}

describe("commitMusic - the one PUT every clip gesture makes", () => {
  it("hands commitEdit the whole edit as the server holds it, the clips replaced and the markers the SAME list (trap 39)", () => {
    const m = mount();
    const next = [bed, { ...bed, id: "m9", at: 8, out: 3 }];
    press(() => m.current().commitMusic(next));
    const op = m.sent();
    expect(op).toEqual({ video: CUT, narration: null, music: next, markers: [intro] });
    expect(op.music).toBe(next);
    expect(op.markers).toBe(m.markers);
    expect(op.video).toBe(CUT);
  });

  it("reads the file's length off the read-back (fileSecondsOf), null for a file the library has lost", () => {
    expect(fileSecondsOf(bed)).toBe(9);
    expect(fileSecondsOf(gone)).toBeNull();
    expect(fileSecondsOf({ ...bed, file_duration: undefined })).toBeNull();
  });
});

describe("removeClip and removeMissingClips - two gestures, two rules", () => {
  it("removes the selected clip alone through clipsAfterDelete, missing or not, and nothing without a selection", () => {
    const m = mount({ storedMusic: [bed, gone], selectedClip: "m1" });
    press(() => m.current().removeClip());
    expect(m.sent().music).toEqual(clipsAfterDelete([bed, gone], "m1"));
    expect(m.sent().music).toEqual([gone]);
    expect(m.sent().markers).toBe(m.markers);
    // A MISSING clip selected: it goes alone, the live one stays (E4c lets the server keep the others).
    m.commitEdit.mockClear();
    m.state.selectedClip = "m2";
    m.rerender();
    press(() => m.current().removeClip());
    expect(m.sent().music).toEqual([bed]);
    // Nothing selected: nothing sent.
    m.commitEdit.mockClear();
    m.state.selectedClip = null;
    m.rerender();
    press(() => m.current().removeClip());
    expect(m.commitEdit).not.toHaveBeenCalled();
  });

  it("removes EVERY missing clip from the banner's button through withoutMissing, and commits nothing when nothing is missing (trap 37)", () => {
    const m = mount({ storedMusic: [bed, gone, gone2] });
    press(() => m.current().removeMissingClips());
    expect(m.sent().music).toEqual(withoutMissing([bed, gone, gone2]));
    expect(m.sent().music).toEqual([bed]);
    expect(m.sent().music[0]).toBe(bed);
    expect(m.sent().markers).toBe(m.markers);
    // The file came back and the plan refetched: `withoutMissing` hands the same array back, and nothing is sent.
    m.commitEdit.mockClear();
    m.state.storedMusic = [bed, { ...gone, missing: false, file_duration: 5 }];
    m.rerender();
    press(() => m.current().removeMissingClips());
    expect(m.commitEdit).not.toHaveBeenCalled();
  });

  it("refuses both under the commit lock and under a locked Music lane, silently", () => {
    const m = mount({ storedMusic: [bed, gone], selectedClip: "m1", editLocked: true });
    press(() => m.current().removeClip());
    press(() => m.current().removeMissingClips());
    expect(m.commitEdit).not.toHaveBeenCalled();
    m.state.editLocked = false;
    m.state.locks = { video: false, narration: false, music: true };
    m.rerender();
    press(() => m.current().removeClip());
    press(() => m.current().removeMissingClips());
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).not.toHaveBeenCalled();
  });
});

describe("addMusic - the Library's Add at playhead", () => {
  it("places the file at the playhead with the studio's music_volume, selects it, closes the library and commits the list with it", async () => {
    const m = mount({ storedMusic: [bed, gone], position: 2 });
    await m.settle();  // the studio's settings land
    press(() => m.current().setLibraryOpen(true));
    expect(m.current().libraryOpen).toBe(true);
    press(() => m.current().addMusic(track("new.mp3", 30)));
    const op = m.sent();
    expect(op.music).toHaveLength(3);
    expect(op.music[0]).toBe(bed);
    expect(op.music[1]).toBe(gone);
    const added = op.music[2];
    // A minted id the lane does not hold; the rest is `newMusicClip`'s: from the playhead, as long as the room allows, the studio's gain, the default fades.
    expect(added.id).toMatch(/^m[a-z0-9]+$/);
    expect(["m1", "m2"]).not.toContain(added.id);
    expect(added).toEqual(newMusicClip({ id: added.id, file: "new.mp3", fileDuration: 30, at: 2, outputDuration: SOURCE, gain: 0.3 }));
    expect(added).toMatchObject({ file: "new.mp3", at: 2, in: 0, out: 10, gain: 0.3, fade_in: 1, fade_out: 2 });
    expect(op.markers).toBe(m.markers);
    expect(m.setSelectedClip).toHaveBeenCalledWith(added.id);
    expect(m.current().libraryOpen).toBe(false);
    expect(m.setRefusal).not.toHaveBeenCalled();
  });

  it("falls back to DEFAULT_MUSIC_GAIN when the studio's settings cannot be read", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/api/settings/studio") throw new Error("no studio");
      if (path === "/api/music/bed.mp3/peaks") return PEAKS;
      throw new Error(`no answer for ${path}`);
    });
    const m = mount({ position: 0 });
    await m.settle();
    expect(m.current().studioGainRef.current).toBeUndefined();
    press(() => m.current().addMusic(track("new.mp3", 4)));
    expect(m.sent().music[1]).toMatchObject({ file: "new.mp3", at: 0, out: 4, gain: DEFAULT_MUSIC_GAIN });
  });

  it("refuses at MAX_CLIPS naming the cap, and with no room at the playhead, and commits nothing", async () => {
    const full = Array.from({ length: MAX_CLIPS }, (_, i) => ({ ...bed, id: `c${i}` }));
    const m = mount({ storedMusic: full });
    await m.settle();
    press(() => m.current().addMusic(track("new.mp3", 30)));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.state.refusal).toBe(`The music is limited to ${MAX_CLIPS} clips — remove one first.`);
    // The playhead at the picture's very end: `newMusicClip` answers null, and the strip says why.
    m.state.storedMusic = [bed];
    m.state.position = SOURCE;
    m.rerender();
    press(() => m.current().addMusic(track("new.mp3", 30)));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.state.refusal).toBe("There is no room for a clip at the playhead — move it earlier, or use a longer track.");
    expect(selectedIds(m)).toEqual([]);
  });

  it("does nothing at all under the commit lock or a locked lane - no refusal, the library left open", async () => {
    const m = mount({ editLocked: true });
    await m.settle();
    press(() => m.current().setLibraryOpen(true));
    press(() => m.current().addMusic(track("new.mp3", 30)));
    m.state.editLocked = false;
    m.state.locks = { video: false, narration: false, music: true };
    m.rerender();
    press(() => m.current().addMusic(track("new.mp3", 30)));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).not.toHaveBeenCalled();
    expect(m.current().libraryOpen).toBe(true);
  });
});

describe("changeClip - the inspector's boxes", () => {
  it("patches one field of one clip, keeps the fades legal through fitFades, and commits the whole list with the others untouched", () => {
    const m = mount({ storedMusic: [bed, gone] });
    press(() => m.current().changeClip("m1", { fade_in: 5 }));
    // The clip is 6 s long: a 5 s fade in leaves the 2 s fade out only 1 s (`fitFades`, the fade in kept whole first).
    expect(fitFades(6, 5, 2)).toEqual([5, 1]);
    expect(m.sent().music).toEqual([{ ...bed, fade_in: 5, fade_out: 1 }, gone]);
    expect(m.sent().music[1]).toBe(gone);
    expect(m.sent().markers).toBe(m.markers);
    m.commitEdit.mockClear();
    press(() => m.current().changeClip("m1", { gain: 0.7 }));
    expect(m.sent().music[0]).toEqual({ ...bed, gain: 0.7 });
  });

  it("ignores an id the lane does not hold, the commit lock and a locked lane", () => {
    const m = mount();
    press(() => m.current().changeClip("nope", { gain: 0.7 }));
    m.state.editLocked = true;
    m.rerender();
    press(() => m.current().changeClip("m1", { gain: 0.7 }));
    m.state.editLocked = false;
    m.state.locks = { video: false, narration: false, music: true };
    m.rerender();
    press(() => m.current().changeClip("m1", { gain: 0.7 }));
    expect(m.commitEdit).not.toHaveBeenCalled();
  });
});

describe("the clip's two pointer handlers", () => {
  it("a click on a clip's button SELECTS it and gives up the marker and the blocks - and nothing else, no seek (the owner's ruling)", () => {
    const m = mount({ storedMusic: [bed, gone], selectedMarker: "k1", selectedBlocks: new Set([1]) });
    const lane = renderLane(m);
    expect(lane.clips()).toHaveLength(2);
    const playhead = m.positionRef.current;
    expect(playhead).toBe(2);
    press(() => lane.clip("bed.mp3").click());
    expectSelected(m, "m1");
    // The press stops at the clip: the strip's own click (a seek) never sees it.
    expect(lane.parentClicks).not.toHaveBeenCalled();
    // And nothing else: the playhead where it was (the clip starts at 1), no drag begun, nothing sent.
    expect(m.positionRef.current).toBe(playhead);
    expect(m.beginDrag).not.toHaveBeenCalled();
    expect(m.commitEdit).not.toHaveBeenCalled();
    // A click the browser fires after a drag's release is not a click; Ctrl: stopped at the clip and ignored.
    m.setSelectedClip.mockClear(); m.setSelectedMarker.mockClear(); m.setSelectedBlocks.mockClear();
    m.suppressClick.current = true;
    press(() => lane.clip("gone.mp3").click());
    m.suppressClick.current = false;
    press(() => lane.clip("gone.mp3").dispatchEvent(new MouseEvent("click", { bubbles: true, ctrlKey: true })));
    expect(m.setSelectedClip).not.toHaveBeenCalled();
    expect(m.setSelectedMarker).not.toHaveBeenCalled();
    expect(m.setSelectedBlocks).not.toHaveBeenCalled();
    expect(lane.parentClicks).not.toHaveBeenCalled();
    // The handler itself, with the event React hands it: the same three, after stopping the event.
    const event = click();
    press(() => m.current().onClipClick(event, gone));
    expect(event.stopPropagation).toHaveBeenCalledTimes(1);
    expectSelected(m, "m2");
    expect(m.positionRef.current).toBe(playhead);
  });

  it("a press on a clip's body begins a MOVE and on either 8 px edge a TRIM, grabbing the start or the end, with the candidate set built without its own edges", () => {
    const m = mount({ storedMusic: [bed, gone], position: 2 });
    const snapTo = clipSnapTargets({ playhead: 2, duration: SOURCE, joins: m.joinsRef.current.map((join) => join.at), clips: [bed, gone], exclude: "m1" });
    expect(snapTo).toContain(8);      // the other clip's start
    expect(snapTo).toContain(11);     // ... and end
    expect(snapTo).not.toContain(7);  // never its own end
    // The body, at 4 s (40 px at 10 px/s): a move from the clip's own `at`.
    const body = pointer(40);
    press(() => m.current().onClipPointerDown(body, bed));
    expect(body.stopPropagation).toHaveBeenCalledTimes(1);
    expectSelected(m, "m1");
    expect(m.beginDrag).toHaveBeenCalledTimes(1);
    expect(m.beginDrag).toHaveBeenLastCalledWith(body, "clip", 1, 1, { clip: bed, zone: "body", snapTo });
    // The left edge, 0.5 s in (CLIP_EDGE_PX / pps = 0.8 s): a trim grabbing the start.
    press(() => m.current().onClipPointerDown(pointer(15), bed));
    expect(m.beginDrag).toHaveBeenLastCalledWith(expect.anything(), "trim", 1, 1, { clip: bed, zone: "in", snapTo });
    // The right edge: a trim grabbing the END, so the audio stays under the pointer.
    press(() => m.current().onClipPointerDown(pointer(68), bed));
    expect(m.beginDrag).toHaveBeenLastCalledWith(expect.anything(), "trim", 1, clipEnd(bed), { clip: bed, zone: "out", snapTo });
    // A MISSING clip has both edges too (E6; E4c gave it none): a press on its end is a trim, which
    // `trimClip` holds inside the slice it has, and a press on its body a move.
    const missingSnap = clipSnapTargets({ playhead: 2, duration: SOURCE, joins: m.joinsRef.current.map((join) => join.at), clips: [bed, gone], exclude: "m2" });
    press(() => m.current().onClipPointerDown(pointer(81), gone));
    expect(m.beginDrag).toHaveBeenLastCalledWith(expect.anything(), "trim", 8, 8, { clip: gone, zone: "in", snapTo: missingSnap });
    press(() => m.current().onClipPointerDown(pointer(108), gone));
    expect(m.beginDrag).toHaveBeenLastCalledWith(expect.anything(), "trim", 8, clipEnd(gone), { clip: gone, zone: "out", snapTo: missingSnap });
    press(() => m.current().onClipPointerDown(pointer(95), gone));
    expect(m.beginDrag).toHaveBeenLastCalledWith(expect.anything(), "clip", 8, 8, { clip: gone, zone: "body", snapTo: missingSnap });
    expect(m.beginDrag).toHaveBeenCalledTimes(6);
  });

  it("a press under the commit lock or on a locked lane is swallowed and begins nothing - the click after it still selects; Ctrl or another button leaves the press alone", () => {
    const m = mount({ editLocked: true });
    const locked = pointer(40);
    press(() => m.current().onClipPointerDown(locked, bed));
    // Stopped at the clip (the ruler under it must not scrub), then nothing: no drag, and no selection either -
    // the click the browser fires after the release is what selects, and `onClipClick` takes no lock.
    expect(locked.stopPropagation).toHaveBeenCalledTimes(1);
    expect(m.beginDrag).not.toHaveBeenCalled();
    expect(selectedIds(m)).toEqual([]);
    expect(m.setSelectedMarker).not.toHaveBeenCalled();
    press(() => m.current().onClipClick(click(), bed));
    expectSelected(m, "m1");
    m.setSelectedClip.mockClear(); m.setSelectedMarker.mockClear(); m.setSelectedBlocks.mockClear();
    m.state.editLocked = false;
    m.state.locks = { video: false, narration: false, music: true };
    m.rerender();
    const onLocked = pointer(40);
    press(() => m.current().onClipPointerDown(onLocked, bed));
    expect(onLocked.stopPropagation).toHaveBeenCalledTimes(1);
    expect(m.beginDrag).not.toHaveBeenCalled();
    expect(selectedIds(m)).toEqual([]);
    // Ctrl+drag is the range everywhere, and a right button is nobody's: the press bubbles untouched.
    m.setSelectedClip.mockClear(); m.setSelectedMarker.mockClear(); m.setSelectedBlocks.mockClear();
    m.state.locks = { video: false, narration: false, music: false };
    m.rerender();
    const ctrl = pointer(40, { ctrlKey: true });
    const right = pointer(40, { button: 2 });
    press(() => m.current().onClipPointerDown(ctrl, bed));
    press(() => m.current().onClipPointerDown(right, bed));
    expect(ctrl.stopPropagation).not.toHaveBeenCalled();
    expect(right.stopPropagation).not.toHaveBeenCalled();
    expect(selectedIds(m)).toEqual([]);
    expect(m.beginDrag).not.toHaveBeenCalled();
  });
});

describe("the painters a clip drag goes through", () => {
  const drag = (over: Partial<Drag>): Drag => ({ kind: "clip", anchor: 1, offset: 0, seekOnClick: false, startX: 0, moved: true, pointerId: 1, delta: 0, clip: bed, ...over });

  it("paintClip moves a clip by a transform and rewrites a trim's left and width, the label following with ⌖ only when a candidate is caught", () => {
    const m = mount();
    const node = el("button");
    m.current().attachClip("m1")(node);
    press(() => m.current().paintClip(drag({ kind: "clip" }), { ...bed, at: 3 }, null));
    expect(node.style.transform).toBe("translateX(20px)");
    expect(node.style.left).toBe("");
    expect(m.label.style.display).toBe("");
    expect(m.label.style.transform).toBe("translateX(30px)");
    expect(m.label.textContent).toBe(timecode(3));
    press(() => m.current().paintClip(drag({ kind: "clip" }), { ...bed, at: 3 }, 3));
    expect(m.label.textContent).toBe(`${timecode(3)} ⌖`);
    // A trim: the left and the width are rewritten (never a transform), and the label says the length.
    press(() => m.current().paintClip(drag({ kind: "trim", zone: "in" }), { ...bed, at: 2, in: 1 }, null));
    expect(node.style.left).toBe("20px");
    expect(node.style.width).toBe("50px");
    expect(m.label.textContent).toBe(`${timecode(2)} · 5.00 s`);
    // No clip on the record: nothing painted.
    m.label.textContent = "untouched";
    press(() => m.current().paintClip(drag({ clip: undefined }), { ...bed, at: 3 }, null));
    expect(m.label.textContent).toBe("untouched");
  });

  it("clearClipDrag puts every clip back where the plan draws it and hides the label", () => {
    const m = mount({ storedMusic: [bed, gone] });
    const one = el("button");
    const two = el("button");
    m.current().attachClip("m1")(one);
    m.current().attachClip("m2")(two);
    press(() => m.current().paintClip(drag({ kind: "clip" }), { ...bed, at: 3 }, 3));
    press(() => m.current().paintClip(drag({ kind: "trim", zone: "out", clip: gone }), { ...gone, out: 2 }, null));
    press(() => m.current().clearClipDrag());
    expect(one.style.transform).toBe("");
    expect(one.style.left).toBe("10px");
    expect(one.style.width).toBe("60px");
    expect(two.style.transform).toBe("");
    expect(two.style.left).toBe("80px");
    expect(two.style.width).toBe("30px");
    expect(m.label.style.display).toBe("none");
    // A node detached from the lane is forgotten.
    m.current().attachClip("m2")(null);
    two.style.left = "1px";
    press(() => m.current().clearClipDrag());
    expect(two.style.left).toBe("1px");
  });
});

describe("the waveform inside each clip", () => {
  const peaksCalls = () => vi.mocked(api.get).mock.calls.map((c) => c[0]).filter((p) => p.includes("/peaks"));

  it("fetches each file's cached peaks once, on the first draw, keeps them by name, and skips a file with no waveform silently", async () => {
    const m = mount({ storedMusic: [bed, gone] });
    await m.settle();
    expect(m.current().filePeaks).toEqual({ "bed.mp3": PEAKS });
    // Once per file, missing files never asked for; a re-render asks again for nothing.
    expect(peaksCalls()).toEqual(["/api/music/bed.mp3/peaks"]);
    m.rerender();
    await m.settle();
    expect(peaksCalls()).toEqual(["/api/music/bed.mp3/peaks"]);
    // A new file whose peaks the server refuses: asked once, by its encoded name, and the lane draws on without it.
    m.state.storedMusic = [bed, { ...bed, id: "m4", file: "new file.mp3" }];
    m.rerender();
    await m.settle();
    expect(peaksCalls()).toEqual(["/api/music/bed.mp3/peaks", "/api/music/new%20file.mp3/peaks"]);
    expect(m.current().filePeaks).toEqual({ "bed.mp3": PEAKS });
    m.rerender();
    await m.settle();
    expect(peaksCalls()).toHaveLength(2);
  });

  it("asks for nothing while the tab is not the open one", async () => {
    const m = mount({ active: false });
    await m.settle();
    expect(peaksCalls()).toEqual([]);
    expect(m.current().filePeaks).toEqual({});
  });
});

describe("the selection, the draft and the library's state", () => {
  it("gives up a selected clip the edit removed - a cut, an undo, a delete - and keeps one the plan still holds", () => {
    const m = mount({ storedMusic: [bed, gone], selectedClip: "m2" });
    // Through the setter's UPDATER, never a value: the effect reads the clips off the ref at the moment it runs.
    expect(m.setSelectedClip).toHaveBeenCalledTimes(1);
    expect(selectedIds(m)).toEqual([]);
    expect(m.state.selectedClip).toBe("m2");
    // The live clip goes: the selected missing one stays selected.
    m.state.storedMusic = [gone];
    m.rerender();
    expect(m.setSelectedClip).toHaveBeenCalledTimes(2);
    expect(m.state.selectedClip).toBe("m2");
    // A re-render with the same clips runs nothing.
    m.rerender();
    expect(m.setSelectedClip).toHaveBeenCalledTimes(2);
    // The selected one goes: deselected.
    m.state.storedMusic = [];
    m.rerender();
    expect(m.setSelectedClip).toHaveBeenCalledTimes(3);
    expect(m.state.selectedClip).toBeNull();
    // The SELECTED clip goes while another remains - a Delete on a two-clip lane: deselected too, not only when the lane empties.
    m.state.storedMusic = [bed, gone];
    m.state.selectedClip = "m1";
    m.rerender();
    expect(m.state.selectedClip).toBe("m1");
    m.state.storedMusic = [gone];
    m.rerender();
    expect(m.state.selectedClip).toBeNull();
  });

  it("clears the inspector's half-typed values when the selection or the plan moves on, and mirrors the library's open state in its ref", () => {
    const m = mount({ selectedClip: "m1" });
    const draft: ClipDraft = { gain: 0.5, fadeIn: "3" };
    press(() => m.current().setClipDraft(draft));
    expect(m.current().clipDraft).toEqual(draft);
    m.state.selectedClip = null;
    m.rerender();
    expect(m.current().clipDraft).toEqual({});
    press(() => m.current().setClipDraft(draft));
    m.state.storedMusic = [{ ...bed, gain: 0.2 }];
    m.rerender();
    expect(m.current().clipDraft).toEqual({});
    expect(m.current().libraryOpen).toBe(false);
    expect(m.current().libraryOpenRef.current).toBe(false);
    press(() => m.current().setLibraryOpen(true));
    expect(m.current().libraryOpen).toBe(true);
    expect(m.current().libraryOpenRef.current).toBe(true);
  });
});

describe("the inspector's markup and its boxes (ClipInspector)", () => {
  it("commits the level on the slider's release and a fade on its box's blur, through changeClip, and removes the clip from its button", () => {
    const view = renderInspector({ clipDraft: { gain: 0.5 } });
    expect(view.level().value).toBe("50");
    expect(view.level().title).toContain("30%");
    press(() => view.level().dispatchEvent(new Event("pointerup", { bubbles: true })));
    expect(view.props.changeClip).toHaveBeenCalledWith("m1", { gain: 0.5 });
    // The slider's own moves reach the draft, not the server; a keyboard step commits the draft once its key is up.
    press(() => view.setValue(view.level(), "70"));
    expect(view.props.setClipDraft).toHaveBeenCalledTimes(1);
    expect(view.view.draft).toEqual({ gain: 0.7 });
    expect(view.props.changeClip).toHaveBeenCalledTimes(1);
    view.render();
    expect(view.level().value).toBe("70");
    expect(view.host.querySelector(".os-tl-inspector-value")!.textContent).toBe("70%");
    press(() => view.level().dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "ArrowRight" })));
    expect(view.props.changeClip).toHaveBeenLastCalledWith("m1", { gain: 0.7 });
    press(() => view.level().dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "Shift" })));
    expect(view.props.changeClip).toHaveBeenCalledTimes(2);
    // The fade in, typed and left: the typing reaches the draft (and the box shows it once the state lands), the blur commits it as a number, never below 0.
    expect(view.numbers().map((n) => n.value)).toEqual(["1", "2"]);
    press(() => view.setValue(view.numbers()[0], "3"));
    expect(view.view.draft).toEqual({ gain: 0.7, fadeIn: "3" });
    view.render();
    expect(view.numbers()[0].value).toBe("3");
    press(() => view.numbers()[0].dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
    expect(view.props.changeClip).toHaveBeenLastCalledWith("m1", { fade_in: 3 });
    press(() => view.setValue(view.numbers()[1], "-2"));
    view.render();
    press(() => view.numbers()[1].dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
    expect(view.props.changeClip).toHaveBeenLastCalledWith("m1", { fade_out: 0 });
    // Enter in a box blurs it, which is the commit.
    press(() => view.numbers()[0].focus());
    press(() => view.numbers()[0].dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" })));
    expect(document.activeElement).not.toBe(view.numbers()[0]);
    press(() => view.remove().click());
    expect(view.props.removeClip).toHaveBeenCalledTimes(1);
  });

  it("disables every box and the button under the commit lock or a locked lane, and says a missing clip's slice can only be shortened", () => {
    const view = renderInspector({ editLocked: true });
    expect(view.level().disabled).toBe(true);
    expect(view.numbers().map((n) => n.disabled)).toEqual([true, true]);
    expect(view.remove().disabled).toBe(true);
    view.render({ editLocked: false, locks: { music: true } });
    expect(view.level().disabled).toBe(true);
    expect(view.remove().disabled).toBe(true);
    view.render({ editLocked: false, locks: { music: false }, inspected: gone });
    expect(view.level().disabled).toBe(false);
    // E6: a missing clip may be trimmed shorter or split, never lengthened - said on the row and in its title.
    expect(view.host.querySelector(".os-tl-status")!.textContent).toContain("the file is missing, so its slice can only be shortened");
    expect(view.host.querySelector(".os-tl-status")!.getAttribute("title")).toContain("can be trimmed shorter or split but never lengthened");
    expect(view.host.querySelector(".os-tl-status")!.textContent).not.toContain("cannot be trimmed");
    expect(view.host.querySelector(".os-tl-clip-file")!.textContent).toBe("gone.mp3");
  });
});

describe("the hook's own text: what a count over the file says best", () => {
  it("makes one call to commitEdit - every clip gesture goes through commitMusic - never seeks, and sends no request of its own", () => {
    // The owner's ruling (a clip click does not seek) holds by construction: nothing in the lane can seek.
    expect(laneSource).not.toMatch(/\bseek\(/);
    // One writer: the stack is reached through `commitMusic` alone (what it sends is the behaviour test's, "hands commitEdit the whole edit…").
    expect(laneSource.match(/commitEdit\(/g) ?? []).toHaveLength(1);
    expect(laneSource).not.toMatch(/api\.(put|patch|delete)\(/);
    // The one fetch the lane makes is the waveform's.
    expect(laneSource.match(/api\.get/g) ?? []).toHaveLength(1);
  });

  it("carries no frozen-lane paragraph in any of its three files (E4c: a missing file costs the render, not the lane)", () => {
    // The negative that stood over the root (lib/music.test.ts) reaches the
    // markup that moved here too: the inspector's copy and the clip's title.
    for (const source of [laneSource, laneMarkupSource, inspectorSource]) {
      expect(source).not.toMatch(/frozen|unfreeze|cannot be changed until/);
    }
    // The trim zones a MISSING clip gets since E6 are the render harness's rule ("draws trim zones on a live clip
    // and on a missing one"): a render of the real markup, which sees either edge go - a regex over this
    // text would see only the one it names.
  });
});
