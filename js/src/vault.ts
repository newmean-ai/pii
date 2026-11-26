// A vault: the local map from numbered placeholders back to the values they replace, so that masking can be undone in the
// model's answer and in tool-call arguments. It is an ordinary in-memory object; nothing in it is sent anywhere.
//
//   const vault = createVault();                              // one per user or conversation, never shared between users
//   const { text } = await redactor.redact(input, { vault }); // "Call [PERSON_1] on [PHONE_1]"
//   vault.restore(answer);                                    // the placeholders in the answer become the values again
//
// The same value always gets the same placeholder within a vault, so a masked conversation stays the same from turn to
// turn, and a value seen once is masked again wherever it reappears in later text, also where the model would miss it on
// its own (spans with source "vault").
//
// Scripts are decided with the explicit code point ranges below, not with the platform's Unicode script data, so that the
// SDKs find the same known values (shared cases: python/tests/vault_cases.json; word characters still come from each
// runtime's Unicode data).
//
// A known value is found as it is written and also: with full-width ASCII (U+FF01 to U+FF5E) and U+3000 read as ASCII;
// in NFC (Hangul written as conjoining jamo, a decomposed accent); in capitals; and, for an e-mail address, a user name, a
// URL's scheme and host and a value of two or more words, in any ASCII case. A one-word value in another case is another
// word ("Will" and "will"). The hit carries the known value, so one value keeps one placeholder however it is written.

export interface VaultEntry {
  /** The placeholder, e.g. "[PERSON_1]". */
  tag: string;
  /** Label family the placeholder was made for (PERSON_L2 shares PERSON's numbers). */
  label: string;
  /** The original text. */
  value: string;
}

/** A vault's whole state (`Vault.toJSON`). */
export interface VaultState {
  entries: VaultEntry[];
  replays: [string, unknown][];
  /** The highest number used in each family, numbers skipped for placeholders a model made up included. */
  counters?: Record<string, number>;
  /** The placeholders of values a model found that are shorter than the vault searches for at word edges, which are not
   *  found as a whole value elsewhere (`tagFound`). Since 1.7.2. */
  local?: string[];
}

/** Limits a restore to some placeholders, e.g. those this SDK put into the current model call (`only`). */
export interface RestoreOptions {
  /** Escape the values for the inside of a JSON string (tool-call arguments as JSON text). */
  json?: boolean;
  /** Restore only these placeholders; others stay as they are. A Map also fixes the values (a snapshot from `bindings`,
   *  so an answer that arrives after the vault was cleared and refilled still gets the values of its own call).
   *  Default: every placeholder of the vault, with its current value. */
  only?: ReadonlySet<string> | ReadonlyMap<string, string>;
}

/** Undoes masking in text that arrives in pieces (a streamed answer). A placeholder split across pieces is held back
 *  until it is complete, so `push` may return less than it was given; `flush` returns what is left. */
export interface VaultRestorer {
  push(chunk: string): string;
  flush(): string;
}

export interface Vault {
  /** Number of values in the vault. */
  readonly size: number;
  /** Goes up by one on every `clear`, so that work started before can tell its placeholders no longer resolve. */
  readonly generation: number;
  /** The placeholder for `value`, adding it under `label` (its family) when new. The same value keeps its first tag.
   *  An empty value has no placeholder (""). Numbers go up to 999,999,999,999; a family that has used them up (after
   *  an imported entry with that number) goes on as "[PERSON__1]". */
  tag(label: string, value: string): string;
  /** The value behind a placeholder, or undefined. */
  value(tag: string): string | undefined;
  /** Replaces the vault's placeholders in `text` with their values; other bracketed text is left as it is. */
  restore(text: string, options?: RestoreOptions): string;
  /** `restore` applied to every string inside a JSON-like value (objects and arrays are copied, keys are kept). */
  restoreValue<T>(value: T, options?: Omit<RestoreOptions, "json">): T;
  /** A restorer for a streamed text. It uses the values the vault holds when it is created, for the whole stream. */
  restorer(options?: RestoreOptions): VaultRestorer;
  /** The current values of these placeholders, as a snapshot for `only` (placeholders the vault does not hold are left out). */
  bindings(tags: Iterable<string>): Map<string, string>;
  /** Replaces the vault's known values in `text` with their placeholders (no model call). Same matching as the "vault"
   *  spans of `redact`: the text as written and in the spellings named at the top of this file (capitals, ASCII case
   *  where it does not matter, full width, NFC), at word edges, 3 characters or more (2 in scripts written without
   *  spaces, and two Hangul syllables). Known values
   *  that overlap ("Alex Kim" and "Kim Lee" in "Alex Kim Lee") are hidden together under a new placeholder. With
   *  `escape`, placeholder-like text in the rest of `text` is escaped as `escapeTags` does, except the placeholders in
   *  `keep`. `loose` as in `find`. */
  hide(text: string, options?: { escape?: boolean; keep?: ReadonlySet<string>; loose?: boolean }): string;
  /** Remembers what the model wrote (`masked`) for a text or value the middleware gave back (restored or not), so that
   *  the next call sends the model's own text again instead of searching the returned one for known values. `key` is the
   *  returned text, or any key the caller chooses. The 4,096 used last are kept (`toJSON` carries them). With
   *  `generation`, nothing is stored if the vault was cleared since (returns false). */
  remember(key: string, masked: unknown, generation?: number): boolean;
  /** What `remember` stored for `key` (a copy), or undefined: also when two different wordings were stored for it. */
  recall(key: string): unknown;
  /** `hide` applied to every string inside a JSON-like value; a string that is exactly a known value is replaced
   *  whatever its length (a restored tool-call argument such as an ID "42"). */
  hideValue<T>(value: T): T;
  /** For the middlewares: the known value that `text` is as a whole, whatever its length and however it is written
   *  (its white space as any white space, a line break also as a comma and a comma also as a line break or left out; a
   *  number in another spelling; with JSON, URL or Python repr escapes; in the spellings `find` matches). When it can be
   *  read as more than one, the one held longest. */
  whole(text: string): { label: string; value: string } | undefined;
  /** For the middlewares: when `value` was added, as its place among the values (smaller is older); undefined for a
   *  value the vault does not hold. */
  age(value: string): number | undefined;
  /** Where the known values occur in `text` (sorted, non-overlapping; overlapping occurrences are merged into one hit whose
   *  value is the merged text and whose label is the first one's). `loose`, for text a model wrote that the
   *  middleware has no record of: every known value wherever it occurs, inside longer words and whatever its length
   *  ("x42y"); this can mask more than needed, never less.
   *  `variants`, for the middlewares: also where a known value is written another way. Its white space may be any white
   *  space, each line break in it also a comma ("12 Main St\nSeoul" as "12 Main St, Seoul" or indented on two lines),
   *  and each comma before white space also a line break or nothing ("12 Main St, Seoul" over two lines or as "12 Main
   *  St Seoul"); a number may be written in any spelling ("8.21023456789e11" for 821023456789). The hit then carries the
   *  known value, not the text that was found. */
  find(text: string, options?: { loose?: boolean; variants?: boolean }): { start: number; end: number; label: string; value: string }[];
  /** The entries in the order they were added, for keeping a vault between requests (`createVault(entries)`). They hold
   *  the original values: store them only where the original text may be stored. */
  entries(): VaultEntry[];
  /** The entries and what `remember` stored, for keeping a vault that a middleware uses between requests
   *  (`createVault(vault.toJSON())`, or `JSON.stringify(vault)`). Holds the original values, like `entries`. */
  toJSON(): VaultState;
  clear(): void;
}

const family = (l: string) => l.replace(/_L2$/, "");
/** Placeholder grammar, the same in every package: "[" + family + "_" + number + "]", where the family is an ASCII capital
 *  letter followed by capitals, digits and underscores (32 characters at most) and the number is 1 to 12 ASCII digits.
 *  Longer look-alikes are ordinary text: they are neither escaped nor restored. */
const TAG = /\[([A-Z][A-Z0-9_]{0,31}_[0-9]{1,12})\]/g;
const TAG_PREFIX = /\[[A-Z0-9_]*$/;
/** Longest placeholder: "[" + a 32-character family + "_" + up to 12 digits + "]". A streamed restore holds back this much. */
const MAX_FAMILY = 32;
const MAX_TAG = MAX_FAMILY + 15;
const MAX_NUMBER = 999_999_999_999;
/** How far above the numbers in use a made-up placeholder number is still skipped. */
const RESERVE_AHEAD = 1_000_000;
/** The family that goes on when the numbers of `family` are used up (which takes an imported or made-up placeholder
 *  with the highest number): one more "_" at its end, "PERSON" → "PERSON_", so that its placeholders stay apart
 *  ("[PERSON__1]"). A family of full length gets its last other character turned into "_" instead, and one that is a
 *  letter and underscores only goes on with the next letter (after Z, with A). */
const nextFamily = (family: string): string => {
  if (family.length < MAX_FAMILY) return `${family}_`;
  for (let i = family.length - 1; i > 0; i--) if (family[i] !== "_") return `${family.slice(0, i)}_${family.slice(i + 1)}`;
  return String.fromCharCode(family[0] < "Z" ? family.charCodeAt(0) + 1 : 65) + family.slice(1);
};
/** How many families are tried after the first: at most 31 steps to a letter and underscores, then the 25 other letters. */
const FAMILY_STEPS = 56;
/** U+FF3B FULLWIDTH LEFT SQUARE BRACKET: what `escapeTags` puts in front of placeholder-like text that was already there. */
const ESCAPED_BRACKET = "\uff3b";

/** Text with every placeholder-like part ("[PERSON_1]") that was already in it turned into "［PERSON_1]", so that the
 *  model and the restore step can tell it from the placeholders the SDK puts in. Same length (one UTF-16 unit each). */
export function escapeTags(text: string): string {
  return text.includes("[") ? text.replace(TAG, (t) => ESCAPED_BRACKET + t.slice(1)) : text;
}

/** The vault placeholders in `text`, in order of appearance (duplicates kept). */
export function vaultTags(text: string): string[] {
  return Array.from(text.matchAll(TAG), (m) => m[0]);
}

type Ranges = readonly (readonly [number, number])[];
// Scripts written without spaces between words (letters and marks of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese,
// Javanese, Han, Hiragana and Katakana): the same code point ranges in every package.
const NO_SPACE: Ranges = [
  [0x0e01, 0x0e3a], [0x0e40, 0x0e4e], // Thai
  [0x0e81, 0x0ecf], // Lao
  [0x1000, 0x103f], [0x1050, 0x108f], [0x109a, 0x109d], [0xa9e0, 0xa9ef], [0xa9fa, 0xa9fe], [0xaa60, 0xaa7f], // Myanmar
  [0x1780, 0x17d3], [0x17d7, 0x17d7], [0x17dc, 0x17dd], [0x19e0, 0x19ff], // Khmer
  [0x1a20, 0x1a5e], [0x1a60, 0x1a7c], [0x1a7f, 0x1a7f], [0x1aa7, 0x1aa7], // Tai Tham
  [0x1b00, 0x1b4c], // Balinese
  [0xa980, 0xa9c0], [0xa9cf, 0xa9cf], // Javanese
  [0x2e80, 0x2fdf], [0x3005, 0x3005], [0x3007, 0x3007], [0x3021, 0x3029], [0x3038, 0x303b], // Han radicals, marks
  [0x3041, 0x3096], [0x309d, 0x309f], // Hiragana
  [0x30a1, 0x30fa], [0x30fd, 0x30ff], [0x31f0, 0x31ff], [0xff66, 0xff6f], [0xff71, 0xff9d], // Katakana
  [0x1b000, 0x1b16f], // kana supplement and extensions
  [0x3400, 0x4dbf], [0x4e00, 0x9fff], [0xf900, 0xfaff], [0x20000, 0x3347f], // Han, to Extension J
];
// The marks among them (vowel signs, tone marks and the like of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese and
// Javanese; Unicode general category M): a mark belongs to the letter before it, so the place before one is no word edge
// ("สม" is not found in "สมัคร"). The same code point ranges in every package.
const NO_SPACE_MARKS: Ranges = [
  [0x0e31, 0x0e31], [0x0e34, 0x0e3a], [0x0e47, 0x0e4e], // Thai
  [0x0eb1, 0x0eb1], [0x0eb4, 0x0ebc], [0x0ec8, 0x0ece], // Lao
  [0x102b, 0x103e], [0x1056, 0x1059], [0x105e, 0x1060], [0x1062, 0x1064], [0x1067, 0x106d], [0x1071, 0x1074], [0x1082, 0x108d],
  [0x108f, 0x108f], [0x109a, 0x109d], [0xa9e5, 0xa9e5], [0xaa7b, 0xaa7d], // Myanmar
  [0x17b4, 0x17d3], [0x17dd, 0x17dd], // Khmer
  [0x1a55, 0x1a5e], [0x1a60, 0x1a7c], [0x1a7f, 0x1a7f], // Tai Tham
  [0x1b00, 0x1b04], [0x1b34, 0x1b44], // Balinese
  [0xa980, 0xa983], [0xa9b3, 0xa9c0], // Javanese
];
const HANGUL: Ranges = [[0x1100, 0x11ff], [0x3131, 0x318e], [0xa960, 0xa97f], [0xac00, 0xd7a3], [0xd7b0, 0xd7ff], [0xffa0, 0xffdc]];
const inRanges = (r: Ranges) => (c: string) => {
  const cp = c.codePointAt(0)!;
  return r.some(([a, b]) => cp >= a && cp <= b);
};
const noSpace = inRanges(NO_SPACE);
const noSpaceMark = inRanges(NO_SPACE_MARKS);
const hangul = inRanges(HANGUL);
const WORD = /^[\p{L}\p{N}\p{M}_]$/u;
const isWord = (c: string) => WORD.test(c);
const NUMBER_CHAR = /^\p{N}$/u;
const isNumber = (c: string) => NUMBER_CHAR.test(c);

const firstChar = (s: string) => String.fromCodePoint(s.codePointAt(0)!);
const lastChar = (s: string) => Array.from(s.slice(-2)).pop()!;
const charBefore = (text: string, i: number) => (i <= 0 ? "" : Array.from(text.slice(Math.max(0, i - 2), i)).pop()!);
const charAt = (text: string, i: number) => (i >= text.length ? "" : firstChar(text.slice(i)));

/** A precomposed Hangul syllable (U+AC00 to U+D7A3). */
const syllable = (c: string) => {
  const cp = c.codePointAt(0)!;
  return cp >= 0xac00 && cp <= 0xd7a3;
};
/** Long enough to be found again at word edges: 3 characters, or 2 when one is in a script written without spaces or
 *  both are Hangul syllables (Korean particles may follow them). */
const findable = (v: string) => {
  const chars = Array.from(v);
  return chars.length >= 3 || (chars.length === 2 && (chars.some(noSpace) || chars.every(syllable)));
};

// What a span's edges can hold that is not part of the value (all in the basic plane, so one UTF-16 unit each): white
// space (Unicode White_Space); sentence punctuation (. , ; : ! ? and the ellipsis, ideographic and full-width forms);
// brackets and quotation marks, each with its partner.
const EDGE_SPACE = new Set([
  0x9, 0xa, 0xb, 0xc, 0xd, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);
const EDGE_STOP = new Set([0x2e, 0x2c, 0x3b, 0x3a, 0x21, 0x3f, 0x2026, 0x3001, 0x3002, 0xff01, 0xff0c, 0xff0e, 0xff1a, 0xff1b, 0xff1f]);
const DOT = 0x2e;
const BRACKETS: [number, number][] = [
  [0x28, 0x29], [0x5b, 0x5d], [0x7b, 0x7d], [0x3c, 0x3e], [0x201c, 0x201d], [0x2018, 0x2019], [0xab, 0xbb], [0x300c, 0x300d],
  [0x300e, 0x300f], [0xff08, 0xff09], [0xff3b, 0xff3d], [0xff5b, 0xff5d], [0x3008, 0x3009], [0x300a, 0x300b], [0x3010, 0x3011],
  [0xff1c, 0xff1e], [0x3014, 0x3015],
];
const CLOSER = new Map(BRACKETS); // an opening bracket: its closing one
const OPENER = new Map(BRACKETS.map(([o, c]) => [c, o])); // a closing bracket: its opening one
const EDGE_QUOTE = new Set([0x22, 0x27, 0x60]); // " ' and `, their own partners
const SMALL_B = 0x62;
/** A date as ISO 8601 writes it, followed by "T" and a digit: the start of an ISO timestamp. */
const STAMP = /^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]/;

/** [start, end) of `text`, a span a detector found with `label`, without what its edges hold that is not part of the
 *  value: a model can mark "Kim Minsu." at the end of a sentence, an address with the " (" after it, a name with the
 *  quotation marks around it or a bytes literal's "b'", and the vault would learn that as the value. At either edge
 *  white space goes. Then, except in a SECRET (a password can end with "!" or hold brackets): sentence punctuation (at
 *  the start not a ".", and at the end of an ORG not its "." : "Tiffany & Co."); a bracket whose partner is not in the
 *  span, a bracket that opens at the end or closes at the start; a straight quotation mark that is the only one of its
 *  kind left in the span; a pair of brackets, or the only two of one straight quotation mark, around all the rest; and
 *  the "b'" of a Python bytes literal at the start. A date of birth that goes on into the time of its ISO timestamp
 *  ("1990-01-02T00") is cut at the date, the edge where the vault finds a known date in a timestamp. A bracket whose
 *  partner is inside stays ("(02) 1234-5678"). undefined when nothing but white space and punctuation is left; the span
 *  without its white space when what is left is too short to be found again (`findable`). One pass over the span (the
 *  bracket partners in an array): time in proportion to its length. The same in every package. */
export function valueEdges(text: string, start: number, end: number, label = ""): [number, number] | undefined {
  // the partner of each bracket in the span (one pass: a stack for each kind of bracket), and how many of each straight
  // quotation mark the span holds, kept as its edges go
  const partner = new Int32Array(end - start).fill(-1); // (at i - start; -1: none)
  const open = new Map<number, number[]>();
  const quotes = new Map<number, number>();
  for (let i = start; i < end; i++) {
    const c = text.charCodeAt(i);
    if (CLOSER.has(c)) {
      let stack = open.get(c);
      if (!stack) open.set(c, (stack = []));
      stack.push(i);
    } else if (OPENER.has(c)) {
      const j = open.get(OPENER.get(c)!)?.pop();
      if (j !== undefined) {
        partner[i - start] = j;
        partner[j - start] = i;
      }
    } else if (EDGE_QUOTE.has(c)) quotes.set(c, (quotes.get(c) ?? 0) + 1);
  }
  let a = start;
  let b = end;
  while (a < b && EDGE_SPACE.has(text.charCodeAt(b - 1))) b--;
  while (a < b && EDGE_SPACE.has(text.charCodeAt(a))) a++;
  const [ws, we] = [a, b];
  const secret = label === "SECRET";
  while (a < b && !secret) {
    const last = text.charCodeAt(b - 1);
    if (
      EDGE_SPACE.has(last) ||
      (EDGE_STOP.has(last) && !(last === DOT && label === "ORG")) ||
      CLOSER.has(last) || // a bracket that opens at the end
      (OPENER.has(last) && partner[b - 1 - start] < a) || // one that closes what the span does not open
      (EDGE_QUOTE.has(last) && quotes.get(last) === 1)
    ) {
      if (EDGE_QUOTE.has(last)) quotes.set(last, 0);
      b--;
      continue;
    }
    const first = text.charCodeAt(a);
    if (
      EDGE_SPACE.has(first) ||
      (EDGE_STOP.has(first) && first !== DOT) ||
      OPENER.has(first) || // a bracket that closes at the start
      (CLOSER.has(first) && !(partner[a - start] >= 0 && partner[a - start] < b)) || // one that opens what the span does not close
      (EDGE_QUOTE.has(first) && quotes.get(first) === 1)
    ) {
      if (EDGE_QUOTE.has(first)) quotes.set(first, 0);
      a++;
      continue;
    }
    // a pair around all the rest
    if (b - a >= 2 && ((CLOSER.has(first) && partner[a - start] === b - 1) || (EDGE_QUOTE.has(first) && last === first && quotes.get(first) === 2))) {
      if (EDGE_QUOTE.has(first)) quotes.set(first, 0);
      a++;
      b--;
      continue;
    }
    // the "b'" of a Python bytes literal (not a "b" at the end of a word: "Bob'")
    const q = text.charCodeAt(a + 1);
    if (first === SMALL_B && b - a >= 2 && EDGE_QUOTE.has(q) && !isWord(charBefore(text, a))) {
      quotes.set(q, (quotes.get(q) ?? 1) - 1);
      a += 2;
      continue;
    }
    break;
  }
  if (label === "DATE_OF_BIRTH" && b - a > 11 && STAMP.test(text.slice(a, a + 12))) b = a + 10;
  if (a >= b) return undefined;
  if (a === ws && b === we) return [a, b];
  return findable(text.slice(a, b)) ? [a, b] : [ws, we];
}

/** `spans` of `text` with `valueEdges`, for the vault to learn; a span with nothing left is left out. A block-list span
 *  (its entry is exact) and a known value's span stay as they are. */
export function valueSpans<S extends { start: number; end: number; label?: string; source?: string }>(text: string, spans: readonly S[]): S[] {
  let out: S[] | undefined;
  for (let i = 0; i < spans.length; i++) {
    const s = spans[i];
    const edges =
      s.source === "block" || s.source === "vault" || !(0 <= s.start && s.start < s.end && s.end <= text.length)
        ? ([s.start, s.end] as [number, number])
        : valueEdges(text, s.start, s.end, s.label);
    if (edges && edges[0] === s.start && edges[1] === s.end) {
      out?.push(s);
      continue;
    }
    out ??= spans.slice(0, i);
    if (edges) out.push({ ...s, start: edges[0], end: edges[1] });
  }
  return out ?? (spans as S[]);
}

// What joins the character before it in the vault's reading: a combining mark, a conjoining Hangul vowel or final
const JOINS = /^[\p{M}\u1160-\u11ff\ud7b0-\ud7ff]$/u;
/** What joins the character before it into one grapheme cluster, so that a hit takes it in ("渡辺" and a variation
 *  selector, "김민수" and a combining mark): what joins it in the vault's reading, and the zero width joiner and
 *  non-joiner, the half-width katakana sound marks, emoji modifiers and tag characters. The same set in every package. */
const extendsBefore = (cp: number) =>
  JOINS.test(String.fromCodePoint(cp)) || cp === 0x200c || cp === 0x200d || cp === 0xff9e || cp === 0xff9f || (cp >= 0x1f3fb && cp <= 0x1f3ff) || (cp >= 0xe0020 && cp <= 0xe007f);
/** `end` moved past what joins the character before it (`extendsBefore`). */
const widen = (text: string, end: number): number => {
  while (end < text.length) {
    const cp = text.codePointAt(end)!;
    if (!extendsBefore(cp)) break;
    end += cp > 0xffff ? 2 : 1;
  }
  return end;
};

/** What a value of two Hangul syllables may have right after it in the same word, one piece after another to the end
 *  of the run of syllables: particles, the honorifics 님 and 씨, and the copula. Any other syllable makes a longer word
 *  ("지원금", "정수기"), and a two-syllable name is often a word too. The same list in every package. */
const KO_TAIL = [
  "가", "같이", "고", "과", "까지", "께", "께서", "나", "나마", "네", "는", "니까", "님", "다", "대로", "더러", "도", "든", "든지", "라고",
  "라는", "라도", "라서", "랑", "로", "로서", "로써", "를", "마다", "마저", "만", "만큼", "며", "밖에", "보다", "부터", "씨", "아", "야",
  "어", "었", "에", "에게", "에게서", "에서", "여", "였", "예요", "와", "요", "으로", "으로서", "으로써", "은", "을", "의", "이", "이면",
  "인", "인데", "입니다", "조차", "죠", "지", "지만", "처럼", "한테", "한테서",
];
/** The longest run of syllables `koTail` reads; a longer one is another word. */
const KO_TAIL_MAX = 8;
/** Whether the Hangul syllables from `at` (right after a value of two syllables) are pieces of `KO_TAIL`, one after
 *  another to the end of the run. */
const koTail = (text: string, at: number): boolean => {
  let end = at;
  while (end < text.length && end - at <= KO_TAIL_MAX && syllable(text[end])) end++;
  const run = text.slice(at, end);
  if (run.length > KO_TAIL_MAX) return false;
  const ok: boolean[] = new Array(run.length + 1).fill(false); // ok[i]: run.slice(i) is pieces of the list
  ok[run.length] = true;
  for (let i = run.length - 1; i >= 0; i--) ok[i] = KO_TAIL.some((p) => run.startsWith(p, i) && ok[i + p.length]);
  return ok[0];
};

/** Greek capitals that carry an accent or a breathing, as ranges (first, last, the capital without it): the tonos
 *  capitals, the polytonic ones and those with varia or oxia (the dialytika is kept). From the Unicode decompositions
 *  (U+0300, U+0301, U+0313, U+0314, U+0342, U+0343 left out); the same table in every package. */
const GREEK_BARE: readonly (readonly [number, number, number])[] = [
  [0x386, 0x386, 0x391], [0x388, 0x388, 0x395], [0x389, 0x389, 0x397], [0x38a, 0x38a, 0x399], [0x38c, 0x38c, 0x39f],
  [0x38e, 0x38e, 0x3a5], [0x38f, 0x38f, 0x3a9], [0x3d3, 0x3d3, 0x3d2], [0x1f08, 0x1f0f, 0x391], [0x1f18, 0x1f1d, 0x395],
  [0x1f28, 0x1f2f, 0x397], [0x1f38, 0x1f3f, 0x399], [0x1f48, 0x1f4d, 0x39f], [0x1f59, 0x1f59, 0x3a5], [0x1f5b, 0x1f5b, 0x3a5],
  [0x1f5d, 0x1f5d, 0x3a5], [0x1f5f, 0x1f5f, 0x3a5], [0x1f68, 0x1f6f, 0x3a9], [0x1fba, 0x1fbb, 0x391], [0x1fc8, 0x1fc9, 0x395],
  [0x1fca, 0x1fcb, 0x397], [0x1fda, 0x1fdb, 0x399], [0x1fea, 0x1feb, 0x3a5], [0x1fec, 0x1fec, 0x3a1], [0x1ff8, 0x1ff9, 0x39f],
  [0x1ffa, 0x1ffb, 0x3a9],
];
const GREEK_DROP = new Set([0x300, 0x301, 0x313, 0x314, 0x342, 0x343]);
/** A Greek letter (or a Coptic one of the Greek block). */
const greek = (cp: number) => (cp >= 0x370 && cp <= 0x3ff) || (cp >= 0x1f00 && cp <= 0x1fff);
/** `up` (a value in capitals) as Greek writes capitals: without accents and breathings on Greek letters, the
 *  dialytika kept ("ΝΊΚΟΣ" as "ΝΙΚΟΣ"); undefined when `up` has no Greek letter. */
function greekCapitals(up: string): string | undefined {
  const cps = Array.from(up, (c) => c.codePointAt(0)!);
  if (!cps.some(greek)) return undefined;
  const out: number[] = [];
  let afterGreek = false; // the last letter that is not a combining mark is Greek
  for (let cp of cps) {
    if (cp >= 0x300 && cp <= 0x36f) {
      if (afterGreek) {
        if (GREEK_DROP.has(cp)) continue;
        if (cp === 0x344) cp = 0x308; // (dialytika and tonos: the dialytika stays)
        const last = out[out.length - 1];
        if (cp === 0x308 && (last === 0x399 || last === 0x3a5)) {
          out[out.length - 1] = last === 0x399 ? 0x3aa : 0x3ab;
          continue;
        }
      }
      out.push(cp);
      continue;
    }
    afterGreek = greek(cp);
    out.push(GREEK_BARE.find(([a, b]) => cp >= a && cp <= b)?.[2] ?? cp);
  }
  return out.map((cp) => String.fromCodePoint(cp)).join("");
}
const WIDE = /[\uff01-\uff5e\u3000]/;
const WIDE_ALL = /[\uff01-\uff5e\u3000]/g;
const narrow = (s: string) => s.replace(WIDE_ALL, (c) => (c === "\u3000" ? " " : String.fromCharCode(c.charCodeAt(0) - 0xfee0)));
/** `text` with full-width ASCII (U+FF01 to U+FF5E) as ASCII and U+3000 as a space, as the vault reads it: one character
 *  for one, so a place in it is the same place in `text`. */
export const narrowed = (text: string): string => (WIDE.test(text) ? narrow(text) : text);
/** The text as the vault reads it: full-width ASCII (U+FF01 to U+FF5E) and U+3000 as ASCII, and in NFC (Hangul written
 *  as conjoining jamo composed into syllables, a decomposed accent composed). `map[i]` is where character i of the
 *  reading starts in the text (map[length] is the text's length); no map when the reading is the text. A character of
 *  a part that NFC leaves as it is stands at its own place (one for one: full width is read one character for one); the
 *  characters of a part that NFC composes all start where the part starts (`endIn` gives where a hit that ends inside
 *  them ends). */
export function vaultReading(text: string): { text: string; map?: Int32Array } {
  if (!WIDE.test(text) && text.normalize("NFC") === text) return { text };
  let out = "";
  const map: number[] = [];
  let i = 0;
  const step = (at: number) => (text.codePointAt(at)! > 0xffff ? 2 : 1);
  while (i < text.length) {
    let j = i + step(i);
    while (j < text.length && JOINS.test(String.fromCodePoint(text.codePointAt(j)!))) j += step(j);
    const piece = narrow(text.slice(i, j));
    const read = piece.normalize("NFC");
    // (a part NFC keeps, such as a name with a variation selector or a mark that composes with nothing, keeps its places:
    // a hit that ends before the joining character ends there, not at the start of the part)
    for (let k = 0; k < read.length; k++) map.push(read === piece ? i + k : i);
    out += read;
    i = j;
  }
  map.push(text.length);
  return { text: out, map: Int32Array.from(map) };
}
/** Where a hit that ends before character `i` of a reading (`vaultReading`) ends in the text: where that character
 *  starts, or, when `i` is inside the characters of a part that NFC composed (they all start at one place), the end of
 *  that part, so that a hit covers the whole part (more, never less). The map does not go down, so the end of the part
 *  is found by a binary search (a part can be long: a letter with thousands of marks). */
const endIn = (map: Int32Array, i: number): number => {
  if (i <= 0 || i >= map.length - 1 || map[i] !== map[i - 1]) return map[i];
  let lo = i + 1;
  let hi = map.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (map[mid] > map[i]) hi = mid;
    else lo = mid + 1;
  }
  return map[lo];
};
const asciiLower = (s: string) => s.replace(/[A-Z]+/g, (m) => m.toLowerCase());
/** A value of two or more words: runs of word characters with white space between them. */
const twoWords = (v: string) => v.split(new RegExp(SPACE)).filter((w) => Array.from(w).some(isWord)).length >= 2;
/** How many characters of `v` (from its start) are matched in any ASCII case: all of an e-mail address, a user name and a
 *  value of two or more words; a URL's scheme and host; nothing of other values. */
const caselessPart = (label: string, v: string): number => {
  if (label === "EMAIL" || label === "USERNAME" || twoWords(v)) return v.length;
  if (label !== "URL") return 0;
  const scheme = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.exec(v);
  const host = scheme ? scheme[0].length : 0;
  const stop = v.slice(host).search(/[/?#]/);
  return stop < 0 ? v.length : host + stop;
};
// (letters for the capitals of some languages, built from their code points)
const SMALL_I = "i";
const CAPITAL_I_DOT = String.fromCharCode(0x130); // the capital of "i" in Turkish and Azerbaijani
/** A known value as the vault searches for it. */
interface Known {
  entry: VaultEntry;
  /** The value as the vault reads it (`vaultReading`). */
  read: string;
  /** The characters of `read`, from its start, that match in any ASCII case; `lower` is `read` in ASCII lower case. */
  caseless: number;
  lower: string;
  /** `read` in capitals, in each spelling that is another one (`capitals`: each way a language writes them). */
  uppers: string[];
  /** The spellings of `uppers` that `find` searches for: not one the caseless search finds already (the ASCII capitals
   *  of a value that matches in any ASCII case), each with the first character of it that is not ASCII ("" when it is
   *  all ASCII), which a text holding it must hold (the D4 time bound of the 1.7.1 release: a text without "İ" is not
   *  searched for "EMİNE"). */
  scan: { text: string; need: string }[];
  findable: boolean;
}
/** Whether every character of `s` is ASCII. */
const allAscii = (s: string): boolean => {
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) > 0x7f) return false;
  return true;
};
/** The first character of `s` that is not ASCII, or "" (what a text must hold for `s` to stand in it). */
const firstWide = (s: string): string => {
  for (const c of s) if (c.charCodeAt(0) > 0x7f) return c;
  return "";
};
const SHARP_S = String.fromCharCode(0xdf);
const CAPITAL_SHARP_S = String.fromCharCode(0x1e9e); // a capital that German may write for "ß"
/** The spellings of a value (as the vault reads it) in capitals that `find` also matches, each way a language writes
 *  them, as the vault reads them (composed: the capitals of "ΐ" are three code points, which a text written in capitals
 *  has composed again in the vault's reading): its full Unicode uppercase ("WEISS" for "Weiß", "EMINE ŞAHIN"), for a
 *  value with "i" the Turkish and Azerbaijani capitals with "İ" ("EMİNE ŞAHİN"), for a Greek value the capitals without
 *  accents and breathings ("ΕΛΕΝΗ" for "Ελένη"), and for a value with a sharp s the capitals with the capital sharp s
 *  ("WEIẞ"). Only the spellings other than `read`, each once. */
const capitals = (read: string): string[] => {
  const up = vaultReading(read.toUpperCase()).text;
  const spellings = [up];
  const bare = greekCapitals(up);
  if (bare !== undefined) spellings.push(vaultReading(bare).text);
  if (read.includes(SMALL_I)) spellings.push(vaultReading(read.split(SMALL_I).join(CAPITAL_I_DOT).toUpperCase()).text);
  if (read.includes(SHARP_S)) spellings.push(vaultReading(read.split(SHARP_S).join(CAPITAL_SHARP_S).toUpperCase()).text);
  return spellings.filter((x, i) => x !== read && spellings.indexOf(x) === i);
};
const knownOf = (e: VaultEntry): Known => {
  const read = vaultReading(e.value).text;
  const caseless = caselessPart(e.label, read);
  const lower = caseless > 0 ? asciiLower(read) : "";
  const uppers = capitals(read);
  // (a spelling the caseless search finds at the same place, with the same length, gives the same hit)
  const covered = (u: string) => caseless === read.length && u.length === read.length && asciiLower(u) === lower;
  const scan = uppers.filter((u) => !covered(u)).map((u) => ({ text: u, need: firstWide(u) }));
  return { entry: e, read, caseless, lower, uppers, scan, findable: findable(read) };
};
/** Whether `s` (a reading) is the known value `k` in a spelling the vault matches. */
const sameAs = (k: Known, s: string) =>
  s === k.read ||
  k.uppers.includes(s) ||
  (k.caseless > 0 && s.length === k.read.length && asciiLower(s.slice(0, k.caseless)) === k.lower.slice(0, k.caseless) && s.slice(k.caseless) === k.read.slice(k.caseless));
// White space as Unicode has it, the same in every package's pattern (not the engine's own class), and what ends a line.
const SPACE = "[\\t\\n\\v\\f\\r \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const SPACES = new RegExp(`${SPACE}+`, "g");
const SPACE_AT_END = new RegExp(`^${SPACE}|${SPACE}$`);
const LINE_BREAK = /[\n\r\u0085\u2028\u2029]/;
/** `value` in its lines, each with every run of white space as one space, for finding it where its white space is
 *  written another way ("12 Main St\n  Apt 8\nSeoul" as ["12 Main St", "Apt 8", "Seoul"]). A comma before white space
 *  ends a line too, so a value held as "12 Main St, Seoul" is also found over two lines or without the comma. Nothing for
 *  a value without white space inside it (one that starts or ends with white space is only found as it is written), nor
 *  for one with an empty line (", Tiffany & Co.", "a, , b": only found as it is written; an empty first line would be
 *  found at every place of a text). */
const linesOf = (value: string): string[] => {
  if (value.search(SPACES) < 0 || SPACE_AT_END.test(value)) return [];
  const out: string[] = [];
  let line = "";
  let last = 0;
  for (const m of value.matchAll(SPACES)) {
    const comma = value[m.index - 1] === "," && m.index - 1 >= last;
    line += value.slice(last, comma ? m.index - 1 : m.index);
    if (comma || LINE_BREAK.test(m[0])) {
      out.push(line);
      line = "";
    } else line += " ";
    last = m.index + m[0].length;
  }
  out.push(line + value.slice(last));
  return out.includes("") ? [] : out;
};
/** Whether `plain` (white space as single spaces) is `lines` written out: between two lines stands a space, or a
 *  comma and a space. With `from` and without `whole`: whether it is so from that place on, and where it ends (-1: no). */
/** A value of at most this many UTF-16 units is looked for again after each place where it starts (each step costs at
 *  most 32 units); a longer one is searched for in one pass (`occurrences`). The same in every package. */
const SHORT_NEEDLE = 32;
/** The shortest period of `p`: the smallest q > 0 with p[i] === p[i + q] wherever both are in `p` (Knuth-Morris-Pratt). */
const periodOf = (p: string): number => {
  const fail = new Int32Array(p.length);
  let k = 0;
  for (let j = 1; j < p.length; j++) {
    const c = p.charCodeAt(j);
    while (k > 0 && c !== p.charCodeAt(k)) k = fail[k - 1];
    if (c === p.charCodeAt(k)) k++;
    fail[j] = k;
  }
  return p.length - k;
};
/** Every place where `p` starts in `s`, overlapping ones too ("Ann Ann" in "xAnn Ann Ann"), in time linear in both. A
 *  long value that repeats itself ("ha ha ha" again and again) is not compared again from its start at every place: after
 *  a place where it starts, it starts again one period (q) later where the text goes on with its last q characters, and
 *  nowhere else before it no longer overlaps itself by q or more (two places closer than that would give it a shorter
 *  period); the engine's search goes on from there. `first`: the first place, when the caller has looked for it (a value
 *  that is not in the text costs one `indexOf`, as before 1.7.2). */
const occurrences = (s: string, p: string, first?: number): number[] => {
  const out: number[] = [];
  let i = first ?? (p === "" ? -1 : s.indexOf(p));
  if (i < 0) return out;
  const m = p.length;
  if (m <= SHORT_NEEDLE) {
    for (; i >= 0; i = s.indexOf(p, i + 1)) out.push(i);
    return out;
  }
  const q = periodOf(p);
  const tail = p.slice(m - q);
  const skip = Math.max(q, m - q + 1);
  while (i >= 0) {
    out.push(i);
    while (s.startsWith(tail, i + m)) {
      i += q;
      out.push(i);
    }
    i = s.indexOf(p, i + skip);
  }
  return out;
};
/** The places of `at` (in order) where `p` also starts `offset` units later in `s`: one pass over both lists when `p` is
 *  long, so that checking the part of a value that must be written as it is costs no more than finding it. */
const followedBy = (at: number[], s: string, offset: number, p: string): number[] => {
  if (p.length <= SHORT_NEEDLE) return at.filter((a) => s.startsWith(p, a + offset));
  if (at.length === 0) return at;
  const starts = occurrences(s, p);
  let j = 0;
  return at.filter((a) => {
    while (j < starts.length && starts[j] < a + offset) j++;
    return j < starts.length && starts[j] === a + offset;
  });
};
const linesAt = (plain: string, lines: readonly string[], from: number): number => {
  if (!plain.startsWith(lines[0], from)) return -1;
  let end = from + lines[0].length;
  for (let k = 1; end >= 0 && k < lines.length; k++) {
    const next = plain.startsWith(", ", end) ? end + 2 : plain[end] === " " ? end + 1 : -1;
    end = next >= 0 && plain.startsWith(lines[k], next) ? next + lines[k].length : -1;
  }
  return end;
};
const commaless = (plain: string) => plain.split(", ").join(" ");
/** How many of `sorted` are less than `x`. */
const below = (sorted: readonly number[], x: number): number => {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
};
/** A text as the search for white space written another way reads it (every run of white space as one space), with what
 *  that search needs of it, made once for all values: where each long line of a value starts, and the text with every
 *  ", " written as " ". */
class Hay {
  private readonly starts = new Map<string, Set<number>>();
  private joined?: { text: string; commas: number[] };
  constructor(readonly text: string) {}

  private lineAt(line: string, at: number): boolean {
    if (line.length <= SHORT_NEEDLE) return this.text.startsWith(line, at);
    let starts = this.starts.get(line);
    if (!starts) this.starts.set(line, (starts = new Set(occurrences(this.text, line))));
    return starts.has(at);
  }

  /** [start, end] of every place where `lines` stand one after another in the text, as `linesAt` reads them (between two
   *  lines a space, or a comma and a space), in time linear in the text and the lines. Where no line ends with a comma,
   *  each line stands where the lines joined by " " do in the text with every ", " as " ", unless a space inside a line
   *  was a comma and a space there: only the spaces between lines may be either. A long value that repeats itself is
   *  then found in one pass, and only a place whose part of the text has a comma and a space looks at them. `first`:
   *  where the first line first starts in the text. */
  places(lines: readonly string[], first: number): [number, number][] {
    const text = this.text;
    const out: [number, number][] = [];
    if (lines.length === 1) {
      for (const i of occurrences(text, lines[0], first)) out.push([i, i + lines[0].length]);
      return out;
    }
    if (lines.some((line) => line.endsWith(","))) {
      // (a line that ends with a comma takes the comma of a comma and a space: line by line, as `linesAt`)
      for (const i of occurrences(text, lines[0], first)) {
        let end = i + lines[0].length;
        for (let k = 1; end >= 0 && k < lines.length; k++) {
          const next = text.startsWith(", ", end) ? end + 2 : text[end] === " " ? end + 1 : -1;
          end = next >= 0 && this.lineAt(lines[k], next) ? next + lines[k].length : -1;
        }
        if (end >= 0) out.push([i, end]);
      }
      return out;
    }
    if (!this.joined) {
      const parts = text.split(", ");
      const commas: number[] = []; // where each space that was ", " is in the joined text
      let at = -1;
      for (let k = 0; k < parts.length - 1; k++) commas.push((at += parts[k].length + 1));
      this.joined = { text: parts.join(" "), commas };
    }
    const { text: joined, commas } = this.joined;
    const value = lines.join(" ");
    const between = new Set<number>(); // where the spaces between two lines are in value
    let at = -1;
    for (let k = 0; k < lines.length - 1; k++) between.add((at += lines[k].length + 1));
    const m = value.length;
    for (const j of occurrences(joined, value)) {
      const lo = below(commas, j);
      const hi = below(commas, j + m);
      let ok = true;
      for (let k = lo; ok && k < hi; k++) ok = between.has(commas[k] - j);
      // (the commas left out before a place move it back to the text: lo before its start, hi before its end)
      if (ok) out.push([j + lo, j + m + hi]);
    }
    return out;
  }
}

/** `text` with every run of white space as one space, and where each of its characters starts in `text`. */
const spaceReading = (text: string): { text: string; map?: Int32Array } => {
  let out = "";
  const map = new Int32Array(text.length + 1); // (a reading is never longer than its text)
  let n = 0;
  let from = 0;
  for (const m of text.matchAll(SPACES)) {
    if (m[0] === " ") continue;
    for (let i = from; i <= m.index; i++) map[n++] = i;
    out += `${text.slice(from, m.index)} `;
    from = m.index + m[0].length;
  }
  if (from === 0) return { text };
  for (let i = from; i <= text.length; i++) map[n++] = i;
  return { text: out + text.slice(from), map: map.subarray(0, n) };
};
// (a number as JSON writes one: no zero in front, so "04524", a postal code, is not the number 4524)
const NUMBER_ONLY = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
const NUMBERS = /\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
/** A number as JSON writes one, with a sign in front or without: what an exponent can be. */
const SIGNED_NUMBER = /^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
/** A date as ISO 8601 writes it ("1990-01-02"), which an ISO timestamp starts with ("1990-01-02T00:00:00.000Z"). */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
/** A date in a Python repr or call: "datetime.date(1990, 1, 2)", "datetime.datetime(1990, 1, 2, 9, 30)"; groups: the
 *  year, the month and the day (the name must not end a longer word: not "update(1990, 1, 2)"). */
const DATE_CALL = /(?:date|datetime)\([ \t]*(\d{4}),[ \t]*(\d{1,2}),[ \t]*(\d{1,2})(?=[ \t]*[,)])/g;
const isDigit = (c: string) => c.length === 1 && c >= "0" && c <= "9";

/** Word edges: a value is not found inside a longer word. A letter of a script written without spaces (Han, kana, Thai,
 *  ...) on either side of the boundary is an edge ("注文ZX-88213-Kは", "CEO王伟来了"), but not a mark of such a script
 *  after a value that ends in one (a Thai vowel sign: "สม" is not found in "สมัคร"). Where Hangul
 *  meets other letters or digits is an edge (as in the SDK's whole-word rule: "010-1234-5678입니다"), and Korean particles
 *  may follow a Hangul value ("김민수님께"); Hangul right before a Hangul value is not an edge ("이김민수"). After a
 *  value of two Hangul syllables, more syllables are an edge only as particles, 님, 씨 or the copula (`KO_TAIL`):
 *  "지원님께" and "지원이" are the name, "지원금" is another word. A dot
 *  between two digits joins them, and so does the sign of an exponent before a number, so a number is not found inside
 *  a longer one ("4524" in "4524.7" or in "1e+4524"). With `stamp` (`find` with `variants`): a date followed by "T" and
 *  a digit is at an edge, as an ISO timestamp writes it ("1990-01-02T00:00:00.000Z"). */
function atEdges(text: string, start: number, end: number, v: string, stamp = false): boolean {
  const before = charBefore(text, start);
  const after = charAt(text, end);
  const first = firstChar(v);
  const last = lastChar(v);
  const okBefore =
    before === "" || !isWord(before) || !isWord(first) || noSpace(first) || noSpace(before) || hangul(before) !== hangul(first);
  // (a mark of a script written without spaces belongs to the letter before it: the word goes on, "สม" in "สมัคร";
  // after a value of two Hangul syllables, more syllables are an edge only as particles, 님, 씨 or the copula)
  const markAfter = noSpace(last) && noSpaceMark(after);
  const twoSyllables = after !== "" && syllable(after) && v.length === 2 && syllable(first) && syllable(last);
  const okAfter = twoSyllables
    ? koTail(text, end)
    : after === "" ||
      !isWord(after) ||
      !isWord(last) ||
      (!markAfter && (noSpace(last) || noSpace(after))) ||
      hangul(after) ||
      hangul(last) ||
      (stamp && (after === "T" || after === "t") && isDigit(charAt(text, end + 1)) && ISO_DATE.test(v));
  return okBefore && okAfter && !joinedToNumber(text, start, end, v);
}

/** Whether the value `v` at [start, end) of `text` is part of a longer number there: a dot between two digits joins
 *  them ("4524" in "4524.7" or in "1.4524"), and an exponent belongs to its number ("4524" in "1e+4524", and "-4524" in
 *  "1e-4524"). An exponent is a number: a value that is not one (an address, a phone number with spaces, "04524") is
 *  not joined to "<digit>e+" before it ("Unit+4E+12+Main+St" in a form field). A digit is any number character, as in
 *  a word. */
export function joinedToNumber(text: string, start: number, end: number, v: string): boolean {
  const first = firstChar(v);
  const last = lastChar(v);
  const before = charBefore(text, start);
  const second = charBefore(text, start - before.length);
  const third = charBefore(text, start - before.length - second.length);
  const after = charAt(text, end);
  const sign = (c: string) => c === "+" || c === "-";
  const exponent = (c: string) => c === "e" || c === "E";
  const number = SIGNED_NUMBER.test(v);
  const exponentBefore = number && sign(before) && exponent(second) && isNumber(third);
  const joinedBefore =
    (isNumber(first) && ((before === "." && isNumber(second)) || exponentBefore)) ||
    (number && sign(first) && isNumber(charAt(v, first.length)) && exponent(before) && isNumber(second)); // (the second: the value starts with the sign)
  const joinedAfter = isNumber(last) && after === "." && isNumber(charAt(text, end + after.length));
  return joinedBefore || joinedAfter;
}

// (what the middlewares ask a vault that is not part of its interface: the known value a found text is in another
// spelling, for `tagFound`; how far a known value can reach in a text, for `vaultReach`)
const INTERNAL = new WeakMap<
  Vault,
  { same: (text: string) => VaultEntry | undefined; longest: () => number; learn: (label: string, value: string) => string }
>();
/** The placeholder for a text found in a text (what a model found, or a merged span): that of the known value it is in
 *  another spelling the vault matches (capitals, full-width, NFD: "KIM MINSU" for a known "Kim Minsu"), or its own.
 *  One value keeps one placeholder. A new value shorter than the vault searches for at word edges (3 characters, 2 in
 *  scripts written without spaces and two Hangul syllables) is masked where it was found and in model text without a
 *  record (the loose search), but not as a whole value elsewhere: not as a JSON value, number or key, a tool argument
 *  or a swept string ("0" that a model took for a date once must not turn every 0 of later tool data and tool calls
 *  into a placeholder). Values the application tags (`tag`) are found as a whole value whatever their length. */
export function tagFound(vault: Vault, label: string, value: string): string {
  const inside = INTERNAL.get(vault);
  return inside ? (inside.same(value)?.tag ?? inside.learn(label, value)) : vault.tag(label, value);
}
/** How far a known value of `vault` can reach in a text, for `escapedReadings`: its longest value in its longest
 *  spelling the vault matches (decomposed, in capitals), and room for its white space written another way. undefined
 *  for a vault that is not made by `createVault` (the whole text is read). */
export function vaultReach(vault: Vault): number | undefined {
  const longest = INTERNAL.get(vault)?.longest();
  return longest === undefined ? undefined : longest + 16;
}

/** A plain record (object literal, JSON.parse output or Object.create(null)), whose own string values are walked. */
export const isRecord = (v: unknown): v is Record<string, unknown> => {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return false;
  const p = Object.getPrototypeOf(v);
  return p === Object.prototype || p === null;
};

/** A JSON copy of a replay record (strings as they are). */
const copy = (v: unknown): unknown => (v === null || typeof v !== "object" ? (v === undefined ? null : v) : JSON.parse(JSON.stringify(v)));

const mapStrings = (value: unknown, f: (s: string) => string): unknown => {
  if (typeof value === "string") return f(value);
  if (Array.isArray(value)) return value.map((v) => mapStrings(v, f));
  if (isRecord(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, mapStrings(v, f)]));
  return value;
};

/** `text` with `spans` (sorted, non-overlapping) replaced by the vault's placeholders, and the placeholders it put in.
 *  Placeholder-like text left in the gaps is escaped ("［PERSON_1]"), except the placeholders in `keep` (text a model
 *  wrote with this vault's placeholders): those stay as they are, and a span that covers one is cut around it. */
export function applyVault(
  text: string,
  spans: readonly { start: number; end: number; label: string }[],
  vault: Vault,
  keep?: ReadonlySet<string>,
): { text: string; tags: string[] } {
  const kept: [number, number][] = [];
  if (keep && keep.size > 0) for (const m of text.matchAll(TAG)) if (keep.has(m[0])) kept.push([m.index, m.index + m[0].length]);
  const gap = (t: string) => (t.includes("[") ? t.replace(TAG, (m) => (keep?.has(m) ? m : ESCAPED_BRACKET + m.slice(1))) : t);
  const tags: string[] = [];
  let out = "";
  let last = 0;
  const piece = (start: number, end: number, label: string) => {
    if (start >= end || start < last) return;
    const original = text.slice(start, end);
    out += gap(text.slice(last, start));
    if (original.trim() === "") out += original; // only white space was left between kept placeholders
    else {
      const tag = tagFound(vault, label, original);
      tags.push(tag);
      out += tag;
    }
    last = end;
  };
  let k = 0; // (spans come in order: one pass over the kept placeholders)
  for (const s of spans) {
    let start = s.start;
    while (k < kept.length && kept[k][1] <= start) k++;
    for (let j = k; j < kept.length && kept[j][0] < s.end; j++) {
      piece(start, kept[j][0], s.label);
      start = Math.max(start, kept[j][1]);
    }
    piece(start, s.end, s.label);
  }
  return { text: out + gap(text.slice(last)), tags };
}

/** One token of a JSON text: a string value, a number or a key. `value` is the decoded string, or the number as it
 *  is written; `key` is the key read last (what a value is, for whoever reads the values in context). */
export interface JsonToken {
  kind: "string" | "number" | "key";
  start: number;
  end: number;
  value: string;
  key?: string;
  /** For a key: the object it is a key of (a number per object of the text). */
  scope?: number;
}

const NUMBER = /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y;

/** The strings, numbers and keys of a JSON text, with where each token is; undefined when `text` is not valid JSON. For
 *  changing values inside JSON text while every other token and the spacing stay as written. */
export function jsonTokens(text: string): JsonToken[] | undefined {
  try {
    JSON.parse(text);
  } catch {
    return undefined;
  }
  const out: JsonToken[] = [];
  // the key the next value stands under: its own in an object, the array's in an array; `under` holds the keys of the
  // objects and arrays around it ({"username":[{"note":"box"},"blue"]} reads "username: blue")
  let key: string | undefined;
  const under: (string | undefined)[] = [];
  // which object a key is a key of (objects are numbered as they open), so that two keys of one object can be told
  const inside: number[] = [];
  let objects = 0;
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "{" || c === "[") {
      under.push(key);
      inside.push(c === "{" ? ++objects : 0);
      if (c === "{") key = undefined;
      i++;
    } else if (c === "}" || c === "]") {
      key = under.pop();
      inside.pop();
      i++;
    } else if (c === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      let k = j + 1;
      while (k < text.length && (text[k] === " " || text[k] === "\t" || text[k] === "\n" || text[k] === "\r")) k++;
      const value = JSON.parse(text.slice(i, j + 1)) as string;
      if (text[k] === ":") {
        out.push({ kind: "key", start: i, end: j + 1, value, scope: inside[inside.length - 1] });
        key = value;
      } else out.push({ kind: "string", start: i, end: j + 1, value, key });
      i = j + 1;
    } else if (c === "-" || (c >= "0" && c <= "9")) {
      // outside a string of valid JSON, a digit or a minus sign starts a number
      NUMBER.lastIndex = i;
      const m = NUMBER.exec(text);
      const end = m ? i + m[0].length : i + 1;
      if (m) out.push({ kind: "number", start: i, end, value: m[0], key });
      i = end;
    } else i++;
  }
  return out;
}

/** The string values of a JSON text (not its keys or numbers); undefined when `text` is not valid JSON. */
export function jsonStringValues(text: string): JsonToken[] | undefined {
  return jsonTokens(text)?.filter((t) => t.kind === "string");
}

/** A way to read a text: the reading, and `map[i]`, where character i of it starts in the text (map[length] is the
 *  text's length), so that what is found in the reading can be replaced in the text without cutting an escape. */
export interface Reading {
  text: string;
  map: Int32Array;
  /** For a reading of a part of a text (`escapedReadings` with `reach`): the whole text. `map` then gives places in it,
   *  from the part's start (map[0]) to its end (map[length]); outside the part, the reading is the text as written. */
  base?: string;
  /** For a reading of a part of a text: where its escapes stand in the text, from the first to the end of the last. The
   *  rest of the part is there for what reaches out from them; what is found there alone is found in the text too. */
  core?: [number, number];
}
const HEX4 = /^[0-9a-fA-F]{4}$/;
// (JSON's escapes, and "\'" as JavaScript and Python write a quotation mark in text with single quotes)
const SHORT = new Map([["n", "\n"], ["r", "\r"], ["t", "\t"], ["b", "\b"], ["f", "\f"], ['"', '"'], ["/", "/"], ["'", "'"]]);
/** The escape that the character at `end` starts after a backslash: what it stands for and how many characters it takes. */
const escapeAt = (text: string, end: number, short: boolean): [string, number] | undefined => {
  const c = text[end];
  if (c === "u") return HEX4.test(text.slice(end + 1, end + 5)) ? [String.fromCharCode(parseInt(text.slice(end + 1, end + 5), 16)), 5] : undefined;
  const d = short && c !== undefined ? SHORT.get(c) : undefined;
  return d === undefined ? undefined : [d, 1];
};

const HEX2 = /^[0-9a-fA-F]{2}$/;
const HEX8 = /^[0-9a-fA-F]{8}$/;
/** A Python repr escape that the character at `end` starts after a backslash: "\UNNNNNNNN", and "\xNN", where a run of
 *  them that makes a UTF-8 character is read as that character (a bytes literal: b'Jos\xc3\xa9') and any other as the
 *  code point NN (a str: 'Kim\xa0Minsu'). What it stands for and how many characters it takes. */
const pyEscapeAt = (text: string, end: number): [string, number] | undefined => {
  if (text[end] === "U") {
    const h = text.slice(end + 1, end + 9);
    const code = HEX8.test(h) ? parseInt(h, 16) : -1;
    return code >= 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? [String.fromCodePoint(code), 9] : undefined;
  }
  // (the byte of the "\xNN" whose "x" is at i, with a backslash before it)
  const byteAt = (i: number) => (text[i - 1] === "\\" && text[i] === "x" && HEX2.test(text.slice(i + 1, i + 3)) ? parseInt(text.slice(i + 1, i + 3), 16) : -1);
  const first = byteAt(end);
  if (first < 0) return undefined;
  const more = first < 0x80 ? 0 : first >= 0xc2 && first <= 0xdf ? 1 : first >= 0xe0 && first <= 0xef ? 2 : first >= 0xf0 && first <= 0xf4 ? 3 : -1;
  let code = more > 0 ? first & (0x3f >> more) : -1;
  let stop = end + 3;
  for (let k = 0; code >= 0 && k < more; k++, stop += 4) {
    const next = text[stop] === "\\" && text[stop + 2] !== undefined ? byteAt(stop + 1) : -1;
    code = next >= 0 && (next & 0xc0) === 0x80 ? (code << 6) | (next & 0x3f) : -1;
  }
  const least = [0, 0x80, 0x800, 0x10000][more] ?? 0; // (not a longer way to write a shorter one, not half a pair)
  if (more > 0 && code >= least && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)) return [String.fromCodePoint(code), stop - end];
  return [String.fromCharCode(first), 3];
};

/** A reading of `text` with its escapes replaced wherever they stand: the Unicode ones ("\u00e9"), and with `short`
 *  also "\n", "\t", "\"" and the like. `whole`: an escape takes every backslash before it, however many (JSON inside
 *  a JSON string, at any depth). Otherwise it takes as many as it is written with at its depth (1, 2, 4, ...: the
 *  lowest bit of their number) and the backslashes before those stay as they are written. One pass; a run of
 *  backslashes is looked at once, however long it is. undefined when nothing was replaced. */
function readEscapes(text: string, short: boolean, whole: boolean, py = false): Reading | undefined {
  let out = "";
  const map = new Int32Array(text.length + 1); // (a reading is never longer than its text)
  let n = 0;
  let from = 0; // what is copied as it is starts here
  let i = 0;
  while (i < text.length) {
    if (text[i] !== "\\") {
      map[n++] = i++;
      continue;
    }
    let end = i + 1;
    while (end < text.length && text[end] === "\\") end++;
    const u = escapeAt(text, end, short);
    // (with `py`, a Python repr escape after an odd run: it takes one backslash, and the pairs before it stay)
    const p = u === undefined && py && (end - i) & 1 ? pyEscapeAt(text, end) : undefined;
    const d = u ?? p;
    const stay = d === undefined ? end - i : p ? end - i - 1 : whole ? 0 : end - i - ((end - i) & -(end - i));
    for (let k = 0; k < stay; k++) map[n++] = i + k;
    if (d === undefined) {
      i = end;
      continue;
    }
    out += text.slice(from, i + stay) + d[0];
    for (let k = 0; k < d[0].length; k++) map[n++] = i + stay;
    i = from = end + d[1];
  }
  if (from === 0) return undefined;
  map[n++] = text.length;
  return { text: out + text.slice(from), map: map.subarray(0, n) };
}

/** `text` unescaped once, as a JSON string is read, wherever an escape stands: two backslashes are one, and a
 *  backslash that is left over is read with the character after it ("\n", "\"", "\u00e9"; one that starts no escape
 *  stays). With `py`, as a Python repr is read: also "\xNN" and "\UNNNNNNNN" (`pyEscapeAt`). undefined when nothing
 *  was replaced. */
function unescapeOnce(text: string, py = false): Reading | undefined {
  let out = "";
  const map = new Int32Array(text.length + 1); // (a reading is never longer than its text)
  let n = 0;
  let from = 0;
  let i = 0;
  while (i < text.length) {
    if (text[i] !== "\\") {
      map[n++] = i++;
      continue;
    }
    let end = i + 1;
    while (end < text.length && text[end] === "\\") end++;
    const pairs = (end - i) >> 1;
    const d = (end - i) & 1 ? ((py ? pyEscapeAt(text, end) : undefined) ?? escapeAt(text, end, true)) : undefined;
    if (pairs === 0 && d === undefined) {
      map[n++] = i++; // one backslash that starts no escape
      continue;
    }
    out += text.slice(from, i) + "\\".repeat(pairs);
    for (let k = 0; k < pairs; k++) map[n++] = i + 2 * k;
    if (d !== undefined) {
      out += d[0];
      for (let k = 0; k < d[0].length; k++) map[n++] = end - 1;
      i = from = end + d[1];
    } else i = from = i + 2 * pairs; // (an odd backslash after the pairs is copied with what follows)
  }
  if (from === 0) return undefined;
  map[n++] = text.length;
  return { text: out + text.slice(from), map: map.subarray(0, n) };
}

/** `text` read as part of a URL: "%40" as "@", bytes that make a UTF-8 character as that character ("%EA%B9%80" as
 *  "김"), and with `plus` a "+" as a space (a form field: "name=Kim+Minsu"). Bytes that are not UTF-8 stay as they are
 *  written. undefined when nothing was replaced. */
function percentReading(text: string, plus: boolean): Reading | undefined {
  const hex = (i: number) => {
    const c = text.charCodeAt(i);
    return (c >= 48 && c <= 57) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102);
  };
  const byteAt = (i: number) => (text[i] === "%" && hex(i + 1) && hex(i + 2) ? parseInt(text.slice(i + 1, i + 3), 16) : -1);
  let out = "";
  const map = new Int32Array(text.length + 1); // (a reading is never longer than its text)
  let n = 0;
  let from = 0;
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    let read: string | undefined;
    let end = i + 1;
    if (c === "+" && plus) read = " ";
    else if (c === "%") {
      const first = byteAt(i);
      // one character: its first byte says how many more it takes
      const more = first < 0 ? -1 : first < 0x80 ? 0 : first >= 0xc2 && first <= 0xdf ? 1 : first >= 0xe0 && first <= 0xef ? 2 : first >= 0xf0 && first <= 0xf4 ? 3 : -1;
      let code = more === 0 ? first : more > 0 ? first & (0x3f >> more) : -1;
      end = i + 3;
      for (let k = 0; code >= 0 && k < more; k++, end += 3) {
        const next = byteAt(end);
        code = next >= 0 && (next & 0xc0) === 0x80 ? (code << 6) | (next & 0x3f) : -1;
      }
      const least = [0, 0x80, 0x800, 0x10000][more] ?? 0; // (not a longer way to write a shorter one, not half a pair)
      if (code >= least && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)) read = String.fromCodePoint(code);
    }
    if (read === undefined) {
      map[n++] = i++;
      continue;
    }
    out += text.slice(from, i) + read;
    for (let k = 0; k < read.length; k++) map[n++] = i;
    i = from = end;
  }
  if (from === 0) return undefined;
  map[n++] = text.length;
  return { text: out + text.slice(from), map: map.subarray(0, n) };
}

/** How many steps are put on top of one another (JSON inside a JSON string inside a URL: three). */
const MAX_DEPTH = 4;
/** How far past a run of "\" or "%" a reading can change the text within four steps ("%255CU0001F600": 13). */
const ESCAPE_REACH = 16;
/** Parts of a text that are read together when they stand this close (characters from the first to the last): fewer
 *  and longer parts, so that the work per part does not add up over many short ones. */
const PART_LENGTH = 4096;
const SIGNS = /[\\%+]+/g;
const SIGN = /[\\%+]/;
/** The parts of `text` that a reading can change: each run of "\", "%" and "+", with the 16 characters after a run
 *  that holds a "\" or a "%", merged where they meet. Outside them every reading is the text as written. */
function changedParts(text: string): [number, number][] {
  const out: [number, number][] = [];
  for (const m of text.matchAll(SIGNS)) {
    const end = Math.min(text.length, m.index + m[0].length + (m[0].includes("\\") || m[0].includes("%") ? ESCAPE_REACH : 0));
    const last = out[out.length - 1];
    if (last && m.index <= last[1]) last[1] = Math.max(last[1], end);
    else out.push([m.index, end]);
  }
  return out;
}
/** How many characters of `text` that are not white space no reading changes (every reading, with its white space as
 *  single spaces as a known value's white space is matched, is at least this long). Full-width ASCII is read as ASCII
 *  first, as `escapedReadings` reads it. */
export function unchangedLength(text: string): number {
  const t = WIDE.test(text) ? narrow(text) : text;
  let n = 0;
  let from = 0;
  for (const [a, b] of [...changedParts(t), [t.length, t.length]]) {
    for (let i = from; i < a; i++) if (!isSpaceCode(t.charCodeAt(i))) n++;
    from = b;
  }
  return n;
}

// (the white space of SPACE by code unit, for walking over a run of it without a pattern per character)
const SPACE_ONE = new RegExp(SPACE);
const SPACE_CODES = Uint8Array.from({ length: 0x3001 }, (_, c) => (SPACE_ONE.test(String.fromCharCode(c)) ? 1 : 0));
export const isSpaceCode = (c: number): boolean => c < SPACE_CODES.length && SPACE_CODES[c] === 1;
/** Where `text` is `n` characters on from `at` (with `back`, before it), a run of white space counted as one character,
 *  as a known value's white space is matched however long its run ("Kim" and 45 spaces and "Minsu" is "Kim Minsu"). A
 *  run that `at` stands in counts as one with the rest of it. */
function stepOver(text: string, at: number, n: number, back: boolean): number {
  let i = at;
  for (let k = 0; k < n; k++) {
    if (back ? i <= 0 : i >= text.length) break;
    if (isSpaceCode(text.charCodeAt(back ? i - 1 : i))) {
      if (back) while (i > 0 && isSpaceCode(text.charCodeAt(i - 1))) i--;
      else while (i < text.length && isSpaceCode(text.charCodeAt(i))) i++;
    } else i += back ? -1 : 1;
  }
  return i;
}

// What a number is written with ("4.524e+03"): a digit, a dot, "e" or "E" after a digit, a sign after "e" or "E".
const numberChar = (c: string | undefined) => c !== undefined && ((c >= "0" && c <= "9") || c === "." || c === "e" || c === "E" || c === "+" || c === "-");
const inNumber = (t: string, i: number) => {
  const c = t[i];
  if ((c >= "0" && c <= "9") || c === ".") return true;
  if (c === "e" || c === "E") return isDigit(t[i - 1] ?? "");
  return (c === "+" || c === "-") && (t[i - 1] === "e" || t[i - 1] === "E");
};
/** Where the number that `at` stands inside starts (not before `floor`); `at` when it stands inside none. */
function numberStart(t: string, at: number, floor: number): number {
  if (at <= floor || at >= t.length || !numberChar(t[at - 1]) || !numberChar(t[at])) return at;
  let i = at;
  while (i > floor && inNumber(t, i - 1)) i--;
  return i;
}
/** Where the number that `at` stands inside ends (not after `ceil`); `at` when it stands inside none. */
function numberEnd(t: string, at: number, ceil: number): number {
  if (at <= 0 || at >= ceil || !numberChar(t[at - 1]) || !numberChar(t[at])) return at;
  let i = at;
  while (i < ceil && inNumber(t, i)) i++;
  return i;
}

/** Every reading of `text` within four steps (see `escapedReadings`), nearest first, each once. Full-width ASCII that a
 *  step decodes is read as ASCII before the next step. */
function readingsOf(text: string): Reading[] {
  const steps: ((t: string) => Reading | undefined)[] = [
    (t) => (t.includes("\\") ? readEscapes(t, true, true) : undefined),
    (t) => (t.includes("\\") ? unescapeOnce(t) : undefined),
    (t) => (t.includes("%") ? percentReading(t, false) : undefined),
    (t) => (t.includes("%") || t.includes("+") ? percentReading(t, true) : undefined),
    (t) => (t.includes("\\x") || t.includes("\\U") ? unescapeOnce(t, true) : undefined),
  ];
  const out: Reading[] = [];
  const seen = new Set([text]);
  let level: (Reading | undefined)[] = [undefined]; // (undefined: the text itself)
  for (let depth = 0; depth < MAX_DEPTH && level.length > 0; depth++) {
    const next: Reading[] = [];
    for (const from of level) {
      for (const step of steps) {
        const r = step(from ? from.text : text);
        if (!r) continue;
        // (full-width ASCII that a step decoded is read as ASCII before the next step, as it is in the text: "%EF%BC%85"
        // is "％", read as "%". One character for one, so the places hold)
        const read = WIDE.test(r.text) ? narrow(r.text) : r.text;
        if (seen.has(read)) continue;
        seen.add(read);
        const reading = { text: read, map: from ? r.map.map((at) => from.map[at]) : r.map };
        out.push(reading);
        next.push(reading);
      }
    }
    level = next;
  }
  return out;
}

/** Other ways to read `text`, for finding the vault's known values where they were written with escapes: JSON inside
 *  other text, a log field, JSON inside a JSON string, a URL, a Python repr. Quotation marks are not paired, so a stray
 *  one cannot hide anything. One step is one of: (1) every JSON escape decoded wherever it stands, with any number of
 *  backslashes before it ("\n", "\\n", "\"", "\u00e9"); (2) the text unescaped once as a JSON string is ("DOMAIN\\kim"
 *  as "DOMAIN\kim"); (3) the text as part of a URL ("kim%40example.com"), with "+" as it is (a path); (4) the same with
 *  "+" as a space (a form field: "name=Kim+Minsu"); (5) the text unescaped once as a Python repr is, with "\xNN" and
 *  "\UNNNNNNNN" too, a run of "\xNN" that makes a UTF-8 character read as that character (what LangChain writes with
 *  str() for a tool's bytes, set or dict: b'Jos\xc3\xa9', 'Kim\xa0Minsu'). Steps are put on top of one another, up to
 *  four deep, because encodings nest: JSON in a form field ("%7B%22user%22%3A%22CORP%5C%5Ckim%22%7D"), a form field in
 *  JSON ("name=Kim\u002BMinsu"), JSON inside a JSON string. Every reading within four steps is given, nearest first,
 *  and those that say the same once: escapes elsewhere in the text cannot use up what is read.
 *  `reach`, for searching known values (the longest of them, and some room for its white space written another way):
 *  the work is bounded by reading parts of the text instead of all of it. Each part is a run of escapes, with the runs
 *  that stand within `reach` of it (and those that keep the part within 4,096 characters) and what stands within
 *  `reach` around them, read on its own (`base`, `map` and `core` place it in the text). Distances count a run of white
 *  space as one character, as a known value's white space is matched whatever its run. A value that reaches no further
 *  than `reach` past the escapes it is written with is found in it as in the whole text, and escapes far away from it
 *  make no more readings of it; a text with escapes everywhere is read whole. Full-width ASCII is read as ASCII before
 *  any step ("kim％40example.com"). A reading can be wrong (in "C:\new" there is no line break, in "1+1" no space):
 *  readings are only used to find more to mask, never less. */
export function escapedReadings(text: string, reach?: number): Reading[] {
  // (full-width ASCII is read as ASCII before the escapes are, so that an escape written with full-width characters is
  // read too: "kim％40example.com" as "kim%40example.com". One character stands for one, so a place in the narrowed text
  // is the same place in the text)
  const t = WIDE.test(text) ? narrow(text) : text;
  if (!SIGN.test(t)) return [];
  if (reach === undefined) return readingsOf(t);
  const parts = changedParts(t);
  const out: Reading[] = [];
  let floor = 0; // (where the part before starts: a part reaches no further back)
  for (let i = 0; i < parts.length; ) {
    // (runs of escapes closer than `reach` are read together, as one value can stand in both; so are those that keep
    // the part within PART_LENGTH. Distances count a run of white space as one character, as a known value's white
    // space is matched: "Kim", 45 spaces and "%4Dinsu" is "Kim Minsu" within its reach)
    let j = i;
    while (j + 1 < parts.length && (parts[j + 1][1] - parts[i][0] <= PART_LENGTH || stepOver(t, parts[j][1], reach, false) >= parts[j + 1][0])) j++;
    const core: [number, number] = [parts[i][0], parts[j][1]];
    // (a number is read whole, however many digits it is written with: a part that would start or end inside one takes
    // all of it, "4.52400000000000000000e%2B03" for a known 4524; not past the part before or the next escapes)
    const from = numberStart(t, stepOver(t, core[0], reach, true), floor);
    const to = numberEnd(t, stepOver(t, core[1], reach, false), j + 1 < parts.length ? Math.max(core[1], parts[j + 1][0]) : t.length);
    for (const r of readingsOf(t.slice(from, to))) out.push({ text: r.text, map: from === 0 ? r.map : r.map.map((at) => at + from), base: text, core });
    floor = from;
    i = j + 1;
  }
  return out;
}

/** A JSON number in plain decimal form, digit for digit and without going through a float: "8.21023456789e11" and
 *  "821023456789.0" as "821023456789", "1.25e1" as "12.5", "0e-1" and "-0.0" as "0", "1.50" as "1.5". A number with an
 *  exponent beyond 64, zero too, and anything that is not a number stays as it is written. */
export function plainNumber(written: string): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(written);
  if (!m) return written;
  const exp = Number(m[4] ?? 0);
  if (Math.abs(exp) > 64) return written;
  const all = m[2] + (m[3] ?? "");
  let lead = 0;
  while (lead < all.length && all[lead] === "0") lead++;
  if (lead === all.length) return "0";
  const digits = all.slice(lead);
  const point = digits.length + exp - (m[3]?.length ?? 0); // where the decimal point stands in `digits`
  const whole = point <= 0 ? "0" : point >= digits.length ? digits + "0".repeat(point - digits.length) : digits.slice(0, point);
  const rest = point <= 0 ? "0".repeat(-point) + digits : digits.slice(point);
  let end = rest.length; // (one scan from the end: no zeros after the last other digit)
  while (end > 0 && rest[end - 1] === "0") end--;
  return m[1] + whole + (end === 0 ? "" : `.${rest.slice(0, end)}`);
}

/** `text` read through the Unicode escapes in it: "Jos\u00e9" as "José", wherever the escape stands (a stray quotation
 *  mark must not hide it) and however many times its backslash was escaped again (JSON inside a JSON string); the
 *  backslashes that stand before the escape's own stay ("DOMAIN\\\uae40" keeps "DOMAIN\\"). Python repr escapes too,
 *  as LangChain writes a tool's value with str() ("Kim\xa0Minsu", b'Jos\xc3\xa9' as UTF-8, "\U0001f600"), each
 *  after one backslash. undefined when there is nothing to decode. Other escapes ("\n", "\t") are not touched:
 *  outside a JSON document a backslash is a backslash ("ACME\tkim", a path). */
export function jsonEscapesView(text: string): Reading | undefined {
  const py = text.includes("\\x") || text.includes("\\U");
  return text.includes("\\u") || py ? readEscapes(text, false, false, py) : undefined;
}

/** The placeholder-like parts of `text` and where they stand. */
export function tagRanges(text: string): { start: number; end: number; tag: string }[] {
  return text.includes("[") ? Array.from(text.matchAll(TAG), (m) => ({ start: m.index, end: m.index + m[0].length, tag: m[0] })) : [];
}

/** A part of a text, from `a` to `b` in the text as it is written, and the value it stands for (what it reads as). */
export interface ValueRange {
  a: number;
  b: number;
  label: string;
  value: string;
}

/** `text` with `ranges` of it (in order) replaced by the vault's placeholders for their values, and each of `brackets`
 *  outside the ranges, the opening bracket of placeholder-like text that was already there, by the escaped bracket. A
 *  range that starts inside the one before it is cut to what is left of it. */
export function applyVaultRanges(
  text: string,
  ranges: readonly ValueRange[],
  vault: Vault,
  brackets: readonly (readonly [number, number])[] = [],
): { text: string; tags: string[] } {
  const tags: string[] = [];
  let out = "";
  let pos = 0;
  let m = 0;
  const gap = (to: number) => {
    for (; m < brackets.length && brackets[m][0] < to; m++) {
      if (brackets[m][0] < pos) continue; // inside a range
      out += text.slice(pos, brackets[m][0]) + ESCAPED_BRACKET;
      pos = brackets[m][1];
    }
    if (pos < to) {
      out += text.slice(pos, to);
      pos = to;
    }
  };
  for (const r of ranges) {
    if (r.b <= pos || r.a >= r.b) continue;
    gap(Math.max(pos, r.a));
    const tag = tagFound(vault, r.label, r.value);
    tags.push(tag);
    out += tag;
    pos = Math.max(pos, r.b);
  }
  gap(text.length);
  return { text: out, tags };
}

/** The tokens of `text` (keys, strings, numbers) when it is one JSON object or array; undefined for any other text
 *  ("[PERSON_1] said", a bare string or number, JSON with text around it). */
export function jsonDocument(text: string): JsonToken[] | undefined {
  return /^\s*[{[]/.test(text) ? jsonTokens(text) : undefined;
}

/** `text` with the string values at `tokens` replaced by `values` (same order); a value that did not change keeps its
 *  token as written. */
export function replaceJsonStrings(text: string, tokens: readonly { start: number; end: number; value: string }[], values: readonly string[]): string {
  // (a number or a key that changed is written as a string too)
  let out = "";
  let last = 0;
  tokens.forEach((t, i) => {
    out += text.slice(last, t.start) + (values[i] === t.value ? text.slice(t.start, t.end) : JSON.stringify(values[i]));
    last = t.end;
  });
  return out + text.slice(last);
}

/** JSON text with the vault's placeholders restored inside complete string values only: keys, numbers and everything
 *  else stay as written (a placeholder may be written with escapes, "\\u005bPERSON_1]"). Text that is not valid JSON
 *  (cut off, for one) is returned as it is, still masked. */
export function restoreJson(vault: Vault, text: string, only?: RestoreOptions["only"]): string {
  const tokens = jsonStringValues(text);
  if (!tokens) return text;
  return replaceJsonStrings(text, tokens, tokens.map((t) => vault.restore(t.value, { only })));
}

/** An answer's text with the placeholders restored: a JSON document (structured output) in its string values, so that a
 *  value with a quote or a line break leaves it valid JSON; any other text as it is. */
export function restoreAnswer(vault: Vault, text: string, only?: RestoreOptions["only"]): string {
  // (keys too: an answer is for the user; tool input, `restoreJson`, leaves keys as the model wrote them)
  const tokens = /^\s*[{["]/.test(text) ? jsonTokens(text)?.filter((t) => t.kind !== "number") : undefined;
  return tokens ? replaceJsonStrings(text, tokens, tokens.map((t) => vault.restore(t.value, { only }))) : vault.restore(text, { only });
}

/** For text a model wrote that is a JSON document (an earlier structured answer): the text with every number in it that
 *  holds something to mask written as a JSON string, and `found` (spans of the text) moved with it, so that the
 *  placeholder goes into a string and the answer stays valid JSON (`{"phone": 2025550148}` becomes
 *  `{"phone": "[PHONE_1]"}`). Something to mask: a span of `found`, or a value the vault holds (`find` with `variants`,
 *  and `loose` as given: inside a longer number too). undefined when no number holds any. */
export function quoteNumbers<S extends { start: number; end: number }>(
  text: string,
  found: readonly S[],
  vault: Vault,
  loose: boolean,
): { text: string; found: S[] } | undefined {
  const numbers = /[0-9]/.test(text) ? jsonDocument(text)?.filter((t) => t.kind === "number") : undefined;
  if (!numbers || numbers.length === 0) return undefined;
  // what is to be masked, as parts of the text in order and apart
  const cover: [number, number][] = [];
  for (const h of [...found, ...vault.find(text, { loose, variants: true })].sort((a, b) => a.start - b.start)) {
    const last = cover[cover.length - 1];
    if (last && h.start <= last[1]) last[1] = Math.max(last[1], h.end);
    else cover.push([h.start, h.end]);
  }
  const quotes: number[] = []; // where a quotation mark goes, in the text as it is
  let out = "";
  let last = 0;
  let k = 0;
  for (const t of numbers) {
    while (k < cover.length && cover[k][1] <= t.start) k++;
    if (k >= cover.length || cover[k][0] >= t.end) continue;
    out += `${text.slice(last, t.start)}"${text.slice(t.start, t.end)}"`;
    last = t.end;
    quotes.push(t.start, t.end);
  }
  if (quotes.length === 0) return undefined;
  // how many quotation marks go before a place: at the start of a number, its opening one too; at its end, not its
  // closing one
  const before = (p: number, opening: boolean) => {
    let lo = 0;
    let hi = quotes.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (opening ? quotes[mid] <= p : quotes[mid] < p) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  return { text: out + text.slice(last), found: found.map((s) => ({ ...s, start: s.start + before(s.start, true), end: s.end + before(s.end, false) })) };
}

export function createVault(state: readonly VaultEntry[] | VaultState = []): Vault {
  const byValue = new Map<string, VaultEntry>();
  const byTag = new Map<string, VaultEntry>();
  // for `find` with `variants`: the values that are numbers, by their plain form, and those with white space, in
  // their lines
  const byNumber = new Map<string, VaultEntry>();
  // the values that are ISO dates, for their Python repr ("datetime.date(1990, 1, 2)")
  const byDate = new Map<string, VaultEntry>();
  // (`fold`: the lines are in ASCII lower case and match in any ASCII case; a value has its lines as it is read and,
  // when that is another spelling, its lines in capitals)
  const spaced = new Map<string, { entry: VaultEntry; lines: string[]; fold: boolean; findable: boolean; need: string }[]>();
  // the values with white space by their lines as one line without commas, in ASCII lower case (for `whole`), and when
  // each value was added
  const bySpelling = new Map<string, { entry: VaultEntry; lines: string[]; fold: boolean }[]>();
  // each value as the vault searches for it; by its reading, its capitals and its caseless part (for `same`)
  const known = new Map<string, Known>();
  const byRead = new Map<string, Known>();
  const byUpper = new Map<string, Known>();
  const byLower = new Map<string, Known[]>();
  const age = new Map<string, number>();
  const counters = new Map<string, number>();
  const replays = new Map<string, unknown>();
  // the values a model found that are shorter than the vault searches for (`tagFound`): in `known` for the loose search
  // only, not in the indexes of a whole value and its other spellings
  const local = new Set<string>();
  const REPLAY_MAX = 4096;
  let generation = 0;
  // the length of the longest value in the longest spelling `find` matches (decomposed, in capitals), for `vaultReach`
  let longest = 0;

  /** A value the vault searches for: its spellings indexed. */
  const index = (e: VaultEntry) => {
    if (NUMBER_ONLY.test(e.value) && !byNumber.has(plainNumber(e.value))) byNumber.set(plainNumber(e.value), e);
    if (ISO_DATE.test(e.value)) byDate.set(e.value, e);
    const k = knownOf(e);
    known.set(e.value, k);
    longest = Math.max(longest, e.value.length, e.value.normalize("NFD").length, ...k.uppers.map((u) => Math.max(u.length, u.normalize("NFD").length)));
    if (!byRead.has(k.read)) byRead.set(k.read, k);
    for (const u of k.uppers) if (!byUpper.has(u)) byUpper.set(u, k);
    if (k.caseless > 0) byLower.set(k.lower, [...(byLower.get(k.lower) ?? []), k]);
    const fold = k.caseless === k.read.length;
    const lines = linesOf(k.read).map((l) => (fold ? asciiLower(l) : l));
    if (lines.length > 0) {
      // the value's lines, and its lines in capitals (each way a language writes them) where those are another
      // spelling than the lines match already ("JOSÉ   GARCÍA" for a known "José García": capitals that ASCII lower
      // case does not undo), as written
      const forms = [{ entry: e, lines, fold, findable: k.findable, need: firstWide(lines.join("")) }];
      for (const u of k.uppers) {
        const upper = linesOf(u);
        const another = !fold || upper.length !== lines.length || upper.some((l, i) => asciiLower(l) !== lines[i]);
        if (upper.length > 0 && another) forms.push({ entry: e, lines: upper, fold: false, findable: k.findable, need: firstWide(upper.join("")) });
      }
      spaced.set(e.value, forms);
      for (const f of forms) {
        const key = asciiLower(commaless(f.lines.join(" ")));
        bySpelling.set(key, [...(bySpelling.get(key) ?? []), { entry: e, lines: f.lines, fold: f.fold }]);
      }
    }
  };
  /** `e` held; with `restoreOnly`, a new value is not indexed as a whole value or in its other spellings (`tagFound`). */
  const add = (e: VaultEntry, restoreOnly = false) => {
    const m = /^\[([A-Z][A-Z0-9_]*)_([0-9]+)\]$/.exec(e.tag);
    if (!m || m[1].length > MAX_FAMILY || m[2].length > 12 || typeof e.value !== "string" || e.value === "")
      throw new Error(`[@newmean-ai/pii] not a vault entry: ${JSON.stringify(e.tag)}`);
    if (byTag.has(e.tag)) throw new Error(`[@newmean-ai/pii] duplicate vault tag ${e.tag}`);
    byTag.set(e.tag, e);
    if (!byValue.has(e.value)) {
      byValue.set(e.value, e);
      age.set(e.value, age.size);
      if (restoreOnly) {
        // (searched for only where every known value is, whatever its length: model text without a record)
        local.add(e.value);
        known.set(e.value, knownOf(e));
      } else index(e);
    }
    counters.set(m[1], Math.max(counters.get(m[1]) ?? 0, Number(m[2])));
  };
  const given = Array.isArray(state) ? { entries: state as readonly VaultEntry[], replays: [] } : (state as VaultState);
  const restoreOnly = new Set(Array.isArray((given as VaultState).local) ? (given as VaultState).local : []);
  for (const e of given.entries) add({ tag: e.tag, label: family(e.label), value: e.value }, restoreOnly.has(e.tag));
  // A numbered placeholder the model made up ("[PERSON_2]" when the vault holds only PERSON_1) must not be given to a
  // real value later: its number is skipped from then on (also after the records are loaded again).
  const reserve = (written: unknown) =>
    void mapStrings(written, (t) => {
      for (const m of t.matchAll(TAG)) {
        if (byTag.has(m[0])) continue;
        const cut = m[1].lastIndexOf("_");
        const [fam, n] = [m[1].slice(0, cut), Number(m[1].slice(cut + 1))];
        // (a number far above the ones in use cannot be reached by counting, and must not use the family up)
        if (n <= (counters.get(fam) ?? 0) + RESERVE_AHEAD) counters.set(fam, Math.max(counters.get(fam) ?? 0, n));
      }
      return t;
    });
  for (const [k, v] of given.replays ?? []) {
    if (typeof k !== "string") continue;
    replays.set(k, copy(v));
    reserve(v);
  }
  for (const [fam, n] of Object.entries((given as VaultState).counters ?? {})) {
    if (!/^[A-Z][A-Z0-9_]{0,31}$/.test(fam) || !Number.isInteger(n) || n < 0 || n > MAX_NUMBER) throw new Error(`[@newmean-ai/pii] not a vault state: counter ${JSON.stringify(fam)}`);
    counters.set(fam, Math.max(counters.get(fam) ?? 0, n));
  }

  // the known value that `read` (a text as the vault reads it) is in a spelling `find` matches: the one held longest;
  // also with what joins its last character after it (a mark, a variation selector), as `find` takes that in
  const sameRead = (read: string): VaultEntry | undefined => {
    const found = sameExactly(read);
    if (found) return found;
    let cut = read.length;
    for (let c = charBefore(read, cut); cut > 0 && extendsBefore(c.codePointAt(0)!); c = charBefore(read, cut)) cut -= c.length;
    if (cut === 0 || cut === read.length || !atEdges(read, 0, cut, read.slice(0, cut))) return undefined;
    return sameExactly(read.slice(0, cut));
  };
  const sameExactly = (read: string): VaultEntry | undefined => {
    let best: Known | undefined;
    const take = (k: Known | undefined) => {
      if (k && (!best || age.get(k.entry.value)! < age.get(best.entry.value)!)) best = k;
    };
    take(byRead.get(read));
    take(byUpper.get(read));
    if (byLower.size > 0) for (const k of byLower.get(asciiLower(read)) ?? []) if (sameAs(k, read)) take(k);
    return best?.entry;
  };

  /** A new entry for `value` under `label` (its family); `restoreOnly` as in `add`. */
  const make = (label: string, value: string, restoreOnly: boolean): string => {
    // one "_" per character that is not A-Z, 0-9 or "_" (per code point, as in every package)
    const fam = family(label.toUpperCase()).replace(/[^A-Z0-9_]/gu, "_").slice(0, MAX_FAMILY).replace(/^[^A-Z]+/, "") || "PII";
    // a family whose numbers are used up goes on in the next one, so a value always gets a placeholder that can be undone
    let numbered = fam;
    for (let left = FAMILY_STEPS; left > 0 && (counters.get(numbered) ?? 0) >= MAX_NUMBER; left--) numbered = nextFamily(numbered);
    // every family on the way used up (some 10^13 values, or a state made by hand): masked, not undone
    if ((counters.get(numbered) ?? 0) >= MAX_NUMBER) return `[${fam.replace(/(_[0-9]*)+$/, "")}]`;
    const e = { tag: `[${numbered}_${(counters.get(numbered) ?? 0) + 1}]`, label: fam, value };
    add(e, restoreOnly);
    return e.tag;
  };

  const replaceTags = (text: string, options: RestoreOptions = {}) =>
    text.replace(TAG, (whole) => {
      const { only } = options;
      const v = only instanceof Map ? only.get(whole) : only && !only.has(whole) ? undefined : byTag.get(whole)?.value;
      if (v === undefined) return whole;
      return options.json ? JSON.stringify(v).slice(1, -1) : v;
    });

  const vault: Vault = {
    get size() {
      return byTag.size;
    },
    get generation() {
      return generation;
    },
    tag(label, value) {
      if (value === "") return "";
      const known = byValue.get(value);
      if (known) {
        // (a short value a model found is found as a whole value from now on: the application gives it)
        if (local.delete(value)) index(known);
        return known.tag;
      }
      return make(label, value, false);
    },
    value: (tag) => byTag.get(tag)?.value,
    restore: (text, options) => (byTag.size === 0 && !(options?.only instanceof Map) ? text : replaceTags(text, options)),
    restoreValue: <T>(value: T, options?: Omit<RestoreOptions, "json">) => mapStrings(value, (s) => replaceTags(s, { only: options?.only })) as T,
    bindings(tags) {
      const out = new Map<string, string>();
      for (const t of tags) {
        const v = byTag.get(t)?.value;
        if (v !== undefined) out.set(t, v);
      }
      return out;
    },
    restorer(options) {
      // the values the vault holds now: a streamed text is restored with one set of values from its start to its end
      const given = options?.only;
      const only: ReadonlyMap<string, string> = given instanceof Map ? given : vault.bindings((given as ReadonlySet<string> | undefined) ?? byTag.keys());
      const fixed = { ...options, only };
      let held = "";
      return {
        push(chunk) {
          held += chunk;
          const m = TAG_PREFIX.exec(held.slice(-MAX_TAG));
          const cut = m ? held.length - m[0].length : held.length;
          const out = replaceTags(held.slice(0, cut), fixed);
          held = held.slice(cut);
          return out;
        },
        flush() {
          const out = replaceTags(held, fixed);
          held = "";
          return out;
        },
      };
    },
    find(text, options) {
      const loose = options?.loose === true;
      // (`age`: when the value was added; where two values are found on the same part of a text, one as it is written
      // and one in another spelling, the older one goes first, so that a value keeps its placeholder)
      const hits: { start: number; end: number; label: string; value: string; age?: number }[] = [];
      if (known.size === 0) return [];
      const stamp = options?.variants === true;
      // the text as the vault reads it (full-width ASCII as ASCII, NFC), searched for each value as the vault reads it
      const reading = vaultReading(text);
      const r = reading.text;
      const rmap = reading.map;
      const at = rmap ? (i: number) => rmap[i] : (i: number) => i;
      const atEnd = rmap ? (i: number) => endIn(rmap, i) : (i: number) => i; // (where a hit ends)
      let lower: string | undefined;
      // whether the text holds a character that is not ASCII (a spelling with one stands only in a text that holds it)
      let wide: Map<string, boolean> | undefined;
      const ascii = allAscii(r);
      const holds = (c: string) => {
        if (c === "") return true;
        if (ascii) return false;
        wide ??= new Map();
        let x = wide.get(c);
        if (x === undefined) wide.set(c, (x = r.includes(c)));
        return x;
      };
      for (const k of known.values()) {
        if (!loose && !k.findable) continue;
        const e = k.entry;
        // (a hit takes in what joins its last character, so that it never ends inside a grapheme cluster)
        const take = (i: number, length: number) => {
          if (loose || atEdges(r, i, i + length, r.slice(i, i + length), stamp))
            hits.push({ start: at(i), end: atEnd(widen(r, i + length)), label: e.label, value: e.value, age: age.get(e.value) });
        };
        if (k.caseless > 0) {
          // the caseless part in any ASCII case, the rest as it is written
          lower ??= asciiLower(r);
          const first = lower.indexOf(k.lower);
          if (first >= 0) for (const i of followedBy(occurrences(lower, k.lower, first), r, k.caseless, k.read.slice(k.caseless))) take(i, k.read.length);
        } else {
          const first = r.indexOf(k.read);
          if (first >= 0) for (const i of occurrences(r, k.read, first)) take(i, k.read.length);
        }
        // (the spellings in capitals the caseless search does not find already, each only in a text that holds its first
        // character that is not ASCII)
        for (const { text: u, need } of k.scan) {
          if (need !== "" && (ascii || !holds(need))) continue;
          const first = r.indexOf(u);
          if (first >= 0) for (const i of occurrences(r, u, first)) take(i, u.length);
        }
      }
      if (options?.variants && byDate.size > 0) {
        // a date in a Python repr or call ("datetime.date(1990, 1, 2)", which LangChain writes for a tool's date with
        // str()): its year, month and day are that date
        for (const m of r.matchAll(DATE_CALL)) {
          if (isWord(charBefore(r, m.index))) continue;
          const e = byDate.get(`${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`);
          if (!e) continue;
          const start = m.index + m[0].indexOf(m[1], m[0].indexOf("("));
          hits.push({ start: at(start), end: atEnd(widen(r, m.index + m[0].length)), label: e.label, value: e.value, age: age.get(e.value) });
        }
      }
      if (options?.variants && spaced.size > 0) {
        // white space written another way: the text with every run of white space as one space, and each value line
        // by line; between two lines of a value stands white space, or a comma and white space ("12 Main St, Seoul")
        const plain = spaceReading(r);
        const pmap = plain.map;
        const pat = pmap ? (i: number) => at(pmap[i]) : at;
        const patEnd = pmap ? (i: number) => atEnd(pmap[i]) : atEnd;
        let plainLower: string | undefined;
        const hays = new Map<boolean, Hay>();
        for (const { entry: e, lines, fold, findable: f, need } of [...spaced.values()].flat()) {
          if ((!loose && !f) || (need !== "" && (ascii || !holds(need)))) continue;
          const hayText = fold ? (plainLower ??= asciiLower(plain.text)) : plain.text;
          // (a value whose first line is not in the text costs one `indexOf`, as before 1.7.2)
          const first = hayText.indexOf(lines[0]);
          if (first < 0) continue;
          let hay = hays.get(fold);
          if (!hay) hays.set(fold, (hay = new Hay(hayText)));
          // (where the value is written as it is, the search above has it: for a long value, every such place at once)
          let written: Set<number> | undefined;
          const asWritten = (start: number, stop: number) =>
            stop - start === e.value.length &&
            (e.value.length <= SHORT_NEEDLE ? text.startsWith(e.value, start) : (written ??= new Set(occurrences(text, e.value))).has(start));
          for (const [i, end] of hay.places(lines, first)) {
            const start = pat(i);
            if (!asWritten(start, patEnd(end)) && (loose || atEdges(plain.text, i, end, plain.text.slice(i, end))))
              hits.push({ start, end: patEnd(widen(plain.text, end)), label: e.label, value: e.value, age: age.get(e.value) });
          }
        }
      }
      if (options?.variants && byNumber.size > 0) {
        // a number in another spelling: every number in the text, in its plain form
        for (const m of r.matchAll(NUMBERS)) {
          let start = m.index;
          const end = start + m[0].length;
          // a minus sign belongs to the number unless it joins it to what stands before ("010-1234")
          if (r[start - 1] === "-" && !isWord(charBefore(r, start - 1))) start--;
          const written = r.slice(start, end);
          if (!NUMBER_ONLY.test(written)) continue;
          const plain = plainNumber(written);
          let e = byNumber.get(plain);
          // one rule for a sign: a number whose plain form without its minus sign is a known value is that value,
          // and the sign stays outside ("-4.524e3" and "-4524" for a known 4524 both become "-[ID_NUMBER_1]")
          if (!e && plain.startsWith("-")) {
            e = byNumber.get(plain.slice(1));
            start++;
          }
          if (!e || r.slice(start, end) === e.value) continue;
          // (where a number stands on its own, by the edges of every other value: not "café4.524e3", not "4.524e3.1")
          if (loose || (plain.replace("-", "").length >= 3 && atEdges(r, start, end, r.slice(start, end))))
            hits.push({ start: at(start), end: atEnd(widen(r, end)), label: e.label, value: e.value, age: age.get(e.value) });
        }
      }
      hits.sort((a, b) => a.start - b.start || b.end - a.end || (a.age ?? 0) - (b.age ?? 0));
      const out: { start: number; end: number; label: string; value: string }[] = [];
      const merged = new Set<number>(); // (the value of a merged hit is cut once, when all of it is known)
      for (const { age: _age, ...h } of hits) {
        const prev = out[out.length - 1];
        if (prev && h.start < prev.end) {
          if (h.end > prev.end) {
            prev.end = h.end;
            merged.add(out.length - 1);
          }
        } else out.push(h);
      }
      for (const i of merged) out[i].value = text.slice(out[i].start, out[i].end);
      return out;
    },
    whole(text) {
      // as it is written, as a number, with its white space written another way, and each of these in the other
      // readings of the text ("Kim+Minsu" in a form field, "CORP\\kim" in JSON), each as the vault reads it (capitals,
      // full-width, NFD): the oldest of them, so that a value keeps its first placeholder
      let oldest: VaultEntry | undefined;
      // (other readings only when one can be as short as a known value: what no reading changes is in every one)
      const signs = SIGN.test(text) || (WIDE.test(text) && SIGN.test(narrow(text))); // (an escape, also in full width)
      const readings = signs && unchangedLength(text) <= longest + 16 ? escapedReadings(text) : [];
      for (const written of [text, ...readings.map((r) => r.text)]) {
        const read = vaultReading(written).text;
        const found = [sameRead(read), NUMBER_ONLY.test(read) ? byNumber.get(plainNumber(read)) : undefined];
        if (bySpelling.size > 0 && read.search(SPACES) >= 0) {
          const plain = spaceReading(read).text;
          const low = asciiLower(plain);
          found.push(bySpelling.get(commaless(low))?.find((x) => linesAt(x.fold ? low : plain, x.lines, 0) === plain.length)?.entry);
        }
        for (const e of found) if (e && (!oldest || age.get(e.value)! < age.get(oldest.value)!)) oldest = e;
      }
      return oldest;
    },
    age: (value) => age.get(value),
    hide(text, options) {
      const hits = byValue.size === 0 ? [] : vault.find(text, { loose: options?.loose });
      if (options?.escape) return applyVault(text, hits, vault, options.keep).text;
      let out = "";
      let last = 0;
      for (const h of hits) {
        out += text.slice(last, h.start) + (byValue.get(h.value)?.tag ?? vault.tag(h.label, h.value));
        last = h.end;
      }
      return out + text.slice(last);
    },
    remember(key, masked, expected) {
      if (expected !== undefined && expected !== generation) return false;
      // a copy, so that later changes to the caller's object cannot change what the model is taken to have written
      let record = copy(masked);
      // two different wordings for one key ("x[ID_1]y" and a literal "x42y" both give "x42y"): neither can be trusted
      // for that key, so it is kept as "no record" (null) and the text is searched instead
      const old = replays.get(key);
      if (old !== undefined && (old === null || JSON.stringify(old) !== JSON.stringify(record))) record = null;
      reserve(masked);
      replays.delete(key);
      replays.set(key, record);
      if (replays.size > REPLAY_MAX) {
        // the oldest record goes; a key marked as untrustworthy (null) stays while there is anything else to drop
        let oldest: string | undefined;
        for (const [k, v] of replays) {
          if (v !== null) {
            oldest = k;
            break;
          }
        }
        replays.delete(oldest ?? (replays.keys().next().value as string));
      }
      return true;
    },
    recall(key) {
      const kept = replays.get(key);
      if (kept === undefined) return undefined;
      replays.delete(key); // in use: the last to be dropped
      replays.set(key, kept);
      return kept === null ? undefined : copy(kept);
    },
    // (a string that is a known value as a whole, in a spelling `find` matches: "AL" for a known "Al", whatever its length)
    hideValue: <T>(value: T) => (byValue.size === 0 ? value : (mapStrings(value, (s) => sameRead(vaultReading(s).text)?.tag ?? vault.hide(s)) as T)),
    entries: () => [...byTag.values()].map((e) => ({ ...e })),
    toJSON: () => ({
      entries: [...byTag.values()].map((e) => ({ ...e })),
      replays: [...replays].map(([k, v]): [string, unknown] => [k, copy(v)]),
      counters: Object.fromEntries(counters),
      ...(local.size > 0 ? { local: [...local].map((v) => byValue.get(v)!.tag) } : {}),
    }),
    clear() {
      byValue.clear();
      byNumber.clear();
      byDate.clear();
      spaced.clear();
      bySpelling.clear();
      known.clear();
      byRead.clear();
      byUpper.clear();
      byLower.clear();
      age.clear();
      byTag.clear();
      counters.clear();
      replays.clear();
      local.clear();
      longest = 0;
      generation++;
    },
  };
  INTERNAL.set(vault, {
    same: (text) => (known.size === 0 ? undefined : sameRead(vaultReading(text).text)),
    longest: () => longest,
    learn: (label, value) => (value === "" ? "" : (byValue.get(value)?.tag ?? make(label, value, !findable(vaultReading(value).text)))),
  });
  return vault;
}
