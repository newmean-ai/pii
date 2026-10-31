import { bestTags, decodeSpans, makeWindows } from "./decode.js";
import { loadFile, type Progress } from "./loader.js";
import { MODEL_FILES, MODEL_VERSION, type Size, type Variant } from "./models.js";
import { regexSpans } from "./regex.js";
import { Tokenizer, type WebTokenizerFile } from "./tokenizer.js";
import type { Label, Span } from "./types.js";
import type { Engine, EngineName } from "./engines/engine.js";
import type { Ort } from "./engines/onnxruntime.js";

/** Tag list of the v1 models (index = model output). */
export const TAGS = [
  "O",
  ...(
    ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"] as const
  ).flatMap((l) => [`B-${l}`, `I-${l}`]),
];

export const LABELS: Label[] = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"];
/** Level 2 = indirect identifiers: a surname with a title, a nickname, initials (PERSON_L2) and public profile or page URLs (URL_L2). */
export const LEVEL1_LABELS: Label[] = LABELS.filter((l) => !l.endsWith("_L2"));

export const DEFAULT_VARIANT: Record<Size, Variant> = { large: "q4e", medium: "q4e", small: "q4", nano: "q4" };

export interface RedactorOptions {
  /** What runs the model. "builtin": the package's own WebAssembly engine (about 6 KB, no extra download; fused
   *  multiply-add where the browser supports relaxed SIMD). "onnxruntime": onnxruntime-web (about 2.4 MB of WebAssembly,
   *  loaded on first use). Both read the same model file and give the same spans. Default "builtin" (faster on the phones and
   *  laptops measured, Appendix P of the technical report). */
  engine?: EngineName;
  /** Model size: nano (≈2.6 MB), small (≈7 MB), medium (≈11 MB), large (≈15 MB). Default "medium". */
  size?: Size;
  /** Weight file: "q4" (4-bit layers, 8-bit embeddings) or "q4e" (4-bit embeddings too). Default: q4e for large and medium, q4 otherwise. */
  variant?: Variant;
  /** Base URL of the model folder (must end with "/"). Default: the Hugging Face repository newmean-ai/pii-<size> at the model version tag.
   *  Self-hosted copies are checked against the same SHA-256 values. */
  modelUrl?: string;
  /** Moves the decision threshold: > 0 masks more (fewer misses, more over-masking), < 0 masks less. Range about −1..1. Default 0. */
  sensitivity?: number;
  /** Labels to mask. Default: all (LABELS). Use LEVEL1_LABELS to leave level 2 identifiers visible. */
  labels?: readonly string[];
  /** Also mask matches of a few patterns (email, URL, card/account, phone, Korean resident number). Default false: on the
   *  public test of newmean-pii-dataset they add almost no coverage and double over-masking (dates and timestamps match the account pattern). */
  regex?: boolean;
  /** Keep downloaded model files in the Cache API. Default true. */
  cache?: boolean;
  /** Check every model file against its published SHA-256. Default true. */
  verify?: boolean;
  onProgress?: (p: Progress) => void;
  /** builtin engine: use the fused multiply-add build. Default: where the browser supports relaxed SIMD. */
  relaxedSimd?: boolean;
  /** onnxruntime engine: where onnxruntime-web loads its .wasm file from (onnxruntime `env.wasm.wasmPaths`). Default: next to the bundle. */
  wasmPaths?: string;
  /** onnxruntime engine: WebAssembly threads (needs a cross-origin isolated page). Default: onnxruntime's choice. */
  threads?: number;
  fetch?: typeof fetch;
  /** onnxruntime engine, advanced: a preloaded onnxruntime-web module. */
  ort?: Ort;
}

export interface RedactOptions {
  /** "tag" → "[EMAIL]", "mask" → "*****" (one per character), "blank" → removed. Default "tag". */
  mode?: "tag" | "mask" | "blank";
  maskChar?: string;
  /** Custom replacement; overrides `mode`. */
  replace?: (span: Span, original: string) => string;
}

export interface RunStats {
  tokens: number;
  windows: number;
  ms: number;
}

export interface Redactor {
  readonly engine: EngineName;
  readonly size: Size;
  readonly variant: Variant;
  readonly version: string;
  /** Spans to mask, sorted and non-overlapping. */
  detect(text: string): Promise<Span[]>;
  redact(text: string, options?: RedactOptions): Promise<{ text: string; spans: Span[] }>;
  /** Changes the threshold without reloading the model. */
  setSensitivity(value: number): void;
  readonly lastRun: RunStats | null;
  dispose(): Promise<void>;
}

const family = (l: string) => l.replace(/_L2$/, "");

/** Model and regex spans → masking regions. Overlapping spans become one region; the model's label wins, and a regex
 *  label is used only where no model label of the same family (URL / URL_L2) covers the text. */
export function combine(model: Span[], regex: Span[], enabled: Set<string>): Span[] {
  const all = [...model, ...regex].sort((a, b) => a.start - b.start || b.end - a.end);
  const groups: Span[][] = [];
  let end = -1;
  for (const s of all) {
    if (groups.length && s.start < end) {
      groups[groups.length - 1].push(s);
      end = Math.max(end, s.end);
    } else {
      groups.push([s]);
      end = s.end;
    }
  }
  const out: Span[] = [];
  for (const g of groups) {
    const m = g.filter((s) => s.source === "model").sort((a, b) => b.end - b.start - (a.end - a.start));
    const modelFamilies = new Set(m.map((s) => family(s.label)));
    const r = g.filter((s) => s.source === "regex" && !modelFamilies.has(s.label));
    const use = [...m, ...r].filter((s) => enabled.has(s.label));
    if (!use.length) continue;
    const hasModel = use.some((s) => s.source === "model");
    const hasRegex = use.some((s) => s.source === "regex");
    out.push({
      start: Math.min(...use.map((s) => s.start)),
      end: Math.max(...use.map((s) => s.end)),
      label: use[0].label,
      source: hasModel && hasRegex ? "model+regex" : hasModel ? "model" : "regex",
    });
  }
  return out;
}

export function applyRedaction(text: string, spans: Span[], options: RedactOptions = {}): string {
  const mode = options.mode ?? "tag";
  let out = "";
  let last = 0;
  for (const s of spans) {
    const orig = text.slice(s.start, s.end);
    out += text.slice(last, s.start);
    out += options.replace
      ? options.replace(s, orig)
      : mode === "tag"
        ? `[${family(s.label)}]`
        : mode === "mask"
          ? (options.maskChar ?? "*").repeat(Array.from(orig).length)
          : "";
    last = s.end;
  }
  return out + text.slice(last);
}

export async function createRedactor(options: RedactorOptions = {}): Promise<Redactor> {
  const size: Size = options.size ?? "medium";
  if (!(size in MODEL_FILES)) throw new Error(`[@newmean-ai/pii] unknown size "${size}" (nano, small, medium, large)`);
  const variant: Variant = options.variant ?? DEFAULT_VARIANT[size];
  const files = MODEL_FILES[size];
  const base = options.modelUrl ?? `https://huggingface.co/newmean-ai/pii-${size}/resolve/v${MODEL_VERSION}/`;
  const load = {
    cache: options.cache ?? true,
    verify: options.verify ?? true,
    fetch: options.fetch ?? globalThis.fetch.bind(globalThis),
    onProgress: options.onProgress,
  };
  const engineName: EngineName = options.engine ?? "builtin";
  if (engineName !== "builtin" && engineName !== "onnxruntime") throw new Error(`[@newmean-ai/pii] unknown engine "${engineName}" (builtin, onnxruntime)`);

  const [tokBytes, modelBytes] = await Promise.all([
    loadFile(base + "tokenizer.web.json", "tokenizer.web.json", files.tokenizer, load),
    loadFile(base + `onnx/model_${variant}.onnx`, `model_${variant}.onnx`, files[variant], load),
  ]);
  const tokenizer = new Tokenizer(JSON.parse(new TextDecoder().decode(tokBytes)) as WebTokenizerFile);
  const engine: Engine =
    engineName === "builtin"
      ? await (await import("./engines/builtin.js")).createBuiltinEngine(modelBytes, TAGS.length, options)
      : await (await import("./engines/onnxruntime.js")).createOrtEngine(modelBytes, tokenizer.padId, TAGS.length, options);

  let oBias = -(options.sensitivity ?? 0);
  const enabled = new Set<string>(options.labels ?? LABELS);
  const useRegex = options.regex ?? false;
  let queue: Promise<unknown> = Promise.resolve(); // one model call at a time
  let lastRun: RunStats | null = null;

  async function modelSpans(text: string): Promise<Span[]> {
    const t0 = performance.now();
    const enc = tokenizer.encode(text);
    if (!enc.ids.length) {
      lastRun = { tokens: 0, windows: 0, ms: performance.now() - t0 };
      return [];
    }
    const windows = makeWindows(enc.ids, tokenizer.bosId, tokenizer.eosId);
    const logits = await engine.run(windows);
    const tags = bestTags(enc.ids.length, windows, logits, TAGS.length, oBias);
    const spans = decodeSpans(text, enc, tags, TAGS);
    lastRun = { tokens: enc.ids.length, windows: windows.length, ms: performance.now() - t0 };
    return spans;
  }

  async function detect(text: string): Promise<Span[]> {
    const run = queue.then(() => modelSpans(text));
    queue = run.catch(() => undefined);
    const model = await run;
    return combine(model, useRegex ? regexSpans(text) : [], enabled);
  }

  return {
    engine: engineName,
    size,
    variant,
    version: MODEL_VERSION,
    detect,
    async redact(text, opts) {
      const spans = await detect(text);
      return { text: applyRedaction(text, spans, opts), spans };
    },
    setSensitivity(value: number) {
      oBias = -value;
    },
    get lastRun() {
      return lastRun;
    },
    async dispose() {
      await queue;
      await engine.dispose();
    },
  };
}
