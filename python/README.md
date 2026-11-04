English | [한국어](https://github.com/newmean-ai/pii/blob/main/python/README.ko.md)

# newmean-pii

Detect and mask personal information in text **on your own machine**, before it is stored, logged or sent to an LLM. A
small multilingual token-classification model (54 languages) runs in a C extension; no network after the first
download, no dependencies. Python package of the npm package [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii):
same models, same spans.

```bash
pip install newmean-pii
```

```python
from newmean_pii import create_redactor

redactor = create_redactor(size="medium")
redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다").text
# '제 메일은 [EMAIL], 전화는 [PHONE]입니다'
```

Python 3.9+. Wheels for Linux (x86-64, arm64), macOS (arm64, x86-64) and Windows (x86-64); other platforms build
from the sdist with a C11 compiler.

## Models

| size | file | notes |
|---|---|---|
| `nano` | 2.6 MB | one shared layer applied 4 times, rank-32 embeddings |
| `small` | 7.1 MB | 4 layers, rank-64 embeddings |
| `medium` (default) | 11.3 MB | 6 layers, rank-192 embeddings |
| `large` | 15.5 MB | 6 layers, full (pruned-vocabulary) embeddings |

Plus a 0.8 MB tokenizer. On first use the files are downloaded from Hugging Face (`newmean-ai/pii-<size>`, tag `v1.1.0`)
into `~/.cache/newmean-pii` and checked against SHA-256 values pinned in this package; every later load checks them again.

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee. On newmean-pii-dataset (public test, 54 languages)
the models miss 1.6% (large) to 2.9% (nano) of PII characters; the full comparison is in the
[repository README](https://github.com/newmean-ai/pii#results).

## Labels

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, and two
level 2 labels for indirect identifiers: `PERSON_L2` (a surname with a title, a nickname, initials) and `URL_L2` (public
profile or page URLs). Pass `labels=LEVEL1_LABELS` to leave level 2 visible.

## API

```python
create_redactor(               # or Redactor(...), same arguments
    size="medium",             # "nano" | "small" | "medium" | "large"
    variant=None,              # "q4" | "q4e"; default q4e for large/medium, q4 for small/nano
    *,
    sensitivity=0.0,           # > 0 masks more, < 0 masks less (about -1..1); NaN or infinity raises ValueError since 1.4.1
    labels=None,               # labels to mask; default all (LABELS); unknown names raise ValueError
    regex=False,               # also mask a few patterns (email, URL, card/account, phone, Korean resident number);
                               # off by default: almost no extra coverage, double the over-masking
    keep_emoji=False,          # True: the model also reads emoji (1.0 behaviour); default: emoji are never read or masked
    whole_words=True,          # default True: a span that ends inside a word covers the whole word
    strict_short_names=False,  # True: short texts (<= 8 tokens) need stronger evidence to be read as names
    allow=None,                # values that are not PII in your data: str or (str, label) (see Lists); since 1.4.0
    block=None,                # values that are PII in your data: (str, label) (see Lists); since 1.4.0
    lists_id=None,             # your name for the lists, shown in info()["lists"]["id"]; since 1.4.0
    model_dir=None,            # folder with tokenizer.web.json and onnx/model_<variant>.onnx; never downloads
    cache_dir=None,            # default: $NEWMEAN_PII_CACHE, $XDG_CACHE_HOME/newmean-pii or ~/.cache/newmean-pii
    model_url=None,            # base URL of a mirror of newmean-ai/pii-<size> (https://, http:// or file://)
    on_progress=None,          # callback(file, loaded_bytes, total_bytes)
) -> Redactor

redactor.detect(text)                  # -> [Span(start, end, label, source)], sorted, non-overlapping
redactor.redact(text, mode="tag")      # -> RedactResult(text, spans); "tag" -> [EMAIL], "mask" -> *****, "blank" -> removed
redactor.redact(text, mask_char="#", replace=None)  # replace(span, original) -> str overrides mode
redactor.detect_table(text, delimiter="auto", header=True, vote=True, context="none")  # table mode (see Tables)
redactor.redact_table(text, mode="tag", *, delimiter="auto", header=True, vote=True, context="none", mask_char="*", replace=None)
redactor.set_sensitivity(value)        # no reload; same ValueError for NaN or infinity
redactor.tokenize(text)                # -> [Token(id, start, end)]
redactor.info()                        # package_version, model_version, size, variant, files (path, bytes, sha256), shape, build,
                                       # lists (allow, block, id)
redactor.close()                       # also: with create_redactor() as r: ...

apply_redaction(text, spans, mode="tag", mask_char="*", replace=None)
regex_spans(text)                      # the pattern layer alone
fetch_model(size, variant=None, cache_dir=None)  # download and verify ahead of time; returns the folder
```

- **Offsets** are Python `str` indices (code points): `text[span.start:span.end]` is the masked text, emoji included. The
  npm package uses UTF-16 indices for the same spans. Spans are widened to whole grapheme clusters, so an emoji, flag or
  combining sequence is masked whole or kept whole. `source` is `"model"`, `"regex"`, `"model+regex"`, `"table"` or `"block"`.
- **Emoji**: by default emoji are taken out of the text the model reads, so the model does not read them as PII; offsets still refer to the
  input, and an emoji inside a span splits it in two. `keep_emoji=True` gives the model the text as given (1.0 behaviour).
- **Whole words**: by default (since 1.3.0), a span that starts or ends inside a word is widened to the whole word ("R23" → `[ID_NUMBER]`, not `[ID_NUMBER]23`); letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, …) never extend a span, and a word ends where Hangul meets digits or Latin letters (「010-1234-5678입니다」 → `[PHONE]입니다`), and Korean particles after a Hangul span are left as they are (「홍길동님께」 → `[PERSON]님께`). It changes where a span ends, not what is masked: a short value the model misreads as a name, such as 「경기도」, becomes `[PERSON]` rather than `[PERSON]기도`; `strict_short_names=True` leaves such values alone. `whole_words=False` gives the 1.2 spans.
- **Short values**: with `strict_short_names=True`, a text the model reads as at most 8 tokens (a survey answer, a table cell) needs stronger evidence to be read as a person's name: 「경기」, 「남성」 and 「네이버」 are left alone, while 「김민지」 and "Maria Lopez" are still found. With large, short survey items without personal information are masked 4.3% of the time instead of 7.9%, and 96.5% of lone names are found instead of 98.9%. Longer texts are unchanged.
- **Versions**: `redactor.version` is the model release (1.1.0); `newmean_pii.__version__` and `info()["package_version"]` give the
  package version, for audit records.
- **`tag` mode** writes the label family: `PERSON_L2` becomes `[PERSON]`, `URL_L2` becomes `[URL]`. `mask` writes one
  `mask_char` per code point.
- **Threads**: a redactor can be shared. The model is immutable and the GIL is released while it runs, so a thread
  pool detects in parallel (one core per call):

  ```python
  from concurrent.futures import ThreadPoolExecutor
  with ThreadPoolExecutor(4) as pool:
      results = list(pool.map(redactor.redact, texts))
  ```

- **Speed**: one thread on Apple M5 Pro, about 10 ms for a short text with nano and 15 ms with medium; a 1,000-token text
  takes 0.4 s (nano) to 0.55 s (medium). Long texts are split into overlapping 256-token windows.
- Errors: `ValueError` for bad options (`ListError`, a `ValueError`, for bad lists), `ModelFileError` when a file is
  missing, cannot be downloaded or does not match its pin.

## Lists

Since 1.4.0 you can tell the redactor what you already know about your data:

```python
import unicodedata

redactor = create_redactor(
    allow=["제주", "배민", ("경기", "PERSON")],  # a str allows any label
    block=[("박지훈", "PERSON"), ("새솔고등학교", "ORG")],
    lists_id="survey-2026-11 v3",
)
redactor.redact(unicodedata.normalize("NFC", "제주, 박지훈")).text  # '제주, [PERSON]'
# without the lists, the medium model gives '[PERSON], [PERSON]'
```

- `block`: every occurrence of an entry that starts and ends on a word boundary is masked with the entry's label (source
  `"block"`), also where the model finds nothing and whatever `labels` enables. A Hangul entry also matches before Korean
  particles (「박지훈님께」 → 「[PERSON]님께」); "Kim" does not match inside "Kimberly", nor in "Kim's" or "Kim-Lee"
  (apostrophes and hyphens join words, as in `whole_words`); in scripts written without spaces any occurrence matches. A
  region with a block match takes the block label and is never allowed.
- `allow`: a span whose whole text is an entry is not masked; with a label, only spans of that label family (PERSON covers
  PERSON_L2, URL covers URL_L2). The test is on the whole span, so it depends on where the model ends a span: a span
  「제주도」 is not allowed by "제주"; list both forms when both occur.
- Entries: `allow` takes `str` or `(str, label)` (label `None`: any label), `block` takes `(str, label)`; either list can be
  any iterable except `str` and `bytes`. A 2-tuple passed as the list itself is read as two entries: `block=("Kim", "PERSON")`
  raises `bad_shape`, and `allow=("경기", "PERSON")` silently allows "경기" and "PERSON" for any label. Write
  `block=[("Kim", "PERSON")]` and `allow=[("경기", "PERSON")]` for one labelled entry.
- Matching folds ASCII letters only (`KIM` = `kim`); nothing else is normalized. Pass NFC text and entries: files exported on
  macOS or copied from PDFs may hold NFD Hangul, which never matches (`unicodedata.normalize("NFC", text)`).
- Exact forms only: 「박 지훈」 and 「박지훈」 are separate entries. A one-syllable entry such as 「김」 also masks every
  word that is 김 plus particles (「김은」, 「김도」).
- In table mode, block entries match inside each cell and do not vote; allowed spans leave before the column vote.
- Up to 10,000 entries per list, 1 to 64 code points each, no line breaks, no leading or trailing white space. Invalid lists
  raise `newmean_pii.ListError` (a `ValueError`; `.list`: "allow", "block" or "meta" for `lists_id`; `.index`, `None` for
  `lists_id` and for the list itself; `.reason`: `bad_shape`, `too_long`, `bad_text`, `empty`, `edge_space`,
  `unknown_label`, `too_many`, `label_conflict`, `in_both`, `bad_lists_id`) before any model file is loaded. The error never
  holds an entry's text.
- `info()["lists"]` gives the entry counts after merging duplicates and your `lists_id`, for audit records; nothing derived
  from the entries' text. The lists are freed with the redactor.

## Tables

```python
redactor.detect_table(text, delimiter="auto", header=True, vote=True, context="none") -> List[Span]
redactor.redact_table(text, mode="tag", *, delimiter="auto", header=True, vote=True, context="none", mask_char="*", replace=None) -> RedactResult
```

Table mode reads a CSV, TSV, semicolon-separated or markdown pipe table as one text and clips every span to a cell value:
delimiters, quotes and line breaks are never masked, so the output is still a valid table. `delimiter` is `"auto"`
(detected), `","`, `"\t"`, `";"` or `"markdown"` (`TABLE_DELIMITERS`); quoted fields follow RFC 4180. With `vote`, a column
of short values is masked whole when the model finds names, emails, phone numbers or usernames in at least 80% of its
non-empty cells (and at least 4; columns with fewer than 5 non-empty cells do not vote); those spans have source `"table"`.
Free-text columns (median value over 40 characters or 4 words) do not vote, placeholders (`N/A`, `-`, `none`, `null`,
`없음`) are skipped, an email column needs `@` and a phone column 6 or more digits. With `header`, the first row is masked
where the model finds PII but never voted on. With `context="header"` (`TABLE_CONTEXTS`; default `"none"`) and a header
row, each non-empty data cell of a column whose non-empty values are at least 80% distinct is also read on its own as
`"<header>: <value>"` and those spans are added: fewer misses on name, contact and ID columns, more PII-free cells masked,
one more model call per such cell. Text that is not a table gives exactly `detect(text)`. A custom `replace` must not
insert the delimiter, a quote or a line break.

```python
csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free"
redactor.redact_table(csv).text
# 'name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free'
redactor.redact_table(tsv, "mask", delimiter="\t", vote=False)
```

On survey and form exports, table mode misses many names split into first and last name columns. `context="header"` cuts the
misses to about a quarter, at 2.3 to 3.7 more points of PII-free cells masked; for recall first, use it. Measurements:
[repository README](https://github.com/newmean-ai/pii#tables-csv-tsv).

## Offline use

Download once, then run without network:

```bash
python -m newmean_pii download --size medium            # into the cache; prints the folder
NEWMEAN_PII_OFFLINE=1 python app.py                     # never download; fail if a file is not cached
```

Or copy the files of the Hugging Face repo yourself (`tokenizer.web.json` and `onnx/model_<variant>.onnx`) and pass the
folder:

```python
redactor = create_redactor(size="medium", model_dir="/opt/models/pii-medium")
```

The files are checked against the pinned SHA-256 there too. For containers, run `python -m newmean_pii download` at
build time with `NEWMEAN_PII_CACHE` set to a folder inside the image.

There is also a small command line: `python -m newmean_pii redact --size nano --mode mask < input.txt`.

## Development

```bash
cd python
uv build                      # sdist (with native/core copied in) and a wheel built from it
pip install dist/*.whl pytest
pytest -q                     # fixture parity tests skip without fixtures and model copies
```

The C extension compiles `native/core` (the shared core of every native package) with the same flags as its Makefile,
so logits are bit-identical to the npm package's baseline WebAssembly engine. Tests: regex parity with the Python
baseline, redaction modes, offsets on emoji text, threads, downloads against a local HTTP server and `file://` (no
network), and, when the private fixtures and model copies exist (`PII_FIXTURES`, default `js/test/fixtures`;
`PII_MODELS`, default `js/test/models`), tokenizer parity with Hugging Face and span parity with the Python evaluator for
every run of the npm test (`PII_SIZES=nano,medium` limits them). `NEWMEAN_PII_NETWORK_TESTS=1` adds one download from
Hugging Face. Wheels: `cibuildwheel` (configured in `pyproject.toml`, workflow `.github/workflows/python-wheels.yml`).

## License

[Apache-2.0](LICENSE), for the package code and the model weights; third-party notices in [NOTICE](NOTICE). If you use
them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a
request, not a license condition). Made by [newmean.ai](https://newmean.ai).
