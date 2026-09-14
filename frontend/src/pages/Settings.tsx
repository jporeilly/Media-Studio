import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, GitBranch, RefreshCw, RotateCcw } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, Card, ErrorBox, PageHeader, Spinner } from "../components/ui";
import { AccountsCard } from "../components/settings/AccountsCard";
import { PasswordCard } from "../components/settings/PasswordCard";
import { PasswordPolicyCard } from "../components/settings/PasswordPolicyCard";

interface UpdateState {
  error: string | null;
  commit: string | null;
  branch: string | null;
  update_available: boolean;
  behind: number;
  latest: string | null;
}
interface Job {
  id: string;
  status: string;
  progress: number;
  message: string;
  error?: string | null;
  result?: { updated_from?: string | null; updated_to?: string | null; changed?: boolean } | null;
}
interface Health {
  status: string;
  version: string;
}

const muted = { color: "var(--muted)" } as const;

// The desktop shell gives a cold start of the media stack four minutes before it
// gives up (desktop/dist/index.html DEADLINE_MS); a post-update start is that same
// cold start with freshly pulled files. Past this the backend is not coming back,
// and a spinner that never stops is worse than a message that says so.
const RESTART_WAIT_MS = 240_000;

export default function SettingsPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isAdmin = user?.role === "admin";
  const [jobId, setJobId] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [pollBack, setPollBack] = useState(false);
  const [restartTimedOut, setRestartTimedOut] = useState(false);

  // The version is shown once, in the sidebar footer (from the API); this page
  // does not repeat it. Here the installed commit is what matters.
  const update = useQuery({
    queryKey: ["system-update"],
    queryFn: () => api.get<UpdateState>("/api/system/update"),
    // Always re-check on a visit to Settings: a cached (possibly stale) result
    // here misleads — e.g. an "upstream" error that has since been fixed.
    staleTime: 0,
  });

  const apply = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/api/system/update"),
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
  useEffect(() => {
    if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["system-update"] });
  }, [job.data?.status, qc]);

  const restart = useMutation({
    mutationFn: () => api.post("/api/system/restart"),
    onSuccess: () => {
      setRestarting(true);
      // Give the backend a moment to actually go down before we start waiting for it to return.
      setTimeout(() => setPollBack(true), 2500);
    },
  });
  // After a restart, wait for the backend to answer again, then reload to pick up new code.
  const back = useQuery({
    queryKey: ["health-after-restart"],
    queryFn: () => api.get<Health>("/api/system/health"),
    enabled: pollBack && !restartTimedOut,
    retry: false,
    refetchInterval: (q) => (q.state.data?.status === "ok" ? false : 1500),
  });
  useEffect(() => {
    if (pollBack && back.data?.status === "ok") {
      const t = setTimeout(() => window.location.reload(), 600);
      return () => clearTimeout(t);
    }
  }, [pollBack, back.data?.status]);
  // Bound the wait: a backend that has not answered in RESTART_WAIT_MS is down,
  // and the page must say so rather than spin until the window is closed.
  // "Keep waiting" clears the flag, which re-arms this timer for another round.
  useEffect(() => {
    if (!restarting || restartTimedOut) return;
    const t = setTimeout(() => setRestartTimedOut(true), RESTART_WAIT_MS);
    return () => clearTimeout(t);
  }, [restarting, restartTimedOut]);

  const u = update.data;
  const jobStatus = job.data?.status;
  const applying = apply.isPending || jobStatus === "queued" || jobStatus === "running";
  const applied = jobStatus === "done";
  const applyError = jobStatus === "error" ? job.data?.error || job.data?.message : apply.isError ? errorMessage(apply.error) : null;
  const pct = Math.round((job.data?.progress || 0) * 100);

  return (
    <>
      <PageHeader title="Settings" subtitle="Studio and account settings." />

      <Card title="Updates">
        {restarting && restartTimedOut && back.data?.status !== "ok" ? (
          <div style={{ display: "grid", gap: 10, justifyItems: "start" }}>
            <ErrorBox message="The backend hasn't come back after four minutes. Relaunch Media Studio Enterprise (or restart the server), then return to Settings › Updates to confirm the installed version." />
            {/* Not a page reload: in the desktop app this page is served by the
                very backend that is down, so a reload would land on a browser
                error page. Re-arming the poll keeps the automatic reload path. */}
            <Button icon={<RefreshCw size={16} />} onClick={() => setRestartTimedOut(false)}>
              Keep waiting
            </Button>
          </div>
        ) : restarting ? (
          <div style={{ display: "grid", gap: 10 }}>
            <Spinner label={pollBack && back.data?.status === "ok" ? "Back online — reloading…" : "Restarting the backend…"} />
            <div style={{ ...muted, fontSize: 13 }}>This page reloads automatically once the app is back.</div>
          </div>
        ) : update.isLoading ? (
          <Spinner label="Checking the installed version…" />
        ) : update.isError ? (
          <ErrorBox message={errorMessage(update.error)} />
        ) : (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "flex", gap: 28, flexWrap: "wrap" }}>
              <div>
                <div style={{ ...muted, fontSize: 12, textTransform: "uppercase", letterSpacing: ".04em" }}>Installed</div>
                <div style={{ fontWeight: 500, fontFamily: "var(--mono)" }}>{u?.commit ?? "—"}</div>
              </div>
              <div>
                <div style={{ ...muted, fontSize: 12, textTransform: "uppercase", letterSpacing: ".04em" }}>Branch</div>
                <div style={{ fontWeight: 500, display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <GitBranch size={14} /> {u?.branch ?? "—"}
                </div>
              </div>
              {u?.latest && (
                <div>
                  <div style={{ ...muted, fontSize: 12, textTransform: "uppercase", letterSpacing: ".04em" }}>Latest upstream</div>
                  <div style={{ fontWeight: 500, fontFamily: "var(--mono)" }}>{u.latest}</div>
                </div>
              )}
            </div>

            {u?.error ? (
              <ErrorBox message={u.error} />
            ) : u?.update_available ? (
              <div style={{ color: "var(--warn)", fontWeight: 500 }}>
                An update is available — {u.behind} new commit{u.behind === 1 ? "" : "s"} on {u.branch}.
              </div>
            ) : (
              <div style={{ color: "var(--good)", fontWeight: 500 }}>You're on the latest version.</div>
            )}

            {applyError && <ErrorBox message={applyError} />}
            {restart.isError && <ErrorBox message={`Restart failed: ${errorMessage(restart.error)}`} />}

            {applying ? (
              <div style={{ display: "grid", gap: 8 }}>
                <div style={muted}>{job.data?.message || "Starting update…"}</div>
                <div style={{ height: 8, borderRadius: 6, background: "var(--surface-3)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${pct}%`, background: "var(--brand)", transition: "width .3s ease" }} />
                </div>
              </div>
            ) : applied ? (
              <div style={{ display: "grid", gap: 10, justifyItems: "start" }}>
                <div style={{ color: "var(--good)" }}>
                  Update applied ({job.data?.result?.updated_from ?? "?"} → {job.data?.result?.updated_to ?? "?"}). Restart to finish.
                </div>
                <Button variant="primary" icon={<RotateCcw size={16} />} onClick={() => restart.mutate()} disabled={restart.isPending}>
                  Restart now
                </Button>
              </div>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Button icon={<RefreshCw size={16} />} onClick={() => update.refetch()} disabled={update.isFetching}>
                  {update.isFetching ? "Checking…" : "Check for updates"}
                </Button>
                {isAdmin && u?.update_available && !u.error && (
                  <Button variant="primary" icon={<Download size={16} />} onClick={() => apply.mutate()}>
                    Update now
                  </Button>
                )}
                {isAdmin && (
                  <Button icon={<RotateCcw size={16} />} onClick={() => restart.mutate()} disabled={restart.isPending}>
                    Restart backend
                  </Button>
                )}
              </div>
            )}

            <div style={{ ...muted, fontSize: 12.5 }}>
              Updates pull the latest commits from this install's Git repo and reinstall Python dependencies in place — no reinstall needed.
            </div>
          </div>
        )}
      </Card>

      <PasswordCard />
      {isAdmin && <AccountsCard />}
      {isAdmin && <PasswordPolicyCard />}
    </>
  );
}
