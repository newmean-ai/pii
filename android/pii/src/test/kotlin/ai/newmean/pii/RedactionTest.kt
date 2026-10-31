package ai.newmean.pii

import org.junit.Assert.assertEquals
import org.junit.Test

class RedactionTest {
    private fun m(start: Int, end: Int, label: String) = PiiSpan(start, end, label, PiiSource.MODEL)

    private val text = "Call Kim at 010-1234-5678."
    private val spans = listOf(m(5, 8, "PERSON_L2"), m(12, 25, "PHONE"))

    @Test
    fun tagsWithTheLabelFamily() = assertEquals("Call [PERSON] at [PHONE].", PiiRedactor.applyRedaction(text, spans))

    @Test
    fun masksPerCharacter() =
        assertEquals("Call *** at *************.", PiiRedactor.applyRedaction(text, spans, RedactMode.MASK))

    @Test
    fun blanks() = assertEquals("Call  at .", PiiRedactor.applyRedaction(text, spans, RedactMode.BLANK))

    @Test
    fun customReplacement() = assertEquals(
        "Call <PERSON_L2:3> at <PHONE:13>.",
        PiiRedactor.applyRedaction(text, spans) { s, o -> "<${s.label}:${o.length}>" },
    )

    @Test
    fun masksCodePointsNotUtf16Units() {
        val t = "hi 😀👍 there"
        assertEquals("hi ## there", PiiRedactor.applyRedaction(t, listOf(m(3, 7, "PERSON")), RedactMode.MASK, "#"))
    }

    @Test
    fun noSpans() = assertEquals("plain", PiiRedactor.applyRedaction("plain", emptyList()))
}
