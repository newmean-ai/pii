// Through the Swift API on the local model copies: tokenizer parity with Hugging Face tokenizers, span parity with the
// Python evaluator (js/test/predict.test.ts: the same runs and the same 99% bar), offsets, options and threads.
// Skips without the fixtures or model copies. Run with `swift test -c release` (the C core is slow at -O0).
import NewmeanPII
import XCTest

final class ModelTests: XCTestCase {
  static let runs: [(PIISize, PIIVariant, Double)] = [
    (.large, .q4e, 0), (.large, .q4, 0), (.medium, .q4e, 0), (.medium, .q4, 0),
    (.small, .q4, 0), (.small, .q4e, 0), (.nano, .q4, 0), (.nano, .q4e, 0),
    (.medium, .q4e, -0.5), (.nano, .q4, 1),
  ]

  func redactor(_ size: PIISize, _ variant: PIIVariant? = nil, sensitivity: Double = 0, labels: Set<PIILabel> = PIILabel.all,
                regex: Bool = false) async throws -> PIIRedactor {
    let dir = try Fixtures.requireModel(size.rawValue, (variant ?? size.defaultVariant).rawValue)
    return try await PIIRedactor(size: size, variant: variant, sensitivity: sensitivity, labels: labels, regex: regex, modelDirectory: dir)
  }

  func testTokenizerParity() async throws {
    let rows = try Fixtures.rows("tokenize.jsonl")
    let r = try await redactor(.nano)
    var badIDs = 0, badOffsets = 0
    for row in rows {
      let tokens = try r.tokenize(row["text"] as! String)
      if tokens.map(\.id) != row["ids"] as! [Int] { badIDs += 1 }
      let want = (row["offsets"] as! [[Int]]).map { $0[0]..<$0[1] }
      if tokens.map(\.scalarRange) != want { badOffsets += 1 }
    }
    print("tokenizer parity: ids \(rows.count - badIDs)/\(rows.count), offsets \(rows.count - badOffsets)/\(rows.count)")
    XCTAssertEqual(badIDs, 0)
    // a grapheme the normalizer replaces aligns to the whole grapheme, as in the npm package: a few rows differ from
    // Hugging Face (js/test/tokenizer.test.ts allows 3; native/core/tests compares offsets with npm exactly)
    XCTAssertLessThanOrEqual(badOffsets, 3)
  }

  func testPredictionParity() async throws {
    var ran = 0
    for (size, variant, bias) in Self.runs where Fixtures.sizes?.contains(size.rawValue) ?? true {
      // the file names of js/test/predict.test.ts ("-b-0.5", "-b1"); %g ignores the locale
      let name = "predict-\(size.rawValue)-\(variant.rawValue)\(bias != 0 ? "-b" + String(format: "%g", bias) : "").jsonl"
      // skip a run without its fixture or model copy, but fail when a model that is there does not load
      guard let rows = try? Fixtures.rows(name), (try? Fixtures.requireModel(size.rawValue, variant.rawValue)) != nil else { continue }
      let r = try await redactor(size, variant, sensitivity: -bias)
      let t0 = Date()
      var same = 0
      var diffs: [String] = []
      for row in rows {
        let text = row["text"] as! String
        let want = (row["spans"] as! [[String: Any]]).map { "\($0["start"] as! Int)-\($0["end"] as! Int):\($0["label"] as! String)" }
        let got = try await r.detect(text).map { "\($0.scalarRange.lowerBound)-\($0.scalarRange.upperBound):\($0.label.rawValue)" }
        if want == got { same += 1 } else if diffs.count < 3 { diffs.append("\(text.prefix(60))\n  py \(want)\n  swift \(got)") }
      }
      print(String(format: "predict %@ %@ bias %g: %d/%d (%.1f s)\n%@", size.rawValue, variant.rawValue, bias, same, rows.count,
                   Date().timeIntervalSince(t0), diffs.joined(separator: "\n")))
      XCTAssertGreaterThanOrEqual(Double(same) / Double(rows.count), 0.99, name)
      ran += 1
    }
    if ran == 0 { throw XCTSkip("no prediction fixtures or model copies") }
  }

  func testDetectAndRedact() async throws {
    let r = try await redactor(.medium)
    XCTAssertEqual(r.variant, .q4e)
    let text = "제 메일은 kim@example.com, 전화는 010-1234-5678입니다"
    let (masked, spans) = try await r.redact(text)
    XCTAssertEqual(masked, "제 메일은 [EMAIL], 전화는 [PHONE]입니다")
    XCTAssertEqual(spans.map { String(text[$0.range]) }, ["kim@example.com", "010-1234-5678"])
    XCTAssertEqual(spans.map(\.source), [.model, .model])
    let empty = try await r.detect(""), blank = try await r.detect("   ")
    XCTAssertTrue(empty.isEmpty && blank.isEmpty)
  }

  func testEmojiOffsets() async throws {
    let r = try await redactor(.medium)
    let text = "👩‍👩‍👧🇰🇷😀 Contact Kim Minjun at kim.minjun@example.com 🙂 or +82 10-1234-5678 ✅"
    let spans = try await r.detect(text)
    let email = try XCTUnwrap(spans.first { $0.label == .email })
    XCTAssertEqual(String(text[email.range]), "kim.minjun@example.com")
    let ns = text as NSString
    for s in spans {
      // the three offset kinds and the String.Index range agree
      XCTAssertEqual(ns.substring(with: NSRange(location: s.start, length: s.end - s.start)), String(text.unicodeScalars[s.range]))
      XCTAssertEqual(text.utf16.distance(from: text.startIndex, to: s.range.lowerBound), s.start)
      XCTAssertEqual(text.unicodeScalars.distance(from: text.startIndex, to: s.range.lowerBound), s.scalarRange.lowerBound)
    }
    // a bridged NSString (UTF-16 storage) gives the same spans
    let bridged = NSString(string: text) as String
    let bridgedSpans = try await r.detect(bridged)
    XCTAssertEqual(bridgedSpans.map(\.utf16Range), spans.map(\.utf16Range))
    let (masked, _) = try await r.redact(text)
    XCTAssertTrue(masked.hasPrefix("👩‍👩‍👧🇰🇷😀 "))
    XCTAssertTrue(masked.contains("[EMAIL]"))
    XCTAssertTrue(masked.hasSuffix(" ✅"))
  }

  func testOptions() async throws {
    let text = "Contact Mr. Kim at kim@example.com or https://example.com/kim, card 4111 1111 1111 1111."
    let all = try await redactor(.medium)
    let level1 = try await redactor(.medium, labels: PIILabel.level1)
    let emailOnly = try await redactor(.medium, labels: [.email])
    let spans = try await all.detect(text)
    let l1 = try await level1.detect(text), email = try await emailOnly.detect(text)
    XCTAssertTrue(spans.contains { $0.label.isLevel2 })
    XCTAssertEqual(email.map(\.label), [.email])
    XCTAssertEqual(l1, spans.filter { !$0.label.isLevel2 })
    // sensitivity: masking more never removes a region of the default
    all.sensitivity = 1
    let more = try await all.detect(text)
    for s in spans { XCTAssertTrue(more.contains { $0.utf16Range.contains(s.start) }) }
    all.sensitivity = 0
    let again = try await all.detect(text)
    XCTAssertEqual(again, spans)
  }

  func testRegexLayer() async throws {
    // regex: true merges pattern matches through the core's combine step (the model's label wins)
    let text = "주민 900101-1234567, 메일 kim.minjun@example.co.kr, 주문번호 2024-1105-889"
    let model = try await redactor(.nano)
    let both = try await redactor(.nano, regex: true)
    let m = try await model.detect(text), b = try await both.detect(text)
    for s in PIIRegex.spans(in: text) {
      XCTAssertTrue(b.contains { $0.utf16Range.lowerBound <= s.start && s.end <= $0.utf16Range.upperBound }, "\(text[s.range])")
    }
    XCTAssertTrue(b.contains { $0.source != .model })
    XCTAssertGreaterThanOrEqual(b.count, 1)
    XCTAssertTrue(m.allSatisfy { $0.source == .model })
  }

  func testConcurrentDetect() async throws {
    let r = try await redactor(.nano)
    let rows = (try? Fixtures.rows("predict-nano-q4.jsonl").prefix(48).map { $0["text"] as! String })
      ?? ["Call Kim Minjun at 010-1234-5678.", "kim@example.com 🙂", String(repeating: "Lee Jiwoo 이지우 ", count: 120)]
    var serial: [[PIISpan]] = []
    for text in rows { serial.append(try await r.detect(text)) }
    let parallel = try await withThrowingTaskGroup(of: (Int, [PIISpan]).self) { group in
      for round in 0..<4 {
        for (i, text) in rows.enumerated() { group.addTask { (i, try await r.detect(text)) } }
        _ = round
      }
      var out: [(Int, [PIISpan])] = []
      for try await x in group { out.append(x) }
      return out
    }
    XCTAssertEqual(parallel.count, rows.count * 4)
    for (i, spans) in parallel { XCTAssertEqual(spans, serial[i]) }
  }

  @MainActor
  func testMainActorCaller() async throws {
    let r = try await PIIRedactor(size: .nano, modelDirectory: Fixtures.requireModel("nano", "q4"))
    let spans = try await r.detect("kim@example.com")
    XCTAssertEqual(spans.map(\.label), [.email])
  }

  func testDamagedFileIsRejected() async throws {
    let src = try Fixtures.requireModel("nano", "q4")
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("newmean-pii-test-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: dir) }
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    // flat layout (as in an app bundle); the tokenizer is a link, the weights a copy with one byte flipped
    try FileManager.default.createSymbolicLink(at: dir.appendingPathComponent("tokenizer.web.json"),
                                               withDestinationURL: src.appendingPathComponent("tokenizer.web.json"))
    var onnx = try Data(contentsOf: src.appendingPathComponent("onnx/model_q4.onnx"))
    try onnx.write(to: dir.appendingPathComponent("model_q4.onnx"))
    _ = try await PIIRedactor(size: .nano, modelDirectory: dir)  // flat layout loads
    onnx[onnx.count / 2] ^= 1
    try onnx.write(to: dir.appendingPathComponent("model_q4.onnx"))
    do {
      _ = try await PIIRedactor(size: .nano, modelDirectory: dir)
      XCTFail("a damaged file loaded")
    } catch PIIError.verificationFailed {
    }
    do {
      _ = try await PIIRedactor(size: .nano, variant: .q4e, modelDirectory: dir)
      XCTFail("a missing file loaded")
    } catch PIIError.fileNotFound {
    }
  }

  func testStoreRepairsDamagedCache() async throws {
    // the store, served from the local copy (file:// base): a damaged or truncated cached file is fetched again
    let src = try Fixtures.requireModel("nano", "q4")
    let store = PIIModelStore(directory: FileManager.default.temporaryDirectory.appendingPathComponent("newmean-pii-store-\(UUID().uuidString)"))
    defer { try? FileManager.default.removeItem(at: store.directory) }
    let base = URL(fileURLWithPath: src.path, isDirectory: true)
    let dir = try await store.download(.nano, from: base)
    let onnx = dir.appendingPathComponent("onnx/model_q4.onnx"), tok = dir.appendingPathComponent("tokenizer.web.json")
    var data = try Data(contentsOf: onnx)
    data[data.count / 3] ^= 1
    try data.write(to: onnx)
    try Data("{}".utf8).write(to: tok)
    do {
      _ = try await PIIRedactor(size: .nano, modelDirectory: dir)  // offline use of the damaged folder fails
      XCTFail("a damaged cache loaded")
    } catch PIIError.verificationFailed {
    }
    let repaired = try await store.download(.nano, from: base)
    XCTAssertEqual(repaired, dir)
    XCTAssertNoThrow(try PIIModelFile.model(.nano, .q4).verify(Data(contentsOf: onnx)))
    XCTAssertNoThrow(try PIIModelFile.tokenizer(.nano).verify(Data(contentsOf: tok)))
    let leftovers = try FileManager.default.subpathsOfDirectory(atPath: store.directory.path).filter { !$0.hasSuffix(".onnx") && !$0.hasSuffix(".json") }
    XCTAssertEqual(Set(leftovers), ["v1.0.0", "v1.0.0/pii-nano", "v1.0.0/pii-nano/onnx"])
  }

  /// PII_DOWNLOAD=1: downloads nano from Hugging Face into a temporary store (3.4 MB) and loads it.
  func testDownload() async throws {
    guard Fixtures.env["PII_DOWNLOAD"] == "1" else { throw XCTSkip("set PII_DOWNLOAD=1 to download from Hugging Face") }
    let store = PIIModelStore(directory: FileManager.default.temporaryDirectory.appendingPathComponent("newmean-pii-dl-\(UUID().uuidString)"))
    defer { try? FileManager.default.removeItem(at: store.directory) }
    let dir = try await store.download(.nano)
    let again = try await store.download(.nano)  // second call: cached, verified, no download
    XCTAssertEqual(again, dir)
    let r = try await PIIRedactor(size: .nano, modelDirectory: dir)
    let spans = try await r.detect("kim@example.com")
    XCTAssertEqual(spans.map(\.label), [.email])
  }
}
