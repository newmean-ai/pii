// Spans of the npm package's detectTable() (builtin WebAssembly engine, regex layer on) for JSON lines with "text" and the
// optional fields of pii_cli table ("delimiter", "header", "vote", "labels", "context", "words", "strict"); one line per input:
//   {"spans": [[start, end, label, source], ...], "extra": [[cp_start, cp_end, label], ...]}
// spans in UTF-16 units with source as pii.h bits (model 1, regex 2, model+regex 3, table 4); extra = the package's regex
// spans in code points, which run_tests.py passes to pii_cli table as pii_options.extra so both sides see the same patterns.
// Used by run_tests.py (table-mode parity); reads js/dist (build it with npm run build).
//
//   node js_table.mjs PACKAGE_DIST MODEL_DIR SIZE VARIANT SENSITIVITY [RELAXED]
//
// Without a model: the table steps alone (detectTableSpans of js/src/table.ts, the module bundled into js/dist;
// node strips its types) on given whole-text spans, the counterpart of pii_cli apply. Input lines also carry
// "spans": [[cp_start, cp_end, "LABEL", source], ...]; out: {"spans": [[cp_start, cp_end, label, source], ...]}.
//
//   node js_table.mjs apply TABLE_TS
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

if (process.argv[2] === "apply") {
  const { detectTableSpans } = await import(pathToFileURL(process.argv[3]).href);
  const SRC = { 1: "model", 2: "regex", 3: "model+regex", 4: "table" };
  const BITS = { model: 1, regex: 2, "model+regex": 3, table: 4 };
  const ALL = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"];
  const out = [];
  for (const l of readFileSync(0, "utf8").split("\n").filter((x) => x.trim())) {
    const row = JSON.parse(l);
    const text = row.text;
    const u16 = [0]; // code point index -> UTF-16 offset
    for (const ch of text) u16.push(u16[u16.length - 1] + ch.length);
    const cpOf = new Map(u16.map((u, i) => [u, i]));
    const spans = (row.spans ?? []).map(([s, e, label, src]) => ({ start: u16[s], end: u16[e], label, source: SRC[src] }));
    const table = {};
    if (row.delimiter !== undefined) table.delimiter = row.delimiter;
    if (row.header !== undefined) table.header = row.header;
    if (row.vote !== undefined) table.vote = row.vote;
    const got = detectTableSpans(text, spans, table, new Set(row.labels ?? ALL), [], row.words ?? false);
    out.push(JSON.stringify({ spans: got.map((s) => [cpOf.get(s.start) ?? -1, cpOf.get(s.end) ?? -1, s.label, BITS[s.source]]) }));
  }
  await new Promise((done) => process.stdout.write(out.join("\n") + "\n", done)); // flush the pipe before exiting
  process.exit(0);
}

const [dist, modelDir, size, variant, sens, relaxed] = process.argv.slice(2);
const pkg = await import(pathToFileURL(`${dist}/index.js`).href);
const localFetch = async (url) => new Response(readFileSync(new URL(url)));
const SOURCE = { model: 1, regex: 2, "model+regex": 3, table: 4 };
const redactors = new Map(); // labels key -> Redactor (labels are a redactor option)

async function redactor(labels, words, strict) {
  const key = (labels ? JSON.stringify(labels) : "") + (words ? " words" : "") + (strict ? " strict" : "");
  if (!redactors.has(key)) {
    const r = await pkg.createRedactor({
      engine: "builtin",
      relaxedSimd: relaxed === "1",
      size,
      variant,
      modelUrl: pathToFileURL(`${modelDir}/`).href,
      fetch: localFetch,
      cache: false,
      regex: true,
      sensitivity: Number(sens),
      ...(labels ? { labels } : {}),
      ...(words ? { wholeWords: true } : {}),
      ...(strict ? { strictShortNames: true } : {}),
    });
    if (typeof r.detectTable !== "function") throw new Error("this js/dist has no detectTable (rebuild: npm run build)");
    redactors.set(key, r);
  }
  return redactors.get(key);
}

// UTF-16 offset -> code point index (an offset inside a surrogate pair maps to the pair's code point, as pii_detect does).
function cpIndex(text) {
  const map = new Int32Array(text.length + 1);
  let cp = 0;
  for (let i = 0; i < text.length; i++) {
    map[i] = cp;
    const c = text.charCodeAt(i);
    if (!(c >= 0xdc00 && c <= 0xdfff && i > 0 && text.charCodeAt(i - 1) >= 0xd800 && text.charCodeAt(i - 1) <= 0xdbff)) cp++;
    else map[i] = cp - 1;
  }
  map[text.length] = cp;
  return map;
}

const lines = readFileSync(0, "utf8").split("\n").filter((l) => l.trim());
const out = [];
for (const l of lines) {
  const row = JSON.parse(l);
  const r = await redactor(row.labels, row.words, row.strict);
  const table = {};
  if (row.delimiter !== undefined) table.delimiter = row.delimiter;
  if (row.header !== undefined) table.header = row.header;
  if (row.vote !== undefined) table.vote = row.vote;
  if (row.context !== undefined) table.context = row.context;
  const spans = await r.detectTable(row.text, table);
  const cp = cpIndex(row.text);
  const extra = pkg.regexSpans(row.text).map((s) => [cp[s.start], cp[s.end] + (cp[s.end] === cp[s.end - 1] && s.end > s.start ? 1 : 0), s.label]);
  out.push(JSON.stringify({ spans: spans.map((s) => [s.start, s.end, s.label, SOURCE[s.source]]), extra }));
}
for (const r of redactors.values()) await r.dispose();
process.stdout.write(out.join("\n") + "\n");
