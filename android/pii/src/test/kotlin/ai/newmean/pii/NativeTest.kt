package ai.newmean.pii

import ai.newmean.pii.internal.NativeBridge
import java.io.File
import java.io.IOException
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

// The JNI path on the host JVM (library built by buildHostJni from the same CMakeLists.txt as the AAR).
class NativeTest {
    @Test
    fun corePinsAndVersionMatchKotlin() {
        TestEnv.requireNative()
        assertEquals("1.0.0", NativeBridge.version())
        for (size in PiiSize.entries) {
            for ((file, pin) in PiiVariant.entries.map { it.id to PiiModels.modelPin(size, it) } + ("tokenizer" to PiiModels.tokenizerPin(size))) {
                assertEquals("$size $file", "${pin.path}\n${pin.bytes}\n${pin.sha256}", NativeBridge.filePin(size.id, file))
            }
        }
        assertEquals(null, NativeBridge.filePin("huge", "q4"))
        println("kernel: ${PiiRedactor.buildInfo()}")
    }

    @Test
    fun tokenizerParityWithHuggingFace() {
        val rows = TestEnv.jsonl(TestEnv.fixture("tokenize.jsonl"))
        TestEnv.redactor(PiiSize.NANO).use { r ->
            var badIds = 0
            var badOffsets = 0
            for (row in rows) {
                val text = row.getString("text")
                val u = TestEnv.units(text)
                val ids = row.getJSONArray("ids")
                val offs = row.getJSONArray("offsets")
                val want = (0 until ids.length()).map { ids.getInt(it) }
                val wantOff = (0 until offs.length()).map { offs.getJSONArray(it).let { o -> u[o.getInt(0)] to u[o.getInt(1)] } }
                val t = r.tokenize(text)
                val got = (0 until t.size / 3).map { t[3 * it] }
                val gotOff = (0 until t.size / 3).map { t[3 * it + 1] to t[3 * it + 2] }
                if (got != want) badIds++
                if (gotOff != wantOff) badOffsets++
            }
            println("tokenizer: ids ${rows.size - badIds}/${rows.size}, offsets ${rows.size - badOffsets}/${rows.size}")
            assertEquals(0, badIds)
            // a grapheme the normalizer replaces aligns to the whole grapheme, as in the npm package: a few rows differ
            // from Hugging Face (js/test/tokenizer.test.ts allows 3; native/core/tests compares offsets with npm exactly)
            assertTrue("offsets: $badOffsets rows differ from Hugging Face", badOffsets <= 3)
        }
    }

    @Test
    fun readmeExample() {
        TestEnv.redactor(PiiSize.MEDIUM).use { r ->
            assertEquals(PiiVariant.Q4E, r.variant)
            val out = r.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다")
            assertEquals("제 메일은 [EMAIL], 전화는 [PHONE]입니다", out.text)
            assertTrue(out.spans.all { it.source == PiiSource.MODEL })
        }
    }

    @Test
    fun regexLayerAndLabels() {
        val text = "Write to kim.minjun@example.com or call 010-1234-5678. Card 4111 1111 1111 1111."
        TestEnv.redactor(PiiSize.NANO).use { r -> assertTrue(r.detect(text).all { it.source == PiiSource.MODEL }) }
        TestEnv.redactor(PiiSize.NANO, regex = true).use { r ->
            // a pattern span of the same label family as the model's is absorbed (source "model"); another label merges
            // into the model's region (source "model+regex"), as combine() in the npm package
            val spans = r.detect(text)
            assertTrue(spans.toString(), spans.any { it.source != PiiSource.MODEL })
            assertEquals(spans.sortedBy { it.start }, spans)
            spans.zipWithNext().forEach { (a, b) -> assertTrue(a.end <= b.start) }
        }
        TestEnv.redactor(PiiSize.NANO, labels = listOf(PiiLabels.EMAIL)).use { r ->
            assertEquals(listOf("EMAIL"), r.detect(text).map { it.label }.distinct())
        }
        TestEnv.redactor(PiiSize.NANO, labels = emptyList()).use { r -> assertEquals(emptyList<PiiSpan>(), r.detect(text)) }
    }

    @Test
    fun offsetsAreUtf16Indices() {
        TestEnv.redactor(PiiSize.NANO).use { r ->
            for (prefix in listOf("", "😀👩‍👩‍👧 ", "é́ ", "\uD800 lone ", "\u0000nul ", "한국어 🇰🇷 ")) {
                val text = prefix + "Contact kim.minjun@example.com today"
                val email = r.detect(text).single { it.label == "EMAIL" }
                assertEquals(prefix, "kim.minjun@example.com", text.substring(email.start, email.end))
            }
            assertEquals(emptyList<PiiSpan>(), r.detect(""))
            assertEquals(emptyList<PiiSpan>(), r.detect(" \n\t "))
        }
    }

    @Test
    fun sensitivityMovesTheThreshold() {
        val text = "Kim from Seoul met Lee at the Hanbit office on Tuesday; send it to minjun_k."
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val base = r.detect(text).sumOf { it.end - it.start }
            r.sensitivity = 1.0
            val more = r.detect(text).sumOf { it.end - it.start }
            r.sensitivity = -1.0
            val less = r.detect(text).sumOf { it.end - it.start }
            assertTrue("$less <= $base <= $more", less <= base && base <= more)
        }
    }

    @Test
    fun threadsGiveTheSameSpans() {
        val rows = TestEnv.jsonl(TestEnv.fixture("predict-nano-q4.jsonl")).take(60).map { it.getString("text") }
        TestEnv.redactor(PiiSize.NANO).use { r ->
            val want = rows.map { r.detect(it) }
            val pool = Executors.newFixedThreadPool(4)
            try {
                val got = (0 until 4).map { k -> pool.submit(Callable { rows.indices.shuffled(java.util.Random(k.toLong())).associateWith { r.detect(rows[it]) } }) }
                for (f in got) for ((i, spans) in f.get()) assertEquals(want[i], spans)
            } finally {
                pool.shutdown()
            }
        }
    }

    @Test
    fun closeAndBadFiles() {
        val r = TestEnv.redactor(PiiSize.NANO)
        r.close()
        r.close()
        try {
            r.detect("x")
            fail("expected IllegalStateException")
        } catch (_: IllegalStateException) {
        }
        val dir = TestEnv.modelDir(PiiSize.NANO)
        val tok = File(dir, PiiModels.TOKENIZER_PATH)
        try {
            // q4e file where q4 is pinned
            PiiRedactor.createBlocking(File(dir, "onnx/model_q4e.onnx"), tok, PiiSize.NANO, PiiVariant.Q4)
            fail("expected a pin mismatch")
        } catch (e: IOException) {
            assertTrue(e.message!!.contains("does not match"))
        }
        try {
            PiiRedactor.createBlocking(File(dir, "onnx/model_q4.onnx"), tok, PiiSize.NANO, labels = listOf("NAME"))
            fail("expected an unknown label")
        } catch (_: IllegalArgumentException) {
        }
    }
}
