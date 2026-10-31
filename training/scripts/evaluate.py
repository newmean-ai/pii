"""PII masking evaluation harness.

Metrics (masking perspective):
  leak      share of gold PII characters not covered by any prediction (lower is better, top priority)
  overmask  share of non-PII characters that get masked (lower is better)
  span_f1   span F1 with matching labels (prediction/gold overlap >= 50%)
  exact_f1  span F1 with exact boundaries and label
Usage: python scripts/evaluate.py --model <hf id or local path | regex | ext:<EXT key>> --sets pii_multilingual_test kdpii_test openpii_val
  Each set name reads data/eval/<name>.jsonl: pii_multilingual_test is the public test dataset (data/test.jsonl of
  newmean-ai/pii-multilingual-test on the Hugging Face Hub, saved under that name); the four external sets
  (kdpii_test, openpii_val, nemotron_test, gretel_fin_test) are rebuilt by ext_data.py eval.
  Our own trained models (train_ner.py's final/ folder, with tags.json) are decoded from BIO into spans directly, with the
  tag list recorded in that model's tags.json (d12 models: 10 labels, v1 models: 13).

Label schemes (2026-10-29, data v1.0):
  --scheme d12 (default)  the earlier behaviour, unchanged.
  --scheme v1             for public detectors: map_label() and the EXT maps send secret-like labels (password, passcode,
                          api key, access/private/secret key, token, PIN, OTP, credential, secret …) to SECRET instead of
                          dropping them / OTHER, and IP labels to ID_NUMBER (the v1 model label for IP_ADDRESS).
                          Our own models are unaffected (they output their own labels).
  --collapse auto|on|off  v1 gold has PERSON_L2 and URL_L2 (PII level 2). auto (default): models that output *_L2 labels
                          (our v1 models) are scored on the 13-label gold natively; every other model (public detectors,
                          regex, d12 models) is scored against gold collapsed to its granularity (PERSON_L2 → PERSON,
                          URL_L2 → URL; subtypes kept, so levels still count). A no-op on gold without *_L2 labels.
Report keys added for v1 (score(); all earlier keys and definitions unchanged):
  ALL / lang:<xx> / src:<s> / gen:<model> / domain / format   leak, overmask, span_f1, exact_f1, entity_leak(_strict)
  labels / exact_by_label        per-label span F1 (overlap) / exact-boundary F1
  leak_by_label / strict_leak_by_label   per gold label character miss rate (any label / same label)
  levels {"L1", "L2"}           per PII level of the gold span (L2 = PERSON surname/alias/initials, URL public, or *_L2):
                                chars, leak, strict_leak, entities, entity_leak, recall (overlap, same label), exact_recall.
                                Recall-type only: over-masking has no level, and collapsed predictions carry none.
"""
import argparse, json, os, re, sys, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L2_SUBTYPES = {"PERSON": {"surname", "alias", "initials"}, "URL": {"public"}}   # PII level 2 (fixed by subtype, data v1.0)
SECRET_KEYS = ("PASSWORD", "PASSWD", "PASSCODE", "PASSPHRASE", "API_KEY", "APIKEY", "ACCESS_KEY", "PRIVATE_KEY", "SECRET",
               "TOKEN", "CREDENTIAL")


def level(label, subtype=None):
    """PII level of a span from its data or model label: 2 for *_L2 and the level-2 subtypes, else 1."""
    return 2 if label.endswith("_L2") or subtype in L2_SUBTYPES.get(label, ()) else 1


def collapse_label(label):
    """v1 model label → public-detector granularity (PERSON_L2 → PERSON, URL_L2 → URL)."""
    return label[:-3] if label.endswith("_L2") else label


def is_secret(raw: str) -> bool:
    l = re.sub(r"[\s-]+", "_", strip_bio(raw).upper())
    parts = set(l.split("_"))
    return any(k in l for k in SECRET_KEYS) or bool(parts & {"PIN", "OTP"}) and "CODE" not in parts   # PIN_CODE = Indian postcode


def map_label(raw: str, secret: bool = False):
    """Generic label of a public HF token classifier → our label. secret=True (--scheme v1) adds the SECRET rule."""
    l = raw.upper().replace("B-", "").replace("I-", "")
    if l in ("O", ""):
        return None
    if "IP_ADDRESS" in l or l in ("IP", "IPV4", "IPV6"):
        return "ID_NUMBER"  # the model label scheme treats IP as ID_NUMBER (IP_ADDRESS is regex-only) — it used to hit the ADDRESS rule first
    if secret and is_secret(l):
        return "SECRET"
    rules = [
        (("EMAIL",), "EMAIL"), (("BIRTH", "DOB"), "DATE_OF_BIRTH"), (("URL", "SITE"), "URL"),
        (("USER", "HANDLE", "NICK_ID"), "USERNAME"), (("PHONE", "TEL", "MOBILE", "FAX"), "PHONE"),
        (("CARD", "ACCOUNT", "BANK", "IBAN", "FINANC"), "FINANCIAL"),
        (("STREET", "ADDRESS", "ZIP", "POSTAL", "BUILDING"), "ADDRESS"),
        (("RRN", "SSN", "PASSPORT", "LICENSE", "DRIVER", "ALIEN", "IPIN", "TAX", "NATIONAL", "ID_NUM", "IDCARD",
          "PLATE", "VEHICLE", "SOCIAL", "ACCOUNT_NUMBER", "ID_NUMBER", "_IP", "IP_ADDRESS"), "ID_NUMBER"),
        (("ORG", "COMPANY", "DEPARTMENT", "SCHOOL", "EMPLOYER"), "ORG"),
        (("PERSON", "NAME", "PER"), "PERSON"),
    ]
    for keys, lab in rules:
        if any(k in l for k in keys):
            return lab
    if l in ("ID", "USERID"):
        return "USERNAME"
    return None


# --- deterministic baseline (regex only) ---
REGEX = [
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("URL", r"https?://\S+|www\.\S+"),
    ("ID_NUMBER", r"\b\d{6}[-\s]?[1-8]\d{6}\b"),
    ("FINANCIAL", r"\b(?:\d{4}[-\s]?){3}\d{4}\b|\b\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,3})?\b"),
    ("PHONE", r"(?:\+\d{1,3}[\s-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}\b"),
]


def regex_predict(text):
    out, taken = [], []
    for lab, pat in REGEX:
        for m in re.finditer(pat, text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append({"start": m.start(), "end": m.end(), "label": lab})
    return out


class HFPredictor:
    labels = None   # unknown label set → scored against collapsed gold

    def __init__(self, model_id, device, secret=False):
        from transformers import pipeline
        self.pipe = pipeline("token-classification", model=model_id, aggregation_strategy="simple", device=device)
        self.secret = secret

    def __call__(self, texts):
        res = self.pipe(texts, batch_size=32)
        out = []
        for ents in res:
            spans = []
            for e in ents:
                lab = map_label(e["entity_group"], self.secret)
                if lab:
                    spans.append({"start": int(e["start"]), "end": int(e["end"]), "label": lab})
            out.append(spans)
        return out


O_BIAS = float(os.environ.get("PII_O_BIAS", 0))   # added to the O logit before decoding (2026-10-30 threshold sweep)


class TaggerPredictor:
    """Reads the final/ folder left by train_ner.py (weights_fp16.safetensors + tags.json + tokenizer) and decodes BIO -> spans."""

    def __init__(self, model_dir, device):
        import torch
        from safetensors.torch import load_file
        from transformers import AutoModelForTokenClassification, AutoTokenizer
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from train_ner import EG2Tagger, labels_of
        meta = json.loads((Path(model_dir) / "tags.json").read_text())
        TAGS = meta["tags"]   # the model's own tag list (d12: 10 labels, v1: 13) — not the train_ner module default
        assert TAGS[0] == "O" and all(t[:2] in ("B-", "I-") for t in TAGS[1:]), f"unexpected tags in {model_dir}/tags.json"
        self.torch, self.device, self.tags, self.labels = torch, device, TAGS, labels_of(TAGS)
        self.normalize = meta.get("normalize") == "nfkc_char"   # the model was trained on per-character NFKC text (textnorm)
        self.tok = AutoTokenizer.from_pretrained(model_dir)
        if "embeddinggemma-2" in meta["base"]:
            self.model = EG2Tagger(meta["base"], meta.get("freeze", False), meta.get("head", "linear"), len(TAGS))
        elif (Path(model_dir) / "config.json").exists():
            from transformers import AutoConfig
            self.model = AutoModelForTokenClassification.from_config(AutoConfig.from_pretrained(model_dir))
            if getattr(self.model.config, "pii_arch", None):   # compressed student tier: rebuild its modules before loading the weights
                from student_arch import apply_arch
                apply_arch(self.model, self.model.config.pii_arch)
        else:
            self.model = AutoModelForTokenClassification.from_pretrained(
                meta["base"], num_labels=len(TAGS), id2label=dict(enumerate(TAGS)), label2id={t: i for i, t in enumerate(TAGS)})
        self.model.load_state_dict({k: v.float() for k, v in load_file(str(Path(model_dir) / "weights_fp16.safetensors")).items()})
        self.model.to(device).eval()

    WINDOW, STRIDE = 256, 64   # same length as training (train_ner --max_len 256); long texts are read in overlapping windows

    def __call__(self, texts, bs=32):
        out = []
        for i in range(0, len(texts), bs):
            batch = texts[i:i + bs]
            from textnorm import normalize, to_original
            normed = [normalize(t) for t in batch] if self.normalize else None
            enc = self.tok([n for n, _ in normed] if normed else batch, truncation=True, max_length=self.WINDOW, stride=self.STRIDE, return_overflowing_tokens=True,
                           padding=True, return_offsets_mapping=True, return_tensors="pt")
            offs = enc.pop("offset_mapping").tolist()
            owner = enc.pop("overflow_to_sample_mapping").tolist()
            if normed:
                offs = [to_original(o, normed[j][1]) for o, j in zip(offs, owner)]
            with self.torch.no_grad():
                o = self.model(**{k: v.to(self.device) for k, v in enc.items()})
                logits = o["logits"] if isinstance(o, dict) else o.logits
            if O_BIAS:   # decision threshold: + favours "not PII" (less over-masking), − favours masking (fewer misses)
                logits = logits.clone(); logits[..., 0] += O_BIAS
            # each token prediction claims its characters; where windows overlap, the window in which the token sits
            # farther from the edge wins (more context on both sides)
            best = [dict() for _ in batch]   # char start → (distance from window edge, token (s, e), tag)
            for off, pred, j in zip(offs, logits.argmax(-1).tolist(), owner):
                toks = [(k, s, e) for k, (s, e) in enumerate(off) if s != e]
                for rank, (k, s, e) in enumerate(toks):
                    d = min(rank, len(toks) - 1 - rank)
                    if s not in best[j] or d > best[j][s][0]:
                        best[j][s] = (d, e, self.tags[pred[k]])
            for b in best:
                spans, cur = [], None
                for s in sorted(b):
                    _, e, tag = b[s]
                    if tag == "O":
                        cur = None
                        continue
                    bio, lab = tag.split("-", 1)
                    if cur is not None and cur["label"] == lab and bio == "I":
                        cur["end"] = max(cur["end"], e)
                    else:
                        cur = {"start": s, "end": e, "label": lab}
                        spans.append(cur)
                out.append(spans)
        return out


# --- external open models (commercially usable only; head-to-head comparison, see the technical report) ---
# map: labels that model "masks" -> our labels. Masking outside our scheme is OTHER (counts toward leak/overmask, wrong in span F1).
# Labels not in map (keep labels such as Mozilla's ORG/LOCATION/PUBLIC_NAME) are treated as not masked — same as the deployed behavior.
EXT = {
    "mozilla": {"dir": "models/ext/smart-pii-redactor-v1-onnx", "onnx": "onnx/model_q4.onnx", "window": 256, "stride": 32,
                "map": {"PERSON": "PERSON", "STREET_ADDRESS": "ADDRESS", "DOB": "DATE_OF_BIRTH", "ID_IN_CONTEXT": "ID_NUMBER",
                        "SECRET_IN_CONTEXT": "OTHER", "USERNAME": "USERNAME"}},
    "rampart": {"dir": "models/ext/rampart", "onnx": "onnx/model_q4.onnx", "window": 512, "stride": 128,
                "map": {"GIVEN_NAME": "PERSON", "SURNAME": "PERSON", "EMAIL": "EMAIL", "PHONE": "PHONE", "URL": "URL",
                        "BUILDING_NUMBER": "ADDRESS", "STREET_NAME": "ADDRESS", "SECONDARY_ADDRESS": "ADDRESS", "CITY": "ADDRESS",
                        "STATE": "ADDRESS", "ZIP_CODE": "ADDRESS", "GOVERNMENT_ID": "ID_NUMBER", "PASSPORT": "ID_NUMBER",
                        "DRIVERS_LICENSE": "ID_NUMBER", "TAX_ID": "ID_NUMBER", "BANK_ACCOUNT": "FINANCIAL", "ROUTING_NUMBER": "FINANCIAL"}},
    "rampart-edge": {"dir": "models/ext/rampart-edge", "onnx": "onnx/model_q4.onnx", "window": 384, "stride": 96, "out": "token_logits",
                     "map": {"NAME": "PERSON", "ADDRESS": "ADDRESS", "EMAIL": "EMAIL", "PHONE": "PHONE", "GOV_ID": "ID_NUMBER",
                             "FINANCIAL": "FINANCIAL", "DOB_KEYED": "DATE_OF_BIRTH", "URL_HANDLE": "URL", "OTHER_ID": "ID_NUMBER"}},
    "openai-pf": {"dir": "models/ext/privacy-filter", "hf": True,
                  "map": {"private_person": "PERSON", "private_email": "EMAIL", "private_phone": "PHONE", "private_address": "ADDRESS",
                          "private_url": "URL", "private_date": "DATE_OF_BIRTH", "account_number": "FINANCIAL", "secret": "OTHER"}},
    # For GLiNER we choose the labels to query: those matching our scheme + the names from the model card. ORG is not a PII label in either model, so it is not queried
    "gliner-edge": {"dir": "models/ext/gliner-pii-edge-v1.0", "gliner": "gliner", "threshold": 0.3,
                    "map": {"name": "PERSON", "email address": "EMAIL", "phone number": "PHONE", "location address": "ADDRESS",
                            "dob": "DATE_OF_BIRTH", "username": "USERNAME", "url": "URL", "ssn": "ID_NUMBER", "passport number": "ID_NUMBER",
                            "driver license": "ID_NUMBER", "credit card": "FINANCIAL", "bank account": "FINANCIAL",
                            "password": "OTHER", "ip address": "OTHER"}},
    "gliner2-pii": {"dir": "models/ext/gliner2-privacy-filter-PII-multi", "gliner": "gliner2", "threshold": 0.5,
                    "map": {"person": "PERSON", "email": "EMAIL", "phone_number": "PHONE", "address": "ADDRESS",
                            "date_of_birth": "DATE_OF_BIRTH", "username": "USERNAME", "national_id_number": "ID_NUMBER",
                            "passport_number": "ID_NUMBER", "drivers_license_number": "ID_NUMBER", "government_id": "ID_NUMBER",
                            "bank_account": "FINANCIAL", "card_number": "FINANCIAL", "iban": "FINANCIAL",
                            "ip_address": "OTHER", "password": "OTHER", "api_key": "OTHER"}},
}


def strip_bio(raw: str) -> str:
    return re.sub(r"^[BIES]-", "", raw)


def ext_spec(key, scheme="d12"):
    """EXT entry for a scheme. v1: secret-like labels mapped to OTHER become SECRET, IP labels become ID_NUMBER (the v1 gold
    labels for them); everything else — window, threshold, queried GLiNER labels — stays as before (same adapters)."""
    spec = dict(EXT[key])
    if scheme == "v1":
        spec["map"] = {k: ("SECRET" if v == "OTHER" and is_secret(k) else
                           "ID_NUMBER" if v == "OTHER" and map_label(re.sub(r"[\s-]+", "_", k)) == "ID_NUMBER" else v) for k, v in spec["map"].items()}
    return spec


def merge_adjacent(spans, text):
    """Merges same-label spans separated only by whitespace (Rampart's GIVEN_NAME + SURNAME -> a single PERSON)."""
    out = []
    for sp in sorted(spans, key=lambda x: x["start"]):
        if out and out[-1]["label"] == sp["label"] and not text[out[-1]["end"]:sp["start"]].strip():
            out[-1]["end"] = max(out[-1]["end"], sp["end"])
        else:
            out.append(dict(sp))
    return out


class OnnxTokenPredictor:
    """Runs a transformers.js ONNX token classifier (quantized file as-is) with onnxruntime. Long texts are split using the model card's window/stride,
    and overlapping tokens take the prediction from the window where they sit farther from the edge."""

    def __init__(self, spec):
        import numpy as np
        import onnxruntime as ort
        from transformers import AutoTokenizer
        d = ROOT / spec["dir"]
        self.np, self.spec = np, spec
        self.labels = sorted(set(spec["map"].values()))
        self.tok = AutoTokenizer.from_pretrained(d)
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("ORT_THREADS", 0))   # several evaluations at once on one machine (2026-10-29)
        self.sess = ort.InferenceSession(str(d / spec["onnx"]), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        cfg = json.loads((d / "config.json").read_text())
        self.id2label = ({int(k): v for k, v in cfg["id2label"].items()} if "id2label" in cfg
                         else dict(enumerate(cfg["span_labels"])))

    def _run(self, windows):
        np = self.np
        L = max(len(w) for w in windows)
        ids = np.full((len(windows), L), self.tok.pad_token_id, dtype=np.int64)
        mask = np.zeros((len(windows), L), dtype=np.int64)
        for i, w in enumerate(windows):
            ids[i, :len(w)] = w
            mask[i, :len(w)] = 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        outs = self.sess.run([self.spec.get("out", "logits")], feed)[0]
        return outs

    def __call__(self, texts, bs=32):
        win, stride = self.spec["window"] - 2, self.spec["stride"]
        cls, sep = self.tok.cls_token_id, self.tok.sep_token_id
        encs = [self.tok(t, add_special_tokens=False, return_offsets_mapping=True) for t in texts]
        jobs = []  # (text index, start token, window tokens)
        for k, e in enumerate(encs):
            ids = e["input_ids"]
            starts = list(range(0, max(1, len(ids) - stride), win - stride)) if len(ids) > win else [0]
            for st in starts:
                jobs.append((k, st, [cls] + ids[st:st + win] + [sep]))
        best = [dict() for _ in texts]  # token position -> (distance from edge, label index)
        for i in range(0, len(jobs), bs):
            chunk = jobs[i:i + bs]
            logits = self._run([w for _, _, w in chunk])
            for (k, st, w), lg in zip(chunk, logits):
                n = len(w) - 2
                lg = lg.copy() if O_BIAS else lg
                if O_BIAS:
                    lg[..., 0] += O_BIAS
                pred = lg[1:n + 1].argmax(-1)
                for j in range(n):
                    c = min(j, n - 1 - j)
                    if st + j not in best[k] or c > best[k][st + j][0]:
                        best[k][st + j] = (c, int(pred[j]))
        out = []
        for k, e in enumerate(encs):
            spans, cur = [], None
            for j, (s, t) in enumerate(e["offset_mapping"]):
                raw = self.id2label[best[k][j][1]] if j in best[k] else "O"
                lab = self.spec["map"].get(strip_bio(raw)) if raw != "O" else None
                if s == t:
                    continue
                if lab is None:
                    cur = None
                    continue
                if cur is not None and cur["label"] == lab and not raw.startswith(("B-", "S-")):
                    cur["end"] = t
                else:
                    cur = {"start": s, "end": t, "label": lab}
                    spans.append(cur)
            out.append(merge_adjacent(spans, texts[k]))
        return out


class ExtHFPredictor(HFPredictor):
    """transformers pipeline + per-model label map (OpenAI privacy-filter: BIOES, originally Viterbi decoding — pipeline default here)."""

    def __init__(self, spec, device):
        super().__init__(str(ROOT / spec["dir"]), device)
        self.map = spec["map"]
        self.labels = sorted(set(self.map.values()))

    def __call__(self, texts):
        # empty texts crash pipeline padding (1 in openpii_val, 2026-10-28) -> skip them and return an empty prediction
        idx = [i for i, t in enumerate(texts) if t.strip()]
        got = dict(zip(idx, self.pipe([texts[i] for i in idx], batch_size=8)))
        res = [got.get(i, []) for i in range(len(texts))]
        return [merge_adjacent([{"start": int(e["start"]), "end": int(e["end"]), "label": self.map[strip_bio(e["entity_group"])]}
                                for e in ents if strip_bio(e["entity_group"]) in self.map], t) for t, ents in zip(texts, res)]


class GlinerPredictor:
    """GLiNER (gliner) / GLiNER2 (gliner2) zero-shot span extraction — runs in a separate venv (.venv-gliner) to avoid transformers version conflicts."""

    def __init__(self, spec, device):
        self.spec, self.queries = spec, list(spec["map"])
        self.labels = sorted(set(spec["map"].values()))
        if spec["gliner"] == "gliner":
            from gliner import GLiNER
            self.model = GLiNER.from_pretrained(str(ROOT / spec["dir"]), load_tokenizer=True).to(device)
        else:
            from gliner2 import GLiNER2
            self.model = GLiNER2.from_pretrained(str(ROOT / spec["dir"]))
            try:
                self.model.to(device)
            except Exception:
                pass

    def __call__(self, texts, bs=16):
        th, mp, out = self.spec["threshold"], self.spec["map"], []
        if self.spec["gliner"] == "gliner":
            res = self.model.inference(texts, self.queries, threshold=th, batch_size=bs)
            out = [[{"start": e["start"], "end": e["end"], "label": mp[e["label"]]} for e in ents] for ents in res]
        else:
            # PII_GLINER2_LONG=1 (2026-10-31): GLiNER2's own long-text path (overlapping 384-word chunks, 64 overlap, merged by
            # the library), after the whole-text call ran out of the L4's memory on Gretel even at batch 1
            if os.environ.get("PII_GLINER2_LONG") == "1":
                res = self.model.batch_extract_entities_long(texts, self.queries, batch_size=bs, threshold=th, include_spans=True,
                                                             chunk_size=384, chunk_overlap=64)
            else:
                res = self.model.batch_extract_entities(texts, self.queries, batch_size=bs, threshold=th, include_spans=True)
            for r in res:
                out.append([{"start": v["start"], "end": v["end"], "label": mp[lab]}
                            for lab, vals in r.get("entities", {}).items() for v in vals if isinstance(v, dict) and "start" in v])
        return [merge_adjacent(sp, t) for sp, t in zip(out, texts)]


def score(rows, preds):
    agg = defaultdict(lambda: {"pii": 0, "leak": 0, "non": 0, "over": 0, "tp": 0, "fp": 0, "fn": 0})
    lab_agg = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    exact = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})   # exact boundaries + label (the overlap F1 above accepts ≥ 50% overlap)
    ent = defaultdict(lambda: [0, 0, 0])   # gold entities, missed (< 50% of characters covered by any prediction), missed by same label
    lab_leak = defaultdict(lambda: [0, 0])  # gold label -> [PII chars, missed chars] — used to compare models with different label schemes
    strict = defaultdict(lambda: [0, 0])    # gold label -> [PII chars, chars not covered by a same-label prediction] — what leaks for a user masking only that label
    ex_lab = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})   # exact boundaries + label, per label
    lev = defaultdict(lambda: defaultdict(int))   # gold PII level -> chars, leak, strict, entities, entity misses, span hits, exact hits
    for r, p in zip(rows, preds):
        n = len(r["text"])
        gold_mask, pred_mask, ign = [False] * n, [False] * n, [False] * n
        for s in r.get("ignore", []):   # external sets: spans of types outside our scheme (company, date, city alone …) — neither PII nor over-masking
            for i in range(max(0, s["start"]), min(s["end"], n)):
                ign[i] = True
        for s in r["spans"]:
            for i in range(s["start"], min(s["end"], n)):
                gold_mask[i] = not r["text"][i].isspace()
        for s in p:
            for i in range(max(0, s["start"]), min(s["end"], n)):
                pred_mask[i] = True
        by_lab = defaultdict(set)
        for ps in p:
            by_lab[ps["label"]].update(range(max(0, ps["start"]), min(ps["end"], n)))
        for s in r["spans"]:
            cs = [i for i in range(s["start"], min(s["end"], n)) if not r["text"][i].isspace()]
            lab_leak[s["label"]][0] += len(cs)
            lab_leak[s["label"]][1] += sum(not pred_mask[i] for i in cs)
            strict[s["label"]][0] += len(cs)
            strict[s["label"]][1] += sum(i not in by_lab[s["label"]] for i in cs)
        matched = set()
        tp = fp = 0
        for ps in p:
            hit = None
            for gi, gs in enumerate(r["spans"]):
                ov = min(ps["end"], gs["end"]) - max(ps["start"], gs["start"])
                if gi not in matched and gs["label"] == ps["label"] and ov >= 0.5 * (gs["end"] - gs["start"]):
                    hit = gi
                    break
            if hit is None:
                width = max(1, min(ps["end"], n) - max(0, ps["start"]))
                if sum(ign[i] for i in range(max(0, ps["start"]), min(ps["end"], n))) >= 0.5 * width:
                    continue   # mostly on an ignored span — not a false positive
                fp += 1
                lab_agg[ps["label"]]["fp"] += 1
            else:
                tp += 1
                matched.add(hit)
                lab_agg[ps["label"]]["tp"] += 1
        for gi, gs in enumerate(r["spans"]):
            if gi not in matched:
                lab_agg[gs["label"]]["fn"] += 1
        fn = len(r["spans"]) - len(matched)
        gold_set = {(s["start"], s["end"], s["label"]) for s in r["spans"]}
        pred_set = {(s["start"], s["end"], s["label"]) for s in p}
        # a prediction mostly on an ignored span is no false positive for exact F1 either (ignore applies to over-masking,
        # the overlap F1 and the exact F1 alike)
        on_ign = lambda t: sum(ign[i] for i in range(max(0, t[0]), min(t[1], n))) >= 0.5 * max(1, min(t[1], n) - max(0, t[0]))
        pred_set = {t for t in pred_set if t in gold_set or not on_ign(t)}
        ex = (len(gold_set & pred_set), len(pred_set - gold_set), len(gold_set - pred_set))
        for t in gold_set & pred_set:
            ex_lab[t[2]]["tp"] += 1
        for t in pred_set - gold_set:
            ex_lab[t[2]]["fp"] += 1
        for t in gold_set - pred_set:
            ex_lab[t[2]]["fn"] += 1
        for gi, s in enumerate(r["spans"]):
            cs = [i for i in range(s["start"], min(s["end"], n)) if not r["text"][i].isspace()]
            if not cs:
                continue
            L = lev[f"L{level(s['label'], s.get('subtype'))}"]
            L["chars"] += len(cs)
            L["leak"] += sum(not pred_mask[i] for i in cs)
            L["strict"] += sum(i not in by_lab[s["label"]] for i in cs)
            L["ent"] += 1
            L["ent_miss"] += sum(pred_mask[i] for i in cs) < 0.5 * len(cs)
            L["hit"] += gi in matched
            L["exact"] += (s["start"], s["end"], s["label"]) in pred_set
        ent_n = ent_miss = ent_miss_strict = 0
        for s in r["spans"]:
            cs = [i for i in range(s["start"], min(s["end"], n)) if not r["text"][i].isspace()]
            if not cs:
                continue
            ent_n += 1
            ent_miss += sum(pred_mask[i] for i in cs) < 0.5 * len(cs)
            ent_miss_strict += sum(i in by_lab[s["label"]] for i in cs) < 0.5 * len(cs)
        spec = r.get("spec") or {}
        keys = ["ALL", f"lang:{r['lang']}", f"src:{r.get('source', '')}"] + ([f"gen:{r['gen']}"] if r.get("gen") else [])
        if r.get("domain") or spec.get("domain"):   # benchmark breakdown by domain and text format as well as language (2026-10-28)
            keys.append(f"domain:{r.get('domain') or spec.get('domain')}")
        if spec.get("format") or r.get("doc_type"):
            keys.append(f"format:{spec.get('format') or r.get('doc_type')}")
        for k in keys:
            a = agg[k]
            a["pii"] += sum(gold_mask)
            a["leak"] += sum(g and not q for g, q in zip(gold_mask, pred_mask))
            a["non"] += sum((not g) and not ign[i] and not r["text"][i].isspace() for i, g in enumerate(gold_mask))
            a["over"] += sum((not g) and q and not ign[i] and not r["text"][i].isspace() for i, (g, q) in enumerate(zip(gold_mask, pred_mask)))
            a["tp"] += tp; a["fp"] += fp; a["fn"] += fn
            x = exact[k]; x["tp"] += ex[0]; x["fp"] += ex[1]; x["fn"] += ex[2]
            e = ent[k]; e[0] += ent_n; e[1] += ent_miss; e[2] += ent_miss_strict

    def f1(d):
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else 0
        rc = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else 0
        return round(2 * p * rc / (p + rc), 3) if p + rc else 0.0
    summary = {k: {"leak": round(a["leak"] / a["pii"], 4) if a["pii"] else None,
                   "overmask": round(a["over"] / a["non"], 4) if a["non"] else None,
                   "span_f1": f1(a), "exact_f1": f1(exact[k]),
                   "entity_leak": round(ent[k][1] / ent[k][0], 4) if ent[k][0] else None,
                   "entity_leak_strict": round(ent[k][2] / ent[k][0], 4) if ent[k][0] else None} for k, a in sorted(agg.items())}
    summary["labels"] = {k: f1(v) for k, v in sorted(lab_agg.items())}
    summary["exact_by_label"] = {k: f1(v) for k, v in sorted(ex_lab.items())}
    rd = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    summary["levels"] = {k: {"chars": v["chars"], "leak": rd(v["leak"], v["chars"]), "strict_leak": rd(v["strict"], v["chars"]),
                             "entities": v["ent"], "entity_leak": rd(v["ent_miss"], v["ent"]), "recall": rd(v["hit"], v["ent"]),
                             "exact_recall": rd(v["exact"], v["ent"])} for k, v in sorted(lev.items())}
    summary["leak_by_label"] = {k: round(v[1] / v[0], 4) for k, v in sorted(lab_leak.items()) if v[0]}
    summary["strict_leak_by_label"] = {k: round(v[1] / v[0], 4) for k, v in sorted(strict.items()) if v[0]}
    tot = [sum(v[0] for v in strict.values()), sum(v[1] for v in strict.values())]
    summary["ALL"]["strict_leak"] = round(tot[1] / tot[0], 4) if tot[0] else None
    return summary


def trim_spans(text: str, spans: list) -> list:
    """Strip whitespace at span edges (exact-boundary F1 would otherwise count ' Maria' ≠ 'Maria'); drop spans left empty."""
    out = []
    for s in spans:
        a, b = max(0, s["start"]), min(len(text), s["end"])
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            out.append({**s, "start": a, "end": b})
    return out


def needs_native(pred):
    """True if the predictor outputs v1 level-2 labels (our v1 models) — then the 13-label gold is scored as is."""
    return bool({"PERSON_L2", "URL_L2"} & set(getattr(pred, "labels", None) or ()))


def collapse_gold(rows):
    """Gold spans relabelled to public-detector granularity (PERSON_L2 → PERSON, URL_L2 → URL); subtype kept for levels."""
    return [{**r, "spans": [{**s, "label": collapse_label(s["label"])} for s in r["spans"]]} for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--sets", nargs="+", default=["pii_multilingual_test", "kdpii_test", "openpii_val", "nemotron_test", "gretel_fin_test"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "mps")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--scheme", choices=["d12", "v1"], default="d12",
                    help="public-detector label mapping: v1 adds SECRET (and IP → ID_NUMBER in EXT maps); see the module docstring")
    ap.add_argument("--collapse", choices=["auto", "on", "off"], default="auto",
                    help="score against gold with PERSON_L2/URL_L2 collapsed (auto: unless the model outputs *_L2 labels)")
    a = ap.parse_args()
    if a.model == "regex":
        pred = None
    elif a.model.startswith("onnx:"):  # our exported model: --model onnx:<dir>[:<file under onnx/>] (export_onnx.py)
        d, _, f = a.model[5:].partition(":")
        labels = json.loads((Path(d) / "tags.json").read_text())["tags"]
        pred = OnnxTokenPredictor({"dir": str(Path(d).resolve()), "onnx": f"onnx/{f or 'model.onnx'}", "window": 256, "stride": 64,
                                   "map": {strip_bio(t): strip_bio(t) for t in labels if t != "O"}})
    elif a.model.startswith("ext:"):  # external open model: --model ext:mozilla
        spec = ext_spec(a.model[4:], a.scheme)
        pred = (GlinerPredictor(spec, a.device) if "gliner" in spec else ExtHFPredictor(spec, a.device) if spec.get("hf")
                else OnnxTokenPredictor(spec))
    elif (Path(a.model) / "tags.json").exists():
        pred = TaggerPredictor(a.model, a.device)
    else:
        pred = HFPredictor(a.model, a.device, secret=a.scheme == "v1")
    collapse = a.collapse == "on" or (a.collapse == "auto" and not needs_native(pred))
    report = {"model": a.model, "scheme": a.scheme, "gold_collapsed": collapse}
    for name in a.sets:
        rows = [json.loads(l) for l in (ROOT / f"data/eval/{name}.jsonl").read_text().split("\n") if l.strip()]
        if a.limit:
            rows = rows[:a.limit]
        if collapse:
            rows = collapse_gold(rows)
        elif a.scheme == "d12" and a.model.startswith("ext:") and any(s["label"] == "SECRET" for r in rows for s in r["spans"]):
            print(f"note: {name} has SECRET gold; --scheme v1 maps the detector's secret labels to SECRET", flush=True)
        t0 = time.time()
        preds = [regex_predict(r["text"]) for r in rows] if pred is None else pred([r["text"] for r in rows])
        preds = [trim_spans(r["text"], p) for r, p in zip(rows, preds)]   # SentencePiece offsets include the leading "▁" space
        if pred is not None:  # also measure model + regex union (the actual deployed setup)
            union = [p + regex_predict(r["text"]) for r, p in zip(rows, preds)]  # keep overlaps (dropping regex hits on overlap would mask only part of an email)
            report[f"{name}+regex"] = score(rows, union)
        report[name] = score(rows, preds)
        # keep per-row predictions for later re-scoring and paired comparison (compare_models.py)
        pd = ROOT / f"reports/preds/{a.tag or re.sub(r'[^\w.-]', '_', a.model)}"
        pd.mkdir(parents=True, exist_ok=True)
        (pd / f"{name}.jsonl").write_text("".join(json.dumps({"i": i, "spans": p}, ensure_ascii=False) + "\n" for i, p in enumerate(preds)))
        report[name]["ms_per_row"] = round((time.time() - t0) * 1000 / max(1, len(rows)), 2)
        print(name, json.dumps(report[name]["ALL"]), "ms/row", report[name]["ms_per_row"], flush=True)
    tag = a.tag or re.sub(r"[^\w.-]", "_", a.model)
    out = ROOT / f"reports/eval_{tag}.json"
    if out.exists():  # merge so that running more eval sets later does not erase earlier results
        report = {**json.loads(out.read_text()), **report}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
