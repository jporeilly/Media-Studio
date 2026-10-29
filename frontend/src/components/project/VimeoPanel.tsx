/**
 * The Vimeo panel (V2, V3) under the Generate card's outputs: **Publish to Vimeo…** for a project with a rendered
 * video; once published, "On Vimeo: <link> (Unlisted)" with **Copy** and **Open**, **Publish again…** (a NEW video;
 * the old one stays, its link may be in a course) and, for a walkthrough project made from a course workshop,
 * **Place in the course…**. While the `vimeo_publish` job runs the panel shows its progress and, for its starter or
 * an administrator, **Cancel**; the line a cancelled or lost job leaves is the panel's notice. The page follows the
 * job as it follows a render (`useFollowedJob`); this panel is handed the page's one cancel. Pure web.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, ExternalLink, Upload } from "lucide-react";
import { api } from "../../api/client";
import type { Job } from "./JobProgress";
import { CancelJobButton, JobNotice, JobProgress } from "./JobProgress";
import { Button, ErrorBox } from "../ui";
import { VIMEO_CANCEL_TITLE, VIMEO_JOB_KIND, type VimeoDefaults, type VimeoRecord, onVimeoLine, previousLine, vimeoQueryKey, vimeoUrl } from "../../lib/vimeo";
import { PlaceInCourseDialog } from "./PlaceInCourseDialog";
import { PublishVimeoDialog } from "./PublishVimeoDialog";

interface Props {
  projectId: string;
  project: { kind: string; vimeo?: VimeoRecord | null };
  job: Job | undefined;
  jobActive: boolean;
  activeKind: string | undefined;
  mayCancel: boolean;
  onCancelJob: () => void;
  cancelPending: boolean;
  cancelError: unknown;
  /** The vimeo job's error line (the page keeps one followed job; the panel shows only its own kind's). */
  jobError: string | null;
  jobNotice: string | null;
  onDismissJobNotice: () => void;
  onJobStarted: (id: string, kind?: string) => void;
}

export function VimeoPanel({
  projectId, project, job, jobActive, activeKind, mayCancel, onCancelJob, cancelPending, cancelError, jobError, jobNotice, onDismissJobNotice, onJobStarted,
}: Props) {
  const [publishOpen, setPublishOpen] = useState(false);
  const [placeOpen, setPlaceOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const vimeo = project.vimeo ?? null;
  // Re-read when the record's video changes: a publish that lands refreshes the course and the privacies.
  const defaults = useQuery({ queryKey: vimeoQueryKey(projectId, vimeo?.id), queryFn: () => api.get<VimeoDefaults>(vimeoUrl(projectId)), staleTime: 30_000 });
  const running = jobActive && activeKind === VIMEO_JOB_KIND;
  const course = defaults.data?.course ?? null;
  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div style={{ display: "grid", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 14 }} data-vimeo-panel="">
      <div style={{ fontWeight: 600, fontSize: 14 }}>Vimeo</div>
      {jobNotice && <JobNotice text={jobNotice} onDismiss={onDismissJobNotice} />}
      {jobError && <ErrorBox message={jobError} />}
      {running ? (
        <div style={{ display: "grid", gap: 10 }} data-vimeo-running="">
          <JobProgress job={job} />
          {job && mayCancel && <CancelJobButton job={job} pending={cancelPending} title={VIMEO_CANCEL_TITLE} onCancel={onCancelJob} />}
          {cancelError ? <ErrorBox message={String((cancelError as { message?: string })?.message ?? cancelError)} /> : null}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {vimeo ? (
            <>
              <div className="os-row" style={{ gap: 8 }}>
                <span data-vimeo-line="">{onVimeoLine(vimeo, defaults.data?.privacies)}</span>
                <Button size="sm" icon={<Copy size={14} />} onClick={() => copy(vimeo.link)} data-vimeo-copy="">{copied ? "Copied" : "Copy"}</Button>
                <a className="os-btn os-btn-secondary os-btn-sm" href={vimeo.link} target="_blank" rel="noopener noreferrer" data-vimeo-open="">
                  <ExternalLink size={14} /> Open
                </a>
              </div>
              {previousLine(vimeo) && <span className="os-muted os-small" data-vimeo-previous="">{previousLine(vimeo)}</span>}
            </>
          ) : (
            <span className="os-muted os-small">Host the video on Vimeo to reference it from a course.</span>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Button
              icon={<Upload size={15} />}
              disabled={jobActive || !defaults.data}
              onClick={() => setPublishOpen(true)}
              title={defaults.data && !defaults.data.token_set ? "No Vimeo access token is set (Settings › Studio)" : undefined}
              data-vimeo-publish=""
            >
              {vimeo ? "Publish again…" : "Publish to Vimeo…"}
            </Button>
            {vimeo && course && (
              <Button icon={<ExternalLink size={15} />} disabled={jobActive} onClick={() => setPlaceOpen(true)} data-vimeo-place="">
                Place in the course…
              </Button>
            )}
          </div>
        </div>
      )}
      {defaults.data && (
        <PublishVimeoDialog
          open={publishOpen}
          projectId={projectId}
          defaults={defaults.data}
          again={!!vimeo}
          onClose={() => setPublishOpen(false)}
          onStarted={(jobId) => onJobStarted(jobId, VIMEO_JOB_KIND)}
        />
      )}
      {course && <PlaceInCourseDialog open={placeOpen} projectId={projectId} course={course} onClose={() => setPlaceOpen(false)} />}
    </div>
  );
}
