// Calls into the native core. These functions block; NewmeanPii runs them in a background isolate. A pii_model and a
// pii_lists are immutable after creation, so any number of isolates may detect on them at once.
import 'dart:convert';
import 'dart:ffi';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';

import 'ffi.dart';
import 'regex.dart';
import 'span.dart';

String _error(int code) => piiStrerror(code).toDartString();

Pointer<Uint8> _copy(List<int> bytes, Allocator allocator) {
  final p = allocator<Uint8>(bytes.isEmpty ? 1 : bytes.length);
  p.asTypedList(bytes.length).setAll(0, bytes);
  return p;
}

/// Loads a model from verified file bytes; returns the pii_model address.
int loadModel(Uint8List onnx, Uint8List tokenizer) => using((arena) {
  const errLen = 512;
  final err = arena<Char>(errLen);
  final m = piiModelLoad(_copy(onnx, arena), onnx.length, _copy(tokenizer, arena), tokenizer.length, err, errLen);
  if (m.address == 0) throw StateError('newmean_pii: model failed to load: ${err.cast<Utf8>().toDartString()}');
  return m.address;
});

void freeModel(int model) => piiModelFree(Pointer<PiiModel>.fromAddress(model));

/// Label bit set of the core (PII_LABEL_BIT per label); unknown names are ignored, as in the npm package.
int labelMask(Iterable<String> labels) {
  var mask = 0;
  for (final l in labels) {
    final i = piiLabels.indexOf(l);
    if (i >= 0) mask |= 1 << i;
  }
  return mask;
}

String _source(int bits) => switch (bits) {
  piiSourceModel => 'model',
  piiSourceRegex => 'regex',
  const (piiSourceModel | piiSourceRegex) => 'model+regex',
  piiSourceTable => 'table',
  piiSourceBlock => 'block',
  _ => throw StateError('newmean_pii: unknown span source $bits'),
};

// The Dart VM lays PiiOptionsC and PiiTableOptionsC out from their annotations; the core must agree before pii_options_init
// writes struct_size bytes into a Dart allocation (flutter/test/layout_test.dart checks the fields on the host).
final bool _layout = () {
  final (o, lo) = (sizeOf<PiiOptionsC>(), piiOptionsSize());
  final (t, lt) = (sizeOf<PiiTableOptionsC>(), piiTableOptionsSize());
  if (o != lo || t != lt) {
    throw StateError(
      'newmean_pii: the native core does not match this package (pii_options $lo bytes, PiiOptionsC $o; '
      'pii_table_options $lt bytes, PiiTableOptionsC $t)',
    );
  }
  return true;
}();

/// Throws [StateError] when the native core's option structs differ in size from this package's bindings.
void checkLayout() => _layout;

/// pii_table_options.delimiter of a `NewmeanPii.detectTable` delimiter ("auto", ",", "\t", ";" or "markdown").
int tableDelimiter(String delimiter) => switch (delimiter) {
  'auto' => 0,
  ',' || '\t' || ';' => delimiter.codeUnitAt(0),
  'markdown' => 0x7C,
  _ => throw ArgumentError.value(delimiter, 'delimiter', 'auto, ",", "\\t", ";" or markdown'),
};

/// pii_table_options.context of a `NewmeanPii.detectTable` context ("none" or "header").
int tableContext(String context) => switch (context) {
  'none' => piiTableContextNone,
  'header' => piiTableContextHeader,
  _ => throw ArgumentError.value(context, 'context', 'none or header'),
};

/// Spans to mask in [text] (UTF-16 offsets): the model's spans, merged with the pattern layer's when [regex] is true,
/// filtered by [labels] (a [labelMask]); [keepEmoji], [wholeWords], [strictShortNames]: pii_options.keep_emoji, whole_words,
/// strict_short_names; [lists]: a pii_lists address (0: none). Same steps as detect() of the npm package.
/// With [table] (a [tableDelimiter] code, header, vote, a [tableContext] code): table mode (pii_detect_table, detectTable()
/// of the npm package).
List<PiiSpan> detectSpans(
  int model,
  String text,
  double sensitivity,
  int labels,
  bool regex,
  bool keepEmoji,
  bool wholeWords,
  bool strictShortNames,
  int lists, [
  (int, bool, bool, int)? table,
]) => using((arena) {
  checkLayout();
  // Lone surrogates become U+FFFD (one UTF-16 unit either way), so offsets stay Dart string indices.
  final bytes = utf8.encode(text);
  final opts = arena<PiiOptionsC>();
  piiOptionsInit(opts);
  opts.ref
    ..sensitivity = sensitivity
    ..labels = labels
    ..keepEmoji = keepEmoji ? 1 : 0
    ..wholeWords = wholeWords ? 1 : 0
    ..strictShortNames = strictShortNames ? 1 : 0
    ..lists = Pointer<PiiLists>.fromAddress(lists);
  if (regex) {
    final extra = regexSpans(text);
    if (extra.isNotEmpty) {
      final p = arena<PiiExtraSpanC>(extra.length);
      for (var i = 0; i < extra.length; i++) {
        p[i]
          ..start = extra[i].start
          ..end = extra[i].end
          ..label = piiLabels.indexOf(extra[i].label);
      }
      opts.ref
        ..extra = p
        ..nExtra = extra.length
        ..extraUnit = piiUnitUtf16;
    }
  }
  final out = arena<Pointer<PiiSpanC>>();
  final n = arena<Size>();
  final m = Pointer<PiiModel>.fromAddress(model), text8 = _copy(bytes, arena);
  final int rc;
  if (table == null) {
    rc = piiDetect(m, text8, bytes.length, opts, out, n);
  } else {
    final t = arena<PiiTableOptionsC>();
    piiTableOptionsInit(t);
    t.ref
      ..delimiter = table.$1
      ..header = table.$2 ? 1 : 0
      ..vote = table.$3 ? 1 : 0
      ..context = table.$4;
    rc = piiDetectTable(m, text8, bytes.length, opts, t, out, n);
  }
  if (rc != piiOk) throw StateError('newmean_pii: detect failed: ${_error(rc)}');
  final spans = <PiiSpan>[];
  for (var i = 0; i < n.value; i++) {
    final s = out.value[i];
    spans.add(PiiSpan(s.u16Start, s.u16End, piiLabels[s.label], _source(s.source)));
  }
  if (out.value.address != 0) piiSpansFree(out.value);
  return spans;
});
