/**
 * The Capture dialog (T3): record the screen or take a still - which part (a region, a window, the
 * whole screen), with the microphone and the system sound for a recording, the pointer and a delay for a
 * still. What it starts is `useCapture()`'s; the rules are `lib/capture.ts`.
 */
import { useEffect, useState } from "react";
import { Camera, Monitor, Mic, MousePointer2, Square, Video, Volume2 } from "lucide-react";
import { Button, Field, Modal, Select, Tabs } from "../ui";
import { useCapture } from "../../context/CaptureContext";
import {
  RECORDING_CURSOR_NOTE, STILL_DELAYS, pickerHint, shellBridge, type CaptureKind, type CaptureMode, type MonitorInfo,
} from "../../lib/capture";

const KINDS: { key: CaptureKind; label: string; record: string; still: string }[] = [
  { key: "region", label: "Region", record: "Drag a rectangle on the frozen screen; the recording is cut to it.", still: "Drag a rectangle on the frozen screen." },
  { key: "window", label: "Window", record: "Pick the window in the share dialog.", still: "Click a window on the frozen screen." },
  { key: "screen", label: "Full screen", record: "Pick the screen in the share dialog.", still: "A whole monitor." },
];

interface Device { deviceId: string; label: string }

export function CaptureDialog({ open, onClose, initialMode = "record" }: { open: boolean; onClose: () => void; initialMode?: CaptureMode }) {
  const capture = useCapture();
  const [mode, setMode] = useState<CaptureMode>(initialMode);
  const [kind, setKind] = useState<CaptureKind>("region");
  const [microphone, setMicrophone] = useState(true);
  const [microphoneId, setMicrophoneId] = useState<string | null>(null);
  const [systemSound, setSystemSound] = useState(true);
  const [cursor, setCursor] = useState(true);
  const [delay, setDelay] = useState<number>(0);
  const [monitor, setMonitor] = useState<number | null>(null);
  const [monitors, setMonitors] = useState<MonitorInfo[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    const bridge = shellBridge();
    if (bridge) {
      (bridge.core!.invoke!("capture_monitors") as Promise<MonitorInfo[]>).then(setMonitors).catch(() => setMonitors([]));
    }
    navigator.mediaDevices?.enumerateDevices?.().then((list) => {
      setDevices(list.filter((d) => d.kind === "audioinput").map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${i + 1}` })));
    }).catch(() => setDevices([]));
  }, [open, initialMode]);

  const listMicrophones = async () => {
    // Labels arrive only once the page has been allowed a microphone: ask once, then list.
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());
      const list = await navigator.mediaDevices.enumerateDevices();
      setDevices(list.filter((d) => d.kind === "audioinput").map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${i + 1}` })));
    } catch { /* refused: the default stays */ }
  };

  const start = async () => {
    setBusy(true);
    try {
      onClose();
      if (mode === "record") {
        await capture.startRecording({ kind, microphone, microphoneId, systemSound });
      } else {
        await capture.takeStill({ kind, cursor, delaySeconds: delay, monitor });
      }
    } finally {
      setBusy(false);
    }
  };

  const unlabelled = devices.length > 0 && devices.every((d) => /^Microphone \d+$/.test(d.label));
  const kindInfo = KINDS.find((k) => k.key === kind)!;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Capture"
      width={620}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={mode === "record" ? <Video size={16} /> : <Camera size={16} />} onClick={start} disabled={busy}>
            {mode === "record" ? "Record" : delay > 0 ? `Capture in ${delay} s` : "Capture"}
          </Button>
        </>
      }
    >
      <Tabs
        tabs={[{ key: "record", label: "Record screen" }, { key: "still", label: "Capture still" }]}
        active={mode}
        onChange={(k) => setMode(k as CaptureMode)}
      />
      <div style={{ display: "flex", gap: 8, margin: "14px 0" }}>
        {KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            className={`os-btn ${kind === k.key ? "os-btn-primary" : "os-btn-secondary"}`}
            onClick={() => setKind(k.key)}
            title={mode === "record" ? k.record : k.still}
            style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}
          >
            {k.key === "region" ? <Square size={15} /> : k.key === "window" ? <MousePointer2 size={15} /> : <Monitor size={15} />}
            {k.label}
          </button>
        ))}
      </div>
      <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>{mode === "record" ? kindInfo.record : kindInfo.still}</p>

      {mode === "record" ? (
        <div style={{ display: "grid", gap: 10 }}>
          <label className="os-row" style={{ gap: 10, alignItems: "center" }}>
            <input type="checkbox" checked={microphone} onChange={(e) => setMicrophone(e.target.checked)} />
            <Mic size={15} /> <span>Microphone</span>
            {microphone && (
              <Select value={microphoneId ?? ""} onChange={(e) => setMicrophoneId(e.target.value || null)} style={{ marginLeft: "auto", maxWidth: 300 }} aria-label="Microphone device">
                <option value="">Default microphone</option>
                {devices.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label}</option>)}
              </Select>
            )}
          </label>
          {microphone && unlabelled && (
            <div className="os-row" style={{ color: "var(--muted)", fontSize: 12, marginLeft: 26 }}>
              <span>Microphone names appear once the app may use one.</span>
              <Button size="sm" variant="ghost" onClick={listMicrophones}>List microphones</Button>
            </div>
          )}
          <label className="os-row" style={{ gap: 10, alignItems: "center" }}>
            <input type="checkbox" checked={systemSound} onChange={(e) => setSystemSound(e.target.checked)} />
            <Volume2 size={15} /> <span>System sound</span>
            <span style={{ color: "var(--muted)", fontSize: 12 }}>- also turn on "Share with system audio" in the share dialog</span>
          </label>
          <div className="os-row" style={{ gap: 10, alignItems: "center", color: "var(--muted)", fontSize: 12 }}>
            <MousePointer2 size={15} /> <span>{RECORDING_CURSOR_NOTE}</span>
          </div>
          <p style={{ color: "var(--muted)", fontSize: 12, margin: "4px 0 0" }}>{pickerHint(kind)} A 3-2-1 countdown follows; Shift+F9 pauses, Shift+F10 stops. The two sounds are mixed at the same level.</p>
        </div>
      ) : (
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
            <input type="checkbox" checked={cursor} onChange={(e) => setCursor(e.target.checked)} />
            <MousePointer2 size={15} /> <span>Cursor</span>
          </label>
          <Field label="Delay" hint="Time to open a menu before the still is taken.">
            <Select value={delay} onChange={(e) => setDelay(Number(e.target.value))} aria-label="Delay">
              {STILL_DELAYS.map((d) => <option key={d} value={d}>{d === 0 ? "None" : `${d} s`}</option>)}
            </Select>
          </Field>
          <p style={{ color: "var(--muted)", fontSize: 12, margin: 0 }}>The still is a pixel-exact copy of the screen at its physical size and lands in Captures below.</p>
        </div>
      )}
    </Modal>
  );
}
