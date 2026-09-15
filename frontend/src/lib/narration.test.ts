import { describe, it, expect } from "vitest";
import { numberChange, previewUrl, voiceChange } from "./narration";

describe("previewUrl", () => {
  it("sends the job's narration, and nothing about the sentence", () => {
    // The sentence's own voice and speed are the server's to apply on top: the
    // client must not re-derive that precedence, or it has to be kept in step
    // with the render by hand.
    expect(previewUrl("abc123", 4, { provider: "edge_tts", voice: "en-GB-RyanNeural", speed: 1.15 }))
      .toBe("/api/projects/abc123/transcript/4/preview?provider=edge_tts&voice=en-GB-RyanNeural&speed=1.15");
  });

  it("omits what has not been chosen yet, so the server uses the studio defaults", () => {
    expect(previewUrl("abc123", 0, { provider: "", voice: "", speed: 1 }))
      .toBe("/api/projects/abc123/transcript/0/preview?speed=1");
  });
});

describe("numberChange", () => {
  it("sends nothing when the box still holds what is saved", () => {
    expect(numberChange("offset", "-0.4", -0.4)).toBeNull();
    expect(numberChange("speed", "1.15", 1.15)).toBeNull();
    expect(numberChange("offset", "0", null)).toBeNull();
    expect(numberChange("speed", "", null)).toBeNull();
  });

  it("rounds to the three decimals the server stores", () => {
    expect(numberChange("offset", "-0.4004999", null)).toEqual({ value: -0.4 });
    expect(numberChange("speed", "1.2345", null)).toEqual({ value: 1.235 });
  });

  it("treats a zero offset as no nudge at all, so typing 0 clears it", () => {
    expect(numberChange("offset", "0", -0.4)).toEqual({ value: null });
    expect(numberChange("offset", "", -0.4)).toEqual({ value: null });
  });

  it("keeps an explicit speed of 1, which is not the same as no speed", () => {
    // A stored speed bypasses the per-sentence fitting rule AND the squeeze, so
    // 1.0 means "exactly this rate" while blank means "whatever the re-voice
    // runs at, sped up to fit if it has to".
    expect(numberChange("speed", "1", null)).toEqual({ value: 1 });
    expect(numberChange("speed", "", 1)).toEqual({ value: null });
  });

  it("leaves what is saved alone when the box cannot be read as a number", () => {
    expect(numberChange("offset", "later", -0.4)).toBeNull();
    expect(numberChange("speed", "fast", 1.15)).toBeNull();
  });
});

describe("voiceChange", () => {
  it("sends nothing when the box still holds what is saved", () => {
    expect(voiceChange("en-GB-RyanNeural", "en-GB-RyanNeural")).toBeNull();
    expect(voiceChange("  ", null)).toBeNull();
  });

  it("clears the override when the box is emptied", () => {
    expect(voiceChange("", "en-GB-RyanNeural")).toEqual({ voice: null });
  });

  it("trims what was typed", () => {
    expect(voiceChange("  af_heart ", null)).toEqual({ voice: "af_heart" });
  });
});
