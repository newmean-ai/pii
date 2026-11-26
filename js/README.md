English | [한국어](README.ko.md)

# @newmean-ai/pii

Detect and mask personal information in text **inside the browser**, before the text is sent anywhere. A small multilingual
token-classification model (54 languages) runs in WebAssembly.

```bash
npm install @newmean-ai/pii
```

```ts
import { createRedactor } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium" });
const { text, spans } = await redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다");
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

## Models

| size | file | notes |
|---|---|---|
| `nano` | 2.6 MB | one shared layer applied 4 times, rank-32 embeddings |
| `small` | 7.1 MB | 4 layers, rank-64 embeddings |
| `medium` (default) | 11.3 MB | 6 layers, rank-192 embeddings |
| `large` | 15.5 MB | 6 layers, full (pruned-vocabulary) embeddings |

Plus a 0.8 MB tokenizer (shared by all sizes). Files are downloaded once from
Hugging Face (`newmean-ai/pii-<size>`), kept in the Cache API, and checked against SHA-256 values pinned in this package.
To serve them yourself, copy the model folder and pass `modelUrl`.

## Engines

Two engines run the same model file and give the same spans; pick one with `engine`.

| `engine` | runtime download | notes |
|---|---|---|
| `"builtin"` (default) | about 13 KB (brotli, JS and WebAssembly together) | this package's own engine for these models; starts faster, and on the phones we measured runs about 1.9× faster on short texts and about 24% faster on long ones (mean of four phones); uses fused multiply-add where the browser supports relaxed SIMD (Chrome, Edge, Firefox) |
| `"onnxruntime"` | onnxruntime-web, about 2.4 MB (brotli) | general ONNX runtime, multi-threading on cross-origin isolated pages |

Each engine is a separate chunk loaded on first use, so an app downloads only the engine it picks.

No model finds every piece of personal information. The technical report lists miss and over-masking rates per language
and dataset; treat the output as a strong first pass, not a guarantee.

## Results

On newmean-pii-dataset (public test, 54 languages) the models miss 1.6% (large) to 2.9% (nano) of PII characters; the closest of the eight
public detectors we compared misses 19.6%. Scores on four external datasets, the full comparison and phone speed are in the
[repository README](https://github.com/newmean-ai/pii#results) and the technical report; weekly scores are on the
[leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

## Labels

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, and two
level 2 labels for indirect identifiers: `PERSON_L2` (a surname with a title, a nickname, initials) and `URL_L2` (public
profile or page URLs). Pass `labels: LEVEL1_LABELS` to leave level 2 visible. Names in `labels` that are not labels are
ignored by this package (the Python and Kotlin packages raise an error).

To mask people but keep company and shop names, leave `ORG` out; a shop you know that the model reads as a person (one
named after its owner) goes on the allow list ([Lists](#lists)):

```ts
import { createRedactor, LABELS } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium", labels: LABELS.filter((l) => l !== "ORG"), allow: [{ text: "Wendy's", label: "PERSON" }] });
(await redactor.redact("Customer Maria Lopez disputes a charge from Jo Malone London and one from Tiffany & Co.")).text;
// "Customer [PERSON] disputes a charge from Jo Malone London and one from Tiffany & Co."
```

## API

```ts
createRedactor(options?: {
  engine?: "builtin" | "onnxruntime";  // default "builtin"
  size?: "nano" | "small" | "medium" | "large";
  variant?: "q4" | "q4e";      // weight file; default q4e for large/medium, q4 for small/nano
  modelUrl?: string;           // base URL of a self-hosted model folder (ending with "/")
  sensitivity?: number;        // > 0 masks more, < 0 masks less (about −1..1); default 0
  labels?: string[];           // labels to mask; default all
  regex?: boolean;             // also mask a few patterns (email, URL, card/account, phone, Korean resident number);
                               // default false (model only): almost no extra coverage, double the over-masking
  keepEmoji?: boolean;         // true: the model also reads emoji (1.0 behaviour); default false: never read or masked
  wholeWords?: boolean;        // default true: a span that ends inside a word covers the whole word; false: 1.2 spans
  strictShortNames?: boolean;  // true: short texts (≤ 8 tokens) need stronger evidence to be read as names; default false
  allow?: (string | { text: string; label?: Label })[];  // values that are not PII in your data (see Lists); since 1.4.0
  block?: { text: string; label: Label }[];              // values that are PII in your data (see Lists); since 1.4.0
  listsId?: string;            // your name for the lists, shown in info.lists.id; since 1.4.0
  cache?: boolean;             // keep model files in the Cache API; default true
  verify?: boolean;            // check SHA-256 of model files; default true
  onProgress?: (p) => void;    // download progress
  fetch?: typeof fetch;        // used to download the model files; default globalThis.fetch (pass one in Node or tests)
  wasmPaths?: string;          // onnxruntime: where onnxruntime-web loads its .wasm from
  threads?: number;            // onnxruntime: WebAssembly threads (cross-origin isolated pages)
  relaxedSimd?: boolean;       // builtin: force the fused multiply-add build on or off (default: detected)
}): Promise<Redactor>

redactor.detect(text)            // → Span[] {start, end, label, source}, sorted, non-overlapping
redactor.redact(text, { mode })  // mode "tag" → [EMAIL], "mask" → *****, "blank" → removed; or replace: (span, original) => string
redactor.detectTable(text, table?)           // table mode (see Tables)
redactor.redactTable(text, table?, options?) // → { text, spans }; options as redact
redactor.setSensitivity(value)
redactor.engine                  // "onnxruntime" | "builtin"
redactor.version                 // model release ("1.1.0"); the package version is VERSION or info.packageVersion
redactor.info                    // { packageVersion, modelVersion, size, variant, engine, modelUrl,
                                 //   files: { model, tokenizer: { path, bytes, sha256 } }, verified,
                                 //   lists: { allow, block, id } }
redactor.dispose()
```

Offsets are UTF-16 indices, as used by `String.prototype.slice`. Spans are widened to whole grapheme clusters, so an emoji,
flag or combining sequence is masked whole or kept whole (this needs `Intl.Segmenter`; where it is missing, spans stay on code
point boundaries). Grapheme segmentation is the runtime's: the C core uses Unicode 17.0, and a browser or Node with other
Unicode data can segment some sequences differently, which can change merges, labels and allow decisions, not only span
edges; word characters (`wholeWords`, block boundaries) come from a pinned Unicode 17.0 table in every runtime. By default emoji are taken out of the text the model reads, so the model does not read them as PII: offsets still refer to the
input, and an emoji inside a span splits it in two. `keepEmoji: true` gives the model the text as given (1.0 behaviour).
By default (`wholeWords`, since 1.3.0), a span that starts or ends inside a word is widened to the whole word: "R23" becomes `[ID_NUMBER]`
instead of `[ID_NUMBER]23`. A word is a run of letters, digits and marks
joined by apostrophes and hyphens; letters of scripts written without spaces (Chinese, Japanese, Thai, Khmer, …) never
extend a span, and a word ends where Hangul meets digits or Latin letters (「010-1234-5678입니다」 → `[PHONE]입니다`), and Korean particles after a Hangul span are left as they are (「홍길동님께」 → `[PERSON]님께`). It changes where a span ends, not what is masked: a short value the model misreads as a name, such as 「경기도」, becomes `[PERSON]` rather than `[PERSON]기도`; `strictShortNames` leaves such values alone. It masks a little more text and nothing less; in table mode it stays inside the cell. `wholeWords: false` gives the 1.2 spans.

With `strictShortNames: true`, a text the model reads as at most 8 tokens (a survey answer, a table cell) needs stronger evidence to be read as a person's name: 「경기」, 「남성」 and 「네이버」 are left alone, while 「김민지」 and "Maria Lopez" are still found. With large, short survey items without personal information are masked 4.3% of the time instead of 7.9%, and 96.5% of lone names are found instead of 98.9%. Longer texts are unchanged.
`info` names what ran, for audit records: the package and model versions and the size and SHA-256 of the two model files.
`info.verified` is true only when the SHA-256 of both loaded files was computed and matched. It is false with
`verify: false` (files used unchecked) and where `crypto.subtle` is unavailable, on a page that is not a secure context such
as plain HTTP (files checked by size only).
Inference runs on the calling thread; for long texts, create the redactor inside a Web Worker.

## Lists

Since 1.4.0 you can tell the redactor what you already know about your data:

```ts
const redactor = await createRedactor({
  allow: ["제주", "배민", { text: "경기", label: "PERSON" }],  // a string allows any label
  block: [{ text: "박지훈", label: "PERSON" }, { text: "새솔고등학교", label: "ORG" }],
  listsId: "survey-2026-11 v3",
});
const { text } = await redactor.redact("제주, 박지훈".normalize("NFC"));
// text: "제주, [PERSON]" (without the lists, the medium model gives "[PERSON], [PERSON]")
```

- `block`: every occurrence of an entry that starts and ends on a word boundary is masked with the entry's label (source
  `"block"`), also where the model finds nothing and whatever `labels` enables. A Hangul entry also matches before Korean
  particles (「박지훈님께」 → 「[PERSON]님께」); "Kim" does not match inside "Kimberly", nor in "Kim's" or "Kim-Lee"
  (apostrophes and hyphens join words, as in `wholeWords`); in scripts written without spaces any occurrence matches. A
  region with a block match takes the block label and is never allowed.
- `allow`: a span whose whole text is an entry is not masked; with `label`, only spans of that label family (PERSON covers
  PERSON_L2, URL covers URL_L2). The test is on the whole span, so it depends on where the model ends a span: a span
  「제주도」 is not allowed by "제주"; list both forms when both occur.
- Matching folds ASCII letters only (`KIM` = `kim`); nothing else is normalized. Pass NFC text and entries: files exported on
  macOS or copied from PDFs may hold NFD Hangul, which never matches (`text.normalize("NFC")`). These rules are the
  lists'; a vault finds its values in more spellings ([below](#how-a-known-value-is-found-again)).
- Exact forms only: 「박 지훈」 and 「박지훈」 are separate entries. A one-syllable entry such as 「김」 also masks every
  word that is 김 plus particles (「김은」, 「김도」).
- In table mode, block entries match inside each cell and do not vote; allowed spans leave before the column vote.
- Up to 10,000 entries per list, 1 to 64 code points each, no line breaks, no leading or trailing white space. Invalid lists
  throw `ListError` (`list`: "allow", "block" or "meta" for `listsId`; `index`; `reason`: `bad_shape`, `too_long`,
  `bad_text`, `empty`, `edge_space`, `unknown_label`, `too_many`, `label_conflict`, `in_both`, `bad_lists_id`) before any
  model file is downloaded. The error never holds an entry's text.
- `info.lists` gives the entry counts after merging duplicates and your `listsId`, for audit records; nothing derived from
  the entries' text.

## Tables

```ts
redactor.detectTable(text: string, table?: {
  delimiter?: "auto" | "," | "\t" | ";" | "markdown";  // default "auto"
  header?: boolean;            // the first row is a header row; default true
  vote?: boolean;              // column vote; default true
  context?: "none" | "header"; // "header": also read cells of distinct-valued columns as "<header>: <value>"; default "none"
}): Promise<Span[]>
redactor.redactTable(text: string, table?: TableOptions, options?: RedactOptions): Promise<{ text: string; spans: Span[] }>
```

Table mode reads a CSV, TSV, semicolon-separated or markdown pipe table as one text (delimiter detected unless given; quoted
fields as in RFC 4180) and clips every span to a cell value: delimiters, quotes and line breaks are never masked, so the
output is still a valid table. With `vote`, a column of short values is masked whole when the model finds names, emails,
phone numbers or usernames in at least 80% of its non-empty cells (and at least 4; columns with fewer than 5 non-empty cells
do not vote); those spans have source `"table"`. Free-text columns (median value over 40 characters or 4 words) do not vote,
placeholders (`N/A`, `-`, `none`, `null`, `없음`) are skipped, an email column needs `@` and a phone column 6 or more digits.
The header row is masked where the model finds PII but never voted on. With `context: "header"` and a header row, each
non-empty data cell of a column whose non-empty values are at least 80% distinct is also read on its own as
`"<header>: <value>"` and those spans are added: fewer misses on name, contact and ID columns, more PII-free cells masked,
one more model call per such cell. Text that is not a table gives exactly the spans of `detect`. A custom `replace` must
not insert the delimiter, a quote or a line break.

```ts
const csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free";
const { text } = await redactor.redactTable(csv);
// "name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free"
await redactor.redactTable(tsv, { delimiter: "\t", vote: false }, { mode: "mask" });
```

On survey and form exports, table mode misses many names split into first and last name columns. `context: "header"` cuts
the misses to about a quarter, at 2.3 to 3.7 more points of PII-free cells masked; for recall first, use it. Measurements:
[repository README](https://github.com/newmean-ai/pii#tables-csv-tsv).

## Vercel AI SDK

Mask the prompt before it reaches a hosted model. `@newmean-ai/pii/ai-sdk` is a language model middleware; the `ai`
package is not a dependency of this one.

```ts
import { generateText, wrapLanguageModel } from "ai";
import { openai } from "@ai-sdk/openai";
import { piiMiddleware } from "@newmean-ai/pii/ai-sdk";

const model = wrapLanguageModel({ model: openai("gpt-6.1"), middleware: piiMiddleware() });
await generateText({ model, prompt: "Call Kim Minsu on 010-1234-5678" });
// the provider receives "Call [PERSON] on [PHONE]"
```

```ts
piiMiddleware({
  redactor,                    // a Redactor you already have (or its promise); default: created on the first call
  create: { size: "medium" },  // createRedactor options when no redactor is given
  roles: ["user"],             // roles to mask: "user" (default), "system", "assistant", "tool" (text, JSON strings and text content of tool results)
  redact: { mode: "tag" },     // as in redact(): "tag", "mask", "blank" or replace; with a vault only { mode: "tag" }
  specificationVersion: "v3",  // default "v3" (AI SDK 6 and 7); "v2" for AI SDK 5
});
```

It works with `generateText`, `streamText` and agents alike, because it changes only the call's prompt (with a vault, also
some call fields: [below](#fields-outside-the-messages)), and it remembers
the last 256 texts, so the unchanged history of a conversation is masked once. Masked values are put back into the
response only with a vault ([below](#putting-the-values-back-vault)), and no model finds everything: this lowers what a provider sees, it does not guarantee that nothing gets through.

## LangChain.js

`@newmean-ai/pii/langchain` is an agent middleware for `langchain` 1.5.1+ (`langchain` and `@langchain/core` 1.2.1+,
which comes with it, are optional peer dependencies, needed only for this entry).

```ts
import { createAgent } from "langchain";
import { piiMiddleware } from "@newmean-ai/pii/langchain";

const agent = createAgent({ model: "openai:gpt-6.1", middleware: [piiMiddleware()] });
// "Call Kim Minsu on 010-1234-5678" reaches the model as "Call [PERSON] on [PHONE]"
```

`piiMiddleware({ redactor, create, roles, redact })` takes the options of the AI SDK middleware above; `roles` are
`"user"` (default), `"tool"` and `"assistant"`. It wraps the model call, so the model gets masked copies and the agent's
state keeps the messages as written. (LangChain's own `piiMiddleware` takes a synchronous detector, which an asynchronous
model call cannot be, so this is a middleware of its own.)

## Agents: mask what the agent reads

Personal information reaches the model on later turns too: a CRM lookup, a search result or a PDF brings in names and
contacts the user never typed. Mask tool results as well as user messages, on every model call:

```ts
piiMiddleware({ redactor, roles: ["user", "tool"] });            // AI SDK or LangChain.js: tool results are masked too
piiMiddleware({ redactor, vault, restoreTools: ["lookupCustomer"] }); // with a vault (below): user, assistant and tool by default
```

With the AI SDK, text, JSON and text content tool results are masked (since 1.7.2). A full agent with a CRM tool and a
ticket search: [`examples/ai-sdk-agent.mjs`](examples/ai-sdk-agent.mjs). Files are not read by the models: extract the
text first (for a PDF, a text extractor such as pdf.js), then mask it like any other text.

**Where it runs.** The middlewares and `redact` mask in your app (browser or server), before the text leaves it; only
there can a vault put the values back. A runtime that does not allow compiling WebAssembly at run time, such as a
Cloudflare Worker, cannot load the model. At a gateway, the Python package's LiteLLM guardrail masks in the
proxy for every app behind it, and nothing is put back. A cloud provider's own PII filter (such as Amazon Bedrock
Guardrails) runs after the text has reached the provider; it can stay on next to this package, and then sees
placeholders.

## Putting the values back (vault)

Since 1.7.2. A vault keeps a local map from numbered placeholders to the values they replace, so masking can be undone in
the model's answer and in tool-call arguments. Nothing in it is sent anywhere.

```ts
import { createRedactor, createVault } from "@newmean-ai/pii";

const vault = createVault();                                   // one per user or conversation
const { text } = await redactor.redact("Call Kim Minsu on 010-1234-5678", { vault });
// "Call [PERSON_1] on [PHONE_1]"
vault.restore("I called [PERSON_1].");                         // "I called Kim Minsu."
```

**Placeholders come back only as written.** `[PERSON_1]` is restored; `[Person_1]`, `PERSON_1` or a translated tag stays
in the answer as the model wrote it (never another value). One line in the system prompt asks the model to copy them:

```text
Text in square brackets with a label and a number, such as [PERSON_n], stands for a value hidden from you. Copy it exactly as written, brackets included, wherever you refer to it.
```


- The same value always gets the same placeholder in a vault, so a masked conversation stays the same from turn to turn
  and prompt caches keep hitting.
- A value the vault holds is masked again wherever it reappears, in the same text or a later one, also where the model
  would miss it there (spans with source `"vault"`). How it is found: [below](#how-a-known-value-is-found-again).
- `vault.restorer()` restores a stream (a placeholder split across chunks is held back until it is complete),
  `restoreValue` and `hideValue` work on the strings of JSON values (keys and numbers stay as they are), and
  `vault.restore(json, { json: true })` on JSON text.
- `vault.entries()` and `createVault(entries)` keep a vault between requests. With a middleware use `vault.toJSON()`
  and `createVault(state)`, which also carry what the model wrote for the answers that were restored. Both hold the
  original values: store them only where the original text may be stored.
- `vault.clear()` empties a vault. If it is cleared while a text is being masked (during `redact`, or while the
  middleware prepares a model call), that call fails with an error instead of writing into the emptied vault. An answer
  that arrives after a clear still gets the values of its own call.
- **Never share a vault between users**: a placeholder one user's model answer contains would bring back another
  user's value.

Both middlewares take `vault`. The model then gets numbered placeholders, and the answer's text comes back with the
values (streamed or not with the AI SDK). The default roles become `user`, `assistant` and `tool`, because earlier
answers and tool results now hold the real values; with the vault they are masked with the same placeholders. With a
vault, `redact` can only be `{ mode: "tag" }`: any other masking option throws when the middleware is created.

- **Only this call's placeholders are restored**, with the values they had when the call was masked: those the
  middleware put into that model call. They include the placeholders of an earlier answer that the app passes back with
  its values (and of reasoning with `restoreReasoning`, and tool arguments with `restoreTools`): the middleware sends it
  as the model wrote it, after a reload from `toJSON()` too. Placeholder-like text that was already in a message
  ("[PERSON_1]" typed by a user or found in a log) is sent as "［PERSON_1]" (full-width bracket) and never restored;
  placeholders the model wrote into earlier tool calls or answers that the app got back unrestored do not count either.
  In the developer's own text (a system message, LangChain's `systemPrompt`, and the
  [fields outside the messages](#fields-outside-the-messages): tool descriptions, schemas, the response format, provider
  options) such text stays as written, so a prompt can explain placeholders; the model then cannot tell it from a
  value's placeholder, so write examples that are not placeholders (`[PERSON_n]`). Percent and HTML forms
  (`%5BPERSON_1%5D`, `&#91;PERSON_1]`) are sent as written. A placeholder the model percent-encodes is not restored, and
  a value restored into a URL is not percent-encoded.
- **Tool-call arguments are restored only for the tools you name** in `restoreTools` (a name, names, or a test on the
  name). Other tools receive the placeholders. Text the model reads (a web page, a document, a tool result) can contain
  instructions that make the model put placeholders into a tool call, so name only tools that may see the values. JSON
  arguments get values back in string values only (keys and numbers stay as written, and arguments that are not valid
  JSON are passed on masked), and the next call sends the arguments as the model wrote them.
- Not restored: tool input inside LangChain content blocks, `invalid_tool_calls` and `additional_kwargs` (tools run
  from `tool_calls`), and AI SDK answer parts other than text, reasoning and tool calls.
- Earlier answers and tool calls go back to the model as the model wrote them (the middleware keeps a record of each,
  and `toJSON()` carries it) and are read by the model again, so a value the model itself wrote out is masked too.
  Where there is no record of an answer (a vault reloaded from `entries()`, an answer from before the vault was
  attached), every known value is masked wherever it occurs in it, also inside longer words: more may be masked there
  than needed, never less. User messages and tool results are always read as received.
- A message or tool result that is a JSON document is read value by value, with the whole document as context for the
  model: escapes ("\u00e9") cannot hide a value, and the JSON stays valid. The model's own earlier JSON answers and tool
  input stay valid JSON too, and keep every field when keys become one placeholder. A number that is found (a card or
  phone number written as a JSON number) becomes its placeholder, a string; it is the same number however it is written
  (8.21023456789e11 and 821023456789) and comes back in plain decimal form (12.5 for 1.25e1; exponents up to 64). Each
  value is read next to its key ("name: Kim Minsu"), which tells the model what the value is. The keys themselves are
  kept, except that a value the vault already holds is hidden in a key too (a map keyed by e-mail address). Two keys
  that become the same placeholder stay two fields (`[PERSON_1]` and `[PERSON_1] #2`); keys that were the same before
  masking stay the same.
- Any other text (a log line with JSON in it, a code fence, several JSON lines, a document that was cut off) is read
  whole. The model is given it through its Unicode escapes ("Jos\u00e9" as "José"). The values the vault already
  holds are also searched in the text as it is written (`u1234` in `C:\Users\u1234`) and in its other readings
  ([below](#how-a-known-value-is-found-again)).
- **What is masked is what is sent.** With a vault, every value in a call that JSON writes in a form of its own is built
  once into the plain JSON a provider writes, before anything is masked, and that JSON is masked and sent (since
  1.7.2). Every value is read the way `JSON.stringify` reads it: by property access, so a getter runs and a Proxy's
  traps answer (not the value a descriptor stores), each field and item once, in JSON's order. A class instance is
  written as its own enumerable fields, a `toJSON` object (a function with one too) as what its `toJSON()` gives
  (called once, so an object that writes something else the second time cannot send it), a boxed number, string or
  boolean as its value, a `Date` as its ISO text, `NaN` and `Infinity` as `null`, `Map` and `Set` as `{}` and bytes as
  JSON writes them. A list or an object with a getter (at an index too), a hole, a getter of its own that JSON does
  not write, or a Proxy (one Node can tell, or one whose read differs from what it stores) is rebuilt from the items
  and fields read once, so what the provider's writer reads is what was masked; a LangChain message, an AI SDK tool
  or response format with such a field is passed on as a copy of what was read. This holds in tool results and
  tool input, content parts and blocks and a message's tool calls, parts and blocks of types this version does not
  know, `additional_kwargs`, tool definitions and every schema of a call (a tool's input schema, the response format,
  their nested definitions and examples, `inputExamples`; a class instance at the top too), provider options and model
  settings. Bytes and URL objects stay as they are only where a file's data stands (an AI SDK file part's `data`, a
  LangChain file block's `data`).
- A value found in one message of a call is masked in all of them, also in the messages before it. Roles left out of
  `roles`, and the system prompt, still get the vault's known values hidden.
- Reasoning stays masked unless `restoreReasoning: true`. Reasoning the model wrote goes back to it exactly as written
  (providers sign it), also where it holds a value the vault learned later: the model wrote that value, so the provider
  had it. A JSON answer (structured output) gets its values back inside its strings, so it stays valid JSON, also
  when the model wrote it in a code fence that `extractJsonMiddleware` or a repair function takes off later; in a
  stream, when the call asked for JSON (`streamObject`), and there a placeholder is restored as it arrives (one that
  the model wrote with JSON escapes stays as it is).
- With the AI SDK, the reason a person gives when refusing a tool call is masked like any other text from outside.
- Before a call is sent, every text field of its messages is checked once more for the values the vault holds, with no
  model call, whatever kind of part it is in. Left alone, in the framework's own parts and blocks only: identifiers and
  names (`id`, `name`, `toolName`), references to files (`url`, `fileId`, AI SDK 7 `originalUrl`, and an AI SDK file
  part given by its URL, `data: "https://..."`; a known value inside such a URL is sent as written), base64 data,
  `providerMetadata` and signed reasoning. `providerOptions` are read as the options below are. In a part or block type
  this version does not know, every field but its `type` and `providerOptions` is checked, its `providerMetadata` too.
  In the data inside a part every string and key counts, also an object or list under `data`. A `data` string that is a
  known value as a whole is hidden. A data URL, or a string of 64
  characters or more that looks like base64, stays as it is unless a known value of 6 or more characters stands in it at
  word edges, as written or in one of its readings (`Kim+Minsu+asked+…`). A file given as inline text is read like any
  other text from outside.
- **A call whose masking cannot finish fails, and nothing of it is sent**: the detector fails, the vault is cleared
  while the call is masked, an input cannot be read (an object that refers to itself, very deeply nested data), JSON
  cannot write a value of the call (a BigInt, a `toJSON()`, a getter or a Proxy's trap that throws, a revoked Proxy; the
  error names its place), a number the vault holds stands where the call's own AI SDK adapter takes only a string
  (below), or a LangChain.js tool cannot be written as a function definition. A `NaN` in tool input is sent as `null`, as JSON writes it.
- Other middleware that calls a model on its own (a summarizer, for one) reads the agent's unmasked state: this
  middleware masks the calls that go through it.
- **List it last.** It masks the request it is given, so what a middleware listed after it adds (a dynamic system
  prompt, retrieved context) reaches the model unmasked: put it at the end of `middleware`, in `wrapLanguageModel`
  and in `createAgent`.

```ts
const vault = createVault();
const middleware = piiMiddleware({ redactor, vault, restoreTools: ["lookup_customer"] });
const model = wrapLanguageModel({ model: openai("gpt-6.1"), middleware });                    // AI SDK
const agent = createAgent({ model: "openai:gpt-6.1", tools, middleware: [piiMiddleware({ redactor, vault, restoreTools: ["lookup_customer"] })] }); // LangChain.js
```

With LangChain.js, tokens streamed through callbacks during a model call still show the placeholders; the message the
agent keeps has the values. With a vault, tool input and tool results inside content blocks (Anthropic `tool_use` and
`tool_result`, OpenAI `function_call`) are masked as well: JSON text value by value, rows of JSON data in every string
value. Only LangChain's and the providers' block types are blocks: in one, the model reads the text, tool input and tool
results, and the other fields only get the values the vault already holds hidden (the check before sending, above). In
what a tool returns or a user sends, an object is a block only when it has its type's own content (a `text` block its
`text`; a `file` or `image` its `url`, base64 data or file id; a `document` its `source`; a `json` block its `json`);
in what a tool returns a block's `id` and `name` are checked too, and in a user's message a block's `id` stays as
written, as in any block of the framework (its `name` is checked). Rows a tool returns with a `type` of their own (`{ type: "contact",
name, id }`), and rows typed like a block without its content (a storage API's `{ type: "file", id, name, owned_by }`),
are data: the model reads them, and every field and key counts. A class instance or a `toJSON` object a tool returns as
a block is sent as the JSON it gives, with the block fields its class gives by getters (`get text()`), which converters
read by name. `additional_kwargs` is data too, except its `tool_calls` and `function_call`. Messages of other kinds
(`ChatMessage`, `FunctionMessage`) are masked like text from outside. A known value in the `name` of a HumanMessage or
ChatMessage is sent as its placeholder without brackets (`PERSON_1`, a form providers accept there) and is never
restored. Providers take only letters, digits, `_` and `-` there, so a known value of two or more words is also found
joined (`Kim_Minsu`, `kim-minsu`, `KimMinsu` for `Kim Minsu`); a one-word value is found as written. An
OpenAI-compatible `name` in a message's or part's `providerOptions` is written the same way. The `structuredResponse` of
an agent gets this call's values back. Since 1.7.2 the AI SDK middleware also masks the strings inside JSON tool results
and the text items of content tool results when `"tool"` is a role (1.6 left them as they were).

### Fields outside the messages

With a vault, the values the vault holds are also hidden, with no model call, in these fields of a call: tool
descriptions; schema descriptions, titles and examples; `inputExamples`; provider tool args; the response format; and
`providerOptions` (the call's, a tool's, a message's or a part's) and, with LangChain.js, `modelSettings`. Those options
are data: every string and key in them (`openai.user`, `promptCacheKey`, a stored prompt's `variables`, `metadata`) and
every number that is a known value. The exceptions are exact paths, each one where a provider defines an identifier, a
credential, a numeric setting or a JSON schema (since 1.7.2, release gate 8: a name is kept only at its path). A value
at such a path stays as written (the keys along it too); the same name anywhere else (one level down, under another
provider's name or one the middleware does not know, in `extra_body`, in a template variable, in a settings field no
provider defines) is data, every key and value of it. "`*`" is any one key or index; "all of it" is everything below.

- AI SDK `providerOptions` of a call or a tool: `openai` and `azure` `previousResponseId`, `conversation` (or
  `conversation.id`), `promptCacheOptions.comparisonResponseId`, `prompt.id`, `prompt.version`, `logitBias.*`,
  `allowedTools.toolNames.*`; `anthropic` `container` (or `container.id` and `container.skills.*.type`, `.skillId`,
  `.version`), `mcpServers.*.type`, `.name`, `.url`, `.authorizationToken`, `.toolConfiguration.allowedTools.*`,
  `fallbacks.*.model`; `google` and `vertex` `cachedContent`; `gateway` `order.*`, `only.*`, `models.*`, `byok` (all of
  it); `openrouter` `provider` (all of it), `models.*`.
- AI SDK `providerOptions` of a message or a part: `openai` and `azure` `itemId`, `reasoningEncryptedContent`,
  `additionalTools.*.type`, `.name`; `anthropic` `signature`, `redactedData`, `toolChanges.*.type`, `.toolName`,
  `.tool.type`, `.tool.id`, `.tool.name`; `bedrock` `signature`, `redactedData`; `google` and `vertex`
  `thoughtSignature`.
- AI SDK provider tools' `args`, by the tool's id without its date (`anthropic.web_search_20250305` is
  `anthropic.web_search`): `openai.mcp` `serverLabel`, `serverUrl`, `connectorId`, `authorization`, `headers.*`,
  `allowedTools.*`, `allowedTools.toolNames.*`, `requireApproval`, `requireApproval.never.toolNames.*`;
  `openai.file_search` `vectorStoreIds.*`; `openai.code_interpreter` `container` (or `container.fileIds.*`);
  `openai.web_search` and `openai.web_search_preview` `filters.allowedDomains.*`, `filters.blockedDomains.*`;
  `openai.image_generation` `model`, `inputImageMask.fileId`, `inputImageMask.imageUrl`; `openai.shell`
  `environment.type`, `.containerId`, `.fileIds.*`, `.networkPolicy.allowedDomains.*`, `.networkPolicy.domainSecrets`
  (all of it); `openai.custom` `name`; `anthropic.web_search` and `anthropic.web_fetch` `allowedDomains.*`,
  `blockedDomains.*`. Every argument of another tool is data.
- LangChain.js `modelSettings` and the model's own request fields (below): `model`, `models.*`, `provider` (all of
  it), `previous_response_id`, `conversation` (or `conversation.id`), `prompt.id`, `prompt.version`, `container` (or
  `container.id` and `container.skills.*.type`, `.skill_id`, `.version`), `mcp_servers.*.type`, `.name`, `.url`,
  `.authorization_token`, `.tool_configuration.allowed_tools.*`, `cachedContent`, `cached_content`, `tools.*.type`,
  `tools.*.name`, `tools.*.function.name`, `functions.*.name`, `response_format.type`,
  `response_format.json_schema.name`, `text.format.type`, `text.format.name`, `tool_choice` (all of it),
  `function_call` (all of it), the built-in tools' `tools.*.server_label`, `.server_url`, `.connector_id`,
  `.authorization`, `.headers.*`, `.allowed_tools` (all of it), `.require_approval` (all of it), `.vector_store_ids.*`,
  `.container`, `.container.file_ids.*`, `.filters.allowed_domains.*`, `.filters.blocked_domains.*`,
  `.allowed_domains.*`, `.blocked_domains.*`, and `logit_bias.*`, `logitBias.*`, `headers.*`.
- Numbers that stay numbers: `maxTokens`, `max_tokens`, `maxOutputTokens`, `max_output_tokens`,
  `maxCompletionTokens`, `max_completion_tokens`, `max_tokens_to_sample`, `temperature`, `topP`, `top_p`, `topK`,
  `top_k`, `minP`, `min_p`, `seed`, `frequencyPenalty`, `frequency_penalty`, `presencePenalty`, `presence_penalty`,
  `repetitionPenalty`, `repetition_penalty`, `n`, `candidateCount`, `candidate_count`, `logprobs`, `topLogprobs`,
  `top_logprobs`, `budgetTokens`, `budget_tokens`, `thinkingBudget`, `thinking_budget`, `maxToolCalls`,
  `max_tool_calls`, `maxRetries`, `max_retries`, `timeout`, `dimensions`, `numCtx`, `num_ctx`, `numPredict` and
  `num_predict` directly under a provider the middleware knows (AI SDK: `openai`, `azure`, `anthropic`, `google`,
  `vertex`, `bedrock`, `mistral`, `groq`, `xai`, `deepseek`, `cohere`, `perplexity`, `fireworks`, `togetherai`,
  `cerebras`, `deepinfra`, `gateway`, `openrouter`, `openaiCompatible`, `ollama`, `huggingface`, `baseten`, `vercel`,
  `alibaba`, `moonshotai`), at the top of `modelSettings` and under its `generationConfig` and `generation_config`;
  and `anthropic.thinking.budgetTokens`, `anthropic.taskBudget.total`, `.remaining`, `anthropic.fallbacks.*.max_tokens`,
  `google` and `vertex` `thinkingConfig.thinkingBudget`, `bedrock.reasoningConfig.budgetTokens`, the settings'
  `thinking.budget_tokens`, `thinking.budgetTokens`, `thinkingConfig.thinkingBudget`,
  `thinking_config.thinking_budget`, and the tools' `openai.file_search` `maxNumResults`, `ranking.scoreThreshold`,
  `openai.image_generation` `outputCompression`, `partialImages`, `anthropic.web_search` and `anthropic.web_fetch`
  `maxUses`, `anthropic.web_fetch` `maxContentTokens`, `anthropic.computer` `displayWidthPx`, `displayHeightPx`,
  `displayNumber`. A string at those paths is still read.
- JSON schemas, read as the schemas above are (property names and literal constraints as written; a Zod or Standard
  Schema object stays, for the framework writes it): the settings' `response_format.json_schema.schema`,
  `text.format.schema`, `tools.*.parameters`, `tools.*.input_schema`, `tools.*.function.parameters`,
  `functions.*.parameters`, `responseSchema`, `response_schema`, `responseJsonSchema`, `response_json_schema`,
  `output_format.schema`, `outputFormat.schema`, `output_config.format.schema`, `outputConfig.format.schema`,
  `generationConfig.responseSchema`, `generationConfig.responseJsonSchema`, `generation_config.response_schema`,
  `generation_config.response_json_schema`; a message's `openai.additionalTools.*.parameters` and
  `anthropic.toolChanges.*.tool.inputSchema`; `openai.tool_search` `parameters`.

Where the call's own AI SDK adapter takes only a string (OpenAI's `user`, `safetyIdentifier` and `promptCacheKey`,
OpenAI Chat Completions' `metadata`, Anthropic's `metadata.userId`; the adapter is the model `wrapLanguageModel` wraps),
a number that is a known value fails the call with nothing sent: the adapter refuses a number there, and a placeholder
string would turn that request into one it sends. Elsewhere such a number is sent as its placeholder. LangChain's own call options in `modelSettings` (`signal`,
`callbacks`, `configurable`, ...) are not sent and stay as they are. Tool names (the AI SDK's `tools[].name` and
`toolChoice`), schema property names, `enum`, `const`, `default` and `pattern`, the call's headers and
`providerMetadata` stay as written. New values are not looked for in these fields: keep
personal information out of them. When the messages of a call hold placeholders, the schema of a JSON answer goes to the
provider without `format` and `pattern` (a date or an e-mail field cannot hold `[DATE_OF_BIRTH_1]`), and the answer is
checked against your schema after its values are back. With LangChain.js, a tool whose description or schema holds a
known value goes to the model as its function definition with the values hidden, and the agent still runs its own tool;
its provider-specific extras (`tool(..., { extras })`) are not sent for that call. A definition given as an object that
is not a LangChain tool (a class instance, getters), and a tool's parameters `{ name, description, schema }` with a Zod
schema, are read the same way; a response strategy made by another copy of LangChain (its CommonJS build next to this
package's ES module build) stays a strategy, with its schema hidden. `responseFormat` keeps its top title
as its name. The tools and schema of a `responseFormat` are hidden where the agent binds them to the model (the binding
the provider gets), and the agent reads its structured response with this call's values back before it checks it against
your schema; langchain 1.2.20 does not give middleware the response format, and this covers it there too. A model that
cannot be copied for this (one without `bindTools`) fails the call when the response format holds a known value.

The model's own fields that go into the request are data too (since 1.7.2): what a LangChain.js chat model reports as
its request parameters (`invocationParams`, also what tracing records), such as ChatOpenAI's `user`, `promptCacheKey`
and `modelKwargs` (`metadata`, `safety_identifier`, a stored prompt's `variables`), and the kwargs objects a provider
package spreads into the request (ChatAnthropic's `invocationKwargs`, with `metadata.user_id`). With a vault that holds
values, the agent gets a copy of the model for each call, whether or not these fields hold a known value: each field is
read once into the JSON the provider writes (its `toJSON` called once, a getter read once, a class instance or a Proxy
rebuilt from what was read), and the copy sends that, with the known values hidden by the rules and identifier
exemptions of the model settings above (a stored prompt's `id`, `previousResponseId`, Anthropic's `container`). A model
the copy makes (`bindTools`, `withConfig`, the model `initChatModel` makes when it is called) is copied too, and your
model is not changed. The copy is checked before the call: a field it cannot send hidden, or one JSON cannot write,
fails the call before anything is sent, with an error that names the field. A model that cannot be copied (a class that
keeps its fields in private `#` fields) fails the call when it reports its parameters for the request, before anything
is sent.

### How a known value is found again

In all six packages, for `redact` with a vault, `hide`, `hideValue` and the middlewares (shared cases:
`python/tests/vault_cases.json`):

- 3 characters or more; 2 when one of them is in a script written without spaces (Han to Extension J, kana from U+1B000
  too, Thai, Lao, Khmer, Myanmar, Tai Tham, Balinese, Javanese) or both are Hangul syllables (`이준`). A shorter value the
  application tags is masked where it is a whole JSON value, key or tool argument, where `hideValue` meets it as a whole
  string, and in model text without a record; one the model found is masked where it was found and in model text
  without a record, and given back in answers.
- At word edges: not inside a longer word (letters, digits, marks and `_` make words). A letter of a script written
  without spaces on either side is an edge (`注文ZX-88213-Kは`), but not a vowel sign or another mark after the value:
  it belongs to the letter before it (`สม` is not found in `สมัคร`). Korean particles may follow a Hangul value
  (`김민수님께`), Hangul right before it is not an edge (`이김민수`), and Hangul next to Latin letters or digits is
  one. After a value of two Hangul syllables, only particles, the honorifics 님 and 씨 or the copula may follow in the
  same word (`지원님께` is the name, `지원금` another word). Letters glued to a name make another word: an Arabic or
  Hebrew prefix (`ومحمد`), a case ending or a suffix (Turkish, Finnish, German `-s`).
- Letter case: an e-mail address, a user name and a value of two or more words match in any ASCII case, and a URL's
  scheme and host in any case (its path as written). Any value matches in capitals: its full Unicode uppercase (`JOSÉ`
  for `José`, `WEISS` for `Weiß`), and as Turkish and Azerbaijani (`EMİNE` for `Emine`), Greek (without accents and
  breathings: `ΝΙΚΟΣ` for `Νίκος`) and German (`WEIẞ` for `Weiß`) write capitals. A one-word value in another case is
  another word (`Will` and `will`).
- A value found takes in the combining marks, variation selectors and joiners right after it, so its span never ends
  inside a grapheme cluster.
- NFC and NFD are the same (`José` either way, Hangul written as conjoining jamo; the four native packages compose
  Hangul only). Full-width ASCII (`ｋｉｍ＠example.com`) and the ideographic space U+3000 are read as ASCII.
  Mathematical and look-alike letters, and digits of other scripts, are another value.
- A dot between digits joins them: a value that starts or ends with a digit is not found where a dot joins it to more
  digits (`4524` in `4524.7` or `1.4524`, also `12 Main St` in `Unit 4.12 Main St`). An exponent joins only numbers:
  `4524` is not found in `1e+4524`, while an address, a phone number or a postal code after `4E+` is found
  (`Unit+4E+12+Main+St`). In the middlewares the same holds when the sign or dot is percent- or Unicode-escaped
  (`1e%2B4524`, `4524%2E7`).
- A value found in another spelling keeps the known value's placeholder: one value, one placeholder. Where two known
  values cover the same part of a text, the one the vault has held longer wins.
- What the model marks is learned without the white space, punctuation, brackets and quotation marks at its edges that
  are not part of the value (`Kim Minsu.`, `"Kim Minsu"` and `(Kim Minsu` are learned as `Kim Minsu`, and those
  characters stay in the text), unless what is left is too short to be found (then only the white space goes). A
  bracket whose partner is in the value stays (`(02) 1234-5678`), and so do a `.` at the start of a value and the `.`
  at the end of an ORG (`Tiffany & Co.`); a SECRET loses only its white space.

The middlewares also find:

- White space written another way. A space matches any white space (tab, NBSP, U+3000, several spaces). A line break
  also matches a comma and a space, or an indented line. A comma and a space also match a line break, or a plain space
  (`12 Main St Seoul`). Other punctuation (`,` with no space, `;`) makes another value, and so do white space at the
  start or end of a value, a zero-width space, joiner or soft hyphen inside it, and a space put into a value that has
  none (`김 민수`). `\v` and `\f` are white space, not line breaks.
- A number in another spelling, compared digit for digit (exponents up to 64): `8.21023456789e11` for `821023456789`,
  trailing zeros, `E`. A zero in front (`04524`) makes a string of digits, found as written only. Digit groups
  (`4,524`, `4 524`) and other punctuation in a phone number (`01012345678` for `010-1234-5678`) are another value. A
  sign stays outside the placeholder: for a known `4524`, `-4524` and `-4.524e3` become `-[ID_NUMBER_1]`, and the JSON
  number `-4524` becomes the string `"-[ID_NUMBER_1]"`.
- A known ISO date (`1990-01-02`) at the start of an ISO timestamp (`1990-01-02T00:00:00.000Z`, as a `Date` gives it)
  and in a Python date repr (`datetime.date(1990, 1, 2)`). Other orders and separators (`01/02/1990`, `2 Jan 1990`)
  are another value.
- Other readings of the text: through JSON escapes (a line break as `\n`, `CORP\\kim` for `CORP\kim`,
  `01\/02\/1990`, also JSON inside a JSON string), percent escapes as UTF-8 (`kim%40example.com`), `+` as a space
  and as itself (`name=Kim+Minsu`), and Python repr escapes (`Kim\xa0Minsu`, `b'Jos\xc3\xa9'` as UTF-8,
  `\U00020bb7`). These may stand inside one another up to four steps deep (JSON in a form field, a form field in
  JSON). Every reading within four steps is searched, however many escapes the text holds elsewhere, and the readings
  are searched again until the vault learns nothing new.
- Read as written, so a value in such a form is masked only where the model finds it: HTML entities and tags
  (`&#233;`, `<br>`), base64, quoted-printable and MIME words, doubled quotes (SQL, CSV), Markdown and shell
  backslashes, `%uXXXX`, punycode host names (`xn--`), other charsets (EUC-KR) and bytes that are not UTF-8, and a
  fifth step of encoding.

### Limits

- **One middleware, one vault.** A wrapped model or a LangChain.js agent built once and used for many conversations
  shares its vault, so build the middleware per conversation. The vault is not part of LangGraph's checkpointer: keep
  `vault.toJSON()` next to the conversation yourself.
- The vault has no size limit. The middleware keeps a record of the 4,096 answers and tool calls used last (older ones
  are then searched loosely: more masking, never less) and the model's spans for the last 256 texts.
- Spans are kept per vault generation and `sensitivity` (taken when `detect` is called), and the vault is applied
  again on every call, so a value learned later also covers text masked before. A value learned from a text is masked
  wherever it occurs in that text on the first call, and what the model finds inside a known value or a larger merged
  span is learned too. What the model finds is learned without what its span took from around the value:
  white space, a sentence's period or comma, brackets and quotation marks (`Kim Minsu` from `I'm Kim Minsu.`), the `b'`
  of a Python bytes literal, and the time of a date of birth's ISO timestamp.
- With a vault, the redactor needs `detect()`; one without it fails with a clear message.
- Allow and block lists work with a vault: the model and the lists read the text as written, and a restored answer
  that is sent again is read by both again.
- What the middleware gives back (tool arguments, an exported state) is a copy: changing it does not change the vault.
- `applyRedaction` and `redactTable` with a vault only number the spans they are given; they do not search for known
  values.
- Not masked, because they are not sent to providers: LangChain's `response_metadata`, `usage_metadata` and a
  ToolMessage's `artifact`. `Map` and `Set` in data are sent as JSON writes them (`{}`); a BigInt fails the call, as
  `JSON.stringify` does (unless your app gives `BigInt.prototype` a `toJSON`).
- Placeholders are `[<FAMILY>_<n>]`: a family of up to 32 characters (`_` for each character that is not allowed) and
  1 to 12 ASCII digits; longer look-alikes are ordinary text. PERSON_L2 shares PERSON's numbers. Numbers the model
  makes up are skipped while they are within a million of the numbers in use, and `toJSON()` keeps them. A family
  that has used its numbers (up to 999,999,999,999) goes on as `[PERSON__1]`, so a state the vault gave out always
  loads. An empty value gets no placeholder.
- The vault's word characters come from the runtime's Unicode data (`wholeWords` uses a pinned table; the vault does
  not). Node 25 has Unicode 17. The package declares no Node version, and an older runtime can treat a newer character
  next to a value differently.
- Run for this release: AI SDK 5.0.290, 6.0.332 and 7.0.173, and LangChain.js `createAgent` with langchain 1.6.6
  (`@langchain/core` 1.4.1, `@langchain/openai` 1.8.4, `@langchain/anthropic` 1.6.5) and 1.5.1 (`@langchain/core` 1.2.1,
  `@langchain/openai` 1.5.3, `@langchain/anthropic` 1.5.1); the LangChain.js tests and the shared corpus also with 1.6.8.
  The peer range starts at langchain 1.5.1 with `@langchain/core` 1.2.1 (1.6 took 1.0.0 or later): every release from
  1.5.1 to 1.6.8 passes the structured-output check, the type check and the LangChain.js tests. Earlier releases are
  not supported: from 1.0.1 to 1.1.2 an agent with a `responseFormat` fails with any middleware that wraps the model
  call (LangChain's own check); from 1.1.4 to 1.2.22 the agent writes a provider strategy's Anthropic schema under
  `output_format`, where this middleware does not take off `format` and `pattern` (the values are hidden); and from
  1.2.3 to 1.5.0 the agent's state, written as JSON, leaves out what a tool returned as a class instance, with or
  without this middleware.
- **Time.** The vault's work grows with the length of the text times the number of known values. Large data with
  thousands of known values is slower. With 2,000 known values, 2,000 JSON rows (181 KB) took 2.0 to 2.1 s per call,
  4,000 rows (362 KB) 4.0 to 4.2 s, 1 MB of prose 4.7 to 4.8 s, and 512 KB of nested encodings (percent and form encodings,
  Unicode escapes, JSON in JSON) 9.1 to 9.2 s (vault work only, no model; the median of three calls, in each of two
  runs, on an Apple M5 Pro with Node 25). A faster search, under 1 s per call for 1 MB with 2,000 known values in
  every shape, is planned for 1.7.3.

## Development

```bash
npm install
npm run typecheck   # tsc --noEmit
npm test            # vitest; the parity tests skip without fixtures
npm run build       # dist/
npm run demo        # test page (vite), needs demo/models/
```

The built-in engine's WebAssembly is checked in as base64 (`src/engines/wasm-*.ts`); after changing `engine/pii.c`, run
`engine/build.sh` (LLVM clang with the wasm32 target) and then `node scripts/embed-wasm.mjs`.

The demo page and the parity tests read local copies of the four Hugging Face model repos (`newmean-ai/pii-<size>`):

```
demo/models/pii-<size>/tokenizer.web.json
demo/models/pii-<size>/onnx/model_q4.onnx, model_q4e.onnx
test/models/pii-<size>/...    (same layout; or set PII_MODELS to a folder with it)
```

The parity tests compare the SDK with the Python evaluator on reference outputs in `test/fixtures/`. The fixtures are made
from validation text and are not published, so these tests skip without them.

## License

[Apache-2.0](LICENSE), for the SDK code and the model weights; third-party notices in [NOTICE](NOTICE). If you use them in a product, we would appreciate a "Powered by newmean.ai" mention in your docs or an about or licenses screen (a request, not a license condition). Made by [newmean.ai](https://newmean.ai).
