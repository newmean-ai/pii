"""Model adapters: run a detector on texts and return spans [{start, end, label}] in our label scheme.

The predictor classes are vendored from training/scripts/evaluate.py with two changes only: model folders are absolute local
paths (downloaded by the job) instead of paths under the training folder, and transformers models are loaded with
trust_remote_code=False and safetensors weights only (a submitted model must not run its own code inside the scoring job).
tests/test_scoring_parity.py checks that every __call__ (the decoding) is identical to evaluate.py's.

Adapters
  transformers   any Hub token-classification model (transformers pipeline, aggregation "simple") with a label map to our
                 labels: a dict {model label: one of config.MAP_TARGETS}, or "auto" (evaluate.py's generic map_label, v1 scheme)
  newmean-onnx   our released models newmean-ai/pii-<size> (onnx/model_<variant>.onnx, onnxruntime CPU, window 256 / stride 64);
                 variant defaults to the SDK default of the tier (DEFAULT_VARIANT, js/src/redactor.ts): what users get
  ext:<key>      public detectors we added ourselves (EXT below: ONNX, transformers or GLiNER), pinned to a Hub revision
  regex          the regex baseline (no model)
"""
import json, os, re
from pathlib import Path

from .scoring import is_secret, map_label, merge_adjacent, regex_predict, strip_bio

O_BIAS = 0.0   # evaluate.py's PII_O_BIAS decision-threshold experiment; always 0 on the leaderboard

# Public detectors (evaluate.py EXT, with the Hub repos and revisions of the published comparison: training/scripts/public_test_eval.sh, report Section 6).
# env: which isolated environment the job runs the adapter in (jobs/requirements-<env>.txt); GLiNER needs its own transformers.
ONNX_FILES = ["*.json", "*.txt", "onnx/model_q4.onnx"]
EXT = {
    "mozilla": {"repo": "Mozilla/smart-pii-redactor-v1-onnx", "revision": "5febfa13dbaa8771072af90914fee0de8bbcf6b7",
                "files": ONNX_FILES, "env": "hf", "onnx": "onnx/model_q4.onnx", "window": 256, "stride": 32,
                "map": {"PERSON": "PERSON", "STREET_ADDRESS": "ADDRESS", "DOB": "DATE_OF_BIRTH", "ID_IN_CONTEXT": "ID_NUMBER",
                        "SECRET_IN_CONTEXT": "OTHER", "USERNAME": "USERNAME"}},
    "rampart": {"repo": "nationaldesignstudio/rampart", "revision": "b1993e4e68b082835b80ffc65acc03325ea2e501",
                "files": ONNX_FILES, "env": "hf", "onnx": "onnx/model_q4.onnx", "window": 512, "stride": 128,
                "map": {"GIVEN_NAME": "PERSON", "SURNAME": "PERSON", "EMAIL": "EMAIL", "PHONE": "PHONE", "URL": "URL",
                        "BUILDING_NUMBER": "ADDRESS", "STREET_NAME": "ADDRESS", "SECONDARY_ADDRESS": "ADDRESS", "CITY": "ADDRESS",
                        "STATE": "ADDRESS", "ZIP_CODE": "ADDRESS", "GOVERNMENT_ID": "ID_NUMBER", "PASSPORT": "ID_NUMBER",
                        "DRIVERS_LICENSE": "ID_NUMBER", "TAX_ID": "ID_NUMBER", "BANK_ACCOUNT": "FINANCIAL", "ROUTING_NUMBER": "FINANCIAL"}},
    "rampart-edge": {"repo": "nationaldesignstudio/rampart-edge", "revision": "69a99a3f0d3cbdb8db11f2ac97899f654be109bf",
                     "files": ONNX_FILES, "env": "hf", "onnx": "onnx/model_q4.onnx", "window": 384, "stride": 96, "out": "token_logits",
                     "map": {"NAME": "PERSON", "ADDRESS": "ADDRESS", "EMAIL": "EMAIL", "PHONE": "PHONE", "GOV_ID": "ID_NUMBER",
                             "FINANCIAL": "FINANCIAL", "DOB_KEYED": "DATE_OF_BIRTH", "URL_HANDLE": "URL", "OTHER_ID": "ID_NUMBER"}},
    "openai-pf": {"repo": "openai/privacy-filter", "revision": "7ffa9a043d54d1be65afb281eddf0ffbe629385b",
                  "files": ["*.json", "model.safetensors", "original/*.json"], "env": "hf", "hf": True,
                  "map": {"private_person": "PERSON", "private_email": "EMAIL", "private_phone": "PHONE", "private_address": "ADDRESS",
                          "private_url": "URL", "private_date": "DATE_OF_BIRTH", "account_number": "FINANCIAL", "secret": "OTHER"}},
    "gliner-edge": {"repo": "knowledgator/gliner-pii-edge-v1.0", "revision": "9b7f39b0a2da971a5beea78d35f1539d4009c891",
                    "files": None, "env": "gliner", "gliner": "gliner", "threshold": 0.3,
                    "map": {"name": "PERSON", "email address": "EMAIL", "phone number": "PHONE", "location address": "ADDRESS",
                            "dob": "DATE_OF_BIRTH", "username": "USERNAME", "url": "URL", "ssn": "ID_NUMBER", "passport number": "ID_NUMBER",
                            "driver license": "ID_NUMBER", "credit card": "FINANCIAL", "bank account": "FINANCIAL",
                            "password": "OTHER", "ip address": "OTHER"}},
    "gliner2-pii": {"repo": "fastino/gliner2-privacy-filter-PII-multi", "revision": "1cb4166094dc58fa8d836429f060d6c95f62b495",
                    "files": None, "env": "gliner", "gliner": "gliner2", "threshold": 0.5,
                    "map": {"person": "PERSON", "email": "EMAIL", "phone_number": "PHONE", "address": "ADDRESS",
                            "date_of_birth": "DATE_OF_BIRTH", "username": "USERNAME", "national_id_number": "ID_NUMBER",
                            "passport_number": "ID_NUMBER", "drivers_license_number": "ID_NUMBER", "government_id": "ID_NUMBER",
                            "bank_account": "FINANCIAL", "card_number": "FINANCIAL", "iban": "FINANCIAL",
                            "ip_address": "OTHER", "password": "OTHER", "api_key": "OTHER"}},
}
NEWMEAN_SIZES = ("nano", "small", "medium", "large")
VARIANTS = ("q4", "q4e")
DEFAULT_VARIANT = {"large": "q4e", "medium": "q4e", "small": "q4", "nano": "q4"}   # js/src/redactor.ts DEFAULT_VARIANT


def default_variant(model):
    """SDK default weight file of newmean-ai/pii-<size> (q4 for an unknown size)."""
    return DEFAULT_VARIANT.get(model.rsplit("/", 1)[-1].removeprefix("pii-"), "q4")


def variant_of(req):
    """Weight file variant of a newmean-onnx request: its own field, else the tier's SDK default."""
    v = req.get("variant") or default_variant(req["model"])
    assert v in VARIANTS, v
    return v


def ext_spec(key, scheme="v1"):
    """evaluate.ext_spec: v1 sends secret-like labels mapped to OTHER to SECRET and IP labels to ID_NUMBER."""
    spec = dict(EXT[key])
    if scheme == "v1":
        spec["map"] = {k: ("SECRET" if v == "OTHER" and is_secret(k) else
                           "ID_NUMBER" if v == "OTHER" and map_label(re.sub(r"[\s-]+", "_", k)) == "ID_NUMBER" else v) for k, v in spec["map"].items()}
    return spec


def adapter_env(req):
    """Isolated environment a request's adapter runs in."""
    a = req["adapter"]
    return EXT[a[4:]]["env"] if a.startswith("ext:") else "hf"


def download_spec(req):
    """(repo, revision, allow_patterns) to fetch for a request, or None (regex)."""
    a = req["adapter"]
    if a == "regex":
        return None
    if a.startswith("ext:"):
        s = EXT[a[4:]]
        return s["repo"], s["revision"], s["files"]
    if a == "newmean-onnx":
        return req["model"], req["revision"], ["*.json", "*.txt", f"onnx/model_{variant_of(req)}.onnx"]
    # transformers: config, tokenizer and safetensors weights only (no pickles, no custom code)
    return req["model"], req["revision"], ["*.json", "*.txt", "*.model", "*.safetensors", "*.tiktoken", "*.vocab"]


class HFPredictor:
    labels = None   # unknown label set → scored against collapsed gold

    def __init__(self, model_id, device, secret=False):
        from transformers import pipeline
        self.pipe = pipeline("token-classification", model=model_id, aggregation_strategy="simple", device=device,
                             trust_remote_code=False, model_kwargs={"use_safetensors": True})
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


class OnnxTokenPredictor:
    """Runs a transformers.js ONNX token classifier (quantized file as-is) with onnxruntime. Long texts are split using the model card's window/stride,
    and overlapping tokens take the prediction from the window where they sit farther from the edge."""

    def __init__(self, spec):
        import numpy as np
        import onnxruntime as ort
        from transformers import AutoTokenizer
        d = Path(spec["dir"])
        self.np, self.spec = np, spec
        self.labels = sorted(set(spec["map"].values()))
        self.tok = AutoTokenizer.from_pretrained(d)
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("ORT_THREADS", 0))
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
        super().__init__(str(spec["dir"]), device)
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
    """GLiNER (gliner) / GLiNER2 (gliner2) zero-shot span extraction — runs in its own environment (transformers version conflicts)."""

    def __init__(self, spec, device):
        self.spec, self.queries = spec, list(spec["map"])
        self.labels = sorted(set(spec["map"].values()))
        if spec["gliner"] == "gliner":
            from gliner import GLiNER
            self.model = GLiNER.from_pretrained(str(spec["dir"]), load_tokenizer=True).to(device)
        else:
            from gliner2 import GLiNER2
            self.model = GLiNER2.from_pretrained(str(spec["dir"]))
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


class LabelMapHFPredictor(ExtHFPredictor):
    """A submitted transformers model with a user label map; 'ignore' targets are dropped (not masked)."""

    def __init__(self, model_dir, label_map, device):
        spec = {"dir": model_dir, "map": {k: v for k, v in label_map.items() if v != "ignore"}}
        super().__init__(spec, device)


class RegexPredictor:
    labels = None

    def __call__(self, texts):
        return [regex_predict(t) for t in texts]


def newmean_onnx_spec(model_dir, variant="q4"):
    """evaluate.py --model onnx:<dir>:model_<variant>.onnx: window 256, stride 64, the model's own labels (tags.json, else
    config id2label)."""
    d = Path(model_dir)
    tags = (json.loads((d / "tags.json").read_text())["tags"] if (d / "tags.json").exists()
            else list(json.loads((d / "config.json").read_text())["id2label"].values()))
    return {"dir": str(d), "onnx": f"onnx/model_{variant}.onnx", "window": 256, "stride": 64,
            "map": {strip_bio(t): strip_bio(t) for t in tags if t != "O"}}


def build_predictor(req, model_dir, device):
    """Predictor for a request (adapter, label_map) whose files were downloaded to model_dir."""
    a = req["adapter"]
    if a == "regex":
        return RegexPredictor()
    if a == "newmean-onnx":
        return OnnxTokenPredictor(newmean_onnx_spec(model_dir, variant_of(req)))
    if a.startswith("ext:"):
        spec = {**ext_spec(a[4:]), "dir": str(model_dir)}
        return (GlinerPredictor(spec, device) if "gliner" in spec else ExtHFPredictor(spec, device) if spec.get("hf")
                else OnnxTokenPredictor(spec))
    if a == "transformers":
        lm = req.get("label_map") or "auto"
        return HFPredictor(str(model_dir), device, secret=True) if lm == "auto" else LabelMapHFPredictor(str(model_dir), lm, device)
    raise ValueError(f"unknown adapter {a!r}")
