// The part of the redactor that runs the model: token windows in, logits out. Two implementations share the same ONNX
// file, tokenizer, windows and decoding: the built-in WebAssembly engine (engine/pii.c, ~6 KB) and onnxruntime-web.

export type EngineName = "builtin" | "onnxruntime";

export interface Engine {
  readonly name: EngineName;
  /** Logits per window: one [ids.length, nTags] row-major block each. */
  run(windows: { ids: number[] }[]): Promise<Float32Array[]>;
  dispose(): Promise<void>;
}
