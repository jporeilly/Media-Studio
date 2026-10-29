/**
 * The interactive player bundle (W2c, C7): how the page reads the walkthrough the server put in its data slot
 * (lib/playerBundle.ts). The built page itself is opened offline from the disk by tests/test_course_export.py.
 */
import { describe, expect, it } from "vitest";
import { bundleImageUrl, bundleTheme, readBundle } from "./playerBundle";

describe("readBundle", () => {
  it("is null for an empty slot, the template's null, text that is not JSON, or no steps", () => {
    for (const text of [null, undefined, "", "null", "{", "[]", "{\"name\":\"x\"}", "{\"steps\":{}}"]) expect(readBundle(text)).toBeNull();
  });

  it("types every step and takes a picture only by a file name beside the page", () => {
    const data = {
      name: "Tour",
      steps: [
        { id: "aaaaaaaaaaaa", image: "step-aaaaaaaaaaaa.png", width: 400, height: 300, hotspot: { x: 1, y: 2, width: 30, height: 20 }, kind: "modal", text: "One", instruction: "Do one", placement: "left", chapter: "Start" },
        { id: "bbbbbbbbbbbb", image: "../outside.png", width: 400, height: 300, hotspot: { x: 1, y: 2, width: 30, height: 20 }, kind: "odd", placement: "middle" },
        { id: "cccccccccccc", image: "https://example.com/step-cccccccccccc.png", width: 10, height: 10 },
        { image: null },
      ],
    };
    const got = readBundle(JSON.stringify(data))!;
    expect(got.name).toBe("Tour");
    expect(got.steps[0]).toEqual({
      id: "aaaaaaaaaaaa", image: "step-aaaaaaaaaaaa.png", width: 400, height: 300, hotspot: { x: 1, y: 2, width: 30, height: 20 }, kind: "modal",
      text: "One", instruction: "Do one", placement: "left", chapter: "Start", note: "", source: null, click: "left", marker: "box",
    });
    // A path that leaves the folder, or an address: no picture - and so no size and no hotspot either. An unknown
    // click kind or marker (F3, F4) reads as the defaults, as an unknown kind does.
    expect(got.steps[1]).toMatchObject({ image: null, width: 0, height: 0, hotspot: null, kind: "tooltip", placement: "auto", text: "", click: "left", marker: "box" });
    const typed = readBundle(JSON.stringify({ name: "T", steps: [{ id: "aaaaaaaaaaaa", image: "step-aaaaaaaaaaaa.png", width: 4, height: 4, click: "right", marker: "beacon" }, { click: "middle", marker: "pin" }] }))!;
    expect([typed.steps[0].click, typed.steps[0].marker, typed.steps[1].click, typed.steps[1].marker]).toEqual(["right", "beacon", "left", "box"]);
    expect(got.steps[2].image).toBeNull();
    expect(got.steps[3].id).toBe("step-4");
    expect(readBundle(JSON.stringify({ steps: [] }))!.name).toBe("Walkthrough");
  });

  it("points a step's picture at its file beside the page", () => {
    expect(bundleImageUrl({ image: "step-aaaaaaaaaaaa.png" })).toBe("step-aaaaaaaaaaaa.png");
    expect(bundleImageUrl({ image: null })).toBe("");
  });

  it("takes the viewer's theme, dark when it cannot tell", () => {
    expect(bundleTheme((q) => ({ matches: q === "(prefers-color-scheme: light)" }))).toBe("light");
    expect(bundleTheme(() => ({ matches: false }))).toBe("dark");
    expect(bundleTheme(undefined)).toBe("dark");
    expect(bundleTheme(() => { throw new Error("no"); })).toBe("dark");
  });
});
