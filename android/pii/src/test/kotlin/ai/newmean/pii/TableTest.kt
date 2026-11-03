package ai.newmean.pii

import ai.newmean.pii.internal.NativeBridge
import ai.newmean.pii.internal.Utf8
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

// Table mode through the JNI bridge (pii_detect_table) on nano: the options reach the core, spans stay inside cells with
// UTF-16 offsets (an astral emoji before later rows), and text that is not a table gives the spans of detect. The column
// logic itself is tested in native/core/tests (pii_cli selftest, run_tests.py table, parity with the npm package).
class TableTest {
    private val names = listOf("Maria Lopez", "Kim Minsu", "Dupont, Jean", "Anna Kowalska", "Ahmed Ali", "Sofia Rossi", "Lars Nilsen", "Priya Sharma")
    private val emails = listOf(
        "maria.lopez@example.com", "minsu.kim@example.com", "jean.dupont@example.fr", "anna.k@example.pl", "ahmed.ali@example.com",
        "sofia.rossi@example.it", "lars@example.no", "priya.sharma@example.in",
    )
    private val phones = listOf(
        "010-1234-5678", "010-2222-3333", "+33 6 12 34 56 78", "+48 512 345 678", "050-123-4567", "+39 347 123 4567",
        "+47 912 34 567", "+91 98765 43210",
    )
    private val csv = "name,email,phone,comment\n" +
        "Maria Lopez,maria.lopez@example.com,010-1234-5678,Called twice about the delivery\n" +
        "Kim Minsu,minsu.kim@example.com,010-2222-3333,no answer\n" +
        "\"Dupont, Jean\",jean.dupont@example.fr,+33 6 12 34 56 78,\"said \"\"later\"\"\"\n" +
        "Anna Kowalska,anna.k@example.pl,+48 512 345 678,ok 👍🏽\n" +
        "Ahmed Ali,ahmed.ali@example.com,050-123-4567,wants a refund\n" +
        "Sofia Rossi,sofia.rossi@example.it,+39 347 123 4567,ok\n" +
        "Lars Nilsen,lars@example.no,+47 912 34 567,will call back\n" +
        "N/A,N/A,N/A,\n" +
        "Priya Sharma,priya.sharma@example.in,+91 98765 43210,ok\n"
    private val prose = "My name is Kim Minsu 👩‍👩‍👧 and my email is minsu.kim@example.com."

    private fun texts(spans: List<PiiSpan>, source: PiiSource? = null) =
        spans.filter { source == null || it.source == source }.map { csv.substring(it.start, it.end) to it.label }

    /** Sorted, non-overlapping, and never over a line break, a delimiter or a quote at either end. */
    private fun checkCells(spans: List<PiiSpan>) {
        spans.zipWithNext().forEach { (a, b) -> assertTrue("$a $b", a.end <= b.start) }
        for (s in spans) {
            val t = csv.substring(s.start, s.end)
            assertFalse(t, t.contains('\n') || t.first() in ",\"" || t.last() in ",\"")
        }
    }

    @Test
    fun columnsArePropagated() {
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val spans = r.detectTable(csv)
            checkCells(spans)
            val want = names.zip(emails).zip(phones).flatMap { (ne, p) -> listOf(ne.first to "PERSON", ne.second to "EMAIL", p to "PHONE") }
            assertEquals(want, texts(spans, PiiSource.TABLE))
            // the header row and the placeholders are not propagated into
            assertTrue(spans.none { it.start < csv.indexOf('\n') })
            val na = csv.indexOf("N/A")
            assertTrue(spans.none { it.source == PiiSource.TABLE && it.start >= na && it.start < na + 12 })
            assertEquals(spans, r.detectTable(csv, TableOptions()))
            assertEquals(spans, r.detectTable(csv, TableOptions(TableDelimiter.COMMA)))
        }
    }

    @Test
    fun optionsReachTheCore() {
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val off = r.detectTable(csv, TableOptions(vote = false))
            checkCells(off)
            assertTrue(off.isNotEmpty() && off.none { it.source == PiiSource.TABLE })
            // header off: the header row is a data row; "name" joins the vote's 80% and is masked, "email" has no "@" and
            // "phone" fewer than 6 digits
            val noHeader = r.detectTable(csv, TableOptions(header = false))
            assertEquals(listOf("name" to "PERSON"), texts(noHeader, PiiSource.TABLE).filter { it.first in setOf("name", "email", "phone") })
            // ';' gives one long column per line: no vote, and no span over a line break
            val semi = r.detectTable(csv, TableOptions(TableDelimiter.SEMICOLON))
            assertTrue(semi.none { it.source == PiiSource.TABLE || csv.substring(it.start, it.end).contains('\n') })
            // markdown: no line starts with '|', so there are no rows and the text is not a table
            assertEquals(r.detect(csv), r.detectTable(csv, TableOptions(TableDelimiter.MARKDOWN)))
        }
        TestEnv.redactor(PiiSize.NANO, labels = listOf(PiiLabels.EMAIL)).use { r ->
            val spans = r.detectTable(csv)
            assertEquals(emails.map { it to "EMAIL" }, texts(spans))
            assertTrue(spans.all { it.source == PiiSource.TABLE })
        }
    }

    @Test
    fun notATableGivesDetect() {
        for (regex in listOf(false, true)) {
            TestEnv.redactor(PiiSize.NANO, regex = regex).use { r ->
                for (t in listOf(prose, "", "one line, with a comma", "a;b")) assertEquals("$regex $t", r.detect(t), r.detectTable(t))
                val email = r.detectTable(prose).single { it.label == "EMAIL" }
                assertEquals("minsu.kim@example.com", prose.substring(email.start, email.end))
            }
        }
        // the regex layer is passed to table mode too (pattern spans are clipped to cells like the model's)
        TestEnv.redactor(PiiSize.NANO, regex = true).use { r ->
            val spans = r.detectTable(csv)
            checkCells(spans)
            assertTrue(spans.any { it.source == PiiSource.TABLE })
        }
    }

    @Test
    fun redactTableKeepsTheTable() {
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val out = r.redactTable(csv)
            assertEquals(r.detectTable(csv), out.spans)
            assertEquals(PiiRedactor.applyRedaction(csv, out.spans), out.text)
            val lines = out.text.split('\n')
            assertEquals(csv.split('\n').size, lines.size)
            assertEquals("name,email,phone,comment", lines[0])
            assertEquals("[PERSON],[EMAIL],[PHONE],Called twice about the delivery", lines[1])
            assertEquals(csv.count { it == '"' }, out.text.count { it == '"' })
            val masked = r.redactTable(csv, TableOptions(), RedactMode.MASK, "#")
            assertEquals(PiiRedactor.applyRedaction(csv, masked.spans, RedactMode.MASK, "#"), masked.text)
            assertEquals(lines.size, masked.text.split('\n').size)
            val custom = r.redactTable(csv, TableOptions(vote = false)) { s, o -> "<${s.label}:${o.length}>" }
            assertEquals(PiiRedactor.applyRedaction(csv, custom.spans) { s, o -> "<${s.label}:${o.length}>" }, custom.text)
        }
    }

    @Test
    fun headerContext() {
        val text = "name,email\n" + names.indices.filter { it != 2 }.joinToString("") { "${names[it]},${emails[it]}\n" }
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val base = r.detectTable(text)
            val ctx = r.detectTable(text, TableOptions(context = TableContext.HEADER))
            assertEquals(base, r.detectTable(text, TableOptions(context = TableContext.NONE)))
            assertTrue(ctx.isNotEmpty())
            ctx.zipWithNext().forEach { (a, b) -> assertTrue("$a $b", a.end <= b.start) }
            // inside one cell: never over a delimiter or a line break
            assertTrue(ctx.none { s -> text.substring(s.start, s.end).any { it == ',' || it == '\n' } })
            fun masked(spans: List<PiiSpan>) = spans.flatMap { it.start until it.end }.toSet()
            assertTrue(masked(ctx).containsAll(masked(base)))
            assertEquals(ctx, r.redactTable(text, TableOptions(context = TableContext.HEADER)).spans)
            // a context the core does not know (only reachable through the bridge) is PII_ERR_ARG
            assertThrows(IllegalArgumentException::class.java) {
                NativeBridge.detectTable(TestEnv.handle(r), Utf8.encode(text), 0.0, PiiLabels.bits(PiiLabels.ALL), null, false, false, false, 0L, 0, true, true, 2)
            }
        }
        assertEquals(listOf("none", "header"), TableContext.entries.map { it.id })
    }

    @Test
    fun keepEmoji() {
        val text = "Call Anna Kim 😀 at 010-1234-5678"
        val e = text.indexOf("😀")
        TestEnv.redactor(PiiSize.NANO).use { r ->
            assertFalse(r.keepEmoji)
            for (spans in listOf(r.detect(text), r.detectTable(text))) {
                assertTrue(spans.isNotEmpty())
                assertTrue("$spans", spans.all { it.end <= e || it.start >= e + 2 })
            }
        }
        TestEnv.redactor(PiiSize.NANO, keepEmoji = true).use { r ->
            assertTrue(r.keepEmoji)
            r.detect(text)
        }
    }

    @Test
    fun wholeWords() {
        TestEnv.redactor(PiiSize.NANO, wholeWords = false).use { assertFalse(it.wholeWords) }
        TestEnv.redactor(PiiSize.NANO).use { r ->
            assertTrue(r.wholeWords) // the default since 1.3.0
            for (text in listOf("남성", "경기도", "R23", "Hi, I'm Kim Minji, call me at 010-1234-5678.", "region,id\n경기도,R23\n서울,R24\n")) {
                for (spans in listOf(r.detect(text), r.detectTable(text))) {
                    for (s in spans) { // no span starts or ends inside a word
                        assertTrue("$text $s", s.start == 0 || !(text[s.start - 1].isLetterOrDigit() && text[s.start].isLetterOrDigit()))
                        assertTrue("$text $s", s.end == text.length || !(text[s.end - 1].isLetterOrDigit() && text[s.end].isLetterOrDigit()))
                    }
                }
            }
        }
    }

    @Test
    fun strictShortNames() {
        TestEnv.redactor(PiiSize.NANO).use { plain ->
            TestEnv.redactor(PiiSize.NANO, strictShortNames = true).use { strict ->
                assertTrue(strict.strictShortNames)
                val long = "Hi, I'm Kim Minji, call me at 010-1234-5678. I live in Seoul and work at Example Corp as an engineer."
                assertEquals(plain.detect(long), strict.detect(long)) // longer texts are unchanged
                fun person(spans: List<PiiSpan>) = spans.filter { it.label.startsWith("PERSON") }.sumOf { it.end - it.start }
                for (text in listOf("경기", "네이버", "카카오", "남성", "김민지", "Maria Lopez")) { // never more PERSON than without the option
                    assertTrue(text, person(strict.detect(text)) <= person(plain.detect(text)))
                }
            }
        }
    }

    @Test
    fun info() {
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val i = r.info()
            assertEquals("1.4.0", i.packageVersion)
            assertEquals(PiiModels.VERSION, i.modelVersion)
            assertEquals(PiiSize.NANO, i.size)
            assertEquals(PiiModels.modelPin(PiiSize.NANO, r.variant), i.model)
            assertEquals(PiiModels.tokenizerPin(PiiSize.NANO), i.tokenizer)
            assertTrue(i.layers > 0)
        }
    }
}
