"""Langfuse: a ``mask`` function, so traces are stored without the personal information the model finds.

    from langfuse import Langfuse
    from newmean_pii.langfuse import pii_mask

    langfuse = Langfuse(mask=pii_mask(size="medium"))

Langfuse calls the function with the input, output and metadata of every observation before they are sent. Every string
inside them is masked, at any depth: dict keys and values, list, tuple and set items, bytes (as UTF-8) and the fields of
dataclasses and pydantic models. A key that masks to a key already there gets `` #2``, `` #3``, ... so no value is lost.
Numbers, booleans, dates, UUIDs, None and Langfuse's own media objects (sent as a reference id) are kept; any other
value, and bytes that are not UTF-8, become
``[UNMASKED: <type>]``, so nothing the function cannot read is sent. This module does not import langfuse: the function
fits any code that takes ``mask(*, data, **kwargs)``.
"""
from __future__ import annotations

import array
import dataclasses
import datetime
import threading
import uuid
from collections import UserString, deque
from collections.abc import Mapping, MappingView
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


def _hashable(value: Any) -> Any:
    """A set item or dict key as walked; one that became a dict (a frozen dataclass) is kept as its text."""
    try:
        hash(value)
    except TypeError:
        return str(value)
    return value


def _is_langfuse_media(value: Any) -> bool:
    """Langfuse's own media objects, made from base64 data before ``mask`` runs and sent as a reference id (no text)."""
    cls = type(value)
    return cls.__module__.split(".")[0] == "langfuse" and cls.__name__ in ("LangfuseMedia", "LangfuseMediaReference")


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
        if isinstance(value, Enum):  # before str: str() of a str-valued member is its name, not its value
            return walk(value.value, path)
        if isinstance(value, str):
            return text(value)
        if isinstance(value, UserString):
            return text(value.data)
        if isinstance(value, _KEPT) or _is_langfuse_media(value):
            return value
        if isinstance(value, array.array):  # read as text only when it holds characters
            return text(value.tounicode()) if value.typecode in ("u", "w") else _placeholder(value)
        if isinstance(value, memoryview):
            value = value.tobytes()
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
                key = _hashable(walk(k, path))
                if key in out:  # two keys that mask to the same text keep both values
                    base, n = key if isinstance(key, str) else str(key), 2
                    while f"{base} #{n}" in out:
                        n += 1
                    key = f"{base} #{n}"
                out[key] = walk(v, path)
            return out
        if type(value) is list:
            return [walk(v, path) for v in value]
        if type(value) in (set, frozenset):
            return type(value)([_hashable(walk(v, path)) for v in value])
        if isinstance(value, tuple):
            items = [walk(v, path) for v in value]
            return type(value)._make(items) if hasattr(type(value), "_fields") else tuple(items)  # a namedtuple keeps its type
        # other containers of whole values; a generic Sequence (array, a str-like class) may yield single characters
        if isinstance(value, (list, set, frozenset, deque, range, MappingView)):
            return [walk(v, path) for v in value]
        if isinstance(value, type):
            return _placeholder(value)
        try:
            if dataclasses.is_dataclass(value):
                fields = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
            else:
                dump = getattr(value, "model_dump", None) or getattr(value, "dict", None)  # pydantic v2, then v1
                if not callable(dump):
                    return _placeholder(value)
                fields = dump()
        except Exception:  # an unset field, a property or a dump that raises
            return _placeholder(value)
        return walk(fields, path)

    def mask(*, data: Any, **kwargs: Any) -> Any:
        return walk(data, set())

    return mask
