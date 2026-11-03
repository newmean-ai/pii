package ai.newmean.pii

import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

// User lists (allow, block, listsId; 1.4.0). The validation cases are those of js/test/lists.test.ts: same list, index and
// reason as the npm package and the core. Matching itself is tested in native/core/tests and js/test.
class ListsTest {
    private fun allow(vararg texts: String) = texts.map { PiiAllowEntry(it) }
    private fun person(vararg texts: String) = texts.map { PiiBlockEntry(it, PiiLabels.PERSON) }

    /** list, index and reason of the PiiListException thrown by [f]; its message never holds an entry's text. */
    private fun listError(f: () -> Unit): Triple<String, Int?, String> {
        try {
            f()
        } catch (e: PiiListException) {
            return Triple(e.list, e.index, e.reason.id).also { assertTrue(e.message!!, e.message!!.endsWith(e.reason.id)) }
        }
        fail("no PiiListException")
        throw AssertionError()
    }

    private fun prepare(a: List<PiiAllowEntry> = emptyList(), b: List<PiiBlockEntry> = emptyList(), id: String? = null) =
        PreparedLists.of(a, b, id)

    @Test
    fun validationReasonsInOrder() {
        // a Java caller can put null or another type into the list
        @Suppress("UNCHECKED_CAST")
        val nulls = listOf(PiiAllowEntry("ok"), null) as List<PiiAllowEntry>
        assertEquals(Triple("allow", 1, "bad_shape"), listError { prepare(nulls) })
        @Suppress("UNCHECKED_CAST")
        val wrongType = listOf<Any>(PiiAllowEntry("Kim")) as List<PiiBlockEntry>
        assertEquals(Triple("block", 0, "bad_shape"), listError { prepare(b = wrongType) })
        assertEquals(Triple("allow", 0, "unknown_label"), listError { prepare(listOf(PiiAllowEntry("x", "NAME"))) })
        assertEquals(Triple("block", 0, "unknown_label"), listError { prepare(b = listOf(PiiBlockEntry("x", "PERSON_L3"))) })
        assertEquals("too_long", listError { prepare(allow("a".repeat(257))) }.third)
        assertEquals("too_long", listError { prepare(allow("\u0000" + "a".repeat(256))) }.third) // the length bound comes first
        assertEquals("too_long", listError { prepare(allow("a".repeat(65))) }.third)
        assertEquals("too_long", listError { prepare(allow("😀".repeat(65))) }.third) // 130 units, 65 code points
        prepare(allow("a".repeat(64), "😀".repeat(64)))
        for (bad in listOf("\uD800", "x\uDC00", "\uDC00\uD800", "a\nb", "a\rb", "\u0085", "a\u2028b", "a\u2029", "a\u0000b")) {
            assertEquals(bad, "bad_text", listError { prepare(allow(bad)) }.third)
        }
        assertEquals("empty", listError { prepare(allow("")) }.third)
        for (bad in listOf("x\u3000", " x", "\tx", "x\u00A0", "\u1680x", "x\u2000", "\u200Ax", "x\u202F", "\u205Fx", "\u000Bx")) {
            assertEquals(bad, "edge_space", listError { prepare(allow(bad)) }.third)
        }
        prepare(allow("\u200Bx", "x\uFEFF", "a b")) // U+200B and U+FEFF are not White_Space; inner spaces are fine
        // text checks before the label; each entry checked completely before the next; allow before block
        assertEquals(Triple("allow", 0, "empty"), listError { prepare(listOf(PiiAllowEntry("", "NAME"))) })
        assertEquals(Triple("allow", 1, "edge_space"), listError { prepare(allow("ok", " x"), listOf(PiiBlockEntry("", "PERSON"))) })
        assertEquals(Triple("block", 2, "bad_text"), listError { prepare(allow("ok"), person("Kim", "Lee", "\uD83D")) })
        val e = try {
            prepare(allow("비밀이름 "))
            null
        } catch (x: PiiListException) {
            x
        }
        assertEquals("allow list entry 0: edge_space", e!!.message)
    }

    @Test
    fun tooManyAfterTheFirst10000() {
        val many = (0..10_000).map { "v$it" }
        assertEquals(Triple("allow", 10_000, "too_many"), listError { prepare(allow(*many.toTypedArray())) })
        assertEquals(Triple("block", 10_000, "too_many"), listError { prepare(b = person(*many.toTypedArray())) })
        val badAt50 = many.take(50) + " x" + many.drop(50)
        assertEquals(Triple("allow", 50, "edge_space"), listError { prepare(allow(*badAt50.toTypedArray())) })
        prepare(allow(*many.take(10_000).toTypedArray()))
    }

    @Test
    fun listsIdFirst() {
        assertNull(prepare().id)
        assertEquals("survey-2026-11 v3", prepare(id = "survey-2026-11 v3").id)
        prepare(id = "x".repeat(128))
        prepare(id = "😀".repeat(128)) // 128 code points
        for (bad in listOf("", "a\nb", "\r", "\u0000", "\uDC00", "x\uD800", "x".repeat(129), "a\u2028")) {
            assertEquals(bad, Triple("meta", null, "bad_lists_id"), listError { prepare(id = bad) })
        }
        // listsId is checked before the lists
        assertEquals(Triple("meta", null, "bad_lists_id"), listError { prepare(allow(""), id = "") })
        assertEquals(PiiListReason.entries.map { it.id }, listOf(
            "bad_shape", "too_long", "bad_text", "empty", "edge_space", "unknown_label", "too_many", "label_conflict", "in_both", "bad_lists_id",
        ))
    }

    @Test
    fun conflictsAndCountsFromTheCore() {
        TestEnv.requireNative()
        fun create(a: List<PiiAllowEntry>, b: List<PiiBlockEntry>) = prepare(a, b).create().also { it.free() }.info
        assertEquals(
            Triple("block", 1, "label_conflict"),
            listError { create(allow("seoul"), listOf(PiiBlockEntry("Kim", "PERSON"), PiiBlockEntry("KIM", "ORG"), PiiBlockEntry("SEOUL", "ORG"))) },
        )
        assertEquals(
            Triple("block", 1, "in_both"),
            listError { create(listOf(PiiAllowEntry("Seoul"), PiiAllowEntry("Busan", "ORG")), listOf(PiiBlockEntry("x", "ORG"), PiiBlockEntry("BUSAN", "ORG"))) },
        )
        val merged = create(
            listOf(PiiAllowEntry("Seoul", "ADDRESS"), PiiAllowEntry("SEOUL", "ORG"), PiiAllowEntry("Busan")),
            person("Kim", "KIM"),
        )
        assertEquals(PiiListsInfo(2, 1, null), merged)
        assertEquals(PiiListsInfo(10_000, 0, null), create(allow(*(0 until 10_000).map { "v$it" }.toTypedArray()), emptyList()))
        // no entries: no handle
        val none = prepare(id = "v1").create()
        assertEquals(0L, none.handle)
        assertEquals(PiiListsInfo(0, 0, "v1"), none.info)
    }

    @Test
    fun validatedBeforeAnyModelFileIsRead() {
        TestEnv.requireNative()
        val missing = File("does-not-exist/model_q4.onnx")
        val tok = File("does-not-exist/tokenizer.web.json")
        assertEquals(
            Triple("block", 0, "edge_space"),
            listError { PiiRedactor.createBlocking(missing, tok, PiiSize.NANO, block = person(" x")) },
        )
        assertEquals(Triple("meta", null, "bad_lists_id"), listError { PiiRedactor.createBlocking(missing, tok, PiiSize.NANO, listsId = "") })
        assertEquals(Triple("allow", 0, "bad_text"), listError { PiiRedactor.fromBytes(ByteArray(0), ByteArray(0), PiiSize.NANO, allow = allow("\uD800")) })
        assertEquals(
            Triple("block", 0, "in_both"),
            listError { PiiRedactor.createBlocking(missing, tok, PiiSize.NANO, allow = allow("박지훈"), block = person("박지훈")) },
        )
    }

    @Test
    fun blockMasksWhateverLabelsSay() {
        val text = "담당: 박지훈님께 문의"
        TestEnv.redactor(PiiSize.NANO, labels = emptyList(), block = person("박지훈")).use { r ->
            assertEquals(listOf(PiiSpan(4, 7, "PERSON", PiiSource.BLOCK)), r.detect(text))
            assertEquals("담당: [PERSON]님께 문의", r.redact(text).text)
            assertEquals(listOf(PiiSpan(4, 7, "PERSON", PiiSource.BLOCK)), r.detectTable(text)) // not a table: detect
            assertEquals(emptyList<PiiSpan>(), r.detect("김박지훈 박지 박지훈석")) // inside a word; a rest that is not particles
            assertEquals(listOf(PiiSpan(0, 3, "PERSON", PiiSource.BLOCK)), r.detect("박지훈A")) // a word ends where Hangul meets Latin
        }
        TestEnv.redactor(PiiSize.NANO, labels = emptyList(), block = listOf(PiiBlockEntry("Kim", PiiLabels.ORG))).use { r ->
            val t = "KIM, Kimberly, kim's"
            assertEquals(listOf(PiiSpan(0, 3, "ORG", PiiSource.BLOCK)), r.detect(t)) // ASCII case folded; whole words only
        }
        TestEnv.redactor(PiiSize.NANO, block = person("박지훈")).use { r ->
            val spans = r.detect(text)
            assertTrue(spans.toString(), spans.any { it.source == PiiSource.BLOCK && it.label == "PERSON" && it.start <= 4 && it.end >= 7 })
            // NFC only: an NFD text does not match the NFC entry
            val nfd = java.text.Normalizer.normalize("박지훈", java.text.Normalizer.Form.NFD)
            TestEnv.redactor(PiiSize.NANO, labels = emptyList(), block = person("박지훈")).use { assertEquals(emptyList<PiiSpan>(), it.detect(nfd)) }
        }
    }

    @Test
    fun allowRemovesWholeSpans() {
        val text = "Write to kim.minjun@example.com or call 010-1234-5678."
        TestEnv.redactor(PiiSize.NANO).use { plain ->
            val base = plain.detect(text)
            assertTrue(base.isNotEmpty())
            val first = base.first()
            val value = text.substring(first.start, first.end)
            val upper = value.map { if (it in 'a'..'z') it.uppercaseChar() else it }.joinToString("")
            TestEnv.redactor(PiiSize.NANO, allow = allow(upper)).use { r ->
                assertEquals(base.drop(1), r.detect(text)) // ASCII letters compared case-insensitively
            }
            val other = PiiLabels.ALL.first { PiiLabels.family(it) != PiiLabels.family(first.label) }
            TestEnv.redactor(PiiSize.NANO, allow = listOf(PiiAllowEntry(value, other))).use { r ->
                assertEquals(base, r.detect(text)) // the entry allows another label family only
            }
            TestEnv.redactor(PiiSize.NANO, allow = listOf(PiiAllowEntry(value, first.label))).use { r -> assertEquals(base.drop(1), r.detect(text)) }
            // a span holding a block match is never allowed
            TestEnv.redactor(PiiSize.NANO, allow = allow(value), block = person("Write")).use { r ->
                val spans = r.detect(text)
                assertEquals(PiiSpan(0, 5, "PERSON", PiiSource.BLOCK), spans.first())
                assertFalse(spans.any { it.start == first.start })
            }
        }
    }

    @Test
    fun tableModeBlocksInsideCells() {
        val csv = "name,city\n박지훈,제주\n김민수,서울\n"
        val start = csv.indexOf("박지훈")
        TestEnv.redactor(PiiSize.NANO, labels = emptyList(), block = person("박지훈")).use { r ->
            assertEquals(listOf(PiiSpan(start, start + 3, "PERSON", PiiSource.BLOCK)), r.detectTable(csv))
            assertEquals("name,city\n[PERSON],제주\n김민수,서울\n", r.redactTable(csv).text)
            assertEquals(r.detectTable(csv), r.detectTable(csv, TableOptions(context = TableContext.HEADER)))
        }
        TestEnv.redactor(PiiSize.NANO, block = person("박지훈")).use { r ->
            val spans = r.detectTable(csv)
            assertTrue(spans.toString(), spans.any { it == PiiSpan(start, start + 3, "PERSON", PiiSource.BLOCK) })
            spans.zipWithNext().forEach { (a, b) -> assertTrue("$a $b", a.end <= b.start) }
            assertTrue(spans.none { s -> csv.substring(s.start, s.end).any { it == ',' || it == '\n' } })
        }
    }

    @Test
    fun infoCountsAndId() {
        TestEnv.redactor(PiiSize.NANO).use { assertEquals(PiiListsInfo(0, 0, null), it.info().lists) }
        TestEnv.redactor(PiiSize.NANO, allow = allow("Seoul", "SEOUL"), block = person("박지훈"), listsId = "v3").use { r ->
            assertEquals(PiiListsInfo(1, 1, "v3"), r.info().lists)
        }
    }

    @Test
    fun closeWaitsForCallsInFlight() {
        val text = "담당: 박지훈님께 문의. " + "Write to kim.minjun@example.com or call 010-1234-5678. ".repeat(200)
        val r = TestEnv.redactor(PiiSize.NANO, allow = allow("Seoul"), block = person("박지훈"))
        val want = r.detect(text)
        assertTrue(want.first().source == PiiSource.BLOCK)
        val pool = Executors.newFixedThreadPool(4)
        val started = CountDownLatch(4)
        val ok = AtomicInteger()
        val closed = AtomicInteger()
        try {
            val jobs = (0 until 4).map { k ->
                pool.submit {
                    started.countDown()
                    while (true) {
                        try {
                            val got = if (k % 2 == 0) r.detect(text) else r.detectTable(text)
                            assertEquals(want, got)
                            ok.incrementAndGet()
                        } catch (e: IllegalStateException) {
                            assertEquals("PiiRedactor is closed", e.message)
                            closed.incrementAndGet()
                            break
                        }
                    }
                }
            }
            started.await()
            Thread.sleep(300)
            r.close() // waits for the calls running now; later ones throw
            jobs.forEach { it.get(60, TimeUnit.SECONDS) }
        } finally {
            pool.shutdown()
        }
        assertEquals(4, closed.get())
        assertTrue("calls before close: ${ok.get()}", ok.get() > 0)
        r.close()
    }
}
