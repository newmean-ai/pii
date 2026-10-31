// Published model files and their pins. The sizes and SHA-256 values come from the native core (native/core/src/models.c,
// generated from js/src/models.ts), so every package checks against one table.
import 'dart:ffi';

import 'package:ffi/ffi.dart';

import 'ffi.dart';

/// Model release (Hugging Face tag v1.0.0 of `newmean-ai/pii-<size>`).
const String modelVersion = '1.0.0';

/// Default model size, as in the npm package.
const String defaultSize = 'medium';

/// Model sizes, smallest first: nano (≈2.6 MB), small (≈7.1 MB), medium (≈11.3 MB), large (≈15.5 MB), plus a 0.8 MB
/// tokenizer shared by all.
const List<String> modelSizes = ['nano', 'small', 'medium', 'large'];

/// Default weight file per size: q4e (4-bit embeddings too) for large and medium, q4 (8-bit embeddings) otherwise.
const Map<String, String> defaultVariant = {'large': 'q4e', 'medium': 'q4e', 'small': 'q4', 'nano': 'q4'};

/// A published file: [path] relative to the model folder (`onnx/model_q4e.onnx`, `tokenizer.web.json`), its size and
/// lowercase hex SHA-256.
final class PiiModelFile {
  final String size, file, path, sha256;
  final int bytes;
  const PiiModelFile(this.size, this.file, this.path, this.bytes, this.sha256);

  /// Download URL on Hugging Face (repository `newmean-ai/pii-<size>`, tag `v<modelVersion>`).
  Uri get url => Uri.parse('https://huggingface.co/newmean-ai/pii-$size/resolve/v$modelVersion/$path');

  @override
  String toString() => 'PiiModelFile($size, $file, $path, $bytes bytes)';
}

/// Pin of [file] ("q4", "q4e" or "tokenizer") of [size]; throws [ArgumentError] for an unknown size or file.
PiiModelFile modelFile(String size, String file) {
  if (!modelSizes.contains(size)) throw ArgumentError.value(size, 'size', 'unknown size (nano, small, medium, large)');
  if (!const ['q4', 'q4e', 'tokenizer'].contains(file)) throw ArgumentError.value(file, 'file', 'q4, q4e or tokenizer');
  return using((arena) {
    final p = piiModelFile(size.toNativeUtf8(allocator: arena), file.toNativeUtf8(allocator: arena));
    if (p.address == 0) throw ArgumentError('no pin for $size $file');
    final r = p.ref;
    return PiiModelFile(size, file, r.path.toDartString(), r.bytes, r.sha256.toDartString());
  });
}

/// Version of the native core library.
String get nativeVersion => piiVersion().toDartString();

/// Kernel build of the native core: "neon", "sse" or "scalar" (bit-identical results on every CPU).
String get nativeBuildInfo => piiBuildInfo().toDartString();
