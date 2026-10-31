"""Submissions as Hub discussions on the requests dataset.

The static Space opens a discussion whose body holds one fenced JSON request after a marker line (lb.js requestBody); anyone
can also open one by hand. The weekly job reads the open ones (validates, enforces the limits), replies with the result or the
reason for a rejection and closes them. Its own ledger (requests/<id>.json, id discussion-<n>) keeps the status.
"""
import json, re
from pathlib import Path

from . import config
from .store import iso, parse_iso

MARKER = "<!-- pii-leaderboard-request v1 -->"
_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


class BadRequest(ValueError):
    pass


def parse_request_body(text):
    """The request fields of a discussion body, None if it is not a leaderboard request, BadRequest if malformed."""
    if MARKER not in (text or ""):
        return None
    m = _BLOCK.search(text)
    if not m:
        raise BadRequest("no ```json request block found in the discussion")
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise BadRequest(f"the request block is not valid JSON ({e.msg})")
    if not isinstance(obj, dict) or not isinstance(obj.get("model"), str):
        raise BadRequest('the request block must be a JSON object with at least "model"')
    return {k: obj.get(k) for k in ("model", "revision", "adapter", "label_map", "variant")}


def request_body(fields):
    """Python twin of lb.js requestBody (tests check that both parse the same)."""
    return (f"{MARKER}\nSubmission to the newmean PII leaderboard. It is checked and scored by the weekly job, which replies here.\n\n"
            f"```json\n{json.dumps(fields, ensure_ascii=False, indent=1)}\n```\n")


class HubForum:
    def __init__(self, api, repo=config.REQUESTS):
        self.api, self.repo = api, repo

    def open_submissions(self):
        """Open discussions (oldest first) that carry the request marker: {num, author, created_at, body}."""
        out = []
        for d in self.api.get_repo_discussions(self.repo, repo_type="dataset", discussion_type="discussion",
                                               discussion_status="open"):
            det = self.api.get_discussion_details(self.repo, d.num, repo_type="dataset")
            first = next((e for e in det.events if e.type == "comment"), None)
            body = getattr(first, "content", "") if first else ""
            if MARKER in body:
                out.append({"num": d.num, "author": d.author, "created_at": iso(d.created_at.replace(microsecond=0)), "body": body})
        return sorted(out, key=lambda s: s["created_at"])

    def reply_and_close(self, num, text):
        self.api.change_discussion_status(self.repo, num, "closed", comment=text, repo_type="dataset")


class LocalForum:
    """discussions.json: [{num, author, created_at, body, status, replies}] (tests, local runs)."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def open_submissions(self):
        return sorted(({k: d[k] for k in ("num", "author", "created_at", "body")} for d in self._load()
                       if d.get("status", "open") == "open" and MARKER in d["body"]), key=lambda s: s["created_at"])

    def reply_and_close(self, num, text):
        ds = self._load()
        for d in ds:
            if d["num"] == num:
                d.setdefault("replies", []).append(text)
                d["status"] = "closed"
        self.path.write_text(json.dumps(ds, ensure_ascii=False, indent=1))


def created(sub):
    return parse_iso(sub["created_at"])
