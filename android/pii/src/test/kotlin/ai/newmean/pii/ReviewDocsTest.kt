package ai.newmean.pii

import org.junit.Assert.assertEquals
import org.junit.Test

// The edges of a span the model found (review round of SDK 1.7.1, L2-B2): the cases every package shares are in
// python/tests/vault_cases.json ("redact", run by VaultTest); these check what is Kotlin's own.
class ReviewDocsTest {
    private fun marked(text: String, value: String, source: PiiSource = PiiSource.MODEL): PiiSpan {
        val start = text.indexOf(value)
        return PiiSpan(start, start + value.length, "PERSON", source)
    }

    @Test
    fun aBlockListSpanKeepsItsEdges() {
        val text = "Tiffany & Co. sent it"
        assertEquals("[PERSON_1] sent it", PiiVault().redact(text, listOf(marked(text, "Tiffany & Co.", PiiSource.BLOCK))).text)
    }

    @Test
    fun applyRedactionWithAVaultLearnsTheValueToo() {
        val text = "\uD840\uDC00 Please look up Kim Minsu."
        val vault = PiiVault()
        assertEquals("\uD840\uDC00 Please look up [PERSON_1].", PiiRedactor.applyRedaction(text, listOf(marked(text, "Kim Minsu.")), vault))
        assertEquals(listOf("Kim Minsu"), vault.entries.map { it.value })
        val spans = vault.redact(text, listOf(marked(text, "Kim Minsu.")), known = false).spans
        assertEquals("Kim Minsu", text.substring(spans[0].start, spans[0].end))
    }
}
