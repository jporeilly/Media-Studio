// @vitest-environment jsdom
/**
 * The markers as BEHAVIOUR (R1d): the hook mounted for real under jsdom with
 * the root's refs, setters and drag primitives faked, so what a regex over
 * the component's source used to pin - `commitMarkers` carrying the tracks
 * and the clips unchanged under the lock, `M` drawing a pending marker at the
 * playhead with its name box open, the ONE commit when the box closes with
 * the sanitised name or the default, a rename only when the name changed,
 * Delete through `markersAfterDelete`, the jump to the neighbouring DRAWN
 * marker, a flag's press beginning a marker drag with the candidate set less
 * its own moment - is asserted on what the fakes were told; and the rules
 * that were never pinned - the painters, the reconcile effect, the flags,
 * `dropPending` - get their first tests. The ruler's markup is rendered for
 * real too, so the name box's Enter, Escape and blur and a flag's click are
 * driven through the DOM. What a count over the file says best (one
 * `commitEdit`, one `seek`, no request of its own) is pinned on the hook's
 * own text at the foot. Each test was watched failing with its defect
 * planted; the round's report lists the plants.
 */
import { act, createElement, type MouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_MARKERS,
  MAX_MARKER_NAME,
  drawnMarkers,
  markersAfterDelete,
  newMarker,
  renameMarker,
  sortMarkers,
  type DrawnMarker,
  type Keep,
  type Marker,
  type MusicClip,
} from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import { MarkerRuler, type MarkerRulerProps } from "./MarkerRuler";
import { mountHook } from "./testing/mountHook";
import { NO_BLOCKS, type Drag, type EditState } from "./types";
import { useMarkers, type MarkersDeps } from "./useMarkers";
import markersSource from "./useMarkers.ts?raw";

const SOURCE = 12;
/** The picture keeps 0-6 and 7.5-12 of a 12 s source: output 0-6 is source 0-6, output 6-10.5 is source 7.5-12. */
const CUT: Keep = [[0, 6], [7.5, 12]];
const INTRO: DrawnMarker = { id: "k1", at: 1, name: "Intro", timeline_at: 1 };
/** In removed picture: the plan gives it no `timeline_at`, and the ruler does not draw it (trap 39). */
const HIDDEN: Marker = { id: "k2", at: 7, name: "Hidden", timeline_at: null };
const LATER: DrawnMarker = { id: "k3", at: 9, name: "Later", timeline_at: 7.5 };
const bed: MusicClip = { id: "m1", file: "bed.mp3", at: 1, in: 0, out: 6, gain: 0.15, fade_in: 1, fade_out: 2, file_duration: 9, missing: false };
/** What the root's `snapTargetsNow` answers for a flag's drag. */
const SNAPS = [0, 1, 3, 10.5];
const ref = <T,>(current: T) => ({ current });
type Setter<T> = T | ((held: T) => T);
const apply = <T,>(held: T, value: Setter<T>): T => (typeof value === "function" ? (value as (h: T) => T)(held) : value);

let mounted: { unmount: () => void }[] = [];
/** The flags of EVERY render, not only the latest: the render a plan lands in runs before the effect that answers it. */
let renders: DrawnMarker[][] = [];
function useRecordedMarkers(deps: MarkersDeps) {
  const out = useMarkers(deps);
  renders.push(out.flags);
  return out;
}
afterEach(() => {
  for (const m of mounted.reverse()) m.unmount();
  mounted = [];
  vi.restoreAllMocks();
});

interface State {
  storedMarkers: Marker[];
  editLocked: boolean;
  selectedMarker: string | null;
  selectedClip: string | null;
  selectedBlocks: ReadonlySet<number>;
  refusal: string | null;
  position: number;
}

/** The hook with the root's refs, state, setters and drag primitives faked; the props thunk reads the mutable `state`, as the root's render would. */
function mount(state0: Partial<State> = {}, over: Partial<MarkersDeps> = {}) {
  const state: State = {
    storedMarkers: [INTRO, HIDDEN, LATER], editLocked: false, selectedMarker: null, selectedClip: "m1",
    selectedBlocks: new Set([2]), refusal: null, position: 5, ...state0,
  };
  const music = [bed];
  const markersRef = ref(state.storedMarkers);
  const editLockedRef = ref(state.editLocked);
  const commitEdit = vi.fn<(next: EditState) => void>();
  const committedRef = ref<EditState>({ video: CUT, narration: null, music, markers: state.storedMarkers });
  const setRefusal = vi.fn((value: Setter<string | null>) => { state.refusal = apply(state.refusal, value); });
  const positionRef = ref(state.position);
  const keepRef = ref<Keep>(CUT);
  const sourceDurationRef = ref(SOURCE);
  const ppsRef = ref(10);
  const seek = vi.fn<(to: number) => void>();
  const beginDrag = vi.fn<MarkersDeps["beginDrag"]>();
  const snapTargetsNow = vi.fn((exclude?: string) => (exclude === undefined ? [...SNAPS, 7.5] : SNAPS));
  const suppressClick = ref(false);
  const setSelectedMarker = vi.fn((value: Setter<string | null>) => { state.selectedMarker = apply(state.selectedMarker, value); });
  const selectedMarkerRef = ref(state.selectedMarker);
  const setSelectedClip = vi.fn((value: Setter<string | null>) => { state.selectedClip = apply(state.selectedClip, value); });
  const setSelectedBlocks = vi.fn((value: Setter<ReadonlySet<number>>) => { state.selectedBlocks = apply(state.selectedBlocks, value); });
  const label = document.createElement("div");
  const markerLabelRef = ref<HTMLDivElement | null>(label);
  renders = [];
  const m = mountHook(useRecordedMarkers, () => {
    // The root mirrors these every render, and its committed effect follows the plan's markers.
    markersRef.current = state.storedMarkers;
    editLockedRef.current = state.editLocked;
    selectedMarkerRef.current = state.selectedMarker;
    positionRef.current = state.position;
    committedRef.current = { video: CUT, narration: null, music, markers: state.storedMarkers };
    return {
      storedMarkers: state.storedMarkers, markersRef, markersSignature: JSON.stringify(state.storedMarkers),
      editLocked: state.editLocked, editLockedRef, commitEdit, committedRef, setRefusal, positionRef, keepRef, sourceDurationRef,
      ppsRef, seek, beginDrag, snapTargetsNow, suppressClick, setSelectedMarker, selectedMarkerRef, setSelectedClip,
      setSelectedBlocks, markerLabelRef, ...over,
    };
  });
  mounted.push(m);
  /** The one `commitEdit` call, or a failure naming how many there were. */
  const sent = () => {
    expect(commitEdit).toHaveBeenCalledTimes(1);
    return commitEdit.mock.calls[0][0];
  };
  return {
    ...m, state, music, markersRef, committedRef, commitEdit, setRefusal, positionRef, seek, beginDrag, snapTargetsNow,
    suppressClick, setSelectedMarker, setSelectedClip, setSelectedBlocks, label, sent,
  };
}
type M = ReturnType<typeof mount>;
const press = (fn: () => void) => act(fn);
/** The marker ids a gesture selected: the reconcile effect's updaters (functions) are not gestures. */
const selectedIds = (m: M) => m.setSelectedMarker.mock.calls.map((c) => c[0]).filter((v) => typeof v !== "function");
/** The three selection setters, as one gesture calls them: the marker taken, the clip and the blocks given up. */
function expectSelected(m: M, id: string) {
  expect(selectedIds(m)).toEqual([id]);
  expect(m.setSelectedClip).toHaveBeenCalledTimes(1);
  expect(m.setSelectedClip).toHaveBeenCalledWith(null);
  expect(m.setSelectedBlocks).toHaveBeenCalledTimes(1);
  expect(m.setSelectedBlocks.mock.calls[0][0]).toBe(NO_BLOCKS);
}
function clearSelections(m: M) {
  m.setSelectedMarker.mockClear(); m.setSelectedClip.mockClear(); m.setSelectedBlocks.mockClear();
}
/** A pointer-down as React hands it to `onMarkerPointerDown`. */
const pointer = (over: Partial<{ ctrlKey: boolean; metaKey: boolean; button: number }> = {}) => ({
  ctrlKey: false, metaKey: false, button: 0, clientX: 40, stopPropagation: vi.fn(), ...over,
}) as unknown as ReactPointerEvent<HTMLButtonElement> & { stopPropagation: ReturnType<typeof vi.fn> };
/** A click or a double-click as React hands it to the flag's handlers. */
const click = (over: Partial<{ ctrlKey: boolean; metaKey: boolean }> = {}) => ({
  ctrlKey: false, metaKey: false, stopPropagation: vi.fn(), ...over,
}) as unknown as MouseEvent<HTMLButtonElement> & { stopPropagation: ReturnType<typeof vi.fn> };
/** `M` at the playhead, and the marker it drew. */
function drop(m: M): DrawnMarker {
  press(() => m.current().dropMarker());
  const held = m.current().pending;
  expect(held).not.toBeNull();
  return held as DrawnMarker;
}
/** Type into the open box, as the input's `onChange` does. */
const type = (m: M, draft: string) => press(() => {
  const box = m.current().nameBox;
  expect(box).not.toBeNull();
  m.current().setNameBox({ id: box!.id, draft });
});

/** The ruler's markup, rendered for real under a parent that counts the clicks and the keydowns that reach it. */
function renderRuler(props: Partial<MarkerRulerProps> = {}) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const parentClicks = vi.fn();
  const parentKeys = vi.fn();
  const all: MarkerRulerProps = {
    flags: drawnMarkers([INTRO, HIDDEN, LATER]), pps: 10, selectedMarker: null, pending: null, nameBox: null,
    nameBoxAt: undefined, setNameBox: vi.fn(), finishNameBox: vi.fn(), attachMarker: () => () => {},
    markerLabelRef: ref<HTMLDivElement | null>(null), onMarkerPointerDown: vi.fn(), onMarkerClick: vi.fn(),
    onMarkerDoubleClick: vi.fn(), ...props,
  };
  const render = (again: Partial<MarkerRulerProps> = {}) => act(() => {
    root.render(createElement("div", { onClick: parentClicks, onKeyDown: parentKeys }, createElement(MarkerRuler, { ...all, ...again })));
  });
  render();
  mounted.push({ unmount: () => { act(() => root.unmount()); host.remove(); } });
  return {
    host, props: all, render, parentClicks, parentKeys,
    flags: () => [...host.querySelectorAll<HTMLButtonElement>("button.os-tl-marker")],
    box: () => host.querySelector<HTMLInputElement>("input.os-tl-marker-name-box"),
  };
}

describe("commitMarkers - the one PUT every marker gesture makes", () => {
  it("hands commitEdit the whole edit as the server holds it, the markers replaced and the tracks and the clips the SAME objects (trap 39); nothing under the commit lock", () => {
    const m = mount();
    const list = [INTRO];
    press(() => m.current().commitMarkers(list));
    const op = m.sent();
    expect(op).toEqual({ video: CUT, narration: null, music: [bed], markers: [INTRO] });
    expect(op.markers).toBe(list);
    expect(op.video).toBe(CUT);
    expect(op.music).toBe(m.music);
    // Under the commit lock (a job holds the project, a commit is in flight): nothing, and nothing said.
    m.commitEdit.mockClear();
    m.state.editLocked = true;
    m.rerender();
    press(() => m.current().commitMarkers(list));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).not.toHaveBeenCalled();
  });
});

describe("dropMarker - M", () => {
  it("draws a pending marker at the playhead through newMarker, its name box open on the default name, selects it and gives up the clip and the blocks - and sends nothing", () => {
    const m = mount({ position: 8 });
    const held = drop(m);
    // Output 8 is SOURCE 9.5 through the picture's list; named for the count; drawn where it was dropped.
    expect(held).toEqual(newMarker([INTRO, HIDDEN, LATER], 8, CUT, SOURCE, () => held.id));
    expect(held).toMatchObject({ at: 9.5, name: "Marker 4", timeline_at: 8 });
    expect(held.id).toMatch(/^k/);
    expect(["k1", "k2", "k3"]).not.toContain(held.id);
    expect(m.current().nameBox).toEqual({ id: held.id, draft: "Marker 4" });
    expectSelected(m, held.id);
    // The PUT waits for the box.
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).not.toHaveBeenCalled();
  });

  it("refuses at MAX_MARKERS naming the cap, and does nothing under the commit lock or while a marker is already pending", () => {
    const full: Marker[] = Array.from({ length: MAX_MARKERS }, (_, i) => ({ id: `c${i}`, at: i * 0.05, name: `M${i}`, timeline_at: i * 0.05 }));
    const m = mount({ storedMarkers: full });
    press(() => m.current().dropMarker());
    expect(m.state.refusal).toBe(`The markers are limited to ${MAX_MARKERS} — remove one first.`);
    expect(m.current().pending).toBeNull();
    expect(m.current().nameBox).toBeNull();
    expect(selectedIds(m)).toEqual([]);
    // Under the lock: nothing at all, nothing said.
    const locked = mount({ editLocked: true });
    press(() => locked.current().dropMarker());
    expect(locked.current().pending).toBeNull();
    expect(locked.current().nameBox).toBeNull();
    expect(locked.setRefusal).not.toHaveBeenCalled();
    expect(selectedIds(locked)).toEqual([]);
    // A second M while the first is pending: the first stays, alone.
    const twice = mount();
    const first = drop(twice);
    twice.state.position = 9;
    twice.rerender();
    press(() => twice.current().dropMarker());
    expect(twice.current().pending).toBe(first);
    expect(selectedIds(twice)).toEqual([first.id]);
  });
});

describe("finishNameBox - the name box closes", () => {
  it("lands the pending marker in ONE commit with the typed name, sanitised, in the list sorted by its source moment - drawn until the plan has it", () => {
    const m = mount({ position: 0.5 });
    const held = drop(m);
    // A paste's tab and newline are dropped and the name trimmed (`sanitizeMarkerName`).
    type(m, "  Open\ting\n ");
    press(() => m.current().finishNameBox(true));
    const op = m.sent();
    expect(op.markers).toEqual(sortMarkers([INTRO, HIDDEN, LATER, { ...held, name: "Opening" }]));
    expect(op.markers[0]).toEqual({ ...held, name: "Opening" });
    expect(op.markers.slice(1)).toEqual([INTRO, HIDDEN, LATER]);
    expect(op.video).toBe(CUT);
    expect(m.current().nameBox).toBeNull();
    expect(m.current().pending).toBe(held);
  });

  it("lands it with the default name on Escape, and when the box was emptied", () => {
    const m = mount({ position: 8 });
    const held = drop(m);
    type(m, "Typed");
    press(() => m.current().finishNameBox(false));
    expect(m.sent().markers).toEqual(sortMarkers([INTRO, HIDDEN, LATER, { ...held, name: "Marker 4" }]));
    const emptied = mount({ position: 8 });
    const second = drop(emptied);
    type(emptied, "  \t ");
    press(() => emptied.current().finishNameBox(true));
    expect(emptied.sent().markers).toContainEqual({ ...second, name: "Marker 4" });
  });

  it("drops the pending marker under the commit lock, sends nothing, and says it was not added", () => {
    const m = mount({ position: 8 });
    drop(m);
    type(m, "Chapter");
    // A job took the project while the box was open.
    m.state.editLocked = true;
    m.rerender();
    press(() => m.current().finishNameBox(true));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.current().pending).toBeNull();
    expect(m.current().nameBox).toBeNull();
    expect(m.state.refusal).toBe("The marker was not added — wait for the last edit to be saved, then press M again.");
  });

  it("renames an existing marker through renameMarker only when the name changed; Escape reverts; under the lock the name is not saved, and says so", () => {
    const m = mount();
    const open = () => press(() => m.current().onMarkerDoubleClick(click(), INTRO));
    open();
    expect(m.current().nameBox).toEqual({ id: "k1", draft: "Intro" });
    // Unchanged, and unchanged once sanitised: nothing sent.
    press(() => m.current().finishNameBox(true));
    open();
    type(m, " Intro\n");
    press(() => m.current().finishNameBox(true));
    // Escape: the old name stays, whatever was typed.
    open();
    type(m, "Welcome");
    press(() => m.current().finishNameBox(false));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.current().nameBox).toBeNull();
    // A real change: ONE commit of `renameMarker`'s list, the other markers the same objects.
    open();
    type(m, "Welcome\t");
    press(() => m.current().finishNameBox(true));
    const op = m.sent();
    expect(op.markers).toEqual(renameMarker([INTRO, HIDDEN, LATER], "k1", "Welcome"));
    expect(op.markers[0]).toEqual({ ...INTRO, name: "Welcome" });
    expect(op.markers[1]).toBe(HIDDEN);
    expect(op.markers[2]).toBe(LATER);
    // Under the lock: nothing sent, and the strip says the name was not saved.
    m.commitEdit.mockClear();
    open();
    type(m, "Other");
    m.state.editLocked = true;
    m.rerender();
    press(() => m.current().finishNameBox(true));
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.state.refusal).toBe("The name was not saved — wait for the last edit to be saved, then rename it again.");
  });

  it("closes exactly once, whichever event closes it first - Enter and the blur that follows it land ONE commit", () => {
    const m = mount({ position: 8 });
    drop(m);
    press(() => {
      m.current().finishNameBox(true);
      m.current().finishNameBox(true);
    });
    expect(m.commitEdit).toHaveBeenCalledTimes(1);
  });
});

describe("removeMarker and jumpToMarker", () => {
  it("removes the selected marker through markersAfterDelete, the others the same objects; nothing without a selection or under the lock", () => {
    const m = mount({ selectedMarker: "k2" });
    press(() => m.current().removeMarker());
    const op = m.sent();
    expect(op.markers).toEqual(markersAfterDelete([INTRO, HIDDEN, LATER], "k2"));
    expect(op.markers).toEqual([INTRO, LATER]);
    expect(op.markers[0]).toBe(INTRO);
    expect(op.markers[1]).toBe(LATER);
    expect(op.music).toBe(m.music);
    m.commitEdit.mockClear();
    m.state.selectedMarker = null;
    m.rerender();
    press(() => m.current().removeMarker());
    m.state.selectedMarker = "k1";
    m.state.editLocked = true;
    m.rerender();
    press(() => m.current().removeMarker());
    expect(m.commitEdit).not.toHaveBeenCalled();
  });

  it("jumps to the previous and the next DRAWN marker from the playhead, never to one in removed picture, and nowhere when there is none", () => {
    const m = mount({ position: 5 });
    press(() => m.current().jumpToMarker(-1));
    expect(m.seek).toHaveBeenLastCalledWith(1);
    press(() => m.current().jumpToMarker(1));
    expect(m.seek).toHaveBeenLastCalledWith(7.5);
    // On a marker: the jump goes past it, not to it; nothing before the first.
    m.state.position = 1;
    m.rerender();
    m.seek.mockClear();
    press(() => m.current().jumpToMarker(-1));
    expect(m.seek).not.toHaveBeenCalled();
    press(() => m.current().jumpToMarker(1));
    expect(m.seek).toHaveBeenCalledWith(7.5);
    // Past the last: nothing after it.
    m.state.position = 10;
    m.rerender();
    m.seek.mockClear();
    press(() => m.current().jumpToMarker(1));
    expect(m.seek).not.toHaveBeenCalled();
    press(() => m.current().jumpToMarker(-1));
    expect(m.seek).toHaveBeenCalledWith(7.5);
    expect(m.seek.mock.calls.flat()).not.toContain(7);
  });
});

describe("the flag's three handlers", () => {
  it("a press on a flag selects its marker, gives up the clip and the blocks, stops the event and begins a marker drag from the flag with the candidate set less its own moment", () => {
    const m = mount();
    const event = pointer();
    press(() => m.current().onMarkerPointerDown(event, LATER));
    expect(event.stopPropagation).toHaveBeenCalledTimes(1);
    expectSelected(m, "k3");
    expect(m.snapTargetsNow).toHaveBeenCalledTimes(1);
    expect(m.snapTargetsNow).toHaveBeenCalledWith("k3");
    expect(m.beginDrag).toHaveBeenCalledTimes(1);
    expect(m.beginDrag).toHaveBeenCalledWith(event, "marker", 7.5, 7.5, { marker: LATER, snapTo: SNAPS });
  });

  it("a press on the pending flag or under the commit lock selects and begins nothing else; Ctrl or another button leaves the press alone", () => {
    const locked = mount({ editLocked: true });
    const event = pointer();
    press(() => locked.current().onMarkerPointerDown(event, INTRO));
    expect(event.stopPropagation).toHaveBeenCalledTimes(1);
    expectSelected(locked, "k1");
    expect(locked.beginDrag).not.toHaveBeenCalled();
    // The pending flag: it is not sent yet, so it cannot be dragged - but a press still selects it.
    const m = mount({ position: 8 });
    const held = drop(m);
    clearSelections(m);
    press(() => m.current().onMarkerPointerDown(pointer(), held));
    expectSelected(m, held.id);
    expect(m.beginDrag).not.toHaveBeenCalled();
    // Ctrl+drag is the range everywhere, and a right button is nobody's.
    clearSelections(m);
    const ctrl = pointer({ ctrlKey: true });
    const right = pointer({ button: 2 });
    press(() => m.current().onMarkerPointerDown(ctrl, INTRO));
    press(() => m.current().onMarkerPointerDown(right, INTRO));
    expect(ctrl.stopPropagation).not.toHaveBeenCalled();
    expect(right.stopPropagation).not.toHaveBeenCalled();
    expect(selectedIds(m)).toEqual([]);
    expect(m.beginDrag).not.toHaveBeenCalled();
  });

  it("a click on a flag SELECTS the marker and gives up the clip and the blocks - and nothing else, no seek; after a drag's release or with Ctrl it selects nothing", () => {
    const m = mount();
    const ruler = renderRuler({ onMarkerClick: m.current().onMarkerClick });
    expect(ruler.flags()).toHaveLength(2);
    press(() => ruler.flags()[1].click());
    expectSelected(m, "k3");
    // Stopped at the flag: the ruler's own click never sees it.
    expect(ruler.parentClicks).not.toHaveBeenCalled();
    expect(m.seek).not.toHaveBeenCalled();
    expect(m.positionRef.current).toBe(5);
    expect(m.beginDrag).not.toHaveBeenCalled();
    expect(m.commitEdit).not.toHaveBeenCalled();
    clearSelections(m);
    m.suppressClick.current = true;
    press(() => ruler.flags()[0].click());
    m.suppressClick.current = false;
    press(() => ruler.flags()[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true, ctrlKey: true })));
    expect(selectedIds(m)).toEqual([]);
    expect(m.setSelectedClip).not.toHaveBeenCalled();
    expect(m.setSelectedBlocks).not.toHaveBeenCalled();
    expect(ruler.parentClicks).not.toHaveBeenCalled();
  });

  it("a double-click opens the flag's name box on its name, stopping the event - but not on the pending flag or under the commit lock", () => {
    const m = mount({ position: 8 });
    const event = click();
    press(() => m.current().onMarkerDoubleClick(event, LATER));
    expect(event.stopPropagation).toHaveBeenCalledTimes(1);
    expect(m.current().nameBox).toEqual({ id: "k3", draft: "Later" });
    press(() => m.current().finishNameBox(false));
    const held = drop(m);
    press(() => m.current().finishNameBox(false));
    m.commitEdit.mockClear();
    press(() => m.current().onMarkerDoubleClick(click(), held));
    expect(m.current().nameBox).toBeNull();
    const locked = mount({ editLocked: true });
    press(() => locked.current().onMarkerDoubleClick(click(), INTRO));
    expect(locked.current().nameBox).toBeNull();
  });
});

describe("the painters a flag drag goes through", () => {
  const drag = (over: Partial<Drag>): Drag => ({ kind: "marker", anchor: 7.5, offset: 0, seekOnClick: false, startX: 0, moved: true, pointerId: 1, delta: 0, marker: LATER, ...over });

  it("paintMarker moves the flag by a transform from where the plan draws it, the label following with the moment, ⌖ only when a candidate is caught", () => {
    const m = mount();
    const node = document.createElement("button");
    m.current().attachMarker("k3")(node);
    press(() => m.current().paintMarker(drag({}), 9, null));
    expect(node.style.transform).toBe("translateX(15px)");
    expect(m.label.style.display).toBe("");
    expect(m.label.style.transform).toBe("translateX(90px)");
    expect(m.label.textContent).toBe(timecode(9));
    press(() => m.current().paintMarker(drag({}), 10.5, 10.5));
    expect(node.style.transform).toBe("translateX(30px)");
    expect(m.label.textContent).toBe(`${timecode(10.5)} ⌖`);
    // No marker on the record: nothing painted.
    m.label.textContent = "untouched";
    press(() => m.current().paintMarker(drag({ marker: undefined }), 3, null));
    expect(m.label.textContent).toBe("untouched");
  });

  it("clearMarkerDrag puts every flag back where the plan draws it and hides the label; a detached flag is forgotten", () => {
    const m = mount();
    const one = document.createElement("button");
    const two = document.createElement("button");
    m.current().attachMarker("k1")(one);
    m.current().attachMarker("k3")(two);
    press(() => m.current().paintMarker(drag({ marker: INTRO }), 2, null));
    press(() => m.current().paintMarker(drag({}), 9, null));
    press(() => m.current().clearMarkerDrag());
    expect(one.style.transform).toBe("");
    expect(two.style.transform).toBe("");
    expect(m.label.style.display).toBe("none");
    m.current().attachMarker("k3")(null);
    two.style.transform = "translateX(1px)";
    press(() => m.current().clearMarkerDrag());
    expect(two.style.transform).toBe("translateX(1px)");
  });
});

describe("the reconcile effect, the flags and dropPending", () => {
  it("gives up the pending marker once the plan draws it, and a selected marker the edit removed - but never the pending one, which the plan does not have yet", () => {
    const m = mount({ position: 8, selectedMarker: "k3" });
    const held = drop(m);
    expect(m.state.selectedMarker).toBe(held.id);
    // The plan changes without the pending marker (another marker went): it stays pending, and stays selected.
    m.state.storedMarkers = [INTRO, LATER];
    m.rerender();
    expect(m.current().pending).toBe(held);
    expect(m.state.selectedMarker).toBe(held.id);
    // The plan that has it lands: pending no longer, and still selected (the plan holds it now).
    m.state.storedMarkers = [INTRO, LATER, { ...held, name: "Marker 4" }];
    m.rerender();
    expect(m.current().pending).toBeNull();
    expect(m.state.selectedMarker).toBe(held.id);
    // A selected marker the edit removed (a delete, an undo): no longer selected.
    m.state.storedMarkers = [INTRO, LATER];
    m.rerender();
    expect(m.state.selectedMarker).toBeNull();
    // Through the setter's UPDATER, never a value, once per signature (the mount and the three plans): the
    // effect reads the markers off the ref when it runs, and a `M` or a name box re-render runs nothing.
    expect(m.setSelectedMarker.mock.calls.filter((c) => typeof c[0] === "function")).toHaveLength(4);
  });

  it("draws the DRAWN markers, plus the pending one while the plan lacks it, and puts the name box on the flag being named", () => {
    const m = mount({ position: 8 });
    expect(m.current().flags).toEqual([INTRO, LATER]);
    expect(m.current().nameBoxAt).toBeUndefined();
    const held = drop(m);
    expect(m.current().flags).toEqual([INTRO, LATER, held]);
    expect(m.current().nameBoxAt).toBe(8);
    // Named in its box, so the marker the plan lands with differs from the pending one, which keeps the default.
    type(m, "Opening");
    press(() => m.current().finishNameBox(true));
    expect(m.current().pending).toBe(held);
    expect(held.name).toBe("Marker 4");
    // The plan has it now: drawn once, from the plan - in the render the plan lands in too, where the
    // marker is still pending (the effect that gives it up runs after that render), and in the one after.
    m.state.storedMarkers = [INTRO, HIDDEN, LATER, { ...held, name: "Opening" }];
    renders = [];
    m.rerender();
    expect(renders.length).toBeGreaterThanOrEqual(2);
    for (const drawn of renders) expect(drawn).toEqual([INTRO, LATER, { ...held, name: "Opening" }]);
    expect(m.current().pending).toBeNull();
    press(() => m.current().onMarkerDoubleClick(click(), INTRO));
    expect(m.current().nameBoxAt).toBe(1);
    // A box on a marker the ruler does not draw sits nowhere.
    press(() => m.current().setNameBox({ id: "k2", draft: "Hidden" }));
    expect(m.current().nameBoxAt).toBeUndefined();
  });

  it("dropPending drops the pending marker - the commit stack's refusal path through afterCommitRef", () => {
    const m = mount({ position: 8 });
    drop(m);
    press(() => m.current().dropPending());
    expect(m.current().pending).toBeNull();
    expect(m.current().flags).toEqual([INTRO, LATER]);
  });
});

describe("the ruler's markup (MarkerRuler), rendered", () => {
  it("draws a flag per marker it is handed, the pending one marked and the selected one pressed, and hands a flag's press, click and double-click its own marker", () => {
    const pendingFlag: DrawnMarker = { id: "kp", at: 9.5, name: "Marker 4", timeline_at: 8 };
    const ruler = renderRuler({ flags: [INTRO, LATER, pendingFlag], pending: pendingFlag, selectedMarker: "k3" });
    const [intro, later, held] = ruler.flags();
    expect(ruler.flags()).toHaveLength(3);
    expect(held.className).toBe("os-tl-marker pending");
    expect(later.className).toBe("os-tl-marker selected");
    expect(later.getAttribute("aria-pressed")).toBe("true");
    expect(intro.getAttribute("aria-pressed")).toBe("false");
    // The name is each button's ONLY content whatever its state - pending, selected or neither - so the drag's
    // transform carries it with the glyph (the deleted 1526 line's rule; the harness draws plain flags only).
    expect(held.innerHTML).toBe('<span class="os-tl-marker-name">Marker 4</span>');
    expect(later.innerHTML).toBe('<span class="os-tl-marker-name">Later</span>');
    expect(intro.innerHTML).toBe('<span class="os-tl-marker-name">Intro</span>');
    expect(later.style.left).toBe("75px");
    press(() => later.dispatchEvent(new window.MouseEvent("pointerdown", { bubbles: true, button: 0 })));
    expect(ruler.props.onMarkerPointerDown).toHaveBeenCalledWith(expect.anything(), LATER);
    press(() => intro.click());
    expect(ruler.props.onMarkerClick).toHaveBeenCalledWith(expect.anything(), INTRO);
    press(() => held.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true })));
    expect(ruler.props.onMarkerDoubleClick).toHaveBeenCalledWith(expect.anything(), pendingFlag);
    // No box until a marker is being named; the label is there, hidden.
    expect(ruler.box()).toBeNull();
    expect(ruler.host.querySelector<HTMLDivElement>(".os-tl-marker-label")!.style.display).toBe("none");
  });

  it("the name box: Enter keeps the name, Escape reverts, a click elsewhere keeps; its keys never reach the strip's bindings; at most MAX_MARKER_NAME characters", () => {
    const strip = vi.fn();
    document.addEventListener("keydown", strip);
    try {
      const ruler = renderRuler({ nameBox: { id: "k1", draft: "Intro" }, nameBoxAt: 1 });
      const box = ruler.box()!;
      expect(box).not.toBeNull();
      expect(box.value).toBe("Intro");
      expect(box.maxLength).toBe(MAX_MARKER_NAME);
      expect(box.style.left).toBe("10px");
      const key = (name: string) => {
        const event = new window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true });
        press(() => { box.dispatchEvent(event); });
        return event;
      };
      const enter = key("Enter");
      expect(ruler.props.finishNameBox).toHaveBeenCalledTimes(1);
      expect(ruler.props.finishNameBox).toHaveBeenLastCalledWith(true);
      expect(enter.defaultPrevented).toBe(true);
      const escape = key("Escape");
      expect(ruler.props.finishNameBox).toHaveBeenCalledTimes(2);
      expect(ruler.props.finishNameBox).toHaveBeenLastCalledWith(false);
      expect(escape.defaultPrevented).toBe(true);
      // A plain letter is the box's: not finished, not prevented - and like Enter and Escape it reaches no binding.
      const letter = key("m");
      expect(ruler.props.finishNameBox).toHaveBeenCalledTimes(2);
      expect(letter.defaultPrevented).toBe(false);
      expect(ruler.parentKeys).not.toHaveBeenCalled();
      expect(strip).not.toHaveBeenCalled();
      // A click elsewhere is the blur: it keeps the typed name.
      press(() => { box.dispatchEvent(new window.FocusEvent("focusout", { bubbles: true })); });
      expect(ruler.props.finishNameBox).toHaveBeenCalledTimes(3);
      expect(ruler.props.finishNameBox).toHaveBeenLastCalledWith(true);
      // Typing reaches the box's own state, on its id.
      press(() => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(box, "Intro!");
        box.dispatchEvent(new Event("input", { bubbles: true }));
      });
      expect(ruler.props.setNameBox).toHaveBeenCalledWith({ id: "k1", draft: "Intro!" });
      // A click in the box is not a click on the ruler.
      press(() => box.click());
      expect(ruler.parentClicks).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener("keydown", strip);
    }
  });
});

describe("the hook's own text: what a count over the file says best", () => {
  it("makes one call to commitEdit - every marker gesture goes through commitMarkers - sends no request of its own, and seeks only in the jump", () => {
    // One writer: the stack is reached through `commitMarkers` alone (what it sends is the behaviour test's).
    expect(markersSource.match(/commitEdit\(/g) ?? []).toHaveLength(1);
    expect(markersSource).not.toMatch(/api\.\w+\(/);
    // A click on a flag selects and does not seek (the test above): the one seek is Ctrl+[ / ]'s.
    expect(markersSource.match(/\bseek\(/g) ?? []).toHaveLength(1);
  });
});
