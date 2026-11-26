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


def legacy_regex_spans(text, rules=None):
    """The matcher of 1.6.0 and earlier, kept as the reference: regex_spans must give its spans, in its order. It tried
    the EMAIL pattern again from every character of a run without "@" and compared each match with all earlier spans, so
    its time grew with the square of the input (fixed in 1.6.1)."""
    from newmean_pii import REGEX_RULES, Span

    out = []
    for label, pattern in REGEX_RULES if rules is None else rules:
        for m in pattern.finditer(text):
            start, end = m.span()
            if any(start < s.end and s.start < end for s in out):
                continue
            out.append(Span(start, end, label, "regex"))
    return out


def test_equals_the_legacy_matcher_exhaustively():
    alphabet = "a@.-+ "
    bad = []
    checked = 0
    texts = [""]
    for _ in range(7):
        checked += len(texts)
        bad += [t for t in texts if regex_spans(t) != legacy_regex_spans(t)]
        texts = [t + c for t in texts for c in alphabet]
    checked += len(texts)
    bad += [t for t in texts if regex_spans(t) != legacy_regex_spans(t)]
    assert bad[:5] == []
    assert checked == 335923


def test_equals_the_legacy_matcher_on_random_unicode():
    import random

    alphabet = [
        "a", "Z", "7", "@", "@", ".", ".", "+", "-", "_", " ", "\n", " ", "한", "é", "𐐀", "𝟙", "①", "́", "😀", "\ud800", "\udc00",
        ":", "/", "(", ")", "w", "0", "1", "http://", "www.", "\x1c", "\x85", "﻿",
    ]
    rng = random.Random(20261117)
    bad = []
    for _ in range(40000):
        t = "".join(rng.choice(alphabet) for _ in range(rng.randrange(60)))
        if regex_spans(t) != legacy_regex_spans(t):
            bad.append(t)
    assert bad[:5] == []


@pytest.mark.parametrize(
    "text",
    [
        "a@b.co@d.e", "a@b.co-+x@c.de", "a@b..co", "a@@b.co", "a@b.co.", ".a+@b.co", "a@b", "@b.co", "a@.co", "a@b.c@d.e.f",
        "😀 김+𐐀@회사.한국 𝟙@𐐀.한", "é@a.co ①@b.co a@b.①", "a\ud800@b.co a@b.\udc00",
        "www.site.test/x@y.co 010-1234-5678 https://other.test", "a@b.co http://one.test a@b.co",
        "900101-1234567 4111 1111 1111 1111 +1 (212) 555-1234", "https://a@b.co/x?mail=c@d.co",
    ],
)
def test_equals_the_legacy_matcher_on_edge_cases(text):
    assert regex_spans(text) == legacy_regex_spans(text)


def test_equals_the_legacy_matcher_with_changed_rules(monkeypatch):
    import newmean_pii._regex as mod

    builtin = list(mod.REGEX_RULES)
    sets = [
        [("EMAIL", re.compile("CUSTOM"))],
        [builtin[1], ("CONTACT", builtin[0][1])],
        [("EMPTY", re.compile("(?=b)")), ("REPLACED", re.compile("ab")), ("TAIL", re.compile("b|c"))],
        [("A", re.compile("a*")), ("B", re.compile("b*")), ("ALL", re.compile("."))],
        [("POINT", re.compile("(?=a)")), ("ALL", re.compile(".")), ("A", re.compile("a"))],
        builtin + [("WORD", re.compile("[a-z]+"))],
    ]
    for rules in sets:
        monkeypatch.setattr(mod, "REGEX_RULES", rules)
        for text in ["", "a", "b", "ab", "abc", "baba", "😀ab😀a", "CUSTOM a@b.co", "https://a@b.co x@y.co"]:
            assert regex_spans(text) == legacy_regex_spans(text, rules), (rules, text)


@pytest.mark.timing
def test_long_inputs_take_time_in_proportion_to_their_length():
    """1.6.0 needed about 20 s for the first text on the machine that takes a few ms here."""
    import time

    t0 = time.perf_counter()
    for text in ["a" * 100000, "a@" + "b" * 100000, "@" * 100000, "a." * 50000, "1" * 100000]:
        assert [s for s in regex_spans(text) if s.label == "EMAIL"] == []
    text = "a@b.co " * 40000
    spans = regex_spans(text)
    assert len(spans) == 40000
    assert spans[0] == (0, 6, "EMAIL", "regex") and spans[-1] == (279993, 279999, "EMAIL", "regex")
    assert time.perf_counter() - t0 < 5
