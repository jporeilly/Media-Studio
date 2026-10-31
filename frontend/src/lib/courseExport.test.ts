/**
 * Export to course (W2c): the dialog's pure lines and the routes (lib/courseExport.ts).
 */
import { describe, expect, it } from "vitest";
import {
  type GuidePlan, courseZipUrl, exportPlanUrl, exportTitle, exportWriteUrl, filesLine, guidesOf, placedWhere, planLine, playerWhere, playerZipUrl, settingLine,
  stepsOfNoGuide, writeBody,
} from "./courseExport";

const guide = (slug: string, sha: string, figures = 1): GuidePlan => ({
  slug, title: slug, guide: `demo/${slug}/guide.md`, guide_sha256: sha, placed: [], not_placed: [], skipped: [], removed: [], new_in_guide: [], kept: [],
  figures, changed: true, diff: "",
});

describe("W2e: several guides", () => {
  const one = { number: 1, step_id: "x", text: "t", reason: "r" };
  it("the combined line counts the guides when there are several, and reads as before for one", () => {
    expect(planLine({ figures: 5, guide: "demo/01-first/guide.md", not_placed: [one], skipped: [one, one], guides: [guide("01-first", "a"), guide("02-second", "b")] })).toBe(
      "5 figures into 2 guides; 1 step not placed; 2 skipped");
    expect(planLine({ figures: 3, guide: "demo/01-first/guide.md", not_placed: [], skipped: [], guides: [guide("01-first", "a", 3)] })).toBe("3 figures into demo/01-first/guide.md");
    expect(planLine(guide("02-second", "b", 2))).toBe("2 figures into demo/02-second/guide.md");
  });
  it("the write sends every guide's sha256 from the plan - and the player's caption when the player goes and one was typed", () => {
    expect(writeBody({ guides: [guide("01-first", "a".repeat(64)), guide("02-second", "b".repeat(64))] }, true)).toEqual({
      guides: [{ slug: "01-first", sha256: "a".repeat(64) }, { slug: "02-second", sha256: "b".repeat(64) }], player: true,
    });
    expect(writeBody({ guides: [guide("01-first", "a".repeat(64))] }, true, "Walkthrough: the first win")).toEqual({
      guides: [{ slug: "01-first", sha256: "a".repeat(64) }], player: true, caption: "Walkthrough: the first win",
    });
    // Without the player the caption is not sent, typed or not.
    expect(writeBody({ guides: [guide("01-first", "a".repeat(64))] }, false, "Walkthrough: x")).toEqual({ guides: [{ slug: "01-first", sha256: "a".repeat(64) }], player: false });
  });
  it("B3: says where the embedded player's figure goes, per the plan's player block", () => {
    expect(playerWhere({ how: "placeholder", line: 11, heading: "" })).toBe("The figure replaces the Interactive walkthrough placeholder at line 11.");
    expect(playerWhere({ how: "video", line: 5, heading: "" })).toBe("The figure goes below the video figure at line 5.");
    expect(playerWhere({ how: "heading", line: 5, heading: "## 1. Open it" })).toBe('The figure goes after "## 1. Open it" (line 5).');
    expect(playerWhere({ how: "heading", line: 0, heading: "" })).toBe("The figure goes at the top of the guide (it has no heading).");
    expect(playerWhere({ how: "replaced", line: 40, heading: "" })).toBe("The figure is rewritten at line 40.");
    expect(playerWhere({ how: "unchanged", line: 40, heading: "" })).toBe("The figure is unchanged at line 40.");
    expect(playerWhere(null)).toBe("");
  });
  it("X5: the steps of no guide are the not placed AND the skipped ones no guide lists, each flagged, in step order", () => {
    const ours5 = { number: 5, step_id: "s5s5s5s5s5s5", text: "Select Close.", reason: "Its text is not in the guide any more." };
    const ours1 = { number: 1, step_id: "s1s1s1s1s1s1", text: "Open it.", reason: "No picture yet." };
    const home9 = { number: 9, step_id: "s9s9s9s9s9s9", text: "Added later.", reason: "Added in the walkthrough, not imported: with several workshops it names no guide to go into, and none is guessed." };
    const home3 = { number: 3, step_id: "s3s3s3s3s3s3", text: "Nothing yet.", reason: "No picture yet." };
    const g1 = { ...guide("01-first", "a"), not_placed: [ours5], skipped: [ours1] };
    expect(stepsOfNoGuide({ guides: [g1], not_placed: [ours5, home9], skipped: [ours1, home3] })).toEqual([{ ...home3, skipped: true }, { ...home9, skipped: false }]);
    expect(stepsOfNoGuide({ guides: [g1], not_placed: [ours5], skipped: [ours1] })).toEqual([]);
    expect(stepsOfNoGuide({ guides: [], not_placed: [home9], skipped: [] })).toEqual([{ ...home9, skipped: false }]);
  });

  it("the Export button's title names every guide of the record, and the one of a record before this", () => {
    const source = { course: "pdi-2hr-lab", slug: "01-first-win", title: "PDI", version: "1" };
    expect(guidesOf(source)).toEqual(["01-first-win"]);
    expect(guidesOf({ ...source, workshops: [{ slug: "01-first-win", title: "A" }, { slug: "02-build-the-pipeline", title: "B" }] })).toEqual(["01-first-win", "02-build-the-pipeline"]);
    expect(exportTitle({ ...source, workshops: [{ slug: "01-first-win", title: "A" }, { slug: "02-build-the-pipeline", title: "B" }] })).toBe(
      "Write the figures into pdi-2hr-lab/01-first-win, 02-build-the-pipeline, or download them; or download the interactive player");
    expect(exportTitle(source)).toBe("Write the figures into pdi-2hr-lab/01-first-win, or download them; or download the interactive player");
    expect(exportTitle(null)).toBe("Download the interactive player (the walkthrough was not imported from a course workshop)");
  });
});

describe("the routes", () => {
  it("names the plan, the write, the course zip (with the player when asked) and the player zip", () => {
    expect(exportPlanUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/export/course/plan");
    expect(exportWriteUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/export/course");
    expect(courseZipUrl("abc123abc123", false)).toBe("/api/walkthroughs/abc123abc123/export/course.zip");
    expect(courseZipUrl("abc123abc123", true)).toBe("/api/walkthroughs/abc123abc123/export/course.zip?player=1");
    // The player's caption travels with the zip when one was typed; never without the player.
    expect(courseZipUrl("abc123abc123", true, "Walkthrough: the first win")).toBe("/api/walkthroughs/abc123abc123/export/course.zip?player=1&caption=Walkthrough%3A+the+first+win");
    expect(courseZipUrl("abc123abc123", false, "Walkthrough: x")).toBe("/api/walkthroughs/abc123abc123/export/course.zip");
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
