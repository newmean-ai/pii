"""Gate 4 of the 1.7.1 release standard: the boundary between the
vault middleware and the provider, in Python LangChain. "Each data slot is materialized once as provider-compatible JSON,
keys and values masked, and that tree is sent. Exemptions are verified at their authorized paths and masking everywhere
else. Unsafe serialization sends zero requests. Width is folded after each decoding step; composed spans are verified on
the reverse-order regression."

Run through a real create_agent (invoke and ainvoke) whose chat model records what it is given (the messages and the
model settings the agent binds it with), with a stand-in detector (every exact occurrence of a held value in the text it
is given) and a fresh vault per call that holds vault_boundary_cases.json "held" and a USERNAME shaped like an
identifier. What the recording model receives is what a provider adapter writes into the HTTP body. Checked on every
call:

  plain    every value that reaches the provider is plain JSON: str, finite int and float, bool, None, list (a tuple is
           written as one) and dict with str keys; no dataclass, pydantic model, date, Decimal, set, Enum, bytes, NaN,
           key that is not a string, cycle. Bytes may stand only where LangChain defines file data.
  leaks    no held value (in any ASCII letter case) in what a permissive adapter writes of it (JSON, with what JSON
           cannot write written as its JSON form or its str()), outside an identifier's authorized path.

Groups:

  plain JSON and unsafe input   values a tool returns or an application passes that JSON writes in another form (or
           not at all) must reach the provider as masked plain JSON; input JSON cannot write (a cycle, a serializer that
           raises) must fail the call with nothing sent; an object JSON cannot write but str() can (a tuple key, a class
           with __str__) may do either. LangChain writes a tool's result that is not content with json.dumps or str(),
           and a ToolMessage writes a content item that is not a dict with str(): when the middleware is given plain
           JSON already, the fixture's expectation is "materialize" (the framework serialized it).
  identifiers   each name of "identifiers" stays as written at its authorized path and is masked when it is planted with
           a held value anywhere else (data payloads at any depth, tool input, typed rows, block types the framework
           does not have, model settings of an unknown provider, extra_body, metadata, a stored prompt's variables,
           additional_kwargs).
  readings      a held e-mail address written through decoding steps with full-width characters between them: the text
           the provider receives is exactly "expect" (the whole source span replaced, the neighbours intact), with the
           stand-in detector and with none (the vault's readings alone). Cases with three or more decoding steps are best
           effort (D1): their ids start with "best-effort" (-k guaranteed runs the others).

The same cases run in npm (js/test/vault-boundary.test.ts). Failures are written out in full: each is a finding for the
fixers, not a flake.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import enum
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

pytest.importorskip("langchain.agents.middleware")

import pydantic  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import AgentMiddleware  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from newmean_pii import Span, Vault  # noqa: E402
from newmean_pii import langchain as _lc  # noqa: E402
from newmean_pii.langchain import pii_middleware  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parent / "vault_boundary_cases.json").read_text(encoding="utf-8"))
HELD: List[Tuple[str, str]] = [(h["label"], h["value"]) for h in CASES["held"]]
PERSON, EMAIL, PHONE, DOB = "Kim Minsu", "kim@example.com", "821023456789", "1990-01-02"
# a held value shaped like an identifier: it stands at identifiers' authorized paths and is planted under their names
ID = "kimminsu_77"
ALL_HELD: List[Tuple[str, str]] = HELD + [("USERNAME", ID)]
for _v in (PERSON, EMAIL, PHONE, DOB):
    assert any(v == _v for _, v in HELD), f"vault_boundary_cases.json has no held {_v}"

MODES = ("sync", "async")


class Finder:
    """A stand-in detector: every exact occurrence of a held value in the text it is given; of two that overlap, the one
    that starts first (the longer of two that start together). The vault handling is the middleware's own."""

    def __init__(self, values: List[Tuple[str, str]]) -> None:
        self.values = values
        self.sensitivity = 0.0

    def detect(self, text: str) -> List[Span]:
        spans = []
        for label, value in self.values:
            i = text.find(value)
            while i >= 0:
                spans.append(Span(i, i + len(value), label, "model"))
                i = text.find(value, i + 1)
        spans.sort(key=lambda s: (s.start, -s.end))
        out: List[Span] = []
        for s in spans:
            if not out or s.start >= out[-1].end:
                out.append(s)
        return out


class Off:
    """A detector that finds nothing: what is masked is what the vault already holds (gate 3 runs this way)."""

    sensitivity = 0.0

    def detect(self, text: str) -> List[Span]:
        return []


DETECTORS = {"finder": Finder(ALL_HELD), "off": Off()}


def fresh_vault() -> Vault:
    vault = Vault()
    for label, value in ALL_HELD:
        vault.tag(label, value)
    return vault


# ------------------------------------------------------------------------------------------------------------- checks

Path_ = Tuple[Any, ...]


def show(path: Path_) -> str:
    return "$" if not path else ".".join(str(p) for p in path)


def at(x: Any, path: Path_) -> Any:
    for k in path:
        if isinstance(x, dict):
            x = x.get(k)
        elif isinstance(x, (list, tuple)) and isinstance(k, int) and k < len(x):
            x = x[k]
        else:
            return None
    return x


def non_plain(x: Any, binary: Callable[[Path_, Any], bool] = lambda p, parent: False) -> List[str]:
    """Where x holds a value that is not plain JSON, as "path: what". binary(path, parent): bytes may stand there."""
    out: List[str] = []
    stack: List[int] = []

    def walk(v: Any, path: Path_, parent: Any) -> None:
        if v is None or isinstance(v, bool):
            return
        if type(v) is str or type(v) is int:
            return
        if type(v) is float:
            if not math.isfinite(v):
                out.append(f"{show(path)}: float {v}")
            return
        if isinstance(v, (bytes, bytearray)) and binary(path, parent):
            return
        if type(v) in (list, tuple, dict):
            if id(v) in stack:
                out.append(f"{show(path)}: cycle")
                return
            stack.append(id(v))
            if type(v) is dict:
                for k, y in v.items():
                    if type(k) is not str:
                        out.append(f"{show(path + (repr(k),))}: key of type {type(k).__name__}")
                    walk(y, path + (k,), v)
            else:
                for i, y in enumerate(v):
                    walk(y, path + (i,), v)
            stack.pop()
            return
        out.append(f"{show(path)}: {type(v).__module__}.{type(v).__qualname__}")

    walk(x, (), None)
    return out


class _Unwritable(Exception):
    pass


def written(x: Any) -> Optional[str]:
    """What a permissive provider adapter writes for x: JSON, with what JSON cannot write as its JSON form (a pydantic
    model's dump, a dataclass's fields, a date's ISO form, a set's items, bytes as UTF-8, an Enum's value) or its str(),
    and keys as str(); None when even that cannot be written (a cycle, a serializer that raises)."""
    stack: List[int] = []

    def prep(v: Any) -> Any:
        if isinstance(v, enum.Enum):
            return prep(v.value)
        if v is None or isinstance(v, (bool, str, int, float)):
            return v
        if id(v) in stack:
            raise _Unwritable("cycle")
        stack.append(id(v))
        try:
            if isinstance(v, dict):
                return {str(prep(k)) if not isinstance(k, str) else k: prep(y) for k, y in v.items()}
            if isinstance(v, (list, tuple, set, frozenset)):
                return [prep(y) for y in v]
            if isinstance(v, pydantic.BaseModel):
                return prep(v.model_dump(mode="json"))
            if dataclasses.is_dataclass(v) and not isinstance(v, type):
                return prep(dataclasses.asdict(v))
            if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
                return v.isoformat()
            if isinstance(v, (bytes, bytearray)):
                return bytes(v).decode("utf-8", "replace")
            return str(v)
        finally:
            stack.pop()

    try:
        return json.dumps(prep(x), ensure_ascii=False)
    except Exception:
        return None


def leaks(x: Any, values: List[Tuple[str, str]] = ALL_HELD) -> List[str]:
    text = written(x)
    if text is None:
        return []
    t = text.lower()
    return [v for _, v in values if v.lower() in t]


def without(x: Any, paths: List[Path_]) -> Any:
    """x with the values at paths replaced by a marker (copies of the containers on those paths only)."""

    def put(o: Any, p: Path_) -> Any:
        if not p:
            return "<authorized>"
        if isinstance(o, dict) and p[0] in o:
            return {**o, p[0]: put(o[p[0]], p[1:])}
        if isinstance(o, list) and isinstance(p[0], int) and p[0] < len(o):
            return [put(y, p[1:]) if i == p[0] else y for i, y in enumerate(o)]
        return o

    for p in paths:
        x = put(x, p)
    return x


def paths_of(x: Any, value: str, path: Path_ = ()) -> List[Path_]:
    """Every path at which x holds value (string leaves only)."""
    if isinstance(x, str):
        return [path] if x == value else []
    if isinstance(x, (list, tuple)):
        return [p for i, y in enumerate(x) for p in paths_of(y, value, path + (i,))]
    if isinstance(x, dict):
        return [p for k, y in x.items() for p in paths_of(y, value, path + (k,))]
    return []


def none(problems: List[str], what: str = "boundary") -> None:
    """Fails with every problem written out (a report the release gate reads), or passes when there is none."""
    if problems:
        pytest.fail(f"{what}: {len(problems)} problem(s)\n  - " + "\n  - ".join(problems), pytrace=False)


def describe_error(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:160]


# --------------------------------------------------------------------------------------------------------- the agent


def lc_sent(m: BaseMessage) -> Dict[str, Any]:
    """The fields of a LangChain message that providers are sent."""
    return {
        "type": m.type,
        "content": m.content,
        "name": m.name,
        "tool_calls": getattr(m, "tool_calls", None),
        "invalid_tool_calls": getattr(m, "invalid_tool_calls", None),
        "additional_kwargs": m.additional_kwargs,
        "tool_call_id": getattr(m, "tool_call_id", None),
    }


def lc_tree(messages: List[BaseMessage], settings: Any = None, own: Any = None) -> Dict[str, Any]:
    """The request tree of one call: the messages as providers are sent them, the model settings bound, and (own) the
    model's own fields that go into the request (model_kwargs and extra_body of the model that sends)."""
    tree = {"messages": [lc_sent(m) for m in messages], "settings": settings}
    if own is not None:
        tree["model"] = own
    return tree


def own_of(model: Any) -> Dict[str, Any]:
    """The model's own fields a provider package puts into the request: model_kwargs and extra_body."""
    return {"model_kwargs": getattr(model, "model_kwargs", None), "extra_body": getattr(model, "extra_body", None)}


def lc_binary(path: Path_, parent: Any) -> bool:
    """Bytes in a LangChain message: the data of a file block."""
    return (
        len(path) == 5 and path[0] == "messages" and path[2] == "content" and path[4] in ("data", "base64")
        and isinstance(parent, dict) and parent.get("type") in ("image", "file", "audio", "video")
    )


@dataclasses.dataclass
class Outcome:
    sent: List[List[BaseMessage]]
    trees: List[Any]
    given: List[Any]
    error: Optional[BaseException]
    entered: bool


def lc_run(
    messages: Callable[[], List[BaseMessage]],
    mode: str,
    *,
    tools: Optional[list] = None,
    script: Optional[List[AIMessage]] = None,
    settings: Optional[Callable[[], Dict[str, Any]]] = None,
    own: Optional[Callable[[], Dict[str, Any]]] = None,
    detector: Any = None,
    start: int = 0,
    pii: bool = True,
) -> Outcome:
    """One create_agent run: messages() is the input state (built inside the run, so a message LangChain refuses to
    build counts as refused by the framework), script the model's answers, start the index of the first model call
    that holds the planted input, settings() the application's model settings (set by a middleware outside this one),
    own() the model's own fields (model_kwargs, extra_body: a provider package such as langchain-openai or
    langchain-anthropic puts them into the request; what is recorded is what the model that sends holds, the middleware's
    copy of it). pii=False runs the same agent without the middleware (the baseline: what the framework does on its own)."""
    record: Dict[str, Any] = {"seen": [], "settings": [], "own": [], "bound": None}
    answers = list(script or [AIMessage("done")])

    class Recorder(GenericFakeChatModel):
        model_kwargs: Dict[str, Any] = pydantic.Field(default_factory=dict)
        extra_body: Optional[Dict[str, Any]] = None

        @property
        def _default_params(self) -> Dict[str, Any]:
            return {**({"extra_body": self.extra_body} if self.extra_body is not None else {}), **self.model_kwargs}

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            record["bound"] = {k: v for k, v in kwargs.items() if k != "tool_choice"}
            return self

        def _generate(self, messages: List[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
            record["seen"].append(list(messages))
            record["settings"].append(record["bound"] if record["bound"] is not None else dict(kwargs))
            record["own"].append(own_of(self))
            record["bound"] = None
            i = len(record["seen"]) - 1
            self.messages = iter([answers[min(i, len(answers) - 1)]])
            return super()._generate(messages, stop=stop, run_manager=run_manager)

    state: Dict[str, Any] = {"entered": 0, "given": []}

    class BoundarySpy(AgentMiddleware):  # type: ignore[misc]
        def _request(self, request: Any) -> Any:
            if settings is not None:
                request = request.override(model_settings={**(request.model_settings or {}), **settings()})
            state["entered"] += 1
            state["given"].append(lc_tree(request.messages, request.model_settings if settings is not None else None,
                                          own_of(request.model) if own is not None else None))
            return request

        def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return handler(self._request(request))

        async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
            return await handler(self._request(request))

    error: Optional[BaseException] = None
    try:
        middleware: List[Any] = [BoundarySpy()]
        if pii:
            middleware.append(pii_middleware(detector or DETECTORS["finder"], vault=fresh_vault()))
        agent = create_agent(Recorder(messages=iter([]), **(own() if own is not None else {})), tools=tools or [], middleware=middleware)
        payload = {"messages": messages()}
        config = {"recursion_limit": 12}
        if mode == "sync":
            agent.invoke(payload, config)
        else:
            asyncio.run(agent.ainvoke(payload, config))
    except Exception as e:  # noqa: BLE001 (any failure is an outcome)
        error = e
    after = record["seen"][start:]
    trees = [lc_tree(ms, record["settings"][start + i] if settings is not None else None,
                     record["own"][start + i] if own is not None else None) for i, ms in enumerate(after)]
    return Outcome(after, trees, state["given"][start:], error, state["entered"] > start)


# ------------------------------------------------------------------------------------------------------------ fixtures


@dataclasses.dataclass
class Customer:
    name: str
    email: str


class CustomerModel(pydantic.BaseModel):
    name: str
    email: str


class Lazy(pydantic.BaseModel):
    """A model whose dump is harmless the first time and holds a held value afterwards: a tree sent with the object in
    it instead of what was masked is written again by the provider adapter."""

    _reads: int = pydantic.PrivateAttr(default=0)

    @pydantic.model_serializer
    def _dump(self) -> Dict[str, str]:
        self._reads += 1
        return {"note": "nothing here"} if self._reads == 1 else {"note": PERSON}


class Refuses(pydantic.BaseModel):
    """A model whose dump raises: JSON cannot write it."""

    @pydantic.model_serializer
    def _dump(self) -> Dict[str, str]:
        raise ValueError("serializer refuses")


class DumpUnreadable(pydantic.BaseModel):
    """A model whose model_dump cannot be read (a property that raises AttributeError): a dump that raises, never a model
    without one (gate 8 of 1.7.1, the sibling of the confirmation's item 8: it was written in pydantic's JSON mode)."""

    name: str = PERSON

    @property
    def model_dump(self) -> Any:
        return self.not_set  # (AttributeError)


@dataclasses.dataclass
class FieldUnset:
    """A dataclass with a field that is not set: a field that cannot be read, never one left out (the same sibling: it
    was sent without that field)."""

    name: str = PERSON
    email: str = dataclasses.field(init=False)


class Plan(enum.Enum):
    OWNER = PERSON


class Named:
    """A class JSON cannot write and str() writes as a held value."""

    def __str__(self) -> str:
        return PERSON

    __repr__ = __str__


def _cycle() -> Dict[str, Any]:
    d: Dict[str, Any] = {"name": PERSON}
    d["self"] = d
    return d


# (id, make, expectation): what the boundary must do with the value
PLAIN_FIXTURES: List[Tuple[str, Callable[[], Any], str]] = [
    ("dataclass", lambda: Customer(PERSON, EMAIL), "materialize"),
    ("pydantic", lambda: CustomerModel(name=PERSON, email=EMAIL), "materialize"),
    ("pydantic-second-dump", lambda: Lazy(), "materialize"),
    ("date", lambda: datetime.date(1990, 1, 2), "materialize"),
    ("datetime", lambda: datetime.datetime(1990, 1, 2, 9, 30), "materialize"),
    ("set", lambda: {PERSON, EMAIL}, "materialize"),
    ("frozenset", lambda: frozenset({PERSON}), "materialize"),
    ("tuple", lambda: (PERSON, EMAIL), "materialize"),
    ("bytes", lambda: PERSON.encode("utf-8"), "materialize"),
    ("decimal", lambda: Decimal(PHONE), "materialize"),
    ("enum", lambda: Plan.OWNER, "materialize"),
    ("nan", lambda: {"score": float("nan"), "who": PERSON}, "materialize"),
    ("int-key", lambda: {int(PHONE): "on file"}, "materialize"),
    ("nested", lambda: {"rows": [Customer(PERSON, EMAIL)], "at": datetime.date(1990, 1, 2), "tags": {EMAIL}}, "materialize"),
    ("tuple-key", lambda: {(PERSON, "x"): 1}, "either"),
    ("str-only", lambda: Named(), "either"),
    ("cycle", _cycle, "fail-closed"),
    ("serializer-raises", lambda: Refuses(), "fail-closed"),
    ("dump-unreadable", lambda: DumpUnreadable(), "fail-closed"),
    ("dataclass-field-unset", lambda: FieldUnset(), "fail-closed"),
]


def converted_by_framework(o: Outcome) -> bool:
    """Whether the framework made the input plain JSON before the middleware got it: no unsafe serialization reached
    the boundary, so the call must go out masked like any other."""
    return bool(o.given) and all(not non_plain(g, lc_binary) for g in o.given)


def judge(o: Outcome, fixture_expectation: str) -> List[str]:
    expectation = "materialize" if fixture_expectation != "materialize" and converted_by_framework(o) else fixture_expectation
    problems: List[str] = []
    sent = bool(o.trees)
    for t in o.trees:
        problems += [f"not plain JSON: {b}" for b in non_plain(t, lc_binary)]
        problems += [f"held value sent: {v}" for v in leaks(t)]
    if sent and o.error is not None:
        problems.append(f"sent and also failed: {describe_error(o.error)}")
    if not sent and o.error is None:
        problems.append("nothing sent and no error")
    if not sent and o.error is not None and o.entered and expectation == "materialize":
        problems.append(f"the middleware failed a call JSON can write (a valid request broken): {describe_error(o.error)}")
    if sent and expectation == "fail-closed":
        problems.append("input JSON cannot write was sent instead of failing the call")
    return problems


def asked() -> HumanMessage:
    return HumanMessage("Look it up.")


def calling(args: Optional[Dict[str, Any]] = None, **extra: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": "c1", "name": "lookup", "args": args or {}}], **extra)


def answered(content: Any, **extra: Any) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="c1", name="lookup", **extra)


def lookup_tool(result: Callable[[], Any]) -> Any:
    @tool
    def lookup() -> Any:
        """Look up."""
        return result()

    return lookup


@dataclasses.dataclass
class JsonBlock:
    """A block a tool returns that is a class instance (24th outside review, A2)."""

    json: Any
    type: str = "json"


def _tool_run(result: Callable[[], Any]) -> Dict[str, Any]:
    return {"messages": lambda: [asked()], "tools": [lookup_tool(result)], "script": [calling(), AIMessage("done")], "start": 1}


# (id, spec(value)): where an application can put a value that is not plain JSON; spec gives lc_run's arguments
PLAIN_POSITIONS: List[Tuple[str, Callable[[Callable[[], Any]], Dict[str, Any]]]] = [
    ("tool-return", lambda v: _tool_run(v)),
    ("tool-return-row", lambda v: _tool_run(lambda: {"row": v(), "note": "ok"})),
    ("tool-return-json-block", lambda v: _tool_run(lambda: [{"type": "json", "json": {"v": v()}}])),
    ("tool-return-typed-rows", lambda v: _tool_run(lambda: [{"type": "contact", "v": v()}])),
    ("tool-return-class-block", lambda v: _tool_run(lambda: [JsonBlock({"v": v()})])),
    ("block-json", lambda v: {"messages": lambda: [asked(), calling(), answered([{"type": "json", "json": {"v": v()}}])]}),
    ("block-class", lambda v: {"messages": lambda: [asked(), calling(), answered([JsonBlock({"v": v()})])]}),
    ("tool-call-args", lambda v: {"messages": lambda: [asked(), calling({"v": v()}), answered("ok")]}),
    ("additional-kwargs", lambda v: {"messages": lambda: [asked(), calling(additional_kwargs={"crm": v()}), answered("ok")]}),
    ("human-block-unknown", lambda v: {"messages": lambda: [HumanMessage(content=[{"type": "text", "text": "hi"}, {"type": "x-later-block", "v": v()}])]}),
    # (model settings are bound as keyword arguments: "metadata" there is LangChain's own run metadata, so OpenAI's
    # metadata goes through extra_body)
    ("settings-extra-body-metadata", lambda v: {"messages": lambda: [asked()], "settings": lambda: {"extra_body": {"metadata": {"crm": v()}}}}),
    ("settings-extra-body", lambda v: {"messages": lambda: [asked()], "settings": lambda: {"extra_body": {"crm": v()}}}),
    ("settings-unknown", lambda v: {"messages": lambda: [asked()], "settings": lambda: {"acme": {"v": v()}}}),
    # the model's own fields (gate 8 of the release standard, items 1 and 2): what the model is constructed with goes
    # into the request (langchain-anthropic spreads model_kwargs over the request, "system" and "messages" too; the
    # provider clients merge extra_body into the body), built once (a second dump can differ)
    ("model-kwargs", lambda v: {"messages": lambda: [asked()], "own": lambda: {"model_kwargs": {"crm": v()}}}),
    ("model-kwargs-system", lambda v: {"messages": lambda: [asked()], "own": lambda: {"model_kwargs": {"system": v()}}}),
    ("model-kwargs-messages", lambda v: {"messages": lambda: [asked()], "own": lambda: {"model_kwargs": {"messages": [{"role": "user", "content": v()}]}}}),
    ("model-extra-body-metadata", lambda v: {"messages": lambda: [asked()], "own": lambda: {"extra_body": {"metadata": {"crm": v()}}}}),
]


def refused_by_framework(o: Outcome, baseline: Callable[[], Outcome]) -> bool:
    """Whether a call that failed with nothing sent fails the same way without the middleware (the framework refuses the
    input itself: listed, and it must send nothing)."""
    if o.trees or o.error is None:
        return False
    base = baseline()
    return not base.trees and base.error is not None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fixture", PLAIN_FIXTURES, ids=[f"{f[0]}[{f[2]}]" for f in PLAIN_FIXTURES])
@pytest.mark.parametrize("position", PLAIN_POSITIONS, ids=[p[0] for p in PLAIN_POSITIONS])
def test_plain_json_and_unsafe_input(position, fixture, mode):
    _, make, expectation = fixture
    o = lc_run(mode=mode, **position[1](make))
    problems = judge(o, expectation)
    if refused_by_framework(o, lambda: lc_run(mode=mode, pii=False, **position[1](make))):
        # (listed in the report: the framework refuses this input without the middleware too, and nothing was sent)
        print(f"refused by the framework (nothing sent): {position[0]} / {fixture[0]} / {mode}: {describe_error(o.error)}")
        problems = [p for p in problems if not p.startswith("the middleware failed")]
    none(problems)


# --------------------------------------------------------------------------------------------------------- identifiers


def plant_all(value: str, skip: Tuple[str, ...] = ()) -> Dict[str, str]:
    """A dict with every identifier name as a key and value under each (skip: names left out)."""
    return {n: value for n in CASES["identifiers"] if n not in skip}


def plant_framed(value: str) -> Dict[str, Any]:
    """Framework shapes inside data: every frame field holding a dict (or a list of typed dicts) with the identifiers
    planted in it. A tool's payload is data at every depth, whatever names and types its objects have."""
    out: Dict[str, Any] = {f: plant_all(value) for f in CASES["frame_fields"]}
    out["content"] = [
        {**plant_all(value), "type": "text", "text": "row"},
        {**plant_all(value), "type": "tool_call"},
        {**plant_all(value), "type": "tool_use"},
        {**plant_all(value), "type": "image"},
    ]
    out["tool_calls"] = [{**plant_all(value), "type": "function", "function": plant_all(value)}]
    return out


_NO_KWARGS_FRAME = ("tool_calls", "function_call")
# The fields a provider defines that a recording agent carries as model settings ("input" and LangChain's
# ls_structured_output_format are refused by the framework there, and "stop" is a parameter of its own), and the
# identifier names that stand at an authorized path one level below one of them (response_format.type,
# function_call.name, prompt.id): left out of the depth-1 plant
_CARRIED_FIELDS = sorted(set(_lc._SETTINGS_FIELDS) - {"input", "ls_structured_output_format", "stop"})
_AT_DEPTH_1 = {"response_format": ("type",), "function_call": ("name",), "prompt": ("id",)}
# (id, messages(), settings() or None): the held ID planted where it is not LangChain's or a provider's
IDENTIFIER_PLANTS: List[Tuple[str, Callable[[], List[BaseMessage]], Optional[Callable[[], Dict[str, Any]]]]] = [
    ("tool-call-args", lambda: [asked(), calling(plant_all(ID)), answered("ok")], None),
    ("tool-call-args-framed", lambda: [asked(), calling(plant_framed(ID)), answered("ok")], None),
    ("block-json", lambda: [asked(), calling(), answered([{"type": "json", "json": plant_all(ID)}])], None),
    ("block-json-framed", lambda: [asked(), calling(), answered([{"type": "json", "json": plant_framed(ID)}])], None),
    ("typed-rows", lambda: [asked(), calling(), answered([{**plant_all(ID), "type": "contact"}])], None),
    ("rows-without-type", lambda: [asked(), calling(), answered([plant_all(ID, ("type",))])], None),
    ("tool-use-input", lambda: [asked(), AIMessage(content=[{"type": "text", "text": "x"}, {"type": "tool_use", "id": "c2", "name": "lookup", "input": plant_all(ID)}]), HumanMessage("ok")], None),
    ("tool-result-block-content", lambda: [asked(), calling(), answered([{"type": "tool_result", "tool_use_id": "c1", "content": [{"type": "json", "json": plant_all(ID)}]}])], None),
    ("kwargs-top", lambda: [asked(), calling(additional_kwargs=plant_all(ID, _NO_KWARGS_FRAME)), answered("ok")], None),
    ("kwargs-nested", lambda: [asked(), calling(additional_kwargs={"crm": plant_all(ID)}), answered("ok")], None),
    ("human-block-unknown", lambda: [HumanMessage(content=[{"type": "text", "text": "hi"}, {**plant_all(ID), "type": "x-later-block"}])], None),
    ("tool-message-kwargs", lambda: [asked(), calling(), answered("ok", additional_kwargs=plant_all(ID, _NO_KWARGS_FRAME))], None),
    ("settings-extra-body-metadata", lambda: [asked()], lambda: {"extra_body": {"metadata": plant_all(ID)}}),
    ("settings-unknown-provider", lambda: [asked()], lambda: {"acme": plant_all(ID)}),
    ("settings-extra-body", lambda: [asked()], lambda: {"extra_body": {"acme": plant_all(ID)}}),
    ("settings-variables", lambda: [asked()], lambda: {"prompt": {"id": "pmpt_1", "variables": plant_all(ID)}}),
    # (the Python sibling of release gate 8, item 5: identifier names one and two levels below a field a provider
    # defines, below the paths of a container, a conversation and a gateway's routing, below a tool choice and in
    # message-like settings away from the paths of their types are no provider's identifiers; npm's settings-nested
    # and the reviewer's settings)
    ("settings-nested", lambda: [asked()], lambda: {"reasoning": plant_all(ID), "prompt": {"nested": plant_all(ID)}, "mcp_servers": [{"name": "s", "url": "https://mcp.test", "nested": plant_all(ID)}]}),
    ("settings-reviewer", lambda: [asked()], lambda: {
        "reasoning": {"effort": "low", "previous_response_id": ID, "file_id": ID}, "text": {"format": {"type": "text", "signature": ID}},
        "thinking": {"type": "enabled", "budget_tokens": 1024, "signature": ID, "item_id": ID},
        "web_search_options": {"user_location": {"type": "approximate", "conversation_id": ID}}, "stream_options": {"include_usage": True, "item_id": ID},
    }),
    ("settings-every-field-depth-1", lambda: [asked()], lambda: {f: plant_all(ID, _AT_DEPTH_1.get(f, ())) for f in _CARRIED_FIELDS}),
    ("settings-every-field-depth-2", lambda: [asked()], lambda: {f: {"x": plant_all(ID)} for f in _CARRIED_FIELDS}),
    ("settings-below-paths", lambda: [asked()], lambda: {
        "container": {"id": "c1", "nested": plant_all(ID)}, "conversation": {"id": "c1", "nested": plant_all(ID)},
        "provider": {"order": ["openai"], "nested": plant_all(ID)}, "models": {"nested": plant_all(ID)},
        "extra_body": {"provider": {"nested": plant_all(ID)}, "models": {"nested": plant_all(ID)}},
        "tool_choice": {"type": "function", "function": {"name": "lookup", "nested": plant_all(ID)}, "nested": plant_all(ID)},
        "logit_bias": {"50256": plant_all(ID)}, "headers": {"x-trace": {"nested": plant_all(ID)}},
    }),
    ("settings-message-like", lambda: [asked()], lambda: {
        "system": [{"type": "text", "text": "hi", "nested": plant_all(ID)}, plant_all(ID, ("type",)), {"type": "text", "text": "hi", "cache_control": plant_all(ID, ("type",))}],
        "instructions": [{**plant_all(ID, ("type", "encrypted_content")), "content": [{"type": "input_text", "text": "hi", "nested": plant_all(ID)}]}],
    }),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("plant", IDENTIFIER_PLANTS, ids=[p[0] for p in IDENTIFIER_PLANTS])
def test_identifier_planted_outside_its_path_is_masked(plant, mode):
    _, messages, settings = plant
    planted = paths_of(lc_tree(messages(), settings() if settings else None), ID)
    assert planted, "nothing planted"
    o = lc_run(messages, mode, settings=settings)
    if refused_by_framework(o, lambda: lc_run(messages, mode, settings=settings, pii=False)):
        pytest.skip(f"refused by the framework (nothing sent): {describe_error(o.error)}")
    none([] if o.error is None else [describe_error(o.error)], "the call failed")
    tree = o.trees[0]
    kept = [p for p in planted if isinstance(at(tree, p), str) and ID in at(tree, p).lower()]
    none([f"{show(p)} keeps the held value: {at(tree, p)!r}" for p in kept], "identifier planted outside its authorized path")
    none(non_plain(tree, lc_binary), "not plain JSON")
    none(leaks(tree), "held value sent")


# (id, the model's own fields): the held ID planted in what the model is constructed with, where it is not a provider's
# (gate 8 of the release standard: the model's own fields are data whatever their key, exemptions only at their paths)
MODEL_PLANTS: List[Tuple[str, Callable[[], Dict[str, Any]]]] = [
    ("model-kwargs-unknown", lambda: {"model_kwargs": {"acme": plant_all(ID)}}),
    ("model-kwargs-metadata", lambda: {"model_kwargs": {"metadata": plant_all(ID)}}),
    ("model-kwargs-messages", lambda: {"model_kwargs": {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}, plant_all(ID)]}]}}),
    # (the system prompt is a field providers define: its block types and signatures are theirs; its text and keys count)
    ("model-kwargs-system", lambda: {"model_kwargs": {"system": [{"type": "text", "text": ID}, {"type": "text", "text": f"Contact {ID}.", ID: ID}]}}),
    ("model-extra-body", lambda: {"extra_body": {"acme": plant_all(ID)}}),
    ("model-extra-body-messages", lambda: {"extra_body": {"messages": [{"role": "user", "content": f"Contact {ID}.", "acme": plant_all(ID)}]}}),
    # (the Python sibling of release gate 8, item 5, in the model's own fields: the reviewer's settings in model_kwargs,
    # identifier names one level below fields a provider defines and below the paths of a container and a gateway's
    # routing, and in message-like input away from the paths of its types)
    ("model-kwargs-nested", lambda: {"model_kwargs": {
        "reasoning": plant_all(ID), "prompt": {"nested": plant_all(ID)}, "mcp_servers": [{"name": "s", "url": "https://mcp.test", "nested": plant_all(ID)}],
        "text": {"format": {"type": "text", "signature": ID}}, "thinking": {"type": "enabled", "budget_tokens": 1024, "signature": ID, "item_id": ID},
        "stream_options": {"include_usage": True, "item_id": ID},
    }}),
    ("model-kwargs-below-paths", lambda: {"model_kwargs": {
        "container": {"id": "c1", "nested": plant_all(ID)}, "conversation": {"id": "c1", "nested": plant_all(ID)}, "provider": {"nested": plant_all(ID)},
        "models": {"nested": plant_all(ID)}, "input": [{**plant_all(ID, ("type", "encrypted_content")), "content": [{"type": "input_text", "nested": plant_all(ID)}]}],
    }}),
    ("model-extra-body-routing", lambda: {"extra_body": {"provider": {"nested": plant_all(ID)}, "models": {"nested": plant_all(ID)}, "previous_response_id": ID}}),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("plant", MODEL_PLANTS, ids=[p[0] for p in MODEL_PLANTS])
def test_identifier_planted_in_the_models_own_fields_is_masked(plant, mode):
    _, own = plant
    planted = paths_of(lc_tree([asked()], None, own()), ID)
    assert planted, "nothing planted"
    o = lc_run(lambda: [asked()], mode, own=own)
    none([] if o.error is None else [describe_error(o.error)], "the call failed")
    tree = o.trees[0]
    kept = [p for p in planted if isinstance(at(tree, p), str) and ID in at(tree, p).lower()]
    none([f"{show(p)} keeps the held value: {at(tree, p)!r}" for p in kept], "identifier planted outside its authorized path")
    none(non_plain(tree, lc_binary), "not plain JSON")
    none(leaks(tree), "held value sent")


@pytest.mark.parametrize("mode", MODES)
def test_authorized_paths_in_the_models_own_fields_keep_the_identifier(mode):
    def own() -> Dict[str, Any]:
        return {"model_kwargs": {"previous_response_id": ID, "prompt": {"id": ID, "variables": {"who": ID}}, "system": f"Contact {ID}.",
                                 "container": {"id": ID, "note": ID}, "mcp_servers": [{"type": "url", "name": ID, "url": f"https://{ID}.test", "note": ID}],
                                 "input": [{"type": "reasoning", "encrypted_content": ID, "note": ID}]},
                "extra_body": {"provider": {"order": [ID], "only": [ID], "note": ID}, "models": [ID], "note": ID}}

    keep: List[Path_] = [
        ("model", "model_kwargs", "previous_response_id"),
        ("model", "model_kwargs", "prompt", "id"),
        ("model", "model_kwargs", "container", "id"),
        ("model", "model_kwargs", "mcp_servers", 0, "name"),
        ("model", "model_kwargs", "mcp_servers", 0, "url"),
        ("model", "model_kwargs", "input", 0, "encrypted_content"),
        ("model", "extra_body", "provider", "order", 0),
        ("model", "extra_body", "provider", "only", 0),
        ("model", "extra_body", "models", 0),
    ]
    before = lc_tree([asked()], None, own())
    o = lc_run(lambda: [asked()], mode, own=own)
    none([] if o.error is None else [describe_error(o.error)], "the call failed")
    tree = o.trees[0]
    none([f"{show(p)}: {at(tree, p)!r}" for p in keep if at(tree, p) != at(before, p)], "identifier changed at its authorized path")
    none(non_plain(tree, lc_binary), "not plain JSON")
    none(leaks(without(tree, keep)), "held value sent outside the authorized paths")
    assert at(tree, ("model", "model_kwargs", "system")) == "Contact [USERNAME_1]."


@pytest.mark.parametrize("mode", MODES)
def test_authorized_paths_keep_the_identifier(mode):
    def messages() -> List[BaseMessage]:
        return [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Look it up.", "id": ID},
                    {"type": "image", "url": f"https://files.test/{ID}/a.png"},
                    {"type": "image_url", "image_url": {"url": f"https://files.test/{ID}/b.png"}},
                    {"type": "file", "file_id": ID},
                ]
            ),
            AIMessage(
                content=[{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": ID, "name": ID, "input": {"q": "x"}}],
                tool_calls=[{"id": ID, "name": ID, "args": {"q": "x"}}],
                additional_kwargs={"tool_calls": [{"id": ID, "type": "function", "function": {"name": ID, "arguments": '{"q":"x"}'}}]},
            ),
            ToolMessage(tool_call_id=ID, name=ID, content="ok"),
        ]

    def settings() -> Dict[str, Any]:
        return {
            "previous_response_id": ID, "prompt": {"id": ID}, "conversation": {"id": ID}, "container": ID, "model": ID,
            "models": [ID], "provider": {"order": [ID], "ignore": [ID]}, "tool_choice": {"type": "function", "function": {"name": ID}},
            "response_format": {"type": "json_schema", "json_schema": {"name": ID, "schema": {"type": "object", "properties": {ID: {"type": "string"}}}}},
            "text": {"format": {"type": "json_schema", "name": ID}}, "logit_bias": {ID: 5}, "extra_headers": {"x-user": ID},
            "mcp_servers": [{"type": "url", "name": ID, "url": f"https://{ID}.test", "authorization_token": ID, "tool_configuration": {"allowed_tools": [ID]}}],
            "system": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
        }

    keep: List[Path_] = [
        ("messages", 0, "content", 0, "id"),
        ("messages", 0, "content", 1, "url"),
        ("messages", 0, "content", 2, "image_url", "url"),
        ("messages", 0, "content", 3, "file_id"),
        ("messages", 1, "content", 1, "id"),
        ("messages", 1, "content", 1, "name"),
        ("messages", 1, "tool_calls", 0, "id"),
        ("messages", 1, "tool_calls", 0, "name"),
        ("messages", 1, "additional_kwargs", "tool_calls", 0, "id"),
        ("messages", 1, "additional_kwargs", "tool_calls", 0, "function", "name"),
        ("messages", 2, "tool_call_id"),
        ("messages", 2, "name"),
        ("settings", "previous_response_id"),
        ("settings", "prompt", "id"),
        ("settings", "conversation", "id"),
        ("settings", "container"),
        ("settings", "model"),
        ("settings", "models", 0),
        ("settings", "provider", "order", 0),
        ("settings", "provider", "ignore", 0),
        ("settings", "tool_choice", "function", "name"),
        ("settings", "response_format", "json_schema", "name"),
        ("settings", "text", "format", "name"),
        ("settings", "extra_headers", "x-user"),
        ("settings", "logit_bias"),  # (a token id is a key there)
        ("settings", "response_format", "json_schema", "schema", "properties"),  # (property names stay as written)
        ("settings", "mcp_servers", 0, "name"),
        ("settings", "mcp_servers", 0, "url"),
        ("settings", "mcp_servers", 0, "authorization_token"),
        ("settings", "mcp_servers", 0, "tool_configuration", "allowed_tools", 0),
    ]
    before = lc_tree(messages(), settings())
    o = lc_run(messages, mode, settings=settings)
    none([] if o.error is None else [describe_error(o.error)], "the call failed")
    tree = o.trees[0]
    none([f"{show(p)}: {at(tree, p)!r}" for p in keep if at(tree, p) != at(before, p)], "identifier changed at its authorized path")
    none(non_plain(tree, lc_binary), "not plain JSON")
    none(leaks(without(tree, keep)), "held value sent outside the authorized paths")


# ------------------------------------------------------------------------------------------------------------ readings


def _reading_params() -> List[Any]:
    out = []
    for c in CASES["readings"]:
        for detector in DETECTORS:
            for mode in MODES:
                kind = "guaranteed" if c["guaranteed"] else "best-effort"
                out.append(pytest.param(c, detector, mode, id=f"{kind}-{c['id']}[{'>'.join(c['steps'])}]-{detector}-{mode}"))
    return out


READINGS = _reading_params()


def _messages_of(o: Outcome) -> List[Dict[str, Any]]:
    none([] if o.error is None else [describe_error(o.error)], "the call failed")
    assert o.trees, "nothing sent"
    return o.trees[0]["messages"]


@pytest.mark.parametrize("case, detector, mode", READINGS)
def test_reading_user(case, detector, mode):
    o = lc_run(lambda: [HumanMessage(case["text"])], mode, detector=DETECTORS[detector])
    assert _messages_of(o)[0]["content"] == case["expect"]


@pytest.mark.parametrize("case, detector, mode", READINGS)
def test_reading_tool_text(case, detector, mode):
    o = lc_run(lambda: [asked()], mode, tools=[lookup_tool(lambda: case["text"])], script=[calling(), AIMessage("done")], detector=DETECTORS[detector], start=1)
    tool_message = next(m for m in _messages_of(o) if m["type"] == "tool")
    assert tool_message["content"] == case["expect"]


@pytest.mark.parametrize("case, detector, mode", READINGS)
def test_reading_tool_json(case, detector, mode):
    o = lc_run(lambda: [asked()], mode, tools=[lookup_tool(lambda: {"mail": case["text"], "ok": 1})], script=[calling(), AIMessage("done")], detector=DETECTORS[detector], start=1)
    tool_message = next(m for m in _messages_of(o) if m["type"] == "tool")
    assert json.loads(tool_message["content"]) == {"mail": case["expect"], "ok": 1}


# ------------------------------------------------------------------------------------------------- the checks themselves


@pytest.mark.parametrize("mode", MODES)
def test_without_the_middleware_every_check_reports(mode):
    # the checks see what is sent: without the middleware the same inputs are reported (so a pass above is not vacuous)
    def messages() -> List[BaseMessage]:
        return [asked(), calling(plant_all(ID)), answered([{"type": "json", "json": {"v": Customer(PERSON, EMAIL), "at": datetime.date(1990, 1, 2)}}])]

    o = lc_run(messages, mode, pii=False)
    assert o.error is None
    assert sorted(leaks(o.trees[0])) == sorted([DOB, ID, PERSON, EMAIL])
    assert non_plain(o.trees[0], lc_binary)
    r = next(c for c in CASES["readings"] if c["id"].startswith("a4-percent-fold-percent@"))
    plain = lc_run(lambda: [HumanMessage(r["text"])], mode, pii=False)
    assert plain.trees[0]["messages"][0]["content"] == r["text"] != r["expect"]
