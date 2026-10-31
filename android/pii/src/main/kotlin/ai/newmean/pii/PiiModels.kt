package ai.newmean.pii

import ai.newmean.pii.internal.ModelStore
import java.io.File
import java.io.IOException

/** Model size: nano (2.6 MB), small (7.1 MB), medium (11.3 MB, default), large (15.5 MB), plus a 0.8 MB tokenizer. */
public enum class PiiSize(public val id: String) {
    NANO("nano"),
    SMALL("small"),
    MEDIUM("medium"),
    LARGE("large");

    /** Default weight file, as in the npm package: q4e for large and medium, q4 for small and nano. */
    public val defaultVariant: PiiVariant
        get() = if (this == LARGE || this == MEDIUM) PiiVariant.Q4E else PiiVariant.Q4

    override fun toString(): String = id

    public companion object {
        @JvmStatic
        public fun of(id: String): PiiSize =
            entries.firstOrNull { it.id == id } ?: throw IllegalArgumentException("unknown size \"$id\" (nano, small, medium, large)")
    }
}

/** Weight file: q4 (4-bit layers, 8-bit embeddings) or q4e (4-bit embeddings too). */
public enum class PiiVariant(public val id: String) {
    Q4("q4"),
    Q4E("q4e");

    override fun toString(): String = id

    public companion object {
        @JvmStatic
        public fun of(id: String): PiiVariant =
            entries.firstOrNull { it.id == id } ?: throw IllegalArgumentException("unknown variant \"$id\" (q4, q4e)")
    }
}

/** A published model file: [path] relative to the model folder, its size and SHA-256 (lowercase hex). */
public data class PiiFilePin(val path: String, val bytes: Long, val sha256: String)

/** Shape of a loaded model: hidden size, layers, layer applications (nano shares one layer), vocabulary, tokens per window, tags, embedding type. */
public data class PiiModelInfo(
    val hidden: Int,
    val layers: Int,
    val layerApplications: Int,
    val vocab: Int,
    val maxTokens: Int,
    val tags: Int,
    val embedding: String,
)

/** The two files of a model on disk. */
public data class PiiModelFiles(val model: File, val tokenizer: File)

/** Download progress; [cached] is true when the file was already on disk and verified. */
public fun interface PiiProgress {
    public fun onProgress(file: String, loaded: Long, total: Long, cached: Boolean)
}

/** Published model files of release [VERSION] and their pins (same values as js/src/models.ts and native/core). */
public object PiiModels {
    /** Model release (Hugging Face tag v1.0.0). */
    public const val VERSION: String = "1.0.0"

    /** Default size, as in the npm package. */
    @JvmField
    public val DEFAULT_SIZE: PiiSize = PiiSize.MEDIUM

    public const val TOKENIZER_PATH: String = "tokenizer.web.json"

    /** "onnx/model_<variant>.onnx" */
    @JvmStatic
    public fun modelPath(variant: PiiVariant): String = "onnx/model_${variant.id}.onnx"

    /** https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/ */
    @JvmStatic
    public fun baseUrl(size: PiiSize): String = "https://huggingface.co/newmean-ai/pii-${size.id}/resolve/v$VERSION/"

    @JvmStatic
    public fun modelPin(size: PiiSize, variant: PiiVariant): PiiFilePin = PINS.getValue(size.id + "/" + variant.id)

    @JvmStatic
    public fun tokenizerPin(size: PiiSize): PiiFilePin = PINS.getValue(size.id + "/tokenizer")

    /**
     * Downloads the model and tokenizer of [size] once into [dir] (as <dir>/onnx/model_<variant>.onnx and
     * <dir>/tokenizer.web.json) and checks both against their pins; files already there are verified and reused.
     * Blocking; needs the INTERNET permission. [baseUrl] must end with "/" (default: the Hugging Face repository).
     */
    @JvmStatic
    @JvmOverloads
    @Throws(IOException::class)
    public fun download(
        dir: File,
        size: PiiSize = DEFAULT_SIZE,
        variant: PiiVariant = size.defaultVariant,
        baseUrl: String = baseUrl(size),
        onProgress: PiiProgress? = null,
    ): PiiModelFiles {
        val tok = tokenizerPin(size)
        val model = modelPin(size, variant)
        return PiiModelFiles(
            model = ModelStore.fetch(baseUrl + model.path, File(dir, model.path), model, onProgress),
            tokenizer = ModelStore.fetch(baseUrl + tok.path, File(dir, tok.path), tok, onProgress),
        )
    }

    private fun pin(size: String, file: String, bytes: Long, sha256: String): Pair<String, PiiFilePin> =
        "$size/$file" to PiiFilePin(if (file == "tokenizer") TOKENIZER_PATH else "onnx/model_$file.onnx", bytes, sha256)

    // From js/src/models.ts (generated from the Hugging Face manifest); PinsTest checks them against it and the core.
    internal val PINS: Map<String, PiiFilePin> = mapOf(
        pin("nano", "q4", 2602752, "df9b5cb908c3c71783c293bc539425e1f9a973627b2a47a124f458b6f7c6d0af"),
        pin("nano", "q4e", 2072202, "3c8032f7bcaf52af893f694fc87af3b0b5d327541fbe574e8a0562aef484fbd0"),
        pin("nano", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
        pin("small", "q4", 7112241, "cc6157ba8e80991690eec169b5d4bcb05fe5acc74ceef01dfafc89ba527a8225"),
        pin("small", "q4e", 6159899, "89bf1225f471ce948dbe2c7b3eafe69e2e3612b16e44393435ea6eb5798efaed"),
        pin("small", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
        pin("medium", "q4", 13962065, "9677ef4dbee6911ad4e45bf2a23a26d88ebb4e381a913d5c625983fe14845715"),
        pin("medium", "q4e", 11303433, "054ba8b06a2ea2ec6bae73ff380e36e5ff3cb3611d7a080074eb44d65669b58d"),
        pin("medium", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
        pin("large", "q4", 20693010, "47a19f7031f439413bdb277f96f3f98e7006c1629a1f3f965b4be48f153753b5"),
        pin("large", "q4e", 15492704, "c8d3d870313797d85aa1d57809d953eea3139bd859f198cfafe0f3b6c6f7735e"),
        pin("large", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
    )
}
