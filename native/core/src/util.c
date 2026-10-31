// Small helpers: errors, aligned memory, growable arrays, a minimal JSON reader, base64, UTF-8 text with offset tables.
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "internal.h"

void pii_set_err(char *err, size_t errlen, const char *fmt, ...) {
  if (!err || !errlen) return;
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(err, errlen, fmt, ap);
  va_end(ap);
}

// 64-byte aligned, zeroed. The offset to the malloc'd block is kept just before the returned pointer.
void *pii_aligned_alloc(size_t n) {
  if (n > SIZE_MAX - 128) return NULL;
  uint8_t *raw = calloc(1, n + 64 + sizeof(size_t));
  if (!raw) return NULL;
  uintptr_t a = ((uintptr_t)raw + sizeof(size_t) + 63) & ~(uintptr_t)63;
  size_t off = (size_t)(a - (uintptr_t)raw);
  memcpy((uint8_t *)a - sizeof(size_t), &off, sizeof off);
  return (void *)a;
}

void pii_aligned_free(void *p) {
  if (!p) return;
  size_t off;
  memcpy(&off, (uint8_t *)p - sizeof(size_t), sizeof off);
  free((uint8_t *)p - off);
}

// ---------------------------------------------------------------- vectors
void pii_vec_init(pii_vec *v, size_t elem) {
  v->p = NULL;
  v->n = v->cap = 0;
  v->elem = elem;
}

int pii_vec_reserve(pii_vec *v, size_t n) {
  if (n <= v->cap) return 0;
  size_t c = v->cap ? v->cap : 16;
  while (c < n) c *= 2;
  void *p = realloc(v->p, c * v->elem);
  if (!p) return -1;
  v->p = p;
  v->cap = c;
  return 0;
}

void *pii_vec_push(pii_vec *v) {
  if (pii_vec_reserve(v, v->n + 1)) return NULL;
  void *e = (uint8_t *)v->p + v->n * v->elem;
  memset(e, 0, v->elem);
  v->n++;
  return e;
}

void pii_vec_free(pii_vec *v) {
  free(v->p);
  v->p = NULL;
  v->n = v->cap = 0;
}

// ---------------------------------------------------------------- JSON
typedef struct {
  const char *p, *end;
  int depth;
} jp;

static void ws(jp *j) {
  while (j->p < j->end && (*j->p == ' ' || *j->p == '\t' || *j->p == '\n' || *j->p == '\r')) j->p++;
}

static int hex4(jp *j, uint32_t *out) {
  if (j->end - j->p < 4) return -1;
  uint32_t v = 0;
  for (int i = 0; i < 4; i++) {
    char c = j->p[i];
    v <<= 4;
    if (c >= '0' && c <= '9') v |= (uint32_t)(c - '0');
    else if (c >= 'a' && c <= 'f') v |= (uint32_t)(c - 'a' + 10);
    else if (c >= 'A' && c <= 'F') v |= (uint32_t)(c - 'A' + 10);
    else return -1;
  }
  j->p += 4;
  *out = v;
  return 0;
}

static size_t put_utf8(char *o, uint32_t c) {
  if (c < 0x80) {
    o[0] = (char)c;
    return 1;
  }
  if (c < 0x800) {
    o[0] = (char)(0xc0 | (c >> 6));
    o[1] = (char)(0x80 | (c & 0x3f));
    return 2;
  }
  if (c < 0x10000) {
    o[0] = (char)(0xe0 | (c >> 12));
    o[1] = (char)(0x80 | ((c >> 6) & 0x3f));
    o[2] = (char)(0x80 | (c & 0x3f));
    return 3;
  }
  o[0] = (char)(0xf0 | (c >> 18));
  o[1] = (char)(0x80 | ((c >> 12) & 0x3f));
  o[2] = (char)(0x80 | ((c >> 6) & 0x3f));
  o[3] = (char)(0x80 | (c & 0x3f));
  return 4;
}

static int jstring(jp *j, char **out, size_t *len) {
  j->p++;  // opening quote
  const char *s = j->p;
  while (j->p < j->end && *j->p != '"') j->p += (*j->p == '\\') ? 2 : 1;
  if (j->p >= j->end) return -1;
  size_t raw = (size_t)(j->p - s);
  char *o = malloc(raw + 1), *w = o;  // escapes never expand
  if (!o) return -1;
  jp r = {s, j->p, 0};
  while (r.p < r.end) {
    char c = *r.p++;
    if (c != '\\') {
      *w++ = c;
      continue;
    }
    if (r.p >= r.end) goto bad;
    c = *r.p++;
    switch (c) {
      case '"': *w++ = '"'; break;
      case '\\': *w++ = '\\'; break;
      case '/': *w++ = '/'; break;
      case 'b': *w++ = '\b'; break;
      case 'f': *w++ = '\f'; break;
      case 'n': *w++ = '\n'; break;
      case 'r': *w++ = '\r'; break;
      case 't': *w++ = '\t'; break;
      case 'u': {
        uint32_t u;
        if (hex4(&r, &u)) goto bad;
        if (u >= 0xd800 && u < 0xdc00 && r.end - r.p >= 6 && r.p[0] == '\\' && r.p[1] == 'u') {
          jp t = {r.p + 2, r.end, 0};
          uint32_t lo;
          if (!hex4(&t, &lo) && lo >= 0xdc00 && lo < 0xe000) {
            u = 0x10000 + ((u - 0xd800) << 10) + (lo - 0xdc00);
            r.p = t.p;
          }
        }
        w += put_utf8(w, u);  // a lone surrogate is kept as its 3-byte form
        break;
      }
      default: goto bad;
    }
  }
  *w = 0;
  *out = o;
  *len = (size_t)(w - o);
  j->p++;  // closing quote
  return 0;
bad:
  free(o);
  return -1;
}

static int jvalue(jp *j, pii_json *v);

static int jpush(pii_json *v, size_t *cap) {
  if (v->n < *cap) return 0;
  size_t c = *cap ? *cap * 2 : 8;
  pii_json *it = realloc(v->items, c * sizeof *it);
  if (!it) return -1;
  v->items = it;
  if (v->type == PJ_OBJ) {
    char **k = realloc(v->keys, c * sizeof *k);
    if (!k) return -1;
    v->keys = k;
  }
  *cap = c;
  return 0;
}

static int jvalue(jp *j, pii_json *v) {
  memset(v, 0, sizeof *v);
  ws(j);
  if (j->p >= j->end || ++j->depth > 64) return -1;
  char c = *j->p;
  int rc = 0;
  if (c == '"') {
    v->type = PJ_STR;
    rc = jstring(j, &v->str, &v->len);
  } else if (c == '{' || c == '[') {
    v->type = c == '{' ? PJ_OBJ : PJ_ARR;
    char close = c == '{' ? '}' : ']';
    size_t cap = 0;
    j->p++;
    ws(j);
    if (j->p < j->end && *j->p == close) j->p++;
    else
      for (;;) {
        if (jpush(v, &cap)) return -1;
        char *key = NULL;
        if (v->type == PJ_OBJ) {
          size_t kl;
          ws(j);
          if (j->p >= j->end || *j->p != '"' || jstring(j, &key, &kl)) return -1;
          ws(j);
          if (j->p >= j->end || *j->p != ':') {
            free(key);
            return -1;
          }
          j->p++;
          v->keys[v->n] = key;
        }
        if (jvalue(j, &v->items[v->n])) {
          v->n++;  // freed with the parent
          return -1;
        }
        v->n++;
        ws(j);
        if (j->p < j->end && *j->p == ',') {
          j->p++;
          continue;
        }
        if (j->p < j->end && *j->p == close) {
          j->p++;
          break;
        }
        return -1;
      }
  } else if (c == 't' && j->end - j->p >= 4 && !memcmp(j->p, "true", 4)) {
    v->type = PJ_BOOL;
    v->num = 1;
    j->p += 4;
  } else if (c == 'f' && j->end - j->p >= 5 && !memcmp(j->p, "false", 5)) {
    v->type = PJ_BOOL;
    j->p += 5;
  } else if (c == 'n' && j->end - j->p >= 4 && !memcmp(j->p, "null", 4)) {
    j->p += 4;
  } else if (c == '-' || (c >= '0' && c <= '9')) {
    char buf[64];
    size_t n = 0;
    while (j->p < j->end && n < sizeof buf - 1 && strchr("+-0123456789.eE", *j->p)) buf[n++] = *j->p++;
    buf[n] = 0;
    char *e;
    v->type = PJ_NUM;
    v->num = strtod(buf, &e);
    if (e != buf + n) rc = -1;
  } else rc = -1;
  j->depth--;
  return rc;
}

int pii_json_parse(const char *s, size_t n, pii_json *out) {
  jp j = {s, s + n, 0};
  if (n >= 3 && (uint8_t)s[0] == 0xef && (uint8_t)s[1] == 0xbb && (uint8_t)s[2] == 0xbf) j.p += 3;  // BOM
  if (jvalue(&j, out)) {
    pii_json_free(out);
    return -1;
  }
  ws(&j);
  if (j.p != j.end) {
    pii_json_free(out);
    return -1;
  }
  return 0;
}

void pii_json_free(pii_json *v) {
  free(v->str);
  for (size_t i = 0; i < v->n; i++) {
    pii_json_free(&v->items[i]);
    if (v->keys) free(v->keys[i]);
  }
  free(v->items);
  free(v->keys);
  memset(v, 0, sizeof *v);
}

const pii_json *pii_json_get(const pii_json *o, const char *key) {
  if (!o || o->type != PJ_OBJ) return NULL;
  const pii_json *hit = NULL;
  for (size_t i = 0; i < o->n; i++)
    if (strcmp(o->keys[i], key) == 0) hit = &o->items[i];  // the last duplicate wins (JSON.parse)
  return hit;
}

// ---------------------------------------------------------------- base64 (standard alphabet; whitespace ignored)
uint8_t *pii_base64(const char *s, size_t n, size_t *out_len) {
  uint8_t *o = malloc(n / 4 * 3 + 3), *w = o;
  if (!o) return NULL;
  uint32_t acc = 0;
  int bits = 0;
  for (size_t i = 0; i < n; i++) {
    char c = s[i];
    int v;
    if (c >= 'A' && c <= 'Z') v = c - 'A';
    else if (c >= 'a' && c <= 'z') v = c - 'a' + 26;
    else if (c >= '0' && c <= '9') v = c - '0' + 52;
    else if (c == '+') v = 62;
    else if (c == '/') v = 63;
    else if (c == '=') break;
    else if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
    else {
      free(o);
      return NULL;
    }
    acc = (acc << 6) | (uint32_t)v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      *w++ = (uint8_t)(acc >> bits);
    }
  }
  *out_len = (size_t)(w - o);
  return o;
}

// ---------------------------------------------------------------- text
// Decodes UTF-8 leniently: surrogate code points in 3-byte form (WTF-8, from Python's "surrogatepass") are kept; any
// other invalid byte becomes one U+FFFD of one byte, so offsets always map back to the input.
int pii_text_init(pii_text *t, const char *utf8, size_t len) {
  memset(t, 0, sizeof *t);
  t->cp = malloc((len + 1) * sizeof *t->cp);
  t->u8 = malloc((len + 1) * sizeof *t->u8);
  t->u16 = malloc((len + 1) * sizeof *t->u16);
  if (!t->cp || !t->u8 || !t->u16) {
    pii_text_free(t);
    return -1;
  }
  const uint8_t *s = (const uint8_t *)utf8;
  size_t i = 0, n = 0, u16 = 0;
  while (i < len) {
    uint8_t c = s[i];
    uint32_t cp = 0xfffd;
    size_t k = 1;
    if (c < 0x80) cp = c;
    else if (c >= 0xc2 && c < 0xe0 && i + 1 < len && (s[i + 1] & 0xc0) == 0x80) {
      cp = ((uint32_t)(c & 0x1f) << 6) | (s[i + 1] & 0x3f);
      k = 2;
    } else if (c >= 0xe0 && c < 0xf0 && i + 2 < len && (s[i + 1] & 0xc0) == 0x80 && (s[i + 2] & 0xc0) == 0x80) {
      uint32_t v = ((uint32_t)(c & 0x0f) << 12) | ((uint32_t)(s[i + 1] & 0x3f) << 6) | (s[i + 2] & 0x3f);
      if (v >= 0x800) {
        cp = v;
        k = 3;
      }
    } else if (c >= 0xf0 && c < 0xf5 && i + 3 < len && (s[i + 1] & 0xc0) == 0x80 && (s[i + 2] & 0xc0) == 0x80 && (s[i + 3] & 0xc0) == 0x80) {
      uint32_t v = ((uint32_t)(c & 0x07) << 18) | ((uint32_t)(s[i + 1] & 0x3f) << 12) | ((uint32_t)(s[i + 2] & 0x3f) << 6) | (s[i + 3] & 0x3f);
      if (v >= 0x10000 && v < 0x110000) {
        cp = v;
        k = 4;
      }
    }
    t->cp[n] = cp;
    t->u8[n] = i;
    t->u16[n] = u16;
    n++;
    i += k;
    u16 += cp >= 0x10000 ? 2 : 1;
  }
  t->u8[n] = i;
  t->u16[n] = u16;
  t->n = n;
  return 0;
}

void pii_text_free(pii_text *t) {
  free(t->cp);
  free(t->u8);
  free(t->u16);
  memset(t, 0, sizeof *t);
}

// ECMAScript WhiteSpace and LineTerminator (\s, String.prototype.trim), Unicode 17
int pii_is_js_space(uint32_t c) {
  return (c >= 9 && c <= 13) || c == 0x20 || c == 0xa0 || c == 0x1680 || (c >= 0x2000 && c <= 0x200a) || c == 0x2028 ||
         c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x3000 || c == 0xfeff;
}
