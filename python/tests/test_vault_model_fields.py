"""The model's own request fields with a vault (SDK 1.7.1, the gate run of 1.7.1: gate 5, the HTTP bodies of @model).

Values given to a chat model's constructor that go into the request (model_kwargs such as user, metadata,
safety_identifier, prompt_cache_key and a stored prompt's variables; extra_body; a field of its own such as Anthropic's
user_profile_id) were sent as written. They are data like the model settings: the middleware sends them hidden from a
copy of the model for the call, and leaves the developer's model as it was. The same in js/test/langchain.test.ts ("the
model's own request fields"); the real adapters in the HTTP-body checks of the release gates (routes @model and @extra).
"""
import asyncio
import re
from typing import Any, Dict, List, Optional

import pytest

from newmean_pii import Vault

pytest.importorskip("langchain.agents.middleware")

LEAK = re.compile(r"Kim Minsu|kim@example\.com|821023456789")


class NoDetector:
    """Finds nothing: the vault alone."""

    sensitivity = 0.0

    def detect(self, text: str) -> list:
        return []


def _vault() -> Vault:
    v = Vault()
    v.tag("PERSON", "Kim Minsu")
    v.tag("EMAIL", "kim@example.com")
    v.tag("PHONE", "821023456789")
    return v


def _middleware(vault: Optional[Vault] = None) -> Any:
    from newmean_pii.langchain import pii_middleware

    return pii_middleware(NoDetector(), vault=vault if vault is not None else _vault())


def _client_default(o: Any) -> Any:
    """What the OpenAI and Anthropic clients write for what json.dumps cannot (openapi_dumps): a datetime's ISO form and a
    pydantic model's dump, made as it is written; anything else is refused."""
    import datetime

    from pydantic import BaseModel

    if isinstance(o, datetime.datetime):
        return o.isoformat()
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json", by_alias=True, exclude_unset=True)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _written(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The request body as the provider client writes it, once."""
    import json

    return json.loads(json.dumps(payload, default=_client_default))


def _openai_like(sent: List[Dict[str, Any]], reply: str = "done", **fields: Any) -> Any:
    """A chat model that builds its requests as langchain-openai does ({**_default_params, **kwargs}, with model_kwargs
    and extra_body), with a field of its own under an alias (user_profile_id, as langchain-anthropic has one)."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from pydantic import ConfigDict, Field

    class Model(GenericFakeChatModel):
        model_config = ConfigDict(populate_by_name=True)
        model_name: str = "gpt-x"
        model_kwargs: Dict[str, Any] = Field(default_factory=dict)
        extra_body: Optional[Dict[str, Any]] = None
        stop: Optional[List[str]] = None
        profile_user: Optional[str] = Field(default=None, alias="user_profile_id")

        @property
        def _default_params(self) -> Dict[str, Any]:
            params: Dict[str, Any] = {"model": self.model_name, "stream": False}
            for key, value in (("stop", self.stop), ("extra_body", self.extra_body), ("user_profile_id", self.profile_user)):
                if value is not None:
                    params[key] = value
            return {**params, **self.model_kwargs}

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            messages = self._convert_input(input_).to_messages()
            if stop is not None:
                kwargs["stop"] = stop
            return {**self._default_params, **kwargs, "messages": [m.content for m in messages]}

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
            sent.append(_written(self._get_request_payload(messages, stop=stop, **kwargs)))
            self.messages = iter([AIMessage(reply)])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self.bind(**kwargs)

    return Model(messages=iter([]), **fields)


def _anthropic_like(sent: List[Dict[str, Any]], reply: str = "done", **fields: Any) -> Any:
    """A chat model that builds its requests as langchain-anthropic does: model_kwargs spread over what it builds from the
    messages (a "system" or "messages" given there is what the provider gets), written as the client writes it."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from pydantic import Field

    class Model(GenericFakeChatModel):
        model: str = "claude-x"
        model_kwargs: Dict[str, Any] = Field(default_factory=dict)

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            messages = self._convert_input(input_).to_messages()
            system = [m.content for m in messages if m.type == "system"]
            turns = [{"role": "assistant" if m.type == "ai" else "user", "content": m.content} for m in messages if m.type != "system"]
            return {"model": self.model, "messages": turns, "system": system[0] if system else None, **self.model_kwargs, **kwargs}

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
            sent.append(_written(self._get_request_payload(messages, stop=stop, **kwargs)))
            self.messages = iter([AIMessage(reply)])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self.bind(**kwargs)

    return Model(messages=iter([]), **fields)


def _rewritten_model(first: str = "control", after: str = "Kim Minsu", text: bool = False) -> Any:
    """A pydantic model whose serializer writes `first` the first time it is called and `after` every time after (gate 8
    of the release standard, item 2: the outside review's Metadata). text: as a string, else as {"owner": ...}."""
    import pydantic

    class Metadata(pydantic.BaseModel):
        _reads: int = pydantic.PrivateAttr(default=0)

        @pydantic.model_serializer
        def _write(self) -> Any:
            self._reads += 1
            value = first if self._reads == 1 else after
            return value if text else {"owner": value}

    return Metadata()


def _own() -> Dict[str, Any]:
    return {
        "model_kwargs": {
            "user": "Kim Minsu",
            "metadata": {"customer": "Kim Minsu", "phone": 821023456789, "Kim Minsu": "vip", "tier": "gold"},
            "safety_identifier": "kim@example.com",
            "prompt_cache_key": "orders-kim@example.com",
            "prompt": {"id": "Kim Minsu", "version": "2", "variables": {"customer_name": "Kim Minsu"}},
            "previous_response_id": "kim@example.com",
        },
        "extra_body": {"metadata": {"email": "kim@example.com"}, "provider": {"order": ["Kim Minsu"]}},
        "user_profile_id": "Kim Minsu",
        "stop": ["Kim Minsu"],
    }


def _run(model: Any, mode: str) -> Any:
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage

    agent = create_agent(model, tools=[], middleware=[_middleware()])
    state = {"messages": [HumanMessage("Hi.")]}
    return agent.invoke(state) if mode == "invoke" else asyncio.run(agent.ainvoke(state))


@pytest.mark.parametrize("mode", ["invoke", "ainvoke"])
def test_the_models_own_fields_are_sent_hidden_from_a_copy_and_the_model_stays(mode):
    sent: List[Dict[str, Any]] = []
    model = _openai_like(sent, "Hello [PERSON_1], your key is [EMAIL_1].", **_own())
    out = _run(model, mode)
    assert len(sent) == 1
    got = {k: v for k, v in sent[0].items() if k != "messages"}
    assert got == {
        "model": "gpt-x",
        "stream": False,
        "stop": ["[PERSON_1]"],
        # (extra_body is data; a gateway's provider routing under it stays, as in model settings)
        "extra_body": {"metadata": {"email": "[EMAIL_1]"}, "provider": {"order": ["Kim Minsu"]}},
        "user_profile_id": "[PERSON_1]",
        "user": "[PERSON_1]",
        "metadata": {"customer": "[PERSON_1]", "phone": "[PHONE_1]", "[PERSON_1]": "vip", "tier": "gold"},
        "safety_identifier": "[EMAIL_1]",
        "prompt_cache_key": "orders-[EMAIL_1]",
        # (identifiers a provider defines stay at their paths: a stored prompt's id, a previous response's id)
        "prompt": {"id": "Kim Minsu", "version": "2", "variables": {"customer_name": "[PERSON_1]"}},
        "previous_response_id": "kim@example.com",
    }
    # the placeholders the call put in through the model's fields are its own: the answer gets the values back
    assert out["messages"][-1].content == "Hello Kim Minsu, your key is kim@example.com."
    # the developer's model is not changed
    own = _own()
    assert model.model_kwargs == own["model_kwargs"] and model.extra_body == own["extra_body"]
    assert model.profile_user == "Kim Minsu" and model.stop == ["Kim Minsu"]


def test_a_model_whose_fields_hold_no_known_value_is_given_as_it_is():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    model = _openai_like([], model_kwargs={"user": "u-17", "metadata": {"tier": "gold"}})
    seen = []
    _middleware().wrap_model_call(ModelRequest(model=model, messages=[HumanMessage("hi")]), lambda r: (seen.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert seen[0].model is model


def test_the_models_own_fields_are_read_as_model_settings_and_a_field_without_a_known_value_keeps_the_model():
    """(The merge of gate 4 and the model's own fields: the model's fields are read as model settings are read, from the
    plain JSON a provider writes. A field is held only when a known value is in it: a date, a tuple or an object with no
    JSON form in a model made on each call does not make a copy or fail the call.)"""
    import datetime

    import httpx
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    sent: List[Dict[str, Any]] = []
    model = _openai_like(sent, model_kwargs={
        "acme": {"previous_response_id": "Kim Minsu", "file_id": "kim@example.com"},
        "previous_response_id": "Kim Minsu",
    }, extra_body={"type": "kim@example.com", "provider": {"order": ["Kim Minsu"]}})
    _run(model, "invoke")
    assert sent[0]["acme"] == {"previous_response_id": "[PERSON_1]", "file_id": "[EMAIL_1]"}  # (an unknown field: data)
    assert sent[0]["extra_body"] == {"type": "[EMAIL_1]", "provider": {"order": ["Kim Minsu"]}}
    assert sent[0]["previous_response_id"] == "Kim Minsu"

    def given(m: Any) -> Any:
        seen: List[Any] = []
        _middleware().wrap_model_call(ModelRequest(model=m, messages=[HumanMessage("hi")]), lambda r: (seen.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
        return seen[0].model

    plain = _openai_like([], model_kwargs={"user": "[PERSON_1] u-17", "metadata": {"since": datetime.date(2020, 1, 2)}, "stop": ("a", "b")})
    assert given(plain) is plain

    class Bare:
        """Not a pydantic chat model, with no known value in its request."""

        _default_params = {"model": "x", "metadata": {"since": datetime.date(2020, 1, 2)}}

    bare = Bare()
    assert given(bare) is bare
    base = pytest.importorskip("langchain.chat_models.base")
    with httpx.Client() as client:
        made = base._ConfigurableModel(default_config={"model": "gpt-x", "http_client": client, "model_kwargs": {"user": "u-17"}}, configurable_fields="any")
        assert given(made) is made
        held = base._ConfigurableModel(default_config={"model": "gpt-x", "http_client": client, "model_kwargs": {"user": "Kim Minsu"}}, configurable_fields="any")
        with pytest.raises(TypeError, match=r'own request field "model_kwargs".*made on each call'):
            given(held)


@pytest.mark.parametrize(
    "fields",
    [
        {"model_kwargs": {"user": "Kim Minsu"}},
        {"model_kwargs": {"extra_body": {"metadata": {"user_id": "Kim Minsu"}}}},
        {"extra_body": {"user": "Kim Minsu"}},
        {"user_profile_id": "Kim Minsu"},
        {"stop": ["kim@example.com"]},
        {"model_kwargs": {"metadata": {"customer_number": 821023456789}}},
    ],
    ids=["model_kwargs", "extra_body-in-kwargs", "extra_body", "own-field-by-alias", "stop", "number"],
)
def test_every_field_of_the_request_is_traced_to_the_field_it_comes_from(fields):
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    from newmean_pii.langchain import _request_fields_of

    model = _openai_like([], **fields)
    seen = []
    _middleware().wrap_model_call(ModelRequest(model=model, messages=[HumanMessage("hi")]), lambda r: (seen.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    copy = seen[0].model
    assert copy is not model and type(copy) is type(model)
    assert not LEAK.search(repr(_request_fields_of(copy)))
    assert LEAK.search(repr(_request_fields_of(model)))


def test_a_provider_package_that_spreads_model_kwargs_into_the_request_as_langchain_anthropic_does():
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from pydantic import Field

    sent: List[Dict[str, Any]] = []

    class Model(GenericFakeChatModel):
        model: str = "claude-x"
        model_kwargs: Dict[str, Any] = Field(default_factory=dict)
        container: Optional[str] = None
        user_profile_id: Optional[str] = None

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            messages = self._convert_input(input_).to_messages()
            return {"model": self.model, "messages": [m.content for m in messages], "container": self.container,
                    "user_profile_id": self.user_profile_id, **self.model_kwargs, **kwargs}

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
            sent.append(self._get_request_payload(messages, stop=stop, **kwargs))
            self.messages = iter([AIMessage("done")])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self.bind(**kwargs)

    model = Model(messages=iter([]), model_kwargs={"extra_body": {"metadata": {"user_id": "Kim Minsu"}}}, container="Kim Minsu", user_profile_id="Kim Minsu")
    create_agent(model, tools=[], middleware=[_middleware()]).invoke({"messages": [HumanMessage("Hi.")]})
    assert sent[0]["extra_body"] == {"metadata": {"user_id": "[PERSON_1]"}}
    assert sent[0]["user_profile_id"] == "[PERSON_1]"
    assert sent[0]["container"] == "Kim Minsu"  # (Anthropic's container id: kept where Anthropic defines it)
    assert model.user_profile_id == "Kim Minsu" and model.model_kwargs == {"extra_body": {"metadata": {"user_id": "Kim Minsu"}}}


def test_a_package_that_refuses_a_request_without_messages_and_a_model_without_a_request_builder():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from pydantic import Field

    from newmean_pii.langchain import _request_fields_of

    class Strict(GenericFakeChatModel):
        """Refuses to build a request for no messages."""

        model_kwargs: Dict[str, Any] = Field(default_factory=dict)

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            messages = self._convert_input(input_).to_messages()
            if not messages:
                raise ValueError("messages must not be empty")
            return {"model": "x", **self.model_kwargs, "messages": [m.content for m in messages], **kwargs}

    class Bare(GenericFakeChatModel):
        """Has the fields LangChain's provider packages pass into the request, but no request builder."""

        model_kwargs: Dict[str, Any] = Field(default_factory=dict)
        extra_body: Optional[Dict[str, Any]] = None

    for model in (Strict(messages=iter([]), model_kwargs={"user": "Kim Minsu"}),
                  Bare(messages=iter([]), model_kwargs={"user": "Kim Minsu"}, extra_body={"metadata": {"email": "kim@example.com"}})):
        seen = []
        _middleware().wrap_model_call(ModelRequest(model=model, messages=[HumanMessage("hi")]), lambda r: (seen.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
        fields = _request_fields_of(seen[0].model)
        assert fields is not None and fields["user"] == "[PERSON_1]", type(model).__name__
        assert not LEAK.search(repr(fields)) and LEAK.search(repr(_request_fields_of(model)))


def test_fails_before_sending_naming_the_field_when_the_copy_cannot_send_it_hidden():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from pydantic import PrivateAttr

    class Hidden(GenericFakeChatModel):
        """Puts a value of its own private state into the request: no field of the model gives it."""

        _user: str = PrivateAttr(default="Kim Minsu")

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            return {"model": "x", "user": self._user, "messages": [], **kwargs}

    sent = []
    request = ModelRequest(model=Hidden(messages=iter([])), messages=[HumanMessage("hi")])
    with pytest.raises(TypeError, match=r'own request field "user".*cannot be copied'):
        _middleware().wrap_model_call(request, lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert sent == []

    class Plain:
        """Not a pydantic chat model."""

        _default_params = {"model": "x", "user": "kim@example.com"}

    request = ModelRequest(model=Plain(), messages=[HumanMessage("hi")])
    with pytest.raises(TypeError, match=r'own request field "user".*pass a LangChain chat model'):
        _middleware().wrap_model_call(request, lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert sent == []


def test_a_model_made_on_each_call_fails_when_its_configuration_holds_a_known_value():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    base = pytest.importorskip("langchain.chat_models.base")
    made = base._ConfigurableModel(default_config={"model": "gpt-x", "model_kwargs": {"user": "Kim Minsu"}}, configurable_fields="any")
    sent = []
    with pytest.raises(TypeError, match=r'own request field "model_kwargs".*made on each call'):
        _middleware().wrap_model_call(ModelRequest(model=made, messages=[HumanMessage("hi")]), lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert sent == []
    # one whose configuration holds no known value is given as it is
    plain = base._ConfigurableModel(default_config={"model": "gpt-x", "model_kwargs": {"user": "u-17"}}, configurable_fields="any")
    _middleware().wrap_model_call(ModelRequest(model=plain, messages=[HumanMessage("hi")]), lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert sent[0].model is plain


# ---------------------------------------------------------------------------------------------------------------------
# gate 8 of the release standard 1.7.1, items 1 and 2: every field of the model that reaches the request body, whatever
# its key, built once into JSON; that one dump is masked and sent
# ---------------------------------------------------------------------------------------------------------------------

SAY = "Contact Kim Minsu."
HIDDEN = "Contact [PERSON_1]."


@pytest.mark.parametrize("mode", ["invoke", "ainvoke"])
@pytest.mark.parametrize(
    "own, where, expect",
    [
        # The outside review's inputs (item 1): ChatAnthropic(model_kwargs={"system": ...}) and {"messages": [...]}
        ({"system": SAY}, lambda b: b["system"], HIDDEN),
        ({"messages": [{"role": "user", "content": SAY}]}, lambda b: b["messages"], [{"role": "user", "content": HIDDEN}]),
        # siblings: blocks, keys, the other message-like keys, extra_body (merged into the body by the client)
        ({"system": [{"type": "text", "text": SAY, "cache_control": {"type": "ephemeral"}}]}, lambda b: b["system"],
         [{"type": "text", "text": HIDDEN, "cache_control": {"type": "ephemeral"}}]),
        ({"system": {"Kim Minsu": SAY}}, lambda b: b["system"], {"[PERSON_1]": HIDDEN}),
        ({"messages": [{"role": "user", "content": [{"type": "text", "text": "Write to kim@example.com for Kim Minsu."}]}]}, lambda b: b["messages"][0]["content"],
         [{"type": "text", "text": "Write to [EMAIL_1] for [PERSON_1]."}]),
        ({"input": [{"role": "user", "content": SAY}]}, lambda b: b["input"], [{"role": "user", "content": HIDDEN}]),
        ({"instructions": SAY}, lambda b: b["instructions"], HIDDEN),
        ({"extra_body": {"system": SAY, "messages": [{"role": "user", "content": SAY}]}}, lambda b: b["extra_body"],
         {"system": HIDDEN, "messages": [{"role": "user", "content": HIDDEN}]}),
        ({"tools": [{"name": "find_orders", "description": SAY, "input_schema": {"type": "object", "properties": {"q": {"type": "string", "description": SAY}}}}]},
         lambda b: b["tools"], [{"name": "find_orders", "description": HIDDEN, "input_schema": {"type": "object", "properties": {"q": {"type": "string", "description": HIDDEN}}}}]),
        # a number that is a known value in a message, as data
        ({"messages": [{"role": "user", "content": SAY, "phone": 821023456789}]}, lambda b: b["messages"][0]["phone"], "[PHONE_1]"),
    ],
    ids=["system", "messages", "system-blocks", "system-key", "messages-blocks", "input", "instructions", "extra-body", "tools", "number"],
)
def test_message_like_fields_given_in_model_kwargs_are_sent_hidden(own, where, expect, mode):
    sent: List[Dict[str, Any]] = []
    model = _anthropic_like(sent, "Hi [PERSON_1].", model_kwargs=own)
    out = _run(model, mode)
    assert len(sent) == 1 and where(sent[0]) == expect
    assert not LEAK.search(repr(sent[0]))
    # the placeholders the model's own fields put in are the call's: the answer gets the value back
    assert out["messages"][-1].content == "Hi Kim Minsu."
    assert model.model_kwargs == own  # (the developer's model is not changed)


@pytest.mark.parametrize("mode", ["invoke", "ainvoke"])
def test_a_message_like_key_from_a_field_of_that_name_is_sent_hidden_and_a_fake_models_answers_are_not_read(mode):
    from typing import Optional as Opt

    from newmean_pii.langchain import _own_message_keys

    sent: List[Dict[str, Any]] = []
    base = _anthropic_like(sent, "done")

    class WithSystem(type(base)):  # type: ignore[misc,valid-type]
        system: Opt[str] = None

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            return {**super()._get_request_payload(input_, stop=stop, **kwargs), **({"system": self.system} if self.system else {})}

    model = WithSystem(messages=iter([]), system=SAY)
    _run(model, mode)
    assert sent[0]["system"] == HIDDEN and model.system == SAY
    # (GenericFakeChatModel's own "messages" field is the iterator of its answers: not a request field)
    assert _own_message_keys(_openai_like([])) == set()


@pytest.mark.parametrize("mode", ["invoke", "ainvoke"])
@pytest.mark.parametrize(
    "make, fields, where, first",
    [
        # The outside review's input (item 2): ChatOpenAI(extra_body={"metadata": Metadata()}) whose second dump holds
        # the name
        (_openai_like, lambda: {"extra_body": {"metadata": _rewritten_model()}}, lambda b: b["extra_body"]["metadata"], {"owner": "control"}),
        (_openai_like, lambda: {"extra_body": {"metadata": {"rows": [_rewritten_model(text=True)]}}}, lambda b: b["extra_body"]["metadata"]["rows"], ["control"]),
        (_openai_like, lambda: {"model_kwargs": {"prompt_cache_key": _rewritten_model(text=True)}}, lambda b: b["prompt_cache_key"], "control"),
        (_openai_like, lambda: {"model_kwargs": {"metadata": {"note": _rewritten_model()}}}, lambda b: b["metadata"], {"note": {"owner": "control"}}),
        # message-like keys: the system prompt and messages a package spreads over the request
        (_anthropic_like, lambda: {"model_kwargs": {"messages": [{"role": "user", "content": _rewritten_model(text=True)}]}}, lambda b: b["messages"],
         [{"role": "user", "content": "control"}]),
        (_anthropic_like, lambda: {"model_kwargs": {"system": _rewritten_model(text=True)}}, lambda b: b["system"], "control"),
        # the first dump holds the known value: it is masked, and the later dumps are never written
        (_openai_like, lambda: {"extra_body": {"metadata": _rewritten_model("Kim Minsu", "kim@example.com")}}, lambda b: b["extra_body"]["metadata"], {"owner": "[PERSON_1]"}),
        (_anthropic_like, lambda: {"model_kwargs": {"system": _rewritten_model(SAY, "kim@example.com", text=True)}}, lambda b: b["system"], HIDDEN),
    ],
    ids=["extra-body-metadata", "extra-body-nested", "model-kwargs", "model-kwargs-metadata", "messages", "system", "first-dump-held", "system-first-dump-held"],
)
def test_the_models_own_data_is_sent_as_the_one_dump_that_was_masked(make, fields, where, first, mode):
    sent: List[Dict[str, Any]] = []
    model = make(sent, **fields())
    _run(model, mode)
    assert len(sent) == 1 and where(sent[0]) == first
    assert not LEAK.search(repr(sent[0]))


def test_a_model_that_cannot_be_copied_or_is_made_on_each_call_fails_when_its_request_holds_an_object_written_again():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    sent: List[Any] = []

    def call(model: Any) -> None:
        _middleware().wrap_model_call(ModelRequest(model=model, messages=[HumanMessage("hi")]), lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])

    class Bare:
        """Not a pydantic chat model: it cannot be given the snapshot."""

        def __init__(self) -> None:
            self._default_params = {"model": "x", "metadata": {"owner": _rewritten_model()}}

    with pytest.raises(TypeError, match=r'own request field "metadata" holds an object whose JSON the client writes again.*pass a LangChain chat model'):
        call(Bare())
    base = pytest.importorskip("langchain.chat_models.base")
    made = base._ConfigurableModel(default_config={"model": "gpt-x", "model_kwargs": {"metadata": _rewritten_model()}}, configurable_fields="any")
    with pytest.raises(TypeError, match=r'own request field "model_kwargs" holds an object whose JSON the client writes again.*made on each call'):
        call(made)
    assert sent == []


def test_a_field_the_copy_cannot_trace_fails_when_it_holds_an_object_written_again():
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from pydantic import PrivateAttr

    class Hidden(GenericFakeChatModel):
        """Puts an object of its own private state into the request: no field of the model gives it."""

        _note: Any = PrivateAttr(default_factory=_rewritten_model)

        def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> Dict[str, Any]:
            return {"model": "x", "note": self._note, **kwargs}

    sent: List[Any] = []
    request = ModelRequest(model=Hidden(messages=iter([])), messages=[HumanMessage("hi")])
    with pytest.raises(TypeError, match=r'own request field "note" holds an object whose JSON the client writes again.*copy cannot send it'):
        _middleware().wrap_model_call(request, lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert sent == []


def test_a_response_format_class_in_the_models_fields_whose_schema_holds_a_known_value_fails_the_call():
    import pydantic
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    class Summary(pydantic.BaseModel):
        """The account of Kim Minsu."""

        note: str = pydantic.Field(description="Write to kim@example.com.")

    class Plain(pydantic.BaseModel):
        note: str

    sent: List[Any] = []

    def call(model: Any) -> None:
        _middleware().wrap_model_call(ModelRequest(model=model, messages=[HumanMessage("hi")]), lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])

    with pytest.raises(TypeError, match=r'own request field "response_format" holds a value the vault knows'):
        call(_openai_like([], model_kwargs={"response_format": Summary}))
    assert sent == []
    model = _openai_like([], model_kwargs={"response_format": Plain})
    call(model)
    assert sent[0].model is model  # (a class whose schema holds no known value: the client writes it as it would)


def test_message_like_settings_hold_text_and_json_only():
    """(An object with no JSON form in the system prompt, the instructions or the input fails the call, as in data; in
    other fields a provider defines, such an object, an httpx timeout or a schema's class, stays as it is.)"""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    class Named:
        def __str__(self) -> str:
            return "Kim Minsu"

    sent: List[Any] = []
    for own in ({"model_kwargs": {"system": Named()}}, {"model_kwargs": {"instructions": [Named()]}}):
        with pytest.raises(TypeError, match="cannot be written as JSON"):
            _middleware().wrap_model_call(ModelRequest(model=_anthropic_like([], **own), messages=[HumanMessage("hi")]), lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
        with pytest.raises(TypeError, match="cannot be written as JSON"):
            _middleware().wrap_model_call(ModelRequest(model=_anthropic_like([]), messages=[HumanMessage("hi")], model_settings=own["model_kwargs"]),
                                          lambda r: (sent.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
    assert sent == []


def test_a_model_whose_fields_need_no_snapshot_is_given_as_it_is_and_one_that_does_is_copied():
    """(A date or a tuple without a known value: the client writes it the same way each time, or refuses it as it would
    without the middleware; a pydantic model is written by its own code, so its one dump is sent from a copy.)"""
    import datetime

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, HumanMessage

    def given(m: Any) -> Any:
        seen: List[Any] = []
        _middleware().wrap_model_call(ModelRequest(model=m, messages=[HumanMessage("hi")]), lambda r: (seen.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
        return seen[0].model

    plain = _openai_like([], model_kwargs={"metadata": {"since": datetime.date(2020, 1, 2)}, "stop": ("a", "b")}, extra_body={"at": datetime.datetime(2020, 1, 2, 9, 30)})
    assert given(plain) is plain
    held = _rewritten_model()
    model = _openai_like([], extra_body={"metadata": held, "since": datetime.date(2020, 1, 2)})
    copy = given(model)
    assert copy is not model and copy.extra_body == {"metadata": {"owner": "control"}, "since": "2020-01-02"}
    assert model.extra_body["metadata"] is held


# ---------------------------------------------------------------------------------------------------------------------
# the Python sibling of release gate 8, item 5: in the model's own fields, as in model settings, a provider's identifier
# names hold only at the exact paths where it defines them; one level away they are data
# ---------------------------------------------------------------------------------------------------------------------


def _nested(value: str = "kim@example.com") -> Dict[str, Any]:
    """The reviewer's settings (Responses, Anthropic, Chat Completions), and the paths of a container and a gateway's
    routing with a field of their own below them; value where a provider defines no identifier."""
    return {
        "reasoning": {"effort": "low", "previous_response_id": value, "file_id": value},
        "text": {"format": {"type": "text", "signature": value}},
        "thinking": {"type": "enabled", "budget_tokens": 1024, "signature": value, "item_id": value},
        "stream_options": {"include_usage": True, "item_id": value},
        "web_search_options": {"user_location": {"type": "approximate", "conversation_id": value}},
        "container": {"id": "cntr_1", "note": value},
        "provider": {"order": ["openai"], "note": value},
    }


@pytest.mark.parametrize("mode", ["invoke", "ainvoke"])
@pytest.mark.parametrize("route", ["openai-model-kwargs", "openai-extra-body", "anthropic-model-kwargs"])
def test_identifier_names_away_from_their_paths_in_the_models_own_fields_are_data(route, mode):
    sent: List[Dict[str, Any]] = []
    if route == "openai-model-kwargs":
        model = _openai_like(sent, model_kwargs=_nested())
    elif route == "openai-extra-body":
        model = _openai_like(sent, extra_body=_nested())
    else:
        model = _anthropic_like(sent, model_kwargs=_nested())
    _run(model, mode)
    assert len(sent) == 1
    body = sent[0]["extra_body"] if route == "openai-extra-body" else {k: v for k, v in sent[0].items() if k in _nested()}
    assert body == _nested("[EMAIL_1]")
    assert not LEAK.search(repr(sent[0]))
    # (the developer's model is not changed)
    assert (model.extra_body if route == "openai-extra-body" else model.model_kwargs) == _nested()


def _literal_list_keys(value: str = "kim@example.com") -> Dict[str, Any]:
    """Gate 8 of 1.7.1, the confirmation's item 5 (outside review): where a provider defines a list (mcp_servers[],
    models[], provider.order[], container.skills[]), an object whose key is written "[]"; value where the list item
    would be."""
    return {
        "mcp_servers": {"[]": {"type": "url", "name": value}},
        "models": {"[]": value},
        "provider": {"order": {"[]": value}},
        "container": {"id": "cntr_1", "skills": {"[]": {"type": "anthropic", "skill_id": value}}},
    }


@pytest.mark.parametrize("mode", ["invoke", "ainvoke"])
@pytest.mark.parametrize("route", ["openai-model-kwargs", "openai-extra-body", "anthropic-model-kwargs"])
def test_a_key_written_like_a_list_step_in_the_models_own_fields_is_data(route, mode):
    """The model's own fields are read as model settings are: a key "[]" is a key and never a list step, so the
    identifier name under it is data. The control: the same name in a list where a provider defines it stays (a gateway's
    models[] in extra_body; mcp_servers[].name)."""
    e = "kim@example.com"
    sent: List[Dict[str, Any]] = []
    if route == "openai-model-kwargs":
        model = _openai_like(sent, model_kwargs=_literal_list_keys(), extra_body={"models": [e]})
        expected = {**_literal_list_keys("[EMAIL_1]"), "extra_body": {"models": [e]}}
    elif route == "openai-extra-body":
        model = _openai_like(sent, extra_body=_literal_list_keys(), model_kwargs={"mcp_servers": [{"type": "url", "name": e}]})
        expected = {"extra_body": _literal_list_keys("[EMAIL_1]"), "mcp_servers": [{"type": "url", "name": e}]}
    else:
        model = _anthropic_like(sent, model_kwargs={**_literal_list_keys(), "extra_body": {"models": [e]}})
        expected = {**_literal_list_keys("[EMAIL_1]"), "extra_body": {"models": [e]}}
    _run(model, mode)
    assert len(sent) == 1
    assert {k: v for k, v in sent[0].items() if k in expected} == expected
