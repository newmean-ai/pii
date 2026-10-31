// Redaction modes of the npm package's applyRedaction.

/// How `redact` replaces each span: `.tag` → "[EMAIL]" (label family: PERSON_L2 → [PERSON], URL_L2 → [URL]),
/// `.mask` → "*****" (one per Unicode scalar), `.mask(with: "•")`, `.blank` → removed, or `.custom { span, original in … }`.
public struct PIIRedactionMode: Sendable {
  enum Kind: Sendable {
    case tag
    case mask(String)
    case blank
    case custom(@Sendable (PIISpan, String) -> String)
  }

  let kind: Kind

  public static let tag = PIIRedactionMode(kind: .tag)
  public static let mask = PIIRedactionMode(kind: .mask("*"))
  public static let blank = PIIRedactionMode(kind: .blank)
  /// One `maskString` per Unicode scalar of the original (npm `maskChar`).
  public static func mask(with maskString: String) -> PIIRedactionMode { PIIRedactionMode(kind: .mask(maskString)) }
  /// Custom replacement: the span and the original text it covers (npm `replace`).
  public static func custom(_ replace: @escaping @Sendable (PIISpan, String) -> String) -> PIIRedactionMode {
    PIIRedactionMode(kind: .custom(replace))
  }
}

extension PIIRedactor {
  /// Replaces `spans` (sorted, non-overlapping, detected in `text`) as `mode` says (npm `applyRedaction`).
  public static func applyRedaction(to text: String, spans: [PIISpan], mode: PIIRedactionMode = .tag) -> String {
    let scalars = text.unicodeScalars
    var out = String.UnicodeScalarView()
    var last = scalars.startIndex
    for s in spans {
      out.append(contentsOf: scalars[last..<s.range.lowerBound])
      let original = scalars[s.range]
      switch mode.kind {
      case .tag: out.append(contentsOf: "[\(s.label.family.rawValue)]".unicodeScalars)
      case .mask(let m): for _ in 0..<original.count { out.append(contentsOf: m.unicodeScalars) }
      case .blank: break
      case .custom(let replace): out.append(contentsOf: replace(s, String(original)).unicodeScalars)
      }
      last = s.range.upperBound
    }
    out.append(contentsOf: scalars[last...])
    return String(out)
  }
}
