import { describe, it, expect } from "vitest";
import {
  CARD_DURATION,
  downloadLinks,
  generateBody,
  optionProblems,
  optionsFromStudio,
  optionsSummary,
  type GenerateOptions,
} from "./generateOptions";
import type { Option, StudioOptions, StudioSettings } from "./studioSettings";

const STUDIO: StudioSettings = {
  tts_provider: "edge_tts",
  edge_tts_voice: "en-US-AriaNeural",
  kokoro_voice: "af_heart",
  kokoro_lang: "en-us",
  whisper_model: "",
  ollama_model: "gemma3:12b",
  output_folder: "C:\\renders",
  transition_pause: 0.8,
  music_volume: 0.25,
  slide_transition: "crossfade",
  transition_duration: 0.7,
  watermark_text: "ACME",
  watermark_position: "top-left",
  watermark_opacity: 0.3,
};

const TRANSITIONS: Option[] = [
  { value: "none", label: "None" },
  { value: "crossfade", label: "Crossfade / Dissolve" },
  { value: "zoom-in", label: "Zoom In" },
];

const RANGES: StudioOptions = {
  tts_provider: { options: [] },
  kokoro_lang: { options: [] },
  whisper_model: { options: [] },
  transition_pause: { min: 0, max: 5, step: 0.1, unit: "seconds" },
  music_volume: { min: 0, max: 1, step: 0.05 },
  slide_transition: { options: TRANSITIONS },
  transition_duration: { min: 0.1, max: 2, step: 0.1, unit: "seconds" },
  watermark_position: { options: [] },
  watermark_opacity: { min: 0.1, max: 1, step: 0.05 },
};

const BASE = { provider: "edge_tts", voice_id: "en-US-AriaNeural", speed: 1, preset: "youtube_1080p" };

describe("optionsFromStudio", () => {
  it("prefills the transition, pause and watermark from the studio and the rest from the engine defaults", () => {
    expect(optionsFromStudio(STUDIO)).toEqual({
      slide_transition: "crossfade",
      transition_duration: 0.7,
      transition_pause: 0.8,
      intro_text: "",
      intro_subtitle: "",
      intro_duration: 3,
      outro_text: "",
      outro_duration: 3,
      watermark_text: "ACME",
      watermark_position: "top-left",
      watermark_opacity: 0.3,
      subtitles: "slide",
      export_webm: false,
      export_gif: false,
      export_audio_only: false,
    });
  });
});

describe("generateBody", () => {
  const options: GenerateOptions = { ...optionsFromStudio(STUDIO), intro_text: "  Welcome ", watermark_text: " ", export_gif: true };

  it("sends the narration, every option (text trimmed) and a full render by default", () => {
    const body = generateBody(BASE, options);
    expect(body).toMatchObject({ ...BASE, slide_transition: "crossfade", intro_text: "Welcome", watermark_text: "", export_gif: true, subtitles: "slide" });
    expect(body.preview_seconds).toBe(0);
    expect(Object.keys(body)).toHaveLength(20);
  });
  it("asks for a preview with preview_seconds", () => {
    expect(generateBody(BASE, options, 15).preview_seconds).toBe(15);
  });
});

describe("optionProblems", () => {
  const ok = optionsFromStudio(STUDIO);

  it("is empty for values inside the ranges", () => {
    expect(optionProblems(ok, RANGES)).toEqual([]);
    expect(optionProblems({ ...ok, intro_duration: CARD_DURATION.max, outro_duration: CARD_DURATION.min }, RANGES)).toEqual([]);
  });
  it("names each value outside its range", () => {
    const problems = optionProblems({ ...ok, transition_duration: 3, transition_pause: -1, intro_duration: 0.5, outro_duration: 11, watermark_opacity: 0 }, RANGES);
    expect(problems).toEqual([
      "The transition duration must be between 0.1 and 2 seconds.",
      "The pause between slides must be between 0 and 5 seconds.",
      "The intro card duration must be between 1 and 10 seconds.",
      "The outro card duration must be between 1 and 10 seconds.",
      "The watermark opacity must be between 0.1 and 1.",
    ]);
  });
  it("leaves a range it does not know yet to the server", () => {
    expect(optionProblems({ ...ok, transition_duration: 3, intro_duration: 0 }, undefined)).toEqual([
      "The intro card duration must be between 1 and 10 seconds.",
    ]);
  });
});

describe("optionsSummary", () => {
  it("says Defaults for a plain render", () => {
    expect(optionsSummary({ ...optionsFromStudio(STUDIO), slide_transition: "none", watermark_text: "" }, TRANSITIONS)).toBe("Defaults");
  });
  it("lists the choices with the transition's label", () => {
    const options = { ...optionsFromStudio(STUDIO), intro_text: "Hi", subtitles: "whisper" as const, export_webm: true, export_audio_only: true };
    expect(optionsSummary(options, TRANSITIONS)).toBe("Crossfade / Dissolve · intro card · watermark · Whisper subtitles · WebM · MP3");
    expect(optionsSummary({ ...options, slide_transition: "wipe", subtitles: "none" }, TRANSITIONS)).toContain("wipe · intro card");
    expect(optionsSummary({ ...options, subtitles: "none" }, TRANSITIONS)).toContain("no subtitles");
  });
});

describe("downloadLinks", () => {
  it("lists the outputs present, in a fixed order, without the preview", () => {
    expect(downloadLinks({ mp3: "deck_audio.mp3", preview: "deck_preview.mp4", srt: "deck.whisper.srt" })).toEqual([
      { kind: "srt", label: "Subtitles (SRT)", filename: "deck.whisper.srt" },
      { kind: "mp3", label: "Audio (MP3)", filename: "deck_audio.mp3" },
    ]);
  });
  it("is empty without outputs", () => {
    expect(downloadLinks(undefined)).toEqual([]);
    expect(downloadLinks({})).toEqual([]);
  });
});
