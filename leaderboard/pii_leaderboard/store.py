"""Ledger of requests and results: one JSON file per request (requests/<id>.json) and per scored model (results/<key>.json).

HubStore keeps them in the two Hub datasets; LocalStore in a folder (tests, local runs: PII_LB_LOCAL=<dir>).
A request: {id, model, revision, adapter, variant, label_map, submitter, submitted_at, status, error, updated_at}
Published for the static page: leaderboard.json (results repo: every result) and queue.json (requests repo: the ledger).
A result:  {key, model, revision, adapter, variant, display_name, submitter, source, hardware, evaluated_at,
            public: {status, dataset_revision, overall, by_label, by_language, by_level, gold_collapsed, ms_per_row},
            private: {status, dataset_revision, overall, gold_collapsed}}   — private never holds more than overall
"""
import hashlib, json, os, re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def result_key(model, revision, adapter, variant=None):
    slug = re.sub(r"[^\w.-]", "_", f"{model}@{(revision or '')[:12]}")
    return f"{slug}__{re.sub(r'[^\w.-]', '_', adapter)}" + (f"_{variant}" if variant else "")


def request_id(submitter, model, revision, adapter, t, variant=None):
    h = hashlib.sha256(f"{model}|{revision}|{adapter}|{variant or ''}|{iso(t)}".encode()).hexdigest()[:8]
    return f"{t.strftime('%Y%m%dT%H%M%SZ')}-{re.sub(r'[^\w-]', '_', submitter)}-{h}"


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def _list(self, kind):
        d = self.root / kind
        return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []

    def _put(self, kind, name, obj, message=""):
        p = self.root / kind / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")

    def requests(self):
        return self._list("requests")

    def results(self):
        return self._list("results")

    def put_request(self, req, message=""):
        self._put("requests", req["id"], req, message)

    def put_result(self, res, message=""):
        self._put("results", res["key"], res, message)

    def put_index(self, kind, name, obj, message=""):
        """A file at the root of the requests or results repo (leaderboard.json, queue.json): <root>/index/<kind>/<name>."""
        p = self.root / "index" / kind / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False) + "\n")


class HubStore:
    """The two Hub datasets. Reads take a fresh snapshot (small JSON files only); each write is one commit."""

    def __init__(self, token=None, requests_repo=config.REQUESTS, results_repo=config.RESULTS):
        from huggingface_hub import HfApi
        self.token = token or os.environ.get(config.TOKEN_ENV)
        self.api = HfApi(token=self.token)
        self.repos = {"requests": requests_repo, "results": results_repo}

    def _list(self, kind):
        from huggingface_hub import snapshot_download
        d = snapshot_download(self.repos[kind], repo_type="dataset", allow_patterns=[f"{kind}/*.json"], token=self.token)
        return [json.loads(p.read_text()) for p in sorted((Path(d) / kind).glob("*.json"))] if (Path(d) / kind).exists() else []

    def _put(self, kind, name, obj, message):
        self.api.upload_file(path_or_fileobj=(json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode(),
                             path_in_repo=f"{kind}/{name}.json", repo_id=self.repos[kind], repo_type="dataset",
                             commit_message=message or f"{kind}: {name}")

    def requests(self):
        return self._list("requests")

    def results(self):
        return self._list("results")

    def put_request(self, req, message=""):
        self._put("requests", req["id"], req, message)

    def put_result(self, res, message=""):
        self._put("results", res["key"], res, message)

    def put_index(self, kind, name, obj, message=""):
        self.api.upload_file(path_or_fileobj=(json.dumps(obj, ensure_ascii=False) + "\n").encode(), path_in_repo=name,
                             repo_id=self.repos[kind], repo_type="dataset", commit_message=message or f"Update {name}")


def open_store():
    local = os.environ.get("PII_LB_LOCAL")
    return LocalStore(local) if local else HubStore()


def recent_by(requests, submitter, t=None, days=7):
    """Requests counted against a submitter's weekly limit (rejected ones do not count)."""
    t = t or now()
    return [r for r in requests if r["submitter"] == submitter and r["status"] != "rejected"
            and parse_iso(r["submitted_at"]) > t - timedelta(days=days)]
