import { inRange, type Option, type Range, type StudioOptions, type StudioSettings } from "./studioSettings";

/** The subtitle modes POST /api/projects/{id}/generate accepts (api/schemas.py SubtitleMode). */
export type SubtitleMode = "none" | "slide" | "whisper";

/** The per-render options of the Generate card's "More options" section: GenerateRequest minus the narration and preset. */
export interface GenerateOptions {
  slide_transition: string;
  transition_duration: number;
  transition_pause: number;
  intro_text: string;
  intro_subtitle: string;
  intro_duration: number;
  outro_text: string;
  outro_duration: number;
  watermark_text: string;
  watermark_position: string;
  watermark_opacity: number;
  subtitles: SubtitleMode;
  export_webm: boolean;
  export_gif: boolean;
  export_audio_only: boolean;
}

/** The narration and preset chosen at the top of the card. */
export interface GenerateBase {
  provider: string;
  voice_id: string;
  speed: number;
  preset: string;
}

/** A title card lasts 1 to 10 seconds (api/schemas.py); the studio has no default for it. */
export const CARD_DURATION: Range = { min: 1, max: 10, step: 0.5, unit: "seconds" };

/** How long a "Preview" render is. */
export const PREVIEW_SECONDS = 15;

export const SUBTITLE_OPTIONS: Option[] = [
  { value: "none", label: "None", note: "No subtitle file." },
  { value: "slide", label: "Per slide", note: "One cue per slide from its notes, timed with the narration (an SRT file)." },
  {
    value: "whisper",
    label: "Whisper, word-level",
    note: "SRT and VTT with word timings. A second pass: Whisper transcribes the rendered video after the render, so the job takes longer.",
  },
];

/**
 * The options a fresh Generate card starts from: the studio defaults (Settings › Studio) for the transition, the
 * pause and the watermark, the engine's own for the rest (no cards, per-slide subtitles, no extra formats).
 */
export function optionsFromStudio(settings: StudioSettings): GenerateOptions {
  return {
    slide_transition: settings.slide_transition,
    transition_duration: settings.transition_duration,
    transition_pause: settings.transition_pause,
    intro_text: "",
    intro_subtitle: "",
    intro_duration: 3,
    outro_text: "",
    outro_duration: 3,
    watermark_text: settings.watermark_text,
    watermark_position: settings.watermark_position,
    watermark_opacity: settings.watermark_opacity,
    subtitles: "slide",
    export_webm: false,
    export_gif: false,
    export_audio_only: false,
  };
}

/**
 * The POST body: the narration and preset plus every option, text trimmed. `previewSeconds` > 0 asks for a
 * short separate render (the `preview` output) instead of the full video.
 */
export function generateBody(base: GenerateBase, options: GenerateOptions, previewSeconds = 0) {
  return {
    ...base,
    slide_transition: options.slide_transition,
    transition_duration: options.transition_duration,
    transition_pause: options.transition_pause,
    intro_text: options.intro_text.trim(),
    intro_subtitle: options.intro_subtitle.trim(),
    intro_duration: options.intro_duration,
    outro_text: options.outro_text.trim(),
    outro_duration: options.outro_duration,
    watermark_text: options.watermark_text.trim(),
    watermark_position: options.watermark_position,
    watermark_opacity: options.watermark_opacity,
    subtitles: options.subtitles,
    export_webm: options.export_webm,
    export_gif: options.export_gif,
    export_audio_only: options.export_audio_only,
    preview_seconds: previewSeconds,
  };
}

/**
 * Why the options cannot be sent yet, one readable sentence each; [] when they can. The ranges come from the
 * studio options (the same bounds the server enforces); a range not known yet is left to the server.
 */
export function optionProblems(options: GenerateOptions, studio: StudioOptions | undefined): string[] {
  const problems: string[] = [];
  const check = (value: number, range: Range | undefined, label: string) => {
    if (!range || inRange(value, range)) return;
    problems.push(`${label} must be between ${range.min} and ${range.max}${range.unit === "seconds" ? " seconds" : ""}.`);
  };
  check(options.transition_duration, studio?.transition_duration, "The transition duration");
  check(options.transition_pause, studio?.transition_pause, "The pause between slides");
  check(options.intro_duration, CARD_DURATION, "The intro card duration");
  check(options.outro_duration, CARD_DURATION, "The outro card duration");
  check(options.watermark_opacity, studio?.watermark_opacity, "The watermark opacity");
  return problems;
}

/** What the collapsed "More options" line says: the choices that differ from a plain render, or "Defaults". */
export function optionsSummary(options: GenerateOptions, transitions: Option[]): string {
  const parts: string[] = [];
  if (options.slide_transition !== "none") {
    parts.push(transitions.find((t) => t.value === options.slide_transition)?.label ?? options.slide_transition);
  }
  if (options.intro_text.trim()) parts.push("intro card");
  if (options.outro_text.trim()) parts.push("outro card");
  if (options.watermark_text.trim()) parts.push("watermark");
  if (options.subtitles === "none") parts.push("no subtitles");
  if (options.subtitles === "whisper") parts.push("Whisper subtitles");
  if (options.export_webm) parts.push("WebM");
  if (options.export_gif) parts.push("GIF");
  if (options.export_audio_only) parts.push("MP3");
  return parts.length ? parts.join(" · ") : "Defaults";
}

/** The sidecar files a generate job records on the project (`outputs`), served by GET /api/projects/{id}/outputs/{kind}. */
export type OutputKind = "srt" | "vtt" | "webm" | "gif" | "mp3" | "preview";
export type Outputs = Partial<Record<OutputKind, string>>;

const DOWNLOADS: { kind: OutputKind; label: string }[] = [
  { kind: "srt", label: "Subtitles (SRT)" },
  { kind: "vtt", label: "Subtitles (VTT)" },
  { kind: "webm", label: "WebM" },
  { kind: "gif", label: "GIF" },
  { kind: "mp3", label: "Audio (MP3)" },
];

/** The download links to show for a project's outputs, in a fixed order. The preview is a player, not a link. */
export function downloadLinks(outputs: Outputs | undefined): { kind: OutputKind; label: string; filename: string }[] {
  if (!outputs) return [];
  return DOWNLOADS.flatMap(({ kind, label }) => {
    const filename = outputs[kind];
    return filename ? [{ kind, label, filename }] : [];
  });
}
