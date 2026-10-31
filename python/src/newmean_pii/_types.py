from typing import List, NamedTuple


class Span(NamedTuple):
    """[start, end) in code points (Python str indices): text[start:end] is the masked text."""

    start: int
    end: int
    label: str
    #: "model", "regex", or "model+regex" when both found it
    source: str


class Token(NamedTuple):
    """A token id and its [start, end) in code points (without <s> and </s>)."""

    id: int
    start: int
    end: int


class RedactResult(NamedTuple):
    text: str
    spans: List[Span]
