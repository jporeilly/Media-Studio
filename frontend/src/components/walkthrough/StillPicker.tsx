/**
 * The picker of stills (W1): the caller's Captures list - thumbnails, names, the annotated badge - in a
 * dialog; one click picks one. Opened by the builder's Add step… (the picture becomes a new step) and by
 * Replace picture… (the step's picture is copied again from the still picked). The picture that will be
 * copied is whatever the still shows now - the server's one oracle - so the thumbnail is the same picture.
 * With `onTakeStill` (F2, Add step in the desktop app) a **Take a still now…** button opens the Capture
 * dialog's still tab from the builder: the still taken becomes the step.
 */
import { useQuery } from "@tanstack/react-query";
import { Camera } from "lucide-react";
import { api } from "../../api/client";
import { Button, EmptyState, Modal, Spinner } from "../ui";
import { type Capture, captureImageUrl, capturesQueryKey } from "../../lib/capture";
import { relativeTime } from "../../lib/format";

export interface StillPickerProps {
  open: boolean;
  title: string;
  /** What picking does, in one line, so the user knows what the click means. */
  lead: string;
  busy?: boolean;
  onClose: () => void;
  onPick: (capture: Capture) => void;
  /** Offered when given (the desktop app, Add step): take a still now instead of picking one. */
  onTakeStill?: () => void;
}

export function StillPicker({ open, title, lead, busy = false, onClose, onPick, onTakeStill }: StillPickerProps) {
  const list = useQuery({ queryKey: capturesQueryKey, queryFn: () => api.get<{ captures: Capture[] }>("/api/captures"), enabled: open });
  const items = list.data?.captures ?? [];
  return (
    <Modal open={open} onClose={onClose} title={title} width={720} footer={<Button onClick={onClose}>Cancel</Button>}>
      <div className="os-row" style={{ justifyContent: "space-between", gap: 10, alignItems: "flex-start" }}>
        <p style={{ marginTop: 0 }}>{lead}</p>
        {onTakeStill && (
          <Button size="sm" icon={<Camera size={14} />} onClick={onTakeStill} disabled={busy} title="Take a still of a region, a window or the screen now; it becomes the step" data-take-still="">Take a still now…</Button>
        )}
      </div>
      {list.isLoading ? (
        <Spinner label="Loading the stills…" />
      ) : items.length === 0 ? (
        <EmptyState icon={<Camera size={26} />} title="No stills yet" sub="In the desktop app, press Take a still now… (Add step…) or take one with Capture (Capture > Capture still); annotate it if you like, then pick it here." />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 10, maxHeight: "55vh", overflow: "auto" }} data-still-picker="">
          {items.map((c) => {
            const w = c.output_width ?? c.width, h = c.output_height ?? c.height;
            return (
              <button
                key={c.id}
                type="button"
                className="os-card"
                data-pick-capture={c.id}
                disabled={busy}
                onClick={() => onPick(c)}
                style={{ padding: 8, display: "grid", gap: 6, textAlign: "left", cursor: busy ? "wait" : "pointer", font: "inherit", color: "inherit" }}
                title={`${c.name} (${w} × ${h})`}
              >
                <span style={{ position: "relative", display: "block" }}>
                  <img src={captureImageUrl(c)} alt="" style={{ width: "100%", aspectRatio: `${w} / ${h}`, objectFit: "contain", background: "var(--surface-2)", borderRadius: 6, display: "block" }} />
                  {c.annotated && <span className="os-chip os-chip-dense" style={{ position: "absolute", top: 6, left: 6, background: "var(--brand)" }}>annotated</span>}
                </span>
                <span className="os-truncate" style={{ fontWeight: 500 }}>{c.name}</span>
                <span className="os-muted" style={{ fontSize: 12 }}>{w}×{h} · {relativeTime(c.created_at)}</span>
              </button>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
