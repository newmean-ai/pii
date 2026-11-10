/* newmean-pii native core: multilingual PII detection with the newmean-pii-<size> models, on device.
 *
 * One C library shared by the Python, Android, iOS and Flutter packages. It reads the published model files
 * (onnx/model_<variant>.onnx and tokenizer.web.json from https://huggingface.co/newmean-ai/pii-<size>), tokenizes,
 * runs the model and decodes spans like detect() of the npm package @newmean-ai/pii with regex: false: the same token
 * ids and token offsets, and logits bit-identical to its wasm engine (see pii_build_info). The pattern (regex) layer
 * and the redaction modes are left to each language; their spans can be merged here (pii_options.extra) so that every
 * package shares one combine step.
 *
 * Threads: a pii_model is immutable after pii_model_load; any number of threads may call pii_detect,
 * pii_detect_table and pii_tokenize on the same model at once (each call uses its own scratch memory). Nothing is global.
 *
 * Offsets: every span and token carries [start, end) in UTF-8 bytes of the input, in code points (Python str), and in
 * UTF-16 code units (JavaScript, Java/Kotlin, Dart, NSString), so wrappers need no conversion. Invalid UTF-8 bytes count
 * as one code point (U+FFFD) each; surrogates in 3-byte form (Python "surrogatepass") count as one code point. Span
 * offsets fall on extended grapheme cluster boundaries (UAX #29) of the whole input, so a span never splits an emoji ZWJ
 * sequence, flag, skin tone, keycap or combining sequence; token offsets fall on code point boundaries only. Pass
 * standard UTF-8: from Java/Kotlin String.getBytes(UTF_8), not JNI GetStringUTFChars (modified UTF-8 splits emoji into
 * surrogates and NUL into two bytes, which changes tokens and offsets); from Python str.encode("utf-8", "surrogatepass").
 *
 * Licence: Apache-2.0 (LICENSE, NOTICE). */
#ifndef NEWMEAN_PII_H
#define NEWMEAN_PII_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32) && defined(PII_BUILD_SHARED)
#define PII_API __declspec(dllexport)
#elif defined(_WIN32) && defined(PII_USE_SHARED)
#define PII_API __declspec(dllimport)
#elif defined(__GNUC__) || defined(__clang__)
#define PII_API __attribute__((visibility("default")))
#else
#define PII_API
#endif

#define PII_VERSION "1.5.0"       /* this library */
#define PII_MODEL_VERSION "1.1.0" /* model release (Hugging Face tag v1.1.0) */
#define PII_DEFAULT_SIZE "medium"

/* Return codes */
#define PII_OK 0
#define PII_ERR_ARG (-1)    /* invalid argument (NULL pointer, extra span out of range, unknown label, non-finite sensitivity) */
#define PII_ERR_NOMEM (-2)  /* out of memory */
#define PII_ERR_MODEL (-3)  /* the model failed (window longer than the model allows) */

/* Labels (index = label id). Level 2 = indirect identifiers: PERSON_L2 (a surname with a title, a nickname, initials)
 * and URL_L2 (public profile or page URLs). */
enum pii_label {
  PII_PERSON = 0,
  PII_PERSON_L2,
  PII_EMAIL,
  PII_PHONE,
  PII_ADDRESS,
  PII_ID_NUMBER,
  PII_FINANCIAL,
  PII_ORG,
  PII_USERNAME,
  PII_URL,
  PII_URL_L2,
  PII_DATE_OF_BIRTH,
  PII_SECRET,
  PII_LABEL_COUNT
};
#define PII_LABEL_BIT(label) (1u << (label))
/* Literals, so that Swift and Dart (ffigen) import them: all labels (default), and all but PERSON_L2 and URL_L2. */
#define PII_LABELS_ALL 0x1fffu
#define PII_LABELS_LEVEL1 0x1bfdu

/* Span sources (bit flags) */
#define PII_SOURCE_MODEL 1
#define PII_SOURCE_REGEX 2 /* from pii_options.extra; both bits when model and pattern found the same text */
#define PII_SOURCE_TABLE 4 /* span propagated down a column by table mode (pii_detect_table; alone, not combined) */
#define PII_SOURCE_BLOCK 8 /* an entry of the block list (pii_options.lists; alone, not combined). Added in 1.4.0 */

/* Offset units of pii_options.extra */
#define PII_UNIT_UTF8 0
#define PII_UNIT_CODEPOINT 1
#define PII_UNIT_UTF16 2

typedef struct pii_model pii_model;

typedef struct {
  size_t byte_start, byte_end; /* UTF-8 bytes of the input */
  size_t cp_start, cp_end;     /* code points */
  size_t u16_start, u16_end;   /* UTF-16 code units */
  int label;                   /* enum pii_label */
  const char *label_name;      /* "PERSON", ... (static; never freed) */
  int source;                  /* PII_SOURCE_* bits */
} pii_span;

typedef struct {
  int32_t id;
  size_t byte_start, byte_end, cp_start, cp_end, u16_start, u16_end;
} pii_token;

/* A span found outside the model (e.g. by a wrapper's regex layer), in the unit pii_options.extra_unit. */
typedef struct {
  size_t start, end;
  int label;
} pii_extra_span;

/* User lists (pii_lists_create, added in 1.4.0). allow: values the caller knows are not PII; a span whose whole text is an
 * allow entry (ASCII letters compared case-insensitively, nothing else normalized) is not masked, when the entry's label
 * family (PERSON covers PERSON_L2, URL covers URL_L2; -1: any label) includes the span's. block: values the caller knows
 * are PII; every occurrence that starts and ends on a word boundary (whole_words rules; a Hangul entry also matches before
 * Korean particles, "박지훈님께") is masked with the entry's label, whatever options->labels enables, also where the model
 * finds nothing. A span that holds a block match is never allowed. Texts and entries must use the same Unicode
 * normalization (NFC). */
typedef struct {
  const char *utf8; /* the entry, UTF-8 (not NUL-terminated) */
  size_t len;       /* bytes */
  int label;        /* enum pii_label; -1 = any label (allow only) */
} pii_term;
typedef struct pii_lists pii_lists; /* immutable after pii_lists_create: one handle may be shared across threads */
#define PII_LIST_ALLOW 0
#define PII_LIST_BLOCK 1
/* Reasons of pii_list_error. Each entry is checked in this order and the first failure is reported: BAD_SHAPE, TOO_LONG
 * (representation), BAD_TEXT, EMPTY, TOO_LONG (code points), EDGE_SPACE, UNKNOWN_LABEL. The first 10,000 entries of a list are
 * checked before TOO_MANY; allow before block; then LABEL_CONFLICT, then IN_BOTH. The numbers below are not that order. */
#define PII_LIST_ERR_BAD_SHAPE 1      /* utf8 NULL, or a block entry without a label (-1) */
#define PII_LIST_ERR_TOO_LONG 2       /* more than 1,024 bytes or 256 UTF-16 units, or (well-formed) more than 64 code points */
#define PII_LIST_ERR_BAD_TEXT 3       /* malformed UTF-8 (strict), U+0000, or a line break (CR, LF, NEL, U+2028, U+2029) */
#define PII_LIST_ERR_EMPTY 4          /* no code points */
#define PII_LIST_ERR_EDGE_SPACE 5     /* first or last code point is White_Space (never trimmed) */
#define PII_LIST_ERR_UNKNOWN_LABEL 6  /* not one of the PII_LABEL_COUNT labels (or -1 in allow) */
#define PII_LIST_ERR_TOO_MANY 7       /* more than 10,000 entries in one list (index 10,000) */
#define PII_LIST_ERR_LABEL_CONFLICT 8 /* two block entries with the same form and different labels (index of the later one) */
#define PII_LIST_ERR_IN_BOTH 9        /* the same form in allow and block (index in block) */
typedef struct {
  int list;     /* PII_LIST_ALLOW or PII_LIST_BLOCK */
  size_t index; /* entry index in that list */
  int reason;   /* PII_LIST_ERR_* */
} pii_list_error;

typedef struct {
  /* sizeof(pii_options); set by pii_options_init. Must equal the library's (pii_options_size()): the struct comes from
   * the header of the same release, and calls with another size return PII_ERR_ARG (1.4.0; earlier releases accepted a
   * prefix). */
  size_t struct_size;
  /* Moves the decision threshold: > 0 masks more (fewer misses, more over-masking), < 0 masks less. Range about -1..1.
   * Same as the npm option `sensitivity`: the O logit gets the bias -sensitivity. Default 0. Must be finite: NaN or
   * +/-infinity is PII_ERR_ARG (1.4.1; earlier releases dropped every model span with NaN and masked everything or
   * nothing with an infinity). */
  double sensitivity;
  /* Labels to report: PII_LABEL_BIT(label) per label. Default PII_LABELS_ALL; 0 reports nothing. */
  uint32_t labels;
  /* Optional spans to merge with the model's (source PII_SOURCE_REGEX), as the npm package merges its regex layer:
   * overlapping spans become one, the model's label wins, and a pattern label is used only where no model label of the
   * same family (URL / URL_L2, PERSON / PERSON_L2) covers the text. */
  const pii_extra_span *extra;
  size_t n_extra;
  int extra_unit; /* PII_UNIT_*; default PII_UNIT_UTF8 */
  /* 0 (default): emoji are taken out of the text the model reads, so the model does not read them as PII; the spans keep the offsets of the input
   * (an emoji inside a span splits it). 1: the model reads the text as given. Added in 1.2.0. */
  int keep_emoji;
  /* 1: a span that starts or ends inside a word is widened to the whole word ("R23", not
   * "[ID_NUMBER]23"). A word is a run of letters, digits and marks (Unicode L, N, M) joined by apostrophes and hyphens;
   * letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, ...) never extend a span, and a word ends
   * where Hangul meets digits or Latin letters ("010-1234-5678입니다"); Korean particles after a
   * Hangul span stay ("홍길동님께"). In table mode the
   * widening stays inside the cell value. Default 1 (set by pii_options_init); 0: spans end where the model ends them (1.2
   * behaviour). Same as the npm option `wholeWords`. Added in 1.3.0. */
  int whole_words;
  /* 1: on a text the model reads as at most 8 tokens (a survey answer, a table cell), the model needs stronger evidence to
   * read it as a person's name (PERSON and PERSON_L2 logits -3), so short values such as "경기", "남성" or "네이버" are left
   * alone; a lone name is missed a little more often. Longer texts are unchanged. 0 (default): no change. Same as the npm
   * option `strictShortNames`. Added in 1.3.0. */
  int strict_short_names;
  /* Optional user lists (pii_lists_create), borrowed for the call; NULL (default): none. Added in 1.4.0. */
  const pii_lists *lists;
} pii_options;

/* Table mode (pii_detect_table). */
typedef struct {
  size_t struct_size; /* sizeof(pii_table_options); set by pii_table_options_init; must equal pii_table_options_size() */
  int delimiter;      /* 0 = auto (default), ',' , '\t', ';' or '|' (markdown pipe table) */
  int header;         /* 1 (default): the first row is a header row (never voted on or propagated into) */
  int vote;           /* 1 (default): column vote and propagation; 0: whole-file detection clipped to cells only */
  /* PII_TABLE_CONTEXT_NONE (default) or PII_TABLE_CONTEXT_HEADER: with a header row, each non-empty data cell of a column whose
   * non-empty values are at least 80% distinct is also read on its own as "<header>: <value>", and those spans are added
   * (fewer misses on name, contact and ID columns, more PII-free cells masked, one more model call per such cell). 1.2.0. */
  int context;
} pii_table_options;
#define PII_TABLE_CONTEXT_NONE 0
#define PII_TABLE_CONTEXT_HEADER 1

/* Library version ("1.5.0"). */
PII_API const char *pii_version(void);
/* Message of a return code ("invalid argument", ...); static, never NULL. */
PII_API const char *pii_strerror(int code);
/* Kernel build: "neon", "sse", "vector" or "scalar", with "+fma" where the matrix kernel fuses multiply and add: always on
 * arm64 (unless built with PII_NO_FMA), elsewhere only in a PII_FMA build. A build without "+fma" gives bit-identical
 * results on every CPU and equals the npm package's baseline WebAssembly build; "+fma" equals its relaxed-SIMD build
 * (which Chrome, Edge and Firefox run) and is about 1.5x as fast on arm64: spans equal on the fixtures, logits differ in
 * the last bits. */
PII_API const char *pii_build_info(void);

/* Label name ("PERSON", ...) or NULL; label id of a name, or -1. */
PII_API const char *pii_label_name(int label);
PII_API int pii_label_from_name(const char *name);

/* Defaults: sensitivity 0, all labels, no extra spans. */
PII_API void pii_options_init(pii_options *options);

/* Loads a model from the bytes of onnx/model_<variant>.onnx and tokenizer.web.json (both may be freed afterwards).
 * Verify the files first (pii_verify_file). On failure returns NULL and writes a message to err (if errlen > 0). */
PII_API pii_model *pii_model_load(const uint8_t *onnx, size_t onnx_len, const uint8_t *tokenizer_json, size_t tokenizer_len,
                                  char *err, size_t errlen);
PII_API void pii_model_free(pii_model *model);

typedef struct {
  int hidden, layers, layer_applications, vocab, max_tokens, tags, embedding_kind; /* embedding_kind 1: int8 (q4), 2: int4 (q4e) */
} pii_model_info;
PII_API void pii_model_get_info(const pii_model *model, pii_model_info *info);

/* Spans to mask in utf8[0, len), sorted and non-overlapping and widened to whole grapheme clusters (the npm package's
 * detect() with regex: false, plus options->extra when given, and options->lists: block entries added, allowed spans
 * dropped). options may be NULL (defaults). On success *out holds *n_out spans (NULL when none);
 * free with pii_spans_free. Returns PII_OK or a PII_ERR_* code (pii_strerror). Texts longer than INT32_MAX bytes and
 * malformed options (another struct_size, a NaN or infinite sensitivity, an extra span out of range or with an unknown
 * label or unit) are PII_ERR_ARG; working memory is about 20 bytes per input byte plus one 256-token window. */
PII_API int pii_detect(const pii_model *model, const char *utf8, size_t len, const pii_options *options, pii_span **out,
                       size_t *n_out);
PII_API void pii_spans_free(pii_span *spans);

/* Defaults: auto-detected delimiter, a header row, column vote on. */
PII_API void pii_table_options_init(pii_table_options *table);
/* Spans of a CSV, TSV, semicolon-separated or markdown pipe table in utf8[0, len), sorted and non-overlapping, never crossing
 * a cell boundary (delimiters, quotes, line breaks and separator lines stay unmasked, so a redaction that inserts none of
 * them keeps the table valid). The whole text is read by pii_detect (same options), its spans are clipped to the cell
 * values (RFC 4180 fields; header cells included), and with table->vote a column of short values (at least 5 non-empty
 * data cells, median length <= 40 code points and <= 4 words) whose cells are at least 80% covered by PERSON (or
 * PERSON_L2), EMAIL, PHONE or USERNAME in at least 4 cells and 80% of them gets that label on every non-empty data cell
 * (source PII_SOURCE_TABLE; placeholders such as "N/A", EMAIL values without "@" and PHONE values with fewer than 6 digits
 * are skipped; only labels enabled in options->labels). Spans are widened to whole grapheme clusters as in pii_detect, but
 * never past the edges of their cell value (a value that starts with a combining mark or ends with a Prepend character
 * shares a cluster with the delimiter or quote beside it). Text that is not a table (fewer than 2 rows, or no delimiter
 * found by auto-detection) gives exactly the spans of pii_detect. options and table may be NULL (defaults). Return codes,
 * ownership (pii_spans_free) and offsets as pii_detect; PII_ERR_ARG for an unknown delimiter. The npm package's
 * Redactor.detectTable gives the same spans (with its regex layer passed here as options->extra). */
PII_API int pii_detect_table(const pii_model *model, const char *utf8, size_t len, const pii_options *options,
                             const pii_table_options *table, pii_span **out, size_t *n_out);

/* Validates and copies the lists (an empty list may be NULL with count 0) into an immutable handle for pii_options.lists.
 * PII_OK: *out holds the handle (free with pii_lists_free after every call that borrows it has returned). PII_ERR_ARG with
 * *err set: an invalid entry (the first in list order: allow, then block; err never holds the entry's text); PII_ERR_ARG
 * with *err unset: a NULL out or err, or a NULL list with a nonzero count. Every error leaves *out NULL (when out is not
 * NULL). Equal entries (same form and label) merge; allow entries that differ only in label merge into one entry allowing
 * all their families. */
PII_API int pii_lists_create(const pii_term *allow, size_t n_allow, const pii_term *block, size_t n_block, pii_lists **out,
                             pii_list_error *err);
PII_API void pii_lists_free(pii_lists *lists);
/* Entries of each list after merging (0 for a NULL handle). */
PII_API void pii_lists_counts(const pii_lists *lists, size_t *n_allow, size_t *n_block);
/* sizeof(pii_options) and sizeof(pii_table_options) of the library (bindings that lay the structs out by hand check theirs). */
PII_API size_t pii_options_size(void);
PII_API size_t pii_table_options_size(void);

/* Token ids and offsets without <s> and </s>, as the npm package's tokenizer gives them: the ids and offsets of Hugging
 * Face tokenizers, except that a grapheme the normalizer replaces (e.g. "e" + U+0301) aligns as a whole. */
PII_API int pii_tokenize(const pii_model *model, const char *utf8, size_t len, pii_token **out, size_t *n_out);
PII_API void pii_tokens_free(pii_token *tokens);

/* Published model files of release PII_MODEL_VERSION, for download and verification. size: "nano", "small", "medium",
 * "large"; file: "q4", "q4e" or "tokenizer". path is relative to https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.1.0/
 * ("onnx/model_q4e.onnx", "tokenizer.web.json"). Returns NULL for an unknown size or file. */
typedef struct {
  const char *size, *file, *path;
  uint64_t bytes;
  const char *sha256; /* lowercase hex */
} pii_file_pin;
PII_API const pii_file_pin *pii_model_file(const char *size, const char *file);
/* Default variant of a size, as in the npm package: "q4e" for large and medium, "q4" for small and nano; NULL if unknown. */
PII_API const char *pii_default_variant(const char *size);
/* SHA-256 of data[0, n) as 64 lowercase hex characters + NUL in hex_out[65]. */
PII_API void pii_sha256_hex(const void *data, size_t n, char hex_out[65]);
/* PII_OK when data matches the published size and SHA-256 of (size, file), else PII_ERR_ARG. */
PII_API int pii_verify_file(const void *data, size_t n, const char *size, const char *file);

#ifdef __cplusplus
}
#endif
#endif
