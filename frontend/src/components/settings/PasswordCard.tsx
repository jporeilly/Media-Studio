import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { Button, Card, ErrorBox, Field, Input } from "../ui";
import { PolicyHint } from "./PasswordPolicyCard";
import { titleCase } from "../../lib/format";

const EMPTY = { current: "", next: "", confirm: "" };

/** "Your account": who is signed in, and the change-password form. The server applies the password policy. */
export function PasswordCard() {
  const { user } = useAuth();
  const [pw, setPw] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const change = useMutation({
    mutationFn: () => api.post("/api/auth/change-password", { current_password: pw.current, new_password: pw.next }),
    onSuccess: () => { setPw(EMPTY); setError(null); setSaved(true); },
    onError: (e) => { setSaved(false); setError(errorMessage(e)); },
  });

  const edit = (patch: Partial<typeof pw>) => { setSaved(false); setPw((f) => ({ ...f, ...patch })); };

  function submit(e: FormEvent) {
    e.preventDefault();
    if (pw.next !== pw.confirm) { setError("The new passwords do not match."); return; }
    setError(null);
    change.mutate();
  }

  if (!user) return null;
  return (
    <Card title="Your account">
      <form onSubmit={submit} style={{ display: "grid", gap: 14 }}>
        <div className="os-muted">Signed in as <strong>{user.username}</strong> ({titleCase(user.role)}).</div>
        <div className="os-form-grid">
          <Field label="Current password"><Input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => edit({ current: e.target.value })} /></Field>
          <div />
          <Field label="New password" hint={<PolicyHint />}><Input type="password" autoComplete="new-password" value={pw.next} onChange={(e) => edit({ next: e.target.value })} /></Field>
          <Field label="Confirm new password"><Input type="password" autoComplete="new-password" value={pw.confirm} onChange={(e) => edit({ confirm: e.target.value })} /></Field>
        </div>
        {error && <ErrorBox message={error} />}
        {saved && <div style={{ color: "var(--good)", fontWeight: 500 }}>Password changed.</div>}
        <div>
          <Button type="submit" variant="primary" icon={<KeyRound size={16} />} disabled={change.isPending || !pw.current || !pw.next || !pw.confirm}>
            {change.isPending ? "Saving…" : "Change password"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
