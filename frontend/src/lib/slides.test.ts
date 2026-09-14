import { describe, it, expect } from "vitest";
import {
  BODY_PREVIEW_CHARS,
  bodyPreview,
  bulkBody,
  clampIndex,
  dirtySlides,
  draftNotes,
  dropDrafts,
  dropSettled,
  hasNotes,
  imageUrl,
  imagesNotice,
  isDirty,
  isEdited,
  isSlidesPayload,
  needsCollapse,
  needsRenderAgain,
  parsePause,
  pauseText,
  renderHint,
  slideLabel,
  slidesSubtitle,
  stepFromKey,
  type Slide,
} from "./slides";

function slide(index: number, notes = "", original = notes, extra: Partial<Slide> = {}): Slide {
  return {
    index,
    speaker_notes: notes,
    original_notes: original,
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
    ...extra,
  };
}

const SLIDES = [slide(0, "Welcome.", "Welcome."), slide(1, "Edited.", "Original."), slide(2, "", "")];

describe("dirty and edited", () => {
  it("a draft counts only when it differs from the saved notes", () => {
    expect(isDirty(SLIDES[0], {})).toBe(false);
    expect(isDirty(SLIDES[0], { 0: "Welcome." })).toBe(false);
    expect(isDirty(SLIDES[0], { 0: "Welcome!" })).toBe(true);
    expect(draftNotes(SLIDES[0], { 0: "Welcome!" })).toBe("Welcome!");
    expect(draftNotes(SLIDES[0], {})).toBe("Welcome.");
  });
  it("edited means the saved notes differ from the deck's", () => {
    expect(SLIDES.map(isEdited)).toEqual([false, true, false]);
    expect(SLIDES.map(hasNotes)).toEqual([true, true, false]);
    expect(hasNotes(slide(3, "   "))).toBe(false);
  });
  it("lists the dirty slides and builds the bulk body in slide order", () => {
    const drafts = { 2: "Typed.", 0: "Welcome!", 1: "Edited." };
    expect(dirtySlides(SLIDES, drafts).map((s) => s.index)).toEqual([0, 2]);
    expect(bulkBody(SLIDES, drafts)).toEqual({ slides: [{ index: 0, speaker_notes: "Welcome!" }, { index: 2, speaker_notes: "Typed." }] });
    expect(bulkBody(SLIDES, {})).toEqual({ slides: [] });
  });
  it("drops drafts by index, and settled ones after a bulk save", () => {
    expect(dropDrafts({ 0: "a", 1: "b" }, [1])).toEqual({ 0: "a" });
    expect(dropDrafts({ 0: "a" }, [5])).toEqual({ 0: "a" });
    const saved = [slide(0, "a"), slide(1, "b")];
    expect(dropSettled({ 0: "a", 1: "b typed further", 2: "c" }, saved)).toEqual({ 1: "b typed further", 2: "c" });
  });
});

describe("labels", () => {
  it("counts slides and edited notes for the subtitle", () => {
    expect(slidesSubtitle(SLIDES)).toBe("3 slides · notes edited on 1");
    expect(slidesSubtitle([slide(0)])).toBe("1 slide · notes edited on 0");
    expect(slidesSubtitle([])).toBe("0 slides · notes edited on 0");
  });
  it("names a slide by number and title", () => {
    expect(slideLabel(slide(2))).toBe("Slide 3");
    expect(slideLabel(slide(2, "", "", { title: "  Agenda " }))).toBe("Slide 3 · Agenda");
  });
  it("builds the image URL by index with a cache-busting version", () => {
    expect(imageUrl("abc", 4, "2026-09-14T10:00:00+00:00")).toBe("/api/projects/abc/slides/4/image?v=2026-09-14T10%3A00%3A00%2B00%3A00");
    expect(imageUrl("abc", 0, null)).toBe("/api/projects/abc/slides/0/image?v=");
  });
  it("only a Pillow fallback render gets a notice", () => {
    expect(imagesNotice("pillow", true)).toContain("title only");
    expect(imagesNotice("pillow", false)).toBeNull();
    expect(imagesNotice("powerpoint", true)).toBeNull();
    expect(imagesNotice("pdf", true)).toBeNull();
    expect(imagesNotice(null, true)).toContain("Render again");
    expect(imagesNotice(null, false)).toBeNull();
    expect(needsRenderAgain(null, true)).toBe(true);
    expect(needsRenderAgain(null, false)).toBe(false);
    expect(needsRenderAgain("powerpoint", true)).toBe(false);
  });
  it("names PowerPoint only for a deck's render hint", () => {
    expect(renderHint("deck")).toContain("PowerPoint");
    expect(renderHint("pdf")).not.toContain("PowerPoint");
    expect(renderHint("pdf")).toContain("PDF pages");
  });
  it("collapses long body text", () => {
    const long = "x".repeat(BODY_PREVIEW_CHARS + 1);
    expect(needsCollapse(long)).toBe(true);
    expect(needsCollapse("short")).toBe(false);
    expect(needsCollapse(null)).toBe(false);
    expect(bodyPreview(long)).toBe(`${"x".repeat(BODY_PREVIEW_CHARS)}…`);
    expect(bodyPreview("short")).toBe("short");
  });
});

describe("keyboard", () => {
  it("moves with the arrow keys outside fields only", () => {
    expect(stepFromKey("ArrowLeft", { tagName: "BODY" })).toBe(-1);
    expect(stepFromKey("ArrowRight", { tagName: "button" })).toBe(1);
    expect(stepFromKey("ArrowRight", null)).toBe(1);
    expect(stepFromKey("ArrowDown", { tagName: "BODY" })).toBe(0);
    expect(stepFromKey("ArrowRight", { tagName: "TEXTAREA" })).toBe(0);
    expect(stepFromKey("ArrowLeft", { tagName: "input" })).toBe(0);
    expect(stepFromKey("ArrowLeft", { tagName: "SELECT" })).toBe(0);
    expect(stepFromKey("ArrowLeft", { tagName: "DIV", isContentEditable: true })).toBe(0);
  });
  it("clamps the index to the deck", () => {
    expect(clampIndex(-1, 3)).toBe(0);
    expect(clampIndex(3, 3)).toBe(2);
    expect(clampIndex(1.7, 3)).toBe(1);
    expect(clampIndex(4, 0)).toBe(0);
  });
});

describe("pause", () => {
  it("blank is the default, a number of seconds within 0 to 30 is the override", () => {
    expect(parsePause("")).toEqual({ ok: true, value: null });
    expect(parsePause("  ")).toEqual({ ok: true, value: null });
    expect(parsePause("2.5")).toEqual({ ok: true, value: 2.5 });
    expect(parsePause("0")).toEqual({ ok: true, value: 0 });
    expect(parsePause("30")).toEqual({ ok: true, value: 30 });
    for (const bad of ["31", "-1", "soon", "1e400"]) {
      const parsed = parsePause(bad);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(parsed.message).toContain("between 0 and 30");
    }
  });
  it("shows the saved value or nothing", () => {
    expect(pauseText(null)).toBe("");
    expect(pauseText(undefined)).toBe("");
    expect(pauseText(1.5)).toBe("1.5");
    expect(pauseText(0)).toBe("0");
  });
});

describe("isSlidesPayload", () => {
  it("accepts the real body and refuses index.html or a bare list", () => {
    expect(isSlidesPayload({ slides: [], images_source: null, images_rendered_at: null, slides_ready: false })).toBe(true);
    expect(isSlidesPayload("<!doctype html>")).toBe(false);
    expect(isSlidesPayload({ slides: [] })).toBe(false);
    expect(isSlidesPayload(null)).toBe(false);
  });
});
