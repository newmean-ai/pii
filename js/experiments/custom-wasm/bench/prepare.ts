// Builds texts.json: three made-up texts of about 40, 250 and 1,000 tokens (from demo/samples.ts), for bench pages.
// Needs test/models/pii-medium/tokenizer.web.json. Run in js/experiments/custom-wasm (no build output is written):
//   npx esbuild bench/prepare.ts --bundle --platform=node --format=esm --log-level=warning | node --input-type=module
import { readFileSync, writeFileSync } from "node:fs";
import { SAMPLES } from "../../../demo/samples.js";
import { makeWindows } from "../../../src/decode.js";
import { Tokenizer, type WebTokenizerFile } from "../../../src/tokenizer.js";

// run from js/experiments/custom-wasm
const tokPath = "../../test/models/pii-medium/tokenizer.web.json";
const tok = new Tokenizer(JSON.parse(readFileSync(tokPath, "utf8")) as WebTokenizerFile);
const all = SAMPLES.map((s) => s.text).join("\n\n");

function cut(target: number, source: string): string {
  // longest prefix (at a space or newline) with at most `target` tokens
  let best = "";
  for (let i = 1; i <= source.length; i++) {
    if (i < source.length && !/\s/.test(source[i])) continue;
    const t = source.slice(0, i);
    if (tok.encode(t).ids.length > target) break;
    best = t;
  }
  return best;
}

let long = all;
while (tok.encode(long).ids.length < 1000) long += "\n\n" + all;
const texts = [
  { name: "short", text: cut(41, SAMPLES.find((s) => s.name === "English support")!.text) },
  { name: "window", text: cut(252, all) },
  { name: "multi", text: cut(1000, long) },
].map((t) => {
  const ids = tok.encode(t.text).ids;
  return { ...t, tokens: ids.length, windows: makeWindows(ids, tok.bosId, tok.eosId).length };
});
for (const t of texts) console.log(t.name, t.tokens, "tokens", t.windows, "windows");
writeFileSync("bench/texts.json", JSON.stringify(texts, null, 1) + "\n");
