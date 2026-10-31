English | [한국어](README.ko.md)

# @newmean-ai/pii

**Browser-side PII redaction.** Detect and mask personal information (names, emails, phone numbers, addresses, IDs, …) in text **entirely in the browser**, so the original text never leaves the user's device.

- 54 languages, 11 labels plus 2 level-2 labels for indirect identifiers (IP addresses are learned as `ID_NUMBER`); an optional
  pattern layer (e-mail, URL, card/account, phone, Korean resident number) is off by default
- Four model sizes: **nano 2.6 MB**, **small 7.1 MB**, **medium 11.3 MB** (default), **large 15.5 MB**
- Runs in WebAssembly: the SDK's own built-in engine (default, about 20 KB) or onnxruntime-web
- Free for any use, including commercial use
- Python and mobile (Android, iOS, Flutter) packages on the same models are planned

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

## Links

- Models on Hugging Face: [pii-nano](https://huggingface.co/newmean-ai/pii-nano) ·
  [pii-small](https://huggingface.co/newmean-ai/pii-small) · [pii-medium](https://huggingface.co/newmean-ai/pii-medium) ·
  [pii-large](https://huggingface.co/newmean-ai/pii-large)
- Public test dataset: [newmean-ai/pii-multilingual-test](https://huggingface.co/datasets/newmean-ai/pii-multilingual-test)
- Demo: [pii.newmean.ai](https://pii.newmean.ai)
- Leaderboard: [newmean-ai/pii-leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)
- Technical report: [docs/report/REPORT.md](docs/report/REPORT.md)

## Results

Share of PII characters left unmasked (miss %, lower is better), model only, on our public test dataset and four external
datasets none of our models was trained on. Full table with over-masking, parameters and speed: [technical report, Section 6](docs/report/REPORT.md);
weekly scores on the public and a private test set: [leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

| model | download | public test (54 lang.) | KDPII (ko) | OpenPII (30 lang.) | Nemotron-PII (en) | Gretel finance |
|---|---|---|---|---|---|---|
| **pii-large** | 15.5 MB | **1.6** | **7.6** | 4.6 | **7.4** | 20.3 |
| **pii-medium** | 11.3 MB | 2.1 | 9.8 | 6.1 | 7.9 | 22.5 |
| **pii-small** | 7.1 MB | 2.7 | 9.5 | 5.6 | 10.6 | 24.1 |
| **pii-nano** | 2.6 MB | 3.3 | 9.5 | 4.9 | 10.6 | 22.8 |
| gliner-pii-edge | 176 MB | 19.7 | 11.9 | 5.7 | 17.3 | **15.7** |
| OpenAI privacy-filter | 2.8 GB | 34.8 | 34.8 | 14.3 | 25.7 | 32.2 |
| Horizon pii-redactor-small | 563 MB | 40.5 | 25.7 | 11.1 | 7.8 | 31.6 |
| KoELECTRA Korean PII NER | — | 43.0 | 8.8 | 24.1* | 45.9 | 37.6 |
| GLiNER2-PII | 1.2 GB | 45.1 | 34.2 | 23.2 | 46.3 | 32.9 |
| Rampart | 15 MB | 54.9 | 36.1 | **3.5*** | 51.6 | 40.5 |
| Mozilla smart-pii-redactor | 15 MB | 56.5 | 46.0 | 25.9 | 48.8 | 32.9 |
| Rampart-edge | 1.5 MB | 75.3 | 89.0 | 46.1 | 55.5 | 48.0 |
| regex baseline | — | 75.9 | 62.0 | 61.1 | 44.6 | 86.7 |

How to read it: our models were trained on data of the same design as the public test dataset, while the other detectors see it
for the first time and some follow other labelling policies (for example, leaving organisations unmasked), so that column shows
how each serves this masking policy; the four external columns are the fairer comparison. \* Trained on OpenPII data (Rampart)
or on data that overlaps it (KoELECTRA). GLiNER2-PII ran on Gretel in its library's long-text mode. On phones (mean of four)
the 11.3 MB model takes about 26 ms for a 40-token message.

## Repository layout

| folder | contents |
|---|---|
| [`js/`](js) | the SDK, `@newmean-ai/pii`, with its test page, the public demo (`playground/`) and the phone benchmark (`experiments/`) |
| [`docs/report/`](docs/report) | the technical report and its two appendices (also in Korean) |
| [`leaderboard/`](leaderboard) | code of the leaderboard Space on Hugging Face |
| [`site/`](site) | the Worker that serves the demo and the phone benchmark at pii.newmean.ai |
| [`tools/data-request/`](tools/data-request) | how to request the training data (research free, commercial by inquiry) |
| [`training/`](training) | training, export and evaluation code (the data generator is not included) |
| [`release/`](release) | the public test dataset card |

## License

- SDK code and model weights: [Apache-2.0](LICENSE).
- If you use them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a request, not a license condition).
- Public test dataset: CC BY-NC 4.0 (research and evaluation; commercial use by inquiry).
- Training and validation data: on request through the [data request form](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform) (research use free; commercial use by inquiry; requests open now, data shared from December 2026); the terms come with the approval email or the contract ([tools/data-request](tools/data-request)).
- Third-party attributions: [NOTICE](NOTICE).

Made by [newmean.ai](https://newmean.ai).
