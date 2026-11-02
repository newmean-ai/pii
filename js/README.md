English | [한국어](README.ko.md)

# @newmean-ai/pii

Detect and mask personal information in text **inside the browser**, before the text is sent anywhere. A small multilingual
token-classification model (54 languages) runs in WebAssembly.

```bash
npm install @newmean-ai/pii
```

```ts
import { createRedactor } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium" });
const { text, spans } = await redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다");
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

## Models

| size | file | notes |
|---|---|---|
| `nano` | 2.6 MB | one shared layer applied 4 times, rank-32 embeddings |
| `small` | 7.1 MB | 4 layers, rank-64 embeddings |
| `medium` (default) | 11.3 MB | 6 layers, rank-192 embeddings |
| `large` | 15.5 MB | 6 layers, full (pruned-vocabulary) embeddings |

Plus a 0.8 MB tokenizer (shared by all sizes). Files are downloaded once from
Hugging Face (`newmean-ai/pii-<size>`), kept in the Cache API, and checked against SHA-256 values pinned in this package.
To serve them yourself, copy the model folder and pass `modelUrl`.

## Engines

Two engines run the same model file and give the same spans; pick one with `engine`.

| `engine` | runtime download | notes |
|---|---|---|
| `"builtin"` (default) | about 20 KB (brotli, JS and WebAssembly together) | this package's own engine for these models; starts faster, and on the phones we measured runs about 1.9× faster on short texts and about 24% faster on long ones (mean of four phones); uses fused multiply-add where the browser supports relaxed SIMD (Chrome, Edge, Firefox) |
| `"onnxruntime"` | onnxruntime-web, about 2.4 MB (brotli) | general ONNX runtime, multi-threading on cross-origin isolated pages |

Each engine is a separate chunk loaded on first use, so an app downloads only the engine it picks.

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee.

## Results

On newmean-pii-dataset (public test, 54 languages) the models miss 1.6% (large) to 2.9% (nano) of PII characters; the closest of the eight
public detectors we compared misses 19.6%. Scores on four external datasets, the full comparison and phone speed are in the
[repository README](https://github.com/newmean-ai/pii#results) and the technical report; weekly scores are on the
[leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

## Labels

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, and two
level 2 labels for indirect identifiers: `PERSON_L2` (a surname with a title, a nickname, initials) and `URL_L2` (public
profile or page URLs). Pass `labels: LEVEL1_LABELS` to leave level 2 visible.

## API

```ts
createRedactor(options?: {
  engine?: "builtin" | "onnxruntime";  // default "builtin"
  size?: "nano" | "small" | "medium" | "large";
  variant?: "q4" | "q4e";      // weight file; default q4e for large/medium, q4 for small/nano
  modelUrl?: string;           // base URL of a self-hosted model folder (ending with "/")
  sensitivity?: number;        // > 0 masks more, < 0 masks less (about −1..1); default 0
  labels?: string[];           // labels to mask; default all
  regex?: boolean;             // also mask a few patterns (email, URL, card/account, phone, Korean resident number);
                               // default false (model only): almost no extra coverage, double the over-masking
  keepEmoji?: boolean;         // true: the model also reads emoji (1.0 behaviour); default false: never read or masked
  wholeWords?: boolean;        // true: a span that ends inside a word covers the whole word; default false
  strictShortNames?: boolean;  // true: short texts (≤ 8 tokens) need stronger evidence to be read as names; default false
  cache?: boolean;             // keep model files in the Cache API; default true
  verify?: boolean;            // check SHA-256 of model files; default true
  onProgress?: (p) => void;    // download progress
  fetch?: typeof fetch;        // used to download the model files; default globalThis.fetch (pass one in Node or tests)
  wasmPaths?: string;          // onnxruntime: where onnxruntime-web loads its .wasm from
  threads?: number;            // onnxruntime: WebAssembly threads (cross-origin isolated pages)
  relaxedSimd?: boolean;       // builtin: force the fused multiply-add build on or off (default: detected)
}): Promise<Redactor>

redactor.detect(text)            // → Span[] {start, end, label, source}, sorted, non-overlapping
redactor.redact(text, { mode })  // mode "tag" → [EMAIL], "mask" → *****, "blank" → removed; or replace: (span, original) => string
redactor.detectTable(text, table?)           // table mode (see Tables)
redactor.redactTable(text, table?, options?) // → { text, spans }; options as redact
redactor.setSensitivity(value)
redactor.engine                  // "onnxruntime" | "builtin"
redactor.version                 // model release ("1.1.0"); the package version is VERSION or info.packageVersion
redactor.info                    // { packageVersion, modelVersion, size, variant, engine, modelUrl,
                                 //   files: { model, tokenizer: { path, bytes, sha256 } }, verified }
redactor.dispose()
```

Offsets are UTF-16 indices, as used by `String.prototype.slice`. Spans are widened to whole grapheme clusters, so an emoji,
flag or combining sequence is masked whole or kept whole (this needs `Intl.Segmenter`; where it is missing, spans stay on code
point boundaries). By default emoji are taken out of the text the model reads, so the model does not read them as PII: offsets still refer to the
input, and an emoji inside a span splits it in two. `keepEmoji: true` gives the model the text as given (1.0 behaviour).
With `wholeWords: true`, a span that starts or ends inside a word is widened to the whole word: 「경기도」 becomes `[PERSON]`
instead of `[PERSON]기도`, and "R23" `[ID_NUMBER]` instead of `[ID_NUMBER]23`. A word is a run of letters, digits and marks
joined by apostrophes and hyphens; letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, …) never
extend a span. It masks a little more text and nothing less; in table mode it stays inside the cell.

With `strictShortNames: true`, a text the model reads as at most 8 tokens (a survey answer, a table cell) needs stronger evidence to be read as a person's name: 「경기」, 「남성」 and 「네이버」 are left alone, while 「김민지」 and "Maria Lopez" are still found. With large, short survey items without personal information are masked 4.3% of the time instead of 7.9%, and 96.5% of lone names are found instead of 98.9%. Longer texts are unchanged.
`info` names what ran, for audit records: the package and model versions and the size and SHA-256 of the two model files
(checked when `verify` is on).
Inference runs on the calling thread; for long texts, create the redactor inside a Web Worker.

## Tables

```ts
redactor.detectTable(text: string, table?: {
  delimiter?: "auto" | "," | "\t" | ";" | "markdown";  // default "auto"
  header?: boolean;            // the first row is a header row; default true
  vote?: boolean;              // column vote; default true
  context?: "none" | "header"; // "header": also read cells of distinct-valued columns as "<header>: <value>"; default "none"
}): Promise<Span[]>
redactor.redactTable(text: string, table?: TableOptions, options?: RedactOptions): Promise<{ text: string; spans: Span[] }>
```

Table mode reads a CSV, TSV, semicolon-separated or markdown pipe table as one text (delimiter detected unless given; quoted
fields as in RFC 4180) and clips every span to a cell value: delimiters, quotes and line breaks are never masked, so the
output is still a valid table. With `vote`, a column of short values is masked whole when the model finds names, emails,
phone numbers or usernames in at least 80% of its non-empty cells (and at least 4; columns with fewer than 5 non-empty cells
do not vote); those spans have source `"table"`. Free-text columns (median value over 40 characters or 4 words) do not vote,
placeholders (`N/A`, `-`, `none`, `null`, `없음`) are skipped, an email column needs `@` and a phone column 6 or more digits.
The header row is masked where the model finds PII but never voted on. With `context: "header"` and a header row, each
non-empty data cell of a column whose non-empty values are at least 80% distinct is also read on its own as
`"<header>: <value>"` and those spans are added: fewer misses on name, contact and ID columns, more PII-free cells masked,
one more model call per such cell. Text that is not a table gives exactly the spans of `detect`. A custom `replace` must
not insert the delimiter, a quote or a line break.

```ts
const csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free";
const { text } = await redactor.redactTable(csv);
// "name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free"
await redactor.redactTable(tsv, { delimiter: "\t", vote: false }, { mode: "mask" });
```

On survey and form exports, table mode misses many names split into first and last name columns. `context: "header"` cuts
the misses to about a quarter, at 2.3 to 3.7 more points of PII-free cells masked; for recall first, use it. Measurements:
[repository README](https://github.com/newmean-ai/pii#tables-csv-tsv).

## Development

```bash
npm install
npm run typecheck   # tsc --noEmit
npm test            # vitest; the parity tests skip without fixtures
npm run build       # dist/
npm run demo        # test page (vite), needs demo/models/
```

The built-in engine's WebAssembly is checked in as base64 (`src/engines/wasm-*.ts`); after changing `engine/pii.c`, run
`engine/build.sh` (LLVM clang with the wasm32 target) and then `node scripts/embed-wasm.mjs`.

The demo page and the parity tests read local copies of the four Hugging Face model repos (`newmean-ai/pii-<size>`):

```
demo/models/pii-<size>/tokenizer.web.json
demo/models/pii-<size>/onnx/model_q4.onnx, model_q4e.onnx
test/models/pii-<size>/...    (same layout; or set PII_MODELS to a folder with it)
```

The parity tests compare the SDK with the Python evaluator on reference outputs in `test/fixtures/`. The fixtures are made
from validation text and are not published, so these tests skip without them.

## License

[Apache-2.0](LICENSE), for the SDK code and the model weights; third-party notices in [NOTICE](NOTICE). If you use them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a request, not a license condition). Made by [newmean.ai](https://newmean.ai).
