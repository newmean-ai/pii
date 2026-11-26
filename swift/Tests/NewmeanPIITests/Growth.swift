// Timing of the doubling tests (REVIEW-GUIDE rule 4), shared by the test classes with "Growth" in their name, which
// the test runner runs alone, after the suites.
import Foundation
import XCTest

/// Throws XCTSkip when PII_SKIP_TIMING=1: a test with an assertion on measured time (a doubling ratio or a time limit)
/// runs alone, never inside a functional step (the 1.7.2 release standard, CI3). The functional step of swift.yml sets
/// the variable, and its timing step runs these tests by name.
func skipTiming() throws {
  if ProcessInfo.processInfo.environment["PII_SKIP_TIMING"] == "1" {
    throw XCTSkip("timing test (PII_SKIP_TIMING=1): it runs alone in a timing step")
  }
}

/// The median of five runs of `f`, in seconds.
func medianTime(_ f: () throws -> Void) rethrows -> Double {
  var times: [Double] = []
  for _ in 0..<5 {
    let started: UInt64 = DispatchTime.now().uptimeNanoseconds
    try f()
    times.append(Double(DispatchTime.now().uptimeNanoseconds - started) / 1e9)
  }
  times.sort()
  return times[2]
}

/// Each doubling of the input costs at most three times as much (REVIEW-GUIDE rule 4). `time(n)` is the time at size n
/// (`medianTime`), with sizes chosen so that one run takes tens of milliseconds. The largest size runs first as a
/// warm-up, and sizes whose doublings look too slow are timed again before they fail (a busy machine can slow one size;
/// time that grows with the square of the size fails both times).
func assertLinear(_ name: String, _ sizes: [Int], file: StaticString = #filePath, line: UInt = #line, _ time: (Int) throws -> Double) rethrows {
  _ = try time(sizes[sizes.count - 1])
  func worst(_ t: [Double]) -> Double {
    var w: Double = 0
    for i in 1..<t.count { w = max(w, t[i] / t[i - 1]) }
    return w
  }
  var times: [Double] = try sizes.map(time)
  if worst(times) > 3.0 { times = try sizes.map(time) }
  let ms: [String] = times.map { String(format: "%.1f ms", $0 * 1000) }
  print("growth \(name) | n \(sizes) | \(ms.joined(separator: ", ")) | worst doubling \(String(format: "%.2f", worst(times)))")
  for i in 1..<times.count {
    XCTAssertLessThanOrEqual(times[i] / times[i - 1], 3.0, "\(name): n \(sizes), seconds \(times), doubling \(i)", file: file, line: line)
  }
}
