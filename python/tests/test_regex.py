import re

import pytest

from newmean_pii import regex_spans

from conftest import FIXTURES, read_jsonl


def test_examples():
    # js/test/regex.test.ts: the account rule (FINANCIAL) runs before PHONE and also takes 010-1234-5678
    t = "메일 kim.minjun@example.co.kr 전화 010-1234-5678 카드 4111 1111 1111 1111 주민 900101-1234567"
    assert [(t[s.start : s.end], s.label) for s in regex_spans(t)] == [
        ("kim.minjun@example.co.kr", "EMAIL"),
        ("900101-1234567", "ID_NUMBER"),
        ("010-1234-5678", "FINANCIAL"),
        ("4111 1111 1111 1111", "FINANCIAL"),
    ]
    assert all(s.source == "regex" for s in regex_spans(t))


def test_offsets_are_code_points():
    t = "👩‍👩‍👧🇰🇷 mail 𝓜@example.com or www.example.com/😀 now"
    got = [(t[s.start : s.end], s.label) for s in regex_spans(t)]
    assert got == [("𝓜@example.com", "EMAIL"), ("www.example.com/😀", "URL")]


def test_parity_with_python_baseline():
    fix = FIXTURES / "regex.jsonl"
    if not fix.is_file():
        pytest.skip("regex.jsonl absent")
    rows = read_jsonl(fix)
    key = lambda spans: sorted((s["start"], s["end"], s["label"]) for s in spans)
    bad = [r["text"][:80] for r in rows if key(r["spans"]) != sorted((s.start, s.end, s.label) for s in regex_spans(r["text"]))]
    assert not bad, f"{len(rows) - len(bad)}/{len(rows)} rows identical; first: {bad[:3]}"
    assert len(rows) > 3000


def test_whitespace_is_javascripts():
    """Whitespace in the patterns is ECMAScript \\s (Zs, TAB, VT, FF, LF, CR, U+2028, U+2029, U+FEFF), as in the npm
    package, not Python's \\s (which adds U+001C..U+001F and U+0085 and leaves out U+FEFF)."""
    import unicodedata

    from newmean_pii._regex import _WS

    js = {c for c in map(chr, range(0x110000)) if unicodedata.category(c) == "Zs"} | set("\t\n\x0b\x0c\r  ﻿")
    assert {c for c in map(chr, range(0x110000)) if re.fullmatch(f"[{_WS}]", c)} == js

    # outputs of the npm package's regexSpans on the same texts
    for c in ("\x1c", "\x85"):
        t = f"see https://example.com/a{c}b and 010{c}1234{c}5678 id 900101{c}1234567"
        assert [(t[s.start : s.end], s.label) for s in regex_spans(t)] == [(f"https://example.com/a{c}b", "URL")]
    t = "see https://example.com/a﻿b and 010﻿1234﻿5678 id 900101﻿1234567"
    assert [(t[s.start : s.end], s.label) for s in regex_spans(t)] == [
        ("https://example.com/a", "URL"),
        ("900101﻿1234567", "ID_NUMBER"),
        ("010﻿1234﻿5678", "PHONE"),
    ]
