"""Writes and checks vault_ci_expected.json, next to the data of the vault test tools: what each run of the public vault
workflow (.github/workflows/vault.yml) must show. A skip is a failure: each job compares, per environment and per file,
the case ids it ran and the numbers passed and skipped with this file. Fewer passed, a case id missing, or a skip outside
the short list below fails the job.

    python3 python/tests/gen_vault_ci_expected.py data [--write]
    python3 python/tests/gen_vault_ci_expected.py write ENV REPORT.xml [REPORT.xml ...]
    python3 python/tests/gen_vault_ci_expected.py check ENV REPORT.xml [REPORT.xml ...] [--summary FILE]

data    the cases of the three data files the generators write (vault_corpus.json, vault_fuzz.json,
        vault_boundary_cases.json): their number and the sha256 of their ids in file order. Without --write it fails when
        vault_ci_expected.json says otherwise (the vault workflow's generators job runs it after the generators).
write   records one environment (ENV, one of ENVS) from the JUnit reports of a complete run of it (vitest --reporter=junit,
        pytest --junitxml): per file the number passed and skipped. It refuses a run with a failure, a skip outside the
        list, or case ids other than the data's, so only a clean run of every case is ever written. The run that writes
        an environment is the local gate run of the same versions (public CI repeats it; it never explores).
check   compares a run of ENV with the file (and the data with the data section); exit code 1 on any difference that
        loses something. More passed than recorded passes with a note (write the environment again).

Case ids: the corpus and the boundary readings name each case in their test names, so every id the data holds (for the
Python corpus: every case with a position the Python runner takes) must be among the passed tests of that file. The
fuzz runners run every case of vault_fuzz.json inside their tests; their cases are the data section's.

Allowed skips (and nothing else): the tests of test_langfuse.py that need the model copies, which public CI does not
have ("no model copy"), the note of vault-boundary.test.ts about AI_SDK_DIR, which is skipped when the AI SDK is there,
and the timing tests of test_langfuse.py, which a functional step skips (PII_SKIP_TIMING=1, "timing test") and the
timing job runs alone (python-timing). Their number may not exceed the recorded one.

Timing tests (the 1.7.2 release standard, CI3): they never share a step with functional tests. Those in files of their
own (*growth*) run whole in the timing job; those inside functional files run there with PII_TIMING_ONLY=1 and
--allowOnly (vitest: the file's other tests are then skipped, and the functional jobs run them) or with -m timing
(pytest: the others are deselected). In a timing-only file the skips are the file's functional tests and are not
counted; the timing tests it must pass are the recorded number.

Standard library only (Python 3.9+).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = HERE / "vault_ci_expected.json"
DATA = {
    "vault_corpus.json": "cases",
    "vault_fuzz.json": "cases",
    "vault_boundary_cases.json": "readings",
}
# the environments of the vault workflow; the runner files each one checks
NPM_FILES = ["test/vault-corpus.test.ts", "test/vault-fuzz.test.ts", "test/vault-boundary.test.ts"]
PY_FILES = [
    "tests/test_vault_corpus.py", "tests/test_vault_fuzz.py", "tests/test_vault_boundary.py",
    "tests/test_vault_model_fields.py", "tests/test_langfuse.py",
]
ENVS = {
    "npm-lockfile": NPM_FILES,
    "npm-langchain-floor": NPM_FILES,
    "npm-langchain-current": NPM_FILES,
    "python-floor": PY_FILES,
    "python-current": PY_FILES,
    # the timing tests, each file in a step of its own (job timing)
    "npm-timing": [
        "test/vault-growth.test.ts", "test/langchain-growth.test.ts", "test/review-docs-growth.test.ts",
        "test/ai-sdk.test.ts", "test/langchain.test.ts", "test/regex.test.ts", "test/vault.test.ts",
    ],
    "python-timing": ["tests/test_vault_growth.py", "tests/test_review_docs_growth.py", "tests/test_langfuse.py"],
}
ALLOWED_SKIPS = {
    "tests/test_langfuse.py": [("message", "no model copy"), ("message", "timing test (PII_SKIP_TIMING=1)")],
    "test/vault-boundary.test.ts": [("name", "boundary: AI SDK > skipped: set AI_SDK_DIR")],
}
# functional files whose timing tests the timing job runs with PII_TIMING_ONLY=1 (their other tests show as skipped)
TIMING_ONLY = {"npm-timing": {"test/ai-sdk.test.ts", "test/langchain.test.ts", "test/regex.test.ts", "test/vault.test.ts"}}
NL = chr(10)


# ---------------------------------------------------------------------------------------------------- data


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def data_section():
    out = {}
    for name, key in DATA.items():
        ids = [c["id"] for c in load(name)[key]]
        out[name] = {"cases": len(ids), "ids_sha256": hashlib.sha256(NL.join(ids).encode("utf-8")).hexdigest()}
    return out


def expected_ids():
    """The case ids each runner file must show in its passed test names (files whose tests are named by case)."""
    corpus = load("vault_corpus.json")["cases"]
    readings = [r["id"] for r in load("vault_boundary_cases.json")["readings"]]
    return {
        "test/vault-corpus.test.ts": {c["id"] for c in corpus},
        # the Python runner leaves out message-options (the AI SDK's only)
        "tests/test_vault_corpus.py": {c["id"] for c in corpus if any(p != "message-options" for p in c["where"])},
        "test/vault-boundary.test.ts": set(readings),
        "tests/test_vault_boundary.py": set(readings),
    }


L, R = re.escape("["), re.escape("]")  # the brackets of the test names, as regular expression text
NPM_CORPUS = re.compile("^vault corpus through the (?:ai-sdk|langchain) middleware > (.+)@(?:ai-sdk|langchain):[a-z0-9-]+$")
PY_CORPUS = re.compile("^test_corpus" + L + "(.+):[a-z0-9-]+" + R + "$")
NPM_READING = re.compile(
    "^boundary: readings, .* > ([^ ]+) " + L + "[^" + R + "]*" + R + " / (?:user|tool text|tool JSON) / detector [^ ]+(?: / [^ ]+)?$"
)
PY_READING = re.compile("^test_reading_[a-z_]+" + L + "(?:guaranteed|best-effort)-([^" + L + "]+)" + L)
ID_OF = {
    "test/vault-corpus.test.ts": NPM_CORPUS,
    "tests/test_vault_corpus.py": PY_CORPUS,
    "test/vault-boundary.test.ts": NPM_READING,
    "tests/test_vault_boundary.py": PY_READING,
}


# ---------------------------------------------------------------------------------------------------- reports


def file_of(classname, name, files):
    """The runner file of a JUnit test case: vitest writes the file as classname, pytest the module path
    (tests.test_x, tests.test_x.Class)."""
    if classname in files:
        return classname
    for key in (classname, name):
        parts = (key or "").replace("/", ".").split(".")
        for n in range(len(parts), 0, -1):
            cand = "/".join(parts[:n]) + ".py"
            if cand in files:
                return cand
    return None


def read_reports(paths, files):
    """{file: {"passed": [names], "skipped": [(name, message)], "xfailed": n, "failed": [names]}}, plus the test cases
    that belong to no checked file ("other")."""
    out = {f: {"passed": [], "skipped": [], "xfailed": 0, "failed": []} for f in files}
    other = {"passed": 0, "skipped": [], "failed": []}
    for path in paths:
        for _event, el in ET.iterparse(path, events=("end",)):
            if el.tag != "testcase":
                continue
            classname, name = el.get("classname", ""), el.get("name", "")
            f = file_of(classname, name, files)
            failed = el.find("failure") is not None or el.find("error") is not None
            skip = el.find("skipped")
            row = out[f] if f else None
            if failed:
                (row["failed"] if row else other["failed"]).append(classname + " > " + name)
            elif skip is not None:
                kind, message = skip.get("type", ""), skip.get("message", "") or ""
                if kind == "pytest.xfail" and row:
                    row["xfailed"] += 1
                else:
                    (row["skipped"] if row else other["skipped"]).append((name if row else classname + " > " + name, message))
            elif row:
                row["passed"].append(name)
            else:
                other["passed"] += 1
            el.clear()
    return out, other


def allowed(f, name, message):
    return any((message if field == "message" else name).startswith(prefix) for field, prefix in ALLOWED_SKIPS.get(f, []))


def ids_in(f, names):
    pattern = ID_OF.get(f)
    if not pattern:
        return None
    out = set()
    for n in names:
        m = pattern.match(n)
        if m:
            out.add(m.group(1))
    return out


# ---------------------------------------------------------------------------------------------------- commands


def read_expected():
    if EXPECTED.exists():
        return json.loads(EXPECTED.read_text(encoding="utf-8"))
    return {"about": "", "data": {}, "runs": {}}


ABOUT = (
    "Written by gen_vault_ci_expected.py (data: from the three data files; runs: from a complete local run of each "
    "environment of .github/workflows/vault.yml, the same versions). The vault workflow fails a job whose run has "
    "fewer passed, a case id missing, or a skip outside the allowed list (see gen_vault_ci_expected.py)."
)


def write_expected(doc):
    doc["about"] = ABOUT
    doc["runs"] = {k: doc["runs"][k] for k in ENVS if k in doc["runs"]}
    EXPECTED.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False) + NL, encoding="utf-8")


def cmd_data(args):
    doc = read_expected()
    now = data_section()
    if args.write:
        doc["data"] = now
        write_expected(doc)
        print("data section written: " + ", ".join(f"{k} {v['cases']}" for k, v in now.items()))
        return 0
    if doc.get("data") != now:
        print("vault_ci_expected.json does not match the data files:")
        for k in DATA:
            if doc.get("data", {}).get(k) != now[k]:
                print(f"  {k}: expected {doc.get('data', {}).get(k)}, the tree has {now[k]}")
        return 1
    print("data: " + ", ".join(f"{k} {v['cases']} cases" for k, v in now.items()) + " (as recorded)")
    return 0


def problems_of(env, rows, other, recorded, files):
    """The differences that fail a run, and notes."""
    bad, notes = [], []
    want_ids = expected_ids()
    only = TIMING_ONLY.get(env, set())
    for f in files:
        row, rec = rows[f], recorded.get(f) if recorded is not None else None
        for n in row["failed"]:
            bad.append(f"{f}: failed: {n}")
        for name, message in row["skipped"]:
            if f not in only and not allowed(f, name, message):
                bad.append(f"{f}: skipped outside the allowed list: {name}" + (f" ({message})" if message else ""))
        if f in want_ids:
            got = ids_in(f, row["passed"])
            missing = sorted(want_ids[f] - got)
            extra = sorted(got - want_ids[f])
            if missing:
                bad.append(f"{f}: {len(missing)} case ids of the data not among the passed tests: " + ", ".join(missing[:10]))
            if extra and recorded is None:
                bad.append(f"{f}: {len(extra)} case ids not in the data: " + ", ".join(extra[:10]))
        if rec is not None:
            if len(row["passed"]) < rec["passed"]:
                bad.append(f"{f}: {len(row['passed'])} passed, fewer than the {rec['passed']} recorded")
            elif len(row["passed"]) > rec["passed"]:
                notes.append(f"{f}: {len(row['passed'])} passed, more than the {rec['passed']} recorded (write {env} again)")
            if f not in only and len(row["skipped"]) > rec["skipped"]:
                bad.append(f"{f}: {len(row['skipped'])} skipped, more than the {rec['skipped']} recorded")
            if row["xfailed"] != rec.get("xfailed", 0):
                bad.append(f"{f}: {row['xfailed']} expected failures (open lists), {rec.get('xfailed', 0)} recorded")
        elif not row["passed"]:
            bad.append(f"{f}: no test passed")
    for n in other["failed"]:
        bad.append(f"failed outside the checked files: {n}")
    return bad, notes


def table(env, rows, recorded, files):
    lines = [f"{env}:"]
    for f in files:
        row, rec = rows[f], (recorded or {}).get(f)
        want = f" of {rec['passed']} recorded" if rec else ""
        ids = ids_in(f, row["passed"])
        lines.append(f"  {f}: {len(row['passed'])} passed{want}, {len(row['skipped'])} skipped, {row['xfailed']} xfailed, "
                     f"{len(row['failed'])} failed" + (f", {len(ids)} case ids" if ids is not None else ""))
    return lines


def cmd_write(args):
    files = ENVS[args.env]
    rows, other = read_reports(args.reports, files)
    bad, _ = problems_of(args.env, rows, other, None, files)
    print(NL.join(table(args.env, rows, None, files)))
    if bad:
        print("not written:" + NL + NL.join("  " + b for b in bad))
        return 1
    doc = read_expected()
    if doc.get("data") != data_section():
        print("not written: the data section is not the tree's (run: data --write)")
        return 1
    doc["runs"][args.env] = {
        f: {"passed": len(rows[f]["passed"]), "skipped": len(rows[f]["skipped"]), "xfailed": rows[f]["xfailed"]} for f in files
    }
    write_expected(doc)
    print(f"{args.env} written to {EXPECTED.name}")
    return 0


def cmd_check(args):
    files = ENVS[args.env]
    doc = read_expected()
    recorded = doc.get("runs", {}).get(args.env)
    rows, other = read_reports(args.reports, files)
    bad, notes = problems_of(args.env, rows, other, recorded if recorded is not None else {}, files)
    if recorded is None:
        bad.append(f"vault_ci_expected.json has no environment {args.env}")
    if doc.get("data") != data_section():
        bad.append("the data files are not those of vault_ci_expected.json (gen_vault_ci_expected.py data)")
    lines = table(args.env, rows, recorded, files)
    lines.append(f"  other files: {other['passed']} passed, {len(other['skipped'])} skipped, {len(other['failed'])} failed")
    lines += ["  note: " + n for n in notes]
    lines += ["  FAIL: " + b for b in bad]
    lines.append(f"{args.env}: " + ("fail" if bad else "pass"))
    print(NL.join(lines))
    if args.summary:
        summary = {
            "env": args.env,
            "result": "fail" if bad else "pass",
            "files": {f: {"passed": len(rows[f]["passed"]), "skipped": [list(s) for s in rows[f]["skipped"]],
                          "xfailed": rows[f]["xfailed"], "failed": rows[f]["failed"], "recorded": (recorded or {}).get(f)} for f in files},
            "other": {"passed": other["passed"], "skipped": [list(s) for s in other["skipped"]], "failed": other["failed"]},
            "problems": bad,
            "notes": notes,
        }
        Path(args.summary).write_text(json.dumps(summary, indent=1, ensure_ascii=False) + NL, encoding="utf-8")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split(NL + NL)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data")
    d.add_argument("--write", action="store_true")
    for name in ("write", "check"):
        p = sub.add_parser(name)
        p.add_argument("env", choices=sorted(ENVS))
        p.add_argument("reports", nargs="+")
        if name == "check":
            p.add_argument("--summary", default="")
    args = ap.parse_args(argv)
    return {"data": cmd_data, "write": cmd_write, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
