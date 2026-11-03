// User lists (allow, block, listsId; 1.4.0) through the Swift API. Validation runs without a model (it comes before any
// model file is read, so a valid list ends in fileNotFound for a missing folder) and reports the list, index and reason of
// the npm package and the C core; the rest runs on nano and skips without the model copy. The matching rules themselves
// are tested in native/core/tests and js/test/lists.test.ts.
import NewmeanPII
import XCTest

final class ListsTests: XCTestCase {
  static let missing = URL(fileURLWithPath: "/nonexistent/pii-nano")

  /// The PIIListError of these options, or nil when they are valid (the load then fails on the missing folder).
  func listError(allow: [PIIAllowEntry] = [], block: [PIIBlockEntry] = [], listsId: String? = nil,
                 file: StaticString = #filePath, line: UInt = #line) async -> PIIListError? {
    do {
      _ = try await PIIRedactor(size: .nano, allow: allow, block: block, listsId: listsId, modelDirectory: Self.missing)
      XCTFail("loaded without a model", file: file, line: line)
    } catch let e as PIIListError {
      return e
    } catch PIIError.fileNotFound {
      return nil
    } catch {
      XCTFail("\(error)", file: file, line: line)
    }
    return nil
  }

  func assertError(_ e: PIIListError?, _ list: PIIListError.List, _ index: Int?, _ reason: PIIListError.Reason,
                   file: StaticString = #filePath, line: UInt = #line) {
    XCTAssertEqual(e, PIIListError(list: list, index: index, reason: reason), file: file, line: line)
  }

  func testEntryChecks() async {
    let a = String(repeating: "a", count: 256)
    for (text, reason) in [(a + "a", PIIListError.Reason.tooLong), ("\0" + a, .tooLong),  // the length bound comes first
                           (String(repeating: "😀", count: 129), .tooLong),  // 258 UTF-16 units
                           (String(repeating: "a", count: 65), .tooLong), (String(repeating: "가", count: 65), .tooLong),
                           ("a\nb", .badText), ("a\rb", .badText), ("a\r\nb", .badText), ("a\u{85}b", .badText),
                           ("a\u{2028}b", .badText), ("a\u{2029}b", .badText), ("a\0b", .badText), ("\0", .badText),
                           ("", .empty), ("x\u{3000}", .edgeSpace), (" x", .edgeSpace), ("\u{a0}x", .edgeSpace),
                           ("x\t", .edgeSpace), ("x\u{202f}", .edgeSpace)] {
      assertError(await listError(allow: [PIIAllowEntry(text: text)]), .allow, 0, reason)
      assertError(await listError(block: [PIIBlockEntry(text: text, label: .person)]), .block, 0, reason)
    }
    for ok in [String(repeating: "a", count: 64), String(repeating: "가", count: 64), "\u{200b}x", "a b", "제주"] {
      let e = await listError(allow: [PIIAllowEntry(text: ok)])
      XCTAssertNil(e, ok)  // U+200B is not White_Space; inner spaces are fine
    }
    let e = await listError(allow: ["비밀이름 "])
    XCTAssertEqual(e?.reason, .edgeSpace)
    XCTAssertFalse(e?.localizedDescription.contains("비밀이름") ?? true)
    XCTAssertEqual(e?.localizedDescription, "[NewmeanPII] allow list entry 0: edge_space")
  }

  func testOrder() async {
    // allow before block; the first invalid entry of a list
    assertError(await listError(allow: ["ok", " x", ""], block: [PIIBlockEntry(text: "", label: .person)]), .allow, 1, .edgeSpace)
    assertError(await listError(allow: ["ok"], block: [PIIBlockEntry(text: "Kim", label: .person), PIIBlockEntry(text: "", label: .org)]),
                .block, 1, .empty)
    // listsId first
    assertError(await listError(allow: [" x"], listsId: ""), .meta, nil, .badListsId)
    // too_many only after the first 10,000 entries are valid
    let many = (0...10_000).map { PIIAllowEntry(text: "v\($0)") }
    assertError(await listError(allow: many), .allow, 10_000, .tooMany)
    assertError(await listError(allow: Array(many[..<50]) + [" x"] + Array(many[50...])), .allow, 50, .edgeSpace)
    assertError(await listError(block: many.map { PIIBlockEntry(text: $0.text, label: .person) }), .block, 10_000, .tooMany)
    let tenThousand = await listError(allow: Array(many[..<10_000]))
    XCTAssertNil(tenThousand)
  }

  func testChecksAcrossEntries() async {
    // from the C core: label conflicts (compared forms: ASCII case folded), then forms in both lists
    assertError(await listError(allow: ["seoul"], block: [PIIBlockEntry(text: "Kim", label: .person), PIIBlockEntry(text: "KIM", label: .org),
                                                          PIIBlockEntry(text: "SEOUL", label: .org)]), .block, 1, .labelConflict)
    assertError(await listError(allow: ["Seoul", PIIAllowEntry(text: "Busan", label: .org)],
                                block: [PIIBlockEntry(text: "x", label: .org), PIIBlockEntry(text: "BUSAN", label: .org)]), .block, 1, .inBoth)
    // same form and label: merged, not an error
    let dup = await listError(allow: ["Seoul", "SEOUL"], block: [PIIBlockEntry(text: "Kim", label: .person), PIIBlockEntry(text: "kim", label: .person)])
    XCTAssertNil(dup)
  }

  func testListsId() async {
    for bad in ["", "a\nb", "a\rb", "\0", "a\u{2028}", String(repeating: "x", count: 129)] {
      assertError(await listError(listsId: bad), .meta, nil, .badListsId)
    }
    for ok in ["survey-2026-11 v3", String(repeating: "x", count: 128), " padded "] {
      let e = await listError(listsId: ok)
      XCTAssertNil(e, ok)
    }
    XCTAssertEqual(PIIListError(list: .meta, index: nil, reason: .badListsId).localizedDescription, "[NewmeanPII] listsId: bad_lists_id")
  }

  func testNames() {
    XCTAssertEqual(PIIListError.Reason.allCases.map(\.rawValue), ["bad_shape", "too_long", "bad_text", "empty", "edge_space", "unknown_label",
                                                                  "too_many", "label_conflict", "in_both", "bad_lists_id"])
    XCTAssertEqual(PIISource.block.rawValue, "block")
    XCTAssertEqual(PIIAllowEntry(stringLiteral: "제주"), PIIAllowEntry(text: "제주", label: nil))
  }

  // MARK: - With the model (nano)

  func redactor(labels: Set<PIILabel> = PIILabel.all, allow: [PIIAllowEntry] = [], block: [PIIBlockEntry] = [],
                listsId: String? = nil) async throws -> PIIRedactor {
    try await PIIRedactor(size: .nano, labels: labels, allow: allow, block: block, listsId: listsId,
                          modelDirectory: Fixtures.requireModel("nano", "q4"))
  }

  func show(_ text: String, _ spans: [PIISpan]) -> [String] {
    spans.map { "\(String(text.unicodeScalars[$0.range])):\($0.label.rawValue):\($0.source.rawValue)" }
  }

  func testInfo() async throws {
    let plain = try await redactor()
    let none = plain.info.lists
    XCTAssertEqual([none.allow, none.block], [0, 0])
    XCTAssertNil(none.id)
    let r = try await redactor(allow: ["Seoul", "SEOUL", PIIAllowEntry(text: "seoul", label: .org)], block: [PIIBlockEntry(text: "박지훈", label: .person)],
                               listsId: "v3")
    let lists = r.info.lists
    XCTAssertEqual([lists.allow, lists.block], [1, 1])  // entries after merging
    XCTAssertEqual(lists.id, "v3")
    let many = try await redactor(allow: (0..<10_000).map { PIIAllowEntry(text: "v\($0)") })
    XCTAssertEqual(many.info.lists.allow, 10_000)
  }

  func testBlockWithNoLabels() async throws {
    let r = try await redactor(labels: [], block: [PIIBlockEntry(text: "박지훈", label: .person), PIIBlockEntry(text: "Kim", label: .person)])
    let text = "담당: 박지훈님께 문의"
    let spans = try await r.detect(text)
    XCTAssertEqual(spans.map { [$0.start, $0.end] }, [[4, 7]])
    XCTAssertEqual(show(text, spans), ["박지훈:PERSON:block"])
    let red = try await r.redact(text)
    XCTAssertEqual(red.text, "담당: [PERSON]님께 문의")
    let kim = try await r.detect("KIM and Kimberly")
    XCTAssertEqual(show("KIM and Kimberly", kim), ["KIM:PERSON:block"])  // whole words, ASCII case
    let nfd = "박지훈".decomposedStringWithCanonicalMapping
    let (a, b) = (try await r.detect(nfd), try await r.detect(nfd.precomposedStringWithCanonicalMapping))
    XCTAssertEqual(a, [])  // NFD text does not match an NFC entry
    XCTAssertEqual(show("박지훈", b), ["박지훈:PERSON:block"])
  }

  func testAllowRemovesASpan() async throws {
    let text = "Hi, I'm Kim Minji, call me at 010-1234-5678."
    let plain = try await redactor()
    let spans = try await plain.detect(text)
    let first = try XCTUnwrap(spans.first)
    let value = String(text.unicodeScalars[first.range])
    let allowed = try await redactor(allow: [PIIAllowEntry(text: value.uppercased(), label: first.label)])
    let after = try await allowed.detect(text)
    XCTAssertEqual(after, Array(spans.dropFirst()), value)
    // an entry of another family keeps the span
    let other = try await redactor(allow: [PIIAllowEntry(text: value, label: first.label.family == .org ? .email : .org)])
    let kept = try await other.detect(text)
    XCTAssertEqual(kept, spans)
  }

  func testTableWithBlock() async throws {
    let csv = "name,city\n박지훈,제주\n김민수,서울\n"
    let r = try await redactor(labels: [], block: [PIIBlockEntry(text: "박지훈", label: .person)])
    let spans = try await r.detectTable(csv)
    XCTAssertEqual(show(csv, spans), ["박지훈:PERSON:block"])
    XCTAssertEqual(spans.map { [$0.start, $0.end] }, [[10, 13]])
    let full = try await redactor(block: [PIIBlockEntry(text: "박지훈", label: .person)])
    let red = try await full.redactTable(csv)
    XCTAssertTrue(red.spans.contains { $0.start == 10 && $0.end == 13 && $0.source == .block && $0.label == .person }, "\(red.spans)")
    XCTAssertTrue(red.text.split(separator: "\n")[1].hasPrefix("[PERSON],"), red.text)
  }

  func testConcurrentCallsShareTheLists() async throws {
    let texts = ["담당: 박지훈님께 문의", "Hi, I'm Kim Minji, call me at 010-1234-5678.", "name,city\n박지훈,제주\n김민수,서울\n", "Seoul 박지훈"]
    weak var released: PIIRedactor?
    do {
      let r = try await redactor(allow: ["Seoul", "제주"], block: [PIIBlockEntry(text: "박지훈", label: .person)], listsId: "c")
      released = r
      let expected = try texts.map { (try r.detect($0), try r.detectTable($0)) }
      try await withThrowingTaskGroup(of: (Int, [PIISpan], [PIISpan]).self) { group in
        for i in 0..<64 {
          group.addTask {
            let t = texts[i % texts.count]
            return (i % texts.count, try await r.detect(t), try await r.detectTable(t))
          }
        }
        for try await (k, d, t) in group {
          XCTAssertEqual(d, expected[k].0)
          XCTAssertEqual(t, expected[k].1)
        }
      }
      XCTAssertTrue(expected[0].0.contains { $0.source == .block })
    }
    XCTAssertNil(released)  // the redactor and its lists handle are freed once the last call has returned
  }
}
