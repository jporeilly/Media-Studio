// @vitest-environment jsdom
/**
 * The Dashboard's Walkthroughs tile (W1): present everywhere - in a browser and inside the desktop shell
 * alike, since it touches no screen - with its wording, and its Open goes to /walkthroughs with no state.
 * The grid stays balanced: five tiles in a browser, six in the shell, both as three a row.
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
function Probe() {
  const loc = useLocation();
  seen = { pathname: loc.pathname, state: loc.state };
  return createElement("div", { id: "probe" }, "there");
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
            createElement(Route, { path: "/walkthroughs", element: createElement(Probe) }),
            createElement(Route, { path: "/projects", element: createElement(Probe) }),
          ),
        ),
      ),
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}

const tiles = () => [...host!.querySelectorAll(".os-tile")];
const titles = () => tiles().map((t) => t.textContent || "");
const walkthroughsTile = () => tiles().find((t) => (t.textContent || "").startsWith("Walkthroughs"))!;

function inShell() {
  const w = window as unknown as Record<string, unknown>;
  w.isTauri = true;
  w.__TAURI__ = { core: { invoke: async () => undefined }, event: { listen: async () => () => {} } };
}

beforeEach(() => { seen = null; });

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  delete (window as { isTauri?: unknown }).isTauri;
  delete (window as { __TAURI__?: unknown }).__TAURI__;
});

describe("the Dashboard's Walkthroughs tile", () => {
  it("is there in a browser, with its wording, as the fifth tile of a three-a-row grid", async () => {
    await mount();
    expect(titles()).toHaveLength(5);
    expect(walkthroughsTile().textContent).toContain("Turn stills into a click-through walkthrough");
    expect(host!.querySelector(".os-grid")!.className).toContain("os-grid-3");
  });

  it("is there inside the desktop shell too, beside Capture, six tiles three a row", async () => {
    inShell();
    await mount();
    expect(titles()).toHaveLength(6);
    expect(titles().some((t) => t.startsWith("Capture"))).toBe(true);
    expect(walkthroughsTile()).toBeDefined();
    expect(host!.querySelector(".os-grid")!.className).toContain("os-grid-3");
  });

  it("opens /walkthroughs with no state", async () => {
    await mount();
    await act(async () => { (walkthroughsTile().querySelector("button") as HTMLButtonElement).click(); });
    expect(seen).toEqual({ pathname: "/walkthroughs", state: null });
  });
});
