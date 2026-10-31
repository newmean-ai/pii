"""Export a train_ner.py model to ONNX for the browser and quantize it the way small browser PII models ship (2026-10-29):
4-bit block-wise weights for every MatMul (MatMulNBits, block 32 — Rampart / Rampart-edge / Mozilla smart-pii-redactor q4)
and int8 word embeddings (Mozilla keeps embeddings at int8: "costs about 0.1 points of leak recall against fp32").
Writes <out>/onnx/model.onnx (fp32), <out>/onnx/model_q4.onnx and the tokenizer, config and tags next to them, so
evaluate.py --model onnx:<out> (fp32) / onnx:<out>:model_q4.onnx scores exactly the file that ships.
Usage: python scripts/export_onnx.py --model models/students/s-p35-kd2/final --out models/onnx/s-p35-kd2
"""
import argparse, json, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def split_shared(m):
    """A weight read by several MatMul nodes (the nano tier's shared layer, unrolled four times) is skipped by MatMulNBits and
    stays fp32 — give every consumer its own copy so each is quantized; dedupe() merges the identical results afterwards."""
    from collections import defaultdict
    from onnx import numpy_helper
    inits = {i.name: i for i in m.graph.initializer}
    # the exporter feeds a weight that is used again through Identity nodes — point those consumers at the weight itself
    ident = {n.output[0]: n.input[0] for n in m.graph.node if n.op_type == "Identity" and n.input[0] in inits}
    if ident:
        for n in m.graph.node:
            for k, x in enumerate(n.input):
                if x in ident:
                    n.input[k] = ident[x]
        keep = [n for n in m.graph.node if not (n.op_type == "Identity" and n.output[0] in ident)]
        del m.graph.node[:]
        m.graph.node.extend(keep)
    users = defaultdict(list)
    for n in m.graph.node:
        if n.op_type == "MatMul":
            for k, x in enumerate(n.input):
                if x in inits:
                    users[x].append((n, k))
    for name, us in users.items():
        if len(us) < 2:
            continue
        arr = numpy_helper.to_array(inits[name])
        for j, (n, k) in enumerate(us[1:], 1):
            copy = numpy_helper.from_array(arr, f"{name}__use{j}")
            m.graph.initializer.append(copy)
            n.input[k] = copy.name
    return m


def dedupe(m):
    """Merge initializers with identical dtype, shape and bytes (the per-use copies made by split_shared after quantization)."""
    import hashlib
    seen, alias, keep = {}, {}, []
    for i in m.graph.initializer:
        h = hashlib.sha256(i.raw_data or i.SerializeToString()).hexdigest() + str(i.data_type) + str(list(i.dims))
        if h in seen:
            alias[i.name] = seen[h]
        else:
            seen[h] = i.name
            keep.append(i)
    if not alias:
        return
    del m.graph.initializer[:]
    m.graph.initializer.extend(keep)
    for n in m.graph.node:
        for k, x in enumerate(n.input):
            if x in alias:
                n.input[k] = alias[x]
    print(f"dedupe: merged {len(alias)} identical initializers", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="a train_ner.py final/ folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--block", type=int, default=32)
    ap.add_argument("--emb4", action="store_true", help="also write model_q4e.onnx: embedding tables 4-bit too (GatherBlockQuantized; "
                    "runs in onnxruntime-web 1.31.1 wasm, checked 2026-10-29)")
    a = ap.parse_args()
    import onnx
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.matmul_nbits_quantizer import DefaultWeightOnlyQuantConfig, MatMulNBitsQuantizer
    from evaluate import TaggerPredictor
    src, out = Path(a.model), Path(a.out)
    (out / "onnx").mkdir(parents=True, exist_ok=True)
    P = TaggerPredictor(str(src), "cpu")
    model = P.model.eval()

    class Logits(torch.nn.Module):   # plain (input_ids, attention_mask) → logits graph
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            o = self.m(input_ids=input_ids, attention_mask=attention_mask)
            return o["logits"] if isinstance(o, dict) else o.logits

    ids = torch.tensor([P.tok("Kim Minji 010-1234-5678")["input_ids"]])
    fp32 = out / "onnx/model.onnx"
    torch.onnx.export(Logits(model), (ids, torch.ones_like(ids)), str(fp32), input_names=["input_ids", "attention_mask"],
                      output_names=["logits"], dynamic_axes={"input_ids": {0: "b", 1: "n"}, "attention_mask": {0: "b", 1: "n"},
                                                             "logits": {0: "b", 1: "n"}}, opset_version=17, dynamo=False)
    q = MatMulNBitsQuantizer(split_shared(onnx.load(str(fp32))), algo_config=DefaultWeightOnlyQuantConfig(block_size=a.block, is_symmetric=True))
    q.process()
    tmp = out / "onnx/model_mm4.onnx"
    dedupe(q.model.model)
    q.model.save_model_to_file(str(tmp), use_external_data_format=False)
    quantize_dynamic(str(tmp), str(out / "onnx/model_q4.onnx"), op_types_to_quantize=["Gather"], weight_type=QuantType.QUInt8)
    tmp.unlink()
    files = ["model.onnx", "model_q4.onnx"]
    if a.emb4:   # every matrix and every embedding table 4-bit, block 32 along the hidden axis
        cfg = DefaultWeightOnlyQuantConfig(block_size=a.block, is_symmetric=True, op_types_to_quantize=("MatMul", "Gather"),
                                           quant_axes=(("MatMul", 0), ("Gather", 1)))
        q = MatMulNBitsQuantizer(split_shared(onnx.load(str(fp32))), algo_config=cfg)
        q.process()
        dedupe(q.model.model)
        q.model.save_model_to_file(str(out / "onnx/model_q4e.onnx"), use_external_data_format=False)
        files.append("model_q4e.onnx")
    P.tok.save_pretrained(out)
    for f in ("config.json", "tags.json"):
        shutil.copy(src / f, out / f)
    for f in files:
        print(f"{f}: {(out / 'onnx' / f).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
