"""Langfuse: a ``mask`` function, so traces are stored without the personal information the model finds.

    from langfuse import Langfuse
    from newmean_pii.langfuse import pii_mask

    langfuse = Langfuse(mask=pii_mask(size="medium"))

Langfuse calls the function with the input, output and metadata of every observation before they are sent. Every string
inside them is masked, at any depth: dict keys and values, list, tuple and set items, bytes (as UTF-8) and the fields of
dataclasses and pydantic models. A key that masks to a key already there gets `` #2``, `` #3``, ... so no value is lost.
Numbers, booleans, dates, UUIDs and None are kept; any other value, and bytes that are not UTF-8, become
``[UNMASKED: <type>]``, so nothing the function cannot read is sent. This module does not import langfuse: the function
fits any code that takes ``mask(*, data, **kwargs)``.
"""
from __future__ import annotations

import dataclasses
import datetime
import threading
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from ._redactor import Redactor, create_redactor

__all__ = ["pii_mask"]

# values that hold no free text and are kept as they are (datetime.datetime is a datetime.date)
_KEPT = (type(None), bool, int, float, complex, Decimal, datetime.date, datetime.time, datetime.timedelta, uuid.UUID)


def _placeholder(value: Any) -> str:
    """What a value that cannot be masked becomes: its type name, never its content."""
    return f"[UNMASKED: {type(value).__name__}]"


def pii_mask(redactor: Optional[Redactor] = None, *, mode: str = "tag", **options: Any) -> Callable[..., Any]:
    """A function ``mask(*, data, **kwargs) -> data`` with every string in ``data`` masked.

    ``mode`` is that of ``Redactor.redact`` ("tag" gives ``[EMAIL]``, "mask" asterisks, "blank" removes). Pass a
    ``redactor`` you already have, or ``options`` for ``create_redactor`` (size, sensitivity, labels, allow, block,
    model_dir, ...); without a redactor the model is loaded on the first call, not here.
    """
    if redactor is not None and options:
        raise TypeError("pass either a redactor or create_redactor options, not both")
    if mode not in ("tag", "mask", "blank"):
        raise ValueError(f'mode must be "tag", "mask" or "blank", not {mode!r}')
    lock = threading.Lock()
    holder: List[Redactor] = [] if redactor is None else [redactor]

    def get() -> Redactor:
        if not holder:
            with lock:
                if not holder:
                    holder.append(create_redactor(**options))
        return holder[0]

    def text(value: str) -> str:
        return get().redact(value, mode=mode).text if value else value

    def walk(value: Any, path: Set[int]) -> Any:
        if isinstance(value, str):
            return text(value)
        if isinstance(value, Enum):
            return walk(value.value, path)
        if isinstance(value, _KEPT):
            return value
        if isinstance(value, (bytes, bytearray)):
            try:
                decoded = bytes(value).decode("utf-8")
            except UnicodeDecodeError:
                return _placeholder(value)
            return type(value)(text(decoded).encode("utf-8"))
        if id(value) in path:  # a container met again on the way down: a cycle
            return "[CYCLE]"
        path.add(id(value))
        try:
            return walk_container(value, path)
        finally:
            path.discard(id(value))

    def walk_container(value: Any, path: Set[int]) -> Any:
        if isinstance(value, Mapping):
            out: Dict[Any, Any] = {}
            for k, v in value.items():
                key = walk(k, path)
                try:
                    hash(key)
                except TypeError:  # a key that walked into a dict (a frozen dataclass)
                    key = str(key)
                if key in out:  # two keys that mask to the same text keep both values
                    base, n = key if isinstance(key, str) else str(key), 2
                    while f"{base} #{n}" in out:
                        n += 1
                    key = f"{base} #{n}"
                out[key] = walk(v, path)
            return out
        if type(value) in (list, set, frozenset):
            return type(value)(walk(v, path) for v in value)
        if isinstance(value, tuple):
            items = [walk(v, path) for v in value]
            return type(value)._make(items) if hasattr(type(value), "_fields") else tuple(items)  # a namedtuple keeps its type
        if isinstance(value, (Sequence, set, frozenset)):  # deque, subclasses, other sequences
            return [walk(v, path) for v in value]
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return walk({f.name: getattr(value, f.name) for f in dataclasses.fields(value)}, path)
        dump = getattr(value, "model_dump", None) or getattr(value, "dict", None)  # pydantic v2, then v1
        if callable(dump) and not isinstance(value, type):
            try:
                dumped = dump()
            except Exception:
                return _placeholder(value)
            return walk(dumped, path)
        return _placeholder(value)

    def mask(*, data: Any, **kwargs: Any) -> Any:
        return walk(data, set())

    return mask
