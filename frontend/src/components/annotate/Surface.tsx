/**
 * The edit surface (T3b): ONE canvas showing the still with the shapes drawn by `lib/annotate.ts`'s
 * renderer (`renderPreview`, the same `drawScene` the export uses) at the current zoom, with the pointer
 * gestures over it - draw with a tool, click to place a step or a text box, select, move, resize by the
 * handles, drag a lens, drag or resize the crop, double-click a text to edit it - and the inline text box
 * while a Text or a Callout is being typed. Every gesture is a function of the pure model; this file owns no
 * geometry of its own.
 *
 * While a drag runs the document is changed WITHOUT a history commit (`onChange(doc, false)`), so the
 * preview follows the pointer; the commit comes once at pointer-up (a drag that changed nothing commits
 * nothing: `commit` compares content). Esc during a drag puts the document back as it was.
 *
 * The canvas prevents the default of `pointerdown`: that cancels the compatibility mouse events, whose
 * default would move the focus to the canvas and blur the text box the same click just opened (seen live:
 * pointerdown -> focusin TEXTAREA -> mousedown -> focusout TEXTAREA, and the empty box was removed). A
 * click on the canvas while a box is open therefore commits the box itself.
 *
 * The backing store is drawn at `backingScale` (zoom x DPR, held under what a canvas can hold) while the
 * CSS size stays still x zoom, so 400 % on a 4K still shows at the size asked for rather than going blank.
 * The first draw waits for the page's fonts, as the export does.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type AnnotationDocument, type Crop, type Ctx2D, type HandleName, type Measure, type Point, type Shape, type Size, type Style, type Tool,
  HANDLE_PX, HIT_PX, TOO_MANY_SHAPES_MESSAGE, addShape, backingScale, canAddShape, cropFromDrag, cropHandles, handleAt, handles, hitTest,
  hitsLens, inRect, measureWith, moveCrop, moveLens, moveShape, placeShape, renderPreview, resizeCrop, resizeShape, setCrop, shapeById,
  shapeFromDrag, shapeId, textBoxHeight, updateShape,
} from "../../lib/annotate";
import { fontsReady, makeScratch } from "./exportPng";
import { TextBox } from "./TextBox";

type Drag =
  | { kind: "draw"; start: Point; id: string; base: AnnotationDocument }
  | { kind: "move" | "lens"; start: Point; id: string; shape: Shape; base: AnnotationDocument }
  | { kind: "resize"; start: Point; id: string; shape: Shape; handle: HandleName; base: AnnotationDocument }
  | { kind: "crop-draw"; start: Point; base: AnnotationDocument }
  | { kind: "crop-move"; start: Point; crop: Crop; base: AnnotationDocument }
  | { kind: "crop-resize"; start: Point; crop: Crop; handle: HandleName; base: AnnotationDocument };

export interface SurfaceProps {
  doc: AnnotationDocument;
  original: HTMLImageElement | HTMLCanvasElement;
  still: Size;
  tool: Tool;
  style: Style;
  zoom: number;
  selectedId: string | null;
  editingId: string | null;
  /** The sequence a new step joins (Restart numbering starts a new one). */
  sequence: number;
  onSelect: (id: string | null) => void;
  /** A changed document: `commit` false while a drag runs (preview only), true once it ends. */
  onChange: (doc: AnnotationDocument, commit: boolean) => void;
  onEdit: (id: string | null) => void;
  onRefused: (message: string) => void;
  /** Ctrl+S inside the text box: the box is committed, then the page saves. */
  onSaveRequest: () => void;
}

export function Surface(props: SurfaceProps) {
  const { doc, original, still, tool, style, zoom, selectedId, editingId, sequence, onSelect, onChange, onEdit, onRefused, onSaveRequest } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drag = useRef<Drag | null>(null);
  const editStart = useRef<{ id: string; text: string; fresh: boolean } | null>(null);
  const [fonts, setFonts] = useState(false);
  // One scratch context for measuring text in the renderer's font.
  const measure = useMemo<Measure>(() => {
    try {
      return measureWith(makeScratch(1, 1).ctx);
    } catch {
      return (text) => text.length * 8;
    }
  }, []);

  // The page's fonts first: a draw before IBM Plex has arrived would rasterise the text in the fallback.
  useEffect(() => {
    let live = true;
    void fontsReady().then(() => { if (live) setFonts(true); });
    return () => { live = false; };
  }, []);

  // Draw: the backing store at `backingScale` (zoom x DPR, capped), the CSS size at the zoom; the
  // renderer draws in still pixels under one scale.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !fonts) return;
    const dpr = typeof window !== "undefined" && window.devicePixelRatio ? window.devicePixelRatio : 1;
    const scale = backingScale(still, zoom, dpr);
    canvas.width = Math.max(1, Math.round(still.width * scale));
    canvas.height = Math.max(1, Math.round(still.height * scale));
    canvas.style.width = `${still.width * zoom}px`;
    canvas.style.height = `${still.height * zoom}px`;
    const ctx = canvas.getContext("2d") as unknown as Ctx2D | null;
    if (!ctx) return;
    ctx.save();
    ctx.scale(scale, scale);
    renderPreview(ctx, doc, original, still, makeScratch, { selectedId, cropHandles: tool === "crop", zoom });
    ctx.restore();
  }, [doc, original, still, zoom, selectedId, tool, fonts]);

  // The pointer in still pixels, kept within the range the server accepts (-size .. 2 x size); the shapes
  // a gesture makes are clamped again by the model (`clampShape`), so a move or a resize cannot leave it.
  const pointAt = useCallback((e: { clientX: number; clientY: number }): Point => {
    const rect = canvasRef.current?.getBoundingClientRect() ?? { left: 0, top: 0 };
    const clamp = (v: number, size: number) => Math.max(-size, Math.min(2 * size, v));
    return { x: clamp((e.clientX - rect.left) / zoom, still.width), y: clamp((e.clientY - rect.top) / zoom, still.height) };
  }, [zoom, still.width, still.height]);

  /** The document a drag at `p` makes, from its base. */
  const dragged = useCallback((d: Drag, p: Point): AnnotationDocument => {
    const dx = p.x - d.start.x, dy = p.y - d.start.y;
    switch (d.kind) {
      case "draw": {
        const s = shapeFromDrag(tool, d.start, p, style, still, d.id);
        return s ? addShape(d.base, s) : d.base;
      }
      case "move":
        return updateShape(d.base, d.id, moveShape(d.shape, dx, dy, still));
      case "lens":
        return d.shape.type === "magnify" ? updateShape(d.base, d.id, moveLens(d.shape, dx, dy, still)) : d.base;
      case "resize":
        return updateShape(d.base, d.id, resizeShape(d.shape, d.handle, p, still));
      case "crop-draw":
        return setCrop(d.base, cropFromDrag(d.start, p, still) ?? d.base.crop);
      case "crop-move":
        return setCrop(d.base, moveCrop(d.crop, dx, dy, still));
      case "crop-resize":
        return setCrop(d.base, resizeCrop(d.crop, d.handle, p, still));
    }
  }, [tool, style, still]);

  const commitText = useCallback(() => {
    const start = editStart.current;
    editStart.current = null;
    onEdit(null);
    if (!start) return;
    const s = shapeById(doc, start.id);
    if (s && (s.type === "text" || s.type === "callout") && s.text.trim() === "") {
      // Nothing typed: an empty box is not a shape.
      onChange({ ...doc, shapes: doc.shapes.filter((x) => x.id !== start.id) }, true);
      onSelect(null);
      return;
    }
    onChange(doc, true);
  }, [doc, onChange, onEdit, onSelect]);

  const cancelText = useCallback(() => {
    const start = editStart.current;
    editStart.current = null;
    onEdit(null);
    if (!start) return;
    if (start.fresh) {
      onChange({ ...doc, shapes: doc.shapes.filter((x) => x.id !== start.id) }, true);
      onSelect(null);
    } else {
      onChange(updateShape(doc, start.id, (s) => (s.type === "text" || s.type === "callout" ? { ...s, text: start.text } : s)), true);
    }
  }, [doc, onChange, onEdit, onSelect]);

  const beginEdit = useCallback((id: string, fresh: boolean) => {
    const s = shapeById(doc, id);
    if (!s || (s.type !== "text" && s.type !== "callout")) return;
    editStart.current = { id, text: s.text, fresh };
    onSelect(id);
    onEdit(id);
  }, [doc, onEdit, onSelect]);

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    // No compatibility mouse events, so the box a click opens keeps its focus (see the module note).
    e.preventDefault();
    if (editingId) { commitText(); return; } // a click away from an open box commits it; the next click acts
    const p = pointAt(e);
    const tol = HIT_PX / zoom, handleTol = HANDLE_PX / zoom;
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* no pointer capture (a synthetic event): the drag still follows the pointer over the canvas */
    }
    if (tool === "select") {
      const selected = shapeById(doc, selectedId);
      if (selected) {
        const handle = handleAt(handles(selected), p, handleTol);
        if (handle) { drag.current = { kind: "resize", start: p, id: selected.id, shape: selected, handle, base: doc }; return; }
      }
      const hit = hitTest(doc, p, tol);
      if (!hit) { onSelect(null); return; }
      const shape = shapeById(doc, hit)!;
      onSelect(hit);
      drag.current = { kind: hitsLens(shape, p, tol) ? "lens" : "move", start: p, id: hit, shape, base: doc };
      return;
    }
    if (tool === "crop") {
      if (doc.crop) {
        const handle = handleAt(cropHandles(doc.crop), p, handleTol);
        if (handle) { drag.current = { kind: "crop-resize", start: p, crop: doc.crop, handle, base: doc }; return; }
        if (inRect(p, doc.crop)) { drag.current = { kind: "crop-move", start: p, crop: doc.crop, base: doc }; return; }
      }
      drag.current = { kind: "crop-draw", start: p, base: doc };
      return;
    }
    if (tool === "text" || tool === "callout" || tool === "step") {
      if (!canAddShape(doc)) { onRefused(TOO_MANY_SHAPES_MESSAGE); return; }
      const s = placeShape(tool, p, style, measure, sequence, still);
      if (!s) return;
      onChange(addShape(doc, s), true);
      onSelect(s.id);
      if (tool !== "step") {
        editStart.current = { id: s.id, text: "", fresh: true };
        onEdit(s.id);
      }
      return;
    }
    if (!canAddShape(doc)) { onRefused(TOO_MANY_SHAPES_MESSAGE); return; }
    drag.current = { kind: "draw", start: p, id: shapeId(), base: doc };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    onChange(dragged(d, pointAt(e)), false);
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    const next = dragged(d, pointAt(e));
    onChange(next, true);
    if (d.kind === "draw" && next !== d.base) onSelect(d.id);
  };

  // Esc while a drag runs: the document goes back as it was.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || !drag.current) return;
      const d = drag.current;
      drag.current = null;
      onChange(d.base, true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onChange]);

  // Double-click to edit a text: opened from the second CLICK of a double-click (`detail` 2), the one path.
  // The spec guarantees `click` after a cancelled pointerdown and says nothing of `dblclick`, so the editor
  // does not listen for `dblclick` at all - nothing can open the box twice.
  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (e.detail !== 2 || tool !== "select") return;
    const hit = hitTest(doc, pointAt(e), HIT_PX / zoom);
    if (hit) beginEdit(hit, false);
  };

  const editing = editingId ? shapeById(doc, editingId) : null;
  const cursor = tool === "select" ? "default" : tool === "text" || tool === "callout" ? "text" : "crosshair";

  return (
    <div style={{ position: "relative", display: "inline-block", lineHeight: 0, margin: 12 }}>
      <canvas
        ref={canvasRef}
        data-annotate-surface=""
        style={{ display: "block", cursor, boxShadow: "var(--shadow)", background: "#fff", touchAction: "none" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={onClick}
      />
      {editing && (editing.type === "text" || editing.type === "callout") && (
        <TextBox
          shape={editing}
          zoom={zoom}
          onChange={(text) => {
            onChange(updateShape(doc, editing.id, (s) => {
              if (s.type !== "text" && s.type !== "callout") return s;
              const height = Math.max(s.height, textBoxHeight(text, s.text_size, s.width, measure, still));
              return { ...s, text, height };
            }), false);
          }}
          onCommit={commitText}
          onCommitAndSave={() => { commitText(); onSaveRequest(); }}
          onCancel={cancelText}
        />
      )}
    </div>
  );
}
