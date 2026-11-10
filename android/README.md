English | [한국어](README.ko.md)

# ai.newmean:pii (Android)

Detect and mask personal information in text **on the device**, on Android. The same multilingual models
(`newmean-pii-<size>`, 54 languages) and the same spans as the npm package [`@newmean-ai/pii`](../js), run by a small C
core ([`native/core`](../native/core)) inside the library. No network at run time once the model files are on the
device; nothing is sent anywhere.

## Install

```kotlin
// build.gradle.kts of the app module
dependencies {
    implementation("ai.newmean:pii:1.5.0")
}
```

Android 7.0+ (minSdk 24), ABIs `arm64-v8a`, `armeabi-v7a` and `x86_64`. One dependency (kotlinx-coroutines-core); the
native library adds about 100 KB per ABI to the app. No permissions; add `INTERNET` only to download the model files.

## Quick start

```kotlin
import ai.newmean.pii.PiiRedactor

val redactor = PiiRedactor.create(context)   // suspend; medium, model files from the app's assets
val (text, spans) = redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다")
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

From Java, or off a coroutine:

```java
try (PiiRedactor redactor = PiiRedactor.createBlocking(context)) {  // on a background thread
    String masked = redactor.redact(text).getText();
}
```

## Models

| size | file | notes |
|---|---|---|
| `NANO` | 2.6 MB | one shared layer applied 4 times, rank-32 embeddings |
| `SMALL` | 7.1 MB | 4 layers, rank-64 embeddings |
| `MEDIUM` (default) | 11.3 MB | 6 layers, rank-192 embeddings |
| `LARGE` | 15.5 MB | 6 layers, full (pruned-vocabulary) embeddings |

Plus a 0.8 MB tokenizer (shared by all sizes). Default weight file: `q4e` for large and medium, `q4` for small and nano,
as in the npm package. Every file is checked against the size and SHA-256 pinned in the library before it is used.

**Bundled in the app (recommended).** Download the two files of one size from Hugging Face
(`https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.1.0/`) into the app's assets, in the layout of the repository:

```
app/src/main/assets/newmean-pii/pii-medium/tokenizer.web.json
app/src/main/assets/newmean-pii/pii-medium/onnx/model_q4e.onnx
```

```bash
cd app/src/main/assets && mkdir -p newmean-pii/pii-medium/onnx && cd newmean-pii/pii-medium
curl -LO https://huggingface.co/newmean-ai/pii-medium/resolve/v1.1.0/tokenizer.web.json
curl -L -o onnx/model_q4e.onnx https://huggingface.co/newmean-ai/pii-medium/resolve/v1.1.0/onnx/model_q4e.onnx
```

`PiiRedactor.create(context)` reads them from there (`source = PiiModelSource.Assets(dir = "models")` for another folder, `""` for the assets root).

**Downloaded once.** With `source = PiiModelSource.Download()`, the files are downloaded from Hugging Face on first use
into `cacheDir/newmean-pii/1.1.0/pii-<size>/` and checked again on every load. The system may clear the cache; files are
then downloaded again. Pass `dir` to keep them elsewhere, or `baseUrl` to serve the files yourself:

```kotlin
val redactor = PiiRedactor.create(
    context, size = PiiSize.NANO,
    source = PiiModelSource.Download(dir = File(context.filesDir, "newmean-pii"), onProgress = { file, loaded, total, cached -> }),
)
// or: PiiModels.download(dir, PiiSize.NANO) and PiiRedactor.create(model, tokenizer, PiiSize.NANO)
```

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee. Results:
[repository README](https://github.com/newmean-ai/pii#results).

## Labels

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, and two
level 2 labels for indirect identifiers: `PERSON_L2` (a surname with a title, a nickname, initials) and `URL_L2` (public
profile or page URLs). Pass `labels = PiiLabels.LEVEL1` to leave level 2 visible.

## API

```kotlin
PiiRedactor.create(                       // suspend, loads on Dispatchers.IO; createBlocking(...) for Java or threads
    context,
    size = PiiSize.MEDIUM,                // NANO, SMALL, MEDIUM, LARGE
    variant = size.defaultVariant,        // Q4 or Q4E; default Q4E for large/medium, Q4 for small/nano
    sensitivity = 0.0,                    // > 0 masks more, < 0 masks less (about -1..1)
    labels = PiiLabels.ALL,
    regex = false,                        // also mask a few patterns (email, URL, card/account, phone, Korean resident
                                          // number); default false (model only): almost no extra coverage, double the over-masking
    source = PiiModelSource.Assets(),     // or PiiModelSource.Download(...)
    keepEmoji = false,                    // true: the model also reads emoji (1.0 behaviour); default: never read or masked
    wholeWords = true,                    // true: a span that ends inside a word covers the whole word
    strictShortNames = false,             // true: short texts (≤ 8 tokens) need stronger evidence to be read as names
    allow = emptyList<PiiAllowEntry>(),   // values that are not PII in your data (see Lists); since 1.4.0
    block = emptyList<PiiBlockEntry>(),   // values that are PII in your data (see Lists); since 1.4.0
    listsId = null,                       // your name for the lists, shown in info().lists.id; since 1.4.0
)
PiiRedactor.create(modelFile, tokenizerFile, size, ...)   // files on disk; fromBytes(model, tokenizer, size, ...) for bytes

redactor.detect(text)                     // List<PiiSpan>, sorted, non-overlapping
redactor.redact(text, RedactMode.TAG)     // PiiRedaction(text, spans); TAG → [EMAIL], MASK → ***** (maskChar = "•"),
                                          // BLANK → removed; redact(text) { span, original -> "..." } for your own
redactor.detectTable(text)                // table mode (see Tables)
redactor.redactTable(text)                // PiiRedaction(text, spans)
redactor.sensitivity = 0.3                // changes the threshold without reloading
redactor.info()                           // packageVersion, modelVersion, size, variant, model and tokenizer pins, shape, lists
redactor.close()                          // frees the model and the lists (AutoCloseable: use { })
PiiRedactor.applyRedaction(text, spans, RedactMode.TAG)
PiiRegex.spans(text)                      // the pattern layer on its own
```

`PiiSpan` has `start` and `end` (UTF-16 indices into the text, as `String.substring` takes them and as the npm package
returns them), `label` and `source` (`MODEL`, `REGEX`, `MODEL_AND_REGEX`, `TABLE`, `BLOCK`). Spans are widened to whole grapheme
clusters, so an emoji, flag or combining sequence is masked whole or kept whole. `MASK` writes one mask character per code
point, like the npm package. By default emoji are taken out of the text the model reads, so the model does not read them as PII (offsets
still refer to the input; an emoji inside a span splits it); `keepEmoji = true` gives the model the text as given (1.0
behaviour). By default (`wholeWords`, since 1.3.0), a span that starts or ends inside a word is widened to the whole word ("R23" → `[ID_NUMBER]`, not `[ID_NUMBER]23`); letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, …) never extend a span, and a word ends where Hangul meets digits or Latin letters (「010-1234-5678입니다」 → `[PHONE]입니다`), and Korean particles after a Hangul span are left as they are (「홍길동님께」 → `[PERSON]님께`). It changes where a span ends, not what is masked: a short value the model misreads as a name, such as 「경기도」, becomes `[PERSON]` rather than `[PERSON]기도`; `strictShortNames` leaves such values alone. `wholeWords = false` gives the 1.2 spans. With `strictShortNames = true`, a text the model reads as at most 8 tokens (a survey answer, a table cell) needs stronger evidence to be read as a person's name: 「경기」, 「남성」 and 「네이버」 are left alone, while 「김민지」 and "Maria Lopez" are still found. With large, short survey items without personal information are masked 4.3% of the time instead of 7.9%, and 96.5% of lone names are found instead of 98.9%. Longer texts are unchanged. `redactor.version` is the model release (1.1.0); `info().packageVersion`
is the package version.

**Threads.** `PiiRedactor` is thread-safe: any number of threads or coroutines may call `detect` and `redact` on one
instance at once (each call has its own scratch memory). They are blocking CPU work: call them from
`Dispatchers.Default` or a background thread, not the main thread. `close()` waits for running calls. Speed per text on
one core: about 10 ms (nano) to 15 ms (medium) for a short text and 40 to 60 ms for a paragraph of 130 tokens on Apple M5
(the C core's numbers; not yet measured on Android phones, which are slower).

**R8 / ProGuard.** Nothing to add: the AAR ships its consumer rule, which keeps the JNI class
`ai.newmean.pii.internal.NativeBridge`.

## Lists

Since 1.4.0 you can tell the redactor what you already know about your data:

```kotlin
import java.text.Normalizer

val redactor = PiiRedactor.create(
    context,
    allow = listOf(PiiAllowEntry("제주"), PiiAllowEntry("배민"), PiiAllowEntry("경기", PiiLabels.PERSON)),  // no label: any label
    block = listOf(PiiBlockEntry("박지훈", PiiLabels.PERSON), PiiBlockEntry("새솔고등학교", PiiLabels.ORG)),
    listsId = "survey-2026-11 v3",
)
redactor.redact(Normalizer.normalize("제주, 박지훈", Normalizer.Form.NFC)).text  // "제주, [PERSON]"
// without the lists, the medium model gives "[PERSON], [PERSON]"
```

- `block`: every occurrence of an entry that starts and ends on a word boundary is masked with the entry's label (source
  `BLOCK`), also where the model finds nothing and whatever `labels` enables. A Hangul entry also matches before Korean
  particles (「박지훈님께」 → 「[PERSON]님께」); "Kim" does not match inside "Kimberly", nor in "Kim's" or "Kim-Lee"
  (apostrophes and hyphens join words, as in `wholeWords`); in scripts written without spaces any occurrence matches. A
  region with a block match takes the block label and is never allowed.
- `allow`: a span whose whole text is an entry is not masked; with `label`, only spans of that label family (PERSON covers
  PERSON_L2, URL covers URL_L2). The test is on the whole span, so it depends on where the model ends a span: a span
  「제주도」 is not allowed by "제주"; list both forms when both occur.
- Matching folds ASCII letters only (`KIM` = `kim`); nothing else is normalized. Pass NFC text and entries: files exported on
  macOS or copied from PDFs may hold NFD Hangul, which never matches
  (`java.text.Normalizer.normalize(s, Normalizer.Form.NFC)`).
- Exact forms only: 「박 지훈」 and 「박지훈」 are separate entries. A one-syllable entry such as 「김」 also masks every
  word that is 김 plus particles (「김은」, 「김도」).
- In table mode, block entries match inside each cell and do not vote; allowed spans leave before the column vote.
- Up to 10,000 entries per list, 1 to 64 code points each, no line breaks, no leading or trailing white space. Invalid lists
  throw `PiiListException`, an `IllegalArgumentException` (`list`: "allow", "block" or "meta" for `listsId`; `index`, null
  for `listsId`; `reason`, a `PiiListReason` whose `id` is `bad_shape`, `too_long`, `bad_text`, `empty`, `edge_space`,
  `unknown_label`, `too_many`, `label_conflict`, `in_both` or `bad_lists_id`), from `create`, `createBlocking` and
  `fromBytes` alike, before any model file is read. The exception never holds an entry's text.
- `info().lists` (`PiiListsInfo`: `allow`, `block`, `id`) gives the entry counts after merging duplicates and your
  `listsId`, for audit records; nothing derived from the entries' text. `close()` frees the lists with the model.

## Tables

```kotlin
redactor.detectTable(text: String, options: TableOptions = TableOptions()): List<PiiSpan>
redactor.redactTable(text: String, options: TableOptions = TableOptions(), mode: RedactMode = RedactMode.TAG,
                     maskChar: String = "*"): PiiRedaction
redactor.redactTable(text: String, options: TableOptions, replace: (span: PiiSpan, original: String) -> String): PiiRedaction
TableOptions(delimiter = TableDelimiter.AUTO, header = true, vote = true,   // AUTO, COMMA, TAB, SEMICOLON, MARKDOWN
             context = TableContext.NONE)                                  // NONE or HEADER
```

Table mode reads a CSV, TSV, semicolon-separated or markdown pipe table as one text and clips every span to a cell value:
delimiters, quotes and line breaks are never masked, so the output is still a valid table. `AUTO` detects the delimiter;
quoted fields follow RFC 4180. With `vote`, a column of short values is masked whole when the model finds names, emails,
phone numbers or usernames in at least 80% of its non-empty cells (and at least 4; columns with fewer than 5 non-empty cells
do not vote); those spans have source `TABLE`. Free-text columns (median value over 40 characters or 4 words) do not vote,
placeholders (`N/A`, `-`, `none`, `null`, `없음`) are skipped, an email column needs `@` and a phone column 6 or more digits.
With `header`, the first row is masked where the model finds PII but never voted on. With `context = TableContext.HEADER`
and a header row, each non-empty data cell of a column whose non-empty values are at least 80% distinct is also read on its
own as "<header>: <value>" and those spans are added: fewer misses on name, contact and ID columns, more PII-free cells
masked, one more model call per such cell. Text that is not a table gives exactly the spans of `detect`. A custom `replace`
must not insert the delimiter, a quote or a line break. Blocking, like `detect`.

```kotlin
val csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free"
redactor.redactTable(csv).text
// "name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free"
redactor.redactTable(tsv, TableOptions(delimiter = TableDelimiter.TAB, vote = false), RedactMode.MASK)
```

On survey and form exports, table mode misses many names split into first and last name columns.
`context = TableContext.HEADER` cuts the misses to about a quarter, at 2.3 to 3.7 more points of PII-free cells masked; for
recall first, use it. Measurements: [repository README](https://github.com/newmean-ai/pii#tables-csv-tsv).

## Development

```bash
cd android
./gradlew :pii:assembleRelease          # AAR with the three ABIs (NDK 29.0.14206865, CMake 3.22.1)
./gradlew :pii:testDebugUnitTest        # JVM tests; the JNI library is also built for the host (macOS, Linux)
./gradlew :pii:publishReleasePublicationToLocalRepository   # AAR, POM, sources and javadoc in pii/build/repo
```

The JNI bridge (`pii/src/main/cpp/pii_jni.c`) and its `CMakeLists.txt` compile `native/core` in place
(`add_subdirectory`); nothing is copied. `armeabi-v7a` uses the core's scalar kernel, because 32-bit NEON flushes
denormals to zero and could change the last bits. For unit tests, the Gradle task `buildHostJni` builds the same
`CMakeLists.txt` for the host with `cmake` and the system C compiler, so the real model runs on the JVM without an
emulator (`-Ppii.hostJni=false` skips it; the native tests then skip). Text crosses JNI as standard UTF-8 bytes (lone
surrogates in their 3-byte form), never through `GetStringUTFChars`.

The parity tests read the private fixtures and model copies in place (`PII_FIXTURES`, default `js/test/fixtures`;
`PII_MODELS`, default `js/test/models` with folders `pii-<size>` in the Hugging Face layout) and skip without them.
`PII_SIZES=nano,medium` limits the sizes; `PII_DOWNLOAD=1` adds a real download of nano (3.4 MB) from Hugging Face.

| Test | Checks |
|---|---|
| tokenizer | ids and offsets vs Hugging Face tokenizers on `tokenize.jsonl` (3,273 rows; ids all, offsets all but 3 rows at most, as the npm test) |
| prediction | spans of `detect()` vs the Python evaluator on every run of `js/test/predict.test.ts` (412 rows each, ≥ 99%) |
| npm | spans, labels and sources equal to the npm package's `detect()` (with `regex`, `labels`, `sensitivity`): `PII_NPM_SPANS` from `scripts/npm_spans.mjs` |
| regex | `PiiRegex` vs the Python baseline on `regex.jsonl` (3,273 rows); JavaScript `\s` and Unicode digit cases |
| redaction, offsets | the three modes and custom replacement; emoji, ZWJ sequences, flags, combining marks, lone surrogates, NUL |
| options, threads, files | labels, sensitivity, regex merging; 4 threads on one instance; pins vs `js/src/models.ts` and the core; damaged files, download and reuse |
| API | the calls of this README from Kotlin (coroutines) and Java |

```bash
node scripts/npm_spans.mjs pii/build/npm.jsonl nano q4 0 1 all ../js/test/fixtures/predict-nano-q4.jsonl   # needs js/dist
PII_NPM_SPANS=$PWD/pii/build/npm.jsonl ./gradlew :pii:testDebugUnitTest --tests '*NpmParityTest*'
```

Releases go to Maven Central from an `android-v<version>` tag (`.github/workflows/android-publish.yml`): the workflow
signs every file when `SIGNING_KEY` and `SIGNING_PASSWORD` are set (local builds stay unsigned), uploads the bundle to the
Central Portal and waits for validation; the maintainer then publishes it there.

## License

[Apache-2.0](LICENSE), for the library code and the model weights; third-party notices in [NOTICE](NOTICE) (both also inside the AAR, `META-INF/ai.newmean.pii/`). If you use
them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a
request, not a license condition). Made by [newmean.ai](https://newmean.ai).
