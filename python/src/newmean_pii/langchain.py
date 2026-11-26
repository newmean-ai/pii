"""LangChain: the newmean-pii models as a detector for ``PIIMiddleware`` (langchain >= 1.4.0).

    from langchain.agents import create_agent
    from newmean_pii.langchain import pii_middleware

    agent = create_agent("openai:gpt-6.1", middleware=[pii_middleware()])
    # "Call Kim Minsu on 010-1234-5678" reaches the model as "Call [REDACTED_PERSON] on [REDACTED_PHONE]"

``pii_detector`` needs no LangChain import: it returns plain dicts in the shape of LangChain's ``PIIMatch``
(``type``, ``value``, ``start``, ``end``), so it also fits any code that takes such a detector.

With a vault (since 1.7.2) the model gets numbered placeholders and the values come back in its answer, and in the
arguments of the tools named in ``restore_tools`` (other tools get the placeholders):

    from newmean_pii import Vault

    vault = Vault()  # one per user or conversation, never shared between users
    agent = create_agent("openai:gpt-6.1", tools=[...], middleware=[pii_middleware(redactor, vault=vault, restore_tools=["lookup_customer"])])
    # the model sees "Call [PERSON_1] on [PHONE_1]"; the answer and lookup_customer get "Kim Minsu" and the number back
"""
from __future__ import annotations

import collections.abc
import dataclasses
import datetime
import json
import math
import pathlib
import re
import threading
import unicodedata
import uuid
from collections import OrderedDict
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union

from ._redactor import Redactor, create_redactor, model_values, redact_readings
from ._types import Span
from ._vault import (
    Vault,
    escaped_readings,
    json_document,
    json_escapes_view,
    plain_number,
    json_string_values,
    json_tokens,
    quote_numbers,
    replace_json_strings,
    vault_tags,
)
from ._vault import _TAG as _VAULT_TAG

__all__ = ["mask_documents", "pii_detector", "pii_middleware"]

Detector = Callable[[str], List[Dict[str, Any]]]


def pii_detector(redactor: Optional[Redactor] = None, *, labels: Optional[Iterable[str]] = None, **options: Any) -> Detector:
    """A detector ``(text) -> [{"type", "value", "start", "end"}]`` for every label in one model call.

    ``type`` is the label in lower case ("person", "phone", "address", ...), which LangChain's "redact" strategy shows
    as ``[REDACTED_PERSON]``. Pass a ``redactor`` you already have, or ``options`` for ``create_redactor`` (size,
    sensitivity, allow, block, model_dir, ...); without a redactor the model is loaded on the first call, not here.
    ``labels`` limits the labels reported (with your own redactor too).
    """
    if redactor is not None and options:
        raise TypeError("pass either a redactor or create_redactor options, not both")
    wanted = None if labels is None else frozenset(labels)
    lock = threading.Lock()
    holder: List[Redactor] = [] if redactor is None else [redactor]

    def get() -> Redactor:
        if not holder:
            with lock:
                if not holder:
                    holder.append(create_redactor(labels=wanted, **options))
        return holder[0]

    def detect(text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        return [
            {"type": s.label.lower(), "value": text[s.start : s.end], "start": s.start, "end": s.end}
            for s in get().detect(text)
            if wanted is None or s.label in wanted
        ]

    return detect


def pii_middleware(
    redactor: Optional[Redactor] = None,
    *,
    strategy: str = "redact",
    apply_to_input: bool = True,
    apply_to_output: bool = False,
    apply_to_tool_results: bool = False,
    labels: Optional[Iterable[str]] = None,
    name: str = "pii",
    vault: Optional[Vault] = None,
    restore_tools: Optional[Union[str, Iterable[str], Callable[[str], bool]]] = None,
    restore_reasoning: bool = False,
    **options: Any,
) -> Any:
    """A ``PIIMiddleware`` that masks everything the model finds: one middleware and one model call per message.

    ``strategy`` and the ``apply_to_*`` flags are LangChain's ("redact", "mask", "hash" or "block"; input only by
    default). The other arguments are those of ``pii_detector``. ``name`` is the middleware's ``pii_type``, used in its
    name and in the error of the "block" strategy; give each instance its own when an agent has several.

    With ``vault`` (since 1.7.2) it is a middleware of this package instead: it wraps each model call, the model gets
    masked copies with numbered placeholders of user messages, earlier answers (their reasoning and the known values in
    their tool-call arguments) and tool results, and the answer comes back with this call's placeholders restored in its
    text. Tool-call arguments are restored only for the tools in ``restore_tools`` (names, or a test on the name;
    default none), because a prompt injection in text the model reads can make it put placeholders into any tool call.
    ``restore_reasoning=True`` also restores reasoning blocks. The agent's state keeps the messages as written. A vault
    numbers every value and restores the answer, so it takes only ``strategy="redact"``, ``apply_to_input=True`` and
    ``apply_to_output=False``; any other value raises (since 1.7.2 also ``apply_to_input=False``). With a vault, tool
    results are masked whatever ``apply_to_tool_results`` says.
    """
    if vault is not None:
        if strategy != "redact" or apply_to_output or not apply_to_input:
            raise ValueError('a vault numbers the values and restores the answer: use strategy="redact", apply_to_input=True and apply_to_output=False')
        return _vault_middleware(redactor, vault, labels, name, restore_tools, restore_reasoning, options)
    if restore_tools is not None or restore_reasoning:
        raise ValueError("restore_tools and restore_reasoning need a vault")
    try:
        from langchain.agents.middleware import PIIMiddleware
    except ImportError as e:  # pragma: no cover
        raise ImportError("pii_middleware needs langchain >= 1.4.0 (pip install 'newmean-pii[langchain]')") from e
    return PIIMiddleware(
        name,
        detector=pii_detector(redactor, labels=labels, **options),
        strategy=strategy,  # type: ignore[arg-type]
        apply_to_input=apply_to_input,
        apply_to_output=apply_to_output,
        apply_to_tool_results=apply_to_tool_results,
    )


def mask_documents(
    documents: Iterable[Any],
    redactor: Optional[Redactor] = None,
    *,
    mode: str = "tag",
    vault: Optional[Vault] = None,
    **options: Any,
) -> List[Any]:
    """Masked copies of retrieved documents (LangChain ``Document``s, or anything with ``page_content``), for a retriever
    step or before documents go into a prompt: ``page_content`` is masked as ``redact`` does, ``metadata`` is kept.

        docs = mask_documents(retriever.invoke(question), redactor)
        chain = retriever | RunnableLambda(lambda docs: mask_documents(docs, redactor)) | ...

    Pass a ``vault`` to number the placeholders, for chains that restore with ``vault.restore`` themselves (``mode`` can
    then only be "tag": another mode raises, since 1.7.2). In an agent
    with the vault middleware, return the documents unmasked from the tool and let the middleware mask them: text that
    reaches the middleware already masked has its placeholders escaped ("［EMAIL_1]") and they are not restored.
    Without a redactor one is created from ``options`` (size, sensitivity, allow, block, model_dir, ...).
    Since 1.7.2.
    """
    if redactor is not None and options:
        raise TypeError("pass either a redactor or create_redactor options, not both")
    r = redactor if redactor is not None else create_redactor(**options)
    # one batch, one vault generation: a vault cleared between two documents fails the batch (RuntimeError) instead of
    # giving the same placeholder to values of different documents
    started = None if vault is None else vault.generation
    out = []
    for d in documents:
        text = getattr(d, "page_content", None)
        if not isinstance(text, str) or not text:
            out.append(d)
            continue
        if vault is None:
            masked = r.redact(text, mode).text
        else:
            if mode != "tag":
                # a vault numbers every value so that it can be restored: another mode cannot be honoured (as in npm)
                raise ValueError(f'a vault numbers the placeholders: mode can only be "tag" with it, not {mode!r}')
            view = json_escapes_view(text)  # the model reads the text through its Unicode escapes, when it has any
            if view is None:
                masked = redact_readings(text, r.detect(text), vault, generation=started)[0]
            else:
                masked = redact_readings(text, [], vault, through=(view, r.detect(view[0])), generation=started)[0]
        copy = getattr(d, "model_copy", None)
        out.append(copy(update={"page_content": masked}) if callable(copy) else type(d)(page_content=masked, metadata=dict(getattr(d, "metadata", {}) or {})))
    if vault is not None:
        vault._unchanged(started)  # also when the documents themselves (an iterator) cleared the vault after the last one
    return out


_CACHE_MAX = 256


# reasoning fields of content blocks (LangChain "reasoning", Anthropic "thinking"); every block's "text" is masked too
_REASONING_FIELDS = {"reasoning": "reasoning", "thinking": "thinking"}
# tool traffic inside content blocks: what a model wrote for a tool, and what came back
_INPUT_FIELDS = ("input", "args", "arguments")
_RESULT_FIELDS = ("content", "output")
# The last pass tells the objects of a framework (a content block, a tool call) from the data inside them. In a
# framework object these fields are not text for a model to read: identifiers, names a framework or provider matches,
# references to files, provider metadata (signatures live there).
_NOT_TEXT = frozenset((
    "type", "role", "id", "name", "index", "tool_call_id", "tool_use_id", "call_id", "mime_type", "media_type", "url",
    "image_url", "file_id", "reference", "signature", "encrypted_content", "response_metadata", "usage_metadata",
))
_SIGNED = frozenset(("reasoning", "thinking", "redacted_thinking"))
# The types of LangChain's content blocks and of the provider blocks it passes on (standard blocks, the block types a
# tool message takes, Anthropic, OpenAI chat and Responses, Google), and of the shapes inside them (sources, citations,
# tool calls). In what came from outside (a tool result) an object with another type is a row of data ({"type":
# "contact", ...}); in the last pass every field of such an object but its type and provider options is data. The same
# list is in js/src/masker.ts (LANGCHAIN_TYPES).
_LANGCHAIN_TYPES = frozenset((
    # standard blocks (langchain-core KNOWN_BLOCK_TYPES) and the block types a tool message takes
    "text", "reasoning", "refusal", "tool_call", "tool_call_chunk", "invalid_tool_call", "server_tool_call",
    "server_tool_call_chunk", "server_tool_call_result", "server_tool_result", "image", "video", "audio", "text-plain",
    "file", "non_standard", "citation", "non_standard_annotation", "image_url", "json", "search_result",
    "custom_tool_call_output", "document",
    # Anthropic
    "thinking", "redacted_thinking", "tool_use", "tool_result", "server_tool_use", "web_search_tool_result",
    "web_fetch_tool_result", "code_execution_tool_result", "bash_code_execution_tool_result",
    "text_editor_code_execution_tool_result", "tool_search_tool_result", "mcp_tool_use", "mcp_tool_result",
    "container_upload", "web_search_result", "char_location", "page_location", "content_block_location",
    "web_search_result_location", "search_result_location", "base64", "url", "content", "ephemeral",
    # OpenAI chat and Responses
    "input_text", "output_text", "input_image", "input_file", "input_audio", "function", "function_call",
    "function_call_output", "custom_tool_call", "computer_call", "computer_call_output", "web_search_call",
    "file_search_call", "code_interpreter_call", "code_interpreter_output", "image_generation_call", "mcp_call",
    "mcp_list_tools", "mcp_approval_request", "mcp_approval_response", "local_shell_call", "local_shell_call_output",
    "url_citation", "file_citation", "container_file_citation", "file_path", "summary_text", "logs", "message",
    "item_reference",
    # Google
    "media", "executable_code", "code_execution_result",
))
# the fields of an object of a type the framework does not have that are not data: its type and provider options
_OPTIONS = frozenset(("type", "providerOptions", "providerMetadata"))
# The names of the fields of the framework's objects. A key of a framework's object that is not one of them is data (a
# tool's row that has a type of a framework, {"type": "text", "Kim Minsu": "open"}): known values are hidden in it.
_FRAME_KEYS = _NOT_TEXT | _OPTIONS | frozenset((
    "text", "data", "value", "json", "input", "args", "arguments", "output", "outputs", "content", "source", "reasoning",
    "thinking", "summary", "refusal", "title", "context", "citations", "annotations", "cache_control", "extras", "kind",
    "filename", "status", "error", "is_error", "isError", "function", "function_call", "tool_calls", "invalid_tool_calls",
    "approved", "reason", "providerExecuted", "sourceType", "server_label", "partial_json", "format", "detail", "code",
    "language", "logs", "base64", "source_type", "file_data", "input_audio", "cited_text", "start_index", "end_index",
    "page_age", "encrypted_index",
))


# In what came from outside (a tool's result, a user's message), the block types that may stand there and the fields
# that hold each one's own content (since 1.7.2, the review of 1.7.1: a storage API's rows typed "file" or "document"
# were taken for blocks, so the model read none of their fields and their id and url went out as written). An object of
# one of these types is a block only when one of its fields here is set; any other typed object there is a row of data.
# The block types a tool message takes (langchain-core TOOL_MESSAGE_BLOCK_TYPES), the standard and LangChain v0 data
# blocks (url, base64, file_id; source_type with data, url or id), Anthropic (a document's or an image's source, a
# tool_result's tool_use_id, the sources base64, url, text and content, a file source's file_id), OpenAI chat and
# Responses (image_url, file, input_*, outputs, a message's role) and Google (media). The same table is in
# js/src/masker.ts (LANGCHAIN_CONTENT_FIELDS).
_FILE_CONTENT = ("url", "base64", "file_id", "data", "source_type", "source", "file")
_CONTENT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "text": ("text", "data"),
    "text-plain": ("text", "url", "base64", "file_id"),
    "image": _FILE_CONTENT,
    "audio": _FILE_CONTENT,
    "video": _FILE_CONTENT,
    "file": _FILE_CONTENT,
    "image_url": ("image_url",),
    "document": ("source",),
    "json": ("json",),
    "search_result": ("content",),
    "custom_tool_call_output": ("output",),
    "function_call_output": ("output",),
    "computer_call_output": ("output",),
    "tool_result": ("tool_use_id",),
    "mcp_tool_result": ("tool_use_id",),
    "input_text": ("text",),
    "input_image": ("image_url", "file_id"),
    "input_file": ("file_id", "file_data", "file_url"),
    "input_audio": ("input_audio",),
    "media": ("data", "file_uri", "url"),
    "non_standard": ("value",),
    "container_upload": ("file_id",),
    "message": ("role",),
    "base64": ("data",),
    "url": ("url",),
    "content": ("content",),
}


def _carries(v: Dict[Any, Any]) -> bool:
    """Whether v (a typed object in what came from outside) is a block of LangChain or a provider: its type is one such
    a place may hold, and it has that type's own content. Otherwise it is a row of data, whatever type it gives itself."""
    fields = _CONTENT_FIELDS.get(v.get("type"))  # type: ignore[arg-type]
    return fields is not None and any(v.get(f) is not None for f in fields)


class _Slot:
    """A place where LangChain's objects stand: a message's content, the field of a tool's result that holds its
    output, a document's source, a message's tool calls. types: the types of the framework's objects that may stand
    there (None: any of _LANGCHAIN_TYPES); an object of another type there is not the framework's (every field but its
    type and provider options is data). untyped: whether an object without a type there is the framework's (a tool
    call, a function); otherwise it is data. received: the slot is in what came from outside, "tool" (a tool's result)
    or "user" (a user's message), where an object is the framework's only when it carries its type's own content
    (_carries); any other typed object there is a row of data ({"type": "file", "id", "name", "owned_by"} from a storage
    API). The slots inside such an object are too. In a tool's result a block's id is data too (no block there is
    matched by its id); in a user's message it is LangChain's identifier of the block."""

    __slots__ = ("types", "untyped", "received")

    def __init__(self, types: Optional[frozenset] = None, untyped: bool = False, received: Union[bool, str] = False) -> None:
        self.types = types
        self.untyped = untyped
        self.received = received


# Which objects are LangChain's is chosen from the object and the field they stand in, never from the "type" an object
# gives itself (since 1.7.2, the 23rd outside review): a tool's JSON payload is data at every depth, whatever types its
# objects have ({"type": "json", "data": {"type": "text", "name": "Kim Minsu"}}). The same in js/src/masker.ts
# (LANGCHAIN_SHAPES).
_BLOCKS = _Slot()
_SOURCES = _Slot(frozenset(("base64", "url", "text", "content", "file")))
# the content of a message that came from outside (a user's message, a tool's result), and the slots inside it
_RECEIVED = _Slot(received="user")
_RECEIVED_TOOL = _Slot(received="tool")
_RECEIVED_SOURCES = {r: _Slot(_SOURCES.types, received=r) for r in ("user", "tool")}
_FUNCTION = _Slot(frozenset(), True)
_TOOL_CALLS = _Slot(frozenset(("tool_call", "tool_call_chunk", "invalid_tool_call", "function")), True)
# the blocks that hold blocks in the field of a tool's result, and those that hold a document's source
_CONTENT_HOLDERS = frozenset((
    "tool_result", "mcp_tool_result", "web_search_tool_result", "web_fetch_tool_result", "code_execution_tool_result",
    "bash_code_execution_tool_result", "text_editor_code_execution_tool_result", "tool_search_tool_result",
    "search_result", "message", "content",
))
_OUTPUT_HOLDERS = frozenset(("function_call_output", "custom_tool_call_output", "computer_call_output"))
_SOURCE_HOLDERS = frozenset(("image", "document", "file", "audio", "video"))
# the types whose "name" is an identifier that the framework or the provider matches (a tool's or a function's name);
# in an object of another type, "name" is text
_NAMED = frozenset((
    "tool_call", "tool_call_chunk", "invalid_tool_call", "server_tool_call", "server_tool_call_chunk", "tool_use",
    "server_tool_use", "mcp_tool_use", "function", "function_call", "custom_tool_call", "mcp_call", "mcp_approval_request",
    "mcp_list_tools",
))
# the fields at the top of additional_kwargs that are the framework's (OpenAI tool calls); the others are data
_KWARGS = {"function_call": _FUNCTION, "tool_calls": _TOOL_CALLS}


def _slot(kind: Optional[str], field: Any, received: Union[bool, str] = False) -> Optional[_Slot]:
    """The objects that may stand in the field of a framework object of type kind (None: an object without a type), or
    None when that field holds data. received: the object stands in what came from outside ("user" or "tool"), and so
    does the slot; what a block holds in the field of a tool's result (a tool_result's content, a function call's
    output, a search result's content) is a tool's result."""
    if kind is None:
        return None
    if field == "content":
        if kind not in _CONTENT_HOLDERS:
            return None
        if not received:
            return _BLOCKS
        return _RECEIVED if received == "user" and kind in ("message", "content") else _RECEIVED_TOOL
    if field == "output":
        return ((_RECEIVED_TOOL if received else _BLOCKS) if kind in _OUTPUT_HOLDERS else None)
    if field == "source":
        return (_RECEIVED_SOURCES["tool" if received == "tool" else "user"] if received else _SOURCES) if kind in _SOURCE_HOLDERS else None
    if field == "function":
        return _FUNCTION if kind == "function" else None
    return None


def _utf8(b: Any) -> Optional[str]:
    """bytes read as UTF-8 text, or None when they are not UTF-8 (binary data: an image's bytes are not)."""
    try:
        return bytes(b).decode("utf-8")
    except UnicodeDecodeError:
        return None


# a class's own namespace and the classes it inherits from, read as the type object holds them (no code of the class runs)
_CLASS_DICT = type.__dict__["__dict__"].__get__
_CLASS_MRO = type.__dict__["__mro__"].__get__


def _declares(cls: type, name: str) -> bool:
    """cls or a class it inherits from declares name in its own namespace (a method, a property, a class attribute)."""
    return any(name in _CLASS_DICT(c) for c in _CLASS_MRO(cls))


def _is_pydantic(v: Any) -> bool:
    """Whether v is a pydantic model instance (one a provider writes with model_dump): told by its class, which has
    pydantic's model_fields and declares model_dump. Its dump is not read here, so a model_dump whose read fails (a
    property that raises AttributeError) is a dump that raises, never a model without one (since 1.7.2, gate 8 of the
    release standard, the sibling of the confirmation's item 8: such a model was written in pydantic's JSON mode)."""
    t = type(v)
    return not isinstance(v, type) and isinstance(getattr(t, "model_fields", None), dict) and _declares(t, "model_dump")


def _typed(v: Any) -> bool:
    """A typed object ({"type": "...", ...}): a content block in text a model wrote."""
    return isinstance(v, dict) and isinstance(v.get("type"), str)


def _block(v: Any) -> bool:
    """A content block of a type LangChain or a provider has (in what a model wrote: a server tool's result)."""
    return _typed(v) and v["type"] in _LANGCHAIN_TYPES


def _received_block(v: Any) -> bool:
    """A content block in what came from outside (a tool's result, a user's message), as opposed to a row of JSON data:
    a block type such a place holds, with that type's own content (a text block's text, a file's url, base64 data or
    file id, a document's source). Rows with a type of their own ({"type": "contact", ...}) and rows typed like a block
    without its content (a storage API's {"type": "file", "id", "name", "owned_by"}) are data."""
    return _typed(v) and _carries(v)


def _is_data(v: Any) -> bool:
    """Whether v holds data to read: a dict or a list (the message is plain JSON)."""
    return isinstance(v, (dict, list, tuple))


def _same(a: Any, b: Any) -> bool:
    """Whether masking left a as it was (b): the same object, or an equal one of the same type."""
    if a is b:
        return True
    try:
        return type(a) is type(b) and bool(a == b)
    except Exception:  # (a type whose == does not give a truth value)
        return False


# The boundary (since 1.7.2, gate 4 of the release standard): every data slot of a request (a message's content, its
# tool calls' arguments, additional_kwargs, model settings) is built once into a plain JSON tree in the form a provider
# client writes it, and that tree is masked and sent. A value read once is never read again: a pydantic model is dumped
# once (a second dump can differ), so what the adapter writes is what was masked. A value JSON has no form for (an object
# that refers to itself, a serializer that raises, a key JSON cannot write, an object with no JSON form) fails the call
# before anything is sent.


class _NotJson(Exception):
    """A value of a data slot that cannot be written as JSON: what it is."""


# the blocks whose file data may be bytes (LangChain's file blocks; Google's media), and the fields that hold it there
# and in a block's source (Anthropic, Bedrock): bytes stay bytes there, the adapter writes them
_FILE_DATA_TYPES = frozenset(("image", "file", "audio", "video", "media"))
_BINARY_FIELDS = frozenset(("data", "base64", "bytes"))


def _json_key(k: Any) -> str:
    """A key as json.dumps writes it (what the OpenAI and Anthropic clients send): a str as it is, a number as its
    digits, true, false and null; any other key cannot be written."""
    if isinstance(k, str):
        return str.__str__(k)  # (a str Enum or another str subclass: its text)
    if k is None:
        return "null"
    if isinstance(k, bool):
        return "true" if k else "false"
    if isinstance(k, int):
        return int.__repr__(k)
    if isinstance(k, float):
        if math.isfinite(k):
            return float.__repr__(k)
        return "NaN" if k != k else ("Infinity" if k > 0 else "-Infinity")
    raise _NotJson(f"a key of type {type(k).__name__}")  # (never the value: an error can reach a trace)


def _json_decimal(d: Decimal) -> Any:
    """A Decimal as a JSON number: an integer, a float that is exactly it, or else its digits as a string; null when it
    is not finite."""
    if not d.is_finite():
        return None
    if d == d.to_integral_value():
        return int(d)
    f = float(d)
    return f if Decimal(repr(f)) == d else str(d)


def _plain(value: Any, strict: bool, on: Set[int]) -> Any:
    """value as a provider client writes it in a JSON body: str, finite int and float, bool, None, list and dict with
    str keys. A float that is not finite is null (as JSON.stringify writes it; the clients refuse it); a tuple, set,
    frozenset or other sequence is a list; a str, int or float subclass its value; an Enum its value; a Decimal a number;
    a date, datetime or time its ISO form; bytes their UTF-8 text; a pydantic model its JSON dump, made once, as the
    OpenAI and Anthropic clients make it (model_dump(mode="json", by_alias=True, exclude_unset=True)); a dataclass its
    fields, every one of them; any other object the form pydantic's JSON mode gives it (a UUID, a path, a timedelta).
    The same object when it is plain JSON already. strict=False (a provider's own settings): a class and an object with
    no JSON form (an httpx.Timeout) stay as they are; strict (data): they raise _NotJson, as a cycle (on: the containers
    being walked), a serializer that raises and a dataclass field that cannot be read do (in either mode)."""
    t = type(value)
    if t is str or t is int or t is bool or value is None:
        return value
    if t is float:
        return value if math.isfinite(value) else None
    if t is dict:
        return _plain_dict(value, strict, on)
    if t is list:
        return _plain_list(value, strict, on)
    return _plain_other(value, strict, on)


def _plain_list(value: List[Any], strict: bool, on: Set[int], item: Optional[Callable[[Any], Any]] = None) -> Any:
    if id(value) in on:
        raise _NotJson("a list that holds itself")
    on.add(id(value))
    try:
        out: Optional[List[Any]] = None
        for i, x in enumerate(value):
            y = _plain(x, strict, on) if item is None else item(x)
            if out is None and y is not x:
                out = list(value[:i])
            if out is not None:
                out.append(y)
        return value if out is None else out
    finally:
        on.discard(id(value))


def _plain_dict(value: Dict[Any, Any], strict: bool, on: Set[int], field: Optional[Callable[[Any, Any], Any]] = None) -> Any:
    if id(value) in on:
        raise _NotJson("a dict that holds itself")
    on.add(id(value))
    try:
        out: Optional[Dict[str, Any]] = None
        for i, (k, x) in enumerate(value.items()):
            key = k if type(k) is str else _json_key(k)
            y = _plain(x, strict, on) if field is None else field(k, x)
            if out is None and (key is not k or y is not x):
                out = {}
                for j, (k0, x0) in enumerate(value.items()):
                    if j == i:
                        break
                    out[k0] = x0
            if out is not None:
                if key in out:
                    # two keys JSON writes the same (4155550123 and "4155550123"): both fields stay, the later one as
                    # "4155550123 #2" (as two keys that masking makes the same do)
                    n = 2
                    while f"{key} #{n}" in out or f"{key} #{n}" in value:
                        n += 1
                    key = f"{key} #{n}"
                out[key] = y
        return value if out is None else out
    finally:
        on.discard(id(value))


def _plain_other(value: Any, strict: bool, on: Set[int]) -> Any:
    if isinstance(value, Enum):
        return _plain(value.value, strict, on)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, Decimal):
        return _json_decimal(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = _utf8(value)
        if text is None:
            raise _NotJson("bytes that are not UTF-8 text")
        return text
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, pathlib.PurePath)):
        return str(value)
    if isinstance(value, type) or callable(value) and not _is_pydantic(value) and not dataclasses.is_dataclass(value):
        if strict:
            raise _NotJson(f"a {'class' if isinstance(value, type) else 'function'} ({getattr(value, '__name__', type(value).__name__)})")
        return value
    if id(value) in on:
        raise _NotJson(f"a {type(value).__name__} that refers to itself")
    if isinstance(value, collections.abc.Mapping):
        return _plain_dict(dict(value.items()), strict, on)
    if isinstance(value, (tuple, set, frozenset, list, collections.abc.Sequence, collections.abc.Set)):
        on.add(id(value))
        try:
            return [_plain(x, strict, on) for x in value]
        finally:
            on.discard(id(value))
    if _is_pydantic(value):
        try:
            dump = value.model_dump(mode="json", by_alias=True, exclude_unset=True, warnings=False, exclude=getattr(value, "__api_exclude__", None))
        except Exception as e:
            raise _NotJson(f"a pydantic model {type(value).__name__} whose dump raises {type(e).__name__}") from None
        on.add(id(value))
        try:
            return _plain(dump, True, on)
        finally:
            on.discard(id(value))
    if dataclasses.is_dataclass(value):
        on.add(id(value))
        try:
            out = {}
            for f in dataclasses.fields(value):
                # every field, read as it is: a field that cannot be read (not set, a property that raises, AttributeError
                # too) fails the call, never a field left out (since 1.7.2, the sibling of gate 8's item 8)
                try:
                    field = getattr(value, f.name)
                except Exception as e:
                    raise _NotJson(f"a dataclass {type(value).__name__} whose field {f.name} cannot be read "
                                   f"({type(e).__name__})") from None
                out[f.name] = _plain(field, strict, on)
            return out
        finally:
            on.discard(id(value))
    if not strict:
        return value
    try:
        from pydantic_core import to_jsonable_python

        form = to_jsonable_python(value, by_alias=True, inf_nan_mode="null")
    except Exception:
        raise _NotJson(f"an object of type {type(value).__name__}, which has no JSON form") from None
    return _plain(form, True, on)


def _plain_content(content: Any, on: Set[int]) -> Any:
    """A message's content as plain JSON (see _plain), where the file data of a block may stay bytes (LangChain's file
    blocks, Google's media, a block's source: the adapter writes them); any other value is data (strict)."""
    if type(content) is str:
        return content
    if isinstance(content, (list, tuple)):
        items = content if type(content) is list else list(content)
        return _plain_list(items, True, on, lambda b: _plain_block(b, on))
    return _plain(content, True, on)


def _plain_block(b: Any, on: Set[int]) -> Any:
    if type(b) is not dict or not isinstance(b.get("type"), str):
        return _plain(b, True, on)
    kind = b["type"]

    def field(k: Any, x: Any) -> Any:
        if k in _BINARY_FIELDS and kind in _FILE_DATA_TYPES and isinstance(x, (bytes, bytearray)):
            return x
        if k == "source" and type(x) is dict:
            return _plain_dict(x, True, on, lambda k2, y: y if k2 in _BINARY_FIELDS and isinstance(y, (bytes, bytearray)) else _plain(y, True, on))
        if k in ("content", "output") and isinstance(x, (list, tuple)) and (kind in _CONTENT_HOLDERS or kind in _OUTPUT_HOLDERS):
            return _plain_content(x, on)
        return _plain(x, True, on)

    return _plain_dict(b, True, on, field)


# a string that is binary data and not text: a data URL, or base64 of some length, in one piece or in lines (one
# character class and a bounded tail: the time grows with the length, whatever the string holds)
_DATA_URL = re.compile(r"data:[^,]*;base64,")
_BASE64 = re.compile(r"[A-Za-z0-9+/_\-\r\n]+={0,2}")


def _binary(s: str) -> bool:
    return bool(_DATA_URL.match(s)) or (len(s) >= 64 and _BASE64.fullmatch(s.rstrip()) is not None)


# Fields of a request outside the messages (since 1.7.2). In a JSON schema the model reads descriptions, titles,
# comments and examples as text. Property names name the fields of a tool's input or of the answer, and literal
# constraints (const, enum, default, pattern) say what a value must be, also where a literal is an object with keys a
# schema has (const: {"description": ...}): both stay as written. The walk goes on only through the keywords that hold
# schemas (the 23rd outside review: it went through every object, and rewrote such a literal). The same lists are in
# js/src/masker.ts.
_SCHEMA_TEXT = frozenset(("description", "title", "$comment"))
_SCHEMA_EXAMPLES = frozenset(("examples", "example"))
# keywords whose value maps names to schemas (dependencies maps a name to a schema or to a list of property names)
_SCHEMA_MAPS = frozenset(("properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"))
# keywords whose value is a schema or a list of schemas
_SCHEMA_SUBSCHEMAS = frozenset((
    "items", "prefixItems", "additionalItems", "unevaluatedItems", "contains", "additionalProperties", "unevaluatedProperties",
    "propertyNames", "if", "then", "else", "not", "allOf", "anyOf", "oneOf", "contentSchema",
))
# Model settings and the model's own request fields: what the provider receives outside the messages is data. Every
# string in them, every number that is a known value and every key counts (a "user", a "safety_identifier", a template
# variable, a metadata key), except at the exact paths where a provider defines an identifier, signed or encrypted data,
# a discriminator, a credential, the name of a tool, model or provider it matches, a numeric setting or a JSON schema
# (_SETTINGS_PATHS below; since 1.7.2, the Python sibling of release gate 8, item 5: a name such as
# previous_response_id was kept at any depth inside a field a provider defines, and everything below container,
# conversation, provider and models was kept). The README lists the same paths. js/src/masker.ts has the table of
# LangChain.js (OPTION_PATHS.settings); this one is exact in two more ways: "[]" takes only the items of a list and "*"
# only the keys of an object, and no path keeps everything below it (OpenRouter's provider keeps the names of the
# providers it routes to, and tool_choice its types and names).
# The names of a provider's identifiers, signed and encrypted data, discriminators, credentials and token ids (snake case
# in the request bodies model settings are written into, camel case in some providers' fields): each holds only at the
# paths of _SETTINGS_PATHS that name it, and is data anywhere else (the tests plant every one of them elsewhere).
_OPTION_KEPT = frozenset((
    # signed and encrypted data (Anthropic, Amazon Bedrock, Google, OpenAI)
    "signature", "thoughtSignature", "thought_signature", "redactedData", "redacted_data", "reasoningEncryptedContent",
    "encryptedContent", "encrypted_content",
    # ids of a provider's objects: responses, items, conversations, containers, cached content, files, vector stores,
    # skills, connectors and MCP servers
    "previousResponseId", "previous_response_id", "itemId", "item_id", "conversationId", "conversation_id", "containerId",
    "container_id", "cachedContent", "cached_content", "fileId", "file_id", "fileIds", "file_ids", "vectorStoreIds",
    "vector_store_ids", "skillId", "skill_id", "connectorId", "connector_id", "serverLabel", "server_label", "serverUrl",
    "server_url",
    # discriminators, and the tools and domains a provider matches by name
    "type", "toolChoice", "tool_choice", "allowedTools", "allowed_tools", "allowedDomains", "allowed_domains", "blockedDomains",
    "blocked_domains",
    # credentials (as a call's headers)
    "authorization", "authorizationToken", "authorization_token", "apiKey", "api_key", "headers", "extraHeaders", "extra_headers",
    # token ids and their biases
    "logitBias", "logit_bias",
))
# The numbers a provider defines as settings (token limits, sampling, thinking budgets, retries): a number under one of
# these keys stays a number whatever it is (a max_tokens of 4096 is not an application's 4096, and a string there would
# make the provider refuse the call), at the top of the settings and directly under the fields that group such settings
# (_SETTINGS_PATHS), and nowhere else. Strings under them are still read. The same list is in js/src/masker.ts.
_OPTION_NUMBERS = frozenset((
    "maxTokens", "max_tokens", "maxOutputTokens", "max_output_tokens", "maxCompletionTokens", "max_completion_tokens",
    "max_tokens_to_sample", "temperature", "topP", "top_p", "topK", "top_k", "minP", "min_p", "seed", "frequencyPenalty",
    "frequency_penalty", "presencePenalty", "presence_penalty", "repetitionPenalty", "repetition_penalty", "n",
    "candidateCount", "candidate_count", "logprobs", "topLogprobs", "top_logprobs", "budgetTokens", "budget_tokens",
    "thinkingBudget", "thinking_budget", "maxToolCalls", "max_tool_calls", "maxRetries", "max_retries", "timeout",
    "dimensions", "numCtx", "num_ctx", "numPredict", "num_predict",
))
# The application's own maps (OpenAI and Anthropic metadata, Google labels, tags, Bedrock requestMetadata, the variables
# of a stored prompt): data, as everything outside the paths is, and built strictly wherever they stand (an object with
# no JSON form in one fails the call, also inside a field a provider defines).
_OPTION_DATA = frozenset(("metadata", "labels", "tags", "requestMetadata", "request_metadata", "variables"))
# The keys a JSON schema stands under (a response format, a tool's parameters): read as a schema (hide_schema) only at
# the paths of _SETTINGS_PATHS; anywhere else such a key is data like any other.
_OPTION_SCHEMAS = frozenset(("schema", "parameters", "inputSchema", "input_schema", "responseSchema", "response_schema", "responseJsonSchema", "response_json_schema"))


def _under(tops: Iterable[str], paths: Iterable[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    paths = list(paths)
    return [(t,) + p for t in tops for p in paths]


def _numbers_at(prefix: Tuple[str, ...]) -> List[Tuple[str, ...]]:
    return [prefix + (n,) for n in sorted(_OPTION_NUMBERS)]


# a gateway's routing (OpenRouter's request fields): the models to fall back to, and the providers to route to by name
_ROUTING = [("models", "[]"), ("provider", "order", "[]"), ("provider", "only", "[]"), ("provider", "ignore", "[]")]
# The exact paths, from the top of the settings (the request's fields). A step is a key, "*" any key of an object, "[]"
# any item of a list. ids: a string, number or boolean there is sent as written, and the keys along the path stay.
# numbers: a number there stays a number (a string is read). schemas: a JSON object there is read as hide_schema reads
# it (property names and literal constraints as written).
_SETTINGS_PATHS: Dict[str, List[Tuple[str, ...]]] = {
    "ids": [
        # the model, and a gateway's routing (also in extra_body, which the OpenAI client merges into the request body)
        ("model",), *_ROUTING, *_under(("extra_body",), _ROUTING),
        # OpenAI: the previous response, the conversation (an id, or {"id": ...}), a stored prompt's id and version
        ("previous_response_id",), ("conversation",), ("conversation", "id"), ("prompt", "id"), ("prompt", "version"),
        # Anthropic: the container (an id, or its id and skills), MCP servers (type, name, URL, token, the tools allowed)
        ("container",), ("container", "id"),
        *_under(("container",), [("skills", "[]", "type"), ("skills", "[]", "skill_id"), ("skills", "[]", "version")]),
        *_under(("mcp_servers",), [
            ("[]", "type"), ("[]", "name"), ("[]", "url"), ("[]", "authorization_token"), ("[]", "tool_configuration", "allowed_tools", "[]"),
        ]),
        # Google: cached content
        ("cachedContent",), ("cached_content",),
        # tools, functions and response formats: their types and names; which tool or function to call (a name, a type,
        # or the tools allowed)
        ("tools", "[]", "type"), ("tools", "[]", "name"), ("tools", "[]", "function", "name"), ("functions", "[]", "name"),
        ("response_format", "type"), ("response_format", "json_schema", "name"), ("text", "format", "type"), ("text", "format", "name"),
        ("tool_choice",), *_under(("tool_choice",), [
            ("type",), ("mode",), ("name",), ("function", "name"), ("server_label",), ("tools", "[]", "type"), ("tools", "[]", "name"),
            ("tools", "[]", "function", "name"), ("tools", "[]", "server_label"),
        ]),
        ("function_call",), ("function_call", "name"),
        # OpenAI's built-in tools given in the request: MCP servers and connectors (their credentials, the tools allowed
        # and the tools that need an approval), vector stores, containers and their files, domains
        *_under(("tools",), [
            ("[]", "server_label"), ("[]", "server_url"), ("[]", "connector_id"), ("[]", "authorization"), ("[]", "headers", "*"),
            ("[]", "allowed_tools", "[]"), ("[]", "allowed_tools", "tool_names", "[]"), ("[]", "require_approval"),
            ("[]", "require_approval", "always", "tool_names", "[]"), ("[]", "require_approval", "never", "tool_names", "[]"),
            ("[]", "vector_store_ids", "[]"), ("[]", "container"), ("[]", "container", "file_ids", "[]"),
            ("[]", "filters", "allowed_domains", "[]"), ("[]", "filters", "blocked_domains", "[]"), ("[]", "allowed_domains", "[]"),
            ("[]", "blocked_domains", "[]"),
        ]),
        # token ids and their biases; the call's headers (credentials)
        ("logit_bias", "*"), ("logitBias", "*"), ("headers", "*"), ("extra_headers", "*"),
        # message-like settings (_TEXT_SETTINGS): the types of Anthropic's system blocks and of the Responses API's items,
        # their content parts and summary parts, and reasoning sent back encrypted
        ("system", "[]", "type"), ("system", "[]", "cache_control", "type"),
        *_under(("input", "instructions"), [("[]", "type"), ("[]", "content", "[]", "type"), ("[]", "summary", "[]", "type"), ("[]", "encrypted_content")]),
    ],
    "numbers": [
        *_numbers_at(()), ("thinking", "budget_tokens"), ("thinking", "budgetTokens"), ("thinkingConfig", "thinkingBudget"),
        ("thinking_config", "thinking_budget"),
        # Google's generation config, Amazon Bedrock's inference config and the model fields it passes on, Ollama's
        # options
        *(p for top in (
            "generationConfig", "generation_config", "inferenceConfig", "inference_config", "additionalModelRequestFields",
            "additional_model_request_fields", "options",
        ) for p in _numbers_at((top,))),
        ("generationConfig", "thinkingConfig", "thinkingBudget"), ("generation_config", "thinking_config", "thinking_budget"),
        ("additionalModelRequestFields", "thinking", "budget_tokens"), ("additional_model_request_fields", "thinking", "budget_tokens"),
    ],
    "schemas": [
        ("response_format", "json_schema", "schema"), ("text", "format", "schema"), ("tools", "[]", "parameters"),
        ("tools", "[]", "input_schema"), ("tools", "[]", "function", "parameters"), ("functions", "[]", "parameters"),
        ("responseSchema",), ("response_schema",), ("responseJsonSchema",), ("response_json_schema",), ("output_format", "schema"),
        ("outputFormat", "schema"), ("output_config", "format", "schema"), ("outputConfig", "format", "schema"),
        ("generationConfig", "responseSchema"), ("generationConfig", "responseJsonSchema"), ("generation_config", "response_schema"),
        ("generation_config", "response_json_schema"),
    ],
}


class _PathNode:
    """A node of the tree of _SETTINGS_PATHS: whether a path ends here (an identifier, a numeric setting, a schema), and
    the steps that go on from here."""

    __slots__ = ("next", "id", "number", "schema")

    def __init__(self) -> None:
        self.next: Dict[Any, "_PathNode"] = {}
        self.id = self.number = self.schema = False


# The two steps of _SETTINGS_PATHS that are not keys, as the tree holds them: "[]" (an item of a list) and "*" (any key of
# an object) become these objects, which no key of an object is. So a key written "[]" or "*" in the data is matched as
# the key it is: "[]" is a list step only where the value is a list (since 1.7.2, gate 8 of the release standard, the
# confirmation's item 5: an object key "[]" was taken for a list step, and an identifier name under it kept, as in
# {"mcp_servers": {"[]": {"name": ...}}}), and "*" is any key, itself among them.
_ITEM = object()
_ANY_KEY = object()
_STEP_OF = {"[]": _ITEM, "*": _ANY_KEY}


def _path_tree(paths: Dict[str, List[Tuple[str, ...]]]) -> _PathNode:
    root = _PathNode()
    for kind, attr in (("ids", "id"), ("numbers", "number"), ("schemas", "schema")):
        for p in paths[kind]:
            n = root
            for k in p:
                k = _STEP_OF.get(k, k)
                m = n.next.get(k)
                if m is None:
                    m = n.next[k] = _PathNode()
                n = m
            setattr(n, attr, True)
    return root


_SETTINGS_TREE = _path_tree(_SETTINGS_PATHS)


def _path_step(nodes: List[_PathNode], key: Any) -> List[_PathNode]:
    """The nodes of the path tree one step further from nodes: by a key of an object (a str, as _json_key writes every
    key: that key, or any key), or by an item of a list (its index, an int: any item). A key never takes a list step,
    whatever it says ("[]"), and an index never takes a key's."""
    steps = (key, _ANY_KEY) if isinstance(key, str) else (_ITEM,)
    return [m for n in nodes for s in steps for m in (n.next.get(s),) if m is not None]


# The fields of a request body that providers define (OpenAI chat completions and Responses, Anthropic, Google, Amazon
# Bedrock, Mistral, Ollama) and that LangChain binds with the settings: in one of them, and at a path above, an object
# with no JSON form (an httpx.Timeout, a schema's class) stays as it is, for the provider client writes it or refuses it.
# A setting with any other name is an unknown provider's (since 1.7.2, gate 4 of the release standard: an application's
# "acme" options were read as a provider's), and extra_body holds the application's fields: there such an object fails
# the call. Either way every string, number and key inside is data, except at the paths above.
_SETTINGS_FIELDS = frozenset((
    # OpenAI chat completions and Responses
    "audio", "background", "context_management", "frequency_penalty", "function_call", "functions", "include", "input",
    "instructions", "logprobs", "max_completion_tokens", "max_output_tokens", "max_tokens", "modalities", "n",
    "parallel_tool_calls", "prediction", "presence_penalty", "prompt", "prompt_cache_key", "prompt_cache_retention",
    "reasoning", "reasoning_effort", "response_format", "safety_identifier", "seed", "service_tier", "stop", "store",
    "stream", "stream_options", "temperature", "text", "tools", "top_logprobs", "top_p", "truncation", "user",
    "verbosity", "web_search_options", "extra_query", "timeout",
    # Anthropic
    "betas", "cache_control", "citations", "inference_geo", "mcp_servers", "output_config", "output_format", "stop_sequences",
    "system", "thinking", "top_k", "effort",
    # Google
    "generation_config", "generationConfig", "safety_settings", "safetySettings", "tool_config", "toolConfig", "response_mime_type",
    "responseMimeType", "thinking_config", "thinkingConfig", "thinking_budget", "include_thoughts", "media_resolution",
    "response_modalities", "speech_config", "image_config", "system_instruction",
    # Amazon Bedrock
    "inferenceConfig", "inference_config", "additionalModelRequestFields", "additional_model_request_fields",
    "additionalModelResponseFieldPaths", "additional_model_response_field_paths", "guardrailConfig", "guardrail_config",
    "performanceConfig", "performance_config",
    # Mistral, Ollama, and LangChain's own (bind_tools, with_structured_output)
    "random_seed", "safe_prompt", "prompt_mode", "options", "format", "keep_alive", "think", "strict", "stream_usage",
    "ls_structured_output_format",
))
_EXTRA_BODY = frozenset(("extra_body", "extraBody"))
# Fields of a request body that hold what the model reads as messages or instructions (the system prompt, Responses input
# and instructions, Google's system instruction; "messages" is not a setting, so it is data): message-like content is
# text, so an object with no JSON form there fails the call as in data (since 1.7.2, gate 8 of the release standard),
# and only the types and the encrypted reasoning at their paths above stay as written.
_TEXT_SETTINGS = frozenset(("system", "input", "instructions", "system_instruction"))


def _mapped(x: Any, f: Callable[[Any, Any], Any]) -> Any:
    """x (a list, tuple or dict) with each item or value mapped by f(key, value); the same object when nothing changed.
    Keys stay as they are; anything else is returned as it is."""
    if isinstance(x, (list, tuple)):
        out = [f(i, y) for i, y in enumerate(x)]
        return x if all(a is b for a, b in zip(out, x)) else (out if isinstance(x, list) else tuple(out))
    if isinstance(x, dict):
        new = {k: f(k, y) for k, y in x.items()}
        return x if all(new[k] is y for k, y in x.items()) else new
    return x


def _bare_tags(name: str, hidden: str) -> str:
    """A message name with the placeholders that hiding put in written without brackets ("PERSON_1"): providers take
    only letters, digits, "_" and "-" there. Such a placeholder is never restored. (One pass over the name.)"""
    if hidden == name:
        return name
    before = set(vault_tags(name))
    return _VAULT_TAG.sub(lambda m: m.group(0) if m.group(0) in before else m.group(1), hidden)


_NAME_JOIN = re.compile(r"[_-]+")


def _name_reading(name: str) -> "Optional[tuple[str, List[int]]]":
    """A message name read with its joins as spaces (since 1.7.2, the review round's L1-A2): "_" and "-" between letters
    or digits, and a small letter followed by a capital ("Kim_Minsu", "kim-minsu" and "KimMinsu" are read as "Kim
    Minsu", "kim minsu" and "Kim Minsu"). The reading and, for each of its characters, where it stands in the name (for
    a space put in, the character after it), with the name's length at the end. None when the name has no such join.
    The same in js/src/masker.ts (nameReading)."""
    word = lambda c: c.isalnum() or unicodedata.category(c).startswith("M")
    chars = list(name)
    for m in _NAME_JOIN.finditer(name):
        a, b = m.span()
        if a > 0 and b < len(name) and word(name[a - 1]) and name[b].isalnum():
            chars[a:b] = " " * (b - a)
    cuts = {i + 1 for i in range(len(name) - 1) if unicodedata.category(name[i]) == "Ll" and unicodedata.category(name[i + 1]) in ("Lu", "Lt")}
    spaced = "".join(chars)
    if spaced == name and not cuts:
        return None
    text: List[str] = []
    places: List[int] = []
    for i, c in enumerate(spaced):
        if i in cuts:
            text.append(" ")
            places.append(i)
        text.append(c)
        places.append(i)
    places.append(len(name))
    return "".join(text), places


def _validation_key(name: str, info: Any) -> Any:
    """The key a pydantic field is validated by: its validation alias (the first of its choices that is a name), its
    alias, or its name."""
    alias = getattr(info, "validation_alias", None)
    if isinstance(alias, str):
        return alias
    for choice in getattr(alias, "choices", None) or ():
        if isinstance(choice, str):
            return choice
    alias = getattr(info, "alias", None)
    return alias if isinstance(alias, str) else name


def _tool_name(t: Any) -> Any:
    """The name a tool is bound under: a LangChain tool's name, or the name of a tool definition (OpenAI or Anthropic
    form)."""
    if isinstance(t, dict):
        fn = t.get("function")
        return fn.get("name") if isinstance(fn, dict) else t.get("name")
    return getattr(t, "name", None)


# The chat model classes whose copies pass the agent's binding through a vault middleware (one per class: a copy's
# class), and where a copy keeps the steps of the middlewares that made it.
_BINDING: Dict[type, type] = {}
_BINDING_STEPS = "_newmean_pii_binding"


def _binding_model(model: Any, steps: Any) -> Any:
    """A copy of a chat model whose bind_tools gives the tools and options the agent binds to steps first (before),
    and the model it binds to them after (since 1.7.2: the review round's L7-1, the 24th outside review's A5): the final
    binding, which is what the provider gets, whatever LangChain version made it. The model itself is not changed; the
    copy is of a class made from the model's own (same name, module and fields), so the agent and the provider package
    read it as the model. A copy made from a copy (two vault middlewares) runs the steps of both, the outer middleware's
    last. Raises TypeError when the model cannot be copied this way."""
    from pydantic import BaseModel

    if not isinstance(model, BaseModel) or not callable(getattr(model, "bind_tools", None)):
        raise TypeError("the model is not a chat model this middleware can copy")
    cls = type(model)
    made = cls if getattr(cls, "__newmean_pii_binding__", False) else _BINDING.get(cls)
    if made is None:

        def bind_tools(self: Any, tools: Any, **kwargs: Any) -> Any:
            private = object.__getattribute__(self, "__pydantic_private__") or {}
            chain = private.get(_BINDING_STEPS, ())
            for before, _after in reversed(chain):
                tools, kwargs = before(tools, kwargs)
            bound = super(made, self).bind_tools(tools, **kwargs)  # type: ignore[misc]
            for _before, after in chain:
                bound = after(bound)
            return bound

        made = type(cls.__name__, (cls,), {
            "__module__": cls.__module__, "__qualname__": cls.__qualname__, "__slots__": (), "bind_tools": bind_tools,
            "__newmean_pii_binding__": True,
        })
        _BINDING[cls] = made
    copy = model.model_copy()
    private = dict(object.__getattribute__(copy, "__pydantic_private__") or {})
    private[_BINDING_STEPS] = (steps,) + tuple(private.get(_BINDING_STEPS, ()))
    object.__setattr__(copy, "__pydantic_private__", private)
    object.__setattr__(copy, "__class__", made)
    return copy


# The parts of a request a chat model builds from the messages (OpenAI "messages" and "input", Anthropic "system"): the
# rest of what _request_fields_of gives comes from the model's own fields and from the call's settings. The model's own
# fields can put these keys in too (since 1.7.2, gate 8 of the release standard: ChatAnthropic spreads model_kwargs over
# what it builds from the messages, so a "system" or "messages" given there is what the provider gets): then they are
# the model's fields like any other (_own_message_keys).
_MESSAGE_KEYS = frozenset(("messages", "input", "system"))


def _own_message_keys(model: Any) -> Set[str]:
    """The keys of _MESSAGE_KEYS that the model's own fields put into the request: a key of its model_kwargs (whatever
    it holds), or a field of that name or alias that holds text or JSON (a fake model's iterator of answers, a field
    left unset, is not one)."""
    kwargs = getattr(model, "model_kwargs", None)
    own = {k for k in _MESSAGE_KEYS if isinstance(kwargs, dict) and k in kwargs}
    fields = getattr(type(model), "model_fields", None)
    if isinstance(fields, dict):
        for name, info in fields.items():
            alias = getattr(info, "alias", None)
            for key in (name, alias):
                if key in _MESSAGE_KEYS and key not in own:
                    try:
                        value = getattr(model, name, None)
                    except Exception:
                        continue
                    if isinstance(value, (str, list, tuple, dict)) and value:
                        own.add(key)
    return own


def _request_fields_of(model: Any) -> Optional[Dict[str, Any]]:
    """What a chat model puts into a request from its own fields (since 1.7.2: model_kwargs such as user, metadata,
    safety_identifier, prompt_cache_key and a stored prompt's variables, extra_body, Anthropic's user_profile_id; since
    gate 8, also a "system", "messages" or "input" they give, whatever the key): the request the provider package builds
    for no messages, or for one empty user message when it refuses none (_get_request_payload, the method
    langchain-openai and langchain-anthropic send from), else its _default_params, else the fields that LangChain's
    provider packages pass into the request as they are (model_kwargs, extra_body); None when the model has none of
    these. What the package builds from those empty messages alone is left out (_MESSAGE_KEYS that no field of the model
    gives)."""
    own = _own_message_keys(model)
    build = getattr(model, "_get_request_payload", None)
    if callable(build):
        from langchain_core.messages import HumanMessage

        for messages in ([], [HumanMessage("")]):
            try:
                payload = build(messages)
            except Exception:  # (the package cannot build it this way)
                continue
            if isinstance(payload, dict):
                return {k: v for k, v in payload.items() if k not in _MESSAGE_KEYS or k in own}
    try:
        params = getattr(model, "_default_params", None)
    except Exception:
        params = None
    if isinstance(params, dict):
        return {k: v for k, v in params.items() if k not in _MESSAGE_KEYS or k in own}
    kwargs, extra = getattr(model, "model_kwargs", None), getattr(model, "extra_body", None)
    if not isinstance(kwargs, dict) and not isinstance(extra, dict):
        return None
    return {**(kwargs if isinstance(kwargs, dict) else {}), **({"extra_body": extra} if isinstance(extra, dict) else {})}


def _rewritten(value: Any, containers: bool = True) -> bool:
    """Whether a client that writes value as JSON runs code of the value's own, which can give another JSON on the next
    write (since 1.7.2, gate 8 of the release standard: a pydantic serializer gave a known value on its second call): a
    pydantic model (its serializers, computed fields), a dataclass, or (containers) a mapping, sequence or set of a type
    of its own, at any depth of the lists, tuples, sets, dicts and Enum values that hold them. Text, numbers, dates,
    UUIDs, paths, bytes, a class and an object with no JSON form (an http client, a timeout) are written the same way
    each time, or not at all. containers=False: the arguments of a model made on each call, which are not all request
    data (a client's header map)."""
    stack, on = [value], set()
    while stack:
        x = stack.pop()
        t = type(x)
        if t in (dict, list, tuple, set, frozenset):
            if id(x) in on:
                continue
            on.add(id(x))
            if t is dict:
                stack.extend(x.keys())
                stack.extend(x.values())
            else:
                stack.extend(x)
            continue
        if isinstance(x, Enum):
            stack.append(x.value)
            continue
        if isinstance(x, (str, int, float, bool, Decimal, bytes, bytearray, memoryview, datetime.date, datetime.time, uuid.UUID, pathlib.PurePath)) or x is None:
            continue
        if isinstance(x, type):
            continue
        if _is_pydantic(x) or dataclasses.is_dataclass(x):
            return True
        if containers and isinstance(x, (collections.abc.Mapping, collections.abc.Sequence, collections.abc.Set)):
            return True
    return False


def _schema_classes(value: Any) -> List[Any]:
    """The pydantic model classes in value (a response format given as a class: the provider client writes its JSON
    schema into the request), at any depth of its lists, tuples and dicts."""
    from pydantic import BaseModel

    out: List[Any] = []
    stack, on = [value], set()
    while stack:
        x = stack.pop()
        if isinstance(x, type):
            if issubclass(x, BaseModel):
                out.append(x)
        elif type(x) in (dict, list, tuple):
            if id(x) not in on:
                on.add(id(x))
                stack.extend(x.values() if type(x) is dict else x)
    return out


def _record_tags(vault: Vault, record: Any, given: Any, kind: str) -> List[str]:
    """The placeholders of a record the middleware sends instead of what the app passed back (given: an earlier answer,
    its reasoning or a tool call's input, restored), that the earlier call restored in it: the record with them restored
    is given (kind: how the earlier call restored it, "answer", "text" for reasoning, "value" for tool input). They are
    put into this call by the middleware, as they would be if it masked given again, so they count as this call's own
    and an answer to them is restored (since 1.7.2, the review of 1.7.1: after a reload from to_json(), which keeps the
    records, they were not, while after one from entries() they were). Placeholders the record holds as given holds them
    (made up by the model, of another call) do not count; when the record does not restore to given, none does. The
    same in js/src/masker.ts (recordTags)."""
    strings: List[str] = []

    def collect(x: Any) -> None:
        if isinstance(x, str):
            strings.append(x)
        elif isinstance(x, (list, tuple)):
            for y in x:
                collect(y)
        elif isinstance(x, dict):
            for y in x.values():
                collect(y)

    collect(record)
    try:
        shown = given if isinstance(given, str) else json.dumps(given, default=str)
    except (TypeError, ValueError, RecursionError):
        return []
    tags = [t for t in dict.fromkeys(t for s in strings for t in vault_tags(s)) if vault.value(t) is not None and t not in shown]
    if not tags:
        return tags
    only = vault.bindings(tags)
    if kind == "answer" and isinstance(record, str):
        restored = vault.restore_answer(record, only=only)
    elif kind == "text" and isinstance(record, str):
        restored = vault.restore(record, only=only)
    else:
        restored = vault.restore_value(record, only=only)
    return tags if _same(restored, given) else []


# what changes a redactor's detections after it is created (the detection cache is kept per setting)
_DETECTOR_SETTINGS = ("sensitivity", "regex", "keep_emoji", "whole_words", "strict_short_names")


def _vault_middleware(
    redactor: Optional[Redactor],
    vault: Vault,
    labels: Optional[Iterable[str]],
    name: str,
    restore_tools: Optional[Union[str, Iterable[str], Callable[[str], bool]]],
    restore_reasoning: bool,
    options: Dict[str, Any],
) -> Any:
    try:
        from langchain.agents.middleware import AgentMiddleware
        from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
    except ImportError as e:  # pragma: no cover
        raise ImportError("pii_middleware needs langchain >= 1.4.0 (pip install 'newmean-pii[langchain]')") from e
    if redactor is not None and options:
        raise TypeError("pass either a redactor or create_redactor options, not both")
    wanted = None if labels is None else frozenset(labels)
    if restore_tools is None:
        restores_tool: Callable[[str], bool] = lambda _name: False
    elif callable(restore_tools):
        restores_tool = restore_tools
    else:
        allowed = frozenset([restore_tools] if isinstance(restore_tools, str) else restore_tools)
        restores_tool = lambda n: n in allowed
    # what the model wrote is kept in the vault (vault.remember): the next call sends it again instead of the restored
    # text, and it is itself masked again on the way
    # (U+E000, a private-use character, keeps these keys apart from answer texts; a control character would not survive
    # every JSON store: PostgreSQL jsonb refuses U+0000)
    tool_key = lambda i: f"\ue000tool-call:{i}" if i else None
    lock = threading.Lock()
    holder: List[Redactor] = [] if redactor is None else [redactor]
    # what the model found in each text; the vault is applied again on every call, so values it learned later are covered
    found: "OrderedDict[Any, List[Any]]" = OrderedDict()

    def get() -> Redactor:
        if not holder:
            with lock:
                if not holder:
                    holder.append(create_redactor(labels=wanted, **options))
        return holder[0]

    def detect(text: str) -> List[Any]:
        r = get()
        # kept per vault generation and detector setting: a cleared vault or a changed sensitivity reads the text again.
        # A Redactor reads the text at the very snapshot of the settings that the result is kept under.
        # (only when the redactor's own detect is the one that would run: a subclass may have replaced it)
        at = getattr(r, "_detect_at", None) if getattr(type(r), "detect", None) is Redactor.detect else None
        settings = getattr(r, "_settings", None) or (lambda: tuple(getattr(r, a, None) for a in _DETECTOR_SETTINGS))
        snapshot = settings()
        key = (vault.generation, snapshot, text)
        with lock:
            spans = found.get(key)
            if spans is not None:
                found.move_to_end(key)
                return spans
        spans = [s for s in (at(text, snapshot) if callable(at) else r.detect(text)) if wanted is None or s.label in wanted]
        if not callable(at) and snapshot != settings():
            return spans  # another detector, and a setting changed while it read the text: not kept under either
        with lock:
            found[key] = spans
            if len(found) > _CACHE_MAX:
                found.popitem(last=False)
        return spans

    class Call:
        """One model call: the vault generation it started in and the placeholders it put in."""

        def __init__(self) -> None:
            self.generation = vault.generation
            self.tags: Set[str] = set()

        def apart(self) -> "Call":
            """The same call (its vault generation) with placeholders of its own, which this call does not restore."""
            other = Call()
            other.generation = self.generation
            return other

    def mask(text: str, call: Call, *, model: bool = False, recall: bool = True) -> str:
        """text as it goes to the model. model: text a model wrote; this vault's placeholders in it stay, what the
        middleware remembered for it is sent instead of the restored text, and without such a record the known values
        are masked wherever they occur in it. Other text (user, tool results) is read as received. recall=False: the
        text is already the model's own wording."""
        if not text:
            return text
        kept = vault.recall(text) if (model and recall) else None
        if isinstance(kept, str):
            source, loose = kept, False  # model text this middleware gave back, as the model wrote it
            # (the placeholders the earlier call restored in it are this call's own: an answer to them is restored)
            call.tags.update(_record_tags(vault, kept, text, "answer"))
        else:
            source, loose = text, model and recall
        # the model is given received text, and model text without a record (searched loosely), through the escapes in
        # it, when it has any (Unicode and Python repr escapes; not both readings: in the text as written it takes
        # pieces of escapes for values); the values the vault holds are searched in the text as it is written as well.
        # A model's own wording (its record) and a model's JSON document (an earlier structured answer, whose numbers
        # are quoted first) are given as written. A model's text keeps this vault's placeholders; in received text
        # every placeholder-like part is escaped.
        view = None if model and (not loose or json_document(source) is not None) else json_escapes_view(source)
        # (the placeholders as the model is given the text: in its reading through escapes, when it has one)
        keep = {t for t in vault_tags(source if view is None else view[0]) if vault.value(t) is not None} if model else None
        if view is None:
            spans = detect(source)
            # a model's text that is a JSON document (an earlier structured answer) stays valid JSON: a number in it
            # that holds something to mask is made a string first, so that its placeholder is quoted
            quoted = quote_numbers(source, spans, vault, loose) if model else None
            if quoted is not None:
                source, spans = quoted
            out, tags = redact_readings(source, spans, vault, keep=keep, loose=loose, generation=call.generation)
            if model:
                out = distinct_model_keys(source, out)
        else:
            out, tags = redact_readings(source, [], vault, keep=keep, loose=loose, through=(view, detect(view[0])), generation=call.generation)
        call.tags.update(tags)
        return out

    def distinct_model_keys(before: str, after: str) -> str:
        """A model's text that is a JSON document (an earlier structured answer), before and after it was masked as
        text: the keys of one object that masking made the same are told apart again as in a received document (" #2"),
        and keys that were the same before stay the same (since 1.7.2, the review of 1.7.1: two keys became one, and a
        JSON reader kept one field). When masking changed the document's shape it is left as it is."""
        if after == before:
            return after
        was = json_document(before)
        if was is None or not any(t[4] == "key" for t in was):
            return after
        now = json_document(after)
        if now is None or len(now) != len(was) or any(a[4] != b[4] or a[4] == "key" and a[5] != b[5] for a, b in zip(now, was)):
            return after
        values = distinct_json_keys(was, [t[2] for t in now])
        return replace_json_strings(after, now, values) if any(x != t[2] for x, t in zip(values, now)) else after

    def whole_known(value: str) -> Any:
        """The known value that value is as a whole, whatever its length and however it is written (its white space, a
        number's spelling)."""
        return vault.whole(value) if value else None

    def whole_value(value: str, call: Call) -> Optional[str]:
        """The placeholder of value when it is, as a whole, a value the vault holds."""
        if not value:
            return None
        with vault._lock:
            vault._unchanged(call.generation)
            whole = whole_known(value)
            if whole is None:
                return None
            tag = vault.tag(whole.label, whole.value)
        call.tags.add(tag)
        return tag

    def whole_number(value: str, call: Call) -> Optional[str]:
        """whole_value for a number as it is written in JSON text (the same number in any spelling: "821023456789.0",
        "8.21023456789e11"). One rule for a sign: a number whose plain form without its minus sign is a known value is
        that value with the sign outside ("-4.524e3" and -4524 for a known 4524 become "-[ID_NUMBER_1]", a string)."""
        tag = whole_value(value, call)
        if tag is not None or not value.startswith("-") or plain_number(value) == "0":
            return tag
        unsigned = whole_value(value[1:], call)
        return None if unsigned is None else "-" + unsigned

    def hide_key(key: Any, call: Call, keep: bool) -> Any:
        """A key of a received JSON document: the model does not read keys, but a value the vault holds is hidden in one
        too (a map keyed by e-mail address, or by phone number: an int key is written as its digits, "4155550123", and
        such a key that is a known value becomes its placeholder). keep: placeholder-like text in it stays as it is (the
        last pass). Keys are strings: every data slot is built into plain JSON first (_plain), as json.dumps writes the
        keys (a key it cannot write fails the call)."""
        if not isinstance(key, str):
            return key
        if not key:
            return key
        tag = whole_value(key, call)
        if tag is not None:
            return tag
        if keep:
            return hide(key, call)
        view = json_escapes_view(key)
        out, tags = redact_readings(key, [], vault, through=None if view is None else (view, []), generation=call.generation)
        call.tags.update(tags)
        return out

    def distinct_keys(before: List[Any], after: List[Any]) -> List[Any]:
        """The keys of one object after masking, different from one another again where masking made two the same
        ("Al" and "%41l" are both "[PERSON_1]"): the later one gets " #2", " #3", ..., so that no field is lost. Keys
        that were the same before (JSON text can repeat a key) stay the same: each gets what the first of them became."""
        used: Set[Any] = set()
        masked: Set[Any] = set()
        every = set(after)  # (a new key must not be one the object has further on either)
        following: Dict[Any, int] = {}  # the number to try next for each key (linear when many keys collide)
        first: Dict[Any, Any] = {}  # what each key as it was before became
        out: List[Any] = []
        for old, key in zip(before, after):
            if old in first:
                out.append(first[old])
                continue
            new = key
            if key in used and (key != old or key in masked):
                base = key if isinstance(key, str) else str(key)
                n = following.get(key, 2)
                while f"{base} #{n}" in used or f"{base} #{n}" in every:
                    n += 1
                following[key] = n + 1
                new = f"{base} #{n}"
            used.add(new)
            if key != old:
                masked.add(key)
            first[old] = new
            out.append(new)
        return out

    def distinct_json_keys(tokens: List[Any], values: List[Any]) -> List[Any]:
        """values (what each token of a JSON text becomes) with the keys of each object told apart as distinct_keys does."""
        by_object: Dict[Any, List[int]] = {}
        for i, t in enumerate(tokens):
            if t[4] == "key":
                by_object.setdefault(t[5], []).append(i)
        for at in by_object.values():
            if len(at) > 1:
                for i, key in zip(at, distinct_keys([tokens[i][2] for i in at], [values[i] for i in at])):
                    values[i] = key
        return values

    def hide(text: str, call: Call) -> str:
        """The developer's text (a system prompt) with the vault's known values hidden, without the model.
        Placeholder-like text in it stays as written (a prompt that explains placeholders). It is read through its
        escapes as received text is."""
        if not text:
            return text
        tokens = json_document(text)
        if tokens is not None:
            # a JSON document is done token by token, so it stays valid JSON: a number that is a known value becomes a string
            values = [
                (whole_number(t[2], call) or t[2]) if t[4] == "number"
                else hide_key(t[2], call, True) if t[4] == "key"
                else (whole_value(t[2], call) or hide(t[2], call))
                for t in tokens
            ]
            return replace_json_strings(text, tokens, distinct_json_keys(tokens, values))
        view = json_escapes_view(text)
        keep = set(vault_tags(text if view is None else view[0]))
        out, tags = redact_readings(text, [], vault, keep=keep, through=None if view is None else (view, []), generation=call.generation)
        call.tags.update(tags)
        return out

    def hide_text(text: str, call: Call) -> str:
        """The developer's text with the known values hidden: a whole known value of any length too."""
        out = (whole_value(text, call) or hide(text, call)) if text else text
        return text if out == text else out

    def hide_options(value: Any, call: Call) -> Any:
        """Model settings (the fields of the request body LangChain binds the model with), and the model's own request
        fields: data, with the known values hidden in every string (a "user", a template variable), in every number that
        is a known value and in every key, except at the exact paths where a provider defines an identifier, signed or
        encrypted data, a discriminator, a credential or a name it matches (a value there is sent as written, and the
        keys along the path stay), a numeric setting (a number there stays a number) or a JSON schema (read as
        hide_schema reads it): _SETTINGS_PATHS (since 1.7.2, the Python sibling of release gate 8, item 5: a name such
        as previous_response_id was kept at any depth inside a field a provider defines). Each setting is first built
        into the JSON the provider client writes (_plain), once. In a field a provider defines (_SETTINGS_FIELDS, or a
        field at a path) an object with no JSON form (an httpx.Timeout, a schema's class) stays as it is; in a setting
        no provider defines (an unknown provider's), in extra_body, in message-like content (_TEXT_SETTINGS) and in the
        application's own maps (_OPTION_DATA: metadata, labels, tags, variables) wherever they stand, such a value fails
        the call. Raises TypeError for it, before anything is sent."""
        if not isinstance(value, dict):
            return value
        on: Set[int] = set()
        number = lambda y: isinstance(y, (int, float, Decimal)) and not isinstance(y, bool)

        def walk(x: Any, nodes: List[_PathNode]) -> Any:
            """x, the value at the path that reaches nodes (none: data at every depth below)."""
            ident = any(n.id for n in nodes)
            if isinstance(x, str):
                return x if ident else hide_text(x, call)
            if number(x):
                return x if ident or any(n.number for n in nodes) else sweep(x, call, None)  # a number that is a known value
            if isinstance(x, dict) and any(n.schema for n in nodes):
                return hide_schema(x, call)
            if isinstance(x, (list, tuple)):
                return _mapped(x, lambda i, y: walk(y, _path_step(nodes, i)))
            if isinstance(x, dict):
                return members(x, nodes, lambda _k, y, steps: walk(y, steps))
            return x  # (True, False, None; an object with no JSON form in a field a provider defines)

        def members(x: Dict[Any, Any], nodes: List[_PathNode], value_of: Callable[[str, Any, List[_PathNode]], Any]) -> Any:
            """An object: a key stays as written where it continues a path, and is hidden as data anywhere else (two keys
            that become one placeholder stay two fields); the value under it is value_of(key, value, the nodes it reaches),
            or data built strictly under a key of _OPTION_DATA off the paths."""
            keys = [_json_key(k) for k in x]
            steps = [_path_step(nodes, k) for k in keys]
            names = distinct_keys(keys, [k if s else hide_key(k, call, True) for k, s in zip(keys, steps)])
            out: Dict[str, Any] = {}
            changed = False
            for k, name, s, y in zip(keys, names, steps, x.values()):
                v = hide_data(_plain(y, True, on), call) if not s and k in _OPTION_DATA else value_of(k, y, s)
                changed = changed or v is not y or name != k
                out[name] = v
            return out if changed or any(type(k) is not str for k in x) else x

        def top(k: str, y: Any, steps: List[_PathNode]) -> Any:
            # an unknown provider's setting and extra_body are the application's: built strictly, as message-like content is
            strict = k in _EXTRA_BODY or k in _TEXT_SETTINGS or (k not in _SETTINGS_FIELDS and not steps)
            return walk(_plain(y, strict, on), steps)

        try:
            return members(value, [_SETTINGS_TREE], top)
        except _NotJson as e:
            raise TypeError(f"[newmean-pii] the call was not sent: the model settings hold {e}, which cannot be written as JSON") from None

    def hide_data(value: Any, call: Call) -> Any:
        """The application's metadata in settings or options, built into plain JSON before (_plain): every string, a
        number that is a known value, and every key (two keys that become one placeholder stay two fields)."""
        if isinstance(value, str):
            return hide_text(value, call)
        if isinstance(value, (list, tuple)):
            return _mapped(value, lambda _i, y: hide_data(y, call))
        if isinstance(value, dict):
            keys = distinct_keys(list(value), [hide_key(k, call, True) for k in value])
            new = {key: hide_data(y, call) for key, y in zip(keys, value.values())}
            same = len(new) == len(value) and all(k in new and new[k] is value[k] for k in value)
            return value if same else new
        # (as data: a number that is a known value becomes its placeholder; the 24th outside review, A1)
        return sweep(value, call, None)

    def hide_name(name: str, call: Call) -> str:
        """The name of a message (HumanMessage, ChatMessage) with each known value in it replaced by its placeholder
        without brackets ("PERSON_1"), the form providers accept there; also a value written with "_", "-" or a capital
        where it has white space ("Kim_Minsu", "KimMinsu" for a known "Kim Minsu"). Such a placeholder is never
        restored, and is not one of the call's."""
        if not name or not len(vault):
            return name
        apart = call.apart()  # never restored: its placeholders are not the call's own
        whole = whole_value(name, apart)
        if whole is not None:
            return _bare_tags(name, whole)
        hidden = _bare_tags(name, hide_text(name, apart))
        # The name's own spelling: providers take only letters, digits, "_" and "-" there, so a value of two or more
        # words can stand in a name only joined ("Kim_Minsu", "kim-minsu", "KimMinsu"). In the name, "_" and "-" between
        # two word characters and a change from a small letter to a capital are read as a space, and a known value found
        # in that reading is hidden where the name as written holds no value of its own (the 1.7.1 review round, L1-A2).
        # Only a value of two or more words is read this way: a one-word value is found as written, and the reading
        # would cut words that are not joins ("xAnn", "1e-4524"). A name that is such a value as a whole in that reading
        # is that value ("Kim_Minsu" is one placeholder).
        read = _name_reading(name)
        if read is None:
            return hidden
        text, places = read
        words = lambda value: any(c.isspace() for c in value)
        own = vault.find(name, variants=True)
        known = vault.whole(text)
        if known is not None and words(known.value):
            hits = [(0, len(text), known.label, known.value)]
        else:
            hits = [(h.start, h.end, h.label, h.value) for h in vault.find(text, variants=True) if words(h.value)]
        out: List[str] = []
        last = k = 0
        for start, end, label, value in hits:
            a, b = places[start], places[end]
            while k < len(own) and own[k].end <= a:
                k += 1
            if a < last or (k < len(own) and own[k].start < b):
                continue  # as written, the name holds a value there
            with vault._lock:
                vault._unchanged(call.generation)
                tag = vault.tag(label, value)
            out.append(name[last:a] + _bare_tags("", tag))
            last = b
        if last == 0:
            return hidden
        # the rest of the name hidden as it is written, now with those values out of it
        rest = "".join(out) + name[last:]
        return _bare_tags(rest, hide_text(rest, apart))

    def hide_schema(schema: Any, call: Call, keep_title: bool = False) -> Any:
        """A JSON schema with the known values hidden in its descriptions, titles, comments and examples, through the
        keywords that hold schemas (properties, items, anyOf, $defs, ...). Property names, literal constraints (const,
        enum, default, pattern, also objects) and every other keyword stay as written; keep_title: the title of the top
        schema too (a framework takes it as the name)."""

        def example(x: Any) -> Any:
            # an example is data: its strings and numbers count, its keys are property names
            if isinstance(x, str):
                return hide_text(x, call)
            if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf"):
                return whole_number(json.dumps(x), call) or x
            return _mapped(x, lambda _k, y: example(y))

        def sub(x: Any) -> Any:
            # a schema (a dict; true and false are schemas too), or a list of them
            return _mapped(x, lambda _i, s: sub(s)) if isinstance(x, (list, tuple)) else walk(x, False)

        def walk(x: Any, top: bool) -> Any:
            if not isinstance(x, dict):
                return x

            def field(k: Any, y: Any) -> Any:
                if k in _SCHEMA_TEXT:
                    return (y if (k == "title" and top and keep_title) else hide_text(y, call)) if isinstance(y, str) else y
                if k in _SCHEMA_EXAMPLES:
                    return example(y)
                if k in _SCHEMA_MAPS:
                    return _mapped(y, lambda _name, s: sub(s))  # a property name, a definition's name -> schema
                if k in _SCHEMA_SUBSCHEMAS:
                    return sub(y)
                return y  # a literal constraint (const, enum, default, pattern), and any other keyword: as written

            return _mapped(x, field)

        return walk(schema, True)

    def hide_tool(t: Any, call: Call) -> Any:
        """A tool given to the model: its description and input schema. A LangChain tool is copied with the schema as
        JSON schema when a known value is in it (its name stays, so the agent still runs its own tool); a tool
        definition (a dict, OpenAI or Anthropic form) is copied. A tool LangChain cannot write as JSON schema fails the
        call, before anything is sent."""
        if isinstance(t, dict):
            new = dict(t)
            if isinstance(t.get("description"), str):
                new["description"] = hide_text(t["description"], call)
            for k in ("parameters", "input_schema"):
                if isinstance(t.get(k), dict):
                    new[k] = hide_schema(t[k], call)
            if isinstance(t.get("function"), dict):
                new["function"] = hide_tool(t["function"], call)
            return t if all(new[k] is t[k] for k in t) else new
        description = getattr(t, "description", None)
        if not isinstance(description, str) or not callable(getattr(t, "model_copy", None)):
            return t
        hidden = hide_text(description, call)
        update: Dict[str, Any] = {} if hidden == description else {"description": hidden}
        from langchain_core.utils.function_calling import convert_to_openai_tool

        parameters = convert_to_openai_tool(t).get("function", {}).get("parameters")
        if isinstance(parameters, dict):
            schema = hide_schema(parameters, call)
            if schema is not parameters:
                update["args_schema"] = schema
        return t.model_copy(update=update) if update else t

    def hide_provider_strategy(f: Any, call: Call) -> Any:
        """A provider strategy with the known values hidden in the JSON schema it sends (its top title is its name and
        stays); the type it parses the answer into stays."""
        import copy

        spec = f.schema_spec
        hidden = hide_schema(spec.json_schema, call, True)
        description = spec.description
        hidden_description = hide_text(description, call) if isinstance(description, str) else description
        if hidden is spec.json_schema and hidden_description == description:
            return f
        new_spec = copy.copy(spec)
        new_spec.json_schema = hidden
        new_spec.description = hidden_description
        new = copy.copy(f)
        new.schema_spec = new_spec
        return new

    def hide_response_format(f: Any, call: Call) -> Any:
        """The response format of the request: a provider strategy with the known values hidden in the JSON schema it
        sends (the type it parses the answer into stays). The tools of a tool strategy, and an auto strategy (a schema
        given as it is), which the agent resolves itself, are hidden where the agent binds them (see binding)."""
        try:
            from langchain.agents.structured_output import ProviderStrategy
        except ImportError:  # pragma: no cover - an older langchain: no strategies to read
            return f
        return hide_provider_strategy(f, call) if isinstance(f, ProviderStrategy) else f

    def format_holds_known_values(f: Any) -> bool:
        """Whether what a response format gives the model (the tool of each of its schemas, or the JSON schema of a
        provider strategy) holds a value the vault knows."""
        from langchain.agents.structured_output import AutoStrategy, OutputToolBinding, ProviderStrategy, ToolStrategy
        from langchain_core.utils.function_calling import convert_to_openai_tool

        schema = f.schema if isinstance(f, AutoStrategy) else f
        tool = f if isinstance(f, ToolStrategy) else None if isinstance(f, ProviderStrategy) else ToolStrategy(schema=schema)
        provider = f if isinstance(f, ProviderStrategy) else None if isinstance(f, ToolStrategy) else ProviderStrategy(schema=schema)
        probe = Call()  # (placeholders of its own: nothing of this is sent)
        for spec in tool.schema_specs if tool is not None else ():
            written = convert_to_openai_tool(OutputToolBinding.from_schema_spec(spec).tool)
            if hide_tool(written, probe) is not written:
                return True
        return provider is not None and hide_provider_strategy(provider, probe) is not provider

    def binding(call: Call, values: Dict[str, str], names: Set[str], parsed: Dict[int, Any]) -> Any:
        """What this call does where the agent binds the model to its tools and its response format: the final
        binding, which the provider gets (since 1.7.2: the review round's L7-1, L7-2 and L3-B5, the 24th outside
        review's A5 and B1). LangChain makes the tools of a tool
        strategy when the agent is created, and before 1.4 binds all of them whatever the request says; it resolves
        an auto strategy itself, and gives a schema without a title a name of its own. So the known values are hidden
        there, in every tool and option bound, under the names the agent gave them; the placeholders put in there join
        the call's (values). With placeholders in the call, the schemas of the response format go without the
        constraints a placeholder cannot meet ("format", "pattern": a date or an e-mail field), so the model is not
        made to write a value in their place, and the model's answer gets this call's values back in what the agent
        reads as the structured response (the tool call of the response format, or the text of a provider strategy)
        before the agent parses it and checks it against the original schema. The agent then keeps the message as the
        model wrote it (parsed maps the copy it read to that message). names: the names of the request's own tools.
        Returns the two steps: before (the tools and options that are bound) and after (the bound model)."""
        state: Dict[str, Any] = {"mine": set(), "native": False}
        placeholders = bool(values)  # (whether the messages gave the model placeholders, which it may write back)

        def before(tools: Any, kwargs: Dict[str, Any]) -> "tuple[Any, Dict[str, Any]]":
            known = set(call.tags)
            tools = [hide_tool(t, call) for t in tools] if isinstance(tools, (list, tuple)) else tools
            kwargs = hide_options(kwargs, call)
            new = call.tags - known
            if new:
                values.update(vault.bindings(new, generation=call.generation))
            mine = {_tool_name(t) for t in tools or ()} - names  # the tools of the response format
            native = isinstance(kwargs.get("response_format"), dict)
            if placeholders and (mine or native):
                tools = [relaxed_tool(t) if _tool_name(t) in mine else t for t in tools or ()]
                if native:
                    kwargs = {**kwargs, "response_format": relaxed_format(kwargs["response_format"])}
            state["mine"], state["native"] = mine, native
            return tools, kwargs

        def restored(m: Any) -> Any:
            if not values or not isinstance(m, AIMessage):
                return m
            update: Dict[str, Any] = {}
            calls = [{**c, "args": vault.restore_value(c["args"], only=values)} if c.get("name") in state["mine"] else c for c in m.tool_calls]
            if any(a is not b for a, b in zip(calls, m.tool_calls)):
                update["tool_calls"] = calls
            if state["native"]:
                content = map_content(m.content, lambda t, kind: json_values_restored(t) if kind == "text" else t)
                if content != m.content:
                    update["content"] = content
            if not update:
                return m
            out = m.model_copy(update=update)
            parsed[id(out)] = (out, m)
            return out

        def json_values_restored(text: str) -> str:
            # the string values of a JSON answer; its keys get their values back with the structured response, where
            # two keys that become the same are told apart (restore_data)
            tokens = [t for t in json_tokens(text) or [] if t[4] not in ("key", "number")]
            return replace_json_strings(text, tokens, [vault.restore(t[2], only=values) for t in tokens]) if tokens else text

        async def arestored(m: Any) -> Any:
            return restored(m)

        def after(bound: Any) -> Any:
            from langchain_core.runnables import RunnableLambda

            return bound | RunnableLambda(restored, afunc=arestored, name="NewmeanPiiStructuredOutput")

        return before, after

    def relaxed_schema(schema: Any) -> Any:
        """A JSON schema without the constraints a placeholder cannot meet ("format", "pattern"), through the keywords
        that hold schemas; the same object when it has none."""

        def sub(x: Any) -> Any:
            return _mapped(x, lambda _i, y: sub(y)) if isinstance(x, (list, tuple)) else walk(x)

        def walk(x: Any) -> Any:
            if not isinstance(x, dict):
                return x
            out = {
                k: (_mapped(y, lambda _n, z: sub(z)) if k in _SCHEMA_MAPS else sub(y) if k in _SCHEMA_SUBSCHEMAS else y)
                for k, y in x.items()
                if k not in ("format", "pattern")
            }
            same = len(out) == len(x) and all(out[k] is x[k] for k in x)
            return x if same else out

        return walk(schema)

    def relaxed_tool(t: Any) -> Any:
        """A tool of the response format, as it is bound, with a relaxed schema of its arguments (written as a tool
        definition when it changes)."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        written = t if isinstance(t, dict) else convert_to_openai_tool(t)
        fn = written["function"] if isinstance(written.get("function"), dict) else written
        key = "parameters" if "parameters" in fn else "input_schema"
        relaxed = relaxed_schema(fn.get(key))
        if relaxed is fn.get(key):
            return t
        if fn is written:
            return {**written, key: relaxed}
        return {**written, "function": {**fn, key: relaxed}}

    def relaxed_format(f: Dict[str, Any]) -> Dict[str, Any]:
        """The response_format option of a provider strategy with a relaxed schema."""
        spec = f.get("json_schema")
        if not isinstance(spec, dict) or not isinstance(spec.get("schema"), dict):
            return f
        relaxed = relaxed_schema(spec["schema"])
        return f if relaxed is spec["schema"] else {**f, "json_schema": {**spec, "schema": relaxed}}

    def request_fields(request: Any, call: Call) -> Dict[str, Any]:
        """The fields of a request outside the messages that hold free text (since 1.7.2): tools, the response format,
        model settings. Only the fields that changed are returned."""
        out: Dict[str, Any] = {}
        tools = getattr(request, "tools", None)
        hidden_tools = [hide_tool(t, call) for t in tools] if isinstance(tools, list) else None
        if hidden_tools is not None and any(a is not b for a, b in zip(hidden_tools, tools)):
            out["tools"] = hidden_tools
        fmt = getattr(request, "response_format", None)
        if fmt is not None:
            hidden_fmt = hide_response_format(fmt, call)
            if hidden_fmt is not fmt:
                out["response_format"] = hidden_fmt
        settings = getattr(request, "model_settings", None)
        if isinstance(settings, dict):
            hidden_settings = hide_options(settings, call)
            if hidden_settings is not settings:
                out["model_settings"] = hidden_settings
        return out

    def hidden_model(model: Any, call: Call) -> Any:
        """The model of a request with the known values hidden in its own fields that go into the request (since
        1.7.2, the gate run of 1.7.1: model_kwargs such as user, metadata, safety_identifier, prompt_cache_key and a
        stored prompt's variables, extra_body, Anthropic's user_profile_id were sent as written). They are data and
        are hidden as model settings are, whatever their key: a "system" or "messages" given in model_kwargs too, which
        ChatAnthropic sends instead of what it builds from the messages (gate 8 of the release standard: they were left
        out). Each field of the request (_request_fields_of) is traced to the field of the model it comes from: a key of
        model_kwargs, or the field of that name or alias. Each such field is built once into the JSON the provider
        client writes (_plain, as model settings are: a pydantic model is dumped once) and that snapshot is masked; a
        copy of the model for this call (pydantic model_copy, which shares the model's clients) holds the snapshot of
        every field that holds a known value or an object whose JSON its own code makes (_rewritten: a pydantic model,
        a dataclass), so the client writes the one dump that was masked and not the object again (gate 8: a serializer
        that gave a known value on its second call was sent; the copy was made only when the first dump held a known
        value). Other fields stay as the model holds them (the client writes a date or a tuple the same way each time,
        or refuses it as it would without the middleware), and the model itself is given when no field needs a
        snapshot. The copy's request is read again before anything is sent, and fails the call, naming the field: a
        field that still holds a known value (one the copy cannot trace, a response format class whose JSON schema
        holds one), or one it cannot trace that holds an object a client would write again on its own. So does a model
        that is not a pydantic model, and a model made on each call (init_chat_model with configurable fields), when
        what it would send holds a known value or such an object."""
        from pydantic import BaseModel

        def held(fields: Dict[str, Any], lenient: bool = False) -> List[str]:
            """The fields that hold a value the vault knows, by name: a field is held when reading it as model settings
            are read puts a placeholder in (each with placeholders of its own: what is only looked at is not sent), or
            when it gives a response format as a pydantic class whose JSON schema (which the client writes) holds a
            known value. (Settings come back as the plain JSON a provider writes, release gate 4, so a field without a
            known value can come back as another object, a date as its text: that alone does not hold a value.) A
            request field that cannot be written as JSON fails the call, naming it. lenient: the arguments of a model
            made on each call, which are not all request fields (an http client, a rate limiter): one with no JSON form
            is not read."""
            out = []
            for k, v in fields.items():
                probe = call.apart()
                try:
                    hide_options({k: v}, probe)
                except TypeError:
                    if lenient:
                        continue
                    raise TypeError(f'[newmean-pii] the call was not sent: the model\'s own request field "{k}" holds a value that cannot be written as JSON') from None
                for cls in _schema_classes(v):
                    try:
                        hide_schema(cls.model_json_schema(), probe)
                    except Exception:  # (a class without a JSON schema: the client cannot write it either)
                        continue
                if probe.tags:
                    out.append(str(k))
            return out

        def fail(names: List[str], why: str) -> TypeError:
            shown = ", ".join(f'"{n}"' for n in names)
            return TypeError(f"[newmean-pii] the model's own request field {shown} holds a value the vault knows, and {why}")

        def rewritten(fields: Dict[str, Any], why: str, containers: bool = True) -> None:
            again = [str(k) for k, v in fields.items() if _rewritten(v, containers)]
            if again:
                shown = ", ".join(f'"{n}"' for n in again)
                raise TypeError(
                    f"[newmean-pii] the call was not sent: the model's own request field {shown} holds an object whose JSON "
                    f"the client writes again on each call (a pydantic model, a dataclass), and {why}"
                )

        config = getattr(model, "_default_config", None)
        if isinstance(config, dict) and not isinstance(model, BaseModel):
            made = "a model made on each call (init_chat_model with configurable fields) cannot be copied to send it"
            left = held(config, lenient=True)
            if left:
                raise fail(left, made + " hidden")
            rewritten(config, made + " as the one JSON that was masked", containers=False)
            return model
        fields = _request_fields_of(model)
        if not fields:
            return model
        if not isinstance(model, BaseModel):
            left = held(fields)
            if left:
                raise fail(left, "this model cannot be copied to send it hidden (pass a LangChain chat model)")
            rewritten(fields, "this model cannot be copied to send it as the one JSON that was masked (pass a LangChain chat model)")
            return model
        def snapshot(value: Any, key: Any = None) -> Any:
            """value (a field of the request, or model_kwargs, whose keys are the request's) built once into plain JSON
            and masked: what the copy sends when it holds a known value or an object whose JSON its own code makes (a
            second dump can differ: what is sent is this one dump, masked); else value itself, which the client writes
            the same way each time (a date, a tuple) or refuses as it would without the middleware."""
            probe = call.apart()
            try:
                hidden = hide_options(value if key is None else {key: value}, probe)
            except TypeError:
                field = "model_kwargs" if key is None else key
                raise TypeError(f'[newmean-pii] the call was not sent: the model\'s own request field "{field}" holds a value that cannot be written as JSON') from None
            hidden = hidden if key is None else next(iter(hidden.values()))  # (the one field, whatever its key became)
            if hidden is value or not (probe.tags or _rewritten(value)):
                return value
            call.tags.update(probe.tags)
            return hidden

        # every field of the model that goes into the request, built once into plain JSON and masked: the copy holds it
        update: Dict[str, Any] = {}
        kwargs = getattr(model, "model_kwargs", None)
        if isinstance(kwargs, dict) and kwargs:
            hidden = snapshot(kwargs)
            if hidden is not kwargs:
                update["model_kwargs"] = hidden
        known = type(model).model_fields
        names = {name: name for name in known}
        for name, info in known.items():
            alias = getattr(info, "alias", None)
            if isinstance(alias, str):
                names.setdefault(alias, name)
        own = _own_message_keys(model)
        traced: Set[str] = set()  # the request's keys whose value the copy holds as its snapshot
        for key in fields:
            if isinstance(kwargs, dict) and key in kwargs:
                traced.add(key)
                continue
            name = names.get(key)
            if name is None or (key in _MESSAGE_KEYS and key not in own):
                continue
            value = getattr(model, name, None)
            if value is None or isinstance(value, bool):
                continue
            traced.add(key)
            if name in update:
                continue
            hidden = snapshot(value, key)
            if hidden is not value:
                update[name] = hidden
        copy = model.model_copy(update=update) if update else model
        sent = (_request_fields_of(copy) or {}) if update else fields
        left = held(sent)
        if left:
            raise fail(left, "this model cannot be copied to send it hidden")
        # (a field the copy holds as its snapshot is plain JSON; one it cannot trace is sent as the model holds it)
        rewritten({k: v for k, v in sent.items() if k not in traced}, "the copy cannot send it as the one JSON that was masked")
        return copy

    def leaves(items: List[Any], call: Call) -> List[str]:
        """Values of one received document, (key, value, is a number) triples, masked with one detection over all of
        them: each on a line of its own after its key ("name: Kim Minsu"), which gives a value the context a model
        needs and keeps JSON syntax out of it. A value that holds Unicode escapes is given in its reading through
        them. A number is compared and remembered in its plain form and masked whole. A string that is itself a JSON
        document is read on its own in the same way."""
        apart: Dict[int, str] = {}
        views: Dict[int, Any] = {}
        for i, (_key, value, number) in enumerate(items):
            if number or not value:
                continue
            if json_document(value) is not None:
                apart[i] = received(value, call)
            else:
                view = json_escapes_view(value)
                if view is not None:
                    views[i] = view

        # the document as the model reads it: a value that holds Unicode escapes in that reading
        starts, doc = [], ""
        for i, (key, value, _number) in enumerate(items):
            if i in apart:
                starts.append(-1)
                continue
            doc += ("" if doc == "" else "\n") + (f"{key}: " if key else "")
            starts.append(len(doc))
            doc += views[i][0] if i in views else value
        spans = detect(doc) if doc.strip() else []

        def own_spans(spans: List[Any]) -> Any:
            """The spans inside the value at a (values come in order), counted from its start."""
            p = 0

            def own(a: int, length: int) -> List[Span]:
                nonlocal p
                while p < len(spans) and spans[p].end <= a:
                    p += 1
                out, q = [], p
                while q < len(spans) and spans[q].start < a + length:
                    s = spans[q]
                    if s.end > a:
                        out.append(Span(max(s.start, a) - a, min(s.end, a + length) - a, s.label, s.source))
                    q += 1
                return out

            return own

        own = own_spans(spans)
        out: List[str] = []
        for i, (_key, value, number) in enumerate(items):
            if i in apart or value == "":
                out.append(apart.get(i, ""))
                continue
            view = views.get(i)
            read = value if view is None else view[0]
            mine = own(starts[i], len(read))  # (of the reading, when the value has one)
            with vault._lock:
                vault._unchanged(call.generation)
                if number:
                    # the same number however it is written: "8.21023456789e11" is 821023456789
                    # (whole_number puts its placeholder into call.tags itself)
                    whole_tag = whole_number(value, call)
                    if whole_tag is not None:
                        masked, tags = whole_tag, []
                    else:
                        # (a number shorter than the vault searches for is held only to be restored: _tag_found)
                        tag = vault._tag_found(mine[0].label, plain_number(value)) if mine else None
                        masked, tags = (value, []) if tag is None else (tag, [tag])
                else:
                    whole = whole_known(value)
                    if whole is not None:
                        # what the model found inside the value is learned as well ("Alice Morgan" in a known
                        # "M. Alice Morgan"), so that it is masked where it stands alone
                        # (not what is a known value written another way: that is that value)
                        for s in model_values(read, mine):
                            inner = read[s.start : s.end]
                            if inner.strip() and vault.whole(inner) is None:
                                vault._tag_found(s.label, inner)
                        tags = [vault.tag(whole.label, whole.value)]
                        masked = tags[0]
                    elif view is None:
                        masked, tags = redact_readings(value, mine, vault)
                    else:
                        masked, tags = redact_readings(value, [], vault, through=(view, mine))
            call.tags.update(tags)
            out.append(masked)
        return out

    def received(text: str, call: Call) -> str:
        """Text that came from outside (a user message, a tool result). A JSON document is read value by value, with the
        whole document as context for the model, so that escapes ("\\u00e9") and JSON syntax cannot hide a value or be
        cut by a placeholder; any other text is masked as it is."""
        tokens = json_document(text) if text else None
        if tokens is not None:
            # values: one reading of the document by the model; keys: the vault's known values only
            masked = iter(leaves([(t[3], t[2], t[4] == "number") for t in tokens if t[4] != "key"], call))
            values = [hide_key(t[2], call, False) if t[4] == "key" else next(masked) for t in tokens]
            return replace_json_strings(text, tokens, distinct_json_keys(tokens, values))
        # any other text is read whole, as it is written (JSON inside it too: the vault's known values are then searched
        # in its other readings, see again)
        return mask(text, call)

    def mask_data(value: Any, call: Call) -> Any:
        """JSON data that came from outside (a tool result): every string value masked, and numbers as they would be
        written (a card or phone number can be a JSON number), with the whole document as context for the model. The
        data is plain JSON: every data slot is built into the JSON a provider client writes before it is masked
        (_plain: a pydantic model as its dump, made once; a dataclass as its fields; a set as a list; a date in its ISO
        form; bytes as their UTF-8 text; a key as json.dumps writes it). Keys: the vault's known values only (the model
        does not read them); two keys that become the same placeholder stay two fields."""
        items: List[Any] = []
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

        def collect(v: Any, key: Optional[str] = None) -> None:
            if isinstance(v, str):
                items.append((key, v, False))
            elif number(v):
                items.append((key, json.dumps(v), True))
            elif isinstance(v, (list, tuple)):
                for x in v:
                    collect(x, key)
            elif isinstance(v, dict):
                for k, x in v.items():
                    collect(x, k if isinstance(k, str) else None)

        collect(value)
        masked = iter(leaves(items, call))

        def rebuild(v: Any) -> Any:
            if isinstance(v, str):
                return next(masked)
            if number(v):
                m = next(masked)
                return v if m == json.dumps(v) else m  # a masked number becomes its placeholder, a string
            if isinstance(v, (list, tuple)):
                return [rebuild(x) for x in v]
            if isinstance(v, dict):
                # (keys: the vault's known values only; the model does not read them. Two keys that become the same
                # placeholder stay two fields)
                keys = distinct_keys(list(v), [hide_key(k, call, False) for k in v])
                return {key: rebuild(x) for key, x in zip(keys, v.values())}
            return v

        return rebuild(value)

    def mask_value(value: Any, call: Call, *, model: bool = True, recall: bool = True) -> Any:
        """Every string inside a JSON-like value masked (tool-call arguments: as model text); a string that is exactly a
        known value becomes its placeholder whatever its length."""
        if isinstance(value, str):
            return whole_value(value, call) or mask(value, call, model=model, recall=recall)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf"):
            # a number that is a value the vault holds (a phone number a model wrote as a number) becomes its placeholder
            return whole_number(json.dumps(value), call) or value
        if isinstance(value, list):
            return [mask_value(v, call, model=model, recall=recall) for v in value]
        if isinstance(value, tuple):
            return tuple(mask_value(v, call, model=model, recall=recall) for v in value)
        if isinstance(value, dict):
            return {k: mask_value(v, call, model=model, recall=recall) for k, v in value.items()}
        return value

    def mask_input(value: Any, tool_call_id: Any, call: Call) -> Any:
        """Tool input as the model wrote it (the record of this tool call when the middleware has one); its strings are
        masked again before they go back to the model. JSON text ("arguments") is masked value by value."""
        key = tool_key(tool_call_id)
        written = vault.recall(key) if key else None
        recall = written is None  # without a record of the call, known values are searched more widely
        if not isinstance(value, str):
            # parsed arguments (tool_calls) may have been restored for the tool: the record of the call is sent instead
            # (and the placeholders restore_tools put back in them are this call's own, as an answer's are)
            if written is not None:
                call.tags.update(_record_tags(vault, written, value, "value"))
            return mask_value(value if written is None else written, call, recall=recall)
        # JSON text inside a block is never restored by this middleware: it is masked value by value, and every other
        # token (numbers, keys) stays as written
        tokens = [t for t in json_tokens(value) or [] if t[4] != "key"] if json_string_values(value) is not None else None
        if tokens is None:
            return mask(value, call, model=True, recall=recall)
        values = [(whole_number(t[2], call) or t[2]) if t[4] == "number" else mask_value(t[2], call, recall=recall) for t in tokens]
        return replace_json_strings(value, tokens, values)

    def map_content(
        content: Any,
        text: Callable[[str, str], str],
        call: Optional[Call] = None,
        model: bool = False,
        wrote: Optional[Callable[[Any, Any], None]] = None,
        outside: bool = True,
    ) -> Any:
        """Bare strings, every block's "text" and reasoning fields mapped by text(s, kind), kind "text" or "reasoning".
        With call (masking): also tool traffic inside blocks, whatever the block type: what a model wrote for a tool
        (Anthropic "tool_use" input, LangChain "tool_call" args, OpenAI "function_call" arguments) and what came back
        (Anthropic "tool_result" content, "server_tool_result" output), read as received. Other fields (ids, names,
        URLs, base64 data, signatures) are left alone. outside: content that is not model text came from outside (a
        tool's or a user's message), not from a model's own message (a server tool's result in it)."""
        if isinstance(content, str):
            return text(content, "text")
        if isinstance(content, list):
            # in text a model wrote (and in what is restored) any typed object is a block; in what came from outside only
            # a block type of LangChain or a provider that carries its own content is: rows a tool returned with a type
            # of their own ({"type": "contact", ...}) or with a block's type and not its content ({"type": "file",
            # "id", "name", "owned_by"}) are data
            block = _typed if (model or call is None) else (_received_block if outside else _block)
            if call is not None and any(isinstance(x, (dict, list, tuple)) for x in content) and not any(block(x) for x in content):
                # rows of JSON data where content blocks were expected (a hand-built tool message, typed rows of a
                # tool): one document of data
                return mask_data(content, call)
            out: List[Any] = []
            for b in content:
                if isinstance(b, str):
                    out.append(text(b, "text"))
                    continue
                if not block(b):
                    # a row of JSON data where a content block was expected (a hand-built tool message) is masked as data
                    out.append(mask_data(b, call) if call is not None and _is_data(b) else b)
                    continue
                kind = "reasoning" if b.get("type") in _REASONING_FIELDS else "text"
                nb = dict(b)
                if isinstance(b.get("text"), str):
                    nb["text"] = text(b["text"], kind)
                field = _REASONING_FIELDS.get(b.get("type"))  # type: ignore[arg-type]
                if field and isinstance(b.get(field), str):
                    nb[field] = text(b[field], "reasoning")
                # the tool call a block belongs to: OpenAI "function_call" names it call_id (its own id is the item's)
                call_id = b["call_id"] if isinstance(b.get("call_id"), str) else b.get("id")
                if call is not None:
                    for k in _INPUT_FIELDS:
                        if b.get(k) is not None:
                            nb[k] = mask_input(b[k], call_id, call) if model else mask_data(b[k], call)
                    for k in _RESULT_FIELDS:
                        if b.get(k) is None:
                            continue
                        if _slot(b.get("type"), k) is not None:
                            # a field where blocks stand (Anthropic tool_result content)
                            nb[k] = mask_result(b[k], call, outside and not model)
                        else:
                            # in any other block the field holds a payload (a json block's content, a server tool's
                            # output): JSON data, whatever types its objects give themselves
                            nb[k] = received(b[k], call) if isinstance(b[k], str) else mask_data(b[k], call)
                    # a document given inline (Anthropic: source {"type": "text", "data"} or {"type": "content", ...})
                    src = b.get("source")
                    if isinstance(src, dict) and src.get("type") == "text" and isinstance(src.get("data"), str):
                        nb["source"] = {**src, "data": received(src["data"], call)}
                    elif isinstance(src, dict) and isinstance(src.get("type"), str) and isinstance(src.get("content"), list):
                        nb["source"] = {**src, "content": mask_result(src["content"], call, outside and not model)}
                elif wrote is not None:
                    for k in _INPUT_FIELDS:
                        if b.get(k) is not None:
                            wrote(call_id, b[k])  # a tool call that is only in a content block
                out.append(nb)
            return out
        return content

    def mask_result(v: Any, call: Call, outside: bool = True) -> Any:
        """What a tool returned, inside a content block: read as received. Text, typed content blocks ({"type": ...})
        and rows of JSON data (every string value). outside: the block is in what came from outside (a tool's result, a
        user's message), where a typed object is a block only with its type's own content; in what a model wrote (a
        server tool's result) the block types of LangChain and the providers are blocks."""
        block = _received_block if outside else _block
        if isinstance(v, str):
            return received(v, call)
        if block(v):
            return map_content([v], lambda t, _kind: received(t, call), call, False, outside=outside)[0]
        if isinstance(v, (list, tuple)) and any(block(x) for x in v):
            return [mask_result(x, call, outside) for x in v]  # content, item by item
        return mask_data(v, call)  # any other list is one document of JSON data

    def binary_data(s: str) -> bool:
        """Whether s is binary data and not text: a data URL or a long base64-looking string that holds no known value of
        six or more characters at word edges (base64 never does by chance; an id or a file name does), as it is written
        or in any reading of it ("Kim+Minsu+asked+..." looks like base64 and is a form field)."""
        if not _binary(s):
            return False
        holds = lambda t: any(len(h.value) >= 6 for h in vault.find(t))
        return not holds(s) and not any(holds(r[0]) for r in escaped_readings(s))

    def sweep(value: Any, call: Call, slot: Optional[_Slot]) -> Any:
        """The last pass over a message that is about to be sent: every string in it, whatever field it is in, has the
        values the vault holds hidden (no model call; placeholder-like text stays as it is). It covers what the steps
        before do not know how to read: a block type added later, provider-specific data. slot: value stands where the
        framework's objects of that slot stand (a block, a tool call, a tool's output), where identifiers, names a
        framework matches, references to files and signed blocks (reasoning) are left alone in the framework's objects;
        an object there with a type the slot does not take (a row a tool returned, {"type": "contact", ...}) is data in
        every field but its type and provider options. None: value is data, where every string and key counts, at any
        depth and whatever types its objects give themselves. The message is plain JSON (plain_message), with bytes
        only as a file block's data. Returns the same object when nothing changed."""
        if isinstance(value, str):
            # a string that is a known value as a whole is hidden whatever it looks like; binary data is not text
            hidden = whole_value(value, call) or (value if binary_data(value) else hide(value, call))
            return value if hidden == value else hidden
        if isinstance(value, (bytes, bytearray)):
            # a file block's data: bytes that are UTF-8 text are that text; other bytes (an image) are binary data
            text = _utf8(value)
            hidden = text if text is None else sweep(text, call, None)
            return value if hidden is text else type(value)(hidden.encode("utf-8"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # in data, a number that is a known value as a whole (a phone number) becomes its placeholder
            return (whole_number(json.dumps(value), call) or value) if math.isfinite(value) and slot is None else value
        if isinstance(value, (list, tuple)):
            out = [sweep(x, call, slot) for x in value]
            return value if all(a is b for a, b in zip(out, value)) else (out if isinstance(value, list) else tuple(out))
        if not isinstance(value, dict):
            return value  # (a boolean or None)
        if slot is None:
            return sweep_fields(value, call, "data", None)
        kind = value.get("type")
        if not isinstance(kind, str):
            return sweep_fields(value, call, "framework" if slot.untyped else "data", None)
        # in what came from outside, a typed object without its type's own content is a row of data
        if slot.received and not _carries(value):
            return sweep_fields(value, call, "data", None)
        # an object with a type that may stand here is the framework's; one with another type is not
        if kind not in (_LANGCHAIN_TYPES if slot.types is None else slot.types):
            return sweep_fields(value, call, "foreign", None)
        return value if kind in _SIGNED else sweep_fields(value, call, "framework", kind, received=slot.received)

    def sweep_fields(
        value: Dict[Any, Any], call: Call, kind: str, of: Optional[str], fields: Optional[Dict[str, _Slot]] = None, received: Union[bool, str] = False
    ) -> Any:
        """The fields of one object in the last pass. kind: "framework" (an object of the framework of type of; None: an
        object without a type), "data", "foreign" (an object of a type the framework does not have there: every field
        but its type and provider options is data) or "fields" (the fields named in fields hold the framework's objects
        of the slot given, the other fields are data: the top of additional_kwargs). received: a framework object that
        stands in what came from outside (the slots in its fields are too)."""
        is_data = lambda k: kind == "data" or (kind == "foreign" and k not in _OPTIONS) or (kind == "fields" and k not in fields)
        # data: every string counts, a key too (two keys that become the same placeholder stay two fields); in the
        # framework's objects, a key that is not the name of one of their fields is data as well
        hidden = (lambda k: k not in _FRAME_KEYS) if kind == "framework" else is_data
        keys = distinct_keys(list(value), [hide_key(k, call, True) if hidden(k) else k for k in value]) if any(hidden(k) for k in value) else list(value)
        new: Dict[Any, Any] = {}
        for key, (k, x) in zip(keys, value.items()):
            if kind != "framework":
                new[key] = sweep(x, call, None) if is_data(k) else (sweep(x, call, fields[k]) if kind == "fields" else x)
            elif k in _NOT_TEXT and (k != "name" or of is None or of in _NAMED) and not (k == "id" and received == "tool" and value.get("source_type") != "id"):
                # identifiers, references and signed data stay; "name" only where it names a tool or a function (an
                # object without a type stands where those stand: a tool call, a function). "id": LangChain's
                # identifier of a block, in a model's and a user's message (since 1.7.2, gate 4 of the release
                # standard: a user's text block had its id masked); in a tool's result no block is matched by its id,
                # and there it is data (except a LangChain v0 file given by its id)
                new[key] = x
            else:
                # a field that holds the framework's objects (the slot chosen by the object's type and the field), or
                # data ("data": a string is text unless it is binary, base64; bytes that are not UTF-8 are binary)
                new[key] = sweep(x, call, _slot(of, k, received))
        same = len(new) == len(value) and all(k in new and new[k] is value[k] for k in value)
        return value if same else new

    def sweep_kwargs(kwargs: Any, call: Call) -> Any:
        """The last pass over a message's additional_kwargs: its tool calls (OpenAI "tool_calls", "function_call") are
        the framework's, anything else in it is data."""
        if not isinstance(kwargs, dict):
            return sweep(kwargs, call, _BLOCKS)
        return sweep_fields(kwargs, call, "fields", None, _KWARGS)

    def mask_message(m: Any, call: Call) -> Any:
        if isinstance(m, SystemMessage):
            # the developer's text: the model does not read it for values, but the values the vault holds are hidden in
            # it like everywhere else
            update = {"content": sweep(map_content(m.content, lambda t, _kind: hide(t, call)), call, _BLOCKS)}
            kwargs = sweep_kwargs(m.additional_kwargs, call)
            if kwargs is not m.additional_kwargs:
                update["additional_kwargs"] = kwargs
            return m if all(update[k] == getattr(m, k) for k in update) else m.model_copy(update=update)
        if not isinstance(m, BaseMessage):
            return m
        # a message of any other kind (a ChatMessage, a legacy FunctionMessage) is text from outside as well
        model = isinstance(m, AIMessage)

        def text(t: str, kind: str) -> str:
            if not model:
                return received(t, call)  # from outside: a JSON document is read value by value
            if kind == "reasoning":
                # reasoning goes back exactly as the model wrote it when the middleware has its record: providers sign it
                written = vault.recall(t)
                if isinstance(written, str):
                    if restore_reasoning:
                        # (the placeholders restore_reasoning put back in it are this call's own, as an answer's are)
                        call.tags.update(_record_tags(vault, written, t, "text"))
                    return written
            return mask(t, call, model=True)

        update: Dict[str, Any] = {"content": map_content(m.content, text, call, model)}
        name = getattr(m, "name", None)
        if isinstance(name, str) and name and (isinstance(m, HumanMessage) or type(m).__name__ == "ChatMessage"):
            # the name of a person's message, which OpenAI-format providers send: a known value in it becomes its
            # placeholder without brackets ("PERSON_1"), the form providers accept there; it is never restored, so
            # hiding it adds nothing to the placeholders of the call (a "[PERSON_1]" in the answer stays as the model
            # wrote it unless the call put that placeholder in somewhere else; the 23rd outside review)
            # (also a value written with "_", "-" or a capital where it has white space: "Kim_Minsu"; the 1.7.1 review
            # round, L1-A2)
            update["name"] = hide_name(name, call)
        if model and m.tool_calls:
            update["tool_calls"] = [{**c, "args": mask_input(c["args"], c.get("id"), call)} for c in m.tool_calls]
        if model and getattr(m, "invalid_tool_calls", None):
            # arguments the provider could not parse are still text the model wrote
            update["invalid_tool_calls"] = [
                {**c, "args": mask_input(c["args"], c.get("id"), call)} if isinstance(c.get("args"), str) else c for c in m.invalid_tool_calls
            ]
        # The last pass: no value the vault holds stays in any text field of the message, whatever kind of block it is
        # in, and in the provider-specific data kept beside the content (additional_kwargs: its tool calls are the
        # framework's, anything else in it is data).
        # (in what came from outside, a typed object without its type's own content is a row of data there too)
        content_slot = _BLOCKS if model else _RECEIVED_TOOL if isinstance(m, ToolMessage) or type(m).__name__ == "FunctionMessage" else _RECEIVED
        update = {k: (v if k == "name" else sweep(v, call, content_slot if k == "content" else _TOOL_CALLS)) for k, v in update.items()}
        kwargs = sweep_kwargs(m.additional_kwargs, call)
        if kwargs is not m.additional_kwargs:
            update["additional_kwargs"] = kwargs
        if all(update[k] == getattr(m, k) for k in update):
            return m
        return m.model_copy(update=update)

    def restore_message(m: Any, generation: int, values: Dict[str, str]) -> Any:
        """The answer with this call's placeholders put back in its text and in allowed tools' arguments. What the model
        wrote is remembered for the next call, also where nothing was restored: text or a tool call that comes back
        without such a record is one this middleware never saw. Tool input inside content blocks stays as the model
        wrote it (the tools run from tool_calls)."""
        if not isinstance(m, AIMessage):
            return m

        def restored(t: str, kind: str) -> str:
            # a JSON answer (structured output) is restored in its string values, so that it stays valid JSON
            if kind != "text":
                r = vault.restore(t, only=values) if restore_reasoning else t
            else:
                r = vault.restore_answer(t, only=values)
            if r:
                vault.remember(r, t, generation=generation)  # not into a vault that was cleared meanwhile
            return r

        def wrote(tool_call_id: Any, tool_input: Any) -> None:
            """A tool call's input as the model wrote it (JSON text as its value, so a block and tool_calls give one record)."""
            key = tool_key(tool_call_id)
            if not key:
                return
            if isinstance(tool_input, str):
                try:
                    tool_input = json.loads(tool_input)
                except (ValueError, RecursionError):
                    pass
            vault.remember(key, tool_input, generation=generation)

        update: Dict[str, Any] = {"content": map_content(m.content, restored, wrote=wrote)}
        if m.tool_calls:
            calls = []
            for c in m.tool_calls:
                wrote(c.get("id"), c["args"])
                calls.append({**c, "args": vault.restore_value(c["args"], only=values)} if restores_tool(c["name"]) else c)
            update["tool_calls"] = calls
        if all(update[k] == getattr(m, k) for k in update):
            return m
        return m.model_copy(update=update)

    def restore_data(x: Any, values: Dict[str, str]) -> Any:
        """JSON-like data with this call's placeholders put back in its strings and in its string keys, as in a JSON
        answer's text: {"[PERSON_1]": "[PERSON_1]"} gets the name in both (the 23rd outside review: keys stayed
        masked). A key whose value is already a key of the same dict stays as the model wrote it, so that no field is
        lost. Other values (a pydantic model or dataclass inside a list) as restore_structured reads them."""
        if isinstance(x, str):
            return vault.restore(x, only=values)
        if isinstance(x, (list, tuple)):
            out = [restore_data(y, values) for y in x]
            return out if isinstance(x, list) else tuple(out)
        if isinstance(x, dict):
            new: Dict[Any, Any] = {}
            for k, y in x.items():
                key = vault.restore(k, only=values) if isinstance(k, str) else k
                if key != k and (key in x or key in new):
                    key = k
                new[key] = restore_data(y, values)
            return new
        return restore_structured(x, values)

    def restore_structured(x: Any, values: Dict[str, str]) -> Any:
        """A structured response (create_agent's response_format) with this call's placeholders put back in its
        strings and keys, of the type it has: a dict or list, a pydantic model, a dataclass. A pydantic model is built
        again from its fields, each given under the key the model validates it by (its alias: the 24th outside review,
        B2), with its extra fields; when the restored values do not validate, the model is copied with them."""
        if isinstance(x, (str, list, tuple, dict)):
            return restore_data(x, values)
        fields = getattr(type(x), "model_fields", None)
        if isinstance(fields, dict) and callable(getattr(type(x), "model_validate", None)):
            changes = {}
            for field_name in fields:
                value = getattr(x, field_name, None)
                restored = restore_structured(value, values)
                if not _same(restored, value):
                    changes[field_name] = restored
            extra = getattr(x, "__pydantic_extra__", None) or {}
            extra_restored = restore_data(dict(extra), values) if extra else {}
            if not changes and _same(extra_restored, extra):
                return x
            data: Dict[Any, Any] = {}
            for field_name, info in fields.items():
                if field_name in changes or field_name in x.model_fields_set:
                    data[_validation_key(field_name, info)] = changes.get(field_name, getattr(x, field_name, None))
            data.update(extra_restored)
            try:
                return type(x).model_validate(data)
            except Exception:  # (a validator that refuses the restored value: the model with the values, as it is)
                return x.model_copy(update={**changes, **extra_restored})
        if dataclasses.is_dataclass(x) and not isinstance(x, type):
            changes = {f.name: restore_structured(getattr(x, f.name), values) for f in dataclasses.fields(x) if f.init}
            return x if all(changes[k] is getattr(x, k) for k in changes) else dataclasses.replace(x, **changes)
        return x

    def restore_response(response: Any, generation: int, values: Dict[str, str], parsed: Optional[Dict[int, Any]] = None) -> Any:
        """The model call's answer with this call's values back. parsed: the copies of the model's messages that the
        agent read its structured response from (see binding), each to the message as the model wrote it."""

        def written(m: Any) -> Any:
            # the message as the model wrote it, with what the agent set on the copy it read (its name)
            pair = parsed.get(id(m)) if parsed else None
            if pair is None or pair[0] is not m:
                return m
            original = pair[1]
            return original if original.name == m.name else original.model_copy(update={"name": m.name})

        if isinstance(response, AIMessage):
            return restore_message(written(response), generation, values)
        inner = getattr(response, "model_response", None)  # ExtendedModelResponse: a response with a command
        if inner is not None and dataclasses.is_dataclass(response):
            return dataclasses.replace(response, model_response=restore_response(inner, generation, values, parsed))
        result = getattr(response, "result", None)
        if isinstance(result, list) and dataclasses.is_dataclass(response):

            def restored(m: Any) -> Any:
                # the tool message that carries a structured response gets its values back as the response does
                if isinstance(m, ToolMessage) and isinstance(m.content, str):
                    content = vault.restore_answer(m.content, only=values)
                    return m if content == m.content else m.model_copy(update={"content": content})
                return restore_message(written(m), generation, values)

            changes: Dict[str, Any] = {"result": [restored(m) for m in result]}
            if getattr(response, "structured_response", None) is not None:
                changes["structured_response"] = restore_structured(response.structured_response, values)
            return dataclasses.replace(response, **changes)
        return response

    def plain_message(m: Any) -> Any:
        """A message with every data slot built once into plain JSON as a provider client writes it (_plain): its
        content (where a file block's data may stay bytes), its additional_kwargs and a model's tool-call arguments.
        That tree is what is masked and sent. Raises TypeError when a slot holds what JSON cannot write (an object that
        refers to itself, a serializer that raises, a key JSON cannot write, an object with no JSON form): the call
        fails before anything is sent."""
        if not isinstance(m, BaseMessage):
            return m
        on: Set[int] = set()
        update: Dict[str, Any] = {}
        slot = "the content"
        try:
            content = _plain_content(m.content, on)
            if content is not m.content:
                update["content"] = content
            slot = "the additional_kwargs"
            kwargs = _plain(m.additional_kwargs, True, on)
            if kwargs is not m.additional_kwargs:
                update["additional_kwargs"] = kwargs
            for field in ("tool_calls", "invalid_tool_calls"):
                calls = getattr(m, field, None)
                if not calls:
                    continue
                slot = "a tool call's arguments"
                plain = [c if not isinstance(c, dict) or a is c.get("args") else {**c, "args": a} for c in calls for a in (_plain(c.get("args") if isinstance(c, dict) else None, True, on),)]
                if any(a is not b for a, b in zip(plain, calls)):
                    update[field] = plain
        except _NotJson as e:
            raise TypeError(f"[newmean-pii] the call was not sent: {slot} of a {type(m).__name__} holds {e}, which cannot be written as JSON") from None
        return m.model_copy(update=update) if update else m

    def masked_request(request: Any) -> "tuple[Any, int, Dict[str, str], Dict[int, Any]]":
        call = Call()
        # every data slot is built into plain JSON once, before anything is masked (a pydantic model is dumped once)
        messages = [plain_message(m) for m in request.messages]
        # A value found in one message is masked in all of them, also in those before it and in the system prompt: the
        # messages are masked again while the vault learns something new (detections are kept, so only the vault is
        # applied again).
        given = getattr(request, "system_message", None)
        system = plain_message(given) if isinstance(given, SystemMessage) else given
        prompt = None if isinstance(system, SystemMessage) else getattr(request, "system_prompt", None)  # older requests
        while True:
            known = len(vault)
            call.tags.clear()
            overrides: Dict[str, Any] = {"messages": [mask_message(m, call) for m in messages]}
            if isinstance(system, SystemMessage):
                hidden = mask_message(system, call)
                if hidden is not given:
                    overrides["system_message"] = hidden
            elif isinstance(prompt, str) and prompt and hide(prompt, call) != prompt:
                overrides["system_prompt"] = hide(prompt, call)
            if len(vault) == known:
                break
        # the request's free-text fields outside the messages (tools, response format, model settings)
        overrides.update(request_fields(request, call))
        # the model's own fields that go into the request (model_kwargs, extra_body, ...): data like the model settings,
        # sent from a copy of the model for this call (see hidden_model)
        model = request.model
        if len(vault):
            model = hidden_model(model, call)
            if model is not request.model:
                overrides["model"] = model
        # the values are fixed now (checked and copied in one step), so an answer that arrives after the vault was
        # cleared and refilled still gets this call's values (and those the binding puts in, see binding)
        values = vault.bindings(call.tags, generation=call.generation)
        parsed: Dict[int, Any] = {}
        fmt = getattr(request, "response_format", None)
        if fmt is not None and len(vault):
            # the response format is bound by the agent itself: the model is given as a copy that hides the known
            # values in that binding and reads the structured response back (see binding); a model that cannot be
            # copied fails the call when the response format holds a known value (nothing is sent)
            tools = overrides.get("tools", getattr(request, "tools", None)) or []
            steps = binding(call, values, {_tool_name(t) for t in tools}, parsed)
            try:
                overrides["model"] = _binding_model(model, steps)
            except TypeError:
                if format_holds_known_values(fmt):
                    raise TypeError(
                        "[newmean-pii] the response format holds a value the vault knows, and this model cannot be "
                        "copied to hide it where the agent binds it (pass a LangChain chat model)"
                    ) from None
        return request.override(**overrides), call.generation, values, parsed

    class NewmeanPiiVaultMiddleware(AgentMiddleware):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.vault = vault

        @property
        def name(self) -> str:  # type: ignore[override]
            return f"NewmeanPiiVaultMiddleware[{name}]"

        def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            masked, generation, values, parsed = masked_request(request)
            return restore_response(handler(masked), generation, values, parsed)

        async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            masked, generation, values, parsed = masked_request(request)
            return restore_response(await handler(masked), generation, values, parsed)

    return NewmeanPiiVaultMiddleware()
