/**
 * The clip inspector's markup (R1c, with `useMusicLane`): the row under the
 * strip with the level and the two fades, each box committing on change - a
 * slider release, a blur or Enter - as the List view's do, one PUT of the
 * whole `music` list through `changeClip`. The element is the root's own,
 * moved whole - every class, title and handler as it was - and takes as
 * props exactly the values it reads, under the names the markup reads them
 * by; the root keeps the guard (`{inspected && …}`) and the derivation.
 */
import type { Dispatch, RefObject, SetStateAction } from "react";
import { Trash2 } from "lucide-react";
import { DEFAULT_MUSIC_GAIN, clipGain, clipLength, type LaneLocks, type MusicClip } from "../../../lib/edit";
import { timecode } from "../../../lib/format";
import { Input } from "../../ui";
import type { ClipDraft } from "./types";

export interface ClipInspectorProps {
  /** The selected clip, as the plan holds it. */
  inspected: MusicClip;
  /** The commit lock and the strip's locks (only `music` is read): the boxes are disabled under either. */
  editLocked: boolean;
  locks: Pick<LaneLocks, "music">;
  clipDraft: ClipDraft;
  setClipDraft: Dispatch<SetStateAction<ClipDraft>>;
  /** The studio's `music_volume`, for the level's tick and title. */
  studioGainRef: RefObject<number | undefined>;
  changeClip: (id: string, patch: Partial<MusicClip>) => void;
  removeClip: () => void;
}

export function ClipInspector({
  inspected, editLocked, locks, clipDraft, setClipDraft, studioGainRef, changeClip, removeClip,
}: ClipInspectorProps) {
  return (
    <div className="os-tl-inspector">
      <span className="os-tl-clip-file" title={inspected.file}>{inspected.file}</span>
      <span
        className="os-tl-status"
        title={inspected.missing
          ? "This file is no longer in the library, so the clip cannot be trimmed: its slice is of a length "
            + "nobody knows now. Move it, set its level and fades, remove it, or put the file back under the same name."
          : undefined}
      >
        at {timecode(inspected.at)} · {timecode(inspected.in)}–{timecode(inspected.out)} of the file
        {" "}· {clipLength(inspected).toFixed(2)} s
        {inspected.missing && " · the file is missing, so it cannot be trimmed"}
      </span>
      <label className="os-tl-inspector-field">
        Level
        <input
          type="range"
          className="os-tl-range os-tl-level"
          min={0}
          max={100}
          step={1}
          list="os-tl-gain-default"
          disabled={editLocked || locks.music}
          value={Math.round((clipDraft.gain ?? clipGain(inspected.gain)) * 100)}
          aria-label="Music level"
          title={`A linear level, as the render applies it. The studio's default is `
            + `${Math.round((studioGainRef.current ?? DEFAULT_MUSIC_GAIN) * 100)}%.`}
          onChange={(e) => setClipDraft((held) => ({ ...held, gain: Number(e.target.value) / 100 }))}
          onPointerUp={() => { if (clipDraft.gain !== undefined) changeClip(inspected.id, { gain: clipDraft.gain }); }}
          onKeyUp={(e) => {
            if (e.key !== "Enter" && !e.key.startsWith("Arrow") && e.key !== "Home" && e.key !== "End") return;
            if (clipDraft.gain !== undefined) changeClip(inspected.id, { gain: clipDraft.gain });
          }}
          onBlur={() => { if (clipDraft.gain !== undefined) changeClip(inspected.id, { gain: clipDraft.gain }); }}
        />
        <datalist id="os-tl-gain-default">
          <option value={Math.round((studioGainRef.current ?? DEFAULT_MUSIC_GAIN) * 100)} />
        </datalist>
        <span className="os-tl-inspector-value">{Math.round((clipDraft.gain ?? clipGain(inspected.gain)) * 100)}%</span>
      </label>
      <label className="os-tl-inspector-field">
        Fade in
        <Input
          type="number"
          min={0}
          step={0.5}
          style={{ width: 82 }}
          disabled={editLocked || locks.music}
          value={clipDraft.fadeIn ?? String(inspected.fade_in)}
          onChange={(e) => setClipDraft((held) => ({ ...held, fadeIn: e.target.value }))}
          onBlur={(e) => { const s = Number(e.target.value); if (Number.isFinite(s)) changeClip(inspected.id, { fade_in: Math.max(0, s) }); }}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
        />
        s
      </label>
      <label className="os-tl-inspector-field">
        Fade out
        <Input
          type="number"
          min={0}
          step={0.5}
          style={{ width: 82 }}
          disabled={editLocked || locks.music}
          value={clipDraft.fadeOut ?? String(inspected.fade_out)}
          onChange={(e) => setClipDraft((held) => ({ ...held, fadeOut: e.target.value }))}
          onBlur={(e) => { const s = Number(e.target.value); if (Number.isFinite(s)) changeClip(inspected.id, { fade_out: Math.max(0, s) }); }}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
        />
        s
      </label>
      <span className="os-tl-spacer" />
      <button
        type="button"
        className="os-tl-btn text"
        disabled={editLocked || locks.music}
        title="Remove this clip from the Music lane (Delete)"
        onClick={removeClip}
      >
        <Trash2 size={13} /> Remove clip
      </button>
    </div>
  );
}
