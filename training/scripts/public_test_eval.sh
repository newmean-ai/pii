#!/bin/bash
# Public test dataset (17,058 rows): the four v1 students as shipped (ONNX q4 and q4e) and the public detectors.
# ONNX runs (students, Mozilla, Rampart, Rampart-edge) and regex share the CPU, four at a time; the PyTorch / GLiNER detectors
# run one after another on $DEVICE (default mps; cuda or cpu elsewhere). Results merge into the existing
# reports/eval_<tag>.json under the set key.
#
# Inputs (run from training/; FETCH=1 downloads the models first):
#   data/eval/$SET.jsonl      the public test dataset: data/test.jsonl of the Hugging Face dataset
#                             newmean-ai/pii-multilingual-test, saved as data/eval/pii_multilingual_test.jsonl, e.g.
#                               hf download newmean-ai/pii-multilingual-test data/test.jsonl --repo-type dataset --local-dir /tmp/pmt
#                               cp /tmp/pmt/data/test.jsonl data/eval/pii_multilingual_test.jsonl
#   $STUDENTS/pii-<tier>/     the released models (Hugging Face newmean-ai/pii-<tier>: onnx/model_q4.onnx, onnx/model_q4e.onnx,
#                             tokenizer, config.json); tags.json, which evaluate.py reads, is written from config.json's tags.
#                             export_web.py builds the same folders from your own export_onnx.py output.
#   models/ext/<name>/        the public detectors at the Hub revisions of the published comparison (list in fetch_models).
# Python environments: .venv (requirements.txt) for everything except GLiNER; .venv-gliner for the two GLiNER detectors
# (gliner==0.2.29, gliner2==2.1.0; see requirements.txt).
#   Usage: [FETCH=1] [SET=pii_multilingual_test] [DEVICE=mps] nohup bash scripts/public_test_eval.sh >> reports/public_test_eval.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
SET=${SET:-pii_multilingual_test}
STUDENTS=${STUDENTS:-models/web}
DEVICE=${DEVICE:-mps}
PY=$PWD/.venv/bin/python
PYG=$PWD/.venv-gliner/bin/python
log() { echo "$(date '+%F %T') [public-test] $*"; }
[ -f "data/eval/$SET.jsonl" ] || { log "missing data/eval/$SET.jsonl (see the header for how to get the public test dataset)"; exit 1; }
mkdir -p reports

fetch_models() {   # evaluate.py EXT dir and the two transformers detectors, at the revisions of the published comparison
  STUDENTS=$STUDENTS "$PY" - <<'PY' || { log "model download failed"; exit 1; }
import os
from huggingface_hub import snapshot_download
ONNX = ["*.json", "*.txt", "onnx/model_q4.onnx"]
for d, repo, rev, pat in [
    ("smart-pii-redactor-v1-onnx", "Mozilla/smart-pii-redactor-v1-onnx", "5febfa13dbaa8771072af90914fee0de8bbcf6b7", ONNX),
    ("rampart", "nationaldesignstudio/rampart", "b1993e4e68b082835b80ffc65acc03325ea2e501", ONNX),
    ("rampart-edge", "nationaldesignstudio/rampart-edge", "69a99a3f0d3cbdb8db11f2ac97899f654be109bf", ONNX),
    ("privacy-filter", "openai/privacy-filter", "7ffa9a043d54d1be65afb281eddf0ffbe629385b", ["*.json", "model.safetensors", "original/*.json"]),
    ("gliner-pii-edge-v1.0", "knowledgator/gliner-pii-edge-v1.0", "9b7f39b0a2da971a5beea78d35f1539d4009c891", None),
    ("gliner2-privacy-filter-PII-multi", "fastino/gliner2-privacy-filter-PII-multi", "1cb4166094dc58fa8d836429f060d6c95f62b495", None),
    ("pii-redactor-small", "Horizon-Labs/pii-redactor-small", "3f6a99b9906378f3faed3e6eeb3b396b0b8257c2", None),
    ("koelectra-ko-pii-ner", "atonlee/koelectra-ko-pii-ner", "1e75c01e707232401883cf364151bbe2e560c708", None),
]:
    snapshot_download(repo, revision=rev, local_dir=f"models/ext/{d}", allow_patterns=pat)
    print("model", d, "ok", flush=True)
for tier in ("large", "medium", "small", "nano"):
    snapshot_download(f"newmean-ai/pii-{tier}", local_dir=f"{os.environ['STUDENTS']}/pii-{tier}")
    print("model", f"pii-{tier}", "ok", flush=True)
PY
}
[ "${FETCH:-0}" = 1 ] && fetch_models
for size in large medium small nano; do   # evaluate.py onnx: reads tags.json; the released folders carry the tags in config.json
  d=$STUDENTS/pii-$size
  [ -f "$d/tags.json" ] || "$PY" -c 'import json,sys; d=sys.argv[1]; json.dump({"tags": json.load(open(d + "/config.json"))["tags"]}, open(d + "/tags.json", "w"))' "$d" \
    || { log "missing $d (run with FETCH=1)"; exit 1; }
done

run() {   # tag python model [args]
  local tag=$1 py=$2 model=$3; shift 3
  log "start $tag"
  if $py scripts/evaluate.py --model "$model" --sets $SET --tag "$tag" "$@" > "reports/evalrun_${tag}_$SET.log" 2>&1; then
    log "$tag $(grep -E "^$SET \{" "reports/evalrun_${tag}_$SET.log" | cut -c1-200)"
  else
    log "FAILED $tag: $(tail -2 "reports/evalrun_${tag}_$SET.log" | tr '\n' ' ')"
  fi
}
cpu_jobs() {
  for size in large medium small nano; do
    for v in q4e q4; do echo "$v-s-$size-v1 $PY onnx:$STUDENTS/pii-$size:model_$v.onnx"; done
  done
  for m in mozilla rampart rampart-edge; do echo "h2h_$m $PY ext:$m --scheme v1"; done
  echo "h2h_regex $PY regex --scheme v1"
}
cpu_queue() {
  while read -r tag py model args; do
    while [ "$(jobs -rp | wc -l)" -ge 4 ]; do sleep 5; done
    run "$tag" "$py" "$model" $args &
  done < <(cpu_jobs)
  wait
}
export ORT_THREADS=4 HF_HUB_OFFLINE=1
cpu_queue &
CPU=$!
run h2h_openai-pf "$PY" ext:openai-pf --scheme v1 --device "$DEVICE"
run h2h_pii-redactor-small "$PY" models/ext/pii-redactor-small --scheme v1 --device "$DEVICE"
run h2h_koelectra-ko-pii-ner "$PY" models/ext/koelectra-ko-pii-ner --scheme v1 --device "$DEVICE"
run h2h_gliner2-pii "$PYG" ext:gliner2-pii --scheme v1 --device "$DEVICE"
run h2h_gliner-edge "$PYG" ext:gliner-edge --scheme v1 --device "$DEVICE"
wait $CPU
wait
log "DONE"
