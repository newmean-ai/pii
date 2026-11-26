// Doubling tests of the vault (REVIEW-GUIDE rule 4), kept apart from vault_test.dart so that the test runner runs them
// alone, after the suites ("growth" in the file name), where no other suite distorts the ratios. Timing: growth.dart.
import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'growth.dart';

void main() {
  /// [s] in full-width ASCII (U+FF01 to U+FF5E).
  String fullWidth(String s) =>
      String.fromCharCodes([for (final c in s.codeUnits) c >= 0x21 && c <= 0x7E ? c + 0xFEE0 : c]);

  PiiSpan model(int start, int end) => PiiSpan(start, end, 'PERSON', 'model');

  test('doubling: withVaultSpans and redactWithVault are one pass', () {
    if (skipTiming()) return;
    // n known values found again, each also found in part by the model, and n other spans: one pass over both
    const unit = 'Ask Kim Minsu about Lee Jun. ';
    (String, List<PiiSpan>) input(int n) => (
      unit * n,
      [
        for (var i = 0; i < n; i++) ...[
          model(i * unit.length + 4, i * unit.length + 7),
          model(i * unit.length + 20, i * unit.length + 27),
        ],
      ],
    );
    final v = PiiVault()..tag('PERSON', 'Kim Minsu');
    expectLinear('withVaultSpans', [32000, 64000, 128000], (n) {
      final (text, spans) = input(n);
      return medianTime(() => expect(v.withVaultSpans(text, spans).length, 2 * n));
    });
    expectLinear('redactWithVault', [8000, 16000, 32000], (n) {
      final (text, spans) = input(n);
      return medianTime(() => expect(redactWithVault(text, spans, PiiVault(v.entries)).spans.length, 2 * n));
    });
    // one span a model found with n brackets before its value and n sentence marks and opening brackets after it: each
    // end is cut once (1.7.1 review, L3-B1)
    expectLinear("redactWithVault, a span's ends", [32000, 64000, 128000], (n) {
      final text = '${'(' * n}Kim Minsu${'.,' * n}${' (' * n}';
      return medianTime(() {
        final got = redactWithVault(text, [model(0, text.length)], PiiVault()).spans;
        expect([for (final s in got) text.substring(s.start, s.end)], ['Kim Minsu']);
      });
    });
    // the same with the text in full-width letters (a reading with a map back to the text)
    final wide = fullWidth(unit);
    expectLinear('withVaultSpans, full-width', [16000, 32000, 64000], (n) {
      final text = wide * n;
      return medianTime(() => expect(v.withVaultSpans(text, const []).length, n));
    });
  }, timeout: const Timeout(Duration(minutes: 5)));

  test('doubling: capitals, two Hangul syllables and clusters', () {
    if (skipTiming()) return;
    String cps(List<int> c) => String.fromCharCodes(c);
    // capitals in other spellings (full Unicode uppercase, Greek without accents, the capital sharp s), a value of two
    // Hangul syllables with particles and inside longer words, values followed by a variation selector and a combining
    // mark, and custom values that merge: seven hits a line
    final jiwon = cps([0xC9C0, 0xC6D0]);
    final watanabe = cps([0x6E21, 0x8FBA]);
    final kim = cps([0xAE40, 0xBBFC, 0xC218]);
    final line =
        '$jiwon${cps([0xB2D8, 0xAED8, 0xC11C])} $jiwon${cps([0xAE08, 0xC744])} $jiwon${cps([0xD588, 0xB2E4])}; '
        '${cps([0x39D, 0x399, 0x39A, 0x39F, 0x3A3])} '
        '${cps([0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A3])}, '
        'ANNA WEISS and ANNA WEI${cps([0x1E9E])}; $watanabe${cps([0xE0100, 0x3055, 0x3093])}, '
        '$kim${cps([0x301, 0xB2D8])}, AB-1234-CD. ';
    final known = PiiVault()
      ..tag('PERSON', jiwon)
      ..tag(
        'PERSON',
        '${cps([0x39D, 0x3AF, 0x3BA, 0x3BF, 0x3C2])} '
            '${cps([0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3CC, 0x3C0, 0x3BF, 0x3C5, 0x3BB, 0x3BF, 0x3C2])}',
      )
      ..tag('PERSON', 'Anna Wei${cps([0xDF])}')
      ..tag('PERSON', watanabe)
      ..tag('PERSON', kim)
      ..tag('CUSTOMER', 'AB-1234')
      ..tag('CUSTOMER', '1234-CD');
    expectLinear('redactWithVault, capitals, Hangul and clusters', [1000, 2000, 4000], (n) {
      final text = line * n;
      return medianTime(() => expect(redactWithVault(text, const [], PiiVault(known.entries)).spans.length, 7 * n));
    });
    // a known value followed by a run of n combining marks (taken in whole), and a value of two syllables followed by n
    // syllables (another word)
    expectLinear('withVaultSpans, a run of marks', [200000, 400000, 800000], (n) {
      final text = '$watanabe${cps([0x301]) * n}${cps([0x3055, 0x3093])}';
      return medianTime(() => expect(known.withVaultSpans(text, const []).first.end, text.length - 2));
    });
    expectLinear('withVaultSpans, a long word after two syllables', [20000, 40000, 80000], (n) {
      final text = '${jiwon * n} ${'$jiwon${cps([0xAE08])}' * n}';
      return medianTime(() => expect(known.withVaultSpans(text, const []).length, 0));
    });
  }, timeout: const Timeout(Duration(minutes: 5)));

  test('doubling: long known values that repeat themselves', () {
    if (skipTiming()) return;
    // a known value of n words that repeats itself, in a text that repeats it: each place where it may start is looked at
    // once, in every spelling the vault searches (1.7.1 gate 7: Swift, Kotlin and Dart compared it from its start at
    // every place). The vault learns the value (its spellings) and finds it; one hit each: the places overlap and merge.
    String words(List<String> w, int n) => [for (var i = 0; i < n; i++) w[i % w.length]].join(' ');
    // a value learned from a model's span over the whole text (". . . .Kim Minsu"), found once after long partial matches
    expectLinear('a long value from a span, found once', [40000, 80000, 160000], (n) {
      final text = '${' .' * n}Kim Minsu${', ' * n}';
      final want = ' [PERSON_1]${', ' * n}';
      return medianTime(() => expect(redactWithVault(text, [model(0, text.length)], PiiVault()).text, want));
    });
    final shapes = <(String, String, String Function(int), String Function(int), List<int>)>[
      ('as it is written', 'PERSON', (n) => words(['ha'], n), (n) => 'ha ' * (2 * n), [32000, 64000, 128000]),
      ('in any ASCII case', 'PERSON', (n) => words(['Ha', 'ha'], n), (n) => 'hA Ha ' * n, [32000, 64000, 128000]),
      (
        "a URL's path as it is written",
        'URL',
        (n) => 'https://x.io/' * n,
        (n) => 'HTTPS://X.IO/${'https://x.io/' * (2 * n - 1)}',
        [16000, 32000, 64000],
      ),
      (
        'in capitals of another spelling',
        'PERSON',
        (n) => words(['Wei${String.fromCharCode(0xDF)}'], n),
        (n) => 'WEISS ' * (2 * n),
        [16000, 32000, 64000],
      ),
    ];
    for (final (name, label, value, text, sizes) in shapes) {
      expectLinear('a long value that overlaps itself, $name', sizes, (n) {
        final known = value(n), t = text(n);
        return medianTime(() {
          final v = PiiVault()..tag(label, known);
          expect(v.find(t).length, 1);
        });
      });
    }
  }, timeout: const Timeout(Duration(minutes: 5)));
}
