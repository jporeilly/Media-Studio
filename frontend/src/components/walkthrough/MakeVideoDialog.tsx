/**
 * Make a video… (W3): the builder's dialog. The project's name (the walkthrough's, editable), whether the
 * steps' Under-the-hood notes are narrated after their steps (ticked by default when any step has a note,
 * disabled with "No step has a note." otherwise), whether the picture zooms to each step's hotspot (ticked
 * by default, disabled when no step has one), the steps it will use ("12 steps, 2 without a picture, 1
 * without any text") and the projects already made from this walkthrough, each with when it was made and an
 * Open link. **Make the video** posts `POST /api/walkthroughs/{wid}/video`, which makes the project from the
 * walkthrough as it is SAVED and starts its generate job with the studio's defaults, and opens the project,
 * whose Generate card follows the job (the job's id travels in the route's state). The walkthrough itself is
 * never changed by it. Pure web: the browser edition has all of it; the rules are `lib/walkthrough.ts`.
 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Field, Input, Modal } from "../ui";
import {
  type MadeProject, type MadeVideo, type MakeVideoBody, type WalkthroughDocument, MAX_NAME_CHARS, NO_HOTSPOTS, NO_NOTES, hasHotspots,
  hasNotes, madeFrom, madeWhen, makeVideoUrl, projectUrl, stepsLine,
} from "../../lib/walkthrough";

interface Props {
  open: boolean;
  wid: string;
  /** The document as it is saved (the builder saves first): the name, and the steps the line and the boxes read. */
  doc: WalkthroughDocument;
  onClose: () => void;
}

export function MakeVideoDialog(props: Props) {
  // Mounted only while open, so every opening starts from the document as it is then.
  return props.open ? <MakeVideoBody {...props} /> : null;
}

function MakeVideoBody({ wid, doc, onClose }: Props) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canNotes = hasNotes(doc);
  const canZoom = hasHotspots(doc);
  const [name, setName] = useState(doc.name.trim());
  const [notes, setNotes] = useState(canNotes);
  const [zoom, setZoom] = useState(canZoom);

  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.get<{ projects: MadeProject[] }>("/api/projects") });
  const made = madeFrom(projects.data?.projects ?? [], wid);

  const make = useMutation({
    mutationFn: () => {
      const body: MakeVideoBody = { name: name.trim(), notes: canNotes && notes, zoom: canZoom && zoom };
      return api.post<MadeVideo>(makeVideoUrl(wid), body);
    },
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
      navigate(projectUrl(r.project_id), { state: { followJob: { id: r.job_id, kind: "generate" } } });
    },
  });

  return (
    <Modal
      open
      onClose={onClose}
      title="Make a video"
      width={560}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Clapperboard size={16} />} disabled={!name.trim() || make.isPending} onClick={() => make.mutate()} data-video-make="">
            {make.isPending ? "Making…" : "Make the video"}
          </Button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        Each step becomes a slide of a new project: its picture on the player's stage with the hotspot ringed, its instruction spoken in the
        studio's voice, the chapters as the video's chapters. The project renders at once with the studio's defaults; its page has the Generate
        card and the Slides card, where the narration can be edited and the video made again. The walkthrough itself is not changed.
      </p>
      <div style={{ display: "grid", gap: 12 }}>
        <Field label="Name" hint="The project's name">
          <Input value={name} maxLength={MAX_NAME_CHARS} autoFocus onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && name.trim() && !make.isPending) make.mutate(); }} data-video-name="" />
        </Field>
        <div style={{ display: "grid", gap: 4 }}>
          <label className="os-checkbox">
            <input type="checkbox" checked={canNotes && notes} disabled={!canNotes} onChange={(e) => setNotes(e.target.checked)} data-video-notes="" />
            Narrate the Under-the-hood notes too
          </label>
          {!canNotes && <span className="os-muted os-small" data-video-no-notes="">{NO_NOTES}</span>}
        </div>
        <div style={{ display: "grid", gap: 4 }}>
          <label className="os-checkbox">
            <input type="checkbox" checked={canZoom && zoom} disabled={!canZoom} onChange={(e) => setZoom(e.target.checked)} data-video-zoom="" />
            Zoom to the hotspot
          </label>
          {!canZoom && <span className="os-muted os-small" data-video-no-hotspots="">{NO_HOTSPOTS}</span>}
        </div>
        <p style={{ margin: 0 }} data-video-steps="">{stepsLine(doc)}</p>
        {made.length > 0 && (
          <div data-video-made="">
            <strong>Made before:</strong>
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {made.map((p) => (
                <li key={p.id} data-video-made-project={p.id}>
                  <em>{p.name}</em> ({madeWhen(p.created_at)}) - <Link to={projectUrl(p.id)}>Open</Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        {make.isError && <ErrorBox message={errorMessage(make.error)} />}
      </div>
    </Modal>
  );
}
