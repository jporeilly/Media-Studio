// @vitest-environment jsdom
/**
 * The builder's Make a video dialog (W3), mounted for real under jsdom with the server faked: the name is the
 * walkthrough's; the notes box is ticked when a step has a note and disabled with its line when none has; the
 * zoom box is ticked when a step has a hotspot and disabled with its line when none has; the steps line; the
 * projects made from this walkthrough before, each with when and an Open link - never another walkthrough's or
 * a deck; Make the video posts the name and the two choices and opens the project with its job to follow.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Step, WalkthroughDocument } from "../../lib/walkthrough";
import { MakeVideoDialog } from "./MakeVideoDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn() } };
});

const WID = "abc123abc123";
const step = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 200, height: 100, hotspot: null, kind: "tooltip", text: "", instruction: "", placement: "auto", chapter: "", note: "", source: null,
  click: "left", marker: "box", ...over,
});
const PROJECTS = [
  { id: "p1", name: "Your First Win", kind: "walkthrough", created_at: "2026-10-22T14:02:00Z", walkthrough_id: WID },
  { id: "p2", name: "Deck", kind: "deck", created_at: "2026-10-22T15:00:00Z" },
  { id: "p3", name: "Elsewhere", kind: "walkthrough", created_at: "2026-10-22T15:30:00Z", walkthrough_id: "fff000fff000" },
];

let root: Root | null = null;
let host: HTMLDivElement | null = null;
const opened: { path: string; state: unknown }[] = [];

function Opened() {
  const location = useLocation();
  opened.push({ path: location.pathname, state: location.state });
  return createElement("div", { "data-opened": location.pathname });
}

async function mount(doc: WalkthroughDocument, onClose = () => {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [`/walkthroughs/${WID}`] },
          createElement(Routes, null,
            createElement(Route, { path: "/walkthroughs/:id", element: createElement(MakeVideoDialog, { open: true, wid: WID, doc, onClose }) }),
            createElement(Route, { path: "/projects/:id", element: createElement(Opened) }),
          ),
        ),
      ),
    );
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const input = (sel: string) => q(sel) as HTMLInputElement;

beforeEach(() => {
  opened.length = 0;
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.get).mockImplementation(async () => ({ projects: PROJECTS }) as never);
  vi.mocked(api.post).mockImplementation(async () => ({ project_id: "p9", job_id: "J1" }) as never);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("Make a video", () => {
  it("starts from the walkthrough's name with the notes and the zoom ticked when the steps have them, and lists what it will use", async () => {
    await mount({ name: " Your First Win ", steps: [
      step("aaaaaaaaaaaa", { instruction: "Open it", hotspot: { x: 1, y: 1, width: 10, height: 10 }, note: "Under the hood." }),
      step("bbbbbbbbbbbb", { image: null, width: 0, height: 0, text: "Watch" }),
      step("cccccccccccc", { image: null, width: 0, height: 0 }),
    ] });
    expect(input("[data-video-name]").value).toBe("Your First Win");
    expect(input("[data-video-notes]").checked).toBe(true);
    expect(input("[data-video-notes]").disabled).toBe(false);
    expect(q("[data-video-no-notes]")).toBeNull();
    expect(input("[data-video-zoom]").checked).toBe(true);
    expect(input("[data-video-zoom]").disabled).toBe(false);
    expect(q("[data-video-no-hotspots]")).toBeNull();
    expect(q("[data-video-steps]")!.textContent).toBe("3 steps, 2 without a picture, 1 without any text");
  });

  it("disables the notes and the zoom with their lines when no step has a note or a hotspot", async () => {
    await mount({ name: "Plain", steps: [step("aaaaaaaaaaaa", { text: "Hi" })] });
    expect(input("[data-video-notes]").checked).toBe(false);
    expect(input("[data-video-notes]").disabled).toBe(true);
    expect(q("[data-video-no-notes]")!.textContent).toBe("No step has a note.");
    expect(input("[data-video-zoom]").checked).toBe(false);
    expect(input("[data-video-zoom]").disabled).toBe(true);
    expect(q("[data-video-no-hotspots]")!.textContent).toBe("No step has a hotspot.");
    expect(q("[data-video-steps]")!.textContent).toBe("1 step");
  });

  it("lists the projects made from this walkthrough before, with when and an Open link, and no other", async () => {
    await mount({ name: "Tour", steps: [step("aaaaaaaaaaaa", { text: "Hi" })] });
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/projects");
    const made = q("[data-video-made]")!;
    expect(made.textContent).toContain("Made before:");
    expect(made.textContent).toContain("Your First Win");
    expect(made.textContent).not.toContain("Elsewhere");
    expect(made.textContent).not.toContain("Deck");
    const link = made.querySelector("a") as HTMLAnchorElement;
    expect(link.textContent).toBe("Open");
    expect(link.getAttribute("href")).toBe("/projects/p1");
    expect(made.querySelectorAll("[data-video-made-project]").length).toBe(1);
  });

  it("says nothing of projects made before when there are none", async () => {
    vi.mocked(api.get).mockImplementation(async () => ({ projects: [PROJECTS[1]] }) as never);
    await mount({ name: "Tour", steps: [step("aaaaaaaaaaaa", { text: "Hi" })] });
    expect(q("[data-video-made]")).toBeNull();
  });

  it("Make the video posts the name and the choices and opens the project with its job to follow", async () => {
    const onClose = vi.fn();
    await mount({ name: "Tour", steps: [
      step("aaaaaaaaaaaa", { instruction: "Open it", hotspot: { x: 1, y: 1, width: 10, height: 10 }, note: "Note." }),
    ] }, onClose);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input("[data-video-name]"), " My video ");
      input("[data-video-name]").dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { input("[data-video-notes]").click(); });
    expect(input("[data-video-notes]").checked).toBe(false);
    await act(async () => { (q("[data-video-make]") as HTMLButtonElement).click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([`/api/walkthroughs/${WID}/video`, { name: "My video", notes: false, zoom: true }]);
    expect(onClose).toHaveBeenCalled();
    expect(opened.at(-1)).toEqual({ path: "/projects/p9", state: { followJob: { id: "J1", kind: "generate" } } });
    expect(q("[data-opened]")!.getAttribute("data-opened")).toBe("/projects/p9");
  });

  it("a name emptied disables Make the video; a refusal is shown", async () => {
    vi.mocked(api.post).mockImplementation(async () => { throw new Error("This walkthrough has no steps yet."); });
    await mount({ name: "Tour", steps: [step("aaaaaaaaaaaa", { text: "Hi" })] });
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input("[data-video-name]"), "   ");
      input("[data-video-name]").dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect((q("[data-video-make]") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input("[data-video-name]"), "Again");
      input("[data-video-name]").dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect((q("[data-video-make]") as HTMLButtonElement).disabled).toBe(false);
    await act(async () => { (q("[data-video-make]") as HTMLButtonElement).click(); });
    await settle();
    expect(document.body.textContent).toContain("This walkthrough has no steps yet.");
    expect(opened.length).toBe(0);
  });
});
