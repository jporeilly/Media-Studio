import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, GitBranch, RefreshCw, RotateCcw } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, Card, ErrorBox, PageHeader, Spinner } from "../components/ui";
import { JobNotice, type Job } from "../components/project/JobProgress";
import { useFollowedJob } from "../lib/useFollowedJob";
import { AccountsCard } from "../components/settings/AccountsCard";
import { AuditCard } from "../components/settings/AuditCard";
import { PasswordCard } from "../components/settings/PasswordCard";
import { PasswordPolicyCard } from "../components/settings/PasswordPolicyCard";
import { StudioCard } from "../components/settings/StudioCard";

interface UpdateState {
  error: string | null;
  commit: string | null;
  branch: string | null;
  update_available: boolean;
  behind: number;
  latest: string | null;
}
/** What a finished update job returns (services/updater.py apply_update). */
interface UpdateResult {
  updated_from?: string | null;
  updated_to?: string | null;
  changed?: boolean;
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
  // What the last update this page followed brought, once it is done: "Update applied (a → b)".
  const [applied, setApplied] = useState<UpdateResult | null>(null);
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

  // The update this page follows: one it started, or the update already running when the page opened
  // (GET /api/system/update/job) - with the project page's rules (lib/useFollowedJob.ts): a 404 means the
  // server no longer holds it, which a restart does to every job; any other failure is tried three times in
  // a row, then the page lets go and asks after it once more when the server answers again, so an update
  // lost in a restart gets the restart line.
  const onUpdateEnded = useCallback((ended: Job) => {
    if (ended.status === "done") setApplied((ended.result as UpdateResult | null) ?? {});
    qc.invalidateQueries({ queryKey: ["system-update"] });
  }, [qc]);
  const followed = useFollowedJob({ activeUrl: "/api/system/update/job", viewer: user, onEnded: onUpdateEnded });
  const followUpdate = followed.follow;
  const apply = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/api/system/update"),
    onSuccess: (r) => {
      setApplied(null);
      followUpdate(r.job_id, "update");
    },
  });

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
  const job = followed.job;
  const applying = apply.isPending || followed.jobActive;
  const applyError = job?.status === "error" ? job.error || job.message : apply.isError ? errorMessage(apply.error) : null;
  const pct = Math.round((job?.progress || 0) * 100);
  const jobNotice = followed.notice?.text ?? null;

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

            {jobNotice && <JobNotice text={jobNotice} onDismiss={followed.dismissNotice} />}
            {applyError && <ErrorBox message={applyError} />}
            {restart.isError && <ErrorBox message={`Restart failed: ${errorMessage(restart.error)}`} />}

            {applying ? (
              <div style={{ display: "grid", gap: 8 }}>
                <div style={muted}>{job?.message || "Starting update…"}</div>
                <div style={{ height: 8, borderRadius: 6, background: "var(--surface-3)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${pct}%`, background: "var(--brand)", transition: "width .3s ease" }} />
                </div>
              </div>
            ) : applied && isAdmin ? (
              <div style={{ display: "grid", gap: 10, justifyItems: "start" }}>
                <div style={{ color: "var(--good)" }}>
                  Update applied ({applied.updated_from ?? "?"} → {applied.updated_to ?? "?"}). Restart to finish.
                </div>
                <Button variant="primary" icon={<RotateCcw size={16} />} onClick={() => restart.mutate()} disabled={restart.isPending}>
                  Restart now
                </Button>
              </div>
            ) : (
              <div style={{ display: "grid", gap: 10, justifyItems: "start" }}>
                {/* An editor watching an administrator's update: restarting is an administrator's (the route
                    answers anyone else "Insufficient permissions"), so the line says so and the card keeps its
                    own buttons. */}
                {applied && (
                  <div style={{ color: "var(--good)" }}>
                    Update applied ({applied.updated_from ?? "?"} → {applied.updated_to ?? "?"}). An administrator restarts the backend to finish.
                  </div>
                )}
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
              </div>
            )}

            <div style={{ ...muted, fontSize: 12.5 }}>
              Updates pull the latest commits from this install's Git repo and reinstall Python dependencies in place — no reinstall needed.
            </div>
          </div>
        )}
      </Card>

      <StudioCard />
      <PasswordCard />
      {/* The admin trio. Vertical 4d moves these three onto a dedicated /admin
          page; each is self-contained so that is a move, not a rewrite. */}
      {isAdmin && <AccountsCard />}
      {isAdmin && <PasswordPolicyCard />}
      {isAdmin && <AuditCard />}
    </>
  );
}
