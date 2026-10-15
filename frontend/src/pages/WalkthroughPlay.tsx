/**
 * Play a walkthrough (W1): `/walkthroughs/:id/play`, full window, outside the Shell (inside RequireAuth). The
 * page loads the record and hands it to the pure Player with the API's picture URLs; the player makes no
 * call of its own. Esc (or Close) goes back to the builder when the player was opened from it, else to the
 * list. `?step=N` (or the route state's `step`) starts at that step.
 */
import { useCallback, useEffect } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api, errorMessage } from "../api/client";
import { ErrorBox, Spinner } from "../components/ui";
import { Player } from "../components/walkthrough/Player";
import { type Walkthrough, GONE_MESSAGE, builderUrl, stepImageUrl, walkthroughQueryKey } from "../lib/walkthrough";

export default function WalkthroughPlayPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  const state = (location.state as { from?: string; step?: number } | null) ?? null;
  const start = Number(search.get("step") ?? state?.step ?? 0) || 0;

  const record = useQuery({ queryKey: walkthroughQueryKey(id), queryFn: () => api.get<Walkthrough>(`/api/walkthroughs/${id}`), retry: false });

  useEffect(() => {
    const e = record.error;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) navigate("/walkthroughs", { replace: true, state: { notice: GONE_MESSAGE } });
  }, [record.error, navigate]);

  const exit = useCallback(() => navigate(state?.from === "builder" ? builderUrl(id) : "/walkthroughs"), [navigate, state?.from, id]);

  if (record.isLoading) return <div className="ms-player"><Spinner label="Loading the walkthrough…" /></div>;
  if (record.isError) {
    const e = record.error;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return <div className="ms-player"><Spinner label="Back to Walkthroughs…" /></div>;
    return <div className="ms-player" style={{ padding: 20 }}><ErrorBox message={errorMessage(e)} /></div>;
  }
  const rec = record.data;
  if (!rec) return null;
  return <Player walkthrough={rec} imageUrl={(s) => stepImageUrl(id, s.id, rec.revision)} initialStep={start} onExit={exit} />;
}
