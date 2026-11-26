"""Leak corpus cases of the class "data walking and keys" (SDK 1.7.1): C1, B1, G2, G4, G21 of the guarantee note
(section 6) and their siblings on the same axes. Read by
gen_vault_corpus.py (load_parts). G3 (JS class instances), G10, G46 and K6o (Python value types), G15
(additional_kwargs), G17 (originalUrl) and G39 (AI SDK part types) are not JSON positions of the corpus: their tests are
in js/test/ai-sdk.test.ts, js/test/langchain.test.ts and python/tests/test_vault.py, and their doubling tests in
js/test/vault-growth.test.ts and python/tests/test_vault_growth.py ("other-fields", "class instances", "python-values")."""
from __future__ import annotations


def add(g):
    KIM = g.KIM
    EMAIL = ("EMAIL", "kim@example.com")
    USER = ("USERNAME", "kim_0142")
    PHONE_NUM = ("PHONE", "821023456789")

    # C1 (keys K3): keys that were the same before masking stay the same after it; " #2" only tells apart keys that
    # were different before ("Al" and "%41l")
    case = g.case
    case("data-C1-identical-keys-escaped", "C1 sibling: an identical key written with a JSON escape is the same key",
         '{"Kim Minsu":1,"Kim Minsu":2,"Kim' + g.uesc(" ") + 'Minsu":3}', known=[KIM], where=g.TEXTS,
         leak=["Kim Minsu", "Minsu"], extra=[" #2", " #3"])
    case("data-C1-identical-keys-two-values", "C1 sibling: two known values, each as two identical keys",
         '{"kim@example.com":1,"Kim Minsu":2,"kim@example.com":3,"Kim Minsu":4}', known=[KIM, EMAIL], where=g.TEXTS,
         leak=["Kim Minsu", "kim@example.com"], extra=[" #2"])
    case("data-C1-identical-keys-per-object", "C1 sibling: identical keys in each object of a list, and in a nested one",
         '[{"Kim Minsu":1,"Kim Minsu":2},{"Kim Minsu":3,"x":{"Kim Minsu":4,"Kim Minsu":5}}]', known=[KIM], where=g.TEXTS,
         leak=["Kim Minsu"], extra=[" #2"])
    case("data-C1-identical-and-other-spelling", "C1 sibling: a key written another way gets \" #2\"; the key identical "
         "to the first stays the same as the first", '{"Kim Minsu":1,"Kim%20Minsu":2,"Kim Minsu":3}', known=[KIM],
         where=g.TEXTS, leak=["Kim Minsu", "Kim%20Minsu"], extra=[" #3"])

    # G2 (positions Q5, keys K4): rows a tool returned with a "type" key of their own (CRM rows) are data: the model
    # reads every field, and every key counts
    rows = [{"type": "contact", "name": "Kim Minsu", "id": "kim@example.com", "url": "https://crm.example.com/u/kim_0142"}]
    case("data-G2-typed-rows-read", "G2: CRM rows with a type of their own; the model finds the name, the vault the rest",
         g.jdump(rows), data=rows, detect=[("Kim Minsu", "PERSON")], known=[EMAIL, USER], where=["tool-data"],
         leak=["Kim Minsu", "kim@example.com", "kim_0142"])
    case("data-G2-typed-rows-known", "G2: the same rows with every value known (a typed block only hides known values)",
         g.jdump(rows), data=rows, known=[KIM, EMAIL, USER], where=["tool-data", "block-data"],
         leak=["Kim Minsu", "kim@example.com", "kim_0142"])
    row = {"type": "contact", "kim@example.com": 1, "role": "billing"}
    case("data-G2-typed-row-keys", "G2: a key of a typed row is a key of data", g.jdump(row), data=row, known=[EMAIL],
         where=["tool-data", "block-data"], leak=["kim@example.com"], keep=["billing"], fields=3)
    nums = [{"type": "customer", "id": 821023456789, "index": 821023456789, "n": 3}]
    case("data-G2-typed-row-numbers", "G2: a number in a typed row is a number of data", g.jdump(nums), data=nums,
         known=[PHONE_NUM], where=["tool-data", "block-data"], leak=["821023456789"])
    mixed = [{"type": "text", "text": "Results:"}, {"type": "contact", "name": "Kim Minsu", "id": "c-17"}]
    case("data-G2-typed-row-after-text-block", "G2 sibling: a typed row after a text block is a row of data, read by the "
         "model", g.jdump(mixed), data=mixed, detect=[("Kim Minsu", "PERSON")], where=["tool-data"],
         leak=["Kim Minsu"], keep=["Results:", "c-17"])
    signed = [{"type": "reasoning", "signature": "kim@example.com", "reasoning": "Kim Minsu"}]
    case("data-G2-typed-row-signed-type", "G2 sibling: a row whose type is a block type of a framework is that block; "
         "its text is still read (reasoning without a record)", g.jdump(signed), data=signed, known=[KIM],
         where=["tool-data"], leak=["Kim Minsu"])

    # B1 (positions Q6, keys K4): an object under a block field named "data" is data, unless it has the type of a
    # framework shape
    b1 = [{"type": "json", "data": {"name": "Kim Minsu", "id": "kim@example.com", "kim_0142": "x"}}]
    case("data-B1-data-object-fields", "B1 sibling: the name, id and keys of an object under data", g.jdump(b1), data=b1,
         known=[KIM, EMAIL, USER], where=["user", "tool-text", "tool-data", "block-data"],
         leak=["Kim Minsu", "kim@example.com", "kim_0142"])
    b1f = [{"type": "file", "mime_type": "application/json", "data": {"name": "Kim Minsu", "Kim Minsu": "vip"}}]
    case("data-B1-file-block-data-object", "B1: an object under the data of a file block (a framework block type)",
         g.jdump(b1f), data=b1f, known=[KIM], where=["tool-data", "block-data"], leak=["Kim Minsu"])
    b1l = [{"type": "file", "mime_type": "application/json", "data": [{"name": "Kim Minsu", "id": 821023456789}]}]
    case("data-B1-file-block-data-list", "B1 sibling: a list under the data of a file block", g.jdump(b1l), data=b1l,
         known=[KIM, PHONE_NUM], where=["tool-data", "block-data"], leak=["Kim Minsu", "821023456789"])

    # G4 (positions P11): a base64-looking string is skipped only when no reading of it holds a known value
    plus = "Kim+Minsu+asked+about+the+refund+for+order+A1234+on+Tuesday+morning+again"
    assert len(plus) >= 64
    case("data-G4-plus-spaced-name", "G4: a known name spelled with + in a string that looks like base64 (81 characters)",
         plus, data={"note": plus}, known=[KIM], where=g.POSITIONS, leak=["Kim+Minsu"])
    plusp = "callback+requested+on+010+1234+5678+before+noon+by+the+customer+today"
    assert len(plusp) >= 64
    case("data-G4-plus-spaced-phone", "G4 sibling: a known phone number spelled with + in a base64-looking string", plusp,
         data={"note": plusp}, known=[("PHONE", "010 1234 5678")], where=g.POSITIONS, leak=["010+1234+5678", "1234+5678"])
    blob = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk" + "A" * 24
    case("data-G4-base64-kept", "G4: base64 with no known value in any reading stays as it is", blob,
         data={"blob": blob}, known=[KIM, EMAIL], where=["tool-data", "block-data"], keep=[blob])

    # G21 (positions Q7, P10i): a data string that is a known value as a whole is hidden; base64 stays
    img = [{"type": "image", "mime_type": "image/png", "data": "kim@example.com"}]
    case("data-G21-data-string-known", "G21: the data string of an image block is a known value as a whole",
         g.jdump(img), data=img, known=[EMAIL], where=["tool-data", "block-data"], leak=["kim@example.com"])
    img2 = [{"type": "image", "mime_type": "image/png", "data": blob}]
    case("data-G21-data-base64-kept", "G21 sibling: base64 data of an image block stays as it is", g.jdump(img2), data=img2,
         known=[KIM, EMAIL], where=["tool-data", "block-data"], keep=[blob])
