// Unigram tokenizer that reproduces the Hugging Face `tokenizers` pipeline of the model (XLM-R vocabulary pruned to 35k pieces):
// Precompiled (SentencePiece) normalizer -> WhitespaceSplit -> Metaspace("▁", prepend always, split) -> Unigram (Viterbi).
// Offsets point into the original text, so detected spans can be masked in place.

export interface WebTokenizerFile {
  format: 1;
  type: "unigram";
  unk_id: number;
  bos_id: number;
  eos_id: number;
  pad_id: number;
  special: { content: string; id: number; lstrip: boolean; rstrip: boolean }[];
  pieces: string; // newline-separated, index = token id
  scores: string; // base64, little-endian float32 per piece
  charsmap: string; // base64 SentencePiece precompiled_charsmap
}

export interface Encoding {
  ids: number[];
  /** [start, end) in UTF-16 code units of the original text, one pair per id */
  offsets: [number, number][];
}

const META = "▁";
const UNK_PENALTY = 10;
// Rust char::is_whitespace (Unicode White_Space), which is what WhitespaceSplit uses
const WHITESPACE = /[\t\n\v\f\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/;

function base64ToBytes(b64: string): Uint8Array {
  if (typeof atob === "function") {
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  return new Uint8Array((globalThis as any).Buffer.from(b64, "base64"));
}

/** SentencePiece precompiled normalization map: a darts-clone double array over UTF-8 bytes plus a blob of
 *  NUL-terminated replacement strings. */
class CharsMap {
  private trie: Uint32Array;
  private normalized: Uint8Array;
  private cache = new Map<string, string | null>();
  private enc = new TextEncoder();
  private dec = new TextDecoder();

  constructor(blob: Uint8Array) {
    const view = new DataView(blob.buffer, blob.byteOffset, blob.byteLength);
    const trieSize = view.getUint32(0, true);
    this.trie = new Uint32Array(trieSize / 4);
    for (let i = 0; i < this.trie.length; i++) this.trie[i] = view.getUint32(4 + i * 4, true);
    this.normalized = blob.subarray(4 + trieSize);
  }

  /** Replacement for the shortest key that is a prefix of `chunk` (same as the Rust `spm_precompiled` crate), or null. */
  transform(chunk: string): string | null {
    const hit = this.cache.get(chunk);
    if (hit !== undefined) return hit;
    const key = this.enc.encode(chunk);
    const t = this.trie;
    const offset = (u: number) => (u >>> 10) << ((u & (1 << 9)) >>> 6);
    let pos = offset(t[0]);
    let result: string | null = null;
    for (let i = 0; i < key.length; i++) {
      const c = key[i];
      pos ^= c;
      const u = t[pos];
      if ((u & 0x800000ff) >>> 0 !== c) break; // label
      pos ^= offset(u);
      if ((u >>> 8) & 1) {
        const start = t[pos] & 0x7fffffff; // value
        let end = start;
        while (end < this.normalized.length && this.normalized[end] !== 0) end++;
        result = this.dec.decode(this.normalized.subarray(start, end));
        break;
      }
    }
    if (this.cache.size < 50000) this.cache.set(chunk, result);
    return result;
  }
}

interface TrieNode {
  next: Map<number, TrieNode>;
  id: number;
}

type Aligned = { ch: string; s: number; e: number };

const segmenter: { segment(s: string): Iterable<{ segment: string; index: number }> } | null =
  typeof Intl !== "undefined" && (Intl as any).Segmenter ? new (Intl as any).Segmenter(undefined, { granularity: "grapheme" }) : null;

function utf8Length(s: string): number {
  let n = 0;
  for (const ch of s) {
    const c = ch.codePointAt(0)!;
    n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
  }
  return n;
}

export class Tokenizer {
  readonly unkId: number;
  readonly bosId: number;
  readonly eosId: number;
  readonly padId: number;
  readonly vocabSize: number;
  private scores: Float32Array;
  private root: TrieNode = { next: new Map(), id: -1 };
  private unkScore: number;
  private charsmap: CharsMap;
  private specialRe: RegExp;
  private specialIds = new Map<string, number>();

  constructor(file: WebTokenizerFile) {
    if (file.format !== 1 || file.type !== "unigram") throw new Error("unsupported tokenizer file");
    const pieces = file.pieces.split("\n");
    const raw = base64ToBytes(file.scores);
    this.scores = new Float32Array(pieces.length);
    const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
    for (let i = 0; i < pieces.length; i++) this.scores[i] = dv.getFloat32(i * 4, true);
    let min = Infinity;
    for (const s of this.scores) min = Math.min(min, s);
    this.unkScore = min - UNK_PENALTY;
    const special = new Set(file.special.map((t) => t.id));
    const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // longest first; lstrip/rstrip tokens also take the whitespace next to them
    const alts = [...file.special]
      .sort((a, b) => b.content.length - a.content.length)
      .map((t) => (t.lstrip ? "\\s*" : "") + esc(t.content) + (t.rstrip ? "\\s*" : ""));
    this.specialRe = new RegExp(alts.join("|"), "g");
    for (const t of file.special) this.specialIds.set(t.content, t.id);
    pieces.forEach((p, id) => {
      if (special.has(id)) return;
      let node = this.root;
      for (const ch of p) {
        const c = ch.codePointAt(0)!;
        let n = node.next.get(c);
        if (!n) node.next.set(c, (n = { next: new Map(), id: -1 }));
        node = n;
      }
      node.id = id;
    });
    this.unkId = file.unk_id;
    this.bosId = file.bos_id;
    this.eosId = file.eos_id;
    this.padId = file.pad_id;
    this.vocabSize = pieces.length;
    this.charsmap = new CharsMap(base64ToBytes(file.charsmap));
  }

  /** Normalized characters, each aligned to the range of the original text it came from. */
  private normalize(text: string): Aligned[] {
    const out: Aligned[] = [];
    const graphemes = segmenter
      ? Array.from(segmenter.segment(text), (g) => ({ g: g.segment, i: g.index }))
      : (() => {
          const a: { g: string; i: number }[] = [];
          let i = 0;
          for (const ch of text) {
            a.push({ g: ch, i });
            i += ch.length;
          }
          return a;
        })();
    for (const { g, i } of graphemes) {
      const end = i + g.length;
      if (utf8Length(g) < 6) {
        const norm = this.charsmap.transform(g);
        if (norm !== null) {
          // the whole grapheme becomes `norm`; every new character points at the whole original grapheme
          for (const ch of norm) out.push({ ch, s: i, e: end });
          continue;
        }
      }
      let j = i;
      for (const ch of g) {
        const norm = this.charsmap.transform(ch);
        if (norm === null) out.push({ ch, s: j, e: j + ch.length });
        else for (const n of norm) out.push({ ch: n, s: j, e: j + ch.length });
        j += ch.length;
      }
    }
    return out;
  }

  /** Words after WhitespaceSplit + Metaspace, each starting with "▁". */
  private preTokenize(chars: Aligned[]): Aligned[][] {
    const words: Aligned[][] = [];
    let cur: Aligned[] = [];
    const flush = () => {
      if (cur.length) words.push(cur);
      cur = [];
    };
    for (const c of chars) {
      if (WHITESPACE.test(c.ch)) flush();
      else cur.push(c);
    }
    flush();
    const out: Aligned[][] = [];
    for (const w of words) {
      // prepend "▁" (aligned to the first character), then split before every "▁"
      const seq: Aligned[] = w[0].ch === META ? w : [{ ch: META, s: w[0].s, e: w[0].e }, ...w];
      let piece: Aligned[] = [];
      for (const c of seq) {
        if (c.ch === META && piece.length) {
          out.push(piece);
          piece = [];
        }
        piece.push(c);
      }
      if (piece.length) out.push(piece);
    }
    return out;
  }

  private viterbi(word: Aligned[], ids: number[], offsets: [number, number][]) {
    const n = word.length;
    const cps = word.map((c) => c.ch.codePointAt(0)!);
    const bestScore = new Float64Array(n + 1).fill(-Infinity);
    const bestId = new Int32Array(n + 1).fill(-1);
    const bestStart = new Int32Array(n + 1).fill(-1);
    bestScore[0] = 0;
    for (let i = 0; i < n; i++) {
      if (bestStart[i] < 0 && i > 0) continue;
      const base = bestScore[i];
      let node: TrieNode | undefined = this.root;
      let single = false;
      for (let j = i; j < n; j++) {
        node = node.next.get(cps[j]);
        if (!node) break;
        if (node.id >= 0) {
          const cand = base + this.scores[node.id];
          if (bestStart[j + 1] < 0 || cand > bestScore[j + 1]) {
            bestScore[j + 1] = cand;
            bestId[j + 1] = node.id;
            bestStart[j + 1] = i;
          }
          if (j === i) single = true;
        }
      }
      if (!single) {
        const cand = base + this.unkScore;
        if (bestStart[i + 1] < 0 || cand > bestScore[i + 1]) {
          bestScore[i + 1] = cand;
          bestId[i + 1] = this.unkId;
          bestStart[i + 1] = i;
        }
      }
    }
    const toks: [number, number, number][] = [];
    for (let e = n; e > 0; e = bestStart[e]) toks.push([bestId[e], bestStart[e], e]);
    toks.reverse();
    let prevUnk = false;
    for (const [id, a, b] of toks) {
      let s = Infinity;
      let e = -Infinity;
      for (let k = a; k < b; k++) {
        s = Math.min(s, word[k].s);
        e = Math.max(e, word[k].e);
      }
      if (id === this.unkId && prevUnk) {
        // consecutive unknown characters become one <unk> (fuse_unk)
        const last = offsets[offsets.length - 1];
        last[0] = Math.min(last[0], s);
        last[1] = Math.max(last[1], e);
      } else {
        ids.push(id);
        offsets.push([s, e]);
      }
      prevUnk = id === this.unkId;
    }
  }

  /** Token ids and original-text offsets, without <s> and </s>. */
  encode(text: string): Encoding {
    const ids: number[] = [];
    const offsets: [number, number][] = [];
    // special-token strings in the text ("<s>", "<mask>", ...) are matched before normalization, as in tokenizers
    let last = 0;
    const run = (a: number, b: number) => {
      if (b <= a) return;
      for (const word of this.preTokenize(this.normalize(text.slice(a, b)))) {
        for (const c of word) {
          c.s += a;
          c.e += a;
        }
        this.viterbi(word, ids, offsets);
      }
    };
    for (const m of text.matchAll(this.specialRe)) {
      run(last, m.index!);
      ids.push(this.specialIds.get(m[0].trim())!);
      offsets.push([m.index!, m.index! + m[0].length]);
      last = m.index! + m[0].length;
    }
    run(last, text.length);
    return { ids, offsets };
  }
}
