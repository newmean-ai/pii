# The Langfuse mask: every value of an observation masked or replaced, keys included (pull request #1). Everything
# that runs a model skips without the local model copies.
import array
import collections
import collections.abc
import copy
import dataclasses
import datetime
import types
import uuid
import warnings
from decimal import Decimal
from enum import Enum, IntEnum

import pytest

TEXT = "Please call Kim Minsu on 010-1234-5678 or write to kim@example.com."

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


def test_langfuse_dump_failure_replaces_the_whole_value(lf):
    # gate 8 of 1.7.1, item 8: a dump that raises is a container that cannot be read, so the whole value becomes
    # [MASK FAILED] as the README says, not a placeholder next to the parts that were read (before: [UNMASKED: _BadDump])
    mask, _ = lf
    assert mask(data=_BadDump()) == "[MASK FAILED]"
    assert mask(data={"status_text": "OK", "payload": _BadDump()}) == "[MASK FAILED]"


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


def test_langfuse_media_reference_kept(lf):
    media = pytest.importorskip("langfuse.media")
    mask, _ = lf
    item = media.LangfuseMedia(base64_data_uri="data:image/png;base64,iVBORw0KGgo=")
    assert mask(data={"url": item})["url"] is item  # Langfuse turns it into its media reference string


def test_langfuse_char_and_byte_sequences(lf):
    mask, M = lf
    assert mask(data=memoryview(EMAIL.encode())) == M(EMAIL).encode()
    assert mask(data=collections.UserString(EMAIL)) == M(EMAIL)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # typecode "u" is deprecated in 3.13
        assert mask(data=array.array("u", EMAIL)) == M(EMAIL)
    assert mask(data=array.array("b", EMAIL.encode())) == "[UNMASKED: array]"
    assert mask(data=range(3)) == [0, 1, 2]


@dataclasses.dataclass(frozen=True)
class _Frozen:
    v: str


@dataclasses.dataclass
class _Unset:
    v: str = dataclasses.field(init=False)


class _BadProperty:
    @property
    def model_dump(self):
        raise RuntimeError(TEXT)


def test_langfuse_access_failures_replace_the_whole_value(lf):
    # gate 8 of 1.7.1, item 8: fields that cannot be read (a dataclass field that is not set, a dump property that
    # raises) fail the whole value, as a mapping that cannot be read does (before: [UNMASKED: <type>])
    mask, M = lf
    assert mask(data={_Frozen(EMAIL)}) == {str({"v": M(EMAIL)})}
    assert mask(data=_Unset()) == "[MASK FAILED]"
    assert mask(data=_BadProperty()) == "[MASK FAILED]"
    assert mask(data={"a_note": "OK", "items": [_Unset()]}) == "[MASK FAILED]"


def test_langfuse_real_pydantic_model(lf):
    pydantic = pytest.importorskip("pydantic")
    mask, M = lf

    class Msg(pydantic.BaseModel):
        role: str
        content: str
        tags: set

    assert mask(data=Msg(role="user", content=TEXT, tags={EMAIL})) == {"role": "user", "content": M(TEXT), "tags": {M(EMAIL)}}


# applying pull request #1 (1.7.1): a switch for keys, the result per key kept, many keys that become one placeholder


def test_langfuse_keys_switch(nano, lf):
    from newmean_pii.langfuse import pii_mask

    _, M = lf
    assert pii_mask(nano, keys=False)(data={EMAIL: TEXT, "a@b.com": [{"c@d.com": 1}]}) == {EMAIL: M(TEXT), "a@b.com": [{"c@d.com": 1}]}
    with pytest.raises(TypeError, match="keys must be True or False"):
        pii_mask(nano, keys="no")


def test_langfuse_key_read_once(nano, monkeypatch):
    from newmean_pii.langfuse import pii_mask

    read = []
    detect_at = nano._detect_at
    monkeypatch.setattr(nano, "_detect_at", lambda text, settings: (read.append(text), detect_at(text, settings))[1])
    mask = pii_mask(nano)
    data = {EMAIL: "x", "role": "user", "content": "hi"}
    first = mask(data=data)
    assert mask(data=data) == first
    assert [read.count(k) for k in (EMAIL, "role", "content")] == [1, 1, 1] and read.count("user") == 2  # values every time
    before = nano.sensitivity
    try:
        nano.set_sensitivity(0.5)  # other settings: the keys are read again
        mask(data={"role": "user"})
        assert read.count("role") == 2
    finally:
        nano.set_sensitivity(before)


def test_langfuse_many_keys_one_placeholder(lf):
    mask, M = lf
    emails = [f"user{i}@example.com" for i in range(300)]
    assert all(M(e) == "[EMAIL]" for e in emails[:3])
    out = mask(data={e: i for i, e in enumerate(emails)})
    assert list(out)[:3] == ["[EMAIL]", "[EMAIL] #2", "[EMAIL] #3"] and out["[EMAIL] #300"] == 299 and len(out) == 300


# 1.7.1 gate items L6-5 and L6-6 (README section "Integrations", Langfuse): the ids of a trace and the keys that are
# field names are kept as written, when the pattern layer and the block list find nothing in them. Ids with a prefix of
# their own are kept wherever they stand; a UUID (or a LangGraph checkpoint namespace) only where the trace names an id.

UUID_TEXT = "6b191bf0-850d-4433-a05c-efce1291a371"
PREFIXED_IDS = [
    "chatcmpl-Bd0Kh8oOOL8dKLzdocJ2isAjIhKtJ",  # OpenAI chat completion
    "chatcmpl-" + UUID_TEXT,  # LiteLLM and other compatible servers
    "call_u8jzPde0IgxLd6GncfBAepfJ",  # OpenAI tool call
    "call_0_" + UUID_TEXT,  # DeepSeek tool call
    "resp_6f5da2cec255404e4fb440034d6608697a8d41bed440e504",  # OpenAI Responses
    "msg_54f31af3176813e02ea68ef786e4d3cea27d26934b484e73",
    "fc_3043b026c48bbf33feff9243a8f506b40928b5b7a767c76f",
    "rs_3043b026c48bbf33feff9243a8f506b40928b5b7a767c76f",
    "fp_536b3216fd",  # OpenAI system fingerprint
    "msg_01DavJA76rNicHTp8hkqdlm7",  # Anthropic message
    "toolu_01CTbyvHNsG9eh6Yo4gfqrc5",  # Anthropic tool use
    "srvtoolu_01CTbyvHNsG9eh6Yo4gfqrc5",
    "req_011CTbyvHNsG9eh6Yo4gfqrc5",
    "tooluse_kZJMlvQmRJ6eAyJE5GIl7Q",  # Amazon Bedrock tool use
    "lc_run--01a213f6-fbf0-72ee-92f7-78180fc0826b-0",  # LangChain message ids
    "lc_run--01a213f6-fbf0-72ee-92f7-78180fc0826b",
    "run-" + UUID_TEXT + "-0",
]
UUID_IDS = [
    UUID_TEXT,
    UUID_TEXT.upper(),
    "01a213f6-fbf0-72ee-92f7-78180fc0826b",  # a UUID 7
    "00000000-0000-0000-0000-000000000000",
    "model:2a2a8994-c2fd-bc92-1dcc-ba11a2ce6ddb",  # LangGraph checkpoint namespaces
    "tools:b2800225-4727-ed1e-6371-685fa63ab8c0|model:2a2a8994-c2fd-bc92-1dcc-ba11a2ce6ddb",
]
ID_KEYS = ["id", "_id", "uuid", "run_id", "runId", "run-id", "RUN_ID", "run_ids", "runIds", "parent_run_id", "session_id",
           "sessionId", "thread_id", "trace_id", "observation_id", "tool_call_id", "toolCallId", "call_id", "message_id",
           "request_id", "response_id", "checkpoint_ns", "langgraph_checkpoint_ns", "conversation_id"]
# where a UUID can be a person's identifier: read by the model
OTHER_KEYS = ["user_id", "userId", "customer_id", "device_id", "advertising_id", "aaid", "pix_key", "note", "ids_of_users",
              "광고ID", "items", "value"]
# looks like an id, and is not one this mask keeps: read by the model
NOT_IDS = [
    "call_u8jzPde0IgxL",  # 12 characters after the prefix
    "call_u8jzPde0IgxLd6GncfBAepfJ!",
    " call_u8jzPde0IgxLd6GncfBAepfJ",
    "call-u8jzPde0IgxLd6GncfBAepfJ",
    "CALL_u8jzPde0IgxLd6GncfBAepfJ",
    "resp_6F5DA2CEC255404E4FB440034D6608697A8D41BED440E504",  # Responses ids are written in lower case
    "sk-u8jzPde0IgxLd6GncfBAepfJ",  # a key, not an id
    "lc_run--" + UUID_TEXT + "x",
    "Kim Minsu " + UUID_TEXT,
    UUID_TEXT.replace("-", ""),  # a UUID without hyphens (also how 128-bit secrets are written)
    "{" + UUID_TEXT + "}",
    "urn:uuid:" + UUID_TEXT,
    "Kim Minsu:" + UUID_TEXT,
]


class _UserStr(collections.UserString):
    pass


def _tagged(**kwargs):
    from newmean_pii.langfuse import pii_mask

    return pii_mask(_Tags(), **kwargs)


def test_langfuse_prefixed_ids_kept_wherever_they_stand():
    mask = _tagged()
    for v in PREFIXED_IDS:
        assert mask(data=v) == v, v
        # keys that are field names (kept), so that each value can be found again
        out = mask(data={"a_note": v, v: "x", "a_list": [v], "a_tuple": (v,), "a_set": {v}, "the_bytes": v.encode(),
                         "byte_array": bytearray(v.encode()), "user_string": _UserStr(v), "a_message": _Msg("ai", v),
                         "deep_down": _deep("mixed", 50, v)})
        assert out == {"a_note": v, v: "[TAG]", "a_list": [v], "a_tuple": (v,), "a_set": {v}, "the_bytes": v.encode(),
                       "byte_array": bytearray(v.encode()), "user_string": v, "a_message": {"[TAG]": "[TAG]", "[TAG] #2": v},
                       "deep_down": out["deep_down"]}, v
        _check_deep(out["deep_down"], "mixed", 50, v, key="[TAG]")
        assert mask(data={"a_note": v + " and Kim Minsu"}) == {"a_note": "[TAG]"}


def test_langfuse_prefixed_ids_as_keys():
    mask = _tagged()
    for v in PREFIXED_IDS:
        assert mask(data={v: "result"}) == {v: "[TAG]"}, v


def test_langfuse_uuids_kept_where_the_trace_names_an_id():
    mask = _tagged()
    for v in UUID_IDS:
        for key in ID_KEYS:
            assert mask(data={key: v}) == {key: v}, (key, v)
            assert mask(data={key: [v, (v,), {v}]}) == {key: [v, (v,), {v}]}, (key, v)
        # gate 8 of 1.7.1, item 6: a key is never where the trace names an id, also inside a map under an id key (the
        # README keeps the value of an id key and the items of a list there; before, this key was kept)
        assert mask(data={"run_ids": {v: 1}}) == {"run_ids": {"[TAG]": 1}}, v
        # (the stand-in detector tags every key that is not kept, "role" and "content" too)
        assert mask(data=_Msg(role="ai", content=v)) == {"[TAG]": "[TAG]", "[TAG] #2": "[TAG]"}
        for key in OTHER_KEYS:
            assert list(mask(data={key: v}).values()) == ["[TAG]"], (key, v)
            assert list(mask(data={key: {"id": v}}).values()) == [{"id": v}], (key, v)  # the nearest key decides
        assert mask(data=v) == "[TAG]" and mask(data=[v]) == ["[TAG]"] and mask(data={v: 1}) == {"[TAG]": 1}
    # the fields of a dataclass and a pydantic-like object are keys like any other
    box = dataclasses.make_dataclass("_Run", ["run_id", "user_id"])(UUID_TEXT, UUID_TEXT)
    assert mask(data=box) == {"run_id": UUID_TEXT, "user_id": "[TAG]"}


def test_langfuse_strings_like_ids_are_read():
    mask = _tagged()
    for v in NOT_IDS:
        assert mask(data={"id": v}) == {"id": "[TAG]"}, v


def test_langfuse_ids_not_kept_when_the_pattern_layer_finds_something():
    from newmean_pii import regex_spans

    mask = _tagged()
    found = ["call_abcdefghij01012345678", "chatcmpl-abcdefghij4111111111111111", "msg_01ABCDEFGHIJ4155550123",
             "toolu_01ABCDEFGHIJ-010-1234-5678", "tooluse_abcdefgh-010-1234-5678", "lc_run--" + UUID_TEXT + "-010123456",
             "ab0101234567:" + UUID_TEXT]
    for v in found:
        assert regex_spans(v), v
        assert mask(data={"id": v, "call_id": [v]}) == {"id": "[TAG]", "call_id": ["[TAG]"]}, v
    # the random hex of a UUID or a hex id is not read: the pattern layer finds phone and card numbers there by chance
    random_hex = ["7d527973-2285-4573-9ecb-a9443395fa5c", "12345678-1234-1234-1234-123456789012",
                  "a2345678-1234-5678-9012-a23456789012", "fp_4155550123", "resp_" + "0" * 30 + "01012345678",
                  "model:7d527973-2285-4573-9ecb-a9443395fa5c", "lc_run--12345678-1234-1234-1234-123456789012-0"]
    for v in random_hex:
        assert regex_spans(v), v
        assert mask(data={"id": v, "call_id": [v]}) == {"id": v, "call_id": [v]}, v


def test_langfuse_ids_and_field_names_not_kept_when_the_block_list_holds_them(tmp_path):
    from conftest import has_model, model_dir

    if not has_model("nano", "q4"):
        pytest.skip("no model copy")
    from newmean_pii import create_redactor
    from newmean_pii.langfuse import pii_mask

    held = [(UUID_TEXT, "ID_NUMBER"), ("ghost_rider", "USERNAME"), ("kZJMlvQmRJ6eAyJE5GIl7Q", "SECRET")]
    r = create_redactor("nano", "q4", model_dir=model_dir("nano"), block=held)
    mask = pii_mask(r)
    out = mask(data={"session_id": UUID_TEXT, "id": UUID_TEXT.upper(), "ghost_rider": 1, "user_name": 2,
                     "call_id": "tooluse_ghost_rider_x1y2z3w4v5", "tool_call_id": "tooluse_kZJMlvQmRJ6eAyJE5GIl7Q",
                     "run_id": "lc_run--" + UUID_TEXT + "-0", "thread_id": "01a213f6-fbf0-72ee-92f7-78180fc0826b"})
    assert UUID_TEXT not in str(out).lower() and "ghost_rider" not in str(out) and "kZJMlvQmRJ6eAyJE5GIl7Q" not in str(out)
    assert out["user_name"] == 2 and out["thread_id"] == "01a213f6-fbf0-72ee-92f7-78180fc0826b"
    # the same keys and ids without the block list are kept
    plain = pii_mask(create_redactor("nano", "q4", model_dir=model_dir("nano")))
    out = plain(data={"session_id": UUID_TEXT, "ghost_rider": 1, "tool_call_id": "tooluse_kZJMlvQmRJ6eAyJE5GIl7Q"})
    assert out == {"session_id": UUID_TEXT, "ghost_rider": 1, "tool_call_id": "tooluse_kZJMlvQmRJ6eAyJE5GIl7Q"}


def test_langfuse_ids_switch():
    from newmean_pii.langfuse import pii_mask

    mask = _tagged(ids=False)
    for v in PREFIXED_IDS + UUID_IDS:
        assert mask(data={"id": v, "a_note": [v]}) == {"id": "[TAG]", "a_note": ["[TAG]"]}, v
    assert mask(data={"user_id": 1, "id": 2}) == {"user_id": 1, "id": 2}  # field names are still kept
    with pytest.raises(TypeError, match="ids must be True or False"):
        pii_mask(_Tags(), ids="no")


FIELD_NAMES = ["user_id", "user_name", "user_email", "user_mail", "user_ip", "lc_run_id", "tool_call_id", "beneficiary_bank",
               "payout_method", "ls_model_name", "USER_ID", "API_KEY", "userName", "toolCallId", "promptTokens",
               # keys that name an id, in any spelling
               "id", "ID", "_id", "uuid", "run-id", "run.id", "Run ID", "parent.run.id", "x_ids"[2:]]
# keys of other shapes, which personal values also take: read by the model
NOT_FIELD_NAMES = ["role", "Kim Minsu", "Minsu", "jukka.nieminen", "minsu_kim92", "ghost-rider", "x-request-id",
                   "gen_ai.usage.input_tokens", "user.id", "UserName", "userID", "user__id", "_user_id", "user_id_",
                   "user id", "antall_forsøk", "사용자_이름", "a" + "_b" * 40, EMAIL, "010-1234-5678"]


def test_langfuse_field_name_keys_kept():
    mask = _tagged()
    for k in FIELD_NAMES:
        assert mask(data={k: 1}) == {k: 1}, k
        assert mask(data={"a_note": {k: [k]}}) == {"a_note": {k: ["[TAG]"]}}, k  # as a value, a field name is text
    for k in NOT_FIELD_NAMES:
        assert mask(data={k: 1}) == {"[TAG]": 1}, k
    assert _tagged(keys=False)(data={k: 1 for k in NOT_FIELD_NAMES}) == {k: 1 for k in NOT_FIELD_NAMES}


@pytest.fixture(scope="module")
def medium():
    from conftest import has_model, model_dir

    if not has_model("medium", "q4e"):
        pytest.skip("no model copy")
    from newmean_pii import create_redactor

    return create_redactor("medium", "q4e", model_dir=model_dir("medium"))


def _trace_like(rng):
    """Input, output and metadata as a LangChain agent's trace gives them to the mask (ids random, from rng)."""
    import random
    import string

    b62 = string.ascii_letters + string.digits

    def pick(chars, k):
        return "".join(rng.choice(chars) for _ in range(k))

    def uid(version=4):
        u = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        return u[:14] + str(version) + u[15:]  # uuid.UUID takes version 7 only from Python 3.14

    call, run, sid = "call_" + pick(b62, 24), f"lc_run--{uid(7)}-0", uid()
    ids = [call, run, sid, "chatcmpl-" + pick(b62, 29), "fp_" + pick("0123456789abcdef", 10), "toolu_01" + pick(b62, 22),
           "resp_" + pick("0123456789abcdef", 48), f"model:{uid()}", uid()]
    data = {
        "messages": [
            {"role": "user", "content": TEXT, "id": ids[8]},
            {"role": "assistant", "content": "", "id": run,
             "tool_calls": [{"id": call, "name": "lookup", "args": {"email": EMAIL}, "type": "tool_call"}],
             "response_metadata": {"id": ids[3], "system_fingerprint": ids[4], "model_name": "gpt-4o-mini"}},
            {"role": "tool", "content": "Kim Minsu, 010-1234-5678", "tool_call_id": call},
            {"role": "assistant", "content": [{"type": "tool_use", "id": ids[5], "input": {"name": NAME}}],
             "response_metadata": {"id": ids[6]}},
        ],
        "session_id": sid, "langgraph_checkpoint_ns": ids[7], "user_id": NAME, "user_name": EMAIL, "user_email": EMAIL,
        "user_mail": "lee.jiwoo@example.org", "user_ip": "192.0.2.1", "lc_run_id": run, "tool_call_id": call,
        EMAIL: "the key is an e-mail address", NAME: "the key is a name",
    }
    return data, ids


@pytest.mark.parametrize("size", ["nano", "medium"])
def test_langfuse_trace_ids_and_field_names_with_the_models(size, request):
    import random

    from newmean_pii.langfuse import pii_mask

    r = request.getfixturevalue(size)
    mask = pii_mask(r)
    rng = random.Random(1125)
    for _ in range(20):
        data, ids = _trace_like(rng)
        out = mask(data=data)
        sent = repr(out)
        assert [v for v in ids if v not in sent] == []
        assert [p for p in PII if p in sent] == [] and "192.0.2.1" not in sent
        assert {"messages", "session_id", "langgraph_checkpoint_ns", "user_id", "user_name", "user_email", "user_mail",
                "user_ip", "lc_run_id", "tool_call_id"} <= set(out)
        assert out["session_id"] == data["session_id"] and out["messages"][2]["tool_call_id"] == data["tool_call_id"]


# 23rd outside review (B1): a mask that fails sends one fixed text in place of the whole value. Langfuse's own fallback
# for an exception keeps every metadata key ({"kim@example.com": "<fully masked due to failed mask function>"}) and logs
# the exception's message, so the mask never raises: input, output and metadata alike, keys masked or kept.

FAILED = "[MASK FAILED]"
NAME = "Kim Minsu"
PII = (EMAIL, NAME, "010-1234-5678", "lee.jiwoo@example.org")


class _Down:
    """A detector that is unavailable; its message holds a value, as an error's message can."""

    def redact(self, *args, **kwargs):
        raise RuntimeError("detector unavailable while reading " + EMAIL)


class _Tags:
    """A detector that tags every string, and fails on the string ``fail_on`` or on its ``fail_at``-th call."""

    def __init__(self, fail_on=None, fail_at=None):
        self.fail_on, self.fail_at, self.calls = fail_on, fail_at, 0

    def redact(self, text, mode="tag"):
        self.calls += 1
        if text == self.fail_on or self.calls == self.fail_at:
            raise ValueError("cannot read " + text)
        return types.SimpleNamespace(text="[TAG]")


class _BadMap(collections.abc.Mapping):
    """A mapping whose values cannot be read."""

    def __iter__(self):
        return iter([EMAIL])

    def __len__(self):
        return 1

    def __getitem__(self, key):
        raise KeyError(key)


SHAPES = [
    {EMAIL: "open", NAME: "010-1234-5678"},  # metadata, which Langfuse writes per key
    {"by_email": {EMAIL: {"status": "open"}}, "tags": {"lee.jiwoo@example.org"}},
    [{"role": "user", "content": TEXT}],
    TEXT,
    TEXT.encode(),
    {(EMAIL, 1): [NAME]},
    _Msg("user", TEXT),
]


@pytest.mark.parametrize("keys", [True, False])
@pytest.mark.parametrize("data", SHAPES, ids=[type(s).__name__ + str(i) for i, s in enumerate(SHAPES)])
def test_langfuse_detector_failure_replaces_the_whole_value(data, keys):
    from newmean_pii.langfuse import pii_mask

    assert pii_mask(_Down(), keys=keys)(data=data) == FAILED


def test_langfuse_failure_after_some_values_masked_sends_none_of_them():
    from newmean_pii.langfuse import pii_mask

    data = {"a": "first", "b": [NAME, EMAIL], EMAIL: "last"}
    for at in range(1, 8):  # the failure at each of the seven strings read, keys included
        assert pii_mask(_Tags(fail_at=at))(data=data) == FAILED, at
    assert pii_mask(_Tags(fail_at=8))(data=data) == {"[TAG]": "[TAG]", "[TAG] #2": ["[TAG]", "[TAG]"], "[TAG] #3": "[TAG]"}


def test_langfuse_failure_when_the_model_cannot_load(tmp_path):
    from newmean_pii.langfuse import pii_mask

    mask = pii_mask(model_dir=tmp_path)  # an empty folder: create_redactor raises ModelFileError on the first call
    assert mask(data={EMAIL: "open"}) == FAILED
    assert mask(data=TEXT) == FAILED  # tried again on the next call, and fails the same way


def test_langfuse_failure_of_a_closed_redactor():
    from conftest import has_model, model_dir

    if not has_model("nano", "q4"):
        pytest.skip("no model copy")
    from newmean_pii import Redactor
    from newmean_pii.langfuse import pii_mask

    r = Redactor("nano", "q4", model_dir=model_dir("nano"))
    mask = pii_mask(r)
    assert mask(data={EMAIL: "open"}) == {"[EMAIL]": "open"}
    r.close()  # the key read once is kept; a new key and every value now raise "the redactor is closed"
    assert mask(data={EMAIL: "open"}) == FAILED
    assert mask(data={"lee.jiwoo@example.org": 1}) == FAILED


def test_langfuse_failure_while_reading_a_container():
    from newmean_pii.langfuse import pii_mask

    assert pii_mask(_Tags())(data={EMAIL: _BadMap()}) == FAILED


# 1.7.1 review (L6-1): nesting of any depth is masked, as Langfuse's own serializer writes it. Before, the walk was
# recursive and a value nested about 500 deep became [MASK FAILED] as a whole.

_Box = dataclasses.make_dataclass("_Box", ["v"])
_Pair = collections.namedtuple("_Pair", "v")
DEEP_KINDS = ["list", "dict", "tuple", "namedtuple", "frozenset", "dataclass", "mixed"]
MIXED = ["list", "dict", "tuple", "namedtuple", "dataclass"]  # a frozenset only holds hashable values


def _kind_at(kind, level):
    """The container kind of one level (counted from the leaf): "mixed" turns through MIXED."""
    return MIXED[level % len(MIXED)] if kind == "mixed" else kind


def _deep(kind, depth, leaf):
    """leaf inside depth containers of the kind, built without recursion."""
    out = leaf
    for level in range(depth):
        k = _kind_at(kind, level)
        out = {"list": lambda v: [v], "dict": lambda v: {"k": v}, "tuple": lambda v: (v,), "namedtuple": _Pair,
               "frozenset": lambda v: frozenset([v]), "dataclass": _Box}[k](out)
    return out


def _check_deep(out, kind, depth, leaf, key="[TAG]"):
    """out is leaf inside depth levels of what the mask makes of each kind (a dataclass and a dict become a dict whose
    one key is the masked key), checked without recursion (== on deep values recurses)."""
    for level in reversed(range(depth)):
        k = _kind_at(kind, level)
        if k in ("dict", "dataclass"):
            assert type(out) is dict and list(out) == [key], (level, k, out if not isinstance(out, (list, dict)) else type(out))
            out = out[key]
        elif k == "frozenset":
            assert type(out) is frozenset and len(out) == 1, (level, k)
            out = next(iter(out))
        else:
            # (a named tuple becomes a tuple: the mask returns Python's own types only, gate 8 of 1.7.1)
            assert type(out) is {"list": list, "tuple": tuple, "namedtuple": tuple}[k] and len(out) == 1, (level, k, type(out))
            out = out[0]
    assert out == leaf


def _deep_enough():
    import sys

    return max(5000, sys.getrecursionlimit() * 4)


@pytest.mark.parametrize("kind", DEEP_KINDS)
def test_langfuse_nesting_of_any_depth_is_masked(kind):
    from newmean_pii.langfuse import pii_mask

    n = _deep_enough()
    out = pii_mask(_Tags())(data={EMAIL: _deep(kind, n, EMAIL)})
    assert type(out) is dict and list(out) == ["[TAG]"]
    _check_deep(out["[TAG]"], kind, n, "[TAG]")
    _check_deep(pii_mask(_Tags())(data=_deep(kind, 50, _S.A)), kind, 50, "[TAG]")  # an Enum at the bottom


def test_langfuse_deep_keys_and_cycles():
    from newmean_pii.langfuse import pii_mask

    n = _deep_enough()
    # a key that is a tuple nested deep (a dict key is hashed when it is put in, so it is no deeper than that)
    key = _deep("tuple", 2000, EMAIL)
    out = pii_mask(_Tags())(data={key: NAME})
    (masked, value), = out.items()
    assert value == "[TAG]"
    _check_deep(masked, "tuple", 2000, "[TAG]")
    out = pii_mask(_Tags(), keys=False)(data={key: NAME})
    assert list(out.values()) == ["[TAG]"] and next(iter(out)) is key
    # a cycle closed at the bottom of a deep list
    top = cur = []
    for _ in range(n):
        cur.append([])
        cur = cur[0]
    cur.append(top)
    _check_deep(pii_mask(_Tags())(data=top), "list", n + 1, "[CYCLE]")
    # the same container twice on one level, deep down: not a cycle
    shared = [EMAIL]
    _check_deep(pii_mask(_Tags())(data=_deep("list", n, {"a": shared, "b": shared})), "list", n,
                {"[TAG]": ["[TAG]"], "[TAG] #2": ["[TAG]"]})


def test_langfuse_failure_deep_down_replaces_the_whole_value():
    from newmean_pii.langfuse import pii_mask

    n = _deep_enough()
    assert pii_mask(_Tags(fail_on=EMAIL))(data={NAME: _deep("mixed", n, EMAIL)}) == FAILED
    assert pii_mask(_Tags())(data={NAME: _deep("mixed", n, _BadMap())}) == FAILED


def test_langfuse_deep_nesting_with_the_model(lf):
    mask, M = lf
    out = mask(data={EMAIL: _deep("dict", 1200, TEXT)})
    assert list(out) == [M(EMAIL)]
    _check_deep(out[M(EMAIL)], "dict", 1200, M(TEXT), key=M("k"))


# 1.7.1 review (L6-1): the options are checked when the mask is built, so a typo raises there and not, as
# [MASK FAILED], on every observation. The model is still loaded on the first call.


def _list_error():
    from newmean_pii import ListError

    return ListError


@pytest.mark.parametrize("options, error, match", [
    (dict(size="medum"), ValueError, "unknown size"),
    (dict(size="medium", variant="q9"), ValueError, "unknown variant"),
    (dict(variant="q9"), ValueError, "unknown variant"),
    (dict(labels=["EMAILS"]), ValueError, "unknown label"),
    (dict(labels="EMAILS"), ValueError, "unknown label"),
    (dict(sensitivity=float("nan")), ValueError, "finite"),
    (dict(sensitivity="high"), ValueError, "float"),
    (dict(allow=[123]), "ListError", "allow list entry 0"),
    (dict(allow="Paris"), "ListError", "allow list"),
    (dict(block=[("Kim Minsu", "NAME")]), "ListError", "block list entry 0: unknown_label"),
    (dict(block=["Kim Minsu"]), "ListError", "block list entry 0"),
    (dict(lists_id=""), "ListError", "lists_id"),
    (dict(sise="medium"), TypeError, "sise"),
    (dict(size="nano", modeldir="/opt/models"), TypeError, "modeldir"),
])
def test_langfuse_options_are_checked_when_the_mask_is_built(options, error, match, tmp_path):
    from newmean_pii.langfuse import pii_mask

    error = _list_error() if error == "ListError" else error
    with pytest.raises(error, match=match):
        pii_mask(model_dir=tmp_path, **options)  # an empty folder: the check needs no model file


@pytest.mark.parametrize("redactor", ["medium", object(), 3])
def test_langfuse_redactor_without_redact_is_refused(redactor):
    from newmean_pii.langfuse import pii_mask

    with pytest.raises(TypeError, match="redact"):
        pii_mask(redactor)


def test_langfuse_options_checked_without_loading_the_model(tmp_path):
    from newmean_pii.langfuse import pii_mask

    pii_mask(size="medium", model_dir=tmp_path, labels=["EMAIL"], allow=["Paris"], block=[("Kim Minsu", "PERSON")],
             sensitivity=0.5, lists_id="crm")  # an empty folder: nothing is read before the first call


def test_langfuse_options_given_as_iterators_reach_the_redactor():
    from conftest import has_model, model_dir

    if not has_model("nano", "q4"):
        pytest.skip("no model copy")
    from newmean_pii.langfuse import pii_mask

    # read once when the mask is built and once more on the first call: a one-shot iterator must not be empty then
    # (labels: the phone number stays; block: the made-up name is masked; allow: the e-mail address stays)
    mask = pii_mask(size="nano", model_dir=model_dir("nano"), labels=iter(["PERSON", "EMAIL"]),
                    block=(x for x in [("Zorblax", "PERSON")]), allow=iter([EMAIL]))
    text = "The ticket from Zorblax was sent to kim@example.com, call 010-1234-5678 in Seoul."
    assert mask(data=text) == "The ticket from [PERSON] was sent to kim@example.com, call 010-1234-5678 in Seoul."


def test_langfuse_failure_offline_with_nothing_cached(monkeypatch, tmp_path):
    from newmean_pii.langfuse import pii_mask

    monkeypatch.setenv("NEWMEAN_PII_OFFLINE", "1")  # the README's offline mode, with an empty cache
    mask = pii_mask(size="nano", cache_dir=tmp_path)
    assert mask(data={EMAIL: {"plan": "pro"}, NAME: "vip"}) == FAILED
    assert mask(data=TEXT) == FAILED


def test_langfuse_failure_is_logged_without_its_message(caplog):
    from newmean_pii.langfuse import pii_mask

    with caplog.at_level("DEBUG"):
        assert pii_mask(_Down())(data={EMAIL: TEXT}) == FAILED
    assert [(r.name, r.levelname) for r in caplog.records] == [("newmean_pii.langfuse", "ERROR")]
    assert "RuntimeError" in caplog.text and FAILED in caplog.text
    assert [p for p in PII if p in caplog.text] == []
    assert caplog.records[0].exc_info is None


def test_langfuse_failure_leaves_the_mask_usable():
    from newmean_pii.langfuse import pii_mask

    mask = pii_mask(_Tags(fail_on="bad"))
    assert mask(data={"k": "bad"}) == FAILED
    assert mask(data={"k": "good"}) == {"[TAG]": "[TAG]"}


def test_langfuse_interrupt_is_not_swallowed():
    from newmean_pii.langfuse import pii_mask

    class Interrupted:
        def redact(self, *args, **kwargs):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pii_mask(Interrupted())(data={EMAIL: "x"})


class _Exporter:
    """A local server that takes the Langfuse client's OTLP exports, and what it received."""

    def __init__(self):
        import gzip
        import http.server
        import threading

        bodies = self.bodies = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                bodies.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.host = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("keys", [True, False])
def test_langfuse_real_client_failure_exports_no_original_key(keys, caplog, tmp_path, monkeypatch):
    pytest.importorskip("langfuse")
    from langfuse import Langfuse

    from newmean_pii.langfuse import pii_mask

    monkeypatch.setenv("NEWMEAN_PII_OFFLINE", "1")  # the README's offline mode: nothing cached in tmp_path
    exporter = _Exporter()
    try:
        failing = [pii_mask(_Down(), keys=keys), pii_mask(model_dir=tmp_path, keys=keys),
                   pii_mask(size="nano", cache_dir=tmp_path, keys=keys)]
        for n, mask in enumerate(failing):
            # a public key of its own: Langfuse keeps one client, and its mask, per public key
            lf = Langfuse(public_key="pk-%s-%s-%d" % (uuid.uuid4().hex, keys, n), secret_key="sk", host=exporter.host, mask=mask)
            with caplog.at_level("WARNING"):
                with lf.start_as_current_observation(name="lookup", as_type="tool", input=SHAPES[1], metadata=SHAPES[0]) as obs:
                    obs.update(output={NAME: [EMAIL]}, metadata={EMAIL: "closed"})
                with lf.start_as_current_observation(name="answer", as_type="generation", input=TEXT, metadata=SHAPES[0]) as gen:
                    gen.update(output=TEXT)
                lf.flush()
                lf.shutdown()
    finally:
        exporter.close()
    sent = b"".join(exporter.bodies)
    assert [p for p in PII if p.encode() in sent] == []
    assert [p for p in PII if p in caplog.text] == []
    assert FAILED.encode() in sent


def test_langfuse_real_client_exports_deep_metadata_masked(nano):
    """1.7.1 review (L6-1): metadata nested 1,200 deep is masked, not failed. Langfuse's serializer writes 20 levels of
    it (and "<dict>" below), so the masked text stands near the top."""
    pytest.importorskip("langfuse")
    from langfuse import Langfuse

    from newmean_pii.langfuse import pii_mask

    exporter = _Exporter()
    try:
        lf = Langfuse(public_key="pk-deep-%s" % uuid.uuid4().hex, secret_key="sk", host=exporter.host, mask=pii_mask(nano))
        meta = {EMAIL: {"note": TEXT, "next": _deep("dict", 1200, TEXT)}, NAME: _deep("list", 1200, EMAIL)}
        with lf.start_as_current_observation(name="lookup", input="hello", metadata=meta):
            pass
        lf.flush()
        lf.shutdown()
    finally:
        exporter.close()
    sent = b"".join(exporter.bodies)
    assert [p for p in PII if p.encode() in sent] == []
    assert FAILED.encode() not in sent and nano.redact(TEXT).text.encode() in sent
    assert b"langfuse.observation.metadata." + nano.redact(EMAIL).text.encode() in sent


@pytest.mark.parametrize("size", ["nano", "medium"])
def test_langfuse_real_client_exports_ids_and_field_names(size, request):
    """1.7.1 gate items L6-5 and L6-6: the real client exports a trace's ids and its field names as written (metadata
    keys stay filterable), and none of the personal values, keys included."""
    pytest.importorskip("langfuse")
    import random

    from langfuse import Langfuse

    from newmean_pii.langfuse import pii_mask

    r = request.getfixturevalue(size)
    data, ids = _trace_like(random.Random(7))
    metadata = {k: v for k, v in data.items() if k != "messages"}
    exporter = _Exporter()
    try:
        lf = Langfuse(public_key="pk-ids-%s-%s" % (size, uuid.uuid4().hex), secret_key="sk", host=exporter.host, mask=pii_mask(r))
        with lf.start_as_current_observation(name="agent", as_type="generation", input=data["messages"][:1],
                                             metadata=metadata) as gen:
            gen.update(output=data["messages"][1:])
        lf.flush()
        lf.shutdown()
    finally:
        exporter.close()
    sent = b"".join(exporter.bodies)
    assert [v for v in ids if v.encode() not in sent] == []
    assert [p for p in PII if p.encode() in sent] == [] and b"192.0.2.1" not in sent
    for key in ("session_id", "langgraph_checkpoint_ns", "user_id", "user_name", "user_email", "user_mail", "user_ip",
                "lc_run_id", "tool_call_id"):
        assert b"langfuse.observation.metadata." + key.encode() in sent, key


# doubling tests (REVIEW-GUIDE rule 4) at n, 2n and 4n: the failure path (what is read before the failure) and
# nesting depth (the walk keeps its own stack)


def _timings(make, sizes, runs, check):
    import gc
    import time

    from newmean_pii.langfuse import pii_mask

    out = []
    for n in sizes:
        data, detector = make(n)
        best = None
        for _ in range(runs):
            mask = pii_mask(detector(), keys=True)
            gc.disable()
            try:
                start = time.perf_counter()
                result = mask(data=data)
                took = time.perf_counter() - start
            finally:
                gc.enable()
            check(result, n)
            best = took if best is None else min(best, took)
        out.append(best)
    return out


def _is_failed(result, n):
    assert result == FAILED


FAILURE_AXES = {
    # n strings, the detector fails on the last one
    "values": ([4000, 8000, 16000], lambda n: ([f"note {i}" for i in range(n)], lambda: _Tags(fail_on=f"note {n - 1}"))),
    # n keys, the detector fails on the last key
    "keys": ([4000, 8000, 16000], lambda n: ({f"key {i}": i for i in range(n)}, lambda: _Tags(fail_on=f"key {n - 1}"))),
    # nested n deep, the detector fails on the string at the bottom
    "depth": ([4000, 8000, 16000], lambda n: (_deep("mixed", n, EMAIL), lambda: _Tags(fail_on=EMAIL))),
}

DEPTH_AXES = {kind: ([4000, 8000, 16000], lambda n, kind=kind: (_deep(kind, n, EMAIL), _Tags)) for kind in ("list", "dict", "mixed")}


def _doubling(name, sizes, make, check):
    times = _timings(make, sizes, 3, check)
    ratios = [b / a for a, b in zip(times, times[1:])]
    if max(ratios) > 3.0:  # timed again before it counts
        times = _timings(make, sizes, 7, check)
        ratios = [b / a for a, b in zip(times, times[1:])]
    shown = ", ".join("%s: %.2f ms" % (n, t * 1000) for n, t in zip(sizes, times))
    print("growth %s | %s | ratios %s" % (name, shown, " ".join("%.2f" % r for r in ratios)))
    assert max(ratios) <= 3.0, "%s: one doubling costs %.2f times as much (%s)" % (name, max(ratios), shown)


@pytest.mark.timing
@pytest.mark.parametrize("axis", sorted(FAILURE_AXES))
def test_langfuse_failure_doubling(axis):
    sizes, make = FAILURE_AXES[axis]
    _doubling("langfuse-failure " + axis, sizes, make, _is_failed)


@pytest.mark.timing
@pytest.mark.parametrize("kind", sorted(DEPTH_AXES))
def test_langfuse_depth_doubling(kind):
    sizes, make = DEPTH_AXES[kind]
    _doubling("langfuse-depth " + kind, sizes, make, lambda result, n: _check_deep(result, kind, n, "[TAG]"))


# 1.7.1 (L6-5, L6-6): the ids and field names kept, at n, 2n and 4n


def _letters(i, width):
    """i written in width small letters (a distinct name for each i, with no digit for the pattern layer to read)."""
    out = []
    for _ in range(width):
        i, d = divmod(i, 26)
        out.append(chr(97 + d))
    return "".join(reversed(out))


def _check_kept_values(key):
    def check(result, n):
        assert len(result[key]) == n and all("[TAG]" not in v for v in result[key])
    return check


KEPT_AXES = {
    # n UUIDs where the trace names ids
    "uuids": ([4000, 8000, 16000], lambda n: ({"run_ids": [str(uuid.UUID(int=i * 7919 + 1)) for i in range(n)]}, _Tags),
              _check_kept_values("run_ids")),
    # n ids with a prefix, as keys
    "id-keys": ([4000, 8000, 16000], lambda n: ({"call_" + _letters(i, 20): i for i in range(n)}, _Tags),
                lambda result, n: len(result) == n and all(k.startswith("call_") for k in result)),
    # n keys that are field names
    "field-keys": ([4000, 8000, 16000], lambda n: ({"field_" + _letters(i, 6): i for i in range(n)}, _Tags),
                   lambda result, n: len(result) == n and all(k.startswith("field_") for k in result)),
    # one string of length n that starts as an id does, and is read as text
    "id-like-length": ([64000, 128000, 256000], lambda n: ({"id": "lc_run--" + "a" * n, "x": "call_" + "a" * n}, _Tags),
                       lambda result, n: result == {"id": "[TAG]", "[TAG]": "[TAG]"}),
    # one key of length n that is written as a field name is, and is read as text
    "field-like-length": ([64000, 128000, 256000], lambda n: ({"a_" * n + "a": 1}, _Tags),
                          lambda result, n: result == {"[TAG]": 1}),
    # a UUID nested n deep in lists where the trace names ids
    "id-depth": ([4000, 8000, 16000], lambda n: ({"run_ids": _deep("list", n, UUID_TEXT)}, _Tags),
                 lambda result, n: _check_deep(result["run_ids"], "list", n, UUID_TEXT)),
}


@pytest.mark.timing
@pytest.mark.parametrize("axis", sorted(KEPT_AXES))
def test_langfuse_kept_ids_and_field_names_doubling(axis):
    sizes, make, check = KEPT_AXES[axis]

    def checked(result, n):
        assert check(result, n) is not False

    _doubling("langfuse-kept " + axis, sizes, make, checked)


# Gate 8 of 1.7.1 (items 6, 7, 8 and C): what the README says the trace keeps and what the mask keeps are the same set,
# position by position. Every string below stands at every position below, and is kept exactly where the README's rule
# keeps it (_readme_keeps); everywhere else it is read (the stand-in detector tags it, a model gives what redact gives):
#   an id with a prefix of its own: everywhere (a value, an item, a str key, a key of another type);
#   a UUID or a checkpoint namespace: the value of a str key that names an id, or an item there of a list, tuple, set,
#     deque or dict view at any depth of such sequences (an Enum's value, bytes and an object's dump stand where the
#     object stands); never a key (item 6), never a value inside a mapping there unless its own key names an id;
#   a field name or an id key of at most 64 characters (item 7): a str key only, subclasses too;
#   anything else: nowhere.
# keys=False keeps every key as it is; ids=False keeps no id.

OUTSIDE_REVIEW_UUID = "a4e84a19-6455-42fd-b038-4da2d02cef91"  # gate 8, item 6
OUTSIDE_REVIEW_KEY = "user" + "JohnSmith" * 8  # gate 8, item 7: camelCase, 76 characters
_Note = dataclasses.make_dataclass("_Note", ["a_note"])
_RunBox = dataclasses.make_dataclass("_RunBox", ["run_id"])


class _Dump:
    """A pydantic-like object whose model_dump returns what it was given."""

    def __init__(self, out):
        self.out = out

    def model_dump(self):
        return self.out


class _StrKey(str):
    pass


def _str_enum(s):
    return Enum("_E", {"A": s}, type=str).A


def _key(d):
    (k,) = d
    return k


def _value(d):
    (v,) = d.values()
    return v


def _text(v):
    return v.decode() if isinstance(v, (bytes, bytearray)) else v


def _field_box(s):
    """A dataclass with one field named s (or a_note when s is no identifier), its value "x"."""
    return dataclasses.make_dataclass("_D", [(s if s.isidentifier() else "a_note", str)])("x")


ID_KEY_64 = "run_id" + "_" * 58  # names an id: 64 characters
ID_KEY_65 = "run_id" + "_" * 59  # too long to name an id: read, and its value stands nowhere special
# (name, place, build, get): place is "id" (a value or item where the trace names an id), "value" (anywhere else),
# "key" (a str key) or "other-key" (a key of another type, read as a value is)
POSITIONS = [
    ("value of run_id", "id", lambda s: {"run_id": s}, lambda o: o["run_id"]),
    ("value of Run ID", "id", lambda s: {"Run ID": s}, lambda o: o["Run ID"]),
    ("value of runIds", "id", lambda s: {"runIds": s}, lambda o: o["runIds"]),
    ("value of a 64-character id key", "id", lambda s: {ID_KEY_64: s}, _value),
    ("value of a str-subclass id key", "id", lambda s: {_StrKey("session_id"): s}, _value),
    ("value of a str-Enum id key", "id", lambda s: {_str_enum("thread_id"): s}, _value),
    ("item of a list there", "id", lambda s: {"run_ids": [s]}, lambda o: o["run_ids"][0]),
    ("item of a tuple there", "id", lambda s: {"run_ids": (s,)}, lambda o: o["run_ids"][0]),
    ("item of a set there", "id", lambda s: {"run_ids": {s}}, lambda o: next(iter(o["run_ids"]))),
    ("item of a frozenset there", "id", lambda s: {"run_ids": frozenset([s])}, lambda o: next(iter(o["run_ids"]))),
    ("item of a deque there", "id", lambda s: {"run_ids": collections.deque([s])}, lambda o: o["run_ids"][0]),
    ("item of a dict view there", "id", lambda s: {"run_ids": {"a_note": s}.values()}, lambda o: o["run_ids"][0]),
    ("item of a list in a list there", "id", lambda s: {"run_ids": [[s]]}, lambda o: o["run_ids"][0][0]),
    ("item of a tuple in a list there", "id", lambda s: {"run_ids": [(1, s)]}, lambda o: o["run_ids"][0][1]),
    ("Enum value there", "id", lambda s: {"run_id": _str_enum(s)}, lambda o: o["run_id"]),
    ("bytes there", "id", lambda s: {"run_id": s.encode()}, lambda o: _text(o["run_id"])),
    ("UserString there", "id", lambda s: {"run_id": collections.UserString(s)}, lambda o: o["run_id"]),
    ("dump of an object there", "id", lambda s: {"run_id": _Dump(s)}, lambda o: o["run_id"]),
    ("dataclass field run_id", "id", lambda s: {"a_note": _RunBox(s)}, lambda o: o["a_note"]["run_id"]),
    ("value of id in a map under a_note", "id", lambda s: {"a_note": {"id": s}}, lambda o: o["a_note"]["id"]),
    ("top", "value", lambda s: s, lambda o: o),
    ("item of a top list", "value", lambda s: [s], lambda o: o[0]),
    ("value of a_note", "value", lambda s: {"a_note": s}, lambda o: o["a_note"]),
    ("value of user_id", "value", lambda s: {"user_id": s}, lambda o: o["user_id"]),
    ("value of a 65-character id key", "value", lambda s: {ID_KEY_65: s}, _value),
    ("item of a list under a_note", "value", lambda s: {"a_note": [s]}, lambda o: o["a_note"][0]),
    ("value in a map under run_id", "value", lambda s: {"run_id": {"a_note": s}}, lambda o: o["run_id"]["a_note"]),
    ("value in a map in a list there", "value", lambda s: {"run_ids": [{"a_note": s}]},
     lambda o: o["run_ids"][0]["a_note"]),
    ("dataclass field under run_id", "value", lambda s: {"run_id": _Note(s)}, lambda o: o["run_id"]["a_note"]),
    ("dump of a map under run_id", "value", lambda s: {"run_id": _Dump({"a_note": s})},
     lambda o: o["run_id"]["a_note"]),
    ("Enum value under a_note", "value", lambda s: {"a_note": _str_enum(s)}, lambda o: o["a_note"]),
    ("bytes under a_note", "value", lambda s: {"a_note": s.encode()}, lambda o: _text(o["a_note"])),
    ("top key", "key", lambda s: {s: 1}, _key),
    ("key in a map under run_id", "key", lambda s: {"run_id": {s: "ok"}}, lambda o: _key(o["run_id"])),
    ("key in a map in a list there", "key", lambda s: {"run_ids": [{s: "ok"}]}, lambda o: _key(o["run_ids"][0])),
    ("key in a map in lists there", "key", lambda s: {"run_ids": [[({s: 1},)]]},
     lambda o: _key(o["run_ids"][0][0][0])),
    ("key under id under run_id", "key", lambda s: {"run_id": {"id": {s: 1}}}, lambda o: _key(o["run_id"]["id"])),
    ("key of a dump there", "key", lambda s: {"run_id": _Dump({s: 1})}, lambda o: _key(o["run_id"])),
    ("key of a dump in a list there", "key", lambda s: {"run_ids": _Dump([{s: 1}])}, lambda o: _key(o["run_ids"][0])),
    ("key under a_note", "key", lambda s: {"a_note": {s: 1}}, lambda o: _key(o["a_note"])),
    ("str-subclass key", "key", lambda s: {_StrKey(s): 1}, _key),
    ("str-Enum key under run_id", "key", lambda s: {"run_id": {_str_enum(s): 1}}, lambda o: _key(o["run_id"])),
    ("bytes key under run_id", "other-key", lambda s: {"run_id": {s.encode(): 1}}, lambda o: _text(_key(o["run_id"]))),
    ("tuple key under run_id", "other-key", lambda s: {"run_id": {(s,): 1}}, lambda o: _key(o["run_id"])[0]),
    ("UserString key in a list there", "other-key", lambda s: {"run_ids": [{collections.UserString(s): 1}]},
     lambda o: _key(o["run_ids"][0])),
    ("bytes key at the top", "other-key", lambda s: {s.encode(): 1}, lambda o: _text(_key(o))),
]
# (kind, string): "prefixed" ids, "uuid" (UUIDs and checkpoint namespaces), "field" (field names and id keys of at most
# 64 characters), "text" (anything else)
POSITION_STRINGS = [
    ("uuid", OUTSIDE_REVIEW_UUID),
    ("uuid", UUID_TEXT.upper()),
    ("uuid", "model:2a2a8994-c2fd-bc92-1dcc-ba11a2ce6ddb"),
    ("prefixed", "call_u8jzPde0IgxLd6GncfBAepfJ"),
    ("prefixed", "lc_run--01a213f6-fbf0-72ee-92f7-78180fc0826b-0"),
    ("field", "user_name"),
    ("field", "userName"),
    ("field", "Run ID"),  # names an id; not a field name's shape
    ("field", "a" + "_b" * 31 + "c"),  # snake_case, 64 characters
    ("field", "user" + "Name" * 15),  # camelCase, 64 characters
    ("text", "a" + "_b" * 32),  # snake_case, 65 characters
    ("text", "user" + "Name" * 15 + "s"),  # camelCase, 65 characters
    ("text", OUTSIDE_REVIEW_KEY),
    ("text", "userID"),
    ("text", "role"),
    ("text", "hello there"),
]


def _readme_keeps(kind, place, ids=True, keys=True):
    """Whether the README keeps a string of this kind at this place."""
    if place in ("key", "other-key") and not keys:
        return True
    if kind == "prefixed":
        return ids
    if kind == "uuid":
        return ids and place == "id"
    if kind == "field":
        return place == "key"
    return False


def test_langfuse_position_strings_are_what_they_say():
    from newmean_pii.langfuse import _FIELD_NAME, _field_name, _id_shape, _is_id_key

    for kind, s in POSITION_STRINGS:
        assert _id_shape(s, True) is (kind in ("prefixed", "uuid")), s
        assert _id_shape(s, False) is (kind == "prefixed"), s
        assert _field_name(s) is (kind == "field"), s
    assert len(OUTSIDE_REVIEW_KEY) == 76 and _FIELD_NAME.fullmatch(OUTSIDE_REVIEW_KEY)
    assert not _field_name(OUTSIDE_REVIEW_KEY)
    assert len(ID_KEY_64) == 64 and _is_id_key(ID_KEY_64) and len(ID_KEY_65) == 65 and not _is_id_key(ID_KEY_65)
    assert len("a" + "_b" * 31 + "c") == 64 and len("user" + "Name" * 15) == 64


@pytest.mark.parametrize("ids", [True, False])
@pytest.mark.parametrize("keys", [True, False])
def test_langfuse_positions_match_the_readme(ids, keys):
    mask = _tagged(ids=ids, keys=keys)
    wrong = []
    for name, place, build, get in POSITIONS:
        for kind, s in POSITION_STRINGS:
            out = get(mask(data=build(s)))
            expected = s if _readme_keeps(kind, place, ids, keys) else "[TAG]"
            if out != expected:
                wrong.append((name, s, out, expected))
    assert wrong == []


def test_langfuse_dataclass_field_names_are_keys():
    """The fields of a dataclass are keys: a field name kept, a UUID never kept (also in a list under an id key)."""
    mask = _tagged()
    assert mask(data={"run_ids": [_field_box("user_name")]}) == {"run_ids": [{"user_name": "[TAG]"}]}
    assert mask(data={"run_ids": [_field_box("role")]}) == {"run_ids": [{"[TAG]": "[TAG]"}]}


@pytest.mark.parametrize("size", ["nano", "medium"])
def test_langfuse_positions_match_the_readme_with_the_models(size, request):
    from newmean_pii.langfuse import pii_mask

    r = request.getfixturevalue(size)
    mask = pii_mask(r)
    chosen = (OUTSIDE_REVIEW_UUID, "model:2a2a8994-c2fd-bc92-1dcc-ba11a2ce6ddb", "call_u8jzPde0IgxLd6GncfBAepfJ",
              "user_name", "Run ID", "user" + "Name" * 15, OUTSIDE_REVIEW_KEY)
    strings = [(k, s) for k, s in POSITION_STRINGS if s in chosen] + [("text", EMAIL)]
    wrong = []
    for name, place, build, get in POSITIONS:
        for kind, s in strings:
            out = get(mask(data=build(s)))
            expected = s if _readme_keeps(kind, place) else r.redact(s).text
            if out != expected:
                wrong.append((name, s, out, expected))
    assert wrong == []


@pytest.mark.parametrize("size", ["nano", "medium"])
def test_langfuse_outside_review_gate8_inputs_with_the_models(size, request):
    """The outside review's gate 8 inputs as given, on both models: item 6 (a UUID map key under an id key), item 7 (a
    camelCase key of 76 characters) and item 8 (a pydantic serializer that raises)."""
    pydantic = pytest.importorskip("pydantic")
    from newmean_pii.langfuse import pii_mask

    r = request.getfixturevalue(size)
    mask = pii_mask(r)
    assert r.redact(OUTSIDE_REVIEW_UUID).text != OUTSIDE_REVIEW_UUID  # both models mask this UUID when they read it
    out = mask(data={"run_id": {OUTSIDE_REVIEW_UUID: "ok"}})
    assert out == {"run_id": {r.redact(OUTSIDE_REVIEW_UUID).text: "ok"}} and OUTSIDE_REVIEW_UUID not in repr(out)
    assert mask(data={"run_id": OUTSIDE_REVIEW_UUID}) == {"run_id": OUTSIDE_REVIEW_UUID}  # the value there is kept
    assert mask(data={OUTSIDE_REVIEW_KEY: 1}) == {r.redact(OUTSIDE_REVIEW_KEY).text: 1}  # read: over 64 characters

    class Example(pydantic.BaseModel):
        note: str = NAME

        @pydantic.model_serializer
        def _serialize(self):
            raise ValueError("cannot serialize " + NAME)

    assert mask(data={"status_text": "OK", "payload": Example()}) == FAILED


def _failing_objects():
    """Objects whose fields cannot be read: each makes the whole value [MASK FAILED] (gate 8, item 8)."""
    pydantic = pytest.importorskip("pydantic")

    class Serializer(pydantic.BaseModel):
        note: str = NAME

        @pydantic.model_serializer
        def _serialize(self):
            raise ValueError("cannot serialize " + NAME)

    class FieldSerializer(pydantic.BaseModel):
        a_note: str = EMAIL

        @pydantic.field_serializer("a_note")
        def _a_note(self, v):
            raise TypeError(v)

    class Computed(pydantic.BaseModel):
        @pydantic.computed_field
        @property
        def a_note(self) -> str:
            raise RuntimeError(EMAIL)

    class Frozen(pydantic.BaseModel, frozen=True):  # hashable: a set item or a dict key
        note: str = NAME

        @pydantic.model_serializer
        def _serialize(self):
            raise ValueError(NAME)

    class DictOnly:  # pydantic v1's dict(), on an object without model_dump
        def dict(self):
            raise ValueError(EMAIL)

    return {"serializer": Serializer(), "field serializer": FieldSerializer(), "computed field": Computed(),
            "frozen": Frozen(), "dict()": DictOnly(), "model_dump": _BadDump(), "unset field": _Unset(),
            "property": _BadProperty()}, Frozen


def test_langfuse_objects_that_cannot_be_read_fail_the_whole_value():
    from newmean_pii.langfuse import pii_mask

    objects, frozen = _failing_objects()
    wrong = []
    for name, bad in objects.items():
        shapes = [bad, [bad], {"status_text": "OK", "payload": bad}, {"run_id": bad}, {"a_note": [{"x_list": (bad,)}]},
                  {EMAIL: "first", "later": bad}, _Dump({"payload": bad}), _Msg("user", bad)]
        for data in shapes:
            for keys in (True, False):
                for ids in (True, False):
                    out = pii_mask(_Tags(), keys=keys, ids=ids)(data=data)
                    if out != FAILED:
                        wrong.append((name, repr(data)[:60], keys, ids, out))
    # as a key and as a set item (a frozen model is hashable)
    for data in ({frozen(): 1}, {"run_id": {frozen(): 1}}, {"tags": {frozen()}}, {(EMAIL, frozen()): 1}):
        if pii_mask(_Tags())(data=data) != FAILED:
            wrong.append(("frozen", repr(data)[:60]))
    assert wrong == []


def test_langfuse_objects_that_can_be_read_are_not_failed():
    """The other side of item 8: objects whose fields are read are walked as before, and an object without a dump is
    [UNMASKED: <type>] (the README's "any other value")."""
    pydantic = pytest.importorskip("pydantic")

    class Fine(pydantic.BaseModel):
        a_note: str = EMAIL

        @pydantic.model_serializer
        def _serialize(self):
            return {"a_note": self.a_note, "run_id": UUID_TEXT}

    class NoDump:
        model_dump = None  # not callable: not a pydantic-like object

    mask = _tagged()
    assert mask(data={"status_text": "OK", "the_payload": Fine()}) == {
        "status_text": "[TAG]", "the_payload": {"a_note": "[TAG]", "run_id": UUID_TEXT}}
    assert mask(data={"the_payload": NoDump(), "a_note": EMAIL}) == {"the_payload": "[UNMASKED: NoDump]", "a_note": "[TAG]"}
    assert mask(data={"the_payload": _Opaque()}) == {"the_payload": "[UNMASKED: _Opaque]"}


# Gate 8 of 1.7.1, the confirmation's item 8 (outside review): a model_dump property that returned self.serializer,
# with the attribute not set, raised AttributeError, which getattr's default took for "no serializer", and
# {"status_text": "OK", "payload": Deferred()} was exported as {"status_text": "OK", "payload": "[UNMASKED: Deferred]"}.
# The class: a read that fails, of anything the mask reads, is a failure and never an absence. Each object below fails
# one such read, most with AttributeError (what a getattr default, hasattr and isinstance's read of __class__ take for
# "not there"), and makes the whole value [MASK FAILED] at every position.


class _Deferred:
    """The outside review's input: model_dump is a property that returns self.serializer, set or not."""

    def __init__(self, serializer=None):
        if serializer is not None:
            self.serializer = serializer

    @property
    def model_dump(self):
        return self.serializer


class _SaysUUID:
    """An object whose __class__ says uuid.UUID: isinstance believed it, the mask kept it as it was, and Langfuse writes a
    UUID's str(), here a name."""

    __class__ = property(lambda self: uuid.UUID)

    def __str__(self):
        return NAME


class _Forwards:
    """A proxy: every attribute it does not have is read from its target through __getattr__ (model_dump too)."""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_target"), name)


def _reads_that_fail():
    """name -> a function that makes the object: each read below fails, of what the mask reads."""
    pydantic = pytest.importorskip("pydantic")

    class DumpPropertyRaises:
        @property
        def model_dump(self):
            raise AttributeError("model_dump of " + NAME)

    class DictProperty:  # pydantic v1's dict(), as a property that returns what is not set
        @property
        def dict(self):
            return self.serializer

    class DumpReadsUnset:
        def model_dump(self):
            return {"a_note": self.not_set}

    class DeferredGetattr(_Deferred):  # __getattr__ answers the attribute that is not set with AttributeError too
        def __getattr__(self, name):
            raise AttributeError(name)

    class GetattributeRaises:  # model_dump is declared; reading it raises
        def model_dump(self):
            return {"a_note": EMAIL}

        def __getattribute__(self, name):
            if name == "model_dump":
                raise AttributeError(name)
            return object.__getattribute__(self, name)

    class SerializerReadsUnset(pydantic.BaseModel):
        a_note: str = EMAIL

        @pydantic.model_serializer
        def _write(self):
            return {"a_note": self.not_a_field}

    class ComputedRaises(pydantic.BaseModel):
        @pydantic.computed_field
        @property
        def a_note(self) -> str:
            raise AttributeError(EMAIL)

    class PrivateUnset(pydantic.BaseModel):
        _cache: str = pydantic.PrivateAttr()

        @pydantic.model_serializer
        def _write(self):
            return {"a_note": self._cache}

    @dataclasses.dataclass
    class Base:
        a_note: str = EMAIL

    class FieldProperty(Base):  # a dataclass field read through a property that reads what is not set
        @property
        def a_note(self):
            return self.not_set

    @dataclasses.dataclass(slots=True)
    class SlotUnset:
        a_note: str = dataclasses.field(init=False)

    class MapItem(collections.abc.Mapping):
        def __iter__(self):
            return iter(["a_note"])

        def __len__(self):
            return 1

        def __getitem__(self, key):
            raise AttributeError(key)

    class MapKeys(collections.abc.Mapping):
        def __iter__(self):
            raise AttributeError("keys of " + NAME)

        def __len__(self):
            return 1

        def __getitem__(self, key):
            return EMAIL

    class ListItems(list):
        def __iter__(self):
            raise AttributeError("items of " + NAME)

    class DequeItems(collections.deque):
        def __iter__(self):
            raise AttributeError(EMAIL)

    class SetItems(set):
        def __iter__(self):
            raise AttributeError(EMAIL)

    class TupleItems(tuple):
        def __iter__(self):
            raise AttributeError(EMAIL)

    class Unset(uuid.UUID):  # a uuid.UUID subclass whose number is not set: what it holds cannot be read
        def __init__(self):
            pass

    class DataProperty(collections.UserString):
        @property
        def data(self):
            return self.not_set

    class BytesUnread(bytes):  # (a decoding error here was taken for bytes that are not UTF-8)
        def __bytes__(self):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, EMAIL)

    def enum_without_value():
        member = Enum("_Unread", {"A": EMAIL}).A
        object.__delattr__(member, "_value_")
        return member

    def released():
        view = memoryview(EMAIL.encode())
        view.release()
        return view

    return {
        "model_dump property (outside review)": _Deferred,
        "model_dump property with __getattr__": DeferredGetattr,
        "model_dump property raises": DumpPropertyRaises,
        "dict property (pydantic v1)": DictProperty,
        "model_dump reads what is not set": DumpReadsUnset,
        "__getattribute__ raises": GetattributeRaises,
        "pydantic serializer reads what is not set": SerializerReadsUnset,
        "pydantic computed field": ComputedRaises,
        "pydantic private attribute not set": PrivateUnset,
        "dataclass field property": lambda: object.__new__(FieldProperty),
        "dataclass slot not set": SlotUnset,
        "mapping item": MapItem,
        "mapping keys": MapKeys,
        "list items": lambda: ListItems([EMAIL]),
        "deque items": lambda: DequeItems([EMAIL]),
        "set items": lambda: SetItems([EMAIL]),
        "tuple items": lambda: TupleItems((EMAIL,)),
        "dict view": lambda: collections.abc.ValuesView(MapItem()),
        "kept subclass not set": Unset,
        "UserString data": lambda: object.__new__(DataProperty),
        "Enum member value": enum_without_value,
        "bytes": lambda: BytesUnread(EMAIL.encode()),
        "released memoryview": released,
    }


def _positions(make):
    """Every place an object stands in an observation, a fresh object each, as (values, keys): the values: alone; a list
    item; next to a sibling (the outside review's shape) and after a key already read; under an id key, and in a list
    there; in lists and tuples; in an object's dump, a dataclass field, a named tuple, a deque, a dict view, an Enum
    member's value; 50 levels down; and, when it can be hashed, a set item and a frozenset item. The keys (when it can
    be hashed; read only with keys=True, which keys=False keeps as they are): a dict key, under an id key too, and in a
    tuple key."""
    values = [make(), [make()], {"status_text": "OK", "payload": make()}, {EMAIL: "first", "later": make()},
              {"run_id": make()}, {"run_ids": [make()]}, {"a_note": [{"x_list": (make(),)}]}, _Dump({"payload": make()}),
              _Msg("user", make()), _Pair(make()), collections.deque([make()]), {"a_note": make()}.values(),
              _deep("mixed", 50, make())]
    try:
        values.append(Enum("_Holds", {"A": make()}).A)
    except Exception:  # noqa: BLE001 - (Enum hashes its values: a released memoryview raises there)
        pass
    try:
        hash(make())
    except Exception:  # noqa: BLE001 - not hashable: no key or set position
        return values, []
    return values + [{"tags": {make()}}, frozenset([make()])], [{make(): 1}, {"run_id": {make(): 1}}, {(EMAIL, make()): 1}]


def test_langfuse_reads_that_fail_fail_the_whole_value_at_every_position():
    from newmean_pii.langfuse import pii_mask

    wrong, runs = [], 0
    for name, make in _reads_that_fail().items():
        values, keyed = _positions(make)
        for keys in (True, False):
            for ids in (True, False):
                for data in values + (keyed if keys else []):
                    runs += 1
                    out = pii_mask(_Tags(), keys=keys, ids=ids)(data=data)
                    if type(out) is not str or out != FAILED:  # (no repr: a kept object can fail it)
                        wrong.append((name, type(data).__name__, keys, ids))
    assert wrong == [] and runs >= 23 * 14 * 4


def test_langfuse_outside_review_deferred_as_given():
    """The outside review's input: with the attribute set, the dump is read; with it not set, the whole value fails
    (before: {"status_text": "OK", "payload": "[UNMASKED: Deferred]"})."""
    mask = _tagged()
    # ("payload" and "count", one word each, are read as keys are; "status_text", a field name, is kept)
    assert mask(data={"status_text": "OK", "payload": _Deferred(lambda: {"count": 1})}) == {"status_text": "[TAG]", "[TAG]": {"[TAG]": 1}}
    assert mask(data={"status_text": "OK", "payload": _Deferred()}) == FAILED


def test_langfuse_a_value_is_what_its_class_is_and_declares():
    """The other side of the class: what the mask does not read is not there because its class does not declare it,
    never because a read failed. A proxy that only forwards model_dump through __getattr__, a Mock and an object with a
    model_dump of its own (not its class's) are "any other value"; an object whose __class__ names another class is
    what its own class is (one that says uuid.UUID was kept, and Langfuse wrote its str(), a name); one whose __class__
    cannot be read is still read by its own class."""
    import threading
    from unittest import mock

    pydantic = pytest.importorskip("pydantic")

    class Note(pydantic.BaseModel):
        a_note: str = EMAIL

    class ClassUnreadable:
        __class__ = property(lambda self: self.not_set)

        def model_dump(self):
            return {"a_note": EMAIL}

    own = types.SimpleNamespace(model_dump=lambda: {"a_note": EMAIL})
    mask = _tagged()
    assert mask(data={"the_payload": _SaysUUID(), "a_note": "x"}) == {"the_payload": "[UNMASKED: _SaysUUID]", "a_note": "[TAG]"}
    assert mask(data={"the_payload": _Forwards(Note())}) == {"the_payload": "[UNMASKED: _Forwards]"}
    assert mask(data={"the_payload": own}) == {"the_payload": "[UNMASKED: SimpleNamespace]"}
    assert mask(data={"the_payload": ClassUnreadable()}) == {"the_payload": {"a_note": "[TAG]"}}
    assert mask(data={"the_payload": Note()}) == {"the_payload": {"a_note": "[TAG]"}}
    # a Mock answers model_dump with a new Mock at every level: before, the walk did not end
    done = []
    worker = threading.Thread(target=lambda: done.append(mask(data={"the_payload": mock.Mock(), "the_spec": mock.Mock(spec=dict)})),
                              daemon=True)
    worker.start()
    worker.join(10)
    assert done == [{"the_payload": "[UNMASKED: Mock]", "the_spec": "[UNMASKED: Mock]"}]


# Gate 8 of 1.7.1, the confirmation's item 8, the other side of "a value is what its own class is": what the mask
# returns is made of Python's own types only. Before, it returned objects of the value's class (a kept number, date or
# UUID of a subclass; a str or bytes subclass; a named tuple; a class named LangfuseMedia), and Langfuse wrote them
# through that class's code: str() of a UUID, isoformat() of a date, str() of a big int and of a class whose name holds
# "Streaming", decode() of bytes, iteration of a tuple, and the attributes an instance holds (a time, a duration, a
# Decimal, a complex): {"the_value": "Kim Minsu"} was exported.


def _returned_objects(M=lambda s: "[TAG]"):
    """name -> (a function that makes the value, what the mask returns for it with the detector M, the type it returns)."""

    class Id(uuid.UUID):
        def __str__(self):
            return NAME

    class Day(datetime.date):
        def isoformat(self):
            return NAME

    class Moment(datetime.datetime):
        def isoformat(self, *args, **kwargs):
            return NAME

        def __reduce__(self):
            raise AttributeError(NAME)

    class Big(int):
        def __str__(self):
            return NAME

        def __index__(self):
            raise AttributeError(NAME)

    class Real(float):
        def __float__(self):
            raise AttributeError(NAME)

    def holding(base, *args):
        """A value of a subclass of base that holds NAME as an attribute (Langfuse writes the attributes of a type it
        has no case for)."""
        value = type("Holding" + base.__name__.title(), (base,), {})(*args)
        value.note = NAME
        return value

    class StreamingText(str):  # Langfuse writes str() of an object whose class name holds "Streaming"
        def __str__(self):
            return NAME

    class Raw(bytes):
        def decode(self, *args, **kwargs):
            return NAME

    class RawArray(bytearray):
        def decode(self, *args, **kwargs):
            return NAME

    class Row(collections.namedtuple("Row", "a_note")):
        def __iter__(self):  # Langfuse iterates a tuple; the mask reads what this gives and returns a tuple of it
            yield "the " + NAME

        @classmethod
        def _make(cls, iterable):
            raise AttributeError(NAME)

    class LangfuseMedia:  # takes the name and module of Langfuse's class, which is not loaded or not this one
        __module__ = "langfuse.media"

        def __init__(self):
            self.note = NAME

    class Named(type):  # a metaclass whose __name__ is text
        __name__ = property(lambda cls: NAME)

    Opaque = Named("Opaque", (), {})
    tz = datetime.timezone(datetime.timedelta(hours=9))
    return {
        "uuid subclass": (lambda: Id(int=5), uuid.UUID(int=5), uuid.UUID),
        "date subclass": (lambda: Day(2026, 1, 2), datetime.date(2026, 1, 2), datetime.date),
        "datetime subclass": (lambda: Moment(2026, 1, 2, 3, tzinfo=tz), datetime.datetime(2026, 1, 2, 3, tzinfo=tz),
                              datetime.datetime),
        "int subclass": (lambda: Big(2 ** 60), 2 ** 60, int),
        "float subclass": (lambda: Real(1.5), 1.5, float),
        "time with attributes": (lambda: holding(datetime.time, 1, 2), datetime.time(1, 2), datetime.time),
        "timedelta with attributes": (lambda: holding(datetime.timedelta, 1), datetime.timedelta(1), datetime.timedelta),
        "Decimal with attributes": (lambda: holding(Decimal, "1.5"), Decimal("1.5"), Decimal),
        "complex with attributes": (lambda: holding(complex, 1, 2), 1 + 2j, complex),
        "str subclass, an id": (lambda: StreamingText(PREFIXED_IDS[0]), PREFIXED_IDS[0], str),
        "str subclass, empty": (lambda: StreamingText(""), "", str),
        "bytes subclass": (lambda: Raw(EMAIL.encode()), M(EMAIL).encode(), bytes),
        "bytearray subclass": (lambda: RawArray(EMAIL.encode()), bytearray(M(EMAIL).encode()), bytearray),
        "named tuple": (lambda: Row(EMAIL), (M("the " + NAME),), tuple),
        "media by name": (LangfuseMedia, "[UNMASKED: LangfuseMedia]", str),
        "metaclass name": (Opaque, "[UNMASKED: Opaque]", str),
    }


def _event_serializer():
    """Langfuse's serializer (what writes an observation's input into the export), or None without langfuse."""
    try:
        from langfuse._utils.serializer import EventSerializer
    except ImportError:
        return None
    return EventSerializer


def test_langfuse_returns_only_pythons_own_types():
    serializer = _event_serializer()
    mask = _tagged()
    wrong, runs = [], 0
    for name, (make, expected, kind) in _returned_objects().items():
        for data, get in (
            (make(), lambda o: o), ([make()], lambda o: o[0]), ({"the_value": make()}, lambda o: o["the_value"]),
            ((make(),), lambda o: o[0]), (_Dump({"payload": make()}), lambda o: o["[TAG]"]),
            ({"run_ids": [[make()]]}, lambda o: o["run_ids"][0][0]), (collections.deque([make()]), lambda o: o[0]),
        ):
            runs += 1
            out = mask(data=data)
            got = get(out)
            exported = serializer().encode(out) if serializer else ""
            if type(got) is not kind or got != expected or NAME in exported:
                wrong.append((name, type(data).__name__, type(got).__name__, exported[:80]))
    assert wrong == [] and runs == 16 * 7


def test_langfuse_keys_and_set_items_returned_as_pythons_own_types():
    """A kept subclass as a key or a set item is the kept type too, and a key or set item that the walk made a dict of
    (a frozen dataclass), or a key that masks to a key already there, is written as text by the mask itself, never by
    str() of what it holds (a datetime's str() runs its tzinfo's __repr__)."""

    class Zone(datetime.tzinfo):
        def utcoffset(self, dt):
            return datetime.timedelta(hours=9)

        def __repr__(self):
            return NAME

    @dataclasses.dataclass(frozen=True)
    class Stamp:
        at: datetime.datetime

    made = _returned_objects()
    mask = _tagged()
    for name in ("uuid subclass", "date subclass", "int subclass", "str subclass, an id", "time with attributes",
                 "metaclass name"):
        make, expected, kind = made[name]
        out = mask(data={"the_tags": {make()}, "by_value": {make(): 1}})
        (item,), ((k, v),) = out["the_tags"], out["by_value"].items()
        assert type(item) is kind and item == expected and type(k) is kind and k == expected and v == 1, name
    at = datetime.datetime(2026, 1, 2, 3, tzinfo=Zone())
    written = "{'[TAG]': '2026-01-02T03:00:00+09:00'}"
    out = mask(data={"the_tags": {Stamp(at)}, "by_stamp": {Stamp(at): 1}, "the_pairs": {(Stamp(at), EMAIL): 1}})
    assert out == {"the_tags": {written}, "by_stamp": {written: 1}, "the_pairs": {f"({written}, '[TAG]')": 1}}
    # two keys that mask to the same text keep both values; the second is written by the mask (before: str() of the
    # first, which held the tzinfo's __repr__)
    out = mask(data={(EMAIL, at): 1, (NAME, at): 2})
    assert list(out.values()) == [1, 2] and list(out)[0] == ("[TAG]", at)
    assert list(out)[1] == "('[TAG]', '2026-01-02T03:00:00+09:00') #2"


@pytest.mark.parametrize("size", ["nano", "medium"])
def test_langfuse_reads_that_fail_with_the_models(size, request):
    """The outside review's input as given, and every read that fails in its shape and two more places, on both
    models; and what the mask returns, of Python's own types only."""
    from newmean_pii.langfuse import pii_mask

    r = request.getfixturevalue(size)
    mask = pii_mask(r)
    assert mask(data={"status_text": "OK", "payload": _Deferred()}) == FAILED

    def M(s):
        return r.redact(s).text

    assert mask(data={"status_text": "OK", "payload": _Deferred(lambda: {"count": 1})}) == {"status_text": M("OK"), M("payload"): {M("count"): 1}}
    wrong = []
    for name, make in _reads_that_fail().items():
        for data in ({"status_text": "OK", "payload": make()}, {EMAIL: [make()]}, {"run_id": _Dump({"payload": make()})}):
            out = mask(data=data)
            if type(out) is not str or out != FAILED:
                wrong.append((name, type(data).__name__))
    assert wrong == []
    assert mask(data={"the_payload": _SaysUUID()}) == {"the_payload": "[UNMASKED: _SaysUUID]"}
    for name, (make, expected, kind) in _returned_objects(M).items():
        got = mask(data={"the_payload": make()})["the_payload"]
        if type(got) is not kind or got != expected:
            wrong.append((name, type(got).__name__))
    assert wrong == []


def test_langfuse_real_client_exports_the_readme_positions(nano, request):
    """Gate 8 of 1.7.1, items 6 to 8, through the real client: each observation's input decoded from the OTLP export and
    compared whole with what the README says, on nano and (when present) medium."""
    pytest.importorskip("langfuse")
    otlp = pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
    import json

    from langfuse import Langfuse

    from newmean_pii.langfuse import pii_mask

    objects, _ = _failing_objects()
    serializer = _event_serializer()

    def written(value):
        """value as Langfuse's serializer writes it into the export (a str stands for itself)."""
        return value if isinstance(value, str) else json.loads(serializer().encode(value))

    sizes = [("nano", nano)]
    try:
        sizes.append(("medium", request.getfixturevalue("medium")))
    except pytest.skip.Exception:
        pass
    long_64, long_65 = "user" + "Name" * 15, "user" + "Name" * 15 + "s"
    for size, r in sizes:
        def M(s, r=r):
            return r.redact(s).text

        cases = {
            "uuid-key-under-id": ({"run_id": {OUTSIDE_REVIEW_UUID: "ok"}},
                                  {"run_id": {M(OUTSIDE_REVIEW_UUID): "ok"}}),
            "uuid-key-in-a-list-there": ({"run_ids": [{OUTSIDE_REVIEW_UUID: "ok"}]},
                                         {"run_ids": [{M(OUTSIDE_REVIEW_UUID): "ok"}]}),
            "uuid-key-of-a-dump-there": ({"run_id": _Dump({OUTSIDE_REVIEW_UUID: 1})},
                                         {"run_id": {M(OUTSIDE_REVIEW_UUID): 1}}),
            "uuid-value-there": ({"run_id": OUTSIDE_REVIEW_UUID}, {"run_id": OUTSIDE_REVIEW_UUID}),
            "uuid-items-there": ({"run_ids": [OUTSIDE_REVIEW_UUID, (OUTSIDE_REVIEW_UUID,)]},
                                 {"run_ids": [OUTSIDE_REVIEW_UUID, [OUTSIDE_REVIEW_UUID]]}),
            "uuid-in-a-map-there": ({"run_id": {"a_note": OUTSIDE_REVIEW_UUID}},
                                    {"run_id": {"a_note": M(OUTSIDE_REVIEW_UUID)}}),
            "uuid-elsewhere": ({"a_note": OUTSIDE_REVIEW_UUID}, {"a_note": M(OUTSIDE_REVIEW_UUID)}),
            "long-key": ({OUTSIDE_REVIEW_KEY: 1}, {M(OUTSIDE_REVIEW_KEY): 1}),
            "key-of-64": ({long_64: 1}, {long_64: 1}),
            "key-of-65": ({long_65: 1}, {M(long_65): 1}),
            **{"fails-" + name.replace(" ", "-"): ({"status_text": "OK", "payload": bad}, FAILED)
               for name, bad in objects.items()},
            # the confirmation's item 8: the outside review's input as given, each read that fails, and what is read or
            # not there.
            # (Not "list items": Langfuse's own media pass makes every list a new list before the mask runs, and the
            # error of that iteration reaches the caller from Langfuse, with nothing exported)
            **{"fails-read-" + name.replace(" ", "-"): ({"status_text": "OK", "payload": make()}, FAILED)
               for name, make in _reads_that_fail().items() if name != "list items"},
            "deferred-set": ({"status_text": "OK", "payload": _Deferred(lambda: {"count": 1})},
                             {"status_text": M("OK"), M("payload"): {M("count"): 1}}),
            "says-uuid": ({"the_payload": _SaysUUID()}, {"the_payload": "[UNMASKED: _SaysUUID]"}),
            "forwards-a-dump": ({"the_payload": _Forwards(_Dump({"a_note": EMAIL}))}, {"the_payload": "[UNMASKED: _Forwards]"}),
            # the confirmation's item 8: what the mask returns is of Python's own types (as the export writes them)
            **{"returns-" + name.replace(" ", "-").replace(",", ""): ({"the_value": make()}, {"the_value": written(expected)})
               for name, (make, expected, _) in _returned_objects(M).items()},
        }
        exporter = _Exporter()
        try:
            lf = Langfuse(public_key="pk-pos-%s-%s" % (size, uuid.uuid4().hex), secret_key="sk", host=exporter.host,
                          mask=pii_mask(r))
            for name, (data, _) in cases.items():
                with lf.start_as_current_observation(name=name, input=data):
                    pass
            lf.flush()
            lf.shutdown()
        finally:
            exporter.close()
        exported = {}
        for body in exporter.bodies:
            for rs in otlp.ExportTraceServiceRequest.FromString(body).resource_spans:
                for ss in rs.scope_spans:
                    for span in ss.spans:
                        attrs = {a.key: a.value.string_value for a in span.attributes}
                        exported[span.name] = attrs.get("langfuse.observation.input")
        wrong = []
        for name, (_, expected) in cases.items():
            got = exported.get(name)
            got = got if isinstance(expected, str) or got is None else json.loads(got)
            if got != expected:
                wrong.append((size, name, got, expected))
        assert wrong == []
        sent = b"".join(exporter.bodies)
        assert [p for p in PII if p.encode() in sent] == [] and OUTSIDE_REVIEW_KEY.encode() not in sent


# Gate 8 of 1.7.1, item C: the README says exactly what the key cache remembers


def test_langfuse_key_cache_holds_the_most_recent_4096_keys(nano, monkeypatch):
    from newmean_pii import langfuse
    from newmean_pii.langfuse import pii_mask

    assert langfuse._KEYS_KEPT == 4096
    read = []
    monkeypatch.setattr(nano, "_detect_at", lambda text, settings: (read.append(text), [])[1])  # reads, cheaply
    mask = pii_mask(nano)
    names = ["key%d" % i for i in range(4097)]  # one word and digits: read, not field names
    mask(data={names[0]: 1})
    mask(data={k: 1 for k in names[1:]})  # 4,096 more keys: the first is no longer among the most recent
    assert read.count(names[0]) == 1 and read.count(names[4096]) == 1
    mask(data={names[4096]: 1, names[1]: 1})  # still among the most recent 4,096: not read again
    assert read.count(names[4096]) == 1 and read.count(names[1]) == 1
    mask(data={names[0]: 1})  # read again
    assert read.count(names[0]) == 2


def test_langfuse_keys_are_read_every_time_with_another_redactor(nano):
    from conftest import model_dir

    from newmean_pii import Redactor
    from newmean_pii.langfuse import pii_mask

    stand_in = _Tags()
    mask = pii_mask(stand_in)
    for _ in range(3):
        mask(data={"role": 1})
    assert stand_in.calls == 3

    class Counting(Redactor):  # replaces redact: its settings are not known, so nothing is remembered
        calls = 0

        def redact(self, text, mode="tag"):
            Counting.calls += 1
            return super().redact(text, mode=mode)

    mask = pii_mask(Counting("nano", "q4", model_dir=model_dir("nano")))
    for _ in range(3):
        mask(data={"role": 1})
    assert Counting.calls == 3


# doubling tests for the paths gate 8 changed: keys under an id key (read, colliding), keys of str subclasses (kept) and
# a failing dump after many values


def _check_uuid_keys(result, n):
    return len(result["run_ids"]) == n and "[TAG] #%d" % n in result["run_ids"]


def _check_subclass_keys(result, n):
    return len(result) == n and all(type(k) is str and k.startswith("field_") for k in result)


GATE8_AXES = {
    "uuid-keys-under-an-id": ([4000, 8000, 16000],
                              lambda n: ({"run_ids": {str(uuid.UUID(int=i * 7919 + 1)): i for i in range(n)}}, _Tags),
                              _check_uuid_keys),
    "str-subclass-keys": ([4000, 8000, 16000],
                          lambda n: ({_StrKey("field_" + _letters(i, 6)): i for i in range(n)}, _Tags),
                          _check_subclass_keys),
    "values-then-a-failing-dump": ([4000, 8000, 16000],
                                   lambda n: ([f"note {i}" for i in range(n)] + [_BadDump()], _Tags),
                                   lambda result, n: result == FAILED),
    # the confirmation's item 8: values, then a dump property whose read fails; and objects whose class declares a dump
    "values-then-an-unreadable-dump-property": ([4000, 8000, 16000],
                                                lambda n: ([f"note {i}" for i in range(n)] + [_Deferred()], _Tags),
                                                lambda result, n: result == FAILED),
    "declared-dumps": ([4000, 8000, 16000],
                       lambda n: ([_Dump({"a_note": i}) for i in range(n)], _Tags),
                       lambda result, n: len(result) == n and result[-1] == {"a_note": n - 1}),
    # the confirmation's item 8, what the mask returns: subclasses of kept types made the types themselves, and keys
    # that the walk made dicts of (frozen dataclasses) written as text by the mask
    "kept-subclasses": ([4000, 8000, 16000],
                        lambda n: ([_KeptId(int=i) for i in range(n)], _Tags),
                        lambda result, n: len(result) == n and type(result[-1]) is uuid.UUID and result[-1].int == n - 1),
    "frozen-keys": ([4000, 8000, 16000],
                    lambda n: ({_Frozen("v%d" % i): i for i in range(n)}, _Tags),
                    lambda result, n: len(result) == n and list(result)[0] == "{'[TAG]': '[TAG]'}"),
}


class _KeptId(uuid.UUID):
    def __str__(self):
        return NAME


@pytest.mark.timing
@pytest.mark.parametrize("axis", sorted(GATE8_AXES))
def test_langfuse_gate8_doubling(axis):
    sizes, make, check = GATE8_AXES[axis]

    def checked(result, n):
        assert check(result, n) is not False

    _doubling("langfuse-gate8 " + axis, sizes, make, checked)
