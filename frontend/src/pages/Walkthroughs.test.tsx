// @vitest-environment jsdom
/**
 * The Walkthroughs list page (W1), mounted for real under jsdom with the server faked: the cards (the first
 * step's picture, the name, "N steps · M chapters", when), Open, Play (from the list), Duplicate, Delete
 * with a confirm, New walkthrough asking for a name, the empty state that says how (and that a still needs
 * the desktop app), and a notice carried in by the route.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { type WalkthroughSummary, EMPTY_HOW } from "../lib/walkthrough";
import WalkthroughsPage from "./Walkthroughs";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), post: vi.fn(), delete: vi.fn() } };
});

const tour: WalkthroughSummary = {
  id: "tour00000001", name: "Onboarding tour", owner_id: "u1", owner_name: "Olive", created_at: "2026-10-12T10:00:00Z", updated_at: "2026-10-12T11:00:00Z",
  revision: 4, step_count: 2, chapter_count: 1, first_step_id: "step00000001",
};
const empty: WalkthroughSummary = { ...tour, id: "tour00000002", name: "Nothing yet", step_count: 0, chapter_count: 0, first_step_id: null, revision: 1 };

let root: Root | null = null;
let host: HTMLDivElement | null = null;

function BuilderStub() {
  const { id } = useParams();
  return createElement("div", { "data-builder": id }, "builder");
}
function PlayStub() {
  const { id } = useParams();
  const location = useLocation();
  return createElement("div", { "data-play": id, "data-state": JSON.stringify(location.state) }, "play");
}

async function mount(state: unknown = null) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client: qc },
        createElement(MemoryRouter, { initialEntries: [{ pathname: "/walkthroughs", state }] },
          createElement(Routes, null,
            createElement(Route, { path: "/walkthroughs", element: createElement(WalkthroughsPage) }),
            createElement(Route, { path: "/walkthroughs/:id", element: createElement(BuilderStub) }),
            createElement(Route, { path: "/walkthroughs/:id/play", element: createElement(PlayStub) }),
          ),
        ),
      ),
    );
  });
  await settle();
}
const settle = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const card = (id: string) => q(`[data-walkthrough="${id}"]`)!;
const buttonIn = (el: ParentNode, text: string) => [...el.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.post).mockReset();
  vi.mocked(api.delete).mockReset();
  vi.mocked(api.get).mockImplementation(async () => ({ walkthroughs: [tour, empty] }) as never);
  vi.mocked(api.delete).mockImplementation(async () => undefined as never);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("the list", () => {
  it("shows a card per walkthrough with the first step's picture, the counts and when; Play is for one with steps", async () => {
    await mount();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/walkthroughs");
    const c = card(tour.id);
    expect(c.textContent).toContain("Onboarding tour");
    expect(c.querySelector("[data-walkthrough-counts]")!.textContent).toContain("2 steps · 1 chapter");
    expect(c.querySelector("img")!.getAttribute("src")).toBe(`/api/walkthroughs/${tour.id}/steps/${tour.first_step_id}/image?v=4`);
    expect(card(empty.id).querySelector("img")).toBeNull();
    expect(card(empty.id).querySelector("[data-walkthrough-counts]")!.textContent).toContain("0 steps");
    expect(buttonIn(card(empty.id), "Play")!.disabled).toBe(true);
    expect(buttonIn(c, "Play")!.disabled).toBe(false);
  });

  it("Open goes to the builder; Play opens the player from the list", async () => {
    await mount();
    await act(async () => { buttonIn(card(tour.id), "Play")!.click(); });
    expect(q("[data-play]")!.getAttribute("data-play")).toBe(tour.id);
    expect(JSON.parse(q("[data-play]")!.getAttribute("data-state")!)).toEqual({ from: "list" });
    await act(async () => root?.unmount());
    await mount();
    await act(async () => { buttonIn(card(tour.id), "Open")!.click(); });
    expect(q("[data-builder]")!.getAttribute("data-builder")).toBe(tour.id);
  });

  it("Duplicate copies it on the server and says so; Delete asks first, then deletes", async () => {
    vi.mocked(api.post).mockImplementation(async () => ({ ...tour, id: "copy00000001", name: "Onboarding tour (copy)", steps: [] }) as never);
    await mount();
    await act(async () => { buttonIn(card(tour.id), "Duplicate")!.click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith(`/api/walkthroughs/${tour.id}/duplicate`);
    expect(q("[data-walkthroughs-notice]")!.textContent).toContain('Copied as "Onboarding tour (copy)".');
    await act(async () => { (card(tour.id).querySelector(`button[aria-label="Delete ${tour.name}"]`) as HTMLButtonElement).click(); });
    expect(q("[role=dialog]")!.textContent).toContain("Delete walkthrough");
    await act(async () => { buttonIn(document, "Cancel")!.click(); });
    expect(vi.mocked(api.delete)).not.toHaveBeenCalled();
    await act(async () => { (card(tour.id).querySelector(`button[aria-label="Delete ${tour.name}"]`) as HTMLButtonElement).click(); });
    await act(async () => { buttonIn(q("[role=dialog]")!, "Delete")!.click(); });
    await settle();
    expect(vi.mocked(api.delete)).toHaveBeenCalledWith(`/api/walkthroughs/${tour.id}`);
  });

  it("New walkthrough asks for a name, creates it and opens the builder", async () => {
    vi.mocked(api.post).mockImplementation(async () => ({ ...tour, id: "new000000001", name: "Fresh", steps: [] }) as never);
    await mount();
    await act(async () => { q("[data-new-walkthrough]")!.click(); });
    const dialog = q("[role=dialog]")!;
    expect(dialog.textContent).toContain("New walkthrough");
    expect(buttonIn(dialog, "Create")!.disabled).toBe(true);
    const input = dialog.querySelector("[data-walkthrough-name]") as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "  Fresh ");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/walkthroughs", { name: "Fresh" });
    expect(q("[data-builder]")!.getAttribute("data-builder")).toBe("new000000001");
  });

  it("W2: Import workshop… lists the courses and workshops, previews the parse, imports on the server and opens the builder with a notice", async () => {
    const payload = {
      configured: true, message: null,
      courses: [{
        id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.21", description: "",
        sections: [
          { title: "Get Ready", workshops: [{ slug: "00-before", title: "Before You Start", description: "", estimatedMinutes: 5, stepCount: 5 }] },
          { title: "See It Work", workshops: [{ slug: "01-first-win", title: "Your First Win", description: "", estimatedMinutes: 10, stepCount: 6 }] },
        ],
      }],
    };
    const preview = (slug: string) => ({
      course: { id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.21" },
      workshop: { slug, title: slug === "01-first-win" ? "Your First Win" : "Before You Start", section: "", estimatedMinutes: 10, stepCount: 6 },
      name: slug === "01-first-win" ? "Your First Win" : "Before You Start",
      chapters: ["Open the pipeline"],
      steps: [
        { number: 1, chapter: "Open the pipeline", instruction: "Open win_preview.ktr in Pentaho Data Integration.", text: "Open win_preview.ktr in Pentaho Data Integration.", picture: false, image: null, alt: "", launcher: { id: "spoon", path: "files/win_preview.ktr", label: "Open" }, note: false },
        { number: 2, chapter: "", instruction: "Right-click the step named Valid rows.", text: "Right-click the step named Valid rows.", picture: true, image: "a.png", alt: "a", launcher: null, note: true },
      ],
      counts: slug === "01-first-win" ? { steps: 10, chapters: 4, pictures: 2, to_capture: 8 } : { steps: 1, chapters: 1, pictures: 1, to_capture: 0 },
      message: null,
    });
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/api/walkthroughs") return { walkthroughs: [tour] } as never;
      if (path === "/api/courses") return payload as never;
      if (path === "/api/courses/pdi-2hr-lab/workshops/00-before/preview") return preview("00-before") as never;
      if (path === "/api/courses/pdi-2hr-lab/workshops/01-first-win/preview") return preview("01-first-win") as never;
      throw new Error(`unexpected GET ${path}`);
    });
    vi.mocked(api.post).mockImplementation(async () => ({ ...tour, id: "imp000000001", name: "Your First Win", steps: [] }) as never);
    await mount();
    await act(async () => { q("[data-import-workshop]")!.click(); });
    await settle();
    const dialog = q("[role=dialog]")!;
    expect(dialog.textContent).toContain("Import workshop");
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/courses");
    const courseSelect = dialog.querySelector("[data-import-course]") as HTMLSelectElement;
    expect([...courseSelect.options].map((o) => o.textContent)).toEqual(["PDI in 2 Hours (0.1.21)"]);
    const workshopSelect = dialog.querySelector("[data-import-slug]") as HTMLSelectElement;
    expect([...workshopSelect.querySelectorAll("optgroup")].map((g) => g.getAttribute("label"))).toEqual(["Get Ready", "See It Work"]);
    expect([...workshopSelect.options].map((o) => o.textContent)).toEqual(["Before You Start (5 min · 5 steps)", "Your First Win (10 min · 6 steps)"]);
    expect(workshopSelect.value).toBe("00-before");
    await settle();
    expect(q("[data-import-line]")!.textContent).toBe("1 step in 1 chapter; 1 picture from the guide, 0 to capture");
    // Another workshop: its preview, with the steps and whether each has a picture.
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(workshopSelect, "01-first-win");
      workshopSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/courses/pdi-2hr-lab/workshops/01-first-win/preview");
    expect(q("[data-import-line]")!.textContent).toBe("10 steps in 4 chapters; 2 pictures from the guide, 8 to capture");
    const rows = [...document.querySelectorAll("[data-import-steps] li")];
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain("Open the pipeline");
    expect(rows[0].querySelector("[aria-label='to capture']")).not.toBeNull();
    expect(rows[1].querySelector("[aria-label='picture from the guide']")).not.toBeNull();
    expect((q("[data-import-name]") as HTMLInputElement).placeholder).toBe("Your First Win");
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
    await act(async () => { (q("[data-import-confirm]") as HTMLButtonElement).click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/walkthroughs/import", { course: "pdi-2hr-lab", slug: "01-first-win" });
    expect(q("[data-builder]")!.getAttribute("data-builder")).toBe("imp000000001");
  });

  it("W2: with no courses folder set, the dialog says where to set it and offers no Import", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/api/walkthroughs") return { walkthroughs: [] } as never;
      if (path === "/api/courses") return { configured: false, message: "Set the Courses folder on the Settings page (Studio settings, an administrator) first.", courses: [] } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    await mount();
    // The empty state offers the import too.
    await act(async () => { q("[data-import-workshop]")!.click(); });
    await settle();
    expect(q("[data-import-unset]")!.textContent).toContain("Set the Courses folder on the Settings page");
    expect((q("[data-import-confirm]") as HTMLButtonElement).disabled).toBe(true);
    expect(q("[data-import-course]")).toBeNull();
  });

  it("the empty state says how a walkthrough is made and that a still needs the desktop app; a route notice is shown once", async () => {
    vi.mocked(api.get).mockImplementation(async () => ({ walkthroughs: [] }) as never);
    await mount({ notice: "That walkthrough is gone." });
    expect(document.body.textContent).toContain("No walkthroughs yet");
    expect(document.body.textContent).toContain(EMPTY_HOW);
    expect(EMPTY_HOW).toContain("desktop app");
    expect(EMPTY_HOW).toContain("browser edition");
    expect(q("[data-walkthroughs-notice]")!.textContent).toContain("That walkthrough is gone.");
    await act(async () => { (q("[data-walkthroughs-notice] button") as HTMLButtonElement).click(); });
    expect(q("[data-walkthroughs-notice]")).toBeNull();
  });
});
