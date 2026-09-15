/**
 * The per-sentence narration editor's pure helpers (services/narration.py is
 * the other half). Unit-tested in narration.test.ts; the page itself is
 * verified in the browser by the owner.
 */

import { qs } from "../api/client";

/** The narration a re-voice would run with: what the Re-voice card has selected. */
export interface JobNarration {
  provider: string;
  voice: string;
  speed: number;
}

/**
 * Where to fetch one sentence's audio from.
 *
 * The three values sent are the JOB's — what the Re-voice card is about to use.
 * The sentence's own voice and speed are NOT sent and do not need to be: the
 * server applies them on top, exactly as `_revoice_video` applies them on top of
 * the job's, so this URL means "this line as that re-voice will speak it" for a
 * sentence with overrides and for one without. Re-deriving the precedence in the
 * client would be a second copy of a rule that has to agree with the render.
 *
 * Fetched (with the session cookie) and played from a blob rather than handed to
 * an `<audio src>`: a media element reports only that its source failed, which
 * would throw away every message the route writes — the 409 naming Kokoro's
 * missing model, the 400 naming a voice from the other provider, the 502 naming
 * the provider — and pressing Play is the path a user actually takes to find out
 * that a hand-typed voice id is wrong.
 */
export function previewUrl(projectId: string, index: number, job: JobNarration): string {
  return `/api/projects/${projectId}/transcript/${index}/preview${qs({ ...job })}`;
}

/** The two per-sentence number boxes. They read an empty box differently. */
export type NumberField = "offset" | "speed";

/**
 * What to save from a typed number box, or `null` when there is nothing to send.
 *
 * Committed on blur / Enter rather than per keystroke: each change is one
 * read-modify-write of the project record, and a request per character would
 * interleave with itself.
 *
 * The two fields differ on an empty box, and the difference is load-bearing:
 *
 * - **offset** — blank and 0 both mean "not nudged", so typing 0 clears the
 *   override rather than storing a zero nobody can tell from the default.
 * - **speed** — blank means "whatever speed the re-voice runs at", but an
 *   explicit 1.0 is NOT the same thing: a stored speed bypasses both the
 *   per-sentence fitting rule and the post-synthesis squeeze, so the sentence is
 *   spoken at exactly that rate and no faster. Only a blank box clears it.
 *
 * An unparseable box sends nothing: what is saved is left alone rather than
 * guessed at zero.
 */
export function numberChange(
  field: NumberField, typed: string, current: number | null | undefined,
): { value: number | null } | null {
  let next: number | null;
  if (typed.trim() === "") {
    next = null;
  } else {
    const parsed = Number(typed);
    if (!Number.isFinite(parsed)) return null;
    // Three decimals, as services/narration.py rounds them.
    next = Math.round(parsed * 1000) / 1000;
    if (field === "offset" && next === 0) next = null;
  }
  return next === (current ?? null) ? null : { value: next };
}

/**
 * What to save from a typed voice box, or `null` when nothing changed. An empty
 * box clears the override back to the job's voice; the stored provider is
 * cleared with it by the server, because a provider on its own says nothing.
 */
export function voiceChange(
  typed: string, current: string | null | undefined,
): { voice: string | null } | null {
  const next = typed.trim() || null;
  return next === (current ?? null) ? null : { voice: next };
}
