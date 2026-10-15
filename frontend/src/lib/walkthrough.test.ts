/**
 * The walkthrough's pure model (lib/walkthrough.ts): the document and its one serialisation, the builder's
 * step edits (update, delete, insert after, move, reorder, the hotspot and its clamp on a smaller picture),
 * the chapters and where next / back / home / end / a chapter jump land, the player's fit and the tooltip's
 * placement, the labels, the keys and the URLs.
 */
import { describe, expect, it } from "vitest";
import {
  type Step, type WalkthroughDocument, MAX_STEPS, MIN_HOTSPOT_SIDE, backIndex, canAddStep, chapterAt, chapterCount, chapterStart, chapters,
  clampHotspot, clampIndex, countsLine, deleteStep, displayedRect, documentOf, endIndex, fitRect, homeIndex, imagesOf, insertStep, moveStep,
  namesOnly, narrationOf, nextIndex, normaliseStep, playUrl, putBody, reorderStep, safeFileStem, sameDocument, serialiseDocument, setHotspot,
  shownAsModal, stepById, stepImageUrl, stepIndex, stepLabel, tooltipPlacement, transcript, transcriptFileName, typingIn, updateStep,
} from "./walkthrough";

const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 400, height: 300, hotspot: null, kind: "tooltip", text: "", instruction: "", placement: "auto", chapter: "", source: null, ...over,
});
const ids = (doc: WalkthroughDocument) => doc.steps.map((s) => s.id);
const doc = (...steps: Step[]): WalkthroughDocument => ({ name: "Tour", steps });
const A = "aaaaaaaaaaaa", B = "bbbbbbbbbbbb", C = "cccccccccccc", D = "dddddddddddd";

describe("the document", () => {
  it("normalises a step to exactly the server's fields, the hotspot in whole pixels, and compares by content", () => {
    const s = normaliseStep({ ...step(A), hotspot: { x: 1.4, y: 2.6, width: 10.2, height: 9.9 }, extra: "dropped" } as Step);
    expect(Object.keys(s)).toEqual(["id", "image", "width", "height", "hotspot", "kind", "text", "instruction", "placement", "chapter", "source"]);
    expect(s.hotspot).toEqual({ x: 1, y: 3, width: 10, height: 10 });
    const a = doc(step(A, { text: "x" })), b = doc({ ...step(A, { text: "x" }) });
    expect(sameDocument(a, b)).toBe(true);
    expect(sameDocument(a, doc(step(A, { text: "y" })))).toBe(false);
    expect(sameDocument(a, { ...a, name: "Other" })).toBe(false);
    expect(serialiseDocument(a)).toBe(JSON.stringify({ name: "Tour", steps: [normaliseStep(step(A, { text: "x" }))] }));
    // The record's extra fields never reach the document; the PUT body carries the revision and nothing else.
    const rec = { id: "w", name: " Tour ", steps: [step(A)], owner_id: "u", owner_name: "U", created_at: "", updated_at: "", revision: 4, version: 1 };
    expect(documentOf(rec)).toEqual({ name: " Tour ", steps: [step(A)] });
    expect(putBody(documentOf(rec), 4)).toEqual({ version: 1, name: "Tour", revision: 4, steps: [step(A)] });
    expect(Object.keys(putBody(documentOf(rec), 4))).toEqual(["version", "name", "revision", "steps"]);
  });

  it("R1: ONE normalisation rule - a chapter with a trailing space, a name with one and CRLF line breaks are the same document as the server's stripped answer", () => {
    const local = { name: "Tour ", steps: [step(A, { chapter: " Intro ", text: "a\r\nb", instruction: "do\rthis" })] };
    const server = { name: "Tour", steps: [step(A, { chapter: "Intro", text: "a\nb", instruction: "do\nthis" })] };
    expect(sameDocument(local, server)).toBe(true);
    expect(serialiseDocument(local)).toBe(serialiseDocument(server));
    expect(normaliseStep(step(A, { chapter: " Intro " })).chapter).toBe("Intro");
    expect(normaliseStep(step(A, { text: "x\r\ny" })).text).toBe("x\ny");
    expect(putBody(local, 1).name).toBe("Tour");
    expect(putBody(local, 1).steps[0].chapter).toBe("Intro");
    // A record from before the instruction existed: "" in the document.
    expect(normaliseStep({ ...step(A), instruction: undefined as unknown as string }).instruction).toBe("");
    // Real differences are still differences.
    expect(sameDocument(local, { ...server, name: "Tour!" })).toBe(false);
    expect(sameDocument(local, { ...server, steps: [step(A, { chapter: "Intro", text: "a\nb", instruction: "do this" })] })).toBe(false);
  });

  it("the pictures a document names, and whether a document names only the pictures a saved record holds (the builder's prune)", () => {
    const held = imagesOf({ steps: [step(A), step(B)] });
    expect([...held]).toEqual([`step-${A}.png`, `step-${B}.png`]);
    expect(namesOnly({ steps: [step(A)] }, held)).toBe(true);
    expect(namesOnly({ steps: [step(A), step(B), step(C)] }, held)).toBe(false);
    expect(namesOnly({ steps: [] }, held)).toBe(true);
  });
});

//: The same document as ``tests/test_walkthroughs.py`` holds as TRANSCRIPT_FIXTURE, hand-synchronised: the two tests
//: assert the same Markdown byte for byte, each naming the other. Change one, change both.
const TRANSCRIPT_FIXTURE = {
  name: "  Fixture tour ",
  steps: [
    step("aaaaaaaaaaaa", { chapter: "Start", instruction: "Open the menu.\nPick Import.", text: "ignored when there is an instruction" }),
    step("bbbbbbbbbbbb", { chapter: "", instruction: "", text: "Press Save" }),
    step("cccccccccccc", { chapter: " Finish ", instruction: "   ", text: "" }),
    // A RAW record's CRLF and CR (the API stores LF; a hand-made record may not): one line break each, both sides.
    step("dddddddddddd", { chapter: "", instruction: "Line one\r\nLine two\rthree", text: "" }),
  ],
};
const TRANSCRIPT_EXPECTED =
  "# Fixture tour\n\n## Start\n\n1. Open the menu.\n   Pick Import.\n2. Press Save\n\n## Finish\n\n3. (no instruction yet)\n" +
  "4. Line one\n   Line two\n   three\n";

describe("the narration and the transcript", () => {
  it("narrationOf is the instruction when it is not blank, else the text (tests/test_walkthroughs.py::test_narration_of_is_the_instruction_else_the_text asserts the same cases)", () => {
    expect(narrationOf({ instruction: "Drag it onto the canvas", text: "Drag" })).toBe("Drag it onto the canvas");
    expect(narrationOf({ instruction: "   ", text: "Press Save" })).toBe("Press Save");
    expect(narrationOf({ instruction: "", text: "Press Save" })).toBe("Press Save");
    expect(narrationOf({ instruction: undefined as unknown as string, text: "Old record" })).toBe("Old record");
    expect(narrationOf({ instruction: "", text: "" })).toBe("");
  });

  it("transcript writes the same bytes as the backend's transcript_markdown (tests/test_walkthroughs.py::test_transcript_markdown_matches_the_pages_transcript_byte_for_byte holds the same fixture)", () => {
    expect(transcript(TRANSCRIPT_FIXTURE)).toBe(TRANSCRIPT_EXPECTED);
    expect(transcript({ name: "Empty", steps: [] })).toBe("# Empty\n\n(no steps yet)\n");
    expect(transcript({ name: "", steps: [] })).toBe("# Walkthrough\n\n(no steps yet)\n");
    // No chapter at all: numbered items straight under the title; CRLF in a text is one line break.
    expect(transcript({ name: "Flat", steps: [step(A, { text: "One" }), step(B, { instruction: "Two\r\nmore" })] })).toBe("# Flat\n\n1. One\n2. Two\n   more\n");
  });

  it("the transcript's file name is the server's safe stem of the name", () => {
    expect(safeFileStem("Tour: Part 1/2")).toBe("Tour-Part-12");
    expect(safeFileStem("  Onboarding   tour  ")).toBe("Onboarding-tour");
    expect(safeFileStem("")).toBe("walkthrough");
    expect(safeFileStem("...")).toBe("walkthrough");
    expect(safeFileStem("x".repeat(80))).toBe("x".repeat(60));
    expect(safeFileStem("ends with dash -")).toBe("ends-with-dash");
    expect(transcriptFileName("Onboarding tour")).toBe("Onboarding-tour.md");
  });
});

describe("the builder's edits", () => {
  it("update, delete, lookup", () => {
    const d = doc(step(A), step(B));
    expect(updateStep(d, B, { text: "Hi" }).steps[1].text).toBe("Hi");
    expect(updateStep(d, B, (s) => ({ ...s, chapter: "C" })).steps[1].chapter).toBe("C");
    expect(updateStep(d, "nope", { text: "x" })).toEqual(d);
    expect(ids(deleteStep(d, A))).toEqual([B]);
    expect(stepIndex(d, B)).toBe(1);
    expect(stepIndex(d, null)).toBe(-1);
    expect(stepById(d, A)?.id).toBe(A);
    expect(stepById(d, "nope")).toBeNull();
  });

  it("insert after the selected step, append with none, never twice", () => {
    const d = doc(step(A), step(B));
    expect(ids(insertStep(d, step(C), A))).toEqual([A, C, B]);
    expect(ids(insertStep(d, step(C), B))).toEqual([A, B, C]);
    expect(ids(insertStep(d, step(C), null))).toEqual([A, B, C]);
    expect(ids(insertStep(d, step(C), "unknown"))).toEqual([A, B, C]);
    expect(insertStep(d, step(A), null)).toBe(d);
    expect(canAddStep(d)).toBe(true);
    const full = doc(...Array.from({ length: MAX_STEPS }, (_, i) => step(String(i).padStart(12, "0"))));
    expect(canAddStep(full)).toBe(false);
  });

  it("move up and down stop at the ends; reorder places a step where it is dropped", () => {
    const d = doc(step(A), step(B), step(C));
    expect(ids(moveStep(d, B, -1))).toEqual([B, A, C]);
    expect(ids(moveStep(d, B, 1))).toEqual([A, C, B]);
    expect(moveStep(d, A, -1)).toBe(d);
    expect(moveStep(d, C, 1)).toBe(d);
    expect(moveStep(d, "nope", 1)).toBe(d);
    expect(ids(reorderStep(d, 0, 2))).toEqual([B, C, A]);
    expect(ids(reorderStep(d, 2, 0))).toEqual([C, A, B]);
    expect(reorderStep(d, 1, 1)).toBe(d);
    expect(reorderStep(d, -1, 1)).toBe(d);
    expect(reorderStep(d, 0, 3)).toBe(d);
  });

  it("the hotspot is set and cleared, and clamped to a smaller picture as the server clamps it", () => {
    const d = setHotspot(doc(step(A)), A, { x: 10, y: 10, width: 20, height: 20 });
    expect(d.steps[0].hotspot).toEqual({ x: 10, y: 10, width: 20, height: 20 });
    expect(setHotspot(d, A, null).steps[0].hotspot).toBeNull();
    const size = { width: 100, height: 100 };
    expect(clampHotspot(null, size)).toBeNull();
    expect(clampHotspot({ x: 10, y: 10, width: 20, height: 20 }, size)).toEqual({ x: 10, y: 10, width: 20, height: 20 });
    expect(clampHotspot({ x: 95, y: 95, width: 20, height: 20 }, size)).toEqual({ x: 80, y: 80, width: 20, height: 20 });
    expect(clampHotspot({ x: 0, y: 0, width: 200, height: 300 }, { width: 100, height: 50 })).toEqual({ x: 0, y: 0, width: 100, height: 50 });
    expect(clampHotspot({ x: 50, y: 50, width: 20, height: 20 }, { width: 60, height: 60 })).toEqual({ x: 40, y: 40, width: 20, height: 20 });
    expect(clampHotspot({ x: 0, y: 0, width: 20, height: 20 }, { width: MIN_HOTSPOT_SIDE - 1, height: 100 })).toBeNull();
    expect(clampHotspot({ x: 0, y: 0, width: 2, height: 2 }, size)).toEqual({ x: 0, y: 0, width: MIN_HOTSPOT_SIDE, height: MIN_HOTSPOT_SIDE });
    expect(clampHotspot({ x: 1.6, y: 1.2, width: 10.4, height: 10.5 }, size)).toEqual({ x: 2, y: 1, width: 10, height: 11 });
  });

  it("a modal step, and a tooltip step without a hotspot, are shown as a modal", () => {
    expect(shownAsModal({ kind: "modal", hotspot: { x: 0, y: 0, width: 10, height: 10 } })).toBe(true);
    expect(shownAsModal({ kind: "tooltip", hotspot: null })).toBe(true);
    expect(shownAsModal({ kind: "tooltip", hotspot: { x: 0, y: 0, width: 10, height: 10 } })).toBe(false);
  });
});

describe("chapters and navigation", () => {
  const d = doc(step(A), step(B, { chapter: "Setup" }), step(C), step(D, { chapter: " Finish " }));

  it("a chapter starts at every step with a name; the count, the current one, the start of a named one", () => {
    expect(chapters(d)).toEqual([{ name: "Setup", index: 1, stepId: B }, { name: "Finish", index: 3, stepId: D }]);
    expect(chapterCount(d)).toBe(2);
    expect(chapterCount(doc(step(A)))).toBe(0);
    expect(chapterAt(d, 0)).toBeNull();
    expect(chapterAt(d, 1)?.name).toBe("Setup");
    expect(chapterAt(d, 2)?.name).toBe("Setup");
    expect(chapterAt(d, 3)?.name).toBe("Finish");
    expect(chapterStart(d, "Finish")).toBe(3);
    expect(chapterStart(d, "Nope")).toBe(-1);
  });

  it("next, back, home, end and the clamp", () => {
    expect(nextIndex(0, 4)).toBe(1);
    expect(nextIndex(3, 4)).toBeNull();
    expect(nextIndex(0, 0)).toBeNull();
    expect(backIndex(2)).toBe(1);
    expect(backIndex(0)).toBe(0);
    expect(homeIndex()).toBe(0);
    expect(endIndex(4)).toBe(3);
    expect(endIndex(0)).toBe(0);
    expect(clampIndex(99, 4)).toBe(3);
    expect(clampIndex(-1, 4)).toBe(0);
    expect(clampIndex(2.7, 4)).toBe(2);
    expect(clampIndex(Number.NaN, 4)).toBe(0);
    expect(clampIndex(1, 0)).toBe(0);
  });

  it("the labels", () => {
    expect(stepLabel(2, 12)).toBe("Step 3 of 12");
    expect(stepLabel(0, 0)).toBe("No steps");
    expect(countsLine(12, 3)).toBe("12 steps · 3 chapters");
    expect(countsLine(1, 1)).toBe("1 step · 1 chapter");
    expect(countsLine(0, 0)).toBe("0 steps");
    expect(countsLine(2, 0)).toBe("2 steps");
  });
});

describe("the player's geometry", () => {
  it("fits the picture inside the container, centred, with the scale used", () => {
    // A wide picture in a square: the width fills, the height is centred.
    expect(fitRect({ width: 400, height: 200 }, { width: 800, height: 800 })).toEqual({ x: 0, y: 200, width: 800, height: 400, scale: 2 });
    // A tall picture in a wide container: the height fills, the width is centred.
    expect(fitRect({ width: 200, height: 400 }, { width: 800, height: 400 })).toEqual({ x: 300, y: 0, width: 200, height: 400, scale: 1 });
    // Exactly the container's shape: it fills.
    expect(fitRect({ width: 1920, height: 1080 }, { width: 960, height: 540 })).toEqual({ x: 0, y: 0, width: 960, height: 540, scale: 0.5 });
    // Nothing to fit into: nothing, never NaN.
    expect(fitRect({ width: 400, height: 300 }, { width: 0, height: 0 })).toEqual({ x: 0, y: 0, width: 0, height: 0, scale: 0 });
    expect(fitRect({ width: 0, height: 0 }, { width: 100, height: 100 }).scale).toBe(100);
    expect(displayedRect({ x: 10, y: 20, width: 30, height: 40 }, { x: 5, y: 6, width: 0, height: 0, scale: 2 })).toEqual({ x: 25, y: 46, width: 60, height: 80 });
  });

  it("auto places the tooltip below, else right, else above, else left, whichever has room; never off screen", () => {
    const container = { width: 1000, height: 600 }, tip = { width: 200, height: 80 };
    const middle = { x: 400, y: 250, width: 100, height: 50 };
    expect(tooltipPlacement(middle, tip, container, "auto")).toEqual({ x: 350, y: 312, side: "bottom" });
    // No room below (the hotspot at the foot): to the right.
    const low = { x: 400, y: 540, width: 100, height: 50 };
    expect(tooltipPlacement(low, tip, container, "auto")).toMatchObject({ side: "right", x: 512 });
    // No room below or right (the bottom-right corner): above.
    const corner = { x: 880, y: 540, width: 100, height: 50 };
    expect(tooltipPlacement(corner, tip, container, "auto")).toMatchObject({ side: "top", y: 448 });
    // A hotspot the height of the screen on the right: left is the one side with room.
    const tall = { x: 880, y: 10, width: 100, height: 580 };
    expect(tooltipPlacement(tall, tip, container, "auto")).toMatchObject({ side: "left", x: 668 });
    // Room nowhere: the side with the most room, and the position kept inside the screen.
    const huge = { x: 10, y: 10, width: 980, height: 580 };
    const placed = tooltipPlacement(huge, tip, container, "auto");
    expect(placed.x).toBeGreaterThanOrEqual(8);
    expect(placed.y).toBeGreaterThanOrEqual(8);
    expect(placed.x + tip.width).toBeLessThanOrEqual(container.width - 8);
    expect(placed.y + tip.height).toBeLessThanOrEqual(container.height - 8);
  });

  it("an asked placement is honoured, centred on the hotspot along its side, and still kept on screen", () => {
    const container = { width: 1000, height: 600 }, tip = { width: 200, height: 80 };
    const middle = { x: 400, y: 250, width: 100, height: 50 };
    expect(tooltipPlacement(middle, tip, container, "top")).toEqual({ x: 350, y: 158, side: "top" });
    expect(tooltipPlacement(middle, tip, container, "bottom")).toEqual({ x: 350, y: 312, side: "bottom" });
    expect(tooltipPlacement(middle, tip, container, "left")).toEqual({ x: 188, y: 235, side: "left" });
    expect(tooltipPlacement(middle, tip, container, "right")).toEqual({ x: 512, y: 235, side: "right" });
    // Asked for the top with no room above: kept on screen (the margin), the side as asked.
    const high = { x: 400, y: 0, width: 100, height: 50 };
    expect(tooltipPlacement(high, tip, container, "top")).toEqual({ x: 350, y: 8, side: "top" });
    // A hotspot at the left edge asked for the left: clamped to the margin.
    const left = { x: 0, y: 250, width: 100, height: 50 };
    expect(tooltipPlacement(left, tip, container, "left")).toEqual({ x: 8, y: 235, side: "left" });
    // A container smaller than the tip: the margin wins, nothing negative.
    expect(tooltipPlacement(middle, tip, { width: 100, height: 50 }, "right")).toEqual({ x: 8, y: 8, side: "right" });
  });
});

describe("keys and urls", () => {
  it("typing in a box is told from a key on the page", () => {
    expect(typingIn(null)).toBe(false);
    expect(typingIn({ tagName: "INPUT" } as unknown as EventTarget)).toBe(true);
    expect(typingIn({ tagName: "TEXTAREA" } as unknown as EventTarget)).toBe(true);
    expect(typingIn({ tagName: "SELECT" } as unknown as EventTarget)).toBe(true);
    expect(typingIn({ tagName: "DIV", isContentEditable: true } as unknown as EventTarget)).toBe(true);
    expect(typingIn({ tagName: "BUTTON", isContentEditable: false } as unknown as EventTarget)).toBe(false);
  });

  it("the picture url is busted by the revision; the play url carries the step when it is not the first", () => {
    expect(stepImageUrl("w1", "s1", 7)).toBe("/api/walkthroughs/w1/steps/s1/image?v=7");
    expect(playUrl("w1")).toBe("/walkthroughs/w1/play");
    expect(playUrl("w1", 0)).toBe("/walkthroughs/w1/play");
    expect(playUrl("w1", 3)).toBe("/walkthroughs/w1/play?step=3");
  });
});
