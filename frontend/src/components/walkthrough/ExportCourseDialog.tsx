/**
 * Export to course… (W2c, C8): the builder's dialog. It previews the export on opening - the plan route, which
 * writes nothing - and shows it: the figures placed (each step's number, the guide's sentence it goes after, the
 * picture's file, the pin in percent, where), the steps not placed and why, the skipped ones, the blocks an
 * earlier export left that go, the guide's own steps that are not in the walkthrough, the files, the guide's
 * changes as a diff (collapsed), and the studio setting's state. **Write to the course** (only while the setting
 * "Allow export into the Courses folder" is on) writes it with the sha256 the preview showed - a guide changed
 * since is refused with the server's line, and **Preview again** reads it afresh; **Download zip** is the same
 * result to copy into the course by hand; **Download player** the interactive player on its own. A walkthrough
 * not imported from a workshop can only download its player. Pure web: the browser edition has all of it.
 *
 * W2e: a walkthrough of several workshops shows the combined line ("5 figures into 2 guides; 1 step not placed; 2
 * skipped"), the steps of no guide, and a block per guide with its own line, lists and diff (folded); the write sends
 * every guide's sha256 from the preview (`writeBody`). With one guide the dialog reads as before.
 *
 * B3 (the embedded player): with **Also the interactive player** ticked the player goes beside the first guide and the
 * guide gets the walkthrough FIGURE a Content Manager from 0.7.44 renders as the player inline; the dialog shows where
 * the figure goes (`playerWhere`), the figure itself, and the first sentence of its caption to edit - leaving the field
 * previews again with the caption, which then travels with the write and the zip (`planBody`, `writeBody`, `courseZipUrl`).
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileDown, RefreshCw, Upload } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Modal, Spinner } from "../ui";
import {
  type ExportPlan, type ExportResult, type GuidePlan, courseZipUrl, exportPlanQueryKey, exportPlanUrl, exportWriteUrl, filesLine, placedWhere,
  planBody, planLine, playerWhere, playerZipUrl, settingLine, stepsOfNoGuide, writeBody,
} from "../../lib/courseExport";

/** The caption's cap, the server's (`course_export.MAX_CAPTION_CHARS`). */
const MAX_CAPTION_CHARS = 200;

interface Props {
  open: boolean;
  wid: string;
  onClose: () => void;
}

export function ExportCourseDialog(props: Props) {
  // Mounted only while open, so every opening previews the guide as it is now.
  return props.open ? <ExportCourseBody {...props} /> : null;
}

function ExportCourseBody({ wid, onClose }: Props) {
  const [player, setPlayer] = useState(false);
  // B3: the caption's first sentence as typed (null until edited: the server's own - the guide's, else the name), and the field's draft.
  const [caption, setCaption] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [written, setWritten] = useState<ExportResult | null>(null);
  const plan = useQuery({
    queryKey: exportPlanQueryKey(wid, player, caption),
    queryFn: () => api.post<ExportPlan>(exportPlanUrl(wid), planBody(player, caption)),
    retry: false,
    gcTime: 0,
  });
  const write = useMutation({
    mutationFn: (p: ExportPlan) => api.post<ExportResult>(exportWriteUrl(wid), writeBody(p, player, caption)),
    onSuccess: (r) => {
      setWritten(r);
      void plan.refetch();
    },
  });
  const p = plan.data ?? null;
  const guides: GuidePlan[] = p?.guides ?? [];
  const several = guides.length > 1;
  const homeless = p ? stepsOfNoGuide(p) : [];
  const again = () => {
    write.reset();
    setWritten(null);
    void plan.refetch();
  };
  /** The caption field left (blur, Enter): a changed first sentence previews again with it. */
  const commitCaption = () => {
    if (draft === null) return;
    const typed = draft.trim();
    const current = caption ?? p?.player_caption ?? "";
    if (typed === current) return;
    setCaption(typed);
    setWritten(null);
    write.reset();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Export to course"
      width={760}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <a className="os-btn os-btn-secondary" href={playerZipUrl(wid)} download data-export-player="">
            <FileDown size={16} /> Download player
          </a>
          {p && (
            <a className={`os-btn ${p.allowed ? "os-btn-secondary" : "os-btn-primary"}`} href={courseZipUrl(wid, player, caption)} download data-export-zip="">
              <Download size={16} /> Download zip
            </a>
          )}
          {p?.allowed && (
            <Button variant="primary" icon={<Upload size={16} />} disabled={!p.changed || write.isPending || plan.isFetching} onClick={() => write.mutate(p)} data-export-write="">
              {write.isPending ? "Writing…" : "Write to the course"}
            </Button>
          )}
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        Each step with a picture Media Studio took becomes a figure right after its step in the workshop's guide - the content manager's annotated
        image, a numbered pin where the hotspot is - and its picture a file in the course's <code>_assets/images</code>. A step whose picture is the
        guide's own is already there. Nothing is written until you press Write to the course.
      </p>
      <p className="os-muted os-small" style={{ marginTop: 0 }} data-export-caveat="">
        The pins show in a Content Manager from 0.7.42 (a Content Editor from 1.30.0); an older one shows the picture, the pin's text as a numbered
        line and the caption. The interactive player needs a Content Manager from 0.7.44 to show the embedded player; an older one shows an empty
        frame and the caption. Export into a published course once its readers have that version.
      </p>
      <label className="os-row" style={{ gap: 8, alignItems: "center", marginBottom: 10 }}>
        <input type="checkbox" checked={player} onChange={(e) => { setPlayer(e.target.checked); setWritten(null); write.reset(); }} data-export-with-player="" />
        <span>Also the interactive player, in the workshop's <code>walkthrough</code> folder, embedded in the guide as a figure (in the Interactive walkthrough placeholder's place, else below the video, else after the first section heading)</span>
      </label>
      {player && p && (
        <div style={{ display: "grid", gap: 6, marginBottom: 10 }} data-export-player-block="">
          <label className="os-row" style={{ gap: 8, alignItems: "center" }}>
            <span style={{ whiteSpace: "nowrap" }}>Caption's first sentence</span>
            <input
              className="os-input"
              style={{ flex: 1 }}
              value={draft ?? caption ?? p.player_caption}
              maxLength={MAX_CAPTION_CHARS}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => { commitCaption(); setDraft(null); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commitCaption(); setDraft(null); } }}
              data-export-player-caption=""
            />
          </label>
          <span className="os-muted os-small" data-export-player-where="">{playerWhere(p.player_block)}</span>
          <details data-export-player-figure="">
            <summary>The figure the guide gets (the player in a frame, the caption under it)</summary>
            <pre className="ms-transcript" style={{ maxHeight: 200 }}>{p.player_lines.join("\n")}</pre>
          </details>
        </div>
      )}
      {plan.isLoading && <Spinner label="Reading the guide…" />}
      {plan.isError && <ErrorBox message={errorMessage(plan.error)} />}
      {p && (
        <div style={{ display: "grid", gap: 8 }} data-export-plan="">
          <div className="os-row" style={{ gap: 8 }}>
            <strong data-export-line="">{planLine(p)}</strong>
            <Button size="sm" icon={<RefreshCw size={14} />} onClick={again} disabled={plan.isFetching} data-export-again="">Preview again</Button>
          </div>
          <span className="os-muted os-small" data-export-setting="">{settingLine(p.allowed)}</span>
          {homeless.length > 0 && (
            <div style={{ display: "grid", gap: 6 }} data-export-homeless="">
              {homeless.some((s) => !s.skipped) && (
                <div>
                  <strong>Not placed</strong>
                  <ul className="ms-import-steps">{homeless.filter((s) => !s.skipped).map((s) => <li key={s.step_id}>Step {s.number}: {s.text || "(no text)"} - {s.reason}</li>)}</ul>
                </div>
              )}
              {homeless.some((s) => s.skipped) && (
                <div data-export-homeless-skipped="">
                  <strong>Skipped</strong>
                  <ul className="ms-import-steps">{homeless.filter((s) => s.skipped).map((s) => <li key={s.step_id}>Step {s.number}: {s.text || "(no text)"} - {s.reason}</li>)}</ul>
                </div>
              )}
            </div>
          )}
          {guides.map((g) => (
            <section key={g.slug} style={{ display: "grid", gap: 6 }} data-export-guide={g.slug}>
              {several && (
                <div className="os-row" style={{ gap: 8, alignItems: "baseline" }}>
                  <strong>{g.title}</strong>
                  <span className="os-muted os-small" data-export-guide-line="">{planLine(g)}</span>
                </div>
              )}
              {g.placed.length > 0 && (
                <ol className="ms-import-steps" data-export-placed="">
                  {g.placed.map((s) => (
                    <li key={s.step_id} title={s.image}>
                      <strong>Step {s.number}</strong>
                      <span className="os-truncate">{s.text}</span>
                      <span className="os-muted">{s.hotspot ? `pin at ${s.hotspot.x} %, ${s.hotspot.y} %` : "no hotspot: a plain figure"} · {placedWhere(s)}</span>
                    </li>
                  ))}
                </ol>
              )}
              {g.not_placed.length > 0 && (
                <div data-export-not-placed="">
                  <strong>Not placed</strong>
                  <ul className="ms-import-steps">{g.not_placed.map((s) => <li key={s.step_id}>Step {s.number}: {s.text || "(no text)"} - {s.reason}</li>)}</ul>
                </div>
              )}
              {g.skipped.length > 0 && (
                <div data-export-skipped="">
                  <strong>Skipped</strong>
                  <ul className="ms-import-steps">{g.skipped.map((s) => <li key={s.step_id}>Step {s.number}: {s.text || "(no text)"} - {s.reason}</li>)}</ul>
                </div>
              )}
              {g.removed.length > 0 && (
                <div data-export-removed="">
                  <strong>Taken out of the guide</strong>
                  <ul className="ms-import-steps">{g.removed.map((r) => <li key={`${r.step_id}-${r.line}`}>The figure at line {r.line} - {r.reason}</li>)}</ul>
                </div>
              )}
              {(g.kept ?? []).length > 0 && (
                <div data-export-kept="">
                  <strong>Kept</strong>
                  <ul className="ms-import-steps">{g.kept.map((k) => <li key={k.path}><code>{k.path}</code> - {k.reason}</li>)}</ul>
                </div>
              )}
              {g.new_in_guide.length > 0 && (
                <div data-export-new-in-guide="">
                  <strong>In the guide, not in the walkthrough</strong>
                  <ul className="ms-import-steps">{g.new_in_guide.map((n) => <li key={n.line}>Line {n.line}: {n.text}</li>)}</ul>
                </div>
              )}
              {g.diff && (
                <details data-export-diff="">
                  <summary>{several ? `The changes to ${g.guide}` : "The guide's changes"}</summary>
                  <pre className="ms-transcript" style={{ maxHeight: 280 }}>{g.diff}</pre>
                </details>
              )}
            </section>
          ))}
          {(p.debris ?? []).length > 0 && (
            <div data-export-debris="">
              <strong>Left over, swept by the write</strong>
              <ul className="ms-import-steps">{p.debris.map((d) => <li key={d.path}><code>{d.path}</code> - {d.reason}</li>)}</ul>
            </div>
          )}
          <details data-export-files="">
            <summary>{filesLine(p.files)}</summary>
            <ul className="ms-import-steps">{p.files.map((f) => <li key={f.path}><code>{f.path}</code> <span className="os-muted">{f.action}</span></li>)}</ul>
          </details>
        </div>
      )}
      {written && <p style={{ color: "var(--good)", fontWeight: 500 }} role="status" data-export-written="">{written.message}</p>}
      {write.isError && <ErrorBox message={errorMessage(write.error)} />}
    </Modal>
  );
}
