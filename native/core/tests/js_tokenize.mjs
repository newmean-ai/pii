// Ids and offsets (UTF-16 units) of the npm package's Tokenizer for JSON lines with a "text" field on stdin; one
// {"ids": [...], "offsets": [[start, end], ...]} line per input. Used by run_tests.py to compare the native tokenizer's
// offsets with the npm SDK's; reads js/dist (build it with npm run build).
//
//   node js_tokenize.mjs PACKAGE_DIST TOKENIZER_JSON
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [dist, tokFile] = process.argv.slice(2);
const { Tokenizer } = await import(pathToFileURL(`${dist}/index.js`).href);
const tok = new Tokenizer(JSON.parse(readFileSync(tokFile, "utf8")));
const out = [];
for (const l of readFileSync(0, "utf8").split("\n")) {
  if (!l.trim()) continue;
  const e = tok.encode(JSON.parse(l).text);
  out.push(JSON.stringify({ ids: e.ids, offsets: e.offsets }));
}
process.stdout.write(out.join("\n") + "\n");
