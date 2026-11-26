package ai.newmean.pii

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse

// Timing of the doubling tests (REVIEW-GUIDE rule 4), shared by the test classes with "Growth" in their name, which
// the test runner runs alone, after the suites.

/** Skips the calling test when PII_SKIP_TIMING=1 (JUnit Assume): a test with an assertion on measured time (a doubling
 *  ratio or a time limit) runs alone, never inside a functional step (the 1.7.2 release standard, CI3). The functional
 *  steps of android.yml set the variable, and its timing step runs these tests by name. */
internal fun skipTiming() = assumeFalse("timing test (PII_SKIP_TIMING=1): it runs alone in a timing step", TestEnv.env("PII_SKIP_TIMING") == "1")

/** The median of five runs of [f], in seconds. */
internal fun medianTime(f: () -> Unit): Double {
    val times = DoubleArray(5) {
        val started = System.nanoTime()
        f()
        (System.nanoTime() - started) / 1e9
    }
    times.sort()
    return times[2]
}

/** Each doubling of the input costs at most three times as much (REVIEW-GUIDE rule 4). [time] gives the time at size n
 *  ([medianTime]), with sizes chosen so that one run takes tens of milliseconds. The largest size runs twice first as a
 *  warm-up (the JIT compiles the code then, not while it is timed), each size starts after a garbage collection, and
 *  sizes whose doublings look too slow are timed again before they fail (a busy machine can slow one size; time that
 *  grows with the square of the size fails both times). */
internal fun assertLinear(name: String, sizes: List<Int>, time: (Int) -> Double) {
    repeat(2) { time(sizes.last()) }
    fun measure(): List<Double> = sizes.map { n ->
        System.gc()
        time(n)
    }
    fun worst(t: List<Double>): Double = (1 until t.size).maxOf { t[it] / t[it - 1] }
    var times = measure()
    if (worst(times) > 3.0) times = measure()
    println("growth $name | n $sizes | ${times.joinToString { "%.1f ms".format(it * 1000) }} | worst doubling ${"%.2f".format(worst(times))}")
    for (i in 1 until times.size) {
        assertTrue("$name: n $sizes, seconds $times, doubling $i", times[i] / times[i - 1] <= 3.0)
    }
}
