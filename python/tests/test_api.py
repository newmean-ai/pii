import json
import re

import pytest

import newmean_pii
from newmean_pii import (
    DEFAULT_SIZE,
    DEFAULT_VARIANT,
    LABELS,
    LEVEL1_LABELS,
    MODEL_VERSION,
    TAGS,
    Redactor,
    Span,
    apply_redaction,
    model_file,
)
from newmean_pii import _core

from conftest import REPO


def test_constants_match_the_npm_package():
    assert DEFAULT_SIZE == "medium"
    assert DEFAULT_VARIANT == {"nano": "q4", "small": "q4", "medium": "q4e", "large": "q4e"}
    assert LABELS == ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"]
    assert LEVEL1_LABELS == [l for l in LABELS if l not in ("PERSON_L2", "URL_L2")]
    assert len(TAGS) == 27 and TAGS[:3] == ["O", "B-PERSON", "I-PERSON"]
    assert MODEL_VERSION == "1.0.0"
    assert _core.version() == "1.0.0"
    assert re.fullmatch(r"(neon|sse|vector|scalar)(\+fma)?", _core.build_info())


def test_pins_match_js_models_ts():
    ts = REPO / "js" / "src" / "models.ts"
    if not ts.is_file():
        pytest.skip("js/src/models.ts not found (not a repository checkout)")
    src = ts.read_text()
    assert re.search(r'MODEL_VERSION = "([^"]+)"', src).group(1) == MODEL_VERSION
    files = json.loads(re.search(r"MODEL_FILES[^=]*= (\{.*\});", src, re.S).group(1))
    for size, entry in files.items():
        for f, info in entry.items():
            pin = model_file(size, f)
            assert (pin.bytes, pin.sha256) == (info["bytes"], info["sha256"])
            assert pin.path == {"q4": "onnx/model_q4.onnx", "q4e": "onnx/model_q4e.onnx", "tokenizer": "tokenizer.web.json"}[f]
    assert len(files) == 4


def test_invalid_options_fail_before_loading(tmp_path):
    with pytest.raises(ValueError, match="unknown size"):
        Redactor("huge", cache_dir=tmp_path)
    with pytest.raises(ValueError, match="unknown variant"):
        Redactor("nano", "fp32", cache_dir=tmp_path)
    with pytest.raises(ValueError, match="unknown label"):
        Redactor("nano", labels=["EMAIL", "PASSPORT"], cache_dir=tmp_path)
    with pytest.raises(ValueError):
        model_file("nano", "q8")
    assert list(tmp_path.iterdir()) == []


def test_model_rejects_bad_bytes():
    with pytest.raises(ValueError, match="cannot load the model"):
        _core.Model(b"not a model", b"{}")
    with pytest.raises(TypeError):
        _core.Model(bytearray(b"x"), b"{}")


# applyRedaction cases of js/test/combine.test.ts
TEXT = "Call Kim at 010-1234-5678."
SPANS = [Span(5, 8, "PERSON_L2", "model"), Span(12, 25, "PHONE", "model")]


def test_redaction_modes():
    assert apply_redaction(TEXT, SPANS) == "Call [PERSON] at [PHONE]."
    assert apply_redaction(TEXT, SPANS, mode="mask") == "Call *** at *************."
    assert apply_redaction(TEXT, SPANS, mode="blank") == "Call  at ."
    assert apply_redaction(TEXT, SPANS, replace=lambda s, o: f"<{s.label}:{len(o)}>") == "Call <PERSON_L2:3> at <PHONE:13>."
    assert apply_redaction(TEXT, SPANS, mode="mask", mask_char="#") == "Call ### at #############."
    assert apply_redaction(TEXT, []) == TEXT
    with pytest.raises(ValueError):
        apply_redaction(TEXT, SPANS, mode="hash")


def test_mask_counts_code_points():
    text = "hi 👩‍👩‍👧 x"  # the family emoji is 5 code points (7 UTF-16 units)
    assert apply_redaction(text, [Span(3, 8, "PERSON", "model")], mode="mask") == "hi ***** x"
    assert apply_redaction("😀 [URL_L2]", [Span(0, 1, "URL_L2", "model")]) == "[URL] [URL_L2]"


def test_cache_dir_resolution(monkeypatch, tmp_path):
    from newmean_pii import default_cache_dir

    monkeypatch.delenv("NEWMEAN_PII_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_dir() == tmp_path / "xdg" / "newmean-pii"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/ignored")  # XDG: relative paths are invalid
    assert default_cache_dir().name == "newmean-pii" and default_cache_dir().parent.name == ".cache"
    monkeypatch.setenv("NEWMEAN_PII_CACHE", str(tmp_path / "own"))
    assert default_cache_dir() == tmp_path / "own"


def test_public_api():
    for name in newmean_pii.__all__:
        assert hasattr(newmean_pii, name), name
