/* newmean-pii native core: multilingual PII detection with the newmean-pii-<size> models, on device.
 *
 * One C library shared by the Python, Android, iOS and Flutter packages. It reads the published model files
 * (onnx/model_<variant>.onnx and tokenizer.web.json from https://huggingface.co/newmean-ai/pii-<size>), tokenizes,
 * runs the model and decodes spans like detect() of the npm package @newmean-ai/pii with regex: false: the same token
 * ids, logits bit-identical to its wasm engine (see pii_build_info), and token offsets that follow Hugging Face
 * tokenizers, which differ from the npm package only where the normalizer rewrites a combining sequence such as
 * "e" + U+0301 (see pii_tokenize). The pattern (regex) layer and the redaction modes are left to each language; their
 * spans can be merged here (pii_options.extra) so that every package shares one combine step.
 *
 * Threads: a pii_model is immutable after pii_model_load; any number of threads may call pii_detect and pii_tokenize
 * on the same model at once (each call uses its own scratch memory). Nothing is global.
 *
 * Offsets: every span and token carries [start, end) in UTF-8 bytes of the input, in code points (Python str), and in
 * UTF-16 code units (JavaScript, Java/Kotlin, Dart, NSString), so wrappers need no conversion. Invalid UTF-8 bytes count
 * as one code point (U+FFFD) each; surrogates in 3-byte form (Python "surrogatepass") count as one code point. Offsets
 * always fall on code point boundaries, but a span may end inside a grapheme (before a combining mark the normalizer
 * dropped): slice by code points or UTF-16 units, not by grapheme (Swift Character) indices. Pass standard UTF-8: from
 * Java/Kotlin String.getBytes(UTF_8), not JNI GetStringUTFChars (modified UTF-8 splits emoji into surrogates and NUL
 * into two bytes, which changes tokens and offsets); from Python str.encode("utf-8", "surrogatepass").
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

#define PII_VERSION "1.0.0"       /* this library */
#define PII_MODEL_VERSION "1.0.0" /* model release (Hugging Face tag v1.0.0) */
#define PII_DEFAULT_SIZE "medium"

/* Return codes */
#define PII_OK 0
#define PII_ERR_ARG (-1)    /* invalid argument (NULL pointer, extra span out of range, unknown label) */
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

typedef struct {
  size_t struct_size; /* sizeof(pii_options); set by pii_options_init (fields may be appended in later versions) */
  /* Moves the decision threshold: > 0 masks more (fewer misses, more over-masking), < 0 masks less. Range about -1..1.
   * Same as the npm option `sensitivity`: the O logit gets the bias -sensitivity. Default 0. */
  double sensitivity;
  /* Labels to report: PII_LABEL_BIT(label) per label. Default PII_LABELS_ALL; 0 reports nothing. */
  uint32_t labels;
  /* Optional spans to merge with the model's (source PII_SOURCE_REGEX), as the npm package merges its regex layer:
   * overlapping spans become one, the model's label wins, and a pattern label is used only where no model label of the
   * same family (URL / URL_L2, PERSON / PERSON_L2) covers the text. */
  const pii_extra_span *extra;
  size_t n_extra;
  int extra_unit; /* PII_UNIT_*; default PII_UNIT_UTF8 */
} pii_options;

/* Library version ("1.0.0"). */
PII_API const char *pii_version(void);
/* Message of a return code ("invalid argument", ...); static, never NULL. */
PII_API const char *pii_strerror(int code);
/* Kernel build: "neon", "sse", "vector" or "scalar", with "+fma" for a PII_FMA build. The default build (no "+fma") gives
 * bit-identical results on every CPU and equals the npm package's baseline WebAssembly build; "+fma" fuses the matrix
 * multiply-add like its relaxed-SIMD build (about 25% faster, spans equal on the fixtures, logits differ in the last bits). */
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

/* Spans to mask in utf8[0, len), sorted and non-overlapping (the npm package's detect() with regex: false, plus
 * options->extra when given). options may be NULL (defaults). On success *out holds *n_out spans (NULL when none);
 * free with pii_spans_free. Returns PII_OK or a PII_ERR_* code (pii_strerror). Texts longer than INT32_MAX bytes are
 * PII_ERR_ARG; working memory is about 20 bytes per input byte plus one 256-token window. */
PII_API int pii_detect(const pii_model *model, const char *utf8, size_t len, const pii_options *options, pii_span **out,
                       size_t *n_out);
PII_API void pii_spans_free(pii_span *spans);

/* Token ids and offsets without <s> and </s>, as Hugging Face tokenizers gives them (ids as the npm tokenizer; offsets of
 * characters replaced inside a grapheme, e.g. "e" + U+0301, follow Hugging Face where tokenizer.ts differs). */
PII_API int pii_tokenize(const pii_model *model, const char *utf8, size_t len, pii_token **out, size_t *n_out);
PII_API void pii_tokens_free(pii_token *tokens);

/* Published model files of release PII_MODEL_VERSION, for download and verification. size: "nano", "small", "medium",
 * "large"; file: "q4", "q4e" or "tokenizer". path is relative to https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/
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
