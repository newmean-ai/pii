"""Seed results from the v1.0 report's Colab L4 runs (2026-10-30): public test numbers from the stored evaluate.py reports
(eval_l4_<tag>.json, key r13_test_final = the public test set, same rows and order as newmean-ai/pii-multilingual-test),
private test "pending" (the scoring job backfills it on its next run; its first run, 2026-10-31, scored all 13 seeded rows).

  python scripts/seed_results.py --reports <dir with eval_<tag>.json> --out seed/  [--dataset-revision <sha>]
Maintainer-only: the stored L4 reports are not public.
Writes <out>/results/<key>.json (LocalStore layout); uploading them to the results dataset is a separate, approved step.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pii_leaderboard.adapters import EXT, default_variant  # noqa: E402
from pii_leaderboard.evaluation import public_summary  # noqa: E402
from pii_leaderboard.store import LocalStore, result_key  # noqa: E402

NEWMEAN_REVISION = "v1.0.0"   # release tag of newmean-ai/pii-<size> (js/src/models.ts MODEL_VERSION)
# tag of the L4 report → (model, revision, adapter, display name, size as in the dataset card). Our models: the SDK default
# weight file of each tier (adapters.DEFAULT_VARIANT: q4e for large and medium, q4 for small and nano), what users get.
SEEDS = {
    "l4_q4e-s-large-v1": ("newmean-ai/pii-large", NEWMEAN_REVISION, "newmean-onnx", "newmean pii-large", "15.5 MB"),
    "l4_q4e-s-medium-v1": ("newmean-ai/pii-medium", NEWMEAN_REVISION, "newmean-onnx", "newmean pii-medium", "11.3 MB"),
    "l4_q4-s-small-v1": ("newmean-ai/pii-small", NEWMEAN_REVISION, "newmean-onnx", "newmean pii-small", "7.1 MB"),
    "l4_q4-s-nano-v1": ("newmean-ai/pii-nano", NEWMEAN_REVISION, "newmean-onnx", "newmean pii-nano", "2.6 MB"),
    "l4_h2h_gliner-edge": (EXT["gliner-edge"]["repo"], EXT["gliner-edge"]["revision"], "ext:gliner-edge", None, "176 MB"),
    "l4_h2h_openai-pf": (EXT["openai-pf"]["repo"], EXT["openai-pf"]["revision"], "ext:openai-pf", None, "2.8 GB"),
    "l4_h2h_pii-redactor-small": ("Horizon-Labs/pii-redactor-small", "3f6a99b9906378f3faed3e6eeb3b396b0b8257c2", "transformers", None, "563 MB"),
    "l4_h2h_koelectra-ko-pii-ner": ("atonlee/koelectra-ko-pii-ner", "1e75c01e707232401883cf364151bbe2e560c708", "transformers", None, "56 MB"),
    "l4_h2h_gliner2-pii": (EXT["gliner2-pii"]["repo"], EXT["gliner2-pii"]["revision"], "ext:gliner2-pii", None, "1.2 GB"),
    "l4_h2h_rampart": (EXT["rampart"]["repo"], EXT["rampart"]["revision"], "ext:rampart", None, "15 MB"),
    "l4_h2h_mozilla": (EXT["mozilla"]["repo"], EXT["mozilla"]["revision"], "ext:mozilla", None, "15 MB"),
    "l4_h2h_rampart-edge": (EXT["rampart-edge"]["repo"], EXT["rampart-edge"]["revision"], "ext:rampart-edge", None, "1.5 MB"),
    "l4_h2h_regex": ("regex", "", "regex", "regex baseline", "—"),
}
SET = "r13_test_final"


def seed(reports, dataset_revision):
    out = []
    for tag, (model, rev, adapter, name, size) in SEEDS.items():
        rep = json.loads((Path(reports) / f"eval_{tag}.json").read_text())
        assert rep["scheme"] == "v1", tag
        r = rep[SET]
        r = {**r, "gold_collapsed": rep["gold_collapsed"]}
        variant = default_variant(model) if adapter == "newmean-onnx" else None
        assert variant is None or f"model_{variant}.onnx" in rep["model"], (tag, rep["model"])   # the L4 run scored that file
        out.append({"key": result_key(model, rev, adapter, variant), "model": model, "revision": rev, "adapter": adapter,
                    "variant": variant,
                    "label_map": "auto" if adapter == "transformers" else None, "display_name": name or model,
                    "size": size, "submitter": "newmean-ai", "source": "seed-l4",
                    "hardware": "Colab L4 VM (report v1.0, 2026-10-30)", "evaluated_at": "2026-10-30T00:00:00Z",
                    "public": {"status": "done", "dataset_revision": dataset_revision, **public_summary(r),
                               "ms_per_row": r.get("ms_per_row"), "rows": 17058},
                    "private": {"status": "pending"}})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dataset-revision", default="")
    a = ap.parse_args()
    store = LocalStore(a.out)
    for res in seed(a.reports, a.dataset_revision):
        store.put_result(res)
        o = res["public"]["overall"]
        print(f"{res['display_name']:45} leak {o['leak']:.4f} over {o['overmask']:.4f} exact_f1 {o['exact_f1']:.3f}")


if __name__ == "__main__":
    main()
