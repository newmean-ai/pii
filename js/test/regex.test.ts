import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { regexSpans } from "../src/regex";

const FILE = new URL("./fixtures/regex.jsonl", import.meta.url);

describe.skipIf(!existsSync(FILE))("regex parity with the Python baseline", () => {
  it("finds the same spans", () => {
    const rows = readFileSync(FILE, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    const bad: string[] = [];
    for (const row of rows) {
      const units: number[] = [0];
      for (const ch of row.text as string) units.push(units[units.length - 1] + ch.length);
      const key = (a: { start: number; end: number; label: string }[]) => a.map((s) => `${s.start}-${s.end}:${s.label}`).sort().join(" ");
      const want = key(row.spans.map((s: any) => ({ ...s, start: units[s.start], end: units[s.end] })));
      const got = key(regexSpans(row.text));
      if (want !== got) bad.push(`${JSON.stringify(row.text).slice(0, 100)}\n  py ${want}\n  js ${got}`);
    }
    console.log(`${rows.length - bad.length}/${rows.length}\n${bad.slice(0, 5).join("\n")}`);
    expect(bad.length).toBe(0);
  });
});

describe("regex examples", () => {
  // the account-number rule (FINANCIAL) runs before PHONE and also takes 010-1234-5678; next to the model, the model's
  // label wins for such overlaps (combine)
  it("masks common formats", () => {
    const t = "메일 kim.minjun@example.co.kr 전화 010-1234-5678 카드 4111 1111 1111 1111 주민 900101-1234567";
    expect(regexSpans(t).map((s) => [t.slice(s.start, s.end), s.label])).toEqual([
      ["kim.minjun@example.co.kr", "EMAIL"],
      ["900101-1234567", "ID_NUMBER"],
      ["010-1234-5678", "FINANCIAL"],
      ["4111 1111 1111 1111", "FINANCIAL"],
    ]);
  });
});
