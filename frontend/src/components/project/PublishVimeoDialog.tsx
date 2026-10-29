/**
 * Publish to Vimeo… (V2): the project page's dialog. The title (the project's name), the description (a walkthrough
 * project's transcript, cut to Vimeo's limit; empty otherwise), the privacy (the studio's default, changeable), then
 * **Publish**: `POST /api/projects/{pid}/vimeo/publish` starts the `vimeo_publish` job, which the panel follows.
 * Without a token the dialog says so and Publish is greyed out. Pure web; the rules are `lib/vimeo.ts`.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Field, Input, Modal, Select, Textarea } from "../ui";
import { NO_TOKEN_LINE, PLUS_LINE, type VimeoDefaults, canPublish, descriptionCounter, publishBody, publishUrl } from "../../lib/vimeo";

interface Props {
  open: boolean;
  projectId: string;
  defaults: VimeoDefaults;
  /** Whether a video was published before: the dialog says a NEW video is made and the old one kept. */
  again: boolean;
  onClose: () => void;
  onStarted: (jobId: string) => void;
}

export function PublishVimeoDialog(props: Props) {
  // Mounted only while open, so every opening starts from the defaults as they are then.
  return props.open ? <PublishVimeoBody {...props} /> : null;
}

function PublishVimeoBody({ projectId, defaults, again, onClose, onStarted }: Props) {
  const [title, setTitle] = useState(defaults.title);
  const [description, setDescription] = useState(defaults.description);
  const [privacy, setPrivacy] = useState(defaults.privacy);
  const privacies = defaults.privacies?.length ? defaults.privacies : [];
  const note = privacies.find((o) => o.value === privacy)?.note;
  const publish = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(publishUrl(projectId), publishBody(title, description, privacy, defaults)),
    onSuccess: (r) => {
      onStarted(r.job_id);
      onClose();
    },
  });
  const ready = canPublish(defaults, title) && !publish.isPending;

  return (
    <Modal
      open
      onClose={onClose}
      title={again ? "Publish to Vimeo again" : "Publish to Vimeo"}
      width={600}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Upload size={16} />} disabled={!ready} onClick={() => publish.mutate()} data-vimeo-publish-go="">
            {publish.isPending ? "Starting…" : "Publish"}
          </Button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>
        The rendered video is uploaded to Vimeo as a {again ? "new video - the one published before stays on Vimeo, its link may be in a course - " : "new video "}
        and the page shows its link once Vimeo has processed it. {PLUS_LINE}
      </p>
      {!defaults.token_set && <p className="os-muted os-small" style={{ marginTop: 0 }} data-vimeo-no-token="">{NO_TOKEN_LINE}</p>}
      <div style={{ display: "grid", gap: 12 }}>
        <Field label="Title" hint={`The video's name on Vimeo (at most ${defaults.name_max} characters).`}>
          <Input value={title} maxLength={defaults.name_max} autoFocus onChange={(e) => setTitle(e.target.value)} data-vimeo-title="" />
        </Field>
        <Field label="Description" hint={descriptionCounter(description, defaults.description_max)}>
          <Textarea value={description} rows={6} maxLength={defaults.description_max} onChange={(e) => setDescription(e.target.value)} data-vimeo-description="" />
        </Field>
        <Field label="Privacy" hint={note}>
          <Select value={privacy} onChange={(e) => setPrivacy(e.target.value)} data-vimeo-privacy="">
            {privacies.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>
        {publish.isError && <ErrorBox message={errorMessage(publish.error)} />}
      </div>
    </Modal>
  );
}
