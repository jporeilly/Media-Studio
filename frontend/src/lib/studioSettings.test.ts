import { describe, it, expect } from "vitest";
import { changedSettings, defaultVoiceFor, inRange, isStudioPayload, pickVoice, withCurrent, type StudioSettings, type Voice } from "./studioSettings";

const SAVED: StudioSettings = {
  tts_provider: "edge_tts",
  edge_tts_voice: "en-US-AriaNeural",
  kokoro_voice: "af_heart",
  kokoro_lang: "en-us",
  whisper_model: "",
  ollama_model: "gemma3:12b",
  output_folder: "C:\\renders",
  transition_pause: 0,
  music_volume: 0.25,
  slide_transition: "none",
  transition_duration: 0.5,
  watermark_text: "",
  watermark_position: "bottom-right",
  watermark_opacity: 0.5,
};

const VOICES: Voice[] = [
  { voice_id: "en-GB-RyanNeural", name: "RyanNeural (en-GB, Male)", locale: "en-GB", gender: "Male" },
  { voice_id: "en-US-AriaNeural", name: "AriaNeural (en-US, Female)", locale: "en-US", gender: "Female" },
  { voice_id: "en-US-GuyNeural", name: "GuyNeural (en-US, Male)", locale: "en-US", gender: "Male" },
];

describe("changedSettings", () => {
  it("returns only the fields that differ", () => {
    expect(changedSettings(SAVED, { ...SAVED, tts_provider: "kokoro", music_volume: 0.5 })).toEqual({
      tts_provider: "kokoro",
      music_volume: 0.5,
    });
  });
  it("is empty when nothing changed", () => {
    expect(changedSettings(SAVED, { ...SAVED })).toEqual({});
  });
});

describe("defaultVoiceFor", () => {
  it("picks the provider's configured voice", () => {
    expect(defaultVoiceFor(SAVED, "edge_tts")).toBe("en-US-AriaNeural");
    expect(defaultVoiceFor(SAVED, "kokoro")).toBe("af_heart");
  });
  it("is empty until the settings are known", () => {
    expect(defaultVoiceFor(undefined, "kokoro")).toBe("");
  });
});

describe("pickVoice", () => {
  it("prefers the studio default when the list carries it", () => {
    expect(pickVoice(VOICES, "en-US-GuyNeural")).toBe("en-US-GuyNeural");
  });
  it("falls back to the first en-US voice, then the first voice", () => {
    expect(pickVoice(VOICES, "af_heart")).toBe("en-US-AriaNeural");
    expect(pickVoice([VOICES[0]], "")).toBe("en-GB-RyanNeural");
  });
  it("is empty for an empty list (the server then uses the studio default)", () => {
    expect(pickVoice([], "en-US-AriaNeural")).toBe("");
  });
});

describe("withCurrent", () => {
  it("maps voices to options", () => {
    expect(withCurrent(VOICES, "en-US-AriaNeural")).toEqual(VOICES.map((v) => ({ value: v.voice_id, label: v.name })));
  });
  it("keeps a current value the list lacks selectable, first", () => {
    const options = withCurrent(VOICES, "en-AU-NatashaNeural");
    expect(options[0]).toEqual({ value: "en-AU-NatashaNeural", label: "en-AU-NatashaNeural (not in the list)" });
    expect(options).toHaveLength(VOICES.length + 1);
  });
  it("says loading, not missing, while the list is still on its way", () => {
    expect(withCurrent([], "en-US-AriaNeural", true)).toEqual([{ value: "en-US-AriaNeural", label: "en-US-AriaNeural (loading…)" }]);
  });
  it("shows the bare id when no list arrived at all", () => {
    expect(withCurrent([], "af_heart")).toEqual([{ value: "af_heart", label: "af_heart" }]);
  });
  it("adds nothing for an empty current value", () => {
    expect(withCurrent([], "")).toEqual([]);
    expect(withCurrent([], "", true)).toEqual([]);
  });
});

describe("isStudioPayload", () => {
  it("accepts the settings + options shape", () => {
    expect(isStudioPayload({ settings: SAVED, options: {} })).toBe(true);
  });
  it("rejects an HTML body (a backend without the route), null and partial objects", () => {
    expect(isStudioPayload("<!doctype html><html></html>")).toBe(false);
    expect(isStudioPayload(null)).toBe(false);
    expect(isStudioPayload({ settings: SAVED })).toBe(false);
    expect(isStudioPayload({ options: {} })).toBe(false);
  });
});

describe("inRange", () => {
  const range = { min: 0, max: 5, step: 0.1 };
  it("accepts the bounds and rejects outside, NaN and infinities", () => {
    expect(inRange(0, range)).toBe(true);
    expect(inRange(5, range)).toBe(true);
    expect(inRange(5.1, range)).toBe(false);
    expect(inRange(-0.1, range)).toBe(false);
    expect(inRange(NaN, range)).toBe(false);
    expect(inRange(Infinity, range)).toBe(false);
  });
});
