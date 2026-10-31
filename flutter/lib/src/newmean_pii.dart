import 'dart:async';
import 'dart:ffi';
import 'dart:isolate';
import 'dart:typed_data';

import 'ffi.dart';
import 'loader.dart';
import 'models.dart';
import 'native.dart';
import 'redaction.dart';
import 'span.dart';

/// Detects and masks personal information with a `newmean-pii-<size>` model, on the device. The model runs in the native
/// core; [detect] and [redact] run it in a background isolate, so the calling (UI) isolate never blocks.
final class NewmeanPii implements Finalizable {
  /// Model size: "nano", "small", "medium" or "large".
  final String size;

  /// Weight file: "q4" or "q4e".
  final String variant;

  /// Model release ([modelVersion]).
  final String version = modelVersion;

  /// Whether the pattern layer runs next to the model.
  final bool regex;

  final int _labels;
  Pointer<PiiModel>? _model;
  int _running = 0;
  Completer<void>? _idle, _disposed;

  static final _finalizer = NativeFinalizer(piiModelFreePointer);

  NewmeanPii._(this.size, this.variant, this.sensitivity, this._labels, this.regex, int model)
    : _model = Pointer<PiiModel>.fromAddress(model) {
    _finalizer.attach(this, _model!.cast(), detach: this);
  }

  /// Loads a model. Files come from [modelDir] (a folder with `tokenizer.web.json` and `onnx/model_<variant>.onnx`, as in
  /// the Hugging Face repository), from [readFile] (called with those two paths, e.g. to read Flutter assets), or are
  /// downloaded once from Hugging Face (or [modelUrl], a folder URL ending with "/") into [cacheDir]. Every file is
  /// checked against its published size and SHA-256 unless [verify] is false.
  ///
  /// - [size]: "nano" (≈2.6 MB), "small" (≈7.1 MB), "medium" (≈11.3 MB, default), "large" (≈15.5 MB).
  /// - [variant]: "q4" or "q4e"; default q4e for large and medium, q4 for small and nano ([defaultVariant]).
  /// - [sensitivity]: > 0 masks more (fewer misses, more over-masking), < 0 masks less; about −1..1, default 0.
  /// - [labels]: labels to mask, default all ([piiLabels]); [level1Labels] leaves level 2 identifiers visible.
  /// - [regex]: also mask a few patterns (email, URL, card/account, phone, Korean resident number). Default false: they
  ///   add almost no coverage on the public test and double over-masking.
  static Future<NewmeanPii> create({
    String size = defaultSize,
    String? variant,
    double sensitivity = 0,
    Iterable<String>? labels,
    bool regex = false,
    String? modelDir,
    Future<Uint8List> Function(String path)? readFile,
    Uri? modelUrl,
    String? cacheDir,
    bool cache = true,
    bool verify = true,
    void Function(PiiProgress progress)? onProgress,
  }) async {
    final v = _checkVariant(size, variant);
    final tokPin = modelFile(size, 'tokenizer');
    final onnxPin = modelFile(size, v);
    final mask = labelMask(labels ?? piiLabels);

    if (readFile != null) {
      final tok = await readFile(tokPin.path);
      final onnx = await readFile(onnxPin.path);
      onProgress?.call(PiiProgress(tokPin.path, tok.length, tok.length, true));
      onProgress?.call(PiiProgress(onnxPin.path, onnx.length, onnx.length, true));
      return fromBytes(
        onnx: onnx,
        tokenizer: tok,
        size: size,
        variant: v,
        sensitivity: sensitivity,
        labels: labels,
        regex: regex,
        verify: verify,
      );
    }

    ReceivePort? port;
    if (onProgress != null) {
      port = ReceivePort()
        ..listen((m) => onProgress(PiiProgress(m[0] as String, m[1] as int, m[2] as int, m[3] as bool)));
    }
    try {
      final model = await _loadInIsolate(tokPin, onnxPin, modelDir, modelUrl, cacheDir, cache, verify, port?.sendPort);
      return NewmeanPii._(size, v, sensitivity, mask, regex, model);
    } finally {
      port?.close();
    }
  }

  // A function of its own so that the closure sent to the isolate captures only these values: a closure made inside
  // create would share create's context, which holds onProgress and readFile (often not sendable, e.g. a setState call).
  static Future<int> _loadInIsolate(
    PiiModelFile tokPin,
    PiiModelFile onnxPin,
    String? modelDir,
    Uri? modelUrl,
    String? cacheDir,
    bool cache,
    bool verify,
    SendPort? send,
  ) => Isolate.run(() async {
    Future<Uint8List> load(PiiModelFile pin) => loadModelFile(
      pin,
      modelDir: modelDir,
      baseUrl: modelUrl,
      cacheDir: cacheDir,
      cache: cache,
      verify: verify,
      progress: send,
    );
    final files = await Future.wait([load(tokPin), load(onnxPin)]);
    return loadModel(files[1], files[0]);
  });

  /// Loads a model from the bytes of `onnx/model_<variant>.onnx` and `tokenizer.web.json` of [size] (checked against the
  /// pins of [size] and [variant] unless [verify] is false). Other options as in [create].
  static Future<NewmeanPii> fromBytes({
    required Uint8List onnx,
    required Uint8List tokenizer,
    String size = defaultSize,
    String? variant,
    double sensitivity = 0,
    Iterable<String>? labels,
    bool regex = false,
    bool verify = true,
  }) async {
    final v = _checkVariant(size, variant);
    final tokPin = modelFile(size, 'tokenizer');
    final onnxPin = modelFile(size, v);
    final model = await Isolate.run(() {
      if (verify) {
        if (!matchesPin(tokenizer, tokPin)) {
          throw StateError('newmean_pii: tokenizer does not match ${tokPin.path} of pii-$size');
        }
        if (!matchesPin(onnx, onnxPin)) {
          throw StateError('newmean_pii: model does not match ${onnxPin.path} of pii-$size');
        }
      }
      return loadModel(onnx, tokenizer);
    });
    return NewmeanPii._(size, v, sensitivity, labelMask(labels ?? piiLabels), regex, model);
  }

  static String _checkVariant(String size, String? variant) {
    if (!modelSizes.contains(size)) {
      throw ArgumentError.value(size, 'size', 'unknown size (nano, small, medium, large)');
    }
    final v = variant ?? defaultVariant[size]!;
    if (v != 'q4' && v != 'q4e') throw ArgumentError.value(variant, 'variant', 'q4 or q4e');
    return v;
  }

  /// Decision threshold (see [create]); changing it needs no reload and applies to later calls.
  double sensitivity;

  /// Same as setting [sensitivity] (the npm package's `setSensitivity`).
  void setSensitivity(double value) => sensitivity = value;

  int get _address {
    final m = _model;
    if (m == null) throw StateError('newmean_pii: this NewmeanPii has been disposed');
    return m.address;
  }

  /// Spans to mask in [text], sorted and non-overlapping, with UTF-16 offsets (Dart string indices). Runs in a background
  /// isolate; calls may overlap.
  Future<List<PiiSpan>> detect(String text) async {
    final job = _job(text);
    _running++;
    try {
      return await Isolate.run(job);
    } finally {
      if (--_running == 0) _idle?.complete();
    }
  }

  /// [detect] on the calling isolate (blocks it; for use inside your own background isolate).
  List<PiiSpan> detectSync(String text) => _job(text)();

  // Only plain values in the closure: it is sent to the background isolate.
  List<PiiSpan> Function() _job(String text) {
    final model = _address, sens = sensitivity, labels = _labels, rx = regex;
    return () => detectSpans(model, text, sens, labels, rx);
  }

  /// [text] with the spans of [detect] replaced: [RedactMode.tag] → "[EMAIL]", [RedactMode.mask] → "*****" (one
  /// [maskChar] per code point), [RedactMode.blank] → removed; [replace] overrides [mode].
  Future<PiiRedaction> redact(
    String text, {
    RedactMode mode = RedactMode.tag,
    String maskChar = '*',
    String Function(PiiSpan span, String original)? replace,
  }) async {
    final spans = await detect(text);
    return PiiRedaction(applyRedaction(text, spans, mode: mode, maskChar: maskChar, replace: replace), spans);
  }

  /// [redact] on the calling isolate.
  PiiRedaction redactSync(
    String text, {
    RedactMode mode = RedactMode.tag,
    String maskChar = '*',
    String Function(PiiSpan span, String original)? replace,
  }) {
    final spans = detectSync(text);
    return PiiRedaction(applyRedaction(text, spans, mode: mode, maskChar: maskChar, replace: replace), spans);
  }

  /// Frees the model after the running calls finish. Later calls throw [StateError].
  Future<void> dispose() async {
    final m = _model;
    if (m == null) return _disposed?.future;
    _model = null;
    final done = _disposed = Completer<void>();
    if (_running > 0) await (_idle = Completer<void>()).future;
    _finalizer.detach(this);
    freeModel(m.address);
    done.complete();
  }
}
