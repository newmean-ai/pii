package ai.newmean.pii

import org.junit.Assert.assertTrue
import org.junit.Test

// Doubling test for the edges of a span the model found (review round of SDK 1.7.1, L2-B2), apart from ReviewDocsTest so
// that the test runner runs it alone ("Growth" in the class name): leaving out what a span's edges hold that is not part
// of the value takes time in proportion to the span, however many brackets, quotation marks, stops or spaces it has to
// look past. Timing: Growth.kt.
class ReviewDocsGrowthTest {
    @Test
    fun doublingSpanEdges() {
        skipTiming()
        val quote = 0x22.toChar().toString()
        val shapes = listOf<Pair<String, (Int) -> String>>(
            "closing brackets after the value" to { n -> "Kim Minsu" + ")".repeat(n) },
            "opening brackets before the value" to { n -> "(".repeat(n) + "Kim Minsu" },
            "pairs of brackets around the value" to { n -> "(".repeat(n) + "Kim Minsu" + ")".repeat(n) },
            "quotation marks around the value" to { n -> quote.repeat(n) + "Kim Minsu" + "'".repeat(n) },
            // (commas before the value: a "." at the start of a value stays)
            "stops and spaces around the value" to { n -> " ,".repeat(n) + "Kim Minsu" + ", ".repeat(n) },
            "brackets that are not one pair" to { n -> "(" + ")(".repeat(n) + "Kim Minsu" + ")(".repeat(n) + ")" },
        )
        for ((name, make) in shapes) {
            assertLinear("span edges, $name", listOf(800_000, 1_600_000, 3_200_000)) { n ->
                val text = make(n)
                val found = listOf(PiiSpan(0, text.length, "PERSON", PiiSource.MODEL))
                medianTime { assertTrue(PiiVault().redact(text, found).text.contains("[PERSON_1]")) }
            }
        }
    }
}
