/**
 * Workshop video - the one canvas (C1-C6), the pure half (lib/workshopVideo.ts): the five stages and which may be
 * opened, each collapsed stage's line, a workshop's status line from the records, the Capture count, the Publish
 * stage's lines and the routes.
 */
import { describe, expect, it } from "vitest";
import type { VideoToCourseProject } from "./videoToCourse";
import {
  type CanvasFacts, NEXT_LABELS, NOT_PLACED_PUBLISH_LINE, SAVE_BEFORE_NEXT, STAGES, STAGE_ORDER, WORKSHOP_VIDEO_TILE, type WorkshopStatusEntry, canOpenStage,
  canPublish, capturedLine, editorLine, furthestOf, nextStage, openLabel, publishConfigUrl, publishCourseUrl, publishDiffUrl, publishMessage, publishedLine, stageIndex,
  stageLine, videoLine, workshopStatusLine, workshopStatusUrl,
} from "./workshopVideo";

const project = (over: Partial<VideoToCourseProject> = {}): VideoToCourseProject => ({
  id: "p1", name: "Your First Win", created_at: "2026-10-31T13:00:00Z", rendered_at: "2026-10-31T14:02:00Z", has_video: true,
  vimeo: { id: "123", link: "https://vimeo.com/123/abc", privacy: "unlisted", published_at: null, previous: [] }, placed: true, guide: "pdi-2hr-lab/01-first-win/guide.md", ...over,
});
const walkthrough = { id: "w1", name: "Your First Win", step_count: 10, pictured: 4, updated_at: null, created_at: null };

describe("the stages", () => {
  it("are five, in order, with a Next label each but the first and the last", () => {
    expect(STAGES.map((s) => s.label)).toEqual(["Workshop", "Capture", "Edit", "Video", "Publish"]);
    expect(STAGE_ORDER).toEqual(["workshop", "capture", "edit", "video", "publish"]);
    expect(stageIndex("edit")).toBe(2);
    expect(nextStage("workshop")).toBe("capture");
    expect(nextStage("publish")).toBeNull();
    expect(NEXT_LABELS).toEqual({ workshop: "", capture: "Next: Edit", edit: "Next: Video", video: "Next: Publish", publish: "" });
    expect(SAVE_BEFORE_NEXT.confirmLabel).toBe("Save and go on");
    expect(WORKSHOP_VIDEO_TILE).toEqual({ title: "Workshop video", description: "Pick a workshop, capture it, edit, make the video, publish." });
  });

  it("open Workshop always, the rest once a walkthrough is open and up to the furthest reached", () => {
    expect(canOpenStage("workshop", "workshop", false)).toBe(true);
    expect(canOpenStage("capture", "workshop", false)).toBe(false);
    expect(canOpenStage("capture", "capture", true)).toBe(true);
    expect(canOpenStage("edit", "capture", true)).toBe(false);
    expect(canOpenStage("capture", "video", true)).toBe(true);
    expect(canOpenStage("publish", "video", true)).toBe(false);
    expect(furthestOf("workshop", "capture")).toBe("capture");
    expect(furthestOf("video", "capture")).toBe("video");
  });
});

describe("the status lines (C2)", () => {
  it("says what a workshop has, as far as it got", () => {
    expect(workshopStatusLine(null)).toBe("no walkthrough yet");
    expect(workshopStatusLine({ walkthrough: null, project: null })).toBe("no walkthrough yet");
    const entry: WorkshopStatusEntry = { walkthrough, project: null };
    expect(workshopStatusLine(entry)).toBe("walkthrough: 4 of 10 steps have a picture");
    expect(workshopStatusLine({ walkthrough, project: project({ vimeo: null, placed: false }) })).toMatch(/^walkthrough: 4 of 10 steps have a picture · video rendered 2026-10-31 \d\d:\d\d$/);
    expect(workshopStatusLine({ walkthrough, project: project({ placed: false }) })).toMatch(/· on Vimeo \(Unlisted\)$/);
    expect(workshopStatusLine({ walkthrough, project: project() })).toMatch(/· on Vimeo \(Unlisted\) · placed in the guide$/);
    expect(workshopStatusLine({ walkthrough, project: project() }, { when: "2026-10-31T15:00:00Z", summary: "committed abc1234 and pushed" })).toMatch(/· placed in the guide · published 2026-10-31 \d\d:\d\d$/);
    expect(capturedLine(1, 1)).toBe("1 of 1 step has a picture");
    expect(capturedLine(0, 3)).toBe("0 of 3 steps have a picture");
    expect(openLabel(entry)).toBe("Open");
    expect(openLabel(null)).toBe("Import and open");
    expect(workshopStatusUrl("pdi-2hr-lab")).toBe("/api/courses/pdi-2hr-lab/workshop-status");
  });

  it("gives each collapsed stage one line", () => {
    const empty: CanvasFacts = { courseTitle: null, workshopTitle: null, walkthroughName: null, stepCount: 0, pictured: 0, chapterCount: 0, project: null, published: null, canCapture: true };
    expect(stageLine("workshop", empty)).toBe("Pick a course and a workshop.");
    expect(stageLine("capture", empty)).toBe("Capture the steps as you perform them (the desktop app).");
    expect(stageLine("edit", empty)).toBe("Edit the steps in the builder.");
    expect(stageLine("video", empty)).toBe("Render the video, publish it to Vimeo and place the link in the guide: one button.");
    expect(stageLine("publish", empty)).toBe("Publish the course through the Content Editor.");
    const facts: CanvasFacts = { ...empty, courseTitle: "PDI in 2 Hours", workshopTitle: "Your First Win", walkthroughName: "Your First Win", stepCount: 10, pictured: 4, chapterCount: 3, project: project({ placed: false }) };
    expect(stageLine("workshop", facts)).toBe("PDI in 2 Hours · Your First Win");
    expect(stageLine("capture", facts)).toBe("4 of 10 steps have a picture");
    expect(stageLine("capture", { ...facts, canCapture: false })).toBe("4 of 10 steps have a picture · capturing needs the desktop app");
    expect(stageLine("edit", facts)).toBe("Your First Win · 10 steps · 3 chapters");
    expect(stageLine("video", facts)).toMatch(/^video rendered 2026-10-31 \d\d:\d\d · on Vimeo \(Unlisted\) · not placed in the guide$/);
    expect(videoLine(project())).toMatch(/· placed in the guide$/);
    expect(videoLine(project({ has_video: false, vimeo: null, placed: false, rendered_at: null }))).toBe("not rendered yet");
    expect(stageLine("publish", { ...facts, published: { when: "2026-10-31T15:00:00Z", summary: "committed abc1234 and pushed (2 files)" } })).toMatch(/^Published 2026-10-31 \d\d:\d\d: committed abc1234 and pushed \(2 files\)$/);
  });
});

describe("the Publish stage (C6)", () => {
  it("names the routes, the message and the editor's line", () => {
    expect(publishConfigUrl).toBe("/api/courses/publish/config");
    expect(publishDiffUrl("pdi-2hr-lab")).toBe("/api/courses/pdi-2hr-lab/publish/diff");
    expect(publishCourseUrl("pdi 2")).toBe("/api/courses/pdi%202/publish");
    expect(publishMessage("Your First Win")).toBe("Walkthrough video: Your First Win");
    expect(publishMessage("  ")).toBe("Walkthrough video: workshop");
    expect(editorLine({ address: "http://localhost:8000", reachable: true, url: "https://github.com/jporeilly/Pentaho-Courses.git", ref: "main", message: null }))
      .toBe("The Content Editor at http://localhost:8000 publishes to https://github.com/jporeilly/Pentaho-Courses.git (main).");
    expect(editorLine({ address: "http://localhost:8000", reachable: false, url: null, ref: null, message: "The Content Editor is not running at http://localhost:8000: start it, then press Publish again." }))
      .toBe("The Content Editor is not running at http://localhost:8000: start it, then press Publish again.");
    expect(editorLine({ address: "http://x:1", reachable: false, url: null, ref: null, message: null })).toBe("The Content Editor is not running at http://x:1: start it, then press Publish again.");
    expect(publishedLine({ when: "2026-10-31T15:00:00Z", summary: "committed abc1234 and pushed" })).toMatch(/^Published 2026-10-31 \d\d:\d\d: committed abc1234 and pushed$/);
    expect(NOT_PLACED_PUBLISH_LINE).toContain("nothing new to publish");
  });

  it("publishes only with the editor reachable, a course and nothing in flight", () => {
    const up = { address: "a", reachable: true, url: "u", ref: "main", message: null };
    expect(canPublish(up, "pdi-2hr-lab", false)).toBe(true);
    expect(canPublish({ ...up, reachable: false }, "pdi-2hr-lab", false)).toBe(false);
    expect(canPublish(up, null, false)).toBe(false);
    expect(canPublish(up, "pdi-2hr-lab", true)).toBe(false);
    expect(canPublish(null, "pdi-2hr-lab", false)).toBe(false);
  });
});
