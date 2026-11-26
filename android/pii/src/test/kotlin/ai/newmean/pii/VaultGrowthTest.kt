package ai.newmean.pii

import org.junit.Assert.assertEquals
import org.junit.Test

// Doubling tests of the vault (REVIEW-GUIDE rule 4), kept apart from VaultTest so that the test runner runs them alone,
// after the suites ("Growth" in the class name), where no other suite distorts the ratios. Timing: Growth.kt.
class VaultGrowthTest {
    /** [s] in full-width ASCII (U+FF01 to U+FF5E). */
    private fun fullWidth(s: String): String = buildString { for (c in s) append(if (c.code in 0x21..0x7E) (c.code + 0xFEE0).toChar() else c) }

    private fun model(start: Int, end: Int) = PiiSpan(start, end, "PERSON", PiiSource.MODEL)

    @Test
    fun doublingWithVaultSpansAndRedact() {
        skipTiming()
        // n known values found again, each also found in part by the model, and n other spans: one pass over both
        val unit = "Ask Kim Minsu about Lee Jun. "
        fun input(n: Int): Pair<String, List<PiiSpan>> =
            unit.repeat(n) to (0 until n).flatMap { i -> listOf(model(i * unit.length + 4, i * unit.length + 7), model(i * unit.length + 20, i * unit.length + 27)) }
        val v = PiiVault()
        v.tag("PERSON", "Kim Minsu")
        assertLinear("withVaultSpans", listOf(128_000, 256_000, 512_000)) { n ->
            val (text, spans) = input(n)
            medianTime { assertEquals(2 * n, v.withVaultSpans(text, spans).size) }
        }
        assertLinear("redact", listOf(16_000, 32_000, 64_000)) { n ->
            val (text, spans) = input(n)
            medianTime { assertEquals(2 * n, PiiVault(v.entries).redact(text, spans).spans.size) }
        }
        // one span a model found with n brackets before its value and n sentence marks and opening brackets after it: each
        // end is cut once (1.7.1 review, L3-B1)
        assertLinear("redact, a span's ends", listOf(512_000, 1_024_000, 2_048_000)) { n ->
            val text = "(".repeat(n) + "Kim Minsu" + ".,".repeat(n) + " (".repeat(n)
            medianTime { assertEquals(listOf("Kim Minsu"), PiiVault().redact(text, listOf(model(0, text.length))).spans.map { text.substring(it.start, it.end) }) }
        }
        // the same with the text in full-width letters (a reading with a map back to the text)
        val wide = fullWidth(unit)
        assertLinear("withVaultSpans, full-width", listOf(128_000, 256_000, 512_000)) { n ->
            val text = wide.repeat(n)
            medianTime { assertEquals(n, v.withVaultSpans(text, emptyList()).size) }
        }
    }

    /** [cps] as a string of code points. */
    private fun cps(vararg cps: Int): String = String(cps, 0, cps.size)

    @Test
    fun doublingCapitalsHangulAndClusters() {
        skipTiming()
        // capitals in other spellings (full Unicode uppercase, Greek without accents, the capital sharp s), a value of two
        // Hangul syllables with particles and inside longer words, values followed by a variation selector and a combining
        // mark, and custom values that merge: seven hits a line
        val jiwon = cps(0xC9C0, 0xC6D0)
        val watanabe = cps(0x6E21, 0x8FBA)
        val kim = cps(0xAE40, 0xBBFC, 0xC218)
        val line = jiwon + cps(0xB2D8, 0xAED8, 0xC11C) + " " + jiwon + cps(0xAE08, 0xC744) + " " + jiwon + cps(0xD588, 0xB2E4) + "; " +
            cps(0x39D, 0x399, 0x39A, 0x39F, 0x3A3) + " " + cps(0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A3) + ", " +
            "ANNA WEISS and ANNA WEI" + cps(0x1E9E) + "; " + watanabe + cps(0xE0100, 0x3055, 0x3093) + ", " + kim + cps(0x301, 0xB2D8) + ", AB-1234-CD. "
        val known = PiiVault()
        known.tag("PERSON", jiwon)
        known.tag("PERSON", cps(0x39D, 0x3AF, 0x3BA, 0x3BF, 0x3C2) + " " + cps(0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3CC, 0x3C0, 0x3BF, 0x3C5, 0x3BB, 0x3BF, 0x3C2))
        known.tag("PERSON", "Anna Wei" + cps(0xDF))
        known.tag("PERSON", watanabe)
        known.tag("PERSON", kim)
        known.tag("CUSTOMER", "AB-1234")
        known.tag("CUSTOMER", "1234-CD")
        assertLinear("redact, capitals, Hangul and clusters", listOf(4_000, 8_000, 16_000)) { n ->
            val text = line.repeat(n)
            medianTime { assertEquals(7 * n, PiiVault(known.entries).redact(text, emptyList()).spans.size) }
        }
        // a known value followed by a run of n combining marks (taken in whole), and a value of two syllables followed by n
        // syllables (another word)
        assertLinear("withVaultSpans, a run of marks", listOf(3_200_000, 6_400_000, 12_800_000)) { n ->
            val text = watanabe + cps(0x301).repeat(n) + cps(0x3055, 0x3093)
            medianTime { assertEquals(text.length - 2, known.withVaultSpans(text, emptyList())[0].end) }
        }
        assertLinear("withVaultSpans, a long word after two syllables", listOf(160_000, 320_000, 640_000)) { n ->
            val text = jiwon.repeat(n) + " " + (jiwon + cps(0xAE08)).repeat(n)
            medianTime { assertEquals(0, known.withVaultSpans(text, emptyList()).size) }
        }
    }

    @Test
    fun doublingLongValuesThatRepeatThemselves() {
        skipTiming()
        // a known value of n words that repeats itself, in a text that repeats it: each place where it may start is looked at
        // once, in every spelling the vault searches (1.7.1 gate 7: Swift, Kotlin and Dart compared it from its start at
        // every place). The vault learns the value (its spellings) and finds it; one hit each: the places overlap and merge.
        fun words(w: List<String>, n: Int): String = (0 until n).joinToString(" ") { w[it % w.size] }
        // a value learned from a model's span over the whole text (". . . .Kim Minsu"), found once after long partial matches
        assertLinear("a long value from a span, found once", listOf(320_000, 640_000, 1_280_000)) { n ->
            val text = " .".repeat(n) + "Kim Minsu" + ", ".repeat(n)
            val want = " [PERSON_1]" + ", ".repeat(n)
            medianTime { assertEquals(want, PiiVault().redact(text, listOf(model(0, text.length))).text) }
        }
        // (name, label, the value and the text of size n, the sizes)
        data class Shape(val name: String, val label: String, val value: (Int) -> String, val text: (Int) -> String, val sizes: List<Int>)
        val shapes = listOf(
            Shape("as it is written", "PERSON", { n -> words(listOf("ha"), n) }, { n -> "ha ".repeat(2 * n) }, listOf(256_000, 512_000, 1_024_000)),
            Shape("in any ASCII case", "PERSON", { n -> words(listOf("Ha", "ha"), n) }, { n -> "hA Ha ".repeat(n) }, listOf(256_000, 512_000, 1_024_000)),
            Shape("a URL's path as it is written", "URL", { n -> "https://x.io/".repeat(n) }, { n -> "HTTPS://X.IO/" + "https://x.io/".repeat(2 * n - 1) },
                listOf(64_000, 128_000, 256_000)),
            Shape("in capitals of another spelling", "PERSON", { n -> words(listOf("Wei" + cps(0xDF)), n) }, { n -> "WEISS ".repeat(2 * n) },
                listOf(128_000, 256_000, 512_000)),
        )
        for ((name, label, value, text, sizes) in shapes) {
            assertLinear("a long value that overlaps itself, $name", sizes) { n ->
                val (known, t) = value(n) to text(n)
                medianTime {
                    val v = PiiVault()
                    v.tag(label, known)
                    assertEquals(1, v.find(t).size)
                }
            }
        }
    }
}
