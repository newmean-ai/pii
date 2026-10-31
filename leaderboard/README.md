---
title: newmean PII leaderboard
emoji: 🛡️
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: apache-2.0
hf_oauth: true
hf_oauth_scopes:
  - read-repos
  - write-discussions
short_description: Multilingual PII detection leaderboard (54 languages)
---

# newmean PII leaderboard

How much personal information does a detector miss, and how much else does it mask? Every model is scored on two test sets of
the same design: the public [newmean PII multilingual test set](https://huggingface.co/datasets/newmean-ai/pii-multilingual-test)
(17,058 synthetic texts, 54 languages) and a private twin that is never released. [한국어](README.ko.md)

This folder is both the Hugging Face Space (`newmean-ai/pii-leaderboard`, a static page: `index.html` + `lb.js`, no server, no
secrets) and the code of the weekly scoring job.

## What is shown

- **Public test set**: overall, per label, per language and per PII level.
- **Private test set**: overall numbers only. No per-row output, per-label or per-language numbers are ever produced for it.

Metrics (masking point of view; the same code as `training/scripts/evaluate.py` and the technical report):

| metric | meaning |
|---|---|
| miss % (leak) | share of PII characters not covered by any predicted span; lower is better, the main ranking |
| over-masking % | share of non-PII characters that get masked; lower is better |
| exact F1 | predicted spans with the same boundaries and label as the gold spans |
| span F1 | same label and at least 50% overlap |
| entity miss % | share of PII values with less than half of their characters masked |

Unless a model outputs the level-2 labels `PERSON_L2` / `URL_L2`, the gold labels are collapsed to its granularity
(`PERSON_L2` → `PERSON`, `URL_L2` → `URL`). Leak and over-masking do not depend on labels. Model only, no regex layer.

## Submitting a model

1. Sign in with Hugging Face on the Space's **Submit** tab (scope `write-discussions`).
2. Give a public Hub model id and revision, and choose the adapter:
   - `transformers`: a token-classification model (`…ForTokenClassification`, safetensors weights, no custom code, at most
     3 GB), run with the transformers pipeline (`aggregation_strategy="simple"`). Add a **label map** from each of the model's
     labels (B-/I- prefixes dropped) to one of our labels: `PERSON`, `PERSON_L2`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`,
     `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `URL_L2`, `DATE_OF_BIRTH`, `SECRET`, or `OTHER` (masked, no matching label) or
     `ignore` (not masked). `auto` uses the generic name-based mapping of `evaluate.py`. IP addresses are `ID_NUMBER` in our
     label scheme.
   - `newmean-onnx`: the newmean models `newmean-ai/pii-<size>`, scored with the weight file the SDK loads by default
     (`variant`: `q4e` for large and medium, `q4` for small and nano).
   - Other formats (GLiNER, other ONNX layouts, …) are added by the maintainers as adapters: open a discussion on the Space.
3. **Check** runs the cheap checks in your browser through the public Hub API (public, not gated, safetensors, size, no custom
   code, token-classification architecture, label map coverage). **Submit** opens a discussion on
   `newmean-ai/pii-leaderboard-requests` whose body holds the request as a JSON block; you can also open one by hand in the same
   format. The weekly job checks it again, enforces two submissions per account per rolling 7 days and the queue cap, scores it,
   and replies in the discussion with the scores or the reason for a rejection, then closes it.

**Why weekly, also for the public set.** The public set is public: you can score yourself right away with
`scripts/score_public.py` (same metric), so the leaderboard adds no information by scoring it within minutes. One weekly job
keeps the Space free of any token that can start paid jobs, amortises the job's setup over the whole queue, keeps the cost
predictable (one run, bounded by a time budget), and makes probing the private set slow.

## How scoring works

`jobs/run_queue.py` runs as a scheduled Hugging Face Job in the `newmean-ai` namespace. It reads the open submission
discussions, checks each again (rejections get a reply and are closed), and keeps its ledger in `requests/<id>.json`. Then for
each pending request (oldest first) it downloads the model files and scores them in a child process (`jobs/score_model.py`)
inside the adapter's own uv environment (`jobs/requirements-<env>.txt`, the library versions of the report's L4 run), without
any Hub token and with the Hub offline. It writes `results/<model>@<revision>__<adapter>.json`, replies in the discussion, and
publishes `leaderboard.json` (results dataset) and `queue.json` (requests dataset), the two files the page reads. Results whose
private score is still pending (rows seeded from stored reports) are scored on the private set in the same run. Submitted models
never run their own code (`trust_remote_code=False`, safetensors only).

| repository | content |
|---|---|
| Space `newmean-ai/pii-leaderboard` (static) | this folder: `index.html`, `lb.js`, and the job code `pii_leaderboard/`, `jobs/` |
| dataset `newmean-ai/pii-leaderboard-requests` | submission discussions; the ledger `requests/<id>.json`; `queue.json` |
| dataset `newmean-ai/pii-leaderboard-results` | `results/<key>.json` (public: overall, per label, language, level; private: overall); `leaderboard.json` |
| dataset `newmean-ai/pii-multilingual-test` | public test set |
| dataset `newmean-ai/pii-multilingual-test-private` | private test set, readable only by the scoring job's token |

The Space has no secrets. The job has one, by name: `HF_TOKEN` (read the test sets and the Space; write the requests and
results datasets and their discussions).

### Scoring job

The job's command downloads this code from the Space and runs it, so nothing local is uploaded (with a local script file,
`hf jobs uv run` would create a `jobs-artifacts` bucket):

```bash
hf jobs run --namespace newmean-ai --flavor l4x1 --timeout 1h -s HF_TOKEN -e ORT_THREADS=8 -e PII_LB_HARDWARE=l4x1 \
  ghcr.io/astral-sh/uv:python3.12-bookworm bash -c \
  'uvx --from huggingface_hub hf download newmean-ai/pii-leaderboard --repo-type space --include "pii_leaderboard/*" --include "jobs/*" \
     --local-dir /code && PII_LB_CODE=/code uv run /code/jobs/run_queue.py'
```

`-s HF_TOKEN` without a value passes the token of the local login as a job secret (it never appears in the command or logs);
the scheduled job (weekly) runs the same command with a dedicated fine-grained token (read and write on the five
repositories above, nothing else). Set `ORT_THREADS` to the flavor's vCPUs: inside a job the CPU count reports the host's. Maintainers queue a request directly with
`scripts/admin_request.py` (adapters we add, re-scoring, test runs).

## Seeded results

The first rows are the technical report's Colab L4 runs (2026-10-30) on the public test set: the four newmean models
(each tier's SDK default weight file: `model_q4e.onnx` for large and medium, `model_q4.onnx` for small
and nano), eight public detectors and the regex baseline (`scripts/seed_results.py`). The job's first run (2026-10-31)
scored all 13 seeded rows on the private test set. New submissions show "pending" until the next weekly run scores them.

## Development

```bash
# tests: the metric is evaluate.py's (AST-identical definitions; all 13 seeded detectors reproduce the stored numbers),
# browser and job checks agree (node), discussion intake and limits, the job end to end on a local store
uv run --no-project --python 3.13 --with pytest --with onnxruntime==1.31.1 --with transformers==5.21.0 --with numpy \
  --with huggingface_hub python -m pytest tests -q
# the page on a local store (http://localhost:8000, reads ./preview/, posts nothing)
python scripts/seed_results.py --reports <reports dir> --out /tmp/lb && python scripts/preview.py /tmp/lb
```

The browser tests need node. The public test set and the newmean models come from the Hub (or local copies, see
`tests/conftest.py`). Number-parity tests need the maintainers' stored L4 predictions, which are not public, and are skipped
without them. Seeding results (`scripts/seed_results.py`) is a maintainer-only step for the same reason.
`pii_leaderboard/scoring.py` is vendored from `training/scripts/evaluate.py`; change the metric there and re-vendor.

Code: Apache-2.0. Test data: CC BY-NC 4.0. Made by [newmean.ai](https://newmean.ai).
