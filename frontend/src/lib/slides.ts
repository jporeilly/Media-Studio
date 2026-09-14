import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { QaReview } from "./ai";

/**
 * The slide editor's data (services/slides.py): one entry per slide of a deck or PDF project, the engine's
 * own state (notes, overrides, undo depth, render flags) merged with the deck's title, body text and its
 * own notes. It never carries a path; images are fetched by index (`imageUrl`).
 */
export interface Slide {
  index: number;
  speaker_notes: string;
  original_notes: string;
  title: string | null;
  body_text: string | null;
  has_image: boolean;
  has_audio: boolean;
  ai_enhanced: boolean;
  needs_regeneration: boolean;
  voice_override: string | null;
  pause_override: number | null;
  alt_text: string | null;
  notes_history_depth: number;
  has_animation: boolean;
}

/** Where the previews came from: PowerPoint, the title-only Pillow fallback, a PDF's page renders, or unknown (null). */
export type ImagesSource = "powerpoint" | "pillow" | "pdf" | null;

/** The kinds of project that have slides. */
export type SlideProjectKind = "deck" | "pdf";

/** What GET /api/projects/{id}/slides returns. */
export interface SlidesPayload {
  slides: Slide[];
  images_source: ImagesSource;
  images_rendered_at: string | null;
  slides_ready: boolean;
  /** The last AI QA review (lib/ai.ts); null until one has run. Absent from a backend older than the AI assistant. */
  qa_review?: QaReview | null;
}

/** Unsaved notes by slide index; a slide without an entry shows its saved notes. */
export type Drafts = Record<number, string>;

/** The pause after a slide may be 0 to 30 seconds (api/schemas.py SlideUpdate). */
export const MAX_PAUSE_SECONDS = 30;

/** The job kind POST /slides/render starts. */
export const RENDER_JOB_KIND = "render-slides";

/** Body text longer than this is collapsed behind "Show more". */
export const BODY_PREVIEW_CHARS = 280;

export function slidesQueryKey(projectId: string) {
  return ["slides", projectId] as const;
}

/**
 * True for a real /slides body. A backend older than this route answers the path with the SPA's
 * index.html (HTTP 200), which the client would otherwise carry around as "data".
 */
export function isSlidesPayload(data: unknown): data is SlidesPayload {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return Array.isArray(d.slides) && typeof d.slides_ready === "boolean";
}

/** The slides of a deck or PDF project; materialises the engine project on the first read. */
export function useSlides(projectId: string, enabled = true) {
  return useQuery({
    queryKey: slidesQueryKey(projectId),
    queryFn: async () => {
      const data = await api.get<unknown>(`/api/projects/${projectId}/slides`);
      if (!isSlidesPayload(data)) {
        throw new Error("The backend did not return the slides — it may be running an older version; restart it and reload.");
      }
      return data;
    },
    enabled: enabled && !!projectId,
  });
}

/** The notes the editor shows: the slide's draft when one exists, else what is saved. */
export function draftNotes(slide: Slide, drafts: Drafts): string {
  return drafts[slide.index] ?? slide.speaker_notes;
}

/** True when the slide has a draft that differs from its saved notes (the unsaved dot). */
export function isDirty(slide: Slide, drafts: Drafts): boolean {
  const draft = drafts[slide.index];
  return draft !== undefined && draft !== slide.speaker_notes;
}

/** True when the saved notes differ from the deck's own (the pencil in the rail). */
export function isEdited(slide: Slide): boolean {
  return slide.speaker_notes !== slide.original_notes;
}

/** True when the slide has notes to narrate (the dot in the rail). */
export function hasNotes(slide: Slide): boolean {
  return slide.speaker_notes.trim().length > 0;
}

export function dirtySlides(slides: Slide[], drafts: Drafts): Slide[] {
  return slides.filter((slide) => isDirty(slide, drafts));
}

/** The bulk PATCH body: every dirty slide's draft, in slide order. */
export function bulkBody(slides: Slide[], drafts: Drafts): { slides: { index: number; speaker_notes: string }[] } {
  return { slides: dirtySlides(slides, drafts).map((slide) => ({ index: slide.index, speaker_notes: drafts[slide.index] })) };
}

/** The drafts without the given slides' (after a save, an undo or a reset the server's text is the text). */
export function dropDrafts(drafts: Drafts, indexes: number[]): Drafts {
  const out: Drafts = { ...drafts };
  for (const index of indexes) delete out[index];
  return out;
}

/**
 * The drafts that still differ from `saved` (after Save all): a draft equal to what the server now holds
 * is settled and dropped; one typed further while the save ran is kept.
 */
export function dropSettled(drafts: Drafts, saved: Slide[]): Drafts {
  const out: Drafts = {};
  for (const [key, draft] of Object.entries(drafts)) {
    const index = Number(key);
    const slide = saved.find((s) => s.index === index);
    if (!slide || slide.speaker_notes !== draft) out[index] = draft;
  }
  return out;
}

/** "12 slides · notes edited on 3" (the card subtitle). */
export function slidesSubtitle(slides: Slide[]): string {
  const n = slides.length;
  const edited = slides.filter(isEdited).length;
  return `${n} ${n === 1 ? "slide" : "slides"} · notes edited on ${edited}`;
}

/** "Slide 3" or "Slide 3 · Its title" (the rail tooltip and the stage caption). */
export function slideLabel(slide: Slide): string {
  const title = (slide.title ?? "").trim();
  return title ? `Slide ${slide.index + 1} · ${title}` : `Slide ${slide.index + 1}`;
}

/** The image of one slide, cache-busted by the render time: a re-render keeps the file name. */
export function imageUrl(projectId: string, index: number, version: string | null | undefined): string {
  return `/api/projects/${projectId}/slides/${index}/image?v=${encodeURIComponent(version ?? "")}`;
}

/** Where a key press happened, as much of the event target as the arrow-key rule needs. */
export interface KeyTarget {
  tagName?: string;
  isContentEditable?: boolean;
}

/** ← / → move between slides (-1 / +1) unless the key was pressed in a field; 0 otherwise. */
export function stepFromKey(key: string, target: KeyTarget | null | undefined): -1 | 0 | 1 {
  const tag = (target?.tagName ?? "").toUpperCase();
  if (target?.isContentEditable || tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return 0;
  if (key === "ArrowLeft") return -1;
  if (key === "ArrowRight") return 1;
  return 0;
}

export function clampIndex(index: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(Math.max(Math.trunc(index), 0), count - 1);
}

/** The pause field's text as the PATCH value: blank is the default (null), else 0 to 30 seconds. */
export function parsePause(text: string): { ok: true; value: number | null } | { ok: false; message: string } {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, value: null };
  const seconds = Number(trimmed);
  if (!Number.isFinite(seconds) || seconds < 0 || seconds > MAX_PAUSE_SECONDS) {
    return { ok: false, message: `The pause after a slide must be between 0 and ${MAX_PAUSE_SECONDS} seconds, or blank for the default.` };
  }
  return { ok: true, value: seconds };
}

export function pauseText(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

/**
 * A Pillow (fallback) render deserves a word: its previews are each slide's title on white, not the slide. So do
 * previews rendered before their source was recorded (the older decks): the AI's vision cannot use them until
 * they are rendered again ("Render again" in the card's actions).
 */
export function imagesNotice(source: ImagesSource, ready: boolean): string | null {
  if (!ready) return null;
  if (source === "pillow") {
    return "PowerPoint was not available on the server, so these previews show each slide's title only.";
  }
  if (source === null) {
    return "These previews were rendered before their source was recorded, so the AI cannot use them as images — Render again to enable vision.";
  }
  return null;
}

/** True when the previews exist but nobody recorded where they came from: the card offers "Render again". */
export function needsRenderAgain(source: ImagesSource, ready: boolean): boolean {
  return ready && source === null;
}

/** What rendering the previews costs, by kind: a deck goes through PowerPoint, a PDF's pages render in seconds. */
export function renderHint(kind: SlideProjectKind): string {
  return kind === "pdf"
    ? "Renders the PDF pages on the server — a few seconds."
    : "Uses PowerPoint on the server — a minute or two for a large deck.";
}

/** Why the editor is read-only while a job runs: the job holds its own copy of the slides and would overwrite the edit. */
export const BUSY_NOTICE = "A job is running for this project — the notes can be edited again when it finishes.";

/** Long body text is collapsed behind "Show more"; short text (or none) is shown as it is. */
export function needsCollapse(text: string | null | undefined): boolean {
  return (text ?? "").length > BODY_PREVIEW_CHARS;
}

export function bodyPreview(text: string): string {
  return text.length > BODY_PREVIEW_CHARS ? `${text.slice(0, BODY_PREVIEW_CHARS).trimEnd()}…` : text;
}
