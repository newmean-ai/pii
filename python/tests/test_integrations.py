# LangChain and LiteLLM adapters. The detector needs neither library; the middleware and guardrail tests skip
# without langchain / litellm, and everything that runs a model skips without the local model copies.
import asyncio
import collections
import copy
import dataclasses
import datetime
import types
import uuid
from decimal import Decimal
from enum import Enum, IntEnum

import pytest

from newmean_pii.langchain import pii_detector, pii_middleware

TEXT = "Please call Kim Minsu on 010-1234-5678 or write to kim@example.com."


def test_detector_matches_spans(nano):
    detect = pii_detector(nano)
    spans = nano.detect(TEXT)
    matches = detect(TEXT)
    assert spans, "the model found nothing in the sample"
    assert [(m["start"], m["end"], m["type"]) for m in matches] == [(s.start, s.end, s.label.lower()) for s in spans]
    assert all(m["value"] == TEXT[m["start"] : m["end"]] for m in matches)
    assert detect("") == []


def test_detector_labels_filter(nano):
    matches = pii_detector(nano, labels=["EMAIL"])(TEXT)
    assert matches and {m["type"] for m in matches} == {"email"}


def test_detector_is_lazy_and_checks_arguments(nano):
    pii_detector(size="nano", model_dir="/nonexistent")  # nothing is loaded until the first call
    with pytest.raises(TypeError):
        pii_detector(nano, size="nano")


def test_langchain_middleware_redacts(nano):
    pytest.importorskip("langchain.agents.middleware")
    from langchain_core.messages import HumanMessage

    mw = pii_middleware(nano)
    out = mw.before_model({"messages": [HumanMessage(TEXT)]}, None)
    content = out["messages"][-1].content
    assert "kim@example.com" not in content and "010-1234-5678" not in content
    assert "[REDACTED_EMAIL]" in content and "[REDACTED_PHONE]" in content


def test_langchain_middleware_block(nano):
    pytest.importorskip("langchain.agents.middleware")
    from langchain.agents.middleware import PIIDetectionError
    from langchain_core.messages import HumanMessage

    mw = pii_middleware(nano, strategy="block")
    with pytest.raises(PIIDetectionError):
        mw.before_model({"messages": [HumanMessage(TEXT)]}, None)
    assert mw.before_model({"messages": [HumanMessage("The meeting is at three.")]}, None) is None


def test_litellm_guardrail_masks_texts(nano):
    pytest.importorskip("litellm")
    from newmean_pii.litellm import NewmeanPIIGuardrail

    g = NewmeanPIIGuardrail(redactor=nano, guardrail_name="newmean-pii", event_hook="pre_call", default_on=True)
    out = asyncio.run(g.apply_guardrail({"texts": [TEXT, "", "The meeting is at three."]}, {}, "request"))
    assert out["texts"][0] == nano.redact(TEXT).text
    assert "[EMAIL]" in out["texts"][0] and "[PHONE]" in out["texts"][0]
    assert out["texts"][1:] == ["", "The meeting is at three."]
    assert asyncio.run(g.apply_guardrail({}, {}, "request")) == {}


def test_litellm_guardrail_options():
    pytest.importorskip("litellm")
    from newmean_pii.litellm import NewmeanPIIGuardrail

    with pytest.raises(ValueError):
        NewmeanPIIGuardrail(redact_mode="pre_call")
    # config.yaml values: create_redactor options are kept for the first request, the rest goes to LiteLLM
    g = NewmeanPIIGuardrail(size="nano", labels=["EMAIL"], sensitivity=0.2, guardrail_name="g", event_hook="pre_call")
    assert g._args == ("nano", None) and g._options == {"sensitivity": 0.2, "labels": ["EMAIL"]}
    assert g.guardrail_name == "g"
    # allow / block as YAML writes them: [text, label] lists become the tuples create_redactor takes
    g = NewmeanPIIGuardrail(allow=["Seoul", ["Jeju", "ADDRESS"]], block=[["Bluebird", "ORG"]], guardrail_name="g", event_hook="pre_call")
    assert g._options == {"allow": ["Seoul", ("Jeju", "ADDRESS")], "block": [("Bluebird", "ORG")]}


def test_presidio_recognizer(nano):
    pytest.importorskip("presidio_analyzer")
    from newmean_pii.presidio import ENTITY_MAPPING, NewmeanPIIRecognizer, recognizers

    rec = NewmeanPIIRecognizer(redactor=nano)
    spans = nano.detect(TEXT)
    results = rec.analyze(TEXT, entities=[])
    assert [(r.start, r.end, r.entity_type) for r in results] == [(s.start, s.end, ENTITY_MAPPING[s.label]) for s in spans]
    assert all(r.score == 0.85 for r in results)
    assert {"EMAIL_ADDRESS", "PHONE_NUMBER"} <= {r.entity_type for r in results} <= set(rec.supported_entities)
    assert [r.entity_type for r in rec.analyze(TEXT, entities=["EMAIL_ADDRESS"])] == ["EMAIL_ADDRESS"]
    assert rec.analyze("", entities=[]) == []

    custom = NewmeanPIIRecognizer(redactor=nano, score=0.5, entity_mapping={"EMAIL": "MAIL", "PHONE": None})
    got = {r.entity_type for r in custom.analyze(TEXT)}
    assert "MAIL" in got and "PHONE_NUMBER" not in got and "PHONE_NUMBER" not in custom.supported_entities

    en, ko = recognizers(["en", "ko"], redactor=nano, score=0.6)
    assert (en.supported_language, ko.supported_language) == ("en", "ko") and en._shared is ko._shared
    assert ko.analyze(TEXT)[0].score == 0.6
    with pytest.raises(TypeError):
        NewmeanPIIRecognizer(redactor=nano, size="nano")
    with pytest.raises(ValueError):
        NewmeanPIIRecognizer(score=2)
    NewmeanPIIRecognizer(size="nano", model_dir="/nonexistent")  # nothing is loaded until the first text


def test_langfuse_mask(nano):
    from newmean_pii.langfuse import pii_mask

    mask = pii_mask(nano)
    masked = nano.redact(TEXT).text
    data = {"messages": [{"role": "user", "content": TEXT}, {"role": "user", "content": ""}], "n": 3, "pair": (TEXT, None), TEXT: 1.5}
    out = mask(data=data, extra="ignored")
    assert out == {"messages": [{"role": "user", "content": masked}, {"role": "user", "content": ""}], "n": 3, "pair": (masked, None), masked: 1.5}
    assert data["messages"][0]["content"] == TEXT  # the input is not changed
    assert mask(data=TEXT) == masked and mask(data=None) is None and mask(data=7) == 7
    assert "*" in pii_mask(nano, mode="mask")(data=TEXT)
    with pytest.raises(ValueError):
        pii_mask(nano, mode="redact")
    with pytest.raises(TypeError):
        pii_mask(nano, size="nano")
    pii_mask(size="nano", model_dir="/nonexistent")  # nothing is loaded until the first call


EMAIL = "kim@example.com"


@pytest.fixture()
def lf(nano):
    """(mask, M): the Langfuse mask on nano and the text redact gives for a string."""
    from newmean_pii.langfuse import pii_mask

    return pii_mask(nano), lambda s: nano.redact(s).text


def test_langfuse_keys_masked(lf):
    mask, M = lf
    assert M(EMAIL) != EMAIL
    assert mask(data={EMAIL: "x", 3: TEXT, (TEXT, 1): "y"}) == {M(EMAIL): "x", 3: M(TEXT), (M(TEXT), 1): "y"}


def test_langfuse_key_collisions(nano, lf):
    from newmean_pii.langfuse import pii_mask

    mask, M = lf
    assert M("a@b.com") == M("c@d.com") == "[EMAIL]"
    out = mask(data={"a@b.com": 1, "c@d.com": 2})
    assert list(out) == ["[EMAIL]", "[EMAIL] #2"] and list(out.values()) == [1, 2]
    blank = pii_mask(nano, mode="blank")(data={"a@b.com": 1, "c@d.com": 2})
    assert list(blank) == ["", " #2"] and list(blank.values()) == [1, 2]


def test_langfuse_key_collision_with_existing_tag(lf):
    mask, M = lf
    assert M("[EMAIL]") == "[EMAIL]"
    assert mask(data={"[EMAIL]": 1, "a@b.com": 2}) == {"[EMAIL]": 1, "[EMAIL] #2": 2}


def test_langfuse_containers(lf):
    mask, M = lf
    out = mask(data={TEXT})
    assert out == {M(TEXT)} and type(out) is set
    out = mask(data=frozenset([TEXT]))
    assert out == frozenset([M(TEXT)]) and type(out) is frozenset
    out = mask(data=collections.deque([TEXT]))
    assert out == [M(TEXT)] and type(out) is list
    out = mask(data=types.MappingProxyType({"k": TEXT}))
    assert out == {"k": M(TEXT)} and type(out) is dict


def test_langfuse_bytes(lf):
    mask, M = lf
    assert mask(data=TEXT.encode()) == M(TEXT).encode()
    out = mask(data=bytearray(TEXT.encode()))
    assert out == bytearray(M(TEXT).encode()) and type(out) is bytearray
    assert mask(data=b"\xff\xfe") == "[UNMASKED: bytes]"


@dataclasses.dataclass
class _Msg:
    role: str
    content: str


class _Dumps:
    def model_dump(self):
        return {"content": TEXT}


class _DictOnly:
    def dict(self):
        return {"content": TEXT}


class _BadDump:
    def model_dump(self):
        raise RuntimeError(TEXT)


class _Opaque:
    def __init__(self):
        self.secret = TEXT


def test_langfuse_dataclass_and_pydantic_like(lf):
    mask, M = lf
    assert mask(data=_Msg("user", TEXT)) == {"role": "user", "content": M(TEXT)}
    assert mask(data=_Dumps()) == {"content": M(TEXT)}
    assert mask(data=_DictOnly()) == {"content": M(TEXT)}


def test_langfuse_unknown_object_placeholder(lf):
    mask, _ = lf
    assert mask(data=_Opaque()) == "[UNMASKED: _Opaque]"
    assert mask(data={"o": _Opaque()}) == {"o": "[UNMASKED: _Opaque]"}


def test_langfuse_dump_failure_is_placeholder(lf):
    mask, _ = lf
    assert mask(data=_BadDump()) == "[UNMASKED: _BadDump]"


def test_langfuse_cycle(lf):
    mask, M = lf
    a = [TEXT]
    a.append(a)
    assert mask(data=a) == [M(TEXT), "[CYCLE]"]


def test_langfuse_shared_object_is_not_a_cycle(lf):
    mask, M = lf
    x = [TEXT]
    assert mask(data={"a": x, "b": x}) == {"a": [M(TEXT)], "b": [M(TEXT)]}


def test_langfuse_scalars_kept(lf):
    mask, _ = lf
    values = [None, True, 7, 1.5, 2j, Decimal("1.2"), datetime.date(2020, 1, 2), datetime.datetime(2020, 1, 2, 3),
              datetime.time(1), datetime.timedelta(1), uuid.UUID(int=1)]
    for v in values:
        out = mask(data=v)
        assert out == v and type(out) is type(v), v


class _S(str, Enum):
    A = EMAIL


class _I(IntEnum):
    A = 1


def test_langfuse_enums(lf):
    mask, M = lf
    assert mask(data=_S.A) == M(EMAIL)
    assert mask(data=_I.A) == 1


def test_langfuse_input_not_mutated(lf):
    mask, _ = lf
    data = {EMAIL: [{TEXT}, _Msg("user", TEXT)], "b": bytearray(TEXT.encode())}
    before = copy.deepcopy(data)
    mask(data=data)
    assert data == before


def test_langfuse_client_accepts_the_mask(nano):
    langfuse = pytest.importorskip("langfuse")
    from newmean_pii.langfuse import pii_mask

    client = langfuse.Langfuse(public_key="pk-test", secret_key="sk-test", host="http://127.0.0.1:9", mask=pii_mask(nano), tracing_enabled=False)
    assert client._mask(data={"input": TEXT}) == {"input": nano.redact(TEXT).text}

