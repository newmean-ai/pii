// Public API: loading, detect (windows, best tag per token, BIO spans, merging; port of js/src/decode.ts and the
// combine step of js/src/redactor.ts), grapheme snapping, table mode and tokenize.
#include <stdlib.h>
#include <string.h>

#include "internal.h"

struct pii_model {
  pii_engine eng;
  pii_tok *tok;
};

#define N_TAGS (1 + 2 * PII_LABEL_COUNT)  // O, then B-/I- per label in label order
_Static_assert(PII_LABELS_ALL == (1u << PII_LABEL_COUNT) - 1, "PII_LABELS_ALL");
_Static_assert(PII_LABELS_LEVEL1 == (PII_LABELS_ALL & ~(PII_LABEL_BIT(PII_PERSON_L2) | PII_LABEL_BIT(PII_URL_L2))), "PII_LABELS_LEVEL1");

static const char *const LABEL_NAMES[PII_LABEL_COUNT] = {"PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL",
                                                   "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"};

const char *pii_version(void) { return PII_VERSION; }

const char *pii_strerror(int code) {
  switch (code) {
    case PII_OK: return "ok";
    case PII_ERR_ARG: return "invalid argument";
    case PII_ERR_NOMEM: return "out of memory";
    case PII_ERR_MODEL: return "model failed";
    default: return "unknown error";
  }
}

const char *pii_label_name(int label) { return label >= 0 && label < PII_LABEL_COUNT ? LABEL_NAMES[label] : NULL; }

int pii_label_from_name(const char *name) {
  if (!name) return -1;
  for (int i = 0; i < PII_LABEL_COUNT; i++)
    if (strcmp(name, LABEL_NAMES[i]) == 0) return i;
  return -1;
}

static int family(int label) { return label == PII_PERSON_L2 ? PII_PERSON : label == PII_URL_L2 ? PII_URL : label; }

void pii_options_init(pii_options *o) {
  if (!o) return;
  memset(o, 0, sizeof *o);
  o->struct_size = sizeof *o;
  o->labels = PII_LABELS_ALL;
  o->extra_unit = PII_UNIT_UTF8;
}

pii_model *pii_model_load(const uint8_t *onnx, size_t onnx_len, const uint8_t *tok_json, size_t tok_len, char *err, size_t errlen) {
  if (err && errlen) err[0] = 0;
  if (!onnx || !tok_json) {
    pii_set_err(err, errlen, "no model or tokenizer data");
    return NULL;
  }
  pii_model *m = calloc(1, sizeof *m);
  if (!m) {
    pii_set_err(err, errlen, "out of memory");
    return NULL;
  }
  uint8_t *image;
  size_t len;
  if (pii_onnx_to_piiw(onnx, onnx_len, &image, &len, err, errlen)) {
    free(m);
    return NULL;
  }
  int rc = pii_engine_load(&m->eng, image, len);
  if (rc) {
    pii_aligned_free(image);
    free(m);
    pii_set_err(err, errlen, "unsupported model file (code %d)", rc);
    return NULL;
  }
  if (m->eng.T != N_TAGS || pii_engine_max_len(&m->eng) < PII_WINDOW) {
    pii_set_err(err, errlen, "model has %d tags and %d positions, expected %d tags and at least %d", m->eng.T,
                pii_engine_max_len(&m->eng), N_TAGS, PII_WINDOW);
    pii_model_free(m);
    return NULL;
  }
  m->tok = pii_tok_load(tok_json, tok_len, err, errlen);
  if (!m->tok) {
    pii_model_free(m);
    return NULL;
  }
  return m;
}

void pii_model_free(pii_model *m) {
  if (!m) return;
  pii_engine_free(&m->eng);
  pii_tok_free(m->tok);
  free(m);
}

void pii_model_get_info(const pii_model *m, pii_model_info *info) {
  if (!m || !info) return;
  info->hidden = m->eng.H;
  info->layers = m->eng.NL;
  info->layer_applications = m->eng.NA;
  info->vocab = m->eng.V;
  info->max_tokens = pii_engine_max_len(&m->eng);
  info->tags = m->eng.T;
  info->embedding_kind = m->eng.kind;
}

// ---------------------------------------------------------------- spans
// Comparators get the text's UTF-16 offsets (or NULL: code points); only lengths depend on them.
static int cmp_start(const pii_cspan *a, const pii_cspan *b, const size_t *u16) {
  (void)u16;
  return a->start < b->start ? -1 : a->start > b->start;
}
static int cmp_start_end_desc(const pii_cspan *a, const pii_cspan *b, const size_t *u16) {
  (void)u16;
  if (a->start != b->start) return a->start < b->start ? -1 : 1;
  return a->end > b->end ? -1 : a->end < b->end;
}
// Longest first, in UTF-16 units like redactor.ts (an emoji counts 2).
static int cmp_len_desc(const pii_cspan *a, const pii_cspan *b, const size_t *u16) {
  size_t la = u16 ? u16[a->end] - u16[a->start] : a->end - a->start, lb = u16 ? u16[b->end] - u16[b->start] : b->end - b->start;
  return la > lb ? -1 : la < lb;
}

// Stable sort (Array.prototype.sort is stable): bottom-up merge sort, insertion sort for short lists (or without memory).
// Model spans plus many pattern spans of a long text would make insertion sort quadratic.
static void sort_spans(pii_cspan *s, size_t n, int (*cmp)(const pii_cspan *, const pii_cspan *, const size_t *),
                       const size_t *u16) {
  pii_cspan *tmp = n > 32 ? malloc(n * sizeof *tmp) : NULL;
  if (tmp) {
    for (size_t w = 1; w < n; w *= 2)
      for (size_t lo = 0; lo + w < n; lo += 2 * w) {
        const size_t mid = lo + w, hi = n - lo > 2 * w ? lo + 2 * w : n;
        memcpy(tmp, s + lo, w * sizeof *tmp);
        size_t a = 0, b = mid, o = lo;
        while (a < w && b < hi) s[o++] = cmp(&s[b], &tmp[a], u16) < 0 ? s[b++] : tmp[a++];
        while (a < w) s[o++] = tmp[a++];
      }
    free(tmp);
    return;
  }
  for (size_t i = 1; i < n; i++) {
    pii_cspan x = s[i];
    size_t j = i;
    for (; j > 0 && cmp(&x, &s[j - 1], u16) < 0; j--) s[j] = s[j - 1];
    s[j] = x;
  }
}

// text.slice(a, b).trim() === ""
static int blank(const pii_text *t, size_t a, size_t b) {
  for (size_t i = a; i < b && i < t->n; i++)
    if (!pii_is_js_space(t->cp[i])) return 0;
  return 1;
}

// Same-label spans separated only by whitespace (or overlapping) are merged (decode.ts mergeAdjacent).
static size_t merge_adjacent(pii_cspan *s, size_t n, const pii_text *t) {
  sort_spans(s, n, cmp_start, NULL);
  size_t k = 0;
  for (size_t i = 0; i < n; i++) {
    if (k && s[k - 1].label == s[i].label && blank(t, s[k - 1].end, s[i].start)) {
      if (s[i].end > s[k - 1].end) s[k - 1].end = s[i].end;
    } else s[k++] = s[i];
  }
  return k;
}

// Model and extra spans -> masking regions (redactor.ts combine). Writes into out (capacity n); returns the count.
size_t pii_combine(pii_cspan *all, size_t n, uint32_t enabled, const size_t *u16, pii_cspan *out) {
  sort_spans(all, n, cmp_start_end_desc, u16);
  size_t nout = 0;
  for (size_t g0 = 0; g0 < n;) {
    size_t g1 = g0 + 1, end = all[g0].end;
    while (g1 < n && all[g1].start < end) {
      if (all[g1].end > end) end = all[g1].end;
      g1++;
    }
    // model spans by length (longest first), then extra spans of families no model span has
    pii_cspan *g = all + g0;
    size_t gn = g1 - g0, nm = 0;
    for (size_t i = 0; i < gn; i++)
      if (g[i].source == PII_SOURCE_MODEL) {
        pii_cspan x = g[i];
        memmove(g + nm + 1, g + nm, (i - nm) * sizeof x);
        g[nm++] = x;
      }
    sort_spans(g, nm, cmp_len_desc, u16);
    uint32_t fam = 0;
    for (size_t i = 0; i < nm; i++) fam |= 1u << family(g[i].label);
    int first = -1, has_model = 0, has_extra = 0;
    size_t s = 0, e = 0;
    for (size_t i = 0; i < gn; i++) {
      const pii_cspan *x = &g[i];
      if (x->source != PII_SOURCE_MODEL && (fam >> x->label & 1)) continue;
      if (!(enabled >> x->label & 1)) continue;
      if (first < 0) {
        first = x->label;
        s = x->start;
        e = x->end;
      }
      if (x->start < s) s = x->start;
      if (x->end > e) e = x->end;
      if (x->source == PII_SOURCE_MODEL) has_model = 1;
      else has_extra = 1;
    }
    if (first >= 0) {
      out[nout].start = s;
      out[nout].end = e;
      out[nout].label = first;
      out[nout].source = (has_model ? PII_SOURCE_MODEL : 0) | (has_extra ? PII_SOURCE_REGEX : 0);
      nout++;
    }
    g0 = g1;
  }
  return nout;
}

// Index of the code point that contains unit offset x (offsets[] has n + 1 increasing entries).
static size_t to_cp(const size_t *offsets, size_t n, size_t x) {
  size_t lo = 0, hi = n;
  while (lo < hi) {
    size_t mid = (lo + hi + 1) / 2;
    if (offsets[mid] <= x) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

// ---------------------------------------------------------------- grapheme snapping
// One forward walk over the clusters (spans are sorted and non-overlapping, so starts and ends only move forward). Empty
// spans are kept as they are. With cells, a span inside a cell is clamped to its value range after widening, so a cell value
// that starts or ends inside a cluster (a leading U+0301, a trailing U+0600) never takes in a delimiter or quote.
size_t pii_snap_graphemes(pii_cspan *s, size_t n, const uint32_t *cp, size_t ncp, const pii_cell *cells, size_t ncells) {
  size_t cs = 0, ce = 0, k = 0, win = 0, j = 0;  // current cluster [cs, ce); win: original length of the last output's label span
  for (size_t i = 0; i < n; i++) {
    const pii_cspan x = s[i];
    const size_t len = x.end - x.start;
    size_t a = x.start, b = x.end;
    if (x.start < x.end) {
      while (ce <= x.start && ce < ncp) cs = ce, ce = pii_grapheme_end(cp, cs, ncp);
      if (x.start < ce) a = cs;  // the cluster that contains the start
      while (ce < x.end && ce < ncp) cs = ce, ce = pii_grapheme_end(cp, cs, ncp);
      if (ce > b) b = ce;  // the end of the cluster that contains x.end - 1
      if (cells) {
        while (j < ncells && cells[j].end <= x.start) j++;  // cells are in text order with increasing ends
        if (j < ncells && cells[j].start <= x.start && x.end <= cells[j].end) {
          if (a < cells[j].start) a = cells[j].start;
          if (b > cells[j].end) b = cells[j].end;
        }
      }
    }
    if (k && a < s[k - 1].end) {
      if (a < s[k - 1].start) s[k - 1].start = a;  // only an empty span before x in x's cluster starts later
      if (b > s[k - 1].end) s[k - 1].end = b;
      if (len > win) {
        s[k - 1].label = x.label;
        s[k - 1].source = x.source;
        win = len;
      }
    } else {
      s[k] = x;
      s[k].start = a;
      s[k].end = b;
      win = len;
      k++;
    }
  }
  return k;
}

// ---------------------------------------------------------------- table mode
// Whitespace everywhere below is pii_is_js_space (the set of the spec). Positions are code points.
static void trim(const uint32_t *cp, size_t *a, size_t *b) {
  while (*a < *b && pii_is_js_space(cp[*a])) ++*a;
  while (*b > *a && pii_is_js_space(cp[*b - 1])) --*b;
}

static size_t non_space(const uint32_t *cp, size_t a, size_t b) {
  size_t k = 0;
  for (size_t i = a; i < b; i++) k += !pii_is_js_space(cp[i]);
  return k;
}

// Record terminator at j: "\n" or "\r\n" (a lone "\r" is an ordinary character).
static int is_term(const uint32_t *cp, size_t n, size_t j) { return cp[j] == '\n' || (cp[j] == '\r' && j + 1 < n && cp[j + 1] == '\n'); }

static int push_cell(pii_vec *cells, size_t start, size_t end, size_t row, uint32_t col) {
  pii_cell *c = pii_vec_push(cells);
  if (!c) return PII_ERR_NOMEM;
  c->start = start;
  c->end = end;
  c->row = (uint32_t)row;
  c->col = col;
  return PII_OK;
}

// RFC 4180 records with delimiter d -> cells of the rows; a record of one empty field is not a row. Stops after max_rows
// rows (0: no limit).
static int parse_csv(const pii_text *tx, uint32_t d, size_t max_rows, pii_vec *cells, size_t *rows) {
  const uint32_t *cp = tx->cp;
  const size_t n = tx->n;
  size_t i = 0, row = 0;
  while (i < n && (!max_rows || row < max_rows)) {
    const size_t first = cells->n;
    for (uint32_t col = 0;; col++) {
      size_t vs, ve, j;
      if (i < n && cp[i] == '"') {  // quoted: "" stays in the value; text after the closing quote is skipped
        vs = j = i + 1;
        while (j < n && !(cp[j] == '"' && !(j + 1 < n && cp[j + 1] == '"'))) j += cp[j] == '"' ? 2 : 1;
        ve = j;
        if (j < n) j++;
        while (j < n && cp[j] != d && !is_term(cp, n, j)) j++;
      } else {
        vs = j = i;
        while (j < n && cp[j] != d && !is_term(cp, n, j)) j++;
        ve = j;
      }
      if (push_cell(cells, vs, ve, row, col)) return PII_ERR_NOMEM;
      if (j < n && cp[j] == d) {
        i = j + 1;
        continue;
      }
      i = j < n ? j + (cp[j] == '\r' ? 2 : 1) : n;
      break;
    }
    const pii_cell *c = (const pii_cell *)cells->p + first;
    size_t a = c->start, b = c->end;
    trim(cp, &a, &b);
    if (cells->n - first == 1 && a == b) cells->n = first;  // blank record
    else row++;
  }
  *rows = row;
  return PII_OK;
}

// Next line [*a, *b) from *pos (split at "\n", one trailing "\r" dropped); 0 when the text is exhausted.
static int next_line(const pii_text *tx, size_t *pos, size_t *a, size_t *b) {
  if (*pos > tx->n) return 0;
  size_t e = *pos;
  while (e < tx->n && tx->cp[e] != '\n') e++;
  *a = *pos;
  *b = e > *pos && tx->cp[e - 1] == '\r' ? e - 1 : e;
  *pos = e + 1;
  return 1;
}

// Markdown pipe table: cells of the table lines that are not separator lines.
static int parse_markdown(const pii_text *tx, pii_vec *cells, size_t *rows) {
  const uint32_t *cp = tx->cp;
  size_t pos = 0, a, b, row = 0;
  while (next_line(tx, &pos, &a, &b)) {
    trim(cp, &a, &b);
    if (a == b || cp[a] != '|') continue;
    const size_t first = cells->n;
    int sep = 1;
    uint32_t col = 0;
    for (size_t p = a + 1, s = a + 1; p <= b; p++) {
      if (p < b && cp[p] != '|') continue;
      if (p == b && cp[b - 1] == '|') break;  // the segment after a final '|' is not a cell
      size_t vs = s, ve = p;
      trim(cp, &vs, &ve);
      if (vs == ve) vs = ve = s;  // an all-space segment: an empty cell at the segment's start
      // separator cell: ^:?-+:?$
      size_t x = vs, y = ve;
      if (x < y && cp[x] == ':') x++;
      if (y > x && cp[y - 1] == ':') y--;
      int dashes = x < y;
      for (size_t q = x; q < y; q++) dashes &= cp[q] == '-';
      sep &= dashes;
      if (push_cell(cells, vs, ve, row, col++)) return PII_ERR_NOMEM;
      s = p + 1;
    }
    if (sep) cells->n = first;  // a separator line (or a line of zero cells) is not a row
    else row++;
  }
  *rows = row;
  return PII_OK;
}

// Auto-detection: markdown when the first non-empty line and at least 2 of the first 5 start with '|'; else the best of
// ',', '\t', ';' by the number of the first 20 rows with as many fields (>= 2) as the first row (ties: earlier). 0: none.
static int auto_delimiter(const pii_text *tx, pii_vec *cells, int *delim) {
  size_t pos = 0, a, b, seen = 0, pipes = 0;
  int first_pipe = 0;
  while (seen < 5 && next_line(tx, &pos, &a, &b)) {
    trim(tx->cp, &a, &b);
    if (a == b) continue;
    const int pipe = tx->cp[a] == '|';
    if (!seen) first_pipe = pipe;
    pipes += pipe;
    seen++;
  }
  *delim = 0;
  if (first_pipe && pipes >= 2) {
    *delim = '|';
    return PII_OK;
  }
  static const uint32_t cand[] = {',', '\t', ';'};
  size_t best = 0;
  for (size_t k = 0; k < 3; k++) {
    size_t rows;
    cells->n = 0;
    if (parse_csv(tx, cand[k], 20, cells, &rows)) return PII_ERR_NOMEM;
    const pii_cell *c = cells->p;
    size_t score = 0, want = 0;
    for (size_t i = 0; i < cells->n;) {  // field count of each row
      size_t j = i;
      while (j < cells->n && c[j].row == c[i].row) j++;
      if (i == 0) want = j - i;
      score += want >= 2 && j - i == want;
      i = j;
    }
    if (score > best) best = score, *delim = (int)cand[k];
  }
  cells->n = 0;
  if (best < 2) *delim = 0;
  return PII_OK;
}

int pii_table_parse(const pii_text *tx, int delimiter, pii_vec *cells, size_t *rows, int *delim_used) {
  *rows = 0;
  *delim_used = 0;
  cells->n = 0;
  if (!delimiter && auto_delimiter(tx, cells, &delimiter)) return PII_ERR_NOMEM;
  if (!delimiter) return PII_OK;
  *delim_used = delimiter;
  return delimiter == '|' ? parse_markdown(tx, cells, rows) : parse_csv(tx, (uint32_t)delimiter, 0, cells, rows);
}

// Vote families in order; the label a voting column propagates.
#define N_FAM 4
static const int FAM_LABEL[N_FAM] = {PII_PERSON, PII_EMAIL, PII_PHONE, PII_USERNAME};
static int vote_family(int label) {
  return label == PII_PERSON || label == PII_PERSON_L2 ? 0 : label == PII_EMAIL ? 1 : label == PII_PHONE ? 2 : label == PII_USERNAME ? 3 : -1;
}

static int is_digit(uint32_t c) {
  static const uint32_t zero[] = {0x30,  0x660, 0x6f0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66,  0xbe6,  0xc66,
                                  0xce6, 0xd66, 0xe50, 0xed0, 0xf20, 0x1040, 0x17e0, 0x1810, 0xff10};
  for (size_t i = 0; i < sizeof zero / sizeof zero[0]; i++)
    if (c >= zero[i] && c <= zero[i] + 9) return 1;
  return 0;
}

// The trimmed value cp[a, b), lowercased in ASCII, is a placeholder ("n/a", "-", "없음", ...).
static int placeholder(const uint32_t *cp, size_t a, size_t b) {
  static const uint32_t words[][5] = {{'n', '/', 'a'}, {'n', 'a'}, {'-'}, {'-', '-'}, {0x2014}, {0x2013},
                                      {'n', 'o', 'n', 'e'}, {'n', 'u', 'l', 'l'}, {0xc5c6, 0xc74c}};
  for (size_t w = 0; w < sizeof words / sizeof words[0]; w++) {
    size_t k = 0;
    while (k < 5 && words[w][k]) k++;
    if (k != b - a) continue;
    size_t i = 0;
    for (; i < k; i++) {
      uint32_t c = cp[a + i];
      if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
      if (c != words[w][i]) break;
    }
    if (i == k) return 1;
  }
  return 0;
}

static int cmp_size(const void *a, const void *b) {
  const size_t x = *(const size_t *)a, y = *(const size_t *)b;
  return x < y ? -1 : x > y;
}

// Column vote (Step 5): appends TABLE spans to prop. clip/clip_cell: the clipped spans and their cells.
static int column_vote(const pii_text *tx, const pii_cell *c, size_t nc, const pii_cspan *clip, const size_t *clip_cell,
                       size_t nclip, int header, uint32_t labels, pii_vec *prop) {
  const uint32_t *cp = tx->cp;
  const uint32_t first_row = header ? 1 : 0;
  uint32_t ncol = 0;
  for (size_t i = 0; i < nc; i++)
    if (c[i].col + 1 > ncol) ncol = c[i].col + 1;
  uint32_t *cov = calloc(nc * N_FAM, sizeof *cov);
  size_t *start = calloc((size_t)ncol + 2, sizeof *start), *idx = malloc(nc * sizeof *idx), *len = malloc(nc * sizeof *len),
         *words = malloc(nc * sizeof *words);
  int rc = cov && start && idx && len && words ? PII_OK : PII_ERR_NOMEM;
  if (rc) goto done;
  for (size_t k = 0; k < nclip; k++) {
    const int f = vote_family(clip[k].label);
    if (f >= 0) cov[clip_cell[k] * N_FAM + (size_t)f] += (uint32_t)non_space(cp, clip[k].start, clip[k].end);
  }
  // non-empty data cells grouped by column (counting sort, row order kept)
  for (size_t i = 0; i < nc; i++) {
    size_t a = c[i].start, b = c[i].end;
    trim(cp, &a, &b);
    if (c[i].row >= first_row && a < b) start[c[i].col + 2]++;
  }
  for (uint32_t k = 0; k < ncol; k++) start[k + 2] += start[k + 1];
  for (size_t i = 0; i < nc; i++) {
    size_t a = c[i].start, b = c[i].end;
    trim(cp, &a, &b);
    if (c[i].row >= first_row && a < b) idx[start[c[i].col + 1]++] = i;
  }
  for (uint32_t col = 0; col < ncol; col++) {
    const size_t *cells = idx + start[col], m = start[col + 1] - start[col];
    if (m < 5) continue;
    size_t support[N_FAM] = {0};
    for (size_t q = 0; q < m; q++) {
      size_t a = c[cells[q]].start, b = c[cells[q]].end;
      trim(cp, &a, &b);
      len[q] = b - a;
      words[q] = 0;
      for (size_t i = a; i < b; i++) words[q] += !pii_is_js_space(cp[i]) && (i == a || pii_is_js_space(cp[i - 1]));
      const uint32_t *cv = cov + cells[q] * N_FAM;
      int best = 0;
      for (int f = 1; f < N_FAM; f++)
        if (cv[f] > cv[best]) best = f;
      if (cv[best] && 5 * (size_t)cv[best] >= 4 * non_space(cp, a, b)) support[best]++;
    }
    qsort(len, m, sizeof *len, cmp_size);
    qsort(words, m, sizeof *words, cmp_size);
    // median <= 40 and <= 4: the sum of the two middle values <= 80 and <= 8 (free-text columns never vote)
    if (len[(m - 1) / 2] + len[m / 2] > 80 || words[(m - 1) / 2] + words[m / 2] > 8) continue;
    int fam = 0;
    for (int f = 1; f < N_FAM; f++)
      if (support[f] > support[fam]) fam = f;
    if (support[fam] < 4 || 5 * support[fam] < 4 * m || !(labels >> FAM_LABEL[fam] & 1)) continue;
    for (size_t q = 0; q < m; q++) {
      size_t a = c[cells[q]].start, b = c[cells[q]].end;
      trim(cp, &a, &b);
      if (placeholder(cp, a, b)) continue;
      if (FAM_LABEL[fam] == PII_EMAIL) {
        int at = 0;
        for (size_t i = a; i < b; i++) at |= cp[i] == '@' || cp[i] == 0xff20;
        if (!at) continue;
      }
      if (FAM_LABEL[fam] == PII_PHONE) {
        size_t digits = 0;
        for (size_t i = a; i < b; i++) digits += is_digit(cp[i]);
        if (digits < 6) continue;
      }
      pii_cspan *x = pii_vec_push(prop);
      if (!x) {
        rc = PII_ERR_NOMEM;
        goto done;
      }
      x->start = a;
      x->end = b;
      x->label = FAM_LABEL[fam];
      x->source = PII_SOURCE_TABLE;
    }
  }
done:
  free(cov);
  free(start);
  free(idx);
  free(len);
  free(words);
  return rc;
}

int pii_table_apply(const pii_text *tx, const pii_cspan *spans, size_t n, int delimiter, int header, int vote, uint32_t labels,
                    pii_cspan **out, size_t *n_out) {
  return pii_table_apply_ext(tx, spans, n, NULL, 0, delimiter, header, vote, labels, out, n_out);
}

int pii_table_apply_ext(const pii_text *tx, const pii_cspan *spans, size_t n, const pii_cspan *extra, size_t n_extra, int delimiter,
                        int header, int vote, uint32_t labels, pii_cspan **out, size_t *n_out) {
  *out = NULL;
  *n_out = 0;
  pii_vec cells, all, clip_cell;
  pii_vec_init(&cells, sizeof(pii_cell));
  pii_vec_init(&all, sizeof(pii_cspan));
  pii_vec_init(&clip_cell, sizeof(size_t));
  size_t rows = 0, nres = 0;
  int used;
  pii_cspan *res = NULL;
  int rc = pii_table_parse(tx, delimiter, &cells, &rows, &used);
  if (rc) goto done;
  if (rows < 2) {  // not a table: detect's spans
    if (n && !(res = malloc(n * sizeof *res))) {
      rc = PII_ERR_NOMEM;
      goto done;
    }
    if (n) memcpy(res, spans, n * sizeof *res);
    nres = n;
    goto snap;
  }
  // Step 4: spans clipped to cell values (cells are in text order and do not overlap, so their ends increase too)
  const pii_cell *c = cells.p;
  const size_t nc = cells.n;
  for (size_t i = 0, j0 = 0; i < n; i++) {
    while (j0 < nc && c[j0].end <= spans[i].start) j0++;
    for (size_t j = j0; j < nc && c[j].start < spans[i].end; j++) {
      const size_t a = spans[i].start > c[j].start ? spans[i].start : c[j].start, b = spans[i].end < c[j].end ? spans[i].end : c[j].end;
      if (a >= b || !non_space(tx->cp, a, b)) continue;
      pii_cspan *x = pii_vec_push(&all);
      size_t *xc = pii_vec_push(&clip_cell);
      if (!x || !xc) {
        rc = PII_ERR_NOMEM;
        goto done;
      }
      *x = spans[i];
      x->start = a;
      x->end = b;
      *xc = j;
    }
  }
  // Step 5: column vote; the vote counts only the clipped spans (appending may move all.p, so vote on a copy)
  const size_t nclip = all.n;
  if (vote && nclip) {
    pii_cspan *clip = malloc(nclip * sizeof *clip);
    if (!clip) {
      rc = PII_ERR_NOMEM;
      goto done;
    }
    memcpy(clip, all.p, nclip * sizeof *clip);
    rc = column_vote(tx, c, nc, clip, clip_cell.p, nclip, header, labels, &all);
    free(clip);
    if (rc) goto done;
  }
  for (size_t i = 0; i < n_extra; i++) {  // header context (pii_detect_table), after the vote so it never votes
    pii_cspan *x = pii_vec_push(&all);
    if (!x) {
      rc = PII_ERR_NOMEM;
      goto done;
    }
    *x = extra[i];
  }
  // Step 6: overlapping spans -> one; a TABLE span's label if any (the first by start), else the longest (code points)
  pii_cspan *s = all.p;
  sort_spans(s, all.n, cmp_start, NULL);  // stable: clipped spans before propagated ones at the same start
  if (all.n && !(res = malloc(all.n * sizeof *res))) {
    rc = PII_ERR_NOMEM;
    goto done;
  }
  for (size_t g0 = 0; g0 < all.n;) {
    size_t g1 = g0 + 1, end = s[g0].end, pick = g0;
    int table = s[g0].source == PII_SOURCE_TABLE;
    for (; g1 < all.n && s[g1].start < end; g1++) {
      if (s[g1].end > end) end = s[g1].end;
      if (table) continue;
      if (s[g1].source == PII_SOURCE_TABLE) table = 1, pick = g1;
      else if (s[g1].end - s[g1].start > s[pick].end - s[pick].start) pick = g1;
    }
    res[nres] = s[pick];
    res[nres].start = s[g0].start;
    res[nres].end = end;
    nres++;
    g0 = g1;
  }
snap:
  nres = pii_snap_graphemes(res, nres, tx->cp, tx->n, rows < 2 ? NULL : cells.p, cells.n);  // Step 7, inside each cell
  if (nres) *out = res;
  else free(res);
  *n_out = nres;
done:
  if (rc) free(res);
  pii_vec_free(&cells);
  pii_vec_free(&all);
  pii_vec_free(&clip_cell);
  return rc;
}

// ---------------------------------------------------------------- detect
typedef struct {
  pii_vec ids, st, en;  // int32_t
} tok_list;

static void tok_list_free(tok_list *t) {
  pii_vec_free(&t->ids);
  pii_vec_free(&t->st);
  pii_vec_free(&t->en);
}

static int tokenize(const pii_model *m, const pii_text *tx, tok_list *tk) {
  pii_vec_init(&tk->ids, sizeof(int32_t));
  pii_vec_init(&tk->st, sizeof(int32_t));
  pii_vec_init(&tk->en, sizeof(int32_t));
  return pii_tok_encode(m->tok, tx->cp, tx->n, &tk->ids, &tk->st, &tk->en) ? PII_ERR_NOMEM : PII_OK;
}

// Model spans (decode.ts: makeWindows, bestTags, decodeSpans) in code points; appended to spans.
static int model_spans(const pii_model *m, const pii_text *tx, double o_bias, pii_vec *spans) {
  tok_list tk;
  int rc = tokenize(m, tx, &tk);
  const size_t n = tk.ids.n;
  if (rc || !n) {
    tok_list_free(&tk);
    return rc;
  }
  const int32_t *ids = tk.ids.p, *st = tk.st.p, *en = tk.en.p;
  // windows of PII_WINDOW tokens (with <s> and </s>) overlapping by PII_STRIDE
  const size_t win = PII_WINDOW - 2;
  int32_t *tag = calloc(n, sizeof *tag), *dist = malloc(n * sizeof *dist), *wids = malloc((win + 2) * sizeof *wids);
  pii_ws ws = {0};
  if (!tag || !dist || !wids || pii_ws_init(&ws, &m->eng, (int)((n < win ? n : win) + 2))) {
    rc = PII_ERR_NOMEM;
    goto done;
  }
  for (size_t i = 0; i < n; i++) dist[i] = -1;
  const size_t limit = n > PII_STRIDE + 1 ? n - PII_STRIDE : 1;
  for (size_t s = 0; s == 0 || (n > win && s < limit); s += win - PII_STRIDE) {
    const size_t wn = n - s < win ? n - s : win;
    wids[0] = pii_tok_bos(m->tok);
    memcpy(wids + 1, ids + s, wn * sizeof *wids);
    wids[wn + 1] = pii_tok_eos(m->tok);
    const float *lg = pii_engine_run(&m->eng, &ws, wids, (int)wn + 2);
    if (!lg) {
      rc = PII_ERR_MODEL;
      goto done;
    }
    // per token: best tag; where windows overlap, the window in which the token sits farther from the edge wins
    for (size_t j = 0; j < wn; j++) {
      const float *row = lg + (j + 1) * N_TAGS;
      int best = 0;
      double best_v = (double)row[0] + o_bias;
      for (int t = 1; t < N_TAGS; t++)
        if ((double)row[t] > best_v) {
          best_v = row[t];
          best = t;
        }
      const int32_t c = (int32_t)(j < wn - 1 - j ? j : wn - 1 - j);
      if (c > dist[s + j]) {
        dist[s + j] = c;
        tag[s + j] = best;
      }
    }
    if (n <= win) break;
  }
  // BIO tags -> spans
  const size_t first = spans->n;
  pii_cspan *cur = NULL;
  for (size_t j = 0; j < n; j++) {
    if (st[j] == en[j]) continue;
    const int t = tag[j];
    if (t == 0) {
      cur = NULL;
      continue;
    }
    const int label = (t - 1) / 2, is_b = (t - 1) % 2 == 0;
    if (cur && cur->label == label && !is_b) cur->end = (size_t)en[j];
    else {
      cur = pii_vec_push(spans);
      if (!cur) {
        rc = PII_ERR_NOMEM;
        goto done;
      }
      cur->start = (size_t)st[j];
      cur->end = (size_t)en[j];
      cur->label = label;
      cur->source = PII_SOURCE_MODEL;
    }
  }
  spans->n = first + merge_adjacent((pii_cspan *)spans->p + first, spans->n - first, tx);
done:
  pii_ws_free(&ws);
  free(tag);
  free(dist);
  free(wids);
  tok_list_free(&tk);
  return rc;
}

// Options with defaults for the fields an older caller's struct lacks; PII_ERR_ARG when malformed.
static int read_options(const pii_options *opt, pii_options *o) {
  pii_options_init(o);
  if (opt) {
    if (opt->struct_size < offsetof(pii_options, extra_unit) + sizeof(int)) return PII_ERR_ARG;
    memcpy(o, opt, sizeof *o < opt->struct_size ? sizeof *o : opt->struct_size);
  }
  if (o->n_extra && !o->extra) return PII_ERR_ARG;
  return PII_OK;
}

// Emoji runs: an Extended_Pictographic or Regional_Indicator code point and the emoji code points after it (pictographs,
// regional indicators, ZWJ, variation selectors, skin tones, tag characters, the keycap mark). A keycap (a digit, '#' or '*',
// an optional U+FE0F, U+20E3) is read as its bare character: the marks are taken out and given back to any span that covers
// the character. Same rule as stripEmoji / restoreSpans in js/src/emoji.ts.
static int emoji_base(uint32_t c) { return (c >= 0x1f1e6 && c <= 0x1f1ff) || pii_is_pictographic(c); }
static int keycap_base(uint32_t c) { return (c >= '0' && c <= '9') || c == '#' || c == '*'; }
static int keycap_mark(uint32_t c) { return c == 0xfe0f || c == 0x20e3; }
static int emoji_part(uint32_t c) {
  return emoji_base(c) || c == 0x200d || c == 0xfe0e || c == 0xfe0f || c == 0x20e3 || (c >= 0x1f3fb && c <= 0x1f3ff) ||
         (c >= 0xe0020 && c <= 0xe007f);
}

// model_spans on tx without its emoji runs and keycap marks. Spans come back in tx's offsets, split where a run fell inside one
// (keycap marks never split: a span over a keycap's character covers its marks too); at a split the pieces lose the
// JavaScript white space next to the run, and a piece left empty is dropped. Text without emoji or keycaps: model_spans.
static int marks_only(const uint32_t *cp, size_t a, size_t b) {
  for (size_t i = a; i < b; i++)
    if (!keycap_mark(cp[i])) return 0;
  return 1;
}

static int model_spans_no_emoji(const pii_model *m, const pii_text *tx, double o_bias, pii_vec *spans) {
  size_t *keep = malloc((tx->n + 1) * sizeof *keep);
  uint32_t *cp = malloc((tx->n + 1) * sizeof *cp);
  if (!keep || !cp) {
    free(keep);
    free(cp);
    return PII_ERR_NOMEM;
  }
  size_t nk = 0;
  for (size_t i = 0; i < tx->n;) {
    if (emoji_base(tx->cp[i])) {
      for (i++; i < tx->n && emoji_part(tx->cp[i]); i++) {}
      continue;
    }
    size_t j = i + 1;
    if (keycap_base(tx->cp[i]) && j < tx->n && tx->cp[j] == 0xfe0f) j++;
    if (keycap_base(tx->cp[i]) && j < tx->n && tx->cp[j] == 0x20e3) {
      keep[nk] = i;
      cp[nk++] = tx->cp[i];
      i = j + 1;
      continue;
    }
    keep[nk] = i;
    cp[nk++] = tx->cp[i++];
  }
  int rc;
  if (nk == tx->n) rc = model_spans(m, tx, o_bias, spans);
  else {
    const pii_text sub = {cp, nk, NULL, NULL};
    pii_vec raw;
    pii_vec_init(&raw, sizeof(pii_cspan));
    rc = model_spans(m, &sub, o_bias, &raw);
    const pii_cspan *r = raw.p;
    for (size_t i = 0; !rc && i < raw.n; i++) {
      for (size_t k = r[i].start; k < r[i].end;) {
        size_t e = k + 1;
        while (e < r[i].end && marks_only(tx->cp, keep[e - 1] + 1, keep[e])) e++;
        size_t a = keep[k], b = keep[e - 1] + 1;
        const size_t next = e < nk ? keep[e] : tx->n;  // keycap marks after the piece's last character belong to it
        while (b < next && keycap_mark(tx->cp[b])) b++;
        if (k > r[i].start)
          while (a < b && pii_is_js_space(tx->cp[a])) a++;
        if (e < r[i].end)
          while (b > a && pii_is_js_space(tx->cp[b - 1])) b--;
        if (a < b) {
          pii_cspan *c = pii_vec_push(spans);
          if (!c) {
            rc = PII_ERR_NOMEM;
            break;
          }
          *c = r[i];
          c->start = a;
          c->end = b;
        }
        k = e;
      }
    }
    pii_vec_free(&raw);
  }
  free(keep);
  free(cp);
  return rc;
}

// Model and extra spans of tx, combined (sorted, non-overlapping, before grapheme snapping) into *comb (malloc'ed, *n).
static int detect_spans(const pii_model *m, const pii_text *tx, const pii_options *o, pii_cspan **comb, size_t *n) {
  *comb = NULL;
  *n = 0;
  pii_vec spans;
  pii_vec_init(&spans, sizeof(pii_cspan));
  int rc = o->keep_emoji ? model_spans(m, tx, -o->sensitivity, &spans) : model_spans_no_emoji(m, tx, -o->sensitivity, &spans);
  for (size_t i = 0; !rc && i < o->n_extra; i++) {
    const pii_extra_span *x = &o->extra[i];
    const size_t *units = o->extra_unit == PII_UNIT_UTF8 ? tx->u8 : o->extra_unit == PII_UNIT_UTF16 ? tx->u16 : NULL;
    if ((o->extra_unit != PII_UNIT_UTF8 && o->extra_unit != PII_UNIT_CODEPOINT && o->extra_unit != PII_UNIT_UTF16) ||
        x->label < 0 || x->label >= PII_LABEL_COUNT || x->start > x->end || x->end > (units ? units[tx->n] : tx->n)) {
      rc = PII_ERR_ARG;
      break;
    }
    pii_cspan *c = pii_vec_push(&spans);
    if (!c) {
      rc = PII_ERR_NOMEM;
      break;
    }
    c->start = units ? to_cp(units, tx->n, x->start) : x->start;
    c->end = units ? to_cp(units, tx->n, x->end) : x->end;
    if (units && units[c->end] < x->end) c->end++;  // an end inside a code point covers it
    c->label = x->label;
    c->source = PII_SOURCE_REGEX;
  }
  if (!rc && spans.n) {
    *comb = malloc(spans.n * sizeof **comb);
    if (!*comb) rc = PII_ERR_NOMEM;
    else *n = pii_combine(spans.p, spans.n, o->labels, tx->u16, *comb);
  }
  pii_vec_free(&spans);
  return rc;
}

// Code point spans -> public spans with all three offsets.
static int emit(const pii_text *tx, const pii_cspan *s, size_t n, pii_span **out, size_t *n_out) {
  if (!n) return PII_OK;
  pii_span *res = calloc(n, sizeof *res);
  if (!res) return PII_ERR_NOMEM;
  for (size_t i = 0; i < n; i++) {
    const size_t a = s[i].start, b = s[i].end;
    res[i].cp_start = a;
    res[i].cp_end = b;
    res[i].byte_start = tx->u8[a];
    res[i].byte_end = tx->u8[b];
    res[i].u16_start = tx->u16[a];
    res[i].u16_end = tx->u16[b];
    res[i].label = s[i].label;
    res[i].label_name = LABEL_NAMES[s[i].label];
    res[i].source = s[i].source;
  }
  *out = res;
  *n_out = n;
  return PII_OK;
}

int pii_detect(const pii_model *m, const char *utf8, size_t len, const pii_options *opt, pii_span **out, size_t *n_out) {
  if (!m || !out || !n_out || (!utf8 && len)) return PII_ERR_ARG;
  *out = NULL;
  *n_out = 0;
  pii_options o;
  if (read_options(opt, &o) || len > (size_t)INT32_MAX) return PII_ERR_ARG;
  pii_text tx;
  if (pii_text_init(&tx, utf8 ? utf8 : "", len)) return PII_ERR_NOMEM;
  pii_cspan *comb;
  size_t n;
  int rc = detect_spans(m, &tx, &o, &comb, &n);
  if (!rc) rc = emit(&tx, comb, pii_snap_graphemes(comb, n, tx.cp, tx.n, NULL, 0), out, n_out);
  free(comb);
  pii_text_free(&tx);
  return rc;
}

// Header context (pii_table_options.context): the spans of "<header>: <value>" for each non-empty data cell of a column whose
// non-empty values are at least 80% distinct (values compared trimmed, ASCII letters folded), moved into tx's offsets and
// clipped to the cell; appended to out. Same as headerContextSpans in js/src/redactor.ts.
static uint64_t value_key(const uint32_t *cp, size_t a, size_t b) {
  uint64_t h = 1469598103934665603ull;  // FNV-1a over code points
  for (size_t i = a; i < b; i++) {
    uint32_t c = cp[i];
    if (c >= 'A' && c <= 'Z') c += 32;
    for (int k = 0; k < 4; k++) h = (h ^ ((c >> (8 * k)) & 0xff)) * 1099511628211ull;
  }
  return h;
}

static int cmp_u64(const void *x, const void *y) {
  const uint64_t a = *(const uint64_t *)x, b = *(const uint64_t *)y;
  return a < b ? -1 : a > b;
}

static int header_context_spans(const pii_model *m, const pii_text *tx, const char *utf8, const pii_options *o, int delimiter,
                                pii_vec *out) {
  pii_vec cells;
  pii_vec_init(&cells, sizeof(pii_cell));
  size_t rows = 0;
  int used;
  int rc = pii_table_parse(tx, delimiter, &cells, &rows, &used);
  const pii_cell *c = cells.p;
  uint32_t ncol = 0;
  for (size_t i = 0; i < cells.n; i++)
    if (c[i].col + 1 > ncol) ncol = c[i].col + 1;
  size_t *hdr = NULL;
  uint64_t *keys = NULL;
  unsigned char *ok = NULL;
  char *buf = NULL;
  if (rc || rows < 2 || !ncol) goto done;
  hdr = malloc(ncol * sizeof *hdr);
  keys = malloc(cells.n * sizeof *keys);
  ok = calloc(ncol, 1);
  buf = malloc(tx->u8[tx->n] + 3);
  if (!hdr || !keys || !ok || !buf) {
    rc = PII_ERR_NOMEM;
    goto done;
  }
  for (uint32_t col = 0; col < ncol; col++) hdr[col] = SIZE_MAX;
  for (size_t i = 0; i < cells.n; i++)
    if (c[i].row == 0) hdr[c[i].col] = i;
  for (uint32_t col = 0; col < ncol; col++) {
    size_t k = 0;
    for (size_t i = 0; i < cells.n; i++) {
      if (c[i].row == 0 || c[i].col != col) continue;
      size_t a = c[i].start, b = c[i].end;
      trim(tx->cp, &a, &b);
      if (a < b) keys[k++] = value_key(tx->cp, a, b);
    }
    if (!k) continue;
    qsort(keys, k, sizeof *keys, cmp_u64);
    size_t distinct = 1;
    for (size_t i = 1; i < k; i++) distinct += keys[i] != keys[i - 1];
    ok[col] = 5 * distinct >= 4 * k;
  }
  pii_options o2 = *o;
  o2.extra = NULL;
  o2.n_extra = 0;
  for (size_t i = 0; !rc && i < cells.n; i++) {
    if (c[i].row == 0 || !ok[c[i].col]) continue;
    size_t va = c[i].start, vb = c[i].end;
    trim(tx->cp, &va, &vb);
    if (va == vb) continue;
    size_t ha = 0, hb = 0, len = 0, plen = 0;
    if (hdr[c[i].col] != SIZE_MAX) {
      ha = c[hdr[c[i].col]].start;
      hb = c[hdr[c[i].col]].end;
      trim(tx->cp, &ha, &hb);
    }
    if (ha < hb) {
      memcpy(buf, utf8 + tx->u8[ha], tx->u8[hb] - tx->u8[ha]);
      len = tx->u8[hb] - tx->u8[ha];
      memcpy(buf + len, ": ", 2);
      len += 2;
      plen = hb - ha + 2;
    }
    const size_t ca = c[i].start, cb = c[i].end;
    memcpy(buf + len, utf8 + tx->u8[ca], tx->u8[cb] - tx->u8[ca]);
    len += tx->u8[cb] - tx->u8[ca];
    pii_text sub;
    if (pii_text_init(&sub, buf, len)) {
      rc = PII_ERR_NOMEM;
      break;
    }
    pii_cspan *sp;
    size_t ns;
    rc = detect_spans(m, &sub, &o2, &sp, &ns);
    for (size_t j = 0; !rc && j < ns; j++) {
      if (sp[j].end <= plen) continue;
      const size_t a = ca + (sp[j].start > plen ? sp[j].start - plen : 0), e = ca + sp[j].end - plen, b = e < cb ? e : cb;
      if (a >= b) continue;
      pii_cspan *x = pii_vec_push(out);
      if (!x) {
        rc = PII_ERR_NOMEM;
        break;
      }
      *x = sp[j];
      x->start = a;
      x->end = b;
    }
    free(sp);
    pii_text_free(&sub);
  }
done:
  free(hdr);
  free(keys);
  free(ok);
  free(buf);
  pii_vec_free(&cells);
  return rc;
}

void pii_table_options_init(pii_table_options *t) {
  if (!t) return;
  memset(t, 0, sizeof *t);
  t->struct_size = sizeof *t;
  t->header = 1;
  t->vote = 1;
}

int pii_detect_table(const pii_model *m, const char *utf8, size_t len, const pii_options *opt, const pii_table_options *table,
                     pii_span **out, size_t *n_out) {
  if (!m || !out || !n_out || (!utf8 && len)) return PII_ERR_ARG;
  *out = NULL;
  *n_out = 0;
  pii_options o;
  if (read_options(opt, &o) || len > (size_t)INT32_MAX) return PII_ERR_ARG;
  pii_table_options t;
  pii_table_options_init(&t);
  if (table) {
    if (table->struct_size < offsetof(pii_table_options, vote) + sizeof(int)) return PII_ERR_ARG;
    memcpy(&t, table, sizeof t < table->struct_size ? sizeof t : table->struct_size);
  }
  if (t.delimiter != 0 && t.delimiter != ',' && t.delimiter != '\t' && t.delimiter != ';' && t.delimiter != '|') return PII_ERR_ARG;
  if (t.context != PII_TABLE_CONTEXT_NONE && t.context != PII_TABLE_CONTEXT_HEADER) return PII_ERR_ARG;
  pii_text tx;
  if (pii_text_init(&tx, utf8 ? utf8 : "", len)) return PII_ERR_NOMEM;
  pii_cspan *comb, *res = NULL;
  size_t n, nres = 0;
  pii_vec ctx;
  pii_vec_init(&ctx, sizeof(pii_cspan));
  int rc = detect_spans(m, &tx, &o, &comb, &n);
  if (!rc && t.context == PII_TABLE_CONTEXT_HEADER && t.header) rc = header_context_spans(m, &tx, utf8 ? utf8 : "", &o, t.delimiter, &ctx);
  if (!rc)
    rc = pii_table_apply_ext(&tx, comb, n, ctx.p, ctx.n, t.delimiter, t.header != 0, t.vote != 0, o.labels, &res, &nres);
  pii_vec_free(&ctx);
  if (!rc) rc = emit(&tx, res, nres, out, n_out);
  free(res);
  free(comb);
  pii_text_free(&tx);
  return rc;
}

void pii_spans_free(pii_span *spans) { free(spans); }

int pii_tokenize(const pii_model *m, const char *utf8, size_t len, pii_token **out, size_t *n_out) {
  if (!m || !out || !n_out || (!utf8 && len)) return PII_ERR_ARG;
  *out = NULL;
  *n_out = 0;
  if (len > (size_t)INT32_MAX) return PII_ERR_ARG;
  pii_text tx;
  if (pii_text_init(&tx, utf8 ? utf8 : "", len)) return PII_ERR_NOMEM;
  tok_list tk;
  int rc = tokenize(m, &tx, &tk);
  if (!rc && tk.ids.n) {
    pii_token *res = calloc(tk.ids.n, sizeof *res);
    if (!res) rc = PII_ERR_NOMEM;
    else {
      const int32_t *ids = tk.ids.p, *st = tk.st.p, *en = tk.en.p;
      for (size_t i = 0; i < tk.ids.n; i++) {
        res[i].id = ids[i];
        res[i].cp_start = (size_t)st[i];
        res[i].cp_end = (size_t)en[i];
        res[i].byte_start = tx.u8[st[i]];
        res[i].byte_end = tx.u8[en[i]];
        res[i].u16_start = tx.u16[st[i]];
        res[i].u16_end = tx.u16[en[i]];
      }
      *out = res;
      *n_out = tk.ids.n;
    }
  }
  tok_list_free(&tk);
  pii_text_free(&tx);
  return rc;
}

void pii_tokens_free(pii_token *tokens) { free(tokens); }
