# The examples in examples/ (agent_tool_results.py, mask_pdf.py) and mask_documents, run with the nano model and a
# scripted chat model instead of a hosted one. Skips without langchain / pypdf / the local model copies.
import importlib.util
import json
import re
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load(name):
    path = EXAMPLES / f"{name}.py"
    if not path.is_file():
        pytest.skip("examples/ is not next to the tests")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_tool_results_example(nano):
    pytest.importorskip("langchain.agents.middleware")
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from newmean_pii import Vault

    ex = load("agent_tool_results")
    seen = []

    class Model(GenericFakeChatModel):
        """Turn 1: look the customer up (by whatever the model was shown) and search tickets. Turn 2: answer."""

        def _generate(self, messages, *args, **kwargs):
            seen.append(list(messages))
            if not any(isinstance(m, ToolMessage) for m in messages):
                asked = next(m for m in messages if isinstance(m, HumanMessage)).content
                who = re.search(r"\[PERSON_\d+\]|Kim Minsu", asked).group(0)
                msg = AIMessage("", tool_calls=[
                    {"id": "c1", "name": "lookup_customer", "args": {"name": who}},
                    {"id": "c2", "name": "search_tickets", "args": {"query": "refund"}},
                ])
            else:
                msg = AIMessage(f"{who_of(messages)}'s refund is promised by Friday.")
            return ChatResult(generations=[ChatGeneration(message=msg)])

        def bind_tools(self, tools, **kwargs):
            return self

    def who_of(messages):
        return re.search(r"\[PERSON_\d+\]|Kim Minsu", next(m for m in messages if isinstance(m, HumanMessage)).content).group(0)

    vault = Vault()
    agent = ex.build_agent(Model(messages=iter([])), nano, vault)
    out = agent.invoke({"messages": [{"role": "user", "content": "What happened with Kim Minsu's refund?"}]})

    sent = json.dumps([[m.content for m in call] for call in seen], ensure_ascii=False)
    for value in ["minsu.kim@example.com", "lee.sora@example.com", "2345-6789"]:
        assert value not in sent, f"{value} reached the model"
    tool_results = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
    assert any("minsu.kim@example.com" in r for r in tool_results)  # the tool itself got the real name
    assert out["messages"][-1].content.endswith("refund is promised by Friday.")
    assert "[PERSON_" not in out["messages"][-1].content  # the answer came back with the name


def test_mask_documents(nano):
    pytest.importorskip("langchain_core.documents")
    from langchain_core.documents import Document

    from newmean_pii import Vault
    from newmean_pii.langchain import mask_documents

    docs = [Document(page_content="Write to kim@example.com.", metadata={"source": "t.txt"}), Document(page_content="")]
    out = mask_documents(docs, nano)
    assert out[0].page_content == nano.redact("Write to kim@example.com.").text and "[EMAIL]" in out[0].page_content
    assert out[0].metadata == {"source": "t.txt"} and out[1].page_content == ""
    assert docs[0].page_content == "Write to kim@example.com."
    vault = Vault()
    numbered = mask_documents(docs[:1], nano, vault=vault)
    assert "[EMAIL_1]" in numbered[0].page_content and vault.restore(numbered[0].page_content) == "Write to kim@example.com."
    with pytest.raises(TypeError):
        mask_documents(docs, nano, size="nano")


def _pdf(text):
    """A one-page PDF with one line of Helvetica text (enough for pypdf's text extraction)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_mask_pdf_example(nano, tmp_path):
    pytest.importorskip("pypdf")
    ex = load("mask_pdf")
    path = tmp_path / "t.pdf"
    path.write_bytes(_pdf("Contact: kim@example.com or 010-1234-5678"))
    pages = ex.masked_pages(str(path), nano)
    assert len(pages) == 1 and "kim@example.com" not in pages[0] and "[EMAIL]" in pages[0]
