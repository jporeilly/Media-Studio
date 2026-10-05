import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Job } from "../components/project/JobProgress";
import type { Slide } from "./slides";
import type { Voice } from "./studioSettings";

/**
 * The AI assistant over the slide editor (services/ai_slides.py, api/routers/ai.py): the Ollama status the
 * card's banner shows, the QA review shapes, and the pure helpers the card's buttons, rows and result line
 * are built from.
 */

/** A tone preset the Tone action offers (core/tone_adapter.py), plus Custom. */
export interface AiTone {
  name: string;
  description: string;
}

/** GET /api/projects/{id}/ai/status. */
export interface AiStatus {
  available: boolean;
  base_url: string;
  configured_model: string;
  /** Whether the configured model is pulled on the server; null while Ollama is unreachable. */
  model_installed: boolean | null;
  models: string[];
  vision_capable: boolean;
  /** True when the model sees images AND this project's previews are real renders (not the title-only fallback). */
  vision_usable: boolean;
  vision_reason: string | null;
  tones: AiTone[];
}

export const QA_CRITERIA = ["grammar", "tone", "flow", "transitions"] as const;
export type QaCriterion = (typeof QA_CRITERIA)[number];
export const QA_LABELS: Record<QaCriterion, string> = { grammar: "Grammar", tone: "Tone", flow: "Flow", transitions: "Transitions" };

/** One reviewed slide: the model's text per criterion ("None" for nothing to report) and the criteria fixed since. */
export interface QaEntry {
  index: number;
  grammar: string;
  tone: string;
  flow: string;
  transitions: string;
  fixed: QaCriterion[];
}

/** The last QA review, as the project record keeps it (`qa_review` on GET /slides). */
export interface QaReview {
  score: number;
  run_at: string;
  model: string;
  issues: number;
  slides: QaEntry[];
}

/** The spellings the model uses for "nothing to report" (services/ai_slides.py NO_ISSUE). */
export const NO_ISSUE = new Set(["none", "no issues", "n/a", "-", "ok", "good", "no issue", ""]);

/** A QA review sends every note in one prompt; a deck is split into passes of at most this many slides. */
export const QA_CHUNK_SIZE = 20;

/** The job kinds the AI actions start all begin with this (ai-notes, ai-enhance, ai-qa, …). */
export const AI_JOB_PREFIX = "ai-";
export const CUSTOM_TONE = "Custom";

/** A target language as GET /api/languages lists it. */
export interface Language {
  name: string;
  subtag: string;
}

/** What POST /ai/analyze returns (core/slide_analyzer.py, plus whether the model's prose was available). */
export interface Analysis {
  overall_score: number;
  summary: string;
  slides: { index: number; score: number; issues: string[] }[];
  suggestions: string[];
  ai: boolean;
  model: string | null;
}

/** The tally every per-slide job (and the rules pacing) answers with. */
export interface AiTally {
  total?: number;
  done?: number;
  failed?: number;
  skipped?: number;
  unchanged?: number;
  errors?: string[];
  cancelled?: boolean;
  vision?: boolean;
  vision_reason?: string | null;
  adjustments?: number;
  // the QA review
  score?: number;
  issues?: number;
  slides?: number;
  passes?: number;
  saved?: boolean;
  // the Q&A document
  pairs?: number;
  qa_doc?: string;
  download?: string;
  // the translation
  language?: string;
  provider?: string;
  match_voice?: boolean;
  suggested_voice_id?: string | null;
  suggested_voice_name?: string | null;
}

export function aiStatusKey(projectId: string, version = "") {
  return ["ai-status", projectId, version] as const;
}

/**
 * The Ollama status for a project. `version` is anything that changes what the answer depends on (the
 * previews' source and readiness), so a render refreshes the vision verdict without a manual refetch.
 */
export function useAiStatus(projectId: string, enabled = true, version = "") {
  return useQuery({
    queryKey: aiStatusKey(projectId, version),
    queryFn: () => api.get<AiStatus>(`/api/projects/${projectId}/ai/status`),
    enabled: enabled && !!projectId,
    staleTime: 30_000,
  });
}

export function isAiJob(kind: string | null | undefined): boolean {
  return !!kind && kind.startsWith(AI_JOB_PREFIX);
}

/** The AI jobs that rewrite the notes: when one ends, the editor's drafts are stale and are dropped. */
export const REWRITE_KINDS = new Set(["ai-notes", "ai-enhance", "ai-tone", "ai-translate", "ai-pacing"]);

/** The Generate card's job: a deck or PDF's full render and its 15-second preview alike (services/processing.py). */
export const GENERATE_JOB_KIND = "generate";

/**
 * Whether Cancel does anything for a job kind: the per-slide loops and the QA review poll the flag, and so
 * does a render (the full video and the preview) at every stage; the Q&A document (one prompt) does not.
 */
export function canCancel(kind: string | null | undefined): boolean {
  return kind === GENERATE_JOB_KIND || (isAiJob(kind) && kind !== "ai-qa-doc");
}

/** What the Generate card's Cancel does (api/routers/projects.py `generate`): the render and the preview alike. */
export const RENDER_CANCEL_TITLE =
  "Stops the render or the preview: while the slides are exported or narrated, or the video is encoded, nothing is written and the previous video (or preview) stays as it was; once the new video is written - during the subtitles or the extra formats - it is kept, with the sidecars that were finished.";

/** What Cancel does, per kind. */
export function cancelTitle(kind: string | null | undefined): string {
  if (kind === GENERATE_JOB_KIND) return RENDER_CANCEL_TITLE;
  if (kind === "ai-qa") return "Stops between review passes; nothing is saved until every pass is done.";
  return "Stops after the slide the model is working on; what is written so far is kept.";
}

export type BannerTone = "muted" | "bad" | "warn" | "ok";

/** The one-line AI status the card shows above its toolbar (`error` is the status request's own failure). */
export function aiBanner(status: AiStatus | undefined, error?: string | null): { text: string; tone: BannerTone } {
  if (error) return { text: `Could not check Ollama: ${error} — AI actions disabled`, tone: "bad" };
  if (!status) return { text: "Checking Ollama…", tone: "muted" };
  if (!status.available) return { text: `Ollama unreachable at ${status.base_url} — AI actions disabled`, tone: "bad" };
  const model = status.configured_model;
  if (status.model_installed === false) {
    return { text: `${model} is not pulled on the Ollama server (ollama pull ${model}) — AI actions will fail until it is`, tone: "warn" };
  }
  if (status.vision_usable) return { text: `${model} · vision`, tone: "ok" };
  return { text: `${model} · vision off: ${status.vision_reason ?? "unavailable"}`, tone: "warn" };
}

/** True for a criterion text that reports nothing. */
export function noIssue(text: string | null | undefined): boolean {
  return NO_ISSUE.has((text ?? "").trim().toLowerCase());
}

export interface QaRow {
  criterion: QaCriterion;
  label: string;
  issue: string;
  fixed: boolean;
}

/** The issue rows of one reviewed slide, in criterion order: criteria with nothing to report are left out. */
export function qaRows(entry: QaEntry | undefined): QaRow[] {
  if (!entry) return [];
  return QA_CRITERIA.flatMap((criterion) => {
    const issue = entry[criterion];
    if (noIssue(issue)) return [];
    return [{ criterion, label: QA_LABELS[criterion], issue: issue.trim(), fixed: (entry.fixed ?? []).includes(criterion) }];
  });
}

export function qaEntryFor(review: QaReview | null | undefined, index: number): QaEntry | undefined {
  return review?.slides.find((entry) => entry.index === index);
}

/** The issues not fixed yet, across the whole review. */
export function openIssues(review: QaReview | null | undefined): number {
  if (!review) return 0;
  return review.slides.reduce((n, entry) => n + qaRows(entry).filter((row) => !row.fixed).length, 0);
}

/** The review with one criterion of one slide marked fixed (a new object; the input is left alone). */
export function markFixed(review: QaReview | null | undefined, index: number, criterion: QaCriterion): QaReview | null {
  if (!review) return null;
  return {
    ...review,
    slides: review.slides.map((entry) =>
      entry.index === index && !(entry.fixed ?? []).includes(criterion) ? { ...entry, fixed: [...(entry.fixed ?? []), criterion] } : entry,
    ),
  };
}

/** The slides without notes (what Generate notes fills). */
export function emptyNotesSlides(slides: Slide[]): Slide[] {
  return slides.filter((slide) => slide.speaker_notes.trim().length === 0);
}

/** The slides with notes (what a review, a tone, a translation or a pacing pass covers). */
export function notesSlides(slides: Slide[]): Slide[] {
  return slides.filter((slide) => slide.speaker_notes.trim().length > 0);
}

/**
 * The first voice whose locale's language is `subtag` ("fr" matches "fr-FR" and "fr-CA"), as
 * core.tts_provider.suggest_voice_for_language picks it on the server; undefined when the list has none.
 */
export function voiceForLanguage(voices: Voice[], subtag: string): Voice | undefined {
  const want = subtag.trim().toLowerCase();
  if (!want) return undefined;
  return voices.find((voice) => {
    const locale = voice.locale || voice.voice_id || "";
    return locale.split("-", 1)[0].trim().toLowerCase() === want;
  });
}

/**
 * How many slides each QA pass reviews: as few passes as possible, balanced (38 → 19 + 19), as the server splits
 * them by count - it also caps a pass by the length of its notes, so long notes mean more passes than this.
 */
export function chunkSizes(count: number, size = QA_CHUNK_SIZE): number[] {
  if (count <= 0) return [];
  const passes = Math.ceil(count / size);
  const base = Math.floor(count / passes);
  const extra = count % passes;
  return Array.from({ length: passes }, (_, i) => base + (i < extra ? 1 : 0));
}

/** "1 pass" or "2 passes of up to 20 slides (more when the notes are long)". */
export function passesText(count: number, size = QA_CHUNK_SIZE): string {
  const passes = chunkSizes(count, size).length;
  return passes <= 1 ? "1 pass (more when the notes are long)" : `${passes} passes of up to ${size} slides (more when the notes are long)`;
}

export function providerLabel(provider: string | null | undefined): string {
  return provider === "kokoro" ? "Kokoro" : provider === "edge_tts" ? "Edge TTS" : provider || "the";
}

function tallyTail(r: AiTally): string {
  const parts: string[] = [];
  if (r.failed) parts.push(`${r.failed} failed`);
  if (r.skipped) parts.push(`${r.skipped} skipped`);
  if (r.unchanged) parts.push(`${r.unchanged} unchanged`);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

/** The one line the card shows once an AI job is over (its kind and result decide the wording). */
export function jobSummary(job: Job): string {
  if (job.status === "error") return job.error || job.message || "The AI job failed.";
  const r: AiTally = (job.result && typeof job.result === "object" ? job.result : {}) as AiTally;
  const prefix = r.cancelled ? "Cancelled — " : "";
  const of = `${r.done ?? 0} of ${r.total ?? 0} slides`;
  const vision = r.vision_reason ? ` (vision off: ${r.vision_reason})` : "";
  switch (job.kind) {
    case "ai-notes":
      return `${prefix}Generated notes for ${of}${tallyTail(r)}${vision}`;
    case "ai-enhance":
      return `${prefix}Enhanced ${of}${tallyTail(r)}${vision}`;
    case "ai-tone":
      return `${prefix}Adapted the tone of ${of}${tallyTail(r)}`;
    case "ai-pacing":
      return `${prefix}Paced ${of}${tallyTail(r)}`;
    case "ai-translate": {
      let voice = "";
      if (r.match_voice && !r.cancelled) {
        voice = r.suggested_voice_id
          ? ` — narration voice switched to ${r.suggested_voice_name || r.suggested_voice_id}`
          : ` — no ${r.language ?? ""} voice in the ${providerLabel(r.provider)} list; the voice was kept`;
      }
      return `${prefix}Translated ${of} to ${r.language ?? "the language"}${tallyTail(r)}${voice}`;
    }
    case "ai-qa":
      if (r.cancelled) return "QA review cancelled — nothing was saved";
      return `QA score ${r.score ?? "?"}/10 — ${r.issues ?? 0} ${r.issues === 1 ? "issue" : "issues"} on ${r.slides ?? 0} slides`;
    case "ai-qa-doc":
      return `Q&A document ready: ${r.pairs ?? 0} ${r.pairs === 1 ? "question" : "questions"}`;
    default:
      return job.message || "Done";
  }
}

/** The line for the rules-only pacing (a sync call, not a job). */
export function pacingSummary(r: AiTally): string {
  const n = r.adjustments ?? 0;
  return `Pacing applied to ${r.done ?? 0} of ${r.total ?? 0} slides (${n} ${n === 1 ? "adjustment" : "adjustments"})${tallyTail(r)}`;
}
