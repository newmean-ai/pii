# Reference logits digests for test_kernel_isa.py, from the native core as it was before the x86-64 kernel choice
# (--ref: the 1.6.1 release, public main 4b63a4c142aab3f35b202fc07254f3a626430beb, KERNEL_BASE in
# .github/workflows/python-wheels.yml, which sets the environment variable KERNEL_BASE for every job). The core of
# that commit is taken from git, gets the test-only pii_model_logits of today's api.c (it reads the model's windows
# and calls the engine; it does not touch the kernels), and is built with today's tests/pii_cli.c
# (-DPII_REFERENCE_BUILD) once per compiler and flag set. Each build runs "pii_cli logits" on the texts of
# test_kernel_isa.py; builds of the same kind ("+fma" or not) must agree, and with --check every build must give the
# pins of its kind.
#
#   python tests/kernel_refs.py --ref KERNEL_BASE     arm64: plain from -DPII_NO_FMA and -DPII_SCALAR, fused from the
#                                                     default build; prints PINS and WITNESS for test_kernel_isa.py
#   python tests/kernel_refs.py --ref KERNEL_BASE --check --full --require-builds 7 \
#       --build "clang:-DPII_NO_FMA" --build "clang:-DPII_SCALAR" --build "clang:-DPII_NO_FMA -DPII_WIDE=0" \
#       --build "gcc-16:-DPII_NO_FMA" --build "gcc-16:-DPII_SCALAR" --build "clang:" --build "gcc-16:"
#                                                     arm64, Apple clang and GCC: the seven builds the pins were made
#                                                     with (five plain, two fused), each of which must give them
#   python tests/kernel_refs.py --check --require-builds 2 \
#       --build "cc:-march=x86-64-v3 -DPII_FMA" --build "cc:"
#                                                     x86-64 runners: the reference sse+fma and sse builds must give the
#                                                     pinned digests
#   python tests/kernel_refs.py --ref . --check   the same builds of the working tree must give the pins
# Options: --build CC:FLAGS (repeatable; default: the three builds of the first line, with cc), --require-builds N (exactly
# N --build options, so the defaults never stand in for them, and a result from each: a build that does not compile or
# run fails the check instead of being left out), --full (also medium q4e and the eight-pair smoke set), --models DIR
# (default PII_MODELS or js/test/models), --ref SHA (or "." for the working tree; default: the environment variable
# KERNEL_BASE, else required), --repo DIR. With --check the output lists every build with its compiler, flags, kernel,
# kind, digests and mismatches; the exit status is 0 only when every build ran and gave the pins.
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_kernel_isa as K  # noqa: E402

MARK = "// internal.h. The windows of model_spans"
PAIRS_FULL = [("nano", "q4"), ("medium", "q4e")]
PAIRS_SMOKE = [(s, v) for s in ("nano", "small", "medium", "large") for v in ("q4", "q4e")]


def reference_tree(repo, ref, dest):
    """native/core of ref under dest, with today's pii_model_logits and pii_cli.c ("." : the working tree as it is)."""
    if ref == ".":
        return os.path.join(repo, "native", "core")
    archive = subprocess.run(["git", "-C", repo, "archive", ref, "native/core"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", dest], input=archive, check=True)
    core, now = os.path.join(dest, "native", "core"), os.path.join(repo, "native", "core")
    with open(os.path.join(now, "src", "api.c"), encoding="utf-8") as f:
        api = f.read()
    with open(os.path.join(now, "src", "internal.h"), encoding="utf-8") as f:
        header = f.read()
    body = api[api.index(MARK):]
    start = header.index("// Logits of every window the model runs")
    decl = header[start:header.index(";", header.index("int pii_model_logits(", start)) + 1]
    with open(os.path.join(core, "src", "api.c"), "a", encoding="utf-8") as f:
        f.write("\n" + body)
    path = os.path.join(core, "src", "internal.h")
    with open(path, encoding="utf-8") as f:
        old = f.read()
    end = old.rindex("#endif")
    with open(path, "w", encoding="utf-8") as f:
        f.write(old[:end] + decl + "\n\n" + old[end:])
    with open(os.path.join(now, "tests", "pii_cli.c"), encoding="utf-8") as f:
        cli = f.read()
    with open(os.path.join(core, "tests", "pii_cli.c"), "w", encoding="utf-8") as f:
        f.write(cli)
    return core


def build_cli(core, cc, flags, out, reference=True):
    srcs = sorted(os.path.join(core, "src", n) for n in os.listdir(os.path.join(core, "src")) if n.endswith(".c"))
    cmd = [cc, "-std=c11", "-O3", "-Wall", "-ffp-contract=off", "-fno-math-errno", "-D_POSIX_C_SOURCE=200809L",
           *(["-DPII_REFERENCE_BUILD"] if reference else []), *shlex.split(flags), *srcs, os.path.join(core, "tests", "pii_cli.c"), "-lpthread", "-o", out]
    subprocess.run(cmd, check=True)
    return cmd


def run_cli(cli, models, size, variant, lines):
    d = os.path.join(models, f"pii-{size}")
    with open(lines, "rb") as f:
        p = subprocess.run([cli, "logits", os.path.join(d, "onnx", f"model_{variant}.onnx"), os.path.join(d, "tokenizer.web.json")],
                           stdin=f, capture_output=True, check=True)
    rows = [json.loads(x) for x in p.stdout.decode().strip().splitlines()]
    return rows[-1]["build"], [r["sha256"] for r in rows[:-1]], rows[-1]["sha256"]


def write_lines(path, texts):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({"text": t}, ensure_ascii=False) + "\n" for t in texts)


def as_pins(got, kind):
    """One build's digests in the form of PINS (pair -> {kind, "smoke_" + kind}) and its witness."""
    pins = {}
    for pair, v in got.items():
        p = pins.setdefault(pair, {})
        if "full" in v:
            p[kind] = v["full"]
        if "smoke" in v:
            p["smoke_" + kind] = v["smoke"]
    return pins, got[("nano", "q4")]["witness"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="append", help="CC:FLAGS")
    ap.add_argument("--require-builds", type=int, metavar="N", help="exactly N --build options, each of which must give a result")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--models", default=os.environ.get("PII_MODELS", os.path.join(HERE, "..", "..", "js", "test", "models")))
    ap.add_argument("--ref", default=os.environ.get("KERNEL_BASE") or None)
    ap.add_argument("--repo", default=os.path.join(HERE, "..", ".."))
    a = ap.parse_args()
    if not a.ref:
        ap.error("--ref: the commit before the change (KERNEL_BASE in .github/workflows/python-wheels.yml) or .")
    if a.require_builds is not None and (a.require_builds < 1 or len(a.build or []) != a.require_builds):
        ap.error(f"--require-builds {a.require_builds}: {len(a.build or [])} --build options given; name each build")
    builds = a.build or ["cc:-DPII_NO_FMA", "cc:-DPII_SCALAR", "cc:"]
    from newmean_pii import MODEL_VERSION, Redactor

    r = Redactor("nano", "q4", model_dir=os.path.join(a.models, "pii-nano"))
    texts = K.make_texts(r.tokenize)
    tokens = [len(r.tokenize(t)) for t in texts]
    assert K.coverage_gaps(tokens) == [], K.coverage_gaps(tokens)
    smoke = K.smoke_indices(texts)
    pairs = PAIRS_FULL if a.full else PAIRS_FULL[:1]
    smoke_pairs = PAIRS_SMOKE if a.full else []
    done = []  # (log entry, pair -> {"full", "smoke", "witness"}) of every build that compiled and ran
    log = []
    with tempfile.TemporaryDirectory() as tmp:
        lines, smoke_lines = os.path.join(tmp, "texts.jsonl"), os.path.join(tmp, "smoke.jsonl")
        write_lines(lines, texts)
        write_lines(smoke_lines, [texts[i] for i in smoke])
        core = reference_tree(os.path.abspath(a.repo), a.ref, tmp)
        for k, spec in enumerate(builds):
            cc, _, flags = spec.partition(":")
            entry = {"spec": spec, "cc": cc, "flags": flags}
            log.append(entry)
            try:
                entry["version"] = (subprocess.run([cc, "--version"], capture_output=True, text=True).stdout.splitlines() or ["?"])[0]
                cli = os.path.join(tmp, f"pii_cli_{k}")
                cmd = build_cli(core, cc, flags, cli, reference=a.ref != ".")
                entry["command"] = " ".join(os.path.basename(x) if x.startswith(tmp) else x for x in cmd)
                got = {}
                for pair in pairs:
                    build, per_text, full = run_cli(cli, a.models, *pair, lines)
                    got[pair] = {"full": full, "witness": per_text[0] if pair == ("nano", "q4") else None}
                for pair in smoke_pairs:
                    build, _, digest = run_cli(cli, a.models, *pair, smoke_lines)
                    got.setdefault(pair, {})["smoke"] = digest
            except (OSError, subprocess.CalledProcessError, ValueError, KeyError, IndexError) as e:
                entry["error"] = f"{type(e).__name__}: {e}"
                print(json.dumps(entry), file=sys.stderr)
                continue
            entry["build"] = build
            entry["kind"] = "fused" if build.endswith("+fma") else "plain"
            pins, entry["witness"] = as_pins(got, entry["kind"])
            entry["digests"] = {f"{s}/{v}": p for (s, v), p in pins.items()}
            done.append((entry, got))
            print(json.dumps({x: entry[x] for x in ("spec", "version", "build", "kind")}), file=sys.stderr)
    errors = [f"{e['spec']}: {e['error']}" for e in log if "error" in e]
    if a.check:
        want, want_w = K.PINS.get(MODEL_VERSION, {}), K.WITNESS.get(MODEL_VERSION, {})
        bad = []
        for entry, got in done:
            pins, witness = as_pins(got, entry["kind"])
            entry["mismatches"] = [f"{s}/{v} {key}" for (s, v), p in pins.items() for key, d in p.items()
                                   if want.get((s, v), {}).get(key) != d]
            if want_w.get(entry["kind"]) != witness:
                entry["mismatches"].append(f"witness {entry['kind']}")
            bad += [f"{entry['spec']}: {m}" for m in entry["mismatches"]]
        if a.require_builds is not None and len(done) != a.require_builds:
            errors.append(f"{len(done)} of the {a.require_builds} required builds gave a result")
        print(json.dumps({"reference": a.ref, "model_release": MODEL_VERSION, "required_builds": a.require_builds,
                          "results": len(done), "builds": log, "texts": len(texts), "tokens": sum(tokens),
                          "errors": errors, "mismatches": bad}, indent=1))
        sys.exit(1 if bad or errors else 0)
    if errors:
        sys.exit("builds that gave no result: " + "; ".join(errors))
    first = {}  # kind -> the digests of the first build of that kind; every other build of the kind must equal them
    for entry, got in done:
        if entry["kind"] not in first:
            first[entry["kind"]] = got
        elif first[entry["kind"]] != got:
            sys.exit(f"{entry['spec']} ({entry['build']}) differs from the first {entry['kind']} build")
    pins, witness = {}, {}
    for kind, got in first.items():
        p, witness[kind] = as_pins(got, kind)
        for pair, v in p.items():
            pins.setdefault(pair, {}).update(v)
    print(json.dumps({"reference": a.ref, "model_release": MODEL_VERSION, "builds": log, "texts": len(texts),
                      "tokens": sum(tokens), "pins": {f"{s}/{v}": p for (s, v), p in pins.items()}, "witness": witness},
                     indent=1))


if __name__ == "__main__":
    main()
