/**
 * Export to course (W2c): the PURE half of the builder's Export to course… dialog - what the plan route and the
 * write answer (`services/course_export.py plan_view` and `write`), the routes, the lines the dialog says. The
 * dialog (`components/walkthrough/ExportCourseDialog.tsx`) is the wiring. Unit-tested in courseExport.test.ts.
 *
 * The export writes each step with a picture Media Studio took into its course workshop's guide as a figure after
 * the step (the content manager's annotated image: the picture with a numbered pin at the hotspot), its picture
 * into the course's `_assets/images`; the plan shows it all and writes nothing; the write is the studio setting's
 * ("Allow export into the Courses folder", off by default) - with it off, the same result comes as a zip.
 *
 * W2e: a walkthrough of several workshops is planned per guide (`guides`, a `GuidePlan` each); the combined line counts
 * the guides (`planLine`), the write sends every guide's sha256 (`writeBody`), and the builder's button names them
 * (`exportTitle`, `guidesOf`).
 */
import type { WalkthroughSource } from "./walkthrough";

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

/** One guide of the plan (W2e): the workshop's own line, lists and diff - a walkthrough of several workshops has one per guide. */
export interface GuidePlan {
  slug: string;
  title: string;
  /** `<course>/<slug>/guide.md`. */
  guide: string;
  guide_sha256: string;
  placed: PlannedStep[];
  not_placed: UnplacedStep[];
  skipped: UnplacedStep[];
  removed: RemovedBlock[];
  new_in_guide: { text: string; line: number }[];
  kept: { path: string; reason: string }[];
  figures: number;
  /** Whether this guide's bytes change. */
  changed: boolean;
  /** This guide as a unified diff ("" when it does not change). */
  diff: string;
}

/** `POST /api/walkthroughs/{wid}/export/course/plan`: the dry run; nothing written. */
export interface ExportPlan {
  course: { id: string; title: string; version: string };
  /** The FIRST workshop (a page before W2e read one); `guides` has them all. */
  workshop: { slug: string; title: string };
  /** `<course>/<first slug>/guide.md`. */
  guide: string;
  guide_sha256: string;
  /** A block per guide, in the course's order (W2e); the flat lists below are theirs joined, the steps of no guide among `not_placed`. */
  guides: GuidePlan[];
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
  /** Per guide (W2e). */
  guides: { slug: string; guide: string; figures: number; guide_written: boolean; guide_sha256: string }[];
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

/**
 * "3 figures into demo/01-first/guide.md; 1 step not placed; 2 skipped" - the dialog's first line, and a guide's own
 * line; for a plan of several guides (W2e) the combined line counts them: "5 figures into 2 guides; 1 step not placed".
 */
export function planLine(plan: Pick<ExportPlan, "figures" | "guide" | "not_placed" | "skipped"> & { guides?: unknown[] }): string {
  const guides = plan.guides?.length ?? 1;
  const parts = [`${plural(plan.figures, "figure")} into ${guides > 1 ? plural(guides, "guide") : plan.guide}`];
  if (plan.not_placed.length) parts.push(`${plural(plan.not_placed.length, "step")} not placed`);
  if (plan.skipped.length) parts.push(`${plan.skipped.length} skipped`);
  return parts.join("; ");
}

/** The write's body (W2e): every guide's sha256 as the preview showed it, and whether the player goes too. */
export function writeBody(plan: Pick<ExportPlan, "guides">, player: boolean): { guides: { slug: string; sha256: string }[]; player: boolean } {
  return { guides: plan.guides.map((g) => ({ slug: g.slug, sha256: g.guide_sha256 })), player };
}

/**
 * The steps of no guide (W2e): a step whose workshop is none of the record's, or none at all - not placed when it has a
 * picture of ours, skipped (`skipped: true`, with the skip's reason) when it has none or the guide's own - each in no
 * guide's own list, in step order. The dialog lists them above the guides, so the first line's counts are all shown.
 */
export function stepsOfNoGuide(plan: Pick<ExportPlan, "not_placed" | "skipped" | "guides">): (UnplacedStep & { skipped: boolean })[] {
  const guides = plan.guides ?? [];
  const notPlacedIn = new Set(guides.flatMap((g) => g.not_placed.map((n) => n.step_id)));
  const skippedIn = new Set(guides.flatMap((g) => g.skipped.map((n) => n.step_id)));
  return [
    ...plan.not_placed.filter((n) => !notPlacedIn.has(n.step_id)).map((n) => ({ ...n, skipped: false })),
    ...plan.skipped.filter((n) => !skippedIn.has(n.step_id)).map((n) => ({ ...n, skipped: true })),
  ].sort((a, b) => a.number - b.number);
}

/** The workshops a walkthrough was imported from, in the course's order: `source.workshops` (W2e), else the one `slug` of a record before it. */
export function guidesOf(source: Pick<WalkthroughSource, "slug" | "workshops">): string[] {
  return source.workshops?.length ? source.workshops.map((w) => w.slug) : [source.slug];
}

/** The builder's Export button's title (E5): the guides the figures go into. */
export function exportTitle(source: WalkthroughSource | null | undefined): string {
  if (!source) return "Download the interactive player (the walkthrough was not imported from a course workshop)";
  return `Write the figures into ${source.course}/${guidesOf(source).join(", ")}, or download them; or download the interactive player`;
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
