// @vitest-environment jsdom
/**
 * The Settings page's update poller as the user meets it (E7 fix round, the review's m1 and m2): the real
 * `SettingsPage` under jsdom against a scripted server (`fetch`) that can be up, down (every request fails to
 * connect) or restarted (every job forgotten), with the intervals shortened (lib/jobs mocked). Pinned: an
 * update already running when the page opens is found and shown; a restart under it leaves the restart line on
 * the Updates card and gives the buttons back - also when the restart first shows as a server that cannot be
 * reached at all, which is how a real one looks from the page.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "./Settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({ role: "editor" }));
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", username: "viewer", display_name: "Viewer", role: hoisted.role } }),
}));
vi.mock("../lib/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/jobs")>()),
  JOB_POLL_MS: 20,
  PROJECT_JOB_POLL_MS: 40,
}));

type Phase = "up" | "down" | "restarted";
const server = {
  phase: "up" as Phase,
  update: null as null | {
    id: string; kind: string; status: string; progress: number; message: string; user_id: null; result?: unknown;
  },
  calls: [] as { url: string; phase: Phase }[],
};

function reply(status: number, body: unknown) {
  return { status, ok: status >= 200 && status < 300, statusText: "", text: async () => JSON.stringify(body), blob: async () => new Blob() };
}

beforeEach(() => {
  hoisted.role = "editor";
  server.phase = "up";
  server.update = { id: "U", kind: "update", status: "running", progress: 0.5, message: "Installing Python dependencies…", user_id: null };
  server.calls = [];
  vi.stubGlobal("fetch", async (input: unknown) => {
    const url = String(input);
    server.calls.push({ url, phase: server.phase });
    if (server.phase === "down") throw new TypeError("Failed to fetch");
    const lost = server.phase === "restarted";
    if (url === "/api/system/update") {
      return reply(200, { error: null, commit: "abc1234", branch: "main", update_available: false, behind: 0, latest: null });
    }
    if (url === "/api/system/update/job") {
      const job = lost || server.update?.status === "done" ? null : server.update;
      return reply(200, { active_job: job ? { id: job.id, kind: job.kind, status: job.status, user_id: job.user_id } : null });
    }
    if (url === "/api/jobs/U") return !lost && server.update ? reply(200, server.update) : reply(404, { detail: "Job not found." });
    return reply(404, { detail: `no route ${url}` });
  });
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;
afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

function mount() {
  container = document.createElement("div");
  document.body.appendChild(container);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15_000 } } });
  root = createRoot(container);
  act(() => {
    root!.render(createElement(QueryClientProvider, { client: qc }, createElement(MemoryRouter, null, createElement(SettingsPage))));
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function pass(ms: number) {
  await act(async () => {
    await sleep(ms);
  });
}
async function until(check: () => boolean, what: string, ms = 3000) {
  for (let waited = 0; waited < ms; waited += 15) {
    if (check()) return;
    await pass(15);
  }
  expect(check(), what).toBe(true);
}
function updatesCard(): HTMLElement | null {
  const heading = [...(container?.querySelectorAll("h3.os-card-title") ?? [])].find((h) => h.textContent?.trim() === "Updates");
  return (heading?.closest("section") as HTMLElement | null) ?? null;
}
const cardText = () => updatesCard()?.textContent ?? "";
const polls = (phase?: Phase) => server.calls.filter((c) => c.url === "/api/jobs/U" && (!phase || c.phase === phase)).length;
const RESTART_LINE = "The job stopped when the server restarted. Start it again.";

describe("the Updates card", () => {
  it("finds an update already running when the page opens, and shows its progress", async () => {
    mount();
    await until(() => cardText().includes("Installing Python dependencies…"), "the running update is found on load");
    expect(cardText()).not.toContain("Check for updates");
  });

  it("says the update was lost when the server restarted under it, and gives the buttons back", async () => {
    mount();
    await until(() => cardText().includes("Installing Python dependencies…"), "followed");
    server.phase = "restarted";
    await until(() => cardText().includes(RESTART_LINE), "the restart line");
    expect(cardText()).toContain("Check for updates");
    expect(polls("restarted")).toBe(1);
  });

  it("gets the restart line too when the restart first shows as a server that cannot be reached", async () => {
    mount();
    await until(() => cardText().includes("Installing Python dependencies…"), "followed");
    server.phase = "down";
    await until(() => cardText().includes("Could not reach the job"), "the page stops following while the server is down");
    await pass(300);
    expect(polls("down")).toBeLessThanOrEqual(3);
    server.phase = "restarted";
    await until(() => cardText().includes(RESTART_LINE), "asked after once more when the server answers again");
    expect(cardText()).not.toContain("Could not reach the job");
    expect(polls("restarted")).toBe(1);
  });

  it("tells an editor watching an administrator's update that an administrator restarts, and keeps the card's buttons", async () => {
    mount();
    await until(() => cardText().includes("Installing Python dependencies…"), "followed");
    Object.assign(server.update!, { status: "done", progress: 1, message: "Complete", result: { updated_from: "abc1234", updated_to: "def5678" } });
    await until(() => cardText().includes("Update applied (abc1234 → def5678)"), "the applied line");
    expect(cardText()).toContain("An administrator restarts the backend to finish.");
    expect(cardText(), "no Restart now: the route refuses anyone but an administrator").not.toContain("Restart now");
    expect(cardText()).toContain("Check for updates");
  });

  it("offers Restart now to an administrator once the update is applied", async () => {
    hoisted.role = "admin";
    mount();
    await until(() => cardText().includes("Installing Python dependencies…"), "followed");
    Object.assign(server.update!, { status: "done", progress: 1, message: "Complete", result: { updated_from: "abc1234", updated_to: "def5678" } });
    await until(() => cardText().includes("Restart now"), "the administrator's Restart now");
    expect(cardText()).toContain("Update applied (abc1234 → def5678). Restart to finish.");
  });
});
