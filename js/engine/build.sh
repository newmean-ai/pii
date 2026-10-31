#!/usr/bin/env bash
# Builds pii.c into build/pii.wasm (wasm32 + SIMD128) and build/pii-relaxed.wasm (+ relaxed SIMD: fused
# multiply-add). No libc, no imports. Needs LLVM clang with the wasm32 target and wasm-ld (macOS: brew install llvm lld);
# wasm-opt (binaryen) is optional. Then node scripts/embed-wasm.mjs. Extra compiler flags: CFLAGS="..." ./build.sh
set -euo pipefail
cd "$(dirname "$0")"
CLANG=${CLANG:-$(command -v /opt/homebrew/opt/llvm/bin/clang || command -v clang)}
mkdir -p build
build() {
  local out=$1; shift
  "$CLANG" --target=wasm32-unknown-unknown -O3 -msimd128 -mbulk-memory -mnontrapping-fptoint -ffreestanding -nostdlib \
    -fno-exceptions -Wall -Wextra -Wno-unused-parameter ${CFLAGS:-} "$@" \
    -Wl,--no-entry -Wl,--strip-all -Wl,--initial-memory=1048576 -Wl,-z,stack-size=262144 \
    -o "build/$out" pii.c
  if command -v wasm-opt >/dev/null; then
    wasm-opt -O3 --enable-simd --enable-bulk-memory --enable-nontrapping-float-to-int --enable-relaxed-simd "build/$out" -o "build/$out"
  fi
}
build pii.wasm
build pii-relaxed.wasm -mrelaxed-simd -DPII_RELAXED -DTILE_M=6  # with FMA, 6 rows per tile pays off (loads become the limit)
ls -l build/pii.wasm build/pii-relaxed.wasm
