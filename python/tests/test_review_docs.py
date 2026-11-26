"""What the READMEs say about the vault middleware, tested where a README sentence rests on behaviour no other test
pins (review round of SDK 1.7.1, class "docs")."""
import pytest

from newmean_pii import Span, Vault


class _Finder:
    """A detector that finds every occurrence of each (value, label) in the text it is given."""

    def __init__(self, pairs):
        self.pairs, self.sensitivity = pairs, 0.0

    def detect(self, text):
        out = []
        for value, label in self.pairs:
            i = text.find(value)
            while i >= 0:
                out.append(Span(i, i + len(value), label, "model"))
                i = text.find(value, i + len(value))
        return sorted(out, key=lambda s: s.start)


def test_the_middleware_masks_what_middleware_listed_before_it_adds_and_not_what_one_after_it_adds():
    """README "List it last" (guarantee note F10): the middleware masks the request it is handed, so a dynamic system
    prompt or a message added by a middleware listed before it is masked, and one listed after it reaches the model as
    written. Real create_agent."""
    pytest.importorskip("langchain.agents.middleware")
    from langchain.agents import create_agent
    from langchain.agents.middleware import dynamic_prompt, wrap_model_call
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from newmean_pii.langchain import pii_middleware

    @dynamic_prompt
    def profile_prompt(request):
        return "You help Kim Minsu (kim@example.com)."

    @wrap_model_call
    def add_profile(request, handler):
        return handler(request.override(messages=[HumanMessage("Profile: Kim Minsu, kim@example.com")] + list(request.messages)))

    class Model(GenericFakeChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            sent.append([m.content for m in messages])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def run(order):
        vault = Vault()
        vault.tag("PERSON", "Kim Minsu")
        vault.tag("EMAIL", "kim@example.com")
        mw = pii_middleware(_Finder([("Kim Minsu", "PERSON")]), vault=vault)
        sent.clear()
        agent = create_agent(Model(messages=iter([AIMessage("Done.")])), tools=[], middleware=order(mw))
        agent.invoke({"messages": [HumanMessage("Hi, I am Kim Minsu.")]})
        return sent[0]

    sent = []
    for other in (profile_prompt, add_profile):
        last = run(lambda mw: [other, mw])
        assert "Kim Minsu" not in str(last) and "kim@example.com" not in str(last), last
        assert any("[PERSON_1]" in str(c) and "[EMAIL_1]" in str(c) for c in last[:1]), last
        first = run(lambda mw: [mw, other])
        assert "Hi, I am [PERSON_1]." in first  # the user's message is masked either way
        assert any("Kim Minsu" in str(c) for c in first)  # what the later middleware added is not (the documented limit)


# The edges of a span the model found (L2-B2): the cases every package shares are in vault_cases.json ("redact"); these
# are the Python package's own.


def test_block_list_and_known_value_spans_keep_their_edges_and_a_secret_loses_only_its_white_space():
    from newmean_pii._vault import value_spans

    text = "Tiffany & Co. Kim Minsu. Kim Minsu. hunter2! x"
    spans = [Span(0, 13, "ORG", "block"), Span(14, 24, "PERSON", "vault"), Span(25, 35, "PERSON", "model"),
             Span(36, 45, "SECRET", "model")]
    assert [(s.start, s.end) for s in value_spans(text, spans)] == [(0, 13), (14, 24), (25, 34), (36, 44)]


def test_apply_redaction_with_a_vault_learns_the_value_too():
    from newmean_pii import apply_redaction

    text = "Please look up Kim Minsu."
    vault = Vault()
    assert apply_redaction(text, [Span(15, 25, "PERSON", "model")], vault=vault) == "Please look up [PERSON_1]."
    assert [e.value for e in vault.entries()] == ["Kim Minsu"]


class _Spanned:
    """A span of a detector of the application's own (not a NamedTuple)."""

    def __init__(self, start, end, label):
        self.start, self.end, self.label = start, end, label


def test_a_span_of_another_type_comes_back_as_a_span():
    from newmean_pii._vault import value_spans

    (s,) = value_spans("Ask (Kim Minsu) now", [_Spanned(4, 15, "PERSON")])
    assert (s.start, s.end, s.label, s.source) == (5, 14, "PERSON", "model")
