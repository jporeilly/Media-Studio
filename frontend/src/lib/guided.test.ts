/**
 * Guided capture (W2), the pure half: the hotspot round the pointer in the picture's own pixels (the origin
 * subtracted, clamped at the edges, none outside), the window under the pointer, what each kind captures, the
 * still's name, the session's walk (advance, skip, back, the end), the bar's payload, the key, the constants.
 */
import { describe, expect, it } from "vitest";
import type { MonitorInfo, WindowInfo } from "./capture";
import {
  BAR_MODE, HOTSPOT_DEFAULT, KEYS_MODE, STEPS_BAR_HEIGHT, STEPS_BAR_WIDTH, STEP_KEY_EVENT, advance, atEnd, barPayload, captureName, captureTarget,
  currentId, guidedHotkey, hotspotAround, hotspotOf, pointerOf, remaining, sessionOf, stepBack, windowUnder,
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
    s = advance(s, "captured");
    expect([currentId(s), s.captured, s.skipped, remaining(s)]).toEqual(["c", 1, 0, 1]);
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

  it("the bar is told the step's place in the WHOLE walkthrough, its text and instruction, and what it may do", () => {
    const doc = { name: "Tour", steps: [step("a"), step("b", { image: null, width: 0, height: 0 }), step("c", { image: null, width: 0, height: 0, text: "" })] };
    const s = sessionOf(["b", "c"]);
    expect(barPayload("Tour", doc, s, "ready")).toEqual({
      mode: BAR_MODE, phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text b", instruction: "Do b", remaining: 2, canBack: false, canCapture: true,
      message: null,
    });
    // A refusal the capture goes on under rides in the payload: the bar shows it under the step.
    expect(barPayload("Tour", doc, s, "ready", "Shift+F8 is taken by another program")).toMatchObject({ message: "Shift+F8 is taken by another program", canCapture: true });
    expect(barPayload("Tour", doc, advance(s, "captured"), "ready")).toMatchObject({ label: "Step 3 of 3", text: "(no text yet)", instruction: "Do c", remaining: 1, canBack: true, canCapture: true });
    expect(barPayload("Tour", doc, s, "capturing")).toMatchObject({ canCapture: false });
    expect(barPayload("Tour", doc, advance(advance(s, "captured"), "skipped"), "finished")).toMatchObject({ label: "All steps visited", instruction: "", remaining: 0, canBack: true, canCapture: false });
    expect(BAR_MODE).toBe("steps");
    expect(KEYS_MODE).toBe("steps");
    expect(STEP_KEY_EVENT).toBe("capture:step-key");
    expect([STEPS_BAR_WIDTH, STEPS_BAR_HEIGHT]).toEqual([600, 96]);
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
