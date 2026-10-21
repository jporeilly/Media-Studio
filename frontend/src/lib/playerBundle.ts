/**
 * The interactive player bundle (W2c, C7): the PURE half of the self-contained player that Export to course
 * writes beside a workshop's guide, and that Download player zips - `index.html` (the player, its script and its
 * styles inline: `src/player/main.tsx` built by `vite build --mode player` into one HTML file), the walkthrough in
 * its data slot, the step pictures beside it. It opens from the disk (`file://`): no network, no app, no API.
 *
 * The server fills the slot (`services/course_export.py player_html`): `<script type="application/json"
 * id="walkthrough-data">` holding the walkthrough as `player_document` writes it - the name and, per step, what the
 * player shows; each step's `image` is its picture's file name beside the page (null for a step without one).
 * Unit-tested in playerBundle.test.ts; the built page is opened offline by tests/test_course_export.py.
 */
import type { Hotspot, Placement, Step, StepKind } from "./walkthrough";

/** The id of the data slot the server fills. */
export const DATA_ELEMENT_ID = "walkthrough-data";
/** What the page says when the slot holds no walkthrough (the template as built, or a damaged file). */
export const UNREADABLE_MESSAGE = "This walkthrough could not be read: export it again from Media Studio.";

export interface BundleWalkthrough { name: string; steps: Step[] }

const KINDS: StepKind[] = ["tooltip", "modal"];
const PLACEMENTS: Placement[] = ["auto", "top", "bottom", "left", "right"];
/** A picture's file name beside the page: `step-<12 hex>.png`, nothing that could leave the folder. */
const IMAGE_NAME = /^step-[0-9a-f]{12}\.png$/;

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

function hotspotOf(v: unknown): Hotspot | null {
  if (!v || typeof v !== "object") return null;
  const h = v as Record<string, unknown>;
  const out = { x: num(h.x), y: num(h.y), width: num(h.width), height: num(h.height) };
  return out.width > 0 && out.height > 0 ? out : null;
}

/** One step as the player takes it: every field typed, a picture only by a file name beside the page. */
function stepOf(v: unknown, index: number): Step {
  const s = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  const image = typeof s.image === "string" && IMAGE_NAME.test(s.image) ? s.image : null;
  return {
    id: str(s.id) || `step-${index + 1}`,
    image,
    width: image ? num(s.width) : 0,
    height: image ? num(s.height) : 0,
    hotspot: image ? hotspotOf(s.hotspot) : null,
    kind: KINDS.includes(s.kind as StepKind) ? (s.kind as StepKind) : "tooltip",
    text: str(s.text),
    instruction: str(s.instruction),
    placement: PLACEMENTS.includes(s.placement as Placement) ? (s.placement as Placement) : "auto",
    chapter: str(s.chapter),
    note: "",
    source: null,
  };
}

/** The walkthrough in the data slot's text, or null when there is none (`null`, not JSON, not a walkthrough). */
export function readBundle(text: string | null | undefined): BundleWalkthrough | null {
  if (!text) return null;
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || !Array.isArray((data as { steps?: unknown }).steps)) return null;
  const d = data as { name?: unknown; steps: unknown[] };
  return { name: str(d.name) || "Walkthrough", steps: d.steps.map(stepOf) };
}

/** A step's picture: its file beside the page (relative, so it opens from the disk). */
export function bundleImageUrl(step: Pick<Step, "image">): string {
  return step.image ?? "";
}

/** The theme the page takes: the viewer's own (`prefers-color-scheme`), dark when it cannot be read. */
export function bundleTheme(matchMedia: ((query: string) => { matches: boolean }) | undefined): "dark" | "light" {
  try {
    return matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}
