package ai.newmean.pii

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test

// Spans equal to the npm package's detect() on the same texts and options, including the regex layer and the combine
// step. The expected spans come from scripts/npm_spans.mjs (PII_NPM_SPANS=file); skipped without it.
class NpmParityTest {
    @Test
    fun matchesTheNpmPackage() {
        val path = TestEnv.env("PII_NPM_SPANS")
        assumeTrue("PII_NPM_SPANS not set (scripts/npm_spans.mjs)", path != null && File(path).isFile)
        val rows = TestEnv.jsonl(File(path!!))
        val o = rows.first()
        val labels = if (o.getString("labels") == "level1") PiiLabels.LEVEL1 else PiiLabels.ALL
        TestEnv.redactor(
            PiiSize.of(o.getString("size")), PiiVariant.of(o.getString("variant")), o.getDouble("sensitivity"),
            o.getBoolean("regex"), labels,
        ).use { r ->
            val bad = ArrayList<String>()
            for (row in rows.drop(1)) {
                val text = row.getString("text")
                val a = row.getJSONArray("spans")
                val want = (0 until a.length()).map { a.getJSONArray(it) }.map { "${it.getInt(0)}-${it.getInt(1)}:${it.getString(2)}:${it.getString(3)}" }
                val got = r.detect(text).map { "${it.start}-${it.end}:${it.label}:${it.source}" }
                if (want != got) bad.add("${text.take(60)}\n  js $want\n  kt $got")
            }
            println("npm parity $o: ${rows.size - 1 - bad.size}/${rows.size - 1}\n${bad.take(5).joinToString("\n")}")
            assertEquals(0, bad.size)
        }
    }
}
