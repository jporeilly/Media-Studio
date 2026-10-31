// @vitest-environment jsdom
/**
 * The Studio card's Export to course box (W2c, C5): "Allow export into the Courses folder", off by default, the
 * studio's `courses_export` - an administrator ticks it and Save settings sends exactly that change; anyone else
 * sees it greyed out. And the Vimeo fields (V1): the access token is write-only - a password field that is never
 * filled from the server, sent as `vimeo_token` once, cleared with "" - with the state line under it, and the
 * privacy select. Mounted for real under jsdom with the server faked.
 */
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { StudioPayload } from "../../lib/studioSettings";
import { StudioCard } from "./StudioCard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const hoisted = vi.hoisted(() => ({ role: "admin" }));
vi.mock("../../context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", username: "someone", display_name: "Someone", role: hoisted.role } }),
}));
vi.mock("../../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../api/client")>();
  return { ...real, api: { ...real.api, get: vi.fn(), put: vi.fn() } };
});

const range = { min: 0, max: 5, step: 0.1 };
const PAYLOAD: StudioPayload = {
  settings: {
    tts_provider: "edge_tts", edge_tts_voice: "en-US-AriaNeural", kokoro_voice: "af_heart", kokoro_lang: "en-us", whisper_model: "",
    ollama_model: "gemma4:12b", output_folder: "C:\\out", transition_pause: 0, music_volume: 0.25, slide_transition: "none",
    transition_duration: 0.5, watermark_text: "", watermark_position: "bottom-right", watermark_opacity: 0.5,
    courses_folder: "C:\\Courses", courses_export: false,
    vimeo_token_set: false, vimeo_token_env: false, vimeo_privacy: "unlisted",
    content_editor_url: "http://localhost:8000",
  },
  options: {
    tts_provider: { options: [{ value: "edge_tts", label: "Edge TTS" }] },
    kokoro_lang: { options: [{ value: "en-us", label: "English (US)" }] },
    whisper_model: { options: [{ value: "", label: "Recommended default" }] },
    transition_pause: range, music_volume: { min: 0, max: 1, step: 0.05 }, slide_transition: { options: [{ value: "none", label: "None" }] },
    transition_duration: { min: 0.1, max: 2, step: 0.1 }, watermark_position: { options: [{ value: "bottom-right", label: "Bottom Right" }] },
    watermark_opacity: { min: 0.1, max: 1, step: 0.05 },
    vimeo_privacy: { options: [
      { value: "unlisted", label: "Unlisted", note: "Needs Vimeo Plus or higher." },
      { value: "private", label: "Private", note: "Needs Vimeo Plus or higher." },
      { value: "anybody", label: "Anybody", note: "A free account may set it." },
    ] },
  },
};
/** The payload with some settings changed: a server that holds a token, say. */
const withSettings = (over: Partial<StudioPayload["settings"]>): StudioPayload => ({ ...PAYLOAD, settings: { ...PAYLOAD.settings, ...over } });

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => { root!.render(createElement(QueryClientProvider, { client: qc }, createElement(StudioCard))); });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}
const box = () => document.querySelector("[data-courses-export]") as HTMLInputElement;
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === text) ?? null;
const q = (sel: string) => document.querySelector(sel) as HTMLElement | null;
const input = (sel: string) => q(sel) as HTMLInputElement;
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });
async function type(el: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function choose(el: HTMLSelectElement, value: string) {
  await act(async () => {
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

beforeEach(() => {
  hoisted.role = "admin";
  vi.mocked(api.get).mockReset();
  vi.mocked(api.put).mockReset();
  vi.mocked(api.get).mockImplementation(async (path: string) => (path.startsWith("/api/voices") ? { provider: "x", voices: [], error: null, notice: null } : PAYLOAD) as never);
  vi.mocked(api.put).mockImplementation(async (_path: string, body: unknown) => ({ ...PAYLOAD, settings: { ...PAYLOAD.settings, ...(body as object) } }) as never);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  document.body.innerHTML = "";
});

describe("Allow export into the Courses folder", () => {
  it("is off by default; an administrator ticks it and the save sends only that", async () => {
    await mount();
    expect(box().checked).toBe(false);
    expect(box().disabled).toBe(false);
    expect(box().closest("label")!.textContent).toContain("Allow export into the Courses folder");
    expect((button("Save settings") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { box().click(); });
    expect(box().checked).toBe(true);
    await act(async () => { button("Save settings")!.click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    expect(vi.mocked(api.put).mock.calls).toEqual([["/api/settings/studio", { courses_export: true }]]);
  });

  it("is greyed out for anyone but an administrator", async () => {
    hoisted.role = "editor";
    await mount();
    expect(box().disabled).toBe(true);
    expect(button("Save settings")).toBeNull();
  });
});

describe("The Vimeo access token and privacy", () => {
  it("is a password field the server never fills; a typed token is sent once as vimeo_token and the field empties again", async () => {
    await mount();
    const token = input("[data-vimeo-token]");
    expect(token.type).toBe("password");
    expect(token.value).toBe("");
    expect(token.placeholder).toBe("(not set)");
    expect(q("[data-vimeo-token-state]")!.textContent).toBe("No token is set.");
    expect(q("[data-vimeo-token-clear]")).toBeNull();
    expect((button("Save settings") as HTMLButtonElement).disabled).toBe(true);
    await type(token, "tok_secret");
    expect(q("[data-vimeo-token-state]")!.textContent).toBe("The new token will be stored when you save.");
    expect((button("Save settings") as HTMLButtonElement).disabled).toBe(false);
    // The server now holds a token: the PUT's answer says so, and so does the re-read the save triggers.
    vi.mocked(api.put).mockImplementation(async () => withSettings({ vimeo_token_set: true }) as never);
    vi.mocked(api.get).mockImplementation(async (path: string) => (path.startsWith("/api/voices") ? { provider: "x", voices: [], error: null, notice: null } : withSettings({ vimeo_token_set: true })) as never);
    await act(async () => { button("Save settings")!.click(); });
    await settle();
    expect(vi.mocked(api.put).mock.calls).toEqual([["/api/settings/studio", { vimeo_token: "tok_secret" }]]);
    expect(input("[data-vimeo-token]").value).toBe("");
    expect(input("[data-vimeo-token]").placeholder).toBe("(set — paste a new token to replace it)");
    expect(q("[data-vimeo-token-state]")!.textContent).toBe("A token is set.");
    expect(document.body.textContent).not.toContain("tok_secret");
  });

  it("Clear the token sends an empty string, and the environment's token is said to win", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => (path.startsWith("/api/voices") ? { provider: "x", voices: [], error: null, notice: null } : withSettings({ vimeo_token_set: true })) as never);
    await mount();
    expect(q("[data-vimeo-token-state]")!.textContent).toBe("A token is set.");
    await act(async () => { q("[data-vimeo-token-clear]")!.click(); });
    expect(q("[data-vimeo-token-state]")!.textContent).toBe("The token will be cleared when you save.");
    expect(q("[data-vimeo-token-clear]")).toBeNull();
    await act(async () => { button("Save settings")!.click(); });
    await settle();
    expect(vi.mocked(api.put).mock.calls).toEqual([["/api/settings/studio", { vimeo_token: "" }]]);

    vi.mocked(api.get).mockImplementation(async (path: string) => (path.startsWith("/api/voices") ? { provider: "x", voices: [], error: null, notice: null } : withSettings({ vimeo_token_set: true, vimeo_token_env: true })) as never);
    await act(async () => root?.unmount());
    await mount();
    expect(q("[data-vimeo-token-state]")!.textContent).toBe("Set by the environment (MEDIA_STUDIO_VIMEO_TOKEN) for this run; the stored token is not used.");
  });

  it("the privacy select sends vimeo_privacy with the server's labels, and both are greyed out for an editor", async () => {
    await mount();
    const select = q("[data-vimeo-privacy]") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Unlisted", "Private", "Anybody"]);
    expect(select.value).toBe("unlisted");
    await choose(select, "anybody");
    await act(async () => { button("Save settings")!.click(); });
    await settle();
    expect(vi.mocked(api.put).mock.calls).toEqual([["/api/settings/studio", { vimeo_privacy: "anybody" }]]);

    hoisted.role = "editor";
    await act(async () => root?.unmount());
    await mount();
    expect(input("[data-vimeo-token]").disabled).toBe(true);
    expect((q("[data-vimeo-privacy]") as HTMLSelectElement).disabled).toBe(true);
    expect(q("[data-vimeo-token-clear]")).toBeNull();
  });
});
