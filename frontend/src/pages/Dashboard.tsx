import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Clapperboard, FolderOpen, Languages, Upload, type LucideIcon } from "lucide-react";
import { api } from "../api/client";
import { Button, Card, EmptyState, ErrorBox, PageHeader } from "../components/ui";

interface Health { status: string; version: string }

const FEATURES: { icon: LucideIcon; title: string; description: string; to?: string }[] = [
  { icon: FolderOpen, title: "Projects", description: "Import a slide deck, PDF, or video and manage it.", to: "/projects" },
  { icon: Upload, title: "Import Video", description: "Bring in an existing video to re-voice or translate.", to: "/projects" },
  { icon: Clapperboard, title: "Generate Video", description: "Turn a PPTX into a narrated video with generated speech." },
  { icon: Languages, title: "Translate", description: "Produce a localised voice track in another language." },
];

export default function DashboardPage() {
  const navigate = useNavigate();
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: () => api.get<Health>("/api/system/health"),
    staleTime: 30_000,
  });

  return (
    <>
      <PageHeader title="Media Studio Enterprise" subtitle="Create narrated and translated videos from your slide decks." />
      {/* Quiet when healthy: the backend's version lives in the sidebar and Settings.
          Only an unreachable API is worth a word here. */}
      {health.isError && <ErrorBox message="The API isn't responding — the backend may still be starting, or has stopped." />}
      <div className="os-grid os-grid-4">
        {FEATURES.map((f) => {
          const Icon = f.icon;
          return (
            <Card key={f.title}>
              <EmptyState
                icon={<Icon size={30} />}
                title={f.title}
                sub={f.description}
                action={f.to
                  ? <Button variant="primary" size="sm" onClick={() => navigate(f.to!)}>Open</Button>
                  : <button className="os-btn os-btn-secondary os-btn-sm" disabled>Coming soon</button>}
              />
            </Card>
          );
        })}
      </div>
    </>
  );
}
