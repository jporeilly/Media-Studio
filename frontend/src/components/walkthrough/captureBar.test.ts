// @vitest-environment jsdom
/**
 * The capture bar page (`public/capture-bar.html`) in its second mode, its script run as the shell runs it with the
 * bridge faked (`window.__TAURI__.event`): once its `capture:state` listener is up it asks the main window for the
 * state with `capture:control` `hello` (the main window sends the state on change only, so the first state would
 * otherwise be missed - W2-1); it draws the state it is told (the label, the name, the text, the instruction, the
 * buttons by phase) and shows the payload's `message` in red under the step - the key taken, the bar not hidden -
 * and nothing when there is none; its buttons send their actions. The recorder's mode sends no hello.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import html from "../../../public/capture-bar.html?raw";

interface Fake {
  handler: ((e: { payload: unknown }) => void) | null;
  emitted: { target: string; name: string; payload: unknown }[];
  /** `listen:<event>` and `emit:<action>` in the order they happened. */
  timeline: string[];
}
let fake: Fake;

async function load(mode: "steps" | "recording") {
  const script = /<script>([\s\S]*?)<\/script>/.exec(html)![1];
  const body = /<body>([\s\S]*?)<script>/.exec(html)![1];
  window.history.replaceState(null, "", mode === "steps" ? "/capture-bar.html?mode=steps" : "/capture-bar.html");
  // A fresh page each time: the body's class and title are the page's own to set.
  document.body.className = "";
  document.title = "";
  document.body.innerHTML = body;
  fake = { handler: null, emitted: [], timeline: [] };
  (window as unknown as Record<string, unknown>).__TAURI__ = {
    event: {
      listen: vi.fn(async (name: string, handler: (e: { payload: unknown }) => void) => {
        fake.timeline.push(`listen:${name}`);
        fake.handler = handler;
        return () => {};
      }),
      emitTo: vi.fn(async (target: string, name: string, payload: unknown) => {
        fake.emitted.push({ target, name, payload });
        fake.timeline.push(`emit:${(payload as { action?: string } | null)?.action}`);
      }),
    },
  };
  new Function(script)();
  await new Promise((r) => setTimeout(r, 0));
}

const text = (id: string) => document.getElementById(id)!.textContent;
const disabled = (id: string) => (document.getElementById(id) as HTMLButtonElement).disabled;
const tell = (payload: Record<string, unknown>) => fake.handler!({ payload: { mode: "steps", ...payload } });
const ready = {
  phase: "ready", name: "Tour", label: "Step 2 of 3", text: "Text b", instruction: "Do b", remaining: 2, canBack: false, canCapture: true, message: null,
};

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("the capture bar page in its steps mode", () => {
  it("asks the main window for the state once its listener is up - after the listen, before anything else - and only in steps mode", async () => {
    await load("steps");
    expect(document.body.classList.contains("mode-steps")).toBe(true);
    expect(document.title).toBe("Capture steps");
    expect(fake.timeline).toEqual(["listen:capture:state", "emit:hello"]);
    expect(fake.emitted).toEqual([{ target: "main", name: "capture:control", payload: { action: "hello" } }]);
    // Until the answer: "Starting…", nothing to press, no message.
    expect(text("s-label")).toBe("Starting…");
    expect(disabled("s-capture")).toBe(true);
    expect(text("s-message")).toBe("");
    await load("recording");
    expect(fake.timeline).toEqual(["listen:capture:state"]);
    expect(document.body.classList.contains("mode-steps")).toBe(false);
  });

  it("draws the state it is told, the buttons by phase, and the message in red under the step - nothing when there is none", async () => {
    await load("steps");
    tell(ready);
    expect([text("s-label"), text("s-name"), text("s-remaining"), text("s-text"), text("s-instruction")]).toEqual(["Step 2 of 3", "Tour", "2 to go", "Text b", "Do b"]);
    expect([disabled("s-capture"), disabled("s-next"), disabled("s-back"), disabled("s-done")]).toEqual([false, false, true, false]);
    expect(text("s-capture")).toBe("Capture");
    // An empty message leaves the line empty (the page's `.message:empty` rule hides it).
    expect(text("s-message")).toBe("");
    tell({ ...ready, message: "Shift+F8 or Shift+F9 is taken by another program: use Capture and Next on the bar." });
    expect(text("s-message")).toBe("Shift+F8 or Shift+F9 is taken by another program: use Capture and Next on the bar.");
    expect(disabled("s-capture")).toBe(false);
    // The next state without a message clears it. While a still is in flight Next stays enabled (W2d, R1: the attach lands
    // on its claimed step whatever Next does meanwhile); Back and Done wait.
    tell({ ...ready, phase: "capturing", canCapture: false });
    expect(text("s-message")).toBe("");
    expect(text("s-capture")).toBe("Capturing…");
    expect([disabled("s-capture"), disabled("s-next"), disabled("s-back"), disabled("s-done")]).toEqual([true, false, true, true]);
    tell({ ...ready, phase: "finished", label: "All steps visited", text: "Press Done to close the bar and go back to the builder.", instruction: "", remaining: 0, canBack: true, canCapture: false });
    expect([text("s-label"), text("s-remaining"), text("s-instruction")]).toEqual(["All steps visited", "", ""]);
    expect([disabled("s-capture"), disabled("s-next"), disabled("s-back"), disabled("s-done")]).toEqual([true, true, false, false]);
  });

  it("in click mode a second small line says so under the instruction, with typing when that is live, until every step is visited (W2b, W2d)", async () => {
    await load("steps");
    tell(ready);
    expect(text("s-hint")).toBe("");
    tell({ ...ready, clicks: true });
    expect(text("s-hint")).toBe("Click to capture · Shift+F8 captures without a click");
    expect(text("s-instruction")).toBe("Do b");
    // A capture's own refusal rides under it in red and the next state clears it; the hint stays.
    tell({ ...ready, clicks: true, message: "Click inside the frame to capture this step." });
    expect([text("s-hint"), text("s-message")]).toEqual(["Click to capture · Shift+F8 captures without a click", "Click inside the frame to capture this step."]);
    tell({ ...ready, clicks: true, phase: "capturing", canCapture: false });
    expect([text("s-hint"), text("s-message")]).toEqual(["Click to capture · Shift+F8 captures without a click", ""]);
    // Typing live (R3): the clause is added; typing alone names the key; the same words as lib/guided.ts hintLine.
    tell({ ...ready, clicks: true, typing: true });
    expect(text("s-hint")).toBe("Click to capture · Shift+F8 captures without a click · typing captures a second after you stop");
    tell({ ...ready, clicks: false, typing: true });
    expect(text("s-hint")).toBe("Shift+F8 captures · typing captures a second after you stop");
    // Off by the tick: no line. Every step visited: nothing left to click for.
    tell({ ...ready, clicks: false });
    expect(text("s-hint")).toBe("");
    tell({ ...ready, clicks: true, typing: true, phase: "finished", label: "All steps visited", canCapture: false });
    expect(text("s-hint")).toBe("");
  });

  it("the one button reads Skip with no still this step and Next with one; \"2 stills this step\" past one; the last still's thumbnail at the left, captioned, and nothing without one (W2d, R1, R4, R5)", async () => {
    await load("steps");
    const still = () => document.getElementById("s-still") as HTMLElement;
    const thumb = () => document.getElementById("s-thumb") as HTMLImageElement;
    tell(ready);
    expect(text("s-next")).toBe("Skip");
    expect(document.getElementById("s-next")!.title).toBe("Leave this step for later and move on (Shift+F9)");
    expect(text("s-stills")).toBe("");
    expect(still().style.display).toBe("none");
    // One still landed on the step: Next, and nothing said about the count (one is the normal case).
    tell({ ...ready, stillsThisStep: 1 });
    expect(text("s-next")).toBe("Next");
    expect(document.getElementById("s-next")!.title).toBe("Move on to the next step (Shift+F9): the last still stands");
    expect(text("s-stills")).toBe("");
    // Two: said under the step's text.
    tell({ ...ready, stillsThisStep: 2 });
    expect([text("s-next"), text("s-stills")]).toEqual(["Next", "2 stills this step"]);
    tell({ ...ready, stillsThisStep: 3 });
    expect(text("s-stills")).toBe("3 stills this step");
    // The last still of the run, whichever step: the thumbnail in the payload (a data URL: the bar fetches nothing) with its caption.
    const last = { step: 3, thumb: "data:image/jpeg;base64,abc" };
    tell({ ...ready, lastStill: last });
    expect(still().style.display).not.toBe("none");
    expect(thumb().getAttribute("src")).toBe(last.thumb);
    expect(text("s-thumb-caption")).toBe("step 3");
    // A payload without one shows nothing (and drops the picture).
    tell({ ...ready, lastStill: null });
    expect(still().style.display).toBe("none");
    expect(thumb().getAttribute("src")).toBe("");
    // The titles say what each button does now.
    expect(document.getElementById("s-capture")!.title).toContain("no hotspot");
    expect(document.getElementById("s-back")!.title).toBe("Back to the step before: a capture there replaces its picture");
    expect(document.getElementById("s-done")!.title).toBe("Close the bar and go back to the builder");
  });

  it("the buttons send their actions to the main window", async () => {
    await load("steps");
    tell({ ...ready, canBack: true });
    fake.emitted.length = 0;
    for (const id of ["s-capture", "s-next", "s-back", "s-done"]) document.getElementById(id)!.click();
    await new Promise((r) => setTimeout(r, 0));
    expect(fake.emitted.map((e) => [e.target, e.name, (e.payload as { action: string }).action])).toEqual([
      ["main", "capture:control", "capture"], ["main", "capture:control", "next"], ["main", "capture:control", "back"], ["main", "capture:control", "done"],
    ]);
  });

  it("in record mode (F1) the bar reads \"Recording · N steps\", the one button is Undo (sent as undo, enabled while there is a step to take back), Back is hidden, Done stays", async () => {
    await load("steps");
    const back = () => document.getElementById("s-back") as HTMLElement;
    tell({ ...ready, record: true, label: "Recording · 0 steps", text: "Every click in the application makes a step.", instruction: "", remaining: 0, canBack: false, canUndo: false, clicks: true });
    expect([text("s-label"), text("s-name"), text("s-remaining"), text("s-text"), text("s-instruction")]).toEqual(["Recording · 0 steps", "Tour", "", "Every click in the application makes a step.", ""]);
    expect(text("s-next")).toBe("Undo");
    expect(disabled("s-next")).toBe(true);
    expect(back().style.display).toBe("none");
    expect([disabled("s-capture"), disabled("s-done")]).toEqual([false, false]);
    expect(text("s-hint")).toBe("Click to capture · Shift+F8 captures without a click");
    expect(document.getElementById("s-capture")!.title).toContain("step");
    tell({ ...ready, record: true, label: "Recording · 2 steps", remaining: 0, canBack: false, canUndo: true, lastStill: { step: 2, thumb: "data:image/jpeg;base64,abc" } });
    expect([text("s-label"), text("s-next"), disabled("s-next")]).toEqual(["Recording · 2 steps", "Undo", false]);
    expect(document.getElementById("s-next")!.title).toBe("Take the last recorded step back");
    expect(text("s-thumb-caption")).toBe("step 2");
    // While a still is in flight Undo and Done wait, as Back does in steps mode.
    tell({ ...ready, record: true, phase: "capturing", canCapture: false, canUndo: false });
    expect([disabled("s-next"), disabled("s-done"), text("s-capture")]).toEqual([true, true, "Capturing…"]);
    tell({ ...ready, record: true, canUndo: true });
    fake.emitted.length = 0;
    document.getElementById("s-next")!.click();
    document.getElementById("s-done")!.click();
    await new Promise((r) => setTimeout(r, 0));
    expect(fake.emitted.map((e) => (e.payload as { action: string }).action)).toEqual(["undo", "done"]);
    // Back to steps mode: the button is Next / Skip again and Back is shown.
    tell({ ...ready, stillsThisStep: 1 });
    expect([text("s-next"), back().style.display]).toEqual(["Next", ""]);
    fake.emitted.length = 0;
    document.getElementById("s-next")!.click();
    await new Promise((r) => setTimeout(r, 0));
    expect(fake.emitted.map((e) => (e.payload as { action: string }).action)).toEqual(["next"]);
  });
});
