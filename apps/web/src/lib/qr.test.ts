import { describe, expect, it } from "vitest";

import { parseConnectLink } from "@/lib/qr";

const ORIGIN = window.location.origin;
const TOKEN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLM1234"; // 43 chars of [A-Za-z0-9_-]

describe("parseConnectLink", () => {
  it("accepts the exact shape: this origin, /connect, no query, a 43-char token", () => {
    expect(parseConnectLink(`${ORIGIN}/connect#token=${TOKEN}`)).toBe(TOKEN);
  });

  it("rejects a different origin", () => {
    expect(parseConnectLink(`https://evil.example/connect#token=${TOKEN}`)).toBeNull();
  });

  it("rejects a path other than /connect", () => {
    expect(parseConnectLink(`${ORIGIN}/share#token=${TOKEN}`)).toBeNull();
    expect(parseConnectLink(`${ORIGIN}/connect/extra#token=${TOKEN}`)).toBeNull();
  });

  it("rejects a query string", () => {
    expect(parseConnectLink(`${ORIGIN}/connect?x=1#token=${TOKEN}`)).toBeNull();
  });

  it("rejects javascript: URLs", () => {
    expect(parseConnectLink(`javascript:alert(1)`)).toBeNull();
  });

  it("rejects a token that's one character short", () => {
    expect(parseConnectLink(`${ORIGIN}/connect#token=${TOKEN.slice(1)}`)).toBeNull();
  });

  it("rejects a token that's one character too long", () => {
    expect(parseConnectLink(`${ORIGIN}/connect#token=${TOKEN}a`)).toBeNull();
  });

  it("rejects a bare token with no URL around it", () => {
    expect(parseConnectLink(TOKEN)).toBeNull();
  });

  it("rejects whitespace-padded junk in the hash", () => {
    expect(parseConnectLink(`${ORIGIN}/connect#token= ${TOKEN}`)).toBeNull();
  });

  it("rejects a whitespace-padded bare token", () => {
    expect(parseConnectLink(`  ${TOKEN}  `)).toBeNull();
  });

  it("rejects an empty or missing hash", () => {
    expect(parseConnectLink(`${ORIGIN}/connect`)).toBeNull();
    expect(parseConnectLink(`${ORIGIN}/connect#`)).toBeNull();
  });

  it("rejects text that isn't a URL at all", () => {
    expect(parseConnectLink("not a url")).toBeNull();
    expect(parseConnectLink("")).toBeNull();
  });
});
