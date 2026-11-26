"""Langfuse: a ``mask`` function, so the input, output and metadata of observations are stored without the personal
information the model finds.

    from langfuse import Langfuse
    from newmean_pii.langfuse import pii_mask

    langfuse = Langfuse(mask=pii_mask(size="medium"))

Langfuse calls the function with the input, output and metadata of every observation (set through its SDK, its
``@observe`` decorator and its integrations) before they are sent. Every string inside them is masked, at any depth:
the keys and values of any mapping; the items of lists, tuples, sets, frozensets, deques, ranges and dict views (the
last three written as lists); the value of an Enum member; ``str`` subclasses and ``UserString``; bytes, bytearray and
memoryview (as UTF-8) and arrays of characters; the fields of dataclasses; and what the ``model_dump`` (pydantic v2) or
``dict`` (pydantic v1) method that the class of any other object declares returns. A value is taken for what its own
class is (``type(value)``), never for the class it says it is (``__class__``), and a serializer and dataclass fields are
looked for in the namespaces of the class and its bases, without running any code of the value: a property,
``__getattr__`` or ``__getattribute__`` that fails is never taken for an attribute that is not there (gate 8 of 1.7.1,
the confirmation's item 8). An object whose class declares neither method (a proxy that only forwards them through
``__getattr__``) is "any other value", below. A key that masks to a key already there gets `` #2``, `` #3``, ... so no
value is lost; ``keys=False`` keeps keys as they are. Python objects of these types are kept: numbers, booleans, dates,
times, durations (``timedelta``), ``uuid.UUID``, None and Langfuse's own media objects (the trace holds their
reference; Langfuse uploaded their content before the mask ran). Any other value (a class too), arrays of numbers and
bytes that are not UTF-8 become ``[UNMASKED: <type>]``, so nothing the function cannot read is sent. What the function
returns is made of Python's own types only (dict, list, tuple, set, frozenset, str, bytes, bytearray and the kept
types; with ``keys=False``, keys as they are), never of a value's class, which Langfuse would write through that
class's code: a named tuple becomes a tuple, a ``str`` or bytes subclass a ``str`` or bytes, and a subclass of a kept
type that type itself (a ``uuid.UUID`` subclass a ``uuid.UUID`` with the same number), without running any of the
subclass's code. When masking fails (the model cannot load; the detector raises; anything the mask reads cannot be
read: a mapping or a sequence whose items or iteration raise, a dataclass field that is not set or whose property
raises, a serializer method or property that raises when it is read or called, an Enum member's value, a
``UserString``'s data, the bytes of a bytes object, what a subclass of a kept type holds), the whole value becomes
``[MASK FAILED]`` and the error's type, never its message, is logged to the ``newmean_pii.langfuse`` logger: the
function does not raise, because Langfuse's own fallback for an exception keeps every metadata key and logs the
message. The options are checked when ``pii_mask`` is called; the model is loaded on the first call.

Ids and field names are kept (1.7.2, measured on our development set), because the model reads most of
them as personal values and a trace needs them as written. Where the string stands decides:

- a whole string of at most 1,024 characters that is an id a provider or framework writes with its own prefix
  (``_PREFIXED_IDS``: OpenAI chat completion, tool call, Responses and fingerprint ids, Anthropic message, tool use
  and request ids, Bedrock tool use ids, LangChain run ids), wherever it stands: a value, an item or a key
  (``ids=False`` reads them as text);
- a whole string of at most 1,024 characters that is a UUID (``8-4-4-4-12`` hex digits, any case) or a LangGraph
  checkpoint namespace (``node:<uuid>``, nested with ``|``), where the trace names an id: the value of a ``str`` key
  that names an id (below), or an item there of a list, tuple, set, deque or dict view, at any depth of such
  sequences. The value of an Enum member, bytes and what an object's dump returns stand where the object stands. A key
  never stands there, and neither do the values of a mapping, which stand where their own key puts them. A UUID
  anywhere else (a key, under an id key too; the value of any other key; no key) is read by the model: it can be a
  person's identifier (an advertising id, a payment key, a customer's id);
- a ``str`` key (subclasses included) of at most 64 characters that is a field name: snake_case (lower or upper case) or
  camelCase of ASCII letters, two words or more (``user_id``, ``USER_ID``, ``userName``; in camelCase each later word is
  one capital and small letters, so ``userID`` is read), or that names an id. Any other key is read by the model: one
  word, digits, dots and hyphens are how user names and other personal values are written (``minsu92``,
  ``jukka.nieminen``). A key of another type (bytes, a number, a tuple, a ``UserString``) is read as a value is.

A key names an id when it has at most 64 characters and is in ``_ID_KEYS`` compared without case, "_", "-", "." and
spaces, a final "ids" read as "id" (``id``, ``run_id``, ``runId``, ``run-ids``, ``Run ID``, ``run.id``). Such a string
is kept only when nothing is found in it: the pattern layer (``regex_spans``) reads it without its random hex (the UUIDs
in it, the payload of a hex id), and the redactor's ``block`` list reads it and each UUID in it; when either finds
something, the string is read by the model like any other text. A number, date or time written as a string is text,
which the model reads.

The result for a key is remembered, so the same key in a later observation is not read again: the most recent 4,096
keys, per detector settings, and only with this package's own ``Redactor`` (any other redactor, or a subclass that
replaces ``detect`` or ``redact``, reads every key every time). Values are read every time.

Langfuse does not pass the rest of a trace to ``mask``: names, status messages, exception events and stack traces, trace
attributes (user and session ids, tags, propagated metadata; the LangChain callback handler propagates the root run's
``metadata`` this way), the spans of other OpenTelemetry instrumentations, and files (base64 data and media objects),
which it uploads before ``mask`` runs. This module does not import langfuse: the function fits any code that takes
``mask(*, data, **kwargs)``.
"""
from __future__ import annotations

import array
import dataclasses
import datetime
import itertools
import logging
import re
import sys
import threading
import uuid
from collections import OrderedDict, UserString, deque
from collections.abc import Iterator, Mapping, MappingView
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple

from ._lists import _MAX_ENTRIES
from ._redactor import Redactor, apply_redaction, check_redactor_options, create_redactor
from ._regex import regex_spans

__all__ = ["pii_mask"]

# Ids and field names kept as written (1.7.2, gate items L6-5 and L6-6). Each is matched against the whole string.
_HEX = "[0-9a-fA-F]"
_UUID = f"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}"
# ids whose part after the prefix is random hex: OpenAI Responses (responses and output items) and system fingerprints
_HEX_IDS = (r"(?:resp|msg|fc|rs|ws|fs|ci|ig|cu|mcp|lsh|ctc)_[0-9a-f]{32,128}", r"fp_[0-9a-f]{8,32}")
# ids that a provider or a framework writes with a prefix of its own: kept wherever they stand
_PREFIXED_IDS = _HEX_IDS + (
    rf"chatcmpl-(?:[A-Za-z0-9]{{16,64}}|{_UUID})",  # OpenAI chat completions; LiteLLM and other compatible servers
    rf"call_(?:[A-Za-z0-9]{{16,64}}|[0-9]{{1,4}}_{_UUID})",  # OpenAI tool calls; DeepSeek writes call_<n>_<uuid>
    r"(?:msg|toolu|srvtoolu|req)_[A-Za-z0-9]{16,64}",  # Anthropic messages, tool uses, server tool uses, requests
    r"tooluse_[A-Za-z0-9_-]{16,64}",  # Amazon Bedrock tool uses
    rf"(?:lc_run--|run-){_UUID}(?:-[0-9]{{1,6}})?",  # LangChain runs and messages
)
_PREFIXED_ID = re.compile("|".join(_PREFIXED_IDS))
_HEX_ID = re.compile("|".join(_HEX_IDS))
# a UUID alone, or a LangGraph checkpoint namespace ("model:<uuid>", nested with "|"): kept only where the trace names
# an id, because a UUID can also be a person's identifier (an advertising id, a payment key)
_NODE = "[A-Za-z_][A-Za-z0-9_.-]{0,63}"
_UUID_ID = re.compile(rf"{_UUID}|{_NODE}:{_UUID}(?:[|]{_NODE}:{_UUID})*")
# the longest string tried as an id (the patterns above are bounded well below it)
_ID_MAX = 1024
# UUIDs inside an id: the pattern layer does not read their random hex (nor that of a hex id), where it finds phone and
# card numbers by chance (in 5.0% of random UUIDs and 1.3% of Responses ids); the block list reads each on its own
_UUID_IN = re.compile(_UUID)
# keys under which a UUID is an id of the trace, compared without case, "_", "-", "." and spaces, with a final "ids"
# read as "id" (run_id, runId, run-ids). The ids of people (user_id, customer_id, device_id) are not among them
_ID_KEYS = frozenset(
    "id uuid guid runid parentrunid rootrunid traceid parenttraceid spanid parentspanid observationid "
    "parentobservationid sessionid threadid checkpointid parentcheckpointid checkpointns langgraphcheckpointns "
    "messageid conversationid requestid responseid previousresponseid toolcallid callid itemid generationid "
    "completionid taskid stepid batchid promptid datasetid datasetitemid datasetrunid experimentid scoreid eventid "
    "correlationid".split()
)
_KEY_GLUE = re.compile("[-_. ]")
# keys that are names of fields: snake_case in lower or upper case, or camelCase, of ASCII letters, two words or more.
# Keys of other shapes are read by the model: user names are written with digits, dots and hyphens ("jukka.nieminen")
_FIELD_NAME = re.compile(r"[a-z]+(?:_[a-z]+)+|[A-Z]+(?:_[A-Z]+)+|[a-z]+(?:[A-Z][a-z]+)+")
# the longest key kept as a field name or read as naming an id (the README names this limit); a longer key is read by
# the model, so that a long key of field-name shape cannot carry text past it
_FIELD_MAX = 64


def _is_id_key(key: str) -> bool:
    """key names an id of the trace (_ID_KEYS)."""
    if len(key) > _FIELD_MAX:
        return False
    name = _KEY_GLUE.sub("", key).lower()
    return (name[:-1] if name.endswith("ids") else name) in _ID_KEYS


def _id_shape(value: str, id_slot: bool) -> bool:
    """value is an id this mask keeps: one with a prefix of its own anywhere, a UUID where the trace names an id."""
    if len(value) > _ID_MAX:
        return False
    return _PREFIXED_ID.fullmatch(value) is not None or (id_slot and _UUID_ID.fullmatch(value) is not None)


def _field_name(key: str) -> bool:
    """key is the name of a field (_FIELD_NAME) or names an id (_ID_KEYS): a key kept as written."""
    return len(key) <= _FIELD_MAX and (_FIELD_NAME.fullmatch(key) is not None or _is_id_key(key))


# masked keys kept per key text: the same keys come with every observation
_KEYS_KEPT = 4096
# values that hold no free text and are kept as they are (datetime.datetime is a datetime.date). Numbers too: read on
# their own, or next to their key, the model takes most long numbers for identifiers, timestamps and token counts as well
# (measured on our development set for 1.7.2)
_KEPT = (type(None), bool, int, float, complex, Decimal, datetime.date, datetime.time, datetime.timedelta, uuid.UUID)
# the kept types themselves: a value of one of them is returned as it is, a value of a subclass as the type itself
# (_as_kept)
_KEPT_TYPES = frozenset(_KEPT + (datetime.datetime,))
# what a whole value becomes when masking it fails: fixed text, nothing of the value (not even a key)
_FAILED = "[MASK FAILED]"
# what _leaf returns for a value that holds other values
_OPEN = object()

_log = logging.getLogger(__name__)


# a class's own name and namespace and the classes it inherits from, read as the type object holds them (a metaclass
# cannot change what these give, nor run code to give them)
_CLASS_NAME = type.__dict__["__name__"].__get__
_CLASS_DICT = type.__dict__["__dict__"].__get__
_CLASS_MRO = type.__dict__["__mro__"].__get__
# what a uuid.UUID holds, read by its own slot
_UUID_INT = uuid.UUID.__dict__["int"].__get__
_ARRAY_TYPECODE = array.array.__dict__["typecode"].__get__


def _placeholder(value: Any) -> str:
    """What a value that cannot be masked becomes: its type name, never its content."""
    return f"[UNMASKED: {_CLASS_NAME(type(value))}]"


def _as_kept(value: Any, cls: type) -> Any:
    """A value of a subclass of a kept type, as the kept type itself, built by that type's own code from what it holds.
    Langfuse writes a kept value through methods a subclass can replace (str() of a UUID, isoformat() of a date) or,
    for a type it has no case for (a time, a duration, a Decimal, a complex), the attributes an instance holds: nothing
    of the subclass runs here, and nothing it adds is returned (gate 8 of 1.7.1, the confirmation's item 8: the mask
    returns only values of Python's own types, so that no code of the value decides what Langfuse writes)."""
    if issubclass(cls, int):  # (bool cannot be subclassed; an int Enum was read as an Enum)
        return int.__index__(value)
    if issubclass(cls, float):
        return float.__float__(value)
    if issubclass(cls, complex):
        return complex(complex.real.__get__(value), complex.imag.__get__(value))
    if issubclass(cls, Decimal):
        return Decimal(value)  # (copies the number a Decimal holds; no method of the subclass runs)
    if issubclass(cls, uuid.UUID):
        return uuid.UUID(int=int.__index__(_UUID_INT(value)))
    # (datetime first: a datetime is a date) the state a value is pickled with, read and read back by the type itself
    for base in (datetime.datetime, datetime.date, datetime.time, datetime.timedelta):
        if issubclass(cls, base):
            return base(*base.__reduce__(value)[1])
    raise TypeError("not a kept type")  # (not reached: _KEPT has no other type)


def _written(value: Any) -> str:
    """What the walk made of a set item or a dict key that cannot be hashed (a frozen dataclass became a dict), as text:
    str() of it for Python's own containers and values, written here so that no code of a value runs (a datetime's
    str() would run its tzinfo's __repr__; a date or time is written as its ISO text). Anything else the walk returns
    (a Langfuse media object) is written as its placeholder."""
    cls = type(value)
    if cls is dict:
        return "{" + ", ".join(f"{_written(k)}: {_written(v)}" for k, v in value.items()) + "}"
    if cls is list:
        return "[" + ", ".join(_written(v) for v in value) + "]"
    if cls is tuple:
        return "(" + ", ".join(_written(v) for v in value) + ("," if len(value) == 1 else "") + ")"
    if cls in (set, frozenset):
        items = ", ".join(_written(v) for v in value)
        if cls is frozenset:
            return f"frozenset({{{items}}})" if value else "frozenset()"
        return f"{{{items}}}" if value else "set()"
    if cls in (datetime.datetime, datetime.time):
        return repr(cls.isoformat(value))
    if cls in _KEPT_TYPES or cls in (str, bytes, bytearray):
        return repr(value)
    return _placeholder(value)


def _declared(cls: type, name: str) -> bool:
    """cls or a class it inherits from declares name in its own namespace. How the mask decides whether a value has a
    serializer or dataclass fields: without running any code of the value (a property,
    __getattr__, __getattribute__), so that a read that fails is never taken for an attribute that is not there (gate 8
    of 1.7.1, the confirmation's item 8: a model_dump property whose read raised AttributeError was taken for no
    serializer, and the object became [UNMASKED: <type>] next to the parts already read). What is declared is then
    read as any attribute is, and a read that fails fails the whole value."""
    return any(name in _CLASS_DICT(c) for c in _CLASS_MRO(cls))


def _serializer(value: Any) -> Optional[Callable[[], Any]]:
    """The model_dump (pydantic v2) or dict (pydantic v1) method of value: the first of the two that its class declares
    and that is callable once read. None when its class declares neither (an attribute only __getattr__ gives, a
    proxy's, is not looked at) or what it declares is not callable (model_dump = None). Reading a declared one is not
    guarded: a property that raises, AttributeError too, raises here."""
    cls = type(value)
    for name in ("model_dump", "dict"):
        if _declared(cls, name):
            method = getattr(value, name)
            if callable(method):
                return method
    return None


def _hashable(value: Any) -> Any:
    """A set item or dict key as walked; one that became a dict or a list (a frozen dataclass) is kept as its text
    (_written). It is given what the walk made (masked text, kept values of Python's own types, the dicts, lists and
    tuples it built), or with keys=False a key as it is (one that cannot be hashed, from a mapping of another kind, is
    written by _written, as its placeholder when it is not of Python's own types)."""
    try:
        hash(value)
    except TypeError:
        return _written(value)
    return value


def _is_langfuse_media(value: Any) -> bool:
    """Langfuse's own media objects, made from base64 data before ``mask`` runs and written as a reference (no text):
    of Langfuse's own classes, loaded (an object of them has loaded them), never a class that only takes their name or
    module (Langfuse would write the attributes it holds)."""
    media = sys.modules.get("langfuse.media")
    if media is None:
        return False
    cls, owned = type(value), vars(media)
    return any(cls is owned.get(name) for name in ("LangfuseMedia", "LangfuseMediaReference"))


def pii_mask(
    redactor: Optional[Redactor] = None, *, mode: str = "tag", keys: bool = True, ids: bool = True, **options: Any
) -> Callable[..., Any]:
    """A function ``mask(*, data, **kwargs) -> data`` with every string in ``data`` masked.

    ``mode`` is that of ``Redactor.redact`` ("tag" gives ``[EMAIL]``, "mask" asterisks, "blank" removes). ``keys``: True
    (default, since 1.7.2) masks dict keys like any other string (a map keyed by e-mail address or name), except ``str``
    keys of at most 64 characters that are field names (``user_id``, ``userName``) or name an id; with this package's
    own ``Redactor``, the results of the most recent 4,096 keys are remembered per detector settings, and a key among
    them is not read again (any other redactor reads every key every time). False keeps keys as they are. ``ids``: True
    (default) keeps the ids of the trace as written (provider and framework ids with their own prefix, and UUIDs where
    the trace names an id, which a key never is; see the module); False reads them as text. Field names and ids are
    kept only when the pattern layer and the ``block`` list find nothing in them. Pass a ``redactor`` you already have,
    or ``options`` for ``create_redactor`` (size, sensitivity, labels, allow, block, model_dir, ...). The options are
    checked here (an unknown name, size, variant or label raises TypeError or ValueError, a bad list ListError); the
    model is loaded on the first call, not here, and tried again on the next call if it cannot load. The function
    never raises an ``Exception``: when masking ``data`` fails, it returns ``"[MASK FAILED]"`` in place of the whole of
    ``data`` and logs the error's type (not its message, which can hold the text) at level ERROR to the
    ``newmean_pii.langfuse`` logger.
    """
    if redactor is not None and options:
        raise TypeError("pass either a redactor or create_redactor options, not both")
    if redactor is not None and not callable(getattr(redactor, "redact", None)):
        raise TypeError(
            f"redactor must be a Redactor (an object with redact), not {type(redactor).__name__}; "
            'pass create_redactor options by name, as in pii_mask(size="medium")'
        )
    if mode not in ("tag", "mask", "blank"):
        raise ValueError(f'mode must be "tag", "mask" or "blank", not {mode!r}')
    if not isinstance(keys, bool):
        raise TypeError(f"keys must be True or False, not {keys!r}")
    if not isinstance(ids, bool):
        raise TypeError(f"ids must be True or False, not {ids!r}")
    if redactor is None:
        # checked now and read again by create_redactor on the first call: a one-shot iterator is kept as a list (as
        # many entries as a list may hold, and one more, so that a list too long still fails)
        options = {k: list(itertools.islice(v, _MAX_ENTRIES + 1)) if isinstance(v, Iterator) else v for k, v in options.items()}
        check_redactor_options(**options)
    lock = threading.Lock()
    holder: List[Redactor] = [] if redactor is None else [redactor]
    # masked keys by (detector settings, key text), the most recent last
    known: "OrderedDict[Any, str]" = OrderedDict()

    def get() -> Redactor:
        if not holder:
            with lock:
                if not holder:
                    holder.append(create_redactor(**options))
        return holder[0]

    def found(value: str) -> bool:
        """The pattern layer or the redactor's block list finds something in value, which is then not kept as an id or a
        field name. The model is not asked: it reads most ids and many field names as personal values. The pattern
        layer reads value without its random hex (the UUIDs in it, the payload of a hex id); the block list reads value
        and each UUID in it on its own (a UUID glued into "lc_run--<uuid>-0" is no word of its own)."""
        readable = "" if _HEX_ID.fullmatch(value) else _UUID_IN.sub(" ", value)
        if readable and regex_spans(readable):
            return True
        r = get()
        if not isinstance(r, Redactor) or r._lists is None or not r._lists_info.get("block"):
            return False
        model = r._core_model()
        for part in [value, *(u for u in _UUID_IN.findall(value) if u != value)]:
            # labels 0: no span of the model's own, while the block list's spans are reported whatever labels enables
            if model.detect(part, r.sensitivity, 0, None, r.keep_emoji, r.whole_words, r.strict_short_names, r._lists):
                return True
        return False

    def kept(value: str, id_slot: bool, key: bool) -> bool:
        """value is kept as written, when nothing is found in it: an id (with ids), or a key that is a field name or
        names an id."""
        shaped = (ids and _id_shape(value, id_slot)) or (key and _field_name(value))
        return shaped and not found(value)

    def text(value: str, id_slot: bool = False) -> str:
        if not value or kept(value, id_slot, False):
            return value
        return get().redact(value, mode=mode).text

    def key_text(value: str) -> str:
        """A str key (a plain str) masked, or kept when it is a field name, names an id or is an id with a prefix of its
        own. A key never stands where the trace names an id: a UUID as a key is read, also under an id key (gate 8 of
        1.7.1, item 6). The result is kept per key text and detector settings, the most recent _KEYS_KEPT of them, read
        at one snapshot of the settings (a redactor of another kind is read every time: its settings are not known)."""
        if not value:
            return value
        r = get()
        cls = type(r)
        # this package's own Redactor, whose detect and redact are its own: its settings are known (a static look at the
        # class, so a settings property that fails is not taken for another kind of redactor: it fails the value)
        own = (issubclass(cls, Redactor) and _declared(cls, "_settings") and cls.detect is Redactor.detect
               and cls.redact is Redactor.redact)
        settings = r._settings if own else None
        if not callable(settings):
            return value if kept(value, False, True) else r.redact(value, mode=mode).text
        snapshot = settings()
        at = (snapshot, value)
        with lock:
            out = known.get(at)
            if out is not None:
                known.move_to_end(at)
                return out
        out = value if kept(value, False, True) else apply_redaction(value, r._detect_at(value, snapshot), mode)
        with lock:
            known[at] = out
            if len(known) > _KEYS_KEPT:
                known.popitem(last=False)
        return out

    def leaf(value: Any, id_slot: bool) -> Any:
        """A value that holds no other value, masked or kept; _OPEN for one that holds others. id_slot: the value stands
        where the trace names an id (a UUID there is kept). A value is what its own class is (type(value)), not what its
        __class__ says: isinstance reads __class__, takes a read that raises AttributeError for none, and believes a
        class it names (an object that says it is a uuid.UUID would be kept, and Langfuse writes its str())."""
        cls = type(value)
        if issubclass(cls, Enum):  # before str: str() of a str-valued member is its name, not its value
            return _OPEN
        # What is returned is of Python's own types (a str, bytes, a kept type itself), never of the value's class:
        # Langfuse writes an object of another class through that class's code (str() of a class whose name holds
        # "Streaming", decode() of bytes, isoformat() of a date, the attributes of an instance), which would write what
        # the mask did not read (gate 8 of 1.7.1, the confirmation's item 8).
        if issubclass(cls, str):
            return text(str.__str__(value), id_slot)
        if issubclass(cls, UserString):
            return text(str.__str__(value.data), id_slot)
        if cls in _KEPT_TYPES or _is_langfuse_media(value):
            return value
        if issubclass(cls, _KEPT):
            return _as_kept(value, cls)
        if issubclass(cls, array.array):  # read as text only when it holds characters
            if _ARRAY_TYPECODE(value) in ("u", "w"):
                return text(array.array.tounicode(value), id_slot)
            return _placeholder(value)
        if issubclass(cls, memoryview):
            value = value.tobytes()
            cls = bytes
        if issubclass(cls, (bytes, bytearray)):
            raw = bytes(value)  # (read outside the try: a read that fails fails the value, it is not "not UTF-8")
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                return _placeholder(value)
            return (bytearray if issubclass(cls, bytearray) else bytes)(text(decoded, id_slot).encode("utf-8"))
        return _OPEN

    def each(values: Any, id_slot: bool) -> Generator[Any, Any, List[Any]]:
        """Yields each item (with the place it stands in) and returns the list of what they became."""
        out = []
        for v in values:
            out.append((yield v, id_slot))
        return out

    def inside(value: Any, id_slot: bool) -> Generator[Any, Any, Any]:
        """Walks a value that holds others: yields each value in it with its place (whether the trace names an id
        there), is sent what that value became, returns the whole. The items of a list, tuple or set stand where the
        list stands, as do the value of an Enum member and what an object's dump returns; the value of a dict key stands
        where its own key names it, and a key never stands where the trace names an id. Kinds are told by the value's
        own class, as in leaf."""
        cls = type(value)
        if issubclass(cls, Enum):
            return (yield value.value, id_slot)
        if issubclass(cls, Mapping):
            out: Dict[Any, Any] = {}
            following: Dict[str, int] = {}  # the number to try next for each key (linear when many keys collide)
            for k, v in value.items():
                # a str key, a subclass (a str Enum) too, is the text it holds: the key kept or masked and the place
                # its value stands are read from that same text
                name = str.__str__(k) if issubclass(type(k), str) else None
                if not keys:
                    key = k
                elif name is not None:
                    key = key_text(name)
                else:  # a key of another type is read as a value is, never where the trace names an id
                    key = yield k, False
                key = _hashable(key)
                if key in out:  # two keys that mask to the same text keep both values
                    base = str.__str__(key) if issubclass(type(key), str) else _written(key)
                    n = following.get(base, 2)
                    while f"{base} #{n}" in out:
                        n += 1
                    following[base] = n + 1
                    key = f"{base} #{n}"
                out[key] = yield v, name is not None and _is_id_key(name)
            return out
        if cls is list:
            return (yield from each(value, id_slot))
        if cls in (set, frozenset):
            return cls([_hashable(v) for v in (yield from each(value, id_slot))])
        if issubclass(cls, tuple):  # a named tuple as a tuple too (Langfuse writes either as a list)
            return tuple((yield from each(value, id_slot)))
        # other containers of whole values; a generic Sequence (array, a str-like class) may yield single characters
        if issubclass(cls, (list, set, frozenset, deque, range, MappingView)):
            return (yield from each(value, id_slot))
        if issubclass(cls, type):
            return _placeholder(value)
        # A dataclass is read as its fields, any other object with a dump as what the dump returns, each found in the
        # class (_declared) and read unguarded. Fields that cannot be read (a field that is not set, a property, a dump
        # or a dump property that raises, AttributeError too) are a container that cannot be read: the error reaches
        # mask() and the whole value becomes [MASK FAILED], its other parts too (gate 8 of 1.7.1, item 8 and its
        # confirmation).
        if _declared(cls, "__dataclass_fields__"):
            fields = {f.name: getattr(value, f.name) for f in dataclasses.fields(cls)}
        else:
            dump = _serializer(value)  # pydantic v2, then v1
            if dump is None:
                return _placeholder(value)
            fields = dump()
        return (yield fields, id_slot)

    def walk(data: Any) -> Any:
        """data masked. A value that holds others is walked by a generator on a stack of this function's own, not by a
        Python call per level, so nesting of any depth is walked, as Langfuse's own serializer writes it."""
        path: Set[int] = set()  # the containers open on the way down, to tell a cycle
        stack: List[Tuple[Generator[Any, Any, Any], int]] = []
        value, id_slot, result, pending = data, False, None, True
        while True:
            if pending:  # value is still to be walked
                result = leaf(value, id_slot)
                if result is _OPEN:
                    if id(value) in path:  # a container met again on the way down: a cycle
                        result = "[CYCLE]"
                    else:
                        path.add(id(value))
                        stack.append((inside(value, id_slot), id(value)))
                        result = None  # what starts a generator
            if not stack:
                return result
            walker, at = stack[-1]
            try:
                (value, id_slot), pending = walker.send(result), True
            except StopIteration as done:
                stack.pop()
                path.discard(at)
                result, pending = done.value, False

    def mask(*, data: Any, **kwargs: Any) -> Any:
        # One failure anywhere replaces the whole value, never only the part that failed: a key or a value read before
        # the failure is not sent either. Langfuse's own fallback for an exception keeps every metadata key and logs
        # the exception's message, so the exception does not leave this function. KeyboardInterrupt and SystemExit do
        # (BaseException): Langfuse does not catch them, so they reach the application and the value is not written.
        try:
            return walk(data)
        except Exception as e:  # noqa: BLE001 - every failure, the detector's included
            _log.error("masking failed (%s); the whole value is sent as %s", type(e).__name__, _FAILED)
            return _FAILED

    return mask
