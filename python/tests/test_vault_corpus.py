"""The leak corpus (vault_corpus.json, written by gen_vault_corpus.py) run through the LangChain vault middleware.

For every case and every position it names, one model call goes through pii_middleware(detector, vault=vault)
.wrap_model_call, with a fresh vault that holds the case's known values (vault.tag) and a stand-in detector that reports
every exact occurrence of the case's "detect" values in the text it is given. Then, in what the handler receives (every
message and the system message, each written with json.dumps(default=repr), with and without ASCII escapes, and with
repr):

- no "leak" string stands, as it is written, JSON-escaped or as repr writes it;
- every "keep" string stands unchanged in the message at the position;
- the top-level object there (the data, or the JSON text when the input text is a JSON object) has "fields" keys;
- "exact" (the finite product of gate 2, corpus_product.py): what stands at the position is the text with each written
  span replaced by its placeholder and every neighbor unchanged (at a data position the data with the text replaced by
  it; at message-name the placeholder without brackets). A JSON document is compared by what it holds, since section 5
  of the guarantee note lets a changed string be written again as JSON writes it.

Positions, in the shapes LangChain gives them to the middleware (a position with two shapes runs both):

  user                   HumanMessage(text)
  system                 the request's system_message, SystemMessage(text)
  tool-text              ToolMessage(text), after the AIMessage that called the tool
  tool-data              rows: a hand-built ToolMessage whose content is the data (an object as its one row);
                         tool-node: the content LangChain's ToolNode makes of a tool's return value (JSON text, or the
                         list itself when it is content blocks)
  block-data             ToolMessage(content=[{"type": "json", "json": data}])
  assistant-unrecorded   an earlier AIMessage(text) the middleware has no record of
  tool-input-unrecorded  an earlier AIMessage's tool call the middleware has no record of: data as tool_calls args (an
                         int key: a {"type": "tool_use"} content block, because tool_calls take string keys only; a
                         list: under "items"); text as args {"input": text} and as the raw arguments of an invalid
                         tool call

Outside the messages and the system prompt (corpus_fields.py, since 1.7.2), with the text in the field:

  message-name           HumanMessage("Hello", name=text) and ChatMessage(role="user", name=text)
  tool-description       a tool's description: a StructuredTool, and a tool definition (dict)
  tool-schema            a property of a tool's input schema (description and an example): a StructuredTool with a
                         pydantic args_schema, and a tool definition (dict)
  response-format        through a real create_agent whose model records what it is bound with: ProviderStrategy,
                         ToolStrategy (JSON schema and pydantic) and the schema as it is (auto)
  call-options           the request's model_settings {"user": text}

The positions of the 23rd outside review (corpus_23rd_outside_review_fields.py, since 1.7.2):

  option-metadata        the application's metadata in model_settings: {"metadata": {"name", "id", "customer_id"}},
                         {"extra_body": {"metadata": {"name"}}}, and settings no provider defines
  option-ids             identifiers providers define in model settings: previous_response_id, conversation.id (they
                         stay as written)
  schema-literal         a literal constraint of a schema property (const, enum, default, pattern, also as an object with
                         keys a schema has) in a tool definition, a StructuredTool's JSON schema and a provider strategy
  schema-nested          a description under every keyword that holds schemas, in the same three shapes

The positions of the review group "options" (corpus_review_options.py, since 1.7.2):

  option-variables       the text under setting names that are a provider's only at another path: a stored prompt's
                         variables (prompt.variables.order, .model, .customer), extra_body.order and .only, and the
                         settings order and only
  option-numbers         the text as a JSON number in metadata, in extra_body (also in a list there) and in a setting
                         no provider defines
  option-settings        the text as a JSON number under numeric settings providers define (max_tokens, top_k, seed,
                         thinking.budget_tokens): it stays a number

The response format also runs a schema without a title (as a tool strategy and as it is) and a schema with typed fields
(a date and a pattern), as a tool strategy and a provider strategy (L7-1, A5 and L3-B5 of the review group "options").

The positions of model settings (call-options and option-*) also run the model's own fields (since 1.7.2, the gate run
of 1.7.1): the same settings in the model's model_kwargs and extra_body (FieldModel), read where the request the model
builds holds them (_request_fields_of). option-variables also runs the text as the system prompt and as a message given
in model_kwargs (model-system, model-messages: gate 8 of the release standard), and under a provider's identifier names
away from the exact paths where it defines them, one level below a field it defines, below the paths of a container
and of a gateway's routing and in extra_body (misplaced-ids, model-misplaced-ids: the Python sibling of gate 8, item 5).

(message-options is the AI SDK's only.) What the handler received is then also written with its tools (as the
provider is sent them), the JSON schema of its response format and its model settings.

With "int_keys", object keys that are decimal digits become int keys first.

Cases that fail at the head they were recorded at are listed in vault_corpus_open.json ("<id>:<position>") and run as
strict expected failures: one that starts to pass fails the run until it is taken off that list.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Optional

import pytest

pytest.importorskip("langchain.agents.middleware")

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware.types import ModelRequest, ModelResponse  # noqa: E402
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage, ChatMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402
from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402
from pydantic import Field, create_model  # noqa: E402

from newmean_pii import Span, Vault  # noqa: E402
from newmean_pii.langchain import _request_fields_of, pii_middleware  # noqa: E402

try:
    from langgraph.prebuilt.tool_node import msg_content_output as tool_node_content
except ImportError:  # pragma: no cover - the same rule, written out
    from langchain_core.tools.base import TOOL_MESSAGE_BLOCK_TYPES

    def tool_node_content(output):
        if isinstance(output, str) or (
            isinstance(output, list) and all(isinstance(x, dict) and x.get("type") in TOOL_MESSAGE_BLOCK_TYPES for x in output)
        ):
            return output
        try:
            return json.dumps(output, ensure_ascii=False)
        except Exception:
            return str(output)


HERE = Path(__file__).resolve().parent
CORPUS = json.loads((HERE / "vault_corpus.json").read_text(encoding="utf-8"))
OPEN = json.loads((HERE / "vault_corpus_open.json").read_text(encoding="utf-8"))
CALL_ID = "call_1"
INPUT_ID = "call_9"
# positions outside the messages and the system prompt; message-options is the AI SDK's only
FIELDS = {
    "message-name", "tool-description", "tool-schema", "response-format", "call-options",
    "option-metadata", "option-ids", "schema-literal", "schema-nested", "option-variables", "option-numbers", "option-settings",
}
NOT_HERE = {"message-options"}


class CorpusDetector:
    """A stand-in detector: every exact occurrence of each (value, label) in the text it is given; where two overlap,
    the one that starts first (then the longer one)."""

    def __init__(self, values):
        self.values = [(value, label) for value, label in values]
        self.sensitivity = 0.0

    def detect(self, text):
        spans = []
        for value, label in self.values:
            i = text.find(value)
            while i >= 0:
                spans.append(Span(i, i + len(value), label, "model"))
                i = text.find(value, i + 1)
        spans.sort(key=lambda s: (s.start, -s.end))
        out = []
        for s in spans:
            if not out or s.start >= out[-1].end:
                out.append(s)
        return out


def int_keyed(v):
    """v with every object key that is decimal digits (written as int would write it) as an int key."""
    if isinstance(v, dict):
        return {(int(k) if isinstance(k, str) and re.fullmatch("[0-9]+", k) and str(int(k)) == k else k): int_keyed(x) for k, x in v.items()}
    if isinstance(v, list):
        return [int_keyed(x) for x in v]
    return v


def strings(v):
    """Every string of a value, keys included (a key that is not a string as str writes it)."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from strings(x)
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k if isinstance(k, str) else str(k)
            yield from strings(x)


def written_forms(s):
    """s as it is written, inside a JSON string (with and without ASCII escapes) and inside a Python repr."""
    forms = {s, json.dumps(s, ensure_ascii=False)[1:-1], json.dumps(s)[1:-1], repr(s)[1:-1]}
    both = "'" + '"'
    forms.add(repr(s + both)[1:-(len(repr(both)) - 1)])  # inside a repr that escapes the apostrophe
    return [f for f in forms if f]


def sent_tool(t):
    """A tool as the provider is sent it (a framework tool written as a function definition)."""
    return t if isinstance(t, dict) else convert_to_openai_tool(t)


class FieldModel(GenericFakeChatModel):
    """A chat model with fields of its own that go into the request, as langchain-openai's do: model_kwargs and
    extra_body (its _default_params, which _request_fields_of reads)."""

    model_kwargs: dict = Field(default_factory=dict)
    extra_body: Optional[dict] = None

    @property
    def _default_params(self):
        return {**({"extra_body": self.extra_body} if self.extra_body is not None else {}), **self.model_kwargs}


def model_shape(name, own, where):
    """A shape whose text is in the model's own fields (own: its model_kwargs and extra_body); where: the position in
    the request fields the model gives."""
    return (name, [HumanMessage("Hello")], {"model": FieldModel(messages=iter([]), **own)}, lambda r: where(_request_fields_of(r.model)))


def field_shapes(case, position):
    """[(shape name, messages, request fields, where the position is)] for a position outside the messages."""
    text = case["text"]
    hello = [HumanMessage("Hello")]
    fn = lambda r: sent_tool(r.tools[0])["function"]
    if position == "message-name":
        at = lambda r: r.messages[0].name
        return [
            ("human", [HumanMessage("Hello", name=text)], {}, at),
            ("chat", [ChatMessage(content="Hello", role="user", name=text)], {}, at),
        ]
    if position == "tool-description":
        lookup = StructuredTool(name="lookup", description=text, args_schema={"type": "object", "properties": {"q": {"type": "string"}}}, func=lambda **_k: "ok")
        definition = {"type": "function", "function": {"name": "lookup", "description": text, "parameters": {"type": "object", "properties": {}}}}
        at = lambda r: fn(r)["description"]
        return [("tool", hello, {"tools": [lookup]}, at), ("definition", hello, {"tools": [definition]}, at)]
    if position == "tool-schema":
        args = create_model("Lookup", q=(str, Field(description=text, examples=[text])))
        lookup = StructuredTool(name="lookup", description="Looks up a customer", args_schema=args, func=lambda **_k: "ok")
        parameters = {"type": "object", "properties": {"q": {"type": "string", "description": text, "examples": [text]}}, "required": ["q"]}
        definition = {"type": "function", "function": {"name": "lookup", "description": "Looks up a customer", "parameters": parameters}}
        at = lambda r: fn(r)["parameters"]["properties"]["q"]
        return [("tool-pydantic", hello, {"tools": [lookup]}, at), ("definition", hello, {"tools": [definition]}, at)]
    if position == "call-options":
        return [
            ("model-settings", hello, {"model_settings": {"user": text}}, lambda r: r.model_settings),
            model_shape("model-kwargs", {"model_kwargs": {"user": text}}, lambda q: q),
            model_shape("model-extra-body", {"extra_body": {"user": text}}, lambda q: q["extra_body"]),
        ]
    if position == "option-metadata":
        return [
            # (the field "name" is where the position is; the others, named like identifiers, are checked for leaks)
            ("settings-metadata", hello, {"model_settings": {"metadata": {"name": text, "id": text, "customer_id": text}}}, lambda r: r.model_settings["metadata"]["name"]),
            ("extra-body", hello, {"model_settings": {"extra_body": {"metadata": {"name": text}}}}, lambda r: r.model_settings["extra_body"]["metadata"]["name"]),
            ("settings-unknown", hello, {"model_settings": {"name": text, "customer_id": text, "user_key": text}}, lambda r: r.model_settings["name"]),
            model_shape("model-metadata", {"model_kwargs": {"metadata": {"name": text, "id": text, "customer_id": text}}}, lambda q: q["metadata"]["name"]),
            model_shape("model-extra-body", {"extra_body": {"metadata": {"name": text}}}, lambda q: q["extra_body"]["metadata"]["name"]),
        ]
    if position == "option-ids":
        return [
            ("previous-response-id", hello, {"model_settings": {"previous_response_id": text}}, lambda r: r.model_settings["previous_response_id"]),
            ("conversation", hello, {"model_settings": {"conversation": {"id": text}}}, lambda r: r.model_settings["conversation"]["id"]),
            model_shape("model-previous-response-id", {"model_kwargs": {"previous_response_id": text}}, lambda q: q["previous_response_id"]),
            model_shape("model-conversation", {"model_kwargs": {"conversation": {"id": text}}}, lambda q: q["conversation"]["id"]),
        ]
    if position == "option-variables":
        variables = {"id": "pmpt_1", "version": "2", "variables": {"order": text, "model": text, "customer": text}}
        return [
            # (the field "order" is where the position is; the others are checked for leaks)
            ("prompt-variables", hello, {"model_settings": {"prompt": variables}}, lambda r: r.model_settings["prompt"]["variables"]["order"]),
            ("extra-body", hello, {"model_settings": {"extra_body": {"order": text, "only": text}}}, lambda r: r.model_settings["extra_body"]["order"]),
            ("routing-names", hello, {"model_settings": {"order": text, "only": text}}, lambda r: r.model_settings["order"]),
            model_shape("model-prompt-variables", {"model_kwargs": {"prompt": variables}}, lambda q: q["prompt"]["variables"]["order"]),
            model_shape("model-extra-body", {"extra_body": {"order": text, "only": text}}, lambda q: q["extra_body"]["order"]),
            # the system prompt and messages given in model_kwargs, which langchain-anthropic sends instead of what it
            # builds from the messages (gate 8 of the release standard, item 1)
            model_shape("model-system", {"model_kwargs": {"system": text}}, lambda q: q["system"]),
            model_shape("model-messages", {"model_kwargs": {"messages": [{"role": "user", "content": text}]}}, lambda q: q["messages"][0]["content"]),
            # (the Python sibling of release gate 8, item 5: a provider's identifier names one level below a field it
            # defines, a field below the paths of a container and of a gateway's routing, and those names in extra_body
            # are no provider's identifiers, in model settings and in the model's own fields)
            ("misplaced-ids", hello, {"model_settings": {
                "reasoning": {"effort": "low", "previous_response_id": text}, "text": {"format": {"type": "text", "signature": text}},
                "thinking": {"type": "enabled", "budget_tokens": 1024, "item_id": text}, "container": {"id": "cntr_1", "note": text},
                "extra_body": {"previous_response_id": text, "provider": {"order": ["openai"], "note": text}},
            }}, lambda r: r.model_settings["reasoning"]["previous_response_id"]),
            model_shape("model-misplaced-ids", {"model_kwargs": {
                "reasoning": {"previous_response_id": text}, "stream_options": {"include_usage": True, "item_id": text}, "provider": {"note": text},
            }}, lambda q: q["reasoning"]["previous_response_id"]),
        ]
    if position in ("option-numbers", "option-settings"):
        n = json.loads(text)
        if position == "option-settings":
            settings = {"max_tokens": n, "top_k": n, "seed": n, "thinking": {"type": "enabled", "budget_tokens": n}}
            return [
                ("settings", hello, {"model_settings": settings}, lambda r: r.model_settings),
                model_shape("model-settings", {"model_kwargs": dict(settings)}, lambda q: q),
            ]
        return [
            ("metadata", hello, {"model_settings": {"metadata": {"customer_number": n}}}, lambda r: r.model_settings["metadata"]),
            ("extra-body", hello, {"model_settings": {"extra_body": {"customer_number": n, "rows": [{"phone": n}]}}}, lambda r: r.model_settings["extra_body"]),
            ("settings-unknown", hello, {"model_settings": {"customer_number": n}}, lambda r: r.model_settings),
            model_shape("model-metadata", {"model_kwargs": {"metadata": {"customer_number": n}}}, lambda q: q["metadata"]),
            model_shape("model-extra-body", {"extra_body": {"customer_number": n, "rows": [{"phone": n}]}}, lambda q: q["extra_body"]),
        ]
    if position in ("schema-literal", "schema-nested"):
        out = []
        for name, schema, where in schema_shapes(text, position):
            definition = {"type": "function", "function": {"name": "lookup", "description": "Looks up a customer", "parameters": schema}}
            structured = StructuredTool(name="lookup", description="Looks up a customer", args_schema=schema, func=lambda **_k: "ok")
            out.append(("definition-" + name, hello, {"tools": [definition]}, lambda r, w=where: w(fn(r)["parameters"])))
            out.append(("tool-" + name, hello, {"tools": [structured]}, lambda r, w=where: w(fn(r)["parameters"])))
            out.append(("provider-" + name, hello, {"response_format": ProviderStrategy({"title": "Answer", **schema})}, lambda r, w=where: w(r.response_format.schema_spec.json_schema)))
        return out
    raise ValueError("unknown position " + position)


def schema_shapes(text, position):
    """[(shape name, JSON schema, where the position is in that schema)]. schema-literal: a property with the text in a
    literal constraint (const, enum, default, pattern, also as an object with keys a schema has), which must stay as
    written; schema-nested: the text in a description under every keyword that holds schemas."""
    if position == "schema-literal":
        literals = [
            ("const", {"type": "string", "const": text}),
            ("const-object", {"type": "object", "const": {"description": text, "title": text}}),
            ("enum", {"type": "string", "enum": [text, "other"]}),
            ("enum-object", {"type": "object", "enum": [{"description": text}]}),
            ("default", {"type": "string", "default": text}),
            ("default-object", {"type": "object", "default": {"description": text, "examples": [text]}}),
            ("pattern", {"type": "string", "pattern": text}),
        ]
        return [(name, {"type": "object", "properties": {"q": q}, "required": ["q"]}, lambda s: s["properties"]["q"]) for name, q in literals]

    def s():
        return {"type": "string", "description": text}

    nested = {
        "type": "object",
        "properties": {
            "list": {"type": "array", "items": s(), "prefixItems": [s()], "contains": s(), "unevaluatedItems": s(), "additionalItems": s()},
            "tuple": {"type": "array", "items": [s()]},
            "any": {"anyOf": [s()], "oneOf": [s()], "allOf": [s()], "not": s()},
            "cond": {"if": s(), "then": s(), "else": s()},
            "map": {"type": "object", "additionalProperties": s(), "patternProperties": {"^x": s()}, "propertyNames": s(), "unevaluatedProperties": s()},
            "deps": {"type": "object", "dependentSchemas": {"a": s()}, "dependencies": {"b": s(), "c": ["a"]}},
            "blob": {"type": "string", "contentSchema": s()},
        },
        "$defs": {"d": s()},
        "definitions": {"e": s()},
    }
    return [("nested", nested, lambda schema: schema)]


def agent_shapes(case):
    """[(shape name, response format)] for the response format of a real agent."""
    text = case["text"]
    schema = {"title": "Answer", "type": "object", "description": text, "properties": {"a": {"type": "string", "description": text}}, "required": ["a"]}
    model = create_model("Answer", __doc__=text, a=(str, Field(description=text)))
    # (the review group "options": a schema without a title gets a name LangChain makes up, A5; typed fields, L3-B5)
    titleless = {k: v for k, v in schema.items() if k != "title"}
    typed = create_model("Answer", __doc__=text, a=(str, Field(description=text)), born=(datetime.date, Field(description=text)),
                         code=(str, Field(description=text, pattern="^[a-z]+$")))
    return [
        ("provider", ProviderStrategy(schema)),
        ("tool", ToolStrategy(schema)),
        ("tool-pydantic", ToolStrategy(model)),
        ("auto", schema),
        ("tool-titleless", ToolStrategy(titleless)),
        ("auto-titleless", titleless),
        ("tool-typed", ToolStrategy(typed)),
        ("provider-typed", ProviderStrategy(typed)),
    ]


def shapes(case, position):
    """[(shape name, messages, system message, where the position is)] for one case at one position. where: a
    function from the request the handler received to the masked object at the position."""
    text = case["text"]
    data = case.get("data")
    if data is not None and case["int_keys"]:
        data = int_keyed(data)
    hello = HumanMessage("Hello")
    asked = AIMessage("", tool_calls=[{"id": CALL_ID, "name": "lookup", "args": {}}])
    msg = lambda r, i: r.messages[i]
    if position == "user":
        return [("user", [HumanMessage(text)], None, lambda r: msg(r, 0).content)]
    if position == "system":
        return [("system", [hello], SystemMessage(text), lambda r: r.system_message.content)]
    if position == "tool-text":
        return [("tool-text", [hello, asked, ToolMessage(text, tool_call_id=CALL_ID)], None, lambda r: msg(r, 2).content)]
    if position == "tool-data":
        rows = data if isinstance(data, list) else [data]
        first = (lambda c: c[0]) if isinstance(data, dict) else (lambda c: c)
        content = tool_node_content(data)
        return [
            ("rows", [hello, asked, ToolMessage(content=rows, tool_call_id=CALL_ID)], None, lambda r: first(msg(r, 2).content)),
            ("tool-node", [hello, asked, ToolMessage(content=content, tool_call_id=CALL_ID)], None, lambda r: msg(r, 2).content),
        ]
    if position == "block-data":
        block = ToolMessage(content=[{"type": "json", "json": data}], tool_call_id=CALL_ID)
        return [("block", [hello, asked, block], None, lambda r: msg(r, 2).content[0]["json"])]
    if position == "assistant-unrecorded":
        return [("assistant", [hello, AIMessage(text), HumanMessage("Thanks")], None, lambda r: msg(r, 1).content)]
    if position == "tool-input-unrecorded":
        done = ToolMessage("done", tool_call_id=INPUT_ID)
        if data is None:
            in_args = AIMessage("", tool_calls=[{"id": INPUT_ID, "name": "lookup", "args": {"input": text}}])
            invalid = AIMessage("", invalid_tool_calls=[{"type": "invalid_tool_call", "id": INPUT_ID, "name": "lookup", "args": text, "error": None}])
            return [
                ("args-input", [hello, in_args, done], None, lambda r: msg(r, 1).tool_calls[0]["args"]["input"]),
                ("raw-arguments", [hello, invalid, done], None, lambda r: msg(r, 1).invalid_tool_calls[0]["args"]),
            ]
        if case["int_keys"]:
            block = AIMessage(content=[{"type": "tool_use", "id": INPUT_ID, "name": "lookup", "input": data}])
            return [("tool-use-block", [hello, block, done], None, lambda r: msg(r, 1).content[0]["input"])]
        if isinstance(data, dict):
            call = AIMessage("", tool_calls=[{"id": INPUT_ID, "name": "lookup", "args": data}])
            return [("args", [hello, call, done], None, lambda r: msg(r, 1).tool_calls[0]["args"])]
        call = AIMessage("", tool_calls=[{"id": INPUT_ID, "name": "lookup", "args": {"items": data}}])
        return [("args-items", [hello, call, done], None, lambda r: msg(r, 1).tool_calls[0]["args"]["items"])]
    raise ValueError("unknown position " + position)


def json_object_keys(text):
    """The distinct keys of the top-level object of a JSON text, or None when it is not a JSON object."""
    try:
        pairs = json.loads(text, object_pairs_hook=lambda p: ("object", p))
    except (ValueError, RecursionError):
        return None
    if not (isinstance(pairs, tuple) and len(pairs) == 2 and pairs[0] == "object"):
        return None
    return {k for k, _v in pairs[1]}


def check(case, messages, system, where, fields=None, expect=None):
    """The problems of one shape: what the handler received against the case's leak, keep, fields and exact output.
    fields: request fields outside the messages (tools, model_settings, and "model": a model with fields of its own).
    expect: what must stand at the position."""
    seen = []
    mw = pii_middleware(CorpusDetector(case["detect"]), vault=fresh_vault(case))
    fields = dict(fields or {})
    model = fields.pop("model", None) or GenericFakeChatModel(messages=iter([]))
    request = ModelRequest(model=model, messages=messages, system_message=system, **fields)
    mw.wrap_model_call(request, lambda req: (seen.append(req), ModelResponse(result=[AIMessage("ok")]))[1])
    assert len(seen) == 1, "the handler was not called once"
    got = seen[0]
    payload = {
        "messages": [m.model_dump() for m in got.messages],
        "system": None if got.system_message is None else got.system_message.model_dump(),
        "tools": [sent_tool(t) for t in got.tools],
        "model_settings": got.model_settings,
        # (what the model's own fields put into the request)
        "model_fields": _request_fields_of(got.model),
        # (the JSON schema a provider strategy sends)
        "response_format": getattr(getattr(got.response_format, "schema_spec", None), "json_schema", None),
    }
    return problems_of(case, payload, [repr(got.messages), repr(got.system_message)], lambda: where(got), expect)


def check_agent(case, response_format):
    """The problems of one response format: what a real agent's model is bound with and given, against the case's
    leak and keep."""
    record = []

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            record.append({"tools": [sent_tool(t) for t in tools], "kwargs": kwargs})
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            record.append({"messages": [m.model_dump() for m in messages]})
            bound = next(r for r in reversed(record) if "kwargs" in r)
            native = "response_format" in bound["kwargs"]
            # (the tool of the response format by the name the agent gave it: a schema without a title gets one made up)
            name = next((t["function"]["name"] for t in bound["tools"]), "Answer")
            args = {"a": "x", "born": "1990-01-02", "code": "x"}
            answer = AIMessage(json.dumps(args)) if native else AIMessage("", tool_calls=[{"id": "s1", "name": name, "args": args}])
            self.messages = iter([answer])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    mw = pii_middleware(CorpusDetector(case["detect"]), vault=fresh_vault(case))
    agent = create_agent(Model(messages=iter([])), tools=[], response_format=response_format, middleware=[mw])
    try:
        agent.invoke({"messages": [HumanMessage("Hello")]})
    except Exception as e:
        # a format this LangChain refuses without the middleware too (LangChain 1.0 and a schema without a title as it
        # is) does not apply here; any other failure is a problem
        try:
            create_agent(Model(messages=iter([])), tools=[], response_format=response_format).invoke({"messages": [HumanMessage("Hello")]})
        except Exception:
            return []
        return ["the agent failed with the middleware only: " + type(e).__name__ + " " + str(e)[:200]]
    bound = [r for r in record if "kwargs" in r][-1]

    def where():
        native = bound["kwargs"].get("response_format")
        if native:
            return native["json_schema"]
        return next(t["function"] for t in bound["tools"])

    return problems_of(case, {"record": record}, [], where)


def problems_of(case, payload, reprs, where, expect=None):
    """What is wrong with what the provider received (payload, and reprs of its objects) for the case. expect: what
    must stand at the position (see expected_at), or None."""
    written = [
        json.dumps(payload, default=repr, ensure_ascii=False),
        json.dumps(payload, default=repr),
        repr(payload),
    ] + reprs + list(strings(payload))
    problems = []
    for s in case["leak"]:
        forms = [f for f in written_forms(s) if any(f in w for w in written)]
        if forms:
            problems.append("leak " + repr(s) + ("" if forms == [s] else " (as " + ", ".join(repr(f) for f in forms) + ")"))
    try:
        target = where()
    except (AttributeError, KeyError, IndexError, TypeError, StopIteration) as e:
        return problems + ["the position is not where it was: " + type(e).__name__ + " " + str(e)]
    here = list(strings(target)) + [json.dumps(target, default=repr, ensure_ascii=False)]
    for s in case["keep"]:
        if not any(s in w for w in here):
            problems.append("lost " + repr(s))
    if "fields" in case:
        if isinstance(target, str):
            before = json_object_keys(case["text"])
            after = json_object_keys(target)
            if before is not None and (after is None or len(after) != case["fields"]):
                problems.append("fields: " + ("not a JSON object any more" if after is None else str(len(after))) + ", not " + str(case["fields"]))
        elif isinstance(target, dict) and len(target) != case["fields"]:
            problems.append("fields: " + str(len(target)) + ", not " + str(case["fields"]))
    if expect is not None and canon(target) != canon(expect):
        shown = json.dumps(expect, default=repr, ensure_ascii=False)
        problems.append("not the exact output; expected " + (shown if len(shown) <= 400 else shown[:400] + "..."))
    if problems:
        shown = json.dumps(target, default=repr, ensure_ascii=False)
        problems.append("received " + (shown if len(shown) <= 400 else shown[:400] + "..."))
    return problems


def canon(x):
    """A value for comparing outputs: a JSON document (an object or an array, as text) by what it holds, at every
    depth, so that the rewrites the guarantee note decides (section 5: a changed string written again as JSON writes
    it) count as the same; any other text exactly."""
    if isinstance(x, str):
        t = x.strip()
        if t[:1] in ("{", "["):
            try:
                parsed = json.loads(t)
            except (ValueError, RecursionError):
                return x
            if isinstance(parsed, (dict, list)):
                return canon(parsed)
        return x
    if isinstance(x, (list, tuple)):
        return [canon(v) for v in x]
    if isinstance(x, dict):
        return {json.dumps(canon(k), ensure_ascii=False, sort_keys=True): canon(v) for k, v in x.items()}
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    return x


def placeholder_marks(case):
    """The placeholder of each known value of a fresh vault, in the order they are tagged."""
    count, out = {}, []
    for e in case["known"]:
        family = e["label"][:-3] if e["label"].endswith("_L2") else e["label"]
        count[family] = count.get(family, 0) + 1
        out.append("[" + family + "_" + str(count[family]) + "]")
    return out


def replaced(v, old, new):
    """v with every string and key equal to old replaced by new."""
    if isinstance(v, str):
        return new if v == old else v
    if isinstance(v, list):
        return [replaced(x, old, new) for x in v]
    if isinstance(v, dict):
        return {replaced(k, old, new): replaced(x, old, new) for k, x in v.items()}
    return v


def expected_at(case, position):
    """What must stand at the position when the case has an exact output ("exact": the text; at a data position the
    case's data with the text replaced by it, or "exact_data"; at message-name the placeholders without brackets), or
    None."""
    if "exact" not in case and "exact_data" not in case:
        return None
    text_positions = ("user", "system", "tool-text", "assistant-unrecorded", "tool-description")
    if position in text_positions or (position == "tool-input-unrecorded" and "data" not in case):
        return case.get("exact")
    if position == "message-name":
        out = case.get("exact")
        for mark in placeholder_marks(case):
            out = out.replace(mark, mark[1:-1])
        return out
    if position in ("tool-data", "block-data", "tool-input-unrecorded") and "data" in case:
        if "exact_data" in case:
            return case["exact_data"]
        return replaced(case["data"], case["text"], case["exact"])
    return None


def fresh_vault(case):
    v = Vault()
    for e in case["known"]:
        v.tag(e["label"], e["value"])
    return v


def params():
    out = []
    for case in CORPUS["cases"]:
        for position in case["where"]:
            if position in NOT_HERE:
                continue
            key = case["id"] + ":" + position
            marks = [pytest.mark.xfail(strict=True, reason="open: listed in vault_corpus_open.json (at 43da689)")] if key in OPEN else []
            out.append(pytest.param(case, position, id=key, marks=marks))
    return out


def test_open_list_names_corpus_runs():
    runs = {c["id"] + ":" + p for c in CORPUS["cases"] for p in c["where"] if p not in NOT_HERE}
    assert CORPUS["version"] == 1
    assert len(OPEN) == len(set(OPEN)) and sorted(OPEN) == OPEN
    assert [k for k in OPEN if k not in runs] == []


@pytest.mark.parametrize("case, position", params())
def test_corpus(case, position):
    failures = []
    if position == "response-format":
        for name, response_format in agent_shapes(case):
            failures += [name + ": " + p for p in check_agent(case, response_format)]
    elif position in FIELDS:
        for name, messages, fields, where in field_shapes(case, position):
            failures += [name + ": " + p for p in check(case, messages, None, where, fields, expected_at(case, position))]
    else:
        for name, messages, system, where in shapes(case, position):
            failures += [name + ": " + p for p in check(case, messages, system, where, expect=expected_at(case, position))]
    assert not failures, case["id"] + " at " + position + " (" + case["from"] + ")" + chr(10) + chr(10).join(failures)
