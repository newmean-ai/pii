"""Doubling tests for the LangChain vault middleware (REVIEW-GUIDE rule 4).

Each test times one model call through pii_middleware(detector, vault=vault).wrap_model_call at sizes n, 2n and 4n
along one axis, on one path, and fails when one doubling costs more than three times as much. Each size is timed three
times (a fresh vault and middleware each time, made outside the timing, the garbage collector off while it runs) and
the shortest time counts; a doubling that looks too slow is timed again, seven times, before it fails. Before the
timing, one call checks that the path did its work: a known value of the text does not reach the handler.

Paths: the positions of the leak corpus (test_vault_corpus.py): user, system, tool-text, tool-data (rows),
block-data, assistant-unrecorded, and tool input with no record as text (args {"input": text}) and as data (args).
An axis that is about text gives the data paths {"note": text}; an axis that is about JSON data gives the text paths
its JSON text.

Axes:
  text-length          a line with every spelling the vault finds (escapes, a URL, a form field, a number's spelling)
                       repeated n times
  known-values         n known values of four labels in the vault, one text
  keys                 an object with n keys, a known value inside two thirds of them (as written and percent-encoded)
  keys-one-placeholder n keys that are all the same known value with other white space (all become one placeholder,
                       told apart by " #2", " #3", ...)
  repeated-known       a known value and its other spellings repeated n times
  repeated-detected    a value the detector finds, repeated n times
  repeated-placeholder placeholder-like text next to a known value, repeated n times
  repeated-overlap     "Lee " n times with a known "Lee Lee" (a value that overlaps itself)
  long-value-once      a text the detector reports whole, " ." n times, a name and ", " n times: the value it holds
                       (". . . .Kim Minsu") is found once after long partial matches (1.7.1 gate 7)
  long-value-overlap   known values of n words that repeat themselves, in a text that repeats them: in any ASCII
                       case, with other white space, a URL whose path must be written as it is, and capitals of another
                       spelling (each place where a value may start is looked at once)
  long-value-lines     a known value of n lines of two words, in a text that writes its lines on one line, twice
                       (one pass over the text with every ", " as a space; 1.7.1 gate 7)
  nested-objects       four objects nested n deep, a known value at the bottom of each
  nested-arrays        32 arrays nested n deep around known values
  open-brackets        n opening brackets that never close, before a JSON document and a known value
  readings-percent     a known value percent-encoded k times, 128 times in a text: k = 1, 2, 4 (read: encodings
                       inside one another are read up to four steps deep) and k = 8, 16, 32 (beyond, written as it is;
                       the known value must not reach the handler at k = 1 only)
  readings-escape      a known value written with Unicode escapes, then percent-encoded k - 1 times (the same sizes)
  numbers-joined       a line repeated n times with numbers next to exponents and dots in every reading (a form field,
                       percent and Unicode escapes), signed numbers in other spellings, and an address and a spaced
                       phone number after "<digit>e+" (which are not numbers, so they are masked)
  dates                a known date at the start of ISO timestamps and in Python date reprs, repeated n times
  model-json-numbers   an array of n objects with known values as JSON numbers (in a model's text each becomes a quoted
                       placeholder, so that the text stays valid JSON)
  readings-mixed       a value two steps deep next to escapes of every other kind (a line break, a path, a form field,
                       a percent escape, a Python repr escape), n times
  repr-escapes         a dict and a bytes value as Python's str() writes them, n times
  case-width-forms     a name in capitals, an e-mail address in full width, a Hangul name as conjoining jamo, n times
  comma-line-break     an address held with a comma, written over two lines, n times
  identical-keys       JSON text that repeats a known value as a key n times and a spelling of it n times (keys that
                       were the same before masking stay the same; the spelling gets " #2")
  typed-rows           n rows with a type of their own ({"type": "contact", ...}): data, every field and key counted
  base64-looking       a form field of n words that looks like base64 and holds a known value spelled with "+"
  python-values        n dataclasses, pydantic models, sets, frozensets, bytes and Decimals, and keys json.dumps writes
                       (a number, a float, a str Enum), all built into the JSON a provider client writes (gate 4 of the
                       1.7.1 standard; tool-data, block-data and other-fields only: JSON has no text for them)
  capitals-white-space a name and an address in capitals with other white space, n times (23rd outside review, A4)
  known-capitals       n known values whose capitals are another spelling (not ASCII), ten of them in capitals with other
                       white space in one text (A4)
  full-width-escapes   escapes written with full-width characters (a percent sign, hex digits, a backslash, a form
                       field's plus) and full-width signs in ordinary text, n times (A4)
  white-space-escapes  a name with a run of white space of each kind between it and its escaped part, n times (A5)
  white-space-run      one run of white space of length n between a name and its escaped part, also as a span the
                       detector reports, and runs of length n between other escapes (A5)
  typed-payloads       n json blocks whose data has a type of its own, and objects typed text with a name and a key
                       that are known values (the 23rd outside review, A2)
  typed-depth          a payload nested n deep in json and tool_result blocks, a typed object with data at the bottom
                       (tool-data, block-data and other-fields)
  serialized-values    n dates, datetimes, times, Enum values, str Enum keys, UUIDs and pydantic models with a computed
                       field (the 23rd outside review, A3; tool-data, block-data and other-fields)
  review171-readings   the readings of the 1.7.1 review, n times: capitals as Turkish, Greek and German write them, a
                       Thai name before a vowel sign, a full-width exponent, a number spelled longer than a known value's
                       reach around an escape, a name with a variation selector next to full width, a full-width "@"
                       behind a percent escape, and spans a model found with a period and brackets around them
  known-language-capitals
                       n known values whose capitals Turkish and German write in their own way, ten of them so written
  number-with-escape   one number of n digits around escapes (L4-REACH: a number is read whole)
  marks-after-value    a name in conjoining jamo with a run of n marks after it, full width elsewhere (L5-A2)
  rows-like-blocks     n rows typed file, document, message, ... without a block's own content: data, read by the model
                       (the review of 1.7.1, L3-A1)
  pydantic-written     n pydantic models written otherwise than their fields: an annotated serializer, extra keys, a
                       model serializer (the 24th outside review, A3; tool-data, block-data and other-fields)
  pydantic-depth       pydantic models nested n deep, a serializer that writes a known value at the bottom (only the
                       outermost model is checked as it is written; A3)
  recorded-answer      an earlier answer of n sentences that the app passes back restored, sent as the record the
                       middleware keeps of it (path assistant-recorded only; L4-R1)
  natives-review       capitals in other spellings (the full Unicode uppercase, Greek without accents, the capital sharp
                       s), a value of two Hangul syllables with particles and inside longer words, values followed by a
                       variation selector and a combining mark, custom values that merge, n times (the natives review)
  marks-run            a known value followed by a run of n combining marks (taken in whole), twice

typed-rows, base64-looking, python-values and the three axes above also run on other-fields: the top of an AIMessage's
additional_kwargs (data, beside its tool calls).

Fields outside the messages (since 1.7.2), with the axis's text in the field: message-name (a HumanMessage's name),
tool-description and tool-schema (a tool definition), response-format (a ToolStrategy's schema), call-options
(model_settings), option-metadata (the application's metadata in model_settings), option-nested (a provider's
identifier names below fields it defines, read against the exact paths where it defines them: the Python sibling of
release gate 8, item 5), schema-nested (descriptions under the keywords that hold schemas) and model-fields (the model's own model_kwargs and extra_body, sent from a copy of the
model for the call: the gate run of 1.7.1; with a system prompt and messages in model_kwargs since gate 8), timed along
the axes in FIELD_AXES. The data axes of Python and pydantic values also run on model-data: the data in the model's own
fields (model_kwargs, a message in it, and extra_body), built once into JSON (gate 8 of the release standard).

The readings depth is timed in two ranges because the cost has a step where the depth passes the fourth step: at k = 5
a text costs 2.1 (percent) to 2.6 (escape) times as much per character as at k = 4, and from there the cost per
character goes down slowly (measured 2026-11-25 at 43da689). A step is not growth, but a doubling across it (4 to 8)
measures the step: 3.4 to 3.5 times as much for readings-escape on the text paths. JSON inside a JSON string is left
out of the readings depth: its backslashes double at every step, so the text grows exponentially with the depth.
"""
from __future__ import annotations

import dataclasses
import datetime
import enum
import gc
import itertools
import json
import time
import unicodedata
import uuid
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

import pytest

pytestmark = pytest.mark.timing  # PII_SKIP_TIMING=1 skips every test of this file (conftest.py)

pytest.importorskip("langchain.agents.middleware")

import pydantic  # noqa: E402

from langchain.agents.middleware.types import ModelRequest, ModelResponse  # noqa: E402
from langchain.agents.structured_output import ToolStrategy  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from newmean_pii import Span, Vault  # noqa: E402
from newmean_pii.langchain import _request_fields_of, pii_middleware  # noqa: E402

BS = chr(92)  # a backslash
NL = chr(10)
LIMIT = 3.0  # what one doubling may cost at most
RUNS = 3
RETRY_RUNS = 7

JOSE = "Jos" + chr(0xE9) + " " + chr(0xC1) + "lvarez"
JOSE_U = "Jos" + BS + "u00e9 " + BS + "u00c1lvarez"
KNOWN = [
    ("PERSON", "Kim Minsu"),
    ("EMAIL", "kim@example.com"),
    ("ADDRESS", "12 Main St" + NL + "Seoul 04524"),
    ("PHONE", "821023456789"),
    ("ID_NUMBER", "4524"),
    ("PERSON", JOSE),
    ("USERNAME", "CORP" + BS + "minsu.kim"),
]
DETECT = [("Park Jisoo", "PERSON"), ("010-9876-5432", "PHONE")]
# one line with every spelling the vault finds, and look-alikes it keeps
UNIT = (
    "Kim Minsu wrote to kim%40example.com from 12 Main St, Seoul 04524 (tel 8.21023456789e11, q=Kim+Minsu&id=4524). "
    "name=" + JOSE_U + ", login CORP" + BS + "minsu.kim, " + '{"to": "12 Main St' + BS + 'nSeoul 04524"} '
    "Park Jisoo at 010-9876-5432; v1.4524.3 and 1e+4524. "
)
# numbers next to exponents and dots in every reading, signed numbers in other spellings, and values that are not
# numbers after "<digit>e+"
NUMBERS_UNIT = (
    "q=Unit+4E+12+Main+St%2C+Seoul&ext=1e%2B4524&w=1e" + BS + "u002B4524&v=4524%2E7 and delta -4.524e3, -4524; "
    "ext 2e+415 555 0123. "
)
NUMBERS_KNOWN = [("ID_NUMBER", "4524"), ("ADDRESS", "12 Main St, Seoul"), ("PHONE", "415 555 0123")]
# a known date (1990-01-02) at the start of ISO timestamps and in Python reprs, next to another date format that stays
DATES_UNIT = "born 1990-01-02T00:00:00.000Z, {'dob': datetime.date(1990, 1, 2)} and 1990-01-02T09:30:00+09:00; not 01/02/1990. "
WHITE = [chr(c) for c in (0x20, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003,
                          0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)]
PATHS = ["user", "system", "tool-text", "tool-data", "block-data", "assistant-unrecorded", "tool-input-text", "tool-input-data"]
# (model-data: the data in the model's own fields, built once into JSON and sent from a copy: gate 8 of the release
# standard)
DATA_PATHS = ["tool-data", "block-data", "other-fields", "model-data"]
# (option-metadata and schema-nested: the paths of the 23rd outside review)
FIELD_PATHS = ["message-name", "tool-description", "tool-schema", "response-format", "call-options", "option-metadata", "option-nested", "schema-nested", "model-fields"]
FIELD_AXES = ["text-length", "known-values", "keys", "repeated-known", "repeated-placeholder", "open-brackets"]


class Detector:
    """A stand-in detector: every occurrence of each (value, label) in the text it is given (linear in the text)."""

    def __init__(self, values):
        self.values = list(values)
        self.sensitivity = 0.0

    def detect(self, text):
        spans = []
        for value, label in self.values:
            i = text.find(value)
            while i >= 0:
                spans.append(Span(i, i + len(value), label, "model"))
                i = text.find(value, i + len(value))
        return sorted(spans, key=lambda s: s.start)


class FieldModel(GenericFakeChatModel):
    """A chat model with fields of its own that go into the request, as langchain-openai's do (its _default_params)."""

    model_kwargs: dict = pydantic.Field(default_factory=dict)
    extra_body: dict = pydantic.Field(default_factory=dict)

    @property
    def _default_params(self):
        return {"model": "fake", "extra_body": self.extra_body, **self.model_kwargs}


def fields_for(path, text):
    """(messages, request fields) with text in a field outside the messages."""
    hello = [HumanMessage("Hello")]
    definition = lambda description, parameters: {"type": "function", "function": {"name": "lookup", "description": description, "parameters": parameters}}
    schema = {"type": "object", "properties": {"q": {"type": "string", "description": text, "examples": [text]}}}
    if path == "message-name":
        return [HumanMessage("Hello", name=text)], {}
    if path == "tool-description":
        return hello, {"tools": [definition(text, {"type": "object", "properties": {}})]}
    if path == "tool-schema":
        return hello, {"tools": [definition("Looks up a customer", schema)]}
    if path == "response-format":
        return hello, {"response_format": ToolStrategy({"title": "Answer", "description": text, **schema})}
    if path == "call-options":
        return hello, {"model_settings": {"user": text}}
    if path == "option-metadata":
        return hello, {"model_settings": {"metadata": {"name": text, "id": text}, "extra_body": {"metadata": {"name": text}}}}
    if path == "option-nested":
        # (a provider's identifier names below fields it defines, read against the exact paths: the Python sibling of
        # release gate 8, item 5)
        return hello, {"model_settings": {
            "reasoning": {"effort": "low", "previous_response_id": text}, "thinking": {"type": "enabled", "signature": text},
            "tools": [{"type": "mcp", "server_label": "s", "note": text}], "container": {"id": "c", "note": text},
        }}
    if path == "model-fields":
        # (with the message-like keys a provider package spreads over the request: gate 8 of the release standard)
        own = {"model_kwargs": {"user": text, "metadata": {"name": text}, "prompt": {"id": "p", "variables": {"note": text}},
                                "system": [{"type": "text", "text": text}], "messages": [{"role": "user", "content": text}]},
               "extra_body": {"note": text}}
        return hello, {"model": FieldModel(messages=iter([]), **own)}
    if path == "schema-nested":
        nested = {
            "type": "object",
            "properties": {"q": {"anyOf": [{"type": "string", "description": text}], "const": {"note": "kept"}}, "r": {"type": "array", "items": {"description": text}}},
            "$defs": {"d": {"description": text}},
        }
        return hello, {"tools": [definition("Looks up a customer", nested)]}
    raise ValueError(path)


def messages_for(path, text, data):
    """(messages, system message) with text or data at the path."""
    hello = HumanMessage("Hello")
    asked = AIMessage("", tool_calls=[{"id": "call_1", "name": "lookup", "args": {}}])
    done = ToolMessage("done", tool_call_id="call_9")
    if path == "user":
        return [HumanMessage(text)], None
    if path == "system":
        return [hello], SystemMessage(text)
    if path == "tool-text":
        return [hello, asked, ToolMessage(text, tool_call_id="call_1")], None
    if path == "tool-data":
        return [hello, asked, ToolMessage(content=data if isinstance(data, list) else [data], tool_call_id="call_1")], None
    if path == "block-data":
        return [hello, asked, ToolMessage(content=[{"type": "json", "json": data}], tool_call_id="call_1")], None
    if path in ("assistant-unrecorded", "assistant-recorded"):
        return [hello, AIMessage(text), HumanMessage("Thanks")], None
    if path == "tool-input-text":
        return [hello, AIMessage("", tool_calls=[{"id": "call_9", "name": "lookup", "args": {"input": text}}]), done], None
    if path == "tool-input-data":
        args = data if isinstance(data, dict) else {"items": data}
        return [hello, AIMessage("", tool_calls=[{"id": "call_9", "name": "lookup", "args": args}]), done], None
    if path == "other-fields":
        kwargs = data if isinstance(data, dict) else {"items": data}
        return [hello, AIMessage("ok", additional_kwargs=kwargs), HumanMessage("Thanks")], None
    raise ValueError(path)


# ---------------------------------------------------------------------------------------------------------------------
# axes: name -> (sizes, make(n) -> (known values, detector values, text, data))
# ---------------------------------------------------------------------------------------------------------------------


def text_axis(text):
    return text, {"note": text}


def data_axis(data):
    return json.dumps(data, ensure_ascii=False), data


def text_length(n):
    return (KNOWN, DETECT) + text_axis(UNIT * n)


def known_values(n):
    extra = []
    for i in range(n):
        extra.append([("PERSON", "Given%d Family%d" % (i, i)), ("EMAIL", "user%d@example.org" % i),
                      ("PHONE", str(4155550000 + i)), ("ID_NUMBER", "ZX-%05d-K" % i)][i % 4])
    text = UNIT * 4 + ", ".join(v for _label, v in extra[:16])
    return (KNOWN + extra, DETECT) + text_axis(text)


def keys(n):
    data = {}
    for i in range(n):
        key = ["kim@example.com/%d" % i, "id%d" % i, "Kim%%20Minsu %d" % i][i % 3]
        data[key] = ["Kim Minsu", i, 821023456789, "x"][i % 4]
    return (KNOWN, DETECT) + data_axis(data)


def keys_one_placeholder(n):
    spellings = ("Kim" + "".join(gap) + "Minsu" for size in itertools.count(1) for gap in itertools.product(WHITE, repeat=size))
    data = {key: i for i, key in zip(range(n), spellings)}
    return (KNOWN, DETECT) + data_axis(data)


def repeated_known(n):
    return (KNOWN, DETECT) + text_axis("Kim Minsu, Kim%20Minsu, Kim+Minsu, 8.21023456789e11; " * n)


def repeated_detected(n):
    return (KNOWN, DETECT) + text_axis("Park Jisoo, " * n)


def repeated_placeholder(n):
    return (KNOWN, DETECT) + text_axis("[PERSON_1] and Kim Minsu [PERSON_9] " * n)


def repeated_overlap(n):
    return ([("PERSON", "Lee Lee")], []) + text_axis("Lee " * n)


def long_value_once(n):
    text = " ." * n + "Kim Minsu" + ", " * n
    return ([], [(text, "PERSON")]) + text_axis(text)


def _words(w, n):
    return " ".join(w[i % len(w)] for i in range(n))


def long_value_lines(n):
    return ([("ADDRESS", NL.join(["Ha ha"] * n))], []) + text_axis("Ha ha " * (2 * n))


def long_value_overlap(n):
    text = NL.join(["hA Ha " * n, ("Ha" + chr(9) + "ha" + chr(9)) * n, "HTTPS://X.IO/" + "https://x.io/" * (2 * n - 1), "WEISS " * (2 * n)])
    known = [("PERSON", _words(["Ha", "ha"], n)), ("URL", "https://x.io/" * n), ("PERSON", _words(["Wei" + chr(0xDF)], n))]
    return (known, []) + text_axis(text)


def nested_objects(n):
    def nest(depth):
        v = {"name": "Kim Minsu", "phone": 821023456789}
        for _ in range(depth):
            v = {"a": v}
        return v

    return (KNOWN, DETECT) + data_axis([nest(n) for _ in range(4)])


def nested_arrays(n):
    def nest(depth):
        v = ["Kim Minsu", "kim@example.com", 821023456789]
        for _ in range(depth):
            v = [v]
        return v

    return (KNOWN, DETECT) + data_axis([nest(n) for _ in range(32)])


def open_brackets(n):
    return (KNOWN, DETECT) + text_axis("[" * n + ' {"name": "' + JOSE_U + '"} Kim Minsu')


def percent(value, k):
    for _ in range(k):
        value = quote(value, safe="")
    return value


def readings_percent(k):
    return (KNOWN, DETECT) + text_axis(" & ".join("ref=" + percent("Kim Minsu", k) for _ in range(128)))


def readings_escape(k):
    return (KNOWN, DETECT) + text_axis(" & ".join("ref=" + percent(JOSE_U, k - 1) for _ in range(128)))


def numbers_joined(n):
    return (NUMBERS_KNOWN, DETECT) + text_axis(NUMBERS_UNIT * n)


def dates(n):
    return ([("DATE_OF_BIRTH", "1990-01-02")], DETECT) + text_axis(DATES_UNIT * n)


def model_json_numbers(n):
    text = "[" + ", ".join('{"id": %d, "phone": %d, "d": -4.524e3}' % (i, 2025550148 if i % 2 == 0 else 3000000 + i) for i in range(n)) + "]"
    return ([("PHONE", "2025550148"), ("ID_NUMBER", "4524")], DETECT, text, json.loads(text))


MIXED = ("line" + BS + "nC:" + BS + BS + "tmp q=a+b x%20y {'k': 'x" + BS + "xa0y'} mail kim%2540example.com now. ")
REPR = ("{'name': 'Kim" + BS + "xa0Minsu', 'raw': b'Jos" + BS + "xc3" + BS + "xa9 Garc" + BS + "xc3" + BS + "xada'} ")
FORMS = ("KIM MINSU wrote from " + "".join(chr(ord(c) + 0xFEE0) for c in "kim@example.com") + " to "
         + unicodedata.normalize("NFD", "김민수") + "님 today. ")
GARCIA = "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a"


def readings_mixed(n):
    return ([("EMAIL", "kim@example.com")], []) + text_axis(MIXED * n)


def repr_escapes(n):
    return ([("PERSON", "Kim Minsu"), ("PERSON", GARCIA)], []) + text_axis(REPR * n)


def case_width_forms(n):
    return ([("PERSON", "Kim Minsu"), ("EMAIL", "kim@example.com"), ("PERSON", "김민수")], []) + text_axis(FORMS * n)


def comma_line_break(n):
    return ([("ADDRESS", "12 Main St, Seoul")], []) + text_axis(("Ship to 12 Main St" + NL + "Seoul today. ") * n)


def identical_keys(n):
    pairs = ['"Kim Minsu": %d' % i for i in range(n)] + ['"Kim%%20Minsu": %d' % i for i in range(n)]
    return (KNOWN, DETECT) + text_axis("{" + ", ".join(pairs) + "}")


def typed_rows(n):
    rows = [{"type": "contact", "name": "Kim Minsu", "id": "kim@example.com/%d" % i, "kim@example.com %d" % i: i} for i in range(n)]
    return (KNOWN, DETECT) + data_axis(rows)


def base64_looking(n):
    return (KNOWN, DETECT) + text_axis("Kim+Minsu+asked+" + "+".join("note%d" % i for i in range(n)))


@dataclasses.dataclass
class Row:
    name: str
    tags: frozenset
    raw: bytes
    amount: Decimal


class RowKey(str, enum.Enum):
    KIM = "kim@example.com"


def python_values(n):
    # (keys: the kinds json.dumps writes, since 1.7.2 the only ones a call may hold; a key it cannot write fails the call)
    data = {}
    for i in range(n):
        data[(821023456789 * 1000 + i, i + 0.5, RowKey.KIM if i == 2 else "kim@example.com/%d" % i, i)[i % 4]] = [
            Row("Kim Minsu", frozenset({"kim@example.com", i}), b"to kim@example.com", Decimal("821023456789")),
            {"kim@example.com", "x%d" % i},
            b"Kim Minsu %d" % i,
            Decimal("821023456789"),
        ][i % 4]
    return (KNOWN, DETECT, None, data)


UPPER_JOSE, UPPER_GARCIA = "JOS" + chr(0xC9), "GARC" + chr(0xCD) + "A"
MALAGA = "Calle Mayor 5, M" + chr(0xE1) + "laga"
CAPITALS = "Signed: " + UPPER_JOSE + " " + chr(9) + " " + UPPER_GARCIA + ", to CALLE MAYOR 5" + NL + "  M" + chr(0xC1) + "LAGA; "


def wide(s):
    return "".join(chr(ord(c) + 0xFEE0) for c in s)


# escapes written with full-width characters and full-width signs in ordinary text (A4)
WIDE_ESCAPES = ("mail kim" + chr(0xFF05) + "40example.com, name=Kim" + chr(0xFF0B) + wide("Minsu") + " or Kim" + chr(0xFF3C)
                + "xa0Minsu at " + wide("50%") + " off " + wide("1+1") + ". ")
# a run of white space of each kind between a name and its escaped part (A5)
WHITE_RUNS = "Contact: Kim" + " " * 45 + "%4Dinsu, %4Bim" + NL * 45 + "Minsu and KIM" + chr(0x3000) * 45 + "%4DINSU. "


def capitals_white_space(n):
    return ([("PERSON", GARCIA), ("ADDRESS", MALAGA)], []) + text_axis(CAPITALS * n)


def known_capitals(n):
    known = [("PERSON", "Jos" + chr(0xE9) + "x%d Garc" % i + chr(0xED) + "a%d" % i) for i in range(n)]
    text = ", ".join(UPPER_JOSE + "X%d " % i + chr(9) + " " + UPPER_GARCIA + "%d" % i for i in range(10)) + " wrote. " + UNIT * 4
    return (known, []) + text_axis(text)


def t(*cps):
    return "".join(chr(c) for c in cps)


# the readings of the 1.7.1 review (group "readings"), the same line as js/test/vault-growth.test.ts
R171_KNOWN = [
    ("PERSON", "Emine " + chr(0x15E) + "ahin"),
    ("PERSON", t(0x395, 0x3BB, 0x3AD, 0x3BD, 0x3B7) + " " + t(0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3BF, 0x3C0, 0x3BF, 0x3CD, 0x3BB, 0x3BF, 0x3C5)),
    ("ADDRESS", "M" + chr(0xFC) + "llerstra" + chr(0xDF) + "e 5, 10115 Berlin"),
    ("PERSON", t(0xE2A, 0xE21)),
    ("ID_NUMBER", "4524"),
    ("PERSON", t(0x6E21, 0x8FBA)),
    ("EMAIL", "kim@example.com"),
]
R171_LINE = (
    "M" + t(0xFC, 0x15F) + "teri: EM" + chr(0x130) + "NE " + chr(0x15E) + "AH" + chr(0x130) + "N, " + t(0x395, 0x39B, 0x395, 0x39D, 0x397) + " "
    + t(0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A5) + ", M" + chr(0xDC) + "LLERSTRA" + chr(0x1E9E) + "E 5, 10115 BERLIN; "
    + t(0xE01, 0xE23, 0xE38, 0xE13, 0xE32, 0xE2A, 0xE21, 0xE31, 0xE04, 0xE23) + " x=" + wide("1e+4524") + " y=4.52400000000000000000e%2B03 "
    + t(0x6E21, 0x8FBA, 0xE0100, 0x3055, 0x3093) + wide("(x)") + " mail kim%EF%BC%8540example.com. I'm Kim Minsu. Call (Lee Jisoo) now. "
)
R171_DETECT = [("Kim Minsu.", "PERSON"), ("(Lee Jisoo)", "PERSON")]


def review171_readings(n):
    return (R171_KNOWN, R171_DETECT) + text_axis(R171_LINE * n)


def known_language_capitals(n):
    known = [("PERSON", "Fatih%d Wei%s%d" % (i, chr(0xDF), i)) for i in range(n)]
    # (in Turkish capitals, and in German capitals with a capital sharp s)
    names = [
        "FATIH%d WEI%s%d" % (i, chr(0x1E9E), i) if i % 2 else "FAT%sH%d WE%sSS%d" % (chr(0x130), i, chr(0x130), i)
        for i in range(10)
    ]
    return (known, []) + text_axis(", ".join(names) + " wrote. " + UNIT * 4)


def number_with_escape(n):
    return ([("ID_NUMBER", "4524")], []) + text_axis("x=4.524" + "0" * n + "e%2B03 and 1" + "0" * n + "%2E5 y")


def marks_after_value(n):
    kim = t(0xAE40, 0xBBFC, 0xC218)
    return ([("PERSON", kim)], []) + text_axis(unicodedata.normalize("NFD", kim) + chr(0x301) * n + " " + wide("(x)"))


def full_width_escapes(n):
    return ([("PERSON", "Kim Minsu"), ("EMAIL", "kim@example.com")], []) + text_axis(WIDE_ESCAPES * n)


def white_space_escapes(n):
    return ([("PERSON", "Kim Minsu")], []) + text_axis(WHITE_RUNS * n)


def _cps(*c):
    return "".join(chr(x) for x in c)


# the natives review (lens 5): see natives-review above
JIWON = _cps(0xC9C0, 0xC6D0)
WATANABE = _cps(0x6E21, 0x8FBA)
KIM_KO = _cps(0xAE40, 0xBBFC, 0xC218)
GREEK_CAPS = _cps(0x39D, 0x399, 0x39A, 0x39F, 0x3A3, 0x20, 0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A3)
NATIVES = (
    JIWON + _cps(0xB2D8, 0xAED8, 0xC11C) + " " + JIWON + _cps(0xAE08, 0xC744) + " " + JIWON + _cps(0xD588, 0xB2E4) + "; "
    + GREEK_CAPS + ", ANNA WEISS and ANNA WEI" + _cps(0x1E9E) + "; " + WATANABE + _cps(0xE0100, 0x3055, 0x3093) + ", "
    + KIM_KO + _cps(0x301, 0xB2D8) + ", AB-1234-CD. "
)
NATIVES_KNOWN = [
    ("PERSON", JIWON),
    ("PERSON", _cps(0x39D, 0x3AF, 0x3BA, 0x3BF, 0x3C2, 0x20, 0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3CC, 0x3C0, 0x3BF, 0x3C5, 0x3BB, 0x3BF, 0x3C2)),
    ("PERSON", "Anna Wei" + _cps(0xDF)),
    ("PERSON", WATANABE),
    ("PERSON", KIM_KO),
    ("CUSTOMER", "AB-1234"),
    ("CUSTOMER", "1234-CD"),
]


def natives_review(n):
    return (NATIVES_KNOWN, []) + text_axis(NATIVES * n)


def marks_run(n):
    text = WATANABE + _cps(0x301) * n + _cps(0x3055, 0x3093) + ", " + KIM_KO + _cps(0x302E) * n + "."
    return ([("PERSON", WATANABE), ("PERSON", KIM_KO)], []) + text_axis(text)


def white_space_run(n):
    value = "Kim" + " " * n + "%4Dinsu"
    text = "Contact: " + value + ", %41" + NL * n + "%42 and " + chr(9) * n + "x%43."
    return ([("PERSON", "Kim Minsu")], [(value, "PERSON")]) + text_axis(text)
def typed_payloads(n):
    # (the 23rd outside review, A2) json blocks whose data has a type of its own, and objects typed text with data
    data = [
        {"type": "json", "data": {"type": "text", "name": "Kim Minsu", "Kim Minsu %d" % i: i}} if i % 2 else
        {"type": "text", "name": "Kim Minsu", "kim@example.com %d" % i: i}
        for i in range(n)
    ]
    return (KNOWN, DETECT) + data_axis(data)


def typed_depth(n):
    # a payload nested n deep in json blocks and tool_result blocks, a typed object with data at the bottom
    v = [{"type": "text", "name": "Kim Minsu", "Kim Minsu": "open"}]
    for i in range(n):
        v = [{"type": "json", "data": v}] if i % 2 else [{"type": "tool_result", "tool_use_id": "c%d" % i, "content": v}]
    return (KNOWN, DETECT) + data_axis(v)


class Tier(enum.Enum):
    KIM = "kim@example.com"
    OTHER = "other"


class Kim(str, enum.Enum):
    MAIL = "kim@example.com"


class Contact(pydantic.BaseModel):
    first: str
    last: str
    dob: datetime.date

    @pydantic.computed_field
    @property
    def display_name(self) -> str:
        return self.first + " " + self.last


def serialized_values(n):
    # (the 23rd outside review, A3) Python values that are written out when the provider serializes them: dates,
    # datetimes and times, Enum values and keys, UUIDs, and pydantic models with a computed field
    data = {}
    for i in range(n):
        key = ("k%d" % i, Kim.MAIL if i % 8 == 1 else "e%d" % i)[i % 2]
        data[key] = [
            datetime.date(1990, 1, 2),
            datetime.datetime(1990, 1, 2, 9, i % 60),
            Tier.KIM,
            uuid.UUID(int=i),
            Contact(first="Kim", last="Minsu", dob=datetime.date(1990, 1, 2)),
            datetime.time(9, i % 60),
        ][i % 6]
    return (KNOWN + [("DATE_OF_BIRTH", "1990-01-02")], DETECT, None, data)


def rows_like_blocks(n):
    # (the review of 1.7.1, L3-A1) rows typed like blocks without a block's own content: data, every field and key
    kinds = ["file", "document", "message", "url", "text", "json", "image", "function", "thinking", "tool_result"]
    owner = {"type": "user", "name": "Kim Minsu", "login": "kim@example.com"}
    rows = [{"type": kinds[i % len(kinds)], "id": "kim@example.com/%d" % i, "name": "Kim Minsu %d.pdf" % i, "owned_by": owner,
             "kim@example.com %d" % i: i} for i in range(n)]
    return (KNOWN, DETECT) + data_axis(rows)


Joined = Annotated[tuple, pydantic.PlainSerializer(lambda v: " ".join(v), return_type=str)]


class Display(pydantic.BaseModel):
    display: Joined


class Open(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow")
    plan: str = "basic"


class Whole(pydantic.BaseModel):
    first: str
    last: str

    @pydantic.model_serializer
    def write(self):
        return {"who": self.first + " " + self.last}


def pydantic_written(n):
    # (the 24th outside review, A3) models a provider writes otherwise than their fields
    data = {"m%d" % i: [Display(display=("Kim", "Minsu")), Open(**{"kim@example.com %d" % i: "open"}), Whole(first="Kim", last="Minsu")][i % 3]
            for i in range(n)}
    return (KNOWN, DETECT, None, data)


class Node(pydantic.BaseModel):
    name: Joined
    child: "Node | None" = None


def pydantic_depth(n):
    node = Node(name=("Kim", "Minsu"))
    for i in range(n):
        node = Node(name=("Lee", "Sora%d" % i), child=node)
    return (KNOWN, DETECT, None, {"tree": node})


def recorded_answer(n):
    return ([("PERSON", "Kim Minsu"), ("EMAIL", "kim@example.com")], []) + text_axis("Kim Minsu wrote to kim@example.com. " * n)


AXES = {  # name: (sizes, make, a known value of the text that must not reach the handler at the first size[, paths])
    "text-length": ([8, 16, 32], text_length, "Kim Minsu"),
    "known-values": ([400, 800, 1600], known_values, "Given0 Family0"),
    "keys": ([300, 600, 1200], keys, "kim@example.com"),
    "keys-one-placeholder": ([500, 1000, 2000], keys_one_placeholder, "Minsu"),
    "repeated-known": ([150, 300, 600], repeated_known, "Minsu"),
    "repeated-detected": ([300, 600, 1200], repeated_detected, None),
    "repeated-placeholder": ([200, 400, 800], repeated_placeholder, "Kim Minsu"),
    "repeated-overlap": ([1000, 2000, 4000], repeated_overlap, "Lee Lee"),
    # (the detector reports the value: not in a system message or a json block, where only known values are looked for)
    "long-value-once": ([2000, 4000, 8000], long_value_once, "Kim Minsu", [p for p in PATHS if p not in ("system", "block-data")]),
    "long-value-overlap": ([500, 1000, 2000], long_value_overlap, "hA Ha"),
    "long-value-lines": ([500, 1000, 2000], long_value_lines, "Ha ha Ha"),
    "nested-objects": ([100, 200, 400], nested_objects, "Kim Minsu"),
    "nested-arrays": ([100, 200, 400], nested_arrays, "Kim Minsu"),
    "open-brackets": ([5000, 10000, 20000], open_brackets, "Kim Minsu"),
    "readings-percent": ([1, 2, 4], readings_percent, "Minsu"),
    "readings-percent-beyond": ([8, 16, 32], readings_percent, None),
    "readings-escape": ([1, 2, 4], readings_escape, "lvarez"),
    "readings-escape-beyond": ([8, 16, 32], readings_escape, None),
    "numbers-joined": ([25, 50, 100], numbers_joined, "Main+St"),
    "dates": ([400, 800, 1600], dates, "1990, 1, 2"),
    "model-json-numbers": ([250, 500, 1000], model_json_numbers, "2025550148"),
    "readings-mixed": ([50, 100, 200], readings_mixed, "kim%2540"),
    "repr-escapes": ([50, 100, 200], repr_escapes, "xa0Minsu"),
    "case-width-forms": ([50, 100, 200], case_width_forms, "KIM MINSU"),
    "comma-line-break": ([100, 200, 400], comma_line_break, "Main St"),
    "identical-keys": ([1000, 2000, 4000], identical_keys, "Kim Minsu"),
    "typed-rows": ([200, 400, 800], typed_rows, "kim@example.com", PATHS + ["other-fields"]),
    "base64-looking": ([1000, 2000, 4000], base64_looking, "Kim+Minsu", PATHS + ["other-fields"]),
    "python-values": ([200, 400, 800], python_values, "kim@example.com", DATA_PATHS),
    "capitals-white-space": ([100, 200, 400], capitals_white_space, UPPER_GARCIA),
    "known-capitals": ([300, 600, 1200], known_capitals, UPPER_GARCIA + "0"),
    "full-width-escapes": ([50, 100, 200], full_width_escapes, "40example"),
    "white-space-escapes": ([50, 100, 200], white_space_escapes, "insu"),
    "white-space-run": ([20000, 40000, 80000], white_space_run, "insu"),
    "typed-payloads": ([200, 400, 800], typed_payloads, "Kim Minsu", PATHS + ["other-fields"]),
    "typed-depth": ([25, 50, 100], typed_depth, "Kim Minsu", DATA_PATHS),
    "serialized-values": ([200, 400, 800], serialized_values, "Kim Minsu", DATA_PATHS),
    "review171-readings": ([25, 50, 100], review171_readings, "40example"),
    "known-language-capitals": ([300, 600, 1200], known_language_capitals, "FAT" + chr(0x130) + "H0"),
    "number-with-escape": ([20000, 40000, 80000], number_with_escape, "x=4.524"),
    "marks-after-value": ([20000, 40000, 80000], marks_after_value, t(0x1100, 0x1175)),
    "rows-like-blocks": ([200, 400, 800], rows_like_blocks, "kim@example.com", PATHS + ["other-fields"]),
    "pydantic-written": ([200, 400, 800], pydantic_written, "Kim Minsu", DATA_PATHS),
    "pydantic-depth": ([25, 50, 100], pydantic_depth, "Kim Minsu", DATA_PATHS),
    "recorded-answer": ([400, 800, 1600], recorded_answer, "Kim Minsu", ["assistant-recorded"]),
    "natives-review": ([50, 100, 200], natives_review, "ANNA WEISS"),
    "marks-run": ([2000, 4000, 8000], marks_run, WATANABE),
}
RUN = [(a, p) for a, v in AXES.items() for p in (v[3] if len(v) > 3 else PATHS)]


def one_call(made, path, seen=None):
    """The time of one model call; the vault, the middleware and the request are made before it starts. seen: a list
    that gets the request the handler received."""
    known, found, text, data = made
    vault = Vault()
    for label, value in known:
        vault.tag(label, value)
    if path == "assistant-recorded":
        vault.remember(text, vault.hide(text))  # what the model wrote, for the restored text the app passes back
    mw = pii_middleware(Detector(found), vault=vault)
    if path in FIELD_PATHS:
        messages, fields = fields_for(path, text)
        model = fields.pop("model", None) or GenericFakeChatModel(messages=iter([]))
        request = ModelRequest(model=model, messages=messages, **fields)
    elif path == "model-data":
        own = {"model_kwargs": {"acme": data, "messages": [{"role": "user", "content": "Hello", "rows": data}]}, "extra_body": {"metadata": data}}
        request = ModelRequest(model=FieldModel(messages=iter([]), **own), messages=[HumanMessage("Hello")])
    else:
        messages, system = messages_for(path, text, data)
        request = ModelRequest(model=GenericFakeChatModel(messages=iter([])), messages=messages, system_message=system)

    def handler(req):
        if seen is not None:
            seen.append(req)
        return ModelResponse(result=[AIMessage("ok")])

    gc.disable()
    try:
        start = time.perf_counter()
        mw.wrap_model_call(request, handler)
        return time.perf_counter() - start
    finally:
        gc.enable()


def timings(made, path, runs):
    return [min(one_call(m, path) for _ in range(runs)) for m in made]


def ratios(times):
    return [b / a for a, b in zip(times, times[1:])]


def received(request):
    """What the handler received, as JSON text (and repr where JSON cannot write a value or a key)."""
    payload = [m.model_dump() for m in request.messages] + [None if request.system_message is None else request.system_message.model_dump()]
    # (the fields outside the messages that go to the provider; a tool strategy sends its tools, which are among them;
    # what the model's own fields put into the request)
    payload += [request.tools, request.model_settings, _request_fields_of(request.model)]
    try:
        return json.dumps(payload, default=repr, ensure_ascii=False)
    except TypeError:
        return repr(payload)


RUNS_OF = RUN + [(a, p) for a in FIELD_AXES for p in FIELD_PATHS]


@pytest.mark.parametrize("axis, path", RUNS_OF, ids=[a + ":" + p for a, p in RUNS_OF])
def test_doubling(axis, path):
    sizes, make, value = AXES[axis][:3]
    made = [make(n) for n in sizes]
    seen = []
    one_call(made[0], path, seen)  # warm-up, and the path does its work: the known value is hidden
    if value is not None:
        assert value not in received(seen[0]), "%s on %s: %r reached the handler" % (axis, path, value)
    times = timings(made, path, RUNS)
    if max(ratios(times)) > LIMIT:
        times = timings(made, path, RETRY_RUNS)  # timed again before it counts
    got = ratios(times)
    shown = ", ".join("%s: %.2f ms" % (n, t * 1000) for n, t in zip(sizes, times))
    print("growth %s %s | %s | ratios %s" % (axis, path, shown, " ".join("%.2f" % r for r in got)))
    assert max(got) <= LIMIT, "%s on %s: one doubling costs %.2f times as much (%s)" % (axis, path, max(got), shown)
