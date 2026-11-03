from typing import List, NamedTuple


class Span(NamedTuple):
    """[start, end) in code points (Python str indices): text[start:end] is the masked text."""

    start: int
    end: int
    label: str
    #: "model", "regex", "model+regex" when both found it, "table" when table mode propagated it down a column, or "block" for
    #: an entry of the option block (since 1.4.0)
    source: str


class Token(NamedTuple):
    """A token id and its [start, end) in code points (without <s> and </s>)."""

    id: int
    start: int
    end: int


class RedactResult(NamedTuple):
    text: str
    spans: List[Span]
