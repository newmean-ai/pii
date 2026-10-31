package ai.newmean.pii.internal

/** JNI entry points of libnewmean_pii_jni (src/main/cpp/pii_jni.c). Text is passed as [Utf8.encode] bytes. */
internal object NativeBridge {
    /** Host JVM tests load the library built by the Gradle task buildHostJni from this path. */
    const val LIBRARY_PROPERTY = "ai.newmean.pii.library"

    // System.load only for the host build named by a system property, which only code in the same process can set
    @Suppress("UnsafeDynamicallyLoadedCode")
    private fun load() {
        val path = System.getProperty(LIBRARY_PROPERTY)
        if (path.isNullOrEmpty()) System.loadLibrary("newmean_pii_jni") else System.load(path)
    }

    init {
        load()
    }

    /** Handle of the loaded model; throws IllegalArgumentException with the core's message. */
    @JvmStatic external fun modelLoad(onnx: ByteArray, tokenizer: ByteArray): Long

    @JvmStatic external fun modelFree(handle: Long)

    /** [hidden, layers, layer_applications, vocab, max_tokens, tags, embedding_kind] */
    @JvmStatic external fun modelInfo(handle: Long): IntArray

    /** [utf16_start, utf16_end, label, source] per span; extra: [utf16_start, utf16_end, label] per pattern span. */
    @JvmStatic external fun detect(handle: Long, utf8: ByteArray, sensitivity: Double, labels: Int, extra: IntArray?): IntArray

    /** [id, utf16_start, utf16_end] per token, without <s> and </s>. */
    @JvmStatic external fun tokenize(handle: Long, utf8: ByteArray): IntArray

    @JvmStatic external fun version(): String

    @JvmStatic external fun buildInfo(): String

    /** "path\nbytes\nsha256" of a published file in the core's pins, or null. */
    @JvmStatic external fun filePin(size: String, file: String): String?
}
