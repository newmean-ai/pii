// PiiOptionsC and PiiTableOptionsC (lib/src/ffi.dart) against pii_options and pii_table_options (native/core/pii.h):
// the same fields in the same order, each with the annotation of its header type (so that the 32-bit layout follows from
// the header too), and on this host the offsets of a test build of the core (PII_TEST_LAYOUT, native/core/src/lists.c).
// The load-time size check (lib/src/native.dart) covers every target; this test covers the field offsets.
import 'dart:ffi';
import 'dart:io';

import 'package:ffi/ffi.dart';
import 'package:newmean_pii/src/ffi.dart';
import 'package:test/test.dart';

const core = '../native/core';

/// (type, name) of each field of a C struct body: type "Pointer", "size_t", "int", "double", "uint32_t", ...
List<(String, String)> cFields(String header, String struct) {
  final m = RegExp('typedef struct \\{([^}]*)\\} $struct;').firstMatch(header)!;
  final body = m.group(1)!.replaceAll(RegExp(r'/\*.*?\*/', dotAll: true), '');
  final out = <(String, String)>[];
  for (final decl in body.split(';').map((d) => d.trim()).where((d) => d.isNotEmpty)) {
    final first = RegExp(r'^(?:const\s+)?(\w+)\s*(.*)$', dotAll: true).firstMatch(decl)!;
    for (final d in first.group(2)!.split(',')) {
      final name = d.trim();
      out.add((name.startsWith('*') ? 'Pointer' : first.group(1)!, name.replaceAll('*', '').trim()));
    }
  }
  return out;
}

/// (annotation or "Pointer", name) of each field of a Dart Struct class.
List<(String, String)> dartFields(String source, String cls) {
  final m = RegExp('final class $cls extends Struct \\{([^}]*)\\}').firstMatch(source)!;
  final out = <(String, String)>[];
  String? annotation;
  for (final line in m.group(1)!.split('\n').map((l) => l.trim()).where((l) => l.isNotEmpty)) {
    if (line.startsWith('@')) {
      annotation = line;
      continue;
    }
    final f = RegExp(r'^external (\S+) (.*);$').firstMatch(line)!;
    final type = f.group(1)!.startsWith('Pointer<') ? (annotation ?? 'Pointer') : annotation!;
    for (final name in f.group(2)!.split(',')) {
      out.add((type, name.trim()));
    }
    annotation = null;
  }
  return out;
}

const annotationOf = {'size_t': '@Size()', 'int': '@Int()', 'double': '@Double()', 'uint32_t': '@Uint32()'};

String camel(String snake) => snake.replaceAllMapped(RegExp(r'_(\w)'), (m) => m.group(1)!.toUpperCase());

// Each field set to a value whose lowest byte is nonzero (little-endian hosts), so the first changed byte is its offset.
final optionSetters = <String, void Function(PiiOptionsC)>{
  'structSize': (o) => o.structSize = -1,
  'sensitivity': (o) => o.sensitivity = 1.0000000000000002,
  'labels': (o) => o.labels = 0xffffffff,
  'extra': (o) => o.extra = Pointer.fromAddress(-1),
  'nExtra': (o) => o.nExtra = -1,
  'extraUnit': (o) => o.extraUnit = -1,
  'keepEmoji': (o) => o.keepEmoji = -1,
  'wholeWords': (o) => o.wholeWords = -1,
  'strictShortNames': (o) => o.strictShortNames = -1,
  'lists': (o) => o.lists = Pointer.fromAddress(-1),
};
final tableSetters = <String, void Function(PiiTableOptionsC)>{
  'structSize': (t) => t.structSize = -1,
  'delimiter': (t) => t.delimiter = -1,
  'header': (t) => t.header = -1,
  'vote': (t) => t.vote = -1,
  'context': (t) => t.context = -1,
};

/// Offset of the field [set] writes in a zeroed T of [size] bytes.
int offsetOf<T extends Struct>(int size, void Function(Pointer<T>) set) {
  final p = calloc<Uint8>(size);
  try {
    set(p.cast());
    final bytes = p.asTypedList(size);
    return bytes.indexWhere((b) => b != 0);
  } finally {
    calloc.free(p);
  }
}

void main() {
  final header = File('$core/pii.h').readAsStringSync();
  final dart = File('lib/src/ffi.dart').readAsStringSync();

  test('fields, order and annotations follow pii.h', () {
    for (final (c, d) in [('pii_options', 'PiiOptionsC'), ('pii_table_options', 'PiiTableOptionsC')]) {
      final cf = cFields(header, c), df = dartFields(dart, d);
      expect(cf, isNotEmpty);
      expect([for (final f in df) f.$2], [for (final f in cf) camel(f.$2)], reason: '$d field names');
      expect(
        [for (final f in df) f.$1],
        [for (final f in cf) f.$1 == 'Pointer' ? 'Pointer' : annotationOf[f.$1]],
        reason: d,
      );
    }
    expect(cFields(header, 'pii_options').last, ('Pointer', 'lists'));
  });

  group('offsets on this host', () {
    late DynamicLibrary lib;
    late Directory tmp;
    setUpAll(() {
      tmp = Directory.systemTemp.createTempSync('pii_layout');
      final out = '${tmp.path}/libpii_layout.${Platform.isMacOS ? 'dylib' : 'so'}';
      final sources = [
        for (final f in Directory('$core/src').listSync())
          if (f.path.endsWith('.c')) f.path,
      ];
      final r = Process.runSync('cc', [
        '-shared',
        '-fPIC',
        '-std=c11',
        '-O0',
        '-DPII_TEST_LAYOUT',
        '-I$core',
        ...sources,
        '-o',
        out,
        '-lm',
      ]);
      expect(r.exitCode, 0, reason: '${r.stderr}');
      lib = DynamicLibrary.open(out);
    });
    tearDownAll(() => tmp.deleteSync(recursive: true));

    test('Dart field offsets and sizes equal the core\'s', () {
      final size = lib.lookupFunction<Size Function(), int Function()>('pii_options_size');
      final tableSize = lib.lookupFunction<Size Function(), int Function()>('pii_table_options_size');
      final opt = lib.lookupFunction<Size Function(Int), int Function(int)>('pii_test_options_offset');
      final table = lib.lookupFunction<Size Function(Int), int Function(int)>('pii_test_table_options_offset');
      expect(sizeOf<PiiOptionsC>(), size());
      expect(sizeOf<PiiTableOptionsC>(), tableSize());
      final of = dartFields(dart, 'PiiOptionsC'), tf = dartFields(dart, 'PiiTableOptionsC');
      expect(optionSetters.keys, [for (final f in of) f.$2]);
      expect(tableSetters.keys, [for (final f in tf) f.$2]);
      var i = 0;
      for (final MapEntry(:key, :value) in optionSetters.entries) {
        expect(offsetOf<PiiOptionsC>(sizeOf<PiiOptionsC>(), (p) => value(p.ref)), opt(i++), reason: 'PiiOptionsC.$key');
      }
      expect(opt(i), -1, reason: 'SIZE_MAX past the last field: the core has no field Dart lacks');
      i = 0;
      for (final MapEntry(:key, :value) in tableSetters.entries) {
        expect(
          offsetOf<PiiTableOptionsC>(sizeOf<PiiTableOptionsC>(), (p) => value(p.ref)),
          table(i++),
          reason: 'PiiTableOptionsC.$key',
        );
      }
      expect(table(i), -1);
    });
  }, skip: Platform.isWindows ? 'needs cc' : false);
}
