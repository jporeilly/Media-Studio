// @vitest-environment jsdom
/**
 * The keyboard as BEHAVIOUR (R1d). The key map is `keyAction` - the
 * component's closure, moved whole - so every binding is a row of a table:
 * the keydown in, exactly the one move it makes out (every other move a fake
 * that must stay silent), and whether it was handled. What regexes over the
 * component's source used to pin - `M` dropping a marker once per press,
 * Ctrl+[ / ] jumping while plain [ / ] and AltGr's Ctrl+Alt+[ nudge, Delete
 * and Ctrl+X giving the marker, then the clip, then the range, Escape's order,
 * `K` held turning J and L into the frame step, the flag's keyup and blur -
 * is asserted on what the fakes were told; the listener is mounted for real
 * on jsdom's document, so the keyup, the blur, the text-box rule and the
 * unbinding are driven through real events. Each test was watched failing
 * with its defect planted; the round's report lists the plants.
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mountHook } from "./testing/mountHook";
import { NO_BLOCKS, NUDGE_LARGE_SECONDS, NUDGE_SECONDS, type Selection } from "./types";
import { keyAction, useTimelineKeys, type KeyActionDeps, type TimelineKeysDeps } from "./useTimelineKeys";

/** The moves the keys make, every one a fake that records its calls. */
const MOVES = [
  "nudge", "togglePlay", "stepBy", "shuttleKey", "seek", "jumpToEnd", "removeMarker", "removeClip", "cutSelection",
  "splitAtPlayhead", "dropMarker", "jumpToMarker", "undo", "redo", "extendSelection", "extendTo", "zoomStep", "zoomFit",
  "zoomSelection", "zoomAll", "setSelectedMarker", "setSelectedClip", "setSelectedBlocks", "setSelection",
] as const;
type Move = (typeof MOVES)[number];

interface Held {
  marker: string | null;
  clip: string | null;
  blocks: ReadonlySet<number>;
  range: Selection | null;
}

/** The key map's deps: the moves faked, the three selections and the range held in refs that the setters keep current, as the root's render does. */
function fakes(held0: Partial<Held> = {}, libraryOpen = false) {
  const held: Held = { marker: null, clip: null, blocks: NO_BLOCKS, range: null, ...held0 };
  const selectedMarkerRef = { current: held.marker };
  const selectedClipRef = { current: held.clip };
  const selectedBlocksRef = { current: held.blocks };
  const selectionRef = { current: held.range };
  const fns = Object.fromEntries(MOVES.map((name) => [name, vi.fn()])) as Record<Move, ReturnType<typeof vi.fn>>;
  fns.setSelectedMarker.mockImplementation((value: string | null) => { selectedMarkerRef.current = value; });
  fns.setSelectedClip.mockImplementation((value: string | null) => { selectedClipRef.current = value; });
  fns.setSelectedBlocks.mockImplementation((value: ReadonlySet<number>) => { selectedBlocksRef.current = value; });
  fns.setSelection.mockImplementation((value: Selection | null) => { selectionRef.current = value; });
  const deps = {
    ...fns, libraryOpenRef: { current: libraryOpen }, selectedMarkerRef, selectedClipRef, selectedBlocksRef, selectionRef,
    kHeldRef: { current: false },
  } as unknown as KeyActionDeps;
  /** Every move called, in order, as `name(args)`. */
  const calls = () => MOVES.flatMap((name) => fns[name].mock.invocationCallOrder.map((order, i) => ({ order, name, args: fns[name].mock.calls[i] })))
    .sort((a, b) => a.order - b.order).map(({ name, args }) => [name, ...args]);
  return { deps, fns, calls, refs: { selectedMarkerRef, selectedClipRef, selectedBlocksRef, selectionRef } };
}

type Init = KeyboardEventInit & { key: string };
const down = (init: Init) => new window.KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
/** One keydown through a fresh key map over `f`'s deps: whether it was handled. */
const hit = (f: ReturnType<typeof fakes>, init: Init) => keyAction(f.deps)(down(init));

const CTRL = { ctrlKey: true };
const SHIFT = { shiftKey: true };
const CS = { ctrlKey: true, shiftKey: true };

/**
 * [what, the keydown, the ONE move it makes (name and arguments) or null for none, handled]. Every binding is
 * a row; and where a binding matches a key by its physical `code` OR by the character it typed, each way is a
 * row of its own in which it ALONE matches - "by its code alone" (a layout whose key types another letter) and
 * "by its key alone" (a virtual keyboard or automation that sets no `code`) - so dropping either half of an
 * `||` turns a row red.
 */
const TABLE: [string, Init, [Move, ...unknown[]] | null, boolean][] = [
  ["Space", { key: " ", code: "Space" }, ["togglePlay"], true],
  ["Space by its key alone (a virtual keyboard)", { key: " ", code: "" }, ["togglePlay"], true],
  ["Space by its code alone", { key: "Unidentified", code: "Space" }, ["togglePlay"], true],
  ["Comma", { key: ",", code: "Comma" }, ["stepBy", -1], true],
  ["Period", { key: ".", code: "Period" }, ["stepBy", 1], true],
  ["S", { key: "s", code: "KeyS" }, ["splitAtPlayhead", false], true],
  ["S by its key alone", { key: "s", code: "" }, ["splitAtPlayhead", false], true],
  ["S by its code alone (a Cyrillic layout's ы)", { key: "ы", code: "KeyS" }, ["splitAtPlayhead", false], true],
  ["M", { key: "m", code: "KeyM" }, ["dropMarker"], true],
  ["M by its key alone", { key: "m", code: "" }, ["dropMarker"], true],
  ["M by its code alone (a Cyrillic layout's ь)", { key: "ь", code: "KeyM" }, ["dropMarker"], true],
  ["K", { key: "k", code: "KeyK" }, ["shuttleKey", "K"], true],
  ["J", { key: "j", code: "KeyJ" }, ["shuttleKey", "J"], true],
  ["L", { key: "l", code: "KeyL" }, ["shuttleKey", "L"], true],
  ["K by its key alone", { key: "k", code: "" }, ["shuttleKey", "K"], true],
  ["J by its key alone", { key: "j", code: "" }, ["shuttleKey", "J"], true],
  ["L by its key alone", { key: "l", code: "" }, ["shuttleKey", "L"], true],
  ["K by its code alone (a Cyrillic layout's л)", { key: "л", code: "KeyK" }, ["shuttleKey", "K"], true],
  ["J by its code alone (a Cyrillic layout's о)", { key: "о", code: "KeyJ" }, ["shuttleKey", "J"], true],
  ["L by its code alone (a Cyrillic layout's д)", { key: "д", code: "KeyL" }, ["shuttleKey", "L"], true],
  ["[", { key: "[", code: "BracketLeft" }, ["nudge", -NUDGE_SECONDS], true],
  ["]", { key: "]", code: "BracketRight" }, ["nudge", NUDGE_SECONDS], true],
  ["[ by its code alone (a German layout's ü)", { key: "ü", code: "BracketLeft" }, ["nudge", -NUDGE_SECONDS], true],
  ["] by its code alone (a German layout's +)", { key: "+", code: "BracketRight" }, ["nudge", NUDGE_SECONDS], true],
  ["Shift+[ ({)", { key: "{", code: "BracketLeft", ...SHIFT }, ["nudge", -NUDGE_LARGE_SECONDS], true],
  ["Shift+] (})", { key: "}", code: "BracketRight", ...SHIFT }, ["nudge", NUDGE_LARGE_SECONDS], true],
  ["Shift+[ reported as [ (Shift alone makes it the large step)", { key: "[", code: "BracketLeft", ...SHIFT }, ["nudge", -NUDGE_LARGE_SECONDS], true],
  ["AltGr+8 on QWERTZ ([ as Ctrl+Alt)", { key: "[", code: "Digit8", ctrlKey: true, altKey: true }, ["nudge", -NUDGE_SECONDS], true],
  ["AltGr+9 on QWERTZ (])", { key: "]", code: "Digit9", ctrlKey: true, altKey: true }, ["nudge", NUDGE_SECONDS], true],
  ["AltGr+7 on QWERTZ ({)", { key: "{", code: "Digit7", ctrlKey: true, altKey: true }, ["nudge", -NUDGE_LARGE_SECONDS], true],
  ["AltGr+0 on QWERTZ (})", { key: "}", code: "Digit0", ctrlKey: true, altKey: true }, ["nudge", NUDGE_LARGE_SECONDS], true],
  ["Ctrl+[", { key: "[", code: "BracketLeft", ...CTRL }, ["jumpToMarker", -1], true],
  ["Ctrl+]", { key: "]", code: "BracketRight", ...CTRL }, ["jumpToMarker", 1], true],
  ["Cmd+[ (a Mac's Ctrl)", { key: "[", code: "BracketLeft", metaKey: true }, ["jumpToMarker", -1], true],
  ["Delete with nothing selected", { key: "Delete", code: "Delete" }, ["cutSelection"], true],
  ["Backspace with nothing selected", { key: "Backspace", code: "Backspace" }, ["cutSelection"], true],
  ["Escape with nothing to give up", { key: "Escape", code: "Escape" }, null, false],
  ["Q (no binding)", { key: "q", code: "KeyQ" }, null, false],
  ["Alt+M (Alt is the browser's)", { key: "m", code: "KeyM", altKey: true }, null, false],
  ["Alt+Space", { key: " ", code: "Space", altKey: true }, null, false],
  ["Shift+Comma", { key: "<", code: "Comma", ...SHIFT }, ["extendSelection", -1], true],
  ["Shift+Period", { key: ">", code: "Period", ...SHIFT }, ["extendSelection", 1], true],
  ["Shift+L", { key: "L", code: "KeyL", ...SHIFT }, null, false],
  ["Shift+K", { key: "K", code: "KeyK", ...SHIFT }, null, false],
  ["Ctrl+Z", { key: "z", code: "KeyZ", ...CTRL }, ["undo"], true],
  ["Cmd+Z", { key: "z", code: "KeyZ", metaKey: true }, ["undo"], true],
  ["Ctrl+Y", { key: "y", code: "KeyY", ...CTRL }, ["redo"], true],
  ["Ctrl+X with nothing selected", { key: "x", code: "KeyX", ...CTRL }, ["cutSelection"], true],
  ["Ctrl+Delete with nothing selected", { key: "Delete", code: "Delete", ...CTRL }, ["cutSelection"], true],
  ["Ctrl+Home", { key: "Home", code: "Home", ...CTRL }, ["seek", 0], true],
  ["Ctrl+End", { key: "End", code: "End", ...CTRL }, ["jumpToEnd"], true],
  ["Ctrl+K", { key: "k", code: "KeyK", ...CTRL }, null, false],
  ["Ctrl+J", { key: "j", code: "KeyJ", ...CTRL }, null, false],
  ["Ctrl+Shift+Z", { key: "Z", code: "KeyZ", ...CS }, ["redo"], true],
  ["Ctrl+Shift+D", { key: "D", code: "KeyD", ...CS }, ["setSelection", null], true],
  ["Ctrl+Shift+S", { key: "S", code: "KeyS", ...CS }, ["splitAtPlayhead", true], true],
  ["Ctrl+Shift+S by its key alone", { key: "S", code: "", ...CS }, ["splitAtPlayhead", true], true],
  ["Ctrl+Shift+S by its code alone (a Cyrillic layout's Ы)", { key: "Ы", code: "KeyS", ...CS }, ["splitAtPlayhead", true], true],
  ["Ctrl+Shift+Home", { key: "Home", code: "Home", ...CS }, ["extendTo", "start"], true],
  ["Ctrl+Shift+End", { key: "End", code: "End", ...CS }, ["extendTo", "end"], true],
  ["Ctrl+Shift+= (Equal)", { key: "+", code: "Equal", ...CS }, ["zoomStep", 1], true],
  ["Ctrl+Shift+NumpadAdd", { key: "+", code: "NumpadAdd", ...CS }, ["zoomStep", 1], true],
  ["Ctrl+Shift+- (Minus)", { key: "_", code: "Minus", ...CS }, ["zoomStep", -1], true],
  ["Ctrl+Shift+NumpadSubtract", { key: "-", code: "NumpadSubtract", ...CS }, ["zoomStep", -1], true],
  ["Ctrl+Shift+Equal by its code alone (a German layout's ´)", { key: "´", code: "Equal", ...CS }, ["zoomStep", 1], true],
  ["Ctrl+Shift+NumpadAdd by its code alone", { key: "Unidentified", code: "NumpadAdd", ...CS }, ["zoomStep", 1], true],
  ["Ctrl+Shift with + typed and no code", { key: "+", code: "", ...CS }, ["zoomStep", 1], true],
  ["Ctrl+Shift with = typed and no code", { key: "=", code: "", ...CS }, ["zoomStep", 1], true],
  ["Ctrl+Shift+Minus by its code alone (a German layout's ß)", { key: "ß", code: "Minus", ...CS }, ["zoomStep", -1], true],
  ["Ctrl+Shift+NumpadSubtract by its code alone", { key: "Unidentified", code: "NumpadSubtract", ...CS }, ["zoomStep", -1], true],
  ["Ctrl+Shift with - typed and no code", { key: "-", code: "", ...CS }, ["zoomStep", -1], true],
  ["Ctrl+Shift with _ typed and no code", { key: "_", code: "", ...CS }, ["zoomStep", -1], true],
  ["Ctrl+Shift+7", { key: "&", code: "Digit7", ...CS }, ["zoomFit"], true],
  ["Ctrl+Shift+8", { key: "*", code: "Digit8", ...CS }, ["zoomSelection"], true],
  ["Ctrl+Shift+9", { key: "(", code: "Digit9", ...CS }, ["zoomAll"], true],
  ["Ctrl+Shift+L", { key: "L", code: "KeyL", ...CS }, null, false],
];

let mounted: { unmount: () => void }[] = [];
afterEach(() => {
  for (const m of mounted.reverse()) m.unmount();
  mounted = [];
  vi.restoreAllMocks();
});

describe("keyAction - the key map, every binding one move", () => {
  it.each(TABLE)("%s", (_what, init, move, handled) => {
    const f = fakes();
    expect(hit(f, init)).toBe(handled);
    expect(f.calls()).toEqual(move === null ? [] : [move]);
  });

  it("M drops a marker once per press: the key's repeat is swallowed and drops nothing", () => {
    const f = fakes();
    expect(hit(f, { key: "m", code: "KeyM", repeat: true })).toBe(true);
    expect(f.calls()).toEqual([]);
    expect(hit(f, { key: "m", code: "KeyM" })).toBe(true);
    expect(f.calls()).toEqual([["dropMarker"]]);
  });

  it("fires play, the split, the nudge, Delete, undo and redo once per press - their repeats swallowed - while the frame step, the selection's growth, the jumps and the zoom repeat", () => {
    const once: Init[] = [
      { key: " ", code: "Space" }, { key: "s", code: "KeyS" }, { key: "[", code: "BracketLeft" }, { key: "Delete", code: "Delete" },
      { key: "Backspace", code: "Backspace" }, { key: "z", code: "KeyZ", ...CTRL }, { key: "y", code: "KeyY", ...CTRL },
      { key: "x", code: "KeyX", ...CTRL }, { key: "Z", code: "KeyZ", ...CS }, { key: "S", code: "KeyS", ...CS },
      { key: "k", code: "KeyK" }, { key: "j", code: "KeyJ" }, { key: "l", code: "KeyL" },
    ];
    for (const init of once) {
      const f = fakes();
      expect(hit(f, { ...init, repeat: true }), init.key).toBe(true);
      expect(f.calls(), `${init.key} ${init.code}`).toEqual([]);
    }
    const repeats: [Init, [Move, ...unknown[]]][] = [
      [{ key: ",", code: "Comma" }, ["stepBy", -1]],
      [{ key: ">", code: "Period", ...SHIFT }, ["extendSelection", 1]],
      [{ key: "[", code: "BracketLeft", ...CTRL }, ["jumpToMarker", -1]],
      [{ key: "+", code: "Equal", ...CS }, ["zoomStep", 1]],
      [{ key: "Home", code: "Home", ...CTRL }, ["seek", 0]],
    ];
    for (const [init, move] of repeats) {
      const f = fakes();
      expect(hit(f, { ...init, repeat: true })).toBe(true);
      expect(f.calls()).toEqual([move]);
    }
  });

  it("gives Delete, Backspace, Ctrl+Delete and Ctrl+X to the selected marker first, then the selected clip, then the range - exactly one of the three", () => {
    const keys: Init[] = [
      { key: "Delete", code: "Delete" }, { key: "Backspace", code: "Backspace" },
      { key: "Delete", code: "Delete", ...CTRL }, { key: "x", code: "KeyX", ...CTRL },
    ];
    for (const init of keys) {
      const label = `${init.ctrlKey ? "Ctrl+" : ""}${init.key}`;
      const both = fakes({ marker: "k1", clip: "m1", range: { start: 1, end: 2 } });
      expect(hit(both, init)).toBe(true);
      expect(both.calls(), label).toEqual([["removeMarker"]]);
      const clip = fakes({ clip: "m1", range: { start: 1, end: 2 } });
      expect(hit(clip, init)).toBe(true);
      expect(clip.calls(), label).toEqual([["removeClip"]]);
      const range = fakes({ range: { start: 1, end: 2 }, blocks: new Set([3]) });
      expect(hit(range, init)).toBe(true);
      expect(range.calls(), label).toEqual([["cutSelection"]]);
    }
  });

  it("gives up the marker, then the clip, then the blocks, then the range, one per Escape, and returns false with nothing left to give up", () => {
    const f = fakes({ marker: "k1", clip: "m1", blocks: new Set([2, 3]), range: { start: 1, end: 2 } });
    const escape = keyAction(f.deps);
    const press = () => escape(down({ key: "Escape", code: "Escape" }));
    expect(press()).toBe(true);
    expect(f.calls()).toEqual([["setSelectedMarker", null]]);
    expect(press()).toBe(true);
    expect(press()).toBe(true);
    expect(press()).toBe(true);
    expect(f.calls()).toEqual([["setSelectedMarker", null], ["setSelectedClip", null], ["setSelectedBlocks", NO_BLOCKS], ["setSelection", null]]);
    expect(f.fns.setSelectedBlocks.mock.calls[0][0]).toBe(NO_BLOCKS);
    expect(press()).toBe(false);
    expect(f.calls()).toHaveLength(4);
  });

  it("K raises the K-held flag and stops the shuttle once per press; under it J and L step a frame at the repeat rate instead of shuttling; without it they shuttle once per press", () => {
    const f = fakes();
    const keys = keyAction(f.deps);
    const kHeld = f.deps.kHeldRef;
    expect(keys(down({ key: "k", code: "KeyK" }))).toBe(true);
    expect(kHeld.current).toBe(true);
    // K's repeats keep the flag up and stop nothing again.
    kHeld.current = false;
    expect(keys(down({ key: "k", code: "KeyK", repeat: true }))).toBe(true);
    expect(kHeld.current).toBe(true);
    expect(keys(down({ key: "j", code: "KeyJ" }))).toBe(true);
    expect(keys(down({ key: "j", code: "KeyJ", repeat: true }))).toBe(true);
    expect(keys(down({ key: "l", code: "KeyL" }))).toBe(true);
    expect(keys(down({ key: "l", code: "KeyL", repeat: true }))).toBe(true);
    expect(f.calls()).toEqual([["shuttleKey", "K"], ["stepBy", -1], ["stepBy", -1], ["stepBy", 1], ["stepBy", 1]]);
    // K up (the listener lowers it): J and L shuttle again, once per press.
    kHeld.current = false;
    expect(keys(down({ key: "j", code: "KeyJ" }))).toBe(true);
    expect(keys(down({ key: "j", code: "KeyJ", repeat: true }))).toBe(true);
    expect(keys(down({ key: "l", code: "KeyL" }))).toBe(true);
    expect(f.calls().slice(5)).toEqual([["shuttleKey", "J"], ["shuttleKey", "L"]]);
  });

  it("keeps J, K and L in the plain branch: with Shift or Ctrl they neither shuttle, step, nor raise the flag", () => {
    for (const mod of [SHIFT, CTRL, CS]) {
      const f = fakes();
      for (const [key, code] of [["k", "KeyK"], ["j", "KeyJ"], ["l", "KeyL"]]) {
        expect(hit(f, { key, code, ...mod })).toBe(false);
      }
      expect(f.calls()).toEqual([]);
      expect(f.deps.kHeldRef.current).toBe(false);
    }
    // And K held with Shift: a Shift+J is still no step.
    const f = fakes();
    f.deps.kHeldRef.current = true;
    expect(hit(f, { key: "J", code: "KeyJ", ...SHIFT })).toBe(false);
    expect(f.calls()).toEqual([]);
  });

  it("leaves the keyboard to the Library while it is open: nothing is handled, nothing moves", () => {
    const f = fakes({ marker: "k1", range: { start: 1, end: 2 } }, true);
    for (const init of [
      { key: " ", code: "Space" }, { key: "Escape", code: "Escape" }, { key: "Delete", code: "Delete" }, { key: "m", code: "KeyM" },
      { key: "k", code: "KeyK" }, { key: "z", code: "KeyZ", ...CTRL }, { key: "[", code: "BracketLeft" },
    ] as Init[]) {
      expect(hit(f, init)).toBe(false);
    }
    expect(f.calls()).toEqual([]);
    expect(f.deps.kHeldRef.current).toBe(false);
  });
});

describe("useTimelineKeys - the listener on the document (mounted under jsdom)", () => {
  function mount(active = true) {
    const f = fakes();
    const state: { active: boolean; togglePlay: () => void } = { active, togglePlay: f.fns.togglePlay as unknown as () => void };
    const { kHeldRef: _own, ...keys } = f.deps;
    const m = mountHook(useTimelineKeys, (): TimelineKeysDeps => ({ ...keys, togglePlay: state.togglePlay, active: state.active }));
    mounted.push(m);
    const on = (target: EventTarget, type: "keydown" | "keyup", init: Init) => {
      const event = new window.KeyboardEvent(type, { bubbles: true, cancelable: true, ...init });
      act(() => { target.dispatchEvent(event); });
      return event;
    };
    return { ...m, f, state, on };
  }
  const box = (tag: "input" | "textarea" | "button" | "div") => {
    const el = document.createElement(tag);
    document.body.appendChild(el);
    mounted.push({ unmount: () => el.remove() });
    return el;
  };

  it("binds keydown on the document while the tab is open and prevents what it handled; leaves alone a key typed into a box, an IME composition and an event already prevented", () => {
    const m = mount();
    const space = m.on(document, "keydown", { key: " ", code: "Space" });
    expect(m.f.calls()).toEqual([["togglePlay"]]);
    expect(space.defaultPrevented).toBe(true);
    const q = m.on(document, "keydown", { key: "q", code: "KeyQ" });
    expect(q.defaultPrevented).toBe(false);
    // Typed into the List tab's boxes or the Re-voice card's fields: theirs.
    for (const tag of ["input", "textarea"] as const) {
      const typed = m.on(box(tag), "keydown", { key: " ", code: "Space" });
      expect(typed.defaultPrevented).toBe(false);
    }
    m.on(document, "keydown", { key: " ", code: "Space", isComposing: true });
    const before = (event: Event) => event.preventDefault();
    window.addEventListener("keydown", before, { capture: true });
    try {
      m.on(document, "keydown", { key: " ", code: "Space" });
    } finally {
      window.removeEventListener("keydown", before, { capture: true });
    }
    expect(m.f.calls()).toEqual([["togglePlay"]]);
    // A closed tab binds nothing.
    const closed = mount(false);
    closed.on(document, "keydown", { key: " ", code: "Space" });
    expect(closed.f.calls()).toEqual([]);
  });

  it("lowers the K-held flag on K's keyup - named by its code alone, by the k or the K it typed alone, or in a text box - and when the window loses focus; a re-render leaves it up", () => {
    const m = mount();
    let seen = 0;
    /** The moves made since the last look. */
    const since = () => {
      const all = m.f.calls();
      const out = all.slice(seen);
      seen = all.length;
      return out;
    };
    m.on(document, "keydown", { key: "k", code: "KeyK" });
    // A re-render between K and J (Play halted, a state set) keeps the flag up: the listener is bound once per
    // `active`, not per render, so its cleanup - which lowers the flag - does not run.
    m.rerender();
    m.on(document, "keydown", { key: "j", code: "KeyJ" });
    expect(since()).toEqual([["shuttleKey", "K"], ["stepBy", -1]]);
    // K's keyup, each way it can be named on its own - by its code (a layout whose K types another letter), by
    // "k", by "K" (Shift or Caps Lock), with no code (a virtual keyboard) - and one landing in a text box.
    const ups: [EventTarget, Init][] = [
      [document, { key: "л", code: "KeyK" }], [document, { key: "k", code: "" }], [document, { key: "K", code: "" }],
      [box("input"), { key: "k", code: "KeyK" }],
    ];
    for (const [target, up] of ups) {
      m.on(document, "keydown", { key: "k", code: "KeyK" });
      m.on(target, "keyup", up);
      m.on(document, "keydown", { key: "j", code: "KeyJ" });
      expect(since(), JSON.stringify(up)).toEqual([["shuttleKey", "K"], ["shuttleKey", "J"]]);
    }
    // K held, then the window loses focus: its keyup never comes, and the flag comes down anyway.
    m.on(document, "keydown", { key: "k", code: "KeyK" });
    act(() => { window.dispatchEvent(new window.FocusEvent("blur")); });
    m.on(document, "keydown", { key: "l", code: "KeyL" });
    expect(since()).toEqual([["shuttleKey", "K"], ["shuttleKey", "L"]]);
    // Another key's keyup leaves it up.
    m.on(document, "keydown", { key: "k", code: "KeyK" });
    m.on(document, "keyup", { key: "j", code: "KeyJ" });
    m.on(document, "keydown", { key: "l", code: "KeyL" });
    expect(since()).toEqual([["shuttleKey", "K"], ["stepBy", 1]]);
  });

  it("unbinds on leaving the tab - the keydown, the keyup and the window's blur, each the very listener it added - the flag coming down with it, and binds again on return", () => {
    type Call = [string, unknown, ...unknown[]];
    const onWindow = { added: vi.spyOn(window, "addEventListener"), removed: vi.spyOn(window, "removeEventListener") };
    const onDocument = { added: vi.spyOn(document, "addEventListener"), removed: vi.spyOn(document, "removeEventListener") };
    /** The listeners handed to one of the four spies for one event type, in order. */
    const handed = (spy: { mock: { calls: unknown[][] } }, type: string) =>
      (spy.mock.calls as Call[]).filter((call) => call[0] === type).map((call) => call[1]);
    const m = mount();
    const blur = handed(onWindow.added, "blur");
    const keydown = handed(onDocument.added, "keydown");
    const keyup = handed(onDocument.added, "keyup");
    expect([blur, keydown, keyup].map((list) => list.length)).toEqual([1, 1, 1]);
    expect([handed(onWindow.removed, "blur"), handed(onDocument.removed, "keydown"), handed(onDocument.removed, "keyup")])
      .toEqual([[], [], []]);
    m.on(document, "keydown", { key: "k", code: "KeyK" });
    m.state.active = false;
    m.rerender();
    // Each listener removed is the one that was added: none is left behind on the window or the document.
    expect(handed(onWindow.removed, "blur")).toEqual(blur);
    expect(handed(onDocument.removed, "keydown")).toEqual(keydown);
    expect(handed(onDocument.removed, "keyup")).toEqual(keyup);
    m.on(document, "keydown", { key: " ", code: "Space" });
    expect(m.f.calls()).toEqual([["shuttleKey", "K"]]);
    m.state.active = true;
    m.rerender();
    expect(handed(onWindow.added, "blur")).toHaveLength(2);
    m.on(document, "keydown", { key: "j", code: "KeyJ" });
    expect(m.f.calls()).toEqual([["shuttleKey", "K"], ["shuttleKey", "J"]]);
  });

  it("prevents Space's keyup on a focused button or link, so Firefox's click cannot toggle a second time - and on nothing else", () => {
    const m = mount();
    expect(m.on(box("button"), "keyup", { key: " ", code: "Space" }).defaultPrevented).toBe(true);
    expect(m.on(box("div"), "keyup", { key: " ", code: "Space" }).defaultPrevented).toBe(false);
    expect(m.on(box("input"), "keyup", { key: " ", code: "Space" }).defaultPrevented).toBe(false);
    expect(m.on(box("button"), "keyup", { key: "Enter", code: "Enter" }).defaultPrevented).toBe(false);
  });

  it("answers with the key map of the LATEST render: a move handed in anew is the one a key calls", () => {
    const m = mount();
    const next = vi.fn<() => void>();
    m.state.togglePlay = next;
    m.rerender();
    m.on(document, "keydown", { key: " ", code: "Space" });
    expect(next).toHaveBeenCalledTimes(1);
    expect(m.f.fns.togglePlay).not.toHaveBeenCalled();
  });
});
