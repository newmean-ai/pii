// Spans of the npm package's detect() for the "text" field of JSON lines, for NpmParityTest (the Kotlin regex layer and
// the native core's combine step against the npm package end to end). Reads js/dist (cd js && npm run build).
// The output holds fixture text: write it outside git-tracked folders (e.g. android/pii/build/).
//
//   node scripts/npm_spans.mjs OUT.jsonl SIZE VARIANT SENSITIVITY REGEX(0|1) LABELS(all|level1) INPUT.jsonl...
//   PII_NPM_SPANS=OUT.jsonl ./gradlew :pii:testDebugUnitTest --tests '*NpmParityTest*'
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const js = resolve(here, "../../js");
const [out, size, variant, sens, regex, labels, ...inputs] = process.argv.slice(2);
const pkg = await import(pathToFileURL(`${js}/dist/index.js`).href);
const models = process.env.PII_MODELS ?? `${js}/test/models`;
const options = { size, variant, sensitivity: Number(sens), regex: regex === "1", labels: labels === "level1" ? pkg.LEVEL1_LABELS : pkg.LABELS };
const r = await pkg.createRedactor({
  ...options,
  engine: "builtin",
  relaxedSimd: false, // baseline build: bit-identical to the native core's default build
  modelUrl: pathToFileURL(`${models}/pii-${size}/`).href,
  fetch: async (url) => new Response(readFileSync(new URL(url))),
  cache: false,
});
const lines = [JSON.stringify({ ...options, labels })];
for (const f of inputs)
  for (const l of readFileSync(f, "utf8").split("\n").filter((x) => x.trim())) {
    const text = JSON.parse(l).text;
    const spans = await r.detect(text);
    lines.push(JSON.stringify({ text, spans: spans.map((s) => [s.start, s.end, s.label, s.source]) }));
  }
await r.dispose();
writeFileSync(out, lines.join("\n") + "\n");
console.log(`${lines.length - 1} texts -> ${out}`);
