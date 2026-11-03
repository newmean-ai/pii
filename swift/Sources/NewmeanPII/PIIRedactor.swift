// Detects and masks PII with a newmean-pii-<size> model on device: the Swift face of the npm package's createRedactor,
// over the shared C core (tokenizer, engine and decoding in native/core).
import CNewmeanPII
import Foundation

/// A loaded model. Immutable after loading except `sensitivity`; any number of tasks or threads may call `detect`,
/// `redact`, `detectTable`, `redactTable` and `tokenize` at once.
public final class PIIRedactor: @unchecked Sendable {
  public let size: PIISize
  public let variant: PIIVariant
  /// Model release ("1.1.0"), not the package version: see `info.packageVersion`.
  public let version = PIIModelFile.version
  /// Labels to mask.
  public let labels: Set<PIILabel>
  /// Whether the pattern layer (PIIRegex) runs next to the model.
  public let regex: Bool
  /// Whether the model reads emoji (see `init`).
  public let keepEmoji: Bool
  /// Whether spans are widened to whole words (see `init`).
  public let wholeWords: Bool
  /// Whether short texts need stronger evidence to be read as names (see `init`).
  public let strictShortNames: Bool
  /// Package and model versions, size, variant and the two checked model files.
  public var info: PIIRedactorInfo {
    PIIRedactorInfo(packageVersion: Self.packageVersion, modelVersion: version, size: size, variant: variant, engine: "native",
                    model: .model(size, variant), tokenizer: .tokenizer(size), build: Self.buildInfo)
  }
  /// Kernel build of the C core ("neon", "sse", ...).
  public static var buildInfo: String { String(cString: pii_build_info()) }
  /// Version of this package and its C core ("1.3.0").
  public static var packageVersion: String { String(cString: pii_version()) }

  private let model: OpaquePointer
  private let labelBits: UInt32
  private let lock = NSLock()
  private var _sensitivity: Double

  /// Loads a model.
  /// - Parameters:
  ///   - size: nano (≈2.6 MB), small (≈7 MB), medium (≈11 MB, default) or large (≈15 MB).
  ///   - variant: weight file; default q4e for large and medium, q4 for small and nano.
  ///   - sensitivity: moves the decision threshold: > 0 masks more (fewer misses, more over-masking), < 0 masks less.
  ///     Range about -1...1. Default 0.
  ///   - labels: labels to mask. Default all; `PIILabel.level1` leaves level 2 identifiers visible.
  ///   - regex: also mask matches of a few patterns (email, URL, card/account, phone, Korean resident number). Default
  ///     false: on the public test of newmean-pii-dataset they add almost no coverage and double over-masking.
  ///   - keepEmoji: false (default): emoji are taken out of the text the model reads, so the model does not read them as PII; spans keep the
  ///     offsets of the input (an emoji inside a span splits it). true: the model reads the text as given (1.0 behaviour).
  ///   - wholeWords: true: a span that starts or ends inside a word is widened to the whole word (
  ///     "R23", not "[ID_NUMBER]23"). A word is a run of letters, digits and marks joined by apostrophes and hyphens; letters of
  ///     scripts written without spaces (Chinese, Japanese, Thai, Khmer, ...) never extend a span, and a word ends where
  ///     Hangul meets digits or Latin letters ("010-1234-5678입니다"); Korean particles after a
  ///     Hangul span stay ("홍길동님께"). Default true (since 1.3.0);
  ///     false: spans end where the model ends them (1.2 behaviour).
  ///   - strictShortNames: true: on a text the model reads as at most 8 tokens (a survey answer, a table cell), the model
  ///     needs stronger evidence to read it as a person's name, so short values such as "경기", "남성" or "네이버" are left
  ///     alone; a lone name is missed a little more often (large: 98.9% → 96.5% of lone names found). Longer texts are
  ///     unchanged. Default false.
  ///   - modelDirectory: folder with `tokenizer.web.json` and `onnx/model_<variant>.onnx` (or `model_<variant>.onnx`
  ///     next to it), for example a folder in the app bundle. Default nil: `PIIModelStore.default`, which downloads the
  ///     files from Hugging Face once into Caches. Files are checked against the published SHA-256 either way.
  public init(size: PIISize = .default, variant: PIIVariant? = nil, sensitivity: Double = 0, labels: Set<PIILabel> = PIILabel.all,
              regex: Bool = false, keepEmoji: Bool = false, wholeWords: Bool = true, strictShortNames: Bool = false,
              modelDirectory: URL? = nil) async throws {
    let variant = variant ?? size.defaultVariant
    let dir: URL
    if let modelDirectory {
      dir = modelDirectory
    } else {
      dir = try await PIIModelStore.default.download(size, variant: variant)
    }
    self.model = try await Self.offCaller { try Self.load(dir, size, variant) }.pointer
    self.size = size
    self.variant = variant
    self.labels = labels
    self.labelBits = labels.reduce(0) { $0 | (1 << UInt32($1.id)) }
    self.regex = regex
    self.keepEmoji = keepEmoji
    self.wholeWords = wholeWords
    self.strictShortNames = strictShortNames
    self._sensitivity = sensitivity
  }

  deinit {
    pii_model_free(model)
  }

  /// Changes the threshold without reloading the model (npm `setSensitivity`).
  public var sensitivity: Double {
    get {
      lock.lock()
      defer { lock.unlock() }
      return _sensitivity
    }
    set {
      lock.lock()
      _sensitivity = newValue
      lock.unlock()
    }
  }

  /// Spans to mask, sorted and non-overlapping. Runs off the caller's actor.
  public func detect(_ text: String) async throws -> [PIISpan] {
    try await Self.offCaller { try self.detect(text) }
  }

  /// Spans to mask, sorted and non-overlapping; blocks the calling thread (about 15 ms per short text on a recent phone
  /// for medium). Async functions get the async overload; top-level code in main.swift gets this one.
  public func detect(_ text: String) throws -> [PIISpan] {
    try spans(text, table: nil)
  }

  /// Table mode for CSV, TSV, semicolon-separated and markdown pipe tables (npm `detectTable`): the whole text is read at
  /// once (cells give each other context), spans are clipped to cell values, and a column of short values that the model
  /// reads as names, emails, phone numbers or usernames almost throughout is masked whole (source `.table`; placeholders
  /// such as "N/A" are skipped). Text that is not a table gives the spans of `detect`. Runs off the caller's actor.
  /// - Parameters:
  ///   - delimiter: default `.auto`.
  ///   - header: the first row is a header row, never voted on or propagated into. Default true.
  ///   - vote: column vote and propagation; false: whole-text detection clipped to the cells only. Default true.
  ///   - context: `.header` also reads the cells of distinct-valued columns as "<header>: <value>". Default `.none`.
  public func detectTable(_ text: String, delimiter: PIITableDelimiter = .auto, header: Bool = true, vote: Bool = true,
                          context: PIITableContext = .none) async throws -> [PIISpan] {
    try await Self.offCaller { try self.detectTable(text, delimiter: delimiter, header: header, vote: vote, context: context) }
  }

  /// Table mode (see the async overload); blocks the calling thread.
  public func detectTable(_ text: String, delimiter: PIITableDelimiter = .auto, header: Bool = true, vote: Bool = true,
                          context: PIITableContext = .none) throws -> [PIISpan] {
    var table = pii_table_options()
    pii_table_options_init(&table)
    table.delimiter = delimiter.code
    table.header = header ? 1 : 0
    table.vote = vote ? 1 : 0
    table.context = context.code
    return try spans(text, table: table)
  }

  /// `detectTable` and the masked text (npm `redactTable`). Spans never cross cells, so delimiters, quotes and line breaks
  /// are kept and the output stays a valid table, as long as a `.custom` replacement does not insert the delimiter, a
  /// quote or a line break. Runs off the caller's actor.
  public func redactTable(_ text: String, delimiter: PIITableDelimiter = .auto, header: Bool = true, vote: Bool = true,
                          context: PIITableContext = .none, mode: PIIRedactionMode = .tag) async throws -> (text: String, spans: [PIISpan]) {
    let spans = try await detectTable(text, delimiter: delimiter, header: header, vote: vote, context: context)
    return (Self.applyRedaction(to: text, spans: spans, mode: mode), spans)
  }

  /// `redactTable`; blocks the calling thread.
  public func redactTable(_ text: String, delimiter: PIITableDelimiter = .auto, header: Bool = true, vote: Bool = true,
                          context: PIITableContext = .none, mode: PIIRedactionMode = .tag) throws -> (text: String, spans: [PIISpan]) {
    let spans = try detectTable(text, delimiter: delimiter, header: header, vote: vote, context: context)
    return (Self.applyRedaction(to: text, spans: spans, mode: mode), spans)
  }

  /// pii_detect, or pii_detect_table with `table`.
  private func spans(_ text: String, table: pii_table_options?) throws -> [PIISpan] {
    let extra = regex ? PIIRegex.utf16Matches(in: text).map { pii_extra_span(start: $0.range.lowerBound, end: $0.range.upperBound, label: $0.label.id) } : []
    var options = pii_options()
    pii_options_init(&options)
    options.sensitivity = sensitivity
    options.labels = labelBits
    options.extra_unit = Int32(PII_UNIT_UTF16)
    options.keep_emoji = keepEmoji ? 1 : 0
    options.whole_words = wholeWords ? 1 : 0
    options.strict_short_names = strictShortNames ? 1 : 0
    var out: UnsafeMutablePointer<pii_span>?
    var n = 0
    let rc = text.withContiguousUTF8 { utf8 in
      extra.withUnsafeBufferPointer { x in
        options.extra = x.baseAddress
        options.n_extra = x.count
        return utf8.withMemoryRebound(to: CChar.self) { p in
          guard var table else { return pii_detect(model, p.baseAddress, p.count, &options, &out, &n) }
          return pii_detect_table(model, p.baseAddress, p.count, &options, &table, &out, &n)
        }
      }
    }
    defer { pii_spans_free(out) }
    try PIIError.check(rc)
    let spans = UnsafeBufferPointer(start: out, count: n)
    let idx = text.indices(utf8Offsets: spans.flatMap { [$0.byte_start, $0.byte_end] })
    return spans.enumerated().map { i, s in
      PIISpan(range: idx[2 * i]..<idx[2 * i + 1], utf16Range: s.u16_start..<s.u16_end, scalarRange: s.cp_start..<s.cp_end,
              label: PIILabel.byID[Int(s.label)], source: PIISource(bits: s.source))
    }
  }

  /// The masked text and the spans (npm `redact`). Runs off the caller's actor.
  public func redact(_ text: String, mode: PIIRedactionMode = .tag) async throws -> (text: String, spans: [PIISpan]) {
    let spans = try await detect(text)
    return (Self.applyRedaction(to: text, spans: spans, mode: mode), spans)
  }

  /// The masked text and the spans; blocks the calling thread.
  public func redact(_ text: String, mode: PIIRedactionMode = .tag) throws -> (text: String, spans: [PIISpan]) {
    let spans = try detect(text)
    return (Self.applyRedaction(to: text, spans: spans, mode: mode), spans)
  }

  /// Token ids and offsets of the model's tokenizer (without `<s>` and `</s>`).
  public func tokenize(_ text: String) throws -> [PIIToken] {
    var out: UnsafeMutablePointer<pii_token>?
    var n = 0
    let rc = text.withContiguousUTF8 { utf8 in
      utf8.withMemoryRebound(to: CChar.self) { pii_tokenize(model, $0.baseAddress, $0.count, &out, &n) }
    }
    defer { pii_tokens_free(out) }
    try PIIError.check(rc)
    let tokens = UnsafeBufferPointer(start: out, count: n)
    let idx = text.indices(utf8Offsets: tokens.flatMap { [$0.byte_start, $0.byte_end] })
    return tokens.enumerated().map { i, t in
      PIIToken(id: Int(t.id), range: idx[2 * i]..<idx[2 * i + 1], utf16Range: t.u16_start..<t.u16_end, scalarRange: t.cp_start..<t.cp_end)
    }
  }

  // MARK: - Loading

  private struct ModelPointer: @unchecked Sendable {
    let pointer: OpaquePointer
  }

  private static func load(_ dir: URL, _ size: PIISize, _ variant: PIIVariant) throws -> ModelPointer {
    let tok = try PIIModelFile.tokenizer(size).read(from: dir)
    let onnx = try PIIModelFile.model(size, variant).read(from: dir)
    var err = [CChar](repeating: 0, count: 256)
    let m = onnx.withUnsafeBytes { o in
      tok.withUnsafeBytes { t in
        pii_model_load(o.bindMemory(to: UInt8.self).baseAddress, o.count, t.bindMemory(to: UInt8.self).baseAddress, t.count, &err, err.count)
      }
    }
    guard let m else { throw PIIError.loadFailed(String(decoding: err.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)) }
    return ModelPointer(pointer: m)
  }

  /// Runs blocking work on a detached task, so that the caller's actor (for example the main actor) stays free.
  private static func offCaller<T: Sendable>(_ body: @escaping @Sendable () throws -> T) async throws -> T {
    try Task.checkCancellation()
    return try await Task.detached(priority: Task.currentPriority) { try body() }.value
  }
}
