"""Submission checks. The static Space runs the cheap ones in the browser (lb.js checkModel, same rules); the weekly job
runs all of them again on every submission before it enters the queue (validate_submission)."""
import json

from . import config
from .adapters import VARIANTS, default_variant
from .scoring import strip_bio
from .store import iso, now, recent_by, request_id, result_key

USER_ADAPTERS = ("transformers", "newmean-onnx")


class Rejected(ValueError):
    pass


def parse_label_map(text):
    text = (text or "").strip()
    if text in ("", "auto"):
        return "auto"
    try:
        m = json.loads(text)
    except json.JSONDecodeError as e:
        raise Rejected(f"label map is not valid JSON: {e}")
    if not isinstance(m, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
        raise Rejected('label map must be a JSON object {"model label": "OUR_LABEL" | "OTHER" | "ignore"}')
    bad = sorted({v for v in m.values() if v not in config.MAP_TARGETS})
    if bad:
        raise Rejected(f"unknown target labels {bad}; allowed: {config.MAP_TARGETS}")
    if all(v == "ignore" for v in m.values()):
        raise Rejected("the label map ignores every label")
    return m


def model_labels(cfg):
    """Entity labels of a token-classification config (BIO prefixes stripped, O dropped)."""
    return sorted({strip_bio(v) for v in (cfg.get("id2label") or {}).values()} - {"O"})


def check_model(api, model, revision, adapter, label_map, download_json, variant=None):
    """Hub checks; returns the resolved commit sha. api: HfApi-like; download_json(repo, filename, revision) -> dict."""
    if adapter not in USER_ADAPTERS:
        raise Rejected(f"adapter must be one of {list(USER_ADAPTERS)}; other formats are added by the maintainers (open a discussion)")
    if adapter == "newmean-onnx" and not model.startswith(f"{config.ORG}/pii-"):
        raise Rejected("the newmean-onnx adapter is only for newmean-ai/pii-<size> models")
    try:
        info = api.model_info(model, revision=revision or None, files_metadata=True)
    except Exception as e:   # not found, private, or bad revision
        raise Rejected(f"cannot read {model}@{revision or 'main'} on the Hub ({type(e).__name__})")
    if getattr(info, "private", False) or getattr(info, "gated", False):
        raise Rejected("the model must be public and not gated")
    files = {s.rfilename: (s.size or 0) for s in (info.siblings or [])}
    if adapter == "newmean-onnx":
        if f"onnx/model_{variant}.onnx" not in files:
            raise Rejected(f"onnx/model_{variant}.onnx not found")
        return info.sha
    if "config.json" not in files:
        raise Rejected("config.json not found")
    weights = [f for f in files if f.endswith(".safetensors")]
    if not weights:
        raise Rejected("safetensors weights are required (pickled .bin weights are not loaded)")
    gb = sum(files[f] for f in weights) / 1e9
    if gb > config.MAX_MODEL_GB:
        raise Rejected(f"weights are {gb:.1f} GB; the limit is {config.MAX_MODEL_GB:g} GB")
    cfg = download_json(model, "config.json", info.sha)
    if cfg.get("auto_map"):
        raise Rejected("models that need trust_remote_code are not run")
    if not any(a.endswith("ForTokenClassification") for a in cfg.get("architectures") or []):
        raise Rejected("the model must be a transformers token-classification model (…ForTokenClassification)")
    if isinstance(label_map, dict):
        labels = model_labels(cfg)
        missing, extra = sorted(set(labels) - set(label_map)), sorted(set(label_map) - set(labels))
        if missing or extra:
            raise Rejected(f"label map must cover exactly the model labels {labels}; missing {missing}, unknown {extra}")
    return info.sha


def validate_submission(requests, result_keys, api, download_json, submitter, fields, t, req_id=None):
    """A queued request (status pending) for a submission, or Rejected with a user-facing reason.
    requests: the ledger so far (weekly limit, queue cap, duplicates); result_keys: keys already on the leaderboard;
    fields: {model, revision, adapter, label_map (JSON text, dict or "auto"), variant}."""
    model = (fields.get("model") or "").strip()
    adapter = fields.get("adapter") or "transformers"
    if not submitter:
        raise Rejected("sign in with Hugging Face to submit")
    if not model or model.count("/") != 1:
        raise Rejected("model must be a Hub id like org/name")
    lm = fields.get("label_map")
    label_map = parse_label_map(lm if isinstance(lm, str) or lm is None else json.dumps(lm)) if adapter == "transformers" else None
    variant = (fields.get("variant") or default_variant(model)) if adapter == "newmean-onnx" else None   # SDK default of the tier
    if variant is not None and variant not in VARIANTS:
        raise Rejected(f"variant must be one of {list(VARIANTS)}")
    if len(recent_by(requests, submitter, t)) >= config.MAX_PER_WEEK:
        raise Rejected(f"limit reached: {config.MAX_PER_WEEK} submissions per account per 7 days")
    if sum(r["status"] in ("pending", "running") for r in requests) >= config.MAX_QUEUE:
        raise Rejected("the queue is full this week; please try again after the next scoring run")
    sha = check_model(api, model, fields.get("revision") or "main", adapter, label_map, download_json, variant)
    for r in requests:
        if (r["model"], r["revision"], r["adapter"], r.get("variant")) == (model, sha, adapter, variant) and r["status"] in ("pending", "running", "done"):
            raise Rejected(f"{model}@{sha[:12]} is already {r['status']}")
    if result_key(model, sha, adapter, variant) in result_keys:
        raise Rejected(f"{model}@{sha[:12]} is already on the leaderboard")
    return {"id": req_id or request_id(submitter, model, sha, adapter, t, variant), "model": model, "revision": sha,
            "adapter": adapter, "variant": variant, "label_map": label_map, "submitter": submitter, "submitted_at": iso(t),
            "status": "pending", "error": None, "updated_at": iso(t)}


def make_request(store, api, download_json, submitter, model, revision, adapter, label_map_text, t=None, variant=None):
    """Validates a submission and writes it to the ledger directly (maintainers, tests). Raises Rejected."""
    t = t or now()
    req = validate_submission(store.requests(), {r["key"] for r in store.results()}, api, download_json, submitter,
                              {"model": model, "revision": revision, "adapter": adapter, "label_map": label_map_text,
                               "variant": variant}, t)
    store.put_request(req, f"Submit {req['model']}@{req['revision'][:12]} ({submitter})")
    return req
