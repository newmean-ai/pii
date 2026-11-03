// User lists (options allow, block and listsId, added in 1.4.0): validation, compared forms, block matching and the allow
// test. Same rules as the C core (native/core/src/lists.c); user-facing rules in js/README.md (Lists).
import { isHangul, isWordChar, keyLess, particlesOnly, type Key, type KSpan, type UserLists } from "./table.js";
import { MAX_PARTICLE_RUN } from "./word_tables.js";
import type { Label, Span } from "./types.js";

const LABEL_NAMES: readonly string[] = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"];
const MAX_ENTRIES = 10_000;
const MAX_CP = 64;
const MAX_U16 = 256;
const MAX_ID = 128;

export type AllowEntry = string | { text: string; label?: Label };
export interface BlockEntry {
  text: string;
  label: Label;
}

export type ListErrorReason =
  | "bad_shape"
  | "too_long"
  | "bad_text"
  | "empty"
  | "edge_space"
  | "unknown_label"
  | "too_many"
  | "label_conflict"
  | "in_both"
  | "bad_lists_id";

/** An invalid `allow`, `block` or `listsId` option. Carries where and why, never the entry's text. */
export class ListError extends Error {
  readonly list: "allow" | "block" | "meta";
  /** Entry index in that list; undefined for `listsId` and for errors about the list itself. */
  readonly index: number | undefined;
  readonly reason: ListErrorReason;
  constructor(list: "allow" | "block" | "meta", index: number | undefined, reason: ListErrorReason) {
    super(`[@newmean-ai/pii] ${list === "meta" ? "listsId" : `${list} list`}${index === undefined ? "" : ` entry ${index}`}: ${reason}`);
    this.name = "ListError";
    this.list = list;
    this.index = index;
    this.reason = reason;
  }
}

const fold = (c: number) => (c >= 0x41 && c <= 0x5a ? c + 32 : c);
const family = (label: string) => label.replace(/_L2$/, "");
/** The 25 White_Space code points of Unicode 17.0. */
const whiteSpace = (c: number) =>
  (c >= 0x09 && c <= 0x0d) || c === 0x20 || c === 0x85 || c === 0xa0 || c === 0x1680 || (c >= 0x2000 && c <= 0x200a) ||
  c === 0x2028 || c === 0x2029 || c === 0x202f || c === 0x205f || c === 0x3000;
const lineBreak = (c: number) => c === 0x0a || c === 0x0d || c === 0x85 || c === 0x2028 || c === 0x2029;
const isJoiner = (c: number) => c === 0x27 || c === 0x2019 || c === 0x2d || c === 0x2010;

/** Code points of s, or null when s holds an unpaired surrogate. */
function codePoints(s: string): number[] | null {
  const out: number[] = [];
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (d < 0xdc00 || d > 0xdfff) return null;
      out.push(((c - 0xd800) << 10) + (d - 0xdc00) + 0x10000);
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) return null;
    else out.push(c);
  }
  return out;
}

/** Checks 1-6 of Section 3 on a text (shape and label are checked by the caller); the folded compared form, or a reason. */
function checkText(text: string): number[] | ListErrorReason {
  if (text.length > MAX_U16) return "too_long"; // 256 UTF-16 units also bound the UTF-8 bytes (at most 768)
  const cps = codePoints(text);
  if (!cps || cps.some((c) => c === 0 || lineBreak(c))) return "bad_text";
  if (!cps.length) return "empty";
  if (cps.length > MAX_CP) return "too_long";
  if (whiteSpace(cps[0]) || whiteSpace(cps[cps.length - 1])) return "edge_space";
  return cps.map(fold);
}

interface TrieNode {
  next: Map<number, TrieNode>;
  term: number; // block term index, or -1
}

/** Validated lists: the allow forms with their families, and a trie of the block forms. */
export class Lists implements UserLists {
  readonly allowCount: number;
  readonly blockCount: number;
  private readonly allow = new Map<string, Set<string> | null>(); // compared form -> families (null: any)
  private readonly root: TrieNode = { next: new Map(), term: -1 };
  private readonly blockLabel: string[] = [];
  private readonly blockIndex: number[] = [];

  /** Validates in the order of Section 3: allow, then block, then the checks across entries. Throws ListError. */
  constructor(allow: unknown, block: unknown) {
    const a = readList(allow, "allow");
    const b = readList(block, "block");
    // label_conflict: the earliest block entry whose label differs from the first entry of its form; in_both after it
    const firstLabel = new Map<string, string>();
    let bad = -1;
    for (const e of b) {
      const l = firstLabel.get(e.form);
      if (l === undefined) firstLabel.set(e.form, e.label!);
      else if (l !== e.label && bad < 0) bad = e.index;
    }
    if (bad >= 0) throw new ListError("block", bad, "label_conflict");
    const allowForms = new Set(a.map((e) => e.form));
    const both = b.find((e) => allowForms.has(e.form));
    if (both) throw new ListError("block", both.index, "in_both");
    for (const e of a) {
      const fams = this.allow.get(e.form);
      if (fams === null) continue;
      if (e.label === null) this.allow.set(e.form, null);
      else if (fams) fams.add(family(e.label));
      else this.allow.set(e.form, new Set([family(e.label)]));
    }
    for (const e of b) {
      if (this.blockTerm(e.form) >= 0) continue;
      let node = this.root;
      for (const c of e.cps) {
        let n = node.next.get(c);
        if (!n) node.next.set(c, (n = { next: new Map(), term: -1 }));
        node = n;
      }
      node.term = this.blockLabel.length;
      this.blockLabel.push(e.label!);
      this.blockIndex.push(e.index);
    }
    this.allowCount = this.allow.size;
    this.blockCount = this.blockLabel.length;
  }

  private blockTerm(form: string): number {
    let node: TrieNode | undefined = this.root;
    for (const ch of form) if (!(node = node.next.get(ch.codePointAt(0)!))) return -1;
    return node.term;
  }

  /** text[start, end) in compared form is an allow entry whose families include label's. */
  allowed(text: string, start: number, end: number, label: string): boolean {
    if (!this.allow.size || end <= start || end - start > 2 * MAX_CP) return false;
    const fams = this.allow.get(text.slice(start, end).replace(/[A-Z]/g, (ch) => ch.toLowerCase()));
    return fams === null || (fams !== undefined && fams.has(family(label)));
  }

  /** Block regions of text[start, end) (UTF-16 offsets): every match that starts and ends on a word boundary of the range,
   *  overlapping ones unioned, each region with the label and key of its smallest key. In start order. */
  blocks(text: string, start: number, end: number): KSpan[] {
    const out: KSpan[] = [];
    if (!this.blockCount || end <= start) return out;
    const cp: number[] = [];
    const u16: number[] = [];
    for (let i = start; i < end; ) {
      const c = text.codePointAt(i)!;
      const w = c > 0xffff && i + 1 < end ? 2 : 1;
      cp.push(w === 2 ? c : text.charCodeAt(i));
      u16.push(i);
      i += w;
    }
    u16.push(end);
    const n = cp.length;
    const sameWord = (c: number, h: boolean) => isWordChar(c) && isHangul(c) === h;
    const startsWord = (i: number) => {
      if (i === 0 || !isWordChar(cp[i])) return true;
      const h = isHangul(cp[i]);
      if (sameWord(cp[i - 1], h)) return false;
      return !(isJoiner(cp[i - 1]) && i >= 2 && sameWord(cp[i - 2], h));
    };
    const endsWord = (e: number) => {
      if (e === n || !isWordChar(cp[e - 1])) return true;
      const h = isHangul(cp[e - 1]);
      const lim = h ? MAX_PARTICLE_RUN + 1 : 1; // enough to tell a particle run
      let x = e;
      while (x < n && x - e < lim && (sameWord(cp[x], h) || (isJoiner(cp[x]) && x + 1 < n && sameWord(cp[x + 1], h)))) x++;
      return x === e || (h && particlesOnly(text.slice(u16[e], u16[x])));
    };
    let cur: KSpan | null = null;
    for (let i = 0; i < n; i++) {
      if (!startsWord(i)) continue;
      let node: TrieNode | undefined = this.root;
      for (let k = i; k < n && k - i < MAX_CP; k++) {
        if (!(node = node.next.get(fold(cp[k])))) break;
        if (node.term < 0 || !endsWord(k + 1)) continue;
        const key: Key = { start: u16[i], len: k + 1 - i, index: this.blockIndex[node.term] };
        const label = this.blockLabel[node.term];
        if (cur && u16[i] < cur.end) {
          if (u16[k + 1] > cur.end) cur.end = u16[k + 1];
          if (keyLess(key, cur.key!)) [cur.label, cur.key] = [label, key];
        } else out.push((cur = { start: u16[i], end: u16[k + 1], label, source: "block", key }));
      }
    }
    return out;
  }
}

interface Entry {
  form: string;
  cps: number[];
  index: number;
  label: string | null; // null: any (allow)
}

function readList(list: unknown, name: "allow" | "block"): Entry[] {
  if (list === undefined || list === null) return [];
  if (!Array.isArray(list)) throw new ListError(name, undefined, "bad_shape");
  const out: Entry[] = [];
  for (let i = 0; i < Math.min(list.length, MAX_ENTRIES); i++) {
    const x: unknown = list[i];
    let text: unknown;
    let label: unknown = null;
    if (name === "allow" && typeof x === "string") text = x;
    else if (typeof x === "object" && x !== null && !Array.isArray(x)) {
      text = (x as { text?: unknown }).text;
      label = (x as { label?: unknown }).label ?? null;
    } else throw new ListError(name, i, "bad_shape");
    if (typeof text !== "string" || (label !== null && typeof label !== "string") || (name === "block" && label === null))
      throw new ListError(name, i, "bad_shape");
    const cps = checkText(text);
    if (typeof cps === "string") throw new ListError(name, i, cps);
    if (label !== null && !LABEL_NAMES.includes(label as string)) throw new ListError(name, i, "unknown_label");
    out.push({ form: String.fromCodePoint(...cps), cps, index: i, label: label as string | null });
  }
  if (list.length > MAX_ENTRIES) throw new ListError(name, MAX_ENTRIES, "too_many");
  return out;
}

/** Validates listsId (checked first, before the lists): 1 to 128 code points, no U+0000, unpaired surrogate or line break. */
export function checkListsId(id: unknown): string | null {
  if (id === undefined || id === null) return null;
  const cps = typeof id === "string" ? codePoints(id) : null;
  if (!cps || !cps.length || cps.length > MAX_ID || cps.some((c) => c === 0 || lineBreak(c))) throw new ListError("meta", undefined, "bad_lists_id");
  return id as string;
}

/** Fresh public span objects (no internal key). */
export const publicSpans = (spans: KSpan[]): Span[] => spans.map((s) => ({ start: s.start, end: s.end, label: s.label, source: s.source }));
