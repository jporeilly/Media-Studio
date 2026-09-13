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
