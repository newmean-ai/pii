package ai.newmean.pii

import ai.newmean.pii.internal.NativeBridge
import ai.newmean.pii.internal.Utf8

/**
 * A value you know is not PII in your data (option `allow` of [PiiRedactor.create]): a span whose whole text is [text] is
 * not masked. [label] null: spans of any label; else only spans of that label family (PERSON covers PERSON_L2, URL covers
 * URL_L2). ASCII letters are compared case-insensitively, nothing else is normalized: pass NFC text. 1 to 64 code points,
 * no line break, no space at either end. Since 1.4.0.
 */
public data class PiiAllowEntry @JvmOverloads public constructor(val text: String, val label: String? = null)

/**
 * A value you know is PII (option `block` of [PiiRedactor.create]): every occurrence that starts and ends on a word
 * boundary is masked with [label] (source [PiiSource.BLOCK]), also where the model misses it and whatever `labels` enables.
 * A Hangul entry also matches before Korean particles ("박지훈님께" -> "[PERSON]님께"). Since 1.4.0.
 */
public data class PiiBlockEntry(val text: String, val label: String)

/** Why a list option is invalid ([PiiListException.reason]); [id] is the reason of the npm package's ListError. */
public enum class PiiListReason(public val id: String, internal val code: Int) {
    /** Not an entry of the list's type (a null element). */
    BAD_SHAPE("bad_shape", 1),

    /** More than 256 UTF-16 units, or more than 64 code points. */
    TOO_LONG("too_long", 2),

    /** An unpaired surrogate, U+0000 or a line break (CR, LF, U+0085, U+2028, U+2029). */
    BAD_TEXT("bad_text", 3),
    EMPTY("empty", 4),

    /** The first or last code point is a space (Unicode White_Space; entries are never trimmed). */
    EDGE_SPACE("edge_space", 5),

    /** Not one of [PiiLabels.ALL]. */
    UNKNOWN_LABEL("unknown_label", 6),

    /** More than 10,000 entries (index 10,000). */
    TOO_MANY("too_many", 7),

    /** Two block entries with the same text (ASCII case ignored) and different labels (index of the later one). */
    LABEL_CONFLICT("label_conflict", 8),

    /** The same text (ASCII case ignored) in allow and block (index in block). */
    IN_BOTH("in_both", 9),

    /** listsId is not 1 to 128 code points without U+0000, unpaired surrogates or line breaks. */
    BAD_LISTS_ID("bad_lists_id", 0);

    override fun toString(): String = id
}

/**
 * An invalid `allow`, `block` or `listsId` option, thrown before any model file is read. [list] is "allow", "block" or
 * "meta" (listsId); [index] is the entry's index in that list (null for listsId). Never carries the entry's text.
 */
public class PiiListException internal constructor(
    public val list: String,
    public val index: Int?,
    public val reason: PiiListReason,
) : IllegalArgumentException("${if (list == "meta") "listsId" else "$list list"}${index?.let { " entry $it" } ?: ""}: ${reason.id}")

/** [PiiModelInfo.lists]: entries of each list after merging duplicates, and the caller's listsId. Since 1.4.0. */
public data class PiiListsInfo(val allow: Int = 0, val block: Int = 0, val id: String? = null)

/** Lists checked by the rules the native core repeats (pii_lists_create in native/core/pii.h). */
internal class PreparedLists(
    private val allow: Terms,
    private val block: Terms,
    val id: String?,
) {
    /** UTF-8 entries back to back, their byte lengths and label ids (-1: any label). */
    class Terms(val utf8: ByteArray, val lens: IntArray, val labels: IntArray)

    val isEmpty: Boolean get() = allow.lens.isEmpty() && block.lens.isEmpty()

    /** The core's handle (0 when both lists are empty); throws [PiiListException] for label_conflict and in_both. */
    fun create(): UserLists {
        if (isEmpty) return UserLists(0L, PiiListsInfo(id = id))
        val err = IntArray(3)
        val h = NativeBridge.listsCreate(allow.utf8, allow.lens, allow.labels, block.utf8, block.lens, block.labels, err)
        if (h == 0L) {
            val reason = PiiListReason.entries.firstOrNull { it.code == err[2] && it.code != 0 }
                ?: throw IllegalStateException("unknown list error ${err[2]}")
            throw PiiListException(if (err[0] == 0) "allow" else "block", err[1], reason)
        }
        val counts = NativeBridge.listsCounts(h)
        return UserLists(h, PiiListsInfo(counts[0], counts[1], id))
    }

    companion object {
        private const val MAX_ENTRIES = 10_000
        private const val MAX_U16 = 256
        private const val MAX_CP = 64
        private const val MAX_ID = 128

        /** Validates listsId, then allow, then block; the first invalid entry is reported. */
        fun of(allow: List<PiiAllowEntry>, block: List<PiiBlockEntry>, listsId: String?): PreparedLists {
            if (listsId != null) {
                val n = codePoints(listsId)
                if (n <= 0 || n > MAX_ID || listsId.any { it == '\u0000' || lineBreak(it.code) }) {
                    throw PiiListException("meta", null, PiiListReason.BAD_LISTS_ID)
                }
            }
            return PreparedLists(
                terms("allow", allow) { (it as? PiiAllowEntry)?.let { e -> e.text to e.label } },
                terms("block", block) { (it as? PiiBlockEntry)?.let { e -> e.text to e.label } },
                listsId,
            )
        }

        // entries arrive as List<*>: a Java caller can put null or another type into the list
        private fun terms(name: String, list: List<*>, read: (Any?) -> Pair<String?, String?>?): Terms {
            val m = minOf(list.size, MAX_ENTRIES)
            val texts = arrayOfNulls<ByteArray>(m)
            val labels = IntArray(m)
            val entries = list.iterator()
            for (i in 0 until m) {
                // null text or label: only reachable from Java or through reflection
                val e = read(entries.next())
                val text = e?.first
                val label = e?.second
                if (text == null || (name == "block" && label == null)) throw PiiListException(name, i, PiiListReason.BAD_SHAPE)
                checkText(text)?.let { reason -> throw PiiListException(name, i, reason) }
                labels[i] = if (label == null) -1 else PiiLabels.id(label)
                if (labels[i] < 0 && label != null) throw PiiListException(name, i, PiiListReason.UNKNOWN_LABEL)
                texts[i] = Utf8.encode(text) // well-formed now: never a lone surrogate
            }
            if (list.size > MAX_ENTRIES) throw PiiListException(name, MAX_ENTRIES, PiiListReason.TOO_MANY)
            val utf8 = ByteArray(texts.sumOf { it!!.size })
            var o = 0
            val lens = IntArray(m) { i -> texts[i]!!.size.also { n -> texts[i]!!.copyInto(utf8, o); o += n } }
            return Terms(utf8, lens, labels)
        }

        /** Checks 2-6 of Section 3 on a text, or null when it passes. */
        private fun checkText(s: String): PiiListReason? {
            if (s.length > MAX_U16) return PiiListReason.TOO_LONG // 256 UTF-16 units also bound the UTF-8 bytes (at most 768)
            val n = codePoints(s)
            if (n < 0 || s.any { it == '\u0000' || lineBreak(it.code) }) return PiiListReason.BAD_TEXT
            if (n == 0) return PiiListReason.EMPTY
            if (n > MAX_CP) return PiiListReason.TOO_LONG
            if (whiteSpace(s.codePointAt(0)) || whiteSpace(s.codePointBefore(s.length))) return PiiListReason.EDGE_SPACE
            return null
        }

        /** Code points of s, or -1 when s holds an unpaired surrogate. */
        private fun codePoints(s: String): Int {
            var n = 0
            var i = 0
            while (i < s.length) {
                val c = s[i]
                if (c.isHighSurrogate() && i + 1 < s.length && s[i + 1].isLowSurrogate()) i++
                else if (c.isSurrogate()) return -1
                i++
                n++
            }
            return n
        }

        private fun lineBreak(c: Int) = c == 0x0a || c == 0x0d || c == 0x85 || c == 0x2028 || c == 0x2029

        /** The 25 White_Space code points of Unicode 17.0 (as js/src/lists.ts). */
        private fun whiteSpace(c: Int) = c in 0x09..0x0d || c == 0x20 || c == 0x85 || c == 0xa0 || c == 0x1680 ||
            c in 0x2000..0x200a || c == 0x2028 || c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x3000
    }
}

/** The core's lists handle of a redactor (0: none) and what [PiiRedactor.info] reports. */
internal class UserLists(val handle: Long, val info: PiiListsInfo) {
    fun free() {
        if (handle != 0L) NativeBridge.listsFree(handle)
    }
}
