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
  /** True once POST /api/jobs/{id}/cancel was called; the ai-* jobs stop between slides. */
  cancel_requested?: boolean;
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
