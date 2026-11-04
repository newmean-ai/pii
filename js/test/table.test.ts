import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { combine, createRedactor } from "../src/redactor";
import { checkDelimiter, detectDelimiter, detectTableSpans, parseCsv, parseMarkdown, parseTable, snapGraphemes, tableSpans } from "../src/table";
import type { Span } from "../src/types";

const values = (text: string, t: { cells: { row: number; col: number; start: number; end: number }[] }) =>
  t.cells.map((c) => `${c.row}.${c.col}=${text.slice(c.start, c.end)}`);
/** Model span over the n-th occurrence of `word`. */
const at = (text: string, word: string, label: string, nth = 0, source: Span["source"] = "model"): Span => {
  let i = -1;
  for (let k = 0; k <= nth; k++) i = text.indexOf(word, i + 1);
  if (i < 0) throw new Error(`no ${word}`);
  return { start: i, end: i + word.length, label, source };
};
const show = (text: string, spans: Span[]) => spans.map((s) => `${text.slice(s.start, s.end)}:${s.label}:${s.source}`);
const sorted = (spans: Span[]) => [...spans].sort((a, b) => a.start - b.start);

describe("delimiter auto-detection", () => {
  it("comma", () => expect(detectDelimiter("name,email\nKim,k@x.io\nLee,l@x.io\n")).toBe(","));
  it("tab", () => expect(detectDelimiter("name\temail\nKim, Minsu\tk@x.io\nLee\tl@x.io\n")).toBe("\t"));
  it("semicolon", () => expect(detectDelimiter("name;city\nKim;Seoul, KR\nLee;Busan\n")).toBe(";"));
  it("markdown", () => expect(detectDelimiter("\n| Name | Email |\n|---|---|\n| Kim | k@x.io |\n")).toBe("|"));
  it("plain prose is not a table", () => {
    expect(detectDelimiter("Hello, I am Kim.\nCall me tomorrow.\nBye.")).toBeNull();
    expect(parseTable("Just one line, with a comma")).toBeNull();
    expect(tableSpans("Hello Kim, bye.", [{ start: 6, end: 9, label: "PERSON", source: "model" }])).toEqual([
      { start: 6, end: 9, label: "PERSON", source: "model" },
    ]);
  });
  it("ties go to the earlier delimiter", () => expect(detectDelimiter("a,b;c\nd,e;f\n")).toBe(","));
  it("rejects an unknown delimiter", () => expect(() => parseTable("a,b\nc,d", "x" as any)).toThrow());
  it("checkDelimiter accepts exactly the TableDelimiter values", () => {
    for (const d of ["auto", ",", "\t", ";", "markdown"] as const) expect(() => checkDelimiter(d)).not.toThrow();
    for (const d of ["|", "x", "", null, undefined, ", "]) expect(() => checkDelimiter(d as any)).toThrow(/unknown table delimiter/);
  });
});

describe("CSV parse", () => {
  it("quoted fields with delimiters, escaped quotes and line breaks", () => {
    const text = 'name,note\n"Kim, Minsu","say ""hi""\nthere"\n"ab"junk,c\n';
    expect(values(text, parseCsv(text, ","))).toEqual([
      "0.0=name", "0.1=note", "1.0=Kim, Minsu", '1.1=say ""hi""\nthere', "2.0=ab", "2.1=c",
    ]);
  });
  it("an unclosed quote runs to the end of the text", () => {
    const text = 'a,b\nc,"open\nd,e';
    expect(values(text, parseCsv(text, ","))).toEqual(["0.0=a", "0.1=b", "1.0=c", "1.1=open\nd,e"]);
  });
  it("blank lines and a trailing newline are not rows", () => {
    const text = "a,b\n\n \t\nc,d\n";
    const t = parseCsv(text, ",");
    expect(t.rows).toBe(2);
    expect(values(text, t)).toEqual(["0.0=a", "0.1=b", "1.0=c", "1.1=d"]);
  });
  it("CRLF terminates a record, a lone CR does not", () => {
    const text = "a,b\r\nc\rx,d\r\n";
    expect(values(text, parseCsv(text, ","))).toEqual(["0.0=a", "0.1=b", "1.0=c\rx", "1.1=d"]);
  });
  it("trailing delimiter gives an empty last field", () => {
    const text = "a,b,\nc,d,";
    expect(values(text, parseCsv(text, ","))).toEqual(["0.0=a", "0.1=b", "0.2=", "1.0=c", "1.1=d", "1.2="]);
  });
});

describe("markdown parse", () => {
  it("cells, separator lines, empty cells, open last cell, ignored prose", () => {
    const text = "Intro text\n| Name | Email |\r\n|---|:--:|\n| Kim | k@x.io |\n|    | x\nafter";
    const t = parseMarkdown(text);
    expect(t.rows).toBe(3);
    expect(values(text, t)).toEqual(["0.0=Name", "0.1=Email", "1.0=Kim", "1.1=k@x.io", "2.0=", "2.1=x"]);
    const empty = t.cells[4];
    expect(empty.start).toBe(text.indexOf("|    |") + 1);
  });
});

// a name column (10 data rows: 8 found by the model, 1 missed, 1 placeholder) next to a free-text column
const NAMES = ["Kim Minsu", "Lee Jiwon", "Park Seo", "Choi Ana", "Jung Ho", "Han Bit", "Yoon Ara", "Kang Dae", "Song Mia", "n/a"];
const NOTES = [
  "called about the delayed parcel twice this week",
  "asked for a refund on the blue jacket order",
  "wants the invoice sent to the office address",
  "no answer on the first two attempts today",
  "prefers email over phone for all updates",
  "returned the item because the size was wrong",
  "will visit the store next Monday morning",
  "complained that the courier left it outside",
  "requested a callback after six in the evening",
  "satisfied with the replacement and closed it",
];
const CSV = "name,note\n" + NAMES.map((n, i) => `${n},${NOTES[i]}`).join("\n") + "\n";
// the model finds 8 names (not "Song Mia") and also tags every note as PERSON (an unrealistic worst case)
const MODEL = sorted([
  ...NAMES.slice(0, 8).map((n) => at(CSV, n, "PERSON")),
  ...NOTES.map((n) => at(CSV, n, "PERSON")),
]);

describe("column vote", () => {
  it("propagates a name column (placeholders skipped) and never a free-text column", () => {
    const out = tableSpans(CSV, MODEL);
    const names = show(CSV, out).filter((s) => !NOTES.some((n) => s.startsWith(n)));
    expect(names).toEqual(NAMES.slice(0, 9).map((n) => `${n}:PERSON:table`));
    // notes keep their clipped model spans, not table spans
    expect(show(CSV, out).filter((s) => NOTES.some((n) => s.startsWith(n))).every((s) => s.endsWith(":model"))).toBe(true);
  });
  it("vote off: clipped spans only", () => {
    const out = tableSpans(CSV, MODEL, { vote: false });
    expect(out.filter((s) => s.source === "table")).toEqual([]);
    expect(out.length).toBe(MODEL.length);
  });
  it("header off: the header row is a data row (here it breaks the 80% bar)", () => {
    expect(tableSpans(CSV, MODEL, { header: false }).filter((s) => s.source === "table")).toEqual([]);
  });
  it("the header row is never propagated into", () => {
    const text = "Kim Minsu\n" + NAMES.slice(0, 8).join("\n") + "\nzz";
    // single column → not a table by auto-detection; force the comma parser
    const spans = sorted([at(text, "Kim Minsu", "PERSON", 1), ...NAMES.slice(1, 8).map((n) => at(text, n, "PERSON"))]);
    const out = tableSpans(text, spans, { delimiter: "," });
    expect(out.some((s) => s.start === 0)).toBe(false);
    expect(show(text, out)).toContain("zz:PERSON:table");
  });
  it("the label filter stops propagation", () => {
    expect(tableSpans(CSV, MODEL, {}, new Set(["EMAIL"])).filter((s) => s.source === "table")).toEqual([]);
  });
  it("partial coverage of a cell does not support (needs 80% of its non-space code points)", () => {
    const spans = sorted(NAMES.slice(0, 8).map((n) => at(CSV, n.split(" ")[0], "PERSON")));
    expect(tableSpans(CSV, spans).filter((s) => s.source === "table")).toEqual([]);
  });
  it("fewer than 5 non-empty cells never vote", () => {
    const text = "name,x\nKim,1\nLee,2\nPark,3\nChoi,4\n,5\n";
    const spans = ["Kim", "Lee", "Park", "Choi"].map((n) => at(text, n, "PERSON"));
    expect(tableSpans(text, spans).filter((s) => s.source === "table")).toEqual([]);
  });
  it("EMAIL needs @ or ＠; PHONE needs 6 digits (any script)", () => {
    const emails = ["a@x.io", "b@x.io", "c@x.io", "d＠x.io", "e@x.io", "unknown"];
    const phones = ["010-1234-5678", "02-555-0101", "+82 10 9999 0000", "٠١٠١٢٣٤٥٦٧", "031-777-8888", "12345"];
    const text = "email;phone\n" + emails.map((e, i) => `${e};${phones[i]}`).join("\n");
    const spans = sorted([...emails.slice(0, 5).map((e) => at(text, e, "EMAIL")), ...phones.slice(0, 5).map((p) => at(text, p, "PHONE"))]);
    // 5 of 6 cells found per column → both columns vote; "unknown" (no @) and "12345" (5 digits) stay visible
    const out = tableSpans(text, spans);
    expect(show(text, out.filter((s) => s.source === "table"))).toEqual([
      ...emails.slice(0, 5).flatMap((e, i) => [`${e}:EMAIL:table`, `${phones[i]}:PHONE:table`]),
    ]);
  });
  it("USERNAME propagates, other labels never do", () => {
    const users = ["kim_01", "lee.j", "parkseo", "choi-a", "jung99", "hanbit"];
    const ids = ["900101-1234567", "900102-1234567", "900103-1234567", "900104-1234567", "900105-1234567", "x"];
    const text = "user\tid\n" + users.map((u, i) => `${u}\t${ids[i]}`).join("\n");
    const spans = sorted([...users.slice(0, 5).map((u) => at(text, u, "USERNAME")), ...ids.slice(0, 5).map((d) => at(text, d, "ID_NUMBER"))]);
    expect(show(text, tableSpans(text, spans).filter((s) => s.source === "table"))).toEqual(users.map((u) => `${u}:USERNAME:table`));
  });
  it("PERSON_L2 counts toward PERSON and propagates as PERSON", () => {
    const text = "who,n\n" + ["Mr. Kim", "Ms. Lee", "Dr. Park", "Mr. Choi", "Ms. Jung"].map((n, i) => `${n},${i}`).join("\n");
    const spans = ["Mr. Kim", "Ms. Lee", "Dr. Park", "Mr. Choi"].map((n) => at(text, n, "PERSON_L2"));
    const out = tableSpans(text, spans);
    expect(show(text, out)).toEqual(["Mr. Kim", "Ms. Lee", "Dr. Park", "Mr. Choi", "Ms. Jung"].map((n) => `${n}:PERSON:table`));
  });
});

describe("clipping and merge", () => {
  it("a span crossing delimiters and quotes is cut at cell values; whitespace-only parts are dropped", () => {
    const text = 'a,b,c\n"Kim", Lee ,x\n';
    const span: Span = { start: text.indexOf('"Kim"'), end: text.indexOf(",x"), label: "PERSON", source: "model" };
    expect(show(text, tableSpans(text, [span], { vote: false }))).toEqual(["Kim:PERSON:model", " Lee :PERSON:model"]);
    const ws: Span = { start: text.indexOf(" Lee"), end: text.indexOf(" Lee") + 1, label: "PERSON", source: "model" };
    expect(tableSpans(text, [ws], { vote: false })).toEqual([]);
  });
  it("a table span absorbs an overlapping clipped span and keeps its own label", () => {
    const names = ["Kim Minsu", "Lee Jiwon", "Park Seo", "Choi Ana", "Jung Ho"];
    const text = "n\n" + names.join("\n") + "\n";
    const spans = [...names.map((n) => at(text, n, "PERSON")).slice(0, 4), at(text, "Ho", "ORG")];
    const out = tableSpans(text, spans, { delimiter: "," });
    expect(show(text, out)).toEqual(names.map((n) => `${n}:PERSON:table`));
  });
});

describe("grapheme snapping", () => {
  const snap = (text: string, part: string, from = 0) => {
    const i = text.indexOf(part, from);
    return show(text, snapGraphemes(text, [{ start: i, end: i + part.length, label: "PERSON", source: "model" }]));
  };
  it("emoji ZWJ sequence", () => expect(snap("👩‍👩‍👧 Contact", "👩‍👩")).toEqual(["👩‍👩‍👧:PERSON:model"]));
  it("flag", () => expect(snap("x 🇰🇷 y", "🇰")).toEqual(["🇰🇷:PERSON:model"]));
  it("skin tone", () => expect(snap("ok 👍🏽!", "👍")).toEqual(["👍🏽:PERSON:model"]));
  it("keycap", () => expect(snap("call 1️⃣2️⃣3", "1")).toEqual(["1️⃣:PERSON:model"]));
  it("combining marks", () => expect(snap("José Kim", "Jose")).toEqual(["José:PERSON:model"]));
  it("Hangul jamo", () => expect(snap("각 x", "ᄀ")).toEqual(["각:PERSON:model"]));
  it("a boundary already on a cluster boundary does not move", () => expect(snap("Kim 👍🏽", "Kim")).toEqual(["Kim:PERSON:model"]));
  it("spans meeting inside one cluster merge; the longer original span's label wins", () => {
    // pre-snap lengths 7 vs 4 code points (USERNAME wins); snapped they would be 8 vs 10
    const text = "a👨‍👩‍👧‍👦cde";
    const cut = 1 + "👨‍👩‍👧‍".length;
    const spans: Span[] = [
      { start: 0, end: cut, label: "USERNAME", source: "model" },
      { start: cut, end: text.length, label: "PERSON", source: "regex" },
    ];
    expect(show(text, snapGraphemes(text, spans))).toEqual([`${text}:USERNAME:model`]);
    const tie: Span[] = [
      { start: 0, end: 1, label: "EMAIL", source: "model" },
      { start: 1, end: 2, label: "PHONE", source: "model" },
    ];
    const t2 = "e\u0301x"; // e + combining acute
    expect(show(t2, snapGraphemes(t2, tie))).toEqual(["e\u0301:EMAIL:model"]);
  });
  it("a chain of 3 spans in one cluster: each next span competes with the current winner's original length", () => {
    // 👨 ZWJ 👩 ZWJ 👧: spans of 1, 2 (wins) and 2 (tie, keeps the winner) code points
    const text = "👨‍👩‍👧";
    const spans: Span[] = [
      { start: 0, end: 2, label: "EMAIL", source: "model" },
      { start: 2, end: 5, label: "PHONE", source: "model" },
      { start: 5, end: 8, label: "USERNAME", source: "model" },
    ];
    expect(show(text, snapGraphemes(text, spans))).toEqual([`${text}:PHONE:model`]);
  });
  it("touching spans in different clusters stay apart", () => {
    const text = "ab";
    const spans: Span[] = [
      { start: 0, end: 1, label: "EMAIL", source: "model" },
      { start: 1, end: 2, label: "PHONE", source: "model" },
    ];
    expect(snapGraphemes(text, spans)).toEqual(spans);
  });
  it("empty spans stay empty", () => {
    const e: Span = { start: 2, end: 2, label: "PERSON", source: "regex" };
    expect(snapGraphemes("abcdef", [e])).toEqual([e]);
    // "b" U+0301 U+0301 is one cluster: a span from inside it takes the group's start back over the empty span
    const text = "ab\u0301\u0301cd";
    expect(snapGraphemes(text, [e, { start: 3, end: 5, label: "EMAIL", source: "model" }])).toEqual([
      { start: 1, end: 5, label: "EMAIL", source: "model" },
    ]);
  });
});

describe("table mode snapping stays inside cells", () => {
  const P = (start: number, end: number, label = "PERSON"): Span => ({ start, end, label, source: "model" });
  // a value starting with U+0301 shares a cluster with the delimiter or opening quote before it; one ending with U+0600
  // (Prepend) with the delimiter or closing quote after it
  const cases: [string, Span[]][] = [
    ['name,city\n"\u0301Ann, Lee",Seoul\nBo,Busan', [P(11, 19)]],
    ["h1,h2\nc,\u0301d\ne,f", [P(6, 7), P(8, 10)]],
    ["a,b\nx\u0600,y\nz,w", [P(4, 6)]],
    ["a;b\nx\u0600;\u0301y\nz;w", [P(4, 6), P(7, 9, "EMAIL")]],
    ["| a | b |\n|---|---|\n| x\u0600|\u0301y |\n| z | w |", [P(22, 24), P(25, 27, "EMAIL")]],
    ['a,b\n"x\u0600","\u0301y"\nz,w', [P(5, 7), P(10, 12, "EMAIL")]],
  ];
  for (const [text, spans] of cases)
    it(JSON.stringify(text), () => {
      expect(detectTableSpans(text, spans, { vote: false })).toEqual(spans);
      expect(snapGraphemes(text, spans)).not.toEqual(spans); // whole-text snapping alone would cross the boundary
    });
  it("a propagated column keeps its delimiters", () => {
    const names = ["\u0301Ann", "Bo\u0600", "\u0301Cy", "Di\u0600", "Ed", "Fa"];
    const text = "id,name,x\n" + names.map((n, i) => `${i},${n},y`).join("\n") + "\n";
    const spans = names.map((n) => at(text, n, "PERSON"));
    const out = detectTableSpans(text, spans);
    expect(show(text, out)).toEqual(names.map((n) => `${n}:PERSON:table`));
  });
  it("not a table: plain snapping", () => {
    const text = "x,\u0301y";
    expect(detectTableSpans(text, [P(2, 4)])).toEqual([P(1, 4)]);
  });
});

describe("large groups", () => {
  it("merge and combine do not spread a group into one call (no RangeError at 130k spans)", () => {
    const K = 130_000;
    const big = "ab ".repeat(K);
    const names = ["Ann", "Bo", "Cy", "Di", "Ed"];
    const text = "name,x\n" + names.map((n) => `${n},1`).join("\n") + `\n"${big}",1\n`;
    const spans = names.map((n) => at(text, n, "PERSON"));
    const q = text.indexOf('"') + 1;
    for (let k = 0; k < K; k++) spans.push({ start: q + 3 * k, end: q + 3 * k + 2, label: "PERSON", source: "model" });
    const out = tableSpans(text, spans);
    expect(out.find((s) => s.source === "table" && s.start === q)?.end).toBe(q + big.length - 1);
    const many: Span[] = Array.from({ length: K }, (_, k) => ({ start: 0, end: k + 1, label: "PERSON", source: "model" }));
    expect(combine(many, [], new Set(["PERSON"]))).toEqual([{ start: 0, end: K, label: "PERSON", source: "model" }]);
  });
});

// end to end with the local model copies (not published; skipped without them)
const MODELS = process.env.PII_MODELS ?? new URL("./models/", import.meta.url).pathname;
const localFetch = (async (url: string) => new Response(readFileSync(url.replace("file://", "")))) as unknown as typeof fetch;
describe.skipIf(!existsSync(`${MODELS}/pii-nano/onnx/model_q4.onnx`))("table mode end to end (nano q4)", () => {
  it("keeps the table shape, stays inside cells, equals detect on prose", async () => {
    const r = await createRedactor({ size: "nano", variant: "q4", modelUrl: `file://${MODELS}/pii-nano/`, fetch: localFetch, cache: false });
    const csv =
      'name,email,phone,comment\n"Kim, Minsu",minsu.kim@example.com,010-1234-5678,"Called twice, ""urgent"""\n' +
      "Lee Jiwon,jiwon@example.org,010-2222-3333,no answer\nPark Seojun,seojun.p@example.net,02-555-0101,ok\n";
    const t = parseTable(csv)!;
    const spans = await r.detectTable(csv);
    expect(spans.length).toBeGreaterThan(0);
    for (const s of spans) expect(t.cells.some((c) => c.start <= s.start && s.end <= c.end)).toBe(true);
    const { text } = await r.redactTable(csv);
    expect(parseTable(text)!.rows).toBe(t.rows);
    expect(text.split("\n").length).toBe(csv.split("\n").length);
    const prose = "My name is Kim Minsu 👩‍👩‍👧 and my email is minsu.kim@example.com.";
    expect(await r.detectTable(prose)).toEqual(await r.detect(prose));
    await r.dispose();
  }, 120_000);

  it("rejects a bad delimiter before running the model, as the C core does", async () => {
    const r = await createRedactor({ size: "nano", variant: "q4", modelUrl: `file://${MODELS}/pii-nano/`, fetch: localFetch, cache: false });
    for (const text of ["name,email\nKim,k@x.io\n", "not a table"]) {
      await expect(r.detectTable(text, { delimiter: "x" as any })).rejects.toThrow(/unknown table delimiter "x"/);
      await expect(r.redactTable(text, { delimiter: "|" as any })).rejects.toThrow(/unknown table delimiter "\|"/);
    }
    // delimiter first, then context (pii_detect_table's order)
    await expect(r.detectTable("a,b\nc,d\n", { delimiter: "x" as any, context: "row" as any })).rejects.toThrow(/delimiter/);
    expect(r.lastRun).toBeNull();
    await r.detectTable("a,b\nc,d\n", { delimiter: "," });
    expect(r.lastRun).not.toBeNull();
    await r.dispose();
  }, 120_000);
});
