#!/usr/bin/env python3
"""Parity tests of the native core against the npm package's fixtures (Python standard library only).

  (a) tokenizer: ids and offsets equal to Hugging Face tokenizers on js/test/fixtures/tokenize.jsonl (100% required)
  (b) predictions: spans equal to the Python evaluator for every run of js/test/predict.test.ts (99% of rows or more)
  (c) the same on a subset under AddressSanitizer + UndefinedBehaviorSanitizer (build/asan/pii_cli)
  (d) several threads sharing one model give the results of one thread
  (e) model files match the pinned sizes and SHA-256
  (f) API self-test (combine cases of js/test/combine.test.ts, arguments, malformed and truncated model files)
  (g) spans identical to the npm package's WebAssembly engine (node + js/dist; skipped without them)
  (h) offsets: UTF-8, code point and UTF-16 offsets agree with Python on hard texts (emoji, ZWJ, combining marks, RTL,
      CRLF, NUL, lone surrogates, invalid UTF-8, empty and blank input, 10,000+ tokens); spans sorted and inside the text
  (i) fuzz (only with --only fuzz): mutated model and tokenizer files and random bytes under ASan + UBSan if built

Fixtures and model copies are private (validation text) and never copied here: they are read from PII_FIXTURES
(default ../../js/test/fixtures) and PII_MODELS (default ../../js/test/models); every test skips when they are absent.
PII_SIZES=nano,medium limits the sizes; PII_CLI / PII_ASAN_CLI override the binaries; PII_JS_DIST points at the built
npm package (default ../../js/dist), PII_JS=0 skips (g). --only pins,selftest,tokenizer,offsets,predict,js,threads,asan
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


def test_tokenizer(cli, limit=None, label="tokenizer"):
    print(f"[{label}] parity with Hugging Face tokenizers")
    fix = FIX / "tokenize.jsonl"
    m = first_model()
    if not fix.exists() or not m:
        return skip("tokenize.jsonl or a model is absent")
    rows = read_jsonl(fix)[:limit]
    t0 = time.time()
    got = run_cli(cli, ["tokenize", m[1], m[2]], [r["text"] for r in rows])
    ms = (time.time() - t0) * 1e3
    bad_ids = bad_off = bad_units = 0
    shown = 0
    for r, g in zip(rows, got):
        u16, u8 = units(r["text"])
        ids_ok = g["ids"] == r["ids"]
        off_ok = g["cp"] == [list(o) for o in r["offsets"]]  # fixture offsets count code points
        bad_ids += not ids_ok
        bad_off += not off_ok
        bad_units += g["u16"] != [[u16[s], u16[e]] for s, e in g["cp"]] or g["u8"] != [[u8[s], u8[e]] for s, e in g["cp"]]
        if (not ids_ok or not off_ok) and shown < 5:
            shown += 1
            print(f"    {json.dumps(r['text'])[:100]}\n      hf ids {r['ids'][:16]} {r['offsets'][:6]}\n      c  ids {g['ids'][:16]} {g['cp'][:6]}")
    n = len(rows)
    if limit is None:
        summary.append(("tokenizer", f"ids {n - bad_ids}/{n}, offsets {n - bad_off}/{n} identical to Hugging Face"))
    check(len(got) == n, f"{len(got)}/{n} rows answered ({ms:.0f} ms)")
    check(bad_ids == 0, f"ids: {n - bad_ids}/{n} rows identical")
    check(bad_off == 0, f"offsets: {n - bad_off}/{n} rows identical")
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
