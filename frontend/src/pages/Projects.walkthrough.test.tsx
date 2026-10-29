// @vitest-environment jsdom
/**
 * The Projects list with a walkthrough project (W3), mounted for real under jsdom with the server faked: the
 * row carries the kind's own label, "Walkthrough", its step count and the link to the project, beside a deck's row.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import ProjectsPage from "./Projects";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fake = vi.hoisted(() => ({
  capture: {
    state: "idle", elapsedMs: 0, message: null, savingJobId: null, clearSavingJob: () => {}, liveRecordingId: null as string | null,
    saveRunning: false, hotkeysLive: false, startRecording: async () => {}, takeStill: async () => null, pause: () => {},
    resume: () => {}, stop: () => {}, cancelCountdown: () => {}, dismiss: () => {}, lastStillAt: 0,
  },
}));
vi.mock("../context/CaptureContext", () => ({ useCapture: () => fake.capture }));
vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", username: "ed", role: "editor" } }) }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), delete: vi.fn() } };
});

const PROJECTS = [
  { id: "p1", name: "Your First Win", kind: "walkthrough", source_filename: "Your-First-Win.walkthrough.json", size_bytes: 2048, slide_count: 12,
    created_at: "2026-10-22T14:02:00Z", owner_id: "u1", owner_name: "Ed", walkthrough_id: "abc123abc123", walkthrough_name: "Your First Win" },
  { id: "p2", name: "Deck", kind: "deck", source_filename: "deck.pptx", size_bytes: 4096, slide_count: 3, created_at: "2026-10-22T15:00:00Z", owner_id: "u1", owner_name: "Ed" },
];

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: ["/projects"] }, createElement(ProjectsPage)),
      ),
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === "/api/projects") return { projects: PROJECTS } as never;
    if (path.startsWith("/api/captures")) return { captures: [] } as never;
    return {} as never;
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("a walkthrough project in the list", () => {
  it("is labelled Walkthrough with its own icon, its step count and the link to its page", async () => {
    await mount();
    const rows = [...document.querySelectorAll("tbody tr")];
    expect(rows.length).toBe(2);
    const row = rows.find((r) => (r.textContent || "").includes("Your First Win"))!;
    const cells = [...row.querySelectorAll("td")].map((td) => (td.textContent || "").trim());
    expect(cells[1]).toBe("Walkthrough");
    expect(cells[2]).toBe("12");
    expect(row.querySelector("td svg")).not.toBeNull();
    expect((row.querySelector("a") as HTMLAnchorElement).getAttribute("href")).toBe("/projects/p1");
    expect(row.textContent).toContain("Your-First-Win.walkthrough.json");
    const deck = rows.find((r) => (r.textContent || "").includes("Deck"))!;
    expect([...deck.querySelectorAll("td")].map((td) => (td.textContent || "").trim())[1]).toBe("Deck");
  });
});
