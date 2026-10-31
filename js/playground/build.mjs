// Builds the public demo into <out>/ (for the site: ../../site/public/demo): index.html, app/ (SDK source + both engines +
// onnxruntime-web), ort/ (onnxruntime-web wasm). No model files: they come from Hugging Face. The same folder works as a
// GitHub Pages site or a static Hugging Face Space. License notices ship with it: app/*.LEGAL.txt (esbuild), ort/LICENSE.txt
// (onnxruntime-web, MIT; the npm package has no LICENSE file, so the text is kept in onnxruntime-web.LICENSE.txt), and
// LICENSE.txt and NOTICE.txt (this repo's Apache-2.0 license and NOTICE plus the onnxruntime-web license), linked from the footer.
// node playground/build.mjs [out]
import { build } from "esbuild";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const js = resolve(here, "..");
const out = resolve(process.argv[2] ?? join(here, "dist"));

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
const ortLicense = join(here, "onnxruntime-web.LICENSE.txt");
cpSync(ortLicense, join(out, "ort", "LICENSE.txt"));
cpSync(join(js, "..", "LICENSE"), join(out, "LICENSE.txt"));
writeFileSync(
  join(out, "NOTICE.txt"),
  `${readFileSync(join(js, "..", "NOTICE"), "utf8")}\n${"-".repeat(80)}\n${readFileSync(ortLicense, "utf8")}`,
);
cpSync(join(here, "index.html"), join(out, "index.html"));
console.log("built", out);
