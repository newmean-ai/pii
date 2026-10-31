package ai.newmean.pii

import ai.newmean.pii.internal.Utf8
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test

class LabelsAndModelsTest {
    @Test
    fun labelsMatchTheCore() {
        // same order as enum pii_label in native/core/pii.h
        assertEquals(13, PiiLabels.ALL.size)
        assertEquals(0x1fff, PiiLabels.bits(PiiLabels.ALL))
        assertEquals(0x1bfd, PiiLabels.bits(PiiLabels.LEVEL1)) // PII_LABELS_LEVEL1
        assertEquals("PERSON", PiiLabels.family("PERSON_L2"))
        assertEquals("URL", PiiLabels.family("URL_L2"))
        assertEquals(0, PiiLabels.bits(emptyList()))
    }

    @Test(expected = IllegalArgumentException::class)
    fun unknownLabelFails() {
        PiiLabels.bits(listOf("EMAIL", "PASSPORT"))
    }

    @Test
    fun defaultsMatchTheNpmPackage() {
        assertEquals(PiiSize.MEDIUM, PiiModels.DEFAULT_SIZE)
        assertEquals(PiiVariant.Q4E, PiiSize.LARGE.defaultVariant)
        assertEquals(PiiVariant.Q4E, PiiSize.MEDIUM.defaultVariant)
        assertEquals(PiiVariant.Q4, PiiSize.SMALL.defaultVariant)
        assertEquals(PiiVariant.Q4, PiiSize.NANO.defaultVariant)
        assertEquals("https://huggingface.co/newmean-ai/pii-nano/resolve/v1.0.0/", PiiModels.baseUrl(PiiSize.NANO))
        assertEquals("onnx/model_q4e.onnx", PiiModels.modelPath(PiiVariant.Q4E))
    }

    @Test
    fun pinsMatchTheNpmPackage() {
        val f = TestEnv.jsModels
        assumeTrue("js/src/models.ts not found", f != null)
        val s = f!!.readText()
        val files = JSONObject(s.substring(s.indexOf("= {") + 2, s.lastIndexOf("};") + 1))
        assertEquals("export const MODEL_VERSION = \"${PiiModels.VERSION}\";", s.lines().first { it.contains("MODEL_VERSION =") })
        var n = 0
        for (size in PiiSize.entries) {
            val js = files.getJSONObject(size.id)
            for (v in PiiVariant.entries) {
                val p = PiiModels.modelPin(size, v)
                assertEquals(js.getJSONObject(v.id).getLong("bytes"), p.bytes)
                assertEquals(js.getJSONObject(v.id).getString("sha256"), p.sha256)
                n++
            }
            val t = PiiModels.tokenizerPin(size)
            assertEquals(js.getJSONObject("tokenizer").getLong("bytes"), t.bytes)
            assertEquals(js.getJSONObject("tokenizer").getString("sha256"), t.sha256)
            n++
        }
        assertEquals(12, n)
    }

    @Test
    fun utf8MatchesTheJdkOnWellFormedText() {
        for (t in listOf("", "abc", "é 한국어 עברית", "😀👩‍👩‍👧🇰🇷", "nul\u0000x", "￿﻿", "é", "a".repeat(10_000) + "😀")) {
            assertArrayEquals(t, t.toByteArray(Charsets.UTF_8), Utf8.encode(t))
        }
    }

    @Test
    fun utf8KeepsLoneSurrogatesAsThreeBytes() {
        // Python "surrogatepass": one code point and one UTF-16 unit in the core, so offsets stay those of the String
        assertArrayEquals(byteArrayOf(0x61, 0xED.toByte(), 0xA0.toByte(), 0x80.toByte(), 0x62), Utf8.encode("a\uD800b"))
        assertArrayEquals(byteArrayOf(0xED.toByte(), 0xB0.toByte(), 0x80.toByte()), Utf8.encode("\uDC00"))
        assertArrayEquals(byteArrayOf(0xED.toByte(), 0xAF.toByte(), 0xBF.toByte(), 0x61), Utf8.encode("\uDBFFa"))
    }

    @Test
    fun sizeAndVariantNames() {
        assertEquals(PiiSize.SMALL, PiiSize.of("small"))
        assertEquals(PiiVariant.Q4E, PiiVariant.of("q4e"))
        assertEquals("nano", PiiSize.NANO.toString())
    }

    @Test
    fun assetFolders() {
        assertEquals("newmean-pii/pii-medium/", PiiModelSource.Assets().folder(PiiSize.MEDIUM))
        assertEquals("models/pii-nano/", PiiModelSource.Assets("/models/").folder(PiiSize.NANO))
        assertEquals("pii-nano/", PiiModelSource.Assets("").folder(PiiSize.NANO))
    }

    @Test(expected = IllegalArgumentException::class)
    fun unknownSize() {
        PiiSize.of("huge")
    }
}
