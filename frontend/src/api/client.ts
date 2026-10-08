/** Thin fetch wrapper for the Media Studio REST API (session cookie auth). */

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

/**
 * The ApiError for a failed response, built from its body: the server's own
 * `detail` when it wrote one, else the body, else the status text. Shared by
 * every call shape so a failure reads the same wherever it came from — and so a
 * binary GET can surface the server's message, which an `<audio>` element's own
 * error event never can (it is told only that its source failed).
 */
function failure(resp: Response, text: string, path: string): ApiError {
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body */
  }
  const detail = (data && typeof data === "object" && "detail" in (data as Record<string, unknown>))
    ? (data as Record<string, unknown>).detail
    : data || resp.statusText;
  if (resp.status === 401 && !path.includes("/auth/")) {
    window.dispatchEvent(new CustomEvent("mediastudio:unauthorized"));
  }
  return new ApiError(resp.status, detail);
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    if (isForm) {
      payload = body as FormData;
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  const resp = await fetch(path, { method, headers, body: payload, credentials: "include" });
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body */
  }
  if (!resp.ok) throw failure(resp, text, path);
  return data as T;
}

/**
 * GET a binary body — audio the page plays, for instance. Fetched rather than
 * handed straight to a media element as a `src` so that a failure comes back as
 * the SAME ApiError as every other call and the server's message reaches the
 * user; a media element only ever reports that its source failed.
 */
async function requestBlob(path: string): Promise<Blob> {
  const resp = await fetch(path, { method: "GET", credentials: "include" });
  if (!resp.ok) throw failure(resp, await resp.text(), path);
  return resp.blob();
}

/**
 * PUT a raw body - a recorder's WebM chunk - with its own content type, read back as JSON. Kept apart from
 * `request` so the JSON and form paths never grow a third shape by accident.
 */
async function requestRaw<T>(method: string, path: string, body: Blob, contentType: string): Promise<T> {
  const resp = await fetch(path, { method, headers: { "Content-Type": contentType }, body, credentials: "include" });
  const text = await resp.text();
  if (!resp.ok) throw failure(resp, text, path);
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  get: <T = any>(path: string) => request<T>("GET", path),
  blob: (path: string) => requestBlob(path),
  putRaw: <T = any>(path: string, body: Blob, contentType: string) => requestRaw<T>("PUT", path, body, contentType),
  post: <T = any>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T = any>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  put: <T = any>(path: string, body?: unknown) => request<T>("PUT", path, body),
  delete: <T = any>(path: string) => request<T>("DELETE", path),
  upload: <T = any>(path: string, form: FormData) => request<T>("POST", path, form, true),
};

/** Build a query string from an object, skipping empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (Array.isArray(err.detail)) {
      return err.detail.map((d: any) => d?.msg || JSON.stringify(d)).join("; ");
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
