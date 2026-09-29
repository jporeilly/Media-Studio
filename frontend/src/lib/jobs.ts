/**
 * Following a background job: which job a page follows, how often it polls it, what a failed poll means, the
 * line a card keeps, and who may cancel it. Pure helpers, unit-tested in jobs.test.ts; the wiring is one hook,
 * `useFollowedJob.ts` (tested under jsdom), which the project page (`pages/ProjectDetail.tsx`) and the
 * Settings page's update (`pages/Settings.tsx`) both use.
 *
 * Jobs live in the server's memory (services/jobs.py), so a backend restart forgets every one of them: a poll
 * of an id the server no longer holds answers 404 "Job not found.", and that is read here as "the job was
 * lost" - the page stops polling, lets go of the job and says so on the card that was running it. Before this
 * nothing read a poll's error, React Query kept the last good answer ("running"), and the page polled every
 * 1.2 s for ever with every card waiting on a job that no longer existed.
 */

import { ApiError, errorMessage } from "../api/client";

/** How often the job a page follows is polled. */
export const JOB_POLL_MS = 1200;
/** How often a page with no job in flight asks whether its scope has one (started elsewhere, or still running). */
export const PROJECT_JOB_POLL_MS = 5000;
/** Consecutive failed attempts of one poll before the page stops asking. */
export const MAX_POLL_FAILURES = 3;
/** The one line a card shows for a job the server lost. */
export const LOST_JOB_MESSAGE = "The job stopped when the server restarted. Start it again.";

/** A scope's running job as `GET /api/projects/{id}/job` (or `GET /api/system/update/job`) reports it. */
export interface ActiveJob {
  id: string;
  kind: string;
  status: string;
  user_id: string | null;
}

export const isActiveStatus = (status: string | null | undefined) => status === "queued" || status === "running";
export const isEndedStatus = (status: string | null | undefined) => status === "done" || status === "error";

/** A poll's error that means the server no longer holds the job: a 404, after a restart. */
export function isLostJob(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * A poll's error that the server itself answered (an error status), as against a request that never reached it
 * (a connection that failed while the server was down or restarting).
 */
export function isServerAnswer(error: unknown): boolean {
  return error instanceof ApiError;
}

/**
 * React Query's `retry` for a job poll. `failuresBefore` is how many attempts of THIS poll have already
 * failed (0 on the first failure). A lost job is never retried; any other failure is tried again until
 * MAX_POLL_FAILURES attempts in a row have failed, and then the poll errors and the page stops.
 */
export function retryJobPoll(failuresBefore: number, error: unknown): boolean {
  return !isLostJob(error) && failuresBefore + 1 < MAX_POLL_FAILURES;
}

/**
 * React Query's `refetchInterval` for a job poll: every `every` ms (JOB_POLL_MS) until the job ended or the
 * poll failed. The interval is passed in rather than read here, so the one place that polls decides it.
 */
export function jobPollInterval(status: string | null | undefined, failed: boolean, every: number = JOB_POLL_MS): number | false {
  return failed || isEndedStatus(status) ? false : every;
}

/** What the card that was running a job says once its poll has failed for good. */
export function pollFailureMessage(error: unknown): string {
  if (isLostJob(error)) return LOST_JOB_MESSAGE;
  return `Could not reach the job (${errorMessage(error)}), so the page stopped following it. Reload the page to see whether it is still running.`;
}

/** A job the page stopped following because its poll kept failing (not a 404): when, and which. */
export interface UnreachedJob {
  id: string;
  kind?: string;
  /** When the page gave up on it (ms since the epoch). */
  since: number;
  /**
   * Whether the give-up was the server's own answer (an error status, `isServerAnswer`) rather than a connection
   * that failed. Only a server that answers can refuse a job's poll while its scope answers - the loop the one
   * more try (`askedAgain`) guards against - so only then is the job held to one more try.
   */
  answered?: boolean;
}

/** What the page knows when it decides which job to follow (lib/useFollowedJob.ts keeps it). */
export interface FollowState {
  /** The job it follows, or null. */
  followed: string | null;
  /** Whether that job is still in flight (not ended, and its poll has not failed). */
  inFlight: boolean;
  /** The jobs it has seen end: done, or lost in a restart (a 404). */
  ended: ReadonlySet<string>;
  /** The job it last gave up on because the server could not be reached, if any. */
  unreached: UnreachedJob | null;
  /** When the scope's job answer (the project's, or the update's) last arrived - its `dataUpdatedAt`. */
  answeredAt: number;
  /** The jobs followed again after a give-up and not yet answered since; each gets one more try. */
  askedAgain: ReadonlySet<string>;
}

/**
 * The job a page should start following, or null to leave things as they are. The page follows the scope's
 * running job whoever started it - after a reload, in another tab, or when someone else started it - but never
 * one it is already following, never while it follows one that is still in flight, and never one it has
 * already seen end (`ended`), which the answer may still name for a moment.
 *
 * Nor a job it gave up on, from an answer that is not newer than the give-up: while the server is down the
 * answer cannot be refreshed, React Query keeps the last good one - the very answer that named the job - and
 * following it again would restart the three failing attempts for as long as the server stays down. A newer
 * answer that still names it (the server is back, and the job still running) is followed. When the give-up was
 * the server's own answer (`unreached.answered`: the job's poll was refused while its scope answered), it is
 * followed once until one of its polls answers (`askedAgain`), so it cannot loop; after a connection that
 * failed it is always followed again from a newer answer - a server that drops again before the poll answers
 * (a flap) must not leave a running job unfollowed.
 */
export function jobToAdopt(active: ActiveJob | null | undefined, state: FollowState): string | null {
  if (!active || !isActiveStatus(active.status)) return null;
  if (active.id === state.followed || state.ended.has(active.id)) return null;
  if (state.followed && state.inFlight) return null;
  const { unreached } = state;
  if (unreached && active.id === unreached.id) {
    if (state.answeredAt <= unreached.since) return null;
    if (unreached.answered && state.askedAgain.has(active.id)) return null;
  }
  return active.id;
}

/**
 * A job to ask after once more, or null. A backend restart usually shows first as polls that cannot connect
 * at all, not as the 404 a restarted server answers, so the page gives up on the job with "could not reach"
 * while the server is down. Once the server answers again - the scope's job answer arrived after the page gave
 * up (`answeredAt`) - the job is polled once more: a job lost in the restart then answers 404 and says so, and
 * one that finished is picked up as finished. Never while the page follows another; and, after a give-up that
 * was the server's own answer, once per job until one of its polls answers (`askedAgain`, as in `jobToAdopt`).
 */
export function jobToAskAgain(state: FollowState): string | null {
  const { unreached } = state;
  if (!unreached || state.followed || state.answeredAt <= unreached.since) return null;
  if (unreached.answered && state.askedAgain.has(unreached.id)) return null;
  return unreached.id;
}

/** The card of the project page that shows a job's progress and its notices. */
export type JobCard = "slides" | "generate" | "transcript" | "revoice";

/**
 * Which card owns a job. A kind the page does not know yet (a job started from the Slides card, lost before
 * its first poll answered) belongs to the card that starts such jobs: the Slides card on a deck or PDF, the
 * Transcript card on a video.
 */
export function cardForJob(kind: string | null | undefined, projectKind: string | null | undefined): JobCard {
  if (kind === "generate") return "generate";
  if (kind === "transcribe") return "transcript";
  if (kind === "revoice") return "revoice";
  if (kind) return "slides"; // render-slides and every ai-* kind
  return projectKind === "video" ? "transcript" : "slides";
}

/**
 * Whether the viewer may cancel a job - the cancel route's own rule (api/routers/jobs.py): the user who started
 * it, or an administrator; a job that records no starter (an update) is an administrator's. Anyone else sees
 * the job read-only.
 */
export function mayCancelJob(
  job: { user_id?: string | null } | null | undefined,
  user: { id: string; role: string } | null | undefined,
): boolean {
  if (!job || !user) return false;
  return user.role === "admin" || (!!job.user_id && job.user_id === user.id);
}

/** What the Re-voice card's Cancel does (services/revoice.py). */
export const REVOICE_CANCEL_TITLE =
  "Stops before the next sentence is synthesised, or while the picture is cut or the music mixed. Stopped before the new narration is laid on the picture, the previous re-voice stays as it was; stopped after that, at the music, the re-voiced video has the new narration without the music. Once the music is mixed - or the narration laid on, when there is no music - it is too late and the render completes.";

/** A cancelled job's result, as the jobs that honour a cancel report it. */
export function wasCancelled(result: unknown): boolean {
  return !!result && typeof result === "object" && (result as { cancelled?: unknown }).cancelled === true;
}

/**
 * The line a done job leaves on its card once the page lets go of it, or null: a cancelled job's closing line
 * (a re-voice says what it left on disk), and a transcription's when it dropped sentences' adjustments ("…; the
 * adjustments on 3 sentences were dropped") - the count the dialog warned about, now as it happened.
 */
export function lineToKeep(job: { kind: string; status: string; message?: string | null; result?: unknown }): string | null {
  if (job.status !== "done") return null;
  if (wasCancelled(job.result)) return job.message || "Cancelled";
  const dropped = (job.result as { adjustments_dropped?: unknown } | null | undefined)?.adjustments_dropped;
  if (job.kind === "transcribe" && typeof dropped === "number" && dropped > 0) return job.message || null;
  return null;
}

/**
 * Which card of the project page shows a notice, or null for none. The Slides card keeps its own line for a
 * finished AI job (its result summary), so a line an AI job leaves is not shown twice; a lost or unreached job's
 * line shows on whichever card ran it.
 */
export function noticeCard(
  notice: { kind?: string; reason: string } | null | undefined,
  projectKind: string | null | undefined,
): JobCard | null {
  if (!notice) return null;
  const card = cardForJob(notice.kind, projectKind);
  return notice.reason === "ended" && card === "slides" ? null : card;
}
