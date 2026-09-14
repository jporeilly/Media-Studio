import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound, LogOut } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, ErrorBox, Field, Input } from "../components/ui";
import { PolicyHint } from "../components/settings/PasswordPolicyCard";

/**
 * First-login gate: RequireAuth renders this instead of the shell while the signed-in
 * account is flagged must_change_password (a new account, or one an admin reset).
 * The server applies the password policy and reports the reason; the only check made
 * here is that the two new-password fields agree.
 */
export default function ChangePasswordPage() {
  const { user, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.post("/api/auth/change-password", { current_password: current, new_password: next });
      await refresh(); // must_change_password is now 0, so RequireAuth lets the shell through
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="os-login">
      <form className="os-login-card" onSubmit={submit}>
        <div className="os-login-logo"><KeyRound size={28} color="var(--brand)" /> Set a new password</div>
        <div className="os-muted">
          Hello {user?.display_name || user?.username}. This account was just created or its password was reset,
          so choose a password of your own before continuing.
        </div>
        <Field label="Current password"><Input autoFocus type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" /></Field>
        <Field label="New password" hint={<PolicyHint />}><Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></Field>
        <Field label="Confirm new password"><Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" /></Field>
        {error && <ErrorBox message={error} />}
        <Button variant="primary" type="submit" disabled={busy || !current || !next || !confirm}>{busy ? "Saving..." : "Save"}</Button>
        <Button variant="ghost" type="button" icon={<LogOut size={16} />} onClick={async () => { await logout(); navigate("/login"); }}>
          Log out
        </Button>
      </form>
    </div>
  );
}
