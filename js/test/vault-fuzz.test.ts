// The fuzz cases of gate 3 of the 1.7.1 release standard (python/tests/vault_fuzz.json, written by
// python/tests/gen_vault_fuzz.py) through the AI SDK middleware (transformParams, prompt
// shapes of the AI SDK 6 specification) and the LangChain.js middleware (wrapModelCall), with a detector that finds
// nothing (the vault alone). The same cases run in python/tests/test_vault_fuzz.py.
//
// Every case runs at every position of the file, with a fresh vault that holds the case's known values, in the shapes
// vault-corpus.test.ts builds. What the provider receives at the position must be the case's "exact" text: each
// value's written span replaced by its placeholder and every neighbor unchanged (a JSON document is compared by what it
// holds: section 5 of the guarantee note lets a changed string be written again as JSON writes it). At a data position
// that is the data with the text replaced by "exact". Besides, no "leak" string stands anywhere in the call, as written
// or JSON-escaped. A problem says which check failed: "leak", "placeholder" (an expected placeholder is missing at the
// position), "exact" (the span was not replaced completely or a neighbor changed) or "error".
//
// Decision D1 of the release standard, reported apart for each framework:
//   - the group "two-layer" (the shape of the gate 2 product: human steps before at most two layers) is
//     release-blocking: zero failures;
//   - the group "beyond" (three and four steps, human steps after a layer) is best effort: every failure must be
//     listed in python/tests/vault_fuzz_open.json ("<case id>@ai-sdk" or "<case id>@langchain-js": disposition), and
//     an entry whose case passes must be taken off the list.
// With VAULT_FUZZ_REPORT=<directory>, every failure is written to <directory>/vault-fuzz-<framework>.json.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { AIMessage, HumanMessage, SystemMessage, ToolMessage, type BaseMessage } from "langchain";
import { piiMiddleware as aiSdkMiddleware } from "../src/ai-sdk";
import { piiMiddleware as langchainMiddleware } from "../src/langchain";
import type { Redactor } from "../src/redactor";
import { createVault, type Vault } from "../src/vault";

type Position = "user" | "system" | "tool-text" | "tool-data" | "block-data" | "assistant-unrecorded" | "tool-input-unrecorded";
type Framework = "ai-sdk" | "langchain-js";
type Group = "two-layer" | "beyond";

interface FuzzCase {
  id: string;
  seed: string;
  group: Group;
  depth: number;
  steps: [string, number | null][];
  known: { label: string; value: string }[];
  text: string;
  exact: string;
  spans: string[];
  leak: string[];
}

const FUZZ = JSON.parse(readFileSync(new URL("../../python/tests/vault_fuzz.json", import.meta.url), "utf8")) as {
  version: number;
  positions: Position[];
  cases: FuzzCase[];
};
const OPEN = JSON.parse(readFileSync(new URL("../../python/tests/vault_fuzz_open.json", import.meta.url), "utf8")) as Record<string, string>;
const FRAMEWORKS: readonly Framework[] = ["ai-sdk", "langchain-js"];

/** A detector that finds nothing: only the vault's own search runs. */
const nothing = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;

const vaultOf = (c: FuzzCase): Vault => {
  const vault = createVault();
  for (const k of c.known) vault.tag(k.label, k.value);
  return vault;
};

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const dataOf = (text: string): Record<string, unknown> => ({ note: text, id: 7, [text]: "key" });

/** v with every string and key equal to `old` replaced by `by`. */
function replacedIn(v: unknown, old: string, by: string): unknown {
  if (typeof v === "string") return v === old ? by : v;
  if (Array.isArray(v)) return v.map((x) => replacedIn(x, old, by));
  if (isObject(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k === old ? by : k, replacedIn(x, old, by)]));
  return v;
}

/** A value for comparing outputs, as text: a JSON document (an object or an array, as text) by what it holds, at every
 *  depth and with its keys sorted; any other string exactly; a number by its value. */
function canonical(v: unknown): string {
  if (typeof v === "string") {
    const t = v.trim();
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        const parsed: unknown = JSON.parse(t);
        if (parsed !== null && typeof parsed === "object") return canonical(parsed);
      } catch {
        // not JSON: compared as written
      }
    }
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v !== null && typeof v === "object") {
    const entries = Object.entries(v).map(([k, x]) => [canonical(k), canonical(x)] as const);
    entries.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    return `{${entries.map(([k, x]) => `${k}:${x}`).join(",")}}`;
  }
  return v === undefined ? "undefined" : JSON.stringify(v);
}

/** Every string of a value: strings, keys, and numbers as they are written. */
function strings(v: unknown, out: string[] = []): string[] {
  if (typeof v === "string") out.push(v);
  else if (typeof v === "number") out.push(String(v));
  else if (Array.isArray(v)) for (const x of v) strings(x, out);
  else if (v !== null && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      out.push(k);
      strings(x, out);
    }
  }
  return out;
}

/** The placeholder of each known value of a fresh vault, in the order they are tagged. */
function placeholderMarks(c: FuzzCase): string[] {
  const count = new Map<string, number>();
  return c.known.map(({ label }) => {
    const family = label.endsWith("_L2") ? label.slice(0, -3) : label;
    count.set(family, (count.get(family) ?? 0) + 1);
    return `[${family}_${count.get(family)}]`;
  });
}

/** What the provider receives in one call: all of it, and the document at the case's position. */
interface Sent {
  all: unknown;
  doc: unknown;
}

// AI SDK ---------------------------------------------------------------------------------------------------------------

const userText = (text: string) => ({ role: "user", content: [{ type: "text", text }] });
const toolCall = (id: string, input: unknown) => ({ role: "assistant", content: [{ type: "tool-call", toolCallId: id, toolName: "lookup", input }] });
const toolResult = (id: string, output: unknown) => ({ role: "tool", content: [{ type: "tool-result", toolCallId: id, toolName: "lookup", output }] });

function aiSdkPrompt(c: FuzzCase, position: Position): { prompt: unknown[]; at: number } {
  const { text } = c;
  const data = dataOf(text);
  switch (position) {
    case "user":
      return { prompt: [userText(text)], at: 0 };
    case "system":
      return { prompt: [{ role: "system", content: text }, userText("hi")], at: 0 };
    case "tool-text":
      return { prompt: [userText("hi"), toolCall("c1", {}), toolResult("c1", { type: "text", value: text })], at: 2 };
    case "tool-data":
      return { prompt: [userText("hi"), toolCall("c1", {}), toolResult("c1", { type: "json", value: data })], at: 2 };
    case "block-data":
      return { prompt: [userText("hi"), toolCall("c1", {}), toolResult("c1", { type: "content", value: [{ type: "json", value: data }] })], at: 2 };
    case "assistant-unrecorded":
      return { prompt: [userText("hi"), { role: "assistant", content: [{ type: "text", text }] }, userText("thanks")], at: 1 };
    case "tool-input-unrecorded":
      return { prompt: [userText("hi"), toolCall("c9", data), toolResult("c9", { type: "text", value: "ok" })], at: 1 };
  }
}

async function viaAiSdk(c: FuzzCase, position: Position): Promise<{ shape: string; sent: Sent }[]> {
  const mw = aiSdkMiddleware({ redactor: nothing, vault: vaultOf(c) });
  const { prompt, at } = aiSdkPrompt(c, position);
  const params = await mw.transformParams({ params: { prompt } } as never);
  const content = ((params as { prompt: unknown[] }).prompt[at] as { content: unknown }).content;
  const part = (Array.isArray(content) ? content[0] : undefined) as { text?: unknown; input?: unknown; output?: { value?: unknown } } | undefined;
  const doc =
    position === "system"
      ? content
      : position === "user" || position === "assistant-unrecorded"
        ? part?.text
        : position === "tool-text" || position === "tool-data"
          ? part?.output?.value
          : position === "block-data"
            ? (part?.output?.value as { value?: unknown }[] | undefined)?.[0]?.value
            : part?.input;
  return [{ shape: position, sent: { all: params, doc } }];
}

// LangChain.js ---------------------------------------------------------------------------------------------------------

interface LcRequest {
  messages: BaseMessage[];
  systemMessage?: SystemMessage;
  systemPrompt?: string;
}
type Hook = (request: LcRequest, handler: (r: LcRequest) => Promise<AIMessage>) => Promise<AIMessage>;

/** The messages the model gets for a request a middleware handed on, as the agent of langchain 1.x builds them (as in
 *  vault-corpus.test.ts). */
function modelInput(given: LcRequest, got: LcRequest): BaseMessage[] {
  let system = given.systemMessage ?? new SystemMessage({ content: [{ type: "text", text: given.systemPrompt ?? "" }] });
  const promptChanged = got.systemPrompt !== system.text;
  const messageChanged = got.systemMessage !== given.systemMessage;
  if (promptChanged && messageChanged) throw new Error("the middleware changed both systemPrompt and systemMessage");
  if (promptChanged) system = new SystemMessage({ content: [{ type: "text", text: got.systemPrompt ?? "" }] });
  if (messageChanged) system = new SystemMessage({ ...(got.systemMessage as unknown as ConstructorParameters<typeof SystemMessage>[0] & object) });
  return [...(system.text === "" ? [] : [system]), ...got.messages];
}

const sentFields = (m: BaseMessage) => ({
  type: m.type,
  content: m.content,
  name: m.name,
  tool_calls: AIMessage.isInstance(m) ? m.tool_calls : undefined,
  invalid_tool_calls: AIMessage.isInstance(m) ? m.invalid_tool_calls : undefined,
  tool_call_id: ToolMessage.isInstance(m) ? m.tool_call_id : undefined,
  additional_kwargs: m.additional_kwargs,
});

/** [shape, messages, where the position is] for one case at one position. */
function langchainShapes(c: FuzzCase, position: Position): { shape: string; request: LcRequest; at: (input: BaseMessage[], got: LcRequest) => unknown }[] {
  const { text } = c;
  const data = dataOf(text);
  const empty = { systemMessage: new SystemMessage(""), systemPrompt: "" };
  const call = (id: string, args: Record<string, unknown>) => new AIMessage({ content: "", tool_calls: [{ id, name: "lookup", args }] });
  const result = (content: unknown) => new ToolMessage({ content: content as ToolMessage["content"], tool_call_id: "c1" });
  const hi = () => new HumanMessage("hi");
  const content = (i: number) => (_input: BaseMessage[], got: LcRequest) => got.messages[i].content as unknown;
  switch (position) {
    case "user":
      return [{ shape: "user", request: { messages: [new HumanMessage(text)], ...empty }, at: content(0) }];
    case "system":
      // the agent's own system prompt: a message of one text block, and the same text as systemPrompt
      return [
        {
          shape: "system",
          request: { messages: [hi()], systemMessage: new SystemMessage({ content: [{ type: "text", text }] }), systemPrompt: text },
          at: (input) => {
            if (!SystemMessage.isInstance(input[0])) throw new Error("no system message reaches the model");
            return input[0].text;
          },
        },
      ];
    case "tool-text":
      return [{ shape: "tool-text", request: { messages: [hi(), call("c1", {}), result(text)], ...empty }, at: content(2) }];
    case "tool-data":
      return [
        { shape: "rows", request: { messages: [hi(), call("c1", {}), result([data])], ...empty }, at: (_i, got) => (got.messages[2].content as unknown[])[0] },
        { shape: "json-text", request: { messages: [hi(), call("c1", {}), result(JSON.stringify(data))], ...empty }, at: content(2) },
      ];
    case "block-data":
      return [
        {
          shape: "block",
          request: { messages: [hi(), call("c1", {}), result([{ type: "json", json: data }])], ...empty },
          at: (_i, got) => ((got.messages[2].content as unknown[])[0] as { json?: unknown }).json,
        },
      ];
    case "assistant-unrecorded":
      return [{ shape: "assistant", request: { messages: [hi(), new AIMessage(text), new HumanMessage("thanks")], ...empty }, at: content(1) }];
    case "tool-input-unrecorded":
      return [
        {
          shape: "args",
          request: { messages: [hi(), call("c9", data), new ToolMessage({ content: "ok", tool_call_id: "c9" })], ...empty },
          at: (_i, got) => (got.messages[1] as AIMessage).tool_calls?.[0]?.args,
        },
      ];
  }
}

async function viaLangChain(c: FuzzCase, position: Position): Promise<{ shape: string; sent: Sent }[]> {
  const out: { shape: string; sent: Sent }[] = [];
  for (const { shape, request, at } of langchainShapes(c, position)) {
    const mw = langchainMiddleware({ redactor: nothing, vault: vaultOf(c) });
    let got: LcRequest | undefined;
    await (mw.wrapModelCall as unknown as Hook)(request, async (r) => ((got = r), new AIMessage("ok")));
    const input = modelInput(request, got!);
    out.push({ shape, sent: { all: input.map(sentFields), doc: at(input, got!) } });
  }
  return out;
}

// Checking a case ------------------------------------------------------------------------------------------------------

const clip = (v: unknown, n = 300) => {
  const s = typeof v === "string" ? v : (JSON.stringify(v) ?? String(v));
  return s.length > n ? `${s.slice(0, n)}...` : s;
};

/** What is wrong with what the provider received at the position; empty when nothing. */
function problems(c: FuzzCase, position: Position, sent: Sent): string[] {
  const out: string[] = [];
  const all = JSON.stringify(sent.all) ?? "";
  const allStrings = strings(sent.all);
  for (const s of c.leak) {
    const escaped = JSON.stringify(s).slice(1, -1);
    if (allStrings.some((x) => x.includes(s)) || all.includes(s) || all.includes(escaped)) out.push(`leak: ${clip(s, 80)}`);
  }
  const shown = typeof sent.doc === "string" ? sent.doc : (JSON.stringify(sent.doc) ?? "");
  for (const mark of placeholderMarks(c)) if (!shown.includes(mark)) out.push(`placeholder: ${mark} missing`);
  const isText = position === "user" || position === "system" || position === "tool-text" || position === "assistant-unrecorded";
  const want = isText ? c.exact : replacedIn(dataOf(c.text), c.text, c.exact);
  if (canonical(sent.doc) !== canonical(want)) out.push(`exact: received ${clip(sent.doc)}; expected ${clip(want)}`);
  return out;
}

type Failed = Record<string, string[]>;
const RESULTS = new Map<Framework, Promise<Map<string, Failed>>>();

/** The failures of every case through one framework: {case id: {"<position>/<shape>": problems}} (run once). */
function resultsOf(framework: Framework): Promise<Map<string, Failed>> {
  let r = RESULTS.get(framework);
  if (!r) {
    r = (async () => {
      const out = new Map<string, Failed>();
      for (const c of FUZZ.cases) {
        const failed: Failed = {};
        for (const position of FUZZ.positions) {
          let runs: { shape: string; sent: Sent }[];
          try {
            runs = framework === "ai-sdk" ? await viaAiSdk(c, position) : await viaLangChain(c, position);
          } catch (e) {
            failed[position] = [`error: ${String(e).slice(0, 200)}`];
            continue;
          }
          for (const { shape, sent } of runs) {
            const p = problems(c, position, sent);
            if (p.length > 0) failed[`${position}/${shape}`] = p;
          }
        }
        out.set(c.id, failed);
      }
      const report = process.env.VAULT_FUZZ_REPORT;
      if (report) {
        mkdirSync(report, { recursive: true });
        const rows = Object.fromEntries(
          FUZZ.cases
            .filter((c) => Object.keys(out.get(c.id)!).length > 0)
            .map((c) => [c.id, { group: c.group, depth: c.depth, steps: c.steps, known: c.known, text: c.text, exact: c.exact, failed: out.get(c.id) }]),
        );
        writeFileSync(`${report}/vault-fuzz-${framework}.json`, JSON.stringify(rows, null, 1));
      }
      return out;
    })();
    RESULTS.set(framework, r);
  }
  return r;
}

const fails = (results: Map<string, Failed>, c: FuzzCase) => Object.keys(results.get(c.id) ?? {}).length > 0;

function summary(results: Map<string, Failed>, cases: FuzzCase[]): string[] {
  return cases.map((c) => {
    const failed = results.get(c.id)!;
    const [where, ps] = Object.entries(failed)[0];
    const kinds = [...new Set(Object.values(failed).flatMap((x) => x.map((p) => p.split(":")[0])))].sort();
    const chain = c.steps.map(([n, i]) => (i === null ? n : `${n}@${i}`)).join(" > ");
    return `${c.id} (depth ${c.depth}, ${chain}; ${kinds.join(", ")} at ${Object.keys(failed).length} shapes): ${where}: ${ps[0].slice(0, 300)}`;
  });
}

// Tests ----------------------------------------------------------------------------------------------------------------

describe("vault fuzz: the file", () => {
  it("has at least 2,000 distinct cases, each in one group of D1", () => {
    expect(FUZZ.version).toBe(1);
    expect(FUZZ.cases.length).toBeGreaterThanOrEqual(2000);
    expect(new Set(FUZZ.cases.map((c) => c.id)).size).toBe(FUZZ.cases.length);
    expect(new Set(FUZZ.cases.map((c) => JSON.stringify([c.known, c.text]))).size).toBe(FUZZ.cases.length);
    for (const c of FUZZ.cases) {
      let layers = 0;
      let after = false;
      const humans = new Map<number, number>();
      for (const [, i] of c.steps) {
        if (i === null) layers++;
        else if (layers > 0) after = true;
        else humans.set(i, (humans.get(i) ?? 0) + 1);
      }
      const group: Group = !after && layers <= 2 && [...humans.values()].every((n) => n === 1) ? "two-layer" : "beyond";
      expect(c.group, c.id).toBe(group);
      expect(c.steps.length, c.id).toBe(c.depth);
    }
    const ids = new Set(FUZZ.cases.map((c) => c.id));
    expect(Object.keys(OPEN).filter((k) => !ids.has(k.split("@")[0]))).toEqual([]);
  });
});

for (const framework of FRAMEWORKS) {
  describe(`vault fuzz through the ${framework} middleware`, () => {
    it("two-layer product (release-blocking under D1): zero failures", { timeout: 600_000 }, async () => {
      const results = await resultsOf(framework);
      const group = FUZZ.cases.filter((c) => c.group === "two-layer");
      const failing = group.filter((c) => fails(results, c));
      expect(failing.length === 0 ? [] : [`${failing.length} of ${group.length} two-layer cases fail`, ...summary(results, failing)]).toEqual([]);
    });

    it("beyond the product (best effort under D1): every failure listed with its disposition", { timeout: 600_000 }, async () => {
      const results = await resultsOf(framework);
      const group = FUZZ.cases.filter((c) => c.group === "beyond");
      const failing = group.filter((c) => fails(results, c));
      const undispositioned = failing.filter((c) => !(`${c.id}@${framework}` in OPEN));
      const stale = Object.keys(OPEN)
        .filter((k) => k.endsWith(`@${framework}`))
        .filter((k) => !failing.some((c) => `${c.id}@${framework}` === k));
      const byDepth: Record<number, number> = {};
      for (const c of failing) byDepth[c.depth] = (byDepth[c.depth] ?? 0) + 1;
      const head = `${failing.length} of ${group.length} cases beyond the product fail (by depth ${JSON.stringify(byDepth)}); ${undispositioned.length} not in vault_fuzz_open.json`;
      expect(
        undispositioned.length === 0 && stale.length === 0
          ? []
          : [head, ...summary(results, undispositioned), ...(stale.length > 0 ? [`passing now, take off vault_fuzz_open.json: ${stale.join(", ")}`] : [])],
      ).toEqual([]);
    });
  });
}
