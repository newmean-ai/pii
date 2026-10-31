// Purpose-built inference engine for the @newmean-ai/pii token classifiers (experiment, 2026-10-30).
//
// Runs exactly one architecture: an XLM-R style post-LN encoder (hidden H, 12 heads of 32, GELU with erf, FFN 4H)
// with 4-bit MatMulNBits weights (block 32, zero point 8), an int8 or int4 embedding table, an optional low-rank
// embedding projection, and a token classifier. Weight file: "PIIW" written by export_wasm_weights.py (format in
// that script's docstring). Freestanding C for wasm32 + SIMD128: no libc, no imports; the JS glue copies the weight
// file into linear memory, calls pii_load, then pii_run per window.
#include <stdint.h>
#include <wasm_simd128.h>

#define EXPORT(name) __attribute__((export_name(#name)))
#define HEAD 32
#define MAX_APPLY 64

typedef v128_t f4;

// ---------------------------------------------------------------- memory (bump allocator, never frees)
extern unsigned char __heap_base;
static uintptr_t heap_top = (uintptr_t)&__heap_base;

EXPORT(pii_alloc) void *pii_alloc(uint32_t n) {
  uintptr_t p = (heap_top + 63) & ~(uintptr_t)63;
  uintptr_t end = p + n;
  uintptr_t have = __builtin_wasm_memory_size(0) * 65536u;
  if (end > have) {
    uintptr_t pages = (end - have + 65535) / 65536;
    if (__builtin_wasm_memory_grow(0, pages) == (uintptr_t)-1) return 0;
  }
  heap_top = end;
  return (void *)p;
}

void *memset(void *d, int c, unsigned long n) {
  unsigned char *p = d;
  while (n--) *p++ = (unsigned char)c;
  return d;
}
void *memcpy(void *d, const void *s, unsigned long n) {
  unsigned char *p = d;
  const unsigned char *q = s;
  while (n--) *p++ = *q++;
  return d;
}

// ---------------------------------------------------------------- model
typedef struct {  // MatMulNBits weight in panel layout: per 8 columns, per 32 rows of K: f32 scales[8], 128 bytes
  const uint8_t *data;
  int K, N, Np;
} Q4;

typedef struct {
  int kind;           // 1: u8 per tensor (scale, zero point), 2: int4 blocks of 32 with f32 scales
  int rows, cols;
  float scale, zp;    // kind 1
  const float *scales;  // kind 2: [rows, cols/32]
  const uint8_t *q;
} Emb;

typedef struct {
  Q4 wq, wk, wv, wo, wi, wout;
  const float *bq, *bk, *bv, *bo, *ln1g, *ln1b, *bi, *bout, *ln2g, *ln2b;
} Layer;

static struct {
  int H, heads, I, T, V, E, kind, NL, NA, pad, max_pos;
  float eps;
  int apply[MAX_APPLY];
  Emb word, pos, type;
  int has_proj;
  Q4 proj;
  const float *elng, *elnb;
  Layer *layers;
  Q4 cls;
  const float *bcls;
  // work buffers for up to cap positions
  int cap;
  float *x, *x2, *q, *k, *v, *ctx, *ff, *emb, *kt, *scores, *panel, *logits, *typerow, *tail;
} M;

static const uint8_t *cur;
static const uint8_t *align16(const uint8_t *p) { return (const uint8_t *)(((uintptr_t)p + 15) & ~(uintptr_t)15); }
static const uint8_t *take(uint32_t n) {
  cur = align16(cur);
  const uint8_t *p = cur;
  cur += n;
  return p;
}
static const float *takef(uint32_t n) { return (const float *)take(n * 4); }

static Q4 take_q4(int K, int N) {
  Q4 w;
  w.K = K;
  w.N = N;
  w.Np = (N + 7) / 8 * 8;
  w.data = take((uint32_t)(w.Np / 8) * (uint32_t)(K / 32) * 160u);
  return w;
}

static Emb take_emb(int rows, int cols) {
  Emb e;
  e.kind = M.kind;
  e.rows = rows;
  e.cols = cols;
  if (M.kind == 1) {
    const float *p = takef(2);
    e.scale = p[0];
    e.zp = p[1];
    e.scales = 0;
    e.q = take((uint32_t)rows * (uint32_t)cols);
  } else {
    e.scale = e.zp = 0;
    e.scales = takef((uint32_t)rows * (uint32_t)(cols / 32));
    e.q = take((uint32_t)rows * (uint32_t)cols / 2);
  }
  return e;
}

static void emb_row(const Emb *e, int r, float *out) {
  if (e->kind == 1) {
    const uint8_t *q = e->q + (uint32_t)r * e->cols;
    for (int c = 0; c < e->cols; c++) out[c] = (float)((int)q[c] - (int)e->zp) * e->scale;
  } else {
    const uint8_t *q = e->q + (uint32_t)r * e->cols / 2;
    const float *s = e->scales + (uint32_t)r * (e->cols / 32);
    for (int c = 0; c < e->cols; c += 2) {
      uint8_t b = q[c / 2];
      int lo = (int)(int8_t)(b << 4) >> 4, hi = (int)(int8_t)b >> 4;
      out[c] = (float)lo * s[c / 32];
      out[c + 1] = (float)hi * s[(c + 1) / 32];
    }
  }
}

static int ensure_cap(int L) {
  if (L <= M.cap) return 0;
  int c = L < 256 ? 256 : L;
  uint32_t H = M.H, I = M.I;
  M.x = pii_alloc(c * H * 4);
  M.x2 = pii_alloc(c * H * 4);
  M.q = pii_alloc(c * H * 4);
  M.k = pii_alloc(c * H * 4);
  M.v = pii_alloc(c * H * 4);
  M.ctx = pii_alloc(c * H * 4);
  M.ff = pii_alloc(c * I * 4);
  M.emb = pii_alloc(c * (uint32_t)M.E * 4);
  M.kt = pii_alloc(HEAD * (uint32_t)(c + 8) * 4);
  M.scores = pii_alloc((uint32_t)c * (uint32_t)(c + 8) * 4);
  M.logits = pii_alloc(c * (uint32_t)M.cls.Np * 4);
  M.tail = pii_alloc(c * 8 * 4);
  if (!M.x || !M.x2 || !M.q || !M.k || !M.v || !M.ctx || !M.ff || !M.emb || !M.kt || !M.scores || !M.logits || !M.tail) return -1;
  M.cap = c;
  return 0;
}

// Returns 0 on success, a negative code on a malformed file.
EXPORT(pii_load) int pii_load(const uint8_t *buf, uint32_t len) {
  const uint32_t *h = (const uint32_t *)buf;
  if (len < 64 || h[0] != 0x57494950u /* 'PIIW' */) return -1;
  if (h[1] != 1) return -2;
  M.H = h[2];
  M.heads = h[3];
  M.I = h[4];
  M.T = h[5];
  M.V = h[6];
  M.E = h[7];
  M.kind = h[8];
  M.NL = h[9];
  M.NA = h[10];
  M.pad = h[11];
  M.max_pos = h[12];
  M.eps = ((const float *)h)[13];
  if (M.H != M.heads * HEAD || M.H % 32 || M.I % 32 || M.E % 32 || (M.kind != 1 && M.kind != 2) || M.NA > MAX_APPLY) return -3;
  for (int i = 0; i < M.NA; i++) {
    M.apply[i] = h[14 + i];
    if (M.apply[i] >= M.NL) return -3;
  }
  cur = buf + (14 + M.NA) * 4;
  M.word = take_emb(M.V, M.E);
  M.pos = take_emb(M.max_pos, M.H);
  M.type = take_emb(1, M.H);
  M.has_proj = M.E != M.H;
  if (M.has_proj) M.proj = take_q4(M.E, M.H);
  M.elng = takef(M.H);
  M.elnb = takef(M.H);
  M.layers = pii_alloc(sizeof(Layer) * M.NL);
  for (int l = 0; l < M.NL; l++) {
    Layer *y = &M.layers[l];
    y->wq = take_q4(M.H, M.H);
    y->bq = takef(M.H);
    y->wk = take_q4(M.H, M.H);
    y->bk = takef(M.H);
    y->wv = take_q4(M.H, M.H);
    y->bv = takef(M.H);
    y->wo = take_q4(M.H, M.H);
    y->bo = takef(M.H);
    y->ln1g = takef(M.H);
    y->ln1b = takef(M.H);
    y->wi = take_q4(M.H, M.I);
    y->bi = takef(M.I);
    y->wout = take_q4(M.I, M.H);
    y->bout = takef(M.H);
    y->ln2g = takef(M.H);
    y->ln2b = takef(M.H);
  }
  M.cls = take_q4(M.H, M.T);
  M.bcls = takef(M.T);
  if (align16(cur) > buf + len) return -4;
  int kmax = M.I > M.H ? M.I : M.H;
  M.panel = pii_alloc(kmax * 8 * 4);
  M.typerow = pii_alloc(M.H * 4);
  emb_row(&M.type, 0, M.typerow);
  M.cap = 0;
  if (ensure_cap(256)) return -5;
  return 0;
}

EXPORT(pii_tags) int pii_tags(void) { return M.T; }
EXPORT(pii_max_len) int pii_max_len(void) { return M.max_pos - M.pad - 1; }

// ---------------------------------------------------------------- kernels
// Panel p of W (8 output columns) -> M.panel[k*8 + j] = dequantized weight (row k, column 8p+j).
static void dequant_panel(const Q4 *w, int p) {
  const uint8_t *src = w->data + (uint32_t)p * (w->K / 32) * 160u;
  float *dst = M.panel;
  for (int b = 0; b < w->K / 32; b++, src += 160) {
    f4 s_lo = wasm_v128_load(src), s_hi = wasm_v128_load(src + 16);
    const uint8_t *q = src + 32;
    for (int r = 0; r < 32; r += 4, q += 16, dst += 32) {
      v128_t raw = wasm_v128_load(q);  // 4 rows x 4 bytes
      v128_t lo = wasm_i8x16_shr(wasm_i8x16_shl(raw, 4), 4);  // columns 0..3 (signed nibble)
      v128_t hi = wasm_i8x16_shr(raw, 4);                     // columns 4..7
      v128_t lo16a = wasm_i16x8_extend_low_i8x16(lo), lo16b = wasm_i16x8_extend_high_i8x16(lo);
      v128_t hi16a = wasm_i16x8_extend_low_i8x16(hi), hi16b = wasm_i16x8_extend_high_i8x16(hi);
#define ROW(i, l16, h16, part)                                                                                   \
  wasm_v128_store(dst + 8 * (i), wasm_f32x4_mul(wasm_f32x4_convert_i32x4(wasm_i32x4_extend_##part##_i16x8(l16)), s_lo)); \
  wasm_v128_store(dst + 8 * (i) + 4, wasm_f32x4_mul(wasm_f32x4_convert_i32x4(wasm_i32x4_extend_##part##_i16x8(h16)), s_hi));
      ROW(0, lo16a, hi16a, low)
      ROW(1, lo16a, hi16a, high)
      ROW(2, lo16b, hi16b, low)
      ROW(3, lo16b, hi16b, high)
#undef ROW
    }
  }
}

// x[i] = LayerNorm(a[i] + b[i]) for L rows of H (b may be 0). Two-pass mean / variance.
static void add_layernorm(float *x, const float *a, const float *b, int L, const float *g, const float *be) {
  const int H = M.H;
  for (int i = 0; i < L; i++) {
    float *o = x + i * H;
    const float *pa = a + i * H, *pb = b ? b + i * H : 0;
    f4 s = wasm_f32x4_splat(0);
    for (int c = 0; c < H; c += 4) {
      f4 t = wasm_v128_load(pa + c);
      if (pb) t = wasm_f32x4_add(t, wasm_v128_load(pb + c));
      wasm_v128_store(o + c, t);
      s = wasm_f32x4_add(s, t);
    }
    float mean = (wasm_f32x4_extract_lane(s, 0) + wasm_f32x4_extract_lane(s, 1) + wasm_f32x4_extract_lane(s, 2) +
                  wasm_f32x4_extract_lane(s, 3)) / (float)H;
    f4 m = wasm_f32x4_splat(mean), sv = wasm_f32x4_splat(0);
    for (int c = 0; c < H; c += 4) {
      f4 d = wasm_f32x4_sub(wasm_v128_load(o + c), m);
      sv = wasm_f32x4_add(sv, wasm_f32x4_mul(d, d));
    }
    float var = (wasm_f32x4_extract_lane(sv, 0) + wasm_f32x4_extract_lane(sv, 1) + wasm_f32x4_extract_lane(sv, 2) +
                 wasm_f32x4_extract_lane(sv, 3)) / (float)H;
    f4 inv = wasm_f32x4_splat(1.0f / __builtin_sqrtf(var + M.eps));
    for (int c = 0; c < H; c += 4) {
      f4 d = wasm_f32x4_mul(wasm_f32x4_sub(wasm_v128_load(o + c), m), inv);
      wasm_v128_store(o + c, wasm_f32x4_add(wasm_f32x4_mul(d, wasm_v128_load(g + c)), wasm_v128_load(be + c)));
    }
  }
}

// exp(x) for x <= 0 (softmax): Cody-Waite range reduction, degree-6 polynomial, about 1 ulp.
static inline f4 vexp(f4 x) {
  x = wasm_f32x4_max(x, wasm_f32x4_splat(-87.0f));
  f4 n = wasm_f32x4_nearest(wasm_f32x4_mul(x, wasm_f32x4_splat(1.44269504088896341f)));
  f4 r = wasm_f32x4_sub(x, wasm_f32x4_mul(n, wasm_f32x4_splat(0.693359375f)));
  r = wasm_f32x4_sub(r, wasm_f32x4_mul(n, wasm_f32x4_splat(-2.12194440e-4f)));
  f4 p = wasm_f32x4_splat(1.9875691500e-4f);
  p = wasm_f32x4_add(wasm_f32x4_mul(p, r), wasm_f32x4_splat(1.3981999507e-3f));
  p = wasm_f32x4_add(wasm_f32x4_mul(p, r), wasm_f32x4_splat(8.3334519073e-3f));
  p = wasm_f32x4_add(wasm_f32x4_mul(p, r), wasm_f32x4_splat(4.1665795894e-2f));
  p = wasm_f32x4_add(wasm_f32x4_mul(p, r), wasm_f32x4_splat(1.6666665459e-1f));
  p = wasm_f32x4_add(wasm_f32x4_mul(p, r), wasm_f32x4_splat(5.0000001201e-1f));
  p = wasm_f32x4_add(wasm_f32x4_mul(wasm_f32x4_mul(p, r), r), wasm_f32x4_add(r, wasm_f32x4_splat(1.0f)));
  v128_t e = wasm_i32x4_shl(wasm_i32x4_add(wasm_i32x4_trunc_sat_f32x4(n), wasm_i32x4_splat(127)), 23);
  return wasm_f32x4_mul(p, e);
}

// Multiply-add: separate mul and add in the baseline build (bit-identical on every engine); one fused
// f32x4.relaxed_madd in the relaxed-SIMD build (FMA where the CPU has it, so the rounding differs slightly).
#ifdef PII_RELAXED
#define MADD(c, a, b) wasm_f32x4_relaxed_madd(a, b, c)
#else
#define MADD(c, a, b) wasm_f32x4_add(c, wasm_f32x4_mul(a, b))
#endif
#ifndef TILE_M
#define TILE_M 4
#endif

// C[M, ldc] (columns 0..N-1, N a multiple of 8) = A[M, lda] (columns 0..K-1) x B[K, ldb]; fp32, k in order.
// Register tile: TILE_M rows x 8 columns (2 x TILE_M accumulators).
static void gemm(float *C, int ldc, const float *A, int lda, const float *B, int ldb, int Mr, int N, int K) {
  for (int n0 = 0; n0 < N; n0 += 8) {
    const float *Bn = B + n0;
    int r = 0;
    for (; r + TILE_M <= Mr; r += TILE_M) {
      const float *a = A + r * lda;
      f4 c0[TILE_M], c1[TILE_M];
#pragma clang loop unroll(full)
      for (int i = 0; i < TILE_M; i++) c0[i] = c1[i] = wasm_f32x4_splat(0);
      for (int k = 0; k < K; k++) {
        f4 w0 = wasm_v128_load(Bn + k * ldb), w1 = wasm_v128_load(Bn + k * ldb + 4);
#pragma clang loop unroll(full)
        for (int i = 0; i < TILE_M; i++) {
          f4 x = wasm_v128_load32_splat(a + i * lda + k);
          c0[i] = MADD(c0[i], x, w0);
          c1[i] = MADD(c1[i], x, w1);
        }
      }
#pragma clang loop unroll(full)
      for (int i = 0; i < TILE_M; i++) {
        wasm_v128_store(C + (r + i) * ldc + n0, c0[i]);
        wasm_v128_store(C + (r + i) * ldc + n0 + 4, c1[i]);
      }
    }
    for (; r < Mr; r++) {
      const float *a0 = A + r * lda;
      f4 c00 = wasm_f32x4_splat(0), c01 = c00;
      for (int k = 0; k < K; k++) {
        f4 x0 = wasm_v128_load32_splat(a0 + k);
        c00 = MADD(c00, x0, wasm_v128_load(Bn + k * ldb));
        c01 = MADD(c01, x0, wasm_v128_load(Bn + k * ldb + 4));
      }
      wasm_v128_store(C + r * ldc + n0, c00);
      wasm_v128_store(C + r * ldc + n0 + 4, c01);
    }
  }
}

// out[L, ldo] (columns 0..N-1) = A[L, lda] (columns 0..K-1) x W + bias, one dequantized 8-column panel at a time.
static void matmul(float *out, int ldo, const float *A, int lda, int L, const Q4 *w, const float *bias) {
  for (int p = 0; p < w->Np / 8; p++) {
    const int n0 = p * 8;
    dequant_panel(w, p);
    if (n0 + 8 <= w->N) {
      gemm(out + n0, ldo, A, lda, M.panel, 8, L, 8, w->K);
      if (bias) {
        f4 b0 = wasm_v128_load(bias + n0), b1 = wasm_v128_load(bias + n0 + 4);
        for (int r = 0; r < L; r++) {
          float *o = out + r * ldo + n0;
          wasm_v128_store(o, wasm_f32x4_add(wasm_v128_load(o), b0));
          wasm_v128_store(o + 4, wasm_f32x4_add(wasm_v128_load(o + 4), b1));
        }
      }
    } else {  // last, partial panel (the classifier): through a scratch block
      gemm(M.tail, 8, A, lda, M.panel, 8, L, 8, w->K);
      for (int r = 0; r < L; r++)
        for (int j = 0; n0 + j < w->N; j++) out[r * ldo + n0 + j] = M.tail[r * 8 + j] + (bias ? bias[n0 + j] : 0.f);
    }
  }
}

static inline float hsum(f4 v) {
  return wasm_f32x4_extract_lane(v, 0) + wasm_f32x4_extract_lane(v, 1) + wasm_f32x4_extract_lane(v, 2) + wasm_f32x4_extract_lane(v, 3);
}
static inline float hmax(f4 v) {
  float a = wasm_f32x4_extract_lane(v, 0), b = wasm_f32x4_extract_lane(v, 1), c = wasm_f32x4_extract_lane(v, 2), d = wasm_f32x4_extract_lane(v, 3);
  a = a > b ? a : b;
  c = c > d ? c : d;
  return a > c ? a : c;
}

// Multi-head self attention without a mask (one unpadded sequence): ctx = softmax(q k^T) v per head;
// q and k are each scaled by 32^-1/4, as in the exported graph. Scores and P.V use the gemm kernel.
static void attention(int L) {
  const int H = M.H;
  const int Lp = (L + 7) & ~7;  // keys padded to 8 (padded scores are dropped before the softmax)
  const f4 qs = wasm_f32x4_splat(0.4204482138156891f);
  for (int i = 0; i < L * H; i += 4) {
    wasm_v128_store(M.q + i, wasm_f32x4_mul(wasm_v128_load(M.q + i), qs));
    wasm_v128_store(M.k + i, wasm_f32x4_mul(wasm_v128_load(M.k + i), qs));
  }
  float *kt = M.kt, *S = M.scores;
  for (int h = 0; h < M.heads; h++) {
    const int o = h * HEAD;
    for (int d = 0; d < HEAD; d++) {
      for (int j = 0; j < L; j++) kt[d * Lp + j] = M.k[j * H + o + d];
      for (int j = L; j < Lp; j++) kt[d * Lp + j] = 0.f;
    }
    gemm(S, Lp, M.q + o, H, kt, Lp, L, Lp, HEAD);
    for (int i = 0; i < L; i++) {
      float *s = S + i * Lp;
      for (int j = L; j < Lp; j++) s[j] = -3.0e38f;
      f4 mx = wasm_f32x4_splat(-3.0e38f);
      for (int j = 0; j < Lp; j += 4) mx = wasm_f32x4_max(mx, wasm_v128_load(s + j));
      f4 vm = wasm_f32x4_splat(hmax(mx)), sum = wasm_f32x4_splat(0);
      for (int j = 0; j < Lp; j += 4) {
        f4 e = vexp(wasm_f32x4_sub(wasm_v128_load(s + j), vm));
        if (j + 4 > L) e = wasm_v128_and(e, wasm_i32x4_lt(wasm_i32x4_make(j, j + 1, j + 2, j + 3), wasm_i32x4_splat(L)));
        wasm_v128_store(s + j, e);
        sum = wasm_f32x4_add(sum, e);
      }
      const f4 inv = wasm_f32x4_splat(1.0f / hsum(sum));
      for (int j = 0; j < Lp; j += 4) wasm_v128_store(s + j, wasm_f32x4_mul(wasm_v128_load(s + j), inv));
    }
    gemm(M.ctx + o, H, S, Lp, M.v + o, H, L, HEAD, L);
  }
}

// GELU(x) = 0.5 x (1 + erf(x / sqrt 2)); erf from Abramowitz & Stegun 7.1.26 (absolute error <= 1.5e-7).
static void gelu(float *x, int n) {
  const f4 half = wasm_f32x4_splat(0.5f), one = wasm_f32x4_splat(1.0f), rs2 = wasm_f32x4_splat(0.70710678118654752f);
  for (int i = 0; i < n; i += 4) {
    f4 v = wasm_v128_load(x + i);
    f4 z = wasm_f32x4_mul(v, rs2);
    f4 a = wasm_f32x4_abs(z);
    f4 t = wasm_f32x4_div(one, wasm_f32x4_add(one, wasm_f32x4_mul(a, wasm_f32x4_splat(0.3275911f))));
    f4 p = wasm_f32x4_splat(1.061405429f);
    p = wasm_f32x4_add(wasm_f32x4_mul(p, t), wasm_f32x4_splat(-1.453152027f));
    p = wasm_f32x4_add(wasm_f32x4_mul(p, t), wasm_f32x4_splat(1.421413741f));
    p = wasm_f32x4_add(wasm_f32x4_mul(p, t), wasm_f32x4_splat(-0.284496736f));
    p = wasm_f32x4_add(wasm_f32x4_mul(p, t), wasm_f32x4_splat(0.254829592f));
    p = wasm_f32x4_mul(p, t);
    f4 e = wasm_f32x4_sub(one, wasm_f32x4_mul(p, vexp(wasm_f32x4_neg(wasm_f32x4_mul(a, a)))));
    e = wasm_v128_or(e, wasm_v128_and(z, wasm_i32x4_splat((int)0x80000000)));  // copysign
    wasm_v128_store(x + i, wasm_f32x4_mul(wasm_f32x4_mul(v, wasm_f32x4_add(e, one)), half));
  }
}

// ---------------------------------------------------------------- forward
EXPORT(pii_ids) int32_t *pii_ids(int L) {
  if (ensure_cap(L)) return 0;
  static int32_t *ids = 0;
  static int idcap = 0;
  if (L > idcap) {
    ids = pii_alloc(L * 4);
    idcap = L;
  }
  return ids;
}

// ids[L] (with <s> and </s>) -> logits [L, n_tags] (row stride n_tags). Returns 0 on error.
EXPORT(pii_run) float *pii_run(const int32_t *ids, int L) {
  if (L <= 0 || L > pii_max_len() || ensure_cap(L)) return 0;
  const int H = M.H, E = M.E;
  // embeddings: word (+ projection) + token type 0 + position (RoBERTa positions: padding_idx + 1 + index)
  for (int i = 0; i < L; i++) {
    int id = ids[i];
    if (id < 0 || id >= M.V) id = 0;
    emb_row(&M.word, id, M.emb + i * E);
  }
  if (M.has_proj) matmul(M.x2, H, M.emb, E, L, &M.proj, 0);
  else
    for (int i = 0; i < L * H; i++) M.x2[i] = M.emb[i];
  int p = M.pad;
  float *posrow = M.ctx;  // scratch
  for (int i = 0; i < L; i++) {
    if (ids[i] != M.pad) p++;
    emb_row(&M.pos, ids[i] != M.pad ? p : M.pad, posrow);
    float *xr = M.x2 + i * H;
    for (int c = 0; c < H; c++) xr[c] = (xr[c] + M.typerow[c]) + posrow[c];
  }
  add_layernorm(M.x, M.x2, 0, L, M.elng, M.elnb);
  for (int a = 0; a < M.NA; a++) {
    const Layer *y = &M.layers[M.apply[a]];
    matmul(M.q, H, M.x, H, L, &y->wq, y->bq);
    matmul(M.k, H, M.x, H, L, &y->wk, y->bk);
    matmul(M.v, H, M.x, H, L, &y->wv, y->bv);
    attention(L);
    matmul(M.x2, H, M.ctx, H, L, &y->wo, y->bo);
    add_layernorm(M.x, M.x2, M.x, L, y->ln1g, y->ln1b);
    matmul(M.ff, M.I, M.x, H, L, &y->wi, y->bi);
    gelu(M.ff, L * M.I);
    matmul(M.x2, H, M.ff, M.I, L, &y->wout, y->bout);
    add_layernorm(M.x, M.x2, M.x, L, y->ln2g, y->ln2b);
  }
  matmul(M.logits, M.T, M.x, H, L, &M.cls, M.bcls);
  return M.logits;
}
