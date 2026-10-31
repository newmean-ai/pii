import 'dart:io';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

void main() {
  final file = '$fixtures/regex.jsonl';
  test('regex parity with the Python baseline (regex.jsonl)', () {
    final rows = readJsonl(file);
    final bad = <String>[];
    for (final row in rows) {
      final text = row['text'] as String;
      final u = codePointToUtf16(text);
      final want = ([
        for (final s in (row['spans'] as List).cast<Map<String, dynamic>>())
          '${u[s['start'] as int]}-${u[s['end'] as int]}:${s['label']}',
      ]..sort()).join(' ');
      final got = ([for (final s in regexSpans(text)) '${s.start}-${s.end}:${s.label}']..sort()).join(' ');
      if (want != got) bad.add('${text.length > 80 ? text.substring(0, 80) : text}\n  py $want\n  dart $got');
    }
    print('regex: ${rows.length - bad.length}/${rows.length} rows identical');
    if (bad.isNotEmpty) print(bad.take(5).join('\n'));
    expect(bad, isEmpty);
  }, skip: File(file).existsSync() ? false : 'no fixtures at $fixtures');

  // the account-number rule (FINANCIAL) runs before PHONE and also takes 010-1234-5678; next to the model, the model's
  // label wins for such overlaps
  test('regex examples (as js/test/regex.test.ts)', () {
    const t = '메일 kim.minjun@example.co.kr 전화 010-1234-5678 카드 4111 1111 1111 1111 주민 900101-1234567';
    expect(
      [for (final s in regexSpans(t)) (t.substring(s.start, s.end), s.label)],
      [
        ('kim.minjun@example.co.kr', 'EMAIL'),
        ('900101-1234567', 'ID_NUMBER'),
        ('010-1234-5678', 'FINANCIAL'),
        ('4111 1111 1111 1111', 'FINANCIAL'),
      ],
    );
  });

  // expected values are the npm package's regexSpans output on the same strings
  test('regex offsets are UTF-16 indices after emoji and astral characters', () {
    const t = '👩‍👩‍👧 𠮷 🇰🇷 mail kim@example.com url https://a.example/😀x tel +82 10-1234-5678';
    expect(
      [for (final s in regexSpans(t)) (t.substring(s.start, s.end), s.label, s.start, s.end)],
      [
        ('kim@example.com', 'EMAIL', 22, 37),
        ('https://a.example/😀x', 'URL', 42, 63),
        ('10-1234-5678', 'FINANCIAL', 72, 84),
      ],
    );
  });

  test('Unicode digits and word boundaries as in the npm package', () {
    List<(String, int, int)> f(String t) => [for (final s in regexSpans(t)) (s.label, s.start, s.end)];
    expect(f('٠١٠-١٢٣٤-٥٦٧٨'), [('FINANCIAL', 0, 13)]); // Arabic-Indic digits
    expect(f('x9001011234567'), [('PHONE', 2, 14)]); // no boundary before the ID number; PHONE has none at its start
    expect(f('가900101-1234567'), [('PHONE', 3, 15)]); // Hangul is a word character
  });
}
