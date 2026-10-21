// @vitest-environment jsdom
/**
 * The Shell's sidebar: Dashboard, Projects, Docs, Settings - no entry for a Dashboard tile's pages (W2c, C9: the
 * Walkthroughs entry went, as Captures never had one; the tile is the way in). The walkthroughs' pages light the
 * Dashboard, so the sidebar always says where you are; the routes are untouched.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Shell } from "./Shell";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return {
    ...real,
    api: {
      ...real.api,
      get: vi.fn(async (path: string) => (path === "/api/recordings/job" ? { active_job: null } : { status: "ok", version: "test" })),
      post: vi.fn(async () => ({})),
      delete: vi.fn(async () => undefined),
    },
  };
});

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount(at: string) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const page = (name: string) => createElement("div", { "data-page": name }, name);
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [at] },
          createElement(Routes, null,
            createElement(Route, { element: createElement(Shell) },
              createElement(Route, { index: true, element: page("dashboard") }),
              createElement(Route, { path: "/projects", element: page("projects") }),
              createElement(Route, { path: "/walkthroughs", element: page("list") }),
              createElement(Route, { path: "/walkthroughs/:id", element: page("builder") }),
              createElement(Route, { path: "/walkthroughsish", element: page("other") }),
            ),
          ),
        ),
      ),
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}

const items = () => [...host!.querySelectorAll("a.os-nav-item")];
const active = () => items().filter((a) => a.className.includes("active")).map((a) => (a.textContent || "").trim());

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the sidebar", () => {
  it("has Dashboard, Projects, Docs and Settings - no Walkthroughs entry", async () => {
    await mount("/");
    expect(items().map((a) => (a.textContent || "").trim())).toEqual(["Dashboard", "Projects", "Docs", "Settings"]);
    expect(items().map((a) => a.getAttribute("href"))).toEqual(["/", "/projects", "/docs", "/settings"]);
    expect(active()).toEqual(["Dashboard"]);
  });

  it("lights the Dashboard on the walkthroughs' pages, whose routes stay", async () => {
    await mount("/walkthroughs");
    expect(host!.querySelector("[data-page]")!.getAttribute("data-page")).toBe("list");
    expect(active()).toEqual(["Dashboard"]);
    await act(async () => root?.unmount());
    host?.remove();
    await mount("/walkthroughs/abc123abc123");
    expect(host!.querySelector("[data-page]")!.getAttribute("data-page")).toBe("builder");
    expect(active()).toEqual(["Dashboard"]);
  });

  it("lights only its own entry elsewhere, and a page that merely starts with the same letters lights nothing", async () => {
    await mount("/projects");
    expect(active()).toEqual(["Projects"]);
    await act(async () => root?.unmount());
    host?.remove();
    await mount("/walkthroughsish");
    expect(active()).toEqual([]);
  });
});
