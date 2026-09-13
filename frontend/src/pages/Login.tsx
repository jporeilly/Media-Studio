import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Clapperboard } from "lucide-react";
import { errorMessage } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, ErrorBox, Field, Input } from "../components/ui";

export default function LoginPage() {
  const { user, login, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!loading && user) return <Navigate to={(location.state as any)?.from || "/"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
      navigate((location.state as any)?.from || "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="os-login">
      <form className="os-login-card" onSubmit={submit}>
        <div className="os-login-logo"><Clapperboard size={28} color="var(--brand)" /> Media Studio Enterprise</div>
        <div className="os-muted">Video creation studio. Sign in to continue.</div>
        <Field label="Username"><Input autoFocus value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></Field>
        <Field label="Password"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
        {error && <ErrorBox message={error} />}
        <Button variant="primary" type="submit" disabled={busy || !username || !password}>{busy ? "Signing in..." : "Sign in"}</Button>
        <div className="os-login-demo">
          <strong>First run:</strong> admin / admin (change it on first login).
        </div>
      </form>
    </div>
  );
}
