// Table mode (CSV / TSV / semicolon / markdown pipe tables) and grapheme snapping. Same algorithm as the C core
// (native/core/src/api.c, pii_detect_table); parity is checked span for span. Positions are UTF-16 code units (Span
// offsets), but every length, count and median below is in code points.
import type { Span } from "./types.js";

export type TableDelimiter = "auto" | "," | "\t" | ";" | "markdown";

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

/** Parsed table, or null when the text is not a table (no delimiter found, or fewer than 2 rows). */
export function parseTable(text: string, delimiter: TableDelimiter = "auto"): Table | null {
  let d: Table["delimiter"] | null;
  if (delimiter === "auto") d = detectDelimiter(text);
  else if (delimiter === "markdown") d = "|";
  else if (delimiter === "," || delimiter === "\t" || delimiter === ";") d = delimiter;
  else throw new Error(`[@newmean-ai/pii] unknown table delimiter ${JSON.stringify(delimiter)} ("auto", ",", "\\t", ";", "markdown")`);
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

export function detectTableSpans(text: string, spans: Span[], options: TableOptions = {}, enabled?: Set<string>, extra: Span[] = []): Span[] {
  const { spans: out, cells } = tableSteps(text, spans, options, enabled, extra);
  return snapGraphemes(text, out, cells ?? undefined);
}

function tableSteps(text: string, spans: Span[], options: TableOptions, enabled?: Set<string>, extra: Span[] = []): { spans: Span[]; cells: Cell[] | null } {
  const t = parseTable(text, options.delimiter ?? "auto");
  if (!t) return { spans, cells: null };
  const { cells } = t;
  const firstData = options.header ?? true ? 1 : 0;

  // clip: keep each span ∩ cell value that holds a non-whitespace code point
  const clipped: Span[] = [];
  const byCell: Span[][] = cells.map(() => []);
  let k = 0;
  cells.forEach((cell, ci) => {
    if (cell.end <= cell.start) return;
    while (k < spans.length && spans[k].end <= cell.start) k++;
    for (let j = k; j < spans.length && spans[j].start < cell.end; j++) {
      const s = Math.max(spans[j].start, cell.start);
      const e = Math.min(spans[j].end, cell.end);
      if (e <= s || !nonSpace(text, s, e)) continue;
      const c: Span = { start: s, end: e, label: spans[j].label, source: spans[j].source };
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

  // merge overlapping spans: a table span's label wins, else the longest span's (ties → earliest start). Group ends are
  // tracked in the loop (a spread over a group of 100k+ spans would exceed the engine's argument limit).
  const all = [...clipped, ...propagated, ...extra].sort((a, b) => a.start - b.start);
  const groups: { spans: Span[]; end: number }[] = [];
  for (const s of all) {
    const g = groups[groups.length - 1];
    if (g && s.start < g.end) {
      g.spans.push(s);
      if (s.end > g.end) g.end = s.end;
    } else groups.push({ spans: [s], end: s.end });
  }
  const merged = groups.map(({ spans: g, end }) => {
    let pick = g.find((s) => s.source === "table");
    if (!pick) {
      pick = g[0];
      let len = cpLength(text, pick.start, pick.end);
      for (let i = 1; i < g.length; i++) {
        const l = cpLength(text, g[i].start, g[i].end);
        if (l > len) [pick, len] = [g[i], l];
      }
    }
    return { start: g[0].start, end, label: pick.label, source: pick.source };
  });
  return { spans: merged, cells };
}

type Segments = { containing(i: number): { segment: string; index: number } | undefined };
const segmenter: { segment(s: string): Segments } | null =
  typeof Intl !== "undefined" && (Intl as any).Segmenter ? new (Intl as any).Segmenter(undefined, { granularity: "grapheme" }) : null;

/** Widens every span to whole extended grapheme clusters (start back to its cluster start, end forward to the end of the
 *  cluster holding the last code point), then merges spans that now overlap. A merged group keeps the label and source of
 *  its winner: each next span replaces the winner only when its original (pre-snap) length in code points is strictly
 *  greater, so ties keep the earlier span. Empty spans stay as they are. `cells` (table mode, in text order): a span inside
 *  a cell's value range is clamped to that range after widening, so snapping never crosses a delimiter, quote or line break.
 *  Without Intl.Segmenter (e.g. Hermes builds without it, older browsers) the spans are returned unchanged: their bounds
 *  are still on code points, but a span may cover part of an emoji sequence or combining sequence. */
export function snapGraphemes(text: string, spans: Span[], cells?: readonly { start: number; end: number }[]): Span[] {
  if (!segmenter || !spans.length) return spans;
  const segs = segmenter.segment(text);
  let j = 0;
  const snapped = spans
    .map((s) => {
      const len = cpLength(text, s.start, s.end);
      if (s.end <= s.start) return { span: { ...s }, len };
      const a = segs.containing(s.start);
      const b = segs.containing(s.end - 1);
      let start = a ? a.index : s.start;
      let end = b ? b.index + b.segment.length : s.end;
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
  const out: Span[] = [];
  let winLen = 0;
  for (const { span: s, len } of snapped) {
    const cur = out[out.length - 1];
    if (cur && s.start < cur.end) {
      const end = Math.max(cur.end, s.end);
      if (len > winLen) {
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
