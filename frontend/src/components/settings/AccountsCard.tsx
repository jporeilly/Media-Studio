import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, UserCheck, UserX } from "lucide-react";
import { useConfirm } from "../ConfirmDialog";
import { api, errorMessage } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { relativeTime, titleCase } from "../../lib/format";
import { Button, Card, ErrorBox, Field, Input, Modal, Select, Spinner, Table } from "../ui";
import { PolicyHint } from "./PasswordPolicyCard";

export interface Account {
  id: string;
  username: string;
  display_name: string;
  role: string;
  is_active: number;
  must_change_password: number;
  last_login: string | null;
  created_at: string;
}

/** The Enterprise edition's roles (api/store.py ROLES): editors do all studio work, admins also manage accounts and updates. */
const ROLES = ["editor", "admin"] as const;

const ROLE_HINT: Record<string, string> = {
  editor: "Studio work: projects, narration, translation.",
  admin: "Studio work plus accounts and updates.",
};

function statusOf(u: Account): string {
  if (!u.is_active) return "Deactivated";
  return u.must_change_password ? "Must change password" : "Active";
}

/** "Accounts" (admins only): the user table with add / edit / deactivate / reset-password. */
export function AccountsCard() {
  const qc = useQueryClient();
  const { user: me } = useAuth();
  // The app's own confirmation: the browser's confirm() is swallowed in the
  // desktop shell, and a deactivation that asks it would silently never run.
  const { confirm, dialog } = useConfirm();
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<{ users: Account[] }>("/api/users"),
  });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);
  const [resetting, setResetting] = useState<Account | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["users"] });

  const setActive = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => api.patch(`/api/users/${id}`, { is_active: active }),
    onSuccess: () => { setRowError(null); invalidate(); },
    onError: (e) => setRowError(errorMessage(e)),
  });

  const list = users.data?.users ?? [];

  return (
    <>
    {dialog}
    <Card
      title="Accounts"
      subtitle="New and reset accounts set their own password at first login. Deactivated accounts cannot sign in; nothing is deleted."
      actions={<Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>Add account</Button>}
    >
      {rowError && <div style={{ marginBottom: 12 }}><ErrorBox message={rowError} /></div>}
      {users.isLoading ? (
        <Spinner label="Loading accounts…" />
      ) : users.isError ? (
        <ErrorBox message={errorMessage(users.error)} />
      ) : (
        <Table headers={["Name", "Username", "Role", "Status", "Last login", ""]}>
          {list.map((u) => {
            const isMe = u.id === me?.id;
            return (
              <tr key={u.id} className={u.is_active ? "" : "os-muted"}>
                <td style={{ fontWeight: 500 }}>{u.display_name}</td>
                <td className="os-mono os-small">{u.username}</td>
                <td>{titleCase(u.role)}</td>
                <td className="os-nowrap">{statusOf(u)}</td>
                <td className="os-nowrap" title={u.last_login ?? undefined}>
                  {u.last_login ? relativeTime(u.last_login) : <span className="os-dim">never</span>}
                </td>
                <td className="os-nowrap" style={{ textAlign: "right" }}>
                  <button type="button" className="os-icon-btn" title="Edit" aria-label={`Edit ${u.display_name}`} onClick={() => setEditing(u)}>
                    <Pencil size={15} />
                  </button>
                  <button type="button" className="os-icon-btn" title="Reset password" aria-label={`Reset password for ${u.display_name}`} onClick={() => setResetting(u)}>
                    <KeyRound size={15} />
                  </button>
                  {u.is_active ? (
                    <button
                      type="button"
                      className="os-icon-btn"
                      title={isMe ? "You cannot deactivate your own account" : "Deactivate"}
                      aria-label={`Deactivate ${u.display_name}`}
                      disabled={isMe || setActive.isPending}
                      onClick={async () => {
                        const ok = await confirm({
                          title: "Deactivate account",
                          message: <>Deactivate <b>{u.display_name}</b>? They will no longer be able to sign in; you can reactivate them later.</>,
                          confirmLabel: "Deactivate",
                          danger: true,
                        });
                        if (ok) setActive.mutate({ id: u.id, active: false });
                      }}
                    >
                      <UserX size={15} />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="os-icon-btn"
                      title="Reactivate"
                      aria-label={`Reactivate ${u.display_name}`}
                      disabled={setActive.isPending}
                      onClick={async () => {
                        const ok = await confirm({
                          title: "Reactivate account",
                          message: <>Reactivate <b>{u.display_name}</b>? They will be able to sign in again.</>,
                          confirmLabel: "Reactivate",
                        });
                        if (ok) setActive.mutate({ id: u.id, active: true });
                      }}
                    >
                      <UserCheck size={15} />
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </Table>
      )}
      {creating && <AccountModal onClose={() => setCreating(false)} onSaved={() => { setCreating(false); invalidate(); }} />}
      {editing && <AccountModal account={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); invalidate(); }} />}
      {resetting && <ResetPasswordModal account={resetting} onClose={() => setResetting(null)} onDone={() => { setResetting(null); invalidate(); }} />}
    </Card>
    </>
  );
}

/** Create (no `account`) or edit (display name + role) an account. */
function AccountModal({ account, onClose, onSaved }: { account?: Account; onClose: () => void; onSaved: () => void }) {
  const { user: me } = useAuth();
  // The server refuses a self-demotion anyway; say so before the save, not after.
  const editingSelf = !!account && account.id === me?.id;
  const [form, setForm] = useState({
    username: account?.username ?? "",
    display_name: account?.display_name ?? "",
    password: "",
    role: account?.role ?? "editor",
  });
  const [error, setError] = useState<string | null>(null);
  const update = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));

  const save = useMutation({
    mutationFn: () => account
      ? api.patch(`/api/users/${account.id}`, { display_name: form.display_name.trim(), role: form.role })
      : api.post("/api/users", { username: form.username.trim(), password: form.password, display_name: form.display_name.trim(), role: form.role }),
    onSuccess: onSaved,
    onError: (e) => setError(errorMessage(e)),
  });

  const canSave = !!form.display_name.trim() && (!!account || (!!form.username.trim() && !!form.password));

  return (
    <Modal
      open
      onClose={onClose}
      title={account ? `Edit ${account.display_name}` : "Add account"}
      width={520}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending || !canSave}>
          {save.isPending ? "Saving…" : account ? "Save" : "Create account"}
        </Button>
      </>}
    >
      {error && <ErrorBox message={error} />}
      <div className="os-form-grid">
        {!account && (
          <Field label="Username">
            <Input value={form.username} onChange={(e) => update({ username: e.target.value })} autoComplete="off" autoFocus />
          </Field>
        )}
        {!account && (
          <Field label="Initial password" hint={<>They will be asked to change it at first login. <PolicyHint /></>}>
            <Input type="password" value={form.password} onChange={(e) => update({ password: e.target.value })} autoComplete="new-password" />
          </Field>
        )}
        <Field label="Display name">
          <Input value={form.display_name} onChange={(e) => update({ display_name: e.target.value })} autoFocus={!!account} />
        </Field>
        <Field label="Role" hint={editingSelf ? "You cannot change your own role." : ROLE_HINT[form.role]}>
          <Select value={form.role} onChange={(e) => update({ role: e.target.value })} disabled={editingSelf}>
            {ROLES.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}

/** Set a temporary password for someone; their sessions end and they must choose their own at next login. */
function ResetPasswordModal({ account, onClose, onDone }: { account: Account; onClose: () => void; onDone: () => void }) {
  const { user: me } = useAuth();
  const isMe = account.id === me?.id;
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reset = useMutation({
    mutationFn: () => api.post(`/api/users/${account.id}/reset-password`, { new_password: password }),
    onSuccess: onDone,
    onError: (e) => setError(errorMessage(e)),
  });

  const submit = () => {
    if (password !== confirm) { setError("The passwords do not match."); return; }
    setError(null);
    reset.mutate();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Reset password: ${account.display_name}`}
      width={460}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={reset.isPending || !password || !confirm}>
          {reset.isPending ? "Resetting…" : "Reset password"}
        </Button>
      </>}
    >
      <div className="os-muted os-small">
        {isMe
          ? "This is your own account: you will be signed out and must log in with the temporary password, then choose a new one."
          : `${account.display_name} is signed out everywhere and must choose a new password at their next login.`}
      </div>
      {error && <ErrorBox message={error} />}
      <Field label="Temporary password" hint={<PolicyHint />}>
        <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" autoFocus />
      </Field>
      <Field label="Confirm">
        <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
      </Field>
    </Modal>
  );
}
