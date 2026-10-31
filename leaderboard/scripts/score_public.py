"""Score your own predictions on the public test set right away, with the leaderboard's metric (no submission needed).

  python scripts/score_public.py --test data/test.jsonl --preds preds.jsonl [--labels PERSON EMAIL … | --labels auto]
preds.jsonl: one line per test row, in order or with "id": {"id": "pmt-00000", "spans": [{"start": 3, "end": 9, "label": "PERSON"}]}
Labels are ours (config.LABELS, or OTHER). --labels: the label set your detector can output; unless it contains PERSON_L2 or
URL_L2 the gold is collapsed to PERSON / URL (as on the leaderboard). Prints the public summary as JSON.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pii_leaderboard.evaluation import load_rows, public_summary, score_predictions  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", required=True, help="data/test.jsonl of newmean-ai/pii-dataset")
    ap.add_argument("--preds", required=True)
    ap.add_argument("--labels", nargs="+", default=["auto"])
    a = ap.parse_args()
    rows = load_rows(a.test)
    ids = [json.loads(l)["id"] for l in open(a.test, encoding="utf-8") if l.strip()]
    got = [json.loads(l) for l in open(a.preds, encoding="utf-8") if l.strip()]
    if got and "id" in got[0]:
        by_id = {p["id"]: p["spans"] for p in got}
        preds = [by_id.get(i, []) for i in ids]
    else:
        preds = [p["spans"] for p in got]
    labels = None if a.labels == ["auto"] else a.labels
    print(json.dumps(public_summary(score_predictions(rows, preds, labels)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
