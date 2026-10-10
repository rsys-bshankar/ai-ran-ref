/// <reference types="vitest/config" />
/**
 * The build, dev-server and test configuration of the console (Vite and Vitest). The dev server (:5173) and `vite preview` (:4173) forward /api to the BFF so the browser only ever talks to its own origin;
 * the production image does the same with nginx (nginx.conf). Tests are the `*.test.ts` and `*.test.tsx` files under src, run by `npx vitest run`. `GUI_BFF_URL` is read from the environment of the dev or preview process.
 */
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
  // No source maps in the build; 800 kB is the size at which Vite starts to warn about a chunk (the app is one bundle).
  build: { sourcemap: false, chunkSizeWarningLimit: 800 },
  test: { include: ["src/**/*.test.{ts,tsx}"] },
});
