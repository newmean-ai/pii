"""Compressed student tiers built from the pruned-vocabulary mMiniLMv2 student: four tiers nano / small /
medium / large; nano is also compared with and without per-loop LoRA, as in Relaxed Recursive Transformers (Bae et al., ICLR 2025).

A tier is a dict stored in config.json as `pii_arch`, so evaluate.py and export_onnx.py rebuild the same modules:
  emb_rank   factorised word embeddings: Embedding(V, r) → Linear(r, H), initialised from the SVD of the pretrained embeddings
             (keeps the r strongest directions of every kept piece's vector)
  layers     indices of the pretrained layers kept (structured layer dropping)
  shared     {"loops": n, "lora_rank": r, "sources": [...]}: one transformer layer applied n times. Its weights start as the
             average of the source layers; with lora_rank > 0 every loop adds its own low-rank update to each linear map
             (query, key, value, attention output, FFN in and out), initialised from the SVD of (source layer − average), so loop
             i starts close to source layer i and the tier stays within the nano budget.
Tiers (TIERS), ranks chosen to fill each size budget (size_report() at 4-bit):
large = no change (≈ 18.9 MB) · medium = emb_rank 192 (≈ 8.8 MB) · small = emb_rank 64 + 4 of the 6 layers (≈ 4.8 MB) ·
nano = emb_rank 32 + one shared layer × 4 (≈ 1.6 MB) · nano-lora = nano + rank-8 LoRA per loop (≈ 1.7 MB).
Released v1.0 models use the four plain tiers (large, medium, small, nano); the other entries are experimental.
"""
import torch
from torch import nn

TIERS = {
    "large": {},
    "medium": {"emb_rank": 192},
    "small": {"emb_rank": 64, "layers": [0, 2, 4, 5]},
    "nano": {"emb_rank": 32, "shared": {"loops": 4, "lora_rank": 0, "sources": [0, 2, 4, 5]}},
    "nano-lora": {"emb_rank": 32, "shared": {"loops": 4, "lora_rank": 8, "sources": [0, 2, 4, 5]}},
    # experimental: the shared layer applied six times — same size as nano, 1.5× the compute
    # (recursive depth, cf. Universal / Recursive Transformers), every pretrained layer averaged into the shared one
    "nano-x6": {"emb_rank": 32, "shared": {"loops": 6, "lora_rank": 0, "sources": [0, 1, 2, 3, 4, 5]}},
    # experimental: smaller points of the size/quality curve (ranks 96 / 32 / 16), to see how far size can drop for web use
    "medium-r96": {"emb_rank": 96},
    "small-r32": {"emb_rank": 32, "layers": [0, 2, 4, 5]},
    "nano-r16": {"emb_rank": 16, "shared": {"loops": 4, "lora_rank": 0, "sources": [0, 2, 4, 5]}},
    "nano-lora-r16": {"emb_rank": 16, "shared": {"loops": 4, "lora_rank": 8, "sources": [0, 2, 4, 5]}},
}
LORA_TARGETS = ["attention.self.query", "attention.self.key", "attention.self.value", "attention.output.dense",
                "intermediate.dense", "output.dense"]


class FactorEmbedding(nn.Module):
    def __init__(self, vocab, rank, hidden, padding_idx=None):
        super().__init__()
        self.emb = nn.Embedding(vocab, rank, padding_idx=padding_idx)
        self.proj = nn.Linear(rank, hidden, bias=False)

    def forward(self, ids):
        return self.proj(self.emb(ids))

    @property
    def weight(self):   # code that reads get_input_embeddings().weight (dtype, device) sees the full matrix
        return self.proj(self.emb.weight)


class LoRALinear(nn.Module):
    """A shared linear map plus one low-rank update per loop; `loop` is set by Looped before each pass."""

    def __init__(self, base, loops, rank):
        super().__init__()
        self.base, self.loop = base, 0
        self.down = nn.ModuleList(nn.Linear(base.in_features, rank, bias=False) for _ in range(loops))
        self.up = nn.ModuleList(nn.Linear(rank, base.out_features, bias=False) for _ in range(loops))
        for u in self.up:
            nn.init.zeros_(u.weight)

    def forward(self, x):
        return self.base(x) + self.up[self.loop](self.down[self.loop](x))


class Looped(nn.Module):
    """One transformer layer applied `loops` times (cross-layer parameter sharing)."""

    def __init__(self, block, loops):
        super().__init__()
        self.block, self.loops = block, loops

    def forward(self, hidden_states, *args, **kw):
        lora = [m for m in self.block.modules() if isinstance(m, LoRALinear)]
        for i in range(self.loops):
            for m in lora:
                m.loop = i
            hidden_states = self.block(hidden_states, *args, **kw)
        return hidden_states


def _get(mod, path):
    for p in path.split("."):
        mod = getattr(mod, p)
    return mod


def _set(mod, path, value):
    *head, last = path.split(".")
    setattr(_get(mod, ".".join(head)) if head else mod, last, value)


def apply_arch(model, arch):
    """Turns a standard XLM-R token classifier (config already carrying the tier's num_hidden_layers) into the tier's modules.
    Structure only — the weights come from build() or from a saved state dict."""
    enc = model.roberta
    cfg = model.config
    if arch.get("emb_rank"):
        we = enc.embeddings.word_embeddings
        if not isinstance(we, FactorEmbedding):
            enc.embeddings.word_embeddings = FactorEmbedding(we.num_embeddings, arch["emb_rank"], cfg.hidden_size, we.padding_idx)
    sh = arch.get("shared")
    if sh and not isinstance(enc.encoder.layer[0], Looped):
        block = enc.encoder.layer[0]
        if sh.get("lora_rank"):
            for t in LORA_TARGETS:
                _set(block, t, LoRALinear(_get(block, t), sh["loops"], sh["lora_rank"]))
        enc.encoder.layer = nn.ModuleList([Looped(block, sh["loops"])])
    return model


@torch.no_grad()
def build(model, arch):
    """Pretrained full student → tier, with the initialisations described above. Returns the new model (same object)."""
    if not arch:
        return model
    import copy
    enc = model.roberta
    src_emb = enc.embeddings.word_embeddings.weight.detach().clone()
    layers = list(enc.encoder.layer)
    sh = arch.get("shared")
    if sh:
        srcs = [layers[i] for i in sh["sources"]]
        block = copy.deepcopy(srcs[0])
        avg = {k: torch.stack([dict(s.named_parameters())[k] for s in srcs]).mean(0) for k, _ in block.named_parameters()}
        for k, p in block.named_parameters():
            p.copy_(avg[k])
        enc.encoder.layer = nn.ModuleList([block])
        model.config.num_hidden_layers = 1
    elif arch.get("layers"):
        enc.encoder.layer = nn.ModuleList([layers[i] for i in arch["layers"]])
        model.config.num_hidden_layers = len(arch["layers"])
    apply_arch(model, arch)
    if arch.get("emb_rank"):
        r = arch["emb_rank"]
        U, S, Vh = torch.linalg.svd(src_emb.float(), full_matrices=False)
        fe = enc.embeddings.word_embeddings
        fe.emb.weight.copy_(U[:, :r] * S[:r])
        fe.proj.weight.copy_(Vh[:r].T)
    if sh and sh.get("lora_rank"):
        blk = enc.encoder.layer[0].block
        for t in LORA_TARGETS:
            m = _get(blk, t)
            for i, s in enumerate(srcs):
                d = _get(s, t).weight - m.base.weight   # source layer i − shared average
                U, S, Vh = torch.linalg.svd(d.float(), full_matrices=False)
                r = sh["lora_rank"]
                m.up[i].weight.copy_(U[:, :r] * S[:r].sqrt())
                m.down[i].weight.copy_(S[:r].sqrt()[:, None] * Vh[:r])
    model.config.pii_arch = arch
    return model


def size_report(model):
    """Parameters and the q4 ONNX size export_onnx.py produces: every embedding table int8 (1 byte), every other matrix 4-bit with a
    float32 scale per 32-weight block (0.625 byte); shared layers counted once. Measured q4 files: large 20.7, medium 14.0,
    small 7.1, nano 2.6 MB (2026-10-29)."""
    n = sum(p.numel() for p in model.parameters())
    emb = sum(p.numel() for name, p in model.named_parameters() if "embeddings" in name and p.dim() == 2 and "proj" not in name)
    mb = (emb * 1.0 + (n - emb) * 0.625) / 1e6
    return f"params {n / 1e6:.2f}M (embedding tables {emb / 1e6:.2f}M) · ≈ {mb:.1f} MB as q4 ONNX"
