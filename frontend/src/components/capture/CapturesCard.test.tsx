// @vitest-environment jsdom
/**
 * The Captures card (T3 / T3b), mounted under jsdom with the server faked: every still has Annotate,
 * which opens the editor's route; an annotated still shows the badge, its thumbnail busts the cache with
 * the annotation's time and takes the output size; and Add to deck… opens the SHARED dialog
 * (components/annotate/AddToDeckDialog), which adds the still - the same component the editor opens.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Capture } from "../../lib/capture";
import { CapturesCard } from "./CapturesCard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), delete: vi.fn(), blob: vi.fn() } };
});

const plain: Capture = {
  id: "plain0000001", name: "Plain still", kind: "region", created_at: "2026-10-10T10:00:00Z", width: 400, height: 300, cursor: false, size_bytes: 10,
  annotated: false, output_width: 400, output_height: 300,
};
const marked: Capture = {
  id: "marked000001", name: "Marked still", kind: "window", created_at: "2026-10-10T09:00:00Z", width: 400, height: 300, cursor: true, size_bytes: 10,
  annotated: true, annotated_at: "2026-10-10T12:34:56Z", output_width: 100, output_height: 300,
};

let root: Root | null = null;
let host: HTMLDivElement | null = null;
const notices: string[] = [];

function EditorStub() {
  const { id } = useParams();
  return createElement("div", { "data-editor": id }, `editor ${id}`);
}

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: ["/projects"] },
          createElement(Routes, null,
            createElement(Route, { path: "/projects", element: createElement(CapturesCard, { onNotice: (t: string) => notices.push(t) }) }),
            createElement(Route, { path: "/captures/:id/annotate", element: createElement(EditorStub) }),
          ),
        ),
      ),
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}

const card = (id: string) => document.querySelector(`[data-capture="${id}"]`) as HTMLElement;
const buttonIn = (el: ParentNode, text: string) => [...el.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;

beforeEach(() => {
  notices.length = 0;
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === "/api/captures") return { captures: [plain, marked] } as never;
    if (path === "/api/projects") return { projects: [{ id: "deck00000001", name: "Launch deck", kind: "deck" }] } as never;
    return {} as never;
  });
  vi.mocked(api.post).mockImplementation(async () => ({ index: 3, slide_count: 4 }) as never);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("the Captures card", () => {
  it("offers Annotate on every still, which opens the editor's route", async () => {
    await mount();
    expect(buttonIn(card(plain.id), "Annotate")).not.toBeNull();
    expect(buttonIn(card(marked.id), "Annotate")).not.toBeNull();
    await act(async () => { buttonIn(card(plain.id), "Annotate")!.click(); });
    expect(document.querySelector(`[data-editor="${plain.id}"]`)).not.toBeNull();
  });

  it("shows the annotated badge, the output size and a thumbnail busted by the annotation's time on an annotated still only", async () => {
    await mount();
    expect(card(marked.id).textContent).toContain("annotated");
    expect(card(marked.id).textContent).toContain("100×300 (cropped from 400×300)");
    expect(card(plain.id).textContent).not.toContain("annotated");
    expect(card(plain.id).textContent).toContain("400×300");
    const markedImg = card(marked.id).querySelector("img")!;
    const plainImg = card(plain.id).querySelector("img")!;
    expect(markedImg.getAttribute("src")).toBe(`/api/captures/${marked.id}/image?v=${encodeURIComponent(marked.annotated_at!)}`);
    expect(plainImg.getAttribute("src")).toBe(`/api/captures/${plain.id}/image?v=${encodeURIComponent(plain.created_at)}`);
    expect(markedImg.style.aspectRatio).toBe("100 / 300");
  });

  it("Add to deck… opens the shared dialog and adds the still as the deck's last slide", async () => {
    await mount();
    await act(async () => { buttonIn(card(marked.id), "Add to deck…")!.click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); }); // the deck list answers
    const dialog = document.querySelector("[role=dialog]")!;
    expect(dialog.textContent).toContain("Add to deck");
    const select = dialog.querySelector("select") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Choose a deck…", "Launch deck"]);
    expect(buttonIn(dialog, "Add as last slide")!.disabled).toBe(true);
    await act(async () => {
      select.value = "deck00000001";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { buttonIn(dialog, "Add as last slide")!.click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(`/api/captures/${marked.id}/add-to-deck`, { project_id: "deck00000001" });
    expect(notices).toEqual(["Added as slide 4 of 4."]);
    expect(document.querySelector("[role=dialog]")).toBeNull();
  });
});
