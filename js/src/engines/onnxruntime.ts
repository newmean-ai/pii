// onnxruntime-web engine. The module is imported on first use, so apps on the built-in engine never load it.
import type * as OrtModule from "onnxruntime-web/wasm";
import type { Engine } from "./engine.js";

export type Ort = typeof OrtModule;

export interface OrtEngineOptions {
  wasmPaths?: string;
  threads?: number;
  ort?: Ort;
}

const BATCH = 8;

export async function createOrtEngine(model: Uint8Array, padId: number, nTags: number, options: OrtEngineOptions): Promise<Engine> {
  const ort = options.ort ?? (await import("onnxruntime-web/wasm"));
  if (options.wasmPaths) ort.env.wasm.wasmPaths = options.wasmPaths;
  if (options.threads) ort.env.wasm.numThreads = options.threads;
  const session = await ort.InferenceSession.create(model, { executionProviders: ["wasm"], graphOptimizationLevel: "all" });

  return {
    name: "onnxruntime",
    async run(windows) {
      const out: Float32Array[] = [];
      for (let i = 0; i < windows.length; i += BATCH) {
        const chunk = windows.slice(i, i + BATCH);
        const L = Math.max(...chunk.map((w) => w.ids.length));
        const ids = new BigInt64Array(chunk.length * L).fill(BigInt(padId));
        const mask = new BigInt64Array(chunk.length * L);
        chunk.forEach((w, b) => {
          w.ids.forEach((id, j) => {
            ids[b * L + j] = BigInt(id);
            mask[b * L + j] = 1n;
          });
        });
        const res = await session.run({
          input_ids: new ort.Tensor("int64", ids, [chunk.length, L]),
          attention_mask: new ort.Tensor("int64", mask, [chunk.length, L]),
        });
        const logits = res.logits.data as Float32Array;
        chunk.forEach((_, b) => out.push(logits.subarray(b * L * nTags, (b + 1) * L * nTags)));
        for (const t of Object.values(res)) (t as { dispose?: () => void }).dispose?.();
      }
      return out;
    },
    async dispose() {
      await session.release();
    },
  };
}
