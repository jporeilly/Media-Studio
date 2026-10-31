/**
 * Workshop video - the one canvas (C1-C6): `/walkthroughs/workshop`, inside the Shell. Five stages across the top
 * (Workshop · Capture · Edit · Video · Publish), the current one open below, the others collapsed to one status line
 * each; the author clicks back to any earlier stage. ONE workshop at a time (several workshops of a course are
 * several runs; Import workshop…'s multi-pick stays on the Walkthroughs page). Works in the browser edition too; only
 * Capture needs the desktop app, and the stage says so.
 *
 * - **Workshop** (C2): the courses (the Courses folder setting, the Import dialog's loader) and the chosen course's
 *   workshops grouped by SUMMARY section, each with a status line from the records (`GET /api/courses/{course}/
 *   workshop-status`: the walkthrough imported from it, its project, its video, its Vimeo record, whether the guide
 *   holds the link). **Open** opens the walkthrough the workshop has (the most recent) or imports it now (all chapters,
 *   the default name) and moves to Capture.
 * - **Capture** (C3): the steps as a compact list (picture / no picture, the chapter), **Record…** and **Capture
 *   steps…** in the desktop app through the SAME guided capture the builder uses (`useGuidedCapture`, the bar, Done
 *   reloads the list), "N of M steps have a picture" and **Next: Edit**; in a browser the stage explains and offers Next.
 * - **Edit** (C4): the builder INLINE (`WalkthroughBuilder` with `embedded`: its three columns and toolbar; the stage
 *   buttons hidden there, since this page holds them) and its **Next: Video**, which saves first when dirty.
 * - **Video** (C5): Video to course's form inline (`VideoToCourseForm`: the prefilled fields and lines, the one
 *   button, the run-again form), the job's progress and Done line (`VideoToCourseStatus`, the job followed as the
 *   project page follows a render) and **Next: Publish**.
 * - **Publish** (C6): the Content Editor at the Studio setting's address (`GET /api/courses/publish/config`), the
 *   course's publish diff summarised, a commit message prefilled "Walkthrough video: <workshop title>", **Publish the
 *   course** through the Media Studio backend's thin proxy (`POST /api/courses/{course}/publish`, the editor commits
 *   and pushes - Media Studio runs no git), and the line afterwards. With the export setting off the stage explains
 *   that the link was not placed and points at the Video stage's lines.
 *
 * Pure web apart from Capture; the rules are `lib/workshopVideo.ts` and `lib/videoToCourse.ts`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Camera, Disc, Download, ImageOff, Undo2, Upload } from "lucide-react";
import { api, errorMessage } from "../api/client";
import type { Job } from "../components/project/JobProgress";
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner } from "../components/ui";
import { CaptureStepsDialog } from "../components/walkthrough/CaptureStepsDialog";
import { VideoToCourseForm } from "../components/walkthrough/VideoToCourseForm";
import { VideoToCourseStatus } from "../components/walkthrough/VideoToCourseStatus";
import { useGuidedCapture } from "../components/walkthrough/useGuidedCapture";
import { useAuth } from "../context/AuthContext";
import { type GuidedOptions, hintLine, nextLabel } from "../lib/guided";
import { useCaptureAvailable } from "../lib/useCaptureAvailable";
import { useFollowedJob } from "../lib/useFollowedJob";
import { VIDEO_TO_COURSE_JOB_KIND, type VideoToCourseDefaults, videoToCourseQueryKey, videoToCourseUrl } from "../lib/videoToCourse";
import { type Walkthrough, chapterCount, documentOf, hasPicture, walkthroughQueryKey, walkthroughsQueryKey } from "../lib/walkthrough";
import { type CoursesPayload, courseLabel, coursesQueryKey, workshopLabel } from "../lib/workshops";
import {
  type CanvasFacts, NEXT_LABELS, NOT_PLACED_PUBLISH_LINE, type PublishAnswer, type PublishConfig, type PublishDiff, type Published, STAGES, type Stage,
  type WorkshopStatusEntry, type WorkshopStatusPayload, canOpenStage, canPublish, capturedLine, editorLine, furthestOf, openLabel, publishConfigQueryKey,
  publishConfigUrl, publishCourseUrl, publishDiffQueryKey, publishDiffUrl, publishMessage, publishedLine, stageLine, workshopStatusLine, workshopStatusQueryKey,
  workshopStatusUrl,
} from "../lib/workshopVideo";
import { WalkthroughBuilder } from "./WalkthroughBuilder";

export const CAPTURE_BROWSER_LINE =
  "Capturing the steps needs the desktop app on the machine whose screen it is: open this page there to capture, or go on to Edit and add the pictures from stills.";

export default function WorkshopVideoPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const canCapture = useCaptureAvailable();
  const [stage, setStage] = useState<Stage>("workshop");
  const [furthest, setFurthest] = useState<Stage>("workshop");
  const [courseId, setCourseId] = useState("");
  const [slug, setSlug] = useState<string | null>(null);
  const [wid, setWid] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // What this session published, per workshop slug (the editor keeps no "last published" for a course).
  const [published, setPublished] = useState<Record<string, Published>>({});
  const [captureOpen, setCaptureOpen] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [vtcEnded, setVtcEnded] = useState<Job | null>(null);
  const [vtcProject, setVtcProject] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const go = useCallback((next: Stage) => {
    setStage(next);
    setFurthest((f) => furthestOf(f, next));
  }, []);

  // -- Workshop ------------------------------------------------------------------------------------------------
  const courses = useQuery({ queryKey: coursesQueryKey, queryFn: () => api.get<CoursesPayload>("/api/courses") });
  const list = useMemo(() => courses.data?.courses ?? [], [courses.data]);
  const course = list.find((c) => c.id === courseId) ?? null;
  useEffect(() => {
    if (!courseId && list.length) setCourseId(list[0].id);
  }, [courseId, list]);
  const status = useQuery({
    queryKey: workshopStatusQueryKey(courseId),
    queryFn: () => api.get<WorkshopStatusPayload>(workshopStatusUrl(courseId)),
    enabled: !!courseId,
    retry: false,
  });
  const entryOf = (s: string): WorkshopStatusEntry | null => status.data?.workshops[s] ?? null;
  const workshop = useMemo(() => course?.sections.flatMap((s) => s.workshops).find((w) => w.slug === slug) ?? null, [course, slug]);

  const refreshStatus = useCallback(() => {
    if (courseId) void qc.invalidateQueries({ queryKey: workshopStatusQueryKey(courseId) });
  }, [qc, courseId]);

  const open = useMutation({
    mutationFn: async (target: string) => {
      const entry = entryOf(target);
      if (entry?.walkthrough) return { id: entry.walkthrough.id, imported: false };
      const rec = await api.post<Walkthrough>("/api/walkthroughs/import", { course: courseId, slug: target });
      return { id: rec.id, imported: true, name: rec.name };
    },
    onSuccess: (r, target) => {
      setSlug(target);
      setWid(r.id);
      setVtcEnded(null);
      setVtcProject(null);
      setNotice(r.imported ? `Imported "${r.name}" from ${courseId}/${target}.` : null);
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey, exact: true });
      refreshStatus();
      setFurthest("capture");
      setStage("capture");
    },
    onError: (e) => setNotice(errorMessage(e)),
  });

  // -- the walkthrough (Capture, Edit) ---------------------------------------------------------------------------
  const record = useQuery({ queryKey: walkthroughQueryKey(wid ?? ""), queryFn: () => api.get<Walkthrough>(`/api/walkthroughs/${wid}`), enabled: !!wid, retry: false });
  const rec = record.data ?? null;
  const doc = rec ? documentOf(rec) : null;
  const pictured = doc ? doc.steps.filter(hasPicture).length : 0;
  const missing = doc ? doc.steps.length - pictured : 0;

  const guided = useGuidedCapture({
    wid: wid ?? "",
    document: () => (rec ? documentOf(rec) : null),
    onAttached: (r) => { qc.setQueryData(walkthroughQueryKey(r.id), r); },
    onFinished: () => {
      void record.refetch();
      refreshStatus();
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey, exact: true });
    },
  });
  const capturing = guided.phase !== "idle";
  const startCapture = useCallback(async (options: GuidedOptions) => {
    setCaptureOpen(false);
    setRecordOpen(false);
    await guided.start(options);
  }, [guided]);

  // -- Video (C5) --------------------------------------------------------------------------------------------------
  const vtcDefaults = useQuery({
    queryKey: videoToCourseQueryKey(wid ?? ""),
    queryFn: () => api.get<VideoToCourseDefaults>(videoToCourseUrl(wid ?? "")),
    enabled: !!wid,
    retry: false,
    staleTime: 0,
  });
  const projectId = vtcProject ?? vtcDefaults.data?.project?.id ?? null;
  // The form prefills from the walkthrough and the guide as they are NOW (the Edit stage may have renamed it, a job may
  // have run): entering the stage reads the defaults again; the form is keyed by the read, so it re-prefills.
  useEffect(() => {
    if (stage === "video" && wid) void qc.invalidateQueries({ queryKey: videoToCourseQueryKey(wid) });
  }, [stage, wid, qc]);
  const vtc = useFollowedJob({
    activeUrl: projectId ? `/api/projects/${projectId}/job` : null,
    viewer: user,
    onEnded: (job) => {
      if (job.kind !== VIDEO_TO_COURSE_JOB_KIND) return;
      setVtcEnded(job);
      if (wid) void qc.invalidateQueries({ queryKey: videoToCourseQueryKey(wid) });
      void qc.invalidateQueries({ queryKey: ["projects"] });
      refreshStatus();
    },
  });
  const vtcRunning = vtc.jobActive && vtc.activeKind === VIDEO_TO_COURSE_JOB_KIND;

  // -- Publish (C6) ------------------------------------------------------------------------------------------------
  const config = useQuery({ queryKey: publishConfigQueryKey, queryFn: () => api.get<PublishConfig>(publishConfigUrl), enabled: stage === "publish", retry: false, staleTime: 0 });
  const diff = useQuery({
    queryKey: publishDiffQueryKey(courseId),
    queryFn: () => api.get<PublishDiff>(publishDiffUrl(courseId)),
    enabled: stage === "publish" && !!courseId && !!config.data?.reachable,
    retry: false,
    staleTime: 0,
  });
  const publish = useMutation({
    mutationFn: () => api.post<PublishAnswer>(publishCourseUrl(courseId), { message: (message ?? publishMessage(workshop?.title ?? "")).trim() }),
    onSuccess: (r) => {
      if (slug) setPublished((p) => ({ ...p, [slug]: { when: new Date().toISOString(), summary: r.summary } }));
      void diff.refetch();
    },
  });
  const publishedHere = slug ? published[slug] ?? null : null;

  const facts: CanvasFacts = {
    courseTitle: course?.title ?? null,
    workshopTitle: workshop?.title ?? null,
    walkthroughName: rec?.name ?? null,
    stepCount: doc?.steps.length ?? 0,
    pictured,
    chapterCount: doc ? chapterCount(doc) : 0,
    project: vtcDefaults.data?.project ?? null,
    published: publishedHere,
    canCapture,
  };
  const allowed = vtcDefaults.data?.allowed ?? true;

  return (
    <>
      <PageHeader
        title="Workshop video"
        subtitle="Pick a workshop, capture it as you perform it, edit the steps, make the narrated video and publish the course - on one page."
        actions={<Link to="/walkthroughs" className="os-btn os-btn-secondary os-btn-sm" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><ArrowLeft size={14} /> Walkthroughs</Link>}
      />
      {notice && (
        <div className="os-card" role="status" aria-live="polite" style={{ padding: "10px 14px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }} data-canvas-notice="">
          <span>{notice}</span>
          <button className="os-icon-btn" onClick={() => setNotice(null)} title="Dismiss" aria-label="Dismiss">×</button>
        </div>
      )}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <ol className="ms-stages" data-stages="">
          {STAGES.map((s, i) => {
            const allowedStage = canOpenStage(s.key, furthest, !!wid);
            const state = s.key === stage ? "current" : allowedStage ? "reached" : "later";
            return (
              <li key={s.key} className={`ms-stage ms-stage-${state}`} data-stage={s.key} data-stage-state={state}>
                <button type="button" className="ms-stage-button" disabled={!allowedStage} aria-current={s.key === stage ? "step" : undefined} onClick={() => go(s.key)} data-stage-open={s.key}>
                  <span className="ms-stage-number">{i + 1}</span>
                  <span className="ms-stage-label">{s.label}</span>
                </button>
                {s.key !== stage && <span className="ms-stage-line os-muted os-small" data-stage-line={s.key}>{stageLine(s.key, facts)}</span>}
              </li>
            );
          })}
        </ol>

        <div style={{ padding: 14, display: "grid", gap: 12 }} data-stage-body={stage}>
          {stage === "workshop" && (
            <>
              <p style={{ margin: 0 }}>One workshop at a time: pick it and press Open. The walkthrough it already has is opened; a workshop without one is imported now (every chapter, the default name). Import workshop… on the Walkthroughs page takes several workshops at once.</p>
              {courses.isLoading && <Spinner label="Loading the courses…" />}
              {courses.isError && <ErrorBox message={errorMessage(courses.error)} />}
              {courses.data && !courses.data.configured && <p className="os-muted" data-canvas-unset="">{courses.data.message || "Set the Courses folder on the Settings page first."}</p>}
              {list.length > 0 && (
                <Field label="Course">
                  <Select value={courseId} onChange={(e) => { setCourseId(e.target.value); }} aria-label="Course" data-canvas-course="">
                    {list.map((c) => <option key={c.id} value={c.id}>{courseLabel(c)}</option>)}
                  </Select>
                </Field>
              )}
              {status.isLoading && <Spinner label="Reading the records…" />}
              {status.isError && <ErrorBox message={errorMessage(status.error)} />}
              {course && (
                <div style={{ display: "grid", gap: 10 }} data-canvas-workshops="">
                  {course.sections.map((section) => (
                    <div key={section.title || "(workshops)"} style={{ display: "grid", gap: 6 }}>
                      <strong>{section.title || "Workshops"}</strong>
                      {section.workshops.map((w) => {
                        const entry = entryOf(w.slug);
                        return (
                          <div key={w.slug} className="os-row" style={{ gap: 10, alignItems: "center", paddingLeft: 12 }} data-canvas-workshop={w.slug}>
                            <div style={{ flex: 1, minWidth: 0, display: "grid" }}>
                              <span>{workshopLabel(w)}</span>
                              <span className="os-muted os-small" data-canvas-status="">{workshopStatusLine(entry, published[w.slug])}</span>
                            </div>
                            <Button size="sm" variant="primary" icon={entry?.walkthrough ? <ArrowRight size={14} /> : <Download size={14} />} disabled={open.isPending || status.isLoading} onClick={() => open.mutate(w.slug)} data-canvas-open={w.slug}>
                              {open.isPending && open.variables === w.slug ? "Opening…" : openLabel(entry)}
                            </Button>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {stage === "capture" && doc && (
            <>
              <p style={{ margin: 0 }}>
                {canCapture
                  ? "Capture the steps as you perform them in the application: Capture steps… takes a still per step with a hotspot where you click; Record… makes new steps from your clicks. Done brings the list back up to date."
                  : CAPTURE_BROWSER_LINE}
              </p>
              <div className="os-row" style={{ gap: 8, flexWrap: "wrap" }}>
                {canCapture && (
                  <Button size="sm" icon={<Camera size={14} />} onClick={() => setCaptureOpen(true)} disabled={doc.steps.length === 0 || capturing} data-canvas-capture-steps="">Capture steps…</Button>
                )}
                {canCapture && (
                  <Button size="sm" icon={<Disc size={14} />} onClick={() => setRecordOpen(true)} disabled={capturing} data-canvas-record="">Record…</Button>
                )}
                <span className="os-muted" data-canvas-captured="">{capturedLine(pictured, doc.steps.length)}</span>
                <span style={{ flex: 1 }} />
                <Button size="sm" variant="primary" icon={<ArrowRight size={14} />} onClick={() => go("edit")} disabled={capturing} data-canvas-next="edit">{NEXT_LABELS.capture}</Button>
              </div>
              {(capturing || guided.message) && (
                <div className="ms-capture-line" role="status" aria-live="polite" data-capture-line="">
                  {capturing && guided.bar && (
                    <>
                      <Camera size={14} />
                      <strong data-capture-label="">{guided.bar.label}</strong>
                      <span className="os-truncate" style={{ flex: 1 }}>{guided.bar.text}</span>
                      <span className="os-muted">{guided.phase === "finished" ? "All steps visited." : `${hintLine(guided.bar.clicks, guided.bar.typing) || "Shift+F8 captures"}; the bar has Capture, ${guided.bar.record ? "Undo and Done" : "Next, Back and Done"}.`}</span>
                      {guided.bar.record
                        ? <Button size="sm" icon={<Undo2 size={14} />} onClick={() => void guided.undo()} disabled={!guided.bar.canUndo} title="Take the last recorded step back" data-capture-undo="">Undo</Button>
                        : <Button size="sm" onClick={guided.next} disabled={guided.phase !== "ready" && guided.phase !== "capturing"} title="Move on to the next step (Shift+F9)" data-capture-next="">{guided.session ? nextLabel(guided.session) : "Next"}</Button>}
                      <Button size="sm" onClick={() => void guided.done()} disabled={guided.phase === "capturing"} data-capture-done="">Done</Button>
                    </>
                  )}
                  {guided.message && (
                    <>
                      <span style={{ color: "var(--bad)" }} data-capture-message="">{guided.message}</span>
                      <button className="os-icon-btn" onClick={guided.dismiss} title="Dismiss" aria-label="Dismiss">×</button>
                    </>
                  )}
                </div>
              )}
              <ol className="ms-canvas-steps" data-canvas-steps="">
                {doc.steps.map((s, i) => (
                  <li key={s.id} data-canvas-step={s.id} data-has-picture={hasPicture(s) ? "yes" : "no"}>
                    {hasPicture(s) ? <Camera size={13} aria-label="has a picture" /> : <ImageOff size={13} className="os-dim" aria-label="no picture yet" />}
                    <span className="os-muted os-small">{i + 1}.</span>
                    {s.chapter.trim() && <span className="os-chip os-chip-dense" style={{ background: "var(--brand)" }}>{s.chapter}</span>}
                    <span className="os-truncate">{s.text || s.instruction || "(no text yet)"}</span>
                  </li>
                ))}
                {doc.steps.length === 0 && <li className="os-muted">No steps yet: Record… makes them from your clicks (the desktop app), or add them in Edit.</li>}
              </ol>
              {canCapture && (
                <>
                  <CaptureStepsDialog open={captureOpen} missing={missing} total={doc.steps.length} onClose={() => setCaptureOpen(false)} onStart={(o) => void startCapture(o)} />
                  <CaptureStepsDialog open={recordOpen} mode="record" missing={missing} total={doc.steps.length} onClose={() => setRecordOpen(false)} onStart={(o) => void startCapture(o)} />
                </>
              )}
            </>
          )}

          {stage === "edit" && wid && (
            <WalkthroughBuilder id={wid} embedded={{ nextLabel: NEXT_LABELS.edit, onNext: () => go("video") }} />
          )}

          {stage === "video" && wid && doc && (
            <>
              {vtcDefaults.isLoading && <Spinner label="Reading the guide…" />}
              {vtcDefaults.isError && <ErrorBox message={errorMessage(vtcDefaults.error)} />}
              {vtcDefaults.data && (
                <VideoToCourseForm
                  key={`${wid}:${vtcDefaults.dataUpdatedAt}`}
                  wid={wid}
                  doc={doc}
                  defaults={vtcDefaults.data}
                  busy={vtcRunning}
                  onStarted={(r) => { setVtcEnded(null); setVtcProject(r.project_id); vtc.follow(r.job_id, VIDEO_TO_COURSE_JOB_KIND); }}
                />
              )}
              <VideoToCourseStatus
                job={vtc.job}
                jobActive={vtc.jobActive}
                activeKind={vtc.activeKind}
                mayCancel={vtc.mayCancel}
                onCancel={vtc.cancel}
                cancelPending={vtc.cancelPending}
                cancelError={vtc.cancelError ? errorMessage(vtc.cancelError) : null}
                ended={vtcEnded}
                onDismiss={() => setVtcEnded(null)}
                projectId={projectId}
              />
              <div className="os-row" style={{ justifyContent: "flex-end" }}>
                <Button size="sm" variant="primary" icon={<ArrowRight size={14} />} onClick={() => go("publish")} disabled={vtcRunning} data-canvas-next="publish">{NEXT_LABELS.video}</Button>
              </div>
            </>
          )}

          {stage === "publish" && (
            <>
              <p style={{ margin: 0 }}>The course is published through the Content Editor's own publish: it copies the course into its clone of the courses repository, commits and pushes, and commits the authoring repository too. Media Studio runs no git itself.</p>
              {config.isLoading && <Spinner label="Asking the Content Editor…" />}
              {config.isError && <ErrorBox message={errorMessage(config.error)} />}
              {config.data && <p className={config.data.reachable ? "os-muted os-small" : ""} style={{ margin: 0, color: config.data.reachable ? undefined : "var(--bad)" }} data-canvas-editor="">{editorLine(config.data)}</p>}
              {!allowed && <p style={{ margin: 0 }} data-canvas-not-placed="">{NOT_PLACED_PUBLISH_LINE}</p>}
              {diff.isLoading && <Spinner label="Reading what would change…" />}
              {diff.isError && <ErrorBox message={errorMessage(diff.error)} />}
              {diff.data && <p style={{ margin: 0 }} data-canvas-diff="">{diff.data.summary}</p>}
              <Field label="Commit message">
                <Input value={message ?? publishMessage(workshop?.title ?? "")} maxLength={500} onChange={(e) => setMessage(e.target.value)} data-canvas-message="" />
              </Field>
              <div className="os-row" style={{ gap: 8 }}>
                <Button variant="primary" icon={<Upload size={16} />} disabled={!canPublish(config.data, courseId, publish.isPending) || (diff.data?.upToDate ?? false)} onClick={() => publish.mutate()} title={diff.data?.upToDate ? "Nothing to publish: the repository already holds the course as it is." : undefined} data-canvas-publish="">
                  {publish.isPending ? "Publishing…" : "Publish the course"}
                </Button>
                {config.data && !config.data.reachable && <Button size="sm" onClick={() => void config.refetch()} data-canvas-editor-again="">Ask again</Button>}
              </div>
              {publish.isError && <ErrorBox message={errorMessage(publish.error)} />}
              {publishedHere && <p style={{ margin: 0, color: "var(--good)", fontWeight: 500 }} role="status" data-canvas-published="">{publishedLine(publishedHere)}</p>}
            </>
          )}
        </div>
      </Card>
    </>
  );
}
