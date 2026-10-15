/**
 * Workshop import (W2): the PURE half of the Import workshop dialog - what `GET /api/courses` and the preview
 * route answer, the one line the preview says ("12 steps in 4 chapters; 9 pictures from the guide, 3 to
 * capture"), the notice the builder opens with, the option labels. The dialog
 * (`components/walkthrough/ImportWorkshopDialog.tsx`) is the wiring; the parser is the server's
 * (`services/workshops.py`). Unit-tested in workshops.test.ts.
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
}

export interface PreviewCounts { steps: number; chapters: number; pictures: number; to_capture: number }

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
