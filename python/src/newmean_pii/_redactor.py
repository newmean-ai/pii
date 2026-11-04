import math
from typing import Callable, Iterable, List, Optional, Tuple

from . import _core
from ._lists import AllowEntry, BlockEntry, check_lists_id, make_lists
from ._models import DEFAULT_SIZE, DEFAULT_VARIANT, MODEL_VERSION, SIZES, VARIANTS, PathLike, Progress, load_file, model_file
from ._regex import regex_spans
from ._types import RedactResult, Span, Token

LABELS: List[str] = list(_core.LABELS)
#: Level 2 = indirect identifiers: a surname with a title, a nickname, initials (PERSON_L2) and public profile or page
#: URLs (URL_L2). Pass labels=LEVEL1_LABELS to leave them visible.
LEVEL1_LABELS: List[str] = [l for l in LABELS if not l.endswith("_L2")]
#: Tag list of the v1 models (index = model output).
TAGS: List[str] = ["O"] + [f"{p}-{l}" for l in LABELS for p in ("B", "I")]

_LABEL_ID = {l: i for i, l in enumerate(LABELS)}
_SOURCES = {
    _core.SOURCE_MODEL: "model",
    _core.SOURCE_REGEX: "regex",
    _core.SOURCE_MODEL | _core.SOURCE_REGEX: "model+regex",
    _core.SOURCE_TABLE: "table",
    _core.SOURCE_BLOCK: "block",
}
#: Table mode delimiters (the npm package's TableDelimiter) -> pii_table_options.delimiter.
TABLE_DELIMITERS = {"auto": 0, ",": ord(","), "\t": ord("\t"), ";": ord(";"), "markdown": ord("|")}
#: Table mode contexts (the npm package's TableOptions.context) -> pii_table_options.context.
TABLE_CONTEXTS = {"none": 0, "header": 1}
_MODES = ("tag", "mask", "blank")


def _source(bits: int) -> str:
    try:
        return _SOURCES[bits]
    except KeyError:
        raise RuntimeError(f"unknown span source bits {bits} from the native core") from None


def _family(label: str) -> str:
    return label[:-3] if label.endswith("_L2") else label


def _check_sensitivity(value: float) -> float:
    """float(value), which must be finite: NaN would turn model detection off and an infinity would mask everything or
    nothing (the core also rejects them, with PII_ERR_ARG)."""
    s = float(value)
    if not math.isfinite(s):
        raise ValueError("sensitivity must be a finite number")
    return s


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
        keep_emoji: bool = False,
        whole_words: bool = True,
        strict_short_names: bool = False,
        allow: Optional[Iterable[AllowEntry]] = None,
        block: Optional[Iterable[BlockEntry]] = None,
        lists_id: Optional[str] = None,
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
        # allow: values you know are not PII in your data (survey categories: regions, brands, jobs), str or (str, label):
        # a span whose whole text is an entry is not masked; with a label, only spans of that label family (PERSON covers
        # PERSON_L2, URL covers URL_L2). block: (str, label) values you know are PII: every occurrence that starts and ends
        # on a word boundary is masked with the entry's label (source "block"), whatever labels enables; a Hangul entry also
        # matches before Korean particles ("박지훈님께"). ASCII letters compare case-insensitively, nothing else is
        # normalized (pass NFC text). At most 10,000 entries of 1 to 64 code points per list. lists_id: your name for the
        # lists, in info()["lists"]. Since 1.4.0. Validated before any model file is loaded (ListError: list, index and
        # reason, never the text).
        lists_id = check_lists_id(lists_id)
        lists, n_allow, n_block = make_lists(allow, block)
        sensitivity = _check_sensitivity(sensitivity)  # after the lists, as in every package

        where = dict(model_dir=model_dir, cache_dir=cache_dir, model_url=model_url, on_progress=on_progress)
        tok, tok_path = load_file(size, "tokenizer", **where)
        onnx, onnx_path = load_file(size, variant, **where)
        self._paths = {"model": str(onnx_path), "tokenizer": str(tok_path)}
        self._model: Optional[_core.Model] = _core.Model(onnx, tok)
        self._lists = lists  # kept for the redactor's lifetime; each call also holds it while the core borrows it
        self._lists_info = {"allow": n_allow, "block": n_block, "id": lists_id}
        self._mask = mask
        self._sensitivity = sensitivity
        self.size: str = size
        self.variant: str = variant
        self.version: str = MODEL_VERSION
        self.labels: List[str] = [l for l in LABELS if mask >> _LABEL_ID[l] & 1]
        self.regex: bool = bool(regex)
        #: False (default): emoji are taken out of the text the model reads, so the model does not read them as PII; spans keep the offsets of
        #: the input (an emoji inside a span splits it). True: the model reads the text as given (1.0 behaviour).
        self.keep_emoji: bool = bool(keep_emoji)
        #: True: a span that starts or ends inside a word is widened to the whole word ("R23", not
        #: "[ID_NUMBER]23"). A word is a run of letters, digits and marks joined by apostrophes and hyphens; letters of scripts
        #: written without spaces (Chinese, Japanese, Thai, Khmer, ...) never extend a span, and a word ends where Hangul meets
        #: digits or Latin letters ("010-1234-5678입니다"); Korean particles after a
        #: Hangul span stay ("홍길동님께"). Masks more characters. Default True
        #: (since 1.3.0); False: spans end where the model ends them (1.2 behaviour).
        self.whole_words: bool = bool(whole_words)
        #: True: on a text the model reads as at most 8 tokens (a survey answer, a table cell), the model needs stronger
        #: evidence to read it as a person's name, so short values such as "경기", "남성" or "네이버" are left alone; a lone name
        #: is missed a little more often (large: 98.9% -> 96.5% of lone names found). Longer texts are unchanged. Default False.
        self.strict_short_names: bool = bool(strict_short_names)

    @property
    def sensitivity(self) -> float:
        """> 0 masks more (fewer misses, more over-masking), < 0 masks less; about -1..1. The O logit gets -sensitivity.
        Always finite: NaN and infinities raise ValueError (since 1.4.1)."""
        return self._sensitivity

    def set_sensitivity(self, value: float) -> None:
        """Changes the threshold without reloading the model (ValueError for NaN or an infinity; the old value stays)."""
        self._sensitivity = _check_sensitivity(value)

    def _core_model(self) -> "_core.Model":
        m = self._model
        if m is None:
            raise RuntimeError("the redactor is closed")
        return m

    def _check(self, text: str) -> "_core.Model":
        if not isinstance(text, str):
            raise TypeError(f"text must be str, not {type(text).__name__}")
        return self._core_model()

    def _extra(self, text: str) -> Optional[List[Tuple[int, int, int]]]:
        return [(s.start, s.end, _LABEL_ID[s.label]) for s in regex_spans(text)] if self.regex else None

    def detect(self, text: str) -> List[Span]:
        """Spans to mask, sorted and non-overlapping, with str indices (code points), widened to whole grapheme clusters."""
        m = self._check(text)
        spans = m.detect(
            text, self._sensitivity, self._mask, self._extra(text), self.keep_emoji, self.whole_words, self.strict_short_names, self._lists
        )
        return [Span(s, e, LABELS[l], _source(src)) for s, e, l, src in spans]

    def detect_table(
        self, text: str, delimiter: str = "auto", header: bool = True, vote: bool = True, context: str = "none"
    ) -> List[Span]:
        """Table mode for CSV, TSV, semicolon-separated and markdown pipe tables (the npm package's detectTable): the whole
        text is read at once, spans are clipped to cell values (never crossing a delimiter, quote or line break), and with
        vote a column of short values that the model finds to be names, emails, phone numbers or usernames almost
        throughout is masked whole (source "table"). delimiter: "auto" (default), ",", "\\t", ";" or "markdown"; header:
        the first row is a header row, never voted on or propagated into; context "header": with a header row, each
        non-empty data cell of a column whose non-empty values are at least 80% distinct is also read on its own as
        "<header>: <value>" and those spans are added (fewer misses on name, contact and ID columns, more PII-free cells
        masked, one more model call per such cell; default "none"). Text that is not a table gives detect(text)."""
        if delimiter not in TABLE_DELIMITERS:
            raise ValueError(f"unknown delimiter {delimiter!r} ({', '.join(map(repr, TABLE_DELIMITERS))})")
        if context not in TABLE_CONTEXTS:
            raise ValueError(f"unknown context {context!r} ({', '.join(map(repr, TABLE_CONTEXTS))})")
        m = self._check(text)
        spans = m.detect_table(
            text, self._sensitivity, self._mask, self._extra(text), TABLE_DELIMITERS[delimiter], bool(header), bool(vote),
            TABLE_CONTEXTS[context], self.keep_emoji, self.whole_words, self.strict_short_names, self._lists,
        )
        return [Span(s, e, LABELS[l], _source(src)) for s, e, l, src in spans]

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

    def redact_table(
        self,
        text: str,
        mode: str = "tag",
        *,
        delimiter: str = "auto",
        header: bool = True,
        vote: bool = True,
        context: str = "none",
        mask_char: str = "*",
        replace: Optional[Callable[[Span, str], str]] = None,
    ) -> RedactResult:
        """detect_table + redaction (modes as redact). Spans never cross cells, so delimiters, quotes and line breaks are
        kept and the output stays a valid table, as long as replace does not insert the delimiter, a quote or a line break."""
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r} (tag, mask, blank)")
        spans = self.detect_table(text, delimiter, header, vote, context)
        return RedactResult(apply_redaction(text, spans, mode, mask_char, replace), spans)

    def tokenize(self, text: str) -> List[Token]:
        """Token ids and offsets (code points) without <s> and </s>, as the npm package's tokenizer gives them."""
        return [Token(*t) for t in self._check(text).tokenize(text)]

    def info(self) -> dict:
        """What this redactor runs: package and model versions, size, variant, the two model files (published size and
        SHA-256, which the loaded bytes were checked against, and where they were read from), the model shape, the
        native kernel build and the lists (entries after merging duplicates, and lists_id or None)."""
        from . import __version__

        def pin(file: str, key: str) -> dict:
            p = model_file(self.size, file)
            return {"path": p.path, "bytes": p.bytes, "sha256": p.sha256, "local_path": self._paths[key]}

        return {
            "package_version": __version__,
            "model_version": MODEL_VERSION,
            "size": self.size,
            "variant": self.variant,
            "engine": "native",
            "files": {"model": pin(self.variant, "model"), "tokenizer": pin("tokenizer", "tokenizer")},
            **self._core_model().info(),
            "build": _core.build_info(),
            "lists": dict(self._lists_info),
        }

    def close(self) -> None:
        """Releases the model (calls running in other threads finish first)."""
        self._model = None  # the lists stay: a call that already holds the model must still pass them

    def __enter__(self) -> "Redactor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Redactor(size={self.size!r}, variant={self.variant!r}, sensitivity={self._sensitivity}, regex={self.regex}, keep_emoji={self.keep_emoji}, whole_words={self.whole_words}, strict_short_names={self.strict_short_names})"


def create_redactor(
    size: str = DEFAULT_SIZE,
    variant: Optional[str] = None,
    *,
    sensitivity: float = 0.0,
    labels: Optional[Iterable[str]] = None,
    regex: bool = False,
    keep_emoji: bool = False,
    whole_words: bool = True,
    strict_short_names: bool = False,
    allow: Optional[Iterable[AllowEntry]] = None,
    block: Optional[Iterable[BlockEntry]] = None,
    lists_id: Optional[str] = None,
    model_dir: Optional[PathLike] = None,
    cache_dir: Optional[PathLike] = None,
    model_url: Optional[str] = None,
    on_progress: Optional[Progress] = None,
) -> Redactor:
    """Same as Redactor(...), named like the npm package's createRedactor."""
    return Redactor(
        size, variant, sensitivity=sensitivity, labels=labels, regex=regex, keep_emoji=keep_emoji, whole_words=whole_words,
        strict_short_names=strict_short_names, allow=allow, block=block, lists_id=lists_id, model_dir=model_dir, cache_dir=cache_dir,
        model_url=model_url, on_progress=on_progress,
    )
