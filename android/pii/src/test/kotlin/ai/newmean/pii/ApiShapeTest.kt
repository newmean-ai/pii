package ai.newmean.pii

import android.content.Context
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

// The calls shown in the README, so that they keep compiling; the file-based ones also run (nano, skipped without it).
class ApiShapeTest {
    @Suppress("unused")
    private suspend fun readmeContextCalls(context: Context) {
        val redactor = PiiRedactor.create(context)
        val (text, spans) = redactor.redact("제 메일은 kim@example.com")
        PiiRedactor.create(
            context, size = PiiSize.NANO,
            source = PiiModelSource.Download(dir = File(context.filesDir, "newmean-pii"), onProgress = { _, _, _, _ -> }),
        ).use { it.detect(text + spans.size) }
        PiiRedactor.createBlocking(context, PiiSize.SMALL, labels = PiiLabels.LEVEL1, regex = true).close()
    }

    @Test
    fun readmeFileCalls() = runBlocking {
        TestEnv.requireNative()
        val dir = TestEnv.modelDir(PiiSize.NANO)
        val model = File(dir, PiiModels.modelPath(PiiVariant.Q4))
        val tokenizer = File(dir, PiiModels.TOKENIZER_PATH)
        PiiRedactor.create(model, tokenizer, PiiSize.NANO).use { r ->
            val text = "Mail kim.minjun@example.com or call 010-1234-5678"
            val (masked, spans) = r.redact(text)
            assertEquals(masked, PiiRedactor.applyRedaction(text, spans, RedactMode.TAG))
            assertEquals(PiiRedactor.applyRedaction(text, spans, RedactMode.MASK, "•"), r.redact(text, RedactMode.MASK, maskChar = "•").text)
            val custom = r.redact(text) { span, original -> "<${span.label}:${original.length}>" }
            assertEquals(PiiRedactor.applyRedaction(text, spans) { s, o -> "<${s.label}:${o.length}>" }, custom.text)
            assertEquals(listOf("EMAIL", "PHONE"), spans.map { it.label })
            // coroutines on Dispatchers.Default share one instance
            val all = (1..8).map { async(Dispatchers.Default) { r.detect(text) } }.awaitAll()
            all.forEach { assertEquals(spans, it) }
            r.sensitivity = 0.3
            assertEquals(PiiVariant.Q4, r.variant)
            assertEquals(PiiSize.NANO, r.size)
        }
    }
}
