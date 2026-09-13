import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileText, Film, Presentation, Save, Wand2 } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { Button, Card, ErrorBox, PageHeader, Spinner, Textarea } from "../components/ui";
import { duration, relativeTime } from "../lib/format";

interface Segment {
  start: number;
  end: number;
  text: string;
}
interface Project {
  id: string;
  name: string;
  kind: "deck" | "pdf" | "video";
  source_filename: string;
  size_bytes: number;
  slide_count: number | null;
  created_at: string;
  transcript?: Segment[];
  language?: string;
  duration?: number;
  transcribed_device?: string;
}
interface Job {
  id: string;
  status: string;
  progress: number;
  message: string;
  error?: string | null;
}

const KIND_ICON = { deck: Presentation, pdf: FileText, video: Film } as const;

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</div>
      <div style={{ fontWeight: 500 }}>{value}</div>
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [segments, setSegments] = useState<Segment[] | null>(null);

  const project = useQuery({
    queryKey: ["project", id],
    queryFn: () => api.get<Project>(`/api/projects/${id}`),
  });

  // Keep the editable copy in sync with the saved transcript.
  useEffect(() => {
    if (project.data?.transcript) setSegments(project.data.transcript);
  }, [project.data?.transcript]);

  const transcribe = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/projects/${id}/transcribe`, {}),
    onSuccess: (r) => setJobId(r.job_id),
  });

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "done" || s === "error" ? false : 1200;
    },
  });

  // When the job finishes, pull the freshly-saved transcript onto the project.
  useEffect(() => {
    if (job.data?.status === "done") {
      qc.invalidateQueries({ queryKey: ["project", id] });
      setJobId(null);
    }
  }, [job.data?.status, id, qc]);

  const save = useMutation({
    mutationFn: (segs: Segment[]) => api.patch(`/api/projects/${id}/transcript`, { transcript: segs }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  if (project.isLoading) {
    return <Card><Spinner label="Loading project…" /></Card>;
  }
  if (project.isError || !project.data) {
    return <ErrorBox message={errorMessage(project.error) || "Project not found."} />;
  }

  const p = project.data;
  const Icon = KIND_ICON[p.kind];
  const jobStatus = job.data?.status;
  const running = transcribe.isPending || jobStatus === "queued" || jobStatus === "running";
  const jobError =
    jobStatus === "error" ? job.data?.error || job.data?.message : transcribe.isError ? errorMessage(transcribe.error) : null;
  const pct = Math.round((job.data?.progress || 0) * 100);

  return (
    <>
      <PageHeader
        title={<span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}><Icon size={22} /> {p.name}</span>}
        subtitle={p.source_filename}
        actions={<Link to="/projects" className="os-btn os-btn-secondary os-btn-sm"><ArrowLeft size={15} /> Back</Link>}
      />

      <Card title="Details">
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap" }}>
          <Meta label="Type" value={p.kind} />
          {p.slide_count != null && <Meta label="Slides" value={String(p.slide_count)} />}
          <Meta label="Imported" value={relativeTime(p.created_at)} />
          {p.language && <Meta label="Language" value={p.language} />}
          {p.duration != null && <Meta label="Duration" value={duration(p.duration)} />}
          {p.transcribed_device && <Meta label="Transcribed on" value={p.transcribed_device.toUpperCase()} />}
        </div>
      </Card>

      {p.kind === "video" && (
        <Card title="Transcript" style={{ marginTop: 16 }}>
          {jobError && <ErrorBox message={jobError} />}

          {running ? (
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ color: "var(--muted)" }}>{job.data?.message || "Starting…"}</div>
              <div style={{ height: 8, borderRadius: 6, background: "var(--surface-3)", overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${pct}%`, background: "var(--brand)", transition: "width .3s ease" }} />
              </div>
              <div style={{ color: "var(--muted)", fontSize: 12, fontVariantNumeric: "tabular-nums" }}>{pct}%</div>
            </div>
          ) : segments && segments.length > 0 ? (
            <div style={{ display: "grid", gap: 10 }}>
              {segments.map((s, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "64px 1fr", gap: 10, alignItems: "start" }}>
                  <div style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums", fontSize: 13, paddingTop: 8 }}>{duration(s.start)}</div>
                  <Textarea
                    value={s.text}
                    rows={2}
                    onChange={(e) => setSegments(segments.map((seg, j) => (j === i ? { ...seg, text: e.target.value } : seg)))}
                  />
                </div>
              ))}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <Button variant="primary" icon={<Save size={16} />} disabled={save.isPending} onClick={() => segments && save.mutate(segments)}>
                  {save.isPending ? "Saving…" : "Save transcript"}
                </Button>
              </div>
            </div>
          ) : (
            <div style={{ display: "grid", gap: 12, justifyItems: "start" }}>
              <div style={{ color: "var(--muted)" }}>No transcript yet. Transcribe the audio to get an editable transcript.</div>
              <Button variant="primary" icon={<Wand2 size={16} />} onClick={() => transcribe.mutate()}>Transcribe audio</Button>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
