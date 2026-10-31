import { describe, expect, it } from "vitest";
import { applyRedaction, combine, LABELS, LEVEL1_LABELS } from "../src/redactor";
import type { Span } from "../src/types";

const m = (start: number, end: number, label: string): Span => ({ start, end, label, source: "model" });
const r = (start: number, end: number, label: string): Span => ({ start, end, label, source: "regex" });

describe("combine", () => {
  it("merges overlapping model and regex spans; the model label wins", () => {
    expect(combine([m(5, 12, "PHONE")], [r(3, 14, "FINANCIAL")], new Set(LABELS))).toEqual([
      { start: 3, end: 14, label: "PHONE", source: "model+regex" },
    ]);
  });

  it("keeps a level 2 URL visible when level 2 is off, even if the URL pattern matches", () => {
    expect(combine([m(0, 20, "URL_L2")], [r(0, 20, "URL")], new Set(LEVEL1_LABELS))).toEqual([]);
  });

  it("uses the regex label when the model label is switched off", () => {
    expect(combine([m(0, 9, "ORG")], [r(0, 16, "EMAIL")], new Set(["EMAIL"]))).toEqual([
      { start: 0, end: 16, label: "EMAIL", source: "regex" },
    ]);
  });
});

describe("applyRedaction", () => {
  const text = "Call Kim at 010-1234-5678.";
  const spans: Span[] = [m(5, 8, "PERSON_L2"), m(12, 25, "PHONE")];
  it("tags with the label family", () => expect(applyRedaction(text, spans)).toBe("Call [PERSON] at [PHONE]."));
  it("masks per character", () => expect(applyRedaction(text, spans, { mode: "mask" })).toBe("Call *** at *************."));
  it("blanks", () => expect(applyRedaction(text, spans, { mode: "blank" })).toBe("Call  at ."));
  it("uses a custom replacement", () =>
    expect(applyRedaction(text, spans, { replace: (s, o) => `<${s.label}:${o.length}>` })).toBe("Call <PERSON_L2:3> at <PHONE:13>."));
});
