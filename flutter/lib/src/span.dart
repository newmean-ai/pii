/// A region to mask: [start, end) in UTF-16 code units, the indices of Dart's `String` (`text.substring(start, end)`).
final class PiiSpan {
  final int start, end;

  /// "PERSON", "EMAIL", ... (see [piiLabels]).
  final String label;

  /// "model", "regex", or "model+regex" when both found it.
  final String source;

  const PiiSpan(this.start, this.end, this.label, this.source);

  @override
  bool operator ==(Object other) =>
      other is PiiSpan && other.start == start && other.end == end && other.label == label && other.source == source;

  @override
  int get hashCode => Object.hash(start, end, label, source);

  @override
  String toString() => 'PiiSpan($start, $end, $label, $source)';
}

/// Labels of the v1 models. Level 2 = indirect identifiers: PERSON_L2 (a surname with a title, a nickname, initials) and
/// URL_L2 (public profile or page URLs).
const List<String> piiLabels = [
  'PERSON', 'PERSON_L2', 'EMAIL', 'PHONE', 'ADDRESS', 'ID_NUMBER', 'FINANCIAL', 'ORG', 'USERNAME', 'URL', 'URL_L2', //
  'DATE_OF_BIRTH', 'SECRET',
];

/// All labels but the level 2 ones (PERSON_L2, URL_L2): pass as `labels` to leave indirect identifiers visible.
const List<String> level1Labels = [
  'PERSON',
  'EMAIL',
  'PHONE',
  'ADDRESS',
  'ID_NUMBER',
  'FINANCIAL',
  'ORG',
  'USERNAME',
  'URL',
  'DATE_OF_BIRTH',
  'SECRET',
];

/// PERSON_L2 → PERSON, URL_L2 → URL.
String labelFamily(String label) => label.endsWith('_L2') ? label.substring(0, label.length - 3) : label;
