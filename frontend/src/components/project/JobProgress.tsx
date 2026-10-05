import { Ban, Info, X } from "lucide-react";
import { Button } from "../ui";

/** A background job as GET /api/jobs/{id} reports it (services/jobs.py). */
export interface Job {
  id: string;
  kind: string;
  status: string;
  progress: number;
  message: string;
  error?: string | null;
  project_id?: string | null;
  /** What the work returned once the job is done (its shape is the job kind's). */
  result?: any;
  /** True once POST /api/jobs/{id}/cancel was called; the ai-* jobs, a re-voice and a render stop at their next check. */
  cancel_requested?: boolean;
  /** Who started the job: only they, or an administrator, may cancel it (lib/jobs.ts `mayCancelJob`). */
  user_id?: string | null;
}

/** The progress bar every card shows for the job it owns (the page polls one job at a time). */
export function JobProgress({ job }: { job?: Job }) {
  const pct = Math.round((job?.progress || 0) * 100);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ color: "var(--muted)" }}>{job?.message || "Starting…"}</div>
      <div style={{ height: 8, borderRadius: 6, background: "var(--surface-3)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "var(--brand)", transition: "width .3s ease" }} />
      </div>
      <div style={{ color: "var(--muted)", fontSize: 12, fontVariantNumeric: "tabular-nums" }}>{pct}%</div>
    </div>
  );
}

/**
 * Cancel for a running job: the Slides card's AI jobs, the Re-voice card and the Generate card (a render or a
 * preview). Shown only to a viewer who may cancel it (the starter or an administrator); it reads "Cancelling…"
 * once asked, until the job stops at its next check. The one cancel request lives in `useFollowedJob`
 * (lib/useFollowedJob.ts); every card is handed its `cancel`, `cancelPending` and `cancelError`.
 */
export function CancelJobButton({ job, pending, title, onCancel }: { job: Job; pending: boolean; title: string; onCancel: () => void }) {
  return (
    <div>
      <Button size="sm" icon={<Ban size={14} />} disabled={pending || !!job.cancel_requested} title={title} onClick={onCancel}>
        {job.cancel_requested ? "Cancelling…" : "Cancel"}
      </Button>
    </div>
  );
}

/**
 * A line a card keeps once its job is over and the page has let go of it: a job the server lost in a restart,
 * a poll that kept failing, a cancelled re-voice or render saying what it left. Dismissed with its cross, or
 * replaced by the next job.
 */
export function JobNotice({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <div className="os-ai-result" role="status" style={{ marginBottom: 14 }}>
      <Info size={14} />
      <span>{text}</span>
      <button type="button" className="os-icon-btn" aria-label="Dismiss" title="Dismiss" onClick={onDismiss}>
        <X size={14} />
      </button>
    </div>
  );
}
