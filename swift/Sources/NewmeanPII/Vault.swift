// A vault: the local map from numbered placeholders back to the values they replace, so that masking can be undone in a
// model's answer (since 1.7.2). It is an ordinary in-memory object; nothing in it is sent anywhere. The placeholders and
// the search for known values are those of the npm package's createVault() and the Python package's Vault (shared cases:
// python/tests/vault_cases.json); the other spellings the npm and Python middlewares also read (escapes, URL forms,
// numbers written another way, other white space) are not read here.
//
//   let vault = PIIVault()   // one per user or conversation, never shared between users
//   let masked = try redactor.redact("Call Kim Minsu on 010-1234-5678", vault: vault).text  // "Call [PERSON_1] on [PHONE_1]"
//   vault.restore("I called [PERSON_1].")                                                   // "I called Kim Minsu."
//
// The same value always gets the same placeholder within a vault, and a value the vault holds is masked again wherever it
// reappears in later text, also where the model would miss it there (spans with source `.vault`): at word edges, 3
// characters or more (2 in scripts written without spaces, and two Hangul syllables, after which only particles, 님, 씨
// or the copula may follow in the same word). It is found as it is written and also: with full-width ASCII (U+FF01 to
// U+FF5E) and U+3000 read as ASCII; with Hangul written as conjoining jamo (NFD) read as syllables; in capitals (the full
// Unicode uppercase, "Weiß" as "WEISS"; a Greek value also without its accents, as Greek writes capitals; a sharp s also
// as the capital sharp s); and, for an e-mail address, a user name, a URL's scheme and host and a value of two or more
// words, in any ASCII case. A one-word value in another case is another word ("Will" and "will"). The hit carries the
// known value, so one value keeps one placeholder however it is written, and it takes in the marks, variation selectors
// and joiners right after it, so that it never ends inside a grapheme cluster.
import Foundation

/// One placeholder and the value behind it.
public struct PIIVaultEntry: Sendable, Hashable, Codable {
  /// The placeholder, e.g. "[PERSON_1]".
  public let tag: String
  /// Label family the placeholder was made for (PERSON_L2 shares PERSON's numbers).
  public let label: String
  /// The original text.
  public let value: String

  public init(tag: String, label: String, value: String) {
    self.tag = tag
    self.label = label
    self.value = value
  }
}

/// Where a known value occurs, in Unicode scalars.
public struct PIIVaultHit: Sendable, Hashable {
  public let scalarRange: Range<Int>
  public let label: String
  public let value: String
}

/// Which placeholders a restore may put back.
public enum PIIVaultScope: Sendable {
  /// Every placeholder of the vault, with its current value.
  case all
  /// Only these placeholders, with their current values.
  case tags(Set<String>)
  /// Only these placeholders, with these values (a snapshot from `bindings`).
  case values([String: String])
}

public enum PIIVaultError: Error, Equatable {
  case notAnEntry(String)
  case duplicateTag(String)
  /// The vault was cleared while a text was being masked into it; nothing was written.
  case cleared
}

private let maxFamily = 32
private let maxTag = maxFamily + 15
private let maxNumber = 999_999_999_999

/// The family that goes on when the numbers of `family` are used up (which takes an imported placeholder with the
/// highest number): one more "_" at its end, "PERSON" → "PERSON_", so that its placeholders stay apart
/// ("[PERSON__1]"). A family of full length gets its last other character turned into "_" instead, and one that is a
/// letter and underscores only goes on with the next letter (after Z, with A).
private func nextFamily(_ family: String) -> String {
  var s = Array(family.unicodeScalars)
  if s.count < maxFamily { return family + "_" }
  if let i = s.indices.dropFirst().last(where: { s[$0] != "_" }) {
    s[i] = "_"
  } else {
    s[0] = Unicode.Scalar(s[0].value < 0x5A ? s[0].value + 1 : 0x41) ?? "A"
  }
  return String(String.UnicodeScalarView(s))
}
/// How many families are tried after the first: at most 31 steps to a letter and underscores, then the 25 other letters.
private let familySteps = 56
/// Values are told apart by their exact bytes: Swift compares strings by canonical equivalence, which would give
/// "José" written with one scalar and with two the same placeholder and then restore the wrong spelling.
private func bytes(_ s: String) -> [UInt8] { Array(s.utf8) }
/// U+FF3B FULLWIDTH LEFT SQUARE BRACKET, which `escapeTags` puts in front of placeholder-like text.
private let escapedBracket: Unicode.Scalar = "\u{FF3B}"

private func isUpper(_ c: Unicode.Scalar) -> Bool { c.value >= 0x41 && c.value <= 0x5A }
private func isDigit(_ c: Unicode.Scalar) -> Bool { c.value >= 0x30 && c.value <= 0x39 }

/// A placeholder at `i` of `s`: its length in scalars, family and number. The grammar is the same in every package:
/// "[" + family + "_" + number + "]", where the family is an ASCII capital letter followed by capitals, digits and
/// underscores (32 characters at most) and the number is 1 to 12 ASCII digits. Longer look-alikes are ordinary text:
/// neither escaped nor restored.
private func parseTag(_ s: [Unicode.Scalar], at i: Int) -> (length: Int, family: String, number: String)? {
  guard i < s.count, s[i] == "[" else { return nil }
  var j = i + 1
  while j < s.count, j - i <= maxTag, s[j] != "]" {
    let c = s[j]
    if !(isUpper(c) || isDigit(c) || c == "_") { return nil }
    j += 1
  }
  guard j < s.count, s[j] == "]" else { return nil }
  let inner = String(String.UnicodeScalarView(s[(i + 1)..<j]))
  guard let us = inner.lastIndex(of: "_") else { return nil }
  let fam = String(inner[..<us]), num = String(inner[inner.index(after: us)...])
  guard fam.unicodeScalars.count <= maxFamily, (1...12).contains(num.unicodeScalars.count) else { return nil }
  guard let f = fam.unicodeScalars.first, isUpper(f), num.unicodeScalars.allSatisfy(isDigit) else { return nil }
  return (j + 1 - i, fam, num)
}

/// `fam` without trailing "_" + digits groups, so that "[fam]" is never a numbered placeholder.
private func unnumbered(_ fam: String) -> String {
  var s = Array(fam.unicodeScalars)
  while true {
    var i = s.count
    while i > 0, isDigit(s[i - 1]) { i -= 1 }
    guard i > 0, s[i - 1] == "_" else { break }
    s.removeSubrange((i - 1)...)
  }
  return String(String.UnicodeScalarView(s))
}

private func tagPositions(_ s: [Unicode.Scalar]) -> [(Int, Int)] {
  var out: [(Int, Int)] = []
  var i = 0
  while i < s.count {
    if s[i] == "[", let t = parseTag(s, at: i) {
      out.append((i, t.length))
      i += t.length
    } else {
      i += 1
    }
  }
  return out
}

/// The vault placeholders in `text`, in order of appearance (duplicates kept).
public func vaultTags(_ text: String) -> [String] {
  let s = Array(text.unicodeScalars)
  return tagPositions(s).map { String(String.UnicodeScalarView(s[$0.0..<($0.0 + $0.1)])) }
}

/// `text` with every placeholder-like part that was already in it ("[PERSON_1]") turned into "［PERSON_1]".
public func escapeTags(_ text: String) -> String {
  guard text.contains("[") else { return text }
  var s = Array(text.unicodeScalars)
  for (at, _) in tagPositions(s) { s[at] = escapedBracket }
  return String(String.UnicodeScalarView(s))
}

// Scripts written without spaces between words (letters and marks of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese,
// Javanese, Han, Hiragana and Katakana), and Hangul: the same code point ranges as the npm and Python packages.
private let noSpaceRanges: [ClosedRange<UInt32>] = [
  0x0E01...0x0E3A, 0x0E40...0x0E4E,  // Thai
  0x0E81...0x0ECF,  // Lao
  0x1000...0x103F, 0x1050...0x108F, 0x109A...0x109D, 0xA9E0...0xA9EF, 0xA9FA...0xA9FE, 0xAA60...0xAA7F,  // Myanmar
  0x1780...0x17D3, 0x17D7...0x17D7, 0x17DC...0x17DD, 0x19E0...0x19FF,  // Khmer
  0x1A20...0x1A5E, 0x1A60...0x1A7C, 0x1A7F...0x1A7F, 0x1AA7...0x1AA7,  // Tai Tham
  0x1B00...0x1B4C,  // Balinese
  0xA980...0xA9C0, 0xA9CF...0xA9CF,  // Javanese
  0x2E80...0x2FDF, 0x3005...0x3005, 0x3007...0x3007, 0x3021...0x3029, 0x3038...0x303B,  // Han radicals, marks
  0x3041...0x3096, 0x309D...0x309F,  // Hiragana
  0x30A1...0x30FA, 0x30FD...0x30FF, 0x31F0...0x31FF, 0xFF66...0xFF6F, 0xFF71...0xFF9D,  // Katakana
  0x1B000...0x1B16F,  // kana supplement and extensions
  0x3400...0x4DBF, 0x4E00...0x9FFF, 0xF900...0xFAFF, 0x20000...0x3347F,  // Han, to Extension J
]
private let hangulRanges: [ClosedRange<UInt32>] = [0x1100...0x11FF, 0x3131...0x318E, 0xA960...0xA97F, 0xAC00...0xD7A3, 0xD7B0...0xD7FF, 0xFFA0...0xFFDC]
private func noSpace(_ c: Unicode.Scalar) -> Bool { noSpaceRanges.contains { $0.contains(c.value) } }
// The marks among them (vowel signs, tone marks and the like of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese and
// Javanese; Unicode general category M): a mark belongs to the letter before it, so the place before one is no word edge.
private let noSpaceMarkRanges: [ClosedRange<UInt32>] = [
  0x0E31...0x0E31, 0x0E34...0x0E3A, 0x0E47...0x0E4E,  // Thai
  0x0EB1...0x0EB1, 0x0EB4...0x0EBC, 0x0EC8...0x0ECE,  // Lao
  0x102B...0x103E, 0x1056...0x1059, 0x105E...0x1060, 0x1062...0x1064, 0x1067...0x106D, 0x1071...0x1074,
  0x1082...0x108D, 0x108F...0x108F, 0x109A...0x109D, 0xA9E5...0xA9E5, 0xAA7B...0xAA7D,  // Myanmar
  0x17B4...0x17D3, 0x17DD...0x17DD,  // Khmer
  0x1A55...0x1A5E, 0x1A60...0x1A7C, 0x1A7F...0x1A7F,  // Tai Tham
  0x1B00...0x1B04, 0x1B34...0x1B44,  // Balinese
  0xA980...0xA983, 0xA9B3...0xA9C0,  // Javanese
]
private func noSpaceMark(_ c: Unicode.Scalar) -> Bool { noSpaceMarkRanges.contains { $0.contains(c.value) } }
private func hangul(_ c: Unicode.Scalar) -> Bool { hangulRanges.contains { $0.contains(c.value) } }
/// A number character (Unicode general category N).
private func isNumber(_ c: Unicode.Scalar?) -> Bool {
  guard let c else { return false }
  switch c.properties.generalCategory {
  case .decimalNumber, .letterNumber, .otherNumber: return true
  default: return false
  }
}
/// Letters, numbers, marks and "_" (Unicode general categories L, N, M).
private func isWord(_ c: Unicode.Scalar) -> Bool {
  if c == "_" { return true }
  switch c.properties.generalCategory {
  case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter,
       .decimalNumber, .letterNumber, .otherNumber, .nonspacingMark, .spacingMark, .enclosingMark:
    return true
  default:
    return false
  }
}

/// A precomposed Hangul syllable (U+AC00 to U+D7A3).
private func syllable(_ c: Unicode.Scalar) -> Bool { c.value >= 0xAC00 && c.value <= 0xD7A3 }

/// White space as Unicode has it (the same set in every package).
private func isSpace(_ c: Unicode.Scalar) -> Bool {
  switch c.value {
  case 0x09...0x0D, 0x20, 0x85, 0xA0, 0x1680, 0x2000...0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000: return true
  default: return false
  }
}

/// What a value of two Hangul syllables may have right after it in the same word, one piece after another to the end of
/// the run of syllables: particles, the honorifics 님 and 씨, and the copula. Any other syllable makes a longer word
/// ("지원금", "정수기"), and a two-syllable name is often a word too. The same list in every package.
private let koTailPieces: [[Unicode.Scalar]] = [
  "가", "같이", "고", "과", "까지", "께", "께서", "나", "나마", "네", "는", "니까", "님", "다", "대로", "더러", "도", "든", "든지", "라고",
  "라는", "라도", "라서", "랑", "로", "로서", "로써", "를", "마다", "마저", "만", "만큼", "며", "밖에", "보다", "부터", "씨", "아", "야",
  "어", "었", "에", "에게", "에게서", "에서", "여", "였", "예요", "와", "요", "으로", "으로서", "으로써", "은", "을", "의", "이", "이면",
  "인", "인데", "입니다", "조차", "죠", "지", "지만", "처럼", "한테", "한테서",
].map { Array($0.unicodeScalars) }
/// The longest run of syllables `koTail` reads; a longer one is another word.
private let koTailMax = 8

/// Whether the Hangul syllables of `t` from `at` (right after a value of two syllables) are pieces of `koTailPieces`,
/// one after another to the end of the run.
private func koTail(_ t: [Unicode.Scalar], _ at: Int) -> Bool {
  var end = at
  while end < t.count, end - at <= koTailMax, syllable(t[end]) { end += 1 }
  let run = Array(t[at..<end])
  if run.count > koTailMax { return false }
  var ok = [Bool](repeating: false, count: run.count + 1)  // ok[i]: run[i...] is pieces of the list
  ok[run.count] = true
  for i in stride(from: run.count - 1, through: 0, by: -1) {
    ok[i] = koTailPieces.contains { p in i + p.count <= run.count && ok[i + p.count] && run[i..<(i + p.count)].elementsEqual(p) }
  }
  return ok[0]
}

/// What joins the character before it into one grapheme cluster, so that a hit takes it in ("渡辺" and a variation
/// selector, "김민수" and a combining mark): marks (general category M), the zero width joiner and non-joiner,
/// conjoining Hangul vowels and finals, the half-width katakana sound marks, emoji modifiers and tag characters. The same
/// set in every package.
private func joins(_ c: Unicode.Scalar) -> Bool {
  switch c.value {
  case 0x200C, 0x200D, 0x1160...0x11FF, 0xD7B0...0xD7FF, 0xFF9E, 0xFF9F, 0x1F3FB...0x1F3FF, 0xE0020...0xE007F: return true
  default:
    switch c.properties.generalCategory {
    case .nonspacingMark, .spacingMark, .enclosingMark: return true
    default: return false
    }
  }
}

/// `end` moved past what joins the character before it (see `joins`).
private func widen(_ t: [Unicode.Scalar], _ end: Int) -> Int {
  var end = end
  while end < t.count, joins(t[end]) { end += 1 }
  return end
}

/// Greek capitals that carry an accent or a breathing, as ranges (first, last, the capital without it): the tonos
/// capitals, the polytonic ones and those with varia or oxia (the dialytika is kept). From the Unicode decompositions
/// (U+0300, U+0301, U+0313, U+0314, U+0342, U+0343 left out); the same table in every package.
private let greekBare: [(UInt32, UInt32, UInt32)] = [
  (0x386, 0x386, 0x391), (0x388, 0x388, 0x395), (0x389, 0x389, 0x397), (0x38A, 0x38A, 0x399), (0x38C, 0x38C, 0x39F),
  (0x38E, 0x38E, 0x3A5), (0x38F, 0x38F, 0x3A9), (0x3D3, 0x3D3, 0x3D2), (0x1F08, 0x1F0F, 0x391), (0x1F18, 0x1F1D, 0x395),
  (0x1F28, 0x1F2F, 0x397), (0x1F38, 0x1F3F, 0x399), (0x1F48, 0x1F4D, 0x39F), (0x1F59, 0x1F59, 0x3A5), (0x1F5B, 0x1F5B, 0x3A5),
  (0x1F5D, 0x1F5D, 0x3A5), (0x1F5F, 0x1F5F, 0x3A5), (0x1F68, 0x1F6F, 0x3A9), (0x1FBA, 0x1FBB, 0x391), (0x1FC8, 0x1FC9, 0x395),
  (0x1FCA, 0x1FCB, 0x397), (0x1FDA, 0x1FDB, 0x399), (0x1FEA, 0x1FEB, 0x3A5), (0x1FEC, 0x1FEC, 0x3A1), (0x1FF8, 0x1FF9, 0x39F),
  (0x1FFA, 0x1FFB, 0x3A9),
]

/// A Greek letter (or a Coptic one of the Greek block).
private func greek(_ c: Unicode.Scalar) -> Bool { (0x370...0x3FF).contains(c.value) || (0x1F00...0x1FFF).contains(c.value) }

/// `up` (a value in capitals) as Greek writes capitals: without accents and breathings on Greek letters, the dialytika
/// kept ("ΝΊΚΟΣ" as "ΝΙΚΟΣ"); nil when `up` has no Greek letter.
private func greekCapitals(_ up: [Unicode.Scalar]) -> [Unicode.Scalar]? {
  guard up.contains(where: greek) else { return nil }
  var out: [Unicode.Scalar] = []
  var afterGreek = false  // the last letter that is not a combining mark is Greek
  for c in up {
    var cp = c.value
    if (0x300...0x36F).contains(cp) {
      if afterGreek {
        if [0x300, 0x301, 0x313, 0x314, 0x342, 0x343].contains(cp) { continue }
        if cp == 0x344 { cp = 0x308 }  // (dialytika and tonos: the dialytika stays)
        if cp == 0x308, let last = out.last, last.value == 0x399 || last.value == 0x3A5 {
          out[out.count - 1] = Unicode.Scalar(UInt32(last.value == 0x399 ? 0x3AA : 0x3AB))!
          continue
        }
      }
      out.append(Unicode.Scalar(cp)!)
      continue
    }
    afterGreek = greek(c)
    let bare = greekBare.first { cp >= $0.0 && cp <= $0.1 }.flatMap { Unicode.Scalar($0.2) }
    out.append(bare ?? c)
  }
  return out
}

/// `v` in capitals: the full Unicode uppercase ("Weiß" as "WEISS").
private func upper(_ v: [Unicode.Scalar]) -> [Unicode.Scalar] { v.flatMap { $0.properties.uppercaseMapping.unicodeScalars } }

/// The spellings of a value in capitals that `find` also matches, each way a language writes them: its full Unicode
/// uppercase ("WEISS" for "Weiß", "EMINE ŞAHIN"), for a value with "i" the Turkish and Azerbaijani capitals with "İ"
/// ("EMİNE ŞAHİN"), for a Greek value the capitals without accents and breathings ("ΕΛΕΝΗ" for "Ελένη"), and for a value
/// with a sharp s the capitals with the capital sharp s ("WEIẞ"). Spellings that are `r` itself, or that the caseless
/// match covers, are left out. The same spellings as the other packages.
private func capitals(_ r: [Unicode.Scalar], _ caseless: Int) -> [[Unicode.Scalar]] {
  var out: [[Unicode.Scalar]] = []
  func add(_ s: [Unicode.Scalar]) { if s != r && !out.contains(s) { out.append(s) } }
  let up = upper(r)
  if !(caseless == r.count && up == asciiUpper(r)) { add(up) }
  if let g = greekCapitals(up) { add(g) }
  if r.contains(where: { $0.value == 0x69 }) { add(upper(r.map { $0.value == 0x69 ? Unicode.Scalar(UInt32(0x130))! : $0 })) }
  if r.contains(where: { $0.value == 0xDF }) { add(upper(r.map { $0.value == 0xDF ? Unicode.Scalar(UInt32(0x1E9E))! : $0 })) }
  return out
}

/// Long enough to be found again at word edges: 3 characters, or 2 when one is in a script written without spaces or
/// both are Hangul syllables (Korean particles may follow them).
private func longEnough(_ v: [Unicode.Scalar]) -> Bool {
  v.count >= 3 || (v.count == 2 && (v.contains(where: noSpace) || v.allSatisfy(syllable)))
}

// What a span's edges can hold that is not part of the value (all in the basic plane): white space (Unicode
// White_Space); sentence punctuation (. , ; : ! ? and the ellipsis, ideographic and full-width forms); brackets and
// quotation marks, each with its partner. As in the npm and Python packages.
private let edgeSpace: Set<UInt32> = [
  0x9, 0xA, 0xB, 0xC, 0xD, 0x20, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008,
  0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
]
private let edgeStop: Set<UInt32> = [0x2E, 0x2C, 0x3B, 0x3A, 0x21, 0x3F, 0x2026, 0x3001, 0x3002, 0xFF01, 0xFF0C, 0xFF0E, 0xFF1A, 0xFF1B, 0xFF1F]
private let edgeBrackets: [(UInt32, UInt32)] = [
  (0x28, 0x29), (0x5B, 0x5D), (0x7B, 0x7D), (0x3C, 0x3E), (0x201C, 0x201D), (0x2018, 0x2019), (0xAB, 0xBB), (0x300C, 0x300D),
  (0x300E, 0x300F), (0xFF08, 0xFF09), (0xFF3B, 0xFF3D), (0xFF5B, 0xFF5D), (0x3008, 0x3009), (0x300A, 0x300B), (0x3010, 0x3011),
  (0xFF1C, 0xFF1E), (0x3014, 0x3015),
]
private let closerOf = Dictionary(uniqueKeysWithValues: edgeBrackets.map { ($0.0, $0.1) })  // an opening bracket: its closing one
private let openerOf = Dictionary(uniqueKeysWithValues: edgeBrackets.map { ($0.1, $0.0) })  // a closing bracket: its opening one
private let edgeQuote: Set<UInt32> = [0x22, 0x27, 0x60]  // " ' and `, their own partners

/// A date as ISO 8601 writes it, followed by "T" and a digit: the start of an ISO timestamp.
private func isoStamp(_ c: ArraySlice<Unicode.Scalar>) -> Bool {
  let v = Array(c.prefix(12)).map(\.value)
  let d = { (i: Int) in v[i] >= 0x30 && v[i] <= 0x39 }
  return v.count == 12 && (0..<4).allSatisfy(d) && v[4] == 0x2D && d(5) && d(6) && v[7] == 0x2D && d(8) && d(9) && (v[10] == 0x54 || v[10] == 0x74) && d(11)
}

/// The scalars `[start, end)` of `t` (a text's scalars), a span a detector found with `label`, without what its edges
/// hold that is not part of the value: a model can mark "Kim Minsu." at the end of a sentence, an address with the " ("
/// after it, a name with the quotation marks around it or a bytes literal's "b'", and the vault would learn that as the
/// value. At either edge white space goes. Then, except in a SECRET (a password can end with "!" or hold brackets):
/// sentence punctuation (at the start not a ".", and at the end of an ORG not its ".": "Tiffany & Co."); a bracket whose
/// partner is not in the span, a bracket that opens at the end or closes at the start; a straight quotation mark that is
/// the only one of its kind left in the span; a pair of brackets, or the only two of one straight quotation mark, around
/// all the rest; and the "b'" of a Python bytes literal at the start. A date of birth that goes on into the time of its
/// ISO timestamp ("1990-01-02T00") is cut at the date, the edge where the vault finds a known date in a timestamp. A
/// bracket whose partner is inside stays ("(02) 1234-5678"). nil when nothing but white space and punctuation is left;
/// the span without its white space when what is left is too short to be found again (`longEnough`). One pass over the
/// span (the bracket partners in an array): time in proportion to its length. The same in every package.
private func valueEdges(_ t: [Unicode.Scalar], _ start: Int, _ end: Int, _ label: PIILabel) -> (Int, Int)? {
  let values = t[start..<end].map(\.value)  // (at i - start)
  // the partner of each bracket in the span (one pass: a stack for each kind of bracket), and how many of each straight
  // quotation mark the span holds, kept as its edges go
  var partner = [Int](repeating: -1, count: values.count)  // (-1: none)
  var opened: [UInt32: [Int]] = [:]
  var quotes: [UInt32: Int] = [:]
  for (i, c) in values.enumerated() {
    if closerOf[c] != nil {
      opened[c, default: []].append(i)
    } else if let o = openerOf[c] {
      if let j = opened[o]?.popLast() {
        partner[i] = j
        partner[j] = i
      }
    } else if edgeQuote.contains(c) {
      quotes[c, default: 0] += 1
    }
  }
  var a = 0
  var b = values.count
  while a < b && edgeSpace.contains(values[b - 1]) { b -= 1 }
  while a < b && edgeSpace.contains(values[a]) { a += 1 }
  let (ws, we) = (a, b)
  while a < b && label != .secret {
    let last = values[b - 1]
    if edgeSpace.contains(last) || (edgeStop.contains(last) && !(last == 0x2E && label == .org))
      || closerOf[last] != nil  // a bracket that opens at the end
      || (openerOf[last] != nil && partner[b - 1] < a)  // one that closes what the span does not open
      || (edgeQuote.contains(last) && quotes[last] == 1)
    {
      quotes[last] = nil
      b -= 1
      continue
    }
    let first = values[a]
    if edgeSpace.contains(first) || (edgeStop.contains(first) && first != 0x2E)
      || openerOf[first] != nil  // a bracket that closes at the start
      || (closerOf[first] != nil && !(0..<b).contains(partner[a]))  // one that opens what the span does not close
      || (edgeQuote.contains(first) && quotes[first] == 1)
    {
      quotes[first] = nil
      a += 1
      continue
    }
    // a pair around all the rest
    if b - a >= 2
      && ((closerOf[first] != nil && partner[a] == b - 1) || (edgeQuote.contains(first) && last == first && quotes[first] == 2))
    {
      quotes[first] = nil
      a += 1
      b -= 1
      continue
    }
    // the "b'" of a Python bytes literal (not a "b" at the end of a word: "Bob'")
    if first == 0x62 && b - a >= 2 && edgeQuote.contains(values[a + 1]) && !(start + a > 0 && isWord(t[start + a - 1])) {
      if let k = quotes[values[a + 1]] { quotes[values[a + 1]] = k - 1 }
      a += 2
      continue
    }
    break
  }
  if label == .dateOfBirth && b - a > 11 && isoStamp(t[(start + a)..<(start + a + 12)]) { b = a + 10 }
  if a >= b { return nil }
  if (a, b) == (ws, we) || longEnough(Array(t[(start + a)..<(start + b)])) { return (start + a, start + b) }
  return (start + ws, start + we)
}

/// `spans` of `text` with `valueEdges`, for the vault to learn; a span with nothing left is left out. A block-list span
/// (its entry is exact) and a known value's span stay as they are.
private func valueSpans(_ text: String, _ spans: [PIISpan]) -> [PIISpan] {
  guard !spans.isEmpty else { return spans }
  let t = Array(text.unicodeScalars)
  return spans.compactMap { s in
    let (start, end) = (s.scalarRange.lowerBound, s.scalarRange.upperBound)
    if s.source == .block || s.source == .vault || !(0 <= start && start < end && end <= t.count) { return s }
    guard let edges = valueEdges(t, start, end, s.label) else { return nil }
    let (a, b) = edges
    if a == start && b == end { return s }
    let units = { (x: ArraySlice<Unicode.Scalar>) in x.reduce(0) { $0 + UTF16.width($1) } }
    let scalars = text.unicodeScalars
    let lo = scalars.index(s.range.lowerBound, offsetBy: a - start)
    let hi = scalars.index(s.range.upperBound, offsetBy: b - end)
    let u = (s.utf16Range.lowerBound + units(t[start..<a]))..<(s.utf16Range.upperBound - units(t[b..<end]))
    return PIISpan(range: lo..<hi, utf16Range: u, scalarRange: a..<b, label: s.label, source: s.source)
  }
}

/// Whether `c` changes in the vault's reading of a text (full-width ASCII, U+3000, a conjoining Hangul vowel or final).
private func readDifferently(_ c: Unicode.Scalar) -> Bool {
  switch c.value {
  case 0xFF01...0xFF5E, 0x3000, 0x1161...0x1175, 0x11A8...0x11C2: return true
  default: return false
  }
}

/// The text as the vault reads it: full-width ASCII (U+FF01 to U+FF5E) and U+3000 as ASCII, and Hangul written as
/// conjoining jamo (NFD, as macOS file names have it) composed into syllables, as NFC composes them. `map[i]` is where
/// scalar `i` of the reading starts in the text, and `map[count]` is the text's length; there is no map when the reading
/// is the text.
private struct Reading {
  let scalars: [Unicode.Scalar]
  let map: [Int]?
  /// The place in the text of a place in the reading.
  func at(_ i: Int) -> Int { map?[i] ?? i }
}

private func vaultReading(_ t: [Unicode.Scalar]) -> Reading {
  guard t.contains(where: readDifferently) else { return Reading(scalars: t, map: nil) }
  var out: [Unicode.Scalar] = []
  var map: [Int] = []
  out.reserveCapacity(t.count)
  map.reserveCapacity(t.count + 1)
  var k = 0
  while k < t.count {
    let cp = t[k].value
    let next1: UInt32 = k + 1 < t.count ? t[k + 1].value : 0
    let next2: UInt32 = k + 2 < t.count ? t[k + 2].value : 0
    var r = cp
    var used = 1
    if cp >= 0xFF01 && cp <= 0xFF5E {
      r = cp - 0xFEE0
    } else if cp == 0x3000 {
      r = 0x20
    } else if cp >= 0x1100 && cp <= 0x1112 && next1 >= 0x1161 && next1 <= 0x1175 {
      // a leading consonant and a vowel, and a final consonant after them
      let lv = 0xAC00 + ((cp - 0x1100) * 21 + (next1 - 0x1161)) * 28
      if next2 >= 0x11A8 && next2 <= 0x11C2 { r = lv + next2 - 0x11A7; used = 3 } else { r = lv; used = 2 }
    } else if syllable(t[k]) && (cp - 0xAC00) % 28 == 0 && next1 >= 0x11A8 && next1 <= 0x11C2 {
      // a syllable without a final consonant, and a final consonant after it
      r = cp + next1 - 0x11A7
      used = 2
    }
    out.append(Unicode.Scalar(r) ?? t[k])
    map.append(k)
    k += used
  }
  map.append(t.count)
  return Reading(scalars: out, map: map)
}

private func asciiLower(_ s: [Unicode.Scalar]) -> [Unicode.Scalar] {
  s.map { isUpper($0) ? Unicode.Scalar($0.value + 0x20)! : $0 }
}

private func asciiUpper(_ s: [Unicode.Scalar]) -> [Unicode.Scalar] {
  s.map { $0.value >= 0x61 && $0.value <= 0x7A ? Unicode.Scalar($0.value - 0x20)! : $0 }
}

/// A value of two or more words: runs of word characters with white space between them.
private func words(_ v: [Unicode.Scalar]) -> Bool {
  v.split(whereSeparator: isSpace).filter { $0.contains(where: isWord) }.count >= 2
}

/// How many scalars of `v` (from its start) are matched in any ASCII case: all of an e-mail address, a user name and a
/// value of two or more words; a URL's scheme and host; nothing of other values.
private func caselessPrefix(_ label: String, _ v: [Unicode.Scalar]) -> Int {
  if label == "EMAIL" || label == "USERNAME" || words(v) { return v.count }
  guard label == "URL" else { return 0 }
  func alpha(_ c: Unicode.Scalar) -> Bool { (c.value | 0x20) >= 0x61 && (c.value | 0x20) <= 0x7A }
  var host = 0
  if let i = v.firstIndex(where: { !(alpha($0) || isDigit($0) || $0 == "+" || $0 == "." || $0 == "-") }), i > 0, alpha(v[0]),
     v[i...].starts(with: Array("://".unicodeScalars)) {
    host = i + 3
  }
  return v[host...].firstIndex(where: { $0 == "/" || $0 == "?" || $0 == "#" }) ?? v.count
}

/// A known value as the vault searches for it.
private struct Known {
  /// Index of its first entry.
  let entry: Int
  /// The value as the vault reads it (see `vaultReading`).
  let text: [Unicode.Scalar]
  /// The scalars of `text`, from its start, that match in any ASCII case.
  let caseless: Int
  /// `text` in ASCII lower case, when `caseless` is not 0.
  let lower: [Unicode.Scalar]
  /// `text` in capitals, in each spelling that is another one and that the caseless match does not cover (see
  /// `capitals`).
  let uppers: [[Unicode.Scalar]]
  let findable: Bool

  init(entry: Int, label: String, value: String) {
    let r = vaultReading(Array(value.unicodeScalars)).scalars
    let n = caselessPrefix(label, r)
    self.entry = entry
    self.text = r
    self.caseless = n
    self.lower = n > 0 ? asciiLower(r) : []
    self.uppers = capitals(r, n)
    self.findable = longEnough(r)
  }

  /// Whether `s` (a reading) is this value in a spelling the vault matches.
  func matches(_ s: [Unicode.Scalar]) -> Bool {
    if s == text || uppers.contains(s) { return true }
    let n = caseless
    guard n > 0, s.count == text.count else { return false }
    return asciiLower(Array(s[..<n])).elementsEqual(lower[..<n]) && s[n...].elementsEqual(text[n...])
  }
}

/// A value of at most this many scalars is compared from its start at each place where it may start (at most 32 steps a
/// place); a longer one is searched for in one pass (`occurrences`). The same in every package.
private let shortNeedle = 32

/// Every place where `p` starts in `s`, overlapping ones too ("Ann Ann" in "xAnn Ann Ann"), in time linear in both. A
/// long value is searched for with Knuth-Morris-Pratt, so that one that repeats itself (". . . . Kim" in a text of
/// ". . . . . . Kim", "ha ha ha" again and again) is not compared again from its start at every place.
private func occurrences(_ s: [Unicode.Scalar], _ p: [Unicode.Scalar]) -> [Int] {
  guard let first = p.first, p.count <= s.count else { return [] }
  var out: [Int] = []
  let m = p.count
  if m <= shortNeedle {
    var i = 0
    while i + m <= s.count {
      if s[i] == first, s[i..<(i + m)].elementsEqual(p) { out.append(i) }
      i += 1
    }
    return out
  }
  // fail[j]: the length of the longest proper prefix of p[0...j] that is also a suffix of it
  var fail = [Int](repeating: 0, count: m)
  var k = 0
  for j in 1..<m {
    while k > 0 && p[j] != p[k] { k = fail[k - 1] }
    if p[j] == p[k] { k += 1 }
    fail[j] = k
  }
  k = 0
  for i in 0..<s.count {
    let c = s[i]
    while k > 0 && c != p[k] { k = fail[k - 1] }
    if c == p[k] { k += 1 }
    if k == m {
      out.append(i + 1 - m)
      k = fail[m - 1]
    }
  }
  return out
}

/// The places of `at` (sorted) where `p` also starts `offset` scalars later in `s`: one pass over both lists when `p` is
/// long, so that checking the part of a value that must be written as it is costs no more than finding it.
private func followedBy(_ at: [Int], _ s: [Unicode.Scalar], _ offset: Int, _ p: ArraySlice<Unicode.Scalar>) -> [Int] {
  if p.count <= shortNeedle {
    return at.filter { s[($0 + offset)..<($0 + offset + p.count)].elementsEqual(p) }
  }
  guard !at.isEmpty else { return [] }
  let starts = occurrences(s, Array(p))
  var out: [Int] = []
  var j = 0
  for a in at {
    while j < starts.count && starts[j] < a + offset { j += 1 }
    if j < starts.count && starts[j] == a + offset { out.append(a) }
  }
  return out
}

/// A number as JSON writes one, with a sign in front or without ("4524", "-4.524e3", "+8210"; not "04524"): what an
/// exponent can be.
private func jsonNumber(_ v: ArraySlice<Unicode.Scalar>) -> Bool {
  var i = v.startIndex
  let digit = { (c: Unicode.Scalar) in c.value >= 0x30 && c.value <= 0x39 }
  func digits() -> Bool {
    let from = i
    while i < v.endIndex && digit(v[i]) { i += 1 }
    return i > from
  }
  if i < v.endIndex && (v[i] == "+" || v[i] == "-") { i += 1 }
  guard i < v.endIndex else { return false }
  if v[i] == "0" { i += 1 } else if digit(v[i]) { _ = digits() } else { return false }
  if i < v.endIndex && v[i] == "." {
    i += 1
    if !digits() { return false }
  }
  if i < v.endIndex && (v[i] == "e" || v[i] == "E") {
    i += 1
    if i < v.endIndex && (v[i] == "+" || v[i] == "-") { i += 1 }
    if !digits() { return false }
  }
  return i == v.endIndex
}

/// Word edges for the part `start..<end` of `t`: a value is not found inside a longer word. A letter of a script written
/// without spaces on either side of the boundary is an edge, but not a mark of such a script after a value that ends in
/// one (a Thai vowel sign: "สม" is not found in "สมัคร"); where Hangul meets other letters or digits is an edge,
/// Korean particles may follow a Hangul value, and Hangul right before a Hangul value is not an edge. After a value of two
/// Hangul syllables, more syllables are an edge only as particles, 님, 씨 or the copula (`koTailPieces`): "지원님께" and
/// "지원이" are the name, "지원금" is another word.
private func atEdges(_ t: [Unicode.Scalar], _ start: Int, _ end: Int) -> Bool {
  let first = t[start], last = t[end - 1]
  let okBefore: Bool
  if start == 0 { okBefore = true } else {
    let b = t[start - 1]
    okBefore = !isWord(b) || !isWord(first) || noSpace(first) || noSpace(b) || hangul(b) != hangul(first)
  }
  let okAfter: Bool
  if end >= t.count { okAfter = true } else if syllable(t[end]), end - start == 2, syllable(first), syllable(last) {
    okAfter = koTail(t, end)
  } else {
    let a = t[end]
    // (a mark of a script written without spaces belongs to the letter before it: the word goes on)
    let markAfter = noSpace(last) && noSpaceMark(a)
    okAfter = !isWord(a) || !isWord(last) || (!markAfter && (noSpace(last) || noSpace(a))) || hangul(a) || hangul(last)
  }
  // a dot between two digits joins them ("4524" is not found in "4524.7" or in "1.4524"), and so does the sign of an
  // exponent before a number ("1e+4524"); a digit is any number character, as in a word. An exponent is a number: a
  // value that is not one (an address, a phone number with spaces, "04524") is not joined to "<digit>e+" before it
  let before: Unicode.Scalar? = start >= 1 ? t[start - 1] : nil
  let second: Unicode.Scalar? = start >= 2 ? t[start - 2] : nil
  let third: Unicode.Scalar? = start >= 3 ? t[start - 3] : nil
  // (whether the value is a number is asked last: reading it costs its length, at every place where it is found)
  let number = { jsonNumber(t[start..<end]) }
  let exponent = (before == "+" || before == "-") && (second == "e" || second == "E") && isNumber(third) && number()
  // (the value may start with the sign itself: "-4524" in "1e-4524")
  let signed = (first == "+" || first == "-") && end - start > 1 && isNumber(t[start + 1]) && (before == "e" || before == "E") && isNumber(second) && number()
  let joinedBefore = (isNumber(first) && ((before == "." && isNumber(second)) || exponent)) || signed
  let joinedAfter = isNumber(last) && end + 1 < t.count && t[end] == "." && isNumber(t[end + 1])
  return okBefore && okAfter && !joinedBefore && !joinedAfter
}

private func family(_ label: String) -> String {
  var up = label.uppercased()
  if up.hasSuffix("_L2") { up.removeLast(3) }
  var cleaned = String(String.UnicodeScalarView(up.unicodeScalars.map { isUpper($0) || isDigit($0) || $0 == "_" ? $0 : "_" }))
  cleaned = String(cleaned.unicodeScalars.prefix(maxFamily).map(Character.init))
  while let f = cleaned.unicodeScalars.first, !isUpper(f) { cleaned.removeFirst() }
  return cleaned.isEmpty ? "PII" : cleaned
}

private func jsonEscape(_ v: String) -> String {
  var out = ""
  for c in v.unicodeScalars {
    switch c {
    case "\"": out += "\\\""
    case "\\": out += "\\\\"
    case "\n": out += "\\n"
    case "\r": out += "\\r"
    case "\t": out += "\\t"
    default:
      if c.value < 0x20 { out += String(format: "\\u%04x", c.value) } else { out.unicodeScalars.append(c) }
    }
  }
  return out
}

/// Numbered placeholders that can be undone. Thread-safe. Pass `entries` (from `entries`) to continue a saved vault; they
/// hold the original values, so store them only where the original text may be stored.
public final class PIIVault: @unchecked Sendable {
  private let lock = NSLock()
  private var list: [PIIVaultEntry] = []
  private var byTag: [String: Int] = [:]
  private var byValue: [[UInt8]: Int] = [:]
  // each value once, in the order it was added (its place is its age), and by the spellings `whole` takes
  private var known: [Known] = []
  private var byRead: [[Unicode.Scalar]: Int] = [:]
  private var byUpper: [[Unicode.Scalar]: Int] = [:]
  private var byLower: [[Unicode.Scalar]: [Int]] = [:]
  private var counters: [String: Int] = [:]
  // the values a model found that are shorter than the vault searches for (`tagFoundLocked`): held to be restored, not
  // found as a whole value elsewhere
  private var local: Set<[UInt8]> = []
  private var replays: [[UInt8]: String] = [:]
  private var replayOrder: [[UInt8]] = []
  private var gen = 0

  public init() {}

  public init(entries: [PIIVaultEntry]) throws {
    for e in entries { try add(PIIVaultEntry(tag: e.tag, label: family(e.label), value: e.value)) }
  }

  /// `e` held; with `local`, a new value is held to be restored only, not searched for (`tagFoundLocked`).
  private func add(_ e: PIIVaultEntry, local isLocal: Bool = false) throws {
    let s = Array(e.tag.unicodeScalars)
    guard let t = parseTag(s, at: 0), t.length == s.count, t.family.count <= maxFamily, t.number.count <= 12, !e.value.isEmpty else {
      throw PIIVaultError.notAnEntry(e.tag)
    }
    if byTag[e.tag] != nil { throw PIIVaultError.duplicateTag(e.tag) }
    counters[t.family] = max(counters[t.family] ?? 0, Int(t.number) ?? 0)
    byTag[e.tag] = list.count
    if byValue[bytes(e.value)] == nil {
      byValue[bytes(e.value)] = list.count
      if isLocal { local.insert(bytes(e.value)) } else { index(list.count, e.label, e.value) }
    }
    list.append(e)
  }

  /// The value of entry `i` searched for: its spellings indexed.
  private func index(_ i: Int, _ label: String, _ value: String) {
    let k = Known(entry: i, label: label, value: value)
    let age = known.count
    if byRead[k.text] == nil { byRead[k.text] = age }
    for u in k.uppers where byUpper[u] == nil { byUpper[u] = age }
    if k.caseless > 0 { byLower[k.lower, default: []].append(age) }
    known.append(k)
  }

  /// The known value that `value` is as a whole, in any spelling `find` matches (the one held longest), also with what
  /// joins its last character after it (a mark, a variation selector), as `find` takes that in.
  private func wholeLocked(_ value: String) -> PIIVaultEntry? {
    guard !known.isEmpty, !value.isEmpty else { return nil }
    let r = vaultReading(Array(value.unicodeScalars)).scalars
    if let e = wholeRead(r) { return e }
    var cut = r.count
    while cut > 0, joins(r[cut - 1]) { cut -= 1 }
    guard cut > 0, cut < r.count, atEdges(r, 0, cut) else { return nil }
    return wholeRead(Array(r[..<cut]))
  }

  /// `wholeLocked` for a reading, as it is.
  private func wholeRead(_ r: [Unicode.Scalar]) -> PIIVaultEntry? {
    var best = [byRead[r], byUpper[r]].compactMap { $0 }.min()
    if let ks = byLower[asciiLower(r)], let k = ks.first(where: { known[$0].matches(r) }) { best = min(best ?? k, k) }
    return best.map { list[known[$0].entry] }
  }

  /// The placeholder for a value found in a text: that of the known value it is in another spelling, or its own. A new
  /// value shorter than the vault searches for at word edges (3 characters, 2 in scripts written without spaces and two
  /// Hangul syllables) is held to be restored, but not found as a whole value elsewhere (`hideValue`): a "0" that a
  /// model took for a date once must not turn every "0" into a placeholder. Values the application tags (`tag`) are
  /// found as a whole value whatever their length.
  private func tagFoundLocked(_ label: String, _ value: String) -> String {
    if let e = wholeLocked(value) { return e.tag }
    if let i = byValue[bytes(value)] { return list[i].tag }  // (held already: one value, one placeholder)
    if value.isEmpty || longEnough(vaultReading(Array(value.unicodeScalars)).scalars) { return tagLocked(label, value) }
    return newLocked(label, value, local: true)
  }

  private func locked<T>(_ f: () throws -> T) rethrows -> T {
    lock.lock()
    defer { lock.unlock() }
    return try f()
  }

  /// Number of values in the vault.
  public var count: Int { locked { list.count } }
  /// Goes up by one on every `clear()`.
  public var generation: Int { locked { gen } }

  /// The placeholder for `value`, adding it under `label` (its family) when new. The same value keeps its first tag.
  /// An empty value has no placeholder (""). Numbers go up to 999,999,999,999; a family that has used them up (after an
  /// imported entry with that number) goes on as "[PERSON__1]".
  @discardableResult
  public func tag(_ label: String, _ value: String) -> String { locked { tagLocked(label, value) } }

  private func tagLocked(_ label: String, _ value: String) -> String {
    if value.isEmpty { return "" }
    if let i = byValue[bytes(value)] {
      // (a short value a model found is found as a whole value from now on: the application gives it)
      if local.remove(bytes(value)) != nil { index(i, list[i].label, list[i].value) }
      return list[i].tag
    }
    return newLocked(label, value, local: false)
  }

  /// A new entry for `value` under `label` (its family); `local` as in `add`.
  private func newLocked(_ label: String, _ value: String, local isLocal: Bool) -> String {
    let fam = family(label)
    // a family whose numbers are used up goes on in the next one, so a value always gets a placeholder that can be undone
    var f = fam
    var left = familySteps
    while left > 0, (counters[f] ?? 0) >= maxNumber {
      f = nextFamily(f)
      left -= 1
    }
    // every family on the way used up (some 10^13 values, or a state made by hand): masked, not undone ("PERSON_1"
    // gives "[PERSON]", never a numbered placeholder)
    guard (counters[f] ?? 0) < maxNumber else { return "[\(unnumbered(fam))]" }
    let e = PIIVaultEntry(tag: "[\(f)_\((counters[f] ?? 0) + 1)]", label: fam, value: value)
    guard (try? add(e, local: isLocal)) != nil else { return "[\(unnumbered(fam))]" }
    return e.tag
  }

  /// The value behind a placeholder.
  public func value(_ tag: String) -> String? { locked { byTag[tag].map { list[$0].value } } }

  /// The vault's placeholders in `text` replaced with their values (limited by `scope`); other bracketed text is left as
  /// it is. With `json`, the values are escaped for the inside of a JSON string.
  public func restore(_ text: String, scope: PIIVaultScope = .all, json: Bool = false) -> String {
    let s = Array(text.unicodeScalars)
    let positions = tagPositions(s)
    if positions.isEmpty { return text }
    return locked {
      var out = String.UnicodeScalarView()
      var last = 0
      for (at, len) in positions {
        let tag = String(String.UnicodeScalarView(s[at..<(at + len)]))
        let v: String?
        switch scope {
        case .all: v = byTag[tag].map { list[$0].value }
        case .tags(let set): v = set.contains(tag) ? byTag[tag].map { list[$0].value } : nil
        case .values(let map): v = map[tag]
        }
        out.append(contentsOf: s[last..<at])
        out.append(contentsOf: (v.map { json ? jsonEscape($0) : $0 } ?? tag).unicodeScalars)
        last = at + len
      }
      out.append(contentsOf: s[last...])
      return String(out)
    }
  }

  /// `restore` applied to every string inside a JSON-like value (dictionaries and arrays are copied, keys are kept).
  public func restoreValue(_ value: Any, scope: PIIVaultScope = .all) -> Any {
    // one snapshot of the values for the whole traversal: a clear() from another thread cannot fall between two strings
    let fixed = fixed(scope)
    return mapStrings(value) { self.restore($0, scope: fixed) }
  }

  /// `scope` with the values the vault holds now, copied in one step.
  private func fixed(_ scope: PIIVaultScope) -> PIIVaultScope {
    switch scope {
    case .values: return scope
    case .all: return .values(locked { Dictionary(list.map { ($0.tag, $0.value) }, uniquingKeysWith: { a, _ in a }) })
    case .tags(let set):
      return .values(locked { Dictionary(list.filter { set.contains($0.tag) }.map { ($0.tag, $0.value) }, uniquingKeysWith: { a, _ in a }) })
    }
  }

  /// A restorer for a streamed text. It uses the values the vault holds when it is created, for the whole stream.
  public func restorer(scope: PIIVaultScope = .all, json: Bool = false) -> PIIVaultRestorer {
    PIIVaultRestorer(vault: self, scope: fixed(scope), json: json)
  }

  /// The current values of these placeholders, as a snapshot for `.values`.
  public func bindings<S: Sequence>(_ tags: S) -> [String: String] where S.Element == String {
    locked {
      var out: [String: String] = [:]
      for t in tags { if let i = byTag[t] { out[t] = list[i].value } }
      return out
    }
  }

  /// Where the known values occur in `text` (sorted, non-overlapping; overlapping occurrences are merged into one hit
  /// whose value is the merged text and whose label is the first one's). A hit carries the known value, also where it is
  /// written another way (see the notes at the top); where two values are found on the same part of the text, the one
  /// the vault has held longer.
  public func find(_ text: String) -> [PIIVaultHit] { locked { findLocked(Array(text.unicodeScalars)) } }

  private func findLocked(_ t: [Unicode.Scalar]) -> [PIIVaultHit] {
    guard !known.isEmpty else { return [] }
    let reading = vaultReading(t)
    let r = reading.scalars
    var lower: [Unicode.Scalar]? = nil
    // (start, end) in the reading and the value's age
    var found: [(Int, Int, Int)] = []
    for (age, k) in known.enumerated() where k.findable {
      // (a hit takes in what joins its last character, so that it never ends inside a grapheme cluster)
      func take(_ at: Int, _ end: Int) {
        if atEdges(r, at, end) { found.append((at, widen(r, end), age)) }
      }
      if k.caseless > 0 {
        // the caseless part in any ASCII case, the rest as it is written
        if lower == nil { lower = asciiLower(r) }
        for at in followedBy(occurrences(lower!, k.lower), r, k.caseless, k.text[k.caseless...]) {
          take(at, at + k.lower.count)
        }
      } else {
        for at in occurrences(r, k.text) { take(at, at + k.text.count) }
      }
      for u in k.uppers {
        for at in occurrences(r, u) { take(at, at + u.count) }
      }
    }
    found.sort { $0.0 != $1.0 ? $0.0 < $1.0 : $0.1 != $1.1 ? $0.1 > $1.1 : $0.2 < $1.2 }
    // overlapping known values are merged ("Alex Kim" + "Kim Lee"); the value of a merged hit is the text it covers
    var merged: [(start: Int, end: Int, age: Int, merged: Bool)] = []
    for h in found {
      if let l = merged.last, h.0 < l.end {
        if h.1 > l.end {
          merged[merged.count - 1].end = h.1
          merged[merged.count - 1].merged = true
        }
      } else {
        merged.append((h.0, h.1, h.2, false))
      }
    }
    return merged.map { m in
      let start = reading.at(m.start), end = reading.at(m.end)
      let e = list[known[m.age].entry]
      return PIIVaultHit(scalarRange: start..<end, label: e.label, value: m.merged ? String(String.UnicodeScalarView(t[start..<end])) : e.value)
    }
  }

  /// The vault's known values in `text` replaced with their placeholders (no model call). Overlapping known values are
  /// hidden together under a new placeholder. With `escape`, placeholder-like text in the rest is escaped.
  public func hide(_ text: String, escape: Bool = false) -> String {
    locked { hideLocked(text, escape: escape) }
  }

  private func hideLocked(_ text: String, escape: Bool) -> String {
    let t = Array(text.unicodeScalars)
    let esc: (String) -> String = escape ? escapeTags : { $0 }
    var out = ""
    var last = 0
    for h in findLocked(t) {
      out += esc(String(String.UnicodeScalarView(t[last..<h.scalarRange.lowerBound])))
      out += byValue[bytes(h.value)].map { list[$0].tag } ?? tagLocked(h.label, h.value)
      last = h.scalarRange.upperBound
    }
    return out + esc(String(String.UnicodeScalarView(t[last...])))
  }

  /// `hide` applied to every string inside a JSON-like value; a string that is as a whole a known value (in any spelling
  /// `find` matches) is replaced whatever its length.
  public func hideValue(_ value: Any) -> Any {
    // one step for the vault: every string is numbered in the same generation
    locked { mapStrings(value) { s in wholeLocked(s)?.tag ?? hideLocked(s, escape: false) } }
  }

  /// Remembers what the model wrote (`masked`) for a text that was restored (at most 4,096).
  public func remember(_ key: String, masked: String) {
    locked {
      let k = bytes(key)
      if replays.updateValue(masked, forKey: k) != nil { replayOrder.removeAll { $0 == k } }
      replayOrder.append(k)
      while replayOrder.count > 4096 { replays.removeValue(forKey: replayOrder.removeFirst()) }
    }
  }

  /// What `remember` stored for `key`.
  public func recall(_ key: String) -> String? { locked { replays[bytes(key)] } }

  /// The entries in the order they were added.
  public var entries: [PIIVaultEntry] { locked { list } }

  public func clear() {
    locked {
      list = []; byTag = [:]; byValue = [:]; counters = [:]; local = []; replays = [:]; replayOrder = []
      known = []; byRead = [:]; byUpper = [:]; byLower = [:]
      gen += 1
    }
  }

  /// `spans` (detected in `text`) plus the vault's known values (source `.vault`). A known value that spans cover only in
  /// part is masked whole: the overlapping spans are merged into one span over both. One pass over the hits and the
  /// spans (sorted by their start first if they are not). A span over known values has their label, and the label of a
  /// span the model found when it was merged with one; a known value of a custom label ("CUSTOMER") is labelled
  /// `.person`, which `redact` does not take for its family.
  public func withVaultSpans(_ text: String, spans: [PIISpan]) -> [PIISpan] { locked { withVaultSpansLocked(text, spans) } }

  private func withVaultSpansLocked(_ text: String, _ spans: [PIISpan]) -> [PIISpan] {
    let hits = findLocked(Array(text.unicodeScalars))  // in order, apart
    if hits.isEmpty { return spans }
    var spans = spans
    if !zip(spans, spans.dropFirst()).allSatisfy({ $0.scalarRange.lowerBound <= $1.scalarRange.lowerBound }) {
      spans.sort { $0.scalarRange.lowerBound < $1.scalarRange.lowerBound }
    }
    var out: [PIISpan] = []
    out.reserveCapacity(spans.count + hits.count)
    var next = 0
    var cursor = ScalarCursor(text)
    for h in hits {
      let hs = h.scalarRange.lowerBound, he = h.scalarRange.upperBound
      while next < spans.count, spans[next].scalarRange.upperBound <= hs {
        out.append(spans[next])
        next += 1
      }
      var over: [PIISpan] = []
      if let l = out.last, l.scalarRange.upperBound > hs { over.append(out.removeLast()) }  // one that reaches this far (merged before)
      while next < spans.count, spans[next].scalarRange.lowerBound < he {
        over.append(spans[next])
        next += 1
      }
      if !over.isEmpty, over.allSatisfy({ $0.scalarRange.lowerBound <= hs && $0.scalarRange.upperBound >= he }) {
        out.append(contentsOf: over)  // already masked whole
        continue
      }
      let (hLo, hU16Lo) = cursor.at(hs)
      let (hHi, hU16Hi) = cursor.at(he)
      // the merged span starts and ends where the hit or a span does (a span carries its own offsets)
      let first = over.filter { $0.scalarRange.lowerBound < hs }.min { $0.scalarRange.lowerBound < $1.scalarRange.lowerBound }
      let last = over.filter { $0.scalarRange.upperBound > he }.max { $0.scalarRange.upperBound < $1.scalarRange.upperBound }
      let start = first?.scalarRange.lowerBound ?? hs, end = last?.scalarRange.upperBound ?? he
      let lo = first?.range.lowerBound ?? hLo, hi = last?.range.upperBound ?? hHi
      let u16lo = first?.utf16Range.lowerBound ?? hU16Lo, u16hi = last?.utf16Range.upperBound ?? hU16Hi
      let label = (start == hs && end == he) || over.isEmpty ? (PIILabel(rawValue: h.label) ?? .person) : over[0].label
      out.append(PIISpan(range: lo..<hi, utf16Range: u16lo..<u16hi, scalarRange: start..<end, label: label, source: .vault))
    }
    out.append(contentsOf: spans[next...])
    return out
  }

  /// `spans` replaced by placeholders. A span of source `.vault` that is exactly where known values were found (as
  /// `withVaultSpans` makes it) is numbered in the family of the first of them, also when that is a custom label that
  /// `PIILabel` cannot name (such a span is labelled `.person`).
  private func applyLocked(_ text: String, _ spans: [PIISpan]) -> String {
    let scalars = text.unicodeScalars
    // (where the vault finds known values: looked up only when a span from them is not as a whole a known value, which
    // is when known values that overlap were merged)
    let merged = spans.contains { $0.source == .vault && wholeLocked(String(scalars[$0.range])) == nil }
    let hits = merged ? findLocked(Array(scalars)) : []
    let hitAt = Dictionary(hits.map { ($0.scalarRange.lowerBound, $0) }, uniquingKeysWith: { a, _ in a })
    var out = ""
    var last = scalars.startIndex
    for s in spans {
      out += escapeTags(String(scalars[last..<s.range.lowerBound]))
      let hit = s.source == .vault ? hitAt[s.scalarRange.lowerBound].flatMap { $0.scalarRange == s.scalarRange ? $0 : nil } : nil
      // (a known value written another way keeps its placeholder)
      out += tagFoundLocked(hit?.label ?? s.label.rawValue, String(scalars[s.range]))
      last = s.range.upperBound
    }
    return out + escapeTags(String(scalars[last...]))
  }

  /// `spans` replaced by the vault's placeholders, as one step for the vault (a `clear()` from another thread cannot fall
  /// between two placeholders). The spans first lose what their edges hold that is not part of the value ("Kim Minsu."
  /// at the end of a sentence is the value "Kim Minsu"). With `known`, the vault's known values are added, and every
  /// value this text adds to the vault is then masked wherever it occurs again in it ("I met Alice. Say Alice twice."
  /// when the model finds only the first "Alice"). With `generation`, nothing is written and `PIIVaultError.cleared` is
  /// thrown if the vault was cleared since.
  public func redact(_ text: String, spans found: [PIISpan], known: Bool = true, generation: Int? = nil) throws -> (text: String, spans: [PIISpan]) {
    try locked {
      if let generation, generation != gen { throw PIIVaultError.cleared }
      let spans = valueSpans(text, found)
      guard known else { return (applyLocked(text, spans), spans) }
      var all = withVaultSpansLocked(text, spans)
      var out = applyLocked(text, all)  // the vault learns this text's values here
      // also what the model found inside a larger merged span ("Kim Lee" of "Alex Kim Lee"), so it is masked where it stands alone
      for s in spans {
        let value = String(text.unicodeScalars[s.range])
        if !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { _ = tagFoundLocked(s.label.rawValue, value) }
      }
      // until nothing new is found: every round either masks more of the text or merges spans, so this ends
      while true {
        let next = withVaultSpansLocked(text, all)
        if next.map(\.utf16Range) == all.map(\.utf16Range) { return (out, all) }
        all = next
        out = applyLocked(text, all)
      }
    }
  }
}

/// String indices and UTF-16 offsets of scalar offsets asked for in order, each counted on from the one before.
private struct ScalarCursor {
  let text: String
  var index: String.Index
  var scalar = 0
  var utf16 = 0

  init(_ text: String) {
    self.text = text
    index = text.unicodeScalars.startIndex
  }

  mutating func at(_ offset: Int) -> (String.Index, Int) {
    if offset < scalar {
      // (not in order: counted from the start)
      (index, scalar, utf16) = (text.unicodeScalars.startIndex, 0, 0)
    }
    let scalars = text.unicodeScalars
    while scalar < offset {
      utf16 += UTF16.width(scalars[index])
      index = scalars.index(after: index)
      scalar += 1
    }
    return (index, utf16)
  }
}

private func mapStrings(_ value: Any, _ f: (String) -> String) -> Any {
  switch value {
  case let s as String: return f(s)
  case let a as [Any]: return a.map { mapStrings($0, f) }
  case let d as [String: Any]: return d.mapValues { mapStrings($0, f) }
  default: return value
  }
}

/// Undoes masking in text that arrives in pieces (a streamed answer). A placeholder split across pieces is held back until
/// it is complete, so `push` may return less than it was given; `flush` returns what is left.
public final class PIIVaultRestorer {
  private let vault: PIIVault
  private let scope: PIIVaultScope
  private let json: Bool
  private var held: [Unicode.Scalar] = []

  init(vault: PIIVault, scope: PIIVaultScope, json: Bool) {
    self.vault = vault
    self.scope = scope
    self.json = json
  }

  public func push(_ chunk: String) -> String {
    held.append(contentsOf: chunk.unicodeScalars)
    var cut = held.count
    let from = max(0, held.count - maxTag)
    if let at = held[from...].lastIndex(of: "["), held[(at + 1)...].allSatisfy({ isUpper($0) || isDigit($0) || $0 == "_" }) { cut = at }
    let out = vault.restore(String(String.UnicodeScalarView(held[..<cut])), scope: scope, json: json)
    held.removeFirst(cut)
    return out
  }

  public func flush() -> String {
    let out = vault.restore(String(String.UnicodeScalarView(held)), scope: scope, json: json)
    held = []
    return out
  }
}

extension PIIRedactor {
  /// Replaces each span by `vault`'s numbered placeholder for its value; placeholder-like text left unmasked is escaped
  /// ("［PERSON_1]"), so that only the vault's own placeholders restore.
  public static func applyRedaction(to text: String, spans: [PIISpan], vault: PIIVault) -> String {
    // without a generation this cannot throw
    (try? vault.redact(text, spans: spans, known: false, generation: nil).text) ?? text
  }

  /// The text with numbered placeholders that `vault` can undo, and the spans. The vault's known values are also masked
  /// where they occur again and the model does not find them (source `.vault`). Throws `PIIVaultError.cleared` if the
  /// vault is cleared while the model reads the text (nothing is written). Since 1.7.2. Blocks the calling thread.
  public func redact(_ text: String, vault: PIIVault) throws -> (text: String, spans: [PIISpan]) {
    let started = vault.generation
    return try vault.redact(text, spans: try detect(text), known: true, generation: started)
  }

  /// `redact(_:vault:)`, run off the caller's actor.
  public func redact(_ text: String, vault: PIIVault) async throws -> (text: String, spans: [PIISpan]) {
    let started = vault.generation
    return try vault.redact(text, spans: try await detect(text), known: true, generation: started)
  }
}
