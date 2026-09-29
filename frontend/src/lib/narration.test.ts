import { describe, it, expect } from "vitest";
import {
  DOWNLOAD_FORMATS,
  adjustedSentences,
  numberChange,
  previewUrl,
  retranscribeEffects,
  transcriptDownloadUrl,
  voiceChange,
} from "./narration";

describe("transcriptDownloadUrl", () => {
  it("names the format and the view, so the link says what it fetches", () => {
    expect(transcriptDownloadUrl("abc123", "srt", "timeline"))
      .toBe("/api/projects/abc123/transcript/download?format=srt&view=timeline");
    expect(transcriptDownloadUrl("abc123", "json", "source"))
      .toBe("/api/projects/abc123/transcript/download?format=json&view=source");
  });

  it("offers the three files the server writes", () => {
    expect([...DOWNLOAD_FORMATS]).toEqual(["srt", "txt", "json"]);
  });
});

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

describe("adjustedSentences: what a new transcription drops", () => {
  const plain = { start: 0, end: 1, text: "Plain." };

  it("counts a sentence once whatever it carries", () => {
    expect(adjustedSentences([
      plain,
      { ...plain, offset: -0.4, speed: 1.2 },
      { ...plain, muted: true },
      { ...plain, voice: "en-GB-RyanNeural", provider: "edge_tts" },
      { ...plain, speed: 1 },
    ])).toBe(4);
  });

  it("does not count a value that is cleared, as the server stores none", () => {
    expect(adjustedSentences([
      { ...plain, offset: 0 },
      { ...plain, muted: false },
      { ...plain, voice: "", provider: null },
      { ...plain, speed: null },
    ])).toBe(0);
  });

  it("is 0 with no transcript", () => {
    expect(adjustedSentences(null)).toBe(0);
    expect(adjustedSentences([])).toBe(0);
  });
});

describe("retranscribeEffects: the Transcribe again dialog", () => {
  it("says the words, every adjustment and the undo history go, with the count", () => {
    const { loses } = retranscribeEffects(3);
    expect(loses[0]).toContain("words");
    expect(loses[1]).toContain("3 sentences have some");
    expect(loses[1]).toContain("none carry over");
    expect(loses[2]).toContain("undo history");
    expect(retranscribeEffects(1).loses[1]).toContain("1 sentence has some");
    expect(retranscribeEffects(0).loses[1]).toContain("none are set now");
  });

  it("says what is placed by time stays, and the last re-voice with it", () => {
    const { keeps } = retranscribeEffects(3);
    expect(keeps[0]).toContain("cuts, markers and music clips");
    expect(keeps[1]).toContain("re-voiced video");
  });
});
