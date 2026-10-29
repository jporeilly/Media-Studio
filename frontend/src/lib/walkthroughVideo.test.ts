/**
 * Make a video (W3), the pure half of the builder's dialog (lib/walkthrough.ts): which boxes are for this
 * walkthrough (a note anywhere, a hotspot anywhere), the steps line, the projects made from it before, when
 * they were made, and the routes.
 */
import { describe, expect, it } from "vitest";
import {
  type MadeProject, type Step, NO_HOTSPOTS, NO_NOTES, SAVE_BEFORE_VIDEO, hasHotspots, hasNotes, madeFrom, madeWhen, makeVideoUrl, projectUrl,
  stepsLine,
} from "./walkthrough";

const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 400, height: 300, hotspot: null, kind: "tooltip", text: "", instruction: "", placement: "auto", chapter: "", note: "", source: null,
  click: "left", marker: "box", ...over,
});
const A = "aaaaaaaaaaaa", B = "bbbbbbbbbbbb", C = "cccccccccccc";

describe("the boxes", () => {
  it("notes are for a walkthrough with a note on any step; hotspots likewise", () => {
    expect(hasNotes({ steps: [step(A), step(B, { note: "  " })] })).toBe(false);
    expect(hasNotes({ steps: [step(A), step(B, { note: "Under the hood." })] })).toBe(true);
    expect(hasHotspots({ steps: [step(A), step(B)] })).toBe(false);
    expect(hasHotspots({ steps: [step(A), step(B, { hotspot: { x: 1, y: 1, width: 10, height: 10 } })] })).toBe(true);
    expect(hasNotes({ steps: [] })).toBe(false);
    expect(NO_NOTES).toBe("No step has a note.");
    expect(NO_HOTSPOTS).toBe("No step has a hotspot.");
  });
});

describe("the steps line", () => {
  it("counts the steps, those without a picture and those with nothing to say", () => {
    const steps = [
      step(A, { instruction: "Open it." }),
      step(B, { image: null, width: 0, height: 0, text: "Watch" }),
      step(C, { image: null, width: 0, height: 0 }),
    ];
    expect(stepsLine({ steps })).toBe("3 steps, 2 without a picture, 1 without any text");
    expect(stepsLine({ steps: [step(A, { text: "Hi" })] })).toBe("1 step");
    expect(stepsLine({ steps: [step(A), step(B)] })).toBe("2 steps, 2 without any text");
    expect(stepsLine({ steps: [step(A, { image: null, width: 0, height: 0, instruction: "Said" })] })).toBe("1 step, 1 without a picture");
    expect(stepsLine({ steps: [] })).toBe("0 steps");
  });
});

describe("made before", () => {
  const projects: MadeProject[] = [
    { id: "p1", name: "First", kind: "walkthrough", created_at: "2026-10-22T14:02:00Z", walkthrough_id: "w1" },
    { id: "p2", name: "A deck", kind: "deck", created_at: "2026-10-22T15:00:00Z" },
    { id: "p3", name: "Other walkthrough", kind: "walkthrough", created_at: "2026-10-22T15:30:00Z", walkthrough_id: "w2" },
    { id: "p4", name: "Second", kind: "walkthrough", created_at: "2026-10-23T09:10:00Z", walkthrough_id: "w1" },
  ];

  it("lists the walkthrough projects made from this walkthrough, newest first, and nothing else", () => {
    expect(madeFrom(projects, "w1").map((p) => p.id)).toEqual(["p4", "p1"]);
    expect(madeFrom(projects, "w9")).toEqual([]);
    expect(madeFrom([], "w1")).toEqual([]);
  });

  it("says when a project was made, to the minute, in local time", () => {
    const d = new Date("2026-10-22T14:02:00Z");
    const two = (n: number) => String(n).padStart(2, "0");
    expect(madeWhen("2026-10-22T14:02:00Z")).toBe(`${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`);
    expect(madeWhen("2026-10-22T14:02:00+00:00")).toBe(madeWhen("2026-10-22T14:02:00Z"));
    expect(madeWhen(null)).toBe("");
    expect(madeWhen("not a date")).toBe("");
  });
});

describe("the routes and the words", () => {
  it("names the video route and the project's page", () => {
    expect(makeVideoUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/video");
    expect(projectUrl("p1")).toBe("/projects/p1");
    expect(SAVE_BEFORE_VIDEO.confirmLabel).toBe("Save and make");
    expect(SAVE_BEFORE_VIDEO.title).toBe("Save before making the video?");
  });
});
