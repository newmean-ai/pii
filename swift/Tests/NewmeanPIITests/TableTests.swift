// Table mode through the Swift API (pii_detect_table) on nano: the options reach the core, spans stay inside cells with
// consistent offsets (an astral emoji before later rows), and text that is not a table gives the spans of detect. The
// column logic itself is tested in native/core/tests (pii_cli selftest, run_tests.py table, parity with the npm package).
// Skips without the model copy. Run with `swift test -c release`.
import NewmeanPII
import XCTest

final class TableTests: XCTestCase {
  static let names = ["Maria Lopez", "Kim Minsu", "Dupont, Jean", "Anna Kowalska", "Ahmed Ali", "Sofia Rossi", "Lars Nilsen", "Priya Sharma"]
  static let emails = ["maria.lopez@example.com", "minsu.kim@example.com", "jean.dupont@example.fr", "anna.k@example.pl",
                       "ahmed.ali@example.com", "sofia.rossi@example.it", "lars@example.no", "priya.sharma@example.in"]
  static let phones = ["010-1234-5678", "010-2222-3333", "+33 6 12 34 56 78", "+48 512 345 678", "050-123-4567", "+39 347 123 4567",
                       "+47 912 34 567", "+91 98765 43210"]
  let csv = """
    name,email,phone,comment
    Maria Lopez,maria.lopez@example.com,010-1234-5678,Called twice about the delivery
    Kim Minsu,minsu.kim@example.com,010-2222-3333,no answer
    "Dupont, Jean",jean.dupont@example.fr,+33 6 12 34 56 78,"said ""later\"""
    Anna Kowalska,anna.k@example.pl,+48 512 345 678,ok 👍🏽
    Ahmed Ali,ahmed.ali@example.com,050-123-4567,wants a refund
    Sofia Rossi,sofia.rossi@example.it,+39 347 123 4567,ok
    Lars Nilsen,lars@example.no,+47 912 34 567,will call back
    N/A,N/A,N/A,
    Priya Sharma,priya.sharma@example.in,+91 98765 43210,ok

    """
  let prose = "My name is Kim Minsu 👩‍👩‍👧 and my email is minsu.kim@example.com."

  func redactor(labels: Set<PIILabel> = PIILabel.all, regex: Bool = false) async throws -> PIIRedactor {
    try await PIIRedactor(size: .nano, labels: labels, regex: regex, modelDirectory: Fixtures.requireModel("nano", "q4"))
  }

  func texts(_ spans: [PIISpan], _ source: PIISource? = nil) -> [String] {
    spans.filter { source == nil || $0.source == source }.map { "\(String(csv.unicodeScalars[$0.range])):\($0.label.rawValue)" }
  }

  /// Sorted, non-overlapping, offsets consistent, and never over a line break, a delimiter or a quote at either end.
  func checkCells(_ spans: [PIISpan], file: StaticString = #filePath, line: UInt = #line) {
    let ns = csv as NSString
    for (a, b) in zip(spans, spans.dropFirst()) { XCTAssertLessThanOrEqual(a.end, b.start, file: file, line: line) }
    for s in spans {
      let t = String(csv.unicodeScalars[s.range])
      XCTAssertEqual(ns.substring(with: NSRange(location: s.start, length: s.end - s.start)), t, file: file, line: line)
      XCTAssertEqual(csv.unicodeScalars.distance(from: csv.startIndex, to: s.range.lowerBound), s.scalarRange.lowerBound, file: file, line: line)
      XCTAssertFalse(t.contains("\n") || ",\"".contains(t.first!) || ",\"".contains(t.last!), t,
                     file: file, line: line)
    }
  }

  func testColumnsArePropagated() async throws {
    let r = try await redactor()
    let spans = try await r.detectTable(csv)
    checkCells(spans)
    let want = (0..<8).flatMap { ["\(Self.names[$0]):PERSON", "\(Self.emails[$0]):EMAIL", "\(Self.phones[$0]):PHONE"] }
    XCTAssertEqual(texts(spans, .table), want)
    // the header row and the placeholders are not propagated into
    let headerEnd = csv.utf16.firstIndex(of: 10).map { csv.utf16.distance(from: csv.utf16.startIndex, to: $0) }!
    XCTAssertTrue(spans.allSatisfy { $0.start > headerEnd })
    XCTAssertFalse(texts(spans, .table).contains { $0.hasPrefix("N/A") })
    let comma = try await r.detectTable(csv, delimiter: .comma)
    XCTAssertEqual(comma, spans)
  }

  func testOptionsReachTheCore() async throws {
    let r = try await redactor()
    let off = try await r.detectTable(csv, vote: false)
    checkCells(off)
    XCTAssertFalse(off.isEmpty)
    XCTAssertFalse(off.contains { $0.source == .table })
    // header off: the header row is a data row; "name" joins the vote's 80% and is masked, "email" has no "@" and "phone"
    // fewer than 6 digits
    let noHeader = try await r.detectTable(csv, header: false)
    XCTAssertEqual(texts(noHeader, .table).filter { ["name", "email", "phone"].contains($0.split(separator: ":")[0]) }, ["name:PERSON"])
    // ';' gives one long column per line: no vote, and no span over a line break
    let semi = try await r.detectTable(csv, delimiter: .semicolon)
    XCTAssertFalse(semi.contains { $0.source == .table || String(csv.unicodeScalars[$0.range]).contains("\n") })
    // markdown: no line starts with "|", so there are no rows and the text is not a table
    let md = try await r.detectTable(csv, delimiter: .markdown), plain = try await r.detect(csv)
    XCTAssertEqual(md, plain)
    let emailOnly = try await redactor(labels: [.email])
    let emails = try await emailOnly.detectTable(csv)
    XCTAssertEqual(texts(emails), Self.emails.map { "\($0):EMAIL" })
    XCTAssertTrue(emails.allSatisfy { $0.source == .table })
  }

  func testNotATableGivesDetect() async throws {
    for regex in [false, true] {
      let r = try await redactor(regex: regex)
      for t in [prose, "", "one line, with a comma", "a;b"] {
        let table = try await r.detectTable(t), plain = try await r.detect(t)
        XCTAssertEqual(table, plain, "\(regex) \(t)")
      }
      let proseSpans = try await r.detectTable(prose)
      let email = try XCTUnwrap(proseSpans.first { $0.label == .email })
      XCTAssertEqual(String(prose.unicodeScalars[email.range]), "minsu.kim@example.com")
    }
    // the regex layer is passed to table mode too (pattern spans are clipped to cells like the model's)
    let rx = try await redactor(regex: true)
    let spans = try await rx.detectTable(csv)
    checkCells(spans)
    XCTAssertTrue(spans.contains { $0.source == .table })
  }

  func testRedactTableKeepsTheTable() async throws {
    let r = try await redactor()
    let (masked, spans) = try await r.redactTable(csv)
    let detected = try await r.detectTable(csv)
    XCTAssertEqual(spans, detected)
    XCTAssertEqual(masked, PIIRedactor.applyRedaction(to: csv, spans: spans))
    let lines = masked.split(separator: "\n", omittingEmptySubsequences: false)
    XCTAssertEqual(lines.count, csv.split(separator: "\n", omittingEmptySubsequences: false).count)
    XCTAssertEqual(lines[0], "name,email,phone,comment")
    XCTAssertEqual(lines[1], "[PERSON],[EMAIL],[PHONE],Called twice about the delivery")
    XCTAssertEqual(masked.filter { $0 == "\"" }.count, csv.filter { $0 == "\"" }.count)
    let stars = try await r.redactTable(csv, mode: .mask(with: "#"))
    XCTAssertEqual(stars.text, PIIRedactor.applyRedaction(to: csv, spans: stars.spans, mode: .mask(with: "#")))
    let mode = PIIRedactionMode.custom { span, original in "<\(span.label.rawValue):\(original.unicodeScalars.count)>" }
    let custom = try await r.redactTable(csv, vote: false, mode: mode)
    XCTAssertEqual(custom.text, PIIRedactor.applyRedaction(to: csv, spans: custom.spans, mode: mode))
  }

  func testHeaderContext() async throws {
    let r = try await redactor()
    let text = "name,email\n" + Self.names.indices.filter { $0 != 2 }.map { "\(Self.names[$0]),\(Self.emails[$0])\n" }.joined()
    let base = try await r.detectTable(text), ctx = try await r.detectTable(text, context: .header)
    let none = try await r.detectTable(text, context: .none)
    XCTAssertEqual(none, base)
    XCTAssertFalse(ctx.isEmpty)
    for (a, b) in zip(ctx, ctx.dropFirst()) { XCTAssertLessThanOrEqual(a.end, b.start) }
    // inside one cell: never over a delimiter or a line break
    XCTAssertFalse(ctx.contains { s in String(text.unicodeScalars[s.range]).contains { $0 == "," || $0 == "\n" } })
    let masked = { (spans: [PIISpan]) in Set(spans.flatMap { $0.start..<$0.end }) }
    XCTAssertTrue(masked(base).isSubset(of: masked(ctx)))
    let redacted = try await r.redactTable(text, context: .header)
    XCTAssertEqual(redacted.spans, ctx)
    XCTAssertEqual(PIITableContext.allCases.map(\.rawValue), ["none", "header"])
    XCTAssertNil(PIITableContext(rawValue: "row"))
  }

  func testKeepEmoji() async throws {
    let text = "Call Anna Kim 😀 at 010-1234-5678"
    let e = (text as NSString).range(of: "😀")
    let r = try await redactor()
    XCTAssertFalse(r.keepEmoji)
    for spans in [try await r.detect(text), try await r.detectTable(text)] {
      XCTAssertFalse(spans.isEmpty)
      XCTAssertTrue(spans.allSatisfy { $0.end <= e.location || $0.start >= e.location + e.length }, "\(spans)")
    }
    let keep = try await PIIRedactor(size: .nano, keepEmoji: true, modelDirectory: Fixtures.requireModel("nano", "q4"))
    XCTAssertTrue(keep.keepEmoji)
    _ = try await keep.detect(text)
  }

  func testWholeWords() async throws {
    let r = try await PIIRedactor(size: .nano, modelDirectory: Fixtures.requireModel("nano", "q4"))
    XCTAssertTrue(r.wholeWords)  // the default since 1.3.0
    let off = try await PIIRedactor(size: .nano, wholeWords: false, modelDirectory: Fixtures.requireModel("nano", "q4"))
    XCTAssertFalse(off.wholeWords)
    for text in ["남성", "경기도", "R23", "Hi, I'm Kim Minji, call me at 010-1234-5678.", "region,id\n경기도,R23\n서울,R24\n"] {
      let units = Array(text.utf16)
      let alnum = { (i: Int) -> Bool in
        guard let u = UnicodeScalar(units[i]) else { return false }
        return CharacterSet.alphanumerics.contains(u)
      }
      for spans in [try await r.detect(text), try await r.detectTable(text)] {
        for s in spans {  // no span starts or ends inside a word
          XCTAssertTrue(s.start == 0 || !(alnum(s.start - 1) && alnum(s.start)), "\(text) \(s)")
          XCTAssertTrue(s.end == units.count || !(alnum(s.end - 1) && alnum(s.end)), "\(text) \(s)")
        }
      }
    }
  }

  func testStrictShortNames() async throws {
    let dir = try Fixtures.requireModel("nano", "q4")
    let plain = try await PIIRedactor(size: .nano, modelDirectory: dir)
    let strict = try await PIIRedactor(size: .nano, strictShortNames: true, modelDirectory: dir)
    XCTAssertTrue(strict.strictShortNames)
    XCTAssertFalse(plain.strictShortNames)
    let long = "Hi, I'm Kim Minji, call me at 010-1234-5678. I live in Seoul and work at Example Corp as an engineer."
    let (strictLong, plainLong) = (try await strict.detect(long), try await plain.detect(long))
    XCTAssertEqual(strictLong, plainLong)  // longer texts are unchanged
    let person = { (spans: [PIISpan]) in spans.filter { $0.label == .person || $0.label == .personL2 }.reduce(0) { $0 + $1.end - $1.start } }
    for text in ["경기", "네이버", "카카오", "남성", "김민지", "Maria Lopez"] {  // never more PERSON than without the option
      let (a, b) = (person(try await strict.detect(text)), person(try await plain.detect(text)))
      XCTAssertLessThanOrEqual(a, b, text)
    }
  }

  func testInfo() async throws {
    let r = try await redactor()
    let i = r.info
    XCTAssertEqual(i.packageVersion, PIIRedactor.packageVersion)
    XCTAssertEqual(i.packageVersion, "1.4.0")
    XCTAssertEqual(i.modelVersion, r.version)
    XCTAssertEqual(i.model, PIIModelFile.model(r.size, r.variant))
    XCTAssertEqual(i.tokenizer, PIIModelFile.tokenizer(r.size))
    XCTAssertEqual(i.engine, "native")
  }
}
