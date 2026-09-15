import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Film, FolderOpen, Presentation, Trash2, Upload } from "lucide-react";
import { api, errorMessage } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { Button, Card, EmptyState, ErrorBox, PageHeader, Spinner, Table } from "../components/ui";
import { relativeTime } from "../lib/format";

interface Project {
  id: string;
  name: string;
  kind: "deck" | "pdf" | "video";
  source_filename: string;
  size_bytes: number;
  slide_count: number | null;
  created_at: string;
  /** Who imported it. Null on projects imported before ownership existed — those are admin-owned. */
  owner_id?: string | null;
  /** Their display name as it was at import, so it still reads after the account is gone. */
  owner_name?: string | null;
}

const ACCEPT = ".pptx,.pdf,.mp4,.mov,.mkv,.avi,.webm,.m4v";

const KIND: Record<Project["kind"], { icon: typeof FileText; label: string; color: string }> = {
  deck: { icon: Presentation, label: "Deck", color: "var(--brand)" },
  pdf: { icon: FileText, label: "PDF", color: "var(--warn)" },
  video: { icon: Film, label: "Video", color: "var(--info)" },
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export default function ProjectsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const fileRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Only admins see the list of everyone's projects, so only they get an Owner
  // column — for anyone else it would be a column of their own name.
  const showOwner = user?.role === "admin";

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<{ projects: Project[] }>("/api/projects"),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.upload<Project>("/api/projects/import", fd);
    },
    onSuccess: (p) => {
      setNotice(`Imported "${p.name}".`);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => setNotice(errorMessage(e)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.delete(`/api/projects/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
    onError: (e) => setNotice(errorMessage(e)),
  });

  const pick = () => fileRef.current?.click();
  const list = projects.data?.projects ?? [];
  const muted = { color: "var(--muted)" } as const;

  return (
    <>
      <PageHeader
        title="Projects"
        subtitle="Import a slide deck, PDF, or video to narrate and translate."
        actions={
          <Button variant="primary" icon={<Upload size={16} />} onClick={pick} disabled={importMut.isPending}>
            {importMut.isPending ? "Importing…" : "Import"}
          </Button>
        }
      />
      <input
        ref={fileRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) importMut.mutate(f);
          e.target.value = "";
        }}
      />

      {notice && (
        <div
          className="os-card"
          role="status"
          aria-live="polite"
          style={{ padding: "10px 14px", marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}
        >
          <span>{notice}</span>
          <button className="os-icon-btn" onClick={() => setNotice(null)} title="Dismiss" aria-label="Dismiss">×</button>
        </div>
      )}

      {projects.isLoading ? (
        <Card>
          <Spinner label="Loading projects…" />
        </Card>
      ) : projects.isError ? (
        <ErrorBox message={errorMessage(projects.error)} />
      ) : list.length === 0 ? (
        <Card>
          <EmptyState
            icon={<FolderOpen size={30} />}
            title="No projects yet"
            sub="Import a .pptx, .pdf, or video file to get started."
            action={<Button variant="primary" icon={<Upload size={16} />} onClick={pick}>Import a file</Button>}
          />
        </Card>
      ) : (
        <Card style={{ padding: 0 }}>
          <Table headers={["Name", "Type", "Slides", "Size", ...(showOwner ? ["Owner"] : []), "Imported", ""]}>
            {list.map((p) => {
              const k = KIND[p.kind];
              const Icon = k.icon;
              return (
                <tr key={p.id}>
                  <td style={{ fontWeight: 500 }}>
                    <Link to={`/projects/${p.id}`}>{p.name}</Link>
                    <div style={{ ...muted, fontSize: 12 }}>{p.source_filename}</div>
                  </td>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <Icon size={15} style={{ color: k.color }} />
                      {k.label}
                    </span>
                  </td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{p.slide_count ?? "—"}</td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{fmtBytes(p.size_bytes)}</td>
                  {showOwner && (
                    <td className="os-nowrap">
                      {p.owner_name || (
                        // Imported before ownership existed: admin-owned, never public.
                        <span className="os-dim" title="Imported before projects had owners — only admins can see it.">
                          unassigned
                        </span>
                      )}
                    </td>
                  )}
                  <td title={p.created_at} style={muted}>{relativeTime(p.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="os-icon-btn"
                      title="Delete project"
                      aria-label={`Delete ${p.name}`}
                      onClick={() => {
                        if (confirm(`Delete "${p.name}"? This removes its files.`)) deleteMut.mutate(p.id);
                      }}
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </Table>
        </Card>
      )}
    </>
  );
}
