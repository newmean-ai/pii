// Test and benchmark CLI for the core. Reads JSON lines with a "text" field on stdin, writes one JSON line per input.
//
//   pii_cli tokenize MODEL.onnx TOKENIZER.json            {"ids": [...], "cp": [[s, e], ...], "u16": [...], "u8": [...]}
//   pii_cli detect MODEL.onnx TOKENIZER.json [SENS]       {"spans": [[cp_s, cp_e, "LABEL", u16_s, u16_e, u8_s, u8_e], ...]}
//   pii_cli threads MODEL.onnx TOKENIZER.json N [SENS]    every text in N threads at once; "ok" when all equal one thread
//   pii_cli bench MODEL.onnx TOKENIZER.json               ms per text (all inputs, inputs of <= 64 tokens, a 1,000-token text)
//   pii_cli verify FILE SIZE KIND                         checks FILE against the pinned size and SHA-256
//   pii_cli info                                          {"version": ..., "build": "neon", "fma": false}
//   pii_cli selftest [MODEL.onnx TOKENIZER.json]          API checks (combine as js/test/combine.test.ts, arguments,
//                                                         malformed and truncated model files); "ok" or the failures
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
} input;

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

static void print_spans(const pii_span *s, size_t n) {
  printf("{\"spans\": [");
  for (size_t i = 0; i < n; i++)
    printf("%s[%zu, %zu, \"%s\", %zu, %zu, %zu, %zu]", i ? ", " : "", s[i].cp_start, s[i].cp_end, s[i].label_name, s[i].u16_start,
           s[i].u16_end, s[i].byte_start, s[i].byte_end);
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
  EXPECT(pii_combine(in, 2, PII_LABELS_ALL, out) == 1 && out[0].start == 3 && out[0].end == 14 && out[0].label == PII_PHONE &&
         out[0].source == (PII_SOURCE_MODEL | PII_SOURCE_REGEX));
  in[0] = cs(0, 20, PII_URL_L2, PII_SOURCE_MODEL);
  in[1] = cs(0, 20, PII_URL, PII_SOURCE_REGEX);
  EXPECT(pii_combine(in, 2, PII_LABELS_LEVEL1, out) == 0);
  in[0] = cs(0, 9, PII_ORG, PII_SOURCE_MODEL);
  in[1] = cs(0, 16, PII_EMAIL, PII_SOURCE_REGEX);
  EXPECT(pii_combine(in, 2, PII_LABEL_BIT(PII_EMAIL), out) == 1 && out[0].start == 0 && out[0].end == 16 &&
         out[0].label == PII_EMAIL && out[0].source == PII_SOURCE_REGEX);
}

// More than 32 spans (merge sort) in reverse order; the result must equal the one of the sorted input.
static void selftest_combine_many(void) {
  enum { N = 100 };
  pii_cspan in[N], out[N];
  for (size_t i = 0; i < N; i++) in[i] = cs((N - 1 - i) * 10, (N - 1 - i) * 10 + 5, (int)(i % PII_LABEL_COUNT), i % 3 ? PII_SOURCE_MODEL : PII_SOURCE_REGEX);
  in[N - 1] = cs(0, 25, PII_EMAIL, PII_SOURCE_REGEX);  // overlaps 10-15 and 20-25: one region
  size_t n = pii_combine(in, N, PII_LABELS_ALL, out);
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
}

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static uint64_t next_rand(void) {
  rng ^= rng << 13;
  rng ^= rng >> 7;
  rng ^= rng << 17;
  return rng;
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
    if (argc >= 4) selftest_model(argv[2], argv[3]);
    printf("%s\n", fails ? "failed" : "ok");
    return fails != 0;
  }
  if (argc < 4) {
    fprintf(stderr, "usage: pii_cli tokenize|detect|threads|bench|verify|selftest|fuzz|info ... (see source)\n");
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
      if (pii_detect(m, texts[i].s, texts[i].n, &opt, &s, &n)) {
        rc = 1;
        break;
      }
      print_spans(s, n);
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
  for (size_t i = 0; i < nt; i++) free(texts[i].s);
  free(texts);
  pii_model_free(m);
  return rc;
}
