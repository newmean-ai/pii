## 1.3.0

- Models `newmean-pii-<size>` v1.1.0, as in 1.2.0.
- `wholeWords` (default false) in `create` / `fromBytes`: a span that starts or ends inside a word is widened to the
  whole word (「경기도」 → `[PERSON]`, not `[PERSON]기도`; "R23" → `[ID_NUMBER]`). Words are runs of letters, digits and
  marks joined by apostrophes and hyphens; scripts written without spaces never extend a span. Same rule in every package.
- `info` (`PiiRedactorInfo`): package and model versions, size, variant and the two model files with their size and
  SHA-256. `version` stays the model release.
- `strictShortNames` (default false) in `create` / `fromBytes`: on a text the model reads as at most 8 tokens (a survey
  answer, a table cell), the model needs stronger evidence to read it as a person's name. Short values such as 「경기」,
  「남성」, 「네이버」 are left alone (large: PII-free short survey items masked 7.9% → 4.3%); a lone name is missed a little
  more often (98.9% → 96.5% found). Longer texts are unchanged.

## 1.2.0

- Models `newmean-pii-<size>` v1.1.0, as in 1.1.0 below: 1.2.0 is the first published update after 1.0.0, so it carries
  that model change. Same sizes, same tokenizer.
- Emoji are taken out of the text the model reads, so they are no longer masked as names (texts with a masked emoji,
  large 23.95% → 0.87%). Spans keep the input's offsets; an emoji inside a span splits it. `keepEmoji: true` in `create` /
  `fromBytes` gives the model the text as given (1.1 behaviour). Text without emoji or keycaps gives exactly the spans
  of 1.1.
- A keycap (0️⃣–9️⃣, #️⃣, *️⃣) is read as its bare character, so numbers written with keycaps are still found; a span over
  that character covers the whole keycap.
- Every span is widened to whole grapheme clusters: an emoji, flag or combining sequence is masked whole or kept whole.
- Table mode: `detectTable` / `redactTable` (and `detectTableSync` / `redactTableSync`) for CSV, TSV, semicolon-separated
  and markdown pipe tables, with `delimiter` (`'auto'`, `','`, `'\t'`, `';'`, `'markdown'`), `header` and `vote`. Spans
  stay inside cell values, so the redacted text is still a valid table; a column the model reads as names, emails, phone
  numbers or usernames in at least 80% of its cells is masked whole (source `"table"`).
- `context: 'header'` (default `'none'`): with a header row, each cell of a column of mostly distinct values is also read
  on its own as "<header>: <value>". Far fewer misses on name, contact and ID columns (survey exports, large 6.17% →
  1.44%), more PII-free cells masked (9.59% → 12.30%), one more model call per such cell.

## 1.1.0 (not published separately)

- Models `newmean-pii-<size>` v1.1.0: short texts, table cells and survey exports. Lone everyday words (column titles,
  answer options such as "매우 그렇다") are read as names far less often; same sizes, same tokenizer, new pins.
- For survey CSV files checked cell by cell, use `large` or `sensitivity: -0.5`: `medium` can still flag a lone gender
  word ("남성", "Male").

## 1.0.0

- First release: `NewmeanPii.create` / `fromBytes`, `detect`, `redact` (tag, mask, blank, custom), `detectSync`,
  `redactSync`, `sensitivity`, `labels`, `regex`, with the defaults of the npm package `@newmean-ai/pii` 1.0.1 (medium,
  q4e for large and medium, q4 for small and nano, regex off).
- Models `newmean-pii-<size>` v1.0.0 (`newmean-ai/pii-<size>` on Hugging Face): downloaded once and cached, or read from a
  folder or Flutter assets; every file checked against its pinned size and SHA-256.
- Inference in the shared native core (`native/core`), compiled by the build hook for Android, iOS, macOS, Linux and
  Windows; runs in a background isolate.
