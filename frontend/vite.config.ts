import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The API runs as a separate long-running container. Proxying in dev keeps
    // the frontend origin-relative, so the same build works against localhost
    // and against the deployed backend without a rebuild.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    // A static bundle on a CDN — no SSR, nothing to run at the edge.
    outDir: "dist",
    sourcemap: true,
  },
});
