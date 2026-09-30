/**
 * The Transcript card's MARKUP for E7: the **Transcribe again** button (there with a transcript, waiting for a
 * job, gone without a transcript) and the line the card keeps for a job the server lost.
 *
 * Rendered through `renderToStaticMarkup` as NarrationTimeline.render.test.tsx renders the timeline: no DOM,
 * no new dependency. The timeline inside the card gets no data here and draws its loading state; what this
 * pins is the card around it. The confirm dialog is opened by a click and so is not reachable here - its text
 * is `retranscribeEffects` (lib/narration.test.ts) - and the live check is still the owner's.
 */
import { beforeAll, describe, expect, it } from "vitest";
import { type ComponentProps, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TranscriptCard } from "./TranscriptCard";
import { LOST_JOB_MESSAGE } from "../../lib/jobs";
import type { Segment } from "../../lib/narration";

const stored = new Map<string, string>();
beforeAll(() => {
  // The timeline inside the card reads its lane locks from localStorage while it renders; node has none.
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => { stored.set(key, value); },
    removeItem: (key: string) => { stored.delete(key); },
  };
});

const SEGMENTS: Segment[] = [
  { start: 0, end: 2, text: "First sentence.", offset: -0.4 },
  { start: 2, end: 4, text: "Second sentence." },
];

function markup(over: Partial<ComponentProps<typeof TranscriptCard>> = {}): string {
  const props: ComponentProps<typeof TranscriptCard> = {
    projectId: "p1", segments: SEGMENTS, setSegments: () => {}, job: undefined, jobActive: false,
    transcribing: false, transcribeError: null, transcribePending: false, onTranscribe: () => {},
    otherJobNotice: null, provider: "edge_tts", voiceId: "en-GB-RyanNeural", speed: 1, voiceOptions: [],
    studioDefaultVoice: "", ...over,
  };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(
    createElement(MemoryRouter, null,
      createElement(QueryClientProvider, { client: qc }, createElement(TranscriptCard, props))),
  );
}

/** The `<button …>` whose content is `label`, as its opening tag. */
function buttonTag(html: string, label: string): string {
  const at = html.indexOf(`${label}</button>`);
  expect(at, `no "${label}" button`).toBeGreaterThan(-1);
  const open = html.lastIndexOf("<button", at);
  return html.slice(open, html.indexOf(">", open) + 1);
}

describe("Transcribe again", () => {
  it("is offered once there is a transcript, and asks first", () => {
    const tag = buttonTag(markup(), "Transcribe again");
    expect(tag).not.toContain("disabled");
    expect(tag).toContain("Asks first");
  });

  it("waits for any job holding the project, as the first transcription does", () => {
    const notice = "A job is running for this project (revoice) — it must finish first.";
    const tag = buttonTag(markup({ jobActive: true, otherJobNotice: notice }), "Transcribe again");
    expect(tag).toContain("disabled");
    expect(tag).toContain("A job is running for this project (revoice)");
    expect(buttonTag(markup({ transcribePending: true }), "Transcribe again")).toContain("disabled");
  });

  it("is not there without a transcript: the first transcription's own button is", () => {
    const html = markup({ segments: [] });
    expect(html).not.toContain("Transcribe again");
    expect(buttonTag(html, "Transcribe audio")).not.toContain("disabled");
  });
});

describe("the line for a lost job", () => {
  it("shows on the card, dismissable, over the transcript and over the empty card alike", () => {
    for (const segments of [SEGMENTS, []]) {
      const html = markup({ segments, jobNotice: LOST_JOB_MESSAGE, onDismissJobNotice: () => {} });
      expect(html).toContain(LOST_JOB_MESSAGE);
      expect(html).toContain('aria-label="Dismiss"');
    }
  });

  it("is not there when there is none", () => {
    expect(markup()).not.toContain(LOST_JOB_MESSAGE);
  });
});
