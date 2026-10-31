"""Paired comparison of two detectors on one eval set from saved predictions.

Metrics per model: character miss rate (leak), over-masking, entity-level miss rate, overlap F1 and exact-boundary F1.
Paired bootstrap over rows (resampling the same rows for both models, n=1000) gives a 95% interval for the difference
A − B of leak, entity leak and exact F1 — an interval that excludes 0 means the gap is not resampling noise.
Usage: python scripts/compare_models.py --set kdpii_test --a <tag A> --b <tag B>
  (tags as passed to evaluate.py --tag; predictions are read from reports/preds/<tag>/<set>.jsonl)
"""
import argparse, json, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def load(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--n", type=int, default=1000)
    a = ap.parse_args()
    rows = load(ROOT / f"data/eval/{a.set}.jsonl")
    PA = [x["spans"] for x in load(ROOT / f"reports/preds/{a.a}/{a.set}.jsonl")]
    PB = [x["spans"] for x in load(ROOT / f"reports/preds/{a.b}/{a.set}.jsonl")]
    assert len(PA) == len(PB) == len(rows)
    keys = ("leak", "overmask", "entity_leak", "span_f1", "exact_f1")
    full = {m: score(rows, P)["ALL"] for m, P in ((a.a, PA), (a.b, PB))}
    # per-row counts once, then resample sums (fast: score() per resample took minutes on 11k rows)
    def counts(P):
        out = []
        for r, p in zip(rows, P):
            s = score([r], [p])["ALL"]
            ch = sum(1 for sp in r["spans"] for i in range(sp["start"], min(sp["end"], len(r["text"]))) if not r["text"][i].isspace())
            ent = sum(1 for sp in r["spans"] if r["text"][sp["start"]:sp["end"]].strip())
            g = {(x["start"], x["end"], x["label"]) for x in r["spans"]}; q = {(x["start"], x["end"], x["label"]) for x in p}
            out.append((ch, (s["leak"] or 0) * ch, ent, (s["entity_leak"] or 0) * ent, len(g & q), len(q - g), len(g - q)))
        return out
    CA, CB = counts(PA), counts(PB)
    def agg(C, idx):
        t = [sum(C[i][k] for i in idx) for k in range(7)]
        f1 = 2 * t[4] / (2 * t[4] + t[5] + t[6]) if t[4] else 0.0
        return {"leak": t[1] / t[0] if t[0] else 0, "entity_leak": t[3] / t[2] if t[2] else 0, "exact_f1": f1}
    rng = random.Random(1)
    diffs = {k: [] for k in ("leak", "entity_leak", "exact_f1")}
    for _ in range(a.n):
        idx = [rng.randrange(len(rows)) for _ in rows]
        sa, sb = agg(CA, idx), agg(CB, idx)
        for k in diffs:
            diffs[k].append(sa[k] - sb[k])
    ci = {k: (sorted(v)[int(0.025 * len(v))], sorted(v)[int(0.975 * len(v))]) for k, v in diffs.items()}
    print(f"{a.set}: rows {len(rows)}")
    for m, s in full.items():
        print(f"  {m:24s} " + " ".join(f"{k} {s[k]:.4f}" for k in keys))
    for k, (lo, hi) in ci.items():
        print(f"  {a.a} − {a.b} {k}: {full[a.a][k] - full[a.b][k]:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]" + ("  *" if lo > 0 or hi < 0 else ""))


if __name__ == "__main__":
    main()
