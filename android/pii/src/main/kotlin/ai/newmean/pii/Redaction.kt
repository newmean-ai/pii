package ai.newmean.pii

/** Replaces [spans] (sorted, non-overlapping, as returned by [PiiRedactor.detect]) in [text]. */
internal fun applyRedaction(
    text: String,
    spans: List<PiiSpan>,
    mode: RedactMode,
    maskChar: String,
    replace: ((PiiSpan, String) -> String)?,
): String {
    val out = StringBuilder(text.length)
    var last = 0
    for (s in spans) {
        val orig = text.substring(s.start, s.end)
        out.append(text, last, s.start)
        out.append(
            when {
                replace != null -> replace(s, orig)
                mode == RedactMode.TAG -> "[${PiiLabels.family(s.label)}]"
                mode == RedactMode.MASK -> maskChar.repeat(orig.codePointCount(0, orig.length))
                else -> ""
            },
        )
        last = s.end
    }
    return out.append(text, last, text.length).toString()
}
