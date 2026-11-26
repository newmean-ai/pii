// The vault (since 1.7.2): the cases every package shares (../python/tests/vault_cases.json) and the Dart API.
import 'dart:convert';
import 'dart:io';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

void main() {
  final cases = File('../python/tests/vault_cases.json');
  group('shared cases', () {
    if (!cases.existsSync()) return;
    for (final g in (jsonDecode(cases.readAsStringSync())['groups'] as List).cast<Map<String, dynamic>>()) {
      test(g['name'] as String, () {
        final v = PiiVault();
        expect([for (final p in g['values'] as List) v.tag(p[0] as String, p[1] as String)], g['tags']);
        for (final c in (g['redact'] as List?) ?? []) {
          // spans a model found (then the vault's known values): each over the next place its value is written
          final text = c['text'] as String;
          var at = 0;
          final spans = <PiiSpan>[];
          for (final f in c['found'] as List) {
            final start = text.indexOf(f[0] as String, at);
            at = start + (f[0] as String).length;
            spans.add(PiiSpan(start, at, f[1] as String, 'model'));
          }
          final got = redactWithVault(text, spans, v);
          expect(got.text, c['want']);
          // the final spans as covered text, label and source, when the case gives them
          if (c['spans'] != null) {
            expect([
              for (final s in got.spans) [text.substring(s.start, s.end), s.label, s.source],
            ], c['spans']);
          }
        }
        if (g['entries'] != null) expect([for (final e in v.entries) e.value], g['entries']);
        for (final c in (g['hide'] as List?) ?? []) {
          expect(v.hide(c['text'] as String), c['want']);
        }
        for (final c in (g['hide_escaped'] as List?) ?? []) {
          expect(v.hide(c['text'] as String, escape: true), c['want']);
        }
        for (final c in (g['hide_value'] as List?) ?? []) {
          expect(v.hideValue(c['value']), c['want']);
        }
        for (final c in (g['restore'] as List?) ?? []) {
          final only = (c['only'] as List?)?.cast<String>().toSet();
          expect(v.restore(c['text'] as String, only: only, json: c['json'] as bool? ?? false), c['want']);
        }
      });
    }
  }, skip: cases.existsSync() ? false : 'no shared cases');

  test('model spans lose what their boundary took, and short values stay where they were found (1.7.1 review)', () {
    // (L3-B1, L3-B2 and L3-B4 in this package; the shared cases have the rest)
    final v = PiiVault();
    expect(
      redactWithVault('page 0 of 7', [PiiSpan(5, 6, 'DATE_OF_BIRTH', 'model')], v).text,
      'page [DATE_OF_BIRTH_1] of 7',
    );
    expect(v.hideValue('0'), '0');
    // a value the application gives is found as a whole value whatever its length
    expect(v.tag('DATE_OF_BIRTH', '0'), '[DATE_OF_BIRTH_1]');
    expect(v.hideValue('0'), '[DATE_OF_BIRTH_1]');
    final u = '${String.fromCharCode(0x1F600)} (Kim Minsu).';
    final r = redactWithVault(u, [PiiSpan(3, u.length, 'PERSON', 'model')], v);
    expect(r.text, '${String.fromCharCode(0x1F600)} ([PERSON_1]).');
    expect([for (final s in r.spans) (s.start, s.end)], [(4, 13)]);
  });

  test('restores a stream split anywhere', () {
    final v = PiiVault()
      ..tag('PERSON', 'Kim Minsu')
      ..tag('EMAIL', 'kim@example.com');
    const text = 'Dear [PERSON_1], we wrote to [EMAIL_1]. [x] [';
    for (final size in [1, 2, 3, 5, 7, 100]) {
      final r = v.restorer();
      final out = StringBuffer();
      for (var i = 0; i < text.length; i += size) {
        out.write(r.push(text.substring(i, i + size > text.length ? text.length : i + size)));
      }
      out.write(r.flush());
      expect(out.toString(), 'Dear Kim Minsu, we wrote to kim@example.com. [x] [');
    }
  });

  test('snapshots, entries, generation, replay', () {
    final v = PiiVault()..tag('PERSON', 'Alice');
    final snap = v.bindings(['[PERSON_1]', '[PERSON_9]']);
    expect(snap, {'[PERSON_1]': 'Alice'});
    v.remember('xid: 42y', 'x[ID_NUMBER_1]y');
    expect(v.recall('xid: 42y'), 'x[ID_NUMBER_1]y');
    final saved = v.entries.map((e) => PiiVaultEntry.fromJson(e.toJson())).toList();
    final g = v.generation;
    v.clear();
    expect([v.length, v.generation, v.recall('xid: 42y')], [0, g + 1, null]);
    v.tag('PERSON', 'Bob');
    expect(v.restore('[PERSON_1]', values: snap), 'Alice');
    expect(PiiVault(saved).restore('[PERSON_1]'), 'Alice');
    expect(() => PiiVault([const PiiVaultEntry('PERSON_1', 'PERSON', 'x')]), throwsArgumentError);
    expect(escapeTags('a [PERSON_1] [note] [x_1]'), 'a ［PERSON_1] [note] [x_1]');
    expect(vaultTags('[A_1] x [B_22] [c_3]'), ['[A_1]', '[B_22]']);
    expect(v.tag('X' * 50, 'value one'), '[${'X' * 32}_1]');
  });

  test('spans merge partial detections and escape gaps', () {
    final v = PiiVault()..tag('PERSON', 'Kim Minsu');
    const t = 'Ask Kim Minsu and Kim Minsuk.';
    final spans = v.withVaultSpans(t, const [PiiSpan(4, 7, 'PERSON', 'model')]);
    expect(spans, const [PiiSpan(4, 13, 'PERSON', 'vault')]);
    expect(applyVaultRedaction(t, spans, v), 'Ask [PERSON_1] and Kim Minsuk.');
    const u = 'account [REF_1] cobalt, see [REF_1]';
    expect(applyVaultRedaction(u, const [PiiSpan(0, 22, 'SECRET', 'block')], v), '[SECRET_1], see ［REF_1]');
    expect(v.value('[SECRET_1]'), 'account [REF_1] cobalt');
  });

  test('fourth review: empty value, a used-up family goes on in the next one, overlapping occurrences', () {
    final x = PiiVault([const PiiVaultEntry('[PERSON_999999999999]', 'PERSON', 'Last One')]);
    expect(x.tag('PERSON', ''), '');
    expect(x.tag('PERSON', 'One More'), '[PERSON__1]');
    expect(x.tag('PERSON', 'And Another'), '[PERSON__2]');
    expect(x.restore('[PERSON__1] [PERSON_999999999999]'), 'One More Last One');
    expect(x.hide('One More and Last One'), '[PERSON__1] and [PERSON_999999999999]');
    expect(PiiVault(x.entries).tag('PERSON', 'A Fourth'), '[PERSON__3]'); // its own entries load again
    final full = 'P' * 32;
    expect(PiiVault([PiiVaultEntry('[${full}_999999999999]', full, 'x')]).tag(full, 'y'), '[${'P' * 31}__1]');
    final last = 'A${'_' * 31}'; // a letter and underscores only: on to the next letter
    final z = PiiVault([PiiVaultEntry('[${last}_999999999999]', last, 'one')]);
    expect(z.tag(last, 'two'), '[B${'_' * 31}_1]');
    expect(z.restore('[B${'_' * 31}_1]'), 'two');
    // after Z it goes on with A; with all 26 used up the value is masked and not put back
    String letterFamily(String c) => '$c${'_' * 31}';
    final w = PiiVault([PiiVaultEntry('[${letterFamily('Z')}_999999999999]', letterFamily('Z'), 'one')]);
    expect(w.tag(letterFamily('Z'), 'two'), '[${letterFamily('A')}_1]');
    expect(w.restore('[${letterFamily('A')}_1]'), 'two');
    final letters = [for (var i = 0; i < 26; i++) String.fromCharCode(65 + i)];
    final none = PiiVault([
      for (final c in letters) PiiVaultEntry('[${letterFamily(c)}_999999999999]', letterFamily(c), 'v$c'),
    ]);
    expect(none.tag(letterFamily('Q'), 'more'), '[Q]');
    expect(none.restore('[Q]'), '[Q]');
    expect(() => PiiVault([const PiiVaultEntry('[PERSON_1000000000000]', 'PERSON', 'x')]), throwsArgumentError);
    final v = PiiVault()..tag('PERSON', 'Ann Ann');
    expect(v.hide('xAnn Ann Ann'), 'xAnn [PERSON_1]');
  });

  test('fifth review: repeats in one text, a merged detection, families with digits, convergence', () {
    PiiSpan found(int start, int end) => PiiSpan(start, end, 'PERSON', 'model');
    final v = PiiVault();
    const t = 'I met Alice yesterday. Use the word Alice twice, not Alicette.';
    final got = redactWithVault(t, [found(6, 11)], v);
    expect(got.text, 'I met [PERSON_1] yesterday. Use the word [PERSON_1] twice, not Alicette.');
    expect(got.spans.map((s) => '${s.start}-${s.end} ${s.source}').toList(), ['6-11 model', '36-41 vault']);
    final w = PiiVault()..tag('PERSON', 'Alex Kim');
    const u = 'Alex Kim Lee wrote. Kim Lee signed. Alex Kim Lee left.';
    final merged = redactWithVault(u, [found(5, 12)], w).text;
    expect(merged.contains('Alex') || merged.contains('Kim') || merged.contains('Lee'), isFalse, reason: merged);
    expect(w.restore(merged), u);
    final x = PiiVault([
      const PiiVaultEntry('[PERSON_1]', 'PERSON', 'Alice'),
      const PiiVaultEntry('[PERSON_1_99999999999]', 'PERSON_1', 'Last'),
    ]);
    expect(x.tag('PERSON_1', 'Next'), '[PERSON_1_100000000000]');
    expect(x.restore('[PERSON_1] [PERSON_1_100000000000]'), 'Alice Next');
    // masking goes on until nothing new is found, however many rounds that takes
    final c = PiiVault()..tag('PERSON', 'W00');
    final words = [for (var i = 0; i < 10; i++) 'W${i.toString().padLeft(2, '0')}'];
    var line = '';
    for (var n = 2; n <= words.length; n++) {
      line = words.sublist(0, n).join(' ');
      expect(redactWithVault(line, [found(line.length - 5, line.length)], c).text.contains('W0'), isFalse);
    }
    final again = redactWithVault(line, [found(line.length - 5, line.length)], c).text;
    expect(again.contains('W0'), isFalse, reason: again);
    expect(c.restore(again), line);
  });

  test('redactVault fails when the vault is cleared while the model reads', () async {
    final p = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'));
    final v = PiiVault();
    final pending = p.redactVault('Please write to kim@example.com.', v);
    v.clear();
    await expectLater(pending, throwsStateError);
    expect(v.length, 0);
    await p.dispose();
  }, skip: hasModel('nano', 'q4') ? false : 'no nano model');

  test('redactVault with the nano model', () async {
    final p = await NewmeanPii.create(size: 'nano', modelDir: modelDir('nano'));
    const text = 'Please call Kim Minsu on 010-1234-5678 or write to kim@example.com.';
    final v = PiiVault();
    final numbered = await p.redactVault(text, v);
    expect(numbered.spans, (await p.redact(text)).spans);
    expect(numbered.text, contains('[EMAIL_1]'));
    expect(v.restore(numbered.text), text);
    expect((await p.redactVault('kim@example.com again', v)).text, '[EMAIL_1] again');
    await p.dispose();
  }, skip: hasModel('nano', 'q4') ? false : 'no nano model');

  /// [s] in full-width ASCII (U+FF01 to U+FF5E).
  String fullWidth(String s) =>
      String.fromCharCodes([for (final c in s.codeUnits) c >= 0x21 && c <= 0x7E ? c + 0xFEE0 : c]);

  /// Text from code points (conjoining Hangul jamo and the like, kept out of the source as characters).
  String points(List<int> cps) => String.fromCharCodes(cps);

  PiiSpan model(int start, int end) => PiiSpan(start, end, 'PERSON', 'model');

  test('other spellings of a known value keep its placeholder', () {
    // what the model finds in another spelling of a known value gets that value's placeholder; the vault learns no new value
    final v = PiiVault()..tag('PERSON', 'Kim Minsu');
    const t = 'KIM MINSU wrote to Kim Minsu.';
    expect(redactWithVault(t, [model(0, 9)], v).text, '[PERSON_1] wrote to [PERSON_1].');
    expect(v.length, 1);
    expect(applyVaultRedaction(t, [model(0, 9)], v), '[PERSON_1] wrote to Kim Minsu.');
    // a one-word value in another case is another value
    expect(redactWithVault('Will will', [model(5, 9)], v).text, 'Will [PERSON_2]');
    expect(v.length, 2);
    // spans of values found in a reading point into the text as it is written: full-width letters and NFD Hangul
    final w = PiiVault()
      ..tag('EMAIL', 'kim@example.com')
      ..tag('PERSON', points([0xAE40, 0xBBFC, 0xC218]));
    final nfdName = points([0x1100, 0x1175, 0x11B7, 0x1106, 0x1175, 0x11AB, 0x1109, 0x116E]);
    final nfdParticle = points([0x1102, 0x1175, 0x11B7]);
    final x = '${fullWidth('kim@example.com')} and $nfdName$nfdParticle';
    final spans = w.withVaultSpans(x, const []);
    expect(spans, const [PiiSpan(0, 15, 'EMAIL', 'vault'), PiiSpan(20, 28, 'PERSON', 'vault')]);
    expect(applyVaultRedaction(x, spans, w), '[EMAIL_1] and [PERSON_1]$nfdParticle');
    expect(w.length, 2);
    // hideValue: a whole string in another spelling, whatever its length
    final y = PiiVault()..tag('PERSON', 'Al');
    expect(y.hideValue(['AL', 'al', fullWidth('Al')]), ['[PERSON_1]', 'al', '[PERSON_1]']);
  });
}
