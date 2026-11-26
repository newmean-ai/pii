import { existsSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { REGEX_RULES, regexSpans } from "../src/regex";
import type { Span } from "../src/types";

// A test with an assertion on measured time (a doubling ratio or a time limit) runs alone, never inside a functional
// step (the 1.7.2 release standard, CI3): PII_SKIP_TIMING=1 skips it (the functional steps of public CI), and
// PII_TIMING_ONLY=1 runs it and none of this file's other tests (vault.yml's timing job, with --allowOnly).
const timingIt = process.env.PII_SKIP_TIMING === "1" ? it.skip : process.env.PII_TIMING_ONLY === "1" ? it.only : it;

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

// The matcher of 1.6.0 and earlier, kept as the reference: regexSpans must give its spans, in its order. It tried the
// EMAIL pattern again from every character of a run without "@" and compared each match with all earlier spans, so its
// time grew with the square of the input (fixed in 1.6.1).
function legacyRegexSpans(text: string, rules: readonly (readonly [string, RegExp])[] = REGEX_RULES): Span[] {
  const out: Span[] = [];
  for (const [label, re] of rules) {
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      const start = m.index!;
      const end = start + m[0].length;
      if (out.some((s) => start < s.end && s.start < end)) continue;
      out.push({ start, end, label, source: "regex" });
    }
  }
  return out;
}

describe("regexSpans equals the 1.6.0 matcher", () => {
  const builtin = REGEX_RULES.map(([label, pattern]): [string, RegExp] => [label, pattern]);
  afterEach(() => void REGEX_RULES.splice(0, REGEX_RULES.length, ...builtin));

  it("on every string of up to 7 characters over a 6-character alphabet", () => {
    const alphabet = ["a", "@", ".", "-", "+", " "];
    let checked = 0;
    const bad: string[] = [];
    const check = (text: string, left: number): void => {
      checked++;
      if (JSON.stringify(regexSpans(text)) !== JSON.stringify(legacyRegexSpans(text))) bad.push(text);
      if (left > 0) for (const c of alphabet) check(text + c, left - 1);
    };
    check("", 7);
    expect(bad.slice(0, 5)).toEqual([]);
    expect(checked).toBe(335923);
  });

  it("on 40,000 random strings with Unicode letters, digits, marks, emoji, lone surrogates and separators", () => {
    const alphabet = [
      "a", "Z", "7", "@", "@", ".", ".", "+", "-", "_", " ", "\n", " ", "한", "é", "𐐀", "𝟙", "①", "́", "😀", "\ud800", "\udc00",
      ":", "/", "(", ")", "w", "0", "1", "http://", "www.",
    ];
    let state = 20261117;
    const random = (limit: number): number => {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return Math.floor((state / 0x100000000) * limit);
    };
    const bad: string[] = [];
    for (let i = 0; i < 40000; i++) {
      let text = "";
      for (let n = random(60); n > 0; n--) text += alphabet[random(alphabet.length)];
      if (JSON.stringify(regexSpans(text)) !== JSON.stringify(legacyRegexSpans(text))) bad.push(JSON.stringify(text));
    }
    expect(bad.slice(0, 5)).toEqual([]);
  });

  it.each([
    "a@b.co@d.e", "a@b.co-+x@c.de", "a@b..co", "a@@b.co", "a@b.co.", ".a+@b.co", "a@b", "@b.co", "a@.co", "a@b.c@d.e.f",
    "😀 김+𐐀@회사.한국 𝟙@𐐀.한", "é@a.co ①@b.co a@b.①", "a\ud800@b.co a@b.\udc00",
    "www.site.test/x@y.co 010-1234-5678 https://other.test", "a@b.co http://one.test a@b.co",
    "900101-1234567 4111 1111 1111 1111 +1 (212) 555-1234", "https://a@b.co/x?mail=c@d.co",
  ])("on %j", (text) => {
    expect(regexSpans(text)).toEqual(legacyRegexSpans(text));
  });

  it("with rules replaced, reordered or added, also where matches are empty", () => {
    const email = builtin[0][1];
    const sets: [string, RegExp][][] = [
      [["EMAIL", /CUSTOM/gu]],
      [builtin[1], ["CONTACT", email]],
      [["EMPTY", /(?=b)/gu], ["REPLACED", /ab/gu], ["TAIL", /b|c/gu]],
      [["A", /a*/gu], ["B", /b*/gu], ["ALL", /./gu]],
      [["AB", /ab/gu], ["POINT", /(?=a)|(?=b)/gu], ["ALL", /./gu]],
      [["POINT", /(?=a)/gu], ["ALL", /./gu], ["A", /a/gu]],
      [...builtin, ["WORD", /[a-z]+/gu]],
    ];
    for (const set of sets) {
      REGEX_RULES.splice(0, REGEX_RULES.length, ...set);
      for (const text of ["", "a", "b", "ab", "abc", "baba", "😀ab😀a", "CUSTOM a@b.co", "https://a@b.co x@y.co"]) {
        expect(regexSpans(text), `${set.map((r) => r[0])} ${JSON.stringify(text)}`).toEqual(legacyRegexSpans(text, set));
      }
    }
    email.lastIndex = 99;
    REGEX_RULES.splice(0, REGEX_RULES.length, ...builtin);
    expect(regexSpans("x@y.co")).toEqual([{ start: 0, end: 6, label: "EMAIL", source: "regex" }]);
    expect(email.lastIndex).toBe(0);
  });
});

describe("regexSpans on long inputs", () => {
  // 1.6.0 needed about 5 s for the first text and 3 s for the last on the machine that takes a few ms here
  timingIt("takes time in proportion to the length", () => {
    const t0 = performance.now();
    for (const text of ["a".repeat(100000), "a@" + "b".repeat(100000), "@".repeat(100000), "a.".repeat(50000), "1".repeat(100000)]) {
      expect(regexSpans(text).filter((s) => s.label === "EMAIL")).toEqual([]);
    }
    const text = "a@b.co ".repeat(40000);
    const spans = regexSpans(text);
    expect(spans).toHaveLength(40000);
    expect(spans[0]).toEqual({ start: 0, end: 6, label: "EMAIL", source: "regex" });
    expect(spans[39999]).toEqual({ start: 279993, end: 279999, label: "EMAIL", source: "regex" });
    expect(performance.now() - t0).toBeLessThan(2000);
  });
});
