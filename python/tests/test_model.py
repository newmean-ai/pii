# Detection through the Python API with the local nano copy (skips without it).
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from newmean_pii import LEVEL1_LABELS, Redactor, Span, regex_spans

from conftest import FIXTURES, has_model, model_dir, read_jsonl

TEXT = "Call Maria Lopez at +1 202 555 0143 or maria.lopez@example.com, 서울시 강남구 테헤란로 123 김민수 010-1234-5678."


def test_detect_and_redact(nano):
    spans = nano.detect(TEXT)
    assert spans and all(isinstance(s, Span) and s.source == "model" for s in spans)
    assert ("maria.lopez@example.com", "EMAIL") in [(TEXT[s.start : s.end], s.label) for s in spans]
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
    out = nano.redact(TEXT)
    assert out.spans == spans and "[EMAIL]" in out.text and "maria.lopez" not in out.text
    masked = nano.redact(TEXT, "mask").text
    assert len(masked) == len(TEXT) and "*" * len("maria.lopez@example.com") in masked
    blank = nano.redact(TEXT, "blank").text
    assert len(blank) == len(TEXT) - sum(s.end - s.start for s in spans)
    assert nano.redact(TEXT, replace=lambda s, o: s.label.lower()).text.count("email") == 1
    with pytest.raises(ValueError):
        nano.redact(TEXT, "hash")
    with pytest.raises(TypeError):
        nano.detect(TEXT.encode())


def test_empty_and_blank(nano):
    for t in ("", " ", "\n\t  　"):
        assert nano.detect(t) == [] and nano.redact(t).text == t and nano.tokenize(t) == []


def test_offsets_on_emoji_text(nano):
    """Spans and tokens are str indices on text with astral characters, ZWJ sequences, flags and lone surrogates."""
    prefix = "😀👩‍👩‍👧🇰🇷 𝓜𝓪𝓻𝓲𝓪 "
    for text in (prefix + TEXT, TEXT.replace(" ", " 👍🏽 "), "\ud800 " + TEXT + " \udfff", "é" * 3 + " " + TEXT):
        spans = nano.detect(text)
        assert "maria.lopez@example.com" in [text[s.start : s.end] for s in spans], text
        assert all(0 <= s.start < s.end <= len(text) for s in spans)
        prev = 0
        for t in nano.tokenize(text):
            assert prev <= t.start <= t.end <= len(text)
            prev = t.start
    n = len(prefix)  # 14 code points (20 UTF-16 units); TEXT starts a new word, so its tokens only shift
    shifted = [(t.id, t.start - n, t.end - n) for t in nano.tokenize(prefix + TEXT) if t.start >= n]
    assert shifted == [tuple(t) for t in nano.tokenize(TEXT)]


def test_labels_and_sensitivity(nano):
    r = Redactor("nano", "q4", model_dir=model_dir("nano"), labels=["EMAIL"])
    assert r.labels == ["EMAIL"]
    assert [TEXT[s.start : s.end] for s in r.detect(TEXT)] == ["maria.lopez@example.com"]
    assert Redactor("nano", "q4", model_dir=model_dir("nano"), labels=[]).detect(TEXT) == []
    lvl1 = Redactor("nano", "q4", model_dir=model_dir("nano"), labels=LEVEL1_LABELS)
    assert all(not s.label.endswith("_L2") for s in lvl1.detect("Mr. Kim, see linkedin.com/in/minsu-kim"))

    texts = [r["text"] for r in read_jsonl(FIXTURES / "predict-nano-q4.jsonl")[:30]] if (FIXTURES / "predict-nano-q4.jsonl").is_file() else [TEXT]
    more = Redactor("nano", "q4", model_dir=model_dir("nano"), sensitivity=1.0)
    assert more.sensitivity == 1.0
    r.set_sensitivity(1.0)
    base = Redactor("nano", "q4", model_dir=model_dir("nano"))
    base.set_sensitivity(1.0)
    assert [base.detect(t) for t in texts] == [more.detect(t) for t in texts]
    base.set_sensitivity(0)
    assert sum(len(nano.detect(t)) for t in texts) == sum(len(base.detect(t)) for t in texts)


def test_regex_layer_merges_like_npm(nano):
    """regex=True: the C core merges the pattern spans exactly like the npm combine(); checked against a Python port."""
    r = Redactor("nano", "q4", model_dir=model_dir("nano"), regex=True)
    texts = [TEXT, "IBAN 1234-5678-9012-3456, order 2024-10-31-001, see https://example.com/a?b=c"]
    if (FIXTURES / "predict-nano-q4.jsonl").is_file():
        texts += [row["text"] for row in read_jsonl(FIXTURES / "predict-nano-q4.jsonl")[:150]]
    regex_only = 0
    for t in texts:
        want = combine(nano.detect(t), regex_spans(t))
        assert r.detect(t) == want, t[:80]
        regex_only += any(s.source != "model" for s in want)
    assert regex_only > 0


def combine(model, regex):
    """js/src/redactor.ts combine(), all labels enabled."""
    fam = lambda l: l[:-3] if l.endswith("_L2") else l
    groups, end = [], -1
    for s in sorted(model + regex, key=lambda s: (s.start, -s.end)):
        if groups and s.start < end:
            groups[-1].append(s)
            end = max(end, s.end)
        else:
            groups.append([s])
            end = s.end
    out = []
    for g in groups:
        m = sorted((s for s in g if s.source == "model"), key=lambda s: -(s.end - s.start))
        fams = {fam(s.label) for s in m}
        use = m + [s for s in g if s.source == "regex" and s.label not in fams]
        src = {s.source for s in use}
        out.append(Span(min(s.start for s in use), max(s.end for s in use), use[0].label, "model+regex" if len(src) == 2 else src.pop()))
    return out


def test_threads_share_one_model(nano):
    texts = [TEXT, TEXT * 20, "😀 " + TEXT] * 4
    if (FIXTURES / "predict-nano-q4.jsonl").is_file():
        texts += [row["text"] for row in read_jsonl(FIXTURES / "predict-nano-q4.jsonl")[:60]]
    want = [nano.detect(t) for t in texts]
    with ThreadPoolExecutor(8) as pool:
        for _ in range(2):
            assert list(pool.map(nano.detect, texts)) == want


def test_close(nano):
    r = Redactor("nano", "q4", model_dir=model_dir("nano"))
    with r:
        assert r.detect(TEXT)
    with pytest.raises(RuntimeError, match="closed"):
        r.detect(TEXT)
    info = nano.info()
    assert info["tags"] == 27 and info["max_tokens"] >= 256 and "build" in info


def test_cli(nano):
    """UTF-8 in and out and CRLF kept, also where the locale's encoding is not UTF-8 (PYTHONIOENCODING=ascii)."""
    text = TEXT + "\r\n" + TEXT + "\n"
    p = subprocess.run(
        [sys.executable, "-m", "newmean_pii", "redact", "--size", "nano", "--model-dir", str(model_dir("nano"))],
        input=text.encode(), capture_output=True, check=True, env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )
    assert p.stdout.decode() == nano.redact(text).text
