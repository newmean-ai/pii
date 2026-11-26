// LangChain.js (langchain >= 1.5.1): an agent middleware that masks personal information in the messages of each model call.
//
//   import { createAgent } from "langchain";
//   import { piiMiddleware } from "@newmean-ai/pii/langchain";
//
//   const agent = createAgent({ model: "openai:gpt-6.1", middleware: [piiMiddleware()] });
//   // "Call Kim Minsu on 010-1234-5678" reaches the model as "Call [PERSON] on [PHONE]"
//
// LangChain's own piiMiddleware takes a synchronous detector, and this package's model call is asynchronous, so this is a
// middleware of its own. It wraps the model call: the model gets masked copies, and the agent's state keeps the messages
// as they were written. `langchain` is an optional peer dependency, needed only for this entry.
//
// With a vault (since 1.7.2) the model gets numbered placeholders ("[PERSON_1]") and the middleware puts the values back
// in the answer's text; only placeholders this middleware put into that call are restored. Tool-call arguments get the
// values back only for the tools in `restoreTools` (default none). Earlier answers, their tool calls and tool results are
// masked again on the next call (default roles user, assistant, tool): what the model wrote is remembered in the vault
// and sent again instead of the restored text. Tokens streamed through callbacks during the call still show the
// placeholders (the restored message comes when the call ends).
import { AIMessage, createMiddleware, HumanMessage, ProviderStrategy, SystemMessage, ToolMessage, ToolStrategy, type BaseMessage } from "langchain";
import { convertToOpenAITool } from "@langchain/core/utils/function_calling";
import { toJsonSchema } from "@langchain/core/utils/json_schema";
import { RunnableLambda } from "@langchain/core/runnables";
import {
  bareTags,
  blockForm,
  carriesContent,
  createMasker,
  librarySchema,
  relaxedSchema,
  storedAs,
  LANGCHAIN_CONTENT,
  LANGCHAIN_KWARGS,
  LANGCHAIN_RECEIVED,
  LANGCHAIN_USER,
  LANGCHAIN_SHAPES,
  LANGCHAIN_TOOL_CALLS,
  plainTree,
  LANGCHAIN_TYPES,
  recordTags,
  toolTest,
  writtenOtherwise,
  type MaskContext,
  type MaskerOptions,
  type Slot,
} from "./masker.js";
import { vaultUnchanged } from "./redactor.js";
import { isRecord, restoreAnswer as restoreAnswerText, restoreJson, type Vault } from "./vault.js";

export type PiiMessageRole = "user" | "assistant" | "tool";

export interface PiiMiddlewareOptions extends MaskerOptions {
  /** Message kinds to mask. Default `["user"]`, or `["user", "assistant", "tool"]` with a vault; "tool" masks tool
   *  results, "assistant" earlier model answers and their reasoning (with a vault also the strings of their tool-call
   *  arguments). With a vault, kinds left out still get the vault's known values hidden. */
  roles?: readonly PiiMessageRole[];
  /** With a vault: tools whose call arguments get the values back before they run: a name, names, or a test. Default
   *  none. Name only tools that may see the values; a prompt injection in text the model reads can make the model put
   *  placeholders into any tool call. */
  restoreTools?: string | readonly string[] | ((toolName: string) => boolean);
  /** With a vault: also put the values back in reasoning blocks. Default false. */
  restoreReasoning?: boolean;
}

type Content = BaseMessage["content"];

// text-bearing fields of content blocks: any block's "text", and reasoning blocks (LangChain "reasoning", Anthropic "thinking")
const REASONING_FIELDS: Record<string, "reasoning" | "thinking"> = { reasoning: "reasoning", thinking: "thinking" };
// Tool traffic inside content blocks, whatever the block type: what a model wrote for a tool (Anthropic "tool_use" input,
// LangChain "tool_call" args, OpenAI "function_call" arguments) and what came back (Anthropic "tool_result" content,
// "server_tool_result" output). Other fields (ids, names, URLs, base64 data, signatures) are left alone.
const INPUT_FIELDS = ["input", "args", "arguments"] as const;
const RESULT_FIELDS = ["content", "output"] as const;

interface Walk {
  /** Text and reasoning. */
  text: (s: string, kind: "text" | "reasoning") => Promise<string> | string;
  /** With a vault: tool input inside a block (the tool call's id is given). */
  input?: (value: unknown, id: unknown) => Promise<unknown>;
  /** With a vault: a tool result inside a block that is JSON data (not text, not a typed content block). */
  result?: (value: unknown) => Promise<unknown>;
  /** With a vault: text of a tool result inside a block (read as received, never as model text). */
  received?: (s: string) => Promise<string> | string;
  /** The content is text a model wrote: any typed object in it is a block (in other content only the types of
   *  LANGCHAIN_TYPES are; rows a tool returned with a type of their own are data). */
  model?: boolean;
}

/** A typed object ({ type: "...", ... }): a content block in text a model wrote. */
const isTyped = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v) && typeof (v as { type?: unknown }).type === "string";
/** A content block of a type LangChain or a provider has (in what a model wrote: a server tool's result). */
const isBlock = (v: unknown): v is Record<string, unknown> => isTyped(v) && LANGCHAIN_TYPES.has(v.type as string);
/** A content block in what came from outside (a tool's result, a user's message), as opposed to a row of JSON data: a
 *  block type such a place holds, with that type's own content (a text block's text, a file's url, base64 data or file
 *  id, a document's source). Rows with a type of their own ({ type: "contact", name }) and rows typed like a block
 *  without its content (a storage API's { type: "file", id, name, owned_by }) are data. */
const isReceivedBlock = (v: unknown): v is Record<string, unknown> => isTyped(v) && carriesContent(v);
/** A class instance or a toJSON object where a block may stand is read as the JSON it gives, with the block fields its
 *  class gives by getters (what a provider is sent: `blockForm`). */
const asWritten = (v: unknown): unknown => (writtenOtherwise(v) ? blockForm(v) : v);

/** One content block with `all` (a vault): its "text", reasoning fields, and tool input and results inside it. */
async function mapBlock(b: Record<string, unknown>, walk: Walk): Promise<Record<string, unknown>> {
  let out = b;
  if (typeof b.text === "string") out = { ...out, text: await walk.text(b.text, b.type === "reasoning" || b.type === "thinking" ? "reasoning" : "text") };
  const field = typeof b.type === "string" ? REASONING_FIELDS[b.type] : undefined;
  if (field && typeof b[field] === "string") out = { ...out, [field]: await walk.text(b[field] as string, "reasoning") };
  // the tool call a block belongs to: OpenAI "function_call" names it call_id (its own id is the item's), others id
  const id = typeof b.call_id === "string" ? b.call_id : b.id;
  if (walk.input) for (const k of INPUT_FIELDS) if (b[k] !== undefined && b[k] !== null) out = { ...out, [k]: await walk.input(b[k], id) };
  if (walk.result) {
    // inside a tool result everything is read as received: text, nested typed blocks, and rows of JSON data (in what
    // came from outside, a typed object is a block only with its type's own content)
    const received = walk.received ?? ((s: string) => walk.text(s, "text"));
    const inner: Walk = { ...walk, text: received, input: walk.result };
    const block = walk.model ? isBlock : isReceivedBlock;
    const item = (x: unknown): Promise<unknown> | unknown => {
      const v = asWritten(x);
      return typeof v === "string" ? received(v) : block(v) ? mapBlock(v, inner) : walk.result!(v);
    };
    // a document given inline (Anthropic: source { type: "text", data } or { type: "content", content: [...] })
    const src = b.source as Record<string, unknown> | undefined;
    if (isTyped(src) && src.type === "text" && typeof src.data === "string") out = { ...out, source: { ...src, data: await received(src.data) } };
    else if (isTyped(src) && Array.isArray(src.content)) out = { ...out, source: { ...src, content: await Promise.all(src.content.map(item)) } };
    for (const k of RESULT_FIELDS) {
      const v = b[k];
      if (v === undefined || v === null) continue;
      // a field where blocks stand (Anthropic tool_result content, OpenAI function_call_output output): a list with
      // typed blocks in it is content, item by item, and any other list is one document of JSON data. In any other
      // block the field holds a payload (a json block's content, a server tool's output): JSON data, whatever types
      // its objects give themselves
      const holdsBlocks = LANGCHAIN_SHAPES.slot(b.type as string, k) !== undefined;
      if (!holdsBlocks) out = { ...out, [k]: typeof v === "string" ? await received(v) : await walk.result(v) };
      else out = { ...out, [k]: Array.isArray(v) && v.some((x) => block(asWritten(x))) ? await Promise.all(v.map(item)) : await item(v) };
    }
  }
  return out;
}

/** The text of a message's content mapped by `walk`. With `all` (a vault): bare strings, every block's "text" and
 *  reasoning fields, and tool input and results inside blocks; without it, as in 1.6: string content and "text" blocks. */
async function mapContent(content: unknown, walk: Walk, all: boolean): Promise<unknown> {
  if (typeof content === "string") return walk.text(content, "text");
  if (!Array.isArray(content)) return content;
  // with a vault, a class instance or a toJSON object where a block stands is read as the JSON it gives (since 1.7.2,
  // the 24th outside review: a LangChain.js tool returned one as a json block, and its payload was sent as written)
  const items = all && content.some(writtenOtherwise) ? content.map(asWritten) : content;
  // rows of JSON data where content blocks were expected (a hand-built tool message, typed rows of a tool): one
  // document of data
  const block = walk.model ? isTyped : isReceivedBlock;
  if (all && walk.result && items.some((x) => x !== null && typeof x === "object") && !items.some(block)) return walk.result(items);
  return Promise.all(
    items.map(async (block: unknown) => {
      if (!all) {
        const b = block as { type?: unknown; text?: unknown } | null;
        return b && typeof b === "object" && b.type === "text" && typeof b.text === "string" ? { ...b, text: await walk.text(b.text, "text") } : block;
      }
      if (typeof block === "string") return walk.text(block, "text");
      if (block === null || typeof block !== "object") return block;
      // a row of JSON data where a content block was expected (a hand-built tool message) is masked as data
      if (!walk.model && !isReceivedBlock(block)) return walk.result ? walk.result(block) : block;
      return isTyped(block) ? mapBlock(block, walk) : walk.result ? walk.result(block) : block;
    }),
  );
}

const same = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);

/** A structured response (JSON data the answer was parsed into) with `values` (this call's placeholders) put back in
 *  its strings and in its keys, as in a JSON answer's text: `{ "[PERSON_1]": "[PERSON_1]" }` gets the name in both (the
 *  23rd outside review: keys stayed masked). A key whose value is already a key of the same object stays as the model
 *  wrote it, so that no field is lost. Objects that are not plain data are left as they are. */
function restoreData(v: Vault, x: unknown, values: ReadonlyMap<string, string>): unknown {
  if (typeof x === "string") return v.restore(x, { only: values });
  if (Array.isArray(x)) return x.map((y) => restoreData(v, y, values));
  if (!isRecord(x)) return x;
  const entries = Object.entries(x);
  const before = new Set(entries.map(([k]) => k));
  const used = new Set<string>();
  const out: Record<string, unknown> = {};
  for (const [k, y] of entries) {
    const restored = v.restore(k, { only: values });
    const key = restored !== k && (before.has(restored) || used.has(restored)) ? k : restored;
    used.add(key);
    // (defined, not assigned: a key "__proto__" is a key like any other)
    Object.defineProperty(out, key, { value: restoreData(v, y, values), enumerable: true, writable: true, configurable: true });
  }
  return out;
}
/** The name a tool is bound under: a LangChain tool's name, or the name of a tool definition (OpenAI or Anthropic
 *  form). */
const toolName = (t: unknown): unknown => {
  if (t === null || typeof t !== "object") return undefined;
  const d = t as { name?: unknown; function?: { name?: unknown } };
  return d.function !== null && typeof d.function === "object" ? d.function.name : d.name;
};

/** The model given to the agent as a copy whose `bindTools` passes the tools and options the agent binds through
 *  `before`, and the model it binds through `after` (since 1.7.2: the review round's L7-1, the 24th outside review's
 *  A5): the final binding, which is what the provider gets, whatever LangChain version made it. The model itself is not
 *  changed: the copy is an object whose prototype is the model (a model bound to options, `withConfig`, is copied with
 *  its model). undefined for a model that has no `bindTools`. */
function bindingModel(
  model: unknown,
  before: (tools: unknown[], options: Record<string, unknown>) => [unknown[], Record<string, unknown>],
  after: (bound: unknown) => unknown,
): unknown {
  if (model === null || typeof model !== "object") return undefined;
  const m = model as { bindTools?: unknown; bound?: unknown };
  if (typeof m.bindTools === "function") {
    const own = m.bindTools as (tools: unknown[], options?: Record<string, unknown>) => unknown;
    const bindTools = (tools: unknown[], options?: Record<string, unknown>) => {
      const bound = own.call(model, ...before(Array.isArray(tools) ? tools : [], options ?? {}));
      return bound instanceof Promise ? bound.then(after) : after(bound);
    };
    return Object.create(model, { bindTools: { value: bindTools, writable: true, configurable: true } });
  }
  const inner = m.bound === undefined ? undefined : bindingModel(m.bound, before, after);
  return inner === undefined ? undefined : Object.create(model, { bound: { value: inner, writable: true, configurable: true } });
}

// The model's own fields that go into the request (since 1.7.2, the gate run of 1.7.1: ChatOpenAI `user`,
// `promptCacheKey` and `modelKwargs` (metadata, safety_identifier, a stored prompt's variables), ChatAnthropic
// `invocationKwargs` (metadata.user_id) were sent as written). A LangChain.js chat model reports what it sends besides
// the messages as its invocation parameters (`invocationParams`, also what tracing records), and a provider package
// may spread its kwargs object into the request once more as it sends it (ChatAnthropic's `invocationKwargs`).
/** An object field of a model that a provider package spreads into the request as it is (`modelKwargs`,
 *  `invocationKwargs`); `lc_kwargs` holds the constructor's arguments for serialization and is never sent. */
const isKwargs = (k: string) => k !== "lc_kwargs" && /kwargs$/i.test(k);
/** Which response strategy of LangChain `f` is (ToolStrategy, ProviderStrategy), also one made by another copy of the
 *  package (an application that loads LangChain's CommonJS build, while this package loads its ES module build): such a
 *  strategy is copied as it is, with its schema hidden, and the agent still takes it for its own. */
const strategyOf = (f: unknown): "tool" | "provider" | undefined => {
  if (f instanceof ToolStrategy) return "tool";
  if (f instanceof ProviderStrategy) return "provider";
  if (f === null || typeof f !== "object") return undefined;
  const name = (f as { constructor?: { name?: unknown } }).constructor?.name;
  if (name === "ToolStrategy" && "schema" in f && "tool" in f) return "tool";
  if (name === "ProviderStrategy" && "schema" in f) return "provider";
  return undefined;
};
/** The fields of a LangChain tool its definition is written from. */
const TOOL_FIELDS = ["name", "description", "schema"] as const;
/** Methods of a model, or of a binding of one, that give another model to call: what they give is copied too
 *  (ChatOpenAI's `withConfig` makes a new model from its constructor fields; a model made by `initChatModel` makes its
 *  model when it is called). */
const MAKERS = ["bindTools", "withConfig", "bind", "withStructuredOutput", "_getModelInstance"] as const;
/** A LangChain chat model: one that reports its invocation parameters and generates (ChatOpenAI keeps two of them, for
 *  Chat Completions and for the Responses API, and calls the one a request needs). */
const isChatModel = (v: unknown): v is Record<string, unknown> =>
  v !== null &&
  typeof v === "object" &&
  typeof (v as { invocationParams?: unknown }).invocationParams === "function" &&
  typeof (v as { _generate?: unknown })._generate === "function";

/** The fields of a model that hold a value the vault knows where they go into the request (`held` tells), by name:
 *  the parameters it reports (`user`, `metadata`, `prompt_cache_key`), its kwargs objects (`modelKwargs`), those of the
 *  chat models it keeps (`completions.user`: ChatOpenAI keeps two), and the model and options of a binding of one
 *  (`bound.user`, `kwargs`). A model made when it is called (`initChatModel`) is named as a whole: its fields can come
 *  with the call's configuration, and it is checked when it makes its model (`made` false: not named). A model that
 *  cannot report its parameters is named with "invocationParams". */
function heldFields(model: unknown, held: (x: unknown) => boolean, made = true, at = "", seen = new Set<object>()): string[] {
  if (model === null || typeof model !== "object" || seen.has(model)) return [];
  seen.add(model);
  const m = model as Record<string, unknown>;
  const name = (k: string) => (at === "" ? k : `${at}.${k}`);
  if (typeof m._getModelInstance === "function") return made ? [at === "" ? "(the model initChatModel makes)" : at] : [];
  const out: string[] = [];
  if (typeof m.invocationParams === "function") {
    let params: unknown;
    try {
      params = (m.invocationParams as (o?: unknown) => unknown).call(m, {});
    } catch (e) {
      // (a field JSON cannot write fails the call)
      if (failing(e)) throw e;
      out.push(name("invocationParams"));
    }
    if (isRecord(params)) for (const [k, v] of Object.entries(params)) if (held({ [k]: v })) out.push(name(k));
  }
  for (const k of Object.keys(m)) {
    const v = m[k];
    // (a kwargs object is read as model settings are, built into the tree the provider writes: `held`)
    if (isKwargs(k) && v !== null && typeof v === "object") {
      if (held(v)) out.push(name(k));
    } else if (k === "bound" || isChatModel(v)) out.push(...heldFields(v, held, made, name(k), seen));
    else if (k === "config" && "bound" in m && held(v)) out.push(name(k));
  }
  return [...new Set(out)];
}

/** What the copies of a model for one call share (since 1.7.2, release gate 8). */
interface CopyState {
  /** The copies give their parameters and kwargs objects hidden: set once the call is checked, before it is sent. */
  hiding: boolean;
  /** How deep the copies are in reporting their parameters (`invocationParams`): a field read then is read into its
   *  snapshot. */
  tracing: number;
  /** A field JSON cannot write, met while the copies reported their parameters: the report fails with it, also where
   *  the model's own code caught it. */
  failure?: unknown;
  /** The call's trees (`MaskContext.trees`): each object built into JSON once, whichever field holds it. */
  trees: Map<object, unknown>;
}

/** The errors of a field JSON cannot write, as a copy of a model read it, and of a model that cannot be copied: they
 *  fail the call (`heldFields` does not take them for a model that cannot report its parameters). */
const failsCall = new WeakSet<object>();
const failing = (e: unknown): boolean => e !== null && typeof e === "object" && failsCall.has(e);

/** An object a model's field holds that is not request data: a library's schema, something that runs (a tool, a chat
 *  model, a runnable: an `invoke` of its own), or an instance of a class with methods besides `toJSON` (a client, an
 *  abort signal, a callback manager, a `Date`, a `Map`). Told from its descriptors, without reading its fields. It
 *  stays as it is in a field's snapshot; where it reaches the request it is built into JSON there, once in the call
 *  (`MaskContext.trees`). */
const machinery = (y: unknown): boolean => {
  if (y === null || typeof y !== "object") return false;
  if (librarySchema(y)) return true;
  if (typeof Object.getOwnPropertyDescriptor(y, "invoke")?.value === "function") return true;
  for (let p = Object.getPrototypeOf(y); p !== null && p !== Object.prototype && p !== Array.prototype; p = Object.getPrototypeOf(p))
    for (const k of Object.getOwnPropertyNames(p)) {
      if (k === "constructor" || k === "toJSON") continue;
      if (typeof Object.getOwnPropertyDescriptor(p, k)?.value === "function") return true;
    }
  return false;
};

/** The names a model reads as its own fields: its own fields and the getters of its classes. */
const fieldNames = (x: object): string[] => {
  const names = new Set<string>(Object.getOwnPropertyNames(x));
  for (let p = Object.getPrototypeOf(x); p !== null && p !== Object.prototype; p = Object.getPrototypeOf(p))
    for (const k of Object.getOwnPropertyNames(p)) if (Object.getOwnPropertyDescriptor(p, k)?.get !== undefined) names.add(k);
  return [...names];
};

/** In a field's snapshot: what is not request data stays as it is (a value that is not an object, written as JSON
 *  writes it where it reaches the request; a function; `machinery`). */
const notData = (_p: readonly (string | number)[], y: unknown): boolean => y === null || typeof y !== "object" || machinery(y);

/** A copy of the model for one call, which is what the call sends whether or not anything in it is masked (since 1.7.2,
 *  release gate 8: the model's own fields were read to look for known values, and when that read held none the model
 *  itself was sent, so the provider package wrote its fields again: a `toJSON` that gave other data the second time
 *  sent that data). The middleware does not change the model: the copy is an object whose prototype is the model. Each
 *  field that goes into the request is read once, when it is first read, into a snapshot the copy holds from then on: a
 *  kwargs object (`modelKwargs`, `invocationKwargs`, a binding's `kwargs`, which a provider package may spread into the
 *  request as it sends it) whenever it is read, and any other field when it is read while the model reports its request
 *  parameters (`invocationParams`; on each report, so a field read only with some call options is read once too). A
 *  snapshot is the field read once as JSON reads it (`plainTree`: its `toJSON` called, a getter read, a Proxy or a
 *  class instance without methods rebuilt), with what is not request data left as it is (`notData`: a function, a
 *  client, a signal, a library's schema, a tool); an object met again in the call gives its first tree (`state.trees`:
 *  ChatOpenAI keeps two chat models made from one constructor's fields, and `withConfig` makes a new model from them).
 *  A field JSON cannot write fails the call. Until a field is read so, the copy gives what the model holds. Once
 *  `state.hiding` is set the copy gives its parameters and kwargs objects hidden (`hide`, as model settings are
 *  hidden), the same each time. What the model's own code writes into its fields during the call is written on the
 *  model, as without the middleware (a lazily made client, a scripted model's next answer). The chat models it keeps
 *  (ChatOpenAI's two) and a binding's model are copied the same way; a model it makes (`bindTools`, `withConfig`, a
 *  model `initChatModel` makes when it is called) is copied too, and given to `check` before it is used. A model whose
 *  own `invocationParams` runs where its copy's does not (a class with private `#` fields) cannot be copied: the call
 *  fails when the copy reports its parameters for the request, before anything is sent. */
function hidingModel(model: unknown, hide: (params: unknown) => unknown, check: (copy: unknown) => void, state: CopyState): unknown {
  const copies = new WeakSet<object>();
  /** The failure of a field met while the copies reported their parameters, once they are done. */
  const failed = (): unknown => {
    if (state.tracing > 0 || state.failure === undefined) return undefined;
    const e = state.failure;
    state.failure = undefined;
    return e;
  };
  /** `report`, the copies reporting their parameters, with each field they read read into its snapshot. */
  const trace = <R>(report: () => R): R => {
    state.tracing++;
    let out: R;
    try {
      out = report();
    } catch (e) {
      state.tracing--;
      throw failed() ?? e;
    }
    state.tracing--;
    const e = failed();
    if (e !== undefined) throw e;
    return out;
  };
  const copy = (x: unknown, made: Map<object, object>, at: string): unknown => {
    if (x === null || typeof x !== "object" || copies.has(x)) return x;
    const done = made.get(x);
    if (done !== undefined) return done;
    const m = x as Record<string, unknown>;
    const out = Object.create(x) as Record<string, unknown>;
    made.set(x, out);
    copies.add(out);
    const name = (k: string) => (at === "" ? k : `${at}.${k}`);
    const own = (value: unknown, enumerable = true): PropertyDescriptor => ({ value, writable: true, enumerable, configurable: true });
    const props: PropertyDescriptorMap = {};
    const params = m.invocationParams;
    if (typeof params === "function") {
      const report = params as (...a: unknown[]) => unknown;
      props.invocationParams = {
        value(this: unknown, ...args: unknown[]) {
          let raw: unknown;
          try {
            raw = trace(() => report.apply(this, args));
          } catch (e) {
            // For the request (the call is checked): where the model's own invocationParams runs and its copy's does not,
            // the model cannot be copied, and nothing is sent (the model is read only here, where the call fails)
            if (state.hiding && !failing(e)) {
              let reports = true;
              try {
                report.apply(m, args);
              } catch {
                reports = false;
              }
              if (reports) {
                const why = e instanceof Error ? e.message : String(e);
                const error = new TypeError(
                  `[@newmean-ai/pii] the model${at === "" ? "" : ` (${at})`} cannot be copied for the call: its invocationParams runs on the model and not on a copy of it (${why}; a class with private "#" fields, for one), and the call sends a copy that holds the model's own request fields as they were read once, so the call is not sent`,
                );
                failsCall.add(error);
                throw error;
              }
            }
            throw e;
          }
          return state.hiding && state.tracing === 0 ? hide(raw) : raw;
        },
        writable: true,
        configurable: true,
      };
    }
    // the chat models it keeps (ChatOpenAI's two) and a binding's model: copies of their own
    for (const k of Object.keys(m)) {
      const d = Object.getOwnPropertyDescriptor(m, k);
      const v = d !== undefined && "value" in d ? d.value : undefined;
      if (k === "bound" || isChatModel(v)) props[k] = own(copy(v, made, name(k)));
    }
    for (const k of MAKERS) {
      const f = m[k];
      if (typeof f !== "function") continue;
      props[k] = {
        value(this: unknown, ...args: unknown[]) {
          const r = (f as (...a: unknown[]) => unknown).apply(this, args);
          const checked = (y: unknown) => {
            const c = copy(y, new Map(), "");
            check(c);
            return c;
          };
          return r instanceof Promise ? r.then(checked) : checked(r);
        },
        writable: true,
        configurable: true,
      };
    }
    Object.defineProperties(out, props);

    // The fields, each read once into its snapshot (see above)
    const enumerable = new Set(Object.keys(m));
    const fields = fieldNames(m).filter((k) => {
      if (k in props) return false;
      const d = Object.getOwnPropertyDescriptor(m, k);
      return d === undefined || !("value" in d) || typeof d.value !== "function";
    });
    const snaps = new Map<string, { value: unknown } | { error: unknown }>();
    const hidden = new Map<string, unknown>();
    /** The snapshot of the field `k` (`self`: the object it is read on). */
    const snapshot = (k: string, self: object): unknown => {
      let s = snaps.get(k);
      if (s === undefined) {
        // (a getter of the model runs once, for the copy; one that throws read nothing, and is read again next time)
        const raw = Reflect.get(m, k, self);
        if (raw !== null && typeof raw === "object" && !machinery(raw)) {
          try {
            s = { value: plainTree(raw, { where: `the model's ${name(k)}`, memo: state.trees, keep: notData }) };
          } catch (e) {
            if (e !== null && typeof e === "object") failsCall.add(e);
            s = { error: e };
          }
        } else s = { value: raw };
        snaps.set(k, s);
      }
      if ("error" in s) {
        if (state.tracing > 0) state.failure ??= s.error;
        throw s.error;
      }
      return s.value;
    };
    for (const k of fields) {
      const kwargs = isKwargs(k);
      Object.defineProperty(out, k, {
        get(this: object) {
          // (a kwargs object is request data wherever it is read; any other field is while the model reports its
          // parameters, and is what the model holds until then)
          if (!snaps.has(k) && !kwargs && state.tracing === 0) return Reflect.get(m, k, this);
          const value = snapshot(k, this);
          if (!kwargs || !state.hiding || value === null || typeof value !== "object") return value;
          if (!hidden.has(k)) hidden.set(k, hide(value));
          return hidden.get(k);
        },
        set(v: unknown) {
          // Written by the model's own code during the call (a client it makes when it is first called, the next answer
          // of a scripted model): written on the model, as without the middleware, so its state lasts beyond the call;
          // the copy reads the field again from then on (once, when it goes into the request)
          snaps.delete(k);
          hidden.delete(k);
          if (!Reflect.set(m, k, v)) throw new TypeError(`Cannot assign to read only property '${k}' of the model`);
        },
        enumerable: enumerable.has(k),
        configurable: true,
      });
    }
    return out;
  };
  return copy(model, new Map(), "");
}

/** Key under which the vault remembers a tool call's arguments as the model wrote them. */
// (U+E000, a private-use character, keeps these keys apart from answer texts; a control character would not survive
// every JSON store: PostgreSQL jsonb refuses U+0000)
const toolKey = (id: unknown) => (typeof id === "string" && id !== "" ? `\ue000tool-call:${id}` : undefined);

export function piiMiddleware(options: PiiMiddlewareOptions = {}) {
  const { vault } = options;
  const roles = new Set<PiiMessageRole>(options.roles ?? (vault ? ["user", "assistant", "tool"] : ["user"]));
  const masker = createMasker(options);
  const restoresTool = toolTest(options.restoreTools);

  const maskMessage = async (m: BaseMessage, call: MaskContext): Promise<BaseMessage> => {
    // a system message is the developer's text: the model does not read it for values, but with a vault the values the
    // vault holds are hidden in it like everywhere else
    const system = SystemMessage.isInstance(m);
    const known: PiiMessageRole | null = HumanMessage.isInstance(m) ? "user" : ToolMessage.isInstance(m) ? "tool" : AIMessage.isInstance(m) ? "assistant" : null;
    // With a vault, a message of any other kind (a ChatMessage, a legacy FunctionMessage) is text from outside as well:
    // a function result like a tool result, anything else like a user message. Without one, as in 1.6, it passes.
    const other = known === null && !system && vault !== undefined;
    const role: PiiMessageRole | null = other ? ((m as { _getType?: () => string })._getType?.() === "function" ? "tool" : "user") : known;
    if (role === null && !(system && vault)) return m;
    const detect = role !== null && roles.has(role);
    // with a vault, kinds left out of `roles` still get the known values hidden (restored answers come back that way)
    if (!detect && !vault) return m;
    const model = role === "assistant";
    const c: MaskContext = model ? { ...call, model: true } : call;
    // text a model wrote; text that came from outside (a JSON document is read value by value)
    const wrote = (s: string, kind: "text" | "reasoning") => {
      // reasoning goes back exactly as the model wrote it when the middleware has its record: providers sign it
      const written = kind === "reasoning" && vault ? vault.recall(s) : undefined;
      if (typeof written !== "string") return detect ? masker.mask(s, c) : masker.hide(s, c);
      // (the placeholders restoreReasoning put back in it are this call's own, as an answer's are)
      if (options.restoreReasoning) for (const t of recordTags(vault!, written, s, "text")) call.tags?.add(t);
      return written;
    };
    const got = (s: string) => masker.received(s, call, detect);
    // what came back from a tool is read as received, also inside an assistant message (server tool results)
    const result = (value: unknown) => masker.data(value, call, detect);
    // Tool input as the model wrote it; its strings are masked again before they go back to the model. Parsed
    // arguments (tool_calls) may have been restored for the tool, so the record of the call is sent instead. JSON text
    // inside a block ("arguments") is never restored by this middleware: it is masked value by value, and every other
    // token (numbers, keys) stays as written. Without a record of the call, known values are searched more widely.
    const input = async (value: unknown, id: unknown): Promise<unknown> => {
      const key = vault ? toolKey(id) : undefined;
      const written = key !== undefined ? vault!.recall(key) : undefined;
      const ctx: MaskContext = written !== undefined ? { ...call, model: true, recall: false } : { ...call, model: true };
      if (typeof value === "string") return masker.json(value, ctx, detect);
      // (the placeholders restoreTools put back in the arguments are this call's own, as an answer's are)
      if (written !== undefined) for (const t of recordTags(vault!, written, value, "value")) call.tags?.add(t);
      return masker.value(written ?? value, ctx, detect);
    };
    // (the developer's text: known values hidden, placeholder-like text left as written)
    const hidden = (s: string) => masker.hide(s, { ...call, system: true });
    const text = model ? wrote : system ? hidden : got;
    const walk: Walk = vault ? { text, input: model ? input : result, result, received: got, model } : { text };
    const read = (await mapContent(m.content, walk, !!vault)) as Content;
    const ai = vault && AIMessage.isInstance(m) ? m : undefined;
    const calls = ai?.tool_calls ? await Promise.all(ai.tool_calls.map(async (t) => ({ ...t, args: (await input(t.args, t.id)) as typeof t.args }))) : undefined;
    // arguments the provider could not parse are still text the model wrote
    const bad = ai?.invalid_tool_calls
      ? await Promise.all(ai.invalid_tool_calls.map(async (t) => (typeof t.args === "string" ? { ...t, args: (await input(t.args, t.id)) as string } : t)))
      : undefined;
    // The last pass: no value the vault holds stays in any text field of the message, whatever kind of block it is
    // in, and in the provider-specific data kept beside the content (additional_kwargs: its tool calls are the
    // framework's, anything else in it is data).
    // (which objects are LangChain's is chosen from where they stand: a tool's payload is data at every depth)
    const sweep = <T>(x: T, root: Slot): T => masker.sweep(x, call, { shapes: LANGCHAIN_SHAPES, root });
    const kwargs = masker.sweep(m.additional_kwargs, call, { shapes: LANGCHAIN_SHAPES, root: LANGCHAIN_CONTENT, fields: LANGCHAIN_KWARGS });
    // (in what came from outside, a typed object without its type's own content is a row of data there too)
    const [content, toolCalls, invalid] = [sweep(read, model || system ? LANGCHAIN_CONTENT : role === "tool" ? LANGCHAIN_RECEIVED : LANGCHAIN_USER), calls && sweep(calls, LANGCHAIN_TOOL_CALLS), bad && sweep(bad, LANGCHAIN_TOOL_CALLS)];
    // The name of a person's message (HumanMessage, ChatMessage), which OpenAI-format providers send: a known value in
    // it becomes its placeholder without brackets ("PERSON_1"), the form providers accept there; it is never restored,
    // so hiding it adds nothing to the placeholders of the call (a "[PERSON_1]" in the answer stays as the model wrote
    // it unless the call put that placeholder in somewhere else; the 23rd outside review).
    // (also a value written with "_", "-" or a capital where it has white space: "Kim_Minsu"; the 1.7.1 review round,
    // L1-A2)
    const name = vault && role === "user" && typeof m.name === "string" && m.name !== "" ? masker.name(m.name, call) : m.name;
    if (
      same(content, m.content) &&
      (toolCalls === undefined || same(toolCalls, ai!.tool_calls)) &&
      (invalid === undefined || same(invalid, ai!.invalid_tool_calls)) &&
      kwargs === m.additional_kwargs &&
      name === m.name
    )
      return m;
    return remade(m, { content, additional_kwargs: kwargs, name, tool_calls: toolCalls, invalid_tool_calls: invalid });
  };

  /** The message built once into what its provider is sent (release gate 4 of 1.7.1): its content, its tool calls'
   *  arguments and its additional_kwargs as the plain JSON trees a provider writes (a class instance, a Date, a toJSON
   *  object with its toJSON called once, Map, Set, NaN and bytes in data become what JSON writes; a class instance where
   *  a block stands also gives the block fields its class gives by getters; a file block's bytes stay). The masking of
   *  the call reads these trees and they are what is sent. Input JSON cannot write (a BigInt, an object that refers to
   *  itself, a toJSON or a getter that throws) fails the call here, before anything is sent. The same message when it
   *  is plain already. */
  const plainMessage = (m: BaseMessage, where: string): BaseMessage => {
    if (m === null || typeof m !== "object") return m;
    const model = AIMessage.isInstance(m);
    const root = model || SystemMessage.isInstance(m) ? LANGCHAIN_CONTENT : LANGCHAIN_RECEIVED;
    // each field read once (since 1.7.2, release gate 8); a field given by a getter makes a new message of what was read
    const given = m.content;
    const givenKwargs = m.additional_kwargs;
    const ai = model ? (m as AIMessage) : undefined;
    const givenCalls = ai?.tool_calls;
    const givenInvalid = ai?.invalid_tool_calls;
    const name = m.name;
    const content = Array.isArray(given) ? (plainTree(given, { how: { shapes: LANGCHAIN_SHAPES, root }, where: `${where}.content` }) as Content) : given;
    const kwargs = plainTree(givenKwargs, { how: { shapes: LANGCHAIN_SHAPES, root: LANGCHAIN_CONTENT, fields: LANGCHAIN_KWARGS }, where: `${where}.additional_kwargs` }) as BaseMessage["additional_kwargs"];
    // a message's tool calls (their ids, names and arguments), built once as the list they are
    const calls = plainTree(givenCalls, { where: `${where}.tool_calls` }) as AIMessage["tool_calls"];
    const invalid = plainTree(givenInvalid, { where: `${where}.invalid_tool_calls` }) as AIMessage["invalid_tool_calls"];
    // (a field the message does not store as what was read, a getter or a Proxy's trap, would give the provider's
    // converter another value when it reads it again: a new message is made of what was read; the confirmation of
    // release gate 8)
    const fields: [string, unknown][] = [["content", given], ["additional_kwargs", givenKwargs], ["name", name]];
    if (ai) fields.push(["tool_calls", givenCalls], ["invalid_tool_calls", givenInvalid]);
    const read = fields.some(([k, v]) => !storedAs(m, k, v));
    if (!read && content === given && kwargs === givenKwargs && calls === givenCalls && invalid === givenInvalid) return m;
    return remade(m, { content, additional_kwargs: kwargs, name, tool_calls: calls, invalid_tool_calls: invalid });
  };

  /** `m` with the fields given, as a message of its own kind. */
  const remade = (
    m: BaseMessage,
    f: { content: Content; additional_kwargs: BaseMessage["additional_kwargs"]; name: string | undefined; tool_calls?: unknown; invalid_tool_calls?: unknown },
  ): BaseMessage => {
    const { content, additional_kwargs: kwargs, name } = f;
    const base = { content, id: m.id, name, additional_kwargs: kwargs, response_metadata: m.response_metadata };
    const known = HumanMessage.isInstance(m) || ToolMessage.isInstance(m) || AIMessage.isInstance(m) || SystemMessage.isInstance(m);
    if (!known) {
      // a kind this middleware has no constructor for: the same message with the masked fields
      const copy = Object.assign(Object.create(Object.getPrototypeOf(m)), m, { content, additional_kwargs: kwargs, name }) as BaseMessage & { lc_kwargs?: object };
      if (copy.lc_kwargs) copy.lc_kwargs = { ...copy.lc_kwargs, content, additional_kwargs: kwargs, ...(name === undefined ? {} : { name }) };
      return copy;
    }
    if (SystemMessage.isInstance(m)) return new SystemMessage(base as ConstructorParameters<typeof SystemMessage>[0]);
    if (ToolMessage.isInstance(m)) return new ToolMessage({ ...base, tool_call_id: m.tool_call_id, status: m.status, artifact: m.artifact } as ConstructorParameters<typeof ToolMessage>[0]);
    if (AIMessage.isInstance(m))
      return new AIMessage({ ...base, tool_calls: f.tool_calls ?? m.tool_calls, invalid_tool_calls: f.invalid_tool_calls ?? m.invalid_tool_calls, usage_metadata: m.usage_metadata } as ConstructorParameters<typeof AIMessage>[0]);
    return new HumanMessage(base as ConstructorParameters<typeof HumanMessage>[0]);
  };

  /** The answer with this call's placeholders (`values`, fixed when the call was masked) put back in its text and in
   *  allowed tools' arguments. What the model wrote is remembered for the next call, also where nothing was restored:
   *  text or a tool call that comes back without such a record is one this middleware never saw. Tool input inside
   *  content blocks stays as the model wrote it (the tools run from `tool_calls`). */
  const restoreAnswer = async (v: Vault, m: AIMessage, generation: number, values: ReadonlyMap<string, string>): Promise<AIMessage> => {
    // a tool call's input as the model wrote it (JSON text as its value, so a block and tool_calls give one record)
    const wrote = (id: unknown, input: unknown) => {
      const key = toolKey(id);
      if (!key) return;
      let written = input;
      if (typeof input === "string") {
        try {
          written = JSON.parse(input);
        } catch {}
      }
      v.remember(key, written, generation);
    };
    const content = (await mapContent(
      m.content,
      {
        text: (s, kind) => {
          // a JSON answer (structured output) is restored in its string values, so that it stays valid JSON
          const restored = kind !== "text" ? (options.restoreReasoning ? v.restore(s, { only: values }) : s) : restoreAnswerText(v, s, values);
          if (restored !== "") v.remember(restored, s, generation);
          return restored;
        },
        input: async (value, id) => (wrote(id, value), value), // a tool call that is only in a content block
        model: true,
      },
      true,
    )) as Content;
    const tool_calls = m.tool_calls?.map((t) => {
      wrote(t.id, t.args);
      return restoresTool(t.name) ? { ...t, args: v.restoreValue(t.args, { only: values }) } : t;
    });
    if (same(content, m.content) && same(tool_calls, m.tool_calls)) return m;
    return new AIMessage({
      content,
      id: m.id,
      name: m.name,
      additional_kwargs: m.additional_kwargs,
      response_metadata: m.response_metadata,
      tool_calls,
      invalid_tool_calls: m.invalid_tool_calls,
      usage_metadata: m.usage_metadata,
    } as ConstructorParameters<typeof AIMessage>[0]);
  };

  /** The fields of a request outside the messages that hold free text (since 1.7.2): tool descriptions and input
   *  schemas, the response format, and string values of the model settings. The known values are hidden without the
   *  model; tool names, property names, ids and signatures stay. Only the fields that changed are returned. */
  const requestFields = (request: { tools?: unknown; responseFormat?: unknown; modelSettings?: unknown }, c: MaskContext): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    const { text, tool, format } = hiders(c);
    const given = request.tools;
    if (Array.isArray(given)) {
      // (each tool read once; a list that does not store what was read, a Proxy or a getter at an index, is sent as the
      // list of what was read)
      const read = given.map((t) => t);
      const tools = read.map(tool);
      if (tools.some((t, i) => t !== read[i]) || read.some((t, i) => !storedAs(given, String(i), t))) out.tools = tools;
    }
    const responseFormat = format(request.responseFormat);
    if (responseFormat !== request.responseFormat) out.responseFormat = responseFormat;
    const modelSettings = masker.options(request.modelSettings, c, false, "settings");
    if (modelSettings !== request.modelSettings) out.modelSettings = modelSettings;
    return out;
  };

  /** How the fields of a request outside the messages are hidden: a string or settings (`text`), a tool (`tool`), a
   *  response format (`format`). */
  const hiders = (c: MaskContext) => {
    const text = (x: unknown) => masker.options(x, c); // a string
    // a schema as JSON schema (a Zod or Standard schema is turned into one only when a known value is in it)
    const schema = (x: unknown, keepTitle = false) => {
      if (x === null || typeof x !== "object") return x;
      const json = toJsonSchema(x as Parameters<typeof toJsonSchema>[0]) as unknown;
      const hidden = masker.schema(json, c, keepTitle);
      return hidden === json ? x : hidden;
    };
    const plain = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === "object" && !Array.isArray(x) && Object.getPrototypeOf(x) === Object.prototype;
    const copy = <T extends object>(x: T, changes: Record<string, unknown>): T => Object.assign(Object.create(Object.getPrototypeOf(x)), x, changes);
    /** A function definition ({ name, description, parameters }), an Anthropic-style tool ({ input_schema }) or a
     *  tool's parameters ({ name, description, schema }). */
    const definition = (d: unknown): unknown => {
      if (!plain(d)) return d;
      const next: Record<string, unknown> = {};
      if (typeof d.description === "string") next.description = text(d.description);
      for (const k of ["parameters", "input_schema", "schema"]) if (d[k] !== undefined) next[k] = schema(d[k]);
      if (plain(d.function)) next.function = definition(d.function);
      return Object.entries(next).some(([k, v]) => v !== d[k]) ? { ...d, ...next } : d;
    };
    const tool = (t: unknown): unknown => {
      if (t === null || typeof t !== "object") return t;
      // A definition (anything that is not a LangChain tool, which runs: `invoke`) is built once into the JSON it gives,
      // a class instance and a getter too, and that is read and sent (since 1.7.2, release gate 8: a class-backed
      // definition went out as written); a library's schema in it (a tool's parameters with a Zod schema) stays
      if (typeof (t as { invoke?: unknown }).invoke !== "function") {
        const tree = plainTree(t, { where: "tools", keep: (_p, y) => librarySchema(y) });
        return definition(tree);
      }
      // A LangChain tool (StructuredTool, tool()): the agent refuses a changed copy of a tool it runs, so the model gets
      // the tool's definition with the known values hidden in its description and input schema, under the same name
      // (a definition is sent as it is); the agent still runs its own tool. A tool with nothing to hide stays.
      // (a tool LangChain cannot write as a definition fails the call here, before anything is sent)
      const written: unknown = convertToOpenAITool(t as Parameters<typeof convertToOpenAITool>[0]);
      if (written === t || !plain(written)) return t;
      const hidden = definition(written);
      // A tool whose name, description or schema a getter or a Proxy's trap gives (one it does not store as what was
      // read: `storedAs`) sends the definition that was read, whether or not anything in it was hidden: the provider
      // package would read the field again (since 1.7.2, release gate 8)
      return hidden === written && TOOL_FIELDS.every((k) => storedAs(t, k, (t as Record<string, unknown>)[k])) ? t : hidden;
    };
    const format = (f: unknown): unknown => {
      if (Array.isArray(f)) {
        const next = f.map(format);
        return next.some((x, i) => x !== f[i]) ? next : f;
      }
      const kind = strategyOf(f);
      if (kind === "tool") {
        const g = f as ToolStrategy;
        // the schema the answer is checked against, and the tool the model is given for it (its name stays; built once
        // into the JSON it gives, as a definition is)
        const s = schema(g.schema, true);
        const t = definition(plainTree(g.tool, { where: "responseFormat.tool", keep: (_p, y) => librarySchema(y) }));
        return s === g.schema && t === g.tool ? f : copy(g, { schema: s, tool: t });
      }
      if (kind === "provider") {
        const g = f as ProviderStrategy;
        const s = schema(g.schema, true);
        return s === g.schema ? f : copy(g, { schema: s });
      }
      return f !== null && typeof f === "object" ? schema(f, true) : f;
    };
    return { text, tool, format };
  };

  /** What a call does where the agent binds the model to its tools and its response format: the final binding, which
   *  the provider gets (since 1.7.2: the review round's L7-1 and L3-B5, the 24th outside review's A5). LangChain.js
   *  1.2.20 (run) makes the tool or the schema of the response format from the agent's own options and does not give it
   *  to middleware at all; so the known values are hidden there, in every tool and option bound, under the names the
   *  agent gave them, and the placeholders put in there join the call's (`values`). With placeholders in the call, the
   *  schemas of the response format go without the constraints a placeholder cannot meet (`format`, `pattern`: a date
   *  or an e-mail field), so the model is not made to write a value in their place, and the model's answer gets this
   *  call's values back in what the agent reads as the structured response (the tool call of the response format, or
   *  the JSON text of a provider strategy) before the agent parses it and checks it against the original schema. The
   *  answer then goes back to the agent as the model wrote it (`parsed` maps the copy the agent read to it). `names`:
   *  the names of the request's own tools. */
  const binding = (v: Vault, call: MaskContext & { tags: Set<string> }, values: Map<string, string>, names: ReadonlySet<unknown>, parsed: WeakMap<object, AIMessage>) => {
    const state = { mine: new Set<unknown>(), native: false };
    const { tool } = hiders(call);
    // (whether the messages gave the model placeholders, which it may write into its structured response)
    const placeholders = values.size > 0;
    const before = (tools: unknown[], options: Record<string, unknown>): [unknown[], Record<string, unknown>] => {
      const known = new Set(call.tags);
      let bound = tools.map(tool);
      let given = masker.options(options, call, false, "settings");
      const added = [...call.tags].filter((t) => !known.has(t));
      if (added.length > 0) for (const [t, value] of v.bindings(added)) values.set(t, value);
      state.mine = new Set(bound.map(toolName).filter((n) => !names.has(n)));
      const native = given.response_format;
      state.native = native !== null && typeof native === "object";
      if (placeholders) {
        bound = bound.map((t) => (state.mine.has(toolName(t)) ? relaxedTool(t) : t));
        given = relaxedOptions(given);
      }
      return [bound, given];
    };
    const restored = (m: unknown): unknown => {
      if (values.size === 0 || !AIMessage.isInstance(m)) return m;
      const calls = m.tool_calls?.map((c) => (state.mine.has(c.name) ? { ...c, args: v.restoreValue(c.args, { only: values }) as typeof c.args } : c));
      // (the string values of a JSON answer: its keys get their values back with the structured response, where two
      // keys that become the same are told apart)
      const content = state.native && typeof m.content === "string" ? restoreJson(v, m.content, values) : m.content;
      if (content === m.content && (calls === undefined || calls.every((c, i) => c === m.tool_calls![i]))) return m;
      const copy = new AIMessage({
        content,
        id: m.id,
        name: m.name,
        additional_kwargs: m.additional_kwargs,
        response_metadata: m.response_metadata,
        tool_calls: calls,
        invalid_tool_calls: m.invalid_tool_calls,
        usage_metadata: m.usage_metadata,
      } as ConstructorParameters<typeof AIMessage>[0]);
      parsed.set(copy, m);
      return copy;
    };
    const after = (bound: unknown): unknown => {
      const runnable = bound as { pipe?: (next: unknown) => unknown };
      return typeof runnable?.pipe === "function" ? runnable.pipe(RunnableLambda.from(restored)) : bound;
    };
    return { before, after };
  };
  /** A tool of the response format, as it is bound, with a relaxed schema of its arguments (written as a tool
   *  definition when it changes). */
  const relaxedTool = (t: unknown): unknown => {
    const written = (isRecord(t) ? t : convertToOpenAITool(t as Parameters<typeof convertToOpenAITool>[0])) as Record<string, unknown>;
    const fn = (isRecord(written.function) ? written.function : written) as Record<string, unknown>;
    const key = "parameters" in fn ? "parameters" : "input_schema";
    const relaxed = relaxedSchema(fn[key]);
    if (relaxed === fn[key]) return t;
    return fn === written ? { ...written, [key]: relaxed } : { ...written, function: { ...fn, [key]: relaxed } };
  };
  /** The options of a provider strategy (OpenAI `response_format`, Anthropic `outputConfig`, Google `responseSchema`)
   *  with relaxed schemas. */
  const relaxedOptions = (o: Record<string, unknown>): Record<string, unknown> => {
    const out = { ...o };
    const format = o.response_format as { json_schema?: { schema?: unknown } } | undefined;
    if (isRecord(format) && isRecord(format.json_schema)) out.response_format = { ...format, json_schema: { ...format.json_schema, schema: relaxedSchema(format.json_schema.schema) } };
    const config = o.outputConfig as { format?: { schema?: unknown } } | undefined;
    if (isRecord(config) && isRecord(config.format)) out.outputConfig = { ...config, format: { ...config.format, schema: relaxedSchema(config.format.schema) } };
    if (o.responseSchema !== undefined) out.responseSchema = relaxedSchema(o.responseSchema);
    return Object.keys(out).some((k) => out[k] !== o[k]) || Object.keys(out).length !== Object.keys(o).length ? out : o;
  };

  /** A structured response (createAgent's responseFormat) and the messages that come with it, with this call's
   *  placeholders put back: the model's answer as `restoreAnswer` does, the tool message that carries the response as
   *  JSON text. */
  const restoreStructured = async (
    v: Vault,
    answer: { structuredResponse: unknown; messages: unknown[] },
    generation: number,
    values: ReadonlyMap<string, string>,
  ): Promise<typeof answer> => {
    const messages = await Promise.all(
      answer.messages.map(async (m) => {
        if (AIMessage.isInstance(m)) return restoreAnswer(v, m, generation, values);
        if (ToolMessage.isInstance(m) && typeof m.content === "string") {
          const content = restoreAnswerText(v, m.content, values);
          return content === m.content ? m : new ToolMessage({ content, tool_call_id: m.tool_call_id, name: m.name, id: m.id, status: m.status, artifact: m.artifact });
        }
        return m;
      }),
    );
    return { ...answer, structuredResponse: restoreData(v, answer.structuredResponse, values), messages };
  };

  return createMiddleware({
    name: "NewmeanPiiMiddleware",
    wrapModelCall: async (request, handler) => {
      // one call: nothing is written into the vault if it is cleared while the call is masked, and the placeholders this
      // call puts in are collected (not the ones a model wrote in earlier answers and tool calls)
      // (and the trees of what it builds into JSON, each object once: MaskContext.trees)
      const call = { generation: vault?.generation, tags: new Set<string>(), trees: new Map<object, unknown>() };
      if (!vault) return handler({ ...request, messages: await Promise.all(request.messages.map((m) => maskMessage(m, call))) });
      // A value found in one message is masked in all of them, also in those before it and in the system prompt: the
      // messages are masked again while the vault learns something new (detections are kept, so only the vault is
      // applied again).
      const given = request as { systemMessage?: unknown; systemPrompt?: unknown };
      // each message built once into what its provider is sent (release gate 4): the masking below reads these
      const sources = request.messages.map((m, i) => plainMessage(m, `messages.${i}`));
      const systemSource = SystemMessage.isInstance(given.systemMessage) ? plainMessage(given.systemMessage, "systemMessage") : undefined;
      let messages: BaseMessage[];
      let system: { systemMessage?: SystemMessage; systemPrompt?: string };
      for (;;) {
        const known = vault.size;
        call.tags.clear();
        messages = await Promise.all(sources.map((m) => maskMessage(m, call)));
        system = {};
        if (systemSource !== undefined) {
          const masked = (await maskMessage(systemSource, call)) as SystemMessage;
          if (masked !== given.systemMessage) system.systemMessage = masked; // only one of the two may change in a request
        } else if (typeof given.systemPrompt === "string") {
          const masked = masker.hide(given.systemPrompt, { ...call, system: true });
          if (masked !== given.systemPrompt) system.systemPrompt = masked;
        }
        if (vault.size === known) break;
      }
      // the request's free-text fields outside the messages (tools, response format, model settings)
      const fields = requestFields(request as Parameters<typeof requestFields>[0], call);
      vaultUnchanged(vault, call.generation);
      const generation = vault.generation;
      // fixed now, for this call's answer (with those the binding puts in)
      const values = new Map(vault.bindings(call.tags));
      // The model's own fields that go into the request (the constructor's `user`, `promptCacheKey`, `modelKwargs`,
      // `invocationKwargs`) are data like the model settings. With a vault that holds values, the model is given as a
      // copy for this call (see hidingModel), whether or not anything in its fields is masked: each field that goes into
      // the request is read once into the JSON the provider writes, and the copy sends that snapshot with the known
      // values hidden (the placeholders put in there join the call's). Since 1.7.2, release gate 8: a field read for the
      // check that held no known value was sent as the model held it, so the provider package wrote it again (a
      // `toJSON` that gave other data the second time sent that data). The call is checked on the copy before anything
      // is sent: a field the copy could not hide fails the call, naming the field, and so does a field JSON cannot
      // write.
      const requested = (request as { model?: unknown }).model;
      let own = requested;
      if (vault.size > 0 && requested !== null && typeof requested === "object") {
        // A field is held when reading it as model settings are read puts a placeholder in (with placeholders of its own:
        // what is only looked at is not sent). (The settings come back as the plain JSON tree a provider writes, release
        // gate 4, so a field without a known value can come back as another object: an undefined field left out, a Date
        // as its text. That alone does not hold a value.)
        const held = (x: unknown) => {
          const tags = new Set<string>();
          masker.options(x, { generation: call.generation, tags, trees: call.trees }, false, "settings");
          return tags.size > 0;
        };
        const hide = (x: unknown) => {
          const known = new Set(call.tags);
          const out = masker.options(x, call, false, "settings");
          if (out !== x && call.tags.size > known.size) for (const [t, value] of vault.bindings([...call.tags].filter((t) => !known.has(t)))) values.set(t, value);
          return out;
        };
        // (where the copy cannot report its parameters without the call's options, it is not held to it: it reads and
        // hides them when the call reports them; a model that reports them where its copy cannot fails the call then)
        const unreported = (f: string) => /(^|\.)invocationParams$/.test(f);
        let unheld = new Set<string>();
        let named: string[] = [];
        const check = (copy: unknown) => {
          const left = heldFields(copy, held, false).filter((f) => !unheld.has(f));
          if (left.length === 0) return;
          const shown = left.some(unreported) ? named.filter((f) => !unreported(f)) : left;
          throw new TypeError(`[@newmean-ai/pii] the model's own request field ${(shown.length > 0 ? shown : left).map((f) => `"${f}"`).join(", ")} holds a value the vault knows, and this model cannot be copied to send it hidden`);
        };
        const state: CopyState = { hiding: false, tracing: 0, trees: call.trees };
        own = hidingModel(requested, hide, check, state);
        // the call checked on the copy: what its fields were read into
        named = heldFields(own, held);
        unheld = new Set(named.filter(unreported));
        state.hiding = true;
        check(own);
      }
      // The tools and the response format are bound by the agent itself: with known values, the model is given as a
      // copy that hides them in that binding and reads the structured response back (see binding). A model that
      // cannot be copied fails the call when the request's response format holds a known value (nothing is sent).
      const parsed = new WeakMap<object, AIMessage>();
      let model: unknown = own === requested ? undefined : own;
      // (a request that says it has no response format: the agent binds only the request's tools, hidden above. A
      // LangChain.js that does not give middleware the response format, as 1.2.20 does not, may still bind one)
      const formatless = "responseFormat" in request && (request as { responseFormat?: unknown }).responseFormat === undefined;
      if (vault.size > 0 && !formatless) {
        const tools = (fields.tools ?? (request as { tools?: unknown }).tools) as unknown[] | undefined;
        const { before, after } = binding(vault, call, values, new Set((tools ?? []).map(toolName)), parsed);
        const bound = bindingModel(own, before, after);
        if (bound === undefined && requested !== undefined && fields.responseFormat !== undefined)
          throw new TypeError("[@newmean-ai/pii] the response format holds a value the vault knows, and this model cannot be copied to hide it where the agent binds it");
        if (bound !== undefined) model = bound;
      }
      const answer = (await handler({ ...request, ...fields, messages, ...system, ...(model === undefined ? {} : { model }) } as typeof request)) as unknown;
      // the agent read its structured response from a copy of the model's message: the message as the model wrote it
      const written = (m: unknown) => (m !== null && typeof m === "object" ? (parsed.get(m) ?? m) : m);
      if (AIMessage.isInstance(answer)) return restoreAnswer(vault, written(answer) as AIMessage, generation, values);
      // an agent with a response format gets { structuredResponse, messages } back from the model call
      const structured = answer as { structuredResponse?: unknown; messages?: unknown } | null;
      if (structured !== null && typeof structured === "object" && "structuredResponse" in structured && Array.isArray(structured.messages))
        return restoreStructured(vault, { ...structured, messages: structured.messages.map(written) } as { structuredResponse: unknown; messages: unknown[] }, generation, values) as never;
      return answer as never;
    },
  });
}
