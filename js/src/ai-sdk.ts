// Vercel AI SDK: a language model middleware that masks personal information in the prompt before the model call.
//
//   import { wrapLanguageModel, generateText } from "ai";
//   import { piiMiddleware } from "@newmean-ai/pii/ai-sdk";
//
//   const model = wrapLanguageModel({ model: openai("gpt-6.1"), middleware: piiMiddleware() });
//   await generateText({ model, prompt: "Call Kim Minsu on 010-1234-5678" });  // the provider gets "Call [PERSON] on [PHONE]"
//
// With a vault (since 1.7.2) the provider gets numbered placeholders ("Call [PERSON_1] on [PHONE_1]") and the middleware
// puts the values back in the answer's text, streamed or not. Only placeholders this middleware put into that call's
// prompt are restored. Tool-call arguments are restored only for the tools named in `restoreTools`, so text the model
// read (a retrieved page, a tool result) cannot make it send the values to some other tool. What the model wrote is
// remembered in the vault and sent again on the next call instead of the restored text, and is itself masked again:
//
//   const vault = createVault();   // one per user or conversation
//   const middleware = piiMiddleware({ redactor, vault, restoreTools: ["lookup_customer"] });
//
// The `ai` package is not a dependency: the middleware is a plain object in the shape `wrapLanguageModel` takes, and the
// prompt and results are read structurally (messages with a role and a string or an array of parts; content parts and
// stream parts by their `type`), the same in provider specifications v2, v3 and v4.
import { AI_SDK_MESSAGE, AI_SDK_SHAPES, createMasker, plainTree, recordTags, relaxedSchema, storedAs, toolTest, type MaskContext, type MaskerOptions } from "./masker.js";
import { vaultUnchanged } from "./redactor.js";
import { jsonDocument, restoreAnswer, restoreJson, type VaultRestorer } from "./vault.js";

export type PiiRole = "system" | "user" | "assistant" | "tool";

export interface PiiMiddlewareOptions extends MaskerOptions {
  /** Message roles to mask. Default `["user"]`, or `["user", "assistant", "tool"]` with a vault (earlier answers and
   *  tool results hold the restored values). "tool" masks tool results (text, JSON strings, text content items);
   *  "assistant" masks earlier answers' text and reasoning (with a vault also the strings of their tool-call inputs);
   *  "system" masks system messages. With a vault, roles left out still get the vault's known values hidden. */
  roles?: readonly PiiRole[];
  /** With a vault: tools whose call arguments get the values back before they run, by name or by a test. Default none:
   *  tools receive the placeholders. Name only tools that may see the values; a prompt injection in text the model reads
   *  can make the model put placeholders into any tool call. */
  restoreTools?: string | readonly string[] | ((toolName: string) => boolean);
  /** With a vault: also put the values back in the model's reasoning text. Default false (reasoning is often logged or
   *  traced). */
  restoreReasoning?: boolean;
  /** The middleware's `specificationVersion`. Default "v3" (AI SDK 6 and 7); pass "v2" for AI SDK 5. */
  specificationVersion?: string;
}

type StreamPart = { type?: unknown; id?: unknown; delta?: unknown; input?: unknown; toolName?: unknown };
type ContentPart = { type?: unknown; text?: unknown; input?: unknown; toolName?: unknown };

export interface PiiMiddleware<V extends string = "v3"> {
  readonly specificationVersion: V;
  /** Returns the call parameters with the chosen roles' text masked. The input is not changed. `model`: the wrapped model
   *  (`wrapLanguageModel` gives it), whose adapter decides which option fields take only strings. */
  transformParams<P>(options: { params: P; model?: unknown }): Promise<P>;
  /** With a vault: the generated text (and tool-call inputs of `restoreTools`) with this call's placeholders put back. */
  wrapGenerate?<R>(options: { doGenerate: () => PromiseLike<R>; params?: unknown }): Promise<R>;
  /** The same for a stream; a placeholder split across deltas is held back until it is complete. */
  wrapStream?<R>(options: { doStream: () => PromiseLike<R>; params?: unknown }): Promise<R>;
}

type Part = {
  type?: unknown;
  text?: unknown;
  input?: unknown;
  toolCallId?: unknown;
  reason?: unknown;
  data?: unknown;
  output?: { type?: unknown; value?: unknown; reason?: unknown };
};
type Message = { role?: unknown; content?: unknown };
type Call = { generation: number; values: ReadonlyMap<string, string>; json?: boolean };

/** Key under which the vault remembers a tool call's input as the model wrote it. */
// (U+E000, a private-use character, keeps these keys apart from answer texts; a control character would not survive
// every JSON store: PostgreSQL jsonb refuses U+0000)
const toolKey = (id: unknown) => (typeof id === "string" ? `\ue000tool-call:${id}` : undefined);
const parseJson = (s: unknown) => {
  if (typeof s !== "string") return s;
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
};

// The fields of the call's provider options that the AI SDK's own adapters take only as strings, where an application
// puts its own values (a user id, a cache key, metadata), by the key of the options the adapter reads: a number there
// makes the adapter refuse the call, so a known number is not turned into a placeholder string there; the call fails
// instead (the 1.7.1 release gate: OpenAI Chat Completions refuses a number in `metadata`, and with its phone number
// masked the request was sent). `endpoints`: only for the adapters whose `provider` id matches (OpenAI Responses takes
// any JSON in `metadata`). "*": any key.
const OPENAI_STRINGS = [{ paths: [["user"], ["safetyIdentifier"], ["promptCacheKey"]] }, { paths: [["metadata", "*"]], endpoints: /\.(chat|completion)$/ }];
const ADAPTER_STRINGS: ReadonlyMap<string, readonly { paths: string[][]; endpoints?: RegExp }[]> = new Map(Object.entries({
  openai: OPENAI_STRINGS,
  azure: OPENAI_STRINGS,
  anthropic: [{ paths: [["metadata", "userId"]] }],
}));
/** Whether the value at `path` of the call's provider options (from the provider's key) is a field the call's own
 *  adapter takes only as a string: the adapter reads the options under its provider id's first part (and OpenAI's
 *  adapters those under "openai" as well). False when the call's model is not known. */
const adapterStrings = (model: unknown) => {
  const id = (model as { provider?: unknown } | null | undefined)?.provider;
  if (typeof id !== "string") return undefined;
  const own = id.split(".")[0].trim();
  const reads = new Set([own, ...(own === "azure" || own === "openai" ? ["openai"] : [])]);
  return (path: readonly string[]) =>
    reads.has(path[0]) &&
    (ADAPTER_STRINGS.get(path[0]) ?? []).some(
      (f) => (!f.endpoints || f.endpoints.test(id)) && f.paths.some((p) => p.length === path.length - 1 && p.every((k, i) => k === "*" || k === path[i + 1])),
    );
};

export function piiMiddleware<V extends string = "v3">(options: PiiMiddlewareOptions & { specificationVersion?: V } = {}): PiiMiddleware<V> {
  const { vault } = options;
  const roles = new Set<string>(options.roles ?? (vault ? ["user", "assistant", "tool"] : ["user"]));
  const masker = createMasker(options);
  const restoresTool = toolTest(options.restoreTools);
  // the vault generation and the placeholders (with their values at masking time) of each transformed params object, for
  // wrapGenerate / wrapStream of the same call; a params object this middleware did not produce restores nothing
  const issued = new WeakMap<object, Call>();

  const maskMessage = async (message: Message, call: MaskContext): Promise<Message> => {
    if (message === null || typeof message !== "object") return message;
    const detect = roles.has(message.role as string);
    // with a vault, roles left out of `roles` still get the known values hidden (restored answers come back that way)
    if (!detect && !vault) return message;
    const model = message.role === "assistant";
    const system = message.role === "system";
    const c: MaskContext = model ? { ...call, model: true } : system ? { ...call, system: true } : call;
    // text a model wrote, or the developer's (a system prompt); text that came from outside (a JSON document is read
    // value by value)
    const wrote = async (s: string) => (detect ? masker.mask(s, c) : masker.hide(s, c));
    const got = (s: string) => masker.received(s, call, detect);
    const str = model || system ? wrote : got;
    const part = async (p: Part): Promise<Part> => {
      if (p === null || typeof p !== "object") return p;
      if (p.type === "reasoning" && typeof p.text === "string" && vault && model) {
        // reasoning goes back exactly as the model wrote it when the middleware has its record: providers sign it,
        // and a changed text is refused
        const written = vault.recall(p.text);
        if (typeof written === "string") {
          // (the placeholders restoreReasoning put back in it are this call's own, as an answer's are)
          if (options.restoreReasoning) for (const t of recordTags(vault, written, p.text, "text")) call.tags?.add(t);
          return written === p.text ? p : { ...p, text: written };
        }
      }
      if ((p.type === "text" || p.type === "reasoning") && typeof p.text === "string") return { ...p, text: await str(p.text) };
      // a file given as inline text (AI SDK 7: data { type: "text", text }), in a message or in a tool result
      const inline = p.data as { type?: unknown; text?: unknown } | null | undefined;
      if (inline && typeof inline === "object" && inline.type === "text" && typeof inline.text === "string") return { ...p, data: { ...inline, text: await got(inline.text) } };
      const o = p.output;
      if ((p.type === "tool-result" || p.type === "tool-error") && o && typeof o === "object") {
        // what a tool returned is read as received, also inside an assistant message (provider-executed tools)
        if ((o.type === "text" || o.type === "error-text") && typeof o.value === "string") return { ...p, output: { ...o, value: await got(o.value) } };
        if (o.type === "json" || o.type === "error-json") return { ...p, output: { ...o, value: await masker.data(o.value, call, detect) } };
        // why a person refused a tool call: their words, read as received
        if (o.type === "execution-denied" && typeof o.reason === "string") return { ...p, output: { ...o, reason: await got(o.reason) } };
        if (o.type === "content" && Array.isArray(o.value)) {
          const item = async (x: Part) => (x?.type === "text" && typeof x.text === "string" ? { ...x, text: await got(x.text) } : part(x));
          return { ...p, output: { ...o, value: await Promise.all(o.value.map(item)) } };
        }
      }
      if (p.type === "tool-approval-response" && typeof p.reason === "string") return { ...p, reason: await got(p.reason) };
      if (vault && p.type === "tool-call" && p.input !== undefined) {
        // the input as the model wrote it, when this middleware restored it for the tool; either way its strings are
        // masked again before they go back to the provider
        const key = toolKey(p.toolCallId);
        const written = key !== undefined ? vault.recall(key) : undefined;
        // (the placeholders restoreTools put back in the input are this call's own, as an answer's are)
        if (written !== undefined) for (const t of recordTags(vault, written, parseJson(p.input), "value")) call.tags?.add(t);
        const input = written !== undefined ? masker.value(written, { ...c, model: true, recall: false }, detect) : masker.value(p.input, { ...c, model: true }, detect);
        return { ...p, input: await input };
      }
      return p;
    };
    const { content } = message;
    if (typeof content === "string") return { ...message, content: await str(content) };
    if (Array.isArray(content)) return { ...message, content: await Promise.all(content.map(part)) };
    return message;
  };

  /** The fields of a call outside the prompt that hold free text (since 1.7.2): tool descriptions, input schemas
   *  (descriptions, titles, examples) and input examples, provider tools' arguments, the response format's description
   *  and schema, and string values of provider options (an `openai.user`), of the call and of each tool. The known
   *  values are hidden without the model. Tool names, property names, ids and signatures stay; so do `headers`, which
   *  carry credentials. Only the fields that changed are returned. */
  const callFields = (params: Record<string, unknown> | null, c: MaskContext, model?: unknown): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    if (params === null || typeof params !== "object") return out;
    // (whether the messages gave the model placeholders, which it may write into a JSON answer)
    const placeholders = c.tags !== undefined && c.tags.size > 0;
    const text = (x: unknown) => masker.options(x, c); // a string, or provider options
    const schema = (x: unknown) => masker.schema(x, c);
    // (each field read once; one the object does not store as what was read, a getter or a Proxy's trap, would give the
    // provider's adapter another value when it reads it again, so the object is copied with what was read and masked;
    // the confirmation of release gate 8)
    const fields = (x: unknown, f: Record<string, (y: unknown) => unknown>): unknown => {
      if (x === null || typeof x !== "object" || Array.isArray(x)) return x;
      const o = x as Record<string, unknown>;
      let next = o;
      for (const [k, g] of Object.entries(f)) {
        const given = o[k];
        if (given === undefined) continue;
        const y = g(given);
        if (y !== given || !storedAs(o, k, given)) next = { ...next, [k]: y };
      }
      return next;
    };
    const examples = (x: unknown) => masker.schema({ examples: x }, c).examples;
    // a provider tool's arguments ({ type: "provider", id: "openai.file_search", args }): data, except the identifiers
    // that tool defines, at their paths (the tool's id without its date: "anthropic.web_search_20250305" is
    // "anthropic.web_search"); every argument of a tool the middleware does not know is data
    const args = (id: unknown) => (x: unknown) => {
      const kind = typeof id === "string" ? id.replace(/_\d{8}$/, "") : "";
      const wrapped = { [kind]: x };
      const out = masker.options(wrapped, c, false, "tools");
      return out === wrapped ? x : out[kind];
    };
    const tool = (t: unknown) =>
      fields(t, { description: text, inputSchema: schema, inputExamples: examples, providerOptions: text, args: args((t as { id?: unknown } | null)?.id) });
    const { tools, responseFormat, providerOptions } = params;
    if (Array.isArray(tools)) {
      // (each tool read once; a list that does not store what was read, a Proxy or a getter at an index, is sent as the
      // list of what was read)
      const given = tools.map((t) => t);
      const masked = given.map(tool);
      if (masked.some((t, i) => t !== given[i]) || given.some((t, i) => !storedAs(tools, String(i), t))) out.tools = masked;
    }
    let format = fields(responseFormat, { description: text, schema });
    // (where the call's own adapter takes only a string, a known number fails the call: adapterStrings)
    const options = masker.options(providerOptions, c, false, "providers", adapterStrings(model));
    if (options !== providerOptions) out.providerOptions = options;
    // With placeholders in the messages, the schema of a JSON answer goes without the constraints a placeholder cannot
    // meet (`format`, `pattern`: a date or an e-mail field), so the provider does not make the model write a value in
    // their place; the AI SDK checks the answer against the original schema after its values are back (the 1.7.1
    // review round, L3-B5)
    if (placeholders) format = fields(format, { schema: relaxedSchema });
    if (format !== responseFormat) out.responseFormat = format;
    return out;
  };

  const middleware: PiiMiddleware<V> = {
    specificationVersion: (options.specificationVersion ?? "v3") as V,
    async transformParams<P>({ params, model }: { params: P; model?: unknown }): Promise<P> {
      const given = (params as { prompt?: unknown } | null)?.prompt;
      if (!Array.isArray(given)) return params;
      // With a vault, each message is built once into the plain JSON tree its provider writes (release gate 4 of 1.7.1):
      // a class instance, a Date, a toJSON object (its toJSON called once), Map, Set, NaN and bytes in data become what
      // JSON writes, and that tree is masked and sent; a file's data (bytes, a URL object) stays where the AI SDK has
      // it. Input JSON cannot write (a BigInt, an object that refers to itself, a toJSON or a getter that throws) fails
      // the call here, before anything is sent.
      const prompt = vault ? given.map((m, i) => plainTree(m, { how: { shapes: AI_SDK_SHAPES, root: AI_SDK_MESSAGE }, where: `prompt.${i}` })) : given;
      // one call: nothing is written into the vault if it is cleared while the call is masked, and the placeholders this
      // call puts in are collected (not the ones a model wrote in earlier answers and tool calls)
      const call = { generation: vault?.generation, tags: new Set<string>() };
      // A value found in one message is masked in all of them, also in those before it (a system prompt that names
      // the user): the messages are masked again while the vault learns something new. Detections are kept, so only
      // the vault is applied again.
      let masked: unknown[];
      for (;;) {
        const known = vault?.size;
        call.tags.clear();
        masked = await Promise.all(prompt.map((m) => maskMessage(m, call)));
        // the last pass: no value the vault holds stays in any text field, whatever kind of part it is in
        // (a part of a type the AI SDK does not have: every field but its type and provider options is text; which
        // objects are the AI SDK's is chosen from where they stand, so a tool's payload is data at every depth)
        masked = masked.map((m) => masker.sweep(m, call, { shapes: AI_SDK_SHAPES, root: AI_SDK_MESSAGE }));
        if (!vault || vault.size === known) break;
      }
      // with a vault, the call's free-text fields outside the prompt get the known values hidden as well
      const out = { ...params, ...(vault ? callFields(params as Record<string, unknown> | null, call, model) : {}), prompt: masked };
      if (vault) {
        vaultUnchanged(vault, call.generation);
        // their values are taken now, for this call's answer
        // (a call that asks for a JSON answer, structured output, gets its streamed values escaped for JSON strings)
        const json = (params as { responseFormat?: { type?: unknown } } | null)?.responseFormat?.type === "json";
        issued.set(out as object, { generation: vault.generation, values: vault.bindings(call.tags), json });
      }
      return out;
    },
  };

  if (vault) {
    const scope = (params: unknown): Call =>
      (params !== null && typeof params === "object" ? issued.get(params) : undefined) ?? { generation: -1, values: new Map() };
    // JSON text: complete string values only, everything else byte for byte; a parsed input: its strings
    const restoreInput = (input: unknown, call: Call) =>
      typeof input === "string" ? restoreJson(vault, input, call.values) : vault.restoreValue(input, { only: call.values });
    // What the model wrote is remembered for the next call, also when nothing in it was restored: an answer or tool
    // call that comes back without such a record is one this middleware never saw, and is searched more widely.
    const wrote = (key: string | undefined, written: unknown, call: Call) => {
      if (key !== undefined && key !== "") vault.remember(key, written, call.generation);
    };
    // the answer's text with the values (a JSON answer, structured output, stays valid JSON); reasoning as plain text.
    // A call that asked for JSON whose answer is not one JSON document (a model without a JSON mode writes it in a
    // code fence, which the AI SDK's extractJsonMiddleware or a repair function takes off later) gets its values
    // escaped for the inside of JSON strings, as a streamed JSON answer does (the 1.7.1 review round, L2-B1)
    const restoreText = (text: string, call: Call, answer = true) => {
      let restored: string;
      if (!answer) restored = vault.restore(text, { only: call.values });
      else if (call.json && !jsonDocument(text) && !/^\s*"/.test(text)) {
        const restorer = vault.restorer({ only: call.values, json: true });
        restored = restorer.push(text) + restorer.flush();
      } else restored = restoreAnswer(vault, text, call.values);
      wrote(restored, text, call);
      return restored;
    };
    const restoreCall = <T extends { toolCallId?: unknown; toolName?: unknown; input?: unknown }>(p: T, call: Call): T => {
      wrote(toolKey(p.toolCallId), parseJson(p.input), call);
      return restoresTool(p.toolName) ? { ...p, input: restoreInput(p.input, call) } : p;
    };

    middleware.wrapGenerate = async <R>({ doGenerate, params }: { doGenerate: () => PromiseLike<R>; params?: unknown }): Promise<R> => {
      const call = scope(params);
      const result = (await doGenerate()) as R & { content?: unknown };
      if (!result || typeof result !== "object" || !Array.isArray(result.content)) return result;
      const content = result.content.map((p: ContentPart) => {
        if (p === null || typeof p !== "object") return p;
        if (p.type === "text" && typeof p.text === "string") return { ...p, text: restoreText(p.text, call) };
        if (p.type === "reasoning" && typeof p.text === "string") {
          if (options.restoreReasoning) return { ...p, text: restoreText(p.text, call, false) };
          wrote(p.text, p.text, call);
        }
        if (p.type === "tool-call" && p.input !== undefined) return restoreCall(p, call);
        return p;
      });
      return { ...result, content };
    };

    middleware.wrapStream = async <R>({ doStream, params }: { doStream: () => PromiseLike<R>; params?: unknown }): Promise<R> => {
      const call = scope(params);
      const result = (await doStream()) as R & { stream?: unknown };
      if (!result || typeof result !== "object" || !(result.stream instanceof ReadableStream)) return result;
      // restorer: null for text that passes as it is
      const open = new Map<string, { restorer: VaultRestorer | null; type: string; id: unknown; raw: string; out: string }>();
      const toolNames = new Map<unknown, unknown>(); // tool-input id -> tool name
      const toolInputs = new Map<unknown, string>(); // allowed tool-input id -> raw JSON so far (restored once, at its end)
      const restoring = (type: string, id: unknown) =>
        type === "text-delta" || (type === "reasoning-delta" && options.restoreReasoning) || (type === "tool-input-delta" && restoresTool(toolNames.get(id)));
      const END: Record<string, string> = { "text-end": "text-delta", "reasoning-end": "reasoning-delta" };
      const finish = (key: string, controller: TransformStreamDefaultController<StreamPart>) => {
        const o = open.get(key);
        if (!o) return;
        open.delete(key);
        const rest = o.restorer ? o.restorer.flush() : "";
        if (rest !== "") controller.enqueue({ type: o.type, id: o.id, delta: rest });
        wrote(o.out + rest, o.raw, call);
      };
      const finishInput = (id: unknown, controller: TransformStreamDefaultController<StreamPart>) => {
        const raw = toolInputs.get(id);
        if (raw === undefined) return;
        toolInputs.delete(id);
        // restored once as a whole, so values come back in complete JSON string values only; cut-off JSON stays masked
        const delta = restoreJson(vault, raw, call.values);
        if (delta !== "") controller.enqueue({ type: "tool-input-delta", id, delta });
      };
      const stream = (result.stream as ReadableStream<StreamPart>).pipeThrough(
        new TransformStream<StreamPart, StreamPart>({
          transform(part, controller) {
            const type = typeof part?.type === "string" ? part.type : "";
            if (type === "tool-input-start") toolNames.set(part.id, part.toolName);
            if (type === "tool-input-delta" && typeof part.delta === "string" && restoring(type, part.id)) {
              toolInputs.set(part.id, (toolInputs.get(part.id) ?? "") + part.delta);
              return;
            }
            if ((type === "text-delta" || type === "reasoning-delta") && typeof part.delta === "string") {
              // reasoning that is not restored passes as it is; its text is still remembered as the model's
              const key = `${type}:${String(part.id)}`;
              let o = open.get(key);
              if (!o) {
                // the text of a JSON answer gets its values escaped for the inside of JSON strings
                const restorer = restoring(type, part.id) ? vault.restorer({ only: call.values, json: type === "text-delta" && call.json === true }) : null;
                open.set(key, (o = { restorer, type, id: part.id, raw: "", out: "" }));
              }
              o.raw += part.delta;
              const delta = o.restorer ? o.restorer.push(part.delta) : part.delta;
              o.out += delta;
              if (delta !== "") controller.enqueue({ ...part, delta });
              return;
            }
            if (type === "tool-input-end") finishInput(part.id, controller);
            else if (type in END) finish(`${END[type]}:${String(part.id)}`, controller);
            else if (type === "tool-call" && part.input !== undefined) return controller.enqueue(restoreCall(part, call));
            controller.enqueue(part);
          },
          flush(controller) {
            for (const key of [...open.keys()]) finish(key, controller);
            for (const id of [...toolInputs.keys()]) finishInput(id, controller);
          },
        }),
      );
      return { ...result, stream };
    };
  }
  return middleware;
}
