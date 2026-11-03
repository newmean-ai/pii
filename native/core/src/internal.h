// Internal interfaces of the core (not installed).
#ifndef PII_INTERNAL_H
#define PII_INTERNAL_H
#include <stddef.h>
#include <stdint.h>

#include "../pii.h"

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "newmean-pii core: little-endian targets only (model files are little-endian)"
#endif

void pii_set_err(char *err, size_t errlen, const char *fmt, ...);
void *pii_aligned_alloc(size_t n);  // 64-byte aligned, zeroed; free with pii_aligned_free
void pii_aligned_free(void *p);

// ---------------------------------------------------------------- growable arrays
typedef struct {
  void *p;
  size_t n, cap, elem;
} pii_vec;
void pii_vec_init(pii_vec *v, size_t elem);
int pii_vec_reserve(pii_vec *v, size_t n);
void *pii_vec_push(pii_vec *v);  // returns the new (zeroed) element or NULL
void pii_vec_free(pii_vec *v);

// ---------------------------------------------------------------- engine (port of js/engine/pii.c)
typedef struct {  // MatMulNBits weight in panel layout: per 8 columns, per 32 rows of K: f32 scales[8], 128 bytes
  const uint8_t *data;
  int K, N, Np;
} pii_q4;

typedef struct {
  int kind;  // 1: u8 per tensor (scale, zero point), 2: int4 blocks of 32 with f32 scales
  int rows, cols;
  float scale, zp;
  const float *scales;
  const uint8_t *q;
} pii_emb;

typedef struct {
  pii_q4 wq, wk, wv, wo, wi, wout;
  const float *bq, *bk, *bv, *bo, *ln1g, *ln1b, *bi, *bout, *ln2g, *ln2b;
} pii_layer;

#define PII_MAX_APPLY 64

typedef struct {  // immutable after pii_engine_load
  int H, heads, I, T, V, E, kind, NL, NA, pad, max_pos;
  float eps;
  int apply[PII_MAX_APPLY];
  pii_emb word, pos, type;
  int has_proj;
  pii_q4 proj;
  const float *elng, *elnb;
  pii_layer *layers;
  pii_q4 cls;
  const float *bcls;
  float *typerow;
  uint8_t *image;  // the owned .piiw image every pointer above points into
} pii_engine;

typedef struct {  // per-call scratch for windows of up to cap tokens
  int cap;
  float *x, *x2, *q, *k, *v, *ctx, *ff, *emb, *kt, *scores, *panel, *logits, *tail;
  void *block;
} pii_ws;

int pii_engine_load(pii_engine *m, uint8_t *image, size_t len);  // takes ownership of image (aligned alloc) on success
void pii_engine_free(pii_engine *m);
int pii_engine_max_len(const pii_engine *m);
int pii_ws_init(pii_ws *w, const pii_engine *m, int L);
void pii_ws_free(pii_ws *w);
// ids[L] (with <s> and </s>) -> logits [L, T] inside w; NULL on error.
const float *pii_engine_run(const pii_engine *m, pii_ws *w, const int32_t *ids, int L);

// ---------------------------------------------------------------- ONNX -> .piiw (port of onnx-weights.ts)
int pii_onnx_to_piiw(const uint8_t *model, size_t n, uint8_t **image, size_t *len, char *err, size_t errlen);

// ---------------------------------------------------------------- JSON (just enough for tokenizer.web.json)
enum { PJ_NULL, PJ_BOOL, PJ_NUM, PJ_STR, PJ_ARR, PJ_OBJ };
typedef struct pii_json {
  int type;
  double num;
  char *str;  // PJ_STR: UTF-8, NUL-terminated; len bytes
  size_t len;
  struct pii_json *items;  // PJ_ARR / PJ_OBJ
  char **keys;             // PJ_OBJ
  size_t n;
} pii_json;
int pii_json_parse(const char *s, size_t n, pii_json *out);  // 0 on success
void pii_json_free(pii_json *j);
const pii_json *pii_json_get(const pii_json *obj, const char *key);
uint8_t *pii_base64(const char *s, size_t n, size_t *out_len);

// ---------------------------------------------------------------- tokenizer (port of tokenizer.ts)
typedef struct pii_tok pii_tok;
pii_tok *pii_tok_load(const uint8_t *json, size_t n, char *err, size_t errlen);
void pii_tok_free(pii_tok *t);
int pii_tok_bos(const pii_tok *t);
int pii_tok_eos(const pii_tok *t);
int pii_tok_pad(const pii_tok *t);
// Tokens of cps[0..n) without <s> and </s>: ids and [start, end) in code points. Appends to the vectors (int32_t each).
int pii_tok_encode(const pii_tok *t, const uint32_t *cps, size_t n, pii_vec *ids, pii_vec *starts, pii_vec *ends);
// End of the extended grapheme cluster (UAX #29) that starts at cps[i], i < n.
size_t pii_grapheme_end(const uint32_t *cps, size_t i, size_t n);
// Extended_Pictographic (UAX #29 table of the tokenizer).
int pii_is_pictographic(uint32_t c);

// ---------------------------------------------------------------- text
typedef struct {
  uint32_t *cp;   // code points
  size_t n;
  size_t *u8;     // n + 1 byte offsets
  size_t *u16;    // n + 1 UTF-16 offsets
} pii_text;
int pii_text_init(pii_text *t, const char *utf8, size_t len);
void pii_text_free(pii_text *t);
int pii_is_js_space(uint32_t c);  // JavaScript \s and String.prototype.trim

// ---------------------------------------------------------------- decode (port of decode.ts and redactor.combine)
typedef struct {
  size_t start, end;  // code points
  int label, source;
  // Winner key of a block region (source PII_SOURCE_BLOCK; unused otherwise): the smallest (start, length in code points,
  // entry index) of its matches; kept unchanged by copying, clipping, widening and snapping.
  size_t key_start;
  uint32_t key_len, key_index;
} pii_cspan;
// Model and extra spans (sorted in place) -> masking regions in out (capacity n); returns the count. Overlapping model
// spans rank by length in UTF-16 units (u16: the text's n + 1 UTF-16 offsets; NULL counts code points). Exposed for tests.
size_t pii_combine(pii_cspan *all, size_t n, uint32_t enabled, const size_t *u16, pii_cspan *out);

// ---------------------------------------------------------------- table mode
typedef struct {
  size_t start, end;  // value range in code points (CSV: inside the quotes, untrimmed; markdown: trimmed)
  uint32_t row, col;  // row 0 is the header row when there is one
} pii_cell;
// Cells of the rows of tx in text order (pii_cell each, appended to cells), with delimiter ',', '\t', ';', '|' (markdown)
// or 0 (auto-detect). *delim_used: the delimiter used, 0 when auto-detection found none (no cells). *rows: row count.
int pii_table_parse(const pii_text *tx, int delimiter, pii_vec *cells, size_t *rows, int *delim_used);
// Grapheme snapping (sorted, non-overlapping spans): starts move back and ends forward to extended grapheme cluster
// boundaries of cp[0, ncp) (empty spans stay as they are); spans that then meet inside one cluster merge, keeping the label
// and source of the longer original span in code points (equal: the earlier). cells (table mode, in text order; NULL for
// none): a span inside a cell's value range is clamped to that range, so snapping never crosses a delimiter, quote or line
// break. In place; returns the new count.
size_t pii_snap_graphemes(pii_cspan *s, size_t n, const uint32_t *cp, size_t ncp, const pii_cell *cells, size_t ncells);
// As pii_snap_graphemes; words (pii_options.whole_words): each span is first widened to the words its start and end fall in
// (snapGraphemes of js/src/table.ts with words), then sorted again by start and merged by the same rule.
// (size_t)-1 when the words step cannot allocate (PII_ERR_NOMEM: skipping the widening would change the result).
size_t pii_snap_spans(pii_cspan *s, size_t n, const uint32_t *cp, size_t ncp, const pii_cell *cells, size_t ncells, int words);
// Table-mode steps after whole-text detection: spans (detect's sorted, non-overlapping spans before snapping) -> clipped to
// cells, column vote and propagation (labels: enabled labels), merge, snapping. Not a table: the snapped spans. *out is
// malloc'ed (NULL when *n_out is 0). Exposed for tests.
// As pii_table_apply, with extra spans (inside cells) merged after the vote, before the overlap merge (header context).
// words: pii_options.whole_words, applied in the last (snapping) step.
int pii_table_apply_ext(const pii_text *tx, const pii_cspan *spans, size_t n, const pii_cspan *extra, size_t n_extra, int delimiter,
                        int header, int vote, uint32_t labels, int words, pii_cspan **out, size_t *n_out);
int pii_table_apply(const pii_text *tx, const pii_cspan *spans, size_t n, int delimiter, int header, int vote, uint32_t labels,
                    pii_cspan **out, size_t *n_out);
// As pii_table_apply_ext, with user lists (NULL: none): block regions per cell, the allow filter before the vote and after
// the final merge; not a table: pii_detect_finish.
int pii_table_apply_lists(const pii_text *tx, const pii_cspan *spans, size_t n, const pii_cspan *extra, size_t n_extra,
                          int delimiter, int header, int vote, uint32_t labels, int words, const pii_lists *lists,
                          pii_cspan **out, size_t *n_out);
// The steps of pii_detect after combine: spans (combine's sorted, non-overlapping spans) merged with the block regions of the
// whole text, widened (words) and snapped, then the allow filter. *out is malloc'ed (NULL when *n_out is 0).
int pii_detect_finish(const pii_text *tx, const pii_cspan *spans, size_t n, const pii_lists *lists, int words, pii_cspan **out,
                      size_t *n_out);
// ---------------------------------------------------------------- whole words and user lists (lists.c)
int pii_word_cp(uint32_t c);  // isWordChar: Unicode L, N, M outside the scripts written without spaces
int pii_hangul(uint32_t c);
int pii_joiner(uint32_t c);   // apostrophes and hyphens join two words
int pii_same_word(uint32_t c, int hangul);
int pii_particles_only(const uint32_t *cp, size_t n);
extern const size_t pii_max_particle_run;  // PII_MAX_PARTICLE_RUN of word_tables.h: longer rests are never particles only
// Block-key order: start ascending, then length descending, then entry index ascending (a before b).
int pii_key_less(const pii_cspan *a, const pii_cspan *b);
int pii_lists_has_allow(const pii_lists *l);
int pii_lists_has_block(const pii_lists *l);
// cp[a, b) (in compared form) is an allow entry whose families include label's.
int pii_lists_allowed(const pii_lists *l, const uint32_t *cp, size_t a, size_t b, int label);
// Block regions of the search range [a, b) of cp, appended to out (pii_cspan, source PII_SOURCE_BLOCK, in start order):
// every boundary-valid match, overlapping ones unioned, each region with the label and key of its smallest key.
int pii_lists_block(const pii_lists *l, const uint32_t *cp, size_t a, size_t b, pii_vec *out);

#define PII_WINDOW 256
#define PII_STRIDE 64
// pii_options.strict_short_names: texts of at most PII_SHORT_TOKENS tokens get PII_SHORT_PERSON_BIAS on the PERSON and
// PERSON_L2 logits (tags 1-4); same constants as js/src/redactor.ts.
#define PII_SHORT_TOKENS 8
#define PII_SHORT_PERSON_BIAS (-3.0)

#endif
