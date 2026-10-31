/**
 * The walkthrough builder (W1): `/walkthroughs/:id`, inside the Shell. A step list on the left (thumbnails,
 * numbers, the chapter a step starts; select, move up / down, drag to reorder, delete with a confirm), the
 * selected step's picture in the middle with its hotspot (drag to draw, move, resize by the handles; Remove
 * hotspot), the step's properties on the right (Tooltip / Modal, the text, the instruction, the placement,
 * the chapter, Replace picture…). Add step… opens a picker of the caller's stills and inserts after the
 * selected step. A Transcript toggle beside Play shows the walkthrough as Markdown (what is said at each step:
 * the instruction, else the text), with Copy and Save as… - the narration script of the narrated video to come.
 *
 * The document in hand is the name and the steps; the revision the record was loaded at is kept beside it.
 * Save (Ctrl+S) writes the WHOLE document with that revision (`PUT /api/walkthroughs/{wid}`); a 409 - somebody
 * saved in between - is shown as the server's own line with a Reload. Undo / Redo (Ctrl+Z / Ctrl+Y) run over
 * the whole document through the one history (`lib/history.ts`, made with `sameDocument` - which compares
 * documents AS THE SERVER STORES THEM, so a trailing space in a chapter is no difference); a text being typed
 * and a hotspot being dragged are PREVIEWS held apart from the history until the box is left or the pointer
 * is up, so undo never lands on half an edit. After a successful save the history is pruned of every document
 * that names a picture the save swept away (an added step undone before the save): such a document could never
 * be saved again, so undo and redo no longer reach it. Add step and Replace picture are server calls (the
 * picture is copied there, through the oracle): the record they answer becomes the saved baseline, and the
 * local document takes the new step at the selected place (or the new picture, the local hotspot clamped),
 * so unsaved edits elsewhere are kept. Play opens the player at the selected step, asking to save first when
 * unsaved - the player shows what was saved. Leaving with unsaved changes asks (`useLeaveGuard`).
 *
 * ONE status derivation: "Saving…" while a save runs; else an event line ("Nothing to save.", the 409 line,
 * "Reloaded", a failure) until the next change; else "Unsaved changes" while the document differs from the
 * saved record; else "Saved" once a load or a save has happened.
 *
 * W2: a step without a picture yet (one imported from a course workshop) shows a placeholder on the stage and a
 * mark in the list; **Capture steps…** (desktop app only, behind `useCaptureAvailable` like the Capture tile)
 * saves first, then runs the guided capture (`useGuidedCapture`): the shell's capture bar follows the steps, each
 * click, typed pause or Shift+F8 takes a still, attaches it and places the hotspot round the click or the pointer,
 * Next (the capture line's button, the bar's, Shift+F9) moves on (W2d), and Done reloads the builder from the
 * server - every record the server answers during the capture is the saved baseline, so nothing is "unsaved".
 * A fresh import opens with a notice on the status line ("Imported …: 12 steps in 4 chapters; …").
 *
 * W2c: **Export to course…** (saving first, asked as Play asks) opens `ExportCourseDialog`: the preview of the
 * figures the walkthrough's pictures become in its workshop's guide, the write (the studio setting's), the zip, and
 * the interactive player as a download.
 *
 * W3: **Make a video…** (saving first, asked the same way) opens `MakeVideoDialog`: the walkthrough becomes a project
 * whose Generate card renders it as a deck is rendered - each step a slide - and the builder opens that project.
 *
 * F1: **Record…** (desktop app only, beside Capture steps…, and on the empty walkthrough's card) runs the guided capture
 * in its record mode: every click, typed pause or Shift+F8 in the application MAKES a step - appended after the last,
 * with its still, a beacon round the point, the click kind and "Click here" - through the add-step route; the bar's
 * Undo (or the capture line's) takes the last one back; Done reloads the builder with the first new step selected.
 * F2: Add step… offers **Take a still now…** (the Capture dialog's still tab, from here; the still becomes a step with
 * no hotspot), and a step without a hotspot has **Add hotspot** (a 160 × 60 hotspot centred on the picture). F3, F4: the
 * step panel's **Click** (Left · Right · Double · None) and **Marker** (Beacon · Box).
 *
 * R1-R4: **Video to course…** beside Make a video… (saving first, asked the same way) opens `VideoToCourseDialog`: one
 * button renders the video, publishes it to Vimeo and places the link in the course workshop's guide as ONE job, which
 * the builder then follows under its toolbar (`useFollowedJob`, as the project page follows a render) with Cancel, and
 * the Done line with Open the video and Copy the link (`VideoToCourseStatus`). The three steps stay as they are.
 *
 * C4: the builder is ONE component (`WalkthroughBuilder`, this page's body) that the Workshop video canvas renders
 * INLINE in its Edit stage (`embedded`): the three columns and the toolbar as they are - Save, Undo, Redo, Play,
 * Transcript - with Export to course…, Make a video…, Video to course…, Capture steps… and Record… hidden there, since
 * the canvas's stages hold them (two paths to one job on one page would show its progress twice), the page header
 * left to the canvas, and a **Next** button that saves first when the document is dirty (asked as Play asks).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Camera, Clapperboard, Copy, Crosshair, Disc, Download, FileText, Film, GripVertical, ImageOff, Play, Plus, Redo2, Replace, Save, Share, Trash2, Undo2, X } from "lucide-react";
import { ApiError, api, errorMessage } from "../api/client";
import { useConfirm } from "../components/ConfirmDialog";
import type { Job } from "../components/project/JobProgress";
import { Button, Card, EmptyState, ErrorBox, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { useLeaveGuard } from "../components/annotate/useLeaveGuard";
import { CaptureDialog } from "../components/capture/CaptureDialog";
import { CaptureStepsDialog } from "../components/walkthrough/CaptureStepsDialog";
import { ExportCourseDialog } from "../components/walkthrough/ExportCourseDialog";
import { HotspotSurface } from "../components/walkthrough/HotspotSurface";
import { MakeVideoDialog } from "../components/walkthrough/MakeVideoDialog";
import { StillPicker } from "../components/walkthrough/StillPicker";
import { VideoToCourseDialog } from "../components/walkthrough/VideoToCourseDialog";
import { VideoToCourseStatus } from "../components/walkthrough/VideoToCourseStatus";
import { useGuidedCapture } from "../components/walkthrough/useGuidedCapture";
import { useAuth } from "../context/AuthContext";
import { fitZoom } from "../lib/annotate";
import type { Capture } from "../lib/capture";
import { SAVE_BEFORE_EXPORT, exportTitle } from "../lib/courseExport";
import { copyText, downloadText } from "../lib/files";
import { type GuidedOptions, capturedSteps, hintLine, nextLabel } from "../lib/guided";
import { type History, canRedo, canUndo, commit, history, prune, redo, undo } from "../lib/history";
import { useCaptureAvailable } from "../lib/useCaptureAvailable";
import { useFollowedJob } from "../lib/useFollowedJob";
import { SAVE_BEFORE_VIDEO_TO_COURSE, VIDEO_TO_COURSE_JOB_KIND, type VideoToCourseDefaults, videoToCourseQueryKey, videoToCourseUrl } from "../lib/videoToCourse";
import { SAVE_BEFORE_NEXT } from "../lib/workshopVideo";
import {
  type ClickKind, type Marker, type Placement, type Step, type StepKind, type Walkthrough, type WalkthroughDocument, CLICKS, CLICK_LABELS, EMPTY_HOW,
  GONE_MESSAGE, MARKERS, MARKER_LABELS, MAX_CHAPTER_CHARS, MAX_INSTRUCTION_CHARS, MAX_NAME_CHARS, MAX_STEP_TEXT, NO_PICTURE_HOW, NO_PICTURE_TITLE,
  PLACEMENTS, PLACEMENT_LABELS, RELOAD_ASK, SAVE_BEFORE_PLAY, SAVE_BEFORE_VIDEO, TOO_MANY_STEPS_MESSAGE, UNSAVED_ASK, canAddStep, centredHotspot,
  clampHotspot, deleteStep, documentOf, hasPicture, imagesOf, insertStep, moveStep, namesOnly, playUrl, putBody, reorderStep, sameDocument, setHotspot,
  stepById, stepImageUrl, stepIndex, stepsToCapture, transcript, transcriptFileName, typingIn, updateStep, walkthroughQueryKey, walkthroughsQueryKey,
} from "../lib/walkthrough";

/** What the status line says until the next change: an event, or a failure (drawn in the warning colour). */
interface Event { text: string; bad: boolean }

/** The canvas's Edit stage (C4): the builder inline, with a Next button that saves first and no header or stage buttons. */
export interface EmbeddedBuilder {
  nextLabel: string;
  onNext: () => void;
}

export default function WalkthroughBuilderPage() {
  const { id = "" } = useParams();
  return <WalkthroughBuilder id={id} />;
}

export function WalkthroughBuilder({ id, embedded }: { id: string; embedded?: EmbeddedBuilder }) {
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const { user } = useAuth();
  const viewportRef = useRef<HTMLDivElement>(null);
  const canCapture = useCaptureAvailable();
  const inline = !!embedded;

  const record = useQuery({ queryKey: walkthroughQueryKey(id), queryFn: () => api.get<Walkthrough>(`/api/walkthroughs/${id}`), retry: false });
  const rec = record.data ?? null;

  const [hist, setHist] = useState<History<WalkthroughDocument> | null>(null);
  const [preview, setPreview] = useState<WalkthroughDocument | null>(null);
  const [saved, setSaved] = useState<WalkthroughDocument | null>(null);
  const [revision, setRevision] = useState(0);
  const [settled, setSettled] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [event, setEvent] = useState<Event | null>(null);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [picker, setPicker] = useState<"add" | "replace" | null>(null);
  const [picking, setPicking] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [takeStillOpen, setTakeStillOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [videoOpen, setVideoOpen] = useState(false);
  const [vtcOpen, setVtcOpen] = useState(false);
  // Video to course (R4): the project the chain runs on (to adopt a running job after a reload) and the job as it ended.
  const [vtcProject, setVtcProject] = useState<string | null>(null);
  const [vtcEnded, setVtcEnded] = useState<Job | null>(null);
  const vtcDefaults = useQuery({
    queryKey: videoToCourseQueryKey(id), queryFn: () => api.get<VideoToCourseDefaults>(videoToCourseUrl(id)), retry: false, staleTime: 30_000, enabled: !inline,
  });
  const vtcProjectId = vtcProject ?? vtcDefaults.data?.project?.id ?? null;
  const vtc = useFollowedJob({
    activeUrl: !inline && vtcProjectId ? `/api/projects/${vtcProjectId}/job` : null,
    viewer: user,
    onEnded: (job) => {
      if (job.kind !== VIDEO_TO_COURSE_JOB_KIND) return;
      setVtcEnded(job);
      void qc.invalidateQueries({ queryKey: videoToCourseQueryKey(id) });
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const vtcRunning = vtc.jobActive && vtc.activeKind === VIDEO_TO_COURSE_JOB_KIND;

  // The document and its history from the record once (and again on Reload); the saved baseline beside it.
  const load = useCallback((r: Walkthrough) => {
    const doc = documentOf(r);
    setHist(history(doc, sameDocument));
    setPreview(null);
    setSaved(doc);
    setRevision(r.revision);
    setSettled(true);
    setSelectedId((current) => (current && doc.steps.some((s) => s.id === current) ? current : doc.steps[0]?.id ?? null));
    setStale(false);
  }, []);
  useEffect(() => { if (rec && !hist) load(rec); }, [rec, hist, load]);

  // A notice carried in by the route (a fresh import): on the status line until the next change, said once.
  const noticeFromRoute = (location.state as { notice?: string } | null)?.notice;
  useEffect(() => {
    if (!noticeFromRoute) return;
    setEvent({ text: noticeFromRoute, bad: false });
    navigate(location.pathname, { replace: true, state: null });
  }, [noticeFromRoute, navigate, location.pathname]);

  const doc = preview ?? hist?.present ?? null;
  const dirty = !!doc && !!saved && !sameDocument(doc, saved);
  const selected = doc ? stepById(doc, selectedId) : null;
  const selectedIndex = doc ? stepIndex(doc, selectedId) : -1;
  const docRef = useRef<WalkthroughDocument | null>(null);
  docRef.current = doc;

  // The guided capture (W2): each attached picture makes the server's record the saved baseline; Done reloads.
  const guided = useGuidedCapture({
    wid: id,
    document: () => docRef.current,
    onAttached: (r) => { qc.setQueryData(walkthroughQueryKey(id), r); load(r); },
    onFinished: (s) => {
      void record.refetch().then((res) => {
        if (res.data) load(res.data);
        if (s.record) {
          // Record (F1): the steps made and kept, the first of them selected.
          const made = s.made.length;
          if (made) setSelectedId(s.made[0].id);
          setEvent({ text: `Recorded ${made} step${made === 1 ? "" : "s"}.`, bad: false });
          return;
        }
        // The distinct steps that got a picture (a retake after Back is not a second step), and the skips.
        const taken = capturedSteps(s);
        setEvent({ text: `Captured ${taken} step${taken === 1 ? "" : "s"}${s.skipped ? `, skipped ${s.skipped}` : ""}.`, bad: false });
      });
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey, exact: true });
    },
  });

  // Every change of the document ends the event line.
  const change = useCallback((next: WalkthroughDocument, commitIt: boolean) => {
    setEvent(null);
    if (!commitIt) { setPreview(next); return; }
    setPreview(null);
    setHist((h) => (h ? commit(h, next) : h));
  }, []);
  const undoOne = useCallback(() => { setEvent(null); setPreview(null); setHist((h) => (h ? undo(h) : h)); }, []);
  const redoOne = useCallback(() => { setEvent(null); setPreview(null); setHist((h) => (h ? redo(h) : h)); }, []);
  const edit = (patch: Partial<Step>, commitIt = true) => {
    if (!doc || !selected) return;
    change(updateStep(doc, selected.id, patch), commitIt);
  };

  // The picture fitted to the middle column: when the step (or its picture) changes, and when the column's
  // size does (a window resized, the Shell's sidebar folded).
  const selectedWidth = selected?.width ?? 0, selectedHeight = selected?.height ?? 0;
  useEffect(() => {
    if (!selectedWidth || !selectedHeight) return;
    const el = viewportRef.current;
    const fit = () => setZoom(fitZoom({ width: el?.clientWidth ?? 0, height: el?.clientHeight ?? 0 }, { width: selectedWidth, height: selectedHeight }));
    fit();
    if (typeof ResizeObserver === "undefined" || !el) {
      window.addEventListener("resize", fit);
      return () => window.removeEventListener("resize", fit);
    }
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, [selectedId, selectedWidth, selectedHeight]);

  // Leaving with unsaved changes asks first; `release` takes the guards down for a navigation that must not ask.
  const ask = useCallback(() => confirm({ ...UNSAVED_ASK, danger: true }), [confirm]);
  const release = useLeaveGuard(dirty, ask);
  const gone = useCallback(() => {
    release();
    navigate("/walkthroughs", { replace: true, state: { notice: GONE_MESSAGE } });
  }, [navigate, release]);
  useEffect(() => {
    const e = record.error;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) gone();
  }, [record.error, gone]);

  const save = useCallback(async (): Promise<boolean> => {
    if (!doc || saving) return false;
    if (!dirty) { setEvent({ text: "Nothing to save.", bad: false }); return true; }
    // A text still being typed is committed first: undo lands on whole edits, and the save sends what is shown.
    const current = doc;
    setPreview(null);
    setHist((h) => (h ? commit(h, current) : h));
    setSaving(true);
    setEvent(null);
    try {
      const res = await api.put<Walkthrough>(`/api/walkthroughs/${id}`, putBody(current, revision));
      qc.setQueryData(walkthroughQueryKey(id), res);
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey, exact: true });
      setSaved(documentOf(res));
      setRevision(res.revision);
      setStale(false);
      setSettled(true);
      // The save swept the pictures no step names any more: a document naming one could never be saved again.
      const held = imagesOf(res);
      setHist((h) => (h ? prune(h, (d) => namesOnly(d, held)) : h));
      return true;
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) { gone(); return false; }
      if (e instanceof ApiError && e.status === 409) { setStale(true); setEvent({ text: errorMessage(e), bad: true }); return false; }
      setEvent({ text: `Could not save: ${errorMessage(e)}`, bad: true });
      return false;
    } finally {
      setSaving(false);
    }
  }, [doc, saving, dirty, id, revision, qc, gone]);

  const reload = useCallback(async () => {
    if (dirty && !(await confirm({ ...RELOAD_ASK, danger: true }))) return;
    const res = await record.refetch();
    if (res.data) { load(res.data); setEvent({ text: "Reloaded", bad: false }); }
  }, [dirty, confirm, record, load]);

  const failed = useCallback((what: string, e: unknown) => {
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) { gone(); return; }
    setEvent({ text: `${what}: ${errorMessage(e)}`, bad: true });
  }, [gone]);

  const addStep = useCallback(async (c: Capture) => {
    if (!doc || !saved) return;
    if (!canAddStep(doc)) { setEvent({ text: TOO_MANY_STEPS_MESSAGE, bad: true }); setPicker(null); return; }
    setPicking(true);
    try {
      const after = selected?.id ?? null;
      const res = await api.post<Walkthrough>(`/api/walkthroughs/${id}/steps`, after ? { capture_id: c.id, after } : { capture_id: c.id });
      // The new step is the one neither the saved record nor the local document knew.
      const known = new Set([...saved.steps, ...doc.steps].map((s) => s.id));
      const added = res.steps.find((s) => !known.has(s.id)) ?? null;
      setSaved(documentOf(res));
      setRevision(res.revision);
      if (added) {
        change(insertStep(doc, added, after), true);
        setSelectedId(added.id);
      }
      setPicker(null);
    } catch (e) {
      failed("Could not add the step", e);
    } finally {
      setPicking(false);
    }
  }, [doc, saved, selected?.id, id, change, failed]);

  const replacePicture = useCallback(async (c: Capture) => {
    if (!doc || !selected) return;
    setPicking(true);
    try {
      const res = await api.post<Walkthrough>(`/api/walkthroughs/${id}/steps/${selected.id}/picture`, { capture_id: c.id });
      const theirs = res.steps.find((s) => s.id === selected.id) ?? null;
      setSaved(documentOf(res));
      setRevision(res.revision);
      if (theirs) {
        change(updateStep(doc, selected.id, (s) => ({
          ...s, image: theirs.image, width: theirs.width, height: theirs.height, source: theirs.source,
          hotspot: clampHotspot(s.hotspot, { width: theirs.width, height: theirs.height }),
        })), true);
      }
      setPicker(null);
    } catch (e) {
      failed("Could not replace the picture", e);
    } finally {
      setPicking(false);
    }
  }, [doc, selected, id, change, failed]);

  const removeStep = useCallback(async (sid: string) => {
    if (!doc) return;
    const i = stepIndex(doc, sid);
    if (i < 0) return;
    const ok = await confirm({
      title: "Delete step",
      message: <>Delete step {i + 1}? Its picture leaves the walkthrough when you save; the still it was made from is kept.</>,
      confirmLabel: "Delete", danger: true,
    });
    if (!ok) return;
    const next = deleteStep(doc, sid);
    change(next, true);
    if (selectedId === sid) setSelectedId(next.steps[Math.min(i, next.steps.length - 1)]?.id ?? null);
  }, [doc, confirm, change, selectedId]);

  const play = useCallback(async () => {
    if (!doc || doc.steps.length === 0) return;
    const at = Math.max(0, selectedIndex);
    if (dirty) {
      if (!(await confirm({ ...SAVE_BEFORE_PLAY }))) return;
      if (!(await save())) return;
    }
    release();
    navigate(playUrl(id, at), { state: { from: "builder", step: at } });
  }, [doc, selectedIndex, dirty, confirm, save, release, navigate, id]);

  /** Export to course (W2c): it reads the SAVED walkthrough, so unsaved changes are saved first, asked as Play asks. */
  const openExport = useCallback(async () => {
    if (dirty) {
      if (!(await confirm({ ...SAVE_BEFORE_EXPORT }))) return;
      if (!(await save())) return;
    }
    setExportOpen(true);
  }, [dirty, confirm, save]);

  /** Make a video (W3): the video is made from the SAVED walkthrough, so unsaved changes are saved first, asked as Play asks. */
  const openVideo = useCallback(async () => {
    if (dirty) {
      if (!(await confirm({ ...SAVE_BEFORE_VIDEO }))) return;
      if (!(await save())) return;
    }
    setVideoOpen(true);
  }, [dirty, confirm, save]);

  /** Video to course (R1): the same - the one job reads the walkthrough as it is saved. */
  const openVideoToCourse = useCallback(async () => {
    if (dirty) {
      if (!(await confirm({ ...SAVE_BEFORE_VIDEO_TO_COURSE }))) return;
      if (!(await save())) return;
    }
    setVtcOpen(true);
  }, [dirty, confirm, save]);

  /** The canvas's Next (C4): saves first when dirty, asked as Play asks, then hands over to the stage after. */
  const goNext = useCallback(async () => {
    if (!embedded) return;
    if (dirty) {
      if (!(await confirm({ ...SAVE_BEFORE_NEXT }))) return;
      if (!(await save())) return;
    }
    embedded.onNext();
  }, [embedded, dirty, confirm, save]);

  /** Capture steps, and Record (F1): the builder saves first (as Play does), then the guided capture starts. */
  const startCapture = useCallback(async (options: GuidedOptions) => {
    setCaptureOpen(false);
    setRecordOpen(false);
    if (!doc) return;
    if (dirty && !(await save())) return;
    setEvent(null);
    await guided.start(options);
  }, [doc, dirty, save, guided]);

  /** Take a still now (F2): the picker closes, the Capture dialog's still tab opens; the still taken becomes a step. */
  const takeStill = useCallback(() => {
    setPicker(null);
    setTakeStillOpen(true);
  }, []);

  // The keys: Ctrl+S saves from anywhere (a text being typed included); undo / redo not while typing in a box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && key === "s") { e.preventDefault(); void save(); return; }
      if (typingIn(e.target)) return;
      if ((e.ctrlKey || e.metaKey) && key === "z" && !e.shiftKey) { e.preventDefault(); undoOne(); return; }
      if ((e.ctrlKey || e.metaKey) && (key === "y" || (key === "z" && e.shiftKey))) { e.preventDefault(); redoOne(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, undoOne, redoOne]);

  const statusText = saving ? "Saving…" : event?.text ?? (dirty ? "Unsaved changes" : settled ? "Saved" : "");
  const capturing = guided.phase !== "idle";
  const missingCount = doc ? stepsToCapture(doc, false).length : 0;

  if (record.isLoading) return <Spinner label="Loading the walkthrough…" />;
  if (record.isError) {
    const e = record.error;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return <Spinner label="Back to Walkthroughs…" />;
    return <ErrorBox message={errorMessage(e)} />;
  }
  if (!rec || !doc) return null;

  const commitPreview = () => change(doc, true);
  const hotspot = selected?.hotspot ?? null;
  const markdown = transcript(doc);
  const copyTranscript = async () => {
    setCopied((await copyText(markdown)) ? "Copied to the clipboard." : "Could not copy: the page may not write to the clipboard.");
  };

  return (
    <>
      {!inline && (
        <PageHeader
          title={doc.name.trim() || rec.name}
          subtitle="Add stills as steps, draw a hotspot on each, write what the tooltip or the card says and what the learner does, name the chapters, then Save and Play."
          actions={<Link to="/walkthroughs" className="os-btn os-btn-secondary os-btn-sm" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><ArrowLeft size={14} /> Walkthroughs</Link>}
        />
      )}
      {dialog}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <div className="os-row" style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", gap: 8 }} data-builder-toolbar="">
          <Input aria-label="Name" value={doc.name} maxLength={MAX_NAME_CHARS} onChange={(e) => change({ ...doc, name: e.target.value }, false)} onBlur={commitPreview} style={{ width: 260 }} data-walkthrough-name="" />
          <Button size="sm" icon={<Undo2 size={14} />} onClick={undoOne} disabled={!hist || !canUndo(hist)} title="Undo (Ctrl+Z)">Undo</Button>
          <Button size="sm" icon={<Redo2 size={14} />} onClick={redoOne} disabled={!hist || !canRedo(hist)} title="Redo (Ctrl+Y)">Redo</Button>
          <Button size="sm" variant="primary" icon={<Save size={14} />} onClick={() => void save()} disabled={!dirty || saving} title="Save (Ctrl+S)">Save</Button>
          <Button size="sm" icon={<Play size={14} />} onClick={() => void play()} disabled={doc.steps.length === 0} title="Play from the selected step">Play</Button>
          <Button size="sm" icon={<FileText size={14} />} onClick={() => setTranscriptOpen((o) => !o)} aria-expanded={transcriptOpen} title="The walkthrough as a transcript: what is said at each step" data-transcript-toggle="">Transcript</Button>
          {!inline && (
            <Button size="sm" icon={<Share size={14} />} onClick={() => void openExport()} disabled={saving || capturing} title={exportTitle(rec.source)} data-export-course="">Export to course…</Button>
          )}
          {!inline && (
            <Button size="sm" icon={<Clapperboard size={14} />} onClick={() => void openVideo()} disabled={doc.steps.length === 0 || saving || capturing} title={doc.steps.length === 0 ? "Add a step first" : "Compile the walkthrough into a narrated video: a project whose Generate card renders it, each step a slide"} data-make-video="">Make a video…</Button>
          )}
          {!inline && (
            <Button size="sm" icon={<Film size={14} />} onClick={() => void openVideoToCourse()} disabled={doc.steps.length === 0 || saving || capturing || vtcRunning} title={doc.steps.length === 0 ? "Add a step first" : vtcRunning ? "The video is being made: follow it below" : "One job: render the video, publish it to Vimeo and place the link in the course workshop's guide"} data-video-to-course="">Video to course…</Button>
          )}
          {canCapture && !inline && (
            <Button size="sm" icon={<Camera size={14} />} onClick={() => setCaptureOpen(true)} disabled={doc.steps.length === 0 || capturing || saving} title={missingCount ? `${missingCount} step${missingCount === 1 ? " has" : "s have"} no picture yet: capture them in order` : "Capture every step again, in order"} data-capture-steps="">Capture steps…</Button>
          )}
          {canCapture && !inline && (
            <Button size="sm" icon={<Disc size={14} />} onClick={() => setRecordOpen(true)} disabled={capturing || saving || !canAddStep(doc)} title={canAddStep(doc) ? "Record: click through the application and every click, typed pause or Shift+F8 becomes a step after the last" : TOO_MANY_STEPS_MESSAGE} data-record="">Record…</Button>
          )}
          {embedded && (
            <Button size="sm" variant="primary" icon={<ArrowRight size={14} />} onClick={() => void goNext()} disabled={saving || capturing} title={dirty ? "Saves the walkthrough first (asked), then goes on" : "Go on to the next stage"} data-builder-next="">{embedded.nextLabel}</Button>
          )}
          <span role="status" aria-live="polite" style={{ color: event?.bad ? "var(--bad)" : "var(--muted)", fontSize: 12.5 }} data-builder-status="">{statusText}</span>
          {stale && <Button size="sm" onClick={() => void reload()} data-builder-reload="">Reload</Button>}
        </div>
        {!inline && (
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
            projectId={vtcProjectId}
          />
        )}
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
                  : <Button size="sm" onClick={guided.next} disabled={guided.phase !== "ready" && guided.phase !== "capturing"} title="Move on to the next step (Shift+F9): a step with no still yet is left for later" data-capture-next="">{guided.session ? nextLabel(guided.session) : "Next"}</Button>}
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
        {transcriptOpen && (
          <div className="ms-transcript-panel" role="region" aria-label="Transcript" data-builder-transcript="">
            <div className="os-row" style={{ justifyContent: "space-between", gap: 8 }}>
              <div>
                <strong>Transcript</strong>
                <span className="os-muted os-small" style={{ marginLeft: 8 }}>What is said at each step, in order: the instruction, else the text. The narration script of the narrated video to come.</span>
              </div>
              <div className="os-row" style={{ gap: 6 }}>
                <Button size="sm" icon={<Copy size={14} />} onClick={() => void copyTranscript()} data-transcript-copy="">Copy</Button>
                <Button size="sm" icon={<Download size={14} />} onClick={() => downloadText(transcriptFileName(doc.name), markdown)} data-transcript-save="">Save as…</Button>
                <button className="os-icon-btn" onClick={() => setTranscriptOpen(false)} title="Close the transcript" aria-label="Close the transcript"><X size={16} /></button>
              </div>
            </div>
            {copied && <span className="os-muted os-small" role="status" aria-live="polite" data-transcript-copied="">{copied}</span>}
            <pre className="ms-transcript" data-transcript-text="">{markdown}</pre>
          </div>
        )}
        <div className="ms-builder" style={{ padding: 12 }}>
          <aside className="ms-step-list" data-step-list="">
            {doc.steps.map((s, i) => (
              <div
                key={s.id}
                className={`ms-step-item${dropAt === i && dragFrom !== null && dragFrom !== i ? " ms-drop-before" : ""}`}
                aria-selected={s.id === selectedId}
                data-step={s.id}
                draggable
                onDragStart={(e) => { setDragFrom(i); e.dataTransfer?.setData("text/plain", String(i)); }}
                onDragOver={(e) => { e.preventDefault(); if (dropAt !== i) setDropAt(i); }}
                onDrop={(e) => { e.preventDefault(); if (dragFrom !== null) change(reorderStep(doc, dragFrom, i), true); setDragFrom(null); setDropAt(null); }}
                onDragEnd={() => { setDragFrom(null); setDropAt(null); }}
                onClick={() => setSelectedId(s.id)}
              >
                <div className="ms-step-num"><GripVertical size={12} />{i + 1}</div>
                <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
                  {hasPicture(s)
                    ? <img src={stepImageUrl(id, s.id, revision)} alt="" />
                    : <div className="ms-step-noimg" title={NO_PICTURE_TITLE} data-step-no-picture=""><ImageOff size={12} /> No picture</div>}
                  {s.chapter.trim() && <span className="os-chip os-chip-dense os-truncate" style={{ background: "var(--brand)", maxWidth: "100%" }} data-step-chapter="">{s.chapter}</span>}
                  <div className="os-row" style={{ gap: 2 }}>
                    <button className="os-icon-btn" aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={(e) => { e.stopPropagation(); change(moveStep(doc, s.id, -1), true); }}><ArrowUp size={13} /></button>
                    <button className="os-icon-btn" aria-label={`Move step ${i + 1} down`} disabled={i === doc.steps.length - 1} onClick={(e) => { e.stopPropagation(); change(moveStep(doc, s.id, 1), true); }}><ArrowDown size={13} /></button>
                    <button className="os-icon-btn" aria-label={`Delete step ${i + 1}`} onClick={(e) => { e.stopPropagation(); void removeStep(s.id); }}><Trash2 size={13} /></button>
                  </div>
                </div>
              </div>
            ))}
            <Button icon={<Plus size={14} />} onClick={() => setPicker("add")} disabled={!canAddStep(doc)} data-add-step="">Add step…</Button>
            {doc.steps.length === 0 && <p className="os-muted os-small" style={{ margin: 0 }}>{EMPTY_HOW}</p>}
          </aside>
          <div ref={viewportRef} className="ms-builder-stage" data-builder-stage="">
            {selected && hasPicture(selected) ? (
              <HotspotSurface
                src={stepImageUrl(id, selected.id, revision)}
                size={{ width: selected.width, height: selected.height }}
                zoom={zoom}
                hotspot={hotspot}
                marker={selected.marker}
                onChange={(h, commitIt) => change(setHotspot(doc, selected.id, h), commitIt)}
              />
            ) : doc.steps.length === 0 ? (
              <div className="ms-placeholder" data-empty-placeholder="">
                <Disc size={28} />
                <strong>No steps yet</strong>
                <p>{EMPTY_HOW}</p>
                <div className="os-row" style={{ gap: 6 }}>
                  {canCapture && !inline && <Button size="sm" variant="primary" icon={<Disc size={14} />} onClick={() => setRecordOpen(true)} disabled={capturing || saving} data-record="">Record…</Button>}
                  <Button size="sm" icon={<Plus size={14} />} onClick={() => setPicker("add")} data-add-step="">Add step…</Button>
                </div>
              </div>
            ) : selected ? (
              <div className="ms-placeholder" data-step-placeholder="">
                <ImageOff size={28} />
                <strong>{NO_PICTURE_TITLE}</strong>
                <p>{NO_PICTURE_HOW}</p>
                <div className="os-row" style={{ gap: 6 }}>
                  {canCapture && !inline && <Button size="sm" icon={<Camera size={14} />} onClick={() => setCaptureOpen(true)} disabled={capturing || saving}>Capture steps…</Button>}
                  <Button size="sm" icon={<Replace size={14} />} onClick={() => setPicker("replace")}>Replace picture…</Button>
                </div>
              </div>
            ) : (
              <EmptyState title="No step selected" sub="Add a step from a still, or pick one on the left." />
            )}
          </div>
          <aside className="ms-builder-props" data-builder-props="">
            {selected ? (
              <>
                <Field label="Shown as">
                  <Select value={selected.kind} onChange={(e) => edit({ kind: e.target.value as StepKind })} data-step-kind="">
                    <option value="tooltip">Tooltip beside the hotspot</option>
                    <option value="modal">Modal card</option>
                  </Select>
                </Field>
                <Field label="Text" hint={`${selected.text.length} / ${MAX_STEP_TEXT} · what the viewer reads on the picture`}>
                  <Textarea rows={4} maxLength={MAX_STEP_TEXT} value={selected.text} onChange={(e) => edit({ text: e.target.value }, false)} onBlur={commitPreview} placeholder="What the viewer reads at this step" data-step-text="" />
                </Field>
                <Field label="Instruction" hint={`${selected.instruction.length} / ${MAX_INSTRUCTION_CHARS} · what the learner does here, as you would say it - read in order, this is the walkthrough's transcript and, later, its narration`}>
                  <Textarea rows={4} maxLength={MAX_INSTRUCTION_CHARS} value={selected.instruction} onChange={(e) => edit({ instruction: e.target.value }, false)} onBlur={commitPreview} placeholder="Drag the Text file input onto the canvas" data-step-instruction="" />
                </Field>
                <Field label="Placement" hint={selected.kind === "modal" ? "A modal card is centred" : "Where the tooltip sits beside the hotspot"}>
                  <Select value={selected.placement} onChange={(e) => edit({ placement: e.target.value as Placement })} disabled={selected.kind === "modal"} data-step-placement="">
                    {PLACEMENTS.map((p) => <option key={p} value={p}>{PLACEMENT_LABELS[p]}</option>)}
                  </Select>
                </Field>
                <Field label="Click" hint="The real click the learner makes here: the tooltip starts with Right-click or Double-click when so">
                  <Select value={selected.click} onChange={(e) => edit({ click: e.target.value as ClickKind })} data-step-click="">
                    {CLICKS.map((c) => <option key={c} value={c}>{CLICK_LABELS[c]}</option>)}
                  </Select>
                </Field>
                <Field label="Marker" hint={selected.marker === "beacon" ? "A pulsing ring with a dot at the hotspot's centre; the whole rectangle is still the click target" : "The outlined, pulsing box round the hotspot"}>
                  <Select value={selected.marker} onChange={(e) => edit({ marker: e.target.value as Marker })} data-step-marker="">
                    {[...MARKERS].reverse().map((m) => <option key={m} value={m}>{MARKER_LABELS[m]}</option>)}
                  </Select>
                </Field>
                <Field label="Chapter" hint="Starts a new chapter at this step; leave it empty for none">
                  <Input maxLength={MAX_CHAPTER_CHARS} value={selected.chapter} onChange={(e) => edit({ chapter: e.target.value }, false)} onBlur={commitPreview} data-step-chapter-input="" />
                </Field>
                <div className="os-row" style={{ gap: 6, flexWrap: "wrap" }}>
                  <Button size="sm" icon={<Replace size={14} />} onClick={() => setPicker("replace")} data-replace-picture="">Replace picture…</Button>
                  {hasPicture(selected) && !hotspot && (
                    <Button size="sm" icon={<Crosshair size={14} />} onClick={() => edit({ hotspot: centredHotspot({ width: selected.width, height: selected.height }) })} title="A 160 × 60 hotspot at the picture's centre: drag it to where the viewer clicks" data-add-hotspot="">Add hotspot</Button>
                  )}
                  <Button size="sm" onClick={() => edit({ hotspot: null })} disabled={!hotspot} data-remove-hotspot="">Remove hotspot</Button>
                </div>
                <p className="os-muted os-small" style={{ margin: 0 }} data-hotspot-line="">
                  {!hasPicture(selected)
                    ? canCapture
                      ? "No picture yet, so no hotspot: the hotspot comes with the capture (Capture steps…: click through the step, or press Shift+F8 - it goes round the click or the pointer)."
                      : "No picture yet, so no hotspot: the hotspot comes with the capture (round the pointer when you press Shift+F8)."
                    : hotspot
                      ? `Hotspot ${hotspot.width} × ${hotspot.height} at ${hotspot.x}, ${hotspot.y}. Drag it to move it; the handles resize it; Remove hotspot takes it away.`
                      : "No hotspot: press Add hotspot, or drag on the picture to draw one. A tooltip step without a hotspot is shown as a modal card."}
                </p>
                <p className="os-muted os-xs" style={{ margin: 0 }} data-step-size="">
                  {hasPicture(selected) ? `${selected.width} × ${selected.height}` : NO_PICTURE_TITLE}
                  {selected.source && "annotated" in selected.source && selected.source.annotated ? " · from an annotated still" : ""}
                  {selected.source && "slug" in selected.source ? ` · from ${selected.source.course}/${selected.source.slug}` : ""}
                  {selected.note ? ` · a note from the guide (${selected.note.length} characters, kept for the narrated video)` : ""}
                </p>
              </>
            ) : (
              <p className="os-muted" style={{ margin: 0 }}>Select a step to edit it.</p>
            )}
          </aside>
        </div>
      </Card>
      {canCapture && (
        <CaptureStepsDialog open={captureOpen} missing={missingCount} total={doc.steps.length} onClose={() => setCaptureOpen(false)} onStart={(o) => void startCapture(o)} />
      )}
      {canCapture && (
        <CaptureStepsDialog open={recordOpen} mode="record" missing={missingCount} total={doc.steps.length} onClose={() => setRecordOpen(false)} onStart={(o) => void startCapture(o)} />
      )}
      {canCapture && (
        <CaptureDialog open={takeStillOpen} onClose={() => setTakeStillOpen(false)} initialMode="still" stillOnly onStill={(cid) => void addStep({ id: cid } as Capture)} />
      )}
      <ExportCourseDialog open={exportOpen} wid={id} onClose={() => setExportOpen(false)} />
      <MakeVideoDialog open={videoOpen} wid={id} doc={saved ?? doc} onClose={() => setVideoOpen(false)} />
      {!inline && (
        <VideoToCourseDialog
          open={vtcOpen}
          wid={id}
          doc={saved ?? doc}
          busy={vtcRunning}
          onClose={() => setVtcOpen(false)}
          onStarted={(r) => { setVtcEnded(null); setVtcProject(r.project_id); vtc.follow(r.job_id, VIDEO_TO_COURSE_JOB_KIND); }}
        />
      )}
      <StillPicker
        open={picker !== null}
        title={picker === "replace" ? "Replace picture" : "Add step"}
        lead={picker === "replace" ? "The step keeps its text, instruction, placement, chapter and hotspot (kept inside the new picture); its picture is copied again from the still you pick, as the still shows now." : `A new step after step ${Math.max(0, selectedIndex) + 1}, with the picture the still shows now - the annotated one once it is annotated.`}
        busy={picking}
        onClose={() => setPicker(null)}
        onPick={(c) => void (picker === "replace" ? replacePicture(c) : addStep(c))}
        onTakeStill={canCapture && picker === "add" ? takeStill : undefined}
      />
    </>
  );
}
