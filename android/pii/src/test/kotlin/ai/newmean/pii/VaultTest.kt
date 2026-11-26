package ai.newmean.pii

import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test

// The vault (since 1.7.2): the cases every package shares (python/tests/vault_cases.json) and the Kotlin API.
class VaultTest {
    private fun toKotlin(x: Any?): Any? = when (x) {
        is JSONObject -> x.keys().asSequence().associateWith { toKotlin(x.get(it)) }
        is JSONArray -> (0 until x.length()).map { toKotlin(x.get(it)) }
        else -> x
    }

    @Test
    fun sharedCases() {
        val file = File(System.getProperty("pii.jsModels") ?: "").parentFile?.parentFile?.parentFile?.let { File(it, "python/tests/vault_cases.json") }
        assumeTrue("no shared vault cases", file != null && file.isFile)
        val groups = JSONObject(file!!.readText()).getJSONArray("groups")
        for (gi in 0 until groups.length()) {
            val g = groups.getJSONObject(gi)
            val name = g.getString("name")
            val v = PiiVault()
            val values = g.getJSONArray("values")
            val tags = (0 until values.length()).map { values.getJSONArray(it).let { p -> v.tag(p.getString(0), p.getString(1)) } }
            assertEquals(name, toKotlin(g.getJSONArray("tags")), tags)
            g.optJSONArray("redact")?.let { a ->
                for (i in 0 until a.length()) a.getJSONObject(i).let { c ->
                    // spans a model found (then the vault's known values): each over the next place its value is written
                    val text = c.getString("text")
                    var at = 0
                    val found = c.getJSONArray("found")
                    val spans = (0 until found.length()).map { k ->
                        val f = found.getJSONArray(k)
                        val start = text.indexOf(f.getString(0), at)
                        at = start + f.getString(0).length
                        PiiSpan(start, at, f.getString(1), PiiSource.MODEL)
                    }
                    val got = v.redact(text, spans)
                    assertEquals(name, c.getString("want"), got.text)
                    // the final spans as covered text, label and source, when the case gives them
                    c.optJSONArray("spans")?.let { want ->
                        assertEquals(name, toKotlin(want), got.spans.map { listOf(text.substring(it.start, it.end), it.label, it.source.id) })
                    }
                }
            }
            g.optJSONArray("entries")?.let { assertEquals(name, toKotlin(it), v.entries.map { e -> e.value }) }
            g.optJSONArray("hide")?.let { a -> for (i in 0 until a.length()) a.getJSONObject(i).let { assertEquals(name, it.getString("want"), v.hide(it.getString("text"))) } }
            g.optJSONArray("hide_escaped")?.let { a -> for (i in 0 until a.length()) a.getJSONObject(i).let { assertEquals(name, it.getString("want"), v.hide(it.getString("text"), escape = true)) } }
            g.optJSONArray("hide_value")?.let { a ->
                for (i in 0 until a.length()) a.getJSONObject(i).let { assertEquals(name, toKotlin(it.get("want")), v.hideValue(toKotlin(it.get("value")))) }
            }
            g.optJSONArray("restore")?.let { a ->
                for (i in 0 until a.length()) a.getJSONObject(i).let { c ->
                    val scope = c.optJSONArray("only")?.let { o -> PiiVaultScope.Tags((0 until o.length()).map { o.getString(it) }.toSet()) } ?: PiiVaultScope.All
                    assertEquals(name, c.getString("want"), v.restore(c.getString("text"), scope, c.optBoolean("json")))
                }
            }
        }
    }

    @Test
    fun modelSpansLoseWhatTheirBoundaryTookAndShortValuesStayWhereFound() {
        // (1.7.1 review, L3-B1, L3-B2 and L3-B4 in this package; the shared cases have the rest)
        val v = PiiVault()
        assertEquals("page [DATE_OF_BIRTH_1] of 7", v.redact("page 0 of 7", listOf(PiiSpan(5, 6, "DATE_OF_BIRTH", PiiSource.MODEL))).text)
        assertEquals("0", v.hideValue("0"))
        // a value the application gives is found as a whole value whatever its length
        assertEquals("[DATE_OF_BIRTH_1]", v.tag("DATE_OF_BIRTH", "0"))
        assertEquals("[DATE_OF_BIRTH_1]", v.hideValue("0"))
        val u = String(Character.toChars(0x1F600)) + " (Kim Minsu)."
        val r = v.redact(u, listOf(PiiSpan(3, u.length, "PERSON", PiiSource.MODEL)))
        assertEquals(String(Character.toChars(0x1F600)) + " ([PERSON_1]).", r.text)
        assertEquals(listOf(4 to 13), r.spans.map { it.start to it.end })
    }

    @Test
    fun streamSplitAnywhere() {
        val v = PiiVault()
        v.tag("PERSON", "Kim Minsu")
        v.tag("EMAIL", "kim@example.com")
        val text = "Dear [PERSON_1], we wrote to [EMAIL_1]. [x] ["
        for (size in listOf(1, 2, 3, 5, 7, 100)) {
            val r = v.restorer()
            val out = text.chunked(size).joinToString("") { r.push(it) } + r.flush()
            assertEquals("Dear Kim Minsu, we wrote to kim@example.com. [x] [", out)
        }
    }

    @Test
    fun snapshotsEntriesGenerationReplay() {
        val v = PiiVault()
        v.tag("PERSON", "Alice")
        val snap = v.bindings(listOf("[PERSON_1]", "[PERSON_9]"))
        assertEquals(mapOf("[PERSON_1]" to "Alice"), snap)
        v.remember("xid: 42y", "x[ID_NUMBER_1]y")
        assertEquals("x[ID_NUMBER_1]y", v.recall("xid: 42y"))
        val saved = v.entries
        val g = v.generation
        v.clear()
        assertEquals(0, v.size)
        assertEquals(g + 1, v.generation)
        assertNull(v.recall("xid: 42y"))
        v.tag("PERSON", "Bob")
        assertEquals("Alice", v.restore("[PERSON_1]", PiiVaultScope.Values(snap)))
        assertEquals("Alice", PiiVault(saved).restore("[PERSON_1]"))
        assertThrows(IllegalArgumentException::class.java) { PiiVault(listOf(PiiVaultEntry("PERSON_1", "PERSON", "x"))) }
        assertEquals("a ［PERSON_1] [note] [x_1]", escapeTags("a [PERSON_1] [note] [x_1]"))
        assertEquals(listOf("[A_1]", "[B_22]"), vaultTags("[A_1] x [B_22] [c_3]"))
        assertEquals("[${"X".repeat(32)}_1]", v.tag("X".repeat(50), "value one"))
    }

    @Test
    fun spansMergeAndEscapedGaps() {
        val v = PiiVault()
        v.tag("PERSON", "Kim Minsu")
        val t = "Ask Kim Minsu and Kim Minsuk."
        val spans = v.withVaultSpans(t, listOf(PiiSpan(4, 7, "PERSON", PiiSource.MODEL)))
        assertEquals(listOf(PiiSpan(4, 13, "PERSON", PiiSource.VAULT)), spans)
        assertEquals("Ask [PERSON_1] and Kim Minsuk.", PiiRedactor.applyRedaction(t, spans, v))
        val u = "account [REF_1] cobalt, see [REF_1]"
        assertEquals("[SECRET_1], see ［REF_1]", PiiRedactor.applyRedaction(u, listOf(PiiSpan(0, 22, "SECRET", PiiSource.BLOCK)), v))
        assertEquals("account [REF_1] cobalt", v.value("[SECRET_1]"))
    }

    @Test
    fun fifthReviewFixes() {
        // a value the model found once is masked wherever it occurs again in the same text, on the first call
        val v = PiiVault()
        val t = "I met Alice yesterday. Use the word Alice twice, not Alicette."
        val got = v.redact(t, listOf(PiiSpan(6, 11, "PERSON", PiiSource.MODEL)))
        assertEquals("I met [PERSON_1] yesterday. Use the word [PERSON_1] twice, not Alicette.", got.text)
        assertEquals(listOf(PiiSpan(6, 11, "PERSON", PiiSource.MODEL), PiiSpan(36, 41, "PERSON", PiiSource.VAULT)), got.spans)
        // what the model found inside a larger merged span is masked where it stands alone
        val w = PiiVault()
        w.tag("PERSON", "Alex Kim")
        val u = "Alex Kim Lee wrote. Kim Lee signed. Alex Kim Lee left."
        val merged = w.redact(u, listOf(PiiSpan(5, 12, "PERSON", PiiSource.MODEL))).text
        assertTrue(merged, !merged.contains("Alex") && !merged.contains("Kim") && !merged.contains("Lee"))
        assertEquals(u, w.restore(merged))
        // a family with digits in its name stays apart from the numbers of another
        val x = PiiVault(listOf(PiiVaultEntry("[PERSON_1]", "PERSON", "Alice"), PiiVaultEntry("[PERSON_1_99999999999]", "PERSON_1", "Last")))
        assertEquals("[PERSON_1_100000000000]", x.tag("PERSON_1", "Next"))
        assertEquals("Alice Next", x.restore("[PERSON_1] [PERSON_1_100000000000]"))
        // masking goes on until nothing new is found, however many rounds that takes
        val c = PiiVault()
        c.tag("PERSON", "W00")
        val words = (0 until 10).map { "W%02d".format(it) }
        var line = ""
        for (n in 2..words.size) {
            line = words.take(n).joinToString(" ")
            assertTrue(!c.redact(line, listOf(PiiSpan(line.length - 5, line.length, "PERSON", PiiSource.MODEL))).text.contains("W0"))
        }
        val again = c.redact(line, listOf(PiiSpan(line.length - 5, line.length, "PERSON", PiiSource.MODEL))).text
        assertTrue(again, !again.contains("W0"))
        assertEquals(line, c.restore(again))
    }

    @Test
    fun fourthReviewFixes() {
        // an empty value has no placeholder; a family whose numbers are used up goes on in the next one; nothing overflows
        val x = PiiVault(listOf(PiiVaultEntry("[PERSON_999999999999]", "PERSON", "Last One"), PiiVaultEntry("[ORG_2147483647]", "ORG", "Big")))
        assertEquals("", x.tag("PERSON", ""))
        assertEquals("[PERSON__1]", x.tag("PERSON", "One More"))
        assertEquals("[PERSON__2]", x.tag("PERSON", "And Another"))
        assertEquals("[ORG_2147483648]", x.tag("ORG", "Next"))
        assertEquals("One More Last One", x.restore("[PERSON__1] [PERSON_999999999999]"))
        assertEquals("[PERSON__1] and [PERSON_999999999999]", x.hide("One More and Last One"))
        assertEquals("[PERSON__3]", PiiVault(x.entries).tag("PERSON", "A Fourth")) // its own entries load again
        val full = "P".repeat(32)
        assertEquals("[${"P".repeat(31)}__1]", PiiVault(listOf(PiiVaultEntry("[${full}_999999999999]", full, "x"))).tag(full, "y"))
        val last = "A" + "_".repeat(31) // a letter and underscores only: on to the next letter
        val z = PiiVault(listOf(PiiVaultEntry("[${last}_999999999999]", last, "one")))
        assertEquals("[B${"_".repeat(31)}_1]", z.tag(last, "two"))
        assertEquals("two", z.restore("[B${"_".repeat(31)}_1]"))
        // after Z it goes on with A; with all 26 used up the value is masked and not put back
        val letterFamily = { c: Char -> c + "_".repeat(31) }
        val wrapped = PiiVault(listOf(PiiVaultEntry("[${letterFamily('Z')}_999999999999]", letterFamily('Z'), "one")))
        assertEquals("[${letterFamily('A')}_1]", wrapped.tag(letterFamily('Z'), "two"))
        assertEquals("two", wrapped.restore("[${letterFamily('A')}_1]"))
        val usedUp = PiiVault(('A'..'Z').map { PiiVaultEntry("[${letterFamily(it)}_999999999999]", letterFamily(it), "v$it") })
        assertEquals("[Q]", usedUp.tag(letterFamily('Q'), "more"))
        assertEquals("[Q]", usedUp.restore("[Q]"))
        assertThrows(IllegalArgumentException::class.java) { PiiVault(listOf(PiiVaultEntry("[PERSON_1000000000000]", "PERSON", "x"))) }
        // spans, known values and placeholders are one step: nothing is written into a vault that was cleared since
        val w = PiiVault()
        val spans = listOf(PiiSpan(0, 5, "PERSON", PiiSource.MODEL), PiiSpan(6, 9, "PERSON", PiiSource.MODEL))
        val started = w.generation
        w.clear()
        assertThrows(IllegalStateException::class.java) { w.redact("Alice Bob", spans, generation = started) }
        assertEquals(0, w.size)
        assertEquals("[PERSON_1] [PERSON_2]", w.redact("Alice Bob", spans, generation = w.generation).text)
        // overlapping occurrences are all tried
        val v = PiiVault()
        v.tag("PERSON", "Ann Ann")
        assertEquals("xAnn [PERSON_1]", v.hide("xAnn Ann Ann"))
    }

    /** [s] in full-width ASCII (U+FF01 to U+FF5E). */
    private fun fullWidth(s: String): String = buildString { for (c in s) append(if (c.code in 0x21..0x7E) (c.code + 0xFEE0).toChar() else c) }

    /** Text from code points (conjoining Hangul jamo and the like, kept out of the source as characters). */
    private fun points(vararg cps: Int): String = buildString { for (c in cps) appendCodePoint(c) }

    private fun model(start: Int, end: Int) = PiiSpan(start, end, "PERSON", PiiSource.MODEL)

    @Test
    fun spellingsKeepOnePlaceholder() {
        // what the model finds in another spelling of a known value gets that value's placeholder; the vault learns no new value
        val v = PiiVault()
        v.tag("PERSON", "Kim Minsu")
        val t = "KIM MINSU wrote to Kim Minsu."
        assertEquals("[PERSON_1] wrote to [PERSON_1].", v.redact(t, listOf(model(0, 9))).text)
        assertEquals(1, v.size)
        assertEquals("[PERSON_1] wrote to Kim Minsu.", v.redact(t, listOf(model(0, 9)), known = false).text)
        // a one-word value in another case is another value
        assertEquals("Will [PERSON_2]", v.redact("Will will", listOf(model(5, 9))).text)
        assertEquals(2, v.size)
        // spans of values found in a reading point into the text as it is written: full-width letters and NFD Hangul
        val w = PiiVault()
        w.tag("EMAIL", "kim@example.com")
        w.tag("PERSON", points(0xAE40, 0xBBFC, 0xC218))
        val nfdName = points(0x1100, 0x1175, 0x11B7, 0x1106, 0x1175, 0x11AB, 0x1109, 0x116E)
        val nfdParticle = points(0x1102, 0x1175, 0x11B7)
        val x = fullWidth("kim@example.com") + " and " + nfdName + nfdParticle
        val spans = w.withVaultSpans(x, emptyList())
        assertEquals(listOf(PiiSpan(0, 15, "EMAIL", PiiSource.VAULT), PiiSpan(20, 28, "PERSON", PiiSource.VAULT)), spans)
        assertEquals("[EMAIL_1] and [PERSON_1]$nfdParticle", w.redact(x, spans, known = false).text)
        assertEquals(2, w.size)
        // hideValue: a whole string in another spelling, whatever its length
        val y = PiiVault()
        y.tag("PERSON", "Al")
        assertEquals(listOf("[PERSON_1]", "al", "[PERSON_1]"), y.hideValue(listOf("AL", "al", fullWidth("Al"))))
    }

    @Test
    fun redactWithNano() {
        val r = TestEnv.redactor(PiiSize.NANO)
        r.use {
            val text = "Please call Kim Minsu on 010-1234-5678 or write to kim@example.com."
            val v = PiiVault()
            val numbered = it.redact(text, v)
            assertEquals(it.redact(text).spans, numbered.spans)
            assertTrue(numbered.text.contains("[EMAIL_1]"))
            assertEquals(text, v.restore(numbered.text))
            assertEquals("[EMAIL_1] again", it.redact("kim@example.com again", v).text)
        }
    }
}
