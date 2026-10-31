"""Model cards for the Hugging Face model repos newmean-ai/pii-<tier>: writes models/web/pii-<tier>/README.md and copies the
license (Apache-2.0, LICENSE) and NOTICE next to it.

Numbers are the v1.0 results of the technical report (Section 5 and the Section 6 L4 table; model only, no regex layer).
python scripts/model_cards.py [--web models/web] [--license ../LICENSE] [--notice ../NOTICE]   (run from training/)
"""
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # repository root (holds LICENSE and NOTICE)

LANGS = ("ar az bg bn ca cs da de el en es et fa fi fr ga he hi hr hu id is it ja ka kk km ko lt lv mr ms mt nl no pl pt ro ru "
         "sk sl sq sr sv sw te th tl tr uk ur uz vi zh").split()
TIERS = {  # structure, params, default variant, sizes (q4, q4e), public test miss/over, exact F1, KDPII, openpii, Nemotron, Gretel, ms 1 thread / 12 threads
    "large": ("6 layers, full pruned embeddings", "24.4M", "q4e", ("20.7 MB", "15.5 MB"), (1.6, 0.6), 0.78,
              (7.6, 2.4), (4.6, 4.2), (7.4, 0.9), (20.3, 5.2), (88, 23)),
    "medium": ("6 layers, embeddings rank 192", "17.7M", "q4e", ("14.0 MB", "11.3 MB"), (2.1, 0.6), 0.77,
               (9.8, 2.0), (6.1, 4.1), (7.9, 0.7), (22.5, 4.2), (88, 23)),
    "small": ("4 layers, embeddings rank 64", "9.6M", "q4", ("7.1 MB", "6.2 MB"), (2.7, 1.2), 0.68,
              (9.5, 3.2), (5.6, 5.5), (10.6, 1.0), (24.1, 5.7), (59, 16)),
    "nano": ("one shared layer applied 4 times, embeddings rank 32", "3.1M", "q4", ("2.6 MB", "2.1 MB"), (3.3, 2.1), 0.59,
             (9.5, 4.2), (4.9, 7.0), (10.6, 2.1), (22.8, 7.5), (59, 16)),
}


def pair(p):
    return f"{p[0]} / {p[1]}"


def card(tier):
    struct, params, default, (s_q4, s_q4e), pub, f1, kd, op, nm, gr, (ms1, ms12) = TIERS[tier]
    others = ", ".join(f"[pii-{t}](https://huggingface.co/newmean-ai/pii-{t})" for t in TIERS if t != tier)
    langs = ", ".join(f'"{l}"' if l == "no" else l for l in LANGS)
    return f"""---
license: apache-2.0
language: [{langs}]
pipeline_tag: token-classification
library_name: onnx
tags: [pii, privacy, redaction, anonymization, multilingual, onnx, browser, webassembly]
---

# newmean pii-{tier}

A small multilingual model that finds personal information (PII) in text so it can be masked **in the browser, before the text
leaves the device**. It is the `{tier}` tier ({params} parameters, {s_q4e if default == "q4e" else s_q4} download) of the
models behind the JavaScript SDK [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii). Other tiers: {others}.

- **54 languages**, 11 labels at the character level: PERSON, EMAIL, PHONE, ADDRESS, ID_NUMBER, FINANCIAL, ORG, USERNAME, URL,
  DATE_OF_BIRTH, SECRET; persons and URLs carry a level (1 = directly identifying, 2 = indirect). IP addresses are covered only
  by the SDK's optional pattern layer (off by default).
- **Built for real input:** forms, chats, spreadsheet cells, logs, JSON, OCR text, e-mails, speech transcripts.
- **Apache-2.0**, free for any use including commercial use; ONNX with 4-bit weights; runs in WebAssembly (onnxruntime-web
  or the SDK's own built-in engine).

## Use

Try it in your browser first: [pii.newmean.ai](https://pii.newmean.ai).

```bash
npm install @newmean-ai/pii
```

```js
import {{ createRedactor }} from "@newmean-ai/pii";

const redactor = await createRedactor({{ size: "{tier}" }});   // downloads this repository's files once, checks their SHA-256
const {{ text, spans }} = await redactor.redact("Hi, I'm Kim Minji, call me at 010-2345-6789.");
```

The SDK pins revision `v1.0.0` of this repository. The files can also be used directly with onnxruntime: the model takes
`input_ids` and `attention_mask` (windows of 256 tokens, stride 64) and returns BIO logits for the tags in `config.json`.

| file | |
|---|---|
| `onnx/model_q4e.onnx` | {s_q4e}, 4-bit weights and 4-bit embeddings{" (SDK default)" if default == "q4e" else ""} |
| `onnx/model_q4.onnx` | {s_q4}, 4-bit weights{" (SDK default)" if default == "q4" else ""} |
| `tokenizer.web.json` | compact Unigram tokenizer for the browser |
| `tokenizer.json`, `tokenizer_config.json` | the same tokenizer for Hugging Face `tokenizers` |
| `config.json` | tags, window settings, architecture |

## Results

Model only (no pattern layer), default file, scored on a Colab L4 VM. **Miss** = share of PII characters left unmasked (lower is
better; what matters most for masking); **over** = share of non-PII characters masked.

| set | miss % / over % |
|---|---|
| [newmean PII multilingual public test dataset](https://huggingface.co/datasets/newmean-ai/pii-multilingual-test) (54 languages, 17,058 rows) | {pair(pub)} (exact F1 {f1:.2f}) |
| KDPII (Korean) | {pair(kd)} |
| OpenPII 1.5M (30 languages) | {pair(op)} |
| Nemotron-PII (English) | {pair(nm)} |
| Gretel finance (7 languages) | {pair(gr)} |

Speed on the VM's CPU (Xeon 2.20 GHz, onnxruntime): {ms1} ms per text with one thread, {ms12} ms with 12 threads, on the
public test dataset's texts. The public test dataset is generated with the same design and guideline as the training data, so
these models have an in-distribution advantage there; the external sets were never used for training. On the public test
dataset the closest of the eight public detectors we compared (gliner-pii-edge, 176 MB) misses 19.7%; the
[leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard) scores them weekly on the public and a private test set. The full
comparison, per-label and per-language results are in the
[technical report](https://github.com/newmean-ai/pii/blob/main/docs/report/REPORT.md).

## Model

{struct}; {params} parameters. All four tiers start from mMiniLMv2-L6-H384 with the XLM-R vocabulary pruned to the 35,298 pieces
that occur in the training data, and are distilled from two larger private teacher models (mDeBERTa-v3-base and XLM-R-large,
fine-tuned for this task). Training data: about 1.07 million synthetic texts in 54 languages generated with Claude models from
a written labelling guideline; no real personal data and no external dataset was used for training.

## Limitations

- No detector finds everything: use the result to reduce what is sent, not as a guarantee of anonymity.
- Trained on synthetic text; rare formats, very long documents and languages outside the 54 may be weaker.
- Context decides some labels (an organisation is masked only when a person belongs to it; dates only when they are birth
  dates), so results depend on the surrounding text. `sensitivity` in the SDK trades misses for over-masking.

## License and attribution

Apache-2.0 ([LICENSE](LICENSE)), like the SDK. If you use the model in a product, we would appreciate a **"Powered by
newmean.ai"** mention in your docs or an about or licenses screen, and "Based on newmean pii-{tier}" in the card of a model you
derive from it (requests, not license conditions).

Built on mMiniLMv2 (Microsoft, MIT License) and the XLM-R SentencePiece tokenizer (Facebook AI, MIT License); see `NOTICE`.
Made by [newmean.ai](https://newmean.ai) — questions and commercial inquiries: hoonjae@newmean.ai.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="models/web")
    ap.add_argument("--license", default=str(REPO / "LICENSE"))
    ap.add_argument("--notice", default=str(REPO / "NOTICE"))
    a = ap.parse_args()
    for t in TIERS:
        p = Path(a.web) / f"pii-{t}" / "README.md"
        p.write_text(card(t), encoding="utf-8")
        for src, dst in ((a.license, "LICENSE"), (a.notice, "NOTICE")):
            (p.parent / dst).write_bytes(Path(src).read_bytes())
        print(p)


if __name__ == "__main__":
    main()
