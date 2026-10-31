from typing import Callable, Iterable, List, Optional

from . import _core
from ._models import DEFAULT_SIZE, DEFAULT_VARIANT, MODEL_VERSION, SIZES, VARIANTS, PathLike, Progress, load_file
from ._regex import regex_spans
from ._types import RedactResult, Span, Token

LABELS: List[str] = list(_core.LABELS)
#: Level 2 = indirect identifiers: a surname with a title, a nickname, initials (PERSON_L2) and public profile or page
#: URLs (URL_L2). Pass labels=LEVEL1_LABELS to leave them visible.
LEVEL1_LABELS: List[str] = [l for l in LABELS if not l.endswith("_L2")]
#: Tag list of the v1 models (index = model output).
TAGS: List[str] = ["O"] + [f"{p}-{l}" for l in LABELS for p in ("B", "I")]

_LABEL_ID = {l: i for i, l in enumerate(LABELS)}
_SOURCES = {_core.SOURCE_MODEL: "model", _core.SOURCE_REGEX: "regex", _core.SOURCE_MODEL | _core.SOURCE_REGEX: "model+regex"}
_MODES = ("tag", "mask", "blank")


def _family(label: str) -> str:
    return label[:-3] if label.endswith("_L2") else label


def apply_redaction(
    text: str,
    spans: Iterable[Span],
    mode: str = "tag",
    mask_char: str = "*",
    replace: Optional[Callable[[Span, str], str]] = None,
) -> str:
    """Replaces each span (sorted, non-overlapping): "tag" -> "[EMAIL]" (PERSON_L2 -> [PERSON], URL_L2 -> [URL]),
    "mask" -> one mask_char per character (code point), "blank" -> removed. replace(span, original) overrides mode."""
    if mode not in _MODES:
        raise ValueError(f"unknown mode {mode!r} (tag, mask, blank)")
    out = []
    last = 0
    for s in spans:
        orig = text[s.start : s.end]
        out.append(text[last : s.start])
        if replace is not None:
            out.append(replace(s, orig))
        elif mode == "tag":
            out.append(f"[{_family(s.label)}]")
        elif mode == "mask":
            out.append(mask_char * len(orig))
        last = s.end
    out.append(text[last:])
    return "".join(out)


class Redactor:
    """Detects and masks personal information with a newmean-pii-<size> model, on this machine.

    Safe to share between threads: the model is immutable and the GIL is released while it runs, so a thread pool
    detects in parallel.
    """

    def __init__(
        self,
        size: str = DEFAULT_SIZE,
        variant: Optional[str] = None,
        *,
        sensitivity: float = 0.0,
        labels: Optional[Iterable[str]] = None,
        regex: bool = False,
        model_dir: Optional[PathLike] = None,
        cache_dir: Optional[PathLike] = None,
        model_url: Optional[str] = None,
        on_progress: Optional[Progress] = None,
    ) -> None:
        if size not in SIZES:
            raise ValueError(f"unknown size {size!r} ({', '.join(SIZES)})")
        variant = variant if variant is not None else DEFAULT_VARIANT[size]
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r} ({', '.join(VARIANTS)})")
        enabled = LABELS if labels is None else ([labels] if isinstance(labels, str) else list(labels))
        unknown = [l for l in enabled if l not in _LABEL_ID]
        if unknown:
            raise ValueError(f"unknown label(s) {unknown} (labels: {', '.join(LABELS)})")
        mask = 0
        for l in enabled:
            mask |= 1 << _LABEL_ID[l]

        where = dict(model_dir=model_dir, cache_dir=cache_dir, model_url=model_url, on_progress=on_progress)
        tok, _ = load_file(size, "tokenizer", **where)
        onnx, _ = load_file(size, variant, **where)
        self._model: Optional[_core.Model] = _core.Model(onnx, tok)
        self._mask = mask
        self._sensitivity = float(sensitivity)
        self.size: str = size
        self.variant: str = variant
        self.version: str = MODEL_VERSION
        self.labels: List[str] = [l for l in LABELS if mask >> _LABEL_ID[l] & 1]
        self.regex: bool = bool(regex)

    @property
    def sensitivity(self) -> float:
        """> 0 masks more (fewer misses, more over-masking), < 0 masks less; about -1..1. The O logit gets -sensitivity."""
        return self._sensitivity

    def set_sensitivity(self, value: float) -> None:
        """Changes the threshold without reloading the model."""
        self._sensitivity = float(value)

    def _core_model(self) -> "_core.Model":
        m = self._model
        if m is None:
            raise RuntimeError("the redactor is closed")
        return m

    def detect(self, text: str) -> List[Span]:
        """Spans to mask, sorted and non-overlapping, with str indices (code points)."""
        if not isinstance(text, str):
            raise TypeError(f"text must be str, not {type(text).__name__}")
        m = self._core_model()
        extra = [(s.start, s.end, _LABEL_ID[s.label]) for s in regex_spans(text)] if self.regex else None
        return [Span(s, e, LABELS[l], _SOURCES[src]) for s, e, l, src in m.detect(text, self._sensitivity, self._mask, extra)]

    def redact(
        self,
        text: str,
        mode: str = "tag",
        *,
        mask_char: str = "*",
        replace: Optional[Callable[[Span, str], str]] = None,
    ) -> RedactResult:
        """Masked text and the spans: mode "tag" -> [EMAIL], "mask" -> *****, "blank" -> removed; or replace(span, original)."""
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r} (tag, mask, blank)")
        spans = self.detect(text)
        return RedactResult(apply_redaction(text, spans, mode, mask_char, replace), spans)

    def tokenize(self, text: str) -> List[Token]:
        """Token ids and offsets (code points) without <s> and </s>, as Hugging Face tokenizers gives them."""
        if not isinstance(text, str):
            raise TypeError(f"text must be str, not {type(text).__name__}")
        return [Token(*t) for t in self._core_model().tokenize(text)]

    def info(self) -> dict:
        """Model shape and the native kernel build."""
        return {**self._core_model().info(), "build": _core.build_info()}

    def close(self) -> None:
        """Releases the model (calls running in other threads finish first)."""
        self._model = None

    def __enter__(self) -> "Redactor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Redactor(size={self.size!r}, variant={self.variant!r}, sensitivity={self._sensitivity}, regex={self.regex})"


def create_redactor(
    size: str = DEFAULT_SIZE,
    variant: Optional[str] = None,
    *,
    sensitivity: float = 0.0,
    labels: Optional[Iterable[str]] = None,
    regex: bool = False,
    model_dir: Optional[PathLike] = None,
    cache_dir: Optional[PathLike] = None,
    model_url: Optional[str] = None,
    on_progress: Optional[Progress] = None,
) -> Redactor:
    """Same as Redactor(...), named like the npm package's createRedactor."""
    return Redactor(
        size, variant, sensitivity=sensitivity, labels=labels, regex=regex, model_dir=model_dir, cache_dir=cache_dir,
        model_url=model_url, on_progress=on_progress,
    )
