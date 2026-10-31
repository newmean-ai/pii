// Deterministic patterns that run next to the model when `regex: true`: a port of js/src/regex.ts (same patterns as the
// Python baseline, with Python's Unicode \w, \d and \b). Dart's RegExp with `unicode: true` follows JavaScript's.
import 'span.dart';

const _w = r'[\p{L}\p{N}_]';
const _d = r'\p{Nd}';
const _b0 = '(?<!$_w)'; // \b before a digit
const _b1 = '(?!$_w)'; // \b after a digit

RegExp _re(String pattern) => RegExp(pattern, unicode: true);

/// The rules in order; a match that overlaps an earlier rule's match is skipped.
final List<(String, RegExp)> regexRules = [
  ('EMAIL', _re(r'[\p{L}\p{N}_.+-]+@[\p{L}\p{N}_-]+(?:\.[\p{L}\p{N}_-]+)+')),
  ('URL', _re(r'https?://\S+|www\.\S+')),
  ('ID_NUMBER', _re('$_b0$_d{6}[-\\s]?[1-8]$_d{6}$_b1')),
  ('FINANCIAL', _re('$_b0(?:$_d{4}[-\\s]?){3}$_d{4}$_b1|$_b0$_d{2,6}-$_d{2,6}-$_d{2,8}(?:-$_d{1,3})?$_b1')),
  ('PHONE', _re('(?:\\+$_d{1,3}[\\s-]?)?\\(?$_d{2,4}\\)?[\\s.-]?$_d{3,4}[\\s.-]?$_d{4}$_b1')),
];

/// Pattern matches in [text], with UTF-16 offsets (source "regex"), in rule order.
List<PiiSpan> regexSpans(String text) {
  final out = <PiiSpan>[];
  for (final (label, re) in regexRules) {
    for (final m in re.allMatches(text)) {
      if (out.any((s) => m.start < s.end && s.start < m.end)) continue;
      out.add(PiiSpan(m.start, m.end, label, 'regex'));
    }
  }
  return out;
}
