// Doubling tests for the vault middlewares (AI SDK transformParams and the LangChain.js wrapModelCall hook, with a
// stand-in detector): the time of one call at sizes n, 2n and 4n, each the fastest of three runs, with a fresh vault and
// middleware for every run (built before the clock starts). A doubling that costs more than three times as much fails:
// time that grows with the size doubles, time that grows with its square quadruples.
//   - text length                       plain text with a few values and every kind of escape in it
//   - number of known values            one text, a vault that holds more and more values
//   - keys of a JSON document           an object with more and more keys, each value with a known value in it
//   - keys that become one placeholder  keys that are one known value with its white space written another way
//   - repetitions of a known value      one value again and again (also one that overlaps itself)
//   - a long known value                one found once after long partial matches; n words that repeat themselves
//                                       in a text that repeats them, in every spelling the vault searches; and a
//                                       value of n lines written on one line
//   - JSON in strings                   a document in a string of a document, more and more levels deep
//   - readings                          a known value percent-encoded more and more times in a URL
//   - numbers joined in readings        a number after an exponent or a dot in a form field, percent and Unicode
//                                       escapes, signed numbers in other spellings, an address after "4E+"
//   - dates in timestamps and reprs     a known date at the start of ISO timestamps and in Python reprs
//   - known numbers in a model's JSON   an array of objects with known values as JSON numbers (in a model's text
//                                       each becomes a quoted placeholder)
//   - escapes of every kind             a value two steps deep next to escapes of every other kind, again and again
//   - Python repr escapes               a dict and a bytes value written with str(), again and again
//   - case, width and Unicode forms     a value in capitals, an address in full width, Hangul as jamo, again and again
//   - a comma as a line break           an address held with a comma, written over two lines, again and again
//   - keys repeated as written          JSON text that repeats a known value as a key, and a spelling of it, n times
//   - typed rows                        rows with a type of their own ({ type: "contact", ... }): data
//   - base64-looking text               a form field of n words that looks like base64, with a known value in it
//   - base64-looking, nothing known     a string that looks like base64 and holds no known value: binary, kept
//   - typed payloads                    json blocks whose data has a type of its own, and objects typed text with a
//                                       name and a key that are known values (the 23rd outside review, A2)
//   - typed payload depth               a payload nested n deep in json and tool_result blocks
//   - class instances                   a class instance with toJSON that gives n rows
//   - rows typed like blocks            n rows typed file, document, message, ... without a block's own content: data
//                                       (the review of 1.7.1, L3-A1)
//   - class instances as blocks         n class instances a LangChain.js tool returns as json blocks (24th review, A2)
//   - a recorded answer                 an earlier answer of n sentences that the app passes back restored, sent as the
//                                       record the middleware keeps of it ("assistant-recorded"; L4-R1)
//   - capitals with other white space   a name and an address in capitals with other white space, again and again
//                                       (23rd outside review, A4); also n known values whose capitals are not ASCII
//   - full-width escapes                escapes written with full-width characters, again and again (A4)
//   - escapes after runs of white space a name with a run of white space of each kind before its escaped part, again and
//                                       again; and one run of length n, also as a span the detector reports (A5)
//   - readings of the 1.7.1 review      capitals as Turkish, Greek and German write them, a Thai name before a vowel
//                                       sign, a full-width exponent, a long number around an escape, a name with a
//                                       variation selector, full width behind a percent escape, and spans a model
//                                       found with a period and brackets, again and again; n known values with capitals
//                                       of a language; one number of n digits around escapes; a name in conjoining
//                                       jamo with n marks after it
// "other-fields" is a part of a type the AI SDK does not have (every field but its type is text), and the top of a
// LangChain AIMessage's additional_kwargs (data beside its tool calls).
// The fields outside the messages (since 1.7.2: a message name, a tool's description, a property of its schema, the
// response format, provider options and model settings, the application's metadata in them, and descriptions under the
// keywords that hold schemas) are timed along the axes that are about text and keys.
// Sizes are chosen so that the whole file runs in well under a minute.
import { describe, expect, it } from "vitest";
import { AIMessage, HumanMessage, SystemMessage, ToolMessage, toolStrategy, type BaseMessage } from "langchain";
import { piiMiddleware as aiSdkMiddleware } from "../src/ai-sdk";
import { piiMiddleware as langchainMiddleware } from "../src/langchain";
import type { Redactor } from "../src/redactor";
import type { Span } from "../src/types";
import { createVault } from "../src/vault";

type Position = "user" | "system" | "tool-text" | "tool-data" | "block-data" | "assistant-unrecorded" | "assistant-recorded" | "tool-input-unrecorded" | "other-fields";
type Framework = "ai-sdk" | "langchain";

/** One input: the text (or JSON data) at a position, the values the vault holds before the call, and the values the
 *  stand-in detector reports (every occurrence). */
interface Input {
  text: string;
  /** assistant-recorded: what the model wrote, remembered for the text the app passes back. */
  record?: string;
  data?: unknown;
  known: readonly (readonly [string, string])[];
  detect: readonly (readonly [string, string])[];
}

const BACKSLASH = String.fromCharCode(92);
// white space as the vault has it (any run of it matches the space of a known value)
const WHITE = [0x20, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000].map((c) => String.fromCharCode(c));
// plain words with what makes other readings of a text: "+" and "%" (URL forms) and backslashes (JSON escapes)
const FILLER = `the order a+b came at 50%25 off, saved under C:${BACKSLASH}tmp${BACKSLASH}x by the desk. `;
// the same without backslashes, for text that goes inside JSON strings again and again
const WORDS = "the order a+b came at 50%25 off and was sent on by the desk. ";
// numbers next to exponents and dots in every reading (a form field, percent and Unicode escapes), signed numbers in
// other spellings, and values that are not numbers after "<digit>e+" (a known 4524, address and phone number)
const NUMBERS_LINE = `q=Unit+4E+12+Main+St%2C+Seoul&ext=1e%2B4524&w=1e${BACKSLASH}u002B4524&v=4524%2E7 and delta -4.524e3, -4524; ext 2e+415 555 0123. `;
const NUMBERS_KNOWN: readonly (readonly [string, string])[] = [["ID_NUMBER", "4524"], ["ADDRESS", "12 Main St, Seoul"], ["PHONE", "415 555 0123"]];
// a known date (1990-01-02) at the start of ISO timestamps and in Python reprs, next to another date format that stays
const DATES_LINE = "born 1990-01-02T00:00:00.000Z, {'dob': datetime.date(1990, 1, 2)} and 1990-01-02T09:30:00+09:00; not 01/02/1990. ";
// a line break, a path, a form field, a percent escape and a Python repr escape next to an address encoded twice
const MIXED = `line${BACKSLASH}nC:${BACKSLASH}${BACKSLASH}tmp q=a+b x%20y {'k': 'x${BACKSLASH}xa0y'} mail kim%2540example.com now. `;
// a dict and a bytes value as Python's str() writes them
const REPR = `{'name': 'Kim${BACKSLASH}xa0Minsu', 'raw': b'Jos${BACKSLASH}xc3${BACKSLASH}xa9 Garc${BACKSLASH}xc3${BACKSLASH}xada'} `;
const wide = (s: string) => Array.from(s, (c) => String.fromCharCode(c.charCodeAt(0) + 0xfee0)).join("");
// a name in capitals, an e-mail address in full width, a Hangul name as conjoining jamo
const FORMS = `KIM MINSU wrote from ${wide("kim@example.com")} to ${"김민수".normalize("NFD")}님 today. `;

// a name and an address in capitals with other white space (the 23rd outside review, A4)
const CAPITALS_LINE = "Signed: JOS\u00c9 \t GARC\u00cdA, to CALLE MAYOR 5\n  M\u00c1LAGA; ";
const CAPITALS_KNOWN: readonly (readonly [string, string])[] = [["PERSON", "Jos\u00e9 Garc\u00eda"], ["ADDRESS", "Calle Mayor 5, M\u00e1laga"]];
// escapes written with full-width characters (A4): a percent sign, hex digits, a backslash, a form field's plus, and
// full-width signs in ordinary text
const WIDE_ESCAPES_LINE = `mail kim\uff0540example.com, name=Kim\uff0b${wide("Minsu")} or Kim\uff3cxa0Minsu at ${wide("50%")} off ${wide("1+1")}. `;
// the natives review (lens 5): a value of two Hangul syllables with particles (U+C9C0 U+C6D0 followed by U+B2D8 U+AED8
// U+C11C) and inside longer words (kept), a Greek name in capitals without accents, a German one with SS and with the
// capital sharp s, a Han name with a variation selector, a Hangul name with a combining mark, and two custom values that
// merge (every character outside ASCII built from its code point)
const cps = (...c: number[]) => String.fromCodePoint(...c);
const JIWON = cps(0xc9c0, 0xc6d0);
const WATANABE = cps(0x6e21, 0x8fba);
const KIM_KO = cps(0xae40, 0xbbfc, 0xc218);
const GREEK_CAPS = cps(0x39d, 0x399, 0x39a, 0x39f, 0x3a3, 0x20, 0x3a0, 0x391, 0x3a0, 0x391, 0x394, 0x39f, 0x3a0, 0x39f, 0x3a5, 0x39b, 0x39f, 0x3a3);
const NATIVES_LINE =
  `${JIWON}${cps(0xb2d8, 0xaed8, 0xc11c)} ${JIWON}${cps(0xae08, 0xc744)} ${JIWON}${cps(0xd588, 0xb2e4)}; ${GREEK_CAPS}, ` +
  `ANNA WEISS and ANNA WEI${cps(0x1e9e)}; ${WATANABE}${cps(0xe0100, 0x3055, 0x3093)}, ${KIM_KO}${cps(0x301, 0xb2d8)}, AB-1234-CD. `;
const NATIVES_KNOWN: readonly (readonly [string, string])[] = [
  ["PERSON", JIWON],
  ["PERSON", cps(0x39d, 0x3af, 0x3ba, 0x3bf, 0x3c2, 0x20, 0x3a0, 0x3b1, 0x3c0, 0x3b1, 0x3b4, 0x3cc, 0x3c0, 0x3bf, 0x3c5, 0x3bb, 0x3bf, 0x3c2)],
  ["PERSON", `Anna Wei${cps(0xdf)}`],
  ["PERSON", WATANABE],
  ["PERSON", KIM_KO],
  ["CUSTOMER", "AB-1234"],
  ["CUSTOMER", "1234-CD"],
];
// a run of white space of each kind between a name and its escaped part (A5)
const RUNS_LINE = `Contact: Kim${" ".repeat(45)}%4Dinsu, %4Bim${"\n".repeat(45)}Minsu and KIM${"\u3000".repeat(45)}%4DINSU. `;

// the readings of the 1.7.1 review (group "readings"): capitals as Turkish, Greek and German write them, a Thai name
// before a vowel sign, a full-width exponent, a number spelled longer than a known value's reach around an escape, a
// name with a variation selector next to full width, a full-width "@" behind a percent escape, and spans a model found
// with a sentence's period and a pair of brackets around them
const cp = (...cps: number[]) => String.fromCodePoint(...cps);
const R171_KNOWN: readonly (readonly [string, string])[] = [
  ["PERSON", `Emine ${cp(0x15e)}ahin`],
  ["PERSON", `${cp(0x395, 0x3bb, 0x3ad, 0x3bd, 0x3b7)} ${cp(0x3a0, 0x3b1, 0x3c0, 0x3b1, 0x3b4, 0x3bf, 0x3c0, 0x3bf, 0x3cd, 0x3bb, 0x3bf, 0x3c5)}`],
  ["ADDRESS", `M${cp(0xfc)}llerstra${cp(0xdf)}e 5, 10115 Berlin`],
  ["PERSON", cp(0xe2a, 0xe21)],
  ["ID_NUMBER", "4524"],
  ["PERSON", cp(0x6e21, 0x8fba)],
  ["EMAIL", "kim@example.com"],
];
const R171_LINE =
  `M${cp(0xfc, 0x15f)}teri: EM${cp(0x130)}NE ${cp(0x15e)}AH${cp(0x130)}N, ${cp(0x395, 0x39b, 0x395, 0x39d, 0x397)} ` +
  `${cp(0x3a0, 0x391, 0x3a0, 0x391, 0x394, 0x39f, 0x3a0, 0x39f, 0x3a5, 0x39b, 0x39f, 0x3a5)}, M${cp(0xdc)}LLERSTRA${cp(0x1e9e)}E 5, 10115 BERLIN; ` +
  `${cp(0xe01, 0xe23, 0xe38, 0xe13, 0xe32, 0xe2a, 0xe21, 0xe31, 0xe04, 0xe23)} x=${wide("1e+4524")} y=4.52400000000000000000e%2B03 ` +
  `${cp(0x6e21, 0x8fba, 0xe0100, 0x3055, 0x3093)}${wide("(x)")} mail kim%EF%BC%8540example.com. I'm Kim Minsu. Call (Lee Jisoo) now. `;
const R171_DETECT: readonly (readonly [string, string])[] = [["Kim Minsu.", "PERSON"], ["(Lee Jisoo)", "PERSON"]];

/** A short run of letters for each number: a, b, ..., z, ba, bb, ... */
const letters = (i: number): string => {
  let s = "";
  do {
    s = String.fromCharCode(97 + (i % 26)) + s;
    i = Math.floor(i / 26);
  } while (i > 0);
  return s;
};

/** The first `n` spellings of "Kim Minsu" with its space written as other runs of white space. */
const spellings = (n: number): string[] => {
  const out: string[] = [];
  for (let length = 1; out.length < n; length++) {
    const at = new Array<number>(length).fill(0);
    for (;;) {
      out.push(`Kim${at.map((i) => WHITE[i]).join("")}Minsu`);
      if (out.length >= n) break;
      let p = length - 1;
      while (p >= 0 && ++at[p] === WHITE.length) at[p--] = 0;
      if (p < 0) break;
    }
  }
  return out;
};

const finder = (values: Input["detect"]): Redactor => {
  const detect = async (text: string): Promise<Span[]> => {
    const all: Span[] = [];
    for (const [value, label] of values) {
      for (let i = text.indexOf(value); i >= 0; i = text.indexOf(value, i + 1)) all.push({ start: i, end: i + value.length, label, source: "model" });
    }
    all.sort((a, b) => a.start - b.start || b.end - a.end);
    const out: Span[] = [];
    for (const s of all) if (out.length === 0 || s.start >= out[out.length - 1].end) out.push(s);
    return out;
  };
  return { sensitivity: 0, detect } as unknown as Redactor;
};

const dataOf = (input: Input) => (input.data !== undefined ? input.data : { note: input.text });

// the call shapes of vault-corpus.test.ts
const userText = (text: string) => ({ role: "user", content: [{ type: "text", text }] });
const toolCall = (id: string, input: unknown) => ({ role: "assistant", content: [{ type: "tool-call", toolCallId: id, toolName: "lookup", input }] });
const toolResult = (id: string, output: unknown) => ({ role: "tool", content: [{ type: "tool-result", toolCallId: id, toolName: "lookup", output }] });

function aiSdkPrompt(position: Position, input: Input): unknown[] {
  const { text } = input;
  switch (position) {
    case "user":
      return [userText(text)];
    case "system":
      return [{ role: "system", content: text }, userText("hi")];
    case "tool-text":
      return [userText("hi"), toolCall("c1", {}), toolResult("c1", { type: "text", value: text })];
    case "tool-data":
      return [userText("hi"), toolCall("c1", {}), toolResult("c1", { type: "json", value: dataOf(input) })];
    case "block-data":
      return [userText("hi"), toolCall("c1", {}), toolResult("c1", { type: "content", value: [{ type: "json", value: dataOf(input) }] })];
    case "assistant-unrecorded":
    case "assistant-recorded":
      return [userText("hi"), { role: "assistant", content: [{ type: "text", text }] }, userText("thanks")];
    case "tool-input-unrecorded":
      return [userText("hi"), toolCall("c9", dataOf(input)), toolResult("c9", { type: "text", value: "ok" })];
    case "other-fields":
      return [{ role: "user", content: [{ ...fieldsOf(input), type: "future-part" }] }];
  }
}

/** The data as the fields of an object: an object as it is, anything else under "items". */
const fieldsOf = (input: Input): Record<string, unknown> => {
  const data = dataOf(input);
  return data !== null && typeof data === "object" && !Array.isArray(data) && Object.getPrototypeOf(data) === Object.prototype ? (data as Record<string, unknown>) : { items: data };
};

interface LcRequest {
  messages: BaseMessage[];
  systemMessage?: SystemMessage;
  systemPrompt?: string;
}
type Hook = (request: LcRequest, handler: (r: LcRequest) => Promise<AIMessage>) => Promise<AIMessage>;

/** The request the agent gives the middlewares (createAgent: the system prompt as a message of one text block). */
function langchainRequest(position: Position, input: Input): LcRequest {
  const { text } = input;
  const data = dataOf(input);
  const call = (id: string, args: unknown) => new AIMessage({ content: "", tool_calls: [{ id, name: "lookup", args: args as Record<string, unknown> }] });
  const result = (content: unknown) => new ToolMessage({ content: content as ToolMessage["content"], tool_call_id: "c1" });
  const none = { systemMessage: new SystemMessage(""), systemPrompt: "" };
  switch (position) {
    case "user":
      return { messages: [new HumanMessage(text)], ...none };
    case "system":
      return { messages: [new HumanMessage("hi")], systemMessage: new SystemMessage({ content: [{ type: "text", text }] }), systemPrompt: text };
    case "tool-text":
      return { messages: [new HumanMessage("hi"), call("c1", {}), result(text)], ...none };
    case "tool-data":
      return { messages: [new HumanMessage("hi"), call("c1", {}), result(Array.isArray(data) ? data : [data])], ...none };
    case "block-data":
      return { messages: [new HumanMessage("hi"), call("c1", {}), result([{ type: "json", json: data }])], ...none };
    case "assistant-unrecorded":
    case "assistant-recorded":
      return { messages: [new HumanMessage("hi"), new AIMessage(text), new HumanMessage("thanks")], ...none };
    case "tool-input-unrecorded":
      return { messages: [new HumanMessage("hi"), call("c9", data), new ToolMessage({ content: "ok", tool_call_id: "c9" })], ...none };
    case "other-fields":
      return { messages: [new HumanMessage("hi"), new AIMessage({ content: "ok", additional_kwargs: fieldsOf(input) }), new HumanMessage("thanks")], ...none };
  }
}

/** One call, ready to run: the vault, the middleware and the input are made here, before the clock starts. The run
 *  gives what the provider receives (with LangChain, the messages the agent builds from the request the middleware
 *  handed on: its system message first, as in vault-corpus.test.ts). */
function prepare(framework: Framework, position: Position, input: Input): () => Promise<unknown> {
  const vault = createVault();
  for (const [label, value] of input.known) vault.tag(label, value);
  if (position === "assistant-recorded") vault.remember(input.text, input.record ?? input.text);
  const redactor = finder(input.detect);
  if (framework === "ai-sdk") {
    const mw = aiSdkMiddleware({ redactor, vault });
    const params = { prompt: aiSdkPrompt(position, input) };
    return async () => (await mw.transformParams({ params })).prompt;
  }
  const hook = langchainMiddleware({ redactor, vault }).wrapModelCall as unknown as Hook;
  const request = langchainRequest(position, input);
  return async () => {
    let got: LcRequest | undefined;
    await hook(request, async (r) => ((got = r), new AIMessage("ok")));
    const system = got!.systemMessage ?? request.systemMessage!;
    return [...(system.text === "" ? [] : [system]), ...got!.messages].map((m) => ({ type: m.type, content: m.content, tool_calls: (m as AIMessage).tool_calls, additional_kwargs: m.additional_kwargs }));
  };
}

/** The fastest of three runs, in milliseconds, and what the provider received in the last one. */
async function fastest(framework: Framework, position: Position, input: Input): Promise<{ ms: number; sent: unknown }> {
  let ms = Infinity;
  let sent: unknown;
  for (let i = 0; i < 3; i++) {
    const run = prepare(framework, position, input);
    const t0 = performance.now();
    sent = await run();
    ms = Math.min(ms, performance.now() - t0);
  }
  return { ms, sent };
}

interface Growth {
  name: string;
  /** The smallest size; the others are twice and four times it. */
  n: number;
  positions: readonly Position[];
  input: (n: number) => Input;
  /** A check of what the provider received at the largest size, that the run did what it is meant to measure (the
   *  vault's known values are hidden everywhere; what only the detector reports is not looked for everywhere). */
  check?: (sent: string, n: number) => void;
}

const KIM: readonly [string, string] = ["PERSON", "Kim Minsu"];
const EMAIL: readonly [string, string] = ["EMAIL", "kim@example.com"];

/** A block a LangChain.js tool returns as a class instance (the 24th outside review, A2). */
class JsonBlock {
  readonly type = "json";
  constructor(readonly json: unknown) {}
}

/** Rows a class instance gives as its JSON (what a provider is sent for it). */
class Rows {
  constructor(private readonly rows: unknown[]) {}
  toJSON() {
    return { rows: this.rows };
  }
}

const GROWTH: readonly Growth[] = [
  {
    name: "text length",
    n: 400,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: `Kim Minsu wrote. ${FILLER.repeat(n)} Reply to kim@example.com`, known: [KIM], detect: [["kim@example.com", "EMAIL"]] }),
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu/),
  },
  {
    name: "number of known values",
    n: 2000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({
      text: `${Array.from({ length: 10 }, (_, i) => `Kim${letters(i)} Lee${letters(i)}`).join(", ")} wrote. ${FILLER.repeat(40)}`,
      known: Array.from({ length: n }, (_, i) => ["PERSON", `Kim${letters(i)} Lee${letters(i)}`] as const),
      detect: [],
    }),
    check: (sent) => expect(sent).not.toMatch(/Kima Leea/),
  },
  {
    name: "keys of a JSON document",
    n: 1500,
    positions: ["user", "system", "tool-text", "tool-data", "block-data", "tool-input-unrecorded"],
    input: (n) => {
      const data = Object.fromEntries(Array.from({ length: n }, (_, i) => [`field_${letters(i)}`, `note ${letters(i)} for Kim Minsu at kim@example.com`]));
      return { text: JSON.stringify(data), data, known: [KIM], detect: [["kim@example.com", "EMAIL"]] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu/),
  },
  {
    name: "keys that become one placeholder",
    n: 4000,
    // (in a model's text too: its keys are told apart after it is masked as text, the review of 1.7.1, L4-K2)
    positions: ["user", "system", "tool-text", "tool-data", "block-data", "assistant-unrecorded", "tool-input-unrecorded"],
    input: (n) => {
      const data = Object.fromEntries(spellings(n).map((k, i) => [k, i]));
      return { text: JSON.stringify(data), data, known: [KIM], detect: [] };
    },
    check: (sent, n) => {
      // every key became the one placeholder, told apart by " #2", " #3", ...
      expect(sent).not.toMatch(/Kim/);
      expect(sent).toContain(`[PERSON_1] #${n}`);
    },
  },
  {
    name: "repetitions of a known value",
    n: 4000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: "Kim Minsu, ".repeat(n), known: [KIM], detect: [["Kim Minsu", "PERSON"]] }),
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu/),
  },
  {
    name: "repetitions of a known value that overlaps itself",
    n: 4000,
    positions: ["user", "system", "tool-text", "assistant-unrecorded"],
    input: (n) => ({ text: "Lee ".repeat(n), known: [["PERSON", "Lee Lee"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/Lee Lee/),
  },
  {
    // (1.7.1 gate 7: a value the model found over a whole text, ". . . .Kim Minsu", searched for in that text: Swift,
    // Kotlin and Dart compared it from its start at every place)
    name: "a long known value found once after long partial matches",
    n: 16000,
    // (the detector reports the value: not in a system message, which only known values are looked for in)
    positions: ["user", "tool-text", "assistant-unrecorded"],
    input: (n) => {
      const text = " .".repeat(n) + "Kim Minsu" + ", ".repeat(n);
      return { text, known: [], detect: [[text, "PERSON"]] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu/),
  },
  {
    // (n words that repeat themselves, in a text that repeats them: in any ASCII case, with other white space, a URL whose
    // path must be written as it is, and capitals of another spelling; each place where a value may start is looked at once)
    name: "a long known value that overlaps itself",
    n: 1000,
    positions: ["user", "system", "tool-text", "assistant-unrecorded"],
    input: (n) => {
      const words = (w: string[]) => Array.from({ length: n }, (_, i) => w[i % w.length]).join(" ");
      const text = ["hA Ha ".repeat(n), "Ha\tha\t".repeat(n), "HTTPS://X.IO/" + "https://x.io/".repeat(2 * n - 1), "WEISS ".repeat(2 * n)].join("\n");
      const known: (readonly [string, string])[] = [
        ["PERSON", words(["Ha", "ha"])],
        ["URL", "https://x.io/".repeat(n)],
        ["PERSON", words(["Wei" + String.fromCharCode(0xdf)])],
      ];
      return { text, known, detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/hA Ha|x\.io|WEISS/i),
  },
  {
    // (a value of n lines of two words, in a text that writes its lines on one line, twice: one pass over the text with
    // every ", " as a space)
    name: "a long known value of many lines that overlaps itself",
    n: 4000,
    positions: ["user", "system", "tool-text", "assistant-unrecorded"],
    input: (n) => ({ text: "Ha ha ".repeat(2 * n), known: [["ADDRESS", Array(n).fill("Ha ha").join("\n")]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/Ha ha Ha/),
  },
  {
    // (from 3 levels on: the inner strings gain readings up to the fourth step, a bounded step that only the part of
    // the text around their escapes pays; beyond 12 levels the size itself grows as 2^levels, since each level escapes
    // the backslashes of the one inside it)
    name: "levels of JSON in strings",
    n: 3,
    positions: ["user", "system", "tool-text", "tool-data", "tool-input-unrecorded"],
    input: (n) => {
      let text = JSON.stringify({ note: `Kim Minsu wrote: ${WORDS.repeat(800)}` });
      for (let level = 1; level < n; level++) text = JSON.stringify({ level, inner: text });
      return { text, data: JSON.parse(text), known: [KIM], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu/),
  },
  {
    name: "escapes of every kind",
    n: 200,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: MIXED.repeat(n), known: [["EMAIL", "kim@example.com"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/kim%2540/),
  },
  {
    name: "Python repr escapes",
    n: 200,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: REPR.repeat(n), known: [KIM, ["PERSON", "José García"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/xa0Minsu|Garc/),
  },
  {
    name: "case, width and Unicode forms",
    n: 200,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: FORMS.repeat(n), known: [KIM, ["EMAIL", "kim@example.com"], ["PERSON", "김민수"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/KIM MINSU|ｋｉｍ/),
  },
  {
    name: "a comma as a line break",
    n: 400,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: "Ship to 12 Main St\nSeoul today. ".repeat(n), known: [["ADDRESS", "12 Main St, Seoul"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/Main St/),
  },
  {
    // (no check: encodings are read up to four steps deep, by decision, so at eight the value is not found)
    name: "readings (percent-encoded again and again)",
    n: 2,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => {
      let value = "Kim Minsu";
      for (let i = 0; i < n; i++) value = encodeURIComponent(value);
      return { text: `see https://crm.test/c?name=${value}&id=7 and ${WORDS}`.repeat(400), known: [KIM], detect: [] };
    },
  },
  {
    // a known value found in one reading is dropped where any reading joins it to a number (A1, B2), and a sign stays
    // outside a known number in another spelling (N4)
    name: "numbers joined in readings",
    n: 100,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: NUMBERS_LINE.repeat(n), known: NUMBERS_KNOWN, detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/Main\+St|4\.524e3|555 0123/),
  },
  {
    // a known date followed by "T" and a digit, and in a Python date repr (S17)
    name: "dates in timestamps and reprs",
    n: 1000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: DATES_LINE.repeat(n), known: [["DATE_OF_BIRTH", "1990-01-02"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/1990-01-02|1990, 1, 2/),
  },
  {
    // an earlier answer that is a JSON document: a number that holds a known value is written as a string first, so
    // that its placeholder is quoted (r07-A4); in data, a number is a placeholder string
    name: "known numbers in a model's JSON",
    n: 1000,
    positions: ["user", "tool-data", "assistant-unrecorded", "tool-input-unrecorded"],
    input: (n) => {
      const text = `[${Array.from({ length: n }, (_, i) => `{"id": ${i}, "phone": ${i % 2 === 0 ? 2025550148 : 3000000 + i}, "d": -4.524e3}`).join(", ")}]`;
      return { text, data: JSON.parse(text), known: [["PHONE", "2025550148"], ["ID_NUMBER", "4524"]], detect: [] };
    },
    // (no known value, and no placeholder left out of a JSON string in a model's text)
    check: (sent) => {
      expect(sent).not.toMatch(/2025550148|4\.524e3/);
      expect(sent).not.toMatch(/: -?\[[A-Z]/);
    },
  },
  {
    // keys that were the same before masking stay the same; the spelling becomes one more key (" #2")
    name: "keys repeated as written",
    n: 1000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => {
      const pairs = [...Array.from({ length: n }, (_, i) => `"Kim Minsu": ${i}`), ...Array.from({ length: n }, (_, i) => `"Kim%20Minsu": ${i}`)];
      return { text: `{${pairs.join(", ")}}`, known: [KIM], detect: [] };
    },
    check: (sent) => {
      expect(sent).not.toMatch(/Kim/);
      expect(sent).not.toContain("#3");
    },
  },
  {
    name: "typed rows",
    n: 200,
    positions: ["user", "tool-text", "tool-data", "block-data", "other-fields"],
    input: (n) => {
      const data = Array.from({ length: n }, (_, i) => ({ type: "contact", name: "Kim Minsu", id: `kim@example.com/${i}`, [`kim@example.com ${i}`]: i }));
      return { text: JSON.stringify(data), data, known: [KIM, EMAIL], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu|kim@example\.com/),
  },
  {
    // (the 23rd outside review, A2) json blocks whose data has a type of its own, and objects typed text with data
    name: "typed payloads",
    n: 200,
    positions: ["user", "tool-text", "tool-data", "block-data", "other-fields"],
    input: (n) => {
      const data = Array.from({ length: n }, (_, i) =>
        i % 2 ? { type: "json", data: { type: "text", name: "Kim Minsu", [`Kim Minsu ${i}`]: i } } : { type: "text", name: "Kim Minsu", [`kim@example.com ${i}`]: i },
      );
      return { text: JSON.stringify(data), data, known: [KIM, EMAIL], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu|kim@example\.com/),
  },
  {
    // a payload nested n deep in json and tool_result blocks, a typed object with data at the bottom
    name: "typed payload depth",
    n: 100,
    positions: ["tool-data", "block-data", "other-fields"],
    input: (n) => {
      let data: unknown = [{ type: "text", name: "Kim Minsu", "Kim Minsu": "open" }];
      for (let i = 0; i < n; i++) data = [i % 2 ? { type: "json", data } : { type: "tool_result", tool_use_id: `c${i}`, content: data }];
      return { text: JSON.stringify(data), data, known: [KIM], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu/),
  },
  {
    name: "base64-looking text",
    n: 8000,
    positions: ["user", "tool-text", "tool-data", "block-data", "other-fields"],
    input: (n) => ({ text: `Kim+Minsu+asked+${Array.from({ length: n }, (_, i) => `note${letters(i)}`).join("+")}`, known: [KIM], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/Kim\+Minsu/),
  },
  {
    // a base64-looking string that holds no known value in any reading is binary data and is kept: the check reads it
    // in every reading (G4), in time that grows with its length
    name: "base64-looking text with nothing known",
    n: 8000,
    positions: ["other-fields"],
    input: (n) => ({ text: "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo+abc/def+ghi".repeat(n), known: [KIM], detect: [] }),
    check: (sent, n) => expect(sent).toContain("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo+abc/def+ghi".repeat(n)),
  },
  {
    name: "class instances",
    n: 300,
    positions: ["tool-data", "block-data", "other-fields"],
    input: (n) => {
      const rows = Array.from({ length: n }, (_, i) => ({ name: "Kim Minsu", email: "kim@example.com", n: i }));
      return { text: JSON.stringify({ rows }), data: new Rows(rows), known: [KIM, EMAIL], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu|kim@example\.com/),
  },
  {
    // (the review of 1.7.1, L3-A1) rows typed like blocks without a block's content: data, read by the model
    name: "rows typed like blocks",
    n: 200,
    positions: ["user", "tool-text", "tool-data", "block-data", "other-fields"],
    input: (n) => {
      const kinds = ["file", "document", "message", "url", "text", "json", "image", "function", "thinking", "tool_result"];
      const owner = { type: "user", name: "Kim Minsu", login: "kim@example.com" };
      const data = Array.from({ length: n }, (_, i) => ({ type: kinds[i % kinds.length], id: `kim@example.com/${i}`, name: `Kim Minsu ${i}.pdf`, owned_by: owner, [`kim@example.com ${i}`]: i }));
      return { text: JSON.stringify(data), data, known: [KIM, EMAIL], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu|kim@example\.com/),
  },
  {
    // (the 24th outside review, A2) class instances a LangChain.js tool returns as blocks: read as the JSON they give
    name: "class instances as blocks",
    n: 300,
    positions: ["tool-data", "block-data", "other-fields"],
    input: (n) => {
      const blocks = Array.from({ length: n }, (_, i) => new JsonBlock({ name: "Kim Minsu", email: "kim@example.com", n: i }));
      return { text: JSON.stringify(blocks), data: blocks, known: [KIM, EMAIL], detect: [] };
    },
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu|kim@example\.com/),
  },
  {
    // (the review of 1.7.1, L4-R1) an earlier answer the app passes back restored, which the middleware has a record of
    name: "a recorded answer",
    n: 400,
    positions: ["assistant-recorded"],
    input: (n) => ({ text: "Kim Minsu wrote to kim@example.com. ".repeat(n), record: "[PERSON_1] wrote to [EMAIL_1]. ".repeat(n), known: [KIM, EMAIL], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/Kim Minsu|kim@example\.com/),
  },
  {
    // (the 23rd outside review, A4: the lines of a value in capitals, searched with other white space)
    name: "capitals with other white space",
    n: 200,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: CAPITALS_LINE.repeat(n), known: CAPITALS_KNOWN, detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/GARC|LAGA/),
  },
  {
    name: "number of known values in capitals with other white space",
    n: 1000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({
      text: `${Array.from({ length: 10 }, (_, i) => `JOS\u00c9${letters(i).toUpperCase()} \t GARC\u00cdA${letters(i).toUpperCase()}`).join(", ")} wrote. ${FILLER.repeat(40)}`,
      known: Array.from({ length: n }, (_, i) => ["PERSON", `Jos\u00e9${letters(i)} Garc\u00eda${letters(i)}`] as const),
      detect: [],
    }),
    check: (sent) => expect(sent).not.toMatch(/GARC\u00cdAA/),
  },
  {
    // (A4: full-width characters read as ASCII before escapes are decoded)
    name: "full-width escapes",
    n: 200,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: WIDE_ESCAPES_LINE.repeat(n), known: [KIM, EMAIL], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/40example|insu/),
  },
  {
    // (A5: the part read around an escape reaches over runs of white space; the runs are walked over a bounded number of
    // times)
    name: "escapes after runs of white space",
    n: 100,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: RUNS_LINE.repeat(n), known: [KIM], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/insu/),
  },
  {
    // (A5: one value with a run of white space as long as the text, also as a span the detector reports, which is read as
    // a whole: what no reading changes counts its white space as nothing)
    name: "length of a run of white space next to an escape",
    n: 20000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => {
      const value = `Kim${" ".repeat(n)}%4Dinsu`;
      return { text: `Contact: ${value}, %41${"\n".repeat(n)}%42 and ${"\t".repeat(n)}x%43.`, known: [KIM], detect: [[value, "PERSON"]] };
    },
    check: (sent) => expect(sent).not.toMatch(/insu/),
  },
  {
    // (the 1.7.1 review, group "readings": every reading applies to what a decoding step gives, and the spans a model
    // finds lose what their boundary took)
    name: "readings of the 1.7.1 review",
    n: 50,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: R171_LINE.repeat(n), known: R171_KNOWN, detect: R171_DETECT }),
    check: (sent) => expect(sent).not.toMatch(new RegExp(`EM${cp(0x130)}NE|${cp(0x395, 0x39b, 0x395, 0x39d, 0x397)}|LLERSTRA|${cp(0x8fba)}|40example|52400`)),
  },
  {
    // (capitals as a language writes them: each known value has more spellings to look for)
    name: "number of known values with capitals of a language",
    n: 1000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({
      // (in Turkish capitals, and in German capitals with a capital sharp s)
      text: `${Array.from({ length: 10 }, (_, i) => (i % 2 ? `FATIH${letters(i).toUpperCase()} WEI${cp(0x1e9e)}${letters(i).toUpperCase()}` : `FAT${cp(0x130)}H${letters(i).toUpperCase()} WE${cp(0x130)}SS${letters(i).toUpperCase()}`)).join(", ")} wrote. ${FILLER.repeat(40)}`,
      known: Array.from({ length: n }, (_, i) => ["PERSON", `Fatih${letters(i)} Wei${cp(0xdf)}${letters(i)}`] as const),
      detect: [],
    }),
    check: (sent) => expect(sent).not.toMatch(new RegExp(`FAT${cp(0x130)}HA|FATIHB`)),
  },
  {
    // (L4-REACH: a number is read whole around an escape, however many digits it is written with)
    name: "length of a number written with an escape",
    n: 20000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: `x=4.524${"0".repeat(n)}e%2B03 and 1${"0".repeat(n)}%2E5 y`, known: [["ID_NUMBER", "4524"]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(/x=4\.524/),
  },
  {
    // (L5-A2: a hit that ends inside a part the reading composed covers the part: a name in conjoining jamo with a run of
    // marks after it, full width elsewhere)
    name: "length of a run of marks after a value",
    n: 20000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: `${cp(0xae40, 0xbbfc, 0xc218).normalize("NFD")}${cp(0x301).repeat(n)} ${wide("(x)")}`, known: [["PERSON", cp(0xae40, 0xbbfc, 0xc218)]], detect: [] }),
    check: (sent) => expect(sent).not.toMatch(new RegExp(cp(0x1100, 0x1175))),
  },
  {
    // (the natives review, lens 5: capitals in other spellings, a value of two Hangul syllables with particles and
    // inside longer words, values followed by a variation selector and a combining mark, custom values that merge)
    name: "capitals, two Hangul syllables and clusters",
    n: 100,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({ text: NATIVES_LINE.repeat(n), known: NATIVES_KNOWN, detect: [] }),
    check: (sent) => {
      for (const leak of ["WEISS", `WEI${cps(0x1e9e)}`, GREEK_CAPS, "AB-1234"]) expect(sent).not.toContain(leak);
    },
  },
  {
    // (lens 5: a known value followed by a run of n combining marks, taken in whole)
    name: "length of a run of marks after a value (lens 5: whole clusters)",
    n: 2000,
    positions: ["user", "system", "tool-text", "tool-data", "assistant-unrecorded"],
    input: (n) => ({
      text: `${WATANABE}${cps(0x301).repeat(n)}${cps(0x3055, 0x3093)}, ${KIM_KO}${cps(0x302e).repeat(n)}.`,
      known: [["PERSON", WATANABE], ["PERSON", KIM_KO]],
      detect: [],
    }),
    check: (sent) => {
      for (const leak of [WATANABE, KIM_KO]) expect(sent).not.toContain(leak);
    },
  },
];

const FRAMEWORKS: readonly Framework[] = ["ai-sdk", "langchain"];
const LIMIT = 3;

// Fields outside the messages (the shapes of vault-corpus.test.ts; the AI SDK has no message names, LangChain no
// provider options of a message) ------------------------------------------------------------------------------------

// (option-metadata and schema-nested: the paths of the 23rd outside review, the application's metadata in provider
// options and model settings, and descriptions under the keywords that hold schemas)
type FieldPosition = "message-name" | "tool-description" | "tool-schema" | "response-format" | "call-options" | "message-options" | "option-metadata" | "schema-nested";
const FIELD_POSITIONS: Record<Framework, readonly FieldPosition[]> = {
  "ai-sdk": ["tool-description", "tool-schema", "response-format", "call-options", "message-options", "option-metadata", "schema-nested"],
  langchain: ["message-name", "tool-description", "tool-schema", "response-format", "call-options", "option-metadata", "schema-nested"],
};
/** A schema with the text in a description under keywords that hold schemas, and in a literal that stays. */
const nestedSchema = (text: string) => ({
  type: "object",
  properties: { q: { anyOf: [{ type: "string", description: text }], const: { note: "kept" } }, r: { type: "array", items: { description: text } } },
  $defs: { d: { description: text } },
});
const FIELD_AXES = new Set(["text length", "number of known values", "keys of a JSON document", "repetitions of a known value"]);

interface LcFieldRequest extends LcRequest {
  tools?: unknown[];
  responseFormat?: unknown;
  modelSettings?: Record<string, unknown>;
}

/** One call with the input's text in a field outside the messages, ready to run (made before the clock starts). */
function prepareField(framework: Framework, position: FieldPosition, input: Input): () => Promise<unknown> {
  const vault = createVault();
  for (const [label, value] of input.known) vault.tag(label, value);
  const redactor = finder(input.detect);
  const { text } = input;
  const schema = { type: "object", properties: { q: { type: "string", description: text, examples: [text] } } };
  if (framework === "ai-sdk") {
    const mw = aiSdkMiddleware({ redactor, vault });
    const prompt = [userText("hi")];
    const params =
      position === "tool-description"
        ? { prompt, tools: [{ type: "function", name: "lookup", description: text, inputSchema: { type: "object", properties: {} } }] }
        : position === "tool-schema"
          ? { prompt, tools: [{ type: "function", name: "lookup", description: "Looks up a customer", inputSchema: schema }] }
          : position === "response-format"
            ? { prompt, responseFormat: { type: "json", name: "answer", description: text, schema } }
            : position === "call-options"
              ? { prompt, providerOptions: { openai: { user: text } } }
              : position === "option-metadata"
                ? { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { openai: { metadata: { name: text } } } }] }], providerOptions: { openai: { metadata: { name: text, id: text } } } }
                : position === "schema-nested"
                  ? { prompt, tools: [{ type: "function", name: "lookup", description: "Looks up a customer", inputSchema: nestedSchema(text) }] }
                  : { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { custom: { note: text } } }], providerOptions: { openai: { user: text } } }] };
    return async () => mw.transformParams({ params } as never);
  }
  const hook = langchainMiddleware({ redactor, vault }).wrapModelCall as unknown as (r: LcFieldRequest, h: (r: LcFieldRequest) => Promise<AIMessage>) => Promise<AIMessage>;
  const base = { messages: [new HumanMessage("hi")] as BaseMessage[], systemMessage: new SystemMessage(""), systemPrompt: "" };
  const definition = (description: string, parameters: unknown) => ({ type: "function", function: { name: "lookup", description, parameters } });
  const request: LcFieldRequest =
    position === "message-name"
      ? { ...base, messages: [new HumanMessage({ content: "hi", name: text })] }
      : position === "tool-description"
        ? { ...base, tools: [definition(text, { type: "object", properties: {} })] }
        : position === "tool-schema"
          ? { ...base, tools: [definition("Looks up a customer", schema)] }
          : position === "response-format"
            ? { ...base, responseFormat: toolStrategy({ title: "Answer", ...schema, description: text } as never) }
            : position === "option-metadata"
              ? { ...base, modelSettings: { metadata: { name: text, id: text }, modelKwargs: { metadata: { name: text } } } }
              : position === "schema-nested"
                ? { ...base, tools: [definition("Looks up a customer", nestedSchema(text))] }
                : { ...base, modelSettings: { user: text } };
  return async () => {
    let got: LcFieldRequest | undefined;
    await hook(request, async (r) => ((got = r), new AIMessage("ok")));
    const format = got!.responseFormat as { tool?: unknown }[] | undefined;
    return { name: got!.messages[0].name, tools: got!.tools, format: format?.map((f) => f.tool), modelSettings: got!.modelSettings };
  };
}

for (const g of GROWTH.filter((x) => FIELD_AXES.has(x.name))) {
  describe.skipIf(process.env.PII_SKIP_TIMING === "1")(`time of the fields outside the messages as the ${g.name} doubles`, () => {
    for (const framework of FRAMEWORKS) {
      for (const position of FIELD_POSITIONS[framework]) {
        it(`${g.name} @${framework}:${position}`, { timeout: 60_000 }, async () => {
          const sizes = [g.n, 2 * g.n, 4 * g.n];
          const best = async (n: number) => {
            let ms = Infinity;
            let sent: unknown;
            for (let i = 0; i < 3; i++) {
              const run = prepareField(framework, position, g.input(n));
              const t0 = performance.now();
              sent = await run();
              ms = Math.min(ms, performance.now() - t0);
            }
            return { ms, sent };
          };
          await best(g.n); // (warm-up)
          const times: number[] = [];
          let last: unknown;
          for (const n of sizes) {
            const { ms, sent } = await best(n);
            times.push(ms);
            last = sent;
          }
          const ratios = [times[1] / times[0], times[2] / times[1]];
          const line = `${g.name} @${framework}:${position}: n ${sizes.join("/")}, ms ${times.map((t) => t.toFixed(1)).join("/")}, ratios ${ratios.map((r) => r.toFixed(2)).join(" ")}`;
          console.log(`growth | ${line}`);
          g.check?.(JSON.stringify(last), sizes[2]);
          for (const r of ratios) expect(r, line).toBeLessThanOrEqual(LIMIT);
        });
      }
    }
  });
}

for (const g of GROWTH) {
  describe.skipIf(process.env.PII_SKIP_TIMING === "1")(`time of the vault middlewares as the ${g.name} doubles`, () => {
    for (const framework of FRAMEWORKS) {
      for (const position of g.positions) {
        it(`${g.name} @${framework}:${position}`, { timeout: 60_000 }, async () => {
          const sizes = [g.n, 2 * g.n, 4 * g.n];
          await fastest(framework, position, g.input(g.n)); // (warm-up)
          const times: number[] = [];
          let last: unknown;
          for (const n of sizes) {
            const { ms, sent } = await fastest(framework, position, g.input(n));
            times.push(ms);
            last = sent;
          }
          const ratios = [times[1] / times[0], times[2] / times[1]];
          const line = `${g.name} @${framework}:${position}: n ${sizes.join("/")}, ms ${times.map((t) => t.toFixed(1)).join("/")}, ratios ${ratios.map((r) => r.toFixed(2)).join(" ")}`;
          console.log(`growth | ${line}`);
          g.check?.(JSON.stringify(last), sizes[2]);
          for (const r of ratios) expect(r, line).toBeLessThanOrEqual(LIMIT);
        });
      }
    }
  });
}
