/**
 * The Captures list (T3): the stills taken in the desktop app, newest first, each with Add to deck…,
 * Copy, Save as… and Delete.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Copy, Download, Presentation, Trash2 } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { useConfirm } from "../ConfirmDialog";
import { Button, Card, EmptyState, Modal, Select, Spinner } from "../ui";
import { captureImageUrl as imageUrl, capturesQueryKey, type Capture } from "../../lib/capture";
import { relativeTime } from "../../lib/format";
import { slidesQueryKey } from "../../lib/slides";

interface DeckProject { id: string; name: string; kind: string }

export function CapturesCard({ onNotice }: { onNotice: (text: string) => void }) {
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const [addTo, setAddTo] = useState<Capture | null>(null);
  const [deck, setDeck] = useState<string>("");

  const list = useQuery({ queryKey: capturesQueryKey, queryFn: () => api.get<{ captures: Capture[] }>("/api/captures") });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<{ projects: DeckProject[] }>("/api/projects"),
    enabled: !!addTo,
  });
  const decks = (projects.data?.projects ?? []).filter((p) => p.kind === "deck");

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/api/captures/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: capturesQueryKey }),
    onError: (e) => onNotice(errorMessage(e)),
  });
  const add = useMutation({
    mutationFn: ({ id, projectId }: { id: string; projectId: string }) => api.post<{ index: number; slide_count: number }>(`/api/captures/${id}/add-to-deck`, { project_id: projectId }),
    onSuccess: (r, vars) => {
      onNotice(`Added as slide ${r.index + 1} of ${r.slide_count}.`);
      void qc.invalidateQueries({ queryKey: slidesQueryKey(vars.projectId) });
      void qc.invalidateQueries({ queryKey: ["projects"] });
      setAddTo(null);
    },
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
        <EmptyState icon={<Camera size={26} />} title="No stills yet" sub="Capture > Capture still takes a pixel-exact still of a region, a window or a screen." />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14 }}>
          {items.map((c) => (
            <div key={c.id} className="os-card" style={{ padding: 10, display: "grid", gap: 8 }}>
              <a href={imageUrl(c)} target="_blank" rel="noreferrer" title="Open the still">
                <img src={imageUrl(c)} alt={c.name} style={{ width: "100%", aspectRatio: `${c.width} / ${c.height}`, objectFit: "contain", background: "var(--surface-2)", borderRadius: 6 }} />
              </a>
              <div className="os-truncate" style={{ fontWeight: 500 }} title={c.name}>{c.name}</div>
              <div style={{ color: "var(--muted)", fontSize: 12 }}>
                {c.width}×{c.height} · {c.kind}{c.cursor ? " · pointer" : ""} · <span title={c.created_at}>{relativeTime(c.created_at)}</span>
              </div>
              <div className="os-row" style={{ gap: 6, flexWrap: "wrap" }}>
                <Button size="sm" icon={<Presentation size={14} />} onClick={() => { setAddTo(c); setDeck(""); }}>Add to deck…</Button>
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
          ))}
        </div>
      )}
      <Modal
        open={!!addTo}
        onClose={() => setAddTo(null)}
        title="Add to deck"
        width={480}
        footer={
          <>
            <Button onClick={() => setAddTo(null)}>Cancel</Button>
            <Button variant="primary" disabled={!deck || add.isPending} onClick={() => addTo && add.mutate({ id: addTo.id, projectId: deck })}>
              {add.isPending ? "Adding…" : "Add as last slide"}
            </Button>
          </>
        }
      >
        {decks.length === 0 ? (
          <p>There is no deck project to add to. Import a .pptx first; a PDF's pages are fixed.</p>
        ) : (
          <>
            <p style={{ marginTop: 0 }}>The still becomes the deck's last slide, letterboxed to the deck's slide size when its shape differs, with empty notes to write.</p>
            <Select value={deck} onChange={(e) => setDeck(e.target.value)}>
              <option value="">Choose a deck…</option>
              {decks.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          </>
        )}
      </Modal>
    </Card>
  );
}
