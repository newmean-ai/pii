import { bestTags, decodeSpans, makeWindows } from "./decode.js";
import { loadFile, type Progress } from "./loader.js";
import { MODEL_FILES, MODEL_VERSION, type FileInfo, type Size, type Variant } from "./models.js";
import { VERSION } from "./version.js";
import { regexSpans } from "./regex.js";
import { checkDelimiter, detectTableSpans, finishDetect, headerContextCells, parseTable, type TableOptions } from "./table.js";
import { checkListsId, Lists, publicSpans, type AllowEntry, type BlockEntry } from "./lists.js";
import { restoreSpans, stripEmoji } from "./emoji.js";
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

// Option strictShortNames: texts of at most SHORT_TOKENS tokens get SHORT_PERSON_BIAS on the PERSON and PERSON_L2 logits
// (same constants as native/core/src/internal.h).
const SHORT_TOKENS = 8;
const SHORT_PERSON_BIAS = -3;

export interface RedactorOptions {
  /** What runs the model. "builtin": the package's own WebAssembly engine (about 13 KB brotli, JS and WebAssembly together,
   *  loaded on first use; fused multiply-add where the browser supports relaxed SIMD). "onnxruntime": onnxruntime-web (about
   *  2.4 MB of WebAssembly, loaded on first use). Both read the same model file and give the same spans. Default "builtin"
   *  (faster on the phones and laptops measured, Appendix P of the technical report). */
  engine?: EngineName;
  /** Model size: nano (≈2.6 MB), small (≈7 MB), medium (≈11 MB), large (≈15 MB). Default "medium". */
  size?: Size;
  /** Weight file: "q4" (4-bit layers, 8-bit embeddings) or "q4e" (4-bit embeddings too). Default: q4e for large and medium, q4 otherwise. */
  variant?: Variant;
  /** Base URL of the model folder (must end with "/"). Default: the Hugging Face repository newmean-ai/pii-<size> at the model version tag.
   *  Self-hosted copies are checked against the same SHA-256 values. */
  modelUrl?: string;
  /** Moves the decision threshold: > 0 masks more (fewer misses, more over-masking), < 0 masks less. Range about −1..1. Default 0.
   *  NaN and ±Infinity throw ("sensitivity must be a finite number"). */
  sensitivity?: number;
  /** Labels to mask. Default: all (LABELS). Use LEVEL1_LABELS to leave level 2 identifiers visible. Names that are not
   *  labels are ignored (the Python and Kotlin packages raise). */
  labels?: readonly string[];
  /** Also mask matches of a few patterns (email, URL, card/account, phone, Korean resident number). Default false: on the
   *  public test of newmean-pii-dataset they add almost no coverage and double over-masking (dates and timestamps match the account pattern). */
  regex?: boolean;
  /** false (default): emoji are taken out of the text the model reads, so the model does not read them as PII; spans keep the offsets of the input
   *  (an emoji inside a span splits it). true: the model reads the text as given (1.0 behaviour). */
  keepEmoji?: boolean;
  /** true: a span that starts or ends inside a word is widened to the whole word, so "R23" is masked whole instead of
   *  "[ID_NUMBER]23". A word is a run of letters, digits and marks, joined by apostrophes and hyphens; letters of scripts
   *  written without spaces (Chinese, Japanese, Thai, Khmer, …) never extend a span, and a word ends where Hangul meets
   *  digits or Latin letters ("010-1234-5678입니다" keeps 「입니다」), and Korean particles after a Hangul
   *  span stay ("홍길동님께" keeps 「님께」). Masks more characters. Default true (since 1.3.0); false: spans end where the model ends them (1.2 behaviour). */
  wholeWords?: boolean;
  /** true: on a text the model reads as at most 8 tokens (a survey answer, a table cell), the model needs stronger
   *  evidence to read it as a person's name, so short values such as 「경기」, 「남성」 or 「네이버」 are left alone; a lone
   *  name is missed a little more often (large: 98.9% → 96.5% of lone names found). Longer texts are unchanged. Default false. */
  strictShortNames?: boolean;
  /** Values you know are not PII in your data (survey categories: regions, brands, jobs). A span whose whole text is an
   *  entry is not masked; with `label`, only spans of that label family (PERSON covers PERSON_L2, URL covers URL_L2).
   *  ASCII letters are compared case-insensitively, nothing else is normalized: pass NFC text (`text.normalize("NFC")`;
   *  files exported on macOS may hold NFD Hangul). At most 10,000 entries of 1 to 64 code points. Since 1.4.0. */
  allow?: readonly AllowEntry[];
  /** Values you know are PII (an instructor's name): every occurrence that starts and ends on a word boundary is masked with
   *  the entry's label (source "block"), also where the model misses it and whatever `labels` enables. A Hangul entry
   *  also matches before Korean particles (「박지훈님께」 → 「[PERSON]님께」); a one-syllable entry such as 「김」 therefore
   *  masks 「김은」 and 「김도」 too. Exact forms only: 「박 지훈」 and 「박지훈」 are separate entries. Since 1.4.0. */
  block?: readonly BlockEntry[];
  /** Your name for the lists (1 to 128 code points), shown in `info.lists.id` for audit records; never derived from the
   *  entries. Since 1.4.0. */
  listsId?: string;
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
  /** The fetch used to download the model files. Default: globalThis.fetch. Pass one in Node to read self-hosted files,
   *  add headers, or serve the files from disk in tests. */
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

/** What a redactor runs: package and model versions and the model files it checked. */
export interface RedactorInfo {
  /** This package's version (VERSION). */
  packageVersion: string;
  /** Model release (MODEL_VERSION; Hugging Face tag v<modelVersion>). */
  modelVersion: string;
  size: Size;
  variant: Variant;
  engine: EngineName;
  /** Where the files were read from (modelUrl or the Hugging Face URL). */
  modelUrl: string;
  /** Published size and SHA-256 of the two files. */
  files: { model: FileInfo & { path: string }; tokenizer: FileInfo & { path: string } };
  /** true when the SHA-256 of both loaded files (downloaded or from the cache) was computed and matched `files`. false with
   *  `verify: false`, and where crypto.subtle is unavailable (insecure context): the files were then used unchecked, or
   *  checked by size only. */
  verified: boolean;
  /** Entries of the lists after merging duplicates, and the caller's listsId (null when not given). Since 1.4.0. */
  lists: { allow: number; block: number; id: string | null };
}

export interface Redactor {
  readonly engine: EngineName;
  readonly size: Size;
  readonly variant: Variant;
  /** Model release (MODEL_VERSION), not the package version: use `info.packageVersion` or `VERSION` for that. */
  readonly version: string;
  readonly info: RedactorInfo;
  /** Spans to mask, sorted and non-overlapping, widened to whole grapheme clusters (an emoji sequence is masked whole).
   *  The widening needs Intl.Segmenter; where it is missing (Hermes builds without it, older browsers) spans stay on
   *  code point boundaries and may cover part of an emoji or combining sequence. */
  detect(text: string): Promise<Span[]>;
  redact(text: string, options?: RedactOptions): Promise<{ text: string; spans: Span[] }>;
  /** Table mode for CSV, TSV, semicolon-separated and markdown pipe tables: the whole text is read at once (cells give each
   *  other context), spans are clipped to cell values, and a short-value column that the model reads as names, emails,
   *  phone numbers or usernames almost throughout is masked whole (source "table"). Grapheme widening stops at the cell
   *  value's edges. Text that is not a table gives the same spans as `detect`. */
  detectTable(text: string, table?: TableOptions): Promise<Span[]>;
  /** `detectTable` + redaction. Spans never cross cells, so delimiters, quotes and line breaks are kept and the output stays
   *  a valid table, as long as a custom `replace` does not insert the delimiter, a quote or a line break. */
  redactTable(text: string, table?: TableOptions, options?: RedactOptions): Promise<{ text: string; spans: Span[] }>;
  /** Changes the threshold without reloading the model. NaN and ±Infinity throw, as in `RedactorOptions.sensitivity`. */
  setSensitivity(value: number): void;
  readonly lastRun: RunStats | null;
  dispose(): Promise<void>;
}

const family = (l: string) => l.replace(/_L2$/, "");

/** The O-logit bias for a sensitivity: −value, coerced as by unary minus (null → 0). Throws when it is not finite (NaN would
 *  turn off model detection, ±Infinity mask everything or nothing); same message as the Kotlin package. */
function oBiasFor(sensitivity: number): number {
  const b = -sensitivity;
  if (!Number.isFinite(b)) throw new Error("[@newmean-ai/pii] sensitivity must be a finite number");
  return b;
}

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
    let lo = use[0].start; // loops, not Math.min(...spread): a group can hold more spans than the argument limit
    let hi = use[0].end;
    for (const s of use) {
      if (s.start < lo) lo = s.start;
      if (s.end > hi) hi = s.end;
    }
    out.push({
      start: lo,
      end: hi,
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
  // lists and sensitivity are validated before any model file is loaded (ListError: list, index and reason, never the text)
  const listsId = checkListsId(options.listsId);
  const userLists = new Lists(options.allow, options.block);
  const lists = userLists.allowCount || userLists.blockCount ? userLists : null;
  let oBias = oBiasFor(options.sensitivity ?? 0);
  const load = {
    cache: options.cache ?? true,
    verify: options.verify ?? true,
    fetch: options.fetch ?? globalThis.fetch.bind(globalThis),
    onProgress: options.onProgress,
  };
  const engineName: EngineName = options.engine ?? "builtin";
  if (engineName !== "builtin" && engineName !== "onnxruntime") throw new Error(`[@newmean-ai/pii] unknown engine "${engineName}" (builtin, onnxruntime)`);

  const [tokFile, modelFile] = await Promise.all([
    loadFile(base + "tokenizer.web.json", "tokenizer.web.json", files.tokenizer, load),
    loadFile(base + `onnx/model_${variant}.onnx`, `model_${variant}.onnx`, files[variant], load),
  ]);
  const tokenizer = new Tokenizer(JSON.parse(new TextDecoder().decode(tokFile.bytes)) as WebTokenizerFile);
  const engine: Engine =
    engineName === "builtin"
      ? await (await import("./engines/builtin.js")).createBuiltinEngine(modelFile.bytes, TAGS.length, options)
      : await (await import("./engines/onnxruntime.js")).createOrtEngine(modelFile.bytes, tokenizer.padId, TAGS.length, options);

  const enabled = new Set<string>(options.labels ?? LABELS);
  const useRegex = options.regex ?? false;
  const keepEmoji = options.keepEmoji ?? false;
  const wholeWords = options.wholeWords ?? true;
  const strictShortNames = options.strictShortNames ?? false;
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
    const personBias = strictShortNames && enc.ids.length <= SHORT_TOKENS ? SHORT_PERSON_BIAS : 0;
    const tags = bestTags(enc.ids.length, windows, logits, TAGS.length, oBias, personBias);
    const spans = decodeSpans(text, enc, tags, TAGS);
    lastRun = { tokens: enc.ids.length, windows: windows.length, ms: performance.now() - t0 };
    return spans;
  }

  async function modelSpansNoEmoji(text: string): Promise<Span[]> {
    const stripped = keepEmoji ? null : stripEmoji(text);
    return stripped ? restoreSpans(text, await modelSpans(stripped.text), stripped.keep) : modelSpans(text);
  }

  /** Sorted, non-overlapping spans before grapheme snapping. */
  async function detectRaw(text: string): Promise<Span[]> {
    const run = queue.then(() => modelSpansNoEmoji(text));
    queue = run.catch(() => undefined);
    const model = await run;
    return combine(model, useRegex ? regexSpans(text) : [], enabled);
  }

  async function detect(text: string): Promise<Span[]> {
    return publicSpans(finishDetect(text, await detectRaw(text), lists, wholeWords));
  }

  async function detectTable(text: string, table: TableOptions = {}): Promise<Span[]> {
    // options are checked before the model runs, in the C core's order: delimiter, then context
    checkDelimiter(table.delimiter ?? "auto");
    const context = table.context ?? "none";
    if (context !== "none" && context !== "header") throw new Error(`[@newmean-ai/pii] unknown table context ${JSON.stringify(context)} ("none", "header")`);
    const raw = await detectRaw(text);
    const extra: Span[] = [];
    const t = context === "header" && (table.header ?? true) ? parseTable(text, table.delimiter ?? "auto") : null;
    if (t) {
      for (const { cell, text: sub, offset } of headerContextCells(text, t)) {
        const run = queue.then(() => modelSpansNoEmoji(sub));
        queue = run.catch(() => undefined);
        for (const s of combine(await run, [], enabled)) {
          if (s.end <= offset) continue;
          const start = cell.start + Math.max(s.start - offset, 0);
          const end = Math.min(cell.start + s.end - offset, cell.end);
          if (start < end) extra.push({ ...s, start, end });
        }
      }
    }
    return publicSpans(detectTableSpans(text, raw, table, enabled, extra, wholeWords, lists)); // header context ran without lists
  }

  return {
    engine: engineName,
    size,
    variant,
    version: MODEL_VERSION,
    info: {
      packageVersion: VERSION,
      modelVersion: MODEL_VERSION,
      size,
      variant,
      engine: engineName,
      modelUrl: base,
      files: { model: { path: `onnx/model_${variant}.onnx`, ...files[variant] }, tokenizer: { path: "tokenizer.web.json", ...files.tokenizer } },
      verified: tokFile.verified && modelFile.verified,
      lists: { allow: userLists.allowCount, block: userLists.blockCount, id: listsId },
    },
    detect,
    async redact(text, opts) {
      const spans = await detect(text);
      return { text: applyRedaction(text, spans, opts), spans };
    },
    detectTable,
    async redactTable(text, table, opts) {
      const spans = await detectTable(text, table);
      return { text: applyRedaction(text, spans, opts), spans };
    },
    setSensitivity(value: number) {
      oBias = oBiasFor(value);
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
