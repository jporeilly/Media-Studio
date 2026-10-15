/**
 * Annotate a still (T3b): `/captures/:id/annotate`. The nine everyday tools - Arrow, Callout, Text, Shape
 * (rectangle, ellipse, line), Step, Blur, Highlighter, Crop, Magnify - drawn over the still; Save keeps the
 * document and the exported picture in one request (and from then on Add to deck, Copy and Save as use the
 * annotated picture); Revert to original throws them away. The original is never touched.
 *
 * Pure web: no screen is read here, so the page is NOT behind the capture hide rule - it works in the
 * browser edition too, from the Captures card, which lists the stills everywhere. The model is
 * `lib/annotate.ts`; the surface, the toolbar and the text box are `components/annotate/*`; the Add-to-deck
 * dialog is the Captures card's own (`components/capture/AddToDeckDialog`).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { ApiError, api, errorMessage } from "../api/client";
import { useConfirm } from "../components/ConfirmDialog";
import { Card, ErrorBox, PageHeader, Spinner } from "../components/ui";
import { AddToDeckDialog } from "../components/capture/AddToDeckDialog";
import { Surface } from "../components/annotate/Surface";
import { Toolbar } from "../components/annotate/Toolbar";
import { exportPng } from "../components/annotate/exportPng";
import { loadImage } from "../components/annotate/loadImage";
import { useLeaveGuard } from "../components/annotate/useLeaveGuard";
import {
  type AnnotationDocument, type Size, type Style, type Tool, DEFAULT_STYLE, GONE_MESSAGE, MAX_ANNOTATE_PIXELS, TOO_BIG_MESSAGE,
  UNSAVED_ASK, currentSequence, deleteShape, emptyDocument, fitZoom, history, parseDocument, restartSequence,
  serialise, shapeById, toolForKey, updateShape, zoomIn, zoomOut,
} from "../lib/annotate";
import { type History, canRedo, canUndo, commit, redo, undo } from "../lib/history";
import { type Capture, captureOriginalUrl, captureQueryKey, capturesQueryKey } from "../lib/capture";

/** The one shared style, remembered for the session (the next still opens with it). */
let sessionStyle: Style = DEFAULT_STYLE;

const typing = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
};

export default function AnnotatePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm, dialog } = useConfirm();
  const viewportRef = useRef<HTMLDivElement>(null);

  const record = useQuery({
    queryKey: captureQueryKey(id),
    queryFn: () => api.get<Capture>(`/api/captures/${id}`),
    retry: false,
  });
  const rec = record.data ?? null;

  const still = useMemo<Size | null>(() => (rec ? { width: rec.width, height: rec.height } : null), [rec]);
  const tooBig = !!still && still.width * still.height > MAX_ANNOTATE_PIXELS;

  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  useEffect(() => {
    if (!rec || tooBig) return;
    let live = true;
    setImage(null);
    loadImage(captureOriginalUrl(rec)).then((img) => { if (live) setImage(img); }).catch((e) => { if (live) setImageError(errorMessage(e)); });
    return () => { live = false; };
    // The picture is the ORIGINAL still, which never changes: load it once per capture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rec?.id, rec?.created_at, tooBig]);

  // The document and its history, from the record once; the last saved state for the dirty flag. A drag
  // in progress is a PREVIEW held apart from the history, so undo never lands on a half-made drag.
  const [hist, setHist] = useState<History<AnnotationDocument> | null>(null);
  const [preview, setPreview] = useState<AnnotationDocument | null>(null);
  const [saved, setSaved] = useState<AnnotationDocument | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    if (!rec || hist) return;
    try {
      const doc = rec.annotated && rec.annotations ? parseDocument(rec.annotations) : emptyDocument();
      setHist(history(doc));
      setSaved(doc);
    } catch (e) {
      setLoadError(errorMessage(e));
    }
  }, [rec, hist]);

  const [tool, setToolState] = useState<Tool>("select");
  const [style, setStyleState] = useState<Style>(sessionStyle);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number>(1);
  const [fitted, setFitted] = useState(false);
  const [sequence, setSequence] = useState(0);
  // What the status line's left side says: an EVENT ("Saved", "Reverted…", "Added as slide…"), shown only
  // while nothing is unsaved, and a PROBLEM ("Could not save: …"), shown until the next attempt.
  const [status, setStatus] = useState<string>("");
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [addTo, setAddTo] = useState<Capture | null>(null);
  const [saveTick, setSaveTick] = useState(0);
  const saveRequested = useRef(false);

  const doc = preview ?? hist?.present ?? null;
  const dirty = !!doc && !!saved && serialise(doc) !== serialise(saved);

  // Fit once the picture is there; the step sequence continues the document's latest.
  useEffect(() => {
    if (!image || !still || fitted) return;
    const el = viewportRef.current;
    setZoom(fitZoom({ width: el?.clientWidth ?? 0, height: el?.clientHeight ?? 0 }, still));
    setFitted(true);
  }, [image, still, fitted]);
  useEffect(() => { if (doc) setSequence((s) => Math.max(s, currentSequence(doc))); }, [doc]);

  const setTool = useCallback((t: Tool) => { setToolState(t); if (t !== "select") setEditingId(null); }, []);
  const change = useCallback((next: AnnotationDocument, commitIt: boolean) => {
    if (!commitIt) { setPreview(next); return; }
    setPreview(null);
    setHist((h) => (h ? commit(h, next) : h));
  }, []);
  const undoOne = useCallback(() => { setPreview(null); setHist((h) => (h ? undo(h) : h)); }, []);
  const redoOne = useCallback(() => { setPreview(null); setHist((h) => (h ? redo(h) : h)); }, []);
  const applyStyle = useCallback((patch: Partial<Style>) => {
    setStyleState((s) => { sessionStyle = { ...s, ...patch }; return sessionStyle; });
    // A selected shape takes the new style as well (a highlight its own colour).
    if (!selectedId) return;
    setHist((h) => {
      if (!h) return h;
      const next = updateShape(h.present, selectedId, (s) => {
        let out = s;
        if (patch.colour && s.type !== "highlight") out = { ...out, colour: patch.colour };
        if (patch.highlight && s.type === "highlight") out = { ...out, colour: patch.highlight };
        if (patch.stroke) out = { ...out, stroke: patch.stroke };
        if (patch.textSize && (out.type === "text" || out.type === "callout")) out = { ...out, text_size: patch.textSize };
        if (patch.lensFactor && out.type === "magnify") out = { ...out, factor: patch.lensFactor };
        return out;
      });
      return commit(h, next);
    });
  }, [selectedId]);

  const fit = useCallback(() => {
    if (!still) return;
    const el = viewportRef.current;
    setZoom(fitZoom({ width: el?.clientWidth ?? 0, height: el?.clientHeight ?? 0 }, still));
  }, [still]);

  const removeSelected = useCallback(() => {
    if (!selectedId || editingId) return;
    setHist((h) => (h ? commit(h, deleteShape(h.present, selectedId)) : h));
    setSelectedId(null);
  }, [selectedId, editingId]);

  // Leaving with unsaved changes asks first (in-app navigation, Back / Forward, and the window where the
  // browser lets a page ask). `release` takes the guards down for a navigation that must not ask.
  const ask = useCallback(() => confirm({ ...UNSAVED_ASK, danger: true }), [confirm]);
  const release = useLeaveGuard(dirty, ask);

  // A still the user can no longer reach (deleted meanwhile, or somebody else's): back to Projects with a
  // notice - past the leave guard, which would otherwise strand the user on a dead page.
  const gone = useCallback(() => {
    release();
    navigate("/projects", { replace: true, state: { notice: GONE_MESSAGE } });
  }, [navigate, release]);
  useEffect(() => {
    const e = record.error;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) gone();
  }, [record.error, gone]);

  const save = useCallback(async (): Promise<Capture | null> => {
    if (!doc || !image || !still || saving) return null;
    if (!dirty) { setStatus("Nothing to save."); return rec; }
    setSaving(true);
    setProblem(null);
    try {
      let next: Capture;
      if (doc.shapes.length === 0 && !doc.crop) {
        // No annotations at all: the still is its own picture. Nothing to keep, so nothing is kept: the
        // server's revert (its audit row says so - it IS one).
        await api.delete(`/api/captures/${id}/annotations`);
        next = { ...(rec as Capture), annotated: false, annotated_at: null, annotations: null, output_width: still.width, output_height: still.height };
      } else {
        const blob = await exportPng(doc, image, still);
        const form = new FormData();
        form.append("document", serialise(doc));
        form.append("image", blob, "annotated.png");
        next = await api.putForm<Capture>(`/api/captures/${id}/annotations`, form);
      }
      qc.setQueryData(captureQueryKey(id), next);
      void qc.invalidateQueries({ queryKey: capturesQueryKey, exact: true });
      setSaved(doc);
      setStatus("Saved");
      return next;
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) { gone(); return null; }
      setProblem(`Could not save: ${errorMessage(e)}`);
      return null;
    } finally {
      setSaving(false);
    }
  }, [doc, image, still, saving, dirty, id, rec, qc, gone]);

  // Ctrl+S inside the text box: the box commits (a state change), then the save runs on the committed
  // document - the tick makes the effect run even when the commit changed nothing.
  useEffect(() => {
    if (!saveRequested.current || preview) return;
    saveRequested.current = false;
    void save();
  }, [saveTick, preview, save]);
  const requestSave = useCallback(() => { saveRequested.current = true; setSaveTick((t) => t + 1); }, []);

  const saveAndAdd = useCallback(async () => {
    if (!rec) return;
    const next = dirty ? await save() : rec;
    if (next) setAddTo(next);
  }, [rec, dirty, save]);

  const revert = useCallback(async () => {
    if (!rec) return;
    const ok = await confirm({ title: "Revert to original", message: <>Throw away every annotation on <b>{rec.name}</b>? The original still is kept as it is.</>, confirmLabel: "Revert", danger: true });
    if (!ok) return;
    setProblem(null);
    try {
      await api.delete(`/api/captures/${id}/annotations`);
      const empty = emptyDocument();
      const next: Capture = { ...rec, annotated: false, annotated_at: null, annotations: null, output_width: rec.width, output_height: rec.height };
      qc.setQueryData(captureQueryKey(id), next);
      void qc.invalidateQueries({ queryKey: capturesQueryKey, exact: true });
      setPreview(null);
      setHist(history(empty));
      setSaved(empty);
      setSelectedId(null);
      setEditingId(null);
      setStatus("Reverted to the original");
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) { gone(); return; }
      setProblem(`Could not revert: ${errorMessage(e)}`);
    }
  }, [rec, id, confirm, qc, gone]);

  // The keys: tools, undo / redo, save, zoom, delete, escape - never while typing in a box (the text box
  // handles its own, Ctrl+S included).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target)) return;
      const key = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && key === "s") { e.preventDefault(); void save(); return; }
      if ((e.ctrlKey || e.metaKey) && key === "z" && !e.shiftKey) { e.preventDefault(); undoOne(); return; }
      if ((e.ctrlKey || e.metaKey) && (key === "y" || (key === "z" && e.shiftKey))) { e.preventDefault(); redoOne(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Escape") { setSelectedId(null); return; }
      if (e.key === "Delete" || e.key === "Backspace") { if (selectedId) { e.preventDefault(); removeSelected(); } return; }
      if (e.key === "+" || e.key === "=") { setZoom((z) => zoomIn(z)); return; }
      if (e.key === "-" || e.key === "_") { setZoom((z) => zoomOut(z)); return; }
      if (e.key === "0") { fit(); return; }
      const t = toolForKey(e);
      if (t) setTool(t);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, selectedId, removeSelected, fit, setTool, undoOne, redoOne]);

  const selected = doc ? shapeById(doc, selectedId) : null;
  // ONE derivation: while saving, "Saving…"; a problem until the next attempt; "Unsaved changes" whenever
  // the document differs from the saved one; an event line only while nothing is unsaved.
  const statusText = saving ? "Saving…" : problem ?? (dirty ? "Unsaved changes" : status || (rec?.annotated ? "Annotated" : ""));

  if (record.isLoading) return <Spinner label="Loading the still…" />;
  if (record.isError) {
    const e = record.error;
    if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return <Spinner label="Back to Projects…" />;
    return <ErrorBox message={errorMessage(e)} />;
  }
  if (!rec || !still) return null;

  return (
    <>
      <PageHeader
        title={rec.name}
        subtitle={`${rec.width} × ${rec.height} · annotate the still, then Save. Add to deck, Copy and Save as use the annotated picture from then on; the original is kept.`}
        actions={<Link to="/projects" className="os-btn os-btn-secondary os-btn-sm" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><ArrowLeft size={14} /> Projects</Link>}
      />
      {dialog}
      {tooBig ? (
        <ErrorBox message={TOO_BIG_MESSAGE(rec.width, rec.height)} />
      ) : loadError ? (
        <ErrorBox message={loadError} />
      ) : imageError ? (
        <ErrorBox message={imageError} />
      ) : (
        <Card style={{ padding: 0, overflow: "hidden" }}>
          {doc && (
            <Toolbar
              tool={tool}
              onTool={setTool}
              style={style}
              onStyle={applyStyle}
              selectedType={selected?.type ?? null}
              onRestartNumbering={() => { setSequence(restartSequence(doc)); setStatus("The next step starts at 1."); }}
              zoom={zoom}
              onZoom={setZoom}
              onFit={fit}
              onZoomIn={() => setZoom((z) => zoomIn(z))}
              onZoomOut={() => setZoom((z) => zoomOut(z))}
              canUndo={!!hist && canUndo(hist)}
              canRedo={!!hist && canRedo(hist)}
              onUndo={undoOne}
              onRedo={redoOne}
              dirty={dirty}
              saving={saving}
              annotated={rec.annotated}
              onSave={() => void save()}
              onSaveAndAdd={() => void saveAndAdd()}
              onRevert={() => void revert()}
            />
          )}
          <div ref={viewportRef} style={{ overflow: "auto", height: "calc(100vh - 300px)", minHeight: 320, background: "var(--surface-2)", position: "relative" }} data-annotate-viewport="">
            {doc && image ? (
              <Surface
                doc={doc}
                original={image}
                still={still}
                tool={tool}
                style={style}
                zoom={zoom}
                selectedId={selectedId}
                editingId={editingId}
                sequence={sequence}
                onSelect={setSelectedId}
                onChange={change}
                onEdit={setEditingId}
                onRefused={setProblem}
                onSaveRequest={requestSave}
              />
            ) : (
              <Spinner label="Loading the picture…" />
            )}
          </div>
          <div className="os-row" style={{ padding: "8px 12px", borderTop: "1px solid var(--border)", color: "var(--muted)", fontSize: 12.5, minHeight: 34 }} role="status" aria-live="polite" data-annotate-status="">
            <span data-annotate-status-left="">{statusText}</span>
            <span style={{ marginLeft: "auto" }}>
              {doc ? `${doc.shapes.length} shape${doc.shapes.length === 1 ? "" : "s"}` : ""}{doc?.crop ? ` · cropped to ${doc.crop.width} × ${doc.crop.height}` : ""}
            </span>
          </div>
        </Card>
      )}
      <AddToDeckDialog capture={addTo} onClose={() => setAddTo(null)} onNotice={setStatus} />
    </>
  );
}
