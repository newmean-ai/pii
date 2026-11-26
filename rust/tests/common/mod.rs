// Timing of the doubling tests (REVIEW-GUIDE rule 4), shared by the test functions with "growth" in their name, which
// tool/test-all.sh runs alone, after the suites.

/// True when PII_SKIP_TIMING=1: a test with an assertion on measured time then prints a note and returns at once.
/// Timing tests run alone, never inside a functional step (the 1.7.2 release standard, CI3): the functional step of
/// rust.yml sets the variable, and its timing step runs these tests one at a time.
pub fn skip_timing(name: &str) -> bool {
    let skip = std::env::var("PII_SKIP_TIMING").as_deref() == Ok("1");
    if skip {
        println!("{name}: a timing test, not run here (PII_SKIP_TIMING=1)");
    }
    skip
}

/// The median of five runs of `f`, in seconds.
pub fn median_time(mut f: impl FnMut()) -> f64 {
    let mut times: Vec<f64> = (0..5)
        .map(|_| {
            let t = std::time::Instant::now();
            f();
            t.elapsed().as_secs_f64()
        })
        .collect();
    times.sort_by(f64::total_cmp);
    times[2]
}

/// Each doubling of the input costs at most three times as much (REVIEW-GUIDE rule 4). `time(n)` is the time at size n
/// ([`median_time`]), with sizes chosen so that one run takes tens of milliseconds. The largest size runs first as a
/// warm-up, and sizes whose doublings look too slow are timed again before they fail (a busy machine can slow one size;
/// time that grows with the square of the size fails both times).
pub fn assert_linear(name: &str, sizes: &[usize], mut time: impl FnMut(usize) -> f64) {
    time(sizes[sizes.len() - 1]);
    let worst = |t: &[f64]| t.windows(2).map(|w| w[1] / w[0]).fold(0.0, f64::max);
    let mut times: Vec<f64> = sizes.iter().map(|&n| time(n)).collect();
    if worst(&times) > 3.0 {
        times = sizes.iter().map(|&n| time(n)).collect();
    }
    let ms: Vec<String> = times.iter().map(|t| format!("{:.1} ms", t * 1000.0)).collect();
    println!("growth {name} | n {sizes:?} | {} | worst doubling {:.2}", ms.join(", "), worst(&times));
    for (i, w) in times.windows(2).enumerate() {
        assert!(w[1] / w[0] <= 3.0, "{name}: n {sizes:?}, seconds {times:?}, doubling {} costs {:.2} times as much", i + 1, w[1] / w[0]);
    }
}
