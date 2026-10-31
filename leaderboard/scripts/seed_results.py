"""Seed results from the report's Colab L4 runs: public test numbers from the stored evaluate.py reports (eval_l4_<tag>.json,
key pmt_v11 = public test 1.1 (2026-10-31), same rows and order as newmean-ai/pii-dataset), private test "pending" (the scoring
job backfills it on its next run; its first run, 2026-10-31, scored all 13 seeded rows).

  python scripts/seed_results.py --reports <dir with eval_<tag>.json> --out seed/  [--dataset-revision <sha>]
  python scripts/seed_results.py --reports <dir> --refresh [--dataset-revision <sha>]
--refresh rewrites the public block and display name of the seeded rows already in the results dataset (private scores kept)
and republishes leaderboard.json. Maintainer-only: the stored L4 reports are not public; --refresh writes to the Hub.
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
    "l4_q4e-s-large-v1": ("newmean-ai/pii-large", NEWMEAN_REVISION, "newmean-onnx", "newmean-pii-large", "15.5 MB"),
    "l4_q4e-s-medium-v1": ("newmean-ai/pii-medium", NEWMEAN_REVISION, "newmean-onnx", "newmean-pii-medium", "11.3 MB"),
    "l4_q4-s-small-v1": ("newmean-ai/pii-small", NEWMEAN_REVISION, "newmean-onnx", "newmean-pii-small", "7.1 MB"),
    "l4_q4-s-nano-v1": ("newmean-ai/pii-nano", NEWMEAN_REVISION, "newmean-onnx", "newmean-pii-nano", "2.6 MB"),
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
SET = "pmt_v11"   # public test 1.1; v1.0 was r13_test_final


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
                    "hardware": "Colab L4 VM (public test 1.1, 2026-10-31)", "evaluated_at": "2026-10-31T00:00:00Z",
                    "public": {"status": "done", "dataset_revision": dataset_revision, **public_summary(r),
                               "ms_per_row": r.get("ms_per_row"), "rows": 17058},
                    "private": {"status": "pending"}})
    return out


def refresh(reports, dataset_revision):
    from pii_leaderboard.publish import leaderboard
    from pii_leaderboard.store import HubStore
    store = HubStore()
    have = {r["key"]: r for r in store.results()}
    for res in seed(reports, dataset_revision):
        old = have.get(res["key"])
        new = {**old, **{k: res[k] for k in ("display_name", "hardware", "evaluated_at", "public")}} if old else res
        store.put_result(new, f"Public test 1.1: {new['display_name']}")
        have[new["key"]] = new
        print(f"{new['display_name']:45} leak {new['public']['overall']['leak']:.4f} private {new['private']['status']}")
    store.put_index("results", "leaderboard.json", leaderboard(list(have.values())), "Update leaderboard.json (public test 1.1)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", required=True)
    ap.add_argument("--out")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dataset-revision", default="")
    a = ap.parse_args()
    if a.refresh:
        return refresh(a.reports, a.dataset_revision)
    store = LocalStore(a.out)
    for res in seed(a.reports, a.dataset_revision):
        store.put_result(res)
        o = res["public"]["overall"]
        print(f"{res['display_name']:45} leak {o['leak']:.4f} over {o['overmask']:.4f} exact_f1 {o['exact_f1']:.3f}")


if __name__ == "__main__":
    main()
