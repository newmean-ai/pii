// Deterministic patterns that run next to the model (`regex: true`), a port of js/src/regex.ts: the patterns of the
// Python baseline (training/scripts/evaluate.py REGEX) with Python's Unicode \w, \d and \b. ICU's \s differs from
// JavaScript's, so the JavaScript whitespace set is spelled out.
import Foundation

public enum PIIRegex {
  private static let w = #"[\p{L}\p{N}_]"#
  private static let d = #"\p{Nd}"#
  private static let b0 = "(?<!\(w))"  // \b before a digit
  private static let b1 = "(?!\(w))"  // \b after a digit
  /// JavaScript \s: WhiteSpace (TAB, VT, FF, ZWNBSP, Zs) and LineTerminator (LF, CR, LS, PS).
  private static let space = #"\t\n\x{0B}\f\r\x{FEFF}\x{2028}\x{2029}\p{Zs}"#

  /// (label, pattern) in rule order; a match that overlaps an earlier rule's match is skipped.
  public static let rules: [(label: PIILabel, pattern: String)] = [
    (.email, #"[\p{L}\p{N}_.+\-]+@[\p{L}\p{N}_\-]+(?:\.[\p{L}\p{N}_\-]+)+"#),
    (.url, "https?://[^\(space)]+|www\\.[^\(space)]+"),
    (.idNumber, "\(b0)\(d){6}[\\-\(space)]?[1-8]\(d){6}\(b1)"),
    (.financial, "\(b0)(?:\(d){4}[\\-\(space)]?){3}\(d){4}\(b1)|\(b0)\(d){2,6}-\(d){2,6}-\(d){2,8}(?:-\(d){1,3})?\(b1)"),
    (.phone, "(?:\\+\(d){1,3}[\(space)\\-]?)?\\(?\(d){2,4}\\)?[\(space).\\-]?\(d){3,4}[\(space).\\-]?\(d){4}\(b1)"),
  ]

  private static let compiled: [(PIILabel, NSRegularExpression)] = rules.map {
    ($0.label, try! NSRegularExpression(pattern: $0.pattern))  // constant patterns, checked by the tests
  }

  /// Pattern matches as UTF-16 ranges, in rule order.
  static func utf16Matches(in text: String) -> [(label: PIILabel, range: Range<Int>)] {
    let ns = text as NSString
    let all = NSRange(location: 0, length: ns.length)
    var out: [(label: PIILabel, range: Range<Int>)] = []
    for (label, re) in compiled {
      for m in re.matches(in: text, range: all) {
        let r = m.range.location..<(m.range.location + m.range.length)
        if out.contains(where: { r.overlaps($0.range) }) { continue }
        out.append((label, r))
      }
    }
    return out
  }

  /// Pattern matches in `text` (source `.regex`), in rule order, as npm `regexSpans`.
  public static func spans(in text: String) -> [PIISpan] {
    let matches = utf16Matches(in: text)
    // one pass over the text for every boundary: UTF-16 offset -> String.Index and scalar offset
    let bounds = Array(Set(matches.flatMap { [$0.range.lowerBound, $0.range.upperBound] })).sorted()
    var index: [Int: (String.Index, Int)] = [:]
    var at = text.startIndex
    var u16 = 0
    var scalars = 0
    for b in bounds {
      let next = text.utf16.index(at, offsetBy: b - u16)
      scalars += text.unicodeScalars.distance(from: at, to: next)
      at = next
      u16 = b
      index[b] = (at, scalars)
    }
    return matches.map { m in
      let lo = index[m.range.lowerBound]!, hi = index[m.range.upperBound]!
      return PIISpan(range: lo.0..<hi.0, utf16Range: m.range, scalarRange: lo.1..<hi.1, label: m.label, source: .regex)
    }
  }
}
