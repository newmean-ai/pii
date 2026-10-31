// Calls into the native core. These functions block; NewmeanPii runs them in a background isolate. A pii_model is
// immutable after loading, so any number of isolates may detect on one model at once.
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

const _sources = ['', 'model', 'regex', 'model+regex'];

/// Spans to mask in [text] (UTF-16 offsets): the model's spans, merged with the pattern layer's when [regex] is true,
/// filtered by [labels] (a [labelMask]). Same steps as detect() of the npm package.
List<PiiSpan> detectSpans(int model, String text, double sensitivity, int labels, bool regex) => using((arena) {
  // Lone surrogates become U+FFFD (one UTF-16 unit either way), so offsets stay Dart string indices.
  final bytes = utf8.encode(text);
  final opts = arena<PiiOptionsC>();
  piiOptionsInit(opts);
  opts.ref
    ..sensitivity = sensitivity
    ..labels = labels;
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
  final rc = piiDetect(Pointer<PiiModel>.fromAddress(model), _copy(bytes, arena), bytes.length, opts, out, n);
  if (rc != piiOk) throw StateError('newmean_pii: detect failed: ${_error(rc)}');
  final spans = <PiiSpan>[];
  for (var i = 0; i < n.value; i++) {
    final s = out.value[i];
    spans.add(PiiSpan(s.u16Start, s.u16End, piiLabels[s.label], _sources[s.source & 3]));
  }
  if (out.value.address != 0) piiSpansFree(out.value);
  return spans;
});
