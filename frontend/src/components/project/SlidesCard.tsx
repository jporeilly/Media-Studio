import { type KeyboardEvent, useEffect, useId, useState } from "react";
import { type UseMutationResult, useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Download, Image as ImageIcon, Pencil, RotateCcw, Save, Undo2 } from "lucide-react";
import { api, errorMessage } from "../../api/client";
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
  parsePause,
  pauseText,
  renderHint,
  slideLabel,
  slidesQueryKey,
  slidesSubtitle,
  stepFromKey,
  useSlides,
  type Drafts,
  type Slide,
  type SlideProjectKind,
  type SlidesPayload,
} from "../../lib/slides";
import { useVoices, withCurrent } from "../../lib/studioSettings";
import { Button, Card, ErrorBox, Field, Input, Modal, Select, Spinner, Textarea } from "../ui";
import { JobProgress, type Job } from "./JobProgress";

interface Props {
  projectId: string;
  projectName: string;
  projectKind: SlideProjectKind;
  /** The narration provider chosen in the Generate card: a per-slide voice override picks from its voices. */
  provider: string;
  /** The page's one polled job; a render-slides job shows its progress here. */
  job?: Job;
  /** True while any job of this project is queued or running: the server refuses writes then (409), so the editor is read-only. */
  jobActive: boolean;
  onJobStarted: (jobId: string) => void;
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
 */
export function SlidesCard({ projectId, projectName, projectKind, provider, job, jobActive, onJobStarted }: Props) {
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

  const payload = query.data;
  const slides = payload?.slides ?? [];
  const slide: Slide | undefined = slides[clampIndex(selected, slides.length)];
  const rendering = job?.kind === RENDER_JOB_KIND && (job.status === "queued" || job.status === "running");
  const renderError = job?.kind === RENDER_JOB_KIND && job.status === "error" ? job.error || job.message : null;
  // Writes are refused while a job holds the project (it has its own copy of the slides).
  const busy = jobActive;
  const hint = renderHint(projectKind);

  const select = (index: number) => {
    setSelected(clampIndex(index, slides.length));
    setPauseDraft(null);
    setPauseError(null);
    setBodyOpen(false);
  };

  // ← / → move between slides unless the focus is in a field (or the reset dialog is open).
  const count = slides.length;
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (confirmReset) return;
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
  }, [count, confirmReset]);

  // The server's copy of one slide replaces the cached one (no refetch of the whole deck).
  const putSlide = (updated: Slide) => {
    qc.setQueryData<SlidesPayload>(slidesQueryKey(projectId), (old) =>
      old ? { ...old, slides: old.slides.map((s) => (s.index === updated.index ? updated : s)) } : old,
    );
  };
  const patchSlide = (index: number, body: Record<string, unknown>) => api.patch<Slide>(`/api/projects/${projectId}/slides/${index}`, body);

  const saveNotes = useMutation({
    mutationFn: (index: number) => patchSlide(index, { speaker_notes: drafts[index] ?? "" }),
    // Only a draft that now matches the saved text is settled: keystrokes typed while the save ran are kept.
    onSuccess: (updated) => {
      putSlide(updated);
      setDrafts((d) => dropSettled(d, [updated]));
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
    },
  });
  const reset = useMutation({
    mutationFn: (index: number) => api.post<Slide>(`/api/projects/${projectId}/slides/${index}/reset`, {}),
    onSuccess: (updated) => {
      putSlide(updated);
      setDrafts((d) => dropDrafts(d, [updated.index]));
      setConfirmReset(false);
    },
  });
  const saveAll = useMutation({
    mutationFn: () => api.patch<{ slides: Slide[] }>(`/api/projects/${projectId}/slides`, bulkBody(slides, drafts)),
    onSuccess: (r) => {
      qc.setQueryData<SlidesPayload>(slidesQueryKey(projectId), (old) => (old ? { ...old, slides: r.slides } : old));
      setDrafts((d) => dropSettled(d, r.slides));
    },
  });
  const render = useMutation({
    mutationFn: () => api.post<{ job_id?: string; cached?: boolean }>(`/api/projects/${projectId}/slides/render`, {}),
    onSuccess: (r) => {
      if (r.job_id) onJobStarted(r.job_id);
      else qc.invalidateQueries({ queryKey: slidesQueryKey(projectId) });
    },
  });

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
  const problem = pauseError ?? renderError ?? firstError([saveNotes, saveAll, undo, reset, saveOverride, render]);
  const notice = payload ? imagesNotice(payload.images_source, payload.slides_ready) : null;
  const voiceList = voices.data?.provider === provider ? voices.data.voices : [];
  const version = payload?.images_rendered_at;

  return (
    <Card
      title="Slides"
      subtitle={payload ? slidesSubtitle(slides) : undefined}
      style={{ marginTop: 16 }}
      actions={
        payload && slides.length > 0 ? (
          <>
            {!payload.slides_ready && !rendering && (
              <Button size="sm" icon={<ImageIcon size={15} />} disabled={render.isPending || busy} title={hint} onClick={() => render.mutate()}>
                Render slide previews
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
      {rendering && (
        <div style={{ marginBottom: 14 }}>
          <JobProgress job={job} />
        </div>
      )}
      {busy && <div className="os-muted os-small" style={{ marginBottom: 12 }}>{BUSY_NOTICE}</div>}
      {payload && !payload.slides_ready && !rendering && <div className="os-muted os-small" style={{ marginBottom: 12 }}>No previews yet. {hint}</div>}
      {notice && <div className="os-muted os-small" style={{ marginBottom: 12 }}>{notice}</div>}
      {voices.data?.notice && <div className="os-muted os-small" style={{ marginBottom: 12 }}>{voices.data.notice}</div>}

      {payload && slides.length === 0 && <div className="os-muted">This file has no slides.</div>}

      {payload && slide && (
        <div className="os-slides">
          <div className="os-slide-rail" role="listbox" aria-label="Slides">
            {slides.map((s) => {
              const dirtyRail = isDirty(s, drafts);
              const current = s.index === slide.index;
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
                {slide.notes_history_depth > 0 && (
                  <span className="os-dim os-xs">{slide.notes_history_depth} undo {slide.notes_history_depth === 1 ? "step" : "steps"}</span>
                )}
              </label>
              <Textarea
                id={notesId}
                rows={8}
                value={draftNotes(slide, drafts)}
                placeholder="No notes: this slide shows for a few seconds without narration."
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
              <span className="os-dim os-xs">← → move between slides · Ctrl+S saves</span>
            </div>

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
    </Card>
  );
}
