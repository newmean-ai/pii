"""Model cards for the Hugging Face model repos newmean-ai/pii-<tier>: writes models/web/pii-<tier>/README.md and copies the
license (Apache-2.0, LICENSE) and NOTICE next to it.

Numbers are the v1.1 results of the technical report (Section 6 table, Section 6.1 short texts, Section 6.2 SDK 1.2; model only,
no regex layer, unless a line says SDK 1.2). The v1.1 files are those SDK 1.2 pins (release 1.2, 2026-11-02).
python scripts/model_cards.py [--web models/web] [--license ../LICENSE] [--notice ../NOTICE]   (run from training/)
"""
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # repository root (holds LICENSE and NOTICE)

LANGS = ("ar az bg bn ca cs da de el en es et fa fi fr ga he hi hr hu id is it ja ka kk km ko lt lv mr ms mt nl no pl pt ro ru "
         "sk sl sq sr sv sw te th tl tr uk ur uz vi zh").split()
TIERS = {  # structure, params, default variant, sizes (q4, q4e), public test miss/over, exact F1, KDPII, openpii, Nemotron, Gretel, ms 1 thread / 12 threads
    "large": ("6 layers, full pruned embeddings", "24.4M", "q4e", ("20.7 MB", "15.5 MB"), (1.6, 0.5), 0.80,
              (9.6, 1.7), (4.9, 4.1), (6.1, 1.6), (21.4, 5.5), (88, 23)),
    "medium": ("6 layers, embeddings rank 192", "17.7M", "q4e", ("14.0 MB", "11.3 MB"), (1.9, 0.6), 0.77,
               (11.5, 1.6), (6.3, 4.3), (7.7, 0.9), (21.5, 5.4), (88, 23)),
    "small": ("4 layers, embeddings rank 64", "9.6M", "q4", ("7.1 MB", "6.2 MB"), (2.4, 1.0), 0.72,
              (10.0, 2.5), (5.6, 5.3), (8.4, 0.9), (22.3, 5.8), (59, 16)),
    "nano": ("one shared layer applied 4 times, embeddings rank 32", "3.1M", "q4", ("2.6 MB", "2.1 MB"), (2.9, 1.5), 0.65,
             (9.5, 4.1), (5.0, 6.2), (9.9, 2.3), (21.4, 7.6), (59, 16)),
}
# v1.0 → v1.1 (Section 6.1 and Appendix Q): short items masked, survey CSV cells masked cell by cell, names written alone
# found, KDPII miss
SHORT = {"large": ((30.6, 7.9), (24.9, 6.6), 99.0, (7.6, 9.6)), "medium": ((30.6, 7.8), (22.5, 5.9), 97.5, (9.8, 11.5)),
         "small": ((36.8, 9.4), (29.5, 7.7), 97.0, (9.5, 10.0)), "nano": ((39.3, 11.1), (32.5, 9.9), 96.7, (9.5, 9.5))}
# SDK 1.2 (Section 6.2): texts with an emoji masked, model only → SDK 1.2; survey exports, table mode + context "header":
# miss / PII-free cells masked, and miss of table mode alone
SDK12 = {"large": ((23.95, 0.87), (1.44, 12.30), 6.17), "medium": ((14.89, 0.78), (1.93, 11.66), 7.93),
         "small": ((20.57, 0.89), (1.72, 19.03), 6.65), "nano": ((26.33, 1.00), (1.58, 27.34), 6.13)}


def pair(p):
    return f"{p[0]} / {p[1]}"


def card(tier):
    struct, params, default, (s_q4, s_q4e), pub, f1, kd, op, nm, gr, (ms1, ms12) = TIERS[tier]
    (sh0, sh1), (cc0, cc1), names, (kd0, kd1) = SHORT[tier]
    (em0, em1), (tx_miss, tx_fp), tbl_miss = SDK12[tier]
    others = ", ".join(f"[newmean-pii-{t}](https://huggingface.co/newmean-ai/pii-{t})" for t in TIERS if t != tier)
    langs = ", ".join(f'"{l}"' if l == "no" else l for l in LANGS)
    return f"""---
license: apache-2.0
language: [{langs}]
pipeline_tag: token-classification
library_name: onnx
tags: [pii, privacy, redaction, anonymization, multilingual, onnx, browser, webassembly]
---

# newmean-pii-{tier}

A small multilingual model that finds personal information (PII) in text so it can be masked **in the browser, before the text
leaves the device**. It is the `{tier}` tier ({params} parameters, {s_q4e if default == "q4e" else s_q4} download) of the
models behind the JavaScript SDK [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii). Other tiers: {others}.

- **54 languages**, 11 labels at the character level: PERSON, EMAIL, PHONE, ADDRESS, ID_NUMBER, FINANCIAL, ORG, USERNAME, URL,
  DATE_OF_BIRTH, SECRET; persons and URLs carry a level (1 = directly identifying, 2 = indirect). IP addresses are learned as
  ID_NUMBER. The SDK has an optional pattern layer (e-mail, URL, card/account, phone, Korean resident number), off by default.
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
const {{ text, spans }} = await redactor.redact("Hi, I'm Kim Minji, call me at 010-1234-5678.");
```

SDK 1.2 pins revision `v1.1.0` of this repository (SDK 1.0 pins `v1.0.0`). The files can also be used directly with onnxruntime: the model takes
`input_ids` and `attention_mask` (windows of 256 tokens, stride 64) and returns BIO logits for the tags in `config.json`.

| file | |
|---|---|
| `onnx/model_q4e.onnx` | {s_q4e}, 4-bit weights and 4-bit embeddings{" (SDK default)" if default == "q4e" else ""} |
| `onnx/model_q4.onnx` | {s_q4}, 4-bit weights{" (SDK default)" if default == "q4" else ""} |
| `tokenizer.web.json` | compact Unigram tokenizer for the browser |
| `tokenizer.json`, `tokenizer_config.json` | the same tokenizer for Hugging Face `tokenizers` |
| `config.json` | tags, window settings, architecture |

## Results

Model only (no pattern layer), default file, onnxruntime on CPU. **Miss** = share of PII characters left unmasked (lower is
better; what matters most for masking); **over** = share of non-PII characters masked.

| set | miss % / over % |
|---|---|
| [newmean-pii-dataset (public test)](https://huggingface.co/datasets/newmean-ai/pii-dataset) (54 languages, 17,058 rows) | {pair(pub)} (exact F1 {f1:.2f}) |
| KDPII (Korean) | {pair(kd)} |
| OpenPII 1.5M (30 languages) | {pair(op)} |
| Nemotron-PII (English) | {pair(nm)} |
| Gretel finance (7 languages) | {pair(gr)} |

Speed on a Colab L4 VM's CPU (Xeon 2.20 GHz, onnxruntime): {ms1} ms per text with one thread, {ms12} ms with 12 threads, on the
public test's texts. The public test of newmean-pii-dataset is generated with the same design and guideline as the training data, so
these models have an in-distribution advantage there; the external sets were never used for training. On the public test
the closest of the eight public detectors we compared (gliner-pii-edge, 176 MB) misses 19.6%; the
[leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard) scores them weekly on its public and private test. The full
comparison, per-label and per-language results are in the
[technical report](https://github.com/newmean-ai/pii/blob/main/docs/report/REPORT.md).

**Short texts and survey files (v1.1).** On a private set of 20,691 short items in 54 languages (column titles, answer options,
single words, names and values written alone) and 228 survey CSV files: masks {sh1}% of the PII-free items (v1.0: {sh0}%) and {cc1}% of the PII-free cells when a survey file is checked cell by cell (v1.0: {cc0}%); finds {names}% of names written alone. The set is generated (by a different model from the training data) and not published.

**With SDK 1.2.** The numbers above move by at most 0.04 points, because the SDK only changes texts with emoji or keycaps.
Emoji are taken out of the text the model reads: texts with an emoji masked fall from {em0:.2f}% (model only) to {em1:.2f}%. Table
mode with `context: "header"` misses {tx_miss:.2f}% of the PII characters of 556 survey and form exports in 54 languages (table mode
alone: {tbl_miss:.2f}%) and masks {tx_fp:.2f}% of their PII-free cells. Both sets are private and synthetic.

## Model

{struct}; {params} parameters. All four tiers start from mMiniLMv2-L6-H384 with the XLM-R vocabulary pruned to the 35,298 pieces
that occur in the training data, and are distilled from two larger private teacher models (mDeBERTa-v3-base and XLM-R-large,
fine-tuned for this task). Training data: about 1.31 million synthetic texts in 54 languages generated with Claude models from
a written labelling guideline (v1.1 added 240,498 short texts: titles, answer options, single words, lone names and values,
survey rows); no real personal data and no external dataset was used for training.

## Limitations

- No detector finds everything: use the result to reduce what is sent, not as a guarantee of anonymity.
- Trained on synthetic text; rare formats, very long documents and languages outside the 54 may be weaker.
- Context decides some labels (an organisation is masked only when a person belongs to it; dates only when they are birth
  dates), so results depend on the surrounding text. `sensitivity` in the SDK trades misses for over-masking.
- Survey files checked cell by cell: use newmean-pii-large or `sensitivity: -0.5`; at the default, smaller tiers can mask a lone
  gender word ("남성", "Male").

## Changelog

- **v1.1.0** (2026-11-01, published with SDK 1.2.0 on 2026-11-02): short texts, table cells and survey exports. PII-free short
  items masked: {sh0}% → {sh1}%. Same architecture, tokenizer and sizes. KDPII miss {kd0}% → {kd1}%{" (higher than v1.0)" if kd1 > kd0 else ""}.
- **v1.0.0** (2026-10-31): first release.

## License and attribution

Apache-2.0 ([LICENSE](LICENSE)), like the SDK. If you use the model in a product, we would appreciate a **"Powered by
newmean.ai"** mention in your docs or an about or licenses screen, and "Based on newmean-pii-{tier}" in the card of a model you
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
