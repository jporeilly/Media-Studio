// @vitest-environment jsdom
/**
 * The edit's commit stack, its lock and its refusal, as BEHAVIOUR (R1a).
 * What a regex over the component's source used to pin - the PUT never a
 * DELETE, the body through `editBody` against what the server holds, the
 * undo entry carrying the music and the markers, the invalidations, the
 * refusal's copy, the lock over the refetch window - is here a hook mounted
 * for real under jsdom with the API mocked, so a rename or a reformat cannot
 * defeat it and a wrong rule cannot pass it. Each test was watched failing
 * with its defect planted; the round's report lists the plants.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SetStateAction } from "react";
import { QueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../../../api/client";
import { UNDO_DEPTH, editBody, type Keep, type Marker, type MusicClip } from "../../../lib/edit";
import { narrationPlanKey } from "../../../lib/timeline";
import { mountHook } from "./testing/mountHook";
import { afterRefusal, commitRequest, nextHistory, refusalKind, useEditCommits, type AfterCommit, type EditCommitsDeps, type History } from "./useEditCommits";
import type { Commit, EditState, Entry, Op } from "./types";
// The hook's own source, as text: the one whole-file negative that stood over
// the root (lib/music.test.ts, the pin "commits every edit with a PUT … and
// never deletes the edit") re-pointed at the unit that owns the code now, so
// the guard reaches every path in the file and not only the request function
// (the Reviewer's MINOR 1 of R1a).
import commitsSource from "./useEditCommits.ts?raw";

vi.mock("../../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../../api/client")>();
  return { ...real, api: { ...real.api, put: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});

const PID = "p1";
const SOURCE = 12;
const CUT: Keep = [[0, 6], [7.5, 12]];
const bed: MusicClip = { id: "m1", file: "bed.mp3", at: 5, in: 0, out: 4, gain: 0.15, fade_in: 1, fade_out: 2 };
const intro: Marker = { id: "k1", at: 1, name: "Intro" };
const wrap: Marker = { id: "k2", at: 9, name: "Wrap" };
/** What the server holds at the start of every test: a cut picture, a whole narration, one clip, one marker. */
const committed = (): EditState => ({ video: CUT, narration: null, music: [bed], markers: [intro] });
const ref = <T,>(current: T) => ({ current });
type EditOp = Extract<Op, { kind: "edit" }>;
const edit = (over: Partial<EditState> = {}): EditOp => ({ kind: "edit", ...committed(), ...over });
const entry = (n: number): Entry => ({ undo: edit({ markers: [] }), redo: edit({ markers: [{ ...wrap, id: `k${n}` }] }) });

beforeEach(() => {
  vi.mocked(api.put).mockReset();
  vi.mocked(api.patch).mockReset();
  vi.mocked(api.delete).mockReset();
});

describe("commitRequest - the one request a commit makes", () => {
  it("PUTs the edit through editBody against what the server holds, and never DELETEs", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const op = edit({ markers: [intro, wrap] });
    await commitRequest(op, committed(), SOURCE, PID);
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put).toHaveBeenCalledWith(`/api/projects/${PID}/edit`, editBody(op, committed(), SOURCE));
    // The body: the tracks always, the markers because they changed, no `music` key because they did not.
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ video: CUT, narration: null, markers: [{ id: "k1", at: 1, name: "Intro" }, { id: "k2", at: 9, name: "Wrap" }] });
    expect(api.delete).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("says a whole edit with nulls in a PUT rather than deleting the record (E3's DELETE wiped the music)", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    await commitRequest(edit({ video: [[0, SOURCE]], narration: null, music: [], markers: [] }), { ...committed(), music: [], markers: [] }, SOURCE, PID);
    expect(api.put).toHaveBeenCalledWith(`/api/projects/${PID}/edit`, { video: null, narration: null });
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("PATCHes a set of offsets as a list of {index, offset}, with null for a cleared one", async () => {
    vi.mocked(api.patch).mockResolvedValue({ sentences: [] });
    await commitRequest({ kind: "offsets", values: { 2: 0.25, 5: null } }, committed(), SOURCE, PID);
    expect(api.patch).toHaveBeenCalledWith(`/api/projects/${PID}/narration/offsets`, { offsets: [{ index: 2, offset: 0.25 }, { index: 5, offset: null }] });
    expect(api.put).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("never deletes the edit anywhere in the stack's own source - the whole file, as the pin over the root stood", () => {
    // E3 sent `DELETE /edit` when both tracks went whole, and a DELETE clears
    // the MUSIC too; the request function is guarded above by behaviour, and
    // this keeps the negative over every other path in the file (a success
    // handler, a refusal) exactly as it was kept over the root.
    expect(commitsSource).not.toMatch(/api\.delete[^\n]*\/edit/);
    expect(commitsSource).not.toMatch(/api\.delete\(/);
  });
});

describe("nextHistory - the stack after a commit succeeded", () => {
  const empty: History = { past: [], future: [] };

  it("pushes a `do` as one entry carrying the music and the markers both ways, and empties the redo side", () => {
    const before = edit();
    const op = edit({ music: [], markers: [intro, wrap] });
    const next = nextHistory({ past: [], future: [entry(1)] }, { kind: "do", op, before });
    expect(next.future).toEqual([]);
    expect(next.past).toHaveLength(1);
    // An undone cut puts the clips AND the markers back: both ops of the entry carry them.
    expect(next.past[0].undo).toEqual(before);
    expect(next.past[0].redo).toEqual(op);
    expect((next.past[0].undo as Op & EditState).music).toEqual([bed]);
    expect((next.past[0].undo as Op & EditState).markers).toEqual([intro]);
    expect((next.past[0].redo as Op & EditState).markers).toEqual([intro, wrap]);
  });

  it("keeps the last UNDO_DEPTH entries and drops the oldest", () => {
    let h = empty;
    for (let i = 0; i < UNDO_DEPTH + 3; i++) h = nextHistory(h, { kind: "do", op: edit({ markers: [{ ...wrap, id: `k${i}` }] }), before: edit() });
    expect(h.past).toHaveLength(UNDO_DEPTH);
    expect((h.past[0].redo as Op & EditState).markers[0].id).toBe("k3");
    expect((h.past[UNDO_DEPTH - 1].redo as Op & EditState).markers[0].id).toBe(`k${UNDO_DEPTH + 2}`);
  });

  it("moves an entry over on undo and back on redo", () => {
    const e = entry(1);
    const undone = nextHistory({ past: [entry(0), e], future: [] }, { kind: "undo", op: e.undo, entry: e });
    expect(undone.past).toEqual([entry(0)]);
    expect(undone.future).toEqual([e]);
    const redone = nextHistory(undone, { kind: "redo", op: e.redo, entry: e });
    expect(redone.past).toEqual([entry(0), e]);
    expect(redone.future).toEqual([]);
  });
});

/** The hook, mounted with its refs and callbacks faked; the props thunk reads the mutable `state`. */
function mount(over: Partial<EditCommitsDeps> = {}, qc?: QueryClient) {
  const state = { jobActive: false, stamp: { data: 1, error: 0 }, refusal: null as string | null };
  const committedRef = ref<EditState>(committed());
  const committedOffsetsRef = ref<Record<number, number | null>>({ 3: 0.5 });
  const sourceDurationRef = ref(SOURCE);
  const editLockedRef = ref(false);
  const setSelection = vi.fn();
  const onOffsetsSaved = vi.fn();
  // The root's own state, as the root would set it: the next render reads it back through the props thunk.
  const setRefusal = vi.fn((value: SetStateAction<string | null>) => {
    state.refusal = typeof value === "function" ? value(state.refusal) : value;
  });
  const after: AfterCommit = { clearMoved: vi.fn(), dropPending: vi.fn() };
  const afterCommitRef = ref(after);
  const m = mountHook(useEditCommits, () => ({
    projectId: PID,
    jobActive: state.jobActive,
    plan: { dataUpdatedAt: state.stamp.data, errorUpdatedAt: state.stamp.error },
    committedRef, committedOffsetsRef, sourceDurationRef, setSelection, onOffsetsSaved, afterCommitRef,
    refusal: state.refusal, setRefusal, editLockedRef,
    ...over,
  }), qc);
  return { ...m, state, committedRef, committedOffsetsRef, sourceDurationRef, editLockedRef, setSelection, setRefusal, onOffsetsSaved, after };
}

describe("afterRefusal - the stack after a refused commit (E6)", () => {
  const e1 = entry(1);
  const e2 = entry(2);
  const e3 = entry(3);
  const h: History = { past: [e1, e2], future: [e3] };
  const refused = new ApiError(400, "music clip 1 (g): file 'gone.mp3' is not in the library, so its slice can shrink but not grow");

  it("takes a refused undo's entry off the PAST and a refused redo's off the FUTURE, the other side the same array", () => {
    const afterUndo = afterRefusal(h, { kind: "undo", op: e2.undo, entry: e2 }, refused);
    expect(afterUndo.past).toEqual([e1]);
    expect(afterUndo.future).toBe(h.future);
    const afterRedo = afterRefusal(h, { kind: "redo", op: e3.redo, entry: e3 }, refused);
    expect(afterRedo.future).toEqual([]);
    expect(afterRedo.past).toBe(h.past);
  });

  it("keeps the stack - the same object - for a 409, a 5xx, a network error, a refused do, or an entry no longer on top", () => {
    const undoTop = { kind: "undo" as const, op: e2.undo, entry: e2 };
    expect(afterRefusal(h, undoTop, new ApiError(409, "A job holds the project."))).toBe(h);
    expect(afterRefusal(h, undoTop, new ApiError(500, "Internal Server Error"))).toBe(h);
    expect(afterRefusal(h, undoTop, new TypeError("Failed to fetch"))).toBe(h);
    expect(afterRefusal(h, { kind: "do", op: edit(), before: edit() }, refused)).toBe(h);
    expect(afterRefusal(h, { kind: "undo", op: e1.undo, entry: e1 }, refused)).toBe(h);
    expect(afterRefusal(h, { kind: "redo", op: e2.redo, entry: e2 }, refused)).toBe(h);
    // An entry EQUAL to the top but not the top itself is not the one that was sent.
    expect(afterRefusal(h, { kind: "undo", op: e2.undo, entry: { ...e2 } }, refused)).toBe(h);
  });

  it("gives the refusal the undo's or the redo's lead when an EDIT step was taken off, the timing's with its list for a timing step, else the edit's or the timing's", () => {
    const offsets: Entry = { undo: { kind: "offsets", values: { 3: null } }, redo: { kind: "offsets", values: { 3: 1 } } };
    expect(refusalKind({ kind: "undo", op: e2.undo, entry: e2 }, refused)).toBe("undo");
    expect(refusalKind({ kind: "redo", op: e3.redo, entry: e3 }, refused)).toBe("redo");
    // A timing step taken off keeps the timing's lead, never the cut's and the clips' (the re-review's r1).
    expect(refusalKind({ kind: "undo", op: offsets.undo, entry: offsets }, refused)).toBe("timing-undo");
    expect(refusalKind({ kind: "redo", op: offsets.redo, entry: offsets }, refused)).toBe("timing-redo");
    expect(refusalKind({ kind: "undo", op: e2.undo, entry: e2 }, new ApiError(409, "busy"))).toBe("edit");
    expect(refusalKind({ kind: "do", op: edit(), before: edit() }, refused)).toBe("edit");
    expect(refusalKind({ kind: "do", op: offsets.redo, before: offsets.undo }, refused)).toBe("timing");
    expect(refusalKind({ kind: "undo", op: offsets.undo, entry: offsets }, new ApiError(409, "busy"))).toBe("timing");
    expect(refusalKind(undefined, refused)).toBe("edit");
  });
});

describe("useEditCommits - the stack mounted", () => {
  let mounted: ReturnType<typeof mount> | null = null;
  afterEach(() => { mounted?.unmount(); mounted = null; });

  it("commits nothing while the edit, the clips and the markers are what the server holds - in the body's own terms", () => {
    const m = (mounted = mount());
    m.current().commitEdit(committed());
    // A list that is still the whole source is the same as `null`: a split on
    // the very start of an untouched track commits nothing.
    m.committedRef.current = { ...committed(), video: null };
    m.current().commitEdit({ ...committed(), video: [[0, SOURCE]] });
    expect(api.put).not.toHaveBeenCalled();
    expect(m.current().history.past).toEqual([]);
  });

  it("sends the edit once the markers alone change, and once the clips alone change", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [intro, wrap] });
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ video: CUT, narration: null, markers: [intro, wrap] });
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    m.current().commitEdit({ ...m.committedRef.current, music: [{ ...bed, at: 9 }] });
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.put).mock.calls[1][1]).toEqual({ video: CUT, narration: null, music: [{ ...bed, at: 9 }] });
  });

  it("advances what the server holds, clears the selection, pushes the undo entry and stamps the lock on success", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    const next: EditState = { video: [[0, 3], [5, 12]], narration: [[0, 3], [5, 12]], music: [{ ...bed, at: 3 }], markers: [intro] };
    m.current().commitEdit(next);
    await m.settle();
    // The success path makes no request of its own - and never a DELETE (the whole-file guard's behavioural half).
    expect(api.delete).not.toHaveBeenCalled();
    expect(m.committedRef.current).toEqual(next);
    expect(m.setSelection).toHaveBeenCalledWith(null);
    expect(m.current().history.past).toHaveLength(1);
    expect(m.current().history.past[0].undo).toEqual(edit());
    expect(m.current().history.past[0].redo).toEqual({ kind: "edit", ...next });
    // Locked: the commit is done but the plan it produced has not landed.
    expect(m.current().commit.isPending).toBe(false);
    expect(m.current().applying).toBe(true);
    expect(m.current().editLocked).toBe(true);
    expect(m.editLockedRef.current).toBe(true);
    expect(m.after.clearMoved).not.toHaveBeenCalled();
  });

  it("keeps the range selection only for a do flagged keepSelection - a marker dropped, named or moved - and clears it for a markers-only do without it, for that commit's undo and redo, and for every other edit (E6)", async () => {
    // The owner decided about DROPPING a marker (E5b's parked call). The
    // gesture says so on its commit; the stack does not guess from what the
    // commit changed, since a marker's removal changes the markers alone too
    // and must clear the range - else a second Delete cuts it.
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    const land = (n: number) => { m.state.stamp = { data: n, error: 0 }; m.rerender(); };
    m.current().commitEdit({ ...committed(), markers: [intro, wrap] }, true);
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(1);
    // The flag is the gesture's, not the request's: the body is what it always was.
    expect(vi.mocked(api.put).mock.calls[0][1]).toEqual({ video: CUT, narration: null, markers: [intro, wrap] });
    expect(m.setSelection).not.toHaveBeenCalled();
    // Its undo and its redo clear the range, as every undo and redo did before E6.
    land(2);
    m.current().undo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(m.setSelection).toHaveBeenCalledTimes(1);
    expect(m.setSelection).toHaveBeenLastCalledWith(null);
    land(3);
    m.current().redo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(3);
    expect(m.setSelection).toHaveBeenCalledTimes(2);
    // A markers-only do WITHOUT the flag - a marker's removal - clears it.
    land(4);
    m.current().commitEdit({ ...m.committedRef.current, markers: [intro] });
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(4);
    expect(m.setSelection).toHaveBeenCalledTimes(3);
    // The clips alone: cleared.
    land(5);
    m.current().commitEdit({ ...m.committedRef.current, music: [{ ...bed, at: 9 }] });
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(5);
    expect(m.setSelection).toHaveBeenCalledTimes(4);
    // A cut: cleared, whatever it carries.
    land(6);
    m.current().commitEdit({ ...m.committedRef.current, video: [[0, 3], [5, 12]], markers: [] });
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(6);
    expect(m.setSelection).toHaveBeenCalledTimes(5);
    expect(m.setSelection).toHaveBeenLastCalledWith(null);
    // An offsets commit never touched the selection, before E6 or since.
    land(7);
    vi.mocked(api.patch).mockResolvedValue({ sentences: [] });
    m.current().commitOffsets([{ index: 3, offset: 1 }]);
    await m.settle();
    expect(m.setSelection).toHaveBeenCalledTimes(5);
  });

  it("takes an undo the server refused on its body (400) off the undo list, says so first, and the next Ctrl+Z sends nothing (E6)", async () => {
    // A missing clip trimmed shorter: the server keeps it (E6), but not its
    // undo, which would lengthen it again. Before E6's fix round the refused
    // entry stayed on top and every later Undo resent it.
    const gone: MusicClip = { ...bed, id: "g", file: "gone.mp3", in: 0, out: 4, missing: true, file_duration: null };
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.committedRef.current = { ...committed(), music: [gone] };
    m.current().commitEdit({ ...m.committedRef.current, music: [{ ...gone, in: 1 }] });
    await m.settle();
    expect(m.current().history.past).toHaveLength(1);
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    const said = "music clip 1 (g): file 'gone.mp3' is not in the library, so its slice can shrink but not grow; it was 1.000–4.000 of the file.";
    vi.mocked(api.put).mockRejectedValueOnce(new ApiError(400, said));
    vi.mocked(m.after.clearMoved).mockClear();
    m.current().undo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(m.current().history).toEqual({ past: [], future: [] });
    expect(m.current().editError).toBe(
      "That undo was refused and has been taken off the undo list — the strip still shows the cut and the clips the server"
      + ` holds. To undo past it, put the file back in the library first. The server said: ${said}`,
    );
    expect(m.after.clearMoved).toHaveBeenCalledTimes(1);
    expect(m.committedRef.current.music).toEqual([{ ...gone, in: 1 }]);
    m.current().undo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(2);
  });

  it("with two steps, a refused undo takes the top one off and the next Ctrl+Z sends the one beneath (E6)", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [intro, wrap] });
    await m.settle();
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    m.current().commitEdit({ ...m.committedRef.current, music: [{ ...bed, at: 9 }] });
    await m.settle();
    m.state.stamp = { data: 3, error: 0 };
    m.rerender();
    const [beneath, top] = m.current().history.past;
    vi.mocked(api.put).mockRejectedValueOnce(new ApiError(400, "refused on its body"));
    m.current().undo();
    await m.settle();
    expect(vi.mocked(api.put).mock.calls[2][1]).toEqual(editBody(top.undo as EditOp, m.committedRef.current, SOURCE));
    expect(m.current().history.past).toEqual([beneath]);
    expect(m.current().history.past[0]).toBe(beneath);
    m.current().undo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(4);
    // The step beneath is the whole edit before the first commit - against what the server still holds,
    // which kept the refused step's clip change, so the clips ride along too.
    expect(vi.mocked(api.put).mock.calls[3][1]).toEqual({ video: CUT, narration: null, music: [bed], markers: [intro] });
    expect(m.current().history.past).toEqual([]);
  });

  it("keeps an undo refused with a 409 - a job, not a verdict on the body - and the next Ctrl+Z resends it byte for byte (E6)", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [intro, wrap] });
    await m.settle();
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    vi.mocked(api.put).mockRejectedValueOnce(new ApiError(409, "A job holds the project."));
    m.current().undo();
    await m.settle();
    expect(m.current().history.past).toHaveLength(1);
    expect(m.current().editError).toMatch(/^That edit was not saved/);
    m.current().undo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(3);
    expect(vi.mocked(api.put).mock.calls[2][1]).toEqual(vi.mocked(api.put).mock.calls[1][1]);
    expect(m.current().history.past).toEqual([]);
  });

  it("takes a redo the server refused on its body off the redo list, the undo list untouched, and says so first (E6)", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [intro, wrap] });
    await m.settle();
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    m.current().commitEdit({ ...m.committedRef.current, music: [{ ...bed, at: 9 }] });
    await m.settle();
    m.state.stamp = { data: 3, error: 0 };
    m.rerender();
    m.current().undo();
    await m.settle();
    m.state.stamp = { data: 4, error: 0 };
    m.rerender();
    const past = m.current().history.past;
    expect(past).toHaveLength(1);
    expect(m.current().history.future).toHaveLength(1);
    vi.mocked(api.put).mockRejectedValueOnce(new ApiError(400, "refused on its body"));
    m.current().redo();
    await m.settle();
    expect(m.current().history.future).toEqual([]);
    expect(m.current().history.past).toBe(past);
    // The server's sentence names no file, so the lead advises none (the re-review's r1).
    expect(m.current().editError).toBe(
      "That redo was refused and has been taken off the redo list — the strip still shows the cut and the clips the server"
      + " holds. The server said: refused on its body",
    );
  });

  it("takes a TIMING undo refused on its body off the undo list under the timing's own lead, advising no file (E6, r1)", async () => {
    // An offsets undo can meet a 400: an index a transcript save has since
    // removed (api/routers/narration.py). The step can never apply, so it is
    // dropped - but it is a timing, not the cut and the clips, and no library
    // file has anything to do with it.
    vi.mocked(api.patch).mockResolvedValue({ sentences: [] });
    const m = (mounted = mount());
    m.current().commitOffsets([{ index: 3, offset: 1 }]);
    await m.settle();
    expect(m.current().history.past).toHaveLength(1);
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    const said = "offset 1: sentence index 3 is outside the transcript.";
    vi.mocked(api.patch).mockRejectedValueOnce(new ApiError(400, said));
    m.current().undo();
    await m.settle();
    expect(api.patch).toHaveBeenCalledTimes(2);
    expect(m.current().history).toEqual({ past: [], future: [] });
    expect(m.current().editError).toBe(
      "That timing was not saved — the blocks are back where the last saved plan puts them. It has been taken off the undo"
      + ` list. The server said: ${said}`,
    );
    expect(m.current().editError).not.toContain("put the file back");
  });

  it("stays locked until the plan's own stamp moves - not on a re-render, not on the job - then clears the moved paint once", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [] });
    await m.settle();
    expect(m.current().editLocked).toBe(true);
    m.rerender();
    m.rerender();
    expect(m.current().editLocked).toBe(true);
    m.state.jobActive = true;
    m.rerender();
    m.state.jobActive = false;
    m.rerender();
    expect(m.current().editLocked).toBe(true);
    expect(m.after.clearMoved).not.toHaveBeenCalled();
    // The refetch FAILED: the error stamp moves, and that unlocks too.
    m.state.stamp = { data: 1, error: 5 };
    m.rerender();
    expect(m.current().editLocked).toBe(false);
    expect(m.current().applying).toBe(false);
    expect(m.after.clearMoved).toHaveBeenCalledTimes(1);
    m.rerender();
    expect(m.after.clearMoved).toHaveBeenCalledTimes(1);
  });

  it("invalidates the plan, the project and the edit on an edit; only the plan on offsets; never another project's", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    vi.mocked(api.patch).mockResolvedValue({ sentences: [] });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const seed = () => {
      for (const key of [[...narrationPlanKey(PID), "edge", "en-GB", 1], ["project", PID], ["edit", PID], [...narrationPlanKey("p2"), "edge", "en-GB", 1], ["edit", "p2"]]) {
        qc.setQueryData(key, { seeded: true });
      }
    };
    const stale = (key: unknown[]) => qc.getQueryState(key)?.isInvalidated === true;
    seed();
    const m = (mounted = mount({}, qc));
    m.current().commitEdit({ ...committed(), markers: [] });
    await m.settle();
    expect(stale([...narrationPlanKey(PID), "edge", "en-GB", 1])).toBe(true);
    expect(stale(["project", PID])).toBe(true);
    expect(stale(["edit", PID])).toBe(true);
    expect(stale([...narrationPlanKey("p2"), "edge", "en-GB", 1])).toBe(false);
    expect(stale(["edit", "p2"])).toBe(false);
    // Offsets: the plan alone (the strip is redrawn from it); the edit's own record did not change.
    seed();
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    m.current().commitOffsets([{ index: 3, offset: 0.75 }]);
    await m.settle();
    expect(stale([...narrationPlanKey(PID), "edge", "en-GB", 1])).toBe(true);
    expect(stale(["project", PID])).toBe(false);
    expect(stale(["edit", PID])).toBe(false);
  });

  it("says what was not saved in the user's terms first, keeps the server's sentence, and puts the paint back", async () => {
    vi.mocked(api.put).mockRejectedValue(new ApiError(409, "A job holds the project."));
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [] });
    await m.settle();
    expect(m.current().editError).toBe("That edit was not saved — the strip still shows the cut and the clips the server holds. The server said: A job holds the project.");
    expect(m.current().editLocked).toBe(false);
    expect(m.after.clearMoved).toHaveBeenCalledTimes(1);
    expect(m.after.dropPending).toHaveBeenCalledTimes(1);
    // Nothing advanced, nothing on the stack.
    expect(m.committedRef.current).toEqual(committed());
    expect(m.current().history.past).toEqual([]);
    // An offsets refusal names the TIMING.
    vi.mocked(api.patch).mockRejectedValue(new ApiError(409, "A job holds the project."));
    m.current().commitOffsets([{ index: 3, offset: 0.75 }]);
    await m.settle();
    expect(m.current().editError).toMatch(/^That timing was not saved/);
    expect(m.current().editError).toContain("A job holds the project.");
    // A refusal made on the strip wins over the server's, and the next commit clears it (`onMutate`).
    m.setRefusal("Keep at least one range — that selection would remove the whole picture.");
    m.rerender();
    expect(m.current().editError).toBe("Keep at least one range — that selection would remove the whole picture.");
    vi.mocked(api.put).mockResolvedValue({});
    m.current().commitEdit({ ...committed(), markers: [] });
    await m.settle();
    expect(m.current().editError).toBeNull();
  });

  it("undoes with the entry's before and redoes with its after, and refuses both under a job", async () => {
    vi.mocked(api.put).mockResolvedValue({});
    const m = (mounted = mount());
    m.current().commitEdit({ ...committed(), markers: [intro, wrap] });
    await m.settle();
    expect(api.delete).not.toHaveBeenCalled();
    m.state.stamp = { data: 2, error: 0 };
    m.rerender();
    m.current().undo();
    await m.settle();
    expect(api.delete).not.toHaveBeenCalled();
    // The undo sends the "before" - against what the server holds NOW, so the markers key is back to the one.
    expect(api.put).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.put).mock.calls[1][1]).toEqual({ video: CUT, narration: null, markers: [intro] });
    expect(m.committedRef.current.markers).toEqual([intro]);
    expect(m.current().history.past).toEqual([]);
    expect(m.current().history.future).toHaveLength(1);
    m.state.stamp = { data: 3, error: 0 };
    m.rerender();
    m.current().redo();
    await m.settle();
    expect(api.delete).not.toHaveBeenCalled();
    expect(api.put).toHaveBeenCalledTimes(3);
    expect(vi.mocked(api.put).mock.calls[2][1]).toEqual({ video: CUT, narration: null, markers: [intro, wrap] });
    expect(m.current().history.past).toHaveLength(1);
    expect(m.current().history.future).toEqual([]);
    m.state.stamp = { data: 4, error: 0 };
    m.state.jobActive = true;
    m.rerender();
    m.current().undo();
    m.current().redo();
    await m.settle();
    expect(api.put).toHaveBeenCalledTimes(3);
    expect(api.delete).not.toHaveBeenCalled();
  });

  it("sends one PATCH for the offsets that changed, with what they were as the undo, and folds the answer in", async () => {
    const m = (mounted = mount());
    // Nothing changed: 3 is already 0.5 and 4 is already absent, which reads as null.
    expect(m.current().commitOffsets([{ index: 3, offset: 0.5 }, { index: 4, offset: null }])).toBe(false);
    expect(api.patch).not.toHaveBeenCalled();
    const saved = [{ index: 3, start: 1, end: 2, text: "x", offset: 0.75 }, { index: 4, start: 5, end: 6, text: "y", offset: 0.1 }];
    vi.mocked(api.patch).mockResolvedValue({ sentences: saved });
    expect(m.current().commitOffsets([{ index: 3, offset: 0.75 }, { index: 4, offset: 0.1 }, { index: 5, offset: null }])).toBe(true);
    await m.settle();
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch).toHaveBeenCalledWith(`/api/projects/${PID}/narration/offsets`, { offsets: [{ index: 3, offset: 0.75 }, { index: 4, offset: 0.1 }] });
    expect(m.onOffsetsSaved).toHaveBeenCalledWith(saved);
    expect(m.committedOffsetsRef.current).toEqual({ 3: 0.75, 4: 0.1 });
    expect(m.current().history.past[0].undo).toEqual({ kind: "offsets", values: { 3: 0.5, 4: null } });
    expect(m.current().history.past[0].redo).toEqual({ kind: "offsets", values: { 3: 0.75, 4: 0.1 } });
    // The selection is the picture's; a timing change leaves it alone.
    expect(m.setSelection).not.toHaveBeenCalled();
  });

  it("is locked while a job holds the project and while a commit is in flight", async () => {
    let resolvePut: (value: unknown) => void = () => {};
    vi.mocked(api.put).mockImplementation(() => new Promise((resolve) => { resolvePut = resolve; }));
    const m = (mounted = mount());
    expect(m.current().editLocked).toBe(false);
    m.state.jobActive = true;
    m.rerender();
    expect(m.current().editLocked).toBe(true);
    expect(m.editLockedRef.current).toBe(true);
    m.state.jobActive = false;
    m.rerender();
    expect(m.editLockedRef.current).toBe(false);
    m.rerender();
    m.current().commitEdit({ ...committed(), markers: [] });
    await m.settle();
    expect(m.current().commit.isPending).toBe(true);
    expect(m.current().editLocked).toBe(true);
    resolvePut({});
    await m.settle();
    expect(m.current().commit.isPending).toBe(false);
    expect(m.current().editLocked).toBe(true);  // awaiting the plan now
  });

  it("carries the mutation's own kind for the toolbar's status line", async () => {
    let resolvePatch: (value: unknown) => void = () => {};
    vi.mocked(api.patch).mockImplementation(() => new Promise((resolve) => { resolvePatch = resolve; }));
    const m = (mounted = mount());
    m.current().commitOffsets([{ index: 3, offset: 0.75 }]);
    await m.settle();
    expect(m.current().commit.isPending).toBe(true);
    expect(m.current().commit.variables?.op.kind).toBe("offsets");
    resolvePatch({ sentences: [] });
    await m.settle();
  });
});

describe("the entry type carries the whole edit", () => {
  it("cannot be built without the music and the markers", () => {
    // By construction: `EditState` is `TrackEdit & { music; markers }`, and a
    // `do` needs one on each side. The cast below is what tsc refuses without
    // them; the runtime check is that the entry really holds both.
    const commit: Commit = { kind: "do", op: edit(), before: edit({ music: [], markers: [] }) };
    expect(commit.kind === "do" && "music" in commit.before && "markers" in commit.before).toBe(true);
  });
});
