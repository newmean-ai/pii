#!/usr/bin/env python3
"""Parity tests of the native core against the npm package's fixtures (Python standard library only).

  (a) tokenizer: ids equal to Hugging Face tokenizers on js/test/fixtures/tokenize.jsonl (100% required); offsets equal
      to the npm package's Tokenizer (node + js/dist) and to Hugging Face but for at most 3 rows, as js/test/tokenizer.test.ts
      allows (a grapheme the normalizer replaces aligns to the whole grapheme, which differs on a few combining sequences)
  (b) predictions: spans equal to the Python evaluator for every run of js/test/predict.test.ts (99% of rows or more)
  (c) the same on a subset under AddressSanitizer + UndefinedBehaviorSanitizer (build/asan/pii_cli)
  (d) several threads sharing one model give the results of one thread
  (e) model files match the pinned sizes and SHA-256
  (f) API self-test (combine cases of js/test/combine.test.ts, arguments, malformed and truncated model files)
  (g) spans identical to the npm package's WebAssembly engine (node + js/dist; skipped without them)
  (h) offsets: UTF-8, code point and UTF-16 offsets agree with Python on hard texts (emoji, ZWJ, combining marks, RTL,
      CRLF, NUL, lone surrogates, invalid UTF-8, empty and blank input, 10,000+ tokens); spans sorted and inside the text
  (j) table mode (pii_detect_table): hand-written and 300 generated CSV/TSV/semicolon/markdown tables (names, emails, phones,
      IDs, Likert answers, free-text comments; quoted fields with delimiters, escaped quotes and line breaks; CRLF; blank
      records; emoji next to values; header, vote, delimiter and label options varied); spans inside one cell value, no
      propagation into free-text columns or header rows, vote off and label filter respected, non-tables equal to detect;
      spans, labels and sources identical to the npm package's detectTable (node + js/dist, js_table.mjs, with its regex
      spans passed as pii_options.extra; skipped without them). Also without a model: the table steps and grapheme snapping
      (pii_cli apply vs js_table.mjs apply, detectTableSpans of js/src/table.ts) on made-up spans over the same
      inputs and over grapheme-heavy texts (ZWJ, flags, tags, keycaps, skin tones, combining marks, Indic conjuncts, jamo)
  (i) fuzz (only with --only fuzz): mutated model and tokenizer files and random bytes under ASan + UBSan if built

Fixtures and model copies are private (validation text) and never copied here: they are read from PII_FIXTURES
(default ../../js/test/fixtures) and PII_MODELS (default ../../js/test/models); every test skips when they are absent.
PII_SIZES=nano,medium limits the sizes; PII_CLI / PII_ASAN_CLI override the binaries; PII_JS_DIST points at the built
npm package (default ../../js/dist), PII_JS=0 skips (g). --only pins,selftest,tokenizer,offsets,predict,js,table,threads,asan
picks tests (fuzz runs only when named; PII_FUZZ=N sets its size, default 100); --bench also measures speed. A PII_FMA build (make FMA=1) is compared with the relaxed-SIMD wasm build in (g).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE.parent
REPO = CORE.parent.parent
FIX = pathlib.Path(os.environ.get("PII_FIXTURES", REPO / "js" / "test" / "fixtures"))
MODELS = pathlib.Path(os.environ.get("PII_MODELS", REPO / "js" / "test" / "models"))
CLI = pathlib.Path(os.environ.get("PII_CLI", CORE / "build" / "pii_cli"))
ASAN_CLI = pathlib.Path(os.environ.get("PII_ASAN_CLI", CORE / "build" / "asan" / "pii_cli"))
SIZES = os.environ["PII_SIZES"].split(",") if os.environ.get("PII_SIZES") else None
# the runs of js/test/predict.test.ts: (size, variant, Python O bias)
RUNS = [
    ("large", "q4e", 0), ("large", "q4", 0), ("medium", "q4e", 0), ("medium", "q4", 0),
    ("small", "q4", 0), ("small", "q4e", 0), ("nano", "q4", 0), ("nano", "q4e", 0),
    ("medium", "q4e", -0.5), ("nano", "q4", 1),
]
THRESHOLD = 0.99
JS_DIST = pathlib.Path(os.environ.get("PII_JS_DIST", REPO / "js" / "dist"))
# runs compared with the WebAssembly engine (both biases included); all runs with PII_JS=all
JS_RUNS = [("nano", "q4", 0), ("medium", "q4e", 0), ("medium", "q4e", -0.5), ("nano", "q4", 1)]

failures = []
summary = []  # (test, result) lines printed at the end
c_spans = {}  # (size, variant, bias) -> spans per row from test_predict, reused by test_js


def skip(msg):
    print(f"  skip: {msg}")


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'} {msg}")
    if not ok:
        failures.append(msg)


def model_paths(size, variant):
    d = MODELS / f"pii-{size}"
    return d / "onnx" / f"model_{variant}.onnx", d / "tokenizer.web.json"


def run_cli(cli, args, texts):
    payload = "".join(json.dumps({"text": t}) + "\n" for t in texts)
    p = subprocess.run([str(cli), *map(str, args)], input=payload.encode(), capture_output=True)
    if p.returncode:
        raise RuntimeError(f"{cli.name} {args[0]} failed ({p.returncode}): {p.stderr.decode()[-2000:]}")
    return [json.loads(l) for l in p.stdout.decode().splitlines()]


def units(text):
    """Code point index -> (UTF-16 offset, UTF-8 offset)."""
    u16, u8 = [0], [0]
    for ch in text:
        u16.append(u16[-1] + (2 if ord(ch) > 0xFFFF else 1))
        u8.append(u8[-1] + len(ch.encode("utf-8", "surrogatepass")))
    return u16, u8


def read_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]  # not splitlines(): texts hold U+2028 etc.


def first_model():
    for size in ("nano", "small", "medium", "large"):
        onnx, tok = model_paths(size, "q4")
        if onnx.exists() and tok.exists() and (not SIZES or size in SIZES):
            return size, onnx, tok
    return None


def js_tokenize(tok, texts):
    """The npm package's Tokenizer on texts ({"ids", "offsets"} per row, UTF-16 offsets), or None without node or js/dist."""
    node = shutil.which("node")
    if os.environ.get("PII_JS") == "0" or not node or not (JS_DIST / "index.js").exists():
        return None
    payload = "".join(json.dumps({"text": t}) + "\n" for t in texts)
    p = subprocess.run([node, str(HERE / "js_tokenize.mjs"), str(JS_DIST), str(tok)], input=payload.encode(), capture_output=True)
    if p.returncode:
        check(False, f"node tokenizer failed: {p.stderr.decode()[-1000:]}")
        return None
    return [json.loads(l) for l in p.stdout.decode().splitlines()]


def test_tokenizer(cli, limit=None, label="tokenizer"):
    print(f"[{label}] parity with Hugging Face tokenizers and the npm package's Tokenizer")
    fix = FIX / "tokenize.jsonl"
    m = first_model()
    if not fix.exists() or not m:
        return skip("tokenize.jsonl or a model is absent")
    rows = read_jsonl(fix)[:limit]
    t0 = time.time()
    got = run_cli(cli, ["tokenize", m[1], m[2]], [r["text"] for r in rows])
    ms = (time.time() - t0) * 1e3
    js = js_tokenize(m[2], [r["text"] for r in rows])
    bad_ids = bad_off = bad_units = bad_js = 0
    shown = 0
    for i, (r, g) in enumerate(zip(rows, got)):
        u16, u8 = units(r["text"])
        ids_ok = g["ids"] == r["ids"]
        off_ok = g["cp"] == [list(o) for o in r["offsets"]]  # fixture offsets count code points
        bad_ids += not ids_ok
        bad_off += not off_ok
        bad_units += g["u16"] != [[u16[s], u16[e]] for s, e in g["cp"]] or g["u8"] != [[u8[s], u8[e]] for s, e in g["cp"]]
        js_ok = js is None or (js[i]["ids"] == g["ids"] and js[i]["offsets"] == g["u16"])
        bad_js += not js_ok
        if (not ids_ok or not off_ok or not js_ok) and shown < 5:
            shown += 1
            print(f"    {json.dumps(r['text'])[:100]}\n      hf ids {r['ids'][:16]} {r['offsets'][:6]}\n      c  ids {g['ids'][:16]} {g['cp'][:6]}")
            if js is not None:
                print(f"      npm (UTF-16) {js[i]['offsets'][:6]}, c (UTF-16) {g['u16'][:6]}")
    n = len(rows)
    if limit is None:
        summary.append(("tokenizer", f"ids {n - bad_ids}/{n}, offsets {n - bad_off}/{n} identical to Hugging Face"
                        + (f", {n - bad_js}/{n} to npm" if js is not None else "")))
    check(len(got) == n, f"{len(got)}/{n} rows answered ({ms:.0f} ms)")
    check(bad_ids == 0, f"ids: {n - bad_ids}/{n} rows identical")
    check(bad_off <= 3, f"offsets: {n - bad_off}/{n} rows identical to Hugging Face (at most 3 may differ)")
    if js is None:
        skip("offsets vs npm: node or js/dist absent (cd js && npm run build)")
    else:
        check(bad_js == 0, f"ids and offsets: {n - bad_js}/{n} rows identical to the npm package's Tokenizer")
    check(bad_units == 0, f"UTF-16 and UTF-8 offsets consistent with code points: {n - bad_units}/{n}")


def predict_runs():
    for size, variant, bias in RUNS:
        if SIZES and size not in SIZES:
            continue
        fix = FIX / f"predict-{size}-{variant}{f'-b{bias:g}' if bias else ''}.jsonl"
        onnx, tok = model_paths(size, variant)
        yield size, variant, bias, fix, onnx, tok


def test_predict(cli, limit=None, label="predict", results=None):
    print(f"[{label}] spans equal to the Python evaluator (threshold {THRESHOLD:.0%})")
    for size, variant, bias, fix, onnx, tok in predict_runs():
        name = f"{size} {variant} bias {bias:g}"
        if not fix.exists() or not onnx.exists() or not tok.exists():
            skip(f"{name}: fixture or model absent")
            continue
        rows = read_jsonl(fix)[:limit]
        t0 = time.time()
        got = run_cli(cli, ["detect", onnx, tok, -bias], [r["text"] for r in rows])  # sensitivity = -bias
        ms = (time.time() - t0) * 1e3
        same = bad_units = 0
        diffs = []
        for r, g in zip(rows, got):
            want = " ".join(f"{s['start']}-{s['end']}:{s['label']}" for s in r["spans"])
            have = " ".join(f"{s[0]}-{s[1]}:{s[2]}" for s in g["spans"])
            u16, u8 = units(r["text"])
            bad_units += any(s[3] != u16[s[0]] or s[4] != u16[s[1]] or s[5] != u8[s[0]] or s[6] != u8[s[1]] for s in g["spans"])
            if want == have:
                same += 1
            elif len(diffs) < 3:
                diffs.append(f"    {json.dumps(r['text'])[:60]}\n      py {want}\n      c  {have}")
        rate = same / len(rows)
        if limit is None and cli == CLI:
            c_spans[(size, variant, bias)] = got
            summary.append((f"predict {name}", f"{same}/{len(rows)} ({rate:.2%}) identical to Python, {ms / len(rows):.1f} ms/row"))
        if results is not None:
            results[name] = (same, len(rows), ms)
        check(rate >= THRESHOLD and len(got) == len(rows) and not bad_units,
              f"{name}: {same}/{len(rows)} rows identical ({rate:.2%}), {ms:.0f} ms" + (f", {bad_units} unit errors" if bad_units else ""))
        for d in diffs:
            print(d)


def offset_texts():
    """Hard texts for (h): (text, raw bytes or None). Raw rows carry invalid UTF-8 (one code point per bad byte)."""
    base = "Call Maria Lopez at +1 202 555 0143 or maria.lopez@example.com, 서울시 강남구 테헤란로 123 김민수 010-1234-5678."
    texts = [
        "", " ", "  \t\n  ", "\r\n\r\n", "\u3000\u00a0\u2028\u2029\ufeff\u0085",
        "😀" + base, base + " 👨‍👩‍👧‍👦 " + base, "🇰🇷🇺🇸 " + base, "𝓜𝓪𝓻𝓲𝓪 𝐋𝐨𝐩𝐞𝐳 " + base,
        "Jose\u0301 Garci\u0301a, jose\u0301@example.com; " + "e\u0301\u0301\u0301 " * 5 + base,
        "李小龍 住在 北京市 朝阳区 建国路 88号, 电话 138 0013 8000. 山田太郎 東京都千代田区1-1",
        "שלום, שמי דוד כהן, טלפון 050-1234567, david@example.com",
        "مرحبا، اسمي أحمد علي، هاتفي 0501234567 وبريدي ahmed@example.com",
        "\u202eabc\u202c " + base, "zero\u200dwidth\u200cjoin\u200b " + base,
        base.replace(" ", "\r\n"), "NUL\x00in\x00text " + base + "\x00",
        "ｆｕｌｌｗｉｄｔｈ　ＡＢＣ　０１２３ ﬁ ß Å K " + base, "<s> </s> <pad> <mask> <unk> " + base + " <mask>",
        "\ud800 lone surrogates " + base + " \udfff",
        "a" * 3000, "0123456789" * 300,
    ]
    long = "\n".join(f"{i}: {base} {texts[10]} {texts[9]}" for i in range(80))  # 10,000+ tokens, many windows
    rows = [(t, None) for t in texts + [long]]
    raw = b"\xff\xfe Kim \xc3 \xe2\x82a \xf4\x90\x80\x80 \xc0\xaf kim@example.com \xe0\x80\x80 Lee\x80 end\xf0\x9f\x98"
    rows.append((raw.decode("utf-8", "surrogateescape"), raw))
    return rows


def test_offsets(cli):
    print("[offsets] UTF-8, code point and UTF-16 offsets on hard texts")
    m = first_model()
    if not m:
        return skip("no model")
    rows = offset_texts()

    def line(t, raw):  # raw: invalid UTF-8 bytes inside a JSON string (no quote, backslash or newline in them)
        return json.dumps({"text": t}).encode() if raw is None else b'{"text": "' + raw + b'"}'

    def ref_units(t):  # surrogateescape characters stand for one invalid byte (one code point, one UTF-16 unit)
        u16, u8 = [0], [0]
        for ch in t:
            u16.append(u16[-1] + (2 if ord(ch) > 0xFFFF else 1))
            u8.append(u8[-1] + (1 if 0xDC80 <= ord(ch) <= 0xDCFF else len(ch.encode("utf-8", "surrogatepass"))))
        return u16, u8

    payload = b"".join(line(t, r) + b"\n" for t, r in rows)
    out = {}
    for cmd in ("tokenize", "detect"):
        p = subprocess.run([str(cli), cmd, m[1], m[2]], input=payload, capture_output=True)
        if p.returncode:
            return check(False, f"{cmd} failed: {p.stderr.decode()[-500:]}")
        out[cmd] = [json.loads(l) for l in p.stdout.decode().splitlines()]
    bad = []
    for (t, _), k, d in zip(rows, out["tokenize"], out["detect"]):
        u16, u8 = ref_units(t)
        n = len(t)
        ok = all(0 <= s <= e <= n and a == [u16[s], u16[e]] and b == [u8[s], u8[e]] for (s, e), a, b in zip(k["cp"], k["u16"], k["u8"]))
        prev = 0
        for cs, ce, _, a, b, x, y in d["spans"]:
            ok &= prev <= cs < ce <= n and [a, b, x, y] == [u16[cs], u16[ce], u8[cs], u8[ce]]
            prev = ce
        ok &= bool(t.strip(" \t\n\r\u3000\u00a0\u2028\u2029\ufeff\u0085")) or not k["ids"]  # blank input: no tokens
        if not ok:
            bad.append(json.dumps(t)[:60])
    longest = max(len(k["ids"]) for k in out["tokenize"])
    check(len(out["tokenize"]) == len(out["detect"]) == len(rows) and not bad,
          f"{len(rows)} texts (up to {longest:,} tokens): offsets consistent, spans sorted and inside the text" + (f"; bad: {bad[:3]}" if bad else ""))
    check(longest >= 10000, "a text of 10,000+ tokens")


def test_fuzz(n):
    cli = ASAN_CLI if ASAN_CLI.exists() else CLI
    print(f"[fuzz] malformed files and random bytes ({'ASan + UBSan' if cli == ASAN_CLI else 'no sanitizer: make asan'})")
    m = first_model()
    if not m:
        return skip("no model")
    t0 = time.time()
    p = subprocess.run([str(cli), "fuzz", m[1], m[2], str(n)], capture_output=True)
    res = p.stdout.decode().strip()
    check(p.returncode == 0, f"{res} ({time.time() - t0:.0f} s)" + (f" {p.stderr.decode()[-1500:]}" if p.returncode else ""))


def test_threads(cli, n_threads=4, limit=200, label="threads"):
    print(f"[{label}] {n_threads} threads sharing one model")
    for size, variant in (("nano", "q4"), ("medium", "q4e")):
        if SIZES and size not in SIZES:
            continue
        fix = FIX / f"predict-{size}-{variant}.jsonl"
        onnx, tok = model_paths(size, variant)
        if not fix.exists() or not onnx.exists():
            skip(f"{size} {variant}: fixture or model absent")
            continue
        texts = [r["text"] for r in read_jsonl(fix)[:limit]]
        # long texts too: several fixture rows joined (several windows each)
        texts += ["\n".join(texts[i:i + 40]) for i in range(0, min(len(texts), 120), 40)]
        res = run_cli(cli, ["threads", onnx, tok, n_threads], texts)[0]
        check(res["ok"], f"{size} {variant}: {res['texts']} texts x {res['threads']} threads, {res['differences']} differences ({res['ms']:.0f} ms)")


def cli_is_fma(cli):
    p = subprocess.run([str(cli), "info"], capture_output=True)
    return b'"fma": true' in p.stdout


def test_js():
    """Spans of the native core vs the npm package's own WebAssembly engine on the same rows (expected: identical)."""
    fma = cli_is_fma(CLI)
    print(f"[js] spans identical to the npm package's WebAssembly engine ({'relaxed SIMD' if fma else 'baseline SIMD128'} build)")
    if os.environ.get("PII_JS") == "0":
        return skip("PII_JS=0")
    node = shutil.which("node")
    if not node or not (JS_DIST / "index.js").exists():
        return skip("node or js/dist absent (cd js && npm run build)")
    runs = RUNS if os.environ.get("PII_JS") == "all" else JS_RUNS
    for size, variant, bias in runs:
        if SIZES and size not in SIZES:
            continue
        name = f"{size} {variant} bias {bias:g}"
        fix = FIX / f"predict-{size}-{variant}{f'-b{bias:g}' if bias else ''}.jsonl"
        onnx, tok = model_paths(size, variant)
        if not fix.exists() or not onnx.exists():
            skip(f"{name}: fixture or model absent")
            continue
        rows = read_jsonl(fix)
        texts = [r["text"] for r in rows]
        got = c_spans.get((size, variant, bias)) or run_cli(CLI, ["detect", onnx, tok, -bias], texts)
        payload = "".join(json.dumps({"text": t}) + "\n" for t in texts)
        t0 = time.time()
        p = subprocess.run([node, str(HERE / "js_detect.mjs"), str(JS_DIST), str(MODELS / f"pii-{size}"), size, variant, str(-bias), "1" if fma else "0"],
                           input=payload.encode(), capture_output=True)
        ms = (time.time() - t0) * 1e3
        if p.returncode:
            check(False, f"{name}: node failed: {p.stderr.decode()[-1000:]}")
            continue
        js = [json.loads(l) for l in p.stdout.decode().splitlines()]
        same = sum(1 for a, b in zip(js, got) if a["spans"] == [[s[3], s[4], s[2]] for s in b["spans"]])
        summary.append((f"js {name}", f"{same}/{len(rows)} identical to the wasm engine"))
        check(same == len(rows) == len(js), f"{name}: {same}/{len(rows)} rows identical to the wasm engine (node {ms:.0f} ms)")
        shown = 0
        for t, a, b in zip(texts, js, got):
            if a["spans"] != [[s[3], s[4], s[2]] for s in b["spans"]] and shown < 3:
                shown += 1
                print(f"    {json.dumps(t)[:80]}\n      js {a['spans'][:6]}\n      c  {[[s[3], s[4], s[2]] for s in b['spans']][:6]}")


# ---------------------------------------------------------------- (j) table mode
NAMES = ["Maria Lopez", "James Smith", "김민수", "이서연", "山田太郎", "佐藤花子", "Jean Dupont", "Anna Kowalska", "Ahmed Ali",
         "Olga Petrova", "Lars Nilsen", "Sofia Rossi", "Nguyen Van An", "Priya Sharma", "José García", "José Martínez",
         "Emma Müller", "Mehmet Yılmaz", "Chen Wei", "Lucas Silva", "प्रिया शर्मा", "স্ত্রী রায়", "O'Brien, Sean", "محمد علي"]
COMMENTS = ["The delivery was late and the box was badly damaged in transit",
            "Great service, the courier was polite and arrived right on time",
            "I would like a refund for the second order because it never came",
            "Packaging could be better but the product itself works fine 👍🏽",
            "Please call me back about the warranty extension next week",
            "Nothing to add, everything was exactly as described online",
            "The app crashed twice while I was paying, \"very\" annoying",
            "Five stars 🇰🇷 I will recommend this shop to all my friends",
            "Support answered quickly and solved my problem in one call",
            "Delivery person left it at the wrong door again, second time",
            "First line of my answer\nand a second line, after a line break",
            "He said \"\"fine\"\" twice; nobody, however, called me back at all",
            "👩‍👩‍👧 Our family loved it; the kids use it every single day",
            "배송이 늦었지만 상품은 아주 좋았습니다. 다음에도 주문할게요",
            "Prices went up again; I am not sure I will order next month\twait"]
CITIES = ["Seoul", "Paris", "Warsaw", "Osaka", "Oslo", "Rome", "Austin", "Hanoi", "Mumbai", "São Paulo", "Seoul 🇰🇷", "🗼Tokyo"]
LIKERT = ["Strongly agree", "Agree", "Neutral", "Disagree", "Strongly disagree", "매우 그렇다", "그렇다", "보통", "Agree 👍", "5", "4", "N/A"]
EMOJI = ["👩‍👩‍👧", "🇰🇷", "👍🏽", "1️⃣", "😀", "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "é"]
PLACEHOLDERS = ["N/A", "n/a", "-", "--", "—", "", "없음", "NULL", "none", " "]


def gen_value(rng, kind, i, name, user):
    if kind == "id":
        return str(i + 1)
    if kind == "cust":
        return rng.choice([f"C-{rng.randint(10000, 99999)}", f"A{rng.randint(100000, 999999)}", f"ORD-2026-{rng.randint(1, 9999):04d}"])
    if kind == "name":
        v = name
        if rng.random() < 0.12:
            v = rng.choice([f"{v} {rng.choice(EMOJI)}", f"{rng.choice(EMOJI)}{v}", f"{v}{rng.choice(EMOJI)}"])
        return v
    if kind == "email":
        v = f"{user}@{rng.choice(['example.com', 'mail.example.org', 'example.co.kr'])}"
        if rng.random() < 0.08:
            v = v.replace("@", "＠")
        if rng.random() < 0.1:
            v = f"{v} {rng.choice(EMOJI)}"
        return v
    if kind == "phone":
        a, b = rng.randint(1000, 9999), rng.randint(1000, 9999)
        return rng.choice([f"010-{a}-{b}", f"+82 10 {a} {b}", f"({a % 900 + 100}) {a % 1000:03d}-{b}", f"+33 6 {a // 100} {a % 100:02d} {b // 100} {b % 100:02d}",
                           f"07700 {b}{a % 100:02d}", f"٠٥٠{a}{b}"[:10]])
    if kind == "city":
        return rng.choice(CITIES)
    if kind == "comment":
        return rng.choice(COMMENTS)
    if kind == "user":
        return user
    if kind == "likert":
        return rng.choice(LIKERT)
    return str(rng.randint(1, 5))  # score


def gen_table(rng, k):
    """A generated table: (text, options, cells, kinds, exact). cells: [row, col, cp_start, cp_end] value ranges (header row 0),
    kinds: column kinds; exact: False when the options give a delimiter other than the one written (cells then unknown)."""
    kinds = rng.sample(["id", "cust"], rng.randint(1, 2)) + ["name"] + rng.sample(["email", "phone", "city", "comment", "user", "likert", "likert", "score"], rng.randint(2, 6))
    rng.shuffle(kinds)
    kinds = [kd if kinds.count(kd) == 1 or kinds.index(kd) == j else kd + "2" for j, kd in enumerate(kinds)]
    delim = [",", "\t", ";", "markdown"][k % 4]
    header = rng.random() > 0.2
    crlf = rng.random() < 0.25
    nrows = rng.randint(3, 16) if rng.random() < 0.9 else rng.randint(20, 30)
    rows = [kinds[:]] if header else []
    for i in range(nrows):
        name = rng.choice(NAMES)
        user = "".join(ch for ch in name.split()[0].lower() if ch.isascii() and ch.isalnum()) or "user"
        user += str(rng.randint(1, 99))
        row = [gen_value(rng, kd.rstrip("2"), i, name, user) for kd in kinds]
        for c, kd in enumerate(kinds):
            if kd in ("name", "email", "phone") and rng.random() < 0.08:
                row[c] = rng.choice(PLACEHOLDERS)
            if kd == "email" and rng.random() < 0.04:
                row[c] = user + " at example.com"  # no '@': never propagated as EMAIL
            if kd == "phone" and rng.random() < 0.04:
                row[c] = f"{rng.randint(10000, 99999)}"  # 5 digits: never propagated as PHONE
        if delim == "markdown":
            row = [v.replace("\n", " ").replace("\t", " ").replace("|", "/").strip() for v in row]
        rows.append(row)
    text, cells, cp = "", [], 0
    nl = "\r\n" if crlf else "\n"

    def add(piece):
        nonlocal text, cp
        text += piece
        cp += len(piece)

    explicit = k % 3 == 0
    if delim == "markdown" and explicit and rng.random() < 0.5:  # auto-detection needs a first line starting with '|'
        add("Survey results\n\n")
    elif delim != "markdown" and rng.random() < 0.1:
        add(rng.choice([nl, "  " + nl, '""' + nl]))  # blank records before the header
    no_last_pipe = delim == "markdown" and rng.random() < 0.25
    for r, row in enumerate(rows):
        if delim == "markdown":
            add("|")
            for c, v in enumerate(row):
                add(" ")
                cells.append([r, c, cp, cp + len(v)])
                add(v + (" |" if c < len(row) - 1 or not no_last_pipe else ""))
            add(nl)
            if r == 0:
                add("|" + "|".join(rng.choice(["---", ":--", "--:", ":-:", "-"]) for _ in row) + "|" + nl)
            if rng.random() < 0.05:
                add(nl)  # a blank line: not a table line
            continue
        for c, v in enumerate(row):
            if c:
                add(delim)
            quote = any(ch in v for ch in (delim, '"', "\n", "\r")) or rng.random() < 0.15
            if quote:
                add('"')
                q = v.replace('"', '""').replace("\n", nl)
                cells.append([r, c, cp, cp + len(q)])
                add(q + '"')
            else:
                if v and rng.random() < 0.05:
                    v = rng.choice([" ", "  ", " "]) + v + rng.choice(["", " "])
                cells.append([r, c, cp, cp + len(v)])
                add(v)
        if r < len(rows) - 1 or rng.random() < 0.85:  # sometimes no newline at the end
            add(nl)
        if rng.random() < 0.06:
            add(rng.choice([nl, "   " + nl, '""' + nl]))  # blank records: empty, whitespace only, a lone ""
    opts = {"delimiter": delim if explicit else "auto", "header": header}
    exact = True
    if k % 17 == 8:  # a wrong explicit delimiter
        opts["delimiter"] = {",": ";", "\t": ",", ";": "\t", "markdown": ","}[delim]
        exact = False
    if rng.random() < 0.2:
        opts["vote"] = False
    if rng.random() < 0.1:
        opts["labels"] = rng.choice([["EMAIL", "PHONE", "ADDRESS"], ["PERSON_L2", "EMAIL"], ["PERSON", "USERNAME"], ["PHONE"]])
    return text, opts, cells, kinds, exact


N_GEN = 300

# texts full of multi-code-point grapheme clusters (snapping parity, model-free)
GRAPHEME_TEXTS = [
    "👩‍👩‍👧 Contact Maria Lopez 🇰🇷🇺🇸 at maria@example.com, 👍🏽 1️⃣ #️⃣ José García 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "क्षत्रिय प्रिया शर्मा, स्त्री, অক্ষয় স্ত্রী রায়, ক্ষ্ম, ଶ୍ରୀ, ജ്ഞാനം, ગ્રાહક, తెలుగు",
    "각너 김민수 이 한 이서연\r\n둘째 줄 010-1234-5678",
    "José García é́́ ạ̈ ño ﬁ ç̧ Zoë Ångström",
    "🇰🇷🇺🇸🇯 odd 🇫🇷🇩🇪🇮🇹 flags, 👨🏽‍💻 👩🏻‍❤️‍💋‍👨🏿 🧑‍🤝‍🧑 ❤️ ☺️ 9️⃣ *️⃣",
    "ภาษาไทย สมชาย ใจดี, Tiếng Việt Nguyễn Văn An, العربية محمد علي, עברית דוד כהן",
    "a\r\nb\rc\n\r\n‍‍ x‍y ̀ start with a mark, ﻿BOM, ⁠ word joiner",
]


def table_inputs(n_gen=N_GEN):
    """(row dict for pii_cli table / js_table.mjs, generated table info or None, True when the text is not a table); the
    first n_gen generated tables (the same ones for any n_gen)."""
    import random
    rng = random.Random(20261101)
    rows = []
    hand = [
        ({"text": "name,email,phone\nMaria Lopez,maria@example.com,010-1234-5678\nKim Minsu,kim@example.com,010-2222-3333\n"}),
        ({"text": "name\temail\nMaria Lopez\tmaria@example.com\r\n\r\nKim Minsu\tkim@example.com\r\n"}),
        ({"text": "name;city\nMaria Lopez;Seoul\nKim Minsu;Paris\n"}),
        ({"text": "| Name | Email |\n|---|:--:|\n| Maria Lopez | maria@example.com |\n| Kim Minsu |  |\n"}),
        ({"text": "Hello, my name is Maria Lopez.\nI live in Seoul; call me at 010-1234-5678.\n", "plain": True}),
        ({"text": 'name,note\n"Lopez, Maria","said ""call me"" at 010-1234-5678"\n"Kim\nMinsu",x\n'}),
        ({"text": 'name,note\n"Lopez, Maria",unclosed "quote\n"Kim Minsu,still open'}),
        ({"text": "name,email\nMaria Lopez,maria@example.com\nKim Minsu,kim@example.com\n", "header": False}),
        ({"text": "name,email\nMaria Lopez,maria@example.com\nKim Minsu,kim@example.com\n", "vote": False}),
        ({"text": "name,email\nMaria Lopez,maria@example.com\nKim Minsu,kim@example.com\n", "delimiter": "\t"}),
        ({"text": "name,email\nMaria Lopez,maria@example.com\nKim Minsu,kim@example.com\n", "labels": ["EMAIL"]}),
        ({"text": "👩‍👩‍👧 Contact Maria Lopez 🇰🇷🇺🇸 at maria@example.com, 👍🏽 1️⃣ José García"}),
        ({"text": "name,emoji\nMaria Lopez 👩‍👩‍👧,🇰🇷\nJosé García,👍🏽 1️⃣\n"}),
        ({"text": "★ Maria Lopez ★ ☆ call 0️⃣1️⃣0️⃣ 1️⃣2️⃣3️⃣4️⃣ 5️⃣6️⃣7️⃣8️⃣, Nadine8️⃣ 🔟 Ⓜ️ 🅰️"}),
        ({"text": "Maria 😀\u0301 Lopez \u0600😀Kim 😀"}),
        ({"text": '\n  \n""\nname,email\r\n"Maria ""Mia"" Lopez" ,maria@example.com\r\nKim Minsu,kim@example.com\rx\r\n'}),
        ({"text": "| Name | Phone\n| --- | ---\n| Maria Lopez | 010-1234-5678\n|\n| Kim Minsu | 010-2222-3333 |\n"}),
    ]
    names = ["Maria Lopez", "Kim Minsu", "Jean Dupont", "Anna Kowalska", "Ahmed Ali", "Sofia Rossi", "Lars Nilsen", "N/A", "Priya Sharma", "Chen Wei"]
    lines = ["id,name,email,phone,comment"] + [
        f"{i + 1},{n},{n.split()[0].lower()}{i}@example.com,010-{1000 + i}-{2000 + i},\"{COMMENTS[i]}\"" for i, n in enumerate(names)]
    hand.append({"text": "\n".join(lines) + "\n"})
    hand.append({"text": "| id | name | phone |\n|---|---|---|\n" + "".join(f"| {i} | {n} | 010-{3000 + i}-{4000 + i} |\n" for i, n in enumerate(names))})
    likert = ["respondent;q1;q2;email"] + [f"{n};{LIKERT[i % 5]};{LIKERT[(i * 3) % 5]};{n.split()[0].lower()}@example.com" for i, n in enumerate(names)]
    hand.append({"text": "\r\n".join(likert) + "\r\n"})
    for t in GRAPHEME_TEXTS:
        hand.append({"text": t})
    for h in hand:
        plain = h.pop("plain", False) or ("\n" not in h["text"] and set(h) == {"text"})  # one line: never 2 rows
        rows.append((h, None, plain))
    for k in range(n_gen):
        text, opts, cells, kinds, exact = gen_table(rng, k)
        rows.append(({"text": text, **opts}, (cells, kinds, opts) if exact else None, False))
    # header context (pii_detect_table only; the model-free apply steps ignore the field): the hand-made inputs and the first
    # 40 generated tables again, with no exact-cell check (the added spans are the model's)
    rows += [(dict(r, context="header"), None, plain) for r, _g, plain in rows[:len(hand) + 40]]
    # whole_words: the hand-made inputs and the first 40 generated tables again, and the word texts
    rows += [(dict(r, words=True), None, plain) for r, _g, plain in rows[:len(hand) + 40]]
    rows += [({"text": t, "words": True}, None, "\n" not in t) for t in WORD_TEXTS]
    # strict_short_names: short values (where it applies), the word texts and some hand-made tables (whole file longer than
    # 8 tokens; with the header context the per-cell texts are short)
    rows += [({"text": t, "strict": True}, None, True) for t in SHORT_TEXTS]
    rows += [({"text": t, "strict": True, "words": True}, None, "\n" not in t) for t in SHORT_TEXTS + WORD_TEXTS]
    rows += [(dict(r, strict=True), None, plain) for r, _g, plain in rows[:len(hand)]]
    rows += [(dict(r, strict=True, context="header"), None, plain) for r, _g, plain in rows[:len(hand)]]
    for t, raw in offset_texts()[:22]:  # one-line texts are not tables: detect's spans (grapheme snapping); no lone surrogates
        if raw is None and not any(0xD800 <= ord(ch) <= 0xDFFF for ch in t):
            rows.append(({"text": t}, None, "\n" not in t))
    return rows


FAMILY_LABELS = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "USERNAME"]
OTHER_LABELS = ["ADDRESS", "ORG", "ID_NUMBER", "URL", "DATE_OF_BIRTH", "SECRET"]


def synth_spans(rng, text, cells):
    """Whole-text spans as detect gives them before snapping (sorted, non-overlapping, code points), made up so that the table
    steps meet their edge cases: columns covered by one family at rates around the 80% bars, cells partly covered, split
    between families or crossing delimiters, and short random spans with boundaries inside grapheme clusters."""
    n = len(text)
    if not n:
        return []
    cand = []
    src = lambda: rng.choice([1, 1, 1, 2, 3])
    cols = sorted({c[1] for c in cells})
    plan = {c: (rng.choice(FAMILY_LABELS + FAMILY_LABELS + OTHER_LABELS), rng.choice([1.0, 1.0, 0.9, 0.8, 0.75, 0.5])) for c in cols if rng.random() < 0.6}
    for _r, c, s, e in cells:
        if c not in plan or e <= s or rng.random() > plan[c][1]:
            continue
        lab = plan[c][0]
        m = rng.random()
        if m < 0.12:
            s += rng.randint(1, 3)
        elif m < 0.24:
            e -= rng.randint(1, 3)
        elif m < 0.32:
            s -= rng.randint(1, 4)
            e += rng.randint(1, 4)
        elif m < 0.42 and e - s >= 2:
            mid = rng.randint(s + 1, e - 1)
            cand.append([s, mid, lab, src()])
            s, lab = mid, rng.choice(FAMILY_LABELS + OTHER_LABELS)
        cand.append([s, e, lab, src()])
    for _ in range(rng.randint(0, 8)):
        a = rng.randrange(n)
        cand.append([a, a + rng.randint(1, 4 if rng.random() < 0.7 else 40), rng.choice(FAMILY_LABELS + OTHER_LABELS), src()])
    out, end = [], 0
    for s, e, lab, so in sorted(([max(0, s), min(n, e), lab, so] for s, e, lab, so in cand), key=lambda x: x[0]):
        if s < e and s >= end:
            out.append([s, e, lab, so])
            end = e
    return out


def apply_inputs():
    """Rows for the model-free parity of the table steps (pii_cli apply vs detectTableSpans): every table input with
    3 sets of made-up spans, plus dense short spans over the grapheme texts (snapping merges, chains of 3 or more)."""
    import random
    rng = random.Random(1101)
    rows = []
    for r, gen, _plain in table_inputs():
        cells = gen[0] if gen else []
        for v in range(3):
            row = dict(r, spans=synth_spans(rng, r["text"], cells))
            if v == 2:
                row["vote"] = not r.get("vote", True)
            rows.append(row)
    for t in GRAPHEME_TEXTS + [t for t, raw in offset_texts()[5:20] if raw is None and not any(0xD800 <= ord(ch) <= 0xDFFF for ch in t)]:
        for _ in range(12):
            spans, pos = [], rng.randint(0, 3)
            while pos < len(t):
                e = pos + rng.randint(1, 4)
                spans.append([pos, min(e, len(t)), rng.choice(FAMILY_LABELS + OTHER_LABELS), rng.choice([1, 2, 3])])
                pos = e + rng.choice([0, 0, 0, 1, 2, 5])
            rows.append({"text": t, "spans": spans})
    rows += edge_inputs(rng)
    # whole_words: every row again with words on, plus dense short spans over texts that mix spaced and unspaced scripts,
    # joiners, digits and astral letters
    rows += [dict(r, words=True) for r in rows]
    for t in WORD_TEXTS:
        for _ in range(12):
            spans, pos = [], rng.randint(0, 3)
            while pos < len(t):
                e = pos + rng.randint(1, 3)
                spans.append([pos, min(e, len(t)), rng.choice(FAMILY_LABELS + OTHER_LABELS), rng.choice([1, 2, 3])])
                pos = e + rng.choice([0, 1, 2, 4, 7])
            rows.append({"text": t, "spans": spans, "words": True})
    return rows


SHORT_TEXTS = ["남성", "여성", "경기", "경기도", "네이버", "카카오", "홍길동", "김민지", "이서연", "Maria Lopez", "R23", "P3", "5, 7",
               "서울 강남구", "Kim Minsu 010-1234-5678", "매우 그렇다", "😀 김민지"]

WORD_TEXTS = [
    "남성, 경기도, R23, 카카오 블로그; 홍길동님 010-1234-5678",
    "region,id,name\n경기도,R23,김민지\n서울,P10,O'Brien\n부산,S2024001,Jean-Luc Picard\n",
    "田中太郎さんとคุณสมชาย met Ana-María (ana_maria@example.com) at 𝐀𝐁c-12 — l'été d'Émile",
    "José Garci\u0301a, Zoë's e\u0301te\u0301, 👩‍💻dev-ops 1️⃣2️⃣, ‐hyphen‐ ’quote’ 'x'",
    "전화는 010-1234-5678입니다, kim@example.com으로, R23호 3층 Kim민지-님 ㄱ1ㄴ \uffa1A-\u1100\ua960x \ud7b0'7",
    "홍길동님께서는 김민지씨에게 이서연이라고 했고 박박이었다 경기도청 경기도 남성이었다 님님님님님님님님님님님님님 홍길동-님 길동이여",
]


EDGE_LEAD = ["\u0301", "\u200d", "\ufe0f", "\U0001f3fd", "\u093e", "\u0301\u0301"]  # Extend, ZWJ, VS16, skin tone, SpacingMark
EDGE_TRAIL = ["\u0600", "\u06dd", "\u070f", "\u0890", "\u08e2"]  # Prepend


def edge_inputs(rng, n=60):
    """Tables whose cell values start with a grapheme extender or end with a Prepend character next to ',', ';', '|' or a
    quote (one cluster across the cell boundary), with spans over whole cells, parts of cells and empty spans; marked "edge"
    so test_table_apply can check that no span covers a delimiter, quote or line break (no value holds one here)."""
    rows = []
    for k in range(n):
        d = [",", ";", "\t", "markdown"][k % 4]
        sep = "|" if d == "markdown" else d
        quote = d != "markdown" and k % 3 == 0
        vals = []
        for _r in range(7):
            row = []
            for _c in range(3):
                v = rng.choice(["Ann Lee", "Bo", "Kim", "x", "Zoë", "010-1234-5678", "a@b.co"])
                if rng.random() < 0.5:
                    v = rng.choice(EDGE_LEAD) + v
                if rng.random() < 0.5:
                    v += rng.choice(EDGE_TRAIL)
                row.append(f'"{v}"' if quote and rng.random() < 0.6 else v)
            vals.append(row)
        if d == "markdown":
            lines = ["|" + "|".join(vals[0]) + "|", "|---|---|---|"] + ["|" + "|".join(r) + "|" for r in vals[1:]]
        else:
            lines = [sep.join(r) for r in vals]
        text = "\n".join(lines) + "\n"
        spans, pos, n_cp = [], 0, len(text)
        while pos < n_cp:
            if text[pos] in ',;\t|"\n':
                pos += 1
                continue
            e = pos
            while e < n_cp and text[e] not in ',;\t|"\n':
                e += 1
            m = rng.random()
            if m < 0.6:
                spans.append([pos, e, rng.choice(["PERSON", "PERSON", "EMAIL", "ORG"]), rng.choice([1, 2, 3])])
            elif m < 0.7:
                spans.append([pos, pos, "PERSON", 2])
            elif m < 0.85 and e - pos >= 2:
                spans.append([pos + 1, e, "PERSON", 1])
            pos = e + 1
        rows.append({"text": text, "spans": spans, "delimiter": d, "vote": k % 5 != 4, "edge": True})
    return rows


def test_table_apply(cli, label="table steps"):
    """Model-free parity: the table steps (parse, clip, vote, merge, snapping) of C and JS on the same made-up spans."""
    print(f"[{label}] table steps and grapheme snapping without a model: pii_cli apply vs the npm package's detectTableSpans")
    node, ts = shutil.which("node"), REPO / "js" / "src" / "table.ts"
    if os.environ.get("PII_JS") == "0" or not node or not ts.exists():
        return skip("node or js/src/table.ts absent")
    rows = apply_inputs()
    payload = "".join(json.dumps(r) + "\n" for r in rows).encode()
    p = subprocess.run([str(cli), "apply"], input=payload, capture_output=True)
    q = subprocess.run([node, str(HERE / "js_table.mjs"), "apply", str(ts)], input=payload, capture_output=True)
    if p.returncode or q.returncode:
        return check(False, f"apply failed: {p.stderr.decode()[-500:]} {q.stderr.decode()[-500:]}")
    c = [json.loads(l)["spans"] for l in p.stdout.decode().splitlines()]
    j = [json.loads(l)["spans"] for l in q.stdout.decode().splitlines()]
    same = sum(1 for a, b in zip(c, j) if a == b)
    n_table = sum(1 for s in c for x in s if x[3] == 4)
    summary.append((label, f"{same}/{len(rows)} identical ({n_table} propagated spans)"))
    check(same == len(rows) == len(c) == len(j), f"{same}/{len(rows)} inputs identical, spans, labels and sources ({n_table} propagated spans)")
    check(n_table > 0, f"column vote fired on made-up spans ({n_table} propagated spans)")
    crossing = sum(1 for r, a in zip(rows, c) if r.get("edge") and any(ch in ',;\t|"\n' for s, e, _l, _s in a for ch in r["text"][s:e]))
    check(crossing == 0, f"snapping stays inside cells: no span covers a delimiter, quote or line break "
          f"({sum(1 for r in rows if r.get('edge'))} tables of cells starting with an extender or ending with a Prepend; {crossing} crossing)")
    shown = 0
    for r, a, b in zip(rows, c, j):
        if a != b and shown < 3:
            shown += 1
            diff = [x for x in a if x not in b][:4], [x for x in b if x not in a][:4]
            print(f"    {json.dumps({k: v for k, v in r.items() if k != 'spans'})[:120]}\n      c only {diff[0]}\n      js only {diff[1]}")


def js_table(size, variant, rows, fma):
    node = shutil.which("node")
    if os.environ.get("PII_JS") == "0" or not node or not (JS_DIST / "index.js").exists():
        return None, "node or js/dist absent (cd js && npm run build)"
    payload = "".join(json.dumps(r) + "\n" for r in rows)
    p = subprocess.run([node, str(HERE / "js_table.mjs"), str(JS_DIST), str(MODELS / f"pii-{size}"), size, variant, "0", "1" if fma else "0"],
                       input=payload.encode(), capture_output=True)
    if p.returncode:
        return None, f"node failed: {p.stderr.decode()[-600:]}"
    return [json.loads(l) for l in p.stdout.decode().splitlines()], None


def test_table(cli, sizes=(("nano", "q4"), ("medium", "q4e")), label="table", js=True, n_gen=N_GEN):
    """Table mode: structure (spans inside one cell value, free-text columns never propagated, vote off and the label
    filter respected, non-tables equal to detect) and span-for-span parity with the npm package's detectTable."""
    print(f"[{label}] table mode and grapheme snapping")
    inputs = table_inputs(n_gen)
    n_gen = sum(1 for _, g, _ in inputs if g)  # generated tables with known cells (not the wrong-delimiter ones)
    fma = cli_is_fma(CLI)
    for size, variant in sizes:
        if SIZES and size not in SIZES:
            continue
        onnx, tok = model_paths(size, variant)
        if not onnx.exists() or not tok.exists():
            skip(f"{size} {variant}: model absent")
            continue
        rows = [r for r, _, _ in inputs]
        t0 = time.time()
        jsr, why = js_table(size, variant, rows, fma) if js else (None, "not run here")
        js_ms = (time.time() - t0) * 1e3
        if jsr is None:
            skip(f"{size} {variant}: parity with detectTable: {why}")
        crows = [dict(r, extra=j["extra"]) if jsr else r for r, j in zip(rows, jsr or rows)]
        payload = "".join(json.dumps(r) + "\n" for r in crows)
        t0 = time.time()
        out = {}
        for cmd in ("table", "detect"):
            p = subprocess.run([str(cli), cmd, onnx, tok], input=payload.encode(), capture_output=True)
            if p.returncode:
                check(False, f"{size} {variant}: pii_cli {cmd} failed: {p.stderr.decode()[-500:]}")
                break
            out[cmd] = [json.loads(l) for l in p.stdout.decode().splitlines()]
        else:
            ms = (time.time() - t0) * 1e3
            tab, det = out["table"], out["detect"]
            bad_units = bad_cell = bad_free = bad_vote = bad_labels = bad_plain = 0
            n_table = n_tables_voted = 0
            for (r, gen, _), g, d in zip(inputs, tab, det):
                t = r["text"]
                u16, u8 = units(t)
                prev = 0
                for cs, ce, _, a, b, x, y, _src in g["spans"]:
                    bad_units += not (prev <= cs < ce <= len(t) and [a, b, x, y] == [u16[cs], u16[ce], u8[cs], u8[ce]])
                    prev = ce
                if "labels" in r:
                    bad_labels += any(s[2] not in r["labels"] for s in g["spans"])
                if r.get("vote") is False:
                    bad_vote += any(s[7] == 4 for s in g["spans"])
                if gen is None:
                    continue
                cells, kinds, _ = gen
                first = 1 if r.get("header", True) else 0
                voted = False
                for cs, ce, lab, *_rest in g["spans"]:
                    src = _rest[-1]
                    cell = next((c for c in cells if c[2] <= cs and ce <= c[3]), None)
                    bad_cell += cell is None
                    if cell and src == 4:
                        n_table += 1
                        voted = True
                        bad_free += kinds[cell[1]] == "comment" or cell[0] < first
                n_tables_voted += voted
            # inputs that are not tables give detect's spans (the same row through pii_cli detect, sources dropped)
            n_plain = 0
            for (r, _, plain), g, d in zip(inputs, tab, det):
                if plain:
                    n_plain += 1
                    bad_plain += [s[:7] for s in g["spans"]] != d["spans"]
            check(len(tab) == len(det) == len(inputs) and not bad_units, f"{size} {variant}: {len(inputs)} inputs answered, offsets consistent "
                  f"(C {ms:.0f} ms for table + detect" + (f", node {js_ms:.0f} ms)" if jsr else ")"))
            check(bad_cell == 0, f"{size} {variant}: every span of the {n_gen} generated tables inside one cell value ({bad_cell} outside)")
            check(bad_free == 0, f"{size} {variant}: no propagation into free-text columns or the header row ({bad_free})")
            check(bad_vote == 0 and bad_labels == 0, f"{size} {variant}: vote off and the label filter respected ({bad_vote}, {bad_labels})")
            check(bad_plain == 0, f"{size} {variant}: {n_plain - bad_plain}/{n_plain} texts that are not tables give the spans of detect")
            check(n_tables_voted > 0, f"{size} {variant}: column vote fired in {n_tables_voted} of {n_gen} generated tables ({n_table} cells)")
            if jsr is not None:
                same = sum(1 for a, b in zip(jsr, tab) if a["spans"] == [[s[3], s[4], s[2], s[7]] for s in b["spans"]])
                summary.append((f"table {size} {variant}", f"{same}/{len(rows)} identical to the npm package's detectTable"))
                check(same == len(rows) == len(jsr), f"{size} {variant}: {same}/{len(rows)} inputs identical to the npm package's detectTable "
                      "(spans, labels, sources)")
                shown = 0
                for r, a, b in zip(rows, jsr, tab):
                    if a["spans"] != [[s[3], s[4], s[2], s[7]] for s in b["spans"]] and shown < 3:
                        shown += 1
                        print(f"    {json.dumps(r)[:100]}\n      js {a['spans'][:6]}\n      c  {[[s[3], s[4], s[2], s[7]] for s in b['spans']][:6]}")


def test_selftest(cli, label="selftest"):
    print(f"[{label}] API checks")
    m = first_model()
    args = ["selftest"] + ([str(m[1]), str(m[2])] if m else [])
    p = subprocess.run([str(cli), *args], capture_output=True)
    check(p.returncode == 0, f"selftest{'' if m else ' (no model: static checks only)'}" + (f": {p.stderr.decode()[-1500:]}" if p.returncode else ""))


def test_pins():
    print("[pins] model files match the published sizes and SHA-256")
    found = False
    for size in ("nano", "small", "medium", "large"):
        for kind, rel in (("q4", "onnx/model_q4.onnx"), ("q4e", "onnx/model_q4e.onnx"), ("tokenizer", "tokenizer.web.json")):
            f = MODELS / f"pii-{size}" / rel
            if not f.exists():
                continue
            found = True
            p = subprocess.run([str(CLI), "verify", str(f), size, kind], capture_output=True)
            check(p.returncode == 0, f"{size} {kind}")
    if not found:
        skip("no model files")


def bench():
    print("[bench] single thread, this machine")
    for size, variant in (("nano", "q4"), ("small", "q4"), ("medium", "q4e"), ("large", "q4e")):
        if SIZES and size not in SIZES:
            continue
        fix = FIX / f"predict-{size}-{variant}.jsonl"
        onnx, tok = model_paths(size, variant)
        if not fix.exists() or not onnx.exists():
            skip(f"{size}: fixture or model absent")
            continue
        texts = [r["text"] for r in read_jsonl(fix)]
        r = run_cli(CLI, ["bench", onnx, tok], texts)[0]
        line = (f"{r['ms_per_short_text']:.1f} ms per short text ({r['short_texts']} texts of <= 64 tokens, {r['short_avg_tokens']:.0f} on average); "
                f"{r['ms_per_text']:.1f} ms per fixture text ({r['avg_tokens']:.0f} tokens on average); {r['ms_long']:.0f} ms for {r['long_tokens']} tokens")
        print(f"  {size} {variant} ({r['build']}): {line}")
        summary.append((f"bench {size} {variant} {r['build']}", line))


def main():
    if not CLI.exists():
        print(f"{CLI} not found: run make (or cmake) first")
        return 2
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    want = lambda t: only is None or t in only
    if want("pins"):
        test_pins()
    if want("selftest"):
        test_selftest(CLI)
    if want("tokenizer"):
        test_tokenizer(CLI)
    if want("offsets"):
        test_offsets(CLI)
    if want("predict"):
        test_predict(CLI)
    if want("js"):
        test_js()
    if want("table"):
        test_table_apply(CLI)
        test_table(CLI)
    if want("threads"):
        test_threads(CLI)
    if want("asan"):
        if ASAN_CLI.exists():
            global SIZES
            test_selftest(ASAN_CLI, label="asan+ubsan selftest")
            test_tokenizer(ASAN_CLI, limit=600, label="asan+ubsan tokenizer (600 rows)")
            test_offsets(ASAN_CLI)
            saved, SIZES = SIZES, ["nano"]
            test_predict(ASAN_CLI, limit=60, label="asan+ubsan predict (nano, 60 rows)")
            test_table_apply(ASAN_CLI, label="asan+ubsan table steps")
            test_table(ASAN_CLI, sizes=(("nano", "q4"),), label="asan+ubsan table (nano, 40 generated tables)", js=False, n_gen=40)
            test_threads(ASAN_CLI, n_threads=3, limit=40, label="asan+ubsan threads (nano)")
            SIZES = saved
        else:
            print(f"[asan] skip: {ASAN_CLI} not built (make asan)")
    if only and "fuzz" in only:
        test_fuzz(int(os.environ.get("PII_FUZZ", "100")))
    if "--bench" in sys.argv:
        bench()
    if summary:
        print("\nsummary")
        for k, v in summary:
            print(f"  {k:32} {v}")
    print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
