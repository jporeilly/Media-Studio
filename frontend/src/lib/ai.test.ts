import { describe, it, expect } from "vitest";
import type { Job } from "../components/project/JobProgress";
import {
  QA_CHUNK_SIZE,
  REWRITE_KINDS,
  aiBanner,
  canCancel,
  cancelTitle,
  chunkSizes,
  emptyNotesSlides,
  isAiJob,
  jobSummary,
  markFixed,
  noIssue,
  notesSlides,
  openIssues,
  pacingSummary,
  passesText,
  qaEntryFor,
  qaRows,
  voiceForLanguage,
  type AiStatus,
  type QaReview,
} from "./ai";
import type { Slide } from "./slides";
import type { Voice } from "./studioSettings";

const STATUS: AiStatus = {
  available: true,
  base_url: "http://localhost:11434",
  configured_model: "gemma3:12b",
  model_installed: true,
  models: ["gemma3:12b"],
  vision_capable: true,
  vision_usable: true,
  vision_reason: null,
  tones: [],
};

function slide(index: number, notes = ""): Slide {
  return {
    index,
    speaker_notes: notes,
    original_notes: notes,
    title: null,
    body_text: null,
    has_image: false,
    has_audio: false,
    ai_enhanced: false,
    needs_regeneration: false,
    voice_override: null,
    pause_override: null,
    alt_text: null,
    notes_history_depth: 0,
    has_animation: false,
  };
}

const REVIEW: QaReview = {
  score: 8,
  run_at: "2026-09-14T10:00:00+00:00",
  model: "gemma3:12b",
  issues: 3,
  slides: [
    { index: 0, grammar: "'deck' should be 'Deck'", tone: "None", flow: " n/a ", transitions: "No link to slide 2", fixed: [] },
    { index: 2, grammar: "none", tone: "Too casual", flow: "OK", transitions: "-", fixed: ["tone"] },
  ],
};

function job(kind: string, result: unknown, status = "done"): Job {
  return { id: "j1", kind, status, progress: 1, message: "Complete", result };
}

describe("banner", () => {
  it("says what the buttons can do", () => {
    expect(aiBanner(undefined)).toEqual({ text: "Checking Ollama…", tone: "muted" });
    expect(aiBanner(undefined, "A job is running for this project (generate).")).toEqual({
      text: "Could not check Ollama: A job is running for this project (generate). — AI actions disabled",
      tone: "bad",
    });
    expect(aiBanner(STATUS, "Internal Server Error").tone).toBe("bad");
    expect(aiBanner({ ...STATUS, available: false })).toEqual({ text: "Ollama unreachable at http://localhost:11434 — AI actions disabled", tone: "bad" });
    expect(aiBanner(STATUS)).toEqual({ text: "gemma3:12b · vision", tone: "ok" });
    expect(aiBanner({ ...STATUS, vision_usable: false, vision_reason: "the slide previews are the title-only fallback" })).toEqual({
      text: "gemma3:12b · vision off: the slide previews are the title-only fallback",
      tone: "warn",
    });
    expect(aiBanner({ ...STATUS, model_installed: false }).text).toBe(
      "gemma3:12b is not pulled on the Ollama server (ollama pull gemma3:12b) — AI actions will fail until it is",
    );
  });
  it("knows the AI job kinds, which rewrite notes and which can be cancelled", () => {
    expect(isAiJob("ai-notes")).toBe(true);
    expect(isAiJob("ai-qa-doc")).toBe(true);
    expect(isAiJob("render-slides")).toBe(false);
    expect(isAiJob(undefined)).toBe(false);
    expect([...REWRITE_KINDS]).toEqual(["ai-notes", "ai-enhance", "ai-tone", "ai-translate", "ai-pacing"]);
    expect(REWRITE_KINDS.has("ai-qa")).toBe(false);
    expect(canCancel("ai-enhance")).toBe(true);
    expect(canCancel("ai-qa")).toBe(true);
    expect(canCancel("ai-qa-doc")).toBe(false);
    expect(canCancel("generate")).toBe(false);
    expect(cancelTitle("ai-qa")).toContain("between review passes");
    expect(cancelTitle("ai-tone")).toContain("after the slide");
  });
});

describe("QA rows", () => {
  it("filters the no-issue spellings and keeps the criterion order", () => {
    expect(["None", "no issues", " N/A ", "-", "ok", "Good", ""].every(noIssue)).toBe(true);
    expect(noIssue("Missing comma")).toBe(false);
    expect(qaRows(REVIEW.slides[0])).toEqual([
      { criterion: "grammar", label: "Grammar", issue: "'deck' should be 'Deck'", fixed: false },
      { criterion: "transitions", label: "Transitions", issue: "No link to slide 2", fixed: false },
    ]);
    expect(qaRows(REVIEW.slides[1])).toEqual([{ criterion: "tone", label: "Tone", issue: "Too casual", fixed: true }]);
    expect(qaRows(undefined)).toEqual([]);
  });
  it("finds a slide's entry and counts the open issues", () => {
    expect(qaEntryFor(REVIEW, 2)?.tone).toBe("Too casual");
    expect(qaEntryFor(REVIEW, 1)).toBeUndefined();
    expect(qaEntryFor(null, 0)).toBeUndefined();
    expect(openIssues(REVIEW)).toBe(2);
    expect(openIssues(null)).toBe(0);
  });
  it("marks a criterion fixed without touching the input", () => {
    const fixed = markFixed(REVIEW, 0, "grammar");
    expect(fixed?.slides[0].fixed).toEqual(["grammar"]);
    expect(REVIEW.slides[0].fixed).toEqual([]);
    expect(openIssues(fixed)).toBe(1);
    expect(markFixed(fixed, 2, "tone")?.slides[1].fixed).toEqual(["tone"]);
    expect(markFixed(null, 0, "flow")).toBeNull();
  });
});

describe("slides", () => {
  it("splits the slides by whether they have notes", () => {
    const slides = [slide(0, "Welcome."), slide(1, "   "), slide(2, "")];
    expect(emptyNotesSlides(slides).map((s) => s.index)).toEqual([1, 2]);
    expect(notesSlides(slides).map((s) => s.index)).toEqual([0]);
  });
});

describe("voice matching", () => {
  const VOICES: Voice[] = [
    { voice_id: "en-US-AriaNeural", name: "Aria", locale: "en-US", gender: "Female" },
    { voice_id: "fr-CA-SylvieNeural", name: "Sylvie", locale: "fr-CA", gender: "Female" },
    { voice_id: "fr-FR-DeniseNeural", name: "Denise", locale: "fr-FR", gender: "Female" },
    { voice_id: "xx_odd", name: "Odd", locale: "", gender: null },
  ];
  it("takes the first voice whose locale language matches the subtag", () => {
    expect(voiceForLanguage(VOICES, "fr")?.voice_id).toBe("fr-CA-SylvieNeural");
    expect(voiceForLanguage(VOICES, "EN")?.voice_id).toBe("en-US-AriaNeural");
    expect(voiceForLanguage(VOICES, "de")).toBeUndefined();
    expect(voiceForLanguage(VOICES, "")).toBeUndefined();
    expect(voiceForLanguage([], "fr")).toBeUndefined();
  });
  it("never matches an id-only locale by accident", () => {
    expect(voiceForLanguage(VOICES, "xx_odd")).toBeDefined();
    expect(voiceForLanguage(VOICES, "xx")).toBeUndefined();
  });
});

describe("QA passes", () => {
  it("balances the chunks as the server does", () => {
    expect(chunkSizes(0)).toEqual([]);
    expect(chunkSizes(20)).toEqual([20]);
    expect(chunkSizes(21)).toEqual([11, 10]);
    expect(chunkSizes(38)).toEqual([19, 19]);
    expect(chunkSizes(41)).toEqual([14, 14, 13]);
    expect(chunkSizes(7, 3)).toEqual([3, 2, 2]);
    expect(QA_CHUNK_SIZE).toBe(20);
  });
  it("describes the passes", () => {
    expect(passesText(12)).toBe("1 pass (more when the notes are long)");
    expect(passesText(38)).toBe("2 passes of up to 20 slides (more when the notes are long)");
  });
});

describe("job summary", () => {
  it("reads the tally per kind", () => {
    expect(jobSummary(job("ai-notes", { total: 5, done: 4, failed: 1, skipped: 0 }))).toBe("Generated notes for 4 of 5 slides · 1 failed");
    expect(jobSummary(job("ai-enhance", { total: 3, done: 3, vision_reason: "the slide previews are the title-only fallback" }))).toBe(
      "Enhanced 3 of 3 slides (vision off: the slide previews are the title-only fallback)",
    );
    expect(jobSummary(job("ai-enhance", { total: 3, done: 1, cancelled: true }))).toBe("Cancelled — Enhanced 1 of 3 slides");
    expect(jobSummary(job("ai-tone", { total: 2, done: 1, unchanged: 1 }))).toBe("Adapted the tone of 1 of 2 slides · 1 unchanged");
    expect(jobSummary(job("ai-pacing", { total: 2, done: 2 }))).toBe("Paced 2 of 2 slides");
    expect(jobSummary(job("ai-qa", { score: 8, issues: 1, slides: 12 }))).toBe("QA score 8/10 — 1 issue on 12 slides");
    expect(jobSummary(job("ai-qa", { cancelled: true }))).toBe("QA review cancelled — nothing was saved");
    expect(jobSummary(job("ai-qa-doc", { pairs: 10 }))).toBe("Q&A document ready: 10 questions");
    expect(jobSummary(job("ai-other", null))).toBe("Complete");
  });
  it("names the voice a translation switched to, or that none matched", () => {
    const base = { total: 2, done: 2, language: "French", provider: "edge_tts", match_voice: true };
    expect(jobSummary(job("ai-translate", { ...base, suggested_voice_id: "fr-FR-DeniseNeural", suggested_voice_name: "Denise (fr-FR)" }))).toBe(
      "Translated 2 of 2 slides to French — narration voice switched to Denise (fr-FR)",
    );
    expect(jobSummary(job("ai-translate", { ...base, suggested_voice_id: null }))).toBe(
      "Translated 2 of 2 slides to French — no French voice in the Edge TTS list; the voice was kept",
    );
    expect(jobSummary(job("ai-translate", { ...base, match_voice: false }))).toBe("Translated 2 of 2 slides to French");
    expect(jobSummary(job("ai-translate", { ...base, cancelled: true }))).toBe("Cancelled — Translated 2 of 2 slides to French");
  });
  it("shows the error of a failed job and the rules pacing tally", () => {
    expect(jobSummary({ ...job("ai-qa", null, "error"), error: "returned 1 entries for 2 slides" })).toBe("returned 1 entries for 2 slides");
    expect(pacingSummary({ total: 3, done: 1, skipped: 1, unchanged: 1, adjustments: 4 })).toBe(
      "Pacing applied to 1 of 3 slides (4 adjustments) · 1 skipped · 1 unchanged",
    );
    expect(pacingSummary({ total: 1, done: 1, adjustments: 1 })).toBe("Pacing applied to 1 of 1 slides (1 adjustment)");
  });
});
