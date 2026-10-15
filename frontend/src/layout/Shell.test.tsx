// @vitest-environment jsdom
/**
 * The Shell's sidebar (W1): a Walkthroughs entry sits right after Projects, and it links to /walkthroughs.
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

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: ["/walkthroughs"] },
          createElement(Routes, null,
            createElement(Route, { element: createElement(Shell) },
              createElement(Route, { path: "/walkthroughs", element: createElement("div", { "data-page": "" }, "the list") }),
            ),
          ),
        ),
      ),
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the sidebar", () => {
  it("has a Walkthroughs entry after Projects, linking to /walkthroughs and active there", async () => {
    await mount();
    const items = [...host!.querySelectorAll("a.os-nav-item")];
    const labels = items.map((a) => (a.textContent || "").trim());
    expect(labels).toEqual(["Dashboard", "Projects", "Walkthroughs", "Docs", "Settings"]);
    const entry = items[2];
    expect(entry.getAttribute("href")).toBe("/walkthroughs");
    expect(entry.className).toContain("active");
    expect(items[1].className).not.toContain("active");
    expect(host!.querySelector("[data-page]")).not.toBeNull();
  });
});
