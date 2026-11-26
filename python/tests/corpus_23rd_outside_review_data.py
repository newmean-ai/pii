"""Leak corpus cases of the 23rd outside review, item A2 (SDK 1.7.1): tool data mistaken for framework objects. A real
tool returned [{"type": "json", "data": {"type": "text", "name": "Kim Minsu", "Kim Minsu": "open"}}]; the nested "type"
made the last pass take the payload for a framework object, whose "name" and keys it leaves as written. The class: which
objects are the framework's is chosen from the block and the field they stand in, and a JSON payload is data at every
depth, whatever "type" its objects have. Siblings: the payload under other block types and other fields, nested types
at other depths and of other kinds (a tool call, a URL, a function), and objects of a framework type that stand where
the framework's blocks stand but carry data (a "name" a text block does not have, a key that is a known value).

The AI SDK shapes of this class that the corpus positions cannot hold (content items and file parts whose "data" holds
an object) are tested in js/test/ai-sdk.test.ts; item A3 (Python values that are written out when the provider
serializes them: dates, pydantic computed fields, Enum values) in python/tests/test_vault.py. Read by
gen_vault_corpus.py (load_parts)."""
from __future__ import annotations


def add(g):
    case = g.case
    KIM = g.KIM
    EMAIL = ("EMAIL", "kim@example.com")
    # the reviewer's payload: an object with a framework type, a name and a key that are a known value
    row = {"type": "text", "name": "Kim Minsu", "Kim Minsu": "open"}

    reviewed = [{"type": "json", "data": row}]
    case("r23-A2-json-block-data", "23rd outside review, A2: a tool's json block whose data has a type of its own",
         g.jdump(reviewed), data=reviewed, known=[KIM],
         where=["user", "system", "tool-text", "tool-data", "block-data", "tool-input-unrecorded"],
         leak=["Kim Minsu"], keep=["open"])

    # other block types with the same payload under data
    others = [{"type": t, "data": row} for t in (
        "file", "image", "audio", "video", "document", "text-plain", "search_result", "custom_tool_call_output",
        "non_standard", "input_file", "media")]
    case("r23-A2-other-blocks-data", "A2 sibling: the payload under data of every other block type",
         g.jdump(others), data=others, known=[KIM], where=["tool-data", "block-data"], leak=["Kim Minsu"], keep=["open"])

    # the payload under the other fields of a json block (fields that hold blocks in other block types)
    fields = [{"type": "json", "content": row}, {"type": "json", "content": [row]}, {"type": "json", "output": row},
              {"type": "json", "output": [row]}, {"type": "json", "source": row}, {"type": "json", "value": row},
              {"type": "json", "json": row}, {"type": "json", "function": row}]
    case("r23-A2-json-block-other-fields", "A2 sibling: the payload under content, output, source, value, json and "
         "function of a json block", g.jdump(fields), data=fields, known=[KIM], where=["tool-data", "block-data"],
         leak=["Kim Minsu"], keep=["open"])

    # payloads of blocks whose field holds a tool's output, not blocks of the framework
    outputs = [{"type": "server_tool_result", "tool_call_id": "c1", "status": "success", "output": row},
               {"type": "server_tool_result", "tool_call_id": "c1", "status": "success", "output": [row]},
               {"type": "mcp_call", "id": "m1", "name": "lookup", "output": row}]
    case("r23-A2-tool-output-payloads", "A2 sibling: a server tool's output with a type of its own", g.jdump(outputs),
         data=outputs, known=[KIM], where=["tool-data", "block-data"], leak=["Kim Minsu"], keep=["open"])

    # nested types at other depths
    deep = [{"type": "json", "data": {"type": "json", "data": row}},
            {"type": "json", "data": {"rows": [row, {"type": "json", "json": row}]}},
            {"type": "json", "json": {"type": "json", "data": {"type": "file", "data": row}}},
            {"type": "json", "data": [[row]]}]
    case("r23-A2-nested-depths", "A2 sibling: typed objects two, three and four levels down in a payload",
         g.jdump(deep), data=deep, known=[KIM], where=["tool-data", "block-data"], leak=["Kim Minsu"], keep=["open"])

    # nested objects of other framework types, with the fields the last pass keeps in the framework's own objects
    kinds = [{"type": "json", "data": {"type": "tool_call", "name": "Kim Minsu", "id": "kim@example.com", "args": {}}},
             {"type": "json", "data": {"type": "url", "url": "mailto:kim@example.com", "name": "Kim Minsu"}},
             {"type": "json", "data": {"type": "image_url", "image_url": {"url": "https://crm.example.com/kim@example.com"},
                                       "name": "Kim Minsu"}},
             {"type": "json", "data": {"type": "function", "function": {"name": "Kim Minsu", "arguments": "{}"}}},
             {"type": "json", "data": {"type": "tool_result", "tool_use_id": "kim@example.com",
                                       "content": [{"type": "text", "name": "Kim Minsu"}]}},
             {"type": "json", "data": {"type": "reasoning", "signature": "kim@example.com", "reasoning": "Kim Minsu"}}]
    case("r23-A2-nested-framework-kinds", "A2 sibling: a payload that holds a tool call, a URL, an image URL, a "
         "function, a tool result and a reasoning block", g.jdump(kinds), data=kinds, known=[KIM, EMAIL],
         where=["tool-data", "block-data"], leak=["Kim Minsu", "kim@example.com"])

    # objects of a framework type where the framework's blocks stand, with data the type does not have
    top = [row]
    case("r23-A2-text-row-alone", "A2 sibling: a tool returns one object typed text, with a name and a key",
         g.jdump(top), data=top, known=[KIM], where=["tool-data", "block-data"], leak=["Kim Minsu"], keep=["open"])
    after = [{"type": "text", "text": "Results:"}, row, {"type": "image", "url": "https://example.com/a.png",
                                                         "name": "Kim Minsu", "kim@example.com": 1}]
    case("r23-A2-typed-rows-after-blocks", "A2 sibling: data in objects typed text and image after a text block",
         g.jdump(after), data=after, known=[KIM, EMAIL], where=["tool-data", "block-data"],
         leak=["Kim Minsu", "kim@example.com"], keep=["Results:", "open"])
    inside = [{"type": "tool_result", "tool_use_id": "c1", "content": [row, {"type": "text", "text": "Kim Minsu"}]}]
    case("r23-A2-tool-result-content", "A2 sibling: an object typed text with data inside a tool_result block",
         g.jdump(inside), data=inside, known=[KIM], where=["tool-data", "block-data"], leak=["Kim Minsu"],
         keep=["open"])
