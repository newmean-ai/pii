// Loads the published model_q4/q4e.onnx files: a minimal protobuf reader for the parts of ModelProto the engine needs,
// and a port of js/src/engines/onnx-weights.ts (itself a port of export_wasm_weights.py) that lays the initializers out
// as a .piiw image. Same checks: anything unexpected is an error.
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "internal.h"

enum { INT4 = 22, UINT8 = 2, FLOAT = 1, INT64 = 7 };

typedef struct {
  const uint8_t *p;
  size_t n;
} bytes;

typedef struct {
  bytes name;
  int64_t dims[8];
  int ndims;
  int data_type;
  bytes raw;       // raw_data, or a re-encoding below
  uint8_t *owned;  // re-encoded float_data / int32_data / int64_data
} tensor;

typedef struct {
  bytes name;
  int has_f, has_i;
  float f;
  int64_t i;
  tensor *t;
} attr;

typedef struct {
  bytes op;
  bytes in[8], out[4];
  int nin, nout;
  attr *attrs;
  int nattrs;
} node;

typedef struct {
  node *nodes;
  size_t nnodes, capnodes;
  tensor *inits;
  size_t ninits, capinits;
  char *err;
  size_t errlen;
  int failed;
} graph;

static int fail(graph *g, const char *fmt, ...) {
  if (!g->failed && g->err && g->errlen) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g->err, g->errlen, fmt, ap);
    va_end(ap);
  }
  g->failed = 1;
  return -1;
}

static int beq(bytes b, const char *s) { return b.n == strlen(s) && memcmp(b.p, s, b.n) == 0; }
static int bhas(bytes b, const char *s) {
  size_t m = strlen(s);
  for (size_t i = 0; i + m <= b.n; i++)
    if (memcmp(b.p + i, s, m) == 0) return 1;
  return 0;
}

// ---------------------------------------------------------------- protobuf
typedef struct {
  const uint8_t *p, *end;
  int bad;
} rd;

static uint64_t varint(rd *r) {
  uint64_t x = 0;
  for (int sh = 0; sh < 70; sh += 7) {
    if (r->p >= r->end) {
      r->bad = 1;
      return 0;
    }
    uint8_t c = *r->p++;
    if (sh < 64) x |= (uint64_t)(c & 0x7f) << sh;
    if (c < 0x80) return x;
  }
  r->bad = 1;
  return 0;
}

// Next field: returns 0 at the end. For wire type 2, *sub is the payload; other values are left for the caller to read
// (or skip with skip()).
static int next(rd *r, int *field, int *wire, rd *sub) {
  if (r->bad || r->p >= r->end) return 0;
  uint64_t key = varint(r);
  *field = (int)(key >> 3);
  *wire = (int)(key & 7);
  if (*wire == 2) {
    uint64_t len = varint(r);
    if (r->bad || len > (uint64_t)(r->end - r->p)) {
      r->bad = 1;
      return 0;
    }
    sub->p = r->p;
    sub->end = r->p + len;
    sub->bad = 0;
    r->p += len;
  }
  return !r->bad;
}

static void skip(rd *r, int wire) {
  size_t n = wire == 1 ? 8 : wire == 5 ? 4 : 0;
  if (wire == 0) varint(r);
  else if (n && (size_t)(r->end - r->p) >= n) r->p += n;
  else r->bad = 1;  // groups (3, 4) and truncated values
}

static bytes rest(rd *r) { return (bytes){r->p, (size_t)(r->end - r->p)}; }

typedef struct {
  int64_t *v;
  size_t n, cap;
} ints;

static int ints_push(ints *a, int64_t x) {
  if (a->n == a->cap) {
    size_t c = a->cap ? a->cap * 2 : 16;
    int64_t *v = realloc(a->v, c * sizeof *v);
    if (!v) return -1;
    a->v = v;
    a->cap = c;
  }
  a->v[a->n++] = x;
  return 0;
}

static int packed(rd *s, ints *out) {
  while (s->p < s->end && !s->bad)
    if (ints_push(out, (int64_t)varint(s))) return -1;
  return s->bad ? -1 : 0;
}

static void tensor_free(tensor *t) {
  free(t->owned);
  t->owned = NULL;
}

static void attr_tensor_free(tensor *t) {  // a heap tensor of an attribute
  if (!t) return;
  tensor_free(t);
  free(t);
}

static void node_free(node *n) {
  for (int k = 0; k < n->nattrs; k++) attr_tensor_free(n->attrs[k].t);
  free(n->attrs);
  n->attrs = NULL;
  n->nattrs = 0;
}

static int read_tensor(graph *g, rd *r, tensor *t) {
  memset(t, 0, sizeof *t);
  ints dims = {0}, i32 = {0}, i64 = {0};
  bytes floats = {0};
  int f, w, rc = 0;
  rd s;
  while (next(r, &f, &w, &s)) {
    if (f == 1) {
      if (w == 2 ? packed(&s, &dims) : ints_push(&dims, (int64_t)varint(r))) rc = -1;
    } else if (f == 2 && w == 0) t->data_type = (int)varint(r);
    else if (f == 8 && w == 2) t->name = rest(&s);
    else if (f == 9 && w == 2) t->raw = rest(&s);
    else if (f == 4 && w == 2) floats = rest(&s);
    else if (f == 5) {  // int32_data: also holds uint8 / int8 / int4 values when raw_data is not used
      if (w == 2 ? packed(&s, &i32) : ints_push(&i32, (int64_t)varint(r))) rc = -1;
    } else if (f == 7) {
      if (w == 2 ? packed(&s, &i64) : ints_push(&i64, (int64_t)varint(r))) rc = -1;
    } else if (f == 13) {
      rc = fail(g, "external tensor data is not supported");
    } else if (f == 14 && w == 0) {  // data_location: 1 = EXTERNAL
      if (varint(r) == 1) rc = fail(g, "external tensor data is not supported");
    } else if (w != 2) skip(r, w);
  }
  if (r->bad || (rc && !g->failed)) rc = fail(g, "malformed protobuf (tensor)");  // also a bad packed field
  if (!rc && dims.n > 8) rc = fail(g, "tensor with %zu dimensions", dims.n);
  if (!rc) {
    t->ndims = (int)dims.n;
    for (size_t i = 0; i < dims.n; i++) t->dims[i] = dims.v[i];
    if (!t->raw.n && floats.n) {
      t->raw = (bytes){floats.p, floats.n - floats.n % 4};
    }
    if (!t->raw.n && i32.n && t->data_type == UINT8) {
      t->owned = malloc(i32.n);
      if (!t->owned) rc = fail(g, "out of memory");
      else {
        for (size_t i = 0; i < i32.n; i++) t->owned[i] = (uint8_t)i32.v[i];
        t->raw = (bytes){t->owned, i32.n};
      }
    } else if (!t->raw.n && i32.n) {
      rc = fail(g, "%.*s: int32_data of type %d is not supported", (int)t->name.n, (const char *)t->name.p, t->data_type);
    }
    if (!rc && !t->raw.n && i64.n) {
      t->owned = malloc(i64.n * 8);
      if (!t->owned) rc = fail(g, "out of memory");
      else {
        memcpy(t->owned, i64.v, i64.n * 8);
        t->raw = (bytes){t->owned, i64.n * 8};
      }
    }
  }
  free(dims.v);
  free(i32.v);
  free(i64.v);
  return rc;
}

static int read_node(graph *g, rd *r, node *n) {
  memset(n, 0, sizeof *n);
  int f, w;
  rd s;
  while (next(r, &f, &w, &s)) {
    if (w != 2) {
      skip(r, w);
      continue;
    }
    if (f == 1) {
      if (n->nin < 8) n->in[n->nin] = rest(&s);
      n->nin++;
    } else if (f == 2) {
      if (n->nout < 4) n->out[n->nout] = rest(&s);
      n->nout++;
    } else if (f == 4) n->op = rest(&s);
    else if (f == 5) {
      attr a;
      memset(&a, 0, sizeof a);
      int g2, w2;
      rd s2;
      while (next(&s, &g2, &w2, &s2)) {
        if (g2 == 1 && w2 == 2) a.name = rest(&s2);
        else if (g2 == 2 && w2 == 5) {
          if (s.end - s.p < 4) {
            s.bad = 1;
            break;
          }
          memcpy(&a.f, s.p, 4);
          s.p += 4;
          a.has_f = 1;
        } else if (g2 == 3 && w2 == 0) {
          a.i = (int64_t)varint(&s);
          a.has_i = 1;
        } else if (g2 == 5 && w2 == 2) {
          attr_tensor_free(a.t);
          a.t = malloc(sizeof(tensor));
          if (!a.t) return fail(g, "out of memory");
          if (read_tensor(g, &s2, a.t)) {
            free(a.t);
            return -1;
          }
        } else if (w2 != 2) skip(&s, w2);
      }
      if (s.bad) {
        attr_tensor_free(a.t);
        return fail(g, "malformed protobuf (attribute)");
      }
      // later attributes with the same name replace earlier ones (Map.set)
      int k = 0;
      for (; k < n->nattrs; k++)
        if (n->attrs[k].name.n == a.name.n && memcmp(n->attrs[k].name.p, a.name.p, a.name.n) == 0) break;
      if (k == n->nattrs) {
        attr *na = realloc(n->attrs, (size_t)(n->nattrs + 1) * sizeof(attr));
        if (!na) {
          attr_tensor_free(a.t);
          return fail(g, "out of memory");
        }
        n->attrs = na;
        n->nattrs++;
      } else attr_tensor_free(n->attrs[k].t);
      n->attrs[k] = a;
    }
  }
  if (r->bad) return fail(g, "malformed protobuf (node)");
  if (n->nin > 8 || n->nout > 4) return fail(g, "node with too many inputs or outputs");
  return 0;
}

static void graph_free(graph *g) {
  for (size_t i = 0; i < g->nnodes; i++) node_free(&g->nodes[i]);
  for (size_t i = 0; i < g->ninits; i++) tensor_free(&g->inits[i]);
  free(g->nodes);
  free(g->inits);
}

static int read_graph(graph *g, const uint8_t *model, size_t n) {
  rd r = {model, model + n, 0}, s, s2;
  int f, w, f2, w2;
  while (next(&r, &f, &w, &s)) {
    if (w != 2) {
      skip(&r, w);
      continue;
    }
    if (f != 7) continue;  // ModelProto.graph
    while (next(&s, &f2, &w2, &s2)) {
      if (w2 != 2) {
        skip(&s, w2);
        continue;
      }
      if (f2 == 1) {
        if (g->nnodes == g->capnodes) {
          size_t c = g->capnodes ? g->capnodes * 2 : 256;
          node *nn = realloc(g->nodes, c * sizeof(node));
          if (!nn) return fail(g, "out of memory");
          g->nodes = nn;
          g->capnodes = c;
        }
        if (read_node(g, &s2, &g->nodes[g->nnodes])) {
          node_free(&g->nodes[g->nnodes]);  // the attributes read so far
          return -1;
        }
        g->nnodes++;
      } else if (f2 == 5) {
        if (g->ninits == g->capinits) {
          size_t c = g->capinits ? g->capinits * 2 : 256;
          tensor *nt = realloc(g->inits, c * sizeof(tensor));
          if (!nt) return fail(g, "out of memory");
          g->inits = nt;
          g->capinits = c;
        }
        if (read_tensor(g, &s2, &g->inits[g->ninits])) return -1;
        g->ninits++;
      }
    }
    if (s.bad) return fail(g, "malformed protobuf (graph)");
  }
  if (r.bad) return fail(g, "malformed protobuf (model)");
  return 0;
}

// Initializer by name; the last one wins (Map.set).
static tensor *find_init(graph *g, const char *name) {
  for (size_t i = g->ninits; i-- > 0;)
    if (beq(g->inits[i].name, name)) return &g->inits[i];
  return NULL;
}
static tensor *find_init_b(graph *g, bytes name) {
  for (size_t i = g->ninits; i-- > 0;)
    if (g->inits[i].name.n == name.n && memcmp(g->inits[i].name.p, name.p, name.n) == 0) return &g->inits[i];
  return NULL;
}
static tensor *need(graph *g, const char *name) {
  tensor *t = find_init(g, name);
  if (!t) fail(g, "missing initializer %s", name);
  return t;
}
static const attr *get_attr(const node *n, const char *name) {
  for (int k = 0; k < n->nattrs; k++)
    if (beq(n->attrs[k].name, name)) return &n->attrs[k];
  return NULL;
}

// ---------------------------------------------------------------- ONNX -> .piiw image
static const char *const ROLES[6] = {"attention/self/query", "attention/self/key", "attention/self/value", "attention/output/dense", "intermediate/dense", "output/dense"};
static const char *const NAMES[6] = {"attention.self.query", "attention.self.key", "attention.self.value", "attention.output.dense", "intermediate.dense", "output.dense"};
#define MAX_LAYERS 64

typedef struct {
  tensor *b, *s;
  int64_t K, N;
  int set;
} mat;

// /layer\.(\d+)\/(?:block\/)?(<role>)(?:_\d+)?\/MatMul/ (unanchored); returns the role index and sets *layer, or -1.
static int match_role(bytes s, int *layer) {
  const char *p = (const char *)s.p, *end = p + s.n;
  for (const char *a = p; a + 6 <= end; a++) {
    if (memcmp(a, "layer.", 6) != 0) continue;
    const char *q = a + 6;
    long num = 0;
    const char *d = q;
    while (q < end && *q >= '0' && *q <= '9') {
      if (num < 100000) num = num * 10 + (*q - '0');
      q++;
    }
    if (q == d || q >= end || *q != '/') continue;
    q++;
    for (int blk = 0; blk < 2; blk++) {  // with and without the optional "block/"
      const char *b = q;
      if (blk == 0) {
        if (end - b < 6 || memcmp(b, "block/", 6) != 0) continue;
        b += 6;
      }
      for (int r = 0; r < 6; r++) {
        size_t m = strlen(ROLES[r]);
        if ((size_t)(end - b) < m || memcmp(b, ROLES[r], m) != 0) continue;
        const char *t = b + m;
        // (?:_\d+)? then "/MatMul": try with the suffix (greedy), then without
        if (t < end && *t == '_') {
          const char *u = t + 1;
          while (u < end && *u >= '0' && *u <= '9') u++;
          if (u > t + 1 && end - u >= 7 && memcmp(u, "/MatMul", 7) == 0) {
            *layer = (int)num;
            return r;
          }
        }
        if (end - t >= 7 && memcmp(t, "/MatMul", 7) == 0) {
          *layer = (int)num;
          return r;
        }
      }
    }
  }
  return -1;
}

typedef struct {
  uint8_t *p;
  size_t n, cap;
  int oom;
} out_buf;

static void out_raw(out_buf *o, const void *src, size_t n) {
  if (o->oom) return;
  if (o->n + n > o->cap) {
    size_t c = o->cap ? o->cap : 1 << 20;
    while (c < o->n + n) c *= 2;
    uint8_t *p = realloc(o->p, c);
    if (!p) {
      o->oom = 1;
      return;
    }
    o->p = p;
    o->cap = c;
  }
  if (src) memcpy(o->p + o->n, src, n);
  else memset(o->p + o->n, 0, n);
  o->n += n;
}
static void out_add(out_buf *o, const void *src, size_t n) {  // 16-byte aligned part
  size_t pad = (16 - o->n % 16) % 16;
  if (pad) out_raw(o, NULL, pad);
  out_raw(o, src, n);
}

static int nbits(graph *g, const node *n, mat *m) {
  const attr *bits = get_attr(n, "bits"), *bs = get_attr(n, "block_size"), *acc = get_attr(n, "accuracy_level");
  const attr *K = get_attr(n, "K"), *N = get_attr(n, "N");
  if (!bits || !bits->has_i || bits->i != 4 || !bs || !bs->has_i || bs->i != 32 || n->nin != 3 ||
      (acc && acc->has_i && acc->i != 0) || !K || !K->has_i || !N || !N->has_i)
    return fail(g, "unsupported MatMulNBits");
  m->b = find_init_b(g, n->in[1]);
  m->s = find_init_b(g, n->in[2]);
  if (!m->b || !m->s) return fail(g, "missing initializer %.*s", (int)n->in[1].n, (const char *)n->in[1].p);
  m->K = K->i;
  m->N = N->i;
  m->set = 1;
  return 0;
}

// MatMulNBits B [N, K/32, 16] + scales -> panel layout (8 columns x 32 rows per block, signed nibbles q-8).
static int q4_matrix(graph *g, out_buf *out, const mat *m) {
  const int64_t K = m->K, N = m->N;
  if (K <= 0 || N <= 0 || K % 32 || K > (1 << 20) || N > (1 << 20)) return fail(g, "MatMulNBits shape K=%lld N=%lld", (long long)K, (long long)N);
  const int64_t nb = K / 32;
  if ((int64_t)m->b->raw.n != N * nb * 16) return fail(g, "MatMulNBits shape (B has %zu bytes)", m->b->raw.n);
  if (m->s->raw.n % 4 || (int64_t)m->s->raw.n < N * nb * 4) return fail(g, "MatMulNBits scales: %zu bytes", m->s->raw.n);
  const int64_t Np = (N + 7) / 8 * 8;
  size_t size = (size_t)(Np / 8) * (size_t)nb * 160;
  uint8_t *res = calloc(size, 1);
  if (!res) return fail(g, "out of memory");
  const uint8_t *B = m->b->raw.p, *S = m->s->raw.p;
  size_t o = 0;
  for (int64_t p = 0; p < Np / 8; p++) {
    for (int64_t b = 0; b < nb; b++, o += 160) {
      for (int j = 0; j < 8; j++) {
        const int64_t n = p * 8 + j;
        if (n < N) memcpy(res + o + 4 * j, S + (size_t)(n * nb + b) * 4, 4);
      }
      // byte j of row k: column j (low nibble), column j+4 (high nibble); value q-8 as a signed nibble = q ^ 8
      for (int j = 0; j < 4; j++) {
        const int64_t n0 = p * 8 + j, n1 = n0 + 4;
        const int64_t s0 = n0 < N ? (n0 * nb + b) * 16 : -1;
        const int64_t s1 = n1 < N ? (n1 * nb + b) * 16 : -1;
        for (int i = 0; i < 16; i++) {
          const uint8_t b0 = s0 >= 0 ? B[s0 + i] ^ 0x88 : 0;
          const uint8_t b1 = s1 >= 0 ? B[s1 + i] ^ 0x88 : 0;
          // rows 2i (low nibbles) and 2i+1 (high nibbles)
          res[o + 32 + (2 * i) * 4 + j] = (uint8_t)((b0 & 0x0f) | ((b1 & 0x0f) << 4));
          res[o + 32 + (2 * i + 1) * 4 + j] = (uint8_t)((b0 >> 4) | (b1 & 0xf0));
        }
      }
    }
  }
  out_add(out, res, size);
  free(res);
  return 0;
}

// Integer value of the Constant node that outputs `name` (the last such node), or *ok = 0.
static int64_t const_int(graph *g, bytes name, int *ok) {
  *ok = 0;
  const node *hit = NULL;
  for (size_t i = 0; i < g->nnodes; i++)
    if (g->nodes[i].nout && g->nodes[i].out[0].n == name.n && memcmp(g->nodes[i].out[0].p, name.p, name.n) == 0) hit = &g->nodes[i];
  if (!hit) return 0;
  const attr *v = get_attr(hit, "value");
  if (!v || !v->t) return 0;
  const tensor *t = v->t;
  if (t->data_type == INT64) {
    if (t->raw.n < 8) return 0;
    int64_t x;
    memcpy(&x, t->raw.p, 8);
    *ok = 1;
    return x;
  }
  if (t->raw.n < 4) return 0;
  int32_t x;
  memcpy(&x, t->raw.p, 4);
  *ok = 1;
  return x;
}

static int table(graph *g, out_buf *out, int kind, const char *base, int64_t rows, int64_t cols) {
  char name[256];
  if (kind == 1) {
    snprintf(name, sizeof name, "%s_quantized", base);
    tensor *q = need(g, name);
    if (!q) return -1;
    if (q->data_type != UINT8 || (int64_t)q->raw.n != rows * cols) return fail(g, "%s: expected uint8 [%lld, %lld]", base, (long long)rows, (long long)cols);
    snprintf(name, sizeof name, "%s_zero_point", base);
    tensor *zp = need(g, name);
    snprintf(name, sizeof name, "%s_scale", base);
    tensor *sc = need(g, name);
    if (!zp || !sc) return -1;
    if (sc->raw.n % 4 || !sc->raw.n || !zp->raw.n) return fail(g, "%s: scale or zero point", base);
    float hdr[2];
    memcpy(&hdr[0], sc->raw.p, 4);
    hdr[1] = (float)zp->raw.p[0];
    out_add(out, hdr, 8);
    out_add(out, q->raw.p, q->raw.n);
  } else {
    snprintf(name, sizeof name, "%s_Q4", base);
    tensor *q = need(g, name);
    if (!q) return -1;
    if (q->data_type != INT4 || (int64_t)q->raw.n != rows * cols / 2) return fail(g, "%s: expected int4 [%lld, %lld]", base, (long long)rows, (long long)cols);
    snprintf(name, sizeof name, "%s_scales", base);
    tensor *s = need(g, name);
    if (!s) return -1;
    if (s->data_type != FLOAT || (int64_t)s->raw.n != rows * cols / 32 * 4) return fail(g, "%s: scales", base);
    out_add(out, s->raw.p, s->raw.n);
    out_add(out, q->raw.p, q->raw.n);
  }
  return 0;
}

static int vec(graph *g, out_buf *out, const char *name) {
  tensor *t = need(g, name);
  if (!t) return -1;
  out_add(out, t->raw.p, t->raw.n);
  return 0;
}

static int build(graph *g, out_buf *out) {
  mat(*mats)[6] = calloc(MAX_LAYERS, sizeof(mat[6]));
  if (!mats) return fail(g, "out of memory");
  int order[PII_MAX_APPLY], norder = 0, rc = -1;
  mat proj = {0}, cls = {0};
  for (size_t i = 0; i < g->nnodes; i++) {
    const node *n = &g->nodes[i];
    if (!beq(n->op, "MatMulNBits")) continue;
    if (!n->nout) goto done;
    bytes o = n->out[0];
    int layer, r = match_role(o, &layer);
    if (r >= 0) {
      if (layer >= MAX_LAYERS) {
        fail(g, "layer %d: too many layers", layer);
        goto done;
      }
      if (!mats[layer][r].set && nbits(g, n, &mats[layer][r])) goto done;
      if (r == 0) {
        if (norder == PII_MAX_APPLY) {
          fail(g, "too many layer applications");
          goto done;
        }
        order[norder++] = layer;
      }
    } else if (bhas(o, "/word_embeddings/proj/")) {
      if (nbits(g, n, &proj)) goto done;
    } else if (bhas(o, "/classifier/")) {
      if (nbits(g, n, &cls)) goto done;
    } else {
      fail(g, "unknown MatMulNBits %.*s", (int)o.n, (const char *)o.p);
      goto done;
    }
  }
  int NL = 0;
  for (int l = 0; l < MAX_LAYERS; l++)
    for (int r = 0; r < 6; r++)
      if (mats[l][r].set) {
        NL++;
        break;
      }
  for (int l = 0; l < NL; l++)
    for (int r = 0; r < 6; r++)
      if (!mats[l][r].set) {
        fail(g, "layer %d misses %s", l, ROLES[r]);
        goto done;
      }
  if (!NL) {
    fail(g, "no encoder layers");
    goto done;
  }
  if (!cls.set) {
    fail(g, "no classifier");
    goto done;
  }
  int block_prefix = 0;
  for (size_t i = 0; i < g->ninits; i++) {
    const char *pre = "m.roberta.encoder.layer.0.block.";
    if (g->inits[i].name.n >= strlen(pre) && memcmp(g->inits[i].name.p, pre, strlen(pre)) == 0) block_prefix = 1;
  }

  const int64_t hidden = mats[0][0].N, inter = mats[0][4].N;
  int64_t head_size = 0, pad = -1;
  int have_eps = 0, ok;
  float eps = 0;
  for (size_t i = 0; i < g->nnodes; i++) {
    const node *n = &g->nodes[i];
    if (beq(n->op, "Constant") && !head_size && n->nout && bhas(n->out[0], "/attention/self/Constant_1_output_0")) {
      head_size = const_int(g, n->out[0], &ok);
      if (!ok) head_size = 0;
    }
    if (beq(n->op, "Equal") && n->nin >= 2 && beq(n->in[0], "input_ids")) {
      pad = const_int(g, n->in[1], &ok);
      if (!ok) pad = -1;
    }
    if (beq(n->op, "LayerNormalization")) {
      const attr *a = get_attr(n, "epsilon");
      float e = a && a->has_f ? a->f : 1e-5f;
      if (!have_eps) {
        eps = e;
        have_eps = 1;
      } else if (e != eps) have_eps = 2;
    }
  }
  if (head_size <= 0 || pad < 0 || have_eps != 1) {
    fail(g, "could not read head size, padding id or LayerNorm eps");
    goto done;
  }

  const char *emb_name = proj.set ? "m.roberta.embeddings.word_embeddings.emb.weight" : "m.roberta.embeddings.word_embeddings.weight";
  char name[256];
  snprintf(name, sizeof name, "%s_Q4", emb_name);
  const int kind = find_init(g, name) ? 2 : 1;
  snprintf(name, sizeof name, "%s%s", emb_name, kind == 2 ? "_Q4" : "_quantized");
  tensor *word = need(g, name);
  snprintf(name, sizeof name, "m.roberta.embeddings.position_embeddings.weight%s", kind == 2 ? "_Q4" : "_quantized");
  tensor *posw = need(g, name);
  if (!word || !posw) goto done;
  if (word->ndims < 2 || posw->ndims < 1) {
    fail(g, "embedding table shape");
    goto done;
  }
  const int64_t vocab = word->dims[0], emb_dim = word->dims[1], max_pos = posw->dims[0];
  if (vocab <= 0 || emb_dim <= 0 || max_pos <= 0 || vocab > (1 << 24) || emb_dim > (1 << 16) || max_pos > (1 << 16)) {
    fail(g, "embedding table shape");
    goto done;
  }

  uint32_t hdr[14 + PII_MAX_APPLY];
  const int64_t vals[13] = {0x57494950, 1, hidden, hidden / head_size, inter, cls.N, vocab, emb_dim, kind, NL, norder, pad, max_pos};
  for (int i = 0; i < 13; i++) hdr[i] = (uint32_t)vals[i];
  memcpy(&hdr[13], &eps, 4);
  for (int i = 0; i < norder; i++) hdr[14 + i] = (uint32_t)order[i];
  out_add(out, hdr, (size_t)(14 + norder) * 4);

  if (table(g, out, kind, emb_name, vocab, emb_dim) ||
      table(g, out, kind, "m.roberta.embeddings.position_embeddings.weight", max_pos, hidden) ||
      table(g, out, kind, "m.roberta.embeddings.token_type_embeddings.weight", 1, hidden))
    goto done;
  if (proj.set && q4_matrix(g, out, &proj)) goto done;
  if (vec(g, out, "m.roberta.embeddings.LayerNorm.weight") || vec(g, out, "m.roberta.embeddings.LayerNorm.bias")) goto done;
  for (int l = 0; l < NL; l++) {
    char p[128];
    snprintf(p, sizeof p, "m.roberta.encoder.layer.%d.%s", l, block_prefix ? "block." : "");
    for (int r = 0; r < 6; r++) {
      if (q4_matrix(g, out, &mats[l][r])) goto done;
      snprintf(name, sizeof name, "%s%s.bias", p, NAMES[r]);
      if (vec(g, out, name)) goto done;
      if (r == 3) {
        snprintf(name, sizeof name, "%sattention.output.LayerNorm.weight", p);
        if (vec(g, out, name)) goto done;
        snprintf(name, sizeof name, "%sattention.output.LayerNorm.bias", p);
        if (vec(g, out, name)) goto done;
      }
    }
    snprintf(name, sizeof name, "%soutput.LayerNorm.weight", p);
    if (vec(g, out, name)) goto done;
    snprintf(name, sizeof name, "%soutput.LayerNorm.bias", p);
    if (vec(g, out, name)) goto done;
  }
  if (q4_matrix(g, out, &cls) || vec(g, out, "m.classifier.bias")) goto done;
  size_t pad16 = (16 - out->n % 16) % 16;
  if (pad16) out_raw(out, NULL, pad16);
  if (out->oom) {
    fail(g, "out of memory");
    goto done;
  }
  rc = 0;
done:
  free(mats);
  return rc;
}

int pii_onnx_to_piiw(const uint8_t *model, size_t n, uint8_t **image, size_t *len, char *err, size_t errlen) {
  graph g;
  memset(&g, 0, sizeof g);
  g.err = err;
  g.errlen = errlen;
  out_buf out = {0};
  int rc = read_graph(&g, model, n);
  if (!rc) rc = build(&g, &out);
  graph_free(&g);
  if (rc) {
    if (!g.failed) pii_set_err(err, errlen, "malformed model file");  // every failure carries a message
    free(out.p);
    return -1;
  }
  // copy into a 64-byte aligned block: the engine reads floats and vectors in place
  uint8_t *img = pii_aligned_alloc(out.n);
  if (!img) {
    free(out.p);
    pii_set_err(err, errlen, "out of memory");
    return -1;
  }
  memcpy(img, out.p, out.n);
  free(out.p);
  *image = img;
  *len = out.n;
  return 0;
}
