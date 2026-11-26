// The edges of a span the model found (review round of SDK 1.7.1, L2-B2): the cases every package shares are in
// python/tests/vault_cases.json ("redact", run by vault_test.dart); these check what is Dart's own.
import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

void main() {
  PiiSpan marked(String text, String value, [String source = 'model']) {
    final start = text.indexOf(value);
    return PiiSpan(start, start + value.length, 'PERSON', source);
  }

  test('a block-list span keeps its edges', () {
    const text = 'Tiffany & Co. sent it';
    expect(redactWithVault(text, [marked(text, 'Tiffany & Co.', 'block')], PiiVault()).text, '[PERSON_1] sent it');
  });

  test('applyVaultRedaction learns the value too', () {
    final text = '${String.fromCharCode(0x20000)} Please look up Kim Minsu.';
    final vault = PiiVault();
    expect(
      applyVaultRedaction(text, [marked(text, 'Kim Minsu.')], vault),
      '${String.fromCharCode(0x20000)} Please look up [PERSON_1].',
    );
    expect([for (final e in vault.entries) e.value], ['Kim Minsu']);
  });
}
