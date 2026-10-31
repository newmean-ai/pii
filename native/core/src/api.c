// Public API: loading, detect (windows, best tag per token, BIO spans, merging; port of js/src/decode.ts and the
// combine step of js/src/redactor.ts) and tokenize.
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

int pii_detect(const pii_model *m, const char *utf8, size_t len, const pii_options *opt, pii_span **out, size_t *n_out) {
  if (!m || !out || !n_out || (!utf8 && len)) return PII_ERR_ARG;
  *out = NULL;
  *n_out = 0;
  pii_options o;
  pii_options_init(&o);
  if (opt) {
    if (opt->struct_size < offsetof(pii_options, extra_unit) + sizeof(int)) return PII_ERR_ARG;
    memcpy(&o, opt, sizeof o < opt->struct_size ? sizeof o : opt->struct_size);
  }
  if (len > (size_t)INT32_MAX) return PII_ERR_ARG;
  if (o.n_extra && !o.extra) return PII_ERR_ARG;
  pii_text tx;
  if (pii_text_init(&tx, utf8 ? utf8 : "", len)) return PII_ERR_NOMEM;
  pii_vec spans;
  pii_vec_init(&spans, sizeof(pii_cspan));
  int rc = model_spans(m, &tx, -o.sensitivity, &spans);
  pii_cspan *comb = NULL;
  for (size_t i = 0; !rc && i < o.n_extra; i++) {
    const pii_extra_span *x = &o.extra[i];
    const size_t *units = o.extra_unit == PII_UNIT_UTF8 ? tx.u8 : o.extra_unit == PII_UNIT_UTF16 ? tx.u16 : NULL;
    if ((o.extra_unit != PII_UNIT_UTF8 && o.extra_unit != PII_UNIT_CODEPOINT && o.extra_unit != PII_UNIT_UTF16) ||
        x->label < 0 || x->label >= PII_LABEL_COUNT || x->start > x->end || x->end > (units ? units[tx.n] : tx.n)) {
      rc = PII_ERR_ARG;
      break;
    }
    pii_cspan *c = pii_vec_push(&spans);
    if (!c) {
      rc = PII_ERR_NOMEM;
      break;
    }
    c->start = units ? to_cp(units, tx.n, x->start) : x->start;
    c->end = units ? to_cp(units, tx.n, x->end) : x->end;
    if (units && units[c->end] < x->end) c->end++;  // an end inside a code point covers it
    c->label = x->label;
    c->source = PII_SOURCE_REGEX;
  }
  size_t n = 0;
  if (!rc && spans.n) {
    comb = malloc(spans.n * sizeof *comb);
    if (!comb) rc = PII_ERR_NOMEM;
    else n = pii_combine(spans.p, spans.n, o.labels, tx.u16, comb);
  }
  if (!rc && n) {
    pii_span *res = calloc(n, sizeof *res);
    if (!res) rc = PII_ERR_NOMEM;
    else {
      for (size_t i = 0; i < n; i++) {
        const size_t s = comb[i].start, e = comb[i].end;
        res[i].cp_start = s;
        res[i].cp_end = e;
        res[i].byte_start = tx.u8[s];
        res[i].byte_end = tx.u8[e];
        res[i].u16_start = tx.u16[s];
        res[i].u16_end = tx.u16[e];
        res[i].label = comb[i].label;
        res[i].label_name = LABEL_NAMES[comb[i].label];
        res[i].source = comb[i].source;
      }
      *out = res;
      *n_out = n;
    }
  }
  free(comb);
  pii_vec_free(&spans);
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
