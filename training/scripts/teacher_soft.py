"""Teacher soft labels for knowledge distillation (character-aligned KD).

Teachers and students use different tokenizers, so the teacher's output is stored per character span, not per token:
for every teacher token whose probability of O (at T=1) is below --keep, one entry [start, end, q] with q = the class probabilities
(O + the model's own labels from its tags.json, B/I summed: 11 classes for a d12 model, 14 for a v1 model) at temperature --T, quantised to 0..255; every other character is O with probability 1.
Long texts use the same overlapping windows as evaluate.py (the window where the token sits farther from the edge wins).
train_ner.py --soft <file> [<file> ...] averages one or more teachers (ensemble KD) onto the student's own tokens.
Usage:
  python scripts/teacher_soft.py --model models/teachers/<teacher>/final --data data/train.jsonl --out data/soft/<teacher>.train.jsonl.gz
"""
import argparse, gzip, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import TaggerPredictor  # noqa: E402
from train_ner import labels_of  # noqa: E402


def classes_of(tags):
    """The model's own tag list (tags.json) → (classes, tag index → class index). Classes = O + labels in tag order (B/I summed),
    so a d12 teacher gives 11 classes and a v1 teacher 14; train_ner.load_soft checks them against the student's labels."""
    labels = labels_of(tags)
    return ["O"] + labels, [0] + [1 + labels.index(t.split("-", 1)[1]) for t in tags[1:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--T", type=float, default=2.0)
    ap.add_argument("--keep", type=float, default=0.99, help="store tokens whose P(O) at T=1 is below this")
    ap.add_argument("--bs", type=int, default=128)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "mps")
    a = ap.parse_args()
    P = TaggerPredictor(a.model, a.device)
    torch = P.torch
    CLASSES, TAG2CLASS = classes_of(P.tags)
    M = torch.zeros(len(P.tags), len(CLASSES), device=a.device)
    M[torch.arange(len(P.tags)), torch.tensor(TAG2CLASS)] = 1
    texts = [json.loads(l)["text"] for l in Path(a.data).read_text().splitlines() if l.strip()]
    texts = texts[:a.limit] if a.limit else texts
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))   # similar lengths per batch: little padding
    from textnorm import normalize, to_original
    res, t0 = {}, time.time()
    for b0 in range(0, len(order), a.bs):
        ids = order[b0:b0 + a.bs]
        batch = [texts[i] for i in ids]
        normed = [normalize(t) for t in batch] if P.normalize else None
        enc = P.tok([n for n, _ in normed] if normed else batch, truncation=True, max_length=P.WINDOW, stride=P.STRIDE,
                    return_overflowing_tokens=True, padding=True, return_offsets_mapping=True, return_tensors="pt")
        offs = enc.pop("offset_mapping").tolist()
        owner = enc.pop("overflow_to_sample_mapping").tolist()
        if normed:
            offs = [to_original(o, normed[j][1]) for o, j in zip(offs, owner)]
        with torch.no_grad():
            o = P.model(**{k: v.to(a.device) for k, v in enc.items()})
            logits = o["logits"] if isinstance(o, dict) else o.logits
            sure_o = ((torch.softmax(logits.float(), -1) @ M)[..., 0] >= a.keep).cpu().tolist()
            probs = (torch.softmax(logits.float() / a.T, -1) @ M).cpu().tolist()
        best = [dict() for _ in batch]
        for off, pr, so, j in zip(offs, probs, sure_o, owner):
            toks = [(k, s, e) for k, (s, e) in enumerate(off) if s != e]
            for rank, (k, s, e) in enumerate(toks):
                d = min(rank, len(toks) - 1 - rank)
                if s not in best[j] or d > best[j][s][0]:
                    best[j][s] = (d, e, None if so[k] else pr[k])
        for i, bj in zip(ids, best):
            res[i] = [[s, e, [round(p * 255) for p in pr]] for s, (_, e, pr) in sorted(bj.items()) if pr is not None]
        if b0 // a.bs % 200 == 0:
            print(f"{b0 + len(ids)}/{len(texts)} rows · {time.time() - t0:.0f}s", flush=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(a.out, "wt") as f:
        f.write(json.dumps({"meta": {"model": a.model, "data": Path(a.data).name, "rows": len(texts), "T": a.T,
                                     "keep": a.keep, "classes": CLASSES}}) + "\n")
        for i in range(len(texts)):
            f.write(json.dumps({"i": i, "s": res[i]}) + "\n")
    n = sum(len(v) for v in res.values())
    print(f"done: {len(texts)} rows, {n} stored token spans ({n / max(1, len(texts)):.1f}/row) · {time.time() - t0:.0f}s → {a.out}")


if __name__ == "__main__":
    main()
