import { Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ChevronRight, Download, Eye, FileText, Film, Mic, Pause, Play, Presentation, Save, Wand2 } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { JobProgress, type Job } from "../components/project/JobProgress";
import { SlidesCard } from "../components/project/SlidesCard";
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { duration, relativeTime, timecode } from "../lib/format";
import { numberChange, previewUrl, voiceChange, type NumberField } from "../lib/narration";
import { slidesQueryKey } from "../lib/slides";
import {
  CARD_DURATION,
  PREVIEW_SECONDS,
  SUBTITLE_OPTIONS,
  downloadLinks,
  generateBody,
  optionProblems,
  optionsFromStudio,
  optionsSummary,
  type GenerateOptions,
  type Outputs,
  type SubtitleMode,
} from "../lib/generateOptions";
import { defaultVoiceFor, pickVoice, useStudioSettings, useVoices, type Option } from "../lib/studioSettings";

interface Segment {
  start: number;
  end: number;
  text: string;
  // Per-sentence narration adjustments. Absent means the default, which is
  // exactly how every project made before they existed behaves.
  offset?: number | null;
  muted?: boolean | null;
  voice?: string | null;
  provider?: string | null;
  speed?: number | null;
}
// What one sentence's PATCH may carry. A field left out is left alone; an
// explicit null clears it. Typed field by field so it can be merged straight
// into a Segment for the optimistic update.
interface SegmentOverride {
  offset?: number | null;
  muted?: boolean | null;
  voice?: string | null;
  provider?: string | null;
  speed?: number | null;
}

// The whole-list Save is a TEXT editor: the server forbids the adjustment keys
// in its body (they would otherwise have been dropped silently and written back
// stripped), and carries them across by index itself. Editing the words never
// moves the sentences.
const words = (segs: Segment[]) => segs.map((s) => ({ start: s.start, end: s.end, text: s.text }));
interface Project {
  id: string;
  name: string;
  kind: "deck" | "pdf" | "video";
  source_filename: string;
  size_bytes: number;
  slide_count: number | null;
  created_at: string;
  transcript?: Segment[];
  language?: string;
  duration?: number;
  transcribed_device?: string;
  output_video?: string;
  outputs?: Outputs;
  rendered_at?: string;
  revoiced_video?: string;
  revoiced_language?: string;
  narration_audio?: string;
  revoice_failed_sentences?: number;
}
interface Lang {
  name: string;
  subtag: string;
}
interface Preset {
  id: string;
  label: string;
  resolution: [number, number];
  video_bitrate: string;
  description: string;
}

const KIND_ICON = { deck: Presentation, pdf: FileText, video: Film } as const;

// Matches services/narration.py MAX_OFFSET_SECONDS: far beyond any real
// correction, and it stops a typo pinning a sentence into the next hour.
const MAX_OFFSET_SECONDS = 300;
// ... and its MIN_SPEED / MAX_SPEED, the same bounds the Generate and Re-voice
// speed boxes use.
const MIN_SPEED = 0.5;
const MAX_SPEED = 2;

/**
 * ONE list of voices for every transcript row, referenced by each row's voice
 * box with `list=`. A `<select>` per row would put the provider's whole voice
 * list (Edge ships north of 300) into the DOM once per sentence — 30,000 option
 * elements on a ten-minute video. A `<datalist>` is the shape HTML already has
 * for "one shared list, many inputs", and it filters as you type, which a
 * 300-item dropdown badly needs.
 */
const VOICE_LIST_ID = "ms-sentence-voices";

/** The draft key for one row's box: drafts are per field AND per row. */
const draftKey = (field: "offset" | "speed" | "voice", index: number) => `${field}:${index}`;

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</div>
      <div style={{ fontWeight: 500 }}>{value}</div>
    </div>
  );
}

function Options({ options }: { options: Option[] }) {
  return <>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</>;
}

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

// One titled group inside the Generate card's "More options" section.
function OptionGroup({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{title}</div>
        {hint && <div className="os-muted os-small">{hint}</div>}
      </div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>{children}</div>
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [segments, setSegments] = useState<Segment[] | null>(null);
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
  // The blob URL is this page's to release.
  useEffect(() => () => { if (clipUrl.current) URL.revokeObjectURL(clipUrl.current); }, []);
  const [provider, setProvider] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1.0);
  const [presetId, setPresetId] = useState("youtube_1080p");
  const [language, setLanguage] = useState("");
  // The per-render options ("More options"), prefilled from the studio settings once they arrive.
  const [options, setOptions] = useState<GenerateOptions | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const project = useQuery({
    queryKey: ["project", id],
    queryFn: () => api.get<Project>(`/api/projects/${id}`),
  });

  // Decks and PDFs generate a video; videos are transcribed and re-voiced.
  const canGenerate = project.data?.kind === "deck" || project.data?.kind === "pdf";
  const isVideo = project.data?.kind === "video";

  // Keep the editable copy in sync with the saved transcript.
  useEffect(() => {
    if (project.data?.transcript) setSegments(project.data.transcript);
  }, [project.data?.transcript]);

  // The narration provider starts as the studio default (Settings › Studio);
  // the voice list then follows whichever provider is selected.
  const studio = useStudioSettings();
  const studioSettings = studio.data?.settings;
  useEffect(() => {
    if (studioSettings && !provider) setProvider(studioSettings.tts_provider);
  }, [studioSettings, provider]);
  // The transition, pause and watermark start as the studio defaults; the rest as the engine's.
  useEffect(() => {
    if (studioSettings && !options) setOptions(optionsFromStudio(studioSettings));
  }, [studioSettings, options]);

  // Voices are needed by both the deck Generate card and the video Re-voice card.
  const voices = useVoices(provider, canGenerate || isVideo);
  const presets = useQuery({
    queryKey: ["output-presets"],
    queryFn: () => api.get<{ presets: Preset[] }>("/api/output-presets"),
    enabled: canGenerate,
    staleTime: Infinity,
  });
  // Target languages for the optional re-voice translation.
  const languages = useQuery({
    queryKey: ["languages"],
    queryFn: () => api.get<{ languages: Lang[] }>("/api/languages"),
    enabled: isVideo,
    staleTime: Infinity,
  });

  // Preselect the studio's default voice for the provider once its list arrives
  // (else the first en-US voice, else the first). An empty list leaves the voice
  // blank and the server falls back to the studio default itself.
  const voiceList = voices.data?.provider === provider ? voices.data.voices : undefined;
  useEffect(() => {
    if (voiceList && !voiceId) setVoiceId(pickVoice(voiceList, defaultVoiceFor(studioSettings, provider)));
  }, [voiceList, voiceId, studioSettings, provider]);
  // The provider's voice ids, built once and shared by every transcript row:
  // a row's voice box saves at once when what is in it came from picking off
  // the shared list, and waits for a blur when it was typed by hand.
  const voiceIds = useMemo(() => new Set((voiceList ?? []).map((v) => v.voice_id)), [voiceList]);

  const changeProvider = (next: string) => {
    setProvider(next);
    setVoiceId(""); // re-picked from the new provider's list
  };

  const transcribe = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/projects/${id}/transcribe`, {}),
    onSuccess: (r) => setJobId(r.job_id),
  });

  // previewSeconds > 0 renders only the opening seconds to a separate preview clip.
  const generate = useMutation({
    mutationFn: (previewSeconds: number) => {
      if (!options) throw new Error("The studio settings have not loaded yet.");
      const body = generateBody({ provider, voice_id: voiceId, speed, preset: presetId }, options, previewSeconds);
      return api.post<{ job_id: string }>(`/api/projects/${id}/generate`, body);
    },
    onSuccess: (r) => setJobId(r.job_id),
  });

  const revoice = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/projects/${id}/revoice`, { provider, voice_id: voiceId, speed, language }),
    onSuccess: (r) => setJobId(r.job_id),
  });

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "done" || s === "error" ? false : 1200;
    },
  });

  // When a job ends, pull the freshly-saved project (transcript or video) and
  // the slides (a render-slides job wrote their images; an AI job wrote notes)
  // - on an error too: an AI job that stopped early keeps what it had written,
  // and the editor must show those notes, not the ones from before. The job
  // stays polled on an error so its message stays visible; a done job is dropped.
  useEffect(() => {
    const status = job.data?.status;
    if (status === "done" || status === "error") {
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: slidesQueryKey(id) });
    }
    if (status === "done") setJobId(null);
  }, [job.data?.status, id, qc]);

  // The answer carries ``timing_adjustments_dropped``: how many sentences lost
  // their adjustment because the saved list no longer holds the sentence it
  // belonged to. Normally 0 - a Save posts the sentences back as they were
  // given - and shown when it is not, rather than lost quietly.
  const save = useMutation({
    mutationFn: (segs: Segment[]) =>
      api.patch<{ timing_adjustments_dropped?: number }>(
        `/api/projects/${id}/transcript`, { transcript: words(segs) },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
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
  /** Drop one row's draft, so the box shows what is saved again. Declared
   *  before the mutation that calls it on settle. */
  const clearDraft = (key: string) => setDrafts((current) => {
    const next = { ...current };
    delete next[key];
    return next;
  });

  // A committed box keeps its draft until the request SETTLES (`draft` names
  // the key, dropped in onSettled). Clearing it at the moment the mutation is
  // fired left a render where the draft was gone and the optimistic value had
  // not arrived, so a box flicked back to its old value and then forward again.
  const adjust = useMutation({
    mutationFn: ({ index, changes }: { index: number; changes: SegmentOverride; draft?: string }) =>
      api.patch<Segment>(`/api/projects/${id}/transcript/${index}`, changes),
    onMutate: ({ index, changes }) => {
      const previous = segments;
      setSegments((prev) => prev && prev.map((seg, j) => (j === index ? { ...seg, ...changes } : seg)));
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) setSegments(context.previous);
    },
    onSuccess: (saved, { index }) =>
      setSegments((prev) => prev && prev.map((seg, j) => (j === index ? { ...saved, text: seg.text } : seg))),
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
    api.blob(previewUrl(id, index, { provider, voice: voiceId, speed }))
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

  if (project.isLoading) {
    return <Card><Spinner label="Loading project…" /></Card>;
  }
  if (project.isError || !project.data) {
    return <ErrorBox message={errorMessage(project.error) || "Project not found."} />;
  }

  const p = project.data;
  const Icon = KIND_ICON[p.kind];
  const jobStatus = job.data?.status;
  // Only one job runs at a time; its kind tells which card owns the progress
  // bar / error (a render-slides job belongs to the Slides card, a transcribe
  // or re-voice to the video cards), so the other cards stay put.
  const activeKind = job.data?.kind;
  const jobActive = jobStatus === "queued" || jobStatus === "running";
  const jobErrText = jobStatus === "error" ? job.data?.error || job.data?.message : null;
  const running = generate.isPending || (jobActive && activeKind === "generate");
  const jobError = generate.isError ? errorMessage(generate.error) : activeKind === "generate" ? jobErrText : null;

  const providerOptions = studio.data?.options.tts_provider.options ?? [];
  const voiceOptions = voiceList ?? [];
  // Why the narration fields are not usable: the studio settings (provider list
  // and defaults) failed to load, or the selected provider could not list voices.
  const voicesError = studio.isError
    ? errorMessage(studio.error)
    : voices.data?.error ?? (voices.isError ? errorMessage(voices.error) : null);
  const studioDefaultVoice = defaultVoiceFor(studioSettings, provider);
  const presetOptions = presets.data?.presets ?? [];
  const presetHint = presetOptions.find((x) => x.id === presetId)?.description;
  const languageOptions = languages.data?.languages ?? [];
  // Optional-chained past the option lists too: a backend older than this card answers without them.
  const studioOptions = studio.data?.options;
  const transitionOptions = studioOptions?.slide_transition?.options ?? [];
  const positionOptions = studioOptions?.watermark_position?.options ?? [];
  const problems = options ? optionProblems(options, studioOptions) : [];
  // One job per project at a time: the server refuses a generate while any job holds the project (a render
  // over a running AI job would save its own copy of the notes over the AI's), so the buttons wait too.
  const canSend = !!provider && !!options && problems.length === 0 && !generate.isPending && !jobActive;
  const otherJobNotice = jobActive && activeKind ? `A job is running for this project (${activeKind}) — it must finish first.` : null;
  const updateOptions = (patch: Partial<GenerateOptions>) => options && setOptions({ ...options, ...patch });
  const outputLinks = downloadLinks(p.outputs);
  // A re-render keeps the file names, so the players are cache-busted by the render time.
  const mediaVersion = encodeURIComponent(p.rendered_at ?? "");

  // The provider + voice pair, shared by the Generate and Re-voice cards (a
  // project shows one or the other).
  const narrationFields = (
    <>
      <Field label="Narration provider">
        <Select value={provider} onChange={(e) => changeProvider(e.target.value)} disabled={providerOptions.length === 0}>
          {providerOptions.length === 0 && <option value="">Loading…</option>}
          {providerOptions.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </Select>
      </Field>
      <Field label="Voice">
        <Select value={voiceId} onChange={(e) => setVoiceId(e.target.value)} disabled={voiceOptions.length === 0}>
          {voiceOptions.length === 0 && (
            <option value="">{voices.isLoading ? "Loading voices…" : `Studio default${studioDefaultVoice ? ` (${studioDefaultVoice})` : ""}`}</option>
          )}
          {voiceOptions.map((v) => (
            <option key={v.voice_id} value={v.voice_id}>{v.name}</option>
          ))}
        </Select>
      </Field>
    </>
  );

  const transcribing = transcribe.isPending || (jobActive && activeKind === "transcribe");
  const revoicing = revoice.isPending || (jobActive && activeKind === "revoice");
  // A refused adjustment (a 409 while a job holds the project, a 400 for a
  // voice from the other provider) has to be visible: nothing was saved, and
  // the row has already been put back to what the server holds.
  const adjustError = adjust.isError ? errorMessage(adjust.error) : null;
  // And a refused Save: a 422 from the schema or a 409 while a job runs used to
  // leave the button going quiet with nothing written.
  const saveError = save.isError ? errorMessage(save.error) : null;
  const adjustmentsDropped = save.data?.timing_adjustments_dropped ?? 0;
  const dropped = p.revoice_failed_sentences ?? 0;
  const transcribeError = transcribe.isError ? errorMessage(transcribe.error) : activeKind === "transcribe" ? jobErrText : null;
  const revoiceError = revoice.isError ? errorMessage(revoice.error) : activeKind === "revoice" ? jobErrText : null;

  return (
    <>
      <PageHeader
        title={<span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}><Icon size={22} /> {p.name}</span>}
        subtitle={p.source_filename}
        actions={<Link to="/projects" className="os-btn os-btn-secondary os-btn-sm"><ArrowLeft size={15} /> Back</Link>}
      />

      <Card title="Details">
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap" }}>
          <Meta label="Type" value={p.kind} />
          {p.slide_count != null && <Meta label="Slides" value={String(p.slide_count)} />}
          <Meta label="Imported" value={relativeTime(p.created_at)} />
          {p.language && <Meta label="Language" value={p.language} />}
          {p.duration != null && <Meta label="Duration" value={duration(p.duration)} />}
          {p.transcribed_device && <Meta label="Transcribed on" value={p.transcribed_device.toUpperCase()} />}
        </div>
      </Card>

      {(p.kind === "deck" || p.kind === "pdf") && (
        <SlidesCard
          projectId={id}
          projectName={p.name}
          projectKind={p.kind}
          provider={provider}
          job={job.data}
          jobActive={jobActive}
          onJobStarted={setJobId}
          onVoiceSuggested={setVoiceId}
          qaDoc={p.outputs?.qa_doc}
        />
      )}

      {canGenerate && (
        <Card title="Generate video" style={{ marginTop: 16 }}>
          {jobError && <ErrorBox message={jobError} />}
          {!running && voicesError && <ErrorBox message={voicesError} />}

          {running ? (
            <JobProgress job={job.data} />
          ) : (
            <div style={{ display: "grid", gap: 16 }}>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
                {narrationFields}
                <Field label="Speed">
                  <Input
                    type="number"
                    step={0.05}
                    min={0.5}
                    max={2}
                    value={speed}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                    style={{ width: 100 }}
                  />
                </Field>
                <Field label="Output preset">
                  <Select value={presetId} onChange={(e) => setPresetId(e.target.value)}>
                    {presetOptions.map((preset) => (
                      <option key={preset.id} value={preset.id}>{preset.label}</option>
                    ))}
                  </Select>
                </Field>
                <Button variant="primary" icon={<Wand2 size={16} />} disabled={!canSend} onClick={() => generate.mutate(0)}>
                  {p.output_video ? "Regenerate" : "Generate video"}
                </Button>
                <Button
                  icon={<Eye size={16} />}
                  disabled={!canSend}
                  onClick={() => generate.mutate(PREVIEW_SECONDS)}
                  title={`Render only the first ${PREVIEW_SECONDS} seconds to check the voice and the options.`}
                >
                  Preview {PREVIEW_SECONDS} s
                </Button>
              </div>

              {otherJobNotice && <div style={{ color: "var(--muted)", fontSize: 13 }}>{otherJobNotice}</div>}
              {voices.data?.notice && <div style={{ color: "var(--muted)", fontSize: 13 }}>{voices.data.notice}</div>}
              {presetHint && <div style={{ color: "var(--muted)", fontSize: 13 }}>{presetHint}</div>}

              {options && (
                <div style={{ display: "grid", gap: 14 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={moreOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      onClick={() => setMoreOpen(!moreOpen)}
                      aria-expanded={moreOpen}
                    >
                      More options
                    </Button>
                    {!moreOpen && <span className="os-muted os-small">{optionsSummary(options, transitionOptions)}</span>}
                  </div>

                  {moreOpen && (
                    <div style={{ display: "grid", gap: 18, padding: "4px 0 4px 8px", borderLeft: "2px solid var(--border)" }}>
                      <OptionGroup title="Transition" hint="The effect between slides. Any effect but None renders at 24 fps, so the encode takes longer.">
                        <Field label="Effect">
                          <Select value={options.slide_transition} onChange={(e) => updateOptions({ slide_transition: e.target.value })}>
                            <Options options={transitionOptions} />
                          </Select>
                        </Field>
                        <Field label="Duration (s)">
                          <Input
                            type="number"
                            min={studioOptions?.transition_duration.min}
                            max={studioOptions?.transition_duration.max}
                            step={studioOptions?.transition_duration.step}
                            value={options.transition_duration}
                            onChange={(e) => updateOptions({ transition_duration: Number(e.target.value) })}
                            style={{ width: 100 }}
                          />
                        </Field>
                        <Field label="Pause between slides (s)">
                          <Input
                            type="number"
                            min={studioOptions?.transition_pause.min}
                            max={studioOptions?.transition_pause.max}
                            step={studioOptions?.transition_pause.step}
                            value={options.transition_pause}
                            onChange={(e) => updateOptions({ transition_pause: Number(e.target.value) })}
                            style={{ width: 100 }}
                          />
                        </Field>
                      </OptionGroup>

                      <OptionGroup title="Intro card" hint="A title card before the first slide; leave the title empty for none.">
                        <Field label="Title">
                          <Input value={options.intro_text} placeholder="Presentation title" onChange={(e) => updateOptions({ intro_text: e.target.value })} style={{ width: 240 }} />
                        </Field>
                        <Field label="Subtitle">
                          <Input value={options.intro_subtitle} placeholder="Author or subtitle" onChange={(e) => updateOptions({ intro_subtitle: e.target.value })} style={{ width: 240 }} />
                        </Field>
                        <Field label="Duration (s)">
                          <Input
                            type="number"
                            min={CARD_DURATION.min}
                            max={CARD_DURATION.max}
                            step={CARD_DURATION.step}
                            value={options.intro_duration}
                            onChange={(e) => updateOptions({ intro_duration: Number(e.target.value) })}
                            style={{ width: 100 }}
                          />
                        </Field>
                      </OptionGroup>

                      <OptionGroup title="Outro card" hint="A closing card after the last slide; leave the text empty for none.">
                        <Field label="Closing text">
                          <Input value={options.outro_text} placeholder="Thank you! Questions?" onChange={(e) => updateOptions({ outro_text: e.target.value })} style={{ width: 240 }} />
                        </Field>
                        <Field label="Duration (s)">
                          <Input
                            type="number"
                            min={CARD_DURATION.min}
                            max={CARD_DURATION.max}
                            step={CARD_DURATION.step}
                            value={options.outro_duration}
                            onChange={(e) => updateOptions({ outro_duration: Number(e.target.value) })}
                            style={{ width: 100 }}
                          />
                        </Field>
                      </OptionGroup>

                      <OptionGroup title="Watermark" hint="Text drawn over the whole video; leave it empty for none.">
                        <Field label="Text">
                          <Input value={options.watermark_text} placeholder="e.g. Company name" onChange={(e) => updateOptions({ watermark_text: e.target.value })} style={{ width: 240 }} />
                        </Field>
                        <Field label="Position">
                          <Select value={options.watermark_position} onChange={(e) => updateOptions({ watermark_position: e.target.value })}>
                            <Options options={positionOptions} />
                          </Select>
                        </Field>
                        <Field label="Opacity">
                          <Input
                            type="number"
                            min={studioOptions?.watermark_opacity.min}
                            max={studioOptions?.watermark_opacity.max}
                            step={studioOptions?.watermark_opacity.step}
                            value={options.watermark_opacity}
                            onChange={(e) => updateOptions({ watermark_opacity: Number(e.target.value) })}
                            style={{ width: 100 }}
                          />
                        </Field>
                      </OptionGroup>

                      <OptionGroup title="Subtitles">
                        <Field label="Mode" hint={SUBTITLE_OPTIONS.find((o) => o.value === options.subtitles)?.note}>
                          <Select value={options.subtitles} onChange={(e) => updateOptions({ subtitles: e.target.value as SubtitleMode })}>
                            <Options options={SUBTITLE_OPTIONS} />
                          </Select>
                        </Field>
                      </OptionGroup>

                      <OptionGroup title="Extra formats" hint="Made from the finished MP4, downloadable beside it.">
                        <label className="os-checkbox">
                          <input type="checkbox" checked={options.export_webm} onChange={(e) => updateOptions({ export_webm: e.target.checked })} />
                          WebM
                        </label>
                        <label className="os-checkbox">
                          <input type="checkbox" checked={options.export_gif} onChange={(e) => updateOptions({ export_gif: e.target.checked })} />
                          GIF (first 30 s)
                        </label>
                        <label className="os-checkbox">
                          <input type="checkbox" checked={options.export_audio_only} onChange={(e) => updateOptions({ export_audio_only: e.target.checked })} />
                          Audio-only MP3
                        </label>
                      </OptionGroup>
                    </div>
                  )}
                  {problems.map((problem) => <ErrorBox key={problem} message={problem} />)}
                </div>
              )}

              {p.outputs?.preview && (
                <div style={{ display: "grid", gap: 6 }}>
                  <div className="os-muted os-small">Preview — the first {PREVIEW_SECONDS} seconds</div>
                  <video controls src={`/api/projects/${id}/outputs/preview?v=${mediaVersion}`} style={{ width: 360, maxWidth: "100%", borderRadius: 8, background: "#000" }} />
                </div>
              )}

              {p.output_video && (
                <div style={{ display: "grid", gap: 10 }}>
                  <video controls src={`/api/projects/${id}/video?v=${mediaVersion}`} style={{ width: "100%", borderRadius: 8, background: "#000" }} />
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/video`} download={`${p.name}.mp4`}>
                      <Download size={15} /> Download video
                    </a>
                    {outputLinks.map((link) => (
                      <a key={link.kind} className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/outputs/${link.kind}`} download={link.filename}>
                        <Download size={15} /> {link.label}
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      {p.kind === "video" && (
        <Card title="Transcript" style={{ marginTop: 16 }}>
          {transcribeError && <ErrorBox message={transcribeError} />}

          {transcribing ? (
            <JobProgress job={job.data} />
          ) : segments && segments.length > 0 ? (
            <div style={{ display: "grid", gap: 10 }}>
              {adjustError && <ErrorBox message={adjustError} />}
              {saveError && <ErrorBox message={saveError} />}
              {/* What the right-hand controls do, and - as honestly as it can be
                  put - what an offset can and cannot promise. Each sentence is
                  pinned to the moment it was spoken and the leftover time
                  becomes silence; a pin is a floor, not a position, so a
                  sentence can always be pushed later but can only be pulled
                  earlier as far as the previous one's new audio actually ends. */}
              <div className="os-muted os-small">
                Nudge a sentence with <strong>Offset</strong> (seconds: positive is later, negative
                earlier) or leave it out with <strong>Mute</strong>. Each change saves on its own, at
                once. A sentence can always be pushed later; pulling it earlier only moves it as far
                as the sentence before it finishes speaking. Editing the words never moves the
                sentences — Save below keeps every adjustment.
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
                <div style={{ display: "grid", gridTemplateColumns: "84px minmax(180px, 1fr) 236px", gap: 10, alignItems: "start" }}>
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
          ) : (
            <div style={{ display: "grid", gap: 12, justifyItems: "start" }}>
              <div style={{ color: "var(--muted)" }}>No transcript yet. Transcribe the audio to get an editable transcript.</div>
              {otherJobNotice && <div className="os-muted os-small">{otherJobNotice}</div>}
              <Button variant="primary" icon={<Wand2 size={16} />} disabled={transcribe.isPending || jobActive} onClick={() => transcribe.mutate()}>
                Transcribe audio
              </Button>
            </div>
          )}
        </Card>
      )}

      {isVideo && segments && segments.length > 0 && (
        <Card title="Re-voice" subtitle="Regenerate the narration in a new voice (and optionally a language), keeping the original video." style={{ marginTop: 16 }}>
          {revoiceError && <ErrorBox message={revoiceError} />}
          {!revoicing && voicesError && <ErrorBox message={voicesError} />}

          {revoicing ? (
            <JobProgress job={job.data} />
          ) : (
            <div style={{ display: "grid", gap: 16 }}>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
                {narrationFields}
                <Field label="Speed">
                  <Input
                    type="number"
                    step={0.05}
                    min={0.5}
                    max={2}
                    value={speed}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                    style={{ width: 100 }}
                  />
                </Field>
                <Field label="Language">
                  <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                    <option value="">Keep original language</option>
                    {languageOptions.map((l) => (
                      <option key={l.subtag} value={l.name}>{l.name}</option>
                    ))}
                  </Select>
                </Field>
                <Button
                  variant="primary"
                  icon={<Mic size={16} />}
                  disabled={!provider || revoice.isPending || jobActive}
                  onClick={() => revoice.mutate()}
                >
                  {p.revoiced_video ? "Re-voice again" : "Re-voice"}
                </Button>
              </div>

              {otherJobNotice && <div style={{ color: "var(--muted)", fontSize: 13 }}>{otherJobNotice}</div>}
              {voices.data?.notice && <div style={{ color: "var(--muted)", fontSize: 13 }}>{voices.data.notice}</div>}

              {/* A translated re-voice translates the WHOLE transcript as one
                  block, so the engine re-cuts it into sentences and spreads them
                  across the video by length: there is no sentence left that the
                  adjustments above could belong to. Said plainly rather than
                  left to be discovered.

                  Worded as a conditional because the condition here is only
                  "a language is selected": the server skips the translation
                  when the target is English or the video's own language, and in
                  that case the adjustments DO apply. Warning a little too
                  widely is the safe direction; claiming they are lost when they
                  are not would not be. */}
              {language && (
                <div style={{ color: "var(--muted)", fontSize: 13 }}>
                  If this re-voice is translated, the whole transcript is translated as one block and
                  the per-sentence adjustments above — offset, mute, voice and speed — do not apply:
                  the translated sentences are spread across the video by length instead. (Choosing
                  the video's own language, or English for an English video, translates nothing and
                  keeps them.)
                </div>
              )}

              {dropped > 0 && (
                <div style={{ color: "var(--muted)", fontSize: 13 }}>
                  {dropped} sentence{dropped === 1 ? "" : "s"} could not be synthesised in the last
                  re-voice and {dropped === 1 ? "is" : "are"} silent in it. Re-voice again to try
                  {dropped === 1 ? " it" : " them"} once more.
                </div>
              )}

              {p.revoiced_video && (
                <div style={{ display: "grid", gap: 10 }}>
                  <video controls src={`/api/projects/${id}/revoiced-video`} style={{ width: "100%", borderRadius: 8, background: "#000" }} />
                  <div>
                    <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/revoiced-video`} download={`${p.name}-revoiced.mp4`}>
                      <Download size={15} /> Download re-voiced video
                    </a>
                  </div>
                </div>
              )}

              {/* The picture and the two voice tracks as separate files. They
                  share a zero, so they line up when dropped onto a timeline in
                  Camtasia or any other editor - which is how you fix by hand
                  anything the automatic fit gets wrong. Not the same length:
                  the narration is padded to the picture only when it is muxed
                  into the video, so the standalone file ends where the last
                  sentence does. */}
              <div style={{ display: "grid", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>Separate tracks</div>
                <div style={{ color: "var(--muted)", fontSize: 13 }}>
                  The picture and each voice as its own file, aligned to the same start, for editing in another tool.
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {/* The project's own filename, not a hard-coded .mp4: a .mov
                      or .mkv import keeps its container all the way through. */}
                  <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/tracks/picture`} download={p.source_filename}>
                    <Film size={15} /> Picture
                  </a>
                  <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/tracks/original-audio`} download={`${p.name}-original.wav`}>
                    <Mic size={15} /> Original audio
                  </a>
                  {p.narration_audio && (
                    <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/tracks/narration`} download={`${p.name}-narration.mp3`}>
                      <Mic size={15} /> New narration
                    </a>
                  )}
                </div>
                {!p.narration_audio && (
                  <div style={{ color: "var(--muted)", fontSize: 13 }}>
                    Re-voice this video to get its narration as a separate track.
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
