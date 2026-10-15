/**
 * Import workshop (W2): the dialog on the Walkthroughs page. The course (its title and version), the workshop
 * (grouped by section, with its minutes and step count), a preview of what the import would make ("12 steps in 4
 * chapters; 9 pictures from the guide, 3 to capture", the steps themselves), a name (the workshop's title when left
 * empty) and Import, which makes the walkthrough on the server (`POST /api/walkthroughs/import`) and opens it in
 * the builder with a notice. When the courses folder is not set, the dialog says where to set it. Pure web: the
 * browser edition has it; the rules are `lib/workshops.ts`.
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Download, ImageOff } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { Button, ErrorBox, Field, Input, Modal, Select, Spinner } from "../ui";
import { type Walkthrough, MAX_NAME_CHARS, builderUrl, walkthroughsQueryKey } from "../../lib/walkthrough";
import {
  type CoursesPayload, type WorkshopPreview, courseLabel, coursesQueryKey, firstWorkshop, importNotice, previewLine, previewQueryKey, previewUrl,
  workshopLabel,
} from "../../lib/workshops";

export function ImportWorkshopDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [courseId, setCourseId] = useState("");
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");

  const courses = useQuery({ queryKey: coursesQueryKey, queryFn: () => api.get<CoursesPayload>("/api/courses"), enabled: open });
  const list = useMemo(() => courses.data?.courses ?? [], [courses.data]);
  const course = list.find((c) => c.id === courseId) ?? null;

  // The first course and its first workshop are preselected once the list is in; a fresh open starts over.
  useEffect(() => {
    if (!open) { setCourseId(""); setSlug(""); setName(""); return; }
    if (!courseId && list.length) { setCourseId(list[0].id); setSlug(firstWorkshop(list[0]) ?? ""); }
  }, [open, list, courseId]);

  const preview = useQuery({
    queryKey: previewQueryKey(courseId, slug),
    queryFn: () => api.get<WorkshopPreview>(previewUrl(courseId, slug)),
    enabled: open && !!courseId && !!slug,
    retry: false,
  });

  const importIt = useMutation({
    mutationFn: () => api.post<Walkthrough>("/api/walkthroughs/import", name.trim() ? { course: courseId, slug, name: name.trim() } : { course: courseId, slug }),
    onSuccess: (rec) => {
      void qc.invalidateQueries({ queryKey: walkthroughsQueryKey });
      onClose();
      navigate(builderUrl(rec.id), { state: { notice: importNotice(rec.name, preview.data?.counts ?? null) } });
    },
  });

  const chooseCourse = (id: string) => {
    setCourseId(id);
    setSlug(firstWorkshop(list.find((c) => c.id === id)) ?? "");
    setName("");
  };
  const p = preview.data ?? null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import workshop"
      width={720}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Download size={16} />} disabled={!p || importIt.isPending} onClick={() => importIt.mutate()} data-import-confirm="">
            {importIt.isPending ? "Importing…" : "Import"}
          </Button>
        </>
      }
    >
      <p style={{ marginTop: 0 }}>A course workshop becomes a walkthrough: its sections the chapters, its numbered steps the steps, each with its instruction and the guide's screenshot when it has one. Steps without a picture are captured in the desktop app afterwards.</p>
      {courses.isLoading && <Spinner label="Loading the courses…" />}
      {courses.isError && <ErrorBox message={errorMessage(courses.error)} />}
      {courses.data && !courses.data.configured && (
        <p className="os-muted" data-import-unset="">{courses.data.message || "Set the Courses folder on the Settings page first."}</p>
      )}
      {courses.data?.configured && list.length === 0 && <p className="os-muted" data-import-empty="">The courses folder holds no course (a course is a folder with a course.json).</p>}
      {list.length > 0 && (
        <div style={{ display: "grid", gap: 10 }}>
          <div className="os-form-grid">
            <Field label="Course">
              <Select value={courseId} onChange={(e) => chooseCourse(e.target.value)} aria-label="Course" data-import-course="">
                {list.map((c) => <option key={c.id} value={c.id}>{courseLabel(c)}</option>)}
              </Select>
            </Field>
            <Field label="Workshop">
              <Select value={slug} onChange={(e) => { setSlug(e.target.value); setName(""); }} aria-label="Workshop" data-import-slug="">
                {course?.sections.map((section) => (
                  <optgroup key={section.title || "(workshops)"} label={section.title || "Workshops"}>
                    {section.workshops.map((w) => <option key={w.slug} value={w.slug}>{workshopLabel(w)}</option>)}
                  </optgroup>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Name" hint="The workshop's title when left empty">
            <Input value={name} maxLength={MAX_NAME_CHARS} placeholder={p?.name ?? ""} onChange={(e) => setName(e.target.value)} data-import-name="" />
          </Field>
          {preview.isLoading && <Spinner label="Reading the guide…" />}
          {preview.isError && <ErrorBox message={errorMessage(preview.error)} />}
          {p && (
            <div style={{ display: "grid", gap: 6 }} data-import-preview="">
              <strong data-import-line="">{previewLine(p.counts)}</strong>
              {p.message && <span className="os-muted os-small" data-import-message="">{p.message}</span>}
              {p.steps.length > 0 && (
                <ol className="ms-import-steps" data-import-steps="">
                  {p.steps.map((s) => (
                    <li key={s.number} title={s.instruction}>
                      {s.picture ? <Camera size={13} aria-label="picture from the guide" /> : <ImageOff size={13} className="os-dim" aria-label="to capture" />}
                      {s.chapter && <span className="os-chip os-chip-dense" style={{ background: "var(--brand)" }}>{s.chapter}</span>}
                      <span className="os-truncate">{s.text}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
          {importIt.isError && <ErrorBox message={errorMessage(importIt.error)} />}
        </div>
      )}
    </Modal>
  );
}
