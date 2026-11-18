import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { findLeaks, readEnvNames } from "./check-bundle.mjs";

const { connectSources, injectContentSecurityPolicy } = createRequire(import.meta.url)("../webpack.config.cjs");
const PAGE = "<html><head><meta charset=utf-8><script id=theme-init>void 0</script></head></html>";

const dirs = [];
function tempDir() {
  const dir = mkdtempSync(join(tmpdir(), "bundle-check-"));
  dirs.push(dir);
  return dir;
}

afterEach(() => {
  while (dirs.length) rmSync(dirs.pop(), { recursive: true, force: true });
});

describe("readEnvNames", () => {
  it("reads variable names from an env template, ignoring comments and blanks", () => {
    const dir = tempDir();
    const file = join(dir, ".env.example");
    writeFileSync(file, "# comment\n\nDATABASE_URL=postgres://x\nSESSION_SECRET=replace_me\n  # indented comment\n");

    expect(readEnvNames(file)).toEqual(["DATABASE_URL", "SESSION_SECRET"]);
  });
});

describe("findLeaks", () => {
  it("reports every file that contains a forbidden name, including nested files", () => {
    const dist = tempDir();
    mkdirSync(join(dist, "assets"));
    writeFileSync(join(dist, "index.html"), "<html></html>");
    writeFileSync(join(dist, "assets", "main.js"), 'const x = "SESSION_SECRET";');
    writeFileSync(join(dist, "assets", "main.js.map"), '{"sources":["DATABASE_URL"]}');

    const leaks = findLeaks(dist, ["SESSION_SECRET", "DATABASE_URL", "EMAIL_PROVIDER_API_KEY"]);

    expect(leaks).toEqual([
      { file: join("assets", "main.js"), name: "SESSION_SECRET" },
      { file: join("assets", "main.js.map"), name: "DATABASE_URL" },
    ]);
  });

  it("returns no leaks for a clean bundle", () => {
    const dist = tempDir();
    writeFileSync(join(dist, "main.js"), "console.log('/api/v1');");

    expect(findLeaks(dist, ["SESSION_SECRET"])).toEqual([]);
  });
});

describe("check-bundle CLI", () => {
  const scriptsDir = dirname(fileURLToPath(import.meta.url));

  function fixture(bundleSource, indexHtml = injectContentSecurityPolicy(PAGE, [])) {
    const root = tempDir();
    const dist = join(root, "dist");
    mkdirSync(dist);
    writeFileSync(join(dist, "main.js"), bundleSource);
    writeFileSync(join(dist, "index.html"), indexHtml);
    const envExample = join(root, ".env.example");
    writeFileSync(envExample, "SESSION_SECRET=replace_me\n");
    return { root, dist, envExample };
  }

  // The check reads the same API_BASE_URL and SOCKET_URL as the build; same origin unless set.
  function run(scriptPath, args, configured = {}) {
    const env = { ...process.env, API_BASE_URL: "/api/v1", SOCKET_URL: "", ...configured };
    return spawnSync(process.execPath, [scriptPath, ...args], { encoding: "utf8", env });
  }

  it("fails with a non-zero exit when a backend name is in the bundle", () => {
    const { dist, envExample } = fixture('const s = "SESSION_SECRET";');

    const result = run(join(scriptsDir, "check-bundle.mjs"), [dist, envExample]);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("SESSION_SECRET found in dist/main.js");
  });

  it("still runs the check when invoked through a symlinked path", () => {
    const { root, dist, envExample } = fixture('const s = "SESSION_SECRET";');
    const linkedScripts = join(root, "linked-scripts");
    symlinkSync(scriptsDir, linkedScripts, "dir");

    const result = run(join(linkedScripts, "check-bundle.mjs"), [dist, envExample]);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("SESSION_SECRET found in dist/main.js");
  });

  it("passes a clean bundle and fails clearly when dist is missing", () => {
    const { root, dist, envExample } = fixture("console.log('/api/v1');");

    const clean = run(join(scriptsDir, "check-bundle.mjs"), [dist, envExample]);
    expect(clean.status).toBe(0);
    expect(clean.stdout).toContain("Bundle check passed");

    const missing = run(join(scriptsDir, "check-bundle.mjs"), [join(root, "no-dist"), envExample]);
    expect(missing.status).toBe(1);
    expect(missing.stderr).toContain("run the production build first");
  });

  it("fails when the built page has no Content Security Policy", () => {
    const { dist, envExample } = fixture("console.log('/api/v1');", PAGE);

    const result = run(join(scriptsDir, "check-bundle.mjs"), [dist, envExample]);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("no Content-Security-Policy meta tag");
  });

  it("fails when the policy's hash no longer matches the theme script", () => {
    const stale = injectContentSecurityPolicy(PAGE, []).replace("void 0", "void 1");
    const { dist, envExample } = fixture("console.log('/api/v1');", stale);

    const result = run(join(scriptsDir, "check-bundle.mjs"), [dist, envExample]);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("inline script #theme-init is not allowed");
  });

  it("checks connect-src against the API and Socket.IO URLs the build was configured with", () => {
    const configured = { API_BASE_URL: "https://api.hub.example/api/v1", SOCKET_URL: "wss://api.hub.example" };
    const page = injectContentSecurityPolicy(PAGE, connectSources(configured));
    const { dist, envExample } = fixture("console.log('/api/v1');", page);
    const script = join(scriptsDir, "check-bundle.mjs");

    expect(run(script, [dist, envExample], configured).status).toBe(0);

    const moved = run(script, [dist, envExample], { ...configured, API_BASE_URL: "https://api.new.example/api/v1" });
    expect(moved.status).toBe(1);
    expect(moved.stderr).toContain("connect-src lacks https://api.new.example");
  });

  it("fails when the built page is missing", () => {
    const { dist, envExample } = fixture("console.log('/api/v1');");
    rmSync(join(dist, "index.html"));

    const result = run(join(scriptsDir, "check-bundle.mjs"), [dist, envExample]);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("index.html");
  });
});
