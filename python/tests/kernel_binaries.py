# Inspection of final x86-64 binaries for the matrix kernels (issue #3, criteria D6): disassembles a shared library,
# executable, object file or static library with objdump (GNU or LLVM) and checks, function by function:
#   - VEX and EVEX instructions (AVX, AVX2, FMA: a mnemonic starting with "v", a ymm or zmm register) only inside the 8-wide
#     kernel functions (gemm_avx2, gemm16_avx2, gemm_avx2fma, gemm16_avx2fma, and compiler clones of them), so every other
#     function runs on the x86-64 baseline;
#   - fused multiply-add only in gemm_avx2fma and gemm16_avx2fma, none in the plain kernels (no unintended contraction);
#   - no instruction above the platform's baseline outside the kernels (--baseline: x86-64 allows nothing above SSE2; penryn,
#     Apple's macOS target, SSE4.1; android, the NDK's x86_64 target, SSE4.2 and POPCNT; LZCNT, MOVBE, BMI1, BMI2 and ADX
#     never), apart from XGETBV, which the kernel choice runs only when CPUID reports OSXSAVE;
#   - aligned vector moves in the kernels only on the stack (%rsp or %rbp based, aligned by the compiler); unaligned loads
#     and stores and compiler-placed vzeroupper are allowed.
#   python tests/kernel_binaries.py BINARY [--kernels plain,fused | plain | none] [--baseline x86-64 | penryn | android]
#       [--base BINARY] [--ignore REGEX] [--objdump CMD]
# --kernels: the kernel functions that must be present (default plain,fused; "none" for a build without the 8-wide
# kernels, which must then have no VEX instruction at all). --base: the same binary built from the code before the change;
# the mnemonics outside the kernels that it lacks are listed (for the record, not a failure). --ignore: functions not
# from the native core (the C runtime, the unwinder) left out. Prints a JSON summary; exit 1 on a failed check.
import argparse
import collections
import json
import re
import shlex
import subprocess
import sys

KERNEL = re.compile(r"^_?(gemm(?:16)?_avx2(?:fma)?)([.$].*)?$")
FUSED_KERNEL = re.compile(r"^_?gemm(?:16)?_avx2fma([.$].*)?$")
FMA = re.compile(r"^v?f(n)?m(add|sub|addsub|subadd)\d{3}[ps][sd]$")
PREFIXES = {"lock", "rep", "repe", "repz", "repne", "repnz", "notrack", "bnd", "data16", "addr32", "cs", "ds", "es", "fs",
            "gs", "ss", "rex", "rex.w", "{vex}", "{vex3}", "{evex}"}
HEADER = re.compile(r"^[0-9a-fA-F]+ <(.+)>:$")
LINE = re.compile(r"^\s*[0-9a-fA-F]+:\s*(.*)$")
RAW = re.compile(r"^(?:[0-9a-fA-F]{2} )+\s*")


# Instructions above SSE2 by extension; each baseline allows some of them: x86-64 (Linux, Windows) none, penryn (what Apple's
# compilers target on macOS x86-64) SSE3 to SSE4.1, android (the NDK's x86_64 target) SSE3 to SSE4.2 and POPCNT.
SSE3 = set("addsubps addsubpd haddps haddpd hsubps hsubpd lddqu movddup movshdup movsldup fisttp monitor mwait".split())
SSSE3 = set("pabsb pabsw pabsd palignr phaddw phaddd phaddsw phsubw phsubd phsubsw pmaddubsw pmulhrsw pshufb psignb psignw "
            "psignd".split())
SSE41 = set("blendpd blendps blendvpd blendvps dppd dpps extractps insertps movntdqa mpsadbw packusdw pblendvb pblendw pcmpeqq "
            "pextrb pextrd pextrq pinsrb pinsrd pinsrq phminposuw pmaxsb pmaxsd pmaxud pmaxuw pminsb pminsd pminud pminuw pmuldq "
            "pmulld ptest roundpd roundps roundsd roundss pmovsx pmovzx".split())  # pmovsx/pmovzx: every width
SSE42 = set("crc32 pcmpestri pcmpestrm pcmpistri pcmpistrm pcmpgtq popcnt".split())
OTHER = set("lzcnt movbe andn bextr blsi blsmsk blsr bzhi mulx pdep pext rorx sarx shlx shrx adcx adox".split())
ABOVE_SSE2 = SSE3 | SSSE3 | SSE41 | SSE42 | OTHER
BASELINES = {"x86-64": set(), "penryn": SSE3 | SSSE3 | SSE41, "android": SSE3 | SSSE3 | SSE41 | SSE42}


def above_baseline(m, allowed):
    names = {m}
    if m[-1:] in ("b", "w", "l", "q"):
        names.add(m[:-1])  # AT&T size suffix (popcntl, crc32b)
    if m.startswith(("pmovsx", "pmovzx")):
        names.add(m[:6])
    return any(n in ABOVE_SSE2 and n not in allowed for n in names)


def disassemble(path, objdump):
    p = subprocess.run([*shlex.split(objdump), "-d", "--no-show-raw-insn", path], capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"{objdump} -d {path}: {p.stderr.strip()[:2000]}")
    funcs = collections.OrderedDict()  # function -> [(mnemonic, operands)]
    cur = None
    for line in p.stdout.splitlines():
        h = HEADER.match(line.strip())
        if h:
            cur = h.group(1)
            funcs.setdefault(cur, [])
            continue
        m = LINE.match(line)
        if not m or cur is None:
            continue
        text = RAW.sub("", m.group(1)).strip()
        if not text or text.startswith("(bad)") or text.startswith("..."):
            continue
        parts = text.split()
        while parts and parts[0].lower() in PREFIXES:
            parts = parts[1:]
        if not parts:
            continue
        funcs[cur].append((parts[0].lower(), " ".join(parts[1:])))
    return funcs


def vex(mnemonic, operands):
    return (mnemonic.startswith("v") and mnemonic not in ("verr", "verw")) or "%ymm" in operands or "%zmm" in operands


def check(path, kernels, objdump, ignore, baseline):
    funcs = disassemble(path, objdump)
    if not funcs:
        sys.exit(f"no code found in {path}")
    failures, found, outside = [], {}, collections.Counter()
    xgetbv = []
    for name, insns in funcs.items():
        if ignore and ignore.search(name):
            continue
        is_kernel = bool(KERNEL.match(name))
        if is_kernel:
            base = KERNEL.match(name).group(1)
            info = found.setdefault(base, {"functions": [], "instructions": 0, "vex": 0, "fma": 0, "vzeroupper": 0,
                                           "aligned_stack": 0})
            info["functions"].append(name)
            for mn, ops in insns:
                info["instructions"] += 1
                info["vex"] += vex(mn, ops)
                info["fma"] += bool(FMA.match(mn))
                info["vzeroupper"] += mn == "vzeroupper"
                if re.match(r"^v?(movaps|movapd|movdqa|movdqa32|movdqa64|movntps|movntpd|movntdq)$", mn) and "(" in ops:
                    if re.search(r"\(%[re](sp|bp)[),]", ops):
                        info["aligned_stack"] += 1
                    else:
                        failures.append(f"{name}: aligned access not on the stack: {mn} {ops}")
                if FMA.match(mn) and not FUSED_KERNEL.match(name):
                    failures.append(f"{name}: fused multiply-add in a plain kernel: {mn} {ops}")
            continue
        for mn, ops in insns:
            outside[mn] += 1
            if mn == "xgetbv":
                xgetbv.append(name)
            elif vex(mn, ops):
                failures.append(f"{name}: VEX/EVEX instruction outside the kernels: {mn} {ops}")
            elif FMA.match(mn):
                failures.append(f"{name}: fused multiply-add outside the kernels: {mn} {ops}")
            elif above_baseline(mn, BASELINES[baseline]):
                failures.append(f"{name}: instruction above the {baseline} baseline outside the kernels: {mn} {ops}")
    want = {"plain,fused": {"gemm_avx2", "gemm16_avx2", "gemm_avx2fma", "gemm16_avx2fma"},
            "plain": {"gemm_avx2", "gemm16_avx2"}, "none": set()}[kernels]
    got = set(found)
    if got != want:
        failures.append(f"kernel functions {sorted(got)}, expected {sorted(want)}")
    for base, info in found.items():
        if base.endswith("fma") and not info["fma"]:
            failures.append(f"{base}: no fused multiply-add in the fused kernel")
        if not info["vex"]:
            failures.append(f"{base}: no AVX instruction in an 8-wide kernel")
    return {"binary": path, "functions": len(funcs), "kernels": found, "xgetbv_in": sorted(set(xgetbv)),
            "failures": failures[:50], "failure_count": len(failures)}, set(outside)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("--kernels", default="plain,fused", choices=("plain,fused", "plain", "none"))
    ap.add_argument("--base")
    ap.add_argument("--ignore")
    ap.add_argument("--objdump", default="objdump")
    ap.add_argument("--baseline", default="x86-64", choices=sorted(BASELINES))
    a = ap.parse_args()
    ignore = re.compile(a.ignore) if a.ignore else None
    out, mnemonics = check(a.binary, a.kernels, a.objdump, ignore, a.baseline)
    out["baseline"] = a.baseline
    if a.base:
        funcs = disassemble(a.base, a.objdump)
        base_mn = {mn for name, insns in funcs.items() if not (ignore and ignore.search(name)) for mn, _ in insns}
        out["new_mnemonics_outside_kernels"] = sorted(mnemonics - base_mn)
    print(json.dumps(out, indent=1))
    sys.exit(1 if out["failure_count"] else 0)


if __name__ == "__main__":
    main()
