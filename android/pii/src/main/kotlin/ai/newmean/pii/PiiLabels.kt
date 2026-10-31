package ai.newmean.pii

/** Labels of the v1 models. */
public object PiiLabels {
    public const val PERSON: String = "PERSON"

    /** Level 2: a surname with a title, a nickname, initials. */
    public const val PERSON_L2: String = "PERSON_L2"
    public const val EMAIL: String = "EMAIL"
    public const val PHONE: String = "PHONE"
    public const val ADDRESS: String = "ADDRESS"
    public const val ID_NUMBER: String = "ID_NUMBER"
    public const val FINANCIAL: String = "FINANCIAL"
    public const val ORG: String = "ORG"
    public const val USERNAME: String = "USERNAME"
    public const val URL: String = "URL"

    /** Level 2: public profile or page URLs. */
    public const val URL_L2: String = "URL_L2"
    public const val DATE_OF_BIRTH: String = "DATE_OF_BIRTH"
    public const val SECRET: String = "SECRET"

    /** All labels (the default), in model order. */
    @JvmField
    public val ALL: List<String> = listOf(
        PERSON, PERSON_L2, EMAIL, PHONE, ADDRESS, ID_NUMBER, FINANCIAL, ORG, USERNAME, URL, URL_L2, DATE_OF_BIRTH, SECRET,
    )

    /** All but the level 2 labels (indirect identifiers stay visible). */
    @JvmField
    public val LEVEL1: List<String> = ALL.filterNot { it.endsWith("_L2") }

    /** `PERSON_L2` -> `PERSON`, `URL_L2` -> `URL`; other labels unchanged. */
    @JvmStatic
    public fun family(label: String): String = label.removeSuffix("_L2")

    internal fun id(label: String): Int = ALL.indexOf(label)

    internal fun bits(labels: Collection<String>): Int = labels.fold(0) { acc, l ->
        val i = id(l)
        require(i >= 0) { "unknown label \"$l\" (${ALL.joinToString()})" }
        acc or (1 shl i)
    }
}
