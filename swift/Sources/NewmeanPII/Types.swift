// Public value types: sizes, variants, labels, spans, tokens and errors.
import CNewmeanPII
import Foundation

/// Model size: nano (≈2.6 MB), small (≈7 MB), medium (≈11 MB, default), large (≈15 MB).
public enum PIISize: String, Sendable, CaseIterable {
  case nano, small, medium, large

  /// The npm package's default size.
  public static let `default` = PIISize(rawValue: PII_DEFAULT_SIZE)!

  /// Default weight file, as in the npm package: q4e for large and medium, q4 for small and nano.
  public var defaultVariant: PIIVariant { PIIVariant(rawValue: String(cString: pii_default_variant(rawValue)))! }
}

/// Weight file: "q4" (4-bit layers, 8-bit embeddings) or "q4e" (4-bit embeddings too).
public enum PIIVariant: String, Sendable, CaseIterable {
  case q4, q4e
}

/// Labels of the v1 models. Level 2 = indirect identifiers: `personL2` (a surname with a title, a nickname, initials)
/// and `urlL2` (public profile or page URLs).
public enum PIILabel: String, Sendable, CaseIterable, Hashable {
  case person = "PERSON"
  case personL2 = "PERSON_L2"
  case email = "EMAIL"
  case phone = "PHONE"
  case address = "ADDRESS"
  case idNumber = "ID_NUMBER"
  case financial = "FINANCIAL"
  case org = "ORG"
  case username = "USERNAME"
  case url = "URL"
  case urlL2 = "URL_L2"
  case dateOfBirth = "DATE_OF_BIRTH"
  case secret = "SECRET"

  /// All labels (default).
  public static let all = Set(allCases)
  /// All but the level 2 labels (npm `LEVEL1_LABELS`).
  public static let level1 = all.filter { !$0.isLevel2 }

  public var isLevel2: Bool { self == .personL2 || self == .urlL2 }
  /// PERSON for PERSON_L2, URL for URL_L2, else the label itself (the "tag" redaction mode prints the family).
  public var family: PIILabel { self == .personL2 ? .person : self == .urlL2 ? .url : self }

  /// Label id in the C core.
  var id: Int32 { pii_label_from_name(rawValue) }
  static let byID: [PIILabel] = (0..<Int32(PII_LABEL_COUNT.rawValue)).map { PIILabel(rawValue: String(cString: pii_label_name($0)))! }
}

/// Where a span came from: the model, the pattern layer (`regex: true`), or both.
public enum PIISource: String, Sendable, Hashable {
  case model
  case regex
  case modelAndRegex = "model+regex"

  init(bits: Int32) {
    self = bits == PII_SOURCE_MODEL ? .model : bits == PII_SOURCE_REGEX ? .regex : .modelAndRegex
  }
}

/// A region of the text to mask.
public struct PIISpan: Sendable, Hashable {
  /// The region in the text it was detected in. It always falls on Unicode scalar boundaries but may end inside a
  /// Character (before a combining mark); slice with `text.unicodeScalars[range]` or `utf16Range`, as `redact` does.
  public let range: Range<String.Index>
  /// [start, end) in UTF-16 code units (NSString, NSRange, the npm package's `start` / `end`).
  public let utf16Range: Range<Int>
  /// [start, end) in Unicode scalars (code points; Python `str` offsets).
  public let scalarRange: Range<Int>
  public let label: PIILabel
  public let source: PIISource

  /// npm `start` / `end`: UTF-16 offsets.
  public var start: Int { utf16Range.lowerBound }
  public var end: Int { utf16Range.upperBound }

  public init(range: Range<String.Index>, utf16Range: Range<Int>, scalarRange: Range<Int>, label: PIILabel, source: PIISource) {
    self.range = range
    self.utf16Range = utf16Range
    self.scalarRange = scalarRange
    self.label = label
    self.source = source
  }

  /// A span over `utf16Range` of `text` (for custom spans; offsets must fall on scalar boundaries).
  public init(in text: String, utf16Range: Range<Int>, label: PIILabel, source: PIISource = .model) {
    let u = text.utf16
    let lo = u.index(u.startIndex, offsetBy: utf16Range.lowerBound)
    let hi = u.index(lo, offsetBy: utf16Range.count)
    let s = text.unicodeScalars
    let start = s.distance(from: s.startIndex, to: lo)
    self.init(range: lo..<hi, utf16Range: utf16Range, scalarRange: start..<(start + s.distance(from: lo, to: hi)), label: label, source: source)
  }
}

/// A token of the model's tokenizer (without `<s>` and `</s>`), with offsets as Hugging Face tokenizers gives them.
public struct PIIToken: Sendable, Hashable {
  public let id: Int
  public let range: Range<String.Index>
  public let utf16Range: Range<Int>
  public let scalarRange: Range<Int>
}

public enum PIIError: Error, LocalizedError, Sendable {
  /// A model file is missing from the model directory.
  case fileNotFound(URL)
  /// A model file does not match the published size and SHA-256.
  case verificationFailed(file: String, reason: String)
  /// Downloading a model file failed.
  case downloadFailed(URL, status: Int)
  /// The C core refused to load the files.
  case loadFailed(String)
  /// The C core returned an error code.
  case core(code: Int, message: String)

  public var errorDescription: String? {
    switch self {
    case .fileNotFound(let url): return "[NewmeanPII] model file not found: \(url.path)"
    case .verificationFailed(let file, let reason): return "[NewmeanPII] \(file) does not match the published model file (\(reason))"
    case .downloadFailed(let url, let status): return "[NewmeanPII] failed to download \(url.absoluteString): HTTP \(status)"
    case .loadFailed(let message): return "[NewmeanPII] failed to load the model: \(message)"
    case .core(let code, let message): return "[NewmeanPII] \(message) (\(code))"
    }
  }

  static func check(_ rc: Int32) throws {
    if rc != PII_OK { throw PIIError.core(code: Int(rc), message: String(cString: pii_strerror(rc))) }
  }
}
