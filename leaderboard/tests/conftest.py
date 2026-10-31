"""Shared test data.

- The public test set: PII_LB_PUBLIC_FILE (a local jsonl), else downloaded from the Hub (newmean-ai/pii-dataset).
- The newmean models: PII_LB_MODEL_ROOT (a dir with newmean-ai/pii-<size>/ folders, as the job reads it), else downloaded
  from the Hub at the release tag.
- PII_RESEARCH_DIR: the maintainers' stored outputs of the 2026-10-30 L4 runs (eval_<tag>.json and
  preds/<tag>/pmt_v11.jsonl). Not public; tests that need it are skipped when it is unset.
Tests that need data which is not available are skipped.
"""
import os, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NEWMEAN_REVISION = "v1.0.0"   # release tag of newmean-ai/pii-<size> (scripts/seed_results.py)


@pytest.fixture(scope="session")
def research_dir():
    d = os.environ.get("PII_RESEARCH_DIR")
    if not d:
        pytest.skip("PII_RESEARCH_DIR not set (maintainer-only: stored L4 run outputs)")
    if not Path(d).is_dir():
        pytest.skip(f"PII_RESEARCH_DIR is not a directory: {d}")
    return Path(d)


@pytest.fixture(scope="session")
def public_file():
    if os.environ.get("PII_LB_PUBLIC_FILE"):
        p = Path(os.environ["PII_LB_PUBLIC_FILE"])
        if not p.exists():
            pytest.skip(f"PII_LB_PUBLIC_FILE not found: {p}")
        return p
    try:
        from huggingface_hub import hf_hub_download
        from pii_leaderboard import config
        return Path(hf_hub_download(config.PUBLIC_TEST, config.TEST_FILE, repo_type="dataset"))
    except Exception as e:   # no network, no huggingface_hub, or the dataset is not readable
        pytest.skip(f"public test file not available ({type(e).__name__})")


@pytest.fixture(scope="session")
def newmean_model():
    """newmean_model(size, patterns=None) -> local dir of newmean-ai/pii-<size>, or None if not available."""
    def get(size, patterns=None):
        repo = f"newmean-ai/pii-{size}"
        root = os.environ.get("PII_LB_MODEL_ROOT")
        if root:
            d = Path(root) / repo
            return d if d.exists() else None
        try:
            from huggingface_hub import snapshot_download
            from pii_leaderboard.adapters import download_spec
            if patterns is None:
                patterns = download_spec({"adapter": "newmean-onnx", "model": repo, "revision": NEWMEAN_REVISION})[2]
            return Path(snapshot_download(repo, revision=NEWMEAN_REVISION, allow_patterns=patterns))
        except Exception:
            return None
    return get
