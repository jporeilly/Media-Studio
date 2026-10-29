/**
 * Publish to Vimeo (V2) and Place in the course (V3): the PURE half of the project page's Vimeo panel - what the
 * routes answer (`api/routers/vimeo.py`), the routes, the lines the panel and its two dialogs say. The panel
 * (`components/project/VimeoPanel.tsx`) and the dialogs are the wiring. Unit-tested in vimeo.test.ts.
 *
 * A rendered project video is published to Vimeo by a `vimeo_publish` job (one job per project, followed like a
 * render); the record then carries `vimeo` - the id, the link (an Unlisted video's carries its hash), the privacy in
 * the studio's words - and Publish again makes a NEW video, the earlier ids kept in `previous` (their links may be in
 * a course). For a walkthrough project made from a course workshop the link can be placed in that workshop's guide:
 * the course standard's Walkthrough placeholder becomes the video figure, or a figure of ours has its link replaced,
 * or - with neither - the figure is inserted after the guide's first section heading, or copied to paste by hand.
 */
import type { Option } from "./studioSettings";

/** The job kind the panel follows (services/jobs.py, api/routers/vimeo.py). */
export const VIMEO_JOB_KIND = "vimeo_publish";

/** What the record keeps of a published video (`services/vimeo.py vimeo_record`). */
export interface VimeoRecord {
  id: string;
  link: string;
  /** An Unlisted video's access hash, from its link; null for a public one. */
  hash: string | null;
  player_url: string | null;
  /** The studio's word: unlisted, private or anybody. */
  privacy: string;
  published_at: string;
  title: string;
  /** The ids of the videos published before this one (never deleted: their links may be in a course). */
  previous?: string[];
}

export interface VimeoCourse { course: string; slug: string; title: string }

/** `GET /api/projects/{pid}/vimeo`: what the Publish dialog opens with and what the panel knows. */
export interface VimeoDefaults {
  title: string;
  description: string;
  privacy: string;
  privacies: Option[];
  token_set: boolean;
  name_max: number;
  description_max: number;
  vimeo: VimeoRecord | null;
  /** The course workshop the link can be placed in (a walkthrough project from a course), else null. */
  course: VimeoCourse | null;
  /** Whether the studio allows writes into the Courses folder. */
  allowed: boolean;
}

export interface PublishBody { title: string; description: string; privacy: string }

export interface PlaceFile { path: string; action: "write" | "unchanged" }

/** `POST /api/projects/{pid}/vimeo/place/plan`: the dry run; nothing written. */
export interface PlacePlan {
  course: string;
  course_title: string;
  slug: string;
  workshop: string;
  /** `<course>/<slug>/guide.md`. */
  guide: string;
  /** Where the link goes: the placeholder, a figure of ours, an insertion after the first section heading, or nowhere. */
  how: "placeholder" | "figure" | "insert" | "none";
  /** The 1-based line of the placeholder's `<figure>`, the figure line, or the heading the block goes after. */
  line: number;
  heading: string;
  /** The caption in force: the one sent, else the figure's own, else the walkthrough's name. */
  caption: string;
  existing_caption: string | null;
  link: string;
  /** The figure, line by line, to paste by hand. */
  lines: string[];
  diff: string;
  welcome: boolean;
  insert: boolean;
  files: PlaceFile[];
  changed: boolean;
  guide_sha256: string;
  manifest_sha256: string | null;
  course_sha256: string | null;
  manifest: string | null;
  allowed: boolean;
}

export interface PlaceBody { caption: string; welcome: boolean; insert: boolean }

/** `POST /api/projects/{pid}/vimeo/place`: what was written. */
export interface PlaceResult { course: string; slug: string; guide: string; link: string; written: string[]; welcome: boolean; message: string }

export const vimeoUrl = (pid: string) => `/api/projects/${pid}/vimeo`;
export const publishUrl = (pid: string) => `${vimeoUrl(pid)}/publish`;
export const placePlanUrl = (pid: string) => `${vimeoUrl(pid)}/place/plan`;
export const placeUrl = (pid: string) => `${vimeoUrl(pid)}/place`;

export const vimeoQueryKey = (pid: string, vimeoId: string | null | undefined) => ["vimeo", pid, vimeoId ?? ""] as const;
export const placePlanQueryKey = (pid: string, body: PlaceBody) => ["vimeo", pid, "place-plan", body.caption, body.welcome, body.insert] as const;

/** The fallback list when the defaults have not arrived (the server's own list wins). */
export const PRIVACIES: Option[] = [
  { value: "unlisted", label: "Unlisted" },
  { value: "private", label: "Private" },
  { value: "anybody", label: "Anybody" },
];

export function privacyLabel(privacy: string, privacies: Option[] = PRIVACIES): string {
  const found = privacies.find((o) => o.value === privacy) ?? PRIVACIES.find((o) => o.value === privacy);
  return found?.label ?? (privacy ? privacy[0].toUpperCase() + privacy.slice(1) : "");
}

/** "On Vimeo: https://vimeo.com/123/abc (Unlisted)" - the panel's line. */
export function onVimeoLine(v: Pick<VimeoRecord, "link" | "privacy">, privacies?: Option[]): string {
  return `On Vimeo: ${v.link} (${privacyLabel(v.privacy, privacies)})`;
}

/** "Published before: 2 earlier videos stay on Vimeo (their links may be in a course)." or null for none. */
export function previousLine(v: Pick<VimeoRecord, "previous">): string | null {
  const n = v.previous?.length ?? 0;
  if (!n) return null;
  return `Published before: ${n === 1 ? "an earlier video stays" : `${n} earlier videos stay`} on Vimeo (${n === 1 ? "its link" : "their links"} may be in a course).`;
}

/** The POST body: trimmed, within Vimeo's limits (the server cuts too). */
export function publishBody(title: string, description: string, privacy: string, limits: { name_max: number; description_max: number }): PublishBody {
  return { title: title.trim().slice(0, limits.name_max), description: description.slice(0, limits.description_max), privacy };
}

export function canPublish(defaults: Pick<VimeoDefaults, "token_set"> | null | undefined, title: string): boolean {
  return !!defaults?.token_set && title.trim().length > 0;
}

export function descriptionCounter(text: string, max: number): string {
  return `${text.length.toLocaleString()} / ${max.toLocaleString()}`;
}

export const NO_TOKEN_LINE = "No Vimeo access token is set: an administrator adds one in Settings › Studio (Vimeo access token).";
export const PLUS_LINE = "Unlisted and Private videos need Vimeo Plus or higher; a free account may publish as Anybody.";

/** What the Vimeo panel's Cancel does (services/vimeo.py). */
export const VIMEO_CANCEL_TITLE =
  "Stops the upload between two chunks (or while Vimeo processes the video) and removes the unfinished video from Vimeo; a video published before stays as it was.";

export const INSERT_LABEL = "Insert the figure after the guide's first section heading";
export const WELCOME_LABEL = "This is the course's Welcome Tour";
export const PLACED_NEXT = "Review and publish it in the Content Editor.";

/** Where the link goes, said plainly - the dialog's first line. */
export function placeLine(plan: Pick<PlacePlan, "how" | "line" | "heading" | "guide">): string {
  switch (plan.how) {
    case "placeholder":
      return `The Walkthrough placeholder at line ${plan.line} of ${plan.guide} becomes the video figure.`;
    case "figure":
      return `The video figure at line ${plan.line} of ${plan.guide} gets this link.`;
    case "insert":
      return plan.heading
        ? `The figure goes after "${plan.heading}" (line ${plan.line}) of ${plan.guide}.`
        : `The figure goes at the top of ${plan.guide} (it has no heading).`;
    default:
      return `${plan.guide} has no Walkthrough placeholder and no video figure of ours.`;
  }
}

/** What the dialog says of the studio setting, on or off. */
export function placeSettingLine(allowed: boolean): string {
  return allowed
    ? "Allow export into the Courses folder is on: Place in the course writes the guide (and the manifest, and course.json for the Welcome Tour)."
    : "Allow export into the Courses folder is off (Settings › Studio, an administrator): copy the lines into the guide by hand.";
}

/** The write's body: the options as planned and each file's sha256 from the preview. */
export function placeWriteBody(plan: Pick<PlacePlan, "guide_sha256" | "manifest_sha256" | "course_sha256">, body: PlaceBody) {
  return { ...body, guide_sha256: plan.guide_sha256, manifest_sha256: plan.manifest_sha256, course_sha256: plan.course_sha256 };
}

/** Whether the Place button does anything: the setting on, something to write, and a place for the link. */
export function canPlace(plan: Pick<PlacePlan, "allowed" | "changed" | "how"> | null | undefined): boolean {
  return !!plan && plan.allowed && plan.changed && plan.how !== "none";
}

/** "2 files to write" - or "Nothing to write: the guide already holds this video." */
export function placeFilesLine(files: PlaceFile[]): string {
  const write = files.filter((f) => f.action === "write").length;
  if (!write) return "Nothing to write: the guide already holds this video.";
  return `${write} file${write === 1 ? "" : "s"} to write`;
}

/** The lines to paste, as one text. */
export function pasteText(lines: string[]): string {
  return lines.join("\n");
}
