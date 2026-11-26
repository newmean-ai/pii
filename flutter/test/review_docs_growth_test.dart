// Doubling test for the edges of a span the model found (review round of SDK 1.7.1, L2-B2), apart from
// review_docs_test.dart so that the test runner runs it alone ("growth" in the file name): leaving out what a span's
// edges hold that is not part of the value takes time in proportion to the span, however many brackets, quotation marks,
// stops or spaces it has to look past. Timing: growth.dart.
import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'growth.dart';

void main() {
  final quote = String.fromCharCode(0x22);
  final shapes = <(String, String Function(int))>[
    ('closing brackets after the value', (n) => 'Kim Minsu${')' * n}'),
    ('opening brackets before the value', (n) => '${'(' * n}Kim Minsu'),
    ('pairs of brackets around the value', (n) => '${'(' * n}Kim Minsu${')' * n}'),
    ('quotation marks around the value', (n) => '${quote * n}Kim Minsu${"'" * n}'),
    // (commas before the value: a "." at the start of a value stays)
    ('stops and spaces around the value', (n) => '${' ,' * n}Kim Minsu${', ' * n}'),
    ('brackets that are not one pair', (n) => '(${')(' * n}Kim Minsu${')(' * n})'),
  ];
  for (final (name, make) in shapes) {
    test('doubling: span edges, $name', () {
      if (skipTiming()) return;
      expectLinear('span edges, $name', [200000, 400000, 800000], (n) {
        final text = make(n);
        final found = [PiiSpan(0, text.length, 'PERSON', 'model')];
        return medianTime(() => expect(redactWithVault(text, found, PiiVault()).text, contains('[PERSON_1]')));
      });
    }, timeout: const Timeout(Duration(minutes: 5)));
  }
}
