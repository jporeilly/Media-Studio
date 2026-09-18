import { type ReactNode, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ChevronRight, Download, Eye, FileText, Film, Mic, Presentation, Wand2 } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { JobProgress, type Job } from "../components/project/JobProgress";
import { SlidesCard } from "../components/project/SlidesCard";
import { TranscriptCard } from "../components/project/TranscriptCard";
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner } from "../components/ui";
import { renderSummary } from "../lib/edit";
import { duration, relativeTime } from "../lib/format";
import { type Segment } from "../lib/narration";
import { slidesQueryKey } from "../lib/slides";
import { narrationPlanKey, type EditPayload } from "../lib/timeline";
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
  revoiced_at?: string;
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
  // The editable copy of the transcript. It stays on the page rather than
  // inside TranscriptCard because the Re-voice card below only exists once
  // there is one.
  const [segments, setSegments] = useState<Segment[] | null>(null);
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
  // The project's edit - which ranges of the video are kept. A read, so it is
  // allowed during a job; the timeline invalidates it with every cut it
  // commits, and the button below turns into "Render" while it removes
  // anything. The query is the truth here, not a copy on the project record.
  const edit = useQuery({
    queryKey: ["edit", id],
    queryFn: () => api.get<EditPayload>(`/api/projects/${id}/edit`),
    enabled: isVideo,
  });

  // Preselect the studio's default voice for the provider once its list arrives
  // (else the first en-US voice, else the first). An empty list leaves the voice
  // blank and the server falls back to the studio default itself.
  const voiceList = voices.data?.provider === provider ? voices.data.voices : undefined;
  useEffect(() => {
    if (voiceList && !voiceId) setVoiceId(pickVoice(voiceList, defaultVoiceFor(studioSettings, provider)));
  }, [voiceList, voiceId, studioSettings, provider]);
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
      // A transcribe rewrites the sentences AND re-extracts audio.wav, which is
      // the scale the whole timeline is drawn against. Both are held with a
      // long staleTime (the peaks for ever), so without this the audition would
      // go on showing the old recording's waveform and the old sentences.
      qc.invalidateQueries({ queryKey: narrationPlanKey(id) });
      qc.invalidateQueries({ queryKey: ["waveform", id] });
      // A transcribe re-measures the source the edit's ranges are checked against.
      qc.invalidateQueries({ queryKey: ["edit", id] });
    }
    if (status === "done") setJobId(null);
  }, [job.data?.status, id, qc]);

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
  // The same trap on the re-voice side, and worse: every run rewrites
  // <stem>_revoiced.mp4 and <stem>_revoiced_narration.mp3 in place, so without
  // this the browser keeps the copy it cached on the first run. A cached body
  // whose file has since been replaced does not play as the OLD video - it
  // fails to decode, which is a black frame at 0:00 and a download of bytes
  // that are no longer a video.
  const revoiceVersion = encodeURIComponent(p.revoiced_at ?? "");

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
  const dropped = p.revoice_failed_sentences ?? 0;
  const transcribeError = transcribe.isError ? errorMessage(transcribe.error) : activeKind === "transcribe" ? jobErrText : null;
  const revoiceError = revoice.isError ? errorMessage(revoice.error) : activeKind === "revoice" ? jobErrText : null;
  // The re-voice IS the render of the edit (one job kind, one code path -
  // E1): when EITHER track removes anything the button says so, with what it
  // will do and about how long the picture step takes. With no edit, or one
  // that keeps everything, the button and the job are exactly as before.
  const cut = edit.data && edit.data.source_duration !== null
    ? renderSummary(edit.data.video.keep, edit.data.narration.keep, edit.data.source_duration)
    : null;

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

      {isVideo && (
        <TranscriptCard
          projectId={id}
          segments={segments}
          setSegments={setSegments}
          job={job.data}
          jobActive={jobActive}
          transcribing={transcribing}
          transcribeError={transcribeError}
          transcribePending={transcribe.isPending}
          onTranscribe={() => transcribe.mutate()}
          otherJobNotice={otherJobNotice}
          provider={provider}
          voiceId={voiceId}
          speed={speed}
          voiceOptions={voiceOptions}
          studioDefaultVoice={studioDefaultVoice}
        />
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
                  {cut ? "Render" : p.revoiced_video ? "Re-voice again" : "Re-voice"}
                </Button>
              </div>

              {cut && <div style={{ color: "var(--muted)", fontSize: 13 }}>{cut}</div>}
              {/* An edit this version cannot read (a 400 on the GET) must be
                  shown, not swallowed behind a plain "Re-voice": the render
                  would refuse it too, and the message says what to do. */}
              {edit.isError && <ErrorBox message={errorMessage(edit.error)} />}
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
                  <video controls src={`/api/projects/${id}/revoiced-video?v=${revoiceVersion}`} style={{ width: "100%", borderRadius: 8, background: "#000" }} />
                  <div>
                    <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/revoiced-video?v=${revoiceVersion}`} download={`${p.name}-revoiced.mp4`}>
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
                    <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/tracks/narration?v=${revoiceVersion}`} download={`${p.name}-narration.mp3`}>
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
