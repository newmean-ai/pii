"""Pick the phone-bench consistency set: two short or medium rows per language from the public test dataset (seeded).

The page runs these texts on every size and engine and compares the spans with a reference computed on a desktop browser
(see `?ref=1` in main.ts). Pass the output to build.mjs (fourth argument or PII_CONSISTENCY). It holds test rows, so it
stays out of git.

python3 experiments/phone-bench/make_consistency.py <test.jsonl> [out.json]
  test.jsonl  data/test.jsonl of the public test dataset on Hugging Face (or set PII_TEST_JSONL). Required.
  out.json    default: PII_CONSISTENCY, else consistency.json next to this script.
"""
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

src_arg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PII_TEST_JSONL")
if not src_arg or not Path(src_arg).is_file():
    sys.exit(
        f"test.jsonl {'not found: ' + src_arg if src_arg else 'missing'}.\n"
        "usage: python3 make_consistency.py <test.jsonl of the public test dataset> [out.json] (or set PII_TEST_JSONL)"
    )
src = Path(src_arg)
out = Path(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("PII_CONSISTENCY") or Path(__file__).parent / "consistency.json")
PER_LANG = 2

by_lang = defaultdict(list)
for line in src.open():
    r = json.loads(line)
    if r["length"] in ("short", "medium"):
        by_lang[r["lang"]].append(r)
rng = random.Random(79)
rows = []
for lang in sorted(by_lang):
    for r in rng.sample(by_lang[lang], PER_LANG):
        rows.append({"id": r["id"], "lang": lang, "text": r["text"]})
out.write_text(json.dumps({"set": rows}, ensure_ascii=False))
print(f"{len(rows)} rows, {len(by_lang)} languages, {sum(len(r['text']) for r in rows)} chars -> {out}")
