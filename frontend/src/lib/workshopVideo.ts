/**
 * Workshop video - the one canvas (C1-C6): the PURE half of `pages/WorkshopVideo.tsx` - the five stages and which
 * may be opened, each stage's one-line status when it is collapsed, a workshop's status line from the records
 * (`GET /api/courses/{course}/workshop-status`), the Capture stage's count, the Publish stage's lines (the editor's
 * config, the diff's summary, the commit message, the published line) and the routes. The page is the wiring; the
 * builder (`pages/WalkthroughBuilder.tsx`, `embedded`) and the Video to course form are rendered inside it as they
 * are. Unit-tested in workshopVideo.test.ts.
 */
import { type VideoToCourseProject, isAgain, privacyWord } from "./videoToCourse";
import { madeWhen } from "./walkthrough";

export type Stage = "workshop" | "capture" | "edit" | "video" | "publish";

export const STAGES: { key: Stage; label: string }[] = [
  { key: "workshop", label: "Workshop" },
  { key: "capture", label: "Capture" },
  { key: "edit", label: "Edit" },
  { key: "video", label: "Video" },
  { key: "publish", label: "Publish" },
];

export const STAGE_ORDER: Stage[] = STAGES.map((s) => s.key);

export function stageIndex(stage: Stage): number {
  return STAGE_ORDER.indexOf(stage);
}

export function nextStage(stage: Stage): Stage | null {
  const i = stageIndex(stage);
  return i >= 0 && i + 1 < STAGE_ORDER.length ? STAGE_ORDER[i + 1] : null;
}

/** The Next button of each stage ("" for the last). */
export const NEXT_LABELS: Record<Stage, string> = { workshop: "", capture: "Next: Edit", edit: "Next: Video", video: "Next: Publish", publish: "" };

/**
 * Whether a stage may be opened: Workshop always; the others once a workshop is open (`hasWalkthrough`) and the
 * stage was reached (`furthest`) - the author clicks back to any earlier stage, and forward to one already visited.
 */
export function canOpenStage(target: Stage, furthest: Stage, hasWalkthrough: boolean): boolean {
  if (target === "workshop") return true;
  if (!hasWalkthrough) return false;
  return stageIndex(target) <= stageIndex(furthest);
}

/** The furthest stage after `stage` is reached. */
export function furthestOf(current: Stage, reached: Stage): Stage {
  return stageIndex(reached) > stageIndex(current) ? reached : current;
}

export const SAVE_BEFORE_NEXT = {
  title: "Save before going on?",
  message: "The next stage reads the walkthrough as it is saved. Save the walkthrough now, then go on?",
  confirmLabel: "Save and go on",
};

// ---- the workshop status (C2) ------------------------------------------------------------------------------

export interface WorkshopStatusWalkthrough { id: string; name: string; step_count: number; pictured: number; updated_at: string | null; created_at: string | null }
export interface WorkshopStatusEntry { walkthrough: WorkshopStatusWalkthrough | null; project: VideoToCourseProject | null }
/** `GET /api/courses/{course}/workshop-status`. */
export interface WorkshopStatusPayload { course: string; workshops: Record<string, WorkshopStatusEntry> }

export const workshopStatusUrl = (course: string) => `/api/courses/${encodeURIComponent(course)}/workshop-status`;
export const workshopStatusQueryKey = (course: string) => ["courses", course, "workshop-status"] as const;

/** What the canvas remembers of a publish this session (the editor does not say when a course was last published). */
export interface Published { when: string; summary: string }

/** "N of M steps have a picture" (the Capture stage's count and the status line's walkthrough part). */
export function capturedLine(pictured: number, total: number): string {
  return `${pictured} of ${total} step${total === 1 ? "" : "s"} ${pictured === 1 ? "has" : "have"} a picture`;
}

/**
 * A workshop's status line from the records: "no walkthrough yet", else the walkthrough's count and, as far as it
 * got, "video rendered <when>", "on Vimeo (Unlisted)", "placed in the guide", and "published <when>" when known this
 * session - the parts joined by " · ".
 */
export function workshopStatusLine(entry: WorkshopStatusEntry | null | undefined, published?: Published | null): string {
  if (!entry || !entry.walkthrough) return "no walkthrough yet";
  const parts = [`walkthrough: ${capturedLine(entry.walkthrough.pictured, entry.walkthrough.step_count)}`];
  const p = entry.project;
  if (p?.has_video) parts.push(`video rendered${madeWhen(p.rendered_at ?? p.created_at) ? ` ${madeWhen(p.rendered_at ?? p.created_at)}` : ""}`);
  if (p?.vimeo) parts.push(`on Vimeo (${privacyWord(p.vimeo.privacy)})`);
  if (p?.placed) parts.push("placed in the guide");
  if (published) parts.push(`published ${madeWhen(published.when) || published.when}`);
  return parts.join(" · ");
}

/** The Open button of a workshop row: the walkthrough it has, or an import first. */
export function openLabel(entry: WorkshopStatusEntry | null | undefined): string {
  return entry?.walkthrough ? "Open" : "Import and open";
}

// ---- the stages' collapsed lines ------------------------------------------------------------------------

export interface CanvasFacts {
  courseTitle: string | null;
  workshopTitle: string | null;
  walkthroughName: string | null;
  stepCount: number;
  pictured: number;
  chapterCount: number;
  project: VideoToCourseProject | null;
  published: Published | null;
  /** Whether capturing is possible here (the desktop app). */
  canCapture: boolean;
}

/** One line per collapsed stage. */
export function stageLine(stage: Stage, facts: CanvasFacts): string {
  switch (stage) {
    case "workshop":
      return facts.workshopTitle ? `${facts.courseTitle ?? ""}${facts.courseTitle ? " · " : ""}${facts.workshopTitle}` : "Pick a course and a workshop.";
    case "capture":
      if (!facts.walkthroughName) return "Capture the steps as you perform them (the desktop app).";
      return capturedLine(facts.pictured, facts.stepCount) + (facts.canCapture ? "" : " · capturing needs the desktop app");
    case "edit":
      if (!facts.walkthroughName) return "Edit the steps in the builder.";
      return `${facts.walkthroughName} · ${facts.stepCount} step${facts.stepCount === 1 ? "" : "s"} · ${facts.chapterCount} chapter${facts.chapterCount === 1 ? "" : "s"}`;
    case "video":
      if (!facts.project) return "Render the video, publish it to Vimeo and place the link in the guide: one button.";
      return videoLine(facts.project);
    case "publish":
      return facts.published ? publishedLine(facts.published) : "Publish the course through the Content Editor.";
  }
}

/** The Video stage's collapsed line: as far as the project got. */
export function videoLine(project: VideoToCourseProject): string {
  const parts: string[] = [];
  const when = madeWhen(project.rendered_at ?? project.created_at);
  parts.push(project.has_video ? `video rendered${when ? ` ${when}` : ""}` : "not rendered yet");
  if (project.vimeo) parts.push(`on Vimeo (${privacyWord(project.vimeo.privacy)})`);
  if (project.placed) parts.push("placed in the guide");
  else if (isAgain(project)) parts.push("not placed in the guide");
  return parts.join(" · ");
}

// ---- the Publish stage (C6) -------------------------------------------------------------------------------

/** `GET /api/courses/publish/config`. */
export interface PublishConfig { address: string; reachable: boolean; url: string | null; ref: string | null; message: string | null }
/** `GET /api/courses/{course}/publish/diff`: the editor's answer with the server's one summary line. */
export interface PublishDiff { summary: string; upToDate: boolean; newCourse: boolean; added: string[]; modified: string[]; removed: string[]; skippedIgnored: string[]; remoteCommit: string }
/** `POST /api/courses/{course}/publish`: the editor's answer with the server's one summary line. */
export interface PublishAnswer { ok: boolean; upToDate: boolean; commit: string; summary: string }

export const publishConfigUrl = "/api/courses/publish/config";
export const publishDiffUrl = (course: string) => `/api/courses/${encodeURIComponent(course)}/publish/diff`;
export const publishCourseUrl = (course: string) => `/api/courses/${encodeURIComponent(course)}/publish`;
export const publishConfigQueryKey = ["courses", "publish-config"] as const;
export const publishDiffQueryKey = (course: string) => ["courses", course, "publish-diff"] as const;

/** The commit message the stage prefills (the server's `content_editor.DEFAULT_MESSAGE`). */
export function publishMessage(workshopTitle: string): string {
  return `Walkthrough video: ${workshopTitle.trim() || "workshop"}`;
}

/** "The Content Editor at http://localhost:8000 publishes to https://…/Pentaho-Courses.git (main)." or the server's own line. */
export function editorLine(config: PublishConfig): string {
  if (!config.reachable) return config.message || `The Content Editor is not running at ${config.address}: start it, then press Publish again.`;
  return `The Content Editor at ${config.address} publishes to ${config.url ?? "its repository"}${config.ref ? ` (${config.ref})` : ""}.`;
}

/** "Published 2026-10-31 14:02: committed abc1234 and pushed (2 files)". */
export function publishedLine(published: Published): string {
  return `Published ${madeWhen(published.when) || published.when}: ${published.summary}`;
}

export const NOT_PLACED_PUBLISH_LINE =
  "Allow export into the Courses folder is off, so the Vimeo link was not placed in the guide: there is nothing new to publish. Copy the lines from the Video stage into the guide (in the Content Editor), then publish from there.";

/** Whether Publish the course does anything: the editor reachable, a course, and nothing in flight. */
export function canPublish(config: PublishConfig | null | undefined, course: string | null, busy: boolean): boolean {
  return !!config?.reachable && !!course && !busy;
}

export const WORKSHOP_VIDEO_TILE = {
  title: "Workshop video",
  description: "Pick a workshop, capture it, edit, make the video, publish.",
};
