"""A vault: the local map from numbered placeholders back to the values they replace, so that masking can be undone in
the model's answer and in tool-call arguments. It is an ordinary in-memory object; nothing in it is sent anywhere.

    vault = Vault()                                  # one per user or conversation, never shared between users
    masked = redactor.redact(text, vault=vault).text # "Call [PERSON_1] on [PHONE_1]"
    vault.restore(answer)                            # the placeholders in the answer become the values again

The same value always gets the same placeholder within a vault, so a masked conversation stays the same from turn to turn,
and a value seen once is masked again wherever it reappears in later text, also where the model would miss it on its own
(spans with source "vault"). Same behaviour as the npm package's createVault(): word edges and scripts are decided with
the same explicit code point ranges, and every package runs the cases in python/tests/vault_cases.json (word characters
still come from each runtime's Unicode data).

A known value is found as it is written and also: with full-width ASCII (U+FF01 to U+FF5E) and U+3000 read as ASCII;
in NFC (Hangul written as conjoining jamo, a decomposed accent); in capitals; and, for an e-mail address, a user name, a
URL's scheme and host and a value of two or more words, in any ASCII case. A one-word value in another case is another
word ("Will" and "will"). The hit carries the known value, so one value keeps one placeholder however it is written.
"""
from __future__ import annotations

import bisect
import json
import re
import threading
import unicodedata
from array import array
from typing import AbstractSet, Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple, Union

#: Placeholders to restore: a set (current values) or a mapping tag -> value (a snapshot from Vault.bindings).
Only = Optional[Union[AbstractSet[str], Mapping[str, str]]]

__all__ = ["Vault", "VaultEntry", "VaultHit", "VaultRestorer", "apply_vault", "escape_tags", "vault_tags"]

# Placeholder grammar, the same in every package: "[" + family + "_" + number + "]", where the family is an ASCII capital
# letter followed by capitals, digits and underscores (32 characters at most) and the number is 1 to 12 ASCII digits.
# Longer look-alikes are ordinary text: they are neither escaped nor restored.
_TAG = re.compile(r"\[([A-Z][A-Z0-9_]{0,31}_[0-9]{1,12})\]")
_ENTRY_TAG = re.compile(r"\[([A-Z][A-Z0-9_]*)_([0-9]+)\]")
_TAG_PREFIX = re.compile(r"\[[A-Z0-9_]*\Z")
# Longest placeholder: "[" + a 32-character family + "_" + up to 12 digits + "]". A streamed restore holds back this much.
_MAX_FAMILY = 32
_MAX_TAG = _MAX_FAMILY + 15
_MAX_NUMBER = 999_999_999_999
# How far above the numbers in use a made-up placeholder number is still skipped.
_RESERVE_AHEAD = 1_000_000


#: How many families are tried after the first: at most 31 steps to a letter and underscores, then the 25 other letters.
_FAMILY_STEPS = 56


def _next_family(family: str) -> str:
    """The family that goes on when the numbers of family are used up (which takes an imported or made-up placeholder
    with the highest number): one more "_" at its end, "PERSON" -> "PERSON_", so that its placeholders stay apart
    ("[PERSON__1]"). A family of full length gets its last other character turned into "_" instead, and one that is a
    letter and underscores only goes on with the next letter (after Z, with A)."""
    if len(family) < _MAX_FAMILY:
        return family + "_"
    for i in range(len(family) - 1, 0, -1):
        if family[i] != "_":
            return family[:i] + "_" + family[i + 1 :]
    return (chr(ord(family[0]) + 1) if family[0] < "Z" else "A") + family[1:]


_MISSING: Any = object()


def _copy_record(v: Any) -> Any:
    """A JSON-like copy of a replay record (tuples become lists, as after to_json and back)."""
    if isinstance(v, dict):
        return {k: _copy_record(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_copy_record(x) for x in v]
    return v
# U+FF3B FULLWIDTH LEFT SQUARE BRACKET: what escape_tags puts in front of placeholder-like text that was already there.
_ESCAPED_BRACKET = "\uff3b"
# Scripts written without spaces between words (letters and marks of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese,
# Javanese, Han, Hiragana and Katakana): the same code point ranges in every package (js/src/vault.ts).
_NO_SPACE = (
    (0x0E01, 0x0E3A), (0x0E40, 0x0E4E),  # Thai
    (0x0E81, 0x0ECF),  # Lao
    (0x1000, 0x103F), (0x1050, 0x108F), (0x109A, 0x109D), (0xA9E0, 0xA9EF), (0xA9FA, 0xA9FE), (0xAA60, 0xAA7F),  # Myanmar
    (0x1780, 0x17D3), (0x17D7, 0x17D7), (0x17DC, 0x17DD), (0x19E0, 0x19FF),  # Khmer
    (0x1A20, 0x1A5E), (0x1A60, 0x1A7C), (0x1A7F, 0x1A7F), (0x1AA7, 0x1AA7),  # Tai Tham
    (0x1B00, 0x1B4C),  # Balinese
    (0xA980, 0xA9C0), (0xA9CF, 0xA9CF),  # Javanese
    (0x2E80, 0x2FDF), (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B),  # Han radicals, marks
    (0x3041, 0x3096), (0x309D, 0x309F),  # Hiragana
    (0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF6F), (0xFF71, 0xFF9D),  # Katakana
    (0x1B000, 0x1B16F),  # kana supplement and extensions
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x3347F),  # Han, to Extension J
)
# The marks among them (vowel signs, tone marks and the like of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese and
# Javanese; Unicode general category M): a mark belongs to the letter before it, so the place before one is no word edge.
# The same code point ranges in every package (js/src/vault.ts).
_NO_SPACE_MARKS = (
    (0x0E31, 0x0E31), (0x0E34, 0x0E3A), (0x0E47, 0x0E4E),  # Thai
    (0x0EB1, 0x0EB1), (0x0EB4, 0x0EBC), (0x0EC8, 0x0ECE),  # Lao
    (0x102B, 0x103E), (0x1056, 0x1059), (0x105E, 0x1060), (0x1062, 0x1064), (0x1067, 0x106D), (0x1071, 0x1074),
    (0x1082, 0x108D), (0x108F, 0x108F), (0x109A, 0x109D), (0xA9E5, 0xA9E5), (0xAA7B, 0xAA7D),  # Myanmar
    (0x17B4, 0x17D3), (0x17DD, 0x17DD),  # Khmer
    (0x1A55, 0x1A5E), (0x1A60, 0x1A7C), (0x1A7F, 0x1A7F),  # Tai Tham
    (0x1B00, 0x1B04), (0x1B34, 0x1B44),  # Balinese
    (0xA980, 0xA983), (0xA9B3, 0xA9C0),  # Javanese
)
_HANGUL = ((0x1100, 0x11FF), (0x3131, 0x318E), (0xA960, 0xA97F), (0xAC00, 0xD7A3), (0xD7B0, 0xD7FF), (0xFFA0, 0xFFDC))


def vault_tags(text: str) -> List[str]:
    """The vault placeholders in text, in order of appearance (duplicates kept)."""
    return [m.group(0) for m in _TAG.finditer(text)]


def escape_tags(text: str) -> str:
    """Text with every placeholder-like part ("[PERSON_1]") that was already in it turned into "［PERSON_1]", so that
    the model and the restore step can tell it from the placeholders the SDK puts in. Same length."""
    return _TAG.sub(lambda m: _ESCAPED_BRACKET + m.group(0)[1:], text) if "[" in text else text


class VaultEntry(NamedTuple):
    #: The placeholder, e.g. "[PERSON_1]".
    tag: str
    #: Label family the placeholder was made for (PERSON_L2 shares PERSON's numbers).
    label: str
    #: The original text.
    value: str


class VaultHit(NamedTuple):
    """Where a known value occurs: [start, end) in code points."""

    start: int
    end: int
    label: str
    value: str


def _family(label: str) -> str:
    return label[:-3] if label.endswith("_L2") else label


def _in(ranges, c: str) -> bool:
    cp = ord(c)
    return any(a <= cp <= b for a, b in ranges)


def _is_word(c: str) -> bool:
    return c == "_" or unicodedata.category(c)[0] in "LNM"


def _no_space(c: str) -> bool:
    return _in(_NO_SPACE, c)


def _no_space_mark(c: str) -> bool:
    return c != "" and _in(_NO_SPACE_MARKS, c)


def _hangul(c: str) -> bool:
    return _in(_HANGUL, c)


def _syllable(c: str) -> bool:
    """A precomposed Hangul syllable (U+AC00 to U+D7A3)."""
    return 0xAC00 <= ord(c) <= 0xD7A3


def _findable(v: str) -> bool:
    """Long enough to be found again at word edges: 3 characters, or 2 when one is in a script written without spaces
    or both are Hangul syllables (Korean particles may follow them)."""
    return len(v) >= 3 or (len(v) == 2 and (any(_no_space(c) for c in v) or all(_syllable(c) for c in v)))


# What a span's edges can hold that is not part of the value: white space (Unicode White_Space); sentence punctuation
# (. , ; : ! ? and the ellipsis, ideographic and full-width forms); brackets and quotation marks, each with its partner.
_EDGE_SPACE = frozenset(chr(c) for c in (
    0x9, 0xA, 0xB, 0xC, 0xD, 0x20, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
    0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000))
_EDGE_STOP = frozenset(chr(c) for c in (0x2E, 0x2C, 0x3B, 0x3A, 0x21, 0x3F, 0x2026, 0x3001, 0x3002, 0xFF01, 0xFF0C, 0xFF0E, 0xFF1A, 0xFF1B, 0xFF1F))
_BRACKETS = [(0x28, 0x29), (0x5B, 0x5D), (0x7B, 0x7D), (0x3C, 0x3E), (0x201C, 0x201D), (0x2018, 0x2019), (0xAB, 0xBB), (0x300C, 0x300D),
             (0x300E, 0x300F), (0xFF08, 0xFF09), (0xFF3B, 0xFF3D), (0xFF5B, 0xFF5D), (0x3008, 0x3009), (0x300A, 0x300B), (0x3010, 0x3011),
             (0xFF1C, 0xFF1E), (0x3014, 0x3015)]
_CLOSER = {chr(o): chr(c) for o, c in _BRACKETS}  # an opening bracket: its closing one
_OPENER = {chr(c): chr(o) for o, c in _BRACKETS}  # a closing bracket: its opening one
_EDGE_QUOTE = frozenset((chr(0x22), chr(0x27), chr(0x60)))  # " ' and `, their own partners
# A date as ISO 8601 writes it, followed by "T" and a digit: the start of an ISO timestamp.
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]")


def value_edges(text: str, start: int, end: int, label: str = "") -> Optional[Tuple[int, int]]:
    """[start, end) of text, a span a detector found with label, without what its edges hold that is not part of the
    value: a model can mark "Kim Minsu." at the end of a sentence, an address with the " (" after it, a name with the
    quotation marks around it or a bytes literal's "b'", and the vault would learn that as the value. At either edge
    white space goes. Then, except in a SECRET (a password can end with "!" or hold brackets): sentence punctuation (at
    the start not a ".", and at the end of an ORG not its ".": "Tiffany & Co."); a bracket whose partner is not in the
    span, a bracket that opens at the end or closes at the start; a straight quotation mark that is the only one of its
    kind left in the span; a pair of brackets, or the only two of one straight quotation mark, around all the rest; and
    the "b'" of a Python bytes literal at the start. A date of birth that goes on into the time of its ISO timestamp
    ("1990-01-02T00") is cut at the date, the edge where the vault finds a known date in a timestamp. A bracket whose
    partner is inside stays ("(02) 1234-5678"). None when nothing but white space and punctuation is left; the span
    without its white space when what is left is too short to be found again (_findable). One pass over the span (the
    bracket partners in a list): time in proportion to its length. The same in every package (valueEdges in npm)."""
    # the partner of each bracket in the span (one pass: a stack for each kind of bracket), and how many of each straight
    # quotation mark the span holds, kept as its edges go
    partner = [-1] * (end - start)  # (at i - start; -1: none)
    opened: Dict[str, List[int]] = {}
    quotes: Dict[str, int] = {}
    for i in range(start, end):
        c = text[i]
        if c in _CLOSER:
            opened.setdefault(c, []).append(i)
        elif c in _OPENER:
            stack = opened.get(_OPENER[c])
            if stack:
                j = stack.pop()
                partner[i - start] = j
                partner[j - start] = i
        elif c in _EDGE_QUOTE:
            quotes[c] = quotes.get(c, 0) + 1
    a, b = start, end
    while a < b and text[b - 1] in _EDGE_SPACE:
        b -= 1
    while a < b and text[a] in _EDGE_SPACE:
        a += 1
    ws, we = a, b
    while a < b and label != "SECRET":
        last = text[b - 1]
        if (last in _EDGE_SPACE or (last in _EDGE_STOP and not (last == "." and label == "ORG"))
                or last in _CLOSER  # a bracket that opens at the end
                or (last in _OPENER and partner[b - 1 - start] < a)  # one that closes what the span does not open
                or (last in _EDGE_QUOTE and quotes.get(last) == 1)):
            if last in _EDGE_QUOTE:
                quotes[last] = 0
            b -= 1
            continue
        first = text[a]
        if (first in _EDGE_SPACE or (first in _EDGE_STOP and first != ".")
                or first in _OPENER  # a bracket that closes at the start
                or (first in _CLOSER and not 0 <= partner[a - start] < b)  # one that opens what the span does not close
                or (first in _EDGE_QUOTE and quotes.get(first) == 1)):
            if first in _EDGE_QUOTE:
                quotes[first] = 0
            a += 1
            continue
        # a pair around all the rest
        if b - a >= 2 and ((first in _CLOSER and partner[a - start] == b - 1)
                           or (first in _EDGE_QUOTE and last == first and quotes.get(first) == 2)):
            if first in _EDGE_QUOTE:
                quotes[first] = 0
            a += 1
            b -= 1
            continue
        # the "b'" of a Python bytes literal (not a "b" at the end of a word: "Bob'")
        if first == "b" and b - a >= 2 and text[a + 1] in _EDGE_QUOTE and (a == 0 or not _is_word(text[a - 1])):
            quotes[text[a + 1]] = quotes.get(text[a + 1], 1) - 1
            a += 2
            continue
        break
    if label == "DATE_OF_BIRTH" and b - a > 11 and _STAMP.match(text, a, a + 12):
        b = a + 10
    if a >= b:
        return None
    if (a, b) == (ws, we):
        return a, b
    return (a, b) if _findable(text[a:b]) else (ws, we)


def value_spans(text: str, spans: Sequence[Any]) -> List[Any]:
    """spans of text with value_edges, for the vault to learn; a span with nothing left is left out. A block-list span
    (its entry is exact) and a known value's span stay as they are. A span that is not a NamedTuple (a detector of the
    application's own) comes back as a Span when it changes."""
    out: List[Any] = []
    for s in spans:
        if getattr(s, "source", None) in ("block", "vault") or not 0 <= s.start < s.end <= len(text):
            out.append(s)
            continue
        edges = value_edges(text, s.start, s.end, getattr(s, "label", "") or "")
        if edges is None:
            continue
        a, b = edges
        if (a, b) == (s.start, s.end):
            out.append(s)
        elif hasattr(s, "_replace"):
            out.append(s._replace(start=a, end=b))
        else:
            from ._types import Span

            out.append(Span(a, b, s.label, getattr(s, "source", "model")))
    return out


def _joins(c: str) -> bool:
    """What joins the character before it in the vault's reading: a combining mark, a conjoining Hangul vowel or final."""
    cp = ord(c)
    return unicodedata.category(c)[0] == "M" or 0x1160 <= cp <= 0x11FF or 0xD7B0 <= cp <= 0xD7FF


def _extends(c: str) -> bool:
    """What joins the character before it into one grapheme cluster, so that a hit takes it in ("渡辺" and a variation
    selector, "김민수" and a combining mark): what joins it in the vault's reading, and the zero width joiner and
    non-joiner, the half-width katakana sound marks, emoji modifiers and tag characters. The same set in every
    package."""
    cp = ord(c)
    return _joins(c) or cp in (0x200C, 0x200D, 0xFF9E, 0xFF9F) or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F


def _widen(text: str, end: int) -> int:
    """end moved past what joins the character before it (_extends)."""
    n = len(text)
    while end < n and _extends(text[end]):
        end += 1
    return end


#: What a value of two Hangul syllables may have right after it in the same word, one piece after another to the end of
#: the run of syllables: particles, the honorifics 님 and 씨, and the copula. Any other syllable makes a longer word
#: ("지원금", "정수기"), and a two-syllable name is often a word too. The same list in every package.
_KO_TAIL = (
    "가", "같이", "고", "과", "까지", "께", "께서", "나", "나마", "네", "는", "니까", "님", "다", "대로", "더러", "도", "든", "든지", "라고",
    "라는", "라도", "라서", "랑", "로", "로서", "로써", "를", "마다", "마저", "만", "만큼", "며", "밖에", "보다", "부터", "씨", "아", "야",
    "어", "었", "에", "에게", "에게서", "에서", "여", "였", "예요", "와", "요", "으로", "으로서", "으로써", "은", "을", "의", "이", "이면",
    "인", "인데", "입니다", "조차", "죠", "지", "지만", "처럼", "한테", "한테서",
)  # fmt: skip
#: The longest run of syllables _ko_tail reads; a longer one is another word.
_KO_TAIL_MAX = 8


def _ko_tail(text: str, at: int) -> bool:
    """Whether the Hangul syllables from at (right after a value of two syllables) are pieces of _KO_TAIL, one after
    another to the end of the run."""
    end = at
    while end < len(text) and end - at <= _KO_TAIL_MAX and _syllable(text[end]):
        end += 1
    run = text[at:end]
    if len(run) > _KO_TAIL_MAX:
        return False
    ok = [False] * len(run) + [True]  # ok[i]: run[i:] is pieces of the list
    for i in range(len(run) - 1, -1, -1):
        ok[i] = any(run.startswith(p, i) and ok[i + len(p)] for p in _KO_TAIL)
    return ok[0]


#: Greek capitals that carry an accent or a breathing, as ranges (first, last, the capital without it): the tonos
#: capitals, the polytonic ones and those with varia or oxia (the dialytika is kept). From the Unicode decompositions
#: (U+0300, U+0301, U+0313, U+0314, U+0342, U+0343 left out); the same table in every package.
_GREEK_BARE = (
    (0x386, 0x386, 0x391), (0x388, 0x388, 0x395), (0x389, 0x389, 0x397), (0x38A, 0x38A, 0x399), (0x38C, 0x38C, 0x39F),
    (0x38E, 0x38E, 0x3A5), (0x38F, 0x38F, 0x3A9), (0x3D3, 0x3D3, 0x3D2), (0x1F08, 0x1F0F, 0x391), (0x1F18, 0x1F1D, 0x395),
    (0x1F28, 0x1F2F, 0x397), (0x1F38, 0x1F3F, 0x399), (0x1F48, 0x1F4D, 0x39F), (0x1F59, 0x1F59, 0x3A5), (0x1F5B, 0x1F5B, 0x3A5),
    (0x1F5D, 0x1F5D, 0x3A5), (0x1F5F, 0x1F5F, 0x3A5), (0x1F68, 0x1F6F, 0x3A9), (0x1FBA, 0x1FBB, 0x391), (0x1FC8, 0x1FC9, 0x395),
    (0x1FCA, 0x1FCB, 0x397), (0x1FDA, 0x1FDB, 0x399), (0x1FEA, 0x1FEB, 0x3A5), (0x1FEC, 0x1FEC, 0x3A1), (0x1FF8, 0x1FF9, 0x39F),
    (0x1FFA, 0x1FFB, 0x3A9),
)  # fmt: skip
_GREEK_DROP = (0x300, 0x301, 0x313, 0x314, 0x342, 0x343)


def _greek(c: str) -> bool:
    """A Greek letter (or a Coptic one of the Greek block)."""
    cp = ord(c)
    return 0x370 <= cp <= 0x3FF or 0x1F00 <= cp <= 0x1FFF


def _greek_capitals(up: str) -> Optional[str]:
    """up (a value in capitals) as Greek writes capitals: without accents and breathings on Greek letters, the
    dialytika kept ("ΝΊΚΟΣ" as "ΝΙΚΟΣ"); None when up has no Greek letter."""
    if not any(_greek(c) for c in up):
        return None
    out: List[str] = []
    after_greek = False  # the last letter that is not a combining mark is Greek
    for c in up:
        cp = ord(c)
        if 0x300 <= cp <= 0x36F:
            if after_greek:
                if cp in _GREEK_DROP:
                    continue
                if cp == 0x344:
                    cp = 0x308  # (dialytika and tonos: the dialytika stays)
                if cp == 0x308 and out and out[-1] in (chr(0x399), chr(0x3A5)):
                    out[-1] = chr(0x3AA) if out[-1] == chr(0x399) else chr(0x3AB)
                    continue
            out.append(chr(cp))
            continue
        after_greek = _greek(c)
        out.append(next((chr(to) for a, b, to in _GREEK_BARE if a <= cp <= b), c))
    return "".join(out)


# (letters for the capitals of some languages, built from their code points)
_CAPITAL_I_DOT = chr(0x130)  # the capital of "i" in Turkish and Azerbaijani
_SHARP_S = chr(0xDF)
_CAPITAL_SHARP_S = chr(0x1E9E)  # a capital that German may write for "ß"


def _capitals(read: str) -> Tuple[str, ...]:
    """The spellings of a value (as the vault reads it) in capitals that find also matches, each way a language writes
    them, as the vault reads them (composed: the capitals of an iota with dialytika and tonos are three code points,
    which a text written in capitals has composed again in the vault's reading): its full Unicode uppercase ("WEISS" for
    "Weiß", "EMINE ŞAHIN"), for a value with "i" the Turkish and Azerbaijani capitals with "İ" ("EMİNE ŞAHİN"), for a
    Greek value the capitals without accents and breathings ("ΕΛΕΝΗ" for "Ελένη"), and for a value with a sharp s the
    capitals with the capital sharp s ("WEIẞ"). Only the spellings other than read, each once."""
    up = vault_reading(read.upper())[0]
    spellings = [up]
    greek = _greek_capitals(up)
    if greek is not None:
        spellings.append(vault_reading(greek)[0])
    if "i" in read:
        spellings.append(vault_reading(read.replace("i", _CAPITAL_I_DOT).upper())[0])
    if _SHARP_S in read:
        spellings.append(vault_reading(read.replace(_SHARP_S, _CAPITAL_SHARP_S).upper())[0])
    out: List[str] = []
    for s in spellings:
        if s != read and s not in out:
            out.append(s)
    return tuple(out)


#: Full-width ASCII (U+FF01 to U+FF5E) as ASCII and U+3000 as a space, for str.translate; and whether a text has any
#: of them (one search instead of a test per character).
_NARROW = {**{c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}, 0x3000: 0x20}
_WIDE_ANY = re.compile("[" + chr(0xFF01) + "-" + chr(0xFF5E) + chr(0x3000) + "]")


def _narrow(s: str) -> str:
    return s.translate(_NARROW)


def narrowed(text: str) -> str:
    """text with full-width ASCII (U+FF01 to U+FF5E) as ASCII and U+3000 as a space, as the vault reads it: one
    character for one, so a place in it is the same place in text."""
    return _narrow(text) if _WIDE_ANY.search(text) else text


def vault_reading(text: str) -> Tuple[str, Optional[Sequence[int]]]:
    """The text as the vault reads it: full-width ASCII (U+FF01 to U+FF5E) and U+3000 as ASCII, and in NFC (Hangul
    written as conjoining jamo composed into syllables, a decomposed accent composed). Returns the reading and where
    each of its characters starts in text (None: the reading is the text). A character of a part that NFC leaves as it
    is stands at its own place (one for one: full width is read one character for one); the characters of a part that
    NFC composes all start where the part starts (_end_in gives where a hit that ends inside them ends)."""
    if not _WIDE_ANY.search(text) and unicodedata.normalize("NFC", text) == text:
        return text, None
    out: List[str] = []
    where = array("i")
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and _joins(text[j]):
            j += 1
        piece = _narrow(text[i:j])
        read = unicodedata.normalize("NFC", piece)
        out.append(read)
        # (a part NFC keeps, such as a name with a variation selector or a mark that composes with nothing, keeps its
        # places: a hit that ends before the joining character ends there, not at the start of the part)
        where.extend(range(i, i + len(read)) if read == piece else [i] * len(read))
        i = j
    where.append(n)
    return "".join(out), where


def _end_in(where: Sequence[int], i: int) -> int:
    """Where a hit that ends before character i of a reading (vault_reading) ends in the text: where that character
    starts, or, when i is inside the characters of a part that NFC composed (they all start at one place), the end of
    that part, so that a hit covers the whole part (more, never less). where does not go down, so the end of the part
    is found by a binary search (a part can be long: a letter with thousands of marks)."""
    if i <= 0 or i >= len(where) - 1 or where[i] != where[i - 1]:
        return where[i]
    return where[bisect.bisect_right(where, where[i], i + 1, len(where) - 1)]


def _ascii_lower(s: str) -> str:
    return s.translate(_LOWER)


_LOWER = {c: c + 32 for c in range(65, 91)}


def _is_number(c: str) -> bool:
    return c != "" and unicodedata.category(c)[0] == "N"


def _at_edges(text: str, start: int, end: int, v: str, stamp: bool = False) -> bool:
    """Word edges: a value is not found inside a longer word. A letter of a script written without spaces (Han, kana,
    Thai, ...) on either side of the boundary is an edge ("注文ZX-88213-Kは", "CEO王伟来了"), but not a mark of such a
    script after a value that ends in one (a Thai vowel sign: "สม" is not found in "สมัคร"). Where
    Hangul meets other letters or digits is an edge, Korean particles may follow a Hangul value ("김민수님께"), and Hangul
    right before a Hangul value is not an edge ("이김민수"). After a value of two Hangul syllables, more syllables are
    an edge only as particles, 님, 씨 or the copula (_KO_TAIL): "지원님께" and "지원이" are the name, "지원금" is another
    word. A dot between two digits joins them, and so does the sign of an exponent before a number, so a number is not
    found inside a longer one ("4524" in "4524.7" or in "1e+4524"). With stamp (find with variants): a date followed by
    "T" and a digit is at an edge, as an ISO timestamp writes it ("1990-01-02T00:00:00.000Z")."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    first, last = v[0], v[-1]
    ok_before = (
        before == ""
        or not _is_word(before)
        or not _is_word(first)
        or _no_space(first)
        or _no_space(before)
        or _hangul(before) != _hangul(first)
    )
    # (a mark of a script written without spaces belongs to the letter before it: the word goes on, "สม" in "สมัคร";
    # after a value of two Hangul syllables, more syllables are an edge only as particles, 님, 씨 or the copula)
    mark_after = _no_space(last) and _no_space_mark(after)
    two_syllables = after != "" and _syllable(after) and len(v) == 2 and _syllable(first) and _syllable(last)
    ok_after = _ko_tail(text, end) if two_syllables else (
        after == ""
        or not _is_word(after)
        or not _is_word(last)
        or (not mark_after and (_no_space(last) or _no_space(after)))
        or _hangul(after)
        or _hangul(last)
        or (stamp and after in ("T", "t") and text[end + 1 : end + 2] in _DIGITS and _ISO_DATE.fullmatch(v) is not None)
    )
    return ok_before and ok_after and not joined_to_number(text, start, end, v)


def joined_to_number(text: str, start: int, end: int, v: str) -> bool:
    """Whether the value v at [start, end) of text is part of a longer number there: a dot between two digits joins
    them ("4524" in "4524.7" or in "1.4524"), and an exponent belongs to its number ("4524" in "1e+4524", and "-4524"
    in "1e-4524"). An exponent is a number: a value that is not one (an address, a phone number with spaces, "04524")
    is not joined to "<digit>e+" before it ("Unit+4E+12+Main+St" in a form field). A digit is any number character, as
    in a word."""
    first, last = v[0], v[-1]
    number = _SIGNED_NUMBER.fullmatch(v) is not None
    before = text[start - 1] if start > 0 else ""
    second = text[start - 2] if start > 1 else ""
    third = text[start - 3] if start > 2 else ""
    after = text[end : end + 1]
    exponent_before = number and before in ("+", "-") and second in ("e", "E") and _is_number(third)
    joined_before = (_is_number(first) and ((before == "." and _is_number(second)) or exponent_before)) or (
        number and first in ("+", "-") and _is_number(v[1:2]) and before in ("e", "E") and _is_number(second)
    )  # (the second: the value starts with the sign)
    joined_after = _is_number(last) and after == "." and _is_number(text[end + 1 : end + 2])
    return joined_before or joined_after


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
# White space as Unicode has it, the same in every package's pattern (not the engine's own class), and what ends a line.
_SPACE = "[\\t\\n\\v\\f\\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
_SPACES = re.compile(_SPACE + "+")
_SPACE_AT_END = re.compile(f"\\A{_SPACE}|{_SPACE}\\Z")
_LINE_BREAK = re.compile("[\n\r\u0085\u2028\u2029]")
# (a number as JSON writes one: no zero in front, so "04524", a postal code, is not the number 4524)
_NUMBER_ONLY = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_NUMBERS = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
# A number as JSON writes one, with a sign in front or without: what an exponent can be.
_SIGNED_NUMBER = re.compile(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
# A date as ISO 8601 writes it ("1990-01-02"), which an ISO timestamp starts with ("1990-01-02T00:00:00.000Z").
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# A date in a Python repr or call: "datetime.date(1990, 1, 2)", "datetime.datetime(1990, 1, 2, 9, 30)"; groups: the year,
# the month and the day (the name must not end a longer word: not "update(1990, 1, 2)").
_DATE_CALL = re.compile(r"(?:date|datetime)\([ \t]*([0-9]{4}),[ \t]*([0-9]{1,2}),[ \t]*([0-9]{1,2})(?=[ \t]*[,)])")
_DIGITS = frozenset("0123456789")


def _lines_of(value: str) -> List[str]:
    """value in its lines, each with every run of white space as one space, for finding it where its white space is
    written another way ("12 Main St\\n  Apt 8\\nSeoul" as ["12 Main St", "Apt 8", "Seoul"]). A comma before white
    space ends a line too, so a value held as "12 Main St, Seoul" is also found over two lines or without the comma.
    Nothing for a value without white space inside it (one that starts or ends with white space is only found as it is
    written), nor for one with an empty line (", Tiffany & Co.", "a, , b": only found as it is written; an empty first
    line would be found at every place of a text)."""
    if not _SPACES.search(value) or _SPACE_AT_END.search(value):
        return []
    out: List[str] = []
    line, last = "", 0
    for m in _SPACES.finditer(value):
        comma = m.start() - 1 >= last and value[m.start() - 1] == ","
        line += value[last : m.start() - 1 if comma else m.start()]
        if comma or _LINE_BREAK.search(m.group(0)):
            out.append(line)
            line = ""
        else:
            line += " "
        last = m.end()
    out.append(line + value[last:])
    return [] if "" in out else out


def _two_words(v: str) -> bool:
    """A value of two or more words: runs of word characters with white space between them."""
    return sum(1 for w in re.split(_SPACE, v) if any(_is_word(c) for c in w)) >= 2


_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_PATH_START = re.compile(r"[/?#]")


def _caseless_part(label: str, v: str) -> int:
    """How many characters of v (from its start) are matched in any ASCII case: all of an e-mail address, a user name
    and a value of two or more words; a URL's scheme and host; nothing of other values."""
    if label in ("EMAIL", "USERNAME") or _two_words(v):
        return len(v)
    if label != "URL":
        return 0
    scheme = _SCHEME.match(v)
    host = scheme.end() if scheme else 0
    stop = _PATH_START.search(v, host)
    return len(v) if stop is None else stop.start()


class _Known(NamedTuple):
    """A known value as the vault searches for it: its entry; the value as the vault reads it (vault_reading); how many
    characters of that, from its start, match in any ASCII case, and it in ASCII lower case; it in capitals, in each
    spelling that is another one (_capitals: each way a language writes them); whether it is long enough to be found at
    word edges."""

    entry: VaultEntry
    read: str
    caseless: int
    lower: str
    uppers: Tuple[str, ...]
    findable: bool


def _known_of(e: VaultEntry) -> _Known:
    read = vault_reading(e.value)[0]
    caseless = _caseless_part(e.label, read)
    return _Known(e, read, caseless, _ascii_lower(read) if caseless else "", _capitals(read), _findable(read))


def _same_as(k: _Known, s: str) -> bool:
    """Whether s (a reading) is the known value k in a spelling the vault matches."""
    if s == k.read or s in k.uppers:
        return True
    c = k.caseless
    return c > 0 and len(s) == len(k.read) and _ascii_lower(s[:c]) == k.lower[:c] and s[c:] == k.read[c:]


# Vault.find reads a text shorter than this through an index of the first two characters of each value it searches for,
# when the vault holds at least _GRAM_VALUES values: only the values whose first two characters stand in the text are
# searched (since 1.7.2, D4 of the release standard: a JSON row's short strings were each searched for every known value).
# The values searched and the hits are the same as without the index.
_GRAM_TEXT = 4096
_GRAM_VALUES = 16


def _grams(s: str) -> Set[str]:
    """Every two characters that stand next to each other in s."""
    return {s[i : i + 2] for i in range(len(s) - 1)}


class _Search:
    """What Vault.find reads, copied once per change of the vault (immutable): the known values in the order they were
    added, when each was added, the numbers and dates, the values with white space (their lines), and, made when a short
    text is first searched, the index of the first two characters of each spelling searched for."""

    __slots__ = ("version", "known", "age", "numbers", "dates", "spaced", "_index")

    def __init__(self, version: int, known: List[_Known], age: Dict[str, int], numbers: Dict[str, "VaultEntry"], dates: Dict[str, "VaultEntry"], spaced: List[Any]) -> None:
        self.version = version
        self.known = known
        self.age = age
        self.numbers = numbers
        self.dates = dates
        self.spaced = spaced
        self._index: Optional[Tuple[Any, ...]] = None

    def index(self) -> Tuple[Any, ...]:
        """(in the reading as written, in its ASCII lower case, always) for the known values and (in the white-space
        reading, in its ASCII lower case, always) for the spaced ones: each maps the first two characters of a spelling
        to the positions of the values that have it; "always" holds the positions of spellings shorter than two."""
        if self._index is None:
            by_read: Dict[str, List[int]] = {}
            by_lower: Dict[str, List[int]] = {}
            always: List[int] = []

            def put(index: Dict[str, List[int]], s: str, j: int, short: List[int]) -> None:
                if len(s) < 2:
                    short.append(j)
                else:
                    index.setdefault(s[:2], []).append(j)

            for j, k in enumerate(self.known):
                if k.caseless:
                    put(by_lower, k.lower, j, always)
                else:
                    put(by_read, k.read, j, always)
                for u in k.uppers:
                    put(by_read, u, j, always)
            plain: Dict[str, List[int]] = {}
            folded: Dict[str, List[int]] = {}
            plain_always: List[int] = []
            for j, (_e, lines, fold, _findable) in enumerate(self.spaced):
                put(folded if fold else plain, lines[0], j, plain_always)
            self._index = (by_read, by_lower, always, plain, folded, plain_always)
        return self._index


def _candidates(index: Dict[str, List[int]], text: Optional[str], into: Set[int]) -> None:
    """The positions of index whose first two characters stand in text, added to into."""
    if text is None or not index:
        return
    for g in _grams(text):
        found = index.get(g)
        if found is not None:
            into.update(found)


#: A value of at most this many characters is looked for again after each place where it starts (each step costs at most
#: 32 characters); a longer one is searched for in one pass (_occurrences). The same in every package.
_SHORT_NEEDLE = 32


def _period(p: str) -> int:
    """The shortest period of p: the smallest q > 0 with p[i] == p[i + q] wherever both are in p (Knuth-Morris-Pratt)."""
    fail = [0] * len(p)
    k = 0
    for j in range(1, len(p)):
        c = p[j]
        while k and c != p[k]:
            k = fail[k - 1]
        if c == p[k]:
            k += 1
        fail[j] = k
    return len(p) - k


def _occurrences(s: str, p: str, first: Optional[int] = None) -> List[int]:
    """Every place where p starts in s, overlapping ones too ("Ann Ann" in "xAnn Ann Ann"), in time linear in both. A
    long value that repeats itself ("ha ha ha" again and again) is not compared again from its start at every place:
    after a place where it starts, it starts again one period (q) later where the text goes on with its last q
    characters, and nowhere else before it no longer overlaps itself by q or more (two places closer than that would give
    it a shorter period); str.find goes on from there. first: the first place, when the caller has looked for it (a
    value that is not in the text costs one str.find, as before 1.7.2)."""
    out: List[int] = []
    i = (s.find(p) if p else -1) if first is None else first
    if i < 0:
        return out
    m = len(p)
    if m <= _SHORT_NEEDLE:
        while i >= 0:
            out.append(i)
            i = s.find(p, i + 1)
        return out
    q = _period(p)
    tail = p[m - q :]
    skip = max(q, m - q + 1)
    while i >= 0:
        out.append(i)
        while s.startswith(tail, i + m):
            i += q
            out.append(i)
        i = s.find(p, i + skip)
    return out


def _followed_by(at: List[int], s: str, offset: int, p: str) -> List[int]:
    """The places of at (in order) where p also starts offset characters later in s: one pass over both lists when p is
    long, so that checking the part of a value that must be written as it is costs no more than finding it."""
    if len(p) <= _SHORT_NEEDLE:
        return [a for a in at if s.startswith(p, a + offset)]
    if not at:
        return at
    starts = set(_occurrences(s, p))
    return [a for a in at if a + offset in starts]


def _lines_at(plain: str, lines: List[str], start: int) -> int:
    """Where lines, written out, end in plain (white space as single spaces) when they start at start; -1 when they do
    not stand there. Between two lines stands a space, or a comma and a space."""
    if not plain.startswith(lines[0], start):
        return -1
    end = start + len(lines[0])
    for line in lines[1:]:
        nxt = end + 2 if plain.startswith(", ", end) else end + 1 if plain.startswith(" ", end) else -1
        if nxt < 0 or not plain.startswith(line, nxt):
            return -1
        end = nxt + len(line)
    return end


class _Hay:
    """A text as the search for white space written another way reads it (every run of white space as one space), with
    what that search needs of it, made once for all values: where each long line of a value starts, and the text with
    every ", " written as " "."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._starts: Dict[str, Set[int]] = {}
        self._joined: Optional[Tuple[str, List[int]]] = None

    def _line_at(self, line: str, at: int) -> bool:
        if len(line) <= _SHORT_NEEDLE:
            return self.text.startswith(line, at)
        starts = self._starts.get(line)
        if starts is None:
            starts = self._starts[line] = set(_occurrences(self.text, line))
        return at in starts

    def places(self, lines: List[str], first: int) -> Iterable[Tuple[int, int]]:
        """(start, end) of every place where lines stand one after another in the text, as _lines_at reads them (between
        two lines a space, or a comma and a space), in time linear in the text and the lines. Where no line ends with a
        comma, each line stands where " ".join(lines) does in the text with every ", " as " ", unless a space inside a
        line was a comma and a space there: only the spaces between lines may be either. A long value that repeats
        itself is then found in one pass, and only a place whose part of the text has a comma and a space looks at
        them. first: where the first line first starts in the text."""
        text = self.text
        if len(lines) == 1:
            for i in _occurrences(text, lines[0], first):
                yield i, i + len(lines[0])
            return
        if any(line.endswith(",") for line in lines):
            # (a line that ends with a comma takes the comma of a comma and a space: line by line, as _lines_at)
            for i in _occurrences(text, lines[0], first):
                end = i + len(lines[0])
                for line in lines[1:]:
                    nxt = end + 2 if text.startswith(", ", end) else end + 1 if text.startswith(" ", end) else -1
                    if nxt < 0 or not self._line_at(line, nxt):
                        end = -1
                        break
                    end = nxt + len(line)
                if end >= 0:
                    yield i, end
            return
        if self._joined is None:
            parts = text.split(", ")
            commas: List[int] = []  # where each space that was ", " is in the joined text
            at = -1
            for part in parts[:-1]:
                at += len(part) + 1
                commas.append(at)
            self._joined = (" ".join(parts), commas)
        joined, commas = self._joined
        value = " ".join(lines)
        between: Set[int] = set()  # where the spaces between two lines are in value
        at = -1
        for line in lines[:-1]:
            at += len(line) + 1
            between.add(at)
        m = len(value)
        for j in _occurrences(joined, value):
            lo = bisect.bisect_left(commas, j)
            hi = bisect.bisect_left(commas, j + m)
            if all(commas[k] - j in between for k in range(lo, hi)):
                # (the commas left out before a place move it back to the text: lo before its start, hi before its end)
                yield j + lo, j + m + hi


def _commaless(plain: str) -> str:
    return plain.replace(", ", " ")


def _space_reading(text: str) -> Tuple[str, Optional[Sequence[int]]]:
    """text with every run of white space as one space, and where each of its characters starts in text (None: the
    text itself)."""
    out: List[str] = []
    where = array("i")  # (4 bytes a place)
    pos = 0
    for m in _SPACES.finditer(text):
        if m.group(0) == " ":
            continue
        out.append(text[pos : m.start()])
        out.append(" ")
        where.extend(range(pos, m.start() + 1))
        pos = m.end()
    if pos == 0:
        return text, None
    out.append(text[pos:])
    where.extend(range(pos, len(text) + 1))
    return "".join(out), where


#: One token of a JSON text: (start, end, value, the key it stands under, kind), and for a key a sixth field, the object
#: it is a key of (a number per object of the text). kind is "string", "number" or "key"; value is the decoded string,
#: or the number as it is written.
JsonToken = Tuple[Any, ...]


def json_tokens(text: str) -> Optional[List[JsonToken]]:
    """The strings, numbers and keys of a JSON text, with where each token is; None when text is not valid JSON. For
    changing values inside JSON text while every other token and the spacing stay as written; the key says what a value
    is, for whoever reads the values in context."""
    try:
        json.loads(text)
    except (ValueError, RecursionError):  # (nested too deep to parse: read as text, like any other text)
        return None
    out: List[JsonToken] = []
    # the key the next value stands under: its own in an object, the array's in an array; under holds the keys of the
    # objects and arrays around it ({"username":[{"note":"box"},"blue"]} reads "username: blue")
    key: Optional[str] = None
    under: List[Optional[str]] = []
    # which object a key is a key of (objects are numbered as they open), so that two keys of one object can be told
    inside: List[int] = []
    objects = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "{[":
            under.append(key)
            if c == "{":
                objects += 1
                key = None
            inside.append(objects if c == "{" else 0)
            i += 1
        elif c in "}]":
            key = under.pop()
            inside.pop()
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            k = j + 1
            while k < n and text[k] in " \t\n\r":
                k += 1
            value = json.loads(text[i : j + 1])
            if k < n and text[k] == ":":
                out.append((i, j + 1, value, None, "key", inside[-1] if inside else 0))
                key = value
            else:
                out.append((i, j + 1, value, key, "string"))
            i = j + 1
        elif c == "-" or c.isdigit():
            m = _NUMBER.match(text, i)  # outside a string of valid JSON, a digit or a minus sign starts a number
            if m:
                out.append((i, m.end(), m.group(0), key, "number"))
                i = m.end()
            else:
                i += 1
        else:
            i += 1
    return out


def json_string_values(text: str) -> Optional[List[JsonToken]]:
    """The string values of a JSON text (not its keys or numbers); None when text is not valid JSON."""
    tokens = json_tokens(text)
    return None if tokens is None else [t for t in tokens if t[4] == "string"]


#: A way to read a text: the reading, and where each of its characters starts in the text (an array of places).
Reading = Tuple[str, Sequence[int]]

_UNICODE_ESCAPE = re.compile(r"\\+u([0-9a-fA-F]{4})")
_RUN = re.compile(r"\\+")
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
# (JSON's escapes, and "\'" as Python and JavaScript write a quotation mark in text with single quotes)
_SHORT = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", '"': '"', "/": "/", "'": "'"}
_PLAIN_NUMBER = re.compile(r"(-?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?")


def _escape_at(text: str, end: int, short: bool, low: str) -> Optional[Tuple[str, int]]:
    """The escape that the character at end starts after a backslash: what it stands for and where it stops. A character
    outside the basic plane is written as two escapes (a surrogate pair): one character here, when the second one
    follows with the backslashes that low allows ("any" number, a "power" of two, or "one")."""
    if text.startswith("u", end):
        m = _HEX4.match(text, end + 1)
        if m is None:
            return None
        code, stop = int(m.group(0), 16), m.end()
        if 0xD800 <= code <= 0xDBFF:
            pair = _UNICODE_ESCAPE.match(text, stop)
            if pair and 0xDC00 <= int(pair.group(1), 16) <= 0xDFFF:
                run = pair.start(1) - 1 - stop
                if low == "any" or run == 1 or (low == "power" and run & (run - 1) == 0):
                    code, stop = 0x10000 + ((code - 0xD800) << 10) + (int(pair.group(1), 16) - 0xDC00), pair.end()
        return chr(code), stop
    if short and end < len(text) and text[end] in _SHORT:
        return _SHORT[text[end]], end + 1
    return None


def _read_escapes(text: str, short: bool, whole: bool, py: bool = False) -> Optional[Reading]:
    """A reading of text with its escapes replaced wherever they stand: the Unicode ones ("\\u00e9"), and with short
    also "\\n", "\\t", "\\"" and the like. whole: an escape takes every backslash before it, however many (JSON inside
    a JSON string, at any depth). Otherwise it takes as many as it is written with at its depth (1, 2, 4, ...: the
    lowest bit of their number) and the backslashes before those stay as they are written. One pass; a run of
    backslashes is looked at once, however long it is. Returns the reading and a map: map[i] is where character i of
    the reading starts in text (and map[len] is len(text)), so that what is found in the reading can be replaced in
    text without cutting an escape. None when nothing was replaced."""
    out: List[str] = []
    where = array("i")  # (4 bytes a place)
    pos, n = 0, len(text)
    for m in _RUN.finditer(text):
        start, end = m.span()
        if start < pos:
            continue  # the second half of a surrogate pair, read with the first
        d = _escape_at(text, end, short, "any" if whole else "power")
        run = end - start
        # (with py, a Python repr escape after an odd run: it takes one backslash, and the pairs before it stay)
        p = _py_escape_at(text, end) if d is None and py and run & 1 else None
        if d is None and p is None:
            continue
        stay = end - 1 if p is not None else start if whole else end - (run & -run)
        d = d if d is not None else p
        assert d is not None
        out.append(text[pos:stay])
        where.extend(range(pos, stay))
        out.append(d[0])
        where.extend([stay] * len(d[0]))
        pos = d[1]
    if pos == 0:
        return None
    out.append(text[pos:])
    where.extend(range(pos, n + 1))
    return "".join(out), where


_HEX2 = re.compile(r"[0-9a-fA-F]{2}")
_HEX8 = re.compile(r"[0-9a-fA-F]{8}")


def _py_escape_at(text: str, end: int) -> Optional[Tuple[str, int]]:
    """A Python repr escape that the character at end starts after a backslash: "U" and eight hex digits, and "x" and
    two, where a run of them that makes a UTF-8 character is read as that character (a bytes literal: b'Jos' with c3
    and a9) and any other as the code point (a str: 'Kim' with a0 and 'Minsu'). What it stands for and where it stops."""
    if text.startswith("U", end):
        m = _HEX8.match(text, end + 1)
        code = int(m.group(0), 16) if m else -1
        return (chr(code), m.end()) if m and code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else None

    def byte_at(i: int) -> int:
        # (the byte of the escape whose "x" is at i, with a backslash before it)
        ok = i >= 1 and text.startswith("x", i) and text[i - 1] == _BS and _HEX2.match(text, i + 1)
        return int(text[i + 1 : i + 3], 16) if ok else -1

    first = byte_at(end)
    if first < 0:
        return None
    more = 0 if first < 0x80 else 1 if 0xC2 <= first <= 0xDF else 2 if 0xE0 <= first <= 0xEF else 3 if 0xF0 <= first <= 0xF4 else -1
    data = [first]
    stop = end + 3
    for _ in range(max(more, 0)):
        nxt = byte_at(stop + 1) if text.startswith(_BS, stop) else -1
        if nxt < 0 or nxt & 0xC0 != 0x80:
            break
        data.append(nxt)
        stop += 4
    if more > 0 and len(data) == more + 1:
        try:
            return bytes(data).decode("utf-8"), stop  # (not a longer way to write a shorter one, not half a pair)
        except UnicodeDecodeError:
            pass
    return chr(first), end + 3


_BS = chr(92)


def _unescape_once(text: str, py: bool = False) -> Optional[Reading]:
    """text unescaped once, as a JSON string is read, wherever an escape stands: two backslashes are one, and a
    backslash that is left over is read with the character after it ("\\n", "\\"", "\\u00e9"; one that starts no escape
    stays). With py, as a Python repr is read: also "x" and "U" escapes (_py_escape_at). The map as in _read_escapes;
    None when nothing was replaced."""
    out: List[str] = []
    where = array("i")  # (4 bytes a place)
    pos, n = 0, len(text)
    for m in _RUN.finditer(text):
        start, end = m.span()
        if start < pos:
            continue
        pairs = (end - start) >> 1
        d = ((_py_escape_at(text, end) if py else None) or _escape_at(text, end, True, "one")) if (end - start) & 1 else None
        if pairs == 0 and d is None:
            continue  # one backslash that starts no escape
        out.append(text[pos:start])
        where.extend(range(pos, start))
        out.append("\\" * pairs)
        where.extend(range(start, start + 2 * pairs, 2))
        if d is not None:
            out.append(d[0])
            where.extend([end - 1] * len(d[0]))
            pos = d[1]
        else:
            pos = start + 2 * pairs  # (an odd backslash after the pairs is copied with what follows)
    if pos == 0:
        return None
    out.append(text[pos:])
    where.extend(range(pos, n + 1))
    return "".join(out), where


_PERCENT = re.compile(r"((?:%[0-9a-fA-F]{2})+)|\+")


def _percent_reading(text: str, plus: bool) -> Optional[Reading]:
    """text read as part of a URL: "%40" as "@", bytes that make a UTF-8 character as that character ("%EA%B9%80" as
    "김"), and with plus a "+" as a space (a form field: "name=Kim+Minsu"). Bytes that are not UTF-8 stay as they are
    written. The map as in _read_escapes; None when nothing was replaced."""
    out: List[str] = []
    where = array("i")  # (4 bytes a place)
    pos, n = 0, len(text)

    def put(start: int, end: int, read: str) -> None:
        nonlocal pos
        out.append(text[pos:start])
        where.extend(range(pos, start))
        out.append(read)
        where.extend([start] * len(read))
        pos = end

    for m in _PERCENT.finditer(text):
        start, end = m.span()
        if m.group(1) is None:
            if plus:
                put(start, end, " ")
            continue
        data = bytes.fromhex(m.group(1).replace("%", ""))
        k = 0
        while k < len(data):
            first = data[k]
            # one character: its first byte says how many more it takes
            more = 0 if first < 0x80 else 1 if 0xC2 <= first <= 0xDF else 2 if 0xE0 <= first <= 0xEF else 3 if 0xF0 <= first <= 0xF4 else -1
            try:
                read = data[k : k + more + 1].decode("utf-8") if more >= 0 and k + more < len(data) else None
            except UnicodeDecodeError:
                read = None
            if read is None:
                k += 1  # not UTF-8: as it is written
                continue
            put(start + 3 * k, start + 3 * (k + more + 1), read)
            k += more + 1
    if pos == 0:
        return None
    out.append(text[pos:])
    where.extend(range(pos, n + 1))
    return "".join(out), where


#: How many steps are put on top of one another (JSON inside a JSON string inside a URL: three).
_MAX_DEPTH = 4
#: How far past a run of backslashes or "%" a reading can change the text within four steps (a percent-encoded "%"
#: before a percent-encoded backslash, "U" and eight hex digits: 13).
_ESCAPE_REACH = 16
#: Parts of a text that are read together when they stand this close (characters from the first to the last): fewer and
#: longer parts, so that the work per part does not add up over many short ones.
_PART_LENGTH = 4096
_SIGNS = re.compile(r"[\\%+]+")


def _changed_parts(text: str) -> List[List[int]]:
    """The parts of text that a reading can change: each run of backslashes, "%" and "+", with the 16 characters after
    a run that holds a backslash or a "%", merged where they meet. Outside them every reading is the text as written."""
    out: List[List[int]] = []
    for m in _SIGNS.finditer(text):
        run = m.group(0)
        end = min(len(text), m.end() + (_ESCAPE_REACH if (_BS in run or "%" in run) else 0))
        if out and m.start() <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([m.start(), end])
    return out


def unchanged_length(text: str) -> int:
    """How many characters of text that are not white space no reading changes (every reading, with its white space
    as single spaces as a known value's white space is matched, is at least this long). Full-width ASCII is read as
    ASCII first, as escaped_readings reads it."""
    t = _narrow(text) if _WIDE_ANY.search(text) else text
    n, start = 0, 0
    for a, b in _changed_parts(t) + [[len(t), len(t)]]:
        n += (a - start) - sum(m.end() - m.start() for m in _SPACES.finditer(t, start, a))
        start = b
    return n


_SPACE_ONE = re.compile(_SPACE)


def is_space_char(c: str) -> bool:
    """Whether c (one character) is white space as the vault has it (_SPACE, the same in every package)."""
    return _SPACE_ONE.fullmatch(c) is not None


def _step_over(text: str, at: int, n: int, back: bool) -> int:
    """Where text is n characters on from at (with back, before it), a run of white space counted as one character, as
    a known value's white space is matched however long its run ("Kim", 45 spaces and "Minsu" is "Kim Minsu"). A run
    that at stands in counts as one with the rest of it."""
    i = at
    for _ in range(n):
        if (i <= 0) if back else (i >= len(text)):
            break
        if _SPACE_ONE.match(text, i - 1 if back else i):
            if back:
                while i > 0 and _SPACE_ONE.match(text, i - 1):
                    i -= 1
            else:
                m = _SPACES.match(text, i)
                i = m.end() if m else i + 1
        else:
            i += -1 if back else 1
    return i


def _number_char(c: str) -> bool:
    """What a number is written with ("4.524e+03"), as a character on its own."""
    return c != "" and c in "0123456789.eE+-"


def _in_number(t: str, i: int) -> bool:
    """Whether t[i] is part of a number: a digit, a dot, "e" or "E" after a digit, a sign after "e" or "E"."""
    c = t[i]
    if c in "0123456789.":
        return True
    if c in "eE":
        return i > 0 and t[i - 1] in _DIGITS
    return c in "+-" and i > 0 and t[i - 1] in "eE"


def _number_start(t: str, at: int, floor: int) -> int:
    """Where the number that at stands inside starts (not before floor); at when it stands inside none."""
    if at <= floor or at >= len(t) or not _number_char(t[at - 1]) or not _number_char(t[at]):
        return at
    i = at
    while i > floor and _in_number(t, i - 1):
        i -= 1
    return i


def _number_end(t: str, at: int, ceil: int) -> int:
    """Where the number that at stands inside ends (not after ceil); at when it stands inside none."""
    if at <= 0 or at >= ceil or not _number_char(t[at - 1]) or not _number_char(t[at]):
        return at
    i = at
    while i < ceil and _in_number(t, i):
        i += 1
    return i


def _readings_of(text: str) -> List[Reading]:
    """Every reading of text within four steps (see escaped_readings), nearest first, each once. Full-width ASCII that
    a step decodes is read as ASCII before the next step."""
    steps = (
        lambda t: _read_escapes(t, True, True) if _BS in t else None,
        lambda t: _unescape_once(t) if _BS in t else None,
        lambda t: _percent_reading(t, False) if "%" in t else None,
        lambda t: _percent_reading(t, True) if "%" in t or "+" in t else None,
        lambda t: _unescape_once(t, True) if (_BS + "x") in t or (_BS + "U") in t else None,
    )
    out: List[Reading] = []
    seen = {text}
    level: List[Optional[Reading]] = [None]  # (None: the text itself)
    for _ in range(_MAX_DEPTH):
        nxt: List[Optional[Reading]] = []
        for origin in level:
            for step in steps:
                r = step(text if origin is None else origin[0])
                if r is None:
                    continue
                # (full-width ASCII that a step decoded is read as ASCII before the next step, as it is in the text:
                # "%EF%BC%85" is "％", read as "%". One character for one, so the places hold)
                read = _narrow(r[0]) if _WIDE_ANY.search(r[0]) else r[0]
                if read in seen:
                    continue
                seen.add(read)
                reading = (read, r[1] if origin is None else array("i", map(origin[1].__getitem__, r[1])))
                out.append(reading)
                nxt.append(reading)
        if not nxt:
            break
        level = nxt
    return out


def escaped_readings(text: str, reach: Optional[int] = None) -> List[Any]:
    """Other ways to read text, for finding the vault's known values where they were written with escapes: JSON inside
    other text, a log field, JSON inside a JSON string, a URL, a Python repr. Quotation marks are not paired, so a stray
    one cannot hide anything. One step is one of: (1) every JSON escape decoded wherever it stands, with any number of
    backslashes before it; (2) the text unescaped once as a JSON string is ("DOMAIN\\\\kim" as "DOMAIN\\kim"); (3) the
    text as part of a URL ("kim%40example.com"), with "+" as it is (a path); (4) the same with "+" as a space (a form
    field: "name=Kim+Minsu"); (5) the text unescaped once as a Python repr is, with its "x" and "U" escapes too, a run
    of "x" escapes that makes a UTF-8 character read as that character (what LangChain writes with str() for a tool's
    bytes, set or dict). Steps are put on top of one another, up to four deep, because encodings nest: JSON in a form
    field ("%7B%22user%22%3A%22CORP%5C%5Ckim%22%7D"), a form field in JSON, JSON inside a JSON string. Every reading
    within four steps is given, nearest first, and those that say the same once: escapes elsewhere in the text cannot
    use up what is read. Each as (reading, map), the map as in json_escapes_view.
    reach, for searching known values (the longest of them, and some room for its white space written another way):
    the work is bounded by reading parts of the text instead of all of it. Each part is a run of escapes, with the runs
    that stand within reach of it (and those that keep the part within 4,096 characters) and what stands within reach
    around them, read on its own, as (reading, map, text, core): the map gives places in the text, from the part's start
    to its end; outside the part the reading is the text as written; core is where its escapes stand, from the first to
    the end of the last (the rest is there for what reaches out from them). Distances count a run of white space as
    one character, as a known value's white space is matched whatever its run. A value that reaches no further than
    reach past the escapes it is written with is found in it as in the whole text, and escapes far away from it make no
    more readings of it; a text with escapes everywhere is read whole. Full-width ASCII is read as ASCII before any step
    ("kim％40example.com"). A reading can be wrong (in "C:\\new" there is no line break, in "1+1" no space): readings
    are only used to find more to mask, never less."""
    # (full-width ASCII is read as ASCII before the escapes are, so that an escape written with full-width characters
    # is read too: "kim％40example.com" as "kim%40example.com". One character stands for one, so a place in the
    # narrowed text is the same place in the text)
    t = _narrow(text) if _WIDE_ANY.search(text) else text
    if not _SIGNS.search(t):
        return []
    if reach is None:
        return list(_readings_of(t))
    parts = _changed_parts(t)
    out: List[Any] = []
    i = 0
    floor = 0  # (where the part before starts: a part reaches no further back)
    while i < len(parts):
        # (runs of escapes closer than reach are read together, as one value can stand in both; so are those that keep
        # the part within _PART_LENGTH. Distances count a run of white space as one character, as a known value's white
        # space is matched: "Kim", 45 spaces and "%4Dinsu" is "Kim Minsu" within its reach)
        j = i
        while j + 1 < len(parts) and (
            parts[j + 1][1] - parts[i][0] <= _PART_LENGTH or _step_over(t, parts[j][1], reach, False) >= parts[j + 1][0]
        ):
            j += 1
        core = (parts[i][0], parts[j][1])
        # (a number is read whole, however many digits it is written with: a part that would start or end inside one
        # takes all of it, "4.52400000000000000000e%2B03" for a known 4524; not past the part before or the next escapes)
        start = _number_start(t, _step_over(t, core[0], reach, True), floor)
        stop = _number_end(t, _step_over(t, core[1], reach, False), max(core[1], parts[j + 1][0]) if j + 1 < len(parts) else len(t))
        for r, where in _readings_of(t[start:stop]):
            out.append((r, where if start == 0 else array("i", (x + start for x in where)), text, core))
        floor = start
        i = j + 1
    return out


def plain_number(written: str) -> str:
    """A JSON number in plain decimal form, digit for digit and without going through a float: "8.21023456789e11" and
    "821023456789.0" as "821023456789", "1.25e1" as "12.5", "0e-1" and "-0.0" as "0", "1.50" as "1.5". A number with an
    exponent beyond 64, zero too, and anything that is not a number stays as it is written."""
    m = _PLAIN_NUMBER.fullmatch(written)
    if m is None:
        return written
    written_exp = (m.group(4) or "0").lstrip("+-").lstrip("0")  # (not int() of any length: it has a limit)
    if len(written_exp) > 2:
        return written
    exp = int(written_exp or 0) * (-1 if (m.group(4) or "").startswith("-") else 1)
    if abs(exp) > 64:
        return written
    digits = (m.group(2) + (m.group(3) or "")).lstrip("0")
    if not digits:
        return "0"
    point = len(digits) + exp - len(m.group(3) or "")  # where the decimal point stands in digits
    whole = "0" if point <= 0 else digits + "0" * (point - len(digits)) if point >= len(digits) else digits[:point]
    part = ("0" * -point + digits if point <= 0 else digits[point:]).rstrip("0")
    return m.group(1) + whole + ("." + part if part else "")


def json_escapes_view(text: str) -> Optional[Reading]:
    """text read through the Unicode escapes in it: "Jos\\u00e9" as "José", wherever the escape stands (a stray
    quotation mark must not hide it) and however many times its backslash was escaped again (JSON inside a JSON
    string); the backslashes that stand before the escape's own stay ("DOMAIN\\\\\\uae40" keeps "DOMAIN\\\\"). Returns
    the reading and a map: map[i] is where character i of the reading starts in text (and map[len] is len(text)), so
    that what is found in the reading can be replaced in text without cutting an escape. None when there is nothing to
    decode. Python repr escapes too, as LangChain writes a tool's value with str() ("Kim\\xa0Minsu", b'Jos\\xc3\\xa9'
    as UTF-8, "\\U0001f600"), each after one backslash. Other escapes ("\\n", "\\t") are not touched: outside a JSON
    document a backslash is a backslash ("ACME\\tkim", a path)."""
    py = "\\x" in text or "\\U" in text
    return _read_escapes(text, False, False, py) if py or "\\u" in text else None


def tag_ranges(text: str) -> List[Tuple[int, int, str]]:
    """The placeholder-like parts of text and where they stand: (start, end, tag)."""
    return [(m.start(), m.end(), m.group(0)) for m in _TAG.finditer(text)] if "[" in text else []


def apply_vault_ranges(
    text: str,
    ranges: Iterable[Tuple[int, int, str, str]],
    vault: "Vault",
    brackets: Iterable[Tuple[int, int]] = (),
) -> Tuple[str, List[str]]:
    """text with ranges of it replaced by the vault's placeholders: each (a, b, label, value), a part of the text as it
    is written and the value it stands for (what it reads as), in order. Each of brackets outside the ranges, the
    opening bracket of placeholder-like text that was already there, becomes the escaped bracket. A range that starts
    inside the one before it is cut to what is left of it. Returns the text and the placeholders put in."""
    out: List[str] = []
    tags: List[str] = []
    marks = list(brackets)
    pos, m = 0, 0

    def gap(to: int) -> None:
        nonlocal pos, m
        while m < len(marks) and marks[m][0] < to:
            if marks[m][0] >= pos:  # (otherwise inside a range)
                out.append(text[pos : marks[m][0]])
                out.append(_ESCAPED_BRACKET)
                pos = marks[m][1]
            m += 1
        if pos < to:
            out.append(text[pos:to])
            pos = to

    with vault._lock:
        for a, b, label, value in ranges:
            if b <= pos or a >= b:
                continue
            gap(max(pos, a))
            tag = vault._tag_found(label, value)
            tags.append(tag)
            out.append(tag)
            pos = max(pos, b)
    gap(len(text))
    return "".join(out), tags


_SURROGATE = re.compile("[\ud800-\udfff]")
_DOCUMENT_START = re.compile(r"\s*[{\[]")
_ANSWER_START = re.compile(r'\s*[{\["]')  # (a JSON answer may also be one string)
_FAMILY = re.compile(r"[A-Z][A-Z0-9_]{0,31}")


def json_document(text: str) -> Optional[List[JsonToken]]:
    """The tokens of text (keys, strings, numbers) when it is one JSON object or array; None for any other text
    ("[PERSON_1] said", a bare string or number, JSON with text around it)."""
    return json_tokens(text) if _DOCUMENT_START.match(text) else None


def quote_numbers(text: str, found: Sequence[Any], vault: "Vault", loose: bool) -> Optional[Tuple[str, List[Any]]]:
    """For text a model wrote that is a JSON document (an earlier structured answer): the text with every number in it
    that holds something to mask written as a JSON string, and found (spans of the text, NamedTuples with start and end)
    moved with it, so that the placeholder goes into a string and the answer stays valid JSON ({"phone": 2025550148}
    becomes {"phone": "[PHONE_1]"}). Something to mask: a span of found, or a value the vault holds (find with
    variants, and loose as given: inside a longer number too). None when no number holds any."""
    tokens = json_document(text) if any(c in _DIGITS for c in text) else None
    numbers = [t for t in tokens or () if t[4] == "number"]
    if not numbers:
        return None
    # what is to be masked, as parts of the text in order and apart
    cover: List[List[int]] = []
    hits = vault.find(text, loose=loose, variants=True)
    for start, end in sorted([(s.start, s.end) for s in found] + [(h.start, h.end) for h in hits]):
        if cover and start <= cover[-1][1]:
            cover[-1][1] = max(cover[-1][1], end)
        else:
            cover.append([start, end])
    quotes: List[int] = []  # where a quotation mark goes, in the text as it is
    out: List[str] = []
    last = k = 0
    for start, end, *_rest in numbers:
        while k < len(cover) and cover[k][1] <= start:
            k += 1
        if k >= len(cover) or cover[k][0] >= end:
            continue
        out.append(text[last:start] + '"' + text[start:end] + '"')
        last = end
        quotes += [start, end]
    if not quotes:
        return None
    out.append(text[last:])
    # how many quotation marks go before a place: at the start of a number, its opening one too; at its end, not its
    # closing one
    moved = [
        s._replace(start=s.start + bisect.bisect_right(quotes, s.start), end=s.end + bisect.bisect_left(quotes, s.end))
        for s in found
    ]
    return "".join(out), moved


def replace_json_strings(text: str, tokens: List[JsonToken], values: List[str]) -> str:
    """text with the values at tokens replaced by values (same order); a value that did not change keeps its token as
    written (a number or a key that changed is written as a string too)."""
    out, last = [], 0
    for (start, end, old, *_rest), new in zip(tokens, values):
        out.append(text[last:start])
        # (a lone surrogate, from an escape like "\ud83d", is written as an escape again: it cannot be sent as UTF-8)
        out.append(text[start:end] if new == old else _SURROGATE.sub(lambda c: "\\u%04x" % ord(c.group()), json.dumps(new, ensure_ascii=False)))
        last = end
    out.append(text[last:])
    return "".join(out)


def _map_strings(value: Any, f) -> Any:
    if isinstance(value, str):
        return f(value)
    if isinstance(value, list):
        return [_map_strings(v, f) for v in value]
    if isinstance(value, tuple):
        return tuple(_map_strings(v, f) for v in value)
    if isinstance(value, dict):
        return {k: _map_strings(v, f) for k, v in value.items()}
    return value


def apply_vault(
    text: str,
    spans: Iterable[Any],
    vault: "Vault",
    keep: Optional[AbstractSet[str]] = None,
    *,
    generation: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """text with spans (sorted, non-overlapping; anything with start, end and label) replaced by the vault's
    placeholders, and the placeholders it put in. Placeholder-like text left in the gaps is escaped ("［PERSON_1]"),
    except the placeholders in keep (text a model wrote with this vault's placeholders): those stay as they are, and a
    span that covers one is cut around it. One step for the vault: with generation, nothing is written and RuntimeError
    is raised if the vault was cleared since."""
    with vault._lock:
        vault._unchanged(generation)
        kept = [(m.start(), m.end()) for m in _TAG.finditer(text) if keep and m.group(0) in keep]

        def gap(t: str) -> str:
            if "[" not in t:
                return t
            return _TAG.sub(lambda m: m.group(0) if keep and m.group(0) in keep else _ESCAPED_BRACKET + m.group(0)[1:], t)

        out: List[str] = []
        tags: List[str] = []
        last = 0

        def piece(start: int, end: int, label: str) -> None:
            nonlocal last
            if start >= end or start < last:
                return
            original = text[start:end]
            out.append(gap(text[last:start]))
            if original.strip() == "":
                out.append(original)  # only white space was left between kept placeholders
            else:
                tag = vault._tag_found(label, original)
                tags.append(tag)
                out.append(tag)
            last = end

        k = 0  # (spans come in order: one pass over the kept placeholders)
        for s in spans:
            start = s.start
            while k < len(kept) and kept[k][1] <= start:
                k += 1
            j = k
            while j < len(kept) and kept[j][0] < s.end:
                piece(start, kept[j][0], s.label)
                start = max(start, kept[j][1])
                j += 1
            piece(start, s.end, s.label)
        out.append(gap(text[last:]))
        return "".join(out), tags


class VaultRestorer:
    """Undoes masking in text that arrives in pieces (a streamed answer). A placeholder split across pieces is held back
    until it is complete, so push may return less than it was given; flush returns what is left."""

    def __init__(self, vault: "Vault", json_escape: bool = False, only: Only = None) -> None:
        # the values the vault holds now: a streamed text is restored with one set of values from its start to its end
        only = only if isinstance(only, Mapping) else vault._snapshot(only)
        self._vault = vault
        self._json = json_escape
        self._only = only
        self._held = ""

    def push(self, chunk: str) -> str:
        self._held += chunk
        m = _TAG_PREFIX.search(self._held[-_MAX_TAG:])
        cut = len(self._held) - len(m.group(0)) if m else len(self._held)
        out = self._vault.restore(self._held[:cut], json_escape=self._json, only=self._only)
        self._held = self._held[cut:]
        return out

    def flush(self) -> str:
        out = self._vault.restore(self._held, json_escape=self._json, only=self._only)
        self._held = ""
        return out


class Vault:
    """Numbered placeholders that can be undone. Pass ``entries()`` or ``to_json()`` of a saved vault to continue it;
    both hold the original values, so store them only where the original text may be stored. Thread-safe."""

    def __init__(self, state: Any = ()) -> None:
        self._lock = threading.RLock()
        self._by_value: Dict[str, VaultEntry] = {}
        # for find with variants: the values that are numbers, by their plain form, and those with white space, in
        # their lines
        self._by_number: Dict[str, VaultEntry] = {}
        # the values that are ISO dates, for their Python repr ("datetime.date(1990, 1, 2)")
        self._by_date: Dict[str, VaultEntry] = {}
        # (a value has its lines as it is read and, when that is another spelling, its lines in capitals)
        self._spaced: Dict[str, List[Tuple[VaultEntry, List[str], bool, bool]]] = {}
        # the values with white space by their lines as one line without commas (for whole), and when each value was added
        # (fold: the lines are in ASCII lower case and match in any ASCII case)
        self._by_spelling: Dict[str, List[Tuple[VaultEntry, List[str], bool]]] = {}
        # each value as the vault searches for it; by its reading, its capitals and its caseless part (for _same)
        self._known: Dict[str, _Known] = {}
        self._by_read: Dict[str, _Known] = {}
        self._by_upper: Dict[str, _Known] = {}
        self._by_lower: Dict[str, List[_Known]] = {}
        # the length of the longest value in the longest spelling find matches (decomposed, in capitals), for reach
        self._longest = 0
        # what find reads, copied once per change (_search): the change count it was copied at
        self._version = 0
        self._search_state: Optional[_Search] = None
        self._age: Dict[str, int] = {}
        self._by_tag: Dict[str, VaultEntry] = {}
        self._counters: Dict[str, int] = {}
        self._generation = 0
        self._replays: Dict[str, Any] = {}
        # the values a model found that are shorter than the vault searches for (_tag_found): in _known for the loose
        # search only, not in the indexes of a whole value and its other spellings
        self._local: Set[str] = set()
        entries = state.get("entries", ()) if isinstance(state, Mapping) else state
        # (the placeholders of short values a model found, since 1.7.2: a list, as the npm package writes it)
        given = state.get("local") if isinstance(state, Mapping) else None
        local = {t for t in given if isinstance(t, str)} if isinstance(given, list) else set()
        for e in entries:
            tag, label, value = (e["tag"], e["label"], e["value"]) if isinstance(e, Mapping) else e
            self._add(VaultEntry(tag, _family(label), value), tag in local)
        if isinstance(state, Mapping):
            for k, v in state.get("replays", ()):
                if isinstance(k, str):
                    self._replays[k] = _copy_record(v)
                    self._reserve(v)
            for fam, n in (state.get("counters") or {}).items():
                ok = isinstance(fam, str) and _FAMILY.fullmatch(fam) and isinstance(n, int) and not isinstance(n, bool)
                if not ok or not 0 <= n <= _MAX_NUMBER:
                    raise ValueError(f"not a vault state: counter {fam!r}")
                self._counters[fam] = max(self._counters.get(fam, 0), n)

    def _reserve(self, written: Any) -> None:
        """A numbered placeholder the model made up ("[PERSON_2]" when the vault holds only PERSON_1) must not be given
        to a real value later: its number is skipped from then on (also after the records are loaded again)."""

        def reserve(t: str) -> str:
            for m in _TAG.finditer(t):
                if m.group(0) not in self._by_tag:
                    fam, _, num = m.group(1).rpartition("_")
                    # (a number far above the ones in use cannot be reached by counting, and must not use the family up)
                    if int(num) <= self._counters.get(fam, 0) + _RESERVE_AHEAD:
                        self._counters[fam] = max(self._counters.get(fam, 0), int(num))
            return t

        _map_strings(written, reserve)

    def _add(self, e: VaultEntry, restore_only: bool = False) -> None:
        """e held; with restore_only, a new value is not indexed as a whole value or in its other spellings
        (_tag_found)."""
        m = _ENTRY_TAG.fullmatch(e.tag) if isinstance(e.tag, str) else None
        ok = m and len(m.group(1)) <= _MAX_FAMILY and len(m.group(2)) <= 12
        if not ok or not isinstance(e.value, str) or e.value == "":
            raise ValueError(f"not a vault entry: {e.tag!r}")
        assert m is not None
        if e.tag in self._by_tag:
            raise ValueError(f"duplicate vault tag {e.tag}")
        self._by_tag[e.tag] = e
        self._version += 1
        if e.value not in self._by_value:
            self._by_value[e.value] = e
            self._age[e.value] = len(self._age)
            if restore_only:
                # (searched for only where every known value is, whatever its length: model text without a record)
                self._local.add(e.value)
                self._known[e.value] = _known_of(e)
            else:
                self._index(e)
        self._counters[m.group(1)] = max(self._counters.get(m.group(1), 0), int(m.group(2)))

    def _index(self, e: VaultEntry) -> None:
        """A value the vault searches for: its spellings indexed."""
        self._version += 1
        if _NUMBER_ONLY.fullmatch(e.value):
            self._by_number.setdefault(plain_number(e.value), e)
        if _ISO_DATE.fullmatch(e.value):
            self._by_date[e.value] = e
        k = _known_of(e)
        self._known[e.value] = k
        self._by_read.setdefault(k.read, k)
        for u in k.uppers:
            self._by_upper.setdefault(u, k)
        if k.caseless:
            self._by_lower.setdefault(k.lower, []).append(k)
        self._longest = max(
            self._longest,
            len(e.value),
            len(unicodedata.normalize("NFD", e.value)),
            *(max(len(u), len(unicodedata.normalize("NFD", u))) for u in k.uppers),
        )
        fold = k.caseless == len(k.read)
        lines = [_ascii_lower(x) if fold else x for x in _lines_of(k.read)]
        if lines:
            # the value's lines, and its lines in capitals (each way a language writes them) where those are another
            # spelling than the lines match already ("JOSÉ   GARCÍA" for a known "José García": capitals that ASCII
            # lower case does not undo), as written
            forms = [(e, lines, fold, k.findable)]
            for u in k.uppers:
                upper = _lines_of(u)
                if upper and (not fold or [_ascii_lower(x) for x in upper] != lines):
                    forms.append((e, upper, False, k.findable))
            self._spaced[e.value] = forms
            for _, form, form_fold, _ in forms:
                self._by_spelling.setdefault(_ascii_lower(_commaless(" ".join(form))), []).append((e, form, form_fold))

    def _unchanged(self, generation: Optional[int]) -> None:
        """Raises if the vault was cleared since its generation was generation (None: no check)."""
        if generation is not None and generation != self._generation:
            raise RuntimeError("the vault was cleared while this text was masked")

    def _same(self, read: str) -> Optional[VaultEntry]:
        """The known value that read (a text as the vault reads it) is in a spelling find matches: the one held
        longest; also with what joins its last character after it (a mark, a variation selector), as find takes that
        in."""
        found = self._same_read(read)
        if found is not None:
            return found
        cut = len(read)
        while cut > 0 and _extends(read[cut - 1]):
            cut -= 1
        if cut == 0 or cut == len(read) or not _at_edges(read, 0, cut, read[:cut]):
            return None
        return self._same_read(read[:cut])

    def _same_read(self, read: str) -> Optional[VaultEntry]:
        """_same for the reading as it is."""
        found = [self._by_read.get(read), self._by_upper.get(read)]
        if self._by_lower:
            found.extend(k for k in self._by_lower.get(_ascii_lower(read), ()) if _same_as(k, read))
        held = [k.entry for k in found if k is not None]
        return min(held, key=lambda e: self._age[e.value]) if held else None

    def _tag_found(self, label: str, value: str) -> str:
        """The placeholder for a text found in a text (what a model found, or a merged span): that of the known value it
        is in another spelling the vault matches (capitals, full-width, NFD: "KIM MINSU" for a known "Kim Minsu"), or
        its own. One value keeps one placeholder. A new value shorter than the vault searches for at word edges (3
        characters, 2 in scripts written without spaces and two Hangul syllables) is masked where it was found and in
        model text without a record (the loose search), but not as a whole value elsewhere: not as a JSON value,
        number or key, a tool argument or a swept string ("0" that a model took for a date once must not turn every 0
        of later tool data and tool calls into a placeholder). Values the application tags (tag) are found as a whole
        value whatever their length."""
        with self._lock:
            known = self._same(vault_reading(value)[0]) if self._known else None
            if known is not None:
                return known.tag
            held = self._by_value.get(value)
            if held is not None or value == "":
                return "" if held is None else held.tag
            return self._make(label, value, not _findable(vault_reading(value)[0]))

    def _reach(self) -> int:
        """How far a known value can reach in a text, for escaped_readings: its longest value in its longest spelling
        find matches (decomposed, in capitals), and room for its white space written another way."""
        return self._longest + 16

    def __len__(self) -> int:
        return len(self._by_tag)

    @property
    def generation(self) -> int:
        """Goes up by one on every clear(), so that work started before can tell its placeholders no longer resolve."""
        return self._generation

    def tag(self, label: str, value: str) -> str:
        """The placeholder for value, adding it under label (its family) when new. The same value keeps its first tag.
        An empty value has no placeholder (""). Numbers go up to 999,999,999,999; a family that has used them up (after
        an imported entry with that number) goes on as "[PERSON__1]"."""
        if value == "":
            return ""
        with self._lock:
            known = self._by_value.get(value)
            if known is not None:
                if value in self._local:
                    # (a short value a model found is found as a whole value from now on: the application gives it)
                    self._local.discard(value)
                    self._index(known)
                return known.tag
            return self._make(label, value, False)

    def _make(self, label: str, value: str, restore_only: bool) -> str:
        """A new entry for value under label (its family); restore_only as in _add."""
        with self._lock:
            fam = re.sub(r"^[^A-Z]+", "", re.sub(r"[^A-Z0-9_]", "_", _family(label.upper()))[:_MAX_FAMILY]) or "PII"
            # a family whose numbers are used up goes on in the next one, so a value always gets a placeholder that can
            # be undone
            numbered = fam
            for _ in range(_FAMILY_STEPS):
                if self._counters.get(numbered, 0) < _MAX_NUMBER:
                    break
                numbered = _next_family(numbered)
            if self._counters.get(numbered, 0) >= _MAX_NUMBER:
                # every family on the way used up (some 10^13 values, or a state made by hand): masked, not undone
                return "[" + re.sub(r"(_[0-9]*)+\Z", "", fam) + "]"
            e = VaultEntry(f"[{numbered}_{self._counters.get(numbered, 0) + 1}]", fam, value)
            self._add(e, restore_only)
            return e.tag

    def value(self, tag: str) -> Optional[str]:
        """The value behind a placeholder, or None."""
        e = self._by_tag.get(tag)
        return None if e is None else e.value

    def restore(self, text: str, *, json_escape: bool = False, only: Only = None) -> str:
        """Replaces the vault's placeholders in text with their values; other bracketed text is left as it is. With
        json_escape=True the values are escaped for the inside of a JSON string (tool-call arguments as JSON text); with
        only, just those placeholders are restored (e.g. the ones put into the current model call)."""

        def sub(m: "re.Match[str]") -> str:
            tag = m.group(0)
            if isinstance(only, Mapping):
                v = only.get(tag)
            elif only is not None and tag not in only:
                v = None
            else:
                e = self._by_tag.get(tag)
                v = None if e is None else e.value
            if v is None:
                return tag
            return json.dumps(v, ensure_ascii=False)[1:-1] if json_escape else v

        with self._lock:  # one step: a clear from another thread cannot give two values to one placeholder
            if not self._by_tag and not isinstance(only, Mapping):
                return text
            return _TAG.sub(sub, text)

    def restore_answer(self, text: str, *, only: Only = None) -> str:
        """An answer's text with the placeholders restored: a JSON document (structured output) in its string values,
        so that a value with a quote or a line break leaves it valid JSON; any other text as it is."""
        # (keys too: an answer is for the user; tool-call arguments are restored value by value elsewhere)
        tokens = [t for t in json_tokens(text) or [] if t[4] != "number"] if _ANSWER_START.match(text) else None
        if not tokens:
            return self.restore(text, only=only)
        with self._lock:
            return replace_json_strings(text, tokens, [self.restore(t[2], only=only) for t in tokens])

    def restore_value(self, value: Any, *, only: Only = None) -> Any:
        """restore applied to every string inside a JSON-like value (dicts, lists and tuples are copied, keys are kept).
        One step for the vault: every string gets the values of the same moment."""
        with self._lock:
            return _map_strings(value, lambda s: self.restore(s, only=only))

    def _snapshot(self, only: Optional[AbstractSet[str]] = None) -> Dict[str, str]:
        """The current values of these placeholders (None: all), copied in one step."""
        with self._lock:
            return {t: e.value for t, e in self._by_tag.items() if only is None or t in only}

    def restorer(self, *, json_escape: bool = False, only: Only = None) -> VaultRestorer:
        """A restorer for a streamed text. It uses the values the vault holds when it is created, for the whole stream."""
        return VaultRestorer(self, json_escape, only)

    def bindings(self, tags: Iterable[str], *, generation: Optional[int] = None) -> Dict[str, str]:
        """The current values of these placeholders, as a snapshot for only= (tags the vault does not hold are left out).
        With generation, RuntimeError is raised if the vault was cleared since (checked and copied in one step)."""
        with self._lock:
            self._unchanged(generation)
            out: Dict[str, str] = {}
            for t in tags:
                e = self._by_tag.get(t)
                if e is not None:
                    out[t] = e.value
            return out

    def _search(self) -> _Search:
        """What find reads, as of now (under the lock): copied once per change of the vault, not on every call."""
        state = self._search_state
        if state is None or state.version != self._version:
            state = _Search(
                self._version,
                list(self._known.values()),
                dict(self._age),
                dict(self._by_number),
                dict(self._by_date),
                [f for forms in self._spaced.values() for f in forms],
            )
            self._search_state = state
        return state

    def find(self, text: str, *, loose: bool = False, variants: bool = False) -> List[VaultHit]:
        """Where the known values occur in text (sorted, non-overlapping; overlapping occurrences are merged into one hit
        whose value is the merged text and whose label is the first one's). loose, for text a model wrote that the
        middleware has no record of: every known value wherever it occurs, inside longer words and whatever its length
        ("x42y"); this can mask more than needed, never less. variants, for the middlewares: also where a known value
        is written another way. Its white space may be any white space, each line break in it also a comma
        ("12 Main St\\nSeoul" as "12 Main St, Seoul" or indented on two lines), and each comma before white space also a
        line break or nothing ("12 Main St, Seoul" over two lines or as "12 Main St Seoul"); a number may be written in
        any spelling
        ("8.21023456789e11" for 821023456789). The hit then carries the known value, not the text that was found."""
        # (start, end, label, value, when the value was added: where two values are found on the same part of a text,
        # one as it is written and one in another spelling, the older one goes first, so a value keeps its placeholder)
        hits: List[Tuple[int, int, str, str, int]] = []
        with self._lock:
            state = self._search()
        known = state.known
        age = state.age
        numbers = state.numbers if variants else {}
        dates = state.dates if variants else {}
        spaced = state.spaced if variants else []
        if not known:
            return []
        # the text as the vault reads it (full-width ASCII as ASCII, NFC), searched for each value as the vault reads it
        r, rmap = vault_reading(text)
        at = (lambda i: i) if rmap is None else rmap.__getitem__
        at_end = (lambda i: i) if rmap is None else (lambda i: _end_in(rmap, i))  # (where a hit ends)
        lower: Optional[str] = None
        # a short text: only the values whose spellings start with two characters that stand in it (the same hits)
        index = state.index() if len(r) <= _GRAM_TEXT and len(known) >= _GRAM_VALUES else None
        searched: Iterable[_Known] = known
        if index is not None:
            by_read, by_lower, always = index[0], index[1], index[2]
            chosen: Set[int] = set(always)
            _candidates(by_read, r, chosen)
            if by_lower:
                lower = _ascii_lower(r)
                _candidates(by_lower, lower, chosen)
            searched = [known[j] for j in sorted(chosen)]
        for k in searched:
            if not loose and not k.findable:
                continue
            e = k.entry

            # (a hit takes in what joins its last character, so that it never ends inside a grapheme cluster; v is the
            # spelling found, which the text holds there in this case or, for the caseless part, in another ASCII case:
            # the edges read the same of both, and it is not cut out of the text again at every place)
            def take(i: int, v: str) -> None:
                if loose or _at_edges(r, i, i + len(v), v, variants):
                    hits.append((at(i), at_end(_widen(r, i + len(v))), e.label, e.value, age.get(e.value, 0)))

            if k.caseless:
                # the caseless part in any ASCII case, the rest as it is written
                if lower is None:
                    lower = _ascii_lower(r)
                i = lower.find(k.lower)
                if i >= 0:
                    for i in _followed_by(_occurrences(lower, k.lower, i), r, k.caseless, k.read[k.caseless :]):
                        take(i, k.read)
            else:
                i = r.find(k.read)
                if i >= 0:
                    for i in _occurrences(r, k.read, i):
                        take(i, k.read)
            for u in k.uppers:
                i = r.find(u)
                if i >= 0:
                    for i in _occurrences(r, u, i):
                        take(i, u)
        if dates:
            # a date in a Python repr or call ("datetime.date(1990, 1, 2)", which LangChain writes for a tool's date with
            # str()): its year, month and day are that date
            for m in _DATE_CALL.finditer(r):
                if m.start() > 0 and _is_word(r[m.start() - 1]):
                    continue
                e = dates.get(f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
                if e is not None:
                    hits.append((at(m.start(1)), at_end(_widen(r, m.end())), e.label, e.value, age.get(e.value, 0)))
        if spaced:
            # white space written another way: the text with every run of white space as one space, and each value
            # line by line; between two lines of a value stands white space, or a comma and white space
            # ("12 Main St, Seoul")
            plain, where = _space_reading(r)
            pat = at if where is None else (lambda i: at(where[i]))
            pat_end = at_end if where is None else (lambda i: at_end(where[i]))
            plain_lower: Optional[str] = None
            forms: Iterable[Any] = spaced
            if index is not None:
                by_plain, by_folded, plain_always = index[3], index[4], index[5]
                picked: Set[int] = set(plain_always)
                _candidates(by_plain, plain, picked)
                if by_folded:
                    plain_lower = _ascii_lower(plain)
                    _candidates(by_folded, plain_lower, picked)
                forms = [spaced[j] for j in sorted(picked)]
            hays: Dict[bool, _Hay] = {}
            for e, lines, fold, findable in forms:
                if not loose and not findable:
                    continue
                if fold and plain_lower is None:
                    plain_lower = _ascii_lower(plain)
                hay_text = plain_lower if fold else plain
                assert hay_text is not None
                # (a value whose first line is not in the text costs one str.find, as before 1.7.2)
                first = hay_text.find(lines[0])
                if first < 0:
                    continue
                hay = hays.get(fold)
                if hay is None:
                    hay = hays[fold] = _Hay(hay_text)
                # (where the value is written as it is, the search above has it: for a long value, every such place at
                # once)
                as_written: Optional[Set[int]] = None
                for i, end in hay.places(lines, first):
                    start, stop = pat(i), pat_end(end)
                    same = stop - start == len(e.value)
                    if same and len(e.value) <= _SHORT_NEEDLE:
                        same = text.startswith(e.value, start)
                    elif same:
                        if as_written is None:
                            as_written = set(_occurrences(text, e.value))
                        same = start in as_written
                    # (the edges read the first and the last character, and whether the value is a number or a date,
                    # which a value with white space in it never is: no copy of it at every place)
                    if not same and (loose or _at_edges(plain, i, end, plain[i] + " " + plain[end - 1])):
                        hits.append((start, pat_end(_widen(plain, end)), e.label, e.value, age.get(e.value, 0)))
        if numbers:
            # a number in another spelling: every number in the text, in its plain form
            for m in _NUMBERS.finditer(r):
                start, end = m.span()
                # a minus sign belongs to the number unless it joins it to what stands before ("010-1234")
                if start > 0 and r[start - 1] == "-" and not (start > 1 and _is_word(r[start - 2])):
                    start -= 1
                written = r[start:end]
                if not _NUMBER_ONLY.fullmatch(written):
                    continue
                plain = plain_number(written)
                e = numbers.get(plain)
                # one rule for a sign: a number whose plain form without its minus sign is a known value is that value,
                # and the sign stays outside ("-4.524e3" and "-4524" for a known 4524 both become "-[ID_NUMBER_1]")
                if e is None and plain.startswith("-"):
                    e = numbers.get(plain[1:])
                    start += 1
                if e is None or r[start:end] == e.value:
                    continue
                # (where a number stands on its own, by the edges of every other value: not "café4.524e3", not
                # "4.524e3.1")
                if loose or (len(plain.replace("-", "")) >= 3 and _at_edges(r, start, end, r[start:end])):
                    hits.append((at(start), at_end(_widen(r, end)), e.label, e.value, age.get(e.value, 0)))
        hits.sort(key=lambda h: (h[0], -h[1], h[4]))
        # overlapping known values are merged ("Alex Kim" + "Kim Lee"); the value of a merged hit is cut once, when all
        # of it is known
        ranges: List[List[Any]] = []
        for start, end, label, value, _age in hits:
            if ranges and start < ranges[-1][1]:
                if end > ranges[-1][1]:
                    ranges[-1][1] = end
                    ranges[-1][3] = None
            else:
                ranges.append([start, end, label, value])
        return [VaultHit(a, b, label, text[a:b] if value is None else value) for a, b, label, value in ranges]

    def whole(self, text: str) -> Optional[VaultEntry]:
        """For the middlewares: the known value that text is as a whole, whatever its length and however it is written
        (its white space as any white space, a line break also as a comma and a comma also as a line break or left out;
        a number in another spelling; with JSON, URL or Python repr escapes: "Kim+Minsu" in a form field; in the
        spellings find matches). When it can be read as more than one, the one held longest, so that a value keeps its
        first placeholder."""
        with self._lock:
            held: List[VaultEntry] = []
            # (other readings only when one can be as short as a known value: what no reading changes is in every one)
            # (an escape, also in full width)
            signs = _SIGNS.search(text) or (_WIDE_ANY.search(text) and _SIGNS.search(_narrow(text)))
            others = escaped_readings(text) if signs and unchanged_length(text) <= self._reach() else []
            for written in [text] + [r[0] for r in others]:
                read = vault_reading(written)[0]
                found = [self._same(read), self._by_number.get(plain_number(read)) if _NUMBER_ONLY.fullmatch(read) else None]
                if self._by_spelling and _SPACES.search(read):
                    plain = _space_reading(read)[0]
                    low = _ascii_lower(plain)
                    found.append(
                        next(
                            (e for e, lines, fold in self._by_spelling.get(_commaless(low), ()) if _lines_at(low if fold else plain, lines, 0) == len(plain)),
                            None,
                        )
                    )
                held.extend(e for e in found if e is not None)
            return min(held, key=lambda e: self._age[e.value]) if held else None

    def age(self, value: str) -> Optional[int]:
        """For the middlewares: when value was added, as its place among the values (smaller is older); None for a
        value the vault does not hold."""
        return self._age.get(value)

    def hide(self, text: str, *, escape: bool = False, keep: Optional[AbstractSet[str]] = None, loose: bool = False) -> str:
        """Replaces the vault's known values in text with their placeholders (no model call). Overlapping known values
        are hidden together under a new placeholder. With escape=True, placeholder-like text in the rest of text is
        escaped as escape_tags does, except the placeholders in keep. loose as in find."""
        with self._lock:
            hits = self.find(text, loose=loose) if self._by_value else []
            if escape:
                return apply_vault(text, hits, self, keep)[0]
            out = []
            last = 0
            for h in hits:
                out.append(text[last : h.start])
                known = self._by_value.get(h.value)
                out.append(known.tag if known is not None else self.tag(h.label, h.value))
                last = h.end
            out.append(text[last:])
            return "".join(out)

    def remember(self, key: str, masked: Any, *, generation: Optional[int] = None) -> bool:
        """Remembers what the model wrote (masked) for a text or value the middleware gave back (restored or not), so
        that the next call sends the model's own text again instead of searching the returned one for known values. key
        is the returned text or any key the caller chooses. The 4,096 used last are kept (to_json carries them). With
        generation, nothing is stored if the vault was cleared since (returns False)."""
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            # a copy, so that later changes to the caller's object cannot change what the model is taken to have written
            record = _copy_record(masked)
            # two different wordings for one key ("x[ID_1]y" and a literal "x42y" both give "x42y"): neither can be
            # trusted for that key, so it is kept as "no record" (None) and the text is searched instead
            old = self._replays.pop(key, _MISSING)
            if old is not _MISSING and (old is None or old != record):
                record = None
            self._reserve(masked)
            self._replays[key] = record
            while len(self._replays) > 4096:
                # the oldest record goes; a key marked as untrustworthy (None) stays while there is anything else to drop
                oldest = next((k for k, v in self._replays.items() if v is not None), next(iter(self._replays)))
                self._replays.pop(oldest)
            return True

    def recall(self, key: str) -> Any:
        """What remember stored for key (a copy), or None: also when two different wordings were stored for it."""
        with self._lock:
            kept = self._replays.pop(key, _MISSING)
            if kept is _MISSING:
                return None
            self._replays[key] = kept  # in use: the last to be dropped
            return _copy_record(kept)

    def hide_value(self, value: Any) -> Any:
        """hide applied to every string inside a JSON-like value; a string that is exactly a known value is replaced
        whatever its length (a restored tool-call argument such as an ID "42")."""
        with self._lock:  # one step for the vault: every string is numbered in the same generation
            if not self._by_value:
                return value
            # (a string that is a known value as a whole, in a spelling find matches: "AL" for a known "Al", whatever its
            # length)
            def one(s: str) -> str:
                known = self._same(vault_reading(s)[0])
                return known.tag if known is not None else self.hide(s)

            return _map_strings(value, one)

    def entries(self) -> List[VaultEntry]:
        """The entries in the order they were added, for keeping a vault between requests (Vault(entries))."""
        with self._lock:
            return list(self._by_tag.values())

    def to_json(self) -> Dict[str, Any]:
        """The entries and what remember stored, as JSON-ready data (the npm package's vault.toJSON()), for keeping a
        vault that a middleware uses between requests: Vault(vault.to_json()). Holds the original values, like entries."""
        with self._lock:
            out: Dict[str, Any] = {
                "entries": [{"tag": e.tag, "label": e.label, "value": e.value} for e in self._by_tag.values()],
                "replays": [[k, _copy_record(v)] for k, v in self._replays.items()],
                # the highest number used in each family, numbers skipped for placeholders a model made up included
                "counters": dict(self._counters),
            }
            if self._local:
                # the placeholders of short values a model found, not found as a whole value elsewhere (_tag_found),
                # since 1.7.2
                out["local"] = [self._by_value[v].tag for v in sorted(self._local, key=self._age.__getitem__)]
            return out

    def clear(self) -> None:
        with self._lock:
            self._by_value.clear()
            self._by_number.clear()
            self._by_date.clear()
            self._spaced.clear()
            self._by_spelling.clear()
            self._known.clear()
            self._by_read.clear()
            self._by_upper.clear()
            self._by_lower.clear()
            self._longest = 0
            self._age.clear()
            self._by_tag.clear()
            self._counters.clear()
            self._replays.clear()
            self._local.clear()
            self._generation += 1
            self._version += 1
            self._search_state = None
