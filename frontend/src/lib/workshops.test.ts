import { describe, expect, it } from "vitest";
import { type CourseInfo, courseLabel, firstWorkshop, importNotice, previewLine, previewUrl, workshopLabel } from "./workshops";

const course: CourseInfo = {
  id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.21", description: "A lab.",
  sections: [
    { title: "Get Ready", workshops: [] },
    { title: "See It Work", workshops: [{ slug: "01-first-win", title: "Your First Win", description: "", estimatedMinutes: 10, stepCount: 6 }] },
  ],
};

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
