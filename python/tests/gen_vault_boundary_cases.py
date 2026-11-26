"""Writes vault_boundary_cases.json, the shared cases of the boundary tests (gate 4 of the 1.7.1 release standard),
next to this file.

    python3 python/tests/gen_vault_boundary_cases.py

Standard library only, deterministic (no randomness). Two runners read the file: js/test/vault-boundary.test.ts (AI SDK
5, 6 and 7, LangChain.js) and python/tests/test_vault_boundary.py (Python LangChain, sync and async).

Format:

    {"version": 1,
     "held": [{"label", "value"}],          put into a fresh vault with vault.tag(label, value) before each call, in order
     "identifiers": [name],                 field names the middlewares treat as identifiers in the framework's own objects
                                            (ids, names, references to files, signatures, discriminators): each is exempt
                                            only at its authorized path; planted with a held value anywhere else, the
                                            value must be masked
     "frame_fields": [name],                field names that hold the framework's own objects in a framework object
                                            (input, content, tool_calls, ...): planted inside data, what they hold is data
     "readings": [{"id", "note", "steps", "decode_steps", "guaranteed", "layout", "text", "expect", "source"}]}

readings: a held value written through decoding steps and full-width folds, in an order where width must be folded after
each decoding step (the reverse-order regression of the 24th outside review, A4: "mail kim%EF%BC%8540example.com" with
"kim@example.com" held reached the provider). "steps" lists the readings in the order a reader takes them ("percent", "unicode"
for a backslash-u escape, "pyrepr" for a Python bytes repr, "fold" for full-width ASCII read as ASCII). "decode_steps" counts
the decoding steps (a fold is not one); "guaranteed" is true when there are at most two (D1 of the release standard: the
two-layer product is guaranteed, compositions through four steps are best effort). "text" is the whole text at the
position; "expect" is that text after masking, exactly: the whole source span ("source") is replaced by the placeholder and
the neighbours stay as written (composed spans replace the whole source span).

Backslashes are built with chr(92) (REVIEW-GUIDE rule 9). The script checks what it writes for stray control and format
characters before it writes it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

BS = chr(92)
HERE = Path(__file__).resolve().parent
OUT = HERE / "vault_boundary_cases.json"

HELD = [
    {"label": "PERSON", "value": "Kim Minsu"},
    {"label": "EMAIL", "value": "kim@example.com"},
    {"label": "PHONE", "value": "821023456789"},
    {"label": "DATE_OF_BIRTH", "value": "1990-01-02"},
]
EMAIL_TAG = "[EMAIL_1]"  # (the first EMAIL the vault numbers: HELD has one)

# The identifiers of the framework's own objects (js/src/masker.ts NOT_TEXT, python langchain.py _NOT_TEXT) and the
# identifiers providers define in provider options and model settings (OPTION_KEPT). Each is exempt only where the
# framework or the provider defines it.
IDENTIFIERS = [
    "type", "role", "id", "name", "index", "toolCallId", "toolName", "approvalId", "tool_call_id", "tool_use_id",
    "call_id", "mediaType", "mimeType", "mime_type", "media_type", "url", "originalUrl", "image_url", "file_id", "fileId",
    "reference", "signature", "encrypted_content", "providerMetadata", "response_metadata", "usage_metadata",
    "thoughtSignature", "thought_signature", "redactedData", "redacted_data", "reasoningEncryptedContent",
    "encryptedContent", "previousResponseId", "previous_response_id", "itemId", "item_id", "conversationId",
    "conversation_id", "containerId", "container_id", "fileIds", "file_ids", "serverLabel", "server_label", "model",
    "toolChoice", "tool_choice", "providerOptions",
]
# Fields that hold the framework's own objects inside a framework object (js/src/masker.ts FRAME_KEYS).
FRAME_FIELDS = [
    "input", "args", "arguments", "output", "content", "source", "function", "function_call", "tool_calls",
    "invalid_tool_calls", "data", "json", "value", "citations", "annotations", "extras", "additional_kwargs",
]


def wide(s: str) -> str:
    """s in full-width ASCII (U+FF01 to U+FF5E); a space as U+3000."""
    return "".join(chr(0x3000) if c == " " else chr(ord(c) + 0xFEE0) for c in s)


def pct(s: str) -> str:
    """Every UTF-8 byte of s percent-encoded, upper-case hex."""
    return "".join("%" + format(b, "02X") for b in s.encode("utf-8"))


def pct_again(s: str) -> str:
    """s with each percent sign percent-encoded once more ("%40" as "%2540")."""
    return s.replace("%", "%25")


def uesc(s: str) -> str:
    """Each character of s as a backslash-u escape (lower-case hex, as JSON writes it)."""
    return "".join(BS + "u" + format(ord(c), "04x") for c in s)


def bytes_repr(s: str) -> str:
    """s as Python writes its UTF-8 bytes with repr(): b'kim' and then a hex escape per byte outside ASCII (what
    LangChain writes with str() for a tool that returns bytes)."""
    return repr(s.encode("utf-8"))


AT = "@"
PCT = "%"
LOCAL, DOMAIN = "kim", "example.com"

# (id, note, steps, source): the held e-mail address written through the steps, as it stands in the text
SPELLINGS = [
    ("fold-percent", "a full-width percent sign before 40 (the earlier fixture of A4)", ["fold", "percent"],
     LOCAL + wide(PCT) + "40" + DOMAIN),
    ("a4-percent-fold-percent", "24th outside review A4: the full-width percent sign percent-encoded",
     ["percent", "fold", "percent"], LOCAL + pct(wide(PCT)) + "40" + DOMAIN),
    ("percent-fold", "a full-width at sign percent-encoded", ["percent", "fold"], LOCAL + pct(wide(AT)) + DOMAIN),
    ("percent-percent-fold", "a full-width at sign percent-encoded twice", ["percent", "percent", "fold"],
     LOCAL + pct_again(pct(wide(AT))) + DOMAIN),
    ("percent-percent-fold-percent", "A4 percent-encoded once more", ["percent", "percent", "fold", "percent"],
     LOCAL + pct_again(pct(wide(PCT))) + "40" + DOMAIN),
    ("unicode-fold", "a full-width at sign as a backslash-u escape", ["unicode", "fold"], LOCAL + uesc(wide(AT)) + DOMAIN),
    ("unicode-fold-percent", "a full-width percent sign as a backslash-u escape before 40", ["unicode", "fold", "percent"],
     LOCAL + uesc(wide(PCT)) + "40" + DOMAIN),
    ("percent-unicode-fold-percent", "the backslash of that escape percent-encoded",
     ["percent", "unicode", "fold", "percent"], LOCAL + pct(BS) + uesc(wide(PCT))[1:] + "40" + DOMAIN),
    ("pyrepr-fold-percent", "a Python bytes repr of the full-width percent sign before 40", ["pyrepr", "fold", "percent"],
     None),  # (built below: the repr is the whole text)
    ("percent-fold-letters", "a full-width letter and at sign, each percent-encoded", ["percent", "fold"],
     pct(wide("k")) + "im" + pct(wide(AT)) + DOMAIN),
    ("percent-fold-percent-digits", "a full-width percent sign and full-width digits, percent-encoded",
     ["percent", "fold", "percent"], LOCAL + pct(wide("%40")) + DOMAIN),
    ("fold-percent-all", "the whole address in full width with a full-width percent escape", ["fold", "percent"],
     wide(LOCAL + "%40" + DOMAIN)),
    ("percent-percent", "control: percent-encoded twice, no width", ["percent", "percent"], LOCAL + "%2540" + DOMAIN),
    ("fold-percent-fold", "full-width percent signs that encode a full-width at sign", ["fold", "percent", "fold"],
     LOCAL + wide(PCT) + "EF" + wide(PCT) + "BC" + wide(PCT) + "A0" + DOMAIN),
    ("fold-all-percent-fold", "the same escape written wholly in full width", ["fold", "percent", "fold"],
     LOCAL + wide(pct(wide(AT))) + DOMAIN),
    ("percent-fold-percent-fold", "those full-width percent signs percent-encoded",
     ["percent", "fold", "percent", "fold"],
     LOCAL + pct(wide(PCT)) + "EF" + pct(wide(PCT)) + "BC" + pct(wide(PCT)) + "A0" + DOMAIN),
]
DECODING = {"percent", "unicode", "pyrepr"}

# (layout, prefix, suffix): where the source stands in the text
LAYOUTS = [
    ("url", "https://crm.test/u?mail=", "&ok=1"),
    ("sentence", "Write to ", " today."),
    ("alone", "", ""),
]


def readings() -> list:
    out = []
    for cid, note, steps, source in SPELLINGS:
        decode_steps = sum(1 for s in steps if s in DECODING)
        for layout, before, after in LAYOUTS:
            if source is None:
                # the whole text is the repr (b'kim', hex escapes, '40example.com'): its b'' quotes are the neighbours
                written = bytes_repr(LOCAL + wide(PCT) + "40" + DOMAIN)
                src = written[2:-1]
                text = before + written + after
                expect = before + written[:2] + EMAIL_TAG + written[-1:] + after
            else:
                src = source
                text = before + source + after
                expect = before + EMAIL_TAG + after
            out.append({
                "id": f"{cid}@{layout}",
                "note": note,
                "steps": steps,
                "decode_steps": decode_steps,
                "guaranteed": decode_steps <= 2,
                "layout": layout,
                "text": text,
                "expect": expect,
                "source": src,
            })
    return out


def check(doc: dict) -> None:
    """No stray control or format characters (Cc, Cf, Zl, Zp) anywhere in what is written."""
    def walk(x, path):
        if isinstance(x, str):
            for c in x:
                if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp"):
                    raise SystemExit(f"stray character U+{ord(c):04X} at {path}")
        elif isinstance(x, list):
            for i, y in enumerate(x):
                walk(y, f"{path}[{i}]")
        elif isinstance(x, dict):
            for k, y in x.items():
                walk(k, f"{path}.key")
                walk(y, f"{path}.{k}")

    walk(doc, "$")
    ids = [r["id"] for r in doc["readings"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate reading ids")
    for r in doc["readings"]:
        if r["source"] not in r["text"] or r["expect"].count(EMAIL_TAG) != 1:
            raise SystemExit(f"bad reading case {r['id']}")
        # every spelling reads, step by step, as the held address (the expectation is about a real spelling of it)
        if decoded(r["source"], r["steps"]) != LOCAL + AT + DOMAIN:
            raise SystemExit(f"{r['id']}: the steps do not read as the address: {decoded(r['source'], r['steps'])!r}")


def decoded(source: str, steps: list) -> str:
    """source read through steps, in order: a reference reader for the fixtures (not the SDK's)."""
    import ast
    import urllib.parse

    narrow = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
    narrow[0x3000] = 0x20
    text = source
    for step in steps:
        if step == "percent":
            text = urllib.parse.unquote(text, errors="strict")
        elif step == "fold":
            text = text.translate(narrow)
        elif step == "unicode":
            text = re.sub(re.escape(BS) + "u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        elif step == "pyrepr":
            text = ast.literal_eval("b'" + text + "'").decode("utf-8")
        else:
            raise SystemExit(f"unknown step {step}")
    return text


def main() -> None:
    doc = {"version": 1, "held": HELD, "identifiers": IDENTIFIERS, "frame_fields": FRAME_FIELDS, "readings": readings()}
    check(doc)
    OUT.write_text(json.dumps(doc, ensure_ascii=True, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: {len(doc['readings'])} reading cases, {len(IDENTIFIERS)} identifiers")


if __name__ == "__main__":
    main()
