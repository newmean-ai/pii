"""Builds the web model folders that the JavaScript package downloads (2026-10-30).

Usage: python scripts/export_web.py [--out models/web] [--tiers large medium small nano]

For each tier, models/onnx/s-<tier>-v1/ (export_onnx.py output) becomes <out>/pii-<tier>/:
  onnx/model_q4.onnx, onnx/model_q4e.onnx   4-bit weights; q4e also stores the embedding table in 4 bits
  tokenizer.web.json                        compact Unigram tokenizer for the browser (pieces, float32 scores, the
                                            SentencePiece normalization map) — about a third of tokenizer.json
  tokenizer.json, tokenizer_config.json     the Hugging Face tokenizer, for Python users
  config.json                               labels and window settings
and <out>/manifest.json lists every file with its size and SHA-256 (the package pins these hashes).
"""
import argparse
import base64
import hashlib
import json
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["onnx/model_q4.onnx", "onnx/model_q4e.onnx", "tokenizer.web.json", "tokenizer.json", "tokenizer_config.json", "config.json"]


def web_tokenizer(tok: dict) -> dict:
    m = tok["model"]
    assert m["type"] == "Unigram" and not m.get("byte_fallback"), "only Unigram without byte fallback is supported"
    assert tok["normalizer"]["type"] == "Precompiled"
    pre = tok["pre_tokenizer"]["pretokenizers"]
    assert [p["type"] for p in pre] == ["WhitespaceSplit", "Metaspace"] and pre[1]["prepend_scheme"] == "always" and pre[1]["split"]
    pieces = [p for p, _ in m["vocab"]]
    assert not any("\n" in p for p in pieces)
    special = {t["content"]: t["id"] for t in tok["added_tokens"]}
    return {
        "format": 1, "type": "unigram",
        "unk_id": m["unk_id"], "bos_id": special["<s>"], "eos_id": special["</s>"], "pad_id": special["<pad>"],
        # matched literally in the raw text before normalization, as Hugging Face tokenizers does
        "special": [{"content": t["content"], "id": t["id"], "lstrip": t["lstrip"], "rstrip": t["rstrip"]} for t in tok["added_tokens"]],
        "pieces": "\n".join(pieces),
        "scores": base64.b64encode(struct.pack(f"<{len(pieces)}f", *[s for _, s in m["vocab"]])).decode(),
        "charsmap": tok["normalizer"]["precompiled_charsmap"],
    }


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="models/web")
    ap.add_argument("--tiers", nargs="+", default=["large", "medium", "small", "nano"])
    ap.add_argument("--version", default="1.0.0")
    a = ap.parse_args()
    out = ROOT / a.out
    manifest = {"version": a.version, "tiers": {}}
    for tier in a.tiers:
        src, dst = ROOT / f"models/onnx/s-{tier}-v1", out / f"pii-{tier}"
        (dst / "onnx").mkdir(parents=True, exist_ok=True)
        for f in ["onnx/model_q4.onnx", "onnx/model_q4e.onnx", "tokenizer.json", "tokenizer_config.json"]:
            shutil.copyfile(src / f, dst / f)
        tok = json.loads((src / "tokenizer.json").read_text())
        (dst / "tokenizer.web.json").write_text(json.dumps(web_tokenizer(tok), ensure_ascii=False, separators=(",", ":")))
        cfg = json.loads((src / "config.json").read_text())
        tags = json.loads((src / "tags.json").read_text())["tags"]
        assert [cfg["id2label"][str(i)] for i in range(len(tags))] == tags
        (dst / "config.json").write_text(json.dumps({
            "model": f"pii-{tier}", "version": a.version, "tags": tags, "id2label": dict(enumerate(tags)),
            "window": 256, "stride": 64,   # training length (train_ner --max_len 256); overlap used by evaluate.py
            "architecture": {"hidden_size": cfg["hidden_size"], "layers": cfg["num_hidden_layers"], "pii_arch": cfg.get("pii_arch")},
        }, indent=2) + "\n")
        manifest["tiers"][tier] = {f: {"bytes": (dst / f).stat().st_size, "sha256": sha256(dst / f)} for f in FILES}
        print(tier, {f: v["bytes"] for f, v in manifest["tiers"][tier].items()})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
