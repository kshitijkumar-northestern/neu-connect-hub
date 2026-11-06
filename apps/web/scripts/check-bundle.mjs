// Fails the production build if any backend configuration name reaches the browser bundle.
// The names come from apps/api/.env.example, so new backend variables are covered automatically.
// Usage: node scripts/check-bundle.mjs [distDir] [envExample]
import { existsSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

/** Variable names declared in an env template (KEY=value lines). */
export function readEnvNames(file) {
  return readFileSync(file, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.split("=", 1)[0].trim())
    .filter(Boolean);
}

/** Every (file, name) pair where a forbidden name appears in the bundle directory. */
export function findLeaks(distDir, names) {
  const leaks = [];
  for (const entry of readdirSync(distDir, { recursive: true, withFileTypes: true })) {
    if (!entry.isFile()) continue;
    const path = join(entry.parentPath, entry.name);
    const content = readFileSync(path, "utf8");
    for (const name of names) {
      if (content.includes(name)) leaks.push({ file: relative(distDir, path), name });
    }
  }
  return leaks.sort((a, b) => a.file.localeCompare(b.file) || a.name.localeCompare(b.name));
}

function main([distArg, envArg]) {
  const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
  const distDir = distArg ?? join(webRoot, "dist");
  const envExample = envArg ?? join(webRoot, "..", "api", ".env.example");

  const names = readEnvNames(envExample);
  if (names.length === 0) throw new Error(`No backend variable names found in ${envExample}`);
  if (!existsSync(distDir)) {
    console.error(`Bundle check: ${distDir} does not exist; run the production build first.`);
    process.exit(1);
  }

  const leaks = findLeaks(distDir, names);
  if (leaks.length > 0) {
    for (const { file, name } of leaks) console.error(`Backend variable ${name} found in dist/${file}`);
    process.exit(1);
  }
  console.log(`Bundle check passed: none of ${names.length} backend variable names appear in dist/.`);
}

// Compare real paths so the check still runs when the repo is reached through a symlink.
const invokedAs = process.argv[1] && existsSync(process.argv[1]) ? realpathSync(process.argv[1]) : null;
if (invokedAs === realpathSync(fileURLToPath(import.meta.url))) main(process.argv.slice(2));
