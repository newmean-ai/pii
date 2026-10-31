// Builds the phone benchmark page into <out>/ (for the site: ../../site/public/mobile-bench): index.html is the page, app/ holds the
// bundle (SDK source + both engines + onnxruntime-web), ort/ the onnxruntime-web wasm, models/ the four default model files.
// License notices ship with it: app/*.LEGAL.txt (esbuild), ort/LICENSE.txt (onnxruntime-web, MIT), and each model's LICENSE and
// NOTICE from the models dir.
// node experiments/phone-bench/build.mjs [out] [models dir] [consistency json]
//   models dir (argv[3] or PII_MODELS_DIR): a folder with pii-<size>/tokenizer.web.json and pii-<size>/onnx/model_<variant>.onnx,
//     i.e. local copies of the four pii-<size> Hugging Face repos. Required.
//   consistency json (argv[4] or PII_CONSISTENCY): output of make_consistency.py, for ?ref=1. Optional; it holds test rows,
//     so keep it out of git.
import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const js = resolve(here, "../..");
const out = resolve(process.argv[2] ?? join(here, "site"));
const modelsArg = process.argv[3] ?? process.env.PII_MODELS_DIR;
if (!modelsArg || !existsSync(modelsArg)) {
  console.error(
    `models dir ${modelsArg ? `not found: ${modelsArg}` : "missing"}. Download the four pii-<size> Hugging Face repos into one ` +
      "folder (pii-nano/, pii-small/, pii-medium/, pii-large/) and pass it as the second argument or set PII_MODELS_DIR.",
  );
  process.exit(1);
}
const models = resolve(modelsArg);
// consistency set with reference spans (make_consistency.py, then ?ref=1); holds test rows, so it stays out of git
const checkArg = process.argv[4] ?? process.env.PII_CONSISTENCY;
const check = checkArg ? resolve(checkArg) : "";
if (checkArg && !existsSync(check)) {
  console.error(`consistency file not found: ${check} (make it with make_consistency.py, or leave PII_CONSISTENCY unset)`);
  process.exit(1);
}
const DEFAULT = { nano: "q4", small: "q4", medium: "q4e", large: "q4e" };

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
  absWorkingDir: js,
});
mkdirSync(join(out, "ort"), { recursive: true });
for (const f of ["ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"])
  cpSync(join(js, "node_modules/onnxruntime-web/dist", f), join(out, "ort", f));
// the onnxruntime-web npm package has no LICENSE file; the playground keeps its MIT text
cpSync(join(js, "playground", "onnxruntime-web.LICENSE.txt"), join(out, "ort", "LICENSE.txt"));
for (const [size, v] of Object.entries(DEFAULT)) {
  mkdirSync(join(out, "models", `pii-${size}`, "onnx"), { recursive: true });
  cpSync(join(models, `pii-${size}`, "tokenizer.web.json"), join(out, "models", `pii-${size}`, "tokenizer.web.json"));
  cpSync(join(models, `pii-${size}`, "onnx", `model_${v}.onnx`), join(out, "models", `pii-${size}`, "onnx", `model_${v}.onnx`));
  for (const f of ["LICENSE", "NOTICE"]) {
    const src = join(models, `pii-${size}`, f);
    if (existsSync(src)) cpSync(src, join(out, "models", `pii-${size}`, f));
    else console.warn(`warning: ${src} not found; the hosted copy of pii-${size} ships without it`);
  }
}
if (check) cpSync(check, join(out, "models", "consistency.json"));
cpSync(join(here, "index.html"), join(out, "index.html"));
console.log("built", out);
