/**
 * Video to course (R4): the progress line under the builder's toolbar (and the Workshop video canvas's Video stage)
 * - the one job's line with **Cancel** for whoever may, then the Done line with **Open the video** (the project page)
 * and **Copy the link** (and **Copy the lines** when the export setting was off), or the job's error, or a cancel's
 * closing line. The job is followed by the page (`useFollowedJob`); this component is handed what it knows and the
 * job as it ended. Pure web; the rules are `lib/videoToCourse.ts`.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { Clapperboard, Copy, ExternalLink } from "lucide-react";
import { Button, ErrorBox } from "../ui";
import type { Job } from "../project/JobProgress";
import { CancelJobButton, JobProgress } from "../project/JobProgress";
import { copyText } from "../../lib/files";
import { VIDEO_TO_COURSE_CANCEL_TITLE, VIDEO_TO_COURSE_JOB_KIND, type VideoToCourseResult, finished, pasteLines, videoProjectUrl } from "../../lib/videoToCourse";

interface Props {
  /** The followed job while it runs (the page's one followed job), when it is this kind. */
  job: Job | undefined;
  jobActive: boolean;
  activeKind: string | undefined;
  mayCancel: boolean;
  onCancel: () => void;
  cancelPending: boolean;
  cancelError: string | null;
  /** The job as it ended (done or error), kept by the page until dismissed or the next start. */
  ended: Job | null;
  onDismiss: () => void;
  /** The project the chain runs on, as the page knows it: a failed phase's line links it (a job that raised has no result). */
  projectId?: string | null;
}

export function VideoToCourseStatus({ job, jobActive, activeKind, mayCancel, onCancel, cancelPending, cancelError, ended, onDismiss, projectId = null }: Props) {
  const [copied, setCopied] = useState<string | null>(null);
  const running = jobActive && activeKind === VIDEO_TO_COURSE_JOB_KIND;
  const copy = async (text: string, what: string) => {
    setCopied((await copyText(text)) ? `${what} copied.` : "Could not copy: the page may not write to the clipboard.");
  };
  if (running) {
    return (
      <div className="ms-capture-line" role="status" aria-live="polite" data-vtc-line="running">
        <Clapperboard size={14} />
        <div style={{ flex: 1, minWidth: 0 }}><JobProgress job={job} /></div>
        {job && mayCancel && <CancelJobButton job={job} pending={cancelPending} title={VIDEO_TO_COURSE_CANCEL_TITLE} onCancel={onCancel} />}
        {cancelError && <ErrorBox message={cancelError} />}
      </div>
    );
  }
  if (!ended) return null;
  const result = (ended.result ?? null) as VideoToCourseResult | null;
  if (ended.status === "error") {
    return (
      <div className="ms-capture-line" role="status" aria-live="polite" data-vtc-line="error">
        <span style={{ color: "var(--bad)", flex: 1 }} data-vtc-error="">{ended.error || ended.message}</span>
        {(result?.project_id || projectId) && <Link to={videoProjectUrl({ project_id: result?.project_id || projectId! })} className="os-btn os-btn-secondary os-btn-sm" data-vtc-open-project=""><ExternalLink size={14} /> Open the project</Link>}
        <button className="os-icon-btn" onClick={onDismiss} title="Dismiss" aria-label="Dismiss">×</button>
      </div>
    );
  }
  return (
    <div className="ms-capture-line" role="status" aria-live="polite" data-vtc-line={finished(result) ? "done" : "cancelled"}>
      <Clapperboard size={14} />
      <span style={{ flex: 1, minWidth: 0 }} data-vtc-done="">{ended.message}</span>
      {result?.project_id && (
        <Link to={videoProjectUrl(result)} className="os-btn os-btn-secondary os-btn-sm" data-vtc-open="">
          <ExternalLink size={14} /> Open the video
        </Link>
      )}
      {finished(result) && (
        <Button size="sm" icon={<Copy size={14} />} onClick={() => void copy(result.link, "The link")} data-vtc-copy="">Copy the link</Button>
      )}
      {finished(result) && result.lines.length > 0 && (
        <Button size="sm" icon={<Copy size={14} />} onClick={() => void copy(pasteLines(result), "The lines")} data-vtc-copy-lines="">Copy the lines</Button>
      )}
      {copied && <span className="os-muted os-small" data-vtc-copied="">{copied}</span>}
      <button className="os-icon-btn" onClick={onDismiss} title="Dismiss" aria-label="Dismiss">×</button>
    </div>
  );
}
