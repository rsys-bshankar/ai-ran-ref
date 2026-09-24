/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the SPA calls /api/* on its own origin; Vite forwards it to the BFF
// (GUI_BFF_URL, default a locally-run BFF on :8090). Production does the same
// with nginx (nginx.conf). Either way the browser never talks to R1 directly.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.GUI_BFF_URL ?? "http://localhost:8090", changeOrigin: false } },
  },
  preview: {
    port: 4173,
    proxy: { "/api": { target: process.env.GUI_BFF_URL ?? "http://localhost:8090", changeOrigin: false } },
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 800 },
  test: { include: ["src/**/*.test.ts"] },
});
