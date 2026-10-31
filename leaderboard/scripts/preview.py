"""Local preview of the static page: writes preview/leaderboard.json and preview/queue.json from a local store and serves
this folder on http://localhost:8000 (the page reads ./preview/ on localhost; nothing is posted).

  python scripts/seed_results.py --reports <reports dir> --out /tmp/lb && python scripts/preview.py /tmp/lb
"""
import functools, http.server, json, sys
from pathlib import Path

LB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LB))
from pii_leaderboard.publish import leaderboard, queue  # noqa: E402
from pii_leaderboard.store import LocalStore  # noqa: E402


def main():
    store = LocalStore(sys.argv[1])
    out = LB / "preview"
    out.mkdir(exist_ok=True)
    (out / "leaderboard.json").write_text(json.dumps(leaderboard(store.results()), ensure_ascii=False))
    (out / "queue.json").write_text(json.dumps(queue(store.requests()), ensure_ascii=False))
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    print(f"http://localhost:{port}/")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(LB))).serve_forever()


if __name__ == "__main__":
    main()
