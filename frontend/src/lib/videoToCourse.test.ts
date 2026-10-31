/**
 * Video to course (R1-R4), the pure half (lib/videoToCourse.ts): the routes, the caption's default, the three
 * read-only lines (where it goes, the token, the setting), the one button's label, the run-again line and when
 * "just place again" is offered, the body, and what counts as a finished chain.
 */
import { describe, expect, it } from "vitest";
import {
  EXPORT_OFF_LINE, EXPORT_ON_LINE, NO_COURSE_LINE, NO_TOKEN_LINE, TOKEN_SET_LINE, VIDEO_TO_COURSE_JOB_KIND, type VideoToCourseDefaults, type VideoToCoursePlace,
  type VideoToCourseProject, againLine, buttonLabel, canPlaceAgain, canStart, captionDefault, exportLine, finished, isAgain, pasteLines, privacyWord, tokenLine,
  videoProjectUrl, videoToCourseBody, videoToCourseQueryKey, videoToCourseUrl, whereLine,
} from "./videoToCourse";

const COURSE = { course: "pdi-2hr-lab", slug: "01-first-win", title: "PDI in 2 Hours" };
const place = (over: Partial<VideoToCoursePlace> = {}): VideoToCoursePlace => ({
  how: "placeholder", line: 23, heading: "", guide: "pdi-2hr-lab/01-first-win/guide.md", existing_caption: null, caption: "Your First Win", placed: false, error: null, ...over,
});
const project = (over: Partial<VideoToCourseProject> = {}): VideoToCourseProject => ({
  id: "p1", name: "Your First Win", created_at: "2026-10-31T13:00:00Z", rendered_at: "2026-10-31T14:02:00Z", has_video: true,
  vimeo: { id: "123", link: "https://vimeo.com/123/abc", privacy: "unlisted", published_at: "2026-10-31T14:10:00Z", previous: [] }, placed: true, guide: "pdi-2hr-lab/01-first-win/guide.md", ...over,
});

describe("the routes and the defaults", () => {
  it("names the route, the key and the job kind", () => {
    expect(videoToCourseUrl("abc123abc123")).toBe("/api/walkthroughs/abc123abc123/video-to-course");
    expect(videoToCourseQueryKey("abc123abc123")).toEqual(["walkthroughs", "abc123abc123", "video-to-course"]);
    expect(VIDEO_TO_COURSE_JOB_KIND).toBe("video_to_course");
    expect(videoProjectUrl({ project_id: "p9" })).toBe("/projects/p9");
  });

  it("prefills the caption as Watch: <the name>, once", () => {
    expect(captionDefault(" Your First Win ")).toBe("Watch: Your First Win");
    expect(captionDefault("Watch: already")).toBe("Watch: already");
    expect(captionDefault("")).toBe("Watch: Walkthrough");
  });
});

describe("the three lines", () => {
  it("say where the link goes, by the plan's how", () => {
    expect(whereLine(COURSE, place())).toBe("pdi-2hr-lab / 01-first-win: the Walkthrough placeholder at line 23 becomes the video figure.");
    expect(whereLine(COURSE, place({ how: "figure", line: 8 }))).toBe("pdi-2hr-lab / 01-first-win: the existing figure's link at line 8 is replaced.");
    expect(whereLine(COURSE, place({ how: "insert", line: 5, heading: "## 1. Open it" }))).toBe('pdi-2hr-lab / 01-first-win: no placeholder: the figure is inserted after "## 1. Open it" (line 5).');
    expect(whereLine(COURSE, place({ how: "insert", line: 0, heading: "" }))).toBe("pdi-2hr-lab / 01-first-win: no placeholder: the figure is inserted at the top of the guide.");
    expect(whereLine(COURSE, place({ how: null, error: "No such workshop in that course: 01-first-win." }))).toBe("pdi-2hr-lab / 01-first-win: the guide could not be read (No such workshop in that course: 01-first-win.).");
    expect(whereLine(COURSE, null)).toBe("pdi-2hr-lab / 01-first-win: the guide could not be read.");
  });

  it("say the token's and the setting's state, and the no-course line", () => {
    expect(tokenLine(true)).toBe(TOKEN_SET_LINE);
    expect(tokenLine(false)).toBe(NO_TOKEN_LINE);
    expect(NO_TOKEN_LINE).toBe("No Vimeo token: set one in Settings › Studio first.");
    expect(exportLine(true)).toBe(EXPORT_ON_LINE);
    expect(exportLine(false)).toBe(EXPORT_OFF_LINE);
    expect(EXPORT_OFF_LINE).toContain("the lines to paste are shown at the end");
    expect(NO_COURSE_LINE).toBe("This walkthrough came from no course workshop: the video is rendered and published to Vimeo; copy its link where you need it.");
    expect(privacyWord("unlisted")).toBe("Unlisted");
    expect(privacyWord("")).toBe("");
  });
});

describe("the one button and the run again (R3)", () => {
  it("labels the button by the course and the again", () => {
    expect(buttonLabel(COURSE, false)).toBe("Render, publish and place");
    expect(buttonLabel(null, false)).toBe("Render and publish");
    expect(buttonLabel(COURSE, true)).toBe("Render, publish and place again");
    expect(buttonLabel(null, true)).toBe("Render and publish again");
  });

  it("knows a project with a video on Vimeo as an again, and says what it has", () => {
    expect(isAgain(null)).toBe(false);
    expect(isAgain(project({ vimeo: null }))).toBe(false);
    expect(isAgain(project({ has_video: false }))).toBe(false);
    expect(isAgain(project())).toBe(true);
    expect(againLine(project())).toMatch(/^Rendered 2026-10-31 \d\d:\d\d; on Vimeo as https:\/\/vimeo.com\/123\/abc; placed in pdi-2hr-lab\/01-first-win\/guide.md\.$/);
    expect(againLine(project({ placed: false }))).toContain("; not placed in the guide.");
    expect(againLine(project({ vimeo: null }))).toMatch(/^Rendered 2026-10-31 \d\d:\d\d; not on Vimeo yet\.$/);
    expect(againLine(project({ has_video: false, vimeo: null, rendered_at: null }))).toBe("Not rendered yet; not on Vimeo yet.");
  });

  it("offers just place again only for a video on Vimeo, a course, the setting on and a guide without the link", () => {
    const base: Pick<VideoToCourseDefaults, "course" | "project" | "allowed"> = { course: COURSE, project: project({ placed: false }), allowed: true };
    expect(canPlaceAgain(base)).toBe(true);
    expect(canPlaceAgain({ ...base, project: project({ placed: true }) })).toBe(false);
    expect(canPlaceAgain({ ...base, course: null })).toBe(false);
    expect(canPlaceAgain({ ...base, allowed: false })).toBe(false);
    expect(canPlaceAgain({ ...base, project: project({ vimeo: null, placed: false }) })).toBe(false);
    expect(canPlaceAgain({ ...base, project: null })).toBe(false);
  });

  it("starts only with a token, a name and nothing in flight", () => {
    expect(canStart({ token_set: true }, "Tour", false)).toBe(true);
    expect(canStart({ token_set: false }, "Tour", false)).toBe(false);
    expect(canStart({ token_set: true }, "  ", false)).toBe(false);
    expect(canStart({ token_set: true }, "Tour", true)).toBe(false);
    expect(canStart(null, "Tour", false)).toBe(false);
  });
});

describe("the body and the result", () => {
  it("trims the name and the caption and carries the mode", () => {
    expect(videoToCourseBody({ name: " Tour ", notes: true, zoom: false, privacy: "anybody", caption: " Watch: it ", welcome: true }, "full"))
      .toEqual({ name: "Tour", notes: true, zoom: false, privacy: "anybody", caption: "Watch: it", welcome: true, mode: "full" });
    expect(videoToCourseBody({ name: "T", notes: false, zoom: true, privacy: "unlisted", caption: "c".repeat(250), welcome: false }, "place").caption).toHaveLength(200);
  });

  it("knows a finished chain from a cancel, and joins the lines to paste", () => {
    const result = { project_id: "p1", link: "https://vimeo.com/1/h", course: COURSE, placed: true, guide: "g", written: ["g"], lines: [], allowed: true };
    expect(finished(result)).toBe(true);
    expect(finished({ ...result, cancelled: true, phase: "publish" as const })).toBe(false);
    expect(finished({ ...result, link: "" })).toBe(false);
    expect(finished(null)).toBe(false);
    expect(pasteLines({ lines: ["<figure>", "", "</figure>"] })).toBe("<figure>\n\n</figure>");
  });
});
