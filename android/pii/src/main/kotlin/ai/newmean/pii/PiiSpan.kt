package ai.newmean.pii

/** Where a span came from. */
public enum class PiiSource(public val id: String) {
    MODEL("model"),
    REGEX("regex"),

    /** The model and a pattern found the same text. */
    MODEL_AND_REGEX("model+regex");

    override fun toString(): String = id
}

/**
 * A region to mask: `[start, end)` in UTF-16 units of the input, as used by [String.substring]. [label] is one of
 * [PiiLabels.ALL].
 */
public data class PiiSpan(
    val start: Int,
    val end: Int,
    val label: String,
    val source: PiiSource,
)

/** How [PiiRedactor.redact] replaces each span. */
public enum class RedactMode {
    /** `[EMAIL]`; level 2 labels use their family (`PERSON_L2` -> `[PERSON]`). */
    TAG,

    /** One mask character per code point (`*****`). */
    MASK,

    /** Removed. */
    BLANK,
}

/** Redacted text and the spans that were replaced. */
public data class PiiRedaction(val text: String, val spans: List<PiiSpan>)
