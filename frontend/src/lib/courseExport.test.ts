/**
 * Export to course (W2c): the dialog's pure lines and the routes (lib/courseExport.ts).
 */
import { describe, expect, it } from "vitest";
import { courseZipUrl, exportPlanUrl, exportWriteUrl, filesLine, placedWhere, planLine, playerZipUrl, settingLine } from "./courseExport";

describe("the routes", () => {
  it("names the plan, the write, the course zip (with the player when asked) and the player zip", () => {
    expect(exportPlanUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/export/course/plan");
    expect(exportWriteUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/export/course");
    expect(courseZipUrl("abc123abc123", false)).toBe("/api/walkthroughs/abc123abc123/export/course.zip");
    expect(courseZipUrl("abc123abc123", true)).toBe("/api/walkthroughs/abc123abc123/export/course.zip?player=1");
    expect(playerZipUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/export/player.zip");
  });
});

describe("the lines", () => {
  const one = { number: 1, step_id: "x", text: "t", reason: "r" };
  it("says how many figures go where, and what is not placed or skipped, with the singulars", () => {
    expect(planLine({ figures: 3, guide: "demo/01/guide.md", not_placed: [], skipped: [] })).toBe("3 figures into demo/01/guide.md");
    expect(planLine({ figures: 1, guide: "g", not_placed: [one], skipped: [one, one] })).toBe("1 figure into g; 1 step not placed; 2 skipped");
    expect(planLine({ figures: 0, guide: "g", not_placed: [one, one], skipped: [] })).toBe("0 figures into g; 2 steps not placed");
  });
  it("says where a figure goes", () => {
    expect(placedWhere({ how: "new", line: 9 })).toBe("after line 9");
    expect(placedWhere({ how: "replaced", line: 11 })).toBe("rewritten at line 11");
    expect(placedWhere({ how: "unchanged", line: 11 })).toBe("unchanged at line 11");
  });
  it("counts the files to write and to remove, and says when there is nothing to do", () => {
    expect(filesLine([{ path: "a", action: "unchanged" }])).toBe("Nothing to write: the guide already holds these figures.");
    expect(filesLine([{ path: "a", action: "write" }, { path: "b", action: "write" }, { path: "c", action: "remove" }])).toBe("2 files to write, 1 to remove");
    expect(filesLine([{ path: "a", action: "remove" }])).toBe("1 to remove");
  });
  it("says the setting's state and what follows from it", () => {
    expect(settingLine(true)).toMatch(/^Allow export into the Courses folder is on/);
    expect(settingLine(false)).toMatch(/is off \(Settings › Studio, an administrator\): download the zip/);
  });
});
