# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface_hub>=1.0"]
# ///
"""Weekly scoring job for the newmean PII leaderboard (Hugging Face Jobs).

Launch without uploading anything (a local script would make the CLI create a jobs-artifacts bucket): the job's own command
downloads this code from the Space and runs it, see README "Scoring job".

1. Intake: every open discussion on the requests dataset that carries a request block is checked again (Hub checks, weekly
   limit per account, queue cap, duplicates). Rejected ones get a reply with the reason and are closed; accepted ones enter the
   ledger (requests/discussion-<n>.json) as pending. Maintainer requests (scripts/admin_request.py) are already in the ledger.
2. Scoring: pending requests, oldest first, at most PII_LB_MAX_PER_RUN, no new model after PII_LB_RUN_BUDGET_MIN minutes; then,
   once the private set is readable, results whose private score is still "pending" (backfill, private set only).
   Each model's files are downloaded here and scored in a child process (jobs/score_model.py) inside the adapter's own uv
   environment (requirements-<env>.txt), with no Hub token and the Hub offline. For the private set only overall numbers
   leave the child. Writes results/<key>.json, the ledger status, and replies to and closes the discussion.
3. Publishes leaderboard.json (results dataset) and queue.json (requests dataset) for the static page.

Secrets (by name): HF_TOKEN — read both test sets and the Space; write the requests and results datasets and their discussions.
Local run (tests): PII_LB_LOCAL=<store dir, discussions.json inside> PII_LB_CODE=<leaderboard dir> PII_LB_PUBLIC_FILE=<jsonl>
                   [PII_LB_PRIVATE_FILE=<jsonl>] [PII_LB_MODEL_ROOT=<dir with <repo id>/ folders>] [PII_LB_NO_UV=1]
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

T0 = time.time()
TOKEN = os.environ.get("HF_TOKEN")


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def code_dir():
    if os.environ.get("PII_LB_CODE"):
        return Path(os.environ["PII_LB_CODE"])
    from huggingface_hub import snapshot_download
    space = os.environ.get("PII_LB_SPACE", "newmean-ai/pii-leaderboard")
    return Path(snapshot_download(space, repo_type="space", token=TOKEN, allow_patterns=["pii_leaderboard/*.py", "jobs/*"]))


CODE = code_dir()
sys.path.insert(0, str(CODE))
from pii_leaderboard import config  # noqa: E402
from pii_leaderboard.adapters import adapter_env, download_spec, variant_of  # noqa: E402
from pii_leaderboard.forum import BadRequest, HubForum, LocalForum, created, parse_request_body  # noqa: E402
from pii_leaderboard.publish import publish  # noqa: E402
from pii_leaderboard.store import iso, now, open_store, result_key  # noqa: E402
from pii_leaderboard.submit import Rejected, validate_submission  # noqa: E402

SPACE_URL = f"https://huggingface.co/spaces/{config.SPACE}"


def fetch_test(repo, local_env):
    """(path, dataset revision) of a test set, or (None, None) if it does not exist or is not readable."""
    if os.environ.get(local_env):
        return os.environ[local_env], "local"
    from huggingface_hub import HfApi, hf_hub_download
    try:
        sha = HfApi(token=TOKEN).dataset_info(repo).sha
        return hf_hub_download(repo, config.TEST_FILE, repo_type="dataset", revision=sha, token=TOKEN), sha
    except Exception as e:
        log(f"test set {repo} not available ({type(e).__name__})")
        return None, None


def fetch_model(req, dest):
    spec = download_spec(req)
    if spec is None:
        return ""
    repo, rev, patterns = spec
    root = os.environ.get("PII_LB_MODEL_ROOT")
    if root and (Path(root) / repo).exists():
        return str(Path(root) / repo)
    from huggingface_hub import snapshot_download
    return snapshot_download(repo, revision=rev or None, allow_patterns=patterns, local_dir=dest, token=TOKEN)


def child_cmd(env):
    if os.environ.get("PII_LB_NO_UV"):
        return [sys.executable]
    return ["uv", "run", "--no-project", "--python", "3.13", "--with-requirements", str(CODE / f"jobs/requirements-{env}.txt"), "python"]


def run_child(req, model_dir, public, private, work):
    rq, out = work / "request.json", work / "out.json"
    rq.write_text(json.dumps(req))
    cmd = child_cmd(adapter_env(req)) + [str(CODE / "jobs/score_model.py"), "--request", str(rq), "--model-dir", model_dir,
                                         "--out", str(out)]
    cmd += ["--public", public] if public else []
    cmd += ["--private", private] if private else []
    env = {k: v for k, v in os.environ.items() if k not in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN") and not k.startswith("OAUTH")}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUNBUFFERED="1", TOKENIZERS_PARALLELISM="false",
               HF_HUB_DISABLE_PROGRESS_BARS="1", TQDM_DISABLE="1")
    p = subprocess.run(cmd, env=env, timeout=float(os.environ.get("PII_LB_MODEL_TIMEOUT_MIN", 120)) * 60)
    if p.returncode:
        raise RuntimeError(f"scoring process exited with code {p.returncode}")
    return json.loads(out.read_text())


def hub_checks():
    """(api, download_json) for the job's own validation of submissions."""
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi(token=TOKEN)
    return api, lambda repo, f, rev: json.loads(Path(hf_hub_download(repo, f, revision=rev, token=TOKEN)).read_text())


def open_forum():
    if os.environ.get("PII_LB_LOCAL"):
        return LocalForum(Path(os.environ["PII_LB_LOCAL"]) / "discussions.json")
    from huggingface_hub import HfApi
    return HubForum(HfApi(token=TOKEN))


def fmt(x, pct=True):
    return "—" if x is None else (f"{100 * x:.1f}" if pct else f"{x:.3f}")


def result_reply(res):
    o, p = res["public"]["overall"], res["private"]
    po = p.get("overall") or {}
    priv = (f"| private test | {fmt(po.get('leak'))} | {fmt(po.get('overmask'))} | {fmt(po.get('exact_f1'), False)} | "
            f"{fmt(po.get('span_f1'), False)} |" if p.get("status") == "done" else "| private test | pending | | | |")
    return (f"Scored **{res['model']}** @ `{res['revision'][:12]}` ({res['adapter']}).\n\n"
            "| | miss % | over-masking % | exact F1 | span F1 |\n|---|---|---|---|---|\n"
            f"| public test | {fmt(o['leak'])} | {fmt(o['overmask'])} | {fmt(o['exact_f1'], False)} | {fmt(o['span_f1'], False)} |\n"
            f"{priv}\n\nPer label, language and PII level: {SPACE_URL}")


def intake(store, forum, ledger, result_keys, checks=None):
    """Open discussions → ledger (pending) or a rejection reply and close. checks: (api, download_json) for the Hub checks."""
    api, dl = checks or hub_checks()
    known = {r.get("discussion") for r in ledger.values()}
    for sub in forum.open_submissions():
        rid = f"discussion-{sub['num']:05d}"
        if rid in ledger or sub["num"] in known:   # already accepted in an earlier run, waiting for scoring
            continue
        fields = None
        try:
            fields = parse_request_body(sub["body"])
            req = validate_submission(list(ledger.values()), result_keys, api, dl, sub["author"], fields, created(sub), rid)
        except (BadRequest, Rejected) as e:
            log(f"rejected discussion #{sub['num']} by {sub['author']}: {e}")
            ledger[rid] = {"id": rid, "model": (fields or {}).get("model"), "revision": "", "adapter": (fields or {}).get("adapter"),
                           "submitter": sub["author"], "submitted_at": sub["created_at"], "status": "rejected", "error": str(e),
                           "discussion": sub["num"], "updated_at": iso(now())}
            store.put_request(ledger[rid], f"Rejected discussion #{sub['num']}")
            forum.reply_and_close(sub["num"], f"❌ Not queued: {e}\n\nYou can open a new submission at {SPACE_URL}.")
            continue
        req["discussion"] = sub["num"]
        ledger[rid] = req
        store.put_request(req, f"Accepted discussion #{sub['num']}")
        log(f"accepted discussion #{sub['num']} {req['model']}@{req['revision'][:12]} by {sub['author']}")


def main():
    store, forum = open_store(), open_forum()
    public, public_rev = fetch_test(config.PUBLIC_TEST, "PII_LB_PUBLIC_FILE")
    private, private_rev = fetch_test(config.PRIVATE_TEST, "PII_LB_PRIVATE_FILE")
    if public is None:
        raise SystemExit("public test set not readable")
    hardware = os.environ.get("PII_LB_HARDWARE", "hf-jobs")
    results = {r["key"]: r for r in store.results()}
    ledger = {r["id"]: r for r in store.requests()}
    intake(store, forum, ledger, set(results))
    pending = sorted((r for r in ledger.values() if r["status"] in ("pending", "running")), key=lambda r: r["submitted_at"])
    work = [("request", r) for r in pending[:config.MAX_PER_RUN]]
    if private:
        work += [("backfill", r) for r in results.values() if r.get("private", {}).get("status") == "pending" and r.get("adapter")]
    log(f"{len(pending)} pending, {len(work)} work items, private set {'available' if private else 'not available'}")
    for kind, item in work:
        if (time.time() - T0) / 60 > config.RUN_BUDGET_MIN:
            log("run budget reached; the rest waits for the next run")
            break
        req = item if kind == "request" else {"model": item["model"], "revision": item["revision"], "adapter": item["adapter"],
                                               "variant": item.get("variant"), "label_map": item.get("label_map")}
        if req["adapter"] == "newmean-onnx":
            req = {**req, "variant": variant_of(req)}   # older requests without the field: the tier's SDK default
        key = result_key(req["model"], req["revision"], req["adapter"], req.get("variant"))
        log(f"{kind} {req['model']}@{(req['revision'] or '')[:12]} adapter={req['adapter']} variant={req.get('variant')}")
        if kind == "request":
            store.put_request({**req, "status": "running", "updated_at": iso(now())}, f"Running {req['id']}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                model_dir = fetch_model(req, tmp / "model")
                out = run_child(req, model_dir, public if kind == "request" else None, private, tmp)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            log("failed", msg)
            if kind == "request":
                store.put_request({**req, "status": "failed", "error": msg, "updated_at": iso(now())}, f"Failed {req['id']}")
                if req.get("discussion"):
                    forum.reply_and_close(req["discussion"], f"❌ Scoring failed: {msg}\n\nThe maintainers have been notified "
                                                             "through the job log; you can submit again after a fix.")
            continue
        t = iso(now())
        if kind == "request":
            res = {"key": key, "model": req["model"], "revision": req["revision"], "adapter": req["adapter"],
                   "variant": req.get("variant"), "label_map": req.get("label_map"), "display_name": req["model"],
                   "submitter": req["submitter"], "source": "hf-job", "hardware": hardware, "evaluated_at": t,
                   "public": {"status": "done", "dataset_revision": public_rev, **out["public"]},
                   "private": ({"status": "done", "dataset_revision": private_rev, **out["private"]} if private
                               else {"status": "pending"})}
            if key in results:   # re-scoring (maintainer --force): keep the display fields of the existing row
                res = {**res, **{k: results[key][k] for k in ("display_name", "size") if k in results[key]}}
        else:
            res = {**item, "private": {"status": "done", "dataset_revision": private_rev, "evaluated_at": t, "hardware": hardware,
                                       **out["private"]}}
        results[key] = res
        store.put_result(res, f"Scores for {req['model']}@{(req['revision'] or '')[:12]}")
        if kind == "request":
            store.put_request({**req, "status": "done", "updated_at": t, "result": key}, f"Done {req['id']}")
            if req.get("discussion"):
                forum.reply_and_close(req["discussion"], "✅ " + result_reply(res))
        log("done", json.dumps(res["public"].get("overall")), "private", json.dumps(res["private"].get("overall")))
    publish(store)
    log("published leaderboard.json and queue.json; finished")


if __name__ == "__main__":
    main()
