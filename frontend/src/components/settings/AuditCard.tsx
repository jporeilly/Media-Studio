import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ScrollText, Trash2 } from "lucide-react";
import { useConfirm } from "../ConfirmDialog";
import { api, errorMessage, qs } from "../../api/client";
import { relativeTime } from "../../lib/format";
import { Button, Card, EmptyState, ErrorBox, Select, Spinner, Table } from "../ui";
import type { Account } from "./AccountsCard";

export interface AuditEntry {
  id: number;
  user_id: string | null;
  username: string | null;
  action: string;
  entity: string | null;
  entity_id: string | null;
  detail: string | null;
  created_at: string;
}

interface AuditPayload {
  entries: AuditEntry[];
  /** The whole action vocabulary, from the server — never a copy kept here. */
  actions: string[];
}

const LIMITS = [50, 100, 250, 1000];
/** Retention windows offered to the purge control (the API takes any 1..3650). */
const PURGE_DAYS = [30, 90, 365];

/**
 * "Audit log" (admins only): who did what, newest first.
 *
 * Filtering is SERVER-side — the action and user go to the API as query
 * parameters and both columns are indexed. OpenSight's equivalent tab
 * substring-matches a fixed 200-row window in the browser, which silently
 * cannot find anything older than those 200 rows.
 *
 * Self-contained on purpose: vertical 4d moves this card, Accounts and
 * Password policy onto a dedicated /admin page, and that should be a move of
 * one line, not a rewrite.
 */
export function AuditCard() {
  const qc = useQueryClient();
  // The app's own confirmation: the browser's confirm() is swallowed in the
  // desktop shell, and a purge that asks it would silently never run.
  const { confirm, dialog } = useConfirm();
  const [action, setAction] = useState("");
  const [userId, setUserId] = useState("");
  const [limit, setLimit] = useState(100);
  const [purgeDays, setPurgeDays] = useState(PURGE_DAYS[1]);
  const [notice, setNotice] = useState<string | null>(null);

  const audit = useQuery({
    queryKey: ["audit", { action, userId, limit }],
    queryFn: () => api.get<AuditPayload>(`/api/admin/audit${qs({ limit, action, user_id: userId })}`),
  });
  // Shares the Accounts card's cache: the same list, fetched once.
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<{ users: Account[] }>("/api/users"),
  });

  const purge = useMutation({
    mutationFn: (days: number) => api.post<{ deleted: number; days: number }>(`/api/admin/maintenance/purge-audit${qs({ days })}`),
    onSuccess: (r) => {
      setNotice(`Removed ${r.deleted} entr${r.deleted === 1 ? "y" : "ies"} older than ${r.days} days.`);
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (e) => setNotice(errorMessage(e)),
  });

  const entries = audit.data?.entries ?? [];
  const actions = audit.data?.actions ?? [];
  const filtered = !!action || !!userId;

  return (
    <>
    {dialog}
    <Card
      title="Audit log"
      subtitle="Every change made through the app: sign-ins, projects, slide edits, accounts and settings. Settings entries record which keys changed, never their values."
      actions={
        <Button icon={<RefreshCw size={16} />} onClick={() => audit.refetch()} disabled={audit.isFetching}>
          {audit.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      }
    >
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 14 }}>
        <Select aria-label="Filter by action" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">All actions</option>
          {actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </Select>
        <Select aria-label="Filter by user" value={userId} onChange={(e) => setUserId(e.target.value)}>
          <option value="">All users</option>
          {(users.data?.users ?? []).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
        </Select>
        <Select aria-label="Number of entries" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {LIMITS.map((n) => <option key={n} value={n}>Latest {n}</option>)}
        </Select>
        {filtered && (
          <Button variant="ghost" onClick={() => { setAction(""); setUserId(""); }}>Clear filters</Button>
        )}
      </div>

      {notice && <div className="os-muted os-small" style={{ marginBottom: 10 }} role="status">{notice}</div>}
      {audit.isError && <ErrorBox message={errorMessage(audit.error)} />}

      {audit.isLoading ? (
        <Spinner label="Loading the audit log…" />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={<ScrollText size={30} />}
          title={filtered ? "Nothing matches those filters" : "Nothing recorded yet"}
          sub={filtered ? "Clear the filters to see the whole log." : "Entries appear as people sign in and change things."}
        />
      ) : (
        <Table headers={["When", "User", "Action", "Entity", "Detail"]}>
          {entries.map((e) => (
            <tr key={e.id}>
              <td className="os-nowrap" title={e.created_at}>{relativeTime(e.created_at)}</td>
              <td
                className="os-nowrap"
                title={
                  e.username && !e.user_id
                    ? "No account behind this entry — a failed sign-in, or the account has since been removed."
                    : undefined
                }
              >
                {e.username || <span className="os-dim">system</span>}
              </td>
              <td className="os-mono os-small os-nowrap">{e.action}</td>
              <td className="os-small os-nowrap">
                {e.entity ?? "—"}
                {e.entity_id && <span className="os-dim os-mono"> {e.entity_id}</span>}
              </td>
              <td className="os-small">{e.detail ?? ""}</td>
            </tr>
          ))}
        </Table>
      )}

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 16 }}>
        <span className="os-muted os-small">Retention is yours to set:</span>
        <Select aria-label="Purge entries older than" value={purgeDays} onChange={(e) => setPurgeDays(Number(e.target.value))}>
          {PURGE_DAYS.map((d) => <option key={d} value={d}>older than {d} days</option>)}
        </Select>
        <Button
          variant="danger"
          icon={<Trash2 size={16} />}
          disabled={purge.isPending}
          onClick={async () => {
            const ok = await confirm({
              title: "Purge the audit log",
              message: <>Delete every audit entry older than <b>{purgeDays} days</b>? This cannot be undone.</>,
              confirmLabel: "Delete entries",
              danger: true,
            });
            if (ok) {
              setNotice(null);
              purge.mutate(purgeDays);
            }
          }}
        >
          {purge.isPending ? "Purging…" : "Purge"}
        </Button>
      </div>
    </Card>
    </>
  );
}
