// User lists (allow, block, listsId; 1.4.0). Validation runs before any model file is read, so those tests need no model:
// the same list, index and reason as the npm package (js/test/lists.test.ts) and the C core. Matching on nano (skips
// without a local copy); the matching rules themselves are tested in native/core/tests and js/test.
import 'dart:typed_data';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

const a = PiiAllowEntry.new;
const b = PiiBlockEntry.new;

/// The outcome of creating a redactor with these lists on bytes that are not a model: the PiiListException as
/// "list index reason", or "ok" when the lists passed (and the model check failed after them).
Future<String> outcome({
  List<PiiAllowEntry>? allow,
  List<PiiBlockEntry>? block,
  String? listsId,
  double sensitivity = 0,
}) async {
  try {
    await NewmeanPii.fromBytes(
      onnx: Uint8List(1),
      tokenizer: Uint8List(1),
      size: 'nano',
      sensitivity: sensitivity,
      allow: allow,
      block: block,
      listsId: listsId,
    );
    return 'loaded';
  } on PiiListException catch (e) {
    return '${e.list} ${e.index} ${e.reason}';
  } on StateError {
    return 'ok';
  }
}

void main() {
  group('validation', () {
    test('each reason, with list and index', () async {
      final cases = <String, (List<PiiAllowEntry>?, List<PiiBlockEntry>?)>{
        'allow 0 too_long': ([a('a' * 257)], null),
        'allow 1 too_long': ([a('ok'), a('가' * 65)], null),
        'allow 0 bad_text': ([a('a\u0000b')], null),
        'block 0 bad_text': (null, [b('a\rb', 'PERSON')]),
        'allow 2 empty': ([a('x'), a('y'), a('')], null),
        'allow 0 edge_space': ([a(' 제주')], null),
        'block 1 edge_space': (null, [b('박지훈', 'PERSON'), b('박지훈　', 'PERSON')]),
        'allow 0 unknown_label': ([a('제주', label: 'REGION')], null),
        'block 0 unknown_label': (null, [b('박지훈', 'person')]),
        'block 2 label_conflict': (null, [b('박지훈', 'PERSON'), b('ACME', 'ORG'), b('박지훈', 'ORG')]),
        'block 1 label_conflict': (null, [b('Acme', 'ORG'), b('ACME', 'PERSON')]), // ASCII case folded
        'block 1 in_both': ([a('acme')], [b('새솔고', 'ORG'), b('ACME', 'ORG')]),
      };
      for (final MapEntry(key: want, value: (allow, block)) in cases.entries) {
        expect(await outcome(allow: allow, block: block), want);
      }
    });

    test('bad_text: every line break, NUL and unpaired surrogates', () async {
      for (final t in ['\n', '\r', '\u0085', ' ', ' ', '\u0000', 'a\ud800', '\udc00a', 'a\udc00\ud800b']) {
        expect(await outcome(allow: [a('x$t')]), 'allow 0 bad_text', reason: t.codeUnits.toString());
      }
      expect(await outcome(allow: [a('𠮷野家')]), 'ok'); // a surrogate pair is fine
    });

    test('edge_space: the White_Space code points at either end, never trimmed', () async {
      const ws = [
        0x09, 0x0b, 0x0c, 0x20, 0xa0, 0x1680, 0x2000, 0x2005, 0x200a, 0x202f, 0x205f, 0x3000, //
      ];
      for (final c in ws) {
        final s = String.fromCharCode(c);
        expect(await outcome(allow: [a('$s제주')]), 'allow 0 edge_space', reason: '$c');
        expect(await outcome(allow: [a('제주$s')]), 'allow 0 edge_space', reason: '$c');
        expect(await outcome(allow: [a('제$s주')]), 'ok', reason: '$c inside');
      }
      expect(await outcome(allow: [a('​제주')]), 'ok'); // ZERO WIDTH SPACE is not White_Space
    });

    test('order of the checks within an entry', () async {
      expect(await outcome(allow: [a('\u0000${'a' * 256}')]), 'allow 0 too_long'); // 257 units before bad_text
      expect(await outcome(allow: [a('a' * 65 + '\n')]), 'allow 0 bad_text'); // 66 units: bad_text before too_long
      expect(await outcome(allow: [a(' ' * 65)]), 'allow 0 too_long'); // 65 code points before edge_space
      expect(await outcome(allow: [a('', label: 'NOPE')]), 'allow 0 empty');
      expect(await outcome(allow: [a(' x', label: 'NOPE')]), 'allow 0 edge_space');
      expect(await outcome(allow: [a('😀' * 64)]), 'ok'); // 128 units, 64 code points
      expect(await outcome(allow: [a('😀' * 65)]), 'allow 0 too_long');
    });

    test('order: listsId, then allow, then block, then the checks across entries', () async {
      expect(await outcome(listsId: '', allow: [a('')]), 'meta null bad_lists_id');
      for (final id in ['a\nb', 'a\u0000', 'a\ud800', 'x' * 129]) {
        expect(await outcome(listsId: id), 'meta null bad_lists_id');
      }
      expect(await outcome(listsId: 'x' * 128), 'ok');
      expect(await outcome(listsId: '😀' * 128), 'ok');
      expect(await outcome(allow: [a('x'), a('')], block: [b('', 'ORG')]), 'allow 1 empty');
      expect(await outcome(allow: [a('x'), a(' y'), a('')]), 'allow 1 edge_space'); // the first failing entry
      expect(await outcome(allow: [a('x')], block: [b('x', 'ORG'), b('', 'ORG')]), 'block 1 empty');
      // label_conflict (anywhere in block) before in_both
      expect(
        await outcome(allow: [a('x')], block: [b('x', 'ORG'), b('y', 'ORG'), b('y', 'PERSON')]),
        'block 2 label_conflict',
      );
      // a list error before a sensitivity error (as in npm, Swift, Kotlin and Python)
      expect(await outcome(block: [b('', 'PERSON')], sensitivity: double.nan), 'block 0 empty');
      expect(await outcome(listsId: '', sensitivity: double.infinity), 'meta null bad_lists_id');
    });

    test('too_many after 10,000 entries', () async {
      final ok = [for (var i = 0; i < 10000; i++) a('w$i')];
      expect(await outcome(allow: ok), 'ok');
      expect(await outcome(allow: [...ok, a('w10000')]), 'allow 10000 too_many');
      expect(await outcome(allow: [...ok, a('')]), 'allow 10000 too_many'); // the 10,001st entry is never checked
      expect(await outcome(allow: [...ok.take(9999), a(''), a('x')]), 'allow 9999 empty');
      expect(await outcome(block: [for (var i = 0; i < 10001; i++) b('w$i', 'ORG')]), 'block 10000 too_many');
    });

    test('checked before any model file is read; the message never holds the text', () async {
      final read = <String>[];
      await expectLater(
        NewmeanPii.create(
          size: 'nano',
          readFile: (p) async => (read..add(p), Uint8List(0)).$2,
          block: [b('박지훈 ', 'PERSON')],
          sensitivity: double.nan, // the list error comes first
        ),
        throwsA(
          isA<PiiListException>()
              .having((e) => (e.list, e.index, e.reason), 'error', ('block', 0, 'edge_space'))
              .having((e) => '$e', 'toString', isNot(contains('박지훈'))),
        ),
      );
      await expectLater(
        NewmeanPii.create(size: 'nano', modelDir: '/nonexistent', listsId: 'x\ny'),
        throwsA(isA<PiiListException>().having((e) => '$e', 'toString', 'PiiListException: listsId: bad_lists_id')),
      );
      expect(read, isEmpty);
      expect('${const PiiListException('allow', 3, 'empty')}', 'PiiListException: allow list entry 3: empty');
    });
  });

  final skip = hasModel('nano', 'q4') ? false : 'no nano model at ${modelDir('nano')}';
  group('matching (nano)', () {
    late NewmeanPii pii;
    setUpAll(() async => pii = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano')));
    tearDownAll(() => pii.dispose());

    test('info counts entries after merging, and the listsId', () async {
      expect(pii.info.lists, isA<PiiListsInfo>().having((l) => (l.allow, l.block, l.id), 'lists', (0, 0, null)));
      final p = await NewmeanPii.create(
        size: 'nano',
        modelDir: modelDir('nano'),
        allow: [
          a('제주'),
          a('제주'),
          a('경기', label: 'PERSON'),
          a('경기', label: 'ORG'),
          a('JEJU'),
          a('jeju'),
        ],
        block: [b('박지훈', 'PERSON'), b('박지훈', 'PERSON'), b('새솔고등학교', 'ORG')],
        listsId: 'survey-2026-11 v3',
      );
      final l = p.info.lists;
      expect((l.allow, l.block, l.id), (3, 2, 'survey-2026-11 v3'));
      expect(p.info.packageVersion, '1.5.0');
      await p.dispose();
    });

    test('a block entry is masked whatever labels enables, before Korean particles', () async {
      final p = await NewmeanPii.create(
        size: 'nano',
        modelDir: modelDir('nano'),
        labels: const [],
        block: [b('박지훈', 'PERSON'), b('새솔고등학교', 'ORG')],
      );
      expect(await p.detect('Kim Minjun, kim@example.com'), isEmpty); // nothing else is enabled
      expect(await p.detect('박지훈님께'), [const PiiSpan(0, 3, 'PERSON', 'block')]);
      const t = '저는 새솔고등학교 학생이고 박지훈 선생님께 배웁니다.';
      final spans = await p.detect(t);
      expect(
        [for (final s in spans) (t.substring(s.start, s.end), s.label, s.source)],
        [('새솔고등학교', 'ORG', 'block'), ('박지훈', 'PERSON', 'block')],
      );
      expect(p.detectSync(t), spans);
      expect((await p.redact('박지훈님께')).text, '[PERSON]님께');
      expect(await p.detect('박지훈씨네고양이'), isEmpty, reason: 'not a particle run: no word boundary');
      await p.dispose();
      final all = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), block: [b('박지훈', 'PERSON')]);
      final s = (await all.detect('박지훈님께')).single;
      expect((s.start, s.end, s.label, s.source), (0, 3, 'PERSON', 'block'));
      await all.dispose();
    });

    test('allow removes a span of the entry\'s label family only', () async {
      const t = 'Kim Minjun, kim@example.com';
      final email = (await pii.detect(t)).singleWhere((s) => s.label == 'EMAIL');
      expect(t.substring(email.start, email.end), 'kim@example.com');
      final any = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), allow: [a('KIM@example.com')]);
      final spans = await any.detect(t);
      expect(spans.any((s) => s.label == 'EMAIL'), isFalse, reason: '$spans');
      expect(spans, [
        for (final s in await pii.detect(t))
          if (s.label != 'EMAIL') s,
      ]);
      await any.dispose();
      final person = await NewmeanPii.create(
        size: 'nano',
        modelDir: modelDir('nano'),
        allow: [a('kim@example.com', label: 'PERSON')],
      );
      expect(await person.detect(t), await pii.detect(t));
      await person.dispose();
    });

    test('table mode: block spans inside cells', () async {
      const csv =
          'name,comment\n'
          'Maria Lopez,ask 박지훈 later\n'
          'Kim Minsu,박지훈\n'
          'Anna Kowalska,"say ""hi"" to 박지훈님"\n';
      final p = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), block: [b('박지훈', 'PERSON')]);
      for (final spans in [await p.detectTable(csv), p.detectTableSync(csv, vote: false)]) {
        final block = [
          for (final s in spans)
            if (s.source == 'block') (csv.substring(s.start, s.end), s.label),
        ];
        expect(block, List.filled(3, ('박지훈', 'PERSON')), reason: '$spans');
      }
      expect((await p.redactTable(csv)).text.split('\n')[2], endsWith(',[PERSON]'));
      await p.dispose();
    });

    test('dispose waits for a running job before freeing the lists', () async {
      final p = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), block: [b('박지훈', 'PERSON')]);
      final t = List.filled(200, '박지훈님께 kim@example.com').join(' ');
      final running = p.detect(t);
      final disposed = p.dispose();
      final spans = await running;
      expect(spans.where((s) => s.source == 'block').length, 200);
      await disposed;
      expect(() => p.detect('박지훈'), throwsStateError);
      expect(p.info.lists.block, 1);
    });
  }, skip: skip);
}
