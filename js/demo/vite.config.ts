import { defineConfig } from "vite";

// Test page: `npm run demo`. Model folders are served from demo/models/pii-<size>/ (a local copy of the published files).
export default defineConfig({
  server: { port: 5178, fs: { strict: false } },
  optimizeDeps: { exclude: ["onnxruntime-web"] },
});
