# User lists (options allow, block and lists_id, added in 1.4.0): validation in the order of the C core and the npm package
# (js/src/lists.ts), so every package reports the same list, index and reason for the same input. The checks across entries
# (label_conflict, in_both) and the matching run in the core (native/core/src/lists.c).
from typing import Iterable, List, Optional, Tuple, Union

from . import _core

AllowEntry = Union[str, Tuple[str, Optional[str]]]
BlockEntry = Tuple[str, str]

_LABEL_ID = {l: i for i, l in enumerate(_core.LABELS)}
_MAX_ENTRIES = 10_000
_MAX_CP = 64
_MAX_U16 = 256  # 256 UTF-16 units also bound the UTF-8 bytes (at most 768, under the core's 1,024)
_MAX_ID = 128
#: The 25 White_Space code points of Unicode 17.0.
_WHITE_SPACE = frozenset(
    [*range(0x09, 0x0E), 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000]
)
_LINE_BREAKS = frozenset([0x0A, 0x0D, 0x85, 0x2028, 0x2029])

REASONS = (
    "bad_shape",
    "too_long",
    "bad_text",
    "empty",
    "edge_space",
    "unknown_label",
    "too_many",
    "label_conflict",
    "in_both",
    "bad_lists_id",
)


class ListError(ValueError):
    """An invalid allow, block or lists_id option. Carries where and why, never the entry's text.

    list: "allow", "block" or "meta" (lists_id); index: the entry index in that list, None for lists_id and for errors about
    the list itself; reason: one of REASONS."""

    def __init__(self, list: str, index: Optional[int], reason: str) -> None:
        where = "lists_id" if list == "meta" else f"{list} list" + ("" if index is None else f" entry {index}")
        super().__init__(f"{where}: {reason}")
        self.list = list
        self.index = index
        self.reason = reason

    def __reduce__(self):
        return (ListError, (self.list, self.index, self.reason))


def _bad_chars(text: str) -> bool:
    """A surrogate code point (a Python str has no pairs: each one is unpaired), U+0000 or a line break."""
    return any(c == 0 or 0xD800 <= c <= 0xDFFF or c in _LINE_BREAKS for c in map(ord, text))


def _check_text(text: str) -> Optional[str]:
    """Checks 2-6 of the design's Section 3 (shape and label are checked by the caller): a reason, or None."""
    if len(text) > _MAX_U16 or len(text) + sum(1 for c in text if ord(c) > 0xFFFF) > _MAX_U16:
        return "too_long"
    if _bad_chars(text):
        return "bad_text"
    if not text:
        return "empty"
    if len(text) > _MAX_CP:
        return "too_long"
    if ord(text[0]) in _WHITE_SPACE or ord(text[-1]) in _WHITE_SPACE:
        return "edge_space"
    return None


def _read_list(entries: object, name: str) -> List[Tuple[bytes, int]]:
    """(utf8, label_id) terms for lists_create (label_id -1: any label); stops after the 10,001st item."""
    if entries is None:
        return []
    if isinstance(entries, (str, bytes)):  # a str would be read character by character
        raise ListError(name, None, "bad_shape")
    try:
        items = iter(entries)  # type: ignore[call-overload]
    except TypeError:
        raise ListError(name, None, "bad_shape") from None
    out: List[Tuple[bytes, int]] = []
    for i, x in enumerate(items):
        if i == _MAX_ENTRIES:
            raise ListError(name, _MAX_ENTRIES, "too_many")
        if name == "allow" and isinstance(x, str):
            text, label = x, None
        elif isinstance(x, tuple) and len(x) == 2:
            text, label = x
        else:
            raise ListError(name, i, "bad_shape")
        if not isinstance(text, str) or not (isinstance(label, str) or (label is None and name == "allow")):
            raise ListError(name, i, "bad_shape")
        reason = _check_text(text)
        if reason:
            raise ListError(name, i, reason)
        if label is not None and label not in _LABEL_ID:
            raise ListError(name, i, "unknown_label")
        out.append((text.encode("utf-8"), -1 if label is None else _LABEL_ID[label]))
    return out


def check_lists_id(lists_id: object) -> Optional[str]:
    """lists_id (checked first): None, or 1 to 128 code points without U+0000, surrogates or line breaks."""
    if lists_id is None:
        return None
    if not isinstance(lists_id, str) or not 0 < len(lists_id) <= _MAX_ID or _bad_chars(lists_id):
        raise ListError("meta", None, "bad_lists_id")
    return lists_id


def make_lists(
    allow: Optional[Iterable[AllowEntry]], block: Optional[Iterable[BlockEntry]]
) -> Tuple[Optional["_core.Lists"], int, int]:
    """Validates allow, then block, then the checks across entries (in the core): (handle or None when both lists are
    empty, allow count, block count) after merging. Raises ListError."""
    a = _read_list(allow, "allow")
    b = _read_list(block, "block")
    if not a and not b:
        return None, 0, 0
    lists = _core.lists_create(a, b)
    if isinstance(lists, tuple):
        raise ListError(*lists)
    n_allow, n_block = lists.counts()
    return lists, n_allow, n_block
