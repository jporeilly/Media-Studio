import { describe, expect, it } from "vitest";
import {
  type CourseInfo, type PreviewStep, BEFORE_FIRST_SECTION, MAX_WORKSHOPS, chapterGroups, chapterLabel, combinedCounts, combinedCountsOf, countsOf,
  courseLabel, defaultName, firstWorkshop, importBody, importNotice, previewLine, previewUrl, sectionState, tickedInOrder, toggleSection, workshopLabel,
} from "./workshops";

const course: CourseInfo = {
  id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.21", description: "A lab.",
  sections: [
    { title: "Get Ready", workshops: [] },
    { title: "See It Work", workshops: [{ slug: "01-first-win", title: "Your First Win", description: "", estimatedMinutes: 10, stepCount: 6 }] },
  ],
};
const w = (slug: string, title: string) => ({ slug, title, description: "", estimatedMinutes: null, stepCount: null });
/** The lab's shape: a section of one, a section of two, a section without a title. */
const lab: CourseInfo = {
  id: "lab", title: "PDI in 2 Hours", version: "1", description: "",
  sections: [
    { title: "Get Ready", workshops: [w("00", "Before You Start")] },
    { title: "See It Work", workshops: [w("01", "Your First Win"), w("02", "Build the Pipeline Yourself")] },
    { title: "", workshops: [w("09", "Bring Your Own Data")] },
  ],
};
const step = (number: number, in_chapter: string, chapter: string, picture: boolean): PreviewStep => ({
  number, chapter, in_chapter, instruction: `Step ${number}.`, text: `Step ${number}.`, picture, image: picture ? "p.png" : null, alt: "", launcher: null, note: false,
});
const steps = [step(1, "", "", false), step(2, "Open it", "Open it", true), step(3, "Open it", "", false), step(4, "Troubleshooting a bad file", "Troubleshooting a bad file", true), step(5, "Run it", "Run it", false)];

describe("W2e: the ticks, the name and the chapters", () => {
  it("orders the ticked workshops as the course does, whatever the tick order", () => {
    expect(tickedInOrder(lab, ["09", "02", "00"])).toEqual(["00", "02", "09"]);
    expect(tickedInOrder(lab, ["nope", "01"])).toEqual(["01"]);
    expect(tickedInOrder(null, ["01"])).toEqual([]);
  });

  it("a section's tick is all, some or none of its workshops, and ticks or unticks them whole", () => {
    const see = lab.sections[1];
    expect(sectionState(see, new Set(["01", "02"]))).toBe("all");
    expect(sectionState(see, new Set(["02", "00"]))).toBe("some");
    expect(sectionState(see, new Set(["00"]))).toBe("none");
    expect(sectionState(lab.sections[0], new Set())).toBe("none");
    expect([...toggleSection(new Set(["00"]), see, true)].sort()).toEqual(["00", "01", "02"]);
    expect([...toggleSection(new Set(["00", "01", "02"]), see, false)]).toEqual(["00"]);
  });

  it("names the walkthrough by one rule with the server's default_name: the workshop, the course, the section, or '+ N more'", () => {
    expect(defaultName(lab, ["01"])).toBe("Your First Win");
    expect(defaultName(lab, ["02", "01"])).toBe("See It Work");
    expect(defaultName(lab, ["09", "02", "01", "00"])).toBe("PDI in 2 Hours");
    expect(defaultName(lab, ["01", "00"])).toBe("Before You Start + 1 more");
    expect(defaultName(lab, ["09"])).toBe("Bring Your Own Data");
    expect(defaultName(lab, ["00", "09"])).toBe("Before You Start + 1 more");
    const small: CourseInfo = { ...lab, title: "Small", sections: [{ title: "All", workshops: [w("a", "A"), w("b", "B")] }] };
    expect(defaultName(small, ["a", "b"])).toBe("Small");
    expect(defaultName(small, ["a"])).toBe("A");
    expect(defaultName(lab, [])).toBe("");
    expect(defaultName(null, ["01"])).toBe("");
    expect(MAX_WORKSHOPS).toBe(50);
  });

  it("groups a preview's steps by the chapter they are in, the steps before the first heading first, and labels them", () => {
    const groups = chapterGroups(steps);
    expect(groups.map((g) => [g.name, g.steps.map((s) => s.number)])).toEqual([["", [1]], ["Open it", [2, 3]], ["Troubleshooting a bad file", [4]], ["Run it", [5]]]);
    expect(chapterLabel("")).toBe(BEFORE_FIRST_SECTION);
    expect(chapterLabel("Open it")).toBe("Open it");
    expect(chapterGroups([])).toEqual([]);
  });

  it("counts the steps kept after the chapters left out, and sums the workshops' counts", () => {
    expect(countsOf(steps, [])).toEqual({ steps: 5, chapters: 3, pictures: 2, to_capture: 3 });
    expect(countsOf(steps, ["Troubleshooting a bad file", ""])).toEqual({ steps: 3, chapters: 2, pictures: 1, to_capture: 2 });
    expect(countsOf(steps, ["", "Open it", "Troubleshooting a bad file", "Run it"])).toEqual({ steps: 0, chapters: 0, pictures: 0, to_capture: 0 });
    expect(combinedCounts([{ steps: 10, chapters: 4, pictures: 2, to_capture: 8 }, { steps: 17, chapters: 3, pictures: 9, to_capture: 8 }])).toEqual({ steps: 27, chapters: 7, pictures: 11, to_capture: 16 });
    expect(previewLine(combinedCounts([{ steps: 10, chapters: 4, pictures: 2, to_capture: 8 }, { steps: 17, chapters: 3, pictures: 9, to_capture: 8 }]))).toBe("27 steps in 7 chapters; 11 pictures from the guide, 16 to capture");
    expect(combinedCounts([])).toEqual({ steps: 0, chapters: 0, pictures: 0, to_capture: 0 });
  });

  it("X1: with several workshops the lines count the chapter the server names after a workshop whose first kept step starts none", () => {
    // The backend's `_three` fixture as the preview answers it: the first workshop has a step before its first heading.
    const first = [step(1, "", "", false), step(2, "Open it", "Open it", false), step(3, "Open it", "", false), step(4, "Troubleshooting a bad file", "Troubleshooting a bad file", false), step(5, "Run it", "Run it", false)];
    const second = [step(1, "Build", "Build", true), step(2, "Build", "", false)];
    const third = [step(1, "Join", "Join", false)];
    // One workshop: the server's chapter_count, nothing added - and the combined line of one is the same.
    expect(countsOf(first, [])).toEqual({ steps: 5, chapters: 3, pictures: 0, to_capture: 5 });
    expect(combinedCountsOf([{ steps: first, skips: [] }])).toEqual({ steps: 5, chapters: 3, pictures: 0, to_capture: 5 });
    // Three workshops whole: 8 steps in 6 chapters - the import's own numbers (the first workshop's steps before its heading start a chapter named by it).
    const whole = combinedCountsOf([{ steps: first, skips: [] }, { steps: second, skips: [] }, { steps: third, skips: [] }]);
    expect(whole).toEqual({ steps: 8, chapters: 6, pictures: 1, to_capture: 7 });
    expect(previewLine(whole)).toBe("8 steps in 6 chapters; 1 picture from the guide, 7 to capture");
    // "Troubleshooting a bad file" unticked: 7 steps in 5 chapters, as the import's audit row says.
    expect(combinedCountsOf([{ steps: first, skips: ["Troubleshooting a bad file"] }, { steps: second, skips: [] }, { steps: third, skips: [] }])).toEqual({ steps: 7, chapters: 5, pictures: 1, to_capture: 6 });
    // The "(before the first section)" group unticked too: the first kept step starts a chapter of its own, so none is added.
    expect(combinedCountsOf([{ steps: first, skips: ["", "Troubleshooting a bad file"] }, { steps: second, skips: [] }, { steps: third, skips: [] }])).toEqual({ steps: 6, chapters: 4, pictures: 1, to_capture: 5 });
    // A workshop's own line counts it too (when several are ticked), so the workshops' lines add up to the combined line.
    expect(countsOf(first, [], true)).toEqual({ steps: 5, chapters: 4, pictures: 0, to_capture: 5 });
    expect(countsOf(first, ["", "Troubleshooting a bad file"], true)).toEqual({ steps: 3, chapters: 2, pictures: 0, to_capture: 3 });
    expect(countsOf(first, ["", "Open it", "Troubleshooting a bad file", "Run it"], true)).toEqual({ steps: 0, chapters: 0, pictures: 0, to_capture: 0 });
    expect(combinedCountsOf([])).toEqual({ steps: 0, chapters: 0, pictures: 0, to_capture: 0 });
  });

  it("the import body names the workshops in the course's order with the chapters to leave out, and the name only when given", () => {
    expect(importBody("lab", ["00", "02"], { "02": ["Troubleshooting a bad file"], "00": [] }, "  ")).toEqual({
      course: "lab", workshops: [{ slug: "00" }, { slug: "02", skip_chapters: ["Troubleshooting a bad file"] }],
    });
    expect(importBody("lab", ["01"], {}, " Mine ")).toEqual({ course: "lab", workshops: [{ slug: "01" }], name: "Mine" });
  });
});

describe("the import's lines", () => {
  it("previewLine says the counts with their singulars, and the empty case plainly", () => {
    expect(previewLine({ steps: 12, chapters: 4, pictures: 9, to_capture: 3 })).toBe("12 steps in 4 chapters; 9 pictures from the guide, 3 to capture");
    expect(previewLine({ steps: 1, chapters: 1, pictures: 1, to_capture: 0 })).toBe("1 step in 1 chapter; 1 picture from the guide, 0 to capture");
    expect(previewLine({ steps: 5, chapters: 0, pictures: 0, to_capture: 5 })).toBe("5 steps in no chapters; 0 pictures from the guide, 5 to capture");
    expect(previewLine({ steps: 0, chapters: 0, pictures: 0, to_capture: 0 })).toBe("No steps: the walkthrough will be empty.");
  });

  it("labels a workshop with its minutes and steps when the manifest gives them, and a course with its version", () => {
    expect(workshopLabel({ title: "Your First Win", estimatedMinutes: 10, stepCount: 6 })).toBe("Your First Win (10 min · 6 steps)");
    expect(workshopLabel({ title: "Only", estimatedMinutes: null, stepCount: 1 })).toBe("Only (1 step)");
    expect(workshopLabel({ title: "Bare", estimatedMinutes: null, stepCount: null })).toBe("Bare");
    expect(courseLabel(course)).toBe("PDI in 2 Hours (0.1.21)");
    expect(courseLabel({ title: "No version", version: "" })).toBe("No version");
  });

  it("preselects the first workshop of the first section that has one; the notice and the URL", () => {
    expect(firstWorkshop(course)).toBe("01-first-win");
    expect(firstWorkshop({ ...course, sections: [] })).toBeNull();
    expect(firstWorkshop(null)).toBeNull();
    expect(importNotice("Your First Win", { steps: 10, chapters: 4, pictures: 2, to_capture: 8 })).toBe('Imported "Your First Win": 10 steps in 4 chapters; 2 pictures from the guide, 8 to capture.');
    expect(importNotice("X", null)).toBe('Imported "X".');
    expect(previewUrl("pdi-2hr-lab", "01-first-win")).toBe("/api/courses/pdi-2hr-lab/workshops/01-first-win/preview");
    expect(previewUrl("a b", "c/d")).toBe("/api/courses/a%20b/workshops/c%2Fd/preview");
  });
});
