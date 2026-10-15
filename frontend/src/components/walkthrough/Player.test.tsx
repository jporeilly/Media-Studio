// @vitest-environment jsdom
/**
 * The walkthrough player (W1), mounted for real under jsdom with nothing faked but the window's size: the
 * keys (ArrowRight / Space / Enter on, ArrowLeft back, Home / End, Escape leaves; none while typing; Enter on
 * a focused button is the button's own), a hotspot click advances and a click on the picture outside it only
 * makes the hotspot pulse again, a modal step and a tooltip step without a hotspot are cards, the chapters
 * menu jumps and marks the current chapter, the progress, Finish -> the end card -> Restart, reduced motion
 * leaves the pulse out, the start step is clamped, and the component makes NO network call of its own.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Step, transcript } from "../../lib/walkthrough";
import { Player, type PlayerProps } from "./Player";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const S = (id: string, over: Partial<Step> = {}): Step => ({
  id, image: `step-${id}.png`, width: 400, height: 200, hotspot: { x: 100, y: 50, width: 40, height: 20 }, kind: "tooltip", text: `Text ${id}`,
  instruction: "", placement: "auto", chapter: "", note: "", source: null, ...over,
});
const THREE: Step[] = [S("one", { chapter: "Start" }), S("two", { kind: "modal", text: "A card" }), S("three", { chapter: "End", text: "Last words" })];

let root: Root | null = null;
let host: HTMLDivElement | null = null;
let exits = 0;
let lastProps: PlayerProps | null = null;

async function mount(over: Partial<PlayerProps> = {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const props: PlayerProps = { walkthrough: { name: "Tour", steps: THREE }, imageUrl: (s) => `/pic/${s.id}.png`, onExit: () => { exits += 1; }, ...over };
  lastProps = props;
  await act(async () => { root!.render(createElement(Player, props)); });
}

/** The same player given new props (a record refetched under it). */
async function rerender(over: Partial<PlayerProps>) {
  lastProps = { ...lastProps!, ...over };
  await act(async () => { root!.render(createElement(Player, lastProps!)); });
}

const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const progress = () => (q("[data-player-progress]")?.textContent || "").trim();
const hotspot = () => q("[data-player-hotspot]") as HTMLButtonElement | null;
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;
const key = (k: string, target: EventTarget = window, init: KeyboardEventInit = {}) =>
  act(async () => { target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...init })); });

beforeEach(() => {
  exits = 0;
  // The stage has a size, so the fit and the hotspot's rectangle are real numbers.
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 800 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 400 });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
  delete (window as { matchMedia?: unknown }).matchMedia;
});

describe("the player", () => {
  it("shows the name, the step label, the picture fitted, the hotspot labelled with the text and the tooltip beside it - with no network call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => { throw new Error("the player must not fetch"); });
    try {
      await mount();
      expect(document.body.textContent).toContain("Tour");
      expect(progress()).toBe("Step 1 of 3");
      const img = q("[data-player-stage] img") as HTMLImageElement;
      expect(img.getAttribute("src")).toBe("/pic/one.png");
      // 400 x 200 in 800 x 400: scale 2, at the origin.
      expect(img.style.left).toBe("0px");
      expect(img.style.width).toBe("800px");
      const spot = hotspot()!;
      expect(spot.getAttribute("aria-label")).toBe("Text one");
      expect(spot.style.left).toBe("200px");
      expect(spot.style.top).toBe("100px");
      expect(spot.style.width).toBe("80px");
      expect(spot.className).toContain("ms-hotspot-pulse");
      const tip = q("[data-player-tooltip]")!;
      expect(tip.getAttribute("role")).toBe("dialog");
      expect(tip.textContent).toBe("Text one");
      expect(tip.getAttribute("data-side")).toBe("bottom");
      expect(q("[data-player-modal]")).toBeNull();
      expect(document.activeElement).toBe(spot);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("ArrowRight, Space and Enter go on; ArrowLeft goes back; Home and End; Escape leaves; nothing while typing", async () => {
    await mount();
    await key("ArrowRight");
    expect(progress()).toBe("Step 2 of 3");
    await key("ArrowLeft");
    expect(progress()).toBe("Step 1 of 3");
    await key(" ", document.body);
    expect(progress()).toBe("Step 2 of 3");
    await key("Enter", document.body);
    expect(progress()).toBe("Step 3 of 3");
    await key("Home");
    expect(progress()).toBe("Step 1 of 3");
    await key("End");
    expect(progress()).toBe("Step 3 of 3");
    const input = document.createElement("input");
    document.body.appendChild(input);
    await key("ArrowLeft", input);
    expect(progress()).toBe("Step 3 of 3");
    await key("ArrowLeft", window, { ctrlKey: true });
    expect(progress()).toBe("Step 3 of 3");
    expect(exits).toBe(0);
    await key("Escape");
    expect(exits).toBe(1);
  });

  it("Enter on the focused hotspot is the button's own click, not a second step on", async () => {
    await mount();
    const spot = hotspot()!;
    await key("Enter", spot);
    expect(progress()).toBe("Step 1 of 3");
    await act(async () => { spot.click(); });
    expect(progress()).toBe("Step 2 of 3");
  });

  it("a click on the hotspot advances; a click on the picture outside it makes the hotspot pulse again and does not advance", async () => {
    await mount();
    const before = hotspot()!;
    await act(async () => { q("[data-player-stage]")!.click(); });
    expect(progress()).toBe("Step 1 of 3");
    const after = hotspot()!;
    expect(after).not.toBe(before);
    expect(after.className).toContain("ms-hotspot-pulse");
    await act(async () => { after.click(); });
    expect(progress()).toBe("Step 2 of 3");
  });

  it("a modal step is a centred card over the dimmed picture, with no tooltip; its Next goes on", async () => {
    await mount({ initialStep: 1 });
    expect(progress()).toBe("Step 2 of 3");
    const card = q("[data-player-modal]")!;
    expect(card.getAttribute("role")).toBe("dialog");
    expect(card.getAttribute("aria-modal")).toBe("true");
    expect(card.textContent).toContain("A card");
    expect(q(".ms-dim")).not.toBeNull();
    expect(q("[data-player-tooltip]")).toBeNull();
    const next = [...card.querySelectorAll("button")].find((b) => (b.textContent || "").trim().startsWith("Next"))!;
    expect(document.activeElement).toBe(next);
    await act(async () => { next.click(); });
    expect(progress()).toBe("Step 3 of 3");
  });

  it("W2: a step without a picture yet is a card on the backdrop - no image, no hotspot, never a broken picture - and Next goes on", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => { throw new Error("the player must not fetch"); });
    try {
      await mount({ walkthrough: { name: "Tour", steps: [S("one", { image: null, width: 0, height: 0, hotspot: null, text: "Open the dialog" }), S("two")] } });
      expect(q("[data-player-stage] img")).toBeNull();
      expect(hotspot()).toBeNull();
      expect(q("[data-player-tooltip]")).toBeNull();
      const card = q("[data-player-modal]")!;
      expect(card.textContent).toContain("Open the dialog");
      expect(q("[data-player-no-picture]")!.textContent).toBe("This step has no picture yet.");
      expect(q(".ms-dim")).not.toBeNull();
      await act(async () => { button("Next")!.click(); });
      expect(progress()).toBe("Step 2 of 3".replace("3", "2"));
      expect((q("[data-player-stage] img") as HTMLImageElement).getAttribute("src")).toBe("/pic/two.png");
      expect(q("[data-player-no-picture]")).toBeNull();
      // Without a text either: the card still stands, with the line alone.
      await rerender({ walkthrough: { name: "Tour", steps: [S("one", { image: null, width: 0, height: 0, hotspot: null, text: "" }), S("two")] }, initialStep: 0 });
      await act(async () => { key("Home"); });
      expect(q("[data-player-no-picture]")).not.toBeNull();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a tooltip step without a hotspot is shown as a modal", async () => {
    await mount({ walkthrough: { name: "T", steps: [S("one", { hotspot: null, text: "No spot" })] } });
    expect(hotspot()).toBeNull();
    expect(q("[data-player-tooltip]")).toBeNull();
    expect(q("[data-player-modal]")!.textContent).toContain("No spot");
  });

  it("the Chapters menu lists the chapters, marks the current one and jumps to a chapter's first step", async () => {
    await mount({ initialStep: 1 });
    const toggle = q("[data-player-chapters]")!;
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await act(async () => { toggle.click(); });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const items = [...document.querySelectorAll("[role=menuitem]")];
    expect(items.map((i) => (i.textContent || "").trim())).toEqual(["1 Start", "3 End"]);
    expect(items[0].getAttribute("aria-current")).toBe("true");
    expect(items[1].getAttribute("aria-current")).toBeNull();
    await act(async () => { (items[1] as HTMLButtonElement).click(); });
    expect(progress()).toBe("Step 3 of 3");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await act(async () => { toggle.click(); });
    expect([...document.querySelectorAll("[role=menuitem]")][1].getAttribute("aria-current")).toBe("true");
  });

  it("the progress bar follows the step; the last step's Next reads Finish and shows the end card, Restart goes to the first step, Close leaves", async () => {
    await mount();
    const bar = () => Number(q("[role=progressbar]")!.getAttribute("aria-valuenow"));
    expect(bar()).toBe(33);
    expect(button("Next")).not.toBeNull();
    await key("End");
    expect(bar()).toBe(100);
    expect(button("Next")).toBeNull();
    const finish = button("Finish")!;
    expect(q("[data-player-end]")).toBeNull();
    await act(async () => { finish.click(); });
    const end = q("[data-player-end]")!;
    expect(end.textContent).toContain("That is the end");
    expect(hotspot()).toBeNull();
    expect(finish.disabled).toBe(true);
    // ArrowRight at the end does nothing more; ArrowLeft takes the card away.
    await key("ArrowRight");
    expect(q("[data-player-end]")).not.toBeNull();
    await act(async () => { button("Restart")!.click(); });
    expect(q("[data-player-end]")).toBeNull();
    expect(progress()).toBe("Step 1 of 3");
    await key("End");
    await key("ArrowRight");
    expect(q("[data-player-end]")).not.toBeNull();
    await act(async () => { button("Close")!.click(); });
    expect(exits).toBe(1);
  });

  it("leaves the pulse out under reduced motion - the prop, or the media query", async () => {
    await mount({ reducedMotion: true });
    expect(hotspot()!.className).not.toContain("ms-hotspot-pulse");
    expect(q("[data-walkthrough-player]")!.getAttribute("data-reduced-motion")).toBe("true");
    await act(async () => root?.unmount());
    (window as unknown as { matchMedia: (q: string) => { matches: boolean } }).matchMedia = (query: string) => ({ matches: query.includes("reduce") });
    await mount();
    expect(hotspot()!.className).not.toContain("ms-hotspot-pulse");
    await act(async () => root?.unmount());
    (window as unknown as { matchMedia: (q: string) => { matches: boolean } }).matchMedia = () => ({ matches: false });
    await mount();
    expect(hotspot()!.className).toContain("ms-hotspot-pulse");
  });

  it("n2: Escape closes the Chapters menu before it leaves, and ArrowDown / ArrowUp move the focus within the menu, wrapping", async () => {
    await mount({ initialStep: 1 });
    await act(async () => { q("[data-player-chapters]")!.click(); });
    const items = () => [...document.querySelectorAll("[role=menuitem]")] as HTMLButtonElement[];
    expect(items()).toHaveLength(2);
    expect(document.activeElement).toBe(items()[0]); // the current chapter ("Start") takes the focus
    await key("ArrowDown");
    expect(document.activeElement).toBe(items()[1]);
    await key("ArrowDown");
    expect(document.activeElement).toBe(items()[0]);
    await key("ArrowUp");
    expect(document.activeElement).toBe(items()[1]);
    // Enter on the focused item is its own click: the chapter opens at its first step.
    await act(async () => { items()[1].click(); });
    expect(progress()).toBe("Step 3 of 3");
    await act(async () => { q("[data-player-chapters]")!.click(); });
    expect(q("[data-player-chapters]")!.getAttribute("aria-expanded")).toBe("true");
    await key("Escape");
    expect(q("[data-player-chapters]")!.getAttribute("aria-expanded")).toBe("false");
    expect(exits).toBe(0);
    await key("Escape");
    expect(exits).toBe(1);
    // With the menu closed the arrows do nothing to the step.
    await key("ArrowDown");
    expect(progress()).toBe("Step 3 of 3");
  });

  it("n3: the tooltip and the card are dialogs named by the step label", async () => {
    await mount();
    expect(q("[data-player-tooltip]")!.getAttribute("aria-label")).toBe("Step 1 of 3");
    await key("ArrowRight");
    expect(q("[data-player-modal]")!.getAttribute("aria-label")).toBe("Step 2 of 3");
  });

  it("n4: the step shown follows the clamped index when the walkthrough shrinks under the player, and Finish still finishes", async () => {
    await mount({ initialStep: 2 });
    expect(progress()).toBe("Step 3 of 3");
    await rerender({ walkthrough: { name: "Tour", steps: THREE.slice(0, 2) } });
    expect(progress()).toBe("Step 2 of 2");
    expect(button("Finish")).not.toBeNull();
    expect(Number(q("[role=progressbar]")!.getAttribute("aria-valuenow"))).toBe(100);
    await act(async () => { button("Finish")!.click(); });
    expect(q("[data-player-end]")).not.toBeNull();
  });

  it("the transcript: T (not while typing) and the header button open a named drawer with the Markdown; Copy and Save as…; Escape closes it before anything else", async () => {
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
      const input = document.createElement("input");
      document.body.appendChild(input);
      await key("t", input);
      expect(q("[data-player-transcript]")).toBeNull();
      await key("t");
      const drawer = q("[data-player-transcript]")!;
      expect(drawer.getAttribute("role")).toBe("dialog");
      expect(drawer.getAttribute("aria-label")).toBe("Transcript");
      expect(q("[data-transcript-text]")!.textContent).toBe(transcript({ name: "Tour", steps: THREE }));
      expect(q("[data-transcript-text]")!.textContent).toContain("## Start");
      expect(q("[data-player-transcript-toggle]")!.getAttribute("aria-expanded")).toBe("true");
      await act(async () => { q("[data-transcript-copy]")!.click(); });
      await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
      expect(writeText).toHaveBeenCalledWith(transcript({ name: "Tour", steps: THREE }));
      expect(q("[data-transcript-copied]")!.textContent).toContain("Copied");
      await act(async () => { q("[data-transcript-save]")!.click(); });
      expect(downloads).toEqual(["Tour.md"]);
      expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
      // Escape closes the drawer first - the player stays - and the Chapters menu would be next.
      await act(async () => { q("[data-player-chapters]")!.click(); });
      await key("Escape");
      expect(q("[data-player-transcript]")).toBeNull();
      expect(q("[data-player-chapters]")!.getAttribute("aria-expanded")).toBe("true");
      expect(exits).toBe(0);
      await key("Escape");
      expect(q("[data-player-chapters]")!.getAttribute("aria-expanded")).toBe("false");
      await key("Escape");
      expect(exits).toBe(1);
      // The header button opens it too, and T closes it again.
      await act(async () => { q("[data-player-transcript-toggle]")!.click(); });
      expect(q("[data-player-transcript]")).not.toBeNull();
      await key("T");
      expect(q("[data-player-transcript]")).toBeNull();
    } finally {
      click.mockRestore();
      urls.createObjectURL = hadCreate;
      urls.revokeObjectURL = hadRevoke;
      delete (navigator as unknown as { clipboard?: unknown }).clipboard;
    }
  });

  it("the start step is clamped; a tooltip placement asked for is used; an empty walkthrough says so", async () => {
    await mount({ initialStep: 99, walkthrough: { name: "T", steps: [S("a"), S("b", { placement: "top", text: "Up" })] } });
    expect(progress()).toBe("Step 2 of 2");
    expect(q("[data-player-tooltip]")!.getAttribute("data-side")).toBe("top");
    await act(async () => root?.unmount());
    await mount({ walkthrough: { name: "Empty", steps: [] } });
    expect(q("[data-player-empty]")).not.toBeNull();
    expect(progress()).toBe("No steps");
    await act(async () => { button("Close")!.click(); });
    expect(exits).toBe(1);
  });
});
