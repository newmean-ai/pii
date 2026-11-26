"""Writes vault_fuzz.json, the fixed-seed fuzz cases of gate 3 of the 1.7.1 release standard, next
to this file. Two runners read it: python/tests/test_vault_fuzz.py (the Python LangChain middleware) and
js/test/vault-fuzz.test.ts (the AI SDK and LangChain.js middlewares).

    python3 python/tests/gen_vault_fuzz.py

Standard library only, deterministic: case i is drawn from random.Random("<SEED>/<i>/<attempt>") (a string seed, which
Python hashes the same way on every version), and a case that repeats an earlier one is drawn again. Each case:

- one to three known values (vault_steps.SEEDS and EXTRA_SEEDS: every label, several scripts), each written once in a
  text whose neighbors are drawn from fragments with characters the layers escape, unrelated escapes, `%` and `+`
  (the class of G1), several scripts and Korean particles after a Hangul value;
- a chain of one to four in-scope steps in a mixed order (vault_steps): human steps (case, NFD, full-width, white space,
  line breaks, dates: sections 2.1 and 10 of the guarantee note; the README section "How a known value is found again")
  on one value, before or after a layer, and layers (serializer settings: the named settings of the product and
  json-slash, json-upper-hex, py-set, quote-lower) on the whole text. A step that changes nothing is not taken;
- "exact": the text with each value's written span replaced by its placeholder and every neighbor unchanged. A JSON
  document may be written again as JSON writes it (section 5), so the runners compare JSON documents by what they hold.

The detector is off (a detector that finds nothing): only the vault's own search runs. Every case runs at every
position of POSITIONS; at a data position the data is data_of(text) and the expected data the same with the text
replaced by "exact", as a value and as a key.

Groups (decision D1 of the release standard):

- "two-layer": every human step comes before the first layer, each value has at most one, and there are at most two
  layers: the shape of the finite product of gate 2 (corpus_product.py), which the release guarantees. A failure
  here blocks the release;
- "beyond": every other chain (three or four steps that are not of that shape, a human step after a layer, two human
  steps on one value). Best effort: a failure is listed in vault_fuzz_open.json with its disposition (gate 1).

The generator rejects a draw that the decided rules leave unmasked on purpose, so that every case expects masking: a
number value joined in the text as written to a number before or after it (a dot between digits, "<digit>e+"), and a
neighbor that holds a value (in a rough fold of case, width and NFD) or text that looks like a placeholder.

Format (keep exactly; both runners read it):

    {"version": 1, "seed": SEED, "positions": [...], "data": "...", "counts": {...},
     "cases": [{"id", "seed", "group", "depth", "steps": [[name, value index or null]], "known": [{"label", "value"}],
                "text", "exact", "spans", "leak"}]}

- known: put into a fresh vault with vault.tag(label, value), in this order; the placeholders are numbered in it.
- spans: each value's written span in "text" (the source span the placeholder replaces).
- leak: strings that must not appear in what the provider receives (the written spans and the values as held).
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("vault_steps_for_fuzz", os.path.join(HERE, "vault_steps.py"))
steps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(steps)

BS = steps.BS
NL = steps.NL
TAB = steps.TAB
QUOTE = steps.QUOTE

SEED = "newmean-pii vault fuzz 1.7.1"
COUNT = 2400
POSITIONS = ["user", "system", "tool-text", "tool-data", "block-data", "assistant-unrecorded", "tool-input-unrecorded"]
DATA = 'data_of(text) = {"note": text, "id": 7, text: "key"}'

VALUES = steps.SEEDS + steps.EXTRA_SEEDS

# neighbors: text before the first value, between two values and after the last; then what stands right next to a
# value (white space or punctuation, so that the value is at word edges in every script)
STARTS = [
    "Café note:", "Re: order 17 for", "東京オフィス 担当", "Kunde", "고객 정보", "Note", "C:" + BS + "tmp" + BS + "new has",
    "see q=a+b&x=1 for", "50% of", "Ünïcödé text with", "Line one" + NL + "line two", "Tab" + TAB + "sep",
    "price " + QUOTE + "low" + QUOTE + " for", "It's for", "x < y & z > w;", "emoji 😀 for", "Ελληνικά για", "Привет для",
    "مرحبا", "path /a" + BS + "/b and", "%41%42 stays", "a" + BS + "u0041 stays", "", "Dear team,", "ส่งถึง",
]
MIDDLES = [
    "and", "then", "ou", "그리고", "および", "y también", BS + "n is not a line", "%41 stays", "a+b", "or" + NL + "else",
    "und", "—", "|", "또는", "或", "plus 100%", "{", "}", "x=1&y=2", "ok",
]
ENDS = [
    "danke schön.", "ok", "end", "끝.", "以上です。", "thanks!", "(ref 7)", "", "see C:" + BS + "tmp", "q=a+b",
    "50% done", "a" + BS + "/b", "Ende" + NL, "über", "конец",
]
BEFORE = [" ", ": ", " (", NL, TAB, " " + QUOTE, " «", " [", " <", ", "]
AFTER = [" ", ", ", ") ", NL, TAB, QUOTE + " ", " »", "; ", "] ", "> "]
PARTICLES = ["는 ", "님께 ", "에게 ", "의 ", "은 "]

LAYER_NAMES = list(steps.FUZZ_LAYERS)
HUMAN_NAMES = list(steps.HUMAN)
HUMAN_WEIGHT = {"full-width": 0.35}


def fold(s):
    return steps.fold(s).replace(steps.IDEO, " ")


def hangul(c):
    return 0xAC00 <= ord(c) <= 0xD7A3


def is_number_char(c):
    return unicodedata.category(c).startswith("N")


def is_number_value(v):
    """A value written as a number (the vault's exponent join applies only to these)."""
    t = v[1:] if v[:1] in "+-" else v
    return bool(t) and all(c.isdigit() or c in ".eE+-" for c in t) and t[0].isdigit()


def joined_to_number(text, start, end, held):
    """Whether the decided number rule leaves the span unmasked in the text as written (a dot between two digits
    before or after it, or the sign of an exponent before a number)."""
    first, last = text[start:start + 1], text[end - 1:end]
    before = text[start - 1:start] if start > 0 else ""
    second = text[start - 2:start - 1] if start > 1 else ""
    third = text[start - 3:start - 2] if start > 2 else ""
    after = text[end:end + 1]
    after2 = text[end + 1:end + 2]
    if first and is_number_char(first) and before == "." and second and is_number_char(second):
        return True
    if is_number_value(held) and before in ("+", "-") and second in ("e", "E") and third and is_number_char(third):
        return True
    if last and is_number_char(last) and after == "." and after2 and is_number_char(after2):
        return True
    return False


def placeholder_like(s):
    i = s.find("[")
    while i >= 0:
        j = i + 1
        while j < len(s) and (s[j].isupper() or s[j] == "_" or s[j].isdigit()):
            j += 1
        if j > i + 1 and j < len(s) and s[j] == "]":
            return True
        i = s.find("[", i + 1)
    return False


def draw(rng):
    """One case drawn with rng, or None when the draw is rejected."""
    k = rng.choices([1, 2, 3], weights=[60, 32, 8])[0]
    picked = []
    for sid, label, held in rng.sample(VALUES, len(VALUES)):
        f = fold(held)
        if any(f in fold(h) or fold(h) in f for _s, _l, h in picked):
            continue
        picked.append((sid, label, held))
        if len(picked) == k:
            break
    values = [steps.Value(label, held) for _sid, label, held in picked]

    segs = [rng.choice(STARTS)]
    for i, v in enumerate(values):
        segs[-1] = segs[-1] + rng.choice(BEFORE) if segs[-1] else segs[-1]
        segs.append(v.held)
        after = rng.choice(PARTICLES) if hangul(v.held[-1]) and rng.random() < 0.3 else rng.choice(AFTER)
        tail = rng.choice(MIDDLES) if i + 1 < len(values) else rng.choice(ENDS)
        segs.append(after + tail)
    # (no neighbor may hold a value, nor look like a placeholder)
    whole = fold("".join(segs[0::2]))
    for v in values:
        if fold(v.held) in whole:
            return None
    if placeholder_like("".join(segs[0::2])):
        return None
    doc = steps.Doc(segs, values)

    depth = rng.choices([1, 2, 3, 4], weights=[15, 25, 30, 30])[0]
    chain = []
    while len(chain) < depth:
        took = False
        if rng.random() < 0.45:
            i = rng.randrange(len(values))
            # a weighted order of the human steps: full width applies to nearly every written form, so it is drawn less
            names = sorted(HUMAN_NAMES, key=lambda n: -(rng.random() ** (1.0 / HUMAN_WEIGHT.get(n, 1.0))))
            for name in names:
                if name == "full-width" and doc.layered and rng.random() > HUMAN_WEIGHT[name]:
                    continue
                d = steps.HUMAN[name].apply(doc, i)
                if d is not None:
                    doc, took = d, True
                    chain.append([name, i])
                    break
        if not took:
            name = rng.choice(LAYER_NAMES)
            d = steps.LAYERS[name].apply(doc)
            if d.text() == doc.text():
                continue
            doc = d
            chain.append([name, None])

    text = doc.text()
    marks = steps.placeholders([(v.label, v.held) for v in values])
    spans = [doc.written(i) for i in range(len(values))]
    # where each span stands in the text, and the decided number rule
    at = 0
    for j, seg in enumerate(doc.segs):
        if j % 2:
            i = j // 2
            if joined_to_number(text, at, at + len(seg), values[i].held):
                return None
        at += len(seg)
    # (a value written in a way that also spells another value would make two expectations)
    if len(set(fold(s) for s in spans)) != len(spans):
        return None
    leak = []
    for i, v in enumerate(values):
        for s in (spans[i], v.held):
            if s not in leak:
                leak.append(s)
    exact = doc.expect(marks)
    for s in leak:
        if s in exact:
            return None
    return {
        "group": group_of(chain),
        "depth": len(chain),
        "steps": chain,
        "known": [{"label": v.label, "value": v.held} for v in values],
        "text": text,
        "exact": exact,
        "spans": spans,
        "leak": leak,
    }


def group_of(chain):
    """"two-layer" when the chain has the shape of the gate 2 product (each value's human step, at most one, before
    the first layer; at most two layers), else "beyond" (decision D1)."""
    layers = 0
    humans = {}
    for name, i in chain:
        if i is None:
            layers += 1
        else:
            if layers > 0:
                return "beyond"
            humans[i] = humans.get(i, 0) + 1
    if layers > 2 or any(n > 1 for n in humans.values()):
        return "beyond"
    return "two-layer"


def escape_breaks(line):
    """JSON text with the characters other tools take for line breaks (NEL, U+2028, U+2029) written as Unicode escapes:
    they stand only inside strings, so the JSON means the same, and the file keeps one case per line."""
    for c in (chr(0x85), chr(0x2028), chr(0x2029)):
        line = line.replace(c, BS + "u%04x" % ord(c))
    return line


def main():
    cases, seen = [], set()
    i = 0
    while len(cases) < COUNT:
        i += 1
        attempt = 0
        while True:
            seed = "%s/%d/%d" % (SEED, i, attempt)
            c = draw(random.Random(seed))
            attempt += 1
            if c is None:
                continue
            key = json.dumps([c["known"], c["text"]], ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            break
        cases.append(dict({"id": "fuzz-%04d" % len(cases), "seed": seed}, **c))
    counts = {"cases": len(cases)}
    for c in cases:
        for key in ("group:" + c["group"], "depth:" + str(c["depth"]), "values:" + str(len(c["known"]))):
            counts[key] = counts.get(key, 0) + 1
        for name, _i in c["steps"]:
            counts["step:" + name] = counts.get("step:" + name, 0) + 1
    counts = dict(sorted(counts.items()))
    for name in LAYER_NAMES + HUMAN_NAMES:
        if "step:" + name not in counts:
            sys.stderr.write("no case takes the step " + name + NL)
            sys.exit(1)
    out = {"version": 1, "seed": SEED, "positions": POSITIONS, "data": DATA, "counts": counts, "cases": cases}
    path = os.path.join(HERE, "vault_fuzz.json")
    # one case per line keeps the file readable and its diffs small
    with open(path, "w", encoding="utf-8") as f:
        head = {k: v for k, v in out.items() if k != "cases"}
        f.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "cases": [' + NL)
        for j, c in enumerate(cases):
            f.write(escape_breaks(json.dumps(c, ensure_ascii=False)) + ("," if j + 1 < len(cases) else "") + NL)
        f.write("]}" + NL)
    print("wrote", path, len(cases), "cases", {k: v for k, v in counts.items() if not k.startswith("step:")})


if __name__ == "__main__":
    main()
