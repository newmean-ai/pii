"""The fuzz cases of gate 3 of the 1.7.1 release standard (vault_fuzz.json, written by gen_vault_fuzz.py)
through the Python LangChain vault middleware, with a detector that finds nothing (the vault alone).

Every case runs at every position of the file, in the shapes LangChain gives them to the middleware (as
test_vault_corpus.py builds them), with a fresh vault that holds the case's known values. What the handler receives at
the position must be the case's "exact" text: each value's written span replaced by its placeholder and every neighbor
unchanged (a JSON document is compared by what it holds: section 5 of the guarantee note lets a changed string be
written again as JSON writes it). At a data position that is the data with the text replaced by "exact". Besides, no
"leak" string stands anywhere in what the handler receives, as written, JSON-escaped or as repr writes it. A problem
says which check failed: "leak", "placeholder" (an expected placeholder is missing at the position), "exact" (the span
was not replaced completely or a neighbor changed) or "error".

Decision D1 of the release standard, reported apart:

- test_fuzz_two_layer_product: the cases of the group "two-layer" (the shape of the gate 2 product: human steps before
  at most two layers). Release-blocking: zero failures.
- test_fuzz_beyond_product_dispositioned: the cases of the group "beyond" (three and four steps, human steps after a
  layer). Best effort: every failure must be listed in vault_fuzz_open.json ("<case id>@python": disposition), and an
  entry whose case passes must be taken off the list.

With VAULT_FUZZ_REPORT=<directory>, every failure of both groups is written to <directory>/vault-fuzz-python.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("langchain.agents.middleware")

from langchain.agents.middleware.types import ModelRequest, ModelResponse  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from newmean_pii import Vault  # noqa: E402
from newmean_pii.langchain import pii_middleware  # noqa: E402

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
FUZZ = json.loads((HERE / "vault_fuzz.json").read_text(encoding="utf-8"))
OPEN = json.loads((HERE / "vault_fuzz_open.json").read_text(encoding="utf-8"))
RUNNER = "python"
CALL_ID = "call_1"
INPUT_ID = "call_9"
NL = chr(10)


class Nothing:
    """A detector that finds nothing: only the vault's own search runs."""

    sensitivity = 0.0

    def detect(self, text):
        return []


def data_of(text):
    return {"note": text, "id": 7, text: "key"}


def replaced(v, old, new):
    """v with every string and key equal to old replaced by new."""
    if isinstance(v, str):
        return new if v == old else v
    if isinstance(v, list):
        return [replaced(x, old, new) for x in v]
    if isinstance(v, dict):
        return {replaced(k, old, new): replaced(x, old, new) for k, x in v.items()}
    return v


def canon(x):
    """A value for comparing outputs: a JSON document (an object or an array, as text) by what it holds, at every
    depth; any other text exactly."""
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
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return float(x)
    return x


def written_forms(s):
    """s as it is written, inside a JSON string (with and without ASCII escapes) and inside a Python repr."""
    forms = {s, json.dumps(s, ensure_ascii=False)[1:-1], json.dumps(s)[1:-1], repr(s)[1:-1]}
    both = "'" + '"'
    forms.add(repr(s + both)[1:-(len(repr(both)) - 1)])
    return [f for f in forms if f]


def placeholders(case):
    count, out = {}, []
    for e in case["known"]:
        family = e["label"][:-3] if e["label"].endswith("_L2") else e["label"]
        count[family] = count.get(family, 0) + 1
        out.append("[" + family + "_" + str(count[family]) + "]")
    return out


def shapes(case, position):
    """[(shape name, messages, system message, where the position is, what must stand there)]."""
    text, exact = case["text"], case["exact"]
    data = data_of(text)
    want = replaced(data, text, exact)
    hello = HumanMessage("Hello")
    asked = AIMessage("", tool_calls=[{"id": CALL_ID, "name": "lookup", "args": {}}])
    msg = lambda r, i: r.messages[i]
    if position == "user":
        return [("user", [HumanMessage(text)], None, lambda r: msg(r, 0).content, exact)]
    if position == "system":
        return [("system", [hello], SystemMessage(text), lambda r: r.system_message.content, exact)]
    if position == "tool-text":
        return [("tool-text", [hello, asked, ToolMessage(text, tool_call_id=CALL_ID)], None, lambda r: msg(r, 2).content, exact)]
    if position == "tool-data":
        return [
            ("rows", [hello, asked, ToolMessage(content=[data], tool_call_id=CALL_ID)], None, lambda r: msg(r, 2).content[0], want),
            ("tool-node", [hello, asked, ToolMessage(content=tool_node_content(data), tool_call_id=CALL_ID)], None,
             lambda r: msg(r, 2).content, want),
        ]
    if position == "block-data":
        block = ToolMessage(content=[{"type": "json", "json": data}], tool_call_id=CALL_ID)
        return [("block", [hello, asked, block], None, lambda r: msg(r, 2).content[0]["json"], want)]
    if position == "assistant-unrecorded":
        return [("assistant", [hello, AIMessage(text), HumanMessage("Thanks")], None, lambda r: msg(r, 1).content, exact)]
    if position == "tool-input-unrecorded":
        done = ToolMessage("done", tool_call_id=INPUT_ID)
        call = AIMessage("", tool_calls=[{"id": INPUT_ID, "name": "lookup", "args": data}])
        return [("args", [hello, call, done], None, lambda r: msg(r, 1).tool_calls[0]["args"], want)]
    raise ValueError("unknown position " + position)


def clip(v, n=300):
    s = v if isinstance(v, str) else json.dumps(v, default=repr, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "..."


def check(case, messages, system, where, want):
    """The problems of one shape."""
    v = Vault()
    for e in case["known"]:
        v.tag(e["label"], e["value"])
    mw = pii_middleware(Nothing(), vault=v)
    seen = []
    request = ModelRequest(model=GenericFakeChatModel(messages=iter([])), messages=messages, system_message=system)
    try:
        mw.wrap_model_call(request, lambda r: (seen.append(r), ModelResponse(result=[AIMessage("ok")]))[1])
        got = seen[0]
        at = where(got)
    except Exception as e:  # an unexpected error is a failure of the case
        return ["error: " + type(e).__name__ + ": " + str(e)[:200]]
    payload = {
        "messages": [m.model_dump() for m in got.messages],
        "system": got.system_message.model_dump() if got.system_message is not None else None,
    }
    written = [
        json.dumps(payload, default=repr, ensure_ascii=False),
        json.dumps(payload, default=repr),
        repr(got.messages),
        repr(got.system_message),
    ]
    problems = []
    for s in case["leak"]:
        if any(f in w for f in written_forms(s) for w in written):
            problems.append("leak: " + clip(s, 80))
    shown = json.dumps(at, default=repr, ensure_ascii=False) if not isinstance(at, str) else at
    for mark in placeholders(case):
        if mark not in shown:
            problems.append("placeholder: " + mark + " missing")
    if canon(at) != canon(want):
        problems.append("exact: received " + clip(at) + "; expected " + clip(want))
    return problems


_RESULTS = {}


def results():
    """{case id: {"<position>/<shape>": [problems]}} of the cases that fail (run once)."""
    if not _RESULTS:
        for case in FUZZ["cases"]:
            failed = {}
            for position in FUZZ["positions"]:
                for name, messages, system, where, want in shapes(case, position):
                    p = check(case, messages, system, where, want)
                    if p:
                        failed[position + "/" + name] = p
            _RESULTS[case["id"]] = failed
        report = os.environ.get("VAULT_FUZZ_REPORT")
        if report:
            os.makedirs(report, exist_ok=True)
            out = {
                c["id"]: {"group": c["group"], "depth": c["depth"], "steps": c["steps"], "known": c["known"], "text": c["text"],
                          "exact": c["exact"], "failed": _RESULTS[c["id"]]}
                for c in FUZZ["cases"] if _RESULTS[c["id"]]
            }
            with open(os.path.join(report, "vault-fuzz-python.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
    return _RESULTS


def summary(cases):
    lines = []
    for c in cases:
        failed = results()[c["id"]]
        first = next(iter(failed.items()))
        kinds = sorted({p.split(":")[0] for ps in failed.values() for p in ps})
        lines.append(c["id"] + " (depth " + str(c["depth"]) + ", " + " > ".join(n if i is None else n + "@" + str(i) for n, i in c["steps"])
                     + "; " + ", ".join(kinds) + " at " + str(len(failed)) + " shapes): " + first[0] + ": " + first[1][0][:300])
    return lines


def group_of(chain):
    """D1: "two-layer" when each value's human step (at most one) comes before the first layer and there are at most two
    layers (a step with a value index is a human step, one without a layer); else "beyond"."""
    layers, humans = 0, {}
    for _name, i in chain:
        if i is None:
            layers += 1
        elif layers:
            return "beyond"
        else:
            humans[i] = humans.get(i, 0) + 1
    return "two-layer" if layers <= 2 and all(n == 1 for n in humans.values()) else "beyond"


def test_fuzz_file():
    """The file the generator writes: at least 2,000 distinct cases, each in one group of D1."""
    assert FUZZ["version"] == 1
    cases = FUZZ["cases"]
    assert len(cases) >= 2000
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({json.dumps([c["known"], c["text"]], ensure_ascii=False) for c in cases}) == len(cases)
    for c in cases:
        assert c["group"] == group_of(c["steps"]) and 1 <= c["depth"] <= 4 and len(c["steps"]) == c["depth"]
        assert all(s in c["text"] for s in c["spans"])
        assert not any(s in c["exact"] for s in c["leak"])
    assert isinstance(OPEN, dict) and all(isinstance(v, str) and v.strip() for v in OPEN.values())
    ids = {c["id"] for c in cases}
    assert [k for k in OPEN if k.split("@")[0] not in ids] == []


def test_fuzz_two_layer_product():
    """D1: the two-layer product is guaranteed; a failure here blocks the release."""
    failing = [c for c in FUZZ["cases"] if c["group"] == "two-layer" and results()[c["id"]]]
    total = sum(1 for c in FUZZ["cases"] if c["group"] == "two-layer")
    assert not failing, str(len(failing)) + " of " + str(total) + " two-layer cases fail (release-blocking):" + NL + NL.join(summary(failing))


def test_fuzz_beyond_product_dispositioned():
    """D1: other compositions through four steps are best effort; each failure is listed with its disposition."""
    beyond = [c for c in FUZZ["cases"] if c["group"] == "beyond"]
    failing = [c for c in beyond if results()[c["id"]]]
    undispositioned = [c for c in failing if c["id"] + "@" + RUNNER not in OPEN]
    stale = sorted(k for k in OPEN if k.endswith("@" + RUNNER) and not results().get(k.split("@")[0]))
    by_depth = {}
    for c in failing:
        by_depth[c["depth"]] = by_depth.get(c["depth"], 0) + 1
    head = (str(len(failing)) + " of " + str(len(beyond)) + " cases beyond the product fail (by depth " + json.dumps(by_depth, sort_keys=True)
            + "); " + str(len(undispositioned)) + " not in vault_fuzz_open.json")
    assert not undispositioned and not stale, head + NL + NL.join(summary(undispositioned)) + (
        NL + "passing now, take off vault_fuzz_open.json: " + ", ".join(stale) if stale else "")
