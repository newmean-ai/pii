"""The finite product of gate 2 of the 1.7.1 release standard, run in full: frozen seeds
(vault_steps.SEEDS: a known value of every label, several scripts) x every human variant that applies to the seed
(vault_steps.HUMAN: the case, NFD, full-width, white-space, line-break and date spellings that section 2.1 and section
10 of the guarantee note put in scope, each kind on its own; the README section "How a known value is found again") x
the named serializer settings (vault_steps.PRODUCT_LAYERS: json.dumps with ensure_ascii, JSON.stringify, which is
json.dumps without it and with compact separators, str() of a Python dict, quote, quote_plus, a form body, and str() of
UTF-8 bytes) applied zero to two layers, every ordered pair, x every position the text can stand in. Per D1 the release
blocks on any failure here: zero leaks, zero changed keep-text, zero unexpected errors.

Each case holds one known value, written once in a sentence whose neighbors have characters every layer escapes
("Café note: <value>, danke schön."). At the data positions the text arrives as model_dump(mode="json") of a pydantic
model writes it, {"note": text, "id": 7}, with the text also as a key (an extra field), so a key is a position too.
Besides "leak" (the value as written, and as the vault holds it), each case has "exact": the text that must reach the
provider, which is the same text with the written span replaced by the placeholder and every neighbor unchanged. A
JSON document is compared by what it holds (section 5 lets the middleware write a changed string again as JSON writes
it). At a data position the expected data is the case's data with the text replaced by "exact", as a value and as a
key; at "message-name" the placeholder is written without brackets (section 10, Q13). A date variant writes text around
the value (an ISO timestamp's time, a repr's call): the vault replaces the date and that text stays (S17).

The keep seed (a one-word name in small letters, "rose" for a known "Rose", U1) must come out unchanged at the
positions that keep word edges.

Read by gen_vault_corpus.py (load_parts): add(g) calls g.case(...); finish() of corpus_fields.py and
corpus_23rd_outside_review_fields.py runs every case of the system position at the field positions as well. Ids:
"product-<seed>-<variant>-<layers>" (a variant's "kind:name" is written "kind.name").

The fuzzer of gate 3 (gen_vault_fuzz.py) uses the same seeds and steps at depths one to four in mixed orders.
"""
from __future__ import annotations

import importlib.util
import os

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("vault_steps_for_product", os.path.join(_here, "vault_steps.py"))
steps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(steps)

PREFIX = "Café note: "
SUFFIX = ", danke schön."
POSITIONS = ["user", "system", "tool-text", "tool-data", "block-data", "assistant-unrecorded", "tool-input-unrecorded"]
# positions that keep word edges (model text without a record is searched loosely, inside words too)
KEEP_POSITIONS = ["user", "system", "tool-text", "tool-data", "block-data"]


def variants(label, held):
    """[(variant name, Doc)] of one seed: as written, and every human variant that applies to it."""
    base = steps.Doc([PREFIX, held, SUFFIX], [steps.Value(label, held)])
    out = [("as-written", base)]
    for name, human in steps.HUMAN.items():
        d = human.apply(base, 0)
        if d is not None:
            out.append((name, d))
    return out


def chains():
    """Every chain of zero to two named settings (every ordered pair, a setting twice included)."""
    names = steps.PRODUCT_LAYERS
    return [[]] + [[a] for a in names] + [[a, b] for a in names for b in names]


def build(doc, label, held, chain, kept=False):
    """(text, exact text, written span) of one case. kept: the written value is not the known value and stays."""
    for name in chain:
        doc = steps.LAYERS[name].apply(doc)
    mark = doc.written(0) if kept else steps.placeholders([(label, held)])[0]
    return doc.text(), doc.expect([mark]), doc.written(0)


def data_of(text):
    """What a data position receives: model_dump(mode="json") of a pydantic model with the text as a field and as the
    name of an extra field."""
    return {"note": text, "id": 7, text: "key"}


def add(g):
    count = 0
    for sid, label, held in steps.SEEDS:
        for vname, doc in variants(label, held):
            for chain in chains():
                text, exact, span = build(doc, label, held, chain)
                layers = "+".join(chain) or "none"
                c = g.case("product-%s-%s-%s" % (sid, vname.replace(":", "."), layers), "gate 2 product", text,
                           known=[(label, held)], where=POSITIONS, data=data_of(text), leak=[span],
                           extra=[held] if held != span else [])
                c["exact"] = exact
                count += 1
    for sid, label, held, written in steps.KEEP_SEEDS:
        doc = steps.Doc([PREFIX, written, SUFFIX], [steps.Value(label, held)])
        for chain in chains():
            text, exact, span = build(doc, label, held, chain, kept=True)
            assert text == exact
            c = g.case("product-%s-%s" % (sid, "+".join(chain) or "none"), "gate 2 product (kept)", text,
                       known=[(label, held)], where=KEEP_POSITIONS, data=data_of(text), keep=[written])
            c["exact"] = exact
            count += 1
    return count
