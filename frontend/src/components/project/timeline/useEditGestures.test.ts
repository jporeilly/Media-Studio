// @vitest-environment jsdom
/**
 * The cut, the split, the nudge and the reset, as BEHAVIOUR (R1a): the
 * gesture hook mounted for real with the stack faked, so the rules the
 * component's source used to pin by regex - the ripple exactly when the
 * PICTURE's list changes, a picture cut across a missing clip refused
 * before anything is sent and a split of one allowed (E6), the cap, the
 * markers riding along unchanged - are asserted on what `commitEdit` and
 * `setRefusal` receive. Each test was watched failing with its defect
 * planted; the round's report lists the plants.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_CLIPS, pictureCutRefusal, type LaneLocks, type MusicClip } from "../../../lib/edit";
import type { PlanSentence } from "../../../lib/timeline";
import { mountHook } from "./testing/mountHook";
import { drawn, useEditGestures, type EditGesturesDeps } from "./useEditGestures";
import type { EditState, Selection } from "./types";

const SOURCE = 12;
const ref = <T,>(current: T) => ({ current });
const locks = (video: boolean, narration: boolean, music: boolean): LaneLocks => ({ video, narration, music });
const clip = (over: Partial<MusicClip>): MusicClip => ({ id: "m", file: "bed.mp3", at: 0, in: 0, out: 2, gain: 0.15, fade_in: 0, fade_out: 0, ...over });
const m1 = clip({ id: "m1", at: 0, out: 2 });   // 0 – 2, before the cut
const m2 = clip({ id: "m2", at: 8, out: 3 });   // 8 – 11, after it
const gone = clip({ id: "g", file: "gone.mp3", at: 4, out: 4, missing: true, file_duration: null });   // 4 – 8
const intro = { id: "k1", at: 1, name: "Intro" };
const sentence = (index: number, start: number, end: number, pinned: number): PlanSentence => ({
  index, start, end, pinned_start: pinned, text: `s${index}`, voice: "en-GB", speed: 1, muted: false, past_end: false,
  speakable: true, window: 5, squeezable: true, preview_url: `/p${index}`,
});
const SENTENCES = [sentence(0, 0, 2, 0), sentence(1, 5, 7, 5.5), sentence(2, 9, 10, 9)];

function mount(over: Partial<EditGesturesDeps> = {}) {
  const commitEdit = vi.fn();
  const commitOffsets = vi.fn(() => true);
  const setRefusal = vi.fn();
  const selectionRef = ref<Selection | null>({ start: 3, end: 5 });
  const locksRef = ref<LaneLocks>(locks(false, false, false));
  const committedRef = ref<EditState>({ video: null, narration: null, music: [m1, m2], markers: [intro] });
  const positionRef = ref(4);
  const sourceDurationRef = ref(SOURCE);
  const selectedBlocksRef = ref<ReadonlySet<number>>(new Set());
  const state = { editLocked: false, selected: null as number | null };
  const m = mountHook(useEditGestures, () => ({
    commitEdit, commitOffsets, editLocked: state.editLocked, setRefusal, selectionRef, locksRef, committedRef, positionRef,
    sourceDurationRef, selectedBlocksRef, sentences: SENTENCES, selected: state.selected, ...over,
  }));
  return { ...m, state, commitEdit, commitOffsets, setRefusal, selectionRef, locksRef, committedRef, positionRef, selectedBlocksRef };
}
/** What `commitEdit` was handed, once. */
const sent = (m: ReturnType<typeof mount>): EditState => {
  expect(m.commitEdit).toHaveBeenCalledTimes(1);
  return m.commitEdit.mock.calls[0][0] as EditState;
};

let mounted: ReturnType<typeof mount> | null = null;
afterEach(() => { mounted?.unmount(); mounted = null; });

describe("the cut", () => {
  const COMBOS: [boolean, boolean, boolean][] = [];
  for (const video of [false, true]) for (const narration of [false, true]) for (const music of [false, true]) COMBOS.push([video, narration, music]);

  it.each(COMBOS)("video=%s narration=%s music=%s: the unlocked lists cut, the clips rippled only when the PICTURE's list changed, the markers untouched", (video, narration, music) => {
    const m = (mounted = mount());
    m.locksRef.current = locks(video, narration, music);
    const before = m.committedRef.current;
    m.current().cutSelection();
    if (video && narration && music) {
      // Nothing to cut into: neither a commit nor a refusal.
      expect(m.commitEdit).not.toHaveBeenCalled();
      expect(m.setRefusal).not.toHaveBeenCalled();
      return;
    }
    if (video && narration) {
      // Music the only unlocked lane: the ripple could move nothing (the owner's ruling).
      expect(m.commitEdit).not.toHaveBeenCalled();
      expect(m.setRefusal).toHaveBeenCalledWith(expect.stringMatching(/^The music rides the picture, so a cut with the picture locked would leave every clip exactly where it is\./));
      return;
    }
    const next = sent(m);
    expect(next.video).toEqual(video ? null : [[0, 3], [5, 12]]);
    expect(next.narration).toEqual(narration ? null : [[0, 3], [5, 12]]);
    if (!video && !music) expect(next.music).toEqual([m1, { ...m2, at: 6 }]);
    else expect(next.music).toBe(before.music);
    expect(next.markers).toBe(before.markers);
    expect(m.setRefusal).not.toHaveBeenCalled();
  });

  it("refuses a cut of the PICTURE across a MISSING clip before anything is sent, with the way out; with the Music lane locked, or the picture locked, it goes through (E6)", () => {
    // The owner's decision of 2026-09-30: the server would store the cut (a
    // missing clip may shrink or split) but not its undo, so the picture's
    // cut could not be taken back. Refused first, naming the gesture, the
    // file and both ways out (`missingCutByPicture`, `pictureCutRefusal`).
    const m = (mounted = mount());
    m.committedRef.current = { ...m.committedRef.current, music: [m1, gone] };
    m.selectionRef.current = { start: 5, end: 7 };
    m.current().cutSelection();
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).toHaveBeenCalledTimes(1);
    expect(m.setRefusal).toHaveBeenCalledWith(pictureCutRefusal("cut", [gone], 5, 7));
    expect(m.setRefusal.mock.calls[0][0]).toContain("Lock the Music lane to cut the picture alone, or remove the missing clip first.");
    // Narration locked too: still the picture, still refused.
    m.setRefusal.mockClear();
    m.locksRef.current = locks(false, true, false);
    m.current().cutSelection();
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).toHaveBeenCalledWith(pictureCutRefusal("cut", [gone], 5, 7));
    // The Music lane locked: the picture is cut alone, the clips as they were.
    m.setRefusal.mockClear();
    m.locksRef.current = locks(false, false, true);
    m.current().cutSelection();
    expect(m.setRefusal).not.toHaveBeenCalled();
    expect(sent(m).music).toBe(m.committedRef.current.music);
    // The picture locked: the clips do not move, so nothing is refused either.
    m.commitEdit.mockClear();
    m.locksRef.current = locks(true, false, false);
    m.current().cutSelection();
    expect(m.setRefusal).not.toHaveBeenCalled();
    expect(sent(m).music).toBe(m.committedRef.current.music);
    // A cut that only RIPPLES the missing clip - wholly before it - goes through, the clip moved.
    m.commitEdit.mockClear();
    m.locksRef.current = locks(false, false, false);
    m.selectionRef.current = { start: 2, end: 3 };
    m.current().cutSelection();
    expect(m.setRefusal).not.toHaveBeenCalled();
    expect(sent(m).music.find((held) => held.id === "g")).toMatchObject({ at: 3, in: 0, out: 4, missing: true });
  });

  it("refuses a cut that would split the music past the cap, naming the count", () => {
    const m = (mounted = mount());
    const many = Array.from({ length: MAX_CLIPS }, (_, i) => clip({ id: `c${i}`, at: i * 5, out: 4 }));
    m.committedRef.current = { ...m.committedRef.current, music: many };
    m.selectionRef.current = { start: 1, end: 1.5 };
    m.current().cutSelection();
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).toHaveBeenCalledWith(expect.stringContaining(`into ${MAX_CLIPS + 1} clips, past the limit of ${MAX_CLIPS}`));
  });

  it("refuses to empty a track, and does nothing at all under the lock or without a selection", () => {
    const m = (mounted = mount());
    m.selectionRef.current = { start: 0, end: SOURCE };
    m.current().cutSelection();
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).toHaveBeenCalledWith("Keep at least one range — that selection would remove the whole picture.");
    m.setRefusal.mockClear();
    m.selectionRef.current = { start: 3, end: 5 };
    m.state.editLocked = true;
    m.rerender();
    m.current().cutSelection();
    m.state.editLocked = false;
    m.selectionRef.current = null;
    m.rerender();
    m.current().cutSelection();
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).not.toHaveBeenCalled();
  });
});

describe("the split", () => {
  it("puts a boundary in every unlocked list and through the clips at the playhead, the markers riding along", () => {
    const m = (mounted = mount());
    m.positionRef.current = 1;
    m.current().splitAtPlayhead(false);
    const next = sent(m);
    expect(next.video).toEqual([[0, 1], [1, 12]]);
    expect(next.narration).toEqual([[0, 1], [1, 12]]);
    // m1 (0 – 2) becomes two clips at 0 and 1; m2 is untouched; nothing moves.
    expect(next.music.map((c) => [c.at, c.in, c.out])).toEqual([[0, 0, 1], [1, 1, 2], [8, 0, 3]]);
    expect(next.markers).toBe(m.committedRef.current.markers);
  });

  it("leaves a locked lane alone - the music too - unless it is Ctrl+Shift+S, which splits everything", () => {
    const m = (mounted = mount());
    m.positionRef.current = 1;
    m.locksRef.current = locks(true, false, true);
    m.current().splitAtPlayhead(false);
    let next = sent(m);
    expect(next.video).toBeNull();
    expect(next.narration).toEqual([[0, 1], [1, 12]]);
    expect(next.music).toBe(m.committedRef.current.music);
    m.commitEdit.mockClear();
    m.locksRef.current = locks(true, true, true);
    m.current().splitAtPlayhead(true);
    next = sent(m);
    expect(next.video).toEqual([[0, 1], [1, 12]]);
    expect(next.narration).toEqual([[0, 1], [1, 12]]);
    expect(next.music).toHaveLength(3);
  });

  it("splits through a MISSING clip like any other (E6), both halves inside its slice; not when the lane is locked; Ctrl+Shift+S regardless", () => {
    const m = (mounted = mount());
    m.committedRef.current = { ...m.committedRef.current, music: [m1, gone] };
    m.positionRef.current = 6;
    m.current().splitAtPlayhead(false);
    expect(m.setRefusal).not.toHaveBeenCalled();
    const halves = (music: MusicClip[]) => music.filter((held) => held.file === "gone.mp3");
    // 4 – 8 split at 6: 0 – 2 of the file under its own id, then 2 – 4 under a new one, nothing moved.
    const [head, tail] = halves(sent(m).music);
    expect(head).toMatchObject({ id: "g", at: 4, in: 0, out: 2, missing: true });
    expect(tail).toMatchObject({ at: 6, in: 2, out: 4, missing: true });
    expect(tail.id).not.toBe("g");
    m.commitEdit.mockClear();
    m.locksRef.current = locks(false, false, true);
    m.current().splitAtPlayhead(false);
    expect(m.setRefusal).not.toHaveBeenCalled();
    expect(sent(m).music).toBe(m.committedRef.current.music);
    // Ctrl+Shift+S splits the locked lane too.
    m.commitEdit.mockClear();
    m.current().splitAtPlayhead(true);
    expect(m.setRefusal).not.toHaveBeenCalled();
    expect(halves(sent(m).music).map((held) => [held.in, held.out])).toEqual([[0, 2], [2, 4]]);
  });

  it("refuses a split past the cap, and does nothing under the lock", () => {
    const m = (mounted = mount());
    const many = Array.from({ length: MAX_CLIPS }, (_, i) => clip({ id: `c${i}`, at: i * 5, out: 4 }));
    m.committedRef.current = { ...m.committedRef.current, music: many };
    m.positionRef.current = 1;
    m.current().splitAtPlayhead(false);
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).toHaveBeenCalledWith(expect.stringContaining(`would make ${MAX_CLIPS + 1} music clips, past the limit of ${MAX_CLIPS}`));
    m.setRefusal.mockClear();
    m.state.editLocked = true;
    m.rerender();
    m.current().splitAtPlayhead(false);
    expect(m.commitEdit).not.toHaveBeenCalled();
    expect(m.setRefusal).not.toHaveBeenCalled();
  });
});

describe("what a nudge or a reset acts on, and what it sends", () => {
  it("acts on the selected blocks, else the chosen sentence when it is on the strip, else nothing", () => {
    const m = (mounted = mount());
    m.selectedBlocksRef.current = new Set([1, 2]);
    expect(m.current().actedOn().map((s) => s.index)).toEqual([1, 2]);
    m.selectedBlocksRef.current = new Set();
    m.state.selected = 0;
    m.rerender();
    expect(m.current().actedOn().map((s) => s.index)).toEqual([0]);
    m.state.selected = 99;
    m.rerender();
    expect(m.current().actedOn()).toEqual([]);
  });

  it("nudges from the DRAWN pins in one commitOffsets, resets to null, and never under the lock", () => {
    const m = (mounted = mount());
    m.selectedBlocksRef.current = new Set([0, 1]);
    m.current().nudge(0.05);
    // s0 is pinned where it was spoken; s1 is already 0.5 s late: both move by the nudge.
    expect(m.commitOffsets).toHaveBeenCalledWith([{ index: 0, offset: 0.05 }, { index: 1, offset: 0.55 }]);
    m.current().resetTiming();
    expect(m.commitOffsets).toHaveBeenLastCalledWith([{ index: 0, offset: null }, { index: 1, offset: null }]);
    m.commitOffsets.mockClear();
    m.state.editLocked = true;
    m.rerender();
    m.current().nudge(0.05);
    m.current().resetTiming();
    expect(m.commitOffsets).not.toHaveBeenCalled();
    m.state.editLocked = false;
    m.selectedBlocksRef.current = new Set();
    m.rerender();
    m.current().nudge(0.05);
    expect(m.commitOffsets).not.toHaveBeenCalled();
  });

  it("drawn: the index, the spoken start, and the pin's distance from it", () => {
    expect(drawn(SENTENCES)).toEqual([{ index: 0, start: 0, offset: 0 }, { index: 1, start: 5, offset: 0.5 }, { index: 2, start: 9, offset: 0 }]);
  });
});
