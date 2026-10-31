"""Split dedupe and leak report — exact duplicates inside each split, near-duplicate templates across splits,
PII-value overlap with train, per-split diversity and refill needs (2026-10-29).

LLM generators reuse favourite names and sentences whatever the seed, so near-identical rows leak between
train / validation / test and inflate benchmark scores. This script:
  1) exact  : drops repeated rows inside a split (text after NFKC + whitespace collapse; the first one is kept)
              exact_cross: drops a row whose normalized text equals a kept row of a higher-priority split
  2) near   : MinHash (128 permutations, LSH 32 bands x 4 rows) over 5-gram shingles of the *masked* text
              (every PII span replaced by its label, so a reused template matches even when the values differ),
              lowercased, punctuation dropped (--keep-punct keeps it); word 5-grams, or character 5-grams for
              zh/ja/th/km and texts with few spaces.
              LSH candidates are confirmed with the exact Jaccard of the shingle sets (>= --threshold).
              Rows are only ever removed from the lower-priority split; references are the kept rows of all
              higher-priority splits. Texts shorter than --min-words / --min-chars are exempt (a bare "<PERSON>"
              cell would otherwise match every split) and only get the exact_cross check.
              Each query looks at no more than --cap refs per LSH bucket and band (cap per priority rank: "50,500" =
              50 for the 2nd split, 500 for the 3rd and later — tests are small, so a high cap is cheap);
              queries that hit the cap are counted.
              Control: --control random non-exempt rows of the first split are perturbed (PII values swapped, 10% of
              words — characters in character mode — shuffled, punctuation changed) and searched like a test split;
              report.md prints the share detected ("near-duplicate recall on perturbed copies").
  3) overlap: share of PII values in each later split that also occur in the first split, per label, for two value
              definitions — exact span text, and normalized (NFKC, casefold, whitespace/punctuation removed, digits only
              for PHONE / ID_NUMBER / FINANCIAL / DATE_OF_BIRTH) — and, for each, the row ids whose values are all unseen
              (rows without spans are not listed)
  4) diversity per split / language / generator (`gen` field, + `:effort`), on up to --sample random kept rows per group:
              distinct 3-gram ratio of the masked text (word or character, as in 2), share of the 100 most frequent
              PERSON values among PERSON spans, mean pairwise MinHash similarity, row count.
              3-gram ratios of character-mode groups (zh/ja/th/km) are character-based and not comparable with word-mode
              groups, so report.md ranks languages by the PERSON top-100 share (value reuse) and marks character-mode groups.
  5) refill : kept rows per split x language (x planned generator: `fallback_from`, else `gen`) against --target split=N
              (N rows per language); languages below target are listed with their deficit.

Memory: every split is streamed once; per row only the MinHash signature, the shingle / 3-gram hashes, a text hash,
small codes and hashed PII values are kept (raw rows are re-read for the output files). Hashes use Python's str hash,
so the script re-executes itself with PYTHONHASHSEED=0 for reproducible results.

Priority (--order, highest first): the first split is never touched. Recommended
  --order train,validation,private_test,test
i.e. validation loses rows that collide with train, the tests lose rows that collide with train or validation,
and between the two tests the public one loses the row (the private test keeps its size).
Row ids are the row's `id` field if present, else the 0-based index among the non-empty lines of its input file.

Usage:
  python scripts/dedupe_splits.py --split train=data/train.jsonl --split validation=data/eval/val.jsonl \\
      --split test=data/eval/test.jsonl --order train,validation,test --out data/dedup/run \\
      --target validation=300 --target test=80 --show 5
  → <out>/<name>.dedup.jsonl (kept rows, original order and bytes) · removed.jsonl · report.json · report.md
"""
import argparse, json, os, random, re, sys, time, unicodedata
from array import array
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

CHAR_LANGS = {"zh", "ja", "th", "km"}
NUMERIC = {"PHONE", "ID_NUMBER", "FINANCIAL", "DATE_OF_BIRTH"}  # normalized value = digits only
N = 5                    # shingle size (words or characters)
PUA = 0xE000             # label placeholder characters (one private-use char per label)
MAX_WORD_LEN = 12        # mean "word" length above this → treat the text as unspaced (character shingles)
CHUNK = 20_000           # rows per featurization chunk
H64 = (1 << 64) - 1
U64 = np.uint64
LABELS, LANGS, GENS = {}, {}, {"": 0}  # string → code
PUNCT_TABLE = None       # Unicode punctuation (category P) → space before shingling; None with --keep-punct


def code(table, key):
    return table.setdefault(key, len(table))


def t0():
    start = time.time()
    return lambda msg: print(f"[{time.time() - start:6.1f}s] {msg}", flush=True)


def norm(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def norm_value(label, v):
    v = unicodedata.normalize("NFKC", v).casefold()
    if label in NUMERIC and any(c.isdigit() for c in v):
        return "".join(c for c in v if c.isdigit())
    return "".join(c for c in v if not unicodedata.category(c)[0] in "PZ" and not c.isspace())


def mask(text, spans, ph):
    """Text with each span replaced by ` <placeholder> ` (overlapping spans: the earlier, longer one wins)."""
    out, pos = [], 0
    for s in sorted(spans, key=lambda s: (s["start"], -s["end"])):
        if s["start"] < pos:
            continue
        out += [text[pos:s["start"]], f" {ph(s['label'])} "]
        pos = s["end"]
    out.append(text[pos:])
    return "".join(out)


def tokens(r):
    """Masked, lowercased token ids of a row (code points in character mode, word hashes otherwise) → (ids, char_mode)."""
    m = unicodedata.normalize("NFKC", mask(r["text"], r["spans"], lambda lab: chr(PUA + code(LABELS, lab)))).lower()
    m = " ".join((m.translate(PUNCT_TABLE) if PUNCT_TABLE else m).split())
    words = m.split()
    cm = r.get("lang") in CHAR_LANGS or (bool(words) and len(m) / len(words) > MAX_WORD_LEN)
    return (list(map(ord, m)) if cm else [hash(w) & 0x3FFF_FFFF_FFFF_FFFF | 1 << 62 for w in words]), cm


def mix(x):
    """splitmix64 finalizer on a uint64 array."""
    with np.errstate(over="ignore"):
        x = (x ^ (x >> U64(30))) * U64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> U64(27))) * U64(0x94D049BB133111EB)
        return x ^ (x >> U64(31))


def gram_hash(T, pos, n):
    """Hash of the n tokens T[pos : pos+n] for every position."""
    with np.errstate(over="ignore"):
        h = mix(T[pos])
        for j in range(1, n):
            h = mix(h * U64(0x9E3779B97F4A7C15) + T[pos + j])
    return h


def ranges(starts, cnt):
    """Concatenated index ranges [start, start+cnt) per row."""
    return np.repeat(starts, cnt) + np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)


class Hasher:
    def __init__(self, perms, seed):
        rng = np.random.default_rng(seed)
        self.a = rng.integers(1, 1 << 63, perms, dtype=np.uint64) | U64(1)
        self.b = rng.integers(0, 1 << 63, perms, dtype=np.uint64)

    def featurize(self, token_lists):
        """Token id lists → shingle hashes (uint32) + counts, MinHash signatures, 3-gram hashes (uint32) + counts."""
        T, starts = array("Q"), []
        pad = [0] * (N - 1)  # sentinels so grams never cross rows
        for ids in token_lists:
            starts.append(len(T)); T.extend(ids); T.extend(pad)
        T = np.frombuffer(T, dtype=U64) if len(T) else np.zeros(1, U64)
        starts = np.array(starts, np.int64)
        lens = np.array([len(t) for t in token_lists], np.int64)
        out = {}
        for n, key in ((N, "sh"), (3, "tri")):
            cnt = np.maximum(lens - n + 1, 1)  # a row shorter than n contributes one padded gram
            out[key] = (gram_hash(T, ranges(starts, cnt), n) >> U64(32)).astype(np.uint32)
            out[key + "_cnt"] = cnt
        x = out["sh"].astype(np.uint64)
        rel = np.concatenate([[0], np.cumsum(out["sh_cnt"])[:-1]])
        sig = np.empty((len(token_lists), len(self.a)), np.uint32)
        with np.errstate(over="ignore"):
            for k in range(len(self.a)):  # multiply-shift universal hashing: (a*x + b) >> 32
                sig[:, k] = np.minimum.reduceat(((self.a[k] * x + self.b[k]) >> U64(32)).astype(np.uint32), rel)
        out["sig"], out["lens"] = sig, lens
        return out


def count_lines(path):
    with open(path, "rb") as f:
        return sum(buf.count(b"\n") for buf in iter(lambda: f.read(1 << 24), b"")) + 1


def iter_rows(path):
    """(index among non-empty lines, raw line, parsed row)."""
    i = 0
    with open(path) as f:
        for line in f:
            if line.strip():
                yield i, line, json.loads(line)
                i += 1


class Pool:
    """Per-row features of all splits (global row index = split base + local index)."""

    def __init__(self, total, perms):
        self.sig = np.empty((total, perms), np.uint32)
        self.n = 0
        self.parts = defaultdict(list)

    def add(self, feat, extra):
        k = len(feat["lens"])
        self.sig[self.n:self.n + k] = feat["sig"]
        self.n += k
        for key in ("sh", "sh_cnt", "tri", "tri_cnt", "lens"):
            self.parts[key].append(feat[key])
        for key, v in extra.items():
            self.parts[key].append(np.asarray(v))

    def finish(self):
        self.sig = self.sig[:self.n]
        for key, v in self.parts.items():
            setattr(self, key, np.concatenate(v))
        self.parts = None
        self.seg = np.concatenate([[0], np.cumsum(self.sh_cnt)])
        self.seg3 = np.concatenate([[0], np.cumsum(self.tri_cnt)])
        del self.sh_cnt, self.tri_cnt


def read_splits(splits, order, hasher, a, log):
    """Stream every split once → Pool, split meta (base, count, ids), flat span arrays, control reservoir."""
    total = sum(count_lines(p) for _, p in splits)
    pool, paths = Pool(total, a.perms), dict(splits)
    meta, spans = {}, defaultdict(list)
    rng, reservoir, seen_ok = random.Random(a.seed), [], 0
    for rank, name in enumerate(order):
        base, ids, has_id = pool.n, [], False
        buf, extra = [], defaultdict(list)

        def flush():
            feat = hasher.featurize([t for t, _ in buf])
            pool.add(feat, {"hash": np.array(extra["hash"], np.uint64), "cm": np.array([c for _, c in buf], bool),
                            **{k: np.array(extra[k], np.int32) for k in ("lang", "gen", "plan")}})
            buf.clear(); extra.clear()

        for i, line, r in iter_rows(paths[name]):
            if "id" in r:
                has_id = True
            ids.append(r.get("id", i))
            toks, cm = tokens(r)
            buf.append((toks, cm))
            extra["hash"].append(hash(norm(r["text"])) & H64)
            extra["lang"].append(code(LANGS, r.get("lang") or ""))
            g = r.get(a.gen_field) or ""
            extra["gen"].append(code(GENS, g + (f":{r['effort']}" if g and r.get("effort") else "")))
            extra["plan"].append(code(GENS, r.get("fallback_from") or g))
            for s in r["spans"]:
                v = r["text"][s["start"]:s["end"]]
                spans["row"].append(base + i)
                spans["label"].append(code(LABELS, s["label"]))
                spans["exact"].append(hash((s["label"], v)) & H64)
                spans["norm"].append(hash((s["label"], norm_value(s["label"], v))) & H64)
                spans["val"].append(hash(v) & H64)
            if rank == 0 and a.control and len(toks) >= (a.min_chars if cm else a.min_words):
                seen_ok += 1  # reservoir sampling of control rows
                if len(reservoir) < a.control:
                    reservoir.append((base + i, r))
                elif (j := rng.randrange(seen_ok)) < a.control:
                    reservoir[j] = (base + i, r)
            if len(buf) >= CHUNK:
                flush()
        if buf:
            flush()
        meta[name] = {"base": base, "n": pool.n - base, "ids": ids if has_id else None, "path": paths[name]}
        log(f"read {name}: {meta[name]['n']:,} rows")
    pool.finish()
    sp = {"row": np.array(spans["row"], np.int64), "label": np.array(spans["label"], np.int32)}
    for k in ("exact", "norm", "val"):
        sp[k] = np.array(spans[k], np.uint64)
    return pool, meta, sp, reservoir


def band_key(sig, rows, b, r):
    h = np.zeros(len(rows), np.uint64)
    with np.errstate(over="ignore"):
        for j in range(r):
            h = mix(h * U64(0x9E3779B97F4A7C15) + sig[rows, b * r + j].astype(np.uint64))
    return h


def candidates(qsig, rsig, ref, qry, bands, cap):
    """Pairs (query, ref) sharing at least one LSH bucket, at most `cap` refs per query per band (no O(n^2) buckets).
    Returns pair arrays and the number of queries that hit the cap in some band."""
    r = qsig.shape[1] // bands
    out, capped = [], np.zeros(len(qry), bool)
    for b in range(bands):
        rk = band_key(rsig, ref, b, r)
        order = np.argsort(rk, kind="stable")
        rs = rk[order]
        qk = band_key(qsig, qry, b, r)
        lo, hi = np.searchsorted(rs, qk, "left"), np.searchsorted(rs, qk, "right")
        capped |= hi - lo > cap
        cnt = np.minimum(hi - lo, cap)
        if cnt.sum():
            out.append(np.repeat(qry, cnt).astype(np.int64) << 32 | ref[order[ranges(lo, cnt)]].astype(np.int64))
    if not out:
        return np.empty(0, np.int64), np.empty(0, np.int64), int(capped.sum())
    p = np.unique(np.concatenate(out))
    return p >> 32, p & 0xFFFFFFFF, int(capped.sum())


def shingle_set(sh, seg, i, cache):
    if i not in cache:
        cache[i] = np.unique(sh[seg[i]:seg[i + 1]])
    return cache[i]


def jaccard(A, B):
    inter = np.intersect1d(A, B, assume_unique=True).size
    return inter / max(1, A.size + B.size - inter)


def near_matches(Q, R, ref, qry, a, cap):
    """Best confirmed match (exact Jaccard >= threshold) among `ref` rows of pool R for each `qry` row of pool Q
    → ({q: (r, jaccard)}, candidate pairs (q, r), queries that hit the cap)."""
    q, r, capped = candidates(Q.sig, R.sig, ref, qry, a.bands, cap)
    cand = (q, r)
    est = np.empty(len(q), np.float32)
    for i in range(0, len(q), 200_000):
        est[i:i + 200_000] = (Q.sig[q[i:i + 200_000]] == R.sig[r[i:i + 200_000]]).mean(1)
    keep = est >= a.threshold - 0.1  # MinHash std at 0.7 is ~0.04 with 128 perms
    q, r, est = q[keep], r[keep], est[keep]
    o = np.lexsort((-est, q))
    qc, rc, found, tried = {}, {}, {}, Counter()
    for qi, ri in zip(q[o].tolist(), r[o].tolist()):
        if qi in found or tried[qi] >= 8:
            continue
        tried[qi] += 1
        j = jaccard(shingle_set(Q.sh, Q.seg, qi, qc), shingle_set(R.sh, R.seg, ri, rc))
        if j >= a.threshold:
            found[qi] = (ri, j)
    return found, cand, capped


PUNCT = list(",.;:!?-")


def perturb(r, rng, pool_vals, cm, swap=True, shuffle=True, punct=True):
    """Copy of a row with PII values swapped (same label), 10% of words (characters in character mode) shuffled
    and about half of the punctuation marks replaced."""
    text = r["text"]
    segs, pos = [], 0  # ("t", text) or ("p", value, label)
    for s in sorted(r["spans"], key=lambda s: (s["start"], -s["end"])):
        if s["start"] < pos:
            continue
        old = text[s["start"]:s["end"]]
        alts = [v for v in pool_vals[s["label"]] if v != old]
        segs += [("t", text[pos:s["start"]]), ("p", rng.choice(alts) if alts and swap else old, s["label"])]
        pos = s["end"]
    segs.append(("t", text[pos:]))
    units = [(list(seg[1]) if cm else re.split(r"(\s+)", seg[1])) if seg[0] == "t" else None for seg in segs]
    slots = [(i, j) for i, u in enumerate(units) if u for j, w in enumerate(u) if w.strip()]
    pick = rng.sample(slots, max(1, round(0.1 * len(slots)))) if slots and shuffle else []
    vals = [units[i][j] for i, j in pick]
    rng.shuffle(vals)
    for (i, j), w in zip(pick, vals):
        units[i][j] = w
    out, new_spans = "", []
    for seg, u in zip(segs, units):
        if seg[0] == "p":
            new_spans.append({"start": len(out), "end": len(out) + len(seg[1]), "label": seg[2]})
            out += seg[1]
        else:
            out += "".join(rng.choice([p for p in PUNCT if p != c]) if punct and unicodedata.category(c)[0] == "P" and rng.random() < 0.5
                           else c for c in "".join(u))
    return {"text": out, "spans": new_spans, "lang": r.get("lang")}


class Mini:
    """Pool-like features of a few extra rows (control copies)."""

    def __init__(self, feat):
        self.sig, self.sh = feat["sig"], feat["sh"]
        self.seg = np.concatenate([[0], np.cumsum(feat["sh_cnt"])])


def control(reservoir, pool, ref, caps, hasher, a, log):
    """Near-duplicate recall on perturbed copies of first-split rows, per cap value."""
    if not reservoir:
        return {}
    rng = random.Random(a.seed + 1)
    pool_vals = defaultdict(list)
    for _, r in reservoir:
        for s in r["spans"]:
            pool_vals[s["label"]].append(r["text"][s["start"]:s["end"]])
    orig = np.array([g for g, _ in reservoir], np.int64)

    def to_orig(M):
        return np.array([jaccard(np.unique(M.sh[M.seg[i]:M.seg[i + 1]]), np.unique(pool.sh[pool.seg[g]:pool.seg[g + 1]]))
                         for i, g in enumerate(orig.tolist())])

    # one perturbation at a time: which one moves the Jaccard most
    ablation = {}
    for kind in ("swap", "shuffle", "punct"):
        one = [perturb(r, random.Random(a.seed + 2), pool_vals, bool(pool.cm[g]), **{k: k == kind for k in ("swap", "shuffle", "punct")})
               for g, r in reservoir]
        ablation[kind] = round(float(np.median(to_orig(Mini(hasher.featurize([tokens(c)[0] for c in one]))))), 4)
    copies = [perturb(r, rng, pool_vals, bool(pool.cm[g])) for g, r in reservoir]
    M = Mini(hasher.featurize([tokens(c)[0] for c in copies]))
    jo = to_orig(M)
    rep = {"n": len(copies), "jaccard_to_original_median": round(float(np.median(jo)), 4),
           "jaccard_to_original_ge_threshold": round(float((jo >= a.threshold).mean()), 4),
           "median_jaccard_single_perturbation": ablation, "by_cap": {}}
    for cap in sorted(set(caps)):
        found, (q, r), capped = near_matches(M, pool, ref, np.arange(len(copies)), a, cap)
        hit = set((q[r == orig[q]]).tolist())
        rep["by_cap"][cap] = {"recall": round(len(found) / len(copies), 4), "original_in_candidates": round(len(hit) / len(copies), 4),
                              "queries_capped": capped}
    log(f"control: {rep}")
    return rep


def unit_hist(vals):
    edges = [(0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    h = {f"{lo:.1f}-{hi:.1f}": sum(lo <= v < hi for v in vals) for lo, hi in edges}
    h["<0.7"] = sum(v < 0.7 for v in vals)
    h["1.0"] = sum(v >= 1.0 for v in vals)
    return h


def overlap(sp, meta, order, kept_mask, rid):
    """PII values (exact and normalized) of later splits' kept rows that also occur in the first split's kept rows."""
    labels = {c: l for l, c in LABELS.items()}
    alive = kept_mask[sp["row"]]
    b0, n0 = meta[order[0]]["base"], meta[order[0]]["n"]
    in0 = (sp["row"] >= b0) & (sp["row"] < b0 + n0) & alive
    ref = {k: np.unique(sp[k][in0]) for k in ("exact", "norm")}
    rep = {}
    for name in order[1:]:
        b, n = meta[name]["base"], meta[name]["n"]
        m = (sp["row"] >= b) & (sp["row"] < b + n) & alive
        rows, lab = sp["row"][m], sp["label"][m]
        seen = {k: np.isin(sp[k][m], ref[k]) for k in ("exact", "norm")}
        with_spans = np.unique(rows)
        o = {"spans": int(m.sum()), "rows_with_spans": len(with_spans), "per_label": {}}
        for k in ("exact", "norm"):
            o[f"span_seen_share_{k}"] = round(float(seen[k].mean()), 4) if len(rows) else 0.0
            dv = np.unique(sp[k][m])
            o[f"distinct_seen_share_{k}"] = round(float(np.isin(dv, ref[k]).mean()), 4) if len(dv) else 0.0
            any_seen = np.unique(rows[seen[k]])
            unseen = np.setdiff1d(with_spans, any_seen)
            o[f"rows_all_unseen_{k}"] = len(unseen)
            o[f"unseen_rows_{k}"] = [rid(name, int(g - b)) for g in unseen]
        for c in sorted(set(lab.tolist()), key=lambda c: -(lab == c).sum()):
            lm = lab == c
            o["per_label"][labels[c]] = {"spans": int(lm.sum()), **{f"seen_{k}": int(seen[k][lm].sum()) for k in ("exact", "norm")},
                                         **{f"unseen_{k}": int((~seen[k][lm]).sum()) for k in ("exact", "norm")}}
        rep[name] = o
    return rep


def diversity(g, pool, sp_person, sample, rng):
    """Metrics for one group of global row indices (sampled to `sample` rows so groups are comparable)."""
    n_all = len(g)
    g = np.sort(np.array(rng.sample(list(g), sample) if n_all > sample else g, np.int64))
    tri = pool.tri[ranges(pool.seg3[g], pool.seg3[g + 1] - pool.seg3[g])]
    pr, pv = sp_person
    sel = np.zeros(pool.n, bool); sel[g] = True
    _, pc = np.unique(pv[sel[pr]], return_counts=True)
    eq, n = 0, len(g)
    for k in range(pool.sig.shape[1]):
        _, c = np.unique(pool.sig[g, k], return_counts=True)
        eq += int((c * (c - 1) // 2).sum())
    pairs = pool.sig.shape[1] * n * (n - 1) / 2
    return {"rows": n_all, "sampled": n, "distinct_3gram": round(len(np.unique(tri)) / max(1, len(tri)), 4),
            "person_top100_share": round(float(np.sort(pc)[::-1][:100].sum() / max(1, pc.sum())), 4),
            "person_spans": int(pc.sum()), "mean_minhash_sim": round(eq / pairs, 4) if pairs else None,
            "char_mode_share": round(float(pool.cm[g].mean()), 3)}


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":  # str hashes must be reproducible
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable] + sys.argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="append", required=True, help="name=path.jsonl (repeat)")
    ap.add_argument("--order", default="", help="priority, highest first (default: --split order); the first is never deduped against")
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.7, help="exact Jaccard of masked 5-gram sets")
    ap.add_argument("--perms", type=int, default=128)
    ap.add_argument("--bands", type=int, default=32)
    ap.add_argument("--cap", default="50,500", help="max refs per query per LSH bucket and band, by priority rank from the 2nd split (last value repeats)")
    ap.add_argument("--control", type=int, default=300, help="perturbed first-split rows for the recall check (0 = off)")
    ap.add_argument("--target", action="append", default=[], help="split=N rows per language for the refill list (repeat)")
    ap.add_argument("--min-words", type=int, default=8, help="shorter masked texts (word mode) are exempt from near dedupe")
    ap.add_argument("--min-chars", type=int, default=20, help="same for character mode")
    ap.add_argument("--keep-punct", action="store_true", help="keep punctuation in the shingles (default: dropped, so punctuation edits do not hide a template)")
    ap.add_argument("--sample", type=int, default=2000, help="rows per diversity group")
    ap.add_argument("--gen-field", default="gen")
    ap.add_argument("--show", type=int, default=0, help="print N removed near-duplicate pairs (masked, 150 chars)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    log = t0()
    global PUNCT_TABLE
    if not a.keep_punct:
        PUNCT_TABLE = {c: " " for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c))[0] == "P"}
    splits = [tuple(s.split("=", 1)) for s in a.split]
    order = a.order.split(",") if a.order else [n for n, _ in splits]
    if sorted(order) != sorted(n for n, _ in splits):
        sys.exit(f"--order {order} must name exactly the splits {[n for n, _ in splits]}")
    capv = [int(c) for c in a.cap.split(",")]
    caps = {name: capv[min(rank - 1, len(capv) - 1)] for rank, name in enumerate(order) if rank}
    targets = {k: int(v) for k, v in (t.split("=", 1) for t in a.target)}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    hasher = Hasher(a.perms, a.seed)
    pool, meta, sp, reservoir = read_splits(splits, order, hasher, a, log)
    rid = lambda name, i: meta[name]["ids"][i] if meta[name]["ids"] else i  # noqa: E731
    where = lambda g: next((n, g - m["base"]) for n, m in meta.items() if m["base"] <= g < m["base"] + m["n"])  # noqa: E731
    exempt = np.where(pool.cm, pool.lens < a.min_chars, pool.lens < a.min_words)
    log(f"features: {pool.n:,} rows, {len(pool.sh):,} shingles, char mode {pool.cm.mean():.1%}")

    removed = []  # (split, local row, reason, match global row, similarity)
    kept_mask = np.zeros(pool.n, bool)
    stats = {n: Counter(rows=meta[n]["n"]) for n in order}
    kh, kg = np.empty(0, np.uint64), np.empty(0, np.int64)  # sorted hashes / global rows of kept rows so far
    ref_global = np.empty(0, np.int64)
    for rank, name in enumerate(order):
        b, n = meta[name]["base"], meta[name]["n"]
        h = pool.hash[b:b + n]
        _, first, inv = np.unique(h, return_index=True, return_inverse=True)
        firstocc = first[inv.ravel()]
        for i in np.nonzero(firstocc != np.arange(n))[0].tolist():
            removed.append((name, i, "exact", b + int(firstocc[i]), 1.0))
        surv = np.nonzero(firstocc == np.arange(n))[0]
        if len(kh):
            p = np.minimum(np.searchsorted(kh, h[surv]), len(kh) - 1)
            hit = kh[p] == h[surv]
            for i, m in zip(surv[hit].tolist(), kg[p[hit]].tolist()):
                removed.append((name, i, "exact_cross", m, 1.0))
            surv = surv[~hit]
        near = {}
        if rank and len(ref_global):
            qry = b + surv[~exempt[b + surv]]
            found, _, capped = near_matches(pool, pool, ref_global, qry, a, caps[name])
            stats[name]["exempt_short"] = int(exempt[b + surv].sum())
            stats[name]["queries_capped"] = capped
            near = {q - b: v for q, v in found.items()}
            for i, (m, j) in sorted(near.items()):
                removed.append((name, i, "near", m, round(j, 4)))
        keep = np.array([i for i in surv.tolist() if i not in near], np.int64)
        kept_mask[b + keep] = True
        allh, allg = np.concatenate([kh, h[keep]]), np.concatenate([kg, b + keep])
        o = np.argsort(allh, kind="stable")
        kh, kg = allh[o], allg[o]
        ref_global = np.concatenate([ref_global, b + keep[~exempt[b + keep]]])
        stats[name].update(Counter(rm[2] for rm in removed if rm[0] == name))
        stats[name]["kept"] = len(keep)
        log(f"{name}: " + ", ".join(f"{k} {v:,}" for k, v in stats[name].items()))

    ctrl = control(reservoir, pool, ref_global[ref_global < meta[order[0]]["base"] + meta[order[0]]["n"]],
                   list(caps.values()) or [capv[0]], hasher, a, log)

    # pick pairs to show, then one output pass over the raw files (kept rows verbatim, removed rows with data)
    rng = random.Random(a.seed)
    nears = [rm for rm in removed if rm[2] == "near"]
    low = [rm for rm in nears if rm[4] < a.threshold + 0.1]
    shown = rng.sample(low, min(len(low), (a.show + 1) // 2)) if a.show else []
    shown += rng.sample([p for p in nears if p not in shown], min(len(nears) - len(shown), max(0, a.show - len(shown))))
    need = {(rm[0], rm[1]) for rm in shown} | {where(rm[3]) for rm in shown}
    texts = {}
    by_split = defaultdict(dict)
    for rm in removed:
        by_split[rm[0]][rm[1]] = rm
    with open(out / "removed.jsonl", "w") as fr:
        for name in order:
            b = meta[name]["base"]
            with open(out / f"{name}.dedup.jsonl", "w") as fk:
                for i, line, r in iter_rows(meta[name]["path"]):
                    if kept_mask[b + i]:
                        fk.write(line if line.endswith("\n") else line + "\n")
                    if i in by_split[name]:
                        _, _, reason, m, j = by_split[name][i]
                        ms, mi = where(m)
                        fr.write(json.dumps({"split": name, "row": rid(name, i), "reason": reason, "match_split": ms,
                                             "match_row": rid(ms, mi), "similarity": j, "data": r}, ensure_ascii=False) + "\n")
                    if (name, i) in need:
                        texts[(name, i)] = mask(r["text"], r["spans"], lambda lab: f"<{lab}>")
    log("wrote rows")

    ov = overlap(sp, meta, order, kept_mask, rid)
    # diversity per split / language / generator on kept rows
    langs, gens = {c: l for l, c in LANGS.items()}, {c: g for g, c in GENS.items()}
    person = LABELS.get("PERSON", -1)
    pm = (sp["label"] == person) & kept_mask[sp["row"]]
    sp_person = (sp["row"][pm], sp["val"][pm])
    div, refill = {}, {}
    for name in order:
        b, n = meta[name]["base"], meta[name]["n"]
        kg_ = b + np.nonzero(kept_mask[b:b + n])[0]
        groups = {("all", "all"): kg_}
        for c in np.unique(pool.lang[kg_]):
            groups[("lang", langs[c])] = kg_[pool.lang[kg_] == c]
        for c in np.unique(pool.gen[kg_]):
            if gens[c]:
                groups[("gen", gens[c])] = kg_[pool.gen[kg_] == c]
        div[name] = {kind: {} for kind in ("all", "lang", "gen")}
        for (kind, key), g in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
            div[name][kind][key] = diversity(g, pool, sp_person, a.sample, rng)
        if name in targets:
            cnt = Counter(zip(pool.lang[kg_].tolist(), pool.plan[kg_].tolist()))
            per_lang = Counter()
            for (l, _), c in cnt.items():
                per_lang[l] += c
            below = {}
            for l in sorted(LANGS.values(), key=lambda l: langs[l]):
                if langs[l] and per_lang[l] < targets[name]:
                    below[langs[l]] = {"kept": per_lang[l], "deficit": targets[name] - per_lang[l],
                                       "by_generator": {gens[gc] or "?": c for (ll, gc), c in cnt.items() if ll == l}}
            refill[name] = {"target_per_language": targets[name], "languages_below": below,
                            "total_deficit": sum(v["deficit"] for v in below.values())}
    log("overlap, diversity, refill done")

    near_sims = defaultdict(list)
    for rm in nears:
        near_sims[f"{rm[0]}<-{where(rm[3])[0]}"].append(rm[4])
    report = {"date": "2026-10-29", "order": order, "inputs": {n: meta[n]["path"] for n in order},
              "params": {**{k: getattr(a, k) for k in ("threshold", "perms", "bands", "min_words", "min_chars", "sample", "seed")}, "caps": caps},
              "counts": {n: dict(stats[n]) for n in order},
              "near_similarity_hist": {k: unit_hist(v) for k, v in near_sims.items()},
              "control": ctrl, "overlap": ov, "diversity": div, "refill": refill}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (out / "report.md").write_text(markdown(report, order))
    log(f"wrote {out}")

    for rm in shown:
        ms, mi = where(rm[3])
        print(f"\n--- {rm[0]}:{rid(rm[0], rm[1])} vs {ms}:{rid(ms, mi)}  J={rm[4]}")
        print("  A:", texts[(rm[0], rm[1])][:150].replace("\n", " / "))
        print("  B:", texts[(ms, mi)][:150].replace("\n", " / "))


def markdown(rep, order):
    L = [f"# Split dedupe report ({rep['date']})", "", f"Priority: {' > '.join(order)} · threshold {rep['params']['threshold']} "
         f"(exact Jaccard of masked 5-gram sets) · caps {rep['params']['caps']} · inputs: " + ", ".join(f"{n}={p}" for n, p in rep["inputs"].items()), "",
         "## Removed", "", "| Split | Rows | Exact (in split) | Exact (cross) | Near | Exempt short | Queries at cap | Kept |", "|---|---|---|---|---|---|---|---|"]
    for n in order:
        c = rep["counts"][n]
        L.append(f"| {n} | {c['rows']:,} | {c.get('exact', 0):,} | {c.get('exact_cross', 0):,} | {c.get('near', 0):,} | "
                 f"{c.get('exempt_short', 0):,} | {c.get('queries_capped', 0):,} | {c['kept']:,} |")
    if rep["near_similarity_hist"]:
        L += ["", "Near-duplicate Jaccard: " + "; ".join(f"{k} {v}" for k, v in rep["near_similarity_hist"].items())]
    c = rep["control"]
    if c:
        L += ["", f"Near-duplicate recall on perturbed copies ({c['n']} {order[0]} rows; PII values swapped, 10% of words shuffled, "
              f"punctuation changed): " + "; ".join(f"cap {k}: {v['recall']:.1%} (original among LSH candidates {v['original_in_candidates']:.1%}, "
                                                     f"queries at cap {v['queries_capped']})" for k, v in c["by_cap"].items())
              + f". Exact Jaccard copy vs original: median {c['jaccard_to_original_median']}, >= threshold {c['jaccard_to_original_ge_threshold']:.1%}; "
              f"median with one perturbation only: " + ", ".join(f"{k} {v}" for k, v in c["median_jaccard_single_perturbation"].items()) + "."]
    L += ["", f"## PII-value overlap with {order[0]}", "", "Exact = span text as is; norm = NFKC, casefold, no whitespace/punctuation, digits only for numeric labels.", "",
          "| Split | Spans | Seen exact | Seen norm | Distinct seen exact | Distinct seen norm | Rows with PII | All unseen exact | All unseen norm |",
          "|---|---|---|---|---|---|---|---|---|"]
    for n, o in rep["overlap"].items():
        L.append(f"| {n} | {o['spans']:,} | {o['span_seen_share_exact']:.1%} | {o['span_seen_share_norm']:.1%} | {o['distinct_seen_share_exact']:.1%} | "
                 f"{o['distinct_seen_share_norm']:.1%} | {o['rows_with_spans']:,} | {o['rows_all_unseen_exact']:,} | {o['rows_all_unseen_norm']:,} |")
    for n, o in rep["overlap"].items():
        L += ["", f"{n} per label", "", "| Label | Spans | Seen exact | Unseen exact | Seen norm | Unseen norm |", "|---|---|---|---|---|---|"]
        L += [f"| {l} | {v['spans']:,} | {v['seen_exact']:,} | {v['unseen_exact']:,} | {v['seen_norm']:,} | {v['unseen_norm']:,} |" for l, v in o["per_label"].items()]
    hdr = "| {} | Rows | Sampled | Distinct 3-gram | PERSON top-100 share | Mean MinHash sim |"
    row = lambda k, m: (f"| {k}{' (char)' if m['char_mode_share'] > 0.5 else ''} | {m['rows']:,} | {m['sampled']:,} | {m['distinct_3gram']:.3f} | "  # noqa: E731
                        f"{m['person_top100_share']:.1%} | {m['mean_minhash_sim'] or 0:.4f} |")
    L += ["", "## Diversity (kept rows, masked text)", "", hdr.format("Split"), "|---|---|---|---|---|---|"]
    L += [row(n, rep["diversity"][n]["all"]["all"]) for n in order]
    for n in order:
        if rep["diversity"][n]["gen"]:
            L += ["", f"{n} by generator", "", hdr.format("Generator"), "|---|---|---|---|---|---|"]
            L += [row(g, m) for g, m in rep["diversity"][n]["gen"].items()]
    langs = sorted(rep["diversity"][order[0]]["lang"].items(), key=lambda x: x[1]["person_top100_share"])
    for title, sel in (("most diverse (lowest PERSON top-100 share)", langs[:10]), ("least diverse (highest PERSON top-100 share)", langs[::-1][:10])):
        L += ["", f"{order[0]} languages, 10 {title}", "", hdr.format("Language"), "|---|---|---|---|---|---|"]
        L += [row(l, m) for l, m in sel]
    for n, f in rep["refill"].items():
        L += ["", f"## Refill: {n} (target {f['target_per_language']} rows per language)", ""]
        if not f["languages_below"]:
            L.append("All languages at or above target.")
            continue
        L += [f"{len(f['languages_below'])} languages below target, total deficit {f['total_deficit']:,}.", "",
              "| Language | Kept | Deficit | Kept by planned generator |", "|---|---|---|---|"]
        L += [f"| {l} | {v['kept']} | {v['deficit']} | {', '.join(f'{g} {c}' for g, c in sorted(v['by_generator'].items()))} |"
              for l, v in f["languages_below"].items()]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
