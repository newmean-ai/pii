"""Shrink the XLM-R vocabulary of a multilingual MiniLM to the pieces our 54 languages use, keeping the pretrained embeddings
exactly (2026-10-29; the approach of Mozilla smart-pii-redactor v1: mMiniLMv2-L6-H384 with a 20k XLM-R vocabulary).

Replacing the vocabulary with our own SentencePiece and averaged sub-piece embeddings lost 4–9 points on the external sets;
pruning keeps every kept piece's trained embedding.
Kept pieces = special tokens + every single-character piece whose character occurs in the corpus (7,243 for our training data — XLM-R has no
byte fallback, so a dropped character would become <unk>) + the N most frequent pieces on a language-balanced corpus.
Token inflation vs the full vocabulary on our validation set: N=20k +26%, 30k +17%, 40k +12%, 60k +6%.
Usage: python scripts/prune_xlmr.py --max 30000 --out models/students/p35-init
"""
import argparse, collections, json, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=30000, help="most frequent pieces kept (single characters come on top)")
    ap.add_argument("--train", default=str(ROOT / "data/train.jsonl"),
                    help="training JSONL with text and lang fields (the corpus the kept pieces are counted on)")
    ap.add_argument("--per_lang", type=int, default=6000)
    a = ap.parse_args()
    import torch
    from tokenizers import Tokenizer
    from transformers import AutoModel, AutoTokenizer, PreTrainedTokenizerFast
    tok = AutoTokenizer.from_pretrained(a.base)
    by = collections.defaultdict(list)
    for l in open(a.train):
        r = json.loads(l)
        by[r["lang"]].append(r["text"])
    rng = random.Random(3)
    corpus = [t for v in by.values() for t in rng.sample(v, min(a.per_lang, len(v)))]
    cnt = collections.Counter()
    for t in corpus:
        cnt.update(tok(t, add_special_tokens=False)["input_ids"])
    chars = set("".join(corpus))
    tj = json.loads(tok.backend_tokenizer.to_str())
    vocab = tj["model"]["vocab"]
    singles = {i for i, (p, _) in enumerate(vocab) if len(p.replace("▁", "")) == 1 and p.replace("▁", "") in chars}
    keep = sorted(set(tok.all_special_ids) | singles | {i for i, _ in cnt.most_common(a.max)})
    o2n = {o: n for n, o in enumerate(keep)}
    tj["model"]["vocab"] = [vocab[i] for i in keep]
    tj["model"]["unk_id"] = o2n[tj["model"]["unk_id"]]
    for at in tj.get("added_tokens", []):
        at["id"] = o2n[at["id"]]
    pp = tj.get("post_processor") or {}
    for k in ("cls", "sep"):
        if k in pp:
            pp[k][1] = o2n[pp[k][1]]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tokenizer.json").write_text(json.dumps(tj, ensure_ascii=False))
    new_tok = PreTrainedTokenizerFast(tokenizer_object=Tokenizer.from_file(str(out / "tokenizer.json")),
                                      bos_token="<s>", eos_token="</s>", cls_token="<s>", sep_token="</s>", unk_token="<unk>",
                                      pad_token="<pad>", mask_token="<mask>")
    new_tok.save_pretrained(out)
    model = AutoModel.from_pretrained(a.base, dtype=torch.float32)
    emb = model.get_input_embeddings().weight.data[keep].clone()
    model.resize_token_embeddings(len(keep))
    model.get_input_embeddings().weight.data = emb
    model.config.vocab_size = len(keep)
    model.config.pad_token_id = o2n[tok.pad_token_id]
    model.config.bos_token_id, model.config.eos_token_id = o2n[tok.bos_token_id], o2n[tok.eos_token_id]
    model.save_pretrained(out)
    # sanity: same text, offsets inside the text, pad/cls/sep where the model expects them
    s = "전화 010-1234-5678, Ελένη kim@example.com ‌می‌خواهم"
    enc = new_tok(s, return_offsets_mapping=True)
    assert enc["input_ids"][0] == o2n[tok.cls_token_id] and enc["input_ids"][-1] == o2n[tok.sep_token_id]
    assert new_tok.pad_token_id == model.config.pad_token_id
    n = sum(p.numel() for p in model.parameters())
    e = model.get_input_embeddings().weight.numel()
    body = n - e
    print(f"{out.name}: vocab {len(vocab)} → {len(keep)} ({len(singles)} single characters) · params {n / 1e6:.1f}M "
          f"(embeddings {e / 1e6:.1f}M) · int8 embeddings + 4-bit body ≈ {e / 1e6 + body * 0.5625 / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
