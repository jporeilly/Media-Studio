/**
 * Guided capture (W2), the pure half: the hotspot round the pointer in the picture's own pixels (the origin
 * subtracted, clamped at the edges, none outside), the window under the pointer, what each kind captures, the
 * still's name, the session's walk (advance, skip, back, the end), the bar's payload, the key, the constants - and
 * (W2b) the click: its payload, the frame rule per kind, the distinct-steps count, the bar's lines.
 */
import { describe, expect, it } from "vitest";
import type { MonitorInfo, WindowInfo } from "./capture";
import {
  BAR_MODE, CLICKS_OFF, CLICK_HINT, CLICK_OUTSIDE_FRAME, CLICK_OUTSIDE_SCREEN, HOTSPOT_DEFAULT, KEYS_MODE, MAX_IN_FLIGHT, NO_WINDOW_UNDER_POINTER,
  NO_WINDOW_UNDER_POINTER_CLICK, STEPS_BAR_HEIGHT, STEPS_BAR_WIDTH, STEP_CLICK_EVENT, STEP_KEY_EVENT, TOO_FAST,
  advance, atEnd, barMessage, barPayload, captureName, captureTarget, capturedSteps, claimStep, clickArea, clickOf, clickRule, currentId, frameFor,
  guidedHotkey, hotspotAround, hotspotOf, insideFrame, keptPictureLine, keysLines, keysUpOf, missedLine, noPictureLine, noWindowLine, outsideLine,
  pointerOf, recordCapture, remaining, sessionOf, stepBack, windowUnder, KEYS_AND_CLICKS_OFF, KEY_TAKEN, KEY_TAKEN_CLICK,
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
  it("walks the ids: advance on a capture or a skip with the counts, back one, the end", () => {
    let s = sessionOf(["b", "c"]);
    expect(currentId(s)).toBe("b");
    expect(remaining(s)).toBe(2);
    expect(atEnd(s)).toBe(false);
    expect([s.taken, s.byClick]).toEqual([[], 0]);
    s = advance(s, "captured");
    expect([currentId(s), s.captured, s.skipped, remaining(s)]).toEqual(["c", 1, 0, 1]);
    expect([s.taken, s.byClick, capturedSteps(s)]).toEqual([["b"], 0, 1]);
    s = advance(s, "skipped");
    expect([currentId(s), s.captured, s.skipped, atEnd(s), remaining(s)]).toEqual([null, 1, 1, true, 0]);
    expect(advance(s, "captured")).toBe(s);
    s = stepBack(s);
    expect([currentId(s), s.captured, s.skipped]).toEqual(["c", 1, 1]);
    s = stepBack(stepBack(s));
    expect(currentId(s)).toBe("b");
    expect(stepBack(s)).toBe(s);
    expect(atEnd(sessionOf([]))).toBe(true);
  });

  it("counts the DISTINCT steps that got a picture (a retake after Back is not a second step), the captures, and those by click", () => {
    let s = sessionOf(["b", "c"]);
    s = advance(s, "captured", true);
    expect([s.captured, s.byClick, s.taken, capturedSteps(s)]).toEqual([1, 1, ["b"], 1]);
    // Back to b and a second capture of it, by the key: two captures, one step pictured, one by click.
    s = advance(stepBack(s), "captured");
    expect([currentId(s), s.captured, s.byClick, s.taken, capturedSteps(s)]).toEqual(["c", 2, 1, ["b"], 1]);
    // And c by a click: three captures over two steps, two of them by click.
    s = advance(s, "captured", true);
    expect([s.captured, s.byClick, capturedSteps(s), s.taken]).toEqual([3, 2, 2, ["b", "c"]]);
    // A skip counts nothing of these - and of a step already pictured it counts no skip either (F5).
    const skipped = advance(stepBack(s), "skipped", true);
    expect([skipped.captured, skipped.byClick, skipped.skipped, capturedSteps(skipped), skipped.passed]).toEqual([3, 2, 0, 2, ["c"]]);
  });

  it("counts the DISTINCT steps skipped, and a step captured after a skip is not skipped (F5)", () => {
    let s = sessionOf(["b", "c"]);
    // Skip b, Back, skip b again: one skipped step, not two.
    s = advance(stepBack(advance(s, "skipped")), "skipped");
    expect([currentId(s), s.skipped, s.passed]).toEqual(["c", 1, ["b"]]);
    // Skip c too: two.
    s = advance(s, "skipped");
    expect([atEnd(s), s.skipped, s.passed]).toEqual([true, 2, ["b", "c"]]);
    // Back to c and capture it: c is pictured, so one skipped step is left (b).
    s = advance(stepBack(s), "captured");
    expect([s.skipped, s.captured, capturedSteps(s), s.taken, s.passed]).toEqual([1, 1, 1, ["c"], ["b", "c"]]);
    // Back twice to b and capture it: none skipped.
    s = advance(stepBack(stepBack(s)), "captured", true);
    expect([s.skipped, s.captured, s.byClick, capturedSteps(s)]).toEqual([0, 2, 1, 2]);
  });

  it("a capture claims the current step and moves on at once; the landing is counted later, in its own call (F8)", () => {
    const s = sessionOf(["b", "c"]);
    const first = claimStep(s)!;
    expect([first.id, currentId(first.session), first.session.captured, first.session.taken]).toEqual(["b", "c", 0, []]);
    // A second claim while the first is in flight: the next step, from the moved-on session.
    const second = claimStep(first.session)!;
    expect([second.id, atEnd(second.session)]).toEqual(["c", true]);
    expect(claimStep(second.session)).toBeNull();
    // The landings, in click order, count once each; a retake of a pictured step counts a capture but not a step.
    let landed = recordCapture(second.session, "b", true);
    landed = recordCapture(landed, "c");
    expect([landed.captured, landed.byClick, landed.taken, capturedSteps(landed), landed.skipped]).toEqual([2, 1, ["b", "c"], 2, 0]);
    expect(recordCapture(landed, "b").taken).toEqual(["b", "c"]);
    expect(recordCapture(landed, "b").captured).toBe(3);
    // advance(s, "captured") is the two in one.
    expect(advance(s, "captured", true)).toEqual(recordCapture(first.session, "b", true));
    expect([MAX_IN_FLIGHT, TOO_FAST]).toEqual([2, "Too fast: wait for the still."]);
  });

  it("the bar is told the step's place in the WHOLE walkthrough, its text and instruction, and what it may do", () => {
    const doc = { name: "Tour", steps: [step("a"), step("b", { image: null, width: 0, height: 0 }), step("c", { image: null, width: 0, height: 0, text: "" })] };
    const s = sessionOf(["b", "c"]);
    expect(barPayload("Tour", doc, s, "ready")).toEqual({
      mode: BAR_MODE, phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text b", instruction: "Do b", remaining: 2, canBack: false, canCapture: true,
      message: null, clicks: false,
    });
    // A refusal the capture goes on under rides in the payload: the bar shows it under the step.
    expect(barPayload("Tour", doc, s, "ready", "Shift+F8 is taken by another program")).toMatchObject({ message: "Shift+F8 is taken by another program", canCapture: true });
    // Capture on click: the bar adds its second line.
    expect(barPayload("Tour", doc, s, "ready", null, true)).toMatchObject({ clicks: true, message: null });
    expect(barPayload("Tour", doc, advance(s, "captured"), "ready")).toMatchObject({ label: "Step 3 of 3", text: "(no text yet)", instruction: "Do c", remaining: 1, canBack: true, canCapture: true });
    expect(barPayload("Tour", doc, s, "capturing")).toMatchObject({ canCapture: false });
    expect(barPayload("Tour", doc, advance(advance(s, "captured"), "skipped"), "finished")).toMatchObject({ label: "All steps visited", instruction: "", remaining: 0, canBack: true, canCapture: false });
    expect(BAR_MODE).toBe("steps");
    expect(KEYS_MODE).toBe("steps");
    expect(STEP_KEY_EVENT).toBe("capture:step-key");
    expect([STEPS_BAR_WIDTH, STEPS_BAR_HEIGHT]).toEqual([600, 112]);
  });

  it("the bar's red line is the standing refusals and one capture's transient refusal, joined; none for none", () => {
    expect(barMessage(null, null)).toBeNull();
    expect(barMessage("Shift+F8 is taken.", null)).toBe("Shift+F8 is taken.");
    expect(barMessage(null, "No window is under the pointer.")).toBe("No window is under the pointer.");
    expect(barMessage("Shift+F8 is taken.", "No window is under the pointer.")).toBe("Shift+F8 is taken. No window is under the pointer.");
    expect(barMessage("", "")).toBeNull();
  });

  it("Shift+F8 alone is the capture key; the shell's payload carries the pointer, anything else is no pointer", () => {
    expect(guidedHotkey({ key: "F8", shiftKey: true })).toBe(true);
    expect(guidedHotkey({ key: "F8", shiftKey: false })).toBe(false);
    expect(guidedHotkey({ key: "F8", shiftKey: true, ctrlKey: true })).toBe(false);
    expect(guidedHotkey({ key: "F9", shiftKey: true })).toBe(false);
    expect(pointerOf({ x: 12.4, y: 7.6 })).toEqual({ x: 12, y: 8 });
    expect(pointerOf("capture")).toBeNull();
    expect(pointerOf(null)).toBeNull();
    expect(pointerOf({ x: "1", y: 2 })).toBeNull();
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

  it("any shell's answer to capture_hotkeys_start is read as {key, clicks}, and the start says exactly which is off (H1)", () => {
    // A shell since H1: {key, clicks} as it is.
    expect(keysUpOf({ key: true, clicks: true }, false)).toEqual({ key: true, clicks: true });
    expect(keysUpOf({ key: false, clicks: true }, false)).toEqual({ key: false, clicks: true });
    expect(keysUpOf({ key: true, clicks: false }, false)).toEqual({ key: true, clicks: false });
    expect(keysUpOf({ key: false, clicks: false }, false)).toEqual({ key: false, clicks: false });
    // The W2b shell before H1: a boolean (true everything asked up; false the key up, the hook refused).
    expect(keysUpOf(true, false)).toEqual({ key: true, clicks: true });
    expect(keysUpOf(false, false)).toEqual({ key: true, clicks: false });
    // The 0.17.0 shell answers nothing and has no click hook at all: the key up, the clicks off.
    expect(keysUpOf(undefined, false)).toEqual({ key: true, clicks: false });
    expect(keysUpOf(null, false)).toEqual({ key: true, clicks: false });
    // A refusal (a shell before H1 with the key taken, which installed no hook either; or the other set held): both off.
    expect(keysUpOf(undefined, true)).toEqual({ key: false, clicks: false });
    // The four outcomes with the tick on: the lines, and whether the clicks are live (the payload's `clicks`).
    expect(keysLines({ key: true, clicks: true }, true)).toEqual({ clicks: true, lines: [] });
    expect(keysLines({ key: false, clicks: true }, true)).toEqual({ clicks: true, lines: [KEY_TAKEN_CLICK] });
    expect(keysLines({ key: true, clicks: false }, true)).toEqual({ clicks: false, lines: [CLICKS_OFF] });
    // Both off with the tick on: ONE line naming both, never the two contradictory ones (each pointed at the other's way).
    expect(keysLines({ key: false, clicks: false }, true)).toEqual({ clicks: false, lines: [KEYS_AND_CLICKS_OFF] });
    expect(KEYS_AND_CLICKS_OFF).toBe(
      "Shift+F8 is taken by another program and clicks could not be captured on this computer: use Capture on the bar (a capture from the bar has no hotspot).",
    );
    // The tick off: never the clicks, and the key-only wording for a taken key.
    expect(keysLines({ key: true, clicks: false }, false)).toEqual({ clicks: false, lines: [] });
    expect(keysLines({ key: false, clicks: false }, false)).toEqual({ clicks: false, lines: [KEY_TAKEN] });
    expect(keysLines({ key: true, clicks: true }, false)).toEqual({ clicks: false, lines: [] });
    expect(KEY_TAKEN_CLICK).toBe("Shift+F8 is taken by another program: click to capture, or use Capture on the bar.");
    expect(KEY_TAKEN).toBe("Shift+F8 is taken by another program: use Capture on the bar (a capture from the bar has no hotspot).");
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
