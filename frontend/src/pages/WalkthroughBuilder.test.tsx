// @vitest-environment jsdom
/**
 * The walkthrough builder page (W1), mounted for real under jsdom with the server faked: the step list and
 * the selection, Add step from the picker of stills (the server copies the picture; the step lands after the
 * selected one), the hotspot drawn, moved, resized by a handle and removed on the picture (the annotation
 * editor's crop maths), reorder by the buttons and by a drag, delete with a confirm, the properties, Save
 * (Ctrl+S) sending the WHOLE document with the revision it loaded and taking the next one, a 409 shown as the
 * server's line with Reload, Ctrl+Z / Ctrl+Y, the leave guard, Play asking to save first, Replace picture
 * keeping the hotspot clamped, and the walkthrough gone.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { Capture } from "../lib/capture";
import { type Step, type Walkthrough, GONE_MESSAGE, SAVE_BEFORE_PLAY, STALE_MESSAGE, UNSAVED_ASK } from "../lib/walkthrough";
import WalkthroughBuilderPage from "./WalkthroughBuilder";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});

const WID = "abc123abc123";
const A = "aaaaaaaaaaaa", B = "bbbbbbbbbbbb", N = "cccccccccccc";
const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: null, kind: "tooltip", text: "", instruction: "", placement: "auto", chapter: "", source: null, ...over,
});
/** What the server stores of a sent document: the name and the chapters stripped, the texts with LF line breaks. */
const asStored = (sent: { name: string; steps: Step[] }) => ({
  name: sent.name.trim(),
  steps: sent.steps.map((s) => ({ ...s, chapter: s.chapter.trim(), text: s.text.replace(/\r\n/g, "\n"), instruction: (s.instruction ?? "").replace(/\r\n/g, "\n") })),
});
const record = (over: Partial<Walkthrough> = {}): Walkthrough => ({
  id: WID, name: "Tour", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T10:00:00Z",
  revision: 2, version: 1, steps: [step(A), step(B)], ...over,
});
const capture: Capture = {
  id: "cap000000001", name: "Dialog", kind: "window", created_at: "2026-10-12T09:00:00Z", width: 200, height: 100, cursor: false, size_bytes: 10,
  annotated: true, annotated_at: "2026-10-12T09:30:00Z", output_width: 200, output_height: 100,
};

let root: Root | null = null;
let host: HTMLDivElement | null = null;
let qc: QueryClient;

function ListStub() {
  const location = useLocation();
  const notice = (location.state as { notice?: string } | null)?.notice;
  return createElement("div", { "data-list": "" }, notice ? createElement("p", { "data-notice": "" }, notice) : "list");
}
function PlayStub() {
  const { id } = useParams();
  const location = useLocation();
  return createElement("div", { "data-play": id, "data-search": location.search, "data-state": JSON.stringify(location.state) }, "play");
}

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [`/walkthroughs/${WID}`] },
          createElement(Routes, null,
            createElement(Route, { path: "/walkthroughs/:id", element: createElement(WalkthroughBuilderPage) }),
            createElement(Route, { path: "/walkthroughs/:id/play", element: createElement(PlayStub) }),
            createElement(Route, { path: "/walkthroughs", element: createElement(ListStub) }),
          ),
        ),
      ),
    );
  });
  await settle();
}

async function settle(ms = 30) {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}

const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const status = () => (q("[data-builder-status]")?.textContent || "").trim();
const steps = () => [...document.querySelectorAll("[data-step]")].map((el) => el.getAttribute("data-step"));
const selectedStep = () => q("[data-step][aria-selected=true]")?.getAttribute("data-step") ?? null;
const hotspotLine = () => (q("[data-hotspot-line]")?.textContent || "").trim();
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;
const labelled = (label: string) => document.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement | null;
const surface = () => q("[data-walkthrough-surface]")!;
const key = (k: string, init: KeyboardEventInit = {}, target: EventTarget = window) =>
  act(async () => { target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...init })); });
const pointer = (type: string, x: number, y: number) =>
  act(async () => { surface().dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true, cancelable: true, button: 0 })); });
async function drag(x1: number, y1: number, x2: number, y2: number) {
  await pointer("pointerdown", x1, y1);
  await pointer("pointermove", x2, y2);
  await pointer("pointerup", x2, y2);
}
function setValue(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}
const puts = () => vi.mocked(api.put).mock.calls;

beforeEach(() => {
  // The middle column has a size, so the picture fits at 100 % and pointer coordinates are picture pixels.
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 1000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === `/api/walkthroughs/${WID}`) return record() as never;
    if (path === "/api/captures") return { captures: [capture] } as never;
    return {} as never;
  });
  vi.mocked(api.put).mockImplementation(async (_path: string, body: unknown) => {
    const sent = body as { name: string; revision: number; steps: Step[] };
    return record({ ...asStored(sent), revision: sent.revision + 1 }) as never;
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("opening", () => {
  it("lists the steps with the first selected, shows its picture busted by the revision, and nothing unsaved", async () => {
    await mount();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith(`/api/walkthroughs/${WID}`);
    expect(steps()).toEqual([A, B]);
    expect(selectedStep()).toBe(A);
    expect((q("[data-walkthrough-surface] img") as HTMLImageElement).getAttribute("src")).toBe(`/api/walkthroughs/${WID}/steps/${A}/image?v=2`);
    expect(status()).toBe("Saved");
    expect(button("Save")!.disabled).toBe(true);
    expect(hotspotLine()).toContain("No hotspot");
  });

  it("sends the user to the list with a notice when the walkthrough is gone", async () => {
    vi.mocked(api.get).mockImplementation(async () => { throw new ApiError(404, "No such walkthrough."); });
    await mount();
    await settle();
    expect(q("[data-list]")).not.toBeNull();
    expect(q("[data-notice]")!.textContent).toBe(GONE_MESSAGE);
  });
});

describe("the steps", () => {
  it("Add step… offers the stills with the annotated badge, the server copies the picture, and the step lands after the selected one with nothing unsaved", async () => {
    vi.mocked(api.post).mockImplementation(async () => record({ revision: 3, steps: [step(A), step(N, { source: { capture_id: capture.id, annotated: true } }), step(B)] }) as never);
    await mount();
    await act(async () => { q("[data-add-step]")!.click(); });
    await settle();
    const dialog = q("[role=dialog]")!;
    expect(dialog.textContent).toContain("Add step");
    expect(dialog.textContent).toContain("Dialog");
    expect(dialog.textContent).toContain("annotated");
    await act(async () => { (dialog.querySelector(`[data-pick-capture="${capture.id}"]`) as HTMLButtonElement).click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(`/api/walkthroughs/${WID}/steps`, { capture_id: capture.id, after: A });
    expect(steps()).toEqual([A, N, B]);
    expect(selectedStep()).toBe(N);
    expect(q("[role=dialog]")).toBeNull();
    expect(status()).toBe("Saved");
    expect(button("Save")!.disabled).toBe(true);
    // The next save carries the revision the add answered.
    await act(async () => { setValue(q("[data-step-chapter-input]") as HTMLInputElement, "Two"); });
    await key("s", { ctrlKey: true });
    await settle();
    expect(puts()).toHaveLength(1);
    expect((puts()[0][1] as { revision: number }).revision).toBe(3);
  });

  it("draws a hotspot by a drag, moves it, resizes it by a handle, removes it; undo and redo walk those edits", async () => {
    await mount();
    await drag(10, 10, 90, 60);
    expect(hotspotLine()).toContain("Hotspot 80 × 50 at 10, 10");
    expect(q("[data-surface-hotspot]")).not.toBeNull();
    expect(document.querySelectorAll("[data-handle]")).toHaveLength(8);
    expect(status()).toBe("Unsaved changes");
    await drag(50, 30, 70, 40);
    expect(hotspotLine()).toContain("at 30, 20");
    // The south-east handle sits at (110, 70) now.
    await drag(110, 70, 130, 80);
    expect(hotspotLine()).toContain("Hotspot 100 × 60 at 30, 20");
    // A drag too small to be a hotspot leaves the old one.
    await drag(150, 90, 152, 92);
    expect(hotspotLine()).toContain("Hotspot 100 × 60 at 30, 20");
    await act(async () => { q("[data-remove-hotspot]")!.click(); });
    expect(hotspotLine()).toContain("No hotspot");
    await key("z", { ctrlKey: true });
    expect(hotspotLine()).toContain("Hotspot 100 × 60 at 30, 20");
    await key("z", { ctrlKey: true });
    expect(hotspotLine()).toContain("Hotspot 80 × 50 at 30, 20");
    await key("y", { ctrlKey: true });
    expect(hotspotLine()).toContain("Hotspot 100 × 60 at 30, 20");
    await key("y", { ctrlKey: true });
    expect(hotspotLine()).toContain("No hotspot");
    // Escape while dragging puts the hotspot back.
    await key("z", { ctrlKey: true });
    await pointer("pointerdown", 150, 90);
    await pointer("pointermove", 190, 95);
    await key("Escape");
    expect(hotspotLine()).toContain("Hotspot 100 × 60 at 30, 20");
  });

  it("move up / down and a drag reorder the steps; delete asks first", async () => {
    await mount();
    await act(async () => { labelled("Move step 1 down")!.click(); });
    expect(steps()).toEqual([B, A]);
    expect(labelled("Move step 1 up")!.disabled).toBe(true);
    await act(async () => { labelled("Move step 2 up")!.click(); });
    expect(steps()).toEqual([A, B]);
    const items = () => [...document.querySelectorAll("[data-step]")] as HTMLElement[];
    await act(async () => { items()[1].dispatchEvent(new Event("dragstart", { bubbles: true })); });
    await act(async () => { items()[0].dispatchEvent(new Event("dragover", { bubbles: true, cancelable: true })); });
    await act(async () => { items()[0].dispatchEvent(new Event("drop", { bubbles: true, cancelable: true })); });
    expect(steps()).toEqual([B, A]);
    expect(status()).toBe("Unsaved changes");
    await act(async () => { labelled("Delete step 1")!.click(); });
    expect(q("[role=dialog]")!.textContent).toContain("Delete step 1");
    await act(async () => { button("Cancel")!.click(); });
    expect(steps()).toEqual([B, A]);
    await act(async () => { labelled("Delete step 1")!.click(); });
    await act(async () => { button("Delete")!.click(); });
    expect(steps()).toEqual([A]);
    expect(selectedStep()).toBe(A);
  });

  it("the properties: kind, text (committed when the box is left), placement, chapter; a chapter shows on the step", async () => {
    await mount();
    await act(async () => { setValue(q("[data-step-text]") as HTMLTextAreaElement, "Press here"); });
    expect(status()).toBe("Unsaved changes");
    expect(button("Undo")!.disabled).toBe(true);
    // Leaving the box commits the text (React's onBlur is the focusout event).
    await act(async () => { q("[data-step-text]")!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
    expect(button("Undo")!.disabled).toBe(false);
    await act(async () => { setValue(q("[data-step-kind]") as HTMLSelectElement, "modal"); });
    expect((q("[data-step-placement]") as HTMLSelectElement).disabled).toBe(true);
    await act(async () => { setValue(q("[data-step-kind]") as HTMLSelectElement, "tooltip"); });
    await act(async () => { setValue(q("[data-step-placement]") as HTMLSelectElement, "left"); });
    await act(async () => { setValue(q("[data-step-chapter-input]") as HTMLInputElement, "Intro"); });
    await act(async () => { q("[data-step-chapter-input]")!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
    expect(q("[data-step-chapter]")!.textContent).toBe("Intro");
    await key("s", { ctrlKey: true });
    await settle();
    const sent = puts()[0][1] as { steps: Step[] };
    expect(sent.steps[0]).toMatchObject({ id: A, text: "Press here", kind: "tooltip", placement: "left", chapter: "Intro" });
  });
});

describe("saving", () => {
  it("Ctrl+S sends the whole document with the revision it loaded, takes the next one, and says Saved", async () => {
    await mount();
    await drag(10, 10, 90, 60);
    await key("s", { ctrlKey: true });
    await settle();
    expect(puts()).toHaveLength(1);
    const [path, body] = puts()[0];
    expect(path).toBe(`/api/walkthroughs/${WID}`);
    expect(body).toEqual({
      version: 1, name: "Tour", revision: 2,
      steps: [{ ...step(A), hotspot: { x: 10, y: 10, width: 80, height: 50 } }, step(B)],
    });
    expect(status()).toBe("Saved");
    expect(button("Save")!.disabled).toBe(true);
    await key("s", { ctrlKey: true });
    await settle();
    expect(puts()).toHaveLength(1);
    expect(status()).toBe("Nothing to save.");
    // The next edit saves with the revision the server answered.
    await act(async () => { setValue(q("[data-walkthrough-name]") as HTMLInputElement, "Renamed"); });
    await act(async () => { button("Save")!.click(); });
    await settle();
    expect((puts()[1][1] as { revision: number; name: string })).toMatchObject({ revision: 3, name: "Renamed" });
  });

  it("a 409 is shown as the server's line with a Reload that asks, then loads the saved walkthrough", async () => {
    vi.mocked(api.put).mockImplementation(async () => { throw new ApiError(409, STALE_MESSAGE); });
    await mount();
    await drag(10, 10, 90, 60);
    await key("s", { ctrlKey: true });
    await settle();
    expect(status()).toBe(STALE_MESSAGE);
    expect(q("[data-builder-reload]")).not.toBeNull();
    vi.mocked(api.get).mockImplementation(async () => record({ revision: 5, name: "Theirs" }) as never);
    await act(async () => { q("[data-builder-reload]")!.click(); });
    expect(q("[role=dialog]")!.textContent).toContain("Reload the walkthrough?");
    await act(async () => { button("Reload")!.click(); });
    await settle();
    expect(hotspotLine()).toContain("No hotspot");
    expect((q("[data-walkthrough-name]") as HTMLInputElement).value).toBe("Theirs");
    expect(status()).toBe("Reloaded");
    expect(q("[data-builder-reload]")).toBeNull();
    vi.mocked(api.put).mockImplementation(async (_p: string, body: unknown) => record({ ...(body as object), revision: 6 }) as never);
    await drag(10, 10, 90, 60);
    await key("s", { ctrlKey: true });
    await settle();
    expect((puts().at(-1)![1] as { revision: number }).revision).toBe(5);
  });

  it("a save answered 404 goes to the list with the notice", async () => {
    vi.mocked(api.put).mockImplementation(async () => { throw new ApiError(404, "No such walkthrough."); });
    await mount();
    await drag(10, 10, 90, 60);
    await key("s", { ctrlKey: true });
    await settle();
    expect(q("[data-list]")).not.toBeNull();
    expect(q("[data-notice]")!.textContent).toBe(GONE_MESSAGE);
  });
});

describe("leaving and playing", () => {
  it("asks before leaving with unsaved changes (Cancel stays, Leave goes), and not when nothing changed", async () => {
    await mount();
    const link = [...document.querySelectorAll("a")].find((a) => (a.textContent || "").trim() === "Walkthroughs")!;
    await act(async () => { link.click(); });
    await settle();
    expect(q("[role=dialog]")).toBeNull();
    expect(q("[data-list]")).not.toBeNull();
    await act(async () => root?.unmount());
    await mount();
    await drag(10, 10, 90, 60);
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    // n7: the guard's history mark is the generic one, since it serves two editors.
    expect((window.history.state as { sentinel?: string } | null)?.sentinel).toBe("unsaved-changes");
    const again = [...document.querySelectorAll("a")].find((a) => (a.textContent || "").trim() === "Walkthroughs")!;
    await act(async () => { again.click(); });
    expect(q("[role=dialog]")!.textContent).toContain(UNSAVED_ASK.title);
    await act(async () => { button("Cancel")!.click(); });
    expect(q("[data-list]")).toBeNull();
    await act(async () => { again.click(); });
    await act(async () => { button(UNSAVED_ASK.confirmLabel)!.click(); });
    await settle();
    expect(q("[data-list]")).not.toBeNull();
  });

  it("Play opens the player at the selected step; with unsaved changes it asks to save first and saves", async () => {
    await mount();
    await act(async () => { (q(`[data-step="${B}"]`) as HTMLElement).click(); });
    expect(selectedStep()).toBe(B);
    await act(async () => { button("Play")!.click(); });
    await settle();
    expect(q("[data-play]")!.getAttribute("data-search")).toBe("?step=1");
    expect(JSON.parse(q("[data-play]")!.getAttribute("data-state")!)).toEqual({ from: "builder", step: 1 });
    expect(puts()).toHaveLength(0);
    await act(async () => root?.unmount());
    await mount();
    await drag(10, 10, 90, 60);
    await act(async () => { button("Play")!.click(); });
    expect(q("[role=dialog]")!.textContent).toContain(SAVE_BEFORE_PLAY.title);
    await act(async () => { button("Cancel")!.click(); });
    expect(q("[data-play]")).toBeNull();
    expect(puts()).toHaveLength(0);
    await act(async () => { button("Play")!.click(); });
    await act(async () => { button(SAVE_BEFORE_PLAY.confirmLabel)!.click(); });
    await settle();
    expect(puts()).toHaveLength(1);
    expect(q("[data-play]")).not.toBeNull();
    expect(q("[role=dialog]")).toBeNull();
  });
});

describe("the fix round", () => {
  const blur = (sel: string) => act(async () => { q(sel)!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });

  it("R1: a chapter typed with a trailing space saves to Saved with Save disabled, the Walkthroughs link does not ask, and Play does not ask to save", async () => {
    await mount();
    await act(async () => { setValue(q("[data-step-chapter-input]") as HTMLInputElement, "Intro "); });
    await blur("[data-step-chapter-input]");
    await act(async () => { setValue(q("[data-walkthrough-name]") as HTMLInputElement, " Tour "); });
    await blur("[data-walkthrough-name]");
    expect(status()).toBe("Unsaved changes");
    await key("s", { ctrlKey: true });
    await settle();
    expect((puts()[0][1] as { name: string; steps: Step[] }).steps[0].chapter).toBe("Intro");
    expect((puts()[0][1] as { name: string }).name).toBe("Tour");
    expect(status()).toBe("Saved");
    expect(button("Save")!.disabled).toBe(true);
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
    await act(async () => { button("Play")!.click(); });
    await settle();
    expect(q("[role=dialog]")).toBeNull();
    expect(q("[data-play]")).not.toBeNull();
    expect(puts()).toHaveLength(1);
    await act(async () => root?.unmount());
    await mount();
    await act(async () => { setValue(q("[data-step-chapter-input]") as HTMLInputElement, "Intro "); });
    await blur("[data-step-chapter-input]");
    await key("s", { ctrlKey: true });
    await settle();
    const link = [...document.querySelectorAll("a")].find((a) => (a.textContent || "").trim() === "Walkthroughs")!;
    await act(async () => { link.click(); });
    await settle();
    expect(q("[role=dialog]")).toBeNull();
    expect(q("[data-list]")).not.toBeNull();
  });

  it("R4: Add step, Ctrl+Z, Ctrl+S, Ctrl+Y - nothing to redo, the step absent, and the next save is accepted", async () => {
    vi.mocked(api.post).mockImplementation(async () => record({ revision: 3, steps: [step(A), step(N), step(B)] }) as never);
    await mount();
    await act(async () => { q("[data-add-step]")!.click(); });
    await settle();
    await act(async () => { (document.querySelector(`[data-pick-capture="${capture.id}"]`) as HTMLButtonElement).click(); });
    await settle();
    expect(steps()).toEqual([A, N, B]);
    await key("z", { ctrlKey: true });
    expect(steps()).toEqual([A, B]);
    expect(button("Redo")!.disabled).toBe(false);
    await key("s", { ctrlKey: true });
    await settle();
    expect(puts()).toHaveLength(1);
    expect((puts()[0][1] as { steps: Step[] }).steps.map((s) => s.id)).toEqual([A, B]);
    expect(status()).toBe("Saved");
    // The save swept N's picture: the redo that would bring N back is gone.
    expect(button("Redo")!.disabled).toBe(true);
    await key("y", { ctrlKey: true });
    expect(steps()).toEqual([A, B]);
    await act(async () => { setValue(q("[data-walkthrough-name]") as HTMLInputElement, "Tour two"); });
    await blur("[data-walkthrough-name]");
    await key("s", { ctrlKey: true });
    await settle();
    expect(puts()).toHaveLength(2);
    expect((puts()[1][1] as { steps: Step[] }).steps.map((s) => s.image)).toEqual([`step-${A}.png`, `step-${B}.png`]);
    expect(status()).toBe("Saved");
  });

  it("R9: one status line - Saved on load, Unsaved changes on an edit, Saved again after Undo, an event line only until the next change", async () => {
    await mount();
    expect(status()).toBe("Saved");
    await act(async () => { setValue(q("[data-walkthrough-name]") as HTMLInputElement, "Tour X"); });
    expect(status()).toBe("Unsaved changes");
    await blur("[data-walkthrough-name]");
    expect(status()).toBe("Unsaved changes");
    await key("z", { ctrlKey: true });
    expect(status()).toBe("Saved");
    await key("s", { ctrlKey: true });
    await settle();
    expect(status()).toBe("Nothing to save.");
    await act(async () => { setValue(q("[data-walkthrough-name]") as HTMLInputElement, "Tour Y"); });
    expect(status()).toBe("Unsaved changes");
    await key("s", { ctrlKey: true });
    await settle();
    expect(status()).toBe("Saved");
    await key("y", { ctrlKey: true });
    expect(status()).toBe("Saved");
  });

  it("n5: a cancelled pointer puts the hotspot back as it was", async () => {
    await mount();
    await drag(10, 10, 90, 60);
    expect(hotspotLine()).toContain("Hotspot 80 × 50 at 10, 10");
    await pointer("pointerdown", 50, 30);
    await pointer("pointermove", 150, 80);
    expect(hotspotLine()).toContain("at 110, 50"); // moved by (100, 50), the 50-high hotspot kept inside the 100-high picture
    await pointer("pointercancel", 150, 80);
    expect(hotspotLine()).toContain("Hotspot 80 × 50 at 10, 10");
    expect(button("Undo")!.disabled).toBe(false);
    await key("z", { ctrlKey: true });
    expect(hotspotLine()).toContain("No hotspot");
  });

  it("n8: the picture's zoom follows the window's size", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === `/api/walkthroughs/${WID}`) return record({ steps: [step(A, { width: 1600, height: 800 }), step(B)] }) as never;
      return {} as never;
    });
    await mount();
    const width = () => surface().style.width;
    expect(width()).toBe("976px"); // 1600 x (1000 - 24) / 1600
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 3000 });
    await act(async () => { window.dispatchEvent(new Event("resize")); });
    expect(width()).toBe("1152px"); // now the height is the bound: 1600 x (600 - 24) / 800
  });

  it("the instruction is a field of the step - typed, counted, undone, saved - and the transcript panel shows the Markdown with Copy and Save as…", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const urls = globalThis.URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
    const hadCreate = urls.createObjectURL, hadRevoke = urls.revokeObjectURL;
    urls.createObjectURL = vi.fn(() => "blob:transcript");
    urls.revokeObjectURL = vi.fn();
    const downloads: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download); });
    try {
      await mount();
      expect(document.body.textContent).toContain("0 / 2000");
      await act(async () => { setValue(q("[data-step-instruction]") as HTMLTextAreaElement, "Drag the Text file input onto the canvas"); });
      expect(document.body.textContent).toContain("40 / 2000");
      expect(status()).toBe("Unsaved changes");
      await blur("[data-step-instruction]");
      await act(async () => { setValue(q("[data-step-chapter-input]") as HTMLInputElement, "Setup"); });
      await blur("[data-step-chapter-input]");
      await act(async () => { q("[data-transcript-toggle]")!.click(); });
      const panel = q("[data-builder-transcript]")!;
      expect(panel.getAttribute("role")).toBe("region");
      const expected = "# Tour\n\n## Setup\n\n1. Drag the Text file input onto the canvas\n2. (no instruction yet)\n";
      expect(q("[data-transcript-text]")!.textContent).toBe(expected);
      await act(async () => { q("[data-transcript-copy]")!.click(); });
      await settle(5);
      expect(writeText).toHaveBeenCalledWith(expected);
      expect(q("[data-transcript-copied]")!.textContent).toContain("Copied");
      await act(async () => { q("[data-transcript-save]")!.click(); });
      expect(downloads).toEqual(["Tour.md"]);
      await key("s", { ctrlKey: true });
      await settle();
      const sent = puts()[0][1] as { steps: Step[] };
      expect(sent.steps[0]).toMatchObject({ instruction: "Drag the Text file input onto the canvas", chapter: "Setup" });
      expect(status()).toBe("Saved");
      await key("z", { ctrlKey: true });
      expect((q("[data-step-instruction]") as HTMLTextAreaElement).value).toBe("Drag the Text file input onto the canvas");
      expect((q("[data-step-chapter-input]") as HTMLInputElement).value).toBe("");
      await key("z", { ctrlKey: true });
      expect((q("[data-step-instruction]") as HTMLTextAreaElement).value).toBe("");
      expect(q("[data-transcript-text]")!.textContent).toBe("# Tour\n\n1. (no instruction yet)\n2. (no instruction yet)\n");
      await act(async () => { (panel.querySelector('button[aria-label="Close the transcript"]') as HTMLButtonElement).click(); });
      expect(q("[data-builder-transcript]")).toBeNull();
    } finally {
      click.mockRestore();
      urls.createObjectURL = hadCreate;
      urls.revokeObjectURL = hadRevoke;
      delete (navigator as unknown as { clipboard?: unknown }).clipboard;
    }
  });
});

describe("the transcript's Copy when the clipboard refuses", () => {
  it("says it could not copy when the page may not write to the clipboard", async () => {
    const writeText = vi.fn(async () => { throw new DOMException("Write permission denied.", "NotAllowedError"); });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    try {
      await mount();
      await act(async () => { q("[data-transcript-toggle]")!.click(); });
      await act(async () => { q("[data-transcript-copy]")!.click(); });
      await settle(5);
      expect(writeText).toHaveBeenCalledTimes(1);
      expect(q("[data-transcript-copied]")!.textContent).toBe("Could not copy: the page may not write to the clipboard.");
    } finally {
      delete (navigator as unknown as { clipboard?: unknown }).clipboard;
    }
  });
});

describe("replace picture", () => {
  it("copies the still's picture again on the server, and keeps the local hotspot clamped to the new size", async () => {
    vi.mocked(api.post).mockImplementation(async () => record({
      revision: 3, steps: [step(A, { width: 100, height: 50, source: { capture_id: capture.id, annotated: true } }), step(B)],
    }) as never);
    await mount();
    await drag(30, 20, 110, 70);
    expect(hotspotLine()).toContain("Hotspot 80 × 50 at 30, 20");
    await act(async () => { q("[data-replace-picture]")!.click(); });
    await settle();
    expect(q("[role=dialog]")!.textContent).toContain("Replace picture");
    await act(async () => { (document.querySelector(`[data-pick-capture="${capture.id}"]`) as HTMLButtonElement).click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(`/api/walkthroughs/${WID}/steps/${A}/picture`, { capture_id: capture.id });
    expect(hotspotLine()).toContain("Hotspot 80 × 50 at 20, 0");
    expect(document.body.textContent).toContain("100 × 50");
    expect(document.body.textContent).toContain("from an annotated still");
    expect((q("[data-walkthrough-surface] img") as HTMLImageElement).getAttribute("src")).toBe(`/api/walkthroughs/${WID}/steps/${A}/image?v=3`);
    expect(status()).toBe("Unsaved changes");
    await key("s", { ctrlKey: true });
    await settle();
    const sent = puts()[0][1] as { revision: number; steps: Step[] };
    expect(sent.revision).toBe(3);
    expect(sent.steps[0]).toMatchObject({ width: 100, height: 50, hotspot: { x: 20, y: 0, width: 80, height: 50 } });
  });
});
