English | [한국어](README.ko.md)

# newmean_pii

Detect and mask personal information in text **on the device**, in Flutter and Dart apps. A small multilingual
token-classification model (54 languages) runs in a C engine through `dart:ffi`; nothing is sent anywhere. Same models,
options and spans as the npm package [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii).

```yaml
dependencies:
  newmean_pii: ^1.0.0
```

```dart
import 'package:newmean_pii/newmean_pii.dart';

final pii = await NewmeanPii.create(size: 'medium');
final r = await pii.redact('제 메일은 kim@example.com, 전화는 010-1234-5678입니다');
// r.text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

Android (arm64-v8a, armeabi-v7a, x86_64), iOS (device and simulator), macOS, Linux and Windows. Needs Flutter 3.38 or Dart
3.10 or later: the package's build hook compiles the engine for each target with the platform's C compiler (Xcode, the
Android NDK, clang or gcc, MSVC), so there is no prebuilt binary and no plugin setup. For the web, use the npm package.

## Models

| size | file | notes |
|---|---|---|
| `nano` | 2.6 MB | one shared layer applied 4 times, rank-32 embeddings |
| `small` | 7.1 MB | 4 layers, rank-64 embeddings |
| `medium` (default) | 11.3 MB | 6 layers, rank-192 embeddings |
| `large` | 15.5 MB | 6 layers, full (pruned-vocabulary) embeddings |

Plus a 0.8 MB tokenizer shared by all sizes. Every file is checked against the size and SHA-256 pinned in the package
before use, wherever it comes from:

- **Downloaded once** (default) from Hugging Face (`newmean-ai/pii-<size>`, tag `v1.0.0`) into `cacheDir`. The default is
  `~/Library/Caches/newmean-pii` on iOS and macOS, `~/.cache/newmean-pii` on Linux, `%LOCALAPPDATA%\newmean-pii` on
  Windows and the app's temporary directory on Android; pass `cacheDir` (for example `getApplicationSupportDirectory()`
  of `path_provider`) to keep the files elsewhere. `modelUrl` points at a self-hosted copy of the model folder. Android
  apps need the `INTERNET` permission for the download.
- **Bundled** as Flutter assets, read with `readFile`:

  ```yaml
  flutter:
    assets:
      - assets/pii-nano/tokenizer.web.json
      - assets/pii-nano/onnx/model_q4.onnx
  ```

  ```dart
  import 'package:flutter/services.dart' show rootBundle;

  final pii = await NewmeanPii.create(
    size: 'nano',
    readFile: (path) async => (await rootBundle.load('assets/pii-nano/$path')).buffer.asUint8List(),
  );
  ```

- **A local folder** with the Hugging Face layout (`modelDir`), or bytes you already hold (`NewmeanPii.fromBytes`).

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee.

## Labels

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, and two
level 2 labels for indirect identifiers: `PERSON_L2` (a surname with a title, a nickname, initials) and `URL_L2` (public
profile or page URLs). Pass `labels: level1Labels` to leave level 2 visible.

## API

```dart
NewmeanPii.create({
  String size = 'medium',      // nano, small, medium, large
  String? variant,             // 'q4' or 'q4e'; default q4e for large/medium, q4 for small/nano
  double sensitivity = 0,      // > 0 masks more, < 0 masks less (about −1..1)
  Iterable<String>? labels,    // labels to mask; default all (piiLabels)
  bool regex = false,          // also mask a few patterns (email, URL, card/account, phone, Korean resident number);
                               // default false (model only): almost no extra coverage, double the over-masking
  String? modelDir,            // local folder: tokenizer.web.json, onnx/model_<variant>.onnx
  Future<Uint8List> Function(String path)? readFile,  // e.g. Flutter assets
  Uri? modelUrl,               // self-hosted model folder (ending with "/"); default Hugging Face
  String? cacheDir,            // where downloads are kept
  bool cache = true,           // keep downloaded files
  bool verify = true,          // check size and SHA-256 of every file
  void Function(PiiProgress)? onProgress,
}) → Future<NewmeanPii>
NewmeanPii.fromBytes(onnx: ..., tokenizer: ..., size: ..., variant: ...) → Future<NewmeanPii>

pii.detect(text)              // → Future<List<PiiSpan>> {start, end, label, source}, sorted, non-overlapping
pii.redact(text, mode: RedactMode.tag)  // tag → [EMAIL], mask → *****, blank → removed; or replace: (span, original) => ...
                                        // → Future<PiiRedaction> {text, spans}
pii.detectSync(text), pii.redactSync(text)  // the same on the calling isolate
pii.sensitivity = 0.3          // or setSensitivity(0.3); no reload
pii.dispose()

regexSpans(text), applyRedaction(text, spans, mode: ...)  // the pattern layer and the redaction modes on their own
modelFile(size, file)          // published path, size and SHA-256 of 'q4', 'q4e' or 'tokenizer'
```

- **Offsets** are UTF-16 code units, the indices of Dart's `String`: `text.substring(span.start, span.end)`. Emoji, flags and
  other characters outside the BMP never get split. `mask` writes one mask character per code point.
- **Isolates**: `detect` and `redact` run the model in a background isolate (`Isolate.run`), so the UI never blocks; the
  overhead is about 0.5 ms per call. One loaded model is shared by all calls and calls may overlap: several `detect`
  calls at once run in parallel on different cores. Inside your own background isolate, `detectSync` avoids the hop.
- **Memory**: a loaded model takes about the size of its file plus the tokenizer; `dispose()` frees it after running
  calls finish (otherwise it is freed when the object is garbage collected).

## Results

Through this package, spans equal the Python evaluator's on the same model files (412 validation texts per run, the
parity test of the npm package): 412/412 for large, medium and small (q4 and q4e), nano q4e and medium q4e at
sensitivity 0.5; 410/412 for nano q4 at sensitivity 0 and −1. In the two differing rows the evaluator reports two
overlapping spans with different labels, which `detect` merges into one region, as the npm package does. On 151
synthetic texts with emoji, flags, combining marks, CRLF, NUL and lone surrogates mixed in (nano and medium, with and
without `regex`), spans, sources and all three redaction modes equal the npm package's in all 604 cases.

Speed on Apple M5 Pro (macOS), one call at a time, a one-sentence text: nano about 11 ms, medium about 16 ms per `detect`
(the background isolate adds about 0.5 ms). Loading from a folder, SHA-256 checks included: nano 40 ms, medium 110 ms,
large 145 ms. Phones are slower; see the technical report for phone measurements of the web package.

Accuracy across 54 languages and against other detectors: the
[repository README](https://github.com/newmean-ai/pii#results) and the technical report; weekly scores on the
[leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

## How it works

The package compiles `native/core` of the repository: one C library shared by the Python, Android, iOS and Flutter
packages. It reads the published ONNX files directly (no ONNX Runtime), tokenizes like the npm package (token ids and
offsets equal), and its logits are bit-identical to the npm package's WebAssembly engine. The pattern layer (`regexSpans`, a port of the npm
package's `regex.ts`) and the redaction modes are Dart; pattern spans are merged with the model's in the C core, by the
same rule as the npm package's `combine`. Builds never fuse multiply and add, so results are the same on every CPU; 32-bit
ARM uses the engine's scalar path for the same reason.

## Development

```bash
cd flutter
dart pub get
dart analyze
dart test                       # builds the C core for the host, then runs the tests
cd example && flutter build apk --debug && flutter build ios --simulator --no-codesign
```

| Test | Checks |
|---|---|
| `predict_test.dart` | spans through the Dart API vs the Python evaluator, every run of the npm parity test (≥ 99% of rows) |
| `regex_test.dart` | `regexSpans` vs the Python baseline on `regex.jsonl` (3,273 rows); emoji offsets; Unicode digits and boundaries |
| `api_test.dart` | UTF-16 offsets around emoji, astral characters, combining marks, RTL, NUL and lone surrogates; redaction modes; labels; sensitivity; regex merging; concurrent and sync calls; long texts; pins; dispose |
| `loader_test.dart` | download from a local server, SHA-256 refusal, cache reuse and repair; `PII_DOWNLOAD=1` downloads nano from Hugging Face |
| `redaction_test.dart` | `applyRedaction` cases of the npm package |

The fixtures and model copies are private (validation text) and are read in place, never copied: `PII_FIXTURES`
(default `../js/test/fixtures`) and `PII_MODELS` (default `../js/test/models`, folders `pii-<size>/`). Tests that need them
skip when they are absent. `PII_SIZES=nano,medium` limits the parity runs; `PII_CONCURRENCY` (default 4) sets how many
calls run at once.

**Publishing.** The build hook compiles `../native/core` when it exists (the repository) and `third_party/native_core`
otherwise (the package from pub.dev). `dart run tool/vendor_core.dart` makes that copy before `dart pub publish` (it is
ignored by git and published through `.pubignore`); `--clean` removes it. Releases are published from a `flutter-v<version>` tag by
`.github/workflows/flutter-publish.yml` (pub.dev automated publishing, after the maintainer's approval).

## License

[Apache-2.0](LICENSE), for the package code and the model weights; third-party notices in [NOTICE](NOTICE). If you use them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a request, not a license condition). Made by [newmean.ai](https://newmean.ai).
