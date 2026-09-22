/**
 * The music library, as the Music lane's header opens it (E4b, spec §12.5).
 *
 * The library itself is STUDIO-WIDE (§12.3): every signed-in user may list,
 * upload and delete, the name is the handle a clip refers to, and nothing is
 * ever replaced — an upload of a name already there is refused (409) rather
 * than changing every project that uses it. So this modal is a table of the
 * index with three actions, and each refusal is shown exactly as the server
 * words it: the 409 that names the file, the 409 that names the projects
 * still using it, and the 400 for a file that will not decode or is wider
 * than stereo.
 *
 * "Add at playhead" hands the file back to the timeline, which makes the clip
 * (`newMusicClip`) and commits the whole `music` list in one PUT — the
 * drawing, as always, comes from the refetched plan and never from local
 * optimism.
 *
 * And because the drawing comes from the plan, an upload or a delete here
 * invalidates the PLAN as well as the library (`libraryChangeKeys`): whether a
 * clip's file is missing is the server's answer, computed against this
 * library, so a file put back under its name has to reach the strip the same
 * way it left it.
 */
import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Music, Plus, Trash2, Upload } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { useConfirm } from "../ConfirmDialog";
import { duration as formatDuration, relativeTime } from "../../lib/format";
import { libraryChangeKeys, musicLibraryKey } from "../../lib/timeline";
import { Button, ErrorBox, Modal, Spinner, Table } from "../ui";

/** One row of `GET /api/music` (`services/music.py`'s index). */
export interface MusicFile {
  name: string;
  size: number;
  duration: number;
  sample_rate: number;
  channels: number;
  uploaded_at: string;
  uploaded_by: string;
}

/** What the upload accepts, as `services.music.ALLOWED_EXTENSIONS` lists it. */
const ACCEPT = ".mp3,.wav,.m4a,.aac,.ogg,.flac";

function megabytes(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /**
   * Whose timeline this library was opened from. The library itself is
   * studio-wide, but a change to it changes what the SERVER says about this
   * project's clips (`missing`), so the queries that carry that answer are
   * invalidated here — `libraryChangeKeys`.
   */
  projectId: string;
  /** Where the clip would land, output seconds — shown on the Add button. */
  playhead: string;
  /** False while the lane is locked or a commit is in flight; the reason is the button's title. */
  canAdd: boolean;
  addTitle: string;
  onAdd: (file: MusicFile) => void;
}

export function MusicLibrary({ open, onClose, projectId, playhead, canAdd, addTitle, onAdd }: Props) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement | null>(null);
  /** The name being uploaded, for the status line: `fetch` cannot report progress on a request body. */
  const [uploading, setUploading] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  /**
   * The app's own confirmation before a delete (never the browser's, which the
   * desktop shell swallows). The library is STUDIO-WIDE and the delete is
   * irreversible with no undo: a file somebody else uploaded and no project
   * refers to went on one click, from a button sharing its cell with "Add at
   * playhead".
   */
  const { confirm, dialog } = useConfirm();
  /**
   * ONE mechanism for both mutations, because an upload and a delete make
   * exactly the same queries stale (`libraryChangeKeys`, lib/timeline.ts): the
   * library's own, and the two that carry the server's `missing` answer for
   * this project's clips. Invalidating the library alone is what let the
   * stuck-clip banner survive the remedy it recommends.
   */
  const libraryChanged = useCallback(() => {
    for (const queryKey of libraryChangeKeys(projectId)) void qc.invalidateQueries({ queryKey });
  }, [qc, projectId]);

  const library = useQuery({
    queryKey: musicLibraryKey,
    queryFn: () => api.get<{ files: MusicFile[] }>("/api/music"),
    enabled: open,
    staleTime: 30_000,
  });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<MusicFile>("/api/music", form);
    },
    onMutate: (file: File) => { setFailure(null); setUploading(file.name); },
    onSuccess: libraryChanged,
    // The server's own words: 409 "…is already in the library…", 400 for a
    // file that will not decode or has more than two channels, 413 too big.
    onError: (err) => setFailure(errorMessage(err)),
    onSettled: () => setUploading(null),
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.delete<void>(`/api/music/${encodeURIComponent(name)}`),
    onMutate: () => setFailure(null),
    onSuccess: libraryChanged,
    // The 409 names the projects that still have a clip on the file.
    onError: (err) => setFailure(errorMessage(err)),
  });

  const files = library.data?.files ?? [];
  const busy = upload.isPending || remove.isPending;

  return (
    <>
      <Modal
        open={open}
        // While the confirmation is up it owns the keyboard and the pointer:
        // Escape and a backdrop click answer IT, and the library stays where it
        // is behind it rather than closing underneath the question.
        onClose={() => { if (!dialog) onClose(); }}
        width={880}
        title={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Music size={18} /> Music library</span>}
        footer={<Button onClick={onClose}>Close</Button>}
      >
        <div style={{ display: "grid", gap: 12 }}>
          <div className="os-muted os-small">
            Shared by everyone in the studio. A file is referred to by its name, so an upload of a name that is
            already here is refused rather than replacing it, and a file cannot be deleted while any project's
            timeline still has a clip on it. Mono or stereo only, up to 100 MB.
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              style={{ display: "none" }}
              onChange={(e) => {
                const picked = e.target.files?.[0];
                // Cleared first: picking the same file twice must fire again.
                e.target.value = "";
                if (picked) upload.mutate(picked);
              }}
            />
            <Button
              variant="primary"
              icon={<Upload size={15} />}
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              Upload a track
            </Button>
            {uploading && (
              <span className="os-muted os-small">
                Uploading {uploading} — it is decoded once to measure it, which takes a moment for a long file.
              </span>
            )}
            {remove.isPending && <span className="os-muted os-small">Deleting…</span>}
          </div>

          {failure && <ErrorBox message={failure} />}
          {library.isError && <ErrorBox message={errorMessage(library.error) || "The library could not be read."} />}

          {library.isLoading ? (
            <Spinner label="Reading the library…" />
          ) : files.length === 0 ? (
            <div className="os-muted os-small">
              Nothing here yet — upload an mp3, wav, m4a, aac, ogg or flac and it will be available to every project.
            </div>
          ) : (
            <Table headers={["Name", "Length", "Size", "Channels", "Added by", "Added", ""]}>
              {files.map((file) => (
                <tr key={file.name}>
                  <td style={{ overflowWrap: "anywhere" }}>{file.name}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{formatDuration(file.duration)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{megabytes(file.size)}</td>
                  <td>{file.channels === 1 ? "mono" : "stereo"}</td>
                  <td>{file.uploaded_by}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{relativeTime(file.uploaded_at)}</td>
                  <td>
                    <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                      <Button
                        size="sm"
                        icon={<Plus size={14} />}
                        disabled={!canAdd || busy}
                        title={canAdd ? `Place this track on the Music lane at ${playhead}` : addTitle}
                        onClick={() => onAdd(file)}
                      >
                        Add at playhead
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        icon={<Trash2 size={14} />}
                        disabled={busy}
                        title="Remove it from the library — refused while any project still has a clip on it"
                        aria-label={`Delete ${file.name}`}
                        onClick={async () => {
                          const go = await confirm({
                            title: "Delete this track?",
                            message: (
                              <>
                                <strong>{file.name}</strong> is removed from the studio's library for everyone, along
                                with its waveform. There is no undo, and it can only be uploaded again from the
                                original file. Any project whose timeline still has a clip on it refuses the delete.
                              </>
                            ),
                            confirmLabel: "Delete",
                            danger: true,
                          });
                          if (go) remove.mutate(file.name);
                        }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
          )}

          <div className="os-muted os-small" style={{ display: "inline-flex", alignItems: "flex-start", gap: 6 }}>
            <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
            A clip is placed at the playhead and plays from the top of the track, for as long as the track lasts
            or until the end of the video — whichever comes first. Drag it along the lane to move it, drag its
            ends to trim it, and set its level and fades below the strip.
          </div>
        </div>
      </Modal>
      {/* A sibling of the library, never a child of it: the confirmation is its
          own fixed backdrop, and inside the modal's scrolling body it would be
          a dialog inside a dialog. */}
      {dialog}
    </>
  );
}
