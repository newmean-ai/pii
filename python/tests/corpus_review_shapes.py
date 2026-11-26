"""Leak corpus cases of the review of 1.7.1 (2026-11-25), class "shapes": values that are not plain JSON, and objects
that only look like the framework's.

L3-A1: a real agent's tool returned rows of a storage API typed like LangChain's blocks, Box style: [{"type": "file",
"id": "1024", "name": "Maria Lopez CV.pdf", "owned_by": {...}}]. LangChain keeps a list of such rows as content
blocks, and the middlewares took every object whose type is a block type for a block: the model read none of its
fields, and the check before sending left its id and url as written (a document row sent a known e-mail address in
both). The class: in what came from outside (a tool's result, a user's message), an object is a block only when it
has its type's own content (a text block its text, a file or image its url, base64 data or file id, a document its
source, a json block its json, a message its role); any other typed object is a row of data, read by the model, every
field and key counted. Siblings: every type a row can share with a block (the block types a tool message takes, the
provider types, the types of sources, tool calls and signed reasoning), rows inside a tool_result block, rows after a
text block, and real blocks, which stay blocks.

L4-K2: an earlier answer of the model that is a JSON document whose keys become one placeholder
('{"Kim Minsu": 1, "KIM MINSU": 2}') was sent with two equal keys, so a JSON reader keeps one field. Keys that become
the same placeholder get " #2"; keys that were the same before stay the same. The Python runner counts distinct keys
("fields"); the JS runner counts keys as written, so the npm side of this is tested in ai-sdk.test.ts and
langchain.test.ts.

Not in this corpus, tested in the packages: values that JSON text cannot hold (review 24 A2: JS class instances
returned as LangChain.js blocks, also with block fields given by getters, langchain.test.ts; review 24 A3: pydantic
serializers, extra keys and aliases, test_vault.py), and restoring after a recorded answer is sent (L4-R1:
ai-sdk.test.ts, langchain.test.ts, test_vault.py). Read by gen_vault_corpus.py (load_parts)."""
from __future__ import annotations

NAME = "Maria Lopez"
EMAIL = "maria.lopez@example.com"
URL = "https://crm.example.com/people/maria.lopez@example.com"

# Every type a row can share with a block, a source, a tool call or signed reasoning, as LangChain and the providers
# write them (js/src/masker.ts LANGCHAIN_TYPES, python/src/newmean_pii/langchain.py _LANGCHAIN_TYPES)
TYPES = (
    # the block types a tool message takes, and the other standard blocks
    "text", "image_url", "image", "json", "search_result", "custom_tool_call_output", "document", "file", "audio",
    "video", "text-plain", "non_standard", "reasoning", "refusal", "citation", "tool_call", "server_tool_result",
    # Anthropic blocks and sources
    "thinking", "redacted_thinking", "tool_use", "tool_result", "base64", "url", "content", "web_search_result",
    # OpenAI chat and Responses
    "input_text", "output_text", "input_image", "input_file", "input_audio", "function", "function_call",
    "function_call_output", "message", "item_reference", "mcp_call",
    # Google
    "media", "executable_code",
)


def row(kind):
    """A row of a storage or CRM API with its own type: a name, an id, an owner and a key that hold the values."""
    return {"type": kind, "id": EMAIL, "name": NAME + " CV.pdf", "owned_by": {"type": "user", "id": "9", "name": NAME,
            "login": EMAIL}, EMAIL: "owner"}


def add(g):
    case = g.case
    PERSON = ("PERSON", NAME)
    MAIL = ("EMAIL", EMAIL)
    found = [(NAME, "PERSON"), (EMAIL, "EMAIL")]
    known_at = ["user", "system", "tool-text", "tool-data", "block-data", "tool-input-unrecorded"]
    # where the model reads a tool's rows (the stand-in detector reports the values; nothing is known before)
    read_at = ["user", "tool-text", "tool-data", "tool-input-unrecorded"]

    # the reviewer's rows (Box style)
    box = [{"type": "file", "id": "1024", "name": NAME + " CV.pdf",
            "owned_by": {"type": "user", "id": "9", "name": NAME, "login": EMAIL}},
           {"type": "file", "id": "1025", "name": EMAIL + " - offer.docx",
            "owned_by": {"type": "user", "id": "9", "name": NAME, "login": EMAIL}}]
    case("rev171-L3A1-box-file-rows-read", "1.7.1 review, L3-A1: Box-style rows typed file, read by the model",
         g.jdump(box), data=box, detect=found, where=read_at, leak=[NAME, EMAIL], keep=["1024", "1025", "offer.docx"])
    case("rev171-L3A1-box-file-rows-known", "L3-A1: Box-style rows typed file, with the values known",
         g.jdump(box), data=box, known=[PERSON, MAIL], where=known_at, leak=[NAME, EMAIL], keep=["1024", "offer.docx"])
    doc = [{"type": "document", "name": NAME, "id": EMAIL, "url": URL}]
    case("rev171-L3A1-document-row-known", "L3-A1: a row typed document with a name, an id and a url that hold known "
         "values", g.jdump(doc), data=doc, known=[PERSON, MAIL], where=known_at, leak=[NAME, EMAIL])
    case("rev171-L3A1-document-row-read", "L3-A1: a row typed document, read by the model", g.jdump(doc), data=doc,
         detect=found, where=read_at, leak=[NAME, EMAIL])
    # a file block with its own content (a url) in a tool's result: a block, and its id and name are data there (no
    # block in a tool's result is matched by its id; a user's block keeps its id: gate 4 of the 1.7.1 standard)
    linked = [{"type": "file", "id": EMAIL, "name": NAME, "url": "https://files.example.com/1024.pdf"}]
    case("rev171-L3A1-linked-file-block-known", "L3-A1: a file block with a url in a tool's result, its id and name known "
         "values", g.jdump(linked), data=linked, known=[PERSON, MAIL], where=["tool-data", "block-data"], leak=[NAME, EMAIL],
         keep=["files.example.com/1024.pdf"])

    # siblings: a row of every type a row can share with the framework's objects
    for kind in TYPES:
        rows = [row(kind)]
        case(f"rev171-L3A1-row-{kind}-known", f"L3-A1 sibling: a row typed {kind}, with the values known",
             g.jdump(rows), data=rows, known=[PERSON, MAIL], where=known_at, leak=[NAME, EMAIL], keep=["owner"])
        case(f"rev171-L3A1-row-{kind}-read", f"L3-A1 sibling: a row typed {kind}, read by the model", g.jdump(rows),
             data=rows, detect=found, where=["tool-data"], leak=[NAME, EMAIL], keep=["owner"])

    # siblings: rows in other places of a tool's result
    nested = [{"type": "tool_result", "tool_use_id": "c1", "content": [row("file"), row("document"), row("message")]}]
    case("rev171-L3A1-rows-in-tool-result-known", "L3-A1 sibling: rows inside a tool_result block", g.jdump(nested),
         data=nested, known=[PERSON, MAIL], where=["tool-data", "block-data"], leak=[NAME, EMAIL], keep=["owner"])
    case("rev171-L3A1-rows-in-tool-result-read", "L3-A1 sibling: rows inside a tool_result block, read by the model",
         g.jdump(nested), data=nested, detect=found, where=["tool-data"], leak=[NAME, EMAIL], keep=["owner"])
    after = [{"type": "text", "text": "Results:"}, row("file"), row("json"), row("url")]
    case("rev171-L3A1-rows-after-text-known", "L3-A1 sibling: rows after a text block", g.jdump(after), data=after,
         known=[PERSON, MAIL], where=["tool-data", "block-data"], leak=[NAME, EMAIL], keep=["Results:", "owner"])
    case("rev171-L3A1-rows-after-text-read", "L3-A1 sibling: rows after a text block, read by the model", g.jdump(after),
         data=after, detect=found, where=["tool-data"], leak=[NAME, EMAIL], keep=["Results:", "owner"])

    # real blocks stay blocks (with their own content: a text, an image's URL, a file's base64 data, a document's
    # source); their content is unchanged. (That a known value inside a file's URL stays, the decided exception, is
    # LangChain's alone: langchain.test.ts and test_vault.py.)
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk" + "A" * 24
    real = [{"type": "text", "text": "See the files."},
            {"type": "image", "url": "https://files.example.com/q3.png", "mime_type": "image/png"},
            {"type": "file", "base64": png, "mime_type": "image/png", "id": "blk_1"},
            {"type": "document", "source": {"type": "url", "url": "https://files.example.com/q3.pdf"}, "title": "Report"}]
    case("rev171-L3A1-real-blocks-kept", "L3-A1: blocks with their own content stay blocks, and their content is "
         "unchanged", g.jdump(real), data=real, known=[MAIL], where=["tool-data"],
         keep=["See the files.", "files.example.com/q3.png", png, "blk_1", "files.example.com/q3.pdf"])

    # L4-K2: keys of a model's earlier JSON answer that become one placeholder
    for cid, text, known in (
        ("names", '{"Kim Minsu": 1, "KIM MINSU": 2}', [g.KIM]),
        ("emails", '{"kim@example.com": {"vip": true}, "Kim@Example.com": {"vip": false}}', [("EMAIL", "kim@example.com")]),
        ("numbers", '{"4524": "a", "4.524e3": "b"}', [("ID_NUMBER", "4524")]),
        ("three", '{"Kim Minsu": 1, "KIM MINSU": 2, "Kim  Minsu": 3}', [g.KIM]),
    ):
        data = g.json.loads(text)
        case(f"rev171-L4K2-model-json-keys-{cid}", "1.7.1 review, L4-K2: keys of a JSON document that become one "
             "placeholder, also in a model's earlier answer", text, data=data, known=known,
             where=["user", "tool-text", "tool-data", "block-data", "assistant-unrecorded", "tool-input-unrecorded"],
             leak=[k for k in data if len(k) >= 3], fields=len(data))
    kept = '{"[PERSON_1]": 1, "Kim Minsu": 2}'
    case("rev171-L4K2-model-json-key-and-its-placeholder", "L4-K2 sibling: a model's JSON answer with a placeholder of "
         "this vault as a key and the value it stands for as another", kept, data=g.json.loads(kept), known=[g.KIM],
         where=["assistant-unrecorded"], leak=["Kim Minsu"], fields=2)
