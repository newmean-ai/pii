// Doubling tests of the vault (REVIEW-GUIDE rule 4), kept apart from VaultTests so that the test runner runs them alone,
// after the suites ("Growth" in the class name), where no other suite distorts the ratios. Timing: Growth.swift. The
// expressions carry their types: the type checker of an Intel macOS runner gave up on one without them.
import Foundation
import NewmeanPII
import XCTest

final class VaultGrowthTests: XCTestCase {
  /// `s` in full-width ASCII (U+FF01 to U+FF5E).
  private func fullWidth(_ s: String) -> String {
    var out = String.UnicodeScalarView()
    for c: Unicode.Scalar in s.unicodeScalars {
      let v: UInt32 = c.value
      out.append(v >= 0x21 && v <= 0x7E ? Unicode.Scalar(v + 0xFEE0)! : c)
    }
    return String(out)
  }

  /// The model's spans in `n` units of an ASCII text: two in each unit. (In ASCII a UTF-16 offset is a scalar offset;
  /// `PIISpan(in:utf16Range:)` would count the scalars before each span, which takes time that grows with the square of n.)
  private func modelSpans(_ text: String, _ n: Int, _ width: Int) -> [PIISpan] {
    let u: String.UTF16View = text.utf16
    func span(_ a: Int, _ b: Int) -> PIISpan {
      let lo: String.Index = u.index(u.startIndex, offsetBy: a)
      let hi: String.Index = u.index(lo, offsetBy: b - a)
      return PIISpan(range: lo..<hi, utf16Range: a..<b, scalarRange: a..<b, label: .person, source: .model)
    }
    var spans: [PIISpan] = []
    spans.reserveCapacity(2 * n)
    for i: Int in 0..<n {
      let at: Int = i * width
      spans.append(span(at + 4, at + 7))
      spans.append(span(at + 20, at + 27))
    }
    return spans
  }

  func testDoublingWithVaultSpansAndRedact() throws {
    try skipTiming()
    // n known values found again, each also found in part by the model, and n other spans: one pass over both
    let unit: String = "Ask Kim Minsu about Lee Jun. "
    let width: Int = unit.utf16.count
    let v = PIIVault()
    v.tag("PERSON", "Kim Minsu")
    try assertLinear("withVaultSpans", [64_000, 128_000, 256_000]) { (n: Int) throws -> Double in
      let text: String = String(repeating: unit, count: n)
      let spans: [PIISpan] = modelSpans(text, n, width)
      return medianTime { XCTAssertEqual(v.withVaultSpans(text, spans: spans).count, 2 * n) }
    }
    try assertLinear("redact", [8_000, 16_000, 32_000]) { (n: Int) throws -> Double in
      let text: String = String(repeating: unit, count: n)
      let spans: [PIISpan] = modelSpans(text, n, width)
      return try medianTime {
        let fresh = try PIIVault(entries: v.entries)
        XCTAssertEqual(try fresh.redact(text, spans: spans).spans.count, 2 * n)
      }
    }
    // one span a model found with n brackets before its value and n sentence marks and opening brackets after it: each
    // end is cut once (1.7.1 review, L3-B1)
    try assertLinear("redact, a span's ends", [128_000, 256_000, 512_000]) { (n: Int) throws -> Double in
      let text: String = String(repeating: "(", count: n) + "Kim Minsu" + String(repeating: ".,", count: n) + String(repeating: " (", count: n)
      return try medianTime {
        let span = PIISpan(in: text, utf16Range: 0..<text.utf16.count, label: .person)
        let got: [PIISpan] = try PIIVault().redact(text, spans: [span]).spans
        XCTAssertEqual(got.map { (s: PIISpan) -> String in String(text.unicodeScalars[s.range]) }, ["Kim Minsu"])
      }
    }
    // the same with the text in full-width letters (a reading with a map back to the text)
    let wide: String = fullWidth(unit)
    try assertLinear("withVaultSpans, full-width", [64_000, 128_000, 256_000]) { (n: Int) throws -> Double in
      let text: String = String(repeating: wide, count: n)
      return medianTime { XCTAssertEqual(v.withVaultSpans(text, spans: []).count, n) }
    }
  }

  func testDoublingCapitalsHangulAndClusters() throws {
    try skipTiming()
    // capitals in other spellings (full Unicode uppercase, Greek without accents, the capital sharp s), a value of two
    // Hangul syllables with particles and inside longer words, values followed by a variation selector and a combining
    // mark, and custom values that merge: seven hits a line
    let parts: [String] = [
      "\u{C9C0}\u{C6D0}\u{B2D8}\u{AED8}\u{C11C} \u{C9C0}\u{C6D0}\u{AE08}\u{C744} \u{C9C0}\u{C6D0}\u{D588}\u{B2E4}; ",
      "\u{39D}\u{399}\u{39A}\u{39F}\u{3A3} \u{3A0}\u{391}\u{3A0}\u{391}\u{394}\u{39F}\u{3A0}\u{39F}\u{3A5}\u{39B}\u{39F}\u{3A3}, ",
      "ANNA WEISS and ANNA WEI\u{1E9E}; \u{6E21}\u{8FBA}\u{E0100}\u{3055}\u{3093}, \u{AE40}\u{BBFC}\u{C218}\u{301}\u{B2D8}, AB-1234-CD. ",
    ]
    let line: String = parts.joined()
    let known = PIIVault()
    known.tag("PERSON", "\u{C9C0}\u{C6D0}")
    known.tag("PERSON", "\u{39D}\u{3AF}\u{3BA}\u{3BF}\u{3C2} \u{3A0}\u{3B1}\u{3C0}\u{3B1}\u{3B4}\u{3CC}\u{3C0}\u{3BF}\u{3C5}\u{3BB}\u{3BF}\u{3C2}")
    known.tag("PERSON", "Anna Wei\u{DF}")
    known.tag("PERSON", "\u{6E21}\u{8FBA}")
    known.tag("PERSON", "\u{AE40}\u{BBFC}\u{C218}")
    known.tag("CUSTOMER", "AB-1234")
    known.tag("CUSTOMER", "1234-CD")
    try assertLinear("redact, capitals, Hangul and clusters", [2_000, 4_000, 8_000]) { (n: Int) throws -> Double in
      let text: String = String(repeating: line, count: n)
      return try medianTime {
        let fresh = try PIIVault(entries: known.entries)
        XCTAssertEqual(try fresh.redact(text, spans: []).spans.count, 7 * n)
      }
    }
    // a known value followed by a run of n combining marks (taken in whole), and a value of two syllables followed by n
    // syllables (another word)
    assertLinear("withVaultSpans, a run of marks", [800_000, 1_600_000, 3_200_000]) { (n: Int) -> Double in
      let text: String = "\u{6E21}\u{8FBA}" + String(repeating: "\u{301}", count: n) + "\u{3055}\u{3093}"
      return medianTime { XCTAssertEqual(known.withVaultSpans(text, spans: []).first?.utf16Range.upperBound, text.utf16.count - 2) }
    }
    assertLinear("withVaultSpans, a long word after two syllables", [80_000, 160_000, 320_000]) { (n: Int) -> Double in
      let text: String = String(repeating: "\u{C9C0}\u{C6D0}", count: n) + " " + String(repeating: "\u{C9C0}\u{C6D0}\u{AE08}", count: n)
      return medianTime { XCTAssertEqual(known.withVaultSpans(text, spans: []).count, 0) }
    }
  }

  func testDoublingLongValuesThatRepeatThemselves() throws {
    try skipTiming()
    // a known value of n words that repeats itself, in a text that repeats it: each place where it may start is looked at
    // once, in every spelling the vault searches (1.7.1 gate 7: Swift, Kotlin and Dart compared it from its start at
    // every place). The vault learns the value (its spellings) and finds it; one hit each: the places overlap and merge.
    func words(_ w: [String], _ n: Int) -> String {
      var out: [String] = []
      out.reserveCapacity(n)
      for i: Int in 0..<n { out.append(w[i % w.count]) }
      return out.joined(separator: " ")
    }
    // a value learned from a model's span over the whole text (". . . .Kim Minsu"), found once after long partial matches
    try assertLinear("a long value from a span, found once", [80_000, 160_000, 320_000]) { (n: Int) throws -> Double in
      let text: String = String(repeating: " .", count: n) + "Kim Minsu" + String(repeating: ", ", count: n)
      let want: String = " [PERSON_1]" + String(repeating: ", ", count: n)
      return try medianTime {
        let span = PIISpan(in: text, utf16Range: 0..<text.utf16.count, label: .person)
        XCTAssertEqual(try PIIVault().redact(text, spans: [span]).text, want)
      }
    }
    let shapes: [(String, String, (Int) -> String, (Int) -> String)] = [
      ("as it is written", "PERSON", { (n: Int) -> String in words(["ha"], n) }, { (n: Int) -> String in String(repeating: "ha ", count: 2 * n) }),
      ("in any ASCII case", "PERSON", { (n: Int) -> String in words(["Ha", "ha"], n) }, { (n: Int) -> String in String(repeating: "hA Ha ", count: n) }),
      ("a URL's path as it is written", "URL", { (n: Int) -> String in String(repeating: "https://x.io/", count: n) },
       { (n: Int) -> String in "HTTPS://X.IO/" + String(repeating: "https://x.io/", count: 2 * n - 1) }),
      ("in capitals of another spelling", "PERSON", { (n: Int) -> String in words(["Wei\u{DF}"], n) }, { (n: Int) -> String in String(repeating: "WEISS ", count: 2 * n) }),
    ]
    for (name, label, value, text) in shapes {
      try assertLinear("a long value that overlaps itself, \(name)", [64_000, 128_000, 256_000]) { (n: Int) throws -> Double in
        let known: String = value(n)
        let t: String = text(n)
        return medianTime {
          let v = PIIVault()
          v.tag(label, known)
          XCTAssertEqual(v.find(t).count, 1)
        }
      }
    }
  }
}
