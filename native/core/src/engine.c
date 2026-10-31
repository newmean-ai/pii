// Inference engine: a native port of js/engine/pii.c (the npm package's WebAssembly engine). Same architecture, same
// .piiw layout, same kernels and the same order of floating-point operations, so logits are bit-identical to the
// baseline wasm build (built with -ffp-contract=off). Differences from pii.c: wasm SIMD128 intrinsics -> simd.h, the
// bump allocator -> malloc, global model state -> an immutable pii_engine plus per-call scratch (pii_ws), so one model
// can serve several threads.
#include <stdlib.h>
#include <string.h>

#include "internal.h"
#include "simd.h"

#define HEAD 32
// One sqrt instruction (correctly rounded everywhere). The build passes -fno-math-errno so that GCC and Clang on Linux
// emit no libm call (the argument is always > 0); MSVC has no __builtin_sqrtf.
#if defined(__GNUC__) || defined(__clang__)
#define PII_SQRTF __builtin_sqrtf
#else
#include <math.h>
#define PII_SQRTF sqrtf
#endif
#ifndef TILE_M  // rows per register tile; does not change results (each output still sums k in order)
#ifdef PII_FMA
#define TILE_M 6  // 12 accumulators hide the fused multiply-add latency (fastest on Apple M5)
#else
#define TILE_M 4
#endif
#endif

// ---------------------------------------------------------------- loading
typedef struct {
  const uint8_t *base, *cur, *end;
  int over;
} cursor;

static const uint8_t *take(cursor *c, size_t n) {
  size_t at = ((size_t)(c->cur - c->base) + 15) & ~(size_t)15;
  if (c->over || at > (size_t)(c->end - c->base) || n > (size_t)(c->end - c->base) - at) {
    c->over = 1;
    return NULL;
  }
  c->cur = c->base + at + n;
  return c->base + at;
}
static const float *takef(cursor *c, size_t n) { return (const float *)take(c, n * 4); }

static pii_q4 take_q4(cursor *c, int K, int N) {
  pii_q4 w;
  w.K = K;
  w.N = N;
  w.Np = (N + 7) / 8 * 8;
  w.data = take(c, (size_t)(w.Np / 8) * (size_t)(K / 32) * 160u);
  return w;
}

static pii_emb take_emb(cursor *c, int kind, int rows, int cols) {
  pii_emb e;
  memset(&e, 0, sizeof e);
  e.kind = kind;
  e.rows = rows;
  e.cols = cols;
  if (kind == 1) {
    const float *p = takef(c, 2);
    if (p) {
      e.scale = p[0];
      e.zp = p[1];
    }
    e.q = take(c, (size_t)rows * (size_t)cols);
  } else {
    e.scales = takef(c, (size_t)rows * (size_t)(cols / 32));
    e.q = take(c, (size_t)rows * (size_t)cols / 2);
  }
  return e;
}

static void emb_row(const pii_emb *e, int r, float *out) {
  if (e->kind == 1) {
    const uint8_t *q = e->q + (size_t)r * e->cols;
    for (int c = 0; c < e->cols; c++) out[c] = (float)((int)q[c] - (int)e->zp) * e->scale;
  } else {
    const uint8_t *q = e->q + (size_t)r * e->cols / 2;
    const float *s = e->scales + (size_t)r * (e->cols / 32);
    for (int c = 0; c < e->cols; c += 2) {
      uint8_t b = q[c / 2];
      int lo = (int)(int8_t)(uint8_t)(b << 4) >> 4, hi = (int)(int8_t)b >> 4;
      out[c] = (float)lo * s[c / 32];
      out[c + 1] = (float)hi * s[(c + 1) / 32];
    }
  }
}

static uint32_t rd32(const uint8_t *p) {
  uint32_t v;
  memcpy(&v, p, 4);
  return v;
}

// Same checks and codes as pii_load in pii.c: -1 not a .piiw file, -2 version, -3 shape, -4 truncated, -5 memory.
int pii_engine_load(pii_engine *m, uint8_t *buf, size_t len) {
  memset(m, 0, sizeof *m);
  if (len < 64 || rd32(buf) != 0x57494950u /* 'PIIW' */) return -1;
  if (rd32(buf + 4) != 1) return -2;
  int h[13];
  for (int i = 2; i < 13; i++) h[i] = (int)rd32(buf + 4 * i);
  m->H = h[2];
  m->heads = h[3];
  m->I = h[4];
  m->T = h[5];
  m->V = h[6];
  m->E = h[7];
  m->kind = h[8];
  m->NL = h[9];
  m->NA = h[10];
  m->pad = h[11];
  m->max_pos = h[12];
  memcpy(&m->eps, buf + 13 * 4, 4);
  if (m->H <= 0 || m->heads <= 0 || m->I <= 0 || m->T <= 0 || m->V <= 0 || m->E <= 0 || m->NL <= 0 || m->NA < 0 ||
      m->pad < 0 || m->max_pos <= m->pad + 1)
    return -3;
  if (m->H != m->heads * HEAD || m->H % 32 || m->I % 32 || m->E % 32 || (m->kind != 1 && m->kind != 2) || m->NA > PII_MAX_APPLY)
    return -3;
  if ((size_t)(14 + m->NA) * 4 > len) return -4;
  for (int i = 0; i < m->NA; i++) {
    m->apply[i] = (int)rd32(buf + 4 * (14 + i));
    if (m->apply[i] < 0 || m->apply[i] >= m->NL) return -3;
  }
  cursor c = {buf, buf + (14 + m->NA) * 4, buf + len, 0};
  m->word = take_emb(&c, m->kind, m->V, m->E);
  m->pos = take_emb(&c, m->kind, m->max_pos, m->H);
  m->type = take_emb(&c, m->kind, 1, m->H);
  m->has_proj = m->E != m->H;
  if (m->has_proj) m->proj = take_q4(&c, m->E, m->H);
  m->elng = takef(&c, m->H);
  m->elnb = takef(&c, m->H);
  m->layers = calloc((size_t)m->NL, sizeof(pii_layer));
  if (!m->layers) return -5;
  for (int l = 0; l < m->NL; l++) {
    pii_layer *y = &m->layers[l];
    y->wq = take_q4(&c, m->H, m->H);
    y->bq = takef(&c, m->H);
    y->wk = take_q4(&c, m->H, m->H);
    y->bk = takef(&c, m->H);
    y->wv = take_q4(&c, m->H, m->H);
    y->bv = takef(&c, m->H);
    y->wo = take_q4(&c, m->H, m->H);
    y->bo = takef(&c, m->H);
    y->ln1g = takef(&c, m->H);
    y->ln1b = takef(&c, m->H);
    y->wi = take_q4(&c, m->H, m->I);
    y->bi = takef(&c, m->I);
    y->wout = take_q4(&c, m->I, m->H);
    y->bout = takef(&c, m->H);
    y->ln2g = takef(&c, m->H);
    y->ln2b = takef(&c, m->H);
  }
  m->cls = take_q4(&c, m->H, m->T);
  m->bcls = takef(&c, m->T);
  if (c.over) {
    free(m->layers);
    m->layers = NULL;
    return -4;
  }
  m->typerow = pii_aligned_alloc((size_t)m->H * 4);
  if (!m->typerow) {
    free(m->layers);
    m->layers = NULL;
    return -5;
  }
  emb_row(&m->type, 0, m->typerow);
  m->image = buf;
  return 0;
}

void pii_engine_free(pii_engine *m) {
  free(m->layers);
  pii_aligned_free(m->typerow);
  pii_aligned_free(m->image);
  memset(m, 0, sizeof *m);
}

int pii_engine_max_len(const pii_engine *m) { return m->max_pos - m->pad - 1; }

// ---------------------------------------------------------------- scratch
static size_t up64(size_t n) { return (n + 63) & ~(size_t)63; }

int pii_ws_init(pii_ws *w, const pii_engine *m, int L) {
  memset(w, 0, sizeof *w);
  size_t c = (size_t)L, H = (size_t)m->H, I = (size_t)m->I, kmax = (size_t)(m->I > m->H ? m->I : m->H);
  size_t sz[13] = {c * H, c * H, c * H, c * H, c * H, c * H, c * I, c * (size_t)m->E, HEAD * (c + 8), c * (c + 8),
                   kmax * 8, c * (size_t)m->cls.Np, c * 8};
  size_t total = 0;
  for (int i = 0; i < 13; i++) total += up64(sz[i] * 4);
  uint8_t *p = pii_aligned_alloc(total);
  if (!p) return -1;
  w->block = p;
  float **dst[13] = {&w->x, &w->x2, &w->q, &w->k, &w->v, &w->ctx, &w->ff, &w->emb, &w->kt, &w->scores, &w->panel, &w->logits, &w->tail};
  for (int i = 0; i < 13; i++) {
    *dst[i] = (float *)p;
    p += up64(sz[i] * 4);
  }
  w->cap = L;
  return 0;
}

void pii_ws_free(pii_ws *w) {
  pii_aligned_free(w->block);
  memset(w, 0, sizeof *w);
}

// ---------------------------------------------------------------- kernels
// Panel p of W (8 output columns) -> panel[k*8 + j] = dequantized weight (row k, column 8p+j).
static void dequant_panel(const pii_q4 *w, int p, float *panel) {
  const uint8_t *src = w->data + (size_t)p * (size_t)(w->K / 32) * 160u;
  float *dst = panel;
  for (int b = 0; b < w->K / 32; b++, src += 160) {
    float s[8];
    memcpy(s, src, 32);
    const uint8_t *q = src + 32;
#if PII_VEC && defined(__has_builtin)
#if __has_builtin(__builtin_shufflevector) && __has_builtin(__builtin_convertvector)
#define PII_DEQUANT_VEC 1
    typedef uint8_t u8x16 __attribute__((vector_size(16)));
    typedef int8_t i8x16 __attribute__((vector_size(16)));
    const f4 s_lo = v_load(s), s_hi = v_load(s + 4);
    for (int r = 0; r < 32; r += 4, q += 16, dst += 32) {
      u8x16 raw;
      memcpy(&raw, q, 16);  // 4 rows x 4 bytes
      i8x16 lo = (i8x16)(u8x16)(raw << 4) >> 4;  // columns 0..3 (signed nibble)
      i8x16 hi = (i8x16)raw >> 4;                // columns 4..7
#define ROW(i)                                                                                                  \
  v_store(dst + 8 * (i), __builtin_convertvector(__builtin_shufflevector(lo, lo, 4 * (i), 4 * (i) + 1, 4 * (i) + 2, 4 * (i) + 3), f4) * s_lo); \
  v_store(dst + 8 * (i) + 4, __builtin_convertvector(__builtin_shufflevector(hi, hi, 4 * (i), 4 * (i) + 1, 4 * (i) + 2, 4 * (i) + 3), f4) * s_hi);
      ROW(0)
      ROW(1)
      ROW(2)
      ROW(3)
#undef ROW
    }
#endif
#endif
#ifndef PII_DEQUANT_VEC
    for (int r = 0; r < 32; r++, dst += 8) {
      for (int j = 0; j < 4; j++) {
        uint8_t byte = q[r * 4 + j];
        dst[j] = (float)((int8_t)(uint8_t)(byte << 4) >> 4) * s[j];
        dst[j + 4] = (float)((int8_t)byte >> 4) * s[j + 4];
      }
    }
#endif
  }
}

// x[i] = LayerNorm(a[i] + b[i]) for L rows of H (b may be 0). Two-pass mean / variance.
static void add_layernorm(const pii_engine *m, float *x, const float *a, const float *b, int L, const float *g, const float *be) {
  const int H = m->H;
  for (int i = 0; i < L; i++) {
    float *o = x + (size_t)i * H;
    const float *pa = a + (size_t)i * H, *pb = b ? b + (size_t)i * H : 0;
    f4 s = v_splat(0);
    for (int c = 0; c < H; c += 4) {
      f4 t = v_load(pa + c);
      if (pb) t = v_add(t, v_load(pb + c));
      v_store(o + c, t);
      s = v_add(s, t);
    }
    float mean = (v_lane(s, 0) + v_lane(s, 1) + v_lane(s, 2) + v_lane(s, 3)) / (float)H;
    f4 mv = v_splat(mean), sv = v_splat(0);
    for (int c = 0; c < H; c += 4) {
      f4 d = v_sub(v_load(o + c), mv);
      sv = v_add(sv, v_mul(d, d));
    }
    float var = (v_lane(sv, 0) + v_lane(sv, 1) + v_lane(sv, 2) + v_lane(sv, 3)) / (float)H;
    f4 inv = v_splat(1.0f / PII_SQRTF(var + m->eps));
    for (int c = 0; c < H; c += 4) {
      f4 d = v_mul(v_sub(v_load(o + c), mv), inv);
      v_store(o + c, v_add(v_mul(d, v_load(g + c)), v_load(be + c)));
    }
  }
}

// exp(x) for x <= 0 (softmax): Cody-Waite range reduction, degree-6 polynomial, about 1 ulp.
static VI f4 vexp(f4 x) {
  x = v_max(x, v_splat(-87.0f));
  f4 n = v_nearest(v_mul(x, v_splat(1.44269504088896341f)));
  f4 r = v_sub(x, v_mul(n, v_splat(0.693359375f)));
  r = v_sub(r, v_mul(n, v_splat(-2.12194440e-4f)));
  f4 p = v_splat(1.9875691500e-4f);
  p = v_add(v_mul(p, r), v_splat(1.3981999507e-3f));
  p = v_add(v_mul(p, r), v_splat(8.3334519073e-3f));
  p = v_add(v_mul(p, r), v_splat(4.1665795894e-2f));
  p = v_add(v_mul(p, r), v_splat(1.6666665459e-1f));
  p = v_add(v_mul(p, r), v_splat(5.0000001201e-1f));
  p = v_add(v_mul(v_mul(p, r), r), v_add(r, v_splat(1.0f)));
  i4 e = v_i4_shl(v_i4_add(v_cvt_i4(n), v_i4_splat(127)), 23);
  return v_mul(p, v_from_bits(e));
}

// Multiply-add of the gemm kernel: separate mul and add by default (bit-identical to the baseline wasm build on every
// CPU); with PII_FMA one fused multiply-add, as f32x4.relaxed_madd in the relaxed-SIMD wasm build (identical to it where
// that build runs FMA, e.g. Chrome on arm64), about twice as fast. Needs NEON (arm64) or FMA3 (x86-64 with -mfma).
#if defined(PII_FMA) && PII_VEC && defined(__aarch64__)
#include <arm_neon.h>
#define MADD(c, a, b) ((f4)vfmaq_f32((float32x4_t)(c), (float32x4_t)(a), (float32x4_t)(b)))
#elif defined(PII_FMA) && PII_VEC && defined(__FMA__)
#include <immintrin.h>
#define MADD(c, a, b) ((f4)_mm_fmadd_ps((__m128)(a), (__m128)(b), (__m128)(c)))
#elif defined(PII_FMA)
#error "PII_FMA needs arm64 NEON or x86-64 FMA3 (-mfma)"
#else
#define MADD(c, a, b) v_add(c, v_mul(a, b))
#endif
#if defined(__clang__)
#define UNROLL _Pragma("clang loop unroll(full)")
#elif defined(__GNUC__)
#define UNROLL _Pragma("GCC unroll 16")
#else
#define UNROLL
#endif

// C[M, ldc] (columns 0..N-1, N a multiple of 8) = A[M, lda] (columns 0..K-1) x B[K, ldb]; fp32, k in order.
// Register tile: TILE_M rows x 8 columns (2 x TILE_M accumulators).
static void gemm(float *C, int ldc, const float *A, int lda, const float *B, int ldb, int Mr, int N, int K) {
  for (int n0 = 0; n0 < N; n0 += 8) {
    const float *Bn = B + n0;
    int r = 0;
    for (; r + TILE_M <= Mr; r += TILE_M) {
      const float *a = A + (size_t)r * lda;
      f4 c0[TILE_M], c1[TILE_M];
      UNROLL for (int i = 0; i < TILE_M; i++) c0[i] = c1[i] = v_splat(0);
      for (int k = 0; k < K; k++) {
        f4 w0 = v_load(Bn + (size_t)k * ldb), w1 = v_load(Bn + (size_t)k * ldb + 4);
        UNROLL for (int i = 0; i < TILE_M; i++) {
          f4 x = v_splat(a[(size_t)i * lda + k]);
          c0[i] = MADD(c0[i], x, w0);
          c1[i] = MADD(c1[i], x, w1);
        }
      }
      UNROLL for (int i = 0; i < TILE_M; i++) {
        v_store(C + (size_t)(r + i) * ldc + n0, c0[i]);
        v_store(C + (size_t)(r + i) * ldc + n0 + 4, c1[i]);
      }
    }
    for (; r < Mr; r++) {
      const float *a0 = A + (size_t)r * lda;
      f4 c00 = v_splat(0), c01 = c00;
      for (int k = 0; k < K; k++) {
        f4 x0 = v_splat(a0[k]);
        c00 = MADD(c00, x0, v_load(Bn + (size_t)k * ldb));
        c01 = MADD(c01, x0, v_load(Bn + (size_t)k * ldb + 4));
      }
      v_store(C + (size_t)r * ldc + n0, c00);
      v_store(C + (size_t)r * ldc + n0 + 4, c01);
    }
  }
}

// out[L, ldo] (columns 0..N-1) = A[L, lda] (columns 0..K-1) x W + bias, one dequantized 8-column panel at a time.
static void matmul(pii_ws *ws, float *out, int ldo, const float *A, int lda, int L, const pii_q4 *w, const float *bias) {
  for (int p = 0; p < w->Np / 8; p++) {
    const int n0 = p * 8;
    dequant_panel(w, p, ws->panel);
    if (n0 + 8 <= w->N) {
      gemm(out + n0, ldo, A, lda, ws->panel, 8, L, 8, w->K);
      if (bias) {
        f4 b0 = v_load(bias + n0), b1 = v_load(bias + n0 + 4);
        for (int r = 0; r < L; r++) {
          float *o = out + (size_t)r * ldo + n0;
          v_store(o, v_add(v_load(o), b0));
          v_store(o + 4, v_add(v_load(o + 4), b1));
        }
      }
    } else {  // last, partial panel (the classifier): through a scratch block
      gemm(ws->tail, 8, A, lda, ws->panel, 8, L, 8, w->K);
      for (int r = 0; r < L; r++)
        for (int j = 0; n0 + j < w->N; j++) out[(size_t)r * ldo + n0 + j] = ws->tail[r * 8 + j] + (bias ? bias[n0 + j] : 0.f);
    }
  }
}

static VI float hsum(f4 v) { return v_lane(v, 0) + v_lane(v, 1) + v_lane(v, 2) + v_lane(v, 3); }
static VI float hmax(f4 v) {
  float a = v_lane(v, 0), b = v_lane(v, 1), c = v_lane(v, 2), d = v_lane(v, 3);
  a = a > b ? a : b;
  c = c > d ? c : d;
  return a > c ? a : c;
}

// Multi-head self attention without a mask (one unpadded sequence): ctx = softmax(q k^T) v per head;
// q and k are each scaled by 32^-1/4, as in the exported graph. Scores and P.V use the gemm kernel.
static void attention(const pii_engine *m, pii_ws *w, int L) {
  const int H = m->H;
  const int Lp = (L + 7) & ~7;  // keys padded to 8 (padded scores are dropped before the softmax)
  const f4 qs = v_splat(0.4204482138156891f);
  for (int i = 0; i < L * H; i += 4) {
    v_store(w->q + i, v_mul(v_load(w->q + i), qs));
    v_store(w->k + i, v_mul(v_load(w->k + i), qs));
  }
  float *kt = w->kt, *S = w->scores;
  for (int h = 0; h < m->heads; h++) {
    const int o = h * HEAD;
    for (int d = 0; d < HEAD; d++) {
      for (int j = 0; j < L; j++) kt[d * Lp + j] = w->k[(size_t)j * H + o + d];
      for (int j = L; j < Lp; j++) kt[d * Lp + j] = 0.f;
    }
    gemm(S, Lp, w->q + o, H, kt, Lp, L, Lp, HEAD);
    for (int i = 0; i < L; i++) {
      float *s = S + (size_t)i * Lp;
      for (int j = L; j < Lp; j++) s[j] = -3.0e38f;
      f4 mx = v_splat(-3.0e38f);
      for (int j = 0; j < Lp; j += 4) mx = v_max(mx, v_load(s + j));
      f4 vm = v_splat(hmax(mx)), sum = v_splat(0);
      for (int j = 0; j < Lp; j += 4) {
        f4 e = vexp(v_sub(v_load(s + j), vm));
        if (j + 4 > L) {  // zero the padded keys
          float t[4];
          v_store(t, e);
          for (int l = 0; l < 4; l++)
            if (j + l >= L) t[l] = 0.f;
          e = v_load(t);
        }
        v_store(s + j, e);
        sum = v_add(sum, e);
      }
      const f4 inv = v_splat(1.0f / hsum(sum));
      for (int j = 0; j < Lp; j += 4) v_store(s + j, v_mul(v_load(s + j), inv));
    }
    gemm(w->ctx + o, H, S, Lp, w->v + o, H, L, HEAD, L);
  }
}

// GELU(x) = 0.5 x (1 + erf(x / sqrt 2)); erf from Abramowitz & Stegun 7.1.26 (absolute error <= 1.5e-7).
static void gelu(float *x, size_t n) {
  const f4 half = v_splat(0.5f), one = v_splat(1.0f), rs2 = v_splat(0.70710678118654752f);
  for (size_t i = 0; i < n; i += 4) {
    f4 v = v_load(x + i);
    f4 z = v_mul(v, rs2);
    f4 a = v_abs(z);
    f4 t = v_div(one, v_add(one, v_mul(a, v_splat(0.3275911f))));
    f4 p = v_splat(1.061405429f);
    p = v_add(v_mul(p, t), v_splat(-1.453152027f));
    p = v_add(v_mul(p, t), v_splat(1.421413741f));
    p = v_add(v_mul(p, t), v_splat(-0.284496736f));
    p = v_add(v_mul(p, t), v_splat(0.254829592f));
    p = v_mul(p, t);
    f4 e = v_sub(one, v_mul(p, vexp(v_neg(v_mul(a, a)))));
    e = v_from_bits(v_i4_or(v_bits(e), v_i4_and(v_bits(z), v_i4_splat((int32_t)0x80000000u))));  // copysign
    v_store(x + i, v_mul(v_mul(v, v_add(e, one)), half));
  }
}

// ---------------------------------------------------------------- build
const char *pii_build_info(void) {
#if !PII_VEC
#define PII_ISA "scalar"
#elif defined(__aarch64__) || defined(__ARM_NEON)
#define PII_ISA "neon"
#elif defined(__x86_64__) || defined(__i386__)
#define PII_ISA "sse"
#else
#define PII_ISA "vector"
#endif
#ifdef PII_FMA
  return PII_ISA "+fma";
#else
  return PII_ISA;
#endif
}

// ---------------------------------------------------------------- forward
const float *pii_engine_run(const pii_engine *m, pii_ws *w, const int32_t *ids, int L) {
  if (L <= 0 || L > pii_engine_max_len(m) || L > w->cap) return NULL;
  const int H = m->H, E = m->E;
  // embeddings: word (+ projection) + token type 0 + position (RoBERTa positions: padding_idx + 1 + index)
  for (int i = 0; i < L; i++) {
    int id = ids[i];
    if (id < 0 || id >= m->V) id = 0;
    emb_row(&m->word, id, w->emb + (size_t)i * E);
  }
  if (m->has_proj) matmul(w, w->x2, H, w->emb, E, L, &m->proj, 0);
  else
    for (int i = 0; i < L * H; i++) w->x2[i] = w->emb[i];
  int p = m->pad;
  float *posrow = w->ctx;  // scratch
  for (int i = 0; i < L; i++) {
    if (ids[i] != m->pad) p++;
    emb_row(&m->pos, ids[i] != m->pad ? p : m->pad, posrow);
    float *xr = w->x2 + (size_t)i * H;
    for (int c = 0; c < H; c++) xr[c] = (xr[c] + m->typerow[c]) + posrow[c];
  }
  add_layernorm(m, w->x, w->x2, 0, L, m->elng, m->elnb);
  for (int a = 0; a < m->NA; a++) {
    const pii_layer *y = &m->layers[m->apply[a]];
    matmul(w, w->q, H, w->x, H, L, &y->wq, y->bq);
    matmul(w, w->k, H, w->x, H, L, &y->wk, y->bk);
    matmul(w, w->v, H, w->x, H, L, &y->wv, y->bv);
    attention(m, w, L);
    matmul(w, w->x2, H, w->ctx, H, L, &y->wo, y->bo);
    add_layernorm(m, w->x, w->x2, w->x, L, y->ln1g, y->ln1b);
    matmul(w, w->ff, m->I, w->x, H, L, &y->wi, y->bi);
    gelu(w->ff, (size_t)L * m->I);
    matmul(w, w->x2, H, w->ff, m->I, L, &y->wout, y->bout);
    add_layernorm(m, w->x, w->x2, w->x, L, y->ln2g, y->ln2b);
  }
  matmul(w, w->logits, m->T, w->x, H, L, &m->cls, m->bcls);
  return w->logits;
}
