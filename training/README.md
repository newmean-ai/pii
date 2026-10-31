English | [한국어](README.ko.md)

# training/

The scripts behind the released models and the published evaluation: training, distillation, compression, export, and
scoring. Run them from this folder (`cd training`); paths such as `data/`, `models/` and `reports/` are relative to it.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

GLiNER-based detectors need a second environment; see the comment at the end of [requirements.txt](requirements.txt).

## Scripts

| Script | What it does | Main inputs |
|---|---|---|
| `scripts/train_ner.py` | Token-classification (BIO) fine-tuning of teachers and students, with optional knowledge distillation from teacher soft labels, EMA weights and per-epoch snapshots | training and dev JSONL (`--train`, `--dev`), base model (`--base`), soft labels (`--soft`), student tier (`--arch`) |
| `scripts/teacher_soft.py` | Writes a teacher's soft labels per character span (tokenizer-independent distillation targets) | a `train_ner.py` `final/` folder (`--model`), training JSONL (`--data`) |
| `scripts/prune_xlmr.py` | Prunes the XLM-R vocabulary of mMiniLMv2 to the pieces the 54 languages use, keeping the pretrained embeddings | training JSONL (`--train`), vocabulary size (`--max`) |
| `scripts/student_arch.py` | The compressed student tiers (factorised embeddings, layer dropping, one shared layer applied several times); imported by `train_ner.py`, `evaluate.py` and `export_onnx.py` | — |
| `scripts/textnorm.py` | Per-character NFKC with an offset map back to the original text, for tokenizers without a Unicode normalizer | — |
| `scripts/select_ckpt.py` | Picks a checkpoint on the validation set by a language- and label-macro miss + over-masking cost, then exact F1 | saved predictions `reports/preds/<tag>/<set>.jsonl` (`--set`, `--tags`) |
| `scripts/export_onnx.py` | Exports a trained model to ONNX and quantizes it (4-bit MatMul weights; `--emb4` also 4-bit embeddings) | a `train_ner.py` `final/` folder (`--model`) |
| `scripts/export_web.py` | Builds the model folders the SDK downloads (`models/web/pii-<tier>`: ONNX, tokenizers, config) and their manifest with SHA-256 | `export_onnx.py` output in `models/onnx/s-<tier>-v1` |
| `scripts/export_wasm_weights.py` | Converts the web ONNX files to the `.piiw` weight file read by the SDK's built-in WebAssembly engine (`js/engine/pii.c`); the docstring defines the format | `models/web/pii-<tier>` (`--src`) |
| `scripts/model_cards.py` | Writes the Hugging Face model cards and copies LICENSE and NOTICE next to them | `models/web` (`--web`) |
| `scripts/evaluate.py` | Scores a model or a public detector: character miss rate (leak), over-masking, span and exact F1, per label, language and PII level | `--model` (`regex`, a local or Hub model, `onnx:<dir>[:<file>]`, `ext:<detector>`), set names read from `data/eval/<name>.jsonl` (`--sets`) |
| `scripts/compare_models.py` | Paired bootstrap comparison of two detectors on one set | saved predictions of two `evaluate.py` runs (`--a`, `--b`, `--set`) |
| `scripts/public_test_eval.sh` | Runs the released models and the public detectors on the public test dataset (the comparison in the report) | `data/eval/pii_multilingual_test.jsonl`; models downloaded with `FETCH=1` |
| `scripts/ext_data.py` | Rebuilds the four external evaluation sets from the original downloads and checks them against the manifest; also converts their training splits | raw downloads in `data/raw` (`--raw`), `eval_sets/manifest.json` |
| `scripts/external_sets.py` | Label mappings of the external datasets to our scheme (used by `ext_data.py`) | — |
| `scripts/dedupe_splits.py` | Exact and near-duplicate removal across splits (MinHash over masked text), PII-value overlap, diversity report | JSONL splits (`--split name=path`, `--order`) |

## Data

- **Public test dataset.** [newmean-ai/pii-multilingual-test](https://huggingface.co/datasets/newmean-ai/pii-multilingual-test)
  on the Hugging Face Hub. Save its `data/test.jsonl` as `data/eval/pii_multilingual_test.jsonl` to use it with `evaluate.py`
  and `public_test_eval.sh`.
- **Training and validation data.** Not in this repository; available on request through the
  [data request form](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform)
  (research use free; commercial use by inquiry).
- **Data generation code is not released.** The prompts, the labelling guideline and the generation and review pipeline stay
  private; the method is described in full in Appendix M of the technical report
  ([APPENDIX-methods.md](../docs/report/APPENDIX-methods.md)).

## External evaluation sets

The four external sets (`openpii_val`, `kdpii_test`, `nemotron_test`, `gretel_fin_test`) are not redistributed. Rebuild them
from the original downloads:

1. Download the raw data into `data/raw/` (licenses and dataset revisions are in
   [eval_sets/manifest.json](eval_sets/manifest.json), `sources`):

   | Source | Files under `data/raw/` |
   |---|---|
   | `ai4privacy/pii-masking-openpii-1.5m` (CC BY 4.0) | `openpii/data/train.jsonl`, `openpii/data/validation.jsonl` |
   | KDPII, Zenodo record 16759166, v2 (CC BY 4.0) | `kdpii.json` |
   | `nvidia/Nemotron-PII` (CC BY 4.0) | `nemotron/train.parquet`, `nemotron/test.parquet` |
   | `gretelai/synthetic_pii_finance_multilingual` (Apache-2.0) | `gretel_fin/train-00000-of-00001.parquet`, `gretel_fin/test-00000-of-00001.parquet` |

2. Run `python scripts/ext_data.py eval`. It selects the rows listed in the manifest, maps their labels to our scheme
   (`external_sets.py`), writes `data/eval/<set>.jsonl` and exits with an error if a file's SHA-256 differs from the manifest.

Sampling and label mapping are described in Appendix M15 of the technical report.
