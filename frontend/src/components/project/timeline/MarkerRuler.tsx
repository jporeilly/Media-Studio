/**
 * The markers' flags on the ruler (R1d, with `useMarkers`): one flag per
 * DRAWN marker at `timeline_at` - the button the 12 px glyph alone, the name
 * a label inside it that takes no pointer -, the box that names one sitting
 * on its flag, and the dragged flag's label. The element is the root's own,
 * moved whole - every class, title and handler as it was - and takes as
 * props exactly the values it reads; the render harness reads it through the
 * root as before.
 */
import type { Dispatch, MouseEvent, PointerEvent as ReactPointerEvent, RefObject, SetStateAction } from "react";
import { MAX_MARKER_NAME, type DrawnMarker } from "../../../lib/edit";
import { timecode } from "../../../lib/format";

export interface MarkerRulerProps {
  /** The drawn markers, plus the one `M` has just dropped while the plan does not have it yet. */
  flags: DrawnMarker[];
  pps: number;
  selectedMarker: string | null;
  pending: DrawnMarker | null;
  /** The box naming a flag, and where that flag is drawn (undefined: not drawn, so no box). */
  nameBox: { id: string; draft: string } | null;
  nameBoxAt: number | undefined;
  setNameBox: Dispatch<SetStateAction<{ id: string; draft: string } | null>>;
  finishNameBox: (keep: boolean) => void;
  attachMarker: (id: string) => (node: HTMLButtonElement | null) => void;
  markerLabelRef: RefObject<HTMLDivElement | null>;
  onMarkerPointerDown: (event: ReactPointerEvent<HTMLButtonElement>, marker: DrawnMarker) => void;
  onMarkerClick: (event: MouseEvent<HTMLButtonElement>, marker: DrawnMarker) => void;
  onMarkerDoubleClick: (event: MouseEvent<HTMLButtonElement>, marker: DrawnMarker) => void;
}

export function MarkerRuler({
  flags, pps, selectedMarker, pending, nameBox, nameBoxAt, setNameBox, finishNameBox, attachMarker, markerLabelRef,
  onMarkerPointerDown, onMarkerClick, onMarkerDoubleClick,
}: MarkerRulerProps) {
  return (
    <div className="os-tl-markers">
      {flags.map((held) => (
        <button
          type="button"
          key={held.id}
          ref={attachMarker(held.id)}
          className={["os-tl-marker",
            held.id === selectedMarker ? "selected" : "",
            pending?.id === held.id ? "pending" : ""].filter(Boolean).join(" ")}
          style={{ left: held.timeline_at * pps }}
          aria-pressed={held.id === selectedMarker}
          aria-label={`Marker ${held.name} at ${timecode(held.timeline_at)}`}
          title={`${held.name} — ${timecode(held.timeline_at)}`
            + "\n\nClick to select; double-click to rename; drag to move it (Alt: no snapping); Delete removes it."
            + " Ctrl+[ and Ctrl+] jump between markers. Markers become the rendered video's chapters."}
          onPointerDown={(event) => onMarkerPointerDown(event, held)}
          onClick={(event) => onMarkerClick(event, held)}
          onDoubleClick={(event) => onMarkerDoubleClick(event, held)}
        >
          <span className="os-tl-marker-name">{held.name}</span>
        </button>
      ))}
      {nameBox && nameBoxAt !== undefined && (
        <input
          className="os-tl-marker-name-box"
          style={{ left: nameBoxAt * pps }}
          value={nameBox.draft}
          maxLength={MAX_MARKER_NAME}
          autoFocus
          aria-label="Marker name"
          placeholder="Marker name"
          title="The marker's name — Enter keeps it, Escape puts the old one back"
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => setNameBox({ id: nameBox.id, draft: event.target.value })}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === "Enter") { event.preventDefault(); finishNameBox(true); }
            else if (event.key === "Escape") { event.preventDefault(); finishNameBox(false); }
          }}
          onBlur={() => finishNameBox(true)}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
        />
      )}
      {/* The dragged flag's new moment, painted through its ref. */}
      <div className="os-tl-marker-label" ref={markerLabelRef} style={{ display: "none" }} aria-hidden="true" />
    </div>
  );
}
