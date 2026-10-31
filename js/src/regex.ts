// Deterministic patterns that run next to the model (the "model + regex" setup evaluated in the technical report).
// Same patterns as the Python baseline (training/scripts/evaluate.py REGEX), with Python's Unicode \w, \d and \b.
import type { Span } from "./types.js";

const W = String.raw`[\p{L}\p{N}_]`;
const D = String.raw`\p{Nd}`;
const B0 = String.raw`(?<!${W})`; // \b before a digit
const B1 = String.raw`(?!${W})`; // \b after a digit

export const REGEX_RULES: [label: string, pattern: RegExp][] = [
  ["EMAIL", new RegExp(String.raw`[\p{L}\p{N}_.+-]+@[\p{L}\p{N}_-]+(?:\.[\p{L}\p{N}_-]+)+`, "gu")],
  ["URL", new RegExp(String.raw`https?://\S+|www\.\S+`, "gu")],
  ["ID_NUMBER", new RegExp(String.raw`${B0}${D}{6}[-\s]?[1-8]${D}{6}${B1}`, "gu")],
  ["FINANCIAL", new RegExp(String.raw`${B0}(?:${D}{4}[-\s]?){3}${D}{4}${B1}|${B0}${D}{2,6}-${D}{2,6}-${D}{2,8}(?:-${D}{1,3})?${B1}`, "gu")],
  ["PHONE", new RegExp(String.raw`(?:\+${D}{1,3}[\s-]?)?\(?${D}{2,4}\)?[\s.-]?${D}{3,4}[\s.-]?${D}{4}${B1}`, "gu")],
];

/** Pattern matches; a match that overlaps an earlier rule's match is skipped. */
export function regexSpans(text: string): Span[] {
  const out: Span[] = [];
  for (const [label, re] of REGEX_RULES) {
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      const start = m.index!;
      const end = start + m[0].length;
      if (out.some((s) => start < s.end && s.start < end)) continue;
      out.push({ start, end, label, source: "regex" });
    }
  }
  return out;
}
