import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/** Kept separate from `vite.config.ts` so the test run does not pull in the
 *  Tailwind plugin, which has nothing to contribute to assertions and adds a
 *  second or two to every run. */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "happy-dom",
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
