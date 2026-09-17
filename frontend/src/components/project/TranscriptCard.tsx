import { type Dispatch, Fragment, type ReactNode, type SetStateAction, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ListOrdered, Pause, Play, Save, Wand2, Waves } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { timecode } from "../../lib/format";
import {
  MAX_OFFSET_SECONDS,
  MAX_SPEED,
  MIN_SPEED,
  draftKey,
  numberChange,
  previewUrl,
  voiceChange,
  words,
  type NumberField,
  type Segment,
  type SegmentOverride,
} from "../../lib/narration";
import { narrationPlanKey } from "../../lib/timeline";
import { Button, Card, ErrorBox, Input, Tabs, Textarea } from "../ui";
import { JobProgress, type Job } from "./JobProgress";
import { NarrationTimeline } from "./NarrationTimeline";

interface Voice {
  voice_id: string;
  name: string;
}

interface Props {
  projectId: string;
  /** The editable copy of the transcript; owned by the page, because the
   *  Re-voice card below only appears once there is one. */
  segments: Segment[] | null;
  setSegments: Dispatch<SetStateAction<Segment[] | null>>;
  /** The page's one polled job — a transcribe shows its progress here. */
  job?: Job;
  jobActive: boolean;
  transcribing: boolean;
  transcribeError: string | null | undefined;
  transcribePending: boolean;
  onTranscribe: () => void;
  otherJobNotice: string | null;
  /** The Re-voice card's selection: what a Play, or the timeline, auditions as. */
  provider: string;
  voiceId: string;
  speed: number;
  voiceOptions: Voice[];
  studioDefaultVoice: string;
}

/**
 * ONE list of voices for every transcript row, referenced by each row's voice
 * box with `list=`. A `<select>` per row would put the provider's whole voice
 * list (Edge ships north of 300) into the DOM once per sentence — 30,000 option
 * elements on a ten-minute video. A `<datalist>` is the shape HTML already has
 * for "one shared list, many inputs", and it filters as you type, which a
 * 300-item dropdown badly needs.
 */
const VOICE_LIST_ID = "ms-sentence-voices";

// A very small labelled control for the transcript rows: four of them share the
// width of one ordinary Field, so the label is a line of 11px muted text and the
// control carries its own aria-label naming the sentence it belongs to.
function RowField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
      <span className="os-muted" style={{ fontSize: 11 }}>{label}</span>
      {children}
    </div>
  );
}

/**
 * "Transcript": the per-sentence narration editor of a video project, in two
 * views over the SAME `segments` state.
 *
 * - **List** — one textarea per sentence with its Offset, Speed, Voice, Mute and
 *   Play. This is where a Whisper mis-hearing gets fixed, and where every
 *   adjustment is typed; the timeline does not replace it.
 * - **Timeline** — the filmstrip, the waveform and one block per sentence on one
 *   time scale, with a transport that plays the new narration against the
 *   picture without rendering anything (`NarrationTimeline`).
 *
 * Extracted from `ProjectDetail.tsx` in phase 3a, following `SlidesCard`: the
 * page was 1059 lines and the timeline is not a thing to add to that.
 */
export function TranscriptCard({
  projectId, segments, setSegments, job, jobActive, transcribing, transcribeError,
  transcribePending, onTranscribe, otherJobNotice, provider, voiceId, speed,
  voiceOptions, studioDefaultVoice,
}: Props) {
  const qc = useQueryClient();
  // The Timeline is the view a video opens on. It is the visual reference -
  // the filmstrip, the waveform and every sentence in its place - and the
  // owner's first reaction to opening on the List (a column of offset boxes
  // with no picture to judge them against) was that the feature did not exist.
  // The List is one click away for fixing words. Safe as a default: the card
  // returns before the tabs exist while there is no transcript to show.
  const [view, setView] = useState("timeline");
  const [selected, setSelected] = useState<number | null>(null);
  // What is being TYPED into a per-sentence box (offset, speed or voice), keyed
  // by field and row, until it is committed on blur or Enter. Held as the raw
  // text so the box shows exactly what was typed while it is being typed - a
  // number-parsed round trip rewrites "0.40" to "0.4" and "-0." to "0"
  // mid-keystroke - and so nothing is sent until the user has finished. The
  // committed value is the parsed one.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // One <audio> element serves every row: pressing Play on another sentence
  // switches its source, which is also how only one sentence plays at a time.
  // The clip is FETCHED and played from a blob rather than pointed at with a
  // src, so a refusal comes back as an ApiError carrying the server's own
  // message - a media element is told only that its source failed - and so a
  // press while a request is in flight can be ignored instead of starting a
  // second synthesis of the same sentence.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const clipUrl = useRef<string | null>(null);
  const [playingRow, setPlayingRow] = useState<number | null>(null);
  const [pendingRow, setPendingRow] = useState<number | null>(null);
  const [playError, setPlayError] = useState<{ row: number; message: string } | null>(null);
  // The blob URL is this card's to release.
  useEffect(() => () => { if (clipUrl.current) URL.revokeObjectURL(clipUrl.current); }, []);

  // The provider's voice ids, built once and shared by every transcript row:
  // a row's voice box saves at once when what is in it came from picking off
  // the shared list, and waits for a blur when it was typed by hand.
  const voiceIds = useMemo(() => new Set(voiceOptions.map((v) => v.voice_id)), [voiceOptions]);

  /** Drop one row's draft, so the box shows what is saved again. Declared
   *  before the mutation that calls it on settle. */
  const clearDraft = (key: string) => setDrafts((current) => {
    const next = { ...current };
    delete next[key];
    return next;
  });

  // The answer carries ``timing_adjustments_dropped``: how many sentences lost
  // their adjustment because the saved list no longer holds the sentence it
  // belonged to. Normally 0 - a Save posts the sentences back as they were
  // given - and shown when it is not, rather than lost quietly.
  const save = useMutation({
    mutationFn: (segs: Segment[]) =>
      api.patch<{ timing_adjustments_dropped?: number }>(
        `/api/projects/${projectId}/transcript`, { transcript: words(segs) },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      // The words decide what is synthesised, so the audition's clips are stale.
      qc.invalidateQueries({ queryKey: narrationPlanKey(projectId) });
    },
  });

  // One sentence's adjustment, one request. Committed on blur / on tick, never
  // per keystroke: the outer project.json is a read-modify-write and a request
  // per character would interleave with itself.
  //
  // Optimistic, and put BACK when the server refuses: a 409 (a job holds the
  // project) or a 400 (a voice from the other provider) would otherwise leave
  // the page showing a value that was never saved, beside an error box saying
  // it was not. The snapshot is the list as this render has it, which is what
  // the event handler that fires the mutation is looking at.
  //
  // On success the saved sentence is folded into the editable copy rather than
  // the whole project being refetched: a refetch would replace the entire list
  // and throw away words the user has typed but not yet saved.
  //
  // A committed box keeps its draft until the request SETTLES (`draft` names
  // the key, dropped in onSettled). Clearing it at the moment the mutation is
  // fired left a render where the draft was gone and the optimistic value had
  // not arrived, so a box flicked back to its old value and then forward again.
  const adjust = useMutation({
    mutationFn: ({ index, changes }: { index: number; changes: SegmentOverride; draft?: string }) =>
      api.patch<Segment>(`/api/projects/${projectId}/transcript/${index}`, changes),
    onMutate: ({ index, changes }) => {
      const previous = segments;
      setSegments((prev) => prev && prev.map((seg, j) => (j === index ? { ...seg, ...changes } : seg)));
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) setSegments(context.previous);
    },
    onSuccess: (saved, { index }) => {
      setSegments((prev) => prev && prev.map((seg, j) => (j === index ? { ...saved, text: seg.text } : seg)));
      // Where a sentence is pinned, how fast it is spoken and in whose voice are
      // all the plan's answers: a saved adjustment makes the audition's clips
      // and its blocks out of date.
      qc.invalidateQueries({ queryKey: narrationPlanKey(projectId) });
    },
    onSettled: (_saved, _error, { draft }) => draft && clearDraft(draft),
  });

  /** Send a number box's typed value, if it changed anything (lib/narration.ts
   *  owns the rule - the two fields read an empty box differently). */
  const commitNumber = (field: NumberField, index: number, seg: Segment) => {
    const key = draftKey(field, index);
    const typed = drafts[key];
    if (typed === undefined) return;
    const change = numberChange(field, typed, seg[field]);
    // Nothing to send ("0.40" over a saved 0.4, or an unreadable box): drop the
    // draft so the box shows the saved value as the server spells it.
    if (!change) return clearDraft(key);
    adjust.mutate({
      index,
      draft: key,
      changes: field === "offset" ? { offset: change.value } : { speed: change.value },
    });
  };

  /** Send a voice box's value, if it changed anything. The provider rides with
   *  it (as the slide editor's voice override does): which provider a stored
   *  voice belongs to is what lets a re-voice under the other one fall back
   *  cleanly instead of failing. ``typedNow`` is passed when the value came
   *  from picking off the shared list rather than from typing. */
  const commitVoice = (index: number, seg: Segment, typedNow?: string) => {
    const key = draftKey("voice", index);
    const typed = typedNow ?? drafts[key];
    if (typed === undefined) return;
    const change = voiceChange(typed, seg.voice);
    if (!change) return clearDraft(key);
    adjust.mutate({ index, draft: key, changes: { ...change, provider } });
  };

  /** Hear one sentence. Fetched with the session cookie (the same credentials
   *  the video player rides on) and played from a blob, so a 400/409/502 comes
   *  back with the server's own words instead of the element's bare "the source
   *  failed". The Re-voice card's provider, voice and speed go with the request;
   *  the sentence's own voice and speed still win, server-side, as they will at
   *  render time.
   *
   *  One request at a time: a second press while one is in flight is ignored
   *  rather than starting a second synthesis of the same sentence. */
  const playSentence = (index: number) => {
    const el = audioRef.current;
    if (!el || pendingRow !== null) return;
    // The player itself is hidden, so this button is also the only way to stop
    // it: pressing it on the sentence that is already playing pauses it.
    if (playingRow === index && !el.paused) {
      el.pause();
      setPlayingRow(null);
      return;
    }
    setPlayError(null);
    setPendingRow(index);
    api.blob(previewUrl(projectId, index, { provider, voice: voiceId, speed }))
      .then((clip) => {
        if (clipUrl.current) URL.revokeObjectURL(clipUrl.current);
        clipUrl.current = URL.createObjectURL(clip);
        el.src = clipUrl.current;
        setPlayingRow(index);
        // Autoplay can still be refused (a tab that has never been interacted
        // with); onError covers a clip the browser cannot decode.
        void el.play().catch(() => undefined);
      })
      .catch((err) => {
        setPlayError({ row: index, message: errorMessage(err) });
        setPlayingRow(null);
      })
      .finally(() => setPendingRow(null));
  };

  // A refused adjustment (a 409 while a job holds the project, a 400 for a
  // voice from the other provider) has to be visible: nothing was saved, and
  // the row has already been put back to what the server holds.
  const adjustError = adjust.isError ? errorMessage(adjust.error) : null;
  // And a refused Save: a 422 from the schema or a 409 while a job runs used to
  // leave the button going quiet with nothing written.
  const saveError = save.isError ? errorMessage(save.error) : null;
  const adjustmentsDropped = save.data?.timing_adjustments_dropped ?? 0;

  if (transcribing) {
    return (
      <Card title="Transcript" style={{ marginTop: 16 }}>
        {transcribeError && <ErrorBox message={transcribeError} />}
        <JobProgress job={job} />
      </Card>
    );
  }

  if (!segments || segments.length === 0) {
    return (
      <Card title="Transcript" style={{ marginTop: 16 }}>
        {transcribeError && <ErrorBox message={transcribeError} />}
        <div style={{ display: "grid", gap: 12, justifyItems: "start" }}>
          <div style={{ color: "var(--muted)" }}>No transcript yet. Transcribe the audio to get an editable transcript.</div>
          {otherJobNotice && <div className="os-muted os-small">{otherJobNotice}</div>}
          <Button variant="primary" icon={<Wand2 size={16} />} disabled={transcribePending || jobActive} onClick={onTranscribe}>
            Transcribe audio
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card title="Transcript" style={{ marginTop: 16 }}>
      {transcribeError && <ErrorBox message={transcribeError} />}

      {/* Two views over the SAME sentences. The list is where they are edited;
          the timeline is where the result is seen and heard. */}
      <Tabs
        tabs={[
          { key: "list", label: <><ListOrdered size={14} /> List</>, count: segments.length },
          { key: "timeline", label: <><Waves size={14} /> Timeline</> },
        ]}
        active={view}
        onChange={setView}
      />

      {/* A refused adjustment (a 409 while a job holds the project, a 400 for a
          voice from the other provider) belongs to whichever view is open:
          nothing was saved, and the row has already been put back. */}
      {adjustError && <div style={{ marginTop: 12 }}><ErrorBox message={adjustError} /></div>}

      {/* BOTH views stay mounted, and the closed one is hidden rather than
          unmounted. The timeline holds the decoded clips of the whole narration,
          and the loop this feature exists to break is: nudge a sentence in the
          list, come back here, listen. Throwing the audition away on every tab
          switch would put a fetch of every sentence back into that loop —
          while an offset changes only where a clip LANDS, not what it says, so
          the clips it already has are still the right ones. The list keeps its
          half-typed boxes for the same reason. */}
      <div style={{ marginTop: 12, display: view === "timeline" ? "block" : "none" }}>
        <NarrationTimeline
          projectId={projectId}
          provider={provider}
          voiceId={voiceId}
          speed={speed}
          active={view === "timeline"}
          selected={selected}
          onSelect={setSelected}
          jobActive={jobActive}
        />
      </div>

      <div style={{ display: view === "list" ? "grid" : "none", gap: 10, marginTop: 12 }}>
        {saveError && <ErrorBox message={saveError} />}
        {/* What the right-hand controls do, and - as honestly as it can be
            put - what an offset can and cannot promise. Each sentence is
            pinned to the moment it was spoken and the leftover time becomes
            silence; a pin is a floor, not a position, so a sentence can
            always be pushed later but can only be pulled earlier as far as
            the previous one's new audio actually ends. */}
        <div className="os-muted os-small">
          Nudge a sentence with <strong>Offset</strong> (seconds: positive is later, negative
          earlier) or leave it out with <strong>Mute</strong>. Each change saves on its own, at
          once. A sentence can always be pushed later; pulling it earlier only moves it as far
          as the sentence before it finishes speaking. Editing the words never moves the
          sentences — Save below keeps every adjustment. The <strong>Timeline</strong> tab plays
          the result back against the picture.
        </div>
        {/* The two phase-2 controls, and what a preview costs. Said here
            rather than left to be discovered: the first press is a round
            trip to the voice service and the button says so while it
            waits, instead of looking broken. */}
        <div className="os-muted os-small">
          Leave <strong>Voice</strong> and <strong>Speed</strong> empty to use the re-voice's
          own, below. A speed set here is spoken at exactly that rate — nothing speeds the
          sentence up to fit the gap after it. <strong>Play</strong> speaks the sentence as the
          re-voice will: the first press waits on the voice service — a second or two, and
          longer for the first one after the server starts — while every press after that is
          instant, because the re-voice reuses the very same audio.
        </div>
        {/* ONE voice list for every row (see VOICE_LIST_ID). */}
        <datalist id={VOICE_LIST_ID}>
          {voiceOptions.map((v) => (
            <option key={v.voice_id} value={v.voice_id}>{v.name}</option>
          ))}
        </datalist>
        {/* ONE player for every row: pressing Play on another sentence
            re-points it, so a second sentence cannot talk over the first.
            Its source is a blob that has already been fetched, so the only
            failure left here is audio the browser cannot decode - every
            refusal the server writes is caught by the fetch instead. */}
        <audio
          ref={audioRef}
          hidden
          onEnded={() => setPlayingRow(null)}
          onError={() => {
            if (playingRow !== null) {
              setPlayError({ row: playingRow, message: "That clip could not be played — the audio came back damaged." });
            }
            setPlayingRow(null);
          }}
        />
        {segments.map((s, i) => (
          <Fragment key={i}>
            <div
              style={{ display: "grid", gridTemplateColumns: "84px minmax(180px, 1fr) 236px", gap: 10, alignItems: "start" }}
              onFocus={() => setSelected(i)}
            >
              {/* Where it was SPOKEN, which is what this column has always
                  meant, and - when it has been nudged - where it is now
                  aimed. "Aimed at" in the text itself, not only in a
                  tooltip: the pin is a floor, so a sentence pulled earlier
                  lands there only if the one before it has finished
                  speaking, and a bare arrow reads as a promise. */}
              <div style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums", fontSize: 13, paddingTop: 8 }}>
                {timecode(s.start)}
                {!!s.offset && (
                  <div style={{ fontSize: 12 }}>
                    aimed at {timecode(Math.max(0, s.start + s.offset))}
                  </div>
                )}
              </div>
              <Textarea
                value={s.text}
                rows={2}
                style={s.muted ? { opacity: 0.55 } : undefined}
                onChange={(e) => setSegments(segments.map((seg, j) => (j === i ? { ...seg, text: e.target.value } : seg)))}
              />
              <div style={{ display: "grid", gap: 6 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <RowField label="Offset (s)">
                    <Input
                      type="number"
                      step={0.05}
                      min={-MAX_OFFSET_SECONDS}
                      max={MAX_OFFSET_SECONDS}
                      aria-label={`Offset for sentence ${i + 1}, in seconds`}
                      title="Seconds to move this sentence by. 0 leaves it where it was spoken."
                      disabled={jobActive}
                      value={drafts[draftKey("offset", i)] ?? String(s.offset ?? 0)}
                      onChange={(e) => setDrafts({ ...drafts, [draftKey("offset", i)]: e.target.value })}
                      onBlur={() => commitNumber("offset", i, s)}
                      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                      style={{ width: "100%" }}
                    />
                  </RowField>
                  {/* Empty is NOT 1.0: empty means the re-voice's own speed,
                      which the render may still raise a little to fit the
                      gap after the sentence, while a number typed here is
                      spoken at exactly that rate. Hence the placeholder
                      rather than a prefilled 1. */}
                  <RowField label="Speed">
                    <Input
                      type="number"
                      step={0.05}
                      min={MIN_SPEED}
                      max={MAX_SPEED}
                      placeholder="auto"
                      aria-label={`Speed for sentence ${i + 1}`}
                      title="Speak this sentence at exactly this rate. Empty uses the re-voice's speed, which may be raised slightly to fit."
                      disabled={jobActive}
                      value={drafts[draftKey("speed", i)] ?? (s.speed ?? "")}
                      onChange={(e) => setDrafts({ ...drafts, [draftKey("speed", i)]: e.target.value })}
                      onBlur={() => commitNumber("speed", i, s)}
                      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                      style={{ width: "100%" }}
                    />
                  </RowField>
                </div>
                <RowField label="Voice">
                  <Input
                    list={VOICE_LIST_ID}
                    placeholder={voiceId || studioDefaultVoice || "the re-voice's voice"}
                    aria-label={`Voice for sentence ${i + 1}`}
                    title="A voice for this sentence only — pick from the list or type a voice id. Empty uses the re-voice's voice."
                    disabled={jobActive}
                    value={drafts[draftKey("voice", i)] ?? s.voice ?? ""}
                    onChange={(e) => {
                      const typed = e.target.value;
                      setDrafts({ ...drafts, [draftKey("voice", i)]: typed });
                      // Picked off the shared list: that is a choice, not
                      // typing, so it saves at once like the Mute tick
                      // rather than waiting for a blur that may never come
                      // before Play is pressed.
                      if (voiceIds.has(typed)) commitVoice(i, s, typed);
                    }}
                    onBlur={() => commitVoice(i, s)}
                    onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                    style={{ width: "100%" }}
                  />
                </RowField>
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                  <label className="os-checkbox os-small" title="Leave this sentence out of the new narration.">
                    <input
                      type="checkbox"
                      checked={!!s.muted}
                      disabled={jobActive}
                      onChange={(e) => adjust.mutate({ index: i, changes: { muted: e.target.checked } })}
                    />
                    Mute
                  </label>
                  {/* Enabled even while a job holds the project: it writes
                      nothing, and hearing a sentence is a read. */}
                  <Button
                    size="sm"
                    icon={playingRow === i ? <Pause size={14} /> : <Play size={14} />}
                    title="Hear this sentence, spoken as the re-voice will speak it."
                    disabled={pendingRow !== null && pendingRow !== i}
                    onClick={() => playSentence(i)}
                  >
                    {pendingRow === i ? "Speaking…" : playingRow === i ? "Stop" : "Play"}
                  </Button>
                </div>
              </div>
            </div>
            {/* Under the row it belongs to: on a long transcript a single
                box at the top of the card is nowhere near the sentence. */}
            {playError?.row === i && <ErrorBox message={playError.message} />}
          </Fragment>
        ))}
        {adjustmentsDropped > 0 && (
          <div className="os-muted os-small">
            {adjustmentsDropped} sentence{adjustmentsDropped === 1 ? "" : "s"} no longer in the
            saved transcript lost {adjustmentsDropped === 1 ? "its" : "their"} adjustments —
            offset, mute, voice and speed.
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button variant="primary" icon={<Save size={16} />} disabled={save.isPending} onClick={() => segments && save.mutate(segments)}>
            {save.isPending ? "Saving…" : "Save transcript"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
