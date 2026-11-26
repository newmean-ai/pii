// The vault (since 1.7.2): the cases every package shares (python/tests/vault_cases.json) and the Swift API.
import Foundation
import NewmeanPII
import XCTest

final class VaultTests: XCTestCase {
  func testSharedCases() throws {
    let url = Fixtures.root.appendingPathComponent("python/tests/vault_cases.json")
    guard FileManager.default.fileExists(atPath: url.path) else { throw XCTSkip("no \(url.path)") }
    let cases = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
    for g in cases["groups"] as! [[String: Any]] {
      let name = g["name"] as! String
      let v = PIIVault()
      let tags = (g["values"] as! [[String]]).map { v.tag($0[0], $0[1]) }
      XCTAssertEqual(tags, g["tags"] as! [String], name)
      for c in g["redact"] as? [[String: Any]] ?? [] {
        // spans a model found (then the vault's known values): each over the next place its value is written
        let text = c["text"] as! String
        var at = text.startIndex
        var found: [PIISpan] = []
        for f in c["found"] as! [[String]] {
          let r = text.range(of: f[0], options: .literal, range: at..<text.endIndex)!
          let lo = text.utf16.distance(from: text.startIndex, to: r.lowerBound)
          let hi = text.utf16.distance(from: text.startIndex, to: r.upperBound)
          found.append(PIISpan(in: text, utf16Range: lo..<hi, label: PIILabel(rawValue: f[1])!))
          at = r.upperBound
        }
        let (out, spans) = try v.redact(text, spans: found)
        XCTAssertEqual(out, c["want"] as! String, name)
        if let want = c["spans"] as? [[String]] {
          // the final spans as covered text, label and source (PIILabel is an enum: a span of a custom family is
          // labelled PERSON; its placeholder keeps the family)
          XCTAssertEqual(spans.map { [String(text.unicodeScalars[$0.range]), $0.label.rawValue, $0.source.rawValue] },
                         want.map { [$0[0], PIILabel(rawValue: $0[1])?.rawValue ?? "PERSON", $0[2]] }, name)
        }
      }
      if let values = g["entries"] as? [String] { XCTAssertEqual(v.entries.map(\.value), values, name) }
      for c in g["hide"] as? [[String: String]] ?? [] { XCTAssertEqual(v.hide(c["text"]!), c["want"]!, name) }
      for c in g["hide_escaped"] as? [[String: String]] ?? [] { XCTAssertEqual(v.hide(c["text"]!, escape: true), c["want"]!, name) }
      for c in g["hide_value"] as? [[String: Any]] ?? [] {
        let got = v.hideValue(c["value"]!)
        XCTAssertEqual(try JSONSerialization.data(withJSONObject: got, options: .sortedKeys),
                       try JSONSerialization.data(withJSONObject: c["want"]!, options: .sortedKeys), name)
      }
      for c in g["restore"] as? [[String: Any]] ?? [] {
        let scope: PIIVaultScope = (c["only"] as? [String]).map { .tags(Set($0)) } ?? .all
        XCTAssertEqual(v.restore(c["text"] as! String, scope: scope, json: c["json"] as? Bool ?? false), c["want"] as! String, name)
      }
    }
  }

  func testModelSpansLoseWhatTheirBoundaryTookAndShortValuesStayWhereFound() throws {
    // (1.7.1 review, L3-B1, L3-B2 and L3-B4 in this package; the shared cases have the rest)
    let v = PIIVault()
    let t = "page 0 of 7"
    XCTAssertEqual(try v.redact(t, spans: [PIISpan(in: t, utf16Range: 5..<6, label: .dateOfBirth)]).text, "page [DATE_OF_BIRTH_1] of 7")
    XCTAssertEqual(v.hideValue("0") as? String, "0")
    // a value the application gives is found as a whole value whatever its length
    XCTAssertEqual(v.tag("DATE_OF_BIRTH", "0"), "[DATE_OF_BIRTH_1]")
    XCTAssertEqual(v.hideValue("0") as? String, "[DATE_OF_BIRTH_1]")
    // the offsets of a cut span in every unit
    let u = "\u{1F600} (Kim Minsu)."
    let (out, spans) = try v.redact(u, spans: [PIISpan(in: u, utf16Range: 3..<u.utf16.count, label: .person)])
    XCTAssertEqual(out, "\u{1F600} ([PERSON_1]).")
    XCTAssertEqual(spans[0].utf16Range, 4..<13)
    XCTAssertEqual(spans[0].scalarRange, 3..<12)
    XCTAssertEqual(String(u.unicodeScalars[spans[0].range]), "Kim Minsu")
  }

  func testStreamSplitAnywhere() {
    let v = PIIVault()
    v.tag("PERSON", "Kim Minsu")
    v.tag("EMAIL", "kim@example.com")
    let text = Array("Dear [PERSON_1], we wrote to [EMAIL_1]. [x] [")
    for size in [1, 2, 3, 5, 7, 100] {
      let r = v.restorer()
      var out = ""
      var i = 0
      while i < text.count {
        out += r.push(String(text[i..<min(i + size, text.count)]))
        i += size
      }
      out += r.flush()
      XCTAssertEqual(out, "Dear Kim Minsu, we wrote to kim@example.com. [x] [")
    }
  }

  func testSnapshotsEntriesGenerationReplay() throws {
    let v = PIIVault()
    v.tag("PERSON", "Alice")
    let snap = v.bindings(["[PERSON_1]", "[PERSON_9]"])
    XCTAssertEqual(snap, ["[PERSON_1]": "Alice"])
    v.remember("xid: 42y", masked: "x[ID_NUMBER_1]y")
    XCTAssertEqual(v.recall("xid: 42y"), "x[ID_NUMBER_1]y")
    let saved = v.entries
    let g = v.generation
    v.clear()
    XCTAssertEqual(v.count, 0)
    XCTAssertEqual(v.generation, g + 1)
    XCTAssertNil(v.recall("xid: 42y"))
    v.tag("PERSON", "Bob")
    XCTAssertEqual(v.restore("[PERSON_1]", scope: .values(snap)), "Alice")
    XCTAssertEqual(try PIIVault(entries: saved).restore("[PERSON_1]"), "Alice")
    XCTAssertThrowsError(try PIIVault(entries: [PIIVaultEntry(tag: "PERSON_1", label: "PERSON", value: "x")]))
    XCTAssertEqual(escapeTags("a [PERSON_1] [note] [x_1]"), "a \u{FF3B}PERSON_1] [note] [x_1]")
    XCTAssertEqual(vaultTags("[A_1] x [B_22] [c_3]"), ["[A_1]", "[B_22]"])
    XCTAssertEqual(v.tag(String(repeating: "X", count: 50), "value one"), "[\(String(repeating: "X", count: 32))_1]")
  }

  func testSpansMergeAndEscapedGaps() {
    let v = PIIVault()
    v.tag("PERSON", "Kim Minsu")
    let t = "Ask Kim Minsu and Kim Minsuk."
    let spans = v.withVaultSpans(t, spans: [PIISpan(in: t, utf16Range: 4..<7, label: .person)])
    XCTAssertEqual(spans.count, 1)
    XCTAssertEqual(spans[0].utf16Range, 4..<13)
    XCTAssertEqual(spans[0].source, .vault)
    XCTAssertEqual(PIIRedactor.applyRedaction(to: t, spans: spans, vault: v), "Ask [PERSON_1] and Kim Minsuk.")
    let u = "account [REF_1] cobalt, see [REF_1]"
    let block = PIISpan(in: u, utf16Range: 0..<22, label: .secret, source: .block)
    XCTAssertEqual(PIIRedactor.applyRedaction(to: u, spans: [block], vault: v), "[SECRET_1], see \u{FF3B}REF_1]")
    XCTAssertEqual(v.value("[SECRET_1]"), "account [REF_1] cobalt")
  }

  func testFourthReviewFixes() throws {
    try skipTiming()
    // values are told apart by their exact scalars: both spellings of "José" get their own placeholder and round-trip
    let v = PIIVault()
    let composed = "Jos\u{E9}", decomposed = "Jose\u{301}"
    XCTAssertEqual(v.tag("PERSON", composed), "[PERSON_1]")
    XCTAssertEqual(v.tag("PERSON", decomposed), "[PERSON_2]")
    XCTAssertEqual(Array(v.restore("[PERSON_2]").unicodeScalars), Array(decomposed.unicodeScalars))
    XCTAssertEqual(v.hide("a \(decomposed) b \(composed)"), "a [PERSON_2] b [PERSON_1]")
    // an empty value has no placeholder; a family whose numbers are used up goes on in the next one
    let x = try PIIVault(entries: [PIIVaultEntry(tag: "[PERSON_999999999999]", label: "PERSON", value: "Last One")])
    XCTAssertEqual(x.tag("PERSON", ""), "")
    XCTAssertEqual(x.tag("PERSON", "One More"), "[PERSON__1]")
    XCTAssertEqual(x.tag("PERSON", "And Another"), "[PERSON__2]")
    XCTAssertEqual(x.restore("[PERSON__1] [PERSON_999999999999]"), "One More Last One")
    XCTAssertEqual(x.hide("One More and Last One"), "[PERSON__1] and [PERSON_999999999999]")
    XCTAssertEqual(try PIIVault(entries: x.entries).tag("PERSON", "A Fourth"), "[PERSON__3]")  // its own entries load again
    let full = String(repeating: "P", count: 32)
    let longest = try PIIVault(entries: [PIIVaultEntry(tag: "[\(full)_999999999999]", label: full, value: "x")])
    XCTAssertEqual(longest.tag(full, "y"), "[\(String(repeating: "P", count: 31))__1]")
    let lastFamily = "A" + String(repeating: "_", count: 31)  // a letter and underscores only: on to the next letter
    let z = try PIIVault(entries: [PIIVaultEntry(tag: "[\(lastFamily)_999999999999]", label: lastFamily, value: "one")])
    let nextTag = "[B\(String(repeating: "_", count: 31))_1]"
    XCTAssertEqual(z.tag(lastFamily, "two"), nextTag)
    XCTAssertEqual(z.restore(nextTag), "two")
    // after Z it goes on with A; with all 26 used up the value is masked and not put back
    let letterFamily = { (c: String) in c + String(repeating: "_", count: 31) }
    let wrapped = try PIIVault(entries: [PIIVaultEntry(tag: "[\(letterFamily("Z"))_999999999999]", label: letterFamily("Z"), value: "one")])
    XCTAssertEqual(wrapped.tag(letterFamily("Z"), "two"), "[\(letterFamily("A"))_1]")
    XCTAssertEqual(wrapped.restore("[\(letterFamily("A"))_1]"), "two")
    let letters = (0..<26).map { String(UnicodeScalar(UInt8(65 + $0))) }
    let usedUp = try PIIVault(entries: letters.map { PIIVaultEntry(tag: "[\(letterFamily($0))_999999999999]", label: letterFamily($0), value: "v" + $0) })
    XCTAssertEqual(usedUp.tag(letterFamily("Q"), "more"), "[Q]")
    XCTAssertEqual(usedUp.restore("[Q]"), "[Q]")
    XCTAssertThrowsError(try PIIVault(entries: [PIIVaultEntry(tag: "[PERSON_1000000000000]", label: "PERSON", value: "x")]))
    // spans, known values and placeholders are one step: nothing is written into a vault that was cleared since
    let w = PIIVault()
    let t = "Alice Bob"
    let spans = [PIISpan(in: t, utf16Range: 0..<5, label: .person), PIISpan(in: t, utf16Range: 6..<9, label: .person)]
    let started = w.generation
    w.clear()
    XCTAssertThrowsError(try w.redact(t, spans: spans, generation: started)) { XCTAssertEqual($0 as? PIIVaultError, .cleared) }
    XCTAssertEqual(w.count, 0)
    XCTAssertEqual(try w.redact(t, spans: spans, generation: w.generation).text, "[PERSON_1] [PERSON_2]")
    // scanning for placeholders is linear in the text
    let brackets = String(repeating: "[", count: 100_000) + String(repeating: "[A", count: 50_000)
    let clock = Date()
    XCTAssertTrue(vaultTags(brackets).isEmpty)
    XCTAssertLessThan(Date().timeIntervalSince(clock), 5)
  }

  func testFifthReviewFixes() throws {
    // a value the model found once is masked wherever it occurs again in the same text, on the first call
    let v = PIIVault()
    let t = "I met Alice yesterday. Use the word Alice twice, not Alicette."
    let got = try v.redact(t, spans: [PIISpan(in: t, utf16Range: 6..<11, label: .person)])
    XCTAssertEqual(got.text, "I met [PERSON_1] yesterday. Use the word [PERSON_1] twice, not Alicette.")
    XCTAssertEqual(got.spans.map(\.utf16Range), [6..<11, 36..<41])
    XCTAssertEqual(got.spans.map(\.source), [.model, .vault])
    // what the model found inside a larger merged span is masked where it stands alone
    let w = PIIVault()
    w.tag("PERSON", "Alex Kim")
    let u = "Alex Kim Lee wrote. Kim Lee signed. Alex Kim Lee left."
    let merged = try w.redact(u, spans: [PIISpan(in: u, utf16Range: 5..<12, label: .person)]).text
    XCTAssertFalse(merged.contains("Alex") || merged.contains("Kim") || merged.contains("Lee"), merged)
    XCTAssertEqual(w.restore(merged), u)
    // a family with digits in its name stays apart from the numbers of another
    let x = try PIIVault(entries: [
      PIIVaultEntry(tag: "[PERSON_1]", label: "PERSON", value: "Alice"),
      PIIVaultEntry(tag: "[PERSON_1_99999999999]", label: "PERSON_1", value: "Last"),
    ])
    XCTAssertEqual(x.tag("PERSON_1", "Next"), "[PERSON_1_100000000000]")
    XCTAssertEqual(x.restore("[PERSON_1] [PERSON_1_100000000000]"), "Alice Next")
    // masking goes on until nothing new is found, however many rounds that takes
    let c = PIIVault()
    c.tag("PERSON", "W00")
    let words = (0..<10).map { String(format: "W%02d", $0) }
    var line = ""
    for n in 2...words.count {
      line = words[..<n].joined(separator: " ")
      let tail = [PIISpan(in: line, utf16Range: (line.utf16.count - 5)..<line.utf16.count, label: .person)]
      XCTAssertFalse(try c.redact(line, spans: tail).text.contains("W0"))
    }
    let again = try c.redact(line, spans: [PIISpan(in: line, utf16Range: (line.utf16.count - 5)..<line.utf16.count, label: .person)]).text
    XCTAssertFalse(again.contains("W0"), again)
    XCTAssertEqual(c.restore(again), line)
    // restoreValue takes the values of one moment for every string
    let r = PIIVault()
    r.tag("PERSON", "Alice")
    let stop = DispatchSemaphore(value: 0)
    var running = true
    let lock = NSLock()
    DispatchQueue.global().async {
      var i = 0
      while lock.withLock({ running }) {
        r.clear()
        r.tag("PERSON", i % 2 == 0 ? "Bob" : "Alice")
        i += 1
      }
      stop.signal()
    }
    for _ in 0..<20 {
      let got = r.restoreValue(Array(repeating: "[PERSON_1]", count: 50)) as! [String]
      XCTAssertEqual(Set(got).count, 1)
    }
    lock.withLock { running = false }
    stop.wait()
  }

  /// `s` in full-width ASCII (U+FF01 to U+FF5E).
  private func fullWidth(_ s: String) -> String {
    String(String.UnicodeScalarView(s.unicodeScalars.map { $0.value >= 0x21 && $0.value <= 0x7E ? Unicode.Scalar($0.value + 0xFEE0)! : $0 }))
  }

  /// Text from code points (conjoining Hangul jamo and the like, kept out of the source as characters).
  private func points(_ cps: [UInt32]) -> String { String(String.UnicodeScalarView(cps.map { Unicode.Scalar($0)! })) }

  func testSpellingsKeepOnePlaceholder() throws {
    // what the model finds in another spelling of a known value gets that value's placeholder; the vault learns no new value
    let v = PIIVault()
    v.tag("PERSON", "Kim Minsu")
    let t = "KIM MINSU wrote to Kim Minsu."
    let model = PIISpan(in: t, utf16Range: 0..<9, label: .person)
    XCTAssertEqual(try v.redact(t, spans: [model]).text, "[PERSON_1] wrote to [PERSON_1].")
    XCTAssertEqual(v.count, 1)
    XCTAssertEqual(PIIRedactor.applyRedaction(to: t, spans: [model], vault: v), "[PERSON_1] wrote to Kim Minsu.")
    // a one-word value in another case is another value
    let u = "Will will"
    XCTAssertEqual(try v.redact(u, spans: [PIISpan(in: u, utf16Range: 5..<9, label: .person)]).text, "Will [PERSON_2]")
    XCTAssertEqual(v.count, 2)
    // spans of values found in a reading point into the text as it is written: full-width letters and NFD Hangul
    let w = PIIVault()
    w.tag("EMAIL", "kim@example.com")
    w.tag("PERSON", points([0xAE40, 0xBBFC, 0xC218]))
    let nfdName = points([0x1100, 0x1175, 0x11B7, 0x1106, 0x1175, 0x11AB, 0x1109, 0x116E])
    let nfdParticle = points([0x1102, 0x1175, 0x11B7])
    let x = fullWidth("kim@example.com") + " and " + nfdName + nfdParticle
    let spans = w.withVaultSpans(x, spans: [])
    XCTAssertEqual(spans.map(\.scalarRange), [0..<15, 20..<28])
    XCTAssertEqual(spans.map(\.utf16Range), [0..<15, 20..<28])
    XCTAssertEqual(spans.map(\.source), [.vault, .vault])
    XCTAssertEqual(spans.map { Array(x.unicodeScalars[$0.range]) }, [Array(fullWidth("kim@example.com").unicodeScalars), Array(nfdName.unicodeScalars)])
    let masked = PIIRedactor.applyRedaction(to: x, spans: spans, vault: w)
    XCTAssertEqual(Array(masked.unicodeScalars), Array(("[EMAIL_1] and [PERSON_1]" + nfdParticle).unicodeScalars))
    XCTAssertEqual(w.count, 2)
    // hideValue: a whole string in another spelling, whatever its length
    let y = PIIVault()
    y.tag("PERSON", "Al")
    XCTAssertEqual(y.hideValue(["AL", "al", fullWidth("Al")]) as? [String], ["[PERSON_1]", "al", "[PERSON_1]"])
  }

  func testRedactWithNano() async throws {
    let dir = try Fixtures.requireModel("nano", "q4")
    let r = try await PIIRedactor(size: .nano, modelDirectory: dir)
    let text = "Please call Kim Minsu on 010-1234-5678 or write to kim@example.com."
    let plain = try await r.redact(text)
    let v = PIIVault()
    let numbered = try await r.redact(text, vault: v)
    XCTAssertEqual(numbered.spans, plain.spans)
    XCTAssertTrue(numbered.text.contains("[EMAIL_1]"))
    XCTAssertEqual(v.restore(numbered.text), text)
    let again = try await r.redact("kim@example.com again", vault: v)
    XCTAssertEqual(again.text, "[EMAIL_1] again")
  }
}
