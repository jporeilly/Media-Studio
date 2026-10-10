// @vitest-environment jsdom
/**
 * The annotation editor page (T3b), mounted for real under jsdom with the server, the picture loader and
 * the canvas faked (jsdom draws nothing: `getContext` answers a recording stub, `toBlob` a PNG-looking
 * blob): the tool keys, a drag that draws an arrow, Ctrl+S saving ONE multipart body carrying `document`
 * (version 1) and `image` (the export), Esc and Delete, the unsaved guard (the window and in-app
 * navigation, through the app's own confirm), the "deleted meanwhile" notice on Projects, Save and add
 * to deck opening the shared dialog, Revert asking first, and a still too big to open.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { GONE_MESSAGE, UNSAVED_ASK } from "../lib/annotate";
import type { Capture } from "../lib/capture";
import AnnotatePage from "./Annotate";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fake = vi.hoisted(() => ({ image: { width: 200, height: 100 } as unknown as HTMLImageElement, contexts: [] as unknown[] }));
vi.mock("../components/annotate/loadImage", () => ({ loadImage: async () => fake.image }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), putForm: vi.fn(), delete: vi.fn() } };
});

const CID = "abc123abc123";
const record = (over: Partial<Capture> = {}): Capture => ({
  id: CID, name: "Notepad", kind: "window", created_at: "2026-10-10T10:00:00Z", width: 200, height: 100, cursor: false, size_bytes: 10,
  annotated: false, output_width: 200, output_height: 100, annotations: null, ...over,
});

/** A context that records property sets and method calls (`__calls`) and answers `measureText`. */
function contextStub() {
  const calls: string[] = [];
  const props: Record<string, unknown> = { font: "", __calls: calls, measureText: (t: string) => ({ width: t.length * 8 }) };
  return new Proxy(props, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      return () => { calls.push(prop); };
    },
    set(target, prop: string, value) { target[prop] = value; return true; },
  });
}

/** Every method call any context stub saw (the surface's draws, the export's). */
const drawCalls = () => (fake.contexts as { __calls: string[] }[]).flatMap((c) => c.__calls);

/** Type into a textarea the way a user does: the native setter, then an input event React listens to. */
function typeInto(el: HTMLTextAreaElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  setter.call(el, text);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

const statusLeft = () => (document.querySelector("[data-annotate-status-left]")?.textContent || "").trim();
const textarea = () => document.querySelector("textarea[data-annotate-text]") as HTMLTextAreaElement | null;

/**
 * The server's own rule on a document, as api/schemas.py holds it (the field lists copied from there, not
 * from the lib, so the page is checked against the server and not against itself): every x-like field
 * within -W .. 2W and y-like within -H .. 2H, a width or a height at least 1, a text at most 500
 * characters, a crop inside the still of at least 16 a side.
 */
const SERVER_X: Record<string, string[]> = { arrow: ["x1", "x2"], line: ["x1", "x2"], rect: ["x", "width"], ellipse: ["x", "width"], blur: ["x", "width"], highlight: ["x", "width"], text: ["x", "width"], callout: ["x", "width", "tail_x"], step: ["x"], magnify: ["x", "width", "lens_x"] };
const SERVER_Y: Record<string, string[]> = { arrow: ["y1", "y2"], line: ["y1", "y2"], rect: ["y", "height"], ellipse: ["y", "height"], blur: ["y", "height"], highlight: ["y", "height"], text: ["y", "height"], callout: ["y", "height", "tail_y"], step: ["y"], magnify: ["y", "height", "lens_y"] };
function serverRefusal(document_: { shapes: Record<string, unknown>[]; crop: Record<string, number> | null }, still: { width: number; height: number }): string | null {
  for (const [i, s] of document_.shapes.entries()) {
    const type = String(s.type);
    for (const [names, size] of [[SERVER_X[type], still.width], [SERVER_Y[type], still.height]] as [string[], number][]) {
      for (const n of names) {
        const v = s[n] as number;
        if (!Number.isFinite(v) || v < -size || v > 2 * size) return `shapes.${i}.${type}: ${type} ${n} is outside the still (-${size} to ${2 * size}).`;
        if ((n === "width" || n === "height") && v < 1) return `shapes.${i}.${type}.${n}: Input should be greater than or equal to 1`;
      }
    }
    if (typeof s.text === "string" && s.text.length > 500) return `shapes.${i}.${type}.text: String should have at most 500 characters`;
  }
  const c = document_.crop;
  if (c && (c.x < 0 || c.y < 0 || c.width < 16 || c.height < 16 || c.x + c.width > still.width || c.y + c.height > still.height)) return "crop: The crop runs past the still's edge.";
  return null;
}

let root: Root | null = null;
let host: HTMLDivElement | null = null;
let qc: QueryClient;

function ProjectsStub() {
  const location = useLocation();
  const notice = (location.state as { notice?: string } | null)?.notice;
  return createElement("div", { "data-projects": "" }, notice ? createElement("p", { "data-notice": "" }, notice) : "projects");
}

async function mount(path = `/captures/${CID}/annotate`) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [path] },
          createElement(Routes, null,
            createElement(Route, { path: "/captures/:id/annotate", element: createElement(AnnotatePage) }),
            createElement(Route, { path: "/projects", element: createElement(ProjectsStub) }),
            createElement(Route, { path: "/elsewhere", element: createElement("div", { "data-elsewhere": "" }, createElement(Link, { to: "/projects" }, "go")) }),
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

const buttons = () => [...document.querySelectorAll("button")];
const button = (text: string) => buttons().find((b) => (b.textContent || "").trim() === text) ?? null;
const toolButton = (tool: string) => document.querySelector(`button[data-tool="${tool}"]`) as HTMLButtonElement | null;
const canvas = () => document.querySelector("canvas[data-annotate-surface]") as HTMLCanvasElement | null;
const status = () => (document.querySelector("[data-annotate-status]")?.textContent || "").trim();
const key = (k: string, init: KeyboardEventInit = {}, target: EventTarget = window) =>
  act(async () => { target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...init })); });
const pointer = (type: string, x: number, y: number) =>
  act(async () => { canvas()!.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y, bubbles: true, cancelable: true, button: 0 })); });

async function drawArrow(x1 = 10, y1 = 10, x2 = 90, y2 = 60) {
  await act(async () => { button("100 %")!.click(); });
  await key("a");
  await pointer("pointerdown", x1, y1);
  await pointer("pointermove", x2, y2);
  await pointer("pointerup", x2, y2);
}

const saves = () => vi.mocked(api.putForm).mock.calls;

beforeEach(() => {
  fake.contexts = [];
  HTMLCanvasElement.prototype.getContext = function () { const c = contextStub(); fake.contexts.push(c); return c as never; } as never;
  HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) { cb(new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" })); };
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.putForm).mockReset();
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === `/api/captures/${CID}`) return record() as never;
    if (path === "/api/projects") return { projects: [{ id: "deck00000001", name: "Launch deck", kind: "deck" }, { id: "pdf000000001", name: "Pages", kind: "pdf" }] } as never;
    return {} as never;
  });
  vi.mocked(api.putForm).mockImplementation(async () => record({ annotated: true, annotated_at: "2026-10-10T11:00:00Z" }) as never);
  vi.mocked(api.delete).mockImplementation(async () => undefined as never);
  vi.mocked(api.post).mockImplementation(async () => ({ index: 1, slide_count: 2 }) as never);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("opening", () => {
  it("loads the record and the original picture and shows the twelve tools with Select pressed", async () => {
    await mount();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith(`/api/captures/${CID}`);
    expect(canvas()).not.toBeNull();
    expect(document.querySelectorAll("button[data-tool]")).toHaveLength(12);
    expect(toolButton("select")!.getAttribute("aria-pressed")).toBe("true");
    expect(document.body.textContent).toContain("Notepad");
    expect(status()).toContain("0 shapes");
  });

  it("sends the user back to Projects with a notice when the still is gone", async () => {
    vi.mocked(api.get).mockImplementation(async () => { throw new ApiError(404, "No such capture."); });
    await mount();
    await settle();
    expect(document.querySelector("[data-projects]")).not.toBeNull();
    expect(document.querySelector("[data-notice]")!.textContent).toBe(GONE_MESSAGE);
  });

  it("refuses a still of more than 100 million pixels with a message and no tools", async () => {
    vi.mocked(api.get).mockImplementation(async () => record({ width: 20000, height: 6000 }) as never);
    await mount();
    expect(document.body.textContent).toContain("100 million");
    expect(canvas()).toBeNull();
    expect(document.querySelectorAll("button[data-tool]")).toHaveLength(0);
  });
});

describe("the keys", () => {
  it("V A C T R E L S B H K M pick the tools, not with Ctrl, and not while typing in a box", async () => {
    await mount();
    for (const [k, tool] of [["a", "arrow"], ["c", "callout"], ["t", "text"], ["r", "rect"], ["e", "ellipse"], ["l", "line"], ["s", "step"], ["b", "blur"], ["h", "highlight"], ["k", "crop"], ["m", "magnify"], ["v", "select"]]) {
      await key(k);
      expect(toolButton(tool)!.getAttribute("aria-pressed"), tool).toBe("true");
    }
    await key("A", { ctrlKey: true });
    expect(toolButton("select")!.getAttribute("aria-pressed")).toBe("true");
    const input = document.createElement("input");
    document.body.appendChild(input);
    await key("a", {}, input);
    expect(toolButton("select")!.getAttribute("aria-pressed")).toBe("true");
  });

  it("Ctrl+S saves one multipart body carrying the document (version 1) and the exported image", async () => {
    await mount();
    await drawArrow();
    expect(status()).toContain("1 shape");
    expect(status()).toContain("Unsaved changes");
    expect(button("Save")!.disabled).toBe(false);
    await key("s", { ctrlKey: true });
    await settle();
    expect(saves()).toHaveLength(1);
    const [path, form] = saves()[0];
    expect(path).toBe(`/api/captures/${CID}/annotations`);
    expect(form).toBeInstanceOf(FormData);
    const document_ = JSON.parse(String(form.get("document")));
    expect(document_.version).toBe(1);
    expect(document_.shapes).toHaveLength(1);
    expect(document_.shapes[0]).toMatchObject({ type: "arrow", x1: 10, y1: 10, x2: 90, y2: 60 });
    expect(document_.crop).toBeNull();
    const image = form.get("image") as File;
    expect(image).toBeInstanceOf(Blob);
    expect(image.type).toBe("image/png");
    expect(image.name).toBe("annotated.png");
    expect(status()).toContain("Saved");
    expect(button("Save")!.disabled).toBe(true);
    // Nothing changed since: Ctrl+S sends nothing more.
    await key("s", { ctrlKey: true });
    await settle();
    expect(saves()).toHaveLength(1);
  });

  it("Delete removes the selected shape; Esc deselects first, so Delete then removes nothing", async () => {
    await mount();
    await drawArrow();
    expect(status()).toContain("1 shape");
    await key("Delete");
    expect(status()).toContain("0 shapes");
    await drawArrow();
    expect(status()).toContain("1 shape");
    await key("Escape");
    await key("Delete");
    expect(status()).toContain("1 shape");
  });

  it("Ctrl+Z and Ctrl+Y undo and redo", async () => {
    await mount();
    await drawArrow();
    await key("z", { ctrlKey: true });
    expect(status()).toContain("0 shapes");
    await key("y", { ctrlKey: true });
    expect(status()).toContain("1 shape");
  });

  it("the Text tool opens a box at the click; Escape with nothing typed takes it away", async () => {
    await mount();
    await act(async () => { button("100 %")!.click(); });
    await key("t");
    await pointer("pointerdown", 20, 20);
    const box = document.querySelector("textarea[data-annotate-text]") as HTMLTextAreaElement | null;
    expect(box).not.toBeNull();
    expect(status()).toContain("1 shape");
    await key("Escape", {}, box!);
    expect(document.querySelector("textarea[data-annotate-text]")).toBeNull();
    expect(status()).toContain("0 shapes");
  });
});

describe("leaving", () => {
  it("asks before the window unloads while there are unsaved changes, and not once they are saved", async () => {
    await mount();
    const unload = () => { const ev = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(ev); return ev.defaultPrevented; };
    expect(unload()).toBe(false);
    await drawArrow();
    expect(unload()).toBe(true);
    await key("s", { ctrlKey: true });
    await settle();
    expect(unload()).toBe(false);
  });

  it("asks before in-app navigation with unsaved changes - Cancel stays, Leave goes", async () => {
    await mount();
    await drawArrow();
    const projectsLink = [...document.querySelectorAll("a")].find((a) => (a.textContent || "").trim() === "Projects")!;
    await act(async () => { projectsLink.click(); });
    const dialog = document.querySelector("[role=dialog]");
    expect(dialog).not.toBeNull();
    expect(dialog!.textContent).toContain(UNSAVED_ASK.title);
    await act(async () => { button("Cancel")!.click(); });
    expect(document.querySelector("[data-projects]")).toBeNull();
    expect(canvas()).not.toBeNull();
    await act(async () => { projectsLink.click(); });
    await act(async () => { button(UNSAVED_ASK.confirmLabel)!.click(); });
    await settle();
    expect(document.querySelector("[data-projects]")).not.toBeNull();
  });

  it("leaves without asking when nothing changed", async () => {
    await mount();
    const projectsLink = [...document.querySelectorAll("a")].find((a) => (a.textContent || "").trim() === "Projects")!;
    await act(async () => { projectsLink.click(); });
    await settle();
    expect(document.querySelector("[role=dialog]")).toBeNull();
    expect(document.querySelector("[data-projects]")).not.toBeNull();
  });
});

describe("the actions", () => {
  it("Save and add to deck… saves, then opens the shared Add to deck dialog, which adds the still", async () => {
    await mount();
    await drawArrow();
    await act(async () => { button("Save and add to deck…")!.click(); });
    await settle();
    expect(saves()).toHaveLength(1);
    const dialog = document.querySelector("[role=dialog]")!;
    expect(dialog.textContent).toContain("Add to deck");
    const select = dialog.querySelector("select") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Choose a deck…", "Launch deck"]);
    await act(async () => {
      select.value = "deck00000001";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { button("Add as last slide")!.click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(`/api/captures/${CID}/add-to-deck`, { project_id: "deck00000001" });
    expect(status()).toContain("Added as slide 2 of 2.");
  });

  it("Revert to original asks first, then deletes the annotations and empties the editor", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === `/api/captures/${CID}`) {
        return record({
          annotated: true, annotated_at: "2026-10-10T11:00:00Z",
          annotations: { version: 1, crop: null, shapes: [{ id: "a", type: "arrow", colour: "#e53935", stroke: "medium", x1: 1, y1: 1, x2: 50, y2: 50 }] },
        }) as never;
      }
      return {} as never;
    });
    await mount();
    expect(status()).toContain("1 shape");
    await act(async () => { button("Revert to original")!.click(); });
    expect(document.querySelector("[role=dialog]")!.textContent).toContain("Revert to original");
    await act(async () => { button("Cancel")!.click(); });
    expect(vi.mocked(api.delete)).not.toHaveBeenCalled();
    await act(async () => { button("Revert to original")!.click(); });
    await act(async () => { button("Revert")!.click(); });
    await settle();
    expect(vi.mocked(api.delete)).toHaveBeenCalledWith(`/api/captures/${CID}/annotations`);
    expect(status()).toContain("0 shapes");
    expect(status()).toContain("Reverted");
  });

  it("a document of another version is refused with its message rather than drawn half", async () => {
    vi.mocked(api.get).mockImplementation(async () => record({ annotated: true, annotations: { version: 2, shapes: [] } }) as never);
    await mount();
    expect(document.body.textContent).toContain("version 2");
    expect(canvas()).toBeNull();
  });
});

/** The document the last save sent, parsed. */
const lastSaved = () => JSON.parse(String((saves().at(-1)![1] as FormData).get("document")));

describe("the fix round", () => {
  it("R2: a shape dragged past -size, a box resized past 2 x size and 500 large characters still save with 200 against the server's own rule", async () => {
    vi.mocked(api.putForm).mockImplementation(async (_path: string, form: FormData) => {
      const refusal = serverRefusal(JSON.parse(String(form.get("document"))), { width: 200, height: 100 });
      if (refusal) throw new ApiError(400, `The annotation document is not valid: ${refusal}`);
      return record({ annotated: true, annotated_at: "2026-10-10T11:00:00Z" }) as never;
    });
    await mount();
    await act(async () => { button("100 %")!.click(); });
    // A box, then dragged far off the left edge: the pointer stops at -200, and the box's x with it.
    await key("r");
    await pointer("pointerdown", 100, 20);
    await pointer("pointermove", 180, 80);
    await pointer("pointerup", 180, 80);
    await key("v");
    await pointer("pointerdown", 140, 50);
    await pointer("pointermove", -5000, 50);
    await pointer("pointerup", -5000, 50);
    // Its east handle dragged far off the right edge: the width stops at 2 x 200.
    await pointer("pointerdown", -140, 50); // select it where it now sits (x -200 .. -120)
    await pointer("pointerup", -140, 50);
    await pointer("pointerdown", -120, 50); // the east handle
    await pointer("pointermove", 5000, 50);
    await pointer("pointerup", 5000, 50);
    // 500 large characters in a text box on this 200 x 100 still.
    await act(async () => { (document.querySelector('button[title="Text size: Large"]') as HTMLButtonElement).click(); });
    await key("t");
    await pointer("pointerdown", 10, 10);
    await act(async () => { typeInto(textarea()!, "x".repeat(500)); });
    await pointer("pointerdown", 190, 90); // click away: commits the box
    expect(textarea()).toBeNull();
    expect(status()).toContain("2 shapes");
    await key("s", { ctrlKey: true });
    await settle();
    expect(statusLeft()).toBe("Saved");
    const sent = lastSaved();
    const box = sent.shapes.find((s: { type: string }) => s.type === "rect");
    const text = sent.shapes.find((s: { type: string }) => s.type === "text");
    expect(box.x).toBe(-200);
    expect(box.width).toBe(400);
    expect(text.text).toHaveLength(500);
    expect(text.height).toBeLessThanOrEqual(200);
    expect(serverRefusal(sent, { width: 200, height: 100 })).toBeNull();
  });

  it("R3: clicking a shape to select it adds no undo step - Ctrl+Z after a click-select removes the shape, and Undo is then disabled", async () => {
    await mount();
    await drawArrow();
    expect(button("Undo")!.disabled).toBe(false);
    await key("v");
    await pointer("pointerdown", 50, 35); // on the arrow, moving nothing
    await pointer("pointerup", 50, 35);
    await key("z", { ctrlKey: true });
    expect(status()).toContain("0 shapes");
    expect(button("Undo")!.disabled).toBe(true);
  });

  it("R7: a save answered 404 sends the user to Projects with the notice and no 'Leave without saving?' in the way", async () => {
    vi.mocked(api.putForm).mockImplementation(async () => { throw new ApiError(404, "No such capture."); });
    await mount();
    await drawArrow();
    await key("s", { ctrlKey: true });
    await settle();
    expect(document.querySelector("[role=dialog]")).toBeNull();
    expect(document.querySelector("[data-projects]")).not.toBeNull();
    expect(document.querySelector("[data-notice]")!.textContent).toBe(GONE_MESSAGE);
  });

  it("R9: Back with unsaved changes re-arms the sentinel at once and asks - Cancel does nothing more, Leave goes back two; nothing asks when clean", async () => {
    // The entry's state outlives the tests of this file (one jsdom window): start from a plain entry, as a
    // page freshly navigated to has - a sentinel already there is reused, not pushed again.
    window.history.replaceState(null, "", window.location.href);
    const pushes = vi.spyOn(window.history, "pushState");
    const go = vi.spyOn(window.history, "go").mockImplementation(() => undefined);
    try {
      await mount();
      await act(async () => { window.dispatchEvent(new PopStateEvent("popstate")); });
      expect(document.querySelector("[role=dialog]")).toBeNull();
      expect(pushes).not.toHaveBeenCalled();
      await drawArrow();
      expect(pushes).toHaveBeenCalledTimes(1); // the sentinel entry under the page's own URL
      pushes.mockClear();
      await act(async () => { window.dispatchEvent(new PopStateEvent("popstate")); });
      // Re-armed synchronously, before the dialog opened: one push, and the dialog.
      expect(pushes).toHaveBeenCalledTimes(1);
      expect(document.querySelector("[role=dialog]")!.textContent).toContain(UNSAVED_ASK.title);
      await act(async () => { button("Cancel")!.click(); });
      await settle();
      expect(pushes).toHaveBeenCalledTimes(1); // Cancel pushed nothing further
      expect(go).not.toHaveBeenCalled();
      expect(canvas()).not.toBeNull();
      expect(status()).toContain("1 shape");
      await act(async () => { window.dispatchEvent(new PopStateEvent("popstate")); });
      expect(pushes).toHaveBeenCalledTimes(2);
      await act(async () => { button(UNSAVED_ASK.confirmLabel)!.click(); });
      await settle();
      expect(go).toHaveBeenCalledWith(-2);
    } finally {
      pushes.mockRestore();
      go.mockRestore();
    }
  });

  it("R9: a second Back while 'Leave without saving?' is open keeps the page and its work (the sentinel is re-pushed each time); Leave then still goes back two", async () => {
    window.history.replaceState(null, "", window.location.href);
    const pushes = vi.spyOn(window.history, "pushState");
    const go = vi.spyOn(window.history, "go").mockImplementation(() => undefined);
    try {
      await mount();
      await drawArrow();
      pushes.mockClear();
      await act(async () => { window.dispatchEvent(new PopStateEvent("popstate")); });
      expect(document.querySelector("[role=dialog]")).not.toBeNull();
      // The second Back while the dialog is open: the page is still here with its shape, re-armed again, asked again.
      await act(async () => { window.dispatchEvent(new PopStateEvent("popstate")); });
      expect(canvas()).not.toBeNull();
      expect(status()).toContain("1 shape");
      expect(pushes).toHaveBeenCalledTimes(2);
      expect(go).not.toHaveBeenCalled();
      expect(document.querySelectorAll("[role=dialog]")).toHaveLength(1);
      await act(async () => { button(UNSAVED_ASK.confirmLabel)!.click(); });
      await settle();
      expect(go).toHaveBeenCalledTimes(1);
      expect(go).toHaveBeenCalledWith(-2);
      expect(pushes).toHaveBeenCalledTimes(2);
    } finally {
      pushes.mockRestore();
      go.mockRestore();
    }
  });

  it("R10: Save with the last shape deleted sends the DELETE (the server's revert) and no picture", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === `/api/captures/${CID}`) {
        return record({ annotated: true, annotated_at: "2026-10-10T11:00:00Z", annotations: { version: 1, crop: null, shapes: [{ id: "a", type: "arrow", colour: "#e53935", stroke: "medium", x1: 10, y1: 10, x2: 90, y2: 60 }] } }) as never;
      }
      return {} as never;
    });
    await mount();
    await act(async () => { button("100 %")!.click(); });
    await key("v");
    await pointer("pointerdown", 50, 35);
    await pointer("pointerup", 50, 35);
    await key("Delete");
    expect(status()).toContain("0 shapes");
    await key("s", { ctrlKey: true });
    await settle();
    expect(vi.mocked(api.delete)).toHaveBeenCalledWith(`/api/captures/${CID}/annotations`);
    expect(saves()).toHaveLength(0);
    expect(statusLeft()).toBe("Saved");
  });

  it("R10: Ctrl+S with nothing changed says 'Nothing to save.' and sends nothing", async () => {
    await mount();
    await key("s", { ctrlKey: true });
    await settle();
    expect(statusLeft()).toBe("Nothing to save.");
    expect(saves()).toHaveLength(0);
    expect(vi.mocked(api.delete)).not.toHaveBeenCalled();
  });

  it("R11: a real click - pointerdown, mousedown with its focus side-effect unless prevented, pointerup, click - leaves the new text box focused and the shape kept", async () => {
    await mount();
    await act(async () => { button("100 %")!.click(); });
    await key("t");
    const target = canvas()!;
    const down = new MouseEvent("pointerdown", { clientX: 20, clientY: 20, bubbles: true, cancelable: true, button: 0 });
    await act(async () => { target.dispatchEvent(down); });
    // The browser's compatibility mousedown follows unless pointerdown was cancelled; its default action
    // moves the focus to what was clicked (the canvas is not focusable, so to the body) - the textarea blurs.
    if (!down.defaultPrevented) {
      const mousedown = new MouseEvent("mousedown", { clientX: 20, clientY: 20, bubbles: true, cancelable: true, button: 0 });
      await act(async () => { target.dispatchEvent(mousedown); });
      if (!mousedown.defaultPrevented) await act(async () => { (document.activeElement as HTMLElement | null)?.blur(); });
    }
    await act(async () => {
      target.dispatchEvent(new MouseEvent("pointerup", { clientX: 20, clientY: 20, bubbles: true, cancelable: true, button: 0 }));
      target.dispatchEvent(new MouseEvent("mouseup", { clientX: 20, clientY: 20, bubbles: true, cancelable: true, button: 0 }));
      target.dispatchEvent(new MouseEvent("click", { clientX: 20, clientY: 20, bubbles: true, cancelable: true, button: 0 }));
    });
    expect(down.defaultPrevented).toBe(true);
    expect(textarea()).not.toBeNull();
    expect(document.activeElement).toBe(textarea());
    expect(status()).toContain("1 shape");
    // Typing, then a click elsewhere on the canvas, commits the box with its text.
    await act(async () => { typeInto(textarea()!, "Look here"); });
    await pointer("pointerdown", 150, 80);
    expect(textarea()).toBeNull();
    expect(status()).toContain("1 shape");
    await key("s", { ctrlKey: true });
    await settle();
    expect(lastSaved().shapes[0]).toMatchObject({ type: "text", text: "Look here" });
  });

  it("R12: the toolbar keeps the same rows and buttons whatever the tool - Restart numbering and the lens factor sit on the style row, enabled only for their tools", async () => {
    await mount();
    const count = () => document.querySelectorAll("[data-annotate-toolbar] button").length;
    const row = () => document.querySelector("[data-annotate-style-row]")!;
    const restart = () => button("Restart numbering")!;
    const lens2 = () => [...row().querySelectorAll("button")].find((b) => (b.textContent || "").trim() === "2×")!;
    const before = count();
    expect(row().contains(restart())).toBe(true);
    expect(row().contains(lens2())).toBe(true);
    expect(restart().disabled).toBe(true);
    expect(lens2().disabled).toBe(true);
    await key("s");
    expect(count()).toBe(before);
    expect(restart().disabled).toBe(false);
    expect(lens2().disabled).toBe(true);
    await key("m");
    expect(count()).toBe(before);
    expect(restart().disabled).toBe(true);
    expect(lens2().disabled).toBe(false);
    await key("v");
    expect(count()).toBe(before);
  });

  it("R13: the status line reads 'Unsaved changes' the moment the document differs from the saved one, 'Saved' only while it does not", async () => {
    await mount();
    await drawArrow();
    expect(statusLeft()).toBe("Unsaved changes");
    await key("s", { ctrlKey: true });
    await settle();
    expect(statusLeft()).toBe("Saved");
    await drawArrow(20, 70, 120, 90);
    expect(statusLeft()).toBe("Unsaved changes");
    await key("s", { ctrlKey: true });
    await settle();
    expect(statusLeft()).toBe("Saved");
  });

  it("R14.1: Ctrl+S inside the text box keeps the text and saves, with the browser's own Save kept out", async () => {
    await mount();
    await act(async () => { button("100 %")!.click(); });
    await key("t");
    await pointer("pointerdown", 10, 10);
    await act(async () => { typeInto(textarea()!, "Hi"); });
    const ev = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    await act(async () => { textarea()!.dispatchEvent(ev); });
    await settle();
    expect(ev.defaultPrevented).toBe(true);
    expect(textarea()).toBeNull();
    expect(saves()).toHaveLength(1);
    expect(lastSaved().shapes[0]).toMatchObject({ type: "text", text: "Hi" });
    expect(statusLeft()).toBe("Saved");
  });

  it("R14.4: with a highlight selected the palette shows the highlight's colour, and a pick restyles that highlight - not the shared colour", async () => {
    await mount();
    await act(async () => { button("100 %")!.click(); });
    await key("h");
    await pointer("pointerdown", 20, 20);
    await pointer("pointermove", 120, 60);
    await pointer("pointerup", 120, 60);
    await key("v");
    await pointer("pointerdown", 70, 40);
    await pointer("pointerup", 70, 40);
    const swatch = (name: string) => document.querySelector(`button[aria-label="${name}"]`) as HTMLButtonElement;
    expect(swatch("Yellow").getAttribute("aria-pressed")).toBe("true");
    await act(async () => { swatch("Blue").click(); });
    await key("a");
    await pointer("pointerdown", 150, 80);
    await pointer("pointermove", 190, 95);
    await pointer("pointerup", 190, 95);
    await key("s", { ctrlKey: true });
    await settle();
    const sent = lastSaved();
    expect(sent.shapes[0]).toMatchObject({ type: "highlight", colour: "#1e88e5" });
    expect(sent.shapes[1]).toMatchObject({ type: "arrow", colour: "#e53935" });
  });

  it("R14.5: the page's fonts are awaited before the first draw and before the export", async () => {
    let ready!: () => void;
    const pending = new Promise<void>((resolve) => { ready = resolve; });
    (document as unknown as { fonts?: unknown }).fonts = { ready: pending };
    try {
      await mount();
      expect(canvas()).not.toBeNull();
      expect(drawCalls()).not.toContain("drawImage");
      await drawArrow();
      await key("s", { ctrlKey: true });
      await settle();
      expect(saves()).toHaveLength(0);
      await act(async () => { ready(); await pending; });
      await settle();
      expect(drawCalls()).toContain("drawImage");
      expect(saves()).toHaveLength(1);
    } finally {
      delete (document as unknown as { fonts?: unknown }).fonts;
    }
  });
});

describe("the addendum", () => {
  it("double-click to edit opens the box from the second click (detail 2) - once, and never from dblclick", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === `/api/captures/${CID}`) {
        return record({
          annotated: true, annotated_at: "2026-10-10T11:00:00Z",
          annotations: { version: 1, crop: null, shapes: [{ id: "t", type: "text", colour: "#000000", stroke: "medium", x: 10, y: 10, width: 100, height: 30, text: "Edit me", text_size: "small" }] },
        }) as never;
      }
      return {} as never;
    });
    await mount();
    await act(async () => { button("100 %")!.click(); });
    await key("v");
    const target = canvas()!;
    const at = (type: string, detail: number) => new MouseEvent(type, { clientX: 40, clientY: 20, bubbles: true, cancelable: true, button: 0, detail });
    // The browser's own sequence for a double-click: two clicks, the second with detail 2, then dblclick.
    await act(async () => { target.dispatchEvent(at("pointerdown", 1)); target.dispatchEvent(at("pointerup", 1)); target.dispatchEvent(at("click", 1)); });
    expect(textarea()).toBeNull();
    await act(async () => { target.dispatchEvent(at("pointerdown", 2)); target.dispatchEvent(at("pointerup", 2)); target.dispatchEvent(at("click", 2)); });
    expect(textarea()).not.toBeNull();
    expect(textarea()!.value).toBe("Edit me");
    await act(async () => { target.dispatchEvent(at("dblclick", 2)); });
    expect(document.querySelectorAll("textarea[data-annotate-text]")).toHaveLength(1);
    expect(document.activeElement).toBe(textarea());
    // A lone dblclick on a text, with no second click before it, opens nothing: there is one path.
    await act(async () => { textarea()!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })); });
    expect(textarea()).toBeNull();
    await act(async () => { target.dispatchEvent(at("dblclick", 2)); });
    expect(textarea()).toBeNull();
    expect(status()).toContain("1 shape");
  });
});
