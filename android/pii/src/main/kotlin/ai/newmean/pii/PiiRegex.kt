package ai.newmean.pii

// Deterministic patterns that run next to the model when regex = true. Port of js/src/regex.ts (same patterns as the
// Python baseline, with Unicode \w, \d and \b); \s and \S are spelled out as JavaScript's whitespace set so that Java and
// Android (ICU) regex match exactly what the npm package matches.

/** Pattern layer (email, URL, card/account, phone, Korean resident number). Off by default, as in the npm package. */
public object PiiRegex {
    // U+1D400 (a letter, so the set is unchanged) is written out because java.util.regex steps back over surrogate pairs
    // in lookbehind only when the pattern itself contains a supplementary character; without it "𝐀900101-1234567"
    // would pass the \b check that JavaScript (u flag) and Python fail.
    private const val W = "[\\p{L}\\p{N}_\uD835\uDC00]"
    private const val D = "\\p{Nd}"
    private const val B0 = "(?<!$W)" // \b before a digit
    private const val B1 = "(?!$W)" // \b after a digit
    private const val WS = "\\t\\n\\x0B\\f\\r \\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000\\uFEFF"
    private const val NS = "[^$WS]" // \S

    /** Label and pattern, in the order they run. */
    @JvmField
    public val RULES: List<Pair<String, Regex>> = listOf(
        PiiLabels.EMAIL to Regex("[\\p{L}\\p{N}_.+\\-]+@[\\p{L}\\p{N}_\\-]+(?:\\.[\\p{L}\\p{N}_\\-]+)+"),
        PiiLabels.URL to Regex("https?://$NS+|www\\.$NS+"),
        PiiLabels.ID_NUMBER to Regex("$B0$D{6}[\\-$WS]?[1-8]$D{6}$B1"),
        PiiLabels.FINANCIAL to Regex("$B0(?:$D{4}[\\-$WS]?){3}$D{4}$B1|$B0$D{2,6}\\-$D{2,6}\\-$D{2,8}(?:\\-$D{1,3})?$B1"),
        PiiLabels.PHONE to Regex("(?:\\+$D{1,3}[$WS\\-]?)?\\(?$D{2,4}\\)?[$WS.\\-]?$D{3,4}[$WS.\\-]?$D{4}$B1"),
    )

    /** Pattern matches in UTF-16 units; a match that overlaps an earlier rule's match is skipped. */
    @JvmStatic
    public fun spans(text: String): List<PiiSpan> {
        val out = ArrayList<PiiSpan>()
        for ((label, re) in RULES) {
            for (m in re.findAll(text)) {
                val start = m.range.first
                val end = m.range.last + 1
                if (out.any { start < it.end && it.start < end }) continue
                out.add(PiiSpan(start, end, label, PiiSource.REGEX))
            }
        }
        return out
    }
}
