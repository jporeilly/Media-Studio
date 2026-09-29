/**
 * The Music lane's markup (R1c, with `useMusicLane`): Camtasia's clips, one
 * dark block per clip with the file's name, its waveform inside, the fades as
 * triangles at its ends and an 8 px trim zone at each edge, then the dragged
 * clip's label. The element is the root's own, moved whole - every class,
 * title and handler as it was - and takes as props exactly the values it
 * reads; the render harness reads it through the root as before.
 */
import type { MouseEvent, PointerEvent as ReactPointerEvent, RefObject } from "react";
import { clipEnd, clipGain, clipLength, clipPeaks, type LaneLocks, type MusicClip } from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import { poolPeaks, waveformPath, type WaveformPeaks } from "../../../lib/timeline";

export interface MusicLaneProps {
  /** The clips as the plan holds them, and the strip's locks (only `music` is read). */
  storedMusic: MusicClip[];
  locks: Pick<LaneLocks, "music">;
  pps: number;
  selectedClip: string | null;
  /** The files' cached peaks by name, sliced per clip here. */
  filePeaks: Record<string, WaveformPeaks>;
  attachClip: (id: string) => (node: HTMLButtonElement | null) => void;
  clipLabelRef: RefObject<HTMLDivElement | null>;
  onClipPointerDown: (event: ReactPointerEvent<HTMLElement>, clip: MusicClip) => void;
  onClipClick: (event: MouseEvent<HTMLButtonElement>, clip: MusicClip) => void;
}

export function MusicLane({
  storedMusic, locks, pps, selectedClip, filePeaks, attachClip, clipLabelRef, onClipPointerDown, onClipClick,
}: MusicLaneProps) {
  return (
    <div className={`os-tl-music${locks.music ? " locked" : ""}`}>
      {storedMusic.map((held) => {
        const length = clipLength(held);
        const width = Math.max(3, length * pps);
        const held_peaks = filePeaks[held.file];
        const pooledClip = held_peaks
          ? poolPeaks(clipPeaks(held_peaks.peaks, held_peaks.bucket_seconds, held), Math.round(width))
          : [];
        const classes = ["os-tl-clip",
          held.id === selectedClip ? "selected" : "",
          held.missing ? "missing" : ""].filter(Boolean).join(" ");
        return (
          <button
            type="button"
            key={held.id}
            ref={attachClip(held.id)}
            className={classes}
            aria-pressed={held.id === selectedClip}
            aria-label={`Music clip ${held.file} at ${timecode(held.at)}`}
            title={`${held.file}\n${timecode(held.at)} – ${timecode(clipEnd(held))}`
              + ` (${timecode(held.in)} – ${timecode(held.out)} of the file)`
              + `\nLevel ${Math.round(clipGain(held.gain) * 100)}%`
              + `, fades ${held.fade_in.toFixed(1)} s in / ${held.fade_out.toFixed(1)} s out`
              + (held.missing
                ? "\n\nMISSING: this file is no longer in the library, so it is silent here and the"
                  + " render will refuse. Drag to move it, set its level and fades, or Delete to remove"
                  + " it — it cannot be trimmed while the file is gone. Or upload the file again under"
                  + " the same name."
                : "\n\nDrag to move it (Alt or Ctrl: no snapping); drag an end to trim it; Delete removes it.")}
            style={{ left: held.at * pps, width }}
            onPointerDown={(event) => onClipPointerDown(event, held)}
            onClick={(event) => onClipClick(event, held)}
          >
            {pooledClip.length > 0 && (
              <svg
                className="os-tl-clip-wave"
                viewBox={`0 0 ${Math.max(1, pooledClip.length)} 100`}
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <path d={waveformPath(pooledClip, 100)} />
              </svg>
            )}
            {held.fade_in > 0 && (
              <span className="os-tl-clip-fade in" style={{ width: Math.min(width, held.fade_in * pps) }} aria-hidden="true" />
            )}
            {held.fade_out > 0 && (
              <span className="os-tl-clip-fade out" style={{ width: Math.min(width, held.fade_out * pps) }} aria-hidden="true" />
            )}
            <span className="os-tl-clip-name">{held.missing ? `${held.file} — missing` : held.file}</span>
            {/* The trim zones: the cursor only - the pointer-down
                bubbles to the clip, which asks `clipAt` which end
                it has. A missing clip has a body and no edges
                (E4c: its slice cannot change), so it gets no
                zones and no ew-resize cursor - `clipAt` answers
                "body" for it wherever it is pressed. */}
            {!held.missing && (
              <>
                <span className="os-tl-clip-edge in" aria-hidden="true" />
                <span className="os-tl-clip-edge out" aria-hidden="true" />
              </>
            )}
          </button>
        );
      })}
      {/* The dragged clip's new time, painted through its ref. */}
      <div className="os-tl-clip-label" ref={clipLabelRef} style={{ display: "none" }} aria-hidden="true" />
    </div>
  );
}
