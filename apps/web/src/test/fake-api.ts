// A stand-in for the NU Connect Hub API: routes keyed by "METHOD /path" (without /api/v1).
import { vi } from "vitest";

export interface RecordedCall {
  key: string;
  body: unknown;
  headers: Headers;
}

export type Reply = [status: number, body?: unknown];
type Route = Reply | ((call: RecordedCall) => Reply | Promise<Reply>);

export const ADA = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "ada@northeastern.edu",
  role: "student",
  verified: true,
  created_at: "2026-11-05T00:00:00Z",
};

export function apiError(status: number, code: string, message = "Request failed."): Reply {
  return [status, { error: { code, message, request_id: "req-test" } }];
}

function toResponse([status, body]: Reply): Response {
  const hasBody = body !== undefined && status !== 204;
  return new Response(hasBody ? JSON.stringify(body) : null, {
    status,
    headers: hasBody ? { "Content-Type": "application/json" } : {},
  });
}

export function fakeApi(routes: Record<string, Route>) {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const call: RecordedCall = {
      key: `${method} ${url.pathname.replace(/^\/api\/v1/, "")}`,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      headers: new Headers(init?.headers),
    };
    calls.push(call);
    const route = routes[call.key];
    if (!route) return toResponse(apiError(404, "not_found", `No fake route for ${call.key}`));
    return toResponse(typeof route === "function" ? await route(call) : route);
  });
  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    callsTo: (key: string) => calls.filter((call) => call.key === key),
    setRoute: (key: string, route: Route) => {
      routes[key] = route;
    },
  };
}
