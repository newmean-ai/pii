import bisect
import inspect
import math
from typing import AbstractSet, Any, Callable, Iterable, List, Optional, Sequence, Tuple

from . import _core
from ._lists import AllowEntry, BlockEntry, check_lists_id, make_lists
from ._models import DEFAULT_SIZE, DEFAULT_VARIANT, MODEL_VERSION, SIZES, VARIANTS, PathLike, Progress, load_file, model_file
from ._regex import regex_spans
from ._types import RedactResult, Span, Token
from ._vault import (
    Vault,
    VaultHit,
    apply_vault,
    apply_vault_ranges,
    escaped_readings,
    joined_to_number,
    narrowed,
    tag_ranges,
    value_spans,
    vault_tags,
)

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


def _checked_options(
    size: str,
    variant: Optional[str],
    sensitivity: float,
    labels: Optional[Iterable[str]],
    allow: Optional[Iterable[AllowEntry]],
    block: Optional[Iterable[BlockEntry]],
    lists_id: Optional[str],
) -> Tuple[str, int, Optional["_core.Lists"], int, int, Optional[str], float]:
    """The options Redactor checks before it loads a model file, in its order (size, variant, labels, lists_id, allow,
    block, sensitivity): (variant, label mask, lists, allow count, block count, lists_id, sensitivity). Raises ValueError
    or ListError."""
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
    lists_id = check_lists_id(lists_id)
    lists, n_allow, n_block = make_lists(allow, block)
    sensitivity = _check_sensitivity(sensitivity)  # after the lists, as in every package
    return variant, mask, lists, n_allow, n_block, lists_id, sensitivity


def with_vault_spans(
    text: str, spans: List[Span], vault: Vault, *, loose: bool = False, drop: Optional[Callable[[VaultHit], bool]] = None
) -> List[Span]:
    """Adds the vault's known values to spans (source "vault"). A known value that detected spans cover only in part
    ("Kim" of a known "Kim Minsu") is masked whole: the overlapping spans are merged into one span over both, which keeps
    the known value's placeholder when the merged span is exactly that value. drop: known values found that are left
    out (for the middlewares: a number joined to a longer one in another reading of the text)."""
    hits = vault.find(text, loose=loose)  # in order, apart
    if drop is not None:
        hits = [h for h in hits if not drop(h)]
    if not hits:
        return spans
    # one pass over both (spans in order; they are sorted first if they are not)
    ordered = spans if all(spans[i - 1].start <= spans[i].start for i in range(1, len(spans))) else sorted(spans, key=lambda s: s.start)
    out: List[Span] = []
    i, n = 0, len(ordered)
    for h in hits:
        while i < n and ordered[i].end <= h.start:
            out.append(ordered[i])
            i += 1
        over: List[Span] = []
        if out and out[-1].end > h.start:
            over.append(out.pop())  # one that reaches this far (merged before)
        while i < n and ordered[i].start < h.end:
            over.append(ordered[i])
            i += 1
        if over and all(s.start <= h.start and s.end >= h.end for s in over):
            out.extend(over)  # already masked whole
            continue
        start = min([h.start] + [s.start for s in over])
        end = max([h.end] + [s.end for s in over])
        label = h.label if (start, end) == (h.start, h.end) else (over[0].label if over else h.label)
        out.append(Span(start, end, label, "vault"))
    out.extend(ordered[i:])
    return out


def model_values(text: str, spans: Iterable[Span]) -> List[Span]:
    """The spans a model found, each without what its boundary took from around the value (value_spans), so that the
    vault learns the value and an answer gets it back without that. The same as modelValues in js/src/redactor.ts."""
    return value_spans(text, list(spans))


def redact_with_vault(
    text: str,
    found: List[Span],
    vault: Vault,
    *,
    keep: Optional[AbstractSet[str]] = None,
    loose: bool = False,
    generation: Optional[int] = None,
    drop: Optional[Callable[[VaultHit], bool]] = None,
) -> Tuple[str, List[Span], List[str]]:
    """text masked with a vault, in one step for the vault: the spans the model found plus the vault's known values, and
    then every value this text added to the vault masked wherever it occurs again in it ("I met Alice. Say Alice twice."
    when the model finds only the first "Alice"). The found spans lose what their edges hold that is not part of the
    value first (value_spans: "Kim Minsu." at the end of a sentence is the value "Kim Minsu"). Returns the text, the
    final spans and the placeholders put in. With generation, RuntimeError if the vault was cleared since (nothing is
    written). drop as in with_vault_spans."""
    found = value_spans(text, found)
    with vault._lock:
        vault._unchanged(generation)
        spans = with_vault_spans(text, found, vault, loose=loose, drop=drop)
        out, tags = apply_vault(text, spans, vault, keep)  # the vault learns this text's values here
        # also what the model found inside a larger merged span ("Kim Lee" of "Alex Kim Lee"), so it is masked where it
        # stands alone
        for s in found:
            value = text[s.start : s.end]
            if value.strip() and not (keep and any(t in keep for t in vault_tags(value))):
                vault._tag_found(s.label, value)
        # until nothing new is found: every round either masks more of the text or merges spans, so this ends
        while True:
            nxt = with_vault_spans(text, spans, vault, loose=loose, drop=drop)
            if [(s.start, s.end) for s in nxt] == [(s.start, s.end) for s in spans]:
                return out, spans, tags
            spans = nxt
            out, tags = apply_vault(text, spans, vault, keep)


def _with_known_ranges(ranges: List[Any], view: Sequence[Any], hits: Iterable[Any], age: Callable[[str], Optional[int]]) -> List[Any]:
    """ranges (in order, apart; see apply_vault_ranges) with hits: what was found in the reading view of the same text
    (the vault's known values, or the spans of that reading; anything with start, end, label and value); without a
    map, view is the text as it is written; with a third item, view is a reading of a part of that text (outside the
    part, the reading is the text as written). As in with_vault_spans: a hit that ranges cover only in part is masked
    whole, as one range over both; it keeps the hit's own value when that range is exactly the hit."""
    read, where = view[0], view[1]
    base: Optional[str] = view[2] if len(view) > 2 else None
    out: List[Any] = []
    i, n = 0, len(ranges)
    for h in hits:
        a, b = (h.start, h.end) if where is None else (where[h.start], where[h.end])
        while i < n and ranges[i][1] <= a:
            out.append(ranges[i])
            i += 1
        over = []
        if out and out[-1][1] > a:
            over.append(out.pop())  # one that was merged just before
        while i < n and ranges[i][0] < b:
            over.append(ranges[i])
            i += 1
        if len(over) == 1 and over[0][0] <= a and over[0][1] >= b:
            # already masked whole. When it is exactly this part of the text, the hit's own value is used (one
            # placeholder for a value however it is written), unless the vault holds what is there already and has
            # held it longer (a known "Kim+Minsu" in a path stays that, also when "Kim Minsu" is known too).
            mine, theirs = age(over[0][3]), age(h.value)
            older = mine is not None and theirs is not None and mine <= theirs
            out.append((a, b, h.label, h.value) if (over[0][0], over[0][1]) == (a, b) and not older else over[0])
            continue
        lo = min([a] + [r[0] for r in over])
        hi = max([b] + [r[1] for r in over])
        if (lo, hi) == (a, b):
            out.append((a, b, h.label, h.value))
        else:
            # both as one value, as this reading has it (over whole escapes): the last place in the reading at or
            # before lo, the first at or after hi
            if where is None:
                out.append((lo, hi, over[0][2], read[lo:hi]))
            else:
                start = max(0, bisect.bisect_right(where, lo) - 1)
                end = min(len(where) - 1, bisect.bisect_left(where, hi))
                a2, b2 = where[start], where[end]
                before = base[lo:a2] if base is not None and lo < a2 else ""
                after = base[b2:hi] if base is not None and hi > b2 else ""
                out.append((min(lo, a2), max(hi, b2), over[0][2], before + read[start:end] + after))
    out.extend(ranges[i:])
    return out


def redact_readings(
    text: str,
    found: List[Span],
    vault: Vault,
    *,
    keep: Optional[AbstractSet[str]] = None,
    loose: bool = False,
    through: Optional[Tuple[Tuple[str, Sequence[int]], List[Span]]] = None,
    generation: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """text masked with a vault in every way it can be read, in one step for the vault. found are the detected spans of
    the text as it is written. through, when the text holds Unicode escapes: its reading through them
    (json_escapes_view) and the spans detected in that reading (the model is then given that reading, and found is
    empty). Both readings count for the values the vault holds, because "\\u1234" is a letter in JSON and a folder in
    "C:\\Users\\u1234". Each is masked as redact_with_vault does, and then the vault's known values
    are searched in every reading of the text, the others too (escaped_readings: a value with a line break, written as
    "\\n" in a log field or in JSON inside other text; a value in a URL), and also where they are written another way
    (find with variants). What one reading finds takes in the ranges of another that cover
    part of it, so a detector that finds only "12 Main St" in "12 Main St\\nSeoul 04524" cannot cut a known address in
    two. The placeholders go into the text as it is written, each over whole escapes. keep: placeholders that stay as
    they are; other placeholder-like text is escaped. Returns the text and the placeholders put in. With generation,
    RuntimeError if the vault was cleared since."""
    # placeholder-like text, as the reading through Unicode escapes has it (it has every such part of the text as written)
    read, where = through[0] if through is not None else (text, None)
    at = (lambda i: i) if where is None else where.__getitem__
    brackets = [(at(t[0]), at(t[0] + 1)) for t in tag_ranges(read) if not (keep and t[2] in keep)]
    # (read in parts around their escapes, as far as a known value can reach: escapes far apart make no more readings)
    others = [r for r in escaped_readings(text, vault._reach()) if through is None or r[0] != through[0][0]]
    # A known value found at [a, b) of the text is left out where it is part of a longer number in the text as written
    # or in any other reading that has [a, b) whole: "4524" in "1e+4524", and also in "1e%2B4524" and "1e\u002B4524",
    # whose escaped sign joins like "+" (as it does in the form-field reading of "1e+4524", where that "+" is a space,
    # the text as written has it), and in "4524%2E7". An exponent joins numbers only ("12 Main St" stays found after "4E+").
    views = ([through[0]] if through is not None else []) + others
    # (the readings of the whole text, and those of parts of it, which come in the order of the text: their starts and
    # their ends only grow, so the parts that hold a place are found by a search, not by looking at every part)
    wholes = [v for v in views if len(v) <= 3]
    parts = [v for v in views if len(v) > 3]
    starts = [v[1][0] for v in parts]

    def holds(view: Any, a: int, b: int, value: str) -> bool:
        read, where = view[0], view[1]
        i = bisect.bisect_left(where, a)
        if i < len(where) and where[i] == a:
            j = bisect.bisect_left(where, b, i)
            return j < len(where) and where[j] == b and joined_to_number(read, i, j, value)
        return False

    # (the text as the vault reads it, full-width ASCII as ASCII: "１ｅ＋４５２４" is "1e+4524", where 4524 is an exponent)
    plain = narrowed(text)

    def joined(a: int, b: int, value: str) -> bool:
        if joined_to_number(plain, a, b, value) or any(holds(v, a, b, value) for v in wholes):
            return True
        k = bisect.bisect_right(starts, a) - 1
        while k >= 0 and parts[k][1][-1] >= b:
            if holds(parts[k], a, b, value):
                return True
            k -= 1
        return False

    def masked(
        reading: str, spans: List[Span], where: Optional[Sequence[int]] = None
    ) -> Tuple[List[VaultHit], List[VaultHit]]:
        """One reading masked with the vault: its final spans, each stopping at a kept placeholder and going on after it."""
        kept = [t for t in tag_ranges(reading) if t[2] in keep] if keep else []
        out: List[VaultHit] = []
        # what the model found that is, as a whole, a value the vault holds written another way ("Kim\nMinsu" for a
        # known "Kim Minsu", "8.21023456789e11" for 821023456789) is that value: it gets no placeholder of its own. The
        # spans are taken in order and each new value is learned at once, so the same holds for two spellings found in
        # one text.
        same: List[VaultHit] = []
        other: List[Span] = []
        for s in spans:
            written = reading[s.start : s.end]
            known = vault.whole(written)
            if known is not None and known.value != written:
                same.append(VaultHit(s.start, s.end, known.label, known.value))
                continue
            if known is None and written.strip() and not (keep and any(t in keep for t in vault_tags(written))):
                vault._tag_found(s.label, written)
            other.append(s)
        k = 0
        place = (lambda i: i) if where is None else where.__getitem__  # (where a place of the reading is in the text)
        drop = None if loose else (lambda h: joined(place(h.start), place(h.end), h.value))
        for s in redact_with_vault(reading, other, vault, keep=keep, loose=loose, drop=drop)[1]:
            start = s.start
            while k < len(kept) and kept[k][1] <= start:
                k += 1
            j = k
            pieces = []
            while j < len(kept) and kept[j][0] < s.end:
                pieces.append((start, kept[j][0]))
                start = max(start, kept[j][1])
                j += 1
            pieces.append((start, s.end))
            out.extend(VaultHit(x, y, s.label, reading[x:y]) for x, y in pieces if x < y and reading[x:y].strip())
        return out, same

    # (what the edges of a found span hold that is not part of the value is left out before the vault learns it)
    found = value_spans(text, found)
    through_found = value_spans(through[0][0], through[1]) if through is not None else []
    with vault._lock:
        vault._unchanged(generation)
        ranges: List[Any] = []
        # until the vault learns nothing new (a merged range is a new value, which can occur again elsewhere in the
        # text). Every round that goes on has added a value to the vault, and with it the ranges only grow or merge or
        # take an older spelling, so this ends; the bound is only a guard against a loop that would not
        for _ in range(4 * len(text) + 4):
            written, same = masked(text, found)
            ranges = _with_known_ranges([tuple(h) for h in written], (text, None), same, vault.age)
            if through is not None:
                read, same = masked(through[0][0], through_found, through[0][1])
                ranges = _with_known_ranges(_with_known_ranges(ranges, through[0], read, vault.age), through[0], same, vault.age)
            # the vault's known values in every reading, also where they are written another way (white space, a
            # number's spelling)
            # (what is found in one reading is looked at in the others as well: "4524" behind the "+" of "1e+4524" stands
            # free in the form-field reading, where the "+" is a space, and is an exponent all the same)
            for r in [(text, None)] + views:
                hits = vault.find(r[0], loose=loose, variants=True)
                where = r[1]
                if len(r) > 3:
                    # (in a reading of a part of the text, what neither touches nor holds any of its escapes is found in
                    # the text as written, where what stands around it is known)
                    core = r[3]
                    hits = [h for h in hits if where[h.start] <= core[1] and where[h.end] >= core[0]]
                if not loose:
                    place = (lambda i: i) if where is None else where.__getitem__
                    hits = [h for h in hits if not joined(place(h.start), place(h.end), h.value)]
                ranges = _with_known_ranges(ranges, r, hits, vault.age)
            size = len(vault)
            for _a, _b, label, value in ranges:
                vault._tag_found(label, value)
            if len(vault) == size:
                break
        return apply_vault_ranges(text, ranges, vault, brackets)


def apply_redaction(
    text: str,
    spans: Iterable[Span],
    mode: str = "tag",
    mask_char: str = "*",
    replace: Optional[Callable[[Span, str], str]] = None,
    vault: Optional[Vault] = None,
    keep: Optional[AbstractSet[str]] = None,
) -> str:
    """Replaces each span (sorted, non-overlapping): "tag" -> "[EMAIL]" (PERSON_L2 -> [PERSON], URL_L2 -> [URL]),
    "mask" -> one mask_char per character (code point), "blank" -> removed. replace(span, original) overrides mode, and
    vault (since 1.7.2) overrides both: each span becomes the vault's numbered tag for its text ("[PERSON_1]"), and
    placeholder-like text left unmasked is escaped ("［PERSON_1]") except the placeholders in keep (text a model wrote
    with this vault's placeholders)."""
    if mode not in _MODES:
        raise ValueError(f"unknown mode {mode!r} (tag, mask, blank)")
    if vault is not None:
        # detection ran on the original text, so block lists and the model saw it as written. The vault learns each span
        # without what its edges hold that is not part of the value (value_spans), as in redact_with_vault
        return apply_vault(text, value_spans(text, list(spans)), vault, keep)[0]
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
        # allow: values you know are not PII in your data (survey categories: regions, brands, jobs), str or (str, label):
        # a span whose whole text is an entry is not masked; with a label, only spans of that label family (PERSON covers
        # PERSON_L2, URL covers URL_L2). block: (str, label) values you know are PII: every occurrence that starts and ends
        # on a word boundary is masked with the entry's label (source "block"), whatever labels enables; a Hangul entry also
        # matches before Korean particles ("박지훈님께"). ASCII letters compare case-insensitively, nothing else is
        # normalized (pass NFC text). At most 10,000 entries of 1 to 64 code points per list. lists_id: your name for the
        # lists, in info()["lists"]. Since 1.4.0. Validated before any model file is loaded (ListError: list, index and
        # reason, never the text).
        variant, mask, lists, n_allow, n_block, lists_id, sensitivity = _checked_options(
            size, variant, sensitivity, labels, allow, block, lists_id)

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
        return self._detect_at(text, self._settings())

    def _settings(self) -> Tuple[float, bool, bool, bool, bool]:
        """What can change a redactor's detections after it is created, as one snapshot."""
        return (self._sensitivity, self.regex, self.keep_emoji, self.whole_words, self.strict_short_names)

    def _detect_at(self, text: str, settings: Tuple[float, bool, bool, bool, bool]) -> List[Span]:
        """detect with the given snapshot of the settings, so that a caller that keeps results per setting (the vault
        middleware) knows which settings a result belongs to, whatever is changed while the model runs."""
        sensitivity, regex, keep_emoji, whole_words, strict_short_names = settings
        m = self._check(text)
        extra = [(s.start, s.end, _LABEL_ID[s.label]) for s in regex_spans(text)] if regex else None
        spans = m.detect(text, sensitivity, self._mask, extra, keep_emoji, whole_words, strict_short_names, self._lists)
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
        vault: Optional[Vault] = None,
        keep: Optional[AbstractSet[str]] = None,
    ) -> RedactResult:
        """Masked text and the spans: mode "tag" -> [EMAIL], "mask" -> *****, "blank" -> removed; or replace(span, original).
        With vault (since 1.7.2): numbered placeholders that vault.restore() undoes ("[PERSON_1]", the same value always
        the same tag), and the vault's known values are also masked where they occur again and the model does not find
        them (spans with source "vault"). keep as in apply_redaction. RuntimeError if the vault is cleared while the
        model reads the text (nothing is written into the cleared vault)."""
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r} (tag, mask, blank)")
        if vault is None:
            spans = self.detect(text)
            return RedactResult(apply_redaction(text, spans, mode, mask_char, replace), spans)
        started = vault.generation
        out, spans, _tags = redact_with_vault(text, self.detect(text), vault, keep=keep, generation=started)
        return RedactResult(out, spans)

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
        vault: Optional[Vault] = None,
    ) -> RedactResult:
        """detect_table + redaction (modes as redact). Spans never cross cells, so delimiters, quotes and line breaks are
        kept and the output stays a valid table, as long as replace does not insert the delimiter, a quote or a line break.
        vault numbers the spans as in redact (no search for known values, so spans stay inside cells)."""
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r} (tag, mask, blank)")
        started = None if vault is None else vault.generation
        spans = self.detect_table(text, delimiter, header, vote, context)
        if vault is not None:
            spans = value_spans(text, spans)  # (what the vault learns: as in apply_redaction)
            return RedactResult(apply_vault(text, spans, vault, generation=started)[0], spans)
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


def check_redactor_options(**options: Any) -> None:
    """Checks keyword options of create_redactor as it would, without loading a model file: an unknown name raises
    TypeError; size, variant, labels, lists_id, allow, block and sensitivity raise ValueError or ListError as Redactor
    does. For adapters that load the model on their first call, so that a typo raises when they are built. Iterators
    among the options are read: pass lists."""
    bound = inspect.signature(create_redactor).bind(**options)
    bound.apply_defaults()
    a = bound.arguments
    _checked_options(a["size"], a["variant"], a["sensitivity"], a["labels"], a["allow"], a["block"], a["lists_id"])
