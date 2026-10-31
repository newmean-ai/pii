"""The leaderboard metric is evaluate.py's metric.

1. Source parity (always runs): every vendored scoring definition and every predictor's decoding (__call__, _run) is
   AST-identical to training/scripts/evaluate.py; the public-detector maps and settings match evaluate.EXT.
2. Number parity (maintainer-only): the leaderboard pipeline, fed the public test set as published on the Hub
   (newmean-ai/pii-dataset) and the predictions stored by the 2026-10-30 L4 runs, reproduces every number of the
   stored evaluate.py reports (overall, per label, per language, per level) for all 13 seeded detectors. The stored L4 run
   outputs are not public: set PII_RESEARCH_DIR to them (eval_<tag>.json, preds/<tag>/pmt_v11.jsonl); without it these
   tests are skipped. Test file and model sources: conftest.py.
"""
import ast, json, sys
from pathlib import Path

import pytest

LB = Path(__file__).resolve().parent.parent
REPO = LB.parent
sys.path.insert(0, str(LB))
sys.path.insert(0, str(REPO / "training/scripts"))

import evaluate  # noqa: E402  (training/scripts/evaluate.py; imports only the standard library at module level)
from pii_leaderboard import adapters, scoring  # noqa: E402
from pii_leaderboard.evaluation import load_rows, score_predictions  # noqa: E402


def defs(path):
    out = {}
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node
    return out


def dump(node):
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def method(cls, name):
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


EVAL = defs(REPO / "training/scripts/evaluate.py")


@pytest.mark.parametrize("name", scoring.VENDORED)
def test_vendored_scoring_is_identical(name):
    assert dump(defs(LB / "pii_leaderboard/scoring.py")[name]) == dump(EVAL[name])


@pytest.mark.parametrize("cls,meth", [("OnnxTokenPredictor", "__call__"), ("OnnxTokenPredictor", "_run"),
                                      ("ExtHFPredictor", "__call__"), ("GlinerPredictor", "__call__"), ("HFPredictor", "__call__")])
def test_decoding_is_identical(cls, meth):
    ours = defs(LB / "pii_leaderboard/adapters.py")
    assert dump(method(ours[cls], meth)) == dump(method(EVAL[cls], meth))


def test_ext_spec_body_is_identical():
    ours = defs(LB / "pii_leaderboard/adapters.py")["ext_spec"]
    assert [dump(s) for s in ours.body[1:]] == [dump(s) for s in EVAL["ext_spec"].body[1:]]   # body after the docstring


@pytest.mark.parametrize("key", sorted(evaluate.EXT))
def test_public_detector_settings_match(key):
    ours, theirs = adapters.ext_spec(key, "v1"), evaluate.ext_spec(key, "v1")
    for k in theirs:
        if k != "dir":
            assert ours[k] == theirs[k], (key, k)


# --- number parity on stored predictions ---------------------------------------------------------------------------------
def seeds():
    sys.path.insert(0, str(LB / "scripts"))
    import seed_results
    return seed_results.SEEDS


def detector_labels(model, adapter, newmean_model):
    if adapter.startswith("ext:"):
        return sorted(set(adapters.ext_spec(adapter[4:])["map"].values()))
    if adapter == "newmean-onnx":
        d = newmean_model(model.rsplit("/", 1)[-1].removeprefix("pii-"), ["*.json"])   # the label set only
        if d is None:
            pytest.skip(f"{model} not available")
        return sorted(set(adapters.newmean_onnx_spec(d)["map"].values()))
    return None   # transformers with label map "auto" (evaluate.HFPredictor) and regex


@pytest.fixture(scope="module")
def public_rows(research_dir, public_file):
    return load_rows(public_file)


@pytest.mark.parametrize("tag", sorted(seeds()))
def test_leaderboard_reproduces_l4_numbers(tag, research_dir, public_rows, newmean_model):
    model, rev, adapter, _, _ = seeds()[tag]
    rep = json.loads((research_dir / f"eval_{tag}.json").read_text())
    want = rep["pmt_v11"]
    preds = [json.loads(l)["spans"] for l in open(research_dir / f"preds/{tag}/pmt_v11.jsonl")]
    got = score_predictions(public_rows, preds, detector_labels(model, adapter, newmean_model))
    assert got["gold_collapsed"] == rep["gold_collapsed"]
    keys = ["ALL", "labels", "exact_by_label", "levels", "leak_by_label", "strict_leak_by_label"] + [k for k in want if k.startswith("lang:")]
    for k in keys:
        assert got[k] == want[k], (tag, k, got[k], want[k])
