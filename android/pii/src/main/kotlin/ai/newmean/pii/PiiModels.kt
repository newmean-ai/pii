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

/**
 * What a redactor runs: the model shape (hidden size, layers, layer applications (nano shares one layer), vocabulary, tokens
 * per window, tags, embedding type), the versions of this package (and its native core) and of the model release, size,
 * variant, the two model files with the size and SHA-256 the loaded bytes were checked against, and the kernel build.
 */
public data class PiiModelInfo(
    val hidden: Int,
    val layers: Int,
    val layerApplications: Int,
    val vocab: Int,
    val maxTokens: Int,
    val tags: Int,
    val embedding: String,
    val packageVersion: String = "",
    val modelVersion: String = "",
    val size: PiiSize? = null,
    val variant: PiiVariant? = null,
    val model: PiiFilePin? = null,
    val tokenizer: PiiFilePin? = null,
    val build: String = "",
)

/** The two files of a model on disk. */
public data class PiiModelFiles(val model: File, val tokenizer: File)

/** Download progress; [cached] is true when the file was already on disk and verified. */
public fun interface PiiProgress {
    public fun onProgress(file: String, loaded: Long, total: Long, cached: Boolean)
}

/** Published model files of release [VERSION] and their pins (same values as js/src/models.ts and native/core). */
public object PiiModels {
    /** Model release (Hugging Face tag v1.1.0). */
    public const val VERSION: String = "1.1.0"

    /** Default size, as in the npm package. */
    @JvmField
    public val DEFAULT_SIZE: PiiSize = PiiSize.MEDIUM

    public const val TOKENIZER_PATH: String = "tokenizer.web.json"

    /** "onnx/model_<variant>.onnx" */
    @JvmStatic
    public fun modelPath(variant: PiiVariant): String = "onnx/model_${variant.id}.onnx"

    /** https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.1.0/ */
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
        pin("nano", "q4", 2602752, "24b87d9933a0cbdb42b201b44488ad297f3d7fda000731c6683913d8e9af1360"),
        pin("nano", "q4e", 2072202, "6054bb535e245455ba321440b47c6e8394aa11949d9f8dbc121ea82bd62a5d84"),
        pin("nano", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
        pin("small", "q4", 7112241, "1cbd7e8e4d840db3d919838fc9dc4496a324af40016508faf71ba2d001ad9e4d"),
        pin("small", "q4e", 6159899, "8a2c26a31858298e96bc34efea534d3e686ab6700c105c724be1db411209c18f"),
        pin("small", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
        pin("medium", "q4", 13962065, "dec417ab148ab203b1cc9051dadf1117a6590ce6bc02585f434c5095c6a37ba5"),
        pin("medium", "q4e", 11303433, "a84ba05d2e98e51c6d1a10cc898285862e98c02e1c5604f0a1be0e367e46c6ad"),
        pin("medium", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
        pin("large", "q4", 20693010, "1f5b371014423eb0c347d1eea68b5112483784b59f65ad91a9951648266e9ea1"),
        pin("large", "q4e", 15492704, "576a030d803517d21423a2dc97d7a83fcb68563eec768ee7fbb0d95da91def40"),
        pin("large", "tokenizer", 810589, "11838f41d00124e43170c8f4e6d025ac5ea301ffee40bc0508b1a412c4fbf11d"),
    )
}
