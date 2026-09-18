/** Formatting helpers shared by pages. Pure functions, unit-tested in format.test.ts. */

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  let text = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return new Date(text + "T00:00:00");
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(text)) text = text.replace(" ", "T");
  if (!/[zZ]|[+-]\d{2}:\d{2}$/.test(text)) text += "Z";
  const d = new Date(text);
  return isNaN(d.getTime()) ? null : d;
}

export function fmtDate(value: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "2-digit", month: "short", year: "numeric" }): string {
  const d = parseDate(value);
  return d ? d.toLocaleDateString("en-GB", opts) : "";
}

export function relativeTime(value: string | null | undefined): string {
  const d = parseDate(value);
  if (!d) return "";
  const seconds = Math.round((Date.now() - d.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86400);
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

export function titleCase(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Format a duration in seconds as m:ss (useful for media length). */
export function duration(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * A position in a recording as m:ss.mmm — deliberately separate from
 * `duration()`, which rounds to whole seconds and is right for "how long is
 * this video". A transcript sentence is nudged in tenths and hundredths of a
 * second, so a label rounded to the second would say nothing about the change
 * that was just made.
 *
 * Rounded to whole milliseconds FIRST and split from there, as the Python
 * twin (services/narration.py::timecode) is. Rounding the seconds remainder
 * on its own (toFixed(3) of `total - m * 60`) printed a position a fraction
 * under a minute as "0:60.000": 59.9996 s is "1:00.000" and 119.9997 s is
 * "2:00.000", the carry riding into the minute. Negative is clamped at zero,
 * as a pin is.
 */
export function timecode(seconds: number | null | undefined): string {
  const ms = Math.round(Math.max(0, Number(seconds || 0)) * 1000);
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const millis = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
