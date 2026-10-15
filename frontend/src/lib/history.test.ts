/**
 * The one undo history (lib/history.ts), shared by the annotation editor and the walkthrough builder:
 * commit, undo, redo, the forgotten future, no limit within the session, and the content-equality rule a
 * history is made with - what each document calls "the same" is its own, and a commit of the same adds no
 * step.
 */
import { describe, expect, it } from "vitest";
import { canRedo, canUndo, commit, history, prune, redo, undo } from "./history";

interface Doc { items: string[] }
const doc = (...items: string[]): Doc => ({ items });
const sameItems = (a: Doc, b: Doc) => a.items.join("|") === b.items.join("|");

describe("the history", () => {
  it("commit, undo, redo; a commit after an undo forgets the future; unlimited within the session", () => {
    let h = history(doc());
    expect(canUndo(h)).toBe(false);
    expect(canRedo(h)).toBe(false);
    h = commit(h, doc("a"));
    h = commit(h, doc("a", "b"));
    expect(h.present.items).toEqual(["a", "b"]);
    expect(canUndo(h)).toBe(true);
    h = undo(h);
    expect(h.present.items).toEqual(["a"]);
    expect(canRedo(h)).toBe(true);
    h = redo(h);
    expect(h.present.items).toEqual(["a", "b"]);
    h = undo(h);
    h = commit(h, doc("a", "c"));
    expect(canRedo(h)).toBe(false);
    expect(h.present.items).toEqual(["a", "c"]);
    for (let i = 0; i < 500; i++) h = commit(h, doc(...h.present.items, `x${i}`));
    expect(h.past).toHaveLength(502);
    for (let i = 0; i < 500; i++) h = undo(h);
    expect(h.present.items).toEqual(["a", "c"]);
  });

  it("undo at the start and redo at the end change nothing, and return the same history", () => {
    const h = history(doc("a"));
    expect(undo(h)).toBe(h);
    expect(redo(h)).toBe(h);
    expect(undo(h).present).toEqual(doc("a"));
  });

  it("with no rule, only the one value is the same: a fresh object of equal content is a step", () => {
    const h = history(doc("a"));
    expect(commit(h, h.present)).toBe(h);
    const next = commit(h, doc("a"));
    expect(next).not.toBe(h);
    expect(canUndo(next)).toBe(true);
  });

  it("a history made with a content rule adds no step for a commit the rule calls the same", () => {
    const h = history(doc("a"), sameItems);
    expect(commit(h, doc("a"))).toBe(h);
    expect(canUndo(commit(h, doc("a")))).toBe(false);
    const moved = commit(h, doc("b"));
    expect(canUndo(moved)).toBe(true);
    // The rule travels with the history through undo and redo.
    const back = undo(moved);
    expect(back.same).toBe(sameItems);
    expect(commit(back, doc("a"))).toBe(back);
    expect(redo(back).same).toBe(sameItems);
  });

  it("prune drops the pasts and the futures the rule refuses, keeps the present whatever the rule says, and is the same history when nothing goes", () => {
    let h = history(doc("a"), sameItems);
    h = commit(h, doc("a", "b"));
    h = commit(h, doc("a", "b", "c"));
    h = undo(h); // past [a], present [a, b], future [[a, b, c]]
    const pruned = prune(h, (d) => !d.items.includes("c"));
    expect(pruned.future).toEqual([]);
    expect(canRedo(pruned)).toBe(false);
    expect(pruned.past).toEqual([doc("a")]);
    expect(pruned.present).toEqual(doc("a", "b"));
    expect(pruned.same).toBe(sameItems);
    expect(prune(h, () => true)).toBe(h);
    const nothingKept = prune(h, () => false);
    expect(nothingKept.past).toEqual([]);
    expect(nothingKept.future).toEqual([]);
    expect(nothingKept.present).toBe(h.present);
  });
});
