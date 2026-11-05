import { config } from "@/lib/config";

/** Cookie the API sets with the CSRF token; its value is echoed back in CSRF_HEADER. */
export const CSRF_COOKIE = "nu_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** One invalid field from a 422 response, e.g. { location: "body.email", message: "..." }. */
export interface FieldError {
  location: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly fields: readonly FieldError[];

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null,
    options?: ErrorOptions & { fields?: readonly FieldError[] },
  ) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.fields = options?.fields ?? [];
  }
}

export type ApiRequestInit = Omit<RequestInit, "body" | "credentials"> & {
  /** Serialised as JSON with a matching Content-Type. */
  json?: unknown;
  body?: BodyInit | null;
  /** Ignored: the API is cookie-authenticated, so credentials are always included. */
  credentials?: RequestCredentials;
};

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string; fields?: unknown };
}

function isFieldError(value: unknown): value is FieldError {
  if (typeof value !== "object" || value === null) return false;
  const { location, message } = value as Record<string, unknown>;
  return typeof location === "string" && typeof message === "string";
}

export function apiUrl(path: string): string {
  if (!path.startsWith("/")) throw new Error(`API paths must start with "/": ${path}`);
  return config.apiBaseUrl.replace(/\/+$/, "") + path;
}

export function readCookie(name: string, cookieString: string = document.cookie): string | null {
  for (const part of cookieString.split(";")) {
    const separator = part.indexOf("=");
    if (separator === -1) continue;
    if (part.slice(0, separator).trim() !== name) continue;
    const raw = part.slice(separator + 1).trim();
    try {
      return decodeURIComponent(raw);
    } catch {
      // Malformed percent-encoding: use the value as sent rather than failing every request.
      return raw;
    }
  }
  return null;
}

function isAbortError(cause: unknown): boolean {
  // Checked by name: DOMException is not always an `instanceof Error` (e.g. across realms).
  return typeof cause === "object" && cause !== null && (cause as { name?: unknown }).name === "AbortError";
}

function transportError(cause: unknown): ApiError {
  if (isAbortError(cause)) {
    return new ApiError(0, "aborted", "The request was cancelled", null, { cause });
  }
  return new ApiError(0, "network_error", "The network request failed", null, { cause });
}

async function readBody(response: Response): Promise<{ ok: true; value: unknown } | { ok: false }> {
  const text = await response.text();
  if (text === "") return { ok: true, value: undefined };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

export async function apiFetch<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  // `credentials` stays in `rest` but is overridden below.
  const { json, body, ...rest } = init;
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");

  let requestBody: BodyInit | null = body ?? null;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    requestBody = JSON.stringify(json);
  }

  if (UNSAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers.set(CSRF_HEADER, token);
  }

  const url = apiUrl(path);
  let response: Response;
  let parsed: Awaited<ReturnType<typeof readBody>> | undefined;
  try {
    response = await fetch(url, { ...rest, method, headers, body: requestBody, credentials: "include" });
    // Reading the body can fail too (connection dropped or request aborted after the headers).
    if (response.status !== 204) parsed = await readBody(response);
  } catch (cause) {
    throw transportError(cause);
  }

  if (parsed === undefined) return undefined as T;
  const headerRequestId = response.headers.get("X-Request-ID");

  if (!response.ok) {
    const envelope = parsed.ok ? (parsed.value as ErrorEnvelope | undefined)?.error : undefined;
    throw new ApiError(
      response.status,
      envelope?.code ?? `http_${response.status}`,
      envelope?.message ?? (response.statusText || "The request failed"),
      envelope?.request_id ?? headerRequestId,
      { fields: Array.isArray(envelope?.fields) ? envelope.fields.filter(isFieldError) : [] },
    );
  }

  if (!parsed.ok) {
    throw new ApiError(response.status, "invalid_response", "The server sent an unreadable response", headerRequestId);
  }
  return parsed.value as T;
}
