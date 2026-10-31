// 4 x f32 vector layer for the engine. GCC/Clang vector extensions (NEON on arm64, SSE on x86-64) or, with PII_SCALAR or
// another compiler, a plain struct with the same lane-wise IEEE operations. Every function maps to one wasm SIMD128
// instruction of js/engine/pii.c, so results are bit-identical to its baseline (non-relaxed) build as long as the compiler
// does not fuse multiply and add: build with -ffp-contract=off (the pragma below covers Clang only).
#ifndef PII_SIMD_H
#define PII_SIMD_H
#include <stdint.h>
#include <string.h>

#if defined(__clang__)
#pragma STDC FP_CONTRACT OFF
#endif

#if (defined(__GNUC__) || defined(__clang__)) && !defined(PII_SCALAR)
#define PII_VEC 1
typedef float f4 __attribute__((vector_size(16)));
typedef int32_t i4 __attribute__((vector_size(16)));
#define VI inline __attribute__((always_inline))

static VI f4 v_load(const float *p) {
  f4 v;
  memcpy(&v, p, 16);
  return v;
}
static VI void v_store(float *p, f4 v) { memcpy(p, &v, 16); }
static VI f4 v_splat(float x) { return (f4){x, x, x, x}; }
static VI f4 v_add(f4 a, f4 b) { return a + b; }
static VI f4 v_sub(f4 a, f4 b) { return a - b; }
static VI f4 v_mul(f4 a, f4 b) { return a * b; }
static VI f4 v_div(f4 a, f4 b) { return a / b; }
static VI float v_lane(f4 a, int i) { return a[i]; }
static VI i4 v_bits(f4 a) { return (i4)a; }
static VI f4 v_from_bits(i4 a) { return (f4)a; }
static VI i4 v_cvt_i4(f4 a) { return __builtin_convertvector(a, i4); }  // truncation (values here are in int range)
// lane-wise a > b ? a : b (wasm f32x4.max for non-NaN input)
static VI f4 v_max(f4 a, f4 b) {
  i4 m = (i4)(a > b);
  return (f4)(((i4)a & m) | ((i4)b & ~m));
}
static VI i4 v_i4_splat(int32_t x) { return (i4){x, x, x, x}; }
static VI i4 v_i4_add(i4 a, i4 b) { return a + b; }
static VI i4 v_i4_shl(i4 a, int n) { return (i4)((uint32_t __attribute__((vector_size(16))))a << (uint32_t)n); }
static VI i4 v_i4_and(i4 a, i4 b) { return a & b; }
static VI i4 v_i4_or(i4 a, i4 b) { return a | b; }
static VI i4 v_i4_xor(i4 a, i4 b) { return a ^ b; }
#else
#define PII_VEC 0
typedef struct { float v[4]; } f4;
typedef struct { int32_t v[4]; } i4;
#define VI inline
#define L4(expr) for (int l_ = 0; l_ < 4; l_++) expr

static VI f4 v_load(const float *p) {
  f4 r;
  memcpy(r.v, p, 16);
  return r;
}
static VI void v_store(float *p, f4 a) { memcpy(p, a.v, 16); }
static VI f4 v_splat(float x) {
  f4 r;
  L4(r.v[l_] = x);
  return r;
}
static VI f4 v_add(f4 a, f4 b) {
  L4(a.v[l_] = a.v[l_] + b.v[l_]);
  return a;
}
static VI f4 v_sub(f4 a, f4 b) {
  L4(a.v[l_] = a.v[l_] - b.v[l_]);
  return a;
}
static VI f4 v_mul(f4 a, f4 b) {
  L4(a.v[l_] = a.v[l_] * b.v[l_]);
  return a;
}
static VI f4 v_div(f4 a, f4 b) {
  L4(a.v[l_] = a.v[l_] / b.v[l_]);
  return a;
}
static VI float v_lane(f4 a, int i) { return a.v[i]; }
static VI i4 v_bits(f4 a) {
  i4 r;
  memcpy(r.v, a.v, 16);
  return r;
}
static VI f4 v_from_bits(i4 a) {
  f4 r;
  memcpy(r.v, a.v, 16);
  return r;
}
static VI i4 v_cvt_i4(f4 a) {
  i4 r;
  L4(r.v[l_] = (int32_t)a.v[l_]);
  return r;
}
static VI f4 v_max(f4 a, f4 b) {
  L4(a.v[l_] = a.v[l_] > b.v[l_] ? a.v[l_] : b.v[l_]);
  return a;
}
static VI i4 v_i4_splat(int32_t x) {
  i4 r;
  L4(r.v[l_] = x);
  return r;
}
static VI i4 v_i4_add(i4 a, i4 b) {
  L4(a.v[l_] = (int32_t)((uint32_t)a.v[l_] + (uint32_t)b.v[l_]));
  return a;
}
static VI i4 v_i4_shl(i4 a, int n) {
  L4(a.v[l_] = (int32_t)((uint32_t)a.v[l_] << n));
  return a;
}
static VI i4 v_i4_and(i4 a, i4 b) {
  L4(a.v[l_] &= b.v[l_]);
  return a;
}
static VI i4 v_i4_or(i4 a, i4 b) {
  L4(a.v[l_] |= b.v[l_]);
  return a;
}
static VI i4 v_i4_xor(i4 a, i4 b) {
  L4(a.v[l_] ^= b.v[l_]);
  return a;
}
#endif

static VI f4 v_abs(f4 a) { return v_from_bits(v_i4_and(v_bits(a), v_i4_splat(0x7fffffff))); }
static VI f4 v_neg(f4 a) { return v_from_bits(v_i4_xor(v_bits(a), v_i4_splat((int32_t)0x80000000u))); }
// round to nearest, ties to even (wasm f32x4.nearest) for |x| < 2^22: adding and subtracting 1.5 * 2^23 rounds in the FPU
static VI f4 v_nearest(f4 a) { return v_sub(v_add(a, v_splat(12582912.0f)), v_splat(12582912.0f)); }

#endif
