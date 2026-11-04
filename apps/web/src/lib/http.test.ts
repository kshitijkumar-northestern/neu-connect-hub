import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, CSRF_COOKIE, CSRF_HEADER, apiFetch, apiUrl, readCookie } from "@/lib/http";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function lastCall() {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  const [url, init] = call;
  return { url, init: init ?? {}, headers: new Headers(init?.headers) };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = `${CSRF_COOKIE}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
});

describe("apiUrl", () => {
  it("prefixes the configured API base URL", () => {
    expect(apiUrl("/me")).toBe("/api/v1/me");
  });

  it("rejects paths without a leading slash", () => {
    expect(() => apiUrl("me")).toThrow(/must start with "\/"/);
  });
});

describe("readCookie", () => {
  it("returns the decoded value of the named cookie", () => {
    expect(readCookie("b", "a=1; b=hello%20world; c=3")).toBe("hello world");
  });

  it("returns null when the cookie is absent", () => {
    expect(readCookie("missing", "a=1; b=2")).toBeNull();
  });

  it("does not match a cookie whose name only ends with the requested name", () => {
    expect(readCookie("token", "xtoken=nope; other=1")).toBeNull();
  });

  it("returns the raw value instead of throwing when the value is not valid percent-encoding", () => {
    expect(readCookie("nu_csrf", "nu_csrf=%E0%A4%A; other=1")).toBe("%E0%A4%A");
  });
});

describe("apiFetch", () => {
  it("sends GET requests with credentials, JSON accept header and no CSRF header", async () => {
    document.cookie = `${CSRF_COOKIE}=csrf-123; path=/`;
    fetchMock.mockResolvedValue(jsonResponse({ id: 7 }));

    const result = await apiFetch<{ id: number }>("/me");

    expect(result).toEqual({ id: 7 });
    const { url, init, headers } = lastCall();
    expect(url).toBe("/api/v1/me");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has(CSRF_HEADER)).toBe(false);
  });

  it("serialises JSON bodies and attaches the CSRF token on unsafe methods", async () => {
    document.cookie = `${CSRF_COOKIE}=csrf-123; path=/`;
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }, { status: 201 }));

    await apiFetch("/connections", { method: "post", json: { recipient_id: "u2" } });

    const { init, headers } = lastCall();
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"recipient_id":"u2"}');
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get(CSRF_HEADER)).toBe("csrf-123");
  });

  it.each(["PUT", "PATCH", "DELETE"])("attaches the CSRF token on %s", async (method) => {
    document.cookie = `${CSRF_COOKIE}=csrf-456; path=/`;
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await apiFetch("/me/profile", { method });

    expect(lastCall().headers.get(CSRF_HEADER)).toBe("csrf-456");
  });

  it("omits the CSRF header when no token cookie exists", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));

    await apiFetch("/auth/login", { method: "POST", json: {} });

    expect(lastCall().headers.has(CSRF_HEADER)).toBe(false);
  });

  it("always sends credentials, even if the caller asks to omit them", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));

    await apiFetch("/me", { credentials: "omit" });

    expect(lastCall().init.credentials).toBe("include");
  });

  it("returns undefined for 204 No Content", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(apiFetch("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("turns the API error envelope into an ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "connection_exists", message: "A request already exists", request_id: "req-9" } },
        { status: 409 },
      ),
    );

    const error = await apiFetch("/connections", { method: "POST", json: {} }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "connection_exists",
      message: "A request already exists",
      requestId: "req-9",
    });
  });

  it("falls back to an http_<status> code for non-JSON error bodies", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" }),
    );

    const error = await apiFetch("/health").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 502, code: "http_502", message: "Bad Gateway", requestId: null });
  });

  it("uses the X-Request-ID header when the error body has no request id", async () => {
    fetchMock.mockResolvedValue(
      new Response("oops", { status: 500, headers: { "X-Request-ID": "req-header" } }),
    );

    const error = await apiFetch("/health").catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 500, code: "http_500", requestId: "req-header" });
  });

  it("reports a successful response with an unreadable body as invalid_response", async () => {
    fetchMock.mockResolvedValue(new Response("not json", { status: 200 }));

    const error = await apiFetch("/me").catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 200, code: "invalid_response" });
  });

  it("reports network failures with status 0", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const error = await apiFetch("/me").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "network_error" });
  });

  it("reports a body that fails mid-read as a network error, not a raw exception", async () => {
    const brokenBody = new ReadableStream({
      start(controller) {
        controller.error(new TypeError("connection reset"));
      },
    });
    fetchMock.mockResolvedValue(new Response(brokenBody, { status: 200 }));

    const error = await apiFetch("/me").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "network_error" });
  });

  it("reports caller aborts as aborted so callers can ignore them", async () => {
    fetchMock.mockRejectedValue(new DOMException("The operation was aborted.", "AbortError"));

    const error = await apiFetch("/me", { signal: AbortSignal.abort() }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "aborted" });
  });

  it("still sends unsafe requests when the CSRF cookie is malformed", async () => {
    document.cookie = `${CSRF_COOKIE}=%E0%A4%A; path=/`;
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(apiFetch("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
    expect(lastCall().headers.get(CSRF_HEADER)).toBe("%E0%A4%A");
  });
});
