# Deterministic patterns that run next to the model (the "model + regex" setup evaluated in the technical report).
# Same patterns as the Python baseline (training/scripts/evaluate.py REGEX) and js/src/regex.ts. Python's Unicode \w, \d
# and \b equal the npm package's explicit classes; whitespace is spelled out as JavaScript's \s, because Python's \s also
# takes U+001C..U+001F and U+0085 and leaves out U+FEFF, which would split or join matches differently from npm.
import re
from typing import List, Tuple

from ._types import Span

_WS = "\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"  # ECMAScript \s
NS = f"[^{_WS}]"

REGEX_RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("URL", re.compile(rf"https?://{NS}+|www\.{NS}+")),
    ("ID_NUMBER", re.compile(rf"\b\d{{6}}[-{_WS}]?[1-8]\d{{6}}\b")),
    ("FINANCIAL", re.compile(rf"\b(?:\d{{4}}[-{_WS}]?){{3}}\d{{4}}\b|\b\d{{2,6}}-\d{{2,6}}-\d{{2,8}}(?:-\d{{1,3}})?\b")),
    ("PHONE", re.compile(rf"(?:\+\d{{1,3}}[-{_WS}]?)?\(?\d{{2,4}}\)?[{_WS}.-]?\d{{3,4}}[{_WS}.-]?\d{{4}}\b")),
]


def regex_spans(text: str) -> List[Span]:
    """Pattern matches in rule order; a match that overlaps an earlier rule's match is skipped."""
    out: List[Span] = []
    for label, pattern in REGEX_RULES:
        for m in pattern.finditer(text):
            start, end = m.span()
            if any(start < s.end and s.start < end for s in out):
                continue
            out.append(Span(start, end, label, "regex"))
    return out
