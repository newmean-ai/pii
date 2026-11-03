# Table mode (Redactor.detect_table / redact_table) with the local nano copy (skips without it), and span-for-span parity
# with the npm package's detectTable through native/core/tests/js_table.mjs (skips without node or js/dist).
import csv
import io
import json
import os
import random
import shutil
import subprocess

import pytest

import newmean_pii
from newmean_pii import TABLE_CONTEXTS, TABLE_DELIMITERS, Redactor, Span
from newmean_pii import _core

from conftest import REPO, has_model, model_dir

NAMES = ["Maria Lopez", "Kim Minsu", "Jean Dupont", "Anna Kowalska", "Ahmed Ali", "Sofia Rossi", "Lars Nilsen", "Priya Sharma",
         "Chen Wei", "José García", "이서연", "Olga Petrova"]
CITIES = ["Seoul", "Paris", "Warsaw", "Cairo", "Rome", "Oslo", "Mumbai", "Beijing"]
COMMENTS = ["The checkout page was slow on my phone last week", "Great service, will come back again soon",
            "Delivery took three days longer than promised", "I liked the new menu but the prices went up a lot",
            "Staff were friendly and helpful during my visit", "Please add more vegetarian options to the lunch set"]

SURVEY = "id,name,email,phone,comment\n" + "".join(
    f"{i + 1},{n},{n.split()[0].lower()}{i}@example.com,010-{1000 + i}-{2000 + i},\"{COMMENTS[i % len(COMMENTS)]}\"\n"
    for i, n in enumerate(NAMES[:10]))
PROSE = "Hello, my name is Maria Lopez.\nI live in Seoul; call me at 010-1234-5678."


def cells_csv(text, delim):
    """[start, end) value ranges by row (header row 0) of a CSV whose quoted fields hold no escaped quotes or line breaks."""
    out, pos = [], 0
    for line in text.split("\n"):
        if line:
            row, s, quoted = [], pos, False
            for i, ch in enumerate(line + delim):
                if ch == '"':
                    quoted = not quoted
                elif ch == delim and not quoted:
                    a, b = s, pos + i
                    row.append((a + 1, b - 1) if text[a] == '"' else (a, b))
                    s = pos + i + 1
            out.append(row)
        pos += len(line) + 1
    return out


def inside_one_cell(spans, cells):
    flat = [c for row in cells for c in row]
    return all(any(a <= s.start and s.end <= b for a, b in flat) for s in spans)


def test_constants():
    assert _core.SOURCE_TABLE == 4
    assert TABLE_DELIMITERS == {"auto": 0, ",": 44, "\t": 9, ";": 59, "markdown": 124}
    assert "TABLE_DELIMITERS" in newmean_pii.__all__


def test_survey_columns(nano):
    spans = nano.detect_table(SURVEY)
    cells = cells_csv(SURVEY, ",")
    assert [len(r) for r in cells] == [5] * 11
    assert spans and all(isinstance(s, Span) for s in spans)
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
    assert inside_one_cell(spans, cells)
    got = {(SURVEY[s.start : s.end], s.label) for s in spans}
    for i, n in enumerate(NAMES[:10]):
        assert (f"{n.split()[0].lower()}{i}@example.com", "EMAIL") in got
        assert (f"010-{1000 + i}-{2000 + i}", "PHONE") in got
    by_col = {}
    for s in spans:
        for r, row in enumerate(cells):
            for c, (a, b) in enumerate(row):
                if a <= s.start < b:
                    by_col.setdefault(c, []).append((r, s))
    assert all(r > 0 for c in by_col for r, s in by_col[c] if s.source == "table")  # never the header row
    assert all(s.source != "table" for r, s in by_col.get(4, []))  # the free-text column never votes
    assert all(s.source != "table" for r, s in by_col.get(0, []))  # ids are not PII


def test_vote_off_and_header_off(nano):
    off = nano.detect_table(SURVEY, vote=False)
    assert all(s.source != "table" for s in off)
    assert off and inside_one_cell(off, cells_csv(SURVEY, ","))
    on = nano.detect_table(SURVEY, header=False)  # the header row joins the vote; "name" etc. are not PII-like
    assert any(s.source == "table" for s in on)


def test_delimiters(nano):
    tsv = SURVEY.replace(",", "\t").replace('"', "")
    semi = SURVEY.replace(",", ";").replace('"', "")
    md = "| id | name | phone |\n|---|:--:|---|\n" + "".join(f"| {i} | {n} | 010-{3000 + i}-{4000 + i} |\n" for i, n in enumerate(NAMES[:10]))
    for text, delim in ((tsv, "\t"), (semi, ";"), (md, "markdown")):
        auto = nano.detect_table(text)
        assert auto == nano.detect_table(text, delim), delim
        assert any(s.source == "table" for s in auto), delim
        assert all("\n" not in text[s.start : s.end] and "|" not in text[s.start : s.end] for s in auto)
    # a wrong explicit delimiter: one field per record, so no column votes
    assert all(s.source != "table" for s in nano.detect_table(SURVEY, "\t"))


def test_not_a_table_gives_detect(nano):
    for t in (PROSE, "Call Maria Lopez at 010-1234-5678.", "", "👩‍👩‍👧 Contact Maria Lopez 🇰🇷 at maria@example.com"):
        assert nano.detect_table(t) == nano.detect(t), t


def test_label_filter():
    if not has_model("nano", "q4"):
        pytest.skip("no model copy")
    r = Redactor("nano", "q4", model_dir=model_dir("nano"), labels=["EMAIL"])
    spans = r.detect_table(SURVEY)
    assert spans and {s.label for s in spans} == {"EMAIL"}


def test_redact_table_keeps_the_table(nano):
    for mode in ("tag", "mask", "blank"):
        out = nano.redact_table(SURVEY, mode)
        assert out.spans == nano.detect_table(SURVEY)
        before, after = list(csv.reader(io.StringIO(SURVEY))), list(csv.reader(io.StringIO(out.text)))
        assert [len(r) for r in after] == [len(r) for r in before] and after[0] == before[0]
        assert "example.com" not in out.text
    out = nano.redact_table(SURVEY, replace=lambda s, o: s.label.lower())
    assert out.text.count("email") >= 10
    assert nano.redact_table(SURVEY, delimiter=",", vote=False).spans == nano.detect_table(SURVEY, ",", vote=False)


def test_arguments(nano):
    with pytest.raises(ValueError, match="unknown delimiter"):
        nano.detect_table(SURVEY, "|")
    with pytest.raises(ValueError, match="unknown mode"):
        nano.redact_table(SURVEY, "hash")
    with pytest.raises(TypeError):
        nano.detect_table(SURVEY.encode())
    with pytest.raises(ValueError):  # PII_ERR_ARG from the core
        nano._core_model().detect_table(SURVEY, 0.0, _core.LABELS_ALL, None, ord("x"))


def test_grapheme_snapping(nano):
    """Spans start and end on grapheme cluster boundaries (no cut inside a ZWJ sequence, flag, skin tone or keycap)."""
    text = "name,note\n" + "".join(f"{n} 👩‍👩‍👧,🇰🇷 {n.split()[0].lower()}@example.com 👍🏽 1️⃣\n" for n in NAMES[:8])
    joiners = {"‍", "️", "⃣", "\U0001f3fd"}
    for spans in (nano.detect_table(text), nano.detect(text)):
        for s in spans:
            assert text[s.start] not in joiners and (s.end == len(text) or text[s.end] not in joiners), s
            flags = sum(1 for ch in text[: s.start] if 0x1F1E6 <= ord(ch) <= 0x1F1FF)
            assert flags % 2 == 0 or not (0x1F1E6 <= ord(text[s.start]) <= 0x1F1FF), s


def gen_table(rng, k):
    kinds = ["id", "name"] + rng.sample(["email", "phone", "city", "comment", "user"], rng.randint(2, 4))
    rng.shuffle(kinds)
    delim = [",", "\t", ";", "markdown"][k % 4]
    nl = "\r\n" if k % 5 == 2 else "\n"
    rows = [kinds[:]]
    for i in range(rng.randint(4, 12)):
        name = rng.choice(NAMES)
        user = name.split()[0].lower() + str(rng.randint(1, 99))
        vals = {"id": str(i + 1), "name": name, "email": f"{user}@example.com", "phone": f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
                "city": rng.choice(CITIES), "comment": rng.choice(COMMENTS), "user": user}
        row = [vals[kd] for kd in kinds]
        if rng.random() < 0.1:
            row[kinds.index("name")] = rng.choice(["N/A", "-", "", "없음"])
        rows.append(row)
    if delim == "markdown":
        lines = ["| " + " | ".join(r) + " |" for r in rows]
        lines.insert(1, "|" + "---|" * len(kinds))
    else:
        q = lambda v: f'"{v}"' if (delim in v or rng.random() < 0.15) else v
        lines = [delim.join(q(v) for v in r) for r in rows]
    opts = {"delimiter": "auto" if k % 3 else delim, "header": k % 7 != 3}
    if k % 11 == 5:
        opts["vote"] = False
    return {"text": nl.join(lines) + nl, **opts}


def js_spans(rows, labels=None):
    node, dist = shutil.which("node"), REPO / "js" / "dist"
    script = REPO / "native" / "core" / "tests" / "js_table.mjs"
    if os.environ.get("PII_JS") == "0" or not node or not (dist / "index.js").is_file() or not script.is_file():
        pytest.skip("node, js/dist or js_table.mjs absent (cd js && npm run build)")
    payload = "".join(json.dumps({**r, **({"labels": labels} if labels else {})}) + "\n" for r in rows)
    p = subprocess.run([node, str(script), str(dist), str(model_dir("nano")), "nano", "q4", "0", "0"], input=payload.encode(),
                       capture_output=True)
    assert p.returncode == 0, p.stderr.decode()[-600:]
    return [json.loads(l)["spans"] for l in p.stdout.decode().splitlines()]


def utf16_to_cp(text):
    m, u = {}, 0
    for i, ch in enumerate(text):
        m[u] = i
        u += 2 if ord(ch) > 0xFFFF else 1
    m[u] = len(text)
    return m


def test_parity_with_npm():
    """Same spans, labels and sources as the npm package's detectTable (regex layer on, as js_table.mjs runs it)."""
    if not has_model("nano", "q4"):
        pytest.skip("no model copy")
    rng = random.Random(20261101)
    rows = [{"text": SURVEY}, {"text": PROSE}, {"text": SURVEY, "header": False}, {"text": SURVEY, "vote": False},
            {"text": SURVEY, "delimiter": "\t"}, {"text": 'name,note\n"Lopez, Maria","said ""call me"" at 010-1234-5678"\n"Kim\nMinsu",x\n'},
            {"text": "name,emoji\nMaria Lopez 👩‍👩‍👧,🇰🇷\nJosé García,👍🏽 1️⃣\n"}]
    rows += [gen_table(rng, k) for k in range(24)]
    sources = {1: "model", 2: "regex", 3: "model+regex", 4: "table"}
    r = Redactor("nano", "q4", model_dir=model_dir("nano"), regex=True, whole_words=False)  # js_table.mjs: wholeWords off
    for labels in (None, ["EMAIL", "PHONE"]):
        rr = r if labels is None else Redactor("nano", "q4", model_dir=model_dir("nano"), regex=True, labels=labels, whole_words=False)
        want_all = js_spans(rows, labels)
        same = 0
        for row, want in zip(rows, want_all):
            m = utf16_to_cp(row["text"])
            want = [Span(m[a], m[b], l, sources[src]) for a, b, l, src in want]
            got = rr.detect_table(row["text"], row.get("delimiter", "auto"), row.get("header", True), row.get("vote", True))
            assert got == want, row["text"][:80]
            same += 1
        assert same == len(rows)
        assert any(s.source == "table" for row in rows for s in rr.detect_table(row["text"], row.get("delimiter", "auto")))


def masked(spans):
    return {i for s in spans for i in range(s.start, s.end)}


def test_header_context(nano):
    text = "name,email\n" + "".join(f"{n},{n.split()[0].lower()}{i}@example.com\n" for i, n in enumerate(NAMES[:6]))
    base = nano.detect_table(text)
    ctx = nano.detect_table(text, context="header")
    assert ctx and inside_one_cell(ctx, cells_csv(text, ","))
    assert all(a.end <= b.start for a, b in zip(ctx, ctx[1:]))
    assert masked(base) <= masked(ctx)
    assert nano.detect_table(text, context="none") == base
    assert nano.redact_table(text, context="header").spans == ctx
    assert TABLE_CONTEXTS == {"none": 0, "header": 1}
    with pytest.raises(ValueError, match="unknown context"):
        nano.detect_table(text, context="row")
    with pytest.raises(ValueError):  # PII_ERR_ARG from the core
        nano._core_model().detect_table(text, 0.0, _core.LABELS_ALL, None, 0, True, True, 2)
