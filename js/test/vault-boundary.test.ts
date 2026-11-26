// Gate 4 of the 1.7.1 release standard: the boundary between the
// vault middlewares and the provider. "Each data slot is materialized once as provider-compatible JSON, keys and values
// masked, and that tree is sent. Exemptions are verified at their authorized paths and masking everywhere else. Unsafe
// serialization sends zero requests. Width is folded after each decoding step; composed spans are verified on the
// reverse-order regression."
//
// Run through the real frameworks with a stand-in detector (every exact occurrence of a held value in the text it is
// given) and a fresh vault per call that holds python/tests/vault_boundary_cases.json "held" and a USERNAME shaped like
// an identifier:
//   - AI SDK 5, 6 and 7: generateText and streamText through wrapLanguageModel (a real agent loop with real tools), and
//     the wrapped model's doGenerate / doStream with call options built by hand (positions the AI SDK's own prompt
//     conversion does not produce, such as part types of a later version);
//   - LangChain.js: a real createAgent whose model records what it is given (the messages, and the model settings the
//     agent binds it with; an application's middleware outside this one sets them).
// What the recording model receives is what a provider adapter writes into the HTTP body. Checked on every call:
//   plain    every value that reaches the provider is plain JSON: strings, finite numbers, booleans, null, arrays and
//            plain objects (no class instance, Date, Map, Set, typed array, BigInt, accessor property, toJSON method,
//            cycle). Binary data may stand only where the framework defines file data (a file part's data, a file
//            block's data).
//   leaks    no held value (in any ASCII letter case) in the JSON a provider adapter writes of it (JSON.stringify, a
//            BigInt as its digits, as permissive adapters write it), outside an identifier's authorized path.
// Groups:
//   plain JSON and unsafe input   values a tool returns or an application passes that JSON writes in another form
//            (Date, class instance, toJSON, Map, Set, bytes, NaN, accessors) must reach the provider as masked plain
//            JSON; input JSON cannot write (a cycle, a throwing toJSON or getter) must fail the call with nothing sent;
//            a BigInt may do either. When the middleware is given plain JSON already (LangChain.js writes a tool's
//            result that is not content as a string), the expectation is "materialize". A call that fails with nothing
//            sent is run again without the middleware: when that fails too, the framework refuses the input itself
//            (reported as "refused by the framework"; it passes when nothing was sent).
//   identifiers   each name of "identifiers" stays as written at its authorized path and is masked when it is planted
//            with a held value anywhere else (data payloads at any depth, tool input, typed rows, part and block types
//            the framework does not have, an unknown provider's options and model settings, extra_body, the
//            application's metadata, a stored prompt's variables, additional_kwargs).
//   readings      a held e-mail address written through decoding steps with full-width characters between them: the
//            text the provider receives is exactly "expect" (the whole source span replaced, the neighbours intact),
//            with the stand-in detector and with none (the vault's readings alone). Cases with three or more decoding
//            steps are best effort (D1) and run in their own describe block.
// The same cases run in Python (python/tests/test_vault_boundary.py). Failures are written out in full: each is a
// finding for the fixers, not a flake.
//
// The AI SDK is not a dependency of this package: AI_SDK_DIR names a folder where ai@5 (as "ai5"), ai@6 ("ai") and ai@7
// ("ai7") are installed (js/scripts/install-gate-ai-sdk.sh writes one). Without it the AI SDK groups are skipped, unless
// PII_GATE=1 (a release gate run), where a missing AI SDK fails the run:
//   sh scripts/install-gate-ai-sdk.sh ../.gate-ai-sdk && AI_SDK_DIR=../.gate-ai-sdk PII_GATE=1 npx vitest run test/vault-boundary.test.ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { types as utilTypes } from "node:util";
import { describe, expect, it } from "vitest";
import { AIMessage, createAgent, createMiddleware, HumanMessage, providerStrategy, tool, ToolMessage, toolStrategy, type BaseMessage } from "langchain";
import { FakeListChatModel } from "@langchain/core/utils/testing";
import { convertToOpenAITool } from "@langchain/core/utils/function_calling";
import { z } from "zod";
import { piiMiddleware as aiSdkMiddleware } from "../src/ai-sdk";
import { piiMiddleware as langchainMiddleware } from "../src/langchain";
import type { Redactor } from "../src/redactor";
import type { Span } from "../src/types";
import { createVault, type Vault } from "../src/vault";

// ---------------------------------------------------------------------------------------------------- shared cases

interface Reading {
  id: string;
  note: string;
  steps: string[];
  decode_steps: number;
  guaranteed: boolean;
  layout: string;
  text: string;
  expect: string;
  source: string;
}
const CASES = JSON.parse(readFileSync(new URL("../../python/tests/vault_boundary_cases.json", import.meta.url), "utf8")) as {
  version: number;
  held: { label: string; value: string }[];
  identifiers: string[];
  frame_fields: string[];
  readings: Reading[];
};
const HELD: readonly (readonly [string, string])[] = CASES.held.map((h) => [h.label, h.value] as const);
const PERSON = "Kim Minsu";
const EMAIL = "kim@example.com";
const PHONE = "821023456789";
const DOB = "1990-01-02";
// a held value shaped like an identifier: it stands at identifiers' authorized paths and is planted under their names
const ID = "kimminsu_77";
const ALL_HELD: readonly (readonly [string, string])[] = [...HELD, ["USERNAME", ID]];
for (const v of [PERSON, EMAIL, PHONE, DOB]) if (!HELD.some(([, h]) => h === v)) throw new Error(`vault_boundary_cases.json has no held ${v}`);

/** A stand-in detector: every exact occurrence of a held value in the text it is given; of two that overlap, the one
 *  that starts first (the longer of two that start together). The vault handling is the middleware's own. */
const finder = (values: readonly (readonly [string, string])[]): Redactor => {
  const detect = async (text: string): Promise<Span[]> => {
    const all: Span[] = [];
    for (const [label, value] of values) {
      for (let i = text.indexOf(value); i >= 0; i = text.indexOf(value, i + 1)) all.push({ start: i, end: i + value.length, label, source: "model" });
    }
    all.sort((a, b) => a.start - b.start || b.end - a.end);
    const out: Span[] = [];
    for (const s of all) if (out.length === 0 || s.start >= out[out.length - 1].end) out.push(s);
    return out;
  };
  return { sensitivity: 0, detect } as unknown as Redactor;
};
/** A detector that finds nothing: what is masked is what the vault already holds (gate 3 runs this way). */
const off = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
/** The detectors the readings run with: the stand-in, and none (the vault's readings alone must find the value). */
const DETECTORS: readonly (readonly ["finder" | "off", Redactor])[] = [
  ["finder", finder(ALL_HELD)],
  ["off", off],
];
const vaultOf = (values: readonly (readonly [string, string])[]): Vault => {
  const vault = createVault();
  for (const [label, value] of values) vault.tag(label, value);
  return vault;
};

// ---------------------------------------------------------------------------------------------------- checks

type Path = (string | number)[];
const show = (p: Path) => (p.length === 0 ? "$" : p.join("."));
const at = (x: unknown, p: Path): unknown => p.reduce<unknown>((o, k) => (o !== null && typeof o === "object" ? (o as Record<string | number, unknown>)[k] : undefined), x);

/** Where `x` holds a value that is not plain JSON, as "path: what". `binary(path, parent)`: binary data (bytes, a URL
 *  object) may stand there. */
function nonPlain(x: unknown, binary: (path: Path, parent: unknown) => boolean = () => false): string[] {
  const out: string[] = [];
  const stack: object[] = [];
  const walk = (v: unknown, path: Path, parent: unknown): void => {
    const bad = (what: string): void => void out.push(`${show(path)}: ${what}`);
    switch (typeof v) {
      case "string":
      case "boolean":
      case "undefined":
        return;
      case "number":
        if (!Number.isFinite(v)) bad(`number ${v}`);
        return;
      case "bigint":
        return bad("BigInt");
      case "symbol":
      case "function":
        return bad(typeof v);
    }
    if (v === null) return;
    const o = v as object;
    if (stack.includes(o)) return bad("cycle");
    // (a Proxy is not plain JSON: its traps can give a writer something else than its descriptors hold, and a revoked
    // one cannot be read at all; the confirmation of release gate 8, items 3 and 4)
    if (utilTypes.isProxy(o)) return bad("Proxy");
    const proto = Object.getPrototypeOf(o);
    if (Array.isArray(o)) {
      if (proto !== Array.prototype) bad("array subclass");
      // (an index read through a getter, a hole, or a field of a list's own: a writer can read something else there)
      if (Reflect.ownKeys(o).length !== o.length + 1) bad("a list with a hole or a field of its own");
      stack.push(o);
      for (let i = 0; i < o.length; i++) {
        const d = Object.getOwnPropertyDescriptor(o, i);
        if (d && !("value" in d)) out.push(`${show([...path, i])}: accessor property`);
        else walk(d?.value, [...path, i], o);
      }
      stack.pop();
      return;
    }
    if (proto !== Object.prototype && proto !== null) {
      if (binary(path, parent) && (o instanceof Uint8Array || o instanceof ArrayBuffer || o instanceof URL)) return;
      return bad(`instance of ${(o as { constructor?: { name?: string } }).constructor?.name ?? "an unknown class"}`);
    }
    if (typeof Object.getOwnPropertyDescriptor(o, "toJSON")?.value === "function") bad("toJSON method");
    stack.push(o);
    for (const key of Object.keys(o)) {
      const d = Object.getOwnPropertyDescriptor(o, key)!;
      if (d.get || d.set) {
        out.push(`${show([...path, key])}: accessor property`);
        continue;
      }
      walk(d.value, [...path, key], o);
    }
    stack.pop();
  };
  walk(x, [], undefined);
  return out;
}

/** What a provider adapter writes for `x`: JSON, a BigInt as its digits (a permissive adapter); undefined when even
 *  that cannot be written (a cycle, a toJSON that throws). */
function written(x: unknown): string | undefined {
  try {
    return JSON.stringify(x, (_k, v) => (typeof v === "bigint" ? v.toString() : v));
  } catch {
    return undefined;
  }
}

/** `x` with the values at `paths` replaced by a marker (a copy of the containers on those paths only). */
function without(x: unknown, paths: readonly Path[]): unknown {
  const set = (o: unknown, p: Path): unknown => {
    if (p.length === 0) return "<authorized>";
    if (o === null || typeof o !== "object") return o;
    const [k, ...rest] = p;
    const copy: Record<string | number, unknown> = Array.isArray(o) ? [...o] : Object.assign(Object.create(Object.getPrototypeOf(o)), o);
    copy[k] = set((o as Record<string | number, unknown>)[k], rest);
    return copy;
  };
  return paths.reduce(set, x);
}

/** `x` with the keys at `paths` (a path to an object and the key in it) renamed to a marker: a key that stays as written
 *  at its authorized place (a schema's property name) is not a value sent elsewhere. */
function withoutKeys(x: unknown, paths: readonly Path[]): unknown {
  const rename = (o: unknown, p: Path): unknown => {
    if (o === null || typeof o !== "object") return o;
    const [k, ...rest] = p;
    if (rest.length === 0) {
      const copy: Record<string | number, unknown> = {};
      for (const [key, v] of Object.entries(o)) copy[key === k ? "<authorized key>" : key] = v;
      return copy;
    }
    const copy: Record<string | number, unknown> = Array.isArray(o) ? [...o] : Object.assign(Object.create(Object.getPrototypeOf(o)), o);
    copy[k] = rename((o as Record<string | number, unknown>)[k], rest);
    return copy;
  };
  return paths.reduce(rename, x);
}

/** The held values that stand in what a provider adapter writes of `x` (any ASCII case). */
function leaks(x: unknown, values: readonly (readonly [string, string])[] = ALL_HELD): string[] {
  const text = written(x);
  if (text === undefined) return [];
  const t = text.toLowerCase();
  return values.filter(([, v]) => t.includes(v.toLowerCase())).map(([, v]) => v);
}

/** Fails with every problem written out (a report the release gate reads), or passes when there is none. */
function none(problems: readonly string[], what = "boundary"): void {
  if (problems.length > 0) throw new Error(`${what}: ${problems.length} problem(s)\n  - ${problems.join("\n  - ")}`);
}

/** What a fixture says of what is sent for it (the confirmation of release gate 8, items 3 and 4): `has`, text that the
 *  JSON a provider writes of the calls must hold (what property access gives, masked as read); `not`, text it must not
 *  hold (what a Proxy's descriptors store, which JSON never reads). */
interface SentText {
  has?: readonly string[];
  not?: readonly string[];
}
function sentProblems(o: { trees: unknown[] }, sent: SentText | undefined): string[] {
  if (!sent || o.trees.length === 0) return [];
  const text = o.trees.map((t) => written(t) ?? "").join(String.fromCharCode(10));
  return [
    ...(sent.has ?? []).filter((s) => !text.includes(s)).map((s) => `not sent although JSON writes it: ${JSON.stringify(s)}`),
    ...(sent.not ?? []).filter((s) => text.includes(s)).map((s) => `sent although JSON never reads it: ${JSON.stringify(s)}`),
  ];
}

/** The outcome of one call with a planted input. */
interface Outcome {
  /** what the model received after the input was planted (each call's request) */
  sent: unknown[];
  /** the request trees to check, per sent call */
  trees: unknown[];
  /** the trees the middleware was given for those calls (what the framework made of the input before it) */
  given: unknown[];
  error?: unknown;
  /** whether the middleware was entered for the call that would have sent the planted input */
  entered: boolean;
}
type Expectation = "materialize" | "fail-closed" | "either";
const describeError = (e: unknown) => (e instanceof Error ? `${e.name}: ${e.message}`.slice(0, 160) : String(e).slice(0, 160));

/** Whether the framework itself made the input plain JSON before the middleware got it (LangChain.js writes a tool's
 *  result that is not content with JSON.stringify, or as a string): no unsafe serialization reached the boundary, so the
 *  call must go out masked like any other. */
const convertedByFramework = (o: Outcome, binary: (path: Path, parent: unknown) => boolean) => o.given.length > 0 && o.given.every((g) => nonPlain(g, binary).length === 0);

/** Problems of an outcome under the expectation of its fixture. */
function judge(o: Outcome, fixtureExpectation: Expectation, binary: (path: Path, parent: unknown) => boolean): string[] {
  const expectation: Expectation = fixtureExpectation !== "materialize" && convertedByFramework(o, binary) ? "materialize" : fixtureExpectation;
  const problems: string[] = [];
  const sent = o.trees.length > 0;
  for (const t of o.trees) {
    for (const b of nonPlain(t, binary)) problems.push(`not plain JSON: ${b}`);
    for (const v of leaks(t)) problems.push(`held value sent: ${v}`);
  }
  if (sent && o.error !== undefined) problems.push(`sent and also failed: ${describeError(o.error)}`);
  if (!sent && o.error === undefined) problems.push("nothing sent and no error");
  if (!sent && o.error !== undefined && o.entered && expectation === "materialize")
    problems.push(`the middleware failed a call JSON can write (a valid request broken): ${describeError(o.error)}`);
  if (sent && expectation === "fail-closed") problems.push("input JSON cannot write was sent instead of failing the call");
  return problems;
}
/** Whether a call that failed with nothing sent fails the same way without the middleware: the framework refuses the
 *  input itself (listed, and it must send nothing). `baseline` runs the same call without the middleware. */
async function refusedByFramework(o: Outcome, baseline: () => Promise<Outcome>): Promise<boolean> {
  if (o.trees.length > 0 || o.error === undefined) return false;
  if (!o.entered) return true;
  const base = await baseline();
  return base.trees.length === 0 && base.error !== undefined;
}
/** The problems of a plain-JSON outcome, with the framework's own refusals taken out (and reported). */
async function plainProblems(o: Outcome, expectation: Expectation, binary: (path: Path, parent: unknown) => boolean, baseline: () => Promise<Outcome>, what: string, sent?: SentText): Promise<string[]> {
  const problems = [...judge(o, expectation, binary), ...sentProblems(o, sent)];
  if (!(await refusedByFramework(o, baseline))) return problems;
  console.info(`refused by the framework (nothing sent): ${what}: ${describeError(o.error)}`);
  return problems.filter((p) => !p.startsWith("the middleware failed"));
}

// ---------------------------------------------------------------------------------------------------- fixtures

class Customer {
  constructor(
    public name: string,
    public email: string,
  ) {}
}
class Money {
  toJSON() {
    return { amount: PHONE };
  }
}
/** An object whose JSON is harmless the first time it is written and holds a held value afterwards: a tree sent with
 *  the object in it instead of what was masked is written again by the provider adapter. */
class Lazy {
  private reads = 0;
  toJSON() {
    return this.reads++ === 0 ? { note: "nothing here" } : { note: PERSON };
  }
}
const accessor = () => Object.defineProperty({}, "name", { get: () => PERSON, enumerable: true });
const throwingGetter = () => Object.defineProperty({}, "name", { get: () => { throw new Error("getter refuses"); }, enumerable: true });
const cyclic = () => {
  const o: Record<string, unknown> = { name: PERSON };
  o.self = o;
  return o;
};

// Proxies (the confirmation of release gate 8, items 3 and 4): their descriptors store STORED while property access gives
// another value. JSON reads through the traps, each value once, so what it gives is what must be masked and sent.
const STORED = "draft-stored";
const VIA_GET = `${PERSON} via-get`;
const KEPT = "kept via-get";
/** A Proxy around `target` whose `get` of each key in `answers` gives `answers[key](n)` on the n-th read. */
const viaGet = <T extends object>(target: T, answers: Record<string, (n: number) => unknown>): T => {
  const reads = new Map<string, number>();
  return new Proxy(target, {
    get(o, k, r) {
      if (typeof k !== "string" || !Object.prototype.hasOwnProperty.call(answers, k)) return Reflect.get(o, k, r);
      const n = (reads.get(k) ?? 0) + 1;
      reads.set(k, n);
      return answers[k](n);
    },
  });
};
const revokedProxy = () => {
  const r = Proxy.revocable({ name: PERSON }, {});
  r.revoke();
  return r.proxy;
};
const PROXY_SENT: SentText = { has: ["via-get"], not: [STORED] };

/** Values that are not plain JSON, each with what the boundary must do with it. A fixture is made fresh for each call. */
const PLAIN_FIXTURES: { id: string; make: () => unknown; expect: Expectation; sent?: SentText }[] = [
  { id: "proxy-class-instance", make: () => viaGet(new Customer(STORED, STORED), { name: () => VIA_GET, email: () => EMAIL }), expect: "materialize", sent: PROXY_SENT },
  { id: "proxy-plain-object", make: () => viaGet({ note: STORED, n: 1 }, { note: () => KEPT }), expect: "materialize", sent: { has: [KEPT], not: [STORED] } },
  { id: "proxy-list", make: () => viaGet([STORED, "row"], { "0": () => VIA_GET }), expect: "materialize", sent: PROXY_SENT },
  { id: "proxy-list-length", make: () => viaGet(["row"], { length: () => 2, "1": () => VIA_GET }), expect: "materialize", sent: PROXY_SENT },
  { id: "proxy-toJSON", make: () => viaGet({ note: STORED }, { toJSON: () => () => ({ note: VIA_GET }) }), expect: "materialize", sent: PROXY_SENT },
  // (a framework may read the value itself before the middleware does: the read the middleware makes is masked and sent,
  // and a writer that read it again would send the held name, which the leak check sees)
  { id: "proxy-first-read", make: () => viaGet({ note: STORED }, { note: (n) => (n === 1 ? KEPT : PERSON) }), expect: "materialize", sent: { not: [STORED] } },
  { id: "proxy-nested", make: () => ({ rows: viaGet([viaGet({ who: STORED }, { who: () => VIA_GET })], {}) }), expect: "materialize", sent: PROXY_SENT },
  { id: "proxy-revoked", make: revokedProxy, expect: "fail-closed" },
  { id: "proxy-trap-throws", make: () => new Proxy({ name: PERSON }, { get: () => { throw new Error("trap refuses"); } }), expect: "fail-closed" },
  { id: "class-instance", make: () => new Customer(PERSON, EMAIL), expect: "materialize" },
  { id: "toJSON", make: () => new Money(), expect: "materialize" },
  { id: "toJSON-second-write", make: () => new Lazy(), expect: "materialize" },
  { id: "date", make: () => new Date(`${DOB}T00:00:00.000Z`), expect: "materialize" },
  { id: "map", make: () => new Map([[PERSON, EMAIL]]), expect: "materialize" },
  { id: "set", make: () => new Set([PERSON, EMAIL]), expect: "materialize" },
  { id: "bytes", make: () => new TextEncoder().encode(PERSON), expect: "materialize" },
  { id: "nan", make: () => ({ score: Number.NaN, who: PERSON }), expect: "materialize" },
  { id: "accessor", make: accessor, expect: "materialize" },
  { id: "null-prototype", make: () => Object.assign(Object.create(null), { name: PERSON }), expect: "materialize" },
  { id: "nested-instances", make: () => ({ rows: [new Customer(PERSON, EMAIL)], at: new Date(`${DOB}T00:00:00.000Z`), tags: new Set([EMAIL]) }), expect: "materialize" },
  { id: "bigint", make: () => ({ phone: BigInt(PHONE) }), expect: "either" },
  { id: "cycle", make: cyclic, expect: "fail-closed" },
  { id: "toJSON-throws", make: () => ({ toJSON() { throw new Error("toJSON refuses"); } }), expect: "fail-closed" },
  { id: "getter-throws", make: throwingGetter, expect: "fail-closed" },
];

/** An object with every identifier name as a key and `value` under each (`skip`: names left out). */
const plantAll = (value: string, skip: readonly string[] = []) => Object.fromEntries(CASES.identifiers.filter((n) => !skip.includes(n)).map((n) => [n, value]));
/** Framework shapes inside data: every frame field holding an object (or a list of typed objects) with the identifiers
 *  planted in it. A tool's payload is data at every depth, whatever names and types its objects have. */
const plantFramed = (value: string) => ({
  ...Object.fromEntries(CASES.frame_fields.map((f) => [f, plantAll(value)])),
  content: [
    { ...plantAll(value), type: "text", text: "row" },
    { ...plantAll(value), type: "tool-call" },
    { ...plantAll(value), type: "tool_use" },
    { ...plantAll(value), type: "image" },
  ],
  tool_calls: [{ ...plantAll(value), type: "function", function: plantAll(value) }],
});
/** Every path at which `x` holds `value` (string leaves only). */
function pathsOf(x: unknown, value: string, path: Path = [], out: Path[] = []): Path[] {
  if (typeof x === "string") {
    if (x === value) out.push(path);
  } else if (Array.isArray(x)) x.forEach((y, i) => pathsOf(y, value, [...path, i], out));
  else if (x !== null && typeof x === "object") for (const [k, y] of Object.entries(x)) pathsOf(y, value, [...path, k], out);
  return out;
}

// Schemas (release gate 8, items 3 and 4): a schema given as a class instance, at the top or inside, with getters, as
// what toJSON gives, and a list read through a getter (the reviewer's: "control" the first three reads, a held value
// afterwards). What the provider receives is plain JSON with the description hidden, and the list's first read.
class SchemaField {
  type = "string";
  description = `Contact ${PERSON}.`;
}
class SchemaRoot {
  type = "object";
  description = `Contact ${PERSON}.`;
  properties = { note: { type: "string" } };
  required = ["note"];
  additionalProperties = false;
}
const readLater = () => {
  let reads = 0;
  const list: unknown[] = [];
  Object.defineProperty(list, "0", { enumerable: true, get: () => (++reads >= 4 ? PERSON : "control") });
  return list;
};
class StoredField {
  type = "string";
  description = STORED;
}
class StoredRoot {
  type = "object";
  description = STORED;
  properties = { note: { type: "string" } };
  required = ["note"];
  additionalProperties = false;
}
const SCHEMA_FIXTURES: { id: string; make: () => unknown; sent?: SentText }[] = [
  // (the confirmation of release gate 8, items 3 and 4: a Proxy around a class-backed field, a list, the root, the
  // properties map, and a description whose first read is the one JSON writes)
  { id: "proxy-property", make: () => ({ type: "object", properties: { note: viaGet(new StoredField(), { description: () => `Contact ${VIA_GET}.` }) } }), sent: PROXY_SENT },
  { id: "proxy-examples", make: () => ({ type: "object", properties: { name: { type: "string", examples: viaGet([STORED], { "0": () => VIA_GET }) } } }), sent: PROXY_SENT },
  { id: "proxy-root", make: () => viaGet(new StoredRoot(), { description: () => `Contact ${VIA_GET}.` }), sent: PROXY_SENT },
  { id: "proxy-properties", make: () => ({ type: "object", properties: viaGet({ note: { type: "string", description: STORED } }, { note: () => ({ type: "string", description: KEPT }) }) }), sent: { has: [KEPT], not: [STORED] } },
  { id: "proxy-first-read", make: () => ({ type: "object", properties: { note: viaGet({ type: "string", description: STORED }, { description: (n) => (n === 1 ? KEPT : `Contact ${PERSON}.`) }) } }), sent: { not: [STORED] } },
  { id: "class-root", make: () => new SchemaRoot() },
  { id: "class-property", make: () => ({ type: "object", properties: { note: new SchemaField() } }) },
  { id: "class-nested", make: () => ({ type: "object", properties: { a: { anyOf: [new SchemaField()] }, b: { type: "array", items: new SchemaField() } }, $defs: { d: new SchemaField() } }) },
  { id: "getter-description", make: () => ({ type: "object", properties: { note: Object.defineProperty({ type: "string" }, "description", { enumerable: true, get: () => `Contact ${PERSON}.` }) } }) },
  { id: "toJSON-property", make: () => ({ type: "object", properties: { note: { toJSON: () => ({ type: "string", description: `Contact ${PERSON}.` }) } } }) },
  { id: "getter-examples", make: () => ({ type: "object", properties: { name: { type: "string", examples: readLater() } } }) },
  { id: "getter-required", make: () => ({ type: "object", properties: { control: { type: "string" } }, required: readLater() }) },
];

// ---------------------------------------------------------------------------------------------------- AI SDK

interface Sdk {
  name: string;
  version: string;
  ai: any;
  test: any;
  Mock: any;
  spec: "v2" | "v3";
  usage: unknown;
  finish: (r: string) => unknown;
}

async function loadSdks(): Promise<Sdk[]> {
  const dir = process.env.AI_SDK_DIR;
  if (!dir) return [];
  const entry = async (pkg: string, sub: "." | "./test") => {
    const root = join(dir, "node_modules", pkg);
    const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
    const e = manifest.exports[sub];
    const file = typeof e === "string" ? e : (e.import ?? e.default);
    return { module: await import(pathToFileURL(join(root, file)).href), version: manifest.version as string };
  };
  const usage2 = { inputTokens: 1, outputTokens: 1, totalTokens: 2 };
  const usage3 = { inputTokens: { total: 1, noCache: 1, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: 1, text: 1, reasoning: 0 } };
  const rows: [string, string, string, "v2" | "v3", unknown, (r: string) => unknown][] = [
    ["ai5", "ai5", "MockLanguageModelV2", "v2", usage2, (r) => r],
    ["ai6", "ai", "MockLanguageModelV3", "v3", usage3, (r) => ({ unified: r, raw: r })],
    ["ai7", "ai7", "MockLanguageModelV4", "v3", usage3, (r) => ({ unified: r, raw: r })],
  ];
  const out: Sdk[] = [];
  for (const [name, pkg, mock, spec, usage, finish] of rows) {
    const ai = await entry(pkg, ".");
    const test = await entry(pkg, "./test");
    if (typeof test.module[mock] !== "function") throw new Error(`${pkg} has no ${mock}`);
    out.push({ name, version: ai.version, ai: ai.module, test: test.module, Mock: test.module[mock], spec, usage, finish });
  }
  return out;
}
const SDKS = await loadSdks();
const GATE = process.env.PII_GATE === "1";

it.runIf(GATE)("the release gate runs with the real AI SDK 5, 6 and 7 (AI_SDK_DIR)", () => {
  expect(SDKS.map((s) => s.name)).toEqual(["ai5", "ai6", "ai7"]);
});

type Step = { text?: string; calls?: { id: string; name: string; input: string }[] };
const MODES = ["generate", "stream"] as const;
type Mode = (typeof MODES)[number];

/** A model that records each call's options and answers with the steps in order (the last one again after that). */
function recordingModel(sdk: Sdk, steps: readonly Step[], sent: any[]) {
  let i = 0;
  const next = () => steps[Math.min(i++, steps.length - 1)];
  const calls = (s: Step) => (s.calls ?? []).map((c) => ({ type: "tool-call", toolCallId: c.id, toolName: c.name, input: c.input }));
  const reason = (s: Step) => sdk.finish(s.calls?.length ? "tool-calls" : "stop");
  return new sdk.Mock({
    doGenerate: async (options: any) => {
      sent.push(options);
      const s = next();
      return { content: [...(s.text !== undefined ? [{ type: "text", text: s.text }] : []), ...calls(s)], finishReason: reason(s), usage: sdk.usage, warnings: [] };
    },
    doStream: async (options: any) => {
      sent.push(options);
      const s = next();
      const text = s.text !== undefined ? [{ type: "text-start", id: "t" }, { type: "text-delta", id: "t", delta: s.text }, { type: "text-end", id: "t" }] : [];
      const chunks = [{ type: "stream-start", warnings: [] }, ...text, ...calls(s), { type: "finish", finishReason: reason(s), usage: sdk.usage }];
      return { stream: sdk.test.simulateReadableStream({ chunks, initialDelayInMs: null, chunkDelayInMs: null }) };
    },
  });
}

/** The AI SDK middleware with a fresh vault, a count of the calls it was entered for and the params it was given. */
function aiSdkPii(sdk: Sdk, redactor: Redactor = finder(ALL_HELD)) {
  const mw = aiSdkMiddleware({ redactor, vault: vaultOf(ALL_HELD), specificationVersion: sdk.spec });
  const state = { entered: 0, given: [] as unknown[] };
  const middleware = { ...mw, transformParams: async (o: { params: unknown }) => (state.entered++, state.given.push(o.params), mw.transformParams(o as never)) };
  return { middleware, state };
}

/** The request trees of one AI SDK call's options: the prompt, and the call's fields outside it. */
const aiSdkTree = (options: any) => ({ prompt: options.prompt, tools: options.tools, toolChoice: options.toolChoice, providerOptions: options.providerOptions, responseFormat: options.responseFormat });
/** Binary data in an AI SDK prompt: a file part's data (bytes, a URL object; AI SDK 7: { type: "data" | "url", ... }). */
const aiSdkBinary = (path: Path, parent: unknown) => {
  const p = show(path);
  const type = (parent as { type?: unknown } | null)?.type;
  return (/^prompt\.\d+\.content\.\d+\.data$/.test(p) && (type === "file" || type === "image" || type === "reasoning-file")) || (/^prompt\.\d+\.content\.\d+\.data\.(data|url)$/.test(p) && (type === "data" || type === "url"));
};

/** One generateText or streamText run: `steps` are the model's answers; `from` is the index of the first model call
 *  that holds the planted input (a tool's result comes in the second call). */
/** `pii: false` runs the same call without the middleware (the baseline: what the framework does on its own). */
async function aiSdkRun(
  sdk: Sdk,
  mode: Mode,
  args: { prompt?: string; messages?: unknown[]; system?: string; tools?: Record<string, unknown>; steps: Step[]; redactor?: Redactor; pii?: boolean },
  from: number,
): Promise<Outcome> {
  const sent: any[] = [];
  const { middleware, state } = aiSdkPii(sdk, args.redactor);
  const recorder = recordingModel(sdk, args.steps, sent);
  const model = args.pii === false ? recorder : sdk.ai.wrapLanguageModel({ model: recorder, middleware });
  const call: Record<string, unknown> = { model, tools: args.tools, stopWhen: sdk.ai.stepCountIs(args.steps.length), system: args.system };
  if (args.messages) call.messages = args.messages;
  else call.prompt = args.prompt;
  let error: unknown;
  try {
    if (mode === "generate") await sdk.ai.generateText(call);
    else {
      const r = sdk.ai.streamText({ ...call, onError: ({ error: e }: { error: unknown }) => void (error ??= e) });
      for await (const p of r.fullStream) if ((p as { type?: string }).type === "error") error ??= (p as { error?: unknown }).error ?? p;
    }
  } catch (e) {
    error ??= e;
  }
  const after = sent.slice(from);
  return { sent: after, trees: after.map(aiSdkTree), given: state.given.slice(from).map(aiSdkTree), error, entered: state.entered > from };
}

/** One call of the wrapped model with call options built by hand (a prompt of the provider specification). */
async function aiSdkDirect(sdk: Sdk, mode: Mode, options: Record<string, unknown>, pii = true): Promise<Outcome> {
  const sent: any[] = [];
  const { middleware, state } = aiSdkPii(sdk);
  const recorder = recordingModel(sdk, [{ text: "ok" }], sent);
  const model = pii ? sdk.ai.wrapLanguageModel({ model: recorder, middleware }) : recorder;
  let error: unknown;
  try {
    if (mode === "generate") await model.doGenerate(options);
    else {
      const r = await model.doStream(options);
      const reader = (r.stream as ReadableStream).getReader();
      while (!(await reader.read()).done);
    }
  } catch (e) {
    error = e;
  }
  return { sent, trees: sent.map(aiSdkTree), given: state.given.map(aiSdkTree), error, entered: state.entered > 0 };
}

const lookupStep = (input = "{}"): Step => ({ calls: [{ id: "c1", name: "lookup", input }] });
/** A prompt of the provider specification: a user question, the model's call of a tool and the tool's result. */
const toolPrompt = (input: unknown, output: unknown, extra: { user?: unknown[]; assistant?: unknown[] } = {}) => [
  { role: "user", content: [{ type: "text", text: "Look it up." }, ...(extra.user ?? [])] },
  { role: "assistant", content: [{ type: "tool-call", toolCallId: "c1", toolName: "lookup", input }, ...(extra.assistant ?? [])] },
  { role: "tool", content: [{ type: "tool-result", toolCallId: "c1", toolName: "lookup", output }] },
];

/** The places an AI SDK application can put a value that is not plain JSON. `from`: the first model call that holds it. */
const AI_SDK_PLAIN_POSITIONS: { id: string; run: (sdk: Sdk, mode: Mode, value: () => unknown, pii?: boolean) => Promise<Outcome> }[] = [
  {
    id: "tool-return",
    run: (sdk, mode, value, pii = true) =>
      aiSdkRun(sdk, mode, { prompt: "Look it up.", tools: { lookup: sdk.ai.tool({ description: "Look up", inputSchema: sdk.ai.jsonSchema({ type: "object", properties: {} }), execute: async () => value() }) }, steps: [lookupStep(), { text: "done" }], pii }, 1),
  },
  {
    id: "tool-return-row",
    run: (sdk, mode, value, pii = true) =>
      aiSdkRun(sdk, mode, { prompt: "Look it up.", tools: { lookup: sdk.ai.tool({ description: "Look up", inputSchema: sdk.ai.jsonSchema({ type: "object", properties: {} }), execute: async () => ({ row: value(), note: "ok" }) }) }, steps: [lookupStep(), { text: "done" }], pii }, 1),
  },
  {
    id: "messages-tool-result",
    run: (sdk, mode, value, pii = true) =>
      aiSdkRun(sdk, mode, {
        messages: [
          { role: "user", content: "Look it up." },
          { role: "assistant", content: [{ type: "tool-call", toolCallId: "c1", toolName: "lookup", input: {} }] },
          { role: "tool", content: [{ type: "tool-result", toolCallId: "c1", toolName: "lookup", output: { type: "json", value: { row: value() } } }] },
        ],
        steps: [{ text: "done" }],
        pii,
      }, 0),
  },
  { id: "prompt-tool-input", run: (sdk, mode, value, pii = true) => aiSdkDirect(sdk, mode, { prompt: toolPrompt({ v: value() }, { type: "text", value: "ok" }) }, pii) },
  { id: "prompt-tool-json", run: (sdk, mode, value, pii = true) => aiSdkDirect(sdk, mode, { prompt: toolPrompt({}, { type: "json", value: { v: value() } }) }, pii) },
  { id: "prompt-tool-error-json", run: (sdk, mode, value, pii = true) => aiSdkDirect(sdk, mode, { prompt: toolPrompt({}, { type: "error-json", value: { v: value() } }) }, pii) },
  { id: "prompt-part-options", run: (sdk, mode, value, pii = true) => aiSdkDirect(sdk, mode, { prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { acme: { v: value() } } }] }] }, pii) },
  { id: "prompt-unknown-part", run: (sdk, mode, value, pii = true) => aiSdkDirect(sdk, mode, { prompt: [{ role: "user", content: [{ type: "text", text: "hi" }, { type: "x-later-part", v: value() }] }] }, pii) },
  { id: "call-provider-options", run: (sdk, mode, value, pii = true) => aiSdkDirect(sdk, mode, { prompt: [{ role: "user", content: [{ type: "text", text: "hi" }] }], providerOptions: { acme: { v: value() } } }, pii) },
];

for (const sdk of SDKS) {
  describe(`boundary: plain JSON and unsafe input (AI SDK ${sdk.name}, ai@${sdk.version})`, () => {
    for (const position of AI_SDK_PLAIN_POSITIONS) {
      for (const fixture of PLAIN_FIXTURES) {
        for (const mode of MODES) {
          it(`${position.id} / ${fixture.id} / ${mode} [${fixture.expect}]`, async () => {
            const o = await position.run(sdk, mode, fixture.make);
            const baseline = () => position.run(sdk, mode, fixture.make, false);
            none(await plainProblems(o, fixture.expect, aiSdkBinary, baseline, `ai@${sdk.version} ${position.id} / ${fixture.id} / ${mode}`, fixture.sent));
          });
        }
      }
    }
  });

  describe(`boundary: identifiers (AI SDK ${sdk.name}, ai@${sdk.version})`, () => {
    // Each identifier with the held ID planted at a place where it is not the framework's or a provider's: masked.
    const user = (extra: unknown[]) => [{ role: "user", content: [{ type: "text", text: "hi" }, ...extra] }];
    const plants: { id: string; options: () => Record<string, unknown> }[] = [
      { id: "tool-json", options: () => ({ prompt: toolPrompt({}, { type: "json", value: plantAll(ID) }) }) },
      { id: "tool-json-nested", options: () => ({ prompt: toolPrompt({}, { type: "json", value: { rows: [plantAll(ID)] } }) }) },
      { id: "tool-json-framed", options: () => ({ prompt: toolPrompt({}, { type: "json", value: plantFramed(ID) }) }) },
      { id: "tool-error-json", options: () => ({ prompt: toolPrompt({}, { type: "error-json", value: plantAll(ID) }) }) },
      { id: "tool-content-unknown-item", options: () => ({ prompt: toolPrompt({}, { type: "content", value: [{ type: "text", text: "ok" }, { ...plantAll(ID), type: "x-later-item" }] }) }) },
      { id: "tool-input", options: () => ({ prompt: toolPrompt(plantAll(ID), { type: "text", value: "ok" }) }) },
      { id: "tool-input-framed", options: () => ({ prompt: toolPrompt(plantFramed(ID), { type: "text", value: "ok" }) }) },
      { id: "unknown-part-user", options: () => ({ prompt: user([{ ...plantAll(ID), type: "x-later-part" }]) }) },
      { id: "unknown-part-assistant", options: () => ({ prompt: [...user([]), { role: "assistant", content: [{ type: "text", text: "ok" }, { ...plantAll(ID), type: "x-later-part" }] }] }) },
      { id: "part-options-unknown-provider", options: () => ({ prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { acme: plantAll(ID) } }] }] }) },
      { id: "message-options-unknown-provider", options: () => ({ prompt: [{ role: "user", content: [{ type: "text", text: "hi" }], providerOptions: { acme: plantAll(ID) } }] }) },
      { id: "part-options-metadata", options: () => ({ prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { openai: { metadata: plantAll(ID) } } }] }] }) },
      { id: "call-options-unknown-provider", options: () => ({ prompt: user([]), providerOptions: { acme: plantAll(ID) } }) },
      { id: "call-options-metadata", options: () => ({ prompt: user([]), providerOptions: { anthropic: { metadata: plantAll(ID) }, openai: { metadata: plantAll(ID) } } }) },
      { id: "tool-schema-examples", options: () => ({ prompt: user([]), tools: [{ type: "function", name: "lookup", description: "Look up", inputSchema: { type: "object", properties: { q: { type: "string" } }, examples: [plantAll(ID)] } }] }) },
      // (release gate 8, item 5: provider options named like a request's fields, extra_body, and a known provider's
      // fields one level down are not where a provider defines its identifiers)
      {
        id: "call-options-misplaced",
        options: () => ({
          prompt: user([]),
          providerOptions: {
            prompt: plantAll(ID),
            mcp_servers: { primary: plantAll(ID) },
            extra_body: { ...plantAll(ID), models: ID },
            gateway: { nested: plantAll(ID) },
            openai: { extra_body: plantAll(ID), nested: plantAll(ID) },
            anthropic: { nested: plantAll(ID), container: { nested: plantAll(ID) } },
          },
        }),
      },
      {
        id: "part-options-misplaced",
        options: () => ({ prompt: [{ role: "user", content: [{ type: "text", text: "hi", providerOptions: { openai: { nested: plantAll(ID) }, anthropic: { extra_body: plantAll(ID) } } }] }] }),
      },
      {
        id: "tool-args-misplaced",
        options: () => ({
          prompt: user([]),
          tools: [
            { type: "provider", id: "acme.lookup", name: "lookup", args: plantAll(ID) },
            { type: "provider", id: "openai.mcp", name: "mcp", args: { serverLabel: "s", serverUrl: "https://mcp.test", nested: plantAll(ID) } },
          ],
        }),
      },
    ];
    for (const plant of plants) {
      for (const mode of MODES) {
        it(`${plant.id} / ${mode}: every identifier planted there is masked`, async () => {
          const options = plant.options();
          const planted = pathsOf(aiSdkTree(options), ID);
          const o = await aiSdkDirect(sdk, mode, options);
          none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
          const tree = o.trees[0];
          // (keys are identifiers, not held values, so a planted path keeps its keys)
          const kept = planted.filter((p) => {
            const v = at(tree, p);
            return typeof v === "string" && v.toLowerCase().includes(ID);
          });
          none(kept.map((p) => `${show(p)} keeps the held value: ${JSON.stringify(at(tree, p))}`), "identifier planted outside its authorized path");
          none(nonPlain(tree, aiSdkBinary), "not plain JSON");
          none(leaks(tree), "held value sent");
        });
      }
    }

    // The same held ID at the paths where it is the framework's or a provider's identifier: kept as written, and
    // nothing else of the request holds a held value.
    it.each(MODES)("authorized paths keep the identifier as written (%s)", async (mode) => {
      const options = {
        prompt: [
          { role: "user", content: [{ type: "text", text: "Look it up." }, { type: "file", data: `https://files.test/${ID}/report.pdf`, mediaType: "application/pdf" }] },
          {
            role: "assistant",
            content: [
              { type: "reasoning", text: "thinking", providerOptions: { anthropic: { signature: ID } } },
              { type: "text", text: "Looking.", providerOptions: { openai: { itemId: ID } } },
              { type: "tool-call", toolCallId: ID, toolName: ID, input: { q: "x" }, providerOptions: { google: { thoughtSignature: ID } } },
            ],
          },
          { role: "tool", content: [{ type: "tool-result", toolCallId: ID, toolName: ID, output: { type: "text", value: "ok" } }] },
        ],
        tools: [{ type: "function", name: ID, description: "Look up", inputSchema: { type: "object", properties: { [ID]: { type: "string" } }, required: [ID] } }],
        providerOptions: { openai: { previousResponseId: ID } },
      };
      const keep: Path[] = [
        ["prompt", 0, "content", 1, "data"],
        ["prompt", 1, "content", 0, "providerOptions", "anthropic", "signature"],
        ["prompt", 1, "content", 1, "providerOptions", "openai", "itemId"],
        ["prompt", 1, "content", 2, "toolCallId"],
        ["prompt", 1, "content", 2, "toolName"],
        ["prompt", 1, "content", 2, "providerOptions", "google", "thoughtSignature"],
        ["prompt", 2, "content", 0, "toolCallId"],
        ["prompt", 2, "content", 0, "toolName"],
        ["tools", 0, "name"],
        ["tools", 0, "inputSchema", "required", 0],
        ["providerOptions", "openai", "previousResponseId"],
      ];
      const o = await aiSdkDirect(sdk, mode, options);
      none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
      const tree = o.trees[0] as any;
      const changed = keep.filter((p) => at(tree, p) !== at(aiSdkTree(options), p)).map((p) => `${show(p)}: ${JSON.stringify(at(tree, p))}`);
      none(changed, "identifier changed at its authorized path");
      expect(Object.keys(tree.tools[0].inputSchema.properties), "a property name is the provider's").toEqual([ID]);
      none(nonPlain(tree, aiSdkBinary), "not plain JSON");
      // (the property name above is kept as written at its authorized place: the key, not a value elsewhere)
      none(leaks(withoutKeys(without(tree, keep), [["tools", 0, "inputSchema", "properties", ID]])), "held value sent outside the authorized paths");
    });
  });

  describe(`boundary: schemas built once (AI SDK ${sdk.name}, ai@${sdk.version}; release gate 8)`, () => {
    const user = [{ role: "user", content: [{ type: "text", text: "hi" }] }];
    const positions: { id: string; run: (make: () => unknown, mode: Mode) => Promise<Outcome> }[] = [
      { id: "tool-input-schema", run: (make, mode) => aiSdkDirect(sdk, mode, { prompt: user, tools: [{ type: "function", name: "lookup", description: "Look up", inputSchema: make() }] }) },
      { id: "response-format", run: (make, mode) => aiSdkDirect(sdk, mode, { prompt: user, responseFormat: { type: "json", name: "answer", schema: make() } }) },
      { id: "input-examples", run: (make, mode) => aiSdkDirect(sdk, mode, { prompt: user, tools: [{ type: "function", name: "lookup", inputSchema: { type: "object", properties: {} }, inputExamples: [{ input: make() }] }] }) },
      {
        id: "generateText-tool",
        run: (make, mode) => aiSdkRun(sdk, mode, { prompt: "hi", tools: { lookup: sdk.ai.tool({ description: "Look up", inputSchema: sdk.ai.jsonSchema(make()), execute: async () => "ok" }) }, steps: [{ text: "done" }] }, 0),
      },
    ];
    for (const position of positions) {
      for (const fixture of SCHEMA_FIXTURES) {
        for (const mode of MODES) {
          it(`${position.id} / ${fixture.id} / ${mode}`, async () => {
            const o = await position.run(fixture.make, mode);
            none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
            for (const t of o.trees) {
              none(nonPlain(t, aiSdkBinary), "not plain JSON");
              none(leaks(t), "held value sent");
              // (written twice, the tree gives the same JSON: nothing in it is read again differently)
              expect(written(t)).toBe(written(t));
            }
            none(sentProblems(o, fixture.sent), "not what JSON writes");
          });
        }
      }
    }
  });

  for (const guaranteed of [true, false]) {
    describe(`boundary: readings, width folded after each decoding step (AI SDK ${sdk.name}, ai@${sdk.version}${guaranteed ? "" : "; three or more steps, best effort (D1)"})`, () => {
      const lookup = (result: () => unknown) => ({ lookup: sdk.ai.tool({ description: "Look up", inputSchema: sdk.ai.jsonSchema({ type: "object", properties: {} }), execute: async () => result() }) });
      for (const c of CASES.readings.filter((r) => r.guaranteed === guaranteed)) {
        for (const [detector, redactor] of DETECTORS) {
          for (const mode of MODES) {
            const name = (where: string) => `${c.id} [${c.steps.join(" > ")}] / ${where} / detector ${detector} / ${mode}`;
            it(name("user"), async () => {
              const o = await aiSdkRun(sdk, mode, { prompt: c.text, steps: [{ text: "done" }], redactor }, 0);
              none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
              const user = (o.trees[0] as any).prompt.find((m: any) => m.role === "user");
              const text = typeof user.content === "string" ? user.content : user.content.map((p: any) => p.text ?? "").join("");
              expect(text).toBe(c.expect);
            });
            it(name("tool text"), async () => {
              const o = await aiSdkRun(sdk, mode, { prompt: "Look it up.", tools: lookup(() => c.text), steps: [lookupStep(), { text: "done" }], redactor }, 1);
              none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
              const result = (o.trees[0] as any).prompt.flatMap((m: any) => (Array.isArray(m.content) ? m.content : [])).find((p: any) => p.type === "tool-result");
              expect(result.output).toEqual({ type: "text", value: c.expect });
            });
            it(name("tool JSON"), async () => {
              const o = await aiSdkRun(sdk, mode, { prompt: "Look it up.", tools: lookup(() => ({ mail: c.text, ok: 1 })), steps: [lookupStep(), { text: "done" }], redactor }, 1);
              none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
              const result = (o.trees[0] as any).prompt.flatMap((m: any) => (Array.isArray(m.content) ? m.content : [])).find((p: any) => p.type === "tool-result");
              expect(result.output).toEqual({ type: "json", value: { mail: c.expect, ok: 1 } });
            });
          }
        }
      }
    });
  }
}

// The checks see what is sent: without the middleware, the same inputs are reported (so a pass above is not vacuous).
describe("boundary: the checks themselves", () => {
  for (const sdk of SDKS) {
    it(`without the middleware every check reports (ai@${sdk.version})`, async () => {
      const options = { prompt: toolPrompt(plantAll(ID), { type: "json", value: { v: new Customer(PERSON, EMAIL), at: new Date(`${DOB}T00:00:00.000Z`) } }) };
      const o = await aiSdkDirect(sdk, "generate", options, false);
      expect(o.error).toBeUndefined();
      expect(leaks(o.trees[0]).sort()).toEqual([DOB, ID, PERSON, EMAIL].sort());
      expect(nonPlain(o.trees[0], aiSdkBinary).length).toBeGreaterThan(0);
      const r = CASES.readings.find((c) => c.id.startsWith("a4-percent-fold-percent@"))!;
      const plain = await aiSdkRun(sdk, "generate", { prompt: r.text, steps: [{ text: "done" }], pii: false }, 0);
      expect(JSON.stringify((plain.trees[0] as any).prompt)).not.toContain(r.expect);
    });
  }
  it("without the middleware every check reports (LangChain.js)", async () => {
    const messages = [asked(), calling(plantAll(ID)), answered([{ type: "json", json: { v: new Customer(PERSON, EMAIL), at: new Date(`${DOB}T00:00:00.000Z`) } }])];
    const o = await lcRun({ messages, script: [done()], pii: false }, 0);
    expect(o.error).toBeUndefined();
    expect(leaks(o.trees[0]).sort()).toEqual([DOB, ID, PERSON, EMAIL].sort());
    expect(nonPlain(o.trees[0], lcBinary).length).toBeGreaterThan(0);
    const r = CASES.readings.find((c) => c.id.startsWith("a4-percent-fold-percent@"))!;
    const plain = await lcRun({ messages: [new HumanMessage(r.text)], script: [done()], pii: false }, 0);
    expect((plain.trees[0] as { messages: { content: unknown }[] }).messages[0].content).toBe(r.text);
  });
});

describe.skipIf(SDKS.length > 0 || GATE)("boundary: AI SDK", () => {
  it.skip("skipped: set AI_SDK_DIR to a folder with ai@5 (ai5), ai@6 (ai) and ai@7 (ai7) installed (js/scripts/install-gate-ai-sdk.sh)", () => {});
});

// ---------------------------------------------------------------------------------------------------- LangChain.js

/** A chat model for createAgent that records what each call is given (the messages, and the settings the agent bound
 *  it with: the request's modelSettings) and answers with the script in order. The state is one object, so a copy of
 *  the model (the middleware binds a copy) records into the same place. */
class Recorder extends FakeListChatModel {
  state: { seen: BaseMessage[][]; settings: unknown[]; bound: unknown; script: AIMessage[]; i: number } = { seen: [], settings: [], bound: undefined, script: [], i: 0 };
  async _generate(messages: BaseMessage[]): Promise<any> {
    this.state.seen.push(messages);
    this.state.settings.push(this.state.bound);
    const m = this.state.script[Math.min(this.state.i++, this.state.script.length - 1)];
    return { generations: [{ text: typeof m.content === "string" ? m.content : "", message: m }] };
  }
  bindTools(_tools: unknown, options?: Record<string, unknown>): any {
    // (the agent binds the request's modelSettings here; tool_choice is the agent's)
    const { tool_choice: _choice, ...settings } = options ?? {};
    this.state.bound = settings;
    return this; // (the stock fake returns a new instance, which would drop this subclass)
  }
}

/** A recording model shaped like ChatOpenAI and ChatAnthropic (release gate 8 on f25d519, item 2): what it sends
 *  besides the messages is its request parameters, made from its own fields (`user`, `modelKwargs` spread into them,
 *  `invocationKwargs` spread once more as it sends), reported once for tracing and once for the request, as
 *  LangChain.js does; the request's parameters are recorded as the settings. */
class FieldRecorder extends Recorder {
  user: unknown = undefined;
  modelKwargs: Record<string, unknown> = {};
  invocationKwargs: Record<string, unknown> = {};
  invocationParams(): any {
    return { model: "fake", user: this.user, ...this.modelKwargs };
  }
  async _generate(messages: BaseMessage[]): Promise<any> {
    this.invocationParams(); // (tracing)
    this.state.bound = { ...this.invocationParams(), ...this.invocationKwargs };
    return super._generate(messages);
  }
  bindTools(): any {
    return this;
  }
}

/** The fields of a LangChain message that providers are sent. */
const lcSent = (m: BaseMessage) => ({
  type: m.getType(),
  content: m.content,
  name: m.name,
  tool_calls: (m as AIMessage).tool_calls,
  invalid_tool_calls: (m as AIMessage).invalid_tool_calls,
  additional_kwargs: m.additional_kwargs,
  tool_call_id: (m as ToolMessage).tool_call_id,
});
/** The request tree of one LangChain.js call: the messages as providers are sent them, and the bound model settings. */
const lcTree = (messages: BaseMessage[], settings?: unknown) => ({ messages: messages.map(lcSent), settings });
/** Binary data in a LangChain message: the data of a file block (bytes). */
const lcBinary = (path: Path, parent: unknown) => {
  const type = (parent as { type?: unknown } | null)?.type;
  return /^messages\.\d+\.content\.\d+\.data$/.test(show(path)) && (type === "image" || type === "file" || type === "audio" || type === "video");
};

/** One createAgent run: `messages` are the input state, `script` the model's answers, `from` the index of the first
 *  model call that holds the planted input. `fields`: the model's own fields (a FieldRecorder), whose request parameters
 *  are checked as the settings, and which the middleware is given as they are. */
async function lcRun(
  args: { messages: BaseMessage[]; tools?: unknown[]; script: AIMessage[]; settings?: () => Record<string, unknown>; fields?: () => Record<string, unknown>; redactor?: Redactor; pii?: boolean },
  from: number,
): Promise<Outcome & { seen: BaseMessage[][] }> {
  const own = args.fields?.();
  const model = own ? Object.assign(new FieldRecorder({ responses: ["x"] }), own) : new Recorder({ responses: ["x"] });
  model.state.script = args.script;
  const state = { entered: 0, given: [] as unknown[] };
  // the application's model settings, set by a middleware outside this one (what an application's own middleware does)
  const settings = createMiddleware({
    name: "BoundarySettings",
    wrapModelCall: async (request, handler) => handler(args.settings ? ({ ...request, modelSettings: { ...(request as { modelSettings?: object }).modelSettings, ...args.settings() } } as never) : request),
  });
  const spy = createMiddleware({
    name: "BoundarySpy",
    wrapModelCall: async (request, handler) => {
      state.entered++;
      state.given.push(lcTree(request.messages, (request as { modelSettings?: unknown }).modelSettings));
      return handler(request);
    },
  });
  const pii = langchainMiddleware({ redactor: args.redactor ?? finder(ALL_HELD), vault: vaultOf(ALL_HELD) });
  const agent = createAgent({ model, tools: (args.tools ?? []) as never, middleware: args.pii === false ? [settings, spy] : [settings, spy, pii] });
  let error: unknown;
  try {
    await agent.invoke({ messages: args.messages }, { recursionLimit: 12 });
  } catch (e) {
    error = e;
  }
  const after = model.state.seen.slice(from);
  const trees = after.map((ms, i) => lcTree(ms, args.settings || own ? model.state.settings[from + i] : undefined));
  // (the model's own fields are given to the middleware as the application wrote them)
  const given = state.given.slice(from).map((g) => (own ? { ...(g as object), settings: own } : args.settings ? g : { ...(g as object), settings: undefined }));
  return { sent: after, trees, given, error, entered: state.entered > from, seen: model.state.seen };
}

const asked = () => new HumanMessage("Look it up.");
const calling = (args: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) => new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args }], ...extra });
const answered = (content: unknown, extra: Record<string, unknown> = {}) => new ToolMessage({ tool_call_id: "c1", name: "lookup", content: content as never, ...extra });
const done = () => new AIMessage("done");

/** A block a tool returns that is a class instance (24th outside review, A2: a real tool returned one). */
class JsonBlock {
  type = "json";
  constructor(public json: unknown) {}
}

const LC_PLAIN_POSITIONS: { id: string; run: (value: () => unknown, pii?: boolean) => ReturnType<typeof lcRun> }[] = [
  {
    id: "tool-return",
    run: (value, pii = true) => lcRun({ messages: [asked()], tools: [tool(async () => value() as never, { name: "lookup", description: "Look up", schema: z.object({}) })], script: [calling(), done()], pii }, 1),
  },
  {
    id: "tool-return-block-list",
    run: (value, pii = true) => lcRun({ messages: [asked()], tools: [tool(async () => [new JsonBlock({ v: value() })] as never, { name: "lookup", description: "Look up", schema: z.object({}) })], script: [calling(), done()], pii }, 1),
  },
  {
    id: "tool-return-typed-rows",
    run: (value, pii = true) => lcRun({ messages: [asked()], tools: [tool(async () => [{ type: "contact", v: value() }] as never, { name: "lookup", description: "Look up", schema: z.object({}) })], script: [calling(), done()], pii }, 1),
  },
  { id: "block-json", run: (value, pii = true) => lcRun({ messages: [asked(), calling(), answered([{ type: "json", json: { v: value() } }])], script: [done()], pii }, 0) },
  { id: "block-class", run: (value, pii = true) => lcRun({ messages: [asked(), calling(), answered([new JsonBlock({ v: value() })])], script: [done()], pii }, 0) },
  { id: "tool-call-args", run: (value, pii = true) => lcRun({ messages: [asked(), calling({ v: value() }), answered("ok")], script: [done()], pii }, 0) },
  { id: "additional-kwargs", run: (value, pii = true) => lcRun({ messages: [asked(), calling({}, { additional_kwargs: { crm: value() } }), answered("ok")], script: [done()], pii }, 0) },
  { id: "human-block-unknown", run: (value, pii = true) => lcRun({ messages: [new HumanMessage({ content: [{ type: "text", text: "hi" }, { type: "x-later-block", v: value() }] as never })], script: [done()], pii }, 0) },
  { id: "model-settings-metadata", run: (value, pii = true) => lcRun({ messages: [asked()], settings: () => ({ metadata: { crm: value() } }), script: [done()], pii }, 0) },
  { id: "model-settings-unknown", run: (value, pii = true) => lcRun({ messages: [asked()], settings: () => ({ acme: { v: value() } }), script: [done()], pii }, 0) },
  // the model's own fields (release gate 8 on f25d519, item 2): read once into the JSON the provider writes, and that
  // is sent, whether or not anything in it was masked
  { id: "model-kwargs", run: (value, pii = true) => lcRun({ messages: [asked()], fields: () => ({ modelKwargs: { metadata: { crm: value() } } }), script: [done()], pii }, 0) },
  { id: "model-kwargs-value", run: (value, pii = true) => lcRun({ messages: [asked()], fields: () => ({ modelKwargs: { metadata: value() } }), script: [done()], pii }, 0) },
  { id: "model-invocation-kwargs", run: (value, pii = true) => lcRun({ messages: [asked()], fields: () => ({ invocationKwargs: { metadata: { crm: value() } } }), script: [done()], pii }, 0) },
  { id: "model-user", run: (value, pii = true) => lcRun({ messages: [asked()], fields: () => ({ user: value() }), script: [done()], pii }, 0) },
];

describe("boundary: plain JSON and unsafe input (LangChain.js createAgent)", () => {
  for (const position of LC_PLAIN_POSITIONS) {
    for (const fixture of PLAIN_FIXTURES) {
      it(`${position.id} / ${fixture.id} [${fixture.expect}]`, async () => {
        const o = await position.run(fixture.make);
        none(await plainProblems(o, fixture.expect, lcBinary, () => position.run(fixture.make, false), `langchain ${position.id} / ${fixture.id}`, fixture.sent));
      });
    }
  }
});

describe("boundary: identifiers (LangChain.js createAgent)", () => {
  // Each identifier with the held ID planted where it is not LangChain's or a provider's: masked.
  const plants: { id: string; messages: () => BaseMessage[]; settings?: () => Record<string, unknown>; fields?: () => Record<string, unknown> }[] = [
    { id: "tool-call-args", messages: () => [asked(), calling(plantAll(ID)), answered("ok")] },
    { id: "tool-call-args-framed", messages: () => [asked(), calling(plantFramed(ID)), answered("ok")] },
    { id: "block-json", messages: () => [asked(), calling(), answered([{ type: "json", json: plantAll(ID) }])] },
    { id: "block-json-framed", messages: () => [asked(), calling(), answered([{ type: "json", json: plantFramed(ID) }])] },
    { id: "typed-rows", messages: () => [asked(), calling(), answered([{ ...plantAll(ID), type: "contact" }])] },
    { id: "rows-without-type", messages: () => [asked(), calling(), answered([plantAll(ID, ["type"])])] },
    { id: "tool-use-input", messages: () => [asked(), new AIMessage({ content: [{ type: "text", text: "x" }, { type: "tool_use", id: "c2", name: "lookup", input: plantAll(ID) }] as never }), new HumanMessage("ok")] },
    { id: "tool-result-block-content", messages: () => [asked(), calling(), answered([{ type: "tool_result", tool_use_id: "c1", content: [{ type: "json", json: plantAll(ID) }] }])] },
    { id: "kwargs-top", messages: () => [asked(), calling({}, { additional_kwargs: plantAll(ID, ["tool_calls", "function_call"]) }), answered("ok")] },
    { id: "kwargs-nested", messages: () => [asked(), calling({}, { additional_kwargs: { crm: plantAll(ID) } }), answered("ok")] },
    { id: "human-block-unknown", messages: () => [new HumanMessage({ content: [{ type: "text", text: "hi" }, { ...plantAll(ID), type: "x-later-block" }] as never })] },
    { id: "tool-message-kwargs", messages: () => [asked(), calling(), answered("ok", { additional_kwargs: plantAll(ID, ["tool_calls", "function_call"]) })] },
    { id: "settings-metadata", messages: () => [asked()], settings: () => ({ metadata: plantAll(ID) }) },
    { id: "settings-unknown-provider", messages: () => [asked()], settings: () => ({ acme: plantAll(ID) }) },
    { id: "settings-extra-body", messages: () => [asked()], settings: () => ({ extra_body: { acme: plantAll(ID) } }) },
    { id: "settings-variables", messages: () => [asked()], settings: () => ({ prompt: { id: "pmpt_1", variables: plantAll(ID) } }) },
    // (release gate 8, item 5: extra_body is data, a gateway's routing names in it too, and a request field's
    // identifiers one level down are not where a provider defines them)
    { id: "settings-extra-body-routing", messages: () => [asked()], settings: () => ({ extra_body: { provider: plantAll(ID), models: [ID], model: ID } }) },
    { id: "settings-nested", messages: () => [asked()], settings: () => ({ reasoning: plantAll(ID), prompt: { nested: plantAll(ID) }, mcp_servers: [{ name: "s", url: "https://mcp.test", nested: plantAll(ID) }] }) },
    // the model's own fields (release gate 8 on f25d519, item 2), read as model settings are
    { id: "model-kwargs-metadata", messages: () => [asked()], fields: () => ({ modelKwargs: { metadata: plantAll(ID) } }) },
    { id: "model-kwargs-unknown", messages: () => [asked()], fields: () => ({ modelKwargs: { acme: plantAll(ID), extra_body: { acme: plantAll(ID) } } }) },
    { id: "model-invocation-kwargs", messages: () => [asked()], fields: () => ({ invocationKwargs: { metadata: plantAll(ID) } }) },
  ];
  for (const plant of plants) {
    it(`${plant.id}: every identifier planted there is masked`, async () => {
      const messages = plant.messages();
      // (the model's own fields reach the request as its parameters: model and user first, then modelKwargs spread,
      // then invocationKwargs spread again)
      const own = plant.fields?.();
      const params = own && { model: "fake", ...(own.modelKwargs as object), ...(own.invocationKwargs as object) };
      const before = lcTree(messages, plant.settings?.() ?? params);
      const planted = pathsOf(before, ID);
      const o = await lcRun({ messages, script: [done()], settings: plant.settings, fields: plant.fields }, 0);
      none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
      const tree = o.trees[0];
      const kept = planted.filter((p) => {
        const v = at(tree, p);
        return typeof v === "string" && v.toLowerCase().includes(ID);
      });
      none(kept.map((p) => `${show(p)} keeps the held value: ${JSON.stringify(at(tree, p))}`), "identifier planted outside its authorized path");
      none(nonPlain(tree, lcBinary), "not plain JSON");
      none(leaks(tree), "held value sent");
    });
  }

  it("authorized paths keep the identifier as written", async () => {
    const messages = [
      new HumanMessage({
        content: [
          { type: "text", text: "Look it up.", id: ID },
          { type: "image", url: `https://files.test/${ID}/a.png` },
          { type: "image_url", image_url: { url: `https://files.test/${ID}/b.png` } },
          { type: "file", file_id: ID },
        ] as never,
      }),
      new AIMessage({
        content: [{ type: "text", text: "Looking." }, { type: "tool_use", id: ID, name: ID, input: { q: "x" } }] as never,
        tool_calls: [{ id: ID, name: ID, args: { q: "x" } }],
        additional_kwargs: { tool_calls: [{ id: ID, type: "function", function: { name: ID, arguments: '{"q":"x"}' } }] },
      }),
      new ToolMessage({ tool_call_id: ID, name: ID, content: "ok" }),
    ];
    const keep: Path[] = [
      ["messages", 0, "content", 0, "id"],
      ["messages", 0, "content", 1, "url"],
      ["messages", 0, "content", 2, "image_url", "url"],
      ["messages", 0, "content", 3, "file_id"],
      ["messages", 1, "content", 1, "id"],
      ["messages", 1, "content", 1, "name"],
      ["messages", 1, "tool_calls", 0, "id"],
      ["messages", 1, "tool_calls", 0, "name"],
      ["messages", 1, "additional_kwargs", "tool_calls", 0, "id"],
      ["messages", 1, "additional_kwargs", "tool_calls", 0, "function", "name"],
      ["messages", 2, "tool_call_id"],
      ["messages", 2, "name"],
      ["settings", "previous_response_id"],
      ["settings", "prompt", "id"],
    ];
    const settings = () => ({ previous_response_id: ID, prompt: { id: ID } });
    const before = lcTree(messages, settings());
    const o = await lcRun({ messages, script: [done()], settings }, 0);
    none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
    const tree = o.trees[0];
    const changed = keep.filter((p) => at(tree, p) !== at(before, p)).map((p) => `${show(p)}: ${JSON.stringify(at(tree, p))}`);
    none(changed, "identifier changed at its authorized path");
    none(nonPlain(tree, lcBinary), "not plain JSON");
    none(leaks(without(tree, keep)), "held value sent outside the authorized paths");
  });
});

// Schemas in LangChain.js (release gate 8, items 3 and 4): what the agent binds the model with (its tools as a provider
// adapter writes them, and the settings of a response format) is plain JSON without the held value.
class BindingRecorder extends Recorder {
  bindTools(tools: unknown, options?: Record<string, unknown>): any {
    (this.state as unknown as { tools: unknown }).tools = tools;
    return super.bindTools(tools, options);
  }
}
class SchemaDefinition {
  type = "function";
  constructor(public function_: unknown) {}
  toJSON() {
    return { type: this.type, function: this.function_ };
  }
}
describe("boundary: schemas built once (LangChain.js createAgent; release gate 8)", () => {
  const positions: { id: string; args: (make: () => unknown) => { tools?: unknown[]; responseFormat?: unknown } }[] = [
    { id: "tool-schema", args: (make) => ({ tools: [tool(async () => "ok", { name: "lookup", description: "Look up", schema: make() as never })] }) },
    { id: "definition", args: (make) => ({ tools: [{ type: "function", function: { name: "lookup", description: "Look up", parameters: make() } }] }) },
    { id: "class-definition", args: (make) => ({ tools: [new SchemaDefinition({ name: "lookup", description: "Look up", parameters: make() })] }) },
    { id: "tool-strategy", args: (make) => ({ responseFormat: toolStrategy(make() as never) }) },
    { id: "provider-strategy", args: (make) => ({ responseFormat: providerStrategy(make() as never) }) },
  ];
  for (const position of positions) {
    for (const fixture of SCHEMA_FIXTURES) {
      it(`${position.id} / ${fixture.id}`, async () => {
        const model = new BindingRecorder({ responses: ["x"] });
        model.state.script = [done()];
        const pii = langchainMiddleware({ redactor: finder(ALL_HELD), vault: vaultOf(ALL_HELD) });
        const { tools, responseFormat } = position.args(fixture.make);
        const agent = createAgent({ model, tools: (tools ?? []) as never, responseFormat: responseFormat as never, middleware: [pii] });
        try {
          await agent.invoke({ messages: [asked()] }, { recursionLimit: 4 });
        } catch {
          // (a fake model's text is no structured response: only the first call's binding is checked)
        }
        const state = model.state as unknown as { tools?: unknown[]; bound: unknown; seen: unknown[] };
        expect(state.seen.length, "the model was called").toBeGreaterThan(0);
        const tree = { tools: (state.tools ?? []).map((t) => convertToOpenAITool(t as never)), settings: state.bound };
        none(nonPlain(tree), "not plain JSON");
        none(leaks(tree), "held value sent");
        expect(written(tree)).toBe(written(tree));
        none(sentProblems({ trees: [tree] }, fixture.sent), "not what JSON writes");
      });
    }
  }
});

for (const guaranteed of [true, false]) {
  describe(`boundary: readings, width folded after each decoding step (LangChain.js createAgent${guaranteed ? "" : "; three or more steps, best effort (D1)"})`, () => {
    const lookup = (result: () => unknown) => [tool(async () => result() as never, { name: "lookup", description: "Look up", schema: z.object({}) })];
    for (const c of CASES.readings.filter((r) => r.guaranteed === guaranteed)) {
      for (const [detector, redactor] of DETECTORS) {
        const name = (where: string) => `${c.id} [${c.steps.join(" > ")}] / ${where} / detector ${detector}`;
        const messagesOf = (o: Outcome) => (o.trees[0] as { messages: any[] }).messages;
        it(name("user"), async () => {
          const o = await lcRun({ messages: [new HumanMessage(c.text)], script: [done()], redactor }, 0);
          none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
          expect(messagesOf(o)[0].content).toBe(c.expect);
        });
        it(name("tool text"), async () => {
          const o = await lcRun({ messages: [asked()], tools: lookup(() => c.text), script: [calling(), done()], redactor }, 1);
          none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
          expect(messagesOf(o).find((m) => m.type === "tool").content).toBe(c.expect);
        });
        it(name("tool JSON"), async () => {
          const o = await lcRun({ messages: [asked()], tools: lookup(() => ({ mail: c.text, ok: 1 })), script: [calling(), done()], redactor }, 1);
          none(o.error === undefined ? [] : [describeError(o.error)], "the call failed");
          const content = messagesOf(o).find((m) => m.type === "tool").content;
          expect(JSON.parse(content)).toEqual({ mail: c.expect, ok: 1 });
        });
      }
    }
  });
}
