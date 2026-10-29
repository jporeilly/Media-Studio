/**
 * Place in the course… (V3): the Vimeo panel's dialog for a walkthrough project made from a course workshop. It
 * previews on opening - the plan route, which writes nothing - and shows where the link goes (the course standard's
 * Walkthrough placeholder becomes the video figure; a figure of ours has its link replaced; with neither, the figure
 * is inserted after the guide's first section heading when ticked, or copied to paste by hand), the caption ("Watch:
 * …", the walkthrough's name by default, editable), the Welcome Tour tick (course.json), the guide's changes as a
 * diff, the files, and the studio setting's state. **Place in the course** (only while "Allow export into the
 * Courses folder" is on) writes with every file's sha256 the preview showed - a file changed since is refused with
 * the server's line, and **Preview again** reads it afresh. Pure web; the rules are `lib/vimeo.ts`.
 */
import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { Copy, RefreshCw, Upload } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Field, Input, Modal, Spinner } from "../ui";
import {
  INSERT_LABEL, PLACED_NEXT, WELCOME_LABEL, type PlacePlan, type PlaceResult, type VimeoCourse, canPlace, pasteText, placeFilesLine, placeLine,
  placePlanQueryKey, placePlanUrl, placeSettingLine, placeUrl, placeWriteBody,
} from "../../lib/vimeo";

interface Props {
  open: boolean;
  projectId: string;
  course: VimeoCourse;
  onClose: () => void;
}

export function PlaceInCourseDialog(props: Props) {
  // Mounted only while open, so every opening previews the guide as it is now.
  return props.open ? <PlaceInCourseBody {...props} /> : null;
}

function copyText(text: string): void {
  void navigator.clipboard?.writeText(text);
}

function PlaceInCourseBody({ projectId, course, onClose }: Props) {
  // The caption starts as the plan's default (the figure's own, else the walkthrough's name) once the first plan answers.
  const [caption, setCaption] = useState<string | null>(null);
  const [welcome, setWelcome] = useState(false);
  const [insert, setInsert] = useState(false);
  const [written, setWritten] = useState<PlaceResult | null>(null);
  const [copied, setCopied] = useState(false);
  const body = { caption: caption ?? "", welcome, insert };
  const plan = useQuery({
    queryKey: placePlanQueryKey(projectId, body),
    queryFn: () => api.post<PlacePlan>(placePlanUrl(projectId), body),
    retry: false,
    gcTime: 0,
    // The last plan stays shown while the next one (a changed caption or tick) is read.
    placeholderData: keepPreviousData,
  });
  const p = plan.data ?? null;
  useEffect(() => {
    if (p && caption === null) setCaption(p.caption);
  }, [p, caption]);
  const write = useMutation({
    mutationFn: (current: PlacePlan) => api.post<PlaceResult>(placeUrl(projectId), placeWriteBody(current, body)),
    onSuccess: (r) => {
      setWritten(r);
      void plan.refetch();
    },
  });
  const again = () => {
    write.reset();
    setWritten(null);
    void plan.refetch();
  };
  const lines = p ? pasteText(p.lines) : "";
  const showLines = !!p && (p.how === "none" || !p.allowed);

  return (
    <Modal
      open
      onClose={onClose}
      title="Place in the course"
      width={760}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          {p && (
            <Button icon={<Copy size={16} />} onClick={() => { copyText(lines); setCopied(true); }} data-place-copy="">
              {copied ? "Copied" : "Copy the lines"}
            </Button>
          )}
          {p?.allowed && (
            <Button variant="primary" icon={<Upload size={16} />} disabled={!canPlace(p) || write.isPending || plan.isFetching} onClick={() => write.mutate(p)} data-place-write="">
              {write.isPending ? "Writing…" : "Place in the course"}
            </Button>
          )}
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        The Vimeo link goes into the workshop <strong>{course.slug}</strong> of <strong>{course.title}</strong> by the course standard: the
        Walkthrough placeholder becomes the video figure (the link on the line above the caption), the workshop's manifest says it has a video,
        and the Content Editor publishes the guide as usual. Nothing is written until you press Place in the course.
      </p>
      {plan.isLoading && <Spinner label="Reading the guide…" />}
      {plan.isError && <ErrorBox message={errorMessage(plan.error)} />}
      {p && (
        <div style={{ display: "grid", gap: 10 }} data-place-plan="">
          <div className="os-row" style={{ gap: 8 }}>
            <strong data-place-line="">{placeLine(p)}</strong>
            <Button size="sm" icon={<RefreshCw size={14} />} onClick={again} disabled={plan.isFetching} data-place-again="">Preview again</Button>
          </div>
          <span className="os-muted os-small" data-place-setting="">{placeSettingLine(p.allowed)}</span>
          <Field label="Caption" hint={`Shown under the video as "Watch: …"${p.existing_caption ? ` (the guide's figure says "${p.existing_caption}")` : ""}.`}>
            <Input value={caption ?? p.caption} maxLength={200} onChange={(e) => setCaption(e.target.value)} data-place-caption="" />
          </Field>
          <label className="os-checkbox">
            <input type="checkbox" checked={welcome} onChange={(e) => { setWelcome(e.target.checked); setWritten(null); write.reset(); }} data-place-welcome="" />
            {WELCOME_LABEL} <span className="os-muted os-small">(course.json: welcome.video and welcome.caption)</span>
          </label>
          {(p.how === "none" || p.how === "insert") && (
            <label className="os-checkbox">
              <input type="checkbox" checked={insert} onChange={(e) => { setInsert(e.target.checked); setWritten(null); write.reset(); }} data-place-insert="" />
              {INSERT_LABEL}{p.heading ? ` (${p.heading})` : ""}
            </label>
          )}
          {showLines && (
            <div style={{ display: "grid", gap: 4 }} data-place-lines="">
              <span className="os-muted os-small">
                {p.how === "none" ? "Or copy these lines into the guide where the video belongs:" : "The lines to paste into the guide by hand:"}
              </span>
              <pre className="ms-transcript" style={{ maxHeight: 200 }}>{lines}</pre>
            </div>
          )}
          {p.diff && (
            <details data-place-diff="" open={p.how !== "none"}>
              <summary>The guide's changes</summary>
              <pre className="ms-transcript" style={{ maxHeight: 280 }}>{p.diff}</pre>
            </details>
          )}
          <details data-place-files="">
            <summary>{placeFilesLine(p.files)}</summary>
            <ul className="ms-import-steps">{p.files.map((f) => <li key={f.path}><code>{f.path}</code> <span className="os-muted">{f.action}</span></li>)}</ul>
          </details>
        </div>
      )}
      {written && (
        <p style={{ color: "var(--good)", fontWeight: 500 }} role="status" data-place-written="">
          {written.message.includes(PLACED_NEXT) ? written.message : `${written.message} ${PLACED_NEXT}`}
        </p>
      )}
      {write.isError && <ErrorBox message={errorMessage(write.error)} />}
    </Modal>
  );
}
