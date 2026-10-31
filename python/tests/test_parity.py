# Parity through the Python API: tokenizer vs Hugging Face tokenizers, spans vs the Python evaluator (the runs of
# js/test/predict.test.ts, same 99% threshold). Private fixtures and model copies; skips without them.
import time

import pytest

from newmean_pii import Redactor

from conftest import FIXTURES, SIZES, has_model, model_dir, read_jsonl

RUNS = [
    ("large", "q4e", 0), ("large", "q4", 0), ("medium", "q4e", 0), ("medium", "q4", 0),
    ("small", "q4", 0), ("small", "q4e", 0), ("nano", "q4", 0), ("nano", "q4e", 0),
    ("medium", "q4e", -0.5), ("nano", "q4", 1),
]


def test_tokenizer():
    fix = FIXTURES / "tokenize.jsonl"
    size = next((s for s in ("nano", "small", "medium", "large") if has_model(s, "q4")), None)
    if not fix.is_file() or size is None:
        pytest.skip("tokenize.jsonl or a model copy absent")
    r = Redactor(size, "q4", model_dir=model_dir(size))
    rows = read_jsonl(fix)
    bad_ids = bad_offsets = 0
    for row in rows:
        toks = r.tokenize(row["text"])
        bad_ids += [t.id for t in toks] != row["ids"]
        bad_offsets += [[t.start, t.end] for t in toks] != [list(o) for o in row["offsets"]]  # code points
    n = len(rows)
    print(f"tokenizer: ids {n - bad_ids}/{n}, offsets {n - bad_offsets}/{n}")
    assert bad_ids == 0 and bad_offsets == 0


@pytest.mark.parametrize("size,variant,bias", RUNS, ids=[f"{s}-{v}-b{b:g}" for s, v, b in RUNS])
def test_predict(size, variant, bias):
    if SIZES and size not in SIZES:
        pytest.skip("PII_SIZES")
    fix = FIXTURES / f"predict-{size}-{variant}{f'-b{bias:g}' if bias else ''}.jsonl"
    if not fix.is_file() or not has_model(size, variant):
        pytest.skip("fixture or model copy absent")
    rows = read_jsonl(fix)
    # Python O bias b is added to the O logit; sensitivity = -b
    r = Redactor(size, variant, model_dir=model_dir(size), sensitivity=-bias)
    t0 = time.perf_counter()
    same, diffs = 0, []
    for row in rows:
        want = [(s["start"], s["end"], s["label"]) for s in row["spans"]]
        got = [(s.start, s.end, s.label) for s in r.detect(row["text"])]
        if want == got:
            same += 1
        elif len(diffs) < 3:
            diffs.append(f"{row['text'][:60]!r}\n  py {want}\n  c  {got}")
    print(f"{size} {variant} bias {bias:g}: {same}/{len(rows)} ({time.perf_counter() - t0:.1f} s)", *diffs, sep="\n")
    assert same / len(rows) >= 0.99
