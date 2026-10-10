/**
 * The editor's toolbar (T3b): the twelve tools with their keys, the one shared style (colour, stroke,
 * text size, the lens factor, Restart numbering - the last two always on the row, enabled only when their
 * tool is up or such a shape is selected, so no row ever appears or disappears and the canvas never
 * jumps), zoom, undo / redo, and the three actions - Save, Save and add to deck…, Revert to original.
 */
import type { ReactNode } from "react";
import {
  ArrowUpRight, Circle, Crop, Grid2x2, Highlighter, ListOrdered, Maximize, MessageSquare, Minus, MousePointer2, PenLine, Presentation,
  Redo2, RotateCcw, Save, Search, Square, Type, Undo2, ZoomIn, ZoomOut,
} from "lucide-react";
import { Button } from "../ui";
import {
  type LensFactor, type ShapeType, type Stroke, type Style, type TextSize, type Tool, LENS_FACTORS, PALETTE, TOOL_KEYS, TOOL_LABELS,
  TOOL_ORDER, ZOOM_PRESETS, zoomLabel,
} from "../../lib/annotate";

const ICONS: Record<Tool, ReactNode> = {
  select: <MousePointer2 size={16} />, arrow: <ArrowUpRight size={16} />, callout: <MessageSquare size={16} />, text: <Type size={16} />,
  rect: <Square size={16} />, ellipse: <Circle size={16} />, line: <Minus size={16} />, step: <ListOrdered size={16} />,
  blur: <Grid2x2 size={16} />, highlight: <Highlighter size={16} />, crop: <Crop size={16} />, magnify: <Search size={16} />,
};
const KEY_OF: Record<Tool, string> = Object.fromEntries(Object.entries(TOOL_KEYS).map(([k, t]) => [t, k.toUpperCase()])) as Record<Tool, string>;
const STROKES: { key: Stroke; label: string }[] = [{ key: "thin", label: "Thin" }, { key: "medium", label: "Medium" }, { key: "thick", label: "Thick" }];
const SIZES: { key: TextSize; label: string }[] = [{ key: "small", label: "Small" }, { key: "medium", label: "Medium" }, { key: "large", label: "Large" }];

export interface ToolbarProps {
  tool: Tool;
  onTool: (tool: Tool) => void;
  style: Style;
  onStyle: (patch: Partial<Style>) => void;
  /** The selected shape's kind: a selected highlight shows its own colour, a magnify enables the lens factor, a step Restart numbering. */
  selectedType: ShapeType | null;
  onRestartNumbering: () => void;
  zoom: number;
  onZoom: (zoom: number) => void;
  onFit: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  dirty: boolean;
  saving: boolean;
  annotated: boolean;
  onSave: () => void;
  onSaveAndAdd: () => void;
  onRevert: () => void;
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="os-row" style={{ gap: 4, flexWrap: "nowrap" }} role="group" aria-label={label}>
      {children}
    </div>
  );
}

function Pick<T extends string>({ value, options, onPick, label, disabled = false }: { value: T; options: { key: T; label: string }[]; onPick: (v: T) => void; label: string; disabled?: boolean }) {
  return (
    <Group label={label}>
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          className={`os-btn os-btn-sm ${value === o.key && !disabled ? "os-btn-primary" : "os-btn-ghost"}`}
          aria-pressed={value === o.key}
          disabled={disabled}
          title={disabled ? `${label}: for the ${label === "Lens" ? "Magnify" : label} tool` : `${label}: ${o.label}`}
          onClick={() => onPick(o.key)}
        >
          {o.label}
        </button>
      ))}
    </Group>
  );
}

export function Toolbar(p: ToolbarProps) {
  // The Highlighter's own colour: while its tool is up, and while a highlight is selected (a pick restyles it).
  const highlighting = p.tool === "highlight" || p.selectedType === "highlight";
  const colour = highlighting ? p.style.highlight : p.style.colour;
  const lensEnabled = p.tool === "magnify" || p.selectedType === "magnify";
  const stepsEnabled = p.tool === "step" || p.selectedType === "step";
  return (
    <div style={{ display: "grid", gap: 8, padding: "10px 12px", borderBottom: "1px solid var(--border)" }} data-annotate-toolbar="">
      <div className="os-row" style={{ gap: 12, justifyContent: "space-between" }}>
        <Group label="Tools">
          {TOOL_ORDER.map((t) => (
            <button
              key={t}
              type="button"
              className={`os-btn os-btn-sm ${p.tool === t ? "os-btn-primary" : "os-btn-ghost"}`}
              aria-pressed={p.tool === t}
              aria-label={TOOL_LABELS[t]}
              title={`${TOOL_LABELS[t]} (${KEY_OF[t]})`}
              onClick={() => p.onTool(t)}
              data-tool={t}
            >
              {ICONS[t]}
            </button>
          ))}
        </Group>
        <Group label="History">
          <Button size="sm" variant="ghost" icon={<Undo2 size={15} />} onClick={p.onUndo} disabled={!p.canUndo} title="Undo (Ctrl+Z)">Undo</Button>
          <Button size="sm" variant="ghost" icon={<Redo2 size={15} />} onClick={p.onRedo} disabled={!p.canRedo} title="Redo (Ctrl+Y)">Redo</Button>
        </Group>
        <Group label="Actions">
          <Button size="sm" variant="primary" icon={<Save size={15} />} onClick={p.onSave} disabled={p.saving || !p.dirty} title="Save the annotated picture (Ctrl+S)">
            {p.saving ? "Saving…" : "Save"}
          </Button>
          <Button size="sm" icon={<Presentation size={15} />} onClick={p.onSaveAndAdd} disabled={p.saving} title="Save, then add the annotated picture to a deck as its last slide">
            Save and add to deck…
          </Button>
          <Button size="sm" variant="ghost" icon={<RotateCcw size={15} />} onClick={p.onRevert} disabled={p.saving || (!p.annotated && !p.dirty)} title="Throw the annotations away; the original is kept as it is">
            Revert to original
          </Button>
        </Group>
      </div>
      <div className="os-row" style={{ gap: 14, justifyContent: "space-between" }} data-annotate-style-row="">
        <div className="os-row" style={{ gap: 14 }}>
          <Group label={highlighting ? "Highlighter colour" : "Colour"}>
            {PALETTE.map((c) => (
              <button
                key={c.hex}
                type="button"
                aria-label={c.name}
                aria-pressed={colour === c.hex}
                title={c.name}
                onClick={() => p.onStyle(highlighting ? { highlight: c.hex } : { colour: c.hex })}
                style={{
                  width: 20, height: 20, borderRadius: "50%", background: c.hex, cursor: "pointer", padding: 0,
                  border: colour === c.hex ? "2px solid var(--text)" : "1px solid var(--border-strong)",
                  boxShadow: colour === c.hex ? "0 0 0 2px var(--surface)" : undefined,
                }}
              />
            ))}
          </Group>
          <Pick value={p.style.stroke} options={STROKES} onPick={(v) => p.onStyle({ stroke: v })} label="Stroke" />
          <Pick value={p.style.textSize} options={SIZES} onPick={(v) => p.onStyle({ textSize: v })} label="Text size" />
          <Pick
            value={String(p.style.lensFactor)}
            options={LENS_FACTORS.map((f) => ({ key: String(f), label: `${f}×` }))}
            onPick={(v) => p.onStyle({ lensFactor: Number(v) as LensFactor })}
            label="Lens"
            disabled={!lensEnabled}
          />
          <Button size="sm" variant="ghost" icon={<PenLine size={14} />} onClick={p.onRestartNumbering} disabled={!stepsEnabled} title={stepsEnabled ? "The next step starts a new 1, 2, 3 …" : "Restart numbering: for the Step tool"}>
            Restart numbering
          </Button>
        </div>
        <Group label="Zoom">
          <Button size="sm" variant="ghost" icon={<Maximize size={14} />} onClick={p.onFit} title="Fit the whole still (0)">Fit</Button>
          {ZOOM_PRESETS.map((z) => (
            <button key={z} type="button" className={`os-btn os-btn-sm ${Math.abs(p.zoom - z) < 1e-6 ? "os-btn-primary" : "os-btn-ghost"}`} aria-pressed={Math.abs(p.zoom - z) < 1e-6} onClick={() => p.onZoom(z)} title={`Zoom to ${zoomLabel(z)}`}>
              {zoomLabel(z)}
            </button>
          ))}
          <button type="button" className="os-icon-btn" onClick={p.onZoomOut} title="Zoom out (−)" aria-label="Zoom out"><ZoomOut size={15} /></button>
          <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 44, textAlign: "center", fontSize: 12.5 }} aria-live="polite" data-zoom="">{zoomLabel(p.zoom)}</span>
          <button type="button" className="os-icon-btn" onClick={p.onZoomIn} title="Zoom in (+)" aria-label="Zoom in"><ZoomIn size={15} /></button>
        </Group>
      </div>
    </div>
  );
}
