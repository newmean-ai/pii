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
    implementation("ai.newmean:pii:1.0.0")
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
(`https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/`) into the app's assets, in the layout of the repository:

```
app/src/main/assets/newmean-pii/pii-medium/tokenizer.web.json
app/src/main/assets/newmean-pii/pii-medium/onnx/model_q4e.onnx
```

```bash
cd app/src/main/assets && mkdir -p newmean-pii/pii-medium/onnx && cd newmean-pii/pii-medium
curl -LO https://huggingface.co/newmean-ai/pii-medium/resolve/v1.0.0/tokenizer.web.json
curl -L -o onnx/model_q4e.onnx https://huggingface.co/newmean-ai/pii-medium/resolve/v1.0.0/onnx/model_q4e.onnx
```

`PiiRedactor.create(context)` reads them from there (`source = PiiModelSource.Assets(dir = "models")` for another folder, `""` for the assets root).

**Downloaded once.** With `source = PiiModelSource.Download()`, the files are downloaded from Hugging Face on first use
into `cacheDir/newmean-pii/1.0.0/pii-<size>/` and checked again on every load. The system may clear the cache; files are
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
)
PiiRedactor.create(modelFile, tokenizerFile, size, ...)   // files on disk; fromBytes(model, tokenizer, size, ...) for bytes

redactor.detect(text)                     // List<PiiSpan>, sorted, non-overlapping
redactor.redact(text, RedactMode.TAG)     // PiiRedaction(text, spans); TAG → [EMAIL], MASK → ***** (maskChar = "•"),
                                          // BLANK → removed; redact(text) { span, original -> "..." } for your own
redactor.sensitivity = 0.3                // changes the threshold without reloading
redactor.close()                          // frees the model (AutoCloseable: use { })
PiiRedactor.applyRedaction(text, spans, RedactMode.TAG)
PiiRegex.spans(text)                      // the pattern layer on its own
```

`PiiSpan` has `start` and `end` (UTF-16 indices into the text, as `String.substring` takes them and as the npm package
returns them), `label` and `source` (`MODEL`, `REGEX`, `MODEL_AND_REGEX`). Spans fall on code point boundaries, which need not be
grapheme boundaries (an emoji ZWJ sequence is one grapheme of several code points). `MASK` writes one mask character per code point, like the npm package.

**Threads.** `PiiRedactor` is thread-safe: any number of threads or coroutines may call `detect` and `redact` on one
instance at once (each call has its own scratch memory). They are blocking CPU work: call them from
`Dispatchers.Default` or a background thread, not the main thread. `close()` waits for running calls. Speed per text on
one core: about 10 ms (nano) to 15 ms (medium) for a short text and 40 to 60 ms for a paragraph of 130 tokens on Apple M5
(the C core's numbers; not yet measured on Android phones, which are slower).

**R8 / ProGuard.** Nothing to add: the AAR ships its consumer rule, which keeps the JNI class
`ai.newmean.pii.internal.NativeBridge`.

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

Publishing to Maven Central (`ai.newmean` namespace, signing and upload) is done by the maintainer and is not set up in
this build.

## License

[Apache-2.0](LICENSE), for the library code and the model weights; third-party notices in [NOTICE](NOTICE) (both also inside the AAR, `META-INF/ai.newmean.pii/`). If you use
them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a
request, not a license condition). Made by [newmean.ai](https://newmean.ai).
