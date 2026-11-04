## 1.4.1

- Models `newmean-pii-<size>` v1.1.0, as in 1.4.0. Bug and doc fixes only: every valid input gives the 1.4.0 spans.
- `sensitivity` must be finite: NaN or ±infinity in `create`, `fromBytes`, the `sensitivity` setter or `setSensitivity`
  throws `ArgumentError` before any model file is read, and the setter keeps the previous value. 1.4.0 accepted them:
  NaN silently switched the model off, and ±infinity masked everything or nothing. The native core also refuses them now.
- `create` and `fromBytes` check the bindings against the native core first, before any file is read, as the 1.4.0
  notes said; 1.4.0 checked only when detecting.
- The model download sends the package version in its User-Agent (`newmean_pii-dart/1.4.1`; 1.4.0 sent the model
  version).
- The native span array is freed even when reading a span fails.
- Docs: names in `labels` that are not labels are ignored, as in the npm package (the Python and Kotlin packages raise an
  error).

## 1.4.0

- Models `newmean-pii-<size>` v1.1.0, as in 1.3.0.
- User lists in `create` / `fromBytes`: `allow` (`PiiAllowEntry(text, {label})`: values you know are not PII, such as
  survey regions, brands or jobs; a span whose whole text is an entry is not masked, with a label only for that label
  family) and `block` (`PiiBlockEntry(text, label)`: values you know are PII; every occurrence on word boundaries is
  masked with the entry's label, also where the model misses it and whatever `labels` enables, and a Hangul entry also
  before Korean particles: 「박지훈님께」 → `[PERSON]님께`). ASCII letters compare case-insensitively, nothing else is
  normalized (pass NFC text). At most 10,000 entries of 1 to 64 code points per list.
- `listsId`: your name for the lists (1 to 128 code points), shown in `info.lists.id`; `info.lists` (`PiiListsInfo`) also
  holds the number of entries of each list after merging duplicates (never their text).
- The lists are checked before any model file is read: `PiiListException` with `list` (`"allow"`, `"block"` or `"meta"`
  for `listsId`), `index` and `reason` (`too_long`, `bad_text`, `empty`, `edge_space`, `unknown_label`, `too_many`,
  `label_conflict`, `in_both`, `bad_lists_id`), the same as in every package; never the entry's text.
- Span source `"block"` for a block entry (alone, never combined with another source).
- The native core's option structs must come from the same release (`struct_size` must equal the library's size; earlier
  releases accepted a prefix). The package checks its bindings against the core at load and refuses to run on a
  mismatch.
- An allocation failure in the native core that would change the result (word widening, block matching) is now an error;
  1.3.0 skipped the word widening and masked less.
- The word table of `wholeWords` (letters, digits and marks; Korean particles), also used for block-list word
  boundaries, is pinned to Unicode 17.0 and generated once for every package (same data as 1.3.0 here; the npm package
  used its runtime's Unicode data before).

## 1.3.0

- Models `newmean-pii-<size>` v1.1.0, as in 1.2.0.
- `wholeWords` (default true; `false` gives the 1.2 spans) in `create` / `fromBytes`: a span that starts or ends inside a word is widened to the
  whole word ("R23" → `[ID_NUMBER]`, not `[ID_NUMBER]23`); a word ends where Hangul meets digits or Latin letters
  (「010-1234-5678입니다」 → `[PHONE]입니다`), and Korean particles after a Hangul span are left as they are (「홍길동님께」 → `[PERSON]님께`). Words are runs of letters, digits and
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
