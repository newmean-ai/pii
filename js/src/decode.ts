// Windowing and BIO decoding, ported from the Python evaluator (training/scripts/evaluate.py OnnxTokenPredictor), which
// produced the ONNX numbers in the technical report.
import type { Encoding } from "./tokenizer.js";
import type { Span } from "./types.js";

export interface Window {
  start: number; // index of the first token of this window in the text's token list
  ids: number[]; // <s> + tokens + </s>
}

/** Splits a token sequence into windows of `window` tokens (including <s> and </s>) that overlap by `stride` tokens. */
export function makeWindows(ids: number[], bos: number, eos: number, window = 256, stride = 64): Window[] {
  const win = window - 2;
  const starts: number[] = [];
  if (ids.length > win) {
    for (let s = 0; s < Math.max(1, ids.length - stride); s += win - stride) starts.push(s);
  } else starts.push(0);
  return starts.map((s) => ({ start: s, ids: [bos, ...ids.slice(s, s + win), eos] }));
}

/** Per-token tag index. Where windows overlap, a token takes the prediction of the window in which it sits farther
 *  from the edge. `logits` holds one [n_tokens + 2, n_tags] row-major block per window. */
export function bestTags(nTokens: number, windows: Window[], logits: Float32Array[], nTags: number, oBias = 0): Int32Array {
  const tag = new Int32Array(nTokens).fill(0);
  const dist = new Int32Array(nTokens).fill(-1);
  windows.forEach((w, k) => {
    const n = w.ids.length - 2;
    const lg = logits[k];
    for (let j = 0; j < n; j++) {
      const base = (j + 1) * nTags;
      let best = 0;
      let bestV = lg[base] + oBias;
      for (let t = 1; t < nTags; t++) {
        if (lg[base + t] > bestV) {
          bestV = lg[base + t];
          best = t;
        }
      }
      const c = Math.min(j, n - 1 - j);
      const pos = w.start + j;
      if (c > dist[pos]) {
        dist[pos] = c;
        tag[pos] = best;
      }
    }
  });
  return tag;
}

/** BIO tags -> character spans; then same-label spans separated only by whitespace are merged. */
export function decodeSpans(text: string, enc: Encoding, tags: Int32Array, tagNames: string[]): Span[] {
  const spans: Span[] = [];
  let cur: Span | null = null;
  enc.offsets.forEach(([s, e], j) => {
    if (s === e) return;
    const raw = tagNames[tags[j]];
    if (raw === "O") {
      cur = null;
      return;
    }
    const label = raw.slice(2);
    if (cur && cur.label === label && !raw.startsWith("B-")) cur.end = e;
    else {
      cur = { start: s, end: e, label, source: "model" };
      spans.push(cur);
    }
  });
  return mergeAdjacent(spans, text);
}

export function mergeAdjacent(spans: Span[], text: string): Span[] {
  const out: Span[] = [];
  for (const sp of [...spans].sort((a, b) => a.start - b.start)) {
    const last = out[out.length - 1];
    if (last && last.label === sp.label && text.slice(last.end, sp.start).trim() === "") last.end = Math.max(last.end, sp.end);
    else out.push({ ...sp });
  }
  return out;
}
