/**
 * The timeline's MARKUP, from the real component.
 *
 * Every other test in this repo is of a pure helper, and that gap has a cost
 * with a name: the E3 regression where the selection band painted "this cut
 * applies here" over a LOCKED lane was fixed in E4b, and putting it straight
 * back left all 258 unit tests green (the Reviewer's mutant F13). Nothing a
 * helper can be asked about knows which lanes the band draws over, whether a
 * banner is on the page at all, or whether a button is disabled — only the
 * markup does.
 *
 * So this renders the REAL `NarrationTimeline` through
 * `renderToStaticMarkup` and reads the answers off the HTML. It needs no new
 * dependency (`react-dom/server` ships with react-dom, which the app already
 * has) and no DOM: `environment` stays "node" and vitest's include was widened
 * to `.tsx` for it (vite.config.ts). The component's data comes from a
 * `QueryClient` seeded with exactly the shapes the server sends, so the render
 * is the component's own code path and not a fixture of the markup.
 *
 * What it CANNOT reach — an effect, a pointer gesture, a keystroke — is not
 * faked here: those stay pinned by the `?raw` assertions in lib/music.test.ts,
 * and the live check is still the owner's.
 */
import { beforeAll, describe, expect, it } from "vitest";
import { type ComponentProps, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NarrationTimeline } from "./NarrationTimeline";
import { narrationPlanKey } from "../../lib/timeline";
import { STUDIO_QUERY_KEY } from "../../lib/studioSettings";

const PID = "p1";

/**
 * `readLocks` reads `localStorage` while the component renders, and node has
 * none. A Map is enough: the render only ever gets the value a test put there.
 */
const stored = new Map<string, string>();
beforeAll(() => {
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => { stored.set(key, value); },
    removeItem: (key: string) => { stored.delete(key); },
  };
});

const SENTENCES = [
  { index: 0, start: 0, end: 2, pinned_start: 0, text: "First sentence.", voice: "en-GB", speed: 1,
    muted: false, past_end: false, speakable: true, window: 5, squeezable: true, preview_url: "/p0" },
  { index: 1, start: 5, end: 7, pinned_start: 5, text: "Second sentence.", voice: "en-GB", speed: 1,
    muted: false, past_end: false, speakable: true, window: 5, squeezable: true, preview_url: "/p1" },
];
/** A clip whose file the library still has. */
const LIVE = { id: "m1", file: "bed.mp3", at: 1, in: 0, out: 6, gain: 0.15, fade_in: 1, fade_out: 2,
  file_duration: 9, missing: false };
/** One whose file has gone: the banner's subject. */
const GONE = { id: "m2", file: "gone.mp3", at: 8, in: 0, out: 3, gain: 0.4, fade_in: 0, fade_out: 0,
  file_duration: null, missing: true };
const GONE2 = { ...GONE, id: "m3", at: 11 };

type Locks = { video: boolean; narration: boolean; music: boolean };

/** The component's markup, with the plan, the peaks and the studio settings already in the cache. */
function markup(over: { music?: unknown[]; markers?: unknown[]; locks?: Locks; snapping?: boolean } = {}): string {
  if (over.locks) stored.set(`ms:tl-locks:${PID}`, JSON.stringify(over.locks));
  else stored.delete(`ms:tl-locks:${PID}`);
  if (over.snapping !== undefined) stored.set(`ms:tl-snap:${PID}`, JSON.stringify(over.snapping));
  else stored.delete(`ms:tl-snap:${PID}`);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // The plan's key carries the Re-voice card's provider, voice and speed.
  qc.setQueryData([...narrationPlanKey(PID), "edge", "en-GB", 1], {
    sentences: SENTENCES, duration: 12, baseline_rate: 14, squeeze_tolerance: 0.15, squeeze_max_factor: 1.2,
    edit: {
      version: 2,
      video: { keep: [[0, 6], [7.5, 12]], output_duration: 10.5 },
      narration: { keep: null, output_duration: 12 },
      music: over.music ?? [LIVE],
      // Left out unless a test gives some: a backend before E5b sends no key.
      ...(over.markers ? { markers: over.markers } : {}),
      source_duration: 12, output_duration: 10.5,
    },
  });
  qc.setQueryData(["waveform", PID], {
    buckets: 12, bucket_seconds: 1, duration: 12, sample_rate: 1000,
    peaks: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120],
  });
  qc.setQueryData(STUDIO_QUERY_KEY, { settings: { music_volume: 0.15 }, options: {} });
  const props: ComponentProps<typeof NarrationTimeline> = {
    projectId: PID, provider: "edge", voiceId: "en-GB", speed: 1, active: true,
    selected: null, onSelect: () => {}, jobActive: false, offsets: {}, onOffsetsSaved: () => {},
  };
  // A MemoryRouter around it since the strip's summary carries the link to the
  // Timeline document: a `<Link>` needs a router context, and this is the app's
  // own way of linking (the only `target="_blank"` in the app is for a link
  // that really does leave it).
  return renderToStaticMarkup(
    createElement(MemoryRouter, null,
      createElement(QueryClientProvider, { client: qc }, createElement(NarrationTimeline, props))),
  );
}

/** The selection band's rows, top to bottom: the classes each carries beyond `os-tl-selection-row`. */
function bandRows(html: string): string[] {
  const from = html.indexOf('class="os-tl-selection"');
  expect(from, "the selection band is not in the markup").toBeGreaterThan(-1);
  // The band holds nothing but its rows, so the first closing tag ends it.
  const slice = html.slice(from, html.indexOf("</div>", from));
  return [...slice.matchAll(/class="os-tl-selection-row([^"]*)"/g)].map((m) => m[1].trim());
}

/** Whether the lane drawn by `os-tl-<name>` says it is locked. */
function laneLocked(html: string, name: string): boolean {
  const at = html.indexOf(`class="os-tl-${name}`);
  expect(at, `the ${name} lane is not in the markup`).toBeGreaterThan(-1);
  const classes = html.slice(at + 7, html.indexOf('"', at + 7));
  return classes.split(" ").includes("locked");
}

/** The `<button …>` whose opening tag carries `needle`, up to its content. */
function button(html: string, needle: string): string {
  const at = html.indexOf(needle);
  expect(at, `${needle} is not in the markup`).toBeGreaterThan(-1);
  return html.slice(html.lastIndexOf("<button", at), html.indexOf(">", at) + 1);
}

const COMBOS: [boolean, boolean, boolean][] = [];
for (const video of [false, true]) {
  for (const narration of [false, true]) {
    for (const music of [false, true]) COMBOS.push([video, narration, music]);
  }
}

describe("the selection band paints the unlocked lanes and only those", () => {
  it.each(COMBOS)("video=%s narration=%s music=%s", (video, narration, music) => {
    const html = markup({ locks: { video, narration, music } });
    const painted = bandRows(html).map((cls) => !cls.includes("locked"));
    // The ruler always; Video and its original audio follow the VIDEO lock;
    // then Narration; then Music. This is the E3 regression's own shape: with
    // Narration locked and Music unlocked the fourth row must be dark and the
    // fifth lit, which one rectangle with a `top` and a `bottom` could not do.
    expect(painted).toEqual([true, !video, !video, !narration, !music]);
  });

  it.each(COMBOS)("agrees with the lanes themselves: video=%s narration=%s music=%s", (video, narration, music) => {
    // Stronger than the expectation above, and the reason the band exists: a
    // row must be dark exactly when the lane UNDER it says it is locked. The
    // two are written in different places, so this is what ties them together.
    const html = markup({ locks: { video, narration, music } });
    const rows = bandRows(html).map((cls) => cls.includes("locked"));
    expect(rows).toEqual([
      false,
      laneLocked(html, "film"),
      laneLocked(html, "wave"),
      laneLocked(html, "blocks"),
      laneLocked(html, "music"),
    ]);
  });

  it("has one row per lane and no lane arithmetic left anywhere", () => {
    const html = markup({ locks: { video: true, narration: true, music: false } });
    expect(bandRows(html)).toHaveLength(5);
    expect(html).not.toMatch(/os-tl-selection[^"]*no-(video|narration|music)/);
  });

  it("draws the four lanes under the ruler in the band's own order", () => {
    // The lanes are in normal flow, so document order IS the order they stack
    // in — which is what makes a row line up with the lane it belongs to. A
    // lane moved without moving its row would put the band back over the wrong
    // one, silently.
    const html = markup();
    const at = (name: string) => html.indexOf(`class="os-tl-${name}`);
    const order = ["ruler", "film", "wave", "blocks", "music"].map(at);
    expect(order.every((x) => x > -1)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });
});

describe("the stuck-clip banner", () => {
  it("is not on the page at all while every file is there", () => {
    const html = markup({ music: [LIVE] });
    expect(html).not.toContain("no longer in the library");
    expect(html).not.toContain("stuck clip");
    // ... and neither is the button that removes them, so nothing can be
    // pressed once the user has put the file back and the plan has refetched.
    expect(html).not.toContain("Remove the stuck clip");
  });

  it("is not on the page when the lane is empty either", () => {
    expect(markup({ music: [] })).not.toContain("no longer in the library");
  });

  it("names one stuck clip in the singular, and says what it costs and what it can still do", () => {
    // E4c: the render refuses, the lane does not freeze. The clip can be
    // moved, levelled and faded, not trimmed; the way out is to remove it or
    // put the file back under the same name.
    const html = markup({ music: [LIVE, GONE] });
    expect(html).toContain("A music clip names a file");
    expect(html).toContain("no longer in the library");
    expect(html).toContain("(gone.mp3)");
    expect(html).toContain("so the render will refuse until it is removed or the file is put back in the library under the same name");
    expect(html).toContain("It can still be moved, levelled and faded here, but not trimmed, and a cut or split across it is"
      + " refused until the lane is locked or the clip removed.");
    expect(html).toContain("Remove the stuck clip");
    // Nothing of the frozen lane survives.
    expect(html).not.toMatch(/nothing on the Music lane|cannot be changed until|frozen|unfreeze/);
  });

  it("counts them when there are several, and names each file once", () => {
    const html = markup({ music: [LIVE, GONE, GONE2] });
    expect(html).toContain("2 music clips name files");
    expect(html).toContain("until they are removed or the files are put back in the library under the same name");
    expect(html).toContain("They can still be moved, levelled and faded here, but not trimmed, and a cut or split across one is"
      + " refused until the lane is locked or the clip removed.");
    expect(html).toContain("Remove the 2 stuck clips");
    expect(html).toContain("(gone.mp3)");
    expect(html).not.toContain("(gone.mp3, gone.mp3)");
    // The live clip is not counted among them.
    expect(html).not.toContain("3 music clips name files");
  });

  it("draws trim zones on a live clip and none on a missing one", () => {
    // The edge overlays carry the ew-resize cursor, and a missing clip has a
    // body and no edges (E4c: its slice cannot change while the file is
    // gone). What the markup holds is the proof; `clipAt` answering "body"
    // is the other half, in lib/music.test.ts.
    const html = markup({ music: [LIVE, GONE] });
    const clipMarkup = (file: string) => {
      const at = html.indexOf(`aria-label="Music clip ${file} at `);
      expect(at, `the ${file} clip is not in the markup`).toBeGreaterThan(-1);
      return html.slice(html.lastIndexOf("<button", at), html.indexOf("</button>", at));
    };
    expect(clipMarkup("bed.mp3")).toContain('class="os-tl-clip-edge in"');
    expect(clipMarkup("bed.mp3")).toContain('class="os-tl-clip-edge out"');
    expect(clipMarkup("gone.mp3")).not.toContain("os-tl-clip-edge");
    expect(clipMarkup("gone.mp3")).toContain("missing");
    // The missing clip's own tooltip says what it can and cannot do.
    expect(clipMarkup("gone.mp3")).toContain("it cannot be trimmed while the file is gone");
    expect(clipMarkup("gone.mp3")).not.toContain("nothing on the lane can be changed");
  });

  it("disables its button while the Music lane is locked, and says why", () => {
    const html = markup({ music: [LIVE, GONE], locks: { video: false, narration: false, music: true } });
    const remove = button(html, "Remove the stuck clip");
    expect(remove).toContain("disabled");
    expect(remove).toContain("The Music lane is locked");
  });

  it("leaves it enabled while the lane is unlocked", () => {
    expect(button(markup({ music: [LIVE, GONE] }), "Remove the stuck clip")).not.toContain("disabled");
  });
});

describe("the scissors says why it is disabled", () => {
  it.each(COMBOS)("video=%s narration=%s music=%s", (video, narration, music) => {
    const html = markup({ locks: { video, narration, music } });
    const cut = button(html, 'aria-label="Cut"');
    if (video && narration && music) expect(cut).toContain("Every lane is locked");
    // Music the ONLY unlocked lane: the music rides the picture, so a cut here
    // would appear to work and move nothing (the owner's ruling, 2026-09-21).
    else if (video && narration) expect(cut).toContain("The music rides the picture");
    else {
      expect(cut).toContain("Remove the selection from the unlocked lanes");
      // The picture locked but Music unlocked is the one ordinary combination
      // where an unlocked lane does NOT move: say so, or the tooltip states the
      // owner's ruling backwards at the control that performs it.
      if (video && !music) expect(cut).toContain("with Video locked the clips stay exactly where they are");
      else expect(cut).not.toContain("with Video locked");
    }
  });
});

describe("the Music header says what the owner ruled, not what the tracks do", () => {
  it("never promises that unlocking the lane makes a cut apply to the clips", () => {
    // "Unlock music: cuts and splits apply to it again" was a track's sentence
    // on a lane that does not behave like one: with Video locked and Music
    // unlocked — reachable, and one click away — a cut leaves every clip where
    // it is. The lock's own tooltip has to be true in all eight combinations.
    const unlocked = button(markup(), 'aria-label="Lock music');
    expect(unlocked).toContain("cuts and splits leave the clips exactly where they are");
    const locked = button(markup({ locks: { video: false, narration: false, music: true } }), 'aria-label="Unlock music');
    expect(locked).toContain("only one that cuts the picture too");
    expect(locked).not.toContain("cuts and splits apply to it again");
  });

  it("does not promise a cut at the control that disables the scissors", () => {
    // Clicking the Music channel's name locks Video and Narration, which is
    // the single combination `canCut` refuses — so this button must promise
    // the split and the clip's own gestures, and nothing more.
    const name = button(markup(), 'title="Select the music channel');
    expect(name).toContain("the scissors is then disabled");
    expect(name).toContain("S still splits a clip at the playhead");
    expect(name).not.toContain("a cut or a split edits just this one");
    // The two tracks still say it, because for them it is true.
    expect(button(markup(), 'title="Select the video channel')).toContain("a cut or a split edits just this one");
  });
});

describe("the header's buttons and the sliders wear their own classes", () => {
  it("gives the library button and the eye a class that does not mean `lock`", () => {
    const html = markup();
    // A4's lesson, applied to the header: all three small buttons share the
    // SHAPE (`os-tl-tool`), only the lock carries `os-tl-lock` — so nothing
    // has to undo the locked header's amber on two buttons that are not locks.
    expect(button(html, 'aria-label="Open the music library"')).toContain('class="os-tl-tool"');
    expect(button(html, 'aria-label="Hear the voice alone"')).toContain('class="os-tl-tool"');
    expect(button(html, 'aria-label="Lock music')).toContain('class="os-tl-tool os-tl-lock"');
    expect(button(html, 'aria-label="Lock video')).toContain('class="os-tl-tool os-tl-lock"');
  });

  it("styles the zoom slider through os-tl-range, as A4 left it", () => {
    const html = markup();
    expect(html).toContain('class="os-tl-range os-tl-zoom"');
    expect(html).not.toContain('class="os-tl-zoom"');
  });
});

describe("the strip's summary is a list of the basic gestures, and the rest is a document", () => {
  /** The `<dl>` of gestures, from its opening tag to its close. */
  function actions(html: string): string {
    const at = html.indexOf('class="os-tl-actions"');
    expect(at, "the action list is not in the markup").toBeGreaterThan(-1);
    const from = html.lastIndexOf("<dl", at);
    return html.slice(from, html.indexOf("</dl>", from) + 5);
  }

  it("pairs every gesture with what it does, in a real definition list", () => {
    const list = actions(markup());
    const terms = [...list.matchAll(/<dt>(.*?)<\/dt>/g)].map((m) => m[1]);
    const meanings = [...list.matchAll(/<dd>(.*?)<\/dd>/g)].map((m) => m[1]);
    // A table faked with spaces was the other way to do this, and it would not
    // survive the app's small size or a narrow window.
    expect(terms.length).toBe(meanings.length);
    expect(terms.length).toBeGreaterThanOrEqual(9);
    // The basics the owner listed: a range, the cut, (E5a) the trim, the
    // split, (E5b) the marker, the channel and the lock - one row since E5a,
    // so the trim's row kept the list at ten and the marker's makes eleven -
    // a block, the library, a clip, the eye, undo.
    expect(terms).toEqual([
      "Drag the green or red handle, or Ctrl+drag",
      "Scissors, or Delete",
      "Drag a piece&#x27;s edge",
      "S",
      "M",
      "A lane&#x27;s name, or its lock",
      "Drag a sentence block",
      "The + on Music",
      "Drag a clip, or either end",
      "The eye on Music",
      "Ctrl+Z",
    ]);
    expect(terms).toHaveLength(11);
    // The handle row says it snaps and how to stop it for one drag; the trim
    // row says what a piece's edge does; the marker row names the jump keys.
    expect(meanings[0]).toBe("Select a range; snaps to pins and joins, Alt: no snap");
    expect(meanings[2]).toBe("Trim the cut; drag it back out to restore");
    expect(meanings[4]).toBe("Drop a marker at the playhead; Ctrl+[ / ] jump between them");
  });

  it("points at the Timeline document for everything else", () => {
    const html = markup();
    expect(html).toContain('href="/docs/guides/timeline"');
    expect(html).toContain("Full help: the Timeline");
  });

  it("no longer carries the two paragraphs of prose that were there", () => {
    // The exact sentences that moved into docs/guides/timeline.md. If one comes
    // back here, the strip is dense again and the document is a second copy.
    const html = markup();
    for (const gone of [
      "Play here to hear the new narration",
      "is the fourth lane",
      "the row under the strip sets its level and its fades",
      "it snaps to the playhead, the joins, the other sentences",
      "Each track is decoded once when you press Play",
      "Ctrl+Shift+S all of them",
    ]) {
      expect(html, `"${gone}" is still on the strip`).not.toContain(gone);
    }
  });

  it("keeps the summary shorter than the prose it replaced", () => {
    // A stand-in for the rendered height, which no SSR render can measure (the
    // real one was measured in a browser against the same stylesheet: 136 px
    // where the two paragraphs were 201 px, in a card 1110 px wide). Those
    // paragraphs were 415 words of unbroken text; this is a list, so its height
    // is its ROWS - and a row added here is a row the ruler moves down by. A
    // sentence added to the context has to be paid for by one taken out.
    const html = markup();
    const help = html.slice(html.indexOf('class="os-tl-help'), html.indexOf('class="os-tl-panel"'));
    const words = help.replace(/<[^>]*>/g, " ").split(/\s+/).filter(Boolean).length;
    const context = help.slice(0, help.indexOf("<dl")).replace(/<[^>]*>/g, " ").split(/\s+/).filter(Boolean).length;
    expect(words, "the summary is creeping back towards the 415 words of prose").toBeLessThan(170);
    expect(context, "two short lines of context, then the list").toBeLessThan(45);
    expect([...actions(html).matchAll(/<dt>/g)].length).toBeLessThanOrEqual(12);
  });
});

/**
 * One pieces layer's markup: from its opening tag to whatever the body draws
 * next (another pieces layer, the film, a join, a block's ghost or a block).
 */
function piecesLayer(html: string, name: string): string {
  const at = html.indexOf(`class="os-tl-pieces ${name}`);
  expect(at, `the ${name} pieces layer is not in the markup`).toBeGreaterThan(-1);
  const ends = ['class="os-tl-pieces ', 'class="os-tl-film', 'class="os-tl-join', 'class="os-tl-ghost', 'class="os-tl-block ']
    .map((marker) => html.indexOf(marker, at + 1))
    .filter((index) => index > at);
  return html.slice(at, Math.min(...ends));
}
const count = (html: string, needle: string) => html.split(needle).length - 1;

describe("the pieces' trim edges (E5a)", () => {
  // The fixture's picture is [[0, 6], [7.5, 12]]: two pieces; its narration
  // is whole: one piece.
  it("draws an edge zone at each end of every piece of an unlocked lane", () => {
    const html = markup();
    const video = piecesLayer(html, "video");
    expect(count(video, 'class="os-tl-piece-edge in"')).toBe(2);
    expect(count(video, 'class="os-tl-piece-edge out"')).toBe(2);
    const narration = piecesLayer(html, "narration");
    expect(count(narration, 'class="os-tl-piece-edge in"')).toBe(1);
    expect(count(narration, 'class="os-tl-piece-edge out"')).toBe(1);
    // ... and says so on the piece.
    expect(video).toContain("drag an edge to trim");
    expect(narration).toContain("drag an edge to trim");
  });

  it("draws none on a locked lane, whose pieces have no edges (trap 19)", () => {
    const videoLocked = markup({ locks: { video: true, narration: false, music: false } });
    expect(piecesLayer(videoLocked, "video")).not.toContain("os-tl-piece-edge");
    expect(piecesLayer(videoLocked, "video")).not.toContain("drag an edge to trim");
    expect(piecesLayer(videoLocked, "narration")).toContain("os-tl-piece-edge");
    const narrationLocked = markup({ locks: { video: false, narration: true, music: false } });
    expect(piecesLayer(narrationLocked, "narration")).not.toContain("os-tl-piece-edge");
    expect(piecesLayer(narrationLocked, "narration")).not.toContain("drag an edge to trim");
    expect(piecesLayer(narrationLocked, "video")).toContain("os-tl-piece-edge");
  });

  it("draws none on the Audio lane, which follows Video and has no edges of its own", () => {
    const audio = piecesLayer(markup(), "audio");
    expect(audio).toContain('class="os-tl-piece"');
    expect(audio).not.toContain("os-tl-piece-edge");
    expect(audio).not.toContain("drag an edge to trim");
  });

  it("carries a trim overlay in every pieces layer and a label on the two that can be trimmed, hidden until a drag", () => {
    const html = markup();
    expect(count(html, 'class="os-tl-trim"')).toBe(3);
    expect(count(html, 'class="os-tl-trim-label"')).toBe(2);
    expect(piecesLayer(html, "audio")).toContain('class="os-tl-trim"');
    expect(piecesLayer(html, "audio")).not.toContain("os-tl-trim-label");
  });
});

describe("the magnet (E5a)", () => {
  it("is on the toolbar beside Fit, pressed by default", () => {
    const html = markup();
    const magnet = button(html, 'aria-label="Snapping"');
    expect(magnet).toContain('aria-pressed="true"');
    expect(magnet).toContain("Hold Alt while dragging to turn it off for that drag");
    expect(html.indexOf('aria-label="Fit"')).toBeLessThan(html.indexOf('aria-label="Snapping"'));
  });

  it("comes back off only when it was switched off, and says how to turn it on", () => {
    const off = button(markup({ snapping: false }), 'aria-label="Snapping"');
    expect(off).toContain('aria-pressed="false"');
    expect(off).toContain("Snapping is off");
    // Anything but a stored `false` is on.
    expect(button(markup({ snapping: true }), 'aria-label="Snapping"')).toContain('aria-pressed="true"');
  });
});

describe("the clips themselves", () => {
  it("hatches a missing clip and says so on its face", () => {
    const html = markup({ music: [LIVE, GONE] });
    expect(html).toContain("os-tl-clip missing");
    expect(html).toContain("gone.mp3 — missing");
  });

  it("draws a trim overlay at each end of every clip", () => {
    const html = markup({ music: [LIVE] });
    expect(html).toContain("os-tl-clip-edge in");
    expect(html).toContain("os-tl-clip-edge out");
  });
});

/** The ruler's markup: from its opening tag to the first pieces layer under it. */
function ruler(html: string): string {
  const at = html.indexOf('class="os-tl-ruler"');
  expect(at, "the ruler is not in the markup").toBeGreaterThan(-1);
  return html.slice(at, html.indexOf('class="os-tl-pieces', at));
}
/** Every flag's opening tag, in order. */
const flags = (html: string): string[] => [...ruler(html).matchAll(/<button[^>]*class="os-tl-marker[^"]*"[^>]*>/g)].map((m) => m[0]);

describe("the markers on the ruler (E5b)", () => {
  // The fixture's cut is 6.0–7.5 of a 12 s source (see `markup`): a marker at
  // 6.5 sits in removed picture and the server projects it to `null`.
  const INTRO = { id: "k1", at: 1, name: "Intro", timeline_at: 1 };
  const HIDDEN = { id: "k2", at: 6.5, name: "In the hole", timeline_at: null };
  const LATER = { id: "k3", at: 9, name: "Wrap = up", timeline_at: 7.5 };

  it("draws a flag per DRAWN marker in the ruler, the name beside it and a title of name — time", () => {
    const html = markup({ markers: [INTRO, HIDDEN, LATER] });
    expect(ruler(html)).toContain('class="os-tl-markers"');
    const drawn = flags(html);
    expect(drawn).toHaveLength(2);
    expect(drawn[0]).toContain('title="Intro — 0:01.000');
    expect(drawn[0]).toContain('aria-label="Marker Intro at 0:01.000"');
    expect(drawn[1]).toContain('title="Wrap = up — 0:07.500');
    expect(ruler(html)).toContain('<span class="os-tl-marker-name">Intro</span>');
    expect(ruler(html)).toContain('<span class="os-tl-marker-name">Wrap = up</span>');
    // Both are flags (the class) and neither is selected until clicked.
    for (const flag of drawn) {
      expect(flag).toContain('type="button"');
      expect(flag).toContain('aria-pressed="false"');
      expect(flag).not.toContain("selected");
      expect(flag).not.toContain("pending");
    }
    // The button is the glyph alone: its only content is the name label
    // (which the stylesheet makes take no pointer - pinned in
    // lib/music.test.ts), so a click or a drag under the name reaches the ruler.
    const body = ruler(html);
    const first = body.slice(body.indexOf(drawn[0]), body.indexOf("</button>", body.indexOf(drawn[0])) + "</button>".length);
    expect(first).toMatch(/^<button[^>]*>\s*<span class="os-tl-marker-name">Intro<\/span>\s*<\/button>$/);
  });

  it("draws none for a marker in removed picture (a null timeline_at), and none at all without markers", () => {
    const html = markup({ markers: [INTRO, HIDDEN, LATER] });
    expect(html).not.toContain("In the hole");
    expect(flags(markup({ markers: [] }))).toHaveLength(0);
    expect(flags(markup({ markers: [HIDDEN] }))).toHaveLength(0);
    // A backend before E5b sends no key: still a ruler, still no flags, no name box.
    const older = markup();
    expect(flags(older)).toHaveLength(0);
    expect(ruler(older)).toContain('class="os-tl-markers"');
  });

  it("has no name box until a marker is being named, and the ruler's own title names M", () => {
    const html = markup({ markers: [INTRO] });
    expect(html).not.toContain("os-tl-marker-name-box");
    expect(ruler(html)).toContain('title="Click to seek, drag to scrub, Ctrl+drag to select a range; M drops a marker at the playhead"');
    // The drag's label is there, hidden, for the move to paint through its ref.
    expect(ruler(html)).toContain('class="os-tl-marker-label"');
  });

  it("says on the flag what it does, and that markers become chapters", () => {
    const [flag] = flags(markup({ markers: [INTRO] }));
    expect(flag).toContain("Click to select; double-click to rename; drag to move it (Alt: no snapping); Delete removes it.");
    expect(flag).toContain("Ctrl+[ and Ctrl+] jump between markers. Markers become the rendered video&#x27;s chapters.");
  });
});
