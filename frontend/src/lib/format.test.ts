import { describe, it, expect } from "vitest";
import { duration, titleCase } from "./format";

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
