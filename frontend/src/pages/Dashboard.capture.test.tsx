// @vitest-environment jsdom
/**
 * The Dashboard's Capture tile (T3, the owner's addendum): present inside the desktop shell where the page
 * can talk to it, absent in a browser and in a shell whose capability refuses the page - THE hide rule the
 * Projects page asks too (`captureAvailable`) - and its Open goes to Projects WITH the route state that asks
 * the page to open its Capture dialog.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "./Dashboard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(async () => ({ status: "ok", version: "test" })) } };
});

let seen: { pathname: string; state: unknown } | null = null;
function ProjectsProbe() {
  const loc = useLocation();
  seen = { pathname: loc.pathname, state: loc.state };
  return createElement("div", { id: "projects-probe" }, "projects");
}

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
        createElement(MemoryRouter, { initialEntries: ["/"] },
          createElement(Routes, null,
            createElement(Route, { path: "/", element: createElement(DashboardPage) }),
            createElement(Route, { path: "/projects", element: createElement(ProjectsProbe) }),
          ),
        ),
      ),
    );
  });
  // The shell's answer to the capability probe.
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}

const tileTitles = () => [...host!.querySelectorAll(".os-tile")].map((t) => t.textContent || "");

/** Inside the shell, with the bridge the capture commands need. */
function inShell(bridge: boolean | "refused" = true) {
  const w = window as unknown as Record<string, unknown>;
  w.isTauri = true;
  if (bridge === "refused") {
    w.__TAURI__ = { core: { invoke: async () => { throw new Error("capture_monitors not allowed"); } }, event: { listen: async () => () => {} } };
  } else if (bridge) {
    w.__TAURI__ = { core: { invoke: async () => undefined }, event: { listen: async () => () => {} } };
  }
}

beforeEach(() => {
  seen = null;
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  delete (window as { isTauri?: unknown }).isTauri;
  delete (window as { __TAURI__?: unknown }).__TAURI__;
});

describe("the Dashboard's Capture tile", () => {
  it("is absent in a browser: four tiles, none of them Capture", async () => {
    await mount();
    const titles = tileTitles();
    expect(titles).toHaveLength(4);
    expect(titles.some((t) => t.startsWith("Capture"))).toBe(false);
  });

  it("is present inside the desktop shell, with the tiles' own wording", async () => {
    inShell();
    await mount();
    const titles = tileTitles();
    expect(titles).toHaveLength(5);
    const tile = titles.find((t) => t.startsWith("Capture"));
    expect(tile).toContain("Record your screen with sound, or grab a still; a recording becomes a video project.");
  });

  it("is absent in a shell whose capability refuses the page, as the Projects page's button is", async () => {
    inShell(false);
    await mount();
    expect(tileTitles()).toHaveLength(4);
  });

  it("is absent in a shell whose capability refuses the page although the bridge is there", async () => {
    inShell("refused");
    await mount();
    expect(tileTitles()).toHaveLength(4);
  });

  it("opens Projects with the state that asks for the Capture dialog", async () => {
    inShell();
    await mount();
    const tile = [...host!.querySelectorAll(".os-tile")].find((t) => (t.textContent || "").startsWith("Capture"))!;
    await act(async () => {
      (tile.querySelector("button") as HTMLButtonElement).click();
    });
    expect(seen).toEqual({ pathname: "/projects", state: { openCapture: true } });
  });

  it("leaves the other tiles' Open without state", async () => {
    inShell();
    await mount();
    const tile = [...host!.querySelectorAll(".os-tile")].find((t) => (t.textContent || "").startsWith("Projects"))!;
    await act(async () => {
      (tile.querySelector("button") as HTMLButtonElement).click();
    });
    expect(seen).toEqual({ pathname: "/projects", state: null });
  });
});
