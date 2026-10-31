# Downloads against a local HTTP server and file:// URLs (no network); one optional test against Hugging Face.
import hashlib
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

import newmean_pii
from newmean_pii import FilePin, ModelFileError, Redactor, _models

from conftest import MODELS, has_model, model_dir

BODY = b"fake model bytes " * 1000
PIN = FilePin("onnx/model_q4.onnx", len(BODY), hashlib.sha256(BODY).hexdigest())


@pytest.fixture
def server(tmp_path):
    """Serves tmp_path/www over HTTP on localhost; .requests lists the paths asked for."""
    root = tmp_path / "www"
    root.mkdir()
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            super().do_GET()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    httpd.root = root
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.requests = requests
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def fake_pins(monkeypatch):
    """Pins every file of every size to BODY."""
    monkeypatch.setattr(_models, "model_file", lambda size, file: PIN._replace(path=newmean_pii._core.model_file(size, file)[0]))


def put(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_download_verify_and_cache(server, fake_pins, tmp_path):
    put(server.root, "pii-nano/onnx/model_q4.onnx", BODY)
    cache = tmp_path / "cache"
    seen = []
    data, path = _models.load_file("nano", "q4", cache_dir=cache, model_url=server.url + "pii-nano", on_progress=lambda *a: seen.append(a))
    assert data == BODY
    assert path == cache / "v1.0.0" / "pii-nano" / "onnx" / "model_q4.onnx" and path.read_bytes() == BODY
    assert server.requests == ["/pii-nano/onnx/model_q4.onnx"]
    assert seen[-1] == ("onnx/model_q4.onnx", len(BODY), len(BODY))
    assert [p.name for p in path.parent.iterdir()] == ["model_q4.onnx"]  # no partial files left

    # second load: from the cache, no request
    assert _models.load_file("nano", "q4", cache_dir=cache, model_url=server.url + "pii-nano/")[0] == BODY
    assert len(server.requests) == 1

    # a damaged cached copy is fetched again
    path.write_bytes(BODY[:-1] + b"!")
    assert _models.load_file("nano", "q4", cache_dir=cache, model_url=server.url + "pii-nano/")[0] == BODY
    assert len(server.requests) == 2 and path.read_bytes() == BODY


def test_mismatch_is_rejected_and_not_cached(server, fake_pins, tmp_path):
    put(server.root, "pii-nano/onnx/model_q4.onnx", BODY[:-1] + b"?")  # same size, other hash
    put(server.root, "pii-small/onnx/model_q4.onnx", BODY + b"more")  # other size
    for size in ("nano", "small"):
        with pytest.raises(ModelFileError, match="does not match"):
            _models.load_file(size, "q4", cache_dir=tmp_path / "c", model_url=server.url + f"pii-{size}/")
    assert not [p for p in (tmp_path / "c").rglob("*") if p.is_file()]


def test_http_error(server, fake_pins, tmp_path):
    with pytest.raises(ModelFileError, match="failed to download"):
        _models.load_file("nano", "q4", cache_dir=tmp_path, model_url=server.url + "missing/")


def test_file_url(fake_pins, tmp_path):
    put(tmp_path / "mirror", "onnx/model_q4.onnx", BODY)
    url = (tmp_path / "mirror").as_uri() + "/"
    data, path = _models.load_file("nano", "q4", cache_dir=tmp_path / "c", model_url=url)
    assert data == BODY and path.is_file()


def test_unwritable_cache_still_loads(server, fake_pins, tmp_path):
    put(server.root, "onnx/model_q4.onnx", BODY)
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    data, _ = _models.load_file("nano", "q4", cache_dir=blocker, model_url=server.url)
    assert data == BODY


def test_offline_env(fake_pins, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWMEAN_PII_OFFLINE", "1")
    with pytest.raises(ModelFileError, match="NEWMEAN_PII_OFFLINE"):
        _models.load_file("nano", "q4", cache_dir=tmp_path, model_url="http://127.0.0.1:9/")
    put(tmp_path / "v1.0.0" / "pii-nano", "onnx/model_q4.onnx", BODY)
    assert _models.load_file("nano", "q4", cache_dir=tmp_path)[0] == BODY


def test_env_cache_dir(server, fake_pins, tmp_path, monkeypatch):
    put(server.root, "onnx/model_q4.onnx", BODY)
    monkeypatch.setenv("NEWMEAN_PII_CACHE", str(tmp_path / "env"))
    _, path = _models.load_file("nano", "q4", model_url=server.url)
    assert path == tmp_path / "env" / "v1.0.0" / "pii-nano" / "onnx" / "model_q4.onnx"


def test_model_dir_is_verified_and_never_downloads(fake_pins, tmp_path):
    with pytest.raises(ModelFileError, match="not found"):
        _models.load_file("nano", "q4", model_dir=tmp_path, model_url="http://127.0.0.1:9/")
    put(tmp_path, "onnx/model_q4.onnx", BODY + b"x")
    with pytest.raises(ModelFileError, match="does not match"):
        _models.load_file("nano", "q4", model_dir=tmp_path)
    put(tmp_path, "onnx/model_q4.onnx", BODY)
    assert _models.load_file("nano", "q4", model_dir=str(tmp_path))[0] == BODY


def test_real_pins_reject_other_files(server, tmp_path):
    put(server.root, "tokenizer.web.json", b"{}")
    with pytest.raises(ModelFileError, match="does not match"):
        Redactor("nano", cache_dir=tmp_path, model_url=server.url)


def test_redactor_from_local_server(server, tmp_path):
    """The real nano files served over HTTP: downloaded, verified against the real pins, cached, then used."""
    if not has_model("nano", "q4"):
        pytest.skip(f"no model copy in {MODELS}")
    for rel in ("tokenizer.web.json", "onnx/model_q4.onnx"):
        put(server.root, f"pii-nano/{rel}", (model_dir("nano") / rel).read_bytes())
    folder = newmean_pii.fetch_model("nano", cache_dir=tmp_path, model_url=server.url + "pii-nano/")
    assert folder == tmp_path / "v1.0.0" / "pii-nano"
    r = Redactor("nano", cache_dir=tmp_path, model_url="http://127.0.0.1:9/")  # cached: no request
    assert len(server.requests) == 2
    text = "Contact maria.lopez@example.com"
    assert [text[s.start : s.end] for s in r.detect(text)] == ["maria.lopez@example.com"]
    assert Redactor("nano", model_dir=folder).detect(text) == r.detect(text)


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("NEWMEAN_PII_NETWORK_TESTS"), reason="set NEWMEAN_PII_NETWORK_TESTS=1 to download from huggingface.co")
def test_hugging_face_download(tmp_path):
    r = newmean_pii.create_redactor(size="nano", cache_dir=tmp_path)
    out = r.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다")
    assert out.text == "제 메일은 [EMAIL], 전화는 [PHONE]입니다", out
    assert (tmp_path / "v1.0.0" / "pii-nano" / "onnx" / "model_q4.onnx").stat().st_size == newmean_pii.model_file("nano", "q4").bytes
