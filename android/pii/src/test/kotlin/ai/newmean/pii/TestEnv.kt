package ai.newmean.pii

import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue

// Fixtures (js/test/fixtures) and model copies (js/test/models/pii-<size>) are private validation material: they are read
// from their place in the repository (or PII_FIXTURES / PII_MODELS) and the tests that need them skip without them.
internal object TestEnv {
    val fixtures: File? = System.getProperty("pii.fixtures")?.let(::File)?.takeIf { it.isDirectory }
    val models: File? = System.getProperty("pii.models")?.let(::File)?.takeIf { it.isDirectory }
    val jsModels: File? = System.getProperty("pii.jsModels")?.let(::File)?.takeIf { it.isFile }

    /** The JNI library built for the host by the Gradle task buildHostJni. */
    val hasNative: Boolean = System.getProperty("ai.newmean.pii.library")?.let { File(it).isFile } == true

    fun env(key: String): String? = System.getProperty("pii.env.$key") ?: System.getenv(key)

    fun fixture(name: String): File {
        val f = fixtures?.let { File(it, name) }
        assumeTrue("fixture $name not found", f != null && f.isFile)
        return f!!
    }

    fun modelDir(size: PiiSize): File {
        val d = models?.let { File(it, "pii-${size.id}") }
        assumeTrue("model copy pii-${size.id} not found", d != null && File(d, PiiModels.TOKENIZER_PATH).isFile)
        return d!!
    }

    fun requireNative() = assumeTrue("host JNI library not built (cmake missing or -Ppii.hostJni=false)", hasNative)

    fun redactor(size: PiiSize, variant: PiiVariant = size.defaultVariant, sensitivity: Double = 0.0, regex: Boolean = false,
                 labels: Collection<String> = PiiLabels.ALL): PiiRedactor {
        requireNative()
        val dir = modelDir(size)
        val model = File(dir, PiiModels.modelPath(variant))
        assumeTrue("$model not found", model.isFile)
        return PiiRedactor.createBlocking(model, File(dir, PiiModels.TOKENIZER_PATH), size, variant, sensitivity, labels, regex)
    }

    fun jsonl(f: File): List<JSONObject> = f.readLines(Charsets.UTF_8).filter { it.isNotBlank() }.map { JSONObject(it) }

    /** Code point index -> UTF-16 index (Python offsets count code points). */
    fun units(text: String): IntArray {
        val u = IntArray(text.codePointCount(0, text.length) + 1)
        var i = 0
        var k = 0
        while (i < text.length) {
            i += Character.charCount(text.codePointAt(i))
            u[++k] = i
        }
        return u
    }

    /** "start-end:LABEL ..." of a fixture's code point spans, in UTF-16 units. */
    fun pySpans(text: String, spans: JSONArray): List<String> {
        val u = units(text)
        return (0 until spans.length()).map { spans.getJSONObject(it) }
            .map { "${u[it.getInt("start")]}-${u[it.getInt("end")]}:${it.getString("label")}" }
    }

    fun key(spans: List<PiiSpan>): List<String> = spans.map { "${it.start}-${it.end}:${it.label}" }
}
