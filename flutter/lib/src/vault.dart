// A vault: the local map from numbered placeholders back to the values they replace, so that masking can be undone in a
// model's answer (since 1.7.2). It is an ordinary in-memory object; nothing in it is sent anywhere. The placeholders and
// the search for known values are those of the npm package's createVault() and the Python package's Vault (shared cases:
// python/tests/vault_cases.json); the other spellings the npm and Python middlewares also read (escapes, URL forms,
// numbers written another way, other white space) are not read here.
//
//   final vault = PiiVault();   // one per user or conversation, never shared between users
//   (await pii.redactVault('Call Kim Minsu on 010-1234-5678', vault)).text;  // 'Call [PERSON_1] on [PHONE_1]'
//   vault.restore('I called [PERSON_1].');                                  // 'I called Kim Minsu.'
//
// The same value always gets the same placeholder within a vault, and a value the vault holds is masked again wherever it
// reappears in later text, also where the model would miss it there (spans with source "vault"): at word edges, 3
// characters or more (2 in scripts written without spaces, and two Hangul syllables, after which only particles, 님, 씨
// or the copula may follow in the same word). It is found as it is written and also: with full-width ASCII (U+FF01 to
// U+FF5E) and U+3000 read as ASCII; with Hangul written as conjoining jamo (NFD) read as syllables; in capitals (the full
// Unicode uppercase, "Weiß" as "WEISS", from lib/src/upper.dart; a Greek value also without its accents, as Greek writes
// capitals; a sharp s also as the capital sharp s); and, for an e-mail address, a user name, a URL's scheme and host and
// a value of two or more words, in any ASCII case. A one-word value in another case is another word ("Will" and "will").
// The hit carries the known value, so one value keeps one placeholder however it is written, and it takes in the marks,
// variation selectors and joiners right after it, so that it never ends inside a grapheme cluster. Offsets are UTF-16
// units, as in PiiSpan.
import 'dart:convert';

import 'redaction.dart';
import 'span.dart';
import 'upper.dart';

/// One placeholder and the value behind it.
final class PiiVaultEntry {
  final String tag, label, value;
  const PiiVaultEntry(this.tag, this.label, this.value);

  Map<String, String> toJson() => {'tag': tag, 'label': label, 'value': value};
  factory PiiVaultEntry.fromJson(Map<String, dynamic> j) =>
      PiiVaultEntry(j['tag'] as String, j['label'] as String, j['value'] as String);

  @override
  bool operator ==(Object other) =>
      other is PiiVaultEntry && other.tag == tag && other.label == label && other.value == value;
  @override
  int get hashCode => Object.hash(tag, label, value);
  @override
  String toString() => 'PiiVaultEntry($tag, $label, $value)';
}

/// Where a known value occurs: [start, end) in UTF-16 units.
final class PiiVaultHit {
  final int start, end;
  final String label, value;
  const PiiVaultHit(this.start, this.end, this.label, this.value);
}

const _maxFamily = 32;
const _maxTag = _maxFamily + 15;
const _maxNumber = 999999999999;

/// The family that goes on when the numbers of [family] are used up (which takes an imported placeholder with the
/// highest number): one more "_" at its end, "PERSON" → "PERSON_", so that its placeholders stay apart
/// ("[PERSON__1]"). A family of full length gets its last other character turned into "_" instead, and one that is a
/// letter and underscores only goes on with the next letter (after Z, with A).
String _nextFamily(String family) {
  if (family.length < _maxFamily) return '${family}_';
  for (var i = family.length - 1; i > 0; i--) {
    if (family[i] != '_') return '${family.substring(0, i)}_${family.substring(i + 1)}';
  }
  final first = family.codeUnitAt(0);
  return String.fromCharCode(first < 0x5A ? first + 1 : 0x41) + family.substring(1);
}

/// How many families are tried after the first: at most 31 steps to a letter and underscores, then the 25 other letters.
const _familySteps = 56;

// Placeholder grammar, the same in every package: "[" + family + "_" + number + "]", where the family is an ASCII capital
// letter followed by capitals, digits and underscores (32 characters at most) and the number is 1 to 12 ASCII digits.
// Longer look-alikes are ordinary text: they are neither escaped nor restored.
final _tag = RegExp(r'\[[A-Z][A-Z0-9_]{0,31}_[0-9]{1,12}\]');
final _numberTail = RegExp(r'(_[0-9]*)+$');
final _entryTag = RegExp(r'^\[([A-Z][A-Z0-9_]*)_([0-9]+)\]$');
final _tagPrefix = RegExp(r'\[[A-Z0-9_]*$');
final _word = RegExp(r'^[\p{L}\p{N}\p{M}_]$', unicode: true);

/// The vault placeholders in [text], in order of appearance (duplicates kept).
List<String> vaultTags(String text) => [for (final m in _tag.allMatches(text)) m[0]!];

/// [text] with every placeholder-like part that was already in it ("[PERSON_1]") turned into "［PERSON_1]".
String escapeTags(String text) =>
    text.contains('[') ? text.replaceAllMapped(_tag, (m) => '［${m[0]!.substring(1)}') : text;

// Scripts written without spaces between words (letters and marks of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese,
// Javanese, Han, Hiragana and Katakana), and Hangul: the same code point ranges as the npm and Python packages.
const _noSpace = [
  0x0E01, 0x0E3A, 0x0E40, 0x0E4E, // Thai
  0x0E81, 0x0ECF, // Lao
  0x1000,
  0x103F,
  0x1050,
  0x108F,
  0x109A,
  0x109D,
  0xA9E0,
  0xA9EF,
  0xA9FA,
  0xA9FE,
  0xAA60,
  0xAA7F, // Myanmar
  0x1780, 0x17D3, 0x17D7, 0x17D7, 0x17DC, 0x17DD, 0x19E0, 0x19FF, // Khmer
  0x1A20, 0x1A5E, 0x1A60, 0x1A7C, 0x1A7F, 0x1A7F, 0x1AA7, 0x1AA7, // Tai Tham
  0x1B00, 0x1B4C, // Balinese
  0xA980, 0xA9C0, 0xA9CF, 0xA9CF, // Javanese
  0x2E80,
  0x2FDF,
  0x3005,
  0x3005,
  0x3007,
  0x3007,
  0x3021,
  0x3029,
  0x3038,
  0x303B, // Han radicals, marks
  0x3041, 0x3096, 0x309D, 0x309F, // Hiragana
  0x30A1,
  0x30FA,
  0x30FD,
  0x30FF,
  0x31F0,
  0x31FF,
  0xFF66,
  0xFF6F,
  0xFF71,
  0xFF9D, // Katakana
  0x1B000, 0x1B16F, // kana supplement and extensions
  0x3400,
  0x4DBF,
  0x4E00,
  0x9FFF,
  0xF900,
  0xFAFF,
  0x20000,
  0x3347F, // Han, to Extension J
];
const _hangul = [0x1100, 0x11FF, 0x3131, 0x318E, 0xA960, 0xA97F, 0xAC00, 0xD7A3, 0xD7B0, 0xD7FF, 0xFFA0, 0xFFDC];

bool _inRanges(List<int> r, int cp) {
  for (var i = 0; i < r.length; i += 2) {
    if (cp >= r[i] && cp <= r[i + 1]) return true;
  }
  return false;
}

// The marks among them (vowel signs, tone marks and the like of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese and
// Javanese; Unicode general category M): a mark belongs to the letter before it, so the place before one is no word edge.
const _noSpaceMarks = [
  0x0E31, 0x0E31, 0x0E34, 0x0E3A, 0x0E47, 0x0E4E, // Thai
  0x0EB1, 0x0EB1, 0x0EB4, 0x0EBC, 0x0EC8, 0x0ECE, // Lao
  0x102B, 0x103E, 0x1056, 0x1059, 0x105E, 0x1060, 0x1062, 0x1064, 0x1067, 0x106D, 0x1071, 0x1074, //
  0x1082, 0x108D, 0x108F, 0x108F, 0x109A, 0x109D, 0xA9E5, 0xA9E5, 0xAA7B, 0xAA7D, // Myanmar
  0x17B4, 0x17D3, 0x17DD, 0x17DD, // Khmer
  0x1A55, 0x1A5E, 0x1A60, 0x1A7C, 0x1A7F, 0x1A7F, // Tai Tham
  0x1B00, 0x1B04, 0x1B34, 0x1B44, // Balinese
  0xA980, 0xA983, 0xA9B3, 0xA9C0, // Javanese
];

bool _noSpaceCp(int cp) => _inRanges(_noSpace, cp);
bool _noSpaceMarkCp(int cp) => _inRanges(_noSpaceMarks, cp);
bool _hangulCp(int cp) => _inRanges(_hangul, cp);
final _number = RegExp(r'^\p{N}$', unicode: true);
bool _isNumber(int cp) => cp >= 0 && _number.hasMatch(String.fromCharCode(cp));
bool _isWord(int cp) => _word.hasMatch(String.fromCharCode(cp));

/// A precomposed Hangul syllable (U+AC00 to U+D7A3).
bool _syllable(int cp) => cp >= 0xAC00 && cp <= 0xD7A3;

/// White space as Unicode has it (the same set in every package).
bool _isSpace(int cp) =>
    (cp >= 0x09 && cp <= 0x0D) ||
    cp == 0x20 ||
    cp == 0x85 ||
    cp == 0xA0 ||
    cp == 0x1680 ||
    (cp >= 0x2000 && cp <= 0x200A) ||
    cp == 0x2028 ||
    cp == 0x2029 ||
    cp == 0x202F ||
    cp == 0x205F ||
    cp == 0x3000;

/// What a value of two Hangul syllables may have right after it in the same word, one piece after another to the end of
/// the run of syllables: particles, the honorifics 님 and 씨, and the copula. Any other syllable makes a longer word
/// ("지원금", "정수기"), and a two-syllable name is often a word too. The same list in every package.
const _koTail = [
  '가', '같이', '고', '과', '까지', '께', '께서', '나', '나마', '네', '는', '니까', '님', '다', '대로', '더러', '도', '든', '든지', '라고', //
  '라는', '라도', '라서', '랑', '로', '로서', '로써', '를', '마다', '마저', '만', '만큼', '며', '밖에', '보다', '부터', '씨', '아', '야', //
  '어', '었', '에', '에게', '에게서', '에서', '여', '였', '예요', '와', '요', '으로', '으로서', '으로써', '은', '을', '의', '이', '이면', //
  '인', '인데', '입니다', '조차', '죠', '지', '지만', '처럼', '한테', '한테서',
];

/// The longest run of syllables [_koTailAt] reads; a longer one is another word.
const _koTailMax = 8;

/// Whether the Hangul syllables of [text] from [at] (right after a value of two syllables) are pieces of [_koTail], one
/// after another to the end of the run.
bool _koTailAt(String text, int at) {
  var end = at;
  while (end < text.length && end - at <= _koTailMax && _syllable(text.codeUnitAt(end))) {
    end++;
  }
  final run = text.substring(at, end);
  if (run.length > _koTailMax) return false;
  final ok = List<bool>.filled(run.length + 1, false); // ok[i]: run.substring(i) is pieces of the list
  ok[run.length] = true;
  for (var i = run.length - 1; i >= 0; i--) {
    ok[i] = _koTail.any((p) => run.startsWith(p, i) && ok[i + p.length]);
  }
  return ok[0];
}

final _mark = RegExp(r'^\p{M}$', unicode: true);

/// What joins the character before it into one grapheme cluster, so that a hit takes it in ("渡辺" and a variation
/// selector, "김민수" and a combining mark): marks (general category M), the zero width joiner and non-joiner,
/// conjoining Hangul vowels and finals, the half-width katakana sound marks, emoji modifiers and tag characters. The same
/// set in every package.
bool _joins(int cp) =>
    cp == 0x200C ||
    cp == 0x200D ||
    (cp >= 0x1160 && cp <= 0x11FF) ||
    (cp >= 0xD7B0 && cp <= 0xD7FF) ||
    cp == 0xFF9E ||
    cp == 0xFF9F ||
    (cp >= 0x1F3FB && cp <= 0x1F3FF) ||
    (cp >= 0xE0020 && cp <= 0xE007F) ||
    _mark.hasMatch(String.fromCharCode(cp));

/// [end] moved past what joins the character before it (see [_joins]).
int _widen(String text, int end) {
  while (end < text.length) {
    final cp = _cpAt(text, end);
    if (!_joins(cp)) break;
    end += cp > 0xFFFF ? 2 : 1;
  }
  return end;
}

/// [s] in capitals as Unicode writes them (from lib/src/upper.dart): String.toUpperCase maps one character to one, so
/// "ß" would stay "ß" where the other packages' runtimes write "SS".
String _fullUpper(String s) {
  final out = <int>[];
  for (final cp in s.runes) {
    final special = specialUpper[cp];
    if (special != null) {
      out.addAll(special);
      continue;
    }
    // the run of upperRuns that cp falls in, if any
    var lo = 0, hi = upperRuns.length ~/ 4 - 1, up = cp;
    while (lo <= hi) {
      final mid = (lo + hi) >> 1, first = upperRuns[mid * 4], last = upperRuns[mid * 4 + 1];
      if (cp < first) {
        hi = mid - 1;
      } else if (cp > last) {
        lo = mid + 1;
      } else {
        if ((cp - first) % upperRuns[mid * 4 + 2] == 0) up = cp + upperRuns[mid * 4 + 3];
        break;
      }
    }
    out.add(up);
  }
  return String.fromCharCodes(out);
}

/// Greek capitals that carry an accent or a breathing, as ranges (first, last, the capital without it): the tonos
/// capitals, the polytonic ones and those with varia or oxia (the dialytika is kept). From the Unicode decompositions
/// (U+0300, U+0301, U+0313, U+0314, U+0342, U+0343 left out); the same table in every package.
const _greekBare = [
  0x386, 0x386, 0x391, 0x388, 0x388, 0x395, 0x389, 0x389, 0x397, 0x38A, 0x38A, 0x399, 0x38C, 0x38C, 0x39F, //
  0x38E, 0x38E, 0x3A5, 0x38F, 0x38F, 0x3A9, 0x3D3, 0x3D3, 0x3D2, 0x1F08, 0x1F0F, 0x391, 0x1F18, 0x1F1D, 0x395, //
  0x1F28, 0x1F2F, 0x397, 0x1F38, 0x1F3F, 0x399, 0x1F48, 0x1F4D, 0x39F, 0x1F59, 0x1F59, 0x3A5, 0x1F5B, 0x1F5B, 0x3A5, //
  0x1F5D, 0x1F5D, 0x3A5, 0x1F5F, 0x1F5F, 0x3A5, 0x1F68, 0x1F6F, 0x3A9, 0x1FBA, 0x1FBB, 0x391, 0x1FC8, 0x1FC9, 0x395, //
  0x1FCA, 0x1FCB, 0x397, 0x1FDA, 0x1FDB, 0x399, 0x1FEA, 0x1FEB, 0x3A5, 0x1FEC, 0x1FEC, 0x3A1, 0x1FF8, 0x1FF9, 0x39F, //
  0x1FFA, 0x1FFB, 0x3A9,
];

/// A Greek letter (or a Coptic one of the Greek block).
bool _greek(int cp) => (cp >= 0x370 && cp <= 0x3FF) || (cp >= 0x1F00 && cp <= 0x1FFF);

/// [up] (a value in capitals) as Greek writes capitals: without accents and breathings on Greek letters, the dialytika
/// kept ("ΝΊΚΟΣ" as "ΝΙΚΟΣ"); null when [up] has no Greek letter.
String? _greekCapitals(String up) {
  if (!up.runes.any(_greek)) return null;
  final out = <int>[];
  var afterGreek = false; // the last letter that is not a combining mark is Greek
  for (var cp in up.runes) {
    if (cp >= 0x300 && cp <= 0x36F) {
      if (afterGreek) {
        if (const [0x300, 0x301, 0x313, 0x314, 0x342, 0x343].contains(cp)) continue;
        if (cp == 0x344) cp = 0x308; // (dialytika and tonos: the dialytika stays)
        if (cp == 0x308 && out.isNotEmpty && (out.last == 0x399 || out.last == 0x3A5)) {
          out[out.length - 1] = out.last == 0x399 ? 0x3AA : 0x3AB;
          continue;
        }
      }
      out.add(cp);
      continue;
    }
    afterGreek = _greek(cp);
    var bare = cp;
    for (var i = 0; i < _greekBare.length; i += 3) {
      if (cp >= _greekBare[i] && cp <= _greekBare[i + 1]) bare = _greekBare[i + 2];
    }
    out.add(bare);
  }
  return String.fromCharCodes(out);
}

/// The spellings of a value in capitals that [PiiVault.find] also matches, each way a language writes them: its full
/// Unicode uppercase ("WEISS" for "Weiß", "EMINE ŞAHIN"), for a value with "i" the Turkish and Azerbaijani capitals
/// with "İ" ("EMİNE ŞAHİN"), for a Greek value the capitals without accents and breathings ("ΕΛΕΝΗ" for "Ελένη"), and
/// for a value with a sharp s the capitals with the capital sharp s ("WEIẞ"). Spellings that are [text] itself, or that
/// the caseless match covers, are left out. The same spellings as the other packages.
List<String> _capitals(String text, int caseless) {
  final out = <String>[];
  void add(String s) {
    if (s != text && !out.contains(s)) out.add(s);
  }

  final up = _fullUpper(text);
  if (!(caseless == text.length && up == _asciiUpper(text))) add(up);
  final greek = _greekCapitals(up);
  if (greek != null) add(greek);
  if (text.contains('i')) add(_fullUpper(text.replaceAll('i', String.fromCharCode(0x130))));
  final sharp = String.fromCharCode(0xDF);
  if (text.contains(sharp)) add(_fullUpper(text.replaceAll(sharp, String.fromCharCode(0x1E9E))));
  return out;
}

/// Long enough to be found again at word edges: 3 characters, or 2 when one is in a script written without spaces or
/// both are Hangul syllables (Korean particles may follow them).
bool _longEnough(String v) {
  final cps = v.runes.toList();
  return cps.length >= 3 || (cps.length == 2 && (cps.any(_noSpaceCp) || cps.every(_syllable)));
}

// What a span's edges can hold that is not part of the value (all in the basic plane, so one UTF-16 unit each): white
// space (Unicode White_Space); sentence punctuation (. , ; : ! ? and the ellipsis, ideographic and full-width forms);
// brackets and quotation marks, each with its partner. As in the npm and Python packages.
const _edgeSpace = {
  0x9, 0xA, 0xB, 0xC, 0xD, 0x20, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, //
  0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
};
const _edgeStop = {
  0x2E, 0x2C, 0x3B, 0x3A, 0x21, 0x3F, 0x2026, 0x3001, 0x3002, 0xFF01, 0xFF0C, 0xFF0E, 0xFF1A, 0xFF1B, 0xFF1F, //
};
const _closer = {
  // an opening bracket: its closing one
  0x28: 0x29, 0x5B: 0x5D, 0x7B: 0x7D, 0x3C: 0x3E, 0x201C: 0x201D, 0x2018: 0x2019, 0xAB: 0xBB, 0x300C: 0x300D, //
  0x300E: 0x300F, 0xFF08: 0xFF09, 0xFF3B: 0xFF3D, 0xFF5B: 0xFF5D, 0x3008: 0x3009, 0x300A: 0x300B, 0x3010: 0x3011,
  0xFF1C: 0xFF1E, 0x3014: 0x3015,
};
final _opener = {for (final e in _closer.entries) e.value: e.key}; // a closing bracket: its opening one
const _edgeQuote = {0x22, 0x27, 0x60}; // " ' and `, their own partners

/// Whether a date as ISO 8601 writes it, followed by "T" and a digit (the start of an ISO timestamp), starts at [at].
bool _isoStamp(String text, int at) {
  if (at + 12 > text.length) return false;
  bool d(int i) => text.codeUnitAt(at + i) >= 0x30 && text.codeUnitAt(at + i) <= 0x39;
  final t = text.codeUnitAt(at + 10);
  return d(0) &&
      d(1) &&
      d(2) &&
      d(3) &&
      text.codeUnitAt(at + 4) == 0x2D &&
      d(5) &&
      d(6) &&
      text.codeUnitAt(at + 7) == 0x2D &&
      d(8) &&
      d(9) &&
      (t == 0x54 || t == 0x74) &&
      d(11);
}

/// [start, end) of [text], a span a detector found with [label], without what its edges hold that is not part of the
/// value: a model can mark "Kim Minsu." at the end of a sentence, an address with the " (" after it, a name with the
/// quotation marks around it or a bytes literal's "b'", and the vault would learn that as the value. At either edge
/// white space goes. Then, except in a SECRET (a password can end with "!" or hold brackets): sentence punctuation (at
/// the start not a ".", and at the end of an ORG not its ".": "Tiffany & Co."); a bracket whose partner is not in the
/// span, a bracket that opens at the end or closes at the start; a straight quotation mark that is the only one of its
/// kind left in the span; a pair of brackets, or the only two of one straight quotation mark, around all the rest; and
/// the "b'" of a Python bytes literal at the start. A date of birth that goes on into the time of its ISO timestamp
/// ("1990-01-02T00") is cut at the date, the edge where the vault finds a known date in a timestamp. A bracket whose
/// partner is inside stays ("(02) 1234-5678"). null when nothing but white space and punctuation is left; the span
/// without its white space when what is left is too short to be found again ([_longEnough]). One pass over the span
/// (the bracket partners in a list): time in proportion to its length. The same in every package.
(int, int)? _valueEdges(String text, int start, int end, [String label = '']) {
  // the partner of each bracket in the span (one pass: a stack for each kind of bracket), and how many of each straight
  // quotation mark the span holds, kept as its edges go
  final partner = List<int>.filled(end - start, -1); // (at i - start; -1: none)
  final opened = <int, List<int>>{};
  final quotes = <int, int>{};
  for (var i = start; i < end; i++) {
    final c = text.codeUnitAt(i);
    if (_closer.containsKey(c)) {
      (opened[c] ??= []).add(i);
    } else if (_opener.containsKey(c)) {
      final stack = opened[_opener[c]];
      if (stack != null && stack.isNotEmpty) {
        final j = stack.removeLast();
        partner[i - start] = j;
        partner[j - start] = i;
      }
    } else if (_edgeQuote.contains(c)) {
      quotes[c] = (quotes[c] ?? 0) + 1;
    }
  }
  var a = start, b = end;
  while (a < b && _edgeSpace.contains(text.codeUnitAt(b - 1))) {
    b--;
  }
  while (a < b && _edgeSpace.contains(text.codeUnitAt(a))) {
    a++;
  }
  final ws = a, we = b;
  while (a < b && label != 'SECRET') {
    final last = text.codeUnitAt(b - 1);
    if (_edgeSpace.contains(last) ||
        (_edgeStop.contains(last) && !(last == 0x2E && label == 'ORG')) ||
        _closer.containsKey(last) || // a bracket that opens at the end
        (_opener.containsKey(last) && partner[b - 1 - start] < a) || // one that closes what the span does not open
        (_edgeQuote.contains(last) && quotes[last] == 1)) {
      quotes.remove(last);
      b--;
      continue;
    }
    final first = text.codeUnitAt(a);
    if (_edgeSpace.contains(first) ||
        (_edgeStop.contains(first) && first != 0x2E) ||
        _opener.containsKey(first) || // a bracket that closes at the start
        (_closer.containsKey(first) &&
            !(partner[a - start] >= 0 && partner[a - start] < b)) || // one that opens what the span does not close
        (_edgeQuote.contains(first) && quotes[first] == 1)) {
      quotes.remove(first);
      a++;
      continue;
    }
    // a pair around all the rest
    if (b - a >= 2 &&
        ((_closer.containsKey(first) && partner[a - start] == b - 1) ||
            (_edgeQuote.contains(first) && last == first && quotes[first] == 2))) {
      quotes.remove(first);
      a++;
      b--;
      continue;
    }
    // the "b'" of a Python bytes literal (not a "b" at the end of a word: "Bob'")
    if (first == 0x62 &&
        b - a >= 2 &&
        _edgeQuote.contains(text.codeUnitAt(a + 1)) &&
        (a == 0 || !_isWord(_cpBefore(text, a)))) {
      final q = text.codeUnitAt(a + 1);
      final k = quotes[q];
      if (k != null) quotes[q] = k - 1;
      a += 2;
      continue;
    }
    break;
  }
  if (label == 'DATE_OF_BIRTH' && b - a > 11 && _isoStamp(text, a)) b = a + 10;
  if (a >= b) return null;
  if ((a == ws && b == we) || _longEnough(text.substring(a, b))) return (a, b);
  return (ws, we);
}

/// [spans] of [text] with [_valueEdges], for the vault to learn; a span with nothing left is left out. A block-list span
/// (its entry is exact) and a known value's span stay as they are.
List<PiiSpan> _valueSpans(String text, List<PiiSpan> spans) => [
  for (final s in spans)
    if (s.source == 'block' || s.source == 'vault' || s.start < 0 || s.start >= s.end || s.end > text.length)
      s
    else
      ...switch (_valueEdges(text, s.start, s.end, s.label)) {
        null => const <PiiSpan>[],
        (final a, final b) when a != s.start || b != s.end => [PiiSpan(a, b, s.label, s.source)],
        _ => [s],
      },
];

/// Whether a UTF-16 unit changes in the vault's reading of a text (full-width ASCII, U+3000, a conjoining Hangul vowel
/// or final).
bool _readDifferently(int c) =>
    (c >= 0xFF01 && c <= 0xFF5E) || c == 0x3000 || (c >= 0x1161 && c <= 0x1175) || (c >= 0x11A8 && c <= 0x11C2);

/// The text as the vault reads it: full-width ASCII (U+FF01 to U+FF5E) and U+3000 as ASCII, and Hangul written as
/// conjoining jamo (NFD, as macOS file names have it) composed into syllables, as NFC composes them. `map[i]` is where
/// UTF-16 unit `i` of the reading starts in the text, and `map[length]` is the text's length; there is no map when the
/// reading is the text.
final class _Reading {
  final String text;
  final List<int>? _map;
  const _Reading(this.text, this._map);

  /// The place in the text of a place in the reading.
  int at(int i) => _map == null ? i : _map[i];
}

_Reading _read(String text) {
  if (!text.codeUnits.any(_readDifferently)) return _Reading(text, null);
  final units = text.codeUnits;
  final out = <int>[];
  final map = <int>[];
  var k = 0;
  while (k < units.length) {
    final cp = units[k];
    final n1 = k + 1 < units.length ? units[k + 1] : 0;
    final n2 = k + 2 < units.length ? units[k + 2] : 0;
    var r = cp, used = 1;
    if (cp >= 0xFF01 && cp <= 0xFF5E) {
      r = cp - 0xFEE0;
    } else if (cp == 0x3000) {
      r = 0x20;
    } else if (cp >= 0x1100 && cp <= 0x1112 && n1 >= 0x1161 && n1 <= 0x1175) {
      // a leading consonant and a vowel, and a final consonant after them
      final lv = 0xAC00 + ((cp - 0x1100) * 21 + (n1 - 0x1161)) * 28;
      if (n2 >= 0x11A8 && n2 <= 0x11C2) {
        r = lv + n2 - 0x11A7;
        used = 3;
      } else {
        r = lv;
        used = 2;
      }
    } else if (_syllable(cp) && (cp - 0xAC00) % 28 == 0 && n1 >= 0x11A8 && n1 <= 0x11C2) {
      // a syllable without a final consonant, and a final consonant after it
      r = cp + n1 - 0x11A7;
      used = 2;
    }
    map.add(k);
    out.add(r);
    k += used;
  }
  map.add(units.length);
  return _Reading(String.fromCharCodes(out), map);
}

String _asciiLower(String s) {
  final u = s.codeUnits;
  if (!u.any((c) => c >= 0x41 && c <= 0x5A)) return s;
  return String.fromCharCodes([for (final c in u) c >= 0x41 && c <= 0x5A ? c + 32 : c]);
}

String _asciiUpper(String s) {
  final u = s.codeUnits;
  if (!u.any((c) => c >= 0x61 && c <= 0x7A)) return s;
  return String.fromCharCodes([for (final c in u) c >= 0x61 && c <= 0x7A ? c - 32 : c]);
}

/// A value of two or more words: runs of word characters with white space between them.
bool _words(String v) {
  var pieces = 0;
  var word = false;
  for (final cp in v.runes) {
    if (_isSpace(cp)) {
      if (word) pieces++;
      word = false;
    } else if (_isWord(cp)) {
      word = true;
    }
  }
  if (word) pieces++;
  return pieces >= 2;
}

/// How many UTF-16 units of [v] (from its start) are matched in any ASCII case: all of an e-mail address, a user name
/// and a value of two or more words; a URL's scheme and host; nothing of other values.
int _caselessPrefix(String label, String v) {
  if (label == 'EMAIL' || label == 'USERNAME' || _words(v)) return v.length;
  if (label != 'URL') return 0;
  bool alpha(int c) => (c | 0x20) >= 0x61 && (c | 0x20) <= 0x7A;
  bool schemeChar(int c) => alpha(c) || (c >= 0x30 && c <= 0x39) || c == 0x2B || c == 0x2E || c == 0x2D;
  final u = v.codeUnits;
  var host = 0;
  var i = 0;
  while (i < u.length && schemeChar(u[i])) {
    i++;
  }
  if (i < u.length && i > 0 && alpha(u[0]) && v.startsWith('://', i)) host = i + 3;
  for (var j = host; j < u.length; j++) {
    if (u[j] == 0x2F || u[j] == 0x3F || u[j] == 0x23) return j;
  }
  return v.length;
}

/// A value of at most this many UTF-16 units is compared from its start at each place where it may start (at most 32
/// steps a place); a longer one is searched for in one pass ([_occurrences]). The same in every package.
const _shortNeedle = 32;

/// Every place where [p] starts in [s], overlapping ones too ("Ann Ann" in "xAnn Ann Ann"), in time linear in both. A
/// long value is searched for with Knuth-Morris-Pratt, so that one that repeats itself (". . . . Kim" in a text of
/// ". . . . . . Kim", "ha ha ha" again and again) is not compared again from its start at every place.
List<int> _occurrences(String s, String p) {
  final m = p.length;
  if (m == 0 || m > s.length) return const [];
  if (m <= _shortNeedle) {
    // (a value that is not in the text costs one indexOf and nothing more, as before 1.7.2)
    final first = s.indexOf(p);
    if (first < 0) return const [];
    return [for (var i = first; i >= 0; i = s.indexOf(p, i + 1)) i];
  }
  final out = <int>[];
  // fail[j]: the length of the longest proper prefix of p[0..j] that is also a suffix of it
  final fail = List<int>.filled(m, 0);
  var k = 0;
  for (var j = 1; j < m; j++) {
    final c = p.codeUnitAt(j);
    while (k > 0 && c != p.codeUnitAt(k)) {
      k = fail[k - 1];
    }
    if (c == p.codeUnitAt(k)) k++;
    fail[j] = k;
  }
  k = 0;
  for (var i = 0; i < s.length; i++) {
    final c = s.codeUnitAt(i);
    while (k > 0 && c != p.codeUnitAt(k)) {
      k = fail[k - 1];
    }
    if (c == p.codeUnitAt(k)) k++;
    if (k == m) {
      out.add(i + 1 - m);
      k = fail[m - 1];
    }
  }
  return out;
}

/// The places of [at] (sorted) where [p] also starts [offset] units later in [s]: one pass over both lists when [p] is
/// long, so that checking the part of a value that must be written as it is costs no more than finding it.
List<int> _followedBy(List<int> at, String s, int offset, String p) {
  if (at.isEmpty) return at;
  if (p.length <= _shortNeedle) {
    return [
      for (final a in at)
        if (s.startsWith(p, a + offset)) a,
    ];
  }
  final starts = _occurrences(s, p);
  final out = <int>[];
  var j = 0;
  for (final a in at) {
    while (j < starts.length && starts[j] < a + offset) {
      j++;
    }
    if (j < starts.length && starts[j] == a + offset) out.add(a);
  }
  return out;
}

/// A known value as the vault searches for it: its first [entry], the value as the vault reads it ([text]), how many
/// units of it match in any ASCII case ([caseless], with [lower] the text in ASCII lower case), and [uppers], the text in
/// capitals, in each spelling that is another one and that the caseless match does not cover ([_capitals]).
final class _Known {
  final PiiVaultEntry entry;
  final String text;
  final int caseless;
  final String lower;
  final List<String> uppers;
  final bool findable;

  _Known._(this.entry, this.text, this.caseless, this.lower, this.uppers, this.findable);

  factory _Known(PiiVaultEntry e) {
    final text = _read(e.value).text;
    final n = _caselessPrefix(e.label, text);
    return _Known._(e, text, n, n > 0 ? _asciiLower(text) : '', _capitals(text, n), _longEnough(text));
  }

  /// Whether [s] (a reading) is this value in a spelling the vault matches.
  bool matches(String s) =>
      s == text ||
      uppers.contains(s) ||
      (caseless > 0 &&
          s.length == text.length &&
          _asciiLower(s.substring(0, caseless)) == lower.substring(0, caseless) &&
          s.substring(caseless) == text.substring(caseless));
}

int _cpBefore(String t, int i) {
  final c = t.codeUnitAt(i - 1);
  if (c >= 0xDC00 && c <= 0xDFFF && i >= 2) {
    final h = t.codeUnitAt(i - 2);
    if (h >= 0xD800 && h <= 0xDBFF) return 0x10000 + ((h - 0xD800) << 10) + (c - 0xDC00);
  }
  return c;
}

int _cpAt(String t, int i) =>
    String.fromCharCodes(t.codeUnits.sublist(i, i + 2 <= t.length ? i + 2 : i + 1)).runes.first;

/// A number as JSON writes one, with a sign in front or without ("4524", "-4.524e3", "+8210"; not "04524"): what an
/// exponent can be.
final _jsonNumber = RegExp(r'^[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$');

/// Word edges for [start, end) of [text]: a value is not found inside a longer word. A letter of a script written without
/// spaces on either side of the boundary is an edge, but not a mark of such a script after a value that ends in one (a
/// Thai vowel sign: "สม" is not found in "สมัคร"); where Hangul meets other letters or digits is an edge, Korean
/// particles may follow a Hangul value, and Hangul right before a Hangul value is not an edge. After a value of two
/// Hangul syllables, more syllables are an edge only as particles, 님, 씨 or the copula ([_koTail]): "지원님께" and
/// "지원이" are the name, "지원금" is another word.
bool _atEdges(String text, int start, int end) {
  final first = _cpAt(text, start), last = _cpBefore(text, end);
  bool okBefore = true, okAfter = true;
  if (start > 0) {
    final b = _cpBefore(text, start);
    okBefore = !_isWord(b) || !_isWord(first) || _noSpaceCp(first) || _noSpaceCp(b) || _hangulCp(b) != _hangulCp(first);
  }
  if (end < text.length) {
    final a = _cpAt(text, end);
    if (_syllable(a) && end - start == 2 && _syllable(first) && _syllable(last)) {
      okAfter = _koTailAt(text, end);
    } else {
      // (a mark of a script written without spaces belongs to the letter before it: the word goes on)
      final markAfter = _noSpaceCp(last) && _noSpaceMarkCp(a);
      okAfter =
          !_isWord(a) ||
          !_isWord(last) ||
          (!markAfter && (_noSpaceCp(last) || _noSpaceCp(a))) ||
          _hangulCp(a) ||
          _hangulCp(last);
    }
  }
  // a dot between two digits joins them ("4524" is not found in "4524.7" or in "1.4524"), and so does the sign of an
  // exponent before a number ("1e+4524"); a digit is any number character, as in a word. An exponent is a number: a
  // value that is not one (an address, a phone number with spaces, "04524") is not joined to "<digit>e+" before it
  int back(int i) => i > 0 ? _cpBefore(text, i) : -1;
  int width(int cp) => cp > 0xFFFF ? 2 : 1;
  final before = back(start);
  final at2 = before < 0 ? 0 : start - width(before);
  final second = back(at2);
  final third = second < 0 ? -1 : back(at2 - width(second));
  // (whether the value is a number is asked last: reading it costs its length, at every place where it is found)
  bool number() => _jsonNumber.hasMatch(text.substring(start, end));
  final exponent =
      (before == 0x2B || before == 0x2D) && (second == 0x65 || second == 0x45) && _isNumber(third) && number();
  // (the value may start with the sign itself: "-4524" in "1e-4524")
  final signed =
      (first == 0x2B || first == 0x2D) &&
      end - start > 1 &&
      _isNumber(_cpAt(text, start + 1)) &&
      (before == 0x65 || before == 0x45) &&
      _isNumber(second) &&
      number();
  final joinedBefore = (_isNumber(first) && ((before == 0x2E && _isNumber(second)) || exponent)) || signed;
  final joinedAfter =
      _isNumber(last) && end + 1 < text.length && text.codeUnitAt(end) == 0x2E && _isNumber(_cpAt(text, end + 1));
  return okBefore && okAfter && !joinedBefore && !joinedAfter;
}

String _family(String label) {
  var up = _fullUpper(label);
  if (up.endsWith('_L2')) up = up.substring(0, up.length - 3);
  // one "_" per character (code point) that is not A-Z, 0-9 or "_", as in every package
  var cleaned = up.replaceAll(RegExp(r'[^A-Z0-9_]', unicode: true), '_');
  if (cleaned.length > _maxFamily) cleaned = cleaned.substring(0, _maxFamily);
  cleaned = cleaned.replaceFirst(RegExp(r'^[^A-Z]+'), '');
  return cleaned.isEmpty ? 'PII' : cleaned;
}

Object? _mapStrings(Object? value, String Function(String) f) => switch (value) {
  String s => f(s),
  List l => [for (final e in l) _mapStrings(e, f)],
  Map m => {for (final e in m.entries) e.key: _mapStrings(e.value, f)},
  _ => value,
};

/// Numbered placeholders that can be undone. Pass [entries] (from [entries]) to continue a saved vault; they hold the
/// original values, so store them only where the original text may be stored.
final class PiiVault {
  final _list = <PiiVaultEntry>[];
  final _byTag = <String, PiiVaultEntry>{};
  final _byValue = <String, PiiVaultEntry>{};
  // each value once, in the order it was added (its place is its age), and by the spellings `_whole` takes
  final _known = <_Known>[];
  final _byRead = <String, int>{};
  final _byUpper = <String, int>{};
  final _byLower = <String, List<int>>{};
  final _counters = <String, int>{};
  // the values a model found that are shorter than the vault searches for (`_tagFound`): held to be restored, not found as
  // a whole value elsewhere
  final _local = <String>{};
  final _replays = <String, String>{};
  int _generation = 0;

  PiiVault([Iterable<PiiVaultEntry> entries = const []]) {
    for (final e in entries) {
      _add(PiiVaultEntry(e.tag, _family(e.label), e.value));
    }
  }

  /// [e] held; with [local], a new value is held to be restored only, not searched for (`_tagFound`).
  void _add(PiiVaultEntry e, {bool local = false}) {
    final m = _entryTag.firstMatch(e.tag);
    if (m == null || m[1]!.length > _maxFamily || m[2]!.length > 12 || e.value.isEmpty) {
      throw ArgumentError('not a vault entry: ${e.tag}');
    }
    final n = int.parse(m[2]!);
    if (_byTag.containsKey(e.tag)) throw ArgumentError('duplicate vault tag ${e.tag}');
    final c = _counters[m[1]!] ?? 0;
    _counters[m[1]!] = n > c ? n : c;
    _list.add(e);
    _byTag[e.tag] = e;
    if (!_byValue.containsKey(e.value)) {
      _byValue[e.value] = e;
      if (local) {
        _local.add(e.value);
      } else {
        _index(e);
      }
    }
  }

  /// The value of [e] searched for: its spellings indexed.
  void _index(PiiVaultEntry e) {
    final k = _Known(e);
    final age = _known.length;
    _byRead.putIfAbsent(k.text, () => age);
    for (final u in k.uppers) {
      _byUpper.putIfAbsent(u, () => age);
    }
    if (k.caseless > 0) _byLower.putIfAbsent(k.lower, () => []).add(age);
    _known.add(k);
  }

  /// The known value that [value] is as a whole, in any spelling [find] matches (the one held longest), also with what
  /// joins its last character after it (a mark, a variation selector), as [find] takes that in.
  PiiVaultEntry? _whole(String value) {
    if (_known.isEmpty || value.isEmpty) return null;
    final r = _read(value).text;
    final found = _wholeRead(r);
    if (found != null) return found;
    var cut = r.length;
    while (cut > 0 && _joins(_cpBefore(r, cut))) {
      cut -= _cpBefore(r, cut) > 0xFFFF ? 2 : 1;
    }
    if (cut == 0 || cut == r.length || !_atEdges(r, 0, cut)) return null;
    return _wholeRead(r.substring(0, cut));
  }

  /// [_whole] for a reading, as it is.
  PiiVaultEntry? _wholeRead(String r) {
    int? best;
    for (final k in [_byRead[r], _byUpper[r]]) {
      if (k != null && (best == null || k < best)) best = k;
    }
    for (final k in _byLower[_asciiLower(r)] ?? const <int>[]) {
      if (_known[k].matches(r)) {
        if (best == null || k < best) best = k;
        break;
      }
    }
    return best == null ? null : _known[best].entry;
  }

  /// The placeholder for a value found in a text: that of the known value it is in another spelling, or its own. A new
  /// value shorter than the vault searches for at word edges (3 characters, 2 in scripts written without spaces and two
  /// Hangul syllables) is held to be restored, but not found as a whole value elsewhere ([hideValue]): a "0" that a model
  /// took for a date once must not turn every "0" into a placeholder. Values the application tags ([tag]) are found as a
  /// whole value whatever their length.
  String _tagFound(String label, String value) {
    final whole = _whole(value);
    if (whole != null) return whole.tag;
    final held = _byValue[value];
    if (held != null) return held.tag; // (held already: one value, one placeholder)
    if (value.isEmpty || _longEnough(_read(value).text)) return tag(label, value);
    return _addNew(label, value, local: true);
  }

  /// Number of values in the vault.
  int get length => _list.length;

  /// Goes up by one on every [clear].
  int get generation => _generation;

  /// The placeholder for [value], adding it under [label] (its family) when new. The same value keeps its first tag. An
  /// empty value has no placeholder (""). Numbers go up to 999,999,999,999; a family that has used them up (after an
  /// imported entry with that number) goes on as "[PERSON__1]".
  String tag(String label, String value) {
    if (value.isEmpty) return '';
    final known = _byValue[value];
    if (known != null) {
      // (a short value a model found is found as a whole value from now on: the application gives it)
      if (_local.remove(value)) _index(known);
      return known.tag;
    }
    return _addNew(label, value);
  }

  /// A new entry for [value] under [label] (its family); [local] as in [_add].
  String _addNew(String label, String value, {bool local = false}) {
    final fam = _family(label);
    // a family whose numbers are used up goes on in the next one, so a value always gets a placeholder that can be undone
    var numbered = fam;
    for (var left = _familySteps; left > 0 && (_counters[numbered] ?? 0) >= _maxNumber; left--) {
      numbered = _nextFamily(numbered);
    }
    // every family on the way used up (some 10^13 values, or a state made by hand): masked, not undone ("PERSON_1"
    // gives "[PERSON]", never a numbered placeholder)
    if ((_counters[numbered] ?? 0) >= _maxNumber) return '[${fam.replaceFirst(_numberTail, '')}]';
    final e = PiiVaultEntry('[${numbered}_${(_counters[numbered] ?? 0) + 1}]', fam, value);
    _add(e, local: local);
    return e.tag;
  }

  /// The value behind a placeholder.
  String? value(String tag) => _byTag[tag]?.value;

  /// The vault's placeholders in [text] replaced with their values; other bracketed text is left as it is. [only] limits
  /// the placeholders (a set), and [values] fixes them (a snapshot from [bindings]); with [json] the values are escaped
  /// for the inside of a JSON string.
  String restore(String text, {Set<String>? only, Map<String, String>? values, bool json = false}) =>
      text.replaceAllMapped(_tag, (m) {
        final t = m[0]!;
        final v = values != null ? values[t] : (only != null && !only.contains(t) ? null : _byTag[t]?.value);
        if (v == null) return t;
        if (!json) return v;
        final e = jsonEncode(v);
        return e.substring(1, e.length - 1);
      });

  /// [restore] applied to every string inside a JSON-like value (maps and lists are copied, keys are kept).
  Object? restoreValue(Object? value, {Set<String>? only, Map<String, String>? values}) =>
      _mapStrings(value, (s) => restore(s, only: only, values: values));

  /// A restorer for a streamed text. It uses the values the vault holds when it is created, for the whole stream.
  PiiVaultRestorer restorer({Set<String>? only, Map<String, String>? values, bool json = false}) =>
      PiiVaultRestorer._(this, null, values ?? bindings(only ?? _byTag.keys.toList()), json);

  /// The current values of these placeholders, as a snapshot for `values:`.
  Map<String, String> bindings(Iterable<String> tags) => {
    for (final t in tags)
      if (_byTag[t] != null) t: _byTag[t]!.value,
  };

  /// Where the known values occur in [text] (sorted, non-overlapping; overlapping occurrences are merged into one hit
  /// whose value is the merged text and whose label is the first one's). A hit carries the known value, also where it is
  /// written another way (see the notes at the top); where two values are found on the same part of the text, the one
  /// the vault has held longer.
  List<PiiVaultHit> find(String text) {
    if (_known.isEmpty) return const [];
    final reading = _read(text);
    final r = reading.text;
    String? lower;
    // start and end in the reading, and the value's age
    final found = <(int, int, int)>[];
    for (var age = 0; age < _known.length; age++) {
      final k = _known[age];
      if (!k.findable) continue;
      // (a hit takes in what joins its last character, so that it never ends inside a grapheme cluster)
      void take(int at, int end) {
        if (_atEdges(r, at, end)) found.add((at, _widen(r, end), age));
      }

      if (k.caseless > 0) {
        // the caseless part in any ASCII case, the rest as it is written
        final low = lower ??= _asciiLower(r);
        for (final i in _followedBy(_occurrences(low, k.lower), r, k.caseless, k.text.substring(k.caseless))) {
          take(i, i + k.lower.length);
        }
      } else {
        for (final i in _occurrences(r, k.text)) {
          take(i, i + k.text.length);
        }
      }
      for (final u in k.uppers) {
        for (final i in _occurrences(r, u)) {
          take(i, i + u.length);
        }
      }
    }
    found.sort((a, b) => a.$1 != b.$1 ? a.$1 - b.$1 : (a.$2 != b.$2 ? b.$2 - a.$2 : a.$3 - b.$3));
    // overlapping known values are merged ("Alex Kim" + "Kim Lee"); the value of a merged hit is the text it covers
    final merged = <List<int>>[]; // start, end, age, 1 when merged
    for (final h in found) {
      if (merged.isNotEmpty && h.$1 < merged.last[1]) {
        final l = merged.last;
        if (h.$2 > l[1]) {
          l[1] = h.$2;
          l[3] = 1;
        }
      } else {
        merged.add([h.$1, h.$2, h.$3, 0]);
      }
    }
    return [
      for (final m in merged)
        () {
          final start = reading.at(m[0]), end = reading.at(m[1]);
          final e = _known[m[2]].entry;
          return PiiVaultHit(start, end, e.label, m[3] == 1 ? text.substring(start, end) : e.value);
        }(),
    ];
  }

  /// The vault's known values in [text] replaced with their placeholders (no model call). Overlapping known values are
  /// hidden together under a new placeholder. With [escape], placeholder-like text in the rest is escaped.
  String hide(String text, {bool escape = false}) {
    String esc(String t) => escape ? escapeTags(t) : t;
    final out = StringBuffer();
    var last = 0;
    for (final h in find(text)) {
      out.write(esc(text.substring(last, h.start)));
      out.write(_byValue[h.value]?.tag ?? tag(h.label, h.value));
      last = h.end;
    }
    out.write(esc(text.substring(last)));
    return out.toString();
  }

  /// [hide] applied to every string inside a JSON-like value; a string that is as a whole a known value (in any spelling
  /// [find] matches) is replaced whatever its length.
  Object? hideValue(Object? value) => _mapStrings(value, (s) => _whole(s)?.tag ?? hide(s));

  /// Remembers what the model wrote ([masked]) for a text that was restored (at most 4,096).
  void remember(String key, String masked) {
    _replays.remove(key);
    _replays[key] = masked;
    while (_replays.length > 4096) {
      _replays.remove(_replays.keys.first);
    }
  }

  /// What [remember] stored for [key].
  String? recall(String key) => _replays[key];

  /// The entries in the order they were added.
  List<PiiVaultEntry> get entries => List.unmodifiable(_list);

  void clear() {
    _list.clear();
    _byTag.clear();
    _byValue.clear();
    _known.clear();
    _byRead.clear();
    _byUpper.clear();
    _byLower.clear();
    _counters.clear();
    _local.clear();
    _replays.clear();
    _generation++;
  }

  /// [spans] (detected in [text]) plus the vault's known values (source "vault"). A known value that spans cover only in
  /// part is masked whole: the overlapping spans are merged into one span over both. One pass over the hits and the
  /// spans (sorted by their start first if they are not). A span over known values has their family as its label, a
  /// custom one too ("CUSTOMER"), and the label of a span the model found when it was merged with one.
  List<PiiSpan> withVaultSpans(String text, List<PiiSpan> spans) {
    final hits = find(text); // in order, apart
    if (hits.isEmpty) return spans;
    var sorted = spans;
    for (var i = 1; i < spans.length; i++) {
      if (spans[i - 1].start > spans[i].start) {
        sorted = [...spans]..sort((a, b) => a.start - b.start);
        break;
      }
    }
    final out = <PiiSpan>[];
    var next = 0;
    for (final h in hits) {
      while (next < sorted.length && sorted[next].end <= h.start) {
        out.add(sorted[next++]);
      }
      final over = <PiiSpan>[];
      // one that reaches this far (merged before)
      if (out.isNotEmpty && out.last.end > h.start) over.add(out.removeLast());
      while (next < sorted.length && sorted[next].start < h.end) {
        over.add(sorted[next++]);
      }
      if (over.isNotEmpty && over.every((s) => s.start <= h.start && s.end >= h.end)) {
        out.addAll(over); // already masked whole
        continue;
      }
      var start = h.start, end = h.end;
      for (final s in over) {
        if (s.start < start) start = s.start;
        if (s.end > end) end = s.end;
      }
      final label = (start == h.start && end == h.end) || over.isEmpty ? h.label : over.first.label;
      out.add(PiiSpan(start, end, label, 'vault'));
    }
    while (next < sorted.length) {
      out.add(sorted[next++]);
    }
    return out;
  }
}

/// Undoes masking in text that arrives in pieces (a streamed answer). A placeholder split across pieces is held back
/// until it is complete, so [push] may return less than it was given; [flush] returns what is left.
final class PiiVaultRestorer {
  final PiiVault _vault;
  final Set<String>? _only;
  final Map<String, String>? _values;
  final bool _json;
  var _held = '';

  PiiVaultRestorer._(this._vault, this._only, this._values, this._json);

  String push(String chunk) {
    _held += chunk;
    final from = _held.length > _maxTag ? _held.length - _maxTag : 0;
    final m = _tagPrefix.firstMatch(_held.substring(from));
    final cut = m != null ? _held.length - m[0]!.length : _held.length;
    final out = _vault.restore(_held.substring(0, cut), only: _only, values: _values, json: _json);
    _held = _held.substring(cut);
    return out;
  }

  String flush() {
    final out = _vault.restore(_held, only: _only, values: _values, json: _json);
    _held = '';
    return out;
  }
}

/// [spans] replaced by [vault]'s numbered placeholders; placeholder-like text left unmasked is escaped ("［PERSON_1]").
/// The vault learns each span without what its edges hold that is not part of the value, as in [redactWithVault].
String applyVaultRedaction(String text, List<PiiSpan> spans, PiiVault vault) =>
    _applySpans(text, _valueSpans(text, spans), vault);

/// [applyVaultRedaction] with the spans as they are.
String _applySpans(String text, List<PiiSpan> spans, PiiVault vault) {
  final out = StringBuffer();
  var last = 0;
  for (final s in spans) {
    out.write(escapeTags(text.substring(last, s.start)));
    // (a known value written another way keeps its placeholder)
    out.write(vault._tagFound(s.label, text.substring(s.start, s.end)));
    last = s.end;
  }
  out.write(escapeTags(text.substring(last)));
  return out.toString();
}

/// [text] masked with [vault]: the spans the model [marked] plus the vault's known values, and then every value this text
/// added to the vault masked wherever it occurs again in it ("I met Alice. Say Alice twice." when the model finds only
/// the first "Alice"). The found spans lose what their edges hold that is not part of the value first ("Kim Minsu." at
/// the end of a sentence is the value "Kim Minsu"). Returns the text and the final spans; this is what
/// `NewmeanPii.redactVault` does.
PiiRedaction redactWithVault(String text, List<PiiSpan> marked, PiiVault vault) {
  final found = _valueSpans(text, marked);
  var spans = vault.withVaultSpans(text, found);
  var out = _applySpans(text, spans, vault); // the vault learns this text's values here
  // also what the model found inside a larger merged span ("Kim Lee" of "Alex Kim Lee"), so it is masked where it stands alone
  for (final s in found) {
    final value = text.substring(s.start, s.end);
    if (value.trim().isNotEmpty) vault._tagFound(s.label, value);
  }
  // until nothing new is found: every round either masks more of the text or merges spans, so this ends
  while (true) {
    final next = vault.withVaultSpans(text, spans);
    var same = next.length == spans.length;
    for (var i = 0; same && i < next.length; i++) {
      same = next[i].start == spans[i].start && next[i].end == spans[i].end;
    }
    if (same) return PiiRedaction(out, spans);
    spans = next;
    out = _applySpans(text, spans, vault);
  }
}
