// @vitest-environment jsdom
/**
 * The builder's Export to course… (W2c, C8), mounted for real under jsdom with the server faked: the button in the
 * toolbar opens the dialog, which previews the export; with unsaved changes it asks first ("Save before
 * exporting?"), saves, then opens - the export reads the SAVED walkthrough, as Play does; Cancel opens nothing.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SAVE_BEFORE_EXPORT } from "../lib/courseExport";
import type { Step, Walkthrough } from "../lib/walkthrough";
import WalkthroughBuilderPage from "./WalkthroughBuilder";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});

const WID = "abc123abc123";
const step = (id: string): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: null, kind: "tooltip", text: "", instruction: "", placement: "auto", chapter: "", note: "",
  source: { course: "demo", slug: "01-first", image: null, alt: "", launcher: null, capture_id: "cap000000001", annotated: false },
  anchor: { key: "s :: one.", ordinal: 1 },
});
const record = (over: Partial<Walkthrough> = {}): Walkthrough => ({
  id: WID, name: "Tour", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z",
  revision: 2, version: 1, steps: [step("aaaaaaaaaaaa")], source: { course: "demo", slug: "01-first", title: "Demo", version: "1" }, ...over,
});
const PLAN = {
  course: { id: "demo", title: "Demo", version: "1" }, workshop: { slug: "01-first", title: "First" }, guide: "demo/01-first/guide.md",
  guide_sha256: "a".repeat(64), placed: [], not_placed: [], skipped: [], removed: [], new_in_guide: [], files: [], changed: false, figures: 0,
  player: false, diff: "", allowed: false,
};

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [`/walkthroughs/${WID}`] },
          createElement(Routes, null, createElement(Route, { path: "/walkthroughs/:id", element: createElement(WalkthroughBuilderPage) })),
        ),
      ),
    );
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;
const plans = () => vi.mocked(api.post).mock.calls.filter(([path]) => String(path).endsWith("/export/course/plan"));

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.get).mockImplementation(async () => record() as never);
  vi.mocked(api.post).mockImplementation(async () => PLAN as never);
  vi.mocked(api.put).mockImplementation(async (_path: string, body: unknown) => {
    const sent = body as { name: string; revision: number; steps: Step[] };
    return record({ name: sent.name.trim(), revision: sent.revision + 1 }) as never;
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("Export to course…", () => {
  it("is in the toolbar and opens the dialog, which previews the export", async () => {
    await mount();
    const open = q("[data-export-course]") as HTMLButtonElement;
    expect(open.textContent).toContain("Export to course…");
    expect(open.title).toContain("demo/01-first");
    expect(q("[data-export-plan]")).toBeNull();
    await act(async () => { open.click(); });
    await settle();
    expect(plans()).toEqual([[`/api/walkthroughs/${WID}/export/course/plan`, { player: false }]]);
    expect(q("[data-export-plan]")).not.toBeNull();
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
  });

  it("with unsaved changes asks first, saves, then previews; Cancel opens nothing", async () => {
    await mount();
    const name = q("[data-walkthrough-name]") as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "Tour two");
      name.dispatchEvent(new Event("input", { bubbles: true }));
      name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    await act(async () => { (q("[data-export-course]") as HTMLButtonElement).click(); });
    await settle();
    expect(document.body.textContent).toContain(SAVE_BEFORE_EXPORT.title);
    await act(async () => { button("Cancel")!.click(); });
    await settle();
    expect(plans()).toEqual([]);
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
    await act(async () => { (q("[data-export-course]") as HTMLButtonElement).click(); });
    await settle();
    await act(async () => { button(SAVE_BEFORE_EXPORT.confirmLabel)!.click(); });
    await settle();
    expect(vi.mocked(api.put)).toHaveBeenCalledTimes(1);
    expect((vi.mocked(api.put).mock.calls[0][1] as { name: string }).name).toBe("Tour two");
    expect(plans().length).toBe(1);
    expect(q("[data-export-plan]")).not.toBeNull();
  });
});
