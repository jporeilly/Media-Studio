/**
 * Capture steps (W2): the builder's dialog before a guided capture - the kind, chosen once for every step
 * (Window: the window under the pointer at the key press; Region: a region drawn once on the overlay and reused;
 * Full screen: a monitor), the pointer on or off, which steps (those without a picture, or every step), then
 * Start. Desktop app only (behind `useCaptureAvailable`, like the Capture button); the flow itself is
 * `useGuidedCapture`.
 */
import { useEffect, useState } from "react";
import { Camera, Monitor, MousePointer2, Square } from "lucide-react";
import { Button, Field, Modal, Select } from "../ui";
import { type CaptureKind, type MonitorInfo, shellBridge } from "../../lib/capture";
import type { GuidedOptions } from "../../lib/guided";

const KINDS: { key: CaptureKind; label: string; how: string }[] = [
  { key: "window", label: "Window", how: "The window under the pointer when you press Shift+F8 (from the bar's Capture button: the front-most window)." },
  { key: "region", label: "Region", how: "Drag a region on the frozen screen once; every step is cut to it." },
  { key: "screen", label: "Full screen", how: "A whole monitor, every step." },
];

export function CaptureStepsDialog({ open, missing, total, onClose, onStart }: {
  open: boolean;
  /** How many steps have no picture yet, of how many. */
  missing: number;
  total: number;
  onClose: () => void;
  onStart: (options: GuidedOptions) => void;
}) {
  const [kind, setKind] = useState<CaptureKind>("window");
  const [cursor, setCursor] = useState(true);
  const [every, setEvery] = useState(false);
  const [monitor, setMonitor] = useState<number | null>(null);
  const [monitors, setMonitors] = useState<MonitorInfo[]>([]);

  useEffect(() => {
    if (!open) return;
    setEvery(missing === 0);
    const bridge = shellBridge();
    if (bridge) (bridge.core!.invoke!("capture_monitors") as Promise<MonitorInfo[]>).then(setMonitors).catch(() => setMonitors([]));
  }, [open, missing]);

  const kindInfo = KINDS.find((k) => k.key === kind)!;
  const count = every ? total : missing;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Capture steps"
      width={620}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Camera size={16} />} disabled={count === 0} onClick={() => onStart({ kind, cursor, every, monitor })} data-capture-steps-start="">Start</Button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        A capture bar opens beside the screen with the step's text and instruction. Do the step in the application, put the pointer on the thing the viewer should click, and press <b>Shift+F8</b> (or <b>Capture</b> on the bar): the still is taken, attached to the step, and a hotspot is placed round the pointer. <b>Skip</b> leaves a step for later, <b>Back</b> returns to the one before, <b>Done</b> closes the bar. The builder is saved first and reloads when you are done.
      </p>
      <div style={{ display: "flex", gap: 8, margin: "14px 0" }}>
        {KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            className={`os-btn ${kind === k.key ? "os-btn-primary" : "os-btn-secondary"}`}
            onClick={() => setKind(k.key)}
            title={k.how}
            data-capture-kind={k.key}
            style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}
          >
            {k.key === "region" ? <Square size={15} /> : k.key === "window" ? <MousePointer2 size={15} /> : <Monitor size={15} />}
            {k.label}
          </button>
        ))}
      </div>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>{kindInfo.how}</p>
      <div style={{ display: "grid", gap: 10 }}>
        {kind === "screen" && monitors.length > 1 && (
          <Field label="Monitor">
            <Select value={monitor ?? ""} onChange={(e) => setMonitor(e.target.value === "" ? null : Number(e.target.value))} aria-label="Monitor">
              <option value="">Primary</option>
              {monitors.map((m) => <option key={m.index} value={m.index}>{`Monitor ${m.index + 1} - ${m.width}x${m.height}${m.primary ? " (primary)" : ""}`}</option>)}
            </Select>
          </Field>
        )}
        <label className="os-row" style={{ gap: 10, alignItems: "center" }}>
          <input type="checkbox" checked={cursor} onChange={(e) => setCursor(e.target.checked)} data-capture-cursor="" />
          <MousePointer2 size={15} /> <span>Cursor in the picture</span>
        </label>
        <Field label="Which steps">
          <Select value={every ? "every" : "missing"} onChange={(e) => setEvery(e.target.value === "every")} aria-label="Which steps" data-capture-which="">
            <option value="missing">{`Steps without a picture (${missing} of ${total})`}</option>
            <option value="every">{`Every step (${total})`}</option>
          </Select>
        </Field>
        {count === 0 && <p className="os-muted os-small" style={{ margin: 0 }}>Every step has a picture already; choose Every step to capture them again.</p>}
      </div>
    </Modal>
  );
}
