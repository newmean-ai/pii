// Doubling test for the edges of a span the model found (review round of SDK 1.7.1, L2-B2), apart from ReviewDocsTests so
// that the test runner runs it alone ("Growth" in the class name): leaving out what a span's edges hold that is not part
// of the value takes time in proportion to the span, however many brackets, quotation marks, stops or spaces it has to
// look past. Timing: Growth.swift.
import Foundation
import NewmeanPII
import XCTest

final class ReviewDocsGrowthTests: XCTestCase {
  func testDoublingSpanEdges() throws {
    try skipTiming()
    let quote: String = String(Unicode.Scalar(0x22))
    func run(_ s: String, _ n: Int) -> String { String(repeating: s, count: n) }
    let shapes: [(String, (Int) -> String)] = [
      ("closing brackets after the value", { (n: Int) -> String in "Kim Minsu" + run(")", n) }),
      ("opening brackets before the value", { (n: Int) -> String in run("(", n) + "Kim Minsu" }),
      ("pairs of brackets around the value", { (n: Int) -> String in run("(", n) + "Kim Minsu" + run(")", n) }),
      ("quotation marks around the value", { (n: Int) -> String in run(quote, n) + "Kim Minsu" + run("'", n) }),
      // (commas before the value: a "." at the start of a value stays)
      ("stops and spaces around the value", { (n: Int) -> String in run(" ,", n) + "Kim Minsu" + run(", ", n) }),
      ("brackets that are not one pair", { (n: Int) -> String in "(" + run(")(", n) + "Kim Minsu" + run(")(", n) + ")" }),
    ]
    for (name, make) in shapes {
      try assertLinear("span edges, \(name)", [200_000, 400_000, 800_000]) { (n: Int) throws -> Double in
        let text: String = make(n)
        let found: [PIISpan] = [PIISpan(in: text, utf16Range: 0..<text.utf16.count, label: .person)]
        return try medianTime {
          XCTAssertTrue(try PIIVault().redact(text, spans: found).text.contains("[PERSON_1]"))
        }
      }
    }
  }
}
