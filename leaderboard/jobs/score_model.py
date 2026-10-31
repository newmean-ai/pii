"""Child process of run_queue.py: runs one model on the test sets and writes the leaderboard numbers.

It runs inside the adapter's isolated environment (requirements-<env>.txt), without any Hub token and with the Hub offline:
the model files and test sets were downloaded by the parent. For the private set only the overall numbers leave this process.

  python jobs/score_model.py --request req.json --model-dir DIR [--public test.jsonl] [--private private.jsonl] --out out.json
"""
import argparse, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pii_leaderboard.adapters import build_predictor  # noqa: E402
from pii_leaderboard.evaluation import load_rows, private_summary, public_summary, score_predictions  # noqa: E402


def device():
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--model-dir", default="")
    ap.add_argument("--public")
    ap.add_argument("--private")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    assert not os.environ.get("HF_TOKEN"), "the scoring child must run without a Hub token"
    req = json.loads(Path(a.request).read_text())
    pred = build_predictor(req, a.model_dir, device())
    labels = getattr(pred, "labels", None)
    out = {"device": device()}
    for name, path, summarize in (("public", a.public, public_summary), ("private", a.private, private_summary)):
        if not path:
            continue
        rows = load_rows(path)
        t0 = time.time()
        preds = pred([r["text"] for r in rows])
        ms = round((time.time() - t0) * 1000 / max(1, len(rows)), 2)
        out[name] = {**summarize(score_predictions(rows, preds, labels)), "ms_per_row": ms, "rows": len(rows)}
        del rows, preds
        print(name, json.dumps(out[name]["overall"]), "ms/row", ms, flush=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
