"""Maintainers: queue a request directly (adapters we add ourselves, re-scoring, test runs). Uses the Hub login of the machine.

  python scripts/admin_request.py --model atonlee/koelectra-ko-pii-ner --adapter transformers --label-map auto [--force]
  python scripts/admin_request.py --adapter ext:gliner2-pii          # model and revision from adapters.EXT
--force queues it even if a result for the same model, revision and adapter exists (the job then overwrites that result).
The weekly limit does not apply; the Hub checks of the submission form do (except for ext: adapters, which are pinned by us).
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pii_leaderboard.adapters import EXT, default_variant  # noqa: E402
from pii_leaderboard.store import HubStore, iso, now, request_id, result_key  # noqa: E402
from pii_leaderboard.submit import check_model, parse_label_map  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--revision", default="main")
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--label-map", default="auto")
    ap.add_argument("--variant")
    ap.add_argument("--submitter", default="newmean-ai")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    from huggingface_hub import HfApi, hf_hub_download
    store, api = HubStore(), HfApi()
    label_map, variant = None, None
    if a.adapter.startswith("ext:"):
        model, sha = EXT[a.adapter[4:]]["repo"], EXT[a.adapter[4:]]["revision"]
    else:
        model = a.model
        label_map = parse_label_map(a.label_map) if a.adapter == "transformers" else None
        variant = (a.variant or default_variant(model)) if a.adapter == "newmean-onnx" else None
        dl = lambda repo, f, rev: json.loads(Path(hf_hub_download(repo, f, revision=rev)).read_text())  # noqa: E731
        sha = check_model(api, model, a.revision, a.adapter, label_map, dl, variant)
    key = result_key(model, sha, a.adapter, variant)
    if not a.force and key in {r["key"] for r in store.results()}:
        raise SystemExit(f"{key} is already on the leaderboard (use --force to re-score)")
    t = now()
    req = {"id": request_id(a.submitter, model, sha, a.adapter, t, variant), "model": model, "revision": sha,
           "adapter": a.adapter, "variant": variant, "label_map": label_map, "submitter": a.submitter, "submitted_at": iso(t),
           "status": "pending", "error": None, "updated_at": iso(t)}
    store.put_request(req, f"Maintainer request {model}@{sha[:12]} ({a.adapter})")
    print("queued", req["id"], key)


if __name__ == "__main__":
    main()
