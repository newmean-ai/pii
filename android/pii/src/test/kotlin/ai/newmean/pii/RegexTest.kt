package ai.newmean.pii

import org.junit.Assert.assertEquals
import org.junit.Test

class RegexTest {
    @Test
    fun parityWithPythonBaseline() {
        val rows = TestEnv.jsonl(TestEnv.fixture("regex.jsonl"))
        val bad = ArrayList<String>()
        for (row in rows) {
            val text = row.getString("text")
            val want = TestEnv.pySpans(text, row.getJSONArray("spans")).sorted()
            val got = TestEnv.key(PiiRegex.spans(text)).sorted()
            if (want != got) bad.add("${text.take(100)}\n  py $want\n  kt $got")
        }
        println("regex parity: ${rows.size - bad.size}/${rows.size}\n${bad.take(5).joinToString("\n")}")
        assertEquals(0, bad.size)
    }

    @Test
    fun commonFormats() {
        // the account-number rule (FINANCIAL) runs before PHONE and also takes 010-1234-5678; next to the model, the
        // model's label wins for such overlaps
        val t = "메일 kim.minjun@example.co.kr 전화 010-1234-5678 카드 4111 1111 1111 1111 주민 900101-1234567"
        assertEquals(
            listOf(
                "kim.minjun@example.co.kr" to "EMAIL",
                "900101-1234567" to "ID_NUMBER",
                "010-1234-5678" to "FINANCIAL",
                "4111 1111 1111 1111" to "FINANCIAL",
            ),
            PiiRegex.spans(t).map { t.substring(it.start, it.end) to it.label },
        )
    }

    @Test
    fun sameAsTheNpmPackageOnEdgeCases() {
        // expected values printed by js/src/regex.ts (node): \S follows JavaScript (U+3000 and U+FEFF are whitespace,
        // U+0085 is not), \d is any Unicode Nd, and offsets are UTF-16 units (also after letters outside the BMP)
        val cases = listOf(
            "see https://example.com/a\u3000b" to listOf(Triple(4, 25, "URL")),
            "www.example.com\u0085x\uFEFFy" to listOf(Triple(0, 17, "URL")),
            "رقم ٩٠٠١٠١-١٢٣٤٥٦٧" to listOf(Triple(6, 18, "PHONE")),
            "카드 ٤١١١ ١١١١ ١١١١ ١١١١" to listOf(Triple(3, 22, "FINANCIAL")),
            "a900101-1234567" to listOf(Triple(3, 15, "PHONE")),
            "\uD835\uDC00900101-1234567" to listOf(Triple(4, 16, "PHONE")),
            "😀 kim@example.com" to listOf(Triple(3, 18, "EMAIL")),
        )
        for ((text, want) in cases) assertEquals(text, want, PiiRegex.spans(text).map { Triple(it.start, it.end, it.label) })
    }

    /**
     * The matcher of 1.6.0 and earlier, kept as the reference: PiiRegex.spans must give its spans, in its order. It tried
     * the EMAIL pattern again from every character of a run without "@" and compared each match with all earlier spans, so
     * its time grew with the square of the input (fixed in 1.6.1).
     */
    private fun legacy(text: String): List<Triple<Int, Int, String>> {
        val out = ArrayList<Triple<Int, Int, String>>()
        for ((label, re) in PiiRegex.RULES) {
            for (m in re.findAll(text)) {
                val start = m.range.first
                val end = m.range.last + 1
                if (out.any { start < it.second && it.first < end }) continue
                out.add(Triple(start, end, label))
            }
        }
        return out
    }

    private fun current(text: String) = PiiRegex.spans(text).map { Triple(it.start, it.end, it.label) }

    @Test
    fun equalsTheLegacyMatcherExhaustively() {
        // every string of up to 7 characters over a 6-character alphabet
        val alphabet = listOf("a", "@", ".", "-", "+", " ")
        var texts = listOf("")
        var checked = 0
        val bad = ArrayList<String>()
        for (length in 0..7) {
            for (t in texts) if (current(t) != legacy(t)) bad.add(t)
            checked += texts.size
            if (length < 7) texts = texts.flatMap { t -> alphabet.map { t + it } }
        }
        assertEquals(emptyList<String>(), bad.take(5))
        assertEquals(335923, checked)
    }

    @Test
    fun equalsTheLegacyMatcherOnRandomUnicode() {
        val alphabet = listOf(
            "a", "Z", "7", "@", "@", ".", ".", "+", "-", "_", " ", "\n", "\u00a0", "한", "é", "\uD801\uDC00", "\uD835\uDFD9", "①", "\u0301",
            "\uD83D\uDE00", "\uD800", "\uDC00", ":", "/", "(", ")", "w", "0", "1", "http://", "www.", "\u000B", "\u0085", "\uFEFF",
        )
        var state = 20261117
        fun random(limit: Int): Int {
            state = state * 1664525 + 1013904223
            return (((state.toLong() and 0xFFFFFFFFL) * limit) ushr 32).toInt()
        }
        val bad = ArrayList<String>()
        repeat(40000) {
            val sb = StringBuilder()
            repeat(random(60)) { sb.append(alphabet[random(alphabet.size)]) }
            val t = sb.toString()
            if (current(t) != legacy(t)) bad.add(t)
        }
        assertEquals(emptyList<String>(), bad.take(5))
    }

    @Test
    fun equalsTheLegacyMatcherOnEdgeCases() {
        val cases = listOf(
            "a@b.co@d.e", "a@b.co-+x@c.de", "a@b..co", "a@@b.co", "a@b.co.", ".a+@b.co", "a@b", "@b.co", "a@.co", "a@b.c@d.e.f",
            "😀 김+\uD801\uDC00@회사.한국 \uD835\uDFD9@\uD801\uDC00.한", "e\u0301@a.co ①@b.co a@b.①", "a\uD800@b.co a@b.\uDC00",
            "www.site.test/x@y.co 010-1234-5678 https://other.test", "a@b.co http://one.test a@b.co",
            "900101-1234567 4111 1111 1111 1111 +1 (212) 555-1234", "https://a@b.co/x?mail=c@d.co",
        )
        for (t in cases) assertEquals(t, legacy(t), current(t))
    }

    @Test
    fun longInputsTakeTimeInProportionToTheirLength() {
        skipTiming()
        // 1.6.0 needed many seconds for the first text
        val start = System.nanoTime()
        for (t in listOf("a".repeat(100000), "a@" + "b".repeat(100000), "@".repeat(100000), "a.".repeat(50000), "1".repeat(100000))) {
            assertEquals(0, PiiRegex.spans(t).count { it.label == "EMAIL" })
        }
        val spans = PiiRegex.spans("a@b.co ".repeat(40000))
        assertEquals(40000, spans.size)
        assertEquals(Triple(0, 6, "EMAIL"), Triple(spans[0].start, spans[0].end, spans[0].label))
        assertEquals(Triple(279993, 279999, "EMAIL"), Triple(spans[39999].start, spans[39999].end, spans[39999].label))
        assert((System.nanoTime() - start) / 1e9 < 10.0)
    }
}
