/**
 * Video to course (R1, R3): the one form - everything prefilled so the author presses the one button without typing.
 * The video's name (the walkthrough's), **Narrate the Under-the-hood notes too** and **Zoom to the hotspot** (Make a
 * video's two ticks, the same defaults), the Vimeo **privacy** (the studio's default), the course **caption** ("Watch:
 * <the walkthrough's name>"), **This is the course's Welcome Tour** (off), and three read-only lines: where the link
 * goes, the Vimeo token's state (no token: the button greyed) and the export setting's state. A walkthrough from no
 * course workshop gets the form without the course part. A walkthrough that already has a video on Vimeo (R3) gets
 * the run-again line, **Render, publish and place again** and - when the guide does not hold the link - **Just place
 * the existing video again**. The one button posts `POST /api/walkthroughs/{wid}/video-to-course` and hands the job
 * to the caller. The dialog (`VideoToCourseDialog.tsx`) wraps it; the Workshop video canvas lays it out inline. Pure
 * web; the rules are `lib/videoToCourse.ts`.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Clapperboard, Upload } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Field, Input, Select } from "../ui";
import { type WalkthroughDocument, MAX_NAME_CHARS, NO_HOTSPOTS, NO_NOTES, hasHotspots, hasNotes } from "../../lib/walkthrough";
import {
  NO_COURSE_LINE, PLACE_AGAIN_LABEL, WELCOME_LABEL, type VideoToCourseDefaults, type VideoToCourseMode, type VideoToCourseStarted, againLine,
  buttonLabel, canPlaceAgain, canStart, captionDefault, exportLine, isAgain, tokenLine, videoToCourseBody, videoToCourseUrl, whereLine,
} from "../../lib/videoToCourse";

interface Props {
  wid: string;
  /** The document as it is saved: the name, and the steps the two ticks read. */
  doc: WalkthroughDocument;
  defaults: VideoToCourseDefaults;
  /** Whether a chain job is in flight already: the buttons wait. */
  busy?: boolean;
  onStarted: (started: VideoToCourseStarted, mode: VideoToCourseMode) => void;
}

export function VideoToCourseForm({ wid, doc, defaults, busy = false, onStarted }: Props) {
  const canNotes = hasNotes(doc);
  const canZoom = hasHotspots(doc);
  const [name, setName] = useState(defaults.name.trim() || doc.name.trim());
  const [notes, setNotes] = useState(canNotes);
  const [zoom, setZoom] = useState(canZoom);
  const [privacy, setPrivacy] = useState(defaults.privacy);
  const [caption, setCaption] = useState(captionDefault(defaults.name || doc.name));
  const [welcome, setWelcome] = useState(false);
  const again = isAgain(defaults.project);
  const privacies = defaults.privacies?.length ? defaults.privacies : [];
  const note = privacies.find((o) => o.value === privacy)?.note;

  const start = useMutation({
    mutationFn: (mode: VideoToCourseMode) => api.post<VideoToCourseStarted>(videoToCourseUrl(wid), videoToCourseBody({ name, notes: canNotes && notes, zoom: canZoom && zoom, privacy, caption, welcome }, mode)),
    onSuccess: (r, mode) => onStarted(r, mode),
  });
  const ready = canStart(defaults, name, busy || start.isPending);

  return (
    <div style={{ display: "grid", gap: 12 }} data-vtc-form="">
      {again && defaults.project && <p style={{ margin: 0 }} data-vtc-again="">{againLine(defaults.project)}</p>}
      <Field label="Name" hint="The video's name: the project's and the Vimeo title">
        <Input value={name} maxLength={MAX_NAME_CHARS} onChange={(e) => setName(e.target.value)} data-vtc-name="" />
      </Field>
      <div style={{ display: "grid", gap: 4 }}>
        <label className="os-checkbox">
          <input type="checkbox" checked={canNotes && notes} disabled={!canNotes} onChange={(e) => setNotes(e.target.checked)} data-vtc-notes="" />
          Narrate the Under-the-hood notes too
        </label>
        {!canNotes && <span className="os-muted os-small">{NO_NOTES}</span>}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        <label className="os-checkbox">
          <input type="checkbox" checked={canZoom && zoom} disabled={!canZoom} onChange={(e) => setZoom(e.target.checked)} data-vtc-zoom="" />
          Zoom to the hotspot
        </label>
        {!canZoom && <span className="os-muted os-small">{NO_HOTSPOTS}</span>}
      </div>
      <Field label="Vimeo privacy" hint={note}>
        <Select value={privacy} onChange={(e) => setPrivacy(e.target.value)} data-vtc-privacy="">
          {privacies.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
      </Field>
      {defaults.course ? (
        <>
          <Field label="Caption" hint='Shown under the video in the guide as "Watch: …"'>
            <Input value={caption} maxLength={200} onChange={(e) => setCaption(e.target.value)} data-vtc-caption="" />
          </Field>
          <label className="os-checkbox">
            <input type="checkbox" checked={welcome} onChange={(e) => setWelcome(e.target.checked)} data-vtc-welcome="" />
            {WELCOME_LABEL} <span className="os-muted os-small">(course.json: welcome.video and welcome.caption)</span>
          </label>
          <p className="os-muted os-small" style={{ margin: 0 }} data-vtc-where="">{whereLine(defaults.course, defaults.place)}</p>
        </>
      ) : (
        <p className="os-muted os-small" style={{ margin: 0 }} data-vtc-no-course="">{NO_COURSE_LINE}</p>
      )}
      <p className="os-muted os-small" style={{ margin: 0 }} data-vtc-token="">{tokenLine(defaults.token_set)}</p>
      {defaults.course && <p className="os-muted os-small" style={{ margin: 0 }} data-vtc-export="">{exportLine(defaults.allowed)}</p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button variant="primary" icon={<Clapperboard size={16} />} disabled={!ready} onClick={() => start.mutate("full")} title={defaults.token_set ? undefined : tokenLine(false)} data-vtc-start="">
          {start.isPending && start.variables === "full" ? "Starting…" : buttonLabel(defaults.course, again)}
        </Button>
        {canPlaceAgain(defaults) && (
          <Button icon={<Upload size={16} />} disabled={busy || start.isPending} onClick={() => start.mutate("place")} data-vtc-place-again="">
            {start.isPending && start.variables === "place" ? "Starting…" : PLACE_AGAIN_LABEL}
          </Button>
        )}
      </div>
      {start.isError && <ErrorBox message={errorMessage(start.error)} />}
    </div>
  );
}
