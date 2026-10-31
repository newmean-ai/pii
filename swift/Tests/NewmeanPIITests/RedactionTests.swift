// Redaction modes (js/test/combine.test.ts applyRedaction cases), spans built from UTF-16 offsets, labels and pins.
import NewmeanPII
import XCTest

final class RedactionTests: XCTestCase {
  let text = "Call Kim at 010-1234-5678."
  lazy var spans = [
    PIISpan(in: text, utf16Range: 5..<8, label: .personL2),
    PIISpan(in: text, utf16Range: 12..<25, label: .phone),
  ]

  func testTagUsesLabelFamily() {
    XCTAssertEqual(PIIRedactor.applyRedaction(to: text, spans: spans), "Call [PERSON] at [PHONE].")
  }

  func testMaskPerCharacter() {
    XCTAssertEqual(PIIRedactor.applyRedaction(to: text, spans: spans, mode: .mask), "Call *** at *************.")
    XCTAssertEqual(PIIRedactor.applyRedaction(to: text, spans: spans, mode: .mask(with: "•")), "Call ••• at •••••••••••••.")
  }

  func testBlank() {
    XCTAssertEqual(PIIRedactor.applyRedaction(to: text, spans: spans, mode: .blank), "Call  at .")
  }

  func testCustomReplacement() {
    let mode = PIIRedactionMode.custom { span, original in "<\(span.label.rawValue):\(original.utf16.count)>" }
    XCTAssertEqual(PIIRedactor.applyRedaction(to: text, spans: spans, mode: mode), "Call <PERSON_L2:3> at <PHONE:13>.")
  }

  func testEmojiOffsets() {
    // 👩‍👩‍👧 is 5 scalars and 8 UTF-16 units; 🇰🇷 2 scalars, 4 units; "é" written as e + U+0301 is 2 scalars
    let t = "👩‍👩‍👧 김민준 🇰🇷 e\u{301}mail kim@example.com 😀"
    let u = t.utf16.count
    let email = PIISpan(in: t, utf16Range: (u - 18)..<(u - 3), label: .email)
    XCTAssertEqual(String(t[email.range]), "kim@example.com")
    XCTAssertEqual(email.scalarRange, (t.unicodeScalars.count - 17)..<(t.unicodeScalars.count - 2))
    let name = PIISpan(in: t, utf16Range: 9..<12, label: .person)
    XCTAssertEqual(String(t[name.range]), "김민준")
    XCTAssertEqual(name.scalarRange, 6..<9)
    // NSString agrees on UTF-16 offsets
    XCTAssertEqual((t as NSString).substring(with: NSRange(location: email.start, length: email.end - email.start)), "kim@example.com")
    XCTAssertEqual(PIIRedactor.applyRedaction(to: t, spans: [name, email]), "👩‍👩‍👧 [PERSON] 🇰🇷 e\u{301}mail [EMAIL] 😀")
    // mask: one per scalar, so a flag (2 scalars) becomes 2 characters, as Array.from in the npm package
    let flag = PIISpan(in: t, utf16Range: 13..<17, label: .person)
    XCTAssertEqual(PIIRedactor.applyRedaction(to: t, spans: [flag], mode: .mask), "👩‍👩‍👧 김민준 ** e\u{301}mail kim@example.com 😀")
  }

  func testSpanEndingInsideCharacter() {
    // a span may end before a combining mark: redaction slices by scalars and keeps the mark
    let t = "Jose\u{301} Lee"
    let s = PIISpan(in: t, utf16Range: 0..<4, label: .person)
    XCTAssertEqual(PIIRedactor.applyRedaction(to: t, spans: [s]), "[PERSON]\u{301} Lee")
    XCTAssertEqual(PIIRedactor.applyRedaction(to: t, spans: [s], mode: .mask), "****\u{301} Lee")
  }

  func testLabels() {
    XCTAssertEqual(PIILabel.all.count, 13)
    XCTAssertEqual(PIILabel.level1, PIILabel.all.subtracting([.personL2, .urlL2]))
    XCTAssertEqual(PIILabel.urlL2.family, .url)
    XCTAssertEqual(PIILabel.allCases.map(\.rawValue), ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH", "SECRET"])
  }

  func testDefaultsMatchNpm() {
    XCTAssertEqual(PIISize.default, .medium)
    XCTAssertEqual(PIISize.allCases.map(\.defaultVariant), [.q4, .q4, .q4e, .q4e])
    XCTAssertEqual(PIIModelFile.version, "1.0.0")
    XCTAssertEqual(PIIModelFile.defaultBaseURL(.nano).absoluteString, "https://huggingface.co/newmean-ai/pii-nano/resolve/v1.0.0/")
  }

  func testPins() throws {
    // same values as js/src/models.ts
    let f = PIIModelFile.model(.medium, .q4e)
    XCTAssertEqual(f.path, "onnx/model_q4e.onnx")
    XCTAssertEqual(f.bytes, 11_303_433)
    XCTAssertEqual(f.sha256, "054ba8b06a2ea2ec6bae73ff380e36e5ff3cb3611d7a080074eb44d65669b58d")
    XCTAssertEqual(PIIModelFile.tokenizer(.nano).sha256, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d")
    XCTAssertThrowsError(try f.verify(Data(count: 10)))
  }

  func testPinsMatchNpm() throws {
    // every pin of the C core equals js/src/models.ts (MODEL_FILES)
    let url = Fixtures.root.appendingPathComponent("js/src/models.ts")
    guard let ts = try? String(contentsOf: url, encoding: .utf8) else { throw XCTSkip("no js/src/models.ts") }
    let start = try XCTUnwrap(ts.range(of: "= {")).upperBound, end = try XCTUnwrap(ts.range(of: "};", options: .backwards)).lowerBound
    let npm = try JSONSerialization.jsonObject(with: Data(("{" + ts[start..<end] + "}").utf8)) as! [String: [String: [String: Any]]]
    XCTAssertEqual(Set(npm.keys), Set(PIISize.allCases.map(\.rawValue)))
    for size in PIISize.allCases {
      for (key, file) in [("tokenizer", PIIModelFile.tokenizer(size)), ("q4", .model(size, .q4)), ("q4e", .model(size, .q4e))] {
        let want = try XCTUnwrap(npm[size.rawValue]?[key], "\(size) \(key)")
        XCTAssertEqual(file.bytes, want["bytes"] as? Int, "\(size) \(key)")
        XCTAssertEqual(file.sha256, want["sha256"] as? String, "\(size) \(key)")
      }
    }
  }

  func testPinsMatchLocalCopies() throws {
    var checked = 0
    for size in PIISize.allCases {
      for file in [PIIModelFile.tokenizer(size), .model(size, .q4), .model(size, .q4e)] {
        let url = Fixtures.modelDirectory(size.rawValue).appendingPathComponent(file.path)
        guard FileManager.default.fileExists(atPath: url.path) else { continue }
        XCTAssertNoThrow(try file.verify(Data(contentsOf: url)), url.path)
        checked += 1
      }
    }
    if checked == 0 { throw XCTSkip("no model copies") }
  }
}
