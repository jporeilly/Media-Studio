/**
 * The Walkthroughs list (W1): `/walkthroughs`. A card per walkthrough - the first step's picture, the name,
 * "12 steps · 3 chapters", when it was last saved - with Open (the builder), Play, Duplicate and Delete
 * (asked first); New walkthrough asks for a name and opens the builder; Import workshop… (W2) makes one from a
 * course workshop through its dialog. The empty state says how one is made: take a still with Capture (desktop
 * app), annotate it if you like, then add it as a step - or import a workshop. Nothing here touches the screen,
 * so the page is the browser edition's too.
 */
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Footprints, Play, Plus, Route as RouteIcon, Trash2 } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { useConfirm } from "../components/ConfirmDialog";
import { Button, Card, EmptyState, ErrorBox, Field, Input, Modal, PageHeader, Spinner } from "../components/ui";
import { ImportWorkshopDialog } from "../components/walkthrough/ImportWorkshopDialog";
import { relativeTime } from "../lib/format";
import {
  type Walkthrough, type WalkthroughSummary, EMPTY_HOW, MAX_NAME_CHARS, builderUrl, countsLine, playUrl, stepImageUrl, walkthroughsQueryKey,
} from "../lib/walkthrough";

export default function WalkthroughsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const [notice, setNotice] = useState<string | null>(null);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [importing, setImporting] = useState(false);

  // The builder and the player send the user back here with a notice when their walkthrough is gone.
  const noticeFromRoute = (location.state as { notice?: string } | null)?.notice;
  useEffect(() => {
    if (!noticeFromRoute) return;
    setNotice(noticeFromRoute);
    navigate(location.pathname, { replace: true, state: null });
  }, [noticeFromRoute, navigate, location.pathname]);

  const list = useQuery({ queryKey: walkthroughsQueryKey, queryFn: () => api.get<{ walkthroughs: WalkthroughSummary[] }>("/api/walkthroughs") });

  const create = useMutation({
    mutationFn: (n: string) => api.post<Walkthrough>("/api/walkthroughs", { name: n }),
    onSuccess: (rec) => {
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey });
      setNaming(false);
      navigate(builderUrl(rec.id));
    },
    onError: (e) => setNotice(errorMessage(e)),
  });
  const duplicate = useMutation({
    mutationFn: (id: string) => api.post<Walkthrough>(`/api/walkthroughs/${id}/duplicate`),
    onSuccess: (rec) => {
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey });
      setNotice(`Copied as "${rec.name}".`);
    },
    onError: (e) => setNotice(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/api/walkthroughs/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: walkthroughsQueryKey }),
    onError: (e) => setNotice(errorMessage(e)),
  });

  const submitName = () => {
    const n = name.trim();
    if (!n || create.isPending) return;
    create.mutate(n);
  };

  const items = list.data?.walkthroughs ?? [];
  const newButton = (
    <Button variant="primary" icon={<Plus size={14} />} onClick={() => { setName(""); setNaming(true); }} data-new-walkthrough="">New walkthrough</Button>
  );
  const importButton = (
    <Button icon={<Download size={14} />} onClick={() => setImporting(true)} title="Make a walkthrough from a course workshop" data-import-workshop="">Import workshop…</Button>
  );
  // Workshop video (C1): the one canvas - pick a workshop, capture, edit, make the video, publish the course.
  const canvasButton = (
    <Button icon={<RouteIcon size={14} />} onClick={() => navigate("/walkthroughs/workshop")} title="One canvas: pick a workshop, capture it, edit, make the video, publish the course" data-workshop-video="">Workshop video…</Button>
  );
  const actions = <div className="os-row" style={{ gap: 8 }}>{canvasButton}{importButton}{newButton}</div>;

  return (
    <>
      <PageHeader title="Walkthroughs" subtitle="Click-through product tours made of stills: a hotspot, a tooltip or a card, and chapters on each step." actions={actions} />
      <ImportWorkshopDialog open={importing} onClose={() => setImporting(false)} />
      {dialog}
      {notice && (
        <div className="os-card" role="status" aria-live="polite" style={{ padding: "10px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }} data-walkthroughs-notice="">
          <span>{notice}</span>
          <button className="os-icon-btn" onClick={() => setNotice(null)} title="Dismiss" aria-label="Dismiss">×</button>
        </div>
      )}
      {list.isLoading ? (
        <Spinner label="Loading walkthroughs…" />
      ) : list.isError ? (
        <ErrorBox message={errorMessage(list.error)} />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState icon={<Footprints size={30} />} title="No walkthroughs yet" sub={EMPTY_HOW} action={actions} />
        </Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 14 }}>
          {items.map((w) => (
            <div key={w.id} className="os-card" style={{ padding: 10, display: "grid", gap: 8 }} data-walkthrough={w.id}>
              <div className="os-clickable" onClick={() => navigate(builderUrl(w.id))} title="Open the builder" style={{ aspectRatio: "16 / 9", background: "var(--surface-2)", borderRadius: 6, overflow: "hidden", display: "flex", alignItems: "center", justifyContent: "center" }}>
                {w.first_step_id ? (
                  <img src={stepImageUrl(w.id, w.first_step_id, w.revision)} alt="" style={{ width: "100%", height: "100%", objectFit: "contain" }} />
                ) : (
                  <Footprints size={28} className="os-dim" />
                )}
              </div>
              <div className="os-truncate" style={{ fontWeight: 500 }} title={w.name}>{w.name}</div>
              <div style={{ color: "var(--muted)", fontSize: 12 }} data-walkthrough-counts="">
                {countsLine(w.step_count, w.chapter_count)} · <span title={w.updated_at}>{relativeTime(w.updated_at)}</span>{w.owner_name ? ` · ${w.owner_name}` : ""}
              </div>
              <div className="os-row" style={{ gap: 6, flexWrap: "wrap" }}>
                <Button size="sm" variant="primary" onClick={() => navigate(builderUrl(w.id))}>Open</Button>
                <Button size="sm" icon={<Play size={14} />} onClick={() => navigate(playUrl(w.id), { state: { from: "list" } })} disabled={w.step_count === 0} title={w.step_count === 0 ? "Add a step first" : "Play from the first step"}>Play</Button>
                <Button size="sm" icon={<Copy size={14} />} onClick={() => duplicate.mutate(w.id)} disabled={duplicate.isPending}>Duplicate</Button>
                <button
                  className="os-icon-btn"
                  title="Delete walkthrough"
                  aria-label={`Delete ${w.name}`}
                  onClick={async () => {
                    if (await confirm({ title: "Delete walkthrough", message: <>Delete <b>{w.name}</b> and its {countsLine(w.step_count, 0)}? The stills it was made from are kept.</>, confirmLabel: "Delete", danger: true })) remove.mutate(w.id);
                  }}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Modal
        open={naming}
        onClose={() => setNaming(false)}
        title="New walkthrough"
        width={440}
        footer={
          <>
            <Button onClick={() => setNaming(false)}>Cancel</Button>
            <Button variant="primary" disabled={!name.trim() || create.isPending} onClick={submitName}>{create.isPending ? "Creating…" : "Create"}</Button>
          </>
        }
      >
        <Field label="Name">
          <Input value={name} maxLength={MAX_NAME_CHARS} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submitName(); }} placeholder="Onboarding tour" data-walkthrough-name="" />
        </Field>
      </Modal>
    </>
  );
}
