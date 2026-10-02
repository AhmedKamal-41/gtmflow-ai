import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Minimal dev-only test setup (Phase 3 closeout Part A.1): Vitest +
// React Testing Library, jsdom environment. Added specifically because a
// disconnected Chrome extension in a prior session did not establish that
// NO interaction-verification tool was available -- this one runs entirely
// in this environment with no browser/extension dependency. Kept small and
// devDependency-only; does not touch the production bundle or `next build`.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
