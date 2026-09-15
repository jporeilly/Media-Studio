import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Clapperboard, FolderOpen, Languages, Upload, type LucideIcon } from "lucide-react";
import { api } from "../api/client";
import { Button, Card, EmptyState, ErrorBox, PageHeader } from "../components/ui";

interface Health { status: string; version: string }

// Every tile opens Projects, because both generating and translating happen
// inside a project: you pick the deck or the video first. Generate Video and
// Translate sat here as disabled "Coming soon" cards left over from the
// scaffold long after both shipped - a dead button on a feature the app has.
const FEATURES: { icon: LucideIcon; title: string; description: string; to: string }[] = [
  { icon: FolderOpen, title: "Projects", description: "Import a slide deck, PDF, or video and manage it.", to: "/projects" },
  { icon: Upload, title: "Import Video", description: "Bring in an existing video to re-voice or translate.", to: "/projects" },
  { icon: Clapperboard, title: "Generate Video", description: "Turn a slide deck or PDF into a narrated video. Open a deck project and use Generate video.", to: "/projects" },
  { icon: Languages, title: "Translate", description: "Re-voice a transcribed video in another language, or translate a deck's notes with the AI assistant.", to: "/projects" },
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
            <Card key={f.title} className="os-tile">
              <EmptyState
                icon={<Icon size={30} />}
                title={f.title}
                sub={f.description}
                action={<Button variant="primary" size="sm" onClick={() => navigate(f.to)}>Open</Button>}
              />
            </Card>
          );
        })}
      </div>
    </>
  );
}
