"""Checkpoint selection on the validation set.

For every candidate with saved predictions (reports/preds/<tag>/<set>.jsonl) on the same set:
  C = 100 · mean over languages ℓ of [ mean over labels k of M(ℓ,k) + O(ℓ) ]
      M(ℓ,k) = share of non-space gold characters of label k in language ℓ left unmasked (any prediction covers them),
      O(ℓ)   = share of non-PII characters masked in language ℓ;
      a (ℓ, k) cell counts only with at least --min-chars gold characters (fixed before looking at candidates).
  Language- and label-macro, so long SECRET strings and long-text languages do not dominate. The micro miss + over-masking
  (the reported headline numbers) are printed next to it.
Rule: among candidates with C ≤ C_min + --tie (percentage points, unrounded), choose the highest language-macro exact F1
(start, end and label identical). Candidates should be compared per architecture.
Usage: python scripts/select_ckpt.py --set <validation set> --tags <tag1> <tag2> … [--tie 0.05] [--min-chars 50]
"""
import argparse, json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def stats(rows, preds, min_chars):
    gold = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # lang → label → [chars, missed]
    over = defaultdict(lambda: [0, 0])                        # lang → [non-PII chars, masked]
    ex = defaultdict(lambda: [0, 0, 0])                       # lang → exact tp, fp, fn
    tot = [0, 0, 0, 0]                                        # pii chars, missed, non-PII chars, masked
    for r, p in zip(rows, preds):
        t, lang, n = r["text"], r.get("lang", "?"), len(r["text"])
        cover = [False] * n
        for s in p:
            for i in range(max(0, s["start"]), min(s["end"], n)):
                cover[i] = True
        is_pii = [False] * n
        for s in r["spans"]:
            for i in range(s["start"], min(s["end"], n)):
                if t[i].isspace():
                    continue
                is_pii[i] = True
                g = gold[lang][s["label"]]
                g[0] += 1; g[1] += not cover[i]
                tot[0] += 1; tot[1] += not cover[i]
        for i in range(n):
            if not is_pii[i] and not t[i].isspace():
                over[lang][0] += 1; over[lang][1] += cover[i]
                tot[2] += 1; tot[3] += cover[i]
        gs = {(s["start"], s["end"], s["label"]) for s in r["spans"]}
        ps = {(s["start"], s["end"], s["label"]) for s in p}
        e = ex[lang]; e[0] += len(gs & ps); e[1] += len(ps - gs); e[2] += len(gs - ps)
    per_lang = []
    for lang in sorted(gold):
        ms = [m / c for c, m in gold[lang].values() if c >= min_chars]
        if not ms:
            continue
        o = over[lang][1] / max(1, over[lang][0])
        tp, fp, fn = ex[lang]
        f1 = 2 * tp / max(1, 2 * tp + fp + fn)
        per_lang.append((lang, sum(ms) / len(ms), o, f1))
    C = 100 * sum(m + o for _, m, o, _ in per_lang) / len(per_lang)
    f1_macro = sum(f for *_, f in per_lang) / len(per_lang)
    return {"C": C, "f1_macro": f1_macro, "miss_micro": 100 * tot[1] / max(1, tot[0]), "over_micro": 100 * tot[3] / max(1, tot[2]),
            "langs": len(per_lang)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="v1_val")
    ap.add_argument("--tags", nargs="+", required=True)
    ap.add_argument("--tie", type=float, default=0.05)
    ap.add_argument("--min-chars", type=int, default=50)
    a = ap.parse_args()
    rows = [json.loads(l) for l in (ROOT / f"data/eval/{a.set}.jsonl").read_text().splitlines() if l.strip()]
    res = {}
    for tag in a.tags:
        f = ROOT / f"reports/preds/{tag}/{a.set}.jsonl"
        if not f.exists():
            print(f"skip {tag}: no predictions"); continue
        pr = {json.loads(l)["i"]: json.loads(l)["spans"] for l in f.read_text().splitlines() if l.strip()}
        if len(pr) != len(rows):
            print(f"skip {tag}: {len(pr)} predictions for {len(rows)} rows"); continue
        res[tag] = stats(rows, [pr[i] for i in range(len(rows))], a.min_chars)
    cmin = min(v["C"] for v in res.values())
    print(f"| candidate | C (macro miss+over, pp) | lang-macro exact F1 | micro miss % | micro over % |\n|---|---|---|---|---|")
    for tag, v in sorted(res.items(), key=lambda kv: kv[1]["C"]):
        print(f"| {tag} | {v['C']:.4f} | {v['f1_macro']:.4f} | {v['miss_micro']:.3f} | {v['over_micro']:.3f} |")
    pool = {k: v for k, v in res.items() if v["C"] <= cmin + a.tie}
    best = max(pool, key=lambda k: pool[k]["f1_macro"])
    print(f"\nC_min {cmin:.4f}; within +{a.tie} pp: {sorted(pool)}; selected (highest lang-macro exact F1): **{best}**")
    (ROOT / f"reports/select_{a.set}.json").write_text(json.dumps({"results": res, "pool": sorted(pool), "selected": best,
                                                                    "tie": a.tie, "min_chars": a.min_chars}, indent=1))


if __name__ == "__main__":
    main()
