# LangChain and LiteLLM adapters. The detector needs neither library; the middleware and guardrail tests skip
# without langchain / litellm, and everything that runs a model skips without the local model copies.
import asyncio

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


def test_langfuse_client_accepts_the_mask(nano):
    langfuse = pytest.importorskip("langfuse")
    from newmean_pii.langfuse import pii_mask

    client = langfuse.Langfuse(public_key="pk-test", secret_key="sk-test", host="http://127.0.0.1:9", mask=pii_mask(nano), tracing_enabled=False)
    assert client._mask(data={"input": TEXT}) == {"input": nano.redact(TEXT).text}

