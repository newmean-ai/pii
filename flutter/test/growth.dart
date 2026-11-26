// Timing of the doubling tests (REVIEW-GUIDE rule 4), shared by the test files with "growth" in their name, which
// the test runner runs alone, after the suites.
import 'dart:io';

import 'package:test/test.dart';

/// True when PII_SKIP_TIMING=1: a test with an assertion on measured time (a doubling ratio or a time limit) then marks
/// itself skipped and returns at once. Such a test runs alone, never inside a functional step (the 1.7.2 release
/// standard, CI3): the functional steps of flutter.yml set the variable, and its timing step runs these tests by name.
bool skipTiming() {
  if (Platform.environment['PII_SKIP_TIMING'] != '1') return false;
  markTestSkipped('timing test (PII_SKIP_TIMING=1): it runs alone in a timing step');
  return true;
}

/// The median of five runs of [f], in seconds.
double medianTime(void Function() f) {
  final times = <double>[];
  for (var i = 0; i < 5; i++) {
    final w = Stopwatch()..start();
    f();
    times.add(w.elapsedMicroseconds / 1e6);
  }
  times.sort();
  return times[2];
}

/// Each doubling of the input costs at most three times as much (REVIEW-GUIDE rule 4). [time] gives the time at size n
/// ([medianTime]), with sizes chosen so that one run takes tens of milliseconds. The largest size runs first as a warm-up,
/// and sizes whose doublings look too slow are timed again before they fail (a busy machine can slow one size; time
/// that grows with the square of the size fails both times).
void expectLinear(String name, List<int> sizes, double Function(int) time) {
  time(sizes.last);
  double worst(List<double> t) {
    var w = 0.0;
    for (var i = 1; i < t.length; i++) {
      if (t[i] / t[i - 1] > w) w = t[i] / t[i - 1];
    }
    return w;
  }

  var times = [for (final n in sizes) time(n)];
  if (worst(times) > 3.0) times = [for (final n in sizes) time(n)];
  final ms = [for (final t in times) '${(t * 1000).toStringAsFixed(1)} ms'].join(', ');
  print('growth $name | n $sizes | $ms | worst doubling ${worst(times).toStringAsFixed(2)}');
  for (var i = 1; i < times.length; i++) {
    expect(times[i] / times[i - 1], lessThanOrEqualTo(3.0), reason: '$name: n $sizes, seconds $times, doubling $i');
  }
}
