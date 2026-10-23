/**
 * Workshop import (W2): the PURE half of the Import workshop dialog - what `GET /api/courses` and the preview
 * route answer, the one line the preview says ("12 steps in 4 chapters; 9 pictures from the guide, 3 to
 * capture"), the notice the builder opens with, the option labels. The dialog
 * (`components/walkthrough/ImportWorkshopDialog.tsx`) is the wiring; the parser is the server's
 * (`services/workshops.py`). Unit-tested in workshops.test.ts.
 *
 * W2e: several workshops of one course in one import. The ticks (`tickedInOrder`: the course's order whatever the
 * tick order; `sectionState` / `toggleSection`: a section's header tick), the name when left empty (`defaultName`,
 * ONE rule with the server's `default_name`), a workshop's preview steps grouped by the chapter they are in
 * (`chapterGroups`; an unticked chapter's steps are left out: `countsOf`, `combinedCounts`) and the body
 * (`importBody`: `workshops: [{slug, skip_chapters?}]`).
 */

export interface WorkshopInfo {
  slug: string;
  title: string;
  description: string;
  estimatedMinutes: number | null;
  stepCount: number | null;
}

export interface CourseSection { title: string; workshops: WorkshopInfo[] }

export interface CourseInfo {
  id: string;
  title: string;
  version: string;
  description: string;
  sections: CourseSection[];
}

/** `GET /api/courses`: `configured` false (with the message) when the courses folder is not set. */
export interface CoursesPayload { configured: boolean; message: string | null; courses: CourseInfo[] }

export interface PreviewStep {
  number: number;
  chapter: string;
  instruction: string;
  text: string;
  /** Whether the guide's picture was found in the course (else the step is captured in the desktop app). */
  picture: boolean;
  image: string | null;
  alt: string;
  launcher: { id: string; path: string | null; label: string } | null;
  note: boolean;
  /** The chapter the step is IN (W2e): what the ticks group by and `skip_chapters` names; "" before the first heading. */
  in_chapter: string;
}

export interface PreviewCounts { steps: number; chapters: number; pictures: number; to_capture: number }

/** Workshops one import may take (the server: `MAX_WORKSHOPS` in services/workshops.py). */
export const MAX_WORKSHOPS = 50;
/** The label of the chapter group before the first heading (its name, as the server names it, is ""). */
export const BEFORE_FIRST_SECTION = "(before the first section)";

export interface ImportWorkshopBody { slug: string; skip_chapters?: string[] }
/** `POST /api/walkthroughs/import` (W2e): the workshops in the course's order, each with the chapters to leave out, and the name when given. */
export interface ImportBody { course: string; workshops: ImportWorkshopBody[]; name?: string }

/** `GET /api/courses/{course}/workshops/{slug}/preview`: the parse, nothing written. */
export interface WorkshopPreview {
  course: { id: string; title: string; version: string };
  workshop: { slug: string; title: string; section: string; estimatedMinutes: number | null; stepCount: number | null };
  name: string;
  chapters: string[];
  steps: PreviewStep[];
  counts: PreviewCounts;
  message: string | null;
}

export const coursesQueryKey = ["courses"] as const;
export const previewQueryKey = (course: string, slug: string) => ["courses", course, slug, "preview"] as const;

export function previewUrl(course: string, slug: string): string {
  return `/api/courses/${encodeURIComponent(course)}/workshops/${encodeURIComponent(slug)}/preview`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** "12 steps in 4 chapters; 9 pictures from the guide, 3 to capture" - with the singulars, and the empty case said plainly. */
export function previewLine(counts: PreviewCounts): string {
  if (counts.steps === 0) return "No steps: the walkthrough will be empty.";
  const chapters = counts.chapters === 0 ? "no chapters" : plural(counts.chapters, "chapter");
  return `${plural(counts.steps, "step")} in ${chapters}; ${plural(counts.pictures, "picture")} from the guide, ${counts.to_capture} to capture`;
}

/** The option label of a workshop: its title with the minutes and the step count the manifest gives. */
export function workshopLabel(w: Pick<WorkshopInfo, "title" | "estimatedMinutes" | "stepCount">): string {
  const parts: string[] = [];
  if (w.estimatedMinutes !== null && w.estimatedMinutes !== undefined) parts.push(`${w.estimatedMinutes} min`);
  if (w.stepCount !== null && w.stepCount !== undefined) parts.push(plural(w.stepCount, "step"));
  return parts.length ? `${w.title} (${parts.join(" · ")})` : w.title;
}

/** The course option's label: the title with its version. */
export function courseLabel(c: Pick<CourseInfo, "title" | "version">): string {
  return c.version ? `${c.title} (${c.version})` : c.title;
}

/** The first workshop of a course (its first section's first), to preselect. */
export function firstWorkshop(course: CourseInfo | null | undefined): string | null {
  if (!course) return null;
  for (const section of course.sections) if (section.workshops.length) return section.workshops[0].slug;
  return null;
}

/** What the builder's status line says when it opens on a fresh import. */
export function importNotice(name: string, counts: PreviewCounts | null | undefined): string {
  return counts ? `Imported "${name}": ${previewLine(counts)}.` : `Imported "${name}".`;
}

// ---- W2e: several workshops ------------------------------------------------------------------------------

/** The ticked slugs in the COURSE's order, whatever the tick order: the previews, the blocks, the name and the import all follow it. */
export function tickedInOrder(course: CourseInfo | null | undefined, ticked: Iterable<string>): string[] {
  if (!course) return [];
  const set = new Set(ticked);
  return course.sections.flatMap((s) => s.workshops.map((w) => w.slug)).filter((slug) => set.has(slug));
}

export type SectionState = "all" | "some" | "none";

/** A section's header tick: all of its workshops ticked, some (shown indeterminate) or none. */
export function sectionState(section: CourseSection, ticked: Set<string>): SectionState {
  const n = section.workshops.filter((w) => ticked.has(w.slug)).length;
  return n === 0 ? "none" : n === section.workshops.length ? "all" : "some";
}

/** The ticks with every workshop of the section ticked (`on`) or unticked. */
export function toggleSection(ticked: Set<string>, section: CourseSection, on: boolean): Set<string> {
  const next = new Set(ticked);
  for (const w of section.workshops) {
    if (on) next.add(w.slug);
    else next.delete(w.slug);
  }
  return next;
}

/**
 * The walkthrough's name when left empty (E1) - ONE rule with the server's `default_name` (services/workshops.py):
 * one workshop, its title; every workshop of the course, the course's title; every workshop of exactly one section
 * (that has a title), the section's title; otherwise "<first workshop's title> + N more" - the first in the COURSE's
 * order, whatever the tick order. "" for none.
 */
export function defaultName(course: CourseInfo | null | undefined, ticked: Iterable<string>): string {
  if (!course) return "";
  const chosen = new Set(ticked);
  const every = course.sections.flatMap((s) => s.workshops);
  const picked = every.filter((w) => chosen.has(w.slug));
  if (picked.length === 0) return "";
  if (picked.length === 1) return picked[0].title;
  if (picked.length === every.length) return course.title;
  for (const section of course.sections) {
    const own = section.workshops.map((w) => w.slug);
    if (own.length && section.title && own.length === picked.length && own.every((slug) => chosen.has(slug))) return section.title;
  }
  return `${picked[0].title} + ${picked.length - 1} more`;
}

export interface ChapterGroup { name: string; steps: PreviewStep[] }

/** A workshop's preview steps grouped by the chapter they are in (`in_chapter`), in the guide's order; "" is the group before the first heading. */
export function chapterGroups(steps: PreviewStep[]): ChapterGroup[] {
  const out: ChapterGroup[] = [];
  for (const s of steps) {
    const last = out[out.length - 1];
    if (last && last.name === s.in_chapter) last.steps.push(s);
    else out.push({ name: s.in_chapter, steps: [s] });
  }
  return out;
}

export function chapterLabel(name: string): string {
  return name || BEFORE_FIRST_SECTION;
}

/**
 * The counts of the steps kept - those of the chapters not left out: what the workshop's line and the combined line
 * say. With several workshops ticked (`namedByWorkshop`) the server starts a chapter named by the workshop at a
 * workshop's first step when that step starts none (`combine_steps`), so that chapter is counted here too - the lines
 * then say the import's own numbers; for one workshop nothing is added.
 */
export function countsOf(steps: PreviewStep[], skipped: Iterable<string>, namedByWorkshop = false): PreviewCounts {
  const skip = new Set(skipped);
  const kept = steps.filter((s) => !skip.has(s.in_chapter));
  const pictures = kept.filter((s) => s.picture).length;
  const named = namedByWorkshop && kept.length > 0 && !kept[0].chapter ? 1 : 0;
  return { steps: kept.length, chapters: kept.filter((s) => s.chapter).length + named, pictures, to_capture: kept.length - pictures };
}

/** The workshops' counts summed: the one combined line. */
export function combinedCounts(counts: PreviewCounts[]): PreviewCounts {
  return counts.reduce(
    (sum, c) => ({ steps: sum.steps + c.steps, chapters: sum.chapters + c.chapters, pictures: sum.pictures + c.pictures, to_capture: sum.to_capture + c.to_capture }),
    { steps: 0, chapters: 0, pictures: 0, to_capture: 0 },
  );
}

/** The combined line's counts over the ticked workshops' previews: each one's `countsOf`, the workshop-named chapter counted when there are several, summed. */
export function combinedCountsOf(blocks: { steps: PreviewStep[]; skips: Iterable<string> }[]): PreviewCounts {
  return combinedCounts(blocks.map((b) => countsOf(b.steps, b.skips, blocks.length > 1)));
}

/** The import's body: the workshops in the course's order (`slugs`), each with the chapters to leave out when any, and the name when given. */
export function importBody(course: string, slugs: string[], skipped: Record<string, string[]>, name: string): ImportBody {
  const workshops = slugs.map((slug) => (skipped[slug]?.length ? { slug, skip_chapters: [...skipped[slug]] } : { slug }));
  const trimmed = name.trim();
  return trimmed ? { course, workshops, name: trimmed } : { course, workshops };
}
