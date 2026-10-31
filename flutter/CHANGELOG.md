## 1.0.0

- First release: `NewmeanPii.create` / `fromBytes`, `detect`, `redact` (tag, mask, blank, custom), `detectSync`,
  `redactSync`, `sensitivity`, `labels`, `regex`, with the defaults of the npm package `@newmean-ai/pii` 1.0.1 (medium,
  q4e for large and medium, q4 for small and nano, regex off).
- Models `newmean-pii-<size>` v1.0.0 (`newmean-ai/pii-<size>` on Hugging Face): downloaded once and cached, or read from a
  folder or Flutter assets; every file checked against its pinned size and SHA-256.
- Inference in the shared native core (`native/core`), compiled by the build hook for Android, iOS, macOS, Linux and
  Windows; runs in a background isolate.
