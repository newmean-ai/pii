// Table mode through the Dart API (pii_detect_table) on nano (skips without a local copy): the options reach the core,
// spans stay inside cells with UTF-16 offsets (an astral emoji before later rows), and text that is not a table gives the
// spans of detect. The column logic itself is tested in native/core/tests (pii_cli selftest, run_tests.py table, parity
// with the npm package).
import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

const names = [
  'Maria Lopez', 'Kim Minsu', 'Dupont, Jean', 'Anna Kowalska', 'Ahmed Ali', 'Sofia Rossi', 'Lars Nilsen', //
  'Priya Sharma',
];
const emails = [
  'maria.lopez@example.com', 'minsu.kim@example.com', 'jean.dupont@example.fr', 'anna.k@example.pl', //
  'ahmed.ali@example.com', 'sofia.rossi@example.it', 'lars@example.no', 'priya.sharma@example.in',
];
const phones = [
  '010-1234-5678', '010-2222-3333', '+33 6 12 34 56 78', '+48 512 345 678', '050-123-4567', '+39 347 123 4567', //
  '+47 912 34 567', '+91 98765 43210',
];
const csv =
    'name,email,phone,comment\n'
    'Maria Lopez,maria.lopez@example.com,010-1234-5678,Called twice about the delivery\n'
    'Kim Minsu,minsu.kim@example.com,010-2222-3333,no answer\n'
    '"Dupont, Jean",jean.dupont@example.fr,+33 6 12 34 56 78,"said ""later"""\n'
    'Anna Kowalska,anna.k@example.pl,+48 512 345 678,ok 👍🏽\n'
    'Ahmed Ali,ahmed.ali@example.com,050-123-4567,wants a refund\n'
    'Sofia Rossi,sofia.rossi@example.it,+39 347 123 4567,ok\n'
    'Lars Nilsen,lars@example.no,+47 912 34 567,will call back\n'
    'N/A,N/A,N/A,\n'
    'Priya Sharma,priya.sharma@example.in,+91 98765 43210,ok\n';
const prose = 'My name is Kim Minsu 👩‍👩‍👧 and my email is minsu.kim@example.com.';

List<String> texts(List<PiiSpan> spans, [String? source]) => [
  for (final s in spans)
    if (source == null || s.source == source) '${csv.substring(s.start, s.end)}:${s.label}',
];

/// Sorted, non-overlapping, and never over a line break, a delimiter or a quote at either end.
void checkCells(List<PiiSpan> spans) {
  var last = 0;
  for (final s in spans) {
    expect(s.start, greaterThanOrEqualTo(last), reason: '$spans');
    final t = csv.substring(s.start, s.end);
    expect(t.contains('\n') || ',"'.contains(t[0]) || ',"'.contains(t[t.length - 1]), isFalse, reason: t);
    last = s.end;
  }
}

void main() {
  final skip = hasModel('nano', 'q4') ? false : 'no nano model at ${modelDir('nano')}';
  group('table mode (nano)', () {
    late NewmeanPii pii;
    setUpAll(() async => pii = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano')));
    tearDownAll(() => pii.dispose());

    test('name, email and phone columns are propagated; header and placeholders are not', () async {
      final spans = await pii.detectTable(csv);
      checkCells(spans);
      expect(texts(spans, 'table'), [
        for (var i = 0; i < names.length; i++) ...['${names[i]}:PERSON', '${emails[i]}:EMAIL', '${phones[i]}:PHONE'],
      ]);
      expect(spans.every((s) => s.start > csv.indexOf('\n')), isTrue);
      expect(texts(spans, 'table').any((t) => t.startsWith('N/A')), isFalse);
      expect(await pii.detectTable(csv, delimiter: ','), spans);
      expect(pii.detectTableSync(csv), spans);
    });

    test('options reach the core', () async {
      final off = await pii.detectTable(csv, vote: false);
      checkCells(off);
      expect(off, isNotEmpty);
      expect(off.any((s) => s.source == 'table'), isFalse);
      // header off: the header row is a data row; "name" joins the vote's 80% and is masked, "email" has no "@" and
      // "phone" fewer than 6 digits
      final noHeader = await pii.detectTable(csv, header: false);
      expect(texts(noHeader, 'table').where((t) => ['name', 'email', 'phone'].contains(t.split(':')[0])), [
        'name:PERSON',
      ]);
      // ';' gives one long column per line: no vote, and no span over a line break
      final semi = await pii.detectTable(csv, delimiter: ';');
      expect(semi.any((s) => s.source == 'table' || csv.substring(s.start, s.end).contains('\n')), isFalse);
      // markdown: no line starts with "|", so there are no rows and the text is not a table
      expect(await pii.detectTable(csv, delimiter: 'markdown'), await pii.detect(csv));
      await expectLater(pii.detectTable(csv, delimiter: '|'), throwsArgumentError);
      expect(() => pii.detectTableSync(csv, delimiter: 'tab'), throwsArgumentError);
      final emailOnly = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), labels: ['EMAIL']);
      final spans = await emailOnly.detectTable(csv);
      expect(texts(spans), [for (final e in emails) '$e:EMAIL']);
      expect(spans.every((s) => s.source == 'table'), isTrue);
      await emailOnly.dispose();
    });

    test('text that is not a table gives the spans of detect (with and without the regex layer)', () async {
      final rx = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), regex: true);
      for (final p in [pii, rx]) {
        for (final t in [prose, '', 'one line, with a comma', 'a;b']) {
          expect(await p.detectTable(t), await p.detect(t), reason: '${p.regex} $t');
        }
        final email = (await p.detectTable(prose)).singleWhere((s) => s.label == 'EMAIL');
        expect(prose.substring(email.start, email.end), 'minsu.kim@example.com');
      }
      // the regex layer is passed to table mode too (pattern spans are clipped to cells like the model's)
      final spans = await rx.detectTable(csv);
      checkCells(spans);
      expect(spans.any((s) => s.source == 'table'), isTrue);
      await rx.dispose();
    });

    test('redactTable keeps the table', () async {
      final r = await pii.redactTable(csv);
      expect(r.spans, await pii.detectTable(csv));
      expect(r.text, applyRedaction(csv, r.spans));
      final lines = r.text.split('\n');
      expect(lines.length, csv.split('\n').length);
      expect(lines[0], 'name,email,phone,comment');
      expect(lines[1], '[PERSON],[EMAIL],[PHONE],Called twice about the delivery');
      expect('"'.allMatches(r.text).length, '"'.allMatches(csv).length);
      final masked = await pii.redactTable(csv, mode: RedactMode.mask, maskChar: '#');
      expect(masked.text, applyRedaction(csv, masked.spans, mode: RedactMode.mask, maskChar: '#'));
      String custom(PiiSpan s, String o) => '<${s.label}:${o.runes.length}>';
      final c = await pii.redactTable(csv, vote: false, replace: custom);
      expect(c.text, applyRedaction(csv, c.spans, replace: custom));
      expect(pii.redactTableSync(csv).text, r.text);
    });

    test('header context adds spans inside cells', () async {
      final rows = [
        for (var i = 0; i < names.length; i++)
          if (i != 2) '${names[i]},${emails[i]}\n',
      ];
      final text = 'name,email\n${rows.join()}';
      final base = await pii.detectTable(text);
      final ctx = await pii.detectTable(text, context: 'header');
      expect(await pii.detectTable(text, context: 'none'), base);
      expect(ctx, isNotEmpty);
      var last = 0;
      for (final s in ctx) {
        expect(s.start, greaterThanOrEqualTo(last), reason: '$ctx');
        // inside one cell: never over a delimiter or a line break
        expect(text.substring(s.start, s.end).contains(RegExp('[,\n]')), isFalse, reason: '$s');
        last = s.end;
      }
      Set<int> masked(List<PiiSpan> spans) => {
        for (final s in spans)
          for (var i = s.start; i < s.end; i++) i,
      };
      expect(masked(ctx).containsAll(masked(base)), isTrue);
      expect(pii.detectTableSync(text, context: 'header'), ctx);
      expect((await pii.redactTable(text, context: 'header')).spans, ctx);
      await expectLater(pii.detectTable(text, context: 'row'), throwsArgumentError);
      expect(() => pii.redactTableSync(text, context: 'Header'), throwsArgumentError);
    });

    test('emoji are never masked unless keepEmoji', () async {
      const text = 'Call Anna Kim 😀 at 010-1234-5678';
      final e = text.indexOf('😀');
      expect(pii.keepEmoji, isFalse);
      for (final spans in [await pii.detect(text), pii.detectSync(text), await pii.detectTable(text)]) {
        expect(spans, isNotEmpty);
        expect(spans.every((s) => s.end <= e || s.start >= e + 2), isTrue, reason: '$spans');
      }
      final keep = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), keepEmoji: true);
      expect(keep.keepEmoji, isTrue);
      await keep.detect(text);
      await keep.dispose();
    });

    test('wholeWords: no span starts or ends inside a word', () async {
      final words = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), wholeWords: true);
      expect(words.wholeWords, isTrue);
      expect(pii.wholeWords, isFalse);
      final alnum = RegExp(r'[\p{L}\p{N}]', unicode: true);
      bool word(String t, int i) => alnum.hasMatch(t[i]);
      for (final text in [
        '남성',
        '경기도',
        'R23',
        "Hi, I'm Kim Minji, call me at 010-1234-5678.",
        'region,id\n경기도,R23\n서울,R24\n',
      ]) {
        for (final spans in [await words.detect(text), words.detectTableSync(text)]) {
          for (final s in spans) {
            expect(s.start == 0 || !(word(text, s.start - 1) && word(text, s.start)), isTrue, reason: '$text $s');
            expect(s.end == text.length || !(word(text, s.end - 1) && word(text, s.end)), isTrue, reason: '$text $s');
          }
        }
      }
      await words.dispose();
    });

    test('strictShortNames: longer texts unchanged, never more PERSON', () async {
      final strict = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), strictShortNames: true);
      expect(strict.strictShortNames, isTrue);
      expect(pii.strictShortNames, isFalse);
      const long =
          "Hi, I'm Kim Minji, call me at 010-1234-5678. I live in Seoul and work at Example Corp as an engineer.";
      expect('${await strict.detect(long)}', '${await pii.detect(long)}');
      int person(List<PiiSpan> spans) =>
          spans.where((s) => s.label.startsWith('PERSON')).fold(0, (n, s) => n + s.end - s.start);
      for (final text in ['경기', '네이버', '카카오', '남성', '김민지', 'Maria Lopez']) {
        expect(person(await strict.detect(text)), lessThanOrEqualTo(person(await pii.detect(text))), reason: text);
      }
      await strict.dispose();
    });

    test('info', () {
      final i = pii.info;
      expect(i.packageVersion, '1.3.0');
      expect(i.packageVersion, nativeVersion);
      expect(i.modelVersion, pii.version);
      expect(
        (i.model.path, i.model.sha256),
        (modelFile(pii.size, pii.variant).path, modelFile(pii.size, pii.variant).sha256),
      );
      expect(i.tokenizer.sha256, modelFile(pii.size, 'tokenizer').sha256);
    });
  }, skip: skip);
}
