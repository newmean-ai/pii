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
import { applyVault, applyVaultRanges, escapedReadings, joinedToNumber, narrowed, tagFound, tagRanges, valueSpans, vaultReach, vaultTags, type Reading, type ValueRange, type Vault } from "./vault.js";
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
  /** Numbered placeholders that can be undone (since 1.7.2): each span becomes the vault's tag for its text
   *  ("[PERSON_1]", the same value always the same tag) and `vault.restore` puts the values back. Overrides `mode` and
   *  `replace`. In `redact` the vault's known values are also masked where they occur again and the model does not
   *  find them (spans with source "vault"); `redactTable` only numbers its spans. Placeholder-like text that was already
   *  in the input and is not masked comes out escaped ("［PERSON_1]"). */
  vault?: Vault;
  /** With `vault`: placeholders already in the text that stay as they are, for text a model wrote with this vault's
   *  placeholders (an earlier answer). Other placeholder-like text is escaped. */
  keep?: ReadonlySet<string>;
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
  /** The sensitivity in use (`RedactorOptions.sensitivity`, or the last `setSensitivity`). Since 1.7.2. */
  readonly sensitivity: number;
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

/** Adds the vault's known values to `spans` (source "vault"). A known value that detected spans cover only in part
 *  ("Kim" of a known "Kim Minsu") is masked whole: the overlapping spans are merged into one span over both, which keeps
 *  the known value's placeholder when the merged span is exactly that value. `drop`: known values found that are left
 *  out (for the middlewares: a number joined to a longer one in another reading of the text). */
export function withVaultSpans(
  text: string,
  spans: Span[],
  vault: Vault,
  options?: { loose?: boolean; drop?: (hit: { start: number; end: number; value: string }) => boolean },
): Span[] {
  const found = vault.find(text, { loose: options?.loose }); // in order, apart
  const drop = options?.drop;
  const hits = drop ? found.filter((h) => !drop(h)) : found;
  if (hits.length === 0) return spans;
  // one pass over both (spans in order; they are sorted first if they are not)
  const sorted = spans.every((s, i) => i === 0 || spans[i - 1].start <= s.start) ? spans : [...spans].sort((a, b) => a.start - b.start);
  const out: Span[] = [];
  let i = 0;
  for (const h of hits) {
    while (i < sorted.length && sorted[i].end <= h.start) out.push(sorted[i++]);
    const over: Span[] = [];
    if (out.length > 0 && out[out.length - 1].end > h.start) over.push(out.pop()!); // one that reaches this far (merged before)
    while (i < sorted.length && sorted[i].start < h.end) over.push(sorted[i++]);
    if (over.length > 0 && over.every((s) => s.start <= h.start && s.end >= h.end)) {
      out.push(...over); // already masked whole
      continue;
    }
    let start = h.start;
    let end = h.end;
    for (const s of over) {
      if (s.start < start) start = s.start;
      if (s.end > end) end = s.end;
    }
    const label = start === h.start && end === h.end ? h.label : (over[0]?.label ?? h.label);
    out.push({ start, end, label, source: "vault" });
  }
  while (i < sorted.length) out.push(sorted[i++]);
  return out;
}

/** The spans a model found, each without what its boundary took from around the value (`valueSpans`), so that the vault
 *  learns the value and an answer gets it back without that. The same as model_values in the Python package. */
export function modelValues<S extends { start: number; end: number; label: string; source?: string }>(text: string, spans: readonly S[]): S[] {
  return valueSpans(text, spans);
}

/** `text` masked with a vault: the spans the model found plus the vault's known values, and then every value this text
 *  added to the vault masked wherever it occurs again in it ("I met Alice. Say Alice twice." when the model finds only
 *  the first "Alice"). The found spans lose what their edges hold that is not part of the value first (`valueSpans`:
 *  "Kim Minsu." at the end of a sentence is the value "Kim Minsu"). `spans` are the final ones; `tags` the placeholders
 *  put in. */
export function redactWithVault(
  text: string,
  given: Span[],
  vault: Vault,
  options?: { keep?: ReadonlySet<string>; loose?: boolean; drop?: (hit: { start: number; end: number; value: string }) => boolean },
): { text: string; spans: Span[]; tags: string[] } {
  const found = valueSpans(text, given);
  let spans = withVaultSpans(text, found, vault, options);
  let out = applyVault(text, spans, vault, options?.keep); // the vault learns this text's values here
  // also what the model found inside a larger merged span ("Kim Lee" of "Alex Kim Lee"), so it is masked where it stands alone
  for (const s of found) {
    const value = text.slice(s.start, s.end);
    if (value.trim() !== "" && !(options?.keep && vaultTags(value).some((t) => options.keep!.has(t)))) tagFound(vault, s.label, value);
  }
  // until nothing new is found: every round either masks more of the text or merges spans, so this ends
  for (;;) {
    const next = withVaultSpans(text, spans, vault, options);
    if (next.length === spans.length && next.every((s, i) => s.start === spans[i].start && s.end === spans[i].end)) break;
    spans = next;
    out = applyVault(text, spans, vault, options?.keep);
  }
  return { text: out.text, spans, tags: out.tags };
}

/** `ranges` (in order, apart) with `hits`: what was found in the reading `view` of the same text (the vault's known
 *  values, or the spans of that reading); without `map`, `view` is the text as it is written. As in `withVaultSpans`: a
 *  hit that ranges cover only in part is masked whole, as one range over both; it keeps the hit's own value when that
 *  range is exactly the hit. */
function withKnownRanges(
  ranges: ValueRange[],
  view: { text: string; map?: Int32Array; base?: string },
  hits: readonly { start: number; end: number; label: string; value: string }[],
  age: (value: string) => number | undefined,
): ValueRange[] {
  if (hits.length === 0) return ranges;
  const { map } = view;
  const at = map ? (i: number) => map[i] : (i: number) => i;
  // the place in the reading of a place in the text: the last one at or before it, or the first at or after it
  const index = (place: number, up: boolean) => {
    if (!map) return place;
    let lo = 0;
    let hi = map.length - 1;
    while (lo < hi) {
      const mid = up ? (lo + hi) >> 1 : (lo + hi + 1) >> 1;
      if (up ? map[mid] >= place : map[mid] > place) hi = up ? mid : mid - 1;
      else lo = up ? mid + 1 : mid;
    }
    return lo;
  };
  const out: ValueRange[] = [];
  let i = 0;
  for (const h of hits) {
    const a = at(h.start);
    const b = at(h.end);
    while (i < ranges.length && ranges[i].b <= a) out.push(ranges[i++]);
    const over: ValueRange[] = [];
    if (out.length > 0 && out[out.length - 1].b > a) over.push(out.pop()!); // one that was merged just before
    while (i < ranges.length && ranges[i].a < b) over.push(ranges[i++]);
    if (over.length === 1 && over[0].a <= a && over[0].b >= b) {
      // already masked whole. When it is exactly this part of the text, the hit's own value is used (one placeholder
      // for a value however it is written), unless the vault holds what is there already and has held it longer
      // (a known "Kim+Minsu" in a path stays that, also when "Kim Minsu" is known too).
      const mine = age(over[0].value);
      const theirs = age(h.value);
      const keep = mine !== undefined && theirs !== undefined && mine <= theirs;
      out.push(over[0].a === a && over[0].b === b && !keep ? { a, b, label: h.label, value: h.value } : over[0]);
      continue;
    }
    let lo = a;
    let hi = b;
    for (const r of over) {
      if (r.a < lo) lo = r.a;
      if (r.b > hi) hi = r.b;
    }
    if (lo === a && hi === b) out.push({ a, b, label: h.label, value: h.value });
    else {
      // both as one value, as this reading has it (over whole escapes); a reading of a part of the text is the text as
      // written outside that part
      const from = index(lo, false);
      const to = index(hi, true);
      const { base } = view;
      const [start, end] = [at(from), at(to)];
      const before = base !== undefined && lo < start ? base.slice(lo, start) : "";
      const after = base !== undefined && hi > end ? base.slice(end, hi) : "";
      out.push({ a: Math.min(lo, start), b: Math.max(hi, end), label: over[0].label, value: before + view.text.slice(from, to) + after });
    }
  }
  while (i < ranges.length) out.push(ranges[i++]);
  return out;
}

/** `text` masked with a vault in every way it can be read. `found` are the detected spans of the text as it is written.
 *  `through`, when the text holds Unicode escapes: its reading through them (`jsonEscapesView`) and the spans detected
 *  in that reading (the model is then given that reading, and `found` is empty). Both readings count for the values the
 *  vault holds, because "\u1234" is a letter in JSON and a folder in "C:\Users\u1234". Each is masked as
 *  `redactWithVault` does, and then the vault's known values are searched in every reading of the text, the others too
 *  (`escapedReadings`: a value with a line break, written as "\n" in a log field or in JSON inside other text; a value
 *  in a URL), and also where they are written another way (`find` with `variants`).
 *  What one reading finds takes in the ranges of another that cover part of it, so a detector that finds only
 *  "12 Main St" in "12 Main St\nSeoul 04524" cannot cut a known address in two. The placeholders go into the text as it
 *  is written, each over whole escapes. `keep`: placeholders that stay as they are; other placeholder-like text is
 *  escaped. */
export function redactReadings(
  text: string,
  given: Span[],
  vault: Vault,
  options?: { keep?: ReadonlySet<string>; loose?: boolean; through?: { view: Reading; found: Span[] } },
): { text: string; tags: string[] } {
  const keep = options?.keep;
  const loose = options?.loose;
  // (what the edges of a found span hold that is not part of the value is left out before the vault learns it)
  const found = valueSpans(text, given);
  const given2 = options?.through;
  const through = given2 && { view: given2.view, found: valueSpans(given2.view.text, given2.found) };
  // placeholder-like text, as the reading through Unicode escapes has it (it has every such part of the text as written)
  const at = through ? (i: number) => through.view.map[i] : (i: number) => i;
  const brackets = tagRanges(through ? through.view.text : text)
    .filter((t) => !keep?.has(t.tag))
    .map((t) => [at(t.start), at(t.start + 1)] as const);
  // (read in parts around their escapes, as far as a known value can reach: escapes far apart make no more readings)
  const others = escapedReadings(text, vaultReach(vault)).filter((r) => r.text !== through?.view.text);
  // A known value found at [a, b) of the text is left out where it is part of a longer number in the text as written or
  // in any other reading that has [a, b) whole: "4524" in "1e+4524", and also in "1e%2B4524" and "1e\u002B4524", whose
  // escaped sign joins like "+" (as it does in the form-field reading of "1e+4524", where that "+" is a space, the
  // text as written has it), and in "4524%2E7". An exponent joins numbers only ("12 Main St" stays found after "4E+").
  const views: Reading[] = [...(through ? [through.view] : []), ...others];
  // (the readings of the whole text, and those of parts of it, which come in the order of the text: their starts and
  // their ends only grow, so the parts that hold a place are found by a search, not by looking at every part)
  const wholes = views.filter((r) => r.core === undefined);
  const parts = views.filter((r) => r.core !== undefined);
  const holds = (r: Reading, a: number, b: number, value: string) => {
    const i = placeIn(r.map, a);
    const j = i < 0 ? -1 : placeIn(r.map, b);
    return j >= 0 && joinedToNumber(r.text, i, j, value);
  };
  // (the text as the vault reads it, full-width ASCII as ASCII: "１ｅ＋４５２４" is "1e+4524", where 4524 is an exponent)
  const plain = narrowed(text);
  const joined = (a: number, b: number, value: string) => {
    if (joinedToNumber(plain, a, b, value) || wholes.some((r) => holds(r, a, b, value))) return true;
    let lo = 0;
    let hi = parts.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (parts[mid].map[0] <= a) lo = mid + 1;
      else hi = mid;
    }
    for (let k = lo - 1; k >= 0 && parts[k].map[parts[k].map.length - 1] >= b; k--) if (holds(parts[k], a, b, value)) return true;
    return false;
  };
  /** One reading masked with the vault: its final spans, each stopping at a kept placeholder and going on after it. */
  const masked = (read: string, spans: Span[], map?: Int32Array) => {
    const kept = keep && keep.size > 0 ? tagRanges(read).filter((t) => keep.has(t.tag)) : [];
    const out: { start: number; end: number; label: string; value: string }[] = [];
    const piece = (start: number, end: number, label: string) => {
      const value = read.slice(start, end);
      if (start < end && value.trim() !== "") out.push({ start, end, label, value });
    };
    // what the model found that is, as a whole, a value the vault holds written another way ("Kim\nMinsu" for a known
    // "Kim Minsu", "8.21023456789e11" for 821023456789) is that value: it gets no placeholder of its own. The spans are
    // taken in order and each new value is learned at once, so the same holds for two spellings found in one text.
    const same: { start: number; end: number; label: string; value: string }[] = [];
    const other = spans.filter((s) => {
      const written = read.slice(s.start, s.end);
      const known = vault.whole(written);
      if (known && known.value !== written) {
        same.push({ start: s.start, end: s.end, label: known.label, value: known.value });
        return false;
      }
      if (!known && written.trim() !== "" && !(keep && vaultTags(written).some((t) => keep.has(t)))) tagFound(vault, s.label, written);
      return true;
    });
    let k = 0;
    const place = (i: number) => (map ? map[i] : i); // (where a place of the reading is in the text)
    const drop = loose ? undefined : (h: { start: number; end: number; value: string }) => joined(place(h.start), place(h.end), h.value);
    for (const s of redactWithVault(read, other, vault, { keep, loose, drop }).spans) {
      let start = s.start;
      while (k < kept.length && kept[k].end <= start) k++;
      for (let j = k; j < kept.length && kept[j].start < s.end; j++) {
        piece(start, kept[j].start, s.label);
        start = Math.max(start, kept[j].end);
      }
      piece(start, s.end, s.label);
    }
    return { out, same };
  };
  let ranges: ValueRange[] = [];
  // until the vault learns nothing new (a merged range is a new value, which can occur again elsewhere in the text).
  // Every round that goes on has added a value to the vault, and with it the ranges only grow or merge or take an
  // older spelling, so this ends; the bound is only a guard against a loop that would not
  const rounds = 4 * text.length + 4;
  for (let round = 0; round < rounds; round++) {
    const written = masked(text, found);
    ranges = withKnownRanges(written.out.map((p) => ({ a: p.start, b: p.end, label: p.label, value: p.value })), { text }, written.same, vault.age);
    if (through) {
      const read = masked(through.view.text, through.found, through.view.map);
      ranges = withKnownRanges(withKnownRanges(ranges, through.view, read.out, vault.age), through.view, read.same, vault.age);
    }
    // the vault's known values in every reading, also where they are written another way (white space, a number's spelling)
    // (what is found in one reading is looked at in the others as well: "4524" behind the "+" of "1e+4524" stands free
    // in the form-field reading, where the "+" is a space, and is an exponent all the same)
    for (const r of [{ text } as Reading | { text: string; map?: undefined; core?: undefined }, ...views]) {
      const { map, core } = r;
      const place = (i: number) => (map ? map[i] : i);
      let hits = vault.find(r.text, { loose, variants: true });
      // (in a reading of a part of the text, what neither touches nor holds any of its escapes is found in the text as
      // written, where what stands around it is known)
      if (map && core) hits = hits.filter((h) => map[h.start] <= core[1] && map[h.end] >= core[0]);
      ranges = withKnownRanges(ranges, r, loose ? hits : hits.filter((h) => !joined(place(h.start), place(h.end), h.value)), vault.age);
    }
    const size = vault.size;
    for (const r of ranges) tagFound(vault, r.label, r.value);
    if (vault.size === size) break;
  }
  return applyVaultRanges(text, ranges, vault, brackets);
}

/** The first place of a reading whose character starts at `place` of the text (`map` as in `Reading`), or -1 when no
 *  character of the reading starts there (it is inside an escape). */
function placeIn(map: Int32Array, place: number): number {
  let lo = 0;
  let hi = map.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (map[mid] < place) lo = mid + 1;
    else hi = mid;
  }
  return lo < map.length && map[lo] === place ? lo : -1;
}

/** Throws if `vault` was cleared since its generation was `started`: placeholders must not be written into a vault that
 *  was cleared while the text was read (they would belong to whatever uses the vault now). */
export function vaultUnchanged(vault: Vault | undefined, started: number | undefined): void {
  if (vault && vault.generation !== started) throw new Error("[@newmean-ai/pii] the vault was cleared while this text was masked");
}

export function applyRedaction(text: string, spans: Span[], options: RedactOptions = {}): string {
  // with a vault, placeholder-like text left in the output is escaped ("［PERSON_1]"), so that only the vault's own
  // placeholders restore; detection ran on the original text, so block lists and the model see it as written. The vault
  // learns each span without what its edges hold that is not part of the value (`valueSpans`), as in `redactWithVault`
  if (options.vault) return applyVault(text, valueSpans(text, spans), options.vault, options.keep).text;
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

  // `bias`: the sensitivity as it was when the caller asked (a setSensitivity while the model runs is for the next call)
  async function modelSpans(text: string, bias: number): Promise<Span[]> {
    const t0 = performance.now();
    const enc = tokenizer.encode(text);
    if (!enc.ids.length) {
      lastRun = { tokens: 0, windows: 0, ms: performance.now() - t0 };
      return [];
    }
    const windows = makeWindows(enc.ids, tokenizer.bosId, tokenizer.eosId);
    const logits = await engine.run(windows);
    const personBias = strictShortNames && enc.ids.length <= SHORT_TOKENS ? SHORT_PERSON_BIAS : 0;
    const tags = bestTags(enc.ids.length, windows, logits, TAGS.length, bias, personBias);
    const spans = decodeSpans(text, enc, tags, TAGS);
    lastRun = { tokens: enc.ids.length, windows: windows.length, ms: performance.now() - t0 };
    return spans;
  }

  async function modelSpansNoEmoji(text: string, bias: number): Promise<Span[]> {
    const stripped = keepEmoji ? null : stripEmoji(text);
    return stripped ? restoreSpans(text, await modelSpans(stripped.text, bias), stripped.keep) : modelSpans(text, bias);
  }

  /** Sorted, non-overlapping spans before grapheme snapping. */
  async function detectRaw(text: string, bias = oBias): Promise<Span[]> {
    const run = queue.then(() => modelSpansNoEmoji(text, bias));
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
    const bias = oBias;
    const raw = await detectRaw(text, bias);
    const extra: Span[] = [];
    const t = context === "header" && (table.header ?? true) ? parseTable(text, table.delimiter ?? "auto") : null;
    if (t) {
      for (const { cell, text: sub, offset } of headerContextCells(text, t)) {
        const run = queue.then(() => modelSpansNoEmoji(sub, bias));
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
      const started = opts?.vault?.generation;
      const found = await detect(text);
      if (!opts?.vault) return { text: applyRedaction(text, found, opts), spans: found };
      vaultUnchanged(opts.vault, started);
      const out = redactWithVault(text, found, opts.vault, { keep: opts.keep });
      return { text: out.text, spans: out.spans };
    },
    detectTable,
    async redactTable(text, table, opts) {
      const started = opts?.vault?.generation;
      const found = await detectTable(text, table);
      vaultUnchanged(opts?.vault, started);
      const spans = opts?.vault ? valueSpans(text, found) : found; // (what the vault learns: as in applyRedaction)
      return { text: applyRedaction(text, spans, opts), spans };
    },
    setSensitivity(value: number) {
      oBias = oBiasFor(value);
    },
    get sensitivity() {
      return 0 - oBias;
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
