// Built-in engine: a freestanding WebAssembly module (engine/pii.c; no libc, no imports) that runs exactly the v1 encoder.
// It reads the published ONNX file: onnxToPiiw lays the initializers out for its kernels, keeping the same 4-bit values and
// scales. Two builds: SIMD128, and relaxed SIMD (fused multiply-add) where the browser supports it (Chrome, Edge, Firefox).
import type { Engine } from "./engine.js";
import { onnxToPiiw } from "./onnx-weights.js";

interface Exports {
  memory: WebAssembly.Memory;
  pii_alloc(n: number): number;
  pii_load(ptr: number, len: number): number;
  pii_tags(): number;
  pii_max_len(): number;
  pii_ids(n: number): number;
  pii_run(ids: number, n: number): number;
}

export interface BuiltinEngineOptions {
  /** Use the fused multiply-add build. Default: where the browser supports relaxed SIMD. */
  relaxedSimd?: boolean;
}

// () -> v128 { f32x4.relaxed_madd(0, 0, 0) }: validates only where relaxed SIMD is available.
const RELAXED_PROBE = new Uint8Array([
  0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 61, 1, 59, 0,
  ...[0, 1, 2].flatMap(() => [0xfd, 0x0c, ...new Array(16).fill(0)]), 0xfd, 0x85, 0x02, 0x0b,
]);

export function relaxedSimdSupported(): boolean {
  try {
    return WebAssembly.validate(RELAXED_PROBE);
  } catch {
    return false;
  }
}

const fromBase64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function createBuiltinEngine(model: Uint8Array, nTags: number, options: BuiltinEngineOptions): Promise<Engine> {
  const relaxed = options.relaxedSimd ?? relaxedSimdSupported();
  const { WASM } = relaxed ? await import("./wasm-relaxed.js") : await import("./wasm-simd.js");
  const { instance } = await WebAssembly.instantiate(fromBase64(WASM), {});
  const ex = instance.exports as unknown as Exports;
  const weights = onnxToPiiw(model);
  const ptr = ex.pii_alloc(weights.length);
  if (!ptr) throw new Error("[@newmean-ai/pii] built-in engine: out of memory");
  new Uint8Array(ex.memory.buffer, ptr, weights.length).set(weights);
  const rc = ex.pii_load(ptr, weights.length);
  if (rc !== 0) throw new Error(`[@newmean-ai/pii] built-in engine: unsupported model file (code ${rc})`);
  if (ex.pii_tags() !== nTags) throw new Error(`[@newmean-ai/pii] built-in engine: model has ${ex.pii_tags()} tags, expected ${nTags}`);
  const maxLen = ex.pii_max_len();

  return {
    name: "builtin",
    async run(windows) {
      return windows.map(({ ids }) => {
        const L = ids.length;
        const ip = L <= maxLen ? ex.pii_ids(L) : 0;
        if (!ip) throw new Error(`[@newmean-ai/pii] built-in engine: window of ${L} tokens (max ${maxLen})`);
        new Int32Array(ex.memory.buffer, ip, L).set(ids);
        const out = ex.pii_run(ip, L);
        if (!out) throw new Error("[@newmean-ai/pii] built-in engine: out of memory");
        return new Float32Array(ex.memory.buffer, out, L * nTags).slice();
      });
    },
    async dispose() {},
  };
}
