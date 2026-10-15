/**
 * The builder's picture with its hotspot (W1): the step's picture at a zoom, the hotspot drawn over it as an
 * outlined rectangle with eight handles, and the pointer gestures - drag on the picture to draw one, drag
 * inside it to move it, drag a handle to resize it. Every gesture is the annotation editor's CROP maths
 * (`lib/annotate.ts`: cropFromDrag, clampCrop, cropHandles, handleAt, resizeCrop, moveCrop) with
 * MIN_HOTSPOT_SIDE passed in as the minimum side; this file owns no geometry of its own.
 *
 * While a drag runs the hotspot is changed WITHOUT a history commit (`onChange(h, false)`), so the outline
 * follows the pointer; the commit comes once at pointer-up (a drag that changed nothing commits nothing: the
 * page's history compares content). Esc during a drag puts the hotspot back as it was.
 */
import { useCallback, useEffect, useRef } from "react";
import { type HandleName, type Point, type Size, HANDLE_PX, cropFromDrag, cropHandles, handleAt, inRect, moveCrop, resizeCrop } from "../../lib/annotate";
import { type Hotspot, MIN_HOTSPOT_SIDE } from "../../lib/walkthrough";

type Drag =
  | { kind: "draw"; start: Point; base: Hotspot | null }
  | { kind: "move"; start: Point; hotspot: Hotspot; base: Hotspot | null }
  | { kind: "resize"; start: Point; hotspot: Hotspot; handle: HandleName; base: Hotspot | null };

export interface HotspotSurfaceProps {
  src: string;
  /** The picture's own size. */
  size: Size;
  /** Screen pixels per picture pixel. */
  zoom: number;
  hotspot: Hotspot | null;
  /** The changed hotspot: `commit` false while a drag runs (preview only), true once it ends. */
  onChange: (hotspot: Hotspot | null, commit: boolean) => void;
}

export function HotspotSurface({ src, size, zoom, hotspot, onChange }: HotspotSurfaceProps) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  // The pointer in the picture's pixels, kept inside it.
  const pointAt = useCallback((e: { clientX: number; clientY: number }): Point => {
    const rect = ref.current?.getBoundingClientRect() ?? { left: 0, top: 0 };
    return {
      x: Math.max(0, Math.min(size.width, (e.clientX - rect.left) / zoom)),
      y: Math.max(0, Math.min(size.height, (e.clientY - rect.top) / zoom)),
    };
  }, [zoom, size.width, size.height]);

  const dragged = useCallback((d: Drag, p: Point): Hotspot | null => {
    switch (d.kind) {
      case "draw":
        return cropFromDrag(d.start, p, size, MIN_HOTSPOT_SIDE) ?? d.base;
      case "move":
        return moveCrop(d.hotspot, p.x - d.start.x, p.y - d.start.y, size);
      case "resize":
        return resizeCrop(d.hotspot, d.handle, p, size, MIN_HOTSPOT_SIDE);
    }
  }, [size]);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const p = pointAt(e);
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* no pointer capture (a synthetic event): the drag still follows the pointer over the surface */
    }
    if (hotspot) {
      const handle = handleAt(cropHandles(hotspot), p, HANDLE_PX / zoom);
      if (handle) { drag.current = { kind: "resize", start: p, hotspot, handle, base: hotspot }; return; }
      if (inRect(p, hotspot)) { drag.current = { kind: "move", start: p, hotspot, base: hotspot }; return; }
    }
    drag.current = { kind: "draw", start: p, base: hotspot };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    onChange(dragged(d, pointAt(e)), false);
  };

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    onChange(dragged(d, pointAt(e)), true);
  };

  // A cancelled drag (the pointer lost to the browser) is no drag: the hotspot goes back as it was.
  const onPointerCancel = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    onChange(d.base, true);
  };

  // Esc while a drag runs: the hotspot goes back as it was.
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

  const width = size.width * zoom, height = size.height * zoom;
  return (
    <div
      ref={ref}
      className="ms-surface"
      data-walkthrough-surface=""
      style={{ width, height, cursor: "crosshair" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    >
      <img src={src} alt="" draggable={false} style={{ width, height }} />
      {hotspot && (
        <>
          <div className="ms-surface-spot" data-surface-hotspot="" style={{ left: hotspot.x * zoom, top: hotspot.y * zoom, width: hotspot.width * zoom, height: hotspot.height * zoom }} />
          {cropHandles(hotspot).map((h) => (
            <div key={h.name} className="ms-surface-handle" data-handle={h.name} style={{ left: h.x * zoom, top: h.y * zoom }} />
          ))}
        </>
      )}
    </div>
  );
}
