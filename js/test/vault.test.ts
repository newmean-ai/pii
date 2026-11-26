import { describe, expect, it } from "vitest";
import { applyRedaction, modelValues, redactReadings, redactWithVault, vaultUnchanged, withVaultSpans } from "../src/redactor";
import { applyVault, createVault, escapedReadings, escapeTags, jsonEscapesView, jsonTokens, plainNumber, restoreAnswer, restoreJson, unchangedLength, vaultReach, type Vault } from "../src/vault";
import type { Span } from "../src/types";

const span = (text: string, value: string, label: string, from = 0): Span => {
  const start = text.indexOf(value, from);
  return { start, end: start + value.length, label, source: "model" };
};

import CASES from "../../python/tests/vault_cases.json";

// A test with an assertion on measured time (a doubling ratio or a time limit) runs alone, never inside a functional
// step (the 1.7.2 release standard, CI3): PII_SKIP_TIMING=1 skips it (the functional steps of public CI), and
// PII_TIMING_ONLY=1 runs it and none of this file's other tests (vault.yml's timing job, with --allowOnly).
const timingIt = process.env.PII_SKIP_TIMING === "1" ? it.skip : process.env.PII_TIMING_ONLY === "1" ? it.only : it;

describe("vault: shared cases (also run by the Python package)", () => {
  for (const g of CASES.groups as any[]) {
    it(g.name, () => {
      const v = createVault();
      expect(g.values.map(([label, value]: [string, string]) => v.tag(label, value))).toEqual(g.tags);
      for (const c of g.redact ?? []) {
        // spans a model found (then the vault's known values): each over the next place its value is written
        let at = 0;
        const spans = c.found.map(([value, label]: [string, string]) => {
          const i = c.text.indexOf(value, at);
          at = i + value.length;
          return { start: i, end: i + value.length, label, source: "model" as const };
        });
        const got = redactWithVault(c.text, spans, v);
        expect(got.text).toBe(c.want);
        // (the final spans as covered text, label and source, when the case gives them)
        if (c.spans) expect(got.spans.map((s) => [c.text.slice(s.start, s.end), s.label, s.source])).toEqual(c.spans);
      }
      if (g.entries) expect(v.entries().map((e) => e.value)).toEqual(g.entries);
      for (const c of g.hide ?? []) expect(v.hide(c.text)).toBe(c.want);
      for (const c of g.hide_escaped ?? []) expect(v.hide(c.text, { escape: true })).toBe(c.want);
      for (const c of g.hide_loose ?? []) expect(v.hide(c.text, { loose: true })).toBe(c.want);
      for (const c of g.hide_keep ?? []) expect(v.hide(c.text, { escape: true, keep: new Set(c.keep) })).toBe(c.want);
      for (const c of g.hide_value ?? []) expect(v.hideValue(c.value)).toEqual(c.want);
      for (const c of g.restore ?? []) expect(v.restore(c.text, { json: c.json, only: c.only && new Set(c.only) })).toBe(c.want);
    });
  }
});

describe("vault", () => {
  it("restores inside JSON text and JSON values", () => {
    const v = createVault();
    v.tag("PERSON", 'Ann "A" O\'Neil');
    const json = '{"to":"[PERSON_1]","n":1}';
    expect(JSON.parse(v.restore(json, { json: true }))).toEqual({ to: 'Ann "A" O\'Neil', n: 1 });
    expect(v.restoreValue({ to: ["[PERSON_1]", 2], k: { x: "hi [PERSON_1]" } })).toEqual({ to: ['Ann "A" O\'Neil', 2], k: { x: 'hi Ann "A" O\'Neil' } });
  });

  it("restores a stream with placeholders split across chunks", () => {
    const v = createVault();
    v.tag("PERSON", "Kim Minsu");
    v.tag("EMAIL", "kim@example.com");
    const text = "Dear [PERSON_1], we wrote to [EMAIL_1]. [x] [";
    for (const size of [1, 2, 3, 5, 7, 100]) {
      const r = v.restorer();
      let out = "";
      for (let i = 0; i < text.length; i += size) out += r.push(text.slice(i, i + size));
      out += r.flush();
      expect(out).toBe("Dear Kim Minsu, we wrote to kim@example.com. [x] [");
      const only = v.restorer({ only: new Set(["[EMAIL_1]"]) });
      expect(only.push("[PERSON_1] [EMAIL") + only.push("_1]") + only.flush()).toBe("[PERSON_1] kim@example.com");
    }
  });

  it("keeps and reloads its entries, and continues the numbering", () => {
    const v = createVault();
    v.tag("PERSON", "A. Kim");
    v.tag("EMAIL", "a@x.io");
    const w = createVault(v.entries());
    expect(w.restore("[PERSON_1] [EMAIL_1]")).toBe("A. Kim a@x.io");
    expect(w.tag("PERSON", "B. Lee")).toBe("[PERSON_2]");
    expect(() => createVault([{ tag: "PERSON_1", label: "PERSON", value: "x" }])).toThrow(/not a vault entry/);
    const g = w.generation;
    w.clear();
    expect(w.size).toBe(0);
    expect(w.generation).toBe(g + 1);
    expect(w.restore("[PERSON_1]")).toBe("[PERSON_1]");
  });

  it("applyRedaction numbers spans, and withVaultSpans masks known values the model missed", () => {
    const v = createVault();
    const a = "Kim Minsu called Lee.";
    expect(applyRedaction(a, [span(a, "Kim Minsu", "PERSON"), span(a, "Lee", "PERSON")], { vault: v, mode: "mask" })).toBe(
      "[PERSON_1] called [PERSON_2].",
    );
    const b = "Then Kim Minsu wrote back to Park.";
    const spans = withVaultSpans(b, [span(b, "Park", "PERSON")], v);
    expect(spans.map((s) => [b.slice(s.start, s.end), s.source])).toEqual([
      ["Kim Minsu", "vault"],
      ["Park", "model"],
    ]);
    expect(applyRedaction(b, spans, { vault: v })).toBe("Then [PERSON_1] wrote back to [PERSON_3].");
  });
});

import { existsSync, readFileSync } from "node:fs";
import { createRedactor } from "../src/redactor";
const MODELS = process.env.PII_MODELS ?? new URL("./models/", import.meta.url).pathname;
const localFetch = (async (url: string) => new Response(readFileSync(url.replace("file://", "")))) as unknown as typeof fetch;

describe("vault: fifth review", () => {
  const span = (text: string, value: string, label: string, from = 0): Span => {
    const start = text.indexOf(value, from);
    return { start, end: start + value.length, label, source: "model" } as Span;
  };

  it("masks a value learned from a text wherever it occurs again in that text", () => {
    const v = createVault();
    const t = "I met Alice yesterday. Use the word Alice twice, not Alicette.";
    const out = redactWithVault(t, [span(t, "Alice", "PERSON")], v);
    expect(out.text).toBe("I met [PERSON_1] yesterday. Use the word [PERSON_1] twice, not Alicette.");
    expect(out.spans.map((s) => [s.start, s.source])).toEqual([[6, "model"], [36, "vault"]]);
    expect(out.tags).toEqual(["[PERSON_1]", "[PERSON_1]"]);
    // a merged value learned in the same text is found again too
    const w = createVault();
    w.tag("PERSON", "Alex Kim");
    const u = "Alex Kim Lee wrote. Kim Lee signed. Alex Kim Lee left.";
    const merged = redactWithVault(u, [span(u, "Kim Lee", "PERSON")], w);
    expect(merged.text).not.toMatch(/Alex|Kim|Lee/);
    expect(w.restore(merged.text)).toBe(u);
  });

  it("keeps a family with digits in its name apart from the numbers of another", () => {
    const v = createVault([
      { tag: "[PERSON_1]", label: "PERSON", value: "Alice" },
      { tag: "[PERSON_1_99999999999]", label: "PERSON_1", value: "Last" },
    ]);
    expect(v.tag("PERSON_1", "Next")).toBe("[PERSON_1_100000000000]");
    expect(v.restore("[PERSON_1] [PERSON_1_100000000000]")).toBe("Alice Next");
    expect(v.tag("A😀B", "x")).toBe("[A_B_1]"); // one "_" per character that is not allowed, as in every package
  });

  it("copies replay records and drops a key that got two different wordings", () => {
    const v = createVault();
    const args = { q: "none" };
    v.remember("call", args);
    args.q = "changed later";
    const got = v.recall("call") as { q: string };
    expect(got).toEqual({ q: "none" });
    got.q = "changed after recall";
    expect(v.recall("call")).toEqual({ q: "none" });
    v.remember("x42y", "x[ID_NUMBER_1]y");
    v.remember("x42y", "x[ID_NUMBER_1]y"); // the same wording again is fine
    expect(v.recall("x42y")).toBe("x[ID_NUMBER_1]y");
    v.remember("x42y", "x42y"); // another wording for the same text: no record can be trusted
    expect(v.recall("x42y")).toBeUndefined();
    v.remember("x42y", "x[ID_NUMBER_1]y");
    expect(v.recall("x42y")).toBeUndefined(); // and it stays that way
    expect(createVault(JSON.parse(JSON.stringify(v))).recall("x42y")).toBeUndefined();
  });

  it("goes on until nothing new is found, however many rounds that takes", () => {
    const v = createVault();
    v.tag("PERSON", "W00");
    const words = Array.from({ length: 10 }, (_, i) => `W${String(i).padStart(2, "0")}`);
    // growing lines; in each the model finds only the last five characters
    const tail = (line: string): Span[] => [{ start: line.length - 5, end: line.length, label: "PERSON", source: "model" } as Span];
    let line = "";
    for (let n = 2; n <= words.length; n++) {
      line = words.slice(0, n).join(" ");
      expect(redactWithVault(line, tail(line), v).text).not.toMatch(/W\d\d/);
    }
    const again = redactWithVault(line, tail(line), v);
    expect(again.text).not.toMatch(/W\d\d/);
    expect(v.restore(again.text)).toBe(line);
  });

  it("restores a placeholder written with JSON escapes, and keeps the other tokens as written", () => {
    const v = createVault();
    v.tag("PERSON", 'A "B"');
    expect(restoreJson(v, '{"who":"\\u005bPERSON_1]","n":9007199254740993,"[PERSON_1]":"x\\n"}')).toBe(
      '{"who":"A \\"B\\"","n":9007199254740993,"[PERSON_1]":"x\\n"}',
    );
  });

  it("restores a stream with the values of the moment the restorer was made", () => {
    const v = createVault();
    v.tag("PERSON", "Alice");
    const all = v.restorer();
    const some = v.restorer({ only: new Set(["[PERSON_1]"]) });
    const first = all.push("[PERSON_1] and [PER") + some.push("[PERSON_1]");
    v.clear();
    v.tag("PERSON", "Bob");
    expect(first + all.push("SON_1]") + all.flush() + some.push(" [PERSON_1]") + some.flush()).toBe("Alice and AliceAlice Alice");
  });

  it("reads a Unicode escape wherever it stands, also behind a stray quotation mark and in JSON inside JSON", () => {
    const v = createVault();
    v.tag("PERSON", "Zoë");
    for (const text of ['24" monitor: {"name":"Zo\\u00eb"}', 'audit 7: {"row":"{\\"name\\":\\"Zo\\\\u00eb\\"}"}', "bare Zo\\u00eb here"]) {
      const view = jsonEscapesView(text)!;
      expect(view.text).toContain("Zoë");
      const at = view.text.indexOf("Zoë");
      const out = redactReadings(text, [], v, { through: { view, found: [{ start: at, end: at + 3, label: "PERSON", source: "model" }] } }).text;
      expect(out).toContain("[PERSON_1]");
      expect(out).not.toMatch(/u00eb/);
    }
    expect(restoreAnswer(v, '"\\u005bPERSON_1] and [PERSON_1]"')).toBe('"Zoë and Zoë"');
    const q = createVault();
    q.tag("PERSON", 'A"B');
    expect(JSON.parse(restoreAnswer(q, '"[PERSON_1]"'))).toBe('A"B');
    expect(restoreAnswer(q, '"Hi" said [PERSON_1]')).toBe('"Hi" said A"B'); // not JSON: as it is
  });

  it("reads JSON tokens, and text through the escapes of the JSON strings in it", () => {
    const t = '{"a": "x\\u00e9", "n": -12.5e3, "k": [1, "2"], "t": true}';
    expect(jsonTokens(t)!.map((x) => [x.kind, x.value, x.key])).toEqual([
      ["key", "a", undefined], ["string", "xé", "a"], ["key", "n", undefined], ["number", "-12.5e3", "n"],
      ["key", "k", undefined], ["number", "1", "k"], ["string", "2", "k"], ["key", "t", undefined],
    ]);
    expect(jsonTokens('{"a":')).toBeUndefined();
    // a value stands under the key of the object or array around it
    expect(jsonTokens('{"u":[{"note":"box"},"blue",[1,{"k":"v"},"z"]],"o":{"p":{"q":1},"r":2},"s":"t"}')!.filter((x) => x.kind !== "key").map((x) => [x.value, x.key])).toEqual([
      ["box", "note"], ["blue", "u"], ["1", "u"], ["v", "k"], ["z", "u"], ["1", "q"], ["2", "r"], ["t", "s"],
    ]);
    expect(jsonTokens('["a",{"k":"b"},"c"]')!.filter((x) => x.kind !== "key").map((x) => [x.value, x.key])).toEqual([["a", undefined], ["b", "k"], ["c", undefined]]);
    expect(jsonEscapesView("no escapes \"here\"")).toBeUndefined();
    expect(jsonEscapesView("a path C:\\new is not a JSON string")).toBeUndefined();
    const text = 'say "Jos\\u00e9 \\ud83d\\ude00\\n" and "plain"';
    const view = jsonEscapesView(text)!;
    expect(view.text).toBe('say "José 😀\\n" and "plain"'); // Unicode escapes only: "\n" stays as it is written
    expect(view.map.length).toBe(view.text.length + 1);
    const v = createVault();
    const at = view.text.indexOf("José");
    const out = redactReadings(text, [], v, { through: { view, found: [{ start: at, end: at + 7, label: "PERSON", source: "model" }] } }); // "José 😀"
    expect(out.text).toBe('say "[PERSON_1]\\n" and "plain"');
    expect(v.value("[PERSON_1]")).toBe("José 😀");
  });

  timingIt("reads text in its other ways without pairing quotation marks, and numbers digit for digit", () => {
    const readings = (t: string) => escapedReadings(t).map((r) => r.text);
    expect(readings("no backslash here")).toEqual([]);
    expect(readings('24" monitor; msg="Dear\\nkim_0142"')).toEqual(['24" monitor; msg="Dear\nkim_0142"']);
    expect(readings('{"data":"{\\"v\\":\\"a\\\\nb\\"}"}')[0]).toBe('{"data":"{"v":"a\nb"}"}'); // any number of backslashes
    expect(readings('"ACME\\\\tkim"')).toEqual(['"ACME\tkim"', '"ACME\\tkim"']); // all decoded; pairs read as one
    for (const r of escapedReadings('a\\n"b\\\\u00e9\\\\\\\\c')) expect(r.map.length).toBe(r.text.length + 1);
    // unescaped as a JSON string is, up to three times over; a backslash that starts no escape stays
    const raw = String.raw;
    expect(readings(raw`C:\\\\Users\\\\kim`)).toEqual([raw`C:\\Users\\kim`, raw`C:\Users\kim`]);
    expect(readings(raw`a\\\\b`)).toEqual(["a\b", raw`a\\b`, raw`a\b`]); // every escape; then two backslashes, then one
    expect(readings(raw`C:\dir\x`)).toEqual([]); // nothing to read otherwise
    expect(readings(raw`{'note': 'Call O\'Brien re: "urgent"'}`)).toEqual([`{'note': 'Call O'Brien re: "urgent"'}`]); // a Python repr
    expect(readings(raw`\\\uae40`)).toEqual(["김", raw`\김`]); // three backslashes: the letter; a backslash and the letter
    // through the Unicode escapes only, the backslashes before an escape's own stay
    expect(jsonEscapesView(raw`CORP\\\uae40`)!.text).toBe(raw`CORP\\김`);
    expect(jsonEscapesView(raw`x\\u00e9 y\u00e9 z\\\\u00e9`)!.text).toBe("xé yé zé");
    const t0 = performance.now();
    escapedReadings("\\".repeat(500_000) + "n");
    escapedReadings('"\\n'.repeat(200_000));
    expect(performance.now() - t0).toBeLessThan(3000);
    expect(["8.21023456789e11", "821023456789.0", "821023456789.00", "0821023456789", "8210234567890e-1"].map(plainNumber)).toEqual(Array(5).fill("821023456789"));
    expect(["9007199254740993", "-0.0", "1.5", "1e400", "12abc"].map(plainNumber)).toEqual(["9007199254740993", "0", "1.5", "1e400", "12abc"]);
    // plain decimal form, also for what is not an integer; zero is zero
    expect(["1e-3", "1.25e1", "0e-1", "-0e64", "1.50", "-12.340e-1", "0.5e1", "100e-2", "1.000"].map(plainNumber)).toEqual(["0.001", "12.5", "0", "0", "1.5", "-1.234", "5", "1", "1"]);
    expect(["0e65", "-0e99999", "1e65"].map(plainNumber)).toEqual(["0e65", "-0e99999", "1e65"]); // beyond the bound: as written, zero too
    const long = performance.now(); // a long fraction with zeros in it: one pass
    for (const n of [50_000, 100_000, 200_000]) expect(plainNumber(`0.1${"0".repeat(n)}1`)).toBe(`0.1${"0".repeat(n)}1`);
    expect(performance.now() - long).toBeLessThan(1500);
    expect(plainNumber(`1e${"0".repeat(4400)}1`)).toBe("10"); // an exponent of any length
    expect(plainNumber(`1e${"9".repeat(4400)}`)).toBe(`1e${"9".repeat(4400)}`);
  });

  it("keeps the replay records used last", () => {
    const v = createVault();
    v.remember("first", "FIRST");
    for (let i = 0; i < 4095; i++) v.remember(`k${i}`, i);
    expect(v.recall("first")).toBe("FIRST"); // used: now the last to go
    v.remember("one more", 1);
    expect(v.recall("first")).toBe("FIRST");
    expect(v.recall("k0")).toBeUndefined();
  });
});

describe("vault: fourth review", () => {
  it("gives an empty value no placeholder, and a family whose numbers are used up goes on in the next one", () => {
    const v = createVault([{ tag: "[PERSON_999999999999]", label: "PERSON", value: "Last One" }]);
    expect(v.tag("PERSON", "")).toBe("");
    expect(v.tag("PERSON", "One More")).toBe("[PERSON__1]");
    expect(v.tag("PERSON", "And Another")).toBe("[PERSON__2]");
    expect(v.restore("[PERSON__1] [PERSON_999999999999]")).toBe("One More Last One");
    expect(v.hide("One More and Last One")).toBe("[PERSON__1] and [PERSON_999999999999]");
    expect(applyRedaction("One More", [{ start: 0, end: 8, label: "PERSON", source: "model" }], { vault: v })).toBe("[PERSON__1]");
    // a state the vault gave out can always be loaded again
    const again = createVault(JSON.parse(JSON.stringify(v)));
    expect(again.tag("PERSON", "A Fourth")).toBe("[PERSON__3]");
    const full = "P".repeat(32);
    const w = createVault([{ tag: `[${full}_999999999999]`, label: full, value: "x" }]);
    expect(w.tag(full, "y")).toBe(`[${"P".repeat(31)}__1]`);
    const last = `A${"_".repeat(31)}`; // a letter and underscores only: on to the next letter
    const z = createVault([{ tag: `[${last}_999999999999]`, label: last, value: "one" }]);
    expect(z.tag(last, "two")).toBe(`[B${"_".repeat(31)}_1]`);
    expect(z.restore(`[B${"_".repeat(31)}_1]`)).toBe("two");
    // after Z it goes on with A; with all 26 used up the value is masked and not put back
    const letter = (c: string) => `${c}${"_".repeat(31)}`;
    const wrap = createVault([{ tag: `[${letter("Z")}_999999999999]`, label: letter("Z"), value: "one" }]);
    expect(wrap.tag(letter("Z"), "two")).toBe(`[${letter("A")}_1]`);
    expect(wrap.restore(`[${letter("A")}_1]`)).toBe("two");
    const letters = Array.from({ length: 26 }, (_, i) => String.fromCharCode(65 + i));
    const none = createVault(letters.map((c) => ({ tag: `[${letter(c)}_999999999999]`, label: letter(c), value: `v${c}` })));
    expect(none.tag(letter("Q"), "more")).toBe("[Q]");
    expect(none.restore("[Q]")).toBe("[Q]");
    expect(none.tag("PERSON", "Kim")).toBe("[PERSON_1]"); // other families are not touched
    expect(() => createVault([{ tag: "[PERSON_1000000000000]", label: "PERSON", value: "x" }])).toThrow(/not a vault entry/);
    // made-up placeholders: a number near the ones in use is skipped, one far above cannot use the family up
    const m = createVault();
    m.remember("answer", "see [PERSON_999999999999] and [PERSON__999999999999] and [PERSON_3]");
    expect(m.tag("PERSON", "Kim")).toBe("[PERSON_4]");
    expect(createVault(JSON.parse(JSON.stringify(m))).tag("PERSON", "Lee")).toBe("[PERSON_5]");
  });

  it("keeps its whole state as JSON, and remembers only in the generation it is given", () => {
    const v = createVault();
    v.tag("PERSON", "Alice");
    expect(v.remember("Hi Alice", "Hi [PERSON_1]", v.generation)).toBe(true);
    expect(v.remember("late", "x", v.generation - 1)).toBe(false);
    const w = createVault(JSON.parse(JSON.stringify(v)));
    expect(w.entries()).toEqual(v.entries());
    expect(w.recall("Hi Alice")).toBe("Hi [PERSON_1]");
    expect(w.recall("late")).toBeUndefined();
    expect(createVault(v.entries()).recall("Hi Alice")).toBeUndefined();
  });

  it("applies spans around kept placeholders and reports the placeholders it put in", () => {
    const v = createVault();
    v.tag("PERSON", "Kim Minsu");
    const t = "[PERSON_1] mail kim@example.com [PERSON_1]x";
    const out = applyVault(t, [{ start: 0, end: 31, label: "EMAIL" }], v, new Set(["[PERSON_1]"]));
    expect(out).toEqual({ text: "[PERSON_1][EMAIL_1] [PERSON_1]x", tags: ["[EMAIL_1]"] });
    expect(v.value("[EMAIL_1]")).toBe(" mail kim@example.com");
    expect(applyVault("[PERSON_1] [PERSON_1]", [{ start: 0, end: 21, label: "PERSON" }], v, new Set(["[PERSON_1]"]))).toEqual({ text: "[PERSON_1] [PERSON_1]", tags: [] });
  });

  it("restores JSON text in string values only", () => {
    const v = createVault();
    v.tag("PERSON", 'Ann "A"');
    expect(restoreJson(v, '{"[PERSON_1]": ["[PERSON_1]", 9007199254740993, "a\\"[PERSON_1]"]}')).toBe(
      '{"[PERSON_1]": ["Ann \\"A\\"", 9007199254740993, "a\\"Ann \\"A\\""]}',
    );
    expect(restoreJson(v, '"[PERSON_1]"')).toBe('"Ann \\"A\\""');
    expect(restoreJson(v, '{"a": "[PERSON_1]"')).toBe('{"a": "[PERSON_1]"');
    expect(restoreJson(v, "[PERSON_1]")).toBe("[PERSON_1]");
  });

  it("fails a redact whose vault was cleared while the model read the text", async () => {
    const v = createVault();
    expect(() => vaultUnchanged(v, v.generation)).not.toThrow();
    const started = v.generation;
    v.clear();
    expect(() => vaultUnchanged(v, started)).toThrow(/vault was cleared/);
  });
});

describe("vault: third review", () => {
  it("hides overlapping known values together under a new placeholder", () => {
    const v = createVault();
    v.tag("PERSON", "Alex Kim");
    v.tag("PERSON", "Kim Lee");
    expect(v.hide("Hi Alex Kim Lee!")).toBe("Hi [PERSON_3]!");
    expect(v.value("[PERSON_3]")).toBe("Alex Kim Lee");
  });

  it("escapes placeholder-like text only where nothing was masked, after detection on the original text", () => {
    const v = createVault();
    const t = "account [REF_1] cobalt, see [REF_1]";
    const spans = [{ start: 0, end: 22, label: "SECRET", source: "block" as const }];
    expect(applyRedaction(t, spans, { vault: v })).toBe("[SECRET_1], see \uff3bREF_1]");
    expect(v.value("[SECRET_1]")).toBe("account [REF_1] cobalt");
    expect(v.hide("Alex [X_1]", { escape: true })).toBe("Alex \uff3bX_1]");
  });

  it("remembers and recalls what the model wrote", () => {
    const v = createVault();
    v.remember("xid: 42y", "x[ID_NUMBER_1]y");
    expect(v.recall("xid: 42y")).toBe("x[ID_NUMBER_1]y");
    v.clear();
    expect(v.recall("xid: 42y")).toBeUndefined();
  });
});

describe("vault: review fixes", () => {
  it("merges a partial detection with a known value, keeping its placeholder", () => {
    const v = createVault();
    v.tag("PERSON", "Kim Minsu");
    const t = "Ask Kim Minsu and Kim Minsuk.";
    const spans = withVaultSpans(t, [span(t, "Kim", "PERSON")], v);
    expect(spans.map((s) => [t.slice(s.start, s.end), s.source])).toEqual([["Kim Minsu", "vault"]]);
    expect(applyRedaction(t, spans, { vault: v })).toBe("Ask [PERSON_1] and Kim Minsuk.");
  });

  it("caps label families at 32 characters so every placeholder fits a streamed restore", () => {
    const v = createVault();
    const tag = v.tag("X".repeat(50), "value one");
    expect(tag).toBe(`[${"X".repeat(32)}_1]`);
    const r = v.restorer();
    expect(r.push(tag.slice(0, -1)) + r.push("]") + r.flush()).toBe("value one");
    expect(() => createVault([{ tag: `[${"Y".repeat(33)}_1]`, label: "Y", value: "v" }])).toThrow(/not a vault entry/);
  });

  it("restores from a snapshot of bindings, and escapes literal tags", () => {
    const v = createVault();
    v.tag("PERSON", "Alice");
    const snap = v.bindings(["[PERSON_1]", "[PERSON_9]"]);
    expect([...snap]).toEqual([["[PERSON_1]", "Alice"]]);
    v.clear();
    v.tag("PERSON", "Bob");
    expect(v.restore("[PERSON_1]", { only: snap })).toBe("Alice");
    expect(escapeTags("a [PERSON_1] [note] [x_1]")).toBe("a \uff3bPERSON_1] [note] [x_1]");
  });
});

describe.skipIf(!existsSync(`${MODELS}/pii-nano/onnx/model_q4.onnx`))("redact with a vault, nano model", () => {
  it("gives the spans of redact() numbered tags, restores them, and finds a known value again", async () => {
    const r = await createRedactor({ size: "nano", modelUrl: `file://${MODELS}/pii-nano/`, fetch: localFetch, cache: false });
    const text = "Please call Kim Minsu on 010-1234-5678 or write to kim@example.com.";
    const plain = await r.redact(text);
    const vault = createVault();
    const numbered = await r.redact(text, { vault });
    expect(numbered.spans).toEqual(plain.spans);
    expect(numbered.text).toMatch(/\[EMAIL_1\]/);
    expect(vault.restore(numbered.text)).toBe(text);
    // the same values in another text keep their placeholders
    const again = await r.redact("kim@example.com again", { vault });
    expect(again.text).toBe("[EMAIL_1] again");
    // the sensitivity of a call is the one it was started with, whatever is set while the model runs
    r.setSensitivity(1);
    expect(r.sensitivity).toBe(1);
    const pending = r.detect("Contact Morgan about the invoice.");
    r.setSensitivity(-1);
    const started = await pending;
    r.setSensitivity(1);
    expect(started).toEqual(await r.detect("Contact Morgan about the invoice."));
    r.setSensitivity(0);
    // a name the model finds once is masked wherever it occurs again in the same text, on the first call
    const fresh = createVault();
    const twice = await r.redact("I met Alice yesterday. Use the word Alice twice.", { vault: fresh });
    expect(twice.text).not.toContain("Alice");
    expect(fresh.restore(twice.text)).toBe("I met Alice yesterday. Use the word Alice twice.");
    // a known value is masked even where the model would not find it on its own
    const email = vault.entries().find((e) => e.label === "EMAIL")!;
    expect((await r.redact(`token-${email.value}-x`, { vault })).text).not.toContain(email.value);
    await r.dispose();
  });
});

describe("vault readings until the vault stops growing", () => {
  it("repeats the readings past 16 rounds (guarantee note G44, O5)", () => {
    // a stand-in vault whose search in the other readings (variants) shows the next word of a chain only once the word
    // before it is known: every round learns one word, and 1.7.0 stopped after 16
    const words = Array.from({ length: 40 }, (_, i) => `w${String(i).padStart(2, "0")}x`);
    const real = createVault();
    real.tag("ID_NUMBER", words[0]);
    const chain = new Proxy(real, {
      get(target, key) {
        if (key !== "find") return Reflect.get(target, key);
        return (text: string, options?: { loose?: boolean; variants?: boolean }) => {
          const hits: { start: number; end: number; label: string; value: string }[] = [];
          words.forEach((w, i) => {
            if (target.age(w) === undefined && !(options?.variants && i > 0 && target.age(words[i - 1]) !== undefined)) return;
            for (let k = text.indexOf(w); k >= 0; k = text.indexOf(w, k + 1)) hits.push({ start: k, end: k + w.length, label: "ID_NUMBER", value: w });
          });
          return hits.sort((a, b) => a.start - b.start);
        };
      },
    }) as Vault;
    const out = redactReadings(words.join(" "), [], chain);
    expect(out.text).not.toMatch(/w[0-9]{2}x/);
    expect(real.size).toBe(40);
  });
});

describe("vault: the 23rd outside review (A4, A5)", () => {
  const ch = (...codes: number[]) => String.fromCharCode(...codes);
  const garcia = `Jos${ch(0xe9)} Garc${ch(0xed)}a`;
  const JOSE = `JOS${ch(0xc9)}`;
  const GARCIA = `GARC${ch(0xcd)}A`;

  it("finds a value in capitals with its white space written another way (A4)", () => {
    const v = createVault();
    v.tag("PERSON", garcia);
    v.tag("ADDRESS", `Calle Mayor 5, M${ch(0xe1)}laga`);
    for (const space of ["   ", "\t", ch(0xa0), ch(0x3000, 0x3000), "\r\n    "]) {
      const text = `Signed: ${JOSE}${space}${GARCIA}.`;
      expect(v.find(text, { variants: true }), JSON.stringify(text)).toEqual([{ start: 8, end: text.length - 1, label: "PERSON", value: garcia }]);
      expect(v.whole(`${JOSE}${space}${GARCIA}`)?.value).toBe(garcia);
    }
    // an address held with a comma, in capitals over two lines
    expect(v.hide(`to CALLE MAYOR 5\nM${ch(0xc1)}LAGA now`, { escape: true })).toBe("to CALLE MAYOR 5\nM" + ch(0xc1) + "LAGA now"); // (exact search only)
    expect(v.find(`to CALLE MAYOR 5\nM${ch(0xc1)}LAGA now`, { variants: true }).map((h) => h.value)).toEqual([`Calle Mayor 5, M${ch(0xe1)}laga`]);
    // capitals that are another spelling only where they are not in ASCII: "KIM   MINSU" was found before by its lines in
    // any ASCII case, and a value in any ASCII case with a non-ASCII letter in lower case is not a value in capitals
    v.tag("PERSON", "Kim Minsu");
    expect(v.find("KIM   MINSU", { variants: true }).map((h) => h.value)).toEqual(["Kim Minsu"]);
    expect(v.find(`JOS${ch(0xc9)}   garc${ch(0xed)}a`, { variants: true })).toEqual([]);
  });

  it("compares capitals composed, as the text is read (A4, U1 with U2)", () => {
    const v = createVault();
    const paisios = `Pa${ch(0x390)}sios`;
    v.tag("PERSON", paisios);
    v.tag("PERSON", `${paisios} Lambrou`);
    const up = paisios.toUpperCase(); // (three code points for the iota, not composed)
    expect(up).toBe(`PA${ch(0x399, 0x308, 0x301)}SIOS`);
    expect(v.find(`Signed: ${up}.`).map((h) => h.value)).toEqual([paisios]);
    expect(v.find(`Signed: ${up}\t LAMBROU.`, { variants: true }).map((h) => h.value)).toEqual([`${paisios} Lambrou`]);
    expect(v.whole(up)?.value).toBe(paisios);
  });

  it("reads full-width characters as ASCII before it decodes escapes (A4, U3 with S1, S5, S6, S13)", () => {
    const v = createVault();
    v.tag("EMAIL", "kim@example.com");
    v.tag("PERSON", "Kim Minsu");
    const PCT = ch(0xff05);
    const fw = (s: string) => Array.from(s, (c) => ch(c.charCodeAt(0) + 0xfee0)).join("");
    const cases: [string, string][] = [
      [`kim${PCT}40example.com`, "kim@example.com"],
      [`kim%${fw("40")}example.com`, "kim@example.com"],
      [`kim${PCT}2540example.com`, "kim@example.com"],
      [`KIM${PCT}40EXAMPLE.COM`, "kim@example.com"],
      [`kim${ch(0xff3c)}u0040example.com`, "kim@example.com"],
      [`kim${fw("\\u0040")}example.com`, "kim@example.com"],
      [`Kim${PCT}20${PCT}09Minsu`, "Kim Minsu"],
      [`Kim${ch(0xff0b)}Minsu`, "Kim Minsu"],
      [`Kim${ch(0xff3c)}xa0Minsu`, "Kim Minsu"],
    ];
    for (const [written, value] of cases) {
      const text = `mail ${written} now`;
      expect(escapedReadings(text).some((r) => r.text.includes(written.includes("Minsu") || written.includes("MINSU") ? "Minsu" : "@")), text).toBe(true);
      expect(v.whole(written)?.value, written).toBe(value);
      const out = redactReadings(text, [], v);
      expect(out.text, text).toBe(`mail ${v.tag(value === "Kim Minsu" ? "PERSON" : "EMAIL", value)} now`);
    }
    // a reading of the narrowed text gives places in the text as written
    const r = escapedReadings(`x kim${PCT}40example.com`, vaultReach(v)).find((x) => x.text.includes("kim@example.com"))!;
    expect(r.map[r.text.indexOf("@")]).toBe(5);
    // ordinary full-width signs stay
    const kept = `Save ${fw("50%")} today, ${fw("1+1")} offer`;
    expect(redactReadings(kept, [], v).text).toBe(kept);
  });

  it("reads an escape across a run of white space of any length and kind (A5, W1, W2)", () => {
    const v = createVault();
    v.tag("PERSON", "Kim Minsu");
    v.tag("ADDRESS", "12 Main St, Seoul");
    const runs = [" ".repeat(45), "\t".repeat(45), ch(0xa0).repeat(45), ch(0x3000).repeat(45), "\n".repeat(45), "\r\n".repeat(30), " \t\n".repeat(400)];
    for (const run of runs) {
      for (const written of [`Kim${run}%4Dinsu`, `%4Bim${run}Minsu`, `Kim${run}Mi%6Esu`, `KIM${run}%4DINSU`]) {
        const text = `Contact: ${written}.`;
        expect(redactReadings(text, [], v).text, JSON.stringify(text).slice(0, 60)).toBe("Contact: [PERSON_1].");
        expect(v.whole(written)?.value).toBe("Kim Minsu");
      }
    }
    expect(redactReadings(`Ship to 12 Main St,\n${" ".repeat(45)}Seo%75l today`, [], v).text).toBe("Ship to [ADDRESS_1] today");
    // what no reading changes counts white space as nothing: a run is one space where a value is matched
    expect(unchangedLength(`Kim${" ".repeat(45)}%4Dinsu`)).toBe(3);
    // another name after the run stays
    const other = `Contact: Kim${" ".repeat(45)}%4Dorothy.`;
    expect(redactReadings(other, [], v).text).toBe(other);
  });
});

describe("modelValues: what a model's span took from around the value (1.7.1 review, L3-B1, L3-B3, L3-B4)", () => {
  // (the shared cases have the whole path, redactWithVault; these are the ends one by one, as in python/tests/test_vault.py)
  const cut = (text: string, label = "PERSON") => modelValues(text, [{ start: 0, end: text.length, label }]).map((s) => text.slice(s.start, s.end));
  const c = (...cps: number[]) => String.fromCharCode(...cps);
  it("cuts white space, brackets, quotation marks and sentence marks at the ends, and keeps what belongs to the value", () => {
    expect(cut("Kim Minsu. ")).toEqual(["Kim Minsu"]);
    expect(cut(" (Kim Minsu)")).toEqual(["Kim Minsu"]);
    expect(cut("((Kim Minsu))")).toEqual(["Kim Minsu"]); // (pairs around all the rest, one after another)
    expect(cut("(02) 1234-5678)", "PHONE")).toEqual(["(02) 1234-5678"]); // (a bracket whose partner is inside stays)
    expect(cut("Kim (Minsu)")).toEqual(["Kim (Minsu)"]);
    expect(cut("(Kim) Minsu")).toEqual(["(Kim) Minsu"]);
    expect(cut("Minsu)")).toEqual(["Minsu"]);
    expect(cut("'Kim Minsu")).toEqual(["Kim Minsu"]);
    expect(cut("Jones'")).toEqual(["Jones"]);
    expect(cut("O'Neil")).toEqual(["O'Neil"]);
    expect(cut("Tiffany & Co.", "ORG")).toEqual(["Tiffany & Co."]);
    expect(cut("Tiffany & Co.,", "ORG")).toEqual(["Tiffany & Co."]);
    expect(cut("pass: x!.", "SECRET")).toEqual(["pass: x!."]);
    expect(cut(`b'Jos${c(0xe9)}'`)).toEqual([`Jos${c(0xe9)}`]);
    expect(cut("bob'")).toEqual(["bob"]);
    expect(cut("1990-01-02T00:00:00Z", "DATE_OF_BIRTH")).toEqual(["1990-01-02"]);
    expect(cut("1990-01-02T00:00:00Z", "ID_NUMBER")).toEqual(["1990-01-02T00:00:00Z"]);
    expect(cut("1990-01-02T", "DATE_OF_BIRTH")).toEqual(["1990-01-02T"]);
    expect(cut(" .( ")).toEqual([]);
    expect(cut(`${c(0x300c)}Tanaka${c(0x300d, 0x3002)}`)).toEqual(["Tanaka"]);
    expect(cut(`Kim${c(0x3000)}`)).toEqual(["Kim"]);
  });

  timingIt("takes time that grows with the length of a span (doubling test)", () => {
    // (one span with n brackets before a value and n sentence marks and opening brackets after it: each is cut once)
    const best = (n: number) => {
      const text = "(".repeat(n) + "Kim Minsu" + ".,".repeat(n) + " (".repeat(n);
      const spans = [{ start: 0, end: text.length, label: "PERSON" }];
      let min = Infinity;
      for (let k = 0; k < 3; k++) {
        const t0 = performance.now();
        const got = modelValues(text, spans);
        min = Math.min(min, performance.now() - t0);
        expect(got.map((s) => text.slice(s.start, s.end))).toEqual(["Kim Minsu"]);
      }
      return min;
    };
    best(4000);
    const [a, b, d] = [best(8000), best(16000), best(32000)];
    expect([b / a, d / b].every((r) => r <= 3), `ms ${a.toFixed(2)}/${b.toFixed(2)}/${d.toFixed(2)}`).toBe(true);
  });
});
