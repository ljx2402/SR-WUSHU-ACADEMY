/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The app is served under /app/ (the Django site keeps /api/ and /admin/).
// In development, Vite proxies API calls to the Django dev server, so the
// browser only ever talks to one origin (no CORS, same as production).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", ["DEV_"]);
  const backend = env.DEV_API_PROXY_TARGET || "http://127.0.0.1:8000";
  return {
    base: "/app/",
    plugins: [react()],
    server: {
      port: 5173,
      proxy: { "/api": { target: backend, changeOrigin: false } },
    },
    preview: {
      port: 4173,
      proxy: { "/api": { target: backend, changeOrigin: false } },
    },
    build: { outDir: "dist", sourcemap: false },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
      restoreMocks: true,
    },
  };
});
