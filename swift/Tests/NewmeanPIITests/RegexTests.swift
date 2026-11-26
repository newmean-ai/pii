// The pattern layer: js/test/regex.test.ts (examples, and parity with the Python baseline on regex.jsonl).
import NewmeanPII
import XCTest

final class RegexTests: XCTestCase {
  func testCommonFormats() {
    // the account-number rule (FINANCIAL) runs before PHONE and also takes 010-1234-5678; next to the model, the model's
    // label wins for such overlaps
    let t = "메일 kim.minjun@example.co.kr 전화 010-1234-5678 카드 4111 1111 1111 1111 주민 900101-1234567"
    let got = PIIRegex.spans(in: t).map { "\(t[$0.range]):\($0.label.rawValue)" }
    XCTAssertEqual(got, ["kim.minjun@example.co.kr:EMAIL", "900101-1234567:ID_NUMBER", "010-1234-5678:FINANCIAL", "4111 1111 1111 1111:FINANCIAL"])
  }

  func testJavaScriptWhitespace() {
    // JavaScript \s includes VT and U+FEFF and excludes U+0085 (ICU's \s differs); a URL ends at the first of them
    XCTAssertEqual(PIIRegex.spans(in: "https://a.b/c\u{0B}x").map(\.utf16Range), [0..<13])
    XCTAssertEqual(PIIRegex.spans(in: "https://a.b/c\u{FEFF}x").map(\.utf16Range), [0..<13])
    XCTAssertEqual(PIIRegex.spans(in: "https://a.b/c\u{85}x").map(\.utf16Range), [0..<15])
    XCTAssertEqual(PIIRegex.spans(in: "www.a.b\u{3000}x").map(\.utf16Range), [0..<7])
  }

  func testOffsetsAfterEmoji() {
    let t = "😀👩‍👩‍👧 mail: kim@example.com"
    let s = PIIRegex.spans(in: t)
    XCTAssertEqual(s.count, 1)
    XCTAssertEqual(String(t[s[0].range]), "kim@example.com")
    XCTAssertEqual(s[0].utf16Range, 17..<32)
    XCTAssertEqual(s[0].scalarRange, 13..<28)
  }

  func testParityWithPythonBaseline() throws {
    let rows = try Fixtures.rows("regex.jsonl")
    var bad: [String] = []
    for row in rows {
      let text = row["text"] as! String
      let want = (row["spans"] as! [[String: Any]]).map { "\($0["start"] as! Int)-\($0["end"] as! Int):\($0["label"] as! String)" }.sorted()
      let got = PIIRegex.spans(in: text).map { "\($0.scalarRange.lowerBound)-\($0.scalarRange.upperBound):\($0.label.rawValue)" }.sorted()
      if want != got { bad.append("\(text.prefix(80))\n  py \(want)\n  swift \(got)") }
    }
    print("regex parity: \(rows.count - bad.count)/\(rows.count)\n\(bad.prefix(5).joined(separator: "\n"))")
    XCTAssertEqual(bad.count, 0)
  }

  /// The matcher of 1.6.0 and earlier, kept as the reference: `PIIRegex.spans(in:)` must give its matches, in its order.
  /// It tried the EMAIL pattern again from every character of a run without "@" and compared each match with all earlier
  /// ones, so its time grew with the square of the input (fixed in 1.6.1).
  private static let legacyRules = PIIRegex.rules.map { ($0.label, try! NSRegularExpression(pattern: $0.pattern)) }

  private func legacy(_ text: String) -> [String] {
    let all = NSRange(location: 0, length: (text as NSString).length)
    var out: [(PIILabel, Range<Int>)] = []
    for (label, re) in Self.legacyRules {
      for m in re.matches(in: text, range: all) {
        let r = m.range.location..<(m.range.location + m.range.length)
        if out.contains(where: { r.overlaps($0.1) }) { continue }
        out.append((label, r))
      }
    }
    return out.map { "\($0.1.lowerBound)-\($0.1.upperBound):\($0.0.rawValue)" }
  }

  private func current(_ text: String) -> [String] {
    PIIRegex.spans(in: text).map { "\($0.utf16Range.lowerBound)-\($0.utf16Range.upperBound):\($0.label.rawValue)" }
  }

  func testEqualsTheLegacyMatcherExhaustively() {
    // every string of up to 6 characters over a 6-character alphabet
    let alphabet = ["a", "@", ".", "-", "+", " "]
    var texts = [""]
    var checked = 0
    var bad: [String] = []
    for length in 0...6 {
      for t in texts where current(t) != legacy(t) { bad.append(t) }
      checked += texts.count
      if length < 6 { texts = texts.flatMap { t in alphabet.map { t + $0 } } }
    }
    XCTAssertEqual(Array(bad.prefix(5)), [])
    XCTAssertEqual(checked, 55987)
  }

  func testEqualsTheLegacyMatcherOnRandomUnicode() {
    let alphabet = [
      "a", "Z", "7", "@", "@", ".", ".", "+", "-", "_", " ", "\n", "\u{a0}", "한", "é", "𐐀", "𝟙", "①", "\u{301}", "😀", ":", "/", "(", ")",
      "w", "0", "1", "http://", "www.", "\u{0B}", "\u{85}", "\u{FEFF}",
    ]
    var state: UInt32 = 20_261_117
    func random(_ limit: Int) -> Int {
      state = state &* 1_664_525 &+ 1_013_904_223
      return Int(UInt64(state) * UInt64(limit) >> 32)
    }
    var bad: [String] = []
    for _ in 0..<8000 {
      var t = ""
      for _ in 0..<random(60) { t += alphabet[random(alphabet.count)] }
      if current(t) != legacy(t) { bad.append(t) }
    }
    XCTAssertEqual(Array(bad.prefix(5)), [])
  }

  func testEqualsTheLegacyMatcherOnEdgeCases() {
    for t in [
      "a@b.co@d.e", "a@b.co-+x@c.de", "a@b..co", "a@@b.co", "a@b.co.", ".a+@b.co", "a@b", "@b.co", "a@.co", "a@b.c@d.e.f",
      "😀 김+𐐀@회사.한국 𝟙@𐐀.한", "e\u{301}@a.co ①@b.co a@b.①",
      "www.site.test/x@y.co 010-1234-5678 https://other.test", "a@b.co http://one.test a@b.co",
      "900101-1234567 4111 1111 1111 1111 +1 (212) 555-1234", "https://a@b.co/x?mail=c@d.co",
    ] {
      XCTAssertEqual(current(t), legacy(t), t)
    }
  }

  func testLongInputsTakeTimeInProportionToTheirLength() throws {
    try skipTiming()
    // 1.6.0 needed minutes for the first text
    let start = Date()
    for t in [
      String(repeating: "a", count: 100_000), "a@" + String(repeating: "b", count: 100_000), String(repeating: "@", count: 100_000),
      String(repeating: "a.", count: 50_000), String(repeating: "1", count: 100_000),
    ] {
      XCTAssertEqual(PIIRegex.spans(in: t).filter { $0.label == .email }.count, 0)
    }
    let spans = PIIRegex.spans(in: String(repeating: "a@b.co ", count: 40_000))
    XCTAssertEqual(spans.count, 40_000)
    XCTAssertEqual(spans.first?.utf16Range, 0..<6)
    XCTAssertEqual(spans.last?.utf16Range, 279_993..<279_999)
    XCTAssertLessThan(Date().timeIntervalSince(start), 10)
  }
}
