import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { restoreSpans, stripEmoji } from "../src/emoji";
import { PICTOGRAPHIC } from "../src/pictographic";
import { createRedactor } from "../src/redactor";
import type { Span } from "../src/types";

const span = (start: number, end: number, label = "PERSON"): Span => ({ start, end, label, source: "model" });

describe("stripEmoji", () => {
  it("leaves text without emoji alone", () => {
    expect(stripEmoji("Anna Kim, 010-1234-5678")).toBeNull();
  });

  it("removes whole runs: ZWJ families, skin tones, flags, variation selectors, tags", () => {
    const t = "a👩‍👩‍👧b👍🏽c🇰🇷d❤️e🏴󠁧󠁢󠁳󠁣󠁴󠁿f😀😀g";
    const s = stripEmoji(t)!;
    expect(s.text).toBe("abcdefg");
    expect(s.keep.map((i) => t[i]).join("")).toBe("abcdefg");
  });

  it("reads a keycap as its bare character", () => {
    const t = "1️⃣ #️⃣ *⃣ 5️";
    const s = stripEmoji(t)!;
    expect(s.text).toBe("1 # * 5️");
    expect(s.keep.map((i) => t[i]).join("")).toBe("1 # * 5️");
  });

  it("keeps lone surrogates and maps UTF-16 units", () => {
    const t = "\ud800x😀𝐀y";
    const s = stripEmoji(t)!;
    expect(s.text).toBe("\ud800x𝐀y");
    expect(s.keep).toEqual([0, 1, 4, 5, 6]);
  });
});

describe("C core parity", () => {
  it("uses the C core's Extended_Pictographic table, not the engine's", () => {
    const h = readFileSync(new URL("../../native/core/src/unicode_tables.h", import.meta.url), "utf8");
    const want = [...h.matchAll(/\{0x([0-9a-f]+), 0x([0-9a-f]+), GC_Extended_Pictographic\}/g)].flatMap((m) => [parseInt(m[1], 16), parseInt(m[2], 16)]);
    expect([...PICTOGRAPHIC]).toEqual(want);
  });

  it("never pairs lone surrogates across a cut", () => {
    const s = stripEmoji("\ud83d😀\ude00x")!;
    expect(s.text).toBe("\ufffd\ude00x");
    expect(s.keep).toEqual([0, 3, 4]);
  });
});

describe("restoreSpans", () => {
  it("maps spans back and splits them at a run, dropping the white space next to it", () => {
    const t = "Hi Anna 😀 Kim!";
    const s = stripEmoji(t)!; // "Hi Anna  Kim!"
    const out = restoreSpans(t, [span(3, 12)], s.keep);
    expect(out.map((x) => t.slice(x.start, x.end))).toEqual(["Anna", "Kim"]);
  });

  it("keeps a span that touches no run as it is", () => {
    const t = "😀 Anna Kim";
    const s = stripEmoji(t)!; // " Anna Kim"
    const out = restoreSpans(t, [span(1, 9)], s.keep);
    expect(out.map((x) => t.slice(x.start, x.end))).toEqual(["Anna Kim"]);
  });

  it("gives keycap marks back to a span and never splits at them", () => {
    const t = "call 0️⃣1️⃣0️⃣ now";
    const s = stripEmoji(t)!; // "call 010 now"
    const out = restoreSpans(t, [span(5, 8, "PHONE")], s.keep);
    expect(out.map((x) => t.slice(x.start, x.end))).toEqual(["0️⃣1️⃣0️⃣"]);
  });

  it("drops a piece that is only white space", () => {
    const t = "Anna 😀 😀 Kim";
    const s = stripEmoji(t)!; // "Anna   Kim"
    const out = restoreSpans(t, [span(0, 10)], s.keep);
    expect(out.map((x) => t.slice(x.start, x.end))).toEqual(["Anna", "Kim"]);
  });
});

const MODELS = process.env.PII_MODELS ?? new URL("./models/", import.meta.url).pathname;
const localFetch = (async (url: string) => new Response(readFileSync(url.replace("file://", "")))) as unknown as typeof fetch;
describe.skipIf(!existsSync(`${MODELS}pii-nano/onnx/model_q4.onnx`))("detect with emoji (nano q4)", () => {
  it("never masks an emoji; keepEmoji lets the model read them", async () => {
    const base = { size: "nano" as const, variant: "q4" as const, modelUrl: `file://${MODELS}/pii-nano/`, fetch: localFetch, cache: false };
    const r = await createRedactor(base);
    const k = await createRedactor({ ...base, keepEmoji: true });
    const texts = ["Call Anna Kim 😀 at 010-1234-5678 🙏", "🇰🇷 서울 강남구 테헤란로 123 👍🏽", "hi 👋 I'm Jo 💖", "no emoji, Anna Kim"];
    for (const t of texts) {
      const s = stripEmoji(t);
      const kept = new Set(s ? s.keep : []);
      for (const x of await r.detect(t)) for (let i = x.start; i < x.end; i++) expect(!s || kept.has(i)).toBe(true);
      if (!s) expect(await r.detect(t)).toEqual(await k.detect(t));
    }
    await r.dispose();
    await k.dispose();
  });
});
