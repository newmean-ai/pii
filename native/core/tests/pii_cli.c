// Test and benchmark CLI for the core. Reads JSON lines with a "text" field on stdin, writes one JSON line per input.
//
//   pii_cli tokenize MODEL.onnx TOKENIZER.json            {"ids": [...], "cp": [[s, e], ...], "u16": [...], "u8": [...]}
//   pii_cli detect MODEL.onnx TOKENIZER.json [SENS]       {"spans": [[cp_s, cp_e, "LABEL", u16_s, u16_e, u8_s, u8_e], ...]}
//   pii_cli table MODEL.onnx TOKENIZER.json [SENS]        pii_detect_table: the same with the source bits appended to each span
//     Optional fields of an input line (detect and table): "extra": [[cp_s, cp_e, "LABEL"], ...] (pii_options.extra in
//     code points), "labels": ["PERSON", ...]; table only: "delimiter": "auto" | "," | "\t" | ";" | "markdown",
//     "header": bool, "vote": bool, "context": "none" | "header".
//   pii_cli apply                                         pii_table_apply without a model (parity of the table steps): each
//     line also has "spans": [[cp_s, cp_e, "LABEL", source], ...] (sorted, non-overlapping, as detect gives them before
//     snapping); {"spans": [[cp_s, cp_e, "LABEL", source], ...]} out
//   pii_cli threads MODEL.onnx TOKENIZER.json N [SENS]    every text in N threads at once; "ok" when all equal one thread
//   pii_cli bench MODEL.onnx TOKENIZER.json               ms per text (all inputs, inputs of <= 64 tokens, a 1,000-token text)
//   pii_cli verify FILE SIZE KIND                         checks FILE against the pinned size and SHA-256
//   pii_cli info                                          {"version": ..., "build": "neon", "fma": false}
//   pii_cli selftest [MODEL.onnx TOKENIZER.json]          API checks (combine as js/test/combine.test.ts, grapheme snapping,
//                                                         table parsing, clipping, vote and merge, arguments, table mode
//                                                         with a model, malformed and truncated model files); "ok" or the
//                                                         failures
//   pii_cli fuzz MODEL.onnx TOKENIZER.json N             N mutated model files, N / 2 tokenizer files, 2N random byte strings
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../pii.h"
#include "../src/internal.h"

static uint8_t *read_file(const char *path, size_t *n) {
  FILE *f = fopen(path, "rb");
  if (!f) return NULL;
  fseek(f, 0, SEEK_END);
  long sz = ftell(f);
  fseek(f, 0, SEEK_SET);
  uint8_t *b = malloc(sz > 0 ? (size_t)sz : 1);
  if (b && fread(b, 1, (size_t)sz, f) != (size_t)sz) {
    free(b);
    b = NULL;
  }
  fclose(f);
  *n = (size_t)sz;
  return b;
}

typedef struct {
  char *s;
  size_t n;
  pii_extra_span *extra;  // code points
  size_t n_extra;
  uint32_t labels;
  int delimiter, header, vote, context;
  pii_cspan *spans;  // "spans" (pii_cli apply), code points
  size_t n_spans;
} input;

// Optional fields of an input line (see the top of this file).
static void read_line_options(const pii_json *j, input *in) {
  in->extra = NULL;
  in->n_extra = 0;
  in->labels = PII_LABELS_ALL;
  in->delimiter = 0;
  in->header = in->vote = 1;
  in->context = 0;
  in->spans = NULL;
  in->n_spans = 0;
  const pii_json *v = pii_json_get(j, "spans");
  if (v && v->type == PJ_ARR && v->n) {
    in->spans = calloc(v->n, sizeof *in->spans);
    for (size_t i = 0; i < v->n; i++) {
      const pii_json *x = &v->items[i];
      if (x->type != PJ_ARR || x->n != 4 || x->items[2].type != PJ_STR) continue;
      in->spans[in->n_spans++] = (pii_cspan){(size_t)x->items[0].num, (size_t)x->items[1].num, pii_label_from_name(x->items[2].str),
                                             (int)x->items[3].num};
    }
  }
  v = pii_json_get(j, "extra");
  if (v && v->type == PJ_ARR && v->n) {
    in->extra = calloc(v->n, sizeof *in->extra);
    for (size_t i = 0; i < v->n; i++) {
      const pii_json *x = &v->items[i];
      if (x->type != PJ_ARR || x->n != 3 || x->items[2].type != PJ_STR) continue;
      in->extra[in->n_extra].start = (size_t)x->items[0].num;
      in->extra[in->n_extra].end = (size_t)x->items[1].num;
      in->extra[in->n_extra].label = pii_label_from_name(x->items[2].str);
      in->n_extra++;
    }
  }
  if ((v = pii_json_get(j, "labels")) && v->type == PJ_ARR) {
    in->labels = 0;
    for (size_t i = 0; i < v->n; i++) {
      const int l = v->items[i].type == PJ_STR ? pii_label_from_name(v->items[i].str) : -1;
      if (l >= 0) in->labels |= PII_LABEL_BIT(l);
    }
  }
  if ((v = pii_json_get(j, "delimiter")) && v->type == PJ_STR)
    in->delimiter = !strcmp(v->str, "markdown") ? '|' : !strcmp(v->str, "auto") ? 0 : v->len == 1 ? v->str[0] : -1;
  if ((v = pii_json_get(j, "header")) && v->type == PJ_BOOL) in->header = v->num != 0;
  if ((v = pii_json_get(j, "vote")) && v->type == PJ_BOOL) in->vote = v->num != 0;
  if ((v = pii_json_get(j, "context")) && v->type == PJ_STR) in->context = !strcmp(v->str, "header") ? PII_TABLE_CONTEXT_HEADER : 0;
}

// pii_options of an input line (base: the command-line options).
static pii_options line_options(const pii_options *base, const input *in) {
  pii_options o = *base;
  o.labels = in->labels;
  o.extra = in->extra;
  o.n_extra = in->n_extra;
  o.extra_unit = PII_UNIT_CODEPOINT;
  return o;
}

// All "text" fields of the JSON lines on stdin.
static input *read_texts(size_t *count) {
  size_t cap = 256, n = 0, lcap = 1 << 16;
  input *t = malloc(cap * sizeof *t);
  char *line = malloc(lcap);
  size_t len = 0;
  int c;
  for (;;) {
    c = getchar();
    if (c != EOF && c != '\n') {
      if (len + 1 >= lcap) line = realloc(line, lcap *= 2);
      line[len++] = (char)c;
      continue;
    }
    if (len) {
      pii_json j;
      if (pii_json_parse(line, len, &j) == 0) {
        const pii_json *v = pii_json_get(&j, "text");
        if (v && v->type == PJ_STR) {
          if (n == cap) t = realloc(t, (cap *= 2) * sizeof *t);
          t[n].s = malloc(v->len + 1);
          memcpy(t[n].s, v->str, v->len + 1);
          t[n].n = v->len;
          read_line_options(&j, &t[n]);
          n++;
        }
        pii_json_free(&j);
      } else {
        fprintf(stderr, "bad JSON line %zu\n", n + 1);
        exit(2);
      }
    }
    len = 0;
    if (c == EOF) break;
  }
  free(line);
  *count = n;
  return t;
}

static pii_model *load(const char *onnx, const char *tok) {
  size_t n1, n2;
  uint8_t *a = read_file(onnx, &n1), *b = read_file(tok, &n2);
  if (!a || !b) {
    fprintf(stderr, "cannot read %s or %s\n", onnx, tok);
    exit(2);
  }
  char err[256];
  pii_model *m = pii_model_load(a, n1, b, n2, err, sizeof err);
  free(a);
  free(b);
  if (!m) {
    fprintf(stderr, "load failed: %s\n", err);
    exit(2);
  }
  return m;
}

static double now_ms(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static void print_spans(const pii_span *s, size_t n, int with_source) {
  printf("{\"spans\": [");
  for (size_t i = 0; i < n; i++) {
    printf("%s[%zu, %zu, \"%s\", %zu, %zu, %zu, %zu", i ? ", " : "", s[i].cp_start, s[i].cp_end, s[i].label_name, s[i].u16_start,
           s[i].u16_end, s[i].byte_start, s[i].byte_end);
    if (with_source) printf(", %d", s[i].source);
    printf("]");
  }
  printf("]}\n");
}

typedef struct {
  const pii_model *m;
  const input *texts;
  size_t n;
  const pii_options *opt;
  char **out;  // per text: "start-end:label ..." in code points
  int fail;
} job;

static char *span_key(const pii_span *s, size_t n) {
  size_t cap = 32 + n * 48;
  char *k = malloc(cap), *w = k;
  *w = 0;
  for (size_t i = 0; i < n; i++)
    w += snprintf(w, cap - (size_t)(w - k), "%zu-%zu:%s/%zu-%zu/%zu-%zu ", s[i].cp_start, s[i].cp_end, s[i].label_name, s[i].u16_start,
                  s[i].u16_end, s[i].byte_start, s[i].byte_end);
  return k;
}

static void *run_job(void *arg) {
  job *j = arg;
  for (size_t i = 0; i < j->n; i++) {
    pii_span *s;
    size_t n;
    if (pii_detect(j->m, j->texts[i].s, j->texts[i].n, j->opt, &s, &n)) {
      j->fail = 1;
      j->out[i] = NULL;
      continue;
    }
    j->out[i] = span_key(s, n);
    pii_spans_free(s);
  }
  return NULL;
}


// ---------------------------------------------------------------- selftest
static int fails;
#define EXPECT(cond)                                              \
  do {                                                            \
    if (!(cond)) {                                                \
      fprintf(stderr, "selftest: %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      fails++;                                                    \
    }                                                             \
  } while (0)

static pii_cspan cs(size_t s, size_t e, int label, int source) {
  pii_cspan x = {s, e, label, source};
  return x;
}

// The cases of js/test/combine.test.ts.
static void selftest_combine(void) {
  pii_cspan in[2], out[2];
  in[0] = cs(5, 12, PII_PHONE, PII_SOURCE_MODEL);
  in[1] = cs(3, 14, PII_FINANCIAL, PII_SOURCE_REGEX);
  EXPECT(pii_combine(in, 2, PII_LABELS_ALL, NULL, out) == 1 && out[0].start == 3 && out[0].end == 14 && out[0].label == PII_PHONE &&
         out[0].source == (PII_SOURCE_MODEL | PII_SOURCE_REGEX));
  in[0] = cs(0, 20, PII_URL_L2, PII_SOURCE_MODEL);
  in[1] = cs(0, 20, PII_URL, PII_SOURCE_REGEX);
  EXPECT(pii_combine(in, 2, PII_LABELS_LEVEL1, NULL, out) == 0);
  in[0] = cs(0, 9, PII_ORG, PII_SOURCE_MODEL);
  in[1] = cs(0, 16, PII_EMAIL, PII_SOURCE_REGEX);
  EXPECT(pii_combine(in, 2, PII_LABEL_BIT(PII_EMAIL), NULL, out) == 1 && out[0].start == 0 && out[0].end == 16 &&
         out[0].label == PII_EMAIL && out[0].source == PII_SOURCE_REGEX);
  // overlapping model spans rank by UTF-16 length: "😀😀abcd", [0, 3) is 5 units, [2, 6) is 4 code points but 4 units
  const size_t u16[] = {0, 2, 4, 5, 6, 7, 8};
  in[0] = cs(0, 3, PII_PERSON, PII_SOURCE_MODEL);
  in[1] = cs(2, 6, PII_ADDRESS, PII_SOURCE_MODEL);
  EXPECT(pii_combine(in, 2, PII_LABELS_ALL, u16, out) == 1 && out[0].label == PII_PERSON && out[0].end == 6);
  in[0] = cs(0, 3, PII_PERSON, PII_SOURCE_MODEL);
  in[1] = cs(2, 6, PII_ADDRESS, PII_SOURCE_MODEL);
  EXPECT(pii_combine(in, 2, PII_LABELS_ALL, NULL, out) == 1 && out[0].label == PII_ADDRESS);
}

// More than 32 spans (merge sort) in reverse order; the result must equal the one of the sorted input.
static void selftest_combine_many(void) {
  enum { N = 100 };
  pii_cspan in[N], out[N];
  for (size_t i = 0; i < N; i++) in[i] = cs((N - 1 - i) * 10, (N - 1 - i) * 10 + 5, (int)(i % PII_LABEL_COUNT), i % 3 ? PII_SOURCE_MODEL : PII_SOURCE_REGEX);
  in[N - 1] = cs(0, 25, PII_EMAIL, PII_SOURCE_REGEX);  // overlaps 10-15 and 20-25: one region
  size_t n = pii_combine(in, N, PII_LABELS_ALL, NULL, out);
  EXPECT(n == N - 2);
  int sorted = 1;
  for (size_t i = 1; i < n; i++) sorted &= out[i - 1].end <= out[i].start;
  EXPECT(sorted && out[0].start == 0 && out[0].end == 25);
}

static void selftest_static(void) {
  EXPECT(!strcmp(pii_version(), PII_VERSION));
  EXPECT(!strcmp(pii_strerror(PII_ERR_ARG), "invalid argument") && !strcmp(pii_strerror(12345), "unknown error"));
  pii_options_init(NULL);
  EXPECT(!strcmp(pii_label_name(PII_URL_L2), "URL_L2") && !pii_label_name(-1) && !pii_label_name(PII_LABEL_COUNT));
  EXPECT(pii_label_from_name("DATE_OF_BIRTH") == PII_DATE_OF_BIRTH && pii_label_from_name("O") == -1 && pii_label_from_name(NULL) == -1);
  EXPECT(!strcmp(pii_default_variant("medium"), "q4e") && !strcmp(pii_default_variant("nano"), "q4") && !pii_default_variant("xl"));
  const pii_file_pin *pin = pii_model_file("medium", "q4e");
  EXPECT(pin && pin->bytes == 11303433u && !strcmp(pin->path, "onnx/model_q4e.onnx") && !pii_model_file("medium", "fp32"));
  char hex[65];
  pii_sha256_hex("abc", 3, hex);
  EXPECT(!strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
  pii_sha256_hex("", 0, hex);
  EXPECT(!strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
  EXPECT(pii_verify_file("abc", 3, "nano", "q4") == PII_ERR_ARG);
  char err[128] = "x";
  EXPECT(!pii_model_load(NULL, 0, NULL, 0, err, sizeof err) && err[0]);
  EXPECT(!pii_model_load((const uint8_t *)"abc", 3, (const uint8_t *)"{}", 2, err, sizeof err) && err[0]);
  pii_span *s;
  size_t n;
  EXPECT(pii_detect(NULL, "a", 1, NULL, &s, &n) == PII_ERR_ARG);
  EXPECT(pii_detect_table(NULL, "a", 1, NULL, NULL, &s, &n) == PII_ERR_ARG);
  pii_table_options t;
  pii_table_options_init(&t);
  pii_table_options_init(NULL);
  EXPECT(t.struct_size == sizeof t && t.delimiter == 0 && t.header == 1 && t.vote == 1);
}

// ---------------------------------------------------------------- selftest: grapheme snapping and table mode (no model)
// Code point index of byte offset b of s.
static size_t cpi(const char *s, size_t b) {
  size_t k = 0;
  for (size_t i = 0; i < b; i++) k += ((unsigned char)s[i] & 0xc0) != 0x80;
  return k;
}
// Code point range of the first occurrence of needle in s at or after code point `from` ([0, 0) when absent).
static pii_cspan find(const char *s, const char *needle, size_t from, int label, int source) {
  const char *p = s;
  while (*p && cpi(s, (size_t)(p - s)) < from) p++;
  p = strstr(p, needle);
  if (!p) return cs(0, 0, label, source);
  const size_t b = (size_t)(p - s);
  return cs(cpi(s, b), cpi(s, b + strlen(needle)), label, source);
}

static size_t snap(const char *text, pii_cspan *s, size_t n) {
  pii_text tx;
  if (pii_text_init(&tx, text, strlen(text))) return 0;
  n = pii_snap_graphemes(s, n, tx.cp, tx.n, NULL, 0);
  pii_text_free(&tx);
  return n;
}

static void selftest_snap(void) {
  pii_cspan s[4];
  // emoji ZWJ sequence: a span over "👩‍👩" (woman ZWJ woman) widens to the whole family "👩‍👩‍👧" (5 code points)
  const char *fam = "\xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa7 Contact";
  s[0] = cs(0, 3, PII_PERSON, PII_SOURCE_MODEL);
  EXPECT(snap(fam, s, 1) == 1 && s[0].start == 0 && s[0].end == 5 && s[0].label == PII_PERSON && s[0].source == PII_SOURCE_MODEL);
  // a span starting inside it moves back; one already on boundaries does not move
  s[0] = cs(2, 7, PII_PERSON, PII_SOURCE_MODEL);
  s[1] = cs(8, 13, PII_ORG, PII_SOURCE_REGEX);
  EXPECT(snap(fam, s, 2) == 2 && s[0].start == 0 && s[0].end == 7 && s[1].start == 8 && s[1].end == 13 && s[1].label == PII_ORG);
  // two spans meeting inside one cluster merge; equal original lengths keep the earlier label and source
  s[0] = cs(0, 1, PII_PERSON, PII_SOURCE_MODEL);
  s[1] = cs(2, 3, PII_ORG, PII_SOURCE_REGEX);
  EXPECT(snap(fam, s, 2) == 1 && s[0].start == 0 && s[0].end == 5 && s[0].label == PII_PERSON && s[0].source == PII_SOURCE_MODEL);
  // the longer original span wins; in a chain each next span competes with the current winner's original length
  s[0] = cs(0, 1, PII_PERSON, PII_SOURCE_MODEL);
  s[1] = cs(2, 4, PII_ORG, PII_SOURCE_REGEX);
  s[2] = cs(4, 6, PII_EMAIL, PII_SOURCE_MODEL);  // length 2 = the winner's: ORG stays
  EXPECT(snap(fam, s, 3) == 1 && s[0].start == 0 && s[0].end == 6 && s[0].label == PII_ORG && s[0].source == PII_SOURCE_REGEX);
  s[0] = cs(0, 1, PII_PERSON, PII_SOURCE_MODEL);
  s[1] = cs(2, 3, PII_ORG, PII_SOURCE_REGEX);
  s[2] = cs(4, 9, PII_EMAIL, PII_SOURCE_MODEL);
  EXPECT(snap(fam, s, 3) == 1 && s[0].start == 0 && s[0].end == 9 && s[0].label == PII_EMAIL && s[0].source == PII_SOURCE_MODEL);
  // flags: regional indicator pairs (🇰🇷🇺🇸); the second indicator of a pair snaps back, the next pair is its own cluster
  const char *flags = "\xf0\x9f\x87\xb0\xf0\x9f\x87\xb7\xf0\x9f\x87\xba\xf0\x9f\x87\xb8 Seoul";
  s[0] = cs(1, 2, PII_ADDRESS, PII_SOURCE_MODEL);
  s[1] = cs(2, 3, PII_ADDRESS, PII_SOURCE_MODEL);
  EXPECT(snap(flags, s, 2) == 2 && s[0].start == 0 && s[0].end == 2 && s[1].start == 2 && s[1].end == 4);
  // skin tone modifier (👍🏽), keycap (1 U+FE0F U+20E3), combining acute (e U+0301), Hangul jamo (ᄀ ᅡ ᆨ)
  const char *tone = "\xf0\x9f\x91\x8d\xf0\x9f\x8f\xbd ok";
  s[0] = cs(0, 1, PII_USERNAME, PII_SOURCE_MODEL);
  EXPECT(snap(tone, s, 1) == 1 && s[0].start == 0 && s[0].end == 2);
  const char *keycap = "1\xef\xb8\x8f\xe2\x83\xa3" "2 x";
  s[0] = cs(0, 1, PII_PHONE, PII_SOURCE_MODEL);
  EXPECT(snap(keycap, s, 1) == 1 && s[0].start == 0 && s[0].end == 3);
  s[0] = cs(1, 4, PII_PHONE, PII_SOURCE_MODEL);  // "⃣" "2" " ": starts back at "1"
  EXPECT(snap(keycap, s, 1) == 1 && s[0].start == 0 && s[0].end == 4);
  const char *acute = "Jose\xcc\x81 Garci\xcc\x81" "a";
  s[0] = cs(0, 4, PII_PERSON, PII_SOURCE_MODEL);
  s[1] = cs(6, 11, PII_PERSON, PII_SOURCE_MODEL);
  EXPECT(snap(acute, s, 2) == 2 && s[0].start == 0 && s[0].end == 5 && s[1].start == 6 && s[1].end == 12);
  const char *jamo = "\xe1\x84\x80\xe1\x85\xa1\xe1\x86\xa8 x";
  s[0] = cs(1, 2, PII_PERSON, PII_SOURCE_MODEL);
  EXPECT(snap(jamo, s, 1) == 1 && s[0].start == 0 && s[0].end == 3);
  // spans touching at a boundary stay apart; CRLF is one cluster
  s[0] = cs(0, 1, PII_PERSON, PII_SOURCE_MODEL);
  s[1] = cs(1, 2, PII_ORG, PII_SOURCE_MODEL);
  EXPECT(snap("ab", s, 2) == 2 && s[0].end == 1 && s[1].start == 1);
  s[0] = cs(1, 2, PII_PERSON, PII_SOURCE_MODEL);
  EXPECT(snap("a\r\nb", s, 1) == 1 && s[0].start == 1 && s[0].end == 3);
  EXPECT(snap("", s, 0) == 0);
  // empty spans stay as they are (pii_options.extra may hold one); in a cluster after another span they join it, and a
  // span whose cluster starts before an empty span takes the group's start back
  s[0] = cs(2, 2, PII_PERSON, PII_SOURCE_REGEX);
  EXPECT(snap("abcdef", s, 1) == 1 && s[0].start == 2 && s[0].end == 2 && s[0].label == PII_PERSON);
  s[0] = cs(2, 2, PII_PERSON, PII_SOURCE_REGEX);
  s[1] = cs(3, 5, PII_EMAIL, PII_SOURCE_MODEL);
  EXPECT(snap("ab\xcc\x81" "cdef", s, 2) == 2 && s[0].start == 2 && s[0].end == 2 && s[1].start == 3 && s[1].end == 5);
  s[0] = cs(2, 2, PII_PERSON, PII_SOURCE_REGEX);
  s[1] = cs(3, 5, PII_EMAIL, PII_SOURCE_MODEL);  // "b" U+0301 U+0301 is one cluster [1, 4)
  EXPECT(snap("ab\xcc\x81\xcc\x81" "cd", s, 2) == 1 && s[0].start == 1 && s[0].end == 5 && s[0].label == PII_EMAIL &&
         s[0].source == PII_SOURCE_MODEL);
}

typedef struct {
  pii_cell *c;
  size_t nc, rows;
  int delim;
} parsed;

static int parse(const char *text, int delimiter, parsed *p) {
  pii_text tx;
  pii_vec cells;
  pii_vec_init(&cells, sizeof(pii_cell));
  memset(p, 0, sizeof *p);
  if (pii_text_init(&tx, text, strlen(text))) return -1;
  int rc = pii_table_parse(&tx, delimiter, &cells, &p->rows, &p->delim);
  p->c = cells.p;
  p->nc = cells.n;
  pii_text_free(&tx);
  return rc;
}

// The cell (row, col) has the code point range of needle (first occurrence at or after from).
static int cell_is(const parsed *p, const char *text, uint32_t row, uint32_t col, const char *needle, size_t from) {
  const pii_cspan r = find(text, needle, from, 0, 0);
  for (size_t i = 0; i < p->nc; i++)
    if (p->c[i].row == row && p->c[i].col == col) return p->c[i].start == r.start && p->c[i].end == r.end;
  return 0;
}

static void selftest_table_parse(void) {
  parsed p;
  // auto-detection: comma, tab, semicolon, markdown; prose and single-column text are not tables
  const char *comma = "name,email\nAnn,a@x.com\nBob,b@x.com\n";
  EXPECT(!parse(comma, 0, &p) && p.delim == ',' && p.rows == 3 && p.nc == 6 && cell_is(&p, comma, 2, 1, "b@x.com", 0));
  free(p.c);
  const char *tab = "name\temail; note\nAnn\ta@x.com; x\nBob\tb@x.com; y\n";
  EXPECT(!parse(tab, 0, &p) && p.delim == '\t' && p.rows == 3 && cell_is(&p, tab, 1, 1, "a@x.com; x", 0));
  free(p.c);
  const char *semi = "a;b;c\n1;2;3\n4;5;6";
  EXPECT(!parse(semi, 0, &p) && p.delim == ';' && p.rows == 3 && p.nc == 9 && cell_is(&p, semi, 2, 2, "6", 0));
  free(p.c);
  const char *md = "\n  | Name | Email |\n|---|:--:|\n| Ann Lee |  |\n| Bob | b@x |\n";
  EXPECT(!parse(md, 0, &p) && p.delim == '|' && p.rows == 3 && p.nc == 6 && cell_is(&p, md, 0, 0, "Name", 0) &&
         cell_is(&p, md, 1, 0, "Ann Lee", 0) && cell_is(&p, md, 2, 1, "b@x", 0));
  for (size_t i = 0; i < p.nc; i++)  // the all-space segment: an empty cell at the segment's start
    if (p.c[i].row == 1 && p.c[i].col == 1) EXPECT(p.c[i].start == p.c[i].end && p.c[i].start == find(md, "|  |", 0, 0, 0).start + 1);
  free(p.c);
  const char *prose = "Hello, my name is Ann Lee.\nI live in Seoul; call me.\nThanks";
  EXPECT(!parse(prose, 0, &p) && p.delim == 0 && p.rows == 0 && p.nc == 0);
  free(p.c);
  EXPECT(!parse("x\n| a |\n| b |", 0, &p) && p.delim == 0);  // the first non-empty line must start with '|'
  free(p.c);
  EXPECT(!parse("| only one |\nplain line", 0, &p) && p.delim == 0);  // markdown needs 2 of the first 5 lines
  free(p.c);
  EXPECT(!parse("a,b\n", 0, &p) && p.delim == 0);  // one record of 2 fields: score 1 < 2
  free(p.c);
  // ties go to the earlier delimiter: 2 rows of 2 fields with ',' and with ';'
  EXPECT(!parse("a,b;c\nd,e;f\n", 0, &p) && p.delim == ',');
  free(p.c);
  // quoted fields: delimiters, escaped quotes and line breaks inside quotes; text after the closing quote is skipped
  const char *q = "a,b\n\"Lee, Ann\",\"say \"\"hi\"\"\"\n\"multi\nline\"junk,x\n";
  EXPECT(!parse(q, ',', &p) && p.rows == 3 && p.nc == 6 && cell_is(&p, q, 1, 0, "Lee, Ann", 0) &&
         cell_is(&p, q, 1, 1, "say \"\"hi\"\"", 0) && cell_is(&p, q, 2, 0, "multi\nline", 0) && cell_is(&p, q, 2, 1, "x", 10));
  free(p.c);
  // blank lines (empty, whitespace, a lone "") are not rows; CRLF ends records, a lone CR is an ordinary character
  const char *crlf = "a,b\r\n\r\n \t\r\n\"\"\r\n1,2\r\nx\ry,4\r\n";
  EXPECT(!parse(crlf, 0, &p) && p.delim == ',' && p.rows == 3 && p.nc == 6 && cell_is(&p, crlf, 0, 1, "b", 0) &&
         cell_is(&p, crlf, 1, 0, "1", 0) && cell_is(&p, crlf, 2, 0, "x\ry", 0));
  free(p.c);
  // an unclosed quote runs to the end of the text; a trailing delimiter gives an empty last field
  const char *open = "a,b,\n\"open,1\n2,3";
  EXPECT(!parse(open, ',', &p) && p.rows == 2 && p.nc == 4 && cell_is(&p, open, 1, 0, "open,1\n2,3", 0));
  for (size_t i = 0; i < p.nc; i++)
    if (p.c[i].row == 0 && p.c[i].col == 2) EXPECT(p.c[i].start == 4 && p.c[i].end == 4);
  free(p.c);
  // markdown: a line without a final '|' keeps its last segment; "|" alone and separator lines are not rows; prose ignored
  const char *md2 = "| a | b\nintro text\n| 1 | 2 |\n|\n| :-: | --- |\n| 3 |\r\n";
  EXPECT(!parse(md2, '|', &p) && p.rows == 3 && p.nc == 5 && cell_is(&p, md2, 0, 1, "b", 0) && cell_is(&p, md2, 1, 1, "2", 0) &&
         cell_is(&p, md2, 2, 0, "3", 0));
  free(p.c);
  // explicit delimiter on text of one row: not a table (0 or 1 rows)
  EXPECT(!parse("a,b,c", ',', &p) && p.delim == ',' && p.rows == 1);
  free(p.c);
}

typedef struct {
  pii_cspan *s;
  size_t n;
} result;

static result apply(const char *text, pii_cspan *spans, size_t n, int delimiter, int header, int vote, uint32_t labels) {
  result r = {NULL, 0};
  pii_text tx;
  if (pii_text_init(&tx, text, strlen(text))) return r;
  EXPECT(pii_table_apply(&tx, spans, n, delimiter, header, vote, labels, &r.s, &r.n) == PII_OK);
  pii_text_free(&tx);
  return r;
}

// Spans of r with this label and source inside [a, b).
static size_t count(const result *r, int label, int source, size_t a, size_t b) {
  size_t k = 0;
  for (size_t i = 0; i < r->n; i++) k += r->s[i].label == label && r->s[i].source == source && r->s[i].start >= a && r->s[i].end <= b;
  return k;
}

static int has(const result *r, pii_cspan x) {
  for (size_t i = 0; i < r->n; i++)
    if (r->s[i].start == x.start && r->s[i].end == x.end && r->s[i].label == x.label && r->s[i].source == x.source) return 1;
  return 0;
}

static void selftest_table_apply(void) {
  // clipping: a span across a delimiter splits into cell pieces; pieces of only whitespace or delimiters are dropped
  const char *small = "name, city\nAnn,Lee \n";
  pii_cspan in[64];
  in[0] = find(small, "name, c", 0, PII_ORG, PII_SOURCE_MODEL);
  in[1] = find(small, "Ann,Lee", 0, PII_PERSON, PII_SOURCE_MODEL | PII_SOURCE_REGEX);
  result r = apply(small, in, 2, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(r.n == 4 && has(&r, find(small, "name", 0, PII_ORG, PII_SOURCE_MODEL)) && has(&r, find(small, " c", 0, PII_ORG, PII_SOURCE_MODEL)) &&
         has(&r, find(small, "Ann", 0, PII_PERSON, 3)) && has(&r, find(small, "Lee", 0, PII_PERSON, 3)));
  free(r.s);
  in[0] = find(small, ", ", 0, PII_ORG, PII_SOURCE_MODEL);
  r = apply(small, in, 1, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(r.n == 0);
  free(r.s);
  // not a table: the spans unchanged (then snapped)
  const char *prose = "Contact \xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa7 Ann, please.";
  in[0] = cs(8, 11, PII_PERSON, PII_SOURCE_MODEL);
  in[1] = find(prose, "Ann, p", 0, PII_PERSON, PII_SOURCE_MODEL);
  r = apply(prose, in, 2, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(r.n == 2 && r.s[0].start == 8 && r.s[0].end == 13 && has(&r, find(prose, "Ann, p", 0, PII_PERSON, PII_SOURCE_MODEL)));
  free(r.s);
  // snapping stays inside the cell: a value starting with U+0301 (one cluster with the ',', ';', '|' or opening '"' before
  // it) or ending with U+0600 (Prepend: one cluster with the delimiter after it) keeps the delimiter and quote unmasked
  static const struct {
    const char *text;
    size_t a0, b0, a1, b1;  // input spans (a1 == b1: one span); the output is the same spans
  } edge[] = {
      {"name,city\n\"\xcc\x81" "Ann, Lee\",Seoul\nBo,Busan", 11, 19, 0, 0},
      {"h1,h2\nc,\xcc\x81" "d\ne,f", 6, 7, 8, 10},
      {"a,b\nx\xd8\x80,y\nz,w", 4, 6, 0, 0},
      {"a;b\nx\xd8\x80;\xcc\x81y\nz;w", 4, 6, 7, 9},
      {"| a | b |\n|---|---|\n| x\xd8\x80|\xcc\x81y |\n| z | w |", 22, 24, 25, 27},
      {"a,b\n\"x\xd8\x80\",\"\xcc\x81y\"\nz,w", 5, 7, 10, 12},
  };
  for (size_t k = 0; k < sizeof edge / sizeof edge[0]; k++) {
    size_t n = 0;
    in[n++] = cs(edge[k].a0, edge[k].b0, PII_PERSON, PII_SOURCE_MODEL);
    if (edge[k].a1 < edge[k].b1) in[n++] = cs(edge[k].a1, edge[k].b1, PII_EMAIL, PII_SOURCE_MODEL);
    r = apply(edge[k].text, in, n, 0, 1, 0, PII_LABELS_ALL);
    EXPECT(r.n == n && has(&r, in[0]) && (n == 1 || has(&r, in[1])));
    free(r.s);
  }
  // an empty span in a table is dropped by the clip (its piece holds no non-whitespace code point)
  in[0] = cs(5, 5, PII_PERSON, PII_SOURCE_REGEX);
  r = apply("a,b\nxy,z\nq,w", in, 1, 0, 1, 0, PII_LABELS_ALL);
  EXPECT(r.n == 0);
  free(r.s);
}

// A survey: id, name (8 of 10 names found, one missed, one placeholder), email, phone, a free-text comment (quoted, with
// commas), a username; a blank CRLF line after row 5.
static const char *const SURVEY[10][6] = {
    {"1", "Ann Lee", "ann@x.com", "010-1234-5678", "The delivery was late and the box was badly damaged", "ann_lee"},
    {"2", "Bo Kim", "bo@x.com", "010-2222-3333", "Great service, the courier was polite and on time", "bokim"},
    {"3", "Cy Park", "cy@x.com", "010-4444-5555", "I would like a refund for the second order, please", "cypark9"},
    {"4", "Di Choi", "di@x.com", "010-6666-7777", "Packaging could be better but the product works fine", "dichoi"},
    {"5", "Ed Yun", "ed@x.com", "010-8888-9999", "Please call me back about the warranty extension", "ed_yun"},
    {"6", "Fa Han", "fa@x.com", "010-1111-0000", "Nothing to add, everything was as described online", "fahan"},
    {"7", "Gu Seo", "gu@x.com", "010-3333-1111", "The app crashed twice while I was paying for it", "guseo"},
    {"8", "Ha Jung", "ha@x.com", "010-5555-2222", "Five stars, I will recommend this shop to friends", "hajung"},
    {"9", "Io Lim", "unknown", "12345", "Support answered quickly and solved my problem", "N/A"},
    {"10", "N/A", "io@x.org", "\xd9\xa0\xd9\xa1\xd9\xa0\xd9\xa1\xd9\xa2\xd9\xa3\xd9\xa4\xd9\xa5", "Delivery person left it at the wrong door again",
     "iolim"},
};

static void survey_text(char *text, size_t cap, size_t *row_cp) {
  size_t o = (size_t)snprintf(text, cap, "id,name,email,phone,comment,user\n");
  for (int i = 0; i < 10; i++) {
    row_cp[i] = cpi(text, o);
    o += (size_t)snprintf(text + o, cap - o, "%s,%s,%s,%s,\"%s\",%s%s", SURVEY[i][0], SURVEY[i][1], SURVEY[i][2], SURVEY[i][3],
                          SURVEY[i][4], SURVEY[i][5], i == 4 ? "\r\n\r\n" : "\n");
  }
}

// Model and pattern spans of the survey in text order: "id" in the header as ORG; names, emails and phones of rows 1-8
// (row 6's name as ORG over "Han" when han_org); every comment whole as PERSON (free text must not vote); usernames of
// rows 1-3 only (3 < 4: no vote); with lim_org an ORG span over "Lim" inside row 9's name.
static size_t survey_spans(const char *text, const size_t *row_cp, int han_org, int lim_org, pii_cspan *in) {
  size_t n = 0;
  in[n++] = find(text, "id", 0, PII_ORG, PII_SOURCE_MODEL);
  for (int i = 0; i < 10; i++) {
    const size_t r0 = row_cp[i];
    if (i == 5 && han_org) in[n++] = find(text, "Han", r0, PII_ORG, PII_SOURCE_MODEL);
    else if (i < 8) in[n++] = find(text, SURVEY[i][1], r0, PII_PERSON, PII_SOURCE_MODEL);
    if (i == 8 && lim_org) in[n++] = find(text, "Lim", r0, PII_ORG, PII_SOURCE_MODEL);
    if (i < 8) in[n++] = find(text, SURVEY[i][2], r0, PII_EMAIL, PII_SOURCE_REGEX);
    if (i < 8) in[n++] = find(text, SURVEY[i][3], r0, PII_PHONE, PII_SOURCE_MODEL | PII_SOURCE_REGEX);
    in[n++] = find(text, SURVEY[i][4], r0, PII_PERSON, PII_SOURCE_MODEL);
    if (i < 3) in[n++] = find(text, SURVEY[i][5], r0, PII_USERNAME, PII_SOURCE_MODEL);
  }
  return n;
}

static void selftest_table_vote(void) {
  char text[4096];
  size_t row_cp[10];
  pii_cspan in[64];
  survey_text(text, sizeof text, row_cp);
  const size_t ncp = cpi(text, strlen(text));
  size_t n = survey_spans(text, row_cp, 0, 0, in);
  // vote on: names, emails and phones propagate; "N/A", an email without "@" and a phone of 5 digits are skipped
  result r = apply(text, in, n, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, ncp) == 9);   // row 10's name is "N/A"; row 9's missed name is added
  EXPECT(count(&r, PII_EMAIL, PII_SOURCE_TABLE, 0, ncp) == 9);    // "unknown" skipped
  EXPECT(count(&r, PII_PHONE, PII_SOURCE_TABLE, 0, ncp) == 9);    // "12345" skipped, Arabic-Indic digits kept
  EXPECT(count(&r, PII_PERSON, PII_SOURCE_MODEL, 0, ncp) == 10);  // comments: clipped only
  EXPECT(count(&r, PII_USERNAME, PII_SOURCE_MODEL, 0, ncp) == 3 && count(&r, PII_USERNAME, PII_SOURCE_TABLE, 0, ncp) == 0);
  EXPECT(has(&r, find(text, "id", 0, PII_ORG, PII_SOURCE_MODEL)) && has(&r, find(text, "Io Lim", 0, PII_PERSON, PII_SOURCE_TABLE)) &&
         has(&r, find(text, "io@x.org", 0, PII_EMAIL, PII_SOURCE_TABLE)) && has(&r, find(text, "Ann Lee", 0, PII_PERSON, PII_SOURCE_TABLE)));
  EXPECT(!has(&r, find(text, "unknown", 0, PII_EMAIL, PII_SOURCE_TABLE)) && !has(&r, find(text, "12345", 0, PII_PHONE, PII_SOURCE_TABLE)));
  int sorted = 1;
  for (size_t i = 1; i < r.n; i++) sorted &= r.s[i - 1].end <= r.s[i].start;
  EXPECT(sorted && r.n == 1 + 9 + 9 + 9 + 10 + 3);
  free(r.s);
  // 7 of 10 names found (row 6 read as ORG): 5 * 7 < 4 * 10, the name column does not vote; the others still do
  n = survey_spans(text, row_cp, 1, 0, in);
  r = apply(text, in, n, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, ncp) == 0 && has(&r, find(text, "Han", row_cp[5], PII_ORG, PII_SOURCE_MODEL)) &&
         count(&r, PII_EMAIL, PII_SOURCE_TABLE, 0, ncp) == 9);
  free(r.s);
  // a short ORG span inside a voting cell ("Lim" of "Io Lim"): the TABLE span over the whole cell wins the merged group
  n = survey_spans(text, row_cp, 0, 1, in);
  r = apply(text, in, n, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(has(&r, find(text, "Io Lim", 0, PII_PERSON, PII_SOURCE_TABLE)) && count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, ncp) == 9 &&
         count(&r, PII_ORG, PII_SOURCE_MODEL, 0, ncp) == 1);
  free(r.s);
  // header off: the header row is data (11 cells, 8 supported: 5 * 8 < 4 * 11), nothing votes
  r = apply(text, in, n, 0, 0, 1, PII_LABELS_ALL);
  EXPECT(count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, ncp) + count(&r, PII_EMAIL, PII_SOURCE_TABLE, 0, ncp) +
         count(&r, PII_PHONE, PII_SOURCE_TABLE, 0, ncp) == 0 && r.n == n);
  free(r.s);
  // vote off: the clipped spans only
  r = apply(text, in, n, 0, 1, 0, PII_LABELS_ALL);
  EXPECT(r.n == n && count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, ncp) + count(&r, PII_EMAIL, PII_SOURCE_TABLE, 0, ncp) == 0 &&
         has(&r, find(text, "Lim", row_cp[8], PII_ORG, PII_SOURCE_MODEL)));
  free(r.s);
  // label filter: PHONE disabled never propagates (the other columns still do)
  r = apply(text, in, n, 0, 1, 1, PII_LABELS_ALL & ~PII_LABEL_BIT(PII_PHONE));
  EXPECT(count(&r, PII_PHONE, PII_SOURCE_TABLE, 0, ncp) == 0 && count(&r, PII_EMAIL, PII_SOURCE_TABLE, 0, ncp) == 9 &&
         count(&r, PII_PHONE, 3, 0, ncp) == 8);
  free(r.s);
  // tab-delimited reading of a comma file: one long field per row (median > 40 code points), nothing votes
  r = apply(text, in, n, '\t', 1, 1, PII_LABELS_ALL);
  EXPECT(r.n == n && count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, ncp) == 0);
  free(r.s);
  // the same table as markdown: same votes
  char md[4096];
  size_t o = (size_t)snprintf(md, sizeof md, "| id | name | email | phone | comment | user |\n|---|---|---|---|---|---|\n");
  for (int i = 0; i < 10; i++) {
    row_cp[i] = cpi(md, o);
    o += (size_t)snprintf(md + o, sizeof md - o, "| %s | %s | %s | %s | %s | %s |\n", SURVEY[i][0], SURVEY[i][1], SURVEY[i][2],
                          SURVEY[i][3], SURVEY[i][4], SURVEY[i][5]);
  }
  n = survey_spans(md, row_cp, 0, 0, in);
  r = apply(md, in, n, 0, 1, 1, PII_LABELS_ALL);
  EXPECT(count(&r, PII_PERSON, PII_SOURCE_TABLE, 0, o) == 9 && count(&r, PII_EMAIL, PII_SOURCE_TABLE, 0, o) == 9 &&
         count(&r, PII_PHONE, PII_SOURCE_TABLE, 0, o) == 9 && count(&r, PII_PERSON, PII_SOURCE_MODEL, 0, o) == 10);
  free(r.s);
}

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint64_t next_rand(void) {
  rng ^= rng << 13;
  rng ^= rng >> 7;
  rng ^= rng << 17;
  return rng;
}

// Every span of s starts and ends on an extended grapheme cluster boundary of text.
static int on_graphemes(const char *text, const pii_span *s, size_t n) {
  pii_text tx;
  if (pii_text_init(&tx, text, strlen(text))) return 0;
  char *bound = calloc(tx.n + 1, 1);
  for (size_t i = 0; i < tx.n; i = pii_grapheme_end(tx.cp, i, tx.n)) bound[i] = 1;
  bound[tx.n] = 1;
  int ok = 1;
  for (size_t i = 0; i < n; i++) ok &= bound[s[i].cp_start] && bound[s[i].cp_end];
  free(bound);
  pii_text_free(&tx);
  return ok;
}

static int same_spans(const pii_span *a, size_t na, const pii_span *b, size_t nb) {
  if (na != nb) return 0;
  for (size_t i = 0; i < na; i++)
    if (a[i].byte_start != b[i].byte_start || a[i].byte_end != b[i].byte_end || a[i].cp_start != b[i].cp_start ||
        a[i].cp_end != b[i].cp_end || a[i].u16_start != b[i].u16_start || a[i].u16_end != b[i].u16_end || a[i].label != b[i].label ||
        a[i].source != b[i].source)
      return 0;
  return 1;
}

// pii_detect_table and grapheme snapping of pii_detect with a model.
static void selftest_model_table(const pii_model *m) {
  pii_span *s, *s2;
  size_t n, n2;
  pii_table_options t;
  pii_table_options_init(&t);
  const char *csv = "name,email,note\nMaria Lopez,maria.lopez@example.com,called twice\nKim Minsu,minsu.kim@example.com,\"left, then came back\"\n";
  t.delimiter = 'x';
  EXPECT(pii_detect_table(m, csv, strlen(csv), NULL, &t, &s, &n) == PII_ERR_ARG);
  t.delimiter = 0;
  t.struct_size = 8;
  EXPECT(pii_detect_table(m, csv, strlen(csv), NULL, &t, &s, &n) == PII_ERR_ARG);
  EXPECT(pii_detect_table(m, NULL, 3, NULL, NULL, &s, &n) == PII_ERR_ARG);
  EXPECT(pii_detect_table(m, "", 0, NULL, NULL, &s, &n) == PII_OK && n == 0 && !s);
  // a table: spans never hold a delimiter, quote or line break (no field here has one inside its value)
  EXPECT(pii_detect_table(m, csv, strlen(csv), NULL, NULL, &s, &n) == PII_OK && n > 0);
  const size_t q0 = (size_t)(strstr(csv, "\"left") - csv), q1 = q0 + strlen("\"left, then came back\"");
  int clean = 1;
  for (size_t i = 0; i < n; i++)
    for (size_t b = s[i].byte_start; b < s[i].byte_end; b++) clean &= csv[b] != '\n' && csv[b] != '"' && (csv[b] != ',' || (b > q0 && b < q1));
  EXPECT(clean);
  pii_spans_free(s);
  // not a table (prose; one row): exactly the spans of pii_detect, also with snapping over emoji
  const char *texts[] = {
      "Please email Maria Lopez at maria.lopez@example.com or call +1 202 555 0143.",
      "\xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa9\xe2\x80\x8d\xf0\x9f\x91\xa7 Maria Lopez\xf0\x9f\x87\xb0\xf0\x9f\x87\xb7 called, "
      "Jose\xcc\x81 Garci\xcc\x81" "a\xf0\x9f\x91\x8d\xf0\x9f\x8f\xbd at 1\xef\xb8\x8f\xe2\x83\xa3" "010-1234-5678",
      "name,email\nMaria Lopez,maria.lopez@example.com",  // explicit tab: one field per row
  };
  for (size_t k = 0; k < sizeof texts / sizeof texts[0]; k++) {
    pii_table_options_init(&t);
    if (k == 2) t.delimiter = '\t';
    EXPECT(pii_detect(m, texts[k], strlen(texts[k]), NULL, &s, &n) == PII_OK);
    EXPECT(pii_detect_table(m, texts[k], strlen(texts[k]), NULL, &t, &s2, &n2) == PII_OK);
    EXPECT(same_spans(s, n, s2, n2) && on_graphemes(texts[k], s, n));
    pii_spans_free(s);
    pii_spans_free(s2);
  }
  // a pattern span over part of an emoji ZWJ sequence covers the whole sequence
  pii_options o;
  pii_options_init(&o);
  pii_extra_span x = {1, 3, PII_USERNAME};  // code points 1-2 of "👩‍👩‍👧"
  o.extra = &x;
  o.n_extra = 1;
  o.extra_unit = PII_UNIT_CODEPOINT;
  EXPECT(pii_detect(m, texts[1], strlen(texts[1]), &o, &s, &n) == PII_OK && n >= 1 && s[0].cp_start == 0 && s[0].cp_end >= 5 &&
         s[0].u16_start == 0 && s[0].byte_end >= 18);
  pii_spans_free(s);
  // an empty extra span stays empty (not widened to the character after it)
  const char *plain = "the weather is nice today";
  pii_extra_span e = {4, 4, PII_PERSON};
  o.extra = &e;
  EXPECT(pii_detect(m, plain, strlen(plain), &o, &s, &n) == PII_OK);
  int widened = 0;
  for (size_t i = 0; i < n; i++) widened |= s[i].cp_start <= 4 && s[i].cp_end > 4;
  EXPECT(!widened);
  pii_spans_free(s);
}

static void selftest_model(const char *onnx, const char *tok) {
  size_t n1, n2;
  uint8_t *a = read_file(onnx, &n1), *b = read_file(tok, &n2);
  EXPECT(a && b);
  if (!a || !b) return;
  char err[256];
  pii_model *m = pii_model_load(a, n1, b, n2, err, sizeof err);
  EXPECT(m != NULL);
  if (!m) return;
  pii_span *s;
  size_t n;
  pii_options o;
  pii_options_init(&o);
  EXPECT(pii_detect(m, "", 0, NULL, &s, &n) == PII_OK && n == 0 && !s);
  EXPECT(pii_detect(m, NULL, 0, NULL, &s, &n) == PII_OK && n == 0);
  EXPECT(pii_detect(m, NULL, 3, NULL, &s, &n) == PII_ERR_ARG);
  const char *text = "Please email Maria Lopez at maria.lopez@example.com or call +1 202 555 0143.";
  EXPECT(pii_detect(m, text, strlen(text), NULL, &s, &n) == PII_OK);
  size_t all = n;
  int has_email = 0;
  for (size_t i = 0; i < n; i++) {
    EXPECT(s[i].byte_start < s[i].byte_end && s[i].byte_end <= strlen(text) && (i == 0 || s[i - 1].byte_end <= s[i].byte_start));
    EXPECT(s[i].cp_start == s[i].byte_start && s[i].u16_end == s[i].byte_end && s[i].source == PII_SOURCE_MODEL);
    has_email |= s[i].label == PII_EMAIL;
  }
  EXPECT(all > 0 && has_email);
  pii_spans_free(s);
  o.labels = 0;
  EXPECT(pii_detect(m, text, strlen(text), &o, &s, &n) == PII_OK && n == 0);
  o.labels = PII_LABELS_ALL & ~PII_LABEL_BIT(PII_EMAIL);
  EXPECT(pii_detect(m, text, strlen(text), &o, &s, &n) == PII_OK && n < all);
  for (size_t i = 0; i < n; i++) EXPECT(s[i].label != PII_EMAIL);
  pii_spans_free(s);
  // an extra span (a pattern match) in UTF-16 units over text with non-BMP characters: "😀 " is 2 units, 1 code point, 4 bytes
  const char *t2 = "\xf0\x9f\x98\x80 ref AB-123456 \xed\x95\x9c\xea\xb8\x80";
  pii_options_init(&o);
  pii_extra_span x = {7, 16, PII_ID_NUMBER};  // "AB-123456" in UTF-16 units
  o.extra = &x;
  o.n_extra = 1;
  o.extra_unit = PII_UNIT_UTF16;
  EXPECT(pii_detect(m, t2, strlen(t2), &o, &s, &n) == PII_OK && n >= 1);
  int found = 0;
  for (size_t i = 0; i < n; i++)
    if (s[i].cp_start <= 6 && s[i].cp_end >= 15) {
      found = 1;
      // the pattern label counts unless the model found the same family there (combine)
      EXPECT((s[i].source & PII_SOURCE_REGEX) || s[i].label == PII_ID_NUMBER);
      EXPECT(s[i].byte_start <= 9 && s[i].byte_end >= 18 && s[i].u16_start <= 7 && s[i].u16_end >= 16);
    }
  EXPECT(found);
  pii_spans_free(s);
  x.end = 100;
  EXPECT(pii_detect(m, t2, strlen(t2), &o, &s, &n) == PII_ERR_ARG);
  x.end = 16;
  x.label = PII_LABEL_COUNT;
  EXPECT(pii_detect(m, t2, strlen(t2), &o, &s, &n) == PII_ERR_ARG);
  o.struct_size = 8;
  EXPECT(pii_detect(m, t2, strlen(t2), &o, &s, &n) == PII_ERR_ARG);
  // invalid UTF-8 and WTF-8 surrogates: one code point per bad byte, offsets stay inside the input
  const char bad[] = "\xff\xfe Kim \xc3 \xed\xa0\x80 kim@example.com \xe2\x82";
  EXPECT(pii_detect(m, bad, sizeof bad - 1, NULL, &s, &n) == PII_OK);
  for (size_t i = 0; i < n; i++) EXPECT(s[i].byte_end <= sizeof bad - 1 && s[i].cp_end <= s[i].byte_end);
  pii_spans_free(s);
  pii_token *tk;
  EXPECT(pii_tokenize(m, bad, sizeof bad - 1, &tk, &n) == PII_OK && n > 0);
  pii_tokens_free(tk);
  // a long text of one repeated character and a long text without spaces (several windows each)
  size_t big_n = 6000;
  char *big = malloc(big_n + 1);
  memset(big, 'a', big_n);
  big[big_n] = 0;
  EXPECT(pii_detect(m, big, big_n, NULL, &s, &n) == PII_OK);
  pii_spans_free(s);
  for (size_t i = 0; i < big_n; i++) big[i] = (char)(i % 7 == 6 ? ' ' : '0' + i % 10);
  EXPECT(pii_detect(m, big, big_n, NULL, &s, &n) == PII_OK);
  pii_spans_free(s);
  // an lstrip special token ("<mask>") after a long whitespace run: the same ids as after one space (and linear time)
  const size_t ws_n = 200000;
  char *wsx = malloc(ws_n + 16);
  wsx[0] = 'x';
  memset(wsx + 1, ' ', ws_n);
  memcpy(wsx + 1 + ws_n, "<mask> y", 8);
  pii_token *tw1 = NULL, *tw2 = NULL;
  size_t n1t = 0, n2t = 0;
  EXPECT(pii_tokenize(m, wsx, ws_n + 9, &tw1, &n1t) == PII_OK && pii_tokenize(m, "x <mask> y", 10, &tw2, &n2t) == PII_OK);
  EXPECT(n1t == n2t && n1t >= 3);
  for (size_t i = 0; i < n1t && i < n2t; i++) EXPECT(tw1[i].id == tw2[i].id);
  EXPECT(n1t >= 3 && tw1[1].cp_start == 1 && tw1[1].cp_end == ws_n + 7);  // the special token takes the run (lstrip)
  pii_tokens_free(tw1);
  pii_tokens_free(tw2);
  free(wsx);
  free(big);
  selftest_model_table(m);
  pii_model_free(m);

  // malformed files: truncated and mutated model files and tokenizer files must fail cleanly (or load) without crashing
  uint8_t *c = malloc(n1 > n2 ? n1 : n2);
  for (int k = 0; k < 40; k++) {
    size_t cut = (size_t)(next_rand() % n1);
    pii_model *x1 = pii_model_load(a, cut, b, n2, err, sizeof err);
    EXPECT(!x1 && err[0]);
    pii_model_free(x1);
    cut = (size_t)(next_rand() % n2);
    pii_model *x2 = pii_model_load(a, n1, b, cut, err, sizeof err);
    EXPECT(!x2 && err[0]);
    pii_model_free(x2);
  }
  for (int k = 0; k < 60; k++) {
    memcpy(c, a, n1);
    // mostly in the first 64 KB (graph structure, names, small tensors), sometimes anywhere
    for (int r = 0; r < 4; r++) {
      size_t at = (size_t)(next_rand() % (k % 3 ? (n1 < 65536 ? n1 : 65536) : n1));
      c[at] = (uint8_t)next_rand();
    }
    pii_model *x1 = pii_model_load(c, n1, b, n2, err, sizeof err);
    if (x1) {
      EXPECT(pii_detect(x1, text, strlen(text), NULL, &s, &n) == PII_OK || 1);
      pii_spans_free(s);
    }
    pii_model_free(x1);
  }
  free(c);
  free(a);
  free(b);
}

// ---------------------------------------------------------------- fuzz
// N mutated model files (truncations, bit flips anywhere or in the first 64 KB, random byte runs), N / 2 mutated tokenizer
// files (truncations, byte and bit flips, digit swaps) and 2N random byte strings. Every file must load or fail with a
// message, never crash; spans and tokens must stay inside the input. Run it under ASan + UBSan (build/asan/pii_cli).
static int fuzz_load(const uint8_t *a, size_t na, const uint8_t *b, size_t nb, int *loaded) {
  uint8_t *ca = malloc(na ? na : 1), *cb = malloc(nb ? nb : 1);  // exact-size copies, so ASan sees reads past the end
  memcpy(ca, a, na);
  memcpy(cb, b, nb);
  char err[256] = "";
  pii_model *m = pii_model_load(ca, na, cb, nb, err, sizeof err);
  free(ca);
  free(cb);
  if (!m) return err[0] ? 0 : 1;
  ++*loaded;
  const char *t = "Kim Min-su (kim@example.com) \xf0\x9f\x98\x80 e\xcc\x81 \xff\xc3 \xed\xa0\x80 end";
  pii_span *s;
  pii_token *tk;
  size_t n;
  pii_detect(m, t, strlen(t), NULL, &s, &n);
  pii_spans_free(s);
  pii_tokenize(m, t, strlen(t), &tk, &n);
  pii_tokens_free(tk);
  pii_model_free(m);
  return 0;
}

static int fuzz(const char *onnx, const char *tok, int N) {
  size_t n1, n2;
  uint8_t *a = read_file(onnx, &n1), *b = read_file(tok, &n2);
  if (!a || !b || !n1 || !n2) return 2;
  uint8_t *c = malloc(n1 > n2 ? n1 : n2);
  int silent = 0, bad = 0, onnx_ok = 0, tok_ok = 0;
  for (int k = 0; k < N; k++) {
    memcpy(c, a, n1);
    size_t len = n1;
    if (k % 4 == 0) len = (size_t)(next_rand() % n1);
    else
      for (int r = 0, flips = 1 + (int)(next_rand() % 8); r < flips; r++) {
        size_t at = (size_t)(next_rand() % (k % 4 == 2 && n1 > 65536 ? 65536 : n1));
        if (k % 4 == 3)
          for (size_t q = at, run = 1 + next_rand() % 32; q < at + run && q < n1; q++) c[q] = (uint8_t)next_rand();
        else c[at] ^= (uint8_t)(1u << (next_rand() % 8));
      }
    silent += fuzz_load(c, len, b, n2, &onnx_ok);
  }
  for (int k = 0; k < N / 2; k++) {
    memcpy(c, b, n2);
    size_t len = n2;
    if (k % 4 == 0) len = (size_t)(next_rand() % n2);
    else if (k % 4 == 1)
      for (int r = 0; r < 4; r++) c[next_rand() % n2] = (uint8_t)next_rand();
    else if (k % 4 == 2)
      for (int r = 0; r < 2; r++) c[next_rand() % (n2 < 4096 ? n2 : 4096)] ^= (uint8_t)(1u << (next_rand() % 8));
    else
      for (size_t q = 0; q < n2; q++)
        if (c[q] >= '0' && c[q] <= '9' && next_rand() % 500 == 0) c[q] = (uint8_t)('0' + next_rand() % 10);
    silent += fuzz_load(a, n1, c, len, &tok_ok);
  }
  char err[256];
  pii_model *m = pii_model_load(a, n1, b, n2, err, sizeof err);
  if (!m) return 2;
  static const char pool[] = {0, ' ', '\t', '\n', '\r', (char)0x80, (char)0xbf, (char)0xc2, (char)0xe0, (char)0xed, (char)0xf0, (char)0xf4,
                              (char)0xff, 'a', 'Z', '9', '@', '.', '-'};
  for (int k = 0; k < 2 * N; k++) {
    size_t len = (size_t)(next_rand() % 1024);
    char *x = malloc(len ? len : 1);
    for (size_t q = 0; q < len; q++) x[q] = k % 2 ? (char)next_rand() : pool[next_rand() % sizeof pool];
    pii_span *s;
    pii_token *tk;
    size_t n;
    if (pii_detect(m, x, len, NULL, &s, &n)) bad++;
    for (size_t i = 0; i < n; i++)
      bad += s[i].byte_end > len || s[i].byte_start >= s[i].byte_end || (i && s[i].byte_start < s[i - 1].byte_end) ||
             s[i].cp_start > s[i].u16_start || s[i].u16_start > s[i].byte_start;
    pii_spans_free(s);
    if (pii_tokenize(m, x, len, &tk, &n)) bad++;
    for (size_t i = 0; i < n; i++) bad += tk[i].byte_end > len || tk[i].byte_start > tk[i].byte_end;
    pii_tokens_free(tk);
    free(x);
  }
  pii_model_free(m);
  printf("{\"model_files\": %d, \"loaded\": %d, \"tokenizer_files\": %d, \"loaded_tok\": %d, \"texts\": %d, "
         "\"failures_without_message\": %d, \"bad_results\": %d, \"ok\": %s}\n",
         N, onnx_ok, N / 2, tok_ok, 2 * N, silent, bad, silent || bad ? "false" : "true");
  free(a);
  free(b);
  free(c);
  return silent || bad;
}

int main(int argc, char **argv) {
  if (argc >= 5 && !strcmp(argv[1], "fuzz")) return fuzz(argv[2], argv[3], atoi(argv[4]));
  if (argc >= 5 && !strcmp(argv[1], "verify")) {
    size_t n;
    uint8_t *b = read_file(argv[2], &n);
    int rc = b ? pii_verify_file(b, n, argv[3], argv[4]) : PII_ERR_ARG;
    printf("%s\n", rc == PII_OK ? "ok" : "mismatch");
    free(b);
    return rc == PII_OK ? 0 : 1;
  }
  if (argc >= 2 && !strcmp(argv[1], "info")) {
    printf("{\"version\": \"%s\", \"build\": \"%s\", \"fma\": %s}\n", pii_version(), pii_build_info(),
           strstr(pii_build_info(), "fma") ? "true" : "false");
    return 0;
  }
  if (argc >= 2 && !strcmp(argv[1], "selftest")) {
    selftest_combine();
    selftest_combine_many();
    selftest_static();
    selftest_snap();
    selftest_table_parse();
    selftest_table_apply();
    selftest_table_vote();
    if (argc >= 4) selftest_model(argv[2], argv[3]);
    printf("%s\n", fails ? "failed" : "ok");
    return fails != 0;
  }
  if (argc >= 2 && !strcmp(argv[1], "apply")) {
    size_t nt;
    input *texts = read_texts(&nt);
    int rc = 0;
    for (size_t i = 0; i < nt; i++) {
      pii_text tx;
      pii_cspan *out = NULL;
      size_t n = 0;
      if (pii_text_init(&tx, texts[i].s, texts[i].n) ||
          pii_table_apply(&tx, texts[i].spans, texts[i].n_spans, texts[i].delimiter, texts[i].header, texts[i].vote, texts[i].labels,
                          &out, &n)) {
        rc = 1;
        break;
      }
      printf("{\"spans\": [");
      for (size_t k = 0; k < n; k++)
        printf("%s[%zu, %zu, \"%s\", %d]", k ? ", " : "", out[k].start, out[k].end, pii_label_name(out[k].label), out[k].source);
      printf("]}\n");
      free(out);
      pii_text_free(&tx);
    }
    for (size_t i = 0; i < nt; i++) free(texts[i].s), free(texts[i].extra), free(texts[i].spans);
    free(texts);
    return rc;
  }
  if (argc < 4) {
    fprintf(stderr, "usage: pii_cli tokenize|detect|table|apply|threads|bench|verify|selftest|fuzz|info ... (see source)\n");
    return 2;
  }
  const char *cmd = argv[1];
  pii_model *m = load(argv[2], argv[3]);
  size_t nt;
  input *texts = read_texts(&nt);
  pii_options opt;
  pii_options_init(&opt);
  int rc = 0;

  if (!strcmp(cmd, "tokenize")) {
    for (size_t i = 0; i < nt; i++) {
      pii_token *t;
      size_t n;
      if (pii_tokenize(m, texts[i].s, texts[i].n, &t, &n)) {
        rc = 1;
        break;
      }
      printf("{\"ids\": [");
      for (size_t k = 0; k < n; k++) printf("%s%d", k ? ", " : "", t[k].id);
      printf("], \"cp\": [");
      for (size_t k = 0; k < n; k++) printf("%s[%zu, %zu]", k ? ", " : "", t[k].cp_start, t[k].cp_end);
      printf("], \"u16\": [");
      for (size_t k = 0; k < n; k++) printf("%s[%zu, %zu]", k ? ", " : "", t[k].u16_start, t[k].u16_end);
      printf("], \"u8\": [");
      for (size_t k = 0; k < n; k++) printf("%s[%zu, %zu]", k ? ", " : "", t[k].byte_start, t[k].byte_end);
      printf("]}\n");
      pii_tokens_free(t);
    }
  } else if (!strcmp(cmd, "detect")) {
    if (argc > 4) opt.sensitivity = atof(argv[4]);
    for (size_t i = 0; i < nt; i++) {
      pii_span *s;
      size_t n;
      const pii_options o = line_options(&opt, &texts[i]);
      if (pii_detect(m, texts[i].s, texts[i].n, &o, &s, &n)) {
        rc = 1;
        break;
      }
      print_spans(s, n, 0);
      pii_spans_free(s);
    }
  } else if (!strcmp(cmd, "table")) {
    if (argc > 4) opt.sensitivity = atof(argv[4]);
    for (size_t i = 0; i < nt; i++) {
      pii_span *s;
      size_t n;
      const pii_options o = line_options(&opt, &texts[i]);
      pii_table_options t;
      pii_table_options_init(&t);
      t.delimiter = texts[i].delimiter;
      t.header = texts[i].header;
      t.vote = texts[i].vote;
      t.context = texts[i].context;
      if (pii_detect_table(m, texts[i].s, texts[i].n, &o, &t, &s, &n)) {
        rc = 1;
        break;
      }
      print_spans(s, n, 1);
      pii_spans_free(s);
    }
  } else if (!strcmp(cmd, "threads") && argc > 4) {
    int nth = atoi(argv[4]);
    if (argc > 5) opt.sensitivity = atof(argv[5]);
    if (nth < 1 || nth > 64) nth = 4;
    job ref = {m, texts, nt, &opt, calloc(nt + 1, sizeof(char *)), 0};
    run_job(&ref);
    job jobs[64];
    pthread_t th[64];
    double t0 = now_ms();
    for (int k = 0; k < nth; k++) {
      jobs[k] = ref;
      jobs[k].out = calloc(nt + 1, sizeof(char *));
      pthread_create(&th[k], NULL, run_job, &jobs[k]);
    }
    for (int k = 0; k < nth; k++) pthread_join(th[k], NULL);
    double ms = now_ms() - t0;
    size_t diff = 0;
    for (int k = 0; k < nth; k++) {
      for (size_t i = 0; i < nt; i++) {
        if (!jobs[k].out[i] || !ref.out[i] || strcmp(jobs[k].out[i], ref.out[i])) diff++;
        free(jobs[k].out[i]);
      }
      free(jobs[k].out);
      rc |= jobs[k].fail;
    }
    for (size_t i = 0; i < nt; i++) free(ref.out[i]);
    free(ref.out);
    printf("{\"threads\": %d, \"texts\": %zu, \"differences\": %zu, \"ms\": %.1f, \"ok\": %s}\n", nth, nt, diff, ms, diff || rc ? "false" : "true");
    rc |= diff != 0;
  } else if (!strcmp(cmd, "bench")) {
    // every input once (warm-up, token counts), then timed passes: all inputs, and only the short ones (<= 64 tokens)
    size_t *ntok = calloc(nt + 1, sizeof *ntok), tokens = 0, nshort = 0, tshort = 0;
    for (size_t i = 0; i < nt; i++) {
      pii_token *t;
      size_t n;
      pii_tokenize(m, texts[i].s, texts[i].n, &t, &ntok[i]);
      pii_tokens_free(t);
      tokens += ntok[i];
      if (ntok[i] <= 64) nshort++, tshort += ntok[i];
      pii_span *s;
      pii_detect(m, texts[i].s, texts[i].n, &opt, &s, &n);
      pii_spans_free(s);
    }
    const int reps = 3;
    double all_ms = 0, short_ms = 0;
    for (int r = 0; r < reps; r++)
      for (size_t i = 0; i < nt; i++) {
        pii_span *s;
        size_t n;
        double a = now_ms();
        pii_detect(m, texts[i].s, texts[i].n, &opt, &s, &n);
        double d = now_ms() - a;
        pii_spans_free(s);
        all_ms += d;
        if (ntok[i] <= 64) short_ms += d;
      }
    // a long text: inputs joined until 1,000 tokens
    size_t cap = 1 << 16, len = 0, nlong = 0;
    char *big = malloc(cap);
    for (size_t i = 0; nlong < 1000 && nt; i = (i + 1) % nt) {
      while (len + texts[i].n + 2 > cap) big = realloc(big, cap *= 2);
      memcpy(big + len, texts[i].s, texts[i].n);
      len += texts[i].n;
      big[len++] = '\n';
      pii_token *t;
      pii_tokenize(m, big, len, &t, &nlong);
      pii_tokens_free(t);
    }
    double best = 1e30;
    for (int r = 0; r < 5; r++) {
      pii_span *s;
      size_t n;
      double a = now_ms();
      pii_detect(m, big, len, &opt, &s, &n);
      double d = now_ms() - a;
      if (d < best) best = d;
      pii_spans_free(s);
    }
    printf("{\"build\": \"%s\", \"texts\": %zu, \"avg_tokens\": %.1f, \"ms_per_text\": %.2f, \"short_texts\": %zu, "
           "\"short_avg_tokens\": %.1f, \"ms_per_short_text\": %.2f, \"long_tokens\": %zu, \"ms_long\": %.1f}\n",
           pii_build_info(), nt, nt ? (double)tokens / nt : 0.0, all_ms / reps / (nt ? nt : 1), nshort,
           nshort ? (double)tshort / nshort : 0.0, short_ms / reps / (nshort ? nshort : 1), nlong, best);
    free(big);
    free(ntok);
  } else {
    fprintf(stderr, "unknown command %s\n", cmd);
    rc = 2;
  }
  for (size_t i = 0; i < nt; i++) free(texts[i].s), free(texts[i].extra), free(texts[i].spans);
  free(texts);
  pii_model_free(m);
  return rc;
}
