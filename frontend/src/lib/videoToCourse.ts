/**
 * Video to course (R1-R4): the PURE half of the one button, the one dialog and the one job - what
 * `GET /api/walkthroughs/{wid}/video-to-course` answers (`api/routers/video_to_course.py`), the routes, the body,
 * the read-only lines the dialog shows (where the link goes, the token's state, the export setting's state), the
 * one button's label, the run-again lines and what the builder's progress line says. The dialog
 * (`components/walkthrough/VideoToCourseDialog.tsx`), the shared form and the status line are the wiring; the
 * Workshop video canvas lays the same form out inline. Unit-tested in videoToCourse.test.ts.
 */
import type { Option } from "./studioSettings";
import { madeWhen } from "./walkthrough";

/** The job's kind (services/video_to_course.py JOB_KIND): the builder and the canvas follow it; the project page shows it on the Generate card. */
export const VIDEO_TO_COURSE_JOB_KIND = "video_to_course";

export interface VideoToCourseCourse { course: string; slug: string; title: string }

/** Where the link would go in the workshop's guide, from the plan computed when the dialog opened (nothing written). */
export interface VideoToCoursePlace {
  /** placeholder: the Walkthrough placeholder becomes the figure; figure: a figure of ours gets this link; insert: after the heading; null: the course could not be read. */
  how: "placeholder" | "figure" | "insert" | null;
  line: number;
  heading: string;
  /** `<course>/<slug>/guide.md`. */
  guide: string;
  existing_caption: string | null;
  caption: string;
  /** Whether the guide already holds the project's link (R3). */
  placed: boolean;
  error: string | null;
}

export interface VideoToCourseVimeo { id: string; link: string; privacy: string; published_at: string | null; previous: string[] }

/** The project the walkthrough already has for this flow (R3), when it has one. */
export interface VideoToCourseProject {
  id: string;
  name: string;
  created_at: string | null;
  rendered_at: string | null;
  has_video: boolean;
  vimeo: VideoToCourseVimeo | null;
  placed: boolean;
  guide: string | null;
}

/** `GET /api/walkthroughs/{wid}/video-to-course`. */
export interface VideoToCourseDefaults {
  name: string;
  privacy: string;
  privacies: Option[];
  token_set: boolean;
  allowed: boolean;
  course: VideoToCourseCourse | null;
  place: VideoToCoursePlace | null;
  project: VideoToCourseProject | null;
  job_kind: string;
}

export type VideoToCourseMode = "full" | "place";

/** The POST body: the name, Make a video's two ticks, the privacy, the caption, the Welcome Tour and the mode. */
export interface VideoToCourseBody { name: string; notes: boolean; zoom: boolean; privacy: string; caption: string; welcome: boolean; mode: VideoToCourseMode }

/** `POST …/video-to-course`'s answer: the project (made or reused) and the one job. */
export interface VideoToCourseStarted { project_id: string; job_id: string; created: boolean }

/** The job's result once it is done (the work's return in `api/routers/video_to_course.py`). */
export interface VideoToCourseResult {
  project_id: string;
  link: string;
  course: VideoToCourseCourse | null;
  placed: boolean;
  guide: string | null;
  written: string[];
  /** The figure to paste by hand, when the export setting was off; empty otherwise. */
  lines: string[];
  allowed: boolean;
  cancelled?: boolean;
  phase?: "render" | "publish" | "place";
}

export const videoToCourseUrl = (wid: string) => `/api/walkthroughs/${wid}/video-to-course`;
export const videoToCourseQueryKey = (wid: string) => ["walkthroughs", wid, "video-to-course"] as const;

/** The caption the dialog prefills: "Watch: <the walkthrough's name>" (the server keeps the Watch: once). */
export function captionDefault(name: string): string {
  const text = name.trim() || "Walkthrough";
  return /^watch\s*:/i.test(text) ? text : `Watch: ${text}`;
}

export const NO_COURSE_LINE = "This walkthrough came from no course workshop: the video is rendered and published to Vimeo; copy its link where you need it.";
export const TOKEN_SET_LINE = "A Vimeo token is set.";
export const NO_TOKEN_LINE = "No Vimeo token: set one in Settings › Studio first.";
export const EXPORT_ON_LINE = "Allow export into the Courses folder is on.";
export const EXPORT_OFF_LINE = "Allow export into the Courses folder is off: the video is rendered and published, and the lines to paste are shown at the end.";
export const WELCOME_LABEL = "This is the course's Welcome Tour";
export const SAVE_BEFORE_VIDEO_TO_COURSE = {
  title: "Save before making the video?",
  message: "The video is made from the walkthrough as it is saved. Save the walkthrough now, then render, publish and place it?",
  confirmLabel: "Save and go",
};
/** What Cancel does, by phase (the render's, the upload's, and nothing half-written before the place). */
export const VIDEO_TO_COURSE_CANCEL_TITLE =
  "Stops the chain at its next check: during the render nothing is written before the video (the finished sidecars are kept after it); during the upload the unfinished video is removed from Vimeo; before the place nothing is written into the course.";

/** "pdi-2hr-lab / 01-first-win: the Walkthrough placeholder at line 23 becomes the video figure" - the dialog's where-it-goes line. */
export function whereLine(course: VideoToCourseCourse, place: VideoToCoursePlace | null): string {
  const at = `${course.course} / ${course.slug}`;
  if (!place || place.how === null) return `${at}: the guide could not be read${place?.error ? ` (${place.error})` : ""}.`;
  if (place.how === "placeholder") return `${at}: the Walkthrough placeholder at line ${place.line} becomes the video figure.`;
  if (place.how === "figure") return `${at}: the existing figure's link at line ${place.line} is replaced.`;
  return place.heading
    ? `${at}: no placeholder: the figure is inserted after "${place.heading}" (line ${place.line}).`
    : `${at}: no placeholder: the figure is inserted at the top of the guide.`;
}

export function tokenLine(tokenSet: boolean): string {
  return tokenSet ? TOKEN_SET_LINE : NO_TOKEN_LINE;
}

/** The privacy as a word: "Unlisted", "Private", "Anybody" (an unknown value capitalised). */
export function privacyWord(privacy: string): string {
  return privacy ? privacy[0].toUpperCase() + privacy.slice(1) : "";
}

export function exportLine(allowed: boolean): string {
  return allowed ? EXPORT_ON_LINE : EXPORT_OFF_LINE;
}

/** The one button: "Render, publish and place" with a course, "Render and publish" without; "… again" for a run again. */
export function buttonLabel(course: VideoToCourseCourse | null, again: boolean): string {
  const base = course ? "Render, publish and place" : "Render and publish";
  return again ? `${base} again` : base;
}

export const PLACE_AGAIN_LABEL = "Just place the existing video again";

/** Whether the walkthrough already has a project with a video and a Vimeo record: the dialog then says so and offers the run again (R3). */
export function isAgain(project: VideoToCourseProject | null | undefined): boolean {
  return !!project && project.has_video && !!project.vimeo;
}

/** "Rendered 2026-10-31 14:02; on Vimeo as https://…; placed in pdi-2hr-lab/01-first-win/guide.md." */
export function againLine(project: VideoToCourseProject): string {
  const parts: string[] = [];
  const when = madeWhen(project.rendered_at ?? project.created_at);
  parts.push(project.has_video ? `Rendered${when ? ` ${when}` : ""}` : "Not rendered yet");
  if (project.vimeo) parts.push(`on Vimeo as ${project.vimeo.link}`);
  else parts.push("not on Vimeo yet");
  if (project.vimeo) parts.push(project.placed && project.guide ? `placed in ${project.guide}` : "not placed in the guide");
  return parts.join("; ") + ".";
}

/** Whether "Just place the existing video again" is offered: a video on Vimeo and a course, and the guide does not hold the link (it failed, or the guide reverted). */
export function canPlaceAgain(defaults: Pick<VideoToCourseDefaults, "course" | "project" | "allowed">): boolean {
  return !!defaults.course && isAgain(defaults.project) && !defaults.project!.placed && defaults.allowed;
}

/** Whether the one button does anything: a token, a name, and nothing else in flight. */
export function canStart(defaults: Pick<VideoToCourseDefaults, "token_set"> | null | undefined, name: string, busy: boolean): boolean {
  return !!defaults?.token_set && name.trim().length > 0 && !busy;
}

export function videoToCourseBody(fields: { name: string; notes: boolean; zoom: boolean; privacy: string; caption: string; welcome: boolean }, mode: VideoToCourseMode): VideoToCourseBody {
  return { name: fields.name.trim(), notes: fields.notes, zoom: fields.zoom, privacy: fields.privacy, caption: fields.caption.trim().slice(0, 200), welcome: fields.welcome, mode };
}

/** The project page the Done line's Open the video opens. */
export function videoProjectUrl(result: Pick<VideoToCourseResult, "project_id">): string {
  return `/projects/${result.project_id}`;
}

/** Whether a done job's result is a finished chain (not a cancel), with a link to copy. */
export function finished(result: VideoToCourseResult | null | undefined): result is VideoToCourseResult {
  return !!result && !result.cancelled && !!result.link;
}

/** The lines to paste, as one text (the setting was off). */
export function pasteLines(result: Pick<VideoToCourseResult, "lines">): string {
  return (result.lines ?? []).join("\n");
}
