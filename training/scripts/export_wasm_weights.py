"""Converts the web ONNX models into the weight file of the SDK's built-in WebAssembly engine.

Usage: python scripts/export_wasm_weights.py [--src models/web] [--out models/custom-wasm] [--tiers large medium small nano]

For each tier and variant, <src>/pii-<tier>/onnx/model_<v>.onnx becomes <out>/pii-<tier>/model_<v>.piiw.
The engine is js/engine/pii.c; this file defines the format it reads. The SDK also builds the same image in memory from
the published ONNX files (js/src/engines/onnx-weights.ts), so no .piiw step is needed at runtime.

Format "PIIW" version 1, little-endian, every tensor 16-byte aligned:
  header  u32 magic 'PIIW', version, hidden, heads, intermediate, n_tags, vocab, emb_dim, emb_kind, n_layers,
          n_apply, pad_id, max_pos, f32 ln_eps, then n_apply u32 layer indices (the order the stored layers run in;
          nano stores one layer and runs it 4 times), padded to 16 bytes
  tensors in this order:
    word embedding [vocab, emb_dim], position embedding [max_pos, hidden], token type embedding [1, hidden]
        emb_kind 1 (q4 file):  f32 scale, f32 zero point, then u8 data            (DequantizeLinear, per tensor)
        emb_kind 2 (q4e file): f32 scales [rows, cols/32], then int4 data, two per byte, low nibble first
                               (GatherBlockQuantized, signed int4, no zero point)
    proj (only if emb_dim != hidden): Q4 matrix K=emb_dim, N=hidden, no bias
    embedding LayerNorm gamma, beta
    per stored layer: q W,b; k W,b; v W,b; attention output W,b; LN1 gamma,beta; intermediate W,b; output W,b; LN2 gamma,beta
    classifier W (N=n_tags, padded to 8 columns with zero weights), bias
  Q4 matrix (MatMulNBits, 4 bits, block 32 along K, zero point 8, no zero-point tensor): the same values and scales,
    re-laid out for the engine's kernel: for each panel of 8 output columns and each 32-row block of K:
    f32 scales[8] (one per column), then 32 rows x 4 bytes; byte j of row k holds column j in the low nibble and
    column j+4 in the high nibble, as the signed value q-8 (so the engine needs no zero-point subtraction).
    Same byte count as the ONNX initializers (4 bytes of scale + 16 bytes of nibbles per column and block).
The engine accepts only version 1 with embedding kind 1 or 2.
"""
import argparse
import re
import struct
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper

ROOT = Path(__file__).resolve().parent.parent
ROLE = re.compile(r"layer\.(\d+)/(?:block/)?(attention/self/(?:query|key|value)|attention/output/dense|intermediate/dense|output/dense)(?:_\d+)?/MatMul")
ROLES = ["attention/self/query", "attention/self/key", "attention/self/value", "attention/output/dense", "intermediate/dense", "output/dense"]


def unpack_u4(packed: np.ndarray, n: int) -> np.ndarray:
    """uint8 [..., n/2] -> uint8 [..., n], low nibble first."""
    lo, hi = packed & 0x0F, packed >> 4
    return np.stack([lo, hi], axis=-1).reshape(*packed.shape[:-1], -1)[..., :n]


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def align(self):
        self.buf += b"\0" * (-len(self.buf) % 16)

    def raw(self, b: bytes):
        self.align()
        self.buf += b

    def f32(self, a):
        self.raw(np.ascontiguousarray(a, dtype="<f4").tobytes())


def q4_matrix(w: Writer, b_q4: np.ndarray, scales: np.ndarray, K: int, N: int):
    """MatMulNBits B [N, K/32, 16] uint8 + scales [N * K/32] -> panel layout (see module docstring)."""
    nb = K // 32
    assert K % 32 == 0 and b_q4.shape == (N, nb, 16), (b_q4.shape, N, K)
    q = unpack_u4(b_q4.reshape(N, nb, 16), 32).astype(np.int16) - 8          # [N, nb, 32] in -8..7
    s = scales.reshape(N, nb).astype(np.float32)
    Np = (N + 7) // 8 * 8
    if Np != N:
        q = np.concatenate([q, np.zeros((Np - N, nb, 32), np.int16)])
        s = np.concatenate([s, np.zeros((Np - N, nb), np.float32)])
    out = bytearray()
    for p in range(Np // 8):
        qp = q[p * 8:(p + 1) * 8]                                             # [8, nb, 32]
        for b in range(nb):
            out += s[p * 8:(p + 1) * 8, b].astype("<f4").tobytes()
            blk = qp[:, b, :].T & 0x0F                                       # [32 rows k, 8 cols]
            out += (blk[:, :4] | (blk[:, 4:] << 4)).astype(np.uint8).tobytes()
    w.raw(bytes(out))


def convert(src: Path, dst: Path):
    m = onnx.load(str(src))
    g = m.graph
    init = {i.name: i for i in g.initializer}
    arr = lambda name: numpy_helper.to_array(init[name])
    nodes = list(g.node)

    def nbits(node):
        at = {a.name: onnx.helper.get_attribute_value(a) for a in node.attribute}
        assert at["bits"] == 4 and at["block_size"] == 32 and len(node.input) == 3 and at.get("accuracy_level", 0) == 0, at
        return node.input[1], node.input[2], at["K"], at["N"]

    # layers: (layer index, role) -> MatMulNBits weights; run order from the query projections
    mats, order = {}, []
    proj = cls = None
    for n in nodes:
        if n.op_type != "MatMulNBits":
            continue
        out = n.output[0]
        r = ROLE.search(out)
        if r:
            key = (int(r.group(1)), r.group(2))
            mats.setdefault(key, nbits(n))
            if r.group(2) == "attention/self/query":
                order.append(int(r.group(1)))
        elif "/word_embeddings/proj/" in out:
            proj = nbits(n)
        elif "/classifier/" in out:
            cls = nbits(n)
        else:
            raise ValueError(f"unknown MatMulNBits {out}")
    layers = sorted({k[0] for k in mats})
    assert layers == list(range(len(layers))) and all((l, r) in mats for l in layers for r in ROLES)
    prefix = "m.roberta.encoder.layer.{}." + ("block." if any(k.startswith("m.roberta.encoder.layer.0.block.") for k in init) else "")

    hidden = mats[(0, "attention/self/query")][3]
    inter = mats[(0, "intermediate/dense")][3]
    n_tags = cls[3]
    heads_const = None
    for n in nodes:  # head size from the q reshape constant ([-1] then [head_size])
        if n.op_type == "Constant" and "/attention/self/Constant_1_output_0" in n.output[0]:
            heads_const = int(numpy_helper.to_array(n.attribute[0].t).reshape(-1)[0])
            break
    heads = hidden // heads_const
    eps = {onnx.helper.get_attribute_value(a) for n in nodes if n.op_type == "LayerNormalization" for a in n.attribute if a.name == "epsilon"}
    assert len(eps) == 1
    eps = eps.pop()
    pad_id = None
    for n in nodes:  # padding_idx: Equal(input_ids, const) in the embeddings
        if n.op_type == "Equal" and n.input[0] == "input_ids":
            c = next(x for x in nodes if x.output and x.output[0] == n.input[1])
            pad_id = int(numpy_helper.to_array(c.attribute[0].t))
    assert pad_id is not None

    emb = "m.roberta.embeddings.word_embeddings.emb.weight" if proj else "m.roberta.embeddings.word_embeddings.weight"
    if emb + "_Q4" in init:
        kind = 2
        word = init[emb + "_Q4"]
    else:
        kind = 1
        word = init[emb + "_quantized"]
    vocab, emb_dim = word.dims
    max_pos = (init.get("m.roberta.embeddings.position_embeddings.weight_Q4") or init["m.roberta.embeddings.position_embeddings.weight_quantized"]).dims[0]

    w = Writer()
    hdr = struct.pack("<4s12I", b"PIIW", 1, hidden, heads, inter, n_tags, vocab, emb_dim, kind, len(layers), len(order), pad_id, max_pos)
    hdr += struct.pack("<f", eps) + struct.pack(f"<{len(order)}I", *order)
    w.raw(hdr)

    def emb_table(base, rows, cols):
        if kind == 1:
            q = arr(base + "_quantized")
            assert q.dtype == np.uint8 and q.shape == (rows, cols)
            w.f32([float(arr(base + "_scale")), float(arr(base + "_zero_point"))])
            w.raw(q.tobytes())
        else:
            t = init[base + "_Q4"]
            assert t.data_type == onnx.TensorProto.INT4 and list(t.dims) == [rows, cols]
            raw = t.raw_data
            assert len(raw) == rows * cols // 2
            sc = arr(base + "_scales")
            assert sc.shape == (rows, cols // 32)
            w.f32(sc)
            w.raw(raw)

    emb_table(emb, vocab, emb_dim)
    emb_table("m.roberta.embeddings.position_embeddings.weight", max_pos, hidden)
    emb_table("m.roberta.embeddings.token_type_embeddings.weight", 1, hidden)

    def mat(spec):
        b, s, K, N = spec
        q4_matrix(w, arr(b), arr(s), K, N)

    if proj:
        assert proj[2] == emb_dim and proj[3] == hidden
        mat(proj)
    w.f32(arr("m.roberta.embeddings.LayerNorm.weight"))
    w.f32(arr("m.roberta.embeddings.LayerNorm.bias"))
    names = {"attention/self/query": "attention.self.query", "attention/self/key": "attention.self.key",
             "attention/self/value": "attention.self.value", "attention/output/dense": "attention.output.dense",
             "intermediate/dense": "intermediate.dense", "output/dense": "output.dense"}
    for l in layers:
        p = prefix.format(l)
        for r in ROLES:
            mat(mats[(l, r)])
            w.f32(arr(p + names[r] + ".bias"))
            if r == "attention/output/dense":
                w.f32(arr(p + "attention.output.LayerNorm.weight"))
                w.f32(arr(p + "attention.output.LayerNorm.bias"))
        w.f32(arr(p + "output.LayerNorm.weight"))
        w.f32(arr(p + "output.LayerNorm.bias"))
    mat(cls)
    w.f32(arr("m.classifier.bias"))
    w.align()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(w.buf))
    return dict(hidden=hidden, heads=heads, inter=inter, tags=n_tags, vocab=vocab, emb_dim=emb_dim, kind=kind,
                layers=len(layers), order=order, pad=pad_id, eps=eps, bytes=len(w.buf), onnx_bytes=src.stat().st_size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="models/web")
    ap.add_argument("--out", default="models/custom-wasm")
    ap.add_argument("--tiers", nargs="+", default=["large", "medium", "small", "nano"])
    a = ap.parse_args()
    for t in a.tiers:
        for v in ["q4", "q4e"]:
            info = convert(ROOT / a.src / f"pii-{t}/onnx/model_{v}.onnx", ROOT / a.out / f"pii-{t}/model_{v}.piiw")
            print(f"pii-{t} {v}: {info}")


if __name__ == "__main__":
    main()
