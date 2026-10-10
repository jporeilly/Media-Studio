/**
 * The Captures list (T3): the stills taken in the desktop app, newest first, each with Annotate (T3b),
 * Add to deck…, Copy, Save as… and Delete. The thumbnail is whatever the still shows now - the annotated
 * picture once there is one, with a small "annotated" badge (the server's one oracle decides; the card
 * only busts the cache with the latest timestamp). Shown wherever stills exist, the browser edition
 * included: nothing on this card touches the screen, so it is not behind the capture hide rule (only
 * taking a still is).
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Copy, Download, PenLine, Presentation, Trash2 } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { useConfirm } from "../ConfirmDialog";
import { Button, Card, EmptyState, Spinner } from "../ui";
import { AddToDeckDialog } from "./AddToDeckDialog";
import { annotateUrl, captureImageUrl as imageUrl, capturesQueryKey, type Capture } from "../../lib/capture";
import { relativeTime } from "../../lib/format";

export function CapturesCard({ onNotice }: { onNotice: (text: string) => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm, dialog } = useConfirm();
  const [addTo, setAddTo] = useState<Capture | null>(null);

  const list = useQuery({ queryKey: capturesQueryKey, queryFn: () => api.get<{ captures: Capture[] }>("/api/captures") });

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/api/captures/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: capturesQueryKey }),
    onError: (e) => onNotice(errorMessage(e)),
  });

  const copy = async (c: Capture) => {
    try {
      const blob = await api.blob(imageUrl(c));
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      onNotice("Copied to the clipboard.");
    } catch (e) {
      onNotice(`Could not copy: ${errorMessage(e)}`);
    }
  };

  const items = list.data?.captures ?? [];
  return (
    <Card title="Captures" subtitle={items.length ? `${items.length} still${items.length === 1 ? "" : "s"}` : undefined} style={{ marginTop: 16 }}>
      {dialog}
      {list.isLoading ? (
        <Spinner label="Loading captures…" />
      ) : items.length === 0 ? (
        <EmptyState icon={<Camera size={26} />} title="No stills yet" sub="In the desktop app, Capture > Capture still takes a pixel-exact still of a region, a window or a screen; the stills are listed here from anywhere." />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14 }}>
          {items.map((c) => {
            const w = c.output_width ?? c.width, h = c.output_height ?? c.height;
            return (
              <div key={c.id} className="os-card" style={{ padding: 10, display: "grid", gap: 8 }} data-capture={c.id}>
                <a href={imageUrl(c)} target="_blank" rel="noreferrer" title="Open the still" style={{ position: "relative", display: "block" }}>
                  <img src={imageUrl(c)} alt={c.name} style={{ width: "100%", aspectRatio: `${w} / ${h}`, objectFit: "contain", background: "var(--surface-2)", borderRadius: 6 }} />
                  {c.annotated && (
                    <span className="os-chip os-chip-dense" style={{ position: "absolute", top: 6, left: 6, background: "var(--brand)" }} title="This still carries annotations; the original is kept">
                      annotated
                    </span>
                  )}
                </a>
                <div className="os-truncate" style={{ fontWeight: 500 }} title={c.name}>{c.name}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>
                  {w}×{h}{c.annotated && (w !== c.width || h !== c.height) ? ` (cropped from ${c.width}×${c.height})` : ""} · {c.kind}{c.cursor ? " · pointer" : ""} · <span title={c.created_at}>{relativeTime(c.created_at)}</span>
                </div>
                <div className="os-row" style={{ gap: 6, flexWrap: "wrap" }}>
                  <Button size="sm" icon={<PenLine size={14} />} onClick={() => navigate(annotateUrl(c.id))} title="Draw arrows, callouts, steps, blur and more over the still">Annotate</Button>
                  <Button size="sm" icon={<Presentation size={14} />} onClick={() => setAddTo(c)}>Add to deck…</Button>
                  <Button size="sm" icon={<Copy size={14} />} onClick={() => void copy(c)}>Copy</Button>
                  <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/captures/${c.id}/download`} download style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Download size={14} /> Save as…
                  </a>
                  <button
                    className="os-icon-btn"
                    title="Delete still"
                    aria-label={`Delete ${c.name}`}
                    onClick={async () => {
                      if (await confirm({ title: "Delete still", message: <>Delete <b>{c.name}</b>?</>, confirmLabel: "Delete", danger: true })) del.mutate(c.id);
                    }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <AddToDeckDialog capture={addTo} onClose={() => setAddTo(null)} onNotice={onNotice} />
    </Card>
  );
}
