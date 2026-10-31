import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

void main() {
  // as js/test/combine.test.ts
  const text = 'Call Kim at 010-1234-5678.';
  const spans = [PiiSpan(5, 8, 'PERSON_L2', 'model'), PiiSpan(12, 25, 'PHONE', 'model')];
  test('tags with the label family', () => expect(applyRedaction(text, spans), 'Call [PERSON] at [PHONE].'));
  test(
    'masks per character',
    () => expect(applyRedaction(text, spans, mode: RedactMode.mask), 'Call *** at *************.'),
  );
  test('blanks', () => expect(applyRedaction(text, spans, mode: RedactMode.blank), 'Call  at .'));
  test('uses a custom replacement', () {
    expect(
      applyRedaction(text, spans, replace: (s, o) => '<${s.label}:${o.length}>'),
      'Call <PERSON_L2:3> at <PHONE:13>.',
    );
  });
  test('masks one character per code point, not per UTF-16 unit', () {
    const t = 'id 😀𠮷x end';
    expect(
      applyRedaction(t, const [PiiSpan(3, 8, 'USERNAME', 'model')], mode: RedactMode.mask, maskChar: '#'),
      'id ### end',
    );
  });
  test('no spans leaves the text unchanged', () => expect(applyRedaction('abc', const []), 'abc'));
  test('labels and families', () {
    expect(piiLabels.length, 13);
    expect(level1Labels, piiLabels.where((l) => !l.endsWith('_L2')).toList());
    expect(labelFamily('URL_L2'), 'URL');
  });
}
