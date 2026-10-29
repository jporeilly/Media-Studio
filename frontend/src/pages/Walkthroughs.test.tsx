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
import { ApiError, api } from "../api/client";
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

  /** W2e: a course of three workshops in two sections; the first workshop's one step comes before any heading (a launcher). */
  const LAB_PAYLOAD = {
    configured: true, message: null,
    courses: [{
      id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.21", description: "",
      sections: [
        { title: "Get Ready", workshops: [{ slug: "00-before", title: "Before You Start", description: "", estimatedMinutes: 5, stepCount: 5 }] },
        { title: "See It Work", workshops: [
          { slug: "01-first-win", title: "Your First Win", description: "", estimatedMinutes: 10, stepCount: 6 },
          { slug: "02-build", title: "Build the Pipeline Yourself", description: "", estimatedMinutes: 25, stepCount: 23 },
        ] },
      ],
    }],
  };
  const row = (number: number, chapter: string, in_chapter: string, text: string, picture: boolean) => (
    { number, chapter, in_chapter, instruction: text, text, picture, image: picture ? "a.png" : null, alt: "", launcher: null, note: false });
  const LAB_PREVIEWS: Record<string, { title: string; steps: ReturnType<typeof row>[]; counts: { steps: number; chapters: number; pictures: number; to_capture: number } }> = {
    "00-before": { title: "Before You Start", steps: [row(1, "", "", "Open Pentaho Data Integration.", true)], counts: { steps: 1, chapters: 0, pictures: 1, to_capture: 0 } },
    "01-first-win": { title: "Your First Win", steps: [
      row(1, "Open the pipeline", "Open the pipeline", "Open win_preview.ktr in Pentaho Data Integration.", false),
      row(2, "", "Open the pipeline", "Right-click the step named Valid rows.", true),
      row(3, "Troubleshooting a bad file", "Troubleshooting a bad file", "Fix the file.", false),
    ], counts: { steps: 3, chapters: 2, pictures: 1, to_capture: 2 } },
    "02-build": { title: "Build the Pipeline Yourself", steps: [
      row(1, "Read the file", "Read the file", "Drag the Text file input.", true), row(2, "", "Read the file", "Press Run.", true),
    ], counts: { steps: 2, chapters: 1, pictures: 2, to_capture: 0 } },
  };
  const labPreview = (slug: string) => ({
    course: { id: "pdi-2hr-lab", title: "PDI in 2 Hours", version: "0.1.21" },
    workshop: { slug, title: LAB_PREVIEWS[slug].title, section: "", estimatedMinutes: 10, stepCount: 6 },
    name: LAB_PREVIEWS[slug].title, chapters: [], steps: LAB_PREVIEWS[slug].steps, counts: LAB_PREVIEWS[slug].counts, message: null,
  });
  /** The server faked for the lab: the list, the courses and every preview - `failing` answers a 400 for that slug. */
  const serveLab = (failing: string | null = null) => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/api/walkthroughs") return { walkthroughs: [tour] } as never;
      if (path === "/api/courses") return LAB_PAYLOAD as never;
      const m = /^\/api\/courses\/pdi-2hr-lab\/workshops\/([^/]+)\/preview$/.exec(path);
      if (m && m[1] === failing) throw new ApiError(400, `pdi-2hr-lab/${m[1]}/guide.md cannot be read as UTF-8 text.`);
      if (m && LAB_PREVIEWS[m[1]]) return labPreview(m[1]) as never;
      throw new Error(`unexpected GET ${path}`);
    });
  };

  it("W2e: Import workshop… ticks workshops by section, previews every ticked one in the course's order, lets chapters out, names and imports them", async () => {
    serveLab();
    vi.mocked(api.post).mockImplementation(async () => ({ ...tour, id: "imp000000001", name: "See It Work", steps: [] }) as never);
    await mount();
    await act(async () => { q("[data-import-workshop]")!.click(); });
    await settle();
    const dialog = q("[role=dialog]")!;
    expect(dialog.textContent).toContain("Import workshop");
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/courses");
    const courseSelect = dialog.querySelector("[data-import-course]") as HTMLSelectElement;
    expect([...courseSelect.options].map((o) => o.textContent)).toEqual(["PDI in 2 Hours (0.1.21)"]);
    // The tick list, grouped by section as SUMMARY.md orders them; the first workshop ticked by default.
    const sections = [...dialog.querySelectorAll("[data-import-section]")] as HTMLInputElement[];
    expect(sections.map((s) => s.getAttribute("data-import-section"))).toEqual(["Get Ready", "See It Work"]);
    const tick = (slug: string) => dialog.querySelector(`[data-import-tick="${slug}"]`) as HTMLInputElement;
    const ticks = [...dialog.querySelectorAll("[data-import-tick]")] as HTMLInputElement[];
    expect(ticks.map((t) => t.getAttribute("data-import-tick"))).toEqual(["00-before", "01-first-win", "02-build"]);
    expect(ticks.map((t) => t.checked)).toEqual([true, false, false]);
    expect(dialog.textContent).toContain("Build the Pipeline Yourself (25 min · 23 steps)");
    await settle();
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/courses/pdi-2hr-lab/workshops/00-before/preview");
    // One workshop: the server's own count - its one step before any heading starts no chapter (X1).
    expect(q("[data-import-line]")!.textContent).toBe("1 step in no chapters; 1 picture from the guide, 0 to capture");
    expect([...dialog.querySelectorAll('[data-import-workshop-preview="00-before"] [data-import-chapter]')].map((c) => c.getAttribute("data-import-chapter"))).toEqual([""]);
    expect(dialog.querySelector('[data-import-workshop-preview="00-before"]')!.textContent).toContain("(before the first section)");
    expect((q("[data-import-name]") as HTMLInputElement).placeholder).toBe("Before You Start");
    // None ticked: no preview, Import greyed out.
    await act(async () => { tick("00-before").click(); });
    await settle();
    expect((q("[data-import-confirm]") as HTMLButtonElement).disabled).toBe(true);
    expect(q("[data-import-line]")).toBeNull();
    // The section's tick takes every workshop of it; the previews are read in parallel and shown in the course's order.
    await act(async () => { sections[1].click(); });
    await settle();
    expect([...dialog.querySelectorAll("[data-import-tick]")].map((t) => (t as HTMLInputElement).checked)).toEqual([false, true, true]);
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/courses/pdi-2hr-lab/workshops/01-first-win/preview");
    expect(vi.mocked(api.get)).toHaveBeenCalledWith("/api/courses/pdi-2hr-lab/workshops/02-build/preview");
    expect([...dialog.querySelectorAll("[data-import-workshop-preview]")].map((b) => b.getAttribute("data-import-workshop-preview"))).toEqual(["01-first-win", "02-build"]);
    expect(q("[data-import-line]")!.textContent).toBe("5 steps in 3 chapters; 3 pictures from the guide, 2 to capture");
    expect((q("[data-import-name]") as HTMLInputElement).placeholder).toBe("See It Work");
    expect(sections[1].checked).toBe(true);
    // Every workshop of the course: the course's title; back to the first section's state afterwards.
    await act(async () => { tick("00-before").click(); });
    await settle();
    expect((q("[data-import-name]") as HTMLInputElement).placeholder).toBe("PDI in 2 Hours");
    expect([...dialog.querySelectorAll("[data-import-workshop-preview]")].map((b) => b.getAttribute("data-import-workshop-preview"))).toEqual(["00-before", "01-first-win", "02-build"]);
    // X1: with several workshops the first workshop's step before its heading starts a chapter named by the workshop - the
    // server's count (6 steps, 4 chapters), on the combined line and on the workshop's own line, which add up.
    expect(q("[data-import-line]")!.textContent).toBe("6 steps in 4 chapters; 4 pictures from the guide, 2 to capture");
    expect(dialog.querySelector('[data-import-workshop-preview="00-before"] [data-import-workshop-line]')!.textContent).toBe("1 step in 1 chapter; 1 picture from the guide, 0 to capture");
    await act(async () => { tick("00-before").click(); });
    await settle();
    expect(sections[0].checked).toBe(false);
    // Each workshop's block: its title, its line, its chapters as ticks with the steps under them (a camera for a
    // picture from the guide, a crossed-out picture to capture).
    const first = dialog.querySelector('[data-import-workshop-preview="01-first-win"]')!;
    expect(first.querySelector("[data-import-workshop-title]")!.textContent).toBe("Your First Win");
    expect(first.querySelector("[data-import-workshop-line]")!.textContent).toBe("3 steps in 2 chapters; 1 picture from the guide, 2 to capture");
    expect([...first.querySelectorAll("[data-import-chapter]")].map((c) => c.getAttribute("data-import-chapter"))).toEqual(["Open the pipeline", "Troubleshooting a bad file"]);
    const rows = [...first.querySelectorAll("[data-import-steps] li")];
    expect(rows.length).toBe(3);
    expect(rows[0].querySelector("[aria-label='to capture']")).not.toBeNull();
    expect(rows[1].querySelector("[aria-label='picture from the guide']")).not.toBeNull();
    // A chapter unticked: its steps leave the counts, the workshop's line and the combined line.
    const trouble = first.querySelector('[data-import-chapter="Troubleshooting a bad file"] [data-import-chapter-tick]') as HTMLInputElement;
    expect(trouble.checked).toBe(true);
    await act(async () => { trouble.click(); });
    await settle();
    expect(first.querySelector("[data-import-workshop-line]")!.textContent).toBe("2 steps in 1 chapter; 1 picture from the guide, 1 to capture");
    expect(q("[data-import-line]")!.textContent).toBe("4 steps in 2 chapters; 3 pictures from the guide, 1 to capture");
    expect(first.querySelector('[data-import-chapter="Troubleshooting a bad file"]')!.className).toContain("ms-import-skipped");
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
    await act(async () => { (q("[data-import-confirm]") as HTMLButtonElement).click(); });
    await settle();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/walkthroughs/import", {
      course: "pdi-2hr-lab", workshops: [{ slug: "01-first-win", skip_chapters: ["Troubleshooting a bad file"] }, { slug: "02-build" }],
    });
    expect(q("[data-builder]")!.getAttribute("data-builder")).toBe("imp000000001");
  });

  it("X2: a workshop whose preview fails is named; the other workshops' blocks stay with their own lines, there is no combined line and Import is greyed", async () => {
    serveLab("02-build");
    await mount();
    await act(async () => { q("[data-import-workshop]")!.click(); });
    await settle();
    const dialog = q("[role=dialog]")!;
    const sections = [...dialog.querySelectorAll("[data-import-section]")] as HTMLInputElement[];
    await act(async () => { sections[1].click(); });
    await settle();
    expect([...dialog.querySelectorAll("[data-import-tick]")].map((t) => (t as HTMLInputElement).checked)).toEqual([true, true, true]);
    expect(dialog.textContent).toContain("02-build: pdi-2hr-lab/02-build/guide.md cannot be read as UTF-8 text.");
    expect([...dialog.querySelectorAll("[data-import-workshop-preview]")].map((b) => b.getAttribute("data-import-workshop-preview"))).toEqual(["00-before", "01-first-win"]);
    expect([...dialog.querySelectorAll("[data-import-workshop-line]")].map((l) => l.textContent)).toEqual([
      "1 step in 1 chapter; 1 picture from the guide, 0 to capture", "3 steps in 2 chapters; 1 picture from the guide, 2 to capture",
    ]);
    expect(q("[data-import-line]")).toBeNull();
    expect((q("[data-import-confirm]") as HTMLButtonElement).disabled).toBe(true);
    // The named workshop unticked: the combined line is back and Import is on.
    await act(async () => { (dialog.querySelector('[data-import-tick="02-build"]') as HTMLInputElement).click(); });
    await settle();
    expect(dialog.textContent).not.toContain("cannot be read");
    expect(q("[data-import-line]")!.textContent).toBe("4 steps in 3 chapters; 2 pictures from the guide, 2 to capture");
    expect((q("[data-import-confirm]") as HTMLButtonElement).disabled).toBe(false);
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
