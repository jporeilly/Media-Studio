import { describe, expect, it } from "vitest";
import {
  type PlacePlan, canPlace, canPublish, descriptionCounter, onVimeoLine, placeFilesLine, placeLine, placeSettingLine, placeWriteBody,
  previousLine, privacyLabel, publishBody, publishUrl, placePlanUrl, placeUrl, vimeoUrl,
} from "./vimeo";

const PRIVACIES = [
  { value: "unlisted", label: "Unlisted", note: "Plus" },
  { value: "private", label: "Private", note: "Plus" },
  { value: "anybody", label: "Anybody", note: "free" },
];

describe("the routes", () => {
  it("are the project's vimeo routes", () => {
    expect(vimeoUrl("p1")).toBe("/api/projects/p1/vimeo");
    expect(publishUrl("p1")).toBe("/api/projects/p1/vimeo/publish");
    expect(placePlanUrl("p1")).toBe("/api/projects/p1/vimeo/place/plan");
    expect(placeUrl("p1")).toBe("/api/projects/p1/vimeo/place");
  });
});

describe("the panel's lines", () => {
  it("names the privacy by the server's list, else by the fallback, else capitalised", () => {
    expect(privacyLabel("unlisted", PRIVACIES)).toBe("Unlisted");
    expect(privacyLabel("private")).toBe("Private");
    expect(privacyLabel("password")).toBe("Password");
    expect(privacyLabel("")).toBe("");
  });

  it("says where the video is", () => {
    expect(onVimeoLine({ link: "https://vimeo.com/123/abc", privacy: "unlisted" }, PRIVACIES)).toBe("On Vimeo: https://vimeo.com/123/abc (Unlisted)");
    expect(onVimeoLine({ link: "https://vimeo.com/123", privacy: "anybody" })).toBe("On Vimeo: https://vimeo.com/123 (Anybody)");
  });

  it("counts the videos published before", () => {
    expect(previousLine({ previous: [] })).toBeNull();
    expect(previousLine({})).toBeNull();
    expect(previousLine({ previous: ["1"] })).toBe("Published before: an earlier video stays on Vimeo (its link may be in a course).");
    expect(previousLine({ previous: ["1", "2"] })).toBe("Published before: 2 earlier videos stay on Vimeo (their links may be in a course).");
  });
});

describe("the publish body", () => {
  it("trims the title and cuts both to the limits; Publish needs a token and a title", () => {
    const limits = { name_max: 5, description_max: 8 };
    expect(publishBody("  Tour  ", "123456789", "unlisted", limits)).toEqual({ title: "Tour", description: "12345678", privacy: "unlisted" });
    expect(publishBody("A very long title", "", "anybody", limits).title).toBe("A ver");
    expect(canPublish({ token_set: true }, " x ")).toBe(true);
    expect(canPublish({ token_set: true }, "   ")).toBe(false);
    expect(canPublish({ token_set: false }, "x")).toBe(false);
    expect(canPublish(null, "x")).toBe(false);
    expect(descriptionCounter("abc", 5000)).toBe("3 / 5,000");
  });
});

const plan = (over: Partial<PlacePlan> = {}): PlacePlan => ({
  course: "demo", course_title: "Demo", slug: "01-first", workshop: "First", guide: "demo/01-first/guide.md", how: "placeholder", line: 12,
  heading: "", caption: "User Console", existing_caption: null, link: "https://vimeo.com/123/abc", lines: ["<figure>"], diff: "+x", welcome: false,
  insert: false, files: [{ path: "01-first/guide.md", action: "write" }, { path: "01-first/manifest.json", action: "unchanged" }], changed: true,
  guide_sha256: "a".repeat(64), manifest_sha256: "b".repeat(64), course_sha256: null, manifest: "demo/01-first/manifest.json", allowed: true, ...over,
});

describe("the place dialog's lines", () => {
  it("says where the link goes, per how", () => {
    expect(placeLine(plan())).toBe("The Walkthrough placeholder at line 12 of demo/01-first/guide.md becomes the video figure.");
    expect(placeLine(plan({ how: "figure", line: 8 }))).toBe("The video figure at line 8 of demo/01-first/guide.md gets this link.");
    expect(placeLine(plan({ how: "insert", line: 5, heading: "## 1. Open it" }))).toBe('The figure goes after "## 1. Open it" (line 5) of demo/01-first/guide.md.');
    expect(placeLine(plan({ how: "insert", line: 0, heading: "" }))).toBe("The figure goes at the top of demo/01-first/guide.md (it has no heading).");
    // The embedded player's round: a guide with the interactive walkthrough's figure takes the video ABOVE it.
    expect(placeLine(plan({ how: "insert", line: 7, heading: "", above_walkthrough: true }))).toBe("The figure goes above the interactive walkthrough (line 7) of demo/01-first/guide.md.");
    expect(placeLine(plan({ how: "none" }))).toBe("demo/01-first/guide.md has no Walkthrough placeholder and no video figure of ours.");
  });

  it("says what the setting allows, counts the files, and knows when Place does anything", () => {
    expect(placeSettingLine(true)).toContain("is on");
    expect(placeSettingLine(false)).toContain("copy the lines into the guide by hand");
    expect(placeFilesLine(plan().files)).toBe("1 file to write");
    expect(placeFilesLine([{ path: "a", action: "write" }, { path: "b", action: "write" }])).toBe("2 files to write");
    expect(placeFilesLine([{ path: "a", action: "unchanged" }])).toBe("Nothing to write: the guide already holds this video.");
    expect(canPlace(plan())).toBe(true);
    expect(canPlace(plan({ allowed: false }))).toBe(false);
    expect(canPlace(plan({ changed: false }))).toBe(false);
    expect(canPlace(plan({ how: "none" }))).toBe(false);
    expect(canPlace(null)).toBe(false);
  });

  it("sends the options with every sha256 from the preview", () => {
    expect(placeWriteBody(plan(), { caption: "x", welcome: true, insert: false })).toEqual({
      caption: "x", welcome: true, insert: false, guide_sha256: "a".repeat(64), manifest_sha256: "b".repeat(64), course_sha256: null,
    });
  });
});
