// @vitest-environment jsdom
/**
 * The builder's Make a video… (W3), mounted for real under jsdom with the server faked: the button in the
 * toolbar beside Export to course… opens the dialog; with unsaved changes it asks first ("Save before making the
 * video?"), saves, then opens - the video is made from the SAVED walkthrough, as Play and Export read it; Cancel
 * opens nothing; a walkthrough with no steps has the button greyed out.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SAVE_BEFORE_VIDEO, type Step, type Walkthrough } from "../lib/walkthrough";
import WalkthroughBuilderPage from "./WalkthroughBuilder";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});

const WID = "abc123abc123";
const step = (id: string): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: { x: 1, y: 1, width: 20, height: 20 }, kind: "tooltip", text: "Open it", instruction: "",
  placement: "auto", chapter: "", note: "", source: null, click: "left", marker: "box",
});
const record = (over: Partial<Walkthrough> = {}): Walkthrough => ({
  id: WID, name: "Tour", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z",
  revision: 2, version: 1, steps: [step("aaaaaaaaaaaa")], ...over,
});

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

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => (path === "/api/projects" ? { projects: [] } : record()) as never);
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

describe("Make a video…", () => {
  it("is in the toolbar after Export to course… and opens the dialog, saving nothing", async () => {
    await mount();
    const open = q("[data-make-video]") as HTMLButtonElement;
    expect(open.textContent).toContain("Make a video…");
    expect(open.disabled).toBe(false);
    const toolbar = q("[data-builder-toolbar]")!;
    const buttons = [...toolbar.querySelectorAll("button")].map((b) => (b.textContent || "").trim());
    expect(buttons.indexOf("Make a video…")).toBe(buttons.indexOf("Export to course…") + 1);
    expect(q("[data-video-make]")).toBeNull();
    await act(async () => { open.click(); });
    await settle();
    expect(q("[data-video-make]")).not.toBeNull();
    expect((q("[data-video-name]") as HTMLInputElement).value).toBe("Tour");
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
  });

  it("with unsaved changes asks first, saves, then opens with the saved name; Cancel opens nothing", async () => {
    await mount();
    const name = q("[data-walkthrough-name]") as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "Tour two");
      name.dispatchEvent(new Event("input", { bubbles: true }));
      name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });
    await act(async () => { (q("[data-make-video]") as HTMLButtonElement).click(); });
    await settle();
    expect(document.body.textContent).toContain(SAVE_BEFORE_VIDEO.title);
    await act(async () => { button("Cancel")!.click(); });
    await settle();
    expect(q("[data-video-make]")).toBeNull();
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
    await act(async () => { (q("[data-make-video]") as HTMLButtonElement).click(); });
    await settle();
    await act(async () => { button(SAVE_BEFORE_VIDEO.confirmLabel)!.click(); });
    await settle();
    expect(vi.mocked(api.put)).toHaveBeenCalledTimes(1);
    expect((vi.mocked(api.put).mock.calls[0][1] as { name: string }).name).toBe("Tour two");
    expect(q("[data-video-make]")).not.toBeNull();
    expect((q("[data-video-name]") as HTMLInputElement).value).toBe("Tour two");
  });

  it("is greyed out while the walkthrough has no steps", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => (path === "/api/projects" ? { projects: [] } : record({ steps: [] })) as never);
    await mount();
    expect((q("[data-make-video]") as HTMLButtonElement).disabled).toBe(true);
  });
});
