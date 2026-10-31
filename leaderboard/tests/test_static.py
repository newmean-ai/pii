"""The static page: its logic passes the node tests, its submission checks give the same answers (same messages) as the
Python checks the weekly job runs, and the request body it posts parses in the job."""
import json, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

LB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LB))
from pii_leaderboard.forum import MARKER, parse_request_body, request_body  # noqa: E402
from pii_leaderboard.submit import Rejected, validate_submission  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node not installed")
CFG = {"architectures": ["BertForTokenClassification"], "id2label": {"0": "O", "1": "B-PER", "2": "I-PER", "3": "B-EMAIL", "4": "I-EMAIL"}}
GOOD = json.dumps({"PER": "PERSON", "EMAIL": "EMAIL"})
# (fields, hub fixture): one problem each, so both implementations stop at the same check
CASES = [
    ({"model": "org/ner", "label_map": GOOD}, {}),
    ({"model": "no-slash", "label_map": GOOD}, {}),
    ({"model": "org/ner", "label_map": GOOD}, {"private": True}),
    ({"model": "org/ner", "label_map": GOOD}, {"gated": "auto"}),
    ({"model": "org/ner", "label_map": GOOD}, {"files": {"config.json": 1, "pytorch_model.bin": 10}}),
    ({"model": "org/ner", "label_map": GOOD}, {"files": {"config.json": 1, "model.safetensors": 4e9}}),
    ({"model": "org/ner", "label_map": GOOD}, {"files": {"model.safetensors": 10}}),
    ({"model": "org/ner", "label_map": GOOD}, {"cfg": {**CFG, "auto_map": {"AutoModel": "x.Y"}}}),
    ({"model": "org/ner", "label_map": GOOD}, {"cfg": {**CFG, "architectures": ["BertModel"]}}),
    ({"model": "org/ner", "label_map": json.dumps({"PER": "PERSON"})}, {}),
    ({"model": "org/ner", "label_map": json.dumps({"PER": "PERSON", "EMAIL": "EMAIL", "LOC": "ORG"})}, {}),
    ({"model": "org/ner", "label_map": json.dumps({"PER": "NAME", "EMAIL": "EMAIL"})}, {}),
    ({"model": "org/ner", "label_map": json.dumps({"PER": "ignore", "EMAIL": "ignore"})}, {}),
    ({"model": "org/ner", "label_map": "auto"}, {}),
    ({"model": "org/ner", "adapter": "gliner"}, {}),
    ({"model": "org/ner", "adapter": "newmean-onnx"}, {}),
    ({"model": "newmean-ai/pii-large", "adapter": "newmean-onnx"}, {"files": {"onnx/model_q4.onnx": 1, "onnx/model_q4e.onnx": 1}}),
    ({"model": "newmean-ai/pii-medium", "adapter": "newmean-onnx"}, {"files": {"onnx/model_q4.onnx": 1}}),
]


def py_check(fields, fx):
    files = fx.get("files", {"config.json": 900, "model.safetensors": 4e8})
    api = SimpleNamespace(model_info=lambda m, revision=None, files_metadata=False: SimpleNamespace(
        sha="a" * 40, private=fx.get("private", False), gated=fx.get("gated", False),
        siblings=[SimpleNamespace(rfilename=f, size=s) for f, s in files.items()]))
    try:
        req = validate_submission([], set(), api, lambda r, f, v: fx.get("cfg", CFG), "alice",
                                  {"revision": "main", "adapter": "transformers", **fields}, datetime.now(timezone.utc))
        return {"ok": {k: req[k] for k in ("model", "revision", "adapter", "variant", "label_map")}}
    except Rejected as e:
        return {"error": str(e)}


JS = """
import * as lb from "%s";
const cases = JSON.parse(await new Response(process.stdin).text());
const out = [];
for (const [f, fx] of cases) {
  const files = fx.files ?? {"config.json": 900, "model.safetensors": 4e8};
  const fetchJson = async (url) => url.includes("/api/models/")
    ? {sha: "a".repeat(40), private: fx.private ?? false, gated: fx.gated ?? false, siblings: Object.entries(files).map(([rfilename, size]) => ({rfilename, size}))}
    : (fx.cfg ?? %s);
  try { out.push({ok: await lb.checkModel(fetchJson, {revision: "main", adapter: "transformers", ...f, labelMap: f.label_map})}); }
  catch (e) { out.push({error: e.message}); }
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_node_unit_tests():
    p = subprocess.run([NODE, "--test", str(LB / "tests/js/lb.test.mjs")], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:]


@needs_node
def test_browser_and_job_checks_agree():
    script = JS % ((LB / "lb.js").as_uri(), json.dumps(CFG))
    p = subprocess.run([NODE, "--input-type=module", "-e", script], input=json.dumps(CASES), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    js = json.loads(p.stdout)
    for case, a in zip(CASES, js):
        assert a == py_check(*case), (case, a, py_check(*case))


@needs_node
def test_posted_body_parses_in_the_job():
    fields = {"model": "org/ner", "revision": "a" * 40, "adapter": "transformers", "variant": None, "label_map": {"PER": "PERSON"}}
    js = subprocess.run([NODE, "--input-type=module", "-e",
                         f'import * as lb from "{(LB / "lb.js").as_uri()}"; process.stdout.write(lb.requestBody({json.dumps(fields)}))'],
                        capture_output=True, text=True, check=True).stdout
    assert parse_request_body(js) == fields
    assert parse_request_body(request_body(fields)) == fields
    assert parse_request_body("an ordinary discussion") is None


def test_space_files():
    readme = (LB / "README.md").read_text()
    assert "sdk: static" in readme and "hf_oauth: true" in readme and "write-discussions" in readme and "app_file: index.html" in readme
    html = (LB / "index.html").read_text()
    assert './lb.js"' in html and "@huggingface/hub@2.17.6" in html and MARKER not in html   # the marker comes from lb.js only
    assert MARKER in (LB / "lb.js").read_text()
