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

On our public test dataset (54 languages) the models miss 1.6% (large) to 3.3% (nano) of PII characters; the closest of the eight
public detectors we compared misses 19.7%. Scores on four external datasets, the full comparison and phone speed are in the
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
  cache?: boolean;             // keep model files in the Cache API; default true
  verify?: boolean;            // check SHA-256 of model files; default true
  onProgress?: (p) => void;    // download progress
  wasmPaths?: string;          // onnxruntime: where onnxruntime-web loads its .wasm from
  threads?: number;            // onnxruntime: WebAssembly threads (cross-origin isolated pages)
  relaxedSimd?: boolean;       // builtin: force the fused multiply-add build on or off (default: detected)
}): Promise<Redactor>

redactor.detect(text)            // → Span[] {start, end, label, source}, sorted, non-overlapping
redactor.redact(text, { mode })  // mode "tag" → [EMAIL], "mask" → *****, "blank" → removed; or replace: (span, original) => string
redactor.setSensitivity(value)
redactor.engine                  // "onnxruntime" | "builtin"
redactor.dispose()
```

Offsets are UTF-16 indices, as used by `String.prototype.slice`. Inference runs on the calling thread; for long texts, create
the redactor inside a Web Worker.

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
