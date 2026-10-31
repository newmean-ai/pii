# Fixtures and model copies are private (validation text) and read in place, never copied: PII_FIXTURES (default
# js/test/fixtures of the repository) and PII_MODELS (default js/test/models, the layout of the Hugging Face repos:
# pii-<size>/tokenizer.web.json, pii-<size>/onnx/model_<variant>.onnx). Tests that need them skip when absent.
# PII_SIZES=nano,medium limits the sizes of the prediction parity tests.
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(os.environ.get("PII_FIXTURES", REPO / "js" / "test" / "fixtures"))
MODELS = Path(os.environ.get("PII_MODELS", REPO / "js" / "test" / "models"))
SIZES = os.environ["PII_SIZES"].split(",") if os.environ.get("PII_SIZES") else None


def read_jsonl(path):
    # not splitlines(): texts hold U+2028 and other line separators
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]


def model_dir(size):
    return MODELS / f"pii-{size}"


def has_model(size, variant):
    d = model_dir(size)
    return (d / "tokenizer.web.json").is_file() and (d / "onnx" / f"model_{variant}.onnx").is_file()


@pytest.fixture(scope="session")
def nano():
    """The nano q4 model from the local copies (skips without them)."""
    if not has_model("nano", "q4"):
        pytest.skip(f"no model copy in {MODELS}")
    from newmean_pii import Redactor

    return Redactor("nano", "q4", model_dir=model_dir("nano"))
