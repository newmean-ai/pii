// Shared by the framework adapters (ai-sdk, langchain): one lazily created redactor and a small memory of what the model
// found in each text, so the unchanged history of a conversation is read once.
import { createRedactor, modelValues, redactReadings, vaultUnchanged, type RedactOptions, type Redactor, type RedactorOptions } from "./redactor.js";
import type { Span } from "./types.js";
import {
  escapedReadings,
  isRecord,
  jsonDocument,
  jsonEscapesView,
  plainNumber,
  jsonStringValues,
  quoteNumbers,
  jsonTokens,
  replaceJsonStrings,
  tagFound,
  restoreAnswer,
  vaultTags,
  type Vault,
} from "./vault.js";

export interface MaskerOptions {
  /** A redactor you already have (or its promise). Without it one is created from `create` on the first model call. */
  redactor?: Redactor | Promise<Redactor>;
  /** Options for `createRedactor` when no `redactor` is given (size, sensitivity, allow, block, modelUrl, ...). */
  create?: RedactorOptions;
  /** How a span is replaced: `mode` "tag" (default, `[EMAIL]`), "mask" or "blank", or `replace`. With a vault only
   *  `{ mode: "tag" }`: anything else throws when the middleware is created. */
  redact?: RedactOptions;
  /** Numbered placeholders that are put back in the model's answer and tool calls (since 1.7.2). One vault per user or
   *  conversation: create the middleware with it for that conversation (pass a shared `redactor` so the model loads once).
   *  Never share a vault between users, or one user's values can come back in another user's answer. Keep it with
   *  `vault.toJSON()` between requests. If it is cleared while a call's messages are being masked, that call fails; an
   *  answer that arrives after a clear still gets the values of its own call. */
  vault?: Vault;
}

/** One model call's masking: the vault generation it started in and the placeholders it put in. */
export interface MaskContext {
  /** `vault.generation` when the call started; a text is not masked into a vault that was cleared since. */
  generation?: number;
  /** Collects the placeholders this call puts in (not the ones a model wrote). */
  tags?: Set<string>;
  /** Text a model wrote (an earlier answer, tool-call arguments): this vault's placeholders in it stay as they are, and
   *  what the middleware remembered for it (`vault.recall`) is sent instead of the restored text. Without such a record
   *  the known values are masked wherever they occur in it, also inside words and whatever their length. Other text
   *  (user, tool results) is read as received and has every placeholder-like part escaped. */
  model?: boolean;
  /** false: the text is already what the model wrote (remembered tool-call arguments), so it is not looked up again. */
  recall?: boolean;
  /** The developer's text (a system prompt): known values are hidden, placeholder-like text in it stays as written. */
  system?: boolean;
  /** The JSON trees this call built of objects that JSON writes in a form of their own (`plainTree`), by the object: an
   *  object read again in the same call (a model's request parameters are reported more than once) gives the tree of
   *  its first read, so what was read for masking is what is sent (since 1.7.2, release gate 8). */
  trees?: Map<object, unknown>;
}

export interface Masker {
  /** The text with what the model finds masked. */
  mask(text: string, context?: MaskContext): Promise<string>;
  /** With a vault and without the model: the vault's known values hidden (for roles that are not masked). */
  hide(text: string, context?: MaskContext): string;
  /** Every string inside a JSON-like value masked (`detect` true) or hidden; keys and other values are kept. With a vault,
   *  a string that is exactly a known value becomes its placeholder whatever its length. */
  value(value: unknown, context: MaskContext | undefined, detect: boolean): Promise<unknown>;
  /** JSON text with its string values masked as `value` does; keys, numbers and spacing stay as written. Text that is
   *  not valid JSON is masked as plain text. */
  json(text: string, context: MaskContext | undefined, detect: boolean): Promise<string>;
  /** Text that came from outside (a user message, a tool result). A JSON document is read value by value, with the whole
   *  document as context for the model, so that escapes ("\u00e9") and JSON syntax cannot hide a value or be cut by a
   *  placeholder; any other text is masked as it is. */
  received(text: string, context: MaskContext | undefined, detect: boolean): Promise<string>;
  /** JSON data that came from outside (a tool result): every string value masked, with the whole document as context
   *  for the model. An object that is not plain (a class instance, something with toJSON) is read as the JSON it gives. */
  data(value: unknown, context: MaskContext | undefined, detect: boolean): Promise<unknown>;
  /** The last pass over a message that is about to be sent: every string in it, whatever field it is in, has the values
   *  the vault holds hidden (no model call; placeholder-like text stays as it is). It covers what the steps before do
   *  not know how to read: a part type added by a later framework version, provider-specific data. Identifiers, binary
   *  data, signed parts (reasoning) and what a framework matches by name are left alone, in the framework's objects
   *  only: which objects those are is chosen from where they stand (`options.shapes`). Returns the same object when
   *  nothing changed; without a vault, always. */
  sweep<T>(value: T, context: MaskContext | undefined, options: SweepOptions): T;
  /** With a vault: a JSON schema of the call (a tool's input, a response format), built once into the plain JSON tree
   *  the provider writes (`plainTree`: a class instance, at the top too, a getter, a list read through a getter), with
   *  the known values hidden, without the model, in its descriptions, titles, comments and examples, through the
   *  keywords that hold schemas (`properties`, `items`, `anyOf`, `$defs`, ...). Property names, literal constraints
   *  (`const`, `enum`, `default`, `pattern`, also objects) and every other keyword stay as written; `keepTitle`: the
   *  title of the top schema too (a framework takes it as the schema's name). Returns the same object when it is plain
   *  and nothing changed; without a vault, always. */
  schema<T>(schema: T, context?: MaskContext, keepTitle?: boolean): T;
  /** With a vault: provider options, model settings or provider tools' arguments of the call, built once into the plain
   *  JSON tree the provider writes (`plainTree`; what LangChain reads itself, such as `signal` and `callbacks`, stays as
   *  it is), with the known values hidden in every string and key (an `openai.user`, a template variable, a metadata
   *  key) and in their numbers. The exceptions are exact paths from the top (`OPTION_PATHS`, the README's list; since
   *  1.7.2, release gate 8): identifiers and credentials a provider defines there stay as written, its numeric settings
   *  (`maxTokens`) stay numbers, and a JSON schema there is read as `schema` reads it (a library's schema object stays).
   *  The same name anywhere else is data. `top`: what the top keys are, providers (AI SDK provider options; `message`:
   *  of a message or a part, where a provider's `name` is the message's name, as `name` writes it), the request's
   *  fields (LangChain model settings) or provider tools' ids (`{ "openai.mcp": args }`, kept). A number that is a known
   *  value at a path `strings` names (a field the call's own adapter takes only as a string) fails the call (a
   *  placeholder would turn a request the adapter refuses into one it sends). Returns the same object when nothing
   *  changed; without a vault, always. */
  options<T>(value: T, context?: MaskContext, message?: boolean, top?: OptionsTop, strings?: (path: readonly string[]) => boolean): T;
  /** With a vault: the name of a message (LangChain `HumanMessage({ name })`, OpenAI-compatible `openaiCompatible.name`)
   *  with each known value in it replaced by its placeholder without brackets (`PERSON_1`), the form providers accept
   *  there; also a value written with "_", "-" or a capital where it has white space ("Kim_Minsu", "KimMinsu" for a
   *  known "Kim Minsu"). Such a placeholder is never restored, and is not one of the call's. */
  name(name: string, context?: MaskContext): string;
}

/** A message name read with its joins as spaces (since 1.7.2, the review round's L1-A2): "_" and "-" between letters or
 *  digits, and a small letter followed by a capital ("Kim_Minsu", "kim-minsu" and "KimMinsu" are read as "Kim Minsu",
 *  "kim minsu" and "Kim Minsu"). `map[i]` is where the character `i` of the reading stands in the name (for a space put
 *  in, the character after it), and `map[text.length]` is the name's length. undefined when the name has no such
 *  join. */
export function nameReading(name: string): { text: string; map: number[] } | undefined {
  // (one pass each, a run of joins at a time: the time grows with the length of the name)
  const spaced = name.replace(/[_-]+/g, (run: string, at: number) =>
    /[\p{L}\p{N}\p{M}]$/u.test(name.slice(Math.max(0, at - 2), at)) && /^[\p{L}\p{N}]/u.test(name.slice(at + run.length, at + run.length + 2))
      ? " ".repeat(run.length)
      : run,
  );
  const cuts = new Set<number>();
  for (const m of spaced.matchAll(/\p{Ll}(?=[\p{Lu}\p{Lt}])/gu)) cuts.add(m.index + m[0].length);
  if (spaced === name && cuts.size === 0) return undefined;
  let text = "";
  const map: number[] = [];
  for (let i = 0; i < spaced.length; i++) {
    if (cuts.has(i)) {
      text += " ";
      map.push(i);
    }
    text += spaced[i];
    map.push(i);
  }
  map.push(name.length);
  return { text, map };
}

export interface SweepOptions {
  /** The framework whose objects the value holds: where its objects stand, and their types. */
  shapes: Shapes;
  /** What the value is: the framework's objects that may stand there (a message, its content, its tool calls). */
  root: Slot;
  /** The value is an object whose fields named here hold the framework's objects of the slot given, and whose other
   *  fields are data (the top of LangChain's additional_kwargs). */
  fields?: ReadonlyMap<string, Slot>;
}

/** A place where a framework's objects stand: the field of a message that holds its parts, the field of a tool result
 *  that holds its output, the field of a file part that holds its file data. */
export interface Slot {
  /** The types of the framework's objects that may stand there; undefined: any type of the framework. An object of
   *  another type there is not the framework's: every field but its type and provider options is data. */
  types?: ReadonlySet<string>;
  /** Whether an object without a type that stands there is the framework's (a message, a tool call, a function);
   *  otherwise it is data. */
  untyped?: boolean;
  /** The slot is in what came from outside (a tool's result, a user's message): an object there is the framework's only
   *  when it carries its type's own content (`Shapes.content`); any other typed object is a row of data, whatever its
   *  type (a storage API's `{ type: "file", id, name, owned_by }`). The slots inside such an object are too. */
  received?: boolean;
  /** In what came from outside: a block's `id` is the framework's identifier here (the blocks of a user's message, which
   *  the application builds); elsewhere there (what a tool returned) it is data, as no provider matches such a block by
   *  its id. Not carried into the slots inside a block. */
  ids?: boolean;
}

/** Where a framework's objects stand inside one another (since 1.7.2, the 23rd outside review). Which objects are the
 *  framework's is chosen from the object and the field they stand in, never from the `type` an object gives itself:
 *  a tool's JSON payload is data at every depth, whatever types its objects have
 *  ({ type: "json", data: { type: "text", name: "Kim Minsu" } }). */
export interface Shapes {
  /** Every type of the framework's objects. */
  types: ReadonlySet<string>;
  /** The types whose `name` is an identifier that the framework or the provider matches (a tool's or a function's
   *  name). In an object of another type, `name` is text. */
  named: ReadonlySet<string>;
  /** The types whose `data` is the content of a file (base64, bytes, a URL object), as opposed to a payload. */
  files: ReadonlySet<string>;
  /** The objects that may stand in the field `field` of an object of the type `type` (undefined: an object without a
   *  type), or undefined when that field holds data. */
  slot(type: string | undefined, field: string): Slot | undefined;
  /** For the slots of what came from outside (`Slot.received`): the fields that hold the own content of each type of
   *  the framework's objects that may stand there. A type that is not here is never the framework's there. */
  content?: ReadonlyMap<string, readonly string[]>;
}

/** A name of a message (LangChain `HumanMessage({ name })`) with the known values replaced by their placeholders
 *  written without brackets (`PERSON_1`): providers take only letters, digits, `_` and `-` there. `hidden` is the name
 *  after the known values were hidden in it. Such a placeholder is never restored. */
export function bareTags(name: string, hidden: string): string {
  if (hidden === name) return name;
  const before = new Set(vaultTags(name));
  // (one pass over the name; the pattern of a placeholder is vault.ts's)
  return hidden.replace(/\[([A-Z][A-Z0-9_]{0,31}_[0-9]{1,12})\]/g, (tag: string, bare: string) => (before.has(tag) ? tag : bare));
}

// The last pass tells the objects of a framework (a message part, a content block, a tool call) from the data inside
// them. In a framework object these fields are not text for a model to read: identifiers, names a framework or
// provider matches, references to files, provider metadata (signatures live there). Provider options are read by
// `options`: their string values are text, their identifiers and signatures are not.
const NOT_TEXT = new Set([
  "type", "role", "id", "name", "index", "toolCallId", "toolName", "approvalId", "tool_call_id", "tool_use_id", "call_id",
  "mediaType", "mimeType", "mime_type", "media_type", "url", "originalUrl", "image_url", "file_id", "fileId", "reference",
  "signature", "encrypted_content", "providerMetadata", "response_metadata", "usage_metadata",
]);
// the fields of an object of a type the framework does not have that are not data: its type and provider options
// (since 1.7.2, release gate 4: `providerMetadata` of a part type the framework does not define is data)
const OPTIONS = new Set(["type", "providerOptions"]);
/** The types of the AI SDK's own objects in a prompt (provider specifications v2, v3 and v4): message parts, tool
 *  outputs, the items of a "content" output, and the data of a file (AI SDK 7). */
export const AI_SDK_TYPES: ReadonlySet<string> = new Set([
  // message parts
  "text", "file", "image", "reasoning", "reasoning-file", "redacted-reasoning", "tool-call", "tool-result", "tool-error",
  "tool-approval-request", "tool-approval-response", "source", "custom",
  // tool outputs
  "json", "error-text", "error-json", "content", "execution-denied",
  // items of a "content" output
  "media", "file-data", "file-url", "file-id", "file-reference", "image-data", "image-url", "image-file-id",
  "image-file-reference",
  // the data of a file
  "data", "url", "reference",
]);
/** The types of LangChain's content blocks and of the provider blocks it passes on (standard blocks, the block types a
 *  tool message takes, Anthropic, OpenAI chat and Responses, Google), and of the shapes inside them (sources,
 *  citations, tool calls). The same list is in python/src/newmean_pii/langchain.py. */
export const LANGCHAIN_TYPES: ReadonlySet<string> = new Set([
  // standard blocks (langchain-core KNOWN_BLOCK_TYPES) and the block types a tool message takes
  "text", "reasoning", "refusal", "tool_call", "tool_call_chunk", "invalid_tool_call", "server_tool_call",
  "server_tool_call_chunk", "server_tool_call_result", "server_tool_result", "image", "video", "audio", "text-plain",
  "file", "non_standard", "citation", "non_standard_annotation", "image_url", "json", "search_result",
  "custom_tool_call_output", "document",
  // Anthropic
  "thinking", "redacted_thinking", "tool_use", "tool_result", "server_tool_use", "web_search_tool_result",
  "web_fetch_tool_result", "code_execution_tool_result", "bash_code_execution_tool_result",
  "text_editor_code_execution_tool_result", "tool_search_tool_result", "mcp_tool_use", "mcp_tool_result",
  "container_upload", "web_search_result", "char_location", "page_location", "content_block_location",
  "web_search_result_location", "search_result_location", "base64", "url", "content", "ephemeral",
  // OpenAI chat and Responses
  "input_text", "output_text", "input_image", "input_file", "input_audio", "function", "function_call",
  "function_call_output", "custom_tool_call", "computer_call", "computer_call_output", "web_search_call",
  "file_search_call", "code_interpreter_call", "code_interpreter_output", "image_generation_call", "mcp_call",
  "mcp_list_tools", "mcp_approval_request", "mcp_approval_response", "local_shell_call", "local_shell_call_output",
  "url_citation", "file_citation", "container_file_citation", "file_path", "summary_text", "logs", "message",
  "item_reference",
  // Google
  "media", "executable_code", "code_execution_result",
]);
const SIGNED = new Set(["reasoning", "thinking", "redacted_thinking"]);
// The names of the fields of the framework's objects. A key of a framework's object that is not one of them is data
// (a tool's row that has a type of a framework, { type: "text", "Kim Minsu": "open" }): known values are hidden in it.
const FRAME_KEYS = new Set([
  ...NOT_TEXT, ...OPTIONS, "text", "data", "value", "json", "input", "args", "arguments", "output", "outputs", "content",
  "source", "reasoning", "thinking", "summary", "refusal", "title", "context", "citations", "annotations",
  "cache_control", "extras", "kind", "filename", "status", "error", "is_error", "isError", "function", "function_call",
  "tool_calls", "invalid_tool_calls", "approved", "reason", "providerExecuted", "sourceType", "server_label",
  "partial_json", "format", "detail", "code", "language", "logs", "base64", "source_type", "file_data", "input_audio",
  "cited_text", "start_index", "end_index", "page_age", "encrypted_index",
]);

const slotOf = (types: readonly string[] | undefined, untyped = false): Slot => ({ types: types && new Set(types), untyped });
// AI SDK (provider specifications v2, v3 and v4): the parts of a message, the output of a tool result, the items of a
// "content" output, the data of a file part (v4)
const AI_SDK_PARTS = slotOf([
  "text", "file", "image", "reasoning", "reasoning-file", "redacted-reasoning", "tool-call", "tool-result", "tool-error",
  "tool-approval-request", "tool-approval-response", "source", "custom",
]);
const AI_SDK_OUTPUTS = slotOf(["text", "json", "error-text", "error-json", "content", "execution-denied"]);
const AI_SDK_ITEMS = slotOf([
  "text", "media", "file", "custom", "file-data", "file-url", "file-id", "file-reference", "image-data", "image-url",
  "image-file-id", "image-file-reference",
]);
const AI_SDK_FILE_DATA = slotOf(["data", "url", "reference", "text"]);
/** The AI SDK's objects in a prompt, the value given to the last pass being one message. */
export const AI_SDK_SHAPES: Shapes = {
  types: AI_SDK_TYPES,
  named: new Set(),
  files: new Set(["file", "image", "reasoning-file", "media", "file-data", "image-data", "data"]),
  slot(type, field) {
    if (type === undefined) return field === "content" ? AI_SDK_PARTS : undefined; // a message: its parts
    if (field === "output") return type === "tool-result" || type === "tool-error" ? AI_SDK_OUTPUTS : undefined;
    if (field === "value") return type === "content" ? AI_SDK_ITEMS : undefined;
    if (field === "data") return type === "file" || type === "reasoning-file" ? AI_SDK_FILE_DATA : undefined;
    return undefined;
  },
};
/** A message of the AI SDK: the value the AI SDK middleware gives the last pass. */
export const AI_SDK_MESSAGE: Slot = { types: new Set(), untyped: true };

// LangChain: content blocks hold blocks in the field of a tool's result (Anthropic tool_result, OpenAI
// function_call_output, ...), a document's source, an OpenAI tool call its function
const LANGCHAIN_BLOCKS = slotOf(undefined);
const LANGCHAIN_SOURCES = slotOf(["base64", "url", "text", "content", "file"]);
const LANGCHAIN_FUNCTION = slotOf([], true);
const CONTENT_HOLDERS = new Set([
  "tool_result", "mcp_tool_result", "web_search_tool_result", "web_fetch_tool_result", "code_execution_tool_result",
  "bash_code_execution_tool_result", "text_editor_code_execution_tool_result", "tool_search_tool_result",
  "search_result", "message", "content",
]);
const OUTPUT_HOLDERS = new Set(["function_call_output", "custom_tool_call_output", "computer_call_output"]);
const SOURCE_HOLDERS = new Set(["image", "document", "file", "audio", "video"]);
// In what came from outside (a tool's result, a user's message), the block types that may stand there and the fields
// that hold each one's own content (since 1.7.2, the review of 1.7.1: a storage API's rows typed "file" or "document"
// were taken for blocks, so the model read none of their fields and their id and url went out as written). An object
// of one of these types is a block only when one of its fields here is set; any other typed object there is a row of
// data. The block types a tool message takes (langchain-core TOOL_MESSAGE_BLOCK_TYPES), the standard and LangChain v0
// data blocks (url, base64, file_id; source_type with data, url or id), Anthropic (a document's or an image's source,
// a tool_result's tool_use_id, the sources base64, url, text and content, a file source's file_id), OpenAI chat and
// Responses (image_url, file, input_*, outputs, a message's role) and Google (media). The same table is in
// python/src/newmean_pii/langchain.py (_CONTENT_FIELDS).
const FILE_CONTENT = ["url", "base64", "file_id", "data", "source_type", "source", "file"];
/** The fields that hold the own content of each LangChain block type that what came from outside may hold. */
export const LANGCHAIN_CONTENT_FIELDS: ReadonlyMap<string, readonly string[]> = new Map<string, readonly string[]>([
  ["text", ["text", "data"]],
  ["text-plain", ["text", "url", "base64", "file_id"]],
  ["image", FILE_CONTENT],
  ["audio", FILE_CONTENT],
  ["video", FILE_CONTENT],
  ["file", FILE_CONTENT],
  ["image_url", ["image_url"]],
  ["document", ["source"]],
  ["json", ["json"]],
  ["search_result", ["content"]],
  ["custom_tool_call_output", ["output"]],
  ["function_call_output", ["output"]],
  ["computer_call_output", ["output"]],
  ["tool_result", ["tool_use_id"]],
  ["mcp_tool_result", ["tool_use_id"]],
  ["input_text", ["text"]],
  ["input_image", ["image_url", "file_id"]],
  ["input_file", ["file_id", "file_data", "file_url"]],
  ["input_audio", ["input_audio"]],
  ["media", ["data", "file_uri", "url"]],
  ["non_standard", ["value"]],
  ["container_upload", ["file_id"]],
  ["message", ["role"]],
  ["base64", ["data"]],
  ["url", ["url"]],
  ["content", ["content"]],
]);
/** Whether `x` (a typed object in what came from outside) is a block of LangChain or a provider: its type is one such a
 *  place may hold, and it has that type's own content. Otherwise it is a row of data, whatever type it gives itself. */
export const carriesContent = (x: Record<string, unknown>, content: ReadonlyMap<string, readonly string[]> = LANGCHAIN_CONTENT_FIELDS): boolean => {
  const fields = typeof x.type === "string" ? content.get(x.type) : undefined;
  return fields !== undefined && fields.some((f) => x[f] !== undefined && x[f] !== null);
};
/** LangChain's content blocks and the provider blocks it passes on. The same in python/src/newmean_pii/langchain.py. */
export const LANGCHAIN_SHAPES: Shapes = {
  content: LANGCHAIN_CONTENT_FIELDS,
  types: LANGCHAIN_TYPES,
  named: new Set([
    "tool_call", "tool_call_chunk", "invalid_tool_call", "server_tool_call", "server_tool_call_chunk", "tool_use",
    "server_tool_use", "mcp_tool_use", "function", "function_call", "custom_tool_call", "mcp_call", "mcp_approval_request",
    "mcp_list_tools",
  ]),
  files: new Set(["image", "audio", "video", "file", "base64", "media"]),
  slot(type, field) {
    if (type === undefined) return undefined;
    if (field === "content") return CONTENT_HOLDERS.has(type) ? LANGCHAIN_BLOCKS : undefined;
    if (field === "output") return OUTPUT_HOLDERS.has(type) ? LANGCHAIN_BLOCKS : undefined;
    if (field === "source") return SOURCE_HOLDERS.has(type) ? LANGCHAIN_SOURCES : undefined;
    if (field === "function") return type === "function" ? LANGCHAIN_FUNCTION : undefined;
    return undefined;
  },
};
/** The content of a LangChain message: its blocks. */
export const LANGCHAIN_CONTENT: Slot = LANGCHAIN_BLOCKS;
/** The content of a LangChain message that came from outside (a tool's result, a user's message): its blocks, which
 *  are blocks only when they carry their own content (`carriesContent`). */
export const LANGCHAIN_RECEIVED: Slot = { ...LANGCHAIN_BLOCKS, received: true };
/** The content of a user's message (HumanMessage, ChatMessage): received as `LANGCHAIN_RECEIVED` is, and a block's `id`
 *  there stays at its authorized path (release gate 4 of 1.7.1). */
export const LANGCHAIN_USER: Slot = { ...LANGCHAIN_BLOCKS, received: true, ids: true };
/** A LangChain message's tool calls (also those of OpenAI in additional_kwargs, { type: "function", function }). */
export const LANGCHAIN_TOOL_CALLS: Slot = slotOf(["tool_call", "tool_call_chunk", "invalid_tool_call", "function"], true);
/** The fields at the top of a LangChain message's additional_kwargs that are the framework's (OpenAI tool calls); the
 *  others are data. */
export const LANGCHAIN_KWARGS: ReadonlyMap<string, Slot> = new Map([
  ["function_call", LANGCHAIN_FUNCTION],
  ["tool_calls", LANGCHAIN_TOOL_CALLS],
]);
/** A string that is binary data and not text: a data URL, or base64 of some length, in one piece or in lines. (One
 *  character class and a bounded tail: the time grows with the length, whatever the string holds.) */
const binary = (s: string) => /^data:[^,]*;base64,/.test(s) || (s.length >= 64 && /^[A-Za-z0-9+/_\-\r\n]+={0,2}$/.test(s.trimEnd()));
/** A URL that names a file where a file's data stands (`https://...`, `gs://...`, `s3://...`): a scheme and "//", as
 *  the AI SDK takes such a string for a URL (`data:` URLs hold the bytes themselves and are read as data). */
const fileUrl = (s: string) => /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s]+$/.test(s);

// Fields of a call outside the messages (since 1.7.2). In a JSON schema the model reads descriptions, titles, comments
// and examples as text. Property names name the fields of a tool's input or of the answer, and literal constraints
// (`const`, `enum`, `default`, `pattern`) say what a value must be, also where a literal is an object with keys a schema
// has (`const: { description: "..." }`): both stay as written. The walk goes on only through the keywords that hold
// schemas (the 23rd outside review: it went through every object, and rewrote such a literal).
const SCHEMA_TEXT = new Set(["description", "title", "$comment"]);
const SCHEMA_EXAMPLES = new Set(["examples", "example"]);
// keywords whose value maps names to schemas (`dependencies` maps a name to a schema or to a list of property names)
const SCHEMA_MAPS = new Set(["properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"]);
// keywords whose value is a schema or a list of schemas
const SCHEMA_SUBSCHEMAS = new Set([
  "items", "prefixItems", "additionalItems", "unevaluatedItems", "contains", "additionalProperties", "unevaluatedProperties",
  "propertyNames", "if", "then", "else", "not", "allOf", "anyOf", "oneOf", "contentSchema",
]);
/** A JSON schema without the constraints a placeholder cannot meet (`format`, `pattern`), through the keywords that
 *  hold schemas; the same object when it has none (since 1.7.2, the review round's L3-B5). */
export function relaxedSchema(schema: unknown): unknown {
  // `x` (an array or a plain object) with each item or value mapped by `f`, without the keys `drop` names; the same
  // object when nothing changed
  const map = (x: unknown, f: (key: string, y: unknown) => unknown, drop?: ReadonlySet<string>): unknown => {
    if (Array.isArray(x)) {
      const out = x.map((y, i) => f(String(i), y));
      return out.some((y, i) => y !== x[i]) ? out : x;
    }
    if (!isRecord(x)) return x;
    let changed = false;
    const out: Record<string, unknown> = {};
    for (const [k, y] of Object.entries(x)) {
      if (drop?.has(k)) {
        changed = true;
        continue;
      }
      const next = f(k, y);
      if (next !== y) changed = true;
      // (defined, not assigned: a key "__proto__" is a key like any other)
      Object.defineProperty(out, k, { value: next, enumerable: true, writable: true, configurable: true });
    }
    return changed ? out : x;
  };
  const sub = (x: unknown): unknown => (Array.isArray(x) ? map(x, (_i, s) => sub(s)) : walk(x));
  const walk = (x: unknown): unknown =>
    map(x, (k, y) => (SCHEMA_MAPS.has(k) ? map(y, (_name, s) => sub(s)) : SCHEMA_SUBSCHEMAS.has(k) ? sub(y) : y), UNMET);
  return walk(schema);
}
// the constraints a placeholder cannot meet ("[DATE_OF_BIRTH_1]" in a date field, "[EMAIL_1]" in a field with a pattern)
const UNMET: ReadonlySet<string> = new Set(["format", "pattern"]);

// Provider options and model settings: what the provider receives outside the messages is data. Every string in them,
// every number that is a known value and every key counts (an `openai.user`, a `safetyIdentifier`, a template variable,
// a metadata key), except what a provider defines as an identifier, a credential or a numeric setting, and only at the
// exact path where that provider defines it (since 1.7.2, release gate 8: a name such as `previous_response_id` was kept
// wherever it stood under a provider the middleware knows, also inside `extra_body`, and `prompt.id` or
// `mcp_servers.*.name` were kept under AI SDK provider options of those names). The paths below are that list; the
// README lists the same ones.

/** A path from the top of the options or settings: a key or an index, "*" for any one key or index, "**" (last) for
 *  the value there and everything below it. */
type OptionPath = readonly string[];
/** The paths of one kind of options: identifiers and credentials (a string, number or boolean there is sent as written;
 *  the keys along the path stay too), numeric settings (a number there stays a number) and JSON schemas (read as
 *  `schema` reads them: property names and literal constraints as written; a library's schema object, Zod or Standard
 *  Schema, stays as it is, for the framework writes it). */
export interface OptionPaths {
  ids: readonly OptionPath[];
  numbers: readonly OptionPath[];
  schemas: readonly OptionPath[];
}
const under = (tops: readonly string[], paths: readonly OptionPath[]): OptionPath[] => tops.flatMap((t) => paths.map((p) => [t, ...p]));
const OPENAI = ["openai", "azure"];
const GOOGLE = ["google", "vertex"];
// The providers whose options the AI SDK has (`providerOptions.<name>`). Their numeric settings stay numbers directly
// under their name. A provider name that is not here is data, every key and value of it (an OpenAI-compatible provider
// spreads its options into the request body as they are).
const KNOWN_PROVIDERS: readonly string[] = [
  "openai", "azure", "anthropic", "google", "vertex", "bedrock", "mistral", "groq", "xai", "deepseek", "cohere",
  "perplexity", "fireworks", "togetherai", "cerebras", "deepinfra", "gateway", "openrouter", "openaiCompatible",
  "ollama", "huggingface", "baseten", "vercel", "alibaba", "moonshotai",
];
// The numbers a provider defines as settings (token limits, sampling, thinking budgets, retries): a number at one of
// these names stays a number whatever it is (a `maxTokens` of 4096 is not an application's 4096, and a string there
// would make the provider refuse the call), directly under a known provider (AI SDK) or at the top of the settings
// (LangChain), and nowhere else. Strings there are still read.
const NUMBER_NAMES: readonly string[] = [
  "maxTokens", "max_tokens", "maxOutputTokens", "max_output_tokens", "maxCompletionTokens", "max_completion_tokens",
  "max_tokens_to_sample", "temperature", "topP", "top_p", "topK", "top_k", "minP", "min_p", "seed", "frequencyPenalty",
  "frequency_penalty", "presencePenalty", "presence_penalty", "repetitionPenalty", "repetition_penalty", "n",
  "candidateCount", "candidate_count", "logprobs", "topLogprobs", "top_logprobs", "budgetTokens", "budget_tokens",
  "thinkingBudget", "thinking_budget", "maxToolCalls", "max_tool_calls", "maxRetries", "max_retries", "timeout",
  "dimensions", "numCtx", "num_ctx", "numPredict", "num_predict",
];
const numbersAt = (prefix: OptionPath): OptionPath[] => NUMBER_NAMES.map((n) => [...prefix, n]);
/** AI SDK provider options of a call (and of a tool), keyed by provider. */
const CALL_PATHS: OptionPaths = {
  ids: [
    // OpenAI (Responses): the previous response, the conversation (an id or { id }), the response a cache prefix is
    // compared with, a stored prompt's id and version, token ids and their biases, the tools a call may use by name
    ...under(OPENAI, [
      ["previousResponseId"], ["conversation"], ["conversation", "id"], ["promptCacheOptions", "comparisonResponseId"], ["prompt", "id"],
      ["prompt", "version"], ["logitBias", "*"], ["allowedTools", "toolNames", "*"],
    ]),
    // Anthropic: the container (an id, or its id and skills), MCP servers (name, URL, token, the tools allowed), the
    // models to fall back to
    ...under(["anthropic"], [
      ["container"], ["container", "id"], ["container", "skills", "*", "type"], ["container", "skills", "*", "skillId"],
      ["container", "skills", "*", "version"], ["mcpServers", "*", "type"], ["mcpServers", "*", "name"], ["mcpServers", "*", "url"],
      ["mcpServers", "*", "authorizationToken"], ["mcpServers", "*", "toolConfiguration", "allowedTools", "*"], ["fallbacks", "*", "model"],
    ]),
    // Google: cached content
    ...under(GOOGLE, [["cachedContent"]]),
    // Vercel AI Gateway and OpenRouter: the providers and models to route to, and the gateway's own provider credentials
    ...under(["gateway"], [["order", "*"], ["only", "*"], ["models", "*"], ["byok", "**"]]),
    ...under(["openrouter"], [["provider", "**"], ["models", "*"]]),
  ],
  numbers: [
    ...KNOWN_PROVIDERS.flatMap((p) => numbersAt([p])),
    ["anthropic", "thinking", "budgetTokens"], ["anthropic", "taskBudget", "total"], ["anthropic", "taskBudget", "remaining"],
    ["anthropic", "fallbacks", "*", "max_tokens"], ...under(GOOGLE, [["thinkingConfig", "thinkingBudget"]]), ["bedrock", "reasoningConfig", "budgetTokens"],
  ],
  schemas: [],
};
/** AI SDK provider options of a message or a part, keyed by provider: the identifiers and signatures of what the
 *  provider sent back (an item, signed or encrypted reasoning), and the tools a message adds or removes by name. */
const MESSAGE_PATHS: OptionPaths = {
  ids: [
    ...under(OPENAI, [["itemId"], ["reasoningEncryptedContent"], ["additionalTools", "*", "type"], ["additionalTools", "*", "name"]]),
    ...under(["anthropic"], [
      ["signature"], ["redactedData"], ["toolChanges", "*", "type"], ["toolChanges", "*", "toolName"], ["toolChanges", "*", "tool", "type"],
      ["toolChanges", "*", "tool", "id"], ["toolChanges", "*", "tool", "name"],
    ]),
    ...under(["bedrock"], [["signature"], ["redactedData"]]),
    ...under(GOOGLE, [["thoughtSignature"]]),
  ],
  numbers: [],
  schemas: [...under(OPENAI, [["additionalTools", "*", "parameters"]]), ["anthropic", "toolChanges", "*", "tool", "inputSchema"]],
};
/** The arguments of an AI SDK provider tool, keyed by the tool's id without its date ("anthropic.web_search" for
 *  "anthropic.web_search_20250305"): servers, connectors, files, vector stores, containers and domains it is given by
 *  name or id, and its credentials. */
const TOOL_PATHS: OptionPaths = {
  ids: [
    ...under(["openai.mcp"], [
      ["serverLabel"], ["serverUrl"], ["connectorId"], ["authorization"], ["headers", "*"], ["allowedTools", "*"], ["allowedTools", "toolNames", "*"],
      ["requireApproval"], ["requireApproval", "never", "toolNames", "*"],
    ]),
    ["openai.file_search", "vectorStoreIds", "*"],
    ["openai.code_interpreter", "container"], ["openai.code_interpreter", "container", "fileIds", "*"],
    ...under(["openai.web_search", "openai.web_search_preview"], [["filters", "allowedDomains", "*"], ["filters", "blockedDomains", "*"]]),
    ["openai.image_generation", "model"], ["openai.image_generation", "inputImageMask", "fileId"], ["openai.image_generation", "inputImageMask", "imageUrl"],
    ...under(["openai.shell"], [
      ["environment", "type"], ["environment", "containerId"], ["environment", "fileIds", "*"], ["environment", "networkPolicy", "allowedDomains", "*"],
      ["environment", "networkPolicy", "domainSecrets", "**"],
    ]),
    ["openai.custom", "name"],
    ...under(["anthropic.web_search", "anthropic.web_fetch"], [["allowedDomains", "*"], ["blockedDomains", "*"]]),
  ],
  numbers: [
    ["openai.file_search", "maxNumResults"], ["openai.file_search", "ranking", "scoreThreshold"], ["openai.image_generation", "outputCompression"],
    ["openai.image_generation", "partialImages"], ...under(["anthropic.web_search", "anthropic.web_fetch"], [["maxUses"]]),
    ["anthropic.web_fetch", "maxContentTokens"], ...under(["anthropic.computer"], [["displayWidthPx"], ["displayHeightPx"], ["displayNumber"]]),
  ],
  schemas: [["openai.tool_search", "parameters"]],
};
/** LangChain model settings and the request fields a model sends of its own, keyed by the request's fields (snake
 *  case, as the request body has them; LangChain's own camel-case call options where it takes them). */
const SETTINGS_PATHS: OptionPaths = {
  ids: [
    // the model, the models and providers to route to (OpenRouter's request fields)
    ["model"], ["models", "*"], ["provider", "**"],
    // OpenAI: the previous response, the conversation, a stored prompt's id and version
    ["previous_response_id"], ["conversation"], ["conversation", "id"], ["prompt", "id"], ["prompt", "version"],
    // Anthropic: the container, MCP servers
    ["container"], ["container", "id"], ["container", "skills", "*", "type"], ["container", "skills", "*", "skill_id"],
    ["container", "skills", "*", "version"], ["mcp_servers", "*", "type"], ["mcp_servers", "*", "name"], ["mcp_servers", "*", "url"],
    ["mcp_servers", "*", "authorization_token"], ["mcp_servers", "*", "tool_configuration", "allowed_tools", "*"],
    // Google: cached content
    ["cachedContent"], ["cached_content"],
    // the names of tools, functions and response formats, and which tool to call
    ["tools", "*", "type"], ["tools", "*", "name"], ["tools", "*", "function", "name"], ["functions", "*", "name"],
    ["response_format", "type"], ["response_format", "json_schema", "name"], ["text", "format", "type"], ["text", "format", "name"],
    ["tool_choice", "**"], ["function_call", "**"],
    // OpenAI's built-in tools given in the request: MCP servers and connectors, vector stores, containers, domains
    ...under(["tools"], [
      ["*", "server_label"], ["*", "server_url"], ["*", "connector_id"], ["*", "authorization"], ["*", "headers", "*"], ["*", "allowed_tools", "**"],
      ["*", "require_approval", "**"], ["*", "vector_store_ids", "*"], ["*", "container"], ["*", "container", "file_ids", "*"],
      ["*", "filters", "allowed_domains", "*"], ["*", "filters", "blocked_domains", "*"], ["*", "allowed_domains", "*"], ["*", "blocked_domains", "*"],
    ]),
    // token ids and their biases; the call's headers (credentials)
    ["logit_bias", "*"], ["logitBias", "*"], ["headers", "*"],
  ],
  numbers: [
    ...numbersAt([]), ["thinking", "budget_tokens"], ["thinking", "budgetTokens"], ["thinkingConfig", "thinkingBudget"],
    ["thinking_config", "thinking_budget"], ...numbersAt(["generationConfig"]), ...numbersAt(["generation_config"]),
  ],
  schemas: [
    ["response_format", "json_schema", "schema"], ["text", "format", "schema"], ["tools", "*", "parameters"], ["tools", "*", "input_schema"],
    ["tools", "*", "function", "parameters"], ["functions", "*", "parameters"], ["responseSchema"], ["response_schema"], ["responseJsonSchema"],
    ["response_json_schema"], ["output_format", "schema"], ["outputFormat", "schema"], ["output_config", "format", "schema"],
    ["outputConfig", "format", "schema"], ["generationConfig", "responseSchema"], ["generationConfig", "responseJsonSchema"],
    ["generation_config", "response_schema"], ["generation_config", "response_json_schema"],
  ],
};
/** The exact paths of each kind of options: AI SDK provider options of a call and of a message or a part, the
 *  arguments of AI SDK provider tools, and LangChain model settings (what the README lists; the tests plant a value at
 *  each path and away from it). */
export const OPTION_PATHS: Readonly<Record<"call" | "message" | "tools" | "settings", OptionPaths>> = {
  call: CALL_PATHS,
  message: MESSAGE_PATHS,
  tools: TOOL_PATHS,
  settings: SETTINGS_PATHS,
};
/** What the top keys of options are: provider names (AI SDK provider options), the request's fields (LangChain model
 *  settings), or the ids of provider tools whose arguments these are (AI SDK, `{ "openai.mcp": args }`). */
export type OptionsTop = "providers" | "settings" | "tools";

/** A tree of option paths: what a path ends at, and what continues it. */
interface PathNode {
  next: Map<string, PathNode>;
  id?: boolean;
  number?: boolean;
  schema?: boolean;
  /** "**": this path and everything below it. */
  rest?: boolean;
}
function pathTree(paths: OptionPaths): PathNode {
  const root: PathNode = { next: new Map() };
  const add = (p: OptionPath, kind: "id" | "number" | "schema") => {
    let n = root;
    for (const k of p) {
      let m = n.next.get(k);
      if (!m) n.next.set(k, (m = { next: new Map() }));
      n = m;
      if (k === "**") n.rest = true;
    }
    n[kind] = true;
  };
  for (const p of paths.ids) add(p, "id");
  for (const p of paths.numbers) add(p, "number");
  for (const p of paths.schemas) add(p, "schema");
  return root;
}
const PATH_TREES = new Map<string, PathNode>([
  ["call", pathTree(CALL_PATHS)],
  ["message", pathTree(MESSAGE_PATHS)],
  ["tools", pathTree(TOOL_PATHS)],
  ["settings", pathTree(SETTINGS_PATHS)],
]);
/** The nodes of the path tree that the path to a key's value reaches, from the nodes its object reached: the key
 *  itself, "*", a "**" that goes on, and a "**" that starts here (it stands for the value at its path too). */
function pathStep(nodes: readonly PathNode[], key: string): PathNode[] {
  const out: PathNode[] = [];
  const reach = (n: PathNode | undefined) => {
    if (!n) return;
    out.push(n);
    const rest = n.next.get("**");
    if (rest) out.push(rest);
  };
  for (const n of nodes) {
    if (n.rest) out.push(n);
    reach(n.next.get(key));
    reach(n.next.get("*"));
  }
  return out;
}
/** A schema object of a library (Zod, a Standard Schema) and not JSON: the framework writes it as a JSON schema. */
export const librarySchema = (x: unknown): boolean =>
  x !== null && typeof x === "object" && !Array.isArray(x) && !isRecord(x) && ("~standard" in x || "_def" in x || "_zod" in x);
// What LangChain reads itself among the settings of a call and does not send (an abort signal, callbacks, the run's
// config, tracing of a structured output): kept as it is, not data.
const SETTING_RUNTIME: ReadonlySet<string> = new Set([
  "signal", "callbacks", "configurable", "runName", "runId", "recursionLimit", "maxConcurrency", "timeout", "store",
  "writer", "context", "ls_structured_output_format",
]);

/** `x` (an array or a plain object) with each item or value mapped by `f`; the same object when nothing changed. Keys
 *  stay as they are; anything else is returned as it is. */
const mapped = (x: unknown, f: (key: string, value: unknown) => unknown): unknown => {
  if (Array.isArray(x)) {
    const out = x.map((y, i) => f(String(i), y));
    return out.some((y, i) => y !== x[i]) ? out : x;
  }
  if (!isRecord(x)) return x;
  let changed = false;
  const out: Record<string, unknown> = {};
  for (const [k, y] of Object.entries(x)) {
    const next = f(k, y);
    if (next !== y) changed = true;
    // (defined, not assigned: a key "__proto__" in parsed JSON is a key like any other)
    Object.defineProperty(out, k, { value: next, enumerable: true, writable: true, configurable: true });
  }
  return changed ? out : x;
};

const CACHE_MAX = 256;

/** `value` as the JSON it gives (what a provider would send for it: a class instance's own enumerable fields, what
 *  `toJSON` gives, a `Date` as its ISO text), built once by `plainTree`: `value` itself only when it is plain JSON data,
 *  which reads the same each time. Input JSON cannot write fails with a TypeError (since 1.7.2, release gate 8: a value
 *  JSON could not write, and a plain object whose getters were read to tell, was given back as it was, and read again
 *  by the provider's writer). */
export const jsonForm = (value: unknown): unknown => plainTree(value);

/** Binary data a framework has as the content of a file (bytes, a URL object): it stays as it is where a file's data
 *  stands (a file part's `data`, a block's `data` or `url`), and is data written as JSON writes it anywhere else. */
const isBinary = (x: unknown): boolean =>
  ArrayBuffer.isView(x) || x instanceof ArrayBuffer || (typeof URL === "function" && x instanceof URL) || (typeof Blob === "function" && x instanceof Blob);

export interface TreeOptions {
  /** Where a framework's objects stand: the file data of its objects (bytes, a URL object) stays as it is, and a class
   *  instance where one of its objects stands also gives the fields its class gives by getters, which converters read
   *  by name (`get text()`). Without it the value is data at every depth. */
  how?: SweepOptions;
  /** A value that is not data and stays as it is (an option the framework reads itself, a schema of a library), by
   *  its path from the top. */
  keep?: (path: readonly (string | number)[], value: unknown) => boolean;
  /** The name of the value in the error, for the path ("providerOptions"). */
  where?: string;
  /** The trees already built in this call, by the object (`MaskContext.trees`; only without `how`): an object met again
   *  gives the tree of its first read (its `toJSON` is not called again, its getters are not read again), walked as this
   *  call walks it, and an object read here for the first time that is not plain data leaves its tree there. */
  memo?: Map<object, unknown>;
}

const GONE: unique symbol = Symbol("gone");

type TypeTest = (x: unknown) => boolean;
/** Node's `util.types` where the runtime has it (`process.getBuiltinModule`): what only the runtime can tell without
 *  reading the object (a Proxy, a boxed primitive's internal slot). undefined elsewhere. */
const runtimeTypes: Partial<Record<"isProxy" | "isBoxedPrimitive" | "isNumberObject" | "isStringObject" | "isBooleanObject" | "isBigIntObject", TypeTest>> | undefined =
  (() => {
    try {
      const get = (globalThis as { process?: { getBuiltinModule?: (id: string) => unknown } }).process?.getBuiltinModule;
      return typeof get === "function" ? (get("node:util") as { types?: Record<string, TypeTest> } | undefined)?.types : undefined;
    } catch {
      return undefined;
    }
  })();

/** Whether `x` is a Proxy, where the runtime can tell (Node's `util.types.isProxy`; false elsewhere): a Proxy can give
 *  one value to the masking and another to the provider's writer, so it is never sent as it is. */
const isProxy: (x: object) => boolean = (() => {
  const f = runtimeTypes?.isProxy;
  return typeof f === "function" ? (x: object) => f(x) : () => false;
})();

/** Whether `read`, what reading the field `k` of `o` gave, is what `o` stores there, so that a writer that reads it
 *  again gets it too: a value field (of its own or of its prototypes) holding `read`, of an object that is not a Proxy
 *  where the runtime can tell. A getter, a Proxy, or a field whose read gave another value than its descriptor holds
 *  (a Proxy the runtime cannot tell) is not: the object is then copied with what was read (since 1.7.2, the
 *  confirmation of release gate 8). */
export const storedAs = (o: object, k: string, read: unknown): boolean => {
  for (let p: object | null = o; p !== null; p = Object.getPrototypeOf(p)) {
    if (isProxy(p)) return false; // (also a prototype that is one: it answers the read through its traps)
    const d = Object.getOwnPropertyDescriptor(p, k);
    if (d !== undefined) return "value" in d && Object.is(d.value, read);
  }
  return read === undefined;
};

const NOT_BOXED: unique symbol = Symbol("not boxed");
/** What `JSON.stringify` makes of a boxed primitive (`new Number(4524)`, `new String("Kim")`, `new Boolean(false)`,
 *  `Object(1n)`), told as it tells one, by the internal slot and not by the prototype: ToNumber or ToString of it (its
 *  `valueOf` or `toString` runs, as there), the slot's own value for a boolean or a BigInt. NOT_BOXED for any other
 *  object: an object made from `Number.prototype`, and a Proxy (which has no such slot), are objects to JSON. Where the
 *  runtime cannot tell the slot without reading (not Node), the builtin tag names the candidate and the slot's own
 *  `valueOf` confirms it. Throws what ToNumber or ToString throws. */
const unboxed: (x: object) => unknown = (() => {
  const t = runtimeTypes;
  const { isBoxedPrimitive: boxed, isNumberObject: num, isStringObject: str, isBooleanObject: bool, isBigIntObject: big } = t ?? {};
  if (boxed && num && str && bool && big)
    return (x: object): unknown => {
      if (!boxed(x)) return NOT_BOXED;
      if (num(x)) return +(x as unknown as number); // (ToNumber: a BigInt from valueOf throws, as there)
      if (str(x)) return String(x);
      if (bool(x)) return Boolean.prototype.valueOf.call(x);
      if (big(x)) return BigInt.prototype.valueOf.call(x);
      return NOT_BOXED; // (a Symbol object is an object to JSON)
    };
  const tag = Object.prototype.toString;
  const slot = (f: () => unknown): boolean => {
    try {
      f();
      return true;
    } catch {
      return false;
    }
  };
  return (x: object): unknown => {
    switch (tag.call(x)) {
      case "[object Number]":
        return slot(() => Number.prototype.valueOf.call(x)) ? +(x as unknown as number) : NOT_BOXED;
      case "[object String]":
        return slot(() => String.prototype.valueOf.call(x)) ? String(x) : NOT_BOXED;
      case "[object Boolean]":
        return slot(() => Boolean.prototype.valueOf.call(x)) ? Boolean.prototype.valueOf.call(x) : NOT_BOXED;
      case "[object BigInt]":
        return typeof BigInt === "function" && slot(() => BigInt.prototype.valueOf.call(x)) ? BigInt.prototype.valueOf.call(x) : NOT_BOXED;
      default:
        return NOT_BOXED;
    }
  };
})();

/** ToLength of a list's `length` as `JSON.stringify` takes it (ToNumber, then a whole number from 0 to 2^53 - 1). */
const toLength = (v: unknown): number => {
  const n = typeof v === "number" ? v : +(v as number); // (ToNumber: a BigInt or a Symbol throws, as there)
  if (!(n > 0)) return 0; // (NaN too)
  return Math.min(Math.trunc(n), Number.MAX_SAFE_INTEGER);
};

/** `value` built once into the plain JSON tree a provider writes of it (since 1.7.2, release gate 4: "each data slot
 *  is materialized once as provider-compatible JSON, and that tree is sent"), read the way `JSON.stringify` reads it
 *  (since 1.7.2, release gate 8: a Proxy gave the provider's writer through its `get` another value than the one its
 *  descriptor held, and the walker had read the descriptor). A value: `toJSON` read once and, when it is a function,
 *  called once and what it gives used in its place; a boxed primitive by its internal slot (its `valueOf` or
 *  `toString` runs as there); `NaN` and `Infinity` as null; `undefined`, functions and symbols left out of objects and
 *  null in lists. A list: its `length` read once, then each index read once, in order. An object: its own keys, then
 *  each string key's descriptor for whether it is enumerable, then the value of each enumerable key read once by
 *  property access, in order. Every read goes through property access, so a getter runs and a Proxy's traps answer as
 *  they do for `JSON.stringify` (a class instance by its own enumerable fields, a `Date` as its ISO text, `Map`, `Set`
 *  and bytes as JSON writes them), and what was read is what is masked and sent. Data is read depth first in JSON's
 *  order; where a framework's object may stand (`how`), its fields are read first, since its `type` decides how each is
 *  read. A list or an object that is not plain data (a getter, at an index too, a hole, a field of its own that JSON
 *  does not write, a Proxy where the runtime can tell, a field whose read gave another value than its descriptor holds)
 *  is rebuilt from what was read. Plain JSON data is returned as it is (the same objects).
 *  Input JSON cannot write fails with a clear TypeError before anything is sent: a BigInt (as in `JSON.stringify`, unless
 *  `BigInt.prototype.toJSON` writes it), an object that refers to itself, a `toJSON`, a getter or a Proxy trap that
 *  throws. */
export function plainTree(value: unknown, o: TreeOptions = {}): unknown {
  const { how, keep } = o;
  const path: (string | number)[] = [];
  const open = new Set<object>();
  const fail = (why: string, e?: unknown): never => {
    const at = [o.where ?? "", ...path].filter((p) => p !== "").join(".");
    const cause = e === undefined ? "" : `: ${e instanceof Error ? e.message : String(e)}`;
    throw new TypeError(`[@newmean-ai/pii] ${at === "" ? "a value of the call" : at} cannot be written as JSON (${why}${cause}), so the call is not sent`);
  };
  /** `f()`, a step of reading `x` that runs its code (a getter, a Proxy's trap); `k`: the field read, for the error. */
  const step = <R>(f: () => R, why: string, k?: string | number): R => {
    try {
      return f();
    } catch (e) {
      if (k !== undefined) path.push(k);
      return fail(why, e);
    }
  };
  /** The value of `k` in `x`, read once by property access ([[Get]]: a getter of its own or of its prototypes, a
   *  Proxy's `get`), as `JSON.stringify` reads it. */
  const read = (x: object, k: string | number): unknown => step(() => (x as Record<string | number, unknown>)[k], "reading it throws", k);
  // the slots inside an object that stands in what came from outside are in it too (as in the last pass)
  const received = new Map<Slot, Slot>();
  const within = (inner: Slot | undefined, outer: Slot | undefined): Slot | undefined => {
    if (!inner || !outer?.received || inner.received) return inner;
    let out = received.get(inner);
    if (!out) received.set(inner, (out = { ...inner, received: true }));
    return out;
  };
  // (the trees of this call: an object met again gives the tree of its first read)
  const memo = how ? undefined : o.memo;
  /** The tree of `x`, the value of `key` (`slot`: where the framework's objects of that slot stand); GONE for what JSON
   *  leaves out of an object (undefined, a function, a symbol). */
  const walk = (x: unknown, key: string, slot: Slot | undefined, fields = false): unknown => {
    if (keep && keep(path, x)) return x;
    if (memo === undefined || x === null || typeof x !== "object") return build(x, key, slot, fields);
    if (memo.has(x)) {
      // (walked as this reader walks it: what another kept as it is, a client in a model's field, is built here)
      const first = memo.get(x);
      return first !== null && typeof first === "object" ? walk(first, key, slot, fields) : first;
    }
    const tree = build(x, key, slot, fields);
    if (tree !== x) memo.set(x, tree);
    return tree;
  };
  const build = (x: unknown, key: string, slot: Slot | undefined, fields: boolean): unknown => {
    let called = false;
    if ((x !== null && typeof x === "object") || typeof x === "function" || typeof x === "bigint") {
      // its toJSON read once (a getter or a Proxy's `get` answers), and called once with the key when it is a function
      // (a function's too: JSON writes a function with a toJSON as what that gives)
      const f = step(() => (x as { toJSON?: unknown }).toJSON, "reading its toJSON failed");
      if (typeof f === "function") {
        const self = x;
        x = step(() => f.call(self, key), "its toJSON() throws");
        called = true;
      }
    }
    if (x !== null && typeof x === "object") {
      // a boxed primitive, by its internal slot (as JSON tells one; a Proxy around one is an object)
      const self = x;
      const b = step(() => unboxed(self), "its valueOf() or toString() throws");
      if (b !== NOT_BOXED) x = b;
    }
    switch (typeof x) {
      case "string":
      case "boolean":
        return x;
      case "number":
        return Number.isFinite(x) ? x : null;
      case "bigint":
        return fail("a BigInt");
      case "undefined":
      case "function":
      case "symbol":
        return GONE;
    }
    if (x === null) return null;
    const obj = x as object;
    if (open.has(obj)) fail("it refers to itself");
    open.add(obj);
    try {
      // (IsArray sees through a Proxy, as for JSON; a revoked one throws)
      return step(() => Array.isArray(obj), "reading it throws") ? list(obj, called, slot) : record(obj, called, slot, fields);
    } finally {
      open.delete(obj);
    }
  };
  const list = (a: object, called: boolean, slot: Slot | undefined): unknown => {
    // as JSON writes a list: its length read once (ToLength), then each index read once, in order
    const length = read(a, "length");
    const n = step(() => toLength(length), "its length is not a number", "length");
    // A list is sent as it is only when it is plain data: its own items, each a field with a value, and nothing else of
    // its own (since 1.7.2, release gate 8: a getter at an index gave the provider's writer another item than the one
    // that was read and masked). Any other list is rebuilt from the items read here, each once. (The descriptors of an
    // ordinary list are read without running anything; a Proxy's are not read: it is rebuilt.)
    let fresh = called || isProxy(a) || Object.getPrototypeOf(a) !== Array.prototype || Reflect.ownKeys(a).length !== n + 1;
    const out: unknown[] = [];
    for (let i = 0; i < n; i++) {
      const d = fresh ? undefined : Object.getOwnPropertyDescriptor(a, i);
      // (a getter runs once; a hole is read as JSON reads it, through the prototypes)
      const y = read(a, i);
      if (!fresh && (d === undefined || !("value" in d) || !Object.is(d.value, y))) fresh = true;
      path.push(i);
      let z = walk(y, String(i), slot);
      path.pop();
      if (z === GONE) z = null;
      if (z !== y) fresh = true;
      out.push(z);
    }
    return fresh ? out : a;
  };
  const record = (x: object, called: boolean, slot: Slot | undefined, top: boolean): unknown => {
    const proxy = isProxy(x);
    // The keys JSON writes, as it finds them: the object's own keys once, then the descriptor of each string key once,
    // for whether it is enumerable (EnumerableOwnProperties, which a Proxy answers through its traps)
    const own = step(() => Reflect.ownKeys(x), "reading its keys throws");
    const keys: string[] = [];
    const descriptors: PropertyDescriptor[] = [];
    // (an object with a getter among the fields of its own that JSON does not write, a symbol or a field that is not
    // enumerable, or a Proxy, is rebuilt from what is read here: a writer that reads such a field by name cannot get
    // anything else. Other such fields hold values, which stay: zod's JSON schemas have a "~standard" field)
    let hiddenGetter = false;
    let symbols = false;
    for (const k of own) {
      if (typeof k !== "string") {
        symbols = true;
        continue;
      }
      const d = step(() => Object.getOwnPropertyDescriptor(x, k), "reading its fields throws", k);
      if (d === undefined) continue;
      if (d.enumerable) {
        keys.push(k);
        descriptors.push(d);
      } else if (!("value" in d)) hiddenGetter = true;
    }
    // a class instance is rebuilt (where the framework's objects stand, a Proxy's prototype is read too: a class
    // instance there also gives the block fields its class gives by getters)
    const byName = how !== undefined && slot !== undefined && !called;
    let fresh = called || proxy || hiddenGetter;
    let plainProto = true;
    if (!fresh || byName) {
      const proto = step(() => Object.getPrototypeOf(x), "reading its prototype throws");
      plainProto = proto === Object.prototype || proto === null;
      if (!plainProto) fresh = true;
    }
    if (!fresh && symbols)
      fresh = Object.getOwnPropertySymbols(x).some((k) => {
        const d = Object.getOwnPropertyDescriptor(x, k);
        return d !== undefined && !("value" in d);
      });
    /** The value of the i-th key, read once; a field that is not a value, or whose read gave another value than its
     *  descriptor holds (a Proxy where the runtime cannot tell), makes the object be rebuilt. */
    const take = (i: number): unknown => {
      const y = read(x, keys[i]);
      const d = descriptors[i];
      if (!("value" in d) || !Object.is(d.value, y)) fresh = true;
      return y;
    };
    // a class instance where a framework's object stands (at the top of `how.fields` too)
    const byNameFields = byName && !plainProto;
    // Where a framework's object may stand, its fields are read first: its type decides how each is read. Data is read
    // and built field by field, in JSON's order.
    const first = how !== undefined && slot !== undefined && (!top || byNameFields);
    const values: unknown[] = first ? keys.map((_k, i) => take(i)) : [];
    // a class instance where a framework's object stands: also the block fields its class gives by getters, which
    // converters read by name (blockForm)
    if (byNameFields) {
      for (const k of READ_BY_NAME) {
        if (keys.includes(k)) continue; // (a field JSON writes; one that is not enumerable is read here)
        let y: unknown;
        try {
          y = (x as Record<string, unknown>)[k];
        } catch {
          continue; // (a getter that throws gives nothing a converter could send)
        }
        if (y === undefined || typeof y === "function" || typeof y === "symbol") continue;
        keys.push(k);
        values.push(y);
      }
    }
    const get = (k: string) => {
      const i = keys.indexOf(k);
      return i < 0 ? undefined : values[i];
    };
    // which of the framework's objects it is, as the last pass reads it: undefined for data
    let kind: "framework" | "fields" | undefined;
    let type: string | undefined;
    if (how && top) kind = "fields";
    else if (first) {
      const t = get("type");
      const content = how.shapes.content;
      if (typeof t !== "string") kind = slot.untyped ? "framework" : undefined;
      else if (slot.received && content && !(content.get(t) ?? []).some((f) => get(f) !== undefined && get(f) !== null)) kind = undefined;
      else if ((slot.types ?? how.shapes.types).has(t)) [kind, type] = ["framework", t];
    }
    const out: unknown[] = new Array(keys.length);
    for (let i = 0; i < keys.length; i++) {
      const k = keys[i];
      const y = first ? values[i] : take(i);
      let z: unknown;
      // a file's data (bytes, a URL object) where the framework has it stays as it is
      if (kind === "framework" && ((k === "data" && type !== undefined && how!.shapes.files.has(type)) || k === "url") && isBinary(y)) z = y;
      else {
        const sub = kind === "framework" ? within(how!.shapes.slot(type, k), slot) : kind === "fields" ? how!.fields!.get(k) : undefined;
        path.push(k);
        z = walk(y, k, sub);
        path.pop();
      }
      if (z !== y) fresh = true;
      out[i] = z;
    }
    if (!fresh) return x;
    const tree: Record<string, unknown> = {};
    // (defined, not assigned: a key "__proto__" is a key like any other)
    for (let i = 0; i < keys.length; i++) if (out[i] !== GONE) Object.defineProperty(tree, keys[i], { value: out[i], enumerable: true, writable: true, configurable: true });
    return tree;
  };
  const out = walk(value, "", how?.root, how?.fields !== undefined);
  return out === GONE ? undefined : out;
}

/** The placeholders of a record the middleware sends instead of what the app passed back (`given`: an earlier answer, its
 *  reasoning or a tool call's input, restored), that the earlier call restored in it: the record with them restored is
 *  `given` (`kind`: how the earlier call restored it, as an answer's text, as reasoning or as tool input). They are put
 *  into this call by the middleware, as they would be if it masked `given` again, so they count as this call's own and
 *  an answer to them is restored (since 1.7.2, the review of 1.7.1: after a reload from `toJSON()`, which keeps the
 *  records, they were not, while after one from `entries()` they were). Placeholders the record holds as `given` holds
 *  them (made up by the model, of another call) do not count; when the record does not restore to `given`, none does. */
export function recordTags(v: Vault, record: unknown, given: unknown, kind: "answer" | "text" | "value"): string[] {
  const strings: string[] = [];
  const collect = (x: unknown): void => {
    if (typeof x === "string") strings.push(x);
    else if (Array.isArray(x)) x.forEach(collect);
    else if (isRecord(x)) Object.values(x).forEach(collect);
  };
  collect(record);
  let shown: string;
  try {
    shown = typeof given === "string" ? given : (JSON.stringify(given) ?? "");
  } catch {
    return []; // (what JSON cannot write was not restored by the middleware)
  }
  const tags = [...new Set(strings.flatMap(vaultTags))].filter((t) => v.value(t) !== undefined && !shown.includes(t));
  if (tags.length === 0) return tags;
  const only = v.bindings(tags);
  const restored =
    kind === "answer" && typeof record === "string"
      ? restoreAnswer(v, record, only)
      : kind === "text" && typeof record === "string"
        ? v.restore(record, { only })
        : v.restoreValue(record, { only });
  const same = typeof given === "string" ? restored === given : JSON.stringify(restored) === shown;
  return same ? tags : [];
}

/** Whether `x` is an object that JSON writes in a form of its own: a class instance, or an object with `toJSON` (binary
 *  data aside: bytes are not text). Such an object is read as `jsonForm` gives it. */
export const writtenOtherwise = (x: unknown): boolean =>
  x !== null &&
  typeof x === "object" &&
  !Array.isArray(x) &&
  !ArrayBuffer.isView(x) &&
  !(x instanceof ArrayBuffer) &&
  (!isRecord(x) || typeof (x as { toJSON?: unknown }).toJSON === "function");

// The fields of a block that frameworks and their provider converters read by name, not through JSON: its type, text,
// reasoning, tool input and results, a document's source, and the own content of each block type.
const READ_BY_NAME: readonly string[] = [
  ...new Set(["type", "text", "reasoning", "thinking", "input", "args", "arguments", "content", "output", "source", ...[...LANGCHAIN_CONTENT_FIELDS.values()].flat()]),
];

/** `x`, an object where the framework's blocks may stand, as a provider is sent it: the JSON it gives (`jsonForm`), and
 *  for a class instance without `toJSON` also the block fields that JSON leaves out and converters read by name (a
 *  getter of its class, `get text()`), so that what masking reads is what a converter sends (since 1.7.2, the 24th
 *  outside review, A2). */
export const blockForm = (x: unknown): unknown => {
  const json = jsonForm(x);
  if (json === x || !isRecord(json) || typeof (x as { toJSON?: unknown }).toJSON === "function") return json;
  let out: Record<string, unknown> | undefined;
  for (const k of READ_BY_NAME) {
    if (Object.prototype.hasOwnProperty.call(json, k)) continue;
    let y: unknown;
    try {
      y = (x as Record<string, unknown>)[k];
    } catch {
      continue; // (a getter that throws gives nothing a converter could send)
    }
    if (y === undefined || typeof y === "function" || typeof y === "symbol") continue;
    (out ??= { ...json })[k] = jsonForm(y);
  }
  return out ?? json;
};

export function createMasker(options: MaskerOptions): Masker {
  let redactor: Promise<Redactor> | undefined = options.redactor === undefined ? undefined : Promise.resolve(options.redactor);
  const { vault } = options;
  // A vault numbers every span so that the answer can get the values back: another way of replacing them cannot be
  // honoured, and is refused rather than ignored (Python raises for its strategy the same way). Since 1.7.2.
  const r = options.redact;
  if (vault && r && ((r.mode !== undefined && r.mode !== "tag") || r.replace !== undefined || r.maskChar !== undefined || (r.vault !== undefined && r.vault !== vault)))
    throw new TypeError('[@newmean-ai/pii] a vault numbers the placeholders and restores the answer: `redact` can only be { mode: "tag" } with it');
  // without a vault: the masked text; with one: the spans the model found (the vault is applied again on every call, so
  // values it learned later are covered too). Both are kept per detector setting (sensitivity) and vault generation.
  const texts = new Map<string, Promise<string>>();
  const found = new Map<string, Promise<Span[]>>();
  const ready = () => (redactor ??= createRedactor(options.create));
  const stamp = (r: Redactor, text: string) => `${vault?.generation ?? ""}|${(r as Partial<Redactor>).sensitivity ?? ""}|${text}`;
  const cached = <T>(cache: Map<string, Promise<T>>, key: string, make: () => Promise<T>): Promise<T> => {
    let out = cache.get(key);
    if (out !== undefined) cache.delete(key); // most recently used last
    else {
      out = make();
      out.catch(() => cache.delete(key)); // a failed call is not remembered
    }
    cache.set(key, out);
    if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value as string);
    return out;
  };

  /** What goes to the model for `text`: the model's own wording when this is model text the middleware remembered. Only
   *  text a model wrote is looked up; a user message or tool result with the same wording is read as received. */
  const source = (v: Vault, text: string, c: MaskContext): { text: string; model: boolean; loose: boolean; recorded?: string[] } => {
    const model = c.model === true;
    const kept = model && c.recall !== false ? v.recall(text) : undefined;
    // (the placeholders the earlier call restored in it are this call's own: an answer to them is restored)
    if (typeof kept === "string") return { text: kept, model: true, loose: false, recorded: recordTags(v, kept, text, "answer") };
    // the model's wording as it was remembered needs no loose search; model text without a record does
    return { text, model, loose: model && c.recall !== false };
  };
  /** Text that a model did not write, and model text without a record (searched loosely), read through the escapes in
   *  it, when it has any ("Jos\u00e9", and Python repr escapes): the reading the model is given. (Not both readings: in
   *  the text as written the model takes pieces of escapes for values.) The values the vault already holds are searched
   *  in the text as it is written as well (`redactReadings`). A model's own wording (its record) and a model's JSON
   *  document (an earlier structured answer, whose numbers are quoted first) are given as written. */
  const reading = (s: { text: string; model: boolean; loose: boolean }) =>
    s.model && (!s.loose || jsonDocument(s.text) !== undefined) ? undefined : jsonEscapesView(s.text);
  /** What the model finds in `read`, remembered per detector setting and vault generation. */
  const detected = async (read: string): Promise<Span[]> => {
    if (read.trim() === "") return [];
    const r = await ready();
    // a stand-in redactor with only redact() (a test double, a wrapper) cannot be used with a vault
    if (typeof r.detect !== "function") throw new TypeError("[@newmean-ai/pii] with a vault, the redactor must have detect()");
    return cached(found, stamp(r, read), () => r.detect(read));
  };
  /** `s` masked. `spans` are of the reading the model was given: the text as it is written (`through` undefined), or
   *  its reading through Unicode escapes (`through`, with those spans; then `spans` is empty). Without the model: no
   *  spans. */
  const apply = (
    v: Vault,
    s: { text: string; model: boolean; loose: boolean; recorded?: readonly string[] },
    spans: Span[],
    c: MaskContext,
    through: { view: NonNullable<ReturnType<typeof jsonEscapesView>>; found: Span[] } | undefined = (() => {
      const view = reading(s);
      return view ? { view, found: [] } : undefined;
    })(),
  ) => {
    vaultUnchanged(v, c.generation ?? v.generation);
    // a model's text that is a JSON document (an earlier structured answer) stays valid JSON: a number in it that holds
    // something to mask is made a string first, so that its placeholder is quoted
    const quoted = s.model && !through ? quoteNumbers(s.text, spans, v, s.loose) : undefined;
    if (quoted) {
      s = { ...s, text: quoted.text };
      spans = quoted.found;
    }
    // A model's text keeps this vault's placeholders, a system prompt every placeholder-like part of it; in other text
    // they are all escaped.
    const tags = s.model || c.system ? vaultTags(through ? through.view.text : s.text) : [];
    const keep = s.model ? new Set(tags.filter((t) => v.value(t) !== undefined)) : c.system ? new Set(tags) : undefined;
    const out = redactReadings(s.text, spans, v, { keep, loose: s.loose, through });
    if (c.tags) for (const t of [...out.tags, ...(s.recorded ?? [])]) c.tags.add(t);
    return s.model && !through ? distinctModelKeys(s.text, out.text) : out.text;
  };
  /** A model's text that is a JSON document (an earlier structured answer), `before` and `after` it was masked as text:
   *  the keys of one object that masking made the same are told apart again as in a received document (" #2"), and keys
   *  that were the same before stay the same (since 1.7.2, the review of 1.7.1: two keys became one, and a JSON reader
   *  kept one field). When masking changed the document's shape it is left as it is. */
  const distinctModelKeys = (before: string, after: string): string => {
    if (after === before) return after;
    const was = jsonDocument(before);
    if (!was || !was.some((t) => t.kind === "key")) return after;
    const now = jsonDocument(after);
    if (!now || now.length !== was.length || now.some((t, i) => t.kind !== was[i].kind || t.scope !== was[i].scope)) return after;
    const values = distinctJsonKeys(was, now.map((t) => t.value));
    return values.some((x, i) => x !== now[i].value) ? replaceJsonStrings(after, now, values) : after;
  };

  /** Values of one received document, masked with one detection over all of them: each on a line of its own after its
   *  key ("name: Kim Minsu"), which gives a value the context a model needs and keeps JSON syntax out of it. A value
   *  that holds Unicode escapes is given in its reading through them. A number is compared and remembered in its plain
   *  form and masked whole. A string that is itself a JSON document is read on its own in the same way. */
  const leaves = async (v: Vault, items: readonly { key?: string; value: string; number?: boolean }[], c: MaskContext, detect: boolean): Promise<string[]> => {
    const ctx = { ...c, model: false, generation: c.generation ?? v.generation };
    const apart = items.map((it) => (!it.number && it.value !== "" && jsonDocument(it.value) !== undefined ? masker.received(it.value, c, detect) : undefined));
    // (they are waited for at the end: if reading the document fails first, a failure in one of them is not left unhandled)
    for (const p of apart) p?.catch(() => {});
    const views = items.map((it, i) => (apart[i] || it.number ? undefined : jsonEscapesView(it.value)));
    // the document as the model reads it: a value that holds Unicode escapes in that reading
    const starts: number[] = [];
    let doc = "";
    items.forEach((it, i) => {
      if (apart[i]) return void starts.push(-1);
      doc += (doc === "" ? "" : "\n") + (it.key !== undefined && it.key !== "" ? `${it.key}: ` : "");
      starts.push(doc.length);
      doc += views[i]?.text ?? it.value;
    });
    const spans = detect ? await detected(doc) : [];
    /** The spans inside the value that starts at `a`, counted from its start (values are asked for in order). */
    const ownSpans = (all: Span[]) => {
      let p = 0;
      return (a: number, length: number) => {
        while (p < all.length && all[p].end <= a) p++;
        const out: Span[] = [];
        for (let q = p; q < all.length && all[q].start < a + length; q++) {
          if (all[q].end > a) out.push({ ...all[q], start: Math.max(all[q].start, a) - a, end: Math.min(all[q].end, a + length) - a });
        }
        return out;
      };
    };
    const own = ownSpans(spans);
    // (in order, one after another: the vault numbers the values as they come)
    return Promise.all(items.map((it, i) => {
      if (apart[i]) return apart[i]!;
      if (it.value === "") return "";
      const view = views[i];
      const mine = own(starts[i], (view ? view.text : it.value).length); // (of the reading, when the value has one)
      if (it.number) {
        // the same number however it is written: "8.21023456789e11" is 821023456789
        vaultUnchanged(v, ctx.generation);
        const whole = wholeNumber(v, it.value, ctx);
        if (whole !== undefined) return whole;
        // (a number shorter than the vault searches for is held only to be restored: `tagFound`)
        const tag = mine.length > 0 ? tagFound(v, mine[0].label, plainNumber(it.value)) : undefined;
        if (tag !== undefined) c.tags?.add(tag);
        return tag ?? it.value;
      }
      const known = wholeKnown(v, it.value);
      if (known) {
        vaultUnchanged(v, ctx.generation);
        // what the model found inside the value is learned as well ("Alice Morgan" in a known "M. Alice Morgan"), so
        // that it is masked where it stands alone (not what is a known value written another way: that is that value)
        const read = view ? view.text : it.value;
        for (const s of modelValues(read, mine)) {
          const inner = read.slice(s.start, s.end);
          if (inner.trim() !== "" && !v.whole(inner)) tagFound(v, s.label, inner);
        }
        const tag = v.tag(known.label, known.value);
        c.tags?.add(tag);
        return tag;
      }
      const text = { text: it.value, model: false, loose: false };
      return view ? apply(v, text, [], ctx, { view, found: mine }) : apply(v, text, mine, ctx, undefined);
    }));
  };

  /** The known value that `value` is as a whole, whatever its length and however it is written (its white space, a
   *  number's spelling). */
  const wholeKnown = (v: Vault, value: string) => (value === "" ? undefined : v.whole(value));
  /** The placeholder of `value` when it is, as a whole, a value the vault holds. */
  const wholeValue = (v: Vault, value: string, c: MaskContext): string | undefined => {
    const known = wholeKnown(v, value);
    if (!known) return undefined;
    vaultUnchanged(v, c.generation ?? v.generation);
    const tag = v.tag(known.label, known.value);
    c.tags?.add(tag);
    return tag;
  };
  /** `wholeValue` for a number as it is written in JSON text (the same number in any spelling: "821023456789.0",
   *  "8.21023456789e11"). One rule for a sign: a number whose plain form without its minus sign is a known value is that
   *  value with the sign outside ("-4.524e3" and -4524 for a known 4524 become "-[ID_NUMBER_1]", a string). */
  const wholeNumber = (v: Vault, value: string, c: MaskContext): string | undefined => {
    const tag = wholeValue(v, value, c);
    if (tag !== undefined || !value.startsWith("-") || plainNumber(value) === "0") return tag;
    const unsigned = wholeValue(v, value.slice(1), c);
    return unsigned === undefined ? undefined : `-${unsigned}`;
  };
  /** A key of a received JSON document: the model does not read keys, but a value the vault holds is hidden in one too
   *  (a map keyed by e-mail address). `keep`: placeholder-like text in it stays as it is (the last pass). */
  const hideKey = (v: Vault, key: string, c: MaskContext, keep: boolean): string =>
    key === "" ? key : (wholeValue(v, key, c) ?? apply(v, { text: key, model: false, loose: false }, [], keep ? { ...c, system: true } : c));

  /** The keys of one object after masking, different from one another again where masking made two the same ("Al"
   *  and "%41l" are both "[PERSON_1]"): the later one gets " #2", " #3", ..., so that no field is lost. Keys that were
   *  the same before (JSON text can repeat a key) stay the same: each gets what the first of them became. */
  const distinctKeys = (before: readonly string[], after: readonly string[]): string[] => {
    const used = new Set<string>();
    const masked = new Set<string>();
    const all = new Set(after); // (a new key must not be one the object has further on either)
    const next = new Map<string, number>(); // the number to try next for each key (linear when many keys collide)
    const first = new Map<string, string>(); // what each key as it was before became
    return after.map((key, i) => {
      const earlier = first.get(before[i]);
      if (earlier !== undefined) return earlier;
      let out = key;
      if (used.has(key) && (key !== before[i] || masked.has(key))) {
        let n = next.get(key) ?? 2;
        while (used.has(`${key} #${n}`) || all.has(`${key} #${n}`)) n++;
        next.set(key, n + 1);
        out = `${key} #${n}`;
      }
      used.add(out);
      if (key !== before[i]) masked.add(key);
      first.set(before[i], out);
      return out;
    });
  };
  /** `values` (what each token of a JSON text becomes) with the keys of each object told apart as `distinctKeys` does. */
  const distinctJsonKeys = (tokens: readonly { kind: string; value: string; scope?: number }[], values: string[]): string[] => {
    const byObject = new Map<number, number[]>();
    tokens.forEach((t, i) => {
      if (t.kind !== "key") return;
      const at = byObject.get(t.scope ?? 0);
      if (at) at.push(i);
      else byObject.set(t.scope ?? 0, [i]);
    });
    for (const at of byObject.values()) {
      if (at.length < 2) continue;
      const fixed = distinctKeys(at.map((i) => tokens[i].value), at.map((i) => values[i]));
      at.forEach((i, n) => (values[i] = fixed[n]));
    }
    return values;
  };

  /** `text` with the vault's known values hidden, without the model, and with its own placeholder-like text left as it
   *  is. A JSON document is done token by token, so it stays valid JSON: a number that is a known value becomes a
   *  string. */
  const hideText = (v: Vault, text: string, c: MaskContext): string => {
    const ctx = { ...c, system: true };
    const tokens = jsonDocument(text);
    if (!tokens) return apply(v, { text, model: false, loose: false }, [], ctx);
    const value = (t: (typeof tokens)[number]) =>
      t.kind === "number"
        ? (wholeNumber(v, t.value, ctx) ?? t.value)
        : t.kind === "key"
          ? hideKey(v, t.value, ctx, true)
          : (wholeValue(v, t.value, ctx) ?? (t.value === "" ? "" : hideText(v, t.value, c)));
    return replaceJsonStrings(text, tokens, distinctJsonKeys(tokens, tokens.map(value)));
  };

  const masker: Masker = {
    mask(text, c = {}) {
      if (text === "") return Promise.resolve(text);
      if (!vault) return ready().then((r) => cached(texts, stamp(r, text), () => r.redact(text, options.redact).then((x) => x.text)));
      const ctx = { ...c, generation: c.generation ?? vault.generation };
      const s = source(vault, text, ctx);
      const view = reading(s);
      return detected(view ? view.text : s.text).then((spans) => (view ? apply(vault, s, [], ctx, { view, found: spans }) : apply(vault, s, spans, ctx, undefined)));
    },
    hide(text, c = {}) {
      if (!vault || text === "") return text;
      return c.system ? hideText(vault, text, c) : apply(vault, source(vault, text, c), [], c);
    },
    async value(value, c = {}, detect) {
      if (typeof value === "string") {
        const tag = vault && wholeValue(vault, value, c);
        if (tag) return tag;
        return detect ? masker.mask(value, c) : masker.hide(value, c);
      }
      // a number that is a value the vault holds (a phone number a model wrote as a number) becomes its placeholder
      if (typeof value === "number" && vault && Number.isFinite(value)) return wholeNumber(vault, String(value), c) ?? value;
      // (anything else that is not an object stays: NaN is not NaN, so it must not be compared below; the 1.7.1
      // release gate 4 found a NaN in tool input recursing until the stack ran out)
      if (value === null || typeof value !== "object") return value;
      if (Array.isArray(value)) return Promise.all(value.map((x) => masker.value(x, c, detect)));
      if (isRecord(value)) return Object.fromEntries(await Promise.all(Object.entries(value).map(async ([k, x]) => [k, await masker.value(x, c, detect)])));
      // a class instance, or anything else that is not plain data: masked as the JSON it gives, built once (a fresh
      // plain tree; what JSON cannot write fails the call, since 1.7.2, release gate 8: it was given back as it was)
      return masker.value(plainTree(value), c, detect);
    },
    async json(text, c = {}, detect) {
      const tokens = jsonStringValues(text);
      if (!tokens) return detect ? masker.mask(text, c) : masker.hide(text, c);
      // model-written JSON (tool input): value by value; a received document: all its values in one reading
      if (vault && c.model !== true) return masker.received(text, c, detect);
      const all = (jsonTokens(text) ?? []).filter((t) => t.kind !== "key");
      const values = all.map((t) => (t.kind === "number" ? ((vault && wholeNumber(vault, t.value, c)) ?? t.value) : masker.value(t.value, c, detect)));
      return replaceJsonStrings(text, all, (await Promise.all(values)) as string[]);
    },
    async received(text, c = {}, detect) {
      const tokens = vault ? jsonDocument(text) : undefined;
      if (vault && tokens) {
        // values: one reading of the document by the model; keys: the vault's known values only
        const values = tokens.filter((t) => t.kind !== "key");
        const masked = await leaves(vault, values.map((t) => ({ key: t.key, value: t.value, number: t.kind === "number" })), c, detect);
        let i = 0;
        return replaceJsonStrings(text, tokens, distinctJsonKeys(tokens, tokens.map((t) => (t.kind === "key" ? hideKey(vault, t.value, c, false) : masked[i++]))));
      }
      // any other text is read whole, as it is written (JSON inside it too: the vault's known values are then searched
      // in its other readings as well, see `redactReadings`)
      return detect ? masker.mask(text, c) : masker.hide(text, c);
    },
    sweep<T>(value: T, c: MaskContext | undefined, how: SweepOptions): T {
      if (!vault || vault.size === 0) return value;
      const v = vault;
      const ctx = { ...c, generation: c?.generation ?? v.generation };
      const { shapes } = how;
      // a string that is a known value as a whole is hidden whatever it looks like; binary data is not text
      // (base64 never holds a known value of six or more characters at word edges by chance; an id or a file name does).
      // Binary only when no reading of it holds one either: "Kim+Minsu+asked+..." looks like base64 and is a form field.
      const holds = (s: string) => v.find(s).some((h) => h.value.length >= 6);
      const data = (x: string) => binary(x) && !holds(x) && !escapedReadings(x).some((r) => holds(r.text));
      const text = (x: string) => (x === "" ? x : (wholeValue(v, x, ctx) ?? (data(x) ? x : hideText(v, x, ctx))));
      // How the fields of an object are read: an object of the framework of the type given (undefined: an object
      // without a type), data, an object of a type the framework does not have there (every field but its type and
      // provider options is data), or `fields` (the fields named there hold the framework's objects, the rest is data).
      type Kind = "framework" | "data" | "foreign" | "fields";
      const fieldIsData = (kind: Kind, k: string) => kind === "data" || (kind === "foreign" ? !OPTIONS.has(k) : kind === "fields" && !how.fields!.has(k));
      // the slots inside an object that stands in what came from outside are in it too
      const received = new Map<Slot, Slot>();
      const within = (inner: Slot | undefined, outer: Slot | undefined): Slot | undefined => {
        if (!inner || !outer?.received || inner.received) return inner;
        let out = received.get(inner);
        if (!out) received.set(inner, (out = { ...inner, received: true }));
        return out;
      };
      // `slot`: x stands where the framework's objects of that slot stand (a part, a block, a tool call, a tool's
      // output, a file's data); undefined: x is data, at any depth and whatever types its objects give themselves
      const walk = (x: unknown, slot: Slot | undefined): unknown => {
        if (typeof x === "string") return text(x);
        // in data, a number that is a known value as a whole (a phone number) becomes its placeholder
        if (typeof x === "number" && !slot && Number.isFinite(x)) return wholeNumber(v, String(x), ctx) ?? x;
        if (Array.isArray(x)) {
          const out = x.map((y) => walk(y, slot));
          return out.some((y, i) => y !== x[i]) ? out : x;
        }
        // (a class instance or a toJSON object is read as the JSON it gives, also where the framework's objects stand)
        if (!isRecord(x) || typeof x.toJSON === "function") return opaque(x, slot);
        if (!slot) return fields(x, "data", undefined);
        if (typeof x.type !== "string") return fields(x, slot.untyped ? "framework" : "data", undefined, slot);
        // in what came from outside, a typed object without its type's own content is a row of data
        if (slot.received && shapes.content && !carriesContent(x, shapes.content)) return fields(x, "data", undefined);
        // an object with a type that may stand here is the framework's; one with another type is not
        if (!(slot.types ?? shapes.types).has(x.type)) return fields(x, "foreign", undefined);
        return SIGNED.has(x.type) ? x : fields(x, "framework", x.type, slot);
      };
      /** An object that is not plain data: a class instance, something with toJSON. It is read as the JSON it gives,
       *  which is what a provider is sent (since 1.7.2, the 24th outside review: also where the framework's objects
       *  stand, so a class instance a LangChain.js tool returns as a block is walked as that block, with the block
       *  fields its class gives by getters: `blockForm`), and it is kept as it is when that holds no known value. Bytes
       *  are binary data, not text. */
      const opaque = (x: unknown, slot: Slot | undefined): unknown => {
        if (x === null || typeof x !== "object" || ArrayBuffer.isView(x) || x instanceof ArrayBuffer) return x;
        // (the tree is what is sent, also when it holds nothing to hide: the object itself would be written again by
        // the provider, and an object that writes something else the second time would send that; release gate 4)
        const json = plainTree(x, slot ? { how: { shapes, root: slot } } : {});
        return json === x ? x : walk(json, slot);
      };
      const fields = (x: Record<string, unknown>, kind: Kind, type: string | undefined, slot?: Slot): unknown => {
        let changed = false;
        const out: Record<string, unknown> = {};
        const entries = Object.entries(x);
        // Data: every string counts, a key too (two keys that become the same placeholder stay two fields). In the
        // framework's objects, a key that is not the name of one of their fields is data as well.
        const hidden = (k: string) => (kind === "framework" ? !FRAME_KEYS.has(k) : fieldIsData(kind, k));
        const keys = entries.some(([k]) => hidden(k)) ? distinctKeys(entries.map(([k]) => k), entries.map(([k]) => (hidden(k) ? hideKey(v, k, ctx, true) : k))) : entries.map(([k]) => k);
        for (const [n, [k, y]] of entries.entries()) {
          const key = keys[n];
          let next: unknown;
          // (in an object of a type the framework does not have, provider options are read as in the framework's own)
          if (kind !== "framework")
            next = fieldIsData(kind, k) ? walk(y, undefined) : kind === "fields" ? walk(y, how.fields!.get(k)) : k === "providerOptions" ? masker.options(y, ctx, true) : y;
          // identifiers, references and signed data of the framework's objects stay; `name` only where it names a tool or
          // a function (an object without a type stands where those stand: a tool call, a function). A block's `id` stays
          // in a user's message, at its authorized path (release gate 4: a HumanMessage text block's id); in what a tool
          // returned no block is matched by its id, and it is data there (except a LangChain v0 file given by its id)
          else if (NOT_TEXT.has(k) && (k !== "name" || type === undefined || shapes.named.has(type)) && !(k === "id" && slot?.received && !slot.ids && x.source_type !== "id")) next = y;
          // provider options: string values are text (an `openai.user`), identifiers and signatures stay (since 1.7.2)
          else if (k === "providerOptions") next = masker.options(y, ctx, true);
          // the content of a file (bytes, a URL object) is binary; a payload under "data" is data
          else if (k === "data" && type !== undefined && shapes.files.has(type) && y !== null && typeof y === "object" && !isRecord(y) && !Array.isArray(y)) next = y;
          // a file given by its URL (an AI SDK file part's `data` "https://..."): a reference to a file, sent as written
          // unless it is a known value as a whole (release gate 4: rewriting its path pointed the request at another file)
          else if (k === "data" && type !== undefined && shapes.files.has(type) && typeof y === "string" && fileUrl(y)) next = wholeValue(v, y, ctx) ?? y;
          // a field that holds the framework's objects (the slot chosen by the object's type and the field), or data
          else next = walk(y, within(shapes.slot(type, k), slot));
          // (defined, not assigned: a key "__proto__" in parsed JSON is a key like any other)
          Object.defineProperty(out, key, { value: next, enumerable: true, writable: true, configurable: true });
          if (key !== k || next !== y) changed = true;
        }
        return changed ? out : x;
      };
      if (how.fields && isRecord(value)) return fields(value, "fields", undefined) as T;
      return walk(value, how.root) as T;
    },
    schema<T>(schema: T, c: MaskContext = {}, keepTitle = false): T {
      if (!vault) return schema;
      // The schema is built once into the plain JSON tree the provider writes (a class instance as its own fields, at
      // the top too, each getter read once, a list with a getter rebuilt), and that tree is what is read and sent
      // (since 1.7.2, release gate 8: a class-backed schema went out as written, and a list read again by the
      // provider's writer gave it another item)
      const tree = plainTree(schema, { where: "schema", memo: c.trees }) as T;
      if (vault.size === 0) return tree;
      const v = vault;
      const ctx = { ...c, generation: c.generation ?? v.generation };
      const text = (x: string) => (x === "" ? x : (wholeValue(v, x, ctx) ?? hideText(v, x, ctx)));
      // an example is data: its strings and numbers count, its keys are property names
      const example = (x: unknown): unknown => {
        if (typeof x === "string") return text(x);
        if (typeof x === "number" && Number.isFinite(x)) return wholeNumber(v, String(x), ctx) ?? x;
        return mapped(x, (_k, y) => example(y));
      };
      // a schema (an object; true and false are schemas too), or a list of them
      const sub = (x: unknown): unknown => (Array.isArray(x) ? mapped(x, (_i, s) => sub(s)) : walk(x, false));
      const walk = (x: unknown, top: boolean): unknown =>
        !isRecord(x)
          ? x
          : mapped(x, (k, y) => {
              if (SCHEMA_TEXT.has(k)) return typeof y === "string" ? (k === "title" && top && keepTitle ? y : text(y)) : y;
              if (SCHEMA_EXAMPLES.has(k)) return example(y); // (examples are data)
              if (SCHEMA_MAPS.has(k)) return mapped(y, (_name, s) => sub(s)); // a property name, a definition's name -> schema
              if (SCHEMA_SUBSCHEMAS.has(k)) return sub(y);
              return y; // a literal constraint (const, enum, default, pattern), and any other keyword: as written
            });
      return walk(tree, true) as T;
    },
    options<T>(value: T, c: MaskContext = {}, message = false, top: OptionsTop = "providers", strings?: (path: readonly string[]) => boolean): T {
      if (!vault) return value;
      const v = vault;
      const ctx = { ...c, generation: c.generation ?? v.generation };
      const empty = v.size === 0;
      const text = (x: string) => (x === "" || empty ? x : (wholeValue(v, x, ctx) ?? hideText(v, x, ctx)));
      const name = top === "settings" ? "modelSettings" : top === "tools" ? "tools" : "providerOptions";
      // the exact paths of this kind of options where a provider defines its identifiers, numeric settings and schemas
      const tree0 = PATH_TREES.get(top === "providers" ? (message ? "message" : "call") : top)!;
      // the keys from the top of the options to the value being read
      const path: string[] = [];
      // A number that is a known value becomes its placeholder. Where the call's own adapter takes only a string
      // (`strings`), the call fails instead: the adapter refuses a number there, and a placeholder would turn that
      // request into one it sends (the 1.7.1 release gate: a phone number in OpenAI's Chat Completions metadata)
      const number = (x: number): unknown => {
        const tag = empty || !Number.isFinite(x) ? undefined : wholeNumber(v, String(x), ctx);
        if (tag === undefined) return x;
        if (strings?.(path))
          throw new TypeError(`[@newmean-ai/pii] ${[name, ...path].join(".")} is a number the vault holds, where the provider's adapter takes only a string; a placeholder would turn a request it refuses into one it sends, so the call is not sent`);
        return tag;
      };
      /** The path tree's nodes the path `p` (from the top) reaches. */
      const reached = (p: readonly (string | number)[]): PathNode[] => {
        let nodes: PathNode[] = [tree0];
        for (const k of p) nodes = pathStep(nodes, String(k));
        return nodes;
      };
      // Built once into the tree the provider writes (an object this call already built is given as it was read then:
      // `MaskContext.trees`). What LangChain reads itself, a whole subtree a provider defines as identifiers or
      // credentials ("**") and a library's schema object at a schema's path stay as they are
      const tree = plainTree(value, {
        where: name,
        memo: c.trees,
        keep: (p, y) => {
          if (y === null || typeof y !== "object" || p.length === 0) return false;
          if (top === "settings" && p.length === 1 && SETTING_RUNTIME.has(String(p[0]))) return true;
          const nodes = reached(p);
          return nodes.some((n) => n.rest && n.id) || (librarySchema(y) && nodes.some((n) => n.schema));
        },
      });
      /** `x`, the value at `path`, which reaches the nodes `nodes` of the path tree. Data unless a provider defines what
       *  stands there: every string, a number that is a known value, and every key (two keys that become one
       *  placeholder stay two fields). The keys along a provider's paths stay, and so does the name of a message. */
      const walk = (x: unknown, nodes: readonly PathNode[]): unknown => {
        if (nodes.some((n) => n.rest && n.id)) return x; // a provider's identifiers or credentials, whole
        const id = nodes.some((n) => n.id);
        if (typeof x === "string") return id ? x : text(x);
        if (typeof x === "number") return id || nodes.some((n) => n.number) ? x : number(x);
        if (x === null || typeof x !== "object") return x;
        // a JSON schema there is read as `schema` reads it (a library's schema object stays: the framework writes it)
        if (nodes.some((n) => n.schema)) return isRecord(x) ? masker.schema(x, ctx) : x;
        if (Array.isArray(x)) return mapped(x, (i, y) => inside(i, () => walk(y, pathStep(nodes, i))));
        if (!isRecord(x)) return x;
        // (the name an OpenAI-compatible provider sends as the message's, also under a provider not known here)
        const named = (key: string) => message && top === "providers" && path.length === 1 && key === "name";
        const entries = Object.entries(x);
        const steps = entries.map(([key]) => pathStep(nodes, key));
        // (the top keys of provider tools' arguments are the tools' ids, put there by the caller)
        const kept = (key: string, n: number) => steps[n].length > 0 || named(key) || (top === "tools" && path.length === 0);
        const keys = empty
          ? entries.map(([key]) => key)
          : distinctKeys(entries.map(([key]) => key), entries.map(([key], n) => (kept(key, n) ? key : hideKey(v, key, ctx, true))));
        let changed = false;
        const out: Record<string, unknown> = {};
        entries.forEach(([key, y], n) => {
          let next: unknown;
          // LangChain's own call options are not sent
          if (top === "settings" && path.length === 0 && SETTING_RUNTIME.has(key)) next = y;
          // The options of a message or a part give a provider's fields of the message (OpenAI-compatible providers send
          // `openaiCompatible.name` as the name of the message): a name there is written as a message's name is
          // (`name`), without brackets
          else if (named(key) && typeof y === "string") next = masker.name(y, c);
          else next = inside(key, () => walk(y, steps[n]));
          if (next !== y || keys[n] !== key) changed = true;
          // (defined, not assigned: a key "__proto__" is a key like any other)
          Object.defineProperty(out, keys[n], { value: next, enumerable: true, writable: true, configurable: true });
        });
        return changed ? out : x;
      };
      const inside = <R>(k: string, f: () => R): R => {
        path.push(k);
        try {
          return f();
        } finally {
          path.pop();
        }
      };
      return walk(tree, [tree0]) as T;
    },
    name(name, c = {}) {
      if (!vault || vault.size === 0 || name === "") return name;
      const v = vault;
      // never restored: its placeholders are not the call's own
      const ctx = { ...c, tags: undefined, generation: c.generation ?? v.generation };
      const whole = wholeValue(v, name, ctx);
      if (whole !== undefined) return bareTags(name, whole);
      const hidden = bareTags(name, hideText(v, name, ctx));
      // The name's own spelling: providers take only letters, digits, "_" and "-" there, so a value of two or more
      // words can stand in a name only joined ("Kim_Minsu", "kim-minsu", "KimMinsu"). In the name, "_" and "-" between
      // two word characters and a change from a small letter to a capital are read as a space, and a known value found
      // in that reading is hidden where the name as written holds no value of its own (the 1.7.1 review round, L1-A2).
      // Only a value of two or more words is read this way: a one-word value is found as written, and the reading would
      // cut words that are not joins ("xAnn", "1e-4524"). A name that is such a value as a whole in that reading is
      // that value ("Kim_Minsu" is one placeholder).
      const read = nameReading(name);
      if (!read) return hidden;
      const words = (value: string) => /\s/u.test(value);
      const own = v.find(name, { variants: true });
      const known = v.whole(read.text);
      const hits =
        known && words(known.value)
          ? [{ start: 0, end: read.text.length, label: known.label, value: known.value }]
          : v.find(read.text, { variants: true }).filter((h) => words(h.value));
      let out = "";
      let last = 0;
      let k = 0;
      for (const h of hits) {
        const a = read.map[h.start];
        const b = read.map[h.end];
        while (k < own.length && own[k].end <= a) k++;
        if (a < last || (k < own.length && own[k].start < b)) continue; // as written, the name holds a value there
        vaultUnchanged(v, ctx.generation);
        out += name.slice(last, a) + bareTags("", v.tag(h.label, h.value));
        last = b;
      }
      if (last === 0) return hidden;
      // the rest of the name hidden as it is written, now with those values out of it
      out += name.slice(last);
      return bareTags(out, hideText(v, out, ctx));
    },
    async data(value, c = {}, detect) {
      // the tree a provider writes of it, built once (what is masked is what is sent)
      const data = plainTree(value);
      if (!vault) return masker.value(data, c, detect);
      // strings, and numbers as they would be written (a card or phone number can be a JSON number)
      const items: { key?: string; value: string; number?: boolean }[] = [];
      const leaf = (v: unknown) => typeof v === "string" || (typeof v === "number" && Number.isFinite(v));
      const collect = (v: unknown, key?: string): void => {
        if (leaf(v)) items.push({ key, value: String(v), number: typeof v === "number" });
        else if (Array.isArray(v)) for (const x of v) collect(x, key);
        else if (isRecord(v)) for (const [k, x] of Object.entries(v)) collect(x, k);
      };
      collect(data);
      const masked = await leaves(vault, items, c, detect);
      const v = vault;
      let i = 0;
      const rebuild = (x: unknown): unknown => {
        if (leaf(x)) {
          const m = masked[i++];
          return typeof x === "number" && m === String(x) ? x : m; // a masked number becomes its placeholder, a string
        }
        if (Array.isArray(x)) return x.map(rebuild);
        if (!isRecord(x)) return x;
        // (keys: the vault's known values only; the model does not read them. Two keys that become the same placeholder
        // stay two fields)
        const entries = Object.entries(x);
        const keys = distinctKeys(entries.map(([k]) => k), entries.map(([k]) => hideKey(v, k, c, false)));
        return Object.fromEntries(entries.map(([, y], n) => [keys[n], rebuild(y)]));
      };
      return rebuild(data);
    },
  };
  return masker;
}

/** Tool names whose call arguments get the values back: a name, a list of names or a test (`restoreTools`). */
export function toolTest(rt: string | readonly string[] | ((toolName: string) => boolean) | undefined): (name: unknown) => boolean {
  if (typeof rt === "function") return (n) => typeof n === "string" && rt(n);
  const names = new Set<string>(typeof rt === "string" ? [rt] : (rt ?? []));
  return (n) => typeof n === "string" && names.has(n);
}

