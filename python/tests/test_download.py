# Downloads against a local HTTP server and file:// URLs (no network); two optional tests against Hugging Face.
import builtins
import contextlib
import errno
import hashlib
import inspect
import os
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    assert path == cache / "v1.1.0" / "pii-nano" / "onnx" / "model_q4.onnx" and path.read_bytes() == BODY
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


@pytest.mark.parametrize("failed_file", ["tokenizer.web.json", "model_q4.onnx"])
def test_fetch_model_requires_complete_cache(fake_pins, tmp_path, monkeypatch, failed_file):
    mirror = tmp_path / "mirror"
    cache = tmp_path / "cache"
    for name in ("tokenizer.web.json", "onnx/model_q4.onnx"):
        put(mirror, name, BODY)
    replace = os.replace

    def refuse_write(src, dest):
        if dest.name == failed_file:
            raise PermissionError("simulated read-only cache")
        replace(src, dest)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", refuse_write)
        with pytest.raises(ModelFileError, match="cache") as err:
            newmean_pii.fetch_model("nano", cache_dir=cache, model_url=mirror.as_uri())
        assert failed_file in str(err.value)
    assert not list(cache.rglob("*.part"))
    folder = newmean_pii.fetch_model("nano", cache_dir=cache, model_url=mirror.as_uri())
    assert (folder / "tokenizer.web.json").read_bytes() == BODY
    assert (folder / "onnx/model_q4.onnx").read_bytes() == BODY


# fetch_model and a cache that cannot be written (pull request #4). Paths in messages are compared as str(Path), so the
# checks hold with Windows separators too.
FILES = ("tokenizer.web.json", "onnx/model_q4.onnx")  # nano's two files, in the order fetch_model writes them
WRITE_STEPS = ("folder", "open", "write", "replace")  # the steps of a cache write that can fail


def cached(cache, rel=""):
    """Where the cache under cache keeps rel of pii-nano (the model folder for "")."""
    folder = Path(cache) / f"v{_models.MODEL_VERSION}" / "pii-nano"
    return folder / rel if rel else folder


class _FullDisk:
    """A file whose write fails as on a full disk; the partial file already exists."""

    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()

    def write(self, data):
        raise OSError(errno.ENOSPC, "simulated full disk")


@contextlib.contextmanager
def failing_write(dest, step):
    """While open, writing the cache file dest fails at one step, as a read-only or full disk does: "folder" (an
    ordinary file stands where its folder must be created), "open" (the partial file), "write" or "replace" (the move
    into place). Other files are written as usual."""

    def partial_of_dest(file):
        p = Path(file)
        return p.parent == dest.parent and p.name.startswith(f".{dest.name}.")

    def fake_open(file, *args, **kwargs):
        if not partial_of_dest(file):
            return builtins.open(file, *args, **kwargs)
        if step == "open":
            raise PermissionError(errno.EACCES, "simulated read-only cache", str(file))
        return _FullDisk(builtins.open(file, *args, **kwargs))

    replace = os.replace

    def fake_replace(src, dst, *args, **kwargs):
        if Path(dst) == dest:
            raise PermissionError(errno.EACCES, "simulated read-only cache", str(dst))
        return replace(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        if step == "folder":
            dest.parent.parent.mkdir(parents=True, exist_ok=True)
            dest.parent.write_bytes(b"")
        elif step in ("open", "write"):
            mp.setattr(_models, "open", fake_open, raising=False)
        else:
            mp.setattr(os, "replace", fake_replace)
        try:
            yield
        finally:
            if step == "folder":
                dest.parent.unlink()


@pytest.mark.parametrize("step", WRITE_STEPS)
@pytest.mark.parametrize("rel", FILES)
def test_fetch_model_returns_a_folder_that_loads_offline_or_raises(server, fake_pins, tmp_path, monkeypatch, rel, step):
    """fetch_model raises ModelFileError, or returns a folder from which both files load with model_dir= and
    NEWMEAN_PII_OFFLINE=1: for the tokenizer and the weights, at each step of the write. No partial file is left, and
    the next call to the folder, now writable, downloads only what is missing."""
    for f in FILES:
        put(server.root, f, BODY)
    cache = tmp_path / "cache"
    folder = cached(cache)
    with failing_write(folder / rel, step):
        with pytest.raises(ModelFileError, match="cannot write model cache file") as err:
            newmean_pii.fetch_model("nano", cache_dir=cache, model_url=server.url)
    assert str(folder / rel) in str(err.value)
    assert not list(cache.rglob("*.part"))
    missing = [f for f in FILES if not (folder / f).is_file()]
    assert missing == (list(FILES) if rel == FILES[0] else [rel])  # after a failed tokenizer the weights wait

    asked = len(server.requests)
    assert newmean_pii.fetch_model("nano", cache_dir=cache, model_url=server.url) == folder
    assert server.requests[asked:] == ["/" + f for f in missing]
    monkeypatch.setenv("NEWMEAN_PII_OFFLINE", "1")
    assert _models.load_file("nano", "tokenizer", model_dir=folder)[0] == BODY
    assert _models.load_file("nano", "q4", model_dir=str(folder))[0] == BODY
    assert newmean_pii.fetch_model("nano", cache_dir=cache, model_url="http://127.0.0.1:9/") == folder
    assert len(server.requests) == asked + len(missing)


@pytest.mark.parametrize("rel", FILES)
def test_fetch_model_accepts_the_same_file_stored_by_another_process(server, fake_pins, tmp_path, rel):
    """The move into place fails because another process stored the file meanwhile (on Windows, replacing a file that is
    open for reading fails): when that copy matches the pin, the cache holds the file and fetch_model returns the
    folder; when it does not, fetch_model raises."""
    for f in FILES:
        put(server.root, f, BODY)
    cache = tmp_path / "cache"
    folder = cached(cache)
    dest = folder / rel
    replace = os.replace

    def raced(other_copy):
        def fake_replace(src, dst, *args, **kwargs):
            if Path(dst) != dest:
                return replace(src, dst, *args, **kwargs)
            dest.write_bytes(other_copy)
            raise PermissionError(errno.EACCES, "simulated sharing violation", str(dst))

        return fake_replace

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", raced(BODY[:-1] + b"!"))
        with pytest.raises(ModelFileError, match="cannot write model cache file"):
            newmean_pii.fetch_model("nano", cache_dir=cache, model_url=server.url)
        mp.setattr(os, "replace", raced(BODY))
        assert newmean_pii.fetch_model("nano", cache_dir=cache, model_url=server.url) == folder
    assert not list(cache.rglob("*.part"))
    assert [(folder / f).read_bytes() == BODY for f in FILES] == [True, True]


@pytest.mark.parametrize("step", WRITE_STEPS)
def test_load_for_use_keeps_verified_bytes_when_a_cache_write_fails(server, fake_pins, tmp_path, step):
    """Loading for immediate use (Redactor, create_redactor) still returns the verified bytes and leaves nothing partial."""
    put(server.root, FILES[1], BODY)
    cache = tmp_path / "cache"
    dest = cached(cache, FILES[1])
    with failing_write(dest, step):
        data, path = _models.load_file("nano", "q4", cache_dir=cache, model_url=server.url)
    assert data == BODY and path == dest and not dest.exists()
    assert not list(cache.rglob("*.part"))


def test_complete_cache_that_cannot_be_written_is_only_read(server, fake_pins, tmp_path):
    """A complete cache in a folder that cannot be written: fetch_model returns it without an error or a request."""
    for f in FILES:
        put(server.root, f, BODY)
    cache = tmp_path / "cache"
    folder = newmean_pii.fetch_model("nano", cache_dir=cache, model_url=server.url)
    asked = len(server.requests)

    def refuse(*args, **kwargs):
        raise PermissionError(errno.EACCES, "simulated read-only cache")

    folders = [folder, folder / "onnx"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_models, "open", refuse, raising=False)
        mp.setattr(os, "replace", refuse)
        mp.setattr(Path, "mkdir", refuse)
        if os.name == "posix":  # a real read-only folder as well (Windows and root ignore the mode)
            for d in folders:
                os.chmod(d, 0o555)
        try:
            for url in (server.url, "http://127.0.0.1:9/"):
                assert newmean_pii.fetch_model("nano", cache_dir=cache, model_url=url) == folder
        finally:
            for d in folders:
                os.chmod(d, 0o755)
    assert len(server.requests) == asked


def test_download_command_with_a_cache_dir_that_is_a_file(server, fake_pins, tmp_path, monkeypatch, capsys):
    """The report of pull request #4: python -m newmean_pii download --cache-dir <an ordinary file> ended with status 0
    and printed a folder that did not exist. Now: status 1, nothing on stdout, the file it could not write on stderr.
    With a writable folder: status 0 and the folder."""
    from newmean_pii.__main__ import main

    for f in FILES:
        put(server.root, f, BODY)
    monkeypatch.setattr(_models, "default_url", lambda size: server.url)  # the command has no --model-url
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    assert main(["download", "--size", "nano", "--cache-dir", str(blocker)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and str(cached(blocker, FILES[0])) in err and "Traceback" not in err
    assert blocker.read_text() == "not a directory"
    cache = tmp_path / "cache"
    assert main(["download", "--size", "nano", "--cache-dir", str(cache)]) == 0
    assert capsys.readouterr() == (f"{cached(cache)}\n", "")


def test_redactor_with_a_cache_that_cannot_be_written(server, tmp_path):
    """The real nano files over HTTP, cache_dir an ordinary file: the redactor loads from the verified bytes and works;
    fetch_model to the same place raises."""
    if not has_model("nano", "q4"):
        pytest.skip(f"no model copy in {MODELS}")
    for f in FILES:
        put(server.root, f, (model_dir("nano") / f).read_bytes())
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    r = Redactor("nano", cache_dir=blocker, model_url=server.url)
    text = "Contact maria.lopez@example.com"
    assert [text[s.start : s.end] for s in r.detect(text)] == ["maria.lopez@example.com"]
    with pytest.raises(ModelFileError, match="cannot write model cache file"):
        newmean_pii.fetch_model("nano", cache_dir=blocker, model_url=server.url)
    assert blocker.read_text() == "not a directory"


def test_require_cache_is_keyword_only_and_off_by_default():
    """fetch_model's signature is unchanged; download and load_file take require_cache by keyword, default False."""
    P = inspect.Parameter
    assert [(p.name, p.kind, p.default) for p in inspect.signature(newmean_pii.fetch_model).parameters.values()] == [
        ("size", P.POSITIONAL_OR_KEYWORD, _models.DEFAULT_SIZE),
        ("variant", P.POSITIONAL_OR_KEYWORD, None),
        ("cache_dir", P.KEYWORD_ONLY, None),
        ("model_url", P.KEYWORD_ONLY, None),
        ("on_progress", P.KEYWORD_ONLY, None),
    ]
    assert list(inspect.signature(_models.download).parameters)[:5] == ["url", "dest", "pin", "on_progress", "timeout"]
    for f in (_models.download, _models.load_file):
        p = inspect.signature(f).parameters["require_cache"]
        assert p.kind == P.KEYWORD_ONLY and p.default is False


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
    put(tmp_path / "v1.1.0" / "pii-nano", "onnx/model_q4.onnx", BODY)
    assert _models.load_file("nano", "q4", cache_dir=tmp_path)[0] == BODY


def test_env_cache_dir(server, fake_pins, tmp_path, monkeypatch):
    put(server.root, "onnx/model_q4.onnx", BODY)
    monkeypatch.setenv("NEWMEAN_PII_CACHE", str(tmp_path / "env"))
    _, path = _models.load_file("nano", "q4", model_url=server.url)
    assert path == tmp_path / "env" / "v1.1.0" / "pii-nano" / "onnx" / "model_q4.onnx"


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
    assert folder == tmp_path / "v1.1.0" / "pii-nano"
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
    assert (tmp_path / "v1.1.0" / "pii-nano" / "onnx" / "model_q4.onnx").stat().st_size == newmean_pii.model_file("nano", "q4").bytes


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("NEWMEAN_PII_NETWORK_TESTS"), reason="set NEWMEAN_PII_NETWORK_TESTS=1 to download from huggingface.co")
def test_hugging_face_download_command_with_a_cache_that_cannot_be_written(tmp_path):
    """The command itself, as in the report of pull request #4: the tokenizer is downloaded, cannot be written, and the
    process ends with status 1, nothing on stdout and the file it could not write on stderr."""
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    run = subprocess.run(
        [sys.executable, "-m", "newmean_pii", "download", "--size", "nano", "--cache-dir", str(blocker)],
        capture_output=True, text=True, timeout=300,
    )
    assert run.returncode == 1 and run.stdout == "", run
    assert str(cached(blocker, FILES[0])) in run.stderr and "Traceback" not in run.stderr, run.stderr
    assert blocker.read_text() == "not a directory"
