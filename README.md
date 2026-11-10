English | [한국어](README.ko.md) | [繁體中文](README.zh-Hant.md)

# @newmean-ai/pii

**Browser-side PII redaction.** Detect and mask personal information (names, emails, phone numbers, addresses, IDs, …) in text **entirely in the browser**, so the original text never leaves the user's device.

- 54 languages, 11 labels plus 2 level-2 labels for indirect identifiers (IP addresses are learned as `ID_NUMBER`); an optional
  pattern layer (e-mail, URL, card/account, phone, Korean resident number) is off by default
- Four model sizes: **nano 2.6 MB**, **small 7.1 MB**, **medium 11.3 MB** (default), **large 15.5 MB**
- Runs in WebAssembly: the SDK's own built-in engine (default, about 13 KB) or onnxruntime-web
- Free for any use, including commercial use
- The same models for Python, iOS, Android and Flutter ([Install](#install))

```bash
npm install @newmean-ai/pii
```

```ts
import { createRedactor } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium" });
const { text, spans } = await redactor.redact("My email is kim@example.com");
// text: "My email is [EMAIL]"
```

See [js/README.md](js/README.md) for the full API.

## Install

| Platform | Package | Install |
|---|---|---|
| Browser, Node.js | [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii) | `npm install @newmean-ai/pii` |
| Python 3.9+ | [`newmean-pii`](https://pypi.org/project/newmean-pii/) | `pip install newmean-pii` |
| iOS 15+, macOS 12+ | `NewmeanPII` (Swift Package Manager) | add `https://github.com/newmean-ai/pii`, from `1.5.0` |
| Android 7.0+ | [`ai.newmean:pii`](https://central.sonatype.com/artifact/ai.newmean/pii) | `implementation("ai.newmean:pii:1.5.0")` |
| Flutter, Dart | [`newmean_pii`](https://pub.dev/packages/newmean_pii) | `flutter pub add newmean_pii` |

The Python, iOS, Android and Flutter packages run the same models on the device through one C engine
([`native/core`](native/core)), whose token ids, offsets and logits equal the npm package's. Model files are downloaded once
from Hugging Face and checked against pinned SHA-256 values, or bundled with the app.

```python
from newmean_pii import create_redactor

create_redactor(size="medium").redact("My email is kim@example.com").text  # 'My email is [EMAIL]'
```

```swift
import NewmeanPII

let (text, _) = try await PIIRedactor(size: .medium).redact("My email is kim@example.com")
```

```kotlin
import ai.newmean.pii.PiiRedactor

val (text, spans) = PiiRedactor.create(context).redact("My email is kim@example.com")  // create: suspend, model from the app's assets
```

```dart
import 'package:newmean_pii/newmean_pii.dart';

final r = await (await NewmeanPii.create(size: 'medium')).redact('My email is kim@example.com');
```

Guides: [Python](python/README.md) · [Swift](swift/README.md) · [Android](android/README.md) · [Flutter](flutter/README.md)

### Tables (CSV, TSV)

```ts
const { text } = await redactor.redactTable(csv);  // delimiter detected: ",", "\t", ";" or a markdown pipe table
const spans = await redactor.detectTable(tsv, { delimiter: "\t", header: true, vote: true });
const named = await redactor.redactTable(csv, { context: "header" });  // also read each cell after its column title
```

```python
create_redactor().redact_table(open("survey.csv", encoding="utf-8").read(), context="header").text
```

Table mode reads the whole table as one text, so cells give each other context, and clips every span to a cell value:
delimiters, quotes and line breaks are never masked, so the output is still a valid table. A column of short values that the
model reads as names, emails, phone numbers or usernames in at least 80% of its cells is masked whole (source `"table"`);
the header row and free-text columns never take part in that vote. With `context: "header"`, each cell of a column of
mostly distinct values is also read on its own after its column title (`"<header>: <value>"`), which finds names split into
first and last name columns: on 556 survey and form exports in 54 languages, large then misses 1.4% of PII characters
instead of 6.2% and masks 12.3% of PII-free cells instead of 9.6% (all sizes under [Results](#results)). It costs one more
model call per such cell (about twice the time of table mode). Every package has table mode (Swift, Kotlin and Dart: `detectTable`,
`redactTable`; C: `pii_detect_table`).

## Links

- Models on Hugging Face: [newmean-pii-nano](https://huggingface.co/newmean-ai/pii-nano) ·
  [newmean-pii-small](https://huggingface.co/newmean-ai/pii-small) · [newmean-pii-medium](https://huggingface.co/newmean-ai/pii-medium) ·
  [newmean-pii-large](https://huggingface.co/newmean-ai/pii-large)
- newmean-pii-dataset (public test): [newmean-ai/pii-dataset](https://huggingface.co/datasets/newmean-ai/pii-dataset)
- Demo: [pii.newmean.ai](https://pii.newmean.ai)
- Leaderboard: [newmean-ai/pii-leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)
- Technical report: [docs/report/REPORT.md](docs/report/REPORT.md)

## Results

Share of PII characters left unmasked (miss %, lower is better), model only, on newmean-pii-dataset (public test) and four external
datasets none of our models was trained on. Full table with over-masking, parameters and speed: [technical report, Section 6](docs/report/REPORT.md);
weekly scores on newmean-pii-dataset (public and private test): [leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

| model | download | public test (54 lang.) | KDPII (ko) | OpenPII (30 lang.) | Nemotron-PII (en) | Gretel finance |
|---|---|---|---|---|---|---|
| **newmean-pii-large** | 15.5 MB | **1.6** | 9.6 | 4.9 | **6.1** | 21.4 |
| **newmean-pii-medium** | 11.3 MB | 1.9 | 11.5 | 6.3 | 7.7 | 21.5 |
| **newmean-pii-small** | 7.1 MB | 2.4 | 10.0 | 5.6 | 8.4 | 22.3 |
| **newmean-pii-nano** | 2.6 MB | 2.9 | 9.5 | 5.0 | 9.9 | 21.4 |
| gliner-pii-edge | 176 MB | 19.6 | 11.9 | 5.7 | 17.3 | **15.7** |
| OpenAI privacy-filter | 2.8 GB | 34.6 | 34.8 | 14.3 | 25.7 | 32.2 |
| Horizon pii-redactor-small | 563 MB | 41.0 | 25.7 | 11.1 | 7.8 | 31.6 |
| KoELECTRA Korean PII NER | — | 43.1 | **8.8** | 24.1* | 45.9 | 37.6 |
| GLiNER2-PII | 1.2 GB | 44.9 | 34.2 | 23.2 | 46.3 | 32.9 |
| Rampart | 15 MB | 56.5 | 36.1 | **3.5*** | 51.6 | 40.5 |
| Mozilla smart-pii-redactor | 15 MB | 56.9 | 46.0 | 25.9 | 48.8 | 32.9 |
| Rampart-edge | 1.5 MB | 74.7 | 89.0 | 46.1 | 55.5 | 48.0 |
| regex baseline | — | 75.3 | 62.0 | 61.1 | 44.6 | 86.7 |

How to read it: our models were trained on data of the same design as newmean-pii-dataset (public test), while the other detectors see it
for the first time and some follow other labelling policies (for example, leaving organisations unmasked), so that column shows
how each serves this masking policy; the four external columns are the fairer comparison. \* Trained on OpenPII data (Rampart)
or on data that overlaps it (KoELECTRA). GLiNER2-PII ran on Gretel in its library's long-text mode. On phones (mean of four)
the 11.3 MB model takes about 26 ms for a 40-token message. With SDK 1.2 (the Python package as shipped) every number of our
models in this table stays within 0.04 points, because the SDK changes only texts with emoji or keycaps.

What 1.2 changes. Release 1.2 is SDK 1.2.0 on the v1.1 models; v1.1 was not released on its own, so its model change ships
here. *Models:* lone everyday words in short texts, table cells and survey exports are read as names far less often. On
20,691 short items in 54 languages (column titles, answer options, single words, names and values written alone), the
PII-free items masked fall from 30.6% to 7.9% (large; medium 30.6% to 7.8%, small 36.8% to 9.4%, nano 39.3% to 11.1%), and
checked cell by cell, large masks 6.6% of the PII-free cells of 228 survey CSV files instead of 24.9%. Trade-off: on KDPII
large misses 9.6% (v1.0: 7.6%), medium 11.5% (9.8%), small 10.0% (9.5%); nano is unchanged at 9.5%. *SDK:* emoji are taken
out of the text the model reads, so the model no longer reads them as names (`keepEmoji: true` turns this off; text without
emoji or keycaps gives exactly the spans of 1.1); a keycap such as 3️⃣ is read as its bare character, so numbers written
with keycaps are still found; every span is widened to whole grapheme clusters, so an emoji, flag or combining sequence is
masked whole or kept whole (the npm package needs `Intl.Segmenter` for this); and table mode with the `context: "header"`
option (above). The short-text, emoji and table sets are private and synthetic.

Emoji, model only and model + SDK 1.2: share of 4,501 texts (one to three emoji placed outside the PII) in which an emoji
is masked. No emoji is cut in half; what is left is mostly a keycap next to a name, masked with it because keycaps are read
as digits. PII characters missed on these texts also fall (large 0.87% to 0.31%, medium 1.03% to 0.61%). Release 1.0
masks an emoji in 7.42% (large), 9.46% (medium), 16.69% (small) and 21.88% (nano) of these texts and cuts one in half in
1.9% to 7.6%.

| model | model only | model + SDK 1.2 |
|---|---|---|
| newmean-pii-large | 23.95% | 0.87% |
| newmean-pii-medium | 14.89% | 0.78% |
| newmean-pii-small | 20.57% | 0.89% |
| newmean-pii-nano | 26.33% | 1.00% |

Survey and form exports, miss % / PII-free data cells masked %: 556 files (121,272 cells, 54 languages) written by Claude
Opus 5.5, which wrote none of our training data; the set was built after the SDK options were fixed and scored once.

| model | whole file, model only | table mode (default) | table mode + `context: "header"` | `detect` on each cell |
|---|---|---|---|---|
| newmean-pii-large | 6.56 / 9.58 | 6.17 / 9.59 | **1.44** / 12.30 | 5.00 / 12.21 |
| newmean-pii-medium | 8.29 / 9.30 | 7.93 / 9.32 | **1.93** / 11.66 | 5.75 / 12.03 |
| newmean-pii-small | 7.00 / 15.87 | 6.65 / 15.90 | **1.72** / 19.03 | 6.21 / 14.21 |
| newmean-pii-nano | 6.47 / 23.62 | 6.13 / 23.66 | **1.58** / 27.34 | 6.31 / 17.74 |

`context: "header"` cuts the misses to about a quarter for every size, at 2.3 to 3.7 more points of PII-free cells masked.
Table mode alone misses about as much as reading the whole file, but keeps the table valid and masks whole contact columns.
small and nano mask many PII-free cells in every mode (16% to 27%). Release 1.0 (v1.0 models, no table mode) reads these
files whole and misses more: large 7.52 / 11.30, medium 9.50 / 9.37, small 9.80 / 13.43, nano 8.04 / 24.36. Two more sets,
on which the options were chosen, and the full numbers: [technical report, Section 6.2](docs/report/REPORT.md).

What 1.3 adds. SDK 1.3.0, same v1.1 models; two options: `wholeWords` on by default (`false` gives the 1.2 spans; the
numbers above are without it), `strictShortNames` off.
`strictShortNames`: a text the model reads as at most 8 tokens (a survey answer, a table cell) needs stronger evidence to be
read as a name, so 「경기」, 「남성」 or 「네이버」 alone are left unmasked. Short PII-free items masked fall from 7.9% to 4.3%
(large; medium 7.8% to 4.5%, small 9.4% to 4.7%, nano 11.1% to 5.9%), and lone names found fall from 98.9% to 96.5% (medium
97.5% to 95.4%, small 97.0% to 93.5%, nano 96.7% to 94.4%); longer texts are unchanged (public test and KDPII within 0.07
points). `wholeWords`: a span that ends inside a word covers the whole word ("R23" → `[ID_NUMBER]`, not `[ID_NUMBER]23`; a word ends
where Hangul meets digits or Latin letters, so 「010-1234-5678입니다」 → `[PHONE]입니다`; Korean particles after a Hangul span stay, 「홍길동님께」 → `[PERSON]님께`),
which lowers misses on every set by 0.1 to 1.4 points (large: public test 1.63% to 1.54%, KDPII 9.61% to 9.08%) and masks
a little more (large over-masking: public test 0.47% to 0.53%, KDPII 1.70% to 2.09%). Every package also gains `info` with the package and model versions and the checked model files.

What 1.4 adds. SDK 1.4.0, same v1.1 models; user lists in every package. `block`: values you know are PII (an
instructor's name) are masked wherever they start and end on a word boundary, also before Korean particles
(「박지훈님께」 → `[PERSON]님께`) and where the model misses them (source `block`). `allow`: a span whose whole text is a
value you know is not PII (a region or brand in a survey) is not masked. ASCII letters are compared case-insensitively,
nothing else is normalized (pass NFC text). Lists are checked before any model file is loaded, errors name the list,
entry and reason but never the text, and `info` gives the entry counts and your `listsId`. On the KDPII dev split (large), a block list holding every name of the set, an upper bound,
cuts PERSON characters missed from 24.6% to 3.1% at 0.4 points more over-masking; a generic 136-entry allow list changes
little, as expected (allow is for the values of your own columns). Details:
[js/README.md](js/README.md#lists). Using the C core directly (not through a package): the C API is source-compatible
only, so rebuild against each release's `pii.h`; options structs from another release are rejected
([native/core](native/core/README.md#api)).

What 1.4.1 fixes. SDK 1.4.1, same v1.1 models; bug and doc fixes only, and every valid input gives the 1.4.0 spans. A
`sensitivity` that is NaN or infinite is now an error in every package and in the C core (Android already refused it);
before, NaN silently turned the model off and an infinity masked everything or nothing. The npm package also loads with
`require()` on Node versions that can require ES modules (20.19+, 22.12+), and its `info.verified` is true only when the
SHA-256 of both model files was actually checked (it used to copy the `verify` option). The Flutter package checks its
native core at load, before any file is read, as its 1.4.0 notes said. Smaller fixes: npm `detectTable` checks the
delimiter before running the model, the Flutter download names the package version in its User-Agent, and a few README
passages were corrected (built-in engine size, the lists example, label names that are not labels).

What 1.5 changes. SDK 1.5.0, same v1.1 models; speed only. On arm64 (Apple silicon, iPhone, 64-bit Android) the native core
of the Python, Swift, Android and Flutter packages now fuses multiply and add in its matrix kernel and works on 16 output
columns at a time, like the npm package's relaxed-SIMD build: about 1.5 times as fast (medium on Apple M5 Pro, one thread:
a short text 14.9 → 10.2 ms, 1,000 tokens 550 → 349 ms). Logits differ from 1.4.1 in the last bits, so a span can differ in
rare cases: on 18,692 development texts and the four sizes, 1 of 74,768 results did. Results on arm64 now equal the npm
package's relaxed-SIMD build (Chrome, Edge, Firefox) and are no longer bit-identical to x86-64, where nothing changes;
building the core with `PII_NO_FMA` keeps the 1.4 kernel ([native/core](native/core/README.md#build)). The npm package
gives the 1.4.1 spans.

## Repository layout

| folder | contents |
|---|---|
| [`js/`](js) | the SDK, `@newmean-ai/pii`, with its test page, the public demo (`playground/`) and the phone benchmark (`experiments/`) |
| [`native/core/`](native/core) | the C engine shared by the Python, iOS, Android and Flutter packages |
| [`python/`](python) | the Python package `newmean-pii` |
| [`swift/`](swift), `Package.swift` | the Swift package `NewmeanPII` (iOS, macOS) |
| [`android/`](android) | the Android library `ai.newmean:pii` |
| [`flutter/`](flutter) | the Flutter and Dart package `newmean_pii` |
| [`docs/report/`](docs/report) | the technical report and its two appendices (also in Korean) |
| [`leaderboard/`](leaderboard) | code of the leaderboard Space on Hugging Face |
| [`site/`](site) | the Worker that serves the demo and the phone benchmark at pii.newmean.ai |
| [`tools/data-request/`](tools/data-request) | how to request the training data (research free, commercial by inquiry) |
| [`training/`](training) | training, export and evaluation code (the data generator is not included) |
| [`release/`](release) | newmean-pii-dataset (public test) card |

## License

- SDK code and model weights: [Apache-2.0](LICENSE).
- If you use them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a request, not a license condition).
- newmean-pii-dataset (public test): CC BY-NC 4.0 (research and evaluation; commercial use by inquiry).
- Training and validation data: on request through the [data request form](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform) (research use free; commercial use by inquiry; requests open now, data shared from December 2026); the terms come with the approval email or the contract ([tools/data-request](tools/data-request)).
- Third-party attributions: [NOTICE](NOTICE).

Made by [newmean.ai](https://newmean.ai).
