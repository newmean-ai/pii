English | [한국어](README.ko.md)

# @newmean-ai/pii

**Browser-side PII redaction.** Detect and mask personal information (names, emails, phone numbers, addresses, IDs, …) in text **entirely in the browser**, so the original text never leaves the user's device.

- 54 languages, 11 labels plus 2 level-2 labels for indirect identifiers (IP addresses are learned as `ID_NUMBER`); an optional
  pattern layer (e-mail, URL, card/account, phone, Korean resident number) is off by default
- Four model sizes: **nano 2.6 MB**, **small 7.1 MB**, **medium 11.3 MB** (default), **large 15.5 MB**
- Runs in WebAssembly: the SDK's own built-in engine (default, about 20 KB) or onnxruntime-web
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
| iOS 15+, macOS 12+ | `NewmeanPII` (Swift Package Manager) | add `https://github.com/newmean-ai/pii`, from `1.0.0` |
| Android 7.0+ | [`ai.newmean:pii`](https://central.sonatype.com/artifact/ai.newmean/pii) | `implementation("ai.newmean:pii:1.0.0")` |
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
| **newmean-pii-large** | 15.5 MB | **1.6** | **7.6** | 4.6 | **7.4** | 20.3 |
| **newmean-pii-medium** | 11.3 MB | 2.1 | 9.8 | 6.1 | 7.9 | 22.5 |
| **newmean-pii-small** | 7.1 MB | 2.7 | 9.5 | 5.6 | 10.6 | 24.1 |
| **newmean-pii-nano** | 2.6 MB | 3.3 | 9.5 | 4.9 | 10.6 | 22.8 |
| gliner-pii-edge | 176 MB | 19.6 | 11.9 | 5.7 | 17.3 | **15.7** |
| OpenAI privacy-filter | 2.8 GB | 34.6 | 34.8 | 14.3 | 25.7 | 32.2 |
| Horizon pii-redactor-small | 563 MB | 41.0 | 25.7 | 11.1 | 7.8 | 31.6 |
| KoELECTRA Korean PII NER | — | 43.1 | 8.8 | 24.1* | 45.9 | 37.6 |
| GLiNER2-PII | 1.2 GB | 44.9 | 34.2 | 23.2 | 46.3 | 32.9 |
| Rampart | 15 MB | 56.5 | 36.1 | **3.5*** | 51.6 | 40.5 |
| Mozilla smart-pii-redactor | 15 MB | 56.9 | 46.0 | 25.9 | 48.8 | 32.9 |
| Rampart-edge | 1.5 MB | 74.7 | 89.0 | 46.1 | 55.5 | 48.0 |
| regex baseline | — | 75.3 | 62.0 | 61.1 | 44.6 | 86.7 |

How to read it: our models were trained on data of the same design as newmean-pii-dataset (public test), while the other detectors see it
for the first time and some follow other labelling policies (for example, leaving organisations unmasked), so that column shows
how each serves this masking policy; the four external columns are the fairer comparison. \* Trained on OpenPII data (Rampart)
or on data that overlaps it (KoELECTRA). GLiNER2-PII ran on Gretel in its library's long-text mode. On phones (mean of four)
the 11.3 MB model takes about 26 ms for a 40-token message.

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
