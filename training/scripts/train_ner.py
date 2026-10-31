"""Multilingual PII NER (token classification, BIO) fine-tuning. Runs on CUDA, Apple MPS or CPU.

Examples:
  python scripts/train_ner.py --base jhu-clsp/mmBERT-small --out models/ner-mmbert-small --epochs 3
  python scripts/train_ner.py --base google/embeddinggemma-2 --out models/ner-eg2 --epochs 3 --lr 3e-5
    puts a token-classification head on the text encoder (bidirectional, 270M) of EmbeddingGemma 2 (Apache 2.0).
"""
import argparse, hashlib, json, os, random, shutil, subprocess, sys, time
from pathlib import Path

import numpy as np
import torch
from datasets import Dataset
from torch import nn
from safetensors.torch import save_file
from transformers.trainer_utils import get_last_checkpoint
from transformers import (AutoModel, AutoModelForTokenClassification, AutoTokenizer, DataCollatorForTokenClassification,
                          Trainer, TrainerCallback, TrainingArguments)

ROOT = Path(__file__).resolve().parent.parent
# Model label schemes (--labels). d12 = the 10 labels of the earlier data versions (default, unchanged); v1 = data v1.0,
# 13 labels (PERSON_L2 / URL_L2 = PII level 2, SECRET kept; the label scheme of the v1.0 training data). The saved final/tags.json records the
# tag list, and evaluate.py / teacher_soft.py decode with that list, so models of either scheme load.
LABEL_SETS = {
    "d12": ["PERSON", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "DATE_OF_BIRTH"],
    "v1": ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2",
           "DATE_OF_BIRTH", "SECRET"],
}


def tags_for(labels):
    return ["O"] + [f"{p}-{l}" for l in labels for p in ("B", "I")]


def labels_of(tags):
    """Tag list (tags.json) → label list in tag order."""
    out = []
    for t in tags[1:]:
        lab = t.split("-", 1)[1]
        if lab not in out:
            out.append(lab)
    return out


def set_labels(labels):
    """Rebinds the module label globals (LABELS, TAGS, T2I, NCLS) for one training run."""
    global LABELS, TAGS, T2I, NCLS
    LABELS = list(labels)
    TAGS = tags_for(LABELS)
    T2I = {t: i for i, t in enumerate(TAGS)}
    NCLS = 1 + len(LABELS)


def resolve_labels(spec, rows=()):
    """--labels value → label list: a scheme name (d12, v1), 'data' (the labels in the training rows, in the order of the
    smallest scheme that covers them, else sorted) or a comma-separated list."""
    if spec in LABEL_SETS:
        return LABEL_SETS[spec]
    if spec == "data":
        seen = {s["label"] for r in rows for s in r["spans"]}
        for ls in LABEL_SETS.values():
            if seen <= set(ls):
                return ls
        return sorted(seen)
    return [l.strip() for l in spec.split(",") if l.strip()]


LABELS = TAGS = T2I = None
set_labels(LABEL_SETS["d12"])   # importers that read the module constants get the old scheme


class EG2Tagger(nn.Module):
    """EmbeddingGemma 2 text tower (language_model) + normalization + linear head. Emits a tag per token in a single forward pass."""

    def __init__(self, base, freeze=False, head="linear", n_tags=None):
        super().__init__()
        n_tags = n_tags or len(TAGS)
        full = AutoModel.from_pretrained(base, dtype=torch.float32)
        self.encoder = full.language_model
        self.frozen = freeze  # True = keep the backbone frozen and train only the head (several heads can share one backbone in the browser)
        if freeze:
            self.encoder.requires_grad_(False)
        dim = self.encoder(input_ids=torch.tensor([[2]])).last_hidden_state.shape[-1]
        self.norm = nn.LayerNorm(dim)
        self.drop = nn.Dropout(0.1)
        self.head = nn.Linear(dim, n_tags) if head == "linear" else nn.Sequential(
            nn.Linear(dim, 1024), nn.GELU(), nn.Dropout(0.1), nn.Linear(1024, n_tags))

    def forward(self, input_ids, attention_mask=None, labels=None, **_):
        with torch.set_grad_enabled(not self.frozen and torch.is_grad_enabled()):
            h = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        logits = self.head(self.drop(self.norm(h)))
        out = {"logits": logits}
        if labels is not None:
            out["loss"] = nn.functional.cross_entropy(logits.view(-1, logits.size(-1)), labels.view(-1), ignore_index=-100)
        return out


def load(path, limit=0):
    rows = [json.loads(l) for l in Path(path).read_text().split("\n") if l.strip()]
    return rows[:limit] if limit else rows


NORMALIZE = False   # set in main() for tokenizers without a Unicode normalizer (textnorm.needs)
SOFT = None         # row index → list of (start, end, class probabilities) from teacher_soft.py (averaged over teachers)


def load_soft(files, n_rows):
    """teacher_soft.py outputs → {row: [(start, end, probs)]}; several teachers are averaged character by character."""
    import gzip
    per = []
    for f in files:
        with gzip.open(f, "rt") as fh:
            meta = json.loads(fh.readline())["meta"]
            assert meta["rows"] >= n_rows, f"{f}: soft labels for {meta['rows']} rows, training data has {n_rows}"
            assert meta.get("classes", ["O"] + LABEL_SETS["d12"]) == ["O"] + LABELS, \
                f"{f}: soft-label classes {meta.get('classes')} differ from the student's labels {['O'] + LABELS}"
            per.append({r["i"]: r["s"] for r in map(json.loads, fh)})
    out = {}
    for i in range(n_rows):
        if len(per) == 1:
            out[i] = [(s, e, [q / 255 for q in qs]) for s, e, qs in per[0].get(i, [])]
            continue
        cuts = sorted({c for p in per for s, e, _ in p.get(i, []) for c in (s, e)})
        segs = []
        for s, e in zip(cuts, cuts[1:]):   # a character not covered by a teacher's entry counts as O for that teacher
            acc = [0.0] * NCLS
            for p in per:
                hit = next((q for a, b, q in p.get(i, []) if a <= s and e <= b), None)
                if hit is None:
                    acc[0] += 1
                else:
                    acc = [x + y / 255 for x, y in zip(acc, hit)]
            if any(next((1 for a, b, _ in p.get(i, []) if a <= s and e <= b), 0) for p in per):
                segs.append((s, e, [x / len(per) for x in acc]))
        out[i] = segs
    return out


def soft_targets(row_i, offsets, text=None):
    """Per student token: (position, mean class probabilities over the soft entries it overlaps), char-length weighted.
    KD_PUNCT_O: punctuation and spaces count as O even inside a teacher entry — teacher subword tokens often swallow the
    punctuation next to an entity (`1234.`), and copying that widens the student's spans (2026-10-29: exact F1 0.90 → 0.77)."""
    pos, val = [], []
    segs = SOFT.get(row_i, [])
    for k, (s, e) in enumerate(offsets):
        if s == e:
            continue
        acc, w = [0.0] * NCLS, 0
        for a, b, q in segs:
            lo, hi = max(s, a), min(e, b)
            if hi <= lo:
                continue
            o = sum(1 for c in text[lo:hi] if not _punct(c)) if KD_PUNCT_O and text is not None else hi - lo
            if o > 0:
                acc = [x + o * y for x, y in zip(acc, q)]
                w += o
        if w:
            acc[0] += (e - s) - w if (e - s) > w else 0   # the rest of the token is O
            tot = sum(acc)
            pos.append(k)
            val.append([x / tot for x in acc])
    return pos, val


KD_PUNCT_O = False
PARTIAL = False      # training rows carry `known` (external data): untagged tokens of those rows get the partial-label loss


def _punct(c):
    import unicodedata
    return c.isspace() or unicodedata.category(c)[0] in "PS"


def encode(batch, tok, max_len):
    from textnorm import normalize, to_original
    if NORMALIZE:   # tokenize per-character NFKC text, align labels on the original text through the back-map
        normed = [normalize(t) for t in batch["text"]]
        enc = tok([n for n, _ in normed], truncation=True, max_length=max_len, return_offsets_mapping=True)
        enc["offset_mapping"] = [to_original(off, b) for off, (_, b) in zip(enc["offset_mapping"], normed)]
    else:
        enc = tok(batch["text"], truncation=True, max_length=max_len, return_offsets_mapping=True)
    all_labels, all_part, all_allowed = [], [], []
    for i, offsets in enumerate(enc["offset_mapping"]):
        spans = batch["spans"][i]
        ignore = (batch["ignore"][i] or []) if "ignore" in batch else []
        known = batch["known"][i] if "known" in batch else None
        # tags an untagged token may take in a row whose source annotates only `known` (ext_data.py, 2026-10-29); empty = plain O
        allowed = [T2I["O"]] + [T2I[f"{x}-{l}"] for l in LABELS if l not in known for x in "BI"] if known and set(LABELS) - set(known) else []
        labels, prev, part = [], None, []
        for (s, e) in offsets:
            if s == e:
                labels.append(-100)
                prev = None
                continue
            lab = None
            for sp in spans:
                if s < sp["end"] and sp["start"] < e:
                    lab = sp
                    break
            if lab is None:
                if ignore and any(s < g["end"] and g["start"] < e for g in ignore):
                    labels.append(-100)   # external data: a type the source labels but our scheme cannot place — no target at all
                elif allowed:
                    labels.append(-100)   # untagged token of a source that does not annotate every label: O or an unannotated label
                    part.append(len(labels) - 1)
                else:
                    labels.append(T2I["O"])
                prev = None
            else:
                inside = prev is lab and s > lab["start"]
                labels.append(T2I[("I-" if inside else "B-") + lab["label"]])
                prev = lab
        all_labels.append(labels)
        all_part.append(part)
        all_allowed.append(allowed)
    enc["labels"] = all_labels
    if PARTIAL:
        enc["part_pos"], enc["part_allowed"] = all_part, all_allowed
    if SOFT is not None and "idx" in batch:
        st = [soft_targets(i, off, t) for i, off, t in zip(batch["idx"], enc["offset_mapping"], batch["text"])]
        enc["soft_pos"], enc["soft_val"] = [p for p, _ in st], [v for _, v in st]
    enc.pop("offset_mapping")
    return enc


_ENC = {}


def _encode_shard(job):
    """Worker (fork): encode rows lo..hi of the shared columns and save them as a dataset shard."""
    lo, hi, path = job
    cols, tok, max_len, drop = _ENC["cols"], _ENC["tok"], _ENC["max_len"], _ENC["drop"]
    part = {k: v[lo:hi] for k, v in cols.items()}
    enc = encode(part, tok, max_len)
    out = {k: v for k, v in enc.items() if k not in drop}
    ds = Dataset.from_dict(out)
    if "soft_val" in out:   # float64 by inference; float32 halves the size
        from datasets import Sequence, Value
        ds = ds.cast_column("soft_val", Sequence(Sequence(Value("float32"))))
    ds.save_to_disk(path)
    return path


def encode_to_disk(a, tok, train_cols, extra_cols, dev_cols):
    """--encode_only: the same encode() as training, in fork workers over row ranges; train/ and dev/ datasets + meta.json."""
    import multiprocessing as mp
    from datasets import concatenate_datasets, load_from_disk
    out = Path(a.encode_only); out.mkdir(parents=True, exist_ok=True)
    n = a.num_proc or os.cpu_count()
    for name, cols in (("train", train_cols), ("dev", dev_cols)):
        rows = len(cols["text"])
        _ENC.update(cols=cols, tok=tok, max_len=a.max_len, drop={"offset_mapping"} | set(extra_cols))
        step = max(1, -(-rows // (n * 4)))
        jobs = [(lo, min(rows, lo + step), str(out / f"_{name}_{i:04d}")) for i, lo in enumerate(range(0, rows, step))]
        global PARTIAL
        keep = PARTIAL
        if name == "dev":
            PARTIAL = False
        with mp.get_context("fork").Pool(n) as pool:
            paths = pool.map(_encode_shard, jobs)
        PARTIAL = keep
        ds = concatenate_datasets([load_from_disk(p) for p in paths])
        ds.save_to_disk(str(out / name))
        for p in paths:
            shutil.rmtree(p)
        print(f"encoded {name}: {len(ds):,} rows → {out / name}", flush=True)
    (out / "meta.json").write_text(json.dumps({"tags": TAGS, "soft": SOFT is not None, "partial": PARTIAL, "max_len": a.max_len,
                                               "base": a.base, "train": a.train, "dev": a.dev, "dev_max": a.dev_max,
                                               "soft_files": [Path(f).name for f in a.soft], "kd_punct_o": a.kd_punct_o}))


class SoftCollator(DataCollatorForTokenClassification):
    """Pads like the token-classification collator and turns the sparse soft targets into [batch, length, classes] + a mask."""

    def __call__(self, features):
        pos = [f.pop("soft_pos", None) for f in features]
        val = [f.pop("soft_val", None) for f in features]
        ppos = [f.pop("part_pos", None) for f in features]
        pall = [f.pop("part_allowed", None) for f in features]
        batch = super().__call__(features)
        if any(p for p in ppos if p):
            B, L = batch["labels"].shape
            pmask = torch.zeros(B, L, dtype=torch.bool)
            allowed = torch.zeros(B, len(TAGS), dtype=torch.bool)
            for b, (ps, al) in enumerate(zip(ppos, pall)):
                if ps:
                    pmask[b, [k for k in ps if k < L]] = True
                    allowed[b, al] = True
            batch["part_mask"], batch["part_allowed"] = pmask, allowed
        if all(p is not None for p in pos):
            B, L = batch["labels"].shape
            soft = torch.zeros(B, L, NCLS)
            mask = torch.zeros(B, L, dtype=torch.bool)
            bi = [b for b, ps in enumerate(pos) for _ in ps]
            ki = [k for ps in pos for k in ps]
            if ki:
                soft[bi, ki] = torch.tensor([v for vs in val for v in vs])
                mask[bi, ki] = True
            batch["soft"], batch["soft_mask"] = soft, mask
        return batch


class KDTrainer(Trainer):
    """Cross-entropy on the gold BIO tags + T²·KL(teacher ‖ student) on the 1 + len(LABELS) classes (B/I summed) where a teacher entry exists.
    Rows with `known` (external data) add the partial-label loss on their untagged tokens; also used without soft labels then."""

    def __init__(self, *args, kd_alpha=0.5, kd_T=2.0, **kw):
        super().__init__(*args, **kw)
        self.kd_alpha, self.kd_T = kd_alpha, kd_T
        self.groups = [[0]] + [[TAGS.index(f"B-{l}"), TAGS.index(f"I-{l}")] for l in LABELS]

    def compute_loss(self, model, inputs, return_outputs=False, **kw):
        soft, mask = inputs.pop("soft", None), inputs.pop("soft_mask", None)
        pmask, pallowed = inputs.pop("part_mask", None), inputs.pop("part_allowed", None)
        out = model(**inputs)
        loss = out["loss"]
        if pmask is not None and pmask.any():
            # partial-label loss (2026-10-29, external data): an untagged token of a row whose source does not annotate every label
            # may be O or any unannotated label — maximise the probability of that set, averaged with the CE over the other tokens
            lp = torch.log_softmax(out["logits"].float(), -1)
            nll = -torch.logsumexp(lp.masked_fill(~pallowed[:, None, :], float("-inf")), -1)[pmask]
            n_ce = (inputs["labels"] != -100).sum()
            loss = (loss * n_ce + nll.sum()) / (n_ce + nll.numel())
        if soft is not None and mask.any():
            lp = torch.log_softmax(out["logits"].float() / self.kd_T, -1)
            lpc = torch.stack([torch.logsumexp(lp[..., g], -1) for g in self.groups], -1)
            kl = (soft * (torch.log(soft.clamp_min(1e-8)) - lpc)).sum(-1)
            loss = (1 - self.kd_alpha) * loss + self.kd_alpha * self.kd_T ** 2 * kl[mask].mean()
        return (loss, out) if return_outputs else loss


class CkptPack(TrainerCallback):
    """Enabled by CKPT_PACK_DIR: on checkpoint save (at most every CKPT_EVERY_MIN minutes, default 30) packs the checkpoint
    into 45 MB tar parts plus SHA256SUMS under <CKPT_PACK_DIR>/checkpoint-N/, so an external process can copy it off a
    preemptible VM. Writes the name to LATEST only once the parts are complete (a copier should read LATEST only); keeps
    only the 2 most recent packs."""

    def __init__(self, root, every_min):
        self.root, self.every, self.last = Path(root), every_min * 60, 0.0

    def on_save(self, args, state, control, **kw):
        name = f"checkpoint-{state.global_step}"
        src = Path(args.output_dir) / name
        if not state.is_world_process_zero or time.time() - self.last < self.every or not src.is_dir():
            return
        tmp = self.root / f".{name}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        subprocess.run(f"tar -cf - -C '{args.output_dir}' {name} | split -b 45m - '{tmp}/part-'", shell=True, check=True)
        (tmp / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                                                for p in sorted(tmp.glob("part-*"))))
        shutil.rmtree(self.root / name, ignore_errors=True)
        tmp.rename(self.root / name)
        (self.root / "LATEST").write_text(name + "\n")
        packs = sorted((d for d in self.root.glob("checkpoint-*") if d.is_dir()), key=lambda d: int(d.name.split("-")[1]))
        for d in packs[:-2]:
            shutil.rmtree(d, ignore_errors=True)
        self.last = time.time()
        print(f"ckpt-pack {name} {time.strftime('%T')}", flush=True)


class EMA(TrainerCallback):
    """Exponential moving average of the weights (plain and EMA weights compared from one run). The shadow is updated after every
    optimizer step with decay min(d, (1 + step) / (10 + step)), saved in each checkpoint as ema.safetensors (so a resumed run
    continues it) and swapped in by EMAEvalMixin for an extra evaluation with the prefix eval_ema."""

    def __init__(self, decay, resume_dir=None):
        self.decay, self.resume_dir, self.shadow, self.params = decay, resume_dir, None, None

    def on_train_begin(self, args, state, control, model=None, **kw):
        self.params = [p for p in model.parameters() if p.requires_grad]
        f = Path(self.resume_dir or "", "ema.safetensors")
        if self.resume_dir and f.is_file():
            from safetensors.torch import load_file
            st = load_file(str(f))
            self.shadow = [st[str(i)].to(p.device) for i, p in enumerate(self.params)]
            print(f"ema: resumed from {f}", flush=True)
        else:
            self.shadow = [p.detach().float().clone() for p in self.params]

    @torch.no_grad()
    def on_step_end(self, args, state, control, **kw):
        d = min(self.decay, (1 + state.global_step) / (10 + state.global_step))
        for s, p in zip(self.shadow, self.params):
            s.mul_(d).add_(p.detach().float(), alpha=1 - d)

    def on_save(self, args, state, control, **kw):
        ck = Path(args.output_dir) / f"checkpoint-{state.global_step}"
        if ck.is_dir() and self.shadow is not None:
            save_file({str(i): s.contiguous() for i, s in enumerate(self.shadow)}, str(ck / "ema.safetensors"))

    @torch.no_grad()
    def swap(self):
        for s, p in zip(self.shadow, self.params):
            tmp = p.detach().clone()
            p.copy_(s.to(p.dtype))
            s.copy_(tmp.float())


def fp16_state(model):
    return {k: v.detach().to(torch.float16).contiguous().cpu() for k, v in model.state_dict().items()}


class EMAEvalMixin:
    """Every evaluation also scores the EMA weights (metrics eval_ema_*). With snapshot_dir, each evaluation (one per epoch with
    --epoch_snapshots) writes both weight sets as fp16 to <snapshot_dir>/ep<N>/{plain,ema}/; the best EMA weights by token cost
    are kept in <out>/final_ema. The Trainer's own best-model choice (final/) stays on the plain weights."""
    ema = None
    snapshot_dir = None
    snapshot_by_step = False
    ema_best = None

    def snap_name(self):
        return f"step{self.state.global_step:06d}" if self.snapshot_by_step else f"ep{int(round(self.state.epoch or 0))}"

    def evaluate(self, *args, **kw):
        res = super().evaluate(*args, **kw)
        if self.ema is None or self.ema.shadow is None:
            return res
        ep = int(round(self.state.epoch or 0))
        if self.snapshot_dir:
            d = Path(self.snapshot_dir, self.snap_name(), "plain")
            d.mkdir(parents=True, exist_ok=True)
            save_file(fp16_state(self.model), str(d / "weights_fp16.safetensors"))
            (d.parent / "meta.json").write_text(json.dumps({"step": self.state.global_step, "epoch": self.state.epoch}))
        self.ema.swap()
        try:
            er = super().evaluate(*args, **{**kw, "metric_key_prefix": "eval_ema"})
            cost = er.get("eval_ema_token_cost")
            if self.snapshot_dir:
                d = Path(self.snapshot_dir, self.snap_name(), "ema")
                d.mkdir(parents=True, exist_ok=True)
                save_file(fp16_state(self.model), str(d / "weights_fp16.safetensors"))
            if cost is not None and (self.ema_best is None or cost < self.ema_best[0]):
                self.ema_best = (cost, self.state.global_step, ep)
                fin = Path(self.args.output_dir, "final_ema")
                fin.mkdir(parents=True, exist_ok=True)
                save_file(fp16_state(self.model), str(fin / "weights_fp16.safetensors"))
                (fin / "ema_best.json").write_text(json.dumps({"token_cost": cost, "step": self.state.global_step, "epoch": ep,
                                                                "decay": self.ema.decay}))
        finally:
            self.ema.swap()
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="jhu-clsp/mmBERT-small")
    ap.add_argument("--train", default=str(ROOT / "data/train.jsonl"))
    # the dev set (checkpoint selection) must be given explicitly, so the best model is never picked on a test set
    ap.add_argument("--dev", required=True, help="a non-test set such as synthetic dev (data/eval/*_test, openpii_val, bench not allowed)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--max_len", type=int, default=256)
    ap.add_argument("--dev_max", type=int, default=0, help="cap the dev set (0 = all rows)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--eval_steps", type=int, default=1000)
    ap.add_argument("--freeze", action="store_true", help="freeze the EG2 backbone, train only the head")
    ap.add_argument("--head", default="linear", choices=["linear", "mlp"])
    ap.add_argument("--seed", type=int, default=42, help="training seed (data order, dropout, head init)")
    ap.add_argument("--ema", type=float, default=0.0, help="EMA decay of the weights (0 = off); the EMA weights are evaluated next to the plain ones, best kept in final_ema/")
    ap.add_argument("--epoch_snapshots", action="store_true", help="evaluate and save once per epoch; fp16 plain (and EMA) weights of every epoch go to <out>/snapshots/ep<N>/")
    ap.add_argument("--snapshot_steps", action="store_true", help="like --epoch_snapshots but at every evaluation (--eval_steps); folders <out>/snapshots/step<NNNNNN>/ (for soups / EMA simulation)")
    ap.add_argument("--soft", nargs="*", default=[], help="teacher_soft.py outputs for the training data (several = ensemble KD)")
    ap.add_argument("--kd_alpha", type=float, default=0.5)
    ap.add_argument("--kd_T", type=float, default=2.0, help="must match the T the soft labels were made with")
    ap.add_argument("--kd_punct_o", action="store_true", help="punctuation inside teacher entries counts as O in the KD targets")
    ap.add_argument("--labels", default="d12",
                    help="model labels: d12 (10, default), v1 (13: PERSON_L2, URL_L2, SECRET), data (from the training rows) or a comma list")
    ap.add_argument("--encode_only", default="", help="tokenise and align (labels, soft targets) the training and dev sets, save them as "
                    "datasets under this folder and exit — run on a CPU machine so the GPU session only trains (2026-10-29)")
    ap.add_argument("--encoded", default="", help="folder written by --encode_only: skip loading and encoding the data")
    ap.add_argument("--num_proc", type=int, default=0, help="--encode_only worker processes (default: all CPUs)")
    ap.add_argument("--arch", default="", help="student tier from student_arch.TIERS (medium, small, nano, nano-lora); default: the base as is")
    ap.add_argument("--cpu", action="store_true", help="train on the CPU even if CUDA/MPS is available (smoke tests)")
    ap.add_argument("--random_batches", action="store_true",
                    help="plain random batches; default groups rows of similar length (rows average ~68 tokens, so random batches padded to the "
                         "longest row spent ~69%% of the compute on padding — 2026-10-29)")
    a = ap.parse_args()
    global NORMALIZE
    from textnorm import needs
    NORMALIZE = needs(a.base)

    from transformers import set_seed
    set_seed(a.seed)   # before the model is built: the classifier head init depends on it too
    enc_meta = json.loads(Path(a.encoded, "meta.json").read_text()) if a.encoded else None
    train_rows = [] if a.encoded else load(a.train, a.limit)
    if any(k in Path(a.dev).name for k in ("_test", "openpii_val", "bench")):
        sys.exit(f"a test set cannot be used as the dev set: {a.dev}")
    dev_rows = [] if a.encoded else load(a.dev)
    set_labels(resolve_labels(a.labels, train_rows))
    if enc_meta and enc_meta["tags"] != TAGS:
        sys.exit(f"--encoded {a.encoded} was made for tags {enc_meta['tags']}, not {TAGS}")
    extra = {s["label"] for r in train_rows + dev_rows for s in r["spans"]} - set(LABELS)
    if extra:   # would otherwise be a KeyError deep inside datasets.map
        sys.exit(f"labels in the data but not in --labels {a.labels}: {sorted(extra)} (v1 data needs --labels v1)")
    print(f"labels ({len(LABELS)}): {LABELS}", flush=True)
    tok = AutoTokenizer.from_pretrained(a.base)
    if "embeddinggemma-2" in a.base:
        model = EG2Tagger(a.base, a.freeze, a.head, len(TAGS))
    else:
        # pin dtype: transformers 5 loads the checkpoint dtype (fp16 etc.) as-is and training blows up with NaN (mMiniLMv2 2026-10-28)
        model = AutoModelForTokenClassification.from_pretrained(
            a.base, num_labels=len(TAGS), id2label=dict(enumerate(TAGS)), label2id=T2I, dtype=torch.float32)
        if a.arch:   # compressed student tier (student_arch.py): factorised embeddings, fewer or shared layers, per-loop LoRA
            import student_arch
            model = student_arch.build(model, student_arch.TIERS[a.arch])
            print(f"tier {a.arch}: {student_arch.size_report(model)}", flush=True)
    random.Random(0).shuffle(dev_rows)
    if a.dev_max:
        dev_rows = dev_rows[:a.dev_max]   # default: the whole dev set (2,000 rows were too few to pick checkpoints reliably)
    global SOFT, KD_PUNCT_O
    KD_PUNCT_O = a.kd_punct_o
    if a.soft and not a.encoded:
        SOFT = load_soft(a.soft, len(train_rows))
        print(f"soft labels: {len(a.soft)} teacher(s), {sum(map(len, SOFT.values()))} spans", flush=True)
    global PARTIAL
    PARTIAL = any(r.get("known") for r in train_rows)
    ext = any("ignore" in r or "known" in r for r in train_rows)   # external rows (ext_data.py train): ignore spans and known labels
    if ext:
        print(f"external rows: {sum(1 for r in train_rows if r.get('known')):,} with `known`, "
              f"{sum(len(r.get('ignore') or []) for r in train_rows):,} ignore spans", flush=True)
    cols = lambda rows, idx: {"text": [r["text"] for r in rows], "spans": [r["spans"] for r in rows],
                              **({"ignore": [r.get("ignore") or [] for r in rows], "known": [r.get("known") for r in rows]} if ext and idx is not None else {}),
                              **({"idx": list(range(len(rows)))} if idx else {})}   # idx: row number into the soft labels (training data only)
    extra_cols = (["idx"] if SOFT is not None else []) + (["ignore", "known"] if ext else [])
    if a.encode_only:
        encode_to_disk(a, tok, cols(train_rows, SOFT is not None), extra_cols, cols(dev_rows, None))
        return
    if a.encoded:
        from datasets import load_from_disk
        ds_tr, ds_dv = load_from_disk(str(Path(a.encoded, "train"))), load_from_disk(str(Path(a.encoded, "dev")))
        PARTIAL_TRAIN, SOFT_USED = enc_meta["partial"], enc_meta["soft"]
        print(f"encoded data {a.encoded}: {len(ds_tr):,} train rows, {len(ds_dv):,} dev rows, soft {SOFT_USED}, partial {PARTIAL_TRAIN}", flush=True)
    else:
        ds_tr = Dataset.from_dict(cols(train_rows, SOFT is not None)).map(lambda b: encode(b, tok, a.max_len), batched=True,
                                                                         remove_columns=["text", "spans"] + extra_cols)
        PARTIAL_TRAIN, PARTIAL = PARTIAL, False   # the dev set has no partial rows
        ds_dv = Dataset.from_dict(cols(dev_rows, None)).map(lambda b: encode(b, tok, a.max_len), batched=True, remove_columns=["text", "spans"])
        SOFT_USED = SOFT is not None

    def metrics(p):
        pred = np.argmax(p.predictions, -1)
        lab = p.label_ids
        m = lab != -100
        gold_pii = (lab != 0) & m
        pred_pii = (pred != 0) & m
        leak = float((gold_pii & ~pred_pii).sum() / max(1, gold_pii.sum()))
        over = float((pred_pii & ~gold_pii & m).sum() / max(1, (~gold_pii & m).sum()))
        # best-model criterion = misses + overmasking (on misses alone, a mask-everything model would win)
        return {"token_leak": leak, "token_overmask": over, "token_cost": leak + over}

    cuda = torch.cuda.is_available() and not a.cpu
    fields = TrainingArguments.__dataclass_fields__
    sampling = {} if a.random_batches else ({"train_sampling_strategy": "group_by_length"} if "train_sampling_strategy" in fields
                                            else {"group_by_length": True})   # transformers 5.x / 4.x spelling
    args = TrainingArguments(
        output_dir=a.out, num_train_epochs=a.epochs, learning_rate=a.lr, per_device_train_batch_size=a.bs,
        per_device_eval_batch_size=64, warmup_steps=0.06, weight_decay=0.01, lr_scheduler_type="linear",
        **({"eval_strategy": "epoch", "save_strategy": "epoch"} if a.epoch_snapshots else
           {"eval_strategy": "steps", "eval_steps": a.eval_steps, "save_steps": a.eval_steps}), save_total_limit=2, logging_steps=100,
        load_best_model_at_end=True, metric_for_best_model="token_cost", greater_is_better=False, seed=a.seed,
        bf16=cuda and torch.cuda.is_bf16_supported(), dataloader_num_workers=2 if cuda else 0, report_to=[], **sampling,
        **({"remove_unused_columns": False} if SOFT_USED or PARTIAL_TRAIN else {}),
        **({"use_cpu": True} if a.cpu and "use_cpu" in fields else {}),
    )
    mix = (lambda cls: type(f"EMA{cls.__name__}", (EMAEvalMixin, cls), {})) if (a.ema or a.epoch_snapshots or a.snapshot_steps) else (lambda cls: cls)
    if SOFT_USED or PARTIAL_TRAIN:
        tr = mix(KDTrainer)(model=model, args=args, train_dataset=ds_tr, eval_dataset=ds_dv, data_collator=SoftCollator(tok),
                            compute_metrics=metrics, kd_alpha=a.kd_alpha, kd_T=a.kd_T)
    else:
        tr = mix(Trainer)(model=model, args=args, train_dataset=ds_tr, eval_dataset=ds_dv,
                          data_collator=DataCollatorForTokenClassification(tok), compute_metrics=metrics)
    last = get_last_checkpoint(a.out) if Path(a.out).is_dir() else None   # resume from the latest checkpoint in --out if one exists
    if a.ema:
        tr.ema = EMA(a.ema, last)
        tr.add_callback(tr.ema)
    if a.epoch_snapshots or a.snapshot_steps:
        tr.snapshot_dir = str(Path(a.out, "snapshots"))
        tr.snapshot_by_step = a.snapshot_steps
    if os.environ.get("CKPT_PACK_DIR"):
        tr.add_callback(CkptPack(os.environ["CKPT_PACK_DIR"], float(os.environ.get("CKPT_EVERY_MIN", "30"))))
    tr.train(resume_from_checkpoint=last)
    # final/ = downloadable bundle: half-precision weights + tags + tokenizer (read by evaluate.py TaggerPredictor)
    fin = Path(a.out, "final")
    fin.mkdir(parents=True, exist_ok=True)
    save_file({k: v.detach().to(torch.float16).contiguous().cpu() for k, v in tr.model.state_dict().items()},
              str(fin / "weights_fp16.safetensors"))
    tok.save_pretrained(fin)
    if hasattr(tr.model, "config"):  # so it can be rebuilt without the base model folder (pruned vocab etc.)
        tr.model.config.save_pretrained(fin)
    (fin / "tags.json").write_text(json.dumps({"base": a.base, "tags": TAGS, "labels": LABELS, "freeze": a.freeze, "head": a.head,
                                               **({"normalize": "nfkc_char"} if NORMALIZE else {}),
                                               **({"kd": {"soft": [Path(f).name for f in a.soft], "alpha": a.kd_alpha, "T": a.kd_T, "punct_o": a.kd_punct_o}} if a.soft else {})}))
    if Path(a.out, "final_ema", "weights_fp16.safetensors").is_file():   # same bundle layout as final/, EMA weights
        fe = Path(a.out, "final_ema")
        tok.save_pretrained(fe)
        if hasattr(tr.model, "config"):
            tr.model.config.save_pretrained(fe)
        shutil.copy(fin / "tags.json", fe / "tags.json")
    tr.snapshot_dir, tr.ema = None, None   # the closing evaluation (best plain model) must not overwrite a snapshot or rescore EMA
    print(json.dumps(tr.evaluate(), indent=1))


if __name__ == "__main__":
    main()
