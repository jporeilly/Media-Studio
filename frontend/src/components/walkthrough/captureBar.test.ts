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
    expect([disabled("s-capture"), disabled("s-skip"), disabled("s-back"), disabled("s-done")]).toEqual([false, false, true, false]);
    expect(text("s-capture")).toBe("Capture");
    // An empty message leaves the line empty (the page's `.message:empty` rule hides it).
    expect(text("s-message")).toBe("");
    tell({ ...ready, message: "Shift+F8 is taken by another program: use Capture on the bar." });
    expect(text("s-message")).toBe("Shift+F8 is taken by another program: use Capture on the bar.");
    expect(disabled("s-capture")).toBe(false);
    // The next state without a message clears it.
    tell({ ...ready, phase: "capturing", canCapture: false });
    expect(text("s-message")).toBe("");
    expect(text("s-capture")).toBe("Capturing…");
    expect([disabled("s-capture"), disabled("s-skip"), disabled("s-back"), disabled("s-done")]).toEqual([true, true, true, true]);
    tell({ ...ready, phase: "finished", label: "All steps visited", text: "Press Done to close the bar and go back to the builder.", instruction: "", remaining: 0, canBack: true, canCapture: false });
    expect([text("s-label"), text("s-remaining"), text("s-instruction")]).toEqual(["All steps visited", "", ""]);
    expect([disabled("s-capture"), disabled("s-skip"), disabled("s-back"), disabled("s-done")]).toEqual([true, true, false, false]);
  });

  it("the buttons send their actions to the main window", async () => {
    await load("steps");
    tell({ ...ready, canBack: true });
    fake.emitted.length = 0;
    for (const id of ["s-capture", "s-skip", "s-back", "s-done"]) document.getElementById(id)!.click();
    await new Promise((r) => setTimeout(r, 0));
    expect(fake.emitted.map((e) => [e.target, e.name, (e.payload as { action: string }).action])).toEqual([
      ["main", "capture:control", "capture"], ["main", "capture:control", "skip"], ["main", "capture:control", "back"], ["main", "capture:control", "done"],
    ]);
  });
});
