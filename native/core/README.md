English | [한국어](README.ko.md)

# newmean-pii native core

One C library that every native package (Python, Android, iOS, Flutter) compiles: it loads the published
`newmean-pii-<size>` model files, tokenizes, runs the model and returns the spans to mask, the same spans as `detect()` of
the npm package [`@newmean-ai/pii`](../../js) with `regex: false` (same token ids and offsets, bit-identical logits).
Plain C11, no dependencies, no network, no global state.

## Layout

| Path | Port of | What |
|---|---|---|
| `pii.h` | | Public API (the only header packages include) |
| `src/engine.c`, `src/simd.h` | `js/engine/pii.c` | Inference: same kernels, same order of floating-point operations |
| `src/onnx.c` | `js/src/engines/onnx-weights.ts` | Minimal protobuf reader; lays `model_<variant>.onnx` out for the engine (same checks) |
| `src/tokenizer.c`, `src/unicode_tables.h` | `js/src/tokenizer.ts` | Unigram tokenizer over `tokenizer.web.json` (Precompiled normalizer, WhitespaceSplit, Metaspace, Viterbi) |
| `src/api.c` | `js/src/decode.ts`, `combine` in `js/src/redactor.ts` | Windows, best tag per token, BIO spans, merging, label filter |
| `src/util.c` | | JSON and base64 readers, UTF-8 decoding with offset tables, aligned memory |
| `src/sha256.c`, `src/models.c` | `js/src/models.ts` | SHA-256 and the pinned file sizes and hashes (generated) |
| `scripts/` | | `gen_models.py` (pins from `js/src/models.ts`), `gen_unicode.py` (grapheme tables) |
| `tests/` | | `pii_cli.c` (test CLI), `run_tests.py` (parity tests), `js_detect.mjs` (npm engine for comparison) |

## API

```c
#include "pii.h"

// files: https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/{onnx/model_<variant>.onnx, tokenizer.web.json}
if (pii_verify_file(onnx, onnx_len, "medium", "q4e") || pii_verify_file(tok, tok_len, "medium", "tokenizer")) { /* refuse */ }
char err[256];
pii_model *m = pii_model_load(onnx, onnx_len, tok, tok_len, err, sizeof err);  // the input buffers may be freed now

pii_options o;
pii_options_init(&o);         // sensitivity 0, all labels
o.sensitivity = 0.3;          // > 0 masks more, < 0 masks less (npm `sensitivity`)
o.labels = PII_LABELS_LEVEL1; // npm LEVEL1_LABELS; or PII_LABEL_BIT(PII_EMAIL) | ...

pii_span *spans;
size_t n;
if (pii_detect(m, text, strlen(text), &o, &spans, &n) == PII_OK) {
  for (size_t i = 0; i < n; i++)  // sorted, non-overlapping
    printf("%s [%zu, %zu)\n", spans[i].label_name, spans[i].byte_start, spans[i].byte_end);
  pii_spans_free(spans);
}
pii_model_free(m);
```

- **Spans** carry `[start, end)` in UTF-8 bytes, code points (Python `str`) and UTF-16 units (JavaScript, Java/Kotlin,
  Dart, `NSString`), so wrappers convert nothing. Invalid UTF-8 bytes count as one code point each. Offsets fall on code
  point boundaries; a grapheme the tokenizer's normalizer rewrites (for example `e` + U+0301) stays whole, but a span
  can still end inside another grapheme, such as a ZWJ sequence the model splits: slice by code points or UTF-16 units,
  not by Swift `Character` indices. Pass standard UTF-8: `String.getBytes(UTF_8)` on the JVM, not
  JNI `GetStringUTFChars` (modified UTF-8 splits emoji into surrogates and NUL into two bytes, which changes tokens and
  offsets); `str.encode("utf-8", "surrogatepass")` in Python.
- **Errors**: functions return `PII_OK` or a `PII_ERR_*` code (`pii_strerror` gives the message); `pii_model_load` writes
  its reason to `err`. Truncated or corrupted model and tokenizer files fail with a message, never crash. Returned arrays
  are owned by the caller and freed with `pii_spans_free` / `pii_tokens_free` (not `free`, which may belong to another C
  runtime on Windows). Texts up to `INT32_MAX` bytes; working memory is about 20 bytes per input byte plus one window.
- **Options** follow the npm package: `sensitivity` (the O logit gets `-sensitivity`), `labels` (bit set; default all 13).
  `extra` takes spans from a wrapper's pattern layer and merges them exactly like the npm `combine` (the model's label
  wins; a pattern label counts only where no model label of the same family covers the text), so the pattern layer can
  stay in each language.
- **Pins and defaults**: `pii_model_file(size, file)` gives the path, size and SHA-256 of each published file;
  `pii_default_variant(size)` the npm default (`q4e` for large and medium, `q4` for small and nano). Default size:
  `PII_DEFAULT_SIZE` (`medium`).
- **Threads**: a `pii_model` is immutable after loading. Any number of threads may call `pii_detect` / `pii_tokenize` on
  one model at once; each call allocates its own scratch memory (about 4.5 MB for a full 256-token window, less for short texts).
- `pii_tokenize` returns token ids and offsets (without `<s>` and `</s>`); `pii_model_get_info` the model shape;
  `pii_build_info` the kernel build.

Left to each language (small and idiomatic there): the regex layer (`js/src/regex.ts`, off by default as in npm) and the
redaction modes of `applyRedaction` (`tag` → `[FAMILY]` with `PERSON_L2` → `PERSON` and `URL_L2` → `URL`; `mask` → one
mask character per code point; `blank`).

## Build

```bash
make                 # build/libpii.a, build/libpii.{dylib,so}, build/pii_cli
make FMA=1           # fused multiply-add kernel (see below)
make asan            # build/asan/pii_cli with AddressSanitizer + UndefinedBehaviorSanitizer

cmake -S native/core -B build && cmake --build build      # targets newmean::pii (static), newmean::pii_shared
#   -DPII_FMA=ON  -DPII_BUILD_SHARED=OFF  -DPII_BUILD_TESTS=ON  -DPII_SANITIZE=ON
```

C11 with `-O3 -Wall -Wextra -Wshadow`. The SIMD layer (`src/simd.h`) uses GCC/Clang vector extensions, which compile to
NEON on arm64 and SSE on x86-64; other compilers (MSVC) get a scalar path with the same IEEE operations
(`-DPII_SCALAR` forces it). `-ffp-contract=off` (MSVC: `/fp:precise`) is part of the build: it keeps every multiply and
add separate, as in the WebAssembly engine. Little-endian targets only (the model files are little-endian).

**FMA build.** By default the kernel multiplies and adds separately, so logits are bit-identical on every CPU and equal to
the npm package's baseline (SIMD128) WebAssembly build. `PII_FMA` fuses the matrix multiply-add, like the npm
package's relaxed-SIMD build (which Chrome, Edge and Firefox run); it is about 25% faster on Apple M5 and needs NEON
(arm64) or FMA3 (x86-64, `-mfma`). Its logits differ from the default build in the last bits; spans were identical on
every fixture row checked.

## Tests

```bash
make && make asan && python3 tests/run_tests.py [--only pins,selftest,tokenizer,offsets,predict,js,threads,asan,fuzz] [--bench]
```

Python standard library only. The fixtures and model copies are private (validation text) and are read in place, never
copied: `PII_FIXTURES` (default `js/test/fixtures`) and `PII_MODELS` (default `js/test/models`); every test skips when
they are absent. `PII_SIZES=nano,medium` limits the sizes. `fuzz` runs only when named (`PII_FUZZ=N` sets its size).

| Test | Checks | Target |
|---|---|---|
| pins | model files match the pinned size and SHA-256 | all |
| selftest | `combine` cases of `js/test/combine.test.ts`; arguments, label filter, `extra` spans in UTF-16 units, invalid UTF-8, long texts; truncated and mutated model and tokenizer files fail cleanly | all |
| tokenizer | ids and offsets vs the npm package's `Tokenizer` (node + `js/dist`) and vs Hugging Face `tokenizers` on `tokenize.jsonl` (3,273 rows) | npm: 100%; Hugging Face: ids 100%, offsets all but 3 rows at most (as the npm test) |
| offsets | UTF-8, code point and UTF-16 offsets vs Python on hard texts: emoji and ZWJ sequences, flags, combining marks, CJK, RTL and bidi controls, zero-width characters, CRLF, NUL, lone surrogates, invalid UTF-8, empty and blank input, special-token strings, 10,000+ tokens; spans sorted and inside the text | all |
| predict | spans vs the Python evaluator for every run of `js/test/predict.test.ts` (412 rows each) | ≥ 99% of rows, as the npm test |
| js | spans vs the npm package's own WebAssembly engine (`node`, `js/dist`, `tests/js_detect.mjs`); `PII_JS=all` for every run | 100% |
| threads | 4 threads sharing one model (nano, medium; short and multi-window texts) give the single-thread results | identical |
| asan | selftest, 600 tokenizer rows, offsets, 60 nano rows and 3 threads under ASan + UBSan | clean |
| fuzz | `pii_cli fuzz`: N truncated or bit-flipped model files, N / 2 mutated tokenizer files, 2N random byte strings (ASan + UBSan when built) | load or fail with a message; spans inside the input |

Results on Apple M5 Pro (macOS, Apple clang 21, default build), 2026-10-31:

| Test | Result |
|---|---|
| pins | 12/12 files (4 sizes × q4, q4e, tokenizer) |
| selftest | passed (default build and ASan + UBSan) |
| tokenizer | ids 3,273/3,273, offsets 3,273/3,273 |
| predict large q4e, large q4, medium q4e, medium q4, small q4, small q4e, nano q4e, medium q4e bias −0.5 | 412/412 each (100%) |
| predict nano q4, nano q4 bias 1 | 410/412 each (99.51%) |
| js (nano q4, nano q4 bias 1, medium q4e, medium q4e bias −0.5) | 412/412 each; the FMA build against the relaxed-SIMD build: 412/412 each |
| threads | nano and medium, 203 texts × 4 threads: 0 differences; 153 texts × 8 threads (medium q4e, nano q4 at sensitivity 0.5): 0 differences |
| asan | no reports |
| offsets | 24 hard texts (up to 13,996 tokens): every UTF-8, code point and UTF-16 offset equal to Python's |
| fuzz (ASan + UBSan) | nano q4 and q4e: 800 mutated model files and 550 tokenizer files, each loads or fails with a message; 1,800 random byte strings, spans and tokens inside the input; no reports. No leaks (`leaks --atExit`, 900 more mutated files) |
| logits | 13 windows × nano q4, medium q4e, large q4 (30,294 logits each): default build bit-identical to the wasm SIMD128 build, `FMA=1` bit-identical to the wasm relaxed-SIMD build (Node, arm64) |
| other builds | GCC 16 (NEON and `-DPII_SCALAR`) give byte-identical output to Apple clang on all 412 nano q4 rows; x86-64 (`-target x86_64-apple-macos12`, with and without `-mfma -DPII_FMA`) compiles without warnings, not run |

Rows that differ from the Python evaluator differ in the npm package as well: there the evaluator reports two
overlapping spans with different labels (for example `20-21:ADDRESS 20-22:ORG`), and `detect()` merges overlapping spans
into one region (`combine`), as both packages do.

Speed, one thread, default build (`--bench`; `make FMA=1` in brackets):

| Model | Short text (≤ 64 tokens, 36 on average) | Fixture text (131 tokens on average) | 1,062-token text |
|---|---|---|---|
| nano q4 | 9.9 ms (7.8 ms) | 39.6 ms (29.6 ms) | 364 ms (269 ms) |
| small q4 | 9.9 ms | 39.7 ms | 366 ms |
| medium q4e (default) | 14.9 ms (11.9 ms) | 59.7 ms (45.0 ms) | 550 ms (412 ms) |
| large q4e | 14.8 ms | 59.3 ms | 547 ms |

The matrix kernel runs at about 78% of the separate multiply and add peak of one core, so the default build is only about
15% faster than the npm package's wasm engine in Node on the same rows (nano: 16.4 s against 19.2 s for 412 rows).

## Relation to `js/engine/pii.c`

`src/engine.c` is the same engine with three mechanical changes: wasm SIMD128 intrinsics become the `v_*` functions of
`src/simd.h` (one per intrinsic, same lane-wise IEEE operation), the bump allocator becomes `malloc`, and the global model
state becomes an immutable `pii_engine` plus per-call scratch (`pii_ws`). Kernels, the `.piiw` layout, the 4-bit panel
format, the exp/erf approximations and the order of every sum are unchanged; `js/engine/pii.c` itself is not touched.
Changes to the engine should be made in both files and checked with the `js` test.

The tokenizer and `combine` follow the npm package where it differs from Hugging Face or from a code point count:

- When the normalizer replaces a grapheme (for example `e` + U+0301 → `é`), every new character is aligned to the whole
  grapheme, as `tokenizer.ts` does; Hugging Face aligns each to one original character (`NormalizedString::transform`).
  Token offsets therefore equal the npm package's on all 3,273 rows and Hugging Face's on 3,272 (`e\u0301cole`: `[0, 2]`
  rather than `[0, 1]`). A span never ends between a letter and a combining mark the normalizer merged with it.
- Where overlapping model spans are merged into one region, the longest span gives the label, its length counted in
  UTF-16 units as `redactor.ts` does (an emoji counts 2).

## Licence

Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)).
