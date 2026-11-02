import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createRedactor } from "../src/redactor";
import { detectTableSpans, isWordChar, snapGraphemes, wordEnd, wordStart } from "../src/table";
import { VERSION } from "../src/version";
import { MODEL_FILES, MODEL_VERSION } from "../src/models";
import type { Span } from "../src/types";

const sp = (start: number, end: number, label = "PERSON", source: Span["source"] = "model"): Span => ({ start, end, label, source });
const show = (t: string, spans: Span[]) => spans.map((s) => `${t.slice(s.start, s.end)}:${s.label}`);
const w = (t: string, part: string, label = "PERSON") => {
  const i = t.indexOf(part);
  return show(t, snapGraphemes(t, [sp(i, i + part.length, label)], undefined, true));
};

describe("word characters", () => {
  it("letters, digits and marks of spaced scripts; no punctuation, emoji or unspaced scripts", () => {
    for (const c of "aZ09가힣éßЖא١ﺍ") expect(isWordChar(c.codePointAt(0)!)).toBe(true);
    for (const c of [" ", ",", "-", "'", "@", "😀", "中", "あ", "ア", "ก", "ក"]) expect(isWordChar(c.codePointAt(0)!)).toBe(false);
    expect(isWordChar(0x0301)).toBe(true); // combining acute
    expect(isWordChar(0x1d400)).toBe(true); // mathematical bold A (astral)
  });

  it("word bounds walk over surrogate pairs and joiners", () => {
    const t = "x 𝐀𝐁c y";
    expect([wordStart(t, 4), wordEnd(t, 4)]).toEqual([2, 7]);
    expect([wordStart("Jean-Luc ok", 5), wordEnd("Jean-Luc ok", 5)]).toEqual([0, 5]); // a span starting at "Luc" / ending at "-"
    expect(wordEnd("Jean-Luc ok", 2)).toBe(8);
    expect(wordEnd("O'Brien", 1)).toBe(7);
    expect(wordStart("a, b", 3)).toBe(3);
  });
});

describe("wholeWords snapping", () => {
  it("widens partial words: the reported cases", () => {
    expect(w("남성", "남")).toEqual(["남성:PERSON"]);
    expect(w("경기도", "경기")).toEqual(["경기도:PERSON"]);
    expect(w("R23", "R", "ID_NUMBER")).toEqual(["R23:ID_NUMBER"]);
    expect(w("카카오 블로그", "카")).toEqual(["카카오:PERSON"]);
    expect(w("Jean-Luc Picard", "Luc")).toEqual(["Jean-Luc:PERSON"]);
    expect(w("call O'Brien.", "Brien")).toEqual(["O'Brien:PERSON"]);
  });

  it("keeps whole words, punctuation and unspaced scripts as they are", () => {
    expect(w("Kim Minji, hi", "Kim Minji")).toEqual(["Kim Minji:PERSON"]);
    expect(w("(kim@example.com)", "kim@example.com", "EMAIL")).toEqual(["kim@example.com:EMAIL"]);
    expect(w("田中太郎さん", "田中太郎")).toEqual(["田中太郎:PERSON"]);
    expect(w("คุณสมชาย", "สมชาย")).toEqual(["สมชาย:PERSON"]);
  });

  it("merges spans that meet in one word; the longest original span keeps its label", () => {
    const t = "AB12CD";
    expect(show(t, snapGraphemes(t, [sp(0, 1, "USERNAME"), sp(2, 5, "ID_NUMBER")], undefined, true))).toEqual(["AB12CD:ID_NUMBER"]);
  });

  it("is off by default", () => {
    expect(show("경기도", snapGraphemes("경기도", [sp(0, 2)]))).toEqual(["경기:PERSON"]);
  });

  it("table mode: widening stays inside the cell", () => {
    const t = "region,id\n경기도,R23\n서울,R24\n";
    const i = t.indexOf("경기"), j = t.indexOf("R23");
    const spans = detectTableSpans(t, [sp(i, i + 2), sp(j, j + 1, "ID_NUMBER")], { vote: false }, undefined, [], true);
    expect(show(t, spans)).toEqual(["경기도:PERSON", "R23:ID_NUMBER"]);
  });
});

const MODELS = new URL("./models/", import.meta.url).pathname;
const localFetch = (async (url: string) => new Response(readFileSync(url.replace("file://", "")))) as unknown as typeof fetch;

describe.skipIf(!existsSync(`${MODELS}/pii-large/onnx/model_q4e.onnx`))("redactor (large)", () => {
  it("info carries package and model versions and the checked files", async () => {
    const r = await createRedactor({ size: "large", modelUrl: `file://${MODELS}/pii-large/`, fetch: localFetch, cache: false });
    expect(r.version).toBe(MODEL_VERSION);
    expect(r.info).toEqual({
      packageVersion: VERSION,
      modelVersion: MODEL_VERSION,
      size: "large",
      variant: "q4e",
      engine: "builtin",
      modelUrl: `file://${MODELS}/pii-large/`,
      files: {
        model: { path: "onnx/model_q4e.onnx", ...MODEL_FILES.large.q4e },
        tokenizer: { path: "tokenizer.web.json", ...MODEL_FILES.large.tokenizer },
      },
      verified: true,
    });
    await r.dispose();
  });

  it("wholeWords masks the reported partial words whole", async () => {
    const opts = { size: "large" as const, modelUrl: `file://${MODELS}/pii-large/`, fetch: localFetch, cache: false };
    const plain = await createRedactor(opts);
    const whole = await createRedactor({ ...opts, wholeWords: true });
    expect((await plain.redact("남성")).text).toBe("[PERSON]성");
    expect((await whole.redact("남성")).text).toBe("[PERSON]");
    expect((await plain.redact("경기도")).text).toBe("[PERSON]기도");
    expect((await whole.redact("경기도")).text).toBe("[PERSON]");
    expect((await whole.redact("R23")).text).toBe("[ID_NUMBER]");
    const text = "Hi, I'm Kim Minji, call me at 010-1234-5678.";
    expect((await whole.redact(text)).text).toBe((await plain.redact(text)).text);
    await plain.dispose();
    await whole.dispose();
  });

  it("strictShortNames leaves short non-name values alone and still finds names", async () => {
    const opts = { size: "large" as const, modelUrl: `file://${MODELS}/pii-large/`, fetch: localFetch, cache: false };
    const plain = await createRedactor(opts);
    const strict = await createRedactor({ ...opts, strictShortNames: true });
    for (const v of ["경기", "네이버", "카카오"]) {
      expect((await plain.redact(v)).text).toBe("[PERSON]");
      expect((await strict.redact(v)).text).toBe(v);
    }
    expect((await strict.redact("남성")).text).toBe("남성");
    for (const v of ["김민지", "이서연", "Maria Lopez"]) expect((await strict.redact(v)).text).toBe("[PERSON]");
    const text = "Hi, I'm Kim Minji, call me at 010-1234-5678. 경기 지역에 삽니다.";
    expect((await strict.redact(text)).text).toBe((await plain.redact(text)).text);
    await plain.dispose();
    await strict.dispose();
  });
});
