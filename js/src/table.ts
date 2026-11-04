// Table mode (CSV / TSV / semicolon / markdown pipe tables) and grapheme snapping. Same algorithm as the C core
// (native/core/src/api.c, pii_detect_table); parity is checked span for span. Positions are UTF-16 code units (Span
// offsets), but every length, count and median below is in code points.
import type { Span } from "./types.js";
import { KO_PARTICLES, MAX_PARTICLE_RUN, WORD_RANGES } from "./word_tables.js";

export type TableDelimiter = "auto" | "," | "\t" | ";" | "markdown";

/** Block-region winner key (option `block`): the smallest (start, length in code points, entry index) of its matches. */
export interface Key {
  start: number;
  len: number;
  index: number;
}
/** A span inside the pipeline; block regions carry a key (dropped at the API boundary). */
export type KSpan = Span & { key?: Key };

/** a's key sorts before b's: start ascending, then length descending, then entry index ascending. */
export function keyLess(a: Key, b: Key): boolean {
  if (a.start !== b.start) return a.start < b.start;
  if (a.len !== b.len) return a.len > b.len;
  return a.index < b.index;
}

/** The precedence rule of every merge: a block span beats every other span and the smaller key wins among block spans;
 *  undefined when neither is a block span (the merge step's own rule decides). */
export function blockWins(x: KSpan, w: KSpan): boolean | undefined {
  const xb = x.source === "block";
  const wb = w.source === "block";
  if (xb !== wb) return xb;
  return xb ? keyLess(x.key!, w.key!) : undefined;
}

/** What the pipeline needs of validated user lists (js/src/lists.ts). */
export interface UserLists {
  readonly allowCount: number;
  readonly blockCount: number;
  /** text[start, end) in compared form is an allow entry whose families include label's. */
  allowed(text: string, start: number, end: number, label: string): boolean;
  /** Block regions of text[start, end), in start order. */
  blocks(text: string, start: number, end: number): KSpan[];
}

/** The allow filter: spans without a block match whose whole text is an allow entry of their label's family are dropped. */
export function allowFilter(text: string, spans: KSpan[], lists: UserLists | null): KSpan[] {
  return lists?.allowCount ? spans.filter((s) => s.source === "block" || !lists.allowed(text, s.start, s.end, s.label)) : spans;
}

/** Sorted, non-overlapping spans and block regions → one list: overlapping spans become one, with the label, source and key
 *  of its block region of the smallest key. */
export function mergeBlocks(spans: KSpan[], blocks: KSpan[]): KSpan[] {
  if (!blocks.length) return spans;
  const out: KSpan[] = [];
  let i = 0;
  let j = 0;
  while (i < spans.length || j < blocks.length) {
    const x = j >= blocks.length || (i < spans.length && spans[i].start < blocks[j].start) ? spans[i++] : blocks[j++];
    const w = out[out.length - 1];
    if (w && x.start < w.end) {
      const end = Math.max(w.end, x.end);
      out[out.length - 1] = blockWins(x, w) ? { ...x, start: w.start, end } : { ...w, end };
    } else out.push({ ...x });
  }
  return out;
}

/** The steps of `detect` after combine: block regions of the whole text merged in, word widening and grapheme snapping, then
 *  the allow filter. */
export function finishDetect(text: string, spans: KSpan[], lists: UserLists | null, words: boolean): KSpan[] {
  const merged = lists?.blockCount ? mergeBlocks(spans, lists.blocks(text, 0, text.length)) : spans;
  return allowFilter(text, snapGraphemes(text, merged, undefined, words), lists);
}

export interface TableOptions {
  /** Field separator. "auto" (default) picks markdown when the text starts with pipe-table lines, else the one of
   *  ",", "\t", ";" that gives the most records with the first record's field count (at least 2 such records). */
  delimiter?: TableDelimiter;
  /** The first row is a header row: masked where the model finds PII, but never voted on or propagated into. Default true. */
  header?: boolean;
  /** Column vote: a short-value column whose cells are at least 80% covered by PERSON, EMAIL, PHONE or USERNAME spans in
   *  at least 80% of its non-empty cells (and at least 4 of them, 5 or more non-empty cells) gets every non-empty cell
   *  masked with that label (source "table"). false: whole-text detection clipped to cell values only. Default true. */
  vote?: boolean;
  /** "header": with a header row, each non-empty data cell of a column whose non-empty values are at least 80% distinct is
   *  also read on its own as "<header>: <value>", and those spans are added. Fewer misses on name, contact and ID columns,
   *  more PII-free cells masked, one more model call per such cell. Default "none". */
  context?: "none" | "header";
}

/** One cell: [start, end) is the value range (CSV: inside the quotes, "" kept as two units; markdown: trimmed). */
export interface Cell {
  row: number;
  col: number;
  start: number;
  end: number;
}

export interface Table {
  delimiter: "," | "\t" | ";" | "|";
  rows: number;
  /** In text order. */
  cells: Cell[];
}

/** The whitespace set of table mode (same in C): U+0009–000D, 0020, 00A0, 1680, 2000–200A, 2028, 2029, 202F, 205F, 3000, FEFF.
 *  All are BMP non-surrogates, so a UTF-16 unit test is exact. */
export function isSpace(c: number): boolean {
  return (
    (c >= 0x09 && c <= 0x0d) || c === 0x20 || c === 0xa0 || c === 0x1680 || (c >= 0x2000 && c <= 0x200a) ||
    c === 0x2028 || c === 0x2029 || c === 0x202f || c === 0x205f || c === 0x3000 || c === 0xfeff
  );
}

function trim(text: string, start: number, end: number): [number, number] {
  while (start < end && isSpace(text.charCodeAt(start))) start++;
  while (end > start && isSpace(text.charCodeAt(end - 1))) end--;
  return [start, end];
}

const isHigh = (c: number) => c >= 0xd800 && c <= 0xdbff;
const isLow = (c: number) => c >= 0xdc00 && c <= 0xdfff;

/** Code points in text[start, end) (a surrogate pair counts once). */
export function cpLength(text: string, start: number, end: number): number {
  let n = 0;
  for (let i = start; i < end; i++) if (!(isLow(text.charCodeAt(i)) && i > start && isHigh(text.charCodeAt(i - 1)))) n++;
  return n;
}

/** Non-whitespace code points in text[start, end). */
function nonSpace(text: string, start: number, end: number): number {
  let n = 0;
  for (let i = start; i < end; i++) {
    const c = text.charCodeAt(i);
    if (isSpace(c) || (isLow(c) && i > start && isHigh(text.charCodeAt(i - 1)))) continue;
    n++;
  }
  return n;
}

function words(text: string, start: number, end: number): number {
  let n = 0;
  let inWord = false;
  for (let i = start; i < end; i++) {
    const sp = isSpace(text.charCodeAt(i));
    if (!sp && !inWord) n++;
    inWord = !sp;
  }
  return n;
}

/** RFC 4180 parse with delimiter d. Records end at "\n" or "\r\n" outside quotes (a lone "\r" is ordinary). A quoted field's
 *  value runs to the closing quote; anything after it up to the next d or line break is skipped. Blank records (one field
 *  with an empty trimmed value) are not rows. `fields` = field count per row. */
export function parseCsv(text: string, d: "," | "\t" | ";", maxRows = Infinity): Table & { fields: number[] } {
  const n = text.length;
  const D = d.charCodeAt(0);
  const cells: Cell[] = [];
  const fields: number[] = [];
  let i = 0;
  while (i < n && fields.length < maxRows) {
    const rec: [number, number][] = [];
    for (;;) {
      let vs: number;
      let ve: number;
      let p: number;
      if (i < n && text.charCodeAt(i) === 0x22) {
        vs = i + 1;
        let j = vs;
        for (;;) {
          if (j >= n) {
            ve = p = n; // unclosed quote: the value runs to the end of the text
            break;
          }
          if (text.charCodeAt(j) === 0x22) {
            if (j + 1 < n && text.charCodeAt(j + 1) === 0x22) {
              j += 2;
              continue;
            }
            ve = j;
            p = j + 1;
            break;
          }
          j++;
        }
        while (p < n && text.charCodeAt(p) !== D && text.charCodeAt(p) !== 0x0a) p++;
      } else {
        vs = p = i;
        for (; p < n; p++) {
          const c = text.charCodeAt(p);
          if (c === D || c === 0x0a || (c === 0x0d && p + 1 < n && text.charCodeAt(p + 1) === 0x0a)) break;
        }
        ve = p;
      }
      rec.push([vs, ve]);
      if (p >= n) {
        i = n;
        break;
      }
      const c = text.charCodeAt(p);
      if (c === D) {
        i = p + 1;
        continue;
      }
      i = c === 0x0d ? p + 2 : p + 1;
      break;
    }
    if (rec.length === 1) {
      const [s, e] = trim(text, rec[0][0], rec[0][1]);
      if (e === s) continue;
    }
    const row = fields.length;
    rec.forEach(([start, end], col) => cells.push({ row, col, start, end }));
    fields.push(rec.length);
  }
  return { delimiter: d, rows: fields.length, cells, fields };
}

const SEPARATOR = /^:?-+:?$/;

/** Markdown pipe table: lines (split at "\n", one trailing "\r" dropped) that start with "|" after trimming. Cells are the
 *  segments between consecutive pipes, plus the one after the last pipe when the line does not end with "|"; a cell's range
 *  is its trimmed segment (an all-space segment: an empty cell at the segment start). Separator lines ("|---|:--:|") and
 *  lines without cells are not rows; other lines are ignored. */
export function parseMarkdown(text: string): Table {
  const n = text.length;
  const cells: Cell[] = [];
  let rows = 0;
  for (let ls = 0; ls <= n; ) {
    let le = text.indexOf("\n", ls);
    if (le < 0) le = n;
    let e = le;
    if (e > ls && text.charCodeAt(e - 1) === 0x0d) e--;
    const [ts, te] = trim(text, ls, e);
    if (te > ts && text.charCodeAt(ts) === 0x7c) {
      const pipes: number[] = [];
      for (let i = ts; i < te; i++) if (text.charCodeAt(i) === 0x7c) pipes.push(i);
      const segs: [number, number][] = [];
      for (let k = 0; k + 1 < pipes.length; k++) segs.push([pipes[k] + 1, pipes[k + 1]]);
      if (text.charCodeAt(te - 1) !== 0x7c) segs.push([pipes[pipes.length - 1] + 1, te]);
      const ranges = segs.map(([s, en]): [number, number] => {
        const [a, b] = trim(text, s, en);
        return b > a ? [a, b] : [s, s];
      });
      if (ranges.length && !ranges.every(([a, b]) => SEPARATOR.test(text.slice(a, b)))) {
        ranges.forEach(([start, end], col) => cells.push({ row: rows, col, start, end }));
        rows++;
      }
    }
    ls = le + 1;
  }
  return { delimiter: "|", rows, cells };
}

/** Auto-detected delimiter, or null when the text does not look like a table. */
export function detectDelimiter(text: string): Table["delimiter"] | null {
  const lines: [number, number][] = [];
  for (let ls = 0; ls <= text.length && lines.length < 5; ) {
    let le = text.indexOf("\n", ls);
    if (le < 0) le = text.length;
    let e = le;
    if (e > ls && text.charCodeAt(e - 1) === 0x0d) e--;
    const [ts, te] = trim(text, ls, e);
    if (te > ts) lines.push([ts, te]);
    ls = le + 1;
  }
  const pipe = ([s]: [number, number]) => text.charCodeAt(s) === 0x7c;
  if (lines.length && pipe(lines[0]) && lines.filter(pipe).length >= 2) return "|";
  let best: Table["delimiter"] | null = null;
  let bestScore = 0;
  for (const d of [",", "\t", ";"] as const) {
    const { fields } = parseCsv(text, d, 20);
    const score = fields.length && fields[0] >= 2 ? fields.filter((f) => f === fields[0]).length : 0;
    if (score > bestScore) {
      best = d;
      bestScore = score;
    }
  }
  return bestScore >= 2 ? best : null;
}

/** Throws on a delimiter option that is not one of TableDelimiter. */
export function checkDelimiter(delimiter: TableDelimiter): void {
  if (delimiter !== "auto" && delimiter !== "markdown" && delimiter !== "," && delimiter !== "\t" && delimiter !== ";")
    throw new Error(`[@newmean-ai/pii] unknown table delimiter ${JSON.stringify(delimiter)} ("auto", ",", "\\t", ";", "markdown")`);
}

/** Parsed table, or null when the text is not a table (no delimiter found, or fewer than 2 rows). */
export function parseTable(text: string, delimiter: TableDelimiter = "auto"): Table | null {
  checkDelimiter(delimiter);
  const d: Table["delimiter"] | null = delimiter === "auto" ? detectDelimiter(text) : delimiter === "markdown" ? "|" : delimiter;
  if (!d) return null;
  const t = d === "|" ? parseMarkdown(text) : parseCsv(text, d);
  return t.rows >= 2 ? t : null;
}

const FAMILIES: readonly [string, readonly string[]][] = [
  ["PERSON", ["PERSON", "PERSON_L2"]],
  ["EMAIL", ["EMAIL"]],
  ["PHONE", ["PHONE"]],
  ["USERNAME", ["USERNAME"]],
];
const familyIndex = (label: string) => FAMILIES.findIndex(([, ls]) => ls.includes(label));
const PLACEHOLDERS = new Set(["n/a", "na", "-", "--", "—", "–", "none", "null", "없음"]);
const DIGITS = [
  0x0030, 0x0660, 0x06f0, 0x0966, 0x09e6, 0x0a66, 0x0ae6, 0x0b66, 0x0be6, 0x0c66, 0x0ce6, 0x0d66, 0x0e50, 0x0ed0, 0x0f20,
  0x1040, 0x17e0, 0x1810, 0xff10,
];
const isDigit = (c: number) => DIGITS.some((z) => c >= z && c <= z + 9);

/** Whole-text spans (sorted, non-overlapping, before grapheme snapping) → table-mode spans: clipped to cell values, plus
 *  column-vote propagation (source "table"). Returns `spans` unchanged when the text is not a table. */
export function tableSpans(text: string, spans: Span[], options: TableOptions = {}, enabled?: Set<string>): Span[] {
  return tableSteps(text, spans, options, enabled).spans;
}

/** Table mode after whole-text detection, as `Redactor.detectTable` returns it: `tableSpans`, then grapheme snapping kept
 *  inside each cell value (a value starting with a combining mark or ending with a Prepend character shares a cluster with
 *  the delimiter or quote next to it; snapping never takes that in). Not a table: `snapGraphemes(text, spans)`. */
/** Header context: for each non-empty data cell of a column whose non-empty values are at least 80% distinct (values compared
 *  trimmed, ASCII letters folded), the text "<header>: <value>" (just the value when the header cell is empty), and the
 *  offset of the value in it. Same as header_context_spans in native/core/src/api.c. */
export function headerContextCells(text: string, t: Table): { cell: Cell; text: string; offset: number }[] {
  const header = new Map<number, Cell>();
  const columns = new Map<number, string[]>();
  for (const c of t.cells) {
    if (c.row === 0) {
      header.set(c.col, c);
      continue;
    }
    const [s, e] = trim(text, c.start, c.end);
    if (e === s) continue;
    let col = columns.get(c.col);
    if (!col) columns.set(c.col, (col = []));
    col.push(text.slice(s, e).replace(/[A-Z]/g, (ch) => ch.toLowerCase()));
  }
  const ok = new Set<number>();
  for (const [col, values] of columns) if (5 * new Set(values).size >= 4 * values.length) ok.add(col);
  const out: { cell: Cell; text: string; offset: number }[] = [];
  for (const c of t.cells) {
    if (c.row === 0 || !ok.has(c.col)) continue;
    const [s, e] = trim(text, c.start, c.end);
    if (e === s) continue;
    const h = header.get(c.col);
    const [hs, he] = h ? trim(text, h.start, h.end) : [0, 0];
    const prefix = he > hs ? `${text.slice(hs, he)}: ` : "";
    out.push({ cell: c, text: prefix + text.slice(c.start, c.end), offset: prefix.length });
  }
  return out;
}

export function detectTableSpans(
  text: string,
  spans: Span[],
  options: TableOptions = {},
  enabled?: Set<string>,
  extra: Span[] = [],
  words = false,
  lists: UserLists | null = null,
): KSpan[] {
  const { spans: out, cells } = tableSteps(text, spans, options, enabled, extra, words, lists);
  if (!cells) return finishDetect(text, spans, lists, words); // not a table: the steps of detect
  return allowFilter(text, snapGraphemes(text, out, cells, words), lists);
}

function tableSteps(
  text: string,
  spans: Span[],
  options: TableOptions,
  enabled?: Set<string>,
  extra: Span[] = [],
  wholeWords = false,
  lists: UserLists | null = null,
): { spans: KSpan[]; cells: Cell[] | null } {
  const t = parseTable(text, options.delimiter ?? "auto");
  if (!t) return { spans, cells: null };
  const { cells } = t;
  const firstData = options.header ?? true ? 1 : 0;

  // clip: keep each span ∩ cell value that holds a non-whitespace code point; pre-vote allow filter: a clipped span whose
  // candidate (the span alone, widened to its words and clusters and clamped to its cell, as the final snapping would) is
  // allowed leaves before the vote
  const clipped: Span[] = [];
  const byCell: Span[][] = cells.map(() => []);
  const segs = lists?.allowCount && segmenter ? segmenter.segment(text) : null;
  let k = 0;
  cells.forEach((cell, ci) => {
    if (cell.end <= cell.start) return;
    while (k < spans.length && spans[k].end <= cell.start) k++;
    for (let j = k; j < spans.length && spans[j].start < cell.end; j++) {
      const s = Math.max(spans[j].start, cell.start);
      const e = Math.min(spans[j].end, cell.end);
      if (e <= s || !nonSpace(text, s, e)) continue;
      const c: Span = { start: s, end: e, label: spans[j].label, source: spans[j].source };
      if (lists?.allowCount) {
        const ws = wholeWords ? wordStart(text, s) : s;
        const we = wholeWords ? wordEnd(text, e) : e;
        const a = segs?.containing(ws);
        const b = segs?.containing(we - 1);
        const cs = Math.max(a ? a.index : ws, cell.start);
        const ce = Math.min(b ? b.index + b.segment.length : we, cell.end);
        if (lists.allowed(text, cs, ce, c.label)) continue;
      }
      clipped.push(c);
      byCell[ci].push(c);
    }
  });

  // vote per column over non-empty data cells; support counts clipped spans only
  const propagated: Span[] = [];
  if (options.vote ?? true) {
    const columns = new Map<number, { ci: number; s: number; e: number }[]>();
    cells.forEach((cell, ci) => {
      if (cell.row < firstData) return;
      const [s, e] = trim(text, cell.start, cell.end);
      if (e === s) return;
      let col = columns.get(cell.col);
      if (!col) columns.set(cell.col, (col = []));
      col.push({ ci, s, e });
    });
    for (const col of [...columns.keys()].sort((a, b) => a - b)) {
      const list = columns.get(col)!;
      const n = list.length;
      if (n < 5) continue;
      const lens = list.map(({ s, e }) => cpLength(text, s, e)).sort((a, b) => a - b);
      const wcs = list.map(({ s, e }) => words(text, s, e)).sort((a, b) => a - b);
      const lo = (n - 1) >> 1;
      const hi = n >> 1;
      if (lens[lo] + lens[hi] > 80 || wcs[lo] + wcs[hi] > 8) continue; // medians ≤ 40 code points, ≤ 4 words
      const support = FAMILIES.map(() => 0);
      for (const { ci, s, e } of list) {
        const nz = nonSpace(text, s, e);
        const cov = FAMILIES.map(() => 0);
        for (const c of byCell[ci]) {
          const f = familyIndex(c.label);
          const a = Math.max(c.start, s);
          const b = Math.min(c.end, e);
          if (f >= 0 && b > a) cov[f] += nonSpace(text, a, b);
        }
        let best = 0;
        for (let f = 1; f < cov.length; f++) if (cov[f] > cov[best]) best = f;
        if (cov[best] > 0 && 5 * cov[best] >= 4 * nz) support[best]++;
      }
      let top = 0;
      for (let f = 1; f < support.length; f++) if (support[f] > support[top]) top = f;
      if (support[top] < 4 || 5 * support[top] < 4 * n) continue;
      const label = FAMILIES[top][0];
      if (enabled && !enabled.has(label)) continue;
      for (const { s, e } of list) {
        const value = text.slice(s, e);
        if (PLACEHOLDERS.has(value.replace(/[A-Z]/g, (ch) => ch.toLowerCase()))) continue;
        if (label === "EMAIL" && !value.includes("@") && !value.includes("＠")) continue;
        if (label === "PHONE") {
          let digits = 0;
          for (let i = 0; i < value.length; i++) if (isDigit(value.charCodeAt(i))) digits++;
          if (digits < 6) continue;
        }
        propagated.push({ start: s, end: e, label, source: "table" });
      }
    }
  }

  // block regions per cell, inside each cell's value range (they never vote)
  const blocked: KSpan[] = [];
  if (lists?.blockCount) for (const cell of cells) for (const b of lists.blocks(text, cell.start, cell.end)) blocked.push(b);

  // merge overlapping spans: the block span of the smallest key wins, else a table span's label, else the longest span's
  // (ties → earliest start). Group ends are tracked in the loop (a spread over a group of 100k+ spans would exceed the
  // engine's argument limit).
  const all: KSpan[] = [...clipped, ...propagated, ...blocked, ...extra].sort((a, b) => a.start - b.start);
  const groups: { spans: KSpan[]; end: number }[] = [];
  for (const s of all) {
    const g = groups[groups.length - 1];
    if (g && s.start < g.end) {
      g.spans.push(s);
      if (s.end > g.end) g.end = s.end;
    } else groups.push({ spans: [s], end: s.end });
  }
  const merged = groups.map(({ spans: g, end }): KSpan => {
    let pick = g[0];
    let len = cpLength(text, pick.start, pick.end);
    for (let i = 1; i < g.length; i++) {
      const x = g[i];
      const b = blockWins(x, pick);
      if (b !== undefined) {
        if (b) pick = x;
        continue;
      }
      if (pick.source === "table") continue;
      const l = cpLength(text, x.start, x.end);
      if (x.source === "table" || l > len) [pick, len] = [x, l];
    }
    return { start: g[0].start, end, label: pick.label, source: pick.source, ...(pick.key ? { key: pick.key } : {}) };
  });
  return { spans: merged, cells };
}

// ---------------------------------------------------------------- whole words (option wholeWords)
// Same rule as the C core (native/core/src/api.c, word_start / word_end). The word characters, KO_PARTICLES and
// MAX_PARTICLE_RUN come from js/src/word_tables.ts, which native/core/scripts/gen_words.mjs writes together with
// native/core/src/word_tables.h (pinned Unicode 17.0 data, whatever the runtime's Unicode version).
export { KO_PARTICLES, MAX_PARTICLE_RUN, NO_SPACE } from "./word_tables.js";

/** A letter, digit or mark (Unicode 17.0 L, N, M) outside NO_SPACE. */
export function isWordChar(c: number): boolean {
  let lo = 0;
  let hi = WORD_RANGES.length >> 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (c > WORD_RANGES[2 * mid + 1]) lo = mid + 1;
    else if (c < WORD_RANGES[2 * mid]) hi = mid;
    else return true;
  }
  return false;
}

/** Apostrophes and hyphens join two words into one: O'Brien, Jean-Luc. */
const isJoiner = (c: number) => c === 0x27 || c === 0x2019 || c === 0x2d || c === 0x2010;

/** Hangul syllables and jamo. A word ends where Hangul meets another word character, so a Korean particle or ending written
 *  onto a number, address or Latin name ("010-1234-5678입니다", "kim@example.com으로") is not masked with it. */
export const isHangul = (c: number) =>
  (c >= 0x1100 && c <= 0x11ff) || (c >= 0x3130 && c <= 0x318f) || (c >= 0xa960 && c <= 0xa97f) || (c >= 0xac00 && c <= 0xd7ff) ||
  (c >= 0xffa0 && c <= 0xffdc);

const PARTICLES = new Set(KO_PARTICLES);

/** Whether `rest` is a run of KO_PARTICLES (at most MAX_PARTICLE_RUN code points). */
export function particlesOnly(rest: string): boolean {
  const cps = Array.from(rest);
  if (!cps.length || cps.length > MAX_PARTICLE_RUN) return false;
  const ok = [true];
  for (let i = 1; i <= cps.length; i++) {
    ok[i] = false;
    for (let k = 1; k <= 3 && k <= i && !ok[i]; k++) ok[i] = ok[i - k] && PARTICLES.has(cps.slice(i - k, i).join(""));
  }
  return ok[cps.length];
}

const before = (t: string, i: number): [number, number] => {
  const lo = t.charCodeAt(i - 1);
  if (i >= 2 && lo >= 0xdc00 && lo <= 0xdfff) {
    const hi = t.charCodeAt(i - 2);
    if (hi >= 0xd800 && hi <= 0xdbff) return [t.codePointAt(i - 2)!, 2];
  }
  return [lo, 1];
};
const at = (t: string, i: number): [number, number] => {
  const c = t.codePointAt(i)!;
  return [c, c > 0xffff ? 2 : 1];
};

/** Start of the word that the code point at `i` belongs to (UTF-16 index); `i` itself when that code point is not a word
 *  character. */
export function wordStart(text: string, i: number): number {
  if (i >= text.length || !isWordChar(at(text, i)[0])) return i;
  const h = isHangul(at(text, i)[0]);
  const same = (c: number) => isWordChar(c) && isHangul(c) === h;
  while (i > 0) {
    const [c, n] = before(text, i);
    if (same(c)) i -= n;
    else if (isJoiner(c) && i - n > 0 && same(before(text, i - n)[0])) i -= n;
    else break;
  }
  return i;
}

/** End of the word that the code point before `i` belongs to; `i` itself when that code point is not a word character, or
 *  when it is Hangul and the rest of the word is particles only (particlesOnly). */
export function wordEnd(text: string, i: number): number {
  if (i <= 0 || !isWordChar(before(text, i)[0])) return i;
  const h = isHangul(before(text, i)[0]);
  const same = (c: number) => isWordChar(c) && isHangul(c) === h;
  let e = i;
  while (e < text.length) {
    const [c, n] = at(text, e);
    if (same(c)) e += n;
    else if (isJoiner(c) && e + n < text.length && same(at(text, e + n)[0])) e += n;
    else break;
  }
  return h && e > i && particlesOnly(text.slice(i, e)) ? i : e;
}

type Segments = { containing(i: number): { segment: string; index: number } | undefined };
const segmenter: { segment(s: string): Segments } | null =
  typeof Intl !== "undefined" && (Intl as any).Segmenter ? new (Intl as any).Segmenter(undefined, { granularity: "grapheme" }) : null;

/** Widens every span to whole extended grapheme clusters (start back to its cluster start, end forward to the end of the
 *  cluster holding the last code point), then merges spans that now overlap. A merged group keeps the label and source of
 *  its winner: each next span replaces the winner only when its original (pre-snap) length in code points is strictly
 *  greater, so ties keep the earlier span. Empty spans stay as they are. `cells` (table mode, in text order): a span inside
 *  a cell's value range is clamped to that range after widening, so snapping never crosses a delimiter, quote or line break.
 *  Without Intl.Segmenter (e.g. Hermes builds without it, older browsers) the bounds are not moved to cluster edges: they
 *  are still on code points, but a span may cover part of an emoji sequence or combining sequence.
 *  `words` (option wholeWords): before that, a span that starts or ends inside a word is widened to the whole word
 *  (isWordChar: letters, digits and marks, joined by apostrophes and hyphens; not in scripts written without spaces; a word
 *  ends where Hangul meets another word character; a Hangul end is kept when the rest of the word is particles only). */
export function snapGraphemes(text: string, spans: KSpan[], cells?: readonly { start: number; end: number }[], words = false): KSpan[] {
  if (!spans.length || (!segmenter && !words)) return spans;
  const segs = segmenter ? segmenter.segment(text) : null;
  let j = 0;
  const snapped = spans
    .map((s) => {
      const len = cpLength(text, s.start, s.end);
      if (s.end <= s.start) return { span: { ...s }, len };
      const ws = words ? wordStart(text, s.start) : s.start;
      const we = words ? wordEnd(text, s.end) : s.end;
      const a = segs?.containing(ws);
      const b = segs?.containing(we - 1);
      let start = a ? a.index : ws;
      let end = b ? b.index + b.segment.length : we;
      if (cells) {
        while (j < cells.length && cells[j].end <= s.start) j++; // cells are in text order with increasing ends
        const c = cells[j];
        if (c && c.start <= s.start && s.end <= c.end) {
          start = Math.max(start, c.start);
          end = Math.min(end, c.end);
        }
      }
      return { span: { ...s, start, end }, len };
    })
    .sort((a, b) => a.span.start - b.span.start);
  const out: KSpan[] = [];
  let winLen = 0;
  for (const { span: s, len } of snapped) {
    const cur = out[out.length - 1];
    if (cur && s.start < cur.end) {
      const end = Math.max(cur.end, s.end);
      if (blockWins(s, cur) ?? len > winLen) {
        out[out.length - 1] = { ...s, start: cur.start, end };
        winLen = len;
      } else cur.end = end;
    } else {
      out.push(s);
      winLen = len;
    }
  }
  return out;
}
