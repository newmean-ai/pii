package ai.newmean.pii.internal

/**
 * UTF-8 bytes of a String for the native core. Well-formed text gives exactly String.getBytes(UTF_8); a lone surrogate is
 * written in its 3-byte form (Python "surrogatepass", 1 UTF-16 unit in the core) instead of '?', so offsets stay those of
 * the String and the tokenizer sees what Python and the npm package see.
 */
internal object Utf8 {
    fun encode(s: String): ByteArray {
        val n = s.length
        val out = ByteArray(utf8Length(s))
        var o = 0
        var i = 0
        while (i < n) {
            val c = s[i].code
            when {
                c < 0x80 -> out[o++] = c.toByte()
                c < 0x800 -> {
                    out[o++] = (0xC0 or (c shr 6)).toByte()
                    out[o++] = (0x80 or (c and 0x3F)).toByte()
                }
                c in 0xD800..0xDBFF && i + 1 < n && s[i + 1].code in 0xDC00..0xDFFF -> {
                    val cp = 0x10000 + ((c - 0xD800) shl 10) + (s[i + 1].code - 0xDC00)
                    out[o++] = (0xF0 or (cp shr 18)).toByte()
                    out[o++] = (0x80 or ((cp shr 12) and 0x3F)).toByte()
                    out[o++] = (0x80 or ((cp shr 6) and 0x3F)).toByte()
                    out[o++] = (0x80 or (cp and 0x3F)).toByte()
                    i++
                }
                else -> {
                    out[o++] = (0xE0 or (c shr 12)).toByte()
                    out[o++] = (0x80 or ((c shr 6) and 0x3F)).toByte()
                    out[o++] = (0x80 or (c and 0x3F)).toByte()
                }
            }
            i++
        }
        return out
    }

    private fun utf8Length(s: String): Int {
        var len = 0L
        var i = 0
        while (i < s.length) {
            val c = s[i].code
            len += when {
                c < 0x80 -> 1
                c < 0x800 -> 2
                c in 0xD800..0xDBFF && i + 1 < s.length && s[i + 1].code in 0xDC00..0xDFFF -> { i++; 4 }
                else -> 3
            }
            i++
        }
        require(len <= Int.MAX_VALUE - 8) { "text too long (${s.length} chars)" }
        return len.toInt()
    }
}
