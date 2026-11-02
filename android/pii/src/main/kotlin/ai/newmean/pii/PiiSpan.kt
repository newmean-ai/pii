package ai.newmean.pii

/** Where a span came from. */
public enum class PiiSource(public val id: String) {
    MODEL("model"),
    REGEX("regex"),

    /** The model and a pattern found the same text. */
    MODEL_AND_REGEX("model+regex"),

    /** Propagated down a table column by [PiiRedactor.detectTable]. */
    TABLE("table");

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

/** Field delimiter of [PiiRedactor.detectTable]: [AUTO] detects comma, tab, semicolon or a markdown pipe table. */
public enum class TableDelimiter(public val id: String, internal val code: Int) {
    AUTO("auto", 0),
    COMMA(",", ','.code),
    TAB("\t", '\t'.code),
    SEMICOLON(";", ';'.code),

    /** Markdown pipe table (`| a | b |`). */
    MARKDOWN("markdown", '|'.code);

    override fun toString(): String = id
}

/**
 * Extra reading of [PiiRedactor.detectTable] (the npm option `context`). [HEADER]: with a header row, each non-empty data
 * cell of a column whose non-empty values are at least 80% distinct is also read on its own as "<header>: <value>", and
 * those spans are added (fewer misses on name, contact and ID columns, more PII-free cells masked, one more model call
 * per such cell).
 */
public enum class TableContext(public val id: String, internal val code: Int) {
    NONE("none", 0),
    HEADER("header", 1);

    override fun toString(): String = id
}

/**
 * Options of [PiiRedactor.detectTable], with the npm package's defaults.
 *
 * @property delimiter field delimiter; default [TableDelimiter.AUTO].
 * @property header the first row is a header row, never voted on or propagated into; default true.
 * @property vote column vote and propagation; false: whole-text detection clipped to the cells only. Default true.
 * @property context [TableContext.HEADER] also reads the cells of distinct-valued columns as "<header>: <value>";
 *   default [TableContext.NONE].
 */
public data class TableOptions @JvmOverloads public constructor(
    val delimiter: TableDelimiter = TableDelimiter.AUTO,
    val header: Boolean = true,
    val vote: Boolean = true,
    val context: TableContext = TableContext.NONE,
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
