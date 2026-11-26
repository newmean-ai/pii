import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { AIMessage, createAgent, HumanMessage, providerStrategy, SystemMessage, tool, ToolMessage, toolStrategy, type BaseMessage } from "langchain";
import { z } from "zod";
import { ChatMessage, FunctionMessage } from "@langchain/core/messages";
import { FakeListChatModel } from "@langchain/core/utils/testing";
import { convertToOpenAITool } from "@langchain/core/utils/function_calling";
import { piiMiddleware } from "../src/langchain";
import { createMasker, OPTION_PATHS } from "../src/masker";
import type { Redactor } from "../src/redactor";

// A test with an assertion on measured time (a doubling ratio or a time limit) runs alone, never inside a functional
// step (the 1.7.2 release standard, CI3): PII_SKIP_TIMING=1 skips it (the functional steps of public CI), and
// PII_TIMING_ONLY=1 runs it and none of this file's other tests (vault.yml's timing job, with --allowOnly).
const timingIt = process.env.PII_SKIP_TIMING === "1" ? it.skip : process.env.PII_TIMING_ONLY === "1" ? it.only : it;

const MODELS = process.env.PII_MODELS ?? new URL("./models/", import.meta.url).pathname;
const localFetch = (async (url: string) => new Response(readFileSync(url.replace("file://", "")))) as unknown as typeof fetch;

// a redactor that masks every digit run, so the message handling is tested without a model
const digits = { redact: async (text: string) => ({ text: text.replace(/\d+/g, "[N]"), spans: [] }) } as unknown as Redactor;

// what the model is called with, through the middleware's own hook
const run = async (mw: ReturnType<typeof piiMiddleware>, messages: BaseMessage[]) => {
  let seen: BaseMessage[] = [];
  const hook = mw.wrapModelCall as unknown as (request: object, handler: (r: { messages: BaseMessage[] }) => Promise<AIMessage>) => Promise<AIMessage>;
  await hook({ messages, systemPrompt: "keep 1" }, async (r) => ((seen = r.messages), new AIMessage("ok")));
  return seen;
};

describe("piiMiddleware (message handling)", () => {
  const history = () => [
    new HumanMessage({ content: "my number is 42", id: "h1", name: "kim" }),
    new AIMessage({ content: "noted 42", id: "a1", tool_calls: [{ id: "c1", name: "t", args: { n: 42 } }] }),
    new ToolMessage({ content: "row 9", id: "t1", tool_call_id: "c1" }),
    new HumanMessage({ content: [{ type: "text", text: "and 7" }, { type: "image_url", image_url: "data:x" }], id: "h2" }),
    new HumanMessage({ content: "no number", id: "h3" }),
  ];

  it("masks user messages only by default, keeps ids and other fields, and leaves the originals", async () => {
    const messages = history();
    const seen = await run(piiMiddleware({ redactor: digits }), messages);
    expect(seen.map((m) => m.content)).toEqual([
      "my number is [N]",
      "noted 42",
      "row 9",
      [{ type: "text", text: "and [N]" }, { type: "image_url", image_url: "data:x" }],
      "no number",
    ]);
    expect([seen[0].id, seen[0].name, HumanMessage.isInstance(seen[0])]).toEqual(["h1", "kim", true]);
    expect(seen[4]).toBe(messages[4]); // unchanged messages are passed as they are
    expect(messages[0].content).toBe("my number is 42");
  });

  it("masks tool results and assistant messages when asked", async () => {
    const seen = await run(piiMiddleware({ redactor: digits, roles: ["tool", "assistant"] }), history());
    expect(seen[0].content).toBe("my number is 42");
    expect(seen[1].content).toBe("noted [N]");
    expect((seen[1] as AIMessage).tool_calls).toEqual([expect.objectContaining({ id: "c1", name: "t", args: { n: 42 } })]);
    expect([seen[2].content, (seen[2] as ToolMessage).tool_call_id, ToolMessage.isInstance(seen[2])]).toEqual(["row [N]", "c1", true]);
  });
});

describe.skipIf(!existsSync(`${MODELS}/pii-nano/onnx/model_q4.onnx`))("piiMiddleware in an agent with the nano model", () => {
  it("the model gets the masked text and the agent's state keeps the original", async () => {
    const seen: string[] = [];
    class Model extends FakeListChatModel {
      async _generate(messages: BaseMessage[], ...rest: [any, any?]) {
        seen.push(String(messages[messages.length - 1].content));
        return super._generate(messages, ...rest);
      }
      bindTools() {
        return this; // the stock fake returns a new instance, which would drop this subclass
      }
    }
    const create = { size: "nano" as const, modelUrl: `file://${MODELS}/pii-nano/`, fetch: localFetch, cache: false };
    const agent = createAgent({ model: new Model({ responses: ["ok"] }), tools: [], middleware: [piiMiddleware({ create })] });
    const text = "Please call Kim Minsu on 010-1234-5678 or write to kim@example.com.";
    const out = await agent.invoke({ messages: [new HumanMessage(text)] });
    expect(seen).toHaveLength(1);
    expect(seen[0]).toContain("[EMAIL]");
    expect(seen[0]).not.toContain("010-1234-5678");
    expect(out.messages[0].content).toBe(text);
  });
});

import { applyRedaction, withVaultSpans } from "../src/redactor";
import { createVault } from "../src/vault";

// a redactor that finds one fixed name, with the vault handling of the real one
const kimSpans = async (text: string) => {
  const found = [];
  for (let i = text.indexOf("Kim Minsu"); i >= 0; i = text.indexOf("Kim Minsu", i + 1)) found.push({ start: i, end: i + 9, label: "PERSON", source: "model" as const });
  return found;
};
const kim = {
  detect: kimSpans,
  async redact(text: string, o?: Parameters<Redactor["redact"]>[1]) {
    const found = await kimSpans(text);
    const spans = o?.vault ? withVaultSpans(text, found, o.vault) : found;
    return { text: applyRedaction(text, spans, o), spans };
  },
} as unknown as Redactor;

describe("piiMiddleware with a vault", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: { messages: BaseMessage[] }) => Promise<AIMessage>) => Promise<AIMessage>;

  it("restores this call's placeholders in the answer, and tool arguments only for restoreTools", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Lee Old"); // [PERSON_1], from an earlier conversation turn, not in this call
    const mw = piiMiddleware({ redactor: kim, vault, restoreTools: ["send"] });
    let seen: BaseMessage[] = [];
    const answer = await hook(mw)({ messages: [new HumanMessage("Email Kim Minsu please")] }, async (r) => {
      seen = r.messages;
      return new AIMessage({
        content: [{ type: "reasoning", reasoning: "user is [PERSON_2]" }, { type: "text", text: "Sending to [PERSON_2], not [PERSON_1]." }],
        id: "a1",
        tool_calls: [
          { id: "c1", name: "send", args: { to: "[PERSON_2]", n: 2 } },
          { id: "c2", name: "post", args: { body: "[PERSON_2]" } },
        ],
      });
    });
    expect(seen[0].content).toBe("Email [PERSON_2] please");
    expect(answer.content).toEqual([{ type: "reasoning", reasoning: "user is [PERSON_2]" }, { type: "text", text: "Sending to Kim Minsu, not [PERSON_1]." }]);
    expect(answer.id).toBe("a1");
    expect(answer.tool_calls?.map((c) => c.args)).toEqual([{ to: "Kim Minsu", n: 2 }, { body: "[PERSON_2]" }]);

    // next call: the restored answer, its tool call and the tool result are masked again with the same placeholder
    const history = [new HumanMessage("Email Kim Minsu please"), answer, new ToolMessage({ content: "sent to Kim Minsu", tool_call_id: "c1" })];
    await hook(mw)({ messages: history }, async (r) => ((seen = r.messages), new AIMessage("done")));
    expect(seen[0].content).toBe("Email [PERSON_2] please");
    expect((seen[1].content as any[])[1].text).toBe("Sending to [PERSON_2], not [PERSON_1]."); // the answer as the model wrote it
    expect((seen[1] as AIMessage).tool_calls?.[0].args).toEqual({ to: "[PERSON_2]", n: 2 });
    expect(seen[2].content).toBe("sent to [PERSON_2]");
    expect((history[1].content as any[])[1].text).toBe("Sending to Kim Minsu, not [PERSON_1]."); // the agent's state is not changed
  });

  it("passes an answer without placeholders through unchanged", async () => {
    const mw = piiMiddleware({ redactor: kim, vault: createVault() });
    const plain = new AIMessage("nothing here");
    expect(await hook(mw)({ messages: [new HumanMessage("hi")] }, async () => plain)).toBe(plain);
  });

  it("masks bare strings and any text block in content lists, and replays tool arguments as the model wrote them", async () => {
    const vault = createVault();
    vault.tag("ID_NUMBER", "42");
    const mw = piiMiddleware({ redactor: kim, vault, restoreTools: "lookup" });
    let seen: BaseMessage[] = [];
    const input = new HumanMessage({ content: ["Kim Minsu", { type: "custom", text: "also Kim Minsu" }] as any });
    const answer = await hook(mw)({ messages: [input] }, async (r) => {
      seen = r.messages;
      return new AIMessage({ content: "ok", tool_calls: [{ id: "c9", name: "lookup", args: { ref: "id [ID_NUMBER_1] for [PERSON_1]" } }] });
    });
    expect(seen[0].content).toEqual(["[PERSON_1]", { type: "custom", text: "also [PERSON_1]" }]);
    expect(answer.tool_calls?.[0].args).toEqual({ ref: "id [ID_NUMBER_1] for Kim Minsu" });
    await hook(mw)({ messages: [input, answer] }, async (r) => ((seen = r.messages), new AIMessage("done")));
    expect((seen[1] as AIMessage).tool_calls?.[0].args).toEqual({ ref: "id [ID_NUMBER_1] for [PERSON_1]" });
  });

  it("masks tool input and tool results inside content blocks", async () => {
    const vault = createVault();
    const mw = piiMiddleware({ redactor: kim, vault });
    let seen: BaseMessage[] = [];
    const messages = [
      new HumanMessage("Find Kim Minsu"),
      new AIMessage({
        content: [
          { type: "tool_use", id: "c1", name: "crm", input: { name: "Kim Minsu", limit: 3 } },
          { type: "function_call", call_id: "c2", name: "crm", arguments: '{"name":"Kim Minsu"}' },
          { type: "server_tool_result", tool_call_id: "s1", output: { rows: ["Kim Minsu, [PERSON_1]"] } },
        ] as any,
        invalid_tool_calls: [{ id: "c3", name: "crm", args: '{"name": "Kim Minsu"', error: "cut", type: "invalid_tool_call" }],
      }),
      new HumanMessage({ content: [{ type: "tool_result", tool_use_id: "c1", content: [{ type: "text", text: "Kim Minsu, VIP" }] }] as any }),
      new ToolMessage({ content: [{ type: "tool_result", tool_use_id: "c1", content: "Kim Minsu again" }] as any, tool_call_id: "c1" }),
    ];
    await hook(mw)({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(JSON.stringify(seen)).not.toContain("Kim Minsu");
    const blocks = seen[1].content as any[];
    expect(blocks[0].input).toEqual({ name: "[PERSON_1]", limit: 3 });
    expect(blocks[1].arguments).toBe('{"name":"[PERSON_1]"}');
    expect(blocks[2].output).toEqual({ rows: ["[PERSON_1], \uff3bPERSON_1]"] }); // a tool result is read as received
    expect((seen[1] as AIMessage).invalid_tool_calls?.[0].args).toBe('{"name": "[PERSON_1]"');
    expect((seen[2].content as any[])[0].content).toEqual([{ type: "text", text: "[PERSON_1], VIP" }]);
    expect((seen[3].content as any[])[0].content).toBe("[PERSON_1] again");
    expect((messages[1].content as any[])[0].input).toEqual({ name: "Kim Minsu", limit: 3 }); // the agent's state is not changed
  });

  it("masks rows of JSON data in tool results, keeps numbers in JSON text, and records what the model wrote", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Alice");
    vault.tag("ID_NUMBER", "42");
    const mw = piiMiddleware({ redactor: kim, vault });
    let seen: BaseMessage[] = [];
    // an answer with a tool call that is only in a content block, and one for a tool that is not restored
    const answer = await hook(mw)({ messages: [new HumanMessage("hi")] }, async () =>
      new AIMessage({
        content: [{ type: "tool_use", id: "b1", name: "search", input: { q: "1425" } }] as any,
        tool_calls: [{ id: "c1", name: "search", args: { q: "none" } }],
      }),
    );
    (answer.tool_calls![0].args as { q: string }).q = "xAlicey"; // the app changes the returned arguments in place
    const rows = new AIMessage({
      content: [
        { type: "server_tool_result", tool_call_id: "s1", output: [{ name: "Alice" }, "Alice", { type: "text", text: "Alice" }, ["Alice"]] },
        { type: "function_call", call_id: "c9", name: "crm", arguments: '{"id":9007199254740993,"who":"Alice","n":1e400}' },
      ] as any,
    });
    await hook(mw)({ messages: [answer, rows] }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect((seen[0].content as any[])[0].input).toEqual({ q: "1425" }); // its own record: not searched inside "1425"
    expect((seen[0] as AIMessage).tool_calls?.[0].args).toEqual({ q: "none" }); // what the model wrote, not the later change
    const blocks = seen[1].content as any[];
    expect(blocks[0].output).toEqual([{ name: "[PERSON_1]" }, "[PERSON_1]", { type: "text", text: "[PERSON_1]" }, ["[PERSON_1]"]]);
    expect(blocks[1].arguments).toBe('{"id":9007199254740993,"who":"[PERSON_1]","n":1e400}');
  });

  it("hides known values in the system prompt from the first call, reads JSON documents value by value, keeps reasoning", async () => {
    const vault = createVault();
    const mw = piiMiddleware({ redactor: kim, vault });
    let request: any = {};
    const systemMessage = new SystemMessage("You are helping the customer Kim Minsu.");
    const thinking = { type: "thinking", thinking: "Kim Minsu is the user", signature: "sig" };
    const answer = await hook(mw)({ messages: [new HumanMessage("I am Kim Minsu")], systemMessage }, async (r) => {
      request = r;
      return new AIMessage({ content: [{ type: "thinking", thinking: "Kim Minsu is the user", signature: "sig" }, { type: "text", text: '{"name": "[PERSON_1]"}' }] as any });
    });
    expect(request.systemMessage.content).toBe("You are helping the customer [PERSON_1].");
    expect(SystemMessage.isInstance(request.systemMessage)).toBe(true);
    expect((answer.content as any[])[1].text).toBe('{"name": "Kim Minsu"}');
    const messages = [
      new SystemMessage("Customer: Kim Minsu"),
      answer,
      new ToolMessage({ content: '{"name": "Kim Minsu", "alias": "Kim M\\u0069nsu", "n": 9007199254740993}', tool_call_id: "c1" }),
    ];
    await hook(mw)({ messages, systemPrompt: "About Kim Minsu" }, async (r) => ((request = r), new AIMessage("ok")));
    expect(request.systemPrompt).toBe("About [PERSON_1]");
    expect(request.messages[0].content).toBe("Customer: [PERSON_1]");
    expect((request.messages[1].content as any[])[0]).toEqual(thinking); // as the model wrote it, signature and all
    expect(request.messages[2].content).toBe('{"name": "[PERSON_1]", "alias": "[PERSON_1]", "n": 9007199254740993}');
  });

  it("masks rows of JSON data in a hand-built tool message, and reads the rows of a result as one document", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Alice");
    const seenTexts: string[] = [];
    const reads = { ...kim, detect: async (t: string) => (seenTexts.push(t), kimSpans(t)) } as unknown as Redactor;
    const mw = piiMiddleware({ redactor: reads, vault });
    let seen: BaseMessage[] = [];
    const messages = [
      new ToolMessage({ content: [{ name: "Alice" }, ["Alice"]] as any, tool_call_id: "c1" }),
      new AIMessage({ content: [{ type: "server_tool_result", tool_call_id: "s1", output: [{ name: "Kim Minsu" }, { name: "Lee Sora" }] }] as any }),
    ];
    await hook(mw)({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(seen[0].content).toEqual([{ name: "[PERSON_1]" }, ["[PERSON_1]"]]);
    expect((seen[1].content as any[])[0].output).toEqual([{ name: "[PERSON_2]" }, { name: "Lee Sora" }]);
    expect(seenTexts).toContain("name: Kim Minsu\nname: Lee Sora"); // one reading for the rows of one result
  });

  it("masks messages of other kinds and provider data beside the content, and leaves them alone without a vault", async () => {
    const vault = createVault();
    const mw = piiMiddleware({ redactor: kim, vault });
    let seen: BaseMessage[] = [];
    const messages = () => [
      new HumanMessage("I am Kim Minsu"),
      new ChatMessage({ content: "note from Kim Minsu", role: "developer" }),
      new FunctionMessage({ content: "Kim Minsu, VIP", name: "crm" }),
      new AIMessage({ content: "ok", additional_kwargs: { function_call: { name: "crm", arguments: '{"who":"Kim Minsu"}' }, note: "Kim Minsu" } }),
    ];
    await hook(mw)({ messages: messages() }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(seen.map((m) => m.content)).toEqual(["I am [PERSON_1]", "note from [PERSON_1]", "[PERSON_1], VIP", "ok"]);
    expect([ChatMessage.isInstance(seen[1]), (seen[1] as ChatMessage).role, FunctionMessage.isInstance(seen[2]), seen[2].name]).toEqual([true, "developer", true, "crm"]);
    expect(seen[3].additional_kwargs).toEqual({ function_call: { name: "crm", arguments: '{"who":"[PERSON_1]"}' }, note: "[PERSON_1]" });
    await hook(piiMiddleware({ redactor: kim }))({ messages: messages() }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(seen.slice(1).map((m) => m.content)).toEqual(["note from Kim Minsu", "Kim Minsu, VIP", "ok"]); // as in 1.6
  });

  // SDK 1.7.1, data walking and keys ------------------------------------------------------------------------------------
  it("reads typed rows of a tool as data, class instances in blocks as their JSON, and data under a block's data field", async () => {
    const vault = createVault();
    vault.tag("EMAIL", "kim@example.com");
    const seenTexts: string[] = [];
    const reads = { ...kim, detect: async (t: string) => (seenTexts.push(t), kimSpans(t)) } as unknown as Redactor;
    const mw = piiMiddleware({ redactor: reads, vault });
    class Customer {
      constructor(
        public name: string,
        public email: string,
      ) {}
    }
    const withToJSON = { toJSON: () => ({ owner: "kim@example.com" }) };
    let seen: BaseMessage[] = [];
    const messages = [
      // G2: rows with a type of their own are data: the model reads them, every field and key counts
      new ToolMessage({ content: [{ type: "contact", name: "Kim Minsu", id: "kim@example.com", "kim@example.com": "primary" }] as any, tool_call_id: "c1" }),
      // G3: a class instance and a toJSON object inside a block
      new ToolMessage({ content: [{ type: "json", json: new Customer("Lee Sora", "kim@example.com") }, { type: "json", json: withToJSON }] as any, tool_call_id: "c2" }),
      // B1: an object under a block field named data is data; a list too
      new ToolMessage({ content: [{ type: "file", mime_type: "application/json", data: { name: "kim@example.com", "kim@example.com": 1 } }, { type: "file", data: [{ id: "kim@example.com" }] }] as any, tool_call_id: "c3" }),
      // a block type no framework has, in what a model wrote: its text is still model text; its other fields are data
      new AIMessage({ content: [{ type: "future_block", text: "for Kim Minsu", id: "kim@example.com" }] as any }),
    ];
    await hook(mw)({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(seen[0].content).toEqual([{ type: "contact", name: "[PERSON_1]", id: "[EMAIL_1]", "[EMAIL_1]": "primary" }]);
    expect(seenTexts.some((t) => t.includes("name: Kim Minsu"))).toBe(true); // read by the model as one document
    expect(seen[1].content).toEqual([{ type: "json", json: { name: "Lee Sora", email: "[EMAIL_1]" } }, { type: "json", json: { owner: "[EMAIL_1]" } }]);
    expect(seen[2].content).toEqual([{ type: "file", mime_type: "application/json", data: { name: "[EMAIL_1]", "[EMAIL_1]": 1 } }, { type: "file", data: [{ id: "[EMAIL_1]" }] }]);
    expect(seen[3].content).toEqual([{ type: "future_block", text: "for [PERSON_1]", id: "[EMAIL_1]" }]);
    // a class instance with nothing to hide is sent as the plain JSON it gives (release gate 4: the tree that was read
    // is the tree that is sent); bytes in a payload are data, written as JSON writes them, and stay binary only where a
    // file's data stands
    const plain = new Customer("Lee Sora", "lee@example.org");
    const bytes = new TextEncoder().encode("kim@example.com");
    const file = new TextEncoder().encode("%PDF");
    await hook(mw)(
      { messages: [new ToolMessage({ content: [{ type: "json", json: plain }, { type: "json", json: { raw: bytes } }, { type: "file", data: file, mime_type: "application/pdf" }] as any, tool_call_id: "c4" })] },
      async (r) => ((seen = r.messages), new AIMessage("ok")),
    );
    const sentBlocks = seen[0].content as any[];
    expect(Object.getPrototypeOf(sentBlocks[0].json)).toBe(Object.prototype);
    expect(sentBlocks[0].json).toEqual({ name: "Lee Sora", email: "lee@example.org" });
    expect(sentBlocks[1].json.raw).toEqual(JSON.parse(JSON.stringify(bytes)));
    expect(sentBlocks[2].data).toBe(file);
  });

  it("walks the top of additional_kwargs as data, and its tool calls as the framework's", async () => {
    const vault = createVault();
    vault.tag("EMAIL", "kim@example.com");
    const mw = piiMiddleware({ redactor: kim, vault });
    let seen: BaseMessage[] = [];
    const kwargs = {
      name: "Kim Minsu",
      id: "kim@example.com",
      "kim@example.com": "key",
      tool_calls: [{ id: "kim@example.com", type: "function" as const, function: { name: "lookup", arguments: '{"who":"Kim Minsu"}' } }],
      function_call: { name: "kim@example.com", arguments: '{"who":"kim@example.com"}' },
    };
    await hook(mw)({ messages: [new HumanMessage("I am Kim Minsu"), new AIMessage({ content: "ok", additional_kwargs: kwargs })] }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(seen[1].additional_kwargs).toEqual({
      name: "[PERSON_1]",
      id: "[EMAIL_1]",
      "[EMAIL_1]": "key",
      // the framework's identifiers stay as they are; their arguments are text
      tool_calls: [{ id: "kim@example.com", type: "function", function: { name: "lookup", arguments: '{"who":"[PERSON_1]"}' } }],
      function_call: { name: "kim@example.com", arguments: '{"who":"[EMAIL_1]"}' },
    });
  });

  it("reads an inline document, rows at the top level as one document, and keeps what is not text", async () => {
    const vault = createVault();
    vault.tag("EMAIL", "alice@example.com");
    const seenTexts: string[] = [];
    const reads = { ...kim, detect: async (t: string) => (seenTexts.push(t), kimSpans(t)) } as unknown as Redactor;
    const mw = piiMiddleware({ redactor: reads, vault });
    let seen: BaseMessage[] = [];
    const messages = [
      new ToolMessage({
        content: [
          { type: "document", source: { type: "text", media_type: "text/plain", data: "Client alice@example.com, Kim Minsu" } },
          { type: "image", source: { type: "base64", media_type: "image/png", data: "alice@example.com" } },
          { type: "json", json: { name: "alice@example.com", contact: "alice@example.com" } },
        ] as any,
        tool_call_id: "c1",
      }),
      new ToolMessage({ content: [{ who: "Kim Minsu" }, { who: "Lee Sora" }] as any, tool_call_id: "c2" }),
    ];
    await hook(mw)({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    const blocks = seen[0].content as any[];
    expect(blocks[0].source.data).toBe("Client [EMAIL_1], [PERSON_1]");
    expect(blocks[1].source.data).toBe("[EMAIL_1]"); // a data string that is a known value as a whole (G21)
    expect(blocks[2].json).toEqual({ name: "[EMAIL_1]", contact: "[EMAIL_1]" });
    expect(seen[1].content).toEqual([{ who: "[PERSON_1]" }, { who: "Lee Sora" }]);
    expect(seenTexts).toContain("who: Kim Minsu\nwho: Lee Sora"); // one reading for the rows
  });

  it("without a vault masks only string content and text blocks, as in 1.6", async () => {
    const mw = piiMiddleware({ redactor: kim });
    let seen: BaseMessage[] = [];
    await hook(mw)({ messages: [new HumanMessage({ content: ["Kim Minsu", { type: "custom", text: "Kim Minsu" }, { type: "text", text: "Kim Minsu" }] as any })] }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    expect(seen[0].content).toEqual(["Kim Minsu", { type: "custom", text: "Kim Minsu" }, { type: "text", text: "[PERSON]" }]);
  });
});

describe("piiMiddleware with a vault: names, request fields, structured responses and failures (1.7.1)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<unknown>) => Promise<any>;
  const known = () => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    vault.tag("EMAIL", "kim@example.com");
    return vault;
  };

  it("gives a known value in the name of a HumanMessage or ChatMessage as its placeholder without brackets", async () => {
    const mw = piiMiddleware({ redactor: kim, vault: known() });
    let seen: BaseMessage[] = [];
    const messages = [
      new HumanMessage({ content: "hi", name: "Kim Minsu" }),
      new ChatMessage({ content: "hello", role: "customer", name: "Kim Minsu (VIP)" }),
      new HumanMessage({ content: "hi", name: "support-bot" }),
      new ToolMessage({ content: "ok", tool_call_id: "c1", name: "Kim Minsu" }), // a tool's name: an identifier
    ];
    const answer = await hook(mw)({ messages }, async (r) => ((seen = r.messages), new AIMessage("Hello PERSON_1 and [PERSON_1]")));
    expect(seen.map((m) => m.name)).toEqual(["PERSON_1", "PERSON_1 (VIP)", "support-bot", "Kim Minsu"]);
    expect(ChatMessage.isInstance(seen[1])).toBe(true);
    expect(messages[0].name).toBe("Kim Minsu"); // the state keeps the original
    // the bare form is never restored, and a name puts no placeholder into the call: the model was never given
    // "[PERSON_1]", so it stays as the model wrote it (the 23rd outside review: it was restored to the name)
    expect(answer.content).toBe("Hello PERSON_1 and [PERSON_1]");
  });

  it("restores a placeholder that a name and the content share only because the content put it in", async () => {
    const answer = async (messages: BaseMessage[]) => {
      const mw = piiMiddleware({ redactor: kim, vault: known() });
      return (await hook(mw)({ messages }, async () => new AIMessage("[PERSON_1] and [EMAIL_1]"))).content;
    };
    // the name alone (HumanMessage, ChatMessage): nothing to restore
    expect(await answer([new HumanMessage({ content: "Hello.", name: "Kim Minsu" })])).toBe("[PERSON_1] and [EMAIL_1]");
    expect(await answer([new ChatMessage({ content: "Hello.", role: "user", name: "kim@example.com" })])).toBe("[PERSON_1] and [EMAIL_1]");
    // the content gives the model the placeholder: it comes back
    expect(await answer([new HumanMessage({ content: "I am Kim Minsu.", name: "Kim Minsu" })])).toBe("Kim Minsu and [EMAIL_1]");
  });

  it("hides known values in tool descriptions and schemas, the response format and model settings", async () => {
    const mw = piiMiddleware({ redactor: kim, vault: known() });
    const lookup = tool(async () => "ok", {
      name: "lookup",
      description: "Looks up Kim Minsu",
      schema: z.object({ email: z.string().describe("for example kim@example.com") }),
    });
    const other = tool(async () => "ok", { name: "other", description: "plain", schema: z.object({ q: z.string() }) });
    const request = {
      messages: [new HumanMessage("hi")],
      tools: [lookup, other, { type: "function", function: { name: "f", description: "about Kim Minsu", parameters: { type: "object", properties: {} } } }],
      responseFormat: { title: "Kim Minsu", type: "object", properties: { who: { type: "string", description: "kim@example.com" } } },
      modelSettings: { user: "kim@example.com", previous_response_id: "kim@example.com" },
    };
    let got: any;
    await hook(mw)(request, async (r) => ((got = r), new AIMessage("ok")));
    const [a, b, c] = got.tools;
    // a tool with a known value goes to the model as its definition (the agent refuses a changed copy of its tool)
    expect([a.type, a.function.name, a.function.description, a.function.parameters.properties.email.description]).toEqual([
      "function", "lookup", "Looks up [PERSON_1]", "for example [EMAIL_1]",
    ]);
    expect(b).toBe(other); // nothing to hide: the same tool
    expect(c.function).toEqual({ name: "f", description: "about [PERSON_1]", parameters: { type: "object", properties: {} } });
    expect(got.responseFormat).toEqual({ title: "Kim Minsu", type: "object", properties: { who: { type: "string", description: "[EMAIL_1]" } } }); // its title is its name
    expect(got.modelSettings).toEqual({ user: "[EMAIL_1]", previous_response_id: "kim@example.com" });
    expect(lookup.description).toBe("Looks up Kim Minsu"); // the agent's tools are not changed
  });

  it("runs a tool of createAgent whose description holds a known value, and the model gets the description hidden", async () => {
    const ran: unknown[] = [];
    const lookup = tool(async (input: { email: string }) => (ran.push(input), "found"), {
      name: "lookup",
      description: "Looks up Kim Minsu",
      schema: z.object({ email: z.string().describe("for example kim@example.com") }),
    });
    const bound: unknown[] = [];
    let turn = 0;
    class Model extends FakeListChatModel {
      bindTools(tools: unknown[]): any {
        bound.push(JSON.parse(JSON.stringify(tools)));
        return this;
      }
      async invoke(): Promise<any> {
        return turn++ === 0 ? new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: { email: "x@example.org" } }] }) : new AIMessage("done");
      }
    }
    const agent = createAgent({ model: new Model({ responses: ["x"] }), tools: [lookup], middleware: [piiMiddleware({ redactor: kim, vault: known() })] });
    const out = (await agent.invoke({ messages: [new HumanMessage("hi")] })) as any;
    expect(ran).toEqual([{ email: "x@example.org" }]); // the agent's own tool ran
    expect(out.messages.at(-1).content).toBe("done");
    expect(JSON.stringify(bound)).not.toMatch(/Kim Minsu|kim@example\.com/);
    expect(JSON.stringify(bound)).toContain("Looks up [PERSON_1]");
  });

  it("restores this call's placeholders in a structured response of createAgent (tool strategy and provider strategy)", async () => {
    for (const strategy of ["tool", "provider"] as const) {
      const vault = createVault();
      const seen: BaseMessage[][] = [];
      const schema = { title: "Contact", type: "object", properties: { name: { type: "string" } }, required: ["name"] };
      class Model extends FakeListChatModel {
        async _generate(messages: BaseMessage[], ...rest: [any, any?]) {
          seen.push(messages);
          return super._generate(messages, ...rest);
        }
        async invoke(messages: any): Promise<any> {
          seen.push(messages);
          return strategy === "tool"
            ? new AIMessage({ content: "", tool_calls: [{ id: "s1", name: "Contact", args: { name: "[PERSON_1]" } }] })
            : new AIMessage('{"name": "[PERSON_1]"}');
        }
        bindTools(): any {
          return this;
        }
      }
      const responseFormat = strategy === "tool" ? toolStrategy(schema as never) : providerStrategy(schema as never);
      const agent = createAgent({ model: new Model({ responses: ["x"] }), tools: [], responseFormat: responseFormat as never, middleware: [piiMiddleware({ redactor: kim, vault })] });
      const out = (await agent.invoke({ messages: [new HumanMessage("I am Kim Minsu")] })) as any;
      expect(JSON.stringify(seen)).not.toContain("Kim Minsu");
      expect(out.structuredResponse, strategy).toEqual({ name: "Kim Minsu" });
    }
  });

  it("restores keys of a structured response as well as values, and keeps a key that would collide (23rd outside review)", async () => {
    const answers = {
      // the reviewer's answer; a key that is already the value; a placeholder this call did not put in
      contacts: { "[PERSON_1]": "[PERSON_1]" },
      clash: { "[PERSON_1]": "a", "Kim Minsu": "b" },
      other: { "[PERSON_9]": "[PERSON_9]", list: [{ "[PERSON_1]": ["[PERSON_1]"] }] },
    };
    for (const strategy of ["tool", "provider"] as const) {
      const seen: unknown[] = [];
      const schema = { title: "Contacts", type: "object", properties: { contacts: { type: "object" }, clash: { type: "object" }, other: { type: "object" } } };
      class Model extends FakeListChatModel {
        async invoke(messages: any): Promise<any> {
          seen.push(messages);
          return strategy === "tool"
            ? new AIMessage({ content: "", tool_calls: [{ id: "s1", name: "Contacts", args: answers }] })
            : new AIMessage(JSON.stringify(answers));
        }
        bindTools(): any {
          return this;
        }
      }
      const responseFormat = strategy === "tool" ? toolStrategy(schema as never) : providerStrategy(schema as never);
      const agent = createAgent({ model: new Model({ responses: ["x"] }), tools: [], responseFormat: responseFormat as never, middleware: [piiMiddleware({ redactor: kim, vault: createVault() })] });
      const out = (await agent.invoke({ messages: [new HumanMessage("Call Kim Minsu.")] })) as any;
      expect(JSON.stringify(seen)).not.toContain("Kim Minsu");
      expect(out.structuredResponse, strategy).toEqual({
        contacts: { "Kim Minsu": "Kim Minsu" },
        clash: { "[PERSON_1]": "a", "Kim Minsu": "b" },
        other: { "[PERSON_9]": "[PERSON_9]", list: [{ "Kim Minsu": ["Kim Minsu"] }] },
      });
      if (strategy === "provider") expect(JSON.parse(out.messages.at(-1).content).contacts).toEqual({ "Kim Minsu": "Kim Minsu" });
    }
  });

  it("hides the application's metadata in model settings and messages whatever its fields are called (23rd outside review)", async () => {
    const jose = `Jos${String.fromCharCode(0xe9)} Garc${String.fromCharCode(0xed)}a`;
    const vault = known();
    vault.tag("PERSON", jose);
    const mw = piiMiddleware({ redactor: kim, vault });
    const request = {
      messages: [
        new HumanMessage({
          content: [{ type: "text", text: "hi", extras: { metadata: { name: jose } } }] as any,
          additional_kwargs: { metadata: { name: jose, id: "Kim Minsu" } },
        }),
      ],
      modelSettings: {
        metadata: { name: jose, id: "Kim Minsu", customer_id: "kim@example.com", "kim@example.com": "vip", type: "Kim Minsu" },
        modelKwargs: { metadata: { name: jose }, safety_identifier: "kim@example.com", prompt_cache_key: "Kim Minsu" },
        name: "Kim Minsu",
        // identifiers providers define stay as written
        previous_response_id: "Kim Minsu",
        conversation: { id: "Kim Minsu" },
        tool_choice: { type: "function", function: { name: "Kim Minsu" } },
        response_format: { type: "json_schema", json_schema: { name: "Kim Minsu", schema: { type: "object", properties: { a: { const: "Kim Minsu", description: "Kim Minsu" } } } } },
      },
    };
    let got: any;
    await hook(mw)(request, async (r) => ((got = r), new AIMessage("ok")));
    expect(got.modelSettings).toEqual({
      metadata: { name: "[PERSON_2]", id: "[PERSON_1]", customer_id: "[EMAIL_1]", "[EMAIL_1]": "vip", type: "[PERSON_1]" },
      modelKwargs: { metadata: { name: "[PERSON_2]" }, safety_identifier: "[EMAIL_1]", prompt_cache_key: "[PERSON_1]" },
      name: "[PERSON_1]",
      previous_response_id: "Kim Minsu",
      conversation: { id: "Kim Minsu" },
      tool_choice: { type: "function", function: { name: "Kim Minsu" } },
      response_format: { type: "json_schema", json_schema: { name: "Kim Minsu", schema: { type: "object", properties: { a: { const: "Kim Minsu", description: "[PERSON_1]" } } } } },
    });
    expect(got.messages[0].additional_kwargs).toEqual({ metadata: { name: "[PERSON_2]", id: "[PERSON_1]" } });
    expect(got.messages[0].content[0].extras).toEqual({ metadata: { name: "[PERSON_2]" } });
  });

  it("keeps literal constraints of request schemas and hides descriptions under every keyword that holds schemas (23rd outside review)", async () => {
    const mw = piiMiddleware({ redactor: kim, vault: known() });
    const literal = { const: { description: "Kim Minsu" }, enum: ["Kim Minsu", { title: "kim@example.com" }], default: { examples: ["Kim Minsu"] }, pattern: "^Kim Minsu$" };
    const d = { type: "string", description: "for Kim Minsu" };
    const parameters = { type: "object", properties: { who: { ...literal, description: "Kim Minsu" }, any: { anyOf: [d], items: d, not: d, if: d }, map: { additionalProperties: d } }, $defs: { x: d } };
    const request = {
      messages: [new HumanMessage("hi")],
      tools: [{ type: "function", function: { name: "f", description: "about Kim Minsu", parameters } }],
      responseFormat: { title: "Answer", type: "object", properties: { who: { const: { description: "Kim Minsu" } } } },
    };
    let got: any;
    await hook(mw)(request, async (r) => ((got = r), new AIMessage("ok")));
    const p = got.tools[0].function.parameters;
    expect(p.properties.who).toEqual({ ...literal, description: "[PERSON_1]" });
    expect(JSON.stringify([p.properties.any, p.properties.map, p.$defs])).not.toContain("Kim Minsu");
    expect(got.responseFormat.properties.who).toEqual({ const: { description: "Kim Minsu" } });
  });

  describe("a call whose masking cannot finish fails, and nothing of it is sent", () => {
    const attempt = async (mw: ReturnType<typeof piiMiddleware>, messages: BaseMessage[]) => {
      let sentToProvider = false;
      const error = await hook(mw)({ messages }, async () => ((sentToProvider = true), new AIMessage("ok"))).then(
        () => undefined,
        (e: unknown) => e ?? "failed",
      );
      return { error, sentToProvider };
    };

    it("when the detector throws", async () => {
      const failing = { detect: async () => Promise.reject(new Error("detector down")) } as unknown as Redactor;
      const got = await attempt(piiMiddleware({ redactor: failing, vault: known() }), [new HumanMessage("I am Kim Minsu")]);
      expect(String(got.error)).toMatch(/detector down/);
      expect(got.sentToProvider).toBe(false);
    });

    it("when the vault is cleared while the call is masked", async () => {
      const vault = known();
      const clearing = { detect: async () => (vault.clear(), []) } as unknown as Redactor;
      const got = await attempt(piiMiddleware({ redactor: clearing, vault }), [new HumanMessage("I am Kim Minsu")]);
      expect(got.error).toBeDefined();
      expect(got.sentToProvider).toBe(false);
    });

    it("when the input cannot be read (rows that refer to themselves)", async () => {
      const loop: Record<string, unknown> = { name: "Kim Minsu" };
      loop.self = loop;
      const messages = [new HumanMessage("hi"), new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "t", args: {} }] }), new ToolMessage({ content: [loop] as any, tool_call_id: "c1" })];
      const got = await attempt(piiMiddleware({ redactor: kim, vault: known() }), messages);
      expect(got.error).toBeDefined();
      expect(got.sentToProvider).toBe(false);
    });

    it("when the data is nested deeper than the walk can go: it fails, or it is masked", async () => {
      let deep: unknown = "Kim Minsu";
      for (let i = 0; i < 20000; i++) deep = { d: deep };
      const messages = [new HumanMessage("hi"), new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "t", args: {} }] }), new ToolMessage({ content: [{ type: "json", json: deep }] as any, tool_call_id: "c1" })];
      let seen: BaseMessage[] | undefined;
      const error = await hook(piiMiddleware({ redactor: kim, vault: known() }))({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok"))).then(
        () => undefined,
        (e: unknown) => e ?? "failed",
      );
      if (error === undefined) {
        let x: any = (seen![2].content as any)[0].json;
        while (x && typeof x === "object") x = x.d;
        expect(x).toBe("[PERSON_1]");
      } else expect(seen).toBeUndefined();
    });
  });
});

// SDK 1.7.1, the 23rd outside review, A2: which objects are LangChain's is chosen from where they stand ---------------
describe("piiMiddleware with a vault: tool data that has the types of LangChain's blocks (23rd outside review)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: { messages: BaseMessage[] }) => Promise<AIMessage>) => Promise<AIMessage>;
  const known = () => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    vault.tag("EMAIL", "kim@example.com");
    return vault;
  };
  const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const row = () => ({ type: "text", name: "Kim Minsu", "Kim Minsu": "open" });
  const masked = { type: "text", name: "[PERSON_1]", "[PERSON_1]": "open" };
  const send = async (content: unknown[], redactor: Redactor = none) => {
    let seen: BaseMessage[] = [];
    const messages = [new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: {} }] }), new ToolMessage({ content: content as any, tool_call_id: "c1" })];
    await hook(piiMiddleware({ redactor, vault: known() }))({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    return seen[1].content as any[];
  };

  it("masks the reviewer's tool result in a real agent: a json block whose data has a type of its own", async () => {
    const seen: BaseMessage[][] = [];
    let turn = 0;
    class Model extends FakeListChatModel {
      bindTools(): any {
        return this;
      }
      async invoke(messages: any): Promise<any> {
        seen.push(messages);
        return turn++ === 0 ? new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: { q: "x" } }] }) : new AIMessage("done");
      }
    }
    const lookup = tool(async () => [{ type: "json", data: row() }] as any, { name: "lookup", description: "Looks up a customer", schema: z.object({ q: z.string() }) });
    const agent = createAgent({ model: new Model({ responses: ["x"] }), tools: [lookup], middleware: [piiMiddleware({ redactor: none, vault: known() })] });
    const out = (await agent.invoke({ messages: [new HumanMessage("hi")] })) as any;
    expect(out.messages.at(-1).content).toBe("done");
    const result = seen[1].find((m) => ToolMessage.isInstance(m))!;
    expect(result.content).toEqual([{ type: "json", data: masked }]);
    expect(JSON.stringify(seen)).not.toContain("Kim Minsu");
    // the agent's state keeps what the tool returned
    expect(JSON.stringify(out.messages)).toContain("Kim Minsu");
  });

  it("walks a payload as data under any block type and field, at any depth", async () => {
    const got = await send([
      ...["file", "image", "audio", "video", "document", "text-plain", "search_result", "non_standard", "input_file"].map((type) => ({ type, data: row() })),
      { type: "json", content: row() },
      { type: "json", output: [row()] },
      { type: "json", source: row() },
      { type: "server_tool_result", tool_call_id: "c9", output: row() },
      { type: "json", data: { type: "json", data: { rows: [{ type: "file", data: row() }] } } },
      { type: "json", data: { type: "tool_call", name: "Kim Minsu", id: "kim@example.com", args: {} } },
      { type: "json", data: { type: "function", function: { name: "Kim Minsu", arguments: "{}" } } },
      { type: "json", data: { type: "image_url", image_url: { url: "https://example.com/kim@example.com" } } },
    ]);
    expect(got.slice(0, 9).map((b) => b.data)).toEqual(Array(9).fill(masked));
    expect(got[9].content).toEqual(masked);
    expect(got[10].output).toEqual([masked]);
    expect(got[11].source).toEqual(masked);
    expect(got[12].output).toEqual(masked);
    expect(got[13].data.data.rows[0].data).toEqual(masked);
    expect(got[14].data).toEqual({ type: "tool_call", name: "[PERSON_1]", id: "[EMAIL_1]", args: {} });
    expect(got[15].data.function.name).toBe("[PERSON_1]");
    expect(got[16].data.image_url.url).toBe("https://example.com/[EMAIL_1]");
    expect(JSON.stringify(got)).not.toMatch(/Kim Minsu|kim@example\.com/);
  });

  it("hides data in blocks of a framework type: a name a text block does not have, a key that is a known value", async () => {
    const got = await send([
      { type: "text", text: "Results:" },
      row(),
      { type: "image", url: "https://example.com/a.png", name: "Kim Minsu", "kim@example.com": 1 },
      { type: "tool_result", tool_use_id: "c1", content: [row(), { type: "text", text: "Kim Minsu" }] },
    ]);
    expect(got).toEqual([
      { type: "text", text: "Results:" },
      masked,
      { type: "image", url: "https://example.com/a.png", name: "[PERSON_1]", "[EMAIL_1]": 1 },
      { type: "tool_result", tool_use_id: "c1", content: [masked, { type: "text", text: "[PERSON_1]" }] },
    ]);
  });

  it("leaves the names and identifiers of LangChain's own objects as written", async () => {
    let seen: BaseMessage[] = [];
    const messages = [
      new AIMessage({
        content: [{ type: "tool_use", id: "toolu_1", name: "lookup", input: { q: "x" } }, { type: "function_call", call_id: "c2", name: "lookup", arguments: "{}" }] as any,
        tool_calls: [{ id: "toolu_1", name: "lookup", args: { q: "x" } }],
        additional_kwargs: { tool_calls: [{ id: "c3", type: "function", function: { name: "lookup", arguments: '{"who":"Kim Minsu"}' } }] },
      }),
      new ToolMessage({ content: [{ type: "tool_result", tool_use_id: "toolu_1", content: [{ type: "image", source: { type: "url", url: "https://example.com/kim@example.com.png" } }] }] as any, tool_call_id: "toolu_1" }),
    ];
    const vault = known();
    vault.tag("USERNAME", "lookup"); // (a known value that is also a tool's name)
    await hook(piiMiddleware({ redactor: none, vault }))({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    const ai = seen[0] as AIMessage;
    expect((ai.content as any[]).map((b) => b.name)).toEqual(["lookup", "lookup"]);
    expect(ai.tool_calls![0].name).toBe("lookup");
    expect(ai.additional_kwargs.tool_calls![0]).toEqual({ id: "c3", type: "function", function: { name: "lookup", arguments: '{"who":"[PERSON_1]"}' } });
    expect((seen[1].content as any[])[0].content[0].source.url).toBe("https://example.com/kim@example.com.png"); // (a file reference)
  });

  it("sends a Date or a class instance in a payload as the JSON it gives, with a known date hidden (A3 in JavaScript)", async () => {
    let seen: BaseMessage[] = [];
    const vault = known();
    vault.tag("DATE_OF_BIRTH", "1990-01-02");
    class Contact {
      constructor(public dob: Date) {}
      get display() {
        return "Kim Minsu"; // (a getter is not written by JSON.stringify, so a provider never sends it)
      }
    }
    const content = [{ type: "json", data: { type: "text", dob: new Date("1990-01-02T00:00:00Z"), contact: new Contact(new Date("1990-01-02T09:30:00Z")) } }, { type: "text", text: "ok", at: new Date("1990-01-02T00:00:00Z") }];
    await hook(piiMiddleware({ redactor: none, vault }))({ messages: [new ToolMessage({ content: content as any, tool_call_id: "c1" })] }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    const got = seen[0].content as any[];
    expect(got[0].data).toEqual({ type: "text", dob: "[DATE_OF_BIRTH_1]T00:00:00.000Z", contact: { dob: "[DATE_OF_BIRTH_1]T09:30:00.000Z" } });
    expect(got[1]).toEqual({ type: "text", text: "ok", at: "[DATE_OF_BIRTH_1]T00:00:00.000Z" });
    expect(JSON.stringify(seen.map((m) => m.content))).not.toContain("1990-01-02");
  });

  it("gives the model a json block's payload under content or output to read as data", async () => {
    const texts: string[] = [];
    const reads = { sensitivity: 0, detect: async (t: string) => (texts.push(t), []) } as unknown as Redactor;
    await send([{ type: "json", content: { type: "text", name: "Lee Jiwon" } }, { type: "server_tool_result", output: [{ type: "text", owner: "Park Sora" }] }], reads);
    expect(texts.some((t) => t.includes("name: Lee Jiwon"))).toBe(true);
    expect(texts.some((t) => t.includes("owner: Park Sora"))).toBe(true);
  });

  it("walks typed payloads at any depth (their time is measured in vault-growth.test.ts, which runs alone)", async () => {
    const deep = (n: number) => {
      let x: unknown = row();
      for (let i = 0; i < n; i++) x = { type: i % 2 ? "tool_result" : "json", [i % 2 ? "content" : "data"]: [x] };
      return [x];
    };
    expect(JSON.stringify(await send(deep(50)))).not.toContain("Kim Minsu");
  });
});

// SDK 1.7.1, the 24th outside review: a message name in the spelling providers accept there, and model settings that
// are data except a provider's identifiers ---------------------------------------------------------------------------
describe("piiMiddleware with a vault: names and model settings (24th outside review)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<unknown>) => Promise<any>;
  const known = (username = true) => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    vault.tag("EMAIL", "kim@example.com");
    vault.tag("PHONE", "010-1234-5678");
    if (username) vault.tag("USERNAME", "kim_minsu");
    vault.tag("PERSON", "Lee Jiwoo");
    vault.tag("ID_NUMBER", "821023456789");
    return vault;
  };
  const names = async (given: string[], username = true) => {
    const mw = piiMiddleware({ redactor: kim, vault: known(username) });
    let seen: BaseMessage[] = [];
    const messages = given.flatMap((name) => [new HumanMessage({ content: "Hello.", name }), new ChatMessage({ content: "Hi", role: "user", name })]);
    await hook(mw)({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    return seen.map((m) => m.name);
  };

  it("reads a value of two words written with '_', '-' or a capital in a name, as providers accept a name", async () => {
    const given = ["Kim_Minsu", "Kim-Minsu", "kim-minsu", "KIM_MINSU", "KimMinsu", "Kim__Minsu", "Dear_Kim_Minsu-VIP", "Lee_Jiwoo_and_Kim_Minsu"];
    const want = ["PERSON_1", "PERSON_1", "PERSON_1", "PERSON_1", "PERSON_1", "PERSON_1", "Dear_PERSON_1-VIP", "PERSON_2_and_PERSON_1"];
    expect(await names(given, false)).toEqual(want.flatMap((n) => [n, n]));
  });

  it("finds a value as the name writes it first, and keeps look-alikes and other names", async () => {
    const given = ["kim_minsu", "call-010-1234-5678", "Kim_Minsuk", "KimMinsukova", "Minsu", "support-bot", "kim@example.com"];
    const want = ["USERNAME_1", "call-PHONE_1", "Kim_Minsuk", "KimMinsukova", "Minsu", "support-bot", "EMAIL_1"];
    expect(await names(given)).toEqual(want.flatMap((n) => [n, n]));
  });

  it("hides template variables, numbers and fields named like identifiers that a provider does not define there", async () => {
    const mw = piiMiddleware({ redactor: kim, vault: known() });
    const request = {
      messages: [new HumanMessage("hi")],
      modelSettings: {
        prompt: { id: "pmpt_1", version: "2", variables: { order: "Kim Minsu", customer: "Kim Minsu" } },
        metadata: { customer_number: 821023456789, phone: "010-1234-5678" },
        extra_body: { order: "kim@example.com", provider: { order: ["Kim Minsu"] } },
        order: "Kim Minsu",
        model: "Kim Minsu",
      },
    };
    let got: any;
    await hook(mw)(request, async (r) => ((got = r), new AIMessage("ok")));
    // (extra_body is data, every key and value of it: LangChain.js sends it as a field of its own, which no provider
    // reads as routing; release gate 8)
    expect(got.modelSettings).toEqual({
      prompt: { id: "pmpt_1", version: "2", variables: { order: "[PERSON_1]", customer: "[PERSON_1]" } },
      metadata: { customer_number: "[ID_NUMBER_1]", phone: "[PHONE_1]" },
      extra_body: { order: "[EMAIL_1]", provider: { order: ["[PERSON_1]"] } },
      order: "[PERSON_1]",
      model: "Kim Minsu",
    });
  });
});

// SDK 1.7.1, the review group "options": a name read through its joins only for values of two or more words (L1-A2),
// the response format hidden where the agent binds it, on every LangChain.js version that gives middleware a request
// (L7-1, A5), and a schema with typed fields (L3-B5) ------------------------------------------------------------------
describe("piiMiddleware with a vault: names and the final binding of a response format (review group options)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<unknown>) => Promise<any>;
  const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;

  it("reads joins in a name only for values of two or more words: one-word values, exponents and look-alikes stay", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Ann");
    vault.tag("PERSON", "Minsu");
    vault.tag("ID_NUMBER", "4524");
    vault.tag("PERSON", "Kim Minsu");
    const given = ["xAnn", "Minsu_VIP", "1e-4524", "Kim_Minsuk", "Ann", "call-4524", "Dear_Kim-Minsu", "Dear_Kim_Minsu"];
    let seen: BaseMessage[] = [];
    await hook(piiMiddleware({ redactor: none, vault }))({ messages: given.map((name) => new HumanMessage({ content: "Hello.", name })) }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    // (a value the name holds as written comes first: "Minsu" after "-" is at a word edge, so "Kim-Minsu" is not read)
    expect(seen.map((m) => m.name)).toEqual(["xAnn", "Minsu_VIP", "1e-4524", "Kim_Minsuk", "PERSON_1", "call-ID_NUMBER_1", "Dear_Kim-PERSON_2", "Dear_PERSON_3"]);
  });

  const typed = z.object({ name: z.string(), born: z.string().date(), mail: z.string().email() });
  const run = async (strategy: "tool" | "provider") => {
    const bound: { tools: string; options: string }[] = [];
    const seen: string[] = [];
    let calls = 0;
    class Model extends FakeListChatModel {
      bindTools(tools: unknown[], options?: Record<string, unknown>): any {
        bound.push({ tools: JSON.stringify(tools), options: JSON.stringify(options ?? {}) });
        return this;
      }
      async invoke(messages: any): Promise<any> {
        calls++;
        seen.push(JSON.stringify(messages));
        const answer = { name: "[PERSON_1]", born: "[DATE_OF_BIRTH_1]", mail: "[EMAIL_1]" };
        const name = JSON.parse(bound.at(-1)!.tools)[0]?.function?.name;
        return strategy === "provider" ? new AIMessage(JSON.stringify(answer)) : new AIMessage({ content: "", tool_calls: [{ id: "s1", name, args: answer }] });
      }
    }
    const vault = createVault();
    for (const [label, value] of [["PERSON", "Kim Minsu"], ["DATE_OF_BIRTH", "1990-01-02"], ["EMAIL", "kim.minsu@example.com"]]) vault.tag(label, value);
    const responseFormat = strategy === "tool" ? toolStrategy(typed.describe("A card for Kim Minsu")) : providerStrategy(typed.describe("A card for Kim Minsu"));
    const agent = createAgent({ model: new Model({ responses: ["x"] }), tools: [], responseFormat: responseFormat as never, middleware: [piiMiddleware({ redactor: none, vault })] });
    const out = (await agent.invoke({ messages: [new HumanMessage("Kim Minsu, born 1990-01-02, kim.minsu@example.com. The card, please.")] }, { recursionLimit: 6 })) as any;
    return { out, bound, seen, calls };
  };

  for (const strategy of ["tool", "provider"] as const) {
    it(`gives the agent a structured response with typed fields, checked against its own schema (${strategy} strategy, L3-B5)`, async () => {
      const { out, bound, seen, calls } = await run(strategy);
      expect(calls).toBe(1); // no retry that asks the model to write values in place of the placeholders
      expect(out.structuredResponse).toEqual({ name: "Kim Minsu", born: "1990-01-02", mail: "kim.minsu@example.com" });
      const sent = JSON.stringify([bound, seen]);
      expect(sent).not.toMatch(/Kim Minsu|1990-01-02|kim\.minsu@example\.com/);
      expect(sent).toContain("A card for [PERSON_1]");
      // the schema the model is given has no constraint a placeholder cannot meet (what LangChain keeps for tracing,
      // ls_structured_output_format, is not sent to the provider)
      const { ls_structured_output_format: _traced, ...options } = JSON.parse(bound.at(-1)!.options);
      expect(bound.at(-1)!.tools + JSON.stringify(options)).not.toMatch(/"format":"|"pattern"/);
    });
  }

  it("binds the model as it is when the request says it has no response format, and binds a copy otherwise", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    const model = new FakeListChatModel({ responses: ["x"] });
    const models: unknown[] = [];
    const mw = piiMiddleware({ redactor: none, vault });
    await hook(mw)({ messages: [new HumanMessage("hi")], model, responseFormat: undefined }, async (r) => (models.push(r.model), new AIMessage("ok")));
    await hook(mw)({ messages: [new HumanMessage("hi")], model }, async (r) => (models.push(r.model), new AIMessage("ok")));
    // (with a vault that holds values the model is given as a copy for the call, which holds its own request fields as
    // they were read once: release gate 8 on f25d519, item 2. Without a response format that copy is what is bound; with
    // one, a copy of it that hides the response format where the agent binds it)
    expect(Object.getPrototypeOf(models[0])).toBe(model);
    expect(Object.getPrototypeOf(models[1])).not.toBe(model);
    expect(Object.getPrototypeOf(Object.getPrototypeOf(models[1]))).toBe(model);
    expect(models[1] instanceof FakeListChatModel).toBe(true);
  });

  it("fails before sending when the response format holds a known value and the model cannot be copied", async () => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    let sent = false;
    const request = { messages: [new HumanMessage("hi")], model: { invoke: async () => new AIMessage("x") }, responseFormat: { title: "Card", type: "object", description: "for Kim Minsu", properties: {} } };
    await expect(hook(piiMiddleware({ redactor: none, vault }))(request, async () => ((sent = true), new AIMessage("ok")))).rejects.toThrow(/cannot be copied/);
    expect(sent).toBe(false);
  });
});

describe("piiMiddleware with a vault: shapes of the review of 1.7.1 and of the 24th outside review", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: { messages: BaseMessage[] }) => Promise<AIMessage>) => Promise<AIMessage>;
  const NAME = "Maria Lopez";
  const EMAIL = "maria.lopez@example.com";
  const owner = () => ({ type: "user", id: "9", name: NAME, login: EMAIL });
  const box = () => [
    { type: "file", id: "1024", name: `${NAME} CV.pdf`, owned_by: owner() },
    { type: "file", id: "1025", name: `${EMAIL} - offer.docx`, owned_by: owner() },
  ];
  /** A detector that finds the name and the e-mail address, and the texts it was given. */
  const finds = (texts: string[] = []) =>
    ({
      sensitivity: 0,
      async detect(t: string) {
        texts.push(t);
        const out = [];
        for (const [v, label] of [[NAME, "PERSON"], [EMAIL, "EMAIL"]] as const)
          for (let i = t.indexOf(v); i >= 0; i = t.indexOf(v, i + 1)) out.push({ start: i, end: i + v.length, label, source: "model" as const });
        return out.sort((a, b) => a.start - b.start);
      },
    }) as unknown as Redactor;
  const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const knownVault = () => {
    const v = createVault();
    v.tag("PERSON", NAME);
    v.tag("EMAIL", EMAIL);
    return v;
  };
  /** What the model gets for a tool result with `content`, after the call that asked for it. */
  const send = async (content: unknown, redactor: Redactor, vault = createVault()) => {
    let seen: BaseMessage[] = [];
    const messages = [new HumanMessage("Find the files."), new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "search", args: {} }] }), new ToolMessage({ content: content as any, tool_call_id: "c1" })];
    await hook(piiMiddleware({ redactor, vault }))({ messages }, async (r) => ((seen = r.messages), new AIMessage("ok")));
    return seen[2].content as any;
  };
  /** A recording model for createAgent that answers from a script. */
  const agentModel = (seen: BaseMessage[][], script: ((m: BaseMessage[]) => AIMessage)[]) => {
    let step = 0;
    class Model extends FakeListChatModel {
      bindTools(): any {
        return this;
      }
      async invoke(messages: any): Promise<any> {
        seen.push(messages);
        return script[Math.min(step++, script.length - 1)](messages);
      }
    }
    return new Model({ responses: ["x"] });
  };

  it("reads rows a tool returns typed like blocks without a block's content as data, in a real agent (L3-A1)", async () => {
    for (const user of ["Find the CV files.", `Find the files of ${NAME} (${EMAIL}).`]) {
      const seen: BaseMessage[][] = [];
      const texts: string[] = [];
      const search = tool(async () => box() as any, { name: "search", description: "Searches files", schema: z.object({}) });
      const model = agentModel(seen, [() => new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "search", args: {} }] }), () => new AIMessage("done")]);
      const agent = createAgent({ model, tools: [search], middleware: [piiMiddleware({ redactor: finds(texts), vault: createVault() })] });
      await agent.invoke({ messages: [new HumanMessage(user)] });
      const result = seen[1].find((m) => ToolMessage.isInstance(m))!;
      // the model read the rows (the user did not name the person in the first run), every field and key counted
      expect(texts.some((t) => t.includes(`name: ${NAME} CV.pdf`) && t.includes(`login: ${EMAIL}`)), user).toBe(true);
      expect(JSON.stringify(seen), user).not.toMatch(/Maria Lopez|maria\.lopez@example\.com/);
      expect((result.content as any[])[0].owned_by, user).toEqual({ type: "user", id: "9", name: "[PERSON_1]", login: "[EMAIL_1]" });
    }
  });

  it("hides known values in every field of such rows, of every type a row can share with a block, at any place", async () => {
    const types = ["text", "image", "image_url", "file", "audio", "video", "text-plain", "document", "json", "search_result", "custom_tool_call_output", "message", "url", "content", "base64", "media", "function", "function_call", "tool_use", "tool_call", "thinking", "reasoning", "redacted_thinking", "input_file", "non_standard"];
    const row = (type: string) => ({ type, id: EMAIL, name: NAME, link: `https://crm.example.com/${EMAIL}`, [EMAIL]: "owner" });
    const rows = types.map(row);
    for (const content of [rows, [{ type: "text", text: "Results:" }, ...rows], [{ type: "tool_result", tool_use_id: "c1", content: rows }]]) {
      const got = await send(content, none, knownVault());
      expect(JSON.stringify(got)).not.toMatch(/Maria Lopez|maria\.lopez@example\.com/);
      expect(JSON.stringify(got)).toContain("owner");
    }
    // a row with a url is a file block of its type: its url is a reference to a file and stays as written (decided), and
    // its id and name are hidden (no block in what came from outside is matched by its id)
    const linked = await send([{ type: "file", id: EMAIL, name: NAME, url: `https://crm.example.com/${EMAIL}` }], none, knownVault());
    expect(linked).toEqual([{ type: "file", id: "[EMAIL_1]", name: "[PERSON_1]", url: `https://crm.example.com/${EMAIL}` }]);
    // ... and the model reads each of them
    for (const type of types) {
      const texts: string[] = [];
      const got = await send([row(type)], finds(texts));
      expect(JSON.stringify(got), type).not.toMatch(/Maria Lopez|maria\.lopez@example\.com/);
      expect(texts.some((t) => t.includes(`name: ${NAME}`)), type).toBe(true);
    }
  });

  it("keeps blocks with their own content as blocks: not read by the model, their ids and file references as written", async () => {
    const texts: string[] = [];
    const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk" + "A".repeat(24);
    const blocks = [
      { type: "text", text: "See the files.", id: "blk_text" },
      { type: "image", url: `https://files.example.com/${EMAIL}.png`, mime_type: "image/png", id: "blk_1" },
      { type: "file", base64: png, mime_type: "image/png" },
      { type: "file", source_type: "id", id: "file-abc" },
      { type: "document", source: { type: "url", url: `https://files.example.com/${EMAIL}.pdf` }, title: "Report" },
      { type: "image_url", image_url: { url: "https://files.example.com/q3.png" } },
      { type: "json", json: { owner: NAME } },
      { type: "tool_result", tool_use_id: "toolu_1", content: [{ type: "text", text: `Owner: ${NAME}` }] },
    ];
    const got = await send(blocks, finds(texts), knownVault());
    expect(got[0]).toEqual({ type: "text", text: "See the files.", id: "blk_text" });
    // a known value inside a file's URL is sent as written (decided: references to files)
    expect(got[1]).toEqual(blocks[1]);
    expect(got[2]).toEqual(blocks[2]);
    expect(got[3]).toEqual(blocks[3]);
    expect(got[4]).toEqual(blocks[4]);
    expect(got[5]).toEqual(blocks[5]);
    expect(got[6]).toEqual({ type: "json", json: { owner: "[PERSON_1]" } }); // (hidden by the check before sending)
    expect(got[7].content).toEqual([{ type: "text", text: "Owner: [PERSON_1]" }]);
    expect(texts.some((t) => t.includes("blk_1") || t.includes(png) || t.includes("Report"))).toBe(false);
  });

  it("reads a class instance or toJSON object a tool returns as a block as the JSON it gives, in a real agent (24th review, A2)", async () => {
    class JsonBlock {
      constructor(
        public type: string,
        public json: unknown,
      ) {}
    }
    class Customer {
      constructor(
        public name: string,
        public email: string,
      ) {}
    }
    class Written {
      get kind() {
        return "json";
      }
      toJSON() {
        return { type: this.kind, json: { name: "Kim Minsu" } };
      }
    }
    const shapes: [string, () => unknown][] = [
      ["a class instance typed json", () => [new JsonBlock("json", { name: "Kim Minsu" })]],
      ["a toJSON object with getters", () => [new Written()]],
      ["a class instance with a class payload", () => [new JsonBlock("json", new Customer("Kim Minsu", "kim@example.com"))]],
      ["a class instance inside a tool_result block", () => [{ type: "tool_result", tool_use_id: "x", content: [new JsonBlock("json", { name: "Kim Minsu" })] }]],
      ["a class instance typed text", () => [new JsonBlock("text", "x"), Object.assign(new JsonBlock("text", null), { text: "Call Kim Minsu" })]],
    ];
    for (const [name, make] of shapes) {
      const vault = createVault();
      vault.tag("PERSON", "Kim Minsu");
      vault.tag("EMAIL", "kim@example.com");
      const seen: BaseMessage[][] = [];
      const lookup = tool(async () => make() as any, { name: "lookup", description: "Looks up", schema: z.object({}) });
      const model = agentModel(seen, [() => new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: {} }] }), () => new AIMessage("done")]);
      const agent = createAgent({ model, tools: [lookup], middleware: [piiMiddleware({ redactor: none, vault })] });
      const out = (await agent.invoke({ messages: [new HumanMessage("Look it up.")] })) as any;
      expect(JSON.stringify(seen[1]), name).not.toMatch(/Kim Minsu|kim@example\.com/);
      expect(JSON.stringify(seen[1]), name).toContain("[PERSON_1]");
      expect(JSON.stringify(out.messages), name).toContain("Kim Minsu"); // the agent's state keeps what the tool returned
    }
    // nothing to hide: the plain JSON it gives is sent (release gate 4: never the instance, which the provider would
    // write again)
    const plain = new JsonBlock("json", { name: "Lee Sora" });
    const got = (await send([plain], none, knownVault()))[0];
    expect(Object.getPrototypeOf(got)).toBe(Object.prototype);
    expect(got).toEqual({ type: "json", json: { name: "Lee Sora" } });
  });

  it("reads the block fields a class gives by getters, which converters read by name and JSON leaves out (24th review, A2)", async () => {
    class ProtoText {
      type = "text";
      constructor(private readonly t: string) {}
      get text() {
        return this.t;
      }
    }
    class ProtoJson {
      constructor(private readonly j: unknown) {}
      get type() {
        return "json";
      }
      get json() {
        return this.j;
      }
    }
    class Hidden {
      type = "text";
      constructor(t: string) {
        Object.defineProperty(this, "text", { value: t, enumerable: false });
      }
    }
    const vault = () => {
      const v = createVault();
      v.tag("PERSON", "Kim Minsu");
      return v;
    };
    const read = (b: any) => ({ type: b.type, text: b.text, json: b.json }); // what a converter takes from a block
    for (const [name, make] of [
      ["a getter for text", () => new ProtoText("Call Kim Minsu")],
      ["getters for type and json", () => new ProtoJson({ name: "Kim Minsu" })],
      ["text that is not enumerable", () => new Hidden("Call Kim Minsu")],
    ] as const) {
      const got = (await send([make()], none, vault()))[0];
      expect(JSON.stringify(read(got)), name).not.toContain("Kim Minsu");
      expect(JSON.stringify(read(got)), name).toContain("[PERSON_1]");
      // ... and the model reads a text block's text (a fresh vault, a detector that finds the name; a json block's
      // payload only gets known values hidden, as in a plain json block)
      if (read(got).text === undefined) continue;
      const fresh = (await send([make()], kim, createVault()))[0];
      expect(JSON.stringify(read(fresh)), `${name}, read`).toBe(JSON.stringify({ type: "text", text: "Call [PERSON_1]" }));
    }
  });

  it("restores an answer to the placeholders of a recorded answer, tool call and reasoning passed back restored (L4-R1)", async () => {
    const vault = createVault();
    const first = await hook(piiMiddleware({ redactor: kim, vault, restoreTools: ["send"], restoreReasoning: true }))({ messages: [new HumanMessage("Hi, I am Kim Minsu.")] }, async () =>
      new AIMessage({
        content: [{ type: "reasoning", reasoning: "The user is [PERSON_1]." }, { type: "text", text: "Nice to meet you, [PERSON_1]." }],
        tool_calls: [{ id: "c1", name: "send", args: { to: "[PERSON_1]" } }],
      }),
    );
    expect((first.content as any[]).map((b) => b.reasoning ?? b.text)).toEqual(["The user is Kim Minsu.", "Nice to meet you, Kim Minsu."]);
    expect(first.tool_calls![0].args).toEqual({ to: "Kim Minsu" });
    const text = new AIMessage((first.content as any[])[1].text);
    const call = new AIMessage({ content: [{ type: "reasoning", reasoning: "The user is Kim Minsu." }], tool_calls: first.tool_calls });
    const windows: [string, BaseMessage[]][] = [
      ["the answer", [text, new HumanMessage("Write me a short greeting.")]],
      ["the reasoning and tool call", [call, new ToolMessage({ content: "sent", tool_call_id: "c1" })]],
    ];
    for (const [what, window] of windows) {
      for (const [name, v] of [["the same vault", vault], ["a toJSON reload", createVault(JSON.parse(JSON.stringify(vault)))], ["an entries() reload", createVault(vault.entries())]] as const) {
        let seen: BaseMessage[] = [];
        const answer = await hook(piiMiddleware({ redactor: kim, vault: v, restoreTools: ["send"], restoreReasoning: true }))({ messages: window }, async (r) => ((seen = r.messages), new AIMessage("Hello [PERSON_1]!")));
        expect(JSON.stringify(seen), `${what}, ${name}`).not.toContain("Kim Minsu");
        expect(answer.content, `${what}, ${name}`).toBe("Hello Kim Minsu!");
      }
    }
    // in a real agent: the restored answer and a new question (a window of two messages)
    for (const reload of [false, true]) {
      const seen: BaseMessage[][] = [];
      const v = createVault();
      const run = (messages: BaseMessage[], reply: string, w = v) =>
        createAgent({ model: agentModel(seen, [() => new AIMessage(reply)]), tools: [], middleware: [piiMiddleware({ redactor: kim, vault: w })] }).invoke({ messages }) as Promise<any>;
      const one = await run([new HumanMessage("Hi, I am Kim Minsu.")], "Nice to meet you, [PERSON_1].");
      const answer = one.messages.at(-1);
      expect(answer.content).toBe("Nice to meet you, Kim Minsu.");
      const two = await run([answer, new HumanMessage("Write me a short greeting.")], "Hello [PERSON_1]!", reload ? createVault(JSON.parse(JSON.stringify(v))) : v);
      expect(seen.at(-1)!.map((m) => m.content)).toEqual(["Nice to meet you, [PERSON_1].", "Write me a short greeting."]);
      expect(two.messages.at(-1).content, `reload ${reload}`).toBe("Hello Kim Minsu!");
    }
  });

  it("keeps every field of a model's JSON answer whose keys become one placeholder, recorded or not (L4-K2)", async () => {
    const known = () => {
      const v = createVault();
      v.tag("PERSON", "Kim Minsu");
      v.tag("EMAIL", "kim@example.com");
      v.tag("ID_NUMBER", "4524");
      return v;
    };
    const answers = [
      ['{"Kim Minsu": 1, "KIM MINSU": 2}', '{"[PERSON_1]": 1, "[PERSON_1] #2": 2}'],
      ['{"kim@example.com": {"vip": true}, "Kim@Example.com": {"vip": false}}', '{"[EMAIL_1]": {"vip": true}, "[EMAIL_1] #2": {"vip": false}}'],
      ['{"4524": "a", "4.524e3": "b"}', '{"[ID_NUMBER_1]": "a", "[ID_NUMBER_1] #2": "b"}'],
      ['{"Kim Minsu": 1, "Kim Minsu": 2}', '{"[PERSON_1]": 1, "[PERSON_1]": 2}'],
    ];
    for (const [text, want] of answers) {
      for (const content of [text, [{ type: "text", text }]]) {
        let seen: BaseMessage[] = [];
        await hook(piiMiddleware({ redactor: none, vault: known() }))({ messages: [new HumanMessage("hi"), new AIMessage({ content } as any), new HumanMessage("ok")] }, async (r) => ((seen = r.messages), new AIMessage("ok")));
        const got = typeof seen[1].content === "string" ? seen[1].content : (seen[1].content as any[])[0].text;
        expect(got, `${text} (unrecorded)`).toBe(want);
      }
      // recorded: the model's own answer comes back in the history
      const vault = known();
      const answer = await hook(piiMiddleware({ redactor: none, vault }))({ messages: [new HumanMessage("list")] }, async () => new AIMessage(text));
      for (const v of [vault, createVault(JSON.parse(JSON.stringify(vault)))]) {
        let seen: BaseMessage[] = [];
        await hook(piiMiddleware({ redactor: none, vault: v }))({ messages: [new HumanMessage("list"), answer, new HumanMessage("again")] }, async (r) => ((seen = r.messages), new AIMessage("ok")));
        expect(seen[1].content, `${text} (recorded)`).toBe(want);
      }
    }
  });
});

// Release gate 4 of 1.7.1: every data slot is built once into the
// plain JSON a provider writes and that tree is masked and sent; identifiers stay only at their authorized paths.
describe("piiMiddleware with a vault: the boundary (release gate 4 of 1.7.1)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: { messages: BaseMessage[]; modelSettings?: any }) => Promise<AIMessage>) => Promise<AIMessage>;
  const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const vault = () => {
    const v = createVault();
    v.tag("PERSON", "Kim Minsu");
    v.tag("EMAIL", "kim@example.com");
    v.tag("USERNAME", "kimminsu_77");
    return v;
  };
  const sent = async (request: Record<string, unknown>) => {
    let got: { messages: BaseMessage[]; modelSettings?: any } | undefined;
    await hook(piiMiddleware({ redactor: none, vault: vault() }))({ modelSettings: undefined, ...request }, async (r) => ((got = r), new AIMessage("ok")));
    return got!;
  };

  it("sends blocks, tool arguments, additional_kwargs and settings as the plain JSON they give", async () => {
    let reads = 0;
    const lazy = { toJSON: () => (reads++ === 0 ? { note: "nothing here" } : { note: "Kim Minsu" }) };
    const got = await sent({
      messages: [
        new HumanMessage({ content: [{ type: "text", text: "hi" }, { type: "x-later-block", v: new Map([["k", 1]]) }] as any }),
        new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: { score: Number.NaN, at: new Date("1990-01-02T00:00:00.000Z") } }], additional_kwargs: { crm: lazy } }),
        new ToolMessage({ content: [{ type: "json", json: { tags: new Set(["kim@example.com"]) } }] as any, tool_call_id: "c1" }),
      ],
      modelSettings: { acme: { who: new Date("1990-01-02T00:00:00.000Z") }, signal: AbortSignal.timeout(60_000) },
    });
    expect((got.messages[0].content as any[])[1]).toEqual({ type: "x-later-block", v: {} });
    expect((got.messages[1] as AIMessage).tool_calls![0].args).toEqual({ score: null, at: "1990-01-02T00:00:00.000Z" });
    expect(got.messages[1].additional_kwargs).toEqual({ crm: { note: "nothing here" } }); // (toJSON called once)
    expect(reads).toBe(1);
    expect((got.messages[2].content as any[])[0]).toEqual({ type: "json", json: { tags: {} } });
    expect(got.modelSettings.acme).toEqual({ who: "1990-01-02T00:00:00.000Z" });
    expect(got.modelSettings.signal).toBeInstanceOf(AbortSignal); // (LangChain reads it itself; it is not sent)
    // input JSON cannot write fails the call before anything is sent
    for (const args of [{ n: BigInt(1) }, { get name() { throw new Error("no"); } }]) {
      let called = false;
      const call = hook(piiMiddleware({ redactor: none, vault: vault() }))({ messages: [new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args }] })] }, async () => ((called = true), new AIMessage("ok")));
      await expect(call).rejects.toThrow(/messages\.0\.tool_calls\.0\.args.* cannot be written as JSON/);
      expect(called).toBe(false);
    }
  });

  it("keeps a block's id at its authorized path in a user's message, and hides it in what a tool returned", async () => {
    const got = await sent({
      messages: [
        new HumanMessage({ content: [{ type: "text", text: "Look it up.", id: "kimminsu_77" }] as any }),
        new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: {} }] }),
        new ToolMessage({ content: [{ type: "text", text: "found", id: "kimminsu_77" }] as any, tool_call_id: "c1" }),
      ],
    });
    expect((got.messages[0].content as any[])[0].id).toBe("kimminsu_77");
    expect((got.messages[2].content as any[])[0].id).toBe("[USERNAME_1]");
  });

  it("keeps identifier names only where a provider defines them: an unknown settings field and extra_body are data", async () => {
    const names = ["type", "signature", "thought_signature", "previous_response_id", "item_id", "conversation_id", "container_id", "file_id", "file_ids", "server_label", "tool_choice"];
    const planted = Object.fromEntries(names.map((n) => [n, "kimminsu_77"]));
    const hidden = Object.fromEntries(names.map((n) => [n, "[USERNAME_1]"]));
    const got = await sent({
      messages: [new HumanMessage("hi")],
      modelSettings: { acme: planted, extra_body: { acme: planted, provider: { order: ["kimminsu_77"] } }, previous_response_id: "kimminsu_77", thinking: { type: "enabled", budget_tokens: 1024 } },
    });
    expect(got.modelSettings).toEqual({
      acme: hidden,
      extra_body: { acme: hidden, provider: { order: ["[USERNAME_1]"] } }, // (data, every key and value: release gate 8)
      previous_response_id: "kimminsu_77",
      thinking: { type: "enabled", budget_tokens: 1024 },
    });
  });
});

// SDK 1.7.1, the gate run of 1.7.1 (gate 5, the HTTP bodies of @model): the model's own fields that go into the
// request (ChatOpenAI `user`, `promptCacheKey`, `modelKwargs`; ChatAnthropic `invocationKwargs`) were sent as written.
// They are data like the model settings: hidden in a copy of the model for each call ------------------------------------
describe("piiMiddleware with a vault: the model's own request fields (gate run of 1.7.1)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<unknown>) => Promise<any>;
  const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const known = () => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    vault.tag("EMAIL", "kim@example.com");
    vault.tag("PHONE", "821023456789");
    return vault;
  };
  const LEAK = /Kim Minsu|kim@example\.com|821023456789/;
  const answer = (text: string) => ({ generations: [{ text, message: new AIMessage(text) }] });

  /** A chat model shaped like ChatOpenAI: the constructor's user, promptCacheKey and modelKwargs, read where a model it
   *  keeps for the request reports its parameters (ChatOpenAI's completions); bindTools and withConfig make a new
   *  model from the constructor's fields, as ChatOpenAI's withConfig does. `sent` gets each request's parameters. */
  const openAILike = (fields: Record<string, any>, sent: Record<string, unknown>[], reply = "done"): any => {
    class Completions extends FakeListChatModel {
      user = fields.user;
      promptCacheKey = fields.promptCacheKey;
      modelKwargs: Record<string, unknown> = fields.modelKwargs ?? {};
      invocationParams(options?: any): any {
        return { model: fields.model ?? "gpt-x", user: this.user, ...this.modelKwargs, prompt_cache_key: options?.promptCacheKey ?? this.promptCacheKey };
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        sent.push({ ...this.invocationParams(options), messages: messages.map((m) => m.content) });
        return answer(reply);
      }
    }
    class Model extends FakeListChatModel {
      fields = fields;
      user = fields.user;
      modelKwargs: Record<string, unknown> = fields.modelKwargs ?? {};
      completions = new Completions({ responses: ["x"] });
      invocationParams(options?: any): any {
        return this.completions.invocationParams(options);
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        return this.completions._generate(messages, options);
      }
      bindTools(): any {
        return this.withConfig({});
      }
      withConfig(_config: unknown): any {
        return new Model({ responses: ["x"] });
      }
    }
    return new Model({ responses: ["x"] });
  };
  /** A chat model shaped like ChatAnthropic: invocationKwargs in its parameters and spread into the request once more
   *  as it is sent. */
  const anthropicLike = (invocationKwargs: Record<string, unknown>, sent: Record<string, unknown>[]): any => {
    class Model extends FakeListChatModel {
      invocationKwargs = invocationKwargs;
      invocationParams(_options?: unknown): any {
        return { model: "claude-x", max_tokens: 1024, ...this.invocationKwargs };
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        sent.push({ ...this.invocationParams(options), ...this.invocationKwargs, messages: messages.map((m) => m.content) });
        return answer("done");
      }
      bindTools(): any {
        return this;
      }
    }
    return new Model({ responses: ["x"] });
  };
  const own = () => ({
    model: "gpt-x",
    user: "Kim Minsu",
    promptCacheKey: "orders-kim@example.com",
    modelKwargs: {
      metadata: { customer: "Kim Minsu", phone: 821023456789, "Kim Minsu": "vip", tier: "gold" },
      safety_identifier: "kim@example.com",
      prompt: { id: "Kim Minsu", version: "2", variables: { customer_name: "Kim Minsu" } },
      previous_response_id: "kim@example.com",
    },
  });

  it("sends the model's own fields hidden from a copy for the call, in a real agent, and leaves the model as it was", async () => {
    const sent: Record<string, unknown>[] = [];
    const fields = own();
    const model = openAILike(fields, sent, "Hello [PERSON_1], your key is [EMAIL_1].");
    const agent = createAgent({ model, tools: [], middleware: [piiMiddleware({ redactor: none, vault: known() })] });
    const out = (await agent.invoke({ messages: [new HumanMessage("Hi.")] })) as any;
    expect(sent).toHaveLength(1);
    const { messages: _messages, ...params } = sent[0];
    expect(params).toEqual({
      model: "gpt-x",
      user: "[PERSON_1]",
      metadata: { customer: "[PERSON_1]", phone: "[PHONE_1]", "[PERSON_1]": "vip", tier: "gold" },
      safety_identifier: "[EMAIL_1]",
      // (identifiers a provider defines stay at their paths: a stored prompt's id, a previous response's id)
      prompt: { id: "Kim Minsu", version: "2", variables: { customer_name: "[PERSON_1]" } },
      previous_response_id: "kim@example.com",
      prompt_cache_key: "orders-[EMAIL_1]",
    });
    // the placeholders the call put in through the model's fields are its own: the answer gets the values back
    expect(out.messages.at(-1).content).toBe("Hello Kim Minsu, your key is kim@example.com.");
    // the developer's model is not changed
    expect(fields).toEqual(own());
    expect(model.user).toBe("Kim Minsu");
    expect(model.completions.user).toBe("Kim Minsu");
    expect(model.completions.invocationParams({}).metadata.customer).toBe("Kim Minsu");
  });

  it("hides a kwargs object the provider package spreads into the request once more as it sends it (ChatAnthropic)", async () => {
    const sent: Record<string, unknown>[] = [];
    const kwargs = { metadata: { user_id: "Kim Minsu" }, container: "Kim Minsu" };
    const agent = createAgent({ model: anthropicLike(kwargs, sent), tools: [], middleware: [piiMiddleware({ redactor: none, vault: known() })] });
    await agent.invoke({ messages: [new HumanMessage("Hi.")] });
    expect(sent[0].metadata).toEqual({ user_id: "[PERSON_1]" });
    expect(sent[0].container).toBe("Kim Minsu"); // (Anthropic's container id: kept where Anthropic defines it)
    expect(kwargs).toEqual({ metadata: { user_id: "Kim Minsu" }, container: "Kim Minsu" });
  });

  it("gives a copy that sends the fields as they were read when they hold no known value, and hides values in fields of every route", async () => {
    // (release gate 8 on f25d519, item 2: the model itself was given, and its fields were written again)
    const plain = openAILike({ user: "u-17", modelKwargs: { metadata: { tier: "gold" } } }, []);
    let got: any;
    await hook(piiMiddleware({ redactor: none, vault: known() }))({ messages: [new HumanMessage("hi")], model: plain, responseFormat: undefined }, async (r) => ((got = r.model), new AIMessage("ok")));
    expect(got).not.toBe(plain);
    expect(Object.getPrototypeOf(got)).toBe(plain);
    expect(got.invocationParams({})).toEqual(plain.invocationParams({}));
    // the model's user only, a kwargs object only, the prompt cache key only, a class instance in the kwargs
    const cases: [string, () => any][] = [
      ["user", () => openAILike({ user: "Kim Minsu" }, [])],
      ["modelKwargs", () => openAILike({ modelKwargs: { user: "Kim Minsu" } }, [])],
      ["promptCacheKey", () => openAILike({ promptCacheKey: "Kim Minsu" }, [])],
      ["a class instance in modelKwargs", () => openAILike({ modelKwargs: { metadata: new (class { name = "Kim Minsu" })() } }, [])],
    ];
    for (const [name, make] of cases) {
      const model = make();
      let seen: any;
      await hook(piiMiddleware({ redactor: none, vault: known() }))({ messages: [new HumanMessage("hi")], model, responseFormat: undefined }, async (r) => ((seen = r.model), new AIMessage("ok")));
      expect(seen, name).not.toBe(model);
      expect(JSON.stringify(seen.invocationParams({})), name).not.toMatch(LEAK);
      expect(JSON.stringify(seen.completions.invocationParams({})), name).not.toMatch(LEAK);
      // what the copy makes for the call (bindTools, withConfig) is hidden too
      expect(JSON.stringify(seen.bindTools([]).invocationParams({})), name).not.toMatch(LEAK);
      expect(JSON.stringify(model.invocationParams({})), name).toMatch(LEAK);
    }
  });

  // (the merge of gate 4 and the model's own fields: the model's fields are read as model settings are read, from the
  // plain JSON tree a provider writes)
  it("reads the model's own fields as model settings: an unknown field and extra_body are data, and a field without a known value is sent as it was read", async () => {
    const sent: Record<string, unknown>[] = [];
    const model = openAILike(
      {
        modelKwargs: {
          acme: { previous_response_id: "Kim Minsu", file_id: "kim@example.com" },
          extra_body: { type: "kim@example.com", provider: { order: ["Kim Minsu"] } },
          previous_response_id: "Kim Minsu",
        },
      },
      sent,
    );
    const agent = createAgent({ model, tools: [], middleware: [piiMiddleware({ redactor: none, vault: known() })] });
    await agent.invoke({ messages: [new HumanMessage("Hi.")] });
    expect(sent).toHaveLength(1);
    expect(sent[0].acme).toEqual({ previous_response_id: "[PERSON_1]", file_id: "[EMAIL_1]" });
    // (extra_body is data, every key and value of it, as in the model settings: release gate 8)
    expect(sent[0].extra_body).toEqual({ type: "[EMAIL_1]", provider: { order: ["[PERSON_1]"] } });
    expect(sent[0].previous_response_id).toBe("Kim Minsu");
    // no known value: a Date, an undefined field and a Map are written as JSON writes them, and placeholder-like text
    // stays. They are not plain JSON, so the copy sends them as they were read and built into JSON once (release gate
    // 8 on f25d519, item 2: until then the model was given as it is, and its fields were written again)
    const plain = openAILike({ user: "[PERSON_1] u-17", modelKwargs: { metadata: { since: new Date("2020-01-02T00:00:00.000Z"), tags: new Map([["k", 1]]), gone: undefined } } }, []);
    let got: any;
    await hook(piiMiddleware({ redactor: none, vault: known() }))({ messages: [new HumanMessage("hi")], model: plain, responseFormat: undefined }, async (r) => ((got = r.model), new AIMessage("ok")));
    expect(got).not.toBe(plain);
    expect(got.invocationParams({})).toEqual({ model: "gpt-x", user: "[PERSON_1] u-17", metadata: { since: "2020-01-02T00:00:00.000Z", tags: {} } });
  });

  it("hides the fields of the model a model made on each call makes (initChatModel), when it makes it", async () => {
    const sent: Record<string, unknown>[] = [];
    const inner = openAILike(own(), sent);
    // (the shape of initChatModel's ConfigurableModel: it makes its model when it is called)
    class Made extends FakeListChatModel {
      async _getModelInstance(): Promise<any> {
        return inner;
      }
      async invoke(input: any, options?: any): Promise<any> {
        return (await this._getModelInstance()).invoke(input, options);
      }
      bindTools(): any {
        return new Made({ responses: ["x"] });
      }
    }
    const agent = createAgent({ model: new Made({ responses: ["x"] }), tools: [], middleware: [piiMiddleware({ redactor: none, vault: known() })] });
    await agent.invoke({ messages: [new HumanMessage("Hi.")] });
    expect(sent).toHaveLength(1);
    expect(sent[0].user).toBe("[PERSON_1]");
    expect(JSON.stringify([sent[0].metadata, sent[0].safety_identifier, sent[0].prompt_cache_key])).not.toMatch(LEAK);
  });

  it("fails before sending when the model cannot be copied, and reads and hides the parameters of a model that reports them only with the call's options", async () => {
    // (a class that keeps the value in a private field: a copy of it cannot read it. The call sends a copy that holds
    // the model's fields as they were read once, so such a model fails the call when the copy reports its parameters
    // for the request, before anything is sent: release gate 8 on f25d519, item 2)
    let sent = false;
    class Private extends FakeListChatModel {
      #user = "Kim Minsu";
      invocationParams(): any {
        return { model: "x", user: this.#user };
      }
      async _generate(): Promise<any> {
        sent = true;
        return answer("done");
      }
      bindTools(): any {
        return this;
      }
    }
    const request = { messages: [new HumanMessage("hi")], model: new Private({ responses: ["x"] }), responseFormat: undefined };
    const call = (r: any) => r.model.invoke([new HumanMessage("hi")]);
    await expect(hook(piiMiddleware({ redactor: none, vault: known() }))(request, call)).rejects.toThrow(/the model cannot be copied for the call.*Cannot read private member.*so the call is not sent/);
    expect(sent).toBe(false);
    // (in a real agent too)
    const agent = createAgent({ model: new Private({ responses: ["x"] }) as never, tools: [], middleware: [piiMiddleware({ redactor: none, vault: known() })] });
    await expect(agent.invoke({ messages: [new HumanMessage("Hi.")] })).rejects.toThrow(/cannot be copied for the call/);
    expect(sent).toBe(false);
    // a model that cannot report its parameters without the call's options is not failed for that: its copy hides what
    // it reports when the call is made
    class NeedsOptions extends FakeListChatModel {
      user = "Kim Minsu";
      invocationParams(options?: any): any {
        if (!options?.stop) throw new Error("no stop sequences");
        return { model: "x", user: this.user, stop: options.stop };
      }
    }
    let params: any;
    await hook(piiMiddleware({ redactor: none, vault: known() }))({ messages: [new HumanMessage("hi")], model: new NeedsOptions({ responses: ["x"] }), responseFormat: undefined }, async (r) => {
      params = r.model.invocationParams({ stop: ["Kim Minsu"] });
      return new AIMessage("ok");
    });
    expect(params).toEqual({ model: "x", user: "[PERSON_1]", stop: ["[PERSON_1]"] });
  });
});

// SDK 1.7.1, release gate 8 (the confirmation on f25d519), item 2: the model's own data was read to look for known
// values, and when that read held none the model itself was sent, so the provider package wrote its fields again
// (ChatOpenAI modelKwargs and ChatAnthropic invocationKwargs holding metadata whose toJSON gave a designated ordinary
// word first and "draft" afterwards sent "draft"). Every field that reaches the request is built once, and that
// snapshot is sent, whether or not anything in it was masked ----------------------------------------------------------
describe("piiMiddleware with a vault: the model's own data read once (release gate 8 on f25d519, item 2)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<unknown>) => Promise<any>;
  const none = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const holding = (...values: [string, string][]) => {
    const vault = createVault();
    for (const [label, value] of values) vault.tag(label, value);
    return vault;
  };
  const kim = () => holding(["PERSON", "Kim Minsu"], ["EMAIL", "kim@example.com"]);
  const answer = (text: string) => ({ generations: [{ text, message: new AIMessage(text) }] });
  /** What a provider package writes: the JSON of the request. */
  const written = (x: unknown) => JSON.parse(JSON.stringify(x));
  /** An object whose toJSON() gives `first` the first time and `then` afterwards; `state.calls` counts the calls. */
  const flipping = (first: unknown, then: unknown) => {
    const state = { calls: 0 };
    return { state, value: { toJSON: () => (state.calls++ === 0 ? first : then) } };
  };
  /** A plain object whose field `key` is a getter: `first` the first time, `then` afterwards; `state.reads` counts. */
  const getterOf = (key: string, first: unknown, then: unknown) => {
    const state = { reads: 0 };
    return { state, value: Object.defineProperty({}, key, { enumerable: true, get: () => (state.reads++ === 0 ? first : then) }) as Record<string, unknown> };
  };

  /** What a provider package does for one request, in either mode: it reports its parameters once for tracing, then
   *  once for the request, whose JSON it writes (`sent`); `extra` is what it spreads into the request besides them. */
  const requested = (model: any, messages: BaseMessage[], options: any, sent: any[], extra: () => object = () => ({})) => {
    model.invocationParams(options); // (tracing)
    sent.push(written({ ...model.invocationParams(options), ...extra(), messages: messages.map((m) => m.content) }));
  };
  const chunk = async (text: string) => {
    const { ChatGenerationChunk } = await import("@langchain/core/outputs");
    const { AIMessageChunk } = await import("@langchain/core/messages");
    return new ChatGenerationChunk({ text, message: new AIMessageChunk(text) });
  };
  /** A chat model shaped like ChatOpenAI: the wrapper keeps a Chat Completions model and a Responses model, both made
   *  from the same constructor fields (one modelKwargs object), and reports its parameters and sends through the one
   *  `api` names; withConfig makes a new model from the constructor's fields with call options (defaultOptions) it reads
   *  with the call's. */
  const openAIShaped = (fields: Record<string, any>, sent: any[], defaultOptions?: Record<string, unknown>, api: "completions" | "responses" = "completions"): any => {
    class Api extends FakeListChatModel {
      user = fields.user;
      promptCacheKey = fields.promptCacheKey;
      modelKwargs: Record<string, unknown> = fields.modelKwargs ?? {};
      invocationParams(options?: any): any {
        // (as both of ChatOpenAI's models do: modelKwargs spread, and read again for the prompt cache key)
        return { model: "gpt-x", user: this.user, ...this.modelKwargs, prompt_cache_key: options?.promptCacheKey ?? this.modelKwargs?.prompt_cache_key ?? this.promptCacheKey };
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        requested(this, messages, options, sent);
        return answer("done");
      }
      async *_streamResponseChunks(messages: BaseMessage[], options: any): AsyncGenerator<any> {
        requested(this, messages, { ...options, stream: true }, sent);
        yield await chunk("done");
      }
    }
    class Model extends FakeListChatModel {
      fields = fields;
      defaultOptions = defaultOptions;
      user = fields.user;
      modelKwargs: Record<string, unknown> = fields.modelKwargs ?? {};
      completions = new Api({ responses: ["x"] });
      // (ChatOpenAI's is `responses`, which FakeListChatModel has for its answers)
      responsesApi = new Api({ responses: ["x"] });
      get api(): Api {
        return api === "completions" ? this.completions : this.responsesApi;
      }
      invocationParams(options?: any): any {
        return this.api.invocationParams({ ...this.defaultOptions, ...options });
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        return this.api._generate(messages, { ...this.defaultOptions, ...options });
      }
      async *_streamResponseChunks(messages: BaseMessage[], options: any): AsyncGenerator<any> {
        yield* this.api._streamResponseChunks(messages, { ...this.defaultOptions, ...options });
      }
      bindTools(): any {
        return this.withConfig({});
      }
      withConfig(config: Record<string, unknown>): any {
        return openAIShaped(this.fields, sent, { ...this.defaultOptions, ...config }, api);
      }
    }
    return new Model({ responses: ["x"] });
  };
  /** A chat model shaped like ChatAnthropic: invocationKwargs in its parameters (and a call option `extra`), and spread
   *  into the request once more as it is sent. */
  const anthropicShaped = (invocationKwargs: Record<string, unknown>, sent: any[]): any => {
    class Model extends FakeListChatModel {
      invocationKwargs = invocationKwargs;
      invocationParams(options?: any): any {
        return { model: "claude-x", max_tokens: 1024, ...this.invocationKwargs, ...(options?.extra === undefined ? {} : { extra: options.extra }) };
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        requested(this, messages, options, sent, () => this.invocationKwargs);
        return answer("done");
      }
      async *_streamResponseChunks(messages: BaseMessage[], options: any): AsyncGenerator<any> {
        requested(this, messages, options, sent, () => ({ ...this.invocationKwargs, stream: true }));
        yield await chunk("done");
      }
      bindTools(): any {
        return this;
      }
    }
    return new Model({ responses: ["x"] });
  };
  type Mode = "invoke" | "stream";
  const runAgent = async (model: unknown, vault = kim(), mode: Mode = "invoke") => {
    const agent = createAgent({ model: model as never, tools: [], middleware: [piiMiddleware({ redactor: none, vault })] });
    if (mode === "invoke") return agent.invoke({ messages: [new HumanMessage("Hello.")] });
    for await (const _ of await agent.stream({ messages: [new HumanMessage("Hello.")] }, { streamMode: "messages" }));
  };
  /** The three adapters of the reviewer's run: ChatOpenAI on Chat Completions and on Responses (modelKwargs), and
   *  ChatAnthropic (invocationKwargs), with OpenAI metadata under `owner` and Anthropic's under `user_id`. */
  const ADAPTERS = ["completions", "responses", "anthropic"] as const;
  const MODES: Mode[] = ["invoke", "stream"];
  const keyOf = (adapter: (typeof ADAPTERS)[number]) => (adapter === "anthropic" ? "user_id" : "owner");
  const withMetadata = (adapter: (typeof ADAPTERS)[number], metadata: unknown, sent: any[]) =>
    adapter === "anthropic" ? anthropicShaped({ metadata }, sent) : openAIShaped({ modelKwargs: { metadata } }, sent, undefined, adapter);

  it("sends the first snapshot of the reviewer's input: metadata whose toJSON gives a designated ordinary word first and draft afterwards", async () => {
    // (the word U+C9C0 U+C6D0 U+AE08 with its first two syllables held, a designated ordinary word kept as written; or
    // with a name held that the metadata does not hold)
    const word = String.fromCodePoint(0xc9c0, 0xc6d0, 0xae08);
    const vaults: [string, () => ReturnType<typeof createVault>][] = [
      ["the word's first two syllables held", () => holding(["PERSON", word.slice(0, 2)])],
      ["Kim Minsu held", kim],
    ];
    for (const [held, vault] of vaults)
      for (const adapter of ADAPTERS)
        for (const mode of MODES) {
          const at = `${held}, ${adapter}, ${mode}`;
          const key = keyOf(adapter);
          const sent: any[] = [];
          const meta = flipping({ [key]: word }, { [key]: "draft" });
          await runAgent(withMetadata(adapter, meta.value, sent), vault(), mode);
          expect(sent, at).toHaveLength(1);
          expect(sent[0].metadata, at).toEqual({ [key]: word });
          // (one toJSON call for the whole call: ChatOpenAI's two chat models and the model withConfig makes share one
          // modelKwargs object; the provider reports the parameters twice and ChatAnthropic spreads its kwargs again)
          expect(meta.state.calls, at).toBe(1);
        }
  });

  it("sends the first read of every sibling: a known value on the second or the first write, a getter, a Proxy, a class instance, the model's user", async () => {
    type Case = [string, (key: string) => { value: unknown; reads: () => number }, (key: string) => unknown];
    const cases: Case[] = [
      ["toJSON, a known value on the second write", (k) => {
        const f = flipping({ [k]: "draft" }, { [k]: "Kim Minsu" });
        return { value: f.value, reads: () => f.state.calls };
      }, (k) => ({ [k]: "draft" })],
      ["toJSON, a known value on the first write", (k) => {
        const f = flipping({ [k]: "Kim Minsu" }, { [k]: "draft" });
        return { value: f.value, reads: () => f.state.calls };
      }, (k) => ({ [k]: "[PERSON_1]" })],
      ["a getter", (k) => {
        const g = getterOf(k, "draft", "Kim Minsu");
        return { value: g.value, reads: () => g.state.reads };
      }, (k) => ({ [k]: "draft" })],
      ["a Proxy", (k) => {
        const state = { reads: 0 };
        const value = new Proxy({ [k]: "draft" }, { get: (t, p, r) => (p === k ? (state.reads++ === 0 ? "draft" : "Kim Minsu") : Reflect.get(t, p, r)) });
        return { value, reads: () => state.reads };
      }, (k) => ({ [k]: "draft" })],
      ["a class instance with a getter of its own", (k) => {
        const state = { reads: 0 };
        class Row {
          constructor() {
            Object.defineProperty(this, k, { enumerable: true, get: () => (state.reads++ === 0 ? "draft" : "Kim Minsu") });
          }
        }
        return { value: new Row(), reads: () => state.reads };
      }, (k) => ({ [k]: "draft" })],
      ["a toJSON object one level down", (k) => {
        const f = flipping("draft", "Kim Minsu");
        return { value: { [k]: f.value }, reads: () => f.state.calls };
      }, (k) => ({ [k]: "draft" })],
    ];
    for (const [name, make, want] of cases)
      for (const adapter of ADAPTERS)
        for (const mode of MODES) {
          const at = `${name} (${adapter}, ${mode})`;
          const key = keyOf(adapter);
          const sent: any[] = [];
          const meta = make(key);
          await runAgent(withMetadata(adapter, meta.value, sent), kim(), mode);
          expect(sent, at).toHaveLength(1);
          expect(sent[0].metadata, at).toEqual(want(key));
          expect(JSON.stringify(sent), at).not.toContain("Kim Minsu");
          expect(meta.reads(), at).toBeLessThanOrEqual(1);
        }
    // the model's own user (a constructor field) given as a toJSON object; ChatAnthropic's invocationKwargs given by a
    // getter of the model, and a kwargs object whose metadata a getter gives
    for (const mode of MODES) {
      const sent: any[] = [];
      const user = flipping("draft", "Kim Minsu");
      await runAgent(openAIShaped({ user: user.value }, sent), kim(), mode);
      expect(sent[0].user, mode).toBe("draft");
      expect(user.state.calls, mode).toBe(1);
      const bySetter: any[] = [];
      let reads = 0;
      const model = anthropicShaped({}, bySetter);
      Object.defineProperty(model, "invocationKwargs", { configurable: true, enumerable: true, get: () => (reads++ === 0 ? { metadata: { user_id: "draft" } } : { metadata: { user_id: "Kim Minsu" } }) });
      await runAgent(model, kim(), mode);
      expect(bySetter[0].metadata, mode).toEqual({ user_id: "draft" });
      expect(reads, mode).toBe(1);
      const kwargs: any[] = [];
      const g = getterOf("metadata", { user_id: "draft" }, { user_id: "Kim Minsu" });
      await runAgent(anthropicShaped(g.value, kwargs), kim(), mode);
      expect(kwargs[0].metadata, mode).toEqual({ user_id: "draft" });
      expect(g.state.reads, mode).toBe(1);
    }
  });

  it("sends a field the model reads only with some call options as it was first read", async () => {
    // (a getter of the model read only when the call asks for audio: it is read once, when the call reports it)
    for (const mode of MODES) {
      const sent: any[] = [];
      let reads = 0;
      class Audio extends FakeListChatModel {
        get audio(): unknown {
          return reads++ === 0 ? { voice: "draft" } : { voice: "Kim Minsu" };
        }
        invocationParams(options?: any): any {
          return { model: "x", ...(options?.audio ? { audio: this.audio } : {}) };
        }
        async _generate(messages: BaseMessage[], options: any): Promise<any> {
          requested(this, messages, { ...options, audio: true }, sent);
          return answer("done");
        }
        async *_streamResponseChunks(messages: BaseMessage[], options: any): AsyncGenerator<any> {
          requested(this, messages, { ...options, audio: true }, sent);
          yield await chunk("done");
        }
        bindTools(): any {
          return this;
        }
      }
      await runAgent(new Audio({ responses: ["x"] }), kim(), mode);
      expect(sent, mode).toHaveLength(1);
      expect(sent[0].audio, mode).toEqual({ voice: "draft" });
      expect(reads, mode).toBe(1);
    }
  });

  it("gives a copy whose fields are what was read, and leaves the model as it was", async () => {
    const meta = flipping({ owner: "draft" }, { owner: "Kim Minsu" });
    const model = openAIShaped({ user: "u-17", modelKwargs: { metadata: meta.value, tier: "gold" } }, []);
    let got: any;
    await hook(piiMiddleware({ redactor: none, vault: kim() }))({ messages: [new HumanMessage("hi")], model, responseFormat: undefined }, async (r) => ((got = r.model), new AIMessage("ok")));
    expect(Object.getPrototypeOf(got)).toBe(model);
    expect(got.invocationParams({})).toEqual({ model: "gpt-x", user: "u-17", metadata: { owner: "draft" }, tier: "gold" });
    expect(got.modelKwargs).toEqual({ metadata: { owner: "draft" }, tier: "gold" });
    expect(got.completions.modelKwargs).toBe(got.modelKwargs);
    expect(meta.state.calls).toBe(1);
    // the model holds what the application gave it
    expect(model.modelKwargs.metadata).toBe(meta.value);
    expect(model.completions.modelKwargs.metadata).toBe(meta.value);
    // what the model's own code writes into its fields during the call is written on the model, as without the
    // middleware, and the copy reads it from then on
    got.completions.user = "u-18";
    expect(model.completions.user).toBe("u-18");
    expect(got.completions.invocationParams({}).user).toBe("u-18");
  });

  it("keeps the state a model writes during a call (a scripted model's next answer), in a real agent", async () => {
    // (a model that answers from a script, as the corpus runners' does: a tool call first, then the answer. Its step is
    // written on the model, so the next call goes on with the script; written on the copy, the agent looped until its
    // recursion limit)
    for (const mode of MODES) {
      const sent: any[] = [];
      class Scripted extends FakeListChatModel {
        step = 0;
        modelKwargs = { metadata: flipping({ owner: "draft" }, { owner: "Kim Minsu" }).value };
        invocationParams(): any {
          return { model: "x", ...this.modelKwargs };
        }
        async _generate(messages: BaseMessage[], options: any): Promise<any> {
          requested(this, messages, options, sent);
          const message = this.step++ === 0 ? new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: { q: "orders" } }] }) : new AIMessage("Done.");
          return { generations: [{ text: "", message }] };
        }
        async *_streamResponseChunks(messages: BaseMessage[], options: any): AsyncGenerator<any> {
          const out = await this._generate(messages, options);
          const { ChatGenerationChunk } = await import("@langchain/core/outputs");
          const { AIMessageChunk } = await import("@langchain/core/messages");
          const m = out.generations[0].message as AIMessage;
          yield new ChatGenerationChunk({ text: String(m.content), message: new AIMessageChunk({ content: m.content, tool_call_chunks: (m.tool_calls ?? []).map((c, index) => ({ id: c.id, name: c.name, args: JSON.stringify(c.args), index, type: "tool_call_chunk" as const })) }) });
        }
        bindTools(): any {
          return this;
        }
      }
      const model = new Scripted({ responses: ["x"] });
      const lookup = tool(async () => "2 orders", { name: "lookup", description: "Looks up orders.", schema: z.object({ q: z.string() }) });
      const agent = createAgent({ model: model as never, tools: [lookup], middleware: [piiMiddleware({ redactor: none, vault: kim() })] });
      if (mode === "invoke") {
        const out = (await agent.invoke({ messages: [new HumanMessage("Hello.")] })) as any;
        expect(out.messages.at(-1).content, mode).toBe("Done.");
      } else for await (const _ of await agent.stream({ messages: [new HumanMessage("Hello.")] }, { streamMode: "messages" }));
      expect(model.step, mode).toBe(2);
      expect(sent, mode).toHaveLength(2);
      // (each call reads the model's data once: a new read for each call, as the provider package would)
      expect(sent[0].metadata, mode).toEqual({ owner: "draft" });
      expect(JSON.stringify(sent), mode).not.toContain("Kim Minsu");
    }
  });

  it("sends the first read of call options the model is bound to (withConfig) and of a binding's kwargs", async () => {
    const sent: any[] = [];
    const key = flipping("orders-draft", "orders-kim@example.com");
    await runAgent(openAIShaped({}, sent, { promptCacheKey: key.value }));
    expect(sent).toHaveLength(1);
    expect(sent[0].prompt_cache_key).toBe("orders-draft");
    expect(key.state.calls).toBe(1);
    // a binding (RunnableBinding) of a model with call options: its kwargs are read once into the copy
    const { RunnableBinding } = await import("@langchain/core/runnables");
    const bound: any[] = [];
    const extra = flipping({ note: "draft" }, { note: "Kim Minsu" });
    const binding = new RunnableBinding({ bound: anthropicShaped({}, bound), kwargs: { extra: extra.value } as never, config: {} });
    let got: any;
    await hook(piiMiddleware({ redactor: none, vault: kim() }))({ messages: [new HumanMessage("hi")], model: binding, responseFormat: undefined }, async (r) => {
      got = r.model;
      await r.model.invoke([new HumanMessage("hi")]);
      return new AIMessage("ok");
    });
    expect(got).not.toBe(binding);
    expect(bound).toHaveLength(1);
    expect(bound[0].extra).toEqual({ note: "draft" });
    expect(extra.state.calls).toBe(1);
  });

  it("reads a field once when the model reports its parameters only with the call's options", async () => {
    const meta = flipping({ owner: "draft" }, { owner: "Kim Minsu" });
    class NeedsOptions extends FakeListChatModel {
      modelKwargs = { metadata: meta.value };
      invocationParams(options?: any): any {
        if (!options?.stop) throw new Error("no stop sequences");
        return { model: "x", ...this.modelKwargs, stop: options.stop };
      }
    }
    const reported: string[] = [];
    await hook(piiMiddleware({ redactor: none, vault: kim() }))({ messages: [new HumanMessage("hi")], model: new NeedsOptions({ responses: ["x"] }), responseFormat: undefined }, async (r) => {
      reported.push(JSON.stringify(r.model.invocationParams({ stop: ["a"] })), JSON.stringify(r.model.invocationParams({ stop: ["a"] })));
      return new AIMessage("ok");
    });
    expect(reported).toEqual([JSON.stringify({ model: "x", metadata: { owner: "draft" }, stop: ["a"] }), JSON.stringify({ model: "x", metadata: { owner: "draft" }, stop: ["a"] })]);
    expect(meta.state.calls).toBe(1);
  });

  it("fails before sending when the model cannot be copied, whatever its fields hold, and when a field cannot be written", async () => {
    // (a class that keeps a field in a private `#` field: a copy cannot report its parameters. It is never sent as it
    // is: a field read for masking would be read again by the provider package)
    const sent: any[] = [];
    class Private extends FakeListChatModel {
      #model = "x";
      modelKwargs: Record<string, unknown>;
      constructor(kwargs: Record<string, unknown>) {
        super({ responses: ["x"] });
        this.modelKwargs = kwargs;
      }
      invocationParams(): any {
        return { model: this.#model, ...this.modelKwargs };
      }
      async _generate(messages: BaseMessage[], options: any): Promise<any> {
        sent.push(written({ ...this.invocationParams(), messages: messages.map((m) => m.content) }));
        return answer("done");
      }
      bindTools(): any {
        return this;
      }
    }
    for (const [name, kwargs] of [
      ["metadata that gives other data each time", () => ({ metadata: flipping({ owner: "draft" }, { owner: "Kim Minsu" }).value })],
      ["plain JSON without a known value", () => ({ metadata: { owner: "draft" } })],
      ["plain JSON with a known value", () => ({ metadata: { owner: "Kim Minsu" } })],
    ] as const) {
      await expect(runAgent(new Private(kwargs())), name).rejects.toThrow(/the model cannot be copied for the call: its invocationParams runs on the model and not on a copy of it/);
      expect(sent, name).toHaveLength(0);
    }
    // a field JSON cannot write fails the call before anything is sent, naming it, and its toJSON is called once
    let calls = 0;
    const throwing = {
      toJSON: () => {
        calls++;
        throw new Error("refuses");
      },
    };
    let handled = false;
    const request = (model: unknown) => ({ messages: [new HumanMessage("hi")], model, responseFormat: undefined });
    await expect(hook(piiMiddleware({ redactor: none, vault: kim() }))(request(openAIShaped({ modelKwargs: { metadata: throwing } }, sent)), async () => ((handled = true), new AIMessage("ok")))).rejects.toThrow(
      /modelKwargs\.metadata cannot be written as JSON/,
    );
    expect(handled).toBe(false);
    expect(calls).toBe(1);
    // also where the model's own code catches what reading it throws (the field is read when the model reports its
    // parameters), and in a field the provider package reads only with the call's options
    class Swallows extends FakeListChatModel {
      user: unknown = throwing;
      invocationParams(): any {
        try {
          return { model: "x", user: this.user };
        } catch {
          return { model: "x" };
        }
      }
    }
    await expect(hook(piiMiddleware({ redactor: none, vault: kim() }))(request(new Swallows({ responses: ["x"] })), async () => ((handled = true), new AIMessage("ok")))).rejects.toThrow(
      /the model's user cannot be written as JSON/,
    );
    expect(handled).toBe(false);
  });

  it("sends the definition that was read of a tool whose description a getter gives", async () => {
    const t = tool(async () => "ok", { name: "lookup", description: "x", schema: z.object({}) });
    let reads = 0;
    Object.defineProperty(t, "description", { enumerable: true, configurable: true, get: () => (reads++ === 0 ? "Looks up orders." : "Looks up Kim Minsu.") });
    let got: any;
    await hook(piiMiddleware({ redactor: none, vault: kim() }))({ messages: [new HumanMessage("hi")], tools: [t], modelSettings: undefined }, async (r) => ((got = r), new AIMessage("ok")));
    expect(got.tools[0]).not.toBe(t);
    // (the definition LangChain wrote of the tool, with the description it read: the provider gets that, not the tool,
    // whose getter it would read again)
    expect(got.tools[0].function.description).toBe("Looks up orders.");
    expect(reads).toBeGreaterThan(0);
  });

  it("builds what the masker reads once and fails closed where JSON cannot write it (jsonForm, value)", async () => {
    const { jsonForm } = await import("../src/masker");
    const g = getterOf("name", "draft", "Kim Minsu");
    expect(jsonForm(g.value)).toEqual({ name: "draft" });
    expect(g.state.reads).toBe(1);
    expect(() => jsonForm({ toJSON: () => { throw new Error("refuses"); } })).toThrow(/cannot be written as JSON/);
    const masker = createMasker({ redactor: none, vault: kim() });
    await expect(masker.value(new (class { toJSON() { throw new Error("refuses"); } })(), {}, false)).rejects.toThrow(/cannot be written as JSON/);
    await expect(masker.value(new (class { name = "Kim Minsu"; })(), {}, false)).resolves.toEqual({ name: "[PERSON_1]" });
    // an object met again in the call gives the tree of its first read (its toJSON is not called again), walked as the
    // second reader walks it: what the first kept as it is (a class instance with methods, in a model's field) is built
    const { plainTree } = await import("../src/masker");
    class Contact {
      constructor(public name: string) {}
      greet() {
        return this.name;
      }
    }
    const note = flipping("draft", "Kim Minsu");
    const shared = { note: note.value, contact: new Contact("Kim Minsu") };
    const trees = new Map<object, unknown>();
    expect(plainTree(shared, { memo: trees, keep: (_p, y) => y instanceof Contact })).toEqual({ note: "draft", contact: shared.contact });
    expect(plainTree(shared, { memo: trees })).toEqual({ note: "draft", contact: { name: "Kim Minsu" } });
    expect(plainTree({ again: shared }, { memo: trees })).toEqual({ again: { note: "draft", contact: { name: "Kim Minsu" } } });
    expect(note.state.calls).toBe(1);
  });
});

// SDK 1.7.1, release gate 8 (the independent confirmation on 9b2d19e), items 3 to 5 in LangChain.js: a definition, a
// schema and a response format given as a class instance or through getters are built once into the JSON they give and
// that is read and sent; a message's tool calls too; a provider's identifier in the model settings stays only at the
// exact path where that provider defines it ---------------------------------------------------------------------------
describe("piiMiddleware with a vault: schemas and lists built once, identifiers at their exact paths (release gate 8)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<AIMessage>) => Promise<AIMessage>;
  const off = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const known = () => {
    const v = createVault();
    v.tag("PERSON", "Kim Minsu");
    v.tag("EMAIL", "kim@example.com");
    v.tag("USERNAME", "kimminsu_77");
    v.tag("PHONE", "821023456789");
    return v;
  };
  const sent = async (request: Record<string, unknown>) => {
    let got: any;
    await hook(piiMiddleware({ redactor: off, vault: known() }))({ messages: [new HumanMessage("hi")], modelSettings: undefined, ...request }, async (r) => ((got = r), new AIMessage("ok")));
    return got;
  };
  class Field {
    type = "string";
    description = "Contact Kim Minsu.";
  }
  class Args {
    type = "object";
    description = "Contact Kim Minsu.";
    properties = { note: { type: "string" } };
  }
  class Definition {
    type = "function";
    function = { name: "lookup", description: "About Kim Minsu", parameters: { type: "object", properties: { note: new Field() } } };
  }
  const twice = (then: unknown = "Kim Minsu") => {
    const state = { reads: 0 };
    const list: unknown[] = [];
    Object.defineProperty(list, "0", { enumerable: true, get: () => (++state.reads === 1 ? "control" : then) });
    return { list, state };
  };

  it("hides a class-backed schema in a tool, a definition, a response format and the settings (gate 8, item 3 in LangChain.js)", async () => {
    let reads = 0;
    const getterDefinition = Object.defineProperty({ type: "function" }, "function", {
      enumerable: true,
      get: () => (++reads === 1 ? { name: "g", description: "About Kim Minsu", parameters: { type: "object", properties: {} } } : { name: "g", description: "Kim Minsu", parameters: {} }),
    });
    const got = await sent({
      tools: [
        tool(async () => "ok", { name: "root", description: "d", schema: new Args() as never }),
        tool(async () => "ok", { name: "inside", description: "d", schema: { type: "object", properties: { note: new Field() } } as never }),
        new Definition(),
        getterDefinition,
      ],
      responseFormat: new Args(),
      modelSettings: { response_format: { type: "json_schema", json_schema: { name: "answer", schema: new Args() } } },
    });
    expect(JSON.stringify([got.tools, got.responseFormat, got.modelSettings])).not.toContain("Kim Minsu");
    expect(reads).toBe(1);
    expect(got.tools[0].function.parameters).toEqual({ type: "object", description: "Contact [PERSON_1].", properties: { note: { type: "string" } } });
    expect(got.tools[2]).toEqual({ type: "function", function: { name: "lookup", description: "About [PERSON_1]", parameters: { type: "object", properties: { note: { type: "string", description: "Contact [PERSON_1]." } } } } });
    expect(got.tools[3]).toEqual({ type: "function", function: { name: "g", description: "About [PERSON_1]", parameters: { type: "object", properties: {} } } });
    // a tool's parameters with a Zod schema ({ name, description, schema }): its descriptions are read as well
    const params = await sent({ tools: [{ name: "zod", description: "About Kim Minsu", schema: z.object({ q: z.string().describe("for Kim Minsu") }) }] });
    expect(JSON.stringify(params.tools)).not.toContain("Kim Minsu");
  });

  it("hides a class-backed schema in a response strategy made by LangChain's CommonJS build, and keeps it a strategy", async () => {
    // (an application that loads LangChain's CommonJS build while this package loads its ES module build: two copies
    // of the strategy classes; the strategy is copied as it is, with its schema hidden)
    const cjs = createRequire(import.meta.url)("langchain") as typeof import("langchain");
    for (const make of [cjs.toolStrategy, cjs.providerStrategy] as ((s: unknown) => object | object[])[]) {
      const [f] = [make(new Args())].flat(); // (toolStrategy gives a list)
      const got = await sent({ responseFormat: [f] });
      const [g] = got.responseFormat;
      expect(Object.getPrototypeOf(g)).toBe(Object.getPrototypeOf(f));
      expect(JSON.stringify(g)).not.toContain("Kim Minsu");
      expect(g.schema.description).toBe("Contact [PERSON_1].");
    }
  });

  it("sends the first read of a list given by a getter: a definition's examples, a message's tool calls, settings (gate 8, item 4 in LangChain.js)", async () => {
    const examples = twice();
    const stop = twice();
    const first = { id: "c1", name: "lookup", args: { who: "control" }, type: "tool_call" as const };
    const calls = { list: [] as unknown[], state: { reads: 0 } };
    Object.defineProperty(calls.list, "0", { enumerable: true, get: () => (++calls.state.reads === 1 ? first : { ...first, args: { who: "Kim Minsu" } }) });
    const ai = new AIMessage({ content: "", tool_calls: [first] });
    (ai as unknown as { tool_calls: unknown }).tool_calls = calls.list;
    const got = await sent({
      messages: [new HumanMessage("hi"), ai],
      tools: [{ type: "function", function: { name: "f", parameters: { type: "object", properties: { q: { type: "string", examples: examples.list } } } } }],
      modelSettings: { stop: stop.list },
    });
    expect(JSON.stringify([got.tools, got.modelSettings, got.messages[1].tool_calls])).not.toContain("Kim Minsu");
    for (const s of [examples, stop, calls]) expect(s.state.reads).toBe(1);
    expect(got.tools[0].function.parameters.properties.q.examples).toEqual(["control"]);
    expect(got.messages[1].tool_calls[0].args).toEqual({ who: "control" });
    // a message whose content is given by a getter (LangChain's own isInstance reads it too): the model is given a
    // message of its own whose content is the text the middleware read, masked, and not the getter
    let reads = 0;
    const lazy = new HumanMessage("Hello.");
    Object.defineProperty(lazy, "content", { enumerable: true, configurable: true, get: () => `I am Kim Minsu (read ${++reads}).` });
    const lazyGot = await sent({ messages: [lazy] });
    const content = Object.getOwnPropertyDescriptor(lazyGot.messages[0], "content");
    expect(content && "value" in content).toBe(true);
    expect(lazyGot.messages[0].content).toMatch(/^I am \[PERSON_1\] \(read \d+\)\.$/);
  });

  it("masks identifier names in the settings away from their paths: extra_body, an unknown field, one level down (gate 8, item 5 in LangChain.js)", async () => {
    const email = "kim@example.com";
    const got = await sent({
      modelSettings: {
        extra_body: { models: [email], provider: { order: [email] }, previous_response_id: email },
        acme: { prompt: { id: email }, mcp_servers: [{ name: email }] },
        reasoning: { previous_response_id: email },
        previous_response_id: email,
        prompt: { id: email, variables: { id: email } },
        mcp_servers: [{ type: "url", name: email, url: "https://mcp.test", note: email }],
      },
    });
    expect(got.modelSettings).toEqual({
      extra_body: { models: ["[EMAIL_1]"], provider: { order: ["[EMAIL_1]"] }, previous_response_id: "[EMAIL_1]" },
      acme: { prompt: { id: "[EMAIL_1]" }, mcp_servers: [{ name: "[EMAIL_1]" }] },
      reasoning: { previous_response_id: "[EMAIL_1]" },
      previous_response_id: email,
      prompt: { id: email, variables: { id: "[EMAIL_1]" } },
      mcp_servers: [{ type: "url", name: email, url: "https://mcp.test", note: "[EMAIL_1]" }],
    });
  });

  // Every exemption of the settings list, by its path: a held value there stays as written (a held number at a numeric
  // setting's path stays a number); the same path under an unknown field, one level down and inside extra_body is data.
  it("keeps every identifier of the settings list at its path, and masks it anywhere else", () => {
    const concrete = (p: readonly string[]): string[] => p.flatMap((k) => (k === "*" ? ["0"] : k === "**" ? ["deep", "x"] : [k]));
    const planted = (p: readonly string[], value: unknown): Record<string, unknown> => {
      const out: Record<string, unknown> = {};
      let o = out;
      p.forEach((k, i) => {
        if (i === p.length - 1) o[k] = value;
        else o = (o[k] = {}) as Record<string, unknown>;
      });
      return out;
    };
    const leaf = (x: unknown, p: readonly string[]): unknown => p.reduce<unknown>((o, k) => (o as Record<string, unknown> | undefined)?.[k], x);
    const table = OPTION_PATHS.settings;
    const onList = (paths: readonly (readonly string[])[], p: readonly string[]) =>
      paths.some((q) => {
        const c = concrete(q);
        return (c.length === p.length && c.every((k, i) => k === p[i])) || (q[q.length - 1] === "**" && p.length >= q.length - 1 && q.slice(0, -1).every((k, i) => k === "*" || k === p[i]));
      });
    const m = createMasker({ redactor: off, vault: known() });
    const problems: string[] = [];
    for (const path of table.ids) {
      const p = concrete(path);
      const at = m.options(planted(p, "kimminsu_77"), {}, false, "settings");
      if (leaf(at, p) !== "kimminsu_77") problems.push(`${p.join(".")} kept? ${JSON.stringify(leaf(at, p))}`);
      for (const q of [["acme", ...p], ["extra_body", ...p], [p[0], "nested", ...p.slice(1)], ["metadata", ...p]]) {
        if (onList(table.ids, q)) continue;
        const away = m.options(planted(q, "kimminsu_77"), {}, false, "settings");
        if (JSON.stringify(away).includes("kimminsu_77")) problems.push(`${q.join(".")} sent as written: ${JSON.stringify(away)}`);
      }
    }
    for (const path of table.numbers) {
      const p = concrete(path);
      const at = m.options(planted(p, 821023456789), {}, false, "settings");
      if (leaf(at, p) !== 821023456789) problems.push(`number ${p.join(".")}: ${JSON.stringify(leaf(at, p))}`);
      const away = m.options(planted(["acme", ...p], 821023456789), {}, false, "settings");
      if (JSON.stringify(away).includes("821023456789")) problems.push(`number acme.${p.join(".")} sent as written`);
    }
    expect(problems).toEqual([]);
  });

  timingIt("takes time in proportion to the number of class-backed definitions and settings at exact paths (doubling test)", async () => {
    const request = (n: number) => ({
      messages: [new HumanMessage("hi")],
      tools: Array.from({ length: n }, () => new Definition()),
      modelSettings: {
        tools: Array.from({ length: n }, (_, i) => ({ type: "mcp", server_label: `s${i}`, server_url: "https://mcp.test", note: "Kim Minsu" })),
        extra_body: Object.fromEntries(Array.from({ length: n }, (_, i) => [`k${i}`, { previous_response_id: "Kim Minsu" }])),
      },
    });
    const time = async (n: number) => {
      let best = Infinity;
      for (let r = 0; r < 3; r++) {
        const r0 = request(n);
        const t = performance.now();
        let got: any;
        await hook(piiMiddleware({ redactor: off, vault: known() }))(r0, async (x) => ((got = x), new AIMessage("ok")));
        best = Math.min(best, performance.now() - t);
        expect(JSON.stringify([got.tools, got.modelSettings])).not.toContain("Kim Minsu");
      }
      return best;
    };
    await time(100);
    const [a, b, c] = [await time(200), await time(400), await time(800)];
    expect([b / a, c / b].every((r) => r <= 3), `ms ${a.toFixed(1)}/${b.toFixed(1)}/${c.toFixed(1)}`).toBe(true);
  });
});

// SDK 1.7.1, the confirmation of release gate 8, items 3 and 4 in LangChain.js: every value is read the way
// JSON.stringify reads it, by property access (a getter runs, a Proxy's traps answer), each value once, and what was read
// is what is masked and sent: the reviewer's Proxy around a class-backed schema field and around a list, and the same
// Proxies at every place the middleware reads a value (a tool's schema, a definition, a response format, model
// settings, content blocks, tool calls, additional_kwargs and what a tool returned) ----------------------------------
describe("piiMiddleware with a vault: values read as JSON.stringify reads them (release gate 8 confirmation, items 3 and 4)", () => {
  const hook = (mw: ReturnType<typeof piiMiddleware>) =>
    mw.wrapModelCall as unknown as (request: object, handler: (r: any) => Promise<AIMessage>) => Promise<AIMessage>;
  const off = { sensitivity: 0, detect: async () => [] } as unknown as Redactor;
  const WORD = "지원금"; // (with "지원" held it is another word, and stays as written)
  const STORED = "draft-stored";
  /** What the model is given for `request`, as JSON (a fresh vault that holds a name and "지원"). */
  const sent = async (request: () => Record<string, unknown>) => {
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    vault.tag("PERSON", "지원");
    let got: any;
    await hook(piiMiddleware({ redactor: off, vault }))({ messages: [new HumanMessage("hi")], modelSettings: undefined, ...request() }, async (r) => ((got = r), new AIMessage("ok")));
    const tools = (got.tools ?? []).map((t: unknown) => (typeof (t as { invoke?: unknown }).invoke === "function" ? convertToOpenAITool(t as never) : t));
    const format = [got.responseFormat ?? []].flat().map((f: any) => (f && typeof f === "object" && "schema" in f ? { schema: f.schema, tool: f.tool } : f));
    // (a message by the fields a provider package reads: a message the middleware rebuilt lists them in another order)
    const messages = got.messages.map((m: any) => ({ type: m.type, content: m.content, name: m.name, tool_calls: m.tool_calls, tool_call_id: m.tool_call_id, additional_kwargs: m.additional_kwargs }));
    return JSON.stringify({ messages, tools, format, settings: got.modelSettings });
  };

  it("sends the reviewer's inputs as JSON writes them: the designated ordinary word stays (items 3 and 4)", async () => {
    const localized = <T extends object>(target: T, key: string): T => new Proxy(target, { get: (o, k, r) => (k === key ? WORD : Reflect.get(o, k, r)) });
    class Field {
      type = "string";
      description = "draft";
    }
    const inputs = [() => localized(new Field(), "description"), () => ({ type: "string", examples: localized(["draft"], "0") })];
    for (const note of inputs) {
      const schema = () => ({ type: "object", properties: { note: note() } });
      for (const request of [() => ({ tools: [tool(async () => "ok", { name: "lookup", description: "d", schema: schema() as never })] }), () => ({ tools: [{ type: "function", function: { name: "lookup", parameters: schema() } }] })]) {
        const out = await sent(request);
        expect(out).toContain(JSON.stringify({ type: "object", properties: { note: JSON.parse(JSON.stringify(note())) } }).slice(1, -1));
        expect(out).toContain(`"${WORD}"`);
        expect(out).not.toContain("draft");
      }
    }
  });

  const proxied = (text: string) => {
    const at = <T extends object>(target: T, key: string): T => new Proxy(target, { get: (o, k, r) => (k === key ? text : Reflect.get(o, k, r)) });
    class Stored {
      type = "string";
      description = STORED;
    }
    return { field: () => at(new Stored(), "description"), object: () => at({ type: "string", description: STORED }, "description"), list: () => at([STORED], "0") };
  };
  // (a list stands where a list does: in examples; a field anywhere a schema does)
  const schemaOf = (v: () => unknown) => {
    const x = v();
    if (Array.isArray(x)) return { title: "Answer", type: "object", properties: { b: { type: "string", examples: x }, c: { anyOf: [{ type: "string", examples: v() }] } } };
    return { title: "Answer", type: "object", properties: { a: x, b: { type: "string", examples: [v()] }, c: { anyOf: [v()] } }, $defs: { d: v() } };
  };
  const PLACES: { id: string; unrecorded?: boolean; request: (v: () => unknown) => Record<string, unknown> }[] = [
    { id: "a tool's JSON schema", request: (v) => ({ tools: [tool(async () => "ok", { name: "lookup", description: "d", schema: schemaOf(v) as never })] }) },
    { id: "a definition", request: (v) => ({ tools: [{ type: "function", function: { name: "lookup", description: "d", parameters: schemaOf(v) } }] }) },
    { id: "a response format (plain, tool and provider strategy)", request: (v) => ({ responseFormat: [schemaOf(v), ...[toolStrategy(schemaOf(v) as never)].flat(), providerStrategy(schemaOf(v) as never)] }) },
    { id: "model settings", request: (v) => ({ modelSettings: { metadata: { v: v() }, acme: { v: v() }, response_format: { type: "json_schema", json_schema: { name: "answer", schema: schemaOf(v) } } } }) },
    { id: "content blocks", request: (v) => ({ messages: [new HumanMessage({ content: [{ type: "text", text: "hi" }, { type: "x-later-block", v: v() }] as never })] }) },
    {
      id: "tool calls, additional_kwargs and what a tool returned",
      unrecorded: true,
      request: (v) => ({
        messages: [
          new HumanMessage("hi"),
          new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: { v: v() } }], additional_kwargs: { crm: { v: v() } } }),
          new ToolMessage({ tool_call_id: "c1", name: "lookup", content: [{ type: "json", json: { v: v() } }] as never }),
        ],
      }),
    },
  ];
  it("makes a new message of what was read when a message gives a field by a getter or a Proxy's trap: the converter reads it after the middleware", async () => {
    // (each message gives plain fields until the middleware is done, and the held name after: a message passed on as it
    // was would give that to the provider's converter, which reads it again)
    let after = false;
    const text = () => (after ? "Contact Kim Minsu." : "hi");
    const args = () => (after ? { who: "Kim Minsu" } : { who: "x" });
    const lazy = Object.defineProperty(new HumanMessage("hi"), "content", { get: text, enumerable: true, configurable: true });
    const human = new Proxy(new HumanMessage("hi"), { get: (o, k, r) => (k === "content" ? text() : Reflect.get(o, k, r)) });
    const ai = new AIMessage({ content: "", tool_calls: [{ id: "c1", name: "lookup", args: { who: "x" } }] });
    const call = new Proxy(ai, {
      get: (o, k, r) => (k === "tool_calls" ? [{ id: "c1", name: "lookup", args: args(), type: "tool_call" }] : k === "additional_kwargs" ? { crm: args() } : Reflect.get(o, k, r)),
    });
    const named = new Proxy(new HumanMessage({ content: "hi", name: "agent" }), { get: (o, k, r) => (k === "name" ? (after ? "Kim Minsu" : "agent") : Reflect.get(o, k, r)) });
    const vault = createVault();
    vault.tag("PERSON", "Kim Minsu");
    let got: any;
    await hook(piiMiddleware({ redactor: off, vault }))({ messages: [human, lazy, call, named], modelSettings: undefined }, async (r) => ((got = r), new AIMessage("ok")));
    after = true;
    const sent = got.messages.map((m: any) => ({ content: m.content, name: m.name, tool_calls: m.tool_calls?.map((c: any) => c.args), additional_kwargs: m.additional_kwargs }));
    expect(JSON.stringify(sent)).not.toContain("Kim Minsu");
    expect(sent.map((m: any) => m.content)).toEqual(["hi", "hi", "", "hi"]);
    expect(sent[2].tool_calls).toEqual([{ who: "x" }]);
    expect(sent[2].additional_kwargs).toEqual({ crm: { who: "x" } });
    expect(sent[3].name).toBe("agent");
    // (a list of tools whose item is given by a Proxy's trap: the list of what was read is sent)
    after = false;
    const definition = { type: "function", function: { name: "lookup", description: "a lookup", parameters: { type: "object", properties: {} } } };
    const tools = new Proxy([definition], {
      get: (o, k, r) => (k === "0" && after ? { ...definition, function: { ...definition.function, description: "Contact Kim Minsu." } } : Reflect.get(o, k, r)),
    });
    await hook(piiMiddleware({ redactor: off, vault }))({ messages: [new HumanMessage("hi")], tools, modelSettings: undefined }, async (r) => ((got = r), new AIMessage("ok")));
    after = true;
    expect(JSON.stringify(got.tools)).toBe(JSON.stringify([definition]));
    // (a message that stores its fields is passed on as it is)
    const plain = new HumanMessage("hi");
    await hook(piiMiddleware({ redactor: off, vault }))({ messages: [plain], modelSettings: undefined }, async (r) => ((got = r), new AIMessage("ok")));
    expect(got.messages[0]).toBe(plain);
  });

  for (const place of PLACES) {
    it(`reads a Proxy by property access in ${place.id}: what is sent is what is sent for the JSON it gives`, async () => {
      for (const text of ["Contact Kim Minsu.", WORD]) {
        for (const kind of ["field", "object", "list"] as const) {
          const make = proxied(text)[kind];
          const out = await sent(() => place.request(make));
          expect(out, `${kind} ${text}`).toBe(await sent(() => place.request(() => JSON.parse(JSON.stringify(make())))));
          expect(out, `${kind} ${text}`).not.toMatch(/Kim Minsu|draft-stored/);
          // (a held name is masked; the designated ordinary word stays as written, except in tool input the model wrote
          // without a record of it, where every known value is masked also inside longer words: README, "Earlier
          // answers and tool calls")
          expect(out, `${kind} ${text}`).toMatch(text === WORD ? /지원금|\[PERSON_2\]금/ : /Contact \[PERSON_1\]\./);
          if (!place.unrecorded) expect(out, `${kind} ${text}`).not.toContain("[PERSON_2]");
        }
      }
    });
  }
});
