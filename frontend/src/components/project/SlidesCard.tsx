import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { type UseMutationResult, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check, ChevronDown, ChevronRight, ClipboardCheck, Download, Gauge, Image as ImageIcon, Languages,
  MessageCircleQuestion, MessageSquareText, Pencil, RotateCcw, Save, Sparkles, Timer, Undo2, Wand2, X,
} from "lucide-react";
import { api, errorMessage } from "../../api/client";
import {
  CUSTOM_TONE,
  REWRITE_KINDS,
  aiBanner,
  canCancel,
  cancelTitle,
  emptyNotesSlides,
  isAiJob,
  jobSummary,
  markFixed,
  notesSlides,
  openIssues,
  pacingSummary,
  passesText,
  qaEntryFor,
  qaRows,
  useAiStatus,
  voiceForLanguage,
  type AiTally,
  type Analysis,
  type Language,
  type QaCriterion,
} from "../../lib/ai";
import { relativeTime } from "../../lib/format";
import {
  BUSY_NOTICE,
  RENDER_JOB_KIND,
  bodyPreview,
  bulkBody,
  clampIndex,
  dirtySlides,
  draftNotes,
  dropDrafts,
  dropSettled,
  hasNotes,
  imageUrl,
  imagesNotice,
  isDirty,
  isEdited,
  needsCollapse,
  needsRenderAgain,
  parsePause,
  pauseText,
  renderHint,
  revealDelta,
  slideLabel,
  slidesQueryKey,
  slidesSubtitle,
  stepFromKey,
  visibleEdges,
  useSlides,
  type Drafts,
  type Slide,
  type SlideProjectKind,
  type SlidesPayload,
} from "../../lib/slides";
import { useVoices, withCurrent } from "../../lib/studioSettings";
import { Button, Card, ErrorBox, Field, Input, Modal, Select, Spinner, Textarea } from "../ui";
import { CancelJobButton, JobNotice, JobProgress, type Job } from "./JobProgress";

interface Props {
  projectId: string;
  projectName: string;
  projectKind: SlideProjectKind;
  /** The narration provider chosen in the Generate card: a per-slide voice override picks from its voices. */
  provider: string;
  /** The page's one polled job; a render-slides or ai-* job shows its progress here. */
  job?: Job;
  /** True while any job of this project is queued or running: the server refuses writes then (409), so the editor is read-only. */
  jobActive: boolean;
  /** Whether this viewer may cancel `job`: its starter or an administrator (lib/jobs.ts mayCancelJob); anyone else sees it read-only. */
  mayCancel: boolean;
  /** The line the page keeps for this card once its job is over (a job lost in a restart), with its dismiss. */
  jobNotice?: string | null;
  onDismissJobNotice?: () => void;
  onJobStarted: (jobId: string) => void;
  /** A translation finished with a voice for its language: the Generate card's voice follows. */
  onVoiceSuggested?: (voiceId: string) => void;
  /** The Q&A document the AI assistant wrote (`outputs.qa_doc` on the project), when there is one. */
  qaDoc?: string | null;
}

type AiModal = "notes" | "enhance" | "tone" | "translate" | "pacing" | "qadoc" | "analysis";

/** The line the card keeps once an AI action is over: a finished job's summary, or the rules pacing's. */
interface AiResult {
  text: string;
  download?: string;
  error?: boolean;
}

function firstError(mutations: UseMutationResult<any, unknown, any, unknown>[]): string | null {
  for (const m of mutations) {
    if (m.isError) return errorMessage(m.error);
  }
  return null;
}

/**
 * "Slides": the per-slide editor of a deck or PDF project. A thumbnail rail on the left, the selected slide on
 * the right - its image (once rendered), the deck's title and body text, the speaker notes with Save / Undo /
 * Reset, and the per-slide voice and pause overrides. Drafts are kept per slide, so moving between slides
 * never loses typing; Save all sends every dirty slide at once.
 *
 * On top sits the AI assistant (lib/ai.ts, services/ai_slides.py): a status line for the local Ollama, the
 * whole-deck actions (notes, enhance, QA review, tone, translate, pacing, analyze, Q&A document) as jobs with
 * a Cancel, a per-slide AI Enhance that loads a proposal into the editor with Revert, and the QA review's
 * issues under the notes with a Fix per criterion.
 */
export function SlidesCard({
  projectId, projectName, projectKind, provider, job, jobActive, mayCancel, jobNotice, onDismissJobNotice, onJobStarted,
  onVoiceSuggested, qaDoc,
}: Props) {
  const qc = useQueryClient();
  const query = useSlides(projectId);
  const voices = useVoices(provider);
  const notesId = useId();
  const [selected, setSelected] = useState(0);
  const [drafts, setDrafts] = useState<Drafts>({});
  // The pause field's text while it is being edited; null shows the saved value.
  const [pauseDraft, setPauseDraft] = useState<string | null>(null);
  const [pauseError, setPauseError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [bodyOpen, setBodyOpen] = useState(false);

  // The AI assistant's state: the text each slide held before an AI proposal replaced it (Revert), the open
  // dialog, the dialogs' fields, the last outcome, and the analysis to show.
  const [aiOriginal, setAiOriginal] = useState<Record<number, string>>({});
  const [modal, setModal] = useState<AiModal | null>(null);
  const [useVision, setUseVision] = useState<boolean | null>(null); // null = whatever the status allows
  const [tone, setTone] = useState("Executive");
  const [customPrompt, setCustomPrompt] = useState("");
  const [language, setLanguage] = useState("Spanish");
  const [matchVoice, setMatchVoice] = useState(true);
  const [numQuestions, setNumQuestions] = useState(10);
  const [aiResult, setAiResult] = useState<AiResult | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const handledJob = useRef<string | null>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const lastShown = useRef<number | undefined>(undefined);

  const payload = query.data;
  const slides = payload?.slides ?? [];
  const slide: Slide | undefined = slides[clampIndex(selected, slides.length)];
  const active = job?.status === "queued" || job?.status === "running";
  const rendering = job?.kind === RENDER_JOB_KIND && active;
  const renderError = job?.kind === RENDER_JOB_KIND && job.status === "error" ? job.error || job.message : null;
  const aiRunning = !!job && isAiJob(job.kind) && active;
  const aiError = job && isAiJob(job.kind) && job.status === "error" ? job.error || job.message : null;
  // Writes are refused while a job holds the project (it has its own copy of the slides).
  const busy = jobActive;
  const hint = renderHint(projectKind);

  // The Ollama status follows the previews: a render can turn vision on.
  const ai = useAiStatus(projectId, !!payload, `${payload?.images_source ?? ""}:${payload?.slides_ready ?? ""}`);
  const banner = aiBanner(ai.data, ai.isError ? errorMessage(ai.error) : undefined);
  const aiReady = !!ai.data?.available && !busy;
  const visionUsable = !!ai.data?.vision_usable;
  const vision = visionUsable && (useVision ?? true);
  const emptyCount = emptyNotesSlides(slides).length;
  const notesCount = notesSlides(slides).length;
  const review = payload?.qa_review ?? null;
  const qaEntry = slide ? qaEntryFor(review, slide.index) : undefined;
  const rows = qaRows(qaEntry);
  const languages = useQuery({
    queryKey: ["languages"],
    queryFn: () => api.get<{ languages: Language[] }>("/api/languages"),
    enabled: modal === "translate",
    staleTime: Infinity,
  });
  const languageOptions = languages.data?.languages ?? [];
  const voiceList = voices.data?.provider === provider ? voices.data.voices : [];
  const subtag = languageOptions.find((l) => l.name === language)?.subtag ?? "";
  const matchedVoice = subtag ? voiceForLanguage(voiceList, subtag) : undefined;

  const select = (index: number) => {
    setSelected(clampIndex(index, slides.length));
    setPauseDraft(null);
    setPauseError(null);
    setBodyOpen(false);
  };

  // ← / → move between slides unless the focus is in a field (or a dialog is open).
  const count = slides.length;
  const dialogOpen = confirmReset || modal !== null;
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (dialogOpen) return;
      const step = stepFromKey(e.key, e.target as HTMLElement | null);
      if (!step) return;
      e.preventDefault();
      setSelected((current) => clampIndex(current + step, count));
      setPauseDraft(null);
      setPauseError(null);
      setBodyOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, dialogOpen]);

  // The selected thumbnail stays in view inside the rail: ← / → can step past the part that shows. Only the
  // rail scrolls, so the page does not jump; a click lands on a thumbnail that already shows, so nothing moves.
  // Not on the first load: the opening slide is a selection nobody made, and the page must not move by itself.
  const shownIndex = slide?.index;
  useEffect(() => {
    const previous = lastShown.current;
    lastShown.current = shownIndex;
    if (previous === undefined || previous === shownIndex) return;
    const rail = railRef.current;
    const item = rail?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!rail || !item) return;
    // Against the part of the rail on screen: before the page scrolls, the sticky rail runs past the window.
    const view = { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth };
    const shown = visibleEdges(rail.getBoundingClientRect(), view);
    if (!shown) return;
    const { dx, dy } = revealDelta(shown, item.getBoundingClientRect());
    if (dx || dy) rail.scrollBy({ left: dx, top: dy });
    // At the end of its own scroll the rail cannot bring the last slides up while its bottom is still below
    // the window: the page moves the rest of the way (and only then).
    const rest = revealDelta(view, item.getBoundingClientRect()).dy;
    if (rest) window.scrollBy({ top: rest });
  }, [shownIndex]);

  // An AI job that ended - done, or stopped on an error with what it had written kept - leaves its summary
  // on the card (the page drops a done job right after); a job that rewrote notes makes every draft and
  // proposal stale, so they go; a translation that found a voice for its language hands it to the
  // Generate card.
  useEffect(() => {
    if (!job || !isAiJob(job.kind) || (job.status !== "done" && job.status !== "error") || handledJob.current === job.id) return;
    handledJob.current = job.id;
    if (REWRITE_KINDS.has(job.kind)) {
      setDrafts({});
      setAiOriginal({});
    }
    if (job.status !== "done") return; // the error shows through `aiError`
    const result: AiTally = job.result && typeof job.result === "object" ? job.result : {};
    setAiResult({ text: jobSummary(job), download: job.kind === "ai-qa-doc" ? result.download : undefined });
    if (job.kind === "ai-translate" && result.suggested_voice_id && onVoiceSuggested) onVoiceSuggested(result.suggested_voice_id);
  }, [job, onVoiceSuggested]);

  // The server's copy of one slide replaces the cached one (no refetch of the whole deck).
  const putSlide = (updated: Slide) => {
    qc.setQueryData<SlidesPayload>(slidesQueryKey(projectId), (old) =>
      old ? { ...old, slides: old.slides.map((s) => (s.index === updated.index ? updated : s)) } : old,
    );
  };
  const patchSlide = (index: number, body: Record<string, unknown>) => api.patch<Slide>(`/api/projects/${projectId}/slides/${index}`, body);
  const forgetProposal = (index: number) =>
    setAiOriginal((o) => {
      const out = { ...o };
      delete out[index];
      return out;
    });

  const saveNotes = useMutation({
    mutationFn: (index: number) => patchSlide(index, { speaker_notes: drafts[index] ?? "" }),
    // Only a draft that now matches the saved text is settled: keystrokes typed while the save ran are kept.
    onSuccess: (updated) => {
      putSlide(updated);
      setDrafts((d) => dropSettled(d, [updated]));
      forgetProposal(updated.index);
    },
  });
  // The voice and pause overrides save as they change; a notes draft on the slide is kept.
  const saveOverride = useMutation({
    mutationFn: ({ index, body }: { index: number; body: Record<string, unknown> }) => patchSlide(index, body),
    onSuccess: (updated) => {
      putSlide(updated);
      setPauseDraft(null);
    },
  });
  const undo = useMutation({
    mutationFn: (index: number) => api.post<Slide>(`/api/projects/${projectId}/slides/${index}/undo`, {}),
    onSuccess: (updated) => {
      putSlide(updated);
      setDrafts((d) => dropDrafts(d, [updated.index]));
      forgetProposal(updated.index);
    },
  });
  const reset = useMutation({
    mutationFn: (index: number) => api.post<Slide>(`/api/projects/${projectId}/slides/${index}/reset`, {}),
    onSuccess: (updated) => {
      putSlide(updated);
      setDrafts((d) => dropDrafts(d, [updated.index]));
      forgetProposal(updated.index);
      setConfirmReset(false);
    },
  });
  const saveAll = useMutation({
    mutationFn: () => api.patch<{ slides: Slide[] }>(`/api/projects/${projectId}/slides`, bulkBody(slides, drafts)),
    onSuccess: (r) => {
      qc.setQueryData<SlidesPayload>(slidesQueryKey(projectId), (old) => (old ? { ...old, slides: r.slides } : old));
      setDrafts((d) => dropSettled(d, r.slides));
      setAiOriginal({});
    },
  });
  // `force` exports the previews again even though every image exists (rendered before their source was recorded).
  const render = useMutation({
    mutationFn: (force: boolean) =>
      api.post<{ job_id?: string; cached?: boolean }>(`/api/projects/${projectId}/slides/render`, force ? { force: true } : {}),
    onSuccess: (r) => {
      if (r.job_id) onJobStarted(r.job_id);
      else qc.invalidateQueries({ queryKey: slidesQueryKey(projectId) });
    },
  });

  // -- the AI assistant ------------------------------------------------------------------------------------
  const startAi = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => api.post<{ job_id: string }>(`/api/projects/${projectId}/ai/${path}`, body),
    onSuccess: (r) => {
      setModal(null);
      setAiResult(null);
      onJobStarted(r.job_id);
    },
  });
  const pacingRules = useMutation({
    mutationFn: () => api.post<AiTally>(`/api/projects/${projectId}/ai/pacing`, {}),
    onSuccess: (r) => {
      setModal(null);
      setDrafts({});
      setAiOriginal({});
      qc.invalidateQueries({ queryKey: slidesQueryKey(projectId) });
      setAiResult({ text: pacingSummary(r) });
    },
  });
  const analyze = useMutation({
    mutationFn: () => api.post<Analysis>(`/api/projects/${projectId}/ai/analyze`, {}),
    onSuccess: (r) => {
      setAnalysis(r);
      setModal("analysis");
    },
  });
  // The proposal replaces the editor's text as a draft; the text it replaced is kept for Revert.
  const enhanceOne = useMutation({
    mutationFn: ({ index, notes }: { index: number; notes: string }) =>
      api.post<{ suggestion: string; vision: boolean; vision_reason: string | null }>(`/api/projects/${projectId}/slides/${index}/ai/enhance`, {
        use_vision: vision,
        notes,
      }),
    onSuccess: (r, { index, notes }) => {
      setAiOriginal((o) => ({ ...o, [index]: notes }));
      setDrafts((d) => ({ ...d, [index]: r.suggestion }));
    },
  });
  const qaFix = useMutation({
    mutationFn: ({ index, criterion, issue }: { index: number; criterion: QaCriterion; issue: string }) =>
      api.post<Slide>(`/api/projects/${projectId}/slides/${index}/ai/qa-fix`, { criterion, issue }),
    onSuccess: (updated, { index, criterion }) => {
      putSlide(updated);
      setDrafts((d) => dropDrafts(d, [index]));
      forgetProposal(index);
      qc.setQueryData<SlidesPayload>(slidesQueryKey(projectId), (old) => (old ? { ...old, qa_review: markFixed(old.qa_review, index, criterion) } : old));
    },
  });
  const cancel = useMutation({
    mutationFn: (jobId: string) => api.post<Job>(`/api/jobs/${jobId}/cancel`, {}),
  });
  // A Cancel that failed says so under its own job, not under the next one.
  const resetCancel = cancel.reset;
  const currentJobId = job?.id;
  useEffect(() => {
    resetCancel();
  }, [currentJobId, resetCancel]);

  const revert = (index: number) => {
    const original = aiOriginal[index];
    const saved = slides.find((s) => s.index === index)?.speaker_notes;
    setDrafts((d) => (original === undefined || original === saved ? dropDrafts(d, [index]) : { ...d, [index]: original }));
    forgetProposal(index);
  };
  const closeModal = () => setModal(null);

  const commitPause = () => {
    if (!slide || pauseDraft === null) return;
    const parsed = parsePause(pauseDraft);
    if (!parsed.ok) {
      setPauseError(parsed.message);
      return;
    }
    setPauseError(null);
    if (parsed.value === slide.pause_override) {
      setPauseDraft(null);
      return;
    }
    saveOverride.mutate({ index: slide.index, body: { pause_override: parsed.value } });
  };

  const dirty = slide ? isDirty(slide, drafts) : false;
  const canSave = !!slide && dirty && !saveNotes.isPending && !busy;

  const onNotesKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (canSave && slide) saveNotes.mutate(slide.index);
    }
  };

  const dirtyCount = dirtySlides(slides, drafts).length;
  // The whole-deck rewrites work on the SAVED notes and would silently bury an unsaved draft under their
  // result, so they wait for Save all (or a discard) - a review and the read-only actions do not.
  const draftsBlock = dirtyCount > 0;
  const draftsTitle = `Save all or discard the ${dirtyCount} unsaved ${dirtyCount === 1 ? "draft" : "drafts"} first: this rewrites the saved notes.`;
  const problem = pauseError ?? renderError ?? aiError ?? firstError([
    saveNotes, saveAll, undo, reset, saveOverride, render, startAi, pacingRules, analyze, enhanceOne, qaFix, cancel,
  ]);
  const notice = payload ? imagesNotice(payload.images_source, payload.slides_ready) : null;
  const version = payload?.images_rendered_at;
  const open = openIssues(review);
  const proposal = slide ? aiOriginal[slide.index] !== undefined : false;
  const fixing = qaFix.isPending && qaFix.variables?.index === slide?.index;

  const visionField = (
    <label className="os-checkbox" title={ai.data?.vision_reason ?? undefined}>
      <input type="checkbox" checked={vision} disabled={!visionUsable} onChange={(e) => setUseVision(e.target.checked)} />
      <span>
        Show the model each slide's image
        {!visionUsable && ai.data?.vision_reason ? <span className="os-muted"> — off: {ai.data.vision_reason}</span> : null}
      </span>
    </label>
  );
  const starting = startAi.isPending;

  return (
    <Card
      title="Slides"
      subtitle={payload ? slidesSubtitle(slides) : undefined}
      style={{ marginTop: 16 }}
      actions={
        payload && slides.length > 0 ? (
          <>
            {!payload.slides_ready && !rendering && (
              <Button size="sm" icon={<ImageIcon size={15} />} disabled={render.isPending || busy} title={hint} onClick={() => render.mutate(false)}>
                Render slide previews
              </Button>
            )}
            {needsRenderAgain(payload.images_source, payload.slides_ready) && !rendering && (
              <Button
                size="sm"
                icon={<ImageIcon size={15} />}
                disabled={render.isPending || busy}
                title={`These previews have no recorded source, so the AI cannot use them as images. ${hint}`}
                onClick={() => render.mutate(true)}
              >
                Render again
              </Button>
            )}
            {projectKind === "deck" && (
              <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${projectId}/export/pptx`} download={`${projectName}-notes.pptx`}>
                <Download size={15} /> Export .pptx with notes
              </a>
            )}
            <Button variant="primary" size="sm" icon={<Save size={15} />} disabled={dirtyCount === 0 || saveAll.isPending || busy} onClick={() => saveAll.mutate()}>
              {saveAll.isPending ? "Saving…" : dirtyCount ? `Save all (${dirtyCount})` : "Save all"}
            </Button>
          </>
        ) : undefined
      }
    >
      {query.isLoading && <Spinner label="Loading the slides…" />}
      {query.isError && <ErrorBox message={errorMessage(query.error)} />}
      {problem && <ErrorBox message={problem} />}
      {jobNotice && onDismissJobNotice && <JobNotice text={jobNotice} onDismiss={onDismissJobNotice} />}
      {rendering && (
        <div style={{ marginBottom: 14 }}>
          <JobProgress job={job} />
        </div>
      )}
      {aiRunning && job && (
        <div style={{ marginBottom: 14, display: "grid", gap: 10 }}>
          <JobProgress job={job} />
          {/* Read-only for a viewer who did not start the job (the page follows the project's job whoever
              started it); the cancel route's rule, lib/jobs.ts mayCancelJob. */}
          {canCancel(job.kind) && mayCancel && (
            <CancelJobButton job={job} pending={cancel.isPending} title={cancelTitle(job.kind)} onCancel={() => cancel.mutate(job.id)} />
          )}
        </div>
      )}
      {busy && <div className="os-muted os-small" style={{ marginBottom: 12 }}>{BUSY_NOTICE}</div>}
      {payload && !payload.slides_ready && !rendering && <div className="os-muted os-small" style={{ marginBottom: 12 }}>No previews yet. {hint}</div>}
      {notice && <div className="os-muted os-small" style={{ marginBottom: 12 }}>{notice}</div>}
      {voices.data?.notice && <div className="os-muted os-small" style={{ marginBottom: 12 }}>{voices.data.notice}</div>}

      {payload && slides.length === 0 && <div className="os-muted">This file has no slides.</div>}

      {payload && slides.length > 0 && (
        <div className="os-ai-bar">
          <div className={`os-ai-status ${banner.tone}`} title={ai.data?.vision_reason ?? undefined}>
            <Sparkles size={14} /> {banner.text}
            {review && (
              <span className="os-muted">
                {" · "}QA score {review.score}/10 · {open} open {open === 1 ? "issue" : "issues"} · {relativeTime(review.run_at)}
              </span>
            )}
          </div>
          <div className="os-ai-tools">
            <Button
              size="sm"
              icon={<Sparkles size={14} />}
              disabled={!aiReady || emptyCount === 0 || draftsBlock}
              title={draftsBlock ? draftsTitle : emptyCount === 0 ? "Every slide already has notes." : `Write notes for the ${emptyCount} slides without any.`}
              onClick={() => setModal("notes")}
            >
              Generate notes{emptyCount ? ` (${emptyCount})` : ""}
            </Button>
            <Button
              size="sm"
              icon={<Wand2 size={14} />}
              disabled={!aiReady || draftsBlock}
              title={draftsBlock ? draftsTitle : "Rewrite every slide's notes for natural narration."}
              onClick={() => setModal("enhance")}
            >
              Enhance all
            </Button>
            <Button
              size="sm"
              icon={<ClipboardCheck size={14} />}
              disabled={!aiReady || notesCount === 0 || starting}
              title={`Reviews the ${notesCount} slides with notes for grammar, tone, flow and transitions in ${passesText(notesCount)}.`}
              onClick={() => startAi.mutate({ path: "qa", body: {} })}
            >
              QA review
            </Button>
            <Button
              size="sm"
              icon={<MessageSquareText size={14} />}
              disabled={!aiReady || notesCount === 0 || draftsBlock}
              title={draftsBlock ? draftsTitle : "Rewrite the notes for an audience."}
              onClick={() => setModal("tone")}
            >
              Tone
            </Button>
            <Button
              size="sm"
              icon={<Languages size={14} />}
              disabled={!aiReady || notesCount === 0 || draftsBlock}
              title={draftsBlock ? draftsTitle : "Translate the notes in place."}
              onClick={() => setModal("translate")}
            >
              Translate
            </Button>
            <Button
              size="sm"
              icon={<Timer size={14} />}
              disabled={busy || notesCount === 0 || draftsBlock}
              title={draftsBlock ? draftsTitle : "Insert narration pauses."}
              onClick={() => setModal("pacing")}
            >
              Pacing
            </Button>
            <span className="os-ai-sep" aria-hidden="true" />
            <Button
              size="sm"
              icon={<Gauge size={14} />}
              disabled={busy || analyze.isPending}
              title="Score the deck for video: text density, notes coverage, visuals (the model adds suggestions when it is reachable)."
              onClick={() => analyze.mutate()}
            >
              {analyze.isPending ? "Analyzing…" : "Analyze"}
            </Button>
            <Button size="sm" icon={<MessageCircleQuestion size={14} />} disabled={!aiReady || notesCount === 0} title="Anticipated audience questions with answers, as a text document." onClick={() => setModal("qadoc")}>
              Q&amp;A doc
            </Button>
            {qaDoc && (
              <a className="os-btn os-btn-secondary os-btn-sm" href={`/api/projects/${projectId}/export/qa`} download title="The last Q&A document written for this project.">
                <Download size={14} /> Download Q&amp;A doc
              </a>
            )}
          </div>
          {aiResult && (
            <div className="os-ai-result" role="status">
              <Sparkles size={14} />
              <span>{aiResult.text}</span>
              {aiResult.download && (
                <a className="os-btn os-btn-secondary os-btn-sm" href={aiResult.download} download>
                  <Download size={14} /> Download
                </a>
              )}
              <button type="button" className="os-icon-btn" aria-label="Dismiss" onClick={() => setAiResult(null)}>
                <X size={14} />
              </button>
            </div>
          )}
        </div>
      )}

      {payload && slide && (
        <div className="os-slides">
          <div className="os-slide-rail" role="listbox" aria-label="Slides" ref={railRef}>
            {slides.map((s) => {
              const dirtyRail = isDirty(s, drafts);
              const current = s.index === slide.index;
              const rowIssues = qaRows(qaEntryFor(review, s.index)).filter((row) => !row.fixed).length;
              return (
                <button
                  key={s.index}
                  type="button"
                  role="option"
                  aria-selected={current}
                  aria-label={slideLabel(s)}
                  className={`os-slide-thumb ${current ? "selected" : ""}`}
                  title={slideLabel(s)}
                  onClick={() => select(s.index)}
                >
                  {s.has_image ? (
                    <img src={imageUrl(projectId, s.index, version)} alt="" loading="lazy" />
                  ) : (
                    <div className="os-slide-placeholder"><ImageIcon size={16} /></div>
                  )}
                  <div className="os-slide-meta">
                    <span className="os-mono">{s.index + 1}</span>
                    {(hasNotes(s) || dirtyRail) && (
                      <span className={`os-slide-dot ${dirtyRail ? "dirty" : ""}`} title={dirtyRail ? "Unsaved changes" : "Has notes"} />
                    )}
                    {isEdited(s) && <Pencil size={11} aria-label="Notes edited" />}
                    {s.ai_enhanced && <Sparkles size={11} aria-label="AI-written notes" />}
                    {rowIssues > 0 && <span className="os-slide-badge" title={`${rowIssues} open QA ${rowIssues === 1 ? "issue" : "issues"}`}>QA {rowIssues}</span>}
                    {s.has_animation && <span className="os-slide-badge">Animated</span>}
                  </div>
                </button>
              );
            })}
          </div>

          <div style={{ display: "grid", gap: 12, minWidth: 0, alignContent: "start" }}>
            {slide.has_image ? (
              <img className="os-slide-stage" src={imageUrl(projectId, slide.index, version)} alt={slide.alt_text ?? slideLabel(slide)} />
            ) : (
              <div className="os-slide-stage os-slide-placeholder">Not rendered yet</div>
            )}

            <div style={{ display: "grid", gap: 4 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600 }}>{slideLabel(slide)}</span>
                {slide.has_animation && <span className="os-slide-badge">Animated</span>}
                {slide.ai_enhanced && <span className="os-slide-badge" title="These notes were written or rewritten by the AI assistant.">AI</span>}
              </div>
              {slide.body_text && (
                <div className="os-slide-text">
                  {bodyOpen || !needsCollapse(slide.body_text) ? slide.body_text : bodyPreview(slide.body_text)}
                  {needsCollapse(slide.body_text) && (
                    <div>
                      <Button variant="ghost" size="sm" icon={bodyOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />} onClick={() => setBodyOpen(!bodyOpen)} aria-expanded={bodyOpen}>
                        {bodyOpen ? "Show less" : "Show more"}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor={notesId} className="os-field-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                Speaker notes
                {dirty && <span className="os-slide-dot dirty" title="Unsaved changes" />}
                {proposal && <span className="os-dim os-xs">AI proposal — Save to keep, Revert to discard</span>}
                {slide.notes_history_depth > 0 && (
                  <span className="os-dim os-xs">{slide.notes_history_depth} undo {slide.notes_history_depth === 1 ? "step" : "steps"}</span>
                )}
              </label>
              <Textarea
                id={notesId}
                rows={8}
                value={draftNotes(slide, drafts)}
                placeholder="No notes: this slide shows for a few seconds without narration."
                disabled={busy}
                title={busy ? BUSY_NOTICE : undefined}
                onChange={(e) => setDrafts({ ...drafts, [slide.index]: e.target.value })}
                onKeyDown={onNotesKey}
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <Button variant="primary" size="sm" icon={<Save size={15} />} disabled={!canSave} onClick={() => saveNotes.mutate(slide.index)}>
                {saveNotes.isPending ? "Saving…" : "Save"}
              </Button>
              <Button
                size="sm"
                icon={<Undo2 size={15} />}
                disabled={slide.notes_history_depth === 0 || dirty || undo.isPending || busy}
                title={dirty ? "Save or reset the unsaved draft first: Undo restores the text before the last saved edit." : "Restore the text before the last saved edit."}
                onClick={() => undo.mutate(slide.index)}
              >
                Undo
              </Button>
              <Button size="sm" icon={<RotateCcw size={15} />} disabled={(!isEdited(slide) && !dirty) || reset.isPending || busy} onClick={() => setConfirmReset(true)}>
                Reset
              </Button>
              <Button
                size="sm"
                icon={<Sparkles size={15} />}
                disabled={!aiReady || enhanceOne.isPending}
                title={ai.data?.available ? "Ask the model for a rewrite of these notes; it lands in the editor as a proposal, nothing is saved until you do." : banner.text}
                onClick={() => enhanceOne.mutate({ index: slide.index, notes: draftNotes(slide, drafts) })}
              >
                {enhanceOne.isPending ? "Enhancing…" : "AI Enhance"}
              </Button>
              {proposal && (
                <Button size="sm" icon={<Undo2 size={15} />} title="Discard the AI proposal and restore the text it replaced." onClick={() => revert(slide.index)}>
                  Revert
                </Button>
              )}
              <span className="os-dim os-xs">← → move between slides · Ctrl+S saves</span>
            </div>

            {qaEntry && (
              <div className="os-qa">
                <div className="os-field-label">
                  QA review{review ? ` · score ${review.score}/10` : ""}{rows.length === 0 ? " · no issues on this slide" : ""}
                </div>
                {rows.map((row) => (
                  <div key={row.criterion} className={`os-qa-row ${row.fixed ? "fixed" : ""}`}>
                    <span className="os-qa-label">{row.label}</span>
                    <span className="os-qa-issue">{row.issue}</span>
                    {row.fixed ? (
                      <Button size="sm" className="os-btn-good" disabled icon={<Check size={14} />}>Fixed</Button>
                    ) : (
                      <Button
                        size="sm"
                        disabled={!aiReady || dirty || fixing}
                        title={dirty ? "Save or revert the draft first: the fix applies to the saved notes." : "Ask the model to fix only this, and save."}
                        onClick={() => qaFix.mutate({ index: slide.index, criterion: row.criterion, issue: row.issue })}
                      >
                        {fixing && qaFix.variables?.criterion === row.criterion ? "Fixing…" : "Fix"}
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
              <Field label="Voice override" hint={voices.isLoading ? "Loading the voices…" : "Narrates this slide only; the rest use the voice chosen below."}>
                <Select
                  value={slide.voice_override ?? ""}
                  disabled={saveOverride.isPending || busy}
                  onChange={(e) => saveOverride.mutate({ index: slide.index, body: { voice_override: e.target.value || null, provider } })}
                >
                  <option value="">Studio default</option>
                  {withCurrent(voiceList, slide.voice_override ?? "", voices.isLoading).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Pause after slide (s)" hint="Blank = the render's pause between slides.">
                <Input
                  type="number"
                  min={0}
                  max={30}
                  step={0.5}
                  placeholder="default"
                  value={pauseDraft ?? pauseText(slide.pause_override)}
                  disabled={saveOverride.isPending || busy}
                  aria-invalid={!!pauseError}
                  onChange={(e) => {
                    setPauseDraft(e.target.value);
                    setPauseError(null);
                  }}
                  onBlur={commitPause}
                  onKeyDown={(e) => e.key === "Enter" && commitPause()}
                  style={{ width: 120 }}
                />
              </Field>
            </div>
          </div>
        </div>
      )}

      {slide && (
        <Modal
          open={confirmReset}
          onClose={() => setConfirmReset(false)}
          title={`Reset the notes of slide ${slide.index + 1}?`}
          width={440}
          footer={
            <>
              <Button onClick={() => setConfirmReset(false)}>Cancel</Button>
              <Button variant="danger" disabled={reset.isPending || busy} onClick={() => reset.mutate(slide.index)}>
                {reset.isPending ? "Resetting…" : "Reset notes"}
              </Button>
            </>
          }
        >
          <p>
            {projectKind === "deck"
              ? "The notes go back to what the deck holds. The current text stays in the undo history."
              : "A PDF has no notes of its own, so the notes are cleared. The current text stays in the undo history."}
          </p>
          {dirty && <p className="os-muted os-small">The unsaved draft on this slide is discarded.</p>}
        </Modal>
      )}

      <Modal
        open={modal === "notes"}
        onClose={closeModal}
        title="Generate speaker notes"
        width={480}
        footer={
          <>
            <Button onClick={closeModal}>Cancel</Button>
            <Button variant="primary" icon={<Sparkles size={15} />} disabled={starting || emptyCount === 0} onClick={() => startAi.mutate({ path: "notes", body: { scope: "empty", use_vision: vision } })}>
              {starting ? "Starting…" : `Generate notes for ${emptyCount} ${emptyCount === 1 ? "slide" : "slides"}`}
            </Button>
          </>
        }
      >
        <p>
          Writes speaker notes for the {emptyCount} {emptyCount === 1 ? "slide" : "slides"} without any, from each slide's title and text
          {vision ? " and its image" : ""}. Slides that already have notes are left alone. One model call per slide, so a large deck takes a while;
          the editor is read-only until it finishes, and Cancel keeps what is written.
        </p>
        {visionField}
      </Modal>

      <Modal
        open={modal === "enhance"}
        onClose={closeModal}
        title="Enhance all notes"
        width={480}
        footer={
          <>
            <Button onClick={closeModal}>Cancel</Button>
            <Button variant="primary" icon={<Wand2 size={15} />} disabled={starting} onClick={() => startAi.mutate({ path: "enhance", body: { scope: "all", use_vision: vision } })}>
              {starting ? "Starting…" : `Enhance ${slides.length} ${slides.length === 1 ? "slide" : "slides"}`}
            </Button>
          </>
        }
      >
        <p>
          Rewrites the notes of all {slides.length} slides for clear, natural narration, using each slide's title and text
          {vision ? " and its image" : ""}; a slide without notes gets them from its content. Each slide's current text stays in its undo history.
        </p>
        {visionField}
      </Modal>

      <Modal
        open={modal === "tone"}
        onClose={closeModal}
        title="Adapt the tone"
        width={480}
        footer={
          <>
            <Button onClick={closeModal}>Cancel</Button>
            <Button
              variant="primary"
              icon={<MessageSquareText size={15} />}
              disabled={starting || (tone === CUSTOM_TONE && !customPrompt.trim())}
              onClick={() => startAi.mutate({ path: "tone", body: { tone, custom_prompt: tone === CUSTOM_TONE ? customPrompt : null } })}
            >
              {starting ? "Starting…" : "Rewrite the notes"}
            </Button>
          </>
        }
      >
        <p>Rewrites every slide's notes for an audience. Each slide's current text stays in its undo history.</p>
        <Field label="Audience" hint={ai.data?.tones.find((t) => t.name === tone)?.description}>
          <Select value={tone} onChange={(e) => setTone(e.target.value)}>
            {(ai.data?.tones ?? []).map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </Select>
        </Field>
        {tone === CUSTOM_TONE && (
          <Field label="Instruction" hint="How the notes should be rewritten, in your own words.">
            <Textarea rows={3} maxLength={2000} value={customPrompt} placeholder="Rewrite for first-year students; keep every number." onChange={(e) => setCustomPrompt(e.target.value)} />
          </Field>
        )}
      </Modal>

      <Modal
        open={modal === "translate"}
        onClose={closeModal}
        title="Translate the notes"
        width={480}
        footer={
          <>
            <Button onClick={closeModal}>Cancel</Button>
            <Button
              variant="primary"
              icon={<Languages size={15} />}
              disabled={starting || !language}
              onClick={() => startAi.mutate({ path: "translate", body: { language, match_voice: matchVoice, provider: provider || null } })}
            >
              {starting ? "Starting…" : `Translate to ${language}`}
            </Button>
          </>
        }
      >
        <Field label="Target language">
          <Select value={language} onChange={(e) => setLanguage(e.target.value)} disabled={languageOptions.length === 0}>
            {languageOptions.length === 0 && <option value={language}>{languages.isLoading ? "Loading…" : language}</option>}
            {languageOptions.map((l) => (
              <option key={l.subtag} value={l.name}>{l.name}</option>
            ))}
          </Select>
        </Field>
        <label className="os-checkbox">
          <input type="checkbox" checked={matchVoice} onChange={(e) => setMatchVoice(e.target.checked)} />
          Switch narration to a matching voice
        </label>
        {matchVoice && (
          <div className="os-muted os-small">
            {matchedVoice
              ? `The Generate card's voice becomes ${matchedVoice.name} (${matchedVoice.locale}) when the translation finishes.`
              : voiceList.length
                ? `No ${language} voice in the ${provider === "kokoro" ? "Kokoro" : "Edge TTS"} list — the voice is kept.`
                : "The server picks the first voice of the narration provider in that language, if it has one."}
          </div>
        )}
        <p className="os-muted os-small">
          Rewrites every slide's speaker notes into {language} with the local Ollama model. This replaces the notes in place — export a copy of the
          deck first if you want to keep the original language. Each slide's current text stays in its undo history.
        </p>
      </Modal>

      <Modal
        open={modal === "pacing"}
        onClose={closeModal}
        title="Narration pacing"
        width={480}
        footer={
          <>
            <Button onClick={closeModal}>Cancel</Button>
            <Button icon={<Timer size={15} />} disabled={pacingRules.isPending || busy} onClick={() => pacingRules.mutate()}>
              {pacingRules.isPending ? "Applying…" : "Apply the rules"}
            </Button>
            <Button
              variant="primary"
              icon={<Sparkles size={15} />}
              disabled={starting || !aiReady}
              title={aiReady ? undefined : banner.text}
              onClick={() => startAi.mutate({ path: "pacing", body: { use_ai: true } })}
            >
              {starting ? "Starting…" : "Pace with the model"}
            </Button>
          </>
        }
      >
        <p>
          Inserts pauses ("…") into the notes so the narration breathes: after transition phrases, before statistics, around questions.
          <strong> Rules</strong> apply instantly and need no model; <strong>the model</strong> places the pauses itself, one call per slide, and
          falls back to the rules where it fails. Each slide's current text stays in its undo history.
        </p>
      </Modal>

      <Modal
        open={modal === "qadoc"}
        onClose={closeModal}
        title="Q&A document"
        width={440}
        footer={
          <>
            <Button onClick={closeModal}>Cancel</Button>
            <Button
              variant="primary"
              icon={<MessageCircleQuestion size={15} />}
              disabled={starting || !(numQuestions >= 1 && numQuestions <= 50)}
              onClick={() => startAi.mutate({ path: "qa-doc", body: { num_questions: numQuestions } })}
            >
              {starting ? "Starting…" : "Write the document"}
            </Button>
          </>
        }
      >
        <p>Anticipated audience questions with prepared answers and the slide each refers to, from the notes, as a text file you can download here.</p>
        <Field label="Questions" hint="1 to 50.">
          <Input
            type="number"
            min={1}
            max={50}
            step={1}
            value={numQuestions}
            aria-invalid={!(numQuestions >= 1 && numQuestions <= 50)}
            onChange={(e) => setNumQuestions(Math.trunc(Number(e.target.value)))}
            style={{ width: 100 }}
          />
        </Field>
      </Modal>

      <Modal open={modal === "analysis" && !!analysis} onClose={closeModal} title="Slide analysis" width={640} footer={<Button onClick={closeModal}>Close</Button>}>
        {analysis && (
          <>
            <div style={{ fontWeight: 600, color: analysis.overall_score >= 7 ? "var(--good)" : analysis.overall_score >= 5 ? "var(--warn)" : "var(--bad)" }}>
              Overall score: {analysis.overall_score}/10
            </div>
            <p className="os-muted">{analysis.summary}</p>
            {analysis.suggestions.length > 0 && (
              <div>
                <div className="os-field-label">Suggestions{analysis.model ? ` from ${analysis.model}` : ""}</div>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                  {analysis.suggestions.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </div>
            )}
            {!analysis.ai && <div className="os-muted os-small">Ollama was not reachable, so this is the rules-based check alone — no model suggestions.</div>}
            <div className="os-analysis">
              {analysis.slides.map((s) => (
                <div key={s.index} className={`os-qa-row ${s.issues.length ? "" : "fixed"}`}>
                  <span className="os-qa-label">Slide {s.index + 1}</span>
                  <span className="os-qa-issue">{s.issues.length ? s.issues.join(" · ") : "Looks good for video."}</span>
                  <span className="os-mono os-small">{s.score}/10</span>
                </div>
              ))}
            </div>
          </>
        )}
      </Modal>
    </Card>
  );
}
