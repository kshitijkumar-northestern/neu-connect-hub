// Checks the Content Security Policy that the production build writes into dist/index.html: it
// must be there, come before every script, allow each inline script by the sha256 of its exact
// text (and carry no stale hash), never let arbitrary inline or evaluated script run, and let the
// page connect to exactly its own origin plus the configured API and Socket.IO origins.
// Parsed independently of the build hook in webpack.config.cjs, so a bug there can't hide here.
import { createHash } from "node:crypto";

const META = /<meta\b[^>]*http-equiv=["']?Content-Security-Policy["']?[^>]*>/gi;
const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;
// script-src values that would let injected markup run script after all.
const UNSAFE_SCRIPT_SOURCES = ["'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'", "*", "https:", "http:", "data:", "blob:"];

function attribute(attributes, name) {
  const match = attributes.match(new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, "i"));
  return match ? (match[1] ?? match[2] ?? match[3]) : null;
}

/** Every inline script (no src) in the page, with its id if it has one. */
export function inlineScripts(html) {
  return [...html.matchAll(SCRIPT)]
    .filter(([, attributes]) => attribute(attributes, "src") === null)
    .map(([, attributes, text]) => ({ id: attribute(attributes, "id"), text }));
}

/** The policy in the page's meta tag, or null. */
export function readPolicy(html) {
  const [meta] = html.match(META) ?? [];
  return meta ? attribute(meta, "content") : null;
}

/**
 * The origins connect-src must list for the configured API_BASE_URL and SOCKET_URL, beyond
 * 'self': for each absolute (or protocol-relative) URL, its host over HTTP and WebSocket, secure
 * unless the URL is plain http:// or ws://. Throws on a value that is neither a path nor a URL.
 */
export function configuredConnectSources(configured) {
  const sources = [];
  for (const [name, value] of Object.entries(configured)) {
    if (!value || /^\/(?!\/)/.test(value)) continue;
    const match = value.match(/^(?:(https?|wss?):)?\/\/([^/?#]+)/i);
    if (!match) throw new Error(`${name} is neither a path nor an absolute URL: ${JSON.stringify(value)}`);
    const scheme = (match[1] ?? "https").toLowerCase();
    const host = new URL(`https://${match[2]}`).host; // lowercased, default port dropped
    const secure = scheme === "https" || scheme === "wss";
    sources.push(`${secure ? "https" : "http"}://${host}`, `${secure ? "wss" : "ws"}://${host}`);
  }
  return [...new Set(sources)];
}

function sha256Source(text) {
  return `'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`;
}

function directive(policy, name) {
  return policy
    .split(";")
    .map((part) => part.trim().split(/\s+/))
    .find(([first]) => first === name);
}

/** What's wrong with connect-src, given the configured API_BASE_URL and SOCKET_URL. */
function connectProblems(policy, configured) {
  const connectSrc = directive(policy, "connect-src");
  if (!connectSrc) return ["the policy has no connect-src directive"];
  let expected;
  try {
    expected = new Set(["'self'", ...configuredConnectSources(configured)]);
  } catch (error) {
    return [`can't check connect-src: ${error.message}`];
  }
  const actual = new Set(connectSrc.slice(1));
  return [
    ...[...expected].filter((source) => !actual.has(source)).map((source) => `connect-src lacks ${source}, which the configured URLs need`),
    ...[...actual].filter((source) => !expected.has(source)).map((source) => `connect-src allows ${source}, which no configured URL needs`),
  ];
}

/**
 * Everything wrong with the page's policy; empty when it's sound. `configured` holds the
 * API_BASE_URL and SOCKET_URL the build used (unset means the page's own origin).
 */
export function cspProblems(html, configured = {}) {
  const metas = html.match(META) ?? [];
  if (metas.length === 0) return ["dist/index.html has no Content-Security-Policy meta tag"];
  if (metas.length > 1) return [`dist/index.html has ${metas.length} Content-Security-Policy meta tags`];

  const problems = [];
  const firstScript = html.search(/<script\b/i);
  if (firstScript !== -1 && html.indexOf(metas[0]) > firstScript) {
    problems.push("the Content-Security-Policy meta tag must come before every script");
  }

  const policy = readPolicy(html) ?? "";
  problems.push(...connectProblems(policy, configured));
  const scriptSrc = directive(policy, "script-src");
  if (!scriptSrc) return [...problems, "the policy has no script-src directive"];

  const sources = scriptSrc.slice(1);
  for (const loose of UNSAFE_SCRIPT_SOURCES) {
    if (sources.includes(loose)) problems.push(`script-src allows ${loose}`);
  }

  const scripts = inlineScripts(html);
  const allowed = new Set(sources.filter((source) => source.startsWith("'sha256-")));
  const present = new Set();
  scripts.forEach(({ id, text }, index) => {
    const hash = sha256Source(text);
    present.add(hash);
    if (!allowed.has(hash)) {
      problems.push(`inline script ${id ? `#${id}` : `number ${index + 1}`} is not allowed by script-src (needs ${hash})`);
    }
  });
  for (const hash of allowed) {
    if (!present.has(hash)) problems.push(`script-src hash ${hash} matches no inline script`);
  }
  return problems;
}
