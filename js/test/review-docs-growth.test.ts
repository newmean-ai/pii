// Doubling test for the edges of a span the model found (review round of SDK 1.7.1, L2-B2): leaving out what a span's
// edges hold that is not part of the value takes time in proportion to the span, however many brackets, quotation marks,
// stops or spaces it has to look past. The time of one redactWithVault at sizes n, 2n and 4n, each the median of five
// runs, after a warm-up at the largest size and with sizes where one run takes tens of milliseconds; a doubling that costs
// more than three times as much fails, after the sizes are timed again once (a busy machine, as when every suite runs at
// once, can slow one size; time that grows with the square of the size fails both times).
import { describe, expect, it } from "vitest";
import { redactWithVault } from "../src/redactor";
import { createVault } from "../src/vault";

const median = (f: () => void) => {
  const times: number[] = [];
  for (let i = 0; i < 5; i++) {
    const t = performance.now();
    f();
    times.push(performance.now() - t);
  }
  return times.sort((a, b) => a - b)[2];
};

const SHAPES: [string, (n: number) => string][] = [
  ["closing brackets after the value", (n) => "Kim Minsu" + ")".repeat(n)],
  ["opening brackets before the value", (n) => "(".repeat(n) + "Kim Minsu"],
  ["pairs of brackets around the value", (n) => "(".repeat(n) + "Kim Minsu" + ")".repeat(n)],
  ["quotation marks around the value", (n) => '"'.repeat(n) + "Kim Minsu" + "'".repeat(n)],
  // (commas before the value: a "." at the start of a value stays)
  ["stops and spaces around the value", (n) => " ,".repeat(n) + "Kim Minsu" + ", ".repeat(n)],
  ["brackets that are not one pair", (n) => "(" + ")(".repeat(n) + "Kim Minsu" + ")(".repeat(n) + ")"],
];

describe.skipIf(process.env.PII_SKIP_TIMING === "1")("doubling: the edges of a found span (L2-B2)", () => {
  it.each(SHAPES)("%s", (_name, make) => {
    const time = (n: number) => {
      const text = make(n);
      const found = [{ start: 0, end: text.length, label: "PERSON", source: "model" as const }];
      return median(() => {
        const out = redactWithVault(text, found, createVault());
        expect(out.text).toContain("[PERSON_1]");
      });
    };
    const sizes = [400_000, 800_000, 1_600_000];
    time(sizes[2]); // (warm up)
    const measure = () => sizes.map(time);
    let [a, b, c] = measure();
    if (b / a > 3 || c / b > 3) [a, b, c] = measure();
    expect(b / a, `${a} ${b} ${c}`).toBeLessThanOrEqual(3);
    expect(c / b, `${a} ${b} ${c}`).toBeLessThanOrEqual(3);
  }, 120_000);
});
