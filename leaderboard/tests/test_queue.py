"""Submission checks, the weekly limit, and the scoring job end to end on a local store. The end-to-end run reads the public
test set and two newmean models from the Hub, or local copies (PII_LB_PUBLIC_FILE, PII_LB_MODEL_ROOT; see conftest.py)."""
import json, os, subprocess, sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

LB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LB))

from pii_leaderboard import config  # noqa: E402
from pii_leaderboard.evaluation import load_rows, score_predictions  # noqa: E402
from pii_leaderboard.scoring import regex_predict  # noqa: E402
from pii_leaderboard.store import LocalStore, iso, now  # noqa: E402
from pii_leaderboard.submit import Rejected, make_request, parse_label_map  # noqa: E402

CFG = {"architectures": ["BertForTokenClassification"], "id2label": {"0": "O", "1": "B-PER", "2": "I-PER", "3": "B-EMAIL", "4": "I-EMAIL"}}


class FakeApi:
    def __init__(self, files=None, private=False, gated=False):
        self.files = files if files is not None else {"config.json": 900, "model.safetensors": 400_000_000}
        self.private, self.gated = private, gated

    def model_info(self, model, revision=None, files_metadata=False):
        if model == "nobody/missing":
            raise FileNotFoundError(model)
        return SimpleNamespace(sha="a" * 40, private=self.private, gated=self.gated,
                               siblings=[SimpleNamespace(rfilename=f, size=s) for f, s in self.files.items()])


def dl(cfg=CFG):
    return lambda repo, filename, revision: cfg


GOOD_MAP = json.dumps({"PER": "PERSON", "EMAIL": "EMAIL"})


def submit(store, user="alice", model="org/ner", lm=GOOD_MAP, api=None, cfg=CFG, adapter="transformers", t=None):
    return make_request(store, api or FakeApi(), dl(cfg), user, model, "main", adapter, lm, t)


def test_label_map_parsing():
    assert parse_label_map("auto") == "auto" and parse_label_map("") == "auto"
    assert parse_label_map('{"PER": "PERSON", "LOC": "ignore"}') == {"PER": "PERSON", "LOC": "ignore"}
    for bad in ('{"PER": "NAME"}', "[1]", "{not json", '{"PER": "ignore"}'):
        with pytest.raises(Rejected):
            parse_label_map(bad)


def test_valid_submission_is_queued(tmp_path):
    store = LocalStore(tmp_path)
    req = submit(store)
    assert req["status"] == "pending" and req["revision"] == "a" * 40 and req["label_map"] == {"PER": "PERSON", "EMAIL": "EMAIL"}
    assert store.requests() == [req]


@pytest.mark.parametrize("kwargs,reason", [
    ({"user": None}, "sign in"),
    ({"model": "no-slash"}, "Hub id"),
    ({"model": "nobody/missing"}, "cannot read"),
    ({"api": FakeApi(private=True)}, "public"),
    ({"api": FakeApi(gated="auto")}, "public"),
    ({"api": FakeApi(files={"config.json": 1, "pytorch_model.bin": 10})}, "safetensors"),
    ({"api": FakeApi(files={"config.json": 1, "model.safetensors": 4e9})}, "limit"),
    ({"cfg": {**CFG, "auto_map": {"AutoModel": "x.Y"}}}, "trust_remote_code"),
    ({"cfg": {**CFG, "architectures": ["BertModel"]}}, "token-classification"),
    ({"lm": json.dumps({"PER": "PERSON"})}, r"missing \['EMAIL'\]"),
    ({"adapter": "gliner"}, "adapter"),
    ({"adapter": "newmean-onnx"}, "only for newmean-ai"),
])
def test_rejections(tmp_path, kwargs, reason):
    store = LocalStore(tmp_path)
    with pytest.raises(Rejected, match=reason):
        submit(store, **kwargs)
    assert store.requests() == []


def test_weekly_limit_and_duplicates(tmp_path):
    store = LocalStore(tmp_path)
    t = now()
    submit(store, model="org/a", t=t - timedelta(days=8))     # older than 7 days: does not count
    with pytest.raises(Rejected, match="already pending"):   # same model, same resolved revision
        submit(store, model="org/a", t=t)
    for i in range(config.MAX_PER_WEEK):
        submit(store, model=f"org/m{i}", t=t)
    with pytest.raises(Rejected, match="limit reached"):
        submit(store, model="org/z", t=t)
    submit(store, user="bob", model="org/z", t=t)             # per account


def test_newmean_variant_defaults_to_the_sdk_file(tmp_path):
    from pii_leaderboard.adapters import download_spec, variant_of
    assert {s: variant_of({"model": f"newmean-ai/pii-{s}"}) for s in ("large", "medium", "small", "nano")} == \
        {"large": "q4e", "medium": "q4e", "small": "q4", "nano": "q4"}   # js/src/redactor.ts DEFAULT_VARIANT
    assert download_spec({"adapter": "newmean-onnx", "model": "newmean-ai/pii-large", "revision": "v1.0.0"})[2][-1] == "onnx/model_q4e.onnx"
    store = LocalStore(tmp_path)
    api = FakeApi(files={"onnx/model_q4.onnx": 1, "onnx/model_q4e.onnx": 1})
    req = submit(store, model="newmean-ai/pii-large", adapter="newmean-onnx", lm=None, api=api)
    assert req["variant"] == "q4e" and req["label_map"] is None
    with pytest.raises(Rejected, match="model_q4e.onnx not found"):
        submit(store, model="newmean-ai/pii-medium", adapter="newmean-onnx", lm=None, api=FakeApi(files={"onnx/model_q4.onnx": 1}))


# --- intake of discussion submissions -----------------------------------------------------------------------------------
def load_job(monkeypatch):
    monkeypatch.setenv("PII_LB_CODE", str(LB))
    sys.path.insert(0, str(LB / "jobs"))
    import importlib
    import run_queue
    return importlib.reload(run_queue)


def discussion(num, author, created, fields, body=None):
    from pii_leaderboard.forum import request_body
    return {"num": num, "author": author, "created_at": created, "body": body or request_body(fields), "status": "open"}


def test_intake_validates_limits_and_replies(tmp_path, monkeypatch):
    rq = load_job(monkeypatch)
    from pii_leaderboard.forum import LocalForum
    t = lambda d: iso(now() - timedelta(days=d))  # noqa: E731
    f = lambda m: {"model": m, "revision": "main", "adapter": "transformers", "label_map": {"PER": "PERSON", "EMAIL": "EMAIL"}}  # noqa: E731
    ds = [discussion(1, "alice", t(3), f("org/a")), discussion(2, "alice", t(2), f("org/b")), discussion(3, "alice", t(1), f("org/c")),
          discussion(4, "bob", t(1), f("org/a")), discussion(5, "bob", t(1), None, body="<!-- pii-leaderboard-request v1 -->\nno block"),
          discussion(6, "carol", t(1), {**f("org/x"), "label_map": {"PER": "NAME"}}),
          {"num": 7, "author": "dave", "created_at": t(1), "body": "a question about the metric", "status": "open"}]
    (tmp_path / "discussions.json").write_text(json.dumps(ds))
    store, forum = LocalStore(tmp_path), LocalForum(tmp_path / "discussions.json")
    ledger = {}
    rq.intake(store, forum, ledger, set(), checks=(FakeApi(), dl()))
    st = {r["discussion"]: (r["status"], r.get("error")) for r in ledger.values()}
    assert st[1][0] == st[2][0] == "pending" and st[3][0] == "rejected" and "limit reached" in st[3][1]
    assert st[4] == ("rejected", "org/a@aaaaaaaaaaaa is already pending")   # same model and commit as #1
    assert st[5][0] == "rejected" and "request block" in st[5][1] and st[6][0] == "rejected" and "unknown target" in st[6][1]
    assert 7 not in st                                                        # ordinary discussions are left alone
    after = {d["num"]: d for d in json.loads((tmp_path / "discussions.json").read_text())}
    assert [n for n, d in after.items() if d["status"] == "closed"] == [3, 4, 5, 6]
    assert all(d["replies"][0].startswith("❌ Not queued") for n, d in after.items() if n in (3, 4, 5, 6))
    assert {r["id"] for r in store.requests()} == {f"discussion-{n:05d}" for n in range(1, 7)}
    rq.intake(store, forum, ledger, set(), checks=(FakeApi(), dl()))    # a second run changes nothing
    assert len(ledger) == 6


def test_intake_queue_cap(tmp_path, monkeypatch):
    rq = load_job(monkeypatch)
    from pii_leaderboard.forum import LocalForum
    monkeypatch.setattr(config, "MAX_QUEUE", 1)
    f = lambda m: {"model": m, "adapter": "transformers", "label_map": "auto"}  # noqa: E731
    (tmp_path / "discussions.json").write_text(json.dumps([discussion(1, "a", iso(now()), f("org/a")), discussion(2, "b", iso(now()), f("org/b"))]))
    ledger = {}
    rq.intake(LocalStore(tmp_path), LocalForum(tmp_path / "discussions.json"), ledger, set(), checks=(FakeApi(), dl()))
    assert ledger["discussion-00002"]["status"] == "rejected" and "queue is full" in ledger["discussion-00002"]["error"]


# --- the scoring job, locally ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def job_run(tmp_path_factory, public_file, newmean_model):
    """Pending regex + newmean-onnx requests and a seeded result, scored by the job on a local store. The private set here is
    a stand-in (a different slice of the public set), never the real one."""
    tmp_path = tmp_path_factory.mktemp("job")
    lines = public_file.read_text(encoding="utf-8").splitlines()
    pub, prv = tmp_path / "public.jsonl", tmp_path / "private.jsonl"
    pub.write_text("\n".join(lines[:300]) + "\n", encoding="utf-8")
    prv.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
    store = LocalStore(tmp_path / "store")
    t = iso(now())
    reqs = [{"id": "r1", "model": "regex", "revision": "", "adapter": "regex", "label_map": None, "submitter": "alice",
             "submitted_at": t, "status": "pending", "error": None, "updated_at": t, "discussion": 1}]   # accepted in an earlier run
    nano = large = None
    if __import__("importlib").util.find_spec("onnxruntime"):
        nano, large = newmean_model("nano"), newmean_model("large")
    onnx = bool(nano and large)
    if onnx:
        reqs.append({**reqs[0], "id": "r2", "model": "newmean-ai/pii-nano", "revision": "v1.0.0", "adapter": "newmean-onnx"})
        reqs.append({**reqs[0], "id": "r3", "model": "newmean-ai/pii-large", "revision": "v1.0.0", "adapter": "newmean-onnx",
                     "variant": "q4e"})
    for r in reqs:
        store.put_request(r)
    (tmp_path / "store/discussions.json").write_text(json.dumps([
        discussion(1, "alice", t, {"model": "regex"}), discussion(2, "bob", t, None, body="<!-- pii-leaderboard-request v1 -->\nbroken")]))
    store.put_result({"key": "seed", "model": "regex", "revision": "", "adapter": "regex", "display_name": "regex",
                      "public": {"status": "done", "overall": {}}, "private": {"status": "pending"}})
    models = tmp_path / "models/newmean-ai"
    models.mkdir(parents=True)
    if onnx:
        (models / "pii-nano").symlink_to(nano)
        (models / "pii-large").symlink_to(large)
    env = {**os.environ, "PII_LB_LOCAL": str(tmp_path / "store"), "PII_LB_CODE": str(LB), "PII_LB_PUBLIC_FILE": str(pub),
           "PII_LB_PRIVATE_FILE": str(prv), "PII_LB_MODEL_ROOT": str(tmp_path / "models"), "PII_LB_NO_UV": "1",
           "PII_LB_HARDWARE": "local-test"}
    env.pop("HF_TOKEN", None)
    p = subprocess.run([sys.executable, str(LB / "jobs/run_queue.py")], env=env, capture_output=True, text=True, timeout=1800)
    print(p.stdout[-3000:], p.stderr[-3000:])
    return SimpleNamespace(proc=p, tmp=tmp_path, store=store, reqs=reqs, pub=pub, onnx=onnx,
                           results={r["key"]: r for r in store.results()})


def test_job_end_to_end(job_run):
    """The job scores the pending requests, writes public detail and private overall only, and answers the discussions."""
    assert job_run.proc.returncode == 0
    store, reqs, results, tmp_path = job_run.store, job_run.reqs, job_run.results, job_run.tmp
    done = {r["id"]: r for r in store.requests()}
    assert all(done[r["id"]]["status"] == "done" for r in reqs), done
    for r in results.values():
        assert set(r["private"]) <= {"status", "dataset_revision", "overall", "gold_collapsed", "ms_per_row", "rows", "evaluated_at", "hardware"}
        assert r["private"]["status"] == "done"
    rx = results["regex___regex"]
    rows = load_rows(job_run.pub)
    assert rx["public"]["overall"]["leak"] == score_predictions(rows, [regex_predict(r["text"]) for r in rows], None)["ALL"]["leak"]
    assert set(rx["public"]) >= {"overall", "by_label", "by_language", "by_level"}
    assert results["seed"]["private"]["status"] == "done" and "by_language" not in results["seed"]["private"]   # backfill
    forum = {d["num"]: d for d in json.loads((tmp_path / "store/discussions.json").read_text())}
    assert forum[1]["status"] == "closed" and forum[1]["replies"][0].startswith("✅ Scored **regex**")
    assert forum[2]["status"] == "closed" and forum[2]["replies"][0].startswith("❌ Not queued")
    board = json.loads((tmp_path / "store/index/results/leaderboard.json").read_text())
    queue = json.loads((tmp_path / "store/index/requests/queue.json").read_text())
    assert {r["key"] for r in board["results"]} == set(results)
    assert {r["id"]: r["status"] for r in queue["requests"]}["discussion-00002"] == "rejected"
    assert all("label_map" not in r for r in queue["requests"])


def test_job_matches_stored_l4(research_dir, job_run):
    """Maintainer-only: our models through the job (SDK default files) give the same numbers as the predictions stored by
    the L4 run on the same rows."""
    if not job_run.onnx:
        pytest.skip("newmean models or onnxruntime not available")
    assert job_run.proc.returncode == 0
    rows = load_rows(job_run.pub)
    for key, tag in (("newmean-ai_pii-nano_v1.0.0__newmean-onnx_q4", "l4_q4-s-nano-v1"),
                     ("newmean-ai_pii-large_v1.0.0__newmean-onnx_q4e", "l4_q4e-s-large-v1")):
        stored = [json.loads(l)["spans"] for l in open(research_dir / f"preds/{tag}/r13_test_final.jsonl")][:300]
        want = score_predictions(rows, stored, ["PERSON_L2"])["ALL"]   # v1 model: native 13-label gold
        got = job_run.results[key]["public"]["overall"]
        assert got == {k: want[k] for k in got}, (key, got, want)   # same predictions as the L4 run


def test_already_scored_is_rejected(tmp_path):
    from pii_leaderboard.store import result_key
    store = LocalStore(tmp_path)
    store.put_result({"key": result_key("org/done", "a" * 40, "transformers"), "public": {}, "private": {}})
    with pytest.raises(Rejected, match="already on the leaderboard"):
        submit(store, model="org/done")
