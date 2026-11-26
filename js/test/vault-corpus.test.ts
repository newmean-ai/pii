// The shared leak corpus of the vault middlewares (python/tests/vault_corpus.json, written by
// python/tests/gen_vault_corpus.py), run through the AI SDK middleware (transformParams, prompt shapes of the AI SDK 6
// specification) and the LangChain.js middleware (wrapModelCall). Every case and every position it names is one test
// per framework, with a fresh vault that holds the case's known values and a stand-in detector that reports every
// occurrence of the case's values in the text it is given. A test checks what the provider receives:
//   - no "leak" string, nor its JSON-escaped form, in any string or key of the call or in its JSON;
//   - every "keep" string in the message at the case's position;
//   - "fields": the number of keys the top-level object at that position keeps (in JSON text, keys are counted as
//     written, so two that are the same count twice);
//   - "exact" (the finite product of gate 2, python/tests/corpus_product.py): the document at the position is the text
//     with each written span replaced by its placeholder and every neighbor unchanged (at a data position the data with
//     the text replaced by it; at message-name the placeholder without brackets). A JSON document is compared by what
//     it holds, since section 5 of the guarantee note lets a changed string be written again as JSON writes it.
//
// The positions outside the messages and the system prompt (python/tests/corpus_fields.py, since 1.7.2) run where the
// framework has them: the AI SDK has no message names, LangChain no provider options of a message. The response format
// runs through a real createAgent, whose model records what it is bound with. The positions of the 23rd outside review
// (python/tests/corpus_23rd_outside_review_fields.py) put the text into the application's metadata of provider options
// and model settings, into the identifiers providers define there, into literal constraints of a schema, and into the
// descriptions under every keyword that holds schemas; each runs in several shapes, in both frameworks. The positions of
// the review group "options" (python/tests/corpus_review_options.py) put the text under option and setting names that
// are a provider's only at another path (a stored prompt's variables, an unknown provider's `order` or `model`), and,
// as a JSON number, into the application's data (masked when known) and under the numeric settings providers define
// (kept). The provider options of a message also carry an OpenAI-compatible `name`, and the response format of a real
// createAgent also runs a schema with typed fields (a date and an e-mail address). The positions of model settings
// (call-options and option-*) also run in the model's own fields (since 1.7.2, the gate run of 1.7.1): the model's
// `user` and `modelKwargs` (FieldModel), read where the model reports its request parameters (`invocationParams`).
//
// Cases that fail at this head are listed in vault-corpus-open.json as "<id>@<framework>:<position>" and are expected to
// fail (it.fails): the suite stays green and the list shows what is open. A listed case that starts to pass fails until
// it is taken off the list.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { AIMessage, createAgent, HumanMessage, providerStrategy, SystemMessage, tool, ToolMessage, toolStrategy, type BaseMessage } from "langchain";
import { ChatMessage } from "@langchain/core/messages";
import { convertToOpenAITool } from "@langchain/core/utils/function_calling";
import { FakeListChatModel } from "@langchain/core/utils/testing";
import { z } from "zod";
import { piiMiddleware as aiSdkMiddleware } from "../src/ai-sdk";
import { piiMiddleware as langchainMiddleware } from "../src/langchain";
import type { Redactor } from "../src/redactor";
import type { Span } from "../src/types";
import { createVault, type Vault } from "../src/vault";

type Position =
  | "user"
  | "system"
  | "tool-text"
  | "tool-data"
  | "block-data"
  | "assistant-unrecorded"
  | "tool-input-unrecorded"
  // outside the messages and the system prompt (since 1.7.2)
  | "message-name"
  | "tool-description"
  | "tool-schema"
  | "response-format"
  | "call-options"
  | "message-options"
  // the 23rd outside review (since 1.7.2)
  | "option-metadata"
  | "option-ids"
  | "schema-literal"
  | "schema-nested"
  // the review group "options" (since 1.7.2)
  | "option-variables"
  | "option-numbers"
  | "option-settings";
type Framework = "ai-sdk" | "langchain";
type FieldPosition = Exclude<Position, "user" | "system" | "tool-text" | "tool-data" | "block-data" | "assistant-unrecorded" | "tool-input-unrecorded">;
const FIELD_POSITIONS: readonly Position[] = [
  "message-name", "tool-description", "tool-schema", "response-format", "call-options", "message-options",
  "option-metadata", "option-ids", "schema-literal", "schema-nested", "option-variables", "option-numbers", "option-settings",
];
const isField = (p: Position): p is FieldPosition => FIELD_POSITIONS.includes(p);
/** Whether the framework has the position: the AI SDK has no message names, LangChain no provider options of a message. */
const applies = (framework: Framework, position: Position) =>
  framework === "ai-sdk" ? position !== "message-name" : position !== "message-options";

interface Case {
  id: string;
  from: string;
  known: { label: string; value: string }[];
  detect: [string, string][];
  where: Position[];
  text: string;
  data?: unknown;
  int_keys: boolean; // (Python runners only: JSON keys are always strings in JavaScript)
  leak: string[];
  keep: string[];
  fields?: number;
  // what must stand at the position (the gate 2 product, python/tests/corpus_product.py): the text with each written span
  // replaced by its placeholder; at a data position the data with the text replaced by it (or exact_data)
  exact?: string;
  exact_data?: unknown;
}

const CORPUS = JSON.parse(readFileSync(new URL("../../python/tests/vault_corpus.json", import.meta.url), "utf8")) as {
  version: number;
  positions: Position[];
  cases: Case[];
};
const OPEN_LIST = JSON.parse(readFileSync(new URL("./vault-corpus-open.json", import.meta.url), "utf8")) as string[];
const OPEN = new Set(OPEN_LIST);
const FRAMEWORKS: readonly Framework[] = ["ai-sdk", "langchain"];
const testId = (c: Case, framework: Framework, position: Position) => `${c.id}@${framework}:${position}`;

/** A detector that reports every occurrence of `values` in the text it is given; of two that overlap, the one that starts
 *  first (the longer of two that start together). The vault handling is the middleware's own. */
const finder = (values: readonly (readonly [string, string])[]): Redactor => {
  const detect = async (text: string): Promise<Span[]> => {
    const all: Span[] = [];
    for (const [value, label] of values) {
      if (value === "") continue;
      for (let i = text.indexOf(value); i >= 0; i = text.indexOf(value, i + 1)) all.push({ start: i, end: i + value.length, label, source: "model" });
    }
    all.sort((a, b) => a.start - b.start || b.end - a.end);
    const out: Span[] = [];
    for (const s of all) if (out.length === 0 || s.start >= out[out.length - 1].end) out.push(s);
    return out;
  };
  return { sensitivity: 0, detect } as unknown as Redactor;
};

const vaultOf = (c: Case): Vault => {
  const vault = createVault();
  for (const k of c.known) vault.tag(k.label, k.value);
  return vault;
};

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);

/** Every string of a value: strings, keys, and numbers as they are written. */
function strings(v: unknown, out: string[] = []): string[] {
  if (typeof v === "string") out.push(v);
  else if (typeof v === "number" || typeof v === "bigint") out.push(String(v));
  else if (Array.isArray(v)) for (const x of v) strings(x, out);
  else if (v !== null && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      out.push(k);
      strings(x, out);
    }
  }
  return out;
}

const QUOTE = 34;
const BACKSLASH = 92;

/** The number of keys of the top-level object of JSON text, counted as they are written (two that are the same count
 *  twice); undefined when the text is not a JSON object. */
function topLevelKeys(text: string): number | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (!isObject(parsed)) return undefined;
  let depth = 0;
  let keys = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    if (ch === QUOTE) {
      let j = i + 1;
      while (text.charCodeAt(j) !== QUOTE) j += text.charCodeAt(j) === BACKSLASH ? 2 : 1;
      if (depth === 1) {
        let k = j + 1;
        while (/\s/.test(text[k])) k++;
        if (text[k] === ":") keys++;
      }
      i = j;
    } else if (text[i] === "{" || text[i] === "[") depth++;
    else if (text[i] === "}" || text[i] === "]") depth--;
  }
  return keys;
}

/** The number of keys of the top-level object at a position: of the object itself, or of the JSON text there. */
const fieldsOf = (doc: unknown): number | undefined =>
  typeof doc === "string" ? topLevelKeys(doc) : isObject(doc) ? Object.keys(doc).length : undefined;

/** A value for comparing outputs, as text: a JSON document (an object or an array, as text) by what it holds, at every
 *  depth and with its keys sorted, so that the rewrites the guarantee note decides (section 5: a changed string written
 *  again as JSON writes it) count as the same; any other string exactly; a number by its value. (As canon in
 *  python/tests/test_vault_corpus.py.) */
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

/** The placeholder of each known value of a fresh vault, in the order they are tagged. */
function placeholderMarks(c: Case): string[] {
  const count = new Map<string, number>();
  return c.known.map(({ label }) => {
    const family = label.endsWith("_L2") ? label.slice(0, -3) : label;
    count.set(family, (count.get(family) ?? 0) + 1);
    return `[${family}_${count.get(family)}]`;
  });
}

/** v with every string and key equal to `old` replaced by `by`. */
function replacedIn(v: unknown, old: string, by: string): unknown {
  if (typeof v === "string") return v === old ? by : v;
  if (Array.isArray(v)) return v.map((x) => replacedIn(x, old, by));
  if (isObject(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k === old ? by : k, replacedIn(x, old, by)]));
  return v;
}

const EXACT_TEXT_AT: readonly Position[] = ["user", "system", "tool-text", "assistant-unrecorded", "tool-description"];

/** What must stand at the position (the document the runner reads there) when the case has an exact output ("exact":
 *  the text; at a data position the case's data with the text replaced by it, or "exact_data"; at message-name the
 *  placeholders written without brackets), or undefined. (As expected_at in python/tests/test_vault_corpus.py.) */
function expectedAt(c: Case, position: Position): { value: unknown } | undefined {
  if (c.exact === undefined && c.exact_data === undefined) return undefined;
  if (EXACT_TEXT_AT.includes(position) || (position === "tool-input-unrecorded" && c.data === undefined)) {
    return c.exact === undefined ? undefined : { value: c.exact };
  }
  if (position === "message-name" && c.exact !== undefined) {
    let out = c.exact;
    for (const mark of placeholderMarks(c)) out = out.split(mark).join(mark.slice(1, -1));
    return { value: out };
  }
  if ((position === "tool-data" || position === "block-data" || position === "tool-input-unrecorded") && c.data !== undefined) {
    if (c.exact_data !== undefined) return { value: c.exact_data };
    return c.exact === undefined ? undefined : { value: replacedIn(c.data, c.text, c.exact) };
  }
  return undefined;
}

/** What the provider receives in one call: all of it, the message at the case's position, and the document there (an
 *  object, or the text that `fields` counts in). */
interface Sent {
  all: unknown;
  target: unknown;
  doc: unknown;
}

// AI SDK ---------------------------------------------------------------------------------------------------------------

const userText = (text: string) => ({ role: "user", content: [{ type: "text", text }] });
const toolCall = (id: string, input: unknown) => ({ role: "assistant", content: [{ type: "tool-call", toolCallId: id, toolName: "lookup", input }] });
const toolResult = (id: string, output: unknown) => ({ role: "tool", content: [{ type: "tool-result", toolCallId: id, toolName: "lookup", output }] });

/** The prompt (AI SDK 6, provider specification v3) with the case at `position`, and the index of that message. */
function aiSdkPrompt(c: Case, position: Position): { prompt: unknown[]; at: number } {
  const { text, data } = c;
  switch (position as Exclude<Position, FieldPosition>) {
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
      return { prompt: [userText("hi"), toolCall("c9", data !== undefined ? data : text), toolResult("c9", { type: "text", value: "ok" })], at: 1 };
  }
}

/** One shape of a position outside the messages: the request, and where the position is in what was handed on. */
type Shape<R> = { shape: string; request: R; at: (r: any) => { target: unknown; doc: unknown } };

/** Schema properties with the text in a literal constraint (`const`, `enum`, `default`, `pattern`, also as an object
 *  with keys a schema has), for "schema-literal": [shape, property]. The literal must stay as written. */
const literalProperties = (text: string): [string, Record<string, unknown>][] => [
  ["const", { type: "string", const: text }],
  ["const-object", { type: "object", const: { description: text, title: text } }],
  ["enum", { type: "string", enum: [text, "other"] }],
  ["enum-object", { type: "object", enum: [{ description: text }] }],
  ["default", { type: "string", default: text }],
  ["default-object", { type: "object", default: { description: text, examples: [text] } }],
  ["pattern", { type: "string", pattern: text }],
];
/** A schema with the text in a description under every keyword that holds schemas, for "schema-nested". */
const nestedSchema = (text: string): Record<string, unknown> => {
  const s = () => ({ type: "string", description: text });
  return {
    type: "object",
    properties: {
      list: { type: "array", items: s(), prefixItems: [s()], contains: s(), unevaluatedItems: s(), additionalItems: s() },
      tuple: { type: "array", items: [s()] },
      any: { anyOf: [s()], oneOf: [s()], allOf: [s()], not: s() },
      cond: { if: s(), then: s(), else: s() },
      map: { type: "object", additionalProperties: s(), patternProperties: { "^x": s() }, propertyNames: s(), unevaluatedProperties: s() },
      deps: { type: "object", dependentSchemas: { a: s() }, dependencies: { b: s(), c: ["a"] } },
      blob: { type: "string", contentSchema: s() },
    },
    $defs: { d: s() },
    definitions: { e: s() },
  };
};

// Schemas made from classes and a list read through a getter (release gate 8, items 3 and 4): the property "q" holds
// the case's text in its description and examples, as the plain shapes do.
class CorpusField {
  type = "string";
  examples: string[];
  constructor(public description: string) {
    this.examples = [description];
  }
}
class CorpusSchema {
  type = "object";
  properties: Record<string, unknown>;
  required = ["q"];
  constructor(text: string) {
    this.properties = { q: new CorpusField(text) };
  }
}
class CorpusDefinition {
  type = "function";
  function: Record<string, unknown>;
  constructor(text: string) {
    this.function = { name: "lookup", description: "Looks up a customer", parameters: new CorpusSchema(text) };
  }
}
/** A list read through a getter: "control" the first three reads, `text` from the fourth on (the reviewer's input). */
const laterList = (text: string): unknown[] => {
  let reads = 0;
  const list: unknown[] = [];
  Object.defineProperty(list, "0", { enumerable: true, get: () => (++reads >= 4 ? text : "control") });
  return list;
};

/** The calls (AI SDK 6) with the case's text in a field outside the prompt, each with where that field is in the masked
 *  call. */
function aiSdkFields(c: Case, position: FieldPosition): Shape<Record<string, unknown>>[] {
  const text = c.text;
  const prompt = [userText("hi")];
  const one = (request: Record<string, unknown>, at: Shape<unknown>["at"]): Shape<Record<string, unknown>>[] => [{ shape: position, request, at }];
  const tool = (inputSchema: unknown) => ({ type: "function", name: "lookup", description: "Looks up a customer", inputSchema });
  const only = (target: (o: any) => unknown) => (o: any) => ({ target: target(o), doc: undefined });
  // the object at the position, and its field "name" (which holds the text: JSON text keeps its number of keys)
  const named = (target: (o: any) => any) => (o: any) => ({ target: target(o), doc: target(o).name });
  switch (position) {
    case "tool-description":
      return one(
        { prompt, tools: [{ type: "function", name: "lookup", description: text, inputSchema: { type: "object", properties: {} } }] },
        (o) => ({ target: o.tools[0].description, doc: o.tools[0].description }),
      );
    case "tool-schema": {
      const inputSchema = { type: "object", properties: { q: { type: "string", description: text, examples: [text] } } };
      const at = (o: any) => ({ target: o.tools[0].inputSchema.properties.q, doc: o.tools[0].inputSchema.properties.q.description });
      return [
        ...one({ prompt, tools: [tool(inputSchema)] }, at),
        // (release gate 8, items 3 and 4: a schema made from classes, at the top and inside, and examples read
        // through a getter, the reviewer's: the text from the fourth read on)
        { shape: "class-property", request: { prompt, tools: [tool({ type: "object", properties: { q: new CorpusField(text) } })] }, at },
        { shape: "class-root", request: { prompt, tools: [tool(new CorpusSchema(text))] }, at },
        { shape: "getter-examples", request: { prompt, tools: [tool({ type: "object", properties: { q: { type: "string", description: text, examples: laterList(text) } } })] }, at },
      ];
    }
    case "response-format": {
      const schema = { type: "object", properties: { a: { type: "string", description: text } } };
      return [
        ...one({ prompt, responseFormat: { type: "json", name: "answer", description: text, schema } }, (o) => ({ target: o.responseFormat, doc: o.responseFormat.description })),
        // (release gate 8, item 3: a response format's schema made from a class)
        { shape: "class-root", request: { prompt, responseFormat: { type: "json", name: "answer", schema: new CorpusSchema(text) } }, at: (o: any) => ({ target: o.responseFormat, doc: o.responseFormat.schema.properties.q.description }) },
      ];
    }
    case "call-options":
      return one({ prompt, providerOptions: { openai: { user: text } } }, (o) => ({ target: o.providerOptions, doc: o.providerOptions.openai.user }));
    case "message-options":
      return [
        ...one(
          { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { custom: { note: text } } }], providerOptions: { openai: { user: text } } }] },
          (o) => ({ target: o.prompt[0].providerOptions, doc: o.prompt[0].providerOptions.openai.user }),
        ),
        // the name an OpenAI-compatible provider sends with the message (written as a message name is, without brackets)
        {
          shape: "openai-compatible-name",
          request: { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { openaiCompatible: { name: text } } }], providerOptions: { openaiCompatible: { name: text } } }] },
          at: (o: any) => ({ target: o.prompt[0].providerOptions.openaiCompatible.name, doc: o.prompt[0].providerOptions.openaiCompatible.name }),
        },
      ];
    case "option-variables": {
      // names a provider defines only at another path (`gateway.order`): a stored prompt's variables, an unknown
      // provider's fields, the options of a message
      const variables = { id: "pmpt_1", version: "2", variables: { order: text, model: text, customer: text } };
      const order = (target: (o: any) => any) => (o: any) => ({ target: target(o).order, doc: target(o).order });
      return [
        { shape: "prompt-variables", request: { prompt, providerOptions: { openai: { prompt: variables } } }, at: order((o) => o.providerOptions.openai.prompt.variables) },
        { shape: "unknown-provider", request: { prompt, providerOptions: { acme: { order: text, only: text, model: text, conversation: text, container: text } } }, at: order((o) => o.providerOptions.acme) },
        { shape: "message-options", request: { prompt: [{ ...userText("hi"), providerOptions: { acme: { order: text } } }] }, at: order((o) => o.prompt[0].providerOptions.acme) },
        // (release gate 8, item 5, the reviewer's paths: provider options named prompt, mcp_servers and extra_body, and
        // extra_body under a known provider, are no provider's identifiers)
        {
          shape: "misplaced-ids",
          request: { prompt, providerOptions: { prompt: { id: text }, mcp_servers: { primary: { name: text } }, extra_body: { models: text }, openai: { extra_body: { previous_response_id: text } } } },
          at: (o: any) => ({ target: o.providerOptions, doc: o.providerOptions.prompt.id }),
        },
      ];
    }
    case "option-numbers": {
      const n = Number(text);
      return [
        { shape: "call-metadata", request: { prompt, providerOptions: { openai: { metadata: { customer_number: n } } } }, at: only((o) => o.providerOptions.openai.metadata) },
        { shape: "unknown-provider", request: { prompt, providerOptions: { acme: { customerNumber: n, nested: [{ phone: n }] } } }, at: only((o) => o.providerOptions.acme) },
        { shape: "provider-tool-args", request: { prompt, tools: [{ type: "provider", id: "acme.lookup", name: "lookup", args: { account: n } }] }, at: only((o) => o.tools[0].args) },
        {
          shape: "part-options",
          request: { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { acme: { account: n } } }] }] },
          at: only((o) => o.prompt[0].content[0].providerOptions.acme),
        },
      ];
    }
    case "option-settings": {
      const n = Number(text);
      const providerOptions = { openai: { maxCompletionTokens: n, seed: n }, anthropic: { thinking: { type: "enabled", budgetTokens: n } }, google: { thinkingConfig: { thinkingBudget: n } } };
      return one({ prompt, providerOptions }, only((o) => o.providerOptions));
    }
    case "option-metadata": {
      // the application's metadata at the call, a message and a part, and an unknown provider's options
      const options = { openai: { metadata: { name: text } } };
      return [
        { shape: "call-metadata", request: { prompt, providerOptions: { openai: { metadata: { name: text, id: text, customerId: text } } } }, at: named((o) => o.providerOptions.openai.metadata) },
        { shape: "call-labels", request: { prompt, providerOptions: { google: { labels: { name: text } } } }, at: named((o) => o.providerOptions.google.labels) },
        { shape: "message-metadata", request: { prompt: [{ ...userText("hi"), providerOptions: options }] }, at: named((o) => o.prompt[0].providerOptions.openai.metadata) },
        {
          shape: "part-metadata",
          request: { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: options }] }] },
          at: named((o) => o.prompt[0].content[0].providerOptions.openai.metadata),
        },
        { shape: "unknown-provider", request: { prompt, providerOptions: { acme: { name: text, customerId: text, user_key: text } } }, at: named((o) => o.providerOptions.acme) },
      ];
    }
    case "option-ids": {
      // identifiers, signatures and encrypted data that providers define, at the call, a message and a part
      const reasoning = (options: unknown) => [userText("hi"), { role: "assistant", content: [{ type: "reasoning", text: "Thinking.", providerOptions: options }, { type: "text", text: "ok" }] }, userText("thanks")];
      const part = (o: any) => o.prompt[1].content[0].providerOptions;
      return [
        { shape: "previous-response-id", request: { prompt, providerOptions: { openai: { previousResponseId: text } } }, at: only((o) => o.providerOptions.openai.previousResponseId) },
        { shape: "item-id", request: { prompt: [{ ...userText("hi"), providerOptions: { openai: { itemId: text } } }] }, at: only((o) => o.prompt[0].providerOptions.openai.itemId) },
        { shape: "signature", request: { prompt: reasoning({ anthropic: { signature: text } }) }, at: only((o) => part(o).anthropic.signature) },
        { shape: "thought-signature", request: { prompt: reasoning({ google: { thoughtSignature: text } }) }, at: only((o) => part(o).google.thoughtSignature) },
        { shape: "encrypted", request: { prompt: reasoning({ openai: { reasoningEncryptedContent: text } }) }, at: only((o) => part(o).openai.reasoningEncryptedContent) },
        { shape: "container", request: { prompt, providerOptions: { anthropic: { container: { id: text } } } }, at: only((o) => o.providerOptions.anthropic.container.id) },
        { shape: "cached-content", request: { prompt, providerOptions: { google: { cachedContent: text } } }, at: only((o) => o.providerOptions.google.cachedContent) },
      ];
    }
    case "schema-literal":
      return [
        ...literalProperties(text).map(([shape, q]) => ({
          shape: `tool-${shape}`,
          request: { prompt, tools: [tool({ type: "object", properties: { q } })] },
          at: only((o) => o.tools[0].inputSchema.properties.q),
        })),
        {
          shape: "response-const-object",
          request: { prompt, responseFormat: { type: "json", name: "answer", schema: { type: "object", properties: { a: { const: { description: text } } } } } },
          at: only((o) => o.responseFormat.schema.properties.a.const),
        },
      ];
    case "schema-nested":
      return [
        { shape: "tool", request: { prompt, tools: [tool(nestedSchema(text))] }, at: only((o) => o.tools[0].inputSchema) },
        { shape: "response-format", request: { prompt, responseFormat: { type: "json", name: "answer", schema: nestedSchema(text) } }, at: only((o) => o.responseFormat.schema) },
      ];
    case "message-name":
      throw new Error("the AI SDK has no message names");
  }
}

// The confirmation of release gate 8, items 3 and 4: the cases of the class "fields" (those at tool-schema) run every
// shape of a field outside the messages a second time with each plain object and list of the request behind a Proxy
// whose stored strings are another text (STORED) while property access gives the case's. What is sent must be what is
// sent for the plain request, and no stored string (the walker reads values as JSON.stringify reads them).
const STORED = "draft-stored";
const throughProxies = (c: Case) => c.where.includes("tool-schema");
/** `x` with every plain object and list (all of whose fields are values) behind such a Proxy, at every depth. */
function proxify(x: unknown): unknown {
  if (x === null || typeof x !== "object") return x;
  const proto = Object.getPrototypeOf(x);
  const list = Array.isArray(x) && proto === Array.prototype;
  if (!list && proto !== Object.prototype && proto !== null) return x; // (a framework's object, a class instance)
  const own = Object.getOwnPropertyDescriptors(x);
  if (Object.values(own).some((d) => !("value" in d))) return x; // (getters are read as they are: their reads count)
  const given = x as Record<string, unknown>;
  const target: Record<string, unknown> = list ? [] : Object.create(proto);
  for (const k of Reflect.ownKeys(own) as string[]) {
    if (list && k === "length") continue;
    const v = own[k].value;
    Object.defineProperty(target, k, { ...own[k], value: typeof v === "string" ? STORED : proxify(v) });
  }
  return new Proxy(target, {
    get: (o, k, r) => (typeof k === "string" && Object.prototype.hasOwnProperty.call(given, k) && typeof given[k] === "string" ? given[k] : Reflect.get(o, k, r)),
  });
}
/** What is wrong with a request sent through Proxies, against the same request given plainly. */
function proxyProblems(sent: Sent, plain: Sent | undefined): string[] {
  if (!plain) return [];
  const out: string[] = [];
  const all = JSON.stringify(sent.all);
  if (all.includes(STORED)) out.push("sent a value the Proxy stored, not the one property access gives");
  if (all !== JSON.stringify(plain.all)) out.push(`through Proxies another request than the plain one: ${clip(sent.all)}`);
  return out;
}
type FieldRun = { shape: string; sent: Sent; plain?: Sent };

async function viaAiSdkFields(c: Case, position: FieldPosition): Promise<FieldRun[]> {
  const out: FieldRun[] = [];
  const send = async (request: Record<string, unknown>, at: Shape<unknown>["at"]): Promise<Sent> => {
    const mw = aiSdkMiddleware({ redactor: finder(c.detect), vault: vaultOf(c) });
    const params = await mw.transformParams({ params: request } as never);
    return { all: params, ...at(params) };
  };
  const shapes = aiSdkFields(c, position);
  // (each run gets its own request: a list read through a getter counts its reads)
  const again = throughProxies(c) ? aiSdkFields(c, position) : [];
  for (const [i, { shape, request, at }] of shapes.entries()) {
    const sent = await send(request, at);
    out.push({ shape, sent });
    if (again[i]) out.push({ shape: `${shape} through Proxies`, sent: await send(proxify(again[i].request) as Record<string, unknown>, at), plain: sent });
  }
  return out;
}

async function viaAiSdk(c: Case, position: Position): Promise<Sent> {
  const mw = aiSdkMiddleware({ redactor: finder(c.detect), vault: vaultOf(c) });
  const { prompt, at } = aiSdkPrompt(c, position);
  const params = await mw.transformParams({ params: { prompt } });
  const content = (params.prompt[at] as { content: unknown }).content;
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
  return { all: params, target: content, doc };
}

// LangChain.js ---------------------------------------------------------------------------------------------------------

interface LcRequest {
  messages: BaseMessage[];
  systemMessage?: SystemMessage;
  systemPrompt?: string;
}
type Hook = (request: LcRequest, handler: (r: LcRequest) => Promise<AIMessage>) => Promise<AIMessage>;

/** How the system prompt comes into the request: the agent's own (createAgent({ systemPrompt: "..." }) makes a message
 *  of one text block), a SystemMessage given to createAgent, or a request with only `systemPrompt` (langchain 1.0). */
type SystemShape = "agent" | "message" | "prompt-only";
const SYSTEM_SHAPES: readonly SystemShape[] = ["agent", "message", "prompt-only"];

const systemRequest = (text: string, shape: SystemShape): Omit<LcRequest, "messages"> =>
  shape === "agent"
    ? { systemMessage: new SystemMessage({ content: [{ type: "text", text }] }), systemPrompt: text }
    : shape === "message"
      ? { systemMessage: new SystemMessage(text), systemPrompt: text }
      : { systemPrompt: text };

/** Tool-call arguments for the case's input (LangChain's args are an object). */
const argsOf = (c: Case): Record<string, unknown> => (isObject(c.data) ? c.data : { input: c.data !== undefined ? c.data : c.text });

/** The messages with the case at `position`, and the index of that message. */
function langchainMessages(c: Case, position: Position): { messages: BaseMessage[]; at: number } {
  const { text, data } = c;
  const call = (id: string, args: Record<string, unknown>) => new AIMessage({ content: "", tool_calls: [{ id, name: "lookup", args }] });
  const result = (content: unknown) => new ToolMessage({ content: content as ToolMessage["content"], tool_call_id: "c1" });
  switch (position as Exclude<Position, FieldPosition>) {
    case "user":
      return { messages: [new HumanMessage(text)], at: 0 };
    case "system":
      return { messages: [new HumanMessage("hi")], at: -1 };
    case "tool-text":
      return { messages: [new HumanMessage("hi"), call("c1", {}), result(text)], at: 2 };
    case "tool-data":
      // JSON data where content blocks are expected: an object as a list of one row, a list as it is
      return { messages: [new HumanMessage("hi"), call("c1", {}), result(Array.isArray(data) ? data : [data])], at: 2 };
    case "block-data":
      return { messages: [new HumanMessage("hi"), call("c1", {}), result([{ type: "json", json: data }])], at: 2 };
    case "assistant-unrecorded":
      return { messages: [new HumanMessage("hi"), new AIMessage(text), new HumanMessage("thanks")], at: 1 };
    case "tool-input-unrecorded":
      return { messages: [new HumanMessage("hi"), call("c9", argsOf(c)), new ToolMessage({ content: "ok", tool_call_id: "c9" })], at: 1 };
  }
}

/** The messages the model gets for a request a middleware handed on, as the agent of langchain 1.x builds them
 *  (AgentNode): the system message (the one the middleware changed, or one made from a changed systemPrompt) before
 *  the messages, when it has text. */
function modelInput(given: LcRequest, got: LcRequest): BaseMessage[] {
  let system = given.systemMessage ?? new SystemMessage({ content: [{ type: "text", text: given.systemPrompt ?? "" }] });
  const promptChanged = got.systemPrompt !== system.text;
  const messageChanged = got.systemMessage !== given.systemMessage;
  if (promptChanged && messageChanged) throw new Error("the middleware changed both systemPrompt and systemMessage");
  if (promptChanged) system = new SystemMessage({ content: [{ type: "text", text: got.systemPrompt ?? "" }] });
  if (messageChanged) system = new SystemMessage({ ...(got.systemMessage as unknown as ConstructorParameters<typeof SystemMessage>[0] & object) });
  return [...(system.text === "" ? [] : [system]), ...got.messages];
}

/** The fields of a message that a provider is sent. */
const sentFields = (m: BaseMessage) => ({
  type: m.type,
  content: m.content,
  name: m.name,
  tool_calls: AIMessage.isInstance(m) ? m.tool_calls : undefined,
  invalid_tool_calls: AIMessage.isInstance(m) ? m.invalid_tool_calls : undefined,
  tool_call_id: ToolMessage.isInstance(m) ? m.tool_call_id : undefined,
  additional_kwargs: m.additional_kwargs,
});

/** The request the agent gives the middlewares for the case at `position` (the agent's empty system message when the
 *  position is not the system prompt). */
function langchainRequest(c: Case, position: Position, shape: SystemShape): { request: LcRequest; at: number } {
  const { messages, at } = langchainMessages(c, position);
  const system = position === "system" ? systemRequest(c.text, shape) : { systemMessage: new SystemMessage(""), systemPrompt: "" };
  return { request: { messages, ...system }, at };
}

async function viaLangChainShape(c: Case, position: Position, shape: SystemShape): Promise<Sent> {
  const mw = langchainMiddleware({ redactor: finder(c.detect), vault: vaultOf(c) });
  const { request, at } = langchainRequest(c, position, shape);
  let got: LcRequest | undefined;
  await (mw.wrapModelCall as unknown as Hook)(request, async (r) => ((got = r), new AIMessage("ok")));
  const input = modelInput(request, got!);
  const m = position === "system" ? input[0] : got!.messages[at];
  if (position === "system" && !SystemMessage.isInstance(m)) throw new Error("no system message reaches the model");
  const { type: _type, ...target } = sentFields(m);
  const content = m.content as unknown;
  const first = Array.isArray(content) ? (content[0] as unknown) : undefined;
  const args = AIMessage.isInstance(m) ? m.tool_calls?.[0]?.args : undefined;
  const doc =
    position === "system"
      ? m.text
      : position === "tool-data"
        ? isObject(c.data)
          ? first
          : content
        : position === "block-data"
          ? (first as { json?: unknown } | undefined)?.json
          : position === "tool-input-unrecorded"
            ? isObject(c.data)
              ? args
              : (args as { input?: unknown } | undefined)?.input
            : content;
  return { all: input.map(sentFields), target, doc };
}

// LangChain.js: fields outside the messages ----------------------------------------------------------------------------

const isPlain = (v: unknown): v is Record<string, unknown> => isObject(v) && Object.getPrototypeOf(v) === Object.prototype;
/** A tool as the provider is sent it (a framework tool written as a function definition). */
const sentTool = (t: unknown): any => (isPlain(t) ? t : convertToOpenAITool(t as never));

interface LcFieldRequest {
  messages: BaseMessage[];
  systemMessage: SystemMessage;
  systemPrompt: string;
  tools?: unknown[];
  responseFormat?: unknown;
  modelSettings?: Record<string, unknown>;
  model?: unknown;
}

/** A chat model with fields of its own that go into the request, as ChatOpenAI's do: `user` and `modelKwargs`, in
 *  what it reports as its request parameters (`invocationParams`). */
class FieldModel extends FakeListChatModel {
  user?: unknown;
  modelKwargs: Record<string, unknown> = {};
  invocationParams(_options?: unknown): any {
    return { ...(this.user === undefined ? {} : { user: this.user }), ...this.modelKwargs };
  }
}
const fieldModel = (own: { user?: unknown; modelKwargs?: Record<string, unknown> }) => Object.assign(new FieldModel({ responses: ["x"] }), own);
/** The request parameters the model of a request gives (its own fields). */
const modelParams = (r: LcFieldRequest): any => (r.model as FieldModel | undefined)?.invocationParams({});

/** The shapes of a request with the case's text in a field outside the system prompt (the response format: see
 *  viaLangChainAgent), and where that field is in the request the middleware handed on. */
function langchainFields(c: Case, position: FieldPosition): Shape<LcFieldRequest>[] {
  const text = c.text;
  const base = () => ({ messages: [new HumanMessage("hi")] as BaseMessage[], systemMessage: new SystemMessage(""), systemPrompt: "" });
  const fn = (r: LcFieldRequest) => sentTool(r.tools![0]).function;
  const property = (r: LcFieldRequest) => ({ target: fn(r).parameters.properties.q, doc: fn(r).parameters.properties.q.description });
  const json = { type: "object", properties: { q: { type: "string", description: text, examples: [text] } }, required: ["q"] };
  /** A shape whose text is in the model's own fields; `where`: the position in the parameters the model gives. */
  const own = (shape: string, fields: { user?: unknown; modelKwargs?: Record<string, unknown> }, where: (p: any) => unknown, doc?: (p: any) => unknown): Shape<LcFieldRequest> => ({
    shape,
    request: { ...base(), model: fieldModel(fields) },
    at: (r: LcFieldRequest) => ({ target: where(modelParams(r)), doc: doc?.(modelParams(r)) }),
  });
  switch (position) {
    case "message-name": {
      const at = (r: LcFieldRequest) => ({ target: r.messages[0].name, doc: r.messages[0].name });
      return [
        { shape: "human", request: { ...base(), messages: [new HumanMessage({ content: "hi", name: text })] }, at },
        { shape: "chat", request: { ...base(), messages: [new ChatMessage({ content: "hi", role: "user", name: text })] }, at },
      ];
    }
    case "tool-description": {
      const at = (r: LcFieldRequest) => ({ target: fn(r).description, doc: fn(r).description });
      const lookup = tool(async () => "ok", { name: "lookup", description: text, schema: z.object({ q: z.string() }) });
      const definition = { type: "function", function: { name: "lookup", description: text, parameters: { type: "object", properties: {} } } };
      return [
        { shape: "tool", request: { ...base(), tools: [lookup] }, at },
        { shape: "definition", request: { ...base(), tools: [definition] }, at },
      ];
    }
    case "tool-schema": {
      const zod = tool(async () => "ok", { name: "lookup", description: "Looks up a customer", schema: z.object({ q: z.string().describe(text) }) });
      const jsonTool = tool(async () => "ok", { name: "lookup", description: "Looks up a customer", schema: json as never });
      const definition = { type: "function", function: { name: "lookup", description: "Looks up a customer", parameters: json } };
      return [
        { shape: "tool-zod", request: { ...base(), tools: [zod] }, at: property },
        { shape: "tool-json", request: { ...base(), tools: [jsonTool] }, at: property },
        { shape: "definition", request: { ...base(), tools: [definition] }, at: property },
        // (release gate 8, items 3 and 4: a definition and a tool's schema made from classes, examples read through a
        // getter)
        { shape: "definition-class", request: { ...base(), tools: [new CorpusDefinition(text)] }, at: property },
        { shape: "tool-json-class", request: { ...base(), tools: [tool(async () => "ok", { name: "lookup", description: "Looks up a customer", schema: new CorpusSchema(text) as never })] }, at: property },
        {
          shape: "definition-getter-examples",
          request: { ...base(), tools: [{ type: "function", function: { name: "lookup", description: "Looks up a customer", parameters: { ...json, properties: { q: { type: "string", description: text, examples: laterList(text) } } } } }] },
          at: property,
        },
      ];
    }
    case "call-options": {
      const at = (r: LcFieldRequest) => ({ target: r.modelSettings, doc: r.modelSettings?.user });
      return [
        { shape: "model-settings", request: { ...base(), modelSettings: { user: text } }, at },
        own("model-user", { user: text }, (p) => p, (p) => p.user),
        own("model-kwargs", { modelKwargs: { user: text } }, (p) => p, (p) => p.user),
      ];
    }
    case "option-metadata": {
      // the application's metadata in model settings, nested settings, and settings no provider defines
      const settings = (shape: string, modelSettings: Record<string, unknown>, target: (s: any) => unknown): Shape<LcFieldRequest> => ({
        shape,
        request: { ...base(), modelSettings },
        at: (r: LcFieldRequest) => ({ target: target(r.modelSettings), doc: (target(r.modelSettings) as { name?: unknown }).name }),
      });
      return [
        settings("settings-metadata", { metadata: { name: text, id: text, customerId: text } }, (s) => s.metadata),
        settings("settings-nested", { modelKwargs: { metadata: { name: text } } }, (s) => s.modelKwargs.metadata),
        settings("settings-unknown", { name: text, customerId: text, user_key: text }, (s) => s),
        own("model-metadata", { modelKwargs: { metadata: { name: text, id: text, customerId: text } } }, (p) => p.metadata, (p) => p.metadata.name),
        own("model-extra-body", { modelKwargs: { extra_body: { metadata: { name: text } } } }, (p) => p.extra_body.metadata, (p) => p.extra_body.metadata.name),
      ];
    }
    case "option-variables": {
      // (the field "order" is where the position is; the others are checked for leaks)
      const order = (target: (s: any) => any) => (r: LcFieldRequest) => ({ target: target(r.modelSettings).order, doc: target(r.modelSettings).order });
      const variables = { id: "pmpt_1", version: "2", variables: { order: text, model: text, customer: text } };
      return [
        { shape: "prompt-variables", request: { ...base(), modelSettings: { prompt: variables } }, at: order((s) => s.prompt.variables) },
        { shape: "extra-body", request: { ...base(), modelSettings: { extra_body: { order: text, only: text } } }, at: order((s) => s.extra_body) },
        { shape: "routing-names", request: { ...base(), modelSettings: { order: text, only: text } }, at: order((s) => s) },
        own("model-prompt-variables", { modelKwargs: { prompt: variables } }, (p) => p.prompt.variables.order, (p) => p.prompt.variables.order),
        own("model-extra-body", { modelKwargs: { extra_body: { order: text, only: text } } }, (p) => p.extra_body.order, (p) => p.extra_body.order),
        // (release gate 8, item 5: extra_body is data, a gateway's routing names in it too, and a request field's
        // identifier one level down is not where a provider defines it)
        {
          shape: "misplaced-ids",
          request: { ...base(), modelSettings: { extra_body: { provider: { order: [text] }, models: [text], previous_response_id: text }, reasoning: { previous_response_id: text } } },
          at: (r: LcFieldRequest) => ({ target: r.modelSettings, doc: (r.modelSettings?.extra_body as { previous_response_id?: unknown } | undefined)?.previous_response_id }),
        },
      ];
    }
    case "option-numbers": {
      const n = Number(text);
      const at = (target: (s: any) => any) => (r: LcFieldRequest) => ({ target: target(r.modelSettings), doc: undefined });
      return [
        { shape: "metadata", request: { ...base(), modelSettings: { metadata: { customer_number: n } } }, at: at((s) => s.metadata) },
        { shape: "extra-body", request: { ...base(), modelSettings: { extra_body: { customer_number: n, rows: [{ phone: n }] } } }, at: at((s) => s.extra_body) },
        { shape: "settings-unknown", request: { ...base(), modelSettings: { customer_number: n } }, at: at((s) => s) },
        own("model-metadata", { modelKwargs: { metadata: { customer_number: n } } }, (p) => p.metadata),
        own("model-extra-body", { modelKwargs: { extra_body: { customer_number: n, rows: [{ phone: n }] } } }, (p) => p.extra_body),
      ];
    }
    case "option-settings": {
      const n = Number(text);
      const modelSettings = { max_tokens: n, top_k: n, seed: n, thinking: { type: "enabled", budget_tokens: n } };
      return [
        { shape: "settings", request: { ...base(), modelSettings }, at: (r) => ({ target: r.modelSettings, doc: undefined }) },
        own("model-settings", { modelKwargs: { ...modelSettings } }, (p) => p),
      ];
    }
    case "option-ids":
      return [
        { shape: "previous-response-id", request: { ...base(), modelSettings: { previous_response_id: text } }, at: (r) => ({ target: r.modelSettings.previous_response_id, doc: undefined }) },
        { shape: "conversation", request: { ...base(), modelSettings: { conversation: { id: text } } }, at: (r) => ({ target: r.modelSettings.conversation.id, doc: undefined }) },
        own("model-previous-response-id", { modelKwargs: { previous_response_id: text } }, (p) => p.previous_response_id),
        own("model-conversation", { modelKwargs: { conversation: { id: text } } }, (p) => p.conversation.id),
      ];
    case "schema-literal": {
      const definition = (q: unknown) => ({ type: "function", function: { name: "lookup", description: "Looks up a customer", parameters: { type: "object", properties: { q } } } });
      const atQ = (r: LcFieldRequest) => ({ target: fn(r).parameters.properties.q, doc: undefined });
      const constObject = { type: "object", properties: { q: { type: "object", const: { description: text } } }, required: ["q"] };
      return [
        ...literalProperties(text).map(([shape, q]) => ({ shape: `definition-${shape}`, request: { ...base(), tools: [definition(q)] }, at: atQ })),
        { shape: "tool-json-const-object", request: { ...base(), tools: [tool(async () => "ok", { name: "lookup", description: "Looks up a customer", schema: constObject as never })] }, at: atQ },
        { shape: "response-format-const-object", request: { ...base(), responseFormat: { title: "Answer", ...constObject } }, at: (r) => ({ target: r.responseFormat.properties.q, doc: undefined }) },
      ];
    }
    case "schema-nested": {
      const definition = { type: "function", function: { name: "lookup", description: "Looks up a customer", parameters: nestedSchema(text) } };
      const jsonTool = tool(async () => "ok", { name: "lookup", description: "Looks up a customer", schema: nestedSchema(text) as never });
      const at = (r: LcFieldRequest) => ({ target: fn(r).parameters, doc: undefined });
      return [
        { shape: "definition", request: { ...base(), tools: [definition] }, at },
        { shape: "tool-json", request: { ...base(), tools: [jsonTool] }, at },
        { shape: "response-format", request: { ...base(), responseFormat: { title: "Answer", ...nestedSchema(text) } }, at: (r) => ({ target: r.responseFormat, doc: undefined }) },
      ];
    }
    default:
      throw new Error(`no request shapes for ${position}`);
  }
}

async function viaLangChainFields(c: Case, position: FieldPosition): Promise<FieldRun[]> {
  const out: FieldRun[] = [];
  const send = async (request: LcFieldRequest, at: Shape<LcFieldRequest>["at"]): Promise<Sent> => {
    const mw = langchainMiddleware({ redactor: finder(c.detect), vault: vaultOf(c) });
    let got: LcFieldRequest | undefined;
    await (mw.wrapModelCall as unknown as (r: LcFieldRequest, h: (r: LcFieldRequest) => Promise<AIMessage>) => Promise<AIMessage>)(request, async (r) => ((got = r), new AIMessage("ok")));
    const all = { messages: modelInput(request, got!).map(sentFields), tools: (got!.tools ?? []).map(sentTool), responseFormat: got!.responseFormat, modelSettings: got!.modelSettings, modelParams: modelParams(got!) };
    return { all, ...at(got!) };
  };
  const shapes = langchainFields(c, position);
  // (each run gets its own request: a list read through a getter counts its reads)
  const again = throughProxies(c) ? langchainFields(c, position) : [];
  for (const [i, { shape, request, at }] of shapes.entries()) {
    const sent = await send(request, at);
    out.push({ shape, sent });
    if (again[i]) out.push({ shape: `${shape} through Proxies`, sent: await send(proxify(again[i].request) as LcFieldRequest, at), plain: sent });
  }
  return out;
}

/** The response format of a real createAgent, as JSON schema, provider strategy and tool strategy: what the model is
 *  bound with (tools and options) and the messages it gets. */
async function viaLangChainAgent(c: Case): Promise<{ shape: string; sent: Sent }[]> {
  const schema = { title: "Answer", type: "object", description: c.text, properties: { a: { type: "string", description: c.text } }, required: ["a"] };
  // typed fields (a date and an e-mail address), which a placeholder cannot meet (L3-B5 of the review group "options")
  const typed = z.object({ a: z.string().describe(c.text), born: z.string().date().describe(c.text), mail: z.string().email().describe(c.text) }).describe(c.text);
  const formats: [string, () => unknown][] = [
    ["json-schema", () => schema],
    ["provider", () => providerStrategy(schema as never)],
    ["tool", () => toolStrategy(schema as never)],
    ["tool-typed", () => toolStrategy(typed)],
    ["provider-typed", () => providerStrategy(typed)],
  ];
  const out: { shape: string; sent: Sent }[] = [];
  for (const [shape, format] of formats) {
    const bound: { tools: unknown[]; options: Record<string, unknown> }[] = [];
    const seen: BaseMessage[][] = [];
    class Model extends FakeListChatModel {
      bindTools(tools: unknown[], options?: Record<string, unknown>): any {
        bound.push({ tools: tools.map(sentTool), options: options ?? {} });
        return this;
      }
      async invoke(messages: any): Promise<any> {
        seen.push(messages);
        const last = bound.at(-1);
        const native = last?.options.response_format !== undefined;
        // (the tool of the response format by the name the agent gave it: a Zod schema gets one made up)
        const name = (last?.tools[0] as any)?.function?.name ?? "Answer";
        const args = { a: "x", born: "1990-01-02", mail: "x@example.org" };
        return native ? new AIMessage(JSON.stringify(args)) : new AIMessage({ content: "", tool_calls: [{ id: "s1", name, args }] });
      }
    }
    const agent = createAgent({ model: new Model({ responses: ["x"] }), tools: [], responseFormat: format() as never, middleware: [langchainMiddleware({ redactor: finder(c.detect), vault: vaultOf(c) })] });
    await agent.invoke({ messages: [new HumanMessage("hi")] });
    const last = bound.at(-1)!;
    const options = last.options as { response_format?: { json_schema?: { schema?: unknown; description?: unknown } } };
    const native = options.response_format?.json_schema;
    const answerTool = last.tools.map((t: any) => t.function).find((f: any) => f?.name !== undefined);
    const target = native ?? answerTool;
    const doc = native ? (native.schema as { description?: unknown } | undefined)?.description : answerTool?.description;
    out.push({ shape, sent: { all: { bound, messages: seen.map((ms) => ms.map(sentFields)) }, target, doc } });
  }
  return out;
}

// Running a case -------------------------------------------------------------------------------------------------------

/** What is wrong with what the provider received at the position; empty when nothing. */
function problems(c: Case, sent: Sent, position: Position): string[] {
  const out: string[] = [];
  const all = JSON.stringify(sent.all);
  const allStrings = strings(sent.all);
  for (const s of c.leak) {
    const escaped = JSON.stringify(s).slice(1, -1);
    if (allStrings.some((x) => x.includes(s)) || all.includes(s) || all.includes(escaped)) out.push(`leak ${JSON.stringify(s)}`);
  }
  const target = JSON.stringify(sent.target) ?? "";
  const targetStrings = strings(sent.target);
  for (const s of c.keep) if (!targetStrings.some((x) => x.includes(s)) && !target.includes(s)) out.push(`lost ${JSON.stringify(s)}`);
  if (c.fields !== undefined) {
    const n = fieldsOf(sent.doc);
    if (n !== c.fields) out.push(`fields ${n === undefined ? "(not a JSON object)" : n}, want ${c.fields}`);
  }
  const want = expectedAt(c, position);
  if (want !== undefined && canonical(sent.doc) !== canonical(want.value)) out.push(`not the exact output; expected ${clip(want.value)}, received ${clip(sent.doc)}`);
  return out;
}

const clip = (v: unknown) => {
  const s = JSON.stringify(v) ?? String(v);
  return s.length > 600 ? `${s.slice(0, 600)}...` : s;
};

async function run(c: Case, framework: Framework, position: Position): Promise<void> {
  if (framework === "ai-sdk" && !isField(position)) {
    const sent = await viaAiSdk(c, position);
    expect(problems(c, sent, position), clip(sent.target)).toEqual([]);
    return;
  }
  if (isField(position)) {
    const all: FieldRun[] =
      framework === "ai-sdk" ? await viaAiSdkFields(c, position) : position === "response-format" ? await viaLangChainAgent(c) : await viaLangChainFields(c, position);
    const found = all.flatMap(({ shape, sent, plain }) => [...problems(c, sent, position), ...proxyProblems(sent, plain)].map((w) => `${shape}: ${w}`));
    expect(found, clip(all.find(({ sent }) => problems(c, sent, position).length > 0)?.sent.target)).toEqual([]);
    return;
  }
  // the system prompt in each shape a request can have it; any other position once
  const shapes = position === "system" ? SYSTEM_SHAPES : (["agent"] as const);
  const found: string[] = [];
  let shown: unknown;
  for (const shape of shapes) {
    const sent = await viaLangChainShape(c, position, shape);
    const wrong = problems(c, sent, position);
    if (wrong.length > 0 && shown === undefined) shown = sent.target;
    for (const w of wrong) found.push(position === "system" ? `${shape}: ${w}` : w);
  }
  expect(found, clip(shown)).toEqual([]);
}

// Tests ----------------------------------------------------------------------------------------------------------------

describe("vault corpus: the corpus and the list of open cases", () => {
  it("has the format the runners read", () => {
    expect(CORPUS.version).toBe(1);
    const positions = new Set(CORPUS.positions);
    expect([...positions].sort()).toEqual([
      "assistant-unrecorded", "block-data", "call-options", "message-name", "message-options", "option-ids", "option-metadata", "option-numbers",
      "option-settings", "option-variables", "response-format", "schema-literal", "schema-nested", "system", "tool-data", "tool-description",
      "tool-input-unrecorded", "tool-schema", "tool-text", "user",
    ]);
    expect(new Set(CORPUS.cases.map((c) => c.id)).size).toBe(CORPUS.cases.length);
    for (const c of CORPUS.cases) {
      expect(c.where.length, c.id).toBeGreaterThan(0);
      for (const p of c.where) expect(positions.has(p), `${c.id}: ${p}`).toBe(true);
    }
  });

  it("names only cases, frameworks and positions of the corpus, once each and in order", () => {
    const ids = new Set(CORPUS.cases.flatMap((c) => FRAMEWORKS.flatMap((f) => c.where.filter((p) => applies(f, p)).map((p) => testId(c, f, p)))));
    expect(OPEN_LIST.filter((x) => !ids.has(x))).toEqual([]);
    expect(OPEN_LIST).toEqual([...OPEN].sort());
  });

  it("builds the model input the way the agent of langchain does (checked against createAgent)", async () => {
    // a recording chat model: what the agent hands the provider
    const seen: BaseMessage[][] = [];
    class Model extends FakeListChatModel {
      async _generate(messages: BaseMessage[], ...rest: [any, any?]) {
        seen.push(messages);
        return super._generate(messages, ...rest);
      }
      bindTools() {
        return this; // the stock fake returns a new instance, which would drop this subclass
      }
    }
    const c = CORPUS.cases.find((x) => x.id === "r09-C1-known-key")!;
    const inMessages = (ps: readonly Position[]) => ps.filter((p) => !isField(p));
    expect(inMessages(c.where)).toEqual(inMessages(CORPUS.positions));
    // (ids are given by the agent's state, so they are left out on both sides)
    const shown = (ms: BaseMessage[]) => JSON.parse(JSON.stringify(ms.map(sentFields)));
    for (const position of inMessages(c.where)) {
      for (const shape of position === "system" ? (["agent", "message"] as const) : (["agent"] as const)) {
        const { messages } = langchainMessages(c, position);
        const systemPrompt = position !== "system" ? undefined : shape === "agent" ? c.text : new SystemMessage(c.text);
        const agent = createAgent({ model: new Model({ responses: ["ok"] }), tools: [], systemPrompt, middleware: [langchainMiddleware({ redactor: finder(c.detect), vault: vaultOf(c) })] });
        seen.length = 0;
        await agent.invoke({ messages });
        expect(seen).toHaveLength(1);
        const viaHook = await viaLangChainShape(c, position, shape);
        expect(shown(seen[0]), `${position} (${shape})`).toEqual(JSON.parse(JSON.stringify(viaHook.all)));
      }
    }
  });
});

for (const framework of FRAMEWORKS) {
  describe(`vault corpus through the ${framework} middleware`, () => {
    for (const c of CORPUS.cases) {
      for (const position of c.where) {
        if (!applies(framework, position)) continue;
        const name = testId(c, framework, position);
        (OPEN.has(name) ? it.fails : it)(name, () => run(c, framework, position));
      }
    }
  });
}
