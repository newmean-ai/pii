// Emoji are taken out of the text the model reads and never masked (1.2.0). Same rule as the C core
// (native/core/src/api.c, model_spans_no_emoji); parity is checked span for span. Positions are UTF-16 code units.
import { PICTOGRAPHIC } from "./pictographic.js";
import type { Span } from "./types.js";

const JS_SPACE = /\s/;

/** Extended_Pictographic from the C core's table (not the engine's \p{...}, whose Unicode version varies). */
function isPictographic(c: number): boolean {
  let lo = 0;
  let hi = PICTOGRAPHIC.length / 2 - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (c < PICTOGRAPHIC[2 * mid]) hi = mid - 1;
    else if (c > PICTOGRAPHIC[2 * mid + 1]) lo = mid + 1;
    else return true;
  }
  return false;
}

function isBase(c: number): boolean {
  return (c >= 0x1f1e6 && c <= 0x1f1ff) || isPictographic(c);
}

function isKeycapBase(c: number): boolean {
  return (c >= 0x30 && c <= 0x39) || c === 0x23 || c === 0x2a;
}

function isKeycapMark(c: number): boolean {
  return c === 0xfe0f || c === 0x20e3;
}

function isPart(c: number): boolean {
  return (
    isBase(c) || c === 0x200d || c === 0xfe0e || c === 0xfe0f || c === 0x20e3 || (c >= 0x1f3fb && c <= 0x1f3ff) || (c >= 0xe0020 && c <= 0xe007f)
  );
}

/** An emoji run is an Extended_Pictographic or Regional_Indicator code point and the emoji code points after it
 *  (pictographs, regional indicators, ZWJ, variation selectors, skin tones, tag characters, the keycap mark). A keycap (a
 *  digit, "#" or "*", an optional U+FE0F, U+20E3) is read as its bare character: its marks are taken out. Returns the text
 *  without runs and keycap marks and, per remaining code unit, its index in `text`; null when nothing was taken out. */
export function stripEmoji(text: string): { text: string; keep: number[] } | null {
  const keep: number[] = [];
  let out = "";
  let removed = false;
  for (let i = 0; i < text.length; ) {
    const c = text.codePointAt(i)!;
    const w = c > 0xffff ? 2 : 1;
    if (isBase(c)) {
      removed = true;
      i += w;
      while (i < text.length) {
        const d = text.codePointAt(i)!;
        if (!isPart(d)) break;
        i += d > 0xffff ? 2 : 1;
      }
      continue;
    }
    let j = i + 1;
    if (isKeycapBase(c) && text.charCodeAt(j) === 0xfe0f) j++;
    if (isKeycapBase(c) && text.charCodeAt(j) === 0x20e3) {
      removed = true;
      keep.push(i);
      out += text[i];
      i = j + 1;
      continue;
    }
    // a lone high surrogate before a cut must not pair up with a lone low surrogate after it (the C core, which reads
    // UTF-8, sees each as U+FFFD): it is passed on as U+FFFD, one unit, so offsets stay put
    const prev = out.charCodeAt(out.length - 1);
    if (w === 1 && c >= 0xdc00 && c <= 0xdfff && prev >= 0xd800 && prev <= 0xdbff && keep[keep.length - 1] !== i - 1)
      out = out.slice(0, -1) + "\ufffd";
    for (let k = 0; k < w; k++) keep.push(i + k);
    out += text.slice(i, i + w);
    i += w;
  }
  return removed ? { text: out, keep } : null;
}

function marksOnly(text: string, a: number, b: number): boolean {
  for (let i = a; i < b; i++) if (!isKeycapMark(text.charCodeAt(i))) return false;
  return true;
}

/** Spans of the stripped text back in `text`'s offsets, split where a run fell inside one (keycap marks never split: a span
 *  over a keycap's character covers its marks too); at a split the pieces lose the white space next to the run, and a piece
 *  left empty is dropped. */
export function restoreSpans(text: string, spans: Span[], keep: readonly number[]): Span[] {
  const out: Span[] = [];
  for (const s of spans) {
    for (let k = s.start; k < s.end; ) {
      let e = k + 1;
      while (e < s.end && marksOnly(text, keep[e - 1] + 1, keep[e])) e++;
      let a = keep[k];
      let b = keep[e - 1] + 1;
      const next = e < keep.length ? keep[e] : text.length; // keycap marks after the piece's last character belong to it
      while (b < next && isKeycapMark(text.charCodeAt(b))) b++;
      if (k > s.start) while (a < b && JS_SPACE.test(text[a])) a++;
      if (e < s.end) while (b > a && JS_SPACE.test(text[b - 1])) b--;
      if (a < b) out.push({ ...s, start: a, end: b });
      k = e;
    }
  }
  return out;
}
