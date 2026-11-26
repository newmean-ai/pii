"""Doubling test for the edges of a span the model found (review round of SDK 1.7.1, L2-B2): leaving out what a span's
edges hold that is not part of the value takes time in proportion to the span, however many brackets, quotation marks,
stops or spaces it has to look past. The time of one redact_with_vault at sizes n, 2n and 4n, each the median of five
runs, after a warm-up at the largest size and with sizes where one run takes tens of milliseconds; a doubling that costs
more than three times as much fails, after the sizes are timed again once (a busy machine can slow one size; time that
grows with the square of the size fails both times)."""
import time

import pytest

from newmean_pii import Span, Vault
from newmean_pii._redactor import redact_with_vault

pytestmark = pytest.mark.timing  # PII_SKIP_TIMING=1 skips every test of this file (conftest.py)

SHAPES = {
    "closing brackets after the value": lambda n: "Kim Minsu" + ")" * n,
    "opening brackets before the value": lambda n: "(" * n + "Kim Minsu",
    "pairs of brackets around the value": lambda n: "(" * n + "Kim Minsu" + ")" * n,
    "quotation marks around the value": lambda n: chr(34) * n + "Kim Minsu" + chr(39) * n,
    # (commas before the value: a "." at the start of a value stays)
    "stops and spaces around the value": lambda n: " ," * n + "Kim Minsu" + ", " * n,
    "brackets that are not one pair": lambda n: "(" + ")(" * n + "Kim Minsu" + ")(" * n + ")",
}


def _median(f):
    times = []
    for _ in range(5):
        t = time.perf_counter()
        f()
        times.append(time.perf_counter() - t)
    return sorted(times)[2]


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_span_edges_take_time_in_proportion_to_the_span(name):
    make = SHAPES[name]

    def timed(n):
        text = make(n)
        found = [Span(0, len(text), "PERSON", "model")]

        def run():
            out, _spans, _tags = redact_with_vault(text, found, Vault())
            assert "[PERSON_1]" in out

        return _median(run)

    sizes = (200_000, 400_000, 800_000)
    timed(sizes[-1])  # (warm up)
    a, b, c = (timed(n) for n in sizes)
    if b / a > 3 or c / b > 3:
        a, b, c = (timed(n) for n in sizes)
    print("growth span edges, %s | %s | %.1f ms, %.1f ms, %.1f ms" % (name, sizes, a * 1000, b * 1000, c * 1000))
    assert b / a <= 3 and c / b <= 3, (a, b, c)
