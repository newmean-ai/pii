// The Dart API on the nano model (skips without a local copy): offsets around emoji and other hard text, options,
// isolates, and disposal.
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

bool _splitsPair(String t, int i) =>
    i > 0 && i < t.length && (t.codeUnitAt(i - 1) & 0xFC00) == 0xD800 && (t.codeUnitAt(i) & 0xFC00) == 0xDC00;

void _checkSpans(String t, List<PiiSpan> spans) {
  var last = 0;
  for (final s in spans) {
    expect(s.start, greaterThanOrEqualTo(last), reason: 'sorted, non-overlapping: $spans');
    expect(s.end, greaterThan(s.start));
    expect(s.end, lessThanOrEqualTo(t.length));
    expect(_splitsPair(t, s.start) || _splitsPair(t, s.end), isFalse, reason: 'span $s splits a surrogate pair');
    expect(piiLabels, contains(s.label));
    expect(['model', 'regex', 'model+regex'], contains(s.source));
    last = s.end;
  }
}

const hard = [
  '',
  ' \n\t ',
  '👩‍👩‍👧👩‍👩‍👧 Email me at kim@example.com 😀',
  '🇰🇷🇯🇵 김민준 (kim.minjun@example.co.kr), 010-1234-5678 🙂',
  '𠮷野家 𝓙𝓸𝓱𝓷 john.smith@example.org 𝟘𝟙𝟚',
  'Café José García, café José García, ㅎㅏㄴ 한 test@example.com',
  'line1\r\nCall Anna Müller on +49 30 1234567\r\n\u0000NUL and ‍ZWJ​',
  'مرحبا، اسمي أحمد علي وبريدي ahmed.ali@example.com \u202eRTL\u202c',
  'lone \ud800 surrogate and \udc00 another, mail lone@example.com',
];

void main() {
  final skip = hasModel('nano', 'q4') ? false : 'no nano model at ${modelDir('nano')}';
  group('nano', () {
    late NewmeanPii pii;
    setUpAll(() async => pii = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano')));
    tearDownAll(() => pii.dispose());

    test('defaults follow the npm package', () {
      expect((pii.size, pii.variant, pii.version, pii.sensitivity, pii.regex), ('nano', 'q4', '1.0.0', 0.0, false));
      expect(defaultSize, 'medium');
      expect(defaultVariant, {'large': 'q4e', 'medium': 'q4e', 'small': 'q4', 'nano': 'q4'});
    });

    test('UTF-16 offsets around emoji, astral characters, combining marks, RTL, NUL and lone surrogates', () async {
      for (final t in hard) {
        final spans = await pii.detect(t);
        _checkSpans(t, spans);
        for (final s in spans.where((s) => s.label == 'EMAIL')) {
          expect(
            t.substring(s.start, s.end),
            endsWith(
              'example.${t.contains('.co.kr')
                  ? 'co.kr'
                  : t.contains('.org')
                  ? 'org'
                  : 'com'}',
            ),
          );
        }
      }
      const t = '👩‍👩‍👧 𠮷 🇰🇷 kim@example.com';
      final email = (await pii.detect(t)).singleWhere((s) => s.label == 'EMAIL');
      expect(t.substring(email.start, email.end), 'kim@example.com');
    });

    test('redact modes', () async {
      const t = '😀 Email kim@example.com now';
      final r = await pii.redact(t);
      expect(r.text, '😀 Email [EMAIL] now');
      expect((await pii.redact(t, mode: RedactMode.mask)).text, '😀 Email *************** now');
      expect((await pii.redact(t, mode: RedactMode.blank)).text, '😀 Email  now');
      expect((await pii.redact(t, replace: (s, o) => '<${s.label}>')).text, '😀 Email <EMAIL> now');
      expect(r.spans, await pii.detect(t));
    });

    test('labels filter', () async {
      const t = 'Kim Minjun, kim@example.com, 010-1234-5678';
      final all = await pii.detect(t);
      expect(all.map((s) => s.label), containsAll(['EMAIL', 'PHONE']));
      final only = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), labels: ['EMAIL', 'NOT_A_LABEL']);
      expect((await only.detect(t)).map((s) => s.label), ['EMAIL']);
      await only.dispose();
      final none = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), labels: const []);
      expect(await none.detect(t), isEmpty);
      await none.dispose();
    });

    // a pattern span of a label family the model already found in the same region adds nothing (source stays "model")
    test('regex layer: every pattern match is masked, merged with the model spans', () async {
      final rx = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), regex: true);
      for (final t in [...hard, '카드 4111 1111 1111 1111 주민 900101-1234567 계좌 123-456-789012 www.example.com/a?b=1']) {
        final spans = await rx.detect(t);
        _checkSpans(t, spans);
        for (final m in regexSpans(t)) {
          expect(spans.any((s) => s.start <= m.start && m.end <= s.end), isTrue, reason: '$m in $spans');
        }
      }
      await rx.dispose();
      // the pattern label is used where the model's label is switched off (js/test/combine.test.ts)
      final fin = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), regex: true, labels: ['FINANCIAL']);
      const t = 'Call 010-1234-5678 today';
      final spans = await fin.detect(t);
      expect([for (final s in spans) (t.substring(s.start, s.end), s.label)], [('010-1234-5678', 'FINANCIAL')]);
      await fin.dispose();
    });

    test('sensitivity: more masks a superset', () async {
      const t =
          'Hi, this is Jae from the Seoul office; call me at the front desk or ping @jae_k on chat. Born 3 May 1990.';
      Set<int> covered(List<PiiSpan> spans) => {
        for (final s in spans)
          for (var i = s.start; i < s.end; i++) i,
      };
      final p = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'), sensitivity: -1);
      final low = covered(await p.detect(t));
      p.setSensitivity(0);
      final mid = covered(await p.detect(t));
      p.sensitivity = 1;
      final high = covered(await p.detect(t));
      await p.dispose();
      expect(mid.containsAll(low) && high.containsAll(mid), isTrue);
      expect(high.length, greaterThan(low.length));
    });

    test('detectSync, and concurrent calls give the sequential results', () async {
      final texts = [for (var i = 0; i < 24; i++) '${hard[i % hard.length]} #$i ${'Kim Minjun ' * (i % 5)}'];
      final seq = [for (final t in texts) pii.detectSync(t)];
      final par = await Future.wait(texts.map(pii.detect));
      expect(par, seq);
    });

    test('long text (several windows) with emoji', () async {
      final t = List.generate(400, (i) => 'Row $i 😀 Kim Minjun (kim$i@example.com)').join('\n');
      final spans = await pii.detect(t);
      _checkSpans(t, spans);
      expect(spans.where((s) => s.label == 'EMAIL').length, greaterThan(300));
    });
  }, skip: skip);

  group('loading', () {
    test('fromBytes and readFile check the pins', () async {
      final dir = modelDir('nano');
      final onnx = File('$dir/onnx/model_q4.onnx').readAsBytesSync();
      final tok = File('$dir/tokenizer.web.json').readAsBytesSync();
      final a = await NewmeanPii.fromBytes(onnx: onnx, tokenizer: tok, size: 'nano');
      final paths = <String>[];
      final b = await NewmeanPii.create(
        size: 'nano',
        readFile: (p) async => (paths..add(p), File('$dir/$p').readAsBytesSync()).$2,
      );
      expect(paths, ['tokenizer.web.json', 'onnx/model_q4.onnx']);
      expect(await b.detect(hard[3]), await a.detect(hard[3]));
      await a.dispose();
      await b.dispose();
      final bad = Uint8List.fromList(onnx)..[onnx.length ~/ 2] ^= 1;
      await expectLater(NewmeanPii.fromBytes(onnx: bad, tokenizer: tok, size: 'nano'), throwsStateError);
      // the q4 file is not the q4e file
      await expectLater(
        NewmeanPii.fromBytes(onnx: onnx, tokenizer: tok, size: 'nano', variant: 'q4e'),
        throwsStateError,
      );
      // without verification the core still rejects a broken file, with a message
      await expectLater(
        NewmeanPii.fromBytes(onnx: Uint8List(100), tokenizer: tok, size: 'nano', verify: false),
        throwsA(isA<StateError>().having((e) => e.message, 'message', contains('failed to load: '))),
      );
    });

    // callbacks stay on the calling isolate: one that captures something unsendable (a Flutter State) must work
    test('onProgress may capture objects that cannot be sent to an isolate', () async {
      final port = RawReceivePort();
      final seen = <String>[];
      final p = await NewmeanPii.create(
        size: 'nano',
        modelDir: modelDir('nano'),
        onProgress: (e) => seen.add('${e.file} ${port.hashCode}'),
      );
      await Future<void>.delayed(const Duration(milliseconds: 50)); // progress messages arrive asynchronously
      port.close();
      await p.dispose();
      expect(seen.length, 2);
    });

    test('dispose waits for running calls; later calls throw', () async {
      final p = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'));
      final t = List.filled(200, 'Kim Minjun kim@example.com').join(' ');
      final running = p.detect(t);
      final disposed = p.dispose();
      expect((await running).length, greaterThan(0));
      await disposed;
      await p.dispose(); // twice is fine
      expect(() => p.detect('x'), throwsStateError);
      expect(() => p.detectSync('x'), throwsStateError);
    });
  }, skip: skip);

  test('argument checks', () async {
    await expectLater(NewmeanPii.create(size: 'huge'), throwsArgumentError);
    await expectLater(NewmeanPii.create(size: 'nano', variant: 'fp32'), throwsArgumentError);
    expect(modelFile('medium', 'q4e').path, 'onnx/model_q4e.onnx');
    expect(modelFile('nano', 'tokenizer').bytes, 810589);
    expect(
      modelFile('large', 'q4').url.toString(),
      'https://huggingface.co/newmean-ai/pii-large/resolve/v1.0.0/onnx/model_q4.onnx',
    );
    expect(nativeBuildInfo, isNot(contains('fma')));
  });
}
