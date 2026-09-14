import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, Film, Mic, Presentation, Save, Wand2 } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { duration, relativeTime } from "../lib/format";
import { defaultVoiceFor, pickVoice, useStudioSettings, useVoices } from "../lib/studioSettings";

interface Segment {
  start: number;
  end: number;
  text: string;
}
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
  revoiced_video?: string;
  revoiced_language?: string;
}
interface Job {
  id: string;
  kind: string;
  status: string;
  progress: number;
  message: string;
  error?: string | null;
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

// Shared progress bar for the transcribe and generate jobs (both poll the same job).
function JobProgress({ job }: { job?: Job }) {
  const pct = Math.round((job?.progress || 0) * 100);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ color: "var(--muted)" }}>{job?.message || "Starting…"}</div>
      <div style={{ height: 8, borderRadius: 6, background: "var(--surface-3)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "var(--brand)", transition: "width .3s ease" }} />
      </div>
      <div style={{ color: "var(--muted)", fontSize: 12, fontVariantNumeric: "tabular-nums" }}>{pct}%</div>
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [segments, setSegments] = useState<Segment[] | null>(null);
  const [provider, setProvider] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1.0);
  const [presetId, setPresetId] = useState("youtube_1080p");
  const [language, setLanguage] = useState("");

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

  const changeProvider = (next: string) => {
    setProvider(next);
    setVoiceId(""); // re-picked from the new provider's list
  };

  const transcribe = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/projects/${id}/transcribe`, {}),
    onSuccess: (r) => setJobId(r.job_id),
  });

  const generate = useMutation({
    mutationFn: () => api.post<{ job_id: string }>(`/api/projects/${id}/generate`, { provider, voice_id: voiceId, speed, preset: presetId }),
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

  // When either job finishes, pull the freshly-saved project (transcript or video).
  useEffect(() => {
    if (job.data?.status === "done") {
      qc.invalidateQueries({ queryKey: ["project", id] });
      setJobId(null);
    }
  }, [job.data?.status, id, qc]);

  const save = useMutation({
    mutationFn: (segs: Segment[]) => api.patch(`/api/projects/${id}/transcript`, { transcript: segs }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  if (project.isLoading) {
    return <Card><Spinner label="Loading project…" /></Card>;
  }
  if (project.isError || !project.data) {
    return <ErrorBox message={errorMessage(project.error) || "Project not found."} />;
  }

  const p = project.data;
  const Icon = KIND_ICON[p.kind];
  const jobStatus = job.data?.status;
  const running = transcribe.isPending || generate.isPending || jobStatus === "queued" || jobStatus === "running";
  const jobError =
    jobStatus === "error"
      ? job.data?.error || job.data?.message
      : transcribe.isError
        ? errorMessage(transcribe.error)
        : generate.isError
          ? errorMessage(generate.error)
          : null;

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

  // Only one job runs at a time; its kind tells which video card owns the
  // progress bar / error, so the transcript editor stays put during a re-voice.
  const activeKind = job.data?.kind;
  const jobActive = jobStatus === "queued" || jobStatus === "running";
  const jobErrText = jobStatus === "error" ? job.data?.error || job.data?.message : null;
  const transcribing = transcribe.isPending || (jobActive && activeKind === "transcribe");
  const revoicing = revoice.isPending || (jobActive && activeKind === "revoice");
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
                <Button
                  variant="primary"
                  icon={<Wand2 size={16} />}
                  disabled={!provider || generate.isPending}
                  onClick={() => generate.mutate()}
                >
                  {p.output_video ? "Regenerate" : "Generate video"}
                </Button>
              </div>

              {voices.data?.notice && <div style={{ color: "var(--muted)", fontSize: 13 }}>{voices.data.notice}</div>}
              {presetHint && <div style={{ color: "var(--muted)", fontSize: 13 }}>{presetHint}</div>}

              {p.output_video && (
                <div style={{ display: "grid", gap: 10 }}>
                  <video controls src={`/api/projects/${id}/video`} style={{ width: "100%", borderRadius: 8, background: "#000" }} />
                  <div>
                    <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${id}/video`} download={`${p.name}.mp4`}>
                      <Download size={15} /> Download video
                    </a>
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
              {segments.map((s, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "64px 1fr", gap: 10, alignItems: "start" }}>
                  <div style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums", fontSize: 13, paddingTop: 8 }}>{duration(s.start)}</div>
                  <Textarea
                    value={s.text}
                    rows={2}
                    onChange={(e) => setSegments(segments.map((seg, j) => (j === i ? { ...seg, text: e.target.value } : seg)))}
                  />
                </div>
              ))}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <Button variant="primary" icon={<Save size={16} />} disabled={save.isPending} onClick={() => segments && save.mutate(segments)}>
                  {save.isPending ? "Saving…" : "Save transcript"}
                </Button>
              </div>
            </div>
          ) : (
            <div style={{ display: "grid", gap: 12, justifyItems: "start" }}>
              <div style={{ color: "var(--muted)" }}>No transcript yet. Transcribe the audio to get an editable transcript.</div>
              <Button variant="primary" icon={<Wand2 size={16} />} onClick={() => transcribe.mutate()}>Transcribe audio</Button>
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
                  disabled={!provider || revoice.isPending}
                  onClick={() => revoice.mutate()}
                >
                  {p.revoiced_video ? "Re-voice again" : "Re-voice"}
                </Button>
              </div>

              {voices.data?.notice && <div style={{ color: "var(--muted)", fontSize: 13 }}>{voices.data.notice}</div>}

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
            </div>
          )}
        </Card>
      )}
    </>
  );
}
