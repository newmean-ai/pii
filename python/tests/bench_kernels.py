# Speed of the matrix kernels (issue #3, criteria P1-P3): each configuration runs in a fresh process, one thread, warmed
# up; configurations alternate (forward, then backward) for --rounds rounds; the medians over the rounds are reported.
# Workloads, from the texts of test_kernel_isa.py: short logs (every base text of at most 64 tokens, ms per text) and one
# input of 1,000 tokens or more (the texts joined by newlines, as pii_cli bench builds it; median of five runs). On
# request, the workload of the issue #3 reporter: one input of 2,500 to 3,500 characters (every ninth short base text,
# all 17 languages, joined by newlines: 2,978 characters; median of five runs), for the wheel configurations only. It
# has no threshold of its own; the output says whether the CPU is the one promised on issue #3 (AVX2 and FMA, no
# AVX-512).
#
#   python tests/bench_kernels.py --model-dir DIR --size nano --variant q4 \
#       --wheel sse "$PY" sse --wheel avx2 "$PY" - --wheel avx2+fma "$PY" avx2+fma \
#       --cli cli-avx2 path/to/pii_cli - --require "avx2/sse>=1.10"
#   --wheel LABEL PYTHON KERNEL   detect() of the newmean_pii that PYTHON imports, NEWMEAN_PII_KERNEL=KERNEL ("-": unset)
#   --cli LABEL PII_CLI KERNEL    pii_cli bench (ms per short text, median of the five long runs)
#   --workloads LIST              what to measure, comma-separated: short, long, reporter (default short,long);
#                                 --cli and --require need short and long
#   --require "A/B>=X"            A must be at least X times as fast as B on both workloads (exit 1 otherwise);
#   --require "A/B>X"             more than X times as fast (A/B>1.0: A must beat B, a tie fails)
# Prints one JSON object: CPU, flags, the kernel each configuration ran, tokens, every round and the medians (with the
# reporter's workload also its characters, lines and sha256, and its tokens per configuration).
#
#   python tests/bench_kernels.py --p3-note BENCH.json LABEL
# Reads that output and says which P3 base LABEL fell below 0.95 of: "base" (the 1.6.1 release, KERNEL_BASE) and
# "nokernel" (this commit without the kernel). Either one fails P3 (the run's --require does that); when only "base" fails,
# the note records an end-to-end regression since 1.6.1 to investigate. It does not say the cause lies outside the
# kernel: the matched ratio passing allows a smaller kernel slowdown inside it. Always exits 0.
import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKLOADS = ("short", "long", "reporter")
REPORTER_CHARS = (2500, 3500)  # the reporter's workload: one input of 2,500 to 3,500 characters (about 3,000)


def cpu():
    """CPU model, the SIMD flags that matter here, whether any AVX-512 flag is present, and whether this is the CPU
    promised on issue #3 for the reporter's workload (x86-64 with AVX2 and FMA, without AVX-512)."""
    name, flags, avx512 = platform.processor() or platform.machine(), [], False
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            info = f.read()
        m = re.search(r"^model name\s*:\s*(.+)$", info, re.M)
        name = m.group(1) if m else name
        m = re.search(r"^flags\s*:\s*(.+)$", info, re.M)
        have = set(m.group(1).split()) if m else set()
        flags = [x for x in ("sse4_2", "avx", "avx2", "fma", "avx512f") if x in have]
        avx512 = any(x.startswith("avx512") for x in have)
    except OSError:
        try:
            name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip() or name
            feats = subprocess.run(["sysctl", "-n", "machdep.cpu.features", "machdep.cpu.leaf7_features"], capture_output=True,
                                   text=True).stdout.upper().split()
            flags = [x.lower() for x in ("SSE4.2", "AVX1.0", "AVX2", "FMA", "AVX512F") if x in feats]
            avx512 = any(x.startswith("AVX512") for x in feats)
        except OSError:
            pass
    promised = platform.machine().lower() in ("x86_64", "amd64") and "avx2" in flags and "fma" in flags and not avx512
    return {"model": name, "flags": flags, "avx512": avx512, "issue3_cpu": promised, "machine": platform.machine(),
            "system": platform.system()}


def reporter_text(texts):
    """The workload of the issue #3 reporter: one input of about 3,000 characters. Every ninth short base text of
    test_kernel_isa.py (make_base_texts: the short texts, then three joined ones), which takes all 17 languages,
    joined by newlines while the input stays within 3,000 characters; it must hold 2,500 to 3,500 (2,978 at this
    writing)."""
    out = ""
    for t in texts[:-3][::9]:
        cand = f"{out}\n{t}" if out else t
        if len(cand) > 3000:
            break
        out = cand
    if not REPORTER_CHARS[0] <= len(out) <= REPORTER_CHARS[1]:
        lo, hi = REPORTER_CHARS
        raise SystemExit(f"the reporter's input has {len(out)} characters, not {lo} to {hi}")
    return out


def long_text(texts, tokenize):
    """The texts joined by newlines until 1,000 tokens (as pii_cli bench builds its long input)."""
    out = ""
    for t in texts * 10:
        out += t + "\n"
        if len(tokenize(out)) >= 1000:
            return out
    return out


def median_ms(detect, text, n=5):
    """The median of n timed calls, in ms."""
    runs = []
    for _ in range(n):
        t0 = time.perf_counter()
        detect(text)
        runs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(runs)


def wheel_worker(model_dir, size, variant, texts_path, workloads="short,long"):
    from newmean_pii import Redactor, _core

    with open(texts_path, encoding="utf-8") as f:
        texts = json.load(f)
    want = workloads.split(",")
    r = Redactor(size, variant, model_dir=model_dir)
    short = [t for t in texts if len(r.tokenize(t)) <= 64] if "short" in want else []
    big = long_text(texts, r.tokenize) if "long" in want else None
    rep = reporter_text(texts) if "reporter" in want else None
    for t in short:  # warm-up, every workload before any is timed
        r.detect(t)
    for t in (big, rep):
        if t is not None:
            r.detect(t)
    # every workload is timed before anything else runs (the token counts come after)
    if short:
        reps = 3
        t0 = time.perf_counter()
        for _ in range(reps):
            for t in short:
                r.detect(t)
        short_ms = (time.perf_counter() - t0) * 1000 / reps / len(short)
    long_ms = median_ms(r.detect, big) if big is not None else None
    rep_ms = median_ms(r.detect, rep) if rep is not None else None
    out = {"build": _core.build_info()}
    if short:
        out.update(short_texts=len(short), short_avg_tokens=sum(len(r.tokenize(t)) for t in short) / len(short),
                   short_ms=short_ms)
    if big is not None:
        out.update(long_tokens=len(r.tokenize(big)), long_ms=long_ms)
    if rep is not None:
        out.update(reporter_chars=len(rep), reporter_tokens=len(r.tokenize(rep)), reporter_ms=rep_ms)
    return out


def env_for(kernel):
    env = dict(os.environ)
    env.pop("NEWMEAN_PII_KERNEL", None)
    if kernel != "-":
        env["NEWMEAN_PII_KERNEL"] = kernel
    return env


def run(cfg, a, texts_path, lines_path):
    kind, label, exe, kernel = cfg
    if kind == "wheel":
        p = subprocess.run([exe, os.path.abspath(__file__), "--worker", a.model_dir, a.size, a.variant, texts_path,
                            ",".join(a.workloads)], env=env_for(kernel), capture_output=True, text=True, check=True)
        return json.loads(p.stdout.strip().splitlines()[-1])
    d = a.model_dir
    with open(lines_path, "rb") as f:
        p = subprocess.run([exe, "bench", os.path.join(d, "onnx", f"model_{a.variant}.onnx"), os.path.join(d, "tokenizer.web.json")],
                           stdin=f, env=env_for(kernel), capture_output=True, check=True)
    b = json.loads(p.stdout.decode().strip().splitlines()[-1])
    return {"build": b["build"], "short_texts": b["short_texts"], "short_avg_tokens": b["short_avg_tokens"],
            "short_ms": b["ms_per_short_text"], "long_tokens": b["long_tokens"], "long_ms": b["ms_long_median"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--size", default="nano")
    ap.add_argument("--variant", default="q4")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--wheel", nargs=3, action="append", default=[], metavar=("LABEL", "PYTHON", "KERNEL"))
    ap.add_argument("--cli", nargs=3, action="append", default=[], metavar=("LABEL", "PII_CLI", "KERNEL"))
    ap.add_argument("--workloads", default="short,long", help="comma-separated: short, long, reporter")
    ap.add_argument("--require", action="append", default=[])
    a = ap.parse_args()
    asked = a.workloads.split(",")
    if not set(asked) <= set(WORKLOADS):
        sys.exit(f"--workloads {a.workloads!r}: want a comma-separated list of {', '.join(WORKLOADS)}")
    a.workloads = [w for w in WORKLOADS if w in asked]  # always in this order
    if (a.cli or a.require) and not {"short", "long"} <= set(a.workloads):
        sys.exit("--cli and --require need the workloads short and long")
    configs = [("wheel", *w) for w in a.wheel] + [("cli", *c) for c in a.cli]
    sys.path.insert(0, HERE)
    import test_kernel_isa as K

    texts = K.make_base_texts()
    rep = reporter_text(texts) if "reporter" in a.workloads else None
    with tempfile.TemporaryDirectory() as tmp:
        texts_path, lines_path = os.path.join(tmp, "texts.json"), os.path.join(tmp, "texts.jsonl")
        with open(texts_path, "w", encoding="utf-8") as f:
            json.dump(texts, f, ensure_ascii=False)
        with open(lines_path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps({"text": t}, ensure_ascii=False) + "\n" for t in texts)
        rounds = {c[1]: [] for c in configs}
        for i in range(a.rounds):
            for cfg in configs if i % 2 == 0 else configs[::-1]:
                rounds[cfg[1]].append(run(cfg, a, texts_path, lines_path))
    out = {"cpu": cpu(), "model": f"{a.size} {a.variant}", "rounds": a.rounds, "workloads": a.workloads, "configs": {}}
    if rep is not None:
        # the input itself, so that runs can be compared; its tokens are per configuration (the model's tokenizer)
        out["reporter"] = {"chars": len(rep), "lines": rep.count("\n") + 1,
                           "sha256": hashlib.sha256(rep.encode("utf-8")).hexdigest(),
                           "threads": 1, "runs_per_round": 5}  # detect() runs on the one calling thread
    for kind, label, exe, kernel in configs:
        rs = rounds[label]
        c = out["configs"][label] = {"kind": kind, "kernel_env": kernel, "build": sorted({r["build"] for r in rs})}
        if "short_ms" in rs[0]:
            c.update(short_texts=rs[0]["short_texts"], short_avg_tokens=round(rs[0]["short_avg_tokens"], 1),
                     short_ms_median=round(statistics.median(r["short_ms"] for r in rs), 3),
                     short_ms=[round(r["short_ms"], 3) for r in rs])
        if "long_ms" in rs[0]:
            c.update(long_tokens=rs[0]["long_tokens"],
                     long_ms_median=round(statistics.median(r["long_ms"] for r in rs), 2),
                     long_ms=[round(r["long_ms"], 2) for r in rs])
        if "reporter_ms" in rs[0]:  # wheel configurations only: pii_cli bench has no such input
            c.update(reporter_chars=rs[0]["reporter_chars"], reporter_tokens=rs[0]["reporter_tokens"],
                     reporter_ms_median=round(statistics.median(r["reporter_ms"] for r in rs), 2),
                     reporter_ms=[round(r["reporter_ms"], 2) for r in rs])
    failed = []
    for req in a.require:
        m = re.fullmatch(r"([^/]+)/([^/>=]+)(>=|>)([0-9.]+)", req)
        if not m:
            sys.exit(f"--require {req!r}: want A/B>=X or A/B>X")
        fast, slow, op, x = out["configs"][m.group(1)], out["configs"][m.group(2)], m.group(3), float(m.group(4))
        for w in ("short_ms_median", "long_ms_median"):
            ratio = slow[w] / fast[w]
            out.setdefault("ratios", {})[f"{req} {w}"] = round(ratio, 3)
            if ratio < x or (op == ">" and ratio == x):
                failed.append(f"{req}: {w} {ratio:.3f}")
    out["failed"] = failed
    print(json.dumps(out, indent=1))
    sys.exit(1 if failed else 0)


def p3_note(path, label):
    """One line on the two P3 ratios of LABEL in a bench output, with a GitHub annotation when one is under 0.95."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        return f"::error::P3 {label}: no bench output to read ({type(e).__name__}); the bench failed before it measured"
    model, failed = d.get("model", "?"), d.get("failed", [])
    ratios = {k: v for k, v in d.get("ratios", {}).items() if k.startswith((f"{label}/base>=", f"{label}/nokernel>="))}
    low = {b: [x for x in failed if x.startswith(f"{label}/{b}>=")] for b in ("base", "nokernel")}
    shown = ", ".join(f"{k} {v}" for k, v in sorted(ratios.items()))
    if low["nokernel"]:
        return (f"::error::P3 {model}: {label} is below 0.95 of this commit without the kernel, a slowdown of the kernel "
                f"change itself{' (and below 0.95 of the 1.6.1 release)' if low['base'] else ''}: {shown}")
    if low["base"]:
        return (f"::error::P3 {model}: {label} is below 0.95 of the 1.6.1 release (KERNEL_BASE) but not of this commit "
                f"without the kernel: an end-to-end regression of detect() since 1.6.1. P3 fails; record the regression "
                f"and investigate its cause (the matched ratio passing does not place it outside the kernel; the kernel "
                f"base job prints the diff of python/src since KERNEL_BASE): {shown}")
    return f"P3 {model}: {label} at least 0.95 of both bases: {shown}"


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--worker":
        print(json.dumps(wheel_worker(*sys.argv[2:7])))
    elif len(sys.argv) == 4 and sys.argv[1] == "--p3-note":
        print(p3_note(sys.argv[2], sys.argv[3]))
    else:
        main()
