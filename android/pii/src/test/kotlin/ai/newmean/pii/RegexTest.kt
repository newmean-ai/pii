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
}
