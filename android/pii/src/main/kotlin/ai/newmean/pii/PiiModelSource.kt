package ai.newmean.pii

import java.io.File

/** Where [PiiRedactor.create] finds the model files. Every file is checked against its published SHA-256 before use. */
public sealed class PiiModelSource {
    /**
     * Files bundled in the app (recommended): `assets/<dir>/pii-<size>/onnx/model_<variant>.onnx` and
     * `assets/<dir>/pii-<size>/tokenizer.web.json`, the layout of the Hugging Face repository. [dir] "" is the assets root.
     */
    public data class Assets(val dir: String = "newmean-pii") : PiiModelSource() {
        /** "<dir>/pii-<size>/" as AssetManager takes it (relative; "" or "/" is the assets root). */
        internal fun folder(size: PiiSize): String = dir.trim('/').let { if (it.isEmpty()) "" else "$it/" } + "pii-${size.id}/"
    }

    /**
     * Downloaded once from Hugging Face (or [baseUrl], the model folder ending with "/") into
     * `<dir>/pii-<size>/` (default `<cacheDir>/newmean-pii/<version>/`) and reused after that. Needs the INTERNET
     * permission in the app.
     */
    public data class Download(
        val baseUrl: String? = null,
        val dir: File? = null,
        val onProgress: PiiProgress? = null,
    ) : PiiModelSource()
}
