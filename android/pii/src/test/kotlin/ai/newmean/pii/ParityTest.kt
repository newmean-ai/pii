package ai.newmean.pii

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

// End-to-end parity with the Python evaluator on the same ONNX files, as js/test/predict.test.ts: spans equal on 99% or
// more of the rows. PII_SIZES=nano,medium limits the sizes.
@RunWith(Parameterized::class)
class ParityTest(private val size: PiiSize, private val variant: PiiVariant, private val bias: Double) {
    companion object {
        @JvmStatic
        @Parameterized.Parameters(name = "{0} {1} bias {2}")
        fun runs(): List<Array<Any>> {
            val sizes = TestEnv.env("PII_SIZES")?.split(",")?.map { it.trim() }
            return listOf(
                Triple(PiiSize.LARGE, PiiVariant.Q4E, 0.0), Triple(PiiSize.LARGE, PiiVariant.Q4, 0.0),
                Triple(PiiSize.MEDIUM, PiiVariant.Q4E, 0.0), Triple(PiiSize.MEDIUM, PiiVariant.Q4, 0.0),
                Triple(PiiSize.SMALL, PiiVariant.Q4, 0.0), Triple(PiiSize.SMALL, PiiVariant.Q4E, 0.0),
                Triple(PiiSize.NANO, PiiVariant.Q4, 0.0), Triple(PiiSize.NANO, PiiVariant.Q4E, 0.0),
                Triple(PiiSize.MEDIUM, PiiVariant.Q4E, -0.5), Triple(PiiSize.NANO, PiiVariant.Q4, 1.0),
            ).filter { sizes == null || it.first.id in sizes }.map { arrayOf(it.first, it.second, it.third) }
        }
    }

    @Test
    fun matchesThePythonEvaluator() {
        TestEnv.requireNative()
        val b = if (bias == 0.0) "" else "-b" + (if (bias == bias.toLong().toDouble()) bias.toLong().toString() else bias.toString())
        val rows = TestEnv.jsonl(TestEnv.fixture("predict-${size.id}-${variant.id}$b.jsonl"))
        assumeTrue(rows.isNotEmpty())
        val t0 = System.nanoTime()
        // Python O bias b is added to the O logit; sensitivity = -b
        TestEnv.redactor(size, variant, sensitivity = -bias).use { r ->
            var same = 0
            val diffs = ArrayList<String>()
            for (row in rows) {
                val text = row.getString("text")
                val want = TestEnv.pySpans(text, row.getJSONArray("spans"))
                val got = TestEnv.key(r.detect(text))
                if (want == got) same++ else if (diffs.size < 3) diffs.add("${text.take(60)}\n  py $want\n  kt $got")
            }
            val ms = (System.nanoTime() - t0) / 1_000_000
            println("parity $size $variant bias $bias: $same/${rows.size} (${"%.2f".format(100.0 * same / rows.size)}%), $ms ms\n${diffs.joinToString("\n")}")
            assertTrue(same.toDouble() / rows.size >= 0.99)
        }
    }
}
