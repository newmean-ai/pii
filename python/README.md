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

To mask people but keep company and shop names, leave `ORG` out; a shop you know that the model reads as a person (one
named after its owner) goes on the allow list ([Lists](#lists)):

```python
from newmean_pii import LABELS, create_redactor

redactor = create_redactor(size="medium", labels=[l for l in LABELS if l != "ORG"], allow=[("Wendy's", "PERSON")])
redactor.redact("Customer Maria Lopez disputes a charge from Jo Malone London and one from Tiffany & Co.").text
# 'Customer [PERSON] disputes a charge from Jo Malone London and one from Tiffany & Co.'
```

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
fetch_model(size, variant=None, cache_dir=None)  # download and verify ahead of time; returns the folder, or raises
                                                 # ModelFileError if a file cannot be written there (see Offline use)
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

- **Speed**: one thread on Apple M5 Pro, about 6.5 ms for a short text with nano and 10 ms with medium; a 1,000-token text
  takes 0.22 s (nano) to 0.35 s (medium) (arm64; since 1.5.0). Long texts are split into overlapping 256-token windows.
  x86-64, one thread on an AMD EPYC 9V74 (a GitHub Actions `ubuntu-24.04` runner, 2026-11-25; 227 short texts of 40
  tokens on average, and one text of 1,010 tokens): with `sse`, about 23 ms for a short text with nano and 35 ms with
  medium, 0.63 s (nano) to 0.94 s (medium) for 1,000 tokens; with `avx2`, about 13 ms and 19 ms, 0.27 s to
  0.41 s (a manylinux wheel of the 1.7.2 kernel, `sse` forced with `NEWMEAN_PII_KERNEL=sse`).
- **Kernel**: `info()["build"]` names the matrix kernel the process runs. Kernels without `+fma` give the same logits bit
  for bit on every CPU, equal to the npm package's baseline WebAssembly build; `+fma` kernels fuse multiply and add, are
  faster, and differ from them in the last bits of the logits, so a span can differ in rare cases. arm64 runs `neon+fma`;
  Windows wheels (MSVC) run `scalar`.
  Since 1.7.2 (`sse` before), Linux and macOS wheels on x86-64 choose the kernel once per process, at the first
  detect or `build_info` call: `avx2` (8 floats at a time) where the CPU and the operating system support AVX2, else
  `sse`. `NEWMEAN_PII_KERNEL=avx2+fma`, set before that call, opts in to the fused kernel on CPUs with AVX2 and FMA3
  (logits equal to `neon+fma`); `NEWMEAN_PII_KERNEL=sse` forces the 4-wide one. An unknown name, or a kernel the CPU
  lacks, keeps the default.
  A source build with fused multiply-add, for x86-64 CPUs with AVX2 and FMA3 (Intel Haswell, AMD Zen and later; the
  build runs only on such CPUs):

  ```bash
  CFLAGS="-march=x86-64-v3 -DPII_FMA" pip install --no-binary newmean-pii newmean-pii
  ```

  Its `info()["build"]` ends in `+fma`: `avx2+fma`, or `sse+fma` with `NEWMEAN_PII_KERNEL=sse+fma`.
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
  macOS or copied from PDFs may hold NFD Hangul, which never matches (`unicodedata.normalize("NFC", text)`). These rules
  are the lists'; a vault finds its values in more spellings ("How a known value is found again" under
  [Integrations](#integrations)).
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

## Integrations

Adapters for LangChain, LiteLLM, Microsoft Presidio and Langfuse: mask personal information before a prompt
reaches a hosted LLM or before a trace is stored. Every adapter runs the model in your own process, and none of the
libraries is a dependency of this package (`pip install "newmean-pii[langchain]"`, `"newmean-pii[litellm]"` or
`"newmean-pii[presidio]"` adds one).

**Where it runs.** `redact` and the middlewares mask in your app's process, before the text leaves it; only there can a
vault put the values back. The LiteLLM guardrail masks in the proxy: one place for every app that calls models through
it, after the text has reached the proxy, and nothing is put back. A cloud provider's own PII filter (such as Amazon
Bedrock Guardrails) runs after the text has reached the provider; it can stay on next to this package, and then sees
placeholders. To keep personal information out of logs and traces, mask what comes back as well (`mode: "post_call"` in
the guardrail, `pii_mask` for Langfuse, which masks the parts of a trace Langfuse passes to it).

**LangChain** (1.4.0+): a detector for `PIIMiddleware` that reports every label in one model call.

```python
from langchain.agents import create_agent
from newmean_pii.langchain import pii_middleware

agent = create_agent("openai:gpt-6.1", middleware=[pii_middleware(size="medium")])
# "Call Kim Minsu on 010-1234-5678" reaches the model as "Call [REDACTED_PERSON] on [REDACTED_PHONE]"
```

`pii_middleware(redactor=None, *, strategy="redact", apply_to_input=True, apply_to_output=False,
apply_to_tool_results=False, labels=None, name="pii", **create_redactor_options)` returns a `PIIMiddleware`; `strategy`
is LangChain's (`"redact"`, `"mask"`, `"hash"`, `"block"`). `pii_detector(redactor=None, *, labels=None, **options)`
returns the detector alone, `(text) -> [{"type", "value", "start", "end"}]` with the label in lower case as `type`, for
your own `PIIMiddleware(...)` or any code that takes such a function. Without a redactor the model is loaded on the
first call.

**Agents: mask what the agent reads.** Personal information reaches the model on later turns too: a CRM lookup, a
search result or a PDF brings in names and contacts the user never typed. Mask tool results as well:
`pii_middleware(redactor, apply_to_tool_results=True)`, or with a vault (below), which masks user messages, earlier
answers and tool results on every call. For retrieval chains, `mask_documents(docs, redactor)` returns masked copies of
LangChain `Document`s (metadata kept). Examples: [`examples/agent_tool_results.py`](examples/agent_tool_results.py) (an
agent with a CRM tool and a ticket search) and [`examples/mask_pdf.py`](examples/mask_pdf.py) (the models read text, so
extract a PDF's text with pypdf first).

**Putting the values back** (since 1.7.2): a `Vault` keeps a local map from numbered placeholders to the values they
replace, so masking can be undone in the model's answer and in tool-call arguments. Nothing in it is sent anywhere.

```python
from newmean_pii import Vault

vault = Vault()  # one per user or conversation, never shared between users
redactor.redact("Call Kim Minsu on 010-1234-5678", vault=vault).text  # 'Call [PERSON_1] on [PHONE_1]'
vault.restore("I called [PERSON_1].")                                  # 'I called Kim Minsu.'

agent = create_agent("openai:gpt-6.1", tools=[...], middleware=[pii_middleware(redactor, vault=vault, restore_tools=["lookup_customer"])])
```

**Placeholders come back only as written.** `[PERSON_1]` is restored; `[Person_1]`, `PERSON_1` or a translated tag stays
in the answer as the model wrote it (never another value). One line in the system prompt asks the model to copy them:

```text
Text in square brackets with a label and a number, such as [PERSON_n], stands for a value hidden from you. Copy it exactly as written, brackets included, wherever you refer to it.
```


The same value always gets the same placeholder in a vault, and a value the vault holds is masked again wherever it
reappears, in the same text or a later one, also where the model would miss it there (source `"vault"`; how it is found:
below). `restorer()` restores a stream, `restore_value` / `hide_value` work on the strings of JSON values (keys and
numbers stay as they are), `entries()` and `Vault(entries)` keep a vault between requests; with the middleware use
`to_json()` and `Vault(state)`, which also carry what the model wrote for the answers that were restored (both hold
the original values). If a vault is cleared while a text is being masked (during `redact`, or while the middleware
prepares a model call), that call raises `RuntimeError` instead of writing into the emptied vault; an answer that
arrives after a clear still gets the values of its own call. Several threads can share a vault: masking, `bindings`,
`restore`, `restore_value` and `hide_value` are each one locked step, and a restorer keeps the values it had when it
was made. Never share a vault between users: a placeholder in one user's answer would bring back another user's value.

With `vault`, `pii_middleware` wraps each model call instead of returning LangChain's `PIIMiddleware`. It then accepts
only `strategy="redact"`, `apply_to_input=True` and `apply_to_output=False` (anything else raises `ValueError`), masks
tool results whatever `apply_to_tool_results` says, and uses `labels` only to choose which of the model's spans are
kept (known values of every label are still hidden).

- The model gets masked copies of user messages, earlier answers and their tool-call arguments, and tool results. The
  answer's text comes back with the placeholders of that call restored, with the values they had when the call was
  masked. They include the placeholders of an earlier answer that the app passes back with its values (and of reasoning
  with `restore_reasoning=True`, and tool arguments with `restore_tools`): the middleware sends it as the model wrote
  it, after a reload from `to_json()` too. Placeholders left unrestored in what the app got back do not count.
- Placeholder-like text already in a message is sent as "［PERSON_1]" (full-width bracket) and never restored. In the
  developer's own text (a `SystemMessage`, `system_prompt`, and the fields outside the messages below: tool
  descriptions, schemas, the response format, `model_settings`) it stays as written, so a prompt can explain
  placeholders; the model then cannot tell it from a value's placeholder, so write examples that are not placeholders
  (`[PERSON_n]`). Percent and HTML forms (`%5BPERSON_1%5D`,
  `&#91;PERSON_1]`) are sent as written, and a placeholder the model percent-encodes is not restored.
- Tool-call arguments are restored only for the tools in `restore_tools` (a name, names or a test on the name), and the
  next call sends the arguments as the model wrote them. Other tools receive the placeholders, because text the model
  reads can contain instructions that make it put placeholders into a tool call. Never restored: tool input inside
  content blocks, `invalid_tool_calls` and `additional_kwargs` (tools run from `tool_calls`).
- Earlier answers and tool calls go back to the model as the model wrote them (the middleware keeps a record of each,
  and `to_json()` carries it) and are read by the model again, so a value the model itself wrote out is masked too.
  Where there is no record of an answer (a vault reloaded from `entries()`), every known value is masked wherever it
  occurs in it, also inside longer words: more may be masked there than needed, never less.
- User messages and tool results are always read as received. Tool input and results inside content blocks (Anthropic
  `tool_use` and `tool_result`, OpenAI `function_call`) are masked as well: JSON text value by value, rows of JSON data
  in every string value. Only LangChain's and the providers' block types are blocks: in one, the model reads the text,
  tool input and tool results, and the other fields only get the values the vault already holds hidden (the check before
  sending, below). In what a tool returns or a user sends, an object is a block only when it has its type's own content
  (a `text` block its `text`; a `file` or `image` its `url`, base64 data or file id; a `document` its `source`; a `json`
  block its `json`). In what a tool returns, a block's `id` and `name` are checked too (no block there is matched by
  its id); in a user's message, a block's `id` is LangChain's identifier of the block and stays as written. Rows a tool
  returns with a `"type"` of their own (`{"type": "contact", "name": ...}`), and rows typed like a block without its
  content (a storage API's `{"type": "file", "id", "name", "owned_by"}`), are data: the model reads them, and every
  field and key counts.
- A message or tool result that is a JSON document (`json.dumps` output, with its `\u00e9` escapes) is read value by
  value, with the whole document as context for the model, and stays valid JSON; so do the model's own earlier JSON
  answers and tool input, which keep every field when keys become one placeholder. A number that is found (a card or
  phone number written as a JSON number) becomes its placeholder, a string; it is the same number however it is written
  (`8.21023456789e11` and `821023456789`) and comes back in plain decimal form (`12.5` for `1.25e1`; exponents up to
  64). Each value is read next to its key (`name: Kim Minsu`), which tells the model what the value is. The keys
  themselves are kept, except that a value the vault already holds is hidden in a key too (a map keyed by e-mail
  address, or by phone number as an `int` key). Two keys that become the same placeholder stay two fields (`[PERSON_1]`
  and `[PERSON_1] #2`); keys that were the same before masking stay the same.
- **Every data slot is sent as plain JSON, built once (since 1.7.2).** The content of each message, its tool calls'
  arguments, `additional_kwargs`, `model_settings` and the model's own fields that go into the request (below) are first
  built into the JSON a provider client writes, and that JSON is masked and sent: the adapter gets it instead of the
  Python objects. A pydantic model is its dump, made once,
  as the OpenAI and Anthropic clients make it (`model_dump(mode="json", by_alias=True, exclude_unset=True)`: its
  serializers, computed fields, aliases and extra keys; a field left at its default is not written), so a model whose
  second dump would differ cannot send what was not masked. A dataclass is its fields; a tuple, set or frozenset a list;
  a date, datetime or time its ISO form; an `Enum` its value; a `Decimal` a number (its digits as a string where a float
  cannot hold it); a `UUID` or a path its text; bytes their UTF-8 text (bytes stay bytes only as the data of a file block
  or a block's source); `NaN` and the infinities `null`; any other object the form pydantic's JSON mode gives it (a
  `timedelta`). Keys are written as `json.dumps` writes them (`4155550123` as `"4155550123"`, `True` as `"true"`, `None`
  as `"null"`, a `str` or `int` Enum as its value), and two keys that JSON writes the same stay two fields
  (`"4155550123"` and `"4155550123 #2"`). The strings and keys of that JSON are then masked like any other JSON data.
  What JSON cannot write fails the call before anything is sent (below): an object that refers to itself, a serializer
  that raises (a `model_dump` whose read raises too, `AttributeError` included: a pydantic model is told by its class,
  so such a model is not taken for one without a dump), a dataclass field that cannot be read (not set, or a property
  that raises: no field is left out), a key of another type (a tuple, `frozenset`, `bytes`, `Decimal` or plain `Enum`
  key), bytes that are not UTF-8 text outside a file block, and an object with no JSON form. When `json.dumps` cannot write a tool's return value
  (a set, bytes, a date), LangChain writes it with `str()` before the middleware sees it, and its repr escapes are read
  (below).
- Any other text (a log line with JSON in it, a code fence, several JSON lines, a document that was cut off) is read
  whole. The model is given it through its Unicode escapes (`Jos\u00e9` as `José`). The values the vault already holds
  are also searched in the text as written (`u1234` in `C:\Users\u1234`) and in its other readings (below).
- A value found in one message of a call is masked in all of them, also in the messages before it and in the system
  prompt.
- Before a call is sent, every text field of its messages is checked once more for the values the vault holds, with no
  model call, whatever kind of block it is in. Left alone, in LangChain's and the providers' own blocks only:
  identifiers and names, references to files and images (`url`, `image_url`, `file_id`; a known value inside such a
  URL is sent as written), base64 data and signed reasoning. In a block type this package does not know, every other
  field is checked too. In the data inside a block every string and key counts, also an object or list under `data`.
  A `data` string that is a known value as a whole is hidden. A data URL, or a string of 64 characters or more that
  looks like base64, stays as it is unless a known value of 6 or more characters stands in it at word edges, as
  written or in one of its readings. `additional_kwargs` is data, except its `tool_calls` and `function_call`. A
  document given as inline text is read like any other text from outside, and messages of other kinds
  (`ChatMessage`, `FunctionMessage`) are masked like text from outside.
- A known value in the `name` of a `HumanMessage` or `ChatMessage` is sent as its placeholder without brackets
  (`PERSON_1`, a form providers accept there) and is never restored. Providers take only letters, digits, `_` and `-`
  there, so a known value of two or more words is also found joined (`Kim_Minsu`, `kim-minsu`, `KimMinsu` for
  `Kim Minsu`); a one-word value is found as written.
- Reasoning stays masked unless `restore_reasoning=True`. Reasoning the model wrote goes back to it exactly as written
  (providers sign it), also where it holds a value the vault learned later: the model wrote that value, so the provider
  had it. A JSON answer (structured output) gets its values back inside its strings, and so does the agent's
  `structured_response` (a pydantic model, dataclass or dict keeps its type; a pydantic model is built again under the
  keys it validates by, its aliases).
- **A call whose masking cannot finish raises, and nothing of it is sent**: the detector raises, the vault is cleared
  while the call is masked, an input cannot be written as JSON (an object that refers to itself, a serializer that
  raises, a key JSON cannot write, an object with no JSON form) or is very deeply nested, or a tool cannot be written as
  a definition.
- Other middleware that calls a model on its own (`SummarizationMiddleware`, for one) reads the agent's unmasked state:
  this middleware masks the calls that go through it.
- **List it last.** It masks the request it is given, so what a middleware listed after it adds (a `@dynamic_prompt`,
  retrieved context added in a `@wrap_model_call`) reaches the model unmasked: put it at the end of `middleware=[...]`.

`mask_documents(docs, redactor, vault=vault)` reads each `page_content` as the middleware reads a tool result (metadata
is kept); with a vault it accepts only `mode="tag"` and raises `ValueError` for another mode.

**Fields outside the messages.** With a vault, known values are also hidden, with no model call, in the tools
(descriptions, and the descriptions, titles and examples of argument schemas; a hidden tool keeps its name, and the
agent still runs its own tool), in the response format (`ProviderStrategy`'s JSON schema, `ToolStrategy`'s
structured-output tools, `AutoStrategy` and a schema without a `"title"` too: they are hidden where the agent binds them
to the model, the binding the provider gets) and in `model_settings`. Model settings are
data: every string and key in them (`user`, a stored prompt's `variables`, `metadata`) and every number that is a known
value. The exceptions are exact paths, each one where a provider defines an identifier, a credential, a numeric setting
or a JSON schema (since 1.7.2, the Python sibling of release gate 8, item 5: a name such as `previous_response_id` was
kept at any depth inside a field a provider defines). A value at such a path stays as written (the keys along it too);
the same name anywhere else (one level down, below another field, under a setting no provider defines such as
`{"acme": {...}}`, in `extra_body`, in a template variable called `order`) is data, every key and value of it. "`*`" is
any key of an object and "`[]`" any item of a list, only where the value is a list: an object with a key written `"[]"`
is data (`{"mcp_servers": {"[]": {"name": ...}}}`); no path keeps everything below it.

- Identifiers and credentials: `model`, `models[]`, `provider.order[]`, `provider.only[]`, `provider.ignore[]` (a
  gateway's routing, also under `extra_body`, which the OpenAI client merges into the request body),
  `previous_response_id`, `conversation` (or `conversation.id`), `prompt.id`, `prompt.version`, `container` (or
  `container.id` and `container.skills[].type`, `.skill_id`, `.version`), `mcp_servers[].type`, `.name`, `.url`,
  `.authorization_token`, `.tool_configuration.allowed_tools[]`, `cachedContent`, `cached_content`, `tools[].type`,
  `tools[].name`, `tools[].function.name`, `functions[].name`, `response_format.type`,
  `response_format.json_schema.name`, `text.format.type`, `text.format.name`, `tool_choice` (a name or a type, or its
  `type`, `mode`, `name`, `function.name`, `server_label` and `tools[].type`, `.name`, `.function.name`,
  `.server_label`), `function_call` (or `function_call.name`), the built-in tools' `tools[].server_label`, `.server_url`,
  `.connector_id`, `.authorization`, `.headers.*`, `.allowed_tools[]`, `.allowed_tools.tool_names[]`,
  `.require_approval` (or its `always.tool_names[]` and `never.tool_names[]`), `.vector_store_ids[]`, `.container`,
  `.container.file_ids[]`, `.filters.allowed_domains[]`, `.filters.blocked_domains[]`, `.allowed_domains[]`,
  `.blocked_domains[]`, `logit_bias.*`, `logitBias.*`, `headers.*` and `extra_headers.*`; in a system prompt,
  instructions and input, the types `system[].type`, `system[].cache_control.type`, `input[].type`,
  `input[].content[].type` and `input[].summary[].type` and reasoning sent back encrypted, `input[].encrypted_content`
  (the same under `instructions`).
- Numbers that stay numbers: `maxTokens`, `max_tokens`, `maxOutputTokens`, `max_output_tokens`, `maxCompletionTokens`,
  `max_completion_tokens`, `max_tokens_to_sample`, `temperature`, `topP`, `top_p`, `topK`, `top_k`, `minP`, `min_p`,
  `seed`, `frequencyPenalty`, `frequency_penalty`, `presencePenalty`, `presence_penalty`, `repetitionPenalty`,
  `repetition_penalty`, `n`, `candidateCount`, `candidate_count`, `logprobs`, `topLogprobs`, `top_logprobs`,
  `budgetTokens`, `budget_tokens`, `thinkingBudget`, `thinking_budget`, `maxToolCalls`, `max_tool_calls`, `maxRetries`,
  `max_retries`, `timeout`, `dimensions`, `numCtx`, `num_ctx`, `numPredict` and `num_predict` at the top of the
  settings and directly under `generationConfig`, `generation_config`, `inferenceConfig`, `inference_config`,
  `additionalModelRequestFields`, `additional_model_request_fields` and `options`; and `thinking.budget_tokens`,
  `thinking.budgetTokens`, `thinkingConfig.thinkingBudget`, `thinking_config.thinking_budget`,
  `generationConfig.thinkingConfig.thinkingBudget`, `generation_config.thinking_config.thinking_budget` and
  `thinking.budget_tokens` under `additionalModelRequestFields` and `additional_model_request_fields`. A string there is
  read.
- JSON schemas, read as a tool's schema is (descriptions, titles and examples hidden; property names and `enum`,
  `const`, `default` and `pattern` as written): `response_format.json_schema.schema`, `text.format.schema`,
  `tools[].parameters`, `tools[].input_schema`, `tools[].function.parameters`, `functions[].parameters`,
  `responseSchema`, `response_schema`, `responseJsonSchema`, `response_json_schema`, `output_format.schema`,
  `outputFormat.schema`, `output_config.format.schema`, `outputConfig.format.schema`,
  `generationConfig.responseSchema`, `generationConfig.responseJsonSchema`, `generation_config.response_schema` and
  `generation_config.response_json_schema`.

Settings are built into JSON first, as the messages are. In a field a provider defines, an object with no JSON form (a
schema's class, an `httpx.Timeout`) stays as it is; in a setting no provider defines, in `extra_body`, in a system
prompt, instructions or input, and in the application's maps (`metadata`, `labels`, `tags`, `variables`) wherever they
stand, it fails the call. Tool names and schema property names stay as written. New values are not looked for in these fields: keep personal information out of them. When the messages of a call hold placeholders, the schema of the
response format goes to the model without `format` and `pattern` (a `datetime.date` field or a pattern cannot hold
`[DATE_OF_BIRTH_1]`), and the agent parses and checks the answer with this call's values back. A model that cannot be
copied for this (one that is not a pydantic chat model) fails the call when the response format holds a known value.
The model's own fields that go into the request are data too (since 1.7.2), whatever their key: `model_kwargs`
(`user`, `safety_identifier`, `prompt_cache_key`, a stored prompt's `variables`, and a `system`, `messages`,
`instructions` or `tools` given there: `langchain-anthropic` sends those instead of what it builds from the messages),
`extra_body` (where OpenAI's `metadata` goes: LangChain keeps a `metadata` given in `model_kwargs` as the run's metadata,
which is not sent; the OpenAI and Anthropic clients merge `extra_body` into the body, a `messages` or `system` in it too)
and fields of the model's own such as Anthropic's `user_profile_id`, read where the provider package builds its request
(`_get_request_payload`, else `_default_params`, else `model_kwargs` and `extra_body` themselves), with the rules and
identifier exemptions of model settings, at the same paths. A system prompt, instructions or input there is text: its
strings and keys count, only the types and the encrypted reasoning at the paths above stay, and an object with no JSON
form in it fails the call. Each field is built into JSON once, as model settings are, and that one build is masked. When a field holds a
known value, or an object whose JSON its own code writes (a pydantic model, whose serializer could give another JSON on
a second dump; a dataclass), the agent gets a copy of the model for that call (`model_copy`, which shares the model's
clients) that holds the masked JSON, so the client writes what was masked; your model is not changed. Other fields stay
as the model holds them (the client writes a date or a tuple the same way each time, or refuses it as it does without
the middleware). The copy's request is checked before the call: a field it cannot send hidden fails the call before
anything is sent, with an error that names the field (also a response format given as a pydantic class whose JSON schema
holds a known value, and a field the copy cannot trace that holds such an object), and so does a model that is not a
pydantic chat model, or one `init_chat_model` makes on each call (configurable fields), when what it sends (its
configuration) holds a known value or such an object.

**How a known value is found again.** In all six packages, for `redact` with a vault, `hide`, `hide_value` and the
middleware (shared cases: `python/tests/vault_cases.json`):

- 3 characters or more; 2 when one of them is in a script written without spaces (Han to Extension J, kana from U+1B000
  too, Thai, Lao, Khmer, Myanmar, Tai Tham, Balinese, Javanese) or both are Hangul syllables (`이준`). A shorter value the
  application tags is masked where it is a whole JSON value, key or tool argument, where `hide_value` meets it as a whole
  string, and in model text without a record; one the model found is masked where it was found and in model text
  without a record, and given back in answers.
- At word edges: not inside a longer word (letters, digits, marks and `_` make words). A letter of a script written
  without spaces on either side is an edge (`注文ZX-88213-Kは`), but not a vowel sign or another mark after the value:
  it belongs to the letter before it (`สม` is not found in `สมัคร`). Korean particles may follow a Hangul value
  (`김민수님께`), Hangul right before it is not an edge (`이김민수`), and Hangul next to Latin letters or digits is
  one. After a value of two Hangul syllables, only particles, the honorifics 님 and 씨 or the copula may follow in the
  same word (`지원님께` is the name, `지원금` another word). Letters glued to a name make another word: an Arabic or
  Hebrew prefix (`ومحمد`), a case ending or a suffix (Turkish, Finnish, German `-s`).
- Letter case: an e-mail address, a user name and a value of two or more words match in any ASCII case, and a URL's
  scheme and host in any case (its path as written). Any value matches in capitals: its full Unicode uppercase (`JOSÉ`
  for `José`, `WEISS` for `Weiß`), and as Turkish and Azerbaijani (`EMİNE` for `Emine`), Greek (without accents and
  breathings: `ΝΙΚΟΣ` for `Νίκος`) and German (`WEIẞ` for `Weiß`) write capitals. A one-word value in another case is
  another word (`Will` and `will`).
- A value found takes in the combining marks, variation selectors and joiners right after it, so its span never ends
  inside a grapheme cluster.
- NFC and NFD are the same (`José` either way, Hangul written as conjoining jamo; the four native packages compose
  Hangul only). Full-width ASCII (`ｋｉｍ＠example.com`) and the ideographic space U+3000 are read as ASCII.
  Mathematical and look-alike letters, and digits of other scripts, are another value.
- A dot between digits joins them: a value that starts or ends with a digit is not found where a dot joins it to more
  digits (`4524` in `4524.7` or `1.4524`, also `12 Main St` in `Unit 4.12 Main St`). An exponent joins only numbers:
  `4524` is not found in `1e+4524`, while an address, a phone number or a postal code after `4E+` is found
  (`Unit+4E+12+Main+St`). In the middleware the same holds when the sign or dot is percent- or Unicode-escaped
  (`1e%2B4524`, `4524%2E7`).
- A value found in another spelling keeps the known value's placeholder: one value, one placeholder. Where two known
  values cover the same part of a text, the one the vault has held longer wins.
- What the model marks is learned without the white space, punctuation, brackets and quotation marks at its edges that
  are not part of the value (`Kim Minsu.`, `"Kim Minsu"` and `(Kim Minsu` are learned as `Kim Minsu`, and those
  characters stay in the text), unless what is left is too short to be found (then only the white space goes). A
  bracket whose partner is in the value stays (`(02) 1234-5678`), and so do a `.` at the start of a value and the `.`
  at the end of an ORG (`Tiffany & Co.`); a SECRET loses only its white space.

The middleware and `mask_documents` also find:

- White space written another way. A space matches any white space (tab, NBSP, U+3000, several spaces). A line break
  also matches a comma and a space, or an indented line. A comma and a space also match a line break, or a plain space
  (`12 Main St Seoul`). Other punctuation (`,` with no space, `;`) makes another value, and so do white space at the
  start or end of a value, a zero-width space, joiner or soft hyphen inside it, and a space put into a value that has
  none (`김 민수`). `\v` and `\f` are white space, not line breaks.
- A number in another spelling, compared digit for digit (exponents up to 64): `8.21023456789e11` for `821023456789`,
  trailing zeros, `E`. A zero in front (`04524`) makes a string of digits, found as written only. Digit groups
  (`4,524`, `4 524`, `4_524`) and other punctuation in a phone number (`01012345678` for `010-1234-5678`) are another
  value. A sign stays outside the placeholder: for a known `4524`, `-4524` and `-4.524e3` become `-[ID_NUMBER_1]`, and
  the JSON number `-4524` becomes the string `"-[ID_NUMBER_1]"`.
- A known ISO date (`1990-01-02`) at the start of an ISO timestamp (`1990-01-02T00:00:00`, as `isoformat()` writes
  it) and in a date repr (`datetime.date(1990, 1, 2)`, which LangChain writes when a tool returns a `date`). Other
  orders and separators (`01/02/1990`, `2 Jan 1990`) are another value.
- Other readings of the text: through JSON escapes (a line break as `\n`, `CORP\\kim` for `CORP\kim`,
  `01\/02\/1990`, also JSON inside a JSON string), percent escapes as UTF-8 (`kim%40example.com`), `+` as a space
  and as itself (`name=Kim+Minsu`), and Python repr escapes (`Kim\xa0Minsu`, `b'Jos\xc3\xa9'` as UTF-8,
  `\U00020bb7`). These may stand inside one another up to four steps deep (JSON in a form field, a form field in
  JSON). Every reading within four steps is searched, however many escapes the text holds elsewhere, and the readings
  are searched again until the vault learns nothing new.
- Read as written, so a value in such a form is masked only where the model finds it: HTML entities and tags
  (`&#233;`, `<br>`), base64, quoted-printable and MIME words, doubled quotes (SQL, CSV), Markdown and shell
  backslashes, `%uXXXX`, punycode host names (`xn--`), other charsets (EUC-KR) and bytes that are not UTF-8, and a
  fifth step of encoding.

**Limits.**

- One middleware, one vault: a compiled agent built once and used for many conversations (many `thread_id`s) shares
  its vault, so build one per conversation. The vault is not part of LangGraph's checkpointer: keep `to_json()` next to
  the conversation yourself.
- The vault has no size limit. The middleware keeps a record of the 4,096 answers and tool calls used last (older ones
  are then searched loosely: more masking, never less) and the model's spans for the last 256 texts. Tokens streamed
  during a model call show the placeholders; the message the agent keeps has the values.
- Spans are kept per vault generation and detector settings (a result is kept only under the settings it ran with),
  and the vault is applied again on every call, so a value learned later also covers text masked before. A value
  learned from a text is masked wherever it occurs in that text on the first call, and what the model finds inside a
  known value or a larger merged span is learned too. What the model finds is learned without what its span took from around the value:
  white space, a sentence's period or comma, brackets and quotation marks (`Kim Minsu` from `I'm Kim Minsu.`), the `b'`
  of a Python bytes literal, and the time of a date of birth's ISO timestamp.
- With a vault, the redactor needs `detect()`; one without it fails with a clear message. A `Redactor` subclass with
  its own `detect` is used as it is.
- Allow and block lists work with a vault: the model and the lists read the text as written, and a restored answer
  that is sent again is read by both again.
- What the middleware gives back (tool arguments, an exported state) is a copy: changing it does not change the vault.
- `apply_redaction` and `redact_table` with a vault only number the spans they are given; they do not search for
  known values.
- Not masked, because they are not sent to providers: `response_metadata`, `usage_metadata` and a `ToolMessage`'s
  `artifact`.
- Placeholders are `[<FAMILY>_<n>]`: a family of up to 32 characters (`_` for each character that is not allowed) and
  1 to 12 ASCII digits; longer look-alikes are ordinary text. PERSON_L2 shares PERSON's numbers. Numbers the model
  makes up are skipped while they are within a million of the numbers in use, and `to_json()` keeps them. A family
  that has used its numbers (up to 999,999,999,999) goes on as `[PERSON__1]`, so a state the vault gave out always
  loads. An empty value gets no placeholder.
- The vault's word characters come from Python's Unicode data: 16.0 in Python 3.14, 13.0 in Python 3.9, the oldest
  the package accepts. A newer character next to a value can be read differently in an older Python.
- Run for this release: `create_agent` with `invoke` and `ainvoke` on langchain 1.5.2 (langchain-core 1.8.4,
  langchain-openai 1.8.5, langchain-anthropic 1.7.12) and on 1.4.0 (langchain-core 1.6.0, langchain-openai 1.6.0,
  langchain-anthropic 1.7.0): the packaged tests, the structured-output check, the shared corpus and the request bodies
  of the OpenAI and Anthropic adapters. The extra asks for langchain 1.4.0 or later (1.6 took 1.0 or later). Earlier
  releases are not supported: the packaged tests do not run on 1.0.0, and this release was not checked on the releases
  between 1.0 and 1.4.
- **Time.** The vault's work grows with the length of the text times the number of known values. Large data with
  thousands of known values is slower. With 2,000 known values, 2,000 JSON rows (181 KB) took 1.8 s per call, 4,000
  rows (362 KB) 3.5 to 3.8 s, 1 MB of prose 6.9 to 7.0 s, and 512 KB of nested encodings (percent and form encodings,
  Unicode escapes, JSON in JSON) 13.8 to 14.2 s (vault work only, no model; the median of three calls, in each of two
  runs, on an Apple M5 Pro with Python 3.14). A faster search, under 2 s per call for 1 MB with 2,000 known values in
  every shape, is planned for 1.7.3.

**LiteLLM proxy**: a guardrail that needs no second service. In `config.yaml`:

```yaml
guardrails:
  - guardrail_name: "newmean-pii"
    litellm_params:
      guardrail: newmean_pii.litellm.NewmeanPIIGuardrail
      mode: "pre_call"      # mask the request before it is sent; "post_call" masks the response
      size: "medium"        # optional; also variant, labels, sensitivity, redact_mode, model_dir, allow, block, ...
```

Requests opt in with `"guardrails": ["newmean-pii"]`, or set `default_on: true`. The text is masked as `redact` does
(`[PERSON]`, `[PHONE]`; `redact_mode: "mask"` or `"blank"` for the other modes). Masked values are not put back into the
response, and the miss rates above apply: this lowers what a provider sees, it does not guarantee that nothing gets through.

**Microsoft Presidio**: a recognizer for `presidio-analyzer`, alone or beside Presidio's own recognizers.

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from newmean_pii.presidio import NewmeanPIIRecognizer

analyzer = AnalyzerEngine()
analyzer.registry.add_recognizer(NewmeanPIIRecognizer(size="medium"))
text = "Call Kim Minsu on 010-1234-5678"
AnonymizerEngine().anonymize(text=text, analyzer_results=analyzer.analyze(text, language="en")).text
# 'Call <PERSON> on <PHONE_NUMBER>'
```

Labels are reported under Presidio's entity names where it has one (`EMAIL` → `EMAIL_ADDRESS`, `PHONE` → `PHONE_NUMBER`,
`ADDRESS` → `LOCATION`, `ORG` → `ORGANIZATION`; the table is `ENTITY_MAPPING`, changed with `entity_mapping={...}`).
Every result has the same `score` (default 0.85): the model returns spans, not scores, so move its threshold with
`sensitivity`. A Presidio recognizer serves one language: `recognizers(["en", "ko", "ja"], size="medium")` returns one
per language on a single model. Presidio itself still needs an NLP engine for each language you analyze.

**Langfuse**: a `mask` function. Langfuse passes it the input, output and metadata of each observation, which are then
stored without the personal information the model finds.

```python
from langfuse import Langfuse
from newmean_pii.langfuse import pii_mask

langfuse = Langfuse(mask=pii_mask(size="medium"))
```

`pii_mask(redactor=None, *, mode="tag", keys=True, ids=True, **create_redactor_options)` masks every string in an
observation's input, output and metadata, at any depth: the keys and values of dicts (any mapping), the items of lists,
tuples, sets, deques and dict views, bytes (as UTF-8), the value of an Enum member, the fields of dataclasses, and what
the `model_dump` (pydantic v2) or `dict` (pydantic v1) method that the class of any other object declares returns. A
value is taken for what its own class is (`type(value)`), not for the class its `__class__` names, and those methods
and fields are looked for in the class and its bases without running any code of the value: an object that only
forwards them through `__getattr__` (a proxy) is "any other value" (below). A key that masks to a key already there gets
` #2`, ` #3`, ... so no value is lost. The ids of a trace and the keys that are field names are kept as written,
because the model reads most of them as personal values:

- **Ids that providers and frameworks write**, wherever they stand (a value, a list item or a key): OpenAI chat
  completion, tool call, Responses and fingerprint ids (`chatcmpl-…`, `call_…`, `resp_…`, `msg_…`, `fc_…`, `fp_…`),
  Anthropic message, tool use and request ids (`msg_01…`, `toolu_01…`, `req_…`), Bedrock tool use ids (`tooluse_…`) and
  LangChain run ids (`lc_run--<uuid>-0`).
- **UUIDs** (`8-4-4-4-12` hex digits, any case) and LangGraph checkpoint namespaces (`model:<uuid>`), where the trace
  names an id: as the value of an id key (`id`, `run_id`, `parent_run_id`, `session_id`, `thread_id`, `trace_id`,
  `message_id`, `tool_call_id`, `call_id`, `request_id`, `response_id`, `checkpoint_ns` and the others listed in the
  module, `_ID_KEYS`), or as an item of a list, tuple, set, deque or dict view there (lists in lists too); bytes, an
  Enum member or an object's dump there counts as the value it holds. A key is never such a place: a UUID used as a key
  is read by the model, under an id key too (`{"run_id": {"<uuid>": "ok"}}`). So is a UUID anywhere else (under any
  other key, in a dict under an id key, or alone), because it can be a person's identifier (an advertising id, a
  payment key, a customer's id under `user_id`).
- **Keys that are field names**: a `str` key (a subclass too) of at most 64 characters in snake_case or camelCase of
  ASCII letters, two words or more (`user_id`, `user_name`, `user_email`, `USER_ID`, `userName`; each word after the
  first in camelCase is a capital and small letters, so `userID` is read), or an id key. Every other key is read like
  any other text (a map keyed by e-mail address or name): one word, digits, dots and hyphens are how user names are
  written too (`minsu92`, `jukka.nieminen`), and a longer key can carry a name in it (`userJohnSmith…`). A key that is
  not a `str` (bytes, a number, a tuple, a `UserString`) is read as a value is.

A key is an id key when it has at most 64 characters and is one of `_ID_KEYS` once case, `_`, `-`, `.` and spaces are
left out and a final `ids` is read as `id` (`runId`, `run-ids`, `Run ID`, `run.id`). An id is kept up to 1,024
characters. An id or a field name is kept only when nothing is found in it: the pattern layer reads it (but not the
random hex of a UUID or a hex id, where it finds phone and card numbers by chance) and your `block` list reads it and
each UUID in it; otherwise the model reads it like any other text. On our development set (measured for 1.7.2),
the field names masked fell from about 1 in 75 of 1,030 to the four that are also place names (`Georgia`, `Paris`, ...;
nano also `antall_forsøk`), personal values used as keys are still masked 96.3% of the time (96.4% when every key was
read), and 200 random ids of each shape above are all kept. `ids=False` reads ids as text, and `keys=False` keeps every
key as it is. With this package's own `Redactor`, the results of the most recent 4,096 keys are remembered for the
detector's settings, so the same keys in the next observation are not read again; any other redactor reads every key
every time.

Python objects of these types are kept: numbers, booleans, dates, times and durations, `uuid.UUID`, `None` and
Langfuse's media objects (the reference to a file Langfuse uploads, below). Read on its own, a long number looks the
same whether it is a phone number or a timestamp, so store numbers you need masked as text. A number, date or time
written as a string, and an id in another shape, is text, which the model reads: it masks some timestamps. Any other
value, and bytes that are not UTF-8, become `[UNMASKED: <type>]`, so nothing it cannot read is sent. What the function
returns is made of Python's own types only (dict, list, tuple, set, frozenset, str, bytes, bytearray and the types
kept above; with `keys=False`, keys as they are), never of your classes, because Langfuse writes an object through its
class's code (`str()`, `isoformat()`, the attributes an instance holds): a named tuple becomes a tuple, a subclass of
`str` or bytes a `str` or bytes, and a subclass of a kept type that type itself (a `uuid.UUID` subclass a `uuid.UUID`
with the same number, a `datetime` subclass a `datetime` with the same date, time and `tzinfo`), without running the
subclass's code. Langfuse's media objects are its own classes, not a class with the same name. When masking fails (the model cannot load, for example
offline with nothing cached; the detector raises; a container or an object's fields cannot be read, such as a
pydantic serializer that raises, a `model_dump` property whose read raises, `AttributeError` too, a dataclass field
that is not set, or a mapping or list whose items cannot be read: a read that fails is never taken for a method or a
field that is not there), the whole value becomes `[MASK FAILED]`, its other parts too, and the error's type is
logged at level ERROR to the `newmean_pii.langfuse` logger: the function never raises. The options are checked when
`pii_mask` is called (a
misspelled size raises there); the model is loaded on the first call. It runs on every observation in your process, so
pick the size with your trace volume in mind.

Langfuse sends the rest of a trace without passing it to `mask`: names, model parameters, status messages, exception
events and stack traces (an error message that quotes an e-mail address is sent as it is), trace attributes (user and
session ids, tags, metadata set with `propagate_attributes`, which is where Langfuse's LangChain callback handler puts
the `metadata` of the run you invoke) and the spans of other OpenTelemetry instrumentations it
exports (OpenInference, OpenLLMetry, ...). It uploads files before `mask` runs: a base64 data URI, a base64 file block
(Anthropic, Vertex, Gemini) or a `LangfuseMedia` object in an observation goes to Langfuse as it is, and the trace holds
a reference to it. To keep files out, leave them out of what you trace, or set `LANGFUSE_MEDIA_UPLOAD_ENABLED=false`:
then nothing is uploaded and the base64 text reaches `mask`, where the model reads it as written (it does not decode
base64).

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

`fetch_model()` and `python -m newmean_pii download` fail when a file cannot be written to the cache (a read-only or
full disk, or a `--cache-dir` that names a file): `fetch_model()` raises `ModelFileError`, and the command prints nothing
on stdout, names the file on stderr and ends with status 1. So a build step never reports a folder that cannot be opened
offline later. Loading for immediate use (`create_redactor`) still works from the downloaded and verified bytes when the
cache cannot be written, and a complete cache is only read. Since 1.7.2; earlier versions printed the folder and ended
with status 0.

There is also a small command line: `python -m newmean_pii redact --size nano --mode mask < input.txt`.

## Development

```bash
cd python
uv build                      # sdist (with native/core copied in) and a wheel built from it
pip install dist/*.whl pytest
pytest -q                     # fixture parity tests skip without fixtures and model copies
```

The C extension compiles `native/core` (the shared core of every native package) with the same flags as its Makefile,
so logits are bit-identical to the npm package's WebAssembly engine (its relaxed-SIMD build on arm64, where the kernel
fuses multiply and add since 1.5.0; its baseline build elsewhere). Tests: regex parity with the Python
baseline, redaction modes, offsets on emoji text, threads, downloads against a local HTTP server and `file://` (no
network), and, when the private fixtures and model copies exist (`PII_FIXTURES`, default `js/test/fixtures`;
`PII_MODELS`, default `js/test/models`), tokenizer parity with Hugging Face and span parity with the Python evaluator for
every run of the npm test (`PII_SIZES=nano,medium` limits them). `NEWMEAN_PII_NETWORK_TESTS=1` adds one download from
Hugging Face. Wheels: `cibuildwheel` (configured in `pyproject.toml`, workflow `.github/workflows/python-wheels.yml`).

## License

[Apache-2.0](LICENSE), for the package code and the model weights; third-party notices in [NOTICE](NOTICE). If you use
them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a
request, not a license condition). Made by [newmean.ai](https://newmean.ai).
