/**
 * Export to course (W2c): the PURE half of the builder's Export to course… dialog - what the plan route and the
 * write answer (`services/course_export.py plan_view` and `write`), the routes, the lines the dialog says. The
 * dialog (`components/walkthrough/ExportCourseDialog.tsx`) is the wiring. Unit-tested in courseExport.test.ts.
 *
 * The export writes each step with a picture Media Studio took into its course workshop's guide as a figure after
 * the step (the content manager's annotated image: the picture with a numbered pin at the hotspot), its picture
 * into the course's `_assets/images`; the plan shows it all and writes nothing; the write is the studio setting's
 * ("Allow export into the Courses folder", off by default) - with it off, the same result comes as a zip.
 */

export interface PlannedStep {
  number: number;
  step_id: string;
  /** The guide's first sentence of the step it is placed at (a block already there: the step's own text). */
  text: string;
  /** The picture's file name in the course's _assets/images. */
  image: string;
  /** The pin, in percent of the picture with one decimal; null for a step without a hotspot (a plain figure). */
  hotspot: { x: string; y: string } | null;
  /** `new`: the guide line the figure goes after; otherwise the line of the block already there. */
  line: number;
  how: "new" | "replaced" | "unchanged";
}

export interface UnplacedStep { number: number; step_id: string; text: string; reason: string }
export interface RemovedBlock { step_id: string | null; line: number; reason: string }
export interface FileAction { path: string; action: "write" | "unchanged" | "remove" }

/** `POST /api/walkthroughs/{wid}/export/course/plan`: the dry run; nothing written. */
export interface ExportPlan {
  course: { id: string; title: string; version: string };
  workshop: { slug: string; title: string };
  /** `<course>/<slug>/guide.md`. */
  guide: string;
  guide_sha256: string;
  placed: PlannedStep[];
  not_placed: UnplacedStep[];
  skipped: UnplacedStep[];
  removed: RemovedBlock[];
  new_in_guide: { text: string; line: number }[];
  /** Pictures an earlier export left that another workshop's guide of the course still shows: kept, with why. */
  kept: { path: string; reason: string }[];
  /** Files an interrupted export left in the folders this one uses (its `.keep`/`.bak`/`.tmp` copies, a picture of
   *  this walkthrough no guide names): the write sweeps them. */
  debris: { path: string; reason: string }[];
  files: FileAction[];
  changed: boolean;
  figures: number;
  player: boolean;
  /** The guide as a unified diff ("" when it does not change). */
  diff: string;
  /** Whether the studio allows the write ("Allow export into the Courses folder"). */
  allowed: boolean;
}

/** `POST /api/walkthroughs/{wid}/export/course`: what was written. */
export interface ExportResult {
  course: string;
  slug: string;
  guide: string;
  figures: number;
  images: number;
  player: boolean;
  guide_written: boolean;
  written: string[];
  removed: string[];
  guide_sha256: string;
  message: string;
}

export const exportPlanQueryKey = (wid: string, player: boolean) => ["walkthroughs", wid, "export-plan", player] as const;

export function exportPlanUrl(wid: string): string {
  return `/api/walkthroughs/${wid}/export/course/plan`;
}

export function exportWriteUrl(wid: string): string {
  return `/api/walkthroughs/${wid}/export/course`;
}

/** The course zip (C6): the guide and the pictures laid out like the course folder, the player too when asked. */
export function courseZipUrl(wid: string, player: boolean): string {
  return `/api/walkthroughs/${wid}/export/course.zip${player ? "?player=1" : ""}`;
}

/** The interactive player as a zip (C7): any walkthrough. */
export function playerZipUrl(wid: string): string {
  return `/api/walkthroughs/${wid}/export/player.zip`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** "3 figures into demo/01-first/guide.md; 1 step not placed; 2 skipped" - the dialog's first line. */
export function planLine(plan: Pick<ExportPlan, "figures" | "guide" | "not_placed" | "skipped">): string {
  const parts = [`${plural(plan.figures, "figure")} into ${plan.guide}`];
  if (plan.not_placed.length) parts.push(`${plural(plan.not_placed.length, "step")} not placed`);
  if (plan.skipped.length) parts.push(`${plan.skipped.length} skipped`);
  return parts.join("; ");
}

/** Where a placed step's figure goes, said plainly. */
export function placedWhere(p: Pick<PlannedStep, "how" | "line">): string {
  if (p.how === "new") return `after line ${p.line}`;
  return p.how === "replaced" ? `rewritten at line ${p.line}` : `unchanged at line ${p.line}`;
}

/** "2 files to write, 1 to remove" - or "Nothing to write: the guide already holds these figures." */
export function filesLine(files: FileAction[]): string {
  const write = files.filter((f) => f.action === "write").length;
  const remove = files.filter((f) => f.action === "remove").length;
  if (!write && !remove) return "Nothing to write: the guide already holds these figures.";
  const parts = [];
  if (write) parts.push(`${plural(write, "file")} to write`);
  if (remove) parts.push(`${remove} to remove`);
  return parts.join(", ");
}

/** The builder asks before exporting unsaved changes: the export reads the saved walkthrough, as the player does. */
export const SAVE_BEFORE_EXPORT = {
  title: "Save before exporting?",
  message: "Export to course reads the walkthrough as it is saved. Save it now, then export?",
  confirmLabel: "Save and export",
};

/** What the dialog says of the studio setting, on or off. */
export function settingLine(allowed: boolean): string {
  return allowed
    ? "Allow export into the Courses folder is on: Write to the course writes these files into the course."
    : "Allow export into the Courses folder is off (Settings › Studio, an administrator): download the zip and copy it into the course by hand.";
}
