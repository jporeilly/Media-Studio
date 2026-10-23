/**
 * Guided capture (W2), the pure half: the hotspot round the pointer in the picture's own pixels (the origin
 * subtracted, clamped at the edges, none outside), the window under the pointer, what each kind captures, the
 * still's name, the session's walk (advance, next, back, the end), the bar's payload, the key, the constants - and
 * (W2b) the click: its payload, the frame rule per kind, the distinct-steps count, the bar's lines - and (W2d) the
 * step held until Next, the stills per step, the typed capture's count, the lines with typing, the delay.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MonitorInfo, WindowInfo } from "./capture";
import {
  BAR_MODE, CLICKS_OFF, CLICK_HINT, CLICK_OUTSIDE_FRAME, CLICK_OUTSIDE_SCREEN, HOOKS_OFF, HOTSPOT_DEFAULT, KEYS_AND_HOOKS_OFF, KEYS_MODE,
  KEYS_TAKEN, KEYS_TAKEN_CLICK, MAX_IN_FLIGHT, NEXT_KEY_TAKEN, NO_WINDOW_UNDER_POINTER, NO_WINDOW_UNDER_POINTER_CLICK, SETTLE_MS,
  STEPS_BAR_HEIGHT, STEPS_BAR_WIDTH, STEP_CLICK_EVENT, STEP_KEY_EVENT, STEP_NEXT_EVENT, STEP_TYPED_EVENT, TOO_FAST, TYPED_OUTSIDE_FRAME,
  TYPED_OUTSIDE_SCREEN, TYPED_OUTSIDE_WINDOW, TYPING_HINT, TYPING_OFF, TYPING_OFF_BAR, TYPING_OFF_CLICK, TYPING_PAUSE_MS,
  advance, atEnd, barMessage, barPayload, captureName, captureTarget, capturedSteps, claimStep, clickArea, clickOf, clickRule, currentId, delay,
  frameFor, guidedHotkey, hintLine, hotspotAround, hotspotOf, insideFrame, keptPictureLine, keysLines, keysUpOf, missedLine, nextHotkey,
  nextLabel, noPictureLine, noWindowLine, outsideLine, pointerOf, recordCapture, rectsIntersect, remaining, sessionOf, stepBack, stillsThisStep,
  typedAfterNext, typedArea, typedHotspotPoint, typedOf, typedOutsideLine, typedRule, windowUnder, KEYS_AND_CLICKS_OFF, KEY_TAKEN, KEY_TAKEN_CLICK,
} from "./guided";
import { type Step, MIN_HOTSPOT_SIDE } from "./walkthrough";

const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: null, kind: "tooltip", text: `Text ${id}`, instruction: `Do ${id}`, placement: "auto", chapter: "", note: "", source: null, ...over,
});
const monitors: MonitorInfo[] = [
  { index: 0, name: "A", x: 0, y: 0, width: 1920, height: 1080, scale: 1, primary: true },
  { index: 1, name: "B", x: 1920, y: 0, width: 2560, height: 1440, scale: 1.25, primary: false },
];
const windows: WindowInfo[] = [
  { hwnd: 7, title: "Dialog on top", x: 300, y: 300, width: 400, height: 200 },
  { hwnd: 42, title: "Spoon", x: 100, y: 100, width: 1200, height: 800 },
];

describe("the hotspot round the pointer", () => {
  it("is HOTSPOT_DEFAULT (160 x 60) centred on the pointer in the picture's pixels, the capture's origin subtracted", () => {
    expect(HOTSPOT_DEFAULT).toEqual({ width: 160, height: 60 });
    expect(hotspotAround({ x: 500, y: 400 }, HOTSPOT_DEFAULT, { x: 100, y: 100 }, { width: 800, height: 600 })).toEqual({ x: 320, y: 270, width: 160, height: 60 });
    // A negative origin (a monitor left of the primary) is the same subtraction.
    expect(hotspotAround({ x: -1000, y: 50 }, HOTSPOT_DEFAULT, { x: -1920, y: 0 }, { width: 1920, height: 1080 })).toEqual({ x: 840, y: 20, width: 160, height: 60 });
  });

  it("is kept inside the picture at the edges, and shrunk to a small picture", () => {
    expect(hotspotAround({ x: 5, y: 5 }, HOTSPOT_DEFAULT, { x: 0, y: 0 }, { width: 800, height: 600 })).toEqual({ x: 0, y: 0, width: 160, height: 60 });
    expect(hotspotAround({ x: 799, y: 599 }, HOTSPOT_DEFAULT, { x: 0, y: 0 }, { width: 800, height: 600 })).toEqual({ x: 640, y: 540, width: 160, height: 60 });
    expect(hotspotAround({ x: 50, y: 20 }, HOTSPOT_DEFAULT, { x: 0, y: 0 }, { width: 100, height: 40 })).toEqual({ x: 0, y: 0, width: 100, height: 40 });
    expect(hotspotAround({ x: 3, y: 3 }, HOTSPOT_DEFAULT, { x: 0, y: 0 }, { width: MIN_HOTSPOT_SIDE - 1, height: 50 })).toBeNull();
  });

  it("is none for a pointer outside the captured area, or no pointer at all (the bar's button)", () => {
    const picture = { width: 800, height: 600 };
    expect(hotspotAround({ x: 99, y: 400 }, HOTSPOT_DEFAULT, { x: 100, y: 100 }, picture)).toBeNull();
    expect(hotspotAround({ x: 900, y: 400 }, HOTSPOT_DEFAULT, { x: 100, y: 100 }, picture)).toBeNull();
    expect(hotspotAround({ x: 500, y: 700 }, HOTSPOT_DEFAULT, { x: 100, y: 100 }, picture)).toBeNull();
    expect(hotspotAround({ x: 100, y: 100 }, HOTSPOT_DEFAULT, { x: 100, y: 100 }, picture)).toEqual({ x: 0, y: 0, width: 160, height: 60 });
    expect(hotspotAround({ x: Number.NaN, y: 1 }, HOTSPOT_DEFAULT, { x: 0, y: 0 }, picture)).toBeNull();
    expect(hotspotOf(null, { x: 0, y: 0, width: 800, height: 600 })).toBeNull();
    expect(hotspotOf({ x: 500, y: 400 }, { x: 100, y: 100, width: 800, height: 600 })).toEqual({ x: 320, y: 270, width: 160, height: 60 });
  });
});

describe("what a capture takes", () => {
  it("the window under the pointer is the top-most whose frame holds it; the front-most without a pointer; none outside every window", () => {
    expect(windowUnder(windows, { x: 350, y: 350 })?.hwnd).toBe(7);
    expect(windowUnder(windows, { x: 150, y: 150 })?.hwnd).toBe(42);
    expect(windowUnder(windows, { x: 1300, y: 150 })).toBeNull();
    expect(windowUnder(windows, { x: 699, y: 499 })?.hwnd).toBe(7);
    expect(windowUnder(windows, { x: 700, y: 500 })?.hwnd).toBe(42);
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 150, y: 150 } })).toEqual({ rect: { x: 100, y: 100, width: 1200, height: 800 }, title: "Spoon" });
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: null })).toEqual({ rect: { x: 300, y: 300, width: 400, height: 200 }, title: "Dialog on top" });
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 5, y: 5 } })).toHaveProperty("error");
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows: [], pointer: null })).toHaveProperty("error");
    // The refusal's wording by mode (W2b-1): with the clicks on, a click will do too.
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 5, y: 5 } })).toEqual({ error: NO_WINDOW_UNDER_POINTER });
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 5, y: 5 }, clicks: false })).toEqual({ error: NO_WINDOW_UNDER_POINTER });
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 5, y: 5 }, clicks: true })).toEqual({ error: NO_WINDOW_UNDER_POINTER_CLICK });
    expect(noWindowLine(true)).toBe("No window is under the pointer: point at the window and click, or press Shift+F8.");
    expect(noWindowLine(false)).toBe("No window is under the pointer: point at the window and press Shift+F8.");
    // A typed pause (F1): the given window - the one in the foreground, typed into - wins over the list and the pointer;
    // null (no foreground window) is the no-window refusal; left out (not a typed capture) is the rule above.
    const typedInto = { x: 1500, y: 100, width: 300, height: 300 };
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 150, y: 150 }, window: typedInto })).toEqual({ rect: typedInto, title: null });
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 150, y: 150 }, window: null, clicks: true })).toEqual({ error: NO_WINDOW_UNDER_POINTER_CLICK });
    expect(captureTarget("window", { region: null, monitors, monitor: null, windows, pointer: { x: 150, y: 150 }, window: undefined })).toEqual({ rect: { x: 100, y: 100, width: 1200, height: 800 }, title: "Spoon" });
    // The other kinds take their own rectangle whatever window was typed into.
    const region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    expect(captureTarget("region", { region, monitors, monitor: null, windows, pointer: null, window: typedInto })).toEqual({ rect: { x: 10, y: 20, width: 300, height: 200 }, title: "Spoon" });
    expect(captureTarget("screen", { region: null, monitors, monitor: null, windows, pointer: null, window: null })).toEqual({ rect: { x: 0, y: 0, width: 1920, height: 1080 }, title: null });
  });

  it("a region is the one chosen once; a full screen is the chosen monitor, else the primary", () => {
    const region = { x: 10, y: 20, width: 300, height: 200, monitor: 0, window_title: "Spoon", hwnd: 42 };
    expect(captureTarget("region", { region, monitors, monitor: null, windows, pointer: { x: 0, y: 0 } })).toEqual({ rect: { x: 10, y: 20, width: 300, height: 200 }, title: "Spoon" });
    expect(captureTarget("region", { region: null, monitors, monitor: null, windows, pointer: null })).toHaveProperty("error");
    expect(captureTarget("screen", { region: null, monitors, monitor: 1, windows, pointer: null })).toEqual({ rect: { x: 1920, y: 0, width: 2560, height: 1440 }, title: null });
    expect(captureTarget("screen", { region: null, monitors, monitor: null, windows, pointer: null })).toEqual({ rect: { x: 0, y: 0, width: 1920, height: 1080 }, title: null });
    expect(captureTarget("screen", { region: null, monitors: [], monitor: null, windows, pointer: null })).toHaveProperty("error");
  });

  it("the still is named \"<walkthrough> · step N\", within 120 characters", () => {
    expect(captureName("Your First Win", 3)).toBe("Your First Win · step 3");
    expect(captureName("   ", 1)).toBe("Walkthrough · step 1");
    const long = captureName("n".repeat(200), 12);
    expect(long.length).toBeLessThanOrEqual(120);
    expect(long.endsWith(" · step 12")).toBe(true);
  });
});

describe("the session", () => {
  it("walks the ids: advance on a capture or at Next with the counts, back one, the end", () => {
    let s = sessionOf(["b", "c"]);
    expect(currentId(s)).toBe("b");
    expect(remaining(s)).toBe(2);
    expect(atEnd(s)).toBe(false);
    expect([s.taken, s.byClick, s.byTyping]).toEqual([[], 0, 0]);
    s = advance(s, "captured");
    expect([currentId(s), s.captured, s.skipped, remaining(s)]).toEqual(["c", 1, 0, 1]);
    expect([s.taken, s.byClick, capturedSteps(s)]).toEqual([["b"], 0, 1]);
    s = advance(s, "next");
    expect([currentId(s), s.captured, s.skipped, atEnd(s), remaining(s)]).toEqual([null, 1, 1, true, 0]);
    expect(advance(s, "captured")).toBe(s);
    expect(advance(s, "next")).toBe(s);
    s = stepBack(s);
    expect([currentId(s), s.captured, s.skipped]).toEqual(["c", 1, 1]);
    s = stepBack(stepBack(s));
    expect(currentId(s)).toBe("b");
    expect(stepBack(s)).toBe(s);
    expect(atEnd(sessionOf([]))).toBe(true);
  });

  it("counts the DISTINCT steps that got a picture (a retake after Back is not a second step), the captures, and those by click or typing", () => {
    let s = sessionOf(["b", "c"]);
    s = advance(s, "captured", "click");
    expect([s.captured, s.byClick, s.byTyping, s.taken, capturedSteps(s)]).toEqual([1, 1, 0, ["b"], 1]);
    // Back to b and a second capture of it, by the key: two captures, one step pictured, one by click.
    s = advance(stepBack(s), "captured");
    expect([currentId(s), s.captured, s.byClick, s.taken, capturedSteps(s)]).toEqual(["c", 2, 1, ["b"], 1]);
    // And c by a typed pause: three captures over two steps, one by click, one typed.
    s = advance(s, "captured", "typing");
    expect([s.captured, s.byClick, s.byTyping, capturedSteps(s), s.taken]).toEqual([3, 1, 1, 2, ["b", "c"]]);
    // Next counts nothing of these - and on a step already pictured it skips nothing either (F5, R1): the step is not passed.
    const moved = advance(stepBack(s), "next");
    expect([moved.captured, moved.byClick, moved.skipped, capturedSteps(moved), moved.passed]).toEqual([3, 1, 0, 2, []]);
  });

  it("counts the DISTINCT steps skipped, and a step captured after a skip is not skipped (F5)", () => {
    let s = sessionOf(["b", "c"]);
    // Skip b, Back, skip b again: one skipped step, not two.
    s = advance(stepBack(advance(s, "next")), "next");
    expect([currentId(s), s.skipped, s.passed]).toEqual(["c", 1, ["b"]]);
    // Skip c too: two.
    s = advance(s, "next");
    expect([atEnd(s), s.skipped, s.passed]).toEqual([true, 2, ["b", "c"]]);
    // Back to c and capture it: c is pictured, so one skipped step is left (b).
    s = advance(stepBack(s), "captured");
    expect([s.skipped, s.captured, capturedSteps(s), s.taken, s.passed]).toEqual([1, 1, 1, ["c"], ["b", "c"]]);
    // Back twice to b and capture it: none skipped.
    s = advance(stepBack(stepBack(s)), "captured", "click");
    expect([s.skipped, s.captured, s.byClick, capturedSteps(s)]).toEqual([0, 2, 1, 2]);
  });

  it("a capture claims the current step and moves on at once; the landing is counted later, in its own call (F8)", () => {
    const s = sessionOf(["b", "c"]);
    const first = claimStep(s, "capture")!;
    expect([first.id, currentId(first.session), first.session.captured, first.session.taken]).toEqual(["b", "c", 0, []]);
    // A second claim while the first is in flight: the next step, from the moved-on session.
    const second = claimStep(first.session, "capture")!;
    expect([second.id, atEnd(second.session)]).toEqual(["c", true]);
    expect(claimStep(second.session, "capture")).toBeNull();
    // The landings, in click order, count once each; a retake of a pictured step counts a capture but not a step.
    let landed = recordCapture(second.session, "b", "click");
    landed = recordCapture(landed, "c");
    expect([landed.captured, landed.byClick, landed.taken, capturedSteps(landed), landed.skipped]).toEqual([2, 1, ["b", "c"], 2, 0]);
    expect(recordCapture(landed, "b").taken).toEqual(["b", "c"]);
    expect(recordCapture(landed, "b").captured).toBe(3);
    // advance(s, "captured") is the two in one.
    expect(advance(s, "captured", "click")).toEqual(recordCapture(first.session, "b", "click"));
    expect([MAX_IN_FLIGHT, TOO_FAST]).toEqual([2, "Too fast: wait for the still."]);
  });

  it("holds the step until Next (W2d, R1): a claim keeps the step, every capture lands on it, Next moves on and skips only a step with no picture", () => {
    let s = sessionOf(["b", "c"]);
    // The claim in "next" mode keeps `at`: the id is claimed at the moment of the capture, the session does not move on.
    const claimed = claimStep(s, "next")!;
    expect([claimed.id, currentId(claimed.session), claimed.session]).toEqual(["b", "b", s]);
    expect(claimStep(sessionOf([]), "next")).toBeNull();
    // Two captures land on the one step: two captures, one step, two stills this step; the button reads Next.
    expect([stillsThisStep(s), nextLabel(s)]).toEqual([0, "Skip"]);
    s = recordCapture(s, "b", "click");
    expect([s.captured, capturedSteps(s), stillsThisStep(s), nextLabel(s), currentId(s)]).toEqual([1, 1, 1, "Next", "b"]);
    s = recordCapture(s, "b", "typing");
    expect([s.captured, s.byClick, s.byTyping, s.taken, stillsThisStep(s), nextLabel(s)]).toEqual([2, 1, 1, ["b"], 2, "Next"]);
    // Next on a pictured step: moved on, nothing skipped, the step not passed; on c, with no still, the button reads Skip.
    s = advance(s, "next");
    expect([currentId(s), s.skipped, s.passed, stillsThisStep(s), nextLabel(s)]).toEqual(["c", 0, [], 0, "Skip"]);
    // Skip (Next with no still) passes the step; Back and a capture there un-skips it, and the label follows.
    s = advance(s, "next");
    expect([atEnd(s), s.skipped, s.passed]).toEqual([true, 1, ["c"]]);
    s = recordCapture(stepBack(s), "c");
    expect([s.skipped, s.passed, stillsThisStep(s), nextLabel(s)]).toEqual([0, ["c"], 1, "Next"]);
    // A still that lands after Next was pressed lands on the step it was taken for: the per-step counts follow the id, not `at`.
    const late = recordCapture(advance(s, "next"), "b", "click");
    expect([atEnd(late), late.landed, capturedSteps(late)]).toEqual([true, { b: 3, c: 1 }, 2]);
    // The label at the end: nothing to move on to.
    expect(nextLabel(late)).toBe("Next");
  });

  it("the bar is told the step's place in the WHOLE walkthrough, its text and instruction, what it may do, and what it got (W2d)", () => {
    const doc = { name: "Tour", steps: [step("a"), step("b", { image: null, width: 0, height: 0 }), step("c", { image: null, width: 0, height: 0, text: "" })] };
    const s = sessionOf(["b", "c"]);
    expect(barPayload("Tour", doc, s, "ready")).toEqual({
      mode: BAR_MODE, phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text b", instruction: "Do b", remaining: 2, canBack: false, canCapture: true,
      message: null, clicks: false, typing: false, stillsThisStep: 0, lastStill: null,
    });
    // A refusal the capture goes on under rides in the payload: the bar shows it under the step.
    expect(barPayload("Tour", doc, s, "ready", "Shift+F8 is taken by another program")).toMatchObject({ message: "Shift+F8 is taken by another program", canCapture: true });
    // Capture on click, and typing: the bar adds its second line from these.
    expect(barPayload("Tour", doc, s, "ready", null, true)).toMatchObject({ clicks: true, typing: false, message: null });
    expect(barPayload("Tour", doc, s, "ready", null, true, true)).toMatchObject({ clicks: true, typing: true });
    expect(barPayload("Tour", doc, advance(s, "captured"), "ready")).toMatchObject({ label: "Step 3 of 3", text: "(no text yet)", instruction: "Do c", remaining: 1, canBack: true, canCapture: true });
    expect(barPayload("Tour", doc, s, "capturing")).toMatchObject({ canCapture: false });
    expect(barPayload("Tour", doc, advance(advance(s, "captured"), "next"), "finished")).toMatchObject({ label: "All steps visited", instruction: "", remaining: 0, canBack: true, canCapture: false });
    // What the bar got (R4): the stills landed on the current step this run, and the last still of the run with its thumbnail.
    const twice = recordCapture(recordCapture(s, "b", "click"), "b", "typing");
    const last = { step: 2, thumb: "data:image/jpeg;base64,xyz" };
    expect(barPayload("Tour", doc, twice, "ready", null, true, true, last)).toMatchObject({ stillsThisStep: 2, lastStill: last, label: "Step 2 of 3" });
    expect(barPayload("Tour", doc, advance(twice, "next"), "ready", null, false, false, last)).toMatchObject({ stillsThisStep: 0, lastStill: last, label: "Step 3 of 3" });
    expect(BAR_MODE).toBe("steps");
    expect(KEYS_MODE).toBe("steps");
    expect(STEP_KEY_EVENT).toBe("capture:step-key");
    expect(STEP_NEXT_EVENT).toBe("capture:step-next");
    expect(STEP_TYPED_EVENT).toBe("capture:step-typed");
    expect([STEPS_BAR_WIDTH, STEPS_BAR_HEIGHT]).toEqual([760, 112]);
  });

  it("the bar's red line is the standing refusals and one capture's transient refusal, joined; none for none", () => {
    expect(barMessage(null, null)).toBeNull();
    expect(barMessage("Shift+F8 is taken.", null)).toBe("Shift+F8 is taken.");
    expect(barMessage(null, "No window is under the pointer.")).toBe("No window is under the pointer.");
    expect(barMessage("Shift+F8 is taken.", "No window is under the pointer.")).toBe("Shift+F8 is taken. No window is under the pointer.");
    expect(barMessage("", "")).toBeNull();
  });

  it("Shift+F8 alone is the capture key and Shift+F9 alone is Next; the shell's payload carries the pointer, anything else is no pointer", () => {
    expect(guidedHotkey({ key: "F8", shiftKey: true })).toBe(true);
    expect(guidedHotkey({ key: "F8", shiftKey: false })).toBe(false);
    expect(guidedHotkey({ key: "F8", shiftKey: true, ctrlKey: true })).toBe(false);
    expect(guidedHotkey({ key: "F9", shiftKey: true })).toBe(false);
    // Shift+F9 (W2d): Next - the page's own when the builder has the focus, as the shell's global one.
    expect(nextHotkey({ key: "F9", shiftKey: true })).toBe(true);
    expect(nextHotkey({ key: "F9", shiftKey: false })).toBe(false);
    expect(nextHotkey({ key: "F9", shiftKey: true, altKey: true })).toBe(false);
    expect(nextHotkey({ key: "F8", shiftKey: true })).toBe(false);
    expect(pointerOf({ x: 12.4, y: 7.6 })).toEqual({ x: 12, y: 8 });
    expect(pointerOf("capture")).toBeNull();
    expect(pointerOf(null)).toBeNull();
    expect(pointerOf({ x: "1", y: 2 })).toBeNull();
  });

  describe("the delay before the still after a click (W2d, R2)", () => {
    afterEach(() => vi.useRealTimers());

    it("resolves after the settle time and not before; SETTLE_MS is half a second", async () => {
      vi.useFakeTimers();
      let done = false;
      void delay(SETTLE_MS).then(() => { done = true; });
      await vi.advanceTimersByTimeAsync(SETTLE_MS - 1);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      expect(done).toBe(true);
      expect(SETTLE_MS).toBe(500);
    });
  });
});

describe("a click (W2b)", () => {
  it("the shell's click carries the point and the button, left or right; anything else is no click", () => {
    expect(STEP_CLICK_EVENT).toBe("capture:step-click");
    expect(clickOf({ x: 900, y: 700, button: "left" })).toEqual({ point: { x: 900, y: 700 }, button: "left" });
    expect(clickOf({ x: -1920.4, y: 42, button: "right" })).toEqual({ point: { x: -1920, y: 42 }, button: "right" });
    expect(clickOf({ x: 1, y: 2 })).toBeNull();
    expect(clickOf({ x: 1, y: 2, button: "middle" })).toBeNull();
    expect(clickOf({ x: "1", y: 2, button: "left" })).toBeNull();
    expect(clickOf(null)).toBeNull();
    expect(clickOf("click")).toBeNull();
  });

  it("a click outside the kind's area - a Region's frame, a Full screen's chosen monitor (W2b-2) - is no step; inside it, and anywhere for a Window, a capture", () => {
    const region = { x: 100, y: 100, width: 800, height: 600, monitor: 0, window_title: "Spoon", hwnd: 42 };
    expect(insideFrame({ x: 100, y: 100 }, region)).toBe(true);
    expect(insideFrame({ x: 899, y: 699 }, region)).toBe(true);
    expect(insideFrame({ x: 900, y: 400 }, region)).toBe(false);
    expect(insideFrame({ x: 400, y: 700 }, region)).toBe(false);
    expect(insideFrame({ x: 99, y: 400 }, region)).toBe(false);
    // The area per kind: the region; the chosen monitor (the primary when none was chosen); none for a window.
    expect(clickArea("region", region, monitors, null)).toBe(region);
    expect(clickArea("screen", region, monitors, 1)).toBe(monitors[1]);
    expect(clickArea("screen", null, monitors, null)).toBe(monitors[0]);
    expect(clickArea("screen", null, [], null)).toBeNull();
    expect(clickArea("window", region, monitors, 1)).toBeNull();
    // Region: inside the frame a step, outside none.
    expect(clickRule("region", region, { x: 500, y: 400 })).toBe("capture");
    expect(clickRule("region", region, { x: 50, y: 50 })).toBe("outside");
    expect(clickRule("region", region, { x: 900, y: 100 })).toBe("outside");
    // No region yet (none chosen): the capture path refuses it with its own line, not this rule.
    expect(clickRule("region", null, { x: 50, y: 50 })).toBe("capture");
    // Full screen on monitor B (1920..4480 x 0..1440): a press on it is a step - another app's included - off it none.
    expect(clickRule("screen", monitors[1], { x: 3000, y: 700 })).toBe("capture");
    expect(clickRule("screen", monitors[1], { x: 1920, y: 0 })).toBe("capture");
    expect(clickRule("screen", monitors[1], { x: 1919, y: 700 })).toBe("outside");
    expect(clickRule("screen", monitors[1], { x: 4480, y: 700 })).toBe("outside");
    expect(clickRule("screen", monitors[0], { x: 3000, y: 700 })).toBe("outside");
    expect(clickRule("screen", null, { x: 5000, y: 50 })).toBe("capture");
    // A window: as the key - the window under the point, with the hotspot rule.
    expect(clickRule("window", null, { x: 50, y: 50 })).toBe("capture");
    expect(clickRule("window", region, { x: 50, y: 50 })).toBe("capture");
    // The bar's line per kind.
    expect(outsideLine("region")).toBe(CLICK_OUTSIDE_FRAME);
    expect(outsideLine("screen")).toBe(CLICK_OUTSIDE_SCREEN);
    expect(CLICK_OUTSIDE_FRAME).toBe("Click inside the frame to capture this step.");
    expect(CLICK_OUTSIDE_SCREEN).toBe("Click on the captured screen to capture this step.");
    expect(CLICK_HINT).toBe("Click to capture · Shift+F8 captures without a click");
    expect(CLICKS_OFF).toContain("Shift+F8");
  });

  it("a typed pause has a where (F1): the foreground window's frame must meet a Region's frame or the Full screen's monitor; the Window kind takes that window; the hotspot is the last click when in the picture, else the pointer when in it, else none", () => {
    // The shell's payload: the pointer and the foreground window's frame, or null for either; an older shell's bare pointer has no window.
    expect(typedOf({ x: 350.4, y: 360, window: { x: 300, y: 300, width: 400, height: 200 } })).toEqual({ point: { x: 350, y: 360 }, window: { x: 300, y: 300, width: 400, height: 200 } });
    expect(typedOf({ x: 1, y: 2, window: null })).toEqual({ point: { x: 1, y: 2 }, window: null });
    expect(typedOf({ x: 1, y: 2 })).toEqual({ point: { x: 1, y: 2 }, window: null });
    expect(typedOf({ x: 1, y: 2, window: { x: 0, y: 0, width: 0, height: 10 } })).toEqual({ point: { x: 1, y: 2 }, window: null });
    expect(typedOf({ x: 1, y: 2, window: { x: "0", y: 0, width: 10, height: 10 } })).toEqual({ point: { x: 1, y: 2 }, window: null });
    expect(typedOf(null)).toEqual({ point: null, window: null });
    expect(typedOf("typed")).toEqual({ point: null, window: null });
    // Two rectangles meet when they share a pixel: half-open on both axes.
    const region = { x: 100, y: 100, width: 800, height: 600 };
    expect(rectsIntersect(region, { x: 850, y: 650, width: 400, height: 200 })).toBe(true);
    expect(rectsIntersect(region, { x: 900, y: 100, width: 10, height: 10 })).toBe(false);
    expect(rectsIntersect(region, { x: 899, y: 699, width: 10, height: 10 })).toBe(true);
    expect(rectsIntersect(region, { x: 0, y: 0, width: 100, height: 100 })).toBe(false);
    expect(rectsIntersect(region, { x: 0, y: 0, width: 101, height: 101 })).toBe(true);
    expect(rectsIntersect(region, { x: 0, y: 0, width: 5000, height: 5000 })).toBe(true);
    // Region: a window off the frame is no still; one that meets it, a still; no window known: the still as before (the pointer rule).
    expect(typedRule("region", region, { x: 1000, y: 100, width: 300, height: 300 })).toBe("outside");
    expect(typedRule("region", region, { x: 850, y: 650, width: 300, height: 300 })).toBe("capture");
    expect(typedRule("region", region, null)).toBe("capture");
    expect(typedRule("region", null, { x: 1000, y: 100, width: 300, height: 300 })).toBe("capture");
    // Full screen: the chosen monitor; a window on another monitor is off it.
    expect(typedRule("screen", monitors[1], { x: 100, y: 100, width: 300, height: 300 })).toBe("outside");
    expect(typedRule("screen", monitors[1], { x: 1800, y: 100, width: 300, height: 300 })).toBe("capture");
    expect(typedRule("screen", monitors[0], null)).toBe("capture");
    // Window (G1): the area is the rectangle of the step's LAST STILL this run - a window off it takes no still, one that meets
    // it takes the still (of the window typed into); a step with no still yet this run has no area, and the window typed
    // into is taken; no window known is the still as before.
    const lastStill = { x: 100, y: 100, width: 1200, height: 800 };
    expect(typedRule("window", lastStill, { x: 1500, y: 100, width: 300, height: 300 })).toBe("outside");
    expect(typedRule("window", lastStill, { x: 300, y: 300, width: 400, height: 200 })).toBe("capture");
    expect(typedRule("window", lastStill, null)).toBe("capture");
    expect(typedRule("window", null, { x: 1500, y: 100, width: 300, height: 300 })).toBe("capture");
    expect(typedRule("window", region, null)).toBe("capture");
    // The area per kind for a typed pause: the step's last still for a Window, the click area (the frame, the monitor) otherwise.
    expect(typedArea("window", null, lastStill)).toBe(lastStill);
    expect(typedArea("window", null, null)).toBeNull();
    expect(typedArea("region", region, lastStill)).toBe(region);
    expect(typedArea("screen", monitors[0], lastStill)).toBe(monitors[0]);
    expect(typedOutsideLine("region")).toBe(TYPED_OUTSIDE_FRAME);
    expect(typedOutsideLine("screen")).toBe(TYPED_OUTSIDE_SCREEN);
    expect(typedOutsideLine("window")).toBe(TYPED_OUTSIDE_WINDOW);
    expect(TYPED_OUTSIDE_FRAME).toBe("Typing outside the frame takes no still.");
    expect(TYPED_OUTSIDE_SCREEN).toBe("Typing off the captured screen takes no still.");
    expect(TYPED_OUTSIDE_WINDOW).toBe("Typing outside the step's window takes no still.");
    // A typed pause that fires within the pause of Next (G2) belongs to the step just left: ignored. Never pressed: never too soon.
    expect(TYPING_PAUSE_MS).toBe(1000);
    expect(typedAfterNext(5000, 5999)).toBe(true);
    expect(typedAfterNext(5000, 6000)).toBe(false);
    expect(typedAfterNext(5000, 5000)).toBe(true);
    expect(typedAfterNext(null, 5)).toBe(false);
    // The hotspot of a typed still: the step's last click when it lies inside the captured rectangle, else the pointer when inside, else none.
    const rect = { x: 300, y: 300, width: 400, height: 200 };
    expect(typedHotspotPoint({ x: 350, y: 350 }, { x: 900, y: 700 }, rect)).toEqual({ x: 350, y: 350 });
    expect(typedHotspotPoint({ x: 900, y: 700 }, { x: 350, y: 350 }, rect)).toEqual({ x: 350, y: 350 });
    expect(typedHotspotPoint(null, { x: 699, y: 499 }, rect)).toEqual({ x: 699, y: 499 });
    expect(typedHotspotPoint({ x: 900, y: 700 }, { x: 700, y: 500 }, rect)).toBeNull();
    expect(typedHotspotPoint(null, null, rect)).toBeNull();
    expect(typedHotspotPoint({ x: 300, y: 300 }, null, rect)).toEqual({ x: 300, y: 300 });
  });

  it("a step left without a picture beside a later still is named by a standing line (G2)", () => {
    expect(noPictureLine(6)).toBe("Step 6 has no picture: press Back to take it again.");
    expect(keptPictureLine(6)).toBe("Step 6 kept its old picture: press Back to take it again.");
    expect(missedLine([])).toBeNull();
    expect(missedLine([{ number: 6, kept: false }])).toBe("Step 6 has no picture: press Back to take it again.");
    expect(missedLine([{ number: 6, kept: false }, { number: 2, kept: false }])).toBe(
      "Step 2 has no picture: press Back to take it again. Step 6 has no picture: press Back to take it again.",
    );
    // "Every step" (H2): a step that had a picture keeps it when its new still fails - the line says so.
    expect(missedLine([{ number: 6, kept: true }, { number: 2, kept: false }])).toBe(
      "Step 2 has no picture: press Back to take it again. Step 6 kept its old picture: press Back to take it again.",
    );
  });

  it("any shell's answer to capture_hotkeys_start is read as {key, next, clicks, typing}, and the start says exactly which is off (H1, W2d, F3)", () => {
    // A shell since the fix round: {key, next, clicks, typing} as it is; one since W2d (no `next`): Shift+F9 follows Shift+F8 (one set then);
    // one since H1 (no typing field): the typing off.
    expect(keysUpOf({ key: true, next: true, clicks: true, typing: true }, false)).toEqual({ key: true, next: true, clicks: true, typing: true });
    expect(keysUpOf({ key: true, next: false, clicks: true, typing: false }, false)).toEqual({ key: true, next: false, clicks: true, typing: false });
    expect(keysUpOf({ key: false, next: true, clicks: false, typing: false }, false)).toEqual({ key: false, next: true, clicks: false, typing: false });
    expect(keysUpOf({ key: true, clicks: true, typing: true }, false)).toEqual({ key: true, next: true, clicks: true, typing: true });
    expect(keysUpOf({ key: false, clicks: true, typing: false }, false)).toEqual({ key: false, next: false, clicks: true, typing: false });
    expect(keysUpOf({ key: true, clicks: true }, false)).toEqual({ key: true, next: true, clicks: true, typing: false });
    expect(keysUpOf({ key: false, clicks: true }, false)).toEqual({ key: false, next: false, clicks: true, typing: false });
    expect(keysUpOf({ key: true, clicks: false }, false)).toEqual({ key: true, next: true, clicks: false, typing: false });
    expect(keysUpOf({ key: false, clicks: false }, false)).toEqual({ key: false, next: false, clicks: false, typing: false });
    // The W2b shell before H1: a boolean (true everything asked up; false the key up, the hook refused).
    expect(keysUpOf(true, false)).toEqual({ key: true, next: true, clicks: true, typing: false });
    expect(keysUpOf(false, false)).toEqual({ key: true, next: true, clicks: false, typing: false });
    // The 0.17.0 shell answers nothing and has no hook at all: the key up, the clicks and the typing off.
    expect(keysUpOf(undefined, false)).toEqual({ key: true, next: true, clicks: false, typing: false });
    expect(keysUpOf(null, false)).toEqual({ key: true, next: true, clicks: false, typing: false });
    // A refusal (a shell before H1 with the key taken, which installed no hook either; or the other set held): all off.
    expect(keysUpOf(undefined, true)).toEqual({ key: false, next: false, clicks: false, typing: false });
    // The outcomes with the clicks ticked (typing not asked): the lines, and whether the clicks are live (the payload's `clicks`).
    const up = (key: boolean, clicks: boolean, typing = false, next = key) => ({ key, next, clicks, typing });
    expect(keysLines(up(true, true), true)).toEqual({ clicks: true, typing: false, lines: [] });
    expect(keysLines(up(false, true, false, true), true)).toEqual({ clicks: true, typing: false, lines: [KEY_TAKEN_CLICK] });
    expect(keysLines(up(true, false), true)).toEqual({ clicks: false, typing: false, lines: [CLICKS_OFF] });
    // Shift+F8 taken AND the clicks off with the tick on: ONE line naming both, never the two contradictory ones (each pointed at the other's way).
    expect(keysLines(up(false, false, false, true), true)).toEqual({ clicks: false, typing: false, lines: [KEYS_AND_CLICKS_OFF] });
    expect(KEYS_AND_CLICKS_OFF).toBe(
      "Shift+F8 is taken by another program and clicks could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).",
    );
    // The tick off: never the clicks, and the key-only wording for a taken Shift+F8 (F3: each key is named on its own again).
    expect(keysLines(up(true, false), false)).toEqual({ clicks: false, typing: false, lines: [] });
    expect(keysLines(up(false, false, false, true), false)).toEqual({ clicks: false, typing: false, lines: [KEY_TAKEN] });
    expect(keysLines(up(true, true), false)).toEqual({ clicks: false, typing: false, lines: [] });
    expect(KEY_TAKEN_CLICK).toBe("Shift+F8 is taken by another program: click to capture, or use Capture on the bar.");
    expect(KEY_TAKEN).toBe("Shift+F8 is taken by another program: use Capture on the bar (a capture from the bar has no hotspot).");
    // Shift+F9 taken on its own (F3): its own line; both keys taken: one line naming both, by whether the clicks are live.
    expect(keysLines(up(true, true, false, false), true)).toEqual({ clicks: true, typing: false, lines: [NEXT_KEY_TAKEN] });
    expect(keysLines(up(true, false, false, false), false)).toEqual({ clicks: false, typing: false, lines: [NEXT_KEY_TAKEN] });
    expect(keysLines(up(false, true, false, false), true)).toEqual({ clicks: true, typing: false, lines: [KEYS_TAKEN_CLICK] });
    expect(keysLines(up(false, false, false, false), false)).toEqual({ clicks: false, typing: false, lines: [KEYS_TAKEN] });
    // Shift+F9 taken beside the clicks off: two lines that do not contradict (Next on the bar; capture with Shift+F8 or the bar's Capture).
    expect(keysLines(up(true, false, false, false), true)).toEqual({ clicks: false, typing: false, lines: [NEXT_KEY_TAKEN, CLICKS_OFF] });
    // Both keys taken and the clicks off: the one line for Shift+F8 and the clicks, then Shift+F9's.
    expect(keysLines(up(false, false, false, false), true)).toEqual({ clicks: false, typing: false, lines: [KEYS_AND_CLICKS_OFF, NEXT_KEY_TAKEN] });
    expect(NEXT_KEY_TAKEN).toBe("Shift+F9 is taken by another program: use Next on the bar.");
    expect(KEYS_TAKEN).toBe("Shift+F8 and Shift+F9 are taken by another program: use Capture and Next on the bar (a capture from the bar has no hotspot).");
    expect(KEYS_TAKEN_CLICK).toBe("Shift+F8 and Shift+F9 are taken by another program: click to capture, or use Capture and Next on the bar.");
    // Typing asked (W2d, R3): live only when asked AND up; TYPING_OFF when asked and not up; both hooks off = ONE line; with
    // Shift+F8 taken too, one line naming all three.
    expect(keysLines(up(true, true, true), true, true)).toEqual({ clicks: true, typing: true, lines: [] });
    expect(keysLines(up(true, true, true), true, false)).toEqual({ clicks: true, typing: false, lines: [] });
    expect(keysLines(up(true, true, false), true, true)).toEqual({ clicks: true, typing: false, lines: [TYPING_OFF] });
    expect(keysLines(up(true, false, false), true, true)).toEqual({ clicks: false, typing: false, lines: [HOOKS_OFF] });
    expect(keysLines(up(true, false, true), true, true)).toEqual({ clicks: false, typing: true, lines: [CLICKS_OFF] });
    // Shift+F8 taken beside the typing off (G5): the typing line never says "press Shift+F8" next to a line that says it is
    // taken - the four rows of the matrix: Shift+F8 or both keys taken, the clicks live or not asked.
    expect(keysLines(up(false, true, false, true), true, true)).toEqual({ clicks: true, typing: false, lines: [KEY_TAKEN_CLICK, TYPING_OFF_CLICK] });
    expect(keysLines(up(false, true, false, true), false, true)).toEqual({ clicks: false, typing: false, lines: [KEY_TAKEN, TYPING_OFF_BAR] });
    expect(keysLines(up(false, true, false, false), true, true)).toEqual({ clicks: true, typing: false, lines: [KEYS_TAKEN_CLICK, TYPING_OFF_CLICK] });
    expect(keysLines(up(false, false, false, false), false, true)).toEqual({ clicks: false, typing: false, lines: [KEYS_TAKEN, TYPING_OFF_BAR] });
    expect(TYPING_OFF_CLICK).toBe("Typing could not be captured on this computer: click, or use the bar's Capture for a typed step.");
    expect(TYPING_OFF_BAR).toBe("Typing could not be captured on this computer: use the bar's Capture for a typed step.");
    expect(keysLines(up(false, false, false, true), true, true)).toEqual({ clicks: false, typing: false, lines: [KEYS_AND_HOOKS_OFF] });
    expect(keysLines(up(false, false, true, true), true, true)).toEqual({ clicks: false, typing: true, lines: [KEYS_AND_CLICKS_OFF] });
    expect(keysLines(up(false, false, false, false), true, true)).toEqual({ clicks: false, typing: false, lines: [KEYS_AND_HOOKS_OFF, NEXT_KEY_TAKEN] });
    expect(keysLines(up(true, false, false), false, true)).toEqual({ clicks: false, typing: false, lines: [TYPING_OFF] });
    // Shift+F9 alone taken with the typing off: Shift+F8 is up, so the typing line may still say "press Shift+F8".
    expect(keysLines(up(true, true, false, false), true, true)).toEqual({ clicks: true, typing: false, lines: [NEXT_KEY_TAKEN, TYPING_OFF] });
    expect(TYPING_OFF).toBe("Typing could not be captured on this computer: click, press Shift+F8 or use the bar's Capture for a typed step.");
    expect(HOOKS_OFF).toBe("Clicks and typing could not be captured on this computer: capture with Shift+F8 or the bar's Capture.");
    expect(KEYS_AND_HOOKS_OFF).toBe(
      "Shift+F8 is taken by another program and clicks and typing could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).",
    );
    // The bar's hint line by what is live: the click hint gains the typing clause; typing alone names the key; neither, nothing.
    expect(hintLine(true, false)).toBe(CLICK_HINT);
    expect(hintLine(true, true)).toBe("Click to capture · Shift+F8 captures without a click · typing captures a second after you stop");
    expect(hintLine(false, true)).toBe("Shift+F8 captures · typing captures a second after you stop");
    expect(hintLine(false, false)).toBe("");
    expect(TYPING_HINT).toBe("typing captures a second after you stop");
  });

  it("the run's frame is the widest width × the tallest height of the stills so far: it converges whatever the order (R7, F7, G5); other kinds keep none", () => {
    const spoon = { width: 1200, height: 800 }, dialog = { width: 400, height: 200 }, maximised = { width: 1920, height: 1080 };
    const wide = { width: 1600, height: 600 }, tall = { width: 1000, height: 1000 };
    // The first still sets the frame and is sent as it is.
    expect(frameFor("window", null, spoon)).toEqual({ frame: spoon, send: null });
    // The same size again: nothing to draw.
    expect(frameFor("window", spoon, { width: 1200, height: 800 })).toEqual({ frame: spoon, send: null });
    // A smaller still: drawn on the frame by the server; the frame stays.
    expect(frameFor("window", spoon, dialog)).toEqual({ frame: spoon, send: spoon });
    // A LARGER still on both sides is never scaled down: attached at its own size, and the frame from here on (F7).
    expect(frameFor("window", spoon, maximised)).toEqual({ frame: maximised, send: null });
    expect(frameFor("window", maximised, spoon)).toEqual({ frame: maximised, send: maximised });
    // Wider but shorter (G5): the frame grows on the width only, and the still is drawn on the NEW frame.
    expect(frameFor("window", spoon, wide)).toEqual({ frame: { width: 1600, height: 800 }, send: { width: 1600, height: 800 } });
    // Then taller but narrower: the frame grows on the height; the run has converged on 1600 x 1000.
    expect(frameFor("window", { width: 1600, height: 800 }, tall)).toEqual({ frame: { width: 1600, height: 1000 }, send: { width: 1600, height: 1000 } });
    // Whatever the order, the same final frame - the sizes need not nest.
    const run = (sizes: { width: number; height: number }[]) => sizes.reduce<{ width: number; height: number } | null>((f, s) => frameFor("window", f, s).frame, null);
    expect(run([spoon, wide, tall])).toEqual({ width: 1600, height: 1000 });
    expect(run([tall, wide, spoon])).toEqual({ width: 1600, height: 1000 });
    expect(run([wide, tall, dialog, spoon])).toEqual({ width: 1600, height: 1000 });
    // A run that starts on a dialog converges on the main window: dialog, Spoon (becomes the frame), dialog (drawn on Spoon's).
    const afterDialog = frameFor("window", null, dialog).frame;
    const afterSpoon = frameFor("window", afterDialog, spoon);
    expect(afterSpoon).toEqual({ frame: spoon, send: null });
    expect(frameFor("window", afterSpoon.frame, dialog)).toEqual({ frame: spoon, send: spoon });
    // A region and a monitor have one size already.
    expect(frameFor("region", null, dialog)).toEqual({ frame: null, send: null });
    expect(frameFor("screen", spoon, dialog)).toEqual({ frame: null, send: null });
  });
});
