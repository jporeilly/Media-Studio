/**
 * Capture steps (W2): the builder's dialog before a guided capture - the kind, chosen once for every step
 * (Window: the window under the pointer at the click, the typed pause or the key press; Region: a region drawn once
 * on the overlay and reused; Full screen: a monitor), the pointer on or off, which steps (those without a picture,
 * or every step), Capture on click (W2b: every click in the frame takes the step's still, on by default; off is the
 * one key press per step), and since W2d: when to move on (at Next, the default - every capture until then re-takes
 * the step's picture and the last still stands - or after each capture, 0.18.0's one capture per step), the still
 * after the click settles (off by default), typing takes a still (on by default), then Start. Desktop app only
 * (behind `useCaptureAvailable`, like the Capture button); the flow itself is `useGuidedCapture`.
 */
import { useEffect, useState } from "react";
import { Camera, Monitor, MousePointer2, Square } from "lucide-react";
import { Button, Field, Modal, Select } from "../ui";
import { type CaptureKind, type MonitorInfo, shellBridge } from "../../lib/capture";
import { type GuidedAdvance, type GuidedOptions, SETTLE_MS } from "../../lib/guided";

const KINDS: { key: CaptureKind; label: string; how: string }[] = [
  { key: "window", label: "Window", how: "The window under the pointer when you click or press Shift+F8 (from the bar's Capture button: the front-most window); after typing, the window you typed into." },
  { key: "region", label: "Region", how: "Drag a region on the frozen screen once; every step is cut to it, and only a click inside it is a step." },
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
  const [clicks, setClicks] = useState(true);
  const [advance, setAdvance] = useState<GuidedAdvance>("next");
  const [settle, setSettle] = useState(false);
  const [typing, setTyping] = useState(true);
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
  const start = () => onStart({ kind, cursor, every, monitor, clicks, advance, settleMs: settle ? SETTLE_MS : 0, typing });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Capture steps"
      width={620}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Camera size={16} />} disabled={count === 0} onClick={start} data-capture-steps-start="">Start</Button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        A capture bar opens beside the screen with the step's text and instruction. Just do the step in the application: every click takes the still at that moment, attaches it to the step and places a hotspot round the click; typing takes one a second after you stop. The step is yours until you press <b>Next</b> (or <b>Shift+F9</b>): every capture until then re-takes its picture, and the last still stands. For a step with no click, put the pointer on the thing the viewer should click and press <b>Shift+F8</b> (or <b>Capture</b> on the bar). <b>Skip</b> is the same button for a step that has no still yet; <b>Back</b> returns to the step before; <b>Done</b> closes the bar. The builder is saved first and reloads when you are done.
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
        <fieldset style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "8px 12px", margin: 0, display: "grid", gap: 6 }} data-capture-advance-group="">
          <legend style={{ fontWeight: 600, padding: "0 4px" }}>Move on to the next step</legend>
          <label className="os-row" style={{ gap: 10, alignItems: "flex-start" }}>
            <input type="radio" name="capture-advance" checked={advance === "next"} onChange={() => setAdvance("next")} style={{ marginTop: 3 }} data-capture-advance="next" />
            <span><b>when I press Next</b> - every click, key press and typed pause takes this step's picture again until you press Next; the last still stands</span>
          </label>
          <label className="os-row" style={{ gap: 10, alignItems: "flex-start" }}>
            <input type="radio" name="capture-advance" checked={advance === "capture"} onChange={() => setAdvance("capture")} style={{ marginTop: 3 }} data-capture-advance="capture" />
            <span><b>after each capture</b> - one capture per step, as in 0.18.0</span>
          </label>
        </fieldset>
        <label className="os-row" style={{ gap: 10, alignItems: "flex-start" }}>
          <input type="checkbox" checked={clicks} onChange={(e) => setClicks(e.target.checked)} style={{ marginTop: 3 }} data-capture-clicks="" />
          <span><b>Capture on click</b> - every click in the frame takes the step's still; Shift+F8 still captures without a click</span>
        </label>
        <label className="os-row" style={{ gap: 10, alignItems: "flex-start" }}>
          <input type="checkbox" checked={settle} disabled={!clicks} onChange={(e) => setSettle(e.target.checked)} style={{ marginTop: 3 }} data-capture-settle="" />
          <span><b>Take the still after the click settles (0.5 s)</b> - the picture shows what the click did (a menu, a dialog); the hotspot stays where you clicked</span>
        </label>
        <label className="os-row" style={{ gap: 10, alignItems: "flex-start" }}>
          <input type="checkbox" checked={typing} onChange={(e) => setTyping(e.target.checked)} style={{ marginTop: 3 }} data-capture-typing="" />
          <span><b>Typing takes a still</b> - a still a second after you stop typing, of the window you typed into when it meets the step's last picture (before the step's first still, that window as it is), the hotspot where you last clicked in this step when that is in the picture, else round the pointer; which keys you press is never recorded</span>
        </label>
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
