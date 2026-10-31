/**
 * Video to course… (R1): the builder's dialog - the one form (`VideoToCourseForm`) with everything prefilled from
 * `GET /api/walkthroughs/{wid}/video-to-course` (read when the dialog opens, so the where-it-goes line and the
 * run-again line are of the guide and the project as they are now). The one button starts the job and the dialog
 * closes; the builder follows the job under its toolbar (R4). Pure web; the rules are `lib/videoToCourse.ts`.
 */
import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Modal, Spinner } from "../ui";
import type { WalkthroughDocument } from "../../lib/walkthrough";
import { type VideoToCourseDefaults, type VideoToCourseMode, type VideoToCourseStarted, videoToCourseQueryKey, videoToCourseUrl } from "../../lib/videoToCourse";
import { VideoToCourseForm } from "./VideoToCourseForm";

interface Props {
  open: boolean;
  wid: string;
  doc: WalkthroughDocument;
  busy?: boolean;
  onClose: () => void;
  onStarted: (started: VideoToCourseStarted, mode: VideoToCourseMode) => void;
}

export function VideoToCourseDialog(props: Props) {
  // Mounted only while open, so every opening reads the defaults afresh.
  return props.open ? <VideoToCourseBody {...props} /> : null;
}

function VideoToCourseBody({ wid, doc, busy, onClose, onStarted }: Props) {
  const defaults = useQuery({ queryKey: videoToCourseQueryKey(wid), queryFn: () => api.get<VideoToCourseDefaults>(videoToCourseUrl(wid)), retry: false, staleTime: 0 });
  return (
    <Modal open onClose={onClose} title="Video to course" width={620} footer={<Button onClick={onClose}>Cancel</Button>}>
      <p style={{ marginTop: 0 }}>
        One job: the walkthrough is rendered into a narrated video (a project under Projects, as Make a video makes it), the video is published to Vimeo,
        and the Vimeo link is placed in the course workshop's guide by the course standard. The builder follows it under its toolbar; the project page
        shows the result on its Vimeo panel.
      </p>
      {defaults.isLoading && <Spinner label="Reading the guide…" />}
      {defaults.isError && <ErrorBox message={errorMessage(defaults.error)} />}
      {defaults.data && <VideoToCourseForm wid={wid} doc={doc} defaults={defaults.data} busy={busy} onStarted={(r, mode) => { onStarted(r, mode); onClose(); }} />}
    </Modal>
  );
}
