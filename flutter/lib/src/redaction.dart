// Redaction modes of js/src/redactor.ts applyRedaction.
import 'span.dart';

/// [tag] → "[EMAIL]" (level 2 labels as their family: "[PERSON]", "[URL]"), [mask] → one mask character per code point,
/// [blank] → removed.
enum RedactMode { tag, mask, blank }

/// Text with [spans] (sorted, non-overlapping, as `detect` returns them) replaced. [replace] overrides [mode].
String applyRedaction(
  String text,
  List<PiiSpan> spans, {
  RedactMode mode = RedactMode.tag,
  String maskChar = '*',
  String Function(PiiSpan span, String original)? replace,
}) {
  final out = StringBuffer();
  var last = 0;
  for (final s in spans) {
    final orig = text.substring(s.start, s.end);
    out.write(text.substring(last, s.start));
    out.write(
      replace != null
          ? replace(s, orig)
          : switch (mode) {
              RedactMode.tag => '[${labelFamily(s.label)}]',
              RedactMode.mask => maskChar * orig.runes.length,
              RedactMode.blank => '',
            },
    );
    last = s.end;
  }
  out.write(text.substring(last));
  return out.toString();
}

/// The result of `redact`: the redacted text and the spans that were replaced (offsets into the original text).
final class PiiRedaction {
  final String text;
  final List<PiiSpan> spans;
  const PiiRedaction(this.text, this.spans);

  @override
  String toString() => 'PiiRedaction($text, $spans)';
}
