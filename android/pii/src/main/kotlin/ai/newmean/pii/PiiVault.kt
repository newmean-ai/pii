package ai.newmean.pii

/*
 * A vault: the local map from numbered placeholders back to the values they replace, so that masking can be undone in a
 * model's answer (since 1.7.2). It is an ordinary in-memory object; nothing in it is sent anywhere. The placeholders and
 * the search for known values are those of the npm package's createVault() and the Python package's Vault (shared cases:
 * python/tests/vault_cases.json); the other spellings the npm and Python middlewares also read (escapes, URL forms,
 * numbers written another way, other white space) are not read here.
 *
 *   val vault = PiiVault()   // one per user or conversation, never shared between users
 *   redactor.redact("Call Kim Minsu on 010-1234-5678", vault).text  // "Call [PERSON_1] on [PHONE_1]"
 *   vault.restore("I called [PERSON_1].")                           // "I called Kim Minsu."
 *
 * The same value always gets the same placeholder within a vault, and a value the vault holds is masked again wherever it
 * reappears in later text, also where the model would miss it there (spans with source VAULT): at word edges, 3 characters
 * or more (2 in scripts written without spaces, and two Hangul syllables, after which only particles, 님, 씨 or the copula
 * may follow in the same word). It is found as it is written and also: with full-width ASCII (U+FF01 to U+FF5E) and
 * U+3000 read as ASCII; with Hangul written as conjoining jamo (NFD) read as syllables; in capitals (the full Unicode
 * uppercase, "Weiß" as "WEISS"; a Greek value also without its accents, as Greek writes capitals; a sharp s also as the
 * capital sharp s); and, for an e-mail address, a user name, a URL's scheme and host and a value of two or more words, in
 * any ASCII case. A one-word value in another case is another word ("Will" and "will"). The hit carries the known value,
 * so one value keeps one placeholder however it is written, and it takes in the marks, variation selectors and joiners
 * right after it, so that it never ends inside a grapheme cluster. Offsets are UTF-16 units, as in PiiSpan.
 */

/** One placeholder and the value behind it. */
public data class PiiVaultEntry(val tag: String, val label: String, val value: String)

/** Where a known value occurs: `[start, end)` in UTF-16 units. */
public data class PiiVaultHit(val start: Int, val end: Int, val label: String, val value: String)

/** Which placeholders a restore may put back: all, only some ([Tags]) or only some with fixed values ([Values]). */
public sealed class PiiVaultScope {
    public object All : PiiVaultScope()
    public data class Tags(val tags: Set<String>) : PiiVaultScope()

    /** A snapshot from [PiiVault.bindings]. */
    public data class Values(val values: Map<String, String>) : PiiVaultScope()
}

private const val MAX_FAMILY = 32
private const val MAX_TAG = MAX_FAMILY + 15
private const val MAX_NUMBER = 999_999_999_999L

/** The family that goes on when the numbers of [family] are used up (which takes an imported placeholder with the
 *  highest number): one more "_" at its end, "PERSON" → "PERSON_", so that its placeholders stay apart
 *  ("[PERSON__1]"). A family of full length gets its last other character turned into "_" instead, and one that is a
 *  letter and underscores only goes on with the next letter (after Z, with A). */
private fun nextFamily(family: String): String {
    if (family.length < MAX_FAMILY) return family + "_"
    val i = (family.length - 1 downTo 1).firstOrNull { family[it] != '_' }
    if (i != null) return family.substring(0, i) + "_" + family.substring(i + 1)
    return (if (family[0] < 'Z') family[0] + 1 else 'A').toString() + family.substring(1)
}

/** How many families are tried after the first: at most 31 steps to a letter and underscores, then the 25 other letters. */
private const val FAMILY_STEPS = 56
// Placeholder grammar, the same in every package: "[" + family + "_" + number + "]", where the family is an ASCII capital
// letter followed by capitals, digits and underscores (32 characters at most) and the number is 1 to 12 ASCII digits.
// Longer look-alikes are ordinary text: they are neither escaped nor restored.
private val TAG = Regex("""\[[A-Z][A-Z0-9_]{0,31}_[0-9]{1,12}]""")
private val NUMBER_TAIL = Regex("""(_[0-9]*)+$""")
private val ENTRY_TAG = Regex("""\[([A-Z][A-Z0-9_]*)_([0-9]+)]""")
private val TAG_PREFIX = Regex("""\[[A-Z0-9_]*$""")

/** The vault placeholders in [text], in order of appearance (duplicates kept). */
public fun vaultTags(text: String): List<String> = TAG.findAll(text).map { it.value }.toList()

/** [text] with every placeholder-like part that was already in it ("[PERSON_1]") turned into "［PERSON_1]". */
public fun escapeTags(text: String): String =
    if ('[' !in text) text else TAG.replace(text) { "［" + it.value.substring(1) }

// Scripts written without spaces between words (letters and marks of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese,
// Javanese, Han, Hiragana and Katakana), and Hangul: the same code point ranges as the npm and Python packages.
private val NO_SPACE = intArrayOf(
    0x0E01, 0x0E3A, 0x0E40, 0x0E4E, // Thai
    0x0E81, 0x0ECF, // Lao
    0x1000, 0x103F, 0x1050, 0x108F, 0x109A, 0x109D, 0xA9E0, 0xA9EF, 0xA9FA, 0xA9FE, 0xAA60, 0xAA7F, // Myanmar
    0x1780, 0x17D3, 0x17D7, 0x17D7, 0x17DC, 0x17DD, 0x19E0, 0x19FF, // Khmer
    0x1A20, 0x1A5E, 0x1A60, 0x1A7C, 0x1A7F, 0x1A7F, 0x1AA7, 0x1AA7, // Tai Tham
    0x1B00, 0x1B4C, // Balinese
    0xA980, 0xA9C0, 0xA9CF, 0xA9CF, // Javanese
    0x2E80, 0x2FDF, 0x3005, 0x3005, 0x3007, 0x3007, 0x3021, 0x3029, 0x3038, 0x303B, // Han radicals, marks
    0x3041, 0x3096, 0x309D, 0x309F, // Hiragana
    0x30A1, 0x30FA, 0x30FD, 0x30FF, 0x31F0, 0x31FF, 0xFF66, 0xFF6F, 0xFF71, 0xFF9D, // Katakana
    0x1B000, 0x1B16F, // kana supplement and extensions
    0x3400, 0x4DBF, 0x4E00, 0x9FFF, 0xF900, 0xFAFF, 0x20000, 0x3347F, // Han, to Extension J
)
private val HANGUL = intArrayOf(0x1100, 0x11FF, 0x3131, 0x318E, 0xA960, 0xA97F, 0xAC00, 0xD7A3, 0xD7B0, 0xD7FF, 0xFFA0, 0xFFDC)

private fun inRanges(r: IntArray, cp: Int): Boolean {
    var i = 0
    while (i < r.size) {
        if (cp >= r[i] && cp <= r[i + 1]) return true
        i += 2
    }
    return false
}

// The marks among them (vowel signs, tone marks and the like of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese and
// Javanese; Unicode general category M): a mark belongs to the letter before it, so the place before one is no word edge.
private val NO_SPACE_MARKS = intArrayOf(
    0x0E31, 0x0E31, 0x0E34, 0x0E3A, 0x0E47, 0x0E4E, // Thai
    0x0EB1, 0x0EB1, 0x0EB4, 0x0EBC, 0x0EC8, 0x0ECE, // Lao
    0x102B, 0x103E, 0x1056, 0x1059, 0x105E, 0x1060, 0x1062, 0x1064, 0x1067, 0x106D, 0x1071, 0x1074,
    0x1082, 0x108D, 0x108F, 0x108F, 0x109A, 0x109D, 0xA9E5, 0xA9E5, 0xAA7B, 0xAA7D, // Myanmar
    0x17B4, 0x17D3, 0x17DD, 0x17DD, // Khmer
    0x1A55, 0x1A5E, 0x1A60, 0x1A7C, 0x1A7F, 0x1A7F, // Tai Tham
    0x1B00, 0x1B04, 0x1B34, 0x1B44, // Balinese
    0xA980, 0xA983, 0xA9B3, 0xA9C0, // Javanese
)

private fun noSpace(cp: Int) = inRanges(NO_SPACE, cp)
private fun noSpaceMark(cp: Int) = inRanges(NO_SPACE_MARKS, cp)
private fun hangul(cp: Int) = inRanges(HANGUL, cp)

/** A number character (Unicode general category N). */
private fun isNumber(cp: Int): Boolean = cp >= 0 && when (Character.getType(cp).toByte()) {
    Character.DECIMAL_DIGIT_NUMBER, Character.LETTER_NUMBER, Character.OTHER_NUMBER -> true
    else -> false
}

/** Letters, numbers, marks and "_" (Unicode general categories L, N, M). */
private fun isWord(cp: Int): Boolean {
    if (cp == '_'.code) return true
    return when (Character.getType(cp).toByte()) {
        Character.UPPERCASE_LETTER, Character.LOWERCASE_LETTER, Character.TITLECASE_LETTER, Character.MODIFIER_LETTER,
        Character.OTHER_LETTER, Character.DECIMAL_DIGIT_NUMBER, Character.LETTER_NUMBER, Character.OTHER_NUMBER,
        Character.NON_SPACING_MARK, Character.COMBINING_SPACING_MARK, Character.ENCLOSING_MARK,
        -> true
        else -> false
    }
}

/** A precomposed Hangul syllable (U+AC00 to U+D7A3). */
private fun syllable(cp: Int) = cp in 0xAC00..0xD7A3

/** White space as Unicode has it (the same set in every package). */
private fun isSpace(cp: Int): Boolean = when (cp) {
    in 0x09..0x0D, 0x20, 0x85, 0xA0, 0x1680, in 0x2000..0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000 -> true
    else -> false
}

/** What a value of two Hangul syllables may have right after it in the same word, one piece after another to the end of
 *  the run of syllables: particles, the honorifics 님 and 씨, and the copula. Any other syllable makes a longer word
 *  ("지원금", "정수기"), and a two-syllable name is often a word too. The same list in every package. */
private val KO_TAIL = listOf(
    "가", "같이", "고", "과", "까지", "께", "께서", "나", "나마", "네", "는", "니까", "님", "다", "대로", "더러", "도", "든", "든지", "라고",
    "라는", "라도", "라서", "랑", "로", "로서", "로써", "를", "마다", "마저", "만", "만큼", "며", "밖에", "보다", "부터", "씨", "아", "야",
    "어", "었", "에", "에게", "에게서", "에서", "여", "였", "예요", "와", "요", "으로", "으로서", "으로써", "은", "을", "의", "이", "이면",
    "인", "인데", "입니다", "조차", "죠", "지", "지만", "처럼", "한테", "한테서",
)

/** The longest run of syllables [koTail] reads; a longer one is another word. */
private const val KO_TAIL_MAX = 8

/** Whether the Hangul syllables of [text] from [at] (right after a value of two syllables) are pieces of [KO_TAIL], one
 *  after another to the end of the run. */
private fun koTail(text: String, at: Int): Boolean {
    var end = at
    while (end < text.length && end - at <= KO_TAIL_MAX && syllable(text[end].code)) end++
    val run = text.substring(at, end)
    if (run.length > KO_TAIL_MAX) return false
    val ok = BooleanArray(run.length + 1) // ok[i]: run.substring(i) is pieces of the list
    ok[run.length] = true
    for (i in run.length - 1 downTo 0) ok[i] = KO_TAIL.any { p -> run.startsWith(p, i) && ok[i + p.length] }
    return ok[0]
}

/** What joins the character before it into one grapheme cluster, so that a hit takes it in ("渡辺" and a variation
 *  selector, "김민수" and a combining mark): marks (general category M), the zero width joiner and non-joiner,
 *  conjoining Hangul vowels and finals, the half-width katakana sound marks, emoji modifiers and tag characters. The same
 *  set in every package. */
private fun joins(cp: Int): Boolean = when (cp) {
    0x200C, 0x200D, in 0x1160..0x11FF, in 0xD7B0..0xD7FF, 0xFF9E, 0xFF9F, in 0x1F3FB..0x1F3FF, in 0xE0020..0xE007F -> true
    else -> when (Character.getType(cp).toByte()) {
        Character.NON_SPACING_MARK, Character.COMBINING_SPACING_MARK, Character.ENCLOSING_MARK -> true
        else -> false
    }
}

/** [end] moved past what joins the character before it (see [joins]). */
private fun widen(text: String, end: Int): Int {
    var i = end
    while (i < text.length && joins(text.codePointAt(i))) i += Character.charCount(text.codePointAt(i))
    return i
}

/** Greek capitals that carry an accent or a breathing, as ranges (first, last, the capital without it): the tonos
 *  capitals, the polytonic ones and those with varia or oxia (the dialytika is kept). From the Unicode decompositions
 *  (U+0300, U+0301, U+0313, U+0314, U+0342, U+0343 left out); the same table in every package. */
private val GREEK_BARE = intArrayOf(
    0x386, 0x386, 0x391, 0x388, 0x388, 0x395, 0x389, 0x389, 0x397, 0x38A, 0x38A, 0x399, 0x38C, 0x38C, 0x39F,
    0x38E, 0x38E, 0x3A5, 0x38F, 0x38F, 0x3A9, 0x3D3, 0x3D3, 0x3D2, 0x1F08, 0x1F0F, 0x391, 0x1F18, 0x1F1D, 0x395,
    0x1F28, 0x1F2F, 0x397, 0x1F38, 0x1F3F, 0x399, 0x1F48, 0x1F4D, 0x39F, 0x1F59, 0x1F59, 0x3A5, 0x1F5B, 0x1F5B, 0x3A5,
    0x1F5D, 0x1F5D, 0x3A5, 0x1F5F, 0x1F5F, 0x3A5, 0x1F68, 0x1F6F, 0x3A9, 0x1FBA, 0x1FBB, 0x391, 0x1FC8, 0x1FC9, 0x395,
    0x1FCA, 0x1FCB, 0x397, 0x1FDA, 0x1FDB, 0x399, 0x1FEA, 0x1FEB, 0x3A5, 0x1FEC, 0x1FEC, 0x3A1, 0x1FF8, 0x1FF9, 0x39F,
    0x1FFA, 0x1FFB, 0x3A9,
)

/** A Greek letter (or a Coptic one of the Greek block). */
private fun greek(cp: Int) = cp in 0x370..0x3FF || cp in 0x1F00..0x1FFF

/** [up] (a value in capitals) as Greek writes capitals: without accents and breathings on Greek letters, the dialytika
 *  kept ("ΝΊΚΟΣ" as "ΝΙΚΟΣ"); null when [up] has no Greek letter. */
private fun greekCapitals(up: String): String? {
    if (up.codePoints().noneMatch(::greek)) return null
    val out = ArrayList<Int>()
    var afterGreek = false // the last letter that is not a combining mark is Greek
    for (c in up.codePoints().toArray()) {
        var cp = c
        if (cp in 0x300..0x36F) {
            if (afterGreek) {
                if (cp == 0x300 || cp == 0x301 || cp == 0x313 || cp == 0x314 || cp == 0x342 || cp == 0x343) continue
                if (cp == 0x344) cp = 0x308 // (dialytika and tonos: the dialytika stays)
                val last = out.lastOrNull()
                if (cp == 0x308 && (last == 0x399 || last == 0x3A5)) {
                    out[out.size - 1] = if (last == 0x399) 0x3AA else 0x3AB
                    continue
                }
            }
            out.add(cp)
            continue
        }
        afterGreek = greek(cp)
        var bare = cp
        for (i in GREEK_BARE.indices step 3) if (cp in GREEK_BARE[i]..GREEK_BARE[i + 1]) bare = GREEK_BARE[i + 2]
        out.add(bare)
    }
    return String(out.toIntArray(), 0, out.size)
}

/** The spellings of a value in capitals that [PiiVault.find] also matches, each way a language writes them: its full
 *  Unicode uppercase ("WEISS" for "Weiß", "EMINE ŞAHIN"), for a value with "i" the Turkish and Azerbaijani capitals with
 *  "İ" ("EMİNE ŞAHİN"), for a Greek value the capitals without accents and breathings ("ΕΛΕΝΗ" for "Ελένη"), and for a
 *  value with a sharp s the capitals with the capital sharp s ("WEIẞ"). Spellings that are [text] itself, or that the
 *  caseless match covers, are left out. The same spellings as the other packages. */
private fun capitals(text: String, caseless: Int): List<String> {
    val out = ArrayList<String>()
    fun add(s: String) {
        if (s != text && s !in out) out.add(s)
    }
    val up = upper(text)
    if (!(caseless == text.length && up == asciiUpper(text))) add(up)
    greekCapitals(up)?.let(::add)
    if (text.indexOf('i') >= 0) add(upper(text.replace("i", String(Character.toChars(0x130)))))
    if (text.indexOf(0xDF.toChar()) >= 0) add(upper(text.replace(0xDF.toChar(), 0x1E9E.toChar())))
    return out
}

/** [s] in capitals ([String.uppercase]: the full Unicode uppercase, "WEISS" for "Weiß"), in pieces of at most 256 units
 *  cut between code points. The JVM copies the whole result again for each letter that becomes two (a sharp s, a
 *  ligature), which takes time that grows with the square of a long value; uppercase in the root locale maps each code
 *  point on its own, so the pieces give the same text. */
private fun upper(s: String): String {
    if (s.length <= 256) return s.uppercase()
    val out = StringBuilder(s.length + s.length / 4)
    var i = 0
    while (i < s.length) {
        var j = minOf(s.length, i + 256)
        if (j < s.length && Character.isLowSurrogate(s[j]) && Character.isHighSurrogate(s[j - 1])) j--
        out.append(s.substring(i, j).uppercase())
        i = j
    }
    return out.toString()
}

/** Long enough to be found again at word edges: 3 characters, or 2 when one is in a script written without spaces or
 *  both are Hangul syllables (Korean particles may follow them). */
private fun longEnough(v: String): Boolean {
    val n = v.codePointCount(0, v.length)
    return n >= 3 || (n == 2 && (v.codePoints().anyMatch(::noSpace) || v.codePoints().allMatch(::syllable)))
}

// What a span's edges can hold that is not part of the value (all in the basic plane, so one UTF-16 unit each): white
// space (Unicode White_Space); sentence punctuation (. , ; : ! ? and the ellipsis, ideographic and full-width forms);
// brackets and quotation marks, each with its partner. As in the npm and Python packages.
private val EDGE_SPACE = setOf(
    0x9, 0xA, 0xB, 0xC, 0xD, 0x20, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008,
    0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
)
private val EDGE_STOP = setOf(0x2E, 0x2C, 0x3B, 0x3A, 0x21, 0x3F, 0x2026, 0x3001, 0x3002, 0xFF01, 0xFF0C, 0xFF0E, 0xFF1A, 0xFF1B, 0xFF1F)
private val EDGE_BRACKETS = listOf(
    0x28 to 0x29, 0x5B to 0x5D, 0x7B to 0x7D, 0x3C to 0x3E, 0x201C to 0x201D, 0x2018 to 0x2019, 0xAB to 0xBB, 0x300C to 0x300D,
    0x300E to 0x300F, 0xFF08 to 0xFF09, 0xFF3B to 0xFF3D, 0xFF5B to 0xFF5D, 0x3008 to 0x3009, 0x300A to 0x300B, 0x3010 to 0x3011,
    0xFF1C to 0xFF1E, 0x3014 to 0x3015,
)
private val CLOSER = EDGE_BRACKETS.toMap() // an opening bracket: its closing one
private val OPENER = EDGE_BRACKETS.associate { it.second to it.first } // a closing bracket: its opening one
private val EDGE_QUOTE = setOf(0x22, 0x27, 0x60) // " ' and `, their own partners

/** Whether a date as ISO 8601 writes it, followed by "T" and a digit (the start of an ISO timestamp), starts at [at]. */
private fun isoStamp(text: String, at: Int): Boolean {
    if (at + 12 > text.length) return false
    val d = { i: Int -> text[at + i] in '0'..'9' }
    return (0 until 4).all(d) && text[at + 4] == '-' && d(5) && d(6) && text[at + 7] == '-' && d(8) && d(9) &&
        (text[at + 10] == 'T' || text[at + 10] == 't') && d(11)
}

/** [start, end) of [text], a span a detector found with [label], without what its edges hold that is not part of the
 *  value: a model can mark "Kim Minsu." at the end of a sentence, an address with the " (" after it, a name with the
 *  quotation marks around it or a bytes literal's "b'", and the vault would learn that as the value. At either edge
 *  white space goes. Then, except in a SECRET (a password can end with "!" or hold brackets): sentence punctuation (at
 *  the start not a ".", and at the end of an ORG not its ".": "Tiffany & Co."); a bracket whose partner is not in the
 *  span, a bracket that opens at the end or closes at the start; a straight quotation mark that is the only one of its
 *  kind left in the span; a pair of brackets, or the only two of one straight quotation mark, around all the rest; and
 *  the "b'" of a Python bytes literal at the start. A date of birth that goes on into the time of its ISO timestamp
 *  ("1990-01-02T00") is cut at the date, the edge where the vault finds a known date in a timestamp. A bracket whose
 *  partner is inside stays ("(02) 1234-5678"). null when nothing but white space and punctuation is left; the span
 *  without its white space when what is left is too short to be found again ([longEnough]). One pass over the span (the
 *  bracket partners in an array): time in proportion to its length. The same in every package. */
internal fun valueEdges(text: String, start: Int, end: Int, label: String = ""): Pair<Int, Int>? {
    // the partner of each bracket in the span (one pass: a stack for each kind of bracket), and how many of each straight
    // quotation mark the span holds, kept as its edges go
    val partner = IntArray(end - start) { -1 } // (at i - start; -1: none)
    val opened = HashMap<Int, ArrayDeque<Int>>()
    val quotes = HashMap<Int, Int>()
    for (i in start until end) {
        val c = text[i].code
        if (c in CLOSER) {
            opened.getOrPut(c) { ArrayDeque() }.addLast(i)
        } else if (c in OPENER) {
            opened[OPENER.getValue(c)]?.removeLastOrNull()?.let { j ->
                partner[i - start] = j
                partner[j - start] = i
            }
        } else if (c in EDGE_QUOTE) {
            quotes[c] = (quotes[c] ?: 0) + 1
        }
    }
    var a = start
    var b = end
    while (a < b && text[b - 1].code in EDGE_SPACE) b--
    while (a < b && text[a].code in EDGE_SPACE) a++
    val ws = a
    val we = b
    while (a < b && label != "SECRET") {
        val last = text[b - 1].code
        if (last in EDGE_SPACE || (last in EDGE_STOP && !(last == '.'.code && label == "ORG")) ||
            last in CLOSER || // a bracket that opens at the end
            (last in OPENER && partner[b - 1 - start] < a) || // one that closes what the span does not open
            (last in EDGE_QUOTE && quotes[last] == 1)
        ) {
            quotes.remove(last)
            b--
            continue
        }
        val first = text[a].code
        if (first in EDGE_SPACE || (first in EDGE_STOP && first != '.'.code) ||
            first in OPENER || // a bracket that closes at the start
            (first in CLOSER && partner[a - start] !in 0 until b) || // one that opens what the span does not close
            (first in EDGE_QUOTE && quotes[first] == 1)
        ) {
            quotes.remove(first)
            a++
            continue
        }
        // a pair around all the rest
        if (b - a >= 2 && ((first in CLOSER && partner[a - start] == b - 1) || (first in EDGE_QUOTE && last == first && quotes[first] == 2))) {
            quotes.remove(first)
            a++
            b--
            continue
        }
        // the "b'" of a Python bytes literal (not a "b" at the end of a word: "Bob'")
        if (first == 'b'.code && b - a >= 2 && text[a + 1].code in EDGE_QUOTE && (a == 0 || !isWord(text.codePointBefore(a)))) {
            val q = text[a + 1].code
            quotes[q]?.let { quotes[q] = it - 1 }
            a += 2
            continue
        }
        break
    }
    if (label == "DATE_OF_BIRTH" && b - a > 11 && isoStamp(text, a)) b = a + 10
    if (a >= b) return null
    if ((a == ws && b == we) || longEnough(text.substring(a, b))) return a to b
    return ws to we
}

/** [spans] of [text] with [valueEdges], for the vault to learn; a span with nothing left is left out. A block-list span
 *  (its entry is exact) and a known value's span stay as they are. */
internal fun valueSpans(text: String, spans: List<PiiSpan>): List<PiiSpan> = spans.mapNotNull { s ->
    if (s.source == PiiSource.BLOCK || s.source == PiiSource.VAULT || s.start < 0 || s.start >= s.end || s.end > text.length) {
        return@mapNotNull s
    }
    val (a, b) = valueEdges(text, s.start, s.end, s.label) ?: return@mapNotNull null
    if (a == s.start && b == s.end) s else s.copy(start = a, end = b)
}

/** Whether a UTF-16 unit changes in the vault's reading of a text (full-width ASCII, U+3000, a conjoining Hangul vowel
 *  or final). */
private fun readDifferently(c: Int): Boolean = c in 0xFF01..0xFF5E || c == 0x3000 || c in 0x1161..0x1175 || c in 0x11A8..0x11C2

/** The text as the vault reads it: full-width ASCII (U+FF01 to U+FF5E) and U+3000 as ASCII, and Hangul written as
 *  conjoining jamo (NFD, as macOS file names have it) composed into syllables, as NFC composes them. `map[i]` is where
 *  UTF-16 unit `i` of the reading starts in the text, and `map[length]` is the text's length; there is no map when the
 *  reading is the text. */
private class Reading(val text: String, private val map: IntArray?) {
    /** The place in the text of a place in the reading. */
    fun at(i: Int): Int = map?.get(i) ?: i
}

private fun read(text: String): Reading {
    if (text.none { readDifferently(it.code) }) return Reading(text, null)
    val out = StringBuilder(text.length)
    val map = IntArray(text.length + 1)
    var k = 0
    while (k < text.length) {
        val cp = text[k].code
        val n1 = if (k + 1 < text.length) text[k + 1].code else 0
        val n2 = if (k + 2 < text.length) text[k + 2].code else 0
        var r = cp
        var used = 1
        when {
            cp in 0xFF01..0xFF5E -> r = cp - 0xFEE0
            cp == 0x3000 -> r = 0x20
            // a leading consonant and a vowel, and a final consonant after them
            cp in 0x1100..0x1112 && n1 in 0x1161..0x1175 -> {
                val lv = 0xAC00 + ((cp - 0x1100) * 21 + (n1 - 0x1161)) * 28
                if (n2 in 0x11A8..0x11C2) {
                    r = lv + n2 - 0x11A7
                    used = 3
                } else {
                    r = lv
                    used = 2
                }
            }
            // a syllable without a final consonant, and a final consonant after it
            syllable(cp) && (cp - 0xAC00) % 28 == 0 && n1 in 0x11A8..0x11C2 -> {
                r = cp + n1 - 0x11A7
                used = 2
            }
        }
        map[out.length] = k
        out.append(r.toChar())
        k += used
    }
    map[out.length] = text.length
    return Reading(out.toString(), map.copyOf(out.length + 1))
}

private fun asciiLower(s: String): String {
    if (s.none { it in 'A'..'Z' }) return s
    val c = s.toCharArray()
    for (i in c.indices) if (c[i] in 'A'..'Z') c[i] = c[i] + 32
    return String(c)
}

private fun asciiUpper(s: String): String {
    if (s.none { it in 'a'..'z' }) return s
    val c = s.toCharArray()
    for (i in c.indices) if (c[i] in 'a'..'z') c[i] = c[i] - 32
    return String(c)
}

/** A value of two or more words: runs of word characters with white space between them. */
private fun words(v: String): Boolean {
    var pieces = 0
    var word = false
    var i = 0
    while (i < v.length) {
        val cp = v.codePointAt(i)
        if (isSpace(cp)) {
            if (word) pieces++
            word = false
        } else if (isWord(cp)) {
            word = true
        }
        i += Character.charCount(cp)
    }
    if (word) pieces++
    return pieces >= 2
}

/** How many UTF-16 units of [v] (from its start) are matched in any ASCII case: all of an e-mail address, a user name and
 *  a value of two or more words; a URL's scheme and host; nothing of other values. */
private fun caselessPrefix(label: String, v: String): Int {
    if (label == "EMAIL" || label == "USERNAME" || words(v)) return v.length
    if (label != "URL") return 0
    fun alpha(c: Char) = c in 'a'..'z' || c in 'A'..'Z'
    var host = 0
    val i = v.indexOfFirst { !(alpha(it) || it in '0'..'9' || it == '+' || it == '.' || it == '-') }
    if (i > 0 && alpha(v[0]) && v.startsWith("://", i)) host = i + 3
    return (host until v.length).firstOrNull { v[it] == '/' || v[it] == '?' || v[it] == '#' } ?: v.length
}

/** A value of at most this many UTF-16 units is compared from its start at each place where it may start (at most 32 steps
 *  a place); a longer one is searched for in one pass ([occurrences]). The same in every package. */
private const val SHORT_NEEDLE = 32

/** Every place where [p] starts in [s], overlapping ones too ("Ann Ann" in "xAnn Ann Ann"), in time linear in both. A
 *  long value is searched for with Knuth-Morris-Pratt, so that one that repeats itself (". . . . Kim" in a text of
 *  ". . . . . . Kim", "ha ha ha" again and again) is not compared again from its start at every place. */
private fun occurrences(s: String, p: String): IntArray {
    val m = p.length
    if (m == 0 || m > s.length) return NONE
    if (m <= SHORT_NEEDLE) {
        // (a value that is not in the text costs one indexOf and nothing more, as before 1.7.2)
        var i = s.indexOf(p)
        if (i < 0) return NONE
        val out = IntList()
        while (i >= 0) {
            out.add(i)
            i = s.indexOf(p, i + 1)
        }
        return out.toArray()
    }
    val out = IntList()
    // fail[j]: the length of the longest proper prefix of p[0..j] that is also a suffix of it
    val fail = IntArray(m)
    var k = 0
    for (j in 1 until m) {
        while (k > 0 && p[j] != p[k]) k = fail[k - 1]
        if (p[j] == p[k]) k++
        fail[j] = k
    }
    k = 0
    for (i in 0 until s.length) {
        val c = s[i]
        while (k > 0 && c != p[k]) k = fail[k - 1]
        if (c == p[k]) k++
        if (k == m) {
            out.add(i + 1 - m)
            k = fail[m - 1]
        }
    }
    return out.toArray()
}

/** The places of [at] (sorted) where [p] also starts [offset] units later in [s]: one pass over both lists when [p] is
 *  long, so that checking the part of a value that must be written as it is costs no more than finding it. */
private fun followedBy(at: IntArray, s: String, offset: Int, p: String): IntArray {
    if (at.isEmpty()) return at
    if (p.length <= SHORT_NEEDLE) return at.filter { s.regionMatches(it + offset, p, 0, p.length) }.toIntArray()
    val starts = occurrences(s, p)
    val out = IntList()
    var j = 0
    for (a in at) {
        while (j < starts.size && starts[j] < a + offset) j++
        if (j < starts.size && starts[j] == a + offset) out.add(a)
    }
    return out.toArray()
}

/** No places. */
private val NONE = IntArray(0)

/** A growing list of ints (no boxing). */
private class IntList {
    private var items = IntArray(8)
    private var size = 0

    fun add(v: Int) {
        if (size == items.size) items = items.copyOf(size * 2)
        items[size++] = v
    }

    fun toArray(): IntArray = items.copyOf(size)
}

/** A known value as the vault searches for it: its first [entry], the value as the vault reads it ([text]), how many
 *  units of it match in any ASCII case ([caseless], with [lower] the text in ASCII lower case), and [uppers], the text in
 *  capitals, in each spelling that is another one and that the caseless match does not cover ([capitals]). */
private class Known(val entry: PiiVaultEntry) {
    val text: String = read(entry.value).text
    val caseless: Int = caselessPrefix(entry.label, text)
    val lower: String = if (caseless > 0) asciiLower(text) else ""
    val uppers: List<String> = capitals(text, caseless)
    val findable: Boolean = longEnough(text)

    /** Whether [s] (a reading) is this value in a spelling the vault matches. */
    fun matches(s: String): Boolean =
        s == text || s in uppers ||
            (caseless > 0 && s.length == text.length && asciiLower(s.substring(0, caseless)) == lower.substring(0, caseless) &&
                s.regionMatches(caseless, text, caseless, text.length - caseless))
}

/** A number as JSON writes one, with a sign in front or without ("4524", "-4.524e3", "+8210"; not "04524"): what an
 *  exponent can be. */
private val JSON_NUMBER = Regex("[+-]?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?").toPattern()

/** Word edges for `[start, end)` of [text]: a value is not found inside a longer word. A letter of a script written
 *  without spaces on either side of the boundary is an edge, but not a mark of such a script after a value that ends in
 *  one (a Thai vowel sign: "สม" is not found in "สมัคร"); where Hangul meets other letters or digits is an edge,
 *  Korean particles may follow a Hangul value, and Hangul right before a Hangul value is not an edge. After a value of two
 *  Hangul syllables, more syllables are an edge only as particles, 님, 씨 or the copula ([KO_TAIL]): "지원님께" and
 *  "지원이" are the name, "지원금" is another word. */
private fun atEdges(text: String, start: Int, end: Int): Boolean {
    val first = text.codePointAt(start)
    val last = text.codePointBefore(end)
    val okBefore = start == 0 || text.codePointBefore(start).let { b ->
        !isWord(b) || !isWord(first) || noSpace(first) || noSpace(b) || hangul(b) != hangul(first)
    }
    val okAfter = end >= text.length || text.codePointAt(end).let { a ->
        if (syllable(a) && end - start == 2 && syllable(first) && syllable(last)) {
            koTail(text, end)
        } else {
            // (a mark of a script written without spaces belongs to the letter before it: the word goes on)
            val markAfter = noSpace(last) && noSpaceMark(a)
            !isWord(a) || !isWord(last) || (!markAfter && (noSpace(last) || noSpace(a))) || hangul(a) || hangul(last)
        }
    }
    // a dot between two digits joins them ("4524" is not found in "4524.7" or in "1.4524"), and so does the sign of an
    // exponent before a number ("1e+4524"); a digit is any number character, as in a word. An exponent is a number: a
    // value that is not one (an address, a phone number with spaces, "04524") is not joined to "<digit>e+" before it
    val before = if (start > 0) text.codePointBefore(start) else -1
    val at2 = start - (if (before >= 0) Character.charCount(before) else 0)
    val second = if (before >= 0 && at2 > 0) text.codePointBefore(at2) else -1
    val at3 = at2 - (if (second >= 0) Character.charCount(second) else 0)
    val third = if (second >= 0 && at3 > 0) text.codePointBefore(at3) else -1
    // (whether the value is a number is asked last, of the text in place: reading it costs its length, at every place where
    // it is found)
    fun number() = JSON_NUMBER.matcher(text).region(start, end).matches()
    val exponent = (before == '+'.code || before == '-'.code) && (second == 'e'.code || second == 'E'.code) && isNumber(third) && number()
    // (the value may start with the sign itself: "-4524" in "1e-4524")
    val signed = (first == '+'.code || first == '-'.code) && end - start > 1 && isNumber(text.codePointAt(start + 1)) &&
        (before == 'e'.code || before == 'E'.code) && isNumber(second) && number()
    val joinedBefore = (isNumber(first) && ((before == '.'.code && isNumber(second)) || exponent)) || signed
    val joinedAfter = isNumber(last) && end + 1 < text.length && text[end] == '.' && isNumber(text.codePointAt(end + 1))
    return okBefore && okAfter && !joinedBefore && !joinedAfter
}

private fun family(label: String): String {
    var up = label.uppercase()
    if (up.endsWith("_L2")) up = up.dropLast(3)
    // one "_" per character (code point) that is not A-Z, 0-9 or "_", as in every package
    val cleaned = up.codePoints().toArray().joinToString("") { c ->
        if (c in 'A'.code..'Z'.code || c in '0'.code..'9'.code || c == '_'.code) c.toChar().toString() else "_"
    }.take(MAX_FAMILY)
    return cleaned.trimStart { it !in 'A'..'Z' }.ifEmpty { "PII" }
}

private fun jsonEscape(v: String): String = buildString {
    for (c in v) {
        when {
            c == '"' -> append("\\\"")
            c == '\\' -> append("\\\\")
            c == '\n' -> append("\\n")
            c == '\r' -> append("\\r")
            c == '\t' -> append("\\t")
            c.code < 0x20 -> append(String.format("\\u%04x", c.code))
            else -> append(c)
        }
    }
}

/**
 * Numbered placeholders that can be undone. Thread-safe. Pass [entries] (from [entries]) to continue a saved vault; they
 * hold the original values, so store them only where the original text may be stored.
 */
public class PiiVault @JvmOverloads constructor(entries: List<PiiVaultEntry> = emptyList()) {
    private val list = ArrayList<PiiVaultEntry>()
    private val byTag = HashMap<String, PiiVaultEntry>()
    private val byValue = HashMap<String, PiiVaultEntry>()
    // each value once, in the order it was added (its place is its age), and by the spellings `whole` takes
    private val known = ArrayList<Known>()
    private val byRead = HashMap<String, Int>()
    private val byUpper = HashMap<String, Int>()
    private val byLower = HashMap<String, MutableList<Int>>()
    private val counters = HashMap<String, Long>()
    // the values a model found that are shorter than the vault searches for ([tagFound]): held to be restored, not found
    // as a whole value elsewhere
    private val local = HashSet<String>()
    private val replays = LinkedHashMap<String, String>()
    private var gen = 0

    init {
        for (e in entries) add(e.copy(label = family(e.label)))
    }

    /** [e] held; with [isLocal], a new value is held to be restored only, not searched for ([tagFound]). */
    private fun add(e: PiiVaultEntry, isLocal: Boolean = false) {
        val m = ENTRY_TAG.matchEntire(e.tag)
        require(m != null && m.groupValues[1].length <= MAX_FAMILY && m.groupValues[2].length <= 12 && e.value.isNotEmpty()) {
            "not a vault entry: ${e.tag}"
        }
        require(e.tag !in byTag) { "duplicate vault tag ${e.tag}" }
        val fam = m.groupValues[1]
        counters[fam] = maxOf(counters[fam] ?: 0L, m.groupValues[2].toLong())
        list.add(e)
        byTag[e.tag] = e
        if (byValue.putIfAbsent(e.value, e) == null) {
            if (isLocal) local.add(e.value) else index(e)
        }
    }

    /** The value of [e] searched for: its spellings indexed. */
    private fun index(e: PiiVaultEntry) {
        val k = Known(e)
        val age = known.size
        byRead.putIfAbsent(k.text, age)
        for (u in k.uppers) byUpper.putIfAbsent(u, age)
        if (k.caseless > 0) byLower.getOrPut(k.lower) { ArrayList() }.add(age)
        known.add(k)
    }

    /** The known value that [value] is as a whole, in any spelling [find] matches (the one held longest), also with what
     *  joins its last character after it (a mark, a variation selector), as [find] takes that in. */
    private fun whole(value: String): PiiVaultEntry? {
        if (known.isEmpty() || value.isEmpty()) return null
        val r = read(value).text
        wholeRead(r)?.let { return it }
        var cut = r.length
        while (cut > 0 && joins(r.codePointBefore(cut))) cut -= Character.charCount(r.codePointBefore(cut))
        if (cut == 0 || cut == r.length || !atEdges(r, 0, cut)) return null
        return wholeRead(r.substring(0, cut))
    }

    /** [whole] for a reading, as it is. */
    private fun wholeRead(r: String): PiiVaultEntry? {
        var best = listOfNotNull(byRead[r], byUpper[r]).minOrNull()
        byLower[asciiLower(r)]?.firstOrNull { known[it].matches(r) }?.let { k -> best = minOf(best ?: k, k) }
        return best?.let { known[it].entry }
    }

    /** The placeholder for a value found in a text: that of the known value it is in another spelling, or its own. A new
     *  value shorter than the vault searches for at word edges (3 characters, 2 in scripts written without spaces and two
     *  Hangul syllables) is held to be restored, but not found as a whole value elsewhere ([hideValue]): a "0" that a
     *  model took for a date once must not turn every "0" into a placeholder. Values the application tags ([tag]) are
     *  found as a whole value whatever their length. */
    @Synchronized
    internal fun tagFound(label: String, value: String): String {
        whole(value)?.let { return it.tag }
        byValue[value]?.let { return it.tag } // (held already: one value, one placeholder)
        if (value.isEmpty() || longEnough(read(value).text)) return tag(label, value)
        return addNew(label, value, true)
    }

    /** Number of values in the vault. */
    public val size: Int @Synchronized get() = list.size

    /** Goes up by one on every [clear]. */
    public val generation: Int @Synchronized get() = gen

    /**
     * The placeholder for [value], adding it under [label] (its family) when new. The same value keeps its first tag. An
     * empty value has no placeholder (""). Numbers go up to 999,999,999,999; a family that has used them up (after an
     * imported entry with that number) goes on as "[PERSON__1]".
     */
    @Synchronized
    public fun tag(label: String, value: String): String {
        if (value.isEmpty()) return ""
        byValue[value]?.let {
            // (a short value a model found is found as a whole value from now on: the application gives it)
            if (local.remove(value)) index(it)
            return it.tag
        }
        return addNew(label, value, false)
    }

    /** A new entry for [value] under [label] (its family); [isLocal] as in [add]. */
    private fun addNew(label: String, value: String, isLocal: Boolean): String {
        val fam = family(label)
        // a family whose numbers are used up goes on in the next one, so a value always gets a placeholder that can be undone
        var numbered = fam
        var left = FAMILY_STEPS
        while (left-- > 0 && (counters[numbered] ?: 0L) >= MAX_NUMBER) numbered = nextFamily(numbered)
        // every family on the way used up (some 10^13 values, or a state made by hand): masked, not undone ("PERSON_1"
        // gives "[PERSON]", never a numbered placeholder)
        if ((counters[numbered] ?: 0L) >= MAX_NUMBER) return "[${NUMBER_TAIL.replace(fam, "")}]"
        val e = PiiVaultEntry("[${numbered}_${(counters[numbered] ?: 0L) + 1}]", fam, value)
        add(e, isLocal)
        return e.tag
    }

    /** The value behind a placeholder. */
    @Synchronized
    public fun value(tag: String): String? = byTag[tag]?.value

    /** The vault's placeholders in [text] replaced with their values (limited by [scope]); with [json] the values are
     *  escaped for the inside of a JSON string. Other bracketed text is left as it is. */
    @JvmOverloads
    @Synchronized
    public fun restore(text: String, scope: PiiVaultScope = PiiVaultScope.All, json: Boolean = false): String =
        TAG.replace(text) { m ->
            val v = when (scope) {
                PiiVaultScope.All -> byTag[m.value]?.value
                is PiiVaultScope.Tags -> if (m.value in scope.tags) byTag[m.value]?.value else null
                is PiiVaultScope.Values -> scope.values[m.value]
            }
            when {
                v == null -> m.value
                json -> jsonEscape(v)
                else -> v
            }
        }

    /** [restore] applied to every string inside a JSON-like value (maps and lists are copied, keys are kept). One step
     *  for the vault: every string gets the values of the same moment. */
    @JvmOverloads
    @Synchronized
    public fun restoreValue(value: Any?, scope: PiiVaultScope = PiiVaultScope.All): Any? = mapStrings(value) { restore(it, scope) }

    /** A restorer for a streamed text. It uses the values the vault holds when it is created, for the whole stream. */
    @JvmOverloads
    @Synchronized
    public fun restorer(scope: PiiVaultScope = PiiVaultScope.All, json: Boolean = false): PiiVaultRestorer {
        val fixed = when (scope) {
            is PiiVaultScope.Values -> scope
            PiiVaultScope.All -> PiiVaultScope.Values(list.associate { it.tag to it.value })
            is PiiVaultScope.Tags -> PiiVaultScope.Values(list.filter { it.tag in scope.tags }.associate { it.tag to it.value })
        }
        return PiiVaultRestorer(this, fixed, json)
    }

    /** The current values of these placeholders, as a snapshot for [PiiVaultScope.Values]. */
    @Synchronized
    public fun bindings(tags: Iterable<String>): Map<String, String> = tags.mapNotNull { t -> byTag[t]?.let { t to it.value } }.toMap()

    /** Where the known values occur in [text] (sorted, non-overlapping; overlapping occurrences are merged into one hit
     *  whose value is the merged text and whose label is the first one's). A hit carries the known value, also where it
     *  is written another way (see the notes at the top); where two values are found on the same part of the text, the
     *  one the vault has held longer. */
    @Synchronized
    public fun find(text: String): List<PiiVaultHit> {
        if (known.isEmpty()) return emptyList()
        val reading = read(text)
        val r = reading.text
        var lower: String? = null
        // start and end in the reading, and the value's age
        val found = ArrayList<IntArray>()
        for ((age, k) in known.withIndex()) {
            if (!k.findable) continue
            // (a hit takes in what joins its last character, so that it never ends inside a grapheme cluster)
            fun take(at: Int, end: Int) {
                if (atEdges(r, at, end)) found.add(intArrayOf(at, widen(r, end), age))
            }
            if (k.caseless > 0) {
                // the caseless part in any ASCII case, the rest as it is written
                val low = lower ?: asciiLower(r).also { lower = it }
                for (i in followedBy(occurrences(low, k.lower), r, k.caseless, k.text.substring(k.caseless))) take(i, i + k.lower.length)
            } else {
                for (i in occurrences(r, k.text)) take(i, i + k.text.length)
            }
            for (u in k.uppers) {
                for (i in occurrences(r, u)) take(i, i + u.length)
            }
        }
        found.sortWith(compareBy<IntArray> { it[0] }.thenByDescending { it[1] }.thenBy { it[2] })
        // overlapping known values are merged ("Alex Kim" + "Kim Lee"); the value of a merged hit is the text it covers
        val merged = ArrayList<IntArray>() // start, end, age, 1 when merged
        for (h in found) {
            val l = merged.lastOrNull()
            if (l != null && h[0] < l[1]) {
                if (h[1] > l[1]) {
                    l[1] = h[1]
                    l[3] = 1
                }
            } else {
                merged.add(intArrayOf(h[0], h[1], h[2], 0))
            }
        }
        return merged.map { m ->
            val start = reading.at(m[0])
            val end = reading.at(m[1])
            val e = known[m[2]].entry
            PiiVaultHit(start, end, e.label, if (m[3] == 1) text.substring(start, end) else e.value)
        }
    }

    /** The vault's known values in [text] replaced with their placeholders (no model call). Overlapping known values are
     *  hidden together under a new placeholder. With [escape], placeholder-like text in the rest is escaped. */
    @JvmOverloads
    @Synchronized
    public fun hide(text: String, escape: Boolean = false): String {
        val esc: (String) -> String = if (escape) ::escapeTags else { s -> s }
        val out = StringBuilder()
        var last = 0
        for (h in find(text)) {
            out.append(esc(text.substring(last, h.start)))
            out.append(byValue[h.value]?.tag ?: tag(h.label, h.value))
            last = h.end
        }
        return out.append(esc(text.substring(last))).toString()
    }

    /** [hide] applied to every string inside a JSON-like value; a string that is as a whole a known value (in any
     *  spelling [find] matches) is replaced whatever its length. */
    @Synchronized // one step for the vault: every string is numbered in the same generation
    public fun hideValue(value: Any?): Any? = mapStrings(value) { s -> whole(s)?.tag ?: hide(s) }

    /** Remembers what the model wrote ([masked]) for a text that was restored (at most 4,096). */
    @Synchronized
    public fun remember(key: String, masked: String) {
        replays.remove(key)
        replays[key] = masked
        while (replays.size > 4096) replays.remove(replays.keys.first())
    }

    /** What [remember] stored for [key]. */
    @Synchronized
    public fun recall(key: String): String? = replays[key]

    /** The entries in the order they were added. */
    public val entries: List<PiiVaultEntry> @Synchronized get() = list.toList()

    @Synchronized
    public fun clear() {
        list.clear(); byTag.clear(); byValue.clear(); counters.clear(); local.clear(); replays.clear()
        known.clear(); byRead.clear(); byUpper.clear(); byLower.clear()
        gen++
    }

    /**
     * [spans] replaced by the vault's placeholders, as one step for the vault (a [clear] from another thread cannot fall
     * between two placeholders). The spans first lose what their edges hold that is not part of the value ("Kim
     * Minsu." at the end of a sentence is the value "Kim Minsu"). With [known], the vault's known values are added, and
     * every value this text adds to the vault is then masked wherever it occurs again in it ("I met Alice. Say Alice
     * twice." when the model finds only the first "Alice"). With [generation], nothing is written and
     * IllegalStateException is thrown if the vault was cleared since.
     */
    @JvmOverloads
    @Synchronized
    public fun redact(text: String, spans: List<PiiSpan>, known: Boolean = true, generation: Int? = null): PiiRedaction {
        check(generation == null || generation == gen) { "the vault was cleared while this text was masked" }
        val found = valueSpans(text, spans)
        if (!known) return PiiRedaction(applyVaultRedaction(text, found, this), found)
        var all = withVaultSpans(text, found)
        var out = applyVaultRedaction(text, all, this) // the vault learns this text's values here
        // also what the model found inside a larger merged span ("Kim Lee" of "Alex Kim Lee"), so it is masked where it stands alone
        for (s in found) {
            val value = text.substring(s.start, s.end)
            if (value.isNotBlank()) tagFound(s.label, value)
        }
        // until nothing new is found: every round either masks more of the text or merges spans, so this ends
        while (true) {
            val next = withVaultSpans(text, all)
            if (next.map { it.start to it.end } == all.map { it.start to it.end }) return PiiRedaction(out, all)
            all = next
            out = applyVaultRedaction(text, all, this)
        }
    }

    /** [spans] (detected in [text]) plus the vault's known values (source VAULT). A known value that spans cover only in
     *  part is masked whole: the overlapping spans are merged into one span over both. One pass over the hits and the
     *  spans (sorted by their start first if they are not). A span over known values has their family as its label, a
     *  custom one too ("CUSTOMER"), and the label of a span the model found when it was merged with one. */
    @Synchronized
    public fun withVaultSpans(text: String, spans: List<PiiSpan>): List<PiiSpan> {
        val hits = find(text) // in order, apart
        if (hits.isEmpty()) return spans
        val sorted = if ((1 until spans.size).all { spans[it - 1].start <= spans[it].start }) spans else spans.sortedBy { it.start }
        val out = ArrayList<PiiSpan>(sorted.size + hits.size)
        var next = 0
        for (h in hits) {
            while (next < sorted.size && sorted[next].end <= h.start) out.add(sorted[next++])
            val over = ArrayList<PiiSpan>()
            if (out.isNotEmpty() && out[out.size - 1].end > h.start) over.add(out.removeAt(out.size - 1)) // one that reaches this far (merged before)
            while (next < sorted.size && sorted[next].start < h.end) over.add(sorted[next++])
            if (over.isNotEmpty() && over.all { it.start <= h.start && it.end >= h.end }) {
                out.addAll(over) // already masked whole
                continue
            }
            val start = minOf(h.start, over.minOfOrNull { it.start } ?: h.start)
            val end = maxOf(h.end, over.maxOfOrNull { it.end } ?: h.end)
            val label = if ((start == h.start && end == h.end) || over.isEmpty()) h.label else over[0].label
            out.add(PiiSpan(start, end, label, PiiSource.VAULT))
        }
        while (next < sorted.size) out.add(sorted[next++])
        return out
    }
}

private fun mapStrings(value: Any?, f: (String) -> String): Any? = when (value) {
    is String -> f(value)
    is List<*> -> value.map { mapStrings(it, f) }
    is Map<*, *> -> value.entries.associate { (k, v) -> k to mapStrings(v, f) }
    else -> value
}

/** Undoes masking in text that arrives in pieces (a streamed answer). A placeholder split across pieces is held back
 *  until it is complete, so [push] may return less than it was given; [flush] returns what is left. */
public class PiiVaultRestorer internal constructor(private val vault: PiiVault, private val scope: PiiVaultScope, private val json: Boolean) {
    private val held = StringBuilder()

    public fun push(chunk: String): String {
        held.append(chunk)
        val tail = held.substring(maxOf(0, held.length - MAX_TAG))
        val m = TAG_PREFIX.find(tail)
        val cut = if (m != null) held.length - m.value.length else held.length
        val out = vault.restore(held.substring(0, cut), scope, json)
        held.delete(0, cut)
        return out
    }

    public fun flush(): String {
        val out = vault.restore(held.toString(), scope, json)
        held.setLength(0)
        return out
    }
}

/** [spans] replaced by [vault]'s numbered placeholders; placeholder-like text left unmasked is escaped ("［PERSON_1]"). */
internal fun applyVaultRedaction(text: String, spans: List<PiiSpan>, vault: PiiVault): String {
    val out = StringBuilder(text.length)
    var last = 0
    for (s in spans) {
        out.append(escapeTags(text.substring(last, s.start)))
        // (a known value written another way keeps its placeholder)
        out.append(vault.tagFound(s.label, text.substring(s.start, s.end)))
        last = s.end
    }
    return out.append(escapeTags(text.substring(last))).toString()
}
