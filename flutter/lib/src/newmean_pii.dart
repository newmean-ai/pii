import 'dart:async';
import 'dart:ffi';
import 'dart:isolate';
import 'dart:typed_data';

import 'ffi.dart';
import 'lists.dart';
import 'loader.dart';
import 'models.dart';
import 'native.dart';
import 'redaction.dart';
import 'span.dart';

/// Detects and masks personal information with a `newmean-pii-<size>` model, on the device. The model runs in the native
/// core; [detect], [redact], [detectTable] and [redactTable] run it in a background isolate, so the calling (UI) isolate
/// never blocks.
final class NewmeanPii implements Finalizable {
  /// Model size: "nano", "small", "medium" or "large".
  final String size;

  /// Weight file: "q4" or "q4e".
  final String variant;

  /// Model release ([modelVersion]), not the package version: see [info].
  final String version = modelVersion;

  /// Whether the pattern layer runs next to the model.
  final bool regex;

  /// Whether the model reads emoji (see [create]).
  final bool keepEmoji;

  /// Whether spans are widened to whole words (see [create]).
  final bool wholeWords;

  /// Whether short texts need stronger evidence to be read as names (see [create]).
  final bool strictShortNames;

  /// Package and model versions, size, variant, the two model files with the size and SHA-256 the loaded bytes were
  /// checked against (unless `verify` was false), and the entries of the user lists.
  PiiRedactorInfo get info => PiiRedactorInfo._(
    nativeVersion,
    modelVersion,
    size,
    variant,
    modelFile(size, variant),
    modelFile(size, 'tokenizer'),
    nativeBuildInfo,
    _listsInfo,
  );

  final int _labels;
  final PiiListsInfo _listsInfo;
  Pointer<PiiModel>? _model;
  int _lists; // pii_lists address, 0 when there are no entries
  int _running = 0;
  Completer<void>? _idle, _disposed;

  static final _finalizer = NativeFinalizer(piiModelFreePointer);
  static final _listsFinalizer = NativeFinalizer(piiListsFreePointer);

  NewmeanPii._(
    this.size,
    this.variant,
    this._sensitivity,
    this._labels,
    this.regex,
    this.keepEmoji,
    this.wholeWords,
    this.strictShortNames,
    int model,
    (int, int, int) lists,
    String? listsId,
  ) : _model = Pointer<PiiModel>.fromAddress(model),
      _lists = lists.$1,
      _listsInfo = PiiListsInfo(lists.$2, lists.$3, listsId) {
    _finalizer.attach(this, _model!.cast(), detach: this);
    if (_lists != 0) _listsFinalizer.attach(this, Pointer<Void>.fromAddress(_lists), detach: this);
  }

  /// Loads a model. Files come from [modelDir] (a folder with `tokenizer.web.json` and `onnx/model_<variant>.onnx`, as in
  /// the Hugging Face repository), from [readFile] (called with those two paths, e.g. to read Flutter assets), or are
  /// downloaded once from Hugging Face (or [modelUrl], a folder URL ending with "/") into [cacheDir]. Every file is
  /// checked against its published size and SHA-256 unless [verify] is false.
  ///
  /// - [size]: "nano" (≈2.6 MB), "small" (≈7.1 MB), "medium" (≈11.3 MB, default), "large" (≈15.5 MB).
  /// - [variant]: "q4" or "q4e"; default q4e for large and medium, q4 for small and nano ([defaultVariant]).
  /// - [sensitivity]: > 0 masks more (fewer misses, more over-masking), < 0 masks less; about −1..1, default 0. NaN and
  ///   ±infinity throw [ArgumentError].
  /// - [labels]: labels to mask, default all ([piiLabels]); [level1Labels] leaves level 2 identifiers visible. Names that
  ///   are not labels are ignored, as in the npm package.
  /// - [regex]: also mask a few patterns (email, URL, card/account, phone, Korean resident number). Default false: they
  ///   add almost no coverage on the public test and double over-masking.
  /// - [keepEmoji]: false (default): emoji are taken out of the text the model reads, so the model does not read them as PII; spans keep the
  ///   offsets of the input (an emoji inside a span splits it). true: the model reads the text as given (1.0 behaviour).
  /// - [wholeWords]: true: a span that starts or ends inside a word is widened to the whole word
  ///   ("R23", not "[ID_NUMBER]23"). A word is a run of letters, digits and marks joined by apostrophes and
  ///   hyphens; letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, ...) never extend a span, and a
  ///   word ends where Hangul meets digits or Latin letters ("010-1234-5678입니다");
  ///   Korean particles after a Hangul span stay ("홍길동님께"). Default true (since 1.3.0); false: spans end where the model ends them (1.2 behaviour).
  /// - [strictShortNames]: true: on a text the model reads as at most 8 tokens (a survey answer, a table cell), the model
  ///   needs stronger evidence to read it as a person's name, so short values such as "경기", "남성" or "네이버" are left
  ///   alone; a lone name is missed a little more often (large: 98.9% → 96.5% of lone names found). Longer texts are
  ///   unchanged. Default false.
  /// - [allow]: values you know are not PII in your data (survey categories: regions, brands, jobs). A span whose whole
  ///   text is an entry is not masked; with a label, only spans of that label family (PERSON covers PERSON_L2, URL covers
  ///   URL_L2). ASCII letters are compared case-insensitively, nothing else is normalized: pass NFC text (files exported
  ///   on macOS may hold NFD Hangul). At most 10,000 entries of 1 to 64 code points. Since 1.4.0.
  /// - [block]: values you know are PII (an instructor's name): every occurrence that starts and ends on a word boundary
  ///   is masked with the entry's label (source "block"), also where the model misses it and whatever [labels] enables. A
  ///   Hangul entry also matches before Korean particles ("박지훈님께" → "[PERSON]님께"); a one-syllable entry such as
  ///   "김" therefore masks "김은" and "김도" too. Exact forms only: "박 지훈" and "박지훈" are separate entries. Since 1.4.0.
  /// - [listsId]: your name for the lists (1 to 128 code points), shown in `info.lists.id` for audit records; never
  ///   derived from the entries. Since 1.4.0.
  ///
  /// The lists and [listsId] are checked before any model file is read; an invalid one throws [PiiListException] (list,
  /// index and reason, never the entry's text).
  static Future<NewmeanPii> create({
    String size = defaultSize,
    String? variant,
    double sensitivity = 0,
    Iterable<String>? labels,
    bool regex = false,
    bool keepEmoji = false,
    bool wholeWords = true,
    bool strictShortNames = false,
    Iterable<PiiAllowEntry>? allow,
    Iterable<PiiBlockEntry>? block,
    String? listsId,
    String? modelDir,
    Future<Uint8List> Function(String path)? readFile,
    Uri? modelUrl,
    String? cacheDir,
    bool cache = true,
    bool verify = true,
    void Function(PiiProgress progress)? onProgress,
  }) async {
    checkLayout(); // a core of another release fails here, before any file is read
    final v = _checkVariant(size, variant);
    final tokPin = modelFile(size, 'tokenizer');
    final onnxPin = modelFile(size, v);
    final mask = labelMask(labels ?? piiLabels);

    final lists = _lists0(allow, block, listsId); // before any model file is read
    ReceivePort? port;
    try {
      _checkSensitivity(sensitivity); // after the lists, as in every package
      final int model;
      if (readFile != null) {
        final tok = await readFile(tokPin.path);
        final onnx = await readFile(onnxPin.path);
        onProgress?.call(PiiProgress(tokPin.path, tok.length, tok.length, true));
        onProgress?.call(PiiProgress(onnxPin.path, onnx.length, onnx.length, true));
        model = await _loadBytes(onnx, tok, size, tokPin, onnxPin, verify);
      } else {
        if (onProgress != null) {
          port = ReceivePort()
            ..listen((m) => onProgress(PiiProgress(m[0] as String, m[1] as int, m[2] as int, m[3] as bool)));
        }
        model = await _loadInIsolate(tokPin, onnxPin, modelDir, modelUrl, cacheDir, cache, verify, port?.sendPort);
      }
      return NewmeanPii._(
        size,
        v,
        sensitivity,
        mask,
        regex,
        keepEmoji,
        wholeWords,
        strictShortNames,
        model,
        lists,
        listsId,
      );
    } catch (_) {
      freeLists(lists.$1);
      rethrow;
    } finally {
      port?.close();
    }
  }

  // listsId first, then the lists: before any model file is read.
  static (int, int, int) _lists0(Iterable<PiiAllowEntry>? allow, Iterable<PiiBlockEntry>? block, String? listsId) {
    checkListsId(listsId);
    return createLists(allow, block);
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
  /// pins of [size] and [variant] unless [verify] is false). Other options as in [create]; the lists are checked before
  /// the model is loaded.
  static Future<NewmeanPii> fromBytes({
    required Uint8List onnx,
    required Uint8List tokenizer,
    String size = defaultSize,
    String? variant,
    double sensitivity = 0,
    Iterable<String>? labels,
    bool regex = false,
    bool keepEmoji = false,
    bool wholeWords = true,
    bool strictShortNames = false,
    Iterable<PiiAllowEntry>? allow,
    Iterable<PiiBlockEntry>? block,
    String? listsId,
    bool verify = true,
  }) async {
    checkLayout();
    final v = _checkVariant(size, variant);
    final tokPin = modelFile(size, 'tokenizer');
    final onnxPin = modelFile(size, v);
    final lists = _lists0(allow, block, listsId); // before the model is loaded
    try {
      _checkSensitivity(sensitivity);
      final model = await _loadBytes(onnx, tokenizer, size, tokPin, onnxPin, verify);
      return NewmeanPii._(
        size,
        v,
        sensitivity,
        labelMask(labels ?? piiLabels),
        regex,
        keepEmoji,
        wholeWords,
        strictShortNames,
        model,
        lists,
        listsId,
      );
    } catch (_) {
      freeLists(lists.$1);
      rethrow;
    }
  }

  // Of its own for the same reason as _loadInIsolate.
  static Future<int> _loadBytes(
    Uint8List onnx,
    Uint8List tokenizer,
    String size,
    PiiModelFile tokPin,
    PiiModelFile onnxPin,
    bool verify,
  ) => Isolate.run(() {
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

  static String _checkVariant(String size, String? variant) {
    if (!modelSizes.contains(size)) {
      throw ArgumentError.value(size, 'size', 'unknown size (nano, small, medium, large)');
    }
    final v = variant ?? defaultVariant[size]!;
    if (v != 'q4' && v != 'q4e') throw ArgumentError.value(variant, 'variant', 'q4 or q4e');
    return v;
  }

  // NaN would switch the model off and ±infinity would mask everything or nothing (the core also refuses them).
  static double _checkSensitivity(double value) {
    if (!value.isFinite) throw ArgumentError.value(value, 'sensitivity', 'sensitivity must be a finite number');
    return value;
  }

  double _sensitivity;

  /// Decision threshold (see [create]); changing it needs no reload and applies to later calls. NaN and ±infinity throw
  /// [ArgumentError] and keep the previous value.
  double get sensitivity => _sensitivity;
  set sensitivity(double value) => _sensitivity = _checkSensitivity(value);

  /// Same as setting [sensitivity] (the npm package's `setSensitivity`).
  void setSensitivity(double value) => sensitivity = value;

  int get _address {
    final m = _model;
    if (m == null) throw StateError('newmean_pii: this NewmeanPii has been disposed');
    return m.address;
  }

  /// Spans to mask in [text], sorted and non-overlapping, with UTF-16 offsets (Dart string indices). Runs in a background
  /// isolate; calls may overlap.
  Future<List<PiiSpan>> detect(String text) async => _run(_job(text));

  /// [detect] on the calling isolate (blocks it; for use inside your own background isolate).
  List<PiiSpan> detectSync(String text) => _job(text)();

  /// Table mode for CSV, TSV, semicolon-separated and markdown pipe tables (the npm package's `detectTable`): the whole
  /// text is read at once (cells give each other context), spans are clipped to cell values, and a column of short values
  /// that the model reads as names, emails, phone numbers or usernames almost throughout is masked whole (source
  /// "table"; placeholders such as "N/A" are skipped). Text that is not a table gives the spans of [detect].
  ///
  /// - [delimiter]: "auto" (default; comma, tab, semicolon or markdown), ",", "\t", ";" or "markdown" (pipe table);
  ///   anything else throws [ArgumentError].
  /// - [header]: the first row is a header row, never voted on or propagated into. Default true.
  /// - [vote]: column vote and propagation; false: whole-text detection clipped to the cells only. Default true.
  /// - [context]: "header": with a header row, each non-empty data cell of a column whose non-empty values are at least
  ///   80% distinct is also read on its own as `"<header>: <value>"`, and those spans are added (fewer misses on name,
  ///   contact and ID columns, more PII-free cells masked, one more model call per such cell). Default "none"; anything
  ///   else throws [ArgumentError].
  Future<List<PiiSpan>> detectTable(
    String text, {
    String delimiter = 'auto',
    bool header = true,
    bool vote = true,
    String context = 'none',
  }) async => _run(_job(text, (tableDelimiter(delimiter), header, vote, tableContext(context))));

  /// [detectTable] on the calling isolate.
  List<PiiSpan> detectTableSync(
    String text, {
    String delimiter = 'auto',
    bool header = true,
    bool vote = true,
    String context = 'none',
  }) => _job(text, (tableDelimiter(delimiter), header, vote, tableContext(context)))();

  Future<List<PiiSpan>> _run(List<PiiSpan> Function() job) async {
    _running++;
    try {
      return await Isolate.run(job);
    } finally {
      if (--_running == 0) _idle?.complete();
    }
  }

  // Only plain values in the closure: it is sent to the background isolate.
  List<PiiSpan> Function() _job(String text, [(int, bool, bool, int)? table]) {
    final model = _address, sens = sensitivity, labels = _labels, rx = regex, keep = keepEmoji, words = wholeWords;
    final strict = strictShortNames, lists = _lists;
    return () => detectSpans(model, text, sens, labels, rx, keep, words, strict, lists, table);
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

  /// [text] with the spans of [detectTable] replaced, as [redact] does. Spans never cross cells, so delimiters, quotes and
  /// line breaks are kept and the output stays a valid table, as long as [replace] does not insert the delimiter, a quote
  /// or a line break.
  Future<PiiRedaction> redactTable(
    String text, {
    String delimiter = 'auto',
    bool header = true,
    bool vote = true,
    String context = 'none',
    RedactMode mode = RedactMode.tag,
    String maskChar = '*',
    String Function(PiiSpan span, String original)? replace,
  }) async {
    final spans = await detectTable(text, delimiter: delimiter, header: header, vote: vote, context: context);
    return PiiRedaction(applyRedaction(text, spans, mode: mode, maskChar: maskChar, replace: replace), spans);
  }

  /// [redactTable] on the calling isolate.
  PiiRedaction redactTableSync(
    String text, {
    String delimiter = 'auto',
    bool header = true,
    bool vote = true,
    String context = 'none',
    RedactMode mode = RedactMode.tag,
    String maskChar = '*',
    String Function(PiiSpan span, String original)? replace,
  }) {
    final spans = detectTableSync(text, delimiter: delimiter, header: header, vote: vote, context: context);
    return PiiRedaction(applyRedaction(text, spans, mode: mode, maskChar: maskChar, replace: replace), spans);
  }

  /// Frees the model and the lists after the running calls finish. Later calls throw [StateError].
  Future<void> dispose() async {
    final m = _model;
    if (m == null) return _disposed?.future;
    _model = null;
    final done = _disposed = Completer<void>();
    if (_running > 0) await (_idle = Completer<void>()).future;
    _finalizer.detach(this);
    _listsFinalizer.detach(this);
    freeModel(m.address);
    freeLists(_lists);
    _lists = 0;
    done.complete();
  }
}

/// What a redactor runs ([NewmeanPii.info]).
final class PiiRedactorInfo {
  /// Version of this package and its native core ("1.4.1").
  final String packageVersion;

  /// Model release ("1.1.0"; Hugging Face tag v1.1.0).
  final String modelVersion;

  final String size, variant;

  /// The weight file and the tokenizer, with their published size and SHA-256.
  final PiiModelFile model, tokenizer;

  /// Kernel build of the native core ("neon", "sse", ...).
  final String build;

  /// Entries of the allow and block lists after merging duplicates, and the `listsId` given to `create` (since 1.4.0).
  final PiiListsInfo lists;

  const PiiRedactorInfo._(
    this.packageVersion,
    this.modelVersion,
    this.size,
    this.variant,
    this.model,
    this.tokenizer,
    this.build,
    this.lists,
  );

  @override
  String toString() =>
      'PiiRedactorInfo(package $packageVersion, model $modelVersion, $size $variant, ${model.path} ${model.sha256}, ${tokenizer.path} ${tokenizer.sha256}, $build, $lists)';
}
