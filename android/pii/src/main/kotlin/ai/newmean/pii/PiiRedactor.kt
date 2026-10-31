package ai.newmean.pii

import ai.newmean.pii.internal.ModelStore
import ai.newmean.pii.internal.NativeBridge
import ai.newmean.pii.internal.Utf8
import android.content.Context
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException
import java.util.concurrent.locks.ReentrantReadWriteLock
import kotlin.concurrent.read
import kotlin.concurrent.write
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Detects and masks personal information on the device with a newmean-pii-<size> model (the same models, defaults and
 * spans as the npm package @newmean-ai/pii). Thread-safe: any number of threads may call [detect] and [redact] at once.
 * Holds the model in native memory until [close].
 */
public class PiiRedactor private constructor(
    private var handle: Long,
    public val size: PiiSize,
    public val variant: PiiVariant,
    labels: Collection<String>,
    /** Whether the pattern layer ([PiiRegex]) runs next to the model. */
    public val regex: Boolean,
    sensitivity: Double,
) : AutoCloseable {
    private val lock = ReentrantReadWriteLock()
    private val labelBits = PiiLabels.bits(labels)

    /** Model release. */
    public val version: String = PiiModels.VERSION

    /** Labels to mask. */
    public val labels: Set<String> = PiiLabels.ALL.filterTo(LinkedHashSet()) { it in labels }

    /**
     * Moves the decision threshold without reloading the model: > 0 masks more (fewer misses, more over-masking), < 0
     * masks less. Range about -1..1. Same as the npm option `sensitivity`.
     */
    @Volatile
    public var sensitivity: Double = checkSensitivity(sensitivity)
        set(value) {
            field = checkSensitivity(value)
        }

    /** Spans to mask, sorted and non-overlapping, with UTF-16 indices into [text]. Blocking (CPU); call off the main thread. */
    public fun detect(text: String): List<PiiSpan> {
        if (text.isEmpty()) return emptyList()
        val extra = extraOf(if (regex) PiiRegex.spans(text) else emptyList())
        val utf8 = Utf8.encode(text)
        val raw = lock.read {
            check(handle != 0L) { "PiiRedactor is closed" }
            NativeBridge.detect(handle, utf8, sensitivity, labelBits, extra)
        }
        return List(raw.size / 4) { i ->
            PiiSpan(raw[4 * i], raw[4 * i + 1], PiiLabels.ALL[raw[4 * i + 2]], sourceOf(raw[4 * i + 3]))
        }
    }

    /** [detect] and replace each span: [RedactMode.TAG] -> "[EMAIL]", [RedactMode.MASK] -> "*****", [RedactMode.BLANK] -> removed. */
    @JvmOverloads
    public fun redact(text: String, mode: RedactMode = RedactMode.TAG, maskChar: String = "*"): PiiRedaction {
        val spans = detect(text)
        return PiiRedaction(applyRedaction(text, spans, mode, maskChar, null), spans)
    }

    /** [detect] and replace each span with [replace] (span, original text). */
    public fun redact(text: String, replace: (span: PiiSpan, original: String) -> String): PiiRedaction {
        val spans = detect(text)
        return PiiRedaction(applyRedaction(text, spans, RedactMode.TAG, "*", replace), spans)
    }

    /** Model shape, for diagnostics. */
    public fun info(): PiiModelInfo = lock.read {
        check(handle != 0L) { "PiiRedactor is closed" }
        val i = NativeBridge.modelInfo(handle)
        PiiModelInfo(i[0], i[1], i[2], i[3], i[4], i[5], if (i[6] == 2) "int4" else "int8")
    }

    internal fun tokenize(text: String): IntArray = lock.read {
        check(handle != 0L) { "PiiRedactor is closed" }
        NativeBridge.tokenize(handle, Utf8.encode(text))
    }

    /** Frees the model; waits for running calls. Later calls throw IllegalStateException. */
    override fun close() {
        lock.write {
            if (handle != 0L) {
                NativeBridge.modelFree(handle)
                handle = 0L
            }
        }
    }

    public companion object {
        /**
         * Loads a model from [source] (default: the app's assets, see [PiiModelSource.Assets]) on [Dispatchers.IO].
         *
         * @param size nano, small, medium (default) or large.
         * @param variant weight file; default q4e for large and medium, q4 for small and nano.
         * @param sensitivity > 0 masks more, < 0 masks less (about -1..1); default 0.
         * @param labels labels to mask; default all ([PiiLabels.ALL]). [PiiLabels.LEVEL1] leaves level 2 visible.
         * @param regex also mask a few patterns (email, URL, card/account, phone, Korean resident number); default false.
         */
        public suspend fun create(
            context: Context,
            size: PiiSize = PiiModels.DEFAULT_SIZE,
            variant: PiiVariant = size.defaultVariant,
            sensitivity: Double = 0.0,
            labels: Collection<String> = PiiLabels.ALL,
            regex: Boolean = false,
            source: PiiModelSource = PiiModelSource.Assets(),
        ): PiiRedactor = withContext(Dispatchers.IO) {
            createBlocking(context, size, variant, sensitivity, labels, regex, source)
        }

        /** [create] from model files on disk (checked against the pins of [size] and [variant]). */
        public suspend fun create(
            model: File,
            tokenizer: File,
            size: PiiSize = PiiModels.DEFAULT_SIZE,
            variant: PiiVariant = size.defaultVariant,
            sensitivity: Double = 0.0,
            labels: Collection<String> = PiiLabels.ALL,
            regex: Boolean = false,
        ): PiiRedactor = withContext(Dispatchers.IO) {
            createBlocking(model, tokenizer, size, variant, sensitivity, labels, regex)
        }

        /** Blocking [create]; call it off the main thread. */
        @JvmStatic
        @JvmOverloads
        @Throws(IOException::class)
        public fun createBlocking(
            context: Context,
            size: PiiSize = PiiModels.DEFAULT_SIZE,
            variant: PiiVariant = size.defaultVariant,
            sensitivity: Double = 0.0,
            labels: Collection<String> = PiiLabels.ALL,
            regex: Boolean = false,
            source: PiiModelSource = PiiModelSource.Assets(),
        ): PiiRedactor {
            val modelPin = PiiModels.modelPin(size, variant)
            val tokPin = PiiModels.tokenizerPin(size)
            return when (source) {
                is PiiModelSource.Assets -> {
                    val dir = source.folder(size)
                    fun asset(path: String, pin: PiiFilePin): ByteArray = try {
                        context.assets.open(dir + path).use { ModelStore.readVerified(it, pin, "assets/$dir$path") }
                    } catch (e: FileNotFoundException) {
                        throw FileNotFoundException(
                            "assets/$dir$path not found: bundle the model files (see the README) or use PiiModelSource.Download",
                        ).apply { initCause(e) }
                    }
                    load(asset(modelPin.path, modelPin), asset(tokPin.path, tokPin), size, variant, sensitivity, labels, regex)
                }
                is PiiModelSource.Download -> {
                    val root = source.dir ?: File(context.cacheDir, "newmean-pii/${PiiModels.VERSION}")
                    val files = PiiModels.download(
                        File(root, "pii-${size.id}"), size, variant, source.baseUrl ?: PiiModels.baseUrl(size), source.onProgress,
                    )
                    createBlocking(files.model, files.tokenizer, size, variant, sensitivity, labels, regex)
                }
            }
        }

        /** Blocking [create] from model files on disk. */
        @JvmStatic
        @JvmOverloads
        @Throws(IOException::class)
        public fun createBlocking(
            model: File,
            tokenizer: File,
            size: PiiSize = PiiModels.DEFAULT_SIZE,
            variant: PiiVariant = size.defaultVariant,
            sensitivity: Double = 0.0,
            labels: Collection<String> = PiiLabels.ALL,
            regex: Boolean = false,
        ): PiiRedactor = load(
            ModelStore.readVerified(model, PiiModels.modelPin(size, variant)),
            ModelStore.readVerified(tokenizer, PiiModels.tokenizerPin(size)),
            size, variant, sensitivity, labels, regex,
        )

        /** Blocking [create] from the bytes of the two files (checked against the pins; they may be dropped afterwards). */
        @JvmStatic
        @JvmOverloads
        @Throws(IOException::class)
        public fun fromBytes(
            model: ByteArray,
            tokenizer: ByteArray,
            size: PiiSize = PiiModels.DEFAULT_SIZE,
            variant: PiiVariant = size.defaultVariant,
            sensitivity: Double = 0.0,
            labels: Collection<String> = PiiLabels.ALL,
            regex: Boolean = false,
        ): PiiRedactor {
            if (!ModelStore.matches(model, PiiModels.modelPin(size, variant))) throw ModelStore.mismatch("model_${variant.id}.onnx")
            if (!ModelStore.matches(tokenizer, PiiModels.tokenizerPin(size))) throw ModelStore.mismatch(PiiModels.TOKENIZER_PATH)
            return load(model, tokenizer, size, variant, sensitivity, labels, regex)
        }

        /** Replaces [spans] in [text] as [redact] does (for spans kept from [detect] or edited by the app). */
        @JvmStatic
        @JvmOverloads
        public fun applyRedaction(
            text: String,
            spans: List<PiiSpan>,
            mode: RedactMode = RedactMode.TAG,
            maskChar: String = "*",
            replace: ((span: PiiSpan, original: String) -> String)? = null,
        ): String = ai.newmean.pii.applyRedaction(text, spans, mode, maskChar, replace)

        /** Kernel of the native library ("neon", "sse", "scalar"); results are bit-identical across them. */
        @JvmStatic
        public fun buildInfo(): String = NativeBridge.buildInfo()

        private fun load(
            model: ByteArray,
            tokenizer: ByteArray,
            size: PiiSize,
            variant: PiiVariant,
            sensitivity: Double,
            labels: Collection<String>,
            regex: Boolean,
        ): PiiRedactor {
            PiiLabels.bits(labels) // unknown labels fail before the model loads
            checkSensitivity(sensitivity)
            return PiiRedactor(NativeBridge.modelLoad(model, tokenizer), size, variant, labels, regex, sensitivity)
        }

        private fun checkSensitivity(v: Double): Double {
            require(v.isFinite()) { "sensitivity must be a finite number" }
            return v
        }

        private fun sourceOf(bits: Int): PiiSource = when (bits) {
            1 -> PiiSource.MODEL
            2 -> PiiSource.REGEX
            else -> PiiSource.MODEL_AND_REGEX
        }

        private fun extraOf(spans: List<PiiSpan>): IntArray? {
            if (spans.isEmpty()) return null
            val a = IntArray(spans.size * 3)
            spans.forEachIndexed { i, s ->
                a[3 * i] = s.start
                a[3 * i + 1] = s.end
                a[3 * i + 2] = PiiLabels.id(s.label)
            }
            return a
        }
    }
}
