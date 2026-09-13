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
  if (!resp.ok) {
    const detail = (data && typeof data === "object" && "detail" in (data as Record<string, unknown>))
      ? (data as Record<string, unknown>).detail
      : data || resp.statusText;
    if (resp.status === 401 && !path.includes("/auth/")) {
      window.dispatchEvent(new CustomEvent("mediastudio:unauthorized"));
    }
    throw new ApiError(resp.status, detail);
  }
  return data as T;
}

export const api = {
  get: <T = any>(path: string) => request<T>("GET", path),
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
