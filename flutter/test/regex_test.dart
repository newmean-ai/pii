import 'dart:io';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'growth.dart';
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

  // The matcher of 1.6.0 and earlier, kept as the reference: regexSpans must give its spans, in its order. It tried the
  // EMAIL pattern again from every character of a run without "@" and compared each match with all earlier spans, so its
  // time grew with the square of the input (fixed in 1.6.1).
  List<String> legacy(String text) {
    final out = <(int, int, String)>[];
    for (final (label, re) in regexRules) {
      for (final m in re.allMatches(text)) {
        if (out.any((s) => m.start < s.$2 && s.$1 < m.end)) continue;
        out.add((m.start, m.end, label));
      }
    }
    return [for (final s in out) '${s.$1}-${s.$2}:${s.$3}'];
  }

  List<String> current(String text) => [for (final s in regexSpans(text)) '${s.start}-${s.end}:${s.label}'];

  test('regexSpans equals the 1.6.0 matcher on every string of up to 7 characters over a 6-character alphabet', () {
    const alphabet = ['a', '@', '.', '-', '+', ' '];
    var texts = [''];
    var checked = 0;
    final bad = <String>[];
    for (var length = 0; length <= 7; length++) {
      for (final t in texts) {
        if (current(t).join(' ') != legacy(t).join(' ')) {
          bad.add(t);
        }
      }
      checked += texts.length;
      if (length < 7) {
        texts = [
          for (final t in texts)
            for (final c in alphabet) t + c,
        ];
      }
    }
    expect(bad.take(5).toList(), isEmpty);
    expect(checked, 335923);
  });

  test('regexSpans equals the 1.6.0 matcher on 40,000 random Unicode strings', () {
    final alphabet = [
      'a',
      'Z',
      '7',
      '@',
      '@',
      '.',
      '.',
      '+',
      '-',
      '_',
      ' ',
      '\n',
      '\u00a0',
      '한',
      'é',
      '\u{10400}',
      '\u{1D7D9}',
      '①',
      '\u0301',
      '\u{1F600}',
      String.fromCharCode(0xD800),
      String.fromCharCode(0xDC00),
      ':',
      '/',
      '(',
      ')',
      'w',
      '0',
      '1',
      'http://',
      'www.',
      '\u000B',
      '\u0085',
      '\uFEFF',
    ];
    var state = 20261117;
    int random(int limit) {
      state = (state * 1664525 + 1013904223) & 0xFFFFFFFF;
      return (state * limit) >> 32;
    }

    final bad = <String>[];
    for (var n = 0; n < 40000; n++) {
      final sb = StringBuffer();
      for (var k = random(60); k > 0; k--) {
        sb.write(alphabet[random(alphabet.length)]);
      }
      final t = sb.toString();
      if (current(t).join(' ') != legacy(t).join(' ')) bad.add(t);
    }
    expect(bad.take(5).toList(), isEmpty);
  });

  test('regexSpans equals the 1.6.0 matcher on edge cases', () {
    final cases = [
      'a@b.co@d.e',
      'a@b.co-+x@c.de',
      'a@b..co',
      'a@@b.co',
      'a@b.co.',
      '.a+@b.co',
      'a@b',
      '@b.co',
      'a@.co',
      'a@b.c@d.e.f',
      '😀 김+\u{10400}@회사.한국 \u{1D7D9}@\u{10400}.한',
      'e\u0301@a.co ①@b.co a@b.①',
      'a${String.fromCharCode(0xD800)}@b.co a@b.${String.fromCharCode(0xDC00)}',
      'www.site.test/x@y.co 010-1234-5678 https://other.test',
      'a@b.co http://one.test a@b.co',
      '900101-1234567 4111 1111 1111 1111 +1 (212) 555-1234',
      'https://a@b.co/x?mail=c@d.co',
    ];
    for (final t in cases) {
      expect(current(t), legacy(t), reason: t);
    }
  });

  // 1.6.0 needed many seconds for the first text
  test('regexSpans takes time in proportion to the length of long inputs', () {
    if (skipTiming()) return;
    final watch = Stopwatch()..start();
    for (final t in ['a' * 100000, 'a@${'b' * 100000}', '@' * 100000, 'a.' * 50000, '1' * 100000]) {
      expect(regexSpans(t).where((s) => s.label == 'EMAIL'), isEmpty);
    }
    final spans = regexSpans('a@b.co ' * 40000);
    expect(spans.length, 40000);
    expect((spans.first.start, spans.first.end, spans.first.label), (0, 6, 'EMAIL'));
    expect((spans.last.start, spans.last.end, spans.last.label), (279993, 279999, 'EMAIL'));
    expect(watch.elapsedMilliseconds, lessThan(10000));
  });
}
