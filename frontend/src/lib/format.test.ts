import { describe, it, expect } from "vitest";
import { duration, timecode, titleCase } from "./format";

describe("titleCase", () => {
  it("splits on underscores and capitalises words", () => {
    expect(titleCase("import_video")).toBe("Import Video");
  });
  it("returns an empty string for nullish input", () => {
    expect(titleCase(null)).toBe("");
    expect(titleCase(undefined)).toBe("");
  });
});

describe("duration", () => {
  it("formats seconds as m:ss with a zero-padded seconds field", () => {
    expect(duration(0)).toBe("0:00");
    expect(duration(5)).toBe("0:05");
    expect(duration(65)).toBe("1:05");
    expect(duration(600)).toBe("10:00");
  });
  it("clamps negatives and rounds", () => {
    expect(duration(-10)).toBe("0:00");
    expect(duration(90.6)).toBe("1:31");
  });
});

describe("timecode", () => {
  it("keeps the milliseconds a transcript sentence is nudged by", () => {
    expect(timecode(0)).toBe("0:00.000");
    expect(timecode(5.25)).toBe("0:05.250");
    expect(timecode(65.004)).toBe("1:05.004");
    expect(timecode(600)).toBe("10:00.000");
  });
  it("clamps negatives and leaves duration() alone", () => {
    expect(timecode(-10)).toBe("0:00.000");
    // The two are deliberately different: duration() is for "how long is this".
    expect(duration(90.6)).toBe("1:31");
    expect(timecode(90.6)).toBe("1:30.600");
  });
  it("rounds to whole milliseconds first, so a carry rides into the minute", () => {
    // The remainder used to be rounded on its own: 59.9996 read "0:60.000" and
    // 119.9997 "1:60.000". Whole milliseconds first, then split, as the Python
    // twin (services/narration.py::timecode) does.
    expect(timecode(59.9996)).toBe("1:00.000");
    expect(timecode(119.9997)).toBe("2:00.000");
    expect(timecode(65.25)).toBe("1:05.250");
    expect(timecode(0)).toBe("0:00.000");
    expect(timecode(-1.5)).toBe("0:00.000");
  });
});
