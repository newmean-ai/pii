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
}
