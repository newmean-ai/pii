English | [한국어](README.ko.md)

# NewmeanPII (Swift)

Detect and mask personal information in text **on the device**, on iOS and macOS. The same multilingual models
(`newmean-pii-<size>`, 54 languages) and the same spans as the npm package [`@newmean-ai/pii`](../js), run by a small C
core ([`native/core`](../native/core)) compiled into your app. No network at run time once the model files are on the
device; nothing is sent anywhere.

## Install

Swift Package Manager: in Xcode, File › Add Package Dependencies… › `https://github.com/newmean-ai/pii`, product
`NewmeanPII`. Or in `Package.swift`:

```swift
.package(url: "https://github.com/newmean-ai/pii", from: "1.3.0"),
// target: .product(name: "NewmeanPII", package: "pii")
```

iOS 15+, macOS 12+. No dependencies; the package adds a few hundred KB of code to the app.

## Quick start

```swift
import NewmeanPII

let redactor = try await PIIRedactor(size: .medium)
let (text, spans) = try await redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다")
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

## Models

| size | file | notes |
|---|---|---|
| `.nano` | 2.6 MB | one shared layer applied 4 times, rank-32 embeddings |
| `.small` | 7.1 MB | 4 layers, rank-64 embeddings |
| `.medium` (default) | 11.3 MB | 6 layers, rank-192 embeddings |
| `.large` | 15.5 MB | 6 layers, full (pruned-vocabulary) embeddings |

Plus a 0.8 MB tokenizer (shared by all sizes). Default weight file: `q4e` for large and medium, `q4` for small and nano,
as in the npm package. Every file is checked against the size and SHA-256 pinned in the package before it is used.

**Bundled in the app (recommended).** Download the two files of one size from Hugging Face
(`https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.1.0/`): `tokenizer.web.json` and `onnx/model_<variant>.onnx`.
Add them to the app target (a folder reference keeps the `onnx/` folder; plain files land next to each other, which also
works) and pass the folder:

```swift
let redactor = try await PIIRedactor(size: .medium, modelDirectory: Bundle.main.resourceURL!)
```

**Downloaded once.** Without `modelDirectory`, the files are downloaded from Hugging Face on first use into
`Caches/newmean-pii/v1.1.0/pii-<size>/` and checked again on every load. The system may clear Caches; files are then
downloaded again. To prefetch, use another folder, or serve the files yourself:

```swift
let store = PIIModelStore(directory: appSupportURL.appendingPathComponent("newmean-pii"))
let dir = try await store.download(.medium, from: URL(string: "https://example.com/pii-medium/"))  // default: Hugging Face
let redactor = try await PIIRedactor(size: .medium, modelDirectory: dir)
```

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee. Results:
[repository README](https://github.com/newmean-ai/pii#results).

## Labels

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, and two
level 2 labels for indirect identifiers: `PERSON_L2` (a surname with a title, a nickname, initials) and `URL_L2` (public
profile or page URLs). Pass `labels: PIILabel.level1` to leave level 2 visible.

## API

```swift
try await PIIRedactor(
  size: PIISize = .medium,          // .nano, .small, .medium, .large
  variant: PIIVariant? = nil,       // .q4 or .q4e; default q4e for large/medium, q4 for small/nano
  sensitivity: Double = 0,          // > 0 masks more, < 0 masks less (about -1...1)
  labels: Set<PIILabel> = PIILabel.all,
  regex: Bool = false,              // also mask a few patterns (email, URL, card/account, phone, Korean resident number);
                                    // default false (model only): almost no extra coverage, double the over-masking
  keepEmoji: Bool = false,          // true: the model also reads emoji (1.0 behaviour); default: never read or masked
  wholeWords: Bool = true,          // true: a span that ends inside a word covers the whole word
  strictShortNames: Bool = false,   // true: short texts (≤ 8 tokens) need stronger evidence to be read as names
  modelDirectory: URL? = nil)       // folder with the model files; nil: download once into Caches

try await redactor.detect(text)                  // [PIISpan], sorted, non-overlapping
try await redactor.redact(text, mode: .tag)      // (text, spans); .tag → [EMAIL], .mask → *****, .mask(with: "•"),
                                                 // .blank → removed, .custom { span, original in ... }
try await redactor.detectTable(text)             // table mode (see Tables)
try await redactor.redactTable(text, mode: .tag) // (text, spans)
redactor.sensitivity = 0.3                       // changes the threshold without reloading
PIIRedactor.applyRedaction(to: text, spans: spans, mode: .tag)
PIIRegex.spans(in: text)                         // the pattern layer on its own
```

`PIISpan` has `range` (`Range<String.Index>` in the text it was detected in), `utf16Range` (NSString / NSRange offsets,
the npm package's `start` and `end`), `scalarRange` (Unicode scalars, Python's offsets), `label` and `source`
(`.model`, `.regex`, `.modelAndRegex`, `.table`). Spans are widened to whole grapheme clusters (UAX #29, as the C core
implements it), so an emoji, flag or combining sequence is masked whole or kept whole. Slice with
`text.unicodeScalars[span.range]` or `utf16Range`, as `redact` does. `.mask` writes one mask character per Unicode scalar,
like the npm package. By default emoji are taken out of the text the model reads, so the model does not read them as PII (offsets still refer
to the input; an emoji inside a span splits it); `keepEmoji: true` gives the model the text as given (1.0 behaviour).
By default (`wholeWords`, since 1.3.0), a span that starts or ends inside a word is widened to the whole word ("R23" → `[ID_NUMBER]`, not `[ID_NUMBER]23`); letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, …) never extend a span, and a word ends where Hangul meets digits or Latin letters (「010-1234-5678입니다」 → `[PHONE]입니다`), and Korean particles after a Hangul span are left as they are (「홍길동님께」 → `[PERSON]님께`). It changes where a span ends, not what is masked: a short value the model misreads as a name, such as 「경기도」, becomes `[PERSON]` rather than `[PERSON]기도`; `strictShortNames` leaves such values alone. `wholeWords: false` gives the 1.2 spans. With `strictShortNames: true`, a text the model reads as at most 8 tokens (a survey answer, a table cell) needs stronger evidence to be read as a person's name: 「경기」, 「남성」 and 「네이버」 are left alone, while 「김민지」 and "Maria Lopez" are still found. With large, short survey items without personal information are masked 4.3% of the time instead of 7.9%, and 96.5% of lone names are found instead of 98.9%. Longer texts are unchanged. `redactor.version` is the model release (1.1.0); `redactor.info` adds the package
version (`PIIRedactor.packageVersion`), size, variant and the two checked model files (`PIIModelFile`: path, bytes, SHA-256).

**Threads.** `PIIRedactor` is `Sendable` and needs no locking: any number of tasks may call `detect` on one instance at
once. The async methods run the model on a detached task, never on the caller's actor, so calling them from the main
actor keeps the UI responsive. Synchronous `detect` and `redact` overloads (used from non-async code, and by Swift from
top-level code in `main.swift`) block the calling thread. Speed per text on one core: about 10 ms (nano) to 15 ms
(medium) for a short text and 40 to 60 ms for a paragraph of 130 tokens on Apple M5 Pro (the C core's numbers; phones are
slower).

**Debug builds are slow.** Xcode compiles package C code without optimization in the Debug configuration, which makes
the model about 18 times slower (about 350 ms instead of 19 ms for a short text with medium on Apple M5 Pro). Measure
speed in a Release build; Release compiles the core at `-Os`, as fast as `-O3`.

## Tables

```swift
try await redactor.detectTable(_ text: String, delimiter: PIITableDelimiter = .auto, header: Bool = true,
                               vote: Bool = true, context: PIITableContext = .none) -> [PIISpan]
try await redactor.redactTable(_ text: String, delimiter: PIITableDelimiter = .auto, header: Bool = true,
                               vote: Bool = true, context: PIITableContext = .none,
                               mode: PIIRedactionMode = .tag) -> (text: String, spans: [PIISpan])
```

Table mode reads a CSV, TSV, semicolon-separated or markdown pipe table as one text and clips every span to a cell value:
delimiters, quotes and line breaks are never masked, so the output is still a valid table. `PIITableDelimiter` is `.auto`
(detected), `.comma`, `.tab`, `.semicolon` or `.markdown`; quoted fields follow RFC 4180. With `vote`, a column of short
values is masked whole when the model finds names, emails, phone numbers or usernames in at least 80% of its non-empty cells
(and at least 4; columns with fewer than 5 non-empty cells do not vote); those spans have source `.table`. Free-text columns
(median value over 40 characters or 4 words) do not vote, placeholders (`N/A`, `-`, `none`, `null`, `없음`) are skipped, an
email column needs `@` and a phone column 6 or more digits. With `header`, the first row is masked where the model finds PII
but never voted on. With `context: .header` (`PIITableContext`; default `.none`) and a header row, each non-empty data cell
of a column whose non-empty values are at least 80% distinct is also read on its own as "<header>: <value>" and those spans
are added: fewer misses on name, contact and ID columns, more PII-free cells masked, one more model call per such cell.
Text that is not a table gives exactly the spans of `detect`. A `.custom` replacement must not insert
the delimiter, a quote or a line break. Synchronous overloads block the calling thread, as for `detect`.

```swift
let csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free"
let (masked, _) = try await redactor.redactTable(csv)
// "name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free"
let (tsvMasked, _) = try await redactor.redactTable(tsv, delimiter: .tab, vote: false, mode: .mask)
```

On survey and form exports, table mode misses many names split into first and last name columns. `context: .header` cuts the
misses to about a quarter, at 2.3 to 3.7 more points of PII-free cells masked; for recall first, use it. Measurements:
[repository README](https://github.com/newmean-ai/pii#tables-csv-tsv).

## Development

```bash
swift build
swift test -c release           # release: the C core is slow at -O0
xcodebuild -scheme NewmeanPII -destination 'generic/platform=iOS' build
xcodebuild -scheme NewmeanPII -destination 'generic/platform=iOS Simulator' build
```

`Package.swift` sits at the repository root so the repository URL can be added as a package. The C target `CNewmeanPII`
(`swift/Sources/CNewmeanPII`) compiles `native/core/src` through one-line shims (`api.c` includes
`native/core/src/api.c`, ...) and exposes only `pii.h` through its module map; a test checks that every core file has a
shim. The Swift sources are in `swift/Sources/NewmeanPII`, the tests in `swift/Tests/NewmeanPIITests`.

The parity tests read the private fixtures and model copies in place (`PII_FIXTURES`, default `js/test/fixtures`;
`PII_MODELS`, default `js/test/models` with folders `pii-<size>` in the Hugging Face layout) and skip without them.
`PII_SIZES=nano,medium` limits the sizes; `PII_DOWNLOAD=1` adds a real download of nano (3.4 MB) from Hugging Face.

| Test | Checks |
|---|---|
| tokenizer | ids and offsets of `tokenize()` vs Hugging Face tokenizers on `tokenize.jsonl` (3,273 rows; ids all, offsets all but 3 rows at most, as the npm test) |
| prediction | spans of `detect()` vs the Python evaluator on every run of `js/test/predict.test.ts` (412 rows each, ≥ 99%) |
| regex | `PIIRegex` vs the Python baseline on `regex.jsonl` (3,273 rows); JavaScript `\s` cases |
| redaction, offsets | modes of `js/test/combine.test.ts`; emoji, ZWJ sequences, flags and combining marks; bridged NSString |
| options, threads, files | labels, sensitivity, regex merging; 4 × 48 concurrent calls; damaged and missing model files |
| package | a shim for every `native/core/src/*.c`; the default (non-FMA) kernel build; all 12 pins equal `js/src/models.ts` |

## License

[Apache-2.0](LICENSE), for the package code and the model weights; third-party notices in [NOTICE](NOTICE). If you use them
in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a request,
not a license condition). Made by [newmean.ai](https://newmean.ai).
