// Builds the public demo into <out>/ (for the site: ../../site/public/demo): index.html, app/ (SDK source + both engines +
// onnxruntime-web), ort/ (onnxruntime-web wasm), models/pii-<size>/ (the four default model files, their tokenizer and each model
// repo's LICENSE and NOTICE). The page loads models from that folder, next to the page, and falls back to Hugging Face (the
// SDK default) when it fails; a site copy sits on the same CDN as the page, which is much faster outside Korea (a visit from
// China took 96 s to fetch medium from Hugging Face) and keeps demo loads out of the Hugging Face download count.
// The files are downloaded from Hugging Face at the SDK's MODEL_VERSION and checked against the SHA-256 in src/models.ts;
// PII_MODELS_DIR (a folder of local pii-<size>/ copies) is read first when set. --no-models leaves them out (the page then
// reads Hugging Face). The same folder works as a GitHub Pages site or a static Hugging Face Space. License notices ship with it: app/*.LEGAL.txt (esbuild), ort/LICENSE.txt
// (onnxruntime-web, MIT; the npm package has no LICENSE file, so the text is kept in onnxruntime-web.LICENSE.txt), and
// LICENSE.txt and NOTICE.txt (this repo's Apache-2.0 license and NOTICE plus the onnxruntime-web license), linked from the footer.
// index.html preloads the medium model (the size the page opens with) unless it is already in the SDK's Cache Storage, so a
// first visit's download starts before the script has loaded.
// node playground/build.mjs [out] [--no-models]
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const js = resolve(here, "..");
const args = process.argv.slice(2);
const withModels = !args.includes("--no-models");
const out = resolve(args.find((a) => !a.startsWith("--")) ?? join(here, "dist"));

// the SDK's model version, default variants and cache name, read from the TypeScript source
const tables = await build({
  stdin: { contents: 'export { MODEL_VERSION, MODEL_FILES } from "./src/models.ts"; export { DEFAULT_VARIANT } from "./src/index.ts"; export { CACHE_NAME } from "./src/loader.ts";', resolveDir: js, loader: "ts" },
  bundle: true,
  format: "esm",
  write: false,
  platform: "neutral",
});
const { MODEL_VERSION, MODEL_FILES, DEFAULT_VARIANT, CACHE_NAME } = await import(`data:text/javascript;base64,${Buffer.from(tables.outputFiles[0].text).toString("base64")}`);

rmSync(out, { recursive: true, force: true });
await build({
  entryPoints: [join(here, "main.ts")],
  bundle: true,
  format: "esm",
  target: "es2022",
  splitting: true,
  outdir: join(out, "app"),
  chunkNames: "[name]-[hash]",
  minify: true,
  legalComments: "linked",
  define: {
    __VERSION__: JSON.stringify(JSON.parse(readFileSync(join(js, "package.json"), "utf8")).version),
    __SITE_MODELS__: JSON.stringify(withModels),
  },
  absWorkingDir: js,
});
mkdirSync(join(out, "ort"), { recursive: true });
for (const f of ["ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"])
  cpSync(join(js, "node_modules/onnxruntime-web/dist", f), join(out, "ort", f));
const ortLicense = join(here, "onnxruntime-web.LICENSE.txt");
cpSync(ortLicense, join(out, "ort", "LICENSE.txt"));
cpSync(join(js, "..", "LICENSE"), join(out, "LICENSE.txt"));
writeFileSync(
  join(out, "NOTICE.txt"),
  `${readFileSync(join(js, "..", "NOTICE"), "utf8")}\n${"-".repeat(80)}\n${readFileSync(ortLicense, "utf8")}`,
);
// model files next to the page: models/pii-<size>/{onnx/model_<variant>.onnx, tokenizer.web.json, LICENSE, NOTICE}
const hfBase = (size) => `https://huggingface.co/newmean-ai/pii-${size}/resolve/v${MODEL_VERSION}/`; // the SDK's default modelUrl
async function modelFile(size, path, info) {
  const local = process.env.PII_MODELS_DIR && join(process.env.PII_MODELS_DIR, `pii-${size}`, path);
  let bytes = local && existsSync(local) ? readFileSync(local) : null;
  if (!bytes) {
    const res = await fetch(hfBase(size) + path);
    if (!res.ok) throw new Error(`download failed: ${hfBase(size)}${path}: HTTP ${res.status}`);
    bytes = Buffer.from(await res.arrayBuffer());
  }
  if (info && (bytes.length !== info.bytes || createHash("sha256").update(bytes).digest("hex") !== info.sha256))
    throw new Error(`pii-${size}/${path} does not match src/models.ts (size or SHA-256)`);
  const to = join(out, "models", `pii-${size}`, path);
  mkdirSync(dirname(to), { recursive: true });
  writeFileSync(to, bytes);
}
if (withModels)
  for (const size of ["nano", "small", "medium", "large"]) {
    const variant = DEFAULT_VARIANT[size];
    await modelFile(size, `onnx/model_${variant}.onnx`, MODEL_FILES[size][variant]);
    await modelFile(size, "tokenizer.web.json", MODEL_FILES[size].tokenizer);
    for (const f of ["LICENSE", "NOTICE"]) await modelFile(size, f);
  }

let html = readFileSync(join(here, "index.html"), "utf8");
// The page opens with the medium model: start its download while the script is still loading. A preload link is the same
// request as the SDK's fetch, so the browser hands the response over. Added from a script, and only when the files are not in
// the SDK's Cache Storage yet: Hugging Face sends them with no-store, so a preload on a later visit would download them again.
// With the site copy the URLs are relative to the document base (/demo/ on pii.newmean.ai), as main.ts builds them.
const medium = withModels ? "models/pii-medium/" : hfBase("medium");
const preload = [`${medium}onnx/model_${DEFAULT_VARIANT.medium}.onnx`, `${medium}tokenizer.web.json`];
const script = `<script>(async () => {
  const urls = ${JSON.stringify(preload)}.map((u) => new URL(u, document.baseURI).href);
  try { const c = await caches.open(${JSON.stringify(CACHE_NAME)}); if (await c.match(urls[0])) return; } catch {}
  for (const href of urls) {
    const l = document.createElement("link");
    Object.assign(l, { rel: "preload", as: "fetch", href, crossOrigin: "anonymous" });
    document.head.append(l);
  }
})();</script>`;
html = html.replace('<meta charset="utf-8">', `<meta charset="utf-8">\n${script}`);
writeFileSync(join(out, "index.html"), html);
cpSync(join(here, "og-image.png"), join(out, "og-image.png")); // link-preview image (source: og-image.html)
console.log("built", out);
