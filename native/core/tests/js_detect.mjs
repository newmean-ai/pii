// Spans of the npm package's detect() (builtin WebAssembly engine, baseline SIMD128 build, regex off) for JSON lines
// with a "text" field on stdin; one {"spans": [[start, end, label], ...]} line per input, offsets in UTF-16 units.
// Used by run_tests.py to compare the native core with the WebAssembly engine; reads js/dist (build it with npm run build).
//
//   node js_detect.mjs PACKAGE_DIST MODEL_DIR SIZE VARIANT SENSITIVITY [RELAXED]
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [dist, modelDir, size, variant, sens, relaxed] = process.argv.slice(2);
const { createRedactor } = await import(pathToFileURL(`${dist}/index.js`).href);
const localFetch = async (url) => new Response(readFileSync(new URL(url)));
const r = await createRedactor({
  engine: "builtin",
  relaxedSimd: relaxed === "1",
  size,
  variant,
  modelUrl: pathToFileURL(`${modelDir}/`).href,
  fetch: localFetch,
  cache: false,
  regex: false,
  sensitivity: Number(sens),
});
const lines = readFileSync(0, "utf8").split("\n").filter((l) => l.trim());
const out = [];
for (const l of lines) {
  const spans = await r.detect(JSON.parse(l).text);
  out.push(JSON.stringify({ spans: spans.map((s) => [s.start, s.end, s.label]) }));
}
await r.dispose();
process.stdout.write(out.join("\n") + "\n");
