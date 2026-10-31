# Model files: pinned sizes and SHA-256 (native/core/src/models.c, generated from the same manifest as the npm package's
# js/src/models.ts), the local cache, and downloads from Hugging Face (standard library only). Every file is checked
# against its pin before use, whether it was downloaded, cached or passed in with model_dir.
import hashlib
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Tuple, Union

from . import _core

MODEL_VERSION: str = _core.MODEL_VERSION
SIZES = ("nano", "small", "medium", "large")
VARIANTS = ("q4", "q4e")
DEFAULT_SIZE: str = _core.DEFAULT_SIZE
DEFAULT_VARIANT = {s: _core.default_variant(s) for s in SIZES}

PathLike = Union[str, "os.PathLike[str]"]
Progress = Callable[[str, int, int], None]  # (file, bytes loaded, total bytes)


class ModelFileError(RuntimeError):
    """A model file is missing, cannot be downloaded, or does not match the published size and SHA-256."""


class FilePin(NamedTuple):
    path: str  # relative to the model folder: "onnx/model_q4e.onnx", "tokenizer.web.json"
    bytes: int
    sha256: str


def model_file(size: str, file: str) -> FilePin:
    """Pin of a published file; file is "q4", "q4e" or "tokenizer"."""
    pin = _core.model_file(size, file)
    if pin is None:
        raise ValueError(f"unknown model file {size!r} {file!r} (sizes: {', '.join(SIZES)}; files: q4, q4e, tokenizer)")
    return FilePin(*pin)


def default_url(size: str) -> str:
    return f"https://huggingface.co/newmean-ai/pii-{size}/resolve/v{MODEL_VERSION}/"


def default_cache_dir() -> Path:
    """NEWMEAN_PII_CACHE, else $XDG_CACHE_HOME/newmean-pii, else ~/.cache/newmean-pii."""
    env = os.environ.get("NEWMEAN_PII_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg and os.path.isabs(os.path.expanduser(xdg)) else Path.home() / ".cache"
    return base / "newmean-pii"


def _offline() -> bool:
    return os.environ.get("NEWMEAN_PII_OFFLINE", "").lower() not in ("", "0", "false", "no")


def _matches(data: bytes, pin: FilePin) -> bool:
    return len(data) == pin.bytes and hashlib.sha256(data).hexdigest() == pin.sha256


def download(url: str, dest: Optional[Path], pin: FilePin, on_progress: Optional[Progress] = None, timeout: float = 60.0) -> bytes:
    """Downloads url and returns its bytes; raises ModelFileError unless they match pin. When dest is given, the file is
    also written there (atomically); a cache that cannot be written is skipped, as the npm package does."""
    req = urllib.request.Request(url, headers={"User-Agent": f"newmean-pii/{_core.VERSION} (python)"})
    h = hashlib.sha256()
    chunks = []
    loaded = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            while loaded <= pin.bytes:
                chunk = res.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                chunks.append(chunk)
                loaded += len(chunk)
                if on_progress:
                    on_progress(pin.path, min(loaded, pin.bytes), pin.bytes)
    except (urllib.error.URLError, OSError) as e:
        raise ModelFileError(f"failed to download {url}: {e} (or download the files yourself and pass model_dir)") from e
    if loaded != pin.bytes or h.hexdigest() != pin.sha256:
        raise ModelFileError(f"{url} does not match the published model file (size or SHA-256 differs)")
    data = b"".join(chunks)
    if dest is not None:
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
    return data


def load_file(
    size: str,
    file: str,
    *,
    model_dir: Optional[PathLike] = None,
    cache_dir: Optional[PathLike] = None,
    model_url: Optional[str] = None,
    on_progress: Optional[Progress] = None,
) -> Tuple[bytes, Path]:
    """Bytes of a verified model file and where they came from: model_dir if given (never downloads), else the cache,
    else a download from model_url (default: Hugging Face newmean-ai/pii-<size> at the model version tag) into the cache."""
    pin = model_file(size, file)
    if model_dir is not None:
        path = Path(model_dir) / pin.path
        if not path.is_file():
            raise ModelFileError(f"{path} not found (model_dir holds the files of newmean-ai/pii-{size}: {pin.path})")
        data = path.read_bytes()
        if not _matches(data, pin):
            raise ModelFileError(f"{path} does not match the published {size} {file} file (size or SHA-256 differs)")
        return data, path

    path = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
    path = path / f"v{MODEL_VERSION}" / f"pii-{size}" / pin.path
    if path.is_file():
        data = path.read_bytes()
        if _matches(data, pin):
            if on_progress:
                on_progress(pin.path, pin.bytes, pin.bytes)
            return data, path
        try:
            path.unlink()  # damaged or stale copy: fetch again
        except OSError:
            pass
    if _offline():
        raise ModelFileError(f"{path} is not cached and NEWMEAN_PII_OFFLINE is set")
    base = model_url or default_url(size)
    if not base.endswith("/"):
        base += "/"
    return download(base + pin.path, path, pin, on_progress), path


def fetch_model(
    size: str = DEFAULT_SIZE,
    variant: Optional[str] = None,
    *,
    cache_dir: Optional[PathLike] = None,
    model_url: Optional[str] = None,
    on_progress: Optional[Progress] = None,
) -> Path:
    """Downloads (or checks) the files of one model into the cache and returns its folder, usable as model_dir."""
    variant = variant or DEFAULT_VARIANT.get(size) or "q4"
    _, tok = load_file(size, "tokenizer", cache_dir=cache_dir, model_url=model_url, on_progress=on_progress)
    load_file(size, variant, cache_dir=cache_dir, model_url=model_url, on_progress=on_progress)
    return tok.parent
