/**
 * Import workshop (W2; W2e: several workshops): the dialog on the Walkthroughs page. The course (its title and
 * version); the workshops as a TICK LIST grouped by section as SUMMARY.md orders them - a tick per workshop (its
 * title, minutes and step count) and a tick on each section header that takes all of its; the first workshop
 * ticked by default, Import greyed out with none; a preview of what the import would make - every ticked workshop
 * read in parallel (the preview route, one query per slug), ONE combined line ("27 steps in 7 chapters; 11 pictures
 * from the guide, 16 to capture" - the import's own numbers: with several workshops the chapter a workshop's kept
 * steps before its first heading start, named by the workshop, is counted) and, under it, each workshop's own block
 * in the COURSE's order whatever the tick order: its title, its line, its chapters as ticks (all ticked; an unticked
 * chapter's steps are left out of the import, and the counts follow) with the steps under them - a workshop whose
 * preview fails is named beside the blocks of the others, with no combined line and Import greyed; a name (when left
 * empty, the one rule of `defaultName`:
 * the workshop's title, a whole section's title, the whole course's, or "<first> + N more") and Import, which makes
 * ONE walkthrough on the server (`POST /api/walkthroughs/import`, `workshops: [{slug, skip_chapters?}]`) and opens
 * it in the builder with a notice. When the courses folder is not set, the dialog says where to set it. Pure web:
 * the browser edition has it; the rules are `lib/workshops.ts`.
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Download, ImageOff } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Field, Input, Modal, Select, Spinner } from "../ui";
import { type Walkthrough, MAX_NAME_CHARS, builderUrl, walkthroughsQueryKey } from "../../lib/walkthrough";
import {
  type CoursesPayload, type WorkshopPreview, chapterGroups, chapterLabel, combinedCountsOf, countsOf, courseLabel, coursesQueryKey, defaultName,
  firstWorkshop, importBody, importNotice, previewLine, previewQueryKey, previewUrl, sectionState, tickedInOrder, toggleSection, workshopLabel,
} from "../../lib/workshops";

export function ImportWorkshopDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [courseId, setCourseId] = useState("");
  const [ticked, setTicked] = useState<Set<string>>(() => new Set());
  /** Per workshop, the chapters left out (by the name the preview's `in_chapter` gives; "" the steps before the first heading). */
  const [skipped, setSkipped] = useState<Record<string, string[]>>({});
  const [name, setName] = useState("");

  const courses = useQuery({ queryKey: coursesQueryKey, queryFn: () => api.get<CoursesPayload>("/api/courses"), enabled: open });
  const list = useMemo(() => courses.data?.courses ?? [], [courses.data]);
  const course = list.find((c) => c.id === courseId) ?? null;

  // The first course and its first workshop are preselected once the list is in; a fresh open starts over.
  useEffect(() => {
    if (!open) { setCourseId(""); setTicked(new Set()); setSkipped({}); setName(""); return; }
    if (!courseId && list.length) {
      setCourseId(list[0].id);
      const first = firstWorkshop(list[0]);
      setTicked(new Set(first ? [first] : []));
    }
  }, [open, list, courseId]);

  // The ticked workshops in the course's order, whatever the tick order; one preview each, read in parallel.
  const order = useMemo(() => tickedInOrder(course, ticked), [course, ticked]);
  const previews = useQueries({
    queries: order.map((slug) => ({
      queryKey: previewQueryKey(courseId, slug),
      queryFn: () => api.get<WorkshopPreview>(previewUrl(courseId, slug)),
      enabled: open && !!courseId,
      retry: false,
    })),
  });
  // The blocks of the previews that have answered (a failed one is named beside them, X2); the combined line and Import
  // wait for every ticked workshop. With several workshops a workshop's kept steps before its first heading start a
  // chapter named by the workshop on the server, and the lines count it (X1): the dialog says the import's own numbers.
  const several = order.length > 1;
  const blocks = order.flatMap((slug, i) => (previews[i].data ? [{ slug, preview: previews[i].data as WorkshopPreview, skips: skipped[slug] ?? [] }] : []));
  const loaded = order.length > 0 && blocks.length === order.length;
  const loading = previews.some((q) => q.isLoading);
  const failed = previews.flatMap((q, i) => (q.isError ? [{ slug: order[i], error: q.error }] : []));
  const combined = loaded ? combinedCountsOf(blocks.map((b) => ({ steps: b.preview.steps, skips: b.skips }))) : null;

  const importIt = useMutation({
    mutationFn: () => api.post<Walkthrough>("/api/walkthroughs/import", importBody(courseId, order, skipped, name)),
    onSuccess: (rec) => {
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey });
      onClose();
      navigate(builderUrl(rec.id), { state: { notice: importNotice(rec.name, combined) } });
    },
  });

  const chooseCourse = (id: string) => {
    setCourseId(id);
    const first = firstWorkshop(list.find((c) => c.id === id));
    setTicked(new Set(first ? [first] : []));
    setSkipped({});
    setName("");
  };
  const tick = (slug: string, on: boolean) => setTicked((t) => {
    const next = new Set(t);
    if (on) next.add(slug);
    else next.delete(slug);
    return next;
  });
  const skip = (slug: string, chapter: string, out: boolean) => setSkipped((s) => {
    const own = (s[slug] ?? []).filter((c) => c !== chapter);
    return { ...s, [slug]: out ? [...own, chapter] : own };
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import workshop"
      width={760}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Download size={16} />} disabled={!loaded || importIt.isPending} onClick={() => importIt.mutate()} data-import-confirm="">
            {importIt.isPending ? "Importing…" : "Import"}
          </Button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>Course workshops become one walkthrough: tick the workshops it is based on - one, a section, or the whole course - and leave out the chapters you do not want. Each workshop's sections become the chapters, its numbered steps the steps, each with its instruction and the guide's screenshot when it has one. Steps without a picture are captured in the desktop app afterwards.</p>
      {courses.isLoading && <Spinner label="Loading the courses…" />}
      {courses.isError && <ErrorBox message={errorMessage(courses.error)} />}
      {courses.data && !courses.data.configured && (
        <p className="os-muted" data-import-unset="">{courses.data.message || "Set the Courses folder on the Settings page first."}</p>
      )}
      {courses.data?.configured && list.length === 0 && <p className="os-muted" data-import-empty="">The courses folder holds no course (a course is a folder with a course.json).</p>}
      {list.length > 0 && (
        <div style={{ display: "grid", gap: 10 }}>
          <Field label="Course">
            <Select value={courseId} onChange={(e) => chooseCourse(e.target.value)} aria-label="Course" data-import-course="">
              {list.map((c) => <option key={c.id} value={c.id}>{courseLabel(c)}</option>)}
            </Select>
          </Field>
          <Field label="Workshops" hint="The walkthrough is based on the workshops ticked, in the course's order; a section's tick takes all of its">
            <div className="ms-workshop-ticks" data-import-workshops="">
              {course?.sections.map((section) => {
                const state = sectionState(section, ticked);
                return (
                  <div key={section.title || "(workshops)"} className="ms-workshop-section">
                    <label className="os-row" style={{ gap: 8, alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={state === "all"}
                        ref={(el) => { if (el) el.indeterminate = state === "some"; }}
                        onChange={(e) => setTicked(toggleSection(ticked, section, e.target.checked))}
                        aria-label={`Every workshop of ${section.title || "the course"}`}
                        data-import-section={section.title}
                      />
                      <strong>{section.title || "Workshops"}</strong>
                    </label>
                    {section.workshops.map((w) => (
                      <label key={w.slug} className="os-row" style={{ gap: 8, alignItems: "center", paddingLeft: 24 }}>
                        <input type="checkbox" checked={ticked.has(w.slug)} onChange={(e) => tick(w.slug, e.target.checked)} aria-label={w.title} data-import-tick={w.slug} />
                        <span>{workshopLabel(w)}</span>
                      </label>
                    ))}
                  </div>
                );
              })}
            </div>
          </Field>
          <Field label="Name" hint="When left empty: the workshop's title; for a whole section, the section's; for the whole course, the course's; otherwise the first workshop's title and how many more">
            <Input value={name} maxLength={MAX_NAME_CHARS} placeholder={defaultName(course, ticked)} onChange={(e) => setName(e.target.value)} data-import-name="" />
          </Field>
          {order.length === 0 && <p className="os-muted os-small" style={{ margin: 0 }} data-import-none="">Tick at least one workshop.</p>}
          {loading && <Spinner label="Reading the guides…" />}
          {failed.map((f) => <ErrorBox key={f.slug} message={`${f.slug}: ${errorMessage(f.error)}`} />)}
          {blocks.length > 0 && (
            <div style={{ display: "grid", gap: 8 }} data-import-preview="">
              {combined && <strong data-import-line="">{previewLine(combined)}</strong>}
              {blocks.map(({ slug, preview: p, skips }) => (
                <div key={slug} style={{ display: "grid", gap: 4 }} data-import-workshop-preview={slug}>
                  <div className="os-row" style={{ gap: 8, alignItems: "baseline" }}>
                    <strong data-import-workshop-title="">{p.workshop.title}</strong>
                    <span className="os-muted os-small" data-import-workshop-line="">{previewLine(countsOf(p.steps, skips, several))}</span>
                  </div>
                  {p.message && <span className="os-muted os-small" data-import-message="">{p.message}</span>}
                  {chapterGroups(p.steps).map((g) => {
                    const out = skips.includes(g.name);
                    return (
                      <div key={`${g.name}#${g.steps[0].number}`} className={`ms-import-chapter${out ? " ms-import-skipped" : ""}`} data-import-chapter={g.name}>
                        <label className="os-row" style={{ gap: 6, alignItems: "center" }}>
                          <input type="checkbox" checked={!out} onChange={(e) => skip(slug, g.name, !e.target.checked)} aria-label={`Import the chapter ${chapterLabel(g.name)}`} data-import-chapter-tick="" />
                          <span className="os-chip os-chip-dense" style={{ background: "var(--brand)" }}>{chapterLabel(g.name)}</span>
                          <span className="os-muted os-xs">{g.steps.length} step{g.steps.length === 1 ? "" : "s"}{out ? " - left out" : ""}</span>
                        </label>
                        <ol className="ms-import-steps" start={g.steps[0].number} data-import-steps="">
                          {g.steps.map((s) => (
                            <li key={s.number} title={s.instruction}>
                              {s.picture ? <Camera size={13} aria-label="picture from the guide" /> : <ImageOff size={13} className="os-dim" aria-label="to capture" />}
                              <span className="os-truncate">{s.text}</span>
                            </li>
                          ))}
                        </ol>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
          {importIt.isError && <ErrorBox message={errorMessage(importIt.error)} />}
        </div>
      )}
    </Modal>
  );
}
