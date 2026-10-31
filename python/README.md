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

Plus a 0.8 MB tokenizer. On first use the files are downloaded from Hugging Face (`newmean-ai/pii-<size>`, tag `v1.0.0`)
into `~/.cache/newmean-pii` and checked against SHA-256 values pinned in this package; every later load checks them again.

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee. On newmean-pii-dataset (public test, 54 languages)
the models miss 1.6% (large) to 3.3% (nano) of PII characters; the full comparison is in the
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
    sensitivity=0.0,           # > 0 masks more, < 0 masks less (about -1..1)
    labels=None,               # labels to mask; default all (LABELS); unknown names raise ValueError
    regex=False,               # also mask a few patterns (email, URL, card/account, phone, Korean resident number);
                               # off by default: almost no extra coverage, double the over-masking
    model_dir=None,            # folder with tokenizer.web.json and onnx/model_<variant>.onnx; never downloads
    cache_dir=None,            # default: $NEWMEAN_PII_CACHE, $XDG_CACHE_HOME/newmean-pii or ~/.cache/newmean-pii
    model_url=None,            # base URL of a mirror of newmean-ai/pii-<size> (https://, http:// or file://)
    on_progress=None,          # callback(file, loaded_bytes, total_bytes)
) -> Redactor

redactor.detect(text)                  # -> [Span(start, end, label, source)], sorted, non-overlapping
redactor.redact(text, mode="tag")      # -> RedactResult(text, spans); "tag" -> [EMAIL], "mask" -> *****, "blank" -> removed
redactor.redact(text, mask_char="#", replace=None)  # replace(span, original) -> str overrides mode
redactor.set_sensitivity(value)        # no reload
redactor.tokenize(text)                # -> [Token(id, start, end)]
redactor.close()                       # also: with create_redactor() as r: ...

apply_redaction(text, spans, mode="tag", mask_char="*", replace=None)
regex_spans(text)                      # the pattern layer alone
fetch_model(size, variant=None, cache_dir=None)  # download and verify ahead of time; returns the folder
```

- **Offsets** are Python `str` indices (code points): `text[span.start:span.end]` is the masked text, emoji included. The
  npm package uses UTF-16 indices for the same spans. `source` is `"model"`, `"regex"` or `"model+regex"`.
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
- Errors: `ValueError` for bad options, `ModelFileError` when a file is missing, cannot be downloaded or does not match
  its pin.

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

Span edges can differ from the npm package by one combining mark in rare text (the native tokenizer follows Hugging Face
offsets; see `native/core/README.md`).

## License

[Apache-2.0](LICENSE), for the package code and the model weights; third-party notices in [NOTICE](NOTICE). If you use
them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a
request, not a license condition). Made by [newmean.ai](https://newmean.ai).
