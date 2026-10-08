import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, FileText, Film, FolderOpen, Pause, Play, Presentation, Square, Trash2, Upload } from "lucide-react";
import { useConfirm } from "../components/ConfirmDialog";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useCapture } from "../context/CaptureContext";
import { CaptureDialog } from "../components/capture/CaptureDialog";
import { CapturesCard } from "../components/capture/CapturesCard";
import { CancelJobButton, JobProgress } from "../components/project/JobProgress";
import { Button, Card, EmptyState, ErrorBox, PageHeader, Spinner, Table } from "../components/ui";
import {
  RECORDING_JOB_KIND, SERVER_EDITION_LINE, capturesQueryKey, formatElapsed, isDesktopShell, offeredRecordings,
  projectOfResult, unfinishedSaveAction, type Unfinished,
} from "../lib/capture";
import { relativeTime } from "../lib/format";
import { useCaptureAvailable } from "../lib/useCaptureAvailable";
import { useFollowedJob } from "../lib/useFollowedJob";

interface Project {
  id: string;
  name: string;
  kind: "deck" | "pdf" | "video";
  source_filename: string;
  size_bytes: number;
  slide_count: number | null;
  created_at: string;
  /** Who imported it. Null on projects imported before ownership existed — those are admin-owned. */
  owner_id?: string | null;
  /** Their display name as it was at import, so it still reads after the account is gone. */
  owner_name?: string | null;
}

const ACCEPT = ".pptx,.pdf,.mp4,.mov,.mkv,.avi,.webm,.m4v";

const KIND: Record<Project["kind"], { icon: typeof FileText; label: string; color: string }> = {
  deck: { icon: Presentation, label: "Deck", color: "var(--brand)" },
  pdf: { icon: FileText, label: "PDF", color: "var(--warn)" },
  video: { icon: Film, label: "Video", color: "var(--info)" },
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export default function ProjectsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { user } = useAuth();
  const capture = useCapture();
  const fileRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [captureOpen, setCaptureOpen] = useState(false);
  // Only admins see the list of everyone's projects, so only they get an Owner
  // column — for anyone else it would be a column of their own name.
  const showOwner = user?.role === "admin";
  // The capture feature needs the desktop shell (lib/capture.ts says why):
  // THE hide rule decides the button, the dialog, the unfinished recordings
  // and the Captures card; a browser elsewhere gets the one line instead.
  const desktop = isDesktopShell();
  const canCapture = useCaptureAvailable();

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<{ projects: Project[] }>("/api/projects"),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.upload<Project>("/api/projects/import", fd);
    },
    onSuccess: (p) => {
      setNotice(`Imported "${p.name}".`);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => setNotice(errorMessage(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.delete(`/api/projects/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
    onError: (e) => setNotice(errorMessage(e)),
  });

  // The recording's save ("Saving the recording"): followed like a project's job, from the recorder's
  // own job id or, after a reload, from the scope route. When it ends with a project, the page opens it.
  const saving = useFollowedJob({
    activeUrl: canCapture ? "/api/recordings/job" : null,
    viewer: user,
    onEnded: (job) => {
      const pid = projectOfResult(job.result);
      void qc.invalidateQueries({ queryKey: ["projects"] });
      void qc.invalidateQueries({ queryKey: ["recordings"] });
      if (job.status === "done" && pid) {
        navigate(`/projects/${pid}`);
      } else if (job.status === "error") {
        setNotice(`The recording could not be saved: ${job.error || job.message}. Its chunks are kept below.`);
      }
    },
  });
  // Taken over ONCE, then cleared: left in the recorder, every later visit to this page followed the
  // finished job again and re-opened its project (seen on the first live run).
  const follow = saving.follow;
  const { savingJobId, clearSavingJob } = capture;
  useEffect(() => {
    if (!savingJobId) return;
    follow(savingJobId, RECORDING_JOB_KIND);
    clearSavingJob();
  }, [savingJobId, clearSavingJob, follow]);

  // The Dashboard's Capture tile sends the user here with the dialog asked for in the route state. It is
  // read once and the state dropped (replace), so a refresh or Back does not open the dialog again.
  const location = useLocation();
  const openFromRoute = !!(location.state as { openCapture?: boolean } | null)?.openCapture;
  useEffect(() => {
    if (!openFromRoute) return;
    if (canCapture) setCaptureOpen(true);
    navigate(location.pathname, { replace: true, state: null });
  }, [openFromRoute, canCapture, navigate, location.pathname]);

  // Recordings stopped but never saved: a crash, a closed window, a failed save.
  const unfinished = useQuery({
    queryKey: ["recordings"],
    queryFn: () => api.get<{ recordings: Unfinished[] }>("/api/recordings"),
    enabled: canCapture,
    refetchInterval: capture.state === "idle" || capture.state === "done" || capture.state === "failed" ? 30_000 : false,
  });
  // No duration: the server measures the chunks it holds. The part before a gap only with accept_loss,
  // after the user confirmed what is given up.
  const saveAgain = useMutation({
    mutationFn: (v: { id: string; chunks: number; acceptLoss: boolean }) =>
      api.post<{ job_id: string }>(`/api/recordings/${v.id}/finish`, { chunks: v.chunks, accept_loss: v.acceptLoss }),
    onSuccess: (res) => follow(res.job_id, RECORDING_JOB_KIND),
    onError: (e) => setNotice(errorMessage(e)),
  });
  const discard = useMutation({
    mutationFn: (id: string) => api.delete(`/api/recordings/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recordings"] }),
    onError: (e) => setNotice(errorMessage(e)),
  });
  const lastStill = capture.lastStillAt;
  useEffect(() => {
    if (lastStill) void qc.invalidateQueries({ queryKey: capturesQueryKey });
  }, [lastStill, qc]);
  // A recording that ends - saved, failed, or stopped by the limit - refreshes the unfinished list.
  const captureState = capture.state;
  useEffect(() => {
    if (captureState === "done" || captureState === "failed") void qc.invalidateQueries({ queryKey: ["recordings"] });
  }, [captureState, qc]);

  const pick = () => fileRef.current?.click();
  const list = projects.data?.projects ?? [];
  // The empty state owns the Import button while it is showing (see the header).
  const emptyShowing = projects.isSuccess && list.length === 0;
  // The app's own confirmation, not the browser's: in the desktop shell the
  // native confirm() was swallowed and Delete silently did nothing.
  const { confirm, dialog } = useConfirm();
  const muted = { color: "var(--muted)" } as const;
  // Offered: the stopped ones, never the one this page's recorder is making (saving or discarding it would
  // lose the rest of it); one being saved shows on its own card.
  const pendingRecordings = offeredRecordings(unfinished.data?.recordings ?? [], capture.liveRecordingId);
  const saveRecording = async (r: Unfinished) => {
    const action = unfinishedSaveAction(r);
    if (action.kind === "none") return;
    if (action.kind === "before-gap") {
      const ok = await confirm({ title: "Save the part before the gap", message: action.confirm, confirmLabel: "Save that part", danger: true });
      if (!ok) return;
    }
    saveAgain.mutate({ id: r.id, chunks: action.chunks, acceptLoss: action.kind === "before-gap" });
  };
  const recording = capture.state === "recording" || capture.state === "paused" || capture.state === "countdown";

  return (
    <>
      <PageHeader
        title="Projects"
        subtitle={desktop ? "Import a slide deck, PDF, or video to narrate and translate, or capture the screen." : "Import a slide deck, PDF, or video to narrate and translate."}
        actions={
          // Hidden only while the empty state is on screen, which carries its
          // own Import and is the better first thing to reach for: centred,
          // and it says which files are accepted. Two primary buttons doing
          // one job a couple of hundred pixels apart is what this avoids -
          // there is exactly one Import on the page at any moment.
          // Gated on isSuccess, not on list.length alone: during the first
          // load the list is also empty, and the button must not flicker out
          // and back before the projects arrive.
          <>
            {canCapture && (
              <Button icon={<Camera size={16} />} onClick={() => setCaptureOpen(true)} disabled={recording || capture.state === "saving"} title="Record the screen, or take a still (desktop app)">
                Capture
              </Button>
            )}
            {emptyShowing ? null : (
              <Button variant="primary" icon={<Upload size={16} />} onClick={pick} disabled={importMut.isPending}>
                {importMut.isPending ? "Importing…" : "Import"}
              </Button>
            )}
          </>
        }
      />
      {dialog}
      {canCapture && <CaptureDialog open={captureOpen} onClose={() => setCaptureOpen(false)} />}
      <input
        ref={fileRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) importMut.mutate(f);
          e.target.value = "";
        }}
      />

      {!desktop && (
        <p style={{ ...muted, fontSize: 13, marginTop: -6 }}>{SERVER_EDITION_LINE}</p>
      )}

      {notice && (
        <div
          className="os-card"
          role="status"
          aria-live="polite"
          style={{ padding: "10px 14px", marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}
        >
          <span>{notice}</span>
          <button className="os-icon-btn" onClick={() => setNotice(null)} title="Dismiss" aria-label="Dismiss">×</button>
        </div>
      )}

      {capture.message && (
        <div className="os-card" role="status" aria-live="polite" style={{ padding: "10px 14px", marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <span>{capture.message}</span>
          <button className="os-icon-btn" onClick={capture.dismiss} title="Dismiss" aria-label="Dismiss">×</button>
        </div>
      )}

      {recording && (
        <Card style={{ marginBottom: 16 }}>
          <div className="os-row" style={{ gap: 14 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: capture.state === "paused" ? "var(--warn)" : "var(--bad)" }} />
            <strong style={{ fontVariantNumeric: "tabular-nums" }}>{formatElapsed(capture.elapsedMs)}</strong>
            <span style={muted}>{capture.state === "countdown" ? "Starting…" : capture.state === "paused" ? "Paused" : "Recording"}{capture.hotkeysLive ? " · Shift+F9 pause · Shift+F10 stop" : ""}</span>
            <span style={{ marginLeft: "auto" }} />
            {capture.state === "paused" ? (
              <Button size="sm" icon={<Play size={14} />} onClick={capture.resume}>Resume</Button>
            ) : (
              <Button size="sm" icon={<Pause size={14} />} onClick={capture.pause} disabled={capture.state !== "recording"}>Pause</Button>
            )}
            {capture.state === "countdown" ? (
              <Button size="sm" icon={<Square size={14} />} onClick={capture.cancelCountdown}>Cancel</Button>
            ) : (
              <Button size="sm" variant="danger" icon={<Square size={14} />} onClick={capture.stop}>Stop</Button>
            )}
          </div>
        </Card>
      )}

      {saving.jobActive && saving.job && (
        <Card title="Saving the recording" style={{ marginBottom: 16 }}>
          <div style={{ display: "grid", gap: 10 }}>
            <JobProgress job={saving.job} />
            {/* The save stops at its next check; the recording stays, with its chunks, under Recordings not
                saved yet. The starter or an administrator, as every job's Cancel. */}
            {saving.mayCancel && (
              <CancelJobButton job={saving.job} pending={saving.cancelPending} title="Stop saving; the recording is kept and can be saved again" onCancel={saving.cancel} />
            )}
            {saving.cancelError ? <ErrorBox message={errorMessage(saving.cancelError)} /> : null}
          </div>
        </Card>
      )}
      {saving.notice && (
        <div className="os-card" role="status" style={{ padding: "10px 14px", marginBottom: 16, display: "flex", justifyContent: "space-between", gap: 12 }}>
          <span>{saving.notice.text}</span>
          <button className="os-icon-btn" onClick={saving.dismissNotice} title="Dismiss" aria-label="Dismiss">×</button>
        </div>
      )}

      {pendingRecordings.length > 0 && !saving.jobActive && (
        <Card title="Recordings not saved yet" subtitle="Stopped but never saved, cut short, or whose save failed - the chunks are on disk." style={{ marginBottom: 16 }}>
          {pendingRecordings.map((r) => {
            const action = unfinishedSaveAction(r);
            return (
            <div key={r.id} className="os-row" style={{ gap: 12, padding: "6px 0" }} data-recording={r.id}>
              <Film size={15} style={{ color: "var(--info)" }} />
              <span style={{ fontWeight: 500 }}>{r.name}</span>
              <span style={muted}>
                {r.chunks} chunk{r.chunks === 1 ? "" : "s"} · {fmtBytes(r.bytes)} · <span title={r.created_at}>{relativeTime(r.created_at)}</span>
                {action.kind === "before-gap" && <> · a piece is missing after {formatElapsed(r.seconds * 1000)}</>}
              </span>
              <span style={{ marginLeft: "auto" }} />
              <Button size="sm" variant="primary" onClick={() => void saveRecording(r)} disabled={saveAgain.isPending || action.kind === "none"}
                title={action.kind === "none" ? action.reason : action.kind === "before-gap" ? "Saves the part before the missing piece; asks first" : undefined}>
                {action.kind === "before-gap" ? "Save the part before the gap" : "Save as a project"}
              </Button>
              <Button size="sm" onClick={async () => { if (await confirm({ title: "Discard recording", message: <>Discard <b>{r.name}</b>? Its chunks are deleted.</>, confirmLabel: "Discard", danger: true })) discard.mutate(r.id); }}>Discard</Button>
            </div>
            );
          })}
        </Card>
      )}

      {projects.isLoading ? (
        <Card>
          <Spinner label="Loading projects…" />
        </Card>
      ) : projects.isError ? (
        <ErrorBox message={errorMessage(projects.error)} />
      ) : list.length === 0 ? (
        <Card>
          {/* This is the Import that shows on a page with nothing on it: it is
              centred, in the tile the eye already goes to, and it sits under a
              line naming the file types. The header's copy is suppressed while
              this is up, so the page never carries two primary buttons doing
              one job. */}
          <EmptyState
            icon={<FolderOpen size={30} />}
            title="No projects yet"
            sub="Import a .pptx, .pdf, or video file to get started."
            action={
              <Button variant="primary" icon={<Upload size={16} />} onClick={pick} disabled={importMut.isPending}>
                {importMut.isPending ? "Importing…" : "Import a file"}
              </Button>
            }
          />
        </Card>
      ) : (
        <Card style={{ padding: 0 }}>
          <Table headers={["Name", "Type", "Slides", "Size", ...(showOwner ? ["Owner"] : []), "Imported", ""]}>
            {list.map((p) => {
              const k = KIND[p.kind];
              const Icon = k.icon;
              return (
                <tr key={p.id}>
                  <td style={{ fontWeight: 500 }}>
                    <Link to={`/projects/${p.id}`}>{p.name}</Link>
                    <div style={{ ...muted, fontSize: 12 }}>{p.source_filename}</div>
                  </td>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <Icon size={15} style={{ color: k.color }} />
                      {k.label}
                    </span>
                  </td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{p.slide_count ?? "—"}</td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{fmtBytes(p.size_bytes)}</td>
                  {showOwner && (
                    <td className="os-nowrap">
                      {p.owner_name || (
                        // Imported before ownership existed: admin-owned, never public.
                        <span className="os-dim" title="Imported before projects had owners — only admins can see it.">
                          unassigned
                        </span>
                      )}
                    </td>
                  )}
                  <td title={p.created_at} style={muted}>{relativeTime(p.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="os-icon-btn"
                      title="Delete project"
                      aria-label={`Delete ${p.name}`}
                      onClick={async () => {
                        const ok = await confirm({
                          title: "Delete project",
                          message: <>Delete <b>{p.name}</b>? This removes its files.</>,
                          confirmLabel: "Delete",
                          danger: true,
                        });
                        if (ok) deleteMut.mutate(p.id);
                      }}
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </Table>
        </Card>
      )}

      {canCapture && <CapturesCard onNotice={setNotice} />}
    </>
  );
}
