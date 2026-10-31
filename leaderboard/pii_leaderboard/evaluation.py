"""From test rows and predictions to leaderboard numbers, exactly as evaluate.main() scores one set (model only, no regex union):
trimmed prediction spans, gold collapsed to the detector's granularity unless it outputs *_L2 labels, then scoring.score().

Public test: overall, per label, per language, per level. Private test: overall only (private_summary drops everything else
before it leaves the scoring process; no per-row output is ever written for it).
"""
import json

from .scoring import collapse_gold, needs_native, score, trim_spans

OVERALL_KEYS = ("leak", "overmask", "span_f1", "exact_f1", "entity_leak", "entity_leak_strict", "strict_leak")
LANG_KEYS = ("leak", "overmask", "span_f1", "exact_f1", "entity_leak")


def to_scorer_row(row):
    """A test dataset row (published data/test.jsonl layout, see the dataset card) → the row shape score() reads. Level comes back from the subtype."""
    return {"text": row["text"], "lang": row["lang"], "source": "",
            "spans": [{"start": s["start"], "end": s["end"], "label": s["label"], "subtype": s.get("subtype")} for s in row["spans"]]}


def load_rows(path):
    with open(path, encoding="utf-8") as f:
        return [to_scorer_row(json.loads(l)) for l in f if l.strip()]


class _Labels:   # needs_native() reads a predictor's .labels
    def __init__(self, labels):
        self.labels = labels


def score_predictions(rows, preds, labels):
    """Full score() report for one set; labels = the detector's output label set (None if unknown)."""
    assert len(rows) == len(preds), (len(rows), len(preds))
    collapse = not needs_native(_Labels(labels))
    gold = collapse_gold(rows) if collapse else rows
    preds = [trim_spans(r["text"], p) for r, p in zip(gold, preds)]
    rep = score(gold, preds)
    rep["gold_collapsed"] = collapse
    return rep


def public_summary(rep):
    labels = sorted(set(rep["labels"]) | set(rep["leak_by_label"]))
    return {
        "overall": {k: rep["ALL"].get(k) for k in OVERALL_KEYS},
        "by_label": {l: {"span_f1": rep["labels"].get(l), "exact_f1": rep["exact_by_label"].get(l),
                         "leak": rep["leak_by_label"].get(l), "strict_leak": rep["strict_leak_by_label"].get(l)} for l in labels},
        "by_language": {k[5:]: {m: v.get(m) for m in LANG_KEYS} for k, v in sorted(rep.items()) if k.startswith("lang:")},
        "by_level": rep["levels"],
        "gold_collapsed": rep["gold_collapsed"],
    }


def private_summary(rep):
    return {"overall": {k: rep["ALL"].get(k) for k in OVERALL_KEYS}, "gold_collapsed": rep["gold_collapsed"]}
