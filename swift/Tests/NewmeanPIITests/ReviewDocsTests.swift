// The edges of a span the model found (review round of SDK 1.7.1, L2-B2): the cases every package shares are in
// python/tests/vault_cases.json ("redact", run by VaultTests); these check what is Swift's own: the ranges of a span
// left, a block-list span and applyRedaction with a vault.
import Foundation
import NewmeanPII
import XCTest

final class ReviewDocsTests: XCTestCase {
  private func marked(_ text: String, _ value: String, _ source: PIISource = .model) -> PIISpan {
    let r = (text as NSString).range(of: value)
    return PIISpan(in: text, utf16Range: r.location..<(r.location + r.length), label: .person, source: source)
  }

  func testASpanLeftHasItsRanges() throws {
    let wide = String(Unicode.Scalar(0x20000)!)
    let open = String(Unicode.Scalar(0x201C)!)
    let close = String(Unicode.Scalar(0x201D)!)
    for (text, mark, value) in [
      ("담당자 「홍길동」、 다음", "「홍길동」、", "홍길동"),
      ("Contact 서울특별시 강남구 테헤란로 123 (also", "서울특별시 강남구 테헤란로 123 (", "서울특별시 강남구 테헤란로 123"),
      (wide + " says " + open + "Kim Minsu" + close + ".", open + "Kim Minsu" + close + ".", "Kim Minsu"),
    ] {
      let vault = PIIVault()
      let (out, spans) = try vault.redact(text, spans: [marked(text, mark)])
      let r = (text as NSString).range(of: value)
      XCTAssertEqual(out, (text as NSString).replacingCharacters(in: r, with: "[PERSON_1]"), text)
      XCTAssertEqual(spans[0].utf16Range, r.location..<(r.location + r.length), text)
      XCTAssertEqual(String(text.unicodeScalars[spans[0].range]), value, text)
      let start = text.unicodeScalars.distance(from: text.unicodeScalars.startIndex, to: spans[0].range.lowerBound)
      XCTAssertEqual(spans[0].scalarRange, start..<(start + value.unicodeScalars.count), text)
    }
  }

  func testABlockListSpanKeepsItsEdges() throws {
    let text = "Tiffany & Co. sent it"
    let (out, _) = try PIIVault().redact(text, spans: [marked(text, "Tiffany & Co.", .block)])
    XCTAssertEqual(out, "[PERSON_1] sent it")
  }

  func testApplyRedactionWithAVaultLearnsTheValueToo() {
    let text = "Please look up Kim Minsu."
    let vault = PIIVault()
    XCTAssertEqual(PIIRedactor.applyRedaction(to: text, spans: [marked(text, "Kim Minsu.")], vault: vault), "Please look up [PERSON_1].")
    XCTAssertEqual(vault.entries.map(\.value), ["Kim Minsu"])
  }
}
