/**
 * Following one background job: the project page's jobs and the Settings page's update, with the same rules
 * (lib/jobs.ts holds them as pure helpers; this hook is the wiring, tested under jsdom in
 * useFollowedJob.test.ts).
 *
 * - **Adopt.** The scope's running job - `GET /api/projects/{id}/job`, or `GET /api/system/update/job` - is
 *   asked on load and every PROJECT_JOB_POLL_MS while nothing is in flight, and followed whoever started it,
 *   so a reload, another tab or someone else's job shows on the page like one started there.
 * - **Poll.** The followed job is polled every JOB_POLL_MS until it ends. A 404 means the server no longer
 *   holds it (a restart forgets every job): the page lets go at once and keeps the restart line. Any other
 *   failure is tried MAX_POLL_FAILURES times in a row, then the page lets go and keeps the error's line - and
 *   does not take the job back from the answer it already had (React Query keeps the last good one while the
 *   server is down), only from a newer one.
 * - **Ask again.** Once the scope answers again after such a give-up, the job is polled once more, so a job
 *   lost in a restart gets the restart line and a finished one is picked up as finished.
 * - **End.** A job that ends is handed to `onEnded` once; a done job is let go (an errored one stays, so its
 *   error stays on its card), and the line a job leaves - a cancel's, a transcription's dropped adjustments -
 *   is kept as the notice.
 * - **Cancel** is offered to the job's starter or an administrator (`mayCancel`); its error is cleared when
 *   the page follows another job.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Job } from "../components/project/JobProgress";
import {
  JOB_POLL_MS,
  PROJECT_JOB_POLL_MS,
  isActiveStatus,
  isEndedStatus,
  isLostJob,
  isServerAnswer,
  jobPollInterval,
  jobToAdopt,
  jobToAskAgain,
  lineToKeep,
  mayCancelJob,
  pollFailureMessage,
  retryJobPoll,
  type ActiveJob,
  type UnreachedJob,
} from "./jobs";

/** The line a card keeps once the page has let go of its job. */
export interface JobNoticeState {
  /** The kind of the job it is about, when known: which card shows it. */
  kind?: string;
  /** `lost`: the server no longer holds it (404); `unreached`: its poll kept failing; `ended`: a line the job left when it finished. */
  reason: "lost" | "unreached" | "ended";
  text: string;
}

export interface FollowedJobOptions {
  /** Where the scope's running job is asked for (answers `{active_job}`); null while there is nothing to ask. */
  activeUrl: string | null;
  /** The viewer, for the Cancel rule. */
  viewer: { id: string; role: string } | null | undefined;
  /** Called once per job when it ends (done or error), with its last state. */
  onEnded?: (job: Job) => void;
}

export interface FollowedJob {
  jobId: string | null;
  /** The followed job's last answer. */
  job: Job | undefined;
  /** In flight: queued or running, or not answered yet; never once its poll has failed. */
  jobActive: boolean;
  /** Its kind: the poll's answer, else what the page knew when it began following it. */
  activeKind: string | undefined;
  follow: (id: string, kind?: string) => void;
  notice: JobNoticeState | null;
  dismissNotice: () => void;
  /** Whether this viewer may cancel it: its starter or an administrator. */
  mayCancel: boolean;
  cancel: () => void;
  cancelPending: boolean;
  cancelError: unknown;
}

/** The query key of a scope's running job. */
export const activeJobKey = (activeUrl: string | null) => ["active-job", activeUrl] as const;

export function useFollowedJob({ activeUrl, viewer, onEnded }: FollowedJobOptions): FollowedJob {
  const qc = useQueryClient();
  const [followed, setFollowed] = useState<{ id: string; kind?: string } | null>(null);
  const [notice, setNotice] = useState<JobNoticeState | null>(null);
  const [unreached, setUnreached] = useState<UnreachedJob | null>(null);
  // The jobs seen to end (done, or lost): never followed again.
  const ended = useRef(new Set<string>());
  // The jobs followed again after a give-up, until one of their polls answers. After a give-up the server
  // answered (a refused poll), that is one more try each; after a connection that failed, it holds nothing back.
  const askedAgain = useRef(new Set<string>());
  // The jobs already handed to onEnded.
  const handled = useRef(new Set<string>());
  const onEndedRef = useRef(onEnded);
  useEffect(() => {
    onEndedRef.current = onEnded;
  });

  const follow = useCallback((next: string, kind?: string) => {
    setFollowed({ id: next, kind });
    setNotice(null);
    setUnreached(null);
  }, []);
  const jobId = followed?.id ?? null;

  const poll = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    retry: retryJobPoll,
    retryDelay: JOB_POLL_MS,
    // A job that has ended is not asked again behind the page's back: after a restart that answer is a 404,
    // and the restart line would land over a job that had finished long before.
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    refetchInterval: (q) => jobPollInterval(q.state.data?.status, q.state.status === "error", JOB_POLL_MS),
  });
  const job = poll.data;
  const status = job?.status;
  // A failed poll of a job that has already ended is not a give-up: it is over, and its state stays.
  const pollFailed = poll.isError && !isEndedStatus(status);
  const jobActive = !!jobId && !pollFailed && (job ? isActiveStatus(status) : true);
  const activeKind = job?.kind ?? followed?.kind;

  const scope = useQuery({
    queryKey: activeJobKey(activeUrl),
    queryFn: () => api.get<{ active_job: ActiveJob | null }>(activeUrl as string),
    enabled: !!activeUrl,
    staleTime: 0,
    retry: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    refetchInterval: jobActive ? false : PROJECT_JOB_POLL_MS,
  });
  const running = scope.data?.active_job ?? null;
  const answeredAt = scope.dataUpdatedAt;

  // A poll that answered: the job was reached, so it has its one more try back for the next outage.
  const answeredFor = job?.id;
  const polledAt = poll.dataUpdatedAt;
  useEffect(() => {
    if (answeredFor && polledAt) askedAgain.current.delete(answeredFor);
  }, [answeredFor, polledAt]);

  // Adopt the scope's running job, or ask after the one given up on once the scope answers again.
  useEffect(() => {
    const state = { followed: jobId, inFlight: jobActive, ended: ended.current, unreached, answeredAt, askedAgain: askedAgain.current };
    const next = jobToAdopt(running, state) ?? jobToAskAgain(state);
    if (!next) return;
    if (next === unreached?.id) askedAgain.current.add(next);
    follow(next, running && next === running.id ? running.kind : unreached?.kind);
  }, [running, jobId, jobActive, unreached, answeredAt, follow]);

  // A poll that failed for good: let go of the job (the buttons are free again) and keep the line. The failed
  // query is dropped, so an ask-again polls afresh; a lost job is never followed again.
  const pollError = poll.error;
  useEffect(() => {
    if (!pollFailed || !jobId) return;
    const lost = isLostJob(pollError);
    if (lost) ended.current.add(jobId);
    else setUnreached({ id: jobId, kind: activeKind, since: Date.now(), answered: isServerAnswer(pollError) });
    setNotice({ kind: activeKind, reason: lost ? "lost" : "unreached", text: pollFailureMessage(pollError) });
    qc.removeQueries({ queryKey: ["job", jobId] });
    setFollowed(null);
    if (activeUrl) void qc.invalidateQueries({ queryKey: activeJobKey(activeUrl) });
  }, [pollFailed, pollError, jobId, activeKind, activeUrl, qc]);

  // A job that ended: hand it over once; let go of a done one and keep the line it leaves.
  useEffect(() => {
    if (!jobId || !job || job.id !== jobId || !isEndedStatus(job.status)) return;
    if (!handled.current.has(jobId)) {
      handled.current.add(jobId);
      onEndedRef.current?.(job);
      if (activeUrl) void qc.invalidateQueries({ queryKey: activeJobKey(activeUrl) });
    }
    if (job.status === "done") {
      ended.current.add(jobId);
      const line = lineToKeep(job);
      if (line) setNotice({ kind: job.kind, reason: "ended", text: line });
      setFollowed(null);
    }
  }, [job, jobId, activeUrl, qc]);

  const cancelMutation = useMutation({
    mutationFn: (target: string) => api.post<Job>(`/api/jobs/${target}/cancel`, {}),
    // The answer is the job's state with its flag up: the card reads "Cancelling…" at once.
    onSuccess: (state) => {
      if (state?.id) qc.setQueryData(["job", state.id], state);
    },
  });
  const resetCancel = cancelMutation.reset;
  useEffect(() => {
    resetCancel();
  }, [jobId, resetCancel]);
  const mutateCancel = cancelMutation.mutate;
  const cancel = useCallback(() => {
    if (jobId) mutateCancel(jobId);
  }, [jobId, mutateCancel]);
  const dismissNotice = useCallback(() => setNotice(null), []);

  return {
    jobId,
    job,
    jobActive,
    activeKind,
    follow,
    notice,
    dismissNotice,
    mayCancel: mayCancelJob(job, viewer),
    cancel,
    cancelPending: cancelMutation.isPending,
    cancelError: cancelMutation.isError ? cancelMutation.error : null,
  };
}
