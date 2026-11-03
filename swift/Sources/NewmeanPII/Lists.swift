// User lists (PIIRedactor options allow, block and listsId, added in 1.4.0): entry types, validation and the C handle.
// The same rules and the same reports as the npm package (js/src/lists.ts) and the C core (native/core/src/lists.c);
// rules: pii_lists_create in native/core/pii.h.
import CNewmeanPII
import Foundation

/// A value you know is not PII in your data: a span whose whole text is `text` (ASCII letters compared
/// case-insensitively, nothing else normalized) is not masked. `label` nil: any label; else only spans of its family
/// (`.person` covers `.personL2`, `.url` covers `.urlL2`). A string literal is an entry for any label.
public struct PIIAllowEntry: Sendable, Hashable, ExpressibleByStringLiteral {
  public let text: String
  public let label: PIILabel?

  public init(text: String, label: PIILabel? = nil) {
    self.text = text
    self.label = label
  }

  public init(stringLiteral value: String) {
    self.init(text: value)
  }
}

/// A value you know is PII: every occurrence that starts and ends on a word boundary (the `wholeWords` rules; a Hangul
/// entry also matches before Korean particles, 「박지훈님께」) is masked with `label` (source `.block`), whatever `labels`
/// enables, also where the model finds nothing.
public struct PIIBlockEntry: Sendable, Hashable {
  public let text: String
  public let label: PIILabel

  public init(text: String, label: PIILabel) {
    self.text = text
    self.label = label
  }
}

/// An invalid `allow`, `block` or `listsId` option (npm `ListError`). Carries where and why, never the entry's text.
public struct PIIListError: Error, LocalizedError, Sendable, Hashable {
  public enum List: String, Sendable, Hashable {
    case allow, block
    /// `listsId`.
    case meta
  }

  public enum Reason: String, Sendable, Hashable, CaseIterable {
    case badShape = "bad_shape"
    /// More than 256 UTF-16 code units or 1,024 UTF-8 bytes, or more than 64 Unicode scalars.
    case tooLong = "too_long"
    /// U+0000 or a line break (CR, LF, U+0085, U+2028, U+2029).
    case badText = "bad_text"
    case empty
    /// The first or last scalar is White_Space (entries are never trimmed).
    case edgeSpace = "edge_space"
    case unknownLabel = "unknown_label"
    /// More than 10,000 entries in one list (index 10,000).
    case tooMany = "too_many"
    /// Two block entries with the same compared form and different labels (index of the later one).
    case labelConflict = "label_conflict"
    /// The same compared form in allow and block (index in block).
    case inBoth = "in_both"
    /// `listsId` is not 1 to 128 scalars without U+0000 or line breaks.
    case badListsId = "bad_lists_id"
  }

  public let list: List
  /// Entry index in that list; nil for `listsId`.
  public let index: Int?
  public let reason: Reason

  public init(list: List, index: Int?, reason: Reason) {
    self.list = list
    self.index = index
    self.reason = reason
  }

  public var errorDescription: String? {
    "[NewmeanPII] \(list == .meta ? "listsId" : "\(list.rawValue) list")\(index.map { " entry \($0)" } ?? ""): \(reason.rawValue)"
  }
}

/// Entries of the lists after merging duplicates, and the caller's `listsId` (`PIIRedactor.info.lists`).
public struct PIIListsInfo: Sendable, Hashable {
  public let allow: Int
  public let block: Int
  public let id: String?
}

/// The validated lists: owns the C handle (immutable, shared by every call) and frees it when the redactor goes away.
final class PIIUserLists: @unchecked Sendable {
  static let maxEntries = 10_000
  /// nil when both lists are empty.
  let pointer: OpaquePointer?
  let allowCount: Int
  let blockCount: Int

  /// Validates in the order of the design (allow, then block, each entry completely before the next; then the checks
  /// across entries in the C core) and copies the entries into the core. Throws PIIListError.
  init(allow: [PIIAllowEntry], block: [PIIBlockEntry]) throws {
    try Self.validate(allow.map(\.text), .allow)
    try Self.validate(block.map(\.text), .block)
    if allow.isEmpty && block.isEmpty {
      (pointer, allowCount, blockCount) = (nil, 0, 0)
      return
    }
    // one buffer for every entry's UTF-8, then the terms pointing into it
    var bytes: [CChar] = []
    let encode = { (text: String) -> (Int, Int) in
      let start = bytes.count
      bytes.append(contentsOf: text.utf8.map { CChar(bitPattern: $0) })
      return (start, bytes.count - start)
    }
    let a = allow.map { (encode($0.text), $0.label?.id ?? -1) }
    let b = block.map { (encode($0.text), $0.label.id) }
    var out: OpaquePointer?
    var err = pii_list_error()
    let rc = bytes.withUnsafeBufferPointer { buf -> Int32 in
      let terms = { (xs: [((Int, Int), Int32)]) in xs.map { pii_term(utf8: buf.baseAddress! + $0.0.0, len: $0.0.1, label: $0.1) } }
      let (ta, tb) = (terms(a), terms(b))
      return ta.withUnsafeBufferPointer { pa in
        tb.withUnsafeBufferPointer { pb in pii_lists_create(pa.baseAddress, pa.count, pb.baseAddress, pb.count, &out, &err) }
      }
    }
    if rc == PII_ERR_ARG && err.reason != 0 {
      let reason = Self.reasons[Int(err.reason)] ?? .badShape
      throw PIIListError(list: err.list == PII_LIST_BLOCK ? .block : .allow, index: err.index, reason: reason)
    }
    try PIIError.check(rc)
    var na = 0, nb = 0
    pii_lists_counts(out, &na, &nb)
    (pointer, allowCount, blockCount) = (out, na, nb)
  }

  deinit {
    pii_lists_free(pointer)
  }

  /// PII_LIST_ERR_* of the C core.
  static let reasons: [Int: PIIListError.Reason] = [
    Int(PII_LIST_ERR_BAD_SHAPE): .badShape, Int(PII_LIST_ERR_TOO_LONG): .tooLong, Int(PII_LIST_ERR_BAD_TEXT): .badText,
    Int(PII_LIST_ERR_EMPTY): .empty, Int(PII_LIST_ERR_EDGE_SPACE): .edgeSpace, Int(PII_LIST_ERR_UNKNOWN_LABEL): .unknownLabel,
    Int(PII_LIST_ERR_TOO_MANY): .tooMany, Int(PII_LIST_ERR_LABEL_CONFLICT): .labelConflict, Int(PII_LIST_ERR_IN_BOTH): .inBoth,
  ]

  /// The first 10,000 entries in order, then too_many at index 10,000. Shapes and labels are typed in Swift, so only the
  /// text checks remain.
  static func validate(_ texts: [String], _ list: PIIListError.List) throws {
    for (i, text) in texts.prefix(maxEntries).enumerated() {
      if let reason = check(text) { throw PIIListError(list: list, index: i, reason: reason) }
    }
    if texts.count > maxEntries { throw PIIListError(list: list, index: maxEntries, reason: .tooMany) }
  }

  /// The text checks of an entry, in order (Swift strings hold no unpaired surrogates or malformed UTF-8).
  static func check(_ text: String) -> PIIListError.Reason? {
    if text.utf8.count > 1024 || text.utf16.count > 256 { return .tooLong }
    let s = text.unicodeScalars
    if s.contains(where: { $0.value == 0 || lineBreak($0.value) }) { return .badText }
    guard let first = s.first, let last = s.last else { return .empty }
    if s.count > 64 { return .tooLong }
    if whiteSpace(first.value) || whiteSpace(last.value) { return .edgeSpace }
    return nil
  }

  /// listsId: 1 to 128 scalars without U+0000 or a line break (checked first, before the lists).
  static func checkID(_ id: String?) throws {
    guard let id else { return }
    let s = id.unicodeScalars
    if s.isEmpty || s.count > 128 || s.contains(where: { $0.value == 0 || lineBreak($0.value) }) {
      throw PIIListError(list: .meta, index: nil, reason: .badListsId)
    }
  }

  static func lineBreak(_ c: UInt32) -> Bool { c == 0x0a || c == 0x0d || c == 0x85 || c == 0x2028 || c == 0x2029 }

  /// The 25 White_Space code points of Unicode 17.0.
  static func whiteSpace(_ c: UInt32) -> Bool {
    (0x09...0x0d).contains(c) || c == 0x20 || c == 0x85 || c == 0xa0 || c == 0x1680 || (0x2000...0x200a).contains(c) ||
      c == 0x2028 || c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x3000
  }
}
