import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createRedactor } from "../src/redactor";
import { checkListsId, ListError, Lists } from "../src/lists";
import { detectTableSpans, finishDetect } from "../src/table";
import { KO_PARTICLES, MAX_PARTICLE_RUN, WORD_RANGES } from "../src/word_tables";
import type { Span } from "../src/types";

const sp = (start: number, end: number, label = "PERSON", source: Span["source"] = "model"): Span => ({ start, end, label, source });
const show = (t: string, spans: Span[]) => spans.map((s) => `${t.slice(s.start, s.end)}:${s.label}:${s.source}`);
const fail = (f: () => unknown) => {
  try {
    f();
  } catch (e) {
    expect(e).toBeInstanceOf(ListError);
    const { list, index, reason, message } = e as ListError;
    return { list, index, reason, message };
  }
  throw new Error("no ListError");
};

describe("validation", () => {
  it("reports list, index and reason in the documented order, never the text", () => {
    expect(fail(() => new Lists("제주", undefined))).toMatchObject({ list: "allow", index: undefined, reason: "bad_shape" });
    expect(fail(() => new Lists(new Set(["a"]), undefined)).reason).toBe("bad_shape");
    expect(fail(() => new Lists(undefined, { text: "Kim", label: "PERSON" }))).toMatchObject({ list: "block", index: undefined });
    expect(fail(() => new Lists(["ok", 5], undefined))).toMatchObject({ list: "allow", index: 1, reason: "bad_shape" });
    expect(fail(() => new Lists(undefined, ["Kim"]))).toMatchObject({ list: "block", index: 0, reason: "bad_shape" });
    expect(fail(() => new Lists(undefined, [{ text: "Kim" }]))).toMatchObject({ list: "block", index: 0, reason: "bad_shape" });
    expect(fail(() => new Lists([{ text: "x", label: "NAME" }], undefined)).reason).toBe("unknown_label");
    expect(fail(() => new Lists(["a".repeat(257)], undefined)).reason).toBe("too_long");
    expect(fail(() => new Lists(["\0" + "a".repeat(256)], undefined)).reason).toBe("too_long"); // the length bound comes first
    expect(fail(() => new Lists(["a".repeat(65)], undefined)).reason).toBe("too_long");
    expect(fail(() => new Lists(["\ud800"], undefined)).reason).toBe("bad_text");
    expect(fail(() => new Lists(["a b"], undefined)).reason).toBe("bad_text");
    expect(fail(() => new Lists([""], undefined)).reason).toBe("empty");
    expect(fail(() => new Lists(["x　"], undefined)).reason).toBe("edge_space");
    expect(new Lists(["​x"], undefined).allowCount).toBe(1); // U+200B is not White_Space
    const e = fail(() => new Lists(["비밀이름 "], undefined));
    expect(e.message).not.toContain("비밀이름");
    // allow before block; the first invalid entry; too_many only after the first 10,000 are valid
    expect(fail(() => new Lists(["ok", " x"], [{ text: "" }]))).toMatchObject({ list: "allow", index: 1, reason: "edge_space" });
    const many = Array.from({ length: 10_001 }, (_, i) => `v${i}`);
    expect(fail(() => new Lists(many, undefined))).toMatchObject({ index: 10_000, reason: "too_many" });
    expect(fail(() => new Lists([...many.slice(0, 50), " x", ...many.slice(50)], undefined))).toMatchObject({ index: 50, reason: "edge_space" });
    expect(new Lists(many.slice(0, 10_000), undefined).allowCount).toBe(10_000);
  });

  it("checks across entries: label conflicts, then forms in both lists; duplicates merge", () => {
    expect(fail(() => new Lists(["seoul"], [{ text: "Kim", label: "PERSON" }, { text: "KIM", label: "ORG" }, { text: "SEOUL", label: "ORG" }])))
      .toMatchObject({ list: "block", index: 1, reason: "label_conflict" });
    expect(fail(() => new Lists(["Seoul", { text: "Busan", label: "ORG" }], [{ text: "x", label: "ORG" }, { text: "BUSAN", label: "ORG" }])))
      .toMatchObject({ list: "block", index: 1, reason: "in_both" });
    const l = new Lists([{ text: "Seoul", label: "ADDRESS" }, { text: "SEOUL", label: "ORG" }, "Busan"], [{ text: "Kim", label: "PERSON" }, { text: "KIM", label: "PERSON" }]);
    expect([l.allowCount, l.blockCount]).toEqual([2, 1]);
    expect(l.allowed("seoul", 0, 5, "ORG") && l.allowed("seoul", 0, 5, "ADDRESS") && !l.allowed("seoul", 0, 5, "PERSON")).toBe(true);
    expect(new Lists([{ text: "Lee", label: "PERSON_L2" }], undefined).allowed("Lee", 0, 3, "PERSON")).toBe(true); // one family
  });

  it("listsId: 1 to 128 code points without NUL, unpaired surrogates or line breaks", () => {
    expect(checkListsId(undefined)).toBe(null);
    expect(checkListsId("survey-2026-11 v3")).toBe("survey-2026-11 v3");
    for (const bad of ["", "a\nb", "\u0000", "\udc00", "x".repeat(129), 5])
      expect(fail(() => checkListsId(bad))).toMatchObject({ list: "meta", index: undefined, reason: "bad_lists_id" });
  });
});

describe("block and allow without a model", () => {
  const lists = new Lists(["제주", { text: "seoul", label: "ADDRESS" }], [
    { text: "박지훈", label: "PERSON" },
    { text: "Kim", label: "PERSON" },
    { text: "Ann Lee", label: "PERSON" },
    { text: "Lee Kim", label: "ORG" },
  ]);
  const run = (t: string, spans: Span[] = [], words = true) => show(t, finishDetect(t, spans, lists, words));

  it("matches on word boundaries, before Korean particles, case-insensitively in ASCII", () => {
    expect(run("박지훈님께서는 김박지훈 박지훈A")).toEqual(["박지훈:PERSON:block", "박지훈:PERSON:block"]);
    expect(run("KIM, Kimberly, kim. Kim")).toEqual(["KIM:PERSON:block", "kim:PERSON:block", "Kim:PERSON:block"]);
    // apostrophes and hyphens join words (the wholeWords rule): "kim's" and "Kim-Lee" are other words
    expect(run("kim's Kim-Lee")).toEqual([]);
    expect(run("Ann Lee Kim said")).toEqual(["Ann Lee Kim:PERSON:block"]); // overlapping entries cover every word
    expect(run("박지훈" + "님".repeat(MAX_PARTICLE_RUN))).toEqual(["박지훈:PERSON:block"]); // a rest of MAX_PARTICLE_RUN particles
    expect(run("박지훈" + "님".repeat(MAX_PARTICLE_RUN + 1))).toEqual([]); // a longer rest is part of the word
  });

  it("a region with a block match takes the block label; allow drops whole-span matches of its family", () => {
    const t = "Kim Leeds in 제주, SEOUL and 제주도";
    expect(run(t, [sp(2, 9, "ORG"), sp(13, 15, "ADDRESS"), sp(17, 22, "ADDRESS"), sp(27, 30, "ADDRESS")])).toEqual([
      "Kim Leeds:PERSON:block",
      "제주도:ADDRESS:model",
    ]);
    expect(run("seoul", [sp(0, 5, "PERSON")])).toEqual(["seoul:PERSON:model"]); // the entry allows ADDRESS only
  });

  it("is NFC-only: an NFD text does not match an NFC entry", () => {
    const nfd = "박지훈".normalize("NFD");
    expect(run(nfd)).toEqual([]);
    expect(run(nfd.normalize("NFC"))).toEqual(["박지훈:PERSON:block"]);
  });

  it("table mode: block regions inside cells; the pre-vote allow filter removes allowed spans before the vote", () => {
    const csv = "name,city\n박지훈,제주\n김민수,서울\n";
    const out = detectTableSpans(csv, [sp(14, 16, "ADDRESS"), sp(17, 20, "PERSON")], {}, undefined, [], true, lists);
    expect(show(csv, out)).toEqual(["박지훈:PERSON:block", "김민수:PERSON:model"]);
    // without lists, nothing is blocked and 제주 stays masked
    expect(show(csv, detectTableSpans(csv, [sp(14, 16, "ADDRESS"), sp(17, 20, "PERSON")], {}, undefined, [], true))).toEqual(["제주:ADDRESS:model", "김민수:PERSON:model"]);
  });
});

describe("pinned word table", () => {
  it("word_tables.ts carries the ranges and particles of native/core/src/word_tables.h", () => {
    const h = readFileSync(new URL("../../native/core/src/word_tables.h", import.meta.url), "utf8");
    const ranges = [...h.slice(h.indexOf("PII_WORD_TABLE"), h.indexOf("};")).matchAll(/\{(0x[0-9a-f]+), (0x[0-9a-f]+)\}/g)].flatMap((m) => [Number(m[1]), Number(m[2])]);
    expect(Array.from(WORD_RANGES)).toEqual(ranges);
    const p = h.slice(h.indexOf("PII_KO_PARTICLES"));
    const particles = [...p.matchAll(/\{(0x[0-9a-f]+), (0x[0-9a-f]+), (0x[0-9a-f]+)\}/g)].map((m) => String.fromCodePoint(...m.slice(1).map(Number).filter(Boolean)));
    expect(particles).toEqual(KO_PARTICLES);
    expect(h).toContain(`#define PII_MAX_PARTICLE_RUN ${MAX_PARTICLE_RUN}`);
  });
});

const MODELS = process.env.PII_MODELS ?? new URL("./models/", import.meta.url).pathname;
const localFetch = (async (url: string) => new Response(readFileSync(url.replace("file://", "")))) as unknown as typeof fetch;

describe.skipIf(!existsSync(`${MODELS}/pii-nano/onnx/model_q4.onnx`))("redactor with lists (nano)", () => {
  const opts = { size: "nano" as const, modelUrl: `file://${MODELS}/pii-nano/`, fetch: localFetch, cache: false };

  it("validates before loading the model and reports counts and listsId in info", async () => {
    let fetched = 0;
    const counting = (async (u: string) => (fetched++, localFetch(u))) as unknown as typeof fetch;
    await expect(createRedactor({ ...opts, fetch: counting, block: [{ text: " x", label: "PERSON" }] })).rejects.toBeInstanceOf(ListError);
    await expect(createRedactor({ ...opts, fetch: counting, listsId: "" })).rejects.toMatchObject({ reason: "bad_lists_id" });
    expect(fetched).toBe(0);
    const r = await createRedactor({ ...opts, allow: ["Seoul", "SEOUL"], block: [{ text: "박지훈", label: "PERSON" }], listsId: "v3" });
    expect(r.info.lists).toEqual({ allow: 1, block: 1, id: "v3" });
    await r.dispose();
  });

  it("masks block entries whatever labels says, as plain span objects", async () => {
    const r = await createRedactor({ ...opts, labels: [], block: [{ text: "박지훈", label: "PERSON" }] });
    const spans = await r.detect("담당: 박지훈님께 문의");
    expect(JSON.parse(JSON.stringify(spans))).toEqual([{ start: 4, end: 7, label: "PERSON", source: "block" }]);
    expect((await r.redact("담당: 박지훈님께 문의")).text).toBe("담당: [PERSON]님께 문의");
    expect(Object.keys((await r.detectTable("a,b\n박지훈,x\ny,z\n"))[0])).toEqual(["start", "end", "label", "source"]);
    await r.dispose();
  });
});
