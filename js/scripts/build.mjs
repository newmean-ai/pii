// ESM bundle of src/ (onnxruntime-web stays an external dependency); declarations come from tsc. Each engine is its own chunk,
// loaded on first use, so an app pays only for the engine it picks.
import { build } from "esbuild";

await build({
  entryPoints: ["src/index.ts"],
  bundle: true,
  format: "esm",
  target: "es2022",
  platform: "neutral",
  external: ["onnxruntime-web", "onnxruntime-web/*"],
  outdir: "dist",
  splitting: true,
  chunkNames: "[name]-[hash]",
  sourcemap: true,
  legalComments: "none",
});
