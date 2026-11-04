import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Must match the "@/*" alias in tsconfig.json, webpack.config.cjs and components.json.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  define: {
    __API_BASE_URL__: JSON.stringify("/api/v1"),
    __SOCKET_URL__: JSON.stringify(""),
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.mjs"],
    restoreMocks: true,
  },
});
