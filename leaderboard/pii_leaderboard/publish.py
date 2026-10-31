"""The two files the static Space reads: leaderboard.json (all results) and queue.json (the request ledger, no label maps)."""
from .store import iso, now

QUEUE_FIELDS = ("id", "model", "revision", "adapter", "variant", "submitter", "submitted_at", "status", "error", "discussion",
                "result")


def leaderboard(results, t=None):
    return {"updated_at": iso(t or now()), "results": sorted(results, key=lambda r: r["key"])}


def queue(requests, t=None):
    rows = [{k: r.get(k) for k in QUEUE_FIELDS} for r in sorted(requests, key=lambda r: r["submitted_at"], reverse=True)]
    return {"updated_at": iso(t or now()), "requests": rows}


def publish(store):
    store.put_index("results", "leaderboard.json", leaderboard(store.results()), "Update leaderboard.json")
    store.put_index("requests", "queue.json", queue(store.requests()), "Update queue.json")
