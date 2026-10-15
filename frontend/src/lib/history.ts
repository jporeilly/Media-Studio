/**
 * Undo / redo over a whole document: a present, the pasts behind it, the futures ahead of it. ONE history
 * for the annotation editor (`lib/annotate.ts`, where it was first written) and the walkthrough builder
 * (`lib/walkthrough.ts`); unlimited within the session. Unit-tested in history.test.ts.
 *
 * THE CONTENT-EQUALITY RULE. A commit of a value EQUAL IN CONTENT to the present adds no step: a click that
 * moved nothing, a drag too small to mean anything, a style pick that applied to nothing. What "equal in
 * content" means is each document's own business (the annotation document compares as the server would
 * store it, so a difference under a hundredth of a pixel is no step), so a history is made WITH its rule -
 * `history(doc, sameDocument)` - and `commit` asks it. With no rule given, two values are the same only
 * when they are one value.
 */

export type Same<T> = (a: T, b: T) => boolean;

export interface History<T> {
  past: T[];
  present: T;
  future: T[];
  /** The content-equality rule this history was made with. */
  same: Same<T>;
}

export function history<T>(present: T, same: Same<T> = Object.is): History<T> {
  return { past: [], present, future: [], same };
}

/** A new present; the old one joins the past and the future is forgotten - unless `next` is the same in content. */
export function commit<T>(h: History<T>, next: T): History<T> {
  if (next === h.present || h.same(next, h.present)) return h;
  return { ...h, past: [...h.past, h.present], present: next, future: [] };
}

export function undo<T>(h: History<T>): History<T> {
  if (h.past.length === 0) return h;
  return { ...h, past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function redo<T>(h: History<T>): History<T> {
  if (h.future.length === 0) return h;
  return { ...h, past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

export const canUndo = <T,>(h: History<T>): boolean => h.past.length > 0;
export const canRedo = <T,>(h: History<T>): boolean => h.future.length > 0;

/**
 * The past and the future with only the values `keep` accepts; the present stays. The walkthrough builder prunes
 * after a save: a document naming a picture the save swept away could never be saved again, so undo and redo
 * no longer reach it. The same history when nothing is dropped.
 */
export function prune<T>(h: History<T>, keep: (value: T) => boolean): History<T> {
  const past = h.past.filter(keep), future = h.future.filter(keep);
  if (past.length === h.past.length && future.length === h.future.length) return h;
  return { ...h, past, future };
}
