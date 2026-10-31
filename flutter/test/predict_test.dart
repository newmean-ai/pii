// End-to-end parity with the Python evaluator through the Dart API (same runs and threshold as js/test/predict.test.ts).
// Fixtures are made from validation text and are not published; the runs skip without them. PII_SIZES=nano,medium
// limits the sizes; PII_CONCURRENCY sets how many detect calls run at once (default 4).
import 'dart:io';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

const runs = [
  ('large', 'q4e', 0.0), ('large', 'q4', 0.0), ('medium', 'q4e', 0.0), ('medium', 'q4', 0.0), //
  ('small', 'q4', 0.0), ('small', 'q4e', 0.0), ('nano', 'q4', 0.0), ('nano', 'q4e', 0.0), //
  ('medium', 'q4e', -0.5), ('nano', 'q4', 1.0),
];

String _bias(double b) => b == 0 ? '' : '-b${b == b.roundToDouble() ? b.toInt() : b}';

void main() {
  final concurrency = int.parse(Platform.environment['PII_CONCURRENCY'] ?? '4');
  for (final (size, variant, bias) in runs) {
    if (sizes != null && !sizes!.contains(size)) continue;
    final file = '$fixtures/predict-$size-$variant${_bias(bias)}.jsonl';
    final skip = !File(file).existsSync()
        ? 'no fixture $file'
        : !hasModel(size, variant)
        ? 'no model ${modelDir(size)}'
        : false;
    test(
      'model spans $size $variant bias $bias match the Python evaluator',
      () async {
        final rows = readJsonl(file);
        final sw = Stopwatch()..start();
        // Python O bias b is added to the O logit; sensitivity = -b
        final pii = await NewmeanPii.create(size: size, variant: variant, modelDir: modelDir(size), sensitivity: -bias);
        final got = List<String>.filled(rows.length, '');
        var next = 0;
        Future<void> worker() async {
          while (next < rows.length) {
            final i = next++;
            final spans = await pii.detect(rows[i]['text'] as String);
            got[i] = spans.map((s) => '${s.start}-${s.end}:${s.label}').join(' ');
          }
        }

        await Future.wait([for (var k = 0; k < concurrency; k++) worker()]);
        await pii.dispose();
        var same = 0;
        final diffs = <String>[];
        for (var i = 0; i < rows.length; i++) {
          final text = rows[i]['text'] as String;
          final u = codePointToUtf16(text);
          final want = [
            for (final s in (rows[i]['spans'] as List).cast<Map<String, dynamic>>())
              '${u[s['start'] as int]}-${u[s['end'] as int]}:${s['label']}',
          ].join(' ');
          if (want == got[i]) {
            same++;
          } else if (diffs.length < 3) {
            diffs.add('${text.length > 60 ? text.substring(0, 60) : text}\n  py   $want\n  dart ${got[i]}');
          }
        }
        print(
          '$size $variant bias $bias: $same/${rows.length} rows identical, ${sw.elapsedMilliseconds} ms'
          '${diffs.isEmpty ? '' : '\n${diffs.join('\n')}'}',
        );
        expect(same / rows.length, greaterThanOrEqualTo(0.99));
      },
      skip: skip,
      timeout: const Timeout(Duration(minutes: 10)),
    );
  }
}
