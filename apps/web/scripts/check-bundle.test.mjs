import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { findLeaks, readEnvNames } from "./check-bundle.mjs";

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

  function fixture(bundleSource) {
    const root = tempDir();
    const dist = join(root, "dist");
    mkdirSync(dist);
    writeFileSync(join(dist, "main.js"), bundleSource);
    const envExample = join(root, ".env.example");
    writeFileSync(envExample, "SESSION_SECRET=replace_me\n");
    return { root, dist, envExample };
  }

  function run(scriptPath, args) {
    return spawnSync(process.execPath, [scriptPath, ...args], { encoding: "utf8" });
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
});
