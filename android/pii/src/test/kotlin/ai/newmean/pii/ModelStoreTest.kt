package ai.newmean.pii

import java.io.File
import java.io.IOException
import java.nio.file.Files
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Test

class ModelStoreTest {
    private fun tmp(): File = Files.createTempDirectory("newmean-pii-test").toFile()

    @Test
    fun downloadsVerifiesAndReuses() {
        val src = TestEnv.modelDir(PiiSize.NANO)
        val dir = tmp()
        try {
            val events = ArrayList<Boolean>()
            val progress = PiiProgress { _, loaded, total, cached -> if (loaded == total) events.add(cached) }
            val base = src.toURI().toString() // file:// URL of a local copy: no network
            val files = PiiModels.download(dir, PiiSize.NANO, PiiVariant.Q4, base, progress)
            assertArrayEquals(File(src, "onnx/model_q4.onnx").readBytes(), files.model.readBytes())
            assertEquals(File(dir, "tokenizer.web.json"), files.tokenizer)
            PiiModels.download(dir, PiiSize.NANO, PiiVariant.Q4, base, progress)
            assertEquals(listOf(false, false, true, true), events)
            assertEquals(listOf("model_q4.onnx"), File(dir, "onnx").list()!!.toList()) // no partial files left
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun rejectsAFileThatDoesNotMatchItsPin() {
        val src = TestEnv.modelDir(PiiSize.NANO)
        val dir = tmp()
        try {
            // the nano q4e file served where q4 is expected: right name, wrong bytes
            val fake = File(dir, "src/onnx/model_q4.onnx").apply { parentFile!!.mkdirs() }
            File(src, "onnx/model_q4e.onnx").copyTo(fake)
            try {
                PiiModels.download(File(dir, "out"), PiiSize.NANO, PiiVariant.Q4, File(dir, "src").toURI().toString())
                fail("expected a mismatch")
            } catch (e: IOException) {
                assertTrue(e.message!!, e.message!!.contains("does not match"))
            }
            assertEquals(0, File(dir, "out/onnx").list()?.size ?: 0)
            // a corrupted file already in place is replaced by a verified download
            val out = File(dir, "out2/onnx/model_q4.onnx").apply { parentFile!!.mkdirs() }
            out.writeBytes(ByteArray(10))
            PiiModels.download(File(dir, "out2"), PiiSize.NANO, PiiVariant.Q4, src.toURI().toString())
            assertEquals(PiiModels.modelPin(PiiSize.NANO, PiiVariant.Q4).bytes, out.length())
        } finally {
            dir.deleteRecursively()
        }
    }

    /** Real download from Hugging Face (nano q4, 3.4 MB); runs only with PII_DOWNLOAD=1. */
    @Test
    fun downloadsFromHuggingFace() {
        assumeTrue("set PII_DOWNLOAD=1 to download from Hugging Face", TestEnv.env("PII_DOWNLOAD") == "1")
        val dir = tmp()
        try {
            val files = PiiModels.download(dir, PiiSize.NANO, PiiVariant.Q4)
            assertEquals(PiiModels.modelPin(PiiSize.NANO, PiiVariant.Q4).bytes, files.model.length())
            assertEquals(PiiModels.tokenizerPin(PiiSize.NANO).bytes, files.tokenizer.length())
            if (TestEnv.hasNative) {
                PiiRedactor.createBlocking(files.model, files.tokenizer, PiiSize.NANO).use { r ->
                    val t = "Mail kim.minjun@example.com"
                    assertTrue(r.detect(t).any { it.label == "EMAIL" })
                }
            }
        } finally {
            dir.deleteRecursively()
        }
    }
}
