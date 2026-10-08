import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Camera, Clapperboard, FolderOpen, Languages, Upload, type LucideIcon } from "lucide-react";
import { api } from "../api/client";
import { Button, Card, EmptyState, ErrorBox, PageHeader } from "../components/ui";
import { useCaptureAvailable } from "../lib/useCaptureAvailable";

interface Health { status: string; version: string }

interface Feature {
  icon: LucideIcon;
  title: string;
  description: string;
  to: string;
  /** Route state the tile navigates with (the Capture tile asks Projects to open its dialog). */
  state?: Record<string, unknown>;
  /** Shown only where screen capture works (useCaptureAvailable). */
  desktopOnly?: boolean;
}

// Every tile opens Projects, because both generating and translating happen
// inside a project: you pick the deck or the video first. Generate Video and
// Translate sat here as disabled "Coming soon" cards left over from the
// scaffold long after both shipped - a dead button on a feature the app has.
// Capture (T3) opens Projects with its Capture dialog ready, and is shown only
// where capture works - the desktop app's own pages - by THE hide rule the
// Projects page asks too (lib/useCaptureAvailable.ts).
const FEATURES: Feature[] = [
  { icon: FolderOpen, title: "Projects", description: "Import a slide deck, PDF, or video and manage it.", to: "/projects" },
  { icon: Upload, title: "Import Video", description: "Bring in an existing video to re-voice or translate.", to: "/projects" },
  { icon: Clapperboard, title: "Generate Video", description: "Turn a slide deck or PDF into a narrated video. Open a deck project and use Generate video.", to: "/projects" },
  { icon: Languages, title: "Translate", description: "Re-voice a transcribed video in another language, or translate a deck's notes with the AI assistant.", to: "/projects" },
  {
    icon: Camera, title: "Capture", description: "Record your screen with sound, or grab a still; a recording becomes a video project.",
    to: "/projects", state: { openCapture: true }, desktopOnly: true,
  },
];

export default function DashboardPage() {
  const navigate = useNavigate();
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: () => api.get<Health>("/api/system/health"),
    staleTime: 30_000,
  });
  const canCapture = useCaptureAvailable();
  const features = FEATURES.filter((f) => !f.desktopOnly || canCapture);

  return (
    <>
      <PageHeader title="Media Studio Enterprise" subtitle="Create narrated and translated videos from your slide decks." />
      {/* Quiet when healthy: the backend's version lives in the sidebar and Settings.
          Only an unreachable API is worth a word here. */}
      {health.isError && <ErrorBox message="The API isn't responding — the backend may still be starting, or has stopped." />}
      {/* Four tiles in a row; with Capture's fifth, three and two (the theme's own grids, so the narrow
          breakpoints still apply). */}
      <div className={`os-grid ${features.length > 4 ? "os-grid-3" : "os-grid-4"}`}>
        {features.map((f) => {
          const Icon = f.icon;
          return (
            <Card key={f.title} className="os-tile">
              <EmptyState
                icon={<Icon size={30} />}
                title={f.title}
                sub={f.description}
                action={<Button variant="primary" size="sm" onClick={() => navigate(f.to, f.state ? { state: f.state } : undefined)}>Open</Button>}
              />
            </Card>
          );
        })}
      </div>
    </>
  );
}
